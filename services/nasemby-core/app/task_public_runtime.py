from __future__ import annotations

import hashlib
import re

from app.torra_subscription_keys import torra_public_storage_key, torra_public_subscription_key


URL_PATTERN = re.compile(
    r"(?:(?:https?|wss?|ftp|file)://|magnet:\?)[^\s]+",
    re.IGNORECASE,
)
HOST_PATTERN = re.compile(
    r"(?<![\w@])(?:"
    r"(?:\d{1,3}\.){3}\d{1,3}"
    r"|localhost"
    r"|\[[0-9a-f:]+\]"
    r"|(?:[A-Za-z0-9-]+\.)+(?:local|lan|internal|private|test|top|com|net|org|io|cn)"
    r"|[A-Za-z][A-Za-z0-9-]{0,62}(?=:\d{2,5}\b)"
    r")(?::\d{1,5})?(?:/[^\s]*)?",
    re.IGNORECASE,
)
WINDOWS_PATH_PATTERN = re.compile(r"\b[A-Za-z]:[\\/][^\s]+")
UNC_PATH_PATTERN = re.compile(r"\\\\[^\\\s]+\\[^\s]+")
UNIX_PATH_PATTERN = re.compile(r"(?<![\w:])/(?:[^/\s]+/)*[^/\s]+")
RELATIVE_MEDIA_PATH_PATTERN = re.compile(
    r"(?<![\w./\\])(?:[^\\/\s:]+[\\/])+[^\\/\s:]+\."
    r"(?:mkv|mp4|avi|mov|m4v|ts|m2ts|wmv|flv|webm|strm)(?:[^\s]*)?",
    re.IGNORECASE,
)
CREDENTIAL_PATTERN = re.compile(
    r"\b(?:"
    r"(?:password|passwd|passkey|token|access[_-]?token|refresh[_-]?token|api[_-]?key|api[_-]?hash|secret|cookie|authorization|sign|signature)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
    r"|bearer\s+[A-Za-z0-9._~+/=-]+"
    r")",
    re.IGNORECASE,
)
EXTERNAL_ID_PATTERN = re.compile(
    r"\b(?:job|run|task|target|analysis|candidate)(?:id)?"
    r"\s*[:=_-]\s*[A-Za-z0-9._:-]+\b",
    re.IGNORECASE,
)


def _digest(namespace, value, length=24) -> str:
    source = f"{namespace}\0{str(value or '').strip()}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:length]


def public_qb_task_ref(value) -> str:
    value = str(value or "").strip().lower()
    return _digest("qb", value, 40) if value else ""


def public_symedia_ref(value) -> str:
    value = str(value or "").strip()
    return f"symedia:{_digest('symedia', value)}" if value else ""


def public_artifact_ref(value) -> str:
    value = str(value or "").strip()
    return f"artifact:ref:{_digest('artifact', value)}" if value else ""


def public_torra_ref(value) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return torra_public_storage_key(value) if value.startswith("torra:") else torra_public_subscription_key(value)


def public_subscription_ref(value) -> str:
    value = str(value or "").strip()
    return torra_public_storage_key(value) if value.startswith("torra:") else value


def public_pipeline_ref(stage, value) -> str:
    stage = str(stage or "fact").strip().lower()
    value = str(value or "").strip()
    return f"fact:{stage}:{_digest(f'pipeline:{stage}', value)}" if value else ""


def public_pipeline_unit_ref(value) -> str:
    value = str(value or "").strip()
    return f"unit:{_digest('pipeline-unit', value)}" if value else ""


def safe_public_text(value, fallback="") -> str:
    text = str(value or "").strip()
    if not text:
        return str(fallback or "")
    text = CREDENTIAL_PATTERN.sub("[已隐藏]", text)
    text = URL_PATTERN.sub("[已隐藏]", text)
    text = UNC_PATH_PATTERN.sub("[已隐藏]", text)
    text = WINDOWS_PATH_PATTERN.sub("[已隐藏]", text)
    text = RELATIVE_MEDIA_PATH_PATTERN.sub("[已隐藏]", text)
    text = UNIX_PATH_PATTERN.sub("[已隐藏]", text)
    text = HOST_PATTERN.sub("[已隐藏]", text)
    text = EXTERNAL_ID_PATTERN.sub("[已隐藏]", text)
    return text[:500]


