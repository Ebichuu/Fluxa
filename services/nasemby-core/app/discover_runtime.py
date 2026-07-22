import html as html_lib
import asyncio
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from importlib.machinery import SourcelessFileLoader
from pathlib import Path

from app.activity_log import write_activity
from app.sqlite_runtime import resolve_database_path
from app.subscription_migration import migrate_legacy_subscription_files
from app.subscription_repository import SubscriptionRepository


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = Path(__file__).resolve().parent / "hdhive"
for runtime_path in (str(PROJECT_ROOT), str(RUNTIME_DIR)):
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)

BEIJING_TZ = timezone(timedelta(hours=8))
SUBSCRIPTION_RESOURCE_TASK_LOCK = threading.Lock()
SUBSCRIPTION_RESOURCE_TASK_KEYS = set()
SUBSCRIPTION_POLL_STATE = {
    "channel_mode_last_ts": 0.0,
    "subscription_search_last_ts": 0.0,
}


def beijing_now_text(fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.now(BEIJING_TZ).strftime(fmt)

TMDB_GENRES_MOVIE = {
    "冒险": 12,
    "奇幻": 14,
    "动画": 16,
    "剧情": 18,
    "恐怖": 27,
    "动作": 28,
    "喜剧": 35,
    "历史": 36,
    "西部": 37,
    "惊悚": 53,
    "犯罪": 80,
    "纪录片": 99,
    "科幻": 878,
    "悬疑": 9648,
    "音乐": 10402,
    "爱情": 10749,
    "家庭": 10751,
    "战争": 10752,
}

TMDB_GENRES_TV = {
    "冒险": 10759,
    "奇幻": 10765,
    "动画": 16,
    "剧情": 18,
    "动作": 10759,
    "喜剧": 35,
    "犯罪": 80,
    "纪录片": 99,
    "科幻": 10765,
    "悬疑": 9648,
    "家庭": 10751,
    "战争": 10768,
    "西部": 37,
}

LANGUAGES = {
    "中文": "zh",
    "英语": "en",
    "日语": "ja",
    "韩语": "ko",
    "法语": "fr",
    "德语": "de",
    "西语": "es",
    "意语": "it",
    "俄语": "ru",
    "葡语": "pt",
    "阿语": "ar",
    "印地语": "hi",
    "泰语": "th",
}

SORTS = {
    "热度降序": "popularity.desc",
    "热度升序": "popularity.asc",
    "评分最高": "vote_average.desc",
    "评分最低": "vote_average.asc",
}

STREAMING_PROVIDERS = {
    "netflix": {"id": "8", "label": "Netflix"},
    "disney": {"id": "337", "label": "Disney+"},
    "max": {"id": "1899", "label": "HBO Max"},
    "prime": {"id": "9", "label": "Prime Video"},
    "apple": {"id": "350", "label": "Apple TV+"},
    "hulu": {"id": "15", "label": "Hulu"},
    "paramount": {"id": "2303|2616", "label": "Paramount+"},
    "peacock": {"id": "386", "label": "Peacock"},
}

PLATFORM_HOT_SOURCES = {
    "\u5168\u90e8": "all",
    "\u7231\u5947\u827a": "iqiyi",
    "\u4f18\u9177": "youku",
    "\u817e\u8baf": "tencent",
    "\u817e\u8baf\u89c6\u9891": "tencent",
    "\u8292\u679c": "mango",
    "\u8292\u679cTV": "mango",
}

DOUBAN_CACHE = {"time": 0, "items": []}
TMDB_CONFIG = None
PANSOU_MODULE = None
HDHIVE_MODULE = None
SUBSCRIPTION_CONFIG_PATH = str(PROJECT_ROOT / "db" / "discover_subscriptions.json")
SUBSCRIPTION_ITEMS_PATH = str(PROJECT_ROOT / "db" / "discover_subscription_items.json")
SUBSCRIPTION_DETAIL_CACHE_PATH = str(PROJECT_ROOT / "db" / "discover_subscription_detail_cache.json")
SUBSCRIPTION_DETAIL_CACHE_VERSION = 3
DISCOVER_CACHE_DB_PATH = str(PROJECT_ROOT / "db" / "discover_cache.db")
DISCOVER_CACHE_TTL_SECONDS = 6 * 60 * 60
DISCOVER_PERMANENT_CACHE_SECONDS = 20 * 365 * 24 * 60 * 60
DISCOVER_PRELOAD_PAGES = 3
EMBY_LIBRARY_INDEX_TTL_SECONDS = 5 * 60
EMBY_LIBRARY_INDEX_CACHE = {"time": 0, "data": None}
_SUBSCRIPTION_REPOSITORIES = {}
_SUBSCRIPTION_REPOSITORIES_LOCK = threading.Lock()
PLATFORM_YEAR_OVERRIDES = {
    "昆仑神宫": "2022",
}
SUBSCRIPTION_TMDB_TITLE_OVERRIDES = {
    "凡人修仙传": "106449",
    "诡秘之主": "232230",
}

IMAGE_PROXY_HOSTS = (
    "image.tmdb.org",
    "img1.doubanio.com",
    "img2.doubanio.com",
    "img3.doubanio.com",
    "img9.doubanio.com",
    "iqiyipic.com",
    "m.iqiyipic.com",
    "qpic.cn",
    "ykimg.com",
    "alicdn.com",
)


def subscription_database_path():
    config_path = Path(SUBSCRIPTION_CONFIG_PATH)
    items_path = Path(SUBSCRIPTION_ITEMS_PATH)
    default_parent = Path(PROJECT_ROOT) / "db"
    if config_path.parent == items_path.parent:
        legacy_path = config_path
    elif config_path.parent != default_parent:
        legacy_path = config_path
    elif items_path.parent != default_parent:
        legacy_path = items_path
    else:
        legacy_path = config_path
    return resolve_database_path(PROJECT_ROOT, legacy_path=legacy_path)


def subscription_repository():
    database_path = subscription_database_path().resolve()
    key = str(database_path)
    with _SUBSCRIPTION_REPOSITORIES_LOCK:
        repository = _SUBSCRIPTION_REPOSITORIES.get(key)
        if repository is None:
            repository = SubscriptionRepository(database_path)
            migrate_legacy_subscription_files(
                repository,
                SUBSCRIPTION_CONFIG_PATH,
                SUBSCRIPTION_ITEMS_PATH,
                get_subscription_item_key,
            )
            _SUBSCRIPTION_REPOSITORIES[key] = repository
    return repository

LEGACY_DEFAULT_SUBSCRIPTION_SOURCES = [
    "hot_movie", "movie_realtime", "hot_tv", "tv_realtime", "global_tv",
    "showing", "domestic_tv", "japanese_tv", "korean_tv", "american_tv", "anime_tv",
]
DEFAULT_SUBSCRIPTION_SOURCES = [
    "hot_movie", "movie_realtime", "hot_tv", "tv_realtime", "global_tv", "daily_airing",
    "showing", "domestic_tv", "japanese_tv", "korean_tv", "american_tv", "anime_tv",
]

DEFAULT_SUBSCRIPTION_CONFIG = {
    "mode": "torra",
    "torra_quality_watch_enabled": False,
    "torra_quality_default_window_hours": 48,
    "torra_quality_schedule_json": None,
    "torra_quality_min_interval_minutes": 60,
    "torra_quality_hourly_limit": 4,
    "torra_quality_daily_limit": 30,
    "torra_quality_scheduler_batch_size": 2,
    "douban": {
        "enabled": True,
        "movie_enabled": True,
        "tv_enabled": True,
        "movie_years": ["2026", "2025", "2024"],
        "tv_min_rating": 0.0,
        "exclude_titles": [],
        "sources": list(DEFAULT_SUBSCRIPTION_SOURCES),
        "daily_only": False,
        "task_time": "08:30",
        "task_enabled": True,
        "updated_at": "",
        "last_run_at": "",
    },
}

DEFAULT_CLOUD_ACQUISITION = {
    "enabled": False,
    "auto_fallback_enabled": False,
    "manual_actions_enabled": False,
    "wait_minutes": 360,
    "sources": ["telegram", "hdhive"],
    "auto_select": False,
    "policy_version": 1,
}

RESOURCE_RULE_GROUPS = {
    "resolution": {"field": "resolution", "mode": "choice"},
    "color": {"field": "color", "mode": "choice"},
    "audio": {"field": "audio_codec", "mode": "choice"},
    "extension": {"field": "file_extension", "mode": "choice"},
    "size": {"field": "size", "mode": "choice"},
    "keyword": {"field": "keyword", "mode": "text"},
    "exclude_keyword": {"field": "exclude_keyword", "mode": "text"},
}

DEFAULT_RESOURCE_RULES = {
    "enabled": False,
    "auto_transfer": True,
    "max_per_run": 8,
    "groups": {
        "resolution": {"require": ["4k"], "reject": []},
        "color": {"require": ["dv"], "reject": []},
        "audio": {"require": [], "reject": []},
        "extension": {"require": ["mkv"], "reject": []},
        "size": {"require": [], "reject": []},
        "keyword": {"require": [], "reject": []},
        "exclude_keyword": {"require": [], "reject": []},
    },
}

SUBSCRIPTION_MODE_LABELS = {
    "moviepilot": "模式1 MoviePilot",
    "torra": "模式2 Torra",
    "resource": "模式3 资源转存",
    "resource_then_pt": "模式4 资源优先，PT兜底",
    "symedia": "模式5 Symedia",
}

SUBSCRIPTION_MODE_TASK_LABELS = {
    "moviepilot": "MoviePilot 推送",
    "torra": "Torra 推送",
    "resource": "精准资源搜索",
    "resource_then_pt": "资源优先，PT兜底",
    "symedia": "Symedia 推送",
}

SUBSCRIPTION_MODE_ALIASES = {
    "1": "moviepilot",
    "mode1": "moviepilot",
    "moviepilot": "moviepilot",
    "mp": "moviepilot",
    "2": "torra",
    "mode2": "torra",
    "torra": "torra",
    "tr": "torra",
    "3": "resource",
    "mode3": "resource",
    "resource": "resource",
    "transfer": "resource",
    "resource_transfer": "resource",
    "4": "resource_then_pt",
    "mode4": "resource_then_pt",
    "resource_then_pt": "resource_then_pt",
    "resource_pt": "resource_then_pt",
    "pt_fallback": "resource_then_pt",
    "5": "symedia",
    "mode5": "symedia",
    "symedia": "symedia",
    "sy": "symedia",
}

DOUBAN_SUBSCRIPTION_SOURCES = {
    "hot_movie": {"label": "\u70ed\u95e8\u7535\u5f71", "media_type": "movie", "tag": "\u70ed\u95e8", "sort": "recommend"},
    "movie_realtime": {"label": "\u7535\u5f71\u5b9e\u65f6\u70ed\u699c", "media_type": "movie", "tag": "\u70ed\u95e8", "sort": "time"},
    "showing": {"label": "\u6b63\u5728\u4e0a\u6620", "media_type": "movie", "tag": "\u6b63\u5728\u4e0a\u6620", "sort": "recommend"},
    "hot_tv": {"label": "\u70ed\u95e8\u5267\u96c6", "media_type": "tv", "tag": "\u70ed\u95e8", "sort": "recommend"},
    "tv_realtime": {"label": "\u5267\u96c6\u5b9e\u65f6\u70ed\u699c", "media_type": "tv", "tag": "\u70ed\u95e8", "sort": "time"},
    "global_tv": {"label": "\u5168\u7403\u5267\u699c", "media_type": "tv", "tag": "\u82f1\u7f8e\u5267", "sort": "recommend"},
    "domestic_tv": {"label": "\u56fd\u4ea7\u5267\u699c", "media_type": "tv", "tag": "\u56fd\u4ea7\u5267", "sort": "recommend"},
    "japanese_tv": {"label": "\u65e5\u5267\u699c", "media_type": "tv", "tag": "\u65e5\u5267", "sort": "recommend"},
    "korean_tv": {"label": "\u97e9\u5267\u699c", "media_type": "tv", "tag": "\u97e9\u5267", "sort": "recommend"},
    "american_tv": {"label": "\u7f8e\u5267\u699c", "media_type": "tv", "tag": "\u7f8e\u5267", "sort": "recommend"},
    "anime_tv": {"label": "\u52a8\u753b\u5267\u699c", "media_type": "tv", "tag": "\u52a8\u753b", "sort": "recommend"},
}

PLATFORM_SUBSCRIPTION_SOURCES = {
    "platform_tencent": {"label": "\u817e\u8baf\u89c6\u9891\u70ed\u66f4", "media_type": "tv", "platform": "\u817e\u8baf\u89c6\u9891"},
    "platform_youku": {"label": "\u4f18\u9177\u70ed\u66f4", "media_type": "tv", "platform": "\u4f18\u9177"},
    "platform_iqiyi": {"label": "\u7231\u5947\u827a\u70ed\u66f4", "media_type": "tv", "platform": "\u7231\u5947\u827a"},
    "platform_mango": {"label": "\u8292\u679c\u70ed\u66f4", "media_type": "tv", "platform": "\u8292\u679c"},
}

DAILY_AIRING_SUBSCRIPTION_SOURCES = {
    "daily_airing": {"label": "\u5168\u7403\u65e5\u64ad", "media_type": "tv"},
}

SUBSCRIPTION_SOURCES = {**DOUBAN_SUBSCRIPTION_SOURCES, **PLATFORM_SUBSCRIPTION_SOURCES, **DAILY_AIRING_SUBSCRIPTION_SOURCES}


def normalize_subscription_sources(sources):
    raw_sources = sources if isinstance(sources, list) and sources else DEFAULT_SUBSCRIPTION_SOURCES
    selected = []
    for value in raw_sources:
        key = str(value or "").strip()
        if key in SUBSCRIPTION_SOURCES and key not in selected:
            selected.append(key)
    if not selected:
        selected = list(DEFAULT_SUBSCRIPTION_SOURCES)
    selected_set = set(selected)
    if all(key in selected_set for key in LEGACY_DEFAULT_SUBSCRIPTION_SOURCES) and "daily_airing" not in selected_set:
        insert_at = selected.index("global_tv") + 1 if "global_tv" in selected else len(selected)
        selected.insert(insert_at, "daily_airing")
    return selected


def load_tmdb_config():
    global TMDB_CONFIG
    try:
        from app.config import load_runtime_env
        load_runtime_env()
    except Exception:
        pass
    if TMDB_CONFIG is not None and (TMDB_CONFIG.get("api_key") or TMDB_CONFIG.get("api_token")):
        return TMDB_CONFIG
    module = SourcelessFileLoader("tmdb_config", str(RUNTIME_DIR / "tmdb_config.pyc")).load_module()
    api_token = str(os.getenv("TMDB_API_TOKEN") or "").strip()
    TMDB_CONFIG = {
        "api_key": "" if api_token else module.resolve_tmdb_api_key(),
        "api_token": api_token,
        "api_base_url": module.resolve_tmdb_api_base_url().rstrip("/"),
        "image_base_url": module.resolve_tmdb_image_base_url().rstrip("/"),
    }
    return TMDB_CONFIG


def load_pansou_module():
    global PANSOU_MODULE
    if PANSOU_MODULE is None:
        PANSOU_MODULE = SourcelessFileLoader("pansou", str(RUNTIME_DIR / "pansou.pyc")).load_module()
    return PANSOU_MODULE


def load_hdhive_module():
    global HDHIVE_MODULE
    if HDHIVE_MODULE is None:
        HDHIVE_MODULE = SourcelessFileLoader("hdhive_search", str(RUNTIME_DIR / "hdhive_search.pyc")).load_module()
    return HDHIVE_MODULE


def http_json(url, timeout=18):
    headers = {
        "User-Agent": "NasEmby Discover/1.0",
        "Accept": "application/json",
    }
    tmdb_base_url = str((TMDB_CONFIG or {}).get("api_base_url") or "").rstrip("/")
    tmdb_token = str((TMDB_CONFIG or {}).get("api_token") or "").strip()
    if tmdb_token and tmdb_base_url and url.startswith(f"{tmdb_base_url}/"):
        headers["Authorization"] = f"Bearer {tmdb_token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def http_text(url, timeout=18):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://movie.douban.com/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def http_bytes(url, timeout=18):
    host = (urllib.parse.urlparse(normalize_platform_url(url)).netloc or "").lower()
    referer = "https://movie.douban.com/"
    if "iqiyipic.com" in host:
        referer = "https://www.iqiyi.com/"
    elif "qpic.cn" in host:
        referer = "https://v.qq.com/"
    elif "ykimg.com" in host or "alicdn.com" in host:
        referer = "https://www.youku.com/"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": referer,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type") or "image/jpeg"


def read_json_body(handler):
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    body = handler.rfile.read(length).decode("utf-8", "replace")
    return json.loads(body or "{}")


def normalize_resource_rule_token(value):
    return re.sub(r"[^a-z0-9_]+", "", str(value or "").strip().lower())


def normalize_resource_rule_texts(values):
    if isinstance(values, str):
        values = re.split(r"[\n,，;；|]+", values)
    if not isinstance(values, list):
        return []
    rows = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            rows.append(text)
    return rows[:24]


def normalize_resource_rules(payload=None):
    rules = deepcopy(DEFAULT_RESOURCE_RULES)
    source = payload if isinstance(payload, dict) else {}
    rules["enabled"] = bool(source.get("enabled", rules.get("enabled")))
    rules["auto_transfer"] = bool(source.get("auto_transfer", rules.get("auto_transfer", True)))
    try:
        max_per_run = int(float(str(source.get("max_per_run", rules.get("max_per_run", 8))).strip()))
    except Exception:
        max_per_run = 8
    rules["max_per_run"] = max(1, min(50, max_per_run))
    source_groups = source.get("groups") if isinstance(source.get("groups"), dict) else {}
    for group_key, spec in RESOURCE_RULE_GROUPS.items():
        group = source_groups.get(group_key) if isinstance(source_groups.get(group_key), dict) else {}
        if spec.get("mode") == "text":
            rules["groups"][group_key] = {
                "require": normalize_resource_rule_texts(group.get("require")),
                "reject": normalize_resource_rule_texts(group.get("reject")),
            }
            continue
        require = [normalize_resource_rule_token(item) for item in (group.get("require") or []) if normalize_resource_rule_token(item)]
        reject = [normalize_resource_rule_token(item) for item in (group.get("reject") or []) if normalize_resource_rule_token(item)]
        rules["groups"][group_key] = {
            "require": list(dict.fromkeys(require))[:24],
            "reject": [item for item in dict.fromkeys(reject) if item not in require][:24],
        }
    return rules


def normalize_subscription_mode(value):
    key = str(value or "").strip().lower()
    key = re.sub(r"[\s\-]+", "_", key)
    key = re.sub(r"[^a-z0-9_]+", "", key)
    return SUBSCRIPTION_MODE_ALIASES.get(key, "torra")


def subscription_mode_label(mode):
    return SUBSCRIPTION_MODE_LABELS.get(normalize_subscription_mode(mode), SUBSCRIPTION_MODE_LABELS["torra"])


def subscription_mode_task_label(mode):
    return SUBSCRIPTION_MODE_TASK_LABELS.get(normalize_subscription_mode(mode), SUBSCRIPTION_MODE_TASK_LABELS["torra"])


def normalize_cloud_acquisition(payload=None):
    source = payload if isinstance(payload, dict) else {}
    result = deepcopy(DEFAULT_CLOUD_ACQUISITION)
    result["enabled"] = bool(source.get("enabled", result["enabled"]))
    result["auto_fallback_enabled"] = bool(
        source.get("auto_fallback_enabled", result["auto_fallback_enabled"])
    )
    result["manual_actions_enabled"] = bool(
        source.get("manual_actions_enabled", result["manual_actions_enabled"])
    )
    try:
        wait_minutes = int(float(str(source.get("wait_minutes", result["wait_minutes"])).strip()))
    except (TypeError, ValueError):
        wait_minutes = result["wait_minutes"]
    result["wait_minutes"] = max(30, min(10080, wait_minutes))
    allowed_sources = {"telegram", "hdhive", "pansou"}
    sources = source.get("sources") if isinstance(source.get("sources"), list) else result["sources"]
    result["sources"] = list(dict.fromkeys(
        str(item or "").strip().lower()
        for item in sources
        if str(item or "").strip().lower() in allowed_sources
    ))
    result["auto_select"] = bool(source.get("auto_select", result["auto_select"]))
    result["policy_version"] = 1
    if not result["enabled"]:
        result["auto_fallback_enabled"] = False
        result["manual_actions_enabled"] = False
        result["auto_select"] = False
    if not result["auto_fallback_enabled"]:
        result["auto_select"] = False
    return result


def parse_subscription_exclude_titles(value):
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\n,，;；|]+", str(value or ""))
    result = []
    seen = set()
    for item in raw_items:
        text = clean_html(str(item or "")).strip()
        if not text:
            continue
        key = compact_match_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def load_subscription_config():
    config = json.loads(json.dumps(DEFAULT_SUBSCRIPTION_CONFIG, ensure_ascii=False))
    saved_has_mode = False
    saved_has_cloud_policy = False
    saved = subscription_repository().load_config()
    if isinstance(saved, dict):
        saved_has_mode = "mode" in saved or "subscription_mode" in saved
        saved_has_cloud_policy = isinstance(saved.get("cloud_acquisition"), dict)
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
    mode_source = config.get("mode") or config.get("subscription_mode")
    if not saved_has_mode:
        douban = config.get("douban") if isinstance(config.get("douban"), dict) else {}
        legacy_mode = str(douban.get("mode") or "").strip().lower()
        legacy_target = str(douban.get("pt_target") or "").strip().lower()
        if legacy_mode in {"pt_only", "pt"}:
            mode_source = "torra" if legacy_target == "torra" else "moviepilot"
        elif legacy_mode in {"resource_then_pt", "resource_first", "hybrid"}:
            mode_source = "resource_then_pt"
        elif legacy_mode in {"resource_only", "resource"}:
            mode_source = "resource"
    normalized_mode = normalize_subscription_mode(mode_source)
    if not saved_has_cloud_policy and normalized_mode in {"resource", "resource_then_pt"}:
        normalized_mode = "torra"
    config["mode"] = normalized_mode
    config["cloud_acquisition"] = normalize_cloud_acquisition(config.get("cloud_acquisition"))
    config["resource_rules"] = normalize_resource_rules(config.get("resource_rules"))
    douban = config.get("douban") if isinstance(config.get("douban"), dict) else {}
    douban["exclude_titles"] = parse_subscription_exclude_titles(douban.get("exclude_titles"))
    douban["sources"] = normalize_subscription_sources(douban.get("sources"))
    return config


def write_subscription_config_data(config):
    payload = config if isinstance(config, dict) else load_subscription_config()
    subscription_repository().save_config(payload)
    return load_subscription_config()


def subscription_blocked_titles():
    config = load_subscription_config()
    douban = config.get("douban") if isinstance(config, dict) else {}
    return parse_subscription_exclude_titles((douban or {}).get("exclude_titles"))


def set_subscription_blocked_titles(titles):
    config = load_subscription_config()
    douban = config.get("douban") if isinstance(config.get("douban"), dict) else {}
    douban["exclude_titles"] = parse_subscription_exclude_titles(titles)
    douban["updated_at"] = beijing_now_text()
    config["douban"] = douban
    return write_subscription_config_data(config)


def save_subscription_config(payload):
    config = load_subscription_config()
    previous_mode = normalize_subscription_mode(config.get("mode"))
    douban = payload.get("douban") if isinstance(payload, dict) else None
    if not isinstance(douban, dict):
        raise RuntimeError("缺少豆瓣订阅配置")

    sources = douban.get("sources")
    if not isinstance(sources, list):
        sources = []
    sources = normalize_subscription_sources(sources)
    years = douban.get("movie_years")
    if isinstance(years, str):
        years = re.findall(r"(?:19|20)\d{2}", years)
    elif not isinstance(years, list):
        years = []
    years = [str(y).strip() for y in years if re.fullmatch(r"(?:19|20)\d{2}", str(y).strip())]

    task_time = str(douban.get("task_time") or "09:00").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", task_time):
        raise RuntimeError("任务时间格式应为 HH:MM")
    hour, minute = [int(part) for part in task_time.split(":")]
    if hour > 23 or minute > 59:
        raise RuntimeError("任务时间超出范围")

    try:
        tv_min_rating = float(douban.get("tv_min_rating", 7.0))
    except Exception:
        tv_min_rating = 7.0
    tv_min_rating = max(0.0, min(10.0, tv_min_rating))
    exclude_titles = parse_subscription_exclude_titles(douban.get("exclude_titles"))

    config["douban"].update({
        "enabled": bool(douban.get("enabled")),
        "movie_enabled": bool(douban.get("movie_enabled")),
        "tv_enabled": bool(douban.get("tv_enabled")),
        "movie_years": years,
        "tv_min_rating": tv_min_rating,
        "exclude_titles": exclude_titles,
        "sources": sources,
        "daily_only": False,
        "task_time": task_time,
        "task_enabled": bool(douban.get("task_enabled")),
        "updated_at": beijing_now_text(),
    })
    config["mode"] = normalize_subscription_mode(
        payload.get("mode") if isinstance(payload, dict) and "mode" in payload else (
            payload.get("subscription_mode") if isinstance(payload, dict) and "subscription_mode" in payload else config.get("mode")
        )
    )
    next_cloud = (
        payload.get("cloud_acquisition")
        if isinstance(payload, dict) and "cloud_acquisition" in payload
        else config.get("cloud_acquisition")
    )
    config["cloud_acquisition"] = normalize_cloud_acquisition(next_cloud)
    next_rules = payload.get("resource_rules") if isinstance(payload, dict) and "resource_rules" in payload else config.get("resource_rules")
    config["resource_rules"] = normalize_resource_rules(next_rules)
    config = write_subscription_config_data(config)
    current_mode = normalize_subscription_mode(config.get("mode"))
    mode_switch_task = {}
    mode_switch_push = not (isinstance(payload, dict) and payload.get("mode_switch_push") is False)
    if mode_switch_push and current_mode in {"moviepilot", "torra", "symedia"} and previous_mode != current_mode:
        mode_switch_task = queue_subscription_provider_mode_switch(previous_mode, current_mode, config)
        if mode_switch_task:
            config["mode_switch_task"] = mode_switch_task
    write_activity(
        "subscription",
        "save_subscription_config",
        "success",
        "订阅配置已保存",
        enabled=config["douban"].get("enabled"),
        task_enabled=config["douban"].get("task_enabled"),
        movie_enabled=config["douban"].get("movie_enabled"),
        tv_enabled=config["douban"].get("tv_enabled"),
        sources=len(config["douban"].get("sources") or []),
        exclude_titles=len(config["douban"].get("exclude_titles") or []),
        mode=subscription_mode_label(config.get("mode")),
        resource_rules="已启用" if config.get("resource_rules", {}).get("enabled") else "未启用",
        rule=resource_rules_required_summary(config.get("resource_rules") or {}),
    )
    return config


def queue_subscription_provider_mode_switch(previous_mode, current_mode, config):
    data = load_subscription_items()
    items = [item for item in (data.get("items") or []) if isinstance(item, dict)] if isinstance(data, dict) else []
    result = _subscription_task_base(config)
    result.update({
        "mode_switched": True,
        "old_mode": normalize_subscription_mode(previous_mode),
        "new_mode": normalize_subscription_mode(current_mode),
        "old_label": subscription_mode_label(previous_mode),
        "new_label": subscription_mode_label(current_mode),
        "background": True,
    })
    if not items:
        result["enabled"] = False
        result["reason"] = "当前没有可补推的订阅"
        write_activity(
            "push",
            "subscription_mode_switch_queue",
            "skip",
            f"订阅模式已切换到 {subscription_mode_label(current_mode)}，但没有可补推订阅",
            old_mode=subscription_mode_label(previous_mode),
            mode=subscription_mode_label(current_mode),
        )
        return result
    queued = queue_subscription_resource_rule_transfer(items, "mode_switch_provider")
    queued.update({
        "mode_switched": True,
        "old_mode": normalize_subscription_mode(previous_mode),
        "new_mode": normalize_subscription_mode(current_mode),
        "old_label": subscription_mode_label(previous_mode),
        "new_label": subscription_mode_label(current_mode),
    })
    write_activity(
        "push",
        "subscription_mode_switch_queue",
        "start" if queued.get("queued") else "skip",
        f"订阅模式切换补推：{subscription_mode_label(previous_mode)} -> {subscription_mode_label(current_mode)}，排队 {queued.get('queued') or 0} 条",
        total=len(items),
        queued=queued.get("queued") or 0,
        skipped=queued.get("skipped") or 0,
        old_mode=subscription_mode_label(previous_mode),
        mode=subscription_mode_label(current_mode),
        task=subscription_mode_task_label(current_mode),
    )
    return queued


CHINESE_NUMBERS = {
    "\u96f6": 0,
    "\u4e00": 1,
    "\u4e8c": 2,
    "\u4e24": 2,
    "\u4e09": 3,
    "\u56db": 4,
    "\u4e94": 5,
    "\u516d": 6,
    "\u4e03": 7,
    "\u516b": 8,
    "\u4e5d": 9,
}


def chinese_number_to_int(value):
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    if text == "\u5341":
        return 10
    if "\u5341" in text:
        left, _, right = text.partition("\u5341")
        tens = CHINESE_NUMBERS.get(left, 1) if left else 1
        ones = CHINESE_NUMBERS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CHINESE_NUMBERS.get(text, 0)


def split_subscription_season_title(title):
    raw = clean_html(title)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return "", 0
    patterns = [
        r"^(?P<base>.+?)[\s:：\-]*第\s*(?P<num>[0-9一二两三四五六七八九十]+)\s*季$",
        r"^(?P<base>.+?)[\s:：\-]*(?:Season|S)\s*(?P<num>\d{1,2})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, raw, re.I)
        if not match:
            continue
        season = chinese_number_to_int(match.group("num"))
        base = clean_html(match.group("base")).strip(" -:：")
        if base and season > 0:
            return base, season
    match = re.match(r"^(?P<base>.+?)(?P<num>\d{1,2})$", raw)
    if match:
        base = clean_html(match.group("base")).strip(" -:：")
        season = chinese_number_to_int(match.group("num"))
        if len(compact_match_text(base)) >= 2 and 1 < season <= 20 and not re.search(r"(?:19|20)\d{2}$", raw):
            return base, season
    return raw, 0


def resolve_subscription_tmdb_meta(title, media_type="tv", year="", target_season=0):
    clean_title = str(title or "").strip()
    media_type = "tv" if str(media_type or "").lower() == "tv" else "movie"
    if not clean_title:
        return {}
    cache = _read_tmdb_match_cache()
    cache_key = "subscription_tmdb_v2|" + media_type + "|" + compact_match_text(clean_title) + "|" + str(target_season or "") + "|" + str(extract_year(year) or "")
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    try:
        cfg = load_tmdb_config()
        if not cfg["api_key"]:
            return {}
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "include_adult": "false",
            "query": clean_title,
            "page": "1",
        }
        resolved_year = extract_year(year)
        if resolved_year and media_type == "movie":
            params["primary_release_year"] = resolved_year
        endpoint = f"{cfg['api_base_url']}/search/{media_type}"
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params), timeout=8)
        selected = pick_tmdb_search_result(data.get("results") or [], clean_title)
        if not selected:
            meta = {}
        else:
            tmdb_id = str(selected.get("id") or "")
            detail = get_cached_tmdb_detail(media_type, tmdb_id, fetch=True) if tmdb_id else {}
            title_value = detail.get("title") or detail.get("name") or selected.get("title") or selected.get("name") or clean_title
            date_value = detail.get("release_date") or detail.get("first_air_date") or selected.get("release_date") or selected.get("first_air_date") or ""
            rating = ""
            try:
                number = float(detail.get("vote_average") if detail else selected.get("vote_average") or 0)
            except Exception:
                number = 0
            if number > 0:
                rating = f"{number:.1f}"
            meta = {
                "tmdb_id": tmdb_id,
                "tmdb_title": title_value,
                "tmdb_year": extract_year(date_value),
                "rating": rating,
                "season_count": int(detail.get("number_of_seasons") or 0) if detail else 0,
                "series_episode_total": int(detail.get("number_of_episodes") or 0) if detail else 0,
                "current_season": 0,
                "episode_total": 0,
                "poster_url": tmdb_image(detail.get("poster_path"), "w342") if detail else tmdb_image(selected.get("poster_path"), "w342"),
                "backdrop_url": tmdb_image(detail.get("backdrop_path"), "w780") if detail else "",
            }
            seasons = [season for season in (detail.get("seasons") or []) if isinstance(season, dict) and tmdb_season_number(season) > 0]
            latest = max([tmdb_season_number(season) for season in seasons] or ([meta["season_count"]] if meta["season_count"] else [0]))
            wanted = int(target_season or latest or 0)
            if wanted:
                meta["current_season"] = wanted
                meta["latest_season"] = wanted
                for season in seasons:
                    if tmdb_season_number(season) == wanted:
                        meta["episode_total"] = int(season.get("episode_count") or 0)
                        meta["season_air_date"] = season.get("air_date") or ""
                        break
            elif latest:
                meta["current_season"] = latest
                meta["latest_season"] = latest
        cache[cache_key] = meta
        _write_tmdb_match_cache(cache)
        return meta
    except Exception:
        cache[cache_key] = {}
        _write_tmdb_match_cache(cache)
        return {}


