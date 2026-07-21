from __future__ import annotations

from datetime import datetime, timezone

from flask import Flask, jsonify

from app import discover_runtime
from app.contract_mapping import map_subscription_item


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _state(key, label, state, detail, *, enabled=False, configured=False, checked_at=""):
    return {
        "key": key,
        "label": label,
        "state": state,
        "enabled": bool(enabled),
        "configured": bool(configured),
        "detail": str(detail or ""),
        "checkedAt": checked_at,
    }


def _first_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if isinstance(value, list) and value:
            return value
        if value not in (None, "", []):
            return value
    return None


def _missing_episodes(row):
    value = _first_value(
        row,
        "missing_episode_numbers",
        "missing_episodes",
        "missingEpisodes",
        "episode_missing",
    )
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    if isinstance(value, (tuple, list, set)):
        return [str(part) for part in value if str(part).strip()]
    return []


def _scope(row):
    media_type = str(row.get("media_type") or row.get("mediaType") or "").lower()
    if media_type == "movie":
        return "整部电影"
    season = _first_value(row, "target_season", "current_season", "latest_season", "season_number", "season")
    if season not in (None, ""):
        return f"第 {season} 季"
    return "按剧集持续追更"


def _step_map(chain_item):
    return {
        str(step.get("key")): step
        for step in (chain_item or {}).get("steps", [])
        if isinstance(step, dict) and step.get("key")
    }


def _chain_item_for_row(row, chain):
    mapped = map_subscription_item(row) or {}
    candidates = [
        mapped.get("id"),
        row.get("key"),
        row.get("subscription_key"),
        row.get("dedupe_key"),
        row.get("id"),
        discover_runtime.get_subscription_item_key(row),
    ]
    return next((chain.get(str(value)) for value in candidates if value and chain.get(str(value))), None)


def _item_snapshot(row, chain_item=None):
    mapped = map_subscription_item(row) or {}
    chain_item = chain_item or {}
    steps = _step_map(chain_item)
    blocked = next((step for step in steps.values() if step.get("status") == "blocked"), None)
    qb = steps.get("download") or {}
    cloud = steps.get("cloud115") or {}
    library = steps.get("library") or {}
    source_ids = chain_item.get("sourceIds") or {}
    return {
        **mapped,
        "scope": _scope(row),
        "missingEpisodes": _missing_episodes(row),
        "torra": {
            "status": "linked" if source_ids.get("torraId") or row.get("torra_remote_id") else "not_linked",
            "remoteId": str(source_ids.get("torraId") or row.get("torra_remote_id") or ""),
            "detail": str((steps.get("subscription") or {}).get("detail") or "Torra 尚未关联"),
        },
        "qb": {
            "status": str(qb.get("status") or "unknown"),
            "detail": str(qb.get("detail") or "未接入 qB 任务证据"),
            "hashes": [str(value) for value in (source_ids.get("qbHashes") or [])],
        },
        "cloud115": {
            "status": str(cloud.get("status") or "unknown"),
            "detail": str(cloud.get("detail") or "未接入 115 记录"),
            "ids": [str(value) for value in (source_ids.get("symediaIds") or [])],
        },
        "library": {
            "status": str(library.get("status") or ("done" if mapped.get("inLibrary") else "waiting")),
            "detail": str(library.get("detail") or ("已入库" if mapped.get("inLibrary") else "尚未入库")),
        },
        "blockingReason": str(blocked.get("detail") if blocked else "") if blocked else "",
        "chainState": str(chain_item.get("state") or ("completed" if mapped.get("status") == "done" else "waiting")),
        "chainProgress": int(chain_item.get("progress") or 0),
    }