def _public_task_reason(stage, reason_code, value, fallback="") -> str:
    text = str(value or "").strip()
    code = str(reason_code or "").strip().upper()
    stage_name = str(stage or "").strip().lower()
    if stage_name in {"symedia", "library"} and code in {
        "QUALITY_SCORE_LOWER",
        "QUALITY_WEIGHT_NOT_HIGHER",
        "QUALITY_HIGHER_VERSION_EXISTS",
        "QUALITY_VERSION_RULE_NOT_MATCHED",
        "QUALITY_OVERWRITE_CANCELLED",
        "QUALITY_OVERWRITE_SKIPPED",
    }:
        return "未命中允许入库的版本规则，已保留现有版本"
    is_symedia = stage_name in {"symedia", "library"} or code.startswith("SYMEDIA_")
    if is_symedia and any(marker in text for marker in (
        "文件转移错误", "未找到", "未查询到", "媒体信息", "识别", "TMDB",
    )):
        return "Symedia 未查询到对应媒体信息"
    return safe_public_text(text, fallback)


def safe_public_url(value) -> str:
    url = str(value or "").strip()
    return url if url.startswith("/") and not url.startswith("//") else ""


def present_source_ids(value) -> dict:
    source = value if isinstance(value, dict) else {}
    subscription_ids = []
    for item in [source.get("subscriptionId"), *(source.get("subscriptionIds") or [])]:
        public = public_subscription_ref(item)
        if public and public not in subscription_ids:
            subscription_ids.append(public)
    torra_ids = []
    for item in [source.get("torraId"), *(source.get("torraIds") or [])]:
        public = public_torra_ref(item)
        if public and public not in torra_ids:
            torra_ids.append(public)
    qb_refs = sorted({
        public_qb_task_ref(item)
        for item in source.get("qbHashes") or []
        if str(item or "").strip()
    })
    symedia_refs = sorted({
        public_symedia_ref(item)
        for item in source.get("symediaIds") or []
        if str(item or "").strip()
    })
    return {
        "subscriptionId": subscription_ids[0] if subscription_ids else "",
        "subscriptionIds": subscription_ids,
        "torraId": torra_ids[0] if torra_ids else "",
        "torraIds": torra_ids,
        "qbHashes": qb_refs,
        "symediaIds": symedia_refs,
    }


def _present_stage(value, *, legacy=False) -> dict:
    stage = value if isinstance(value, dict) else {}
    stage_name = str(stage.get("key") or stage.get("stage") or "unknown")
    reason_text = _public_task_reason(
        stage_name,
        stage.get("reasonCode"),
        stage.get("userReasonText") or stage.get("reasonText") or stage.get("detail"),
    )
    if legacy:
        return {
            "key": stage_name,
            "label": safe_public_text(stage.get("label"), "未命名阶段"),
            "status": str(stage.get("status") or "unknown"),
            "evidence": str(stage.get("evidence") or "missing"),
            "detail": reason_text,
            "timestamp": str(stage.get("timestamp") or stage.get("observedAt") or ""),
            "source": safe_public_text(stage.get("source")),
        }
    return {
        "stage": stage_name,
        "label": safe_public_text(stage.get("label"), "未命名阶段"),
        "status": str(stage.get("status") or "unknown"),
        "healthState": str(stage.get("healthState") or "evidence_insufficient"),
        "evidence": str(stage.get("evidence") or "missing"),
        "observedAt": str(stage.get("observedAt") or stage.get("timestamp") or ""),
        "freshUntil": str(stage.get("freshUntil") or ""),
        "source": safe_public_text(stage.get("source")),
        "reasonCode": str(stage.get("reasonCode") or ""),
        "reasonText": reason_text,
        "userReasonText": reason_text,
        "recommendedAction": safe_public_text(stage.get("recommendedAction")),
        "retryEligible": bool(stage.get("retryEligible")),
        "plannedRetryAt": str(stage.get("plannedRetryAt") or ""),
        "actions": {
            "preview": bool((stage.get("actions") or {}).get("preview")),
            "retry": bool((stage.get("actions") or {}).get("retry")),
        },
    }


