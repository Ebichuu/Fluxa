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
    "MCC_SUBSCRIPTION_SCHEDULER_ENABLED": "启用后台订阅定时扫描",
    "MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED": "Torra 订阅状态同步",
    "NASEMBY_CORE_WRITE_ENABLED": "允许订阅写入",
    "MCC_PRIVATE_RSS_ENABLED": "启用私人 RSS 采集",
    "MCC_TORRA_QUALITY_WATCH_ENABLED": "启用 Torra 质量观察",
    "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "允许下载升级候选",
    "MCC_MOVIEPILOT_BACKUP_ENABLED": "启用 MoviePilot 备用通道",
    "MCC_PRESERVED_CORE_API_ENABLED": "启用旧核心兼容接口",
    "MCC_INTEGRATION_PROBE_ENABLED": "启用服务连通性探测",
    "MCC_INTEGRATION_MANAGEMENT_ENABLED": "允许管理外部服务",
    "MCC_TELEGRAM_MANAGEMENT_ENABLED": "允许管理 Telegram",
    "MCC_HDHIVE_MANAGEMENT_ENABLED": "允许管理 HDHive",
    "MCC_CLOUD_SEARCH_ENABLED": "启用云盘资源搜索",
    "MCC_CLOUD_TRANSFER_ENABLED": "允许云盘转存",
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
    "ENV_115_LINK_UPLOAD_PID": "115 链接转存目录 ID",
    "ENV_115_UPLOAD_PID": "115 默认上传目录 ID",
    "ENV_UPLOAD_PID": "115 主上传目录 ID",
    "ENV_115_TGMONITOR_SWITCH": "启用旧版 Telegram 监控转存",
    "ENV_115_TG_CHANNEL": "旧版 Telegram 监控频道",
    "ENV_CHECK_INTERVAL": "旧自动化检查间隔",
    "ENV_SUBSCRIPTION_SEARCH_INTERVAL": "旧订阅搜索间隔",
    "ENV_TG_PHONE": "Telegram 手机号",
    "ENV_TG_API_ID": "Telegram API ID",
    "ENV_TG_API_HASH": "Telegram API Hash",
    "ENV_TG_CHANNELS": "Telegram 资源频道列表",
    "ENV_115_CLEAN_PID": "115 清理目录 ID",
    "ENV_115_TRASH_PASSWORD": "115 回收站密码",
    "ENV_TG_BOT_TOKEN": "Telegram Bot Token",
    "ENV_TG_ADMIN_USER_ID": "Telegram 管理员用户 ID",
    "ENV_TG_TRANSFER_NOTIFY_ENABLED": "启用转存通知",
    "ENV_TG_TRANSFER_NOTIFY_CHAT_IDS": "转存通知会话 ID",
    "ENV_TG_TRANSFER_NOTIFY_WHITELIST": "转存通知白名单",
    "ENV_TG_TRANSFER_NOTIFY_BLACKLIST": "转存通知黑名单",
    "ENV_TG_TRANSFER_NOTIFY_TEMPLATE": "转存通知模板",
    "ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED": "启用订阅通知",
    "ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE": "订阅通知模板",
    "ENV_PTTO115_SWITCH": "启用旧 PT 转存 115",
    "ENV_PTTO115_UPLOAD_PID": "旧 PT 转存 115 目录 ID",
    "ENV_PTTO123_SWITCH": "启用旧 PT 转存 123",
    "ENV_PTTO123_UPLOAD_PID": "旧 PT 转存 123 目录 ID",
    "ENV_123_CLIENT_ID": "123 云盘 Client ID",
    "ENV_123_CLIENT_SECRET": "123 云盘 Client Secret",
    "ENV_EMBY_SERVER_URL": "旧 Emby 服务地址",
    "ENV_EMBY_API_KEY": "旧 Emby API Key",
    "ENV_MEDIA_LIBRARY_ADMIN": "旧媒体库管理员账号",
    "ENV_MEDIA_LIBRARY_PASSWORD": "旧媒体库管理员密码",
    "ENV_MEDIA_SYNC_CATEGORIES": "旧媒体库同步分类",
    "ENV_115_AUTO_CLASSIFY": "启用 115 自动分类",
    "ENV_115_CLASSIFY_OVERWRITE": "允许覆盖 115 分类",
    "ENV_115_CATEGORY_ROOT": "115 分类根目录 ID",
    "ENV_MOVIEPILOT_URL": "MoviePilot 地址",
    "ENV_MOVIEPILOT_API_TOKEN": "MoviePilot Token",
    "ENV_MOVIEPILOT_USERNAME": "MoviePilot 用户名",
    "ENV_MOVIEPILOT_AUTO_SUBSCRIBE": "启用 MoviePilot 自动订阅",
    "ENV_TORRA_URL": "旧 Torra 服务地址",
    "ENV_TORRA_TOKEN": "旧 Torra Token",
    "ENV_TORRA_AUTO_SUBSCRIBE": "启用旧 Torra 自动订阅",
    "ENV_SYMEDIA_URL": "旧 Symedia 服务地址",
    "ENV_SYMEDIA_TOKEN": "旧 Symedia Token",
    "ENV_SYMEDIA_USERNAME": "旧 Symedia 用户名",
    "ENV_SYMEDIA_PASSWORD": "旧 Symedia 密码",
    "ENV_SYMEDIA_CHANNEL_TYPE": "旧 Symedia 频道类型",
    "ENV_SYMEDIA_CHANNEL_IDS": "旧 Symedia 频道 ID",
    "ENV_SYMEDIA_PARENT_ID": "旧 Symedia 父目录 ID",
    "ENV_SYMEDIA_RULE_ID": "旧 Symedia 整理规则 ID",
    "ENV_SYMEDIA_AUTO_SUBSCRIBE": "启用旧 Symedia 自动订阅",
    "ENV_HDHIVE_CHECKIN_ENABLED": "启用 HDHive 签到",
    "ENV_HDHIVE_CHECKIN_GAMBLER": "启用 HDHive 签到抽奖",
    "ENV_HDHIVE_CHECKIN_NOTIFY": "启用 HDHive 签到通知",
    "ENV_HDHIVE_UNLOCK_POINTS_LIMIT": "HDHive 解锁积分上限",
    "ENV_HDHIVE_UNLOCK_RATE_LIMIT": "HDHive 解锁频率上限",
    "ENV_HDHIVE_EXPIRY_REMINDER": "启用 HDHive 到期提醒",
    "ENV_HDHIVE_REMINDER_INTERVAL_HOURS": "HDHive 提醒间隔（小时）",
    "ENV_PROXY": "外部请求代理",
}

