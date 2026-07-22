from __future__ import annotations

from datetime import datetime, timezone

from flask import Flask, jsonify, request

from app.http_runtime import current_request_id
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

    def snapshot(self, *, limit=None, offset=0, media_type="", query=""):
        checked_at = _now()
        raw = discover_runtime.load_subscription_items(
            with_progress=False,
            remove_completed=False,
            persist_progress=False,
        )
        rows = [row for row in (raw.get("items") or []) if isinstance(row, dict)]
        reconciliation = None
        reconciliation_error = ""
        reconciliation_service = self.app.extensions.get("mcc_subscription_reconciliation")
        if reconciliation_service:
            try:
                reconciliation = reconciliation_service.snapshot()
            except Exception as exc:
                reconciliation_error = str(exc) or "追更对账读取失败"
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

        reconciliation_items = {
            str(item.get("localId") or ""): item
            for item in (reconciliation or {}).get("items", [])
            if isinstance(item, dict) and item.get("localId")
        }
        mapped_items = []
        for row in rows:
            mapped = _item_snapshot(row, _chain_item_for_row(row, chain))
            local_key = str(mapped.get("id") or discover_runtime.get_subscription_item_key(row) or "")
            recon = reconciliation_items.get(local_key)
            if recon:
                mapped.update({
                    "reconciliationState": recon.get("reconciliationState"),
                    "fulfillmentState": recon.get("fulfillmentState"),
                    "healthState": recon.get("healthState"),
                    "reasonCode": recon.get("reasonCode"),
                    "reasonText": recon.get("reasonText"),
                    "observedAt": recon.get("observedAt"),
                    "freshUntil": recon.get("freshUntil"),
                })
            mapped_items.append(mapped)

        for recon in (reconciliation or {}).get("items", []):
            if not isinstance(recon, dict) or recon.get("localId") or recon.get("reconciliationState") != "only_torra":
                continue
            mapped_items.append({
                "id": recon.get("id"),
                "title": recon.get("title") or "未命名订阅",
                "seasonName": f"第 {recon.get('seasonNumber', 0)} 季" if recon.get("mediaType") == "tv" else "",
                "seasonNumber": recon.get("seasonNumber"),
                "mediaType": recon.get("mediaType") or "unknown",
                "tmdbId": recon.get("tmdbId") or "",
                "posterUrl": "",
                "progressText": "Torra 已有订阅，待建立本地镜像",
                "inLibrary": False,
                "updatedAt": recon.get("observedAt") or checked_at,
                "createdAt": recon.get("observedAt") or checked_at,
                "sourceLabel": "Torra 已有订阅",
                "status": "pending",
                "origin": "torra",
                "readOnly": True,
                "torraSyncState": "current",
                "torraMappingStatus": "mapped",
                "reconciliationState": recon.get("reconciliationState"),
                "fulfillmentState": recon.get("fulfillmentState"),
                "healthState": recon.get("healthState"),
                "reasonCode": recon.get("reasonCode"),
                "reasonText": recon.get("reasonText"),
                "observedAt": recon.get("observedAt"),
                "freshUntil": recon.get("freshUntil"),
            })
        stats = {
            "total": len(mapped_items),
            "movie": sum(item.get("mediaType") == "movie" for item in mapped_items),
            "tv": sum(item.get("mediaType") == "tv" for item in mapped_items),
            "pending": sum(item.get("chainState") not in {"completed"} for item in mapped_items),
            "inLibrary": sum(item.get("inLibrary") is True or item.get("library", {}).get("status") == "done" for item in mapped_items),
        }
        filtered_items = mapped_items
        if media_type in {"movie", "tv"}:
            filtered_items = [item for item in filtered_items if item.get("mediaType") == media_type]
        normalized_query = str(query or "").strip().casefold()
        if normalized_query:
            filtered_items = [
                item for item in filtered_items
                if normalized_query in " ".join((
                    str(item.get("title") or ""),
                    str(item.get("tmdbId") or ""),
                    str(item.get("sourceLabel") or ""),
                )).casefold()
            ]
        page_total = len(filtered_items)
        page_offset = max(0, int(offset or 0))
        page_limit = max(1, min(100, int(limit))) if limit is not None else max(1, page_total or 1)
        paged_items = filtered_items[page_offset:page_offset + page_limit] if limit is not None else filtered_items
        next_offset = page_offset + len(paged_items)

        torra_sync = self.app.extensions.get("mcc_torra_subscription_sync")
        torra_status = torra_sync.status() if torra_sync else {"enabled": False, "linked": 0, "current": 0, "remoteMissing": 0, "errors": 0, "lastSyncedAt": ""}
        torra_client = self.app.extensions.get("mcc_torra_client")
        torra_configured = bool(torra_client and torra_client.is_configured())
        task_services = (chain_payload.get("services") if 'chain_payload' in locals() and isinstance(chain_payload, dict) else {}) or {}
        torra_service = task_services.get("torra") or {}
        torra_connected = bool(torra_service.get("connected")) if task_service else torra_configured
        torra_error = str(torra_service.get("error") or chain_error or "")
        rss = self.app.extensions.get("mcc_private_rss")
        rss_summary = rss.repository.summary(rss.collection_enabled()) if rss else {
            "enabled": False,
            "sources": 0,
            "activeSources": 0,
            "errorSources": 0,
            "items": 0,
            "lastSuccessAt": "",
            "matches": 0,
            "matcherRan": False,
            "lastMatchAt": "",
        }
        config = discover_runtime.load_subscription_config() or {}
        douban = config.get("douban") if isinstance(config, dict) else {}
        douban = douban if isinstance(douban, dict) else {}
        write_enabled = _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))
        source_scheduler_enabled = bool(douban.get("enabled") and douban.get("task_enabled"))
        global_scheduler_configured = "MCC_SUBSCRIPTION_SCHEDULER_ENABLED" in self.environment
        global_scheduler_enabled = _truthy(self.environment.get("MCC_SUBSCRIPTION_SCHEDULER_ENABLED"))
        scheduler_registry = self.app.extensions.get("mcc_scheduler_status")
        scheduler_runtime = scheduler_registry.snapshot("subscription-task") if scheduler_registry else {}
        scheduler_started = bool(scheduler_runtime.get("started"))
        scheduler_error = str(scheduler_runtime.get("lastError") or "")
        scheduler_enabled = source_scheduler_enabled and (
            global_scheduler_enabled and scheduler_started
            if global_scheduler_configured
            else (scheduler_started if scheduler_registry else True)
        )
        if not source_scheduler_enabled:
            scheduler_state = "disabled"
            scheduler_detail = "自动订阅来源或定时任务未开启"
        elif global_scheduler_configured and not global_scheduler_enabled:
            scheduler_state = "disabled"
            scheduler_detail = "系统定时任务总开关已关闭"
        elif scheduler_error:
            scheduler_state = "error"
            scheduler_detail = "后台定时任务最近执行失败"
        elif scheduler_registry and not scheduler_started:
            scheduler_state = "unknown"
            scheduler_detail = "定时任务已开启，后台尚未确认运行"
        else:
            scheduler_state = "ready"
            scheduler_detail = f"每日 {douban.get('task_time') or '08:30'} 自动更新来源"
        if not rss_summary.get("enabled"):
            rss_state = "disabled"
            rss_detail = "RSS 采集未开启"
        elif rss_summary.get("errorSources"):
            rss_state = "error"
            rss_detail = f"{rss_summary.get('errorSources')} 个来源最近采集失败"
        elif not rss_summary.get("matcherRan"):
            rss_state = "unknown"
            rss_detail = f"已采集 {rss_summary.get('items', 0)} 条，匹配器尚未运行"
        elif not rss_summary.get("matches"):
            rss_state = "ready"
            rss_detail = f"采集正常，匹配器已运行，当前暂无命中（{rss_summary.get('items', 0)} 条种子）"
        else:
            rss_state = "ready"
            rss_detail = f"{rss_summary.get('activeSources', 0)} 个来源，已命中 {rss_summary.get('matches', 0)} 条"
        capabilities = [
            _state("local_write", "本地写入", "ready" if write_enabled else "disabled", "可保存和管理 Fluxa 本地订阅" if write_enabled else "本地订阅写入已关闭", enabled=write_enabled, configured=True, checked_at=checked_at),
            _state("torra_connection", "Torra 连接", "ready" if torra_connected else ("error" if torra_error else "disabled"), "连接正常" if torra_connected else (torra_error or ("Torra 未配置" if not torra_configured else "暂未建立连接")), enabled=torra_connected, configured=torra_configured, checked_at=checked_at),
            _state("torra_mirror", "镜像同步", "ready" if torra_status.get("enabled") and not torra_status.get("errors") else ("error" if torra_status.get("errors") else "disabled"), f"已关联 {torra_status.get('linked', 0)} 条，最近同步 {torra_status.get('lastSyncedAt') or '尚未同步'}" if torra_status.get("enabled") else "Torra 订阅镜像未开启", enabled=bool(torra_status.get("enabled")), configured=torra_configured, checked_at=checked_at),
            _state("rss", "RSS", rss_state, rss_detail, enabled=bool(rss_summary.get("enabled")), configured=bool(rss_summary.get("sources")), checked_at=checked_at),
            _state("scheduler", "定时任务", scheduler_state, scheduler_detail, enabled=scheduler_enabled, configured=bool(douban), checked_at=checked_at),
        ]
        return {
            "ok": True,
            "lastReadAt": checked_at,
            "capabilities": capabilities,
            "stats": stats,
            "items": paged_items,
            "page": {
                "total": page_total,
                "limit": page_limit,
                "offset": page_offset,
                "nextOffset": next_offset if next_offset < page_total else None,
                "hasMore": next_offset < page_total,
            },
            "blockedTitles": discover_runtime.subscription_blocked_titles(),
            "errors": list(raw.get("errors") or []) + ([chain_error] if chain_error else []) + ([reconciliation_error] if reconciliation_error else []),
            "torraSync": torra_status,
            "rss": rss_summary,
            "scheduler": {
                "enabled": scheduler_enabled,
                "state": scheduler_state,
                "taskTime": str(douban.get("task_time") or "08:30"),
                "lastRunAt": str(scheduler_runtime.get("lastRunAt") or douban.get("last_run_at") or ""),
                "lastError": scheduler_error,
            },
            "reconciliation": reconciliation or {
                "ok": False,
                "sourceError": reconciliation_error,
                "summary": {},
                "items": [],
            },
        }