def _present_primary_action(value) -> dict:
    action = value if isinstance(value, dict) else {}
    kind = str(action.get("kind") or "none")
    if kind in {"open_qb", "open_torra"}:
        return {
            "kind": "view_details",
            "label": "查看详情",
            "available": True,
            "reason": "外部服务地址仅在控制室中显示",
        }
    return {
        "kind": kind,
        "label": safe_public_text(action.get("label")),
        "available": bool(action.get("available")),
        "reason": safe_public_text(action.get("reason")),
    }


def _present_ownership(value) -> dict:
    row = value if isinstance(value, dict) else {}
    return {
        "artifactKey": public_artifact_ref(row.get("artifactKey")),
        "ownerTargetKey": str(row.get("ownerTargetKey") or ""),
        "matchMethod": str(row.get("matchMethod") or ""),
        "confidence": str(row.get("confidence") or ""),
        "conflictCandidates": [str(item) for item in row.get("conflictCandidates") or []][:20],
        "source": safe_public_text(row.get("source")),
        "mediaType": str(row.get("mediaType") or "unknown"),
        "seasonNumber": int(row.get("seasonNumber") or 0),
    }


def _present_episode(value) -> dict:
    row = value if isinstance(value, dict) else {}
    return {
        key: row.get(key)
        for key in (
            "seasonNumber", "episodeStart", "episodeEnd", "numberingScheme", "stage",
            "status", "reasonCode", "eventAt", "observedAt", "matchMethod", "ownerScope",
            "ownerTargetKey", "parentTargetKey",
        )
        if key in row
    } | {"artifactKey": public_artifact_ref(row.get("artifactKey"))}


def _present_pipeline_unit(value, stage: str) -> dict:
    unit = value if isinstance(value, dict) else {}
    return {
        "unitKey": public_pipeline_unit_ref(unit.get("unitKey")),
        "state": str(unit.get("state") or "unknown"),
        "scope": str(unit.get("scope") or "system-category"),
        "evidence": str(unit.get("evidence") or "missing"),
        "eventAt": str(unit.get("eventAt") or ""),
        "observedAt": str(unit.get("observedAt") or ""),
        "freshUntil": str(unit.get("freshUntil") or ""),
        "sourceRef": public_pipeline_ref(stage, unit.get("sourceRef")),
        "reasonCode": str(unit.get("reasonCode") or ""),
        "reasonText": _public_task_reason(
            stage, unit.get("reasonCode"), unit.get("reasonText")
        ),
        "plannedRetryAt": str(unit.get("plannedRetryAt") or ""),
        "retryEligible": bool(unit.get("retryEligible")),
    }


def present_pipeline_fact(value) -> dict:
    fact = value if isinstance(value, dict) else {}
    stage = str(fact.get("stage") or "unknown")
    return {
        "stage": stage,
        "state": str(fact.get("state") or "unknown"),
        "scope": str(fact.get("scope") or "system-category"),
        "evidence": str(fact.get("evidence") or "missing"),
        "eventAt": str(fact.get("eventAt") or ""),
        "observedAt": str(fact.get("observedAt") or ""),
        "freshUntil": str(fact.get("freshUntil") or ""),
        "source": safe_public_text(fact.get("source")),
        "sourceRef": public_pipeline_ref(stage, fact.get("sourceRef")),
        "unitKey": public_pipeline_unit_ref(fact.get("unitKey")),
        "reasonCode": str(fact.get("reasonCode") or ""),
        "reasonText": _public_task_reason(
            stage, fact.get("reasonCode"), fact.get("reasonText")
        ),
        "plannedRetryAt": str(fact.get("plannedRetryAt") or ""),
        "retryEligible": bool(fact.get("retryEligible")),
        "isStale": bool(fact.get("isStale")),
        "firstConfirmedPlayableAt": str(fact.get("firstConfirmedPlayableAt") or ""),
        "units": [_present_pipeline_unit(unit, stage) for unit in fact.get("units") or []],
    }


def present_pipeline_outcome(value) -> dict:
    outcome = value if isinstance(value, dict) else {}
    return {
        "state": str(outcome.get("state") or "evidence_insufficient"),
        "stage": str(outcome.get("stage") or ""),
        "reasonCode": str(outcome.get("reasonCode") or ""),
        "reasonText": _public_task_reason(
            outcome.get("stage"), outcome.get("reasonCode"), outcome.get("reasonText")
        ),
        "observedAt": str(outcome.get("observedAt") or ""),
        "playableAt": str(outcome.get("playableAt") or ""),
    }