class SubscriptionWorkbenchService:
    def __init__(self, app: Flask, environment=None):
        self.app = app
        self.environment = environment if environment is not None else {}

    def snapshot(self):
        checked_at = _now()
        raw = discover_runtime.load_subscription_items(
            with_progress=False,
            remove_completed=False,
            persist_progress=False,
        )
        rows = [row for row in (raw.get("items") or []) if isinstance(row, dict)]
        chain = {}
        chain_error = ""
        task_service = self.app.extensions.get("mcc_task_chain_service")
        if task_service:
            try:
                chain_payload = task_service.get_chain()
                chain = {
                    str(item.get("sourceIds", {}).get("subscriptionId") or ""): item
                    for item in (chain_payload.get("items") or [])
                    if isinstance(item, dict) and item.get("sourceIds", {}).get("subscriptionId")
                }
            except Exception as exc:
                chain_error = str(exc) or "任务链读取失败"

        mapped_items = [_item_snapshot(row, _chain_item_for_row(row, chain)) for row in rows]
        stats = {
            "total": len(mapped_items),
            "movie": sum(item.get("mediaType") == "movie" for item in mapped_items),
            "tv": sum(item.get("mediaType") == "tv" for item in mapped_items),
            "pending": sum(item.get("chainState") not in {"completed"} for item in mapped_items),
            "inLibrary": sum(item.get("inLibrary") is True or item.get("library", {}).get("status") == "done" for item in mapped_items),
        }

        torra_sync = self.app.extensions.get("mcc_torra_subscription_sync")
        torra_status = torra_sync.status() if torra_sync else {"enabled": False, "linked": 0, "current": 0, "remoteMissing": 0, "errors": 0, "lastSyncedAt": ""}
        torra_client = self.app.extensions.get("mcc_torra_client")
        torra_configured = bool(torra_client and torra_client.is_configured())
        task_services = (chain_payload.get("services") if 'chain_payload' in locals() and isinstance(chain_payload, dict) else {}) or {}
        torra_service = task_services.get("torra") or {}
        torra_connected = bool(torra_service.get("connected")) if task_service else torra_configured
        torra_error = str(torra_service.get("error") or chain_error or "")
        rss = self.app.extensions.get("mcc_private_rss")
        rss_summary = rss.repository.summary(rss.collection_enabled()) if rss else {"enabled": False, "sources": 0, "activeSources": 0, "errorSources": 0, "items": 0, "lastSuccessAt": ""}
        config = discover_runtime.load_subscription_config() or {}
        douban = config.get("douban") if isinstance(config, dict) else {}
        douban = douban if isinstance(douban, dict) else {}
        write_enabled = _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))
        scheduler_enabled = bool(douban.get("enabled") and douban.get("task_enabled"))
        capabilities = [
            _state("local_write", "本地写入", "ready" if write_enabled else "disabled", "可保存和管理 Fluxa 本地订阅" if write_enabled else "本地订阅写入已关闭", enabled=write_enabled, configured=True, checked_at=checked_at),
            _state("torra_connection", "Torra 连接", "ready" if torra_connected else ("error" if torra_error else "disabled"), "连接正常" if torra_connected else (torra_error or ("Torra 未配置" if not torra_configured else "暂未建立连接")), enabled=torra_connected, configured=torra_configured, checked_at=checked_at),
            _state("torra_mirror", "镜像同步", "ready" if torra_status.get("enabled") and not torra_status.get("errors") else ("error" if torra_status.get("errors") else "disabled"), f"已关联 {torra_status.get('linked', 0)} 条，最近同步 {torra_status.get('lastSyncedAt') or '尚未同步'}" if torra_status.get("enabled") else "Torra 订阅镜像未开启", enabled=bool(torra_status.get("enabled")), configured=torra_configured, checked_at=checked_at),
            _state("rss", "RSS", "ready" if rss_summary.get("enabled") else "disabled", f"{rss_summary.get('activeSources', 0)} 个来源，{rss_summary.get('items', 0)} 条种子" if rss_summary.get("enabled") else "RSS 采集未开启", enabled=bool(rss_summary.get("enabled")), configured=bool(rss_summary.get("sources")), checked_at=checked_at),
            _state("scheduler", "定时任务", "ready" if scheduler_enabled else "disabled", f"每日 {douban.get('task_time') or '08:30'} 自动更新来源" if scheduler_enabled else "自动订阅定时任务未开启", enabled=scheduler_enabled, configured=bool(douban), checked_at=checked_at),
        ]
        return {
            "ok": True,
            "lastReadAt": checked_at,
            "capabilities": capabilities,
            "stats": stats,
            "items": mapped_items,
            "blockedTitles": discover_runtime.subscription_blocked_titles(),
            "errors": list(raw.get("errors") or []) + ([chain_error] if chain_error else []),
            "torraSync": torra_status,
            "rss": rss_summary,
            "scheduler": {"enabled": scheduler_enabled, "taskTime": str(douban.get("task_time") or "08:30"), "lastRunAt": str(douban.get("last_run_at") or "")},
        }


def register_subscription_workbench(app: Flask, environment=None):
    service = SubscriptionWorkbenchService(app, environment=environment)
    app.extensions["mcc_subscription_workbench"] = service

    @app.get("/api/v2/subscriptions/workbench")
    def subscription_workbench():
        try:
            return jsonify(service.snapshot())
        except Exception:
            return jsonify({"ok": False, "code": "SUBSCRIPTION_WORKBENCH_READ_FAILED", "error": "订阅工作台读取失败"}), 502

    return service
