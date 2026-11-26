from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT_DIR.parent.parent if (ROOT_DIR.parent.parent / "package.json").is_file() else ROOT_DIR

# Load optional workspace .env only for initial defaults
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


DEFAULT_SITE_OFFICIAL_GROUPS: list[dict[str, Any]] = [
    {
        "siteId": "mteam",
        "siteName": "M-Team",
        "domains": ["kp.m-team.cc", "api.m-team.cc", "m-team.cc"],
        "officialGroups": ["MTeam", "MTeamTV", "MPAD", "MWeb", "OneHD"],
    },
    {
        "siteId": "hdhome",
        "siteName": "HDHome",
        "domains": ["hdhome.org"],
        "officialGroups": ["HDHome", "HDH", "HDHTV", "HDHPad", "HDHWEB"],
    },
    {
        "siteId": "zmpt",
        "siteName": "织梦",
        "domains": ["zmpt.cc"],
        "officialGroups": ["ZmWeb", "ZMPT", "ZmTeam"],
    },
    {
        "siteId": "qingwa",
        "siteName": "青蛙",
        "domains": ["qingwapt.com", "new.qingwa.pro"],
        "officialGroups": ["FROG", "FROGTV", "FROGWeb", "QingWa"],
    },
    {
        "siteId": "chdbits",
        "siteName": "CHDBits",
        "domains": ["ptchdbits.co", "chdbits.co"],
        "officialGroups": ["CHD", "CHDWEB", "CHDPAD", "CHDHKTV"],
    },
    {
        "siteId": "hdsky",
        "siteName": "HDSky",
        "domains": ["hdsky.me"],
        "officialGroups": ["HDSky", "HDS", "HDSWEB", "HDSPad", "HDSTV"],
    },
    {
        "siteId": "ourbits",
        "siteName": "OurBits",
        "domains": ["ourbits.club"],
        "officialGroups": ["OurBits", "OurTV", "OurPad", "PbK", "Ao", "MGs", "iLoveTV", "iLoveHD"],
    },
    {
        "siteId": "audiences",
        "siteName": "观众",
        "domains": ["audiences.me"],
        "officialGroups": ["Audies", "ADE", "ADWeb", "ADAudio", "ADeBook"],
    },
    {
        "siteId": "frds",
        "siteName": "FRDS",
        "domains": ["pt.keepfrds.com"],
        "officialGroups": ["FRDS", "Yumi"],
    },
]

SECRET_FIELDS: dict[str, set[str]] = {
    "moviepilot": {"apiToken", "password"},
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
                "username": (os.environ.get("MOVIEPILOT_USERNAME") or os.environ.get("ENV_MOVIEPILOT_USERNAME") or "").strip(),
                "password": (os.environ.get("MOVIEPILOT_PASSWORD") or "").strip(),
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
            "requireSiteOfficialGroup": False,
            "blockCrossSiteRepost": True,
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
                for row in value:
                    if not isinstance(row, dict):
                        continue
                    site_name = str(row.get("siteName") or "").strip()
                    site_id = str(row.get("siteId") or site_name.lower().replace(" ", "_")).strip()
                    if not site_name or not site_id:
                        continue
                    domains = [str(d).strip().lower() for d in (row.get("domains") or []) if str(d).strip()]
                    groups = [str(g).strip() for g in (row.get("officialGroups") or []) if str(g).strip()]
                    cleaned_sites.append({
                        "siteId": site_id,
                        "siteName": site_name,
                        "domains": domains,
                        "officialGroups": groups,
                    })
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
