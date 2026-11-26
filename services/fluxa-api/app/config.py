from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT_DIR.parent.parent if (ROOT_DIR.parent.parent / "package.json").is_file() else ROOT_DIR

if (REPO_ROOT / ".env").is_file():
    load_dotenv(REPO_ROOT / ".env", override=False)


def resolve_data_dirs() -> tuple[Path, Path]:
    data_root = os.environ.get("FLUXA_DATA_ROOT") or os.environ.get("MCC_DATA_ROOT")
    if data_root:
        base = Path(data_root)
        return base / "data", base / "db"
    if Path("/app/db").is_dir() or os.environ.get("MCC_ENV") == "production":
        return Path("/app/data"), Path("/app/db")
    runtime = REPO_ROOT / "runtime"
    return runtime / "data", runtime / "db"


# Initial import from MoviePilot Neo rules (ordered by priority).
# Note: represents initial user rule snapshot, editable in Settings.
DEFAULT_SITE_OFFICIAL_GROUPS: list[dict[str, Any]] = [
    {
        "priority": 1,
        "siteId": "audiences",
        "siteName": "观众",
        "matchMode": "group_list",
        "domains": ["audiences.me"],
        "officialGroups": ["Audies", "ADWeb", "ADE"],
    },
    {
        "priority": 2,
        "siteId": "hhanclub",
        "siteName": "憨憨",
        "matchMode": "group_list",
        "domains": ["hhanclub.top"],
        "officialGroups": ["HHWEB"],
    },
    {
        "priority": 3,
        "siteId": "ubits",
        "siteName": "UBits",
        "matchMode": "group_list",
        "domains": ["ubits.club"],
        "officialGroups": ["UBits", "UBWEB", "UBTV"],
    },
    {
        "priority": 4,
        "siteId": "hdsky",
        "siteName": "天空",
        "matchMode": "group_list",
        "domains": ["hdsky.me"],
        "officialGroups": ["HDS", "HDSky", "HDSTV", "HDSPad", "HDSWEB"],
    },
    {
        "priority": 5,
        "siteId": "chdbits",
        "siteName": "彩虹岛",
        "matchMode": "group_list",
        "domains": ["ptchdbits.co", "chdbits.co"],
        "officialGroups": ["CHD", "CHDWEB", "CHDBits", "CHDTV", "CHDPAD"],
    },
    {
        "priority": 6,
        "siteId": "springsunday",
        "siteName": "春天",
        "matchMode": "group_list",
        "domains": ["springsunday.net"],
        "officialGroups": ["CMCT", "CMCTV"],
    },
    {
        "priority": 7,
        "siteId": "ourbits",
        "siteName": "我堡",
        "matchMode": "group_list",
        "domains": ["ourbits.club"],
        "officialGroups": ["OurBits", "OurTV", "FLTTH", "Ao", "PbK", "MGs", "iLoveHD", "iLoveTV"],
    },
    {
        "priority": 8,
        "siteId": "mteam",
        "siteName": "馒头",
        "matchMode": "group_list",
        "domains": ["kp.m-team.cc", "api.m-team.cc", "m-team.cc"],
        "officialGroups": ["MTeam", "MTeamTV", "MWeb", "MPAD"],
    },
    {
        "priority": 9,
        "siteId": "qingwa",
        "siteName": "青蛙",
        "matchMode": "group_list",
        "domains": ["qingwapt.com", "new.qingwa.pro"],
        "officialGroups": ["FROG", "FROGWeb", "FROGE"],
    },
    {
        "priority": 10,
        "siteId": "frds",
        "siteName": "朋友",
        "matchMode": "group_list",
        "domains": ["pt.keepfrds.com"],
        "officialGroups": ["FRDS", "Yumi", "cXcY"],
    },
    {
        "priority": 11,
        "siteId": "pter",
        "siteName": "猫站",
        "matchMode": "group_list",
        "domains": ["pterclub.com"],
        "officialGroups": ["PTer", "PTerDIY", "PTerWEB", "PTerTV", "PTerMV"],
    },
    {
        "priority": 12,
        "siteId": "zmpt",
        "siteName": "织梦",
        "matchMode": "group_list",
        "domains": ["zmpt.cc"],
        "officialGroups": ["ZmWeb"],
    },
    {
        "priority": 13,
        "siteId": "pthome",
        "siteName": "家园",
        "matchMode": "group_list",
        "domains": ["pthome.net"],
        "officialGroups": ["PTH", "PTHome", "PTHWEB", "PTHtv"],
    },
    # Sites currently judged by site-level "official" tag (no fixed release-group list)
    {"priority": 14, "siteId": "hdvideo", "siteName": "高清视界", "matchMode": "official_tag", "domains": [], "officialGroups": []},
    {"priority": 15, "siteId": "crabpt", "siteName": "蟹黄堡", "matchMode": "official_tag", "domains": [], "officialGroups": []},
    {"priority": 16, "siteId": "open", "siteName": "皇后", "matchMode": "official_tag", "domains": [], "officialGroups": []},
    {"priority": 17, "siteId": "hdkyl", "siteName": "麒麟", "matchMode": "official_tag", "domains": [], "officialGroups": []},
    {"priority": 18, "siteId": "agsvpt", "siteName": "AGSVPT", "matchMode": "official_tag", "domains": ["agsvpt.com"], "officialGroups": []},
    {"priority": 19, "siteId": "hddolby", "siteName": "高清杜比", "matchMode": "official_tag", "domains": ["hddolby.com"], "officialGroups": []},
    {"priority": 20, "siteId": "sunny", "siteName": "Sunny", "matchMode": "official_tag", "domains": [], "officialGroups": []},
    {"priority": 21, "siteId": "cola", "siteName": "Cola", "matchMode": "official_tag", "domains": [], "officialGroups": []},
]

