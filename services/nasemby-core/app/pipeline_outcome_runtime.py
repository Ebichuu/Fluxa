from __future__ import annotations

from datetime import datetime, timezone

from app.pipeline_fact_runtime import PIPELINE_SCOPES, merge_pipeline_facts


PIPELINE_OUTCOMES = (
    "waiting", "in_progress", "protected", "action_required", "playable", "evidence_insufficient",
)


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
        and (
            (target_scope == "movie" and row.get("scope") == "movie")
            or (
                target_scope == "episode"
                and row.get("scope") == "episode"
                and (not target_unit_key or row.get("unitKey") == target_unit_key)
            )
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
