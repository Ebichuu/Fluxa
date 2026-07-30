from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.quality_watch_repository import DEFAULT_LIFECYCLE_MODE, WATCH_LIFECYCLE_MODES, make_unit_key


DEFAULT_WINDOW_HOURS = 48
DEFAULT_OFFSETS = {
    24: [720, 1440],
    48: [720, 1440, 2880],
}
EPISODE_PATTERN = re.compile(r"S0*(\d{1,2})E(\d+(?:E\d+)*)", re.IGNORECASE)


def _utc_now():
    return datetime.now(timezone.utc)


def _text(value):
    return str(value or "").strip()


def _integer(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _media_type(value):
    normalized = _text(value).lower()
    if normalized in {"movie", "film", "电影"}:
        return "movie"
    if normalized in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return ""


def _subscription_key(subscription):
    for key in ("key", "subscription_key", "dedupe_key", "id"):
        value = _text(subscription.get(key))
        if value:
            return value
    return ""


def _tmdb_id(mapping):
    for key in ("tmdbId", "tmdb_id", "tmdbid"):
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _season_number(mapping):
    for key in ("seasonNumber", "target_season", "season_number", "season"):
        value = _integer(mapping.get(key))
        if value > 0:
            return value
    return 0


def _positive_integers(values):
    result = set()
    for value in values if isinstance(values, (list, tuple, set)) else []:
        number = _integer(value)
        if number > 0:
            result.add(number)
    return result


def _mapping_episode_numbers(value):
    return _positive_integers(value.keys()) if isinstance(value, dict) else set()


def _episode_numbers_from_text(value, expected_season):
    result = set()
    for match in EPISODE_PATTERN.finditer(_text(value)):
        if expected_season and _integer(match.group(1)) != expected_season:
            continue
        result.update(_positive_integers(re.findall(r"E?(\d+)", match.group(2), re.IGNORECASE)))
    return result


def _episode_numbers_from_files(value, expected_season):
    if isinstance(value, dict):
        strings = [*value.keys(), *value.values()]
    elif isinstance(value, list):
        strings = value
    else:
        strings = [value]
    result = set()
    for item in strings:
        if isinstance(item, list):
            for nested in item:
                result.update(_episode_numbers_from_text(nested, expected_season))
        else:
            result.update(_episode_numbers_from_text(item, expected_season))
    return result


def _download_step(task_item):
    steps = task_item.get("steps") if isinstance(task_item, dict) else []
    return next(
        (step for step in steps if isinstance(step, dict) and step.get("key") == "download"),
        {},
    )


def _download_is_complete(task_item):
    step = _download_step(task_item)
    return step.get("status") == "done" and step.get("evidence") == "verified"


def _parse_schedule(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("追更洗版检查时间表不是有效 JSON") from exc
    if not isinstance(value, list):
        raise ValueError("追更洗版检查时间表必须是数组")
    return value


def _policy_value(subscription, global_config, field):
    nested = subscription.get("torra_quality_watch")
    if isinstance(nested, dict) and field in nested:
        return nested[field]
    subscription_key = {
        "window_hours": "torra_quality_window_hours",
        "offsets_minutes": "torra_quality_schedule_json",
        "lifecycle_mode": "torra_quality_lifecycle_mode",
    }[field]
    if subscription_key in subscription:
        return subscription[subscription_key]
    global_key = {
        "window_hours": "torra_quality_default_window_hours",
        "offsets_minutes": "torra_quality_schedule_json",
        "lifecycle_mode": "torra_quality_lifecycle_mode",
    }[field]
    return global_config.get(global_key)


def _resolve_lifecycle_mode(subscription, global_config):
    lifecycle_mode = _text(_policy_value(subscription, global_config, "lifecycle_mode")).lower()
    lifecycle_mode = lifecycle_mode or DEFAULT_LIFECYCLE_MODE
    if lifecycle_mode not in WATCH_LIFECYCLE_MODES:
        raise ValueError("观察模式只允许 follow_rss 或 fixed_window")
    return lifecycle_mode


def _resolve_offsets(offsets_value, window_hours):
    if offsets_value is None:
        return list(DEFAULT_OFFSETS[window_hours])
    offsets = [_integer(value) for value in _parse_schedule(offsets_value)]
    window_minutes = window_hours * 60
    if not offsets or offsets != sorted(set(offsets)):
        raise ValueError("追更洗版检查时间点必须严格递增且不能重复")
    if any(value < 30 or value > window_minutes for value in offsets):
        raise ValueError("追更洗版检查时间点必须在观察窗口内且不少于 30 分钟")
    if offsets[-1] != window_minutes:
        offsets.append(window_minutes)
    return offsets


def resolve_watch_policy(subscription, global_config=None):
    global_config = global_config if isinstance(global_config, dict) else {}
    lifecycle_mode = _resolve_lifecycle_mode(subscription, global_config)
    window_value = _policy_value(subscription, global_config, "window_hours")
    window_hours = _integer(window_value, DEFAULT_WINDOW_HOURS)
    if window_hours not in {24, 48}:
        raise ValueError("追更洗版窗口只允许 24 或 48 小时")
    return {
        "lifecycle_mode": lifecycle_mode,
        "window_hours": window_hours,
        "offsets_minutes": _resolve_offsets(
            _policy_value(subscription, global_config, "offsets_minutes"),
            window_hours,
        ),
    }


def _media_type_from(mapping):
    for key in ("media_type", "mediaType", "type"):
        value = _media_type(mapping.get(key))
        if value:
            return value
    return ""


def _optional_matches(expected, values):
    return all(not value or value == expected for value in values)


def _identity_is_valid(context, task_values, torra_values):
    required = bool(context["subscription_key"] and context["tmdb_id"])
    media_valid = context["media_type"] in {"movie", "tv"}
    task_valid = _optional_matches(context["subscription_key"], [task_values["key"]])
    task_valid = task_valid and _optional_matches(context["media_type"], [task_values["media_type"]])
    task_valid = task_valid and _optional_matches(context["tmdb_id"], [task_values["tmdb_id"]])
    task_valid = task_valid and (
        context["media_type"] != "tv"
        or _optional_matches(context["season_number"], [task_values["season_number"]])
    )
    torra_valid = _optional_matches(context["media_type"], [torra_values["media_type"]])
    torra_valid = torra_valid and _optional_matches(context["tmdb_id"], [torra_values["tmdb_id"]])
    season_valid = context["media_type"] != "tv" or _optional_matches(
        context["season_number"],
        [torra_values["season_number"]],
    )
    torra_id_valid = _optional_matches(
        context["torra_subscription_id"],
        [task_values["torra_subscription_id"], torra_values["torra_subscription_id"]],
    )
    return all((required, media_valid, task_valid, torra_valid, season_valid, torra_id_valid))


def _task_identity(subscription, task_item, torra_row):
    subscription_key = _subscription_key(subscription)
    source_ids = task_item.get("sourceIds") if isinstance(task_item.get("sourceIds"), dict) else {}
    task_torra_id = _text(source_ids.get("torraId"))
    row_torra_id = _text(torra_row.get("id"))
    context = {
        "subscription_key": subscription_key,
        "media_type": _media_type_from(subscription),
        "tmdb_id": _tmdb_id(subscription),
        "season_number": _season_number(subscription) or _season_number(task_item),
        "torra_subscription_id": row_torra_id or task_torra_id,
    }
    task_values = {
        "key": _text(source_ids.get("subscriptionId")),
        "media_type": _media_type_from(task_item),
        "tmdb_id": _tmdb_id(task_item),
        "season_number": _season_number(task_item),
        "torra_subscription_id": task_torra_id,
    }
    torra_values = {
        "media_type": _media_type_from(torra_row),
        "tmdb_id": _tmdb_id(torra_row),
        "season_number": _season_number(torra_row),
        "torra_subscription_id": row_torra_id,
    }
    context["valid"] = _identity_is_valid(context, task_values, torra_values)
    return context


def _new_episode_numbers(context, torra_row, evidence):
    supplied = _positive_integers(evidence.get("episode_numbers"))
    if supplied:
        return supplied
    season = context["season_number"]
    last_added = _episode_numbers_from_text(torra_row.get("last_added_name"), season)
    if last_added:
        return last_added
    explicit = _positive_integers(torra_row.get("downloaded_episode_numbers"))
    explicit.update(_mapping_episode_numbers(torra_row.get("downloaded_episode_files")))
    file_numbers = _episode_numbers_from_files(torra_row.get("downloaded_file_names"), season)
    combined = explicit | file_numbers
    return combined if len(combined) == 1 else set()


def _library_episode_numbers(context, torra_row):
    season = context["season_number"]
    result = _positive_integers(torra_row.get("available_episode_numbers"))
    result.update(_mapping_episode_numbers(torra_row.get("library_episode_files")))
    result.update(_episode_numbers_from_files(torra_row.get("library_file_names"), season))
    result.update(_episode_numbers_from_files(torra_row.get("library_episode_files"), season))
    return result


def _movie_library_ready(torra_row):
    names = torra_row.get("library_file_names")
    return bool(names) if isinstance(names, list) else bool(_text(names))


def _observed_at(value, fallback):
    if isinstance(value, datetime):
        return value
    text = _text(value)
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


def _episodes_to_create(context, torra_row, evidence):
    if context["media_type"] == "movie":
        return [None]
    if not context["season_number"]:
        return None
    episodes = sorted(_new_episode_numbers(context, torra_row, evidence))
    return episodes or None


def _new_evidence_summary(task_item, evidence, torra_subscription_id):
    step = _download_step(task_item)
    source = _text(evidence.get("source"))
    downloaded_at = _text(evidence.get("observed_at"))
    return {
        "source": source or _text(step.get("source")),
        "downloadedAt": downloaded_at or _text(step.get("timestamp")),
        "firstDownloadAt": _text(evidence.get("first_download_at")),
        "torraSubscriptionId": torra_subscription_id,
    }


def _first_download_at(task_item, evidence, fallback):
    step = _download_step(task_item)
    for value in (
        evidence.get("first_download_at"),
        evidence.get("download_started_at"),
        task_item.get("downloadStartedAt"),
        task_item.get("createdAt"),
        step.get("startedAt"),
        step.get("createdAt"),
        step.get("addedAt"),
        step.get("timestamp"),
        evidence.get("observed_at"),
    ):
        parsed = _observed_at(value, None)
        if parsed is not None:
            return parsed
    return fallback


def _target_reached_for_unit(context, evidence, unit):
    if evidence.get("target_reached") is not True:
        return False
    if context["media_type"] == "movie":
        return True
    episodes = _positive_integers(evidence.get("target_reached_episode_numbers"))
    if not episodes:
        episodes = _positive_integers(evidence.get("episode_numbers"))
    return int(unit.get("episode_number") or 0) in episodes


RELIABLE_TIME_SOURCES = {"torra_completed", "qb_completed", "symedia_completed"}


def _utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    parsed = _observed_at(value, None)
    return parsed.astimezone(timezone.utc) if parsed else None


def _iso(value):
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _planned_unit(context, episode, policy, first_success_at, task_item, evidence):
    unit_key = make_unit_key(
        context["subscription_key"], context["media_type"], context["season_number"] or None, episode
    )
    return {
        "unit_key": unit_key,
        "subscription_key": context["subscription_key"],
        "season_number": context["season_number"] or None,
        "episode_number": episode,
        "torra_subscription_id": context["torra_subscription_id"],
        "state": "blocked" if unit_key.endswith(":blocked") else "waiting_library_baseline",
        "first_success_at": _iso(first_success_at),
        "baseline_ready_at": "",
        "window_hours": policy["window_hours"],
        "next_check_at": "",
        "observation_ends_at": "",
        "current_evidence": _new_evidence_summary(task_item, evidence, context["torra_subscription_id"]),
        "last_result": {},
        "target_reached_at": "",
        "lifecycle_mode": policy["lifecycle_mode"],
        "version": 0,
        "_new": True,
    }


def _strict_time_error(now, evidence):
    if evidence.get("require_reliable_times") is not True:
        return ""
    if _text(evidence.get("time_source")) not in RELIABLE_TIME_SOURCES:
        return "time_source_untrusted"
    first = _utc(evidence.get("first_download_at") or evidence.get("upstream_occurred_at"))
    baseline = _utc(evidence.get("baseline_ready_at"))
    if first is None:
        return "success_time_missing"
    if first > now or (baseline and baseline > now):
        return "future_success_time"
    if baseline and baseline < first:
        return "success_time_inverted"
    return ""


def _baseline_ready_for_unit(context, torra_row, evidence, unit):
    if evidence.get("baseline_success") is True:
        if context["media_type"] == "movie":
            return True
        return int(unit.get("episode_number") or 0) in _positive_integers(
            evidence.get("baseline_episode_numbers") or evidence.get("episode_numbers")
        )
    if context["media_type"] == "movie":
        return _movie_library_ready(torra_row)
    return int(unit.get("episode_number") or 0) in _library_episode_numbers(context, torra_row)


def plan_reconcile(*, now, subscription, task_item, torra_row, evidence, policy, existing_units, policy_error=""):
    now = _utc(now)
    if now is None:
        raise ValueError("reconcile now must be a UTC-compatible datetime")
    subscription = subscription if isinstance(subscription, dict) else {}
    task_item = task_item if isinstance(task_item, dict) else {}
    torra_row = torra_row if isinstance(torra_row, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    existing_units = [deepcopy(unit) for unit in existing_units if isinstance(unit, dict)]
    context = _task_identity(subscription, task_item, torra_row)
    relevant = [
        unit for unit in existing_units
        if context["media_type"] == "movie" or int(unit.get("season_number") or 0) == context["season_number"]
    ]
    original = {unit["unit_key"]: deepcopy(unit) for unit in relevant}
    if not relevant and not evidence.get("is_new"):
        return {"status": "ignored", "reason": "historical_evidence", "writes": [], "unitKeys": [], "backfillUnitKeys": []}
    if not relevant and not _download_is_complete(task_item):
        return {"status": "ignored", "reason": "download_not_complete", "writes": [], "unitKeys": [], "backfillUnitKeys": []}
    time_error = _strict_time_error(now, evidence)
    if time_error:
        return {"status": "needs_review", "reason": time_error, "writes": [], "unitKeys": [], "backfillUnitKeys": []}

    units = {unit["unit_key"]: unit for unit in relevant}
    created_keys = []
    if not context["valid"] or policy_error:
        block_reason = policy_error or "identity_conflict"
        if not units:
            if not context["subscription_key"] or context["media_type"] not in {"movie", "tv"}:
                return {"status": "blocked", "reason": block_reason, "writes": [], "unitKeys": [], "backfillUnitKeys": []}
            episode = next(iter(_positive_integers(evidence.get("episode_numbers"))), None)
            blocked = _planned_unit(context, episode, policy, now, task_item, evidence)
            blocked["state"] = "blocked"
            blocked["last_result"] = {"reason": block_reason}
            units[blocked["unit_key"]] = blocked
            created_keys.append(blocked["unit_key"])
        else:
            for unit in units.values():
                unit["state"] = "blocked"
                unit["last_result"] = {"reason": block_reason}
        reason = block_reason
    else:
        reason = ""
        if evidence.get("is_new") and _download_is_complete(task_item):
            episodes = _episodes_to_create(context, torra_row, evidence)
            if episodes is None:
                episodes = [None]
                reason = "episode_identity_missing"
            first_success = _utc(
                evidence.get("first_download_at") or evidence.get("upstream_occurred_at")
            ) or _first_download_at(task_item, evidence, now)
            for episode in episodes:
                unit_key = make_unit_key(
                    context["subscription_key"], context["media_type"], context["season_number"] or None, episode
                )
                if unit_key in units:
                    continue
                unit = _planned_unit(context, episode, policy, first_success, task_item, evidence)
                if reason:
                    unit["state"] = "blocked"
                    unit["last_result"] = {"reason": reason}
                units[unit_key] = unit
                created_keys.append(unit_key)

        baseline_at = _utc(evidence.get("baseline_ready_at")) or now
        for unit in units.values():
            stored_torra = _text(unit.get("torra_subscription_id"))
            if stored_torra and context["torra_subscription_id"] and stored_torra != context["torra_subscription_id"]:
                unit["state"] = "blocked"
                unit["last_result"] = {"reason": "torra_subscription_conflict"}
                reason = reason or "torra_subscription_conflict"
                continue
            if context["torra_subscription_id"] and not stored_torra:
                unit["torra_subscription_id"] = context["torra_subscription_id"]
            if (
                unit.get("state") == "waiting_library_baseline"
                and context["torra_subscription_id"]
                and _baseline_ready_for_unit(context, torra_row, evidence, unit)
            ):
                observation_ends = baseline_at + timedelta(hours=int(unit.get("window_hours") or policy["window_hours"]))
                unit["baseline_ready_at"] = _iso(baseline_at)
                unit["observation_ends_at"] = _iso(observation_ends)
                unit["lifecycle_mode"] = policy["lifecycle_mode"]
                unit["state"] = "observation_expired" if observation_ends <= now else "observing_upgrade"
                unit["next_check_at"] = "" if observation_ends <= now else (
                    _iso(observation_ends) if policy["lifecycle_mode"] == DEFAULT_LIFECYCLE_MODE
                    else _iso(baseline_at + timedelta(minutes=policy["offsets_minutes"][0]))
                )
                if _target_reached_for_unit(context, evidence, unit):
                    unit["state"] = "target_reached"
                    unit["target_reached_at"] = unit["baseline_ready_at"]
                    unit["last_result"] = {"reason": "version_target_reached"}

    writes = []
    compared = (
        "torra_subscription_id", "state", "baseline_ready_at", "next_check_at",
        "observation_ends_at", "current_evidence", "last_result",
        "target_reached_at", "lifecycle_mode",
    )
    for unit_key, unit in sorted(units.items()):
        if unit.get("_new"):
            values = {key: value for key, value in unit.items() if not key.startswith("_") and key != "version"}
            writes.append({"operation": "insert", "unitKey": unit_key, "values": values})
            continue
        before = original.get(unit_key) or {}
        changes = {key: unit.get(key) for key in compared if unit.get(key) != before.get(key)}
        if changes:
            writes.append({
                "operation": "update", "unitKey": unit_key,
                "expectedVersion": int(before.get("version") or 0), "values": changes,
            })
    blocked = [unit for unit in units.values() if unit.get("state") == "blocked"]
    status = "blocked" if blocked else ("created" if created_keys else "updated")
    return {
        "status": status,
        "reason": reason or (blocked[0].get("last_result") or {}).get("reason", "") if blocked else reason,
        "writes": writes,
        "unitKeys": sorted(units),
        "backfillUnitKeys": sorted({
            *created_keys,
            *(unit["unit_key"] for unit in units.values() if unit.get("baseline_ready_at")),
        }),
    }


class QualityWatchRuntime:
    def __init__(self, repository, config_loader=None, clock=None, candidate_backfill=None):
        self.repository = repository
        self.config_loader = config_loader or (lambda: {})
        self.clock = clock or _utc_now
        self.candidate_backfill = candidate_backfill

    def set_candidate_backfill(self, callback):
        self.candidate_backfill = callback

    def _backfill_candidates(self, unit):
        if not self.candidate_backfill or not unit:
            return
        try:
            self.candidate_backfill(unit["unit_key"])
        except Exception:
            return

    def _resolve_policy(self, subscription):
        try:
            return resolve_watch_policy(subscription, self.config_loader()), ""
        except ValueError:
            return None, "invalid_watch_policy"

    def reconcile(self, subscription, task_item, torra_row=None, evidence=None):
        subscription = subscription if isinstance(subscription, dict) else {}
        task_item = task_item if isinstance(task_item, dict) else {}
        torra_row = torra_row if isinstance(torra_row, dict) else {}
        evidence = evidence if isinstance(evidence, dict) else {}
        policy, policy_error = self._resolve_policy(subscription)
        if policy_error:
            policy = {
                "lifecycle_mode": DEFAULT_LIFECYCLE_MODE,
                "window_hours": DEFAULT_WINDOW_HOURS,
                "offsets_minutes": DEFAULT_OFFSETS[48],
            }
        subscription_key = _subscription_key(subscription)
        now = self.clock()
        with self.repository.runtime.transaction(immediate=True) as connection:
            existing = self.repository.list_watch_units_in_connection(connection, subscription_key)
            plan = plan_reconcile(
                now=now,
                subscription=subscription,
                task_item=task_item,
                torra_row=torra_row,
                evidence=evidence,
                policy=policy,
                existing_units=existing,
                policy_error=policy_error,
            )
            self.repository.apply_reconcile_plan(connection, plan, now=now)
        units = [
            unit for unit in (self.repository.get_watch_unit(key) for key in plan["unitKeys"])
            if unit
        ]
        for unit_key in plan["backfillUnitKeys"]:
            self._backfill_candidates(self.repository.get_watch_unit(unit_key))
        return {"status": plan["status"], "reason": plan["reason"], "units": units}


def register_quality_watch(app, repository, config_loader=None, clock=None):
    runtime = QualityWatchRuntime(repository, config_loader=config_loader, clock=clock)
    app.extensions["mcc_quality_watch_runtime"] = runtime
    return runtime
