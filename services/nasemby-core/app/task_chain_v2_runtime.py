from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request

from app.http_runtime import current_request_id
from app.resource_identity_runtime import artifact_key, chain_id, media_key, target_key
from app.task_exception_runtime import classify_stage, classify_task


HEALTH_PRIORITY = {
    "action_required": 0,
    "evidence_insufficient": 1,
    "waiting": 2,
    "protected": 3,
    "normal": 4,
}
STATE_PRIORITY = {"blocked": 0, "active": 1, "waiting": 2, "completed": 3}
EVIDENCE_PRIORITY = {"verified": 0, "inferred": 1, "missing": 2}
STATUS_PRIORITY = {"blocked": 0, "active": 1, "waiting": 2, "unknown": 3, "done": 4}
ORIGIN_PRIORITY = {"subscription": 0, "download": 1, "library": 2}
CONFIDENCE_PRIORITY = {"strong": 0, "fallback": 1, "unlinked": 2}
IDENTITY_STATES = ("unidentified", "linked", "conflict")
EXECUTION_STATES = ("normal", "waiting", "protected", "suspected_blocked", "action_required", "confirmed_failed")
EXECUTION_PRIORITY = {state: index for index, state in enumerate((
    "confirmed_failed", "action_required", "suspected_blocked", "waiting", "protected", "normal",
))}
STAGE_ORDER = {
    name: index
    for index, name in enumerate((
        "subscription",
        "resource",
        "download",
        "cloud115",
        "symedia",
        "strm",
        "library",
        "emby",
        "identity",
    ))
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _health(item: dict, observed_at: str, fresh_until: str, now=None) -> dict:
    return classify_task(item, now=now, observed_at=observed_at, fresh_until=fresh_until)


def _stage(step: dict, observed_at: str, fresh_until: str, now=None) -> dict:
    status = str(step.get("status") or "unknown")
    result = {
        "stage": str(step.get("key") or step.get("stage") or "unknown"),
        "label": str(step.get("label") or "未命名阶段"),
        "status": status,
        "healthState": "evidence_insufficient",
        "evidence": str(step.get("evidence") or "missing"),
        "observedAt": str(step.get("timestamp") or step.get("observedAt") or observed_at),
        "freshUntil": str(step.get("freshUntil") or fresh_until),
        "source": str(step.get("source") or ""),
        "reasonCode": str(step.get("reasonCode") or f"{str(step.get('key') or step.get('stage') or 'task').upper()}_{status.upper()}"),
        "reasonText": str(step.get("detail") or step.get("reasonText") or ""),
        "matchedProtectionRule": str(step.get("matchedProtectionRule") or ""),
        "protectionRules": list(step.get("protectionRules") or []),
        "actions": {"preview": False, "retry": False},
    }
    result.update(classify_stage(result, now=now, observed_at=observed_at, fresh_until=fresh_until))
    result["actions"] = dict(step.get("actions") or {"preview": False, "retry": False})
    result["actions"].setdefault("preview", False)
    result["actions"].setdefault("retry", False)
    return result


def _adapt_item(item: dict, observed_at: str, fresh_until: str, now_value: datetime) -> dict:
    media = media_key(item.get("mediaType"), item.get("tmdbId"), item.get("title"))
    target = target_key(
        item.get("mediaType"),
        item.get("tmdbId"),
        item.get("title"),
        item.get("seasonNumber", 0),
        item.get("episodeNumber"),
    )
    source_ids = item.get("sourceIds") or {}
    artifact_keys = [artifact_key(qb_hash=value) for value in source_ids.get("qbHashes") or []]
    artifact_keys.extend(artifact_key(remote_file_id=value) for value in source_ids.get("symediaIds") or [])
    artifact_keys = sorted(set(artifact_keys))
    stages = [_stage(step, observed_at, fresh_until, now_value) for step in item.get("steps") or item.get("stages") or []]
    confidence = str(item.get("confidence") or ("strong" if item.get("tmdbId") else "unlinked"))
    normalized = {**item, "confidence": confidence, "stages": stages}
    item_health = _health(normalized, observed_at, fresh_until, now_value)
    return {
        **normalized,
        "chainId": chain_id(media, target, artifact_keys),
        "mediaKey": media,
        "targetKey": target,
        "artifactKeys": artifact_keys,
        "subscriptionId": str(source_ids.get("subscriptionId") or ""),
        **item_health,
        "stages": stages,
    }


def _stage_rank(stage: dict):
    return (
        HEALTH_PRIORITY.get(str(stage.get("healthState") or ""), len(HEALTH_PRIORITY)),
        EVIDENCE_PRIORITY.get(str(stage.get("evidence") or ""), len(EVIDENCE_PRIORITY)),
        STATUS_PRIORITY.get(str(stage.get("status") or ""), len(STATUS_PRIORITY)),
    )


def _merge_stage(candidates: list[dict]) -> dict:
    best_rank = min(_stage_rank(stage) for stage in candidates)
    ranked = [stage for stage in candidates if _stage_rank(stage) == best_rank]
    return max(ranked, key=lambda stage: str(stage.get("observedAt") or ""))


def _dedupe(values) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _normalize_title(value) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _source_ids(items: list[dict]) -> dict:
    source_rows = [item.get("sourceIds") or {} for item in items]
    subscription_ids = _dedupe(
        value
        for row in source_rows
        for value in [row.get("subscriptionId"), *(row.get("subscriptionIds") or [])]
    )
    torra_ids = _dedupe(
        value
        for row in source_rows
        for value in [row.get("torraId"), *(row.get("torraIds") or [])]
    )
    qb_hashes = _dedupe(value for row in source_rows for value in row.get("qbHashes") or [])
    symedia_ids = _dedupe(value for row in source_rows for value in row.get("symediaIds") or [])
    return {
        "subscriptionId": subscription_ids[0] if subscription_ids else "",
        "subscriptionIds": subscription_ids,
        "torraId": torra_ids[0] if torra_ids else "",
        "torraIds": torra_ids,
        "qbHashes": qb_hashes,
        "symediaIds": symedia_ids,
    }


def _episode_evidence(items: list[dict]) -> list[dict]:
    merged = {}
    for item in items:
        for row in item.get("episodeEvidence") or []:
            if not isinstance(row, dict):
                continue
            key = (
                int(row.get("seasonNumber") or 0),
                int(row.get("episodeStart") or 0),
                int(row.get("episodeEnd") or 0),
                str(row.get("numberingScheme") or ""),
                str(row.get("stage") or ""),
                str(row.get("artifactKey") or ""),
            )
            current = merged.get(key)
            if current is None or str(row.get("observedAt") or "") >= str(current.get("observedAt") or ""):
                merged[key] = dict(row)
    return [merged[key] for key in sorted(merged)]


def _primary_item(items: list[dict]) -> dict:
    return min(
        items,
        key=lambda item: (
            ORIGIN_PRIORITY.get(str(item.get("origin") or ""), len(ORIGIN_PRIORITY)),
            CONFIDENCE_PRIORITY.get(str(item.get("confidence") or ""), len(CONFIDENCE_PRIORITY)),
            -len(str(item.get("title") or "")),
        ),
    )


def _merged_state(stages: list[dict]) -> str:
    statuses = {str(stage.get("status") or "unknown") for stage in stages}
    if "blocked" in statuses:
        return "blocked"
    if "active" in statuses:
        return "active"
    if statuses & {"waiting", "unknown"}:
        return "waiting"
    return "completed" if statuses and statuses == {"done"} else "waiting"


def _merged_steps(stages: list[dict]) -> list[dict]:
    return [{
        "key": stage.get("stage"),
        "label": stage.get("label"),
        "status": stage.get("status"),
        "evidence": stage.get("evidence"),
        "detail": stage.get("reasonText"),
        "timestamp": stage.get("observedAt"),
        "source": stage.get("source"),
    } for stage in stages]


def _chain_progress(stages: list[dict], items: list[dict]) -> int:
    if not stages:
        return max((int(item.get("progress") or 0) for item in items), default=0)
    weights = {"done": 1.0, "active": 0.5}
    completed = sum(weights.get(str(stage.get("status") or "unknown"), 0.0) for stage in stages)
    return round(completed / len(stages) * 100)


def _merge_group(items: list[dict], observed_at: str, fresh_until: str, now_value: datetime) -> dict:
    primary = dict(_primary_item(items))
    source_ids = _source_ids(items)
    stage_groups = {}
    for item in items:
        for stage in item.get("stages") or []:
            stage_groups.setdefault(str(stage.get("stage") or "unknown"), []).append(stage)
    stages = [
        _merge_stage(candidates)
        for _, candidates in sorted(stage_groups.items(), key=lambda row: (STAGE_ORDER.get(row[0], 100), row[0]))
    ]
    artifacts = _dedupe(value for item in items for value in item.get("artifactKeys") or [])
    episode_evidence = _episode_evidence(items)
    state = _merged_state(stages)
    confidence = min(
        (str(item.get("confidence") or "unlinked") for item in items),
        key=lambda value: CONFIDENCE_PRIORITY.get(value, len(CONFIDENCE_PRIORITY)),
    )
    merged = {
        **primary,
        "id": str(primary.get("id") or primary["chainId"]),
        "state": state,
        "confidence": confidence,
        "progress": _chain_progress(stages, items),
        "embyIndexed": any(bool(item.get("embyIndexed")) for item in items),
        "sourceIds": source_ids,
        "subscriptionId": source_ids["subscriptionId"],
        "artifactKeys": artifacts,
        "episodeEvidence": episode_evidence,
        "origins": _dedupe(item.get("origin") for item in items),
        "relatedRecords": len(items),
        "updatedAt": max((str(item.get("updatedAt") or "") for item in items), default=""),
        "stages": stages,
        "steps": _merged_steps(stages),
        "qbControl": {
            "total": len(source_ids["qbHashes"]),
            "paused": max((int((item.get("qbControl") or {}).get("paused") or 0) for item in items), default=0),
            "canPause": any(bool((item.get("qbControl") or {}).get("canPause")) for item in items),
            "canResume": any(bool((item.get("qbControl") or {}).get("canResume")) for item in items),
        },
    }
    merged.update(_health(merged, observed_at, fresh_until, now_value))
    return merged


def _counts(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "active": sum(item.get("state") == "active" for item in items),
        "blocked": sum(item.get("state") == "blocked" for item in items),
        "completed": sum(item.get("state") == "completed" for item in items),
        "waiting": sum(item.get("state") == "waiting" for item in items),
        "unlinked": sum(item.get("confidence") == "unlinked" for item in items),
    }


def _stage_counts(items: list[dict]) -> dict:
    result = {}
    for item in items:
        for stage in item.get("stages") or []:
            name = str(stage.get("stage") or "unknown")
            status = str(stage.get("status") or "unknown")
            result.setdefault(name, {})[status] = result.setdefault(name, {}).get(status, 0) + 1
    return result


def adapt_task_chain(chain: dict, *, now: datetime | None = None, health_filter: str = "") -> dict:
    now_value = now or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    observed_at = _iso(now_value)
    fresh_until = _iso(now_value + timedelta(minutes=5))
    grouped = {}
    for item in chain.get("items") or []:
        if not isinstance(item, dict):
            continue
        adapted = _adapt_item(item, observed_at, fresh_until, now_value)
        grouped.setdefault(adapted["chainId"], []).append(adapted)
    all_items = [_merge_group(items, observed_at, fresh_until, now_value) for items in grouped.values()]
    all_items.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    all_items.sort(key=lambda item: (
        HEALTH_PRIORITY.get(str(item.get("healthState") or ""), len(HEALTH_PRIORITY)),
        EXECUTION_PRIORITY.get(str(item.get("executionState") or ""), len(EXECUTION_PRIORITY)),
    ))
    health_counts = {
        state: sum(item.get("healthState") == state for item in all_items)
        for state in HEALTH_PRIORITY
    }
    identity_counts = {
        state: sum(item.get("identityState") == state for item in all_items)
        for state in IDENTITY_STATES
    }
    execution_counts = {
        state: sum(item.get("executionState") == state for item in all_items)
        for state in EXECUTION_STATES
    }
    items = [
        item for item in all_items
        if not health_filter or item.get("healthState") == health_filter
    ]
    return {
        **chain,
        "items": items,
        "counts": _counts(all_items),
        "originCounts": {
            origin: sum(origin in (item.get("origins") or [item.get("origin")]) for item in all_items)
            for origin in ("subscription", "download", "library")
        },
        "stageCounts": _stage_counts(all_items),
        "healthCounts": health_counts,
        "identityCounts": identity_counts,
        "executionCounts": execution_counts,
        "generatedAt": str(chain.get("generatedAt") or observed_at),
        "contractVersion": 2,
    }


def _summary_item(item: dict) -> dict:
    fields = (
        "id", "title", "mediaType", "tmdbId", "seasonNumber", "episodeNumber", "posterUrl",
        "origin", "origins", "channel", "state", "confidence", "progress", "currentStep",
        "embyIndexed", "qbControl", "acquisition", "updatedAt", "chainId", "mediaKey",
        "targetKey", "subscriptionId", "healthState", "observedAt", "freshUntil", "source",
        "reasonCode", "reasonText", "userReasonText", "recommendedAction", "retryEligible", "plannedRetryAt",
        "identityState", "executionState",
        "relatedRecords",
    )
    result = {field: item.get(field) for field in fields if field in item}
    result["stageSummary"] = [{
        "stage": stage.get("stage"),
        "label": stage.get("label"),
        "status": stage.get("status"),
        "healthState": stage.get("healthState"),
    } for stage in item.get("stages") or []]
    return result


def _version(payload: dict) -> str:
    stable = {
        "counts": payload.get("counts") or {},
        "healthCounts": payload.get("healthCounts") or {},
        "identityCounts": payload.get("identityCounts") or {},
        "executionCounts": payload.get("executionCounts") or {},
        "originCounts": payload.get("originCounts") or {},
        "stageCounts": payload.get("stageCounts") or {},
        "services": payload.get("services") or {},
        "items": [{
            "chainId": item.get("chainId"),
            "updatedAt": item.get("updatedAt"),
            "state": item.get("state"),
            "healthState": item.get("healthState"),
            "identityState": item.get("identityState"),
            "executionState": item.get("executionState"),
            "reasonCode": item.get("reasonCode"),
            "artifactKeys": item.get("artifactKeys") or [],
            "episodeEvidence": [{
                "seasonNumber": row.get("seasonNumber"),
                "episodeStart": row.get("episodeStart"),
                "episodeEnd": row.get("episodeEnd"),
                "numberingScheme": row.get("numberingScheme"),
                "stage": row.get("stage"),
                "artifactKey": row.get("artifactKey"),
                "status": row.get("status"),
                "reasonCode": row.get("reasonCode"),
                "observedAt": row.get("observedAt"),
            } for row in item.get("episodeEvidence") or []],
            "stages": [{
                "stage": stage.get("stage"),
                "status": stage.get("status"),
                "healthState": stage.get("healthState"),
                "evidence": stage.get("evidence"),
                "reasonCode": stage.get("reasonCode"),
                "reasonText": stage.get("reasonText"),
                "userReasonText": stage.get("userReasonText"),
            } for stage in item.get("stages") or []],
        } for item in payload.get("items") or []],
    }
    content = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


class TaskChainV2Service:
    def __init__(self, app: Flask, repository=None, clock=None, cache_seconds=45):
        self.app = app
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache_seconds = max(1, int(cache_seconds))
        self._cache = None
        self._cache_at = 0.0
        self._lock = threading.RLock()

    def full_snapshot(self, *, force=False):
        with self._lock:
            if not force and self._cache and time.monotonic() - self._cache_at < self.cache_seconds:
                return self._cache
            service = self.app.extensions.get("mcc_task_chain_service")
            if not service:
                raise RuntimeError("任务链尚未注册")
            payload = adapt_task_chain(service.get_chain(), now=self.clock())
            if self.repository:
                payload["ledger"] = self.repository.record_snapshot(payload)
            payload["version"] = _version(payload)
            self._cache = payload
            self._cache_at = time.monotonic()
            return payload

    def snapshot(self, health_filter=""):
        payload = self.full_snapshot(force=True)
        if not health_filter:
            return payload
        return {
            **payload,
            "items": [
                item for item in payload.get("items") or []
                if item.get("healthState") == health_filter
            ],
        }

    def summary(self, *, force=False):
        payload = self.full_snapshot(force=force)
        return {
            key: payload.get(key)
            for key in (
                "contractVersion", "generatedAt", "version", "counts", "healthCounts",
                "identityCounts", "executionCounts", "originCounts", "stageCounts",
                "services", "ledger",
            )
            if key in payload
        }

    def list_items(
        self,
        *,
        health_state="",
        identity_state="",
        execution_state="",
        chain_id_value="",
        target_key_value="",
        subscription_id="",
        tmdb_id="",
        title="",
        season_number=None,
        updated_after=None,
        offset=0,
        limit=20,
        force=False,
    ):
        payload = self.full_snapshot(force=force)
        items = payload.get("items") or []
        if health_state:
            items = [item for item in items if item.get("healthState") == health_state]
        if identity_state:
            items = [item for item in items if item.get("identityState") == identity_state]
        if execution_state:
            items = [item for item in items if item.get("executionState") == execution_state]
        if chain_id_value:
            items = [item for item in items if item.get("chainId") == chain_id_value]
        if target_key_value:
            items = [item for item in items if item.get("targetKey") == target_key_value]
        if subscription_id:
            items = [
                item for item in items
                if subscription_id in {
                    str(item.get("subscriptionId") or ""),
                    *(str(value) for value in (item.get("sourceIds") or {}).get("subscriptionIds") or []),
                }
            ]
        if tmdb_id:
            items = [item for item in items if str(item.get("tmdbId") or "") == tmdb_id]
        if season_number is not None:
            items = [item for item in items if int(item.get("seasonNumber") or 0) == season_number]
        if title:
            wanted = _normalize_title(title)
            items = [
                item for item in items
                if wanted and (
                    wanted in _normalize_title(item.get("title"))
                    or _normalize_title(item.get("title")) in wanted
                )
            ]
        if updated_after:
            items = [
                item for item in items
                if (parsed := _parse_datetime(item.get("updatedAt"))) and parsed > updated_after
            ]
        total = len(items)
        page = items[offset:offset + limit]
        return {
            **self.summary(force=False),
            "items": [_summary_item(item) for item in page],
            "page": {
                "total": total,
                "offset": offset,
                "limit": limit,
                "nextOffset": offset + len(page) if offset + len(page) < total else None,
                "hasMore": offset + len(page) < total,
            },
        }

    def detail(self, chain_id_value: str, *, force=False):
        payload = self.full_snapshot(force=force)
        item = next((
            item for item in payload.get("items") or []
            if item.get("chainId") == chain_id_value
        ), None)
        return {
            **self.summary(force=False),
            "item": item,
        }


def _error(code, message, status):
    return jsonify({
        "code": code,
        "error": message,
        "request_id": current_request_id(),
    }), status


def _integer_query(name, default, minimum, maximum):
    raw = request.args.get(name)
    if raw in {None, ""}:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(name)
    if value < minimum or value > maximum:
        raise ValueError(name)
    return value


def _conditional(payload, scope):
    version = str(payload.get("version") or "")
    etag = hashlib.sha256(f"{version}|{scope}".encode("utf-8")).hexdigest()[:24] if version else ""
    if etag and request.if_none_match.contains(etag):
        response = Response(status=304)
    else:
        response = jsonify(payload)
    if etag:
        response.set_etag(etag)
    response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
    return response


def register_task_chain_v2(app: Flask, repository=None, clock=None):
    service = TaskChainV2Service(app, repository=repository, clock=clock)
    app.extensions["mcc_task_chain_v2_service"] = service

    @app.get("/api/v2/tasks/summary")
    def task_summary_v2():
        try:
            return _conditional(service.summary(force=request.args.get("refresh") == "1"), "summary")
        except Exception:
            return _error("TASK_SUMMARY_V2_READ_FAILED", "任务摘要读取失败", 502)

    @app.get("/api/v2/tasks/chains")
    def task_chains_v2():
        health_state = str(request.args.get("healthState") or request.args.get("health") or "").strip()
        allowed = set(HEALTH_PRIORITY)
        if health_state and health_state not in allowed:
            return _error("TASK_HEALTH_FILTER_INVALID", "健康状态筛选无效", 400)
        identity_state = str(request.args.get("identityState") or "").strip()
        if identity_state and identity_state not in IDENTITY_STATES:
            return _error("TASK_IDENTITY_FILTER_INVALID", "身份状态筛选无效", 400)
        execution_state = str(request.args.get("executionState") or "").strip()
        if execution_state and execution_state not in EXECUTION_STATES:
            return _error("TASK_EXECUTION_FILTER_INVALID", "执行状态筛选无效", 400)
        try:
            offset = _integer_query("offset", 0, 0, 1_000_000)
            limit = _integer_query("limit", 20, 1, 100)
            season_number = _integer_query("seasonNumber", None, 0, 10_000)
        except ValueError:
            return _error("TASK_PAGINATION_INVALID", "任务分页参数无效", 400)
        updated_after = None
        if request.args.get("updatedAfter"):
            updated_after = _parse_datetime(request.args.get("updatedAfter"))
            if updated_after is None:
                return _error("TASK_UPDATED_AFTER_INVALID", "任务增量时间无效", 400)
        try:
            payload = service.list_items(
                health_state=health_state,
                identity_state=identity_state,
                execution_state=execution_state,
                chain_id_value=str(request.args.get("chainId") or "").strip(),
                target_key_value=str(request.args.get("targetKey") or "").strip(),
                subscription_id=str(request.args.get("subscriptionId") or "").strip(),
                tmdb_id=str(request.args.get("tmdbId") or "").strip(),
                title=str(request.args.get("title") or "").strip(),
                season_number=season_number,
                updated_after=updated_after,
                offset=offset,
                limit=limit,
                force=request.args.get("refresh") == "1",
            )
            scope = request.query_string.decode("utf-8", errors="replace") or "default"
            return _conditional(payload, f"list:{scope}")
        except Exception:
            return _error("TASK_CHAIN_V2_READ_FAILED", "任务链读取失败", 502)

    @app.get("/api/v2/tasks/chains/<path:chain_id_value>")
    def task_chain_detail_v2(chain_id_value):
        try:
            payload = service.detail(chain_id_value, force=request.args.get("refresh") == "1")
            if payload.get("item") is None:
                return _error("TASK_CHAIN_NOT_FOUND", "任务链不存在", 404)
            return _conditional(payload, f"detail:{chain_id_value}")
        except Exception:
            return _error("TASK_CHAIN_V2_READ_FAILED", "任务链读取失败", 502)

    return service
