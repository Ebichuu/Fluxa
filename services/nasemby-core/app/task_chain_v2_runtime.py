from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request

from app.http_runtime import current_request_id
from app.pipeline_fact_runtime import (
    merge_pipeline_facts,
    normalize_pipeline_fact,
    target_scope_for_item,
)
from app.pipeline_outcome_runtime import PIPELINE_OUTCOMES, derive_outcome_counts, derive_pipeline_outcome
from app.resource_identity_runtime import artifact_key, chain_id, media_key, target_key
from app.resource_task_repository import pipeline_source_ref, pipeline_unit_ref
from app.task_exception_runtime import classify_stage, classify_task
from app.task_public_runtime import (
    present_migration_preview,
    present_services,
    present_system_issues,
    present_task_item,
    public_subscription_ref,
)


HEALTH_PRIORITY = {
    "action_required": 0,
    "evidence_insufficient": 1,
    "waiting": 2,
    "protected": 3,
    "normal": 4,
}
USER_STATES = ("action_required", "in_progress", "completed", "no_action")
USER_STATE_PRIORITY = {state: index for index, state in enumerate(USER_STATES)}
BEIJING_TZ = timezone(timedelta(hours=8))
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
    technical_reason = str(step.get("technicalReasonText") or step.get("detail") or step.get("reasonText") or "")
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
        "reasonText": str(step.get("detail") or step.get("reasonText") or step.get("userReasonText") or ""),
        "technicalReasonText": technical_reason,
        "matchedProtectionRule": str(step.get("matchedProtectionRule") or ""),
        "protectionRules": list(step.get("protectionRules") or []),
        "actions": {"preview": False, "retry": False},
    }
    result.update(classify_stage(result, now=now, observed_at=observed_at, fresh_until=fresh_until))
    result["actions"] = dict(step.get("actions") or {"preview": False, "retry": False})
    result["actions"].setdefault("preview", False)
    result["actions"].setdefault("retry", False)
    return result


def _mark_unlinked_upstream_stages(stages: list[dict]) -> list[dict]:
    """下游阶段已有完成证据时，缺少直接证据的上游阶段只标记"未关联到对应证据"。

    不再使用"未正常继续"、"刷新来源"等失败/故障文案；健康态保持 evidence_insufficient，
    也不据下游成功反推上游具体执行方式。真正有失败证据的阶段（action_required）不受影响。
    """
    completed_indexes = [
        index for index, stage in enumerate(stages)
        if str(stage.get("status") or "") == "done"
        and str(stage.get("evidence") or "") in {"verified", "inferred"}
        and str(stage.get("healthState") or "") == "normal"
    ]
    if not completed_indexes:
        return stages
    last_completed = max(completed_indexes)
    for index, stage in enumerate(stages):
        if (
            index < last_completed
            and str(stage.get("healthState") or "") == "evidence_insufficient"
            and str(stage.get("evidence") or "") == "missing"
        ):
            stage.update({
                "reasonCode": "STAGE_EVIDENCE_NOT_LINKED",
                "reasonText": "未关联到对应证据",
                "userReasonText": "未关联到对应证据",
                "recommendedAction": "下游阶段已有完成证据，无需在此处理",
            })
    return stages


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
    pipeline_facts = [
        normalize_pipeline_fact(fact)
        for fact in item.get("pipelineFacts") or []
    ]
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
        "pipelineFacts": pipeline_facts,
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
                str(row.get("ownerTargetKey") or ""),
            )
            current = merged.get(key)
            if current is None or str(row.get("observedAt") or "") >= str(current.get("observedAt") or ""):
                merged[key] = dict(row)
    return [merged[key] for key in sorted(merged)]


