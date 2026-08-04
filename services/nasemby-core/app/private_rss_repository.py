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
from app.torra_subscription_keys import torra_public_storage_key


MATCH_STATUSES = {"candidate", "ignored", "triggered", "confirmed", "expired"}
IDENTITY_STATUSES = {"identified", "conflict", "unidentified"}
RSS_REVIEW_STATES = {"needs_review", "follow_needs_review", "unlinked"}
RSS_GROUP_SCOPES = {"scoreable", "cleanup"}
CLEANUP_RULE_VERSION = "rss-match-cleanup-v1"
RSS_GROUP_STATES = {
    "initial_best",
    "waiting_baseline",
    "monitoring_rss",
    "upgrade_available",
    "protected",
    "needs_cleanup",
    "blocked",
}
MATCH_EVALUATION_COLUMNS = {
    "torra_subscription_id": "TEXT NOT NULL DEFAULT ''",
    "target_key": "TEXT NOT NULL DEFAULT ''",
    "artifact_key": "TEXT NOT NULL DEFAULT ''",
    "rule_id": "TEXT NOT NULL DEFAULT ''",
    "rule_hash": "TEXT NOT NULL DEFAULT ''",
    "candidate_score": "REAL",
    "baseline_score": "REAL",
    "evaluation_status": "TEXT NOT NULL DEFAULT 'pending'",
    "decision": "TEXT NOT NULL DEFAULT ''",
    "evaluation_reason": "TEXT NOT NULL DEFAULT ''",
    "evaluation_action_id": "TEXT NOT NULL DEFAULT ''",
    "download_action_id": "TEXT NOT NULL DEFAULT ''",
    "candidate_summary_json": "TEXT NOT NULL DEFAULT '{}'",
    "baseline_summary_json": "TEXT NOT NULL DEFAULT '{}'",
    "is_best_candidate": "INTEGER NOT NULL DEFAULT 0",
    "evaluated_at": "TEXT NOT NULL DEFAULT ''",
    "archive_state": "TEXT NOT NULL DEFAULT 'active'",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
    "archive_reason_code": "TEXT NOT NULL DEFAULT ''",
    "archive_run_id": "TEXT NOT NULL DEFAULT ''",
    "version": "INTEGER NOT NULL DEFAULT 1",
}


class RssMatchCleanupConflict(RuntimeError):
    pass


class RssMatchCleanupStale(RssMatchCleanupConflict):
    pass


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


def _follow_link_exists_sql(item_alias="i"):
    return (
        "EXISTS (SELECT 1 FROM rss_subscription_matches follow_match "
        f"WHERE follow_match.item_id={item_alias}.id "
        "AND follow_match.match_status IN ('candidate','triggered','confirmed'))"
    )


def _review_required_sql(item_alias="i"):
    return (
        f"({item_alias}.identity_status IN ('unidentified', 'conflict') "
        f"OR {item_alias}.media_type='' "
        f"OR ({item_alias}.media_type='tv' AND {item_alias}.season_number IS NULL))"
    )


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


def _json_list(value):
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _cleanup_fingerprint(rows):
    facts = [{
        "matchId": str(row.get("id") or ""),
        "version": int(row.get("version") or 1),
        "archiveState": str(row.get("archive_state") or "active"),
        "evaluationStatus": str(row.get("evaluation_status") or ""),
        "evaluationReason": str(row.get("evaluation_reason") or ""),
        "subscriptionKey": str(row.get("subscription_key") or ""),
        "unitKey": str(row.get("unit_key") or ""),
        "targetKey": str(row.get("target_key") or ""),
        "artifactKey": str(row.get("artifact_key") or ""),
    } for row in sorted(rows, key=lambda value: str(value.get("id") or ""))]
    payload = _json_dump({"cleanupRuleVersion": CLEANUP_RULE_VERSION, "facts": facts})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cleanup_eligible(row):
    return (
        str(row.get("archive_state") or "active") == "active"
        and str(row.get("evaluation_status") or "") == "blocked"
        and str(row.get("evaluation_reason") or "") == "subscription_missing"
    )


def _create_match_table(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS rss_subscription_matches ("
        "id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES rss_items(id) ON DELETE CASCADE, "
        "subscription_key TEXT NOT NULL, unit_key TEXT NOT NULL, match_status TEXT NOT NULL DEFAULT 'candidate', "
        "match_reason_json TEXT NOT NULL DEFAULT '{}', trigger_action_id TEXT NOT NULL DEFAULT '', "
        "torra_subscription_id TEXT NOT NULL DEFAULT '', target_key TEXT NOT NULL DEFAULT '', "
        "artifact_key TEXT NOT NULL DEFAULT '', rule_id TEXT NOT NULL DEFAULT '', rule_hash TEXT NOT NULL DEFAULT '', "
        "candidate_score REAL, baseline_score REAL, evaluation_status TEXT NOT NULL DEFAULT 'pending', "
        "decision TEXT NOT NULL DEFAULT '', evaluation_reason TEXT NOT NULL DEFAULT '', "
        "evaluation_action_id TEXT NOT NULL DEFAULT '', download_action_id TEXT NOT NULL DEFAULT '', "
        "candidate_summary_json TEXT NOT NULL DEFAULT '{}', baseline_summary_json TEXT NOT NULL DEFAULT '{}', "
        "is_best_candidate INTEGER NOT NULL DEFAULT 0, "
        "evaluated_at TEXT NOT NULL DEFAULT '', archive_state TEXT NOT NULL DEFAULT 'active', "
        "archived_at TEXT NOT NULL DEFAULT '', archive_reason_code TEXT NOT NULL DEFAULT '', "
        "archive_run_id TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(item_id, unit_key))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rss_matches_status "
        "ON rss_subscription_matches(match_status, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_rss_matches_artifact "
        "ON rss_subscription_matches(artifact_key, subscription_key, created_at DESC)"
    )


def _ensure_match_columns(connection):
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(rss_subscription_matches)").fetchall()
    }
    for name, definition in MATCH_EVALUATION_COLUMNS.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE rss_subscription_matches ADD COLUMN {name} {definition}"
            )
    _create_match_table(connection)


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
        _ensure_match_columns(connection)
        return
    _migrate_legacy_match_table(connection)
    _ensure_match_columns(connection)


