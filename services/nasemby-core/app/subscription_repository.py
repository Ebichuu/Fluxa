from __future__ import annotations

import json
import hashlib
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.sqlite_runtime import SQLiteRuntime


def _now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value, fallback):
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return deepcopy(fallback)
    return parsed


def _candidate_id(media_type, tmdb_id, season_number):
    identity = _json_dump({
        "mediaType": str(media_type or ""),
        "tmdbId": str(tmdb_id or ""),
        "seasonNumber": int(season_number or 0),
    })
    return f"candidate:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _future_text(value, days=30):
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) + timedelta(days=days)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


class SubscriptionRepository:
    def __init__(self, database_path):
        self.runtime = SQLiteRuntime(database_path)
        self.database_path = self.runtime.database_path
        self.initialize()

    def initialize(self):
        self.runtime.initialize()
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS subscription_config ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), payload_json TEXT NOT NULL, "
                "version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS subscription_ledger ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), metadata_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS subscriptions ("
                "subscription_key TEXT PRIMARY KEY, media_type TEXT NOT NULL DEFAULT '', "
                "tmdb_id TEXT NOT NULL DEFAULT '', season_number INTEGER, title TEXT NOT NULL DEFAULT '', "
                "payload_json TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0, "
                "version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_identity ON subscriptions(media_type, tmdb_id, season_number)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_sort ON subscriptions(sort_order, updated_at DESC)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS migration_runs ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, source_fingerprint TEXT NOT NULL UNIQUE, "
                "status TEXT NOT NULL, report_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS torra_subscription_links ("
                "subscription_key TEXT PRIMARY KEY, remote_id TEXT NOT NULL UNIQUE, "
                "origin TEXT NOT NULL, mapping_status TEXT NOT NULL, remote_status_json TEXT NOT NULL, "
                "remote_fingerprint TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_synced_at TEXT NOT NULL, "
                "sync_state TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_torra_subscription_links_state "
                "ON torra_subscription_links(sync_state, updated_at DESC)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS torra_subscription_sync_runs ("
                "idempotency_key TEXT PRIMARY KEY, response_json TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS discover_candidates ("
                "candidate_id TEXT PRIMARY KEY, media_type TEXT NOT NULL, tmdb_id TEXT NOT NULL, "
                "season_number INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL DEFAULT '', "
                "year TEXT NOT NULL DEFAULT '', source_key TEXT NOT NULL DEFAULT '', "
                "state TEXT NOT NULL DEFAULT 'active', payload_json TEXT NOT NULL, "
                "first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, "
                "followed_at TEXT NOT NULL DEFAULT '', follow_idempotency_key TEXT NOT NULL DEFAULT '', "
                "follow_response_json TEXT NOT NULL DEFAULT '{}', "
                "version INTEGER NOT NULL DEFAULT 1, "
                "UNIQUE(media_type, tmdb_id, season_number))"
            )
            candidate_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(discover_candidates)").fetchall()
            }
            if "followed_at" not in candidate_columns:
                connection.execute(
                    "ALTER TABLE discover_candidates ADD COLUMN followed_at TEXT NOT NULL DEFAULT ''"
                )
            if "follow_idempotency_key" not in candidate_columns:
                connection.execute(
                    "ALTER TABLE discover_candidates "
                    "ADD COLUMN follow_idempotency_key TEXT NOT NULL DEFAULT ''"
                )
            if "follow_response_json" not in candidate_columns:
                connection.execute(
                    "ALTER TABLE discover_candidates "
                    "ADD COLUMN follow_response_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_discover_candidates_state "
                "ON discover_candidates(state, last_seen_at DESC)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_discover_candidates_follow_idempotency "
                "ON discover_candidates(follow_idempotency_key) WHERE follow_idempotency_key<>''"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS candidate_migration_runs ("
                "run_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, "
                "preview_fingerprint TEXT NOT NULL, backup_ref TEXT NOT NULL DEFAULT '', "
                "status TEXT NOT NULL, migrated_count INTEGER NOT NULL DEFAULT 0, "
                "skipped_count INTEGER NOT NULL DEFAULT 0, conflict_summary_json TEXT NOT NULL DEFAULT '{}', "
                "compensation_json TEXT NOT NULL DEFAULT '[]', response_json TEXT NOT NULL DEFAULT '{}', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    @staticmethod
    def _identity(item):
        media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
        if media_type in {"电视剧", "series"}:
            media_type = "tv"
        elif media_type in {"电影", "film"}:
            media_type = "movie"
        tmdb_id = str(item.get("tmdb_id") or item.get("tmdbId") or "").strip()
        season = None
        for key in ("target_season", "current_season", "latest_season", "season_number", "season"):
            try:
                value = int(item.get(key))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                season = value
                break
        return media_type, tmdb_id, season, str(item.get("title") or item.get("name") or "").strip()

    def has_config(self):
        with closing(self.runtime.connect()) as connection:
            return connection.execute("SELECT 1 FROM subscription_config WHERE id=1").fetchone() is not None

    def has_items(self):
        with closing(self.runtime.connect()) as connection:
            return connection.execute("SELECT 1 FROM subscriptions LIMIT 1").fetchone() is not None

    def load_config(self):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT payload_json FROM subscription_config WHERE id=1").fetchone()
        return _json_load(row["payload_json"], {}) if row else None

    def save_config(self, payload):
        now = _now_text()
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO subscription_config (id, payload_json, version, updated_at) VALUES (1, ?, 1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json, "
                "version=subscription_config.version+1, updated_at=excluded.updated_at",
                (_json_dump(dict(payload or {})), now),
            )
        return deepcopy(dict(payload or {}))

    def load_payload(self):
        with closing(self.runtime.connect()) as connection:
            ledger = connection.execute("SELECT metadata_json FROM subscription_ledger WHERE id=1").fetchone()
            rows = connection.execute("SELECT payload_json FROM subscriptions ORDER BY sort_order, updated_at DESC").fetchall()
        metadata = _json_load(ledger["metadata_json"], {}) if ledger else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        metadata["items"] = [value for row in rows if isinstance((value := _json_load(row["payload_json"], None)), dict)]
        return metadata

    def save_payload(self, payload, key_resolver):
        source = dict(payload or {})
        items = [dict(item) for item in source.pop("items", []) if isinstance(item, dict)]
        now = _now_text()
        seen = set()
        prepared = []
        for index, item in enumerate(items):
            key = str(key_resolver(item) or "").strip()
            if not key:
                raise ValueError("订阅条目缺少稳定 key")
            if key in seen:
                raise ValueError(f"订阅条目 key 重复：{key}")
            seen.add(key)
            media_type, tmdb_id, season, title = self._identity(item)
            prepared.append((key, media_type, tmdb_id, season, title, _json_dump(item), index, now, now))
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM subscriptions")
            if prepared:
                connection.executemany(
                    "INSERT INTO subscriptions (subscription_key, media_type, tmdb_id, season_number, title, payload_json, sort_order, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    prepared,
                )
            connection.execute(
                "INSERT INTO subscription_ledger (id, metadata_json, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json, updated_at=excluded.updated_at",
                (_json_dump(source), now),
            )
        result = deepcopy(source)
        result["items"] = items
        return result

    def import_legacy(self, config, payload, key_resolver):
        source = dict(payload or {})
        items = [dict(item) for item in source.pop("items", []) if isinstance(item, dict)]
        now = _now_text()
        seen = set()
        prepared = []
        for index, item in enumerate(items):
            key = str(key_resolver(item) or "").strip()
            if not key or key in seen:
                raise ValueError("旧订阅台账包含空 key 或重复 key")
            seen.add(key)
            media_type, tmdb_id, season, title = self._identity(item)
            prepared.append((key, media_type, tmdb_id, season, title, _json_dump(item), index, now, now))
        with self.runtime.transaction(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM subscription_config WHERE id=1").fetchone():
                raise RuntimeError("SQLite 已存在订阅配置，停止旧台账迁移")
            if connection.execute("SELECT 1 FROM subscriptions LIMIT 1").fetchone():
                raise RuntimeError("SQLite 已存在订阅条目，停止旧台账迁移")
            if config is not None:
                connection.execute(
                    "INSERT INTO subscription_config (id, payload_json, version, updated_at) VALUES (1, ?, 1, ?)",
                    (_json_dump(dict(config)), now),
                )
            if prepared:
                connection.executemany(
                    "INSERT INTO subscriptions (subscription_key, media_type, tmdb_id, season_number, title, payload_json, sort_order, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    prepared,
                )
            connection.execute(
                "INSERT INTO subscription_ledger (id, metadata_json, updated_at) VALUES (1, ?, ?)",
                (_json_dump(source), now),
            )
        return len(items)

    def upsert_item(self, item, key):
        row = dict(item or {})
        key = str(key or "").strip()
        if not key:
            raise ValueError("订阅条目缺少稳定 key")
        media_type, tmdb_id, season, title = self._identity(row)
        now = _now_text()
        with self.runtime.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT sort_order, created_at, version, payload_json FROM subscriptions WHERE subscription_key=?", (key,)
            ).fetchone()
            if existing:
                row = {**_json_load(existing["payload_json"], {}), **row}
                media_type, tmdb_id, season, title = self._identity(row)
                connection.execute(
                    "UPDATE subscriptions SET media_type=?, tmdb_id=?, season_number=?, title=?, payload_json=?, "
                    "version=version+1, updated_at=? WHERE subscription_key=?",
                    (media_type, tmdb_id, season, title, _json_dump(row), now, key),
                )
                replaced = True
            else:
                connection.execute("UPDATE subscriptions SET sort_order=sort_order+1")
                connection.execute(
                    "INSERT INTO subscriptions (subscription_key, media_type, tmdb_id, season_number, title, payload_json, sort_order, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (key, media_type, tmdb_id, season, title, _json_dump(row), now, now),
                )
                replaced = False
        return replaced, row

    def supplement_item_visuals(self, key, poster_url="", backdrop_url=""):
        key = str(key or "").strip()
        poster_url = str(poster_url or "").strip()
        backdrop_url = str(backdrop_url or "").strip()
        if not key or (not poster_url and not backdrop_url):
            return None
        with self.runtime.transaction(immediate=True) as connection:
            stored = connection.execute(
                "SELECT payload_json FROM subscriptions WHERE subscription_key=?",
                (key,),
            ).fetchone()
            if not stored:
                return None
            item = _json_load(stored["payload_json"], {})
            changed = False
            if poster_url and not str(item.get("poster_url") or item.get("poster") or "").strip():
                item["poster_url"] = poster_url
                changed = True
            if backdrop_url and not str(item.get("backdrop_url") or "").strip():
                item["backdrop_url"] = backdrop_url
                changed = True
            if changed:
                connection.execute(
                    "UPDATE subscriptions SET payload_json=?, version=version+1 WHERE subscription_key=?",
                    (_json_dump(item), key),
                )
        return item if changed else None

    def mutate_item(self, key, updater, key_resolver):
        key = str(key or "").strip()
        with self.runtime.transaction(immediate=True) as connection:
            stored = connection.execute("SELECT payload_json FROM subscriptions WHERE subscription_key=?", (key,)).fetchone()
            if not stored:
                return None
            item = _json_load(stored["payload_json"], {})
            updater(item)
            next_key = str(key_resolver(item) or key).strip()
            if next_key != key and connection.execute("SELECT 1 FROM subscriptions WHERE subscription_key=?", (next_key,)).fetchone():
                raise ValueError("更新后的订阅 key 已存在")
            media_type, tmdb_id, season, title = self._identity(item)
            connection.execute(
                "UPDATE subscriptions SET subscription_key=?, media_type=?, tmdb_id=?, season_number=?, title=?, payload_json=?, "
                "version=version+1, updated_at=? WHERE subscription_key=?",
                (next_key, media_type, tmdb_id, season, title, _json_dump(item), _now_text(), key),
            )
        return item

    def delete_where(self, predicate):
        with self.runtime.transaction(immediate=True) as connection:
            rows = connection.execute("SELECT subscription_key, payload_json FROM subscriptions ORDER BY sort_order").fetchall()
            removed = []
            for row in rows:
                item = _json_load(row["payload_json"], {})
                if predicate(item):
                    removed.append(item)
                    connection.execute("DELETE FROM subscriptions WHERE subscription_key=?", (row["subscription_key"],))
            remaining = connection.execute("SELECT subscription_key FROM subscriptions ORDER BY sort_order").fetchall()
            for index, row in enumerate(remaining):
                connection.execute("UPDATE subscriptions SET sort_order=? WHERE subscription_key=?", (index, row["subscription_key"]))
        return removed

    def clear_items(self):
        with self.runtime.transaction(immediate=True) as connection:
            count = int(connection.execute("SELECT COUNT(*) AS count FROM subscriptions").fetchone()["count"])
            connection.execute("DELETE FROM subscriptions")
            connection.execute(
                "INSERT INTO subscription_ledger (id, metadata_json, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json, updated_at=excluded.updated_at",
                (_json_dump({"last_run_at": "", "stats": {"total": 0, "movie": 0, "tv": 0}, "errors": []}), _now_text()),
            )
        return count

    def upsert_discover_candidates(self, items, *, observed_at="", expires_at=""):
        source_items = list(items or [])
        now = str(observed_at or _now_text())
        expiry = str(expires_at or _future_text(now))
        prepared = []
        skipped = 0
        seen = set()
        for source in source_items:
            if not isinstance(source, dict):
                skipped += 1
                continue
            item = dict(source)
            media_type, tmdb_id, season, title = self._identity(item)
            if media_type not in {"movie", "tv"} or not tmdb_id.isdigit():
                skipped += 1
                continue
            season_number = int(season or 0) if media_type == "tv" else 0
            candidate_id = _candidate_id(media_type, tmdb_id, season_number)
            if candidate_id in seen:
                skipped += 1
                continue
            seen.add(candidate_id)
            prepared.append({
                "candidate_id": candidate_id,
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "season_number": season_number,
                "title": title,
                "year": str(item.get("year") or "")[:20],
                "source_key": str(item.get("source_key") or item.get("source") or "")[:120],
                "payload_json": _json_dump(item),
            })
        added = 0
        updated = 0
        with self.runtime.transaction(immediate=True) as connection:
            for row in prepared:
                existing = connection.execute(
                    "SELECT state FROM discover_candidates WHERE candidate_id=?",
                    (row["candidate_id"],),
                ).fetchone()
                if existing:
                    connection.execute(
                        "UPDATE discover_candidates SET media_type=?, tmdb_id=?, season_number=?, title=?, "
                        "year=?, source_key=?, payload_json=?, "
                        "state=CASE WHEN state='expired' THEN 'active' ELSE state END, "
                        "last_seen_at=?, expires_at=?, version=version+1 WHERE candidate_id=?",
                        (
                            row["media_type"], row["tmdb_id"], row["season_number"], row["title"],
                            row["year"], row["source_key"], row["payload_json"], now, expiry,
                            row["candidate_id"],
                        ),
                    )
                    updated += 1
                else:
                    connection.execute(
                        "INSERT INTO discover_candidates ("
                        "candidate_id, media_type, tmdb_id, season_number, title, year, source_key, state, "
                        "payload_json, first_seen_at, last_seen_at, expires_at, version"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 1)",
                        (
                            row["candidate_id"], row["media_type"], row["tmdb_id"], row["season_number"],
                            row["title"], row["year"], row["source_key"], row["payload_json"],
                            now, now, expiry,
                        ),
                    )
                    added += 1
        return {
            "scanned": len(source_items),
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "candidateIds": [row["candidate_id"] for row in prepared],
        }

    def expire_discover_candidates(self, *, observed_at=""):
        now = str(observed_at or _now_text())
        with self.runtime.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE discover_candidates SET state='expired', version=version+1 "
                "WHERE state='active' AND expires_at<=?",
                (now,),
            )
        return int(cursor.rowcount or 0)

    def list_discover_candidates(
        self,
        *,
        state="active",
        media_type="",
        query="",
        expires_after="",
        limit=100,
        offset=0,
    ):
        normalized_state = str(state or "").strip()
        normalized_media_type = str(media_type or "").strip().lower()
        normalized_query = str(query or "").strip().casefold()
        clauses = []
        params = []
        if normalized_state:
            clauses.append("state=?")
            params.append(normalized_state)
        if normalized_media_type in {"movie", "tv"}:
            clauses.append("media_type=?")
            params.append(normalized_media_type)
        if normalized_query:
            clauses.append("(LOWER(title) LIKE ? OR tmdb_id=?)")
            params.extend((f"%{normalized_query}%", normalized_query))
        if expires_after:
            clauses.append("expires_at>?")
            params.append(str(expires_after))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self.runtime.connect()) as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) AS count FROM discover_candidates {where}", params
            ).fetchone()["count"])
            rows = connection.execute(
                f"SELECT * FROM discover_candidates {where} "
                "ORDER BY last_seen_at DESC, candidate_id LIMIT ? OFFSET ?",
                (*params, max(1, min(100, int(limit))), max(0, int(offset))),
            ).fetchall()
        return {
            "total": total,
            "items": [{
                **dict(row),
                "payload": _json_load(row["payload_json"], {}),
            } for row in rows],
        }

    def get_discover_candidate(self, candidate_id):
        key = str(candidate_id or "").strip()
        if not key:
            return None
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM discover_candidates WHERE candidate_id=?", (key,)
            ).fetchone()
        return {**dict(row), "payload": _json_load(row["payload_json"], {})} if row else None

    def get_candidate_follow_response(self, idempotency_key):
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT candidate_id, follow_response_json FROM discover_candidates "
                "WHERE follow_idempotency_key=?",
                (key,),
            ).fetchone()
        if not row:
            return None
        return {
            "candidateId": row["candidate_id"],
            "response": _json_load(row["follow_response_json"], {}),
        }

    def record_candidate_follow(self, candidate_id, idempotency_key, response):
        candidate_key = str(candidate_id or "").strip()
        action_key = str(idempotency_key or "").strip()
        if not candidate_key or not action_key:
            raise ValueError("候选确认缺少目标或幂等键")
        now = _now_text()
        with self.runtime.transaction(immediate=True) as connection:
            replay = connection.execute(
                "SELECT candidate_id, follow_response_json FROM discover_candidates "
                "WHERE follow_idempotency_key=?",
                (action_key,),
            ).fetchone()
            if replay:
                if replay["candidate_id"] != candidate_key:
                    raise ValueError("幂等键已用于其他候选")
                return _json_load(replay["follow_response_json"], {}), True
            cursor = connection.execute(
                "UPDATE discover_candidates SET state='followed', followed_at=?, "
                "follow_idempotency_key=?, follow_response_json=?, version=version+1 "
                "WHERE candidate_id=? AND state='active'",
                (now, action_key, _json_dump(dict(response or {})), candidate_key),
            )
            if not cursor.rowcount:
                raise ValueError("候选状态已变化，请重新读取")
        return deepcopy(dict(response or {})), False

    def list_torra_links(self):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM torra_subscription_links ORDER BY updated_at DESC, remote_id"
            ).fetchall()
        return [{
            "subscription_key": row["subscription_key"],
            "remote_id": row["remote_id"],
            "origin": row["origin"],
            "mapping_status": row["mapping_status"],
            "remote_status": _json_load(row["remote_status_json"], {}),
            "remote_fingerprint": row["remote_fingerprint"],
            "last_seen_at": row["last_seen_at"],
            "last_synced_at": row["last_synced_at"],
            "sync_state": row["sync_state"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        } for row in rows]

    def get_torra_link(self, subscription_key="", remote_id=""):
        subscription_key = str(subscription_key or "").strip()
        remote_id = str(remote_id or "").strip()
        if not subscription_key and not remote_id:
            return None
        column, value = ("subscription_key", subscription_key) if subscription_key else ("remote_id", remote_id)
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                f"SELECT * FROM torra_subscription_links WHERE {column}=?", (value,)
            ).fetchone()
        if not row:
            return None
        return next((link for link in self.list_torra_links() if link[column] == value), None)

    def get_torra_sync_run(self, idempotency_key):
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT response_json FROM torra_subscription_sync_runs WHERE idempotency_key=?", (key,)
            ).fetchone()
        return _json_load(row["response_json"], {}) if row else None

    def record_torra_sync_run(self, idempotency_key, response):
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("同步幂等键不能为空")
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO torra_subscription_sync_runs (idempotency_key, response_json, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT(idempotency_key) DO NOTHING",
                (key, _json_dump(dict(response or {})), _now_text()),
            )
        return self.get_torra_sync_run(key)

    def save_torra_link(self, link):
        row = dict(link or {})
        subscription_key = str(row.get("subscription_key") or "").strip()
        remote_id = str(row.get("remote_id") or "").strip()
        if not subscription_key or not remote_id:
            raise ValueError("Torra 关联缺少本地 key 或远端 ID")
        now = _now_text()
        with self.runtime.transaction(immediate=True) as connection:
            conflict = connection.execute(
                "SELECT subscription_key FROM torra_subscription_links WHERE remote_id=?", (remote_id,)
            ).fetchone()
            if conflict and conflict["subscription_key"] != subscription_key:
                raise ValueError("Torra 远端 ID 已关联其他订阅")
            connection.execute(
                "INSERT INTO torra_subscription_links ("
                "subscription_key, remote_id, origin, mapping_status, remote_status_json, remote_fingerprint, "
                "last_seen_at, last_synced_at, sync_state, last_error, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(subscription_key) DO UPDATE SET remote_id=excluded.remote_id, origin=excluded.origin, "
                "mapping_status=excluded.mapping_status, remote_status_json=excluded.remote_status_json, "
                "remote_fingerprint=excluded.remote_fingerprint, last_seen_at=excluded.last_seen_at, "
                "last_synced_at=excluded.last_synced_at, sync_state=excluded.sync_state, "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (
                    subscription_key,
                    remote_id,
                    str(row.get("origin") or "torra_import"),
                    str(row.get("mapping_status") or "mapped"),
                    _json_dump(row.get("remote_status") if isinstance(row.get("remote_status"), dict) else {}),
                    str(row.get("remote_fingerprint") or ""),
                    str(row.get("last_seen_at") or now),
                    str(row.get("last_synced_at") or now),
                    str(row.get("sync_state") or "current"),
                    str(row.get("last_error") or "")[:500],
                    now,
                    now,
                ),
            )
        return self.get_torra_link(subscription_key=subscription_key)

    def _apply_torra_mirror_rows(
        self,
        connection,
        rows,
        key_resolver,
        *,
        import_new,
        mark_missing,
        now,
    ):
        seen_remote_ids = set()
        imported = 0
        updated = 0
        skipped = 0
        for candidate in rows:
            remote_id = str(candidate.get("remote_id") or "").strip()
            item = dict(candidate.get("item") or {})
            if not remote_id or not item:
                skipped += 1
                continue
            seen_remote_ids.add(remote_id)
            linked = connection.execute(
                "SELECT subscription_key FROM torra_subscription_links WHERE remote_id=?", (remote_id,)
            ).fetchone()
            subscription_key = str(
                (linked["subscription_key"] if linked else candidate.get("subscription_key"))
                or key_resolver(item)
                or ""
            ).strip()
            if not subscription_key:
                skipped += 1
                continue
            conflicting_link = connection.execute(
                "SELECT remote_id FROM torra_subscription_links WHERE subscription_key=?", (subscription_key,)
            ).fetchone()
            if conflicting_link and conflicting_link["remote_id"] != remote_id:
                raise ValueError("本地订阅已关联其他 Torra 远端 ID")
            stored = connection.execute(
                "SELECT payload_json, sort_order, created_at FROM subscriptions WHERE subscription_key=?",
                (subscription_key,),
            ).fetchone()
            if not stored and not import_new:
                skipped += 1
                continue
            merged = {**(_json_load(stored["payload_json"], {}) if stored else {}), **item}
            merged["subscription_key"] = subscription_key
            media_type, tmdb_id, season, title = self._identity(merged)
            if stored:
                connection.execute(
                    "UPDATE subscriptions SET media_type=?, tmdb_id=?, season_number=?, title=?, payload_json=?, "
                    "version=version+1, updated_at=? WHERE subscription_key=?",
                    (media_type, tmdb_id, season, title, _json_dump(merged), now, subscription_key),
                )
                updated += 1
            else:
                connection.execute("UPDATE subscriptions SET sort_order=sort_order+1")
                connection.execute(
                    "INSERT INTO subscriptions (subscription_key, media_type, tmdb_id, season_number, title, "
                    "payload_json, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (subscription_key, media_type, tmdb_id, season, title, _json_dump(merged), now, now),
                )
                imported += 1
            connection.execute(
                "INSERT INTO torra_subscription_links ("
                "subscription_key, remote_id, origin, mapping_status, remote_status_json, remote_fingerprint, "
                "last_seen_at, last_synced_at, sync_state, last_error, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', '', ?, ?) "
                "ON CONFLICT(subscription_key) DO UPDATE SET remote_id=excluded.remote_id, "
                "mapping_status=excluded.mapping_status, remote_status_json=excluded.remote_status_json, "
                "remote_fingerprint=excluded.remote_fingerprint, last_seen_at=excluded.last_seen_at, "
                "last_synced_at=excluded.last_synced_at, sync_state='current', last_error='', updated_at=excluded.updated_at",
                (
                    subscription_key,
                    remote_id,
                    str(candidate.get("origin") or "torra_import"),
                    str(candidate.get("mapping_status") or "mapped"),
                    _json_dump(candidate.get("remote_status") if isinstance(candidate.get("remote_status"), dict) else {}),
                    str(candidate.get("remote_fingerprint") or ""),
                    now,
                    now,
                    now,
                    now,
                ),
            )
        if mark_missing:
            if seen_remote_ids:
                placeholders = ",".join("?" for _ in seen_remote_ids)
                connection.execute(
                    f"UPDATE torra_subscription_links SET sync_state='remote_missing', updated_at=? "
                    f"WHERE remote_id NOT IN ({placeholders})",
                    (now, *sorted(seen_remote_ids)),
                )
            else:
                connection.execute(
                    "UPDATE torra_subscription_links SET sync_state='remote_missing', updated_at=?", (now,)
                )
        remote_missing = int(connection.execute(
            "SELECT COUNT(*) AS count FROM torra_subscription_links WHERE sync_state='remote_missing'"
        ).fetchone()["count"])
        return {
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "remoteMissing": remote_missing,
        }

    def apply_torra_mirror(self, candidates, key_resolver, import_new=True, mark_missing=True):
        rows = [dict(candidate) for candidate in (candidates or []) if isinstance(candidate, dict)]
        with self.runtime.transaction(immediate=True) as connection:
            return self._apply_torra_mirror_rows(
                connection,
                rows,
                key_resolver,
                import_new=import_new,
                mark_missing=mark_missing,
                now=_now_text(),
            )

    def apply_torra_mirror_once(self, candidates, key_resolver, idempotency_key, response_builder):
        key = str(idempotency_key or "").strip()
        if not key:
            raise ValueError("同步幂等键不能为空")
        if not callable(response_builder):
            raise TypeError("同步响应构造器无效")
        rows = [dict(candidate) for candidate in (candidates or []) if isinstance(candidate, dict)]
        with self.runtime.transaction(immediate=True) as connection:
            replay = connection.execute(
                "SELECT response_json FROM torra_subscription_sync_runs WHERE idempotency_key=?", (key,)
            ).fetchone()
            if replay:
                return _json_load(replay["response_json"], {}), True
            result = self._apply_torra_mirror_rows(
                connection,
                rows,
                key_resolver,
                import_new=True,
                mark_missing=True,
                now=_now_text(),
            )
            response = dict(response_builder(result) or {})
            connection.execute(
                "INSERT INTO torra_subscription_sync_runs (idempotency_key, response_json, created_at) VALUES (?, ?, ?)",
                (key, _json_dump(response), _now_text()),
            )
            return response, False

    def migration_completed(self, fingerprint):
        with closing(self.runtime.connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM migration_runs WHERE source_fingerprint=? AND status='success'", (fingerprint,)
            ).fetchone() is not None

    def record_migration(self, fingerprint, status, report_path=""):
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO migration_runs (source_fingerprint, status, report_path, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source_fingerprint) DO UPDATE SET status=excluded.status, report_path=excluded.report_path",
                (fingerprint, status, str(report_path or ""), _now_text()),
            )
