from __future__ import annotations

from datetime import datetime, timezone

from app.pipeline_fact_runtime import PIPELINE_SCOPES, merge_pipeline_facts


PIPELINE_OUTCOMES = (
    "waiting", "in_progress", "protected", "action_required", "playable", "evidence_insufficient",
)
MEDIA_RESULT_STATES = (
    "unknown", "acquisition_satisfied", "downloaded", "cloud_transferred",
    "archived", "strm_ready", "playable",
)

_STAGE_ORDER = {
    "torra": 0,
    "qb": 1,
    "cloud115": 2,
    "symedia": 3,
    "strm": 4,
    "emby": 5,
}
_MEDIA_RESULT_BY_STAGE = {
    "torra": ("acquisition_satisfied", "获取目标已满足"),
    "qb": ("downloaded", "下载已完成"),
    "cloud115": ("cloud_transferred", "115 秒传已完成"),
    "symedia": ("archived", "已整理入库"),
    "strm": ("strm_ready", "播放入口已生成"),
    "emby": ("playable", "已可播放"),
}


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _utc(parsed)


def _fact_rows(facts: list[dict]):
    for fact in facts:
        yield fact
        for unit in fact.get("units") or []:
            yield {
                **{key: value for key, value in fact.items() if key != "units"},
                **unit,
            }


def _current(row: dict, now: datetime) -> bool:
    fresh_until = _parse(row.get("freshUntil"))
    return not bool(row.get("isStale")) and fresh_until is not None and fresh_until >= now


def _latest(rows):
    return max(rows, key=lambda row: str(row.get("observedAt") or ""))


def _outcome(state: str, row=None) -> dict:
    row = row or {}
    playable_at = ""
    if state == "playable":
        playable_at = str(
            row.get("firstConfirmedPlayableAt")
            or row.get("eventAt")
            or row.get("observedAt")
            or ""
        )
    return {
        "state": state,
        "stage": str(row.get("stage") or ""),
        "reasonCode": str(row.get("reasonCode") or ""),
        "reasonText": str(row.get("reasonText") or ""),
        "observedAt": str(row.get("observedAt") or ""),
        "playableAt": playable_at,
    }


def _verified_current_facts(
    facts,
    *,
    target_scope: str,
    now_value: datetime,
) -> list[dict]:
    return [
        fact for fact in merge_pipeline_facts(
            facts,
            target_scope=target_scope,
            observed_at=_iso(now_value),
            now=now_value,
        )
        if _current(fact, now_value) and fact.get("evidence") == "verified"
    ]


def _matches_emby_target(row: dict, *, target_scope: str, target_unit_key: str) -> bool:
    return (
        (target_scope == "movie" and row.get("scope") == "movie")
        or (
            target_scope == "episode"
            and row.get("scope") == "episode"
            and (not target_unit_key or row.get("unitKey") == target_unit_key)
        )
    )


def _matches_media_stage_target(row: dict, *, target_scope: str, target_unit_key: str) -> bool:
    scope = str(row.get("scope") or "")
    if target_scope == "movie":
        allowed_scopes = {"movie", "file"}
    elif target_scope == "episode":
        allowed_scopes = {"episode", "file"}
    elif target_scope == "season":
        allowed_scopes = {"season", "file"}
    else:
        return False
    if scope not in allowed_scopes:
        return False
    unit_key = str(row.get("unitKey") or "")
    return not target_unit_key or not unit_key or unit_key == target_unit_key


def derive_media_result(
    facts,
    *,
    target_scope: str,
    target_unit_key: str = "",
    now: datetime | None = None,
) -> dict:
    now_value = _utc(now)
    if target_scope not in PIPELINE_SCOPES:
        raise ValueError(f"target_scope 值无效: {target_scope}")
    candidates = []
    for fact in _verified_current_facts(
        facts,
        target_scope=target_scope,
        now_value=now_value,
    ):
        if fact.get("state") != "succeeded":
            continue
        if fact.get("stage") == "emby" and not _matches_emby_target(
            fact,
            target_scope=target_scope,
            target_unit_key=target_unit_key,
        ):
            continue
        if fact.get("stage") != "emby" and not _matches_media_stage_target(
            fact,
            target_scope=target_scope,
            target_unit_key=target_unit_key,
        ):
            continue
        candidates.append(fact)
    if not candidates:
        return {
            "state": "unknown",
            "stage": "",
            "resultText": "媒体结果暂未确认",
            "observedAt": "",
            "eventAt": "",
        }
    selected = max(
        candidates,
        key=lambda row: (_STAGE_ORDER.get(str(row.get("stage") or ""), -1), str(row.get("observedAt") or "")),
    )
    state, result_text = _MEDIA_RESULT_BY_STAGE[str(selected.get("stage") or "")]
    event_at = str(
        selected.get("firstConfirmedPlayableAt")
        or selected.get("eventAt")
        or selected.get("observedAt")
        or ""
    )
    return {
        "state": state,
        "stage": str(selected.get("stage") or ""),
        "resultText": result_text,
        "observedAt": str(selected.get("observedAt") or ""),
        "eventAt": event_at,
    }


