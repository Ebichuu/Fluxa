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

    def capability_snapshot(self):
        checked_at = _now()
        registry = self.app.extensions.get("mcc_scheduler_status")
        scheduler = registry.snapshot("subscription-task") if registry else {}
        scheduler_configured = "MCC_SUBSCRIPTION_SCHEDULER_ENABLED" in self.environment
        scheduler_enabled = _truthy(self.environment.get("MCC_SUBSCRIPTION_SCHEDULER_ENABLED"))
        scheduler_started = bool(scheduler.get("started"))
        scheduler_error = str(scheduler.get("lastError") or "")
        return {
            "ok": True,
            "checkedAt": checked_at,
            "localWrite": {
                "enabled": _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")),
            },
            "torraPush": {
                "enabled": _truthy(self.environment.get("TORRA_PUSH_ENABLED")),
            },
            "scheduler": {
                "configured": scheduler_configured,
                "enabled": scheduler_enabled,
                "started": scheduler_started,
                "running": bool(scheduler_enabled and scheduler_started and not scheduler_error),
                "lastRunAt": str(scheduler.get("lastRunAt") or ""),
                "lastError": scheduler_error,
            },
        }

    @staticmethod
    def _requested_visual_ids(item_ids):
        requested = []
        for value in item_ids or []:
            key = str(value or "").strip()
            if key and key not in requested:
                requested.append(key)
        return requested[:100]

    def _visual_rows_by_key(self):
        write_enabled = _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))
        raw = discover_runtime.load_subscription_items(
            with_progress=False,
            remove_completed=False,
            persist_progress=False,
        )
        rows = [
            {**row, "_visual_read_only": not write_enabled}
            for row in (raw.get("items") or [])
            if isinstance(row, dict)
        ]
        by_key = {
            str(discover_runtime.get_subscription_item_key(row) or ""): row
            for row in rows
            if discover_runtime.get_subscription_item_key(row)
        }
        reconciliation_service = self.app.extensions.get("mcc_subscription_reconciliation")
        if reconciliation_service:
            try:
                reconciliation = reconciliation_service.snapshot() or {}
            except Exception:
                reconciliation = {}
            for row in reconciliation.get("items") or []:
                if not isinstance(row, dict) or row.get("reconciliationState") != "only_torra":
                    continue
                key = str(row.get("id") or "").strip()
                if not key:
                    continue
                by_key[key] = {
                    "title": row.get("title") or "",
                    "media_type": row.get("mediaType") or "unknown",
                    "tmdb_id": row.get("tmdbId") or "",
                    "_visual_read_only": True,
                }
        return by_key

    @staticmethod
    def _visual_response_item(key, visuals, mapped=None):
        mapped = mapped or {}
        return {
            "id": mapped.get("id") or key,
            "posterUrl": mapped.get("posterUrl") or visuals.get("poster_url") or "",
            "backdropUrl": mapped.get("backdropUrl") or visuals.get("backdrop_url") or "",
        }

    def _backfill_visual(self, key, row):
        visuals = discover_runtime.resolve_subscription_visuals(row, fetch=True)
        if not visuals.get("poster_url"):
            return "unchanged", None
        if row.get("_visual_read_only"):
            return "updated", self._visual_response_item(key, visuals)
        saved = discover_runtime.supplement_subscription_visuals(key, visuals)
        if not saved:
            return "unchanged", None
        return "updated", self._visual_response_item(key, visuals, map_subscription_item(saved) or {})

    def backfill_visuals(self, item_ids):
        requested = self._requested_visual_ids(item_ids)
        by_key = self._visual_rows_by_key()
        result = {"ok": True, "scanned": 0, "updated": 0, "unchanged": 0, "items": [], "errors": []}
        for key in requested:
            row = by_key.get(key)
            if not row:
                continue
            result["scanned"] += 1
            try:
                status, item = self._backfill_visual(key, row)
            except Exception:
                result["errors"].append(key)
                continue
            result[status] += 1
            if item:
                result["items"].append(item)
        return result

    def snapshot(self, *, limit=None, offset=0, media_type="", query=""):
        checked_at = _now()
        write_enabled = _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))
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
        for source_row in rows:
            row = dict(source_row)
            poster_missing = not str(row.get("poster_url") or row.get("poster") or "").strip()
            visuals = discover_runtime.resolve_subscription_visuals(row, fetch=False)
            if visuals:
                row.update(visuals)
            mapped = _item_snapshot(row, _chain_item_for_row(row, chain))
            local_key = str(mapped.get("id") or discover_runtime.get_subscription_item_key(row) or "")
            if (
                poster_missing
                and local_key
                and str(mapped.get("tmdbId") or "").isdigit()
            ):
                mapped["_posterBackfillId"] = local_key
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
            fulfillment_state = str(recon.get("fulfillmentState") or "following")
            remote_completed = fulfillment_state == "completed"
            remote_visuals = discover_runtime.resolve_subscription_visuals({
                "title": recon.get("title") or "",
                "media_type": recon.get("mediaType") or "unknown",
                "tmdb_id": recon.get("tmdbId") or "",
            }, fetch=False)
            remote_item = {
                "id": recon.get("id"),
                "title": recon.get("title") or "未命名订阅",
                "seasonName": f"第 {recon.get('seasonNumber', 0)} 季" if recon.get("mediaType") == "tv" else "",
                "seasonNumber": recon.get("seasonNumber"),
                "mediaType": recon.get("mediaType") or "unknown",
                "tmdbId": recon.get("tmdbId") or "",
                "posterUrl": remote_visuals.get("poster_url") or "",
                "progressText": "Torra 订阅已完成" if remote_completed else "Torra 正在追更",
                "inLibrary": False,
                "updatedAt": recon.get("observedAt") or checked_at,
                "createdAt": recon.get("observedAt") or checked_at,
                "sourceLabel": "Torra 已有订阅",
                "status": "done" if remote_completed else "pending",
                "origin": "torra",
                "readOnly": True,
                "chainState": "completed" if remote_completed else "waiting",
                "torraSyncState": "current",
                "torraMappingStatus": "mapped",
                "reconciliationState": recon.get("reconciliationState"),
                "fulfillmentState": recon.get("fulfillmentState"),
                "healthState": recon.get("healthState"),
                "reasonCode": recon.get("reasonCode"),
                "reasonText": recon.get("reasonText"),
                "observedAt": recon.get("observedAt"),
                "freshUntil": recon.get("freshUntil"),
            }
            if (
                not remote_item["posterUrl"]
                and str(remote_item.get("tmdbId") or "").isdigit()
            ):
                remote_item["_posterBackfillId"] = str(remote_item.get("id") or "")
            mapped_items.append(remote_item)
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
        poster_backfill_ids = [
            str(item.pop("_posterBackfillId"))
            for item in paged_items
            if item.get("_posterBackfillId")
        ]
        next_offset = page_offset + len(paged_items)

        torra_sync = self.app.extensions.get("mcc_torra_subscription_sync")
        torra_status = torra_sync.status() if torra_sync else {"enabled": False, "linked": 0, "current": 0, "remoteMissing": 0, "errors": 0, "lastSyncedAt": ""}
        torra_client = self.app.extensions.get("mcc_torra_client")
        torra_configured = bool(torra_client and torra_client.is_configured())
        task_services = (chain_payload.get("services") if 'chain_payload' in locals() and isinstance(chain_payload, dict) else {}) or {}
        torra_service = task_services.get("torra") or {}
        torra_connected = bool(torra_service.get("connected")) if task_service else torra_configured
        torra_error = str(torra_service.get("error") or chain_error or "")
        reconciliation_counts = ((reconciliation or {}).get("summary") or {}).get("reconciliation") or {}
        try:
            reconciliation_linked = int(reconciliation_counts.get("linked") or 0)
        except (TypeError, ValueError):
            reconciliation_linked = 0
        reconciliation_readable = bool(reconciliation and not reconciliation.get("sourceError"))
        if reconciliation_readable:
            mirror_state = "ready" if torra_status.get("enabled") and not torra_status.get("errors") else ("error" if torra_status.get("errors") else "disabled")
            mirror_detail = (
                f"当前对账已关联 {reconciliation_linked} 条；历史镜像链接 {torra_status.get('linked', 0)} 条"
                + ("；镜像同步未开启" if not torra_status.get("enabled") else "")
            )
        elif reconciliation and reconciliation.get("sourceError"):
            mirror_state = "error"
            mirror_detail = "对账暂不可用；历史镜像链接仅供参考"
        elif torra_status.get("enabled"):
            mirror_state = "error" if torra_status.get("errors") else "ready"
            mirror_detail = f"历史镜像链接 {torra_status.get('linked', 0)} 条，最近同步 {torra_status.get('lastSyncedAt') or '尚未同步'}"
        else:
            mirror_state = "disabled"
            mirror_detail = "Torra 订阅镜像未开启"
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
            _state("torra_mirror", "镜像同步", mirror_state, mirror_detail, enabled=bool(torra_status.get("enabled")), configured=torra_configured, checked_at=checked_at),
            _state("rss", "RSS", rss_state, rss_detail, enabled=bool(rss_summary.get("enabled")), configured=bool(rss_summary.get("sources")), checked_at=checked_at),
            _state("scheduler", "定时任务", scheduler_state, scheduler_detail, enabled=scheduler_enabled, configured=bool(douban), checked_at=checked_at),
        ]
        return {
            "ok": True,
            "lastReadAt": checked_at,
            "capabilities": capabilities,
            "stats": stats,
            "items": paged_items,
            "posterBackfillIds": poster_backfill_ids,
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

    @app.get("/api/v2/subscriptions/capabilities")
    def subscription_capabilities():
        return jsonify(service.capability_snapshot())

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

    @app.post("/api/v2/subscriptions/visual-backfills")
    def subscription_visual_backfills():
        item_ids = (request.get_json(silent=True) or {}).get("ids")
        if not isinstance(item_ids, list) or len(item_ids) > 100:
            return jsonify({
                "code": "SUBSCRIPTION_VISUAL_BACKFILL_INVALID",
                "error": "订阅海报补齐目标无效",
                "request_id": current_request_id(),
            }), 422
        try:
            return jsonify(service.backfill_visuals(item_ids))
        except Exception:
            return jsonify({
                "code": "SUBSCRIPTION_VISUAL_BACKFILL_FAILED",
                "error": "订阅海报补齐失败",
                "request_id": current_request_id(),
            }), 500

    return service