def _evidence_ownership(items: list[dict]) -> list[dict]:
    merged = {}
    for item in items:
        for row in item.get("evidenceOwnership") or []:
            if not isinstance(row, dict):
                continue
            key = (
                str(row.get("artifactKey") or ""),
                str(row.get("ownerTargetKey") or ""),
                str(row.get("matchMethod") or ""),
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


def _confirmed_stage_count(facts: list[dict]) -> int:
    return sum(
        str(fact.get("state") or "unknown") != "unknown"
        and str(fact.get("evidence") or "missing") != "missing"
        for fact in facts
        if isinstance(fact, dict)
    )


class ArchiveSourceUnavailable(RuntimeError):
    pass


def _archive_date_key(value) -> str:
    parsed = _parse_datetime(value)
    return parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d") if parsed else ""


def _historical_archive_files(repository, archived_date):
    history_reader = getattr(repository, "list_symedia_archive_events", None) if repository else None
    if not callable(history_reader):
        return []
    return [
        (
            str(event.get("fileKey") or ""),
            str(event.get("chainId") or ""),
            str(event.get("eventAt") or ""),
        )
        for event in history_reader(archived_date)
        if isinstance(event, dict)
    ]


def _current_archive_files(payload, archived_date, repository):
    result = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        chain_id_value = str(item.get("chainId") or "")
        canonical_chain_id = (
            repository.resolve_chain_id(chain_id_value)
            if repository and chain_id_value
            else chain_id_value
        )
        linked_chain = canonical_chain_id if (
            canonical_chain_id
            and str(item.get("confidence") or "unlinked") in {"strong", "fallback"}
            and str(item.get("identityState") or "unidentified") == "linked"
        ) else ""
        fact = next((
            row for row in item.get("pipelineFacts") or []
            if isinstance(row, dict) and row.get("stage") == "symedia"
        ), None)
        for unit in (fact or {}).get("units") or []:
            if not isinstance(unit, dict):
                continue
            if unit.get("state") != "succeeded" or unit.get("evidence") != "verified":
                continue
            event_at = str(unit.get("eventAt") or fact.get("eventAt") or "")
            if _archive_date_key(event_at) != archived_date:
                continue
            source_ref = str(unit.get("sourceRef") or "").strip()
            unit_key = str(unit.get("unitKey") or "").strip()
            raw_identity = source_ref or unit_key
            if not raw_identity or raw_identity.startswith("row-"):
                continue
            file_key = pipeline_source_ref("symedia", source_ref) if source_ref else pipeline_unit_ref(unit_key)
            result.append((file_key, linked_chain, event_at))
    return result


def _archive_projection(payload: dict, archived_date: str, repository=None) -> dict:
    service = ((payload.get("services") or {}).get("symedia") or {})
    files = {}

    def add_file(file_key, owner="", event_at=""):
        if not file_key:
            return
        record = files.setdefault(file_key, {"owners": set(), "eventAt": event_at})
        if owner:
            record["owners"].add(owner)
        if event_at and (not record["eventAt"] or event_at < record["eventAt"]):
            record["eventAt"] = event_at

    for file_key, owner, event_at in [
        *_historical_archive_files(repository, archived_date),
        *_current_archive_files(payload, archived_date, repository),
    ]:
        add_file(file_key, owner, event_at)

    if not files and service.get("connected") is not True:
        raise ArchiveSourceUnavailable("Symedia 归档数据源暂不可用")

    linked_chain_ids = {
        next(iter(record["owners"]))
        for record in files.values()
        if len(record["owners"]) == 1
    }
    linked_files = sum(
        len(record["owners"]) == 1
        for record in files.values()
    )
    archived_files = len(files)
    return {
        "summary": {
            "date": archived_date,
            "timezone": "Asia/Shanghai",
            "archivedFiles": archived_files,
            "linkedFiles": linked_files,
            "linkedTasks": len(linked_chain_ids),
            "unlinkedFiles": archived_files - linked_files,
        },
        "chainIds": linked_chain_ids,
    }


def _stage_by_name(item: dict, *names: str) -> dict:
    wanted = set(names)
    return next((
        stage for stage in item.get("stages") or []
        if str(stage.get("stage") or stage.get("key") or "") in wanted
    ), {})


def _subscription_keys(*values) -> set[str]:
    keys = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        keys.add(raw)
        public = public_subscription_ref(raw)
        if public:
            keys.add(public)
    return keys


def _stage_is_current(stage: dict, *health_states: str) -> bool:
    return str(stage.get("healthState") or "") in health_states


def _has_current_stage(item: dict, *names: str) -> bool:
    wanted = set(names)
    return any(
        str(stage.get("stage") or stage.get("key") or "") in wanted
        and str(stage.get("evidence") or "") in {"verified", "inferred"}
        and _stage_is_current(stage, "normal", "waiting", "protected", "action_required")
        for stage in item.get("stages") or []
        if isinstance(stage, dict)
    )


def _legacy_user_state(outcome_state: str) -> str:
    return {
        "action_required": "action_required",
        "in_progress": "in_progress",
        "playable": "completed",
    }.get(outcome_state, "no_action")


def _outcome_text(outcome: dict) -> str:
    state = str(outcome.get("state") or "evidence_insufficient")
    if state == "playable":
        return "已可播放"
    if state == "protected":
        return str(outcome.get("reasonText") or "已按保护规则保留现有版本")
    return str(outcome.get("reasonText") or {
        "action_required": "当前任务需要处理",
        "in_progress": "正在处理",
        "waiting": "等待下一阶段",
        "evidence_insufficient": "暂未确认，暂不判断完成",
    }.get(state, "状态待确认"))


def _primary_action(item: dict, services: dict, outcome: dict) -> dict:
    outcome_state = str(outcome.get("state") or "evidence_insufficient")
    qb_control = item.get("qbControl") or {}
    if outcome_state == "in_progress" and qb_control.get("canPause"):
        return {"kind": "pause_download", "label": "暂停下载", "available": True, "reason": "qB 任务正在下载"}
    if outcome_state != "action_required":
        return {"kind": "none", "label": "", "available": False, "reason": "当前无需人工处理"}

    if qb_control.get("canResume"):
        return {"kind": "resume_download", "label": "恢复下载", "available": True, "reason": "qB 任务当前可恢复"}

    stage_name = str(outcome.get("stage") or "")
    if stage_name == "qb" and str(((services.get("qb") or {}).get("webUrl") or "")):
        return {"kind": "open_qb", "label": "打开 qB 检查", "available": True, "reason": "需要在下载器中确认文件或任务状态"}
    if stage_name in {"torra", "cloud115"} and str(((services.get("torra") or {}).get("webUrl") or "")):
        return {"kind": "open_torra", "label": "打开 Torra 检查", "available": True, "reason": "需要核对 Torra 获取或秒传状态"}
    if stage_name in {"symedia", "strm", "emby"}:
        return {"kind": "view_details", "label": "查看入库失败原因", "available": True, "reason": "Fluxa 已保留安全诊断信息"}
    return {"kind": "view_details", "label": "查看处理方法", "available": True, "reason": "当前没有可直接安全执行的自动动作"}


def _apply_user_projection(item: dict, services: dict) -> dict:
    outcome = item.get("pipelineOutcome") or {}
    outcome_state = str(outcome.get("state") or "evidence_insufficient")
    user_state = _legacy_user_state(outcome_state)
    playable_at = str(outcome.get("playableAt") or "")
    return {
        **item,
        "outcomeState": outcome_state,
        "playableAt": playable_at,
        "userState": user_state,
        "resultText": _outcome_text(outcome),
        "completedAt": playable_at if user_state == "completed" else "",
        "primaryAction": _primary_action(item, services, outcome),
    }


def _refresh_pipeline_projections(payload: dict, now_value: datetime) -> dict:
    services = payload.get("services") or {}
    items = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        item["pipelineOutcome"] = derive_pipeline_outcome(
            item.get("pipelineFacts") or [],
            target_scope=target_scope_for_item(item),
            target_unit_key=str(item.get("targetUnitKey") or ""),
            now=now_value,
        )
        items.append(_apply_user_projection(item, services))
    payload["items"] = items
    payload["counts"] = _counts(items)
    payload["userCounts"] = {
        state: sum(item.get("userState") == state for item in items)
        for state in USER_STATES
    }
    payload["outcomeCounts"] = derive_outcome_counts(items)
    return payload


def _merge_group(items: list[dict], observed_at: str, fresh_until: str, now_value: datetime) -> dict:
    primary = dict(_primary_item(items))
    source_ids = _source_ids(items)
    stage_groups = {}
    for item in items:
        for stage in item.get("stages") or []:
            stage_groups.setdefault(str(stage.get("stage") or "unknown"), []).append(stage)
    stages = _mark_unlinked_upstream_stages([
        _merge_stage(candidates)
        for _, candidates in sorted(stage_groups.items(), key=lambda row: (STAGE_ORDER.get(row[0], 100), row[0]))
    ])
    artifacts = _dedupe(value for item in items for value in item.get("artifactKeys") or [])
    episode_evidence = _episode_evidence(items)
    pipeline_facts = merge_pipeline_facts(
        [fact for item in items for fact in item.get("pipelineFacts") or []],
        target_scope=target_scope_for_item(primary),
        observed_at=observed_at,
        now=now_value,
    )
    pipeline_outcome = derive_pipeline_outcome(
        pipeline_facts,
        target_scope=target_scope_for_item(primary),
        target_unit_key=str(primary.get("targetUnitKey") or ""),
        now=now_value,
    )
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
        "embyEvidenceScope": (
            "episode" if any(item.get("embyEvidenceScope") == "episode" for item in items)
            else "title" if any(bool(item.get("embyIndexed")) for item in items)
            else "none"
        ),
        "sourceIds": source_ids,
        "subscriptionId": source_ids["subscriptionId"],
        "artifactKeys": artifacts,
        "evidenceOwnership": _evidence_ownership(items),
        "episodeEvidence": episode_evidence,
        "pipelineFacts": pipeline_facts,
        "confirmedStageCount": _confirmed_stage_count(pipeline_facts),
        "pipelineOutcome": pipeline_outcome,
        "origins": _dedupe(item.get("origin") for item in items),
        "relatedRecords": len(items),
        "updatedAt": max((str(item.get("updatedAt") or "") for item in items), default=""),
        "stages": stages,
        "steps": _merged_steps(stages),
        "qbControl": {
            "total": len(source_ids["qbHashes"]),
            "active": sum(
                int(item.get("activeDownloadTasks") or (item.get("qbControl") or {}).get("active") or 0)
                for item in items
                if _has_current_stage(item, "download")
            ),
            "paused": max((int((item.get("qbControl") or {}).get("paused") or 0) for item in items), default=0),
            "canPause": any(bool((item.get("qbControl") or {}).get("canPause")) for item in items),
            "canResume": any(bool((item.get("qbControl") or {}).get("canResume")) for item in items),
        },
        "activeDownloadTasks": sum(
            int(item.get("activeDownloadTasks") or (item.get("qbControl") or {}).get("active") or 0)
            for item in items
            if _has_current_stage(item, "download")
        ),
        "completedDownloadTasks": sum(
            int(item.get("completedDownloadTasks") or (item.get("qbControl") or {}).get("completed") or 0)
            for item in items
            if _has_current_stage(item, "download")
        ),
    }
    merged["concurrentDownloadCount"] = merged["activeDownloadTasks"]
    merged.update(_health(merged, observed_at, fresh_until, now_value))
    return merged


