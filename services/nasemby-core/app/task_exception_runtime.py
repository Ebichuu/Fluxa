from __future__ import annotations

from datetime import datetime, timezone

from app.health_state_runtime import evidence


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


def _contains_protection(*values) -> bool:
    text = " ".join(_text(value).lower() for value in values)
    markers = (
        "低分",
        "评分更高",
        "更高版本",
        "高分版本",
        "正常保护",
        "重复",
        "已存在",
        "跳过",
        "low score",
        "higher quality",
        "duplicate",
        "skipped",
    )
    return any(marker in text for marker in markers)


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


def _outcome(state, reason_code, reason_text, recommended):
    return state, reason_code, reason_text, recommended


def _protected_stage(stage, _status, _evidence_state, _expired, _planned_at):
    protected = _contains_protection(
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
    result = evidence(
        state=state,
        source=_text(stage.get("source") or "task-chain"),
        reason_code=reason_code,
        reason_text=reason_text,
        observed_at=observed or _iso(current),
        fresh_until=freshness,
    )
    result.update({
        "recommendedAction": recommended,
        "retryEligible": bool(_retry_eligible(stage) and state == "action_required"),
        "plannedRetryAt": _planned_retry(stage, current),
    })
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


def _task_outcome(item, classified):
    if _text(item.get("confidence")).lower() == "unlinked":
        return _outcome("evidence_insufficient", "TASK_IDENTITY_UNLINKED", "任务尚未关联到订阅或媒体身份", "补充媒体身份后重新检查")
    for resolver in (_task_action, _task_evidence, _task_waiting, _task_protected):
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
    state, reason_code, reason_text, recommended = _task_outcome(item, classified)
    result = evidence(
        state=state,
        source=_text(item.get("source") or "task-chain"),
        reason_code=reason_code,
        reason_text=reason_text,
        observed_at=_text(item.get("observedAt") or observed_at) or _iso(current),
        fresh_until=_text(item.get("freshUntil") or fresh_until),
    )
    planned = next((_planned_retry(stage, current) for stage in stages if _planned_retry(stage, current)), "")
    result.update({
        "recommendedAction": recommended,
        "retryEligible": bool(state == "action_required" and any(_retry_eligible(stage) for stage in stages)),
        "plannedRetryAt": planned,
    })
    return result
