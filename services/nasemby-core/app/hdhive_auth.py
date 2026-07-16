from __future__ import annotations

import importlib.machinery
import json
import os
import random
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from app.config import DATA_DIR, read_config, write_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HDHIVE_DIR = PROJECT_ROOT / "app" / "hdhive"
INSTALL_TEMPLATE = HDHIVE_DIR / "db" / "hdhive" / ".hdhive_install.json"
INSTALL_TARGET = PROJECT_ROOT / "db" / "hdhive" / ".hdhive_install.json"
STATE_PATH = DATA_DIR / "hdhive_state.json"

HDHIVE_CONFIG_KEYS = [
    "ENV_HDHIVE_CHECKIN_ENABLED",
    "ENV_HDHIVE_CHECKIN_GAMBLER",
    "ENV_HDHIVE_CHECKIN_NOTIFY",
    "ENV_HDHIVE_UNLOCK_POINTS_LIMIT",
    "ENV_HDHIVE_UNLOCK_RATE_LIMIT",
    "ENV_HDHIVE_EXPIRY_REMINDER",
    "ENV_HDHIVE_REMINDER_INTERVAL_HOURS",
]

EXPIRY_REMINDER_SECONDS = 3 * 24 * 60 * 60


def ensure_hdhive_install_file() -> None:
    if INSTALL_TARGET.exists() or not INSTALL_TEMPLATE.exists():
        return
    INSTALL_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(INSTALL_TEMPLATE, INSTALL_TARGET)


def _load_user_client():
    ensure_hdhive_install_file()
    path = HDHIVE_DIR / "hdhive_user_client.pyc"
    if not path.exists():
        raise RuntimeError("缺少影巢授权模块 hdhive_user_client.pyc")
    if str(HDHIVE_DIR) not in sys.path:
        sys.path.insert(0, str(HDHIVE_DIR))
    return importlib.machinery.SourcelessFileLoader("hdhive_user_client", str(path)).load_module()