FIELD_DESCRIPTIONS = {
    "MCC_SUBSCRIPTION_SCHEDULER_ENABLED": "定时运行 Fluxa 的订阅扫描任务；关闭后仍可手动执行。",
    "MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED": "定时刷新已镜像到 Fluxa 的 Torra 订阅状态，不会删除 Torra 订阅。",
    "NASEMBY_CORE_WRITE_ENABLED": "允许新增、修改和运行 Fluxa 订阅；只读查看时可关闭。",
    "MCC_PRIVATE_RSS_ENABLED": "允许添加私人 RSS 来源并采集种子；修改后需要重启。",
    "MCC_TORRA_QUALITY_WATCH_ENABLED": "观察 Torra 已下载版本并生成质量升级建议；修改后需要重启。",
    "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "允许人工确认后下载质量升级候选，关闭时仅分析。",
    "MCC_MOVIEPILOT_BACKUP_ENABLED": "Torra 主链无结果时允许使用 MoviePilot 备用通道。",
    "MCC_PRESERVED_CORE_API_ENABLED": "保留旧 NasEmby 接口兼容能力，新部署通常保持关闭。",
    "MCC_INTEGRATION_PROBE_ENABLED": "允许主动探测已配置服务的连通性。",
    "MCC_INTEGRATION_MANAGEMENT_ENABLED": "允许在 Fluxa 内执行外部服务管理操作。",
    "MCC_TELEGRAM_MANAGEMENT_ENABLED": "允许执行 Telegram 管理和账号相关操作。",
    "MCC_HDHIVE_MANAGEMENT_ENABLED": "允许执行 HDHive 签到、积分等管理操作。",
    "MCC_CLOUD_SEARCH_ENABLED": "允许从已配置的云盘与频道来源搜索资源。",
    "MCC_CLOUD_TRANSFER_ENABLED": "允许把资源转存到已配置的云盘目录。",
    "EMBY_BASE_URL": "Fluxa 当前使用的 Emby 服务地址。",
    "EMBY_API_KEY": "优先使用的 Emby API 凭据；配置后通常无需用户名密码。",
    "EMBY_USER_ID": "用于定位 Emby 用户首页和播放记录，可留空自动解析。",
    "EMBY_USERNAME": "未使用 API Key 时用于登录 Emby。",
    "EMBY_PASSWORD": "与 Emby 用户名配套使用；已保存值不会回显。",
    "QB_BASE_URL": "qBittorrent Web UI 地址。",
    "QB_USERNAME": "qBittorrent Web UI 用户名。",
    "QB_PASSWORD": "qBittorrent Web UI 密码；已保存值不会回显。",
    "TORRA_BASE_URL": "Fluxa 当前使用的 Torra 服务地址。",
    "TORRA_TOKEN": "优先使用的 Torra 访问令牌。",
    "TORRA_USERNAME": "未使用令牌时用于登录 Torra。",
    "TORRA_PASSWORD": "与 Torra 用户名配套使用；已保存值不会回显。",
    "TORRA_PUSH_ENABLED": "允许把 Fluxa 新建订阅推送到 Torra。",
    "TORRA_DOWNLOAD_ROOT": "Torra 下载任务在主机上的根目录。",
    "TORRA_DOWNLOADER_ID": "Torra 中目标下载器的 ID；单下载器时通常可留空。",
    "SYMEDIA_BASE_URL": "Fluxa 当前使用的 Symedia 服务地址。",
    "SYMEDIA_TOKEN": "优先使用的 Symedia 访问令牌。",
    "SYMEDIA_USERNAME": "未使用令牌时用于登录 Symedia。",
    "SYMEDIA_PASSWORD": "与 Symedia 用户名配套使用；已保存值不会回显。",
    "TMDB_API_KEY": "用于发现页、榜单和影视资料查询。",
    "TMDB_API_TOKEN": "TMDB Bearer Token；与 API Key 配置一种即可。",
    "ENV_115_COOKIES": "115 账号 Cookie，只有启用 115 搜索或转存时才需要。",
    "ENV_115_LINK_UPLOAD_PID": "旧版链接转存流程使用的 115 目录 ID；未沿用旧流程时留空。",
    "ENV_115_UPLOAD_PID": "旧版通用上传目录 ID；当前主目录优先使用“115 主上传目录 ID”。",
    "ENV_UPLOAD_PID": "当前 115 转存主目录 ID；使用云盘转存时填写。",
    "ENV_115_TGMONITOR_SWITCH": "旧版 Telegram 频道监控转存开关，新部署通常保持关闭。",
    "ENV_115_TG_CHANNEL": "旧版 Telegram 监控流程读取的频道标识。",
    "ENV_CHECK_INTERVAL": "旧自动化流程的轮询间隔；未启用旧流程时保持默认。",
    "ENV_SUBSCRIPTION_SEARCH_INTERVAL": "旧订阅自动搜索流程的间隔；Fluxa 调度器不依赖此字段。",
    "ENV_TG_PHONE": "Telegram 用户账号登录手机号，不使用账号会话时可留空。",
    "ENV_TG_API_ID": "Telegram 用户账号 API ID，与手机号和 API Hash 配套使用。",
    "ENV_TG_API_HASH": "Telegram 用户账号 API Hash；已保存值不会回显。",
    "ENV_TG_CHANNELS": "允许作为资源来源的 Telegram 频道列表。",
    "ENV_115_CLEAN_PID": "旧版自动清理流程使用的 115 目录 ID，多个值沿用旧格式填写。",
    "ENV_115_TRASH_PASSWORD": "旧版 115 清理流程访问回收站时使用的密码。",
    "ENV_TG_BOT_TOKEN": "仅用于 Bot 通知，不用于 Telegram 用户账号登录。",
    "ENV_TG_ADMIN_USER_ID": "允许接收管理通知的 Telegram 用户 ID。",
    "ENV_TG_TRANSFER_NOTIFY_ENABLED": "云盘转存完成后发送 Telegram 通知。",
    "ENV_TG_TRANSFER_NOTIFY_CHAT_IDS": "接收转存通知的会话 ID，多个值按旧配置格式填写。",
    "ENV_TG_TRANSFER_NOTIFY_WHITELIST": "只通知匹配白名单的转存任务。",
    "ENV_TG_TRANSFER_NOTIFY_BLACKLIST": "不通知匹配黑名单的转存任务。",
    "ENV_TG_TRANSFER_NOTIFY_TEMPLATE": "旧版转存通知文本模板，留空使用默认模板。",
    "ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED": "订阅状态变化时发送 Telegram 通知。",
    "ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE": "旧版订阅通知文本模板，留空使用默认模板。",
    "ENV_PTTO115_SWITCH": "旧版 PT 下载后转存 115 的开关，新部署通常保持关闭。",
    "ENV_PTTO115_UPLOAD_PID": "旧版 PT 转存 115 的目标目录 ID。",
    "ENV_PTTO123_SWITCH": "旧版 PT 下载后转存 123 云盘的开关，新部署通常保持关闭。",
    "ENV_PTTO123_UPLOAD_PID": "旧版 PT 转存 123 云盘的目标目录 ID。",
    "ENV_123_CLIENT_ID": "旧版 123 云盘转存流程的客户端 ID。",
    "ENV_123_CLIENT_SECRET": "旧版 123 云盘转存流程的客户端密钥。",
    "ENV_EMBY_SERVER_URL": "旧 NasEmby 使用的 Emby 地址；当前请优先填写“Emby 服务地址”。",
    "ENV_EMBY_API_KEY": "旧 NasEmby 使用的 Emby API Key；当前请优先填写同名常用配置。",
    "ENV_MEDIA_LIBRARY_ADMIN": "旧媒体库登录账号别名；当前请优先填写 Emby 用户名。",
    "ENV_MEDIA_LIBRARY_PASSWORD": "旧媒体库登录密码别名；当前请优先填写 Emby 密码。",
    "ENV_MEDIA_SYNC_CATEGORIES": "旧媒体库同步流程限定的分类列表。",
    "ENV_115_AUTO_CLASSIFY": "旧版 115 自动分类开关，新部署通常保持关闭。",
    "ENV_115_CLASSIFY_OVERWRITE": "旧版自动分类是否覆盖已有分类结果。",
    "ENV_115_CATEGORY_ROOT": "旧版自动分类流程使用的 115 根目录 ID。",
    "ENV_MOVIEPILOT_URL": "MoviePilot 备用通道服务地址。",
    "ENV_MOVIEPILOT_API_TOKEN": "MoviePilot API Token。",
    "ENV_MOVIEPILOT_USERNAME": "MoviePilot 用户名；Token 可用时通常无需填写。",
    "ENV_MOVIEPILOT_AUTO_SUBSCRIBE": "旧自动订阅流程开关，Fluxa 主链通常保持关闭。",
    "ENV_TORRA_URL": "旧 NasEmby 自动订阅使用的 Torra 地址；当前请优先填写“Torra 地址”。",
    "ENV_TORRA_TOKEN": "旧 NasEmby 自动订阅使用的 Torra Token。",
    "ENV_TORRA_AUTO_SUBSCRIBE": "旧 NasEmby 直接创建 Torra 订阅的开关，Fluxa 主链通常保持关闭。",
    "ENV_SYMEDIA_URL": "旧 NasEmby 自动订阅使用的 Symedia 地址；当前请优先填写“Symedia 地址”。",
    "ENV_SYMEDIA_TOKEN": "旧 NasEmby 自动订阅使用的 Symedia Token。",
    "ENV_SYMEDIA_USERNAME": "旧 NasEmby 自动订阅使用的 Symedia 用户名。",
    "ENV_SYMEDIA_PASSWORD": "旧 NasEmby 自动订阅使用的 Symedia 密码。",
    "ENV_SYMEDIA_CHANNEL_TYPE": "旧 Symedia 自动订阅监听的频道类型，例如 115 频道。",
    "ENV_SYMEDIA_CHANNEL_IDS": "旧 Symedia 自动订阅监听的频道 ID 列表。",
    "ENV_SYMEDIA_PARENT_ID": "旧 Symedia 自动订阅整理到的 115 父目录 ID。",
    "ENV_SYMEDIA_RULE_ID": "旧 Symedia 自动整理流程使用的规则 ID。",
    "ENV_SYMEDIA_AUTO_SUBSCRIBE": "旧 Symedia 自动订阅开关，Fluxa 主链通常保持关闭。",
    "ENV_HDHIVE_CHECKIN_ENABLED": "开启旧 HDHive 自动签到流程。",
    "ENV_HDHIVE_CHECKIN_GAMBLER": "签到后参与旧版积分抽奖流程。",
    "ENV_HDHIVE_CHECKIN_NOTIFY": "签到完成后发送通知。",
    "ENV_HDHIVE_UNLOCK_POINTS_LIMIT": "旧自动解锁流程允许消耗的积分上限。",
    "ENV_HDHIVE_UNLOCK_RATE_LIMIT": "旧自动解锁流程的频率限制。",
    "ENV_HDHIVE_EXPIRY_REMINDER": "开启 HDHive 资源到期提醒。",
    "ENV_HDHIVE_REMINDER_INTERVAL_HOURS": "两次到期提醒之间的最短小时数。",
    "ENV_PROXY": "访问 TMDB、Telegram 等外部服务时使用的 HTTP 或 SOCKS 代理。",
}

