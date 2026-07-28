from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


PIPELINE_STAGES = ("torra", "qb", "cloud115", "symedia", "strm", "emby")
PIPELINE_STATES = ("unknown", "waiting", "active", "succeeded", "failed", "protected", "not_applicable")
PIPELINE_SCOPES = ("movie", "season", "episode", "file", "system-category")
PIPELINE_EVIDENCE = ("verified", "inferred", "missing")

_FACT_FIELDS = {
    "stage", "state", "scope", "evidence", "eventAt", "observedAt", "freshUntil", "source", "sourceRef",
    "unitKey", "reasonCode", "reasonText", "plannedRetryAt", "retryEligible", "units",
    "firstConfirmedPlayableAt",
}
_UNIT_FIELDS = {
    "unitKey", "state", "scope", "evidence", "eventAt", "observedAt", "freshUntil", "sourceRef",
    "reasonCode", "reasonText", "plannedRetryAt", "retryEligible",
}
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


class PipelineFactValidationError(ValueError):
    pass


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value, field: str, default: datetime | None = None) -> datetime:
    if value in (None, ""):
        if default is None:
            raise PipelineFactValidationError(f"{field} 不能为空")
        return _utc(default)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PipelineFactValidationError(f"{field} 不是有效时间") from exc
    return _utc(parsed)


