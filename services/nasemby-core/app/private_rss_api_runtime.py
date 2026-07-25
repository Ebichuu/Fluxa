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
from app.task_public_runtime import safe_public_text
from app.torra_subscription_keys import torra_public_match_keys


RSS_MATCH_PUBLIC_FIELDS = (
    "id",
    "itemId",
    "subscriptionId",
    "unitId",
    "status",
    "reason",
    "triggerActionId",
    "createdAt",
    "updatedAt",
)
RSS_MATCH_IDENTITY_BASES = {"tmdb", "standard-title-map", "title", "title-alias"}
RSS_MATCH_MEDIA_TYPES = {"movie", "tv"}
RSS_MATCH_SOURCES = {"manual"}
RSS_MATCH_NUMBER_FIELDS = {
    "year": ("item", "subscription"),
    "season": ("item", "unit"),
    "episode": ("start", "end", "unit"),
}


def _public_reason_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value)
    else:
        return None
    return number if 0 < number <= 1_000_000 else None


def _public_match_reason(reason, hidden_refs=()):
    source = reason if isinstance(reason, dict) else {}
    result = {}

    identity_source = source.get("identity")
    if isinstance(identity_source, dict):
        identity = {}
        basis = str(identity_source.get("basis") or "").strip().lower()
        if basis in RSS_MATCH_IDENTITY_BASES:
            identity["basis"] = basis
        tmdb_id = str(identity_source.get("tmdbId") or "").strip()
        if tmdb_id.isdigit() and len(tmdb_id) <= 24:
            identity["tmdbId"] = tmdb_id
        alias_value = identity_source.get("alias")
        alias = safe_public_text(alias_value) if isinstance(alias_value, str) else ""
        for hidden in sorted(set(hidden_refs), key=len, reverse=True):
            if hidden:
                alias = alias.replace(hidden, "[已隐藏]")
        if alias:
            identity["alias"] = alias
        if identity:
            result["identity"] = identity

    media_type = str(source.get("mediaType") or "").strip().lower()
    if media_type in RSS_MATCH_MEDIA_TYPES:
        result["mediaType"] = media_type

    for section, fields in RSS_MATCH_NUMBER_FIELDS.items():
        section_source = source.get(section)
        if not isinstance(section_source, dict):
            continue
        projected = {}
        for field in fields:
            number = _public_reason_number(section_source.get(field))
            if number is not None:
                projected[field] = number
        if projected:
            result[section] = projected

    match_source = str(source.get("matchSource") or "").strip().lower()
    if match_source in RSS_MATCH_SOURCES:
        result["matchSource"] = match_source
    return result


def _present_rss_match(match):
    if not isinstance(match, dict):
        return None
    value = {field: match.get(field) for field in RSS_MATCH_PUBLIC_FIELDS}
    value["subscriptionId"], value["unitId"] = torra_public_match_keys(
        match.get("subscriptionId"), match.get("unitId")
    )
    internal_key = str(match.get("subscriptionId") or "").strip()
    hidden_refs = []
    if internal_key != value["subscriptionId"]:
        hidden_refs.extend((
            internal_key,
            internal_key.removeprefix("torra:"),
            str(match.get("unitId") or "").strip(),
        ))
    value["reason"] = _public_match_reason(match.get("reason"), hidden_refs)
    return value


