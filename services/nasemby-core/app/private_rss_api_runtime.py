from __future__ import annotations

import os
import threading
import uuid

from flask import jsonify, request

from app.http_runtime import current_request_id
from app.private_rss_collector import PrivateRssCollector
from app.private_rss_repository import PrivateRssRepository


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _error(code, message, status):
    return jsonify({"code": code, "error": message, "request_id": current_request_id()}), status


class PrivateRssService:
    def __init__(self, repository, environment=None, collector=None):
        self.repository = repository
        self.environment = os.environ if environment is None else environment
        self.collector = collector or PrivateRssCollector(repository)
        self.actions = {}
        self.action_lock = threading.Lock()

    def collection_enabled(self):
        return _truthy(self.environment.get("MCC_PRIVATE_RSS_ENABLED"))

    def config_write_enabled(self):
        return _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED"))

    def create_test_action(self, source_id):
        action_id = uuid.uuid4().hex
        action = {
            "id": action_id,
            "provider": "private-rss",
            "type": "rss-source-test",
            "status": "running",
            "result": None,
        }
        with self.action_lock:
            self.actions[action_id] = action
        try:
            result = self.collector.fetch_source(source_id, persist=False)
            action = {**action, "status": "succeeded", "result": result}
        except Exception:
            action = {**action, "status": "failed", "result": {"message": "RSS 测试失败"}}
        with self.action_lock:
            self.actions[action_id] = action
        return action

    def get_action(self, action_id):
        with self.action_lock:
            action = self.actions.get(str(action_id))
        return dict(action) if action else None


def register_private_rss(app, database_path, environment=None, repository=None, collector=None):
    service = PrivateRssService(
        repository or PrivateRssRepository(database_path),
        environment=environment,
        collector=collector,
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
        response = jsonify(action)
        response.status_code = 202
        response.headers["Location"] = f"/api/v2/automation-actions/{action['id']}"
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
        return jsonify({"items": [], "total": 0, "limit": 50, "offset": 0})

    @app.get("/api/v2/automation-actions/<action_id>")
    def automation_action_detail(action_id):
        action = service.get_action(action_id)
        return jsonify(action) if action else _error("AUTOMATION_ACTION_NOT_FOUND", "自动化动作不存在", 404)

    return service