def _enum(value, allowed, field: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise PipelineFactValidationError(f"{field} 值无效: {normalized or '<empty>'}")
    return normalized


def _reason_code(value, stage: str, state: str) -> str:
    code = str(value or f"{stage}_{state}").strip().upper()
    if not _CODE_PATTERN.fullmatch(code):
        raise PipelineFactValidationError("reasonCode 格式无效")
    return code


def _validate_state_evidence(state: str, evidence: str, prefix=""):
    if (state == "unknown") != (evidence == "missing"):
        label = f"{prefix} " if prefix else ""
        raise PipelineFactValidationError(f"{label}unknown 状态必须且只能使用 missing 证据")


def _time_window(value, *, prefix="", observed_default=None, fresh_default=None):
    field_prefix = f"{prefix}." if prefix else ""
    observed = _parse_datetime(value.get("observedAt"), f"{field_prefix}observedAt", observed_default)
    fresh = _parse_datetime(value.get("freshUntil"), f"{field_prefix}freshUntil", fresh_default)
    if fresh < observed:
        raise PipelineFactValidationError(f"{field_prefix}freshUntil 不能早于 observedAt")
    return observed, fresh


def _add_optional_fields(result: dict, value: dict):
    unit_key = str(value.get("unitKey") or "").strip()
    if unit_key:
        result["unitKey"] = unit_key[:240]
    planned_retry = value.get("plannedRetryAt")
    if planned_retry:
        result["plannedRetryAt"] = _iso(_parse_datetime(planned_retry, "plannedRetryAt"))
    event_at = value.get("eventAt")
    if event_at:
        result["eventAt"] = _iso(_parse_datetime(event_at, "eventAt"))
    first_playable = value.get("firstConfirmedPlayableAt")
    if first_playable:
        result["firstConfirmedPlayableAt"] = _iso(
            _parse_datetime(first_playable, "firstConfirmedPlayableAt")
        )


def _normalize_units(values, parent):
    units = [_normalize_unit(unit, parent=parent) for unit in values or []]
    unit_keys = [unit["unitKey"] for unit in units]
    if len(unit_keys) != len(set(unit_keys)):
        raise PipelineFactValidationError("同一 fact 中的 unitKey 必须唯一")
    return units


def _normalize_unit(value, *, parent: dict) -> dict:
    if not isinstance(value, dict):
        raise PipelineFactValidationError("units 只能包含对象")
    unknown = set(value) - _UNIT_FIELDS
    if unknown:
        raise PipelineFactValidationError(f"unit 包含未知字段: {', '.join(sorted(unknown))}")
    unit_key = str(value.get("unitKey") or "").strip()
    if not unit_key:
        raise PipelineFactValidationError("unitKey 不能为空")
    state = _enum(value.get("state", parent["state"]), PIPELINE_STATES, "unit.state")
    scope = _enum(value.get("scope", parent["scope"]), PIPELINE_SCOPES, "unit.scope")
    evidence = _enum(value.get("evidence", parent["evidence"]), PIPELINE_EVIDENCE, "unit.evidence")
    _validate_state_evidence(state, evidence, "unit")
    observed, fresh = _time_window(
        value,
        prefix="unit",
        observed_default=_parse_datetime(parent["observedAt"], "observedAt"),
        fresh_default=_parse_datetime(parent["freshUntil"], "freshUntil"),
    )
    result = {
        "unitKey": unit_key[:240],
        "state": state,
        "scope": scope,
        "evidence": evidence,
        "observedAt": _iso(observed),
        "freshUntil": _iso(fresh),
        "sourceRef": str(value.get("sourceRef", parent.get("sourceRef") or "")).strip()[:500],
        "reasonCode": _reason_code(value.get("reasonCode"), parent["stage"], state),
        "reasonText": str(value.get("reasonText") or parent.get("reasonText") or "").strip()[:1000],
        "retryEligible": bool(value.get("retryEligible", parent.get("retryEligible"))),
    }
    event_at = value.get("eventAt")
    if event_at:
        result["eventAt"] = _iso(_parse_datetime(event_at, "unit.eventAt"))
    planned_retry = value.get("plannedRetryAt", parent.get("plannedRetryAt"))
    if planned_retry:
        result["plannedRetryAt"] = _iso(_parse_datetime(planned_retry, "unit.plannedRetryAt"))
    return result


def normalize_pipeline_fact(value) -> dict:
    if not isinstance(value, dict):
        raise PipelineFactValidationError("pipeline fact 必须是对象")
    unknown = set(value) - _FACT_FIELDS
    if unknown:
        raise PipelineFactValidationError(f"pipeline fact 包含未知字段: {', '.join(sorted(unknown))}")

    stage = _enum(value.get("stage"), PIPELINE_STAGES, "stage")
    state = _enum(value.get("state"), PIPELINE_STATES, "state")
    scope = _enum(value.get("scope"), PIPELINE_SCOPES, "scope")
    evidence = _enum(value.get("evidence"), PIPELINE_EVIDENCE, "evidence")
    _validate_state_evidence(state, evidence)
    observed, fresh = _time_window(value)

    result = {
        "stage": stage,
        "state": state,
        "scope": scope,
        "evidence": evidence,
        "observedAt": _iso(observed),
        "freshUntil": _iso(fresh),
        "source": str(value.get("source") or "").strip()[:160],
        "sourceRef": str(value.get("sourceRef") or "").strip()[:500],
        "reasonCode": _reason_code(value.get("reasonCode"), stage, state),
        "reasonText": str(value.get("reasonText") or "").strip()[:1000],
        "retryEligible": bool(value.get("retryEligible")),
    }
    _add_optional_fields(result, value)
    units = _normalize_units(value.get("units"), result)
    if units:
        result["units"] = units
    return result


def target_scope_for_item(item: dict) -> str:
    if str(item.get("mediaType") or "") == "movie":
        return "movie"
    try:
        if int(item.get("episodeNumber") or 0) > 0:
            return "episode"
        if int(item.get("seasonNumber") or 0) > 0:
            return "season"
    except (TypeError, ValueError):
        pass
    return "system-category"


def _missing_fact(stage: str, scope: str, observed_at: str, fresh_until: str, reason_code=None) -> dict:
    return {
        "stage": stage,
        "state": "unknown",
        "scope": scope,
        "evidence": "missing",
        "observedAt": observed_at,
        "freshUntil": fresh_until,
        "source": "",
        "sourceRef": "",
        "reasonCode": reason_code or f"{stage.upper()}_EVIDENCE_MISSING",
        "reasonText": "缺少明确阶段证据",
        "retryEligible": False,
        "isStale": False,
    }


def _fact_signature(fact: dict) -> tuple:
    return (
        fact.get("state"), fact.get("scope"), fact.get("evidence"), fact.get("unitKey", ""),
        fact.get("eventAt", ""), fact.get("plannedRetryAt", ""), bool(fact.get("retryEligible")),
    )


def merge_pipeline_facts(
    facts,
    *,
    target_scope: str,
    observed_at: str,
    now: datetime | None = None,
) -> list[dict]:
    scope = _enum(target_scope, PIPELINE_SCOPES, "target_scope")
    now_value = _utc(now)
    observed_value = _parse_datetime(observed_at, "observed_at", now_value)
    observed_text = _iso(observed_value)
    fresh_text = _iso(observed_value + timedelta(minutes=5))
    grouped = {stage: [] for stage in PIPELINE_STAGES}
    for value in facts or []:
        candidate = dict(value) if isinstance(value, dict) else value
        if isinstance(candidate, dict):
            candidate.pop("isStale", None)
        normalized = normalize_pipeline_fact(candidate)
        grouped[normalized["stage"]].append(normalized)

    result = []
    for stage in PIPELINE_STAGES:
        candidates = grouped[stage]
        if not candidates:
            result.append(_missing_fact(stage, scope, observed_text, fresh_text))
            continue
        current = [
            fact for fact in candidates
            if _parse_datetime(fact["freshUntil"], "freshUntil") >= now_value
        ]
        if len({_fact_signature(fact) for fact in current}) > 1:
            result.append(_missing_fact(stage, scope, observed_text, fresh_text, "EVIDENCE_CONFLICT"))
            result[-1]["reasonText"] = "同一阶段存在相互冲突的当前证据"
            continue
        selected_pool = current or candidates
        selected = max(selected_pool, key=lambda fact: fact["observedAt"])
        result.append({**selected, "isStale": not bool(current)})
    return result
