from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

from flask import Flask, jsonify, request

from app.config import read_config
from app.hdhive_auth import (
    hdhive_auth_url,
    hdhive_checkin_now,
    hdhive_status,
    update_hdhive_config,
)
from app.services import check_115_account, moviepilot_status
from app.telegram_runtime import (
    list_channels as telegram_list_channels,
    logout as telegram_logout,
    save_channels as telegram_save_channels,
    send_login_code as telegram_send_login_code,
    sign_in as telegram_sign_in,
    telegram_status,
)


logger = logging.getLogger(__name__)
_action_lock = threading.Lock()
_last_actions: dict[str, float] = {}


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _configured(value) -> bool:
    return bool(str(value or "").strip())


def _error(code: str, message: str, status: int):
    return jsonify({"ok": False, "code": code, "error": message}), status


def _enabled(environment, name: str) -> bool:
    return _truthy(environment.get(name, "false"))


def _cooldown(action: str, seconds: int) -> int:
    now = time.monotonic()
    with _action_lock:
        remaining = seconds - int(now - _last_actions.get(action, 0.0))
        if remaining > 0:
            return remaining
        _last_actions[action] = now
    return 0


def _service_summary(config: dict, channels_reader=telegram_list_channels) -> list[dict]:
    channels = []
    try:
        channel_payload = channels_reader()
        channels = channel_payload.get("channels") if isinstance(channel_payload, dict) else []
    except Exception:
        channels = []
    return [
        {
            "id": "cloud115",
            "name": "115",
            "role": "网盘目标与单条转存",
            "configured": _configured(config.get("ENV_115_COOKIES")),
            "connected": None,
            "detail": "目标目录已设置" if str(config.get("ENV_UPLOAD_PID") or "0") != "0" else "等待设置目标目录",
        },
        {
            "id": "telegram",
            "name": "Telegram",
            "role": "资源频道与通知",
            "configured": _configured(config.get("ENV_TG_API_ID")) and _configured(config.get("ENV_TG_API_HASH")),
            "connected": None,
            "detail": f"{len(channels or [])} 个频道",
        },
        {
            "id": "hdhive",
            "name": "HDHive / pansou",
            "role": "网盘候选搜索",
            "configured": _truthy(config.get("ENV_HDHIVE_CHECKIN_ENABLED")),
            "connected": None,
            "detail": "授权状态等待检查",
        },
        {
            "id": "moviepilot",
            "name": "MoviePilot",
            "role": "PT 兼容通道",
            "configured": _configured(config.get("ENV_MOVIEPILOT_URL")) and _configured(config.get("ENV_MOVIEPILOT_API_TOKEN")),
            "connected": None,
            "detail": "兼容能力",
        },
    ]


def _probe_service(service: dict, probes: dict[str, Callable]) -> dict:
    if not service["configured"]:
        return service
    try:
        if service["id"] == "cloud115":
            result = probes["cloud115"]()
            user = result.get("user") if isinstance(result, dict) and isinstance(result.get("user"), dict) else {}
            label = str(user.get("nick_name") or user.get("nickname") or user.get("user_name") or "").strip()
            return {**service, "connected": bool(result.get("ok")), "detail": label or "账号检查通过"}
        if service["id"] == "telegram":
            result = probes["telegram"]()
            channels = result.get("channels") if isinstance(result, dict) and isinstance(result.get("channels"), list) else []
            return {
                **service,
                "connected": bool(result.get("authorized")),
                "detail": f"已登录 · {len(channels)} 个频道" if result.get("authorized") else "未登录",
            }
        if service["id"] == "hdhive":
            result = probes["hdhive"]()
            account = result.get("account") if isinstance(result, dict) and isinstance(result.get("account"), dict) else {}
            label = str(account.get("display_name") or account.get("name") or "").strip()
            return {**service, "connected": bool(result.get("ok")), "detail": label or ("授权有效" if result.get("ok") else "授权不可用")}
        if service["id"] == "moviepilot":
            result = probes["moviepilot"]()
            return {**service, "connected": bool(result.get("ok")), "detail": "连接正常" if result.get("ok") else "连接失败"}
    except Exception:
        return {**service, "connected": False, "detail": "检查失败"}
    return service


