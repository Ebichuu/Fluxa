from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.rss_subscription_match_runtime import qb_task_matches
from app.torra_subscription_keys import torra_public_subscription_key


ANALYSIS_ACTION_TYPE = "rewash-analysis"
MISSING_FALLBACK_ACTION_SOURCE = "missing-episode-fallback"
MISSING_FALLBACK_LOOKBACK_DAYS = 90
BEIJING_TZ = timezone(timedelta(hours=8))
TERMINAL_ACTION_STATUSES = {"succeeded", "failed", "cancelled"}


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


def _subscription_key(subscription):
    for key in ("key", "subscription_key", "id"):
        value = _text(subscription.get(key))
        if value:
            return value
    return ""


def _media_type(value):
    value = _text(value).lower()
    if value in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    if value in {"movie", "film", "电影"}:
        return "movie"
    return ""


def _tmdb_id(value):
    if not isinstance(value, dict):
        return ""
    for key in ("tmdb_id", "tmdbId", "tmdbid"):
        candidate = _text(value.get(key))
        if candidate.isdigit() and int(candidate) > 0:
            return candidate
    return ""


def _season_number(value):
    if not isinstance(value, dict):
        return None
    for key in ("target_season", "season_number", "seasonNumber", "current_season", "season"):
        if value.get(key) in (None, ""):
            continue
        try:
            number = int(value.get(key))
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


@dataclass(frozen=True)
class MissingEpisodeFallbackDependencies:
    torra: object
    qb: object
    rss_runtime: object
    calendar_service: object