SECRET_FIELDS: dict[str, set[str]] = {
    "moviepilot": {"apiToken"},
    "torra": {"token", "password"},
    "qbittorrent": {"password"},
    "symedia": {"token", "password"},
    "emby": {"apiKey", "password"},
    "tmdb": {"apiKey", "bearerToken"},
    "telegram": {"botToken"},
}


def default_settings() -> dict[str, Any]:
    return {
        "services": {
            "moviepilot": {
                "baseUrl": (os.environ.get("MOVIEPILOT_BASE_URL") or os.environ.get("ENV_MOVIEPILOT_URL") or "").strip().rstrip("/"),
                "webUrl": (os.environ.get("MOVIEPILOT_WEB_URL") or "").strip().rstrip("/"),
                "apiToken": (os.environ.get("MOVIEPILOT_API_TOKEN") or os.environ.get("ENV_MOVIEPILOT_API_TOKEN") or "").strip(),
            },
            "torra": {
                "baseUrl": (os.environ.get("TORRA_BASE_URL") or "").strip().rstrip("/"),
                "webUrl": (os.environ.get("TORRA_WEB_URL") or "").strip().rstrip("/"),
                "token": (os.environ.get("TORRA_TOKEN") or "").strip(),
                "username": (os.environ.get("TORRA_USERNAME") or "").strip(),
                "password": (os.environ.get("TORRA_PASSWORD") or "").strip(),
            },
            "qbittorrent": {
                "baseUrl": (os.environ.get("QB_BASE_URL") or "").strip().rstrip("/"),
                "webUrl": (os.environ.get("QB_WEB_URL") or "").strip().rstrip("/"),
                "username": (os.environ.get("QB_USERNAME") or "").strip(),
                "password": (os.environ.get("QB_PASSWORD") or "").strip(),
                "protectSeeding": True,
            },
            "symedia": {
                "baseUrl": (os.environ.get("SYMEDIA_BASE_URL") or "").strip().rstrip("/"),
                "webUrl": (os.environ.get("SYMEDIA_WEB_URL") or "").strip().rstrip("/"),
                "token": (os.environ.get("SYMEDIA_TOKEN") or "").strip(),
                "username": (os.environ.get("SYMEDIA_USERNAME") or "").strip(),
                "password": (os.environ.get("SYMEDIA_PASSWORD") or "").strip(),
            },
            "emby": {
                "baseUrl": (os.environ.get("EMBY_BASE_URL") or "").strip().rstrip("/"),
                "webUrl": (os.environ.get("EMBY_WEB_URL") or "").strip().rstrip("/"),
                "apiKey": (os.environ.get("EMBY_API_KEY") or "").strip(),
                "userId": (os.environ.get("EMBY_USER_ID") or "").strip(),
                "username": (os.environ.get("EMBY_USERNAME") or "").strip(),
                "password": (os.environ.get("EMBY_PASSWORD") or "").strip(),
            },
            "tmdb": {
                "apiKey": (os.environ.get("TMDB_API_KEY") or "").strip(),
                "bearerToken": (os.environ.get("TMDB_API_TOKEN") or "").strip(),
            },
            "telegram": {
                "enabled": False,
                "botToken": (os.environ.get("TG_BOT_TOKEN") or os.environ.get("ENV_TG_BOT_TOKEN") or "").strip(),
                "chatId": (os.environ.get("TG_CHAT_ID") or os.environ.get("ENV_TG_ADMIN_USER_ID") or "").strip(),
                "fluxaPublicUrl": (os.environ.get("FLUXA_PUBLIC_URL") or "").strip().rstrip("/"),
                "notifyOnFailure": True,
            },
        },
        "guardrails": {
            "newReleaseMaxAgeDays": 365,
            "newSeasonMaxAgeDays": 180,
            "blockOldMoviesFromCharts": True,
            "blockFinishedOldSeriesFromCharts": True,
            "onlySubscribeLatestSeasonForReturning": True,
            "requireSiteOfficialGroup": True,
            "blockCrossSiteRepost": True,
            "noFallbackToNonOfficial": True,
            "defaultSubscribeTarget": "moviepilot",
            "defaultIntent": "follow",
            "siteOfficialGroups": DEFAULT_SITE_OFFICIAL_GROUPS,
        },
        "safety": {
            "requireConfirmForSeedingActions": True,
            "requireConfirmForRewashOrSwitch": True,
            "requireConfirmForDelete": True,
        },
    }


