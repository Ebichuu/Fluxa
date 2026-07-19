from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone


ACTIVE_WATCH_STATES = {"observing_upgrade", "search_due", "search_running"}
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
LATIN_BOUNDARY = re.compile(r"(?<![a-z0-9]){}(?![a-z0-9])", re.IGNORECASE)
QB_EPISODE_PATTERN = re.compile(r"S0*(\d{1,2})E0*(\d{1,4})(?:[-~]E?0*(\d{1,4}))?", re.IGNORECASE)
ANALYSIS_ACTION_TYPE = "rewash-analysis"


def _text(value):
    return str(value or "").strip()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _media_type(value):
    value = _text(value).lower()
    if value in {"movie", "film", "电影"}:
        return "movie"
    if value in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return ""


def _tmdb_id(value):
    if not isinstance(value, dict):
        return ""
    for key in ("tmdb_id", "tmdbId", "tmdbid"):
        candidate = _text(value.get(key))
        if candidate:
            return candidate
    mapping = value.get("standard_media") or value.get("standard_mapping")
    if isinstance(mapping, dict):
        return _tmdb_id(mapping)
    return ""


def _compact(value):
    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _contains_title(text, alias):
    text = unicodedata.normalize("NFKC", _text(text)).casefold()
    alias = unicodedata.normalize("NFKC", _text(alias)).casefold()
    compact_alias = _compact(alias)
    if len(compact_alias) < 2:
        return False
    if any(ord(char) > 127 for char in alias):
        return compact_alias in _compact(text)
    escaped = re.escape(" ".join(alias.split()))
    escaped = escaped.replace(r"\ ", r"[ ._\-]+")
    return bool(LATIN_BOUNDARY.pattern.format(escaped) and re.search(
        LATIN_BOUNDARY.pattern.format(escaped), text, re.IGNORECASE
    ))


def _year(*values):
    for value in values:
        match = YEAR_PATTERN.search(_text(value))
        if match:
            return match.group(0)
    return ""


def _positive_range(start, end):
    start = _int(start)
    end = _int(end) or start
    if start <= 0 or end < start:
        return None
    return start, end


def _as_utc(value):
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _subscription_key(subscription):
    for key in ("key", "subscription_key", "id"):
        value = _text(subscription.get(key))
        if value:
            return value
    return ""


def _subscription_aliases(subscription):
    canonical = []
    aliases = []
    for key in ("title", "name", "original_title", "original_name", "source_title"):
        value = _text(subscription.get(key))
        if value:
            canonical.append(value)
    for key in ("aliases", "names", "title_aliases", "alternate_titles", "aka"):
        values = subscription.get(key)
        if isinstance(values, str):
            values = [values]
        if isinstance(values, (list, tuple, set)):
            aliases.extend(_text(value) for value in values)
    return (
        list(dict.fromkeys(value for value in canonical if value)),
        list(dict.fromkeys(value for value in aliases if value)),
    )


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def qb_task_matches(task, subscription, unit):
    if str(task.get("status") or "").lower() not in {"downloading", "stalled"}:
        return False
    canonical, aliases = _subscription_aliases(subscription)
    if not any(_contains_title(task.get("name"), alias) for alias in (*canonical, *aliases)):
        return False
    if unit.get("season_number") is None:
        return True
    matches = list(QB_EPISODE_PATTERN.finditer(_text(task.get("name"))))
    if not matches:
        return True
    season = _int(unit.get("season_number"))
    episode = _int(unit.get("episode_number"))
    return any(
        _int(match.group(1)) == season
        and _int(match.group(2)) <= episode <= _int(match.group(3) or match.group(2))
        for match in matches
    )


@dataclass(frozen=True)
class RssAnalysisDependencies:
    environment: object
    torra: object
    qb: object
    config_loader: object