class MissingEpisodeFallbackCoordinator:
    def __init__(self, repository, dependencies, clock=None):
        self.repository = repository
        self.torra = dependencies.torra
        self.qb = dependencies.qb
        self.rss_runtime = dependencies.rss_runtime
        self.calendar_service = dependencies.calendar_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def enabled(config):
        return _truthy(config.get("torra_quality_missing_fallback_enabled"))

    @staticmethod
    def _limits(config):
        return {
            "hourly": max(1, _integer(config.get("torra_quality_hourly_limit"), 4)),
            "daily": max(1, _integer(config.get("torra_quality_daily_limit"), 30)),
        }

    @staticmethod
    def _subscription_aliases(subscription):
        key = _subscription_key(subscription)
        aliases = {key} if key else set()
        remote_id = _text(subscription.get("torra_remote_id"))
        if remote_id:
            aliases.add(f"torra:{remote_id}")
            aliases.add(torra_public_subscription_key(remote_id))
        return aliases

    @staticmethod
    def _torra_identity_matches(subscription, torra_row, season_number):
        return bool(
            _media_type(
                subscription.get("media_type") or subscription.get("mediaType") or subscription.get("type")
            ) == "tv"
            and _media_type(
                torra_row.get("media_type") or torra_row.get("mediaType") or torra_row.get("type")
            ) == "tv"
            and _tmdb_id(subscription)
            and _tmdb_id(subscription) == _tmdb_id(torra_row)
            and _season_number(subscription) == season_number
            and _season_number(torra_row) == season_number
        )

    def _calendar_entries(self):
        if self.calendar_service is None:
            return []
        current = _as_utc(self.clock())
        if current is None:
            return []
        local_day = current.astimezone(BEIJING_TZ).date()
        start = local_day - timedelta(days=MISSING_FALLBACK_LOOKBACK_DAYS)
        try:
            payload = self.calendar_service.snapshot(
                local_day.year,
                local_day.month,
                "tv",
                start=start,
                end=local_day,
                include_unlinked=False,
            )
        except Exception:
            return []
        calendar = payload.get("calendar") if isinstance(payload, dict) else None
        entries = calendar.get("entries") if isinstance(calendar, dict) else None
        if not isinstance(entries, list):
            return []
        return [
            entry for entry in entries if isinstance(entry, dict)
            and _text(entry.get("status")) == "missing"
            and _text(entry.get("mediaType")) == "tv"
            and _text(entry.get("linkState")) in {"linked", "manual"}
            and entry.get("followScopeExplicit") is True
            and _tmdb_id(entry)
            and _season_number(entry) is not None
            and _integer(entry.get("episodeNumber")) > 0
        ]

    def _torra_rows_by_id(self):
        if self.torra is None or not self.torra.is_configured():
            return {}
        try:
            torra_rows = self.torra.list_subscriptions()
        except Exception:
            return {}
        return {
            _text(row.get("id")): row
            for row in torra_rows if isinstance(row, dict) and _text(row.get("id"))
        }

    def _matching_subscription(self, entry, subscriptions, torra_by_id):
        entry_key = _text(entry.get("key"))
        entry_tmdb = _tmdb_id(entry)
        season = _season_number(entry)
        matching = []
        for subscription in subscriptions.values():
            remote_id = _text(subscription.get("torra_remote_id"))
            torra_row = torra_by_id.get(remote_id)
            if not torra_row or entry_key not in self._subscription_aliases(subscription):
                continue
            if _tmdb_id(subscription) != entry_tmdb or _season_number(subscription) != season:
                continue
            if self._torra_identity_matches(subscription, torra_row, season):
                matching.append((subscription, remote_id, torra_row))
        return matching[0] if len(matching) == 1 else None

    def _group_entries(self, subscriptions, torra_by_id):
        grouped = {}
        for entry in self._calendar_entries():
            entry_tmdb = _tmdb_id(entry)
            season = _season_number(entry)
            matching = self._matching_subscription(entry, subscriptions, torra_by_id)
            if not matching:
                continue
            subscription, remote_id, torra_row = matching
            subscription_key = _subscription_key(subscription)
            group_key = (subscription_key, entry_tmdb, season)
            context = grouped.setdefault(group_key, {
                "subscription": subscription,
                "subscription_key": subscription_key,
                "torra_subscription_id": remote_id,
                "torra_row": torra_row,
                "tmdb_id": entry_tmdb,
                "season_number": season,
                "episode_numbers": set(),
                "evidence_times": [],
            })
            context["episode_numbers"].add(_integer(entry.get("episodeNumber")))
            evidence_time = _text(entry.get("airAt") or entry.get("date"))
            if evidence_time:
                context["evidence_times"].append(evidence_time)
        return grouped.values()

    def _build_context(self, grouped):
        episodes = sorted(grouped["episode_numbers"])
        evidence_times = sorted(grouped["evidence_times"])
        if not episodes:
            return None
        subscription_key = grouped["subscription_key"]
        season = grouped["season_number"]
        unit_key = f"{subscription_key}:s{season}:missing-fallback"
        idempotency_key = (
            f"missing-episode-fallback:{subscription_key}:"
            f"s{season}:e{','.join(map(str, episodes))}"
        )
        existing = self.repository.get_action_by_idempotency(idempotency_key)
        if existing and existing.get("status") in TERMINAL_ACTION_STATUSES:
            return None
        due_at = _as_utc(evidence_times[0]) if evidence_times else None
        return {
            **grouped,
            "episode_numbers": episodes,
            "evidence_at": evidence_times[-1] if evidence_times else "",
            "idempotency_key": idempotency_key,
            "due_at": due_at or _as_utc(self.clock()),
            "unit": {
                "unit_key": unit_key,
                "subscription_key": subscription_key,
                "torra_subscription_id": grouped["torra_subscription_id"],
                "season_number": season,
            },
        }

    def contexts(self, subscriptions):
        torra_by_id = self._torra_rows_by_id()
        if not torra_by_id:
            return []
        return [
            context
            for grouped in self._group_entries(subscriptions, torra_by_id)
            if (context := self._build_context(grouped)) is not None
        ]

    def _qb_preflight(self, context):
        if self.qb is None:
            return "qb_unavailable"
        summary = self.qb.summary()
        if not isinstance(summary, dict) or summary.get("connected") is not True:
            return "qb_unavailable"
        tasks = [task for task in summary.get("tasks") or [] if isinstance(task, dict)]
        if any(
            qb_task_matches(
                task,
                context["subscription"],
                {**context["unit"], "episode_number": episode},
            )
            for task in tasks
            for episode in context["episode_numbers"]
        ):
            return "qb_busy"
        return ""

    def _rss_preflight(self, context):
        if self.rss_runtime is None or not hasattr(self.rss_runtime, "has_executable_candidate"):
            return "rss_evidence_unavailable"
        try:
            has_candidate = self.rss_runtime.has_executable_candidate(
                context["subscription_key"],
                media_type="tv",
                season_number=context["season_number"],
                episode_numbers=context["episode_numbers"],
                torra_subscription_id=context["torra_subscription_id"],
            )
        except Exception:
            return "rss_evidence_unavailable"
        return "rss_candidate_available" if has_candidate else ""

    def _preflight(self, context):
        row = context["torra_row"]
        if row.get("is_running") is True or row.get("is_mutating") is True:
            return "torra_busy"
        return self._qb_preflight(context) or self._rss_preflight(context)

    def _claim(self, context, config):
        return self.repository.claim_action(
            context["idempotency_key"],
            context["subscription_key"],
            "torra",
            ANALYSIS_ACTION_TYPE,
            unit_key=context["unit"]["unit_key"],
            request_summary={
                "source": MISSING_FALLBACK_ACTION_SOURCE,
                "tmdbId": context["tmdb_id"],
                "seasonNumber": context["season_number"],
                "episodeNumbers": context["episode_numbers"],
                "evidenceAt": context["evidence_at"],
            },
            cooldown_seconds=max(
                60, _integer(config.get("torra_quality_min_interval_minutes"), 60)
            ) * 60,
            rate_limits=self._limits(config),
            require_idle=True,
            require_provider_idle=True,
        )

    def _finish(self, action, job):
        action_id = action["action_id"]
        result = job.get("result") if isinstance(job, dict) else None
        rows = result.get("rows") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            self.repository.complete_action(
                action_id,
                "failed",
                {"message": "Torra 缺集分析结果无效"},
                error_code="TORRA_MISSING_FALLBACK_RESULT_INVALID",
                error_message="Torra 缺集分析结果无效",
            )
            return {"status": "failed", "actionId": action_id, "reason": "analysis_result_invalid"}
        candidate_count = sum(
            len(row.get("candidates") or [])
            for row in rows if isinstance(row, dict) and isinstance(row.get("candidates"), list)
        )
        episode_count = len(action.get("request_summary", {}).get("episodeNumbers") or [])
        self.repository.complete_action(
            action_id,
            "succeeded",
            {
                "jobStatus": "success",
                "reason": "missing_episode_fallback_checked",
                "rowCount": len(rows),
                "candidateCount": candidate_count,
                "episodeCount": episode_count,
            },
        )
        return {
            "status": "checked",
            "actionId": action_id,
            "rowCount": len(rows),
            "candidateCount": candidate_count,
        }

    def _poll(self, action):
        action_id = action["action_id"]
        try:
            job = self.torra.get_job(action["external_job_id"])
        except Exception:
            self.repository.save_external_job(
                action_id,
                action["external_job_id"],
                status="polling",
            )
            return {"status": "poll_failed", "actionId": action_id}
        status = _text(job.get("status"))
        if status in {"pending", "running"}:
            self.repository.save_external_job(
                action_id,
                action["external_job_id"],
                status="polling",
            )
            return {"status": "polling", "actionId": action_id}
        if status in {"failed", "cancelled"}:
            self.repository.complete_action(
                action_id,
                status,
                {"jobStatus": status, "reason": "missing_episode_fallback_failed"},
                error_code=f"TORRA_MISSING_FALLBACK_{status.upper()}",
                error_message=f"Torra 缺集分析任务{status}",
            )
            return {"status": status, "actionId": action_id}
        return self._finish(action, job)

    def _submit(self, context, action):
        action_id = action["action_id"]
        try:
            job_id = self.torra.submit_analysis(context["torra_subscription_id"])
            self.repository.save_external_job(action_id, job_id)
            return {"status": "submitted", "actionId": action_id}
        except Exception:
            self.repository.complete_action(
                action_id,
                "failed",
                {"message": "Torra 缺集分析提交失败"},
                error_code="TORRA_MISSING_FALLBACK_SUBMIT_FAILED",
                error_message="Torra 缺集分析提交失败",
            )
            return {"status": "failed", "actionId": action_id}

    def _handle_claim(self, context, claim):
        disposition = claim["disposition"]
        action = claim.get("action")
        if disposition == "resume":
            return self._poll(action)
        if disposition == "replay":
            return {"status": "replay", "actionId": action["action_id"]}
        if disposition == "in_progress":
            return {"status": "in_progress", "actionId": action["action_id"]}
        if disposition in {"cooldown", "rate_limited", "global_busy"}:
            result = {"status": "deferred", "reason": disposition}
            result.update({
                key: claim[key]
                for key in ("remaining_seconds", "window", "limit") if key in claim
            })
            return result
        if disposition not in {"claimed", "reclaimed"}:
            return {"status": "skipped", "reason": disposition}
        return self._submit(context, action)

    def process(self, context, config):
        try:
            reason = self._preflight(context)
        except Exception:
            reason = "provider_check_failed"
        if reason:
            return {"status": "skipped", "reason": reason}
        return self._handle_claim(context, self._claim(context, config))

    def resume(self, action, config, subscriptions):
        if action.get("external_job_id"):
            return self._poll(action)
        if (
            not _truthy(config.get("torra_quality_watch_enabled"))
            or not self.enabled(config)
        ):
            self.repository.complete_action(
                action["action_id"],
                "cancelled",
                {"reason": "missing_fallback_disabled"},
            )
            return {
                "status": "cancelled",
                "reason": "missing_fallback_disabled",
                "actionId": action["action_id"],
            }
        context = next((
            item for item in self.contexts(subscriptions)
            if item["idempotency_key"] == action.get("idempotency_key")
        ), None)
        if not context:
            self.repository.complete_action(
                action["action_id"],
                "cancelled",
                {"reason": "missing_evidence_no_longer_actionable"},
            )
            return {
                "status": "cancelled",
                "reason": "missing_evidence_no_longer_actionable",
                "actionId": action["action_id"],
            }
        return self.process(context, config)
