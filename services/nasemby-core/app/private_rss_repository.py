from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from app.sqlite_runtime import SQLiteRuntime


MATCH_STATUSES = {"candidate", "ignored", "triggered", "confirmed", "expired"}
IDENTITY_STATUSES = {"identified", "conflict", "unidentified"}


def _now():
    return datetime.now(timezone.utc)


def _iso(value=None):
    return (value or _now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _domain(url):
    try:
        return str(urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def _source_fingerprint(url):
    return hashlib.sha256(str(url or "").strip().encode("utf-8")).hexdigest()


def _search_text(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    tokens = re.findall(r"[a-z0-9][a-z0-9._+-]*", text)
    for block in re.findall(r"[\u3040-\u30ff\u3400-\u9fff]+", text):
        tokens.extend(block)
        tokens.extend(block[index:index + 2] for index in range(max(0, len(block) - 1)))
        tokens.extend(block[index:index + 3] for index in range(max(0, len(block) - 2)))
    return " ".join(dict.fromkeys(token for token in tokens if token))


def _match_query(value):
    tokens = _search_text(value).split()
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:12])


def _title_seasons(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    seasons = {
        int(match)
        for pattern in (
            r"(?i)\bS(?:eason)?[ ._-]?(\d{1,3})\b",
            r"(?i)\bSeason[ ._-]?(\d{1,3})\b",
            r"第\s*(\d{1,3})\s*季",
        )
        for match in re.findall(pattern, text)
    }
    return seasons


def _tv_target_match(item, season_number):
    item_season = item.get("seasonNumber")
    if item_season is not None:
        if season_number is not None and int(item_season) != season_number:
            return None
        return {
            "matchMethod": "title_media_season",
            "matchConfidence": "fallback",
            "seasonScopeState": "confirmed",
        }
    title_seasons = _title_seasons(item.get("title"))
    if season_number is not None and title_seasons and season_number not in title_seasons:
        return None
    return {
        "matchMethod": "title_media_scope",
        "matchConfidence": "fallback",
        "seasonScopeState": "unknown",
    }


def _movie_target_match(item, year):
    if not year or year not in str(item.get("title") or ""):
        return None
    return {
        "matchMethod": "title_media_year",
        "matchConfidence": "fallback",
        "seasonScopeState": "not_applicable",
    }


def _target_match(item, *, tmdb_id="", media_type="", season_number=None, year=""):
    item_tmdb = str(item.get("tmdbId") or "")
    item_type = str(item.get("mediaType") or "")
    item_season = item.get("seasonNumber")

    if item_tmdb:
        if not tmdb_id or item_tmdb != tmdb_id:
            return None
        if media_type and item_type and item_type != media_type:
            return None
        if media_type == "tv" and season_number is not None and item_season is not None:
            if int(item_season) != season_number:
                return None
        return {
            "matchMethod": "tmdb_exact",
            "matchConfidence": "strong",
            "seasonScopeState": "confirmed" if item_season is not None else "unknown",
        }

    if item.get("imdbId") or str(item.get("identityStatus") or "unidentified") != "unidentified":
        return None
    if media_type and item_type and item_type != media_type:
        return None
    if media_type == "tv":
        return _tv_target_match(item, season_number)
    if media_type == "movie":
        return _movie_target_match(item, year)
    return {
        "matchMethod": "title_scoped",
        "matchConfidence": "fallback",
        "seasonScopeState": "unknown",
    }


def _json_dump(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value):
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _create_match_table(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rss_subscription_matches ("
        "id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES rss_items(id) ON DELETE CASCADE, "
        "subscription_key TEXT NOT NULL, unit_key TEXT NOT NULL, match_status TEXT NOT NULL DEFAULT 'candidate', "
        "match_reason_json TEXT NOT NULL DEFAULT '{}', trigger_action_id TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(item_id, unit_key))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rss_matches_status "
        "ON rss_subscription_matches(match_status, created_at DESC)"
    )


def _legacy_match_values(row):
    source = dict(row)
    created_at = str(source.get("created_at") or _iso())
    reason = str(source.get("reason") or "")[:240]
    return (
        str(source.get("id") or uuid.uuid4().hex),
        str(source.get("item_id") or ""),
        str(source.get("subscription_key") or ""),
        f"{source.get('subscription_key') or 'unknown'}:legacy",
        _json_dump({"legacyReason": reason, "migration": "pre-unit-key"}),
        created_at,
        created_at,
    )


def _migrate_legacy_match_table(connection):
    legacy_rows = connection.execute("SELECT * FROM rss_subscription_matches").fetchall()
    connection.execute("ALTER TABLE rss_subscription_matches RENAME TO rss_subscription_matches_legacy")
    _create_match_table(connection)
    connection.executemany(
        "INSERT INTO rss_subscription_matches ("
        "id, item_id, subscription_key, unit_key, match_status, match_reason_json, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, 'ignored', ?, ?, ?)",
        (_legacy_match_values(row) for row in legacy_rows),
    )
    connection.execute("DROP TABLE rss_subscription_matches_legacy")


def _initialize_match_table(connection):
    existing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rss_subscription_matches'"
    ).fetchone()
    if not existing:
        _create_match_table(connection)
        return
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(rss_subscription_matches)").fetchall()}
    if {"unit_key", "match_status", "match_reason_json", "trigger_action_id", "updated_at"} <= columns:
        _create_match_table(connection)
        return
    _migrate_legacy_match_table(connection)


@dataclass(frozen=True)
class FetchRunRecord:
    item_count: int = 0
    message: str = ""
    etag: str = ""
    last_modified: str = ""
    http_status: int = 0
    retry_after_seconds: int | None = None
    now: datetime | None = None


class PrivateRssRepository:
    def __init__(self, database_path):
        self.runtime = SQLiteRuntime(database_path)
        self.runtime.initialize()
        self.initialize()

    def initialize(self):
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_sources ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, feed_url TEXT NOT NULL, source_fingerprint TEXT NOT NULL UNIQUE, "
                "domain TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, interval_minutes INTEGER NOT NULL DEFAULT 5, "
                "retention_days INTEGER NOT NULL DEFAULT 7, allow_http INTEGER NOT NULL DEFAULT 0, etag TEXT NOT NULL DEFAULT '', "
                "last_modified TEXT NOT NULL DEFAULT '', last_success_at TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', "
                "failure_count INTEGER NOT NULL DEFAULT 0, backoff_until TEXT NOT NULL DEFAULT '', "
                "next_poll_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_items ("
                "id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES rss_sources(id) ON DELETE CASCADE, "
                "fingerprint TEXT NOT NULL, guid TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
                "published_at TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', size_bytes INTEGER NOT NULL DEFAULT 0, "
                "detail_url TEXT NOT NULL DEFAULT '', download_url TEXT NOT NULL DEFAULT '', media_type TEXT NOT NULL DEFAULT '', "
                "season_number INTEGER, episode_start INTEGER, episode_end INTEGER, version_summary TEXT NOT NULL DEFAULT '', "
                "tmdb_id TEXT NOT NULL DEFAULT '', imdb_id TEXT NOT NULL DEFAULT '', "
                "identity_status TEXT NOT NULL DEFAULT 'unidentified', identity_source TEXT NOT NULL DEFAULT '', "
                "identity_confidence TEXT NOT NULL DEFAULT '', identity_updated_at TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, UNIQUE(source_id, fingerprint))"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_rss_items_time ON rss_items(published_at DESC, created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_rss_items_source ON rss_items(source_id, published_at DESC)")
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rss_item_search USING fts5(item_id UNINDEXED, title, search_text, tokenize='unicode61')"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_fetch_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL, status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0, "
                "http_status INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_match_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, scanned_count INTEGER NOT NULL DEFAULT 0, "
                "match_count INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_identity_backfill_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT NOT NULL, scanned_count INTEGER NOT NULL DEFAULT 0, "
                "identified_count INTEGER NOT NULL DEFAULT 0, conflict_count INTEGER NOT NULL DEFAULT 0, "
                "unchanged_count INTEGER NOT NULL DEFAULT 0, remaining_count INTEGER NOT NULL DEFAULT 0, "
                "batch_limit INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
            )
            source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(rss_sources)").fetchall()}
            if "failure_count" not in source_columns:
                connection.execute("ALTER TABLE rss_sources ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
            if "backoff_until" not in source_columns:
                connection.execute("ALTER TABLE rss_sources ADD COLUMN backoff_until TEXT NOT NULL DEFAULT ''")
            run_columns = {row["name"] for row in connection.execute("PRAGMA table_info(rss_fetch_runs)").fetchall()}
            if "http_status" not in run_columns:
                connection.execute("ALTER TABLE rss_fetch_runs ADD COLUMN http_status INTEGER NOT NULL DEFAULT 0")
            item_columns = {row["name"] for row in connection.execute("PRAGMA table_info(rss_items)").fetchall()}
            if "tmdb_id" not in item_columns:
                connection.execute("ALTER TABLE rss_items ADD COLUMN tmdb_id TEXT NOT NULL DEFAULT ''")
            if "imdb_id" not in item_columns:
                connection.execute("ALTER TABLE rss_items ADD COLUMN imdb_id TEXT NOT NULL DEFAULT ''")
            if "identity_status" not in item_columns:
                connection.execute(
                    "ALTER TABLE rss_items ADD COLUMN identity_status TEXT NOT NULL DEFAULT 'unidentified'"
                )
            if "identity_source" not in item_columns:
                connection.execute("ALTER TABLE rss_items ADD COLUMN identity_source TEXT NOT NULL DEFAULT ''")
            if "identity_confidence" not in item_columns:
                connection.execute("ALTER TABLE rss_items ADD COLUMN identity_confidence TEXT NOT NULL DEFAULT ''")
            if "identity_updated_at" not in item_columns:
                connection.execute("ALTER TABLE rss_items ADD COLUMN identity_updated_at TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rss_items_identity "
                "ON rss_items(identity_status, published_at DESC, created_at DESC)"
            )
            _initialize_match_table(connection)

    @staticmethod
    def _public_source(row):
        return {
            "id": row["id"],
            "name": row["name"],
            "domain": row["domain"],
            "feedConfigured": True,
            "enabled": bool(row["enabled"]),
            "intervalMinutes": int(row["interval_minutes"]),
            "retentionDays": int(row["retention_days"]),
            "allowHttp": bool(row["allow_http"]),
            "lastSuccessAt": row["last_success_at"],
            "lastError": row["last_error"],
            "failureCount": int(row["failure_count"] or 0),
            "backoffUntil": row["backoff_until"],
            "nextPollAt": row["next_poll_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _public_item(row):
        return {
            "id": row["id"],
            "sourceId": row["source_id"],
            "sourceName": row["source_name"],
            "sourceDomain": row["source_domain"],
            "title": row["title"],
            "description": row["description"],
            "publishedAt": row["published_at"],
            "category": row["category"],
            "sizeBytes": int(row["size_bytes"] or 0),
            "mediaType": row["media_type"],
            "seasonNumber": row["season_number"],
            "episodeStart": row["episode_start"],
            "episodeEnd": row["episode_end"],
            "versionSummary": row["version_summary"],
            "tmdbId": row["tmdb_id"],
            "imdbId": row["imdb_id"],
            "identityStatus": row["identity_status"],
            "identitySource": row["identity_source"],
            "identityConfidence": row["identity_confidence"],
            "identityUpdatedAt": row["identity_updated_at"],
            "hasDownload": bool(row["download_url"]),
            "lastSeenAt": row["last_seen_at"],
        }

    def list_sources(self):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute("SELECT * FROM rss_sources ORDER BY name COLLATE NOCASE, created_at").fetchall()
        return [self._public_source(row) for row in rows]

    def get_source(self, source_id, public=True):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM rss_sources WHERE id=?", (str(source_id),)).fetchone()
        if not row:
            return None
        return self._public_source(row) if public else dict(row)

    def save_source(self, payload, source_id=None):
        source_id = str(source_id or uuid.uuid4().hex)
        existing = self.get_source(source_id, public=False)
        feed_url = str(payload.get("feedUrl") if "feedUrl" in payload else (existing or {}).get("feed_url") or "").strip()
        if not feed_url or len(feed_url) > 4096:
            raise ValueError("RSS 地址不能为空")
        parsed = urlsplit(feed_url)
        allow_http = bool(payload.get("allowHttp", (existing or {}).get("allow_http", False)))
        if parsed.scheme not in ({"https", "http"} if allow_http else {"https"}) or not parsed.hostname:
            raise ValueError("RSS 地址必须使用 HTTPS；HTTP 需要明确允许")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise ValueError("RSS 端口无效") from exc
        if port not in {80, 443} and not allow_http:
            raise ValueError("非标准端口需要明确允许")
        if not existing and len(self.list_sources()) >= 10:
            raise ValueError("第一版最多配置 10 个 RSS 来源")
        interval_value = payload.get("intervalMinutes", (existing or {}).get("interval_minutes", 5))
        if isinstance(interval_value, bool) or (isinstance(interval_value, float) and not interval_value.is_integer()):
            raise ValueError("轮询周期必须是整数分钟")
        try:
            interval = int(interval_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("轮询周期必须是整数分钟") from exc
        retention = int(payload.get("retentionDays", (existing or {}).get("retention_days", 7)))
        if interval < 1 or interval > 1440:
            raise ValueError("轮询周期必须在 1 到 1440 分钟之间")
        if retention not in {3, 7, 14}:
            raise ValueError("保留期只允许 3、7、14 天")
        name = str(payload.get("name", (existing or {}).get("name") or _domain(feed_url))).strip()[:80]
        if not name:
            raise ValueError("来源名称不能为空")
        now = _iso()
        feed_changed = bool(existing and feed_url != existing["feed_url"])
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO rss_sources (id, name, feed_url, source_fingerprint, domain, enabled, interval_minutes, retention_days, allow_http, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, feed_url=excluded.feed_url, source_fingerprint=excluded.source_fingerprint, "
                "domain=excluded.domain, enabled=excluded.enabled, interval_minutes=excluded.interval_minutes, retention_days=excluded.retention_days, "
                "allow_http=excluded.allow_http, updated_at=excluded.updated_at",
                (source_id, name, feed_url, _source_fingerprint(feed_url), _domain(feed_url), int(bool(payload.get("enabled", (existing or {}).get("enabled", True)))), interval, retention, int(allow_http), now, now),
            )
            if feed_changed:
                connection.execute(
                    "UPDATE rss_sources SET etag='', last_modified='', last_success_at='', last_error='', "
                    "failure_count=0, backoff_until='', next_poll_at='' WHERE id=?",
                    (source_id,),
                )
        return self.get_source(source_id)

    def delete_source(self, source_id):
        with self.runtime.transaction(immediate=True) as connection:
            item_ids = [row["id"] for row in connection.execute("SELECT id FROM rss_items WHERE source_id=?", (source_id,)).fetchall()]
            if item_ids:
                connection.executemany("DELETE FROM rss_item_search WHERE item_id=?", ((value,) for value in item_ids))
            cursor = connection.execute("DELETE FROM rss_sources WHERE id=?", (source_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _normalized_item(item):
        normalized = dict(item)
        normalized["fingerprint"] = str(item.get("fingerprint") or "").strip()
        normalized["title"] = str(item.get("title") or "").strip()
        return normalized if normalized["fingerprint"] and normalized["title"] else None

    @staticmethod
    def _write_item(connection, source_id, item, now, expires):
        fingerprint = item["fingerprint"]
        title = item["title"]
        existing = connection.execute(
            "SELECT * FROM rss_items WHERE source_id=? AND fingerprint=?", (source_id, fingerprint)
        ).fetchone()
        item_id = existing["id"] if existing else uuid.uuid4().hex
        identity_status = str(item.get("identity_status") or "unidentified").strip().lower()
        if identity_status not in IDENTITY_STATUSES:
            identity_status = "unidentified"
        preserve_identity = bool(
            existing
            and identity_status == "unidentified"
            and str(existing["identity_status"] or "unidentified") != "unidentified"
        )
        if preserve_identity:
            tmdb_id = str(existing["tmdb_id"] or "")
            imdb_id = str(existing["imdb_id"] or "")
            identity_status = str(existing["identity_status"] or "unidentified")
            identity_source = str(existing["identity_source"] or "")
            identity_confidence = str(existing["identity_confidence"] or "")
            identity_updated_at = str(existing["identity_updated_at"] or "")
        else:
            tmdb_id = str(item.get("tmdb_id") or "").strip()[:24]
            imdb_id = str(item.get("imdb_id") or "").strip().lower()[:24]
            identity_source = str(item.get("identity_source") or "").strip()[:160]
            identity_confidence = str(item.get("identity_confidence") or "").strip()[:40]
            identity_updated_at = _iso(now)
        values = (
            item_id, source_id, fingerprint, str(item.get("guid") or ""), title[:500],
            str(item.get("description") or "")[:2000], str(item.get("published_at") or ""),
            str(item.get("category") or "")[:300], int(item.get("size_bytes") or 0),
            str(item.get("detail_url") or "")[:4096], str(item.get("download_url") or "")[:4096],
            str(item.get("media_type") or ""), item.get("season_number"), item.get("episode_start"),
            item.get("episode_end"), str(item.get("version_summary") or "")[:300],
            tmdb_id, imdb_id, identity_status, identity_source, identity_confidence, identity_updated_at,
            _iso(now), _iso(now), expires,
        )
        connection.execute(
            "INSERT INTO rss_items (id, source_id, fingerprint, guid, title, description, published_at, category, "
            "size_bytes, detail_url, download_url, media_type, season_number, episode_start, episode_end, "
            "version_summary, tmdb_id, imdb_id, identity_status, identity_source, identity_confidence, "
            "identity_updated_at, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_id, fingerprint) DO UPDATE SET title=excluded.title, "
            "description=excluded.description, published_at=excluded.published_at, category=excluded.category, "
            "size_bytes=excluded.size_bytes, detail_url=excluded.detail_url, download_url=excluded.download_url, "
            "media_type=excluded.media_type, season_number=excluded.season_number, episode_start=excluded.episode_start, "
            "episode_end=excluded.episode_end, version_summary=excluded.version_summary, "
            "tmdb_id=excluded.tmdb_id, imdb_id=excluded.imdb_id, identity_status=excluded.identity_status, "
            "identity_source=excluded.identity_source, identity_confidence=excluded.identity_confidence, "
            "identity_updated_at=excluded.identity_updated_at, "
            "last_seen_at=excluded.last_seen_at, expires_at=excluded.expires_at",
            values,
        )
        connection.execute("DELETE FROM rss_item_search WHERE item_id=?", (item_id,))
        search = " ".join((title, str(item.get("category") or ""), str(item.get("version_summary") or "")))
        connection.execute(
            "INSERT INTO rss_item_search (item_id, title, search_text) VALUES (?, ?, ?)",
            (item_id, title, _search_text(search)),
        )
        return bool(existing), {
            **dict(item),
            "id": item_id,
            "source_id": source_id,
            "created_at": _iso(now),
            "expires_at": expires,
        }

    def upsert_items(self, source_id, items, on_insert=None):
        source = self.get_source(source_id, public=False)
        if not source:
            raise KeyError("RSS 来源不存在")
        now = _now()
        expires = _iso(now + timedelta(days=int(source["retention_days"])))
        inserted = updated = 0
        inserted_rows = []
        with self.runtime.transaction(immediate=True) as connection:
            for item in items:
                normalized = self._normalized_item(item)
                if not normalized:
                    continue
                stored = self._write_item(connection, source_id, normalized, now, expires)
                existing, inserted_row = stored
                if existing:
                    updated += 1
                else:
                    inserted += 1
                    inserted_rows.append(inserted_row)
            if inserted_rows and on_insert:
                matches = on_insert(connection, inserted_rows) or []
            else:
                matches = []
        return {
            "inserted": inserted,
            "updated": updated,
            "_match_ids": [str(match.get("id") or "") for match in matches if isinstance(match, dict)],
        }

    def search_items(
        self,
        query="",
        source_id="",
        window_hours=None,
        identity_status="",
        limit=50,
        offset=0,
        tmdb_id="",
        media_type="",
        season_number=None,
        year="",
    ):
        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        query_text = str(query or "").strip()
        match = _match_query(query_text)
        target_tmdb_id = str(tmdb_id or "").strip()
        if target_tmdb_id and (not target_tmdb_id.isdigit() or len(target_tmdb_id) > 24):
            raise ValueError("TMDB ID 无效")
        target_media_type = str(media_type or "").strip().lower()
        if target_media_type and target_media_type not in {"movie", "tv"}:
            raise ValueError("媒体类型无效")
        if season_number in (None, ""):
            target_season = None
        else:
            target_season = int(season_number)
            if target_season < 0 or target_season > 999:
                raise ValueError("季号无效")
        target_year = str(year or "").strip()
        if target_year and not re.fullmatch(r"(?:19|20)\d{2}", target_year):
            raise ValueError("年份无效")
        identity_status = str(identity_status or "").strip().lower()
        if identity_status and identity_status not in IDENTITY_STATUSES:
            raise ValueError("身份状态无效")

        if query_text and not match:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}

        base_where = []
        base_params = []
        if source_id:
            base_where.append("i.source_id=?")
            base_params.append(str(source_id))
        if window_hours:
            cutoff = _iso(_now() - timedelta(hours=int(window_hours)))
            base_where.append("COALESCE(NULLIF(i.published_at, ''), i.created_at) >= ?")
            base_params.append(cutoff)
        if identity_status:
            base_where.append("i.identity_status=?")
            base_params.append(identity_status)

        targeted = bool(target_tmdb_id or (query_text and (target_media_type or target_season is not None or target_year)))
        with closing(self.runtime.connect()) as connection:
            if targeted:
                rows_by_id = {}

                def add_candidates(extra_joins, extra_where, extra_params):
                    where = [*base_where, *extra_where]
                    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
                    rows = connection.execute(
                        "SELECT i.*, s.name AS source_name, s.domain AS source_domain FROM rss_items i "
                        f"JOIN rss_sources s ON s.id=i.source_id {' '.join(extra_joins)} {where_sql} "
                        "ORDER BY COALESCE(NULLIF(i.published_at, ''), i.created_at) DESC, i.id DESC",
                        (*base_params, *extra_params),
                    ).fetchall()
                    for row in rows:
                        rows_by_id[str(row["id"])] = row

                if target_tmdb_id:
                    add_candidates([], ["i.tmdb_id=?"], [target_tmdb_id])
                if match:
                    add_candidates(
                        ["JOIN rss_item_search f ON f.item_id=i.id"],
                        ["i.tmdb_id=''", "i.imdb_id=''", "i.identity_status='unidentified'", "f.search_text MATCH ?"],
                        [match],
                    )

                items = []
                for row in rows_by_id.values():
                    item = self._public_item(row)
                    matched = _target_match(
                        item,
                        tmdb_id=target_tmdb_id,
                        media_type=target_media_type,
                        season_number=target_season,
                        year=target_year,
                    )
                    if matched:
                        item.update(matched)
                        items.append(item)
                items.sort(
                    key=lambda item: str(item.get("publishedAt") or item.get("lastSeenAt") or ""),
                    reverse=True,
                )
                items.sort(key=lambda item: 0 if item.get("matchMethod") == "tmdb_exact" else 1)
                total = len(items)
                return {"items": items[offset:offset + limit], "total": total, "limit": limit, "offset": offset}

            joins = []
            where = list(base_where)
            params = list(base_params)
            if target_media_type:
                where.append("i.media_type=?")
                params.append(target_media_type)
            if target_season is not None:
                where.append("i.season_number=?")
                params.append(target_season)
            if match:
                joins.append("JOIN rss_item_search f ON f.item_id=i.id")
                where.append("f.search_text MATCH ?")
                params.append(match)
            if target_year:
                where.append("i.title LIKE ?")
                params.append(f"%{target_year}%")
            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            join_sql = " ".join(joins)
            total = int(connection.execute(
                f"SELECT COUNT(DISTINCT i.id) AS count FROM rss_items i {join_sql} {where_sql}", params
            ).fetchone()["count"])
            rows = connection.execute(
                f"SELECT i.*, s.name AS source_name, s.domain AS source_domain FROM rss_items i "
                f"JOIN rss_sources s ON s.id=i.source_id {join_sql} {where_sql} "
                "ORDER BY COALESCE(NULLIF(i.published_at, ''), i.created_at) DESC, i.id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        items = [self._public_item(row) for row in rows]
        if target_media_type or target_season is not None or target_year:
            for item in items:
                if target_media_type == "tv" and target_season is not None:
                    item["matchMethod"] = "title_media_season"
                    item["matchConfidence"] = "fallback"
                elif target_media_type == "movie" and target_year:
                    item["matchMethod"] = "title_media_year"
                    item["matchConfidence"] = "fallback"
                else:
                    item["matchMethod"] = "title_scoped"
                    item["matchConfidence"] = "fallback"
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_item(self, item_id, public=True):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT i.*, s.name AS source_name, s.domain AS source_domain FROM rss_items i "
                "JOIN rss_sources s ON s.id=i.source_id WHERE i.id=?", (item_id,)
            ).fetchone()
        if not row:
            return None
        return self._public_item(row) if public else dict(row)

    def list_unidentified_items(self, limit=50):
        limit = max(1, min(int(limit or 50), 200))
        with closing(self.runtime.connect()) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT i.*, s.name AS source_name, s.domain AS source_domain "
                "FROM rss_items i JOIN rss_sources s ON s.id=i.source_id "
                "WHERE i.identity_status='unidentified' AND i.tmdb_id='' AND i.imdb_id='' "
                "ORDER BY i.identity_updated_at ASC, "
                "COALESCE(NULLIF(i.published_at, ''), i.created_at) DESC, i.id DESC LIMIT ?",
                (limit,),
            ).fetchall()]

    def count_unidentified_items(self):
        with closing(self.runtime.connect()) as connection:
            return int(connection.execute(
                "SELECT COUNT(*) AS count FROM rss_items "
                "WHERE identity_status='unidentified' AND tmdb_id='' AND imdb_id=''"
            ).fetchone()["count"])

    def list_items_for_match(self, limit=200):
        limit = max(1, min(int(limit or 200), 200))
        with closing(self.runtime.connect()) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT i.*, s.name AS source_name, s.domain AS source_domain "
                "FROM rss_items i JOIN rss_sources s ON s.id=i.source_id "
                "WHERE i.identity_status<>'conflict' AND NOT EXISTS ("
                "SELECT 1 FROM rss_subscription_matches m WHERE m.item_id=i.id) "
                "ORDER BY i.identity_updated_at ASC, "
                "COALESCE(NULLIF(i.published_at, ''), i.created_at) DESC, i.id DESC LIMIT ?",
                (limit,),
            ).fetchall()]

    def count_items_for_match(self):
        with closing(self.runtime.connect()) as connection:
            return int(connection.execute(
                "SELECT COUNT(*) AS count FROM rss_items i "
                "WHERE i.identity_status<>'conflict' AND NOT EXISTS ("
                "SELECT 1 FROM rss_subscription_matches m WHERE m.item_id=i.id)"
            ).fetchone()["count"])

    @staticmethod
    def supplement_item_identity(connection, item_id, tmdb_id="", imdb_id="", source="subscription_match", confidence="fallback"):
        tmdb_id = str(tmdb_id or "").strip()[:24]
        imdb_id = str(imdb_id or "").strip().lower()[:24]
        if not tmdb_id and not imdb_id:
            return False
        cursor = connection.execute(
            "UPDATE rss_items SET tmdb_id=?, imdb_id=?, identity_status='identified', identity_source=?, "
            "identity_confidence=?, identity_updated_at=? "
            "WHERE id=? AND identity_status='unidentified' AND tmdb_id='' AND imdb_id=''",
            (tmdb_id, imdb_id, str(source or "")[:160], str(confidence or "")[:40], _iso(), str(item_id)),
        )
        return cursor.rowcount > 0

    @staticmethod
    def mark_item_identity_conflict(connection, item_id, source="subscription_match", confidence="conflict"):
        cursor = connection.execute(
            "UPDATE rss_items SET identity_status='conflict', identity_source=?, "
            "identity_confidence=?, identity_updated_at=? "
            "WHERE id=? AND identity_status='unidentified' AND tmdb_id='' AND imdb_id=''",
            (str(source or "")[:160], str(confidence or "")[:40], _iso(), str(item_id)),
        )
        return cursor.rowcount > 0

    @staticmethod
    def update_item_release_scope(
        connection,
        item_id,
        media_type,
        season_number=None,
        episode_start=None,
        episode_end=None,
    ):
        cursor = connection.execute(
            "UPDATE rss_items SET media_type=?, season_number=?, episode_start=?, episode_end=? "
            "WHERE id=? AND (media_type<>? OR season_number IS NOT ? OR episode_start IS NOT ? OR episode_end IS NOT ?)",
            (
                str(media_type or ""),
                season_number,
                episode_start,
                episode_end,
                str(item_id),
                str(media_type or ""),
                season_number,
                episode_start,
                episode_end,
            ),
        )
        return cursor.rowcount > 0

    @staticmethod
    def touch_item_identity_check(connection, item_id):
        cursor = connection.execute(
            "UPDATE rss_items SET identity_updated_at=? "
            "WHERE id=? AND identity_status='unidentified' AND tmdb_id='' AND imdb_id=''",
            (_iso(_now() + timedelta(seconds=1)), str(item_id)),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _match(row):
        if not row:
            return None
        result = dict(row)
        return {
            "id": result["id"],
            "itemId": result["item_id"],
            "subscriptionId": result["subscription_key"],
            "unitId": result["unit_key"],
            "status": result["match_status"],
            "reason": _json_load(result["match_reason_json"]),
            "triggerActionId": result["trigger_action_id"],
            "createdAt": result["created_at"],
            "updatedAt": result["updated_at"],
        }

    def get_match(self, match_id):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE id=?", (str(match_id),)
            ).fetchone()
        return self._match(row)

    def update_match(self, match_id, status, trigger_action_id=None):
        status = str(status or "").strip().lower()
        if status not in MATCH_STATUSES:
            raise ValueError("RSS 匹配状态无效")
        allowed = {
            "candidate": {"candidate", "triggered", "ignored", "expired"},
            "triggered": {"triggered", "candidate", "ignored", "confirmed", "expired"},
            "ignored": {"ignored"},
            "confirmed": {"confirmed"},
            "expired": {"expired"},
        }
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE id=?", (str(match_id),)
            ).fetchone()
            if not row:
                raise KeyError("RSS 匹配不存在")
            if status not in allowed.get(row["match_status"], set()):
                raise ValueError("RSS 匹配状态转换无效")
            action_id = row["trigger_action_id"] if trigger_action_id is None else str(trigger_action_id or "")
            connection.execute(
                "UPDATE rss_subscription_matches SET match_status=?, trigger_action_id=?, updated_at=? WHERE id=?",
                (status, action_id, _iso(), str(match_id)),
            )
            updated = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE id=?", (str(match_id),)
            ).fetchone()
        return self._match(updated)

    def list_matches_by_ids(self, match_ids):
        values = [str(value or "").strip() for value in match_ids if str(value or "").strip()]
        return [match for match in (self.get_match(value) for value in values) if match]

    def create_match(self, item_id, subscription_key, unit_key, reason, connection=None):
        item_id = str(item_id or "").strip()
        subscription_key = str(subscription_key or "").strip()
        unit_key = str(unit_key or "").strip()
        if not all((item_id, subscription_key, unit_key)):
            raise ValueError("RSS 匹配缺少条目、订阅或观察单元")
        now = _iso()

        def insert(target):
            target.execute(
                "INSERT INTO rss_subscription_matches ("
                "id, item_id, subscription_key, unit_key, match_status, match_reason_json, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?) ON CONFLICT(item_id, unit_key) DO NOTHING",
                (uuid.uuid4().hex, item_id, subscription_key, unit_key, _json_dump(reason), now, now),
            )
            return target.execute(
                "SELECT * FROM rss_subscription_matches WHERE item_id=? AND unit_key=?",
                (item_id, unit_key),
            ).fetchone()

        if connection is not None:
            return self._match(insert(connection))
        with self.runtime.transaction(immediate=True) as target:
            row = insert(target)
        return self._match(row)

    def list_matches(self, status="", limit=50, offset=0):
        status = str(status or "").strip().lower()
        if status and status not in MATCH_STATUSES:
            raise ValueError("RSS 匹配状态无效")
        limit = max(1, min(int(limit or 50), 100))
        offset = max(0, int(offset or 0))
        where = "WHERE match_status=?" if status else ""
        params = (status,) if status else ()
        with closing(self.runtime.connect()) as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS count FROM rss_subscription_matches {where}", params
            ).fetchone()["count"])
            rows = connection.execute(
                f"SELECT * FROM rss_subscription_matches {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {"items": [self._match(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    def record_fetch(self, source_id, status, record=None):
        source = self.get_source(source_id, public=False)
        if not source:
            return
        record = record or FetchRunRecord()
        now = record.now or _now()
        succeeded = status == "success"
        failure_count = 0 if succeeded else int(source["failure_count"] or 0) + 1
        backoff_until = ""
        if succeeded:
            next_poll = _iso(now + timedelta(minutes=int(source["interval_minutes"])))
        else:
            if record.retry_after_seconds is None:
                delay_seconds = min(3600, 60 * (2 ** min(failure_count - 1, 6)))
            else:
                delay_seconds = min(3600, max(1, int(record.retry_after_seconds)))
            backoff_until = _iso(now + timedelta(seconds=delay_seconds))
            next_poll = backoff_until
        safe_message = str(record.message or "")[:240]
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO rss_fetch_runs (source_id, status, item_count, http_status, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, status, int(record.item_count or 0), int(record.http_status or 0), safe_message, _iso(now)),
            )
            connection.execute(
                "UPDATE rss_sources SET etag=?, last_modified=?, last_success_at=CASE WHEN ?='success' THEN ? ELSE last_success_at END, "
                "last_error=CASE WHEN ?='success' THEN '' ELSE ? END, failure_count=?, backoff_until=?, "
                "next_poll_at=?, updated_at=? WHERE id=?",
                (
                    record.etag or source["etag"], record.last_modified or source["last_modified"], status, _iso(now), status,
                    safe_message, failure_count, backoff_until, next_poll, _iso(now), source_id,
                ),
            )
            connection.execute("DELETE FROM rss_fetch_runs WHERE created_at<?", (_iso(now - timedelta(days=30)),))
            connection.execute(
                "DELETE FROM rss_fetch_runs WHERE source_id=? AND id IN ("
                "SELECT id FROM rss_fetch_runs WHERE source_id=? ORDER BY id DESC LIMIT -1 OFFSET 1000)",
                (source_id, source_id),
            )

    def due_sources(self):
        now = _iso()
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM rss_sources WHERE enabled=1 AND (next_poll_at='' OR next_poll_at<=?) "
                "AND (backoff_until='' OR backoff_until<=?) ORDER BY next_poll_at, name LIMIT 10",
                (now, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup(self):
        with self.runtime.transaction(immediate=True) as connection:
            expired = [row["id"] for row in connection.execute("SELECT id FROM rss_items WHERE expires_at<? LIMIT 1000", (_iso(),)).fetchall()]
            if expired:
                connection.executemany("DELETE FROM rss_item_search WHERE item_id=?", ((value,) for value in expired))
                connection.executemany("DELETE FROM rss_items WHERE id=?", ((value,) for value in expired))
            connection.execute("DELETE FROM rss_fetch_runs WHERE created_at<?", (_iso(_now() - timedelta(days=30)),))
            connection.execute("DELETE FROM rss_match_runs WHERE created_at<?", (_iso(_now() - timedelta(days=30)),))
        return len(expired)

    def record_match_run(self, scanned_count, match_count, status="success", message="", connection=None):
        values = (
            str(status or "success"),
            max(0, int(scanned_count or 0)),
            max(0, int(match_count or 0)),
            str(message or "")[:240],
            _iso(),
        )

        def insert(target):
            target.execute(
                "INSERT INTO rss_match_runs (status, scanned_count, match_count, message, created_at) VALUES (?, ?, ?, ?, ?)",
                values,
            )

        if connection is not None:
            insert(connection)
        else:
            with self.runtime.transaction(immediate=True) as target:
                insert(target)

    def record_identity_backfill_run(self, result, status="success", message=""):
        values = (
            str(status or "success"),
            max(0, int(result.get("scanned") or 0)),
            max(0, int(result.get("identified") or 0)),
            max(0, int(result.get("conflicts") or 0)),
            max(0, int(result.get("unchanged") or 0)),
            max(0, int(result.get("remaining") or 0)),
            max(0, int(result.get("limit") or 0)),
            str(message or "")[:240],
            _iso(),
        )
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO rss_identity_backfill_runs ("
                "status, scanned_count, identified_count, conflict_count, unchanged_count, remaining_count, "
                "batch_limit, message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def summary(self, enabled=False):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled, "
                "SUM(CASE WHEN last_error<>'' THEN 1 ELSE 0 END) AS errors, MAX(last_success_at) AS last_success FROM rss_sources"
            ).fetchone()
            item_count = int(connection.execute("SELECT COUNT(*) AS count FROM rss_items").fetchone()["count"])
            match_count = int(connection.execute("SELECT COUNT(*) AS count FROM rss_subscription_matches").fetchone()["count"])
            match_run = connection.execute(
                "SELECT status, scanned_count, match_count, message, created_at FROM rss_match_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            identity_run = connection.execute(
                "SELECT status, scanned_count, identified_count, conflict_count, unchanged_count, remaining_count, "
                "batch_limit, message, created_at FROM rss_identity_backfill_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "enabled": bool(enabled),
            "sources": int(row["total"] or 0),
            "activeSources": int(row["enabled"] or 0),
            "errorSources": int(row["errors"] or 0),
            "items": item_count,
            "lastSuccessAt": row["last_success"] or "",
            "matches": match_count,
            "matcherRan": bool(match_run),
            "matcherStatus": match_run["status"] if match_run else "not_run",
            "lastMatchAt": match_run["created_at"] if match_run else "",
            "lastMatchStatus": match_run["status"] if match_run else "not_run",
            "lastMatchScanned": int(match_run["scanned_count"] or 0) if match_run else 0,
            "lastMatchCreated": int(match_run["match_count"] or 0) if match_run else 0,
            "identityBackfillRan": bool(identity_run),
            "identityBackfillStatus": identity_run["status"] if identity_run else "not_run",
            "lastIdentityBackfillAt": identity_run["created_at"] if identity_run else "",
            "lastIdentityBackfillStatus": identity_run["status"] if identity_run else "not_run",
            "lastIdentityBackfillScanned": int(identity_run["scanned_count"] or 0) if identity_run else 0,
            "lastIdentityBackfillIdentified": int(identity_run["identified_count"] or 0) if identity_run else 0,
            "lastIdentityBackfillConflicts": int(identity_run["conflict_count"] or 0) if identity_run else 0,
            "lastIdentityBackfillUnchanged": int(identity_run["unchanged_count"] or 0) if identity_run else 0,
            "lastIdentityBackfillRemaining": int(identity_run["remaining_count"] or 0) if identity_run else item_count,
            "lastIdentityBackfillLimit": int(identity_run["batch_limit"] or 0) if identity_run else 0,
        }
