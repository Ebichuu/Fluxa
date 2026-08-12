from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

from flask import Flask, Response, jsonify, request

from app import discover_runtime
from app.contract_mapping import map_calendar_payload
from app.http_runtime import current_request_id
from app.statistic_metadata_runtime import statistic_metadata
from app.calendar_snapshot_repository import CalendarRefreshConflict, normalize_scope
from app.task_exception_runtime import protection_rule
from app.task_public_runtime import (
    present_pipeline_fact,
    present_pipeline_outcome,
    public_subscription_ref,
)


ALLOWED_MEDIA_TYPES = {"all", "movie", "tv"}
ALLOWED_VIEWS = {"", "summary", "detail"}
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_CACHE_TTL_SECONDS = 300


def _text(value) -> str:
    return str(value or "").strip()


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _as_datetime(value):
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text[:19], pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(timezone.utc)


def _parse_date(value) -> date | None:
    try:
        return datetime.strptime(_text(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _month_keys(start: date, end: date) -> list[tuple[int, int]]:
    values = []
    current = date(start.year, start.month, 1)
    while current <= end:
        values.append((current.year, current.month))
        current = date(current.year + (1 if current.month == 12 else 0), 1 if current.month == 12 else current.month + 1, 1)
    return values


def _subscription_keys(*values) -> set[str]:
    keys = set()
    for value in values:
        raw = _text(value)
        if not raw:
            continue
        keys.add(raw)
        public = public_subscription_ref(raw)
        if public:
            keys.add(public)
    return keys


def _task_subscription_keys(item: dict) -> set[str]:
    source_ids = item.get("sourceIds") or {}
    return _subscription_keys(
        item.get("subscriptionId"),
        source_ids.get("subscriptionId"),
        *(source_ids.get("subscriptionIds") or []),
    )


def _matches_identity(entry: dict, item: dict) -> bool:
    entry_tmdb = _text(entry.get("tmdbId"))
    item_tmdb = _text(item.get("tmdbId"))
    if entry_tmdb and item_tmdb:
        if entry_tmdb != item_tmdb:
            return False
    else:
        entry_key = _text(entry.get("key"))
        if not entry_key or _subscription_keys(entry_key).isdisjoint(_task_subscription_keys(item)):
            return False
    entry_media = _text(entry.get("mediaType"))
    if entry_media and entry_media != _text(item.get("mediaType")):
        return False
    entry_season = _integer(entry.get("seasonNumber"))
    item_season = _integer(item.get("seasonNumber"))
    return not (
        entry_media == "tv"
        and entry_season
        and item_season
        and entry_season != item_season
    )


def _match_rank(entry: dict, item: dict) -> tuple[int, str]:
    same_subscription = not _subscription_keys(entry.get("key")).isdisjoint(_task_subscription_keys(item))
    return (0 if same_subscription else 1, _text(item.get("updatedAt")))


def _empty_task() -> dict:
    return {
        "chainId": "",
        "targetKey": "",
        "healthState": "evidence_insufficient",
        "reasonCode": "CALENDAR_TASK_NOT_FOUND",
        "reasonText": "尚未形成可关联的任务链",
        "observedAt": "",
        "freshUntil": "",
        "acquiredAt": "",
        "acquisitionSource": "",
        "libraryAt": "",
        "librarySource": "",
        "strmAt": "",
        "strmSource": "",
        "playableAt": "",
        "firstConfirmedPlayableAt": "",
        "playableSource": "",
        "outcomeState": "evidence_insufficient",
        "pipelineOutcome": present_pipeline_outcome(None),
        "torraFact": None,
    }


def _task_matches_exact_target(entry: dict, item: dict) -> bool:
    if not _matches_identity(entry, item):
        return False
    if _text(entry.get("mediaType")) != "tv":
        return _text(item.get("mediaType")) == "movie"
    return (
        _integer(item.get("seasonNumber")) == _integer(entry.get("seasonNumber"))
        and _integer(item.get("episodeNumber")) == _integer(entry.get("episodeNumber"))
        and _integer(entry.get("episodeNumber")) > 0
    )


def _event_covers_entry(event: dict, entry: dict) -> bool:
    episode = _integer(entry.get("episodeNumber"))
    return bool(
        episode > 0
        and _integer(event.get("seasonNumber")) == _integer(entry.get("seasonNumber"))
        and _integer(event.get("episodeStart")) <= episode <= _integer(event.get("episodeEnd"))
        and (
            event.get("kind") in {"pipeline_fact", "pipeline_fact_unit"}
            or _text(event.get("ownerTargetKey"))
        )
    )


def _snapshot_episode_events(item: dict) -> list[dict]:
    stage_names = {"download": "qb", "library": "symedia", "strm": "strm", "emby": "emby"}
    statuses = {"done": "succeeded", "blocked": "failed", "active": "active", "waiting": "waiting"}
    result = []
    for row in item.get("episodeEvidence") or []:
        if not isinstance(row, dict) or not row.get("ownerTargetKey"):
            continue
        stage = stage_names.get(_text(row.get("stage")))
        status = statuses.get(_text(row.get("status")))
        if not stage or not status or not row.get("eventAt"):
            continue
        result.append({
            **row,
            "kind": "episode_evidence",
            "stage": stage,
            "status": status,
            "eventAt": _text(row.get("eventAt")),
        })
    for fact in item.get("pipelineFacts") or []:
        if not isinstance(fact, dict) or fact.get("stage") not in {"strm", "emby"}:
            continue
        for unit in fact.get("units") or []:
            match = re.fullmatch(r"tv:[^:]+:s(\d+):e(\d+)", _text(unit.get("unitKey")))
            if not match or unit.get("evidence") != "verified":
                continue
            episode = int(match.group(2))
            result.append({
                "kind": "pipeline_fact_unit",
                "currentObservation": True,
                "stage": fact.get("stage"),
                "status": unit.get("state"),
                "seasonNumber": int(match.group(1)),
                "episodeStart": episode,
                "episodeEnd": episode,
                "eventAt": _text(unit.get("eventAt")),
                "observedAt": _text(unit.get("observedAt")),
                "freshUntil": _text(unit.get("freshUntil")),
                "source": _text(fact.get("source")),
                "reasonCode": _text(unit.get("reasonCode")),
                "reasonText": _text(unit.get("reasonText")),
            })
    return result


def _history_episode_events(item: dict, repository) -> list[dict]:
    if not repository or not callable(getattr(repository, "list_episode_events", None)):
        return []
    try:
        rows = repository.list_episode_events(item.get("chainId"), limit=1000)
        if _integer(item.get("episodeNumber")) <= 0:
            return rows
        result = []
        for row in rows:
            value = dict(row)
            if value.get("kind") == "pipeline_fact":
                value.update({
                    "seasonNumber": _integer(item.get("seasonNumber")),
                    "episodeStart": _integer(item.get("episodeNumber")),
                    "episodeEnd": _integer(item.get("episodeNumber")),
                })
            result.append(value)
        return result
    except Exception:
        return []


def _episode_events(item: dict, repository) -> list[dict]:
    merged = {}
    for row in [*_history_episode_events(item, repository), *_snapshot_episode_events(item)]:
        if not isinstance(row, dict):
            continue
        key = (
            _text(row.get("kind")), _text(row.get("stage")), _text(row.get("status")),
            _integer(row.get("seasonNumber")), _integer(row.get("episodeStart")),
            _integer(row.get("episodeEnd")), _text(row.get("eventAt")),
            _text(row.get("ownerTargetKey")),
        )
        merged[key] = row
    return list(merged.values())


def _event_index_key(item: dict) -> str:
    return _text(item.get("chainId") or item.get("targetKey"))


def _episode_event_index(items: list[dict], repository) -> dict[str, list[dict]]:
    result = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _event_index_key(item)
        if key and key not in result:
            result[key] = _episode_events(item, repository)
    return result


def _current_pipeline_facts(item: dict, current: datetime) -> list[dict]:
    result = []
    for fact in item.get("pipelineFacts") or []:
        if not isinstance(fact, dict) or fact.get("evidence") != "verified" or fact.get("isStale") is True:
            continue
        deadline = _as_datetime(fact.get("freshUntil"))
        if deadline is None or deadline < current.astimezone(timezone.utc):
            continue
        result.append(fact)
    return result


def _latest_fact(facts: list[dict], stages: set[str], states: set[str]) -> dict | None:
    candidates = [
        fact for fact in facts
        if _text(fact.get("stage")) in stages and _text(fact.get("state")) in states
    ]
    return max(candidates, key=lambda row: _text(row.get("observedAt"))) if candidates else None


def _event_time(event: dict | None) -> tuple[str, str]:
    if not event:
        return "", ""
    return _text(event.get("eventAt") or event.get("observedAt")), _text(event.get("source"))


def _stage_event(events: list[dict], entry: dict, stage: str, status: str, *, first=False) -> dict | None:
    candidates = [
        event for event in events
        if _text(event.get("stage")) == stage
        and _text(event.get("status")) == status
        and _event_covers_entry(event, entry)
        and _text(event.get("eventAt") or event.get("observedAt"))
    ]
    if not candidates:
        return None
    selector = min if first else max
    return selector(candidates, key=lambda row: _text(row.get("eventAt") or row.get("observedAt")))


def _current_episode_success(events: list[dict], entry: dict, stage: str, current: datetime) -> dict | None:
    candidates = []
    for event in events:
        if _text(event.get("kind")) != "pipeline_fact_unit" or not event.get("currentObservation"):
            continue
        if _text(event.get("stage")) != stage or _text(event.get("status")) != "succeeded":
            continue
        if not _event_covers_entry(event, entry):
            continue
        fresh_until = _as_datetime(event.get("freshUntil"))
        if fresh_until and fresh_until >= current.astimezone(timezone.utc):
            candidates.append(event)
    return max(candidates, key=lambda row: _text(row.get("observedAt"))) if candidates else None


def _public_task(entry: dict, items: list[dict], current: datetime, repository=None, event_index=None) -> dict:
    candidates = [
        item for item in items
        if isinstance(item, dict) and _matches_identity(entry, item)
    ]
    event_index = event_index or {
        _event_index_key(item): _episode_events(item, repository)
        for item in candidates
    }
    matches = [
        item for item in candidates
        if _task_matches_exact_target(entry, item)
        or any(_event_covers_entry(event, entry) for event in event_index.get(_event_index_key(item), []))
    ]
    if not matches:
        return _empty_task()
    item = sorted(matches, key=lambda row: _match_rank(entry, row))[0]
    episode_events = event_index.get(_event_index_key(item), [])
    facts = _current_pipeline_facts(item, current)
    acquired_event = _stage_event(episode_events, entry, "qb", "succeeded")
    library_event = _stage_event(episode_events, entry, "symedia", "succeeded")
    strm_event = _stage_event(episode_events, entry, "strm", "succeeded")
    playable_event = _stage_event(episode_events, entry, "emby", "succeeded", first=True)
    acquired_fact = _latest_fact(facts, {"qb"}, {"active", "succeeded"}) if _task_matches_exact_target(entry, item) else None
    library_fact = _latest_fact(facts, {"symedia"}, {"succeeded"}) if _task_matches_exact_target(entry, item) else None
    playable_fact = _latest_fact(facts, {"emby"}, {"succeeded"}) if _task_matches_exact_target(entry, item) else None
    if playable_fact and _text(entry.get("mediaType")) == "tv" and playable_fact.get("scope") != "episode":
        playable_fact = None
    acquired_at, acquisition_source = _event_time(acquired_event or acquired_fact)
    library_at, library_source = _event_time(library_event or library_fact)
    strm_at, strm_source = _event_time(strm_event)
    playable_at, playable_source = _event_time(playable_event or playable_fact)
    pipeline_outcome = present_pipeline_outcome(item.get("pipelineOutcome"))
    current_episode_playable = _current_episode_success(episode_events, entry, "emby", current)
    if current_episode_playable:
        pipeline_outcome = {
            "state": "playable",
            "stage": "emby",
            "reasonCode": "EMBY_EPISODE_INDEXED",
            "reasonText": "Emby 已收录目标集",
            "observedAt": _text(current_episode_playable.get("observedAt")),
            "playableAt": playable_at,
        }
    elif pipeline_outcome["state"] == "playable" and not playable_fact:
        pipeline_outcome = present_pipeline_outcome(None)
    outcome_state = pipeline_outcome["state"]
    health_state = {
        "playable": "normal",
        "action_required": "action_required",
        "in_progress": "waiting",
        "waiting": "waiting",
        "protected": "protected",
        "evidence_insufficient": "evidence_insufficient",
    }.get(outcome_state, "evidence_insufficient")
    torra = next((fact for fact in facts if fact.get("stage") == "torra"), None)
    latest_failure = max((
        event for event in episode_events
        if _event_covers_entry(event, entry) and event.get("status") == "failed"
    ), key=lambda row: _text(row.get("eventAt")), default=None)
    recovered = bool(latest_failure and any(
        event.get("stage") == latest_failure.get("stage")
        and event.get("artifactKey")
        and event.get("artifactKey") == latest_failure.get("artifactKey")
        and event.get("status") in {"succeeded", "recovered"}
        and _event_covers_entry(event, entry)
        and _text(event.get("eventAt")) > _text(latest_failure.get("eventAt"))
        for event in episode_events
    ))
    if latest_failure and not recovered and outcome_state != "action_required":
        health_state = "evidence_insufficient"
    common = {
        "chainId": _text(item.get("chainId")),
        "targetKey": _text(item.get("targetKey")),
        "freshUntil": _text(item.get("freshUntil")),
    }
    return {
        **common,
        "healthState": health_state,
        "reasonCode": (
            "HISTORICAL_FAILURE_CURRENT_UNKNOWN"
            if latest_failure and not recovered and outcome_state != "action_required"
            else pipeline_outcome["reasonCode"]
        ),
        "reasonText": (
            f"曾于 {_text(latest_failure.get('eventAt'))} 失败 · 当前状态暂未确认"
            if latest_failure and not recovered and outcome_state != "action_required"
            else pipeline_outcome["reasonText"]
        ),
        "observedAt": pipeline_outcome["observedAt"],
        "acquiredAt": acquired_at,
        "acquisitionSource": acquisition_source,
        "libraryAt": library_at,
        "librarySource": library_source,
        "strmAt": strm_at,
        "strmSource": strm_source,
        "playableAt": playable_at,
        "firstConfirmedPlayableAt": playable_at,
        "playableSource": playable_source,
        "outcomeState": outcome_state,
        "pipelineOutcome": pipeline_outcome,
        "torraFact": present_pipeline_fact(torra) if torra else None,
    }


def _normalize_entry_evidence(entry: dict) -> dict:
    value = dict(entry)
    library_at = _as_datetime(value.get("libraryAt"))
    acquired_at = _as_datetime(value.get("acquiredAt"))
    value["inLibrary"] = bool(library_at)
    if acquired_at and library_at and acquired_at > library_at:
        value["acquiredAt"] = ""
        value["acquisitionSource"] = ""
    return value


def _entry_status(entry: dict, today: str) -> str:
    current = datetime.now(timezone.utc)
    if entry.get("linkState") == "unlinked":
        return "unlinked"
    if entry.get("playableAt") and entry.get("outcomeState") == "playable":
        return "playable"
    if entry.get("inLibrary") or entry.get("libraryAt"):
        return "library"
    if entry.get("acquiredAt"):
        return "acquiring"
    if _text(entry.get("healthState")) == "protected" or protection_rule(
        entry.get("reasonCode"), entry.get("reasonText")
    ):
        return "protected"
    if _text(entry.get("date")) >= today:
        return "upcoming"
    if not entry.get("followScopeExplicit"):
        return "unknown"
    created_at = _as_datetime(entry.get("subscriptionCreatedAt"))
    aired_at = _as_datetime(entry.get("airAt")) or _as_datetime(entry.get("date"))
    if not created_at and not entry.get("includePastEpisodes"):
        return "unknown"
    if created_at and aired_at and aired_at < created_at and not entry.get("includePastEpisodes"):
        return "unknown"
    fresh_until = _as_datetime(entry.get("freshUntil"))
    if fresh_until and fresh_until < current:
        return "unknown"
    delay_hours = max(0, _integer(entry.get("allowedDelayHours"), 24))
    if aired_at and current < aired_at + timedelta(hours=delay_hours):
        return "unknown"
    return "missing"


def _is_pre_subscription_episode(entry: dict) -> bool:
    if entry.get("includePastEpisodes"):
        return False
    created_at = _as_datetime(entry.get("subscriptionCreatedAt"))
    entry_date = _parse_date(entry.get("date"))
    if created_at and entry_date:
        return entry_date < created_at.astimezone(BEIJING_TZ).date()
    aired_at = _as_datetime(entry.get("airAt")) or _as_datetime(entry.get("date"))
    return bool(created_at and aired_at and aired_at < created_at)


def _calendar_link_state(entry: dict) -> str:
    explicit_scope = bool(entry.get("followScopeExplicit"))
    if _text(entry.get("mediaType")) == "tv":
        explicit_scope = explicit_scope and _integer(entry.get("seasonNumber")) >= 0 and _integer(entry.get("episodeNumber")) > 0
    if not explicit_scope or entry.get("migrationReview"):
        return "unlinked"
    if _text(entry.get("subscriptionOrigin")) == "manual":
        return "manual"
    torra_fact = entry.get("torraFact") or {}
    if (
        entry.get("torraLinked")
        or _text(entry.get("subscriptionOrigin")) == "torra"
        or _text(entry.get("sourceLabel")) == "Torra 只读追更"
        or (
            torra_fact.get("evidence") == "verified"
            and torra_fact.get("state") not in {"", "unknown"}
        )
    ):
        return "linked"
    return "unlinked"


def _summary_calendar(calendar: dict, current: datetime) -> dict:
    today = current.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
    grouped = {}
    for entry in calendar.get("entries") or []:
        grouped.setdefault(_text(entry.get("date")), []).append(entry)
    days = []
    for date_key in sorted(grouped):
        entries = grouped[date_key]
        status_counts = {
            state: sum(_entry_status(entry, today) == state for entry in entries)
            for state in (
                "upcoming", "acquiring", "library", "playable", "protected", "missing", "unknown", "unlinked",
            )
        }
        days.append({
            "date": date_key,
            "total": len(entries),
            "statusCounts": status_counts,
            "preview": [{
                "key": entry.get("key"),
                "title": entry.get("title"),
                "episodeLabel": entry.get("episodeLabel"),
                "posterUrl": entry.get("posterUrl"),
                "mediaType": entry.get("mediaType"),
                "healthState": entry.get("healthState"),
                "status": _entry_status(entry, today),
            } for entry in entries[:3]],
            "hasMore": len(entries) > 3,
        })
    return {
        **calendar,
        "entries": [],
        "days": days,
        "searchIndex": [{
            "date": entry.get("date"),
            "key": entry.get("key"),
            "title": entry.get("title"),
            "episodeLabel": entry.get("episodeLabel"),
            "mediaType": entry.get("mediaType"),
            "status": _entry_status(entry, today),
        } for entry in calendar.get("entries") or []],
        "view": "summary",
    }


def _detail_calendar_stats(base, entries, all_entries, include_unlinked, excluded_before):
    status_counts = {
        state: 0
        for state in (
            "upcoming", "acquiring", "library", "playable", "protected", "missing", "unknown", "unlinked",
        )
    }
    unlinked = sum(entry.get("linkState") == "unlinked" for entry in all_entries)
    titles = set()
    counts = {
        "inLibrary": 0,
        "pending": 0,
        "acquired": 0,
        "libraryEvidence": 0,
        "playable": 0,
        "actionRequired": 0,
    }
    for entry in entries:
        titles.add(_text(entry.get("key")) or _text(entry.get("title")))
        counts["inLibrary"] += int(bool(entry.get("inLibrary")))
        counts["pending"] += int(not bool(entry.get("inLibrary")))
        counts["acquired"] += int(bool(entry.get("acquiredAt")))
        counts["libraryEvidence"] += int(bool(entry.get("libraryAt")))
        counts["playable"] += int(entry.get("status") == "playable")
        counts["actionRequired"] += int(entry.get("healthState") == "action_required")
        status = _text(entry.get("status"))
        if status in status_counts:
            status_counts[status] += 1
    return {
        **(base or {}),
        "entries": len(entries),
        "titles": len(titles),
        **counts,
        "unlinked": unlinked,
        "excludedUnlinked": 0 if include_unlinked else unlinked,
        "linkedEntries": len(all_entries) - unlinked,
        "unlinkedEntries": unlinked,
        "totalEntries": len(all_entries),
        "excludedBeforeSubscription": int(excluded_before or 0),
        "statusCounts": status_counts,
    }


def _torra_calendar_source_item(row: dict) -> dict | None:
    if not isinstance(row, dict) or _text(row.get("reconciliationState")) != "only_torra":
        return None
    media_type = _text(row.get("mediaType"))
    tmdb_id = _text(row.get("tmdbId"))
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        return None
    season = _integer(row.get("seasonNumber")) if media_type == "tv" else 0
    remote_ref = _text(row.get("remoteRef")) or tmdb_id
    return {
        "subscription_key": f"torra:{remote_ref}",
        "title": _text(row.get("title")),
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "target_season": season,
        "season_number": season,
        "source": "torra",
        "source_label": "Torra 只读追更",
        "subscription_origin": "torra",
        "torra_linked": True,
        "read_only": True,
        # 远端创建时间不在公开订阅响应中，使用本次可靠读取时间避免把历史集误判为缺集。
        "subscribed_at": _text(row.get("observedAt")),
        "follow_scope_explicit": True,
        "include_past_episodes": False,
        "allowed_delay_hours": 24,
        "in_library": False,
    }


def _calendar_entry_identity(entry: dict) -> tuple | None:
    date_key = _text(entry.get("date"))
    media_type = _text(entry.get("mediaType"))
    tmdb_id = _text(entry.get("tmdbId"))
    if not _parse_date(date_key) or media_type not in {"movie", "tv"}:
        return None
    if not tmdb_id.isdigit() or int(tmdb_id) <= 0:
        return None
    season_number = _integer(entry.get("seasonNumber"))
    episode_number = _integer(entry.get("episodeNumber"))
    if media_type == "tv" and (season_number < 0 or episode_number <= 0):
        return None
    return (
        date_key,
        media_type,
        tmdb_id,
        season_number if media_type == "tv" else 0,
        episode_number if media_type == "tv" else 0,
    )


def _calendar_entry_priority(entry: dict) -> tuple:
    origin = _text(entry.get("subscriptionOrigin"))
    source_label = _text(entry.get("sourceLabel"))
    if origin == "manual":
        origin_rank = 0
    elif origin == "torra" or source_label == "Torra 只读追更":
        origin_rank = 2
    else:
        origin_rank = 1
    completeness = sum(bool(entry.get(field)) for field in (
        "key", "title", "posterUrl", "seasonName", "episodeLabel", "episodeTitle", "subscriptionCreatedAt",
    ))
    return (
        origin_rank,
        -completeness,
        _text(entry.get("key")),
        source_label,
        _text(entry.get("title")),
    )


def _ordered_unique(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = _text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _earliest_calendar_time(entries: list[dict], field: str) -> str:
    values = []
    for entry in entries:
        raw = _text(entry.get(field))
        parsed = _as_datetime(raw)
        if raw and parsed:
            values.append((parsed, raw))
    return min(values, key=lambda row: row[0])[1] if values else ""


def _merge_calendar_group(entries: list[dict]) -> dict:
    ordered = sorted(entries, key=_calendar_entry_priority)
    merged = dict(ordered[0])
    for field in (
        "key", "title", "posterUrl", "seasonName", "episodeLabel", "episodeTitle", "progressText",
    ):
        if not merged.get(field):
            merged[field] = next((_text(entry.get(field)) for entry in ordered if _text(entry.get(field))), "")
    merged["torraLinked"] = any(bool(entry.get("torraLinked")) for entry in ordered)
    merged["followScopeExplicit"] = any(bool(entry.get("followScopeExplicit")) for entry in ordered)
    merged["includePastEpisodes"] = any(bool(entry.get("includePastEpisodes")) for entry in ordered)
    merged["inLibrary"] = any(bool(entry.get("inLibrary")) for entry in ordered)
    merged["migrationReview"] = all(bool(entry.get("migrationReview")) for entry in ordered)
    merged["allowedDelayHours"] = max((_integer(entry.get("allowedDelayHours"), 24) for entry in ordered), default=24)
    merged["subscriptionCreatedAt"] = _earliest_calendar_time(ordered, "subscriptionCreatedAt")
    merged["libraryPaths"] = _ordered_unique(
        path for entry in ordered for path in (entry.get("libraryPaths") or [])
    )
    source_records = []
    seen_sources = set()
    for entry in ordered:
        record = (
            _text(entry.get("key")),
            _text(entry.get("sourceLabel")),
            _text(entry.get("subscriptionOrigin")),
        )
        if not any(record) or record in seen_sources:
            continue
        seen_sources.add(record)
        source_records.append(record)
    merged["sourceKeys"] = _ordered_unique(record[0] for record in source_records)
    merged["sourceLabels"] = _ordered_unique(record[1] for record in source_records)
    merged["sourceOrigins"] = _ordered_unique(record[2] for record in source_records)
    merged["sourceCount"] = max(1, len(source_records))
    if not merged.get("sourceLabel") and merged["sourceLabels"]:
        merged["sourceLabel"] = merged["sourceLabels"][0]
    if not merged.get("subscriptionOrigin") and merged["sourceOrigins"]:
        merged["subscriptionOrigin"] = merged["sourceOrigins"][0]
    return merged


def _merge_calendar_entries(entries: list[dict]) -> list[dict]:
    grouped = {}
    unlinked = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identity = _calendar_entry_identity(entry)
        if identity is None:
            unlinked.append(_merge_calendar_group([entry]))
            continue
        grouped.setdefault(identity, []).append(entry)
    merged = [_merge_calendar_group(group) for _, group in sorted(grouped.items())]
    merged.extend(unlinked)
    return merged


class CalendarTimelineService:
    def __init__(self, app: Flask, calendar_loader=None, clock=None, repository=None):
        self.app = app
        self.calendar_loader = calendar_loader or discover_runtime.build_subscription_calendar
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.repository = repository
        self._torra_cache = {}
        self._torra_cache_lock = threading.RLock()
        self._snapshot_cache = {}
        self._snapshot_cache_lock = threading.RLock()
        self._snapshot_build_locks = {}
        self._date_snapshot_cache = {}

    def _read_cached_snapshot(self, year: int, month: int, media_type: str, include_unlinked=False) -> dict | None:
        key = (int(year), int(month), str(media_type or "all"), bool(include_unlinked))
        with self._snapshot_cache_lock:
            cached = self._snapshot_cache.get(key)
            if not cached or time.monotonic() - cached[0] >= SNAPSHOT_CACHE_TTL_SECONDS:
                self._snapshot_cache.pop(key, None)
                return None
            return deepcopy(cached[1])

    def cached_snapshot(self, year: int, month: int, media_type: str, include_unlinked=False) -> dict | None:
        cached = self._read_cached_snapshot(year, month, media_type, include_unlinked)
        if cached:
            cached.pop("_allEntries", None)
            cached.pop("_excludedBeforeByDate", None)
        return cached

    @staticmethod
    def _cache_confirmation(calendar: dict, payload: dict) -> str:
        if not payload.get("calendar"):
            return "unknown"
        if calendar.get("errorCount") or calendar.get("errors") or any(
            entry.get("status") in {"unknown", "unlinked"}
            for entry in calendar.get("entries") or []
            if isinstance(entry, dict)
        ):
            return "partial"
        return "confirmed"

    def build_cache_payload(self, year: int, month: int, media_type: str, *, include_unlinked=False) -> dict:
        """Build one scope outside SQLite; the refresh worker persists the result."""
        self._build_snapshot(
            year,
            month,
            media_type,
            include_unlinked=include_unlinked,
            current=self.clock(),
        )
        key = (int(year), int(month), str(media_type or "all"), bool(include_unlinked))
        with self._snapshot_cache_lock:
            cached = self._snapshot_cache.get(key)
            if not cached:
                raise RuntimeError("日历快照未生成")
            payload = deepcopy(cached[1])
        payload["confirmation"] = self._cache_confirmation(payload.get("calendar") or {}, payload)
        return payload

    @staticmethod
    def _cache_meta(row, *, queue=None):
        return {
            "status": "fresh" if row and row.get("isFresh") else "stale" if row else "cold",
            "confirmation": row.get("effectiveConfirmation") if row else "unknown",
            "observedAt": row.get("observedAt", "") if row else "",
            "freshUntil": row.get("freshUntil", "") if row else "",
            "lastSuccessAt": row.get("lastSuccessAt", "") if row else "",
            "lastAttemptAt": row.get("lastAttemptAt", "") if row else "",
            "lastErrorCode": row.get("lastErrorCode", "") if row else "",
            "lastErrorText": row.get("lastErrorText", "") if row else "",
            "refresh": {
                "status": queue.get("status") if queue else "idle",
                "running": bool(queue and queue.get("status") == "running"),
                "requestedAt": queue.get("lastRequestedAt", "") if queue else "",
            },
        }

    def _empty_cached_response(self, year, month, media_type, include_unlinked, *, view="", meta=None):
        calendar = {
            "year": int(year), "month": int(month), "mediaType": media_type,
            "timeZone": "Asia/Shanghai", "includeUnlinked": bool(include_unlinked),
            "entries": [], "errors": [], "errorCount": 0,
            "stats": {
                "entries": None, "titles": None, "inLibrary": None, "pending": None,
                "linkedEntries": None, "unlinkedEntries": None, "totalEntries": None,
            }, "view": view or "legacy",
        }
        return {"ok": True, "version": "", "confirmation": "unknown", "cache": meta or {"status": "cold", "confirmation": "unknown"}, "calendar": calendar}

    def read_cached(self, year, month, media_type="all", *, view="", start=None, end=None, detail_date=None, include_unlinked=False):
        if not self.repository:
            return self.snapshot(
                year, month, media_type, view=view, start=start, end=end,
                detail_date=detail_date, include_unlinked=include_unlinked,
            )
        current = self.clock()
        if detail_date:
            year, month = detail_date.year, detail_date.month
        if start is None and end is None:
            start = detail_date
            end = detail_date
        if start and end:
            scopes = [
                normalize_scope(item_year, item_month, media_type, include_unlinked)
                for item_year, item_month in _month_keys(start, end)
            ]
        else:
            scopes = [normalize_scope(year, month, media_type, include_unlinked)]
        rows = self.repository.get_many(scopes, now=current)
        available = [row for row in rows.values() if row]
        if not available:
            queue = self.repository.queue_state(year, month, media_type, include_unlinked)
            return self._empty_cached_response(year, month, media_type, include_unlinked, view=view, meta=self._cache_meta(None, queue=queue))
        first = available[0]
        all_entries = []
        excluded_by_date = {}
        errors = []
        confirmations = []
        for row in available:
            payload = row.get("payload") or {}
            calendar = payload.get("calendar") or {}
            all_entries.extend(payload.get("_allEntries") or calendar.get("entries") or [])
            errors.extend(calendar.get("errors") or [])
            confirmations.append(row.get("effectiveConfirmation") or "unknown")
            for key, value in (payload.get("_excludedBeforeByDate") or {}).items():
                excluded_by_date[key] = excluded_by_date.get(key, 0) + int(value or 0)
        all_entries = _merge_calendar_entries(all_entries)
        if start and end:
            all_entries = [entry for entry in all_entries if _text(entry.get("date")) and start.isoformat() <= _text(entry.get("date")) <= end.isoformat()]
        visible = all_entries if include_unlinked else [entry for entry in all_entries if entry.get("linkState") != "unlinked"]
        visible = [{**entry, "status": _entry_status(entry, current.astimezone(BEIJING_TZ).strftime("%Y-%m-%d"))} for entry in visible]
        calendar = deepcopy((first.get("payload") or {}).get("calendar") or {})
        calendar.update({
            "entries": visible,
            "errors": list(dict.fromkeys(errors))[:20],
            "errorCount": len(errors),
            "includeUnlinked": bool(include_unlinked),
            "view": view or "legacy",
            "stats": _detail_calendar_stats(
                calendar.get("stats"), visible, all_entries, include_unlinked,
                sum(excluded_by_date.values()),
            ),
        })
        if view == "summary":
            calendar = _summary_calendar(calendar, current)
        stable = json.dumps(calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = hashlib.sha256(f"{first.get('version') or ''}|{stable}".encode("utf-8")).hexdigest()[:24]
        confirmation = "unknown" if "unknown" in confirmations else "partial" if "partial" in confirmations or len(available) < len(scopes) else "confirmed"
        queue = self.repository.queue_state(year, month, media_type, include_unlinked)
        meta = self._cache_meta(first, queue=queue)
        meta["confirmation"] = confirmation
        return {"ok": True, "version": version, "confirmation": confirmation, "cache": meta, "calendar": calendar}

    def _snapshot_build_lock(self, key):
        with self._snapshot_cache_lock:
            return self._snapshot_build_locks.setdefault(key, threading.Lock())

    def _read_cached_date(self, detail_date: date, media_type: str, include_unlinked: bool):
        key = (detail_date.isoformat(), str(media_type or "all"), bool(include_unlinked))
        with self._snapshot_cache_lock:
            cached = self._date_snapshot_cache.get(key)
            if not cached or time.monotonic() - cached[0] >= SNAPSHOT_CACHE_TTL_SECONDS:
                self._date_snapshot_cache.pop(key, None)
                return None
            return deepcopy(cached[1])

    @staticmethod
    def _cached_response(cached: dict, view: str, current: datetime) -> dict:
        public = deepcopy(cached)
        public.pop("_allEntries", None)
        public.pop("_excludedBeforeByDate", None)
        if view != "summary":
            public["calendar"]["view"] = view or "legacy"
            return public
        calendar = _summary_calendar(public["calendar"], current)
        stable = json.dumps(calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = hashlib.sha256(
            f"{public.get('version') or ''}|{stable}".encode("utf-8")
        ).hexdigest()[:24]
        return {"ok": True, "version": version, "calendar": calendar}

    @staticmethod
    def _detail_from_cached(cached: dict, detail_date: date, include_unlinked: bool) -> dict:
        day = detail_date.isoformat()
        source_calendar = deepcopy(cached.get("calendar") or {})
        all_entries = [
            entry for entry in cached.get("_allEntries") or source_calendar.get("entries") or []
            if _text(entry.get("date")) == day
        ]
        entries = all_entries if include_unlinked else [
            entry for entry in all_entries if entry.get("linkState") != "unlinked"
        ]
        source_calendar.update({
            "entries": entries,
            "view": "detail",
            "includeUnlinked": bool(include_unlinked),
            "stats": _detail_calendar_stats(
                source_calendar.get("stats"),
                entries,
                all_entries,
                include_unlinked,
                (cached.get("_excludedBeforeByDate") or {}).get(day),
            ),
        })
        stable = json.dumps(source_calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = hashlib.sha256(
            f"{cached.get('version') or ''}|{day}|{stable}".encode("utf-8")
        ).hexdigest()[:24]
        return {"ok": True, "version": version, "calendar": source_calendar}

    def _torra_calendar_entries(self, year: int, month: int, media_type: str) -> tuple[list[dict], list[str]]:
        cache_key = (year, month, media_type)
        with self._torra_cache_lock:
            cached = self._torra_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1], cached[2]

        reconciliation = self.app.extensions.get("mcc_subscription_reconciliation")
        if not reconciliation:
            return [], []
        try:
            snapshot = reconciliation.snapshot() or {}
        except Exception:
            return [], ["Torra 只读追更暂时无法生成日历"]
        if snapshot.get("sourceError"):
            return [], ["Torra 只读追更暂时无法读取"]

        source_items = []
        seen_targets = set()
        for row in snapshot.get("items") or []:
            item = _torra_calendar_source_item(row)
            if not item:
                continue
            if media_type in {"movie", "tv"} and item["media_type"] != media_type:
                continue
            target = (item["media_type"], item["tmdb_id"], _integer(item.get("target_season")))
            if target in seen_targets:
                continue
            seen_targets.add(target)
            source_items.append(item)
        raw_entries, errors = discover_runtime.build_subscription_calendar_entries_for_items(
            source_items,
            year,
            month,
            media_type,
        )

        payload = {
            "success": True,
            "year": year,
            "month": month,
            "type": media_type,
            "entries": raw_entries,
            "stats": {
                "entries": len(raw_entries),
                "titles": len({str(row.get("key") or row.get("title") or "") for row in raw_entries}),
                "in_library": 0,
                "pending": len(raw_entries),
            },
            "errors": errors,
        }
        mapped = map_calendar_payload(payload).get("calendar") or {}
        entries = [entry for entry in mapped.get("entries") or [] if isinstance(entry, dict)]
        public_errors = ["部分 Torra 只读追更缺少可验证播出日历"] if errors else []
        with self._torra_cache_lock:
            self._torra_cache[cache_key] = (time.monotonic(), entries, public_errors)
        return entries, public_errors

    def _base_calendar(
        self,
        year: int,
        month: int,
        media_type: str,
        start: date | None,
        end: date | None,
    ) -> dict:
        range_start = start or date(year, month, 1)
        range_end = end or (
            date(year + 1, 1, 1) - timedelta(days=1)
            if month == 12
            else date(year, month + 1, 1) - timedelta(days=1)
        )
        entries = []
        errors = []
        for current_year, current_month in _month_keys(range_start, range_end):
            mapped = map_calendar_payload(self.calendar_loader(current_year, current_month, media_type))
            current = mapped.get("calendar") or {}
            entries.extend(
                entry
                for entry in current.get("entries") or []
                if range_start.isoformat() <= _text(entry.get("date")) <= range_end.isoformat()
            )
            errors.extend(current.get("errors") or [])
            torra_entries, torra_errors = self._torra_calendar_entries(
                current_year,
                current_month,
                media_type,
            )
            entries.extend(
                entry for entry in torra_entries
                if range_start.isoformat() <= _text(entry.get("date")) <= range_end.isoformat()
            )
            errors.extend(torra_errors)
        entries = _merge_calendar_entries(entries)
        entries.sort(key=lambda entry: (
            _text(entry.get("date")),
            _text(entry.get("title")),
            _integer(entry.get("seasonNumber")),
            _integer(entry.get("episodeNumber")),
        ))
        return {
            "year": year,
            "month": month,
            "mediaType": media_type,
            "entries": entries,
            "errors": errors[:20],
            "errorCount": len(errors),
            "stats": {
                "entries": len(entries),
                "titles": len({_text(entry.get("key")) or _text(entry.get("title")) for entry in entries}),
                "inLibrary": sum(bool(entry.get("inLibrary")) for entry in entries),
                "pending": sum(not bool(entry.get("inLibrary")) for entry in entries),
            },
        }

    def snapshot(
        self,
        year: int,
        month: int,
        media_type: str,
        *,
        view: str = "",
        start: date | None = None,
        end: date | None = None,
        detail_date: date | None = None,
        include_unlinked: bool = False,
    ) -> dict:
        current = self.clock()
        if detail_date:
            year, month = detail_date.year, detail_date.month
            cached_date = self._read_cached_date(detail_date, media_type, include_unlinked)
            if cached_date:
                return self._detail_from_cached(cached_date, detail_date, include_unlinked)
        cache_key = (int(year), int(month), str(media_type or "all"), bool(include_unlinked))
        build_lock = self._snapshot_build_lock(cache_key)
        with build_lock:
            cached = self._read_cached_snapshot(year, month, media_type, include_unlinked)
            if cached:
                if detail_date:
                    return self._detail_from_cached(cached, detail_date, include_unlinked)
                if start is None and end is None:
                    return self._cached_response(cached, view, current)
            return self._build_snapshot(
                year,
                month,
                media_type,
                view=view,
                start=start,
                end=end,
                detail_date=detail_date,
                include_unlinked=include_unlinked,
                current=current,
            )

    def _build_snapshot(
        self,
        year: int,
        month: int,
        media_type: str,
        *,
        view: str = "",
        start: date | None = None,
        end: date | None = None,
        detail_date: date | None = None,
        include_unlinked: bool = False,
        current: datetime | None = None,
    ) -> dict:
        current = current or self.clock()
        cacheable = start is None and end is None and detail_date is None
        if detail_date:
            start = end = detail_date
            year, month = detail_date.year, detail_date.month
        calendar = self._base_calendar(year, month, media_type, start, end)
        task_service = self.app.extensions.get("mcc_task_chain_v2_service")
        task_payload = task_service.full_snapshot() if task_service else {"items": [], "version": ""}
        repository = getattr(task_service, "repository", None) if task_service else None
        task_items = task_payload.get("items") or []
        event_index = _episode_event_index(task_items, repository)
        calendar_entries = calendar.get("entries") or []
        rss_counts = {}
        rss_service = self.app.extensions.get("mcc_private_rss")
        rss_repository = getattr(rss_service, "repository", None) if rss_service else None
        count_resources = getattr(rss_repository, "resource_counts_for_targets", None)
        if callable(count_resources):
            try:
                rss_counts = count_resources([{
                    "targetId": str(index),
                    "subscriptionId": _text(entry.get("key")),
                    "tmdbId": _text(entry.get("tmdbId")),
                    "mediaType": _text(entry.get("mediaType")),
                    "seasonNumber": _integer(entry.get("seasonNumber")),
                    "episodeNumber": _integer(entry.get("episodeNumber")),
                } for index, entry in enumerate(calendar_entries)])
            except Exception:
                rss_counts = {}
        raw_entries = []
        for index, entry in enumerate(calendar_entries):
            task = _public_task(entry, task_items, current, repository, event_index)
            resources = rss_counts.get(str(index)) or {}
            resource_total = max(0, _integer(resources.get("total")))
            if resource_total and not task.get("chainId"):
                task = {
                    **task,
                    "reasonCode": "RSS_CANDIDATES_AVAILABLE",
                    "reasonText": f"已找到 {resource_total} 个 RSS 候选，尚未形成下载或入库任务链",
                }
            value = _normalize_entry_evidence({
                **entry,
                "airAt": f"{entry.get('date')}T00:00:00+08:00" if entry.get("date") else "",
                **task,
                "rssResourceCount": resource_total,
                "rssExactEpisodeCount": max(0, _integer(resources.get("exactEpisode"))),
                "rssMultiEpisodeCount": max(0, _integer(resources.get("multiEpisode"))),
                "rssSeasonPackCount": max(0, _integer(resources.get("seasonPack"))),
                "rssScopePendingCount": max(0, _integer(resources.get("scopePending"))),
            })
            raw_entries.append({**value, "linkState": _calendar_link_state(value)})
        excluded_before_subscription = sum(_is_pre_subscription_episode(entry) for entry in raw_entries)
        excluded_before_by_date = {}
        for entry in raw_entries:
            if _is_pre_subscription_episode(entry):
                key = _text(entry.get("date"))
                excluded_before_by_date[key] = excluded_before_by_date.get(key, 0) + 1
        entries = [entry for entry in raw_entries if not _is_pre_subscription_episode(entry)]
        excluded_unlinked = sum(entry.get("linkState") == "unlinked" for entry in entries)
        linked_entries = len(entries) - excluded_unlinked
        total_entries = len(entries)
        today = current.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")
        all_entries = [{**entry, "status": _entry_status(entry, today)} for entry in entries]
        if not include_unlinked:
            entries = [entry for entry in entries if entry.get("linkState") != "unlinked"]
        entries = [{**entry, "status": _entry_status(entry, today)} for entry in entries]
        observed_at = current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        source_confirmation = "partial" if int(calendar.get("errorCount") or 0) > 0 else "confirmed"
        evidence_confirmation = (
            "unknown" if not task_service
            else "partial" if source_confirmation == "partial" or any(
                entry.get("status") in {"unknown", "unlinked"} for entry in entries
            )
            else "confirmed"
        )
        statistics_meta = {
            **{
                key: statistic_metadata(
                    scope="calendar_query", unit="episode_event",
                    observed_at=observed_at, confirmation=source_confirmation,
                )
                for key in ("entries", "linkedEntries", "unlinkedEntries", "totalEntries")
            },
            **{
                key: statistic_metadata(
                    scope="calendar_query", unit="episode_event",
                    observed_at=observed_at, confirmation=evidence_confirmation,
                )
                for key in ("upcoming", "acquiring", "library", "playable", "protected", "missing", "unknown")
            },
        }
        calendar = {
            **calendar,
            "timeZone": "Asia/Shanghai",
            "includeUnlinked": bool(include_unlinked),
            "entries": entries,
            "view": view or "legacy",
            "statisticsMeta": statistics_meta,
            "stats": {
                **(calendar.get("stats") or {}),
                "entries": len(entries),
                "titles": len({_text(entry.get("key")) or _text(entry.get("title")) for entry in entries}),
                "inLibrary": sum(bool(entry.get("inLibrary")) for entry in entries),
                "pending": sum(not bool(entry.get("inLibrary")) for entry in entries),
                "acquired": sum(bool(entry.get("acquiredAt")) for entry in entries),
                "libraryEvidence": sum(bool(entry.get("libraryAt")) for entry in entries),
                "playable": sum(entry.get("status") == "playable" for entry in entries),
                "unlinked": excluded_unlinked,
                "excludedUnlinked": 0 if include_unlinked else excluded_unlinked,
                "linkedEntries": linked_entries,
                "unlinkedEntries": excluded_unlinked,
                "totalEntries": total_entries,
                "actionRequired": sum(entry.get("healthState") == "action_required" for entry in entries),
                "excludedBeforeSubscription": excluded_before_subscription,
                "statusCounts": {
                    state: sum(entry.get("status") == state for entry in entries)
                    for state in (
                        "upcoming", "acquiring", "library", "playable", "protected", "missing", "unknown", "unlinked",
                    )
                },
            },
        }
        full_stable = json.dumps(calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        full_version = hashlib.sha256(
            f"{task_payload.get('version') or ''}|{full_stable}".encode("utf-8")
        ).hexdigest()[:24]
        if cacheable:
            cache_payload = {
                "ok": True,
                "version": full_version,
                "confirmation": self._cache_confirmation(calendar, {"calendar": calendar}),
                "calendar": deepcopy(calendar),
                "_allEntries": deepcopy(all_entries),
                "_excludedBeforeByDate": excluded_before_by_date,
            }
            with self._snapshot_cache_lock:
                self._snapshot_cache[(year, month, media_type, bool(include_unlinked))] = (
                    time.monotonic(),
                    cache_payload,
                )
        else:
            cache_payload = {
                "ok": True,
                "version": full_version,
                "confirmation": self._cache_confirmation(calendar, {"calendar": calendar}),
                "calendar": deepcopy(calendar),
                "_allEntries": deepcopy(all_entries),
                "_excludedBeforeByDate": excluded_before_by_date,
            }
        with self._snapshot_cache_lock:
            cached_at = time.monotonic()
            for day in {_text(entry.get("date")) for entry in all_entries if _text(entry.get("date"))}:
                self._date_snapshot_cache[(day, media_type, bool(include_unlinked))] = (
                    cached_at,
                    cache_payload,
                )
        if view == "summary":
            calendar = _summary_calendar(calendar, current)
        stable = json.dumps(calendar, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = hashlib.sha256(
            f"{task_payload.get('version') or ''}|{stable}".encode("utf-8")
        ).hexdigest()[:24]
        return {"ok": True, "version": version, "calendar": calendar}


def _error(code: str, message: str, status: int):
    return jsonify({"code": code, "error": message, "request_id": current_request_id()}), status


def register_calendar_timeline(app: Flask, calendar_loader=None, clock=None, repository=None):
    service = CalendarTimelineService(app, calendar_loader=calendar_loader, clock=clock, repository=repository)
    app.extensions["mcc_calendar_timeline"] = service

    @app.get("/api/v2/calendar")
    def calendar_timeline():
        now = datetime.now()
        try:
            year = int(request.args.get("year", now.year))
            month = int(request.args.get("month", now.month))
        except (TypeError, ValueError):
            return _error("CALENDAR_RANGE_INVALID", "日历年月无效", 400)
        media_type = _text(request.args.get("type") or "all").lower()
        view = _text(request.args.get("view")).lower()
        detail_date = _parse_date(request.args.get("date")) if request.args.get("date") else None
        from_value = _parse_date(request.args.get("from")) if request.args.get("from") else None
        to_value = _parse_date(request.args.get("to")) if request.args.get("to") else None
        include_unlinked = _truthy(request.args.get("includeUnlinked"))
        invalid_date = (
            bool(request.args.get("date")) and not detail_date
            or bool(request.args.get("from")) and not from_value
            or bool(request.args.get("to")) and not to_value
        )
        invalid_range = (
            bool(from_value) != bool(to_value)
            or bool(from_value and to_value and (from_value > to_value or (to_value - from_value).days > 62))
        )
        if (
            not 2000 <= year <= 2100
            or not 1 <= month <= 12
            or media_type not in ALLOWED_MEDIA_TYPES
            or view not in ALLOWED_VIEWS
            or invalid_date
            or invalid_range
            or (view == "detail" and not detail_date)
        ):
            return _error("CALENDAR_RANGE_INVALID", "日历范围、日期或视图无效", 400)
        try:
            if service.repository:
                payload = service.read_cached(
                    year,
                    month,
                    media_type,
                    view=view,
                    start=from_value,
                    end=to_value,
                    detail_date=detail_date,
                    include_unlinked=include_unlinked,
                )
            else:
                payload = service.snapshot(
                    year,
                    month,
                    media_type,
                    view=view,
                    start=from_value,
                    end=to_value,
                    detail_date=detail_date,
                    include_unlinked=include_unlinked,
                )
        except Exception:
            return _error("CALENDAR_TIMELINE_READ_FAILED", "日历时间线读取失败", 502)
        etag = payload.get("version") or ""
        if etag and request.if_none_match.contains(etag):
            response = Response(status=304)
        else:
            response = jsonify(payload)
        if etag:
            response.set_etag(etag)
        response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
        return response

    @app.post("/api/v2/calendar/refresh-requests")
    def calendar_refresh_request():
        if not service.repository:
            return _error("CALENDAR_REFRESH_UNAVAILABLE", "日历后台刷新尚未启用", 503)
        body = request.get_json(silent=True) or {}
        try:
            scope = normalize_scope(
                body.get("year"),
                body.get("month"),
                body.get("mediaType") or body.get("type") or "all",
                _truthy(body.get("includeUnlinked")),
            )
            idempotency_key = _text(body.get("idempotencyKey"))
            if not idempotency_key or len(idempotency_key) > 180:
                raise ValueError("刷新幂等键无效")
            result = service.repository.request_refresh(
                scope["year"], scope["month"], scope["mediaType"], scope["includeUnlinked"],
                now=service.clock(), idempotency_key=idempotency_key,
            )
        except CalendarRefreshConflict:
            return _error("CALENDAR_REFRESH_CONFLICT", "刷新请求与其他日历范围冲突", 409)
        except (TypeError, ValueError):
            return _error("CALENDAR_REFRESH_INVALID", "日历刷新范围或幂等键无效", 422)
        return jsonify({"ok": True, "scope": scope, "refresh": result}), 202

    return service
