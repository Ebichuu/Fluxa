from __future__ import annotations

from urllib.parse import urlsplit

from flask import Flask, jsonify, request

from app.config import CONFIG_FIELDS, read_config, write_config
from app.emby_runtime import resolve_emby_config
from app.qbittorrent_runtime import resolve_qbittorrent_config
from app.symedia_read_runtime import resolve_symedia_read_config
from app.torra_read_runtime import resolve_torra_read_config


SECRET_HINTS = ("PASSWORD", "TOKEN", "API_KEY", "API_HASH", "COOKIES", "CLIENT_SECRET")
BOOLEAN_SUFFIXES = ("_ENABLED", "_SWITCH", "_AUTO_SUBSCRIBE", "_AUTO_CLASSIFY", "_OVERWRITE")
BOOLEAN_FIELDS = {
    "ENV_HDHIVE_CHECKIN_GAMBLER",
    "ENV_HDHIVE_CHECKIN_NOTIFY",
    "ENV_HDHIVE_EXPIRY_REMINDER",
}
NUMBER_FIELDS = {
    "ENV_CHECK_INTERVAL",
    "ENV_SUBSCRIPTION_SEARCH_INTERVAL",
    "ENV_TG_API_ID",
    "ENV_TG_ADMIN_USER_ID",
    "ENV_HDHIVE_UNLOCK_POINTS_LIMIT",
    "ENV_HDHIVE_UNLOCK_RATE_LIMIT",
    "ENV_HDHIVE_REMINDER_INTERVAL_HOURS",
}
RESTART_REQUIRED = {
    "MCC_SUBSCRIPTION_SCHEDULER_ENABLED",
    "MCC_PRIVATE_RSS_ENABLED",
    "MCC_TORRA_QUALITY_WATCH_ENABLED",
}


FIELD_LABELS = {
    "MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED": "Torra 订阅状态同步",
    "EMBY_BASE_URL": "Emby 服务地址",
    "EMBY_API_KEY": "Emby API Key",
    "EMBY_USER_ID": "Emby 用户 ID",
    "EMBY_USERNAME": "Emby 用户名",
    "EMBY_PASSWORD": "Emby 密码",
    "QB_BASE_URL": "qBittorrent 地址",
    "QB_USERNAME": "qBittorrent 用户名",
    "QB_PASSWORD": "qBittorrent 密码",
    "TORRA_BASE_URL": "Torra 地址",
    "TORRA_TOKEN": "Torra Token",
    "TORRA_USERNAME": "Torra 用户名",
    "TORRA_PASSWORD": "Torra 密码",
    "TORRA_PUSH_ENABLED": "允许 Torra 推送",
    "TORRA_DOWNLOAD_ROOT": "Torra 下载根目录",
    "TORRA_DOWNLOADER_ID": "Torra 下载器 ID",
    "SYMEDIA_BASE_URL": "Symedia 地址",
    "SYMEDIA_TOKEN": "Symedia Token",
    "SYMEDIA_USERNAME": "Symedia 用户名",
    "SYMEDIA_PASSWORD": "Symedia 密码",
    "TMDB_API_KEY": "TMDB API Key",
    "TMDB_API_TOKEN": "TMDB Bearer Token",
    "ENV_115_COOKIES": "115 Cookie",
    "ENV_UPLOAD_PID": "115 上传目录 ID",
    "ENV_TG_PHONE": "Telegram 手机号",
    "ENV_TG_API_ID": "Telegram API ID",
    "ENV_TG_API_HASH": "Telegram API Hash",
    "ENV_MOVIEPILOT_URL": "MoviePilot 地址",
    "ENV_MOVIEPILOT_API_TOKEN": "MoviePilot Token",
    "ENV_PROXY": "外部请求代理",
}


GROUP_DEFINITIONS = [
    ("emby", "Emby", "媒体库首页、图片和入库刷新", ("EMBY_", "ENV_EMBY_")),
    ("qbittorrent", "qBittorrent", "下载任务读取与暂停/恢复", ("QB_",)),
    ("torra", "Torra", "PT 订阅、下载器和追更洗版", ("TORRA_", "ENV_TORRA_")),
    ("symedia", "Symedia", "115 整理入库记录", ("SYMEDIA_", "ENV_SYMEDIA_")),
    ("tmdb", "TMDB 与发现", "影视元数据、榜单和日历", ("TMDB_",)),
    ("moviepilot", "MoviePilot", "备用兼容通道", ("MOVIEPILOT_", "ENV_MOVIEPILOT_")),
    ("cloud", "115 与 123 云盘", "云盘上传、分类和整理兼容配置", ("ENV_115_", "ENV_123_", "ENV_UPLOAD_")),
    ("telegram", "Telegram", "频道、通知和资源来源兼容配置", ("ENV_TG_",)),
    ("hdhive", "HDHive", "签到和积分兼容配置", ("ENV_HDHIVE_",)),
    ("automation", "自动化与安全开关", "控制写入、采集、调度和兼容能力", ("MCC_", "NASEMBY_")),
    ("advanced", "高级兼容配置", "历史 NasEmby 兼容字段和网络参数", ("ENV_",)),
]


def _group_for_key(key: str):
    for group_id, title, note, prefixes in GROUP_DEFINITIONS:
        if any(key.startswith(prefix) for prefix in prefixes):
            return group_id, title, note
    return "advanced", "高级兼容配置", "历史兼容字段"


