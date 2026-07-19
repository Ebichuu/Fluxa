from __future__ import annotations

from flask import jsonify, request

from app.automation_action_runtime import present_automation_action
from app.http_runtime import current_request_id


class AutomationApiError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = int(status)


def _error_response(error):
    return jsonify({
        "code": error.code,
        "error": error.message,
        "request_id": current_request_id(),
    }), error.status


def _accepted_response(action):
    public = present_automation_action(action)
    response = jsonify(public)
    response.status_code = 202
    response.headers["Location"] = f"/api/v2/automation-actions/{public['id']}"
    return response


def register_subscription_automation(app, service):
    app.extensions["mcc_subscription_automation"] = service

    def execute(callback):
        try:
            return callback()
        except AutomationApiError as exc:
            return _error_response(exc)

    @app.get("/api/v2/subscription-automation/settings")
    def subscription_automation_settings_get():
        return execute(lambda: jsonify(service.present_settings()))

    @app.patch("/api/v2/subscription-automation/settings")
    def subscription_automation_settings_patch():
        return execute(lambda: jsonify(service.update_settings(request.get_json(silent=True))))

    @app.get("/api/v2/subscriptions/<path:key>/quality-watch")
    def subscription_quality_watch_get(key):
        return execute(lambda: jsonify(service.get_quality_watch(key)))

    @app.patch("/api/v2/subscriptions/<path:key>/quality-watch")
    def subscription_quality_watch_patch(key):
        return execute(lambda: jsonify(service.update_quality_watch(key, request.get_json(silent=True))))

    @app.post("/api/v2/subscriptions/<path:key>/torra-rewash-analyses")
    def subscription_rewash_analysis(key):
        return execute(lambda: _accepted_response(service.create_analysis(key, request.get_json(silent=True))))

    @app.post("/api/v2/subscriptions/<path:key>/torra-rewashes")
    def subscription_rewash_download(key):
        return execute(lambda: _accepted_response(service.create_download(key, request.get_json(silent=True))))

    @app.post("/api/v2/rss-matches/<match_id>/torra-rewash-analyses")
    def rss_match_rewash_analysis(match_id):
        return execute(lambda: _accepted_response(service.create_rss_analysis(match_id, request.get_json(silent=True))))

    return service
