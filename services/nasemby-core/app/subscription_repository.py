from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone

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
            if config:
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
