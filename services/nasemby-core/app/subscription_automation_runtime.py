from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.quality_watch_repository import DEFAULT_LIFECYCLE_MODE, WATCH_LIFECYCLE_MODES
from app.quality_watch_runtime import DEFAULT_OFFSETS, resolve_watch_policy
from app.subscription_automation_api_runtime import AutomationApiError
from app.subscription_automation_preflight import require_rewash_provider_ready
from app.torra_subscription_keys import (
    resolve_torra_subscription_key,
    torra_internal_unit_key,
    torra_public_storage_key,
    torra_public_unit_key,
)


ANALYSIS_TYPE = "rewash-analysis"
DOWNLOAD_TYPE = "rewash-download"
MANUAL_SOURCE = "manual-subscription"
MANUAL_RSS_SOURCE = "manual-rss"
ACTIVE_STATES = {"observing_upgrade", "search_due", "search_running"}
PERMANENT_RECLAIM_ERRORS = {
    "SUBSCRIPTION_NOT_FOUND",
    "QUALITY_WATCH_UNIT_NOT_FOUND",
    "QUALITY_WATCH_UNIT_REQUIRED",
    "QUALITY_WATCH_WINDOW_EXPIRED",
    "TORRA_REWASH_SUBSCRIPTION_MISSING",
    "TORRA_ANALYSIS_ACTION_NOT_FOUND",
    "TORRA_ANALYSIS_ACTION_NOT_READY",
    "TORRA_ANALYSIS_HAS_NO_UPGRADE",
}
SETTINGS_FIELDS = {
    "enabled",
    "missingFallbackEnabled",
    "lifecycleMode",
    "defaultWindowHours",
    "scheduleMinutes",
    "minIntervalMinutes",
    "hourlyLimit",
    "dailyLimit",
    "batchSize",
    "bridgeMode",
    "bridgeModeConfirm",
}
QUALITY_PATCH_FIELDS = {"paused", "lifecycleMode", "windowHours", "scheduleMinutes"}


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
    bridge_runtime: object = None
    baseline_initializer: object = None
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
        self.bridge_runtime = dependencies.bridge_runtime
        self.baseline_initializer = dependencies.baseline_initializer
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

    def _local_torra_context(self, key):
        normalized = _text(key)
        matches = []
        for internal_key, item in self._subscriptions().items():
            if not internal_key.startswith("torra:"):
                continue
            public_key = torra_public_storage_key(
                internal_key,
                item.get("torra_remote_id"),
            )
            if normalized not in {internal_key, public_key}:
                continue
            matches.append({
                "item": item,
                "internalKey": internal_key,
                "publicKey": public_key,
                "readOnly": True,
                "local": True,
            })
        if len(matches) > 1:
            raise AutomationApiError(
                "TORRA_SUBSCRIPTION_KEY_CONFLICT",
                "Torra 订阅公开标识发生冲突",
                409,
            )
        return matches[0] if matches else None

    def _item_or_torra(self, key):
        normalized = _text(key)
        subscriptions = self._subscriptions()
        if not normalized.startswith("torra:"):
            item = subscriptions.get(normalized)
            if not item:
                raise AutomationApiError("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
            return {
                "item": item,
                "internalKey": normalized,
                "publicKey": normalized,
                "readOnly": False,
                "local": True,
            }

        local_context = self._local_torra_context(normalized)
        if local_context:
            return local_context
        try:
            if self.torra is None or not self.torra.is_configured():
                raise AutomationApiError(
                    "TORRA_REWASH_UPSTREAM_UNAVAILABLE",
                    "Torra 未配置或不可用",
                    502,
                )
            rows = self.torra.list_subscriptions()
        except AutomationApiError:
            raise
        except Exception as exc:
            raise AutomationApiError("TORRA_REWASH_UPSTREAM_UNAVAILABLE", "Torra 状态检查失败", 502) from exc
        resolved = resolve_torra_subscription_key(normalized, rows)
        if resolved.get("status") == "conflict":
            raise AutomationApiError(
                "TORRA_SUBSCRIPTION_KEY_CONFLICT",
                "Torra 订阅公开标识发生冲突",
                409,
            )
        if resolved.get("status") != "resolved":
            raise AutomationApiError("SUBSCRIPTION_NOT_FOUND", "Torra 订阅不存在", 404)
        local_key = next((
            candidate
            for candidate in (resolved["canonicalKey"], resolved["publicKey"])
            if candidate in subscriptions
        ), "")
        row = dict(subscriptions.get(local_key) or resolved["item"])
        internal_key = local_key or resolved["canonicalKey"]
        row["key"] = internal_key
        row.setdefault("source", "torra")
        return {
            "item": row,
            "internalKey": internal_key,
            "publicKey": resolved["publicKey"],
            "readOnly": True,
            "local": bool(local_key),
        }

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
    def _require_matching_request(existing, request_summary):
        if existing and existing.get("request_summary") != request_summary:
            raise AutomationApiError(
                "TORRA_REWASH_IDEMPOTENCY_CONFLICT",
                "幂等键已用于其他请求",
                409,
            )

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
        policy = resolve_watch_policy({}, config)
        window = policy["window_hours"]
        result = {
            "enabled": _truthy(config.get("torra_quality_watch_enabled")),
            "missingFallbackEnabled": _truthy(
                config.get("torra_quality_missing_fallback_enabled")
            ),
            "environmentEnabled": _truthy(self.environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED")),
            "downloadEnvironmentEnabled": _truthy(self.environment.get("MCC_TORRA_REWASH_DOWNLOAD_ENABLED")),
            "lifecycleMode": policy["lifecycle_mode"],
            "defaultWindowHours": window,
            "scheduleMinutes": policy["offsets_minutes"],
            "minIntervalMinutes": int(config.get("torra_quality_min_interval_minutes") or 60),
            "hourlyLimit": int(config.get("torra_quality_hourly_limit") or 4),
            "dailyLimit": int(config.get("torra_quality_daily_limit") or 30),
            "batchSize": int(config.get("torra_quality_scheduler_batch_size") or 2),
        }
        if self.bridge_runtime:
            result["bridgeMode"] = self.bridge_runtime.summary()["mode"]
        return result

    def update_settings(self, body):
        self._require_write()
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, SETTINGS_FIELDS)
        if not body:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_EMPTY", "至少需要一个设置字段", 422)
        if "bridgeModeConfirm" in body and "bridgeMode" not in body:
            raise AutomationApiError(
                "QUALITY_WATCH_BRIDGE_MODE_REQUIRED", "确认生产桥接时必须指定 bridgeMode", 422
            )
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
        lifecycle_mode = _text(body.get("lifecycleMode", current["lifecycleMode"])).lower()
        if lifecycle_mode not in WATCH_LIFECYCLE_MODES:
            raise AutomationApiError(
                "SUBSCRIPTION_AUTOMATION_LIFECYCLE_INVALID",
                "lifecycleMode 只允许 follow_rss 或 fixed_window",
                422,
            )
        if "enabled" in body and not isinstance(body["enabled"], bool):
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID", "enabled 必须是布尔值", 422)
        if "missingFallbackEnabled" in body and not isinstance(body["missingFallbackEnabled"], bool):
            raise AutomationApiError(
                "SUBSCRIPTION_AUTOMATION_SETTINGS_INVALID",
                "missingFallbackEnabled 必须是布尔值",
                422,
            )
        bridge_mode = _text(body.get("bridgeMode", current.get("bridgeMode", ""))).lower()
        if "bridgeMode" in body:
            if body.get("bridgeModeConfirm") is not True:
                raise AutomationApiError(
                    "QUALITY_WATCH_BRIDGE_CONFIRM_REQUIRED",
                    "切换生产桥接模式需要明确确认",
                    422,
                )
            if not self.bridge_runtime:
                raise AutomationApiError(
                    "QUALITY_WATCH_BRIDGE_UNAVAILABLE", "生产桥接运行时不可用", 503
                )
            if bridge_mode not in {"off", "shadow", "apply"}:
                raise AutomationApiError(
                    "QUALITY_WATCH_BRIDGE_MODE_INVALID",
                    "bridgeMode 只允许 off、shadow 或 apply",
                    422,
                )
            state = self.bridge_runtime.summary()
            if bridge_mode == "apply" and not state.get("activatedAt"):
                raise AutomationApiError(
                    "QUALITY_WATCH_BRIDGE_SHADOW_REQUIRED",
                    "正式桥接前必须先启用影子模式并完成核对",
                    409,
                )
        config.update({
            "torra_quality_watch_enabled": body.get("enabled", current["enabled"]),
            "torra_quality_missing_fallback_enabled": body.get(
                "missingFallbackEnabled", current["missingFallbackEnabled"]
            ),
            "torra_quality_lifecycle_mode": lifecycle_mode,
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
        if "bridgeMode" in body:
            try:
                self.bridge_runtime.set_mode(bridge_mode)
            except ValueError as exc:
                raise AutomationApiError(
                    "QUALITY_WATCH_BRIDGE_TRANSITION_INVALID", "生产桥接状态无法切换", 409
                ) from exc
        return self.present_settings(saved if isinstance(saved, dict) else config)

    def get_bridge_summary(self):
        if not self.bridge_runtime:
            raise AutomationApiError(
                "QUALITY_WATCH_BRIDGE_UNAVAILABLE", "生产桥接运行时不可用", 503
            )
        return self.bridge_runtime.summary()

    def create_baseline_initialization_preview(self):
        self._require_write()
        if not self.baseline_initializer:
            raise AutomationApiError(
                "BASELINE_INITIALIZATION_UNAVAILABLE", "历史基线初始化运行时不可用", 503
            )
        return self.baseline_initializer.preview()

    def execute_baseline_initialization(self, body):
        self._require_write()
        if not self.baseline_initializer:
            raise AutomationApiError(
                "BASELINE_INITIALIZATION_UNAVAILABLE", "历史基线初始化运行时不可用", 503
            )
        return self.baseline_initializer.execute(body)

    def get_baseline_initialization(self, run_id):
        if not self.baseline_initializer:
            raise AutomationApiError(
                "BASELINE_INITIALIZATION_UNAVAILABLE", "历史基线初始化运行时不可用", 503
            )
        return self.baseline_initializer.get_run(run_id)

    @staticmethod
    def _public_unit(unit, internal_key="", public_key=""):
        last = unit.get("last_result") if isinstance(unit.get("last_result"), dict) else {}
        return {
            "id": torra_public_unit_key(unit.get("unit_key"), internal_key, public_key),
            "state": _text(unit.get("state")),
            "seasonNumber": unit.get("season_number"),
            "episodeNumber": unit.get("episode_number"),
            "windowHours": int(unit.get("window_hours") or 0),
            "lifecycleMode": _text(unit.get("lifecycle_mode")) or DEFAULT_LIFECYCLE_MODE,
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
        key = _text(key)
        context = self._item_or_torra(key)
        item = context["item"]
        config = self._config()
        try:
            policy = resolve_watch_policy(item, config)
        except ValueError as exc:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_SCHEDULE_INVALID", str(exc), 422) from exc
        units = self.repository.list_watch_units(context["internalKey"])
        missing_fallback = self._missing_fallback_projection(context["internalKey"], config)
        return {
            "subscriptionId": context["publicKey"],
            "readOnly": context["readOnly"],
            "policy": {
                "lifecycleMode": policy["lifecycle_mode"],
                "windowHours": policy["window_hours"],
                "scheduleMinutes": policy["offsets_minutes"],
            },
            "paused": bool(units) and all(unit["state"] == "paused" for unit in units),
            "missingFallback": missing_fallback,
            "units": [
                self._public_unit(unit, context["internalKey"], context["publicKey"])
                for unit in units
            ],
        }

    def _missing_fallback_projection(self, subscription_key, config):
        enabled = bool(
            _truthy(self.environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED"))
            and _truthy(config.get("torra_quality_watch_enabled"))
            and _truthy(config.get("torra_quality_missing_fallback_enabled"))
        )
        action = self.repository.latest_subscription_action(
            subscription_key,
            "torra",
            ANALYSIS_TYPE,
            source="missing-episode-fallback",
        )
        if not action:
            return {
                "enabled": enabled,
                "state": "idle" if enabled else "disabled",
                "reasonText": "等待可靠缺集证据" if enabled else "缺集 PT 搜索兜底未启用",
                "episodeNumbers": [],
                "actionId": "",
                "observedAt": "",
            }
        state = {
            "claimed": "queued",
            "submitted": "running",
            "polling": "running",
            "succeeded": "checked",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(_text(action.get("status")), "unknown")
        reason_text = {
            "queued": "已排队，等待单订阅缺集分析",
            "running": "正在执行单订阅缺集分析",
            "checked": "最近一次缺集分析已完成",
            "failed": "最近一次缺集分析失败",
            "cancelled": "最近一次缺集分析已取消",
            "unknown": "缺集分析状态暂未确认",
        }[state]
        if not enabled:
            reason_text = f"已关闭 · {reason_text}"
        episodes = sorted({
            int(value)
            for value in action.get("request_summary", {}).get("episodeNumbers") or []
            if str(value).isdigit() and int(value) > 0
        })
        return {
            "enabled": enabled,
            "state": state,
            "reasonText": reason_text,
            "episodeNumbers": episodes,
            "actionId": _text(action.get("action_id")),
            "observedAt": _text(action.get("updated_at")),
        }

    @staticmethod
    def _lifecycle_changes(unit, lifecycle_mode):
        changes = {}
        state = unit["state"]
        if unit.get("lifecycle_mode") != lifecycle_mode:
            changes["lifecycle_mode"] = lifecycle_mode
        if lifecycle_mode != DEFAULT_LIFECYCLE_MODE:
            return changes
        if state == "search_due":
            changes.update(state="observing_upgrade", last_result_json={"reason": "following_rss"})
        ends_at = _text(unit.get("observation_ends_at"))
        if state != "search_running" and ends_at:
            changes["next_check_at"] = ends_at
        return changes

    @staticmethod
    def _pause_changes(unit, lifecycle_mode, paused, now):
        state = unit["state"]
        if paused is True:
            return (
                {"state": "paused", "last_result_json": {"reason": "manual_pause"}}
                if state in ACTIVE_STATES else {}
            )
        if paused is not False or state != "paused":
            return {}
        ends_at = _as_utc(unit.get("observation_ends_at"))
        if not ends_at or ends_at < now:
            next_state = "observation_expired"
        elif lifecycle_mode == "fixed_window":
            next_at = _as_utc(unit.get("next_check_at"))
            next_state = "search_due" if next_at and next_at <= now else "observing_upgrade"
        else:
            next_state = "observing_upgrade"
        return {"state": next_state, "last_result_json": {"reason": "manual_resume"}}

    def _update_units(self, key, lifecycle_mode, paused=None):
        now = _as_utc(self.clock())
        for unit in self.repository.list_watch_units(key):
            changes = self._lifecycle_changes(unit, lifecycle_mode)
            changes.update(self._pause_changes(unit, lifecycle_mode, paused, now))
            if changes:
                self.repository.update_watch_unit(unit["unit_key"], unit["version"], **changes)

    def update_quality_watch(self, key, body):
        self._require_write()
        normalized = _text(key)
        if normalized.startswith("torra:"):
            context = self._local_torra_context(normalized)
            if not context:
                raise AutomationApiError("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        else:
            context = self._item_or_torra(normalized)
        item = context["item"]
        internal_key = context["internalKey"]
        body = body if isinstance(body, dict) else {}
        self._validate_fields(body, QUALITY_PATCH_FIELDS)
        if not body:
            raise AutomationApiError("SUBSCRIPTION_QUALITY_WATCH_EMPTY", "至少需要一个设置字段", 422)
        if "paused" in body and not isinstance(body["paused"], bool):
            raise AutomationApiError("SUBSCRIPTION_QUALITY_WATCH_INVALID", "paused 必须是布尔值", 422)
        current = resolve_watch_policy(item, self._config())
        lifecycle_mode = _text(body.get("lifecycleMode", current["lifecycle_mode"])).lower()
        if lifecycle_mode not in WATCH_LIFECYCLE_MODES:
            raise AutomationApiError(
                "SUBSCRIPTION_AUTOMATION_LIFECYCLE_INVALID",
                "lifecycleMode 只允许 follow_rss 或 fixed_window",
                422,
            )
        window = _integer(body.get("windowHours", current["window_hours"]), "windowHours")
        if window not in {24, 48}:
            raise AutomationApiError("SUBSCRIPTION_AUTOMATION_WINDOW_INVALID", "窗口只允许 24 或 48 小时", 422)
        schedule_value = body.get("scheduleMinutes", current["offsets_minutes"])
        schedule = self._schedule(window, schedule_value)

        def updater(row):
            nested = row.get("torra_quality_watch")
            nested = dict(nested) if isinstance(nested, dict) else {}
            nested.update({
                "lifecycle_mode": lifecycle_mode,
                "window_hours": window,
                "offsets_minutes": schedule,
            })
            row["torra_quality_watch"] = nested

        if not self.subscription_updater(internal_key, updater):
            raise AutomationApiError("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        self._update_units(internal_key, lifecycle_mode, body.get("paused"))
        return self.get_quality_watch(context["publicKey"])

    def _manual_unit(self, key, unit_id="", public_key=""):
        units = self.repository.list_watch_units(key)
        eligible = [
            unit for unit in units
            if unit.get("baseline_ready_at") and unit.get("torra_subscription_id")
            and unit.get("state") not in {"waiting_first_version", "waiting_library_baseline", "blocked"}
        ]
        if unit_id:
            internal_unit_id = torra_internal_unit_key(unit_id, key, public_key)
            unit = next((item for item in eligible if item["unit_key"] == internal_unit_id), None)
            if not unit:
                raise AutomationApiError("QUALITY_WATCH_UNIT_NOT_FOUND", "订阅观察单元不存在或尚未就绪", 404)
            return unit
        if len(eligible) != 1:
            raise AutomationApiError("QUALITY_WATCH_UNIT_REQUIRED", "需要明确指定一个观察单元", 422)
        return eligible[0]

    def _require_download_window(self, unit):
        now = _as_utc(self.clock())
        ends_at = _as_utc(unit.get("observation_ends_at"))
        if unit.get("state") not in ACTIVE_STATES or not now or not ends_at or ends_at <= now:
            raise AutomationApiError(
                "QUALITY_WATCH_WINDOW_EXPIRED",
                "质量观察窗口已结束，不能继续下载",
                409,
            )

    def _cancel_reclaimed_action(self, action, error):
        return self.repository.complete_action(
            action["action_id"],
            "cancelled",
            {
                "reason": "reclaim_context_invalid",
                "errorCode": error.code,
            },
            http_status=error.status,
            error_code=error.code,
            error_message=error.message,
        )

    def _claim_action(self, key, unit, idempotency_key, action_type, request_summary, action_unit_key=""):
        config = self._config()
        claim = self.repository.claim_action(
            idempotency_key,
            key,
            "torra",
            action_type,
            unit_key=action_unit_key or unit["unit_key"],
            request_summary=request_summary,
            cooldown_seconds=max(60, int(config.get("torra_quality_min_interval_minutes") or 60)) * 60,
            rate_limits={
                "hourly": max(1, int(config.get("torra_quality_hourly_limit") or 4)),
                "daily": max(1, int(config.get("torra_quality_daily_limit") or 30)),
            },
            require_idle=True,
            require_provider_idle=True,
        )
        disposition = claim["disposition"]
        if disposition in {"claimed", "reclaimed"}:
            return claim["action"], False, disposition
        if disposition in {"replay", "in_progress", "resume"}:
            return claim["action"], True, disposition
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
        context = self._item_or_torra(key)
        item = context["item"]
        unit = self._manual_unit(
            context["internalKey"],
            _text(body.get("unitId")),
            context["publicKey"],
        )
        public_unit_key = torra_public_unit_key(
            unit["unit_key"], context["internalKey"], context["publicKey"]
        )
        request_summary = {"source": MANUAL_SOURCE, "unitId": public_unit_key}
        existing = self.repository.get_action_by_idempotency(idempotency_key)
        self._require_matching_request(existing, request_summary)
        if not existing:
            if self.repository.find_inflight_action("torra", DOWNLOAD_TYPE):
                raise AutomationApiError("TORRA_REWASH_BUSY", "已有 Torra 追更洗版下载正在执行", 409)
            require_rewash_provider_ready(self.torra, self.qb, item, unit)
        action, immediate, disposition = self._claim_action(
            context["publicKey"],
            unit,
            idempotency_key,
            ANALYSIS_TYPE,
            request_summary,
            public_unit_key,
        )
        if disposition == "reclaimed":
            try:
                require_rewash_provider_ready(self.torra, self.qb, item, unit)
            except AutomationApiError as error:
                if error.code in PERMANENT_RECLAIM_ERRORS:
                    self._cancel_reclaimed_action(action, error)
                raise
        return action if immediate else self._submit_analysis(action, unit)

    def _analysis_selection(self, key, unit_key, action_id):
        action = self.repository.get_action(action_id)
        if not action or action["subscription_key"] != key or action["unit_key"] != unit_key:
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
        context = self._item_or_torra(key)
        item = context["item"]
        unit = self._manual_unit(
            context["internalKey"],
            _text(body.get("unitId")),
            context["publicKey"],
        )
        public_unit_key = torra_public_unit_key(
            unit["unit_key"], context["internalKey"], context["publicKey"]
        )
        analysis_action_id = _text(body.get("analysisActionId"))
        analysis_id, selected = self._analysis_selection(
            context["publicKey"], public_unit_key, analysis_action_id
        )
        request_summary = {
            "source": MANUAL_SOURCE,
            "unitId": public_unit_key,
            "analysisActionId": analysis_action_id,
        }
        existing = self.repository.get_action_by_idempotency(idempotency_key)
        self._require_matching_request(existing, request_summary)
        if not existing:
            self._require_download_window(unit)
            if self.repository.find_inflight_action("torra", ANALYSIS_TYPE):
                raise AutomationApiError("TORRA_REWASH_BUSY", "已有 Torra 追更洗版分析正在执行", 409)
            require_rewash_provider_ready(self.torra, self.qb, item, unit)
        action, immediate, disposition = self._claim_action(
            context["publicKey"],
            unit,
            idempotency_key,
            DOWNLOAD_TYPE,
            request_summary,
            public_unit_key,
        )
        if disposition == "reclaimed":
            try:
                self._require_download_window(unit)
                require_rewash_provider_ready(self.torra, self.qb, item, unit)
            except AutomationApiError as error:
                if error.code in PERMANENT_RECLAIM_ERRORS:
                    self._cancel_reclaimed_action(action, error)
                raise
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
                "upgradeOptions": selection.get("upgrade_options") or [],
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
            try:
                if action["action_type"] == ANALYSIS_TYPE:
                    self._require_analysis()
                else:
                    self._require_download()
                context = self._item_or_torra(action["subscription_key"])
                item = context["item"]
                unit = self._manual_unit(
                    context["internalKey"],
                    action["unit_key"],
                    context["publicKey"],
                )
                if action["action_type"] == DOWNLOAD_TYPE:
                    self._require_download_window(unit)
                require_rewash_provider_ready(self.torra, self.qb, item, unit)
                if action["action_type"] == ANALYSIS_TYPE:
                    self._submit_analysis(claim["action"], unit)
                else:
                    analysis_action_id = action.get("request_summary", {}).get("analysisActionId")
                    analysis_id, selected = self._analysis_selection(
                        action["subscription_key"], action["unit_key"], analysis_action_id
                    )
                    self._submit_download(claim["action"], unit, analysis_id, selected)
            except AutomationApiError as error:
                if error.code in PERMANENT_RECLAIM_ERRORS:
                    self._cancel_reclaimed_action(claim["action"], error)
                raise
            return {"status": "submitted", "actionId": action["action_id"]}
        return {"status": claim["disposition"], "actionId": action["action_id"]}
