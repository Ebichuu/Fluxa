from __future__ import annotations

from app.task_exception_runtime import protection_rule


LOW_SCORE_PROTECTION_CODES = {
    "QUALITY_SCORE_LOWER",
    "QUALITY_WEIGHT_NOT_HIGHER",
    "QUALITY_HIGHER_VERSION_EXISTS",
}
VERSION_RULE_PROTECTION_CODES = {
    "QUALITY_VERSION_RULE_NOT_MATCHED",
}
CANCELLED_OVERRIDE_CODES = {
    "QUALITY_OVERWRITE_CANCELLED",
    "QUALITY_OVERWRITE_SKIPPED",
}
SYMEDIA_PROTECTION_CODES = (
    LOW_SCORE_PROTECTION_CODES
    | VERSION_RULE_PROTECTION_CODES
    | CANCELLED_OVERRIDE_CODES
)


def normalize_symedia_status(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def symedia_protection_rule(row: dict) -> str:
    rule = protection_rule(row.get("reasonCode"), row.get("errmsg"))
    return rule if rule in SYMEDIA_PROTECTION_CODES else ""


def is_successful_replacement(row: dict) -> bool:
    if normalize_symedia_status(row.get("status")) is not True:
        return False
    source = row.get("src_detail") if isinstance(row.get("src_detail"), dict) else {}
    target = row.get("dst_detail") if isinstance(row.get("dst_detail"), dict) else {}
    source_score = source.get("totalScore")
    target_score = target.get("totalScore")
    if isinstance(source_score, bool) or isinstance(target_score, bool):
        return False
    return (
        isinstance(source_score, (int, float))
        and isinstance(target_score, (int, float))
        and source_score > target_score
    )


def is_low_score_protection(row: dict) -> bool:
    return symedia_protection_rule(row) in LOW_SCORE_PROTECTION_CODES


def is_version_rule_protection(row: dict) -> bool:
    return symedia_protection_rule(row) in VERSION_RULE_PROTECTION_CODES


def is_cancelled_override(row: dict) -> bool:
    return symedia_protection_rule(row) in CANCELLED_OVERRIDE_CODES


def symedia_outcome(row: dict) -> str:
    status = normalize_symedia_status(row.get("status"))
    if status is True:
        return "replaced" if is_successful_replacement(row) else "archived"
    if status is False:
        return "protected" if symedia_protection_rule(row) else "failed"
    return "evidence_insufficient"