def split_subscription_season_title(title):
    raw = clean_html(title)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return "", 0
    yearly = re.match(r"^(?P<base>.+?)[\s:：\-]*(?:年番|年番剧|年番篇)\s*\d*$", raw, re.I)
    if yearly:
        base = clean_html(yearly.group("base")).strip(" -:：")
        if base:
            return base, 0
    patterns = [
        r"^(?P<base>.+?)[\s:：\-]*第\s*(?P<num>[0-9一二两三四五六七八九十]+)\s*季$",
        r"^(?P<base>.+?)[\s:：\-]*(?:Season|S)\s*(?P<num>\d{1,2})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, raw, re.I)
        if not match:
            continue
        season = chinese_number_to_int(match.group("num"))
        base = clean_html(match.group("base")).strip(" -:：")
        if base and season > 0:
            return base, season
    match = re.match(r"^(?P<base>.+?)(?P<num>\d{1,2})$", raw)
    if match:
        base = clean_html(match.group("base")).strip(" -:：")
        season = chinese_number_to_int(match.group("num"))
        if (
            len(compact_match_text(base)) >= 2
            and 1 < season <= 20
            and not re.search(r"\d$", base)
            and not re.search(r"\d{3,}$", raw)
            and not re.search(r"(?:19|20)\d{2}$", raw)
        ):
            return base, season
    return raw, 0


def pick_subscription_tmdb_result(results, title, target_season=0, year=""):
    clean_title = str(title or "").strip()
    title_key = compact_match_text(clean_title)
    wanted_year = extract_year(year)
    best = None
    best_score = -10**9
    for row in (results or [])[:8]:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        name = row.get("name") or row.get("title") or ""
        original = row.get("original_name") or row.get("original_title") or ""
        row_key = compact_match_text(name)
        original_key = compact_match_text(original)
        exact = bool(title_key and title_key in {row_key, original_key})
        if title_key and not exact and title_key not in row_key and title_key not in original_key:
            continue
        tmdb_id = str(row.get("id") or "")
        try:
            detail = get_cached_tmdb_detail("tv", tmdb_id, fetch=True)
        except Exception:
            detail = {}
        season_count = int((detail or {}).get("number_of_seasons") or 0)
        episode_count = int((detail or {}).get("number_of_episodes") or 0)
        rating = 0.0
        try:
            rating = float((detail or {}).get("vote_average") or row.get("vote_average") or 0)
        except Exception:
            rating = 0.0
        date_value = (detail or {}).get("first_air_date") or row.get("first_air_date") or ""
        score = 1000 if exact else 100
        if target_season:
            score += 600 if season_count >= int(target_season) else -1200
        if wanted_year and str(date_value).startswith(wanted_year):
            score += 30
        score += min(season_count, 30) * 25
        score += min(episode_count, 500) * 0.2
        score += rating * 10
        if score > best_score:
            best_score = score
            best = (row, detail)
    if best:
        return best
    selected = pick_tmdb_search_result(results or [], clean_title)
    if selected and selected.get("id"):
        try:
            return selected, get_cached_tmdb_detail("tv", str(selected.get("id")), fetch=True)
        except Exception:
            return selected, {}
    return None, {}


def subscription_tmdb_lookup_titles(title):
    raw = clean_html(title)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return []
    titles = []
    seen = set()

    def add(value):
        text = clean_html(value).strip(" -:：")
        key = compact_match_text(text)
        if text and key and key not in seen:
            seen.add(key)
            titles.append(text)

    base, _ = split_subscription_season_title(raw)
    special_info = subscription_special_title_info(raw)
    if special_info.get("is_special"):
        add(special_info.get("main_title") or base)
    add(raw)
    add(base)

    for separator in ("：", ":", " - ", "-"):
        if separator in raw:
            add(raw.split(separator, 1)[0])

    special_patterns = [
        r"^(?P<base>.+?)[\s:：\-]*(?:特别篇|番外篇|番外|外传|剧场版|电影版|OVA|OAD|SP|Specials?|Movie).*$",
        r"^(?P<base>.+?)[\s:：\-]*(?:白银城篇|猎物篇|年番篇|年番剧|年番).*$",
    ]
    for source in list(titles):
        for pattern in special_patterns:
            match = re.match(pattern, source, re.I)
            if match:
                add(match.group("base"))
    return titles


SUBSCRIPTION_SPECIAL_PATTERNS = (
    "特别篇",
    "番外篇",
    "番外",
    "外传",
    "剧场版",
    "电影版",
    "OVA",
    "OAD",
    "SP",
    "Special",
    "Specials",
    "Movie",
)


def subscription_special_title_info(title):
    raw = clean_html(title)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return {"is_special": False, "main_title": "", "terms": []}
    pattern = r"^(?P<main>.+?)[\s:：\-]*(?P<marker>特别篇|番外篇|番外|外传|剧场版|电影版|OVA|OAD|SP|Specials?|Movie)(?P<tail>.*)$"
    match = re.match(pattern, raw, re.I)
    if not match:
        return {"is_special": False, "main_title": raw, "terms": []}
    main_title = clean_html(match.group("main")).strip(" -:：")
    tail = clean_html(match.group("tail") or "").strip(" -:：")
    terms = []
    for term in re.split(r"[\s:：·•,，、/\\|\-]+", tail):
        term = clean_html(term).strip(" -:：")
        if len(compact_match_text(term)) >= 2:
            terms.append(term)
    return {"is_special": True, "main_title": main_title or raw, "terms": terms}


def subscription_extract_date(*values):
    for value in values:
        text = normalize_resource_text(value)
        if not text:
            continue
        match = re.search(r"((?:19|20)\d{2})[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?", text)
        if not match or not match.group(3):
            continue
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except Exception:
            continue
    return None


def subscription_date_distance_days(left, right):
    if not left or not right:
        return None
    try:
        return abs((left - right).days)
    except Exception:
        return None


def subscription_tmdb_season_date(season):
    value = str((season or {}).get("air_date") or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def tmdb_show_title_is_special(title):
    text = compact_match_text(title)
    return any(compact_match_text(word) in text for word in SUBSCRIPTION_SPECIAL_PATTERNS)


def subscription_season_matches_special(tmdb_id, season, source_title="", source_date=None):
    season_number = tmdb_season_number(season)
    season_name = str((season or {}).get("name") or "")
    special_info = subscription_special_title_info(source_title)
    terms = special_info.get("terms") or []
    haystack = compact_match_text(season_name)
    title_match = bool(terms and any(compact_match_text(term) in haystack for term in terms))
    season_date = subscription_tmdb_season_date(season)
    distance = subscription_date_distance_days(source_date, season_date)
    date_match = distance is not None and distance <= 21
    episode_count = int((season or {}).get("episode_count") or 0)
    try:
        season_data = get_cached_tmdb_season_detail(tmdb_id, season_number)
    except Exception:
        season_data = {}
    for episode in season_data.get("episodes") or []:
        episode_title = str(episode.get("name") or "")
        episode_key = compact_match_text(episode_title)
        if terms and any(compact_match_text(term) in episode_key for term in terms):
            title_match = True
        episode_date = subscription_tmdb_season_date(episode)
        episode_distance = subscription_date_distance_days(source_date, episode_date)
        if episode_distance is not None and episode_distance <= 21:
            date_match = True
    if title_match and (date_match or not source_date):
        return "title_date" if date_match else "title"
    if date_match:
        return "date"
    if season_number == 0 and special_info.get("is_special") and episode_count:
        return "main_special"
    return ""


def pick_subscription_tmdb_season(detail, tmdb_id, fallback_title="", target_season=0, source_title="", source_date_text=""):
    seasons = [season for season in (detail.get("seasons") or []) if isinstance(season, dict)]
    regular = [season for season in seasons if tmdb_season_number(season) > 0]
    source_date = subscription_extract_date(source_date_text, source_title)
    special_info = subscription_special_title_info(source_title or fallback_title)
    show_title = detail.get("title") or detail.get("name") or fallback_title
    standalone_special = tmdb_show_title_is_special(show_title) and (
        special_info.get("is_special")
        or compact_match_text(special_info.get("main_title") or fallback_title) in compact_match_text(show_title)
    )

    def latest_regular():
        if not regular:
            return None
        if source_date:
            aired = [season for season in regular if (subscription_tmdb_season_date(season) and subscription_tmdb_season_date(season) <= source_date)]
            if aired:
                return max(aired, key=lambda season: (subscription_tmdb_season_date(season) or datetime.min.date(), tmdb_season_number(season)))
        return max(regular, key=tmdb_season_number)

    explicit = None
    try:
        if str(target_season).strip() != "":
            explicit = int(target_season)
    except Exception:
        explicit = None
    if explicit is not None and explicit > 0:
        for season in seasons:
            if tmdb_season_number(season) == explicit:
                return season, "explicit", ""

    if special_info.get("is_special"):
        if standalone_special:
            chosen = latest_regular()
            return chosen, "special_standalone", subscription_season_matches_special(tmdb_id, chosen or {}, source_title, source_date)
        special_season = next((season for season in seasons if tmdb_season_number(season) == 0), None)
        if special_season:
            match_reason = subscription_season_matches_special(tmdb_id, special_season, source_title, source_date)
            if match_reason:
                return special_season, "special", match_reason
        chosen = latest_regular()
        return chosen, "regular_fallback", ""

    if standalone_special:
        chosen = latest_regular()
        return chosen, "special_standalone", "tmdb_title"

    chosen = latest_regular()
    return chosen, "regular", ""


def subscription_title_override_tmdb_id(title):
    raw = str(title or "").strip()
    for candidate in subscription_tmdb_lookup_titles(raw):
        title_key = compact_match_text(candidate)
        for override_title, tmdb_id in SUBSCRIPTION_TMDB_TITLE_OVERRIDES.items():
            override_key = compact_match_text(override_title)
            if title_key == override_key or title_key.startswith(override_key):
                return str(tmdb_id)
    return ""


def build_subscription_tmdb_meta(media_type, tmdb_id, fallback_title="", target_season=0, source_title="", source_date=""):
    media_type = "tv" if str(media_type or "").lower() == "tv" else "movie"
    tmdb_id = str(tmdb_id or "").strip()
    if not tmdb_id:
        return {}
    detail = get_cached_tmdb_detail(media_type, tmdb_id, fetch=True)
    title_value = detail.get("title") or detail.get("name") or fallback_title
    date_value = detail.get("release_date") or detail.get("first_air_date") or ""
    try:
        number = float(detail.get("vote_average") or 0)
    except Exception:
        number = 0.0
    meta = {
        "tmdb_id": tmdb_id,
        "tmdb_title": title_value,
        "tmdb_year": extract_year(date_value),
        "rating": f"{number:.1f}" if number > 0 else "",
        "season_count": int(detail.get("number_of_seasons") or 0),
        "series_episode_total": int(detail.get("number_of_episodes") or 0),
        "current_season": 0,
        "episode_total": 0,
        "poster_url": tmdb_image(detail.get("poster_path"), "w342"),
        "backdrop_url": tmdb_image(detail.get("backdrop_path"), "w780"),
    }
    if media_type == "tv":
        chosen, season_type, season_match = pick_subscription_tmdb_season(detail, tmdb_id, fallback_title, target_season, source_title or fallback_title, source_date)
        special_info = subscription_special_title_info(source_title or fallback_title)
        if special_info.get("is_special") and special_info.get("main_title"):
            meta["lookup_title"] = str(special_info.get("main_title") or "").strip()
            meta["resource_title"] = meta["lookup_title"]
        elif season_type == "special_standalone":
            tmdb_special_info = subscription_special_title_info(title_value)
            if tmdb_special_info.get("is_special") and tmdb_special_info.get("main_title"):
                meta["lookup_title"] = str(tmdb_special_info.get("main_title") or "").strip()
                meta["resource_title"] = meta["lookup_title"]
        if isinstance(chosen, dict):
            wanted = tmdb_season_number(chosen)
            season_name = chosen.get("name") or ("特别篇" if wanted == 0 else f"第 {wanted} 季")
            if season_type == "special_standalone":
                season_name = "特别篇"
            meta["current_season"] = wanted
            meta["latest_season"] = wanted
            meta["season_number"] = wanted
            meta["target_season"] = wanted
            meta["season_name"] = season_name
            meta["season_type"] = season_type
            meta["season_match"] = season_match
            meta["episode_total"] = int(chosen.get("episode_count") or 0)
            meta["season_air_date"] = chosen.get("air_date") or ""
    return meta


def resolve_subscription_tmdb_meta(title, media_type="tv", year="", target_season=0):
    clean_title = str(title or "").strip()
    media_type = "tv" if str(media_type or "").lower() == "tv" else "movie"
    if not clean_title:
        return {}
    cache = _read_tmdb_match_cache()
    cache_key = "subscription_tmdb_v7|" + media_type + "|" + compact_match_text(clean_title) + "|" + str(target_season or "") + "|" + str(extract_year(year) or "")
    override_tmdb_id = subscription_title_override_tmdb_id(clean_title) if media_type == "tv" else ""
    if override_tmdb_id:
        try:
            meta = build_subscription_tmdb_meta(media_type, override_tmdb_id, clean_title, target_season, clean_title, year)
            if not meta.get("lookup_title"):
                meta["lookup_title"] = (subscription_tmdb_lookup_titles(clean_title) or [clean_title])[-1]
            cache[cache_key] = meta
            _write_tmdb_match_cache(cache)
            return meta
        except Exception:
            return {}
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    try:
        cfg = load_tmdb_config()
        if not cfg["api_key"]:
            return {}
        resolved_year = extract_year(year)
        endpoint = f"{cfg['api_base_url']}/search/{media_type}"
        best_meta = {}
        best_score = -1
        special_info = subscription_special_title_info(clean_title)
        for candidate_title in subscription_tmdb_lookup_titles(clean_title):
            params = {
                "api_key": cfg["api_key"],
                "language": "zh-CN",
                "include_adult": "false",
                "query": candidate_title,
                "page": "1",
            }
            if resolved_year and media_type == "movie":
                params["primary_release_year"] = resolved_year
            data = http_json(endpoint + "?" + urllib.parse.urlencode(params), timeout=10)
            if media_type == "tv":
                selected, detail = pick_subscription_tmdb_result(data.get("results") or [], candidate_title, target_season, year)
            else:
                selected = pick_tmdb_search_result(data.get("results") or [], candidate_title)
                detail = get_cached_tmdb_detail(media_type, str(selected.get("id")), fetch=True) if selected and selected.get("id") else {}
            if not selected or not selected.get("id"):
                continue
            tmdb_id = str(selected.get("id") or "")
            meta = build_subscription_tmdb_meta(media_type, tmdb_id, candidate_title, target_season, clean_title, year)
            if not meta and detail:
                meta = {"tmdb_id": tmdb_id}
            if meta:
                tmdb_title = str(meta.get("tmdb_title") or "").strip()
                lookup_title = tmdb_title or candidate_title
                if special_info.get("is_special") and special_info.get("main_title"):
                    lookup_title = str(special_info.get("main_title") or "").strip()
                    meta["resource_title"] = lookup_title
                meta["lookup_title"] = lookup_title
                if compact_match_text(lookup_title) != compact_match_text(clean_title):
                    meta["match_title"] = clean_title
                score = 100
                if special_info.get("is_special"):
                    season_type = str(meta.get("season_type") or "")
                    if season_type == "special" and meta.get("season_match"):
                        score = 1300
                    elif season_type == "special_standalone" and meta.get("season_match"):
                        score = 1200
                    elif season_type == "special":
                        score = 900
                    elif season_type == "special_standalone":
                        score = 850
                    elif compact_match_text(candidate_title) == compact_match_text(special_info.get("main_title") or ""):
                        score = 700
                    else:
                        score = 500
                if compact_match_text(candidate_title) == compact_match_text(clean_title):
                    score += 20
                if score > best_score:
                    best_score = score
                    best_meta = meta
                if score >= 1300:
                    break
        meta = best_meta
        cache[cache_key] = meta
        _write_tmdb_match_cache(cache)
        return meta
    except Exception:
        cache[cache_key] = {}
        _write_tmdb_match_cache(cache)
        return {}


def normalize_subscription_item_metadata(item, resolve_tmdb=False):
    row = dict(item or {})
    media_type = discover_item_media_type(row) or str(row.get("media_type") or row.get("type") or "").lower()
    if media_type not in ("movie", "tv"):
        media_type = "movie" if row.get("release_date") else "tv"
    row["media_type"] = media_type
    if media_type != "tv":
        return row
    raw_title = str(row.get("source_title") or row.get("match_title") or row.get("title") or row.get("name") or "").strip()
    base_title, parsed_target_season = split_subscription_season_title(raw_title)
    stored_target_season = None
    for key in ("target_season", "current_season", "latest_season", "season_number", "season"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            number = int(float(str(value).strip()))
        except Exception:
            continue
        if number >= 0:
            stored_target_season = number
            break
    target_season = parsed_target_season or (stored_target_season if stored_target_season is not None else 0)
    if raw_title and base_title and base_title != raw_title:
        row["title"] = base_title
    if parsed_target_season:
        row["target_season"] = parsed_target_season
        row["current_season"] = parsed_target_season
        row["latest_season"] = parsed_target_season
        row["season_number"] = parsed_target_season
    elif stored_target_season is not None:
        row["target_season"] = stored_target_season
        row["current_season"] = stored_target_season
        row["latest_season"] = stored_target_season
        row["season_number"] = stored_target_season
    else:
        if resolve_tmdb:
            row.pop("target_season", None)
            row.pop("season_number", None)
            row.pop("current_season", None)
            row.pop("latest_season", None)
    tmdb_id = discover_item_tmdb_id(row, "tv")
    if not tmdb_id and resolve_tmdb:
        cached = get_cached_discover_item({"title": row.get("title") or base_title, "media_type": "tv", "year": row.get("year") or ""})
        if isinstance(cached, dict):
            tmdb_id = discover_item_tmdb_id(cached, "tv")
            for key in ("poster_url", "backdrop_url", "rating", "season_count", "episode_total", "total_episodes"):
                if not row.get(key) and cached.get(key):
                    row[key] = cached.get(key)
    meta = {}
    needs_tmdb_resolve = True
    meta_source_title = row.get("source_title") or raw_title or row.get("title") or base_title
    if target_season == 0 and (
        str(row.get("season_type") or "") == "special"
        or "特别" in str(row.get("season_name") or "")
    ):
        if not subscription_special_title_info(meta_source_title).get("is_special"):
            meta_source_title = f"{row.get('title') or base_title or meta_source_title} 特别篇"
    if resolve_tmdb and needs_tmdb_resolve and not tmdb_id:
        meta = resolve_subscription_tmdb_meta(meta_source_title, "tv", row.get("year") or row.get("date") or row.get("air_date") or "", target_season)
        tmdb_id = str(meta.get("tmdb_id") or "")
    elif resolve_tmdb and needs_tmdb_resolve and tmdb_id:
        try:
            meta = build_subscription_tmdb_meta("tv", tmdb_id, row.get("title") or base_title, target_season, meta_source_title, row.get("year") or row.get("date") or row.get("air_date") or "")
        except Exception:
            meta = resolve_subscription_tmdb_meta(meta_source_title, "tv", row.get("year") or "", target_season)
        if meta.get("tmdb_id"):
            tmdb_id = str(meta.get("tmdb_id"))
    if tmdb_id:
        row["tmdb_id"] = tmdb_id
    elif row.get("tmdb_id") and row.get("tmdb_id") == row.get("id"):
        row.pop("tmdb_id", None)
    if meta:
        lookup_title = str(meta.get("lookup_title") or "").strip()
        if lookup_title and compact_match_text(lookup_title) != compact_match_text(row.get("title") or ""):
            row["title"] = lookup_title
        for key in ("tmdb_title", "season_count", "series_episode_total", "current_season", "latest_season", "season_number", "target_season", "season_air_date", "season_name", "season_type", "season_match"):
            if key in meta and meta.get(key) not in (None, ""):
                row[key] = meta.get(key)
        if "episode_total" in meta and meta.get("episode_total") not in (None, ""):
            row["episode_total"] = meta["episode_total"]
            row["total_episodes"] = meta["episode_total"]
        if not row.get("poster_url") and meta.get("poster_url"):
            row["poster_url"] = meta["poster_url"]
        if not row.get("backdrop_url") and meta.get("backdrop_url"):
            row["backdrop_url"] = meta["backdrop_url"]
        if not row.get("rating") and meta.get("rating"):
            row["rating"] = meta["rating"]
        corrected_year = meta.get("tmdb_year") or ""
        if corrected_year:
            old_year = extract_year(row.get("year") or "")
            if old_year and old_year != corrected_year and not row.get("source_year"):
                row["source_year"] = old_year
            row["year"] = corrected_year
        elif not row.get("year"):
            row["year"] = meta.get("season_air_date", "")[:4] or ""
    final_tmdb_id = discover_item_tmdb_id(row, "tv")
    title_key = normalize_subscription_dedupe_title(row.get("title") or base_title or "")
    if final_tmdb_id and title_key:
        season = subscription_target_season(row)
        season_suffix = f":season:{season}" if season is not None else ""
        row["subscription_key"] = f"tv:{title_key}:tmdb:{final_tmdb_id}{season_suffix}"
        row["dedupe_key"] = row["subscription_key"]
        url_value = str(row.get("url") or "")
        if "themoviedb.org/tv/" in url_value or str(row.get("source") or "").startswith("TMDB"):
            row["id"] = final_tmdb_id
            row["source_id"] = final_tmdb_id
            row["url"] = f"https://www.themoviedb.org/tv/{final_tmdb_id}"
    row.pop("source_title", None)
    row.pop("match_title", None)
    row.pop("resource_title", None)
    return row


def subscription_has_required_tmdb(item):
    if not isinstance(item, dict):
        return False
    media_type = discover_item_media_type(item) or str(item.get("media_type") or item.get("type") or "").lower()
    if media_type != "tv":
        return True
    return bool(discover_item_tmdb_id(item, "tv"))


def enrich_subscription_items(data, remove_completed=False):
    if not isinstance(data, dict):
        return {"items": [], "last_run_at": "", "stats": {"total": 0, "movie": 0, "tv": 0}, "errors": []}
    items = data.get("items")
    if not isinstance(items, list):
        data["items"] = []
        return data
    enriched = []
    removed_completed = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = normalize_subscription_item_metadata(item, resolve_tmdb=True)
        row = merge_cached_discover_item(row)
        row = normalize_subscription_item_metadata(row, resolve_tmdb=subscription_special_title_info(row.get("title") or row.get("name") or "").get("is_special"))
        media_type = str(row.get("media_type") or row.get("type") or "").strip()
        if media_type in ("电视剧", "tv", "series"):
            media_type = "tv"
        elif media_type in ("电影", "movie", "film"):
            media_type = "movie"
        else:
            media_type = "movie" if row.get("release_date") else "tv"
        row["media_type"] = media_type
        if media_type == "tv" and not subscription_has_required_tmdb(row):
            row["metadata_pending"] = True
            row["in_library"] = bool(row.get("in_library"))
            row["library_episode_count"] = first_positive_int(row.get("library_episode_count"))
            enriched.append(row)
            continue
        title = row.get("title") or row.get("name") or ""
        library_title = row.get("tmdb_title") if row.get("season_type") == "special_standalone" else title
        library_title = library_title or title
        tmdb_id = discover_item_tmdb_id(row, media_type)
        target_season = subscription_target_season(row)
        if media_type == "tv" and target_season is not None:
            seasons = get_library_season_episodes(library_title, tmdb_id)
            season_count = len(seasons.get(str(target_season)) or [])
            row["in_library"] = season_count > 0
            row["library_episode_count"] = season_count
        else:
            status = get_library_item_status(library_title, tmdb_id, media_type)
            row["in_library"] = bool(status.get("in_library"))
            row["library_episode_count"] = int(status.get("episode_count") or 0)
        enrich_tmdb_tv_progress_fields(row, title, tmdb_id)
        if remove_completed and is_subscription_completed(row):
            removed_completed.append(row)
            continue
        enriched.append(row)
    enriched = dedupe_subscription_items(enriched)
    data["items"] = enriched
    data["stats"] = recalc_subscription_stats(enriched)
    if removed_completed:
        data["removed_completed"] = removed_completed
    data.setdefault("errors", [])
    return data


def load_subscription_items(with_progress=False, remove_completed=True, persist_progress=True):
    data = subscription_repository().load_payload()
    if not isinstance(data, dict):
        data = {}
    data.setdefault("items", [])
    data["items"] = [sanitize_subscription_item_for_storage(item) for item in (data.get("items") or []) if isinstance(item, dict)]
    data.setdefault("last_run_at", "")
    data["stats"] = recalc_subscription_stats(data.get("items") or [])
    data.setdefault("errors", [])
    if with_progress:
        enriched = enrich_subscription_items(data, remove_completed=remove_completed)
        if persist_progress:
            write_subscription_items_data(enriched)
        return enriched
    return data


def get_subscription_item_key(item):
    if not isinstance(item, dict):
        return ""
    if item.get("subscription_key"):
        return str(item.get("subscription_key") or "")
    media_type = discover_item_media_type(item) or str(item.get("media_type") or item.get("type") or "").strip().lower()
    title_value = item.get("title") or ""
    if media_type == "tv":
        title_value, _ = split_subscription_season_title(title_value)
    title_key = normalize_subscription_dedupe_title(title_value)
    tmdb_id = discover_item_tmdb_id(item, media_type)
    if media_type == "tv" and tmdb_id and title_key:
        season = subscription_target_season(item)
        season_suffix = f":season:{season}" if season is not None else ""
        return f"tv:{title_key}:tmdb:{tmdb_id}{season_suffix}"
    item_id = str(item.get("id") or "").strip()
    return item_id or f"{media_type}:{title_key}"


def recalc_subscription_stats(items):
    rows = items if isinstance(items, list) else []
    return {
        "total": len(rows),
        "movie": sum(1 for item in rows if item.get("media_type") == "movie"),
        "tv": sum(1 for item in rows if item.get("media_type") == "tv"),
    }


def first_positive_int(*values):
    for value in values:
        try:
            number = int(float(str(value).strip()))
        except Exception:
            number = 0
        if number > 0:
            return number
    return 0


def subscription_target_season(item):
    if not isinstance(item, dict):
        return None
    for key in ("target_season", "current_season", "latest_season", "season_number", "season"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            number = int(float(str(value).strip()))
        except Exception:
            continue
        if number >= 0:
            return number
    return None


def subscription_item_rank(item):
    season_value = subscription_target_season(item)
    season = season_value if season_value is not None else 0
    has_target = 1 if season_value is not None else 0
    year = first_positive_int(item.get("year"), item.get("season_air_date"))
    updated = str(item.get("updated_at") or item.get("created_at") or "")
    return (season, has_target, year, updated)


def dedupe_subscription_items(items):
    rows = items if isinstance(items, list) else []
    deduped = []
    positions = {}
    for item in rows:
        key = get_subscription_dedupe_key(item)
        if not key:
            deduped.append(item)
            continue
        if key not in positions:
            positions[key] = len(deduped)
            deduped.append(item)
            continue
        index = positions[key]
        if subscription_item_rank(item) >= subscription_item_rank(deduped[index]):
            deduped[index] = item
    return deduped


def is_subscription_completed(item):
    if not isinstance(item, dict):
        return False
    media_type = discover_item_media_type(item) or str(item.get("media_type") or "").lower()
    if media_type == "movie":
        return bool(item.get("in_library"))
    if media_type != "tv":
        return False
    total = first_positive_int(
        item.get("episode_total"),
        item.get("total_episodes"),
        item.get("episodes_total"),
        item.get("episode_count"),
    )
    if total <= 0:
        return False
    current = first_positive_int(item.get("library_episode_count"))
    return current >= total


def sanitize_subscription_item_for_storage(item):
    row = dict(item or {})
    row.pop("source_title", None)
    row.pop("match_title", None)
    row.pop("resource_title", None)
    if str(row.get("season_type") or "") == "special_standalone":
        name = str(row.get("season_name") or "").strip().lower()
        if not name or name in ("season 1", "第 1 季", "第1季"):
            row["season_name"] = "特别篇"
    return row


def write_subscription_items_data(data):
    payload = dict(data) if isinstance(data, dict) else {}
    payload.pop("removed_completed", None)
    payload.setdefault("items", [])
    payload["items"] = [sanitize_subscription_item_for_storage(item) for item in (payload.get("items") or []) if isinstance(item, dict)]
    payload["stats"] = recalc_subscription_stats(payload.get("items") or [])
    return subscription_repository().save_payload(payload, get_subscription_item_key)


def merge_subscription_source_items(existing_items, source_items):
    """Merge one automatic-source result without deleting local intent.

    Automatic source refreshes are additive. Manual subscriptions and Torra
    mirror rows remain in the ledger even when they are absent from this
    source's current result set.
    """
    existing = [dict(item) for item in (existing_items or []) if isinstance(item, dict)]
    incoming = [dict(item) for item in (source_items or []) if isinstance(item, dict)]
    by_key = {}
    by_identity = {}
    for item in existing:
        key = str(get_subscription_item_key(item) or "").strip()
        if key:
            by_key[key] = item
        identity = str(get_subscription_dedupe_key(item) or "").strip()
        if identity:
            by_identity[identity] = item

    merged = list(existing)
    positions = {id(item): index for index, item in enumerate(merged)}
    protected_fields = {
        "origin",
        "read_only",
        "torra_remote_id",
        "torra_sync_state",
        "torra_mapping_status",
        "torra_remote_status",
    }
    added = 0
    updated = 0
    for candidate in incoming:
        normalized = normalize_subscription_item_metadata(candidate, resolve_tmdb=False)
        key = str(get_subscription_item_key(normalized) or "").strip()
        identity = str(get_subscription_dedupe_key(normalized) or "").strip()
        current = by_key.get(key) or by_identity.get(identity)
        if current is None:
            merged.append(normalized)
            if key:
                by_key[key] = normalized
            if identity:
                by_identity[identity] = normalized
            added += 1
            continue
        preserved = {field: current[field] for field in protected_fields if field in current}
        if current.get("read_only") or str(current.get("origin") or "") in {"manual", "torra"}:
            for field in ("source", "source_label"):
                if field in current:
                    preserved[field] = current[field]
        next_item = {**current, **normalized, **preserved}
        index = positions.get(id(current))
        if index is None:
            index = next((idx for idx, item in enumerate(merged) if item is current), None)
        if index is None:
            merged.append(next_item)
        else:
            merged[index] = next_item
        next_key = str(get_subscription_item_key(next_item) or key).strip()
        next_identity = str(get_subscription_dedupe_key(next_item) or identity).strip()
        if next_key:
            by_key[next_key] = next_item
        if next_identity:
            by_identity[next_identity] = next_item
        updated += 1
    return merged, {"added": added, "updated": updated, "preserved": max(0, len(existing) - updated)}


def delete_subscription_item(payload):
    payload = payload or {}
    key = str(payload.get("key") or "").strip()
    item_payload = payload.get("item") if isinstance(payload.get("item"), dict) else None
    candidate_keys = {key} if key else set()
    if item_payload:
        for candidate in (item_payload, normalize_subscription_item_metadata(dict(item_payload), resolve_tmdb=False)):
            for value in (
                get_subscription_item_key(candidate),
                get_subscription_dedupe_key(candidate),
                candidate.get("subscription_key"),
                candidate.get("dedupe_key"),
            ):
                value = str(value or "").strip()
                if value:
                    candidate_keys.add(value)
    if not candidate_keys:
        raise RuntimeError("\u7f3a\u5c11\u5220\u9664\u76ee\u6807")
    def matches_delete_target(item):
        row_keys = {
            get_subscription_item_key(item),
            get_subscription_dedupe_key(item),
            str(item.get("subscription_key") or "").strip(),
            str(item.get("dedupe_key") or "").strip(),
        }
        row_keys = {value for value in row_keys if value}
        return bool(row_keys & candidate_keys)

    removed = subscription_repository().delete_where(matches_delete_target)
    data = load_subscription_items()
    data["removed_count"] = len(removed)
    title = str((removed[0].get("title") if removed else "") or "")
    write_activity("subscription", "delete_subscription", "success", "删除订阅", title=title, key=key)
    return data


def block_subscription_item(payload):
    payload = payload or {}
    key = str(payload.get("key") or "").strip()
    item_payload = payload.get("item") if isinstance(payload.get("item"), dict) else None
    item = find_subscription_item(key) if key else None
    if not item and item_payload:
        item = item_payload
    title = str((item or {}).get("title") or (item or {}).get("name") or payload.get("title") or "").strip()
    if not title:
        raise RuntimeError("缺少屏蔽标题")
    blocked = subscription_blocked_titles()
    blocked_keys = {compact_match_text(value) for value in blocked}
    if compact_match_text(title) not in blocked_keys:
        blocked.append(title)
    config = set_subscription_blocked_titles(blocked)
    try:
        data = delete_subscription_item({"key": key, "item": item or item_payload or {"title": title}})
    except Exception:
        data = load_subscription_items()
        data["removed_count"] = 0
    data["config"] = config
    data["blocked_titles"] = subscription_blocked_titles()
    write_activity("subscription", "block_subscription", "success", f"屏蔽订阅：{title}", title=title, removed_count=data.get("removed_count") or 0)
    return data


def unblock_subscription_title(payload):
    payload = payload or {}
    title = str(payload.get("title") or "").strip()
    if not title:
        raise RuntimeError("缺少取消屏蔽标题")
    target = compact_match_text(title)
    blocked = [value for value in subscription_blocked_titles() if compact_match_text(value) != target]
    config = set_subscription_blocked_titles(blocked)
    data = load_subscription_items()
    data["config"] = config
    data["blocked_titles"] = subscription_blocked_titles()
    write_activity("subscription", "unblock_subscription", "success", f"取消屏蔽订阅：{title}", title=title)
    return data


def clear_subscription_items():
    old_count = subscription_repository().clear_items()
    data = {
        "items": [],
        "last_run_at": "",
        "stats": {"total": 0, "movie": 0, "tv": 0},
        "errors": [],
    }
    write_activity("subscription", "clear_subscriptions", "success", "清空订阅", total=old_count)
    return data


def _tg_notify_truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _tg_notify_split(value):
    return [item.strip() for item in re.split(r"[\s,，;；|]+", str(value or "")) if item and item.strip()]


def _tg_notify_keywords(value):
    return [item.strip() for item in re.split(r"[\n,，;；|]+|\s{2,}", str(value or "")) if item and item.strip()]


def _default_subscription_notify_template():
    return [
        {"key": "poster", "label": "海报", "icon": "🖼️", "sample": "TMDB订阅海报/背景图", "enabled": True},
        {"key": "title", "label": "标题", "icon": "📺", "sample": "电视剧：诡秘之主 (2025) 特别篇", "enabled": True},
        {"key": "season", "label": "季集", "icon": "📅", "sample": "订阅季: 特别篇 / Season 0", "enabled": True},
        {"key": "id", "label": "ID", "icon": "🍿", "sample": "TMDB ID: 232230", "enabled": True},
        {"key": "rating", "label": "评分", "icon": "⭐", "sample": "评分: 8.4", "enabled": True},
        {"key": "genre", "label": "题材", "icon": "🎭", "sample": "题材: 动画/奇幻", "enabled": True},
        {"key": "region", "label": "地区", "icon": "📂", "sample": "地区: 大陆", "enabled": True},
        {"key": "status", "label": "状态", "icon": "🧭", "sample": "状态: 已订阅 / 待入库", "enabled": True},
        {"key": "source", "label": "来源", "icon": "📢", "sample": "来源: 手动订阅", "enabled": True},
        {"key": "plot", "label": "剧情", "icon": "📝", "sample": "剧情简介: 一段简介文本...", "enabled": True},
    ]


def _subscription_template_rows(config_value):
    try:
        parsed = json.loads(str(config_value or ""))
    except Exception:
        parsed = []
    saved = {str(item.get("key") or ""): item for item in parsed if isinstance(item, dict)} if isinstance(parsed, list) else {}
    rows = []
    for default in _default_subscription_notify_template():
        key = str(default.get("key") or "")
        merged = dict(default)
        if key in saved:
            merged.update({k: v for k, v in saved[key].items() if k in {"enabled", "label", "icon", "sample"}})
        rows.append(merged)
    return rows


def _subscription_notify_label(label, value):
    text = str(value or "").strip()
    return f"{label}: {text}" if text else ""


def _subscription_notify_context(item, original_title="", replaced=False, metadata_pending=False):
    media_type = discover_item_media_type(item) or str(item.get("media_type") or "")
    media_label = "电视剧" if media_type == "tv" else "电影"
    title = str(item.get("title") or item.get("name") or original_title or "").strip()
    tmdb_id = discover_item_tmdb_id(item, media_type) if media_type in ("tv", "movie") else str(item.get("tmdb_id") or item.get("id") or "")
    season = subscription_target_season(item)
    season_name = str(item.get("season_name") or "").strip()
    season_text = ""
    if media_type == "tv":
        if season_name and season is not None:
            season_text = f"{season_name} / Season {season}"
        elif season_name:
            season_text = season_name
        elif season is not None:
            season_text = f"Season {season}"
    status_parts = ["已订阅"]
    if replaced:
        status_parts.append("已更新")
    if metadata_pending:
        status_parts.append("等待 TMDB 匹配")
    elif item.get("in_library"):
        status_parts.append("已入库")
    else:
        status_parts.append("待入库")
    tmdb_path = "tv" if media_type == "tv" else "movie"
    tmdb_url = f"https://www.themoviedb.org/{tmdb_path}/{tmdb_id}" if tmdb_id else str(item.get("url") or "")
    genre = item.get("genre") or item.get("genres") or item.get("category") or ""
    if isinstance(genre, list):
        genre = " / ".join(str(v) for v in genre if v)
    region = item.get("region") or item.get("country") or item.get("area") or ""
    return {
        "poster": str(item.get("backdrop_url") or item.get("poster_url") or ""),
        "title": f"{media_label}：{title}",
        "season": _subscription_notify_label("订阅季", season_text),
        "id": f"TMDB ID: {tmdb_id}" if tmdb_id else "",
        "rating": _subscription_notify_label("评分", item.get("rating") or ""),
        "genre": _subscription_notify_label("题材", genre),
        "region": _subscription_notify_label("地区", region),
        "status": _subscription_notify_label("状态", " / ".join(status_parts)),
        "source": _subscription_notify_label("来源", item.get("source_label") or item.get("source") or item.get("source_key") or "手动订阅"),
        "link": f"链接: {tmdb_url}" if tmdb_url else "",
        "plot": _subscription_notify_label("剧情简介", item.get("overview") or item.get("plot") or ""),
        "signature": "- Powered by NasEmby",
        "filter_text": " ".join(str(v or "") for v in [title, original_title, tmdb_id, season_name, item.get("source_label"), item.get("source_key")]),
    }


def _render_subscription_notify_message(context):
    from app.config import read_config
    cfg = read_config()
    lines = []
    for row in _subscription_template_rows(cfg.get("ENV_TG_SUBSCRIPTION_NOTIFY_TEMPLATE")):
        if not _tg_notify_truthy(row.get("enabled")):
            continue
        key = str(row.get("key") or "")
        value = str(context.get(key) or "").strip()
        if not value or key == "poster":
            continue
        icon = str(row.get("icon") or "").strip()
        lines.append(f"{icon} {value}".strip())
    return "\n".join(lines).strip()


def _subscription_notify_allowed(context):
    from app.config import read_config
    cfg = read_config()
    haystack = str(context.get("filter_text") or "").lower()
    whitelist = [item.lower() for item in _tg_notify_keywords(cfg.get("ENV_TG_TRANSFER_NOTIFY_WHITELIST"))]
    blacklist = [item.lower() for item in _tg_notify_keywords(cfg.get("ENV_TG_TRANSFER_NOTIFY_BLACKLIST"))]
    if blacklist and any(item in haystack for item in blacklist):
        return False, "blacklist"
    if whitelist and not any(item in haystack for item in whitelist):
        return False, "whitelist"
    return True, ""


def _send_subscription_tg_notify(item, original_title="", replaced=False, metadata_pending=False):
    from app.config import read_config
    cfg = read_config()
    if not _tg_notify_truthy(cfg.get("ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED")):
        return
    token = str(cfg.get("ENV_TG_BOT_TOKEN") or "").strip()
    chat_ids = _tg_notify_split(cfg.get("ENV_TG_TRANSFER_NOTIFY_CHAT_IDS")) or _tg_notify_split(cfg.get("ENV_TG_ADMIN_USER_ID"))
    context = _subscription_notify_context(item, original_title, replaced, metadata_pending)
    allowed, reason = _subscription_notify_allowed(context)
    if not token or not chat_ids or not allowed:
        write_activity(
            "subscription",
            "telegram_subscription_notify",
            "skip" if not allowed else "error",
            "订阅 TG 通知跳过" if not allowed else ("订阅 TG 通知失败：未配置 Bot Token 或 TG ID"),
            title=item.get("title") or "",
            reason=reason or "missing_config",
        )
        return
    message = _render_subscription_notify_message(context)
    if not message:
        return
    sent = 0
    errors = []
    for chat_id in chat_ids:
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({"chat_id": chat_id, "text": message, "disable_web_page_preview": False}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
            if data.get("ok"):
                sent += 1
            else:
                errors.append(f"{chat_id}: {data}")
        except Exception as exc:
            errors.append(f"{chat_id}: {exc}")
    write_activity(
        "subscription",
        "telegram_subscription_notify",
        "success" if sent else "error",
        "订阅 TG 通知已发送" if sent else "订阅 TG 通知发送失败",
        title=item.get("title") or "",
        tmdb_id=discover_item_tmdb_id(item, discover_item_media_type(item) or "tv") or "",
        sent=sent,
        errors="; ".join(errors)[:240],
    )


def save_subscription_item(payload):
    item = (payload or {}).get("item")
    if not isinstance(item, dict):
        raise RuntimeError("\u7f3a\u5c11\u8ba2\u9605\u5185\u5bb9")
    original_title_for_log = str(item.get("source_title") or item.get("title") or item.get("name") or "").strip()
    item = normalize_subscription_item_metadata(item, resolve_tmdb=subscription_special_title_info(original_title_for_log).get("is_special"))
    item = merge_cached_discover_item(item)
    item = normalize_subscription_item_metadata(item, resolve_tmdb=subscription_special_title_info(item.get("title") or original_title_for_log).get("is_special"))
    metadata_pending = False
    if discover_item_media_type(item) == "tv" and not subscription_has_required_tmdb(item):
        item["metadata_pending"] = True
        metadata_pending = True
    key = get_subscription_item_key(item)
    if not key:
        raise RuntimeError("\u7f3a\u5c11\u8ba2\u9605\u76ee\u6807")
    item = dict(item)
    item.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    item["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    set_discover_item_cache(item, "subscription")
    replaced, saved_item = subscription_repository().upsert_item(item, key)
    item = saved_item
    data = load_subscription_items()
    message = "保存订阅，等待 TMDB 匹配" if metadata_pending else "保存订阅成功"
    data["saved_item"] = item
    data["message"] = message
    write_activity(
        "subscription",
        "save_subscription",
        "success",
        message,
        title=item.get("title") or "",
        source_title=original_title_for_log if original_title_for_log and compact_match_text(original_title_for_log) != compact_match_text(item.get("title") or "") else "",
        media_type=item.get("media_type") or "",
        tmdb_id=discover_item_tmdb_id(item, "tv") if discover_item_media_type(item) == "tv" else item.get("tmdb_id") or item.get("id") or "",
        season=subscription_target_season(item) if discover_item_media_type(item) == "tv" else "",
        season_name=item.get("season_name") or "",
        season_type=item.get("season_type") or "",
        key=key,
        replaced=replaced,
        metadata_pending=metadata_pending,
        reason="缺少 TMDB ID，刷新订阅时会尝试按主标题重新匹配" if metadata_pending else "",
    )
    data["subscription_task"] = queue_subscription_resource_rule_transfer([item], "manual_subscription")
    data["auto_transfer"] = data["subscription_task"]
    _send_subscription_tg_notify(item, original_title_for_log, replaced, metadata_pending)
    return data


def update_subscription_item(key, updater):
    def mutate(item):
        updater(item)
        item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return subscription_repository().mutate_item(key, mutate, get_subscription_item_key)


def subscription_lookup_key_set(item):
    if not isinstance(item, dict):
        return set()
    keys = {
        get_subscription_item_key(item),
        get_subscription_dedupe_key(item),
        str(item.get("subscription_key") or "").strip(),
        str(item.get("dedupe_key") or "").strip(),
    }
    normalized = normalize_subscription_item_metadata(item, resolve_tmdb=False)
    keys.update({
        get_subscription_item_key(normalized),
        get_subscription_dedupe_key(normalized),
        str(normalized.get("subscription_key") or "").strip(),
        str(normalized.get("dedupe_key") or "").strip(),
    })
    return {key for key in keys if key}


def subscription_series_lookup_key(item):
    if not isinstance(item, dict):
        return ""
    row = normalize_subscription_item_metadata(item, resolve_tmdb=False)
    media_type = discover_item_media_type(row) or str(row.get("media_type") or row.get("type") or "").strip().lower()
    if media_type != "tv":
        return ""
    title_value, _ = split_subscription_season_title(row.get("title") or "")
    title_key = normalize_subscription_dedupe_title(title_value)
    tmdb_id = discover_item_tmdb_id(row, "tv") or str(row.get("tmdb_id") or "").strip()
    if title_key and tmdb_id:
        return f"tv:{title_key}:tmdb:{tmdb_id}"
    if title_key:
        return f"tv:{title_key}"
    return ""


def subscription_exclusion_match(item, exclude_titles):
    terms = parse_subscription_exclude_titles(exclude_titles)
    if not terms:
        return ""
    if isinstance(item, dict):
        values = [
            item.get("title"),
            item.get("name"),
            item.get("source_title"),
            item.get("match_title"),
            item.get("original_title"),
            item.get("original_name"),
            item.get("tmdb_title"),
            item.get("season_name"),
        ]
    else:
        values = [item]
    candidates = []
    for value in values:
        text = str(value or "").strip()
        if text:
            candidates.append(text)
    for term in terms:
        term_key = compact_match_text(term)
        if not term_key:
            continue
        for candidate in candidates:
            candidate_key = compact_match_text(candidate)
            if candidate_key and term_key in candidate_key:
                return term
    return ""


def sync_daily_airing_subscriptions(limit=72):
    limit = parse_positive_int(limit, 72, 1, 120)
    write_activity("subscription", "sync_daily_airing", "start", "开始检测全球日播订阅", limit=limit)
    now_text = beijing_now_text()
    errors = []
    try:
        rows = fetch_daily_airing_subscription_source(limit)
    except Exception as exc:
        message = f"全球日播获取失败：{exc}"
        write_activity("subscription", "sync_daily_airing", "error", message, error=str(exc))
        raise

    data = load_subscription_items()
    config = load_subscription_config()
    douban = config.get("douban") if isinstance(config, dict) else {}
    exclude_titles = parse_subscription_exclude_titles((douban or {}).get("exclude_titles"))
    existing_items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(existing_items, list):
        existing_items = []
    existing_keys = set()
    existing_series_keys = set()
    for item in existing_items:
        existing_keys.update(subscription_lookup_key_set(item))
        if subscription_target_season(item) is None:
            series_key = subscription_series_lookup_key(item)
            if series_key:
                existing_series_keys.add(series_key)

    added = []
    skipped = []
    seen = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        source_title = str(raw.get("source_title") or raw.get("title") or raw.get("name") or "").strip()
        try:
            item = normalize_subscription_item_metadata(raw, resolve_tmdb=True)
            item = merge_cached_discover_item(item)
            item = normalize_subscription_item_metadata(item, resolve_tmdb=True)
        except Exception as exc:
            title = source_title or str(raw.get("id") or "")
            errors.append(f"{title}: {exc}")
            write_activity("subscription", "sync_daily_airing_item", "error", f"全球日播订阅解析失败：{title}", title=title, error=str(exc))
            continue
        title = str(item.get("title") or item.get("name") or source_title or "").strip()
        exclude_match = subscription_exclusion_match(item, exclude_titles)
        if exclude_match:
            skipped.append({"title": title, "reason": f"排除订阅: {exclude_match}"})
            write_activity("subscription", "sync_daily_airing_item", "skip", f"全球日播排除订阅：{title}", title=title, exclude=exclude_match)
            continue
        if not subscription_has_required_tmdb(item):
            skipped.append({"title": title, "reason": "TMDB未匹配"})
            write_activity("subscription", "sync_daily_airing_item", "skip", f"全球日播跳过：TMDB未匹配：{title}", title=title, reason="TMDB未匹配")
            continue
        lookup_keys = subscription_lookup_key_set(item)
        if not lookup_keys:
            skipped.append({"title": title, "reason": "缺少订阅目标"})
            continue
        series_key = subscription_series_lookup_key(item)
        if lookup_keys & existing_keys or lookup_keys & seen or (series_key and series_key in existing_series_keys):
            skipped.append({"title": title, "reason": "已订阅"})
            continue
        item = dict(item)
        item["source"] = "全球日播"
        item["source_key"] = "daily_airing"
        item["source_label"] = "全球日播"
        item["media_type"] = "tv"
        item["type"] = "电视剧"
        item["airing_today"] = True
        item["daily_airing_synced_at"] = now_text
        item.setdefault("created_at", now_text)
        item["updated_at"] = now_text
        dedupe_key = get_subscription_dedupe_key(item)
        if dedupe_key:
            item["dedupe_key"] = dedupe_key
        set_discover_item_cache(item, "daily_airing_subscription")
        added.append(item)
        seen.update(lookup_keys)

    next_items = added + existing_items
    payload = dict(data) if isinstance(data, dict) else {}
    payload["items"] = next_items
    payload["stats"] = recalc_subscription_stats(next_items)
    payload["last_run_at"] = data.get("last_run_at") if isinstance(data, dict) else ""
    payload["daily_airing_last_run_at"] = now_text
    payload["errors"] = errors
    payload = write_subscription_items_data(payload)

    payload["added_count"] = len(added)
    payload["skipped_count"] = len(skipped)
    payload["checked_count"] = len(rows)
    payload["added_items"] = added
    payload["skipped_items"] = skipped[:30]
    payload["message"] = f"全球日播检测完成：新增 {len(added)} 条，跳过 {len(skipped)} 条"
    status = "success" if added else "skip"
    write_activity(
        "subscription",
        "sync_daily_airing",
        status,
        payload["message"],
        checked=len(rows),
        added=len(added),
        skipped=len(skipped),
        first_added=added[0].get("title") if added else "",
    )
    subscription_task = queue_subscription_resource_rule_transfer(added, "daily_airing_sync") if added else _subscription_task_base(load_subscription_config())
    if not added:
        subscription_task["enabled"] = False
        subscription_task["reason"] = "没有新增订阅"
    payload["subscription_task"] = subscription_task
    payload["auto_transfer"] = subscription_task
    return payload


def find_subscription_item(key):
    data = load_subscription_items()
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    for item in items:
        if get_subscription_item_key(item) == key:
            return item
    return None


def pick_tmdb_search_result(results, title):
    rows = results if isinstance(results, list) else []
    if not rows:
        return None
    title_text = str(title or "").strip().lower()
    for row in rows:
        candidate = str(row.get("title") or row.get("name") or "").strip().lower()
        if candidate and candidate == title_text:
            return row
    return rows[0]


def read_subscription_detail_cache():
    if not os.path.exists(SUBSCRIPTION_DETAIL_CACHE_PATH):
        return {}
    try:
        with open(SUBSCRIPTION_DETAIL_CACHE_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_subscription_detail_cache(data):
    os.makedirs(os.path.dirname(SUBSCRIPTION_DETAIL_CACHE_PATH), exist_ok=True)
    with open(SUBSCRIPTION_DETAIL_CACHE_PATH, "w", encoding="utf-8") as fp:
        json.dump(data if isinstance(data, dict) else {}, fp, ensure_ascii=False, indent=2)


def subscription_detail_cache_key(item, key):
    media_type = discover_item_media_type(item) or ("tv" if str(item.get("media_type") or "").lower() == "tv" else "movie")
    tmdb_id = discover_item_tmdb_id(item, media_type)
    title = compact_match_text(item.get("title") or item.get("name") or "")
    if tmdb_id.isdigit():
        return f"{media_type}:tmdb:{tmdb_id}"
    return f"{media_type}:title:{title or key}"


def tmdb_season_number(season):
    try:
        return int(season.get("season_number") or 0)
    except Exception:
        return -1


def refresh_cached_subscription_library_state(item, detail, seasons):
    detail = deepcopy(detail) if isinstance(detail, dict) else {}
    seasons = deepcopy(seasons) if isinstance(seasons, list) else []
    media_type = detail.get("media_type") or ("tv" if str(item.get("media_type") or "").lower() == "tv" else "movie")
    title = detail.get("title") or item.get("title") or ""
    tmdb_id = detail.get("tmdb_id") or item.get("tmdb_id") or item.get("id") or ""
    library_status = get_library_item_status(title, tmdb_id, media_type)
    library_count = int(library_status.get("episode_count") or 0)
    detail["in_library"] = bool(library_status.get("in_library"))
    detail["library_episode_count"] = library_count
    detail["library_paths"] = get_library_paths(title, tmdb_id, media_type)
    if media_type == "tv" and seasons:
        library_map = get_library_season_episodes(title, tmdb_id)
        episode_info = get_library_episode_info(title, tmdb_id)
        for season in seasons:
            if not isinstance(season, dict):
                continue
            season_number = str(season.get("season_number") or "")
            library_eps = set(library_map.get(season_number) or [])
            season_episode_info = episode_info.get(season_number) or {}
            if library_eps:
                season["library_count"] = len(library_eps)
            elif season_episode_info:
                season["library_count"] = sum(1 for info in season_episode_info.values() if (info or {}).get("paths"))
            else:
                season["library_count"] = 0
            for episode in season.get("episodes") or []:
                if not isinstance(episode, dict):
                    continue
                ep_num = int(episode.get("episode_number") or 0)
                ep_info = season_episode_info.get(str(ep_num)) or {}
                episode["library_paths"] = ep_info.get("paths") or []
                episode["in_library"] = bool(episode["library_paths"]) or ep_num in library_eps
    return detail, seasons


def fetch_subscription_detail(query):
    key = str(query.get("key") or "").strip()
    if not key:
        raise RuntimeError("\u7f3a\u5c11\u8be6\u60c5\u76ee\u6807")
    item = find_subscription_item(key)
    if not item:
        raise RuntimeError("\u672a\u627e\u5230\u8be5\u8ba2\u9605")
    item = normalize_subscription_item_metadata(merge_cached_discover_item(item), resolve_tmdb=True)

    media_type = discover_item_media_type(item) or ("tv" if str(item.get("media_type") or "").lower() == "tv" else "movie")
    detail = {}
    seasons = []
    title = item.get("title") or ""
    cache_key = subscription_detail_cache_key(item, key)
    force_refresh = str(query.get("refresh") or query.get("force") or "").lower() in {"1", "true", "yes"}
    cache_data = read_subscription_detail_cache()
    cached = cache_data.get(cache_key) if isinstance(cache_data, dict) else None
    if isinstance(cached, dict) and cached.get("version") == SUBSCRIPTION_DETAIL_CACHE_VERSION and not force_refresh:
        cached_detail, cached_seasons = refresh_cached_subscription_library_state(
            item,
            cached.get("detail") or {},
            cached.get("seasons") or [],
        )
        return {
            "success": True,
            "item": item,
            "detail": cached_detail,
            "seasons": cached_seasons,
            "subscribed": True,
            "cache_hit": True,
            "cached_at": cached.get("cached_at") or 0,
        }

    cfg = load_tmdb_config()
    try:
        tmdb_id = discover_item_tmdb_id(item, media_type)
        match = {"id": tmdb_id} if tmdb_id else None
        if not match:
            search_url = f"{cfg['api_base_url']}/search/{media_type}?" + urllib.parse.urlencode({
                "api_key": cfg["api_key"],
                "language": "zh-CN",
                "include_adult": "false",
                "query": title,
                "page": "1",
            })
            search_data = http_json(search_url)
            match = pick_tmdb_search_result(search_data.get("results") or [], title)
        if match and match.get("id"):
            tmdb_id = str(match.get("id") or "")
            raw = get_cached_tmdb_detail(media_type, tmdb_id, "external_ids,credits")
            title_value = raw.get("title") or raw.get("name") or title
            date_value = raw.get("release_date") or raw.get("first_air_date") or ""
            runtime_value = ""
            if media_type == "tv":
                runtimes = raw.get("episode_run_time") or []
                runtime_value = f"{runtimes[0]} \u5206\u949f/\u96c6" if runtimes else ""
            else:
                runtime_value = f"{raw.get('runtime')} \u5206\u949f" if raw.get("runtime") else ""
            countries = raw.get("production_countries") or raw.get("origin_country") or []
            country_value = ""
            if countries and isinstance(countries[0], dict):
                country_value = countries[0].get("name") or countries[0].get("iso_3166_1") or ""
            elif countries:
                country_value = str(countries[0])
            languages = raw.get("spoken_languages") or []
            language_value = languages[0].get("english_name") if languages and isinstance(languages[0], dict) else (raw.get("original_language") or "")
            external = raw.get("external_ids") if isinstance(raw.get("external_ids"), dict) else {}
            credits = raw.get("credits") if isinstance(raw.get("credits"), dict) else {}
            cast = []
            for person in (credits.get("cast") or [])[:12]:
                if not isinstance(person, dict):
                    continue
                cast.append({
                    "name": person.get("name") or "",
                    "character": person.get("character") or "",
                    "profile_url": tmdb_image(person.get("profile_path"), "w185"),
                })
            if not cast:
                try:
                    credits_path = "aggregate_credits" if media_type == "tv" else "credits"
                    credit_data = http_json(f"{cfg['api_base_url']}/{media_type}/{tmdb_id}/{credits_path}?" + urllib.parse.urlencode({
                        "api_key": cfg["api_key"],
                        "language": "zh-CN",
                    }))
                    for person in (credit_data.get("cast") or [])[:12]:
                        if not isinstance(person, dict):
                            continue
                        roles = person.get("roles") if isinstance(person.get("roles"), list) else []
                        character = ""
                        if roles and isinstance(roles[0], dict):
                            character = roles[0].get("character") or ""
                        cast.append({
                            "name": person.get("name") or "",
                            "character": character or person.get("character") or "",
                            "profile_url": tmdb_image(person.get("profile_path"), "w185"),
                        })
                except Exception:
                    pass
            detail = {
                "tmdb_id": tmdb_id,
                "imdb_id": external.get("imdb_id") or raw.get("imdb_id") or "",
                "title": title_value,
                "original_title": raw.get("original_title") or raw.get("original_name") or "",
                "year": (date_value or "")[:4],
                "rating": f"{float(raw.get('vote_average') or 0):.1f}" if raw.get("vote_average") is not None else (item.get("rating") or ""),
                "overview": raw.get("overview") or "",
                "poster_url": tmdb_image(raw.get("poster_path"), "w342") or item.get("poster_url") or "",
                "backdrop_url": tmdb_image(raw.get("backdrop_path"), "w1280"),
                "genres": [g.get("name") for g in (raw.get("genres") or []) if isinstance(g, dict) and g.get("name")],
                "runtime": runtime_value,
                "status": raw.get("status") or "",
                "date": date_value,
                "country": country_value,
                "language": language_value,
                "season_count": int(raw.get("number_of_seasons") or 0),
                "episode_count": int(raw.get("number_of_episodes") or 0),
                "media_type": media_type,
                "cast": cast,
            }
            try:
                english_detail = http_json(f"{cfg['api_base_url']}/{media_type}/{tmdb_id}?" + urllib.parse.urlencode({
                    "api_key": cfg["api_key"],
                    "language": "en-US",
                }))
                detail["english_title"] = english_detail.get("title") or english_detail.get("name") or ""
            except Exception:
                detail["english_title"] = ""
            library_status = get_library_item_status(title_value, tmdb_id, media_type)
            detail["in_library"] = bool(library_status.get("in_library"))
            detail["library_episode_count"] = int(library_status.get("episode_count") or 0)
            detail["library_paths"] = get_library_paths(title_value, tmdb_id, media_type)
            if media_type == "tv":
                library_count = int(library_status.get("episode_count") or 0)
                episode_info = get_library_episode_info(title_value, tmdb_id)
                raw_seasons = sorted(
                    [s for s in (raw.get("seasons") or []) if isinstance(s, dict) and tmdb_season_number(s) >= 0],
                    key=tmdb_season_number,
                )
                for season in raw_seasons:
                    season_number = tmdb_season_number(season)
                    season_episode_info = episode_info.get(str(season_number)) or {}
                    season_library_count = sum(1 for info in season_episode_info.values() if (info or {}).get("paths"))
                    try:
                        season_data = get_cached_tmdb_season_detail(tmdb_id, season_number)
                        episodes = []
                        for ep in season_data.get("episodes", []) or []:
                            ep_num = int(ep.get("episode_number") or 0)
                            ep_info = season_episode_info.get(str(ep_num)) or {}
                            ep_paths = ep_info.get("paths") or []
                            episodes.append({
                                "episode_number": ep_num,
                                "title": ep.get("name") or f"\u7b2c {ep_num} \u96c6",
                                "overview": ep.get("overview") or "",
                                "air_date": ep.get("air_date") or "",
                                "runtime": ep.get("runtime") or "",
                                "in_library": bool(ep_paths),
                                "library_paths": ep_paths,
                            })
                        seasons.append({
                            "season_number": season_number,
                            "name": season_data.get("name") or season.get("name") or ("特别篇" if season_number == 0 else f"第 {season_number} 季"),
                            "overview": season_data.get("overview") or season.get("overview") or "",
                            "poster_url": tmdb_image(season_data.get("poster_path") or season.get("poster_path"), "w342"),
                            "air_date": season_data.get("air_date") or season.get("air_date") or "",
                            "episode_count": int(season_data.get("episodes", []) and len(season_data.get("episodes", [])) or season.get("episode_count") or 0),
                            "library_count": season_library_count,
                            "episodes": episodes,
                        })
                    except Exception:
                        seasons.append({
                            "season_number": season_number,
                            "name": season.get("name") or ("特别篇" if season_number == 0 else f"第 {season_number} 季"),
                            "overview": season.get("overview") or "",
                            "poster_url": tmdb_image(season.get("poster_path"), "w342"),
                            "air_date": season.get("air_date") or "",
                            "episode_count": int(season.get("episode_count") or 0),
                            "library_count": season_library_count,
                            "episodes": [],
                        })
    except Exception:
        detail = {}

    if not detail:
        detail = {
            "tmdb_id": "",
            "imdb_id": "",
            "title": title,
            "year": item.get("year") or "",
            "rating": item.get("rating") or "",
            "overview": "",
            "poster_url": item.get("poster_url") or "",
            "backdrop_url": "",
            "genres": [],
            "runtime": "",
            "status": "",
            "date": "",
            "country": "",
            "language": "",
            "season_count": 0,
            "episode_count": 0,
            "media_type": media_type,
            "cast": [],
            "in_library": bool(get_library_item_status(title, "", media_type).get("in_library")),
            "library_episode_count": int(get_library_item_status(title, "", media_type).get("episode_count") or 0),
            "library_paths": get_library_paths(title, "", media_type),
        }
    elif detail.get("tmdb_id"):
        cache_data[cache_key] = {
            "version": SUBSCRIPTION_DETAIL_CACHE_VERSION,
            "detail": detail,
            "seasons": seasons,
            "cached_at": int(time.time()),
            "item_title": item.get("title") or "",
        }
        try:
            write_subscription_detail_cache(cache_data)
        except Exception:
            pass
    return {"success": True, "item": item, "detail": detail, "seasons": seasons, "subscribed": True, "cache_hit": False}


def subscription_calendar_date(value):
    text = str(value or "").strip()
    match = re.match(r"^((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not match:
        return ""
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def subscription_calendar_progress_text(item):
    total = first_positive_int(
        item.get("episode_total"),
        item.get("total_episodes"),
        item.get("episodes_total"),
        item.get("episode_count"),
    )
    current = first_positive_int(
        item.get("library_episode_count"),
        item.get("current_episode_count"),
        item.get("aired_episode_count"),
        item.get("latest_episode"),
    )
    if total > 0:
        return f"{current}/{total}"
    return f"{current}" if current else "0/?"


def subscription_calendar_target_season(item, tmdb_id):
    target = subscription_target_season(item)
    if target is not None:
        return target
    try:
        detail = get_cached_tmdb_detail("tv", tmdb_id, fetch=True)
    except Exception:
        detail = {}
    seasons = [
        tmdb_season_number(season)
        for season in (detail.get("seasons") or [])
        if isinstance(season, dict) and tmdb_season_number(season) > 0
    ]
    return max(seasons or [1])


def build_subscription_calendar_entries_for_item(item, year, month, media_filter="all"):
    if not isinstance(item, dict):
        return [], ""
    media_type = discover_item_media_type(item) or ("tv" if str(item.get("media_type") or "").lower() == "tv" else "movie")
    if media_filter in {"movie", "tv"} and media_type != media_filter:
        return [], ""
    key = get_subscription_item_key(item)
    title = item.get("title") or item.get("name") or ""
    source_def = DOUBAN_SUBSCRIPTION_SOURCES.get(item.get("source_key") or "") or {}
    source_label = item.get("source_label") or source_def.get("label") or item.get("source") or ""
    poster = item.get("poster_url") or item.get("poster") or ""
    tmdb_id = discover_item_tmdb_id(item, media_type)
    subscription_created_at = item.get("subscribed_at") or item.get("created_at") or item.get("createdAt") or ""
    scope_value = item.get("follow_scope_explicit", True)
    follow_scope_explicit = (
        scope_value
        if isinstance(scope_value, bool)
        else str(scope_value).strip().lower() not in {"0", "false", "off", "no"}
    )
    past_value = item.get("include_past_episodes", item.get("backfill", item.get("allow_backfill", False)))
    include_past_episodes = (
        past_value
        if isinstance(past_value, bool)
        else str(past_value).strip().lower() in {"1", "true", "on", "yes"}
    )
    try:
        allowed_delay_hours = max(0, int(item.get("allowed_delay_hours", item.get("grace_hours", 24))))
    except (TypeError, ValueError):
        allowed_delay_hours = 24
    entries = []

    if media_type == "movie":
        date = subscription_calendar_date(item.get("release_date") or item.get("date") or item.get("air_date"))
        if date.startswith(f"{year:04d}-{month:02d}-"):
            entries.append({
                "date": date,
                "key": key,
                "title": title,
                "media_type": media_type,
                "poster_url": poster,
                "tmdb_id": tmdb_id,
                "source_label": source_label,
                "episode_label": "电影上映",
                "progress_text": "1/1" if item.get("in_library") else "0/1",
                "in_library": bool(item.get("in_library")),
                "subscription_created_at": subscription_created_at,
                "follow_scope_explicit": follow_scope_explicit,
                "include_past_episodes": include_past_episodes,
                "allowed_delay_hours": allowed_delay_hours,
            })
        return entries, ""

    if not str(tmdb_id or "").isdigit():
        return [], f"{title} 缺少 TMDB ID，无法生成播出日历"

    season_number = subscription_calendar_target_season(item, tmdb_id)
    try:
        season_data = get_cached_tmdb_season_detail(tmdb_id, season_number)
    except Exception as exc:
        return [], f"{title} 第 {season_number} 季日历加载失败：{exc}"
    episodes = season_data.get("episodes") if isinstance(season_data, dict) else []
    if not isinstance(episodes, list) or not episodes:
        return [], f"{title} 第 {season_number} 季没有分集播出日期"

    library_episode_info = get_library_episode_info(title, tmdb_id)
    season_episode_info = library_episode_info.get(str(int(season_number or 0))) or {}
    season_name = item.get("season_name") or season_data.get("name") or (f"第 {season_number} 季" if season_number else "特别篇")
    progress = subscription_calendar_progress_text(item)
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        date = subscription_calendar_date(episode.get("air_date"))
        if not date.startswith(f"{year:04d}-{month:02d}-"):
            continue
        episode_number = first_positive_int(episode.get("episode_number"))
        episode_library_info = season_episode_info.get(str(int(episode_number or 0))) or {}
        episode_paths = [path for path in (episode_library_info.get("paths") or []) if str(path or "").strip()]
        entries.append({
            "date": date,
            "key": key,
            "title": title,
            "media_type": media_type,
            "poster_url": poster,
            "tmdb_id": tmdb_id,
            "source_label": source_label,
            "season_number": season_number,
            "season_name": season_name,
            "episode_number": episode_number,
            "episode_title": episode.get("name") or "",
            "episode_label": f"S{int(season_number):02d}E{int(episode_number):02d}" if episode_number else f"第 {season_number} 季",
            "progress_text": progress,
            "in_library": bool(episode_paths),
            "library_paths": episode_paths,
            "subscription_created_at": subscription_created_at,
            "follow_scope_explicit": follow_scope_explicit,
            "include_past_episodes": include_past_episodes,
            "allowed_delay_hours": allowed_delay_hours,
        })
    if not entries:
        return [], ""
    return entries, ""


def build_subscription_calendar(year=None, month=None, media_type="all"):
    now = datetime.now(BEIJING_TZ)
    try:
        year_value = int(year or now.year)
    except Exception:
        year_value = now.year
    try:
        month_value = int(month or now.month)
    except Exception:
        month_value = now.month
    month_value = max(1, min(12, month_value))
    media_filter = "tv" if str(media_type or "").lower() == "tv" else ("movie" if str(media_type or "").lower() == "movie" else "all")
    data = load_subscription_items(with_progress=False)
    items = [item for item in (data.get("items") or []) if isinstance(item, dict)]
    entries = []
    errors = []
    for item in items:
        try:
            rows, error = build_subscription_calendar_entries_for_item(item, year_value, month_value, media_filter)
            entries.extend(rows)
            if error:
                errors.append(error)
        except Exception as exc:
            title = item.get("title") or item.get("name") or ""
            errors.append(f"{title or '订阅'} 日历生成失败：{exc}")
    entries.sort(key=lambda row: (
        row.get("date") or "",
        str(row.get("title") or ""),
        int(row.get("season_number") or 0),
        int(row.get("episode_number") or 0),
    ))
    title_count = len({(row.get("key") or row.get("title") or "") for row in entries})
    return {
        "success": True,
        "year": year_value,
        "month": month_value,
        "type": media_filter,
        "entries": entries,
        "stats": {
            "entries": len(entries),
            "titles": title_count,
            "in_library": sum(1 for row in entries if row.get("in_library")),
            "pending": sum(1 for row in entries if not row.get("in_library")),
        },
        "errors": errors[:20],
    }


def fetch_douban_subscription_source(source_key, limit=24):
    source = DOUBAN_SUBSCRIPTION_SOURCES.get(source_key)
    if not source:
        return []
    params = {
        "type": source["media_type"],
        "tag": source["tag"],
        "sort": source["sort"],
        "page_limit": str(limit),
        "page_start": "0",
    }
    url = "https://movie.douban.com/j/search_subjects?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://movie.douban.com/explore",
    })
    with urllib.request.urlopen(req, timeout=18) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    rows = []
    for subject in data.get("subjects", []) or []:
        if not isinstance(subject, dict):
            continue
        title = clean_html(subject.get("title") or "")
        if not title:
            continue
        rate_text = clean_html(subject.get("rate") or "")
        try:
            rating_num = float(rate_text) if rate_text else 0.0
        except Exception:
            rating_num = 0.0
        rows.append({
            "id": str(subject.get("id") or ""),
            "title": title,
            "year": extract_year(subject.get("year"), subject.get("card_subtitle"), subject.get("episodes_info"), subject.get("url")) or current_year(),
            "type": "\u7535\u89c6\u5267" if source["media_type"] == "tv" else "\u7535\u5f71",
            "media_type": source["media_type"],
            "rating": rate_text,
            "rating_num": rating_num,
            "poster_url": subject.get("cover") or "",
            "url": subject.get("url") or "",
            "episodes_info": clean_html(subject.get("episodes_info") or ""),
            "source_key": source_key,
            "source_label": source["label"],
        })
    return rows


def normalize_subscription_dedupe_title(title):
    title = clean_html(title)
    title = re.sub(r"[（(]\s*(?:19|20)\d{2}\s*[）)]", "", title)
    title = re.sub(r"\s+", "", title)
    title = re.sub(r"[·•：:，,。.!！?？《》\"'“”‘’\-\[\]【】_]", "", title)
    return title.lower()


def get_subscription_dedupe_key(item):
    media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
    if "剧" in media_type or media_type == "tv":
        media_type = "tv"
    elif "电影" in media_type or media_type == "movie":
        media_type = "movie"
    title_value = item.get("title") or ""
    if media_type == "tv":
        title_value, _ = split_subscription_season_title(title_value)
    title_key = normalize_subscription_dedupe_title(title_value)
    tmdb_id = discover_item_tmdb_id(item, media_type)
    if media_type == "tv" and title_key and tmdb_id:
        season = subscription_target_season(item)
        season_suffix = f":season:{season}" if season is not None else ""
        return f"tv:{title_key}:tmdb:{tmdb_id}{season_suffix}"
    if title_key:
        return f"{media_type}:{title_key}"
    return str(item.get("id") or "")


def fetch_subscription_source(source_key, limit=24):
    if source_key in DAILY_AIRING_SUBSCRIPTION_SOURCES:
        return fetch_daily_airing_subscription_source(max(limit, 72))
    if source_key in DOUBAN_SUBSCRIPTION_SOURCES:
        return fetch_douban_subscription_source(source_key, limit)
    source = PLATFORM_SUBSCRIPTION_SOURCES.get(source_key)
    if not source:
        return []
    rows = fetch_platform_hot({
        "platform": source["platform"],
        "page": "1",
        "limit": str(limit),
    }).get("items") or []
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["source_key"] = source_key
        item["source_label"] = source["label"]
        item["media_type"] = "tv"
        item["type"] = "电视剧"
        normalized.append(item)
    return normalized


def fetch_daily_airing_subscription_source(limit=72):
    limit = parse_positive_int(limit, 72, 1, 120)
    data = fetch_daily_airing_all({"timezone": "Asia/Shanghai", "max_pages": "8"})
    rows = []
    for row in (data.get("items") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["source"] = "全球日播"
        item["source_key"] = "daily_airing"
        item["source_label"] = "全球日播"
        item["media_type"] = "tv"
        item["type"] = "电视剧"
        rows.append(item)
    return rows


def subscription_row_year(row):
    year = extract_year(row.get("year") or "")
    if year:
        return year
    for value in (row.get("release_date"), row.get("first_air_date"), row.get("air_date"), row.get("date")):
        year = extract_year(value or "")
        if year:
            return year
    return ""


def subscription_row_rating(row):
    for key in ("rating_num", "rating", "vote_average", "score"):
        value = row.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        if match:
            try:
                return float(match.group(0))
            except Exception:
                pass
    return 0.0


def add_subscription_run_issue(errors, message, status="skip", action="skip_subscription", category="subscription", **meta):
    text = str(message or "").strip()
    if not text:
        return
    if text not in errors:
        errors.append(text)
        write_activity(category, action, status, text, **meta)


def _subscription_task_base(config, rules=None):
    mode = normalize_subscription_mode((config or {}).get("mode") if isinstance(config, dict) else "")
    rules = normalize_resource_rules(rules if rules is not None else ((config or {}).get("resource_rules") if isinstance(config, dict) else None))
    return {
        "enabled": True,
        "mode": mode,
        "label": subscription_mode_label(mode),
        "task_label": subscription_mode_task_label(mode),
        "searched": 0,
        "matched": 0,
        "transferred": 0,
        "pushed": 0,
        "fallback_pushed": 0,
        "skipped": 0,
        "errors": [],
        "rule": resource_rules_required_summary(rules),
    }


def subscription_postprocess_category(mode):
    mode = normalize_subscription_mode(mode)
    if mode in {"moviepilot", "torra", "symedia"}:
        return "push"
    return "transfer"


def _subscription_item_title(item):
    return str((item or {}).get("title") or (item or {}).get("name") or "").strip()


def push_subscription_to_provider(item, provider, reason="", errors=None, skip_existing=False):
    provider_key = str(provider or "").strip().lower()
    if provider_key not in {"moviepilot", "torra", "symedia"}:
        provider_key = "moviepilot"
    provider_label = {"moviepilot": "MoviePilot", "torra": "Torra", "symedia": "Symedia"}.get(provider_key, "MoviePilot")
    title = _subscription_item_title(item)
    try:
        if provider_key == "torra":
            from app.services import torra_subscribe
            data = torra_subscribe({"item": item, "auto": False, "skip_existing": bool(skip_existing)})
        elif provider_key == "symedia":
            from app.services import symedia_subscribe
            data = symedia_subscribe({"item": item, "auto": False, "skip_existing": bool(skip_existing)})
        else:
            from app.services import moviepilot_subscribe
            data = moviepilot_subscribe({"item": item, "auto": False, "skip_existing": bool(skip_existing)})
        pushed = bool(data.get("pushed"))
        already_exists = bool(data.get("already_exists"))
        skipped_existing = bool(skip_existing and already_exists and not pushed)
        ok = bool(data.get("ok") or pushed or skipped_existing)
        status = "skip" if skipped_existing else ("success" if pushed or ok else "error")
        message = str(data.get("message") or data.get("skipped") or ("推送成功" if pushed else "推送未执行"))
        write_activity(
            "push",
            "subscription_mode_push",
            status,
            f"{provider_label} {'已有订阅，跳过' if skipped_existing else ('推送成功' if pushed or ok else '推送失败')}：{title}",
            title=title,
            target=provider_label,
            reason=reason,
            result_message=message,
            already_exists=already_exists,
        )
        return {
            "ok": ok,
            "pushed": pushed,
            "already_exists": already_exists,
            "skipped_existing": skipped_existing,
            "provider": provider_key,
            "provider_label": provider_label,
            "message": message,
            "data": data,
        }
    except Exception as exc:
        message = str(exc)
        write_activity(
            "push",
            "subscription_mode_push",
            "error",
            f"{provider_label} 推送失败：{title}",
            title=title,
            target=provider_label,
            reason=reason,
            error=message,
        )
        if errors is not None:
            add_subscription_run_issue(errors, f"{provider_label}推送失败: {title}: {message}", "error", "subscription_mode_push", category="push", title=title, target=provider_label, reason=reason, error=message)
        return {
            "ok": False,
            "provider": provider_key,
            "provider_label": provider_label,
            "message": message,
            "error": message,
        }


def subscription_pt_fallback_providers(config):
    douban = (config or {}).get("douban") if isinstance(config, dict) else {}
    target = str((douban or {}).get("pt_target") or "").strip().lower()
    if target == "torra":
        return ("torra", "moviepilot")
    if target == "moviepilot":
        return ("moviepilot", "torra")
    return ("moviepilot", "torra")


def push_subscription_pt_fallback(item, reason="", errors=None, providers=None):
    result = {
        "ok": False,
        "provider": "",
        "message": "",
        "attempts": [],
    }
    for provider in (providers or ("moviepilot", "torra")):
        attempt = push_subscription_to_provider(item, provider, reason, errors=None)
        result["attempts"].append(attempt)
        if attempt.get("ok"):
            result.update({
                "ok": True,
                "provider": attempt.get("provider"),
                "provider_label": attempt.get("provider_label"),
                "message": attempt.get("message") or "",
            })
            return result
    title = _subscription_item_title(item)
    message = "MoviePilot 与 Torra 都未推送成功"
    result["message"] = message
    write_activity(
        "push",
        "subscription_pt_fallback",
        "error",
        f"PT 兜底推送失败：{title}",
        title=title,
        reason=reason,
        error=message,
    )
    if errors is not None:
        add_subscription_run_issue(errors, f"PT兜底失败: {title}: {message}", "error", "subscription_pt_fallback", category="push", title=title, reason=reason)
    return result


def apply_subscription_pt_fallback(item, result, errors, reason="", providers=None):
    fallback = push_subscription_pt_fallback(item, reason, errors, providers)
    if fallback.get("ok"):
        result["fallback_pushed"] += 1
        result["pushed"] += 1
    else:
        title = _subscription_item_title(item)
        result["errors"].append(f"{title}: PT兜底失败")
    return fallback


def run_subscription_provider_push(items, config, errors, provider, skip_existing=False):
    result = _subscription_task_base(config)
    provider_key = str(provider or "").strip().lower()
    if provider_key not in {"moviepilot", "torra", "symedia"}:
        provider_key = "moviepilot"
    provider_label = {"moviepilot": "MoviePilot", "torra": "Torra", "symedia": "Symedia"}.get(provider_key, "MoviePilot")
    rows = [item for item in (items or []) if isinstance(item, dict)]
    for item in rows:
        title = _subscription_item_title(item)
        if not title:
            result["skipped"] += 1
            continue
        data = push_subscription_to_provider(item, provider_key, f"{result['label']} 后台执行", errors, skip_existing=skip_existing)
        if data.get("skipped_existing"):
            result["skipped"] += 1
        elif data.get("ok"):
            result["pushed"] += 1
        else:
            result["skipped"] += 1
            result["errors"].append(f"{title}: {provider_label}推送失败")
    return result


def run_subscription_resource_rule_transfer(items, config, errors):
    rules = normalize_resource_rules((config or {}).get("resource_rules") if isinstance(config, dict) else None)
    result = _subscription_task_base(config, rules)
    result["enabled"] = bool(rules.get("enabled") and rules.get("auto_transfer"))
    mode = normalize_subscription_mode((config or {}).get("mode") if isinstance(config, dict) else "")
    fallback_enabled = mode == "resource_then_pt"
    fallback_providers = subscription_pt_fallback_providers(config)
    if not result["enabled"]:
        if fallback_enabled:
            rows = [item for item in (items or []) if isinstance(item, dict)]
            result["enabled"] = True
            for item in rows:
                title = _subscription_item_title(item)
                if not title:
                    result["skipped"] += 1
                    continue
                apply_subscription_pt_fallback(item, result, errors, "资源规则未启用，直接转推 PT", fallback_providers)
            return result
        return result
    max_per_run = int(rules.get("max_per_run") or 8)
    rows = [item for item in (items or []) if isinstance(item, dict)]
    for item in rows:
        if result["searched"] >= max_per_run:
            break
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        media_type = discover_item_media_type(item) or str(item.get("media_type") or item.get("type") or "movie").lower()
        media_type = "tv" if media_type in ("tv", "电视剧", "series") or "剧" in media_type else "movie"
        query = {
            "title": title,
            "type": media_type,
            "year": item.get("year") or item.get("date") or "",
            "tmdb_id": discover_item_tmdb_id(item, media_type),
        }
        result["searched"] += 1
        try:
            data = search_channel_resources(query)
            candidates = []
            reject_reasons = []
            for row in data.get("items") or []:
                ok, reason = resource_matches_subscription_rules(row, rules, title)
                if ok:
                    candidates.append(row)
                elif len(reject_reasons) < 3:
                    reject_reasons.append(reason)
            candidates = sort_resource_rule_matches(candidates)
            if not candidates:
                result["skipped"] += 1
                reason = "；".join(dict.fromkeys(reject_reasons)) or "未搜索到符合精准格式的资源"
                write_activity("transfer", "auto_transfer_match", "skip", f"未命中精准资源：{title}", title=title, reason=reason, rule=result["rule"])
                if fallback_enabled:
                    apply_subscription_pt_fallback(item, result, errors, f"无可转存资源：{reason}", fallback_providers)
                continue
            chosen = None
            transfer_reason = ""
            for row in candidates:
                transferable, transfer_reason = resource_transferable_for_auto(row)
                if transferable:
                    chosen = row
                    break
            if not chosen:
                result["skipped"] += 1
                write_activity("transfer", "auto_transfer_match", "skip", f"精准资源不可自动转存：{title}", title=title, reason=transfer_reason, rule=result["rule"])
                if fallback_enabled:
                    apply_subscription_pt_fallback(item, result, errors, f"资源不可自动转存：{transfer_reason}", fallback_providers)
                continue
            result["matched"] += 1
            from app.services import transfer_yingchao_item
            transfer_result = transfer_yingchao_item(chosen)
            if transfer_result.get("ok"):
                result["transferred"] += 1
                write_activity(
                    "transfer",
                    "auto_transfer_resource",
                    "success",
                    f"精准命中并已转存：{title}",
                    title=title,
                    resource_title=chosen.get("title") or "",
                    rule=result["rule"],
                    share_url=transfer_result.get("share_url") or chosen.get("url") or "",
                )
            else:
                result["errors"].append(f"{title}: 转存失败")
                write_activity("transfer", "auto_transfer_resource", "error", f"精准资源转存失败：{title}", title=title, resource_title=chosen.get("title") or "", rule=result["rule"])
                if fallback_enabled:
                    apply_subscription_pt_fallback(item, result, errors, "精准资源转存失败", fallback_providers)
        except Exception as exc:
            message = f"{title}: {exc}"
            result["errors"].append(message)
            write_activity("transfer", "auto_transfer_resource", "error", f"精准资源自动转存异常：{title}", title=title, error=str(exc), rule=result["rule"])
            if fallback_enabled:
                apply_subscription_pt_fallback(item, result, errors, f"资源搜索或转存异常：{exc}", fallback_providers)
    if result["errors"]:
        for message in result["errors"]:
            add_subscription_run_issue(errors, f"精准转存: {message}", "error", "auto_transfer_resource", category="transfer")
    return result


def run_subscription_mode_task(items, config, errors, trigger="subscription_saved"):
    mode = normalize_subscription_mode((config or {}).get("mode") if isinstance(config, dict) else "")
    trigger_key = str(trigger or "")
    skip_existing = trigger_key.startswith("mode_switch") or trigger_key in {
        "subscription_search_poll",
        "subscription_run",
        "daily_airing_sync",
    }
    if mode == "moviepilot":
        return run_subscription_provider_push(items, config, errors, "moviepilot", skip_existing=skip_existing)
    if mode == "torra":
        return run_subscription_provider_push(items, config, errors, "torra", skip_existing=skip_existing)
    if mode == "symedia":
        return run_subscription_provider_push(items, config, errors, "symedia", skip_existing=skip_existing)
    return run_subscription_resource_rule_transfer(items, config, errors)


def queue_subscription_resource_rule_transfer(items, trigger="subscription_saved", config_override=None):
    config = config_override if isinstance(config_override, dict) else load_subscription_config()
    mode = normalize_subscription_mode(config.get("mode") if isinstance(config, dict) else "")
    rules = normalize_resource_rules(config.get("resource_rules") if isinstance(config, dict) else None)
    if mode in {"moviepilot", "torra", "symedia"}:
        enabled = True
    elif mode == "resource_then_pt":
        enabled = True
    else:
        enabled = bool(rules.get("enabled") and rules.get("auto_transfer"))
    result = {
        "enabled": enabled,
        "mode": mode,
        "label": subscription_mode_label(mode),
        "task_label": subscription_mode_task_label(mode),
        "queued": 0,
        "skipped": 0,
        "searched": 0,
        "matched": 0,
        "transferred": 0,
        "pushed": 0,
        "fallback_pushed": 0,
        "errors": [],
        "rule": resource_rules_required_summary(rules),
        "background": True,
    }
    if mode == "torra":
        from app.config import read_config

        torra_push_enabled = str(read_config().get("TORRA_PUSH_ENABLED") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not torra_push_enabled:
            result["enabled"] = False
            result["reason"] = "允许向 Torra 创建订阅已关闭"
            return result
    if not enabled:
        result["reason"] = "资源规则未启用"
        return result
    rows = [item for item in (items or []) if isinstance(item, dict)]
    max_per_run = int(rules.get("max_per_run") or 8)
    if mode not in {"moviepilot", "torra", "symedia"} and trigger not in {"manual_subscription", "mode_switch_provider"}:
        rows = rows[:max_per_run]
    queued = []
    queued_keys = []
    with SUBSCRIPTION_RESOURCE_TASK_LOCK:
        for item in rows:
            item_key = get_subscription_item_key(item) or get_subscription_dedupe_key(item) or str(item.get("title") or item.get("name") or "")
            key = f"{mode}:{item_key}" if item_key else ""
            if not key:
                result["skipped"] += 1
                continue
            if key in SUBSCRIPTION_RESOURCE_TASK_KEYS:
                result["skipped"] += 1
                continue
            SUBSCRIPTION_RESOURCE_TASK_KEYS.add(key)
            queued.append(dict(item))
            queued_keys.append(key)
    result["queued"] = len(queued)
    if not queued:
        return result
    postprocess_category = subscription_postprocess_category(mode)
    write_activity(
        postprocess_category,
        "subscription_postprocess_queue",
        "start",
        f"订阅后处理已排队：{subscription_mode_task_label(mode)} {len(queued)} 条",
        total=len(queued),
        trigger=trigger,
        mode=subscription_mode_label(mode),
        task=subscription_mode_task_label(mode),
        rule=result["rule"],
        first_title=queued[0].get("title") or queued[0].get("name") or "",
    )
    delay_seconds = 1.5
    result["delay_seconds"] = delay_seconds
    timer = threading.Timer(
        delay_seconds,
        _subscription_resource_rule_transfer_worker,
        args=(queued, queued_keys, config, trigger),
    )
    timer.name = "subscription-postprocess"
    timer.daemon = True
    timer.start()
    return result


def _subscription_resource_rule_transfer_worker(items, keys, config, trigger):
    errors = []
    try:
        result = run_subscription_mode_task(items, config, errors, trigger)
        success_count = int(result.get("transferred") or 0) + int(result.get("pushed") or 0) + int(result.get("fallback_pushed") or 0)
        status = "success" if success_count else ("skip" if not result.get("errors") else "error")
        write_activity(
            subscription_postprocess_category(result.get("mode") or (config or {}).get("mode")),
            "subscription_postprocess_background",
            status,
            f"订阅后处理完成：{result.get('task_label') or subscription_mode_task_label((config or {}).get('mode'))} {len(items)} 条",
            total=len(items),
            trigger=trigger,
            mode=result.get("label") or subscription_mode_label((config or {}).get("mode")),
            task=result.get("task_label") or "",
            searched=result.get("searched"),
            matched=result.get("matched"),
            transferred=result.get("transferred"),
            pushed=result.get("pushed"),
            fallback_pushed=result.get("fallback_pushed"),
            skipped=result.get("skipped"),
            errors=len(result.get("errors") or []),
            rule=result.get("rule") or "",
        )
    except Exception as exc:
        write_activity(
            subscription_postprocess_category((config or {}).get("mode") if isinstance(config, dict) else ""),
            "subscription_postprocess_background",
            "error",
            f"订阅模式后台任务异常：{exc}",
            total=len(items or []),
            trigger=trigger,
            mode=subscription_mode_label((config or {}).get("mode") if isinstance(config, dict) else ""),
            error=str(exc),
        )
    finally:
        with SUBSCRIPTION_RESOURCE_TASK_LOCK:
            for key in keys or []:
                SUBSCRIPTION_RESOURCE_TASK_KEYS.discard(key)


def run_subscription_now():
    write_activity("subscription", "run_subscription", "start", "开始刷新订阅")
    config = load_subscription_config()
    douban = config.get("douban") if isinstance(config, dict) else {}
    if not isinstance(douban, dict):
        douban = {}
    if not douban.get("enabled"):
        write_activity("subscription", "run_subscription", "error", "订阅刷新失败：请先启用自动订阅", reason="请先启用自动订阅")
        raise RuntimeError("\u8bf7\u5148\u542f\u7528\u81ea\u52a8\u8ba2\u9605")
    daily_only = False
    selected_sources = [] if daily_only else [str(s) for s in (douban.get("sources") or []) if str(s) in SUBSCRIPTION_SOURCES]
    if not daily_only and not selected_sources:
        write_activity("subscription", "run_subscription", "error", "订阅刷新失败：请先选择订阅来源", reason="请先选择订阅来源")
        raise RuntimeError("\u8bf7\u5148\u9009\u62e9\u8ba2\u9605\u6765\u6e90")

    movie_years = {str(y).strip() for y in (douban.get("movie_years") or []) if str(y).strip()}
    try:
        tv_min_rating = float(douban.get("tv_min_rating") or 0)
    except Exception:
        tv_min_rating = 0.0
    exclude_titles = parse_subscription_exclude_titles(douban.get("exclude_titles"))

    items = []
    seen = set()
    errors = []
    if daily_only:
        try:
            rows = fetch_daily_airing_subscription_source(72)
        except Exception as exc:
            add_subscription_run_issue(errors, f"全球日播: {exc}", "error", "fetch_subscription_source", source="全球日播", error=str(exc))
            rows = []
        for row in rows:
            row = normalize_subscription_item_metadata(row, resolve_tmdb=True)
            exclude_match = subscription_exclusion_match(row, exclude_titles)
            if exclude_match:
                title = row.get("title") or row.get("source_title") or row.get("name") or ""
                add_subscription_run_issue(
                    errors,
                    f"排除订阅，跳过：{title}",
                    "skip",
                    "skip_subscription",
                    title=title,
                    source="全球日播",
                    reason="排除订阅",
                    exclude=exclude_match,
                )
                continue
            if not subscription_has_required_tmdb(row):
                title = row.get("title") or row.get("source_title") or row.get("name") or ""
                add_subscription_run_issue(
                    errors,
                    f"TMDB未匹配，跳过订阅：{title}",
                    "skip",
                    "skip_subscription",
                    title=title,
                    source="全球日播",
                    reason="TMDB 未匹配",
                    source_title=row.get("source_title") or "",
                )
                continue
            dedupe_key = get_subscription_dedupe_key(row)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            row["dedupe_key"] = dedupe_key
            items.append(row)
    for source_key in selected_sources:
        source = SUBSCRIPTION_SOURCES[source_key]
        try:
            rows = fetch_subscription_source(source_key, 24)
        except Exception as exc:
            add_subscription_run_issue(errors, f"{source['label']}: {exc}", "error", "fetch_subscription_source", source=source.get("label") or source_key, error=str(exc))
            continue
        for row in rows:
            if row["media_type"] == "tv" and not douban.get("tv_enabled", True):
                continue
            if row["media_type"] == "movie" and not douban.get("movie_enabled", True):
                continue
            row = normalize_subscription_item_metadata(row, resolve_tmdb=True)
            exclude_match = subscription_exclusion_match(row, exclude_titles)
            if exclude_match:
                title = row.get("title") or row.get("source_title") or row.get("name") or ""
                add_subscription_run_issue(
                    errors,
                    f"排除订阅，跳过：{title}",
                    "skip",
                    "skip_subscription",
                    title=title,
                    source=source.get("label") or source_key,
                    reason="排除订阅",
                    exclude=exclude_match,
                )
                continue
            if not subscription_has_required_tmdb(row):
                title = row.get("title") or row.get("source_title") or row.get("name") or ""
                add_subscription_run_issue(
                    errors,
                    f"TMDB未匹配，跳过订阅：{title}",
                    "skip",
                    "skip_subscription",
                    title=title,
                    source=source.get("label") or source_key,
                    reason="TMDB 未匹配",
                    source_title=row.get("source_title") or "",
                )
                continue
            row_media_type = discover_item_media_type(row) or str(row.get("media_type") or row.get("type") or "").strip().lower()
            row_media_type = "tv" if row_media_type in ("tv", "电视剧", "series") or "剧" in row_media_type else "movie"
            row_year = subscription_row_year(row)
            if row_media_type == "movie" and movie_years and row_year not in movie_years:
                continue
            row_rating = subscription_row_rating(row)
            if row_media_type == "tv" and tv_min_rating and row_rating < tv_min_rating:
                continue
            dedupe_key = get_subscription_dedupe_key(row)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            row["dedupe_key"] = dedupe_key
            items.append(row)

    now_text = beijing_now_text()
    stats = {
        "total": len(items),
        "movie": sum(1 for item in items if item.get("media_type") == "movie"),
        "tv": sum(1 for item in items if item.get("media_type") == "tv"),
        "sources": 1 if daily_only else len(selected_sources),
        "daily_only": daily_only,
    }
    existing_payload = load_subscription_items(remove_completed=False, persist_progress=False)
    merged_items, merge_stats = merge_subscription_source_items(
        existing_payload.get("items") if isinstance(existing_payload, dict) else [],
        items,
    )
    payload = {
        "success": True,
        "last_run_at": now_text,
        "stats": stats,
        "items": merged_items,
        "errors": errors,
        "source_merge": merge_stats,
    }
    for item in merged_items:
        set_discover_item_cache(item, "subscription_task")
    payload = enrich_subscription_items(payload, remove_completed=False)
    write_subscription_items_data(payload)
    config["douban"]["last_run_at"] = now_text
    write_subscription_config_data(config)
    payload["config"] = config
    summary_status = "success" if not errors else ("skip" if items else "error")
    write_activity(
        "subscription",
        "run_subscription",
        summary_status,
        f"订阅刷新完成：{len(items)} 条，跳过/错误 {len(errors)} 条",
        total=len(items),
        movie=stats.get("movie"),
        tv=stats.get("tv"),
        skipped=len(errors),
        mode=subscription_mode_label(config.get("mode")),
        task=subscription_mode_task_label(config.get("mode")),
        first_error=errors[0] if errors else "",
    )
    subscription_task = queue_subscription_resource_rule_transfer(items, "subscription_run")
    payload["subscription_task"] = subscription_task
    payload["auto_transfer"] = subscription_task
    return payload


def run_due_subscription_task():
    config = load_subscription_config()
    douban = config.get("douban") if isinstance(config, dict) else {}
    if not isinstance(douban, dict):
        return {"ok": False, "ran": False, "reason": "invalid_config"}
    if not douban.get("enabled") or not douban.get("task_enabled"):
        return {"ok": True, "ran": False, "reason": "disabled"}
    task_time = str(douban.get("task_time") or "08:30").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", task_time):
        return {"ok": False, "ran": False, "reason": "invalid_time"}
    today = beijing_now_text("%Y-%m-%d")
    now_hm = beijing_now_text("%H:%M")
    last_run = str(douban.get("last_run_at") or "")
    if last_run.startswith(today):
        return {"ok": True, "ran": False, "reason": "already_ran_today"}
    if now_hm < task_time:
        return {"ok": True, "ran": False, "reason": "not_due"}
    result = run_subscription_now()
    return {"ok": True, "ran": True, "result": result}


def _config_interval_minutes(key, default_value, minimum=1):
    try:
        from app.config import read_config
        cfg = read_config()
        value = int(float(str(cfg.get(key) or default_value).strip()))
    except Exception:
        value = int(default_value)
    return max(int(minimum), value)


def _poll_due(state_key, interval_minutes):
    now = time.time()
    last = float(SUBSCRIPTION_POLL_STATE.get(state_key) or 0)
    if not last:
        SUBSCRIPTION_POLL_STATE[state_key] = now
        return False
    if now - last < max(1, int(interval_minutes)) * 60:
        return False
    SUBSCRIPTION_POLL_STATE[state_key] = now
    return True


def queue_subscription_channel_resource_search(items, trigger="subscription_search_poll"):
    config = load_subscription_config()
    if not isinstance(config, dict):
        config = {}
    channel_config = deepcopy(config)
    channel_config["mode"] = "resource"
    return queue_subscription_resource_rule_transfer(items, trigger, config_override=channel_config)


def subscription_has_missing_episodes(item):
    if not isinstance(item, dict):
        return False
    media_type = discover_item_media_type(item) or str(item.get("media_type") or "").lower()
    if media_type != "tv":
        return False
    total = first_positive_int(
        item.get("episode_total"),
        item.get("total_episodes"),
        item.get("episodes_total"),
        item.get("episode_count"),
    )
    current = first_positive_int(item.get("library_episode_count"))
    return bool(total > 0 and current < total)


def filter_subscription_items_for_channel_modes(items, modes):
    rows = [item for item in (items or []) if isinstance(item, dict)]
    modes = {str(mode or "").strip().lower() for mode in (modes or [])}
    if "full" in modes:
        return rows
    selected = []
    seen = set()

    def add(item):
        key = get_subscription_item_key(item) or get_subscription_dedupe_key(item) or str(item.get("title") or item.get("name") or "")
        if key and key in seen:
            return
        if key:
            seen.add(key)
        selected.append(item)

    if "follow" in modes:
        for item in rows:
            if not is_subscription_completed(item):
                add(item)
    if "rewash" in modes:
        for item in rows:
            if item.get("in_library") or first_positive_int(item.get("library_episode_count")) > 0:
                add(item)
    if "complete" in modes:
        for item in rows:
            if subscription_has_missing_episodes(item):
                add(item)
    return selected


def run_due_subscription_search_poll():
    interval = _config_interval_minutes("ENV_SUBSCRIPTION_SEARCH_INTERVAL", 5)
    if not _poll_due("subscription_search_last_ts", interval):
        return {"ok": True, "ran": False, "reason": "not_due", "interval": interval}
    data = load_subscription_items(with_progress=True)
    items = [item for item in (data.get("items") or []) if isinstance(item, dict)] if isinstance(data, dict) else []
    if not items:
        return {"ok": True, "ran": False, "reason": "no_subscription_items", "interval": interval}
    result = queue_subscription_resource_rule_transfer(items, "subscription_search_poll")
    write_activity(
        "subscription",
        "subscription_search_poll",
        "start" if result.get("queued") else "skip",
        f"订阅刷新轮询：{result.get('task_label') or subscription_mode_task_label(result.get('mode'))} 排队 {result.get('queued') or 0} 条",
        interval=interval,
        mode=result.get("label"),
        task=result.get("task_label"),
        queued=result.get("queued"),
        skipped=result.get("skipped"),
        rule=result.get("rule"),
    )
    return {"ok": True, "ran": True, "interval": interval, "result": result}


def _configured_channel_modes():
    try:
        from app.config import read_config
        cfg = read_config()
        data = json.loads(str(cfg.get("ENV_TG_CHANNELS") or "[]"))
    except Exception:
        data = []
    modes = set()
    rows = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("enabled") is False or str(item.get("enabled") or "").strip().lower() in {"0", "false", "no", "off", "disabled"}:
                continue
            mode = str(item.get("mode") or "incoming").strip().lower() or "incoming"
            modes.add(mode)
            rows.append(item)
    return modes, rows


def run_due_channel_mode_poll():
    interval = _config_interval_minutes("ENV_CHECK_INTERVAL", 5)
    if not _poll_due("channel_mode_last_ts", interval):
        return {"ok": True, "ran": False, "reason": "not_due", "interval": interval}
    modes, channels = _configured_channel_modes()
    if not channels:
        return {"ok": True, "ran": False, "reason": "no_channels", "interval": interval}
    result = {
        "ok": True,
        "ran": True,
        "interval": interval,
        "modes": sorted(modes),
        "channels": len(channels),
        "incoming": None,
        "subscription_search": None,
    }
    incoming_enabled = bool(modes & {"incoming", "full"})
    search_enabled = bool(modes & {"follow", "rewash", "complete", "full"})
    status = "skip"
    if incoming_enabled:
        try:
            from app.services import run_115_monitor_once
            result["incoming"] = run_115_monitor_once()
            status = "success"
        except Exception as exc:
            result["incoming"] = {"ok": False, "error": str(exc)}
            status = "error"
    if search_enabled:
        data = load_subscription_items(with_progress=True)
        items = [item for item in (data.get("items") or []) if isinstance(item, dict)] if isinstance(data, dict) else []
        selected_items = filter_subscription_items_for_channel_modes(items, modes)
        result["subscription_search"] = queue_subscription_channel_resource_search(selected_items, "channel_mode_poll") if selected_items else {"ok": True, "queued": 0, "reason": "no_matching_subscription_items"}
        if result["subscription_search"].get("queued"):
            status = "success" if status != "error" else status
    write_activity(
        "transfer",
        "channel_mode_poll",
        status,
        f"频道模式轮询：{len(channels)} 个频道，模式 {','.join(sorted(modes)) or '-'}",
        interval=interval,
        channels=len(channels),
        modes=",".join(sorted(modes)),
        incoming=bool(incoming_enabled),
        queued=(result.get("subscription_search") or {}).get("queued") if isinstance(result.get("subscription_search"), dict) else "",
    )
    return result


def tmdb_image(path, size="w342"):
    if not path:
        return ""
    cfg = load_tmdb_config()
    return f"{cfg['image_base_url']}/{size}{path}"


def enrich_tmdb_tv_progress_fields(item, title="", tmdb_id=""):
    if not isinstance(item, dict):
        return item
    media_type = str(item.get("media_type") or item.get("type") or "").lower()
    if media_type not in ("tv", "电视剧", "series") and "剧" not in media_type:
        return item
    tmdb_id = str(tmdb_id or item.get("tmdb_id") or item.get("id") or "").strip()
    if not tmdb_id.isdigit():
        return item
    existing_total = int(item.get("episode_total") or item.get("total_episodes") or item.get("episodes_total") or 0)
    if existing_total:
        item["episode_total"] = existing_total
        item["total_episodes"] = existing_total
        if item.get("current_season") in (None, "") and item.get("season_count"):
            item["current_season"] = item.get("season_count")
        item["tmdb_id"] = tmdb_id
        return item
    try:
        data = get_cached_tmdb_detail("tv", tmdb_id, fetch=False)
    except Exception:
        return item
    if not isinstance(data, dict):
        return item
    total = int(data.get("number_of_episodes") or 0)
    season_count = int(data.get("number_of_seasons") or 0)
    if total:
        item["episode_total"] = total
        item["total_episodes"] = total
    if season_count:
        item["season_count"] = season_count
    seasons = [season for season in (data.get("seasons") or []) if isinstance(season, dict)]
    target_season = subscription_target_season(item)
    if target_season is not None:
        for season in seasons:
            if tmdb_season_number(season) == target_season:
                total = int(season.get("episode_count") or 0)
                if total:
                    item["episode_total"] = total
                    item["total_episodes"] = total
                item["current_season"] = target_season
                item["latest_season"] = target_season
                item["season_number"] = target_season
                item["target_season"] = target_season
                item["season_name"] = season.get("name") or ("特别篇" if target_season == 0 else f"第 {target_season} 季")
                item["season_air_date"] = season.get("air_date") or item.get("season_air_date") or ""
                item["tmdb_id"] = tmdb_id
                return item
    regular_seasons = []
    for season in seasons:
        season_number = tmdb_season_number(season)
        if season_number <= 0:
            continue
        regular_seasons.append(season_number)
    latest = max(regular_seasons or ([season_count] if season_count else [0]))
    if latest:
        item["current_season"] = latest
    item["tmdb_id"] = tmdb_id
    return item


def normalize_tmdb_item(item, media_type):
    title = item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or ""
    date = item.get("release_date") or item.get("first_air_date") or ""
    year = date[:4] if date else ""
    rating = item.get("vote_average")
    return {
        "source": "TMDB",
        "title": title,
        "year": year,
        "type": "电视剧" if media_type == "tv" else "电影",
        "rating": f"{float(rating):.1f}" if isinstance(rating, (int, float)) else "",
        "overview": item.get("overview") or "",
        "poster_url": tmdb_image(item.get("poster_path"), "w342"),
        "backdrop_url": tmdb_image(item.get("backdrop_path"), "w780"),
        "id": item.get("id"),
        "url": f"https://www.themoviedb.org/{'tv' if media_type == 'tv' else 'movie'}/{item.get('id')}" if item.get("id") else "",
    }


def get_library_episode_count(title, tmdb_id):
    seasons = get_library_season_episodes(title, tmdb_id)
    return sum(len(episodes or []) for episodes in seasons.values())


def get_library_season_episodes(title, tmdb_id):
    emby = get_emby_season_episodes(title, tmdb_id)
    if emby:
        return emby
    organize = get_organize_episode_library_info(title, tmdb_id)
    seasons = {}
    for season_num, episodes in organize.items():
        for episode_num, info in (episodes or {}).items():
            if not (info or {}).get("paths"):
                continue
            try:
                season = str(int(season_num or 1))
                episode = int(episode_num or 0)
            except Exception:
                continue
            if episode <= 0:
                continue
            seasons.setdefault(season, set()).add(episode)
    return {season: sorted(values) for season, values in seasons.items()}


def emby_library_connection():
    try:
        from app.config import read_config
        cfg = read_config()
    except Exception:
        return "", ""
    base_url = str(cfg.get("ENV_EMBY_SERVER_URL") or "").strip().rstrip("/")
    api_key = str(cfg.get("ENV_EMBY_API_KEY") or "").strip()
    return base_url, api_key


def emby_api_json(base_url, api_key, path, params=None, timeout=20):
    params = dict(params or {})
    params["api_key"] = api_key
    url = base_url + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
    return http_json(url, timeout=timeout)


def get_emby_library_index(force=False):
    now = time.time()
    cached = EMBY_LIBRARY_INDEX_CACHE.get("data")
    if not force and cached is not None and now - float(EMBY_LIBRARY_INDEX_CACHE.get("time") or 0) < EMBY_LIBRARY_INDEX_TTL_SECONDS:
        return cached
    base_url, api_key = emby_library_connection()
    empty = {"available": False, "series_by_id": {}, "series_by_tmdb": {}, "series_by_title": {}, "movie_by_tmdb": {}, "movie_by_title": {}}
    if not base_url or not api_key:
        EMBY_LIBRARY_INDEX_CACHE.update({"time": now, "data": empty})
        return empty
    data = {"available": True, "series_by_id": {}, "series_by_tmdb": {}, "series_by_title": {}, "movie_by_tmdb": {}, "movie_by_title": {}}
    try:
        series_data = emby_api_json(base_url, api_key, "/Items", {
            "Recursive": "true",
            "IncludeItemTypes": "Series",
            "Fields": "ProviderIds,Path,SortName",
            "Limit": "50000",
        })
        for row in series_data.get("Items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip()
            series_id = str(row.get("Id") or "").strip()
            provider_ids = row.get("ProviderIds") if isinstance(row.get("ProviderIds"), dict) else {}
            tmdb = str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or "").strip()
            node = {
                "id": series_id,
                "name": name,
                "tmdb_id": tmdb,
                "paths": [str(row.get("Path") or "").strip()] if row.get("Path") else [],
                "seasons": {},
                "episode_info": {},
                "episodes_loaded": False,
            }
            if series_id:
                data["series_by_id"][series_id] = node
            if tmdb:
                data["series_by_tmdb"][tmdb] = node
            title_key = compact_match_text(name)
            if title_key:
                data["series_by_title"][title_key] = node

        movies_data = emby_api_json(base_url, api_key, "/Items", {
            "Recursive": "true",
            "IncludeItemTypes": "Movie",
            "Fields": "ProviderIds,Path,SortName",
            "Limit": "50000",
        })
        for row in movies_data.get("Items", []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip()
            provider_ids = row.get("ProviderIds") if isinstance(row.get("ProviderIds"), dict) else {}
            tmdb = str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or "").strip()
            node = {
                "id": str(row.get("Id") or "").strip(),
                "name": name,
                "tmdb_id": tmdb,
                "paths": [str(row.get("Path") or "").strip()] if row.get("Path") else [],
            }
            if tmdb:
                data["movie_by_tmdb"][tmdb] = node
            title_key = compact_match_text(name)
            if title_key:
                data["movie_by_title"][title_key] = node
    except Exception:
        data["available"] = False
    EMBY_LIBRARY_INDEX_CACHE.update({"time": now, "data": data})
    return data


def find_emby_series_index(title, tmdb_id):
    index = get_emby_library_index()
    if not isinstance(index, dict) or not index.get("available"):
        return None, False
    tmdb_key = str(tmdb_id or "").strip()
    if tmdb_key and tmdb_key in index.get("series_by_tmdb", {}):
        return index["series_by_tmdb"][tmdb_key], True
    title_key = compact_match_text(title)
    if title_key and title_key in index.get("series_by_title", {}):
        return index["series_by_title"][title_key], True
    return None, True


def find_emby_movie_index(title, tmdb_id):
    index = get_emby_library_index()
    if not isinstance(index, dict) or not index.get("available"):
        return None, False
    tmdb_key = str(tmdb_id or "").strip()
    if tmdb_key and tmdb_key in index.get("movie_by_tmdb", {}):
        return index["movie_by_tmdb"][tmdb_key], True
    title_key = compact_match_text(title)
    if title_key and title_key in index.get("movie_by_title", {}):
        return index["movie_by_title"][title_key], True
    return None, True


def load_emby_series_episodes_for_node(node):
    if not isinstance(node, dict) or not node.get("id"):
        return node
    if node.get("episodes_loaded"):
        return node
    base_url, api_key = emby_library_connection()
    if not base_url or not api_key:
        node["episodes_loaded"] = True
        return node
    try:
        episodes_data = emby_api_json(base_url, api_key, "/Items", {
            "ParentId": str(node.get("id") or ""),
            "Recursive": "true",
            "IncludeItemTypes": "Episode",
            "Fields": "ParentIndexNumber,IndexNumber,SeriesId,SeriesName,Path",
            "Limit": "10000",
        }, timeout=20)
    except Exception:
        node["episodes_loaded"] = True
        return node
    season_sets = {}
    episode_info = {}
    for row in episodes_data.get("Items", []) or []:
        if not isinstance(row, dict):
            continue
        try:
            season = str(int(row.get("ParentIndexNumber") or row.get("SeasonIndex") or 0))
            episode = int(row.get("IndexNumber") or 0)
        except Exception:
            continue
        path = str(row.get("Path") or "").strip()
        if season == "0" or episode <= 0:
            continue
        if not path:
            continue
        season_sets.setdefault(season, set()).add(episode)
        ep_node = episode_info.setdefault(season, {}).setdefault(str(episode), {"paths": []})
        if path and path not in ep_node["paths"]:
            ep_node["paths"].append(path)
    node["seasons"] = {season: sorted(values) for season, values in season_sets.items()}
    node["episode_info"] = episode_info
    node["episodes_loaded"] = True
    return node


def get_emby_season_episodes(title, tmdb_id):
    node, indexed = find_emby_series_index(title, tmdb_id)
    if indexed:
        node = load_emby_series_episodes_for_node(node)
        return deepcopy((node or {}).get("seasons") or {})
    try:
        from app.config import read_config
        cfg = read_config()
    except Exception:
        return {}
    base_url = str(cfg.get("ENV_EMBY_SERVER_URL") or "").strip().rstrip("/")
    api_key = str(cfg.get("ENV_EMBY_API_KEY") or "").strip()
    if not base_url or not api_key:
        return {}

    def emby_json(path, params=None):
        params = dict(params or {})
        params["api_key"] = api_key
        url = base_url + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
        return http_json(url, timeout=12)

    try:
        data = emby_json("/Items", {
            "Recursive": "true",
            "IncludeItemTypes": "Series",
            "Fields": "ProviderIds",
            "SearchTerm": title,
            "Limit": "30",
        })
        series = None
        for item in data.get("Items", []) or []:
            provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
            item_tmdb = str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or "").strip()
            name = str(item.get("Name") or "").strip()
            if tmdb_id and item_tmdb == str(tmdb_id):
                series = item
                break
            if not series and compact_match_text(name) == compact_match_text(title):
                series = item
        if not series:
            return {}
        series_id = str(series.get("Id") or "").strip()
        if not series_id:
            return {}
        episodes_data = emby_json("/Items", {
            "ParentId": series_id,
            "Recursive": "true",
            "IncludeItemTypes": "Episode",
            "Fields": "ParentIndexNumber,IndexNumber,Path",
            "Limit": "10000",
        })
        seasons = {}
        for item in episodes_data.get("Items", []) or []:
            try:
                season = int(item.get("ParentIndexNumber") or item.get("SeasonIndex") or 0)
                episode = int(item.get("IndexNumber") or 0)
            except Exception:
                continue
            path = str(item.get("Path") or "").strip()
            if season > 0 and episode > 0 and path:
                seasons.setdefault(str(season), set()).add(episode)
        return {season: sorted(values) for season, values in seasons.items()}
    except Exception:
        return {}


def get_emby_library_paths(title, tmdb_id, media_type):
    if media_type == "tv":
        node, indexed = find_emby_series_index(title, tmdb_id)
    else:
        node, indexed = find_emby_movie_index(title, tmdb_id)
    if indexed:
        return list(dict.fromkeys((node or {}).get("paths") or []))[:12]
    try:
        from app.config import read_config
        cfg = read_config()
    except Exception:
        return []
    base_url = str(cfg.get("ENV_EMBY_SERVER_URL") or "").strip().rstrip("/")
    api_key = str(cfg.get("ENV_EMBY_API_KEY") or "").strip()
    if not base_url or not api_key:
        return []

    def emby_json(path, params=None):
        params = dict(params or {})
        params["api_key"] = api_key
        url = base_url + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
        return http_json(url, timeout=12)

    include_types = "Series" if media_type == "tv" else "Movie"
    try:
        data = emby_json("/Items", {
            "Recursive": "true",
            "IncludeItemTypes": include_types,
            "Fields": "ProviderIds,Path",
            "SearchTerm": title,
            "Limit": "30",
        })
        paths = []
        for item in data.get("Items", []) or []:
            provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
            item_tmdb = str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or "").strip()
            name = str(item.get("Name") or "").strip()
            matched = bool(tmdb_id and item_tmdb == str(tmdb_id)) or compact_match_text(name) == compact_match_text(title)
            if matched and item.get("Path"):
                paths.append(str(item.get("Path")))
        return list(dict.fromkeys(paths))[:12]
    except Exception:
        return []


def get_emby_episode_library_info(title, tmdb_id):
    node, indexed = find_emby_series_index(title, tmdb_id)
    if indexed:
        node = load_emby_series_episodes_for_node(node)
        return deepcopy((node or {}).get("episode_info") or {})
    try:
        from app.config import read_config
        cfg = read_config()
    except Exception:
        return {}
    base_url = str(cfg.get("ENV_EMBY_SERVER_URL") or "").strip().rstrip("/")
    api_key = str(cfg.get("ENV_EMBY_API_KEY") or "").strip()
    if not base_url or not api_key:
        return {}

    def emby_json(path, params=None):
        params = dict(params or {})
        params["api_key"] = api_key
        url = base_url + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
        return http_json(url, timeout=12)

    try:
        data = emby_json("/Items", {
            "Recursive": "true",
            "IncludeItemTypes": "Series",
            "Fields": "ProviderIds,Path",
            "SearchTerm": title,
            "Limit": "30",
        })
        series = None
        for item in data.get("Items", []) or []:
            provider_ids = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
            item_tmdb = str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or "").strip()
            name = str(item.get("Name") or "").strip()
            if tmdb_id and item_tmdb == str(tmdb_id):
                series = item
                break
            if not series and compact_match_text(name) == compact_match_text(title):
                series = item
        if not series or not str(series.get("Id") or "").strip():
            return {}
        episodes_data = emby_json("/Items", {
            "ParentId": str(series.get("Id") or "").strip(),
            "Recursive": "true",
            "IncludeItemTypes": "Episode",
            "Fields": "ParentIndexNumber,IndexNumber,Path",
            "Limit": "10000",
        })
        result = {}
        for item in episodes_data.get("Items", []) or []:
            try:
                season = str(int(item.get("ParentIndexNumber") or item.get("SeasonIndex") or 0))
                episode = str(int(item.get("IndexNumber") or 0))
            except Exception:
                continue
            path = str(item.get("Path") or "").strip()
            if season == "0" or episode == "0":
                continue
            if not path:
                continue
            node = result.setdefault(season, {}).setdefault(episode, {"paths": []})
            if path not in node["paths"]:
                node["paths"].append(path)
        return result
    except Exception:
        return {}


def quote_sqlite_identifier(value):
    identifier = str(value or "")
    if not identifier or "\x00" in identifier:
        raise ValueError("invalid sqlite identifier")
    return '"' + identifier.replace('"', '""') + '"'


def get_organize_episode_library_info(title, tmdb_id):
    db_path = str(PROJECT_ROOT / "db" / "organize" / "organize_history.db")
    if not os.path.exists(db_path):
        return {}
    try:
        con = sqlite3.connect(db_path)
        columns = [row[1] for row in con.execute("pragma table_info(organize_history_records)").fetchall()]
        lower_columns = {column.lower(): column for column in columns}
        season_column = lower_columns.get("season_num") or lower_columns.get("season") or lower_columns.get("parent_index_number")
        episode_column = lower_columns.get("episode_num") or lower_columns.get("episode") or lower_columns.get("index_number")
        path_columns = [
            column for column in columns
            if any(token in column.lower() for token in ("path", "target", "dest", "dir", "file"))
        ]
        if not season_column or not episode_column or not path_columns:
            con.close()
            return {}
        filters = []
        params = []
        if "status" in lower_columns:
            filters.append(f"{quote_sqlite_identifier(lower_columns['status'])} = ?")
            params.append("success")
        if tmdb_id and "tmdb_id" in lower_columns:
            filters.append(f"{quote_sqlite_identifier(lower_columns['tmdb_id'])} = ?")
            params.append(str(tmdb_id))
        elif title and "title" in lower_columns:
            filters.append(f"{quote_sqlite_identifier(lower_columns['title'])} = ?")
            params.append(title)
        if "media_type" in lower_columns:
            filters.append(
                f"{quote_sqlite_identifier(lower_columns['media_type'])} "
                "in ('tv', '电视剧', 'episode')"
            )
        where = f" where {' and '.join(filters)}" if filters else ""
        select_columns = ", ".join(
            quote_sqlite_identifier(column)
            for column in [season_column, episode_column, *path_columns[:8]]
        )
        query = (
            f"select {select_columns} from organize_history_records{where} "
            "order by rowid desc limit 1000"
        )
        rows = con.execute(query, params).fetchall()
        con.close()
        result = {}
        for row in rows:
            try:
                season = str(int(row[0] or 0))
                episode = str(int(row[1] or 0))
            except Exception:
                continue
            if season == "0" or episode == "0":
                continue
            paths = []
            for value in row[2:]:
                text = str(value or "").strip()
                if text and text not in paths:
                    paths.append(text)
            if not paths:
                continue
            node = result.setdefault(season, {}).setdefault(episode, {"paths": []})
            for path in paths:
                if path not in node["paths"]:
                    node["paths"].append(path)
        return result
    except Exception:
        return {}


def get_library_episode_info(title, tmdb_id):
    result = get_emby_episode_library_info(title, tmdb_id)
    organize = get_organize_episode_library_info(title, tmdb_id)
    for season, episodes in organize.items():
        season_node = result.setdefault(str(season), {})
        for episode, info in episodes.items():
            node = season_node.setdefault(str(episode), {"paths": []})
            for path in info.get("paths") or []:
                if path not in node["paths"]:
                    node["paths"].append(path)
    return result


def get_organize_library_paths(title, tmdb_id, media_type):
    db_path = str(PROJECT_ROOT / "db" / "organize" / "organize_history.db")
    if not os.path.exists(db_path):
        return []
    try:
        con = sqlite3.connect(db_path)
        columns = [row[1] for row in con.execute("pragma table_info(organize_history_records)").fetchall()]
        lower_columns = {column.lower(): column for column in columns}
        path_columns = [
            column for column in columns
            if any(token in column.lower() for token in ("path", "target", "dest", "dir", "file"))
        ]
        if not path_columns:
            con.close()
            return []
        filters = []
        params = []
        if "status" in lower_columns:
            filters.append(f"{quote_sqlite_identifier(lower_columns['status'])} = ?")
            params.append("success")
        if tmdb_id and "tmdb_id" in lower_columns:
            filters.append(f"{quote_sqlite_identifier(lower_columns['tmdb_id'])} = ?")
            params.append(str(tmdb_id))
        elif title and "title" in lower_columns:
            filters.append(f"{quote_sqlite_identifier(lower_columns['title'])} = ?")
            params.append(title)
        if media_type and "media_type" in lower_columns:
            media_type_column = quote_sqlite_identifier(lower_columns["media_type"])
            if media_type == "tv":
                filters.append(f"{media_type_column} in ('tv', '电视剧', 'episode')")
            else:
                filters.append(f"{media_type_column} not in ('tv', '电视剧', 'episode')")
        where = f" where {' and '.join(filters)}" if filters else ""
        select_columns = ", ".join(
            quote_sqlite_identifier(column) for column in path_columns[:8]
        )
        query = (
            f"select {select_columns} from organize_history_records{where} "
            "order by rowid desc limit 40"
        )
        rows = con.execute(query, params).fetchall()
        con.close()
        paths = []
        for row in rows:
            for value in row:
                text = str(value or "").strip()
                if text and text not in paths:
                    paths.append(text)
        return paths[:12]
    except Exception:
        return []


def get_library_paths(title, tmdb_id, media_type):
    paths = []
    paths.extend(get_emby_library_paths(title, tmdb_id, media_type))
    paths.extend(get_organize_library_paths(title, tmdb_id, media_type))
    return list(dict.fromkeys(path for path in paths if path))[:16]


def get_library_item_status(title, tmdb_id, media_type):
    episode_count = get_library_episode_count(title, tmdb_id) if media_type == "tv" else 0
    if media_type == "tv":
        return {"in_library": episode_count > 0, "episode_count": episode_count}
    emby_movie_present = False
    movie_node, movie_indexed = find_emby_movie_index(title, tmdb_id)
    if movie_indexed:
        emby_movie_present = bool(movie_node)
    db_path = str(PROJECT_ROOT / "db" / "organize" / "organize_history.db")
    series_episode_count = get_library_episode_count(title, "")
    if not os.path.exists(db_path):
        return {"in_library": emby_movie_present or series_episode_count > 0, "episode_count": series_episode_count}
    try:
        con = sqlite3.connect(db_path)
        row = None
        if tmdb_id:
            row = con.execute(
                """
                select count(1)
                from organize_history_records
                where status = 'success'
                  and media_type not in ('tv', '电视剧', 'episode')
                  and tmdb_id = ?
                """,
                (str(tmdb_id),),
            ).fetchone()
        count = int(row[0] or 0) if row else 0
        if not count and title:
            row = con.execute(
                """
                select count(1)
                from organize_history_records
                where status = 'success'
                  and media_type not in ('tv', '电视剧', 'episode')
                  and title = ?
                """,
                (title,),
            ).fetchone()
            count = int(row[0] or 0) if row else 0
        con.close()
        return {"in_library": emby_movie_present or count > 0 or series_episode_count > 0, "episode_count": series_episode_count}
    except Exception:
        return {"in_library": emby_movie_present or series_episode_count > 0, "episode_count": series_episode_count}


def enrich_tmdb_tv_items(items):
    for item in items:
        tmdb_id = item.get("id")
        item["library_episode_count"] = get_library_episode_count(item.get("title") or "", tmdb_id)
        item["in_library"] = int(item.get("library_episode_count") or 0) > 0
        if not tmdb_id:
            continue
        try:
            detail = get_cached_tmdb_detail("tv", tmdb_id)
            item["episode_total"] = int(detail.get("number_of_episodes") or 0)
            item["total_episodes"] = item["episode_total"]
            item["season_count"] = int(detail.get("number_of_seasons") or 0)
            enrich_tmdb_tv_progress_fields(item, item.get("title") or "", tmdb_id)
        except Exception:
            item["episode_total"] = 0
    return items


def enrich_library_status_items(items):
    rows = items if isinstance(items, list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        media_type = item.get("media_type") or item.get("type") or ""
        if media_type in ("tv", "电视剧", "series"):
            media_type = "tv"
        elif media_type in ("movie", "电影", "film"):
            media_type = "movie"
        else:
            media_type = "movie"
        tmdb_id = item.get("tmdb_id") or item.get("id") or ""
        title = item.get("title") or item.get("name") or ""
        status = get_library_item_status(title, tmdb_id, media_type)
        item["in_library"] = bool(status.get("in_library"))
        item["library_episode_count"] = int(status.get("episode_count") or item.get("library_episode_count") or 0)
        enrich_tmdb_tv_progress_fields(item, title, tmdb_id)
    return rows


def parse_year_filter(value, media_type, params):
    if not value or value == "全部":
        return
    year_key = "first_air_date_year" if media_type == "tv" else "primary_release_year"
    gte_key = "first_air_date.gte" if media_type == "tv" else "primary_release_date.gte"
    lte_key = "first_air_date.lte" if media_type == "tv" else "primary_release_date.lte"
    if re.fullmatch(r"\d{4}", value):
        params[year_key] = value
        return
    decade_match = re.fullmatch(r"(\d{4})年代", value)
    if decade_match:
        start = int(decade_match.group(1))
        params[gte_key] = f"{start}-01-01"
        params[lte_key] = f"{start + 9}-12-31"
        return
    if value == "90年代":
        params[gte_key] = "1990-01-01"
        params[lte_key] = "1999-12-31"
    elif value == "80年代":
        params[gte_key] = "1980-01-01"
        params[lte_key] = "1989-12-31"


def parse_positive_int(value, default, min_value=1, max_value=500):
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min_value, min(max_value, parsed))


def fetch_tmdb(query):
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        raise RuntimeError("TMDB API Key 不可用")

    media_type = "tv" if query.get("type") == "tv" else "movie"
    trend = query.get("trend") or "全部"
    sort_label = query.get("sort") or "热度降序"
    page = parse_positive_int(query.get("page"), 1, 1, 500)
    limit = parse_positive_int(query.get("limit"), 24, 1, 24)
    watch_provider_ids = str(query.get("_watch_provider_ids") or "").strip()

    if trend == "周榜":
        endpoint = f"{cfg['api_base_url']}/trending/{media_type}/week"
        params = {"api_key": cfg["api_key"], "language": "zh-CN"}
    elif trend == "日榜":
        endpoint = f"{cfg['api_base_url']}/trending/{media_type}/day"
        params = {"api_key": cfg["api_key"], "language": "zh-CN"}
    else:
        endpoint = f"{cfg['api_base_url']}/discover/{media_type}"
        sort_by = SORTS.get(sort_label)
        if not sort_by and sort_label in ("上映时间降序", "上映时间升序"):
            sort_by = "first_air_date.desc" if media_type == "tv" else "primary_release_date.desc"
            if sort_label == "上映时间升序":
                sort_by = sort_by.replace(".desc", ".asc")
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "sort_by": sort_by or "popularity.desc",
            "include_adult": "false",
            "include_video": "false",
        }
        if sort_label in ("评分最高", "评分最低"):
            params["vote_count.gte"] = "20"
        lang = LANGUAGES.get(query.get("language") or "")
        if lang:
            params["with_original_language"] = lang
        genre = (TMDB_GENRES_TV if media_type == "tv" else TMDB_GENRES_MOVIE).get(query.get("genre") or "")
        if genre:
            params["with_genres"] = str(genre)
        parse_year_filter(query.get("year") or "", media_type, params)
        if watch_provider_ids:
            params["with_watch_providers"] = watch_provider_ids
            params["watch_region"] = "US"

    offset = (page - 1) * limit
    api_page_size = 20
    api_start_page = offset // api_page_size + 1
    api_end_page = (offset + limit - 1) // api_page_size + 1
    collected = []
    total_results = 0
    total_pages = 1
    for api_page in range(api_start_page, api_end_page + 1):
        page_params = dict(params)
        page_params["page"] = str(api_page)
        data = http_json(endpoint + "?" + urllib.parse.urlencode(page_params))
        if not total_results:
            total_results = int(data.get("total_results") or 0)
            total_pages = max(1, (total_results + limit - 1) // limit) if total_results else 1
        collected.extend(data.get("results", []) or [])

    local_start = offset % api_page_size
    raw_items = collected[local_start:local_start + limit]
    items = [normalize_tmdb_item(item, media_type) for item in raw_items]
    if media_type == "tv":
        items = enrich_tmdb_tv_items(items)
    else:
        items = enrich_library_status_items(items)
    return {
        "success": True,
        "source": "TMDB",
        "items": items,
        "page": page,
        "limit": limit,
        "total_results": total_results or len(items),
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "generated_at": int(time.time()),
    }


_fetch_tmdb_uncached = fetch_tmdb


def fetch_tmdb(query):
    query = dict(query or {})
    return cached_discover_call("tmdb", query, lambda: _fetch_tmdb_uncached(query), ttl=discover_cache_ttl_for_query(query))


def fetch_streaming(query):
    normalized_query = dict(query or {})
    provider_key = str(normalized_query.get("provider") or "netflix").strip().lower()
    if provider_key not in STREAMING_PROVIDERS:
        provider_key = "netflix"
    provider = STREAMING_PROVIDERS[provider_key]
    normalized_query["provider"] = provider_key
    normalized_query["trend"] = "全部"

    def loader():
        tmdb_query = dict(normalized_query)
        tmdb_query["_watch_provider_ids"] = provider["id"]
        payload = _fetch_tmdb_uncached(tmdb_query)
        source_label = f"JustWatch · {provider['label']}"
        payload["source"] = source_label
        payload["source_label"] = source_label
        payload["provider"] = provider_key
        payload["provider_label"] = provider["label"]
        payload["watch_region"] = "US"
        for item in payload.get("items") or []:
            if isinstance(item, dict):
                item["source"] = source_label
        return payload

    return cached_discover_call(
        "streaming",
        normalized_query,
        loader,
        ttl=discover_cache_ttl_for_query(normalized_query),
    )


DAILY_MAINLAND_CODES = {"CN"}
DAILY_HK_TW_CODES = {"HK", "TW"}
DAILY_JP_KR_CODES = {"JP", "KR"}
DAILY_WESTERN_CODES = {
    "US", "GB", "CA", "AU", "NZ", "IE",
    "FR", "DE", "ES", "IT", "NL", "BE", "SE", "NO", "DK", "FI", "PL", "PT",
}
DAILY_VARIETY_GENRES = {10764, 10767}
DAILY_ANIMATION_GENRES = {16}


def daily_origin_codes(item):
    raw = item.get("origin_country") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(value).upper() for value in raw if str(value).strip()}


def daily_genre_ids(item):
    return {int(value) for value in (item.get("genre_ids") or []) if str(value).isdigit()}


def has_chinese_title(item):
    title = str(item.get("title") or item.get("name") or "").strip()
    return bool(re.search(r"[\u4e00-\u9fff]", title))


def daily_airing_category(item):
    countries = daily_origin_codes(item)
    genres = daily_genre_ids(item)
    is_variety = bool(genres & DAILY_VARIETY_GENRES)
    is_animation = bool(genres & DAILY_ANIMATION_GENRES)

    if is_animation:
        language = str(item.get("original_language") or "").lower()
        if countries & (DAILY_MAINLAND_CODES | DAILY_HK_TW_CODES) or language.startswith("zh"):
            return "国漫"
        if "JP" in countries or language == "ja":
            return "日漫"
        if "KR" in countries or language == "ko":
            return "韩漫"
        if countries & DAILY_WESTERN_CODES or language in {"en", "fr", "de", "es", "it", "nl", "pt"}:
            return "欧美动画"
        return "动漫"

    if is_variety:
        if countries & DAILY_MAINLAND_CODES:
            return "内地综艺"
        if "HK" in countries:
            return "香港综艺"
        if "TW" in countries:
            return "台湾综艺"
        return ""
    if countries & DAILY_MAINLAND_CODES:
        return "国产剧"
    if countries & DAILY_HK_TW_CODES:
        return "港台剧"
    if countries & DAILY_JP_KR_CODES:
        return "日韩剧"
    if countries & DAILY_WESTERN_CODES:
        return "欧美剧"
    return ""


def daily_airing_dedupe_key(item):
    tmdb_id = item.get("id")
    if tmdb_id:
        return f"tmdb:{tmdb_id}"
    title = item.get("name") or item.get("title") or item.get("original_name") or ""
    year = str(item.get("first_air_date") or "")[:4]
    return f"title:{normalize_subscription_dedupe_title(title)}:{year}"


def _fetch_daily_airing_uncached(query):
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        raise RuntimeError("TMDB API Key 不可用")

    page = parse_positive_int((query or {}).get("page"), 1, 1, 500)
    limit = parse_positive_int((query or {}).get("limit"), 24, 1, 24)
    timezone = (query or {}).get("timezone") or "Asia/Shanghai"
    today = time.strftime("%Y-%m-%d", time.localtime())
    endpoint = f"{cfg['api_base_url']}/tv/airing_today"

    max_pages = parse_positive_int((query or {}).get("max_pages"), 8, 1, 20)
    category_order = {
        "日漫": 1,
        "国漫": 2,
        "韩漫": 3,
        "欧美动画": 4,
        "动漫": 5,
        "欧美剧": 6,
        "日韩剧": 7,
        "港台剧": 8,
        "国产剧": 9,
        "内地综艺": 10,
        "香港综艺": 11,
        "台湾综艺": 12,
    }
    filtered = []
    seen = set()
    total_results = 0
    api_total_pages = 1
    for api_page in range(1, max_pages + 1):
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "timezone": timezone,
            "page": str(api_page),
        }
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params))
        if not total_results:
            total_results = int(data.get("total_results") or 0)
            api_total_pages = int(data.get("total_pages") or 1)
        for raw in data.get("results", []) or []:
            if not isinstance(raw, dict):
                continue
            if not has_chinese_title(raw):
                continue
            category = daily_airing_category(raw)
            if not category:
                continue
            dedupe_key = daily_airing_dedupe_key(raw)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            row = dict(raw)
            row["_airing_category"] = category
            filtered.append(row)
        if api_page >= api_total_pages:
            break

    buckets = {name: [] for name in category_order}
    for row in filtered:
        buckets.setdefault(row.get("_airing_category") or "", []).append(row)
    for rows in buckets.values():
        rows.sort(key=lambda row: (
            -(float(row.get("popularity") or 0)),
            str(row.get("name") or row.get("title") or ""),
        ))
    mixed = []
    while any(buckets.values()):
        for category in category_order:
            if buckets.get(category):
                mixed.append(buckets[category].pop(0))
    filtered = mixed
    total_filtered = len(filtered)
    total_pages = max(1, (total_filtered + limit - 1) // limit) if total_filtered else 1
    local_start = (page - 1) * limit
    raw_items = filtered[local_start:local_start + limit]
    items = []
    for raw in raw_items:
        item = normalize_tmdb_item(raw, "tv")
        item["source"] = "全球日播"
        item["source_key"] = "daily_airing"
        item["source_label"] = "全球日播"
        item["media_type"] = "tv"
        item["airing_today"] = True
        item["airing_category"] = raw.get("_airing_category") or daily_airing_category(raw)
        item["air_date"] = today
        item["tmdb_id"] = item.get("id")
        items.append(item)

    return {
        "success": True,
        "source": "全球日播",
        "items": items,
        "page": page,
        "limit": limit,
        "total_results": total_filtered,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "generated_at": int(time.time()),
        "air_date": today,
        "timezone": timezone,
        "raw_total_results": total_results,
    }


def _fetch_daily_airing_all_uncached(query):
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        raise RuntimeError("TMDB API Key 不可用")

    timezone = (query or {}).get("timezone") or "Asia/Shanghai"
    max_pages = parse_positive_int((query or {}).get("max_pages"), 8, 1, 20)
    today = time.strftime("%Y-%m-%d", time.localtime())
    endpoint = f"{cfg['api_base_url']}/tv/airing_today"
    category_order = {
        "日漫": 1,
        "国漫": 2,
        "韩漫": 3,
        "欧美动画": 4,
        "动漫": 5,
        "欧美剧": 6,
        "日韩剧": 7,
        "港台剧": 8,
        "国产剧": 9,
        "内地综艺": 10,
        "香港综艺": 11,
        "台湾综艺": 12,
    }
    buckets = {name: [] for name in category_order}
    seen = set()
    raw_total_results = 0
    api_total_pages = 1
    for api_page in range(1, max_pages + 1):
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "timezone": timezone,
            "page": str(api_page),
        }
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params))
        if not raw_total_results:
            raw_total_results = int(data.get("total_results") or 0)
            api_total_pages = int(data.get("total_pages") or 1)
        for raw in data.get("results", []) or []:
            if not isinstance(raw, dict):
                continue
            if not has_chinese_title(raw):
                continue
            category = daily_airing_category(raw)
            if not category:
                continue
            dedupe_key = daily_airing_dedupe_key(raw)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            item = normalize_tmdb_item(raw, "tv")
            item["source"] = "全球日播"
            item["source_key"] = "daily_airing"
            item["source_label"] = "全球日播"
            item["media_type"] = "tv"
            item["airing_today"] = True
            item["airing_category"] = category
            item["air_date"] = today
            item["tmdb_id"] = item.get("id")
            item["dedupe_key"] = dedupe_key
            item["_popularity"] = float(raw.get("popularity") or 0)
            enrich_library_status_items([item])
            buckets.setdefault(category, []).append(item)
        if api_page >= api_total_pages:
            break

    for rows in buckets.values():
        rows.sort(key=lambda row: (-float(row.get("_popularity") or 0), str(row.get("title") or "")))
    items = []
    while any(buckets.values()):
        for category in category_order:
            if buckets.get(category):
                row = buckets[category].pop(0)
                row.pop("_popularity", None)
                items.append(row)

    return {
        "success": True,
        "source": "全球日播",
        "items": items,
        "total_results": len(items),
        "raw_total_results": raw_total_results,
        "generated_at": int(time.time()),
        "air_date": today,
        "timezone": timezone,
    }