def present_media_result(value) -> dict:
    result = value if isinstance(value, dict) else {}
    return {
        "state": str(result.get("state") or "unknown"),
        "stage": str(result.get("stage") or ""),
        "resultText": safe_public_text(result.get("resultText"), "媒体结果暂未确认"),
        "observedAt": str(result.get("observedAt") or ""),
        "eventAt": str(result.get("eventAt") or ""),
    }


def present_residual_issues(value) -> list[dict]:
    issues = value if isinstance(value, list) else []
    result = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        stage = str(issue.get("stage") or "")
        reason_code = str(issue.get("reasonCode") or "")
        result.append({
            "stage": stage,
            "reasonCode": reason_code,
            "reasonText": _public_task_reason(stage, reason_code, issue.get("reasonText")),
            "observedAt": str(issue.get("observedAt") or ""),
            "resourceCount": max(1, int(issue.get("resourceCount") or 1)),
        })
    return result


ITEM_FIELDS = (
    "title", "mediaType", "tmdbId", "seasonNumber", "episodeNumber", "origin", "origins",
    "channel", "state", "confidence", "progress", "currentStep", "embyIndexed",
    "embyEvidenceScope", "updatedAt", "chainId", "mediaKey", "targetKey", "healthState",
    "observedAt", "freshUntil", "source", "reasonCode", "identityState", "executionState",
    "outcomeState", "playableAt", "userState", "completedAt", "relatedRecords", "activeDownloadTasks",
    "completedDownloadTasks", "concurrentDownloadCount", "retryEligible", "plannedRetryAt",
    "confirmedStageCount",
)


def present_task_item(value) -> dict:
    item = value if isinstance(value, dict) else {}
    result = {key: item.get(key) for key in ITEM_FIELDS if key in item}
    raw_id = str(item.get("id") or "")
    if raw_id.startswith("qb:"):
        public_id = f"qb:{public_qb_task_ref(raw_id.removeprefix('qb:'))}"
    elif raw_id.startswith("symedia:"):
        public_id = public_symedia_ref(raw_id.removeprefix("symedia:"))
    elif raw_id.startswith("torra:"):
        public_id = public_torra_ref(raw_id)
    else:
        public_id = raw_id
    outcome = item.get("pipelineOutcome") if isinstance(item.get("pipelineOutcome"), dict) else {}
    public_reason = _public_task_reason(
        outcome.get("stage"),
        item.get("reasonCode") or outcome.get("reasonCode"),
        item.get("userReasonText") or item.get("reasonText"),
    )
    public_result = _public_task_reason(
        outcome.get("stage"), outcome.get("reasonCode"), item.get("resultText")
    )
    result.update({
        "id": public_id,
        "title": safe_public_text(item.get("title"), "未命名媒体任务"),
        "posterUrl": safe_public_url(item.get("posterUrl")),
        "currentStep": safe_public_text(item.get("currentStep")),
        "source": safe_public_text(item.get("source")),
        "reasonText": public_reason,
        "userReasonText": public_reason,
        "recommendedAction": safe_public_text(item.get("recommendedAction")),
        "resultText": public_result,
        "suggestion": None,
        "qbControl": {
            key: (item.get("qbControl") or {}).get(key)
            for key in ("total", "active", "paused", "canPause", "canResume")
        },
        "sourceIds": present_source_ids(item.get("sourceIds")),
        "subscriptionId": public_subscription_ref(item.get("subscriptionId")),
        "artifactKeys": [public_artifact_ref(key) for key in item.get("artifactKeys") or []],
        "steps": [_present_stage(stage, legacy=True) for stage in item.get("steps") or []],
        "stages": [_present_stage(stage) for stage in item.get("stages") or []],
        "episodeEvidence": [_present_episode(row) for row in item.get("episodeEvidence") or []],
        "evidenceOwnership": [_present_ownership(row) for row in item.get("evidenceOwnership") or []],
        "pipelineFacts": [present_pipeline_fact(fact) for fact in item.get("pipelineFacts") or []],
        "pipelineOutcome": present_pipeline_outcome(item.get("pipelineOutcome")),
        "mediaResult": present_media_result(item.get("mediaResult")),
        "residualIssues": present_residual_issues(item.get("residualIssues")),
        "primaryAction": _present_primary_action(item.get("primaryAction")),
    })
    rss_source_match = item.get("rssSourceMatch") if isinstance(item.get("rssSourceMatch"), dict) else {}
    match_id = str(rss_source_match.get("matchId") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9:_-]{1,80}", match_id):
        result["rssSourceMatch"] = {"matchId": match_id}
    if isinstance(item.get("acquisition"), dict):
        result["acquisition"] = {
            key: item["acquisition"].get(key)
            for key in (
                "primary", "cloudState", "cloudEnabled", "subscriptionCloudEnabled",
                "autoFallbackEnabled", "manualActionsEnabled",
            )
        }
        result["acquisition"]["cloudDetail"] = safe_public_text(item["acquisition"].get("cloudDetail"))
    return result