def merge_settings(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = _deep_copy_json(base)
    if not isinstance(patch, dict):
        return result

    if isinstance(patch.get("services"), dict):
        for svc_name, svc_patch in patch["services"].items():
            if svc_name not in result["services"] or not isinstance(svc_patch, dict):
                continue
            target = result["services"][svc_name]
            secret_keys = SECRET_FIELDS.get(svc_name, set())
            clear_keys = set(svc_patch.get("_clear") or []) if isinstance(svc_patch.get("_clear"), list) else set()
            for key, value in svc_patch.items():
                if key == "_clear" or key not in target:
                    continue
                if key in secret_keys:
                    if key in clear_keys:
                        target[key] = ""
                    elif isinstance(value, str) and value.strip() and value.strip() != "••••••••":
                        target[key] = value.strip()
                    continue
                if isinstance(target[key], bool):
                    target[key] = bool(value)
                elif isinstance(value, str):
                    cleaned = value.strip()
                    if key in {"baseUrl", "webUrl", "fluxaPublicUrl"}:
                        cleaned = cleaned.rstrip("/")
                    target[key] = cleaned
                else:
                    target[key] = value

    if isinstance(patch.get("guardrails"), dict):
        g_target = result["guardrails"]
        for key, value in patch["guardrails"].items():
            if key not in g_target:
                continue
            if key == "siteOfficialGroups" and isinstance(value, list):
                cleaned_sites = []
                for idx, row in enumerate(value, start=1):
                    if not isinstance(row, dict):
                        continue
                    site_name = str(row.get("siteName") or "").strip()
                    site_id = str(row.get("siteId") or site_name.lower().replace(" ", "_")).strip()
                    if not site_name or not site_id:
                        continue
                    domains = [str(d).strip().lower() for d in (row.get("domains") or []) if str(d).strip()]
                    groups = [str(g).strip() for g in (row.get("officialGroups") or []) if str(g).strip()]
                    raw_mode = str(row.get("matchMode") or "").strip()
                    match_mode = raw_mode if raw_mode in {"group_list", "official_tag"} else ("group_list" if groups else "official_tag")
                    try:
                        priority = max(1, int(row.get("priority") or idx))
                    except (TypeError, ValueError):
                        priority = idx
                    cleaned_sites.append({
                        "priority": priority,
                        "siteId": site_id,
                        "siteName": site_name,
                        "matchMode": match_mode,
                        "domains": domains,
                        "officialGroups": groups,
                    })
                cleaned_sites.sort(key=lambda r: (r["priority"], r["siteName"]))
                g_target[key] = cleaned_sites
            elif isinstance(g_target[key], bool):
                g_target[key] = bool(value)
            elif isinstance(g_target[key], int):
                try:
                    g_target[key] = max(1, min(3650, int(value)))
                except (TypeError, ValueError):
                    pass
            elif isinstance(value, str):
                g_target[key] = value.strip()

    if isinstance(patch.get("safety"), dict):
        s_target = result["safety"]
        for key, value in patch["safety"].items():
            if key in s_target:
                s_target[key] = bool(value)

    return result


def redact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    view = _deep_copy_json(settings)
    for svc_name, svc_cfg in view.get("services", {}).items():
        secret_keys = SECRET_FIELDS.get(svc_name, set())
        configured_secrets: dict[str, bool] = {}
        for key in secret_keys:
            has_val = bool(svc_cfg.get(key))
            configured_secrets[key] = has_val
            svc_cfg[key] = ""
        svc_cfg["_hasSecrets"] = configured_secrets
    return view


def _deep_copy_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _deep_copy_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy_json(v) for v in obj]
    return obj
