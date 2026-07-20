from __future__ import annotations

from flask import jsonify, request

from app.activity_log import clear_activities, read_activities, write_activity
from app.http_runtime import current_request_id


def _error(code, message, status):
    return jsonify({
        "ok": False,
        "code": code,
        "error": message,
        "request_id": current_request_id(),
    }), status


def register_activity_api(app):
    @app.get("/api/v2/activity/logs")
    def activity_logs_v2():
        category = str(request.args.get("category") or "").strip()
        limit = request.args.get("limit", "200")
        return jsonify({"ok": True, "logs": read_activities(limit=limit, category=category)})

    @app.delete("/api/v2/activity/logs")
    def activity_logs_clear_v2():
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm") is not True:
            return _error("ACTIVITY_CLEAR_CONFIRMATION_REQUIRED", "需要明确确认清空活动日志", 400)
        clear_activities()
        write_activity(
            "system",
            "activity_logs_cleared",
            "success",
            "活动日志已清空",
            request_id=current_request_id(),
        )
        return jsonify({"ok": True, "message": "活动日志已清空"})

    return app