def fetch_daily_airing_all(query=None):
    query = dict(query or {})
    cache_query = {
        "mode": "regions_anime_zh_title_v1",
        "timezone": query.get("timezone") or "Asia/Shanghai",
        "max_pages": str(parse_positive_int(query.get("max_pages"), 8, 1, 20)),
    }
    return cached_discover_call(
        "daily_airing_all",
        cache_query,
        lambda: _fetch_daily_airing_all_uncached(cache_query),
        ttl=60 * 60,
    )


def fetch_daily_airing(query=None):
    query = dict(query or {})
    page = parse_positive_int(query.get("page"), 1, 1, 500)
    limit = parse_positive_int(query.get("limit"), 24, 1, 24)
    data = fetch_daily_airing_all(query)
    all_items = data.get("items") or []
    total_results = len(all_items)
    total_pages = max(1, (total_results + limit - 1) // limit) if total_results else 1
    start = (page - 1) * limit
    return {
        "success": True,
        "source": "全球日播",
        "items": all_items[start:start + limit],
        "page": page,
        "limit": limit,
        "total_results": total_results,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "generated_at": data.get("generated_at") or int(time.time()),
        "air_date": data.get("air_date") or time.strftime("%Y-%m-%d", time.localtime()),
        "timezone": data.get("timezone") or query.get("timezone") or "Asia/Shanghai",
        "raw_total_results": data.get("raw_total_results") or total_results,
        "cache_hit": data.get("cache_hit"),
        "cached_at": data.get("cached_at"),
    }


def clean_html(value):
    value = re.sub(r"<[^>]+>", "", value or "")
    value = value.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#47;", "/")
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def normalize_platform_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


def is_supported_image_proxy_url(url):
    try:
        parsed = urllib.parse.urlparse(normalize_platform_url(url))
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return parsed.scheme in ("http", "https") and any(host == item or host.endswith("." + item) for item in IMAGE_PROXY_HOSTS)


def extract_year(*values):
    for value in values:
        match = re.search(r"(?:19|20)\d{2}", clean_html(str(value or "")))
        if match:
            return match.group(0)
    return ""


def current_year():
    return str(time.localtime().tm_year)


def tmdb_year_for_candidate(item_type, tmdb_id):
    cfg = load_tmdb_config()
    if not cfg["api_key"] or item_type not in ("movie", "tv") or not tmdb_id:
        return ""
    endpoint = f"{cfg['api_base_url']}/{item_type}/{int(tmdb_id)}"
    params = urllib.parse.urlencode({"api_key": cfg["api_key"], "language": "zh-CN"})
    data = http_json(endpoint + "?" + params, timeout=12)
    date = data.get("first_air_date") if item_type == "tv" else data.get("release_date")
    return extract_year(date)


def year_from_discover_cache(title):
    target = compact_match_text(title)
    if not target:
        return ""
    try:
        con = discover_cache_db()
        rows = con.execute(
            "select payload from discover_cache where category in ('discover_item', 'tmdb', 'daily_airing', 'daily_airing_all', 'douban') order by updated_at desc limit 160"
        ).fetchall()
        con.close()
    except Exception:
        return ""
    for (payload,) in rows:
        try:
            data = json.loads(payload or "{}")
        except Exception:
            continue
        items = data.get("items") or []
        if isinstance(data.get("item"), dict):
            items = [data.get("item")] + list(items)
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or ""
            if compact_match_text(name) != target:
                continue
            year = extract_year(item.get("year"), item.get("first_air_date"), item.get("release_date"))
            if year:
                return year
    return ""


def resolve_platform_year(title, explicit_year="", update_text="", desc=""):
    explicit = extract_year(explicit_year, update_text, desc)
    if explicit:
        return explicit
    override = PLATFORM_YEAR_OVERRIDES.get(str(title or "").strip())
    if override:
        return override
    cached_year = year_from_discover_cache(title)
    if cached_year:
        return cached_year
    cache = _read_tmdb_match_cache()
    cache_key = "platform_year_v2|" + compact_match_text(title)
    if cache_key in cache:
        return str(cache.get(cache_key) or "")
    try:
        cfg = load_tmdb_config()
        if not cfg["api_key"]:
            return ""
        endpoint = f"{cfg['api_base_url']}/search/tv"
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "include_adult": "false",
            "query": title,
            "page": "1",
        }
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params), timeout=6)
        rows = data.get("results") or []
        selected = None
        compact_title = compact_match_text(title)
        for row in rows:
            name = row.get("name") or row.get("original_name") or ""
            if compact_match_text(name) == compact_title:
                selected = row
                break
        year = extract_year((selected or {}).get("first_air_date") or "")
        cache[cache_key] = year
        _write_tmdb_match_cache(cache)
        return year
    except Exception:
        cache[cache_key] = ""
        _write_tmdb_match_cache(cache)
        return ""