def _present_upload_record(value) -> dict | None:
    row = value if isinstance(value, dict) else None
    if row is None:
        return None
    result = {
        key: row.get(key)
        for key in ("status", "runCount", "startedAt", "finishedAt", "createdAt")
        if key in row
    }
    counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
    result["counts"] = {"success": counts.get("success"), "failed": counts.get("failed")}
    return result


def _public_nonnegative_integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _present_secupload(value) -> dict:
    source = value if isinstance(value, dict) else {}
    result = {
        key: source.get(key)
        for key in (
            "configured", "connected", "pluginEnabled", "readable", "perFileEvidence",
            "activeRuns", "lastRunAt", "nextRunAt", "lastCheckedAt",
        )
        if key in source
    }
    result["pluginKey"] = ""
    result["error"] = safe_public_text(source.get("error"))
    result["latestRun"] = _present_upload_record(source.get("latestRun"))
    result["latestBatch"] = _present_upload_record(source.get("latestBatch"))
    result["recentBatches"] = [
        row for row in (_present_upload_record(item) for item in source.get("recentBatches") or []) if row
    ][:20]
    return result


def _present_issue_category(value) -> dict | None:
    source = value if isinstance(value, dict) else None
    if source is None:
        return None
    latest = source.get("latest") if isinstance(source.get("latest"), dict) else {}
    counts = {
        "success": latest.get("success"),
        "failed": latest.get("failed"),
    }
    recent = [
        int(item) for item in (source.get("recentFailedCounts") or [])[:3]
        if isinstance(item, int) and not isinstance(item, bool) and item >= 0
    ]
    category_id = str(source.get("id") or "")[:80]
    if not category_id.startswith("category:"):
        category_id = ""
    file_evidence_count = _public_nonnegative_integer(source.get("fileEvidenceCount")) or 0
    return {
        "id": category_id,
        "label": safe_public_text(source.get("label"), "未命名分类")[:80],
        "latest": {
            "success": counts["success"],
            "failed": counts["failed"],
            "finishedAt": str(latest.get("finishedAt") or "")[:80],
        },
        "recentFailedCounts": recent,
        "retryPolicyText": safe_public_text(source.get("retryPolicyText"), "重试策略未提供")[:120],
        "nextRunAt": str(source.get("nextRunAt") or "")[:80],
        "fileEvidenceAvailable": source.get("fileEvidenceAvailable") is True,
        "fileEvidenceCount": file_evidence_count,
    }


def _present_issue_file(value) -> dict | None:
    source = value if isinstance(value, dict) else None
    if source is None:
        return None
    retry_count = _public_nonnegative_integer(source.get("retryCount"))
    file_ref = str(source.get("ref") or "")[:80]
    batch_ref = str(source.get("batchRef") or "")[:80]
    category_id = str(source.get("categoryId") or "")[:80]
    refs_are_public = all((
        file_ref.startswith("file:"),
        batch_ref.startswith("batch:"),
        category_id.startswith("category:"),
    ))
    if not refs_are_public:
        return None
    return {
        "ref": file_ref,
        "batchRef": batch_ref,
        "categoryId": category_id,
        "displayName": safe_public_text(source.get("displayName"), "未命名文件")[:160],
        "errorCategory": str(source.get("errorCategory") or "upload_failed")[:80],
        "errorLabel": safe_public_text(source.get("errorLabel"), "秒传失败")[:80],
        "retryCount": retry_count,
        "observedAt": str(source.get("observedAt") or "")[:80],
    }


