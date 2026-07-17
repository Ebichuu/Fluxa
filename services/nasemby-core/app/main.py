from __future__ import annotations

import logging
import os
import threading
import time

from flask import Blueprint, Flask, Response, jsonify, redirect, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from app.activity_log import clear_activities, read_activities, write_activity
from app.config import DATA_DIR, read_config, write_config
from app import discover_runtime
from app.access_auth import AccessAuth, is_production_environment, resolve_access_config
from app.auth_runtime import configure_access_runtime
from app.frontend_runtime import register_frontend
from app.http_runtime import configure_http_runtime
from app.mineradio_runtime import register_mineradio
from app.discover_compat_runtime import register_discover_compat
from app.subscription_compat_runtime import register_subscription_compat
from app.media_read_runtime import register_emby_reads
from app.emby_refresh_runtime import register_emby_refresh
from app.qbittorrent_runtime import register_qbittorrent_read
from app.qbittorrent_action_runtime import register_qbittorrent_actions
from app.torra_read_runtime import register_torra_read
from app.symedia_read_runtime import register_symedia_read
from app.task_chain_runtime import register_task_chain
from app.integration_runtime import register_integrations
from app.cloud_acquisition_runtime import register_cloud_acquisition
from app.system_metrics_runtime import register_system_metrics
from app.hdhive_auth import (
    hdhive_auth_url,
    hdhive_checkin_now,
    hdhive_identity,
    hdhive_status,
    run_due_hdhive_checkin,
    update_hdhive_account,
    update_hdhive_config,
)
from app.telegram_runtime import (
    delete_channel as telegram_delete_channel,
    list_channels as telegram_list_channels,
    logout as telegram_logout,
    reorder_channels as telegram_reorder_channels,
    save_channels as telegram_save_channels,
    send_login_code as telegram_send_login_code,
    sign_in as telegram_sign_in,
    telegram_status,
)
from app.services import (
    check_115_account,
    dashboard_system_metrics,
    extract_115_links,
    fetch_emby_library_image,
    fetch_emby_libraries,
    moviepilot_status,
    moviepilot_subscribe,
    project_status,
    run_115_cleanup,
    run_115_invite_boost,
    run_115_monitor_once,
    search_yingchao_resources,
    symedia_status,
    symedia_subscribe,
    transfer_115_share,
    transfer_yingchao_item,
    torra_status,
    torra_subscribe,
)


logger = logging.getLogger(__name__)
core_routes = Blueprint("nasemby_core_routes", __name__)
_hdhive_scheduler_started = False
_discover_preload_started = False
_subscription_scheduler_started = False
_background_runtime_started = False


# 这些接口来自可运行的 NasEmby 源码，仍承载 115、Telegram、HDHive、
# provider 推送和维护动作。v2 保留其实现与契约，但在统一页面完成安全接入前，
# 默认只允许查看源码和执行模拟测试，不能从生产端口直接触发。
PRESERVED_CORE_API_PATHS = {
    "/api/config",
    "/api/dashboard/system",
    "/api/discover/cache/preload",
    "/api/emby/libraries",
    "/api/hdhive/authorize",
    "/api/hdhive/status",
    "/api/hdhive/identity",
    "/api/hdhive/config",
    "/api/hdhive/account",
    "/api/hdhive/checkin",
    "/api/moviepilot/status",
    "/api/moviepilot/subscribe",
    "/api/torra/status",
    "/api/torra/subscribe",
    "/api/symedia/status",
    "/api/symedia/subscribe",
    "/api/115/check",
    "/api/115/extract",
    "/api/115/transfer",
    "/api/115/monitor/run",
    "/api/115/cleanup/run",
    "/api/115/boost",
    "/api/yingchao/search",
    "/api/yingchao/transfer",
}
PRESERVED_CORE_API_PREFIXES = (
    "/api/activity/",
    "/api/emby/library-image/",
    "/api/telegram/",
)


