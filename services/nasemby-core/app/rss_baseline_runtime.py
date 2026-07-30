from __future__ import annotations

import hashlib
import re
import unicodedata

from app.private_rss_parser import extract_release_scope
from app.symedia_evidence_runtime import normalize_symedia_status


def _text(value):
    return str(value or "").strip()


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _media_type(value):
    normalized = _text(value).lower()
    if normalized in {"movie", "film", "电影"}:
        return "movie"
    if normalized in {"tv", "series", "电视剧", "剧集"}:
        return "tv"
    return ""


def _tmdb_id(mapping):
    mapping = mapping if isinstance(mapping, dict) else {}
    for key in ("tmdb_id", "tmdbId", "tmdbid"):
        value = _text(mapping.get(key))
        if value:
            return value
    return ""


def _basename(value):
    return _text(value).replace("\\", "/").rsplit("/", 1)[-1]


def _artifact_identity(value):
    name = unicodedata.normalize("NFKC", _basename(value)).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", name)


def _flatten_strings(value):
    if isinstance(value, dict):
        result = []
        for nested in value.values():
            result.extend(_flatten_strings(nested))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for nested in value:
            result.extend(_flatten_strings(nested))
        return result
    return [_text(value)] if _text(value) else []


def _positive_size(mapping):
    mapping = mapping if isinstance(mapping, dict) else {}
    for key in ("size_bytes", "sizeBytes", "file_size", "fileSize", "filesize", "size"):
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _scope_includes(value, media_type, season, episode):
    if media_type == "movie":
        return True
    parsed_type, parsed_season, start, end = extract_release_scope(value)
    return bool(
        parsed_type == "tv"
        and parsed_season == season
        and start is not None
        and int(start) <= episode <= int(end or start)
    )


def _add_candidate(target, value, source, size=None):
    name = _basename(value)
    identity = _artifact_identity(name)
    if not name or not identity:
        return
    candidate = target.setdefault(identity, {
        "versionSummary": name[:240],
        "sizes": set(),
        "sources": set(),
    })
    candidate["sources"].add(source)
    if size:
        candidate["sizes"].add(int(size))


def _torra_candidates(torra_row, unit, media_type):
    torra_row = torra_row if isinstance(torra_row, dict) else {}
    season = _integer(unit.get("season_number"))
    episode = _integer(unit.get("episode_number"))
    result = {}
    if media_type == "tv":
        if _integer(torra_row.get("season_number", torra_row.get("season"))) != season:
            return result
        for field in ("downloaded_episode_files", "library_episode_files"):
            mapping = torra_row.get(field)
            if not isinstance(mapping, dict):
                continue
            values = mapping.get(str(episode), mapping.get(episode))
            for value in _flatten_strings(values):
                _add_candidate(result, value, "torra")
        for field in ("downloaded_file_names", "library_file_names", "last_added_name"):
            for value in _flatten_strings(torra_row.get(field)):
                if _scope_includes(value, media_type, season, episode):
                    _add_candidate(result, value, "torra")
        return result

    for field in (
        "downloaded_file_names",
        "library_file_names",
        "last_added_name",
        "downloaded_episode_files",
        "library_episode_files",
    ):
        for value in _flatten_strings(torra_row.get(field)):
            _add_candidate(result, value, "torra")
    return result


def _symedia_candidates(rows, subscription, unit, media_type):
    expected_tmdb = _tmdb_id(subscription)
    season = _integer(unit.get("season_number"))
    episode = _integer(unit.get("episode_number"))
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or normalize_symedia_status(row.get("status")) is not True:
            continue
        if not expected_tmdb or _tmdb_id(row) != expected_tmdb:
            continue
        if _media_type(row.get("type") or row.get("media_type")) != media_type:
            continue
        if media_type == "tv":
            row_season = _integer(row.get("season") or row.get("season_number"))
            scope_text = " ".join(filter(None, (
                _text(row.get("season_episode")),
                _text(row.get("src")),
                _text(row.get("dest")),
            )))
            if row_season != season or not _scope_includes(scope_text, media_type, season, episode):
                continue
        value = _text(row.get("src")) or _text(row.get("dest"))
        _add_candidate(result, value, "symedia", _positive_size(row))
    return result


def _merge_candidates(*collections):
    merged = {}
    for collection in collections:
        for identity, source in collection.items():
            target = merged.setdefault(identity, {
                "versionSummary": source["versionSummary"],
                "sizes": set(),
                "sources": set(),
            })
            target["sizes"].update(source["sizes"])
            target["sources"].update(source["sources"])
    return merged


def _enrich_qb_size(candidates, qb_summary):
    if not isinstance(qb_summary, dict) or qb_summary.get("connected") is not True:
        return
    for task in qb_summary.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        identity = _artifact_identity(task.get("name"))
        if identity not in candidates:
            continue
        size = _positive_size(task)
        if size:
            candidates[identity]["sizes"].add(size)
            candidates[identity]["sources"].add("qb")


def resolve_baseline_artifact(
    subscription,
    torra_row,
    unit,
    *,
    qb_summary=None,
    symedia_rows=None,
):
    subscription = subscription if isinstance(subscription, dict) else {}
    torra_row = torra_row if isinstance(torra_row, dict) else {}
    unit = unit if isinstance(unit, dict) else {}
    media_type = _media_type(subscription.get("media_type") or subscription.get("mediaType"))
    if media_type not in {"movie", "tv"}:
        return {"status": "unconfirmed", "reason": "baseline_identity_unconfirmed"}
    if not _tmdb_id(subscription) or _tmdb_id(torra_row) != _tmdb_id(subscription):
        return {"status": "unconfirmed", "reason": "baseline_identity_unconfirmed"}

    candidates = _merge_candidates(
        _torra_candidates(torra_row, unit, media_type),
        _symedia_candidates(symedia_rows, subscription, unit, media_type),
    )
    _enrich_qb_size(candidates, qb_summary)
    if not candidates:
        return {"status": "unconfirmed", "reason": "baseline_version_unconfirmed"}
    if len(candidates) != 1:
        return {"status": "blocked", "reason": "baseline_artifact_conflict"}

    identity, candidate = next(iter(candidates.items()))
    if len(candidate["sizes"]) > 1:
        return {"status": "blocked", "reason": "baseline_size_conflict"}
    size = next(iter(candidate["sizes"]), None)
    return {
        "status": "ready",
        "artifactKey": f"baseline:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
        "versionSummary": candidate["versionSummary"],
        "sizeBytes": size,
        "sources": sorted(candidate["sources"]),
    }
