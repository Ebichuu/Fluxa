from __future__ import annotations

import os
import threading
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
except ModuleNotFoundError:
    def dotenv_values(path):
        values = {}
        path = Path(path)
        if not path.exists():
            return values
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    def load_dotenv(path, override=False):
        for key, value in dotenv_values(path).items():
            if override or key not in os.environ:
                os.environ[key] = value
        return True


ROOT_DIR = Path(__file__).resolve().parents[1]


def resolve_workspace_env_path(root_dir: Path) -> Path:
    root_dir = Path(root_dir)
    for candidate in (root_dir, *root_dir.parents):
        if (candidate / "package.json").is_file():
            return candidate / ".env"
    return root_dir / ".env"


WORKSPACE_ENV_PATH = resolve_workspace_env_path(ROOT_DIR)
DATA_DIR = ROOT_DIR / "data"
USER_ENV_PATH = DATA_DIR / "user.env"
LEGACY_DB_DIR = ROOT_DIR / "db"
AUTH_DB_PATH = LEGACY_DB_DIR / "auth.sqlite3"
LEGACY_USER_ENV_PATH = LEGACY_DB_DIR / "user.env"
SYS_ENV_PATH = ROOT_DIR / "sys.env"
CONFIG_WRITE_LOCK = threading.RLock()

CONFIG_FIELDS = [
    # Compose-provided application settings that are editable from the admin UI.
    "MCC_SUBSCRIPTION_SCHEDULER_ENABLED",
    "NASEMBY_CORE_WRITE_ENABLED",
    "MCC_PRIVATE_RSS_ENABLED",
    "MCC_TORRA_QUALITY_WATCH_ENABLED",
    "MCC_TORRA_REWASH_DOWNLOAD_ENABLED",
    "MCC_MOVIEPILOT_BACKUP_ENABLED",
    "MCC_PRESERVED_CORE_API_ENABLED",
    "MCC_INTEGRATION_PROBE_ENABLED",
    "MCC_INTEGRATION_MANAGEMENT_ENABLED",
    "MCC_TELEGRAM_MANAGEMENT_ENABLED",
    "MCC_HDHIVE_MANAGEMENT_ENABLED",
    "MCC_CLOUD_SEARCH_ENABLED",
    "MCC_CLOUD_TRANSFER_ENABLED",
    "EMBY_BASE_URL",
    "EMBY_API_KEY",
    "EMBY_USER_ID",
    "EMBY_USERNAME",
    "EMBY_PASSWORD",
    "QB_BASE_URL",
    "QB_USERNAME",
    "QB_PASSWORD",
    "TORRA_BASE_URL",
    "TORRA_TOKEN",
    "TORRA_USERNAME",
    "TORRA_PASSWORD",
    "TORRA_PUSH_ENABLED",
    "TORRA_DOWNLOAD_ROOT",
    "TORRA_DOWNLOADER_ID",
    "SYMEDIA_BASE_URL",
    "SYMEDIA_TOKEN",
    "SYMEDIA_USERNAME",
    "SYMEDIA_PASSWORD",
    "TMDB_API_KEY",
    "TMDB_API_TOKEN",
    "ENV_115_COOKIES",
    "ENV_115_LINK_UPLOAD_PID",
    "ENV_115_UPLOAD_PID",
    "ENV_UPLOAD_PID",
    "ENV_115_TGMONITOR_SWITCH",
    "ENV_115_TG_CHANNEL",
    "ENV_CHECK_INTERVAL",
    "ENV_SUBSCRIPTION_SEARCH_INTERVAL",
    "ENV_TG_PHONE",
    "ENV_TG_API_ID",
    "ENV_TG_API_HASH",
    "ENV_TG_CHANNELS",
    "ENV_115_CLEAN_PID",
    "ENV_115_TRASH_PASSWORD",
    "ENV_TG_BOT_TOKEN",
    "ENV_TG_ADMIN_USER_ID",
    "ENV_TG_TRANSFER_NOTIFY_ENABLED",
    "ENV_TG_TRANSFER_NOTIFY_CHAT_IDS",
    "ENV_TG_TRANSFER_NOTIFY_WHITELIST",
    "ENV_TG_TRANSFER_NOTIFY_BLACKLIST",
    "ENV_TG_TRANSFER_NOTIFY_TEMPLATE",
    "ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED",
    "ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE",
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
    "ENV_MOVIEPILOT_URL",
    "ENV_MOVIEPILOT_API_TOKEN",
    "ENV_MOVIEPILOT_USERNAME",
    "ENV_MOVIEPILOT_AUTO_SUBSCRIBE",
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
    "ENV_HDHIVE_CHECKIN_ENABLED",
    "ENV_HDHIVE_CHECKIN_GAMBLER",
    "ENV_HDHIVE_CHECKIN_NOTIFY",
    "ENV_HDHIVE_UNLOCK_POINTS_LIMIT",
    "ENV_HDHIVE_UNLOCK_RATE_LIMIT",
    "ENV_HDHIVE_EXPIRY_REMINDER",
    "ENV_HDHIVE_REMINDER_INTERVAL_HOURS",
    "ENV_PROXY",
]

