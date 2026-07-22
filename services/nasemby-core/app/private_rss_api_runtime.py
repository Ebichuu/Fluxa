from __future__ import annotations

import os
import uuid

from flask import jsonify, request

from app.activity_log import write_activity
from app.http_runtime import current_request_id
from app.automation_action_runtime import present_automation_action
from app.private_rss_collector import PrivateRssCollector
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository
from app.rss_subscription_match_runtime import (
    RssAnalysisDependencies,
    RssSubscriptionMatchRuntime,
    register_rss_subscription_match,
)


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _error(code, message, status):
    write_activity(
        "operation",
        "private_rss_request",
        "error",
        message,
        request_id=current_request_id(),
        code=code,
        http_status=status,
    )
    return jsonify({"code": code, "error": message, "request_id": current_request_id()}), status


class PrivateRssService:
    def __init__(self, repository, action_repository, environment=None, collector=None):
        self.repository = repository
        self.action_repository = action_repository
        self.environment = os.environ if environment is None else environment
        self.collector = collector or PrivateRssCollector(repository)

    def collection_enabled(self):
        return _truthy(self.environment.get("MCC_PRIVATE_RSS_ENABLED"))

    def config_write_enabled(self):
        return _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))

    def create_test_action(self, source_id):
        claimed = self.action_repository.claim_action(
            f"rss-source-test:{source_id}:{uuid.uuid4().hex}",
            str(source_id),
            "private-rss",
            "rss-source-test",
            request_summary={"sourceId": str(source_id)},
        )
        action_id = claimed["action"]["action_id"]
        try:
            result = self.collector.fetch_source(source_id, persist=False)
            source = result if isinstance(result, dict) else {}
            summary = {
                "status": str(source.get("status") or "success")[:40],
                "items": int(source.get("items") or 0),
                "title": str(source.get("title") or "")[:120],
                "message": str(source.get("message") or "")[:240],
            }
            return self.action_repository.complete_action(
                action_id,
                "succeeded",
                summary,
                http_status=200,
            )
        except Exception:
            return self.action_repository.complete_action(
                action_id,
                "failed",
                {"message": "RSS 测试失败"},
                http_status=502,
                error_code="RSS_SOURCE_TEST_FAILED",
                error_message="RSS 测试失败",
            )


def register_private_rss(
    app,
    database_path,
    environment=None,
    repository=None,
    collector=None,
    subscription_loader=None,
    config_loader=None,
    match_runtime=None,
):
    resolved_environment = os.environ if environment is None else environment
    repository = repository or PrivateRssRepository(database_path)
    watch_repository = app.extensions.get("mcc_quality_watch_repository") or QualityWatchRepository(database_path)
    match_runtime = match_runtime or RssSubscriptionMatchRuntime(
        repository,
        watch_repository,
        subscription_loader,
        analysis=RssAnalysisDependencies(
            resolved_environment,
            app.extensions.get("mcc_torra_quality_client"),
            app.extensions.get("mcc_qbittorrent_client"),
            config_loader,
        ),
    )
    register_rss_subscription_match(app, match_runtime)
    service = PrivateRssService(
        repository,
        watch_repository,
        environment=resolved_environment,
        collector=collector or PrivateRssCollector(
            repository,
            item_matcher=match_runtime.match_inserted_rows,
            match_waker=match_runtime.wake_matches,
        ),
    )
    app.extensions["mcc_private_rss"] = service

    @app.get("/api/v2/rss-sources")
    def rss_sources_list():
        return jsonify({"items": service.repository.list_sources(), "summary": service.repository.summary(service.collection_enabled())})

    @app.post("/api/v2/rss-sources")
    def rss_sources_create():
        if not service.config_write_enabled():
            return _error("RSS_CONFIG_WRITE_DISABLED", "RSS 来源配置写入尚未启用", 503)
        try:
            source = service.repository.save_source(request.get_json(silent=True) or {})
        except ValueError as exc:
            return _error("RSS_SOURCE_INVALID", str(exc), 422)
        except Exception:
            return _error("RSS_SOURCE_CONFLICT", "RSS 来源已存在或无法保存", 409)
        response = jsonify(source)
        response.status_code = 201
        response.headers["Location"] = f"/api/v2/rss-sources/{source['id']}"
        return response

    @app.get("/api/v2/rss-sources/<source_id>")
    def rss_sources_detail(source_id):
        source = service.repository.get_source(source_id)
        return jsonify(source) if source else _error("RSS_SOURCE_NOT_FOUND", "RSS 来源不存在", 404)

    @app.patch("/api/v2/rss-sources/<source_id>")
    def rss_sources_update(source_id):
        if not service.config_write_enabled():
            return _error("RSS_CONFIG_WRITE_DISABLED", "RSS 来源配置写入尚未启用", 503)
        if not service.repository.get_source(source_id):
            return _error("RSS_SOURCE_NOT_FOUND", "RSS 来源不存在", 404)
        try:
            return jsonify(service.repository.save_source(request.get_json(silent=True) or {}, source_id=source_id))
        except ValueError as exc:
            return _error("RSS_SOURCE_INVALID", str(exc), 422)
        except Exception:
            return _error("RSS_SOURCE_CONFLICT", "RSS 来源已存在或无法保存", 409)

    @app.delete("/api/v2/rss-sources/<source_id>")
    def rss_sources_delete(source_id):
        if not service.config_write_enabled():
            return _error("RSS_CONFIG_WRITE_DISABLED", "RSS 来源配置写入尚未启用", 503)
        if not service.repository.delete_source(source_id):
            return _error("RSS_SOURCE_NOT_FOUND", "RSS 来源不存在", 404)
        return "", 204

    @app.post("/api/v2/rss-sources/<source_id>/tests")
    def rss_sources_test(source_id):
        if not service.collection_enabled():
            return _error("RSS_COLLECTION_DISABLED", "真实 RSS 访问尚未启用", 503)
        if not service.repository.get_source(source_id):
            return _error("RSS_SOURCE_NOT_FOUND", "RSS 来源不存在", 404)
        action = service.create_test_action(source_id)
        public_action = present_automation_action(action)
        response = jsonify(public_action)
        response.status_code = 202
        response.headers["Location"] = f"/api/v2/automation-actions/{public_action['id']}"
        return response

    @app.get("/api/v2/rss-items")
    def rss_items_list():
        window = str(request.args.get("window") or "").lower()
        window_hours = {"1h": 1, "24h": 24, "7d": 168}.get(window)
        try:
            payload = service.repository.search_items(
                query=request.args.get("query") or "",
                source_id=request.args.get("sourceId") or "",
                window_hours=window_hours,
                identity_status=request.args.get("identityStatus") or "",
                limit=request.args.get("limit") or 50,
                offset=request.args.get("offset") or 0,
            )
        except (TypeError, ValueError):
            return _error("RSS_QUERY_INVALID", "种子库查询参数无效", 422)
        return jsonify(payload)

    @app.get("/api/v2/rss-items/<item_id>")
    def rss_items_detail(item_id):
        item = service.repository.get_item(item_id)
        return jsonify(item) if item else _error("RSS_ITEM_NOT_FOUND", "种子条目不存在", 404)

    @app.get("/api/v2/rss-matches")
    def rss_matches_list():
        try:
            return jsonify(service.repository.list_matches(
                status=request.args.get("status") or "",
                limit=request.args.get("limit") or 50,
                offset=request.args.get("offset") or 0,
            ))
        except (TypeError, ValueError):
            return _error("RSS_MATCH_QUERY_INVALID", "RSS 匹配查询参数无效", 422)

    return service
