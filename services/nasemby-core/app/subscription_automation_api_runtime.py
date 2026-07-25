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


def _rss_download_context(service, match_id, body):
    if not service.rss_runtime:
        raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
    result = service.rss_runtime.prepare_download(
        match_id,
        body.get("analysisActionId"),
        body.get("idempotencyKey"),
    )
    reason = result.get("reason")
    if result.get("status") == "ready":
        return result
    if result.get("status") == "missing":
        raise AutomationApiError("RSS_MATCH_NOT_FOUND", "RSS 匹配不存在", 404)
    if reason == "analysis_action_missing":
        raise AutomationApiError("TORRA_ANALYSIS_ACTION_NOT_FOUND", "分析动作不属于当前 RSS 匹配", 404)
    if reason == "analysis_action_not_ready":
        raise AutomationApiError("TORRA_ANALYSIS_ACTION_NOT_READY", "分析动作尚未成功", 409)
    if reason == "analysis_has_no_upgrade":
        raise AutomationApiError("TORRA_ANALYSIS_HAS_NO_UPGRADE", "分析动作没有可下载的升级候选", 409)
    if reason == "idempotency_conflict":
        raise AutomationApiError("TORRA_REWASH_IDEMPOTENCY_CONFLICT", "幂等键已用于其他 RSS 匹配下载", 409)
    raise AutomationApiError("RSS_MATCH_NOT_READY", "RSS 匹配当前不可下载", 409)


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

    @app.post("/api/v2/rss-matches/<match_id>/torra-rewashes")
    def rss_match_rewash_download(match_id):
        def create():
            service._require_download()
            body = request.get_json(silent=True)
            body = body if isinstance(body, dict) else {}
            service._validate_fields(body, {"confirm", "idempotencyKey", "analysisActionId"})
            if body.get("confirm") is not True:
                raise AutomationApiError("TORRA_REWASH_CONFIRMATION_REQUIRED", "下载需要明确确认", 422)
            service._validate_idempotency(body)
            if not str(body.get("analysisActionId") or "").strip():
                raise AutomationApiError("TORRA_ANALYSIS_ACTION_REQUIRED", "下载需要指定分析动作", 422)
            context = _rss_download_context(service, match_id, body)
            action = service.create_download(context["subscriptionId"], {
                **body,
                "unitId": context["unitId"],
            })
            recorded = service.rss_runtime.record_download(
                match_id,
                context["analysisActionId"],
                action,
            )
            if recorded.get("status") in {"conflict", "missing", "blocked"}:
                raise AutomationApiError("RSS_MATCH_DOWNLOAD_LINK_FAILED", "RSS 匹配下载动作关联失败", 409)
            return _accepted_response(action)

        return execute(create)

    return service