def normalize_platform_rating(value):
    text = clean_html(value)
    if not text:
        return ""
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return text
    try:
        number = float(match.group(0))
    except Exception:
        return text
    if number <= 0:
        return ""
    return f"{number:.1f}"


def resolve_platform_tmdb_meta(title, year=""):
    clean_title = str(title or "").strip()
    if not clean_title:
        return {}
    cache = _read_tmdb_match_cache()
    cache_key = "platform_meta_v1|" + compact_match_text(clean_title) + "|" + str(extract_year(year) or "")
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached
    try:
        cfg = load_tmdb_config()
        if not cfg["api_key"]:
            return {}
        endpoint = f"{cfg['api_base_url']}/search/tv"
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "include_adult": "false",
            "query": clean_title,
            "page": "1",
        }
        resolved_year = extract_year(year)
        if resolved_year:
            params["first_air_date_year"] = resolved_year
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params), timeout=6)
        selected = pick_tmdb_search_result(data.get("results") or [], clean_title)
        rating = ""
        if selected:
            try:
                number = float(selected.get("vote_average") or 0)
            except Exception:
                number = 0
            if number > 0:
                rating = f"{number:.1f}"
        meta = {
            "year": extract_year((selected or {}).get("first_air_date") or "") if selected else "",
            "rating": rating,
        }
        cache[cache_key] = meta
        _write_tmdb_match_cache(cache)
        return meta
    except Exception:
        cache[cache_key] = {}
        _write_tmdb_match_cache(cache)
        return {}


