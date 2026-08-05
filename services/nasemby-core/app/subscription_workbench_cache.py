from __future__ import annotations

import json
import threading
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.sqlite_runtime import SQLiteRuntime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


class SubscriptionWorkbenchCacheRepository:
    """追更工作台的最后可靠快照；不保存任何外部凭据或路径。"""

    def __init__(self, database_path, clock=None):
        self.runtime = SQLiteRuntime(database_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.initialize()

    def initialize(self):
        self.runtime.initialize()
        now = _iso(_utc(self.clock()))
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS subscription_workbench_cache ("
                "id INTEGER PRIMARY KEY CHECK(id=1), payload_json TEXT NOT NULL DEFAULT '{}', "
                "generated_at TEXT NOT NULL DEFAULT '', confirmation TEXT NOT NULL DEFAULT 'unknown', "
                "fresh_until TEXT NOT NULL DEFAULT '', stale INTEGER NOT NULL DEFAULT 1, "
                "refresh_state TEXT NOT NULL DEFAULT 'idle', last_error TEXT NOT NULL DEFAULT '', "
                "running INTEGER NOT NULL DEFAULT 0, lease_token TEXT NOT NULL DEFAULT '', "
                "lease_until TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO subscription_workbench_cache "
                "(id, payload_json, generated_at, confirmation, fresh_until, stale, refresh_state, last_error, updated_at) "
                "VALUES (1, '{}', '', 'unknown', '', 1, 'idle', '', ?) ON CONFLICT(id) DO NOTHING",
                (now,),
            )

    @staticmethod
    def _decode(row):
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        return {
            "payload": payload if isinstance(payload, dict) else {},
            "generatedAt": row["generated_at"],
            "confirmation": row["confirmation"],
            "freshUntil": row["fresh_until"],
            "stale": bool(row["stale"]),
            "refreshState": row["refresh_state"],
            "lastError": row["last_error"],
            "running": bool(row["running"]),
            "version": int(row["version"] or 0),
            "updatedAt": row["updated_at"],
        }

    def get(self, *, now=None):
        now = _utc(now or self.clock())
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM subscription_workbench_cache WHERE id=1").fetchone()
        value = self._decode(row)
        if not value:
            return None
        fresh_until = _parse(value["freshUntil"])
        value["stale"] = bool(value["stale"] or not fresh_until or fresh_until <= now)
        if value["stale"] and value["refreshState"] == "idle":
            value["refreshState"] = "stale"
        return value

    def claim_refresh(self, *, now=None, lease_seconds=120):
        now = _utc(now or self.clock())
        token = f"subscription-workbench:{uuid.uuid4().hex}"
        now_text = _iso(now)
        lease_text = _iso(now + timedelta(seconds=max(5, int(lease_seconds))))
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT running, lease_until, version FROM subscription_workbench_cache WHERE id=1").fetchone()
            lease_until = _parse(row["lease_until"] if row else "")
            if row and row["running"] and lease_until and lease_until > now:
                return None
            version = int(row["version"] or 0) + 1 if row else 1
            connection.execute(
                "INSERT INTO subscription_workbench_cache "
                "(id, running, lease_token, lease_until, refresh_state, version, updated_at) "
                "VALUES (1, 1, ?, ?, 'refreshing', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET running=1, lease_token=excluded.lease_token, "
                "lease_until=excluded.lease_until, refresh_state='refreshing', version=excluded.version, updated_at=excluded.updated_at",
                (token, lease_text, version, now_text),
            )
        return token

    def write_success(self, payload, *, now=None, fresh_seconds=90, confirmation="confirmed", token=""):
        now = _utc(now or self.clock())
        now_text = _iso(now)
        fresh_text = _iso(now + timedelta(seconds=max(30, int(fresh_seconds))))
        data = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False, separators=(",", ":"))
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT version, lease_token FROM subscription_workbench_cache WHERE id=1").fetchone()
            if token and (not row or row["lease_token"] != token):
                raise RuntimeError("追更工作台刷新租约已失效")
            version = int(row["version"] or 0) + 1 if row else 1
            connection.execute(
                "INSERT INTO subscription_workbench_cache "
                "(id, payload_json, generated_at, confirmation, fresh_until, stale, refresh_state, last_error, running, lease_token, lease_until, version, updated_at) "
                "VALUES (1, ?, ?, ?, ?, 0, 'idle', '', 0, '', '', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json, generated_at=excluded.generated_at, "
                "confirmation=excluded.confirmation, fresh_until=excluded.fresh_until, stale=0, refresh_state='idle', "
                "last_error='', running=0, lease_token='', lease_until='', version=excluded.version, updated_at=excluded.updated_at",
                (data, now_text, str(confirmation or "confirmed"), fresh_text, version, now_text),
            )

    def write_failure(self, error, *, now=None, token=""):
        now_text = _iso(_utc(now or self.clock()))
        public_error = "追更工作台更新暂时失败"
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT version, payload_json, lease_token FROM subscription_workbench_cache WHERE id=1").fetchone()
            if token and (not row or row["lease_token"] != token):
                return False
            version = int(row["version"] or 0) + 1 if row else 1
            connection.execute(
                "INSERT INTO subscription_workbench_cache "
                "(id, payload_json, stale, refresh_state, last_error, running, lease_token, lease_until, version, updated_at) "
                "VALUES (1, '{}', 1, 'failed', ?, 0, '', '', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET stale=1, "
                "refresh_state='failed', last_error=?, running=0, lease_token='', lease_until='', version=?, updated_at=?",
                (public_error, version, now_text, public_error, version, now_text),
            )
        return True

    def finish_refresh(self, token, *, error="", now=None):
        now_text = _iso(_utc(now or self.clock()))
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT lease_token FROM subscription_workbench_cache WHERE id=1").fetchone()
            if not row or row["lease_token"] != token:
                return False
            connection.execute(
                "UPDATE subscription_workbench_cache SET running=0, lease_token='', lease_until='', "
                "refresh_state=?, last_error=?, updated_at=? WHERE id=1",
                ("failed" if error else "idle", str(error or "")[:240], now_text),
            )
        return True


class SubscriptionWorkbenchRefreshRuntime:
    def __init__(self, repository, collector, clock=None, lease_seconds=120):
        self.repository = repository
        self.collector = collector
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = lease_seconds
        self._lock = threading.Lock()

    def run_once(self):
        if not self._lock.acquire(blocking=False):
            return {"status": "already_running", "ran": False}
        started_at = _utc(self.clock())
        token = None
        try:
            token = self.repository.claim_refresh(now=started_at, lease_seconds=self.lease_seconds)
            if not token:
                return {"status": "already_running", "ran": False}
            payload = self.collector.live_snapshot()
            completed_at = _utc(self.clock())
            self.repository.write_success(payload, now=completed_at, confirmation="confirmed", token=token)
            token = None
            return {"status": "success", "ran": True}
        except Exception as exc:
            if token:
                try:
                    self.repository.write_failure(
                        "追更工作台更新暂时失败",
                        now=_utc(self.clock()),
                        token=token,
                    )
                except Exception:
                    pass
            return {"status": "failed", "ran": True, "errorCode": type(exc).__name__}
        finally:
            self._lock.release()