def _identity_match(item, subscription):
    item_tmdb = _tmdb_id(item)
    subscription_tmdb = _tmdb_id(subscription)
    if item_tmdb and subscription_tmdb:
        return None if item_tmdb != subscription_tmdb else ("tmdb", "", subscription_tmdb)
    canonical, aliases = _subscription_aliases(subscription)
    for alias in canonical:
        if _contains_title(item.get("title"), alias):
            basis = "standard-title-map" if subscription_tmdb else "title"
            return basis, alias, subscription_tmdb
    for alias in aliases:
        if _contains_title(item.get("title"), alias):
            return "title-alias", alias, subscription_tmdb
    return None


def _year_match(item, subscription):
    item_year = _year(item.get("year"), item.get("title"))
    subscription_year = _year(
        subscription.get("year"),
        subscription.get("release_date"),
        subscription.get("first_air_date"),
    )
    if item_year and subscription_year and item_year != subscription_year:
        return None
    return item_year, subscription_year


def _episode_match(item, unit, item_type):
    if item_type == "movie":
        return None if item.get("season_number") or item.get("episode_start") else (None, {})
    item_season = _int(item.get("season_number"))
    episode_range = _positive_range(item.get("episode_start"), item.get("episode_end"))
    unit_season = _int(unit.get("season_number"))
    unit_episode = _int(unit.get("episode_number"))
    if item_season <= 0 or not episode_range or item_season != unit_season:
        return None
    if not episode_range[0] <= unit_episode <= episode_range[1]:
        return None
    return episode_range, {
        "season": {"item": item_season, "unit": unit_season},
        "episode": {"start": episode_range[0], "end": episode_range[1], "unit": unit_episode},
    }


def _is_after_baseline(item, unit):
    published_at = _as_utc(item.get("published_at")) or _as_utc(item.get("created_at"))
    baseline_at = _as_utc(unit.get("baseline_ready_at"))
    return not published_at or not baseline_at or published_at >= baseline_at


