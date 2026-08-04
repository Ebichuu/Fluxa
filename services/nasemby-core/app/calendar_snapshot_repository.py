from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.sqlite_runtime import SQLiteRuntime


UTC = timezone.utc
ALLOWED_MEDIA_TYPES = {"all", "movie", "tv"}
VALID_CONFIRMATIONS = {"confirmed", "partial", "unknown"}


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _dump(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value):
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed


def normalize_scope(year, month, media_type="all", include_unlinked=False):
    try:
        year = int(year)
        month = int(month)
    except (TypeError, ValueError) as exc:
        raise ValueError("日历年月无效") from exc
    media_type = str(media_type or "all").strip().lower()
    if not 2000 <= year <= 2100 or not 1 <= month <= 12 or media_type not in ALLOWED_MEDIA_TYPES:
        raise ValueError("日历范围无效")
    include_unlinked = bool(include_unlinked)
    return {
        "year": year,
        "month": month,
        "mediaType": media_type,
        "includeUnlinked": include_unlinked,
        "scopeKey": f"{year:04d}-{month:02d}:{media_type}:{1 if include_unlinked else 0}",
    }


class CalendarRefreshConflict(ValueError):
    pass


class CalendarSnapshotRepository:
    def __init__(self, database_path, clock=None):
        self.runtime = SQLiteRuntime(database_path)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.runtime.initialize()
        self.initialize()

    def initialize(self):
        now_text = _iso(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS calendar_snapshot_cache ("
                "scope_key TEXT PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL, "
                "media_type TEXT NOT NULL, include_unlinked INTEGER NOT NULL DEFAULT 0, "
                "payload_json TEXT NOT NULL DEFAULT '{}', observed_at TEXT NOT NULL DEFAULT '', "
                "fresh_until TEXT NOT NULL DEFAULT '', confirmation TEXT NOT NULL DEFAULT 'unknown', "
                "last_success_at TEXT NOT NULL DEFAULT '', last_attempt_at TEXT NOT NULL DEFAULT '', "
                "last_error_code TEXT NOT NULL DEFAULT '', last_error_text TEXT NOT NULL DEFAULT '', "
                "version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_calendar_snapshot_month "
                "ON calendar_snapshot_cache(year, month, media_type, include_unlinked)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS calendar_snapshot_refresh_queue ("
                "scope_key TEXT PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL, "
                "media_type TEXT NOT NULL, include_unlinked INTEGER NOT NULL DEFAULT 0, "
                "status TEXT NOT NULL DEFAULT 'pending', requested_at TEXT NOT NULL, "
                "last_requested_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '', "
                "finished_at TEXT NOT NULL DEFAULT '', lease_owner TEXT NOT NULL DEFAULT '', "
                "lease_until TEXT NOT NULL DEFAULT '', attempt_count INTEGER NOT NULL DEFAULT 0, "
                "idempotency_key TEXT NOT NULL DEFAULT '', last_error_code TEXT NOT NULL DEFAULT '', "
                "last_error_text TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_calendar_refresh_due "
                "ON calendar_snapshot_refresh_queue(status, lease_until, requested_at)"
            )
            connection.execute(
                "INSERT INTO calendar_snapshot_refresh_queue "
                "(scope_key, year, month, media_type, include_unlinked, status, requested_at, last_requested_at, updated_at) "
                "VALUES ('__bootstrap__', 2000, 1, 'all', 0, 'succeeded', ?, ?, ?) "
                "ON CONFLICT(scope_key) DO NOTHING",
                (now_text, now_text, now_text),
            )
            connection.execute("DELETE FROM calendar_snapshot_refresh_queue WHERE scope_key='__bootstrap__'")
        return self.runtime.database_path

    @staticmethod
    def _cache_row(row, *, now=None):
        if not row:
            return None
        value = dict(row)
        value["payload"] = _load(value.pop("payload_json"))
        value["scopeKey"] = value.pop("scope_key")
        value["mediaType"] = value.pop("media_type")
        value["includeUnlinked"] = bool(value.pop("include_unlinked"))
        value["observedAt"] = value.pop("observed_at")
        value["freshUntil"] = value.pop("fresh_until")
        value["lastSuccessAt"] = value.pop("last_success_at")
        value["lastAttemptAt"] = value.pop("last_attempt_at")
        value["lastErrorCode"] = value.pop("last_error_code")
        value["lastErrorText"] = value.pop("last_error_text")
        value["updatedAt"] = value.pop("updated_at")
        current = _utc(now)
        fresh_until = _parse(value["freshUntil"])
        value["isFresh"] = bool(fresh_until and fresh_until >= current and value["lastSuccessAt"])
        value["effectiveConfirmation"] = value["confirmation"] if value["isFresh"] else (
            "partial" if value["lastSuccessAt"] else "unknown"
        )
        return value

    @staticmethod
    def _queue_row(row):
        if not row:
            return None
        value = dict(row)
        value["scopeKey"] = value.pop("scope_key")
        value["mediaType"] = value.pop("media_type")
        value["includeUnlinked"] = bool(value.pop("include_unlinked"))
        value["requestedAt"] = value.pop("requested_at")
        value["lastRequestedAt"] = value.pop("last_requested_at")
        value["startedAt"] = value.pop("started_at")
        value["finishedAt"] = value.pop("finished_at")
        value["leaseOwner"] = value.pop("lease_owner")
        value["leaseUntil"] = value.pop("lease_until")
        value["attemptCount"] = int(value.pop("attempt_count") or 0)
        value["idempotencyKey"] = value.pop("idempotency_key")
        value["lastErrorCode"] = value.pop("last_error_code")
        value["lastErrorText"] = value.pop("last_error_text")
        value["updatedAt"] = value.pop("updated_at")
        return value

    def get(self, year, month, media_type="all", include_unlinked=False, *, now=None):
        scope = normalize_scope(year, month, media_type, include_unlinked)
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM calendar_snapshot_cache WHERE scope_key=?", (scope["scopeKey"],)
            ).fetchone()
        return self._cache_row(row, now=now or self.clock())

    def get_many(self, scopes, *, now=None):
        normalized = [
            normalize_scope(
                scope.get("year"), scope.get("month"),
                scope.get("mediaType") or scope.get("media_type") or "all",
                scope.get("includeUnlinked", scope.get("include_unlinked", False)),
            ) if isinstance(scope, dict) else scope
            for scope in scopes
        ]
        keys = [scope["scopeKey"] for scope in normalized]
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM calendar_snapshot_cache WHERE scope_key IN ({placeholders})", keys
            ).fetchall()
        return {row["scope_key"]: self._cache_row(row, now=now or self.clock()) for row in rows}

    def request_refresh(self, year, month, media_type="all", include_unlinked=False, *, now=None, idempotency_key=""):
        scope = normalize_scope(year, month, media_type, include_unlinked)
        current = _utc(now or self.clock())
        now_text = _iso(current)
        key = str(idempotency_key or f"calendar-refresh:{scope['scopeKey']}:{uuid.uuid4().hex}")[:180]
        with self.runtime.transaction(immediate=True) as connection:
            duplicate = connection.execute(
                "SELECT * FROM calendar_snapshot_refresh_queue WHERE idempotency_key=? AND scope_key<>?",
                (key, scope["scopeKey"]),
            ).fetchone()
            if duplicate:
                raise CalendarRefreshConflict("刷新幂等键已用于其他日历范围")
            row = connection.execute(
                "SELECT * FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (scope["scopeKey"],)
            ).fetchone()
            if row and row["idempotency_key"] == key and row["status"] in {"pending", "running", "succeeded"}:
                return self._queue_row(row)
            if row and row["status"] == "running":
                lease_until = _parse(row["lease_until"])
                if lease_until and lease_until > current:
                    connection.execute(
                        "UPDATE calendar_snapshot_refresh_queue SET last_requested_at=?, updated_at=? WHERE scope_key=?",
                        (now_text, now_text, scope["scopeKey"]),
                    )
                    row = connection.execute(
                        "SELECT * FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (scope["scopeKey"],)
                    ).fetchone()
                    return self._queue_row(row)
            connection.execute(
                "INSERT INTO calendar_snapshot_refresh_queue ("
                "scope_key, year, month, media_type, include_unlinked, status, requested_at, last_requested_at, "
                "idempotency_key, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET status='pending', requested_at=excluded.requested_at, "
                "last_requested_at=excluded.last_requested_at, idempotency_key=excluded.idempotency_key, "
                "last_error_code='', last_error_text='', updated_at=excluded.updated_at",
                (scope["scopeKey"], scope["year"], scope["month"], scope["mediaType"], int(scope["includeUnlinked"]), now_text, now_text, key, now_text),
            )
            row = connection.execute(
                "SELECT * FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (scope["scopeKey"],)
            ).fetchone()
        return self._queue_row(row)

    def enqueue_due(self, *, now=None, recent_days=30):
        current = _utc(now or self.clock())
        now_text = _iso(current)
        recent_cutoff = _iso(current - timedelta(days=max(1, int(recent_days))))
        with self.runtime.transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT c.year, c.month, c.media_type, c.include_unlinked, c.scope_key "
                "FROM calendar_snapshot_cache c LEFT JOIN calendar_snapshot_refresh_queue q ON q.scope_key=c.scope_key "
                "WHERE (c.fresh_until='' OR c.fresh_until < ?) "
                "AND (COALESCE(q.last_requested_at, '') >= ? OR "
                "(c.year=? AND c.month=? AND c.media_type='all' AND c.include_unlinked=0))",
                (now_text, recent_cutoff, current.astimezone(timezone(timedelta(hours=8))).year, current.astimezone(timezone(timedelta(hours=8))).month),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "INSERT INTO calendar_snapshot_refresh_queue ("
                    "scope_key, year, month, media_type, include_unlinked, status, requested_at, last_requested_at, "
                    "idempotency_key, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?) "
                    "ON CONFLICT(scope_key) DO UPDATE SET status=CASE WHEN status='running' THEN status ELSE 'pending' END, "
                    "updated_at=excluded.updated_at",
                    (row["scope_key"], row["year"], row["month"], row["media_type"], row["include_unlinked"], now_text, now_text, f"calendar-due:{row['scope_key']}", now_text),
                )
        return len(rows)

    def claim_next(self, *, now=None, lease_seconds=120, owner=None):
        current = _utc(now or self.clock())
        now_text = _iso(current)
        lease_until = _iso(current + timedelta(seconds=max(5, int(lease_seconds))))
        owner = str(owner or f"calendar-refresh:{uuid.uuid4().hex}")[:120]
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM calendar_snapshot_refresh_queue WHERE "
                "status='pending' OR (status='running' AND lease_until<>'' AND lease_until < ?) "
                "ORDER BY requested_at, scope_key LIMIT 1", (now_text,)
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE calendar_snapshot_refresh_queue SET status='running', lease_owner=?, lease_until=?, "
                "started_at=?, attempt_count=attempt_count+1, updated_at=? WHERE scope_key=?",
                (owner, lease_until, now_text, now_text, row["scope_key"]),
            )
            row = connection.execute(
                "SELECT * FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (row["scope_key"],)
            ).fetchone()
        return self._queue_row(row)

    def complete_success(self, claim, payload, *, observed_at, fresh_until, confirmation="confirmed", now=None):
        if confirmation not in VALID_CONFIRMATIONS or not isinstance(payload, dict):
            raise ValueError("日历快照结果无效")
        scope_key = str(claim.get("scopeKey") or "")
        current_text = _iso(now or self.clock())
        observed_text = _iso(observed_at)
        fresh_text = _iso(fresh_until)
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT lease_owner FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (scope_key,)
            ).fetchone()
            if not row or row["lease_owner"] != claim.get("leaseOwner"):
                raise CalendarRefreshConflict("日历刷新租约已失效")
            old = connection.execute(
                "SELECT version FROM calendar_snapshot_cache WHERE scope_key=?", (scope_key,)
            ).fetchone()
            version = int(old["version"]) + 1 if old else 1
            connection.execute(
                "INSERT INTO calendar_snapshot_cache (scope_key, year, month, media_type, include_unlinked, payload_json, observed_at, fresh_until, confirmation, last_success_at, last_attempt_at, last_error_code, last_error_text, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET payload_json=excluded.payload_json, observed_at=excluded.observed_at, fresh_until=excluded.fresh_until, confirmation=excluded.confirmation, last_success_at=excluded.last_success_at, last_attempt_at=excluded.last_attempt_at, last_error_code='', last_error_text='', version=excluded.version, updated_at=excluded.updated_at",
                (scope_key, claim["year"], claim["month"], claim["mediaType"], int(claim["includeUnlinked"]), _dump(payload), observed_text, fresh_text, confirmation, observed_text, current_text, version, current_text),
            )
            connection.execute(
                "UPDATE calendar_snapshot_refresh_queue SET status='succeeded', lease_owner='', lease_until='', finished_at=?, last_error_code='', last_error_text='', updated_at=? WHERE scope_key=?",
                (current_text, current_text, scope_key),
            )
        return self.get(claim["year"], claim["month"], claim["mediaType"], claim["includeUnlinked"], now=now)

    def complete_failure(self, claim, error_code, error_text, *, now=None):
        scope_key = str(claim.get("scopeKey") or "")
        current_text = _iso(now or self.clock())
        code = str(error_code or "CALENDAR_REFRESH_FAILED")[:120]
        text = str(error_text or "日历刷新暂时失败")[:240]
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT version, payload_json, last_success_at FROM calendar_snapshot_cache WHERE scope_key=?", (scope_key,)
            ).fetchone()
            queue = connection.execute(
                "SELECT lease_owner FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (scope_key,)
            ).fetchone()
            if not queue or queue["lease_owner"] != claim.get("leaseOwner"):
                raise CalendarRefreshConflict("日历刷新租约已失效")
            if row:
                connection.execute(
                    "UPDATE calendar_snapshot_cache SET confirmation=?, last_attempt_at=?, last_error_code=?, last_error_text=?, version=?, updated_at=? WHERE scope_key=?",
                    ("partial" if row["last_success_at"] else "unknown", current_text, code, text, int(row["version"]) + 1, current_text, scope_key),
                )
            connection.execute(
                "UPDATE calendar_snapshot_refresh_queue SET status='failed', lease_owner='', lease_until='', finished_at=?, last_error_code=?, last_error_text=?, updated_at=? WHERE scope_key=?",
                (current_text, code, text, current_text, scope_key),
            )
        return self.get(claim["year"], claim["month"], claim["mediaType"], claim["includeUnlinked"], now=now)

    def queue_state(self, year, month, media_type="all", include_unlinked=False):
        scope = normalize_scope(year, month, media_type, include_unlinked)
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM calendar_snapshot_refresh_queue WHERE scope_key=?", (scope["scopeKey"],)
            ).fetchone()
        return self._queue_row(row)