def discover_cache_db():
    path = Path(DISCOVER_CACHE_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        """
        create table if not exists discover_cache (
            cache_key text primary key,
            category text not null,
            payload text not null,
            created_at integer not null,
            updated_at integer not null,
            expires_at integer not null
        )
        """
    )
    con.execute("create index if not exists idx_discover_cache_expires on discover_cache(expires_at)")
    return con


def discover_cache_key(category, query):
    clean = {}
    for key, value in sorted((query or {}).items()):
        if value is None:
            continue
        clean[str(key)] = str(value)
    return category + ":" + json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def discover_cache_ttl_for_query(query):
    try:
        page = int(str((query or {}).get("page") or "1"))
    except Exception:
        page = 1
    if 1 <= page <= DISCOVER_PRELOAD_PAGES:
        return DISCOVER_PERMANENT_CACHE_SECONDS
    return DISCOVER_CACHE_TTL_SECONDS


def discover_item_media_type(item):
    if not isinstance(item, dict):
        return ""
    raw = str(item.get("media_type") or item.get("type") or item.get("mediaType") or "").strip().lower()
    if raw in {"tv", "series", "episode"} or "series" in raw or "\u5267" in raw:
        return "tv"
    if raw in {"movie", "film"} or "movie" in raw or "film" in raw or "\u7535\u5f71" in raw:
        return "movie"
    if item.get("first_air_date") or item.get("episode_total") or item.get("total_episodes") or item.get("season_count"):
        return "tv"
    if item.get("release_date"):
        return "movie"
    return ""