ADVANCED_COMPATIBILITY_FIELDS = {
    "ENV_115_LINK_UPLOAD_PID",
    "ENV_115_UPLOAD_PID",
    "ENV_115_TGMONITOR_SWITCH",
    "ENV_115_TG_CHANNEL",
    "ENV_CHECK_INTERVAL",
    "ENV_SUBSCRIPTION_SEARCH_INTERVAL",
    "ENV_115_CLEAN_PID",
    "ENV_115_TRASH_PASSWORD",
    "ENV_PTTO115_SWITCH",
    "ENV_PTTO115_UPLOAD_PID",
    "ENV_PTTO123_SWITCH",
    "ENV_PTTO123_UPLOAD_PID",
    "ENV_123_CLIENT_ID",
    "ENV_123_CLIENT_SECRET",
    "ENV_EMBY_SERVER_URL",
    "ENV_EMBY_API_KEY",
    "ENV_MEDIA_LIBRARY_ADMIN",
    "ENV_MEDIA_LIBRARY_PASSWORD",
    "ENV_MEDIA_SYNC_CATEGORIES",
    "ENV_115_AUTO_CLASSIFY",
    "ENV_115_CLASSIFY_OVERWRITE",
    "ENV_115_CATEGORY_ROOT",
    "ENV_TORRA_URL",
    "ENV_TORRA_TOKEN",
    "ENV_TORRA_AUTO_SUBSCRIBE",
    "ENV_SYMEDIA_URL",
    "ENV_SYMEDIA_TOKEN",
    "ENV_SYMEDIA_USERNAME",
    "ENV_SYMEDIA_PASSWORD",
    "ENV_SYMEDIA_CHANNEL_TYPE",
    "ENV_SYMEDIA_CHANNEL_IDS",
    "ENV_SYMEDIA_PARENT_ID",
    "ENV_SYMEDIA_RULE_ID",
    "ENV_SYMEDIA_AUTO_SUBSCRIBE",
}