def register_subscription_workbench(app: Flask, environment=None):
    service = SubscriptionWorkbenchService(app, environment=environment)
    app.extensions["mcc_subscription_workbench"] = service

    @app.get("/api/v2/subscriptions/workbench")
    def subscription_workbench():
        try:
            limit_value = int(request.args.get("limit", "24"))
            offset_value = int(request.args.get("offset", "0"))
        except ValueError:
            return jsonify({"code": "SUBSCRIPTION_PAGE_INVALID", "error": "分页参数无效", "request_id": current_request_id()}), 400
        if not 1 <= limit_value <= 100 or offset_value < 0:
            return jsonify({"code": "SUBSCRIPTION_PAGE_INVALID", "error": "分页参数无效", "request_id": current_request_id()}), 400
        media_type = str(request.args.get("mediaType") or "").strip().lower()
        if media_type and media_type not in {"movie", "tv"}:
            return jsonify({"code": "SUBSCRIPTION_MEDIA_TYPE_INVALID", "error": "媒体类型无效", "request_id": current_request_id()}), 400
        try:
            return jsonify(service.snapshot(
                limit=limit_value,
                offset=offset_value,
                media_type=media_type,
                query=str(request.args.get("query") or ""),
            ))
        except Exception:
            return jsonify({"code": "SUBSCRIPTION_WORKBENCH_READ_FAILED", "error": "订阅工作台读取失败", "request_id": current_request_id()}), 502

    return service
