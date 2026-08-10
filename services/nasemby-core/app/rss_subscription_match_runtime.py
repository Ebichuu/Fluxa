from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.private_rss_parser import extract_media_identity, extract_release_scope
from app.quality_watch_repository import make_unit_key
from app.quality_watch_subscription_runtime import QualityWatchSubscriptionResolver
from app.rss_baseline_runtime import resolve_baseline_artifact
from app.rss_shadow_scoring_runtime import (
    ShadowScoringUnsupported,
    rss_artifact_key,
    rss_target_key,
    score_rss_candidate,
    select_subscription_rule,
    stable_payload_hash,
)
from app.torra_subscription_keys import (
    resolve_torra_subscription_key,
    torra_internal_unit_key,
    torra_public_match_keys,
    torra_public_subscription_key,
    torra_public_unit_key,
)


ACTIVE_WATCH_STATES = {"observing_upgrade", "search_due", "search_running"}
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
LATIN_BOUNDARY = re.compile(r"(?<![a-z0-9]){}(?![a-z0-9])", re.IGNORECASE)
QB_EPISODE_PATTERN = re.compile(r"S0*(\d{1,2})E0*(\d{1,4})(?:[-~]E?0*(\d{1,4}))?", re.IGNORECASE)
ANALYSIS_ACTION_TYPE = "rewash-analysis"
DOWNLOAD_ACTION_TYPE = "rewash-download"
SHADOW_EVALUATION_ACTION_TYPE = "rss-candidate-evaluation"
RESOURCE_DOWNLOAD_ACTION_TYPE = "rss-resource-download"
MANUAL_SUBSCRIPTION_SOURCE = "manual-subscription"
QB_TARGET_OCCUPYING_STATES = {"downloading", "stalled", "queued", "paused"}
TORRA_DOWNLOAD_ROOT_DEFAULT = "/vol02/1000-4-32d3f6a0/torra"
TORRA_MEDIA_CATEGORIES = {
    "anime_jp": {"label": "日漫", "directory": "00-日漫"},
    "anime_cn": {"label": "国漫", "directory": "01-国漫"},
    "tv_cn": {"label": "国产剧", "directory": "02-国产剧"},
    "tv_asia": {"label": "日韩剧", "directory": "03-日韩剧"},
    "tv_western": {"label": "欧美剧", "directory": "04-欧美剧"},
    "tv_hk_tw": {"label": "港台剧", "directory": "05-港台剧"},
    "variety": {"label": "综艺", "directory": "06-综艺"},
    "movie": {"label": "电影", "directory": "10-电影"},
}
PERMANENT_RECLAIM_CONTEXT_CODES = {
    "window_expired": "RSS_REWASH_WINDOW_EXPIRED",
    "watch_unit_missing": "RSS_REWASH_WATCH_UNIT_MISSING",
    "subscription_missing": "RSS_REWASH_SUBSCRIPTION_MISSING",
    "torra_subscription_missing": "RSS_REWASH_TORRA_SUBSCRIPTION_MISSING",
}