def _present_rss_match_list(payload):
    value = dict(payload) if isinstance(payload, dict) else {}
    value["items"] = [
        presented
        for presented in (_present_rss_match(match) for match in value.get("items") or [])
        if presented is not None
    ]
    return value


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
    def __init__(self, repository, action_repository, environment=None, collector=None, match_runtime=None):
        self.repository = repository
        self.action_repository = action_repository
        self.environment = os.environ if environment is None else environment
        self.collector = collector or PrivateRssCollector(repository)
        self.match_runtime = match_runtime

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


    def backfill_identities(self, limit=50):
        if not self.match_runtime:
            raise RuntimeError("RSS identity matcher is not initialized")
        return self.match_runtime.backfill_unidentified_items(limit)

    def run_matcher(self, limit=200):
        if not self.match_runtime:
            raise RuntimeError("RSS identity matcher is not initialized")
        return self.match_runtime.match_existing_items(limit)

    def create_match(self, body):
        if not self.match_runtime:
            raise RuntimeError("RSS matcher is not initialized")
        body = body if isinstance(body, dict) else {}
        if set(body) - {"rssItemId", "subscriptionId", "unitId"}:
            return {"status": "invalid", "reason": "unsupported_fields"}
        return self.match_runtime.create_manual_match(
            body.get("rssItemId"),
            body.get("subscriptionId"),
            body.get("unitId"),
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
        match_runtime=match_runtime,
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
                tmdb_id=request.args.get("tmdbId") or "",
                media_type=request.args.get("mediaType") or "",
                season_number=request.args.get("seasonNumber") or None,
                year=request.args.get("year") or "",
            )
        except (TypeError, ValueError):
            return _error("RSS_QUERY_INVALID", "种子库查询参数无效", 422)
        return jsonify(payload)

    @app.post("/api/v2/rss-items/identity-backfills")
    def rss_items_identity_backfill():
        if not service.config_write_enabled():
            return _error("RSS_IDENTITY_WRITE_DISABLED", "RSS 身份回填需要开启本地写入", 503)
        try:
            raw_limit = (request.get_json(silent=True) or {}).get("limit", 50)
            if isinstance(raw_limit, bool):
                raise ValueError
            limit = int(raw_limit)
            if limit < 1 or limit > 200:
                raise ValueError
            result = service.backfill_identities(limit)
        except (TypeError, ValueError):
            return _error("RSS_BACKFILL_INVALID", "身份回填批量必须是 1 到 200", 422)
        except Exception:
            return _error("RSS_BACKFILL_FAILED", "RSS 身份回填失败", 500)
        write_activity("rss", "identity_backfill", "success", "RSS 身份回填完成", **result)
        return jsonify({"ok": True, **result})

    @app.post("/api/v2/rss-items/match-runs")
    def rss_items_match_run():
        if not service.config_write_enabled():
            return _error("RSS_MATCH_WRITE_DISABLED", "RSS 匹配需要开启本地写入", 503)
        try:
            raw_limit = (request.get_json(silent=True) or {}).get("limit", 200)
            if isinstance(raw_limit, bool):
                raise ValueError
            limit = int(raw_limit)
            if limit < 1 or limit > 200:
                raise ValueError
            result = service.run_matcher(limit)
        except (TypeError, ValueError):
            return _error("RSS_MATCH_INVALID", "RSS 匹配批量必须是 1 到 200", 422)
        except Exception:
            return _error("RSS_MATCH_FAILED", "RSS 历史匹配失败", 500)
        write_activity("rss", "match_run", "success", "RSS 历史匹配完成", **result)
        return jsonify({"ok": True, **result})

    @app.get("/api/v2/rss-items/<item_id>")
    def rss_items_detail(item_id):
        item = service.repository.get_item(item_id)
        return jsonify(item) if item else _error("RSS_ITEM_NOT_FOUND", "种子条目不存在", 404)

    @app.get("/api/v2/rss-matches")
    def rss_matches_list():
        try:
            return jsonify(_present_rss_match_list(service.repository.list_matches(
                status=request.args.get("status") or "",
                limit=request.args.get("limit") or 50,
                offset=request.args.get("offset") or 0,
            )))
        except (TypeError, ValueError):
            return _error("RSS_MATCH_QUERY_INVALID", "RSS 匹配查询参数无效", 422)

    @app.get("/api/v2/rss-matches/<match_id>")
    def rss_matches_detail(match_id):
        match = service.repository.get_match(match_id)
        return jsonify(_present_rss_match(match)) if match else _error("RSS_MATCH_NOT_FOUND", "RSS 匹配不存在", 404)

    @app.post("/api/v2/rss-matches")
    def rss_matches_create():
        try:
            result = service.create_match(request.get_json(silent=True))
        except Exception:
            return _error("RSS_MATCH_CREATE_FAILED", "RSS 匹配建立失败", 500)
        reason = result.get("reason")
        if result.get("status") == "missing":
            messages = {
                "item_missing": "RSS 种子条目不存在",
                "subscription_missing": "追更不存在或当前不可读取",
                "watch_unit_missing": "观察单元不存在",
            }
            return _error("RSS_MATCH_TARGET_NOT_FOUND", messages.get(reason, "RSS 匹配目标不存在"), 404)
        if result.get("status") == "blocked":
            messages = {
                "watch_unit_inactive": "当前没有有效的质量观察窗口",
                "torra_subscription_missing": "Torra 追更归属尚未确认",
                "torra_unavailable": "Torra 当前不可用",
            }
            return _error("RSS_MATCH_NOT_READY", messages.get(reason, "RSS 匹配当前不可建立"), 409)
        if result.get("status") == "conflict":
            return _error("RSS_MATCH_CONFLICT", "该种子已归属其他追更观察单元", 409)
        if result.get("status") == "invalid":
            messages = {
                "required_fields_missing": "需要指定种子、追更和观察单元",
                "unsupported_fields": "请求包含不支持的字段",
                "watch_unit_owner_mismatch": "观察单元不属于当前追更",
                "torra_subscription_owner_mismatch": "Torra 追更归属不一致",
                "item_not_compatible": "种子身份、类型或季集与当前追更不一致",
            }
            return _error("RSS_MATCH_INVALID", messages.get(reason, "RSS 匹配参数无效"), 422)
        match = _present_rss_match(result.get("match") or {})
        response = jsonify(match)
        response.status_code = 200 if result.get("status") == "existing" else 201
        response.headers["Location"] = f"/api/v2/rss-matches/{match.get('id', '')}"
        return response

    return service
