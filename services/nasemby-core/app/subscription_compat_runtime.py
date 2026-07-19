from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from app import discover_runtime
from app.activity_log import read_activities, write_activity
from app.contract_mapping import (
    first_text,
    integer,
    map_calendar_payload,
    map_subscription_detail,
    map_subscription_item,
    map_subscription_payload,
    number_array,
    record,
    string_array,
)
from app.services import project_status
from app.quality_watch_repository import QualityWatchRepository
from app.subscription_torra_action_runtime import TorraSubscriptionActionService


MEDIA_CATEGORIES = {
    "anime_jp": {"key": "anime_jp", "label": "日漫", "directory": "00-日漫", "isAnime": True},
    "anime_cn": {"key": "anime_cn", "label": "国漫", "directory": "01-国漫", "isAnime": True},
    "tv_cn": {"key": "tv_cn", "label": "国产剧", "directory": "02-国产剧", "isAnime": False},
    "tv_asia": {"key": "tv_asia", "label": "日韩剧", "directory": "03-日韩剧", "isAnime": False},
    "tv_western": {"key": "tv_western", "label": "欧美剧", "directory": "04-欧美剧", "isAnime": False},
    "tv_hk_tw": {"key": "tv_hk_tw", "label": "港台剧", "directory": "05-港台剧", "isAnime": False},
    "variety": {"key": "variety", "label": "综艺", "directory": "06-综艺", "isAnime": False},
    "movie": {"key": "movie", "label": "电影", "directory": "10-电影", "isAnime": False},
}
MAINLAND_CODES = {"CN"}
HK_TW_CODES = {"HK", "TW"}
JP_KR_CODES = {"JP", "KR"}
SOUTH_ASIA_CODES = {"IN", "PK", "BD", "LK", "NP", "BT", "MV"}
WESTERN_CODES = {
    "US", "GB", "CA", "AU", "NZ", "IE", "FR", "DE", "ES", "IT", "NL", "BE",
    "SE", "NO", "DK", "FI", "PL", "PT",
}
WESTERN_LANGUAGES = {"en", "fr", "de", "es", "it", "nl", "pt"}
SOUTH_ASIA_LANGUAGES = {"hi", "bn", "ta", "te", "ur", "si", "ne"}


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _error(code, message, status):
    return jsonify({"ok": False, "success": False, "code": code, "error": message}), status


def _write_guard(environment):
    if _truthy(environment.get("NASEMBY_CORE_WRITE_ENABLED")):
        return None
    return _error("NASEMBY_CORE_WRITE_DISABLED", "订阅写入尚未启用", 403)


def _raw_subscription_payload(include_progress=False):
    data = discover_runtime.load_subscription_items(
        with_progress=include_progress,
        remove_completed=False,
        persist_progress=False,
    )
    items = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        kind = discover_runtime.discover_item_media_type(row) or str(row.get("media_type") or "")
        row["key"] = discover_runtime.get_subscription_item_key(row)
        row["media_type"] = kind
        row["tmdb_id"] = discover_runtime.discover_item_tmdb_id(row, kind)
        items.append(row)
    return {
        "success": True,
        "items": items,
        "blocked_titles": discover_runtime.subscription_blocked_titles(),
        "last_run_at": data.get("last_run_at") or "",
        "stats": data.get("stats") or {},
        "errors": data.get("errors") or [],
    }


def _find_item(key):
    target = str(key or "").strip()
    if not target:
        return None
    return next((item for item in _raw_subscription_payload()["items"] if item.get("key") == target), None)


def _core_item(body):
    return {
        "title": body.get("title"),
        "media_type": body.get("mediaType"),
        "tmdb_id": str(body.get("tmdbId") or ""),
        "poster_url": body.get("posterUrl") or "",
        "year": str(body.get("year") or ""),
        "target_season": body.get("seasonNumber"),
        "season_name": body.get("seasonName") or "",
        "original_language": body.get("originalLanguage") or "",
        "genre_ids": body.get("genreIds") if isinstance(body.get("genreIds"), list) else [],
        "origin_country": body.get("originCountry") if isinstance(body.get("originCountry"), list) else [],
        "source": "manual",
        "source_label": "手动订阅",
        "origin": "manual",
        "allow_cloud_fallback": bool(body.get("allowCloudFallback", False)),
    }