class RssSubscriptionMatchRuntime:
    def __init__(self, rss_repository, watch_repository, subscription_loader, clock=None, analysis=None):
        self.rss_repository = rss_repository
        self.watch_repository = watch_repository
        self.subscription_loader = subscription_loader or (lambda: [])
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.analysis = analysis

    def _subscriptions(self):
        payload = self.subscription_loader()
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        return {
            _subscription_key(item): item
            for item in payload if isinstance(item, dict) and _subscription_key(item)
        }

    @staticmethod
    def _compatible_type(item, subscription, unit):
        item_type = _media_type(item.get("media_type") or item.get("mediaType"))
        if not item_type:
            item_type = "tv" if item.get("season_number") or item.get("episode_start") else "movie"
        unit_type = "tv" if unit.get("season_number") is not None else "movie"
        subscription_type = _media_type(subscription.get("media_type") or subscription.get("mediaType"))
        if item_type != unit_type or (subscription_type and subscription_type != item_type):
            return ""
        return item_type

    def _candidate(self, item, subscription, unit):
        item_type = self._compatible_type(item, subscription, unit)
        if not item_type:
            return None
        identity = _identity_match(item, subscription)
        years = _year_match(item, subscription)
        episode = _episode_match(item, unit, item_type)
        if identity is None or years is None or episode is None or not _is_after_baseline(item, unit):
            return None
        basis, matched_alias, subscription_tmdb = identity
        item_year, subscription_year = years
        _, episode_reason = episode
        identity_key = subscription_tmdb or _compact(matched_alias)
        reason = {
            "identity": {
                "basis": basis,
                "tmdbId": subscription_tmdb,
                "alias": matched_alias[:120],
            },
            "mediaType": item_type,
        }
        if item_year or subscription_year:
            reason["year"] = {"item": item_year, "subscription": subscription_year}
        reason.update(episode_reason)
        return {
            "unit": unit,
            "identity_key": f"{item_type}:{identity_key}:{item_year or subscription_year}",
            "reason": reason,
        }

    def _candidates_for_item(self, item, subscriptions, active_units):
        candidates = []
        for unit in active_units:
            subscription = subscriptions.get(_text(unit.get("subscription_key")))
            if subscription:
                candidate = self._candidate(item, subscription, unit)
                if candidate:
                    candidates.append(candidate)
        identities = {candidate["identity_key"] for candidate in candidates}
        return [] if len(identities) > 1 else candidates

    def match_inserted_rows(self, connection, rows):
        subscriptions = self._subscriptions()
        active_units = [
            unit for unit in self.watch_repository.list_active_watch_units(self.clock())
            if unit.get("state") in ACTIVE_WATCH_STATES
        ]
        created = []
        rows = rows if isinstance(rows, list) else []
        for item in rows:
            for candidate in self._candidates_for_item(item, subscriptions, active_units):
                match = self.rss_repository.create_match(
                    item["id"],
                    candidate["unit"]["subscription_key"],
                    candidate["unit"]["unit_key"],
                    candidate["reason"],
                    connection=connection,
                )
                if match:
                    created.append(match)
        return created

    def match_inserted_items(self, item_ids):
        rows = []
        for item_id in item_ids if isinstance(item_ids, (list, tuple, set)) else []:
            item = self.rss_repository.get_item(item_id, public=False)
            if item:
                rows.append(item)
        if not rows:
            return []
        with self.rss_repository.runtime.transaction(immediate=True) as connection:
            return self.match_inserted_rows(connection, rows)

    def _analysis_config(self, require_rss_gate=True):
        if not self.analysis:
            return {}, "analysis_not_configured"
        environment = self.analysis.environment or {}
        if require_rss_gate and not _truthy(environment.get("MCC_PRIVATE_RSS_ENABLED")):
            return {}, "rss_disabled"
        if not _truthy(environment.get("MCC_TORRA_QUALITY_WATCH_ENABLED")):
            return {}, "quality_watch_disabled"
        config = self.analysis.config_loader() if self.analysis.config_loader else {}
        config = config if isinstance(config, dict) else {}
        if not _truthy(config.get("torra_quality_watch_enabled")):
            return {}, "quality_watch_disabled"
        return config, ""

    def _local_analysis_context(self, match):
        unit = self.watch_repository.get_watch_unit(match.get("unitId"))
        if not unit:
            return None, "watch_unit_missing"
        ends_at = _as_utc(unit.get("observation_ends_at"))
        current = _as_utc(self.clock())
        if unit.get("state") not in ACTIVE_WATCH_STATES or not ends_at or not current or ends_at < current:
            self.rss_repository.update_match(match["id"], "expired")
            return None, "window_expired"
        subscription = self._subscriptions().get(unit["subscription_key"])
        if not subscription:
            return None, "subscription_missing"
        torra_id = _text(unit.get("torra_subscription_id"))
        if not torra_id:
            return None, "torra_subscription_missing"
        return {"match": match, "unit": unit, "subscription": subscription, "torra_id": torra_id}, ""

    def _torra_preflight(self, context):
        torra = self.analysis.torra
        if torra is None or not torra.is_configured():
            return "torra_unavailable"
        rows = torra.list_subscriptions()
        torra_row = next((row for row in rows if _text(row.get("id")) == context["torra_id"]), None)
        if not torra_row:
            return "torra_subscription_missing"
        if torra_row.get("is_running") is True or torra_row.get("is_mutating") is True:
            return "torra_busy"
        return ""

    def _qb_preflight(self, context):
        qb = self.analysis.qb
        if qb is None:
            return "qb_unavailable"
        summary = qb.summary()
        if not isinstance(summary, dict) or summary.get("connected") is not True:
            return "qb_unavailable"
        if any(
            qb_task_matches(task, context["subscription"], context["unit"])
            for task in summary.get("tasks") or [] if isinstance(task, dict)
        ):
            return "qb_busy"
        return ""

    def _provider_preflight(self, context):
        return self._torra_preflight(context) or self._qb_preflight(context)

    def _safe_provider_preflight(self, context):
        try:
            return self._provider_preflight(context)
        except Exception:
            return "provider_check_failed"

    def _inflight_conflict(self, preclaimed):
        inflight = self.watch_repository.find_inflight_action("torra", ANALYSIS_ACTION_TYPE)
        if not inflight:
            return None
        if preclaimed and inflight["action_id"] == preclaimed["action"]["action_id"]:
            return None
        return inflight

    def _claim_analysis(self, context, config, idempotency_key, source):
        return self.watch_repository.claim_action(
            idempotency_key,
            context["unit"]["subscription_key"],
            "torra",
            ANALYSIS_ACTION_TYPE,
            unit_key=context["unit"]["unit_key"],
            request_summary={"matchId": context["match"]["id"], "source": source},
            cooldown_seconds=max(60, _int(config.get("torra_quality_min_interval_minutes") or 60)) * 60,
            rate_limits={
                "hourly": max(1, _int(config.get("torra_quality_hourly_limit") or 4)),
                "daily": max(1, _int(config.get("torra_quality_daily_limit") or 30)),
            },
            require_idle=True,
        )

    def _submit_analysis(self, context, action):
        action_id = action["action_id"]
        try:
            job_id = self.analysis.torra.submit_analysis(context["torra_id"])
            self.watch_repository.save_external_job(action_id, job_id)
            self.rss_repository.update_match(context["match"]["id"], "triggered", action_id)
            return {"status": "submitted", "actionId": action_id}
        except Exception:
            self.watch_repository.complete_action(
                action_id,
                "failed",
                {"message": "Torra 分析提交失败"},
                error_code="TORRA_ANALYSIS_SUBMIT_FAILED",
                error_message="Torra 分析提交失败",
            )
            return {"status": "failed", "reason": "torra_submit_failed", "actionId": action_id}

    def _finish_analysis_job(self, match, action, job):
        action_id = action["action_id"]
        status = job["status"]
        if status in {"pending", "running"}:
            self.watch_repository.save_external_job(action_id, action["external_job_id"], status="polling")
            return {"status": "polling", "actionId": action_id}
        if status in {"failed", "cancelled"}:
            self.watch_repository.complete_action(
                action_id,
                status,
                {"jobStatus": status},
                error_code=f"TORRA_ANALYSIS_{status.upper()}",
                error_message=f"Torra 分析任务{status}",
            )
            self.rss_repository.update_match(match["id"], "candidate", action_id)
            return {"status": status, "actionId": action_id}
        selection = self.analysis.torra.select_upgrade_candidates(job)
        self.watch_repository.complete_action(
            action_id,
            "succeeded",
            {
                "jobStatus": "success",
                "analysisId": selection["analysis_id"],
                "selectedCandidates": selection["selected_candidates"],
                "rowCount": selection["row_count"],
                "selectedCount": selection["selected_count"],
            },
        )
        next_status = "ignored" if selection["selected_count"] == 0 else "triggered"
        self.rss_repository.update_match(match["id"], next_status, action_id)
        return {"status": next_status, "actionId": action_id, "selectedCount": selection["selected_count"]}

    def _resume_analysis(self, match, claim):
        action = claim["action"]
        if match.get("status") == "candidate":
            match = self.rss_repository.update_match(match["id"], "triggered", action["action_id"])
        try:
            job = self.analysis.torra.get_job(action["external_job_id"])
        except Exception:
            return {"status": "polling", "reason": "torra_poll_failed", "actionId": action["action_id"]}
        try:
            return self._finish_analysis_job(match, action, job)
        except Exception:
            self.watch_repository.complete_action(
                action["action_id"],
                "failed",
                {"message": "Torra 分析结果无效"},
                error_code="TORRA_ANALYSIS_RESULT_INVALID",
                error_message="Torra 分析结果无效",
            )
            self.rss_repository.update_match(match["id"], "candidate", action["action_id"])
            return {"status": "failed", "reason": "torra_result_invalid", "actionId": action["action_id"]}

    def _replay_analysis(self, match, action):
        if action["status"] == "succeeded":
            selected_count = _int(action.get("response_summary", {}).get("selectedCount"))
            next_status = "triggered" if selected_count > 0 else "ignored"
            if match.get("status") in {"candidate", "triggered"}:
                self.rss_repository.update_match(match["id"], next_status, action["action_id"])
            return {"status": "replay", "actionId": action["action_id"], "selectedCount": selected_count}
        if match.get("status") == "triggered" and action["status"] in {"failed", "cancelled"}:
            self.rss_repository.update_match(match["id"], "candidate", action["action_id"])
        return {"status": "replay", "actionId": action["action_id"]}

    def _existing_analysis_claim(self, match, idempotency_key, source):
        existing = self.watch_repository.get_action_by_idempotency(idempotency_key)
        if not existing:
            return None, None
        summary = existing.get("request_summary") or {}
        target_conflict = (
            existing.get("provider") != "torra"
            or existing.get("action_type") != ANALYSIS_ACTION_TYPE
            or existing.get("subscription_key") != match.get("subscriptionId")
            or existing.get("unit_key") != match.get("unitId")
        )
        fixed_rss_identity = (
            source == "private-rss"
            and idempotency_key == f"rss-rewash-analysis:{match['id']}"
            and summary.get("source") in {None, "", source}
            and summary.get("matchId") in {None, "", match["id"]}
        )
        explicit_identity = summary.get("source") == source and summary.get("matchId") == match.get("id")
        if target_conflict or not (fixed_rss_identity or explicit_identity):
            return None, {"status": "conflict", "actionId": existing["action_id"]}
        claim = self.watch_repository.claim_action(
            idempotency_key,
            existing["subscription_key"],
            existing["provider"],
            existing["action_type"],
            unit_key=existing["unit_key"],
        )
        if claim["disposition"] == "resume":
            return None, self._resume_analysis(match, claim)
        if claim["disposition"] == "reclaimed":
            return claim, None
        if claim["disposition"] == "replay":
            return None, self._replay_analysis(match, claim["action"])
        return None, {"status": claim["disposition"], "actionId": existing["action_id"]}

    def start_analysis(
        self,
        match_id,
        idempotency_key=None,
        source="private-rss",
        require_rss_gate=True,
    ):
        match = self.rss_repository.get_match(match_id)
        if not match:
            return {"status": "missing", "reason": "match_missing"}
        idempotency_key = _text(idempotency_key) or f"rss-rewash-analysis:{match['id']}"
        preclaimed, immediate = self._existing_analysis_claim(match, idempotency_key, source)
        if immediate:
            return immediate
        inflight = self._inflight_conflict(preclaimed)
        if inflight:
            return {"status": "global_busy", "actionId": inflight["action_id"]}
        config, reason = self._analysis_config(require_rss_gate=require_rss_gate)
        if reason:
            return {"status": "blocked", "reason": reason}
        context, reason = self._local_analysis_context(match)
        if reason:
            return {"status": "blocked", "reason": reason}
        reason = self._safe_provider_preflight(context)
        if reason:
            return {"status": "blocked", "reason": reason}
        claim = preclaimed or self._claim_analysis(context, config, idempotency_key, source)
        if claim["disposition"] == "resume":
            return self._resume_analysis(match, claim)
        if claim["disposition"] not in {"claimed", "reclaimed"}:
            return {"status": claim["disposition"]}
        return self._submit_analysis(context, claim["action"])

    def wake_pending_candidates(self, limit=2):
        results = []
        matches = self.rss_repository.list_matches(status="candidate", limit=max(1, int(limit))).get("items") or []
        for match in matches:
            action = self.watch_repository.get_action_by_idempotency(
                f"rss-rewash-analysis:{match['id']}"
            )
            if action and action["status"] in {"succeeded", "failed", "cancelled"}:
                continue
            result = {"matchId": match["id"], **self.start_analysis(match["id"])}
            results.append(result)
            if result["status"] in {"submitted", "polling", "in_progress", "global_busy"}:
                break
        return results

    def wake_matches(self, match_ids):
        results = []
        for match in self.rss_repository.list_matches_by_ids(match_ids):
            try:
                results.append({"matchId": match["id"], **self.start_analysis(match["id"])})
            except Exception:
                results.append({"matchId": match["id"], "status": "failed", "reason": "analysis_runtime_failed"})
        return results


def register_rss_subscription_match(app, runtime):
    app.extensions["mcc_rss_subscription_match_runtime"] = runtime
    return runtime
