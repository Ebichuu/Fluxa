from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.quality_watch_runtime import DEFAULT_OFFSETS, resolve_watch_policy
from app.subscription_automation_api_runtime import AutomationApiError
from app.subscription_automation_preflight import require_rewash_provider_ready


ANALYSIS_TYPE = "rewash-analysis"
DOWNLOAD_TYPE = "rewash-download"
MANUAL_SOURCE = "manual-subscription"
MANUAL_RSS_SOURCE = "manual-rss"
ACTIVE_STATES = {"observing_upgrade", "search_due", "search_running"}
SETTINGS_FIELDS = {
    "enabled",
    "defaultWindowHours",
    "scheduleMinutes",
    "minIntervalMinutes",
    "hourlyLimit",
    "dailyLimit",
    "batchSize",
}
QUALITY_PATCH_FIELDS = {"paused", "windowHours", "scheduleMinutes"}


def _text(value):
    return str(value or "").strip()


def _truthy(value):
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _integer(value, field, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID", f"{field} 必须是整数", 422)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID", f"{field} 必须是整数", 422) from exc
    if minimum is not None and number < minimum:
        raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID", f"{field} 不能小于 {minimum}", 422)
    if maximum is not None and number > maximum:
        raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID", f"{field} 不能大于 {maximum}", 422)
    return number


def _as_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _subscription_key(item):
    for key in ("key", "subscription_key", "id"):
        value = _text(item.get(key))
        if value:
            return value
    return ""


@dataclass(frozen=True)
class SubscriptionAutomationDependencies:
    environment: object
    repository: object
    torra: object
    qb: object
    config_loader: object
    config_saver: object
    subscription_loader: object
    subscription_updater: object
    rss_runtime: object = None
    clock: object = None