def _source_catalog():
    result = []
    for key, source in discover_runtime.SUBSCRIPTION_SOURCES.items():
        result.append({
            "key": key,
            "label": str(source.get("label") or key),
            "mediaType": "movie" if source.get("media_type") == "movie" else "tv",
        })
    return result


def _resolve_category(item):
    selected = str(item.get("media_category") or "")
    if selected in MEDIA_CATEGORIES:
        return MEDIA_CATEGORIES[selected], "人工指定分类"
    kind = discover_runtime.discover_item_media_type(item)
    if kind == "movie":
        return MEDIA_CATEGORIES["movie"], "电影统一进入 10-电影"
    countries = {value.upper() for value in string_array(item.get("origin_country"))}
    genres = set(number_array(item.get("genre_ids")))
    language = str(item.get("original_language") or "").lower()
    animation = 16 in genres
    variety = bool({10764, 10767} & genres)
    if animation:
        if countries & (MAINLAND_CODES | HK_TW_CODES) or language.startswith("zh"):
            return MEDIA_CATEGORIES["anime_cn"], "中文或中国大陆/港台动画"
        if "JP" in countries or language == "ja":
            return MEDIA_CATEGORIES["anime_jp"], "日本动画"
        if countries & (JP_KR_CODES | SOUTH_ASIA_CODES) or language == "ko":
            return MEDIA_CATEGORIES["tv_asia"], "韩国或南亚动画并入 03-日韩剧"
        if countries & WESTERN_CODES or language in WESTERN_LANGUAGES:
            return MEDIA_CATEGORIES["tv_western"], "欧美动画并入 04-欧美剧"
        return None, "缺少可确认动画地区的证据"
    if variety:
        return MEDIA_CATEGORIES["variety"], "TMDB Reality/Talk 类型"
    if countries & MAINLAND_CODES:
        return MEDIA_CATEGORIES["tv_cn"], "中国大陆剧集"
    if countries & HK_TW_CODES:
        return MEDIA_CATEGORIES["tv_hk_tw"], "香港或台湾剧集"
    if countries & (JP_KR_CODES | SOUTH_ASIA_CODES):
        return MEDIA_CATEGORIES["tv_asia"], "日韩或南亚剧集"
    if countries & WESTERN_CODES:
        return MEDIA_CATEGORIES["tv_western"], "欧美剧集"
    if language in {"ja", "ko"} | SOUTH_ASIA_LANGUAGES:
        return MEDIA_CATEGORIES["tv_asia"], "依据原始语言归入日韩/南亚剧集"
    if language in WESTERN_LANGUAGES:
        return MEDIA_CATEGORIES["tv_western"], "依据原始语言归入欧美剧集"
    return None, "缺少可确认地区或语言的证据"


def _torra_payload(item, category, environment):
    kind = discover_runtime.discover_item_media_type(item) or "movie"
    tmdb_id = str(discover_runtime.discover_item_tmdb_id(item, kind) or "")
    season = discover_runtime.subscription_target_season(item) or (1 if kind == "tv" else 0)
    root = str(environment.get("TORRA_DOWNLOAD_ROOT") or "/vol02/1000-4-32d3f6a0/torra").rstrip("/\\")
    save_path = f"{root}/{category['directory']}" if root else ""
    year = str(item.get("year") or "")
    title = str(item.get("title") or item.get("name") or "")
    total = next((integer(item.get(key)) for key in (
        "episode_total", "total_episodes", "total_episode_count", "episode_count"
    ) if integer(item.get(key)) > 0), 0)
    payload = {
        "id": f"mcc_{kind}_{tmdb_id}_{season if kind == 'tv' else 0}",
        "name": title,
        "keyword": title,
        "main_title_pattern": "",
        "media_type": kind,
        "is_anime": category["isAnime"],
        "tmdb_id": integer(tmdb_id),
        "names": [title],
        "year": year,
        "poster_path": str(item.get("poster_url") or item.get("poster") or ""),
        "backdrop_path": str(item.get("backdrop_url") or ""),
        "season_years": {str(season): year} if kind == "tv" and year else {},
        "season_number": season if kind == "tv" else 0,
        "episode_group": "",
        "start_episode": 1,
        "end_episode": 0,
        "total_episode_count": total,
        "available_episode_numbers": [],
        "downloaded_episode_numbers": [],
        "downloaded_episode_files": {},
        "downloaded_file_names": [],
        "library_episode_files": {},
        "library_file_names": [],
        "site_ids": [],
        "downloader_id": str(environment.get("TORRA_DOWNLOADER_ID") or ""),
        "save_path": save_path,
        "version_control_enabled": False,
        "version_control_entries": [],
        "version_control_mode": "include",
        "enabled": True,
        "completed": False,
        "auto_rewash_status": "",
        "auto_rewash_started_at": "",
    }
    return payload, save_path


