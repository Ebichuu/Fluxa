from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from app.activity_log import write_activity
from app.http_runtime import current_request_id
from app.automation_action_runtime import present_automation_action
from app.private_rss_collector import PrivateRssCollector
from app.private_rss_repository import (
    PrivateRssRepository,
    RssMatchCleanupConflict,
    RssMatchCleanupStale,
)
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
    "torraLinked",
    "targetKey",
    "artifactKey",
    "ruleId",
    "ruleHash",
    "candidateScore",
    "baselineScore",
    "evaluationStatus",
    "decision",
    "evaluationReason",
    "evaluationActionId",
    "downloadActionId",
    "candidateSummary",
    "baselineSummary",
    "bestCandidate",
    "evaluatedAt",
    "archiveState",
    "archivedAt",
    "archiveReasonCode",
    "archiveRunId",
    "version",
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
SHANGHAI_TZ = timezone(timedelta(hours=8))


def _published_date_bounds(value):
    text = str(value or "").strip()
    if not text:
        return "", ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("发布日期无效")
    try:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ)
    except ValueError as exc:
        raise ValueError("发布日期无效") from exc
    start = day.astimezone(timezone.utc)
    end = (day + timedelta(days=1)).astimezone(timezone.utc)
    return (
        start.isoformat(timespec="seconds").replace("+00:00", "Z"),
        end.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


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


def _public_score_summary(summary, baseline=False):
    source = summary if isinstance(summary, dict) else {}
    result = {}
    version_summary = safe_public_text(source.get("versionSummary"))
    if version_summary:
        result["versionSummary"] = version_summary[:240]
    version_state = str(source.get("versionState") or "").strip().lower()
    if version_state in {"accepted", "unconfirmed", "rejected"}:
        result["versionState"] = version_state
    version_name = safe_public_text(source.get("versionName"))
    if version_name:
        result["versionName"] = version_name[:120]
    breakdown = []
    for row in source.get("scoreBreakdown") or []:
        if not isinstance(row, dict) or isinstance(row.get("score"), bool):
            continue
        if not isinstance(row.get("score"), (int, float)):
            continue
        field = str(row.get("field") or "").strip()[:80]
        label = safe_public_text(row.get("label"))[:120]
        if field and label:
            breakdown.append({"field": field, "label": label, "score": row["score"]})
    if breakdown:
        result["scoreBreakdown"] = breakdown
    if baseline:
        artifact_key = str(source.get("artifactKey") or "").strip()
        if artifact_key.startswith("baseline:") and len(artifact_key) <= 80:
            result["artifactKey"] = artifact_key
        sources = sorted({
            str(value or "").strip().lower()
            for value in source.get("sources") or []
            if str(value or "").strip().lower() in {"torra", "qb", "symedia"}
        })
        if sources:
            result["sources"] = sources
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
    value["candidateSummary"] = _public_score_summary(match.get("candidateSummary"))
    value["baselineSummary"] = _public_score_summary(match.get("baselineSummary"), baseline=True)
    value["bestCandidate"] = bool(match.get("bestCandidate"))
    return value


def _present_rss_match_list(payload):
    value = dict(payload) if isinstance(payload, dict) else {}
    value["items"] = [
        presented
        for presented in (_present_rss_match(match) for match in value.get("items") or [])
        if presented is not None
    ]
    return value


def _present_rss_match_group(group):
    if not isinstance(group, dict):
        return None
    subscription_id, unit_id = torra_public_match_keys(
        group.get("subscriptionId"), group.get("unitId")
    )
    ownerships = []
    for row in group.get("ownerships") or []:
        if not isinstance(row, dict):
            continue
        owner_subscription, owner_unit = torra_public_match_keys(
            row.get("subscriptionId"), row.get("unitId")
        )
        ownerships.append({
            "matchId": str(row.get("matchId") or "")[:80],
            "subscriptionId": owner_subscription,
            "unitId": owner_unit,
            "state": str(row.get("state") or "conflict")[:40],
            "reasonCode": str(row.get("reasonCode") or "")[:80],
        })
    return {
        "id": str(group.get("id") or "")[:80],
        "subscriptionId": subscription_id,
        "unitId": unit_id,
        "title": safe_public_text(group.get("title"))[:240],
        "episodeLabel": safe_public_text(group.get("episodeLabel"))[:80],
        "state": str(group.get("state") or "monitoring_rss")[:80],
        "candidateCount": max(0, int(group.get("candidateCount") or 0)),
        "bestMatchId": str(group.get("bestMatchId") or "")[:80],
        "bestArtifactKey": str(group.get("bestArtifactKey") or "")[:120],
        "bestCandidateScore": group.get("bestCandidateScore"),
        "baselineScore": group.get("baselineScore"),
        "baselineSummary": _public_score_summary(group.get("baselineSummary"), baseline=True),
        "baselineState": str(group.get("baselineState") or "baseline_missing")[:80],
        "blockerCode": str(group.get("blockerCode") or "")[:80],
        "nextAction": str(group.get("nextAction") or "continue_monitoring")[:80],
        "lastCandidateAt": str(group.get("lastCandidateAt") or "")[:80],
        "ownerships": ownerships,
        "candidates": [
            presented
            for presented in (
                _present_rss_match(candidate)
                for candidate in group.get("candidates") or []
            )
            if presented is not None
        ],
    }


def _present_rss_match_group_list(payload):
    value = dict(payload) if isinstance(payload, dict) else {}
    value["groups"] = [
        presented
        for presented in (
            _present_rss_match_group(group) for group in value.get("groups") or []
        )
        if presented is not None
    ]
    counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
    value["counts"] = {
        "total": max(0, int(counts.get("total") or 0)),
        "scoreableTotal": max(0, int(counts.get("scoreable_total") or 0)),
        "initialBest": max(0, int(counts.get("initial_best") or 0)),
        "waitingBaseline": max(0, int(counts.get("waiting_baseline") or 0)),
        "monitoringRss": max(0, int(counts.get("monitoring_rss") or 0)),
        "upgradeAvailable": max(0, int(counts.get("upgrade_available") or 0)),
        "protected": max(0, int(counts.get("protected") or 0)),
        "needsCleanup": max(0, int(counts.get("needs_cleanup") or 0)),
        "blocked": max(0, int(counts.get("blocked") or 0)),
    }
    return value


def _present_artifact_candidate(match):
    value = _present_rss_match(match)
    if value:
        value.pop("artifactKey", None)
    return value


def _present_rss_artifact_group(group):
    if not isinstance(group, dict):
        return None
    subscription_id, _ = torra_public_match_keys(group.get("subscriptionId"), "")
    representative = _present_artifact_candidate(group.get("representativeMatch"))
    unit_results = []
    for row in group.get("unitResults") or []:
        if not isinstance(row, dict):
            continue
        _, unit_id = torra_public_match_keys(group.get("subscriptionId"), row.get("unitId"))
        unit_results.append({
            "unitId": unit_id,
            "seasonNumber": row.get("seasonNumber"),
            "episodeNumber": row.get("episodeNumber"),
            "state": str(row.get("state") or "monitoring_rss")[:80],
            "winsUnit": bool(row.get("winsUnit")),
            "baselineState": str(row.get("baselineState") or "baseline_missing")[:80],
            "blockerCode": str(row.get("blockerCode") or "")[:80],
            "nextAction": str(row.get("nextAction") or "continue_monitoring")[:80],
            "match": _present_artifact_candidate(row.get("match")),
        })
    return {
        "id": str(group.get("id") or "")[:80],
        "subscriptionId": subscription_id,
        "unitId": str((representative or {}).get("unitId") or "")[:200],
        "title": safe_public_text(group.get("title"))[:240],
        "episodeLabel": safe_public_text(group.get("episodeLabel"))[:80],
        "state": str(group.get("state") or "monitoring_rss")[:80],
        "candidateCount": max(0, int(group.get("candidateCount") or 0)),
        "coveredUnits": [row["unitId"] for row in unit_results],
        "coveredEpisodeStart": group.get("coveredEpisodeStart"),
        "coveredEpisodeEnd": group.get("coveredEpisodeEnd"),
        "winsAllCoveredUnits": bool(group.get("winsAllCoveredUnits")),
        "representativeMatch": representative,
        "bestMatchId": str((representative or {}).get("id") or "")[:80],
        "bestCandidateScore": group.get("bestCandidateScore"),
        "baselineScore": group.get("baselineScore"),
        "baselineSummary": (representative or {}).get("baselineSummary") or {},
        "baselineState": str(group.get("baselineState") or "baseline_missing")[:80],
        "blockerCode": str(group.get("blockerCode") or "")[:80],
        "nextAction": str(group.get("nextAction") or "continue_monitoring")[:80],
        "lastCandidateAt": str(group.get("lastCandidateAt") or "")[:80],
        "unitResults": unit_results,
        "ownerships": [],
        "candidates": [row["match"] for row in unit_results if row.get("match")],
    }


def _present_rss_artifact_group_list(payload):
    value = dict(payload) if isinstance(payload, dict) else {}
    value["groups"] = [
        presented
        for presented in (
            _present_rss_artifact_group(group) for group in value.get("groups") or []
        )
        if presented is not None
    ]
    counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
    value["counts"] = {
        "total": max(0, int(counts.get("total") or 0)),
        "scoreableTotal": max(
            0, int(counts.get("total") or 0) - int(counts.get("needs_cleanup") or 0)
        ),
        "initialBest": max(0, int(counts.get("initial_best") or 0)),
        "waitingBaseline": max(0, int(counts.get("waiting_baseline") or 0)),
        "monitoringRss": max(0, int(counts.get("monitoring_rss") or 0)),
        "upgradeAvailable": max(0, int(counts.get("upgrade_available") or 0)),
        "partiallyBest": max(0, int(counts.get("partially_best") or 0)),
        "protected": max(0, int(counts.get("protected") or 0)),
        "needsCleanup": max(0, int(counts.get("needs_cleanup") or 0)),
        "blocked": max(0, int(counts.get("blocked") or 0)),
    }
    return value


def _present_cleanup_item(item):
    source = item if isinstance(item, dict) else {}
    subscription_id, unit_id = torra_public_match_keys(
        source.get("subscriptionId"), source.get("unitId")
    )
    return {
        "matchId": str(source.get("matchId") or "")[:80],
        "subscriptionId": subscription_id,
        "unitId": unit_id,
        "title": safe_public_text(source.get("title"))[:240],
        "version": max(1, int(source.get("version") or 1)),
        "reasonCode": str(source.get("reasonCode") or "subscription_missing")[:80],
    }


def _present_cleanup_preview(payload):
    source = payload if isinstance(payload, dict) else {}
    return {
        "id": str(source.get("id") or "")[:80],
        "status": str(source.get("status") or "previewed")[:40],
        "fingerprint": str(source.get("fingerprint") or "")[:64],
        "cleanupRuleVersion": str(source.get("cleanupRuleVersion") or "")[:80],
        "itemCount": max(0, int(source.get("itemCount") or 0)),
        "items": [_present_cleanup_item(row) for row in source.get("items") or []],
        "skipped": [{
            "matchId": str(row.get("matchId") or "")[:80],
            "reasonCode": str(row.get("reasonCode") or "")[:80],
        } for row in source.get("skipped") or [] if isinstance(row, dict)],
        "createdAt": str(source.get("createdAt") or "")[:80],
    }


def _present_cleanup_result(payload):
    source = payload if isinstance(payload, dict) else {}
    return {
        "id": str(source.get("id") or "")[:80],
        "status": str(source.get("status") or "")[:40],
        "fingerprint": str(source.get("fingerprint") or "")[:64],
        "archivedCount": max(0, int(source.get("archivedCount") or 0)),
        "archivedMatchIds": [
            str(value or "")[:80] for value in source.get("archivedMatchIds") or []
            if str(value or "")
        ],
        "appliedAt": str(source.get("appliedAt") or "")[:80],
    }


def _present_cleanup_run_list(payload):
    source = payload if isinstance(payload, dict) else {}
    return {
        "items": [{
            "id": str(row.get("id") or "")[:80],
            "status": str(row.get("status") or "")[:40],
            "fingerprint": str(row.get("fingerprint") or "")[:64],
            "itemCount": max(0, int(row.get("itemCount") or 0)),
            "archivedCount": max(0, int(row.get("archivedCount") or 0)),
            "items": [_present_cleanup_item(item) for item in row.get("items") or []],
            "createdAt": str(row.get("createdAt") or "")[:80],
            "confirmedAt": str(row.get("confirmedAt") or "")[:80],
            "updatedAt": str(row.get("updatedAt") or "")[:80],
        } for row in source.get("items") or [] if isinstance(row, dict)],
        "total": max(0, int(source.get("total") or 0)),
    }


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
    media_metadata_cache_loader=None,
):
    resolved_environment = os.environ if environment is None else environment
    repository = repository or PrivateRssRepository(
        database_path,
        media_metadata_cache_loader=media_metadata_cache_loader,
    )
    if media_metadata_cache_loader and hasattr(repository, "set_media_metadata_cache_loader"):
        repository.set_media_metadata_cache_loader(media_metadata_cache_loader)
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
            app.extensions.get("mcc_symedia_client"),
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
            published_from, published_before = _published_date_bounds(request.args.get("publishedDate"))
            payload = service.repository.search_items(
                query=request.args.get("query") or "",
                source_id=request.args.get("sourceId") or "",
                window_hours=window_hours,
                identity_status=request.args.get("identityStatus") or "",
                review_state=request.args.get("reviewState") or "",
                follow_state=request.args.get("followState") or "",
                published_from=published_from,
                published_before=published_before,
                limit=request.args.get("limit") or 50,
                offset=request.args.get("offset") or 0,
                subscription_id=request.args.get("subscriptionId") or "",
                tmdb_id=request.args.get("tmdbId") or "",
                media_type=request.args.get("mediaType") or "",
                season_number=request.args.get("seasonNumber") or None,
                episode_number=request.args.get("episodeNumber") or None,
                year=request.args.get("year") or "",
            )
        except (TypeError, ValueError):
            return _error("RSS_QUERY_INVALID", "资源中心查询参数无效", 422)
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
            view = str(request.args.get("view") or "").strip().lower()
            if view not in {"", "groups", "artifact-groups"}:
                raise ValueError
            if view == "artifact-groups":
                return jsonify(_present_rss_artifact_group_list(
                    service.repository.list_candidate_artifact_groups(
                        status=request.args.get("status") or "",
                        group_state=request.args.get("groupState") or "",
                        group_scope=request.args.get("groupScope") or "",
                        subscription_id=request.args.get("subscriptionId") or "",
                        media_type=request.args.get("mediaType") or "",
                        season_number=request.args.get("seasonNumber") or None,
                        episode_number=request.args.get("episodeNumber") or None,
                        match_id=request.args.get("matchId") or "",
                        item_id=request.args.get("itemId") or "",
                        limit=request.args.get("limit") or 20,
                        offset=request.args.get("offset") or 0,
                    )
                ))
            if view == "groups":
                return jsonify(_present_rss_match_group_list(
                    service.repository.list_candidate_groups(
                        status=request.args.get("status") or "",
                        group_state=request.args.get("groupState") or "",
                        group_scope=request.args.get("groupScope") or "",
                        subscription_id=request.args.get("subscriptionId") or "",
                        media_type=request.args.get("mediaType") or "",
                        season_number=request.args.get("seasonNumber") or None,
                        episode_number=request.args.get("episodeNumber") or None,
                        match_id=request.args.get("matchId") or "",
                        limit=request.args.get("limit") or 20,
                        offset=request.args.get("offset") or 0,
                    )
                ))
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

    @app.get("/api/v2/rss-match-cleanups")
    def rss_match_cleanups_list():
        try:
            payload = service.repository.list_match_cleanup_runs(
                limit=request.args.get("limit") or 20,
            )
        except (TypeError, ValueError):
            return _error("RSS_MATCH_CLEANUP_QUERY_INVALID", "RSS 清理审计查询参数无效", 400)
        return jsonify(_present_cleanup_run_list(payload))

    @app.get("/api/v2/rss-match-cleanups/<run_id>")
    def rss_match_cleanups_detail(run_id):
        run = service.repository.get_match_cleanup_run(run_id)
        if not run:
            return _error("RSS_MATCH_CLEANUP_NOT_FOUND", "RSS 清理记录不存在", 404)
        return jsonify(_present_cleanup_run_list({"items": [run], "total": 1})["items"][0])

    @app.post("/api/v2/rss-match-cleanups/previews")
    def rss_match_cleanups_preview():
        if not service.config_write_enabled():
            return _error("RSS_MATCH_CLEANUP_WRITE_DISABLED", "RSS 本地清理写入尚未启用", 503)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or set(body) - {"matchIds"} or not isinstance(body.get("matchIds"), list):
            return _error("RSS_MATCH_CLEANUP_PREVIEW_INVALID", "RSS 清理预览参数无效", 400)
        try:
            preview = service.repository.create_match_cleanup_preview(body.get("matchIds"))
        except ValueError:
            return _error("RSS_MATCH_CLEANUP_PREVIEW_INVALID", "RSS 清理预览参数无效", 400)
        except Exception:
            return _error("RSS_MATCH_CLEANUP_PREVIEW_FAILED", "RSS 清理预览生成失败", 500)
        response = jsonify(_present_cleanup_preview(preview))
        response.status_code = 201
        response.headers["Location"] = f"/api/v2/rss-match-cleanups/{preview['id']}"
        return response

    @app.post("/api/v2/rss-match-cleanups")
    def rss_match_cleanups_apply():
        if not service.config_write_enabled():
            return _error("RSS_MATCH_CLEANUP_WRITE_DISABLED", "RSS 本地清理写入尚未启用", 503)
        body = request.get_json(silent=True) or {}
        expected_fields = {"previewId", "fingerprint", "matchIds", "idempotencyKey"}
        if (
            not isinstance(body, dict)
            or set(body) != expected_fields
            or not isinstance(body.get("matchIds"), list)
        ):
            return _error("RSS_MATCH_CLEANUP_INVALID", "RSS 清理确认参数无效", 400)
        try:
            result = service.repository.apply_match_cleanup(
                preview_id=body.get("previewId"),
                fingerprint=body.get("fingerprint"),
                match_ids=body.get("matchIds"),
                idempotency_key=body.get("idempotencyKey"),
            )
        except ValueError:
            return _error("RSS_MATCH_CLEANUP_INVALID", "RSS 清理确认参数无效", 400)
        except KeyError:
            return _error("RSS_MATCH_CLEANUP_NOT_FOUND", "RSS 清理预览不存在", 404)
        except RssMatchCleanupStale:
            return _error("RSS_MATCH_CLEANUP_PREVIEW_STALE", "RSS 清理预览已过期，请重新预览", 409)
        except RssMatchCleanupConflict:
            return _error("RSS_MATCH_CLEANUP_CONFLICT", "RSS 清理确认发生冲突", 409)
        except Exception:
            return _error("RSS_MATCH_CLEANUP_FAILED", "RSS 清理执行失败", 500)
        public_result = _present_cleanup_result(result)
        write_activity(
            "rss",
            "match_cleanup",
            "success",
            "RSS 失效匹配已归档",
            archivedCount=public_result["archivedCount"],
        )
        return jsonify(public_result)

    return service
