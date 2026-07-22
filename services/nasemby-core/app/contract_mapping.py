from __future__ import annotations

import re


def record(value):
    return value if isinstance(value, dict) else {}


def text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def first_text(row, *keys):
    for key in keys:
        value = text(record(row).get(key))
        if value:
            return value
    return ""


def number(value, fallback=0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def integer(value, fallback=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def source_boolean(value):
    if value in (True, 1):
        return True
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def string_array(value, limit=100):
    if not isinstance(value, list):
        return []
    return [text(item) for item in value[:limit] if text(item)]


def number_array(value, limit=300):
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return [int(item) if item.is_integer() else item for item in result]


def media_type(row):
    value = first_text(row, "media_type", "type").lower()
    if value in {"tv", "series"} or "剧" in value:
        return "tv"
    if value in {"movie", "film"} or "电影" in value:
        return "movie"
    return "unknown"


def season_number(row):
    for key in ("target_season", "current_season", "latest_season", "season_number", "season"):
        value = record(row).get(key)
        if value in (None, ""):
            continue
        parsed = integer(value, -1)
        if parsed >= 0:
            return parsed
    return None


def _progress(row, kind, in_library):
    supplied = first_text(row, "progress_text")
    if supplied:
        return supplied
    if kind == "movie":
        return "1/1" if in_library else "0/1"
    total = next((number(row.get(key)) for key in (
        "episode_total", "total_episodes", "episodes_total", "episode_count"
    ) if number(row.get(key))), 0)
    current = next((number(row.get(key)) for key in (
        "current_episode_count", "aired_episode_count", "latest_episode",
        "progress_episode_count", "library_episode_count"
    ) if number(row.get(key))), 0)
    if total:
        return f"{int(current)}/{int(total)}"
    return str(int(current)) if current else "0/?"


def map_subscription_item(value):
    row = record(value)
    if not row:
        return None
    kind = media_type(row)
    library_count = number(row.get("library_episode_count"))
    in_library = source_boolean(row.get("in_library")) or library_count > 0
    progress = _progress(row, kind, in_library)
    match = re.match(r"^(\d+)/(\d+)$", progress)
    done = bool(match and int(match.group(2)) > 0 and int(match.group(1)) >= int(match.group(2)))
    if kind == "movie" and in_library:
        done = True
    year = first_text(row, "year") or first_text(row, "release_date", "air_date")[:4]
    origin = first_text(row, "origin", "subscription_origin").lower()
    if origin not in {"manual", "auto", "torra"}:
        origin = "unknown"
    result = {
        "id": first_text(row, "key", "subscription_key", "dedupe_key", "id"),
        "title": first_text(row, "title", "name"),
        "seasonName": first_text(row, "season_name"),
        "mediaType": kind,
        "tmdbId": first_text(row, "tmdb_id"),
        "mediaCategory": first_text(row, "media_category") or None,
        "allowCloudFallback": source_boolean(row.get("allow_cloud_fallback")),
        "posterUrl": first_text(row, "poster_url", "poster"),
        "backdropUrl": first_text(row, "backdrop_url"),
        "progressText": progress,
        "inLibrary": in_library,
        "updatedAt": first_text(row, "updated_at"),
        "createdAt": first_text(row, "created_at"),
        "year": year,
        "sourceLabel": first_text(row, "source_label", "source", "source_key"),
        "status": "done" if done else "pending",
        "metadataPending": source_boolean(row.get("metadata_pending")),
        "origin": origin,
        "readOnly": source_boolean(row.get("read_only")),
        "torraSyncState": first_text(row, "torra_sync_state") or None,
        "torraMappingStatus": first_text(row, "torra_mapping_status") or None,
    }
    season = season_number(row)
    if season is not None:
        result["seasonNumber"] = season
    if not result["mediaCategory"]:
        result.pop("mediaCategory")
    for optional_key in ("torraSyncState", "torraMappingStatus"):
        if result[optional_key] is None:
            result.pop(optional_key)
    return result


def map_subscription_payload(payload):
    root = record(payload)
    if not isinstance(root.get("items"), list):
        raise ValueError("NasEmby Core 返回了无效订阅数据")
    items = [mapped for mapped in (map_subscription_item(item) for item in root["items"]) if mapped]
    error_count = len(root.get("errors") or []) if isinstance(root.get("errors"), list) else 0
    return {
        "configured": True,
        "blockedTitles": string_array(root.get("blocked_titles")),
        "errors": ["部分订阅进度暂不可用"] if error_count else [],
        "errorCount": error_count,
        "subscriptions": {
            "lastRunAt": first_text(root, "last_run_at"),
            "items": items,
            "stats": {
                "total": len(items),
                "movie": sum(item["mediaType"] == "movie" for item in items),
                "tv": sum(item["mediaType"] == "tv" for item in items),
            },
        },
    }


def _map_cast(value):
    if not isinstance(value, list):
        return []
    result = []
    for raw in value[:12]:
        person = record(raw)
        name = first_text(person, "name")
        if name:
            result.append({
                "name": name,
                "character": first_text(person, "character"),
                "profileUrl": first_text(person, "profile_url"),
            })
    return result


def _map_episodes(value):
    if not isinstance(value, list):
        return []
    return [{
        "episodeNumber": integer(episode.get("episode_number")),
        "title": first_text(episode, "title", "name"),
        "overview": first_text(episode, "overview"),
        "airDate": first_text(episode, "air_date"),
        "runtime": first_text(episode, "runtime"),
        "inLibrary": source_boolean(episode.get("in_library")),
        "libraryPaths": string_array(episode.get("library_paths")),
    } for episode in map(record, value)]


def _map_seasons(value):
    if not isinstance(value, list):
        return []
    return [{
        "seasonNumber": integer(season.get("season_number")),
        "name": first_text(season, "name"),
        "overview": first_text(season, "overview"),
        "posterUrl": first_text(season, "poster_url"),
        "airDate": first_text(season, "air_date"),
        "episodeCount": integer(season.get("episode_count")),
        "libraryCount": integer(season.get("library_count")),
        "episodes": _map_episodes(season.get("episodes")),
    } for season in map(record, value)]


def map_subscription_detail(payload):
    root = record(payload)
    if root.get("success") is not True:
        raise ValueError("NasEmby Core 返回了无效订阅详情")
    detail = record(root.get("detail"))
    mapped_detail = None
    if detail:
        mapped_detail = {
            "tmdbId": first_text(detail, "tmdb_id"),
            "imdbId": first_text(detail, "imdb_id"),
            "title": first_text(detail, "title", "name"),
            "originalTitle": first_text(detail, "original_title", "original_name"),
            "englishTitle": first_text(detail, "english_title"),
            "year": first_text(detail, "year"),
            "rating": first_text(detail, "rating"),
            "overview": first_text(detail, "overview"),
            "posterUrl": first_text(detail, "poster_url"),
            "backdropUrl": first_text(detail, "backdrop_url"),
            "genres": string_array(detail.get("genres")),
            "runtime": first_text(detail, "runtime"),
            "status": first_text(detail, "status"),
            "date": first_text(detail, "date"),
            "country": first_text(detail, "country"),
            "language": first_text(detail, "language"),
            "seasonCount": integer(detail.get("season_count")),
            "episodeCount": integer(detail.get("episode_count")),
            "mediaType": media_type(detail),
            "cast": _map_cast(detail.get("cast")),
            "inLibrary": source_boolean(detail.get("in_library")),
            "libraryEpisodeCount": integer(detail.get("library_episode_count")),
            "libraryPaths": string_array(detail.get("library_paths")),
        }
    result = {
        "success": True,
        "detail": mapped_detail,
        "seasons": _map_seasons(root.get("seasons")),
        "cacheHit": source_boolean(root.get("cache_hit")),
    }
    item = map_subscription_item(root.get("item"))
    if item:
        result["item"] = item
    return result


def map_calendar_payload(payload):
    root = record(payload)
    if root.get("success") is not True or not isinstance(root.get("entries"), list):
        raise ValueError("NasEmby Core 返回了无效订阅日历")
    entries = []
    for entry in map(record, root["entries"]):
        entries.append({
            "date": first_text(entry, "date"),
            "key": first_text(entry, "key"),
            "title": first_text(entry, "title"),
            "mediaType": media_type(entry),
            "posterUrl": first_text(entry, "poster_url"),
            "tmdbId": first_text(entry, "tmdb_id"),
            "sourceLabel": first_text(entry, "source_label"),
            "seasonNumber": integer(entry.get("season_number")),
            "seasonName": first_text(entry, "season_name"),
            "episodeNumber": integer(entry.get("episode_number")),
            "episodeTitle": first_text(entry, "episode_title"),
            "episodeLabel": first_text(entry, "episode_label"),
            "progressText": first_text(entry, "progress_text"),
            "inLibrary": source_boolean(entry.get("in_library")),
            "libraryPaths": string_array(entry.get("library_paths")),
            "subscriptionCreatedAt": first_text(entry, "subscription_created_at"),
            "followScopeExplicit": source_boolean(entry.get("follow_scope_explicit")),
            "includePastEpisodes": source_boolean(entry.get("include_past_episodes")),
            "allowedDelayHours": integer(entry.get("allowed_delay_hours"), 24),
        })
    stats = record(root.get("stats"))
    error_count = len(root.get("errors") or []) if isinstance(root.get("errors"), list) else 0
    return {
        "configured": True,
        "calendar": {
            "year": integer(root.get("year")),
            "month": integer(root.get("month")),
            "mediaType": first_text(root, "type") or "all",
            "entries": entries,
            "stats": {
                "entries": integer(stats.get("entries"), len(entries)),
                "titles": integer(stats.get("titles")),
                "inLibrary": integer(stats.get("in_library")),
                "pending": integer(stats.get("pending")),
            },
            "errors": ["部分订阅未能生成播出日历"] if error_count else [],
            "errorCount": error_count,
        },
    }


def map_discover_payload(payload, source):
    root = record(payload)
    rows = root.get("items") if isinstance(root.get("items"), list) else []
    tmdb_native = source in {"tmdb", "streaming", "daily"}
    results = []
    for index, item in enumerate(map(record, rows), start=1):
        title = first_text(item, "title", "name")
        if not title:
            continue
        raw_id = first_text(item, "id")
        direct_tmdb_id = first_text(item, "tmdb_id", "tmdbId")
        tmdb_id = direct_tmdb_id if direct_tmdb_id.isdigit() else (raw_id if tmdb_native and raw_id.isdigit() else "")
        result = {
            "id": int(tmdb_id or raw_id) if (tmdb_id or raw_id).isdigit() else index,
            "mediaType": "tv" if media_type(item) == "tv" else "movie",
            "title": title,
            "year": first_text(item, "year"),
            "posterUrl": first_text(item, "poster_url", "poster"),
            "overview": first_text(item, "overview", "description", "desc"),
            "rating": number(item.get("rating")),
            "genreIds": number_array(item.get("genre_ids") or item.get("genreIds")),
            "originCountry": string_array(item.get("origin_country") or item.get("originCountry")),
            "source": source,
            "sourceLabel": first_text(item, "source_label", "source") or first_text(root, "source") or source,
            "sourceId": first_text(item, "source_id", "sourceId") or raw_id or tmdb_id or str(index),
        }
        language = first_text(item, "original_language", "originalLanguage")
        if language:
            result["originalLanguage"] = language
        if tmdb_id:
            result["tmdbId"] = tmdb_id
        results.append(result)
    page = integer(root.get("page"), 1)
    total_pages = integer(root.get("total_pages") or root.get("totalPages"), 1)
    total_results = integer(root.get("total_results") or root.get("totalResults"), len(results))
    return {
        "configured": True,
        "results": results,
        "page": page,
        "totalPages": total_pages,
        "totalResults": total_results,
        "hasNext": root.get("has_next") if isinstance(root.get("has_next"), bool) else page < total_pages,
        "hasPrev": root.get("has_prev") if isinstance(root.get("has_prev"), bool) else page > 1,
        "sourceLabel": first_text(root, "source_label", "source") or source,
    }


def sanitize_resource_payload(payload):
    root = record(payload)
    items = []
    for item in map(record, (root.get("items") or [])[:120] if isinstance(root.get("items"), list) else []):
        items.append({
            "source": first_text(item, "source"),
            "source_key": first_text(item, "source_key"),
            "source_label": first_text(item, "source_label"),
            "drive": first_text(item, "drive"),
            "title": first_text(item, "title", "name"),
            "subtitle": first_text(item, "subtitle"),
            "quality": first_text(item, "quality"),
            "size": first_text(item, "size"),
            "date": first_text(item, "date"),
            "url": first_text(item, "url"),
            "preview_url": first_text(item, "preview_url"),
            "share_url": first_text(item, "share_url"),
            "full_text": first_text(item, "full_text"),
            "password": first_text(item, "password"),
            "season": first_text(item, "season"),
            "episodes": number_array(item.get("episodes")),
            "links": string_array(item.get("links"), 20),
        })
    sources = [{
        "key": first_text(source, "key"),
        "label": first_text(source, "label"),
        "count": integer(source.get("count")),
    } for source in map(record, (root.get("sources") or [])[:20] if isinstance(root.get("sources"), list) else [])]
    seasons = [{
        "season": first_text(season, "season"),
        "episodes": number_array(season.get("episodes")),
        "resource_episodes": number_array(season.get("resource_episodes")),
        "library_episodes": number_array(season.get("library_episodes")),
        "missing_episodes": number_array(season.get("missing_episodes")),
        "notice": first_text(season, "notice"),
    } for season in map(record, (root.get("seasons") or [])[:100] if isinstance(root.get("seasons"), list) else [])]
    return {
        "success": root.get("success") is not False,
        "title": first_text(root, "title"),
        "media_type": "tv" if root.get("media_type") == "tv" else "movie",
        "items": items,
        "sources": sources,
        "seasons": seasons,
        "errors": ["部分资源来源当前不可用"] if root.get("errors") else [],
        "cache_hits": string_array(root.get("cache_hits"), 20),
    }
