from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.quality_watch_repository import QualityWatchVersionConflict
from app.quality_watch_runtime import resolve_watch_policy
from app.rss_subscription_match_runtime import qb_task_matches


ANALYSIS_ACTION_TYPE = "rewash-analysis"
SCHEDULER_ACTION_SOURCE = "quality-watch-scheduler"
SCHEDULER_STATE_KEY = "quality-watch-scheduler"
RUNNING_RESULTS = {"in_progress", "polling", "poll_failed", "submitted"}


def _text(value):
    return str(value or "").strip()


def _integer(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _truthy(value):
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _as_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def deterministic_jitter_minutes(unit_key, offset_index):
    digest = hashlib.sha256(f"{unit_key}:{int(offset_index)}".encode("utf-8")).digest()
    return digest[0] % 16


def _subscription_key(subscription):
    for key in ("key", "subscription_key", "id"):
        value = _text(subscription.get(key))
        if value:
            return value
    return ""


@dataclass(frozen=True)
class QualityWatchSchedulerDependencies:
    environment: object
    torra: object
    qb: object
    subscription_loader: object
    config_loader: object
    rss_runtime: object = None
    automation_runtime: object = None


class QualityWatchScheduler:
    def __init__(self, repository, dependencies, clock=None):
        self.repository = repository
        self.environment = dependencies.environment or {}
        self.torra = dependencies.torra
        self.qb = dependencies.qb
        self.subscription_loader = dependencies.subscription_loader or (lambda: [])
        self.config_loader = dependencies.config_loader or (lambda: {})
        self.rss_runtime = dependencies.rss_runtime
        self.automation_runtime = dependencies.automation_runtime
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _config(self):
        value = self.config_loader()
        return value if isinstance(value, dict) else {}

    def enabled(self, config=None):
        config = self._config() if config is None else config
        return _truthy(self.environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED")) and _truthy(
            config.get("torra_quality_watch_enabled")
        )

    def _environment_enabled(self):
        return _truthy(self.environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED"))

    def _subscriptions(self):
        payload = self.subscription_loader()
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        return {
            _subscription_key(item): item
            for item in payload if isinstance(item, dict) and _subscription_key(item)
        }

    @staticmethod
    def _batch_size(config):
        value = _integer(config.get("torra_quality_scheduler_batch_size"), 2)
        return value if value in {2, 3} else 2

    @staticmethod
    def _limits(config):
        return {
            "hourly": max(1, _integer(config.get("torra_quality_hourly_limit"), 4)),
            "daily": max(1, _integer(config.get("torra_quality_daily_limit"), 30)),
        }

    @staticmethod
    def _policy(unit, subscription, config):
        value = dict(subscription)
        nested = value.get("torra_quality_watch")
        nested = dict(nested) if isinstance(nested, dict) else {}
        nested["window_hours"] = int(unit["window_hours"])
        value["torra_quality_watch"] = nested
        return resolve_watch_policy(value, config)

    def _context(self, unit, subscription, config, forced_index=None):
        baseline = _as_utc(unit.get("baseline_ready_at"))
        ends_at = _as_utc(unit.get("observation_ends_at"))
        if not baseline or not ends_at:
            return None, "watch_window_invalid"
        try:
            policy = self._policy(unit, subscription, config)
        except ValueError:
            return None, "invalid_watch_policy"
        offsets = policy["offsets_minutes"]
        index = min(max(0, _integer(unit.get("current_offset_index"))), len(offsets) - 1)
        now = _as_utc(self.clock())
        if forced_index is not None:
            index = min(max(0, int(forced_index)), len(offsets) - 1)
        elif unit.get("state") != "search_running" and now >= ends_at:
            index = len(offsets) - 1
        scheduled_at = baseline + timedelta(minutes=offsets[index])
        due_at = min(scheduled_at + timedelta(minutes=deterministic_jitter_minutes(unit["unit_key"], index)), ends_at)
        return {
            "unit": unit,
            "subscription": subscription,
            "policy": policy,
            "offset_index": index,
            "scheduled_at": scheduled_at,
            "due_at": due_at,
            "ends_at": ends_at,
            "now": now,
        }, ""

    def _update_unit(self, unit, **changes):
        try:
            return self.repository.update_watch_unit(unit["unit_key"], unit["version"], **changes)
        except QualityWatchVersionConflict:
            return self.repository.get_watch_unit(unit["unit_key"])

    def _block_unit(self, unit, reason):
        self._update_unit(unit, state="blocked", last_result_json={"reason": reason})
        return {"status": "blocked", "reason": reason, "unitId": unit["unit_key"]}

    def _defer(self, context, reason, **details):
        unit = self.repository.get_watch_unit(context["unit"]["unit_key"]) or context["unit"]
        result = {"reason": reason, "offsetIndex": context["offset_index"], **details}
        if context["now"] >= context["ends_at"]:
            self._update_unit(unit, state="observation_expired", next_check_at="", last_result_json=result)
            return {"status": "expired", "reason": reason, "unitId": unit["unit_key"]}
        self._update_unit(
            unit,
            state="search_due",
            current_offset_index=context["offset_index"],
            next_check_at=_iso(context["due_at"]),
            last_result_json=result,
        )
        return {"status": "deferred", "reason": reason, "unitId": unit["unit_key"]}

    def _advance(self, context, result, increment_attempt=False):
        unit = self.repository.get_watch_unit(context["unit"]["unit_key"]) or context["unit"]
        index = context["offset_index"]
        offsets = context["policy"]["offsets_minutes"]
        changes = {
            "last_result_json": {"offsetIndex": index, **result},
            "attempt_count": int(unit.get("attempt_count") or 0) + (1 if increment_attempt else 0),
        }
        if context["now"] >= context["ends_at"] or index >= len(offsets) - 1:
            changes.update(state="observation_expired", next_check_at="", current_offset_index=index)
        else:
            next_index = index + 1
            scheduled = _as_utc(unit["baseline_ready_at"]) + timedelta(minutes=offsets[next_index])
            due_at = min(
                scheduled + timedelta(minutes=deterministic_jitter_minutes(unit["unit_key"], next_index)),
                context["ends_at"],
            )
            changes.update(
                state="observing_upgrade",
                next_check_at=_iso(due_at),
                current_offset_index=next_index,
            )
        updated = self._update_unit(unit, **changes)
        return updated

    def _set_running(self, context, action_id, increment_attempt=False, reason="analysis_running"):
        unit = self.repository.get_watch_unit(context["unit"]["unit_key"]) or context["unit"]
        self._update_unit(
            unit,
            state="search_running",
            current_offset_index=context["offset_index"],
            next_check_at=_iso(context["due_at"]),
            attempt_count=int(unit.get("attempt_count") or 0) + (1 if increment_attempt else 0),
            last_result_json={"reason": reason, "actionId": action_id, "offsetIndex": context["offset_index"]},
        )

    def _rss_triggered_in_interval(self, context):
        offsets = context["policy"]["offsets_minutes"]
        index = context["offset_index"]
        start_offset = offsets[index - 1] if index > 0 else 0
        start_at = _as_utc(context["unit"]["baseline_ready_at"]) + timedelta(minutes=start_offset)
        for action in self.repository.list_unit_actions_since(
            context["unit"]["unit_key"], "torra", ANALYSIS_ACTION_TYPE, start_at
        ):
            created_at = _as_utc(action.get("created_at"))
            if index > 0 and created_at and created_at <= start_at:
                continue
            source = action.get("request_summary", {}).get("source")
            if (
                created_at
                and created_at <= context["now"]
                and source == "private-rss"
                and action.get("external_job_id")
            ):
                return action
        return None

    def _torra_preflight(self, context):
        if self.torra is None or not self.torra.is_configured():
            return "torra_unavailable"
        rows = self.torra.list_subscriptions()
        torra_id = _text(context["unit"].get("torra_subscription_id"))
        row = next((item for item in rows if _text(item.get("id")) == torra_id), None)
        if not row:
            return "torra_subscription_missing"
        if row.get("is_running") is True or row.get("is_mutating") is True:
            return "torra_busy"
        return ""

    def _qb_preflight(self, context):
        if self.qb is None:
            return "qb_unavailable"
        summary = self.qb.summary()
        if not isinstance(summary, dict) or summary.get("connected") is not True:
            return "qb_unavailable"
        if any(
            qb_task_matches(task, context["subscription"], context["unit"])
            for task in summary.get("tasks") or [] if isinstance(task, dict)
        ):
            return "qb_busy"
        return ""

    def _provider_preflight(self, context):
        return self._torra_preflight(context) or self._qb_preflight(context)

    @staticmethod
    def _idempotency_key(context):
        return f"scheduled-rewash-analysis:{context['unit']['unit_key']}:{context['offset_index']}"

    def _claim(self, context, config):
        return self.repository.claim_action(
            self._idempotency_key(context),
            context["unit"]["subscription_key"],
            "torra",
            ANALYSIS_ACTION_TYPE,
            unit_key=context["unit"]["unit_key"],
            request_summary={
                "source": SCHEDULER_ACTION_SOURCE,
                "offsetIndex": context["offset_index"],
                "scheduledAt": _iso(context["scheduled_at"]),
            },
            cooldown_seconds=max(60, _integer(config.get("torra_quality_min_interval_minutes"), 60)) * 60,
            rate_limits=self._limits(config),
            require_idle=True,
        )

    def _finish_success(self, context, action_id, job):
        try:
            selection = self.torra.select_upgrade_candidates(job)
        except Exception:
            self.repository.complete_action(
                action_id,
                "failed",
                {"message": "Torra 分析结果无效"},
                error_code="TORRA_ANALYSIS_RESULT_INVALID",
                error_message="Torra 分析结果无效",
            )
            self._advance(context, {"reason": "analysis_result_invalid", "actionId": action_id})
            return {"status": "failed", "actionId": action_id, "unitId": context["unit"]["unit_key"]}
        self.repository.complete_action(
            action_id,
            "succeeded",
            {
                "jobStatus": "success",
                "analysisId": selection["analysis_id"],
                "selectedCandidates": selection["selected_candidates"],
                "rowCount": selection["row_count"],
                "selectedCount": selection["selected_count"],
            },
        )
        reason = "upgrade_found" if selection["selected_count"] else "no_upgrade"
        self._advance(
            context,
            {"reason": reason, "actionId": action_id, "selectedCount": selection["selected_count"]},
        )
        return {
            "status": reason,
            "actionId": action_id,
            "selectedCount": selection["selected_count"],
            "unitId": context["unit"]["unit_key"],
        }

    def _poll(self, context, action):
        action_id = action["action_id"]
        try:
            job = self.torra.get_job(action["external_job_id"])
        except Exception:
            self._set_running(context, action_id, reason="analysis_poll_failed")
            return {"status": "poll_failed", "actionId": action_id, "unitId": context["unit"]["unit_key"]}
        status = job["status"]
        if status in {"pending", "running"}:
            self.repository.save_external_job(action_id, action["external_job_id"], status="polling")
            self._set_running(context, action_id)
            return {"status": "polling", "actionId": action_id, "unitId": context["unit"]["unit_key"]}
        if status in {"failed", "cancelled"}:
            self.repository.complete_action(
                action_id,
                status,
                {"jobStatus": status},
                error_code=f"TORRA_ANALYSIS_{status.upper()}",
                error_message=f"Torra 分析任务{status}",
            )
            self._advance(context, {"reason": f"analysis_{status}", "actionId": action_id})
            return {"status": status, "actionId": action_id, "unitId": context["unit"]["unit_key"]}
        return self._finish_success(context, action_id, job)

    def _submit(self, context, action):
        action_id = action["action_id"]
        try:
            job_id = self.torra.submit_analysis(context["unit"]["torra_subscription_id"])
            self.repository.save_external_job(action_id, job_id)
            self._set_running(context, action_id, increment_attempt=True, reason="analysis_submitted")
            return {"status": "submitted", "actionId": action_id, "unitId": context["unit"]["unit_key"]}
        except Exception:
            self.repository.complete_action(
                action_id,
                "failed",
                {"message": "Torra 分析提交失败"},
                error_code="TORRA_ANALYSIS_SUBMIT_FAILED",
                error_message="Torra 分析提交失败",
            )
            self._advance(context, {"reason": "analysis_submit_failed", "actionId": action_id}, increment_attempt=True)
            return {"status": "failed", "actionId": action_id, "unitId": context["unit"]["unit_key"]}

    def _replay(self, context, action):
        selected_count = _integer(action.get("response_summary", {}).get("selectedCount"))
        reason = "upgrade_found" if action["status"] == "succeeded" and selected_count else (
            "no_upgrade" if action["status"] == "succeeded" else f"analysis_{action['status']}"
        )
        self._advance(context, {"reason": reason, "actionId": action["action_id"], "selectedCount": selected_count})
        return {"status": "replay", "actionId": action["action_id"], "unitId": context["unit"]["unit_key"]}

    def _handle_claim(self, context, claim, config, preflight_checked=False):
        disposition = claim["disposition"]
        action = claim.get("action")
        if disposition == "resume":
            return self._poll(context, action)
        if disposition == "replay":
            return self._replay(context, action)
        if disposition == "in_progress":
            self._set_running(context, action["action_id"])
            return {"status": "in_progress", "actionId": action["action_id"], "unitId": context["unit"]["unit_key"]}
        if disposition in {"cooldown", "rate_limited"}:
            details = {key: claim[key] for key in ("remaining_seconds", "window", "limit") if key in claim}
            return self._defer(context, disposition, **details)
        if disposition not in {"claimed", "reclaimed"}:
            return self._defer(context, disposition)
        if not preflight_checked:
            try:
                reason = self._provider_preflight(context)
            except Exception:
                reason = "provider_check_failed"
            if reason:
                self.repository.complete_action(
                    action["action_id"],
                    "cancelled",
                    {"reason": reason},
                    error_code="TORRA_ANALYSIS_PREFLIGHT_BLOCKED",
                    error_message="Torra 分析预检未通过",
                )
                self._advance(context, {"reason": reason, "actionId": action["action_id"]})
                return {"status": "skipped", "reason": reason, "unitId": context["unit"]["unit_key"]}
        return self._submit(context, action)

    def _process_context(self, context, config):
        existing = self.repository.get_action_by_idempotency(self._idempotency_key(context))
        if existing:
            return self._handle_claim(context, self._claim(context, config), config)
        rss_action = self._rss_triggered_in_interval(context)
        if rss_action:
            self._advance(
                context,
                {"reason": "rss_analysis_in_interval", "actionId": rss_action["action_id"]},
            )
            return {"status": "rss_skipped", "unitId": context["unit"]["unit_key"]}
        try:
            reason = self._provider_preflight(context)
        except Exception:
            reason = "provider_check_failed"
        if reason:
            return self._defer(context, reason)
        return self._handle_claim(context, self._claim(context, config), config, preflight_checked=True)

    def _due_contexts(self, config, subscriptions):
        contexts = []
        for unit in self.repository.list_scheduler_watch_units():
            subscription = subscriptions.get(unit["subscription_key"])
            if not subscription:
                self._block_unit(unit, "subscription_missing")
                continue
            if not _text(unit.get("torra_subscription_id")):
                self._block_unit(unit, "torra_subscription_missing")
                continue
            context, reason = self._context(unit, subscription, config)
            if reason:
                self._block_unit(unit, reason)
            elif context["now"] >= context["due_at"]:
                contexts.append(context)
            elif unit.get("next_check_at") != _iso(context["due_at"]):
                self._update_unit(
                    unit,
                    current_offset_index=context["offset_index"],
                    next_check_at=_iso(context["due_at"]),
                )
        return contexts

    def _scheduler_state(self):
        return self.repository.get_scheduler_state(SCHEDULER_STATE_KEY) or {
            "payload": {}, "version": 0
        }

    def _select_fair(self, contexts, config, state):
        ordered = sorted(contexts, key=lambda item: (item["due_at"], item["unit"]["unit_key"]))
        cursor = _text(state["payload"].get("cursor"))
        if cursor:
            position = next((index for index, item in enumerate(ordered) if item["unit"]["unit_key"] == cursor), -1)
            if position >= 0:
                ordered = ordered[position + 1:] + ordered[:position + 1]
        last_subscription = _text(state["payload"].get("lastSubscription"))
        ordered.sort(key=lambda item: item["unit"]["subscription_key"] == last_subscription)
        selected = []
        subscriptions = set()
        for context in ordered:
            key = context["unit"]["subscription_key"]
            if key in subscriptions:
                continue
            selected.append(context)
            subscriptions.add(key)
            if len(selected) >= self._batch_size(config):
                break
        return selected

    def _record_cursor(self, context):
        state = self._scheduler_state()
        payload = dict(state["payload"])
        payload.update({
            "cursor": context["unit"]["unit_key"],
            "lastSubscription": context["unit"]["subscription_key"],
            "lastRunAt": _iso(_as_utc(self.clock())),
        })
        try:
            self.repository.save_scheduler_state(
                SCHEDULER_STATE_KEY,
                payload,
                expected_version=state["version"],
            )
        except QualityWatchVersionConflict:
            pass

    def _resume_inflight(self, action, config, subscriptions):
        source = action.get("request_summary", {}).get("source")
        if source == "private-rss" and self.rss_runtime:
            match_id = action.get("request_summary", {}).get("matchId")
            return {"source": source, **self.rss_runtime.start_analysis(match_id)}
        if source in {"manual-subscription", "manual-rss"} and self.automation_runtime:
            return {"source": source, **self.automation_runtime.resume_action(action)}
        if source != SCHEDULER_ACTION_SOURCE:
            return {"status": "global_busy", "actionId": action["action_id"]}
        unit = self.repository.get_watch_unit(action["unit_key"])
        subscription = subscriptions.get(action["subscription_key"])
        if not unit or not subscription:
            return {"status": "blocked", "reason": "watch_context_missing", "actionId": action["action_id"]}
        context, reason = self._context(
            unit,
            subscription,
            config,
            forced_index=action.get("request_summary", {}).get("offsetIndex"),
        )
        if reason:
            return self._block_unit(unit, reason)
        result = self._handle_claim(context, self._claim(context, config), config)
        self._record_cursor(context)
        return result

    def _wake_pending_rss(self):
        if not self.rss_runtime:
            return None
        results = self.rss_runtime.wake_pending_candidates()
        return next(
            (
                result for result in results
                if result.get("status") in {"submitted", "polling", "in_progress", "global_busy"}
            ),
            None,
        )

    def run_once(self):
        config = self._config()
        if not self._environment_enabled():
            return {"status": "disabled", "processed": []}
        subscriptions = self._subscriptions()
        inflight = [
            action for action in (
                self.repository.find_inflight_action("torra", ANALYSIS_ACTION_TYPE),
                self.repository.find_inflight_action("torra", "rewash-download"),
            ) if action
        ]
        inflight = min(inflight, key=lambda action: action["created_at"]) if inflight else None
        if inflight and inflight.get("external_job_id"):
            return {"status": "ok", "processed": [self._resume_inflight(inflight, config, subscriptions)]}
        if not _truthy(config.get("torra_quality_watch_enabled")):
            return {"status": "disabled", "processed": []}
        if inflight:
            return {"status": "ok", "processed": [self._resume_inflight(inflight, config, subscriptions)]}
        running = self._wake_pending_rss()
        if running:
            return {"status": "ok", "processed": [{"source": "private-rss", **running}]}
        state = self._scheduler_state()
        selected = self._select_fair(self._due_contexts(config, subscriptions), config, state)
        processed = []
        for context in selected:
            result = self._process_context(context, config)
            processed.append(result)
            self._record_cursor(context)
            if result.get("status") in RUNNING_RESULTS:
                break
        return {"status": "ok", "selected": len(selected), "processed": processed}


def register_quality_watch_scheduler(app, scheduler):
    app.extensions["mcc_quality_watch_scheduler"] = scheduler
    return scheduler