def _read_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_state(state: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def _format_local(ts: float | int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts)).strftime("%Y/%m/%d %H:%M:%S")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _int_config(cfg: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(cfg.get(key, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _telegram_notify(message: str) -> bool:
    cfg = read_config()
    token = str(cfg.get("ENV_TG_BOT_TOKEN") or "").strip()
    chat_id = str(cfg.get("ENV_TG_ADMIN_USER_ID") or "").strip()
    if not token or not chat_id or chat_id == "0":
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
        return response.ok
    except Exception:
        return False


def _status_user(status: dict[str, Any]) -> dict[str, Any]:
    for value in (
        status.get("user"),
        status.get("account"),
        (status.get("data") or {}).get("user") if isinstance(status.get("data"), dict) else None,
    ):
        if isinstance(value, dict):
            return value
    return {}


def _account_summary(status: dict[str, Any], identity: dict[str, Any] | None = None) -> dict[str, Any]:
    identity = identity or {}
    state = _read_state()
    user = _status_user(status)
    custom_name = str(state.get("account_display_name") or "").strip()
    api_name = str(
        user.get("nickname")
        or user.get("username")
        or user.get("name")
        or status.get("nickname")
        or status.get("username")
        or ""
    ).strip()
    name = custom_name or api_name
    user_hash = str(status.get("hdhive_user_hash") or status.get("install_hash") or identity.get("install_hash") or "").strip()
    return {
        "display_name": name or "影巢已授权账号",
        "display_source": "local" if custom_name else ("api" if api_name else "token"),
        "hash": user_hash,
        "short_hash": user_hash[:8] if user_hash else "",
    }


def _checkin_message(result: Any) -> str:
    if isinstance(result, dict):
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        return str(data.get("message") or result.get("meta") or result.get("message") or result.get("description") or result.get("code") or result)
    return str(result)


def _checkin_record(result: Any, ok: bool, error: str = "") -> dict[str, Any]:
    data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
    points = data.get("points")
    try:
        points = int(points)
    except (TypeError, ValueError):
        points = 0
    checked_in = data.get("checked_in")
    return {
        "time": _format_local(time.time()),
        "ok": bool(ok),
        "checked_in": checked_in if isinstance(checked_in, bool) else bool(ok),
        "points": points,
        "message": error or _checkin_message(result),
    }


def update_hdhive_account(payload: dict[str, object]) -> dict[str, Any]:
    state = _read_state()
    display_name = str(payload.get("display_name") or "").strip()
    if display_name:
        state["account_display_name"] = display_name[:80]
    else:
        state.pop("account_display_name", None)
    _write_state(state)
    return hdhive_status()


def _schedule_next_checkin(state: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    state = dict(state or _read_state())
    now = time.time()
    if force or float(state.get("next_checkin_ts") or 0) <= now:
        next_ts = now + random.randint(30 * 60, 24 * 60 * 60)
        state["next_checkin_ts"] = int(next_ts)
        state["next_checkin_at"] = _format_local(next_ts)
        _write_state(state)
    return state


def hdhive_config() -> dict[str, str]:
    cfg = read_config()
    return {key: cfg.get(key, "") for key in HDHIVE_CONFIG_KEYS}


def update_hdhive_config(payload: dict[str, object]) -> dict[str, str]:
    updated = write_config({key: payload[key] for key in HDHIVE_CONFIG_KEYS if key in payload})
    return {key: updated.get(key, "") for key in HDHIVE_CONFIG_KEYS}


def _module_error(exc: Exception) -> str:
    if exc.__class__.__name__ == "ImportError" and "bad magic number" in str(exc):
        return "影巢授权模块是 Python 3.13 编译模块，请用 Docker 镜像运行本项目后再授权。"
    return str(exc)


def hdhive_identity() -> dict[str, Any]:
    try:
        mod = _load_user_client()
        data = mod.identity()
        data["ok"] = True
        data["install_file"] = str(INSTALL_TARGET)
        return data
    except Exception as exc:
        return {"ok": False, "error": _module_error(exc)}


def hdhive_auth_url() -> str:
    try:
        mod = _load_user_client()
        return mod.build_auth_url()
    except Exception as exc:
        raise RuntimeError(_module_error(exc)) from exc


def hdhive_status() -> dict[str, Any]:
    try:
        mod = _load_user_client()
        client = mod.HDHiveUserAPIClient()
        result = client.token_status()
        cfg = hdhive_config()
        state = _read_state()
        if cfg.get("ENV_HDHIVE_CHECKIN_ENABLED") in ("1", "true", "yes", "on"):
            state = _schedule_next_checkin(state)
        identity = hdhive_identity()
        return {
            "ok": True,
            "status": result,
            "identity": identity,
            "account": _account_summary(result if isinstance(result, dict) else {}, identity),
            "config": cfg,
            "checkin_state": state,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": _module_error(exc),
            "identity": hdhive_identity(),
            "config": hdhive_config(),
            "checkin_state": _read_state(),
        }


def hdhive_checkin_now(is_gambler: bool | None = None) -> dict[str, Any]:
    cfg = hdhive_config()
    if is_gambler is None:
        is_gambler = _truthy(cfg.get("ENV_HDHIVE_CHECKIN_GAMBLER"))
    try:
        mod = _load_user_client()
        client = mod.HDHiveUserAPIClient()
        result = client.checkin(is_gambler=is_gambler)
        state = _read_state()
        state["last_checkin_ts"] = int(time.time())
        state["last_checkin_at"] = _format_local(state["last_checkin_ts"])
        state["last_checkin_result"] = result
        history = list(state.get("checkin_history") or [])
        history.insert(0, _checkin_record(result, True))
        state["checkin_history"] = history[:30]
        state = _schedule_next_checkin(state, force=True)
        if _truthy(cfg.get("ENV_HDHIVE_CHECKIN_NOTIFY")):
            _telegram_notify(f"影巢签到已执行：{_checkin_message(result)}")
        return {"ok": True, "result": result, "checkin_state": state}
    except Exception as exc:
        state = _read_state()
        state["last_checkin_ts"] = int(time.time())
        state["last_checkin_at"] = _format_local(state["last_checkin_ts"])
        state["last_checkin_error"] = _module_error(exc)
        history = list(state.get("checkin_history") or [])
        history.insert(0, _checkin_record({}, False, _module_error(exc)))
        state["checkin_history"] = history[:30]
        _write_state(state)
        if _truthy(cfg.get("ENV_HDHIVE_CHECKIN_NOTIFY")):
            _telegram_notify(f"影巢签到失败：{_module_error(exc)}")
        return {"ok": False, "error": _module_error(exc), "checkin_state": state}


def run_due_hdhive_checkin() -> dict[str, Any] | None:
    cfg = hdhive_config()
    state = _read_state()
    _maybe_send_expiry_reminder(cfg, state)
    if not _truthy(cfg.get("ENV_HDHIVE_CHECKIN_ENABLED")):
        return None
    next_ts = float(state.get("next_checkin_ts") or 0)
    if not next_ts:
        _schedule_next_checkin(state, force=True)
        return None
    if next_ts <= time.time():
        return hdhive_checkin_now()
    return None


def _maybe_send_expiry_reminder(cfg: dict[str, str], state: dict[str, Any]) -> None:
    if not _truthy(cfg.get("ENV_HDHIVE_EXPIRY_REMINDER")):
        return
    try:
        mod = _load_user_client()
        client = mod.HDHiveUserAPIClient()
        status = client.token_status()
    except Exception:
        return
    if not isinstance(status, dict) or status.get("auth_required") is True:
        return
    try:
        expires_in = int(status.get("expires_in_seconds") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in <= 0 or expires_in > EXPIRY_REMINDER_SECONDS:
        return
    interval = max(1, _int_config(cfg, "ENV_HDHIVE_REMINDER_INTERVAL_HOURS", 6)) * 60 * 60
    now = int(time.time())
    if now - int(state.get("last_expiry_reminder_ts") or 0) < interval:
        return
    account = _account_summary(status, hdhive_identity())
    expires_at = _format_local(status.get("expires_at"))
    hours = max(1, round(expires_in / 3600))
    if _telegram_notify(f"{account['display_name']}授权将在 {expires_at} 到期，剩余约 {hours} 小时，请及时重新授权。"):
        state["last_expiry_reminder_ts"] = now
        state["last_expiry_reminder_at"] = _format_local(now)
        _write_state(state)


def hdhive_status_text() -> str:
    data = hdhive_status()
    return json.dumps(data, ensure_ascii=False, indent=2)