def _field_type(key: str) -> str:
    upper = key.upper()
    if any(hint in upper for hint in SECRET_HINTS):
        return "secret"
    if key in BOOLEAN_FIELDS or upper.endswith(BOOLEAN_SUFFIXES):
        return "boolean"
    if key in NUMBER_FIELDS:
        return "number"
    if upper.endswith("_URL") or upper in {"ENV_PROXY"}:
        return "url"
    return "text"


def _label_for_key(key: str) -> str:
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    return key.replace("_", " ").title()


def _catalog_field(key: str) -> dict:
    field_type = _field_type(key)
    return {
        "key": key,
        "label": _label_for_key(key),
        "type": field_type,
        "secret": field_type == "secret",
        "restartRequired": key in RESTART_REQUIRED,
        "description": "已保存值不会回显，留空保持原值" if field_type == "secret" else "",
    }


def _environment_values(environment) -> dict[str, str]:
    values = read_config()
    if environment is not None:
        for key in CONFIG_FIELDS:
            if key in environment:
                values[key] = str(environment.get(key) or "")
    return values


def build_runtime_settings(environment=None) -> dict:
    values = _environment_values(environment)
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for key in CONFIG_FIELDS:
        group_id, title, note = _group_for_key(key)
        if group_id not in grouped:
            grouped[group_id] = {"id": group_id, "title": title, "note": note, "fields": []}
            order.append(group_id)
        field = _catalog_field(key)
        raw = str(values.get(key) or "")
        if field["secret"]:
            field["value"] = ""
            field["hasValue"] = bool(raw)
        else:
            field["value"] = raw
            field["hasValue"] = bool(raw)
        grouped[group_id]["fields"].append(field)
    return {"success": True, "groups": [grouped[group_id] for group_id in order]}


def _normalise_value(field: dict, value) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    value = str(value if value is not None else "").strip()
    if len(value) > 4096 or "\r" in value or "\n" in value:
        raise ValueError(f"{field['key']} 值无效或过长")
    if field["type"] == "boolean":
        lowered = value.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return "true"
        if lowered in {"0", "false", "no", "off"}:
            return "false"
        raise ValueError(f"{field['key']} 必须是布尔值")
    if field["type"] == "number" and value:
        try:
            int(value)
        except ValueError as exc:
            raise ValueError(f"{field['key']} 必须是数字") from exc
    if field["type"] == "url" and value:
        parsed = urlsplit(value)
        allowed_schemes = {"http", "https", "socks4", "socks5", "socks5h"} if field["key"] == "ENV_PROXY" else {"http", "https"}
        if parsed.scheme not in allowed_schemes or not parsed.netloc:
            raise ValueError(f"{field['key']} 必须是完整的服务地址")
    return value


def save_runtime_settings(payload: dict, environment=None, app: Flask | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("配置请求必须是对象")
    raw_values = payload.get("values", {})
    clear_secrets = payload.get("clearSecrets", [])
    if not isinstance(raw_values, dict) or not isinstance(clear_secrets, list):
        raise ValueError("配置请求格式不正确")
    fields = {key: _catalog_field(key) for key in CONFIG_FIELDS}
    unknown = (set(raw_values) | set(clear_secrets)) - set(fields)
    if unknown:
        raise ValueError(f"不支持的配置项：{', '.join(sorted(unknown))}")
    clear_set = set(clear_secrets)
    for key in clear_set:
        if not fields[key]["secret"]:
            raise ValueError(f"{key} 不是敏感配置，不能使用清除操作")
    values = {}
    for key, value in raw_values.items():
        field = fields[key]
        normalised = _normalise_value(field, value)
        if field["secret"] and not normalised:
            continue
        values[key] = normalised
    merged_values = _environment_values(environment)
    merged_values.update(values)
    for key in clear_set:
        merged_values[key] = ""
    saved = write_config(merged_values, clear_fields=clear_set)
    target_environment = environment
    if target_environment is not None:
        for key in CONFIG_FIELDS:
            if key in saved:
                target_environment[key] = saved[key]
    import os
    for key, value in saved.items():
        os.environ[key] = value

    if app is not None:
        clients = (
            ("mcc_emby_client", resolve_emby_config, "reconfigure"),
            ("mcc_qbittorrent_client", resolve_qbittorrent_config, "reconfigure"),
            ("mcc_torra_client", resolve_torra_read_config, "reconfigure"),
            ("mcc_torra_quality_client", resolve_torra_read_config, "reconfigure"),
            ("mcc_symedia_client", resolve_symedia_read_config, "reconfigure"),
        )
        for extension, resolver, method_name in clients:
            client = app.extensions.get(extension)
            method = getattr(client, method_name, None)
            if callable(method):
                method(resolver(target_environment or os.environ))
    changed_keys = sorted(set(values) | clear_set)
    restart_required = sorted(key for key in changed_keys if key in RESTART_REQUIRED)
    result = build_runtime_settings(target_environment or os.environ)
    result.update({
        "changedKeys": changed_keys,
        "restartRequired": restart_required,
        "message": "配置已保存" if not restart_required else "配置已保存，部分调度器将在重启后生效",
    })
    return result


def register_runtime_settings(app: Flask, environment=None):
    @app.get("/api/v2/settings/runtime")
    def runtime_settings_get():
        return jsonify(build_runtime_settings(environment))

    @app.put("/api/v2/settings/runtime")
    def runtime_settings_put():
        try:
            payload = request.get_json(silent=True) or {}
            return jsonify(save_runtime_settings(payload, environment, app))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 422