def discover_item_tmdb_id(item, media_type=""):
    if not isinstance(item, dict):
        return ""
    direct = str(item.get("tmdb_id") or item.get("tmdbId") or "").strip()
    raw_id = str(item.get("id") or "").strip()
    source_text = " ".join(str(item.get(key) or "") for key in ("source", "source_key", "url")).lower()
    if direct.isdigit():
        if direct == raw_id and ("douban" in source_text or "movie.douban.com" in source_text):
            return ""
        return direct
    if not raw_id.isdigit():
        return ""
    if "tmdb" in source_text or "themoviedb.org" in source_text:
        return raw_id
    return ""


def discover_item_cache_keys(item):
    if not isinstance(item, dict):
        return []
    media_type = discover_item_media_type(item) or "unknown"
    keys = []
    tmdb_id = discover_item_tmdb_id(item, media_type)
    if tmdb_id and media_type != "unknown":
        keys.append(f"tmdb:{media_type}:{tmdb_id}")
    source_key = str(item.get("source_key") or item.get("source") or "").strip().lower()
    raw_id = str(item.get("id") or "").strip()
    if source_key and raw_id:
        keys.append(f"id:{source_key}:{raw_id}")
    title = str(item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or "").strip()
    title_key = compact_match_text(title) if title else ""
    year = extract_year(item.get("year"), item.get("release_date"), item.get("first_air_date"), item.get("date"))
    if title_key:
        if year:
            keys.append(f"title:{media_type}:{title_key}:{year}")
        keys.append(f"title:{media_type}:{title_key}")
        if media_type != "unknown":
            keys.append(f"title:any:{title_key}")
    deduped = []
    for key in keys:
        if key and key not in deduped:
            deduped.append(key)
    return deduped


def set_discover_item_cache(item, source_category=""):
    if not isinstance(item, dict):
        return item
    cache_item = deepcopy(item)
    media_type = discover_item_media_type(cache_item)
    if media_type:
        cache_item["media_type"] = media_type
    tmdb_id = discover_item_tmdb_id(cache_item, media_type)
    if tmdb_id:
        cache_item["tmdb_id"] = tmdb_id
    keys = discover_item_cache_keys(cache_item)
    if not keys:
        return item
    now = int(time.time())
    payload = {
        "success": True,
        "item": cache_item,
        "source_category": source_category or "",
        "cached_at": now,
        "permanent": True,
    }
    con = None
    try:
        con = discover_cache_db()
        for item_key in keys:
            cache_key = discover_cache_key("discover_item", {"key": item_key})
            row_payload = dict(payload)
            row_payload["item_key"] = item_key
            con.execute(
                """
                insert into discover_cache(cache_key, category, payload, created_at, updated_at, expires_at)
                values(?, ?, ?, ?, ?, ?)
                on conflict(cache_key) do update set
                  payload = excluded.payload,
                  updated_at = excluded.updated_at,
                  expires_at = excluded.expires_at
                """,
                (
                    cache_key,
                    "discover_item",
                    json.dumps(row_payload, ensure_ascii=False),
                    now,
                    now,
                    now + DISCOVER_PERMANENT_CACHE_SECONDS,
                ),
            )
        con.commit()
    except Exception:
        pass
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass
    return item


def get_cached_discover_item(item):
    for item_key in discover_item_cache_keys(item):
        cached = get_discover_cache("discover_item", {"key": item_key})
        if isinstance(cached, dict) and isinstance(cached.get("item"), dict):
            return deepcopy(cached["item"])
    return None


def merge_cached_discover_item(item):
    if not isinstance(item, dict):
        return item
    cached = get_cached_discover_item(item)
    if not isinstance(cached, dict):
        return item
    merged = deepcopy(cached)
    for key, value in item.items():
        if value not in (None, "", [], {}):
            merged[key] = value
        elif key not in merged:
            merged[key] = value
    media_type = discover_item_media_type(merged)
    if media_type:
        merged["media_type"] = media_type
    tmdb_id = discover_item_tmdb_id(merged, media_type)
    if tmdb_id:
        merged["tmdb_id"] = tmdb_id
    return merged


def cache_discover_items_from_payload(category, payload):
    if category == "discover_item" or not isinstance(payload, dict):
        return
    rows = []
    if isinstance(payload.get("items"), list):
        rows.extend(payload.get("items") or [])
    if isinstance(payload.get("item"), dict):
        rows.append(payload.get("item"))
    for row in rows:
        if isinstance(row, dict):
            set_discover_item_cache(row, category)


def get_cached_tmdb_detail(media_type, tmdb_id, append_to_response="", fetch=True):
    media_type = "tv" if str(media_type or "").lower() == "tv" else "movie"
    tmdb_id = str(tmdb_id or "").strip()
    if not tmdb_id.isdigit():
        return {}
    query = {"media_type": media_type, "tmdb_id": tmdb_id, "append": append_to_response or ""}
    cached = get_discover_cache("tmdb_detail", query)
    if isinstance(cached, dict) and isinstance(cached.get("detail"), dict):
        return deepcopy(cached["detail"])
    if not fetch:
        return {}
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        return {}
    params = {"api_key": cfg["api_key"], "language": "zh-CN"}
    if append_to_response:
        params["append_to_response"] = append_to_response
    raw = http_json(f"{cfg['api_base_url']}/{media_type}/{int(tmdb_id)}?" + urllib.parse.urlencode(params), timeout=12)
    set_discover_cache(
        "tmdb_detail",
        query,
        {"success": True, "detail": raw},
        ttl=DISCOVER_PERMANENT_CACHE_SECONDS,
    )
    try:
        item = normalize_tmdb_item(raw, media_type)
        item["tmdb_id"] = tmdb_id
        if media_type == "tv":
            total = int(raw.get("number_of_episodes") or 0)
            season_count = int(raw.get("number_of_seasons") or 0)
            if total:
                item["episode_total"] = total
                item["total_episodes"] = total
            if season_count:
                item["season_count"] = season_count
            regular = [
                tmdb_season_number(season)
                for season in (raw.get("seasons") or [])
                if isinstance(season, dict) and tmdb_season_number(season) > 0
            ]
            if regular:
                item["current_season"] = max(regular)
        set_discover_item_cache(item, "tmdb_detail")
    except Exception:
        pass
    return raw


def get_cached_tmdb_season_detail(tmdb_id, season_number):
    tmdb_id = str(tmdb_id or "").strip()
    season_number = str(season_number or "").strip()
    if not tmdb_id.isdigit() or not season_number.isdigit():
        return {}
    query = {"tmdb_id": tmdb_id, "season": season_number}
    cached = get_discover_cache("tmdb_season", query)
    if isinstance(cached, dict) and isinstance(cached.get("detail"), dict):
        return deepcopy(cached["detail"])
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        return {}
    params = urllib.parse.urlencode({"api_key": cfg["api_key"], "language": "zh-CN"})
    raw = http_json(f"{cfg['api_base_url']}/tv/{int(tmdb_id)}/season/{int(season_number)}?{params}", timeout=12)
    set_discover_cache(
        "tmdb_season",
        query,
        {"success": True, "detail": raw},
        ttl=DISCOVER_PERMANENT_CACHE_SECONDS,
    )
    return raw


def get_discover_cache(category, query):
    key = discover_cache_key(category, query)
    now = int(time.time())
    try:
        con = discover_cache_db()
        row = con.execute(
            "select payload, expires_at from discover_cache where cache_key = ?",
            (key,),
        ).fetchone()
        con.close()
        if not row or int(row[1] or 0) < now:
            return None
        data = json.loads(row[0])
        if isinstance(data, dict):
            data = deepcopy(data)
            data["cache_hit"] = True
            return data
    except Exception:
        return None
    return None


def set_discover_cache(category, query, payload, ttl=DISCOVER_CACHE_TTL_SECONDS):
    if not isinstance(payload, dict) or payload.get("success") is False:
        return payload
    key = discover_cache_key(category, query)
    now = int(time.time())
    cache_payload = deepcopy(payload)
    cache_payload["cache_hit"] = False
    cache_payload["cached_at"] = now
    try:
        con = discover_cache_db()
        con.execute(
            """
            insert into discover_cache(cache_key, category, payload, created_at, updated_at, expires_at)
            values(?, ?, ?, ?, ?, ?)
            on conflict(cache_key) do update set
              payload = excluded.payload,
              updated_at = excluded.updated_at,
              expires_at = excluded.expires_at
            """,
            (key, category, json.dumps(cache_payload, ensure_ascii=False), now, now, now + int(ttl)),
        )
        con.commit()
        con.close()
    except Exception:
        pass
    try:
        cache_discover_items_from_payload(category, cache_payload)
    except Exception:
        pass
    return payload


def cached_discover_call(category, query, loader, ttl=DISCOVER_CACHE_TTL_SECONDS):
    cached = get_discover_cache(category, query)
    if cached is not None:
        if int(ttl or 0) >= DISCOVER_PERMANENT_CACHE_SECONDS:
            try:
                set_discover_cache(category, query, cached, ttl)
            except Exception:
                pass
        else:
            try:
                cache_discover_items_from_payload(category, cached)
            except Exception:
                pass
        return cached
    data = loader()
    if isinstance(data, dict):
        data["cache_hit"] = False
    return set_discover_cache(category, query, data, ttl)


def discover_cache_stats():
    now = int(time.time())
    try:
        con = discover_cache_db()
        total = int((con.execute("select count(1) from discover_cache").fetchone() or [0])[0] or 0)
        valid = int((con.execute("select count(1) from discover_cache where expires_at >= ?", (now,)).fetchone() or [0])[0] or 0)
        by_category = [
            {"category": row[0], "count": int(row[1] or 0)}
            for row in con.execute("select category, count(1) from discover_cache group by category order by category").fetchall()
        ]
        con.close()
        return {"success": True, "total": total, "valid": valid, "categories": by_category}
    except Exception as exc:
        return {"success": False, "error": str(exc), "total": 0, "valid": 0, "categories": []}


def preload_discover_cache(pages=DISCOVER_PRELOAD_PAGES):
    pages = parse_positive_int(pages, DISCOVER_PRELOAD_PAGES, 1, 10)
    jobs = []
    for page in range(1, pages + 1):
        for media_type in ("movie", "tv"):
            jobs.append(("tmdb", {"page": str(page), "limit": "16", "type": media_type}))
        jobs.append(("daily_airing", {"page": str(page), "limit": "16", "timezone": "Asia/Shanghai"}))
        jobs.append(("douban", {"page": str(page), "limit": "16"}))
        for platform in ("全部", "腾讯视频", "优酷", "爱奇艺", "芒果"):
            jobs.append(("platform_hot", {"page": str(page), "limit": "16", "platform": platform}))

    result = {"success": True, "total": len(jobs), "ok": 0, "errors": []}
    for category, query in jobs:
        try:
            if category == "tmdb":
                fetch_tmdb(query)
            elif category == "daily_airing":
                fetch_daily_airing(query)
            elif category == "douban":
                fetch_douban(query)
            elif category == "platform_hot":
                fetch_platform_hot(query)
            result["ok"] += 1
        except Exception as exc:
            result["errors"].append(f"{category}:{query}: {exc}")
    return result


def extract_episode_total(*values):
    text = " ".join(clean_html(str(value or "")) for value in values if value)
    patterns = [
        r"(?:更新至|更至|更新|已更新|全)\s*(\d{1,4})\s*(?:集|期|话)",
        r"(\d{1,4})\s*(?:集|期|话)\s*(?:全|完结)",
        r"共\s*(\d{1,4})\s*(?:集|期|话)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 0


def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def extract_balanced_json(text, marker):
    marker_index = text.find(marker)
    if marker_index < 0:
        raise RuntimeError("未找到页面数据")
    start = text.find("{", marker_index)
    if start < 0:
        raise RuntimeError("未找到 JSON 数据")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise RuntimeError("JSON 数据不完整")


def normalize_platform_item(platform_key, title, poster_url="", url="", item_id="", update_text="", desc="", rating="", year=""):
    title = clean_html(title)
    if not title:
        return None
    update_text = clean_html(update_text)
    desc = clean_html(desc)
    episode_total = 0
    match = re.search(r"(?:更新至|全|共)\s*(\d+)\s*集", update_text)
    if match:
        episode_total = int(match.group(1))
    platform_names = {
        "iqiyi": "爱奇艺",
        "youku": "优酷",
        "tencent": "腾讯视频",
        "mango": "芒果",
    }
    source_name = platform_names.get(platform_key, platform_key)
    resolved_year = extract_year(year, update_text, desc)
    return {
        "source": source_name,
        "source_key": platform_key,
        "source_label": f"{source_name}热更",
        "id": f"{platform_key}:{item_id or title}",
        "title": title,
        "year": resolved_year,
        "type": "电视剧",
        "media_type": "tv",
        "rating": normalize_platform_rating(rating),
        "poster_url": normalize_platform_url(poster_url),
        "backdrop_url": "",
        "url": normalize_platform_url(url),
        "episodes_info": update_text,
        "episode_total": episode_total or extract_episode_total(update_text, desc, title),
        "overview": desc,
    }


def is_platform_tv_hot_item(item):
    if not item:
        return False
    title = item.get("title") or ""
    update_text = item.get("episodes_info") or ""
    desc = item.get("overview") or ""
    text = f"{title} {update_text} {desc}"
    if any(word in text for word in ("预告", "花絮", "片花", "幕后", "资讯", "速看", "解说")):
        return False
    if any(word in text for word in ("更新", "集", "季", "剧", "热度榜", "新上线", "全")):
        return True
    return False


def dedupe_platform_items(items, limit=120):
    rows = []
    seen = set()
    for item in items:
        if not item:
            continue
        if item.get("media_type") == "tv" and not is_platform_tv_hot_item(item):
            continue
        key = item.get("id") or f"{item.get('source')}:{item.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def enrich_platform_item_years(items):
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        rating = normalize_platform_rating(row.get("rating") or "")
        needs_year = not extract_year(row.get("year") or "")
        needs_rating = not rating
        tmdb_meta = resolve_platform_tmdb_meta(row.get("title") or "", row.get("year") or "") if needs_year or needs_rating else {}
        if needs_year:
            row["year"] = resolve_platform_year(
                row.get("title") or "",
                tmdb_meta.get("year") or "",
                row.get("episodes_info") or "",
                row.get("overview") or "",
            )
        if needs_rating:
            row["rating"] = normalize_platform_rating(tmdb_meta.get("rating") or "")
        else:
            row["rating"] = rating
        rows.append(row)
    return rows


def fetch_platform_iqiyi():
    url = "https://www.iqiyi.com/prelw/portal/lw/v7/channel/tv?" + urllib.parse.urlencode({
        "lwaFastKey": "Page_tv_1",
        "v": "17.063.25600",
        "adExt": json.dumps({"r": "2.18.0-ares6-pure"}, ensure_ascii=False),
    })
    raw = http_text(url)
    data = json.loads(extract_balanced_json(raw, "response:"))
    items = []
    for row in walk_dicts(data):
        if row.get("isAd") is True:
            continue
        title = row.get("display_name") or row.get("short_display_name") or row.get("title")
        album_id = row.get("album_id") or row.get("entity_id") or row.get("tv_id")
        poster = row.get("image_cover") or row.get("image_url") or row.get("thumbnail_url") or row.get("banner_image_url")
        if not title or not album_id or not poster:
            continue
        channel_id = str(row.get("channel_id") or "")
        if channel_id and channel_id != "2":
            continue
        date_info = row.get("date") if isinstance(row.get("date"), dict) else {}
        items.append(normalize_platform_item(
            "iqiyi",
            title,
            poster_url=poster,
            url=row.get("page_url") or "",
            item_id=album_id,
            update_text=row.get("dq_updatestatus") or row.get("sub_title") or row.get("desc") or "",
            desc=row.get("description") or row.get("desc") or "",
            rating=row.get("score") or row.get("sns_score") or "",
            year=date_info.get("year") or "",
        ))
    return dedupe_platform_items(items)


def fetch_platform_youku():
    raw = http_text("https://tv.youku.com/")
    data_text = extract_balanced_json(raw, "window.__INITIAL_DATA__")
    data_text = re.sub(r":\s*undefined\b", ": null", data_text)
    data = json.loads(data_text)
    items = []
    for row in walk_dicts(data):
        title = row.get("title")
        poster = row.get("img") or row.get("hImg")
        item_id = row.get("action_value") or row.get("id") or row.get("action_url")
        if not title or not poster or not item_id:
            continue
        mark = row.get("mark") if isinstance(row.get("mark"), dict) else {}
        if clean_html(mark.get("text") or "") == "广告":
            continue
        reason = row.get("reason") if isinstance(row.get("reason"), dict) else {}
        reason_text = reason.get("text") if isinstance(reason.get("text"), dict) else {}
        update_text = row.get("updateTips") or row.get("lbTexts") or mark.get("text") or reason_text.get("title") or ""
        action_value = str(row.get("action_value") or "")
        item_url = row.get("action_url") or ""
        if not item_url and action_value:
            item_url = f"https://v.youku.com/video?s={urllib.parse.quote(action_value)}"
        items.append(normalize_platform_item(
            "youku",
            title,
            poster_url=poster,
            url=item_url,
            item_id=item_id,
            update_text=update_text,
            desc=row.get("desc") or "",
            rating=row.get("score") or row.get("rating") or "",
        ))
    return dedupe_platform_items(items)


def fetch_platform_tencent():
    url = "https://pbaccess.video.qq.com/trpc.vector_layout.page_view.PageService/getPage?video_appid=3000010&vversion_platform=2"
    payload = {
        "page_params": {
            "page_type": "channel",
            "page_id": "100113",
            "scene": "channel",
            "new_mark_label_enabled": "1",
        },
        "page_bypass_params": {
            "params": {
                "platform_id": "2",
                "caller_id": "3000010",
                "data_mode": "default",
                "user_mode": "default",
                "page_type": "channel",
                "page_id": "100113",
                "scene": "channel",
                "new_mark_label_enabled": "1",
            },
            "scene": "channel",
            "app_version": "",
            "abtest_bypass_id": "",
        },
        "page_context": None,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Content-Type": "application/json",
            "Origin": "https://v.qq.com",
            "Referer": "https://v.qq.com/channel/tv",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=18) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    items = []
    for row in walk_dicts(data):
        params = row.get("params") if isinstance(row.get("params"), dict) else row
        title = params.get("title")
        cid = params.get("cid") or params.get("cover_id") or params.get("item_id")
        poster = params.get("image_url_vertical") or params.get("pic_276x386") or params.get("image_url")
        if not title or not cid or not poster:
            continue
        if params.get("ad_title") or params.get("ad_desc"):
            continue
        update_text = params.get("episode_updated") or params.get("sub_title") or params.get("second_title") or params.get("material_video_subtitle") or ""
        items.append(normalize_platform_item(
            "tencent",
            title,
            poster_url=poster,
            url=f"https://v.qq.com/x/cover/{urllib.parse.quote(str(cid))}.html",
            item_id=cid,
            update_text=update_text,
            desc=params.get("summary") or params.get("description") or params.get("second_title") or "",
            rating=params.get("score") or params.get("rating") or "",
        ))
    return dedupe_platform_items(items)


def fetch_platform_mango():
    url = "https://pianku.api.mgtv.com/rider/list/pcweb/v3?" + urllib.parse.urlencode({
        "allowedRC": "1",
        "platform": "pcweb",
        "channelId": "2",
        "pn": "1",
        "pc": "72",
    })
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.mgtv.com/tv/",
    })
    with urllib.request.urlopen(req, timeout=18) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    hit_docs = (((data or {}).get("data") or {}).get("hitDocs") or [])
    items = []
    for row in hit_docs:
        if not isinstance(row, dict):
            continue
        clip_id = row.get("clipId") or row.get("clip_id") or row.get("id")
        title = row.get("title") or row.get("name")
        poster = row.get("img") or row.get("imgV") or row.get("imgUrlH")
        if not title or not clip_id or not poster:
            continue
        item_url = row.get("url") or row.get("playUrl") or f"https://www.mgtv.com/h/{clip_id}.html"
        genres = row.get("kind") if isinstance(row.get("kind"), list) else []
        desc = row.get("story") or row.get("desc") or " ".join(str(g) for g in genres if g)
        items.append(normalize_platform_item(
            "mango",
            title,
            poster_url=poster,
            url=item_url,
            item_id=clip_id,
            update_text=row.get("updateInfo") or row.get("updateDesc") or "",
            desc=desc,
            rating=row.get("score") or "",
            year=row.get("year") or "",
        ))
    return dedupe_platform_items(items)


def fetch_platform_hot(query):
    source_label = query.get("platform") or "全部"
    source_key = PLATFORM_HOT_SOURCES.get(source_label, source_label)
    page = parse_positive_int(query.get("page"), 1, 1, 500)
    limit = parse_positive_int(query.get("limit"), 24, 1, 24)
    fetchers = [
        ("iqiyi", "爱奇艺", fetch_platform_iqiyi),
        ("youku", "优酷", fetch_platform_youku),
        ("tencent", "腾讯视频", fetch_platform_tencent),
        ("mango", "芒果", fetch_platform_mango),
    ]
    rows = []
    errors = []
    counts = {}
    for key, label, fetcher in fetchers:
        if source_key not in ("all", key):
            continue
        try:
            items = fetcher()
            counts[label] = len(items)
            rows.extend(items)
        except Exception as exc:
            counts[label] = 0
            errors.append(f"{label}: {exc}")
    rows = dedupe_platform_items(rows, 300)
    total_results = len(rows)
    total_pages = max(1, (total_results + limit - 1) // limit) if total_results else 1
    start = (page - 1) * limit
    page_items = enrich_library_status_items(enrich_platform_item_years(rows[start:start + limit]))
    return {
        "success": True,
        "source": source_label if source_key != "all" else "平台热更",
        "platform": source_label,
        "items": page_items,
        "counts": counts,
        "errors": errors,
        "page": page,
        "limit": limit,
        "total_results": total_results,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "generated_at": int(time.time()),
    }


_fetch_platform_hot_uncached = fetch_platform_hot


def fetch_platform_hot(query):
    query = dict(query or {})
    query.setdefault("year_mode", "tmdb_v5")
    query.setdefault("rating_mode", "tmdb_v1")
    return cached_discover_call("platform_hot", query, lambda: _fetch_platform_hot_uncached(query), ttl=discover_cache_ttl_for_query(query))


def parse_douban_chart(html):
    items = []
    blocks = re.findall(r'<tr class="item">([\s\S]*?)</tr>', html)
    for block in blocks:
        title_match = re.search(r'<a class="nbg"[^>]+title="([^"]+)"', block)
        img_match = re.search(r'<img src="([^"]+)"[^>]+alt="([^"]*)"', block)
        link_match = re.search(r'href="(https://movie\.douban\.com/subject/(\d+)/)"', block)
        rating_match = re.search(r'<span class="rating_nums">([^<]+)</span>', block)
        desc_match = re.search(r"<p>([\s\S]*?)</p>", block)
        title = clean_html(title_match.group(1) if title_match else (img_match.group(2) if img_match else ""))
        desc = clean_html(desc_match.group(1) if desc_match else "")
        year_match = re.search(r"(19|20)\d{2}", desc)
        if title:
            items.append({
                "source": "豆瓣",
                "title": title,
                "year": year_match.group(0) if year_match else "",
                "type": "电影",
                "rating": clean_html(rating_match.group(1) if rating_match else ""),
                "poster_url": img_match.group(1) if img_match else "",
                "backdrop_url": "",
                "id": link_match.group(2) if link_match else "",
                "url": link_match.group(1) if link_match else "",
            })

    if len(items) < 24:
        top_blocks = re.findall(r"<dl>[\s\S]*?</dl>", html)
        for block in top_blocks:
            if len(items) >= 24:
                break
            img_match = re.search(r'<img src="([^"]+)"', block)
            link_match = re.search(r'href="(https://movie\.douban\.com/subject/(\d+)/[^"]*)"', block)
            titles = re.findall(r"<a[^>]*>\s*([^<]+?)\s*</a>", block)
            title = clean_html(titles[-1] if titles else "")
            if title and not any(item["id"] == (link_match.group(2) if link_match else "") for item in items):
                items.append({
                    "source": "豆瓣",
                    "title": title,
                    "year": extract_year(block) or current_year(),
                    "type": "电影",
                    "rating": "",
                    "poster_url": img_match.group(1) if img_match else "",
                    "backdrop_url": "",
                    "id": link_match.group(2) if link_match else "",
                    "url": link_match.group(1) if link_match else "",
                })
    return items[:24]


def fetch_douban_subjects(page, limit):
    start = (page - 1) * limit
    params = {
        "type": "movie",
        "tag": "\u70ed\u95e8",
        "sort": "recommend",
        "page_limit": str(limit),
        "page_start": str(start),
    }
    url = "https://movie.douban.com/j/search_subjects?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://movie.douban.com/explore",
    })
    with urllib.request.urlopen(req, timeout=18) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    rows = []
    for subject in data.get("subjects", []) or []:
        if not isinstance(subject, dict):
            continue
        title = clean_html(subject.get("title") or "")
        if not title:
            continue
        rows.append({
            "source": "\u8c46\u74e3",
            "title": title,
            "year": extract_year(subject.get("year"), subject.get("card_subtitle"), subject.get("episodes_info"), subject.get("url")) or current_year(),
            "type": "\u7535\u5f71",
            "rating": clean_html(subject.get("rate") or ""),
            "poster_url": subject.get("cover") or "",
            "backdrop_url": "",
            "id": str(subject.get("id") or ""),
            "url": subject.get("url") or "",
        })
    return rows