class SubscriptionAutomationService:
    def __init__(self, dependencies):
        self.environment = dependencies.environment or {}
        self.repository = dependencies.repository
        self.torra = dependencies.torra
        self.qb = dependencies.qb
        self.config_loader = dependencies.config_loader or (lambda: {})
        self.config_saver = dependencies.config_saver
        self.subscription_loader = dependencies.subscription_loader or (lambda: [])
        self.subscription_updater = dependencies.subscription_updater
        self.rss_runtime = dependencies.rss_runtime
        self.clock = dependencies.clock or (lambda: datetime.now(timezone.utc))

    def _config(self):
        value = self.config_loader()
        return dict(value) if isinstance(value, dict) else {}

    def _subscriptions(self):
        payload = self.subscription_loader()
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        return {
            _subscription_key(item): item
            for item in payload if isinstance(item, dict) and _subscription_key(item)
        }

    def _item(self, key):
        item = self._subscriptions().get(_text(key))
        if not item:
            raise AutomationApiError("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        return item

    def _require_write(self):
        if not _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")):
            raise AutomationApiError(
                "SUBSCRIPTION_AUTOMATION_WRITE_DISABLED",
                "订阅自动化设置写入开关未启用",
                503,
            )

    def _require_analysis(self):
        if not _truthy(self.environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED")):
            raise AutomationApiError("TORRA_REWASH_ANALYSIS_DISABLED", "Torra 追更洗版分析开关未启用", 503)
        if not _truthy(self._config().get("torra_quality_watch_enabled")):
            raise AutomationApiError("TORRA_REWASH_ANALYSIS_DISABLED", "订阅追更洗版设置未启用", 503)

    def _require_download(self):
        self._require_analysis()
        if not _truthy(self.environment.get("MCC_TORRA_REWASH_DOWNLOAD_ENABLED")):
            raise AutomationApiError("TORRA_REWASH_DOWNLOAD_DISABLED", "Torra 追更洗版下载开关未启用", 503)

    @staticmethod
    def _validate_idempotency(body):
        key = _text(body.get("idempotencyKey"))
        if not 12 <= len(key) <= 128:
            raise AutomationApiError("TORRA_REWASH_IDEMPOTENCY_INVALID", "幂等键长度必须为 12 到 128 个字符", 422)
        return key

    @staticmethod
    def _validate_fields(body, allowed):
        unknown = sorted(set(body) - set(allowed))
        if unknown:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_FIELDS_INVALID", "请求包含不支持的字段", 422)

    @staticmethod
    def _schedule(window_hours, schedule):
        try:
            policy = resolve_watch_policy({
                "torra_quality_window_hours": window_hours,
                "torra_quality_schedule_json": schedule,
            })
        except ValueError as exc:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SCHEDULE_INVALID", str(exc), 422) from exc
        return policy["offsets_minutes"]

    def present_settings(self, config=None):
        config = self._config() if config is None else config
        window = int(config.get("torra_quality_default_window_hours") or 48)
        raw_schedule = config.get("torra_quality_schedule_json")
        schedule = self._schedule(window, raw_schedule) if raw_schedule is not None else list(DEFAULT_OFFSETS[window])
        return {
            "enabled": _truthy(config.get("torra_quality_watch_enabled")),
            "environmentEnabled": _truthy(self.environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED")),
            "downloadEnvironmentEnabled": _truthy(self.environment.get("MCC_TORRA_REWASH_DOWNLOAD_ENABLED")),
            "defaultWindowHours": window,
            "scheduleMinutes": schedule,
            "minIntervalMinutes": int(config.get("torra_quality_min_interval_minutes") or 60),
            "hourlyLimit": int(config.get("torra_quality_hourly_limit") or 4),
            "dailyLimit": int(config.get("torra_quality_daily_limit") or 30),
            "batchSize": int(config.get("torra_quality_scheduler_batch_size") or 2),
        }

    def update_settings(self, body):
        self._require_write()
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, SETTINGS_FIELDS)
        if not body:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_EMPTY", "至少需要一个设置字段", 422)
        config = self._config()
        current = self.present_settings(config)
        window = _integer(body.get("defaultWindowHours", current["defaultWindowHours"]), "defaultWindowHours")
        if window not in {24, 48}:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_WINDOW_INVALID", "窗口只允许 24 或 48 小时", 422)
        schedule_value = body.get("scheduleMinutes")
        if schedule_value is None and "defaultWindowHours" in body:
            schedule_value = list(DEFAULT_OFFSETS[window])
        elif schedule_value is None:
            schedule_value = current["scheduleMinutes"]
        schedule = self._schedule(window, schedule_value)
        if "enabled" in body and not isinstance(body["enabled"], bool):
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID", "enabled 必须是布尔值", 422)
        config.update({
            "torra_quality_watch_enabled": body.get("enabled", current["enabled"]),
            "torra_quality_default_window_hours": window,
            "torra_quality_schedule_json": schedule,
            "torra_quality_min_interval_minutes": _integer(
                body.get("minIntervalMinutes", current["minIntervalMinutes"]), "minIntervalMinutes", 60, 1440
            ),
            "torra_quality_hourly_limit": _integer(
                body.get("hourlyLimit", current["hourlyLimit"]), "hourlyLimit", 1, 1000
            ),
            "torra_quality_daily_limit": _integer(
                body.get("dailyLimit", current["dailyLimit"]), "dailyLimit", 1, 1000
            ),
            "torra_quality_scheduler_batch_size": _integer(
                body.get("batchSize", current["batchSize"]), "batchSize", 2, 3
            ),
        })
        saved = self.config_saver(config)
        return self.present_settings(saved if isinstance(saved, dict) else config)

    @staticmethod
    def _public_unit(unit):
        last = unit.get("last_result") if isinstance(unit.get("last_result"), dict) else {}
        return {
            "id": _text(unit.get("unit_key")),
            "state": _text(unit.get("state")),
            "seasonNumber": unit.get("season_number"),
            "episodeNumber": unit.get("episode_number"),
            "windowHours": int(unit.get("window_hours") or 0),
            "baselineReadyAt": _text(unit.get("baseline_ready_at")),
            "nextCheckAt": _text(unit.get("next_check_at")),
            "observationEndsAt": _text(unit.get("observation_ends_at")),
            "attemptCount": int(unit.get("attempt_count") or 0),
            "currentOffsetIndex": int(unit.get("current_offset_index") or 0),
            "lastResult": {
                key: last[key]
                for key in ("reason", "actionId", "selectedCount", "offsetIndex", "window", "limit")
                if key in last
            },
        }

    def get_quality_watch(self, key):
        item = self._item(key)
        config = self._config()
        try:
            policy = resolve_watch_policy(item, config)
        except ValueError as exc:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SCHEDULE_INVALID", str(exc), 422) from exc
        units = self.repository.list_watch_units(key)
        return {
            "subscriptionId": key,
            "policy": {
                "windowHours": policy["window_hours"],
                "scheduleMinutes": policy["offsets_minutes"],
            },
            "paused": bool(units) and all(unit["state"] == "paused" for unit in units),
            "units": [self._public_unit(unit) for unit in units],
        }

    def _update_pause(self, key, paused):
        now = _as_utc(self.clock())
        for unit in self.repository.list_watch_units(key):
            state = unit["state"]
            if paused and state in ACTIVE_STATES:
                self.repository.update_watch_unit(
                    unit["unit_key"], unit["version"], state="paused", last_result_json={"reason": "manual_pause"}
                )
            elif not paused and state == "paused":
                ends_at = _as_utc(unit.get("observation_ends_at"))
                next_at = _as_utc(unit.get("next_check_at"))
                next_state = "observation_expired" if not ends_at or ends_at < now else (
                    "search_due" if next_at and next_at <= now else "observing_upgrade"
                )
                self.repository.update_watch_unit(
                    unit["unit_key"], unit["version"], state=next_state, last_result_json={"reason": "manual_resume"}
                )

    def update_quality_watch(self, key, body):
        self._require_write()
        item = self._item(key)
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, QUALITY_PATCH_FIELDS)
        if not body:
            raise AutomationApiError("SUBSCRIPTION_QUALITY_WATCH_EMPTY", "至少需要一个设置字段", 422)
        if "paused" in body and not isinstance(body["paused"], bool):
            raise AutomationApiError("SUBSCRIPTION_QUALITY_WATCH_INVALID", "paused 必须是布尔值", 422)
        current = resolve_watch_policy(item, self._config())
        window = _integer(body.get("windowHours", current["window_hours"]), "windowHours")
        if window not in {24, 48}:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_WINDOW_INVALID", "窗口只允许 24 或 48 小时", 422)
        schedule_value = body.get("scheduleMinutes", current["offsets_minutes"])
        schedule = self._schedule(window, schedule_value)

        def updater(row):
            nested = row.get("torra_quality_watch")
            nested = dict(nested) if isinstance(nested, dict) else {}
            nested.update({"window_hours": window, "offsets_minutes": schedule})
            row["torra_quality_watch"] = nested

        if not self.subscription_updater(key, updater):
            raise AutomationApiError("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        if "paused" in body:
            self._update_pause(key, body["paused"])
        return self.get_quality_watch(key)

    def _manual_unit(self, key, unit_id=""):
        units = self.repository.list_watch_units(key)
        eligible = [
            unit for unit in units
            if unit.get("baseline_ready_at") and unit.get("torra_subscription_id")
            and unit.get("state") not in {"waiting_first_version", "waiting_library_baseline", "blocked"}
        ]
        if unit_id:
            unit = next((item for item in eligible if item["unit_key"] == unit_id), None)
            if not unit:
                raise AutomationApiError("QUALITY_WATCH_UNIT_NOT_FOUND", "订阅观察单元不存在或尚未就绪", 404)
            return unit
        if len(eligible) != 1:
            raise AutomationApiError("QUALITY_WATCH_UNIT_REQUIRED", "需要明确指定一个观察单元", 422)
        return eligible[0]

    def _claim_action(self, key, unit, idempotency_key, action_type, request_summary):
        config = self._config()
        claim = self.repository.claim_action(
            idempotency_key,
            key,
            "torra",
            action_type,
            unit_key=unit["unit_key"],
            request_summary=request_summary,
            cooldown_seconds=max(60, int(config.get("torra_quality_min_interval_minutes") or 60)) * 60,
            rate_limits={
                "hourly": max(1, int(config.get("torra_quality_hourly_limit") or 4)),
                "daily": max(1, int(config.get("torra_quality_daily_limit") or 30)),
            },
            require_idle=True,
        )
        disposition = claim["disposition"]
        if disposition in {"claimed", "reclaimed"}:
            return claim["action"], False
        if disposition in {"replay", "in_progress", "resume"}:
            return claim["action"], True
        if disposition == "rate_limited":
            raise AutomationApiError("TORRA_REWASH_RATE_LIMITED", "Torra 追更洗版动作已达到限额", 429)
        if disposition == "cooldown":
            raise AutomationApiError("TORRA_REWASH_COOLDOWN", "该观察单元仍在冷却时间内", 409)
        if disposition == "conflict":
            raise AutomationApiError("TORRA_REWASH_IDEMPOTENCY_CONFLICT", "幂等键已用于其他动作", 409)
        raise AutomationApiError("TORRA_REWASH_BUSY", "已有 Torra 追更洗版动作正在执行", 409)

    def _submit_analysis(self, action, unit):
        try:
            job_id = self.torra.submit_analysis(unit["torra_subscription_id"])
            return self.repository.save_external_job(action["action_id"], job_id)
        except Exception as exc:
            self.repository.complete_action(
                action["action_id"],
                "failed",
                {"message": "Torra 分析提交失败"},
                error_code="TORRA_ANALYSIS_SUBMIT_FAILED",
                error_message="Torra 分析提交失败",
            )
            raise AutomationApiError("TORRA_REWASH_UPSTREAM_FAILED", "Torra 分析提交失败", 502) from exc

    def create_analysis(self, key, body):
        self._require_analysis()
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, {"idempotencyKey", "unitId"})
        idempotency_key = self._validate_idempotency(body)
        item = self._item(key)
        unit = self._manual_unit(key, _text(body.get("unitId")))
        existing = self.repository.get_action_by_idempotency(idempotency_key)
        if not existing:
            if self.repository.find_inflight_action("torra", DOWNLOAD_TYPE):
                raise AutomationApiError("TORRA_REWASH_BUSY", "已有 Torra 追更洗版下载正在执行", 409)
            require_rewash_provider_ready(self.torra, self.qb, item, unit)
        action, immediate = self._claim_action(
            key,
            unit,
            idempotency_key,
            ANALYSIS_TYPE,
            {"source": MANUAL_SOURCE, "unitId": unit["unit_key"]},
        )
        return action if immediate else self._submit_analysis(action, unit)

    def _analysis_selection(self, key, unit, action_id):
        action = self.repository.get_action(action_id)
        if not action or action["subscription_key"] != key or action["unit_key"] != unit["unit_key"]:
            raise AutomationApiError("TORRA_ANALYSIS_ACTION_NOT_FOUND", "分析动作不存在", 404)
        if action["provider"] != "torra" or action["action_type"] != ANALYSIS_TYPE or action["status"] != "succeeded":
            raise AutomationApiError("TORRA_ANALYSIS_ACTION_NOT_READY", "分析动作尚未成功", 409)
        summary = action.get("response_summary") or {}
        analysis_id = _text(summary.get("analysisId"))
        selected = summary.get("selectedCandidates")
        if not analysis_id or not isinstance(selected, dict) or not selected:
            raise AutomationApiError("TORRA_ANALYSIS_HAS_NO_UPGRADE", "分析动作没有可下载的升级候选", 409)
        return analysis_id, selected

    def _submit_download(self, action, unit, analysis_id, selected):
        try:
            job_id = self.torra.submit_download(unit["torra_subscription_id"], analysis_id, selected)
            return self.repository.save_external_job(action["action_id"], job_id)
        except Exception as exc:
            self.repository.complete_action(
                action["action_id"],
                "failed",
                {"message": "Torra 下载提交失败"},
                error_code="TORRA_DOWNLOAD_SUBMIT_FAILED",
                error_message="Torra 下载提交失败",
            )
            raise AutomationApiError("TORRA_REWASH_UPSTREAM_FAILED", "Torra 下载提交失败", 502) from exc

    def create_download(self, key, body):
        self._require_download()
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, {"confirm", "idempotencyKey", "analysisActionId", "unitId"})
        if body.get("confirm") is not True:
            raise AutomationApiError("TORRA_REWASH_CONFIRMATION_REQUIRED", "下载需要明确确认", 422)
        idempotency_key = self._validate_idempotency(body)
        item = self._item(key)
        unit = self._manual_unit(key, _text(body.get("unitId")))
        analysis_id, selected = self._analysis_selection(key, unit, _text(body.get("analysisActionId")))
        existing = self.repository.get_action_by_idempotency(idempotency_key)
        if not existing:
            if self.repository.find_inflight_action("torra", ANALYSIS_TYPE):
                raise AutomationApiError("TORRA_REWASH_BUSY", "已有 Torra 追更洗版分析正在执行", 409)
            require_rewash_provider_ready(self.torra, self.qb, item, unit)
        action, immediate = self._claim_action(
            key,
            unit,
            idempotency_key,
            DOWNLOAD_TYPE,
            {
                "source": MANUAL_SOURCE,
                "unitId": unit["unit_key"],
                "analysisActionId": _text(body.get("analysisActionId")),
            },
        )
        return action if immediate else self._submit_download(action, unit, analysis_id, selected)

    def create_rss_analysis(self, match_id, body):
        self._require_analysis()
        if not self.rss_runtime:
            raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, {"idempotencyKey"})
        idempotency_key = self._validate_idempotency(body)
        result = self.rss_runtime.start_analysis(
            match_id,
            idempotency_key=idempotency_key,
            source=MANUAL_RSS_SOURCE,
            require_rss_gate=False,
        )
        status = result.get("status")
        if status in {"submitted", "polling", "in_progress", "replay", "triggered", "ignored"}:
            action = self.repository.get_action(result.get("actionId"))
            if action:
                return action
        reason = result.get("reason") or status
        if status == "missing":
            raise AutomationApiError("RSS_MATCH_NOT_FOUND", "RSS 匹配不存在", 404)
        if status == "rate_limited":
            raise AutomationApiError("TORRA_REWASH_RATE_LIMITED", "Torra 追更洗版动作已达到限额", 429)
        if status == "conflict":
            raise AutomationApiError("TORRA_REWASH_IDEMPOTENCY_CONFLICT", "幂等键已用于其他 RSS 匹配动作", 409)
        if status == "cooldown":
            raise AutomationApiError("TORRA_REWASH_COOLDOWN", "该观察单元仍在冷却时间内", 409)
        if status == "global_busy":
            raise AutomationApiError("TORRA_REWASH_BUSY", "已有 Torra 追更洗版动作正在执行", 409)
        if reason in {"window_expired", "watch_unit_missing", "subscription_missing", "torra_subscription_missing"}:
            raise AutomationApiError("RSS_MATCH_NOT_READY", "RSS 匹配已过期或观察单元不可用", 409)
        if reason in {"torra_unavailable", "qb_unavailable", "provider_check_failed"}:
            raise AutomationApiError("TORRA_REWASH_UPSTREAM_UNAVAILABLE", "Torra 或 qBittorrent 不可用", 502)
        raise AutomationApiError("TORRA_REWASH_BUSY", "RSS 匹配分析暂不可执行", 409)

    def _resume_claim(self, action):
        return self.repository.claim_action(
            action["idempotency_key"],
            action["subscription_key"],
            action["provider"],
            action["action_type"],
            unit_key=action["unit_key"],
        )

    def _complete_job(self, action, job):
        status = job["status"]
        if status in {"pending", "running"}:
            self.repository.save_external_job(action["action_id"], action["external_job_id"], status="polling")
            return {"status": "polling", "actionId": action["action_id"]}
        if status in {"failed", "cancelled"}:
            self.repository.complete_action(
                action["action_id"],
                status,
                {"jobStatus": status},
                error_code=f"TORRA_{action['action_type'].upper().replace('-', '_')}_{status.upper()}",
                error_message=f"Torra 任务{status}",
            )
            return {"status": status, "actionId": action["action_id"]}
        if action["action_type"] == ANALYSIS_TYPE:
            selection = self.torra.select_upgrade_candidates(job)
            summary = {
                "jobStatus": "success",
                "analysisId": selection["analysis_id"],
                "selectedCandidates": selection["selected_candidates"],
                "rowCount": selection["row_count"],
                "selectedCount": selection["selected_count"],
            }
        else:
            summary = {"jobStatus": "success", "downloadAccepted": True}
        self.repository.complete_action(action["action_id"], "succeeded", summary)
        return {"status": "succeeded", "actionId": action["action_id"]}

    def resume_action(self, action):
        source = action.get("request_summary", {}).get("source")
        if source == MANUAL_RSS_SOURCE and self.rss_runtime:
            return self.rss_runtime.start_analysis(
                action.get("request_summary", {}).get("matchId"),
                idempotency_key=action["idempotency_key"],
                source=MANUAL_RSS_SOURCE,
                require_rss_gate=False,
            )
        if source != MANUAL_SOURCE:
            return {"status": "global_busy", "actionId": action["action_id"]}
        claim = self._resume_claim(action)
        if claim["disposition"] == "resume":
            try:
                return self._complete_job(claim["action"], self.torra.get_job(action["external_job_id"]))
            except Exception:
                return {"status": "poll_failed", "actionId": action["action_id"]}
        if claim["disposition"] == "reclaimed":
            if action["action_type"] == ANALYSIS_TYPE:
                self._require_analysis()
            else:
                self._require_download()
            item = self._item(action["subscription_key"])
            unit = self._manual_unit(action["subscription_key"], action["unit_key"])
            require_rewash_provider_ready(self.torra, self.qb, item, unit)
            if action["action_type"] == ANALYSIS_TYPE:
                self._submit_analysis(claim["action"], unit)
            else:
                analysis_action_id = action.get("request_summary", {}).get("analysisActionId")
                analysis_id, selected = self._analysis_selection(action["subscription_key"], unit, analysis_action_id)
                self._submit_download(claim["action"], unit, analysis_id, selected)
            return {"status": "submitted", "actionId": action["action_id"]}
        return {"status": claim["disposition"], "actionId": action["action_id"]}