def _initialize_rule_snapshot_table(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS torra_rule_snapshots ("
        "rule_id TEXT NOT NULL, rule_hash TEXT NOT NULL, rule_json TEXT NOT NULL, "
        "observed_at TEXT NOT NULL, PRIMARY KEY(rule_id, rule_hash))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_torra_rule_snapshots_observed "
        "ON torra_rule_snapshots(observed_at DESC, rule_id)"
    )


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
                "match_checked_at TEXT NOT NULL DEFAULT '', "
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
            if "match_checked_at" not in item_columns:
                connection.execute("ALTER TABLE rss_items ADD COLUMN match_checked_at TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rss_items_identity "
                "ON rss_items(identity_status, published_at DESC, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rss_items_match_check "
                "ON rss_items(match_checked_at, identity_status)"
            )
            _initialize_match_table(connection)
            _initialize_rule_snapshot_table(connection)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_match_cleanup_runs ("
                "id TEXT PRIMARY KEY, status TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                "selected_json TEXT NOT NULL DEFAULT '[]', preview_json TEXT NOT NULL DEFAULT '{}', "
                "result_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL DEFAULT '', "
                "item_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, confirmed_at TEXT NOT NULL DEFAULT '', "
                "updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rss_match_cleanup_runs_created "
                "ON rss_match_cleanup_runs(created_at DESC)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_rss_match_cleanup_runs_idempotency "
                "ON rss_match_cleanup_runs(idempotency_key) WHERE idempotency_key<>''"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS rss_match_cleanup_items ("
                "run_id TEXT NOT NULL REFERENCES rss_match_cleanup_runs(id) ON DELETE CASCADE, "
                "match_id TEXT NOT NULL, match_version INTEGER NOT NULL, reason_code TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, match_id))"
            )

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
        item = {
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
        if "follow_state" in row.keys() and row["follow_state"] in {"linked", "unlinked"}:
            item["followState"] = row["follow_state"]
        return item

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
        review_state="",
        published_from="",
        published_before="",
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
        review_state = str(review_state or "").strip().lower()
        if review_state and review_state not in RSS_REVIEW_STATES:
            raise ValueError("复核状态无效")
        published_from = str(published_from or "").strip()
        published_before = str(published_before or "").strip()
        if bool(published_from) != bool(published_before) or (
            published_from and published_from >= published_before
        ):
            raise ValueError("发布时间范围无效")

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
        if review_state in {"needs_review", "follow_needs_review"}:
            base_where.append(_review_required_sql())
        if review_state == "follow_needs_review":
            base_where.append(_follow_link_exists_sql())
        elif review_state == "unlinked":
            base_where.append(f"NOT {_follow_link_exists_sql()}")
        if published_from:
            base_where.append("COALESCE(NULLIF(i.published_at, ''), i.created_at) >= ?")
            base_where.append("COALESCE(NULLIF(i.published_at, ''), i.created_at) < ?")
            base_params.extend((published_from, published_before))

        targeted = bool(target_tmdb_id or (query_text and (target_media_type or target_season is not None or target_year)))
        follow_state_select = (
            f"CASE WHEN {_follow_link_exists_sql()} THEN 'linked' ELSE 'unlinked' END AS follow_state"
        )
        with closing(self.runtime.connect()) as connection:
            if targeted:
                rows_by_id = {}

                def add_candidates(extra_joins, extra_where, extra_params):
                    where = [*base_where, *extra_where]
                    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
                    rows = connection.execute(
                        "SELECT i.*, s.name AS source_name, s.domain AS source_domain, "
                        f"{follow_state_select} FROM rss_items i "
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
                "SELECT i.*, s.name AS source_name, s.domain AS source_domain, "
                f"{follow_state_select} FROM rss_items i "
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
                "SELECT i.*, s.name AS source_name, s.domain AS source_domain, "
                f"CASE WHEN {_follow_link_exists_sql()} THEN 'linked' ELSE 'unlinked' END AS follow_state "
                "FROM rss_items i "
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
                "ORDER BY i.match_checked_at ASC, "
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

    def count_unchecked_items_for_match(self):
        with closing(self.runtime.connect()) as connection:
            return int(connection.execute(
                "SELECT COUNT(*) AS count FROM rss_items i "
                "WHERE i.identity_status<>'conflict' AND i.match_checked_at='' AND NOT EXISTS ("
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
    def supplement_item_tmdb_identity(
        connection,
        item_id,
        tmdb_id,
        source="subscription_match",
        confidence="fallback",
    ):
        tmdb_id = str(tmdb_id or "").strip()[:24]
        if not tmdb_id:
            return False
        cursor = connection.execute(
            "UPDATE rss_items SET tmdb_id=?, identity_status='identified', identity_source=?, "
            "identity_confidence=?, identity_updated_at=? "
            "WHERE id=? AND identity_status<>'conflict' AND tmdb_id=''",
            (
                tmdb_id,
                str(source or "")[:160],
                str(confidence or "")[:40],
                _iso(),
                str(item_id),
            ),
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
    def touch_item_match_check(connection, item_id):
        cursor = connection.execute(
            "UPDATE rss_items SET match_checked_at=? WHERE id=?",
            (_iso(), str(item_id)),
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
            "torraLinked": bool(result.get("torra_subscription_id")),
            "targetKey": result.get("target_key") or "",
            "artifactKey": result.get("artifact_key") or "",
            "ruleId": result.get("rule_id") or "",
            "ruleHash": result.get("rule_hash") or "",
            "candidateScore": result.get("candidate_score"),
            "baselineScore": result.get("baseline_score"),
            "evaluationStatus": result.get("evaluation_status") or "pending",
            "decision": result.get("decision") or "",
            "evaluationReason": result.get("evaluation_reason") or "",
            "evaluationActionId": result.get("evaluation_action_id") or "",
            "downloadActionId": result.get("download_action_id") or "",
            "candidateSummary": _json_load(result.get("candidate_summary_json")),
            "baselineSummary": _json_load(result.get("baseline_summary_json")),
            "bestCandidate": bool(result.get("is_best_candidate")),
            "evaluatedAt": result.get("evaluated_at") or "",
            "archiveState": result.get("archive_state") or "active",
            "archivedAt": result.get("archived_at") or "",
            "archiveReasonCode": result.get("archive_reason_code") or "",
            "archiveRunId": result.get("archive_run_id") or "",
            "version": int(result.get("version") or 1),
            "createdAt": result["created_at"],
            "updatedAt": result["updated_at"],
        }

    def get_match(self, match_id):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE id=?", (str(match_id),)
            ).fetchone()
        return self._match(row)

    def get_match_internal(self, match_id):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE id=?", (str(match_id),)
            ).fetchone()
        return dict(row) if row else None

    def set_match_binding(
        self,
        match_id,
        *,
        torra_subscription_id,
        target_key,
        artifact_key,
        connection=None,
    ):
        values = (
            str(torra_subscription_id or ""),
            str(target_key or ""),
            str(artifact_key or ""),
            _iso(),
            str(match_id),
        )

        def update(target):
            target.execute(
                "UPDATE rss_subscription_matches SET torra_subscription_id=?, target_key=?, "
                "artifact_key=?, updated_at=?, version=version+1 WHERE id=?",
                values,
            )

        if connection is not None:
            update(connection)
        else:
            with self.runtime.transaction(immediate=True) as target:
                update(target)
        return self.get_match(match_id)

    def list_internal_matches_for_artifact(self, artifact_key):
        artifact_key = str(artifact_key or "").strip()
        if not artifact_key:
            return []
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE artifact_key=? "
                "AND archive_state='active' ORDER BY created_at, id",
                (artifact_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_match_evaluation(self, match_ids, evaluation):
        ids = sorted({str(value or "").strip() for value in match_ids if str(value or "").strip()})
        if not ids:
            return []
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        candidate_score = evaluation.get("candidateScore")
        baseline_score = evaluation.get("baselineScore")
        if isinstance(candidate_score, bool) or (
            candidate_score is not None and not isinstance(candidate_score, (int, float))
        ):
            raise ValueError("candidateScore must be numeric or null")
        if isinstance(baseline_score, bool) or (
            baseline_score is not None and not isinstance(baseline_score, (int, float))
        ):
            raise ValueError("baselineScore must be numeric or null")
        now = str(evaluation.get("evaluatedAt") or _iso())
        values = (
            str(evaluation.get("ruleId") or ""),
            str(evaluation.get("ruleHash") or ""),
            float(candidate_score) if candidate_score is not None else None,
            float(baseline_score) if baseline_score is not None else None,
            str(evaluation.get("status") or "pending"),
            str(evaluation.get("decision") or ""),
            str(evaluation.get("reason") or ""),
            str(evaluation.get("actionId") or ""),
            _json_dump(evaluation.get("candidateSummary")),
            _json_dump(evaluation.get("baselineSummary")),
            now,
            now,
        )
        placeholders = ",".join("?" for _ in ids)
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE rss_subscription_matches SET rule_id=?, rule_hash=?, candidate_score=?, "
                "baseline_score=?, evaluation_status=?, decision=?, evaluation_reason=?, "
                "evaluation_action_id=?, candidate_summary_json=?, baseline_summary_json=?, "
                f"evaluated_at=?, updated_at=?, version=version+1 WHERE archive_state='active' AND id IN ({placeholders})",
                (*values, *ids),
            )
        return [match for match in (self.get_match(match_id) for match_id in ids) if match]

    def list_matches_for_units(self, unit_refs):
        refs = sorted({
            (str(subscription_key or "").strip(), str(unit_key or "").strip())
            for subscription_key, unit_key in unit_refs
            if str(subscription_key or "").strip() and str(unit_key or "").strip()
        })
        if not refs:
            return []
        where = " OR ".join("(subscription_key=? AND unit_key=?)" for _ in refs)
        params = [value for ref in refs for value in ref]
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM rss_subscription_matches WHERE archive_state='active' AND ({where}) ORDER BY created_at, id",
                params,
            ).fetchall()
        return [self._match(row) for row in rows]

    def save_candidate_decisions(self, updates):
        rows = []
        now = _iso()
        for update in updates if isinstance(updates, list) else []:
            match_ids = sorted({
                str(value or "").strip()
                for value in update.get("matchIds") or []
                if str(value or "").strip()
            })
            for match_id in match_ids:
                rows.append((
                    str(update.get("decision") or ""),
                    str(update.get("reason") or ""),
                    1 if update.get("bestCandidate") else 0,
                    now,
                    match_id,
                ))
        if not rows:
            return 0
        with self.runtime.transaction(immediate=True) as connection:
            connection.executemany(
                "UPDATE rss_subscription_matches SET decision=?, evaluation_reason=?, "
                "is_best_candidate=?, updated_at=?, version=version+1 WHERE archive_state='active' AND id=?",
                rows,
            )
        return len(rows)

    def save_rule_snapshots(self, snapshots, observed_at=None):
        observed_at = str(observed_at or _iso())
        rows = []
        for snapshot in snapshots if isinstance(snapshots, list) else []:
            if not isinstance(snapshot, dict):
                continue
            rule_id = str(snapshot.get("ruleId") or "").strip()
            rule_hash = str(snapshot.get("ruleHash") or "").strip()
            rule = snapshot.get("rule")
            if not rule_id or not rule_hash or not isinstance(rule, dict):
                continue
            rows.append((rule_id, rule_hash, _json_dump(rule), observed_at))
        if not rows:
            return 0
        with self.runtime.transaction(immediate=True) as connection:
            before = connection.total_changes
            connection.executemany(
                "INSERT INTO torra_rule_snapshots (rule_id, rule_hash, rule_json, observed_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(rule_id, rule_hash) DO UPDATE SET observed_at=excluded.observed_at",
                rows,
            )
            return connection.total_changes - before

    def list_items_for_watch_backfill(self, unit, unit_keys, limit=200):
        unit = unit if isinstance(unit, dict) else {}
        first_success_at = str(unit.get("first_success_at") or "").strip()
        if not first_success_at:
            return []
        keys = sorted({str(value or "").strip() for value in unit_keys if str(value or "").strip()})
        if not keys:
            return []
        limit = max(1, min(int(limit or 200), 200))
        placeholders = ",".join("?" for _ in keys)
        clauses = [
            "i.identity_status<>'conflict'",
            "COALESCE(NULLIF(i.published_at, ''), i.created_at)>=?",
            f"NOT EXISTS (SELECT 1 FROM rss_subscription_matches m WHERE m.item_id=i.id AND m.unit_key IN ({placeholders}))",
        ]
        params = [first_success_at, *keys]
        season = unit.get("season_number")
        if season is None:
            clauses.append("i.media_type IN ('', 'movie')")
        else:
            clauses.append("i.media_type IN ('', 'tv')")
            clauses.append("(i.season_number IS NULL OR i.season_number=?)")
            params.append(int(season))
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT i.*, s.name AS source_name, s.domain AS source_domain "
                "FROM rss_items i JOIN rss_sources s ON s.id=i.source_id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY COALESCE(NULLIF(i.published_at, ''), i.created_at) DESC, i.id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_match_for_item_unit(self, item_id, unit_key):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM rss_subscription_matches WHERE item_id=? AND unit_key=?",
                (str(item_id), str(unit_key)),
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
            if str(row["archive_state"] or "active") != "active":
                raise ValueError("已归档 RSS 匹配不可修改")
            action_id = row["trigger_action_id"] if trigger_action_id is None else str(trigger_action_id or "")
            connection.execute(
                "UPDATE rss_subscription_matches SET match_status=?, trigger_action_id=?, updated_at=?, "
                "version=version+1 WHERE id=?",
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
        where = "WHERE archive_state='active'"
        params = ()
        if status:
            where += " AND match_status=?"
            params = (status,)
        with closing(self.runtime.connect()) as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS count FROM rss_subscription_matches {where}", params
            ).fetchone()["count"])
            rows = connection.execute(
                f"SELECT * FROM rss_subscription_matches {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {"items": [self._match(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _cleanup_preview_item(row):
        return {
            "matchId": str(row.get("id") or ""),
            "subscriptionId": str(row.get("subscription_key") or ""),
            "unitId": str(row.get("unit_key") or ""),
            "artifactKey": str(row.get("artifact_key") or ""),
            "title": str(row.get("item_title") or "")[:240],
            "version": int(row.get("version") or 1),
            "reasonCode": "subscription_missing",
        }

    @staticmethod
    def _cleanup_skip_reason(row):
        if not row:
            return "match_missing"
        if str(row.get("archive_state") or "active") == "archived":
            return "already_archived"
        if str(row.get("evaluation_status") or "") != "blocked":
            return "candidate_still_active"
        reason = str(row.get("evaluation_reason") or "")
        if reason == "subscription_missing":
            return "eligible"
        if "conflict" in reason:
            return "ownership_conflict"
        return "reason_not_eligible"

    def create_match_cleanup_preview(self, match_ids):
        ids = sorted({str(value or "").strip() for value in match_ids or [] if str(value or "").strip()})
        if not ids or len(ids) > 200 or any(len(value) > 80 for value in ids):
            raise ValueError("RSS 清理预览需要 1 到 200 个匹配")
        placeholders = ",".join("?" for _ in ids)
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT m.*, i.title AS item_title FROM rss_subscription_matches m "
                "JOIN rss_items i ON i.id=m.item_id "
                f"WHERE m.id IN ({placeholders}) ORDER BY m.id",
                ids,
            ).fetchall()
        by_id = {str(row["id"]): dict(row) for row in rows}
        eligible = [by_id[match_id] for match_id in ids if match_id in by_id and _cleanup_eligible(by_id[match_id])]
        skipped = [{
            "matchId": match_id,
            "reasonCode": self._cleanup_skip_reason(by_id.get(match_id)),
        } for match_id in ids if match_id not in {str(row.get("id") or "") for row in eligible}]
        selected = [str(row["id"]) for row in eligible]
        fingerprint = _cleanup_fingerprint(eligible)
        now = _iso()
        run_id = f"rss-match-cleanup:{uuid.uuid4().hex[:24]}"
        preview = {
            "id": run_id,
            "status": "previewed",
            "fingerprint": fingerprint,
            "cleanupRuleVersion": CLEANUP_RULE_VERSION,
            "itemCount": len(eligible),
            "items": [self._cleanup_preview_item(row) for row in eligible],
            "skipped": skipped,
            "createdAt": now,
        }
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO rss_match_cleanup_runs ("
                "id, status, fingerprint, selected_json, preview_json, item_count, created_at, updated_at"
                ") VALUES (?, 'previewed', ?, ?, ?, ?, ?, ?)",
                (run_id, fingerprint, _json_dump(selected), _json_dump(preview), len(eligible), now, now),
            )
        return preview

    def _mark_cleanup_run(self, run_id, status):
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE rss_match_cleanup_runs SET status=?, updated_at=? "
                "WHERE id=? AND status='previewed'",
                (status, _iso(), str(run_id or "")),
            )

    def apply_match_cleanup(
        self,
        *,
        preview_id,
        fingerprint,
        match_ids,
        idempotency_key,
    ):
        preview_id = str(preview_id or "").strip()
        fingerprint = str(fingerprint or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        selected = sorted({str(value or "").strip() for value in match_ids or [] if str(value or "").strip()})
        if not preview_id or len(preview_id) > 80 or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("RSS 清理确认参数无效")
        if not idempotency_key or len(idempotency_key) > 160:
            raise ValueError("RSS 清理确认缺少有效幂等键")
        if not selected or len(selected) > 200 or any(len(value) > 80 for value in selected):
            raise ValueError("RSS 清理确认需要 1 到 200 个匹配")

        with closing(self.runtime.connect()) as connection:
            replay = connection.execute(
                "SELECT id, result_json FROM rss_match_cleanup_runs "
                "WHERE idempotency_key=? AND status='applied'",
                (idempotency_key,),
            ).fetchone()
        if replay:
            if str(replay["id"] or "") != preview_id:
                raise RssMatchCleanupConflict("RSS 清理幂等键已用于其他预览")
            return _json_load(replay["result_json"])

        try:
            with self.runtime.transaction(immediate=True) as connection:
                replay = connection.execute(
                    "SELECT id, result_json FROM rss_match_cleanup_runs "
                    "WHERE idempotency_key=? AND status='applied'",
                    (idempotency_key,),
                ).fetchone()
                if replay:
                    if str(replay["id"] or "") != preview_id:
                        raise RssMatchCleanupConflict("RSS 清理幂等键已用于其他预览")
                    return _json_load(replay["result_json"])
                run = connection.execute(
                    "SELECT * FROM rss_match_cleanup_runs WHERE id=?",
                    (preview_id,),
                ).fetchone()
                if not run:
                    raise KeyError("RSS 清理预览不存在")
                if str(run["status"] or "") != "previewed":
                    raise RssMatchCleanupConflict("RSS 清理预览当前不可执行")
                expected = sorted(str(value or "") for value in _json_list(run["selected_json"]) if str(value or ""))
                if fingerprint != str(run["fingerprint"] or "") or selected != expected:
                    raise RssMatchCleanupStale("RSS 清理预览已过期")
                placeholders = ",".join("?" for _ in selected)
                rows = connection.execute(
                    f"SELECT * FROM rss_subscription_matches WHERE id IN ({placeholders}) ORDER BY id",
                    selected,
                ).fetchall()
                current = [dict(row) for row in rows]
                if len(current) != len(selected) or any(not _cleanup_eligible(row) for row in current):
                    raise RssMatchCleanupStale("RSS 清理预览已过期")
                if _cleanup_fingerprint(current) != fingerprint:
                    raise RssMatchCleanupStale("RSS 清理预览已过期")

                now = _iso()
                connection.execute(
                    f"UPDATE rss_subscription_matches SET archive_state='archived', archived_at=?, "
                    "archive_reason_code='subscription_missing', archive_run_id=?, updated_at=?, "
                    f"version=version+1 WHERE id IN ({placeholders})",
                    (now, preview_id, now, *selected),
                )
                connection.executemany(
                    "INSERT INTO rss_match_cleanup_items ("
                    "run_id, match_id, match_version, reason_code, status, created_at"
                    ") VALUES (?, ?, ?, 'subscription_missing', 'archived', ?)",
                    ((preview_id, str(row["id"]), int(row.get("version") or 1), now) for row in current),
                )
                result = {
                    "id": preview_id,
                    "status": "applied",
                    "fingerprint": fingerprint,
                    "archivedCount": len(current),
                    "archivedMatchIds": selected,
                    "appliedAt": now,
                }
                connection.execute(
                    "UPDATE rss_match_cleanup_runs SET status='applied', result_json=?, "
                    "idempotency_key=?, confirmed_at=?, updated_at=? WHERE id=?",
                    (_json_dump(result), idempotency_key, now, now, preview_id),
                )
                return result
        except RssMatchCleanupStale:
            self._mark_cleanup_run(preview_id, "stale")
            raise
        except (KeyError, RssMatchCleanupConflict):
            raise
        except Exception:
            self._mark_cleanup_run(preview_id, "failed")
            raise

    @staticmethod
    def _match_cleanup_run(row):
        if not row:
            return None
        preview = _json_load(row["preview_json"])
        applied = _json_load(row["result_json"])
        return {
            "id": str(row["id"] or ""),
            "status": str(row["status"] or ""),
            "fingerprint": str(row["fingerprint"] or ""),
            "itemCount": int(row["item_count"] or 0),
            "items": preview.get("items") if isinstance(preview.get("items"), list) else [],
            "archivedCount": int(applied.get("archivedCount") or 0),
            "createdAt": str(row["created_at"] or ""),
            "confirmedAt": str(row["confirmed_at"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        }

    def get_match_cleanup_run(self, run_id):
        run_id = str(run_id or "").strip()
        if not run_id or len(run_id) > 80:
            return None
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT id, status, fingerprint, preview_json, result_json, item_count, "
                "created_at, confirmed_at, updated_at FROM rss_match_cleanup_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        return self._match_cleanup_run(row)

    def list_match_cleanup_runs(self, limit=20):
        limit = max(1, min(int(limit or 20), 100))
        with closing(self.runtime.connect()) as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS count FROM rss_match_cleanup_runs"
            ).fetchone()["count"])
            rows = connection.execute(
                "SELECT id, status, fingerprint, preview_json, result_json, item_count, "
                "created_at, confirmed_at, updated_at FROM rss_match_cleanup_runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {
            "items": [self._match_cleanup_run(row) for row in rows],
            "total": total,
        }

    @staticmethod
    def _candidate_group_state(candidates):
        best = next((row for row in candidates if row.get("bestCandidate")), None)
        if best:
            decision = str(best.get("decision") or "")
            if decision == "current_best":
                return "upgrade_available"
            if decision == "best_available":
                return "initial_best"
            if decision == "best_waiting_baseline":
                return "waiting_baseline"
            if decision in {"same_score", "lower_score", "rule_rejected"}:
                return "protected"
        if candidates and all(
            row.get("evaluationStatus") == "blocked"
            and row.get("evaluationReason") == "subscription_missing"
            for row in candidates
        ):
            return "needs_cleanup"
        if candidates and all(row.get("evaluationStatus") == "blocked" for row in candidates):
            return "blocked"
        return "monitoring_rss"

    @staticmethod
    def _candidate_group_episode_label(candidates):
        reason = next((
            row.get("reason") for row in candidates
            if isinstance(row.get("reason"), dict)
        ), {})
        if str(reason.get("mediaType") or "") == "movie":
            return "电影"
        season = reason.get("season") if isinstance(reason.get("season"), dict) else {}
        episode = reason.get("episode") if isinstance(reason.get("episode"), dict) else {}
        season_number = season.get("unit") or season.get("item")
        episode_number = episode.get("unit")
        if isinstance(season_number, int) and isinstance(episode_number, int):
            return f"S{season_number:02d}E{episode_number:02d}"
        return "季集暂未确认"

    @staticmethod
    def _candidate_groups_query(where=""):
        active_where = f"{where} AND archive_state='active'" if where else "WHERE archive_state='active'"
        return (
            "WITH candidate_groups AS ("
            "SELECT subscription_key, unit_key, MAX(created_at) AS latest_at, "
            "CASE "
            "WHEN MAX(CASE WHEN is_best_candidate=1 AND decision='current_best' THEN 1 ELSE 0 END)=1 THEN 'upgrade_available' "
            "WHEN MAX(CASE WHEN is_best_candidate=1 AND decision='best_available' THEN 1 ELSE 0 END)=1 THEN 'initial_best' "
            "WHEN MAX(CASE WHEN is_best_candidate=1 AND decision='best_waiting_baseline' THEN 1 ELSE 0 END)=1 THEN 'waiting_baseline' "
            "WHEN MAX(CASE WHEN is_best_candidate=1 AND decision IN ('same_score','lower_score','rule_rejected') THEN 1 ELSE 0 END)=1 THEN 'protected' "
            "WHEN COUNT(*)>0 AND SUM(CASE WHEN evaluation_status='blocked' AND evaluation_reason='subscription_missing' THEN 1 ELSE 0 END)=COUNT(*) THEN 'needs_cleanup' "
            "WHEN COUNT(*)>0 AND SUM(CASE WHEN evaluation_status='blocked' THEN 1 ELSE 0 END)=COUNT(*) THEN 'blocked' "
            "ELSE 'monitoring_rss' END AS group_state "
            f"FROM rss_subscription_matches {active_where} GROUP BY subscription_key, unit_key"
            ") "
        )

    @staticmethod
    def _stored_subscription_key(connection, requested):
        requested = str(requested or "").strip()
        if not requested:
            return ""
        rows = connection.execute(
            "SELECT DISTINCT subscription_key FROM rss_subscription_matches"
        ).fetchall()
        matches = {
            str(row["subscription_key"] or "")
            for row in rows
            if requested in {
                str(row["subscription_key"] or ""),
                torra_public_storage_key(row["subscription_key"]),
            }
        }
        if len(matches) > 1:
            raise ValueError("RSS 订阅筛选存在冲突")
        return next(iter(matches), requested)

    def list_candidate_groups(
        self,
        status="",
        group_state="",
        group_scope="",
        subscription_id="",
        media_type="",
        season_number=None,
        episode_number=None,
        match_id="",
        limit=20,
        offset=0,
    ):
        status = str(status or "").strip().lower()
        if status and status not in MATCH_STATUSES:
            raise ValueError("RSS 匹配状态无效")
        group_state = str(group_state or "").strip().lower()
        if group_state and group_state not in RSS_GROUP_STATES:
            raise ValueError("RSS 候选组状态无效")
        group_scope = str(group_scope or "").strip().lower()
        if group_scope and group_scope not in RSS_GROUP_SCOPES:
            raise ValueError("RSS 候选组范围无效")
        subscription_id = str(subscription_id or "").strip()
        if len(subscription_id) > 200:
            raise ValueError("RSS 订阅筛选无效")
        media_type = str(media_type or "").strip().lower()
        if media_type and media_type not in {"movie", "tv"}:
            raise ValueError("媒体类型无效")
        match_id = str(match_id or "").strip()
        if len(match_id) > 80:
            raise ValueError("RSS 匹配 ID 无效")
        try:
            season = int(season_number) if season_number not in (None, "") else None
            episode = int(episode_number) if episode_number not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ValueError("季集筛选无效") from exc
        if season is not None and not 0 < season <= 999:
            raise ValueError("季号无效")
        if episode is not None and not 0 < episode <= 100_000:
            raise ValueError("集号无效")
        if episode is not None and season is None:
            raise ValueError("集号筛选需要季号")
        if media_type == "movie" and (season is not None or episode is not None):
            raise ValueError("电影不接受季集筛选")
        limit = max(1, min(int(limit or 20), 50))
        offset = max(0, int(offset or 0))
        with closing(self.runtime.connect()) as connection:
            where_parts = []
            params = []
            if status:
                where_parts.append("match_status=?")
                params.append(status)
            selected_subscription = self._stored_subscription_key(connection, subscription_id)
            if match_id:
                match_row = connection.execute(
                    "SELECT subscription_key, unit_key FROM rss_subscription_matches WHERE id=?",
                    (match_id,),
                ).fetchone()
                if not match_row:
                    return {
                        "groups": [], "total": 0, "limit": limit, "offset": offset,
                        "counts": {
                            "total": 0,
                            "scoreable_total": 0,
                            **{state: 0 for state in RSS_GROUP_STATES},
                        },
                    }
                where_parts.extend(("subscription_key=?", "unit_key=?"))
                params.extend((match_row["subscription_key"], match_row["unit_key"]))
                selected_subscription = str(match_row["subscription_key"] or "")
            if subscription_id:
                where_parts.append("subscription_key=?")
                params.append(selected_subscription)
            if media_type or season is not None or episode is not None:
                if not selected_subscription:
                    raise ValueError("季集筛选需要订阅 ID")
                if media_type == "movie":
                    where_parts.append("unit_key=?")
                    params.append(f"{selected_subscription}:movie")
                elif season is not None:
                    if episode is not None:
                        where_parts.append("unit_key=?")
                        params.append(f"{selected_subscription}:s{season}:e{episode}")
                    else:
                        prefix = f"{selected_subscription}:s{season}:"
                        where_parts.append("substr(unit_key, 1, length(?))=?")
                        params.extend((prefix, prefix))
            where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
            grouped_query = self._candidate_groups_query(where)
            state_parts = []
            state_params = list(params)
            if group_state:
                state_parts.append("group_state=?")
                state_params.append(group_state)
            if group_scope == "scoreable":
                state_parts.append("group_state<>'needs_cleanup'")
            elif group_scope == "cleanup":
                state_parts.append("group_state='needs_cleanup'")
            state_where = f"WHERE {' AND '.join(state_parts)}" if state_parts else ""
            state_params = tuple(state_params)
            total = int(connection.execute(
                grouped_query + f"SELECT COUNT(*) AS count FROM candidate_groups {state_where}",
                state_params,
            ).fetchone()["count"])
            count_rows = connection.execute(
                grouped_query + "SELECT group_state, COUNT(*) AS count FROM candidate_groups GROUP BY group_state",
                tuple(params),
            ).fetchall()
            state_counts = {state: 0 for state in RSS_GROUP_STATES}
            state_counts.update({str(row["group_state"]): int(row["count"] or 0) for row in count_rows})
            refs = connection.execute(
                grouped_query
                + f"SELECT subscription_key, unit_key, latest_at FROM candidate_groups {state_where} "
                "ORDER BY latest_at DESC, subscription_key, unit_key LIMIT ? OFFSET ?",
                (*state_params, limit, offset),
            ).fetchall()
            if not refs:
                return {
                    "groups": [],
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "counts": {
                        "total": sum(state_counts.values()),
                        "scoreable_total": sum(state_counts.values()) - state_counts["needs_cleanup"],
                        **state_counts,
                    },
                }
            clauses = " OR ".join("(m.subscription_key=? AND m.unit_key=?)" for _ in refs)
            ref_params = [value for row in refs for value in (row["subscription_key"], row["unit_key"])]
            rows = connection.execute(
                "SELECT m.*, i.title AS item_title, i.published_at AS item_published_at "
                "FROM rss_subscription_matches m JOIN rss_items i ON i.id=m.item_id "
                f"WHERE m.archive_state='active' AND ({clauses}) ORDER BY m.created_at DESC, m.id DESC",
                ref_params,
            ).fetchall()
            artifact_keys = sorted({str(row["artifact_key"] or "") for row in rows if row["artifact_key"]})
            ownership_rows = []
            if artifact_keys:
                artifact_marks = ",".join("?" for _ in artifact_keys)
                ownership_rows = connection.execute(
                    "SELECT id, subscription_key, unit_key, artifact_key, target_key, "
                    "evaluation_status, evaluation_reason, archive_state "
                    f"FROM rss_subscription_matches WHERE artifact_key IN ({artifact_marks}) "
                    "ORDER BY created_at DESC, id DESC",
                    artifact_keys,
                ).fetchall()

        grouped = {}
        for row in rows:
            candidate = self._match(row)
            candidate["itemTitle"] = str(row["item_title"] or "")[:240]
            candidate["publishedAt"] = str(row["item_published_at"] or "")
            grouped.setdefault((candidate["subscriptionId"], candidate["unitId"]), []).append(candidate)
        ownership_by_artifact = {}
        for source in ownership_rows:
            row = dict(source)
            archive_state = str(row.get("archive_state") or "active")
            reason_code = str(row.get("evaluation_reason") or "")
            if archive_state == "archived":
                ownership_state = "archived"
            elif reason_code == "subscription_missing":
                ownership_state = "invalid"
            elif str(row.get("evaluation_status") or "") == "blocked":
                ownership_state = "conflict"
            else:
                ownership_state = "valid"
            ownership_by_artifact.setdefault(str(row.get("artifact_key") or ""), []).append({
                "matchId": str(row.get("id") or ""),
                "subscriptionId": str(row.get("subscription_key") or ""),
                "unitId": str(row.get("unit_key") or ""),
                "targetKey": str(row.get("target_key") or ""),
                "state": ownership_state,
                "reasonCode": reason_code,
            })
        groups = []
        for ref in refs:
            key = (ref["subscription_key"], ref["unit_key"])
            candidates = grouped.get(key) or []
            candidates.sort(key=lambda row: (
                0 if row.get("bestCandidate") else 1,
                -float(row.get("candidateScore") or 0),
                str(row.get("createdAt") or ""),
                row["id"],
            ))
            best = next((row for row in candidates if row.get("bestCandidate")), None)
            baseline = next((
                row for row in candidates
                if isinstance(row.get("baselineScore"), (int, float))
                and not isinstance(row.get("baselineScore"), bool)
            ), None)
            ownerships = []
            seen_ownerships = set()
            for artifact_key in {str(row.get("artifactKey") or "") for row in candidates}:
                for ownership in ownership_by_artifact.get(artifact_key, []):
                    identity = (ownership["matchId"], ownership["state"])
                    if identity in seen_ownerships:
                        continue
                    seen_ownerships.add(identity)
                    ownerships.append(ownership)
            groups.append({
                "id": "rss-group:" + hashlib.sha256(
                    f"{key[0]}|{key[1]}".encode("utf-8")
                ).hexdigest()[:24],
                "subscriptionId": key[0],
                "unitId": key[1],
                "title": str((best or (candidates[0] if candidates else {})).get("itemTitle") or "")[:240],
                "episodeLabel": self._candidate_group_episode_label(candidates),
                "state": self._candidate_group_state(candidates),
                "candidateCount": len(candidates),
                "bestMatchId": str((best or {}).get("id") or ""),
                "bestArtifactKey": str((best or {}).get("artifactKey") or ""),
                "bestCandidateScore": (best or {}).get("candidateScore"),
                "baselineScore": (baseline or {}).get("baselineScore"),
                "baselineSummary": (baseline or {}).get("baselineSummary") or {},
                "lastCandidateAt": max((
                    str(row.get("evaluatedAt") or row.get("createdAt") or "")
                    for row in candidates
                ), default=""),
                "ownerships": ownerships,
                "candidates": candidates,
            })
        return {
            "groups": groups,
            "total": total,
            "limit": limit,
            "offset": offset,
            "counts": {
                "total": sum(state_counts.values()),
                "scoreable_total": sum(state_counts.values()) - state_counts["needs_cleanup"],
                **state_counts,
            },
        }

    def resource_center_summary(self, published_from, published_before):
        published_from = str(published_from or "").strip()
        published_before = str(published_before or "").strip()
        if not published_from or not published_before or published_from >= published_before:
            raise ValueError("资源中心统计时间范围无效")
        review_sql = _review_required_sql()
        follow_sql = _follow_link_exists_sql()
        with closing(self.runtime.connect()) as connection:
            item_counts = connection.execute(
                "SELECT "
                "SUM(CASE WHEN COALESCE(NULLIF(i.published_at, ''), i.created_at) >= ? "
                "AND COALESCE(NULLIF(i.published_at, ''), i.created_at) < ? THEN 1 ELSE 0 END) AS new_today, "
                f"SUM(CASE WHEN {review_sql} THEN 1 ELSE 0 END) AS needs_review, "
                f"SUM(CASE WHEN {review_sql} AND {follow_sql} THEN 1 ELSE 0 END) AS follow_needs_review, "
                f"SUM(CASE WHEN NOT {follow_sql} THEN 1 ELSE 0 END) AS unlinked_items "
                "FROM rss_items i",
                (published_from, published_before),
            ).fetchone()
            grouped_query = self._candidate_groups_query()
            upgrade_count = int(connection.execute(
                grouped_query
                + "SELECT COUNT(*) AS count FROM candidate_groups WHERE group_state='upgrade_available'"
            ).fetchone()["count"])
        return {
            "newToday": int(item_counts["new_today"] or 0),
            "needsReview": int(item_counts["needs_review"] or 0),
            "followNeedsReview": int(item_counts["follow_needs_review"] or 0),
            "unlinkedItems": int(item_counts["unlinked_items"] or 0),
            "upgradeAvailable": upgrade_count,
        }

    def find_unique_source_match(self, artifact_keys, subscription_keys, target_key):
        artifacts = sorted({str(value or "").strip() for value in artifact_keys or [] if str(value or "").strip()})
        subscriptions = sorted({str(value or "").strip() for value in subscription_keys or [] if str(value or "").strip()})
        target_key = str(target_key or "").strip()
        if not artifacts or not subscriptions or not target_key:
            return None
        artifacts = artifacts[:100]
        with closing(self.runtime.connect()) as connection:
            stored_subscriptions = sorted({
                self._stored_subscription_key(connection, value) for value in subscriptions
            })
            artifact_marks = ",".join("?" for _ in artifacts)
            subscription_marks = ",".join("?" for _ in stored_subscriptions)
            rows = connection.execute(
                "SELECT id, artifact_key FROM rss_subscription_matches "
                f"WHERE archive_state='active' AND target_key=? AND artifact_key IN ({artifact_marks}) "
                f"AND subscription_key IN ({subscription_marks}) "
                "ORDER BY is_best_candidate DESC, created_at DESC, id DESC",
                (target_key, *artifacts, *stored_subscriptions),
            ).fetchall()
        matched_artifacts = {str(row["artifact_key"] or "") for row in rows if row["artifact_key"]}
        if len(matched_artifacts) != 1 or not rows:
            return None
        return {"matchId": str(rows[0]["id"] or "")}

    def list_pending_evaluation_matches(self, limit=20):
        limit = max(1, min(int(limit or 20), 50))
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM rss_subscription_matches "
                "WHERE archive_state='active' AND match_status='candidate' AND (evaluation_status='pending' "
                "OR (evaluation_status='blocked' AND evaluation_reason IN "
                "('rule_ambiguous', 'version_fields_unconfirmed'))) "
                "ORDER BY created_at, id LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._match(row) for row in rows]

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