def fetch_douban(query=None):
    query = query or {}
    page = parse_positive_int(query.get("page"), 1, 1, 500)
    limit = parse_positive_int(query.get("limit"), 24, 1, 24)
    now = time.time()
    try:
        page_items = enrich_library_status_items(fetch_douban_subjects(page, limit))
        total_pages = page + 1 if len(page_items) >= limit else page
        total_results = (page - 1) * limit + len(page_items)
        if len(page_items) >= limit:
            total_results += limit
        return {
            "success": True,
            "source": "\u8c46\u74e3",
            "items": page_items,
            "page": page,
            "limit": limit,
            "total_results": total_results,
            "total_pages": max(1, total_pages),
            "has_next": len(page_items) >= limit,
            "has_prev": page > 1,
            "generated_at": int(now),
        }
    except Exception:
        if DOUBAN_CACHE["items"] and now - DOUBAN_CACHE["time"] < 900:
            items = DOUBAN_CACHE["items"]
        else:
            html = http_text("https://movie.douban.com/chart")
            items = parse_douban_chart(html)
            DOUBAN_CACHE.update({"time": now, "items": items})
    total_results = len(items)
    total_pages = max(1, (total_results + limit - 1) // limit) if total_results else 1
    start = (page - 1) * limit
    page_items = enrich_library_status_items(items[start:start + limit])
    return {
        "success": True,
        "source": "豆瓣",
        "items": page_items,
        "page": page,
        "limit": limit,
        "total_results": total_results,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "generated_at": int(now),
    }


_fetch_douban_uncached = fetch_douban


def fetch_douban(query=None):
    query = dict(query or {})
    return cached_discover_call("douban", query, lambda: _fetch_douban_uncached(query), ttl=discover_cache_ttl_for_query(query))


def infer_season_episode(text):
    text = text or ""
    season = ""
    episodes = set()
    m = re.search(r"S(\d{1,2})", text, re.I)
    if m:
        season = str(int(m.group(1)))
    for ep in re.findall(r"E(\d{1,3})", text, re.I):
        episodes.add(int(ep))
    for start, end in re.findall(r"E(\d{1,3})\s*[-~至]\s*E?(\d{1,3})", text, re.I):
        a, b = int(start), int(end)
        if a <= b <= 80:
            episodes.update(range(a, b + 1))
    return season, sorted(episodes)


def infer_season_episode(text):
    text = text or ""
    season = ""
    episodes = set()
    m = re.search(r"S(\d{1,2})", text, re.I)
    if m:
        season = str(int(m.group(1)))
    for ep in re.findall(r"E(\d{1,3})", text, re.I):
        num = int(ep)
        if 1 <= num <= 300:
            episodes.add(num)
    for start, end in re.findall(r"E(\d{1,3})\s*[-~至到]\s*E?(\d{1,3})", text, re.I):
        a, b = int(start), int(end)
        if a <= b <= 300:
            episodes.update(range(a, b + 1))
    for start, end in re.findall(r"(?:第)?\s*(\d{1,3})\s*[-~至到]\s*(\d{1,3})\s*(?:集|话|期)", text):
        a, b = int(start), int(end)
        if a <= b <= 300:
            episodes.update(range(a, b + 1))
    for end in re.findall(r"(?:更新至|更至|更新|全|共)\s*(\d{1,3})\s*(?:集|话|期)", text):
        b = int(end)
        if 1 <= b <= 300:
            episodes.update(range(1, b + 1))
    for ep in re.findall(r"(?:第|\b)(\d{1,3})(?:集|话|期)", text):
        num = int(ep)
        if 1 <= num <= 300:
            episodes.add(num)
    return season, sorted(episodes)


def infer_season_episode(text):
    text = text or ""
    season = ""
    episodes = set()
    max_episode = 80
    season_match = re.search(r"\bS(\d{1,2})\b", text, re.I) or re.search(r"\u7b2c\s*(\d{1,2})\s*\u5b63", text)
    if season_match:
        season = str(int(season_match.group(1)))

    for match in re.finditer(r"\bS(\d{1,2})\s*E(\d{1,3})(?:\s*[-~\u2013\u2014\u81f3\u5230]\s*E?(\d{1,3}))?", text, re.I):
        season = str(int(match.group(1)))
        start = int(match.group(2))
        end = int(match.group(3) or start)
        if 1 <= start <= end <= max_episode:
            episodes.update(range(start, end + 1))

    if season:
        for ep in re.findall(r"(?<![A-Za-z0-9])E(\d{1,3})(?!\d)", text, re.I):
            num = int(ep)
            if 1 <= num <= max_episode:
                episodes.add(num)

    for start, end in re.findall(r"\u7b2c?\s*(\d{1,3})\s*[-~\u2013\u2014\u81f3\u5230]\s*(\d{1,3})\s*(?:\u96c6|\u8bdd|\u671f)", text):
        a, b = int(start), int(end)
        if 1 <= a <= b <= max_episode:
            episodes.update(range(a, b + 1))

    for end in re.findall(r"(?:\u66f4\u65b0\u81f3|\u66f4\u81f3|\u66f4\u65b0|\u5168)\s*(\d{1,3})\s*(?:\u96c6|\u8bdd|\u671f)", text):
        b = int(end)
        if 1 <= b <= max_episode:
            episodes.update(range(1, b + 1))

    for ep in re.findall(r"\u7b2c\s*(\d{1,3})\s*(?:\u96c6|\u8bdd|\u671f)", text):
        num = int(ep)
        if 1 <= num <= max_episode:
            episodes.add(num)
    return season, sorted(episodes)


def normalize_resource_text(value):
    if isinstance(value, (list, tuple)):
        return " / ".join(str(v) for v in value if v)
    return str(value or "").strip()


def strip_resource_original_headers(value):
    text = normalize_resource_text(value)
    if not text:
        return ""
    text = re.sub(
        r"(?m)^\s*[\[【][^\]\】\n]*(?:资源转存)?原始消息\s*[\]\】]?\s*$\n?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?m)^\s*(?:HDHiveAPI|TG\s*频道[:：][^\n]*|频道搜索|资源转存)\s*(?:资源转存)?原始消息\s*$\n?",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_resource_resolution(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    match = re.search(r"(?<![A-Za-z0-9])(?:4320|2160|1440|1080|720|576|480)[pPiI](?![A-Za-z0-9])", text)
    if match:
        value = match.group(0)
        return value[:-1] + value[-1].lower()
    if re.search(r"(?<![A-Za-z0-9])8K(?![A-Za-z0-9])", text, re.I):
        return "8K"
    if re.search(r"(?<![A-Za-z0-9])(?:4K|UHD)(?![A-Za-z0-9])", text, re.I):
        return "4K"
    match = re.search(r"(?<!\d)(\d{3,4})\s*[xX×]\s*(\d{3,4})(?!\d)", text)
    if match:
        height = int(match.group(2))
        if 400 <= height <= 5000:
            return f"{height}p"
    return ""


def extract_resource_video_codec(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    patterns = [
        (r"(?<![A-Za-z0-9])(?:H[.\s_-]?265|X265|HEVC)(?![A-Za-z0-9])", "H.265"),
        (r"(?<![A-Za-z0-9])(?:H[.\s_-]?264|X264|AVC)(?![A-Za-z0-9])", "H.264"),
        (r"(?<![A-Za-z0-9])AV1(?![A-Za-z0-9])", "AV1"),
        (r"(?<![A-Za-z0-9])VP9(?![A-Za-z0-9])", "VP9"),
        (r"(?<![A-Za-z0-9])MPEG[.\s_-]?2(?![A-Za-z0-9])", "MPEG-2"),
        (r"(?<![A-Za-z0-9])VC[.\s_-]?1(?![A-Za-z0-9])", "VC-1"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def extract_resource_audio_codec(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    if re.search(r"(?<![A-Za-z0-9])AAC\s*2(?:[.\s]*0)?(?![A-Za-z0-9])", text, re.I):
        return "AAC2.0"
    patterns = [
        (r"(?<![A-Za-z0-9])DTS[.\s_-]?HD[.\s_-]?MA(?:[.\s_-]?5[.\s]*1)?(?![A-Za-z0-9])", "DTS-HD MA"),
        (r"(?<![A-Za-z0-9])DTS[.\s_-]?HD(?![A-Za-z0-9])", "DTS-HD"),
        (r"(?<![A-Za-z0-9])DDP\s*5[.\s]*1(?![A-Za-z0-9])", "DDP5.1"),
        (r"(?<![A-Za-z0-9])AAC(?![A-Za-z0-9])", "AAC"),
        (r"(?<![A-Za-z0-9])E[.\s_-]?AC[.\s_-]?3(?![A-Za-z0-9])", "EAC3"),
        (r"(?<![A-Za-z0-9])AC[.\s_-]?3(?![A-Za-z0-9])", "AC3"),
        (r"(?<![A-Za-z0-9])DDP?\s*5[.\s]*1(?![A-Za-z0-9])", "DD5.1"),
        (r"(?<![A-Za-z0-9])TrueHD(?![A-Za-z0-9])", "TrueHD"),
        (r"(?<![A-Za-z0-9])DTS(?:[.\s_-]?HD)?(?![A-Za-z0-9])", "DTS"),
        (r"(?<![A-Za-z0-9])FLAC(?![A-Za-z0-9])", "FLAC"),
        (r"(?<![A-Za-z0-9])MP3(?![A-Za-z0-9])", "MP3"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def extract_resource_frame_rate(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    match = re.search(r"(?<!\d)(23\.976|24|25|29\.97|30|50|59\.94|60|120)\s*(?:fps|FPS|帧|帧率|Hz|HZ)(?![A-Za-z0-9])", text, re.I)
    if not match:
        match = re.search(r"(?:fps|FPS|帧率)\s*[:：]?\s*(23\.976|24|25|29\.97|30|50|59\.94|60|120)(?!\d)", text, re.I)
    if match:
        value = match.group(1)
        return f"{value}fps"
    return ""


def extract_resource_dolby_vision(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    if re.search(r"(?<![A-Za-z0-9])(?:Dolby\s*Vision|DoVi|DV)(?![A-Za-z0-9])|杜比视界", text, re.I):
        return "杜比视界"
    return ""


def extract_resource_dynamic_range(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    patterns = [
        (r"(?<![A-Za-z0-9])HDR\s*Vivid(?![A-Za-z0-9])|菁彩HDR", "HDR Vivid"),
        (r"(?<![A-Za-z0-9])HDR\s*10\s*\+(?![A-Za-z0-9])", "HDR10+"),
        (r"(?<![A-Za-z0-9])HDR\s*10(?![A-Za-z0-9])", "HDR10"),
        (r"(?<![A-Za-z0-9])HLG(?![A-Za-z0-9])", "HLG"),
        (r"(?<![A-Za-z0-9])HDR(?![A-Za-z0-9])", "HDR"),
        (r"(?<![A-Za-z0-9])SDR(?![A-Za-z0-9])", "SDR"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def extract_resource_enhancement_tags(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return []
    patterns = [
        (r"(?<![A-Za-z0-9])IMAX\s*Enhanced(?![A-Za-z0-9])", "IMAX Enhanced"),
        (r"(?<![A-Za-z0-9])IMAX(?![A-Za-z0-9])", "IMAX"),
        (r"(?<![A-Za-z0-9])10\s*bit(?![A-Za-z0-9])|(?<![A-Za-z0-9])Hi10P(?![A-Za-z0-9])", "10bit"),
        (r"(?<![A-Za-z0-9])Atmos(?![A-Za-z0-9])|杜比全景声", "Atmos"),
        (r"(?<![A-Za-z0-9])DTS\s*:\s*X(?![A-Za-z0-9])|(?<![A-Za-z0-9])DTS[-\s]?X(?![A-Za-z0-9])", "DTS:X"),
        (r"高码率|高码版|高码", "高码率"),
        (r"(?<![A-Za-z0-9])REPACK(?![A-Za-z0-9])", "REPACK"),
        (r"(?<![A-Za-z0-9])PROPER(?![A-Za-z0-9])", "PROPER"),
        (r"(?<![A-Za-z0-9])UNCUT(?![A-Za-z0-9])", "UNCUT"),
    ]
    rows = []
    seen = set()
    for pattern, label in patterns:
        if re.search(pattern, text, re.I) and label.lower() not in seen:
            seen.add(label.lower())
            rows.append(label)
    return rows


def extract_resource_release_method(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    combos = [
        (r"WEB[.\s_-]?DL\s*/\s*WEB[.\s_-]?Rip", "WEB-DL/WEBRip"),
        (r"WEB[.\s_-]?Rip\s*/\s*WEB[.\s_-]?DL", "WEB-DL/WEBRip"),
    ]
    for pattern, label in combos:
        if re.search(pattern, text, re.I):
            return label
    patterns = [
        (r"(?<![A-Za-z0-9])REMUX(?![A-Za-z0-9])", "REMUX"),
        (r"(?<![A-Za-z0-9])WEB[.\s_-]?DL(?![A-Za-z0-9])", "WEB-DL"),
        (r"(?<![A-Za-z0-9])WEB[.\s_-]?Rip(?![A-Za-z0-9])", "WEBRip"),
        (r"(?<![A-Za-z0-9])BluRay(?![A-Za-z0-9])|蓝光", "BluRay"),
        (r"(?<![A-Za-z0-9])BDRip(?![A-Za-z0-9])", "BDRip"),
        (r"(?<![A-Za-z0-9])BRRip(?![A-Za-z0-9])", "BRRip"),
        (r"(?<![A-Za-z0-9])HDTV(?![A-Za-z0-9])", "HDTV"),
        (r"(?<![A-Za-z0-9])DVDRip(?![A-Za-z0-9])", "DVDRip"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def extract_resource_medium(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    patterns = [
        (r"(?<![A-Za-z0-9])UHD\s*Blu[-\s]?Ray(?![A-Za-z0-9])|(?<![A-Za-z0-9])UHD\s*BD(?![A-Za-z0-9])", "UHD BluRay"),
        (r"(?<![A-Za-z0-9])Blu[-\s]?Ray(?![A-Za-z0-9])|(?<![A-Za-z0-9])BD(?![A-Za-z0-9])|蓝光", "BluRay"),
        (r"(?<![A-Za-z0-9])WEB(?![A-Za-z0-9])|WEB[.\s_-]?(?:DL|Rip)", "WEB"),
        (r"(?<![A-Za-z0-9])HDTV(?![A-Za-z0-9])", "HDTV"),
        (r"(?<![A-Za-z0-9])DVD(?![A-Za-z0-9])", "DVD"),
        (r"(?<![A-Za-z0-9])TV(?![A-Za-z0-9])", "TV"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def extract_resource_streaming_platform(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    patterns = [
        (r"(?<![A-Za-z0-9])(?:Bilibili|BiliBili|B-Global|BGlobal|哔哩哔哩|B站)(?![A-Za-z0-9])", "Bilibili"),
        (r"(?<![A-Za-z0-9])(?:Netflix|NF)(?![A-Za-z0-9])", "Netflix"),
        (r"(?<![A-Za-z0-9])(?:AMZN|Amazon)(?![A-Za-z0-9])", "Amazon"),
        (r"(?<![A-Za-z0-9])(?:Disney\+?|DSNP|DisneyPlus)(?![A-Za-z0-9])", "Disney+"),
        (r"(?<![A-Za-z0-9])(?:AppleTV\+?|ATVP|Apple\s*TV)(?![A-Za-z0-9])", "Apple TV+"),
        (r"(?<![A-Za-z0-9])(?:HBO|HMAX|MAX)(?![A-Za-z0-9])", "Max"),
        (r"(?<![A-Za-z0-9])Hulu(?![A-Za-z0-9])", "Hulu"),
        (r"(?<![A-Za-z0-9])(?:Paramount\+?|PMTP)(?![A-Za-z0-9])", "Paramount+"),
        (r"(?<![A-Za-z0-9])Peacock(?![A-Za-z0-9])", "Peacock"),
        (r"(?<![A-Za-z0-9])(?:iQIYI|IQ|爱奇艺)(?![A-Za-z0-9])", "iQIYI"),
        (r"(?<![A-Za-z0-9])(?:Tencent|WeTV|腾讯视频)(?![A-Za-z0-9])", "Tencent"),
        (r"(?<![A-Za-z0-9])(?:Youku|优酷)(?![A-Za-z0-9])", "Youku"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return ""


def extract_resource_file_extension(*values):
    text = " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))
    if not text:
        return ""
    match = re.search(r"\.(mkv|mp4|ts|m2ts|avi|mov|wmv|flv|iso|rmvb|mpeg|mpg)(?:\b|$)", text, re.I)
    if not match:
        match = re.search(r"(?<![A-Za-z0-9])(?:\[|\()?((?:MKV|MP4|M2TS|TS|AVI|MOV|WMV|FLV|ISO|RMVB|MPEG|MPG))(?:\]|\))?(?![A-Za-z0-9])", text, re.I)
    if match:
        return match.group(1).upper()
    return ""


def extract_resource_media_tags(*values):
    tags = {
        "frame_rate": extract_resource_frame_rate(*values),
        "audio_codec": extract_resource_audio_codec(*values),
        "resolution": extract_resource_resolution(*values),
        "video_codec": extract_resource_video_codec(*values),
        "dolby_vision": extract_resource_dolby_vision(*values),
        "dynamic_range": extract_resource_dynamic_range(*values),
        "enhancement_tags": extract_resource_enhancement_tags(*values),
        "resource_medium": extract_resource_medium(*values),
        "release_method": extract_resource_release_method(*values),
        "streaming_platform": extract_resource_streaming_platform(*values),
        "file_extension": extract_resource_file_extension(*values),
    }
    return tags


def flatten_resource_media_tags(tags):
    rows = []
    seen = set()
    for key in (
        "frame_rate",
        "audio_codec",
        "resolution",
        "video_codec",
        "dolby_vision",
        "dynamic_range",
        "enhancement_tags",
        "resource_medium",
        "release_method",
        "streaming_platform",
        "file_extension",
    ):
        value = tags.get(key) if isinstance(tags, dict) else ""
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = normalize_resource_text(item)
            token = compact_match_text(text)
            if text and token and token not in seen:
                seen.add(token)
                rows.append(text)
    return rows


def strip_extracted_media_tags(value):
    text = strip_resource_original_headers(value)
    if not text:
        return ""
    patterns = [
        r"\[?\s*TMDB\s*ID[-:：\s]*\d+\s*\]?",
        r"\[?\s*tmdbid[-:：\s]*\d+\s*\]?",
        r"(?<!\d)(?:23\.976|24|25|29\.97|30|50|59\.94|60|120)\s*(?:fps|FPS|帧|帧率|Hz|HZ)(?![A-Za-z0-9])",
        r"(?:fps|FPS|帧率)\s*[:：]?\s*(?:23\.976|24|25|29\.97|30|50|59\.94|60|120)(?!\d)",
        r"(?<![A-Za-z0-9])(?:4320|2160|1440|1080|720|576|480)[pPiI](?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])(?:8K|4K|UHD)(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])(?:Dolby\s*Vision|DoVi|DV)(?![A-Za-z0-9])|杜比视界",
        r"(?<![A-Za-z0-9])(?:HDR\s*Vivid|HDR\s*10\s*\+|HDR\s*10|HLG|HDR|SDR)(?![A-Za-z0-9])|菁彩HDR",
        r"(?<![A-Za-z0-9])(?:H[.\s_-]?265|X265|HEVC|H[.\s_-]?264|X264|AVC|AV1|VP9|MPEG[.\s_-]?2|VC[.\s_-]?1)(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])(?:IMAX\s*Enhanced|IMAX|10\s*bit|Hi10P|Atmos|DTS\s*:\s*X|DTS[-\s]?X|REPACK|PROPER|UNCUT)(?![A-Za-z0-9])|杜比全景声|高码率|高码版|高码",
        r"(?<![A-Za-z0-9])(?:WEB[.\s_-]?DL\s*/\s*WEB[.\s_-]?Rip|WEB[.\s_-]?Rip\s*/\s*WEB[.\s_-]?DL|WEB[.\s_-]?DL|WEB[.\s_-]?Rip|UHD\s*Blu[-\s]?Ray|UHD\s*BD|Blu[-\s]?Ray|BluRay|BDRip|BRRip|HDTV|DVDRip|REMUX|DVD|WEB)(?![A-Za-z0-9])|蓝光",
        r"(?<![A-Za-z0-9])(?:AAC\s*2(?:[.\s]*0)?|AAC|E[.\s_-]?AC[.\s_-]?3|AC[.\s_-]?3|DDP?\s*5[.\s]*1|TrueHD|DTS[.\s_-]?HD[.\s_-]?MA(?:[.\s_-]?5[.\s]*1)?|DTS[.\s_-]?HD|DTS(?:[.\s_-]?HD)?|FLAC|MP3)(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])(?:Bilibili|BiliBili|B-Global|BGlobal|Netflix|NF|AMZN|Amazon|Disney\+?|DSNP|DisneyPlus|AppleTV\+?|ATVP|Apple\s*TV|HBO|HMAX|MAX|Hulu|Paramount\+?|PMTP|Peacock|iQIYI|IQ|Tencent|WeTV|Youku)(?![A-Za-z0-9])|哔哩哔哩|B站|爱奇艺|腾讯视频|优酷",
        r"(?<![A-Za-z0-9])(?:MP4|MKV|M2TS|TS|AVI|MOV|WMV|FLV|ISO|RMVB|MPEG|MPG)(?![A-Za-z0-9])",
        r"(?:内嵌|外挂)?(?:简中|繁中|中字|中文字幕|字幕)(?:字幕)?(?:\[[^\]]+\])?",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"^\s*[^\w\u4e00-\u9fff]*(?:电视剧|电影|动漫|剧集)\s*[：:]\s*", "", text)
    text = re.sub(r"\[\s*\]", " ", text)
    text = re.sub(r"\[[^\]]{1,24}\]\s*$", " ", text)
    text = re.sub(r"\s*[._|｜/\\]+\s*", " ", text)
    text = re.sub(r"\s+-\s+", " - ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+(?:版本|版)$", "", text)
    return text.strip(" ._-|｜/\\")


def first_resource_title_line(value):
    text = strip_resource_original_headers(value)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^(?:链接|地址|提取码|密码|时间|日期|大小)\s*[：:]", line):
            continue
        return line
    return text[:80].strip()


def compact_match_text(value):
    text = normalize_resource_text(value).lower()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def compact_resource_match_text(value):
    text = normalize_resource_text(value).lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)


def is_precise_resource_row(row, title):
    target = compact_resource_match_text(title)
    if not target:
        return True
    url = normalize_resource_text(row.get("url") or row.get("preview_url"))
    if not url:
        return False
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    title_candidates = [
        normalize_resource_text(row.get("title")),
        normalize_resource_text(row.get("subtitle")),
        normalize_resource_text(raw.get("title")),
        normalize_resource_text(raw.get("name")),
    ]
    title_blob = " ".join(value for value in title_candidates if value)
    if title_blob:
        return target in compact_resource_match_text(title_blob)
    haystack = " ".join([
        normalize_resource_text(row.get("full_text")),
        normalize_resource_text(raw.get("note")),
        normalize_resource_text(raw.get("text")),
    ])
    return target in compact_resource_match_text(haystack)


def resource_text_blob(row):
    raw = row.get("raw") if isinstance(row, dict) and isinstance(row.get("raw"), dict) else {}
    values = [
        row.get("title") if isinstance(row, dict) else "",
        row.get("subtitle") if isinstance(row, dict) else "",
        row.get("quality") if isinstance(row, dict) else "",
        row.get("full_text") if isinstance(row, dict) else "",
        raw.get("title"),
        raw.get("name"),
        raw.get("note"),
        raw.get("remark"),
    ]
    return " ".join(normalize_resource_text(value) for value in values if normalize_resource_text(value))


def resource_size_bytes(value, allow_bare_gb=False):
    text = normalize_resource_text(value)
    if not text:
        return 0.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(TB|T|GB|G|MB|M)", text, re.I)
    if not match:
        if allow_bare_gb and re.fullmatch(r"\d+(?:\.\d+)?", text):
            return float(text) * 1024 * 1024 * 1024
        return 0.0
    number = float(match.group(1))
    unit = match.group(2).upper()
    if unit in {"TB", "T"}:
        return number * 1024 * 1024 * 1024 * 1024
    if unit in {"GB", "G"}:
        return number * 1024 * 1024 * 1024
    return number * 1024 * 1024


def resource_size_gb(row):
    size = row.get("size") if isinstance(row, dict) else ""
    bytes_value = resource_size_bytes(size, allow_bare_gb=True)
    if not bytes_value:
        bytes_value = resource_size_bytes(resource_text_blob(row))
    return bytes_value / (1024 * 1024 * 1024) if bytes_value else 0.0


def resource_rule_tokens_for_group(row, group_key):
    if not isinstance(row, dict):
        return set()
    tags = extract_resource_media_tags(
        row.get("title"),
        row.get("subtitle"),
        row.get("quality"),
        row.get("full_text"),
        row.get("resolution"),
        row.get("video_codec"),
        row.get("audio_codec"),
        row.get("frame_rate"),
        row.get("dolby_vision"),
        row.get("dynamic_range"),
        row.get("enhancement_tags"),
        row.get("resource_medium"),
        row.get("release_method"),
        row.get("streaming_platform"),
        row.get("file_extension"),
    )
    tokens = set()
    if group_key == "resolution":
        resolution = normalize_resource_text(row.get("resolution") or tags.get("resolution")).lower()
        height = 0
        match = re.search(r"(\d{3,4})p", resolution)
        if match:
            height = int(match.group(1))
        if resolution in {"8k", "4320p"} or height >= 4320:
            tokens.add("8k")
        if resolution in {"4k", "uhd", "2160p"} or height >= 2160:
            tokens.add("4k")
        if resolution == "1080p" or height == 1080:
            tokens.add("1080p")
        if resolution in {"720p", "576p", "480p"} or (height and height <= 720):
            tokens.add("720p_low")
        return tokens
    if group_key == "color":
        dolby = normalize_resource_text(row.get("dolby_vision") or tags.get("dolby_vision"))
        dynamic = normalize_resource_text(row.get("dynamic_range") or tags.get("dynamic_range"))
        dynamic_key = compact_match_text(dynamic)
        has_dv = bool(dolby)
        has_hdr = dynamic_key in {"hdr", "hdr10", "hdr10", "hdr10plus", "hdrvivid", "hlg"} or "hdr" in dynamic_key
        if has_dv:
            tokens.add("dv")
        if has_dv and has_hdr:
            tokens.add("dv_hdr")
        if dynamic_key in {"hdr10", "hdr10plus"}:
            tokens.add("hdr10")
        if has_hdr:
            tokens.add("hdr")
        return tokens
    if group_key == "audio":
        value = normalize_resource_text(row.get("audio_codec") or tags.get("audio_codec")).lower()
        blob = resource_text_blob(row)
        for token, pattern in {
            "truehd": r"truehd",
            "dtshdma": r"dts[.\s_-]?hd[.\s_-]?ma",
            "dtsx": r"dts\s*:\s*x|dts[-\s]?x",
            "dtshd": r"dts[.\s_-]?hd",
            "dts": r"\bdts\b",
            "eac3": r"e[.\s_-]?ac[.\s_-]?3|ddp",
            "ac3": r"\bac[.\s_-]?3\b|\bdd\b",
            "flac": r"\bflac\b",
            "aac": r"\baac\b",
        }.items():
            if token in compact_match_text(value) or re.search(pattern, blob, re.I):
                tokens.add(token)
        return tokens
    if group_key == "extension":
        value = normalize_resource_text(row.get("file_extension") or tags.get("file_extension")).upper()
        common = {"MKV", "MP4", "TS", "ISO", "RMVB", "AVI", "MOV", "MPEG", "MPG", "WMV"}
        if value:
            tokens.add(value.lower())
            if value not in common:
                tokens.add("minor")
        elif re.search(r"\.(m2ts|flv|webm|m4v)\b", resource_text_blob(row), re.I):
            tokens.add("minor")
        return tokens
    if group_key == "size":
        gb = resource_size_gb(row)
        if gb <= 0:
            return tokens
        if gb > 115:
            tokens.add("gt115g")
        if gb >= 40:
            tokens.add("ge40g")
        if 20 <= gb < 40:
            tokens.add("20_40g")
        if 10 <= gb < 20:
            tokens.add("10_20g")
        if 5 <= gb < 10:
            tokens.add("5_10g")
        if 0 < gb < 5:
            tokens.add("0_5g")
        tokens.add("big_to_small")
        return tokens
    return tokens


def resource_rules_required_summary(rules):
    groups = (rules or {}).get("groups") if isinstance(rules, dict) else {}
    parts = []
    for group_key, group in (groups or {}).items():
        if not isinstance(group, dict):
            continue
        values = group.get("require") or []
        if values:
            parts.append(f"{group_key}:{','.join(str(value) for value in values)}")
    return " ".join(parts)


def resource_matches_subscription_rules(row, rules, title=""):
    rules = normalize_resource_rules(rules)
    if not rules.get("enabled"):
        return True, "规则未启用"
    if title and not is_precise_resource_row(row, title):
        return False, "资源标题未精准命中订阅名称"
    groups = rules.get("groups") or {}
    for group_key, group in groups.items():
        if not isinstance(group, dict):
            continue
        require = group.get("require") or []
        reject = group.get("reject") or []
        if group_key in {"keyword", "exclude_keyword"}:
            blob = compact_match_text(resource_text_blob(row))
            if group_key == "keyword":
                missing = [value for value in require if compact_match_text(value) not in blob]
                if missing:
                    return False, f"缺少关键词：{', '.join(missing)}"
            blocked = list(reject or []) + (list(require or []) if group_key == "exclude_keyword" else [])
            hit = [value for value in blocked if compact_match_text(value) and compact_match_text(value) in blob]
            if hit:
                return False, f"命中屏蔽词：{', '.join(hit)}"
            continue
        tokens = resource_rule_tokens_for_group(row, group_key)
        if require and not (tokens & set(require)):
            return False, f"未命中规则 {group_key}:{'/'.join(require)}"
        if reject and (tokens & set(reject)):
            return False, f"命中排除规则 {group_key}:{'/'.join(sorted(tokens & set(reject)))}"
    return True, "精准命中资源规则"


def resource_transferable_for_auto(row):
    if not isinstance(row, dict):
        return False, "资源无效"
    points = 0
    try:
        points = int(float(str(row.get("points") or (row.get("raw") or {}).get("unlock_points") or 0)))
    except Exception:
        points = 0
    if row.get("source_key") == "hdhive" and points > 0 and not row.get("unlocked"):
        return False, f"影巢资源需要 {points} 积分，自动转存已跳过"
    text = " ".join([normalize_resource_text(row.get("url")), normalize_resource_text(row.get("preview_url")), resource_text_blob(row)])
    if row.get("source_key") == "hdhive" or re.search(r"https?://(?:115|115cdn|anxia)\.com/s/\w+", text, re.I):
        return True, ""
    return False, "没有可自动转存的 115 链接"


def sort_resource_rule_matches(rows):
    return sorted(
        rows,
        key=lambda row: (
            1 if row.get("source_key") == "hdhive" else 0,
            1 if row.get("unlocked") else 0,
            resource_size_gb(row),
        ),
        reverse=True,
    )


def format_pansou_full_text(item, source_label, url, password, date):
    note = normalize_resource_text(item.get("note")) or normalize_resource_text(item.get("title")) or normalize_resource_text(item.get("name"))
    note = strip_resource_original_headers(note)
    note_text = note.lower()
    url_text = normalize_resource_text(url)
    password_text = normalize_resource_text(password)
    lines = []
    if note:
        lines.append(note)
    if date and not re.search(r"(?:时间|日期|发布)\s*[：:]", note):
        lines.append(f"时间：{date}")
    if url_text and url_text.lower() not in note_text:
        lines.append(f"链接：{url_text}")
    if password_text:
        has_password = password_text.lower() in note_text or re.search(r"(?:password|pwd|提取码|密码)[=：:\s]*[A-Za-z0-9]{4,}", note, re.I)
        if not has_password:
            lines.append(f"提取码：{password_text}")
    return "\n".join(lines).strip()


def normalize_channel_alias(value):
    text = normalize_resource_text(value).lower()
    text = re.sub(r"^(tg:|plugin:)", "", text).strip()
    text = re.sub(r"^https?://t\.me/s?/", "", text, flags=re.I).strip()
    return text.strip().strip("/").lstrip("@")


def configured_telegram_channel_scope():
    try:
        from app.config import read_config
        cfg = read_config()
    except Exception:
        return []
    rows = []
    raw = cfg.get("ENV_TG_CHANNELS") or "[]"
    try:
        data = json.loads(raw)
    except Exception:
        data = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("enabled") is False or str(item.get("enabled") or "").strip().lower() in {"0", "false", "no", "off", "disabled"}:
                continue
            name = str(item.get("name") or item.get("username") or item.get("input") or "").strip()
            values = [item.get("username"), item.get("input"), item.get("id"), item.get("name")]
            aliases = {normalize_channel_alias(value) for value in values if normalize_channel_alias(value)}
            preferred = normalize_channel_alias(item.get("username") or item.get("input") or item.get("id") or "")
            if aliases and preferred:
                rows.append({"name": name or preferred, "aliases": aliases, "preferred": preferred, "mode": str(item.get("mode") or "incoming")})
    if not rows:
        legacy = str(cfg.get("ENV_115_TG_CHANNEL") or "").strip()
        for value in [part.strip() for part in legacy.split("|") if part.strip()]:
            alias = normalize_channel_alias(value)
            if alias:
                rows.append({"name": alias, "aliases": {alias}, "preferred": alias})
    return rows


def apply_pansou_channel_scope(pansou, scope):
    channels = [item["preferred"] for item in scope if item.get("preferred")]
    pansou.PANSOU_CHANNELS = ",".join(dict.fromkeys(channels))
    pansou.PANSOU_PLUGINS = ""


def pansou_source_label(source, channel_scope):
    source_alias = normalize_channel_alias(source)
    if channel_scope:
        for item in channel_scope:
            if source_alias and source_alias in item.get("aliases", set()):
                return f"TG频道: {item.get('name') or source_alias}"
        if len(channel_scope) == 1:
            item = channel_scope[0]
            return f"TG频道: {item.get('name') or item.get('preferred') or '频道'}"
        return ""
    if source:
        return source.replace("tg:", "TG频道: ").replace("plugin:", "插件: ")
    return "频道搜索"


def fetch_configured_pansou_resources(title):
    scope = configured_telegram_channel_scope()
    if not scope:
        return []
    pansou = load_pansou_module()
    apply_pansou_channel_scope(pansou, scope)
    return normalize_pansou_resources(pansou._fetch_pansou_resources(title), scope)


def classify_drive_from_url(url):
    text = str(url or "").lower()
    if "115.com" in text:
        return "115"
    if "123pan" in text or "123684.com" in text or "123865.com" in text:
        return "123"
    if "quark" in text:
        return "夸克"
    if "uc.cn" in text or "drive.uc" in text:
        return "UC"
    if "baidu" in text:
        return "百度"
    if "aliyun" in text or "alipan" in text:
        return "阿里"
    if "cloud.189" in text:
        return "天翼"
    return "链接"


def extract_resource_urls(text):
    urls = re.findall(r"https?://[^\s，,。；;）)】\]]+", str(text or ""))
    cleaned = []
    for url in urls:
        value = html_lib.unescape(url).replace("\\/", "/").rstrip(".。\"'")
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


RESOURCE_PREVIEW_PREFERRED_HOSTS = (
    "115.com",
    "115cdn.com",
    "anxia.com",
    "quark.cn",
    "uc.cn",
    "aliyundrive.com",
    "alipan.com",
    "pan.baidu.com",
    "cloud.189.cn",
)


def is_preferred_resource_preview_link(url):
    host = (urllib.parse.urlparse(str(url or "")).netloc or "").lower()
    return any(host == item or host.endswith("." + item) for item in RESOURCE_PREVIEW_PREFERRED_HOSTS)


def clean_resource_preview_text(raw):
    text = html_lib.unescape(str(raw or "")).replace("\\/", "/")
    text = re.sub(r"(?is)<(script|style|noscript|svg|canvas)[^>]*>.*?</\1>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|td|th|h[1-6]|section|article|header|footer)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    lines = []
    for line in text.splitlines():
        value = re.sub(r"\s+", " ", line).strip()
        if value and value not in lines:
            lines.append(value)
    return "\n".join(lines)[:8000]


def preview_candidate_urls(payload, item):
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    values = []
    for value in payload.get("urls") or []:
        values.append(value)
    for key in ("share_url", "preview_url", "url", "media_url"):
        values.append(item.get(key))
        values.append(raw.get(key))
    values.extend(extract_resource_urls(payload.get("fallback_text") or item.get("full_text") or ""))
    urls = []
    for value in values:
        url = str(value or "").strip()
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if url not in urls:
            urls.append(url)
    return urls[:6]


def preview_link_sort_key(url):
    return (0 if is_preferred_resource_preview_link(url) else 1, len(url), url)


def fetch_resource_preview(payload):
    payload = payload if isinstance(payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    fallback_text = clean_resource_preview_text(payload.get("fallback_text") or item.get("full_text") or "")
    urls = preview_candidate_urls(payload, item)
    fetched = []
    page_texts = []
    links = []
    errors = []
    for url in urls:
        try:
            raw = http_text(url, timeout=12)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            fetched.append({"url": url, "ok": False, "error": str(exc)})
            continue
        raw = html_lib.unescape(raw).replace("\\/", "/")
        page_text = clean_resource_preview_text(raw)
        page_links = extract_resource_urls(raw + "\n" + page_text)
        for link in page_links:
            if link not in links:
                links.append(link)
        fetched.append({"url": url, "ok": True, "links": len(page_links), "chars": len(page_text)})
        if page_text:
            page_texts.append(f"来源：{url}\n{page_text}")

    for url in urls:
        if url not in links:
            links.append(url)
    links = sorted(links, key=preview_link_sort_key)[:24]
    text_parts = []
    if fallback_text:
        text_parts.append(fallback_text)
    if page_texts:
        text_parts.append("\n\n".join(page_texts))
    text = "\n\n".join(part for part in text_parts if part).strip()
    return {
        "success": True,
        "title": item.get("title") or item.get("name") or "",
        "text": text,
        "links": links,
        "preferred_links": [link for link in links if is_preferred_resource_preview_link(link)],
        "fetched": fetched,
        "errors": errors[:6],
    }


def extract_resource_password(text, url=""):
    for value in [url, text]:
        match = re.search(r"(?:password|pwd|提取码|密码)[=：:\s]*([A-Za-z0-9]{4,})", str(value or ""), re.I)
        if match:
            return match.group(1)
    return ""


def extract_resource_size(text):
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:GB|G|MB|M|TB|T))", str(text or ""), re.I)
    return match.group(1).replace(" ", "") if match else ""


def telegram_message_url(channel, message_id):
    username = str(channel.get("username") or "").strip().lstrip("@")
    if username and message_id:
        return f"https://t.me/{username}/{message_id}"
    return ""


def is_resource_meta_line(line):
    text = normalize_resource_text(line)
    if not text:
        return True
    if re.search(r"https?://", text):
        return True
    if re.match(r"^(?:链接|地址|提取码|密码|时间|日期|大小|来源)\s*[：:]", text):
        return True
    compact = compact_match_text(strip_extracted_media_tags(text))
    return not compact or compact in {"4k", "1080p", "720p", "web", "webrip", "webdl"}


def telegram_resource_context_for_url(text, url):
    lines = [line.strip() for line in str(text or "").splitlines()]
    url_index = next((idx for idx, line in enumerate(lines) if url in line), -1)
    if url_index < 0:
        title = strip_extracted_media_tags(first_resource_title_line(text))
        return title, text
    title_index = -1
    lower_bound = max(0, url_index - 8)
    for idx in range(url_index, lower_bound - 1, -1):
        line = lines[idx]
        if is_resource_meta_line(line):
            continue
        title_index = idx
        break
    start = title_index if title_index >= 0 else max(0, url_index - 2)
    end = min(len(lines), url_index + 3)
    for idx in range(url_index + 1, min(len(lines), url_index + 8)):
        if lines[idx] and re.search(r"https?://", lines[idx]):
            end = idx
            break
        if lines[idx] and not is_resource_meta_line(lines[idx]):
            end = idx
            break
    note = "\n".join(line for line in lines[start:end] if line).strip()
    title_line = lines[title_index] if title_index >= 0 else first_resource_title_line(note)
    title = strip_extracted_media_tags(title_line) or title_line
    return title, note or text


def normalize_telegram_channel_resources(messages):
    rows = []
    seen = set()
    for item in messages:
        raw_text = normalize_resource_text(item.get("text"))
        text = strip_resource_original_headers(raw_text)
        if not text:
            continue
        urls = extract_resource_urls(text)
        if not urls:
            continue
        date = normalize_resource_text(item.get("date"))
        source_label = f"TG频道: {item.get('channel_name') or item.get('channel_username') or '频道'}"
        source_raw = item.get("channel_username") or item.get("channel_name") or ""
        for url in urls[:3]:
            display_title, note = telegram_resource_context_for_url(text, url)
            season, episodes = infer_season_episode(note)
            media_tags = extract_resource_media_tags(note)
            key = (url, note[:80])
            if key in seen:
                continue
            seen.add(key)
            password = extract_resource_password(note, url) or extract_resource_password(text, url)
            rows.append({
                "source": "频道搜索",
                "source_key": "pansou",
                "source_label": source_label,
                "source_raw": source_raw,
                "drive": classify_drive_from_url(url),
                "title": display_title or first_resource_title_line(note) or note[:80],
                "subtitle": date,
                "size": extract_resource_size(note) or extract_resource_size(text),
                "quality": "",
                **media_tags,
                "url": url,
                "preview_url": item.get("message_url") or url,
                "password": password,
                "date": date,
                "season": season,
                "episodes": episodes,
                "full_text": format_pansou_full_text({"note": note}, source_label, url, password, date),
                "raw": {"message_id": item.get("message_id"), "channel": source_raw, "text": raw_text},
            })
    return rows


def fetch_configured_telegram_resources(title, limit_per_channel=60):
    async def _inner():
        from app.telegram_runtime import _connected_client, _credentials, _entity_lookup_value, _read_channels_from_config
        _, api_id, api_hash = _credentials({})
        channels = _read_channels_from_config()
        if not channels:
            return []
        rows = []
        async with _connected_client(api_id, api_hash) as client:
            for channel in channels:
                if channel.get("enabled") is False or str(channel.get("enabled") or "").strip().lower() in {"0", "false", "no", "off", "disabled"}:
                    continue
                source = channel.get("username") or channel.get("input") or channel.get("id")
                if not source:
                    continue
                try:
                    entity = await client.get_entity(_entity_lookup_value(source))
                    async for msg in client.iter_messages(entity, search=title, limit=limit_per_channel):
                        text = getattr(msg, "message", "") or ""
                        rows.append({
                            "channel_name": channel.get("name") or getattr(entity, "title", "") or source,
                            "channel_username": channel.get("username") or getattr(entity, "username", "") or source,
                            "message_id": getattr(msg, "id", ""),
                            "message_url": telegram_message_url(channel, getattr(msg, "id", "")),
                            "date": getattr(getattr(msg, "date", None), "strftime", lambda *_: "")("%Y-%m-%d %H:%M"),
                            "text": text,
                        })
                except Exception:
                    continue
        return rows

    return normalize_telegram_channel_resources(asyncio.run(_inner()))


def normalize_pansou_resources(raw, channel_scope=None):
    rows = []
    if not isinstance(raw, dict):
        return rows
    channel_scope = channel_scope or []
    drive_labels = {
        "115": "115",
        "123": "123",
        "quark": "夸克",
        "uc": "UC",
        "baidu": "百度",
        "aliyun": "阿里",
        "tianyi": "天翼",
        "magnet": "磁力",
    }
    for drive, items in raw.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            note = normalize_resource_text(item.get("note")) or normalize_resource_text(item.get("title")) or normalize_resource_text(item.get("name"))
            note = strip_resource_original_headers(note)
            url = normalize_resource_text(item.get("url"))
            password = normalize_resource_text(item.get("password"))
            date = normalize_resource_text(item.get("datetime"))
            source = normalize_resource_text(item.get("source"))
            source_label = pansou_source_label(source, channel_scope)
            if channel_scope and not source_label:
                continue
            season, episodes = infer_season_episode(note)
            media_tags = extract_resource_media_tags(note)
            rows.append({
                "source": "频道搜索",
                "source_key": "pansou",
                "source_label": source_label,
                "drive": drive_labels.get(str(drive).lower(), str(drive)),
                "title": strip_extracted_media_tags(first_resource_title_line(note)) or first_resource_title_line(note) or url,
                "subtitle": date,
                "size": "",
                "quality": "",
                **media_tags,
                "url": url,
                "password": password,
                "date": date,
                "season": season,
                "episodes": episodes,
                "full_text": format_pansou_full_text(item, source_label, url, password, date),
                "raw": item,
                "source_raw": source,
            })
    return rows


def is_hdhive_115_resource(item, url):
    pan_type = normalize_resource_text(item.get("pan_type")).lower()
    if pan_type == "115":
        return True
    text = " ".join([
        url,
        normalize_resource_text(item.get("share_url")),
        normalize_resource_text(item.get("media_url")),
        normalize_resource_text(item.get("remark")),
        normalize_resource_text(item.get("title")),
    ]).lower()
    return "115cdn.com" in text or "115.com" in text or "115://" in text


def normalize_hdhive_resources(raw):
    rows = []
    if not isinstance(raw, dict):
        return rows
    data = raw.get("data")
    if not isinstance(data, list):
        return rows
    for item in data:
        if not isinstance(item, dict):
            continue
        title_parts = [
            normalize_resource_text(item.get("title")),
            normalize_resource_text(item.get("remark")),
        ]
        raw_title = strip_resource_original_headers(" ".join(part for part in title_parts if part).strip())
        size = normalize_resource_text(item.get("share_size"))
        media_tags = extract_resource_media_tags(
            normalize_resource_text(item.get("video_resolution")),
            normalize_resource_text(item.get("video_codec")),
            normalize_resource_text(item.get("video_encoding")),
            normalize_resource_text(item.get("codec")),
            normalize_resource_text(item.get("audio_codec")),
            normalize_resource_text(item.get("audio_encoding")),
            normalize_resource_text(item.get("frame_rate")),
            normalize_resource_text(item.get("fps")),
            normalize_resource_text(item.get("source")),
            normalize_resource_text(item.get("media_source")),
            normalize_resource_text(item.get("streaming_platform")),
            normalize_resource_text(item.get("file_extension")),
            raw_title,
        )
        excluded_quality_keys = {compact_match_text(part) for part in flatten_resource_media_tags(media_tags)}
        quality = " ".join(part for part in [
            normalize_resource_text(item.get("source")),
            normalize_resource_text(item.get("subtitle_language")),
            normalize_resource_text(item.get("subtitle_type")),
        ] if part and compact_match_text(part) not in excluded_quality_keys).strip()
        title = strip_extracted_media_tags(raw_title) or raw_title
        url = normalize_resource_text(item.get("share_url")) or normalize_resource_text(item.get("url")) or normalize_resource_text(item.get("media_url"))
        hdhive_url = ""
        slug = normalize_resource_text(item.get("slug"))
        if slug:
            hdhive_url = f"https://hdhive.com/resource/{slug}"
        if not is_hdhive_115_resource(item, url):
            continue
        season, episodes = infer_season_episode(title)
        rows.append({
            "source": "HDHiveAPI",
            "source_key": "hdhive",
            "source_label": "HDHiveAPI",
            "drive": normalize_resource_text(item.get("pan_type")) or "HDHive",
            "title": title or hdhive_url or url,
            "subtitle": quality,
            "size": size,
            "quality": quality,
            **media_tags,
            "url": hdhive_url or url,
            "preview_url": normalize_resource_text(item.get("media_url")),
            "password": "",
            "date": normalize_resource_text(item.get("created_at"))[:10],
            "season": season,
            "episodes": episodes,
            "points": item.get("unlock_points"),
            "unlocked": bool(item.get("is_unlocked")),
            "full_text": "\n".join(part for part in [
                title,
                " ".join(flatten_resource_media_tags(media_tags) + ([quality] if quality else [])),
                f"大小：{size}" if size else "",
                f"链接：{normalize_resource_text(item.get('media_url')) or url}" if (normalize_resource_text(item.get("media_url")) or url) else "",
            ] if part),
            "raw": item,
        })
    return rows


def search_tmdb_candidates(title, media_type=""):
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        return []
    candidates = []
    seen = set()

    def add_from(endpoint_type):
        endpoint = f"{cfg['api_base_url']}/search/{endpoint_type}"
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "include_adult": "false",
            "query": title,
            "page": "1",
        }
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params))
        for item in data.get("results", []) or []:
            item_type = endpoint_type if endpoint_type in ("movie", "tv") else item.get("media_type")
            tmdb_id = item.get("id")
            if item_type not in ("movie", "tv") or not tmdb_id:
                continue
            key = (item_type, int(tmdb_id))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(key)
            if len(candidates) >= 4:
                return

    if media_type in ("movie", "tv"):
        add_from(media_type)
    if len(candidates) < 4:
        add_from("multi")
    return candidates


def search_media(query):
    title = (query.get("title") or query.get("query") or "").strip()
    if not title:
        raise RuntimeError("缺少搜索关键词")
    cfg = load_tmdb_config()
    media_filter = (query.get("type") or "").strip()
    page = parse_positive_int(query.get("page"), 1, 1, 500)
    limit = parse_positive_int(query.get("limit"), 24, 1, 24)
    endpoint_type = media_filter if media_filter in ("movie", "tv") else "multi"
    endpoint = f"{cfg['api_base_url']}/search/{endpoint_type}"
    params = {
        "api_key": cfg["api_key"],
        "language": "zh-CN",
        "include_adult": "false",
        "query": title,
        "page": str(page),
    }
    data = http_json(endpoint + "?" + urllib.parse.urlencode(params))
    rows = []
    for item in data.get("results", []) or []:
        media_type = endpoint_type if endpoint_type in ("movie", "tv") else item.get("media_type")
        if media_type not in ("movie", "tv"):
            continue
        normalized = normalize_tmdb_item(item, media_type)
        normalized["source"] = "TMDB搜索"
        rows.append(normalized)
        if len(rows) >= limit:
            break
    if media_filter == "tv":
        rows = enrich_tmdb_tv_items(rows)
    else:
        rows = enrich_library_status_items(rows)
    total_results = int(data.get("total_results") or len(rows))
    total_pages = max(1, (total_results + limit - 1) // limit) if total_results else 1
    return {
        "success": True,
        "source": "影片搜索",
        "query": title,
        "items": rows,
        "page": page,
        "limit": limit,
        "total_results": total_results,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "generated_at": int(time.time()),
    }


_search_media_uncached = search_media


def search_media(query):
    query = dict(query or {})
    return cached_discover_call("search_media", query, lambda: _search_media_uncached(query))


def search_resources_legacy_disabled(query):
    title = (query.get("title") or "").strip()
    media_type = "tv" if query.get("type") in ("tv", "电视剧") else "movie"
    tmdb_id_raw = (query.get("tmdb_id") or "").strip()
    if tmdb_id_raw and not tmdb_id_raw.isdigit():
        tmdb_id_raw = ""
    if tmdb_id_raw and not tmdb_id_raw.isdigit():
        tmdb_id_raw = ""
    if not title:
        raise RuntimeError("缺少搜索标题")

    errors = []
    pansou_rows = []
    hdhive_rows = []

    try:
        pansou_rows = fetch_configured_telegram_resources(title)
    except Exception as exc:
        errors.append(f"频道搜索失败：{exc}")

    if tmdb_id_raw:
        try:
            hdhive = load_hdhive_module()
            api = hdhive.HDHiveAPI()
            hdhive_rows = normalize_hdhive_resources(api.get_resources_via_openapi(media_type, int(tmdb_id_raw)))
        except Exception as exc:
            errors.append(f"HDHiveAPI 搜索失败：{exc}")
    else:
        try:
            hdhive = load_hdhive_module()
            api = hdhive.HDHiveAPI()
            for candidate_type, candidate_id in search_tmdb_candidates(title, media_type):
                hdhive_rows.extend(normalize_hdhive_resources(api.get_resources_via_openapi(candidate_type, candidate_id)))
        except Exception as exc:
            errors.append(f"HDHiveAPI 搜索失败：{exc}")

    rows = hdhive_rows + pansou_rows
    tmdb_lookup_id = int(tmdb_id_raw) if tmdb_id_raw.isdigit() else 0
    if not tmdb_lookup_id:
        for item in matched_tmdb:
            if item.get("type") == "tv" and str(item.get("id", "")).isdigit():
                tmdb_lookup_id = int(item["id"])
                break
    season_tabs = build_resource_season_status(rows, title, tmdb_lookup_id) if media_type == "tv" else []

    sources = [
        {"key": "all", "label": "全部", "count": len(rows)},
        {"key": "pansou", "label": "频道搜索", "count": len(pansou_rows)},
        {"key": "hdhive", "label": "HDHiveAPI", "count": len(hdhive_rows)},
    ]
    return {
        "success": True,
        "title": title,
        "media_type": media_type,
        "tmdb_id": tmdb_id_raw,
        "items": rows[:120],
        "sources": sources,
        "seasons": season_tabs,
        "errors": errors,
        "generated_at": int(time.time()),
    }


TMDB_MATCH_CACHE_PATH = str(PROJECT_ROOT / "db" / "tmdb_match_cache.json")


def _read_tmdb_match_cache():
    try:
        path = Path(TMDB_MATCH_CACHE_PATH)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_tmdb_match_cache(data):
    path = Path(TMDB_MATCH_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _tmdb_match_cache_key(title, media_type, year=""):
    return "|".join([compact_match_text(title), media_type or "", str(year or "")])


def search_tmdb_candidates(title, media_type="", year=""):
    cfg = load_tmdb_config()
    if not cfg["api_key"]:
        return []
    media_type = media_type if media_type in ("movie", "tv") else ""
    year = extract_year(year)
    cache = _read_tmdb_match_cache()
    cache_key = _tmdb_match_cache_key(title, media_type or "multi", year)
    cached = cache.get(cache_key)
    if isinstance(cached, list):
        return [(item.get("type"), int(item.get("id"))) for item in cached if item.get("type") in ("movie", "tv") and str(item.get("id", "")).isdigit()]

    candidates = []
    seen = set()
    endpoints = [media_type] if media_type in ("movie", "tv") else ["multi"]
    if "multi" not in endpoints:
        endpoints.append("multi")

    for endpoint_type in endpoints:
        endpoint = f"{cfg['api_base_url']}/search/{endpoint_type}"
        params = {
            "api_key": cfg["api_key"],
            "language": "zh-CN",
            "include_adult": "false",
            "query": title,
            "page": "1",
        }
        if year and endpoint_type == "tv":
            params["first_air_date_year"] = year
        elif year and endpoint_type == "movie":
            params["primary_release_year"] = year
        data = http_json(endpoint + "?" + urllib.parse.urlencode(params), timeout=12)
        for item in data.get("results", []) or []:
            item_type = endpoint_type if endpoint_type in ("movie", "tv") else item.get("media_type")
            tmdb_id = item.get("id")
            if item_type not in ("movie", "tv") or not tmdb_id:
                continue
            if media_type and item_type != media_type:
                continue
            key = (item_type, int(tmdb_id))
            if key in seen:
                continue
            seen.add(key)
            name = item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or ""
            date = item.get("release_date") or item.get("first_air_date") or ""
            score = 0
            if compact_match_text(name) == compact_match_text(title):
                score += 10
            if year and str(date).startswith(year):
                score += 5
            candidates.append((score, key))
    candidates.sort(key=lambda item: item[0], reverse=True)
    result = [key for _, key in candidates[:4]]
    cache[cache_key] = [{"type": item_type, "id": tmdb_id} for item_type, tmdb_id in result]
    _write_tmdb_match_cache(cache)
    return result


def fetch_tmdb_season_episodes(tmdb_id, season_numbers):
    cfg = load_tmdb_config()
    if not cfg["api_key"] or not tmdb_id:
        return {}
    seasons = {}
    for season in sorted({str(s) for s in season_numbers if str(s).isdigit()}, key=lambda value: int(value)):
        try:
            data = get_cached_tmdb_season_detail(tmdb_id, season)
        except Exception:
            continue
        episodes = []
        for item in data.get("episodes", []) or []:
            try:
                number = int(item.get("episode_number") or 0)
            except Exception:
                number = 0
            if number > 0:
                episodes.append(number)
        if episodes:
            seasons[season] = sorted(set(episodes))
    return seasons


def build_resource_season_status(rows, title, tmdb_id):
    resource = {}
    season_only = set()
    for row in rows:
        season = str(row.get("season") or "").strip()
        episodes = [int(ep) for ep in (row.get("episodes") or []) if str(ep).isdigit()]
        if not season and episodes:
            season = "1"
        if not season:
            continue
        bucket = resource.setdefault(season, set())
        if episodes:
            bucket.update(ep for ep in episodes if ep > 0)
        else:
            season_only.add(season)

    library = get_library_season_episodes(title, tmdb_id)
    wanted_seasons = set(resource)
    tmdb = fetch_tmdb_season_episodes(tmdb_id, wanted_seasons) if tmdb_id else {}
    wanted_seasons |= set(tmdb)

    status = []
    for season in sorted(wanted_seasons, key=lambda value: int(value) if str(value).isdigit() else 999):
        resource_eps = sorted(resource.get(season) or [])
        tmdb_eps = tmdb.get(season) or []
        library_eps = library.get(season) or []
        if resource_eps:
            expected = resource_eps
            source = "resource"
        elif tmdb_eps:
            expected = tmdb_eps
            source = "tmdb"
        else:
            expected = library_eps
            source = "library"
        missing = [ep for ep in expected if ep not in set(library_eps)] if expected and library_eps else []
        notice = ""
        if season in season_only and not resource_eps and tmdb_eps:
            notice = f"资源只标注第 {season} 季，已按 TMDB 补全 {len(tmdb_eps)} 集"
        if expected and not library_eps:
            notice = f"Emby 库未找到第 {season} 季，可能缺集"
        elif missing:
            notice = f"Emby 库第 {season} 季缺集：{', '.join(str(ep) for ep in missing[:30])}"
        status.append({
            "season": season,
            "episodes": expected,
            "resource_episodes": resource_eps,
            "tmdb_episodes": tmdb_eps,
            "library_episodes": library_eps,
            "missing_episodes": missing,
            "source": source,
            "notice": notice,
        })
    return status


def build_resource_season_status(rows, title, tmdb_id):
    resource = {}
    season_only = set()
    for row in rows:
        season = str(row.get("season") or "").strip()
        episodes = []
        for ep in row.get("episodes") or []:
            try:
                value = int(ep)
            except Exception:
                continue
            if 1 <= value <= 80:
                episodes.append(value)
        if not season and episodes:
            season = "1"
        if not season:
            continue
        resource.setdefault(season, set())
        if episodes:
            resource[season].update(episodes)
        else:
            season_only.add(season)

    library = get_library_season_episodes(title, tmdb_id)
    tmdb = fetch_tmdb_season_episodes(tmdb_id, set(resource)) if tmdb_id else {}
    status = []
    for season in sorted(resource, key=lambda value: int(value) if str(value).isdigit() else 999):
        resource_eps = sorted(resource.get(season) or [])
        tmdb_eps = tmdb.get(season) or []
        library_eps = library.get(season) or []
        expected = resource_eps or (tmdb_eps if season in season_only else [])
        source = "resource" if resource_eps else ("tmdb" if expected else "season")
        missing = [ep for ep in expected if ep not in set(library_eps)] if expected and library_eps else []
        notice = ""
        if season in season_only and not resource_eps:
            notice = f"资源只标注第 {season} 季，未标明具体集数"
            if tmdb_eps:
                notice = f"资源只标注第 {season} 季，已按 TMDB 显示完整集数"
        if expected and not library_eps:
            notice = f"{notice}；Emby 库未找到第 {season} 季，可能缺集" if notice else f"Emby 库未找到第 {season} 季，可能缺集"
        elif missing:
            notice = f"Emby 库第 {season} 季缺集：{', '.join(str(ep) for ep in missing[:30])}"
        status.append({
            "season": season,
            "episodes": expected,
            "resource_episodes": resource_eps,
            "tmdb_episodes": tmdb_eps,
            "library_episodes": library_eps,
            "missing_episodes": missing,
            "source": source,
            "notice": notice,
        })
    return status


def fetch_tmdb_tv_episode_map(tmdb_id):
    cfg = load_tmdb_config()
    if not cfg["api_key"] or not tmdb_id:
        return {}
    endpoint = f"{cfg['api_base_url']}/tv/{int(tmdb_id)}"
    params = urllib.parse.urlencode({"api_key": cfg["api_key"], "language": "zh-CN"})
    try:
        data = http_json(endpoint + "?" + params, timeout=12)
    except Exception:
        return {}
    seasons = {}
    for item in data.get("seasons", []) or []:
        try:
            season = int(item.get("season_number") or 0)
            count = int(item.get("episode_count") or 0)
        except Exception:
            continue
        if season <= 0 or count <= 0:
            continue
        seasons[str(season)] = list(range(1, count + 1))
    return seasons


def build_resource_season_status(rows, title, tmdb_id):
    resource = {}
    for row in rows:
        season = str(row.get("season") or "").strip()
        episodes = []
        for ep in row.get("episodes") or []:
            try:
                value = int(ep)
            except Exception:
                continue
            if 1 <= value <= 80:
                episodes.append(value)
        if not season and episodes:
            season = "1"
        if not season:
            continue
        resource.setdefault(season, set()).update(episodes)

    tmdb = fetch_tmdb_tv_episode_map(tmdb_id) if tmdb_id else {}
    library = get_library_season_episodes(title, tmdb_id)
    season_keys = set(tmdb) or set(resource) or set(library)
    status = []
    total_episodes = 0
    total_library = 0
    total_missing = 0
    total_resource = 0
    for season in sorted(season_keys, key=lambda value: int(value) if str(value).isdigit() else 999):
        tmdb_eps = tmdb.get(season) or []
        resource_eps = sorted(resource.get(season) or [])
        library_eps = library.get(season) or []
        episodes = tmdb_eps or resource_eps or library_eps
        library_set = set(library_eps)
        missing = [ep for ep in episodes if ep not in library_set] if episodes else []
        total_episodes += len(episodes)
        total_library += len([ep for ep in episodes if ep in set(library_eps)])
        total_resource += len([ep for ep in episodes if ep in set(resource_eps)])
        total_missing += len(missing)
        notice = ""
        if episodes and not library_eps:
            notice = f"Emby 库未找到第 {season} 季，缺 {len(episodes)} 集"
        elif missing:
            notice = f"Emby 库第 {season} 季缺 {len(missing)} 集"
        status.append({
            "season": season,
            "episodes": episodes,
            "tmdb_episodes": tmdb_eps,
            "resource_episodes": resource_eps,
            "library_episodes": library_eps,
            "missing_episodes": missing,
            "source": "tmdb" if tmdb_eps else ("resource" if resource_eps else "library"),
            "notice": notice,
        })
    if status:
        status[0]["summary"] = {
            "total_seasons": len(status),
            "total_episodes": total_episodes,
            "current_episodes": total_resource or total_library,
            "resource_episodes": total_resource,
            "library_episodes": total_library,
            "missing_episodes": total_missing,
        }
    return status


def telegram_channel_scope_signature():
    scope = configured_telegram_channel_scope()
    if not scope:
        return "all"
    parts = []
    for item in scope:
        aliases = sorted(alias for alias in (item.get("aliases") or []) if alias)
        preferred = normalize_channel_alias(item.get("preferred") or "")
        parts.append(preferred + "|" + ",".join(aliases) + "|" + str(item.get("mode") or "incoming"))
    return ";".join(sorted(part for part in parts if part)) or "all"


def resource_source_cache_query(source, title, media_type, year="", tmdb_id="", channel_signature=""):
    query = {
        "version": "resource_clean_v4",
        "source": str(source or ""),
        "title": compact_match_text(title),
        "media_type": str(media_type or ""),
        "year": str(year or ""),
        "tmdb_id": str(tmdb_id or ""),
    }
    if source == "pansou":
        query["channels"] = channel_signature or telegram_channel_scope_signature()
    return query


def get_cached_resource_source(source, title, media_type, year="", tmdb_id="", channel_signature="", force=False):
    if force:
        return None
    cached = get_discover_cache(
        "resource_source",
        resource_source_cache_query(source, title, media_type, year, tmdb_id, channel_signature),
    )
    if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
        return cached
    return None


def set_cached_resource_source(source, title, media_type, rows, year="", tmdb_id="", channel_signature="", matched_tmdb=None):
    rows = rows if isinstance(rows, list) else []
    ttl = DISCOVER_PERMANENT_CACHE_SECONDS if rows else DISCOVER_CACHE_TTL_SECONDS
    payload = {
        "success": True,
        "source": source,
        "title": title,
        "media_type": media_type,
        "year": year,
        "tmdb_id": tmdb_id,
        "rows": rows[:240],
        "matched_tmdb": matched_tmdb or [],
    }
    set_discover_cache(
        "resource_source",
        resource_source_cache_query(source, title, media_type, year, tmdb_id, channel_signature),
        payload,
        ttl=ttl,
    )
    return rows


def search_resources(query):
    title = (query.get("title") or "").strip()
    media_type = "tv" if query.get("type") in ("tv", "电视剧") else "movie"
    year = extract_year(query.get("year") or "")
    tmdb_id_raw = (query.get("tmdb_id") or "").strip()
    if tmdb_id_raw and not tmdb_id_raw.isdigit():
        tmdb_id_raw = ""
    if not title:
        raise RuntimeError("缺少搜索标题")

    errors = []
    matched_tmdb = []
    cache_hits = []
    force_refresh = str(query.get("refresh") or query.get("force") or "").lower() in {"1", "true", "yes"}
    channel_signature = telegram_channel_scope_signature()

    def fetch_pansou_rows():
        cached = get_cached_resource_source("pansou", title, media_type, year, tmdb_id_raw, channel_signature, force_refresh)
        if cached:
            cache_hits.append("pansou")
            return [row for row in (cached.get("rows") or []) if is_precise_resource_row(row, title)]
        rows = fetch_configured_telegram_resources(title)
        rows = [row for row in rows if is_precise_resource_row(row, title)]
        return set_cached_resource_source("pansou", title, media_type, rows, year, tmdb_id_raw, channel_signature)

    def fetch_hdhive_rows():
        nonlocal matched_tmdb
        cached = get_cached_resource_source("hdhive", title, media_type, year, tmdb_id_raw, "", force_refresh)
        if cached:
            cache_hits.append("hdhive")
            matched_tmdb = cached.get("matched_tmdb") or []
            return cached.get("rows") or []
        hdhive = load_hdhive_module()
        if tmdb_id_raw:
            matched_tmdb = [{"type": media_type, "id": int(tmdb_id_raw), "source": "card"}]
            api = hdhive.HDHiveAPI()
            rows = normalize_hdhive_resources(api.get_resources_via_openapi(media_type, int(tmdb_id_raw)))
            return set_cached_resource_source("hdhive", title, media_type, rows, year, tmdb_id_raw, matched_tmdb=matched_tmdb)
        candidates = search_tmdb_candidates(title, media_type, year)
        matched_tmdb = [{"type": item_type, "id": tmdb_id, "source": "auto"} for item_type, tmdb_id in candidates]
        if not candidates:
            set_cached_resource_source("hdhive", title, media_type, [], year, tmdb_id_raw, matched_tmdb=matched_tmdb)
            return []

        def fetch_candidate(candidate_type, candidate_id):
            api = hdhive.HDHiveAPI()
            return api.get_resources_via_openapi(candidate_type, candidate_id)

        rows = []
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
            futures = [pool.submit(fetch_candidate, candidate_type, candidate_id) for candidate_type, candidate_id in candidates]
            for future in as_completed(futures):
                rows.extend(normalize_hdhive_resources(future.result()))
        return set_cached_resource_source("hdhive", title, media_type, rows, year, tmdb_id_raw, matched_tmdb=matched_tmdb)

    pansou_rows = []
    hdhive_rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(fetch_pansou_rows): "频道搜索",
            pool.submit(fetch_hdhive_rows): "HDHiveAPI",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                if label == "频道搜索":
                    pansou_rows = future.result()
                else:
                    hdhive_rows = future.result()
            except Exception as exc:
                if label == "HDHiveAPI" and not matched_tmdb and "Network is unreachable" in str(exc):
                    errors.append(f"TMDB 自动匹配失败：{exc}")
                else:
                    errors.append(f"{label} 搜索失败：{exc}")

    if not matched_tmdb and not any("TMDB 自动匹配失败" in item for item in errors) and not tmdb_id_raw:
        errors.append("TMDB 自动匹配未找到对应条目，影巢无法按平台 ID 直接搜索")
    elif matched_tmdb and not hdhive_rows and not any("HDHiveAPI" in item for item in errors):
        ids = ", ".join(f"{item['type']}:{item['id']}" for item in matched_tmdb)
        errors.append(f"已自动匹配 TMDB：{ids}，但影巢未返回资源")

    rows = hdhive_rows + pansou_rows
    tmdb_lookup_id = int(tmdb_id_raw) if tmdb_id_raw.isdigit() else 0
    if not tmdb_lookup_id:
        for item in matched_tmdb:
            if item.get("type") == "tv" and str(item.get("id", "")).isdigit():
                tmdb_lookup_id = int(item["id"])
                break
    season_tabs = build_resource_season_status(rows, title, tmdb_lookup_id) if media_type == "tv" else []
    sources = [
        {"key": "all", "label": "全部", "count": len(rows)},
        {"key": "pansou", "label": "频道搜索", "count": len(pansou_rows)},
        {"key": "hdhive", "label": "HDHiveAPI", "count": len(hdhive_rows)},
    ]
    return {
        "success": True,
        "title": title,
        "media_type": media_type,
        "year": year,
        "tmdb_id": tmdb_id_raw,
        "matched_tmdb": matched_tmdb,
        "items": rows[:120],
        "sources": sources,
        "seasons": season_tabs,
        "errors": errors,
        "cache_hits": cache_hits,
        "generated_at": int(time.time()),
    }


def search_channel_resources(query):
    title = (query.get("title") or "").strip()
    media_type = "tv" if query.get("type") in ("tv", "电视剧") else "movie"
    year = extract_year(query.get("year") or "")
    tmdb_id_raw = (query.get("tmdb_id") or "").strip()
    if tmdb_id_raw and not tmdb_id_raw.isdigit():
        tmdb_id_raw = ""
    if not title:
        raise RuntimeError("缺少搜索标题")
    channel_signature = telegram_channel_scope_signature()
    force_refresh = str(query.get("refresh") or query.get("force") or "").lower() in {"1", "true", "yes"}
    cache_hits = []
    cached = get_cached_resource_source("pansou", title, media_type, year, tmdb_id_raw, channel_signature, force_refresh)
    if cached:
        cache_hits.append("pansou")
        rows = [row for row in (cached.get("rows") or []) if is_precise_resource_row(row, title)]
    else:
        rows = fetch_configured_telegram_resources(title)
        rows = [row for row in rows if is_precise_resource_row(row, title)]
        rows = set_cached_resource_source("pansou", title, media_type, rows, year, tmdb_id_raw, channel_signature)
    tmdb_lookup_id = int(tmdb_id_raw) if tmdb_id_raw.isdigit() else 0
    season_tabs = build_resource_season_status(rows, title, tmdb_lookup_id) if media_type == "tv" else []
    return {
        "success": True,
        "title": title,
        "media_type": media_type,
        "year": year,
        "tmdb_id": tmdb_id_raw,
        "matched_tmdb": [],
        "items": rows[:120],
        "sources": [
            {"key": "all", "label": "全部", "count": len(rows)},
            {"key": "pansou", "label": "频道搜索", "count": len(rows)},
        ],
        "seasons": season_tabs,
        "errors": [],
        "cache_hits": cache_hits,
        "generated_at": int(time.time()),
    }


def search_resources_legacy_disabled_2(query):
    title = (query.get("title") or "").strip()
    media_type = "tv" if query.get("type") in ("tv", "电视剧") else "movie"
    tmdb_id_raw = (query.get("tmdb_id") or "").strip()
    if not title:
        raise RuntimeError("缺少搜索标题")

    if tmdb_id_raw and not tmdb_id_raw.isdigit():
        tmdb_id_raw = ""

    def fetch_pansou_rows():
        rows = fetch_configured_telegram_resources(title)
        return [row for row in rows if is_precise_resource_row(row, title)]

    def fetch_hdhive_rows():
        hdhive = load_hdhive_module()
        if tmdb_id_raw:
            api = hdhive.HDHiveAPI()
            return normalize_hdhive_resources(api.get_resources_via_openapi(media_type, int(tmdb_id_raw)))
        candidates = search_tmdb_candidates(title, media_type)
        rows = []
        if not candidates:
            return rows
        def fetch_candidate(candidate_type, candidate_id):
            api = hdhive.HDHiveAPI()
            return api.get_resources_via_openapi(candidate_type, candidate_id)
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
            futures = [pool.submit(fetch_candidate, candidate_type, candidate_id) for candidate_type, candidate_id in candidates]
            for future in as_completed(futures):
                rows.extend(normalize_hdhive_resources(future.result()))
        return rows

    errors = []
    pansou_rows = []
    hdhive_rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(fetch_pansou_rows): "频道搜索",
            pool.submit(fetch_hdhive_rows): "HDHiveAPI",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                if label == "频道搜索":
                    pansou_rows = future.result()
                else:
                    hdhive_rows = future.result()
            except Exception as exc:
                errors.append(f"{label} 搜索失败：{exc}")

    if not hdhive_rows and not any("HDHiveAPI" in item for item in errors):
        errors.append("HDHiveAPI 未返回该片资源，可能暂未收录或 TMDB 匹配不到对应条目")

    rows = hdhive_rows + pansou_rows
    seasons = {}
    for row in rows:
        season = row.get("season") or ""
        for ep in row.get("episodes") or []:
            seasons.setdefault(season or "1", set()).add(ep)
    season_tabs = [
        {"season": season, "episodes": sorted(list(eps))}
        for season, eps in sorted(seasons.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 999)
    ]
    sources = [
        {"key": "all", "label": "全部", "count": len(rows)},
        {"key": "pansou", "label": "频道搜索", "count": len(pansou_rows)},
        {"key": "hdhive", "label": "HDHiveAPI", "count": len(hdhive_rows)},
    ]
    return {
        "success": True,
        "title": title,
        "media_type": media_type,
        "tmdb_id": tmdb_id_raw,
        "items": rows[:120],
        "sources": sources,
        "seasons": season_tabs,
        "errors": errors,
        "generated_at": int(time.time()),
    }
