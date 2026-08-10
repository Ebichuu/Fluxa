from __future__ import annotations

from flask import jsonify, request

from app.automation_action_runtime import present_automation_action
from app.http_runtime import current_request_id
from app.quality_watch_baseline_init_runtime import BaselineInitializationError
from app.rss_subscription_match_runtime import RssExactDownloadError


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


def _created_response(payload, location):
    response = jsonify(payload)
    response.status_code = 201
    response.headers["Location"] = location
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
        except BaselineInitializationError as exc:
            return _error_response(AutomationApiError(exc.code, exc.message, exc.status))
        except RssExactDownloadError as exc:
            return _error_response(AutomationApiError(exc.code, exc.message, exc.status))

    @app.get("/api/v2/subscription-automation/settings")
    def subscription_automation_settings_get():
        return execute(lambda: jsonify(service.present_settings()))

    @app.patch("/api/v2/subscription-automation/settings")
    def subscription_automation_settings_patch():
        return execute(lambda: jsonify(service.update_settings(request.get_json(silent=True))))

    @app.get("/api/v2/subscription-automation/bridge-summary")
    def subscription_automation_bridge_summary_get():
        return execute(lambda: jsonify(service.get_bridge_summary()))

    @app.post("/api/v2/subscription-automation/baseline-initialization-previews")
    def subscription_automation_baseline_preview_post():
        def create():
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or body:
                raise AutomationApiError(
                    "BASELINE_INITIALIZATION_PREVIEW_FIELDS_INVALID",
                    "预览请求必须是 JSON 空对象",
                    422,
                )
            payload = service.create_baseline_initialization_preview()
            location = (
                "/api/v2/subscription-automation/baseline-initializations/"
                f"{payload['runId']}"
            )
            return _created_response(payload, location)

        return execute(create)

    @app.post("/api/v2/subscription-automation/baseline-initializations")
    def subscription_automation_baseline_initialization_post():
        def create():
            payload = service.execute_baseline_initialization(request.get_json(silent=True))
            location = (
                "/api/v2/subscription-automation/baseline-initializations/"
                f"{payload['runId']}"
            )
            return _created_response(payload, location)

        return execute(create)

    @app.get("/api/v2/subscription-automation/baseline-initializations/<path:run_id>")
    def subscription_automation_baseline_initialization_get(run_id):
        return execute(lambda: jsonify(service.get_baseline_initialization(run_id)))

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

    @app.post("/api/v2/rss-items/<item_id>/download-previews")
    def rss_item_download_preview(item_id):
        def preview():
            if not service.rss_runtime:
                raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise AutomationApiError(
                    "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID",
                    "请求必须是 JSON 空对象",
                    422,
                )
            service._validate_fields(body, set())
            result, _fingerprint = service.rss_runtime.preview_resource_download(item_id)
            return jsonify(result)

        return execute(preview)

    @app.post("/api/v2/rss-items/<item_id>/downloads")
    def rss_item_download(item_id):
        def create():
            service._require_write()
            body = request.get_json(silent=True)
            body = body if isinstance(body, dict) else {}
            service._validate_fields(body, {"confirm", "previewToken", "idempotencyKey"})
            if body.get("confirm") is not True:
                raise AutomationApiError(
                    "RSS_RESOURCE_CONFIRMATION_REQUIRED",
                    "资源下载需要明确确认",
                    422,
                )
            if not str(body.get("previewToken") or "").strip():
                raise AutomationApiError(
                    "RSS_RESOURCE_PREVIEW_REQUIRED",
                    "资源下载需要有效预览",
                    422,
                )
            service._validate_idempotency(body)
            if not service.rss_runtime:
                raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
            action = service.rss_runtime.execute_resource_download(
                item_id,
                body.get("previewToken"),
                body.get("idempotencyKey"),
            )
            return _accepted_response(action)

        return execute(create)

    @app.post("/api/v2/rss-matches/<match_id>/exact-download-previews")
    def rss_match_exact_download_preview(match_id):
        def preview():
            if not service.rss_runtime:
                raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise AutomationApiError(
                    "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID",
                    "请求必须是 JSON 空对象",
                    422,
                )
            service._validate_fields(body, set())
            result = service.rss_runtime.preview_exact_download(match_id)
            if result.get("status") == "missing":
                raise AutomationApiError("RSS_MATCH_NOT_FOUND", "RSS 匹配不存在", 404)
            return jsonify(result)

        return execute(preview)

    @app.post("/api/v2/rss-artifact-groups/<group_id>/exact-download-previews")
    def rss_artifact_exact_download_preview(group_id):
        def preview():
            if not service.rss_runtime:
                raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise AutomationApiError(
                    "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID", "请求必须是 JSON 空对象", 422
                )
            service._validate_fields(body, set())
            result, _fingerprint, _match_ids = service.rss_runtime.preview_artifact_exact_download(group_id)
            return jsonify(result)

        return execute(preview)

    @app.post("/api/v2/rss-artifact-groups/<group_id>/exact-downloads")
    def rss_artifact_exact_download(group_id):
        def create():
            service._require_write()
            body = request.get_json(silent=True)
            body = body if isinstance(body, dict) else {}
            service._validate_fields(body, {"confirm", "previewToken", "idempotencyKey"})
            if body.get("confirm") is not True:
                raise AutomationApiError("RSS_EXACT_CONFIRMATION_REQUIRED", "精准下载需要明确确认", 422)
            if not str(body.get("previewToken") or "").strip():
                raise AutomationApiError("RSS_EXACT_PREVIEW_REQUIRED", "精准下载需要有效预览", 422)
            service._validate_idempotency(body)
            if not service.rss_runtime:
                raise AutomationApiError("RSS_MATCH_RUNTIME_UNAVAILABLE", "RSS 匹配运行时不可用", 503)
            action = service.rss_runtime.execute_artifact_exact_download(
                group_id,
                body.get("previewToken"),
                body.get("idempotencyKey"),
            )
            return _accepted_response(action)

        return execute(create)

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
