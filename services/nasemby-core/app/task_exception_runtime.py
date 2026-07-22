from __future__ import annotations

import re
from datetime import datetime, timezone

from app.health_state_runtime import evidence


PROTECTION_RULES = (
    ("QUALITY_SCORE_LOWER", ("评分低于目标", "源文件评分低于", "低分")),
    ("QUALITY_WEIGHT_NOT_HIGHER", ("权重低于或等于", "权重不高于")),
    ("QUALITY_OVERWRITE_CANCELLED", ("取消覆盖",)),
    ("QUALITY_OVERWRITE_SKIPPED", ("不执行覆盖", "不覆盖")),
    ("QUALITY_HIGHER_VERSION_EXISTS", ("已有更高质量版本", "现有版本评分更高", "更高版本", "高分版本")),
    ("DUPLICATE_RESOURCE_SKIPPED", ("重复资源跳过", "重复", "已存在", "跳过")),
)
TECHNICAL_REASON_PATTERN = re.compile(
    r"(?:[a-zA-Z]:\\|/(?:vol|volume|mnt|downloads?)/|https?://|\b[a-f0-9]{32,}\b)",
    re.IGNORECASE,
)


def _text(value) -> str:
    return str(value or "").strip()


def _as_utc(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now(value=None) -> datetime:
    parsed = _as_utc(value)
    return parsed or datetime.now(timezone.utc)


def protection_rule(*values) -> str:
    text = " ".join(_text(value).lower() for value in values)
    reason_code = next((
        _text(value)
        for value in values
        if _text(value).startswith(("QUALITY_", "DUPLICATE_"))
    ), "")
    if reason_code:
        return reason_code
    english_rules = {
        "QUALITY_SCORE_LOWER": ("low score",),
        "QUALITY_HIGHER_VERSION_EXISTS": ("higher quality",),
        "DUPLICATE_RESOURCE_SKIPPED": ("duplicate", "skipped"),
    }
    for code, markers in (*PROTECTION_RULES, *english_rules.items()):
        if any(marker in text for marker in markers):
            return code
    return ""


def _contains_protection(*values) -> bool:
    return bool(protection_rule(*values))


def _planned_retry(value: dict, current: datetime) -> str:
    for key in (
        "nextRetryAt",
        "next_retry_at",
        "retryAt",
        "retry_at",
        "scheduledRetryAt",
        "scheduled_retry_at",
        "nextCheckAt",
        "next_check_at",
    ):
        planned = _as_utc(value.get(key))
        if planned and planned > current:
            return _iso(planned)
    return ""


def _retry_eligible(value: dict) -> bool:
    explicit = value.get("retryEligible", value.get("retry_eligible"))
    if explicit is not None:
        return bool(explicit)
    attempts = value.get("attemptCount", value.get("attempt_count"))
    limit = value.get("maxRetryCount", value.get("max_retry_count"))
    try:
        return int(limit) > 0 and int(attempts or 0) >= int(limit)
    except (TypeError, ValueError):
        return False


def _stage_label(stage: dict) -> str:
    return _text(stage.get("label") or stage.get("key") or "当前阶段")


def _action_text(stage: dict) -> str:
    key = _text(stage.get("key") or stage.get("stage"))
    return {
        "download": "检查 qB 下载任务并确认是否需要处理",
        "cloud115": "检查秒传状态和原始下载文件",
        "library": "查看归档失败原因和保留文件",
    }.get(key, f"查看 {_stage_label(stage)} 的任务证据")


def _user_reason_text(stage: dict, state: str, value: str) -> str:
    text = _text(value)
    if not text or not TECHNICAL_REASON_PATTERN.search(text):
        return text
    key = _text(stage.get("key") or stage.get("stage")).lower()
    source = _text(stage.get("source")).lower()
    reason_code = _text(stage.get("reasonCode")).upper()
    if key in {"library", "symedia"} or "symedia" in source or "SYMEDIA" in reason_code:
        if any(marker in text for marker in ("未找到", "识别", "TMDB", "媒体信息")):
            return "Symedia 未找到对应媒体信息"
        return "Symedia 未完成媒体入库"
    if key == "cloud115" or source == "115" or "UPLOAD" in reason_code:
        return "115 处理未完成"
    if key == "download" or "qbittorrent" in source or "DOWNLOAD" in reason_code:
        return "qB 下载任务未正常继续"
    if key in {"resource", "torra"} or "torra" in source:
        return "Torra 未确认资源处理结果"
    return "当前阶段没有形成可验证结果" if state != "normal" else "当前阶段已完成"


def _outcome(state, reason_code, reason_text, recommended):
    return state, reason_code, reason_text, recommended


def _protected_stage(stage, _status, _evidence_state, _expired, _planned_at):
    protected = protection_rule(
        stage.get("reasonCode"),
        stage.get("reasonText"),
        stage.get("detail"),
    )
    if protected:
        return _outcome(
            "protected",
            _text(stage.get("reasonCode")) or "QUALITY_PROTECTED",
            _text(stage.get("reasonText") or stage.get("detail") or "版本被正常保护，未覆盖已有资源"),
            "已保留低分源文件，可进入存储清理",
        )


def _scheduled_stage(_stage, _status, _evidence_state, _expired, planned_at):
    if planned_at:
        return _outcome(
            "waiting",
            "RETRY_SCHEDULED",
            f"已安排下一次检查：{planned_at}",
            "等待下一轮计划重试",
        )


def _blocked_stage(stage, status, _evidence_state, _expired, _planned_at):
    if status == "blocked":
        return _outcome(
            "action_required",
            _text(stage.get("reasonCode")) or "STAGE_BLOCKED",
            _text(stage.get("reasonText") or stage.get("detail") or "当前阶段发生阻塞"),
            _action_text(stage),
        )


def _expired_stage(_stage, _status, _evidence_state, expired, _planned_at):
    if expired:
        return _outcome(
            "evidence_insufficient",
            "EVIDENCE_EXPIRED",
            "这一步的状态证据已过期，请刷新来源后重新检查",
            "刷新来源后重新检查",
        )


def _waiting_stage(stage, status, _evidence_state, _expired, _planned_at):
    if status in {"active", "waiting"}:
        return _outcome(
            "waiting",
            "STAGE_IN_PROGRESS",
            _text(stage.get("reasonText") or stage.get("detail") or "当前阶段仍在处理"),
            "等待当前阶段完成",
        )


def _missing_stage(stage, status, evidence_state, _expired, _planned_at):
    if evidence_state == "missing" or status == "unknown":
        return _outcome(
            "evidence_insufficient",
            _text(stage.get("reasonCode")) or "EVIDENCE_MISSING",
            _text(stage.get("reasonText") or stage.get("detail") or "暂时没有足够证据确认状态"),
            "刷新来源后重新检查",
        )


def _completed_stage(stage, status, evidence_state, _expired, _planned_at):
    if status == "done" and evidence_state in {"verified", "inferred"}:
        return _outcome(
            "normal",
            _text(stage.get("reasonCode")) or "STAGE_DONE",
            _text(stage.get("reasonText") or stage.get("detail") or "当前阶段已完成"),
            "",
        )


def _stage_outcome(stage, status, evidence_state, expired, planned_at):
    resolvers = (
        _protected_stage,
        _scheduled_stage,
        _blocked_stage,
        _expired_stage,
        _waiting_stage,
        _missing_stage,
        _completed_stage,
    )
    for resolver in resolvers:
        result = resolver(stage, status, evidence_state, expired, planned_at)
        if result:
            return result
    return _outcome(
        "evidence_insufficient",
        _text(stage.get("reasonCode")) or "EVIDENCE_UNVERIFIED",
        _text(stage.get("reasonText") or stage.get("detail") or "状态尚未完成验证"),
        "刷新来源后重新检查",
    )


def classify_stage(stage: dict, *, now=None, observed_at="", fresh_until="") -> dict:
    current = _now(now)
    status = _text(stage.get("status") or "unknown").lower()
    evidence_state = _text(stage.get("evidence") or "missing").lower()
    observed = _text(stage.get("observedAt") or stage.get("timestamp") or observed_at)
    freshness = _text(stage.get("freshUntil") or fresh_until)
    deadline = _as_utc(freshness)
    outcome = _stage_outcome(
        stage,
        status,
        evidence_state,
        bool(deadline and deadline <= current),
        _planned_retry(stage, current),
    )
    state, reason_code, reason_text, recommended = outcome
    technical_reason = _text(stage.get("technicalReasonText") or stage.get("reasonText") or stage.get("detail"))
    user_reason = _user_reason_text(stage, state, reason_text)
    result = evidence(
        state=state,
        source=_text(stage.get("source") or "task-chain"),
        reason_code=reason_code,
        reason_text=user_reason,
        observed_at=observed or _iso(current),
        fresh_until=freshness,
    )
    result.update({
        "userReasonText": user_reason,
        "technicalReasonText": technical_reason,
        "recommendedAction": recommended,
        "retryEligible": bool(_retry_eligible(stage) and state == "action_required"),
        "plannedRetryAt": _planned_retry(stage, current),
    })
    matched_rule = protection_rule(
        stage.get("reasonCode"),
        stage.get("reasonText"),
        stage.get("detail"),
    )
    if matched_rule:
        result["matchedProtectionRule"] = matched_rule
    return result


def _first_result(classified, state):
    return next((result for result in classified if result["healthState"] == state), {})


def _task_action(item, classified):
    blocked = _text(item.get("state")).lower() == "blocked"
    protected_or_waiting = any(result["healthState"] in {"protected", "waiting"} for result in classified)
    has_action = any(result["healthState"] == "action_required" for result in classified)
    if not has_action and (not blocked or protected_or_waiting):
        return None
    result = _first_result(classified, "action_required")
    return _outcome(
        "action_required",
        _text(item.get("reasonCode")) or result.get("reasonCode") or "TASK_BLOCKED",
        _text(item.get("reasonText")) or result.get("reasonText") or "任务链出现阻塞",
        result.get("recommendedAction") or "查看任务证据并处理阻塞",
    )


def _task_evidence(item, classified):
    has_missing = any(result["healthState"] == "evidence_insufficient" for result in classified)
    if not has_missing and _text(item.get("state")).lower() != "unknown":
        return None
    result = _first_result(classified, "evidence_insufficient")
    return _outcome(
        "evidence_insufficient",
        _text(item.get("reasonCode")) or result.get("reasonCode") or "EVIDENCE_INSUFFICIENT",
        _text(item.get("reasonText")) or result.get("reasonText") or "暂时没有足够证据确认任务状态",
        result.get("recommendedAction") or "刷新来源后重新检查",
    )


def _task_waiting(item, classified):
    has_waiting = any(result["healthState"] == "waiting" for result in classified)
    if not has_waiting and _text(item.get("state")).lower() not in {"active", "waiting"}:
        return None
    result = _first_result(classified, "waiting")
    return _outcome(
        "waiting",
        _text(item.get("reasonCode")) or result.get("reasonCode") or "TASK_IN_PROGRESS",
        _text(item.get("reasonText")) or result.get("reasonText") or "任务正在处理或等待下一阶段",
        result.get("recommendedAction") or "等待当前阶段完成",
    )


def _task_protected(item, classified):
    has_protected = any(result["healthState"] == "protected" for result in classified)
    item_protected = _contains_protection(item.get("reasonCode"), item.get("reasonText"))
    if not has_protected and not item_protected:
        return None
    result = _first_result(classified, "protected")
    return _outcome(
        "protected",
        _text(item.get("reasonCode")) or result.get("reasonCode") or "QUALITY_PROTECTED",
        _text(item.get("reasonText")) or result.get("reasonText") or "版本被正常保护，未覆盖已有资源",
        "已保留低分源文件，可进入存储清理",
    )


def _identity_state(item):
    explicit = _text(item.get("identityState")).lower()
    if explicit in {"unidentified", "linked", "conflict"}:
        return explicit
    confidence = _text(item.get("confidence")).lower()
    reason_code = _text(item.get("reasonCode")).upper()
    if confidence == "unlinked":
        return "unidentified"
    if confidence == "conflict" or "CONFLICT" in reason_code:
        return "conflict"
    return "linked"


def _execution_state(item, stages, classified, identity_state):
    action_indexes = [
        index for index, result in enumerate(classified)
        if result.get("healthState") == "action_required"
    ]
    if action_indexes:
        if any(_text(stages[index].get("evidence")).lower() == "verified" for index in action_indexes):
            return "confirmed_failed"
        return "suspected_blocked" if identity_state == "unidentified" else "action_required"
    if _text(item.get("state")).lower() == "blocked":
        return "suspected_blocked" if identity_state == "unidentified" else "action_required"
    if any(result.get("healthState") == "protected" for result in classified):
        return "protected"
    if any(result.get("healthState") == "waiting" for result in classified):
        return "waiting"
    if classified and all(result.get("healthState") == "normal" for result in classified):
        return "normal"
    return "waiting"


def _task_outcome(item, classified, identity_state, execution_state):
    if execution_state in {"action_required", "confirmed_failed"}:
        result = _task_action(item, classified)
        if result:
            return result
    if execution_state == "suspected_blocked":
        return _outcome(
            "evidence_insufficient",
            "TASK_SUSPECTED_BLOCKED",
            "已有处理阶段长时间没有形成后续证据，当前只能判断为疑似阻塞",
            "补充媒体身份并刷新下游状态",
        )
    if identity_state == "unidentified":
        return _outcome("evidence_insufficient", "TASK_IDENTITY_UNLINKED", "任务尚未关联到订阅或媒体身份", "补充媒体身份后重新检查")
    if identity_state == "conflict":
        return _outcome("evidence_insufficient", "TASK_IDENTITY_CONFLICT", "任务对应多个媒体候选，当前没有自动绑定", "检查媒体身份冲突")
    for resolver in (_task_action, _task_protected, _task_waiting, _task_evidence):
        result = resolver(item, classified)
        if result:
            return result
    all_normal = classified and all(result["healthState"] == "normal" for result in classified)
    if all_normal and _text(item.get("state")).lower() == "completed":
        return _outcome("normal", "TASK_COMPLETED", "任务链已完成且证据仍有效", "")
    return _outcome("evidence_insufficient", "EVIDENCE_UNVERIFIED", "暂时没有足够证据确认任务状态", "刷新来源后重新检查")


def classify_task(item: dict, *, now=None, observed_at="", fresh_until="") -> dict:
    current = _now(now)
    stages = [stage for stage in (item.get("stages") or item.get("steps") or []) if isinstance(stage, dict)]
    classified = [
        classify_stage(stage, now=current, observed_at=observed_at, fresh_until=fresh_until)
        for stage in stages
    ]
    identity_state = _identity_state(item)
    execution_state = _execution_state(item, stages, classified, identity_state)
    state, reason_code, reason_text, recommended = _task_outcome(
        item, classified, identity_state, execution_state
    )
    problem_index = next((
        index for index, result in enumerate(classified)
        if result.get("healthState") in {"action_required", "evidence_insufficient"}
    ), None)
    problem_stage = stages[problem_index] if problem_index is not None else item
    technical_reason = _text(
        item.get("technicalReasonText")
        or item.get("reasonText")
        or (classified[problem_index].get("technicalReasonText") if problem_index is not None else "")
    )
    user_reason = _user_reason_text(problem_stage, state, reason_text)
    result = evidence(
        state=state,
        source=_text(item.get("source") or "task-chain"),
        reason_code=reason_code,
        reason_text=user_reason,
        observed_at=_text(item.get("observedAt") or observed_at) or _iso(current),
        fresh_until=_text(item.get("freshUntil") or fresh_until),
    )
    planned = next((_planned_retry(stage, current) for stage in stages if _planned_retry(stage, current)), "")
    result.update({
        "identityState": identity_state,
        "executionState": execution_state,
        "userReasonText": user_reason,
        "technicalReasonText": technical_reason,
        "recommendedAction": recommended,
        "retryEligible": bool(state == "action_required" and any(_retry_eligible(stage) for stage in stages)),
        "plannedRetryAt": planned,
    })
    return result
