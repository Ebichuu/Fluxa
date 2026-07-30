from __future__ import annotations

import re


def _optional_nonnegative_integer(value):
    match = re.fullmatch(r"\d+", str(value if value is not None else "").strip())
    return int(match.group(0)) if match else None


def _field(item: dict, snake_name: str, camel_name: str = ""):
    if snake_name in item:
        return item.get(snake_name)
    return item.get(camel_name) if camel_name else None


def extract_response_items(payload):
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data if all(isinstance(item, dict) for item in data) else None
    if not isinstance(data, dict):
        return None
    for key in ("items", "jobs", "schedules", "tasks"):
        items = data.get(key)
        if isinstance(items, list):
            return items if all(isinstance(item, dict) for item in items) else None
    return None


def extract_response_object(payload):
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else None


def project_schedule(item: dict | None):
    if item is None:
        return None
    enabled = item.get("enabled") if isinstance(item.get("enabled"), bool) else None
    return {
        "registered": True,
        "enabled": enabled,
        "lastRunAt": str(_field(item, "last_run_at", "lastRunAt") or ""),
        "nextRunAt": str(_field(item, "next_run_at", "nextRunAt") or ""),
    }


def latest_subscription_batch(items: list[dict]):
    batches = [
        item
        for item in items
        if str(item.get("kind") or "").strip() == "subscription.batch_run"
    ]
    return max(
        batches,
        key=lambda item: str(
            _field(item, "created_at", "createdAt")
            or _field(item, "started_at", "startedAt")
            or ""
        ),
        default=None,
    )


def _normalized_batch_mode(value) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "rss":
        return "rss"
    if normalized == "auto":
        return "automatic_search"
    return "unknown"


def _normalized_batch_status(value) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"pending", "queued", "running", "success", "failed", "cancelled"}:
        return normalized
    return "unknown"


def _normalized_batch_trigger(value) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "manual":
        return "manual"
    if normalized in {"schedule", "scheduler"}:
        return "scheduler"
    return "unknown"


def _batch_subscription_count(payload: dict, result: dict):
    for item in (result, payload):
        value = _optional_nonnegative_integer(
            _field(item, "subscription_count", "subscriptionCount")
        )
        if value is not None:
            return value
    subscription_ids = _field(payload, "subscription_ids", "subscriptionIds")
    return len(subscription_ids) if isinstance(subscription_ids, list) else None


def _batch_site_request_count(result: dict):
    for snake_name, camel_name in (
        ("site_request_count", "siteRequestCount"),
        ("estimated_site_requests", "estimatedSiteRequests"),
    ):
        value = _optional_nonnegative_integer(_field(result, snake_name, camel_name))
        if value is not None:
            return value
    return None


def project_batch(overview: dict, detail: dict) -> dict:
    source = detail or overview
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    result = source.get("result") if isinstance(source.get("result"), dict) else {}
    mode = (
        _field(payload, "mode_override", "modeOverride")
        or payload.get("mode")
        or result.get("mode")
    )
    return {
        "mode": _normalized_batch_mode(mode),
        "status": _normalized_batch_status(source.get("status") or overview.get("status")),
        "trigger": _normalized_batch_trigger(
            _field(source, "trigger_source", "triggerSource")
            or _field(overview, "trigger_source", "triggerSource")
        ),
        "startedAt": str(
            _field(source, "started_at", "startedAt")
            or _field(overview, "started_at", "startedAt")
            or _field(source, "created_at", "createdAt")
            or _field(overview, "created_at", "createdAt")
            or ""
        ),
        "finishedAt": str(
            _field(source, "finished_at", "finishedAt")
            or _field(overview, "finished_at", "finishedAt")
            or ""
        ),
        "subscriptionCount": _batch_subscription_count(payload, result),
        "estimatedSiteRequests": _batch_site_request_count(result),
    }


def unavailable_search_automation(total: int, *, connected: bool) -> dict:
    state = "unsupported" if connected else "unknown"
    reason_code = (
        "TORRA_SUBSCRIPTION_MODE_NOT_EXPOSED"
        if connected
        else "TORRA_SEARCH_AUTOMATION_NOT_READ"
    )
    return {
        "capabilityState": state,
        "subscriptionModes": {
            "state": state,
            "counts": {
                "rssPreferred": None,
                "automaticSearch": None,
                "unknown": total,
            },
            "reasonCode": reason_code,
            "reasonText": (
                "Torra 未提供可确认的订阅级搜索模式"
                if connected
                else "Torra 搜索策略尚未读取"
            ),
        },
        "schedules": {
            "state": state,
            "rss": None,
            "automaticSearch": None,
            "reasonCode": (
                "TORRA_SCHEDULES_ENDPOINT_UNAVAILABLE"
                if connected
                else "TORRA_SCHEDULES_NOT_READ"
            ),
        },
        "recentBatchState": state,
        "recentBatch": None,
        "recentBatchReasonCode": (
            "TORRA_BATCH_HISTORY_ENDPOINT_UNAVAILABLE"
            if connected
            else "TORRA_BATCH_HISTORY_NOT_READ"
        ),
        "adjustmentPreview": {
            "state": "blocked",
            "canApply": False,
            "eligibleSubscriptions": 0,
            "blockedSubscriptions": total,
            "reasonCode": reason_code,
            "reasonText": "无法安全确认哪些订阅可调整为 RSS 优先",
        },
    }


def search_automation_capability_state(schedules_state: str, jobs_state: str) -> str:
    if "confirmed" in {schedules_state, jobs_state}:
        return "partial"
    if "unknown" in {schedules_state, jobs_state}:
        return "unknown"
    return "unsupported"