def present_system_issue(value) -> dict:
    source = value if isinstance(value, dict) else {}
    state = str(source.get("state") or "unknown")
    if state not in {"normal", "recovering", "action_required", "unknown"}:
        state = "unknown"
    manual = source.get("manualRetry") if isinstance(source.get("manualRetry"), dict) else {}
    primary = source.get("primaryAction") if isinstance(source.get("primaryAction"), dict) else {}
    categories = [
        row for row in (_present_issue_category(item) for item in source.get("categories") or [])
        if row is not None and row.get("id")
    ]
    failed_total = _public_nonnegative_integer(source.get("failedTotal"))
    files = [
        row for row in (_present_issue_file(item) for item in source.get("files") or [])
        if row is not None
    ][:100]
    return {
        "id": "secupload_failures",
        "state": state,
        "stateReason": str(source.get("stateReason") or "")[:80],
        "failedTotal": failed_total,
        "nextRunAt": str(source.get("nextRunAt") or "")[:80],
        "observedAt": str(source.get("observedAt") or "")[:80],
        "scheduleGraceSeconds": int(source.get("scheduleGraceSeconds") or 600),
        "maxScheduleHorizonSeconds": int(source.get("maxScheduleHorizonSeconds") or 86400),
        "categories": categories,
        "fileEvidenceAvailable": source.get("fileEvidenceAvailable") is True,
        "evidenceLimitText": safe_public_text(
            source.get("evidenceLimitText"),
            "本次运行没有文件级详情。",
        )[:160],
        "files": files,
        "fileFacts": [present_pipeline_fact(fact) for fact in source.get("fileFacts") or []][:100],
        "manualRetry": {
            "supported": manual.get("supported") is True,
            "allowed": manual.get("allowed") is True,
            "reason": safe_public_text(manual.get("reason"))[:120],
        },
        "primaryAction": {
            "kind": str(primary.get("kind") or "none")[:40],
            "label": safe_public_text(primary.get("label"))[:80],
            "available": primary.get("available") is True,
        },
    }


def present_system_issues(value) -> list[dict]:
    rows = value if isinstance(value, list) else []
    return [present_system_issue(row) for row in rows if isinstance(row, dict)]


def present_services(value) -> dict:
    services = value if isinstance(value, dict) else {}
    result = {}
    safe_fields = {
        "qb": ("connected", "configured", "total", "active", "downloadSpeed"),
        "torra": ("connected", "configured", "total"),
        "symedia": ("connected", "configured", "total", "sampled"),
        "emby": ("connected", "configured", "indexedMovies", "indexedSeries", "evidenceScope"),
    }
    for name, fields in safe_fields.items():
        source = services.get(name) if isinstance(services.get(name), dict) else {}
        item = {key: source.get(key) for key in fields if key in source}
        item["error"] = safe_public_text(source.get("error"))
        item["webUrl"] = ""
        if name == "torra" and isinstance(source.get("secupload115"), dict):
            item["secupload115"] = _present_secupload(source["secupload115"])
        result[name] = item
    return result


def present_task_chain(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    ownership = payload.get("evidenceOwnership") if isinstance(payload.get("evidenceOwnership"), dict) else {}
    return {
        "generatedAt": str(payload.get("generatedAt") or ""),
        "counts": dict(payload.get("counts") or {}),
        "services": present_services(payload.get("services")),
        "evidenceOwnership": {
            "summary": dict(ownership.get("summary") or {}),
            "records": [_present_ownership(row) for row in ownership.get("records") or []],
        },
        "items": [present_task_item(item) for item in payload.get("items") or []],
    }


def present_migration_preview(value) -> dict:
    payload = value if isinstance(value, dict) else {}
    result = {
        key: payload.get(key)
        for key in (
            "generatedAt", "artifactMigrations", "chainAliases", "artifactConflicts", "migrationSkipped",
            "migrationSkipReasons", "deletedEmptyChains", "migrationBackupCreated",
        )
        if key in payload
    }

    def present_row(row):
        source = row if isinstance(row, dict) else {}
        return {
            key: source.get(key)
            for key in (
                "expectedOldChainId", "newChainId", "targetKey", "matchMethod",
                "confidence", "migrationMode", "reasonCode",
            )
            if key in source
        } | {"artifactKey": public_artifact_ref(source.get("artifactKey"))}

    result["migrationPlans"] = [present_row(row) for row in payload.get("migrationPlans") or []]
    result["migrationSkips"] = [present_row(row) for row in payload.get("migrationSkips") or []]
    return result