def derive_residual_issues(
    facts,
    *,
    target_scope: str,
    target_unit_key: str = "",
    now: datetime | None = None,
) -> list[dict]:
    now_value = _utc(now)
    media_result = derive_media_result(
        facts,
        target_scope=target_scope,
        target_unit_key=target_unit_key,
        now=now_value,
    )
    media_stage_order = _STAGE_ORDER.get(str(media_result.get("stage") or ""), -1)
    if media_stage_order < 0:
        return []

    grouped = {}
    for fact in _verified_current_facts(
        facts,
        target_scope=target_scope,
        now_value=now_value,
    ):
        stage = str(fact.get("stage") or "")
        if _STAGE_ORDER.get(stage, 99) >= media_stage_order:
            continue
        unit_failures = [
            unit for unit in fact.get("units") or []
            if unit.get("state") == "failed" and unit.get("evidence") == "verified"
            and _current(unit, now_value)
        ]
        failures = unit_failures or ([fact] if fact.get("state") == "failed" else [])
        for row in failures:
            retry_at = _parse(row.get("plannedRetryAt"))
            if retry_at is not None and retry_at > now_value:
                continue
            reason_code = str(row.get("reasonCode") or fact.get("reasonCode") or "")
            reason_text = str(row.get("reasonText") or fact.get("reasonText") or "")
            key = (stage, reason_code, reason_text)
            issue = grouped.setdefault(key, {
                "stage": stage,
                "reasonCode": reason_code,
                "reasonText": reason_text,
                "observedAt": "",
                "resourceCount": 0,
            })
            issue["resourceCount"] += 1
            issue["observedAt"] = max(
                str(issue.get("observedAt") or ""),
                str(row.get("observedAt") or fact.get("observedAt") or ""),
            )
    return sorted(
        grouped.values(),
        key=lambda issue: (_STAGE_ORDER.get(issue["stage"], 99), issue["reasonCode"], issue["reasonText"]),
    )


def derive_pipeline_outcome(
    facts,
    *,
    target_scope: str,
    target_unit_key: str = "",
    now: datetime | None = None,
) -> dict:
    now_value = _utc(now)
    if target_scope not in PIPELINE_SCOPES:
        raise ValueError(f"target_scope 值无效: {target_scope}")
    facts = merge_pipeline_facts(
        facts,
        target_scope=target_scope,
        observed_at=_iso(now_value),
        now=now_value,
    )
    rows = [
        row for row in _fact_rows(facts)
        if _current(row, now_value) and row.get("evidence") == "verified"
    ]

    playable = [
        row for row in rows
        if row.get("stage") == "emby"
        and row.get("state") == "succeeded"
        and row.get("evidence") == "verified"
        and _matches_emby_target(
            row,
            target_scope=target_scope,
            target_unit_key=target_unit_key,
        )
    ]
    if playable:
        row = _latest(playable)
        return _outcome("playable", row)

    failures = [row for row in rows if row.get("state") == "failed"]
    unrecovered = [
        row for row in failures
        if not (retry_at := _parse(row.get("plannedRetryAt"))) or retry_at <= now_value
    ]
    if unrecovered:
        return _outcome("action_required", _latest(unrecovered))

    recovering = [
        row for row in failures
        if (retry_at := _parse(row.get("plannedRetryAt"))) and retry_at > now_value
    ]
    active = [row for row in rows if row.get("state") == "active"]
    if active or recovering:
        return _outcome("in_progress", _latest([*active, *recovering]))

    protected = [row for row in rows if row.get("state") == "protected"]
    if protected:
        return _outcome("protected", _latest(protected))

    waiting = [row for row in rows if row.get("state") == "waiting"]
    if waiting:
        return _outcome("waiting", _latest(waiting))

    return _outcome("evidence_insufficient", {
        "reasonCode": "EVIDENCE_INSUFFICIENT",
        "reasonText": "缺少当前目标的明确可播放证据",
    })


def derive_outcome_counts(items) -> dict:
    counts = {state: 0 for state in PIPELINE_OUTCOMES}
    for item in items or []:
        state = str((item.get("pipelineOutcome") or {}).get("state") or "evidence_insufficient")
        counts[state if state in counts else "evidence_insufficient"] += 1
    return counts