def register_integrations(
    app: Flask,
    environment=None,
    config_reader=None,
    functions=None,
):
    environment = os.environ if environment is None else environment
    config_reader = config_reader or read_config
    functions = functions or {}
    probes = {
        "cloud115": functions.get("check_115_account", check_115_account),
        "telegram": functions.get("telegram_status", telegram_status),
        "hdhive": functions.get("hdhive_status", hdhive_status),
        "moviepilot": functions.get("moviepilot_status", moviepilot_status),
    }
    send_code = functions.get("telegram_send_login_code", telegram_send_login_code)
    sign_in = functions.get("telegram_sign_in", telegram_sign_in)
    logout = functions.get("telegram_logout", telegram_logout)
    list_channels = functions.get("telegram_list_channels", telegram_list_channels)
    save_channels = functions.get("telegram_save_channels", telegram_save_channels)
    get_hdhive_auth_url = functions.get("hdhive_auth_url", hdhive_auth_url)
    save_hdhive_config = functions.get("update_hdhive_config", update_hdhive_config)
    run_hdhive_checkin = functions.get("hdhive_checkin_now", hdhive_checkin_now)

    @app.get("/api/v2/integrations", endpoint="mcc_v2_integrations_summary")
    def integrations_summary():
        config = config_reader()
        services = _service_summary(config, list_channels)
        if request.args.get("probe") == "1":
            if not _enabled(environment, "MCC_INTEGRATION_PROBE_ENABLED"):
                return _error("INTEGRATION_PROBE_DISABLED", "连接检查开关未启用", 403)
            services = [_probe_service(service, probes) for service in services]
        return jsonify({
            "ok": True,
            "services": services,
            "managementEnabled": _enabled(environment, "MCC_INTEGRATION_MANAGEMENT_ENABLED"),
            "probeEnabled": _enabled(environment, "MCC_INTEGRATION_PROBE_ENABLED"),
            "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    @app.post("/api/v2/integrations/cloud115/probes", endpoint="mcc_v2_cloud115_check")
    def cloud115_check():
        if not _enabled(environment, "MCC_INTEGRATION_PROBE_ENABLED"):
            return _error("INTEGRATION_PROBE_DISABLED", "115 账号检查开关未启用", 403)
        remaining = _cooldown("cloud115-check", 30)
        if remaining:
            return _error("INTEGRATION_COOLDOWN", f"请在 {remaining} 秒后重试", 409)
        try:
            service = _probe_service(_service_summary(config_reader(), list_channels)[0], probes)
            return jsonify({"ok": bool(service.get("connected")), "service": service})
        except Exception:
            logger.exception("115 integration check failed")
            return _error("CLOUD115_CHECK_FAILED", "115 账号检查失败", 502)

    @app.get("/api/v2/integrations/telegram/channels", endpoint="mcc_v2_telegram_channels")
    def telegram_channels():
        try:
            payload = list_channels()
            channels = payload.get("channels") if isinstance(payload, dict) and isinstance(payload.get("channels"), list) else []
            safe_channels = [
                {
                    "name": str(item.get("name") or item.get("title") or item.get("username") or "频道")[:100],
                    "input": str(item.get("input") or item.get("username") or item.get("id") or "")[:160],
                    "enabled": item.get("enabled") is not False,
                }
                for item in channels[:100]
                if isinstance(item, dict)
            ]
            return jsonify({"ok": True, "channels": safe_channels})
        except Exception:
            logger.exception("telegram channel read failed")
            return _error("TELEGRAM_CHANNELS_FAILED", "Telegram 频道读取失败", 502)

    def telegram_write_guard():
        if not _enabled(environment, "MCC_INTEGRATION_MANAGEMENT_ENABLED") or not _enabled(environment, "MCC_TELEGRAM_MANAGEMENT_ENABLED"):
            return _error("TELEGRAM_MANAGEMENT_DISABLED", "Telegram 管理开关未启用", 403)
        return None

    @app.post("/api/v2/integrations/telegram/login-codes", endpoint="mcc_v2_telegram_send_code")
    def telegram_send_code():
        denied = telegram_write_guard()
        if denied:
            return denied
        remaining = _cooldown("telegram-send-code", 60)
        if remaining:
            return _error("INTEGRATION_COOLDOWN", f"请在 {remaining} 秒后重试", 409)
        payload = request.get_json(silent=True) or {}
        allowed = {key: payload.get(key) for key in ("phone", "api_id", "api_hash") if key in payload}
        try:
            return jsonify(send_code(allowed))
        except ValueError as exc:
            return _error("TELEGRAM_INPUT_INVALID", str(exc), 400)
        except Exception:
            logger.exception("telegram send code failed")
            return _error("TELEGRAM_SEND_CODE_FAILED", "Telegram 验证码发送失败", 502)

    @app.post("/api/v2/integrations/telegram/session", endpoint="mcc_v2_telegram_sign_in")
    def telegram_session_create():
        denied = telegram_write_guard()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        try:
            result = sign_in({"code": str(payload.get("code") or "")[:20]})
            return jsonify({"ok": bool(result.get("ok")), "authorized": bool(result.get("authorized"))})
        except ValueError as exc:
            return _error("TELEGRAM_INPUT_INVALID", str(exc), 400)
        except Exception:
            logger.exception("telegram sign in failed")
            return _error("TELEGRAM_SIGN_IN_FAILED", "Telegram 登录失败", 502)

    @app.delete("/api/v2/integrations/telegram/session", endpoint="mcc_v2_telegram_logout")
    def telegram_session_delete():
        denied = telegram_write_guard()
        if denied:
            return denied
        try:
            result = logout()
            return jsonify({"ok": bool(result.get("ok")), "authorized": False})
        except Exception:
            logger.exception("telegram logout failed")
            return _error("TELEGRAM_LOGOUT_FAILED", "Telegram 退出失败", 502)

    @app.put("/api/v2/integrations/telegram/channels", endpoint="mcc_v2_telegram_channels_save")
    def telegram_channels_save():
        denied = telegram_write_guard()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        channels = payload.get("channels")
        if not isinstance(channels, list) or len(channels) > 100:
            return _error("TELEGRAM_CHANNELS_INVALID", "channels 必须是最多 100 项的数组", 400)
        try:
            result = save_channels({"channels": channels, "resolve": bool(payload.get("resolve", True))})
            return jsonify({"ok": bool(result.get("ok")), "channelCount": len(result.get("channels") or [])})
        except ValueError as exc:
            return _error("TELEGRAM_CHANNELS_INVALID", str(exc), 400)
        except Exception:
            logger.exception("telegram channel save failed")
            return _error("TELEGRAM_CHANNELS_SAVE_FAILED", "Telegram 频道保存失败", 502)

    def hdhive_write_guard():
        if not _enabled(environment, "MCC_INTEGRATION_MANAGEMENT_ENABLED") or not _enabled(environment, "MCC_HDHIVE_MANAGEMENT_ENABLED"):
            return _error("HDHIVE_MANAGEMENT_DISABLED", "HDHive 管理开关未启用", 403)
        return None

    @app.get("/api/v2/integrations/hdhive/authorization", endpoint="mcc_v2_hdhive_authorization")
    def hdhive_authorization():
        try:
            return jsonify({"ok": True, "authorizationUrl": str(get_hdhive_auth_url())})
        except Exception:
            logger.exception("hdhive authorization url failed")
            return _error("HDHIVE_AUTHORIZATION_FAILED", "HDHive 授权地址生成失败", 502)

    @app.patch("/api/v2/integrations/hdhive/config", endpoint="mcc_v2_hdhive_config")
    def hdhive_config_save():
        denied = hdhive_write_guard()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        allowed_keys = {
            "ENV_HDHIVE_CHECKIN_ENABLED",
            "ENV_HDHIVE_CHECKIN_GAMBLER",
            "ENV_HDHIVE_CHECKIN_NOTIFY",
            "ENV_HDHIVE_UNLOCK_POINTS_LIMIT",
            "ENV_HDHIVE_UNLOCK_RATE_LIMIT",
            "ENV_HDHIVE_EXPIRY_REMINDER",
            "ENV_HDHIVE_REMINDER_INTERVAL_HOURS",
        }
        safe_payload = {key: payload[key] for key in allowed_keys if key in payload}
        try:
            saved = save_hdhive_config(safe_payload)
            return jsonify({"ok": True, "configuredFields": sorted(key for key, value in saved.items() if str(value).strip())})
        except Exception:
            logger.exception("hdhive config save failed")
            return _error("HDHIVE_CONFIG_SAVE_FAILED", "HDHive 配置保存失败", 502)

    @app.post("/api/v2/integrations/hdhive/check-ins", endpoint="mcc_v2_hdhive_checkin")
    def hdhive_checkin():
        denied = hdhive_write_guard()
        if denied:
            return denied
        remaining = _cooldown("hdhive-checkin", 3600)
        if remaining:
            return _error("INTEGRATION_COOLDOWN", f"请在 {remaining} 秒后重试", 409)
        try:
            result = run_hdhive_checkin()
            return jsonify({"ok": bool(result.get("ok")), "message": "HDHive 签到已执行" if result.get("ok") else "HDHive 签到失败"})
        except Exception:
            logger.exception("hdhive checkin failed")
            return _error("HDHIVE_CHECKIN_FAILED", "HDHive 签到失败", 502)

    return app