def _push_preview(item, environment, torra_client):
    blockers = []
    warnings = ["Torra 版本控制模板尚待现网核对，dry-run 暂不启用版本控制"]
    category, reason = _resolve_category(item)
    kind = discover_runtime.discover_item_media_type(item) or "movie"
    tmdb_id = str(discover_runtime.discover_item_tmdb_id(item, kind) or "")
    if not tmdb_id:
        blockers.append("缺少 TMDB ID，无法建立稳定媒体身份")
    if not category:
        blockers.append("无法可靠判断媒体分类，需要人工选择后才能推送")
    if not torra_client.is_configured():
        blockers.append("Torra 尚未配置")
    if not str(environment.get("TORRA_DOWNLOADER_ID") or ""):
        blockers.append("TORRA_DOWNLOADER_ID 尚未核对")
    if not _truthy(environment.get("TORRA_PUSH_ENABLED")):
        blockers.append("TORRA_PUSH_ENABLED 当前关闭")
    payload = None
    save_path = ""
    duplicate = None
    if category:
        payload, save_path = _torra_payload(item, category, environment)
        target = {
            "title": payload["name"],
            "mediaType": payload["media_type"],
            "tmdbId": str(payload["tmdb_id"]),
            "seasonNumber": payload["season_number"],
            "year": payload["year"],
        }
        duplicate = torra_client.inspect_duplicate(target)
        if duplicate.get("error") and torra_client.is_configured():
            blockers.append(f"Torra 在线查重未完成：{duplicate['error']}")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "category": category,
        "categoryReason": reason,
        "savePath": save_path,
        "payload": payload,
        "duplicate": duplicate,
    }


def _update_item(key, updater):
    matched = discover_runtime.update_subscription_item(key, updater)
    if not matched:
        return None
    mapped = dict(matched)
    mapped["key"] = discover_runtime.get_subscription_item_key(mapped)
    return mapped


