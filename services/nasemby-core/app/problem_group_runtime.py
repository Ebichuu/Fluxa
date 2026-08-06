"""任务链问题组的纯派生逻辑。

问题组只回答“哪些资源因为同一个原因需要一起处理”，不改变任务身份、事实
阶段或历史事件。调用方可以把同一份投影用于首页和任务中心。
"""

import hashlib
import unicodedata


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_integer(value):
    parsed = _integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _reliable_identity(item: dict) -> bool:
    if str(item.get("identityState") or "") != "linked":
        return False
    media_type = str(item.get("mediaType") or "").strip().lower()
    if media_type not in {"movie", "tv"} or _positive_integer(item.get("tmdbId")) is None:
        return False
    return media_type == "movie" or _positive_integer(item.get("seasonNumber")) is not None


def _mechanical_title_key(value) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )


def _resource_ref(item: dict) -> str:
    return str(item.get("chainId") or item.get("targetKey") or item.get("id") or "").strip()


def _pipeline_outcome(item: dict):
    outcome = item.get("pipelineOutcome")
    if not isinstance(outcome, dict):
        return None
    if not str(outcome.get("stage") or "").strip():
        return None
    if not str(outcome.get("reasonCode") or "").strip():
        return None
    return outcome


def _stage_reason(item: dict) -> tuple[str, str]:
    outcome = _pipeline_outcome(item)
    if outcome is not None:
        return (
            str(outcome.get("stage") or "").strip().lower(),
            str(outcome.get("reasonCode") or "").strip().upper(),
        )
    stage = str(item.get("currentStep") or "").strip().lower()
    reason_code = str(item.get("reasonCode") or "").strip().upper()
    return stage, reason_code


def _reason_text(item: dict) -> str:
    outcome = _pipeline_outcome(item)
    if outcome is not None:
        return str(outcome.get("reasonText") or "当前任务需要处理")
    return str(item.get("userReasonText") or item.get("reasonText") or "当前任务需要处理")


def _group_key(item: dict):
    media_type = str(item.get("mediaType") or "").strip().lower()
    tmdb_id = _positive_integer(item.get("tmdbId"))
    season = _positive_integer(item.get("seasonNumber"))
    stage, reason_code = _stage_reason(item)
    resource_ref = _resource_ref(item)

    # 没有阶段或原因时不能把不同故障猜成同一组。
    if not stage or not reason_code:
        return ("resource", resource_ref)
    if _reliable_identity(item):
        identity = (
            f"movie:tmdb:{tmdb_id}"
            if media_type == "movie"
            else f"tv:tmdb:{tmdb_id}:season:{season}"
        )
        return ("identity", identity, stage, reason_code)

    # 冲突身份、媒体类型/季数不完整或无标题时逐资源处理。
    title_key = _mechanical_title_key(item.get("title"))
    if (
        str(item.get("identityState") or "") == "conflict"
        or media_type not in {"movie", "tv"}
        or (media_type == "tv" and season is None)
        or not title_key
    ):
        return ("resource", resource_ref)
    display = f"{media_type}:{title_key}"
    if media_type == "tv":
        display += f":season:{season}"
    return ("display", display, stage, reason_code)


def _member_sort_key(item: dict):
    episode = _positive_integer(item.get("episodeNumber"))
    return (
        episode if episode is not None else 10**9,
        str(item.get("title") or ""),
        _resource_ref(item),
    )


def _group_id(key) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in key).encode("utf-8")).hexdigest()[:16]
    return f"pg:{digest}"


def _is_action_required(item: dict) -> bool:
    outcome = item.get("pipelineOutcome") if isinstance(item.get("pipelineOutcome"), dict) else {}
    return str(item.get("outcomeState") or outcome.get("state") or "") == "action_required"


def derive_problem_groups(items) -> dict:
    """返回全部需要处理资源的稳定问题组及三项共享计数。"""
    buckets = {}
    for item in items or []:
        if not isinstance(item, dict) or not _is_action_required(item):
            continue
        buckets.setdefault(_group_key(item), []).append(item)

    groups = []
    for key, members in sorted(buckets.items(), key=lambda row: tuple(str(part) for part in row[0])):
        ordered_members = sorted(members, key=_member_sort_key)
        primary = ordered_members[0]
        media_type = str(primary.get("mediaType") or "unknown").strip().lower()
        tmdb_id = str(primary.get("tmdbId") or "").strip()
        season = _positive_integer(primary.get("seasonNumber"))
        stage, reason_code = _stage_reason(primary)
        episodes = sorted({
            episode for member in ordered_members
            if (episode := _positive_integer(member.get("episodeNumber"))) is not None
        })
        groups.append({
            "groupId": _group_id(key),
            "title": str(primary.get("title") or "未命名媒体"),
            "mediaType": media_type,
            "tmdbId": tmdb_id,
            "seasonNumber": season or 0,
            "stage": stage,
            "reasonCode": reason_code,
            "reasonText": _reason_text(primary),
            "resourceCount": len(ordered_members),
            "identityUnconfirmedResources": sum(not _reliable_identity(member) for member in ordered_members),
            "episodeNumbers": episodes,
            "memberChainIds": [_resource_ref(member) for member in ordered_members if _resource_ref(member)],
            "members": [{
                "chainId": _resource_ref(member),
                "targetKey": str(member.get("targetKey") or ""),
                "title": str(member.get("title") or "未命名媒体"),
                "mediaType": str(member.get("mediaType") or "unknown"),
                "tmdbId": str(member.get("tmdbId") or ""),
                "seasonNumber": _positive_integer(member.get("seasonNumber")) or 0,
                "episodeNumber": _positive_integer(member.get("episodeNumber")) or 0,
                "identityState": str(member.get("identityState") or "unidentified"),
                "reasonCode": str(member.get("reasonCode") or reason_code),
                "reasonText": str(member.get("reasonText") or ""),
                "userReasonText": str(member.get("userReasonText") or ""),
                "resultText": str(member.get("resultText") or ""),
                "pipelineOutcome": dict(member.get("pipelineOutcome") or {}),
                "primaryAction": dict(member.get("primaryAction") or {}),
            } for member in ordered_members],
        })

    return {
        "groups": groups,
        "summary": {
            "actionRequiredGroups": len(groups),
            "actionRequiredResources": sum(group["resourceCount"] for group in groups),
            "actionRequiredIdentityUnconfirmedResources": sum(
                group["identityUnconfirmedResources"] for group in groups
            ),
        },
    }
