from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlsplit, urlunsplit


def _text(value, limit=240):
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:limit]


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json(value):
    try:
        result = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _value(row, key, default=""):
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return default


def _media_type(value):
    normalized = _text(value, 30).lower()
    if normalized in {"tv", "series", "episode", "电视剧", "剧集"}:
        return "tv"
    if normalized in {"movie", "film", "电影"}:
        return "movie"
    return ""


def media_identity(row, *, require_identified=False):
    if require_identified and _text(_value(row, "identity_status"), 30) != "identified":
        return None
    media_type = _media_type(_value(row, "media_type"))
    tmdb_id = _text(_value(row, "tmdb_id"), 24)
    if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
        return None
    season = _integer(_value(row, "season_number")) if media_type == "tv" else 0
    if media_type == "tv" and season <= 0:
        return None
    return media_type, tmdb_id, season


def _safe_poster_url(value):
    try:
        parsed = urlsplit(_text(value, 2000))
        host = str(parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if host == "localhost" or host.endswith((".local", ".localhost")):
            return ""
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            address = None
        if address and not address.is_global:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _year(value):
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", _text(value, 80))
    return match.group(1) if match else ""


def _metadata(row):
    payload = _json(_value(row, "payload_json"))
    title = _text(
        payload.get("title")
        or payload.get("name")
        or payload.get("local_title")
        or _value(row, "title")
    )
    if not title:
        return {}
    result = {"mediaTitle": title}
    year = _year(
        payload.get("year")
        or payload.get("release_date")
        or payload.get("first_air_date")
        or _value(row, "year")
    )
    poster = _safe_poster_url(
        payload.get("poster_url")
        or payload.get("posterUrl")
        or payload.get("poster")
        or _value(row, "poster_url")
    )
    if year:
        result["mediaYear"] = year
    if poster:
        result["posterUrl"] = poster
    return result


def _public_metadata(value):
    source = value if isinstance(value, dict) else {}
    title = _text(source.get("mediaTitle") or source.get("title") or source.get("name"))
    if not title:
        return {}
    result = {"mediaTitle": title}
    year = _year(source.get("mediaYear") or source.get("year"))
    poster = _safe_poster_url(source.get("posterUrl") or source.get("poster_url") or source.get("poster"))
    if year:
        result["mediaYear"] = year
    if poster:
        result["posterUrl"] = poster
    return result


def _table_exists(connection, name):
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone())


def _metadata_values(rows, identity):
    values = []
    for row in rows:
        if media_identity(row) != identity:
            continue
        value = _metadata(row)
        if value:
            values.append(value)
    return values


def _unique_metadata(rows, identity):
    values = _metadata_values(rows, identity)
    if not values:
        return "absent", {}
    titles = {unicodedata.normalize("NFKC", row["mediaTitle"]).casefold() for row in values}
    if len(titles) != 1:
        return "conflict", {}
    result = {"mediaTitle": values[0]["mediaTitle"]}
    years = {row["mediaYear"] for row in values if row.get("mediaYear")}
    posters = {row["posterUrl"] for row in values if row.get("posterUrl")}
    if len(years) == 1:
        result["mediaYear"] = next(iter(years))
    if len(posters) == 1:
        result["posterUrl"] = next(iter(posters))
    return "resolved", result


def _query_chunks(values, size=400):
    items = list(values)
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _subscription_sources(connection):
    if not _table_exists(connection, "subscriptions"):
        return {}, {}
    subscriptions = connection.execute(
        "SELECT subscription_key, media_type, tmdb_id, season_number, title, payload_json "
        "FROM subscriptions WHERE tmdb_id<>'' ORDER BY sort_order, subscription_key"
    ).fetchall()
    subscriptions_by_key = {str(_value(row, "subscription_key")): row for row in subscriptions}
    subscriptions_by_identity = defaultdict(list)
    for row in subscriptions:
        if identity := media_identity(row):
            subscriptions_by_identity[identity].append(row)
    return subscriptions_by_key, subscriptions_by_identity


def _matched_subscription_keys(connection, item_ids):
    matched_keys = defaultdict(list)
    if not item_ids or not _table_exists(connection, "rss_subscription_matches"):
        return matched_keys
    for chunk in _query_chunks(item_ids):
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            "SELECT item_id, subscription_key FROM rss_subscription_matches "
            f"WHERE item_id IN ({placeholders}) AND archive_state='active' "
            "AND match_status IN ('candidate','triggered','confirmed') ORDER BY created_at, id",
            chunk,
        ).fetchall()
        for row in rows:
            item_id = str(_value(row, "item_id"))
            key = str(_value(row, "subscription_key"))
            if key and key not in matched_keys[item_id]:
                matched_keys[item_id].append(key)
    return matched_keys


def _discover_sources(connection):
    discover_by_identity = defaultdict(list)
    if not _table_exists(connection, "discover_candidates"):
        return discover_by_identity
    rows = connection.execute(
        "SELECT media_type, tmdb_id, season_number, title, year, payload_json "
        "FROM discover_candidates WHERE state IN ('active','followed') ORDER BY last_seen_at DESC, candidate_id"
    ).fetchall()
    for row in rows:
        if identity := media_identity(row):
            discover_by_identity[identity].append(row)
    return discover_by_identity


def _cache_sources(cache_loader, identities):
    if not cache_loader:
        return {}
    try:
        loaded = cache_loader(set(identities))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _resolve_identity(identity, matched_rows, subscription_rows, discover_rows, cached):
    for source_rows in (matched_rows, subscription_rows, discover_rows):
        status, value = _unique_metadata(source_rows, identity)
        if status == "resolved":
            return value
        if status == "conflict":
            return {}
    return _public_metadata(cached)


def resolve_rss_media_metadata(connection, rows, cache_loader=None):
    item_identities = {
        str(_value(row, "id")): identity
        for row in rows
        if row is not None and (identity := media_identity(row, require_identified=True))
    }
    if not item_identities:
        return {}

    subscriptions_by_key, subscriptions_by_identity = _subscription_sources(connection)
    matched_keys = _matched_subscription_keys(connection, item_identities)
    discover_by_identity = _discover_sources(connection)
    cache_values = _cache_sources(cache_loader, item_identities.values())

    resolved = {}
    for item_id, identity in item_identities.items():
        matched_rows = [
            subscriptions_by_key[key]
            for key in matched_keys.get(item_id, [])
            if key in subscriptions_by_key
        ]
        value = _resolve_identity(
            identity,
            matched_rows,
            subscriptions_by_identity.get(identity, []),
            discover_by_identity.get(identity, []),
            cache_values.get(identity),
        )
        if value:
            resolved[item_id] = value
    return resolved


def media_title_matches(value, query_tokens):
    title = _text(value)
    if not title or not query_tokens:
        return False
    normalized = unicodedata.normalize("NFKC", title).casefold()
    tokens = re.findall(r"[a-z0-9][a-z0-9._+-]*", normalized)
    for block in re.findall(r"[\u3040-\u30ff\u3400-\u9fff]+", normalized):
        tokens.extend(block)
        tokens.extend(block[index:index + 2] for index in range(max(0, len(block) - 1)))
        tokens.extend(block[index:index + 3] for index in range(max(0, len(block) - 2)))
    return set(query_tokens) <= set(tokens)