GROUP_DEFINITIONS = [
    ("emby", "Emby", "媒体库首页、图片和入库刷新", ("EMBY_", "ENV_EMBY_")),
    ("qbittorrent", "qBittorrent", "下载任务读取与暂停/恢复", ("QB_",)),
    ("torra", "Torra", "PT 订阅、下载器和追更洗版", ("TORRA_", "ENV_TORRA_")),
    ("symedia", "Symedia", "115 整理入库记录", ("SYMEDIA_", "ENV_SYMEDIA_")),
    ("tmdb", "TMDB 与发现", "影视元数据、榜单和日历", ("TMDB_",)),
    ("moviepilot", "MoviePilot", "备用兼容通道", ("MOVIEPILOT_", "ENV_MOVIEPILOT_")),
    ("cloud", "115 与 123 云盘", "云盘账号、主转存目录和当前转存能力", ("ENV_115_", "ENV_123_", "ENV_UPLOAD_")),
    ("telegram", "Telegram", "账号登录、资源频道和通知设置", ("ENV_TG_",)),
    ("hdhive", "HDHive", "签到、积分和到期提醒", ("ENV_HDHIVE_",)),
    ("automation", "自动化与安全开关", "控制写入、采集、调度和外部管理能力", ("MCC_", "NASEMBY_")),
    ("advanced", "高级兼容设置", "旧 NasEmby 工作流字段，通常无需修改", ("ENV_",)),
]