DEFAULT_CONFIG = {
    "MCC_SUBSCRIPTION_SCHEDULER_ENABLED": "false",
    "NASEMBY_CORE_WRITE_ENABLED": "false",
    "MCC_PRIVATE_RSS_ENABLED": "false",
    "MCC_TORRA_QUALITY_WATCH_ENABLED": "false",
    "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "false",
    "MCC_MOVIEPILOT_BACKUP_ENABLED": "false",
    "MCC_PRESERVED_CORE_API_ENABLED": "false",
    "MCC_INTEGRATION_PROBE_ENABLED": "false",
    "MCC_INTEGRATION_MANAGEMENT_ENABLED": "false",
    "MCC_TELEGRAM_MANAGEMENT_ENABLED": "false",
    "MCC_HDHIVE_MANAGEMENT_ENABLED": "false",
    "MCC_CLOUD_SEARCH_ENABLED": "false",
    "MCC_CLOUD_TRANSFER_ENABLED": "false",
    "EMBY_BASE_URL": "",
    "EMBY_API_KEY": "",
    "EMBY_USER_ID": "",
    "EMBY_USERNAME": "",
    "EMBY_PASSWORD": "",
    "QB_BASE_URL": "",
    "QB_USERNAME": "",
    "QB_PASSWORD": "",
    "TORRA_BASE_URL": "",
    "TORRA_TOKEN": "",
    "TORRA_USERNAME": "",
    "TORRA_PASSWORD": "",
    "TORRA_PUSH_ENABLED": "false",
    "TORRA_DOWNLOAD_ROOT": "",
    "TORRA_DOWNLOADER_ID": "",
    "SYMEDIA_BASE_URL": "",
    "SYMEDIA_TOKEN": "",
    "SYMEDIA_USERNAME": "",
    "SYMEDIA_PASSWORD": "",
    "TMDB_API_KEY": "",
    "TMDB_API_TOKEN": "",
    "ENV_115_COOKIES": "",
    "ENV_115_LINK_UPLOAD_PID": "0",
    "ENV_115_UPLOAD_PID": "0",
    "ENV_UPLOAD_PID": "0",
    "ENV_115_TGMONITOR_SWITCH": "0",
    "ENV_115_TG_CHANNEL": "",
    "ENV_CHECK_INTERVAL": "5",
    "ENV_SUBSCRIPTION_SEARCH_INTERVAL": "5",
    "ENV_TG_PHONE": "",
    "ENV_TG_API_ID": "",
    "ENV_TG_API_HASH": "",
    "ENV_TG_CHANNELS": "[]",
    "ENV_115_CLEAN_PID": "0,0",
    "ENV_115_TRASH_PASSWORD": "0",
    "ENV_TG_BOT_TOKEN": "",
    "ENV_TG_ADMIN_USER_ID": "0",
    "ENV_TG_TRANSFER_NOTIFY_ENABLED": "0",
    "ENV_TG_TRANSFER_NOTIFY_CHAT_IDS": "",
    "ENV_TG_TRANSFER_NOTIFY_WHITELIST": "",
    "ENV_TG_TRANSFER_NOTIFY_BLACKLIST": "",
    "ENV_TG_TRANSFER_NOTIFY_TEMPLATE": "",
    "ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED": "0",
    "ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE": "",
    "ENV_PTTO115_SWITCH": "0",
    "ENV_PTTO115_UPLOAD_PID": "0",
    "ENV_PTTO123_SWITCH": "0",
    "ENV_PTTO123_UPLOAD_PID": "0",
    "ENV_123_CLIENT_ID": "",
    "ENV_123_CLIENT_SECRET": "",
    "ENV_EMBY_SERVER_URL": "http://127.0.0.1:8096",
    "ENV_EMBY_API_KEY": "",
    "ENV_MEDIA_LIBRARY_ADMIN": "",
    "ENV_MEDIA_LIBRARY_PASSWORD": "",
    "ENV_MEDIA_SYNC_CATEGORIES": "anime_movie,hk_movie,hk_tv,cn_movie,cn_tv,anime,documentary,variety",
    "ENV_115_AUTO_CLASSIFY": "0",
    "ENV_115_CLASSIFY_OVERWRITE": "0",
    "ENV_115_CATEGORY_ROOT": "/归档整理",
    "ENV_MOVIEPILOT_URL": "",
    "ENV_MOVIEPILOT_API_TOKEN": "",
    "ENV_MOVIEPILOT_USERNAME": "NasEmby",
    "ENV_MOVIEPILOT_AUTO_SUBSCRIBE": "0",
    "ENV_TORRA_URL": "",
    "ENV_TORRA_TOKEN": "",
    "ENV_TORRA_AUTO_SUBSCRIBE": "0",
    "ENV_SYMEDIA_URL": "",
    "ENV_SYMEDIA_TOKEN": "",
    "ENV_SYMEDIA_USERNAME": "",
    "ENV_SYMEDIA_PASSWORD": "",
    "ENV_SYMEDIA_CHANNEL_TYPE": "channel_115",
    "ENV_SYMEDIA_CHANNEL_IDS": "",
    "ENV_SYMEDIA_PARENT_ID": "",
    "ENV_SYMEDIA_RULE_ID": "",
    "ENV_SYMEDIA_AUTO_SUBSCRIBE": "0",
    "ENV_HDHIVE_CHECKIN_ENABLED": "0",
    "ENV_HDHIVE_CHECKIN_GAMBLER": "0",
    "ENV_HDHIVE_CHECKIN_NOTIFY": "1",
    "ENV_HDHIVE_UNLOCK_POINTS_LIMIT": "100",
    "ENV_HDHIVE_UNLOCK_RATE_LIMIT": "3",
    "ENV_HDHIVE_EXPIRY_REMINDER": "1",
    "ENV_HDHIVE_REMINDER_INTERVAL_HOURS": "6",
    "ENV_PROXY": "",
}

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
PRESERVE_EMPTY_FIELDS = {
    "EMBY_API_KEY",
    "EMBY_PASSWORD",
    "QB_PASSWORD",
    "TORRA_TOKEN",
    "TORRA_PASSWORD",
    "SYMEDIA_TOKEN",
    "SYMEDIA_PASSWORD",
    "TMDB_API_KEY",
    "TMDB_API_TOKEN",
    "ENV_115_COOKIES",
    "ENV_115_TRASH_PASSWORD",
    "ENV_123_CLIENT_SECRET",
    "ENV_EMBY_API_KEY",
    "ENV_MEDIA_LIBRARY_PASSWORD",
    "ENV_MOVIEPILOT_API_TOKEN",
    "ENV_TG_API_HASH",
    "ENV_TG_BOT_TOKEN",
    "ENV_TORRA_TOKEN",
    "ENV_SYMEDIA_PASSWORD",
    "ENV_SYMEDIA_TOKEN",
}


