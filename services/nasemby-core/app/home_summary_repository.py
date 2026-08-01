from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.sqlite_runtime import SQLiteRuntime


MODULE_KEYS = (
    "task_pipeline",
    "qb_activity",
    "archive_today",
    "secupload",
    "subscription_progress",
    "rss_resource_center",
    "service_health",
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _json_dump(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value):
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}


def _validate_module(module_key: str) -> str:
    value = str(module_key or "").strip()
    if value not in MODULE_KEYS:
        raise ValueError(f"unsupported home summary module: {value}")
    return value


def _validate_scope(scope_key: str) -> str:
    value = str(scope_key or "").strip()
    if not value:
        raise ValueError("home summary scope is required")
    return value


class HomeSummaryRepository:
    def __init__(self, database_path, clock=None):
        self.runtime = SQLiteRuntime(database_path)
        self.database_path = self.runtime.database_path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.initialize()

    def initialize(self):
        self.runtime.initialize()
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS home_summary_module_cache ("
                "module_key TEXT NOT NULL, scope_key TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', "
                "observed_at TEXT NOT NULL DEFAULT '', fresh_until TEXT NOT NULL DEFAULT '', "
                "confirmation TEXT NOT NULL DEFAULT 'unknown', last_success_at TEXT NOT NULL DEFAULT '', "
                "last_attempt_at TEXT NOT NULL DEFAULT '', last_error_code TEXT NOT NULL DEFAULT '', "
                "last_error_text TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, "
                "updated_at TEXT NOT NULL, PRIMARY KEY(module_key, scope_key))"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_home_summary_cache_scope "
                "ON home_summary_module_cache(scope_key, module_key)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS home_summary_refresh_state ("
                "id INTEGER PRIMARY KEY CHECK(id=1), running INTEGER NOT NULL DEFAULT 0, "
                "lease_token TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '', "
                "started_at TEXT NOT NULL DEFAULT '', finished_at TEXT NOT NULL DEFAULT '', "
                "last_error_code TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, "
                "updated_at TEXT NOT NULL)"
            )
            now_text = _iso(_as_utc(self.clock()))
            connection.execute(
                "INSERT INTO home_summary_refresh_state "
                "(id, running, lease_token, lease_until, started_at, finished_at, last_error_code, version, updated_at) "
                "VALUES (1, 0, '', '', '', '', '', 1, ?) ON CONFLICT(id) DO NOTHING",
                (now_text,),
            )
        return self.database_path

    @staticmethod
    def _cache_row(row):
        if not row:
            return None
        value = dict(row)
        value["payload"] = _json_load(value.pop("payload_json"))
        value["moduleKey"] = value.pop("module_key")
        value["scopeKey"] = value.pop("scope_key")
        value["observedAt"] = value.pop("observed_at")
        value["freshUntil"] = value.pop("fresh_until")
        value["lastSuccessAt"] = value.pop("last_success_at")
        value["lastAttemptAt"] = value.pop("last_attempt_at")
        value["lastErrorCode"] = value.pop("last_error_code")
        value["lastErrorText"] = value.pop("last_error_text")
        value["updatedAt"] = value.pop("updated_at")
        return value

    @staticmethod
    def _refresh_row(row):
        if not row:
            return {
                "running": False,
                "leaseToken": "",
                "leaseUntil": "",
                "startedAt": "",
                "finishedAt": "",
                "lastErrorCode": "",
                "version": 0,
                "updatedAt": "",
            }
        value = dict(row)
        return {
            "running": bool(value["running"]),
            "leaseToken": value["lease_token"],
            "leaseUntil": value["lease_until"],
            "startedAt": value["started_at"],
            "finishedAt": value["finished_at"],
            "lastErrorCode": value["last_error_code"],
            "version": int(value["version"]),
            "updatedAt": value["updated_at"],
        }

    def get(self, module_key: str, scope_key: str):
        module_key = _validate_module(module_key)
        scope_key = _validate_scope(scope_key)
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM home_summary_module_cache WHERE module_key=? AND scope_key=?",
                (module_key, scope_key),
            ).fetchone()
        return self._cache_row(row)

    def get_many(self, scopes: dict[str, str] | None = None):
        scopes = scopes or {}
        for module_key in scopes:
            _validate_module(module_key)
            _validate_scope(scopes[module_key])
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute("SELECT * FROM home_summary_module_cache").fetchall()
        values = {}
        for row in rows:
            item = self._cache_row(row)
            if scopes and scopes.get(item["moduleKey"]) != item["scopeKey"]:
                continue
            values[(item["moduleKey"], item["scopeKey"])] = item
        return values

    def write_success(
        self, module_key: str, scope_key: str, payload, *, observed_at: datetime,
        fresh_until: datetime, confirmation: str = "confirmed", now: datetime | None = None,
    ):
        module_key = _validate_module(module_key)
        scope_key = _validate_scope(scope_key)
        confirmation = str(confirmation or "confirmed").strip().lower()
        if confirmation not in {"confirmed", "partial", "unknown"}:
            raise ValueError("invalid home summary confirmation")
        now_text = _iso(_as_utc(now or self.clock()))
        observed_text = _iso(_as_utc(observed_at))
        fresh_text = _iso(_as_utc(fresh_until))
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT version FROM home_summary_module_cache WHERE module_key=? AND scope_key=?",
                (module_key, scope_key),
            ).fetchone()
            version = int(row["version"]) + 1 if row else 1
            connection.execute(
                "INSERT INTO home_summary_module_cache ("
                "module_key, scope_key, payload_json, observed_at, fresh_until, confirmation, "
                "last_success_at, last_attempt_at, last_error_code, last_error_text, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?) "
                "ON CONFLICT(module_key, scope_key) DO UPDATE SET payload_json=excluded.payload_json, "
                "observed_at=excluded.observed_at, fresh_until=excluded.fresh_until, "
                "confirmation=excluded.confirmation, last_success_at=excluded.last_success_at, "
                "last_attempt_at=excluded.last_attempt_at, last_error_code='', last_error_text='', "
                "version=excluded.version, updated_at=excluded.updated_at",
                (module_key, scope_key, _json_dump(payload), observed_text, fresh_text, confirmation, now_text, now_text, version, now_text),
            )
        return self.get(module_key, scope_key)

    def write_failure(
        self, module_key: str, scope_key: str, error_code: str, error_text: str,
        *, now: datetime | None = None,
    ):
        module_key = _validate_module(module_key)
        scope_key = _validate_scope(scope_key)
        now_text = _iso(_as_utc(now or self.clock()))
        code = str(error_code or "HOME_SUMMARY_MODULE_FAILED").strip()[:120]
        text = str(error_text or "模块暂时无法确认").strip()[:240]
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT version, payload_json FROM home_summary_module_cache WHERE module_key=? AND scope_key=?",
                (module_key, scope_key),
            ).fetchone()
            version = int(row["version"]) + 1 if row else 1
            confirmation = "partial" if row and _json_load(row["payload_json"]) not in ({}, None) else "unknown"
            connection.execute(
                "INSERT INTO home_summary_module_cache ("
                "module_key, scope_key, payload_json, observed_at, fresh_until, confirmation, "
                "last_success_at, last_attempt_at, last_error_code, last_error_text, version, updated_at) "
                "VALUES (?, ?, '{}', '', '', ?, '', ?, ?, ?, ?, ?) "
                "ON CONFLICT(module_key, scope_key) DO UPDATE SET confirmation=excluded.confirmation, "
                "last_attempt_at=excluded.last_attempt_at, last_error_code=excluded.last_error_code, "
                "last_error_text=excluded.last_error_text, version=excluded.version, updated_at=excluded.updated_at",
                (module_key, scope_key, confirmation, now_text, code, text, version, now_text),
            )
        return self.get(module_key, scope_key)

    def refresh_state(self):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM home_summary_refresh_state WHERE id=1").fetchone()
        return self._refresh_row(row)

    def claim_refresh(self, *, now: datetime | None = None, lease_seconds: int = 120, token: str | None = None):
        current = _as_utc(now or self.clock())
        now_text = _iso(current)
        lease_until = _iso(current + timedelta(seconds=max(1, int(lease_seconds))))
        token = str(token or f"home-refresh:{uuid.uuid4().hex}")
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM home_summary_refresh_state WHERE id=1").fetchone()
            current_state = self._refresh_row(row)
            active_until = _parse_iso(current_state["leaseUntil"])
            if current_state["running"] and active_until and active_until > current:
                return None
            version = int(current_state["version"]) + 1
            connection.execute(
                "UPDATE home_summary_refresh_state SET running=1, lease_token=?, lease_until=?, "
                "started_at=?, last_error_code='', version=?, updated_at=? WHERE id=1",
                (token, lease_until, now_text, version, now_text),
            )
        return token

    def finish_refresh(self, token: str, *, now: datetime | None = None, error_code: str = "") -> bool:
        token = str(token or "")
        if not token:
            return False
        now_text = _iso(_as_utc(now or self.clock()))
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT version, lease_token FROM home_summary_refresh_state WHERE id=1"
            ).fetchone()
            if not row or row["lease_token"] != token:
                return False
            connection.execute(
                "UPDATE home_summary_refresh_state SET running=0, lease_token='', lease_until='', "
                "finished_at=?, last_error_code=?, version=?, updated_at=? WHERE id=1",
                (now_text, str(error_code or "").strip()[:120], int(row["version"]) + 1, now_text),
            )
        return True

