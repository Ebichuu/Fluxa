from __future__ import annotations

from app.rss_subscription_match_runtime import qb_task_matches
from app.subscription_automation_api_runtime import AutomationApiError


def _text(value):
    return str(value or "").strip()


def _require_torra_ready(torra, unit):
    if torra is None or not torra.is_configured():
        raise AutomationApiError("TORRA_REWASH_UPSTREAM_UNAVAILABLE", "Torra 未配置或不可用", 502)
    try:
        rows = torra.list_subscriptions()
    except Exception as exc:
        raise AutomationApiError("TORRA_REWASH_UPSTREAM_UNAVAILABLE", "Torra 状态检查失败", 502) from exc
    torra_id = _text(unit.get("torra_subscription_id"))
    row = next((value for value in rows if _text(value.get("id")) == torra_id), None)
    if not row:
        raise AutomationApiError("TORRA_REWASH_SUBSCRIPTION_MISSING", "Torra 订阅映射不存在", 409)
    if row.get("is_running") is True or row.get("is_mutating") is True:
        raise AutomationApiError("TORRA_REWASH_BUSY", "Torra 正在处理该订阅", 409)


def _require_qb_ready(qb, item, unit):
    try:
        summary = qb.summary() if qb else None
    except Exception as exc:
        raise AutomationApiError("TORRA_REWASH_UPSTREAM_UNAVAILABLE", "qBittorrent 状态检查失败", 502) from exc
    if not isinstance(summary, dict) or summary.get("connected") is not True:
        raise AutomationApiError("TORRA_REWASH_UPSTREAM_UNAVAILABLE", "qBittorrent 不可用", 502)
    if any(
        qb_task_matches(task, item, unit)
        for task in summary.get("tasks") or [] if isinstance(task, dict)
    ):
        raise AutomationApiError("TORRA_REWASH_QB_BUSY", "该观察单元已有活动下载", 409)


def require_rewash_provider_ready(torra, qb, item, unit):
    _require_torra_ready(torra, unit)
    _require_qb_ready(qb, item, unit)