def apply_proxy_env(proxy: str) -> None:
    value = str(proxy or "").strip()
    for key in PROXY_ENV_KEYS:
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def load_runtime_env() -> None:
    # Local development keeps the shared environment file at the workspace root;
    # deployment containers may still provide a service-local .env.
    load_dotenv(WORKSPACE_ENV_PATH, override=False)
    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(USER_ENV_PATH, override=True)
    load_dotenv(LEGACY_USER_ENV_PATH, override=True)
    load_dotenv(SYS_ENV_PATH, override=True)
    apply_proxy_env(os.getenv("ENV_PROXY", ""))


def read_config() -> dict[str, str]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEGACY_DB_DIR.mkdir(parents=True, exist_ok=True)
    load_runtime_env()
    values = dict(DEFAULT_CONFIG)
    if USER_ENV_PATH.exists():
        values.update({k: str(v or "") for k, v in dotenv_values(USER_ENV_PATH).items() if k in CONFIG_FIELDS})
    values.update({k: os.getenv(k, values.get(k, "")) for k in CONFIG_FIELDS})
    _sync_115_directory_values(values)
    return values


def _sync_115_directory_values(values: dict[str, str]) -> None:
    unified = str(
        values.get("ENV_UPLOAD_PID")
        or values.get("ENV_115_LINK_UPLOAD_PID")
        or values.get("ENV_115_UPLOAD_PID")
        or "0"
    )
    values["ENV_UPLOAD_PID"] = unified
    values["ENV_115_LINK_UPLOAD_PID"] = unified
    values["ENV_115_UPLOAD_PID"] = unified


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_config(payload: dict[str, object], clear_fields: set[str] | None = None) -> dict[str, str]:
    with CONFIG_WRITE_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        values = dict(DEFAULT_CONFIG)
        current = read_config()
        clear_fields = set(clear_fields or ())
        values.update({k: current.get(k, "") for k in CONFIG_FIELDS})
        for key in CONFIG_FIELDS:
            if key in payload:
                next_value = str(payload.get(key) or "")
                if key in PRESERVE_EMPTY_FIELDS and not next_value and current.get(key) and key not in clear_fields:
                    continue
                values[key] = next_value
        _sync_115_directory_values(values)
        apply_proxy_env(values.get("ENV_PROXY", ""))
        lines = ["# Generated by NasEmby\n"]
        for key in CONFIG_FIELDS:
            value = values.get(key, "").replace("\n", " ").replace("\r", " ")
            lines.append(f"{key}={value}\n")
            os.environ[key] = value
        content = "".join(lines)
        _atomic_write(USER_ENV_PATH, content)
        _atomic_write(LEGACY_USER_ENV_PATH, content)
        return values