def _environment_flag_enabled(name, default="false", environment=None):
    source = os.environ if environment is None else environment
    return str(source.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _is_preserved_core_api(path):
    return path in PRESERVED_CORE_API_PATHS or path.startswith(PRESERVED_CORE_API_PREFIXES)


SENSITIVE_LOG_KEYS = {"password", "passwd", "token", "api_key", "api_hash", "cookie", "cookies", "secret", "authorization"}
OPERATION_LABELS = {
    "/api/config": "保存配置",
    "/api/hdhive/config": "保存影巢配置",
    "/api/hdhive/account": "更新影巢账号",
    "/api/hdhive/checkin": "影巢签到",
    "/api/emby/libraries": "读取 Emby 媒体库",
    "/api/telegram/send-code": "发送 Telegram 验证码",
    "/api/telegram/sign-in": "Telegram 登录",
    "/api/telegram/logout": "Telegram 退出登录",
    "/api/telegram/channels": "保存 Telegram 频道",
    "/api/telegram/channels/reorder": "调整 Telegram 频道排序",
    "/api/subscriptions/config": "保存订阅配置",
    "/api/subscriptions/run": "执行订阅刷新",
    "/api/subscriptions/daily-airing/sync": "同步全球日播订阅",
    "/api/subscriptions/save": "保存订阅",
    "/api/subscriptions/delete": "删除订阅",
    "/api/subscriptions/clear": "清空订阅",
    "/api/115/check": "检查 115 账号",
    "/api/115/extract": "提取 115 链接",
    "/api/115/transfer": "转存 115 分享",
    "/api/115/monitor/run": "运行 115 监控",
    "/api/115/cleanup/run": "运行 115 清理",
    "/api/115/boost": "运行 115 助力",
    "/api/yingchao/search": "搜索影巢资源",
    "/api/yingchao/transfer": "转存影巢资源",
    "/api/moviepilot/subscribe": "推送到 MoviePilot",
    "/api/torra/subscribe": "推送到 Torra",
    "/api/symedia/subscribe": "推送到 Symedia",
}
READ_OPERATION_LABELS = {
    "/api/status": "读取服务状态",
    "/api/dashboard/system": "读取设备性能",
    "/api/hdhive/status": "刷新影巢状态",
    "/api/hdhive/identity": "读取影巢身份",
    "/api/telegram/status": "刷新 Telegram 状态",
    "/api/telegram/channels": "读取 Telegram 频道",
    "/api/discover/search": "搜索影片",
    "/api/discover/tmdb": "读取 TMDB 榜单",
    "/api/discover/streaming": "读取海外流媒体榜单",
    "/api/discover/douban": "读取豆瓣榜单",
    "/api/discover/platform-hot": "读取平台热榜",
    "/api/discover/daily-airing": "读取全球日播",
    "/api/discover/cache/status": "读取发现缓存状态",
    "/api/discover/resources/search": "搜索资源",
    "/api/subscriptions/config": "读取订阅配置",
    "/api/subscriptions/items": "读取我的订阅",
    "/api/subscriptions/detail": "读取订阅详情",
    "/api/subscriptions/calendar": "读取订阅日历",
    "/api/moviepilot/status": "检测 MoviePilot 连接",
    "/api/torra/status": "检测 Torra 连接",
    "/api/symedia/status": "检测 Symedia 连接",
}
CONFIG_SECTION_LABELS = {
    "account": "保存 115 账号设置",
    "clean": "保存清理设置",
    "library": "保存媒体库设置",
    "moviepilot": "保存 MoviePilot 设置",
    "proxy": "保存代理设置",
    "settings": "保存配置",
    "symedia": "保存 Symedia 设置",
    "telegram": "保存 Telegram 设置",
    "torra": "保存 Torra 设置",
}


def _safe_log_value(value, depth=0):
    if depth > 2:
        return "..."
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if any(secret in key_text.lower() for secret in SENSITIVE_LOG_KEYS):
                result[key_text] = "***"
            else:
                result[key_text] = _safe_log_value(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_safe_log_value(item, depth + 1) for item in value[:5]]
    if isinstance(value, str):
        return value[:160]
    return value


def _operation_payload_summary(payload):
    if not isinstance(payload, dict):
        return {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    douban = payload.get("douban") if isinstance(payload.get("douban"), dict) else {}
    rules = payload.get("resource_rules") if isinstance(payload.get("resource_rules"), dict) else {}
    rule_groups = rules.get("groups") if isinstance(rules.get("groups"), dict) else {}
    required_rules = []
    for group_key, group in rule_groups.items():
        if isinstance(group, dict) and group.get("require"):
            required_rules.append(f"{group_key}:{','.join(str(value) for value in group.get('require') or [])}")
    summary = {
        "title": item.get("title") or item.get("name") or payload.get("title") or payload.get("name") or "",
        "key": payload.get("key") or "",
        "section": payload.get("__section") or "",
        "mode": payload.get("mode") or payload.get("subscription_mode") or "",
        "source": item.get("source_label") or item.get("source") or payload.get("source") or "",
        "media_type": item.get("media_type") or item.get("type") or payload.get("type") or "",
        "tmdb_id": item.get("tmdb_id") or item.get("id") or payload.get("tmdb_id") or "",
        "share_url": payload.get("share_url") or item.get("share_url") or "",
        "target_pid": payload.get("target_pid") or "",
        "enabled": douban.get("enabled") if "enabled" in douban else "",
        "task_enabled": douban.get("task_enabled") if "task_enabled" in douban else "",
        "sources": len(douban.get("sources") or []) if isinstance(douban.get("sources"), list) else "",
        "resource_rules": "已启用" if rules.get("enabled") else ("未启用" if rules else ""),
        "rule": " ".join(required_rules[:6]),
        "channels": len(payload.get("channels") or []) if isinstance(payload.get("channels"), list) else "",
        "payload": _safe_log_value(payload),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _operation_query_summary(args):
    query = args.to_dict(flat=True) if hasattr(args, "to_dict") else {}
    if not isinstance(query, dict):
        return {}
    summary = {
        "title": query.get("title") or query.get("query") or query.get("keyword") or "",
        "key": query.get("key") or "",
        "source": query.get("source") or query.get("category") or "",
        "media_type": query.get("type") or query.get("media_type") or "",
        "tmdb_id": query.get("tmdb_id") or query.get("tmdbid") or "",
        "year": query.get("year") or "",
        "page": query.get("page") or "",
        "limit": query.get("limit") or "",
        "date": query.get("date") or "",
        "month": query.get("month") or "",
        "refresh": query.get("refresh") or "",
        "query": _safe_log_value(query),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _operation_response_summary(response):
    if not getattr(response, "is_json", False):
        return {}
    data = response.get_json(silent=True)
    if not isinstance(data, dict):
        return {}
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    auto_transfer = data.get("auto_transfer") if isinstance(data.get("auto_transfer"), dict) else {}
    subscription_task = data.get("subscription_task") if isinstance(data.get("subscription_task"), dict) else auto_transfer
    summary = {
        "ok": data.get("ok") if "ok" in data else data.get("success"),
        "result_message": data.get("message") or data.get("skipped") or "",
        "title": data.get("title") or "",
        "items": len(data.get("items") or []) if isinstance(data.get("items"), list) else "",
        "count": data.get("count") or "",
        "links": len(data.get("links") or []) if isinstance(data.get("links"), list) else "",
        "libraries": len(data.get("libraries") or []) if isinstance(data.get("libraries"), list) else "",
        "channels": len(data.get("channels") or []) if isinstance(data.get("channels"), list) else "",
        "removed_count": data.get("removed_count") or "",
        "total": stats.get("total") if stats else "",
        "movie": stats.get("movie") if stats else "",
        "tv": stats.get("tv") if stats else "",
        "errors": len(data.get("errors") or []) if isinstance(data.get("errors"), list) else "",
        "mode": subscription_task.get("label") or "",
        "task": subscription_task.get("task_label") or "",
        "auto_transfer": (f"排队 {auto_transfer.get('queued')}" if auto_transfer.get("queued") else f"{auto_transfer.get('transferred', 0)}/{auto_transfer.get('searched', 0)}") if auto_transfer.get("enabled") else "",
        "queued": subscription_task.get("queued") if subscription_task.get("queued") else "",
        "error": data.get("error") or "",
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _log_user_operation(response):
    if request.path.startswith("/api/activity/"):
        return
    if request.method not in {"GET", "POST", "DELETE"}:
        return
    if request.path.startswith(("/static/", "/api/image", "/api/emby/library-image/", "/api/telegram/channel-icons/")):
        return
    payload = request.get_json(silent=True) or {}
    label = ""
    if request.method == "GET":
        label = READ_OPERATION_LABELS.get(request.path, "")
        if not label:
            return
    else:
        label = OPERATION_LABELS.get(request.path)
        if request.path == "/api/config":
            section = str(payload.get("__section") or "").strip().lower()
            label = CONFIG_SECTION_LABELS.get(section, label)
        if request.method == "DELETE" and request.path.startswith("/api/telegram/channels/"):
            label = "删除 Telegram 频道"
        if not label:
            for prefix, value in OPERATION_LABELS.items():
                if request.path.startswith(prefix + "/"):
                    label = value
                    break
    if not label:
        label = f"{request.method} {request.path}"
    try:
        summary = _operation_query_summary(request.args) if request.method == "GET" else _operation_payload_summary(payload)
        response_summary = _operation_response_summary(response)
        for key, value in response_summary.items():
            summary.setdefault(key, value)
        ok_value = response_summary.get("ok")
        status = "error" if response.status_code >= 400 or ok_value is False else "success"
        write_activity("operation", request.path, status, label, method=request.method, status_code=response.status_code, **summary)
    except Exception:
        pass


def _hdhive_scheduler_loop():
    while True:
        try:
            run_due_hdhive_checkin()
        except Exception as exc:
            logger.error("background scheduler failed scheduler=hdhive-checkin error_type=%s", type(exc).__name__)
        time.sleep(60)


def start_hdhive_scheduler():
    global _hdhive_scheduler_started
    if _hdhive_scheduler_started:
        return
    _hdhive_scheduler_started = True
    thread = threading.Thread(target=_hdhive_scheduler_loop, name="hdhive-checkin", daemon=True)
    thread.start()


def _discover_preload_loop():
    time.sleep(3)
    while True:
        try:
            discover_runtime.preload_discover_cache()
        except Exception as exc:
            logger.error("background scheduler failed scheduler=discover-cache-preload error_type=%s", type(exc).__name__)
        time.sleep(6 * 60 * 60)


def start_discover_preload_scheduler():
    global _discover_preload_started
    if _discover_preload_started:
        return
    _discover_preload_started = True
    thread = threading.Thread(target=_discover_preload_loop, name="discover-cache-preload", daemon=True)
    thread.start()


def _subscription_scheduler_loop():
    time.sleep(5)
    while True:
        try:
            discover_runtime.run_due_subscription_task()
            discover_runtime.run_due_subscription_search_poll()
            discover_runtime.run_due_channel_mode_poll()
        except Exception as exc:
            logger.error("background scheduler failed scheduler=subscription-task error_type=%s", type(exc).__name__)
        time.sleep(60)


def start_subscription_scheduler():
    global _subscription_scheduler_started
    if _subscription_scheduler_started:
        return
    _subscription_scheduler_started = True
    thread = threading.Thread(target=_subscription_scheduler_loop, name="subscription-task", daemon=True)
    thread.start()


def start_background_runtime():
    global _background_runtime_started
    if _background_runtime_started:
        return []
    started = []
    start_hdhive_scheduler()
    started.append("hdhive-checkin")
    start_discover_preload_scheduler()
    started.append("discover-cache-preload")
    if str(os.getenv("MCC_SUBSCRIPTION_SCHEDULER_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        start_subscription_scheduler()
        started.append("subscription-task")
    _background_runtime_started = True
    return started


@core_routes.after_request
def add_no_cache_headers(response):
    _log_user_operation(response)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@core_routes.get("/")
def index():
    return render_template("index.html")


@core_routes.get("/api/status")
def api_status():
    return jsonify(project_status())


@core_routes.get("/api/health")
def api_health():
    try:
        runtime_config = read_config()
    except Exception:
        runtime_config = {}

    emby_base_url = os.getenv("EMBY_BASE_URL") or runtime_config.get("ENV_EMBY_SERVER_URL", "")
    emby_api_key = os.getenv("EMBY_API_KEY") or runtime_config.get("ENV_EMBY_API_KEY", "")
    emby_user_id = os.getenv("EMBY_USER_ID", "")
    emby_username = os.getenv("EMBY_USERNAME", "")
    emby_password = os.getenv("EMBY_PASSWORD", "")
    torra_base_url = os.getenv("TORRA_BASE_URL") or runtime_config.get("ENV_TORRA_URL", "")
    torra_token = os.getenv("TORRA_TOKEN") or runtime_config.get("ENV_TORRA_TOKEN", "")
    torra_username = os.getenv("TORRA_USERNAME", "")
    torra_password = os.getenv("TORRA_PASSWORD", "")
    symedia_base_url = os.getenv("SYMEDIA_BASE_URL") or runtime_config.get("ENV_SYMEDIA_URL", "")
    symedia_token = os.getenv("SYMEDIA_TOKEN") or runtime_config.get("ENV_SYMEDIA_TOKEN", "")
    symedia_username = os.getenv("SYMEDIA_USERNAME") or runtime_config.get("ENV_SYMEDIA_USERNAME", "")
    symedia_password = os.getenv("SYMEDIA_PASSWORD") or runtime_config.get("ENV_SYMEDIA_PASSWORD", "")

    return jsonify({
        "app": "media-control-center",
        "status": "ok",
        "runtime": "python",
        "services": [
            {
                "id": "emby",
                "name": "Emby",
                "type": "media-library",
                "configured": bool(
                    emby_base_url
                    and ((emby_api_key and emby_user_id) or (emby_username and emby_password))
                ),
            },
            {
                "id": "qbittorrent",
                "name": "qBittorrent",
                "type": "downloader",
                "configured": bool(os.getenv("QB_BASE_URL", "")),
            },
            {
                "id": "torra",
                "name": "Torra",
                "type": "source",
                "configured": bool(
                    torra_base_url and (torra_token or (torra_username and torra_password))
                ),
            },
            {
                "id": "symedia",
                "name": "Symedia",
                "type": "tool",
                "configured": bool(
                    symedia_base_url and (symedia_token or (symedia_username and symedia_password))
                ),
            },
            {
                "id": "subscriptions",
                "name": "订阅中枢",
                "type": "subscription",
                "configured": bool(os.getenv("TMDB_API_KEY", "")),
            },
            {
                "id": "nasemby-core",
                "name": "NasEmby Core",
                "type": "subscription-core",
                "configured": True,
            },
            {
                "id": "cloud115",
                "name": "115",
                "type": "cloud-storage",
                "configured": bool(runtime_config.get("ENV_115_COOKIES")),
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "type": "resource-source",
                "configured": bool(
                    runtime_config.get("ENV_TG_API_ID") and runtime_config.get("ENV_TG_API_HASH")
                ),
            },
            {
                "id": "hdhive",
                "name": "HDHive / pansou",
                "type": "resource-source",
                "configured": _environment_flag_enabled(
                    "ENV_HDHIVE_CHECKIN_ENABLED",
                    environment=runtime_config,
                ),
            },
            {
                "id": "moviepilot",
                "name": "MoviePilot",
                "type": "pt-compatibility",
                "configured": bool(
                    runtime_config.get("ENV_MOVIEPILOT_URL")
                    and runtime_config.get("ENV_MOVIEPILOT_API_TOKEN")
                ),
            },
        ],
    })


@core_routes.get("/api/dashboard/system")
def api_dashboard_system():
    try:
        return jsonify(dashboard_system_metrics())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.get("/api/hdhive/authorize")
def api_hdhive_authorize():
    try:
        return redirect(hdhive_auth_url(), code=302)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 501


@core_routes.get("/api/hdhive/status")
def api_hdhive_status():
    data = hdhive_status()
    return jsonify(data), 200 if data.get("ok") else 501


@core_routes.get("/api/hdhive/identity")
def api_hdhive_identity():
    data = hdhive_identity()
    return jsonify(data), 200 if data.get("ok") else 501


@core_routes.post("/api/hdhive/config")
def api_hdhive_config():
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "config": update_hdhive_config(payload)})


@core_routes.post("/api/hdhive/account")
def api_hdhive_account():
    payload = request.get_json(silent=True) or {}
    return jsonify(update_hdhive_account(payload))


@core_routes.post("/api/hdhive/checkin")
def api_hdhive_checkin():
    data = hdhive_checkin_now()
    return jsonify(data), 200 if data.get("ok") else 502


@core_routes.get("/api/config")
def api_get_config():
    return jsonify({"ok": True, "config": read_config()})


@core_routes.post("/api/config")
def api_save_config():
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "config": write_config(payload)})


@core_routes.post("/api/emby/libraries")
def api_emby_libraries():
    try:
        return jsonify(fetch_emby_libraries(request.get_json(silent=True) or {}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.get("/api/emby/library-image/<path:item_id>")
def api_emby_library_image(item_id):
    try:
        body, content_type = fetch_emby_library_image(item_id)
        return Response(body, content_type=content_type, headers={"Cache-Control": "public, max-age=3600"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@core_routes.get("/api/telegram/status")
def api_telegram_status():
    try:
        data = telegram_status()
        channels = data.get("channels") if isinstance(data.get("channels"), list) else []
        message = "Telegram 已登录" if data.get("authorized") else "Telegram 未登录或未授权"
        write_activity("operation", "telegram_status", "success" if data.get("ok", True) else "error", message, channels=len(channels))
        return jsonify(data)
    except Exception as exc:
        write_activity("operation", "telegram_status", "error", f"Telegram 状态检测失败：{exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/telegram/send-code")
def api_telegram_send_code():
    try:
        return jsonify(telegram_send_login_code(request.get_json(silent=True) or {}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@core_routes.post("/api/telegram/sign-in")
def api_telegram_sign_in():
    try:
        return jsonify(telegram_sign_in(request.get_json(silent=True) or {}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@core_routes.post("/api/telegram/logout")
def api_telegram_logout():
    try:
        return jsonify(telegram_logout())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.get("/api/telegram/channels")
def api_telegram_channels():
    return jsonify(telegram_list_channels())


@core_routes.get("/api/telegram/channel-icons/<path:filename>")
def api_telegram_channel_icon(filename):
    return send_from_directory(DATA_DIR / "telegram_channel_icons", filename)


@core_routes.post("/api/telegram/channels")
def api_telegram_save_channels():
    try:
        return jsonify(telegram_save_channels(request.get_json(silent=True) or {}))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@core_routes.delete("/api/telegram/channels/<int:index>")
def api_telegram_delete_channel(index):
    try:
        return jsonify(telegram_delete_channel(index))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@core_routes.post("/api/telegram/channels/reorder")
def api_telegram_reorder_channels():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(telegram_reorder_channels(int(payload.get("from", -1)), int(payload.get("to", -1))))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@core_routes.get("/api/discover/search")
def api_discover_search():
    query = request.args.to_dict()
    try:
        data = discover_runtime.search_media(query)
        write_activity(
            "operation",
            "discover_search",
            "success",
            f"影片搜索完成：{query.get('title') or query.get('query') or ''}",
            title=query.get("title") or query.get("query") or "",
            media_type=query.get("type") or "",
            items=len(data.get("items") or []),
            total=data.get("total_results") or "",
        )
        return jsonify(data)
    except Exception as exc:
        write_activity("operation", "discover_search", "error", f"影片搜索失败：{exc}", title=query.get("title") or query.get("query") or "", error=str(exc))
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/tmdb")
def api_discover_tmdb():
    try:
        return jsonify(discover_runtime.fetch_tmdb(request.args.to_dict()))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/streaming")
def api_discover_streaming():
    try:
        return jsonify(discover_runtime.fetch_streaming(request.args.to_dict()))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/douban")
def api_discover_douban():
    try:
        return jsonify(discover_runtime.fetch_douban(request.args.to_dict()))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/platform-hot")
def api_discover_platform_hot():
    try:
        return jsonify(discover_runtime.fetch_platform_hot(request.args.to_dict()))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/daily-airing")
def api_discover_daily_airing():
    try:
        return jsonify(discover_runtime.fetch_daily_airing(request.args.to_dict()))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/cache/status")
def api_discover_cache_status():
    return jsonify(discover_runtime.discover_cache_stats())


@core_routes.post("/api/discover/cache/preload")
def api_discover_cache_preload():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(discover_runtime.preload_discover_cache(payload.get("pages") or 3))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/discover/resources/search")
def api_discover_resources_search():
    query = request.args.to_dict()
    try:
        data = discover_runtime.search_resources(query)
        write_activity(
            "operation",
            "resource_search",
            "success" if data.get("items") else "skip",
            f"资源搜索完成：{query.get('title') or ''}",
            title=query.get("title") or "",
            media_type=query.get("type") or "",
            tmdb_id=query.get("tmdb_id") or "",
            year=query.get("year") or "",
            items=len(data.get("items") or []),
            sources=", ".join(f"{item.get('label')}:{item.get('count')}" for item in (data.get("sources") or []) if isinstance(item, dict)),
            errors=len(data.get("errors") or []) if isinstance(data.get("errors"), list) else "",
            cache_hits=", ".join(data.get("cache_hits") or []) if isinstance(data.get("cache_hits"), list) else "",
        )
        return jsonify(data)
    except Exception as exc:
        write_activity("operation", "resource_search", "error", f"资源搜索失败：{exc}", title=query.get("title") or "", media_type=query.get("type") or "", tmdb_id=query.get("tmdb_id") or "", error=str(exc))
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/discover/resources/preview")
def api_discover_resources_preview():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(discover_runtime.fetch_resource_preview(payload))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/image")
def api_discover_image():
    url = request.args.get("url") or ""
    if not discover_runtime.is_supported_image_proxy_url(url):
        return jsonify({"success": False, "error": "Unsupported image URL"}), 400
    try:
        body, content_type = discover_runtime.http_bytes(url)
        return Response(body, content_type=content_type)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/subscriptions/config")
def api_subscriptions_config():
    try:
        return jsonify({"success": True, "config": discover_runtime.load_subscription_config()})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/config")
def api_subscriptions_save_config():
    try:
        config = discover_runtime.save_subscription_config(request.get_json(silent=True) or {})
        mode_switch_task = config.pop("mode_switch_task", {}) if isinstance(config, dict) else {}
        return jsonify({"success": True, "config": config, "mode_switch_task": mode_switch_task})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/subscriptions/items")
def api_subscriptions_items():
    try:
        include_progress = request.args.get("include_progress", request.args.get("progress", "0")) == "1"
        read_only = request.args.get("read_only", "0") == "1"
        data = discover_runtime.load_subscription_items(
            with_progress=include_progress,
            remove_completed=not read_only,
            persist_progress=not read_only,
        )
        items = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            media_type = discover_runtime.discover_item_media_type(row) or str(row.get("media_type") or "")
            row["key"] = discover_runtime.get_subscription_item_key(row)
            row["media_type"] = media_type
            row["tmdb_id"] = discover_runtime.discover_item_tmdb_id(row, media_type)
            items.append(row)
        return jsonify({
            "success": True,
            "items": items,
            "blocked_titles": discover_runtime.subscription_blocked_titles(),
            "saved_item": data.get("saved_item") or {},
            "message": data.get("message") or "保存订阅成功",
            "last_run_at": data.get("last_run_at") or "",
            "stats": data.get("stats") or {},
            "errors": data.get("errors") or [],
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/subscriptions/detail")
def api_subscriptions_detail():
    query = request.args.to_dict()
    try:
        data = discover_runtime.fetch_subscription_detail(query)
        item = data.get("item") if isinstance(data, dict) and isinstance(data.get("item"), dict) else {}
        detail = data.get("detail") if isinstance(data, dict) and isinstance(data.get("detail"), dict) else {}
        write_activity(
            "subscription",
            "subscription_detail",
            "success",
            f"打开订阅详情：{item.get('title') or detail.get('name') or query.get('key') or ''}",
            title=item.get("title") or detail.get("name") or "",
            key=query.get("key") or "",
            media_type=item.get("media_type") or detail.get("media_type") or "",
            tmdb_id=item.get("tmdb_id") or detail.get("id") or "",
        )
        return jsonify(data)
    except Exception as exc:
        write_activity("subscription", "subscription_detail", "error", f"订阅详情加载失败：{exc}", key=query.get("key") or "", error=str(exc))
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.get("/api/subscriptions/calendar")
def api_subscriptions_calendar():
    try:
        return jsonify(discover_runtime.build_subscription_calendar(
            request.args.get("year"),
            request.args.get("month"),
            request.args.get("type") or "all",
        ))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/run")
def api_subscriptions_run():
    try:
        return jsonify(discover_runtime.run_subscription_now())
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/daily-airing/sync")
def api_subscriptions_sync_daily_airing():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(discover_runtime.sync_daily_airing_subscriptions(payload.get("limit") or 72))
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/save")
def api_subscriptions_save_item():
    try:
        data = discover_runtime.save_subscription_item(request.get_json(silent=True) or {})
        return jsonify({
            "success": True,
            "items": data.get("items") or [],
            "blocked_titles": discover_runtime.subscription_blocked_titles(),
            "last_run_at": data.get("last_run_at") or "",
            "stats": data.get("stats") or {},
            "errors": data.get("errors") or [],
            "message": data.get("message") or "",
            "auto_transfer": data.get("auto_transfer") or {},
            "subscription_task": data.get("subscription_task") or data.get("auto_transfer") or {},
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/delete")
def api_subscriptions_delete_item():
    try:
        data = discover_runtime.delete_subscription_item(request.get_json(silent=True) or {})
        return jsonify({
            "success": True,
            "items": data.get("items") or [],
            "blocked_titles": discover_runtime.subscription_blocked_titles(),
            "last_run_at": data.get("last_run_at") or "",
            "stats": data.get("stats") or {},
            "errors": data.get("errors") or [],
            "removed_count": data.get("removed_count") or 0,
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/block")
def api_subscriptions_block_item():
    try:
        data = discover_runtime.block_subscription_item(request.get_json(silent=True) or {})
        return jsonify({
            "success": True,
            "items": data.get("items") or [],
            "blocked_titles": data.get("blocked_titles") or discover_runtime.subscription_blocked_titles(),
            "config": data.get("config") or discover_runtime.load_subscription_config(),
            "last_run_at": data.get("last_run_at") or "",
            "stats": data.get("stats") or {},
            "errors": data.get("errors") or [],
            "removed_count": data.get("removed_count") or 0,
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/unblock")
def api_subscriptions_unblock_item():
    try:
        data = discover_runtime.unblock_subscription_title(request.get_json(silent=True) or {})
        return jsonify({
            "success": True,
            "items": data.get("items") or [],
            "blocked_titles": data.get("blocked_titles") or discover_runtime.subscription_blocked_titles(),
            "config": data.get("config") or discover_runtime.load_subscription_config(),
            "last_run_at": data.get("last_run_at") or "",
            "stats": data.get("stats") or {},
            "errors": data.get("errors") or [],
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502


@core_routes.post("/api/subscriptions/clear")
def api_subscriptions_clear_items():
    try:
        data = discover_runtime.clear_subscription_items()
        return jsonify({
            "success": True,
            "items": data.get("items") or [],
            "blocked_titles": discover_runtime.subscription_blocked_titles(),
            "last_run_at": data.get("last_run_at") or "",
            "stats": data.get("stats") or {},
            "errors": data.get("errors") or [],
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 502



@core_routes.get("/api/activity/logs")
def api_activity_logs():
    category = str(request.args.get("category") or "").strip()
    limit = request.args.get("limit", "200")
    return jsonify({"ok": True, "logs": read_activities(limit=limit, category=category)})


@core_routes.post("/api/activity/clear")
def api_activity_clear():
    clear_activities()
    return jsonify({"ok": True, "message": "日志已清空"})


@core_routes.post("/api/activity/event")
def api_activity_event():
    payload = request.get_json(silent=True) or {}
    category = str(payload.get("category") or "operation").strip() or "operation"
    if category not in {"operation", "subscription", "push", "transfer", "system"}:
        category = "operation"
    action = str(payload.get("action") or "ui_event").strip()[:80] or "ui_event"
    status = str(payload.get("status") or "info").strip() or "info"
    if status not in {"start", "success", "error", "skip", "info"}:
        status = "info"
    message = str(payload.get("message") or action).strip()[:160]
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    safe_meta = _safe_log_value(meta)
    if not isinstance(safe_meta, dict):
        safe_meta = {}
    for reserved in ("category", "action", "status", "message"):
        safe_meta.pop(reserved, None)
    write_activity(category, action, status, message, **safe_meta)
    return jsonify({"ok": True})
@core_routes.post("/api/115/check")
def api_check_115():
    try:
        return jsonify(check_115_account())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/115/extract")
def api_extract_115():
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "links": extract_115_links(str(payload.get("text") or ""))})


@core_routes.post("/api/115/transfer")
def api_transfer_115():
    payload = request.get_json(silent=True) or {}
    share_url = str(payload.get("share_url") or "").strip()
    if not share_url:
        return jsonify({"ok": False, "error": "缺少115分享链接"}), 400
    try:
        return jsonify(transfer_115_share(share_url, payload.get("target_pid")))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/yingchao/search")
def api_yingchao_search():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(search_yingchao_resources(
            str(payload.get("title") or ""),
            str(payload.get("type") or "tv"),
            str(payload.get("tmdb_id") or ""),
        ))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/yingchao/transfer")
def api_yingchao_transfer():
    payload = request.get_json(silent=True) or {}
    item = payload.get("item")
    if not isinstance(item, dict):
        return jsonify({"ok": False, "error": "缺少影巢资源"}), 400
    try:
        return jsonify(transfer_yingchao_item(item, payload.get("target_pid")))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.get("/api/moviepilot/status")
def api_moviepilot_status():
    try:
        data = moviepilot_status()
        write_activity("operation", "moviepilot_status", "success" if data.get("configured") and data.get("ok", True) else "error", str(data.get("message") or "MoviePilot 状态检测"))
        return jsonify(data)
    except Exception as exc:
        write_activity("operation", "moviepilot_status", "error", f"MoviePilot 连接失败：{exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/moviepilot/subscribe")
def api_moviepilot_subscribe():
    try:
        data = moviepilot_subscribe(request.get_json(silent=True) or {})
        return jsonify(data), 200 if data.get("ok") else 502
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.get("/api/torra/status")
def api_torra_status():
    try:
        data = torra_status()
        write_activity("operation", "torra_status", "success" if data.get("configured") and data.get("ok", True) else "error", str(data.get("message") or "Torra 状态检测"))
        return jsonify(data)
    except Exception as exc:
        write_activity("operation", "torra_status", "error", f"Torra 连接失败：{exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/torra/subscribe")
def api_torra_subscribe():
    try:
        data = torra_subscribe(request.get_json(silent=True) or {})
        return jsonify(data), 200 if data.get("ok") else 502
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.get("/api/symedia/status")
def api_symedia_status():
    try:
        data = symedia_status()
        write_activity("operation", "symedia_status", "success" if data.get("configured") and data.get("ok", True) else "error", str(data.get("message") or "Symedia 状态检测"))
        return jsonify(data)
    except Exception as exc:
        write_activity("operation", "symedia_status", "error", f"Symedia 连接失败：{exc}")
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/symedia/subscribe")
def api_symedia_subscribe():
    try:
        data = symedia_subscribe(request.get_json(silent=True) or {})
        return jsonify(data), 200 if data.get("ok") else 502
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/115/monitor/run")
def api_run_monitor():
    try:
        return jsonify(run_115_monitor_once())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/115/cleanup/run")
def api_run_cleanup():
    try:
        return jsonify(run_115_cleanup())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


@core_routes.post("/api/115/boost")
def api_run_boost():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(run_115_invite_boost(str(payload.get("text") or "")))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502


def create_app(
    access_environment=None,
    frontend_dist=None,
    now_ms=None,
    mineradio_public_dir=None,
    mineradio_fragment_dir=None,
    emby_client_factory=None,
    external_image_fetcher=None,
    emby_clock=None,
    qb_client_factory=None,
    qb_clock=None,
    torra_client_factory=None,
    torra_clock=None,
    symedia_client_factory=None,
    symedia_clock=None,
    integration_functions=None,
    cloud_functions=None,
    cloud_state_path=None,
    cloud_clock=None,
    system_metrics_sampler=None,
    system_metrics_clock=None,
):
    environment = os.environ if access_environment is None else access_environment
    application = Flask(__name__, static_folder=None)
    application.secret_key = os.getenv("APP_SECRET", "nasemby-dev")
    if is_production_environment(environment):
        application.wsgi_app = ProxyFix(application.wsgi_app, x_for=1, x_proto=1, x_host=1)
    configure_http_runtime(application)
    configure_access_runtime(
        application,
        AccessAuth(resolve_access_config(environment), now_ms=now_ms),
    )
    register_mineradio(
        application,
        public_dir=mineradio_public_dir,
        environment=environment,
        fragment_dir=mineradio_fragment_dir,
    )
    register_emby_reads(
        application,
        environment=environment,
        client_factory=emby_client_factory,
        external_image_fetcher=external_image_fetcher,
        clock=emby_clock,
    )
    qb_client = register_qbittorrent_read(
        application,
        environment=environment,
        client_factory=qb_client_factory,
        clock=qb_clock,
    )
    register_qbittorrent_actions(application, qb_client)
    register_torra_read(
        application,
        environment=environment,
        client_factory=torra_client_factory,
        clock=torra_clock,
    )
    register_symedia_read(
        application,
        environment=environment,
        client_factory=symedia_client_factory,
        clock=symedia_clock,
    )
    register_emby_refresh(application)
    register_task_chain(application)
    register_integrations(
        application,
        environment=environment,
        functions=integration_functions,
    )
    register_cloud_acquisition(
        application,
        environment=environment,
        functions=cloud_functions,
        state_path=cloud_state_path,
        clock=cloud_clock,
    )
    register_system_metrics(
        application,
        sampler=system_metrics_sampler,
        clock=system_metrics_clock,
    )
    register_discover_compat(application)
    register_subscription_compat(application, environment=environment)
    register_frontend(
        application,
        frontend_dist if frontend_dist is not None else environment.get("MCC_FRONTEND_DIST", ""),
    )
    application.register_blueprint(core_routes)

    @application.before_request
    def guard_preserved_core_management_api():
        endpoint = request.endpoint or ""
        if endpoint.startswith("nasemby_core_routes.") and request.path not in {"/api/status", "/api/health"}:
            if _environment_flag_enabled(
                "MCC_PRESERVED_CORE_API_ENABLED",
                environment=environment,
            ):
                return None
            return jsonify({
                "ok": False,
                "error": "该核心接口已保留，等待统一页面完成安全接入",
                "code": "PRESERVED_CORE_API_DISABLED",
            }), 503

    return application


app = create_app()


if __name__ == "__main__":
    start_background_runtime()
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "12388"))
    app.run(host=host, port=port)
