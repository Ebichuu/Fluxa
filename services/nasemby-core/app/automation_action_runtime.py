from __future__ import annotations

import hashlib
import re

from flask import jsonify

from app.http_runtime import current_request_id


SENSITIVE_KEY_PARTS = {
    "authorization",
    "candidate",
    "cookie",
    "download",
    "feed_url",
    "headers",
    "passkey",
    "password",
    "payload",
    "secret",
    "stack",
    "token",
    "trace",
    "url",
}
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _is_sensitive_key(key):
    normalized = str(key or "").strip().lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _safe_mapping(value, depth):
    result = {}
    for key, item in list(value.items())[:24]:
        if _is_sensitive_key(key):
            continue
        safe = _safe_summary(item, depth + 1)
        if safe is not None:
            result[str(key)[:80]] = safe
    return result


def _safe_sequence(value, depth):
    values = (_safe_summary(item, depth + 1) for item in value[:20])
    return [item for item in values if item is not None]


def _safe_summary(value, depth=0):
    if depth > 3:
        return None
    if isinstance(value, dict):
        return _safe_mapping(value, depth)
    if isinstance(value, list):
        return _safe_sequence(value, depth)
    if isinstance(value, str):
        return URL_PATTERN.sub("[已脱敏]", value)[:240]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:240]


def _external_job_reference(external_job_id):
    value = str(external_job_id or "").strip()
    if not value:
        return ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _action_text(action, field, limit=0):
    value = str(action.get(field) or "")
    return value[:limit] if limit else value


def _action_error(action):
    code = _action_text(action, "error_code", 120)
    message = URL_PATTERN.sub("[已脱敏]", _action_text(action, "error_message"))[:240]
    return {"code": code, "message": message} if code or message else None


def present_automation_action(action):
    if not isinstance(action, dict):
        return None
    return {
        "id": _action_text(action, "action_id"),
        "subscriptionId": _action_text(action, "subscription_key"),
        "unitId": _action_text(action, "unit_key"),
        "provider": _action_text(action, "provider"),
        "type": _action_text(action, "action_type"),
        "status": _action_text(action, "status"),
        "externalJobId": _external_job_reference(action.get("external_job_id")),
        "createdAt": _action_text(action, "created_at"),
        "updatedAt": _action_text(action, "updated_at"),
        "completedAt": _action_text(action, "completed_at"),
        "result": _safe_summary(action.get("response_summary")),
        "error": _action_error(action),
    }


def register_automation_actions(app, action_repository):
    app.extensions["mcc_automation_action_repository"] = action_repository

    @app.get("/api/v2/automation-actions/<action_id>")
    def automation_action_detail(action_id):
        action = action_repository.get_action(action_id)
        if not action:
            return jsonify({
                "code": "AUTOMATION_ACTION_NOT_FOUND",
                "error": "自动化动作不存在",
                "request_id": current_request_id(),
            }), 404
        return jsonify(present_automation_action(action))

    return action_repository
