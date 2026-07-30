STATISTIC_CONFIRMATIONS = {"confirmed", "partial", "unknown"}


def statistic_metadata(*, scope: str, unit: str, observed_at: str, confirmation: str) -> dict:
    """构造公开统计元数据，不改变对应数值的兼容字段。"""
    normalized_confirmation = str(confirmation or "unknown").strip().lower()
    if normalized_confirmation not in STATISTIC_CONFIRMATIONS:
        raise ValueError("统计确认状态无效")
    return {
        "scope": str(scope or "").strip(),
        "unit": str(unit or "").strip(),
        "observedAt": str(observed_at or "").strip(),
        "confirmation": normalized_confirmation,
    }