def _counts(items: list[dict]) -> dict:
    return {
        "total": len(items),
        "active": sum(item.get("outcomeState") == "in_progress" for item in items),
        "blocked": sum(item.get("outcomeState") == "action_required" for item in items),
        "completed": sum(item.get("outcomeState") == "playable" for item in items),
        "waiting": sum(item.get("outcomeState") == "waiting" for item in items),
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
    services = chain.get("services") or {}
    all_items = [
        _apply_user_projection(_merge_group(items, observed_at, fresh_until, now_value), services)
        for items in grouped.values()
    ]
    all_items.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    all_items.sort(key=lambda item: (
        USER_STATE_PRIORITY.get(str(item.get("userState") or ""), len(USER_STATE_PRIORITY)),
        HEALTH_PRIORITY.get(str(item.get("healthState") or ""), len(HEALTH_PRIORITY)),
        EXECUTION_PRIORITY.get(str(item.get("executionState") or ""), len(EXECUTION_PRIORITY)),
    ))
    user_counts = {
        state: sum(item.get("userState") == state for item in all_items)
        for state in USER_STATES
    }
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
    outcome_counts = derive_outcome_counts(all_items)
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
        "userCounts": user_counts,
        "outcomeCounts": outcome_counts,
        "generatedAt": str(chain.get("generatedAt") or observed_at),
        "contractVersion": 2,
    }


