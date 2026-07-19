from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from app.quality_watch_repository import make_unit_key


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
    }[field]
    if subscription_key in subscription:
        return subscription[subscription_key]
    global_key = {
        "window_hours": "torra_quality_default_window_hours",
        "offsets_minutes": "torra_quality_schedule_json",
    }[field]
    return global_config.get(global_key)


def resolve_watch_policy(subscription, global_config=None):
    global_config = global_config if isinstance(global_config, dict) else {}
    window_value = _policy_value(subscription, global_config, "window_hours")
    window_hours = _integer(window_value, DEFAULT_WINDOW_HOURS)
    if window_hours not in {24, 48}:
        raise ValueError("追更洗版窗口只允许 24 或 48 小时")
    offsets_value = _policy_value(subscription, global_config, "offsets_minutes")
    if offsets_value is None:
        return {"window_hours": window_hours, "offsets_minutes": list(DEFAULT_OFFSETS[window_hours])}
    offsets = [_integer(value) for value in _parse_schedule(offsets_value)]
    window_minutes = window_hours * 60
    if not offsets or offsets != sorted(set(offsets)):
        raise ValueError("追更洗版检查时间点必须严格递增且不能重复")
    if any(value < 30 or value > window_minutes for value in offsets):
        raise ValueError("追更洗版检查时间点必须在观察窗口内且不少于 30 分钟")
    if offsets[-1] != window_minutes:
        offsets.append(window_minutes)
    return {"window_hours": window_hours, "offsets_minutes": offsets}


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
        "torraSubscriptionId": torra_subscription_id,
    }


def _target_reached_for_unit(context, evidence, unit):
    if evidence.get("target_reached") is not True:
        return False
    if context["media_type"] == "movie":
        return True
    episodes = _positive_integers(evidence.get("target_reached_episode_numbers"))
    if not episodes:
        episodes = _positive_integers(evidence.get("episode_numbers"))
    return int(unit.get("episode_number") or 0) in episodes


