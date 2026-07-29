from __future__ import annotations

import math
from datetime import datetime, timezone

from app.task_public_runtime import safe_public_text


QB_STALL_OBSERVATION_SECONDS = 15 * 60


def _number(value) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if math.isfinite(number) else 0


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or float(value) <= 0:
            return None
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            if not math.isfinite(number) or number <= 0:
                return None
            parsed = datetime.fromtimestamp(number, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_timestamp(value) -> str:
    parsed = _timestamp(value)
    if not parsed:
        return str(value or "").strip()
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "不到 1 分钟"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {remaining_minutes} 分钟" if remaining_minutes else f"{hours} 小时"
    days, remaining_hours = divmod(hours, 24)
    return f"{days} 天 {remaining_hours} 小时" if remaining_hours else f"{days} 天"


def _inactive_seconds(task, observed_at):
    observed = _timestamp(observed_at)
    activity = _timestamp(task.get("lastActivity") or task.get("last_activity"))
    if not observed or not activity:
        return None
    elapsed = (observed - activity).total_seconds()
    return elapsed if elapsed >= 0 else None


def _result(
    state,
    fact_state,
    count_category,
    evidence,
    reason_code,
    reason_text,
    action_text,
    *,
    inactive_seconds=None,
):
    return {
        "state": state,
        "factState": fact_state,
        "countCategory": count_category,
        "evidence": evidence,
        "reasonCode": reason_code,
        "reasonText": safe_public_text(reason_text, "qB 状态暂未确认"),
        "durationText": (
            f"已 {_duration_text(inactive_seconds)}无下载活动"
            if inactive_seconds is not None
            else "持续时间暂未确认"
        ),
        "actionText": safe_public_text(action_text, "刷新 qB 状态后重新检查"),
        "inactiveSeconds": int(inactive_seconds) if inactive_seconds is not None else None,
    }


def assess_qb_task(task, observed_at):
    task = task if isinstance(task, dict) else {}
    status = str(task.get("status") or "").strip().lower()
    raw_state = str(task.get("state") or "").strip().lower()
    progress = _number(task.get("progress"))
    download_speed = _number(task.get("dlspeed"))

    if "missing" in raw_state:
        return _result(
            "action_required", "failed", "actionRequired", "verified",
            "QB_MISSING_FILES", "qB 文件缺失", "检查文件路径后重新校验",
        )
    if "error" in raw_state:
        return _result(
            "action_required", "failed", "actionRequired", "verified",
            "QB_DOWNLOAD_FAILED", "qB 下载发生错误", "打开 qB 查看错误状态",
        )
    if "checking" in raw_state:
        return _result(
            "normal", "active", "processing", "verified",
            "QB_CHECKING", "qB 正在校验", "等待校验完成",
        )
    if status == "completed" or progress >= 0.999 or "upload" in raw_state or "stalledup" in raw_state:
        return _result(
            "normal", "succeeded", None, "verified",
            "QB_SEEDING", "qB 下载完成，正在做种", "无需处理",
        )
    if status == "paused" or "pause" in raw_state:
        return _result(
            "normal", "waiting", "waiting", "verified",
            "QB_DOWNLOAD_PAUSED", "qB 下载已暂停", "恢复下载后继续处理",
        )
    if status == "queued" and "queued" in raw_state:
        return _result(
            "normal", "waiting", "waiting", "verified",
            "QB_DOWNLOAD_QUEUED", "qB 等待下载", "检查队列优先级和下载限额",
        )
    if download_speed > 0:
        return _result(
            "normal", "active", "processing", "verified",
            "QB_DOWNLOAD_ACTIVE", "qB 正在下载", "等待下载完成",
        )

    observing = (
        status in {"stalled", "downloading"}
        or ("stalled" in raw_state and "stalledup" not in raw_state)
        or any(marker in raw_state for marker in ("downloading", "metadl", "forceddl"))
    )
    if observing:
        inactive_seconds = _inactive_seconds(task, observed_at)
        if inactive_seconds is None or inactive_seconds < QB_STALL_OBSERVATION_SECONDS:
            return _result(
                "observing", "waiting", "observing", "verified",
                "QB_DOWNLOAD_STALLED_OBSERVING", "qB 短暂无下载活动", "继续观察",
                inactive_seconds=inactive_seconds,
            )
        return _result(
            "action_required", "failed", "actionRequired", "verified",
            "QB_DOWNLOAD_STALLED", "qB 下载持续无活动", "检查 Tracker、网络和可用做种",
            inactive_seconds=inactive_seconds,
        )

    return _result(
        "unknown", "unknown", "unknown", "missing",
        "QB_STATUS_UNKNOWN", "qB 状态无法确认", "刷新 qB 状态后重新检查",
    )


def summarize_qb_assessments(results, observed_at):
    assessments = [result for result in results or [] if isinstance(result, dict)]
    counts = {
        "processing": 0,
        "waiting": 0,
        "observing": 0,
        "actionRequired": 0,
        "unknown": 0,
    }
    for result in assessments:
        category = result.get("countCategory")
        if category in counts:
            counts[category] += 1

    state = next((
        candidate for candidate in ("action_required", "unknown", "observing")
        if any(result.get("state") == candidate for result in assessments)
    ), "normal")
    relevant = [result for result in assessments if result.get("state") == state]
    if len(relevant) == 1:
        reason_code = relevant[0].get("reasonCode") or "QB_STATUS_UNKNOWN"
        reason_text = relevant[0].get("reasonText") or "qB 状态暂未确认"
    elif state == "action_required":
        reason_code = "QB_ACTION_REQUIRED"
        reason_text = f"{counts['actionRequired']} 个 qB 任务需要处理"
    elif state == "unknown":
        reason_code = "QB_STATUS_UNKNOWN"
        reason_text = "部分任务状态暂未确认"
    elif state == "observing":
        reason_code = "QB_DOWNLOAD_STALLED_OBSERVING"
        reason_text = "短暂无下载活动 · 观察中"
    else:
        reason_code = "QB_HEALTH_NORMAL"
        reason_text = "qB 当前任务状态正常"

    return {
        "state": state,
        "counts": counts,
        "reasonCode": reason_code,
        "reasonText": safe_public_text(reason_text, "qB 状态暂未确认"),
        "observedAt": _iso_timestamp(observed_at),
    }