def _summary_item(item: dict) -> dict:
    item = present_task_item(item)
    fields = (
        "id", "title", "mediaType", "tmdbId", "seasonNumber", "episodeNumber", "posterUrl",
        "origin", "origins", "channel", "state", "confidence", "progress", "currentStep",
        "embyIndexed", "embyEvidenceScope", "qbControl", "acquisition", "updatedAt", "chainId", "mediaKey",
        "targetKey", "subscriptionId", "healthState", "observedAt", "freshUntil", "source",
        "reasonCode", "reasonText", "userReasonText", "recommendedAction", "retryEligible", "plannedRetryAt",
        "identityState", "executionState", "outcomeState", "playableAt",
        "userState", "resultText", "completedAt", "primaryAction",
        "pipelineOutcome", "confirmedStageCount",
        "relatedRecords", "activeDownloadTasks", "completedDownloadTasks", "concurrentDownloadCount",
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
    def fact_version(fact):
        return {
            "stage": fact.get("stage"),
            "state": fact.get("state"),
            "scope": fact.get("scope"),
            "evidence": fact.get("evidence"),
            "reasonCode": fact.get("reasonCode"),
            "eventAt": fact.get("eventAt"),
            "firstConfirmedPlayableAt": fact.get("firstConfirmedPlayableAt"),
            "plannedRetryAt": fact.get("plannedRetryAt"),
            "retryEligible": bool(fact.get("retryEligible")),
            "isStale": bool(fact.get("isStale")),
            "units": [{
                "state": unit.get("state"),
                "scope": unit.get("scope"),
                "evidence": unit.get("evidence"),
                "reasonCode": unit.get("reasonCode"),
                "eventAt": unit.get("eventAt"),
                "plannedRetryAt": unit.get("plannedRetryAt"),
                "retryEligible": bool(unit.get("retryEligible")),
            } for unit in fact.get("units") or []],
        }

    def outcome_version(outcome):
        return {
            "state": outcome.get("state"),
            "stage": outcome.get("stage"),
            "reasonCode": outcome.get("reasonCode"),
            "playableAt": outcome.get("playableAt"),
        }

    stable = {
        "counts": payload.get("counts") or {},
        "healthCounts": payload.get("healthCounts") or {},
        "identityCounts": payload.get("identityCounts") or {},
        "executionCounts": payload.get("executionCounts") or {},
        "userCounts": payload.get("userCounts") or {},
        "outcomeCounts": payload.get("outcomeCounts") or {},
        "originCounts": payload.get("originCounts") or {},
        "stageCounts": payload.get("stageCounts") or {},
        "services": payload.get("services") or {},
        "systemIssues": payload.get("systemIssues") or [],
        "items": [{
            "chainId": item.get("chainId"),
            "updatedAt": item.get("updatedAt"),
            "state": item.get("state"),
            "healthState": item.get("healthState"),
            "identityState": item.get("identityState"),
            "executionState": item.get("executionState"),
            "userState": item.get("userState"),
            "pipelineOutcome": outcome_version(item.get("pipelineOutcome") or {}),
            "pipelineFacts": [fact_version(fact) for fact in item.get("pipelineFacts") or []],
            "resultText": item.get("resultText"),
            "completedAt": item.get("completedAt"),
            "primaryAction": item.get("primaryAction") or {},
            "reasonCode": item.get("reasonCode"),
            "artifactKeys": item.get("artifactKeys") or [],
            "activeDownloadTasks": item.get("activeDownloadTasks") or 0,
            "completedDownloadTasks": item.get("completedDownloadTasks") or 0,
            "concurrentDownloadCount": item.get("concurrentDownloadCount") or 0,
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
                "eventAt": row.get("eventAt"),
                "ownerScope": row.get("ownerScope"),
                "ownerTargetKey": row.get("ownerTargetKey"),
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
            now_value = self.clock()
            payload = adapt_task_chain(service.get_chain(), now=now_value)
            issue_service = self.app.extensions.get("mcc_secupload_issue")
            if issue_service:
                secupload = (((payload.get("services") or {}).get("torra") or {}).get("secupload115"))
                try:
                    payload["systemIssues"] = [issue_service.snapshot(secupload)]
                except Exception:
                    payload["systemIssues"] = []
            if self.repository:
                project_history = getattr(self.repository, "project_historical_fact_times", None)
                if callable(project_history):
                    project_history(payload)
                    _refresh_pipeline_projections(payload, now_value)
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
        result = {
            key: payload.get(key)
            for key in (
                "contractVersion", "generatedAt", "version", "counts", "healthCounts",
                "identityCounts", "executionCounts", "originCounts", "stageCounts",
                "userCounts", "outcomeCounts", "services", "ledger", "systemIssues",
            )
            if key in payload
        }
        result["services"] = present_services(payload.get("services"))
        result["systemIssues"] = present_system_issues(payload.get("systemIssues"))
        return result

    def archive_summary(self, archived_date: str, payload=None) -> dict:
        projection = _archive_projection(
            payload or self.full_snapshot(force=False),
            archived_date,
            self.repository,
        )
        return projection["summary"]

    def list_items(
        self,
        *,
        health_state="",
        identity_state="",
        identity_states=None,
        execution_state="",
        outcome_states=None,
        user_state="",
        completed_date="",
        archived_date="",
        qb_active=False,
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
        archive_summary = None
        if archived_date:
            archive = _archive_projection(payload, archived_date, self.repository)
            archive_summary = archive["summary"]
            items = [item for item in items if item.get("chainId") in archive["chainIds"]]
        if qb_active:
            items = [
                item for item in items
                if int(item.get("activeDownloadTasks") or (item.get("qbControl") or {}).get("active") or 0) > 0
            ]
        if chain_id_value and self.repository:
            chain_id_value = self.repository.resolve_chain_id(chain_id_value)
        if health_state:
            items = [item for item in items if item.get("healthState") == health_state]
        wanted_identity_states = set(identity_states or ([identity_state] if identity_state else []))
        if wanted_identity_states:
            items = [item for item in items if item.get("identityState") in wanted_identity_states]
        if execution_state:
            items = [item for item in items if item.get("executionState") == execution_state]
        wanted_outcomes = set(outcome_states or [])
        if wanted_outcomes:
            items = [item for item in items if item.get("outcomeState") in wanted_outcomes]
        if user_state:
            items = [item for item in items if item.get("userState") == user_state]
        if completed_date:
            items = [
                item for item in items
                if (parsed := _parse_datetime(item.get("completedAt")))
                and parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d") == completed_date
            ]
        if chain_id_value:
            items = [item for item in items if item.get("chainId") == chain_id_value]
        if target_key_value:
            items = [item for item in items if item.get("targetKey") == target_key_value]
        if subscription_id:
            requested_keys = _subscription_keys(subscription_id)
            items = [
                item for item in items
                if not requested_keys.isdisjoint(_subscription_keys(
                    item.get("subscriptionId"),
                    (item.get("sourceIds") or {}).get("subscriptionId"),
                    *((item.get("sourceIds") or {}).get("subscriptionIds") or []),
                ))
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
        result = {
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
        if archive_summary is not None:
            result["archiveSummary"] = archive_summary
        return result

    def detail(self, chain_id_value: str, *, force=False):
        payload = self.full_snapshot(force=force)
        if self.repository:
            chain_id_value = self.repository.resolve_chain_id(chain_id_value)
        item = next((
            item for item in payload.get("items") or []
            if item.get("chainId") == chain_id_value
        ), None)
        return {
            **self.summary(force=False),
            "item": present_task_item(item) if item else None,
        }

    def migration_preview(self):
        if not self.repository:
            raise RuntimeError("任务台账尚未启用")
        service = self.app.extensions.get("mcc_task_chain_service")
        if not service:
            raise RuntimeError("任务链尚未注册")
        payload = adapt_task_chain(service.get_chain(), now=self.clock())
        preview = self.repository.preview_snapshot_migrations(payload)
        return {
            "generatedAt": payload.get("generatedAt"),
            **preview,
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
        identity_states = [
            str(value or "").strip()
            for value in request.args.getlist("identityState")
            if str(value or "").strip()
        ]
        if any(identity_state not in IDENTITY_STATES for identity_state in identity_states):
            return _error("TASK_IDENTITY_FILTER_INVALID", "身份状态筛选无效", 400)
        execution_state = str(request.args.get("executionState") or "").strip()
        if execution_state and execution_state not in EXECUTION_STATES:
            return _error("TASK_EXECUTION_FILTER_INVALID", "执行状态筛选无效", 400)
        outcome_states = [
            str(value or "").strip()
            for value in request.args.getlist("outcomeState")
            if str(value or "").strip()
        ]
        if any(outcome_state not in PIPELINE_OUTCOMES for outcome_state in outcome_states):
            return _error("TASK_OUTCOME_FILTER_INVALID", "任务结果筛选无效", 400)
        user_state = str(request.args.get("userState") or "").strip()
        if user_state and user_state not in USER_STATES:
            return _error("TASK_USER_STATE_FILTER_INVALID", "任务状态筛选无效", 400)
        completed_date = str(request.args.get("completedDate") or "").strip()
        if completed_date:
            try:
                datetime.strptime(completed_date, "%Y-%m-%d")
            except ValueError:
                return _error("TASK_COMPLETED_DATE_INVALID", "完成日期筛选无效", 400)
        archived_date = str(request.args.get("archivedDate") or "").strip()
        if archived_date:
            try:
                datetime.strptime(archived_date, "%Y-%m-%d")
            except ValueError:
                return _error("TASK_ARCHIVED_DATE_INVALID", "归档日期筛选无效", 400)
        qb_active_value = request.args.get("qbActive")
        if qb_active_value not in {None, "", "1"}:
            return _error("TASK_QB_ACTIVE_FILTER_INVALID", "qB 活跃任务筛选无效", 400)
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
                identity_states=identity_states,
                execution_state=execution_state,
                outcome_states=outcome_states,
                user_state=user_state,
                completed_date=completed_date,
                archived_date=archived_date,
                qb_active=qb_active_value == "1",
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
        except ArchiveSourceUnavailable:
            return _error("TASK_ARCHIVE_SOURCE_UNAVAILABLE", "Symedia 归档数据源暂不可用", 502)
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

    @app.get("/api/v2/tasks/ledger/migrations/preview")
    def task_ledger_migration_preview_v2():
        try:
            return jsonify(present_migration_preview(service.migration_preview()))
        except RuntimeError:
            return _error("TASK_LEDGER_NOT_AVAILABLE", "任务台账暂不可用", 503)
        except Exception:
            return _error("TASK_LEDGER_MIGRATION_PREVIEW_FAILED", "任务台账迁移预检失败", 502)

    return service