class RssExactDownloadError(RuntimeError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


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


def _imdb_id(value):
    if not isinstance(value, dict):
        return ""
    for key in ("imdb_id", "imdbId", "imdbid"):
        candidate = _text(value.get(key)).lower()
        if candidate:
            return candidate
    mapping = value.get("standard_media") or value.get("standard_mapping")
    if isinstance(mapping, dict):
        return _imdb_id(mapping)
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
    tokens = re.findall(r"[a-z0-9]+", alias)
    if not tokens:
        return False
    normalized_alias = r"[^a-z0-9]+".join(re.escape(token) for token in tokens)
    return bool(re.search(LATIN_BOUNDARY.pattern.format(normalized_alias), text, re.IGNORECASE))


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
    for key in ("title", "name", "keyword", "original_title", "original_name", "source_title"):
        value = _text(subscription.get(key))
        if value:
            canonical.append(value)
    for key in (
        "aliases", "names", "names_json", "search_names",
        "title_aliases", "alternate_titles", "aka",
    ):
        values = subscription.get(key)
        if isinstance(values, str):
            try:
                parsed = json.loads(values)
            except (TypeError, ValueError):
                parsed = None
            values = parsed if isinstance(parsed, (list, tuple, set)) else [values]
        if isinstance(values, (list, tuple, set)):
            for value in values:
                if isinstance(value, dict):
                    aliases.extend(
                        _text(value.get(field))
                        for field in ("name", "title", "value")
                        if _text(value.get(field))
                    )
                else:
                    aliases.append(_text(value))
    for key in ("tmdb_title", "match_title"):
        value = subscription.get(key)
        if isinstance(value, str) and value.strip():
            aliases.append(value.strip())
    return (
        list(dict.fromkeys(value for value in canonical if value)),
        list(dict.fromkeys(value for value in aliases if value)),
    )


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def qb_task_matches(task, subscription, unit):
    if str(task.get("status") or "").lower() not in QB_TARGET_OCCUPYING_STATES:
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
    if episode <= 0:
        return any(_int(match.group(1)) == season for match in matches)
    return any(
        _int(match.group(1)) == season
        and _int(match.group(2)) <= episode <= _int(match.group(3) or match.group(2))
        for match in matches
    )


def _secret_digest(value) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RssAnalysisDependencies:
    environment: object
    torra: object
    qb: object
    config_loader: object
    symedia: object = None


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


def _is_after_first_download(item, unit):
    published_at = _as_utc(item.get("published_at")) or _as_utc(item.get("created_at"))
    first_success_at = _as_utc(unit.get("first_success_at"))
    return bool(published_at and first_success_at and published_at >= first_success_at)


class RssSubscriptionMatchRuntime:
    def __init__(self, rss_repository, watch_repository, subscription_loader, clock=None, analysis=None):
        self.rss_repository = rss_repository
        self.watch_repository = watch_repository
        self.subscription_loader = subscription_loader or (lambda: [])
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.analysis = analysis
        self._exact_preview_lock = threading.RLock()
        self._exact_previews = {}
        self._resource_previews = {}

    def _local_subscriptions(self):
        payload = self.subscription_loader()
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        return {
            _subscription_key(item): item
            for item in payload if isinstance(item, dict) and _subscription_key(item)
        }

    def _torra_subscriptions(self):
        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        if torra is None:
            return {}
        try:
            if hasattr(torra, "is_configured") and not torra.is_configured():
                return {}
            rows = torra.list_subscriptions()
        except Exception:
            return {}
        return QualityWatchSubscriptionResolver([], rows).subscription_map()

    def _subscriptions(self):
        local = self._local_subscriptions()
        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        if torra is None:
            return local
        try:
            if hasattr(torra, "is_configured") and not torra.is_configured():
                return local
            rows = torra.list_subscriptions()
        except Exception:
            return local
        return QualityWatchSubscriptionResolver(list(local.values()), rows).subscription_map()

    def has_executable_candidate(
        self,
        subscription_key,
        *,
        media_type="tv",
        season_number=None,
        episode_numbers=(),
        torra_subscription_id="",
    ):
        """Return whether a strict, uniquely selected RSS upgrade already covers the target."""
        internal_key = _text(subscription_key)
        episodes = sorted({_int(value) for value in episode_numbers if _int(value) > 0})
        if not internal_key or media_type != "tv" or season_number is None or not episodes:
            return False
        public_key = (
            torra_public_subscription_key(torra_subscription_id)
            if internal_key.startswith("torra:") and _text(torra_subscription_id)
            else internal_key
        )
        refs = []
        for episode in episodes:
            internal_unit = make_unit_key(
                internal_key,
                "tv",
                _int(season_number),
                episode,
            )
            public_unit = torra_public_unit_key(internal_unit, internal_key, public_key)
            refs.extend(((internal_key, internal_unit), (public_key, public_unit)))
        matches = self.rss_repository.list_matches_for_units(refs)
        for match in matches:
            candidate_score = match.get("candidateScore")
            baseline_score = match.get("baselineScore")
            if any(isinstance(value, bool) for value in (candidate_score, baseline_score)):
                continue
            if not all(isinstance(value, (int, float)) for value in (candidate_score, baseline_score)):
                continue
            if (
                match.get("status") in {"candidate", "triggered", "confirmed"}
                and match.get("torraLinked") is True
                and match.get("evaluationStatus") == "scored"
                and match.get("bestCandidate") is True
                and match.get("decision") == "current_best"
                and float(candidate_score) > float(baseline_score)
            ):
                return True
        return False

    def _subscription_context(self, subscription_id, torra_rows=None):
        subscription_id = _text(subscription_id)
        local_subscriptions = self._local_subscriptions()
        if not subscription_id.startswith("torra:"):
            subscription = local_subscriptions.get(subscription_id)
            if not subscription:
                return None, "subscription_missing"
            return {
                "subscription": subscription,
                "internalKey": subscription_id,
                "publicKey": subscription_id,
                "torraRows": None,
            }, ""

        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        if torra is None:
            return None, "torra_unavailable"
        try:
            if hasattr(torra, "is_configured") and not torra.is_configured():
                return None, "torra_unavailable"
            rows = torra_rows if isinstance(torra_rows, list) else torra.list_subscriptions()
        except Exception:
            return None, "torra_unavailable"
        resolved = resolve_torra_subscription_key(subscription_id, rows)
        if resolved.get("status") == "conflict":
            return None, "torra_subscription_key_conflict"
        if resolved.get("status") != "resolved":
            return None, "subscription_missing"
        resolution = QualityWatchSubscriptionResolver(
            list(local_subscriptions.values()),
            rows,
        ).resolve({
            "subscription_key": subscription_id,
            "torra_subscription_id": resolved["remoteId"],
            "media_type": _media_type(
                resolved["item"].get("media_type")
                or resolved["item"].get("mediaType")
                or resolved["item"].get("type")
            ),
            "tmdb_id": _tmdb_id(resolved["item"]),
            "season_number": self._subscription_season(resolved["item"]),
        })
        if resolution.get("status") != "resolved":
            return None, resolution.get("reason") or "subscription_missing"
        internal_key = resolution["subscriptionKey"]
        subscription = dict(resolution["subscription"])
        subscription["key"] = internal_key
        subscription["subscription_key"] = internal_key
        subscription.setdefault("source", "torra")
        return {
            "subscription": subscription,
            "internalKey": internal_key,
            "publicKey": resolution["publicKey"],
            "canonicalKey": resolution["canonicalKey"],
            "remoteId": resolved["remoteId"],
            "torraRows": rows,
        }, ""

    @staticmethod
    def _public_match_keys(unit):
        internal_key = _text(unit.get("subscription_key"))
        unit_key = _text(unit.get("unit_key"))
        torra_id = _text(unit.get("torra_subscription_id"))
        if not internal_key.startswith("torra:") or not torra_id:
            return internal_key, unit_key
        public_key = torra_public_subscription_key(torra_id)
        return public_key, torra_public_unit_key(unit_key, internal_key, public_key)

    @staticmethod
    def _public_match(match):
        if not match:
            return match
        value = dict(match)
        value["subscriptionId"], value["unitId"] = torra_public_match_keys(
            value.get("subscriptionId"), value.get("unitId")
        )
        return value

    @staticmethod
    def _subscription_season(subscription):
        for key in ("target_season", "season_number", "current_season", "latest_season", "season"):
            if subscription.get(key) not in (None, ""):
                return _int(subscription.get(key))
        return None

    @staticmethod
    def _subscription_years(subscription, item_season=None):
        years = {
            value
            for value in (
                _year(subscription.get("year")),
                _year(subscription.get("release_date")),
                _year(subscription.get("first_air_date")),
            )
            if value
        }
        season_years = subscription.get("season_years") or subscription.get("season_years_json")
        if isinstance(season_years, str):
            try:
                season_years = json.loads(season_years)
            except (TypeError, ValueError):
                season_years = {}
        if isinstance(season_years, dict):
            if item_season not in (None, ""):
                value = season_years.get(str(_int(item_season)), season_years.get(_int(item_season)))
                if _year(value):
                    years.add(_year(value))
            else:
                years.update(_year(value) for value in season_years.values() if _year(value))
        return years

    def _torra_owner_matches(self, subscription, unit, torra_row):
        expected_type = _media_type(subscription.get("media_type") or subscription.get("mediaType"))
        if not expected_type:
            expected_type = "tv" if unit.get("season_number") is not None else "movie"
        torra_type = _media_type(torra_row.get("media_type") or torra_row.get("mediaType"))
        if torra_type and torra_type != expected_type:
            return False

        expected_season = _int(unit.get("season_number"))
        torra_season = self._subscription_season(torra_row)
        if (
            expected_type == "tv"
            and expected_season > 0
            and torra_season is not None
            and torra_season > 0
            and torra_season != expected_season
        ):
            return False

        subscription_tmdb = _tmdb_id(subscription)
        torra_tmdb = _tmdb_id(torra_row)
        if subscription_tmdb and torra_tmdb:
            return subscription_tmdb == torra_tmdb

        subscription_titles = sum(_subscription_aliases(subscription), [])
        torra_titles = sum(_subscription_aliases(torra_row), [])
        if subscription_titles and torra_titles:
            return any(
                _contains_title(torra_title, subscription_title)
                or _contains_title(subscription_title, torra_title)
                for subscription_title in subscription_titles
                for torra_title in torra_titles
            )
        return True

    def _identity_backfill_candidates(self, item, subscriptions):
        item_type = _media_type(item.get("media_type") or item.get("mediaType"))
        item_season = item.get("season_number", item.get("seasonNumber"))
        item_year = _year(item.get("title"))
        candidates = []
        for subscription in subscriptions.values():
            subscription_type = _media_type(subscription.get("media_type") or subscription.get("mediaType"))
            if not item_type or subscription_type != item_type:
                continue
            identity = _identity_match(item, subscription)
            if identity is None:
                continue
            basis, matched_alias, subscription_tmdb = identity
            if not subscription_tmdb:
                continue
            if item_type == "tv":
                subscription_season = self._subscription_season(subscription)
                if (
                    item_season not in (None, "")
                    and subscription_season is not None
                    and _int(item_season) != subscription_season
                ):
                    continue
                subscription_years = self._subscription_years(subscription, item_season)
                if item_year and subscription_years and item_year not in subscription_years:
                    continue
            else:
                subscription_years = self._subscription_years(subscription)
                if not item_year or not subscription_years or item_year not in subscription_years:
                    continue
            candidates.append({
                "tmdbId": subscription_tmdb,
                "subscriptionId": _subscription_key(subscription),
                "basis": basis,
                "alias": matched_alias,
            })
        return candidates

    def _refresh_item_scope(self, connection, item):
        category = _text(item.get("category"))
        categories = [value.strip() for value in category.split("/") if value.strip()]
        media_type, season, episode_start, episode_end = extract_release_scope(
            item.get("title"),
            categories,
        )
        self.rss_repository.update_item_release_scope(
            connection,
            item.get("id"),
            media_type,
            season,
            episode_start,
            episode_end,
        )
        return {
            **item,
            "media_type": media_type,
            "season_number": season,
            "episode_start": episode_start,
            "episode_end": episode_end,
        }

    def _supplement_item_from_subscriptions(self, connection, item, subscriptions):
        identity_status = _text(item.get("identity_status") or item.get("identityStatus")) or "unidentified"
        if identity_status == "conflict" or _tmdb_id(item):
            return item
        candidates = self._identity_backfill_candidates(item, subscriptions)
        candidate_tmdb_ids = {candidate["tmdbId"] for candidate in candidates if candidate["tmdbId"]}
        if len(candidate_tmdb_ids) == 1:
            tmdb_id = next(iter(candidate_tmdb_ids))
            changed = self.rss_repository.supplement_item_tmdb_identity(
                connection,
                item.get("id"),
                tmdb_id=tmdb_id,
                source="torra_subscription_match"
                if any(
                    _text(candidate.get("subscriptionId")).startswith("torra:")
                    for candidate in candidates
                )
                else "subscription_match",
                confidence="fallback",
            )
            if changed:
                return {
                    **item,
                    "tmdb_id": tmdb_id,
                    "identity_status": "identified",
                }
        elif len(candidate_tmdb_ids) > 1:
            self.rss_repository.mark_item_identity_conflict(
                connection,
                item.get("id"),
                source="torra_subscription_match",
            )
        return item

    def backfill_unidentified_items(self, limit=50):
        limit = max(1, min(int(limit or 50), 200))
        rows = self.rss_repository.list_unidentified_items(limit)
        subscriptions = self._subscriptions()
        result = {"scanned": len(rows), "identified": 0, "conflicts": 0, "unchanged": 0}
        with self.rss_repository.runtime.transaction(immediate=True) as connection:
            for item in rows:
                item = self._refresh_item_scope(connection, item)
                explicit = extract_media_identity({
                    "description": item.get("description"),
                    "link": item.get("detail_url"),
                })
                if explicit.get("identity_status") == "identified":
                    changed = self.rss_repository.supplement_item_identity(
                        connection,
                        item.get("id"),
                        tmdb_id=explicit.get("tmdb_id"),
                        imdb_id=explicit.get("imdb_id"),
                        source=explicit.get("identity_source") or "rss_history",
                        confidence=explicit.get("identity_confidence") or "explicit",
                    )
                    result["identified" if changed else "unchanged"] += 1
                    continue
                if explicit.get("identity_status") == "conflict":
                    changed = self.rss_repository.mark_item_identity_conflict(
                        connection,
                        item.get("id"),
                        source=explicit.get("identity_source") or "rss_history",
                    )
                    result["conflicts" if changed else "unchanged"] += 1
                    continue
                candidates = self._identity_backfill_candidates(item, subscriptions)
                candidate_tmdb_ids = {candidate["tmdbId"] for candidate in candidates if candidate["tmdbId"]}
                if len(candidate_tmdb_ids) == 1:
                    source = (
                        "torra_subscription_match"
                        if any(
                            _text(candidate.get("subscriptionId")).startswith("torra:")
                            for candidate in candidates
                        )
                        else "subscription_match"
                    )
                    changed = self.rss_repository.supplement_item_identity(
                        connection,
                        item.get("id"),
                        tmdb_id=next(iter(candidate_tmdb_ids)),
                        source=source,
                        confidence="fallback",
                    )
                    result["identified" if changed else "unchanged"] += 1
                elif len(candidate_tmdb_ids) > 1:
                    source = (
                        "torra_subscription_match"
                        if any(
                            _text(candidate.get("subscriptionId")).startswith("torra:")
                            for candidate in candidates
                        )
                        else "subscription_match"
                    )
                    changed = self.rss_repository.mark_item_identity_conflict(
                        connection,
                        item.get("id"),
                        source=source,
                    )
                    result["conflicts" if changed else "unchanged"] += 1
                else:
                    self.rss_repository.touch_item_identity_check(connection, item.get("id"))
                    result["unchanged"] += 1
        result["remaining"] = self.rss_repository.count_unidentified_items()
        result["limit"] = limit
        self.rss_repository.record_identity_backfill_run(result)
        return result

    def match_existing_items(self, limit=200):
        limit = max(1, min(int(limit or 200), 200))
        rows = self.rss_repository.list_items_for_match(limit)
        with self.rss_repository.runtime.transaction(immediate=True) as connection:
            created = self.match_inserted_rows(connection, rows)
        evaluated = self.evaluate_matches([match["id"] for match in created])
        return {
            "scanned": len(rows),
            "created": len(created),
            "evaluated": len(evaluated),
            "remaining": self.rss_repository.count_items_for_match(),
            "uncheckedRemaining": self.rss_repository.count_unchecked_items_for_match(),
            "limit": limit,
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

    @staticmethod
    def _is_subscription_target(unit):
        return bool(unit.get("subscription_target_only"))

    def _candidate(self, item, subscription, unit):
        item_type = self._compatible_type(item, subscription, unit)
        if not item_type:
            return None
        identity = _identity_match(item, subscription)
        years = _year_match(item, subscription)
        episode = _episode_match(item, unit, item_type)
        if (
            identity is None
            or years is None
            or episode is None
            or (
                not self._is_subscription_target(unit)
                and not _is_after_first_download(item, unit)
            )
        ):
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
        reason["candidatePhase"] = (
            "initial_acquisition"
            if self._is_subscription_target(unit)
            else "post_success_upgrade"
        )
        return {
            "unit": unit,
            "identity_key": f"{item_type}:{identity_key}:{item_year or subscription_year}",
            "reason": reason,
        }

    def _subscription_target_units(self, item, subscriptions, active_units):
        item_type = _media_type(item.get("media_type") or item.get("mediaType"))
        if item_type not in {"movie", "tv"}:
            return []
        if item_type == "tv":
            season = _int(item.get("season_number", item.get("seasonNumber")))
            episode_range = _positive_range(
                item.get("episode_start", item.get("episodeStart")),
                item.get("episode_end", item.get("episodeEnd")),
            )
            if season <= 0 or not episode_range or episode_range[1] - episode_range[0] > 199:
                return []
            episodes = range(episode_range[0], episode_range[1] + 1)
        else:
            season = None
            episodes = (None,)

        covered_targets = {
            (
                _text(unit.get("torra_subscription_id")),
                unit.get("season_number"),
                unit.get("episode_number"),
            )
            for unit in active_units
            if _text(unit.get("torra_subscription_id"))
        }
        targets = []
        for subscription_key, subscription in subscriptions.items():
            if (
                not subscription_key.startswith("torra:")
                or _text(subscription.get("source")) != "torra"
            ):
                continue
            remote_id = _text(subscription.get("id")) or subscription_key.removeprefix("torra:")
            if not remote_id:
                continue
            subscription_type = _media_type(
                subscription.get("media_type") or subscription.get("mediaType")
            )
            if subscription_type and subscription_type != item_type:
                continue
            subscription_season = self._subscription_season(subscription)
            if (
                item_type == "tv"
                and subscription_season is not None
                and subscription_season > 0
                and subscription_season != season
            ):
                continue
            for episode in episodes:
                covered_key = (remote_id, season, episode)
                if covered_key in covered_targets:
                    continue
                targets.append({
                    "unit_key": make_unit_key(
                        subscription_key,
                        item_type,
                        season,
                        episode,
                    ),
                    "subscription_key": subscription_key,
                    "season_number": season,
                    "episode_number": episode,
                    "torra_subscription_id": remote_id,
                    "first_success_at": "",
                    "subscription_target_only": True,
                })
        return targets

    def _candidates_for_item(self, item, subscriptions, active_units):
        candidates = []
        units = [
            *active_units,
            *self._subscription_target_units(item, subscriptions, active_units),
        ]
        for unit in units:
            subscription = subscriptions.get(_text(unit.get("subscription_key")))
            if subscription:
                candidate = self._candidate(item, subscription, unit)
                if candidate:
                    candidates.append(candidate)
        identities = {candidate["identity_key"] for candidate in candidates}
        return [] if len(identities) > 1 else candidates

    def match_inserted_rows(self, connection, rows):
        subscriptions = self._subscriptions()
        active_units = self.watch_repository.list_candidate_watch_units(self.clock())
        created = []
        rows = rows if isinstance(rows, list) else []
        for item in rows:
            item = self._supplement_item_from_subscriptions(connection, item, subscriptions)
            candidates = self._candidates_for_item(item, subscriptions, active_units)
            identity_candidates = {
                str(candidate.get("reason", {}).get("identity", {}).get("tmdbId") or "")
                for candidate in candidates
                if candidate.get("reason", {}).get("identity", {}).get("basis") == "standard-title-map"
            }
            identity_units = {
                str(candidate.get("unit", {}).get("subscription_key") or "")
                for candidate in candidates
                if candidate.get("reason", {}).get("identity", {}).get("basis") == "standard-title-map"
            }
            if len(identity_candidates) == 1 and identity_candidates != {""} and len(identity_units) == 1:
                self.rss_repository.supplement_item_identity(
                    connection,
                    item.get("id"),
                    tmdb_id=next(iter(identity_candidates)),
                    source="subscription_match",
                    confidence="fallback",
                )
            for candidate in candidates:
                subscription_key = _text(candidate["unit"].get("subscription_key"))
                unit_key = _text(candidate["unit"].get("unit_key"))
                match = self.rss_repository.create_match(
                    item["id"],
                    subscription_key,
                    unit_key,
                    candidate["reason"],
                    connection=connection,
                )
                if match:
                    created.append(match)
            self.rss_repository.touch_item_match_check(connection, item.get("id"))
        self.rss_repository.record_match_run(len(rows), len(created), connection=connection)
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

    def create_manual_match(self, item_id, subscription_id, unit_id):
        item_id = _text(item_id)
        subscription_id = _text(subscription_id)
        unit_id = _text(unit_id)
        if not all((item_id, subscription_id, unit_id)):
            return {"status": "invalid", "reason": "required_fields_missing"}

        item = self.rss_repository.get_item(item_id, public=False)
        if not item:
            return {"status": "missing", "reason": "item_missing"}
        context, context_error = self._subscription_context(subscription_id)
        if context_error == "torra_unavailable":
            return {"status": "blocked", "reason": "torra_unavailable"}
        if context_error == "torra_subscription_key_conflict":
            return {"status": "conflict", "reason": context_error}
        if not context:
            return {"status": "missing", "reason": "subscription_missing"}
        subscription = context["subscription"]
        internal_unit_id = torra_internal_unit_key(
            unit_id, context["internalKey"], context["publicKey"]
        )
        unit = self.watch_repository.get_watch_unit(internal_unit_id)
        if not unit:
            return {"status": "missing", "reason": "watch_unit_missing"}
        if _text(unit.get("subscription_key")) != context["internalKey"]:
            return {"status": "invalid", "reason": "watch_unit_owner_mismatch"}

        current = _as_utc(self.clock())
        ends_at = _as_utc(unit.get("observation_ends_at"))
        if (
            unit.get("state") not in ACTIVE_WATCH_STATES
            or not _text(unit.get("baseline_ready_at"))
            or not current
            or not ends_at
            or ends_at <= current
        ):
            return {"status": "blocked", "reason": "watch_unit_inactive"}

        torra_id = _text(unit.get("torra_subscription_id"))
        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        if not torra_id or torra is None:
            return {"status": "blocked", "reason": "torra_subscription_missing"}
        try:
            if hasattr(torra, "is_configured") and not torra.is_configured():
                return {"status": "blocked", "reason": "torra_unavailable"}
            torra_rows = context.get("torraRows") or torra.list_subscriptions()
        except Exception:
            return {"status": "blocked", "reason": "torra_unavailable"}
        torra_row = next(
            (
                row for row in torra_rows if isinstance(row, dict)
                and _text(row.get("id")) == torra_id
            ),
            None,
        )
        if not torra_row:
            return {"status": "blocked", "reason": "torra_subscription_missing"}
        if context["internalKey"].startswith("torra:") and context["internalKey"] not in {
            context.get("canonicalKey"),
            context.get("publicKey"),
        }:
            return {"status": "invalid", "reason": "torra_subscription_owner_mismatch"}
        if not self._torra_owner_matches(subscription, unit, torra_row):
            return {"status": "invalid", "reason": "torra_subscription_owner_mismatch"}

        candidate = self._candidate(item, subscription, unit)
        if not candidate:
            return {"status": "invalid", "reason": "item_not_compatible"}
        public_unit_id = torra_public_unit_key(
            internal_unit_id, context["internalKey"], context["publicKey"]
        )
        existing = self.rss_repository.get_match_for_item_unit(item_id, internal_unit_id)
        if not existing and public_unit_id != internal_unit_id:
            existing = self.rss_repository.get_match_for_item_unit(item_id, public_unit_id)
        if existing:
            if existing.get("subscriptionId") not in {context["internalKey"], context["publicKey"]}:
                return {"status": "conflict", "reason": "match_owner_conflict"}
            return {"status": "existing", "match": self._public_match(existing)}
        match = self.rss_repository.create_match(
            item_id,
            context["internalKey"],
            internal_unit_id,
            {**candidate["reason"], "matchSource": "manual"},
        )
        self.evaluate_matches([match["id"]])
        return {
            "status": "created",
            "match": self._public_match(self.rss_repository.get_match(match["id"])),
        }

    def _subscription_target_from_match(self, match, item, context, torra_rows):
        if not context["internalKey"].startswith("torra:"):
            return None, None, "watch_unit_missing"
        remote_id = _text(context.get("remoteId"))
        torra_row = next(
            (
                row for row in torra_rows
                if isinstance(row, dict) and _text(row.get("id")) == remote_id
            ),
            None,
        )
        if not remote_id or not torra_row:
            return None, None, "torra_subscription_missing"

        media_type = _media_type(item.get("media_type") or item.get("mediaType"))
        reason = match.get("reason") if isinstance(match.get("reason"), dict) else {}
        if media_type == "movie":
            season = None
            episode = None
        elif media_type == "tv":
            season_reason = reason.get("season") if isinstance(reason.get("season"), dict) else {}
            episode_reason = reason.get("episode") if isinstance(reason.get("episode"), dict) else {}
            season = _int(season_reason.get("unit"))
            episode = _int(episode_reason.get("unit"))
            episode_range = _positive_range(
                item.get("episode_start", item.get("episodeStart")),
                item.get("episode_end", item.get("episodeEnd")),
            )
            if (
                season <= 0
                or episode <= 0
                or not episode_range
                or not episode_range[0] <= episode <= episode_range[1]
            ):
                return None, None, "artifact_scope_unconfirmed"
        else:
            return None, None, "subscription_media_type_unconfirmed"

        expected_key = make_unit_key(
            context["internalKey"],
            media_type,
            season,
            episode,
        )
        internal_unit_id = torra_internal_unit_key(
            match.get("unitId"),
            context["internalKey"],
            context["publicKey"],
        )
        if internal_unit_id != expected_key:
            return None, None, "candidate_scope_mismatch"
        return {
            "unit_key": expected_key,
            "subscription_key": context["internalKey"],
            "season_number": season,
            "episode_number": episode,
            "torra_subscription_id": remote_id,
            "first_success_at": "",
            "baseline_artifact_key": "",
            "baseline_score": None,
            "baseline_rule_hash": "",
            "current_evidence": {},
            "subscription_target_only": True,
        }, torra_row, ""

    def _evaluation_unit(self, internal_match, context, torra_rows, *, match=None, item=None):
        internal_unit_id = torra_internal_unit_key(
            internal_match.get("unit_key"),
            context["internalKey"],
            context["publicKey"],
        )
        unit = self.watch_repository.get_watch_unit(internal_unit_id)
        if not unit:
            if match and item:
                return self._subscription_target_from_match(match, item, context, torra_rows)
            return None, None, "watch_unit_missing"
        if _text(unit.get("subscription_key")) != context["internalKey"]:
            return None, None, "watch_unit_missing"
        torra_id = _text(unit.get("torra_subscription_id"))
        torra_row = next(
            (
                row for row in torra_rows
                if isinstance(row, dict) and _text(row.get("id")) == torra_id
            ),
            None,
        )
        if not torra_id or not torra_row:
            return None, None, "torra_subscription_missing"
        return unit, torra_row, ""

    @staticmethod
    def _evaluation_identity_valid(item, subscription, torra_row):
        item_type = _media_type(item.get("media_type") or item.get("mediaType"))
        item_tmdb = _tmdb_id(item)
        subscription_tmdb = _tmdb_id(subscription)
        torra_tmdb = _tmdb_id(torra_row)
        return not (
            _text(item.get("identity_status") or item.get("identityStatus")) != "identified"
            or item_type not in {"movie", "tv"}
            or not item_tmdb
            or not subscription_tmdb
            or not torra_tmdb
            or len({item_tmdb, subscription_tmdb, torra_tmdb}) != 1
        )

    def _evaluation_context(self, match, torra_rows):
        internal_match = self.rss_repository.get_match_internal(match.get("id"))
        item = self.rss_repository.get_item(match.get("itemId"), public=False)
        if not internal_match or not item:
            return None, "match_context_missing"
        context, context_error = self._subscription_context(
            internal_match.get("subscription_key"),
            torra_rows=torra_rows,
        )
        if context_error or not context:
            return None, context_error or "subscription_missing"
        unit, torra_row, unit_error = self._evaluation_unit(
            internal_match,
            context,
            torra_rows,
            match=match,
            item=item,
        )
        if unit_error:
            return None, unit_error
        subscription = context["subscription"]
        torra_id = _text(unit.get("torra_subscription_id"))
        if not self._evaluation_identity_valid(item, subscription, torra_row):
            return None, "identity_unconfirmed"
        if not self._torra_owner_matches(subscription, unit, torra_row):
            return None, "torra_subscription_owner_mismatch"
        if not self._candidate(item, subscription, unit):
            return None, "candidate_scope_mismatch"
        artifact_key = rss_artifact_key(item)
        target_key = rss_target_key(item)
        if not artifact_key or not target_key:
            return None, "artifact_scope_unconfirmed"
        self.rss_repository.set_match_binding(
            match["id"],
            torra_subscription_id=torra_id,
            target_key=target_key,
            artifact_key=artifact_key,
        )
        return {
            "match": self.rss_repository.get_match(match["id"]),
            "item": item,
            "subscription": subscription,
            "torraRow": torra_row,
            "torraSubscriptionId": torra_id,
            "unit": unit,
            "artifactKey": artifact_key,
            "targetKey": target_key,
        }, ""

    def _record_shadow_action(self, context, result):
        artifact_key = context["artifactKey"]
        rule_hash = _text(result.get("ruleHash"))
        reason = _text(result.get("reason"))
        revision = rule_hash or stable_payload_hash({
            "artifactKey": artifact_key,
            "reason": reason,
        })
        claim = self.watch_repository.claim_action(
            f"rss-candidate-evaluation:{artifact_key}:{revision[:20]}",
            context["match"]["subscriptionId"],
            "fluxa",
            SHADOW_EVALUATION_ACTION_TYPE,
            unit_key=context["targetKey"],
            request_summary={
                "matchId": context["match"]["id"],
                "artifactKey": artifact_key,
                "targetKey": context["targetKey"],
                "source": "private-rss-shadow",
            },
        )
        action = claim.get("action") or {}
        if claim.get("disposition") in {"claimed", "reclaimed"}:
            action = self.watch_repository.complete_action(
                action["action_id"],
                "succeeded",
                {
                    "evaluationStatus": result.get("status"),
                    "decision": result.get("decision"),
                    "reason": reason,
                    "candidateScore": result.get("candidateScore"),
                    "ruleHash": rule_hash,
                },
            )
        return _text(action.get("action_id"))

    def _save_shadow_result(self, contexts, result):
        contexts = [context for context in contexts if isinstance(context, dict)]
        if not contexts:
            return []
        action_id = self._record_shadow_action(contexts[0], result)
        evaluated_at = _as_utc(self.clock())
        if isinstance(evaluated_at, datetime):
            evaluated_at = evaluated_at.isoformat().replace("+00:00", "Z")
        return self.rss_repository.save_match_evaluation(
            [context["match"]["id"] for context in contexts],
            {
                **result,
                "actionId": action_id,
                "evaluatedAt": _text(evaluated_at),
            },
        )

    def _blocked_shadow_result(self, matches, reason):
        results = []
        for match in matches:
            action_context = {
                "match": match,
                "artifactKey": match.get("artifactKey") or f"match:{match['id']}",
                "targetKey": match.get("targetKey") or match.get("unitId") or match["id"],
            }
            results.extend(self._save_shadow_result([action_context], {
                "status": "blocked",
                "decision": "temporarily_unconfirmed",
                "reason": reason,
                "candidateScore": None,
                "baselineScore": None,
                "ruleId": "",
                "ruleHash": "",
            }))
        return results

    @staticmethod
    def _unconfirmed_shadow_result(
        reason,
        *,
        baseline_score=None,
        rule_id="",
        rule_hash="",
    ):
        return {
            "status": "blocked",
            "decision": "temporarily_unconfirmed",
            "reason": reason,
            "candidateScore": None,
            "baselineScore": baseline_score,
            "ruleId": rule_id,
            "ruleHash": rule_hash,
        }

    def _read_shadow_inputs(self, torra):
        try:
            if hasattr(torra, "is_configured") and not torra.is_configured():
                raise RuntimeError("torra unavailable")
            torra_rows = torra.list_subscriptions()
            rules = torra.list_meta_weight_rules()
        except Exception:
            return None

        snapshots = []
        rule_hashes = {}
        for rule in rules:
            rule_id = _text(rule.get("id"))
            if not rule_id:
                continue
            rule_hash = stable_payload_hash(rule)
            rule_hashes[rule_id] = rule_hash
            snapshots.append({"ruleId": rule_id, "ruleHash": rule_hash, "rule": rule})
        self.rss_repository.save_rule_snapshots(snapshots)
        baseline_inputs = {"qbSummary": {}, "symediaRows": []}
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        if qb is not None:
            try:
                summary = qb.summary()
                if isinstance(summary, dict):
                    baseline_inputs["qbSummary"] = summary
            except Exception:
                pass
        symedia = getattr(self.analysis, "symedia", None) if self.analysis else None
        if symedia is not None:
            try:
                page = symedia.list_transfer_history(200)
                if isinstance(page, dict) and isinstance(page.get("rows"), list):
                    baseline_inputs["symediaRows"] = page["rows"]
            except Exception:
                pass
        return torra_rows, rules, rule_hashes, baseline_inputs

    @staticmethod
    def _score_summary(score, version_summary):
        return {
            "versionSummary": _text(version_summary)[:240],
            "versionState": _text(score.get("versionState"))[:40],
            "versionName": _text(score.get("versionName"))[:120],
            "scoreBreakdown": [
                {
                    "field": _text(row.get("field"))[:80],
                    "label": _text(row.get("label"))[:120],
                    "score": row.get("score"),
                }
                for row in score.get("breakdown") or []
                if isinstance(row, dict) and isinstance(row.get("score"), (int, float))
            ],
        }

    def _baseline_for_context(self, rule, rule_hash, context, baseline_inputs):
        unit = context["unit"]
        resolved = resolve_baseline_artifact(
            context["subscription"],
            context["torraRow"],
            unit,
            qb_summary=baseline_inputs.get("qbSummary"),
            symedia_rows=baseline_inputs.get("symediaRows"),
        )
        if resolved.get("status") == "ready":
            try:
                score = score_rss_candidate(rule, {
                    "title": resolved.get("versionSummary"),
                    "size_bytes": resolved.get("sizeBytes"),
                })
            except ShadowScoringUnsupported as exc:
                resolved = {"status": "unconfirmed", "reason": exc.code}
            else:
                if score.get("versionState") == "unconfirmed":
                    resolved = {
                        "status": "unconfirmed",
                        "reason": "baseline_version_unconfirmed",
                    }
                else:
                    summary = {
                        **self._score_summary(score, resolved.get("versionSummary")),
                        "artifactKey": resolved.get("artifactKey"),
                        "sources": list(resolved.get("sources") or []),
                    }
                    if any((
                        _text(unit.get("baseline_artifact_key")) != _text(resolved.get("artifactKey")),
                        unit.get("baseline_score") != score["score"],
                        _text(unit.get("baseline_rule_hash")) != rule_hash,
                    )) and not self._is_subscription_target(unit):
                        self.watch_repository.save_baseline(
                            [unit["unit_key"]],
                            resolved.get("artifactKey"),
                            score["score"],
                            rule_hash,
                            summary,
                        )
                    return score["score"], summary, ""

        persisted_score = unit.get("baseline_score")
        persisted_hash = _text(unit.get("baseline_rule_hash"))
        if (
            not isinstance(persisted_score, bool)
            and isinstance(persisted_score, (int, float))
            and persisted_hash == rule_hash
        ):
            evidence = unit.get("current_evidence") if isinstance(unit.get("current_evidence"), dict) else {}
            summary = evidence.get("baselineSummary") if isinstance(evidence.get("baselineSummary"), dict) else {}
            return float(persisted_score), summary, ""
        return None, {}, _text(resolved.get("reason")) or "baseline_version_unconfirmed"

    def _save_scored_contexts(self, contexts, results):
        if not contexts or not results:
            return []
        action_id = self._record_shadow_action(contexts[0], results[0])
        evaluated_at = _as_utc(self.clock())
        if isinstance(evaluated_at, datetime):
            evaluated_at = evaluated_at.isoformat().replace("+00:00", "Z")
        saved = []
        for context, result in zip(contexts, results):
            saved.extend(self.rss_repository.save_match_evaluation(
                [context["match"]["id"]],
                {
                    **result,
                    "actionId": action_id,
                    "evaluatedAt": _text(evaluated_at),
                },
            ))
        return saved

    def _score_artifact_contexts(self, rules, rule_hashes, contexts, baseline_inputs):
        primary = contexts[0]
        rule, reason = select_subscription_rule(
            rules,
            {**primary["subscription"], **primary["torraRow"]},
        )
        if not rule:
            return self._save_shadow_result(contexts, self._unconfirmed_shadow_result(reason))
        rule_id = _text(rule.get("id"))
        rule_hash = rule_hashes.get(rule_id) or stable_payload_hash(rule)
        try:
            score = score_rss_candidate(rule, primary["item"])
        except ShadowScoringUnsupported as exc:
            return self._save_shadow_result(contexts, self._unconfirmed_shadow_result(
                exc.code,
                rule_id=rule_id,
                rule_hash=rule_hash,
            ))
        candidate_summary = self._score_summary(score, primary["item"].get("title"))
        results = []
        for context in contexts:
            baseline_score, baseline_summary, baseline_reason = self._baseline_for_context(
                rule,
                rule_hash,
                context,
                baseline_inputs,
            )
            if score["versionState"] == "rejected":
                decision = "rule_rejected"
            elif baseline_score is None:
                decision = (
                    "initial_candidate"
                    if self._is_subscription_target(context["unit"])
                    else "waiting_baseline"
                )
            elif score["score"] > float(baseline_score):
                decision = "upgrade_available"
            elif score["score"] == float(baseline_score):
                decision = "same_score"
            else:
                decision = "lower_score"
            results.append({
                "status": "scored",
                "decision": decision,
                "reason": (
                    "version_fields_unconfirmed"
                    if score["versionState"] == "unconfirmed"
                    else baseline_reason or "shadow_only_no_download"
                ),
                "candidateScore": score["score"],
                "baselineScore": baseline_score,
                "candidateSummary": candidate_summary,
                "baselineSummary": baseline_summary,
                "ruleId": rule_id,
                "ruleHash": rule_hash,
            })
        return self._save_scored_contexts(contexts, results)

    def _evaluate_artifact_contexts(
        self,
        rules,
        rule_hashes,
        baseline_inputs,
        artifact_key,
        contexts,
    ):
        stored_rows = self.rss_repository.list_internal_matches_for_artifact(artifact_key)
        owners = {
            (_text(row.get("torra_subscription_id")), _text(row.get("target_key")))
            for row in stored_rows
            if _text(row.get("torra_subscription_id")) or _text(row.get("target_key"))
        }
        if len(owners) != 1:
            result = {
                **self._unconfirmed_shadow_result("artifact_owner_conflict"),
                "decision": "ownership_conflict",
            }
            return self._save_shadow_result(contexts, result)
        else:
            return self._score_artifact_contexts(
                rules,
                rule_hashes,
                contexts,
                baseline_inputs,
            )

    def _expand_evaluation_contexts(self, matches, torra_rows):
        all_matches = {match["id"]: match for match in matches}
        contexts = {}
        reasons = {}
        for _ in range(12):
            contexts = {}
            reasons = {}
            for match in all_matches.values():
                context, reason = self._evaluation_context(match, torra_rows)
                if context:
                    contexts[match["id"]] = context
                else:
                    reasons[match["id"]] = reason
            refs = {
                (context["match"]["subscriptionId"], context["match"]["unitId"])
                for context in contexts.values()
            }
            expanded = self.rss_repository.list_matches_for_units(refs)
            for artifact_key in {context["artifactKey"] for context in contexts.values()}:
                expanded.extend(
                    self.rss_repository.get_match(row["id"])
                    for row in self.rss_repository.list_internal_matches_for_artifact(artifact_key)
                )
            changed = False
            for match in expanded:
                if match and match["id"] not in all_matches:
                    all_matches[match["id"]] = match
                    changed = True
            if not changed:
                break
        return list(contexts.values()), [
            (all_matches[match_id], reason)
            for match_id, reason in reasons.items()
        ]

    @staticmethod
    def _winner_decision(match):
        baseline = match.get("baselineScore")
        score = match.get("candidateScore")
        if baseline is None:
            return (
                "best_available"
                if match.get("decision") == "initial_candidate"
                else "best_waiting_baseline"
            )
        if score > baseline:
            return "current_best"
        if score == baseline:
            return "same_score"
        return "lower_score"

    def _reconcile_champions(self, contexts):
        fresh = {
            match["id"]: match
            for match in self.rss_repository.list_matches_by_ids(
                [context["match"]["id"] for context in contexts]
            )
        }
        context_by_match = {context["match"]["id"]: context for context in contexts}
        groups = {}
        artifacts = {}
        for match_id, match in fresh.items():
            groups.setdefault((match["subscriptionId"], match["unitId"]), []).append(match)
            artifacts.setdefault(match.get("artifactKey") or f"match:{match_id}", []).append(match)

        eligible_groups = {
            group_key: [
                match for match in matches
                if match.get("evaluationStatus") == "scored"
                and match.get("decision") != "rule_rejected"
                and not isinstance(match.get("candidateScore"), bool)
                and isinstance(match.get("candidateScore"), (int, float))
            ]
            for group_key, matches in groups.items()
        }
        available_artifacts = {
            match.get("artifactKey") or f"match:{match['id']}"
            for matches in eligible_groups.values()
            for match in matches
        }
        winners = {}
        for _ in range(len(available_artifacts) + 1):
            winners = {}
            for group_key, matches in eligible_groups.items():
                available = [
                    match for match in matches
                    if (match.get("artifactKey") or f"match:{match['id']}") in available_artifacts
                ]
                if available:
                    winners[group_key] = min(available, key=lambda match: (
                        -float(match["candidateScore"]),
                        _text(match.get("createdAt")),
                        _text(match.get("artifactKey")),
                        match["id"],
                    ))
            partial_range_winners = set()
            for artifact_key, rows in artifacts.items():
                covered_groups = {
                    (row["subscriptionId"], row["unitId"])
                    for row in rows
                    if any(row["id"] == eligible["id"] for eligible in eligible_groups.get(
                        (row["subscriptionId"], row["unitId"]),
                        [],
                    ))
                }
                if len(covered_groups) <= 1:
                    continue
                won_groups = {
                    group_key for group_key in covered_groups
                    if group_key in winners
                    and (winners[group_key].get("artifactKey") or f"match:{winners[group_key]['id']}") == artifact_key
                }
                if won_groups and won_groups != covered_groups:
                    partial_range_winners.add(artifact_key)
            if not partial_range_winners:
                break
            available_artifacts.difference_update(partial_range_winners)

        outcomes = {artifact_key: [] for artifact_key in artifacts}
        for group_key, matches in eligible_groups.items():
            winner = winners.get(group_key)
            winner_artifact = (
                winner.get("artifactKey") or f"match:{winner['id']}"
                if winner
                else ""
            )
            for match in matches:
                artifact_key = match.get("artifactKey") or f"match:{match['id']}"
                outcomes.setdefault(artifact_key, []).append({
                    "winner": artifact_key == winner_artifact,
                    "decision": self._winner_decision(match),
                    "score": float(match["candidateScore"]),
                })

        canonical_ids = {
            artifact_key: min(
                rows,
                key=lambda match: (_text(match.get("createdAt")), match["id"]),
            )["id"]
            for artifact_key, rows in artifacts.items()
        }
        updates = []
        priority = {
            "best_waiting_baseline": 0,
            "lower_score": 1,
            "same_score": 2,
            "best_available": 3,
            "current_best": 4,
        }
        for artifact_key, matches in artifacts.items():
            artifact_outcomes = outcomes.get(artifact_key) or []
            if not artifact_outcomes:
                updates.append({
                    "matchIds": [match["id"] for match in matches],
                    "decision": matches[0].get("decision"),
                    "reason": matches[0].get("evaluationReason"),
                    "bestCandidate": False,
                })
                continue
            wins_all = all(outcome["winner"] for outcome in artifact_outcomes)
            decision = (
                min(artifact_outcomes, key=lambda outcome: priority[outcome["decision"]])["decision"]
                if wins_all
                else "superseded"
            )
            updates.append({
                "matchIds": [match["id"] for match in matches],
                "decision": decision,
                "reason": (
                    "version_fields_unconfirmed"
                    if wins_all and any(
                        isinstance(match.get("candidateSummary"), dict)
                        and match["candidateSummary"].get("versionState") == "unconfirmed"
                        for match in matches
                    )
                    else "shadow_only_no_download"
                    if wins_all
                    else "higher_scored_candidate"
                ),
                "bestCandidate": wins_all,
            })
        self.rss_repository.save_candidate_decisions(updates)

        for group_key, group_matches in groups.items():
            winner = winners.get(group_key)
            if not winner:
                context = next((
                    context_by_match.get(match["id"])
                    for match in group_matches
                    if context_by_match.get(match["id"])
                ), None)
                if context:
                    if not self._is_subscription_target(context["unit"]):
                        self.watch_repository.clear_candidate_champion(
                            context["unit"]["unit_key"],
                            max((
                                _text(match.get("evaluatedAt") or match.get("createdAt"))
                                for match in group_matches
                            ), default=""),
                        )
                continue
            artifact_key = winner.get("artifactKey") or f"match:{winner['id']}"
            context = context_by_match.get(winner["id"])
            if not context:
                continue
            if not self._is_subscription_target(context["unit"]):
                self.watch_repository.save_candidate_champion(
                    context["unit"]["unit_key"],
                    match_id=canonical_ids[artifact_key],
                    score=winner["candidateScore"],
                    last_candidate_at=winner.get("evaluatedAt") or winner.get("createdAt"),
                    artifact_key=artifact_key,
                    decision=self._winner_decision(winner),
                    summary=winner.get("candidateSummary"),
                )

    def evaluate_matches(self, match_ids):
        matches = self.rss_repository.list_matches_by_ids(match_ids)
        if not matches:
            return []
        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        if torra is None:
            return self._blocked_shadow_result(matches, "torra_unavailable")
        shadow_inputs = self._read_shadow_inputs(torra)
        if shadow_inputs is None:
            return self._blocked_shadow_result(matches, "torra_rule_read_failed")
        torra_rows, rules, rule_hashes, baseline_inputs = shadow_inputs

        contexts, blocked_matches = self._expand_evaluation_contexts(matches, torra_rows)
        blocked = []
        for match, reason in blocked_matches:
            blocked.extend(self._blocked_shadow_result([match], reason))

        grouped = {}
        for context in contexts:
            grouped.setdefault(context["artifactKey"], []).append(context)
        evaluated = list(blocked)
        for artifact_key, artifact_contexts in grouped.items():
            evaluated.extend(self._evaluate_artifact_contexts(
                rules,
                rule_hashes,
                baseline_inputs,
                artifact_key,
                artifact_contexts,
            ))
        self._reconcile_champions(contexts)
        return self.rss_repository.list_matches_by_ids([
            match["id"] for match in evaluated
        ])

    @staticmethod
    def _resource_category(value):
        normalized = _text(value).replace("\\", "/").rstrip("/").lower()
        leaf = normalized.rsplit("/", 1)[-1]
        aliases = {
            "日番": "anime_jp",
            "日漫": "anime_jp",
            "国番": "anime_cn",
            "国漫": "anime_cn",
            "国产动画": "anime_cn",
            "国产剧": "tv_cn",
            "日韩剧": "tv_asia",
            "南亚剧": "tv_asia",
            "欧美剧": "tv_western",
            "欧美动画": "tv_western",
            "港台剧": "tv_hk_tw",
            "综艺": "variety",
            "电影": "movie",
        }
        for key, category in TORRA_MEDIA_CATEGORIES.items():
            candidates = {
                key.lower(),
                category["label"].lower(),
                category["directory"].lower(),
            }
            if normalized in candidates or leaf in candidates:
                return {"key": key, **category}
        mapped = aliases.get(normalized) or aliases.get(leaf)
        return {"key": mapped, **TORRA_MEDIA_CATEGORIES[mapped]} if mapped else None

    @staticmethod
    def _resource_scope(item):
        media_type = _media_type(item.get("media_type") or item.get("mediaType"))
        if media_type == "movie":
            return "整部电影", True
        season = _int(item.get("season_number", item.get("seasonNumber")))
        episode_range = _positive_range(
            item.get("episode_start", item.get("episodeStart")),
            item.get("episode_end", item.get("episodeEnd")),
        )
        if media_type != "tv" or season <= 0:
            return "范围待确认", False
        if not episode_range:
            return f"S{season:02d} 季包", True
        start, end = episode_range
        return (
            f"S{season:02d}E{start:02d}"
            if start == end
            else f"S{season:02d}E{start:02d}–E{end:02d}"
        ), True

    @staticmethod
    def _resource_title_key(value):
        text = re.sub(
            r"(?i)(?:\.(?:torrent|mkv|mp4|m4v|ts|m2ts|avi|wmv|mov))+$",
            "",
            _text(value),
        )
        return _compact(text)

    @classmethod
    def _resource_release_key(cls, value):
        text = re.sub(
            r"(?i)(S0*\d{1,2})E0*\d{1,4}(?:[-~](?:S0*\d{1,2})?E?0*\d{1,4})?",
            r"\1",
            _text(value),
        )
        return cls._resource_title_key(text)

    @classmethod
    def _qb_resource_task_from_summary(cls, summary, item):
        if not isinstance(summary, dict) or summary.get("connected") is not True:
            return None
        title_key = cls._resource_title_key(item.get("title"))
        release_key = cls._resource_release_key(item.get("title"))
        for task in summary.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            task_title_key = cls._resource_title_key(task.get("name"))
            task_release_key = cls._resource_release_key(task.get("name"))
            if title_key and title_key == task_title_key:
                return task
            if release_key and release_key == task_release_key:
                return task
        return None

    def _resource_subscription_candidates(self, item, torra_rows):
        item_type = _media_type(item.get("media_type") or item.get("mediaType"))
        item_tmdb = _tmdb_id(item)
        item_imdb = _imdb_id(item)
        item_season = _int(item.get("season_number", item.get("seasonNumber")))
        candidates = []
        for row in torra_rows if isinstance(torra_rows, list) else []:
            if not isinstance(row, dict):
                continue
            row_type = _media_type(row.get("media_type") or row.get("mediaType") or row.get("type"))
            if row_type and row_type != item_type:
                continue
            row_tmdb = _tmdb_id(row)
            row_imdb = _imdb_id(row)
            if item_tmdb:
                if not row_tmdb or row_tmdb != item_tmdb:
                    continue
            elif item_imdb:
                if not row_imdb or row_imdb != item_imdb:
                    continue
            else:
                continue
            row_season = self._subscription_season(row)
            if (
                item_type == "tv"
                and item_season > 0
                and row_season is not None
                and row_season > 0
                and row_season != item_season
            ):
                continue
            candidates.append(row)
        return candidates

    def _resource_downloader_id(self, torra, row, blockers):
        environment = self.analysis.environment or {}
        configured = _text(environment.get("TORRA_DOWNLOADER_ID"))
        if not configured and torra is not None and hasattr(torra, "resolve_downloader_id"):
            try:
                configured = _text(torra.resolve_downloader_id(""))
            except Exception:
                configured = ""
        row_downloader = _text(
            (row or {}).get("downloader_id") or (row or {}).get("downloaderId")
        )
        if not configured:
            blockers.append({
                "code": "RSS_RESOURCE_DOWNLOADER_UNCONFIRMED",
                "message": "Fluxa 当前 qB 与 Torra 下载器映射暂未确认",
            })
        elif row_downloader and row_downloader != configured:
            blockers.append({
                "code": "RSS_RESOURCE_DOWNLOADER_MISMATCH",
                "message": "资源关联订阅没有使用 Fluxa 当前连接的 qB 下载器",
            })
        return configured

    def _build_resource_download_preview(self, item_id):
        item = self.rss_repository.get_item(item_id, public=False)
        if not item:
            raise RssExactDownloadError("RSS_ITEM_NOT_FOUND", "RSS 种子条目不存在", 404)
        display_item = self.rss_repository.get_item(item_id) or {}
        blockers = []

        def add_blocker(code, message):
            if not any(row["code"] == code for row in blockers):
                blockers.append({"code": str(code), "message": str(message)})

        media_type = _media_type(item.get("media_type") or item.get("mediaType"))
        identity_status = _text(item.get("identity_status") or item.get("identityStatus"))
        scope_label, scope_confirmed = self._resource_scope(item)
        if identity_status != "identified":
            add_blocker("RSS_RESOURCE_IDENTITY_UNCONFIRMED", "资源媒体身份尚未确认")
        if media_type not in {"movie", "tv"}:
            add_blocker("RSS_RESOURCE_MEDIA_TYPE_UNCONFIRMED", "资源媒体类型尚未确认")
        if media_type == "tv" and not scope_confirmed:
            add_blocker("RSS_RESOURCE_SCOPE_UNCONFIRMED", "剧集季号或资源范围尚未确认")
        if not _text(item.get("download_url")):
            add_blocker("RSS_RESOURCE_UNAVAILABLE", "RSS 来源没有提供可用下载资源")
        if not _tmdb_id(item) and not _imdb_id(item):
            add_blocker("RSS_RESOURCE_IDENTITY_UNCONFIRMED", "资源缺少可核验的 TMDB 或 IMDb 身份")

        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        torra_rows = []
        if torra is None:
            add_blocker("RSS_RESOURCE_TORRA_UNAVAILABLE", "Torra 当前不可读")
        else:
            try:
                if hasattr(torra, "is_configured") and not torra.is_configured():
                    raise RuntimeError("torra unavailable")
                torra_rows = torra.list_subscriptions()
            except Exception:
                add_blocker("RSS_RESOURCE_TORRA_UNAVAILABLE", "Torra 订阅当前不可读")
                torra_rows = []

        candidates = self._resource_subscription_candidates(item, torra_rows)
        if len(candidates) > 1:
            add_blocker(
                "RSS_RESOURCE_SUBSCRIPTION_CONFLICT",
                "同一媒体范围存在多个 Torra 订阅，不能自动选择下载归属",
            )
        torra_row = candidates[0] if len(candidates) == 1 else None
        if torra_row and (
            torra_row.get("is_running") is True or torra_row.get("is_mutating") is True
        ):
            add_blocker("RSS_RESOURCE_TORRA_BUSY", "Torra 当前正在处理该订阅")

        category = None
        category_reason = ""
        route_source = ""
        save_path = ""
        qb_category = ""
        environment = self.analysis.environment if self.analysis else {}
        environment = environment or {}
        root = _text(environment.get("TORRA_DOWNLOAD_ROOT")) or TORRA_DOWNLOAD_ROOT_DEFAULT
        root = root.replace("\\", "/").rstrip("/")
        if not root.startswith("/") or "/../" in f"{root}/" or root.endswith("/.."):
            add_blocker("RSS_RESOURCE_DOWNLOAD_ROOT_INVALID", "Torra 下载根目录配置无效")
            root = ""

        if torra_row:
            path_category = self._resource_category(
                torra_row.get("save_path")
                or torra_row.get("savePath")
                or torra_row.get("download_path")
            )
            field_categories = [
                self._resource_category(torra_row.get(key))
                for key in (
                    "resolved_category", "media_category", "category",
                    "qb_category", "download_category",
                )
            ]
            field_categories = [value for value in field_categories if value]
            category_keys = {
                value["key"] for value in ([path_category] if path_category else []) + field_categories
            }
            if len(category_keys) > 1:
                add_blocker(
                    "RSS_RESOURCE_CATEGORY_CONFLICT",
                    "Torra 订阅分类与保存目录不一致",
                )
            elif category_keys:
                category = next(
                    value for value in ([path_category] if path_category else []) + field_categories
                    if value["key"] in category_keys
                )
                category_reason = "沿用唯一 Torra 订阅的八分类"
                route_source = "torra_subscription"
            qb_category = _text(
                torra_row.get("qb_category") or torra_row.get("download_category")
            )
            save_path = _text(
                torra_row.get("save_path")
                or torra_row.get("savePath")
                or torra_row.get("download_path")
            ).replace("\\", "/").rstrip("/")

        if category is None and media_type in {"movie", "tv"} and identity_status == "identified":
            try:
                from app.subscription_compat_runtime import _resolve_category

                resolved, reason = _resolve_category({
                    "title": _text(item.get("title")),
                    "media_type": media_type,
                    "tmdb_id": _tmdb_id(item),
                    "target_season": _int(item.get("season_number")) or None,
                })
            except Exception:
                resolved, reason = None, "媒体分类证据当前不可读"
            category = self._resource_category((resolved or {}).get("key")) if resolved else None
            if category:
                category_reason = _text(reason) or "依据媒体身份自动分类"
                route_source = "media_identity"

        if category is None:
            add_blocker(
                "RSS_RESOURCE_CATEGORY_UNCONFIRMED",
                "无法可靠判断八分类，未向 qB 提交",
            )
        elif media_type == "movie" and category["key"] != "movie":
            add_blocker("RSS_RESOURCE_CATEGORY_CONFLICT", "电影资源只能进入 10-电影")
        elif media_type == "tv" and category["key"] == "movie":
            add_blocker("RSS_RESOURCE_CATEGORY_CONFLICT", "电视剧资源不能进入电影目录")

        if category and root:
            expected_path = f"{root}/{category['directory']}"
            if save_path and save_path != expected_path:
                add_blocker(
                    "RSS_RESOURCE_SAVE_PATH_MISMATCH",
                    "Torra 订阅保存目录没有落在当前八分类接力路径",
                )
            else:
                save_path = expected_path

        downloader_id = self._resource_downloader_id(torra, torra_row, blockers)
        qb_summary = {}
        if qb is None:
            add_blocker("RSS_RESOURCE_QB_UNAVAILABLE", "qB 当前不可读")
        else:
            try:
                qb_summary = qb.summary()
            except Exception:
                qb_summary = {}
            if not isinstance(qb_summary, dict) or qb_summary.get("connected") is not True:
                add_blocker("RSS_RESOURCE_QB_UNAVAILABLE", "qB 当前不可读")
                qb_summary = {}

        subscription = torra_row or {
            "title": _text(display_item.get("mediaTitle")),
            "name": _text(display_item.get("sourceTitle")),
            "keyword": _text(item.get("title")),
        }
        unit = {
            "season_number": _int(item.get("season_number")) if media_type == "tv" else None,
            "episode_number": _int(item.get("episode_start")) if media_type == "tv" else None,
        }
        existing_resource_task = self._qb_resource_task_from_summary(qb_summary, item)
        if existing_resource_task:
            if _text(existing_resource_task.get("status")).lower() == "completed":
                add_blocker("RSS_RESOURCE_ALREADY_IN_QB", "qB 已存在同一 RSS 资源任务")
            else:
                add_blocker("RSS_RESOURCE_QB_BUSY", "同一 RSS 资源正在 qB 中处理")
        else:
            for task in qb_summary.get("tasks") or []:
                if not isinstance(task, dict):
                    continue
                if qb_task_matches(task, subscription, unit):
                    add_blocker("RSS_RESOURCE_QB_BUSY", "当前媒体范围已有 qB 下载任务")
                    break

        config, config_error = self._analysis_config()
        if config_error:
            add_blocker("RSS_RESOURCE_EXECUTION_DISABLED", "RSS 资源下载执行当前未启用")
            config = {}
        if _text(config.get("torra_quality_execution_mode")).lower() != "manual":
            add_blocker("RSS_RESOURCE_EXECUTION_DISABLED", "RSS 资源下载需要启用人工执行模式")
        if not _truthy(environment.get("MCC_TORRA_REWASH_DOWNLOAD_ENABLED")):
            add_blocker("RSS_RESOURCE_DOWNLOAD_GATE_DISABLED", "RSS 资源下载硬门禁未开启")

        routing = {}
        if not blockers and category and save_path and downloader_id:
            remote_id = _text((torra_row or {}).get("id"))
            routing = {
                "subscriptionKey": f"torra:{remote_id}" if remote_id else f"rss-item:{item['id']}",
                "itemId": _text(item.get("id")),
                "downloadUrl": _text(item.get("download_url")),
                "downloadUrlDigest": _secret_digest(item.get("download_url")),
                "savePath": save_path,
                "downloaderId": downloader_id,
                "category": qb_category,
                "categoryKey": category["key"],
                "categoryDirectory": category["directory"],
                "routeSource": route_source,
                "itemFingerprint": _text(item.get("fingerprint")),
            }
        fingerprint = stable_payload_hash({
            "itemId": item.get("id"),
            "itemFingerprint": item.get("fingerprint"),
            "identityStatus": identity_status,
            "mediaType": media_type,
            "tmdbId": _tmdb_id(item),
            "imdbId": _imdb_id(item),
            "seasonNumber": item.get("season_number"),
            "episodeStart": item.get("episode_start"),
            "episodeEnd": item.get("episode_end"),
            "downloadUrlDigest": _secret_digest(item.get("download_url")),
            "routing": {
                key: routing.get(key)
                for key in (
                    "subscriptionKey", "savePath", "downloaderId", "category",
                    "categoryKey", "categoryDirectory", "routeSource",
                )
            },
        })
        observed_at = _as_utc(self.clock()) or datetime.now(timezone.utc)
        public = {
            "status": "ready" if not blockers and routing else "blocked",
            "ready": bool(not blockers and routing),
            "capabilityState": "ready" if not blockers and routing else "blocked",
            "itemId": _text(item.get("id")),
            "mediaType": media_type,
            "scopeLabel": scope_label,
            "categoryKey": category["key"] if category else "",
            "categoryLabel": category["label"] if category else "",
            "categoryDirectory": category["directory"] if category else "",
            "classificationReason": category_reason,
            "routeSource": route_source,
            "subscriptionMatched": bool(torra_row),
            "destinationConfigured": bool(routing),
            "blockers": blockers,
            "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
        }
        return public, routing, fingerprint

    def preview_resource_download(self, item_id, *, persist=True):
        public, _routing, fingerprint = self._build_resource_download_preview(item_id)
        if not public["ready"] or not persist:
            return {**public, "previewToken": "", "expiresAt": ""}, fingerprint
        now = _as_utc(self.clock()) or datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=10)
        token = secrets.token_urlsafe(32)
        with self._exact_preview_lock:
            self._resource_previews = {
                key: value for key, value in self._resource_previews.items()
                if value["expiresAt"] > now
            }
            self._resource_previews[token] = {
                "itemId": _text(item_id),
                "fingerprint": fingerprint,
                "expiresAt": expires_at,
            }
        return {
            **public,
            "previewToken": token,
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        }, fingerprint

    def _preview_exact_match(self, match_id):
        match = self.rss_repository.get_match(match_id)
        if not match:
            return {"status": "missing"}

        blockers = []

        def add_blocker(code, message):
            if not any(row["code"] == code for row in blockers):
                blockers.append({"code": code, "message": message})

        candidate_score = match.get("candidateScore")
        baseline_score = match.get("baselineScore")
        candidate_score = (
            float(candidate_score)
            if isinstance(candidate_score, (int, float)) and not isinstance(candidate_score, bool)
            else None
        )
        baseline_score = (
            float(baseline_score)
            if isinstance(baseline_score, (int, float)) and not isinstance(baseline_score, bool)
            else None
        )
        if match.get("evaluationStatus") != "scored":
            add_blocker("RSS_EXACT_SCORE_UNCONFIRMED", "候选评分暂未确认")
        candidate_summary = (
            match.get("candidateSummary")
            if isinstance(match.get("candidateSummary"), dict)
            else {}
        )
        if candidate_summary.get("versionState") != "accepted":
            add_blocker(
                "RSS_EXACT_VERSION_UNCONFIRMED",
                "候选版本条件尚未完全确认",
            )
        if not match.get("bestCandidate") or match.get("decision") != "current_best":
            add_blocker("RSS_EXACT_NOT_CURRENT_BEST", "当前候选不是该季集唯一最佳版本")
        if candidate_score is None or baseline_score is None:
            add_blocker("RSS_EXACT_BASELINE_UNCONFIRMED", "当前版本基线暂未确认")
        elif baseline_score is not None and candidate_score <= baseline_score:
            add_blocker("RSS_EXACT_NOT_STRICT_UPGRADE", "候选分数没有严格高于当前版本")
        if not match.get("torraLinked"):
            add_blocker("RSS_EXACT_SUBSCRIPTION_UNCONFIRMED", "Torra 订阅绑定暂未确认")
        if not _text(match.get("targetKey")) or not _text(match.get("artifactKey")):
            add_blocker("RSS_EXACT_TARGET_UNCONFIRMED", "候选季集范围或产物身份暂未确认")
        if match.get("status") == "confirmed" or _text(match.get("downloadActionId")):
            add_blocker("RSS_EXACT_ALREADY_SUBMITTED", "该候选已经存在下载动作")

        internal_match = self.rss_repository.get_match_internal(match["id"])
        item = self.rss_repository.get_item(match.get("itemId"), public=False)
        if not internal_match or not item:
            add_blocker("RSS_EXACT_CONTEXT_UNAVAILABLE", "候选上下文当前不可读")
        elif not _text(item.get("download_url")):
            add_blocker("RSS_EXACT_RESOURCE_UNAVAILABLE", "RSS 来源没有提供可用下载资源")

        torra = getattr(self.analysis, "torra", None) if self.analysis else None
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        torra_row = None
        routing = {}
        if torra is None:
            add_blocker("RSS_EXACT_TORRA_UNAVAILABLE", "Torra 当前不可读")
        elif not internal_match or not item:
            # Context is already reported above; do not dereference partial
            # matches or perform unrelated upstream reads.
            pass
        else:
            try:
                if hasattr(torra, "is_configured") and not torra.is_configured():
                    raise RuntimeError("torra unavailable")
                torra_rows = torra.list_subscriptions()
                rules = torra.list_meta_weight_rules()
            except Exception:
                add_blocker("RSS_EXACT_TORRA_UNAVAILABLE", "Torra 订阅或规则当前不可读")
            else:
                context, context_error = self._subscription_context(
                    internal_match.get("subscription_key") if internal_match else "",
                    torra_rows=torra_rows,
                )
                if context_error or not context:
                    add_blocker("RSS_EXACT_SUBSCRIPTION_UNCONFIRMED", "Torra 订阅绑定暂未确认")
                else:
                    unit, torra_row, unit_error = self._evaluation_unit(
                        internal_match,
                        context,
                        torra_rows,
                        match=match,
                        item=item,
                    )
                    if unit_error or not unit or not torra_row:
                        add_blocker("RSS_EXACT_SUBSCRIPTION_UNCONFIRMED", "Torra 订阅季集目标暂未确认")
                    elif not self._evaluation_identity_valid(item, context["subscription"], torra_row):
                        add_blocker("RSS_EXACT_IDENTITY_UNCONFIRMED", "作品身份与 Torra 订阅当前不一致")
                    elif not self._torra_owner_matches(context["subscription"], unit, torra_row):
                        add_blocker("RSS_EXACT_SUBSCRIPTION_UNCONFIRMED", "Torra 订阅所有权当前不一致")
                    elif not self._candidate(item, context["subscription"], unit):
                        add_blocker("RSS_EXACT_TARGET_UNCONFIRMED", "候选季集范围与当前订阅不一致")
                    elif (
                        rss_target_key(item) != _text(match.get("targetKey"))
                        or rss_artifact_key(item) != _text(match.get("artifactKey"))
                    ):
                        add_blocker("RSS_EXACT_TARGET_CHANGED", "候选季集范围或产物身份已经变化")
                    else:
                        rule, rule_error = select_subscription_rule(
                            rules,
                            {**context["subscription"], **torra_row},
                        )
                        if not rule:
                            add_blocker("RSS_EXACT_RULE_UNCONFIRMED", "适用 Torra 规则暂未确认")
                        else:
                            current_rule_hash = stable_payload_hash(rule)
                            if current_rule_hash != _text(match.get("ruleHash")):
                                add_blocker("RSS_EXACT_RULE_CHANGED", "Torra 规则已经变化，需要重新评分")
                            try:
                                current_candidate = score_rss_candidate(rule, item)
                            except ShadowScoringUnsupported:
                                add_blocker("RSS_EXACT_SCORE_UNCONFIRMED", "候选无法使用当前 Torra 规则重新评分")
                            else:
                                if candidate_score is None or float(current_candidate["score"]) != candidate_score:
                                    add_blocker("RSS_EXACT_SCORE_CHANGED", "候选评分已经变化，需要重新确认冠军")
                                if current_candidate.get("versionState") != "accepted":
                                    add_blocker(
                                        "RSS_EXACT_VERSION_UNCONFIRMED",
                                        "候选版本条件尚未完全确认",
                                    )

                            qb_summary = {}
                            if qb is None:
                                add_blocker("RSS_EXACT_QB_UNAVAILABLE", "qB 当前不可读")
                            else:
                                try:
                                    qb_summary = qb.summary()
                                except Exception:
                                    qb_summary = {}
                                if not isinstance(qb_summary, dict) or qb_summary.get("connected") is not True:
                                    add_blocker("RSS_EXACT_QB_UNAVAILABLE", "qB 当前不可读")
                                    qb_summary = {}
                                elif any(
                                    qb_task_matches(task, context["subscription"], unit)
                                    for task in qb_summary.get("tasks") or []
                                    if isinstance(task, dict)
                                ):
                                    add_blocker("RSS_EXACT_QB_BUSY", "当前季集已有 qB 下载任务")

                            symedia_rows = []
                            symedia = getattr(self.analysis, "symedia", None) if self.analysis else None
                            if symedia is not None:
                                try:
                                    page = symedia.list_transfer_history(200)
                                    if isinstance(page, dict) and isinstance(page.get("rows"), list):
                                        symedia_rows = page["rows"]
                                except Exception:
                                    symedia_rows = []
                            resolved = resolve_baseline_artifact(
                                context["subscription"],
                                torra_row,
                                unit,
                                qb_summary=qb_summary,
                                symedia_rows=symedia_rows,
                            )
                            baseline_summary = (
                                match.get("baselineSummary")
                                if isinstance(match.get("baselineSummary"), dict)
                                else {}
                            )
                            if resolved.get("status") != "ready":
                                add_blocker("RSS_EXACT_BASELINE_UNCONFIRMED", "当前版本无法从最新证据重新确认")
                            elif resolved.get("status") == "ready" and (
                                _text(resolved.get("artifactKey"))
                                != _text(baseline_summary.get("artifactKey"))
                            ):
                                add_blocker("RSS_EXACT_BASELINE_CHANGED", "当前已入库版本已经变化，需要重新建立基线")
                            elif resolved.get("status") == "ready":
                                try:
                                    current_baseline = score_rss_candidate(rule, {
                                        "title": resolved.get("versionSummary"),
                                        "size_bytes": resolved.get("sizeBytes"),
                                    })
                                except ShadowScoringUnsupported:
                                    add_blocker("RSS_EXACT_BASELINE_UNCONFIRMED", "当前版本无法使用最新 Torra 规则评分")
                                else:
                                    if current_baseline.get("versionState") == "unconfirmed":
                                        add_blocker(
                                            "RSS_EXACT_BASELINE_UNCONFIRMED",
                                            "当前版本条件尚未完全确认",
                                        )
                                    if baseline_score is None or float(current_baseline["score"]) != baseline_score:
                                        add_blocker("RSS_EXACT_BASELINE_CHANGED", "当前版本分数已经变化，需要重新建立基线")

                    if torra_row and (
                        torra_row.get("is_running") is True
                        or torra_row.get("is_mutating") is True
                    ):
                        add_blocker("RSS_EXACT_TORRA_BUSY", "Torra 当前正在处理该订阅")
                    if torra_row:
                        save_path = _text(
                            torra_row.get("save_path")
                            or torra_row.get("savePath")
                            or torra_row.get("download_path")
                        )
                        downloader_id = _text(
                            torra_row.get("downloader_id") or torra_row.get("downloaderId")
                        )
                        configured_downloader_id = _text(
                            (self.analysis.environment or {}).get("TORRA_DOWNLOADER_ID")
                        )
                        category = _text(
                            torra_row.get("qb_category")
                            or torra_row.get("download_category")
                        )
                        if not save_path:
                            add_blocker("RSS_EXACT_SAVE_PATH_UNCONFIRMED", "Torra 订阅保存目录暂未确认")
                        if not downloader_id:
                            add_blocker("RSS_EXACT_DOWNLOADER_UNCONFIRMED", "Torra 订阅下载器暂未确认")
                        elif not configured_downloader_id:
                            add_blocker(
                                "RSS_EXACT_QB_DOWNLOADER_UNCONFIRMED",
                                "Fluxa 当前 qB 与 Torra 下载器映射暂未确认",
                            )
                        elif configured_downloader_id != downloader_id:
                            add_blocker(
                                "RSS_EXACT_QB_DOWNLOADER_MISMATCH",
                                "Torra 订阅没有使用 Fluxa 当前连接的 qB 下载器",
                            )
                        if (
                            save_path and downloader_id and internal_match and item
                            and configured_downloader_id == downloader_id
                        ):
                            routing = {
                                "subscriptionKey": _text(internal_match.get("subscription_key")),
                                "downloadUrl": _text(item.get("download_url")),
                                "downloadUrlDigest": _secret_digest(item.get("download_url")),
                                "savePath": save_path,
                                "downloaderId": downloader_id,
                                "category": category,
                                "artifactKey": _text(match.get("artifactKey")),
                                "targetKey": _text(match.get("targetKey")),
                                "ruleHash": _text(match.get("ruleHash")),
                                "baselineArtifactKey": _text(
                                    (match.get("baselineSummary") or {}).get("artifactKey")
                                    if isinstance(match.get("baselineSummary"), dict) else ""
                                ),
                            }
        observed_at = _as_utc(self.clock()) or datetime.now(timezone.utc)
        ready = not blockers and bool(routing)
        return {
            "status": "ready" if ready else "blocked",
            "ready": ready,
            "capabilityState": "ready" if ready else "blocked",
            "matchId": match["id"],
            "targetKey": _text(match.get("targetKey")),
            "versionSummary": _text(candidate_summary.get("versionSummary"))[:240],
            "candidateScore": candidate_score,
            "baselineScore": baseline_score,
            "scoreGain": (
                candidate_score - baseline_score
                if candidate_score is not None and baseline_score is not None
                else None
            ),
            "blockers": blockers,
            "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
            "_execution": routing,
        }

    def _build_artifact_exact_preview(self, group_id):
        group = self.rss_repository.get_candidate_artifact_group(group_id)
        if not group:
            raise RssExactDownloadError("RSS_ARTIFACT_GROUP_NOT_FOUND", "RSS 产物候选不存在", 404)
        blockers = []

        def add_blocker(code, message):
            if not any(row["code"] == code for row in blockers):
                blockers.append({"code": str(code), "message": str(message)})

        if group.get("state") != "upgrade_available" or not group.get("winsAllCoveredUnits"):
            add_blocker(
                "RSS_EXACT_ARTIFACT_NOT_UNIQUE_WINNER",
                "该产物没有在覆盖的全部季集中成为唯一冠军",
            )
        unit_results = group.get("unitResults") or []
        validations = []
        for result in unit_results:
            match = result.get("match") if isinstance(result, dict) else None
            if not isinstance(match, dict) or not _text(match.get("id")):
                add_blocker("RSS_EXACT_CONTEXT_UNAVAILABLE", "候选季集上下文当前不可读")
                continue
            validation = self._preview_exact_match(match["id"])
            validations.append(validation)
            for blocker in validation.get("blockers") or []:
                if isinstance(blocker, dict):
                    add_blocker(blocker.get("code"), blocker.get("message"))
        routes = [
            validation.get("_execution")
            for validation in validations
            if isinstance(validation.get("_execution"), dict) and validation.get("_execution")
        ]
        routing_keys = (
            "subscriptionKey", "downloadUrl", "downloadUrlDigest", "savePath",
            "downloaderId", "category", "artifactKey", "ruleHash",
        )
        if len(routes) != len(unit_results) or not routes:
            add_blocker("RSS_EXACT_ROUTE_UNCONFIRMED", "订阅级下载参数暂未确认")
        elif any(
            any(_text(route.get(key)) != _text(routes[0].get(key)) for key in routing_keys)
            for route in routes[1:]
        ):
            add_blocker("RSS_EXACT_ROUTE_CONFLICT", "覆盖季集的订阅级下载参数不一致")
        match_ids = sorted({
            _text(result.get("match", {}).get("id"))
            for result in unit_results if isinstance(result, dict) and isinstance(result.get("match"), dict)
            and _text(result.get("match", {}).get("id"))
        })
        fingerprint = stable_payload_hash({
            "groupId": group.get("id"),
            "state": group.get("state"),
            "winsAllCoveredUnits": bool(group.get("winsAllCoveredUnits")),
            "matches": [{
                "id": match.get("id"),
                "version": match.get("version"),
                "decision": match.get("decision"),
                "candidateScore": match.get("candidateScore"),
                "baselineScore": match.get("baselineScore"),
                "ruleHash": match.get("ruleHash"),
                "artifactKey": match.get("artifactKey"),
                "targetKey": match.get("targetKey"),
                "baselineArtifactKey": (
                    match.get("baselineSummary", {}).get("artifactKey")
                    if isinstance(match.get("baselineSummary"), dict) else ""
                ),
            } for match in (
                result.get("match") for result in unit_results if isinstance(result, dict)
            ) if isinstance(match, dict)],
            "routing": [{key: route.get(key) for key in routing_keys if key != "downloadUrl"} for route in routes],
        })
        representative = group.get("representativeMatch") or {}
        candidate_score = group.get("bestCandidateScore")
        baseline_score = group.get("baselineScore")
        public = {
            "status": "ready" if not blockers else "blocked",
            "ready": not blockers,
            "capabilityState": "ready" if not blockers else "blocked",
            "groupId": group.get("id"),
            "matchId": representative.get("id"),
            "versionSummary": _text(
                (representative.get("candidateSummary") or {}).get("versionSummary")
                if isinstance(representative.get("candidateSummary"), dict) else ""
            )[:240],
            "episodeLabel": _text(group.get("episodeLabel"))[:80],
            "coveredUnitCount": len(group.get("coveredUnits") or []),
            "coveredEpisodeStart": group.get("coveredEpisodeStart"),
            "coveredEpisodeEnd": group.get("coveredEpisodeEnd"),
            "candidateScore": candidate_score,
            "baselineScore": baseline_score,
            "scoreGain": (
                float(candidate_score) - float(baseline_score)
                if isinstance(candidate_score, (int, float))
                and not isinstance(candidate_score, bool)
                and isinstance(baseline_score, (int, float))
                and not isinstance(baseline_score, bool)
                else None
            ),
            "downloadCategory": _text(routes[0].get("category"))[:120] if routes else "",
            "downloadCategoryConfigured": bool(_text(routes[0].get("category"))) if routes else False,
            "destinationConfigured": bool(routes),
            "blockers": blockers,
            "observedAt": (_as_utc(self.clock()) or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
        }
        return public, (routes[0] if routes else {}), fingerprint, match_ids

    def preview_artifact_exact_download(self, group_id, *, persist=True):
        public, _routing, fingerprint, match_ids = self._build_artifact_exact_preview(group_id)
        if not public["ready"] or not persist:
            return {**public, "previewToken": "", "expiresAt": ""}, fingerprint, match_ids
        now = _as_utc(self.clock()) or datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=10)
        token = secrets.token_urlsafe(32)
        with self._exact_preview_lock:
            self._exact_previews = {
                key: value for key, value in self._exact_previews.items()
                if value["expiresAt"] > now
            }
            self._exact_previews[token] = {
                "groupId": _text(group_id),
                "fingerprint": fingerprint,
                "matchIds": list(match_ids),
                "expiresAt": expires_at,
            }
        return {
            **public,
            "previewToken": token,
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        }, fingerprint, match_ids

    def preview_exact_download(self, match_id):
        page = self.rss_repository.list_candidate_artifact_groups(match_id=match_id, limit=1)
        if not page.get("groups"):
            return {"status": "missing"}
        preview, _fingerprint, _match_ids = self.preview_artifact_exact_download(
            page["groups"][0]["id"]
        )
        return preview

    @staticmethod
    def _qb_task_for_tag(qb, tag):
        try:
            summary = qb.summary()
        except Exception:
            return None
        if not isinstance(summary, dict) or summary.get("connected") is not True:
            return None
        for task in summary.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            tags = {
                value.strip() for value in str(task.get("tags") or "").split(",") if value.strip()
            }
            if tag in tags:
                return task
        return None

    def _qb_task_for_resource(self, qb, item_id):
        item = self.rss_repository.get_item(item_id, public=False)
        if not item:
            return None
        try:
            summary = qb.summary()
        except Exception:
            return None
        return self._qb_resource_task_from_summary(summary, item)

    def execute_artifact_exact_download(self, group_id, preview_token, idempotency_key):
        now = _as_utc(self.clock()) or datetime.now(timezone.utc)
        with self._exact_preview_lock:
            receipt = self._exact_previews.get(_text(preview_token))
        if not receipt or receipt["groupId"] != _text(group_id) or receipt["expiresAt"] <= now:
            raise RssExactDownloadError("RSS_EXACT_PREVIEW_EXPIRED", "精准下载预览已过期，请重新预览", 409)
        request_key = _text(idempotency_key)
        if not 12 <= len(request_key) <= 128:
            raise RssExactDownloadError("RSS_EXACT_IDEMPOTENCY_INVALID", "幂等键长度必须为 12 到 128 个字符", 422)
        fingerprint = receipt["fingerprint"]
        match_ids = receipt["matchIds"]
        stored_idempotency = "rss-exact:" + stable_payload_hash({
            "fingerprint": fingerprint,
        })[:48]
        existing = self.watch_repository.get_action_by_idempotency(stored_idempotency)
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        if existing:
            request_summary = existing.get("request_summary") or {}
            if (
                _text(request_summary.get("groupId")) != _text(group_id)
                or _text(request_summary.get("previewFingerprint")) != fingerprint
            ):
                raise RssExactDownloadError(
                    "RSS_EXACT_IDEMPOTENCY_CONFLICT", "精准下载收据与当前候选不一致", 409
                )
            if existing.get("status") in {"succeeded", "failed", "cancelled"}:
                if existing.get("status") == "succeeded":
                    self.rss_repository.record_artifact_download_action(match_ids, existing["action_id"])
                return existing
            external_job = _text(existing.get("external_job_id"))
            audit_tag = external_job.removeprefix("qb-tag:") if external_job.startswith("qb-tag:") else ""
            task = self._qb_task_for_tag(qb, audit_tag) if qb is not None and audit_tag else None
            if task:
                self.rss_repository.record_artifact_download_action(match_ids, existing["action_id"])
                existing = self.watch_repository.complete_action(existing["action_id"], "succeeded", {
                    "accepted": True,
                    "confirmed": True,
                    "qbHash": _text(task.get("hash")),
                    "groupId": _text(group_id),
                })
                return existing
        current, routing, fingerprint, match_ids = self._build_artifact_exact_preview(group_id)
        if not current["ready"] or fingerprint != receipt["fingerprint"] or match_ids != receipt["matchIds"]:
            raise RssExactDownloadError("RSS_EXACT_PREVIEW_STALE", "候选事实已经变化，请重新预览", 409)
        config, config_error = self._analysis_config()
        if config_error:
            raise RssExactDownloadError("RSS_EXACT_ANALYSIS_DISABLED", "Torra 规则评分当前未启用", 503)
        mode = _text(config.get("torra_quality_execution_mode")).lower()
        if mode != "manual":
            raise RssExactDownloadError("RSS_EXACT_EXECUTION_DISABLED", "精准下载执行授权未开启", 503)
        environment = self.analysis.environment or {}
        if not _truthy(environment.get("MCC_TORRA_REWASH_DOWNLOAD_ENABLED")):
            raise RssExactDownloadError("RSS_EXACT_DOWNLOAD_GATE_DISABLED", "精准下载硬门禁未开启", 503)
        if qb is None or not hasattr(qb, "add_torrent"):
            raise RssExactDownloadError("RSS_EXACT_QB_WRITE_UNAVAILABLE", "qB 精准提交能力不可用", 503)
        claim = self.watch_repository.claim_action(
            stored_idempotency,
            routing["subscriptionKey"],
            "qbittorrent",
            "rss-exact-download",
            unit_key=_text(group_id),
            request_summary={
                "source": "manual-rss-artifact",
                "groupId": _text(group_id),
                "matchIds": match_ids,
                "previewFingerprint": fingerprint,
                "requestKeyHash": stable_payload_hash({"requestKey": request_key})[:16],
            },
            cooldown_seconds=int(config.get("torra_quality_min_interval_minutes") or 60) * 60,
            rate_limits={
                "hourly": int(config.get("torra_quality_hourly_limit") or 4),
                "daily": int(config.get("torra_quality_daily_limit") or 30),
            },
            require_idle=True,
        )
        disposition = claim.get("disposition")
        action = claim.get("action")
        if disposition == "conflict":
            raise RssExactDownloadError("RSS_EXACT_IDEMPOTENCY_CONFLICT", "幂等键已用于其他动作", 409)
        if disposition in {"global_busy", "cooldown", "rate_limited"}:
            codes = {
                "global_busy": "RSS_EXACT_GLOBAL_BUSY",
                "cooldown": "RSS_EXACT_COOLDOWN",
                "rate_limited": "RSS_EXACT_RATE_LIMITED",
            }
            raise RssExactDownloadError(codes[disposition], "当前已有精准下载或已达到安全限额", 409)
        if not action:
            raise RssExactDownloadError("RSS_EXACT_ACTION_UNAVAILABLE", "精准下载动作无法建立", 500)
        action_id = _text(action.get("action_id"))
        external_job = _text(action.get("external_job_id"))
        audit_tag = external_job.removeprefix("qb-tag:") if external_job.startswith("qb-tag:") else ""
        if disposition in {"replay", "in_progress", "resume"} and audit_tag:
            task = self._qb_task_for_tag(qb, audit_tag)
            if task and action.get("status") not in {"succeeded", "failed", "cancelled"}:
                self.rss_repository.record_artifact_download_action(match_ids, action_id)
                action = self.watch_repository.complete_action(action_id, "succeeded", {
                    "accepted": True,
                    "confirmed": True,
                    "qbHash": _text(task.get("hash")),
                    "groupId": _text(group_id),
                })
                return action
            if disposition in {"replay", "in_progress"}:
                return action
        if disposition == "replay":
            if action.get("status") == "succeeded":
                self.rss_repository.record_artifact_download_action(match_ids, action_id)
            return action
        if not audit_tag:
            audit_tag = f"fluxa-action-{action_id[:8]}"
            action = self.watch_repository.save_external_job(
                action_id, f"qb-tag:{audit_tag}", status="submitted", lease_seconds=60
            )
        try:
            qb.add_torrent(
                routing["downloadUrl"],
                routing["savePath"],
                routing["category"],
                ["fluxa-rss", audit_tag],
            )
        except Exception as exc:
            task = self._qb_task_for_tag(qb, audit_tag)
            if not task:
                raise RssExactDownloadError(
                    "RSS_EXACT_QB_SUBMIT_UNCONFIRMED",
                    "qB 提交结果暂未确认，请检查带 Fluxa 标签的任务",
                    502,
                ) from exc
        task = self._qb_task_for_tag(qb, audit_tag)
        if task:
            self.rss_repository.record_artifact_download_action(match_ids, action_id)
            action = self.watch_repository.complete_action(action_id, "succeeded", {
                "accepted": True,
                "confirmed": True,
                "qbHash": _text(task.get("hash")),
                "groupId": _text(group_id),
            })
        return action

    def execute_resource_download(self, item_id, preview_token, idempotency_key):
        now = _as_utc(self.clock()) or datetime.now(timezone.utc)
        with self._exact_preview_lock:
            receipt = self._resource_previews.get(_text(preview_token))
        if not receipt or receipt["itemId"] != _text(item_id) or receipt["expiresAt"] <= now:
            raise RssExactDownloadError(
                "RSS_RESOURCE_PREVIEW_EXPIRED",
                "资源下载预览已过期，请重新预览",
                409,
            )
        request_key = _text(idempotency_key)
        if not 12 <= len(request_key) <= 128:
            raise RssExactDownloadError(
                "RSS_RESOURCE_IDEMPOTENCY_INVALID",
                "幂等键长度必须为 12 到 128 个字符",
                422,
            )
        fingerprint = receipt["fingerprint"]
        stored_idempotency = "rss-resource:" + stable_payload_hash({
            "fingerprint": fingerprint,
        })[:48]
        existing = self.watch_repository.get_action_by_idempotency(stored_idempotency)
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        if existing:
            request_summary = existing.get("request_summary") or {}
            if (
                _text(request_summary.get("itemId")) != _text(item_id)
                or _text(request_summary.get("previewFingerprint")) != fingerprint
            ):
                raise RssExactDownloadError(
                    "RSS_RESOURCE_IDEMPOTENCY_CONFLICT",
                    "资源下载收据与当前资源不一致",
                    409,
                )
            if existing.get("status") in {"succeeded", "failed", "cancelled"}:
                return existing
            external_job = _text(existing.get("external_job_id"))
            audit_tag = external_job.removeprefix("qb-tag:") if external_job.startswith("qb-tag:") else ""
            tagged_task = self._qb_task_for_tag(qb, audit_tag) if qb is not None and audit_tag else None
            task = tagged_task or (
                self._qb_task_for_resource(qb, item_id) if qb is not None else None
            )
            if task:
                return self.watch_repository.complete_action(existing["action_id"], "succeeded", {
                    "accepted": True,
                    "confirmed": True,
                    "alreadyPresent": tagged_task is None,
                    "qbHash": _text(task.get("hash")),
                    "itemId": _text(item_id),
                })

        current, routing, current_fingerprint = self._build_resource_download_preview(item_id)
        if not current["ready"] or current_fingerprint != fingerprint:
            raise RssExactDownloadError(
                "RSS_RESOURCE_PREVIEW_STALE",
                "资源身份、分类或 qB 状态已经变化，请重新预览",
                409,
            )
        environment = self.analysis.environment or {}
        if not _truthy(environment.get("MCC_TORRA_REWASH_DOWNLOAD_ENABLED")):
            raise RssExactDownloadError(
                "RSS_RESOURCE_DOWNLOAD_GATE_DISABLED",
                "RSS 资源下载硬门禁未开启",
                503,
            )
        if qb is None or not hasattr(qb, "add_torrent"):
            raise RssExactDownloadError(
                "RSS_RESOURCE_QB_WRITE_UNAVAILABLE",
                "qB 资源提交能力不可用",
                503,
            )
        config, config_error = self._analysis_config()
        if config_error or _text(config.get("torra_quality_execution_mode")).lower() != "manual":
            raise RssExactDownloadError(
                "RSS_RESOURCE_EXECUTION_DISABLED",
                "RSS 资源下载需要启用人工执行模式",
                503,
            )
        claim = self.watch_repository.claim_action(
            stored_idempotency,
            routing["subscriptionKey"],
            "qbittorrent",
            RESOURCE_DOWNLOAD_ACTION_TYPE,
            unit_key=f"rss-item:{_text(item_id)}",
            request_summary={
                "source": "manual-rss-resource",
                "itemId": _text(item_id),
                "previewFingerprint": fingerprint,
                "categoryKey": routing["categoryKey"],
                "categoryDirectory": routing["categoryDirectory"],
                "routeSource": routing["routeSource"],
                "requestKeyHash": stable_payload_hash({"requestKey": request_key})[:16],
            },
            rate_limits={
                "hourly": int(config.get("torra_quality_hourly_limit") or 4),
                "daily": int(config.get("torra_quality_daily_limit") or 30),
            },
            require_idle=True,
            require_provider_idle=True,
        )
        disposition = claim.get("disposition")
        action = claim.get("action")
        if disposition == "conflict":
            raise RssExactDownloadError(
                "RSS_RESOURCE_IDEMPOTENCY_CONFLICT",
                "幂等键已用于其他资源下载",
                409,
            )
        if disposition in {"global_busy", "rate_limited"}:
            code = (
                "RSS_RESOURCE_GLOBAL_BUSY"
                if disposition == "global_busy"
                else "RSS_RESOURCE_RATE_LIMITED"
            )
            raise RssExactDownloadError(code, "当前已有 qB 提交动作或已达到安全限额", 409)
        if not action:
            raise RssExactDownloadError(
                "RSS_RESOURCE_ACTION_UNAVAILABLE",
                "资源下载动作无法建立",
                500,
            )
        action_id = _text(action.get("action_id"))
        external_job = _text(action.get("external_job_id"))
        audit_tag = external_job.removeprefix("qb-tag:") if external_job.startswith("qb-tag:") else ""
        if disposition in {"replay", "in_progress", "resume"} and audit_tag:
            task = self._qb_task_for_tag(qb, audit_tag)
            if task and action.get("status") not in {"succeeded", "failed", "cancelled"}:
                return self.watch_repository.complete_action(action_id, "succeeded", {
                    "accepted": True,
                    "confirmed": True,
                    "qbHash": _text(task.get("hash")),
                    "itemId": _text(item_id),
                })
            if disposition in {"replay", "in_progress"}:
                return action
        if disposition == "replay":
            return action
        if not audit_tag:
            audit_tag = f"fluxa-resource-{action_id[:8]}"
            action = self.watch_repository.save_external_job(
                action_id,
                f"qb-tag:{audit_tag}",
                status="submitted",
                lease_seconds=60,
            )
        try:
            qb.add_torrent(
                routing["downloadUrl"],
                routing["savePath"],
                routing["category"],
                ["fluxa-rss-resource", audit_tag],
            )
        except Exception as exc:
            tagged_task = self._qb_task_for_tag(qb, audit_tag)
            task = tagged_task or self._qb_task_for_resource(qb, item_id)
            if not task:
                raise RssExactDownloadError(
                    "RSS_RESOURCE_QB_SUBMIT_UNCONFIRMED",
                    "qB 提交结果暂未确认，请检查带 Fluxa 标签的任务",
                    502,
                ) from exc
        tagged_task = self._qb_task_for_tag(qb, audit_tag)
        task = tagged_task or self._qb_task_for_resource(qb, item_id)
        if task:
            action = self.watch_repository.complete_action(action_id, "succeeded", {
                "accepted": True,
                "confirmed": True,
                "alreadyPresent": tagged_task is None,
                "qbHash": _text(task.get("hash")),
                "itemId": _text(item_id),
            })
        return action

    def recover_pending_resource_download(self):
        action = self.watch_repository.find_inflight_action(
            "qbittorrent", RESOURCE_DOWNLOAD_ACTION_TYPE
        )
        if not action:
            return None
        request_summary = action.get("request_summary") or {}
        if request_summary.get("source") != "manual-rss-resource":
            return None
        external_job = _text(action.get("external_job_id"))
        audit_tag = external_job.removeprefix("qb-tag:") if external_job.startswith("qb-tag:") else ""
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        item_id = _text(request_summary.get("itemId"))
        tagged_task = (
            self._qb_task_for_tag(qb, audit_tag)
            if qb is not None and audit_tag
            else None
        )
        task = tagged_task or (
            self._qb_task_for_resource(qb, item_id)
            if qb is not None and item_id
            else None
        )
        if not task or not item_id:
            return None
        action_id = _text(action.get("action_id"))
        self.watch_repository.complete_action(action_id, "succeeded", {
            "accepted": True,
            "confirmed": True,
            "alreadyPresent": tagged_task is None,
            "qbHash": _text(task.get("hash")),
            "itemId": item_id,
        })
        return {
            "status": "succeeded",
            "actionId": action_id,
            "itemId": item_id,
        }

    def recover_pending_exact_download(self):
        action = self.watch_repository.find_inflight_action(
            "qbittorrent", "rss-exact-download"
        )
        if not action:
            return None
        request_summary = action.get("request_summary") or {}
        if request_summary.get("source") != "manual-rss-artifact":
            return None
        external_job = _text(action.get("external_job_id"))
        audit_tag = external_job.removeprefix("qb-tag:") if external_job.startswith("qb-tag:") else ""
        qb = getattr(self.analysis, "qb", None) if self.analysis else None
        task = self._qb_task_for_tag(qb, audit_tag) if qb is not None and audit_tag else None
        if not task:
            return None
        match_ids = sorted({
            _text(value) for value in request_summary.get("matchIds") or [] if _text(value)
        })
        group_id = _text(request_summary.get("groupId"))
        if not match_ids or not group_id:
            return None
        action_id = _text(action.get("action_id"))
        self.rss_repository.record_artifact_download_action(match_ids, action_id)
        action = self.watch_repository.complete_action(action_id, "succeeded", {
            "accepted": True,
            "confirmed": True,
            "qbHash": _text(task.get("hash")),
            "groupId": group_id,
        })
        return {
            "status": "succeeded",
            "actionId": action_id,
            "groupId": group_id,
        }

    def backfill_watch_unit(self, unit_key, limit=200):
        unit = self.watch_repository.get_watch_unit(unit_key)
        if not unit:
            return {"scanned": 0, "created": 0, "evaluated": 0}
        _public_subscription_key, public_unit_key = self._public_match_keys(unit)
        rows = self.rss_repository.list_items_for_watch_backfill(
            unit,
            {unit["unit_key"], public_unit_key},
            limit=limit,
        )
        subscriptions = self._subscriptions()
        subscription = subscriptions.get(_text(unit.get("subscription_key")))
        if not subscription:
            return {"scanned": len(rows), "created": 0, "evaluated": 0}
        created = []
        with self.rss_repository.runtime.transaction(immediate=True) as connection:
            for item in rows:
                item = self._supplement_item_from_subscriptions(connection, item, subscriptions)
                candidate = self._candidate(item, subscription, unit)
                if not candidate:
                    continue
                match = self.rss_repository.create_match(
                    item["id"],
                    unit["subscription_key"],
                    unit["unit_key"],
                    candidate["reason"],
                    connection=connection,
                )
                if match:
                    created.append(match)
        evaluated = self.evaluate_matches([match["id"] for match in created])
        return {"scanned": len(rows), "created": len(created), "evaluated": len(evaluated)}

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

    def _subscription_target_analysis_context(self, match, context):
        evaluation, reason = self._evaluation_context(
            match,
            context.get("torraRows") or [],
        )
        if reason:
            return None, reason
        return {
            "match": evaluation["match"],
            "unit": evaluation["unit"],
            "subscription": {
                **evaluation["subscription"],
                **evaluation["torraRow"],
            },
            "torra_id": evaluation["torraSubscriptionId"],
        }, ""

    def _local_analysis_context(self, match):
        subscription_id = _text(match.get("subscriptionId"))
        unit = None
        if not subscription_id.startswith("torra:"):
            unit = self.watch_repository.get_watch_unit(match.get("unitId"))
            if not unit:
                return None, "watch_unit_missing"
        context, context_error = self._subscription_context(subscription_id)
        if context_error:
            return None, context_error
        internal_unit_id = torra_internal_unit_key(
            match.get("unitId"), context["internalKey"], context["publicKey"]
        )
        unit = unit or self.watch_repository.get_watch_unit(internal_unit_id)
        if not unit:
            if context["internalKey"].startswith("torra:"):
                return self._subscription_target_analysis_context(match, context)
            return None, "watch_unit_missing"
        if _text(unit.get("subscription_key")) != context["internalKey"]:
            return None, "watch_unit_missing"
        ends_at = _as_utc(unit.get("observation_ends_at"))
        current = _as_utc(self.clock())
        if unit.get("state") not in ACTIVE_WATCH_STATES or not ends_at or not current or ends_at <= current:
            self.rss_repository.update_match(match["id"], "expired")
            return None, "window_expired"
        subscription = context["subscription"]
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
            context["match"]["subscriptionId"],
            "torra",
            ANALYSIS_ACTION_TYPE,
            unit_key=context["match"]["unitId"],
            request_summary={"matchId": context["match"]["id"], "source": source},
            cooldown_seconds=max(60, _int(config.get("torra_quality_min_interval_minutes") or 60)) * 60,
            rate_limits={
                "hourly": max(1, _int(config.get("torra_quality_hourly_limit") or 4)),
                "daily": max(1, _int(config.get("torra_quality_daily_limit") or 30)),
            },
            require_idle=True,
            require_provider_idle=True,
        )

    def _cancel_reclaimed_context(self, claim, reason):
        code = PERMANENT_RECLAIM_CONTEXT_CODES[reason]
        return self.watch_repository.complete_action(
            claim["action"]["action_id"],
            "cancelled",
            {
                "reason": "rss_reclaim_context_invalid",
                "contextReason": reason,
            },
            error_code=code,
            error_message="RSS 匹配上下文已不可用",
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
            error_message = str(job.get("error") or "").strip() or f"Torra 分析任务{status}"
            self.watch_repository.complete_action(
                action_id,
                status,
                {"jobStatus": status},
                error_code=f"TORRA_ANALYSIS_{status.upper()}",
                error_message=error_message,
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
                "upgradeOptions": selection.get("upgrade_options") or [],
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
            if preclaimed and reason in PERMANENT_RECLAIM_CONTEXT_CODES:
                self._cancel_reclaimed_context(preclaimed, reason)
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

    def prepare_download(self, match_id, analysis_action_id, idempotency_key):
        match = self.rss_repository.get_match(match_id)
        if not match:
            return {"status": "missing", "reason": "match_missing"}
        if match.get("status") in {"ignored", "expired"}:
            return {"status": "blocked", "reason": "match_not_ready"}

        analysis_action = self.watch_repository.get_action(analysis_action_id)
        analysis_summary = analysis_action.get("request_summary") if analysis_action else {}
        if (
            not analysis_action
            or analysis_action.get("provider") != "torra"
            or analysis_action.get("action_type") != ANALYSIS_ACTION_TYPE
            or analysis_action.get("subscription_key") != match.get("subscriptionId")
            or analysis_action.get("unit_key") != match.get("unitId")
            or not isinstance(analysis_summary, dict)
            or analysis_summary.get("matchId") != match.get("id")
        ):
            return {"status": "blocked", "reason": "analysis_action_missing"}
        if analysis_action.get("status") != "succeeded":
            return {"status": "blocked", "reason": "analysis_action_not_ready"}

        result = analysis_action.get("response_summary") or {}
        selected = result.get("selectedCandidates")
        if not _text(result.get("analysisId")) or not isinstance(selected, dict) or not selected:
            return {"status": "blocked", "reason": "analysis_has_no_upgrade"}

        existing = self.watch_repository.get_action_by_idempotency(idempotency_key)
        if existing:
            summary = existing.get("request_summary") or {}
            if (
                existing.get("provider") != "torra"
                or existing.get("action_type") != DOWNLOAD_ACTION_TYPE
                or existing.get("subscription_key") != match.get("subscriptionId")
                or existing.get("unit_key") != match.get("unitId")
                or not isinstance(summary, dict)
                or summary.get("source") != MANUAL_SUBSCRIPTION_SOURCE
                or summary.get("analysisActionId") != analysis_action.get("action_id")
            ):
                return {"status": "conflict", "reason": "idempotency_conflict"}

        return {
            "status": "ready",
            "matchId": match["id"],
            "subscriptionId": match["subscriptionId"],
            "unitId": match["unitId"],
            "analysisActionId": analysis_action["action_id"],
        }

    def record_download(self, match_id, analysis_action_id, download_action):
        action = download_action if isinstance(download_action, dict) else {}
        match = self.rss_repository.get_match(match_id)
        analysis_action = self.watch_repository.get_action(analysis_action_id)
        analysis_summary = analysis_action.get("request_summary") if analysis_action else {}
        summary = action.get("request_summary") or {}
        if (
            not match
            or not analysis_action
            or analysis_action.get("provider") != "torra"
            or analysis_action.get("action_type") != ANALYSIS_ACTION_TYPE
            or analysis_action.get("status") != "succeeded"
            or analysis_action.get("subscription_key") != match.get("subscriptionId")
            or analysis_action.get("unit_key") != match.get("unitId")
            or not isinstance(analysis_summary, dict)
            or analysis_summary.get("matchId") != match.get("id")
            or action.get("provider") != "torra"
            or action.get("action_type") != DOWNLOAD_ACTION_TYPE
            or action.get("subscription_key") != match.get("subscriptionId")
            or action.get("unit_key") != match.get("unitId")
            or not isinstance(summary, dict)
            or summary.get("source") != MANUAL_SUBSCRIPTION_SOURCE
            or summary.get("analysisActionId") != _text(analysis_action_id)
        ):
            return {"status": "conflict", "reason": "download_action_mismatch"}

        action_id = _text(action.get("action_id"))
        if not action_id:
            return {"status": "conflict", "reason": "download_action_missing"}
        next_status = "triggered" if action.get("status") in {"failed", "cancelled"} else "confirmed"
        now = _as_utc(self.clock()) or datetime.now(timezone.utc)
        updated_at = now.isoformat().replace("+00:00", "Z")
        with self.rss_repository.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT match_status FROM rss_subscription_matches WHERE id=?",
                (str(match_id),),
            ).fetchone()
            if not row:
                return {"status": "missing", "reason": "match_missing"}
            current = _text(row["match_status"])
            if current in {"ignored", "expired"}:
                return {"status": "blocked", "reason": "match_not_ready"}
            if current == "confirmed":
                next_status = "confirmed"
            connection.execute(
                "UPDATE rss_subscription_matches "
                "SET match_status=?, trigger_action_id=?, updated_at=? WHERE id=?",
                (next_status, action_id, updated_at, str(match_id)),
            )
        return {
            "status": next_status,
            "matchId": str(match_id),
            "actionId": action_id,
        }

    def wake_pending_candidates(self, limit=2):
        matches = self.rss_repository.list_pending_evaluation_matches(
            max(1, min(int(limit or 2), 50))
        )
        evaluated = self.evaluate_matches([match["id"] for match in matches])
        return [
            {
                "matchId": match.get("id"),
                "status": "evaluated" if match.get("evaluationStatus") == "scored" else "blocked",
                "reason": match.get("evaluationReason") or "",
            }
            for match in evaluated
        ]

    def wake_matches(self, match_ids):
        evaluated = self.evaluate_matches(match_ids)
        return [
            {
                "matchId": match.get("id"),
                "status": "evaluated" if match.get("evaluationStatus") == "scored" else "blocked",
                "reason": match.get("evaluationReason") or "",
            }
            for match in evaluated
        ]


def register_rss_subscription_match(app, runtime):
    app.extensions["mcc_rss_subscription_match_runtime"] = runtime
    return runtime