class QualityWatchRuntime:
    def __init__(self, repository, config_loader=None, clock=None):
        self.repository = repository
        self.config_loader = config_loader or (lambda: {})
        self.clock = clock or _utc_now

    def _set_blocked(self, unit, reason):
        if unit["state"] == "blocked" and unit["last_result"].get("reason") == reason:
            return unit
        return self.repository.update_watch_unit(
            unit["unit_key"],
            unit["version"],
            state="blocked",
            last_result_json={"reason": reason},
        )

    def _block(self, context, episode_number, reason, policy):
        unit = self.repository.ensure_watch_unit(
            context["subscription_key"],
            context["media_type"],
            context["season_number"] or None,
            episode_number,
            window_hours=policy["window_hours"],
            torra_subscription_id=context["torra_subscription_id"],
        )
        return self._set_blocked(unit, reason)

    def _ensure_new_unit(self, context, task_item, creation, episode):
        evidence = creation["evidence"]
        policy = creation["policy"]
        unit_key = make_unit_key(
            context["subscription_key"],
            context["media_type"],
            context["season_number"] or None,
            episode,
        )
        existing = self.repository.get_watch_unit(unit_key)
        unit = self.repository.ensure_watch_unit(
            context["subscription_key"],
            context["media_type"],
            context["season_number"] or None,
            episode,
            first_success_at=creation["observed_at"],
            window_hours=policy["window_hours"],
            torra_subscription_id=context["torra_subscription_id"],
        )
        if existing:
            return unit
        return self.repository.update_watch_unit(
            unit["unit_key"],
            unit["version"],
            current_evidence_json=_new_evidence_summary(
                task_item,
                evidence,
                context["torra_subscription_id"],
            ),
        )

    def _create_units(self, context, task_item, torra_row, evidence, policy):
        if not evidence.get("is_new"):
            return []
        if not _download_is_complete(task_item):
            return []
        observed_at = _observed_at(evidence.get("observed_at"), self.clock())
        episodes = _episodes_to_create(context, torra_row, evidence)
        if episodes is None:
            return [self._block(context, None, "episode_identity_missing", policy)]
        creation = {"evidence": evidence, "policy": policy, "observed_at": observed_at}
        return [
            self._ensure_new_unit(context, task_item, creation, episode)
            for episode in episodes
        ]

    def _context_units(self, context):
        units = self.repository.list_watch_units(context["subscription_key"])
        if context["media_type"] == "movie":
            return [unit for unit in units if unit.get("season_number") is None]
        season = int(context.get("season_number") or 0)
        return [unit for unit in units if int(unit.get("season_number") or 0) == season]

    def _link_torra(self, unit, torra_subscription_id):
        stored_torra_id = _text(unit.get("torra_subscription_id"))
        if stored_torra_id and torra_subscription_id and stored_torra_id != torra_subscription_id:
            return self._set_blocked(unit, "torra_subscription_conflict"), False
        if torra_subscription_id and not stored_torra_id:
            unit = self.repository.update_watch_unit(
                unit["unit_key"],
                unit["version"],
                torra_subscription_id=torra_subscription_id,
            )
        return unit, True

    def _baseline_ready(self, context, torra_row, unit, library_episodes):
        if context["media_type"] == "movie":
            return _movie_library_ready(torra_row)
        return int(unit.get("episode_number") or 0) in library_episodes

    def _mark_baseline(self, context, unit, baseline):
        if unit["state"] != "waiting_library_baseline":
            return unit
        unit, linked = self._link_torra(unit, context["torra_subscription_id"])
        if not linked:
            return unit
        if not context["torra_subscription_id"]:
            return unit
        if not self._baseline_ready(context, baseline["torra_row"], unit, baseline["library_episodes"]):
            return unit
        unit = self.repository.mark_baseline_ready(
            unit["unit_key"],
            baseline_ready_at=baseline["ready_at"],
            offsets_minutes=baseline["policy"]["offsets_minutes"],
        )
        if not _target_reached_for_unit(context, baseline["evidence"], unit):
            return unit
        return self.repository.update_watch_unit(
            unit["unit_key"],
            unit["version"],
            state="target_reached",
            target_reached_at=unit["baseline_ready_at"],
            last_result_json={"reason": "version_target_reached"},
        )

    def _mark_baselines(self, context, torra_row, evidence, policy):
        units = self._context_units(context)
        library_episodes = set()
        if context["media_type"] == "tv":
            library_episodes = _library_episode_numbers(context, torra_row)
        baseline_at = _observed_at(evidence.get("baseline_ready_at"), self.clock())
        baseline = {
            "torra_row": torra_row,
            "evidence": evidence,
            "policy": policy,
            "library_episodes": library_episodes,
            "ready_at": baseline_at,
        }
        return [
            self._mark_baseline(context, unit, baseline)
            for unit in units
        ]

    def _resolve_policy(self, subscription):
        try:
            return resolve_watch_policy(subscription, self.config_loader()), ""
        except ValueError:
            return None, "invalid_watch_policy"

    def _blocked_result(self, context, existing, reason, policy, evidence=None):
        units = [self._set_blocked(unit, reason) for unit in existing]
        can_create = bool(context["subscription_key"] and context["media_type"])
        if not units and can_create:
            evidence = evidence if isinstance(evidence, dict) else {}
            episode = next(iter(_positive_integers(evidence.get("episode_numbers"))), None)
            units = [self._block(context, episode, reason, policy)]
        return {"status": "blocked", "reason": reason, "units": units}

    @staticmethod
    def _result(created, units):
        blocked_units = [unit for unit in units if unit["state"] == "blocked"]
        reason = blocked_units[0]["last_result"].get("reason", "") if blocked_units else ""
        status = "blocked" if blocked_units else ("created" if created else "updated")
        return {"status": status, "reason": reason, "units": units}

    def reconcile(self, subscription, task_item, torra_row=None, evidence=None):
        subscription = subscription if isinstance(subscription, dict) else {}
        task_item = task_item if isinstance(task_item, dict) else {}
        torra_row = torra_row if isinstance(torra_row, dict) else {}
        evidence = evidence if isinstance(evidence, dict) else {}
        context = _task_identity(subscription, task_item, torra_row)
        existing = self._context_units(context)
        if not existing and not evidence.get("is_new"):
            return {"status": "ignored", "reason": "historical_evidence", "units": []}
        if not existing and not _download_is_complete(task_item):
            return {"status": "ignored", "reason": "download_not_complete", "units": []}
        policy, policy_error = self._resolve_policy(subscription)
        if policy_error:
            fallback = {"window_hours": DEFAULT_WINDOW_HOURS, "offsets_minutes": DEFAULT_OFFSETS[48]}
            return self._blocked_result(context, existing, policy_error, fallback)
        if not context["valid"]:
            return self._blocked_result(context, existing, "identity_conflict", policy, evidence)
        created = self._create_units(context, task_item, torra_row, evidence, policy)
        units = self._mark_baselines(context, torra_row, evidence, policy)
        return self._result(created, units)


def register_quality_watch(app, repository, config_loader=None, clock=None):
    runtime = QualityWatchRuntime(repository, config_loader=config_loader, clock=clock)
    app.extensions["mcc_quality_watch_runtime"] = runtime
    return runtime