def register_subscription_compat(app: Flask, environment=None, action_repository=None):
    environment = os.environ if environment is None else environment
    action_repository = action_repository or QualityWatchRepository(discover_runtime.subscription_database_path())
    app.extensions["mcc_quality_watch_repository"] = action_repository
    torra_action_service = TorraSubscriptionActionService(
        environment,
        action_repository,
        app.extensions["mcc_torra_client"],
        _find_item,
        lambda item: _push_preview(item, environment, app.extensions["mcc_torra_client"]),
    )
    app.extensions["mcc_torra_subscription_action_service"] = torra_action_service

    @app.get("/api/subscriptions/items", endpoint="mcc_compat_subscriptions_items")
    def subscriptions_items():
        try:
            include_progress = request.args.get("include_progress", request.args.get("progress", "0")) == "1"
            return jsonify(map_subscription_payload(_raw_subscription_payload(include_progress)))
        except Exception:
            return _error("NASEMBY_SUBSCRIPTIONS_UNAVAILABLE", "订阅列表暂不可用", 502)

    @app.post("/api/subscriptions/save", endpoint="mcc_compat_subscriptions_save")
    def subscriptions_save():
        denied = _write_guard(environment)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        if not body.get("title") or not body.get("tmdbId") or body.get("mediaType") not in {"movie", "tv"}:
            return _error("SUBSCRIPTION_INVALID", "需要 title、tmdbId 和 mediaType（movie/tv）", 400)
        try:
            data = discover_runtime.save_subscription_item({"item": _core_item(body)})
            return jsonify({
                "success": True,
                "item": map_subscription_item(data.get("saved_item")),
                "message": data.get("message") or "保存订阅成功",
            })
        except Exception:
            return _error("NASEMBY_SUBSCRIPTION_SAVE_FAILED", "订阅保存失败", 502)

    @app.get("/api/subscriptions/push-preview", endpoint="mcc_compat_subscriptions_push_preview")
    def subscriptions_push_preview():
        key = str(request.args.get("id") or "").strip()
        if not key:
            return _error("SUBSCRIPTION_ID_REQUIRED", "需要订阅 id", 400)
        item = _find_item(key)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        return jsonify({"success": True, "preview": _push_preview(item, environment, app.extensions["mcc_torra_client"])})

    @app.get(
        "/api/v2/subscriptions/<key>/torra-push-preview",
        endpoint="mcc_v2_subscriptions_torra_push_preview",
    )
    def subscriptions_torra_push_preview(key):
        item = _find_item(key)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        plan = _push_preview(item, environment, app.extensions["mcc_torra_client"])
        return jsonify({
            "ok": True,
            "subscription": {
                "id": key,
                "title": str(item.get("title") or item.get("name") or "")[:240],
            },
            "preview": plan,
        })

    @app.patch("/api/subscriptions/<key>/category", endpoint="mcc_compat_subscriptions_category")
    def subscriptions_category(key):
        denied = _write_guard(environment)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        category = body.get("category")
        if category is not None and category not in MEDIA_CATEGORIES:
            return _error("SUBSCRIPTION_CATEGORY_INVALID", "需要有效的八分类 key，或传 null 清除人工覆盖", 400)

        def update(item):
            if category is None:
                item.pop("media_category", None)
            else:
                item["media_category"] = category

        item = _update_item(key, update)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        return jsonify({"success": True, "item": map_subscription_item(item)})

    @app.patch("/api/v2/subscriptions/<key>/cloud-policy", endpoint="mcc_v2_subscriptions_cloud_policy")
    def subscriptions_cloud_policy(key):
        denied = _write_guard(environment)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("allowCloudFallback"), bool):
            return _error("CLOUD_POLICY_INVALID", "allowCloudFallback 必须是布尔值", 400)

        def update(item):
            item["allow_cloud_fallback"] = body["allowCloudFallback"]

        item = _update_item(key, update)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        return jsonify({"success": True, "item": map_subscription_item(item)})

    @app.post("/api/subscriptions/push", endpoint="mcc_compat_subscriptions_push")
    def subscriptions_push():
        denied = _write_guard(environment)
        if denied:
            return denied
        if not _truthy(environment.get("TORRA_PUSH_ENABLED")):
            return _error("TORRA_PUSH_DISABLED", "Torra 安全推送开关未启用，请先使用 push-preview 核对载荷", 400)
        key = str((request.get_json(silent=True) or {}).get("id") or "").strip()
        item = _find_item(key)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        plan = _push_preview(item, environment, app.extensions["mcc_torra_client"])
        if not plan["ready"]:
            return jsonify({"success": False, "error": "；".join(plan["blockers"]), "preview": plan}), 409
        try:
            return jsonify(app.extensions["mcc_torra_client"].push_subscription(plan["payload"]))
        except Exception:
            return _error("TORRA_PUSH_FAILED", "Torra 推送失败", 502)

    @app.post(
        "/api/v2/subscriptions/<key>/torra-pushes",
        endpoint="mcc_v2_subscriptions_torra_push",
    )
    def subscriptions_torra_push(key):
        denied = _write_guard(environment)
        if denied:
            return denied
        response, http_status = torra_action_service.execute(key, request.get_json(silent=True) or {})
        if response.get("requestId") and not response.get("replayed"):
            write_activity(
                "push",
                "torra_push_v2",
                "success" if response["success"] else "error",
                response["message"],
                subscription_id=key,
                request_id=response["requestId"],
                already_exists=response["alreadyExists"],
            )
        return jsonify(response), http_status

    @app.get("/api/subscriptions/config", endpoint="mcc_compat_subscriptions_config")
    def subscriptions_config():
        try:
            return jsonify({"success": True, "config": discover_runtime.load_subscription_config(), "sources": _source_catalog()})
        except Exception:
            return _error("NASEMBY_CONFIG_UNAVAILABLE", "订阅配置暂不可用", 502)

    @app.post("/api/subscriptions/config", endpoint="mcc_compat_subscriptions_save_config")
    def subscriptions_save_config():
        denied = _write_guard(environment)
        if denied:
            return denied
        body = dict(request.get_json(silent=True) or {})
        body["mode_switch_push"] = False
        try:
            config = discover_runtime.save_subscription_config(body)
            config.pop("mode_switch_task", None)
            return jsonify({"success": True, "config": config, "sources": _source_catalog()})
        except Exception:
            return _error("NASEMBY_CONFIG_SAVE_FAILED", "订阅配置保存失败", 502)

    @app.post("/api/subscriptions/run", endpoint="mcc_compat_subscriptions_run")
    def subscriptions_run():
        denied = _write_guard(environment)
        if denied:
            return denied
        try:
            return jsonify(discover_runtime.run_subscription_now())
        except Exception:
            return _error("NASEMBY_SUBSCRIPTION_RUN_FAILED", "订阅任务执行失败", 502)

    @app.post("/api/subscriptions/block", endpoint="mcc_compat_subscriptions_block")
    def subscriptions_block():
        denied = _write_guard(environment)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        key = str(body.get("id") or "").strip()
        item = _find_item(key) if key else None
        title = str(body.get("title") or (item or {}).get("title") or "").strip()
        if not title:
            return _error("SUBSCRIPTION_TARGET_REQUIRED", "缺少屏蔽目标", 400)
        try:
            data = discover_runtime.block_subscription_item({"key": key, "item": item, "title": title})
            return jsonify({"success": True, "blocked_titles": data.get("blocked_titles") or [], "removed_count": data.get("removed_count") or 0})
        except Exception:
            return _error("NASEMBY_SUBSCRIPTION_BLOCK_FAILED", "订阅屏蔽失败", 502)

    @app.post("/api/subscriptions/unblock", endpoint="mcc_compat_subscriptions_unblock")
    def subscriptions_unblock():
        denied = _write_guard(environment)
        if denied:
            return denied
        title = str((request.get_json(silent=True) or {}).get("title") or "").strip()
        if not title:
            return _error("SUBSCRIPTION_TARGET_REQUIRED", "缺少解除目标", 400)
        try:
            data = discover_runtime.unblock_subscription_title({"title": title})
            return jsonify({"success": True, "blocked_titles": data.get("blocked_titles") or []})
        except Exception:
            return _error("NASEMBY_SUBSCRIPTION_UNBLOCK_FAILED", "订阅解除屏蔽失败", 502)

    @app.post("/api/subscriptions/clear", endpoint="mcc_compat_subscriptions_clear")
    def subscriptions_clear():
        denied = _write_guard(environment)
        if denied:
            return denied
        try:
            discover_runtime.clear_subscription_items()
            return jsonify({"success": True})
        except Exception:
            return _error("NASEMBY_SUBSCRIPTIONS_CLEAR_FAILED", "订阅清空失败", 502)

    @app.get("/api/subscriptions/detail", endpoint="mcc_compat_subscriptions_detail")
    def subscriptions_detail():
        key = str(request.args.get("id") or "").strip()
        if not key:
            return _error("SUBSCRIPTION_ID_REQUIRED", "需要订阅 id", 400)
        try:
            return jsonify(map_subscription_detail(discover_runtime.fetch_subscription_detail({"key": key})))
        except Exception:
            return _error("NASEMBY_SUBSCRIPTION_DETAIL_UNAVAILABLE", "订阅详情暂不可用", 502)

    @app.post("/api/subscriptions/season", endpoint="mcc_compat_subscriptions_season")
    def subscriptions_season():
        denied = _write_guard(environment)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        key = str(body.get("id") or "").strip()
        season = integer(body.get("seasonNumber"))
        if not key or season < 1:
            return _error("SUBSCRIPTION_SEASON_INVALID", "需要 id 和有效的 seasonNumber", 400)

        def update(item):
            item.update({
                "target_season": season,
                "current_season": season,
                "latest_season": season,
                "season_number": season,
                "season_name": str(body.get("seasonName") or f"第 {season} 季"),
            })

        item = _update_item(key, update)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        return jsonify({"success": True, "item": map_subscription_item(item)})

    @app.post("/api/subscriptions/delete", endpoint="mcc_compat_subscriptions_delete")
    def subscriptions_delete():
        denied = _write_guard(environment)
        if denied:
            return denied
        key = str((request.get_json(silent=True) or {}).get("id") or "").strip()
        if not key:
            return _error("SUBSCRIPTION_ID_REQUIRED", "需要 id", 400)
        item = _find_item(key)
        if not item:
            return _error("SUBSCRIPTION_NOT_FOUND", "订阅不存在", 404)
        try:
            data = discover_runtime.delete_subscription_item({"key": key, "item": item})
            return jsonify({"success": bool(data.get("removed_count")), "removed_count": data.get("removed_count") or 0})
        except Exception:
            return _error("NASEMBY_SUBSCRIPTION_DELETE_FAILED", "订阅删除失败", 502)

    @app.get("/api/subscriptions/calendar", endpoint="mcc_compat_subscriptions_calendar")
    def subscriptions_calendar():
        try:
            payload = discover_runtime.build_subscription_calendar(
                request.args.get("year"), request.args.get("month"), request.args.get("type") or "all"
            )
            return jsonify(map_calendar_payload(payload))
        except Exception:
            return _error("NASEMBY_CALENDAR_UNAVAILABLE", "订阅日历暂不可用", 502)

    @app.post("/api/subscriptions/import-nasemby", endpoint="mcc_compat_subscriptions_import")
    def subscriptions_import():
        return _error("NASEMBY_IMPORT_DISABLED", "统一后端不导入外部 NasEmby 台账", 404)

    @app.get("/api/activity/logs", endpoint="mcc_compat_activity_logs")
    def activity_logs():
        return jsonify({
            "ok": True,
            "logs": read_activities(limit=request.args.get("limit", "200"), category=str(request.args.get("category") or "").strip()),
        })

    @app.get("/api/internal/nasemby-core/status", endpoint="mcc_compat_internal_status")
    def internal_status():
        status = record(project_status())
        return jsonify({
            "configured": True,
            "connected": bool(status.get("ok")),
            "writeEnabled": _truthy(environment.get("NASEMBY_CORE_WRITE_ENABLED")),
            "features": status.get("features") if isinstance(status.get("features"), list) else [],
            "checkedAt": _iso_now(),
        })

    @app.get("/api/internal/nasemby-core/subscriptions", endpoint="mcc_compat_internal_subscriptions")
    def internal_subscriptions():
        return jsonify(_raw_subscription_payload(request.args.get("include_progress") == "1"))

    @app.get("/api/internal/nasemby-core/subscriptions/detail", endpoint="mcc_compat_internal_subscription_detail")
    def internal_subscription_detail():
        query = {key: value for key, value in request.args.items() if key in {"key", "id", "season"} and len(value) <= 200}
        if not query.get("key") and query.get("id"):
            query["key"] = query.pop("id")
        try:
            return jsonify(discover_runtime.fetch_subscription_detail(query))
        except Exception:
            return _error("NASEMBY_CORE_UNAVAILABLE", "NasEmby Core 订阅详情暂不可用", 502)

    @app.get("/api/internal/nasemby-core/calendar", endpoint="mcc_compat_internal_calendar")
    def internal_calendar():
        try:
            return jsonify(discover_runtime.build_subscription_calendar(
                request.args.get("year"), request.args.get("month"), request.args.get("type") or "all"
            ))
        except Exception:
            return _error("NASEMBY_CORE_UNAVAILABLE", "NasEmby Core 日历暂不可用", 502)

    return app