def _group_for_key(key: str):
    if key in ADVANCED_COMPATIBILITY_FIELDS:
        return "advanced", "高级兼容设置", "旧 NasEmby 工作流字段，通常无需修改"
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
    return "兼容配置项"


def _description_for_key(key: str, field_type: str) -> str:
    if key in FIELD_DESCRIPTIONS:
        return FIELD_DESCRIPTIONS[key]
    if key in ADVANCED_COMPATIBILITY_FIELDS:
        return "仅供旧 NasEmby 自动化流程兼容；未沿用旧流程时保持默认或留空。"
    if field_type == "secret":
        return "已保存值不会回显；留空保持原值，重新输入才会修改。"
    if field_type == "boolean":
        return "关闭时不启用此能力。"
    return "未使用对应功能时可留空。"


def _catalog_field(key: str) -> dict:
    field_type = _field_type(key)
    return {
        "key": key,
        "label": _label_for_key(key),
        "type": field_type,
        "secret": field_type == "secret",
        "restartRequired": key in RESTART_REQUIRED,
        "description": _description_for_key(key, field_type),
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
    for key in CONFIG_FIELDS:
        group_id, title, note = _group_for_key(key)
        if group_id not in grouped:
            grouped[group_id] = {"id": group_id, "title": title, "note": note, "fields": []}
        field = _catalog_field(key)
        raw = str(values.get(key) or "")
        if field["secret"]:
            field["value"] = ""
            field["hasValue"] = bool(raw)
        else:
            field["value"] = raw
            field["hasValue"] = bool(raw)
        grouped[group_id]["fields"].append(field)
    group_order = {group_id: index for index, (group_id, *_rest) in enumerate(GROUP_DEFINITIONS)}
    groups = sorted(grouped.values(), key=lambda group: group_order.get(group["id"], len(group_order)))
    return {"success": True, "groups": groups}


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
