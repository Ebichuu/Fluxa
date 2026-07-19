from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app.sqlite_runtime import SQLiteRuntime


WATCH_STATES = {
    "waiting_first_version",
    "waiting_library_baseline",
    "observing_upgrade",
    "search_due",
    "search_running",
    "target_reached",
    "observation_expired",
    "paused",
    "blocked",
}
ACTION_STATUSES = {
    "claimed",
    "submitted",
    "polling",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
}
TERMINAL_ACTION_STATUSES = {"succeeded", "failed", "cancelled"}


class QualityWatchVersionConflict(RuntimeError):
    pass


class ExternalJobConflict(RuntimeError):
    pass


def _utc_now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value):
    return _as_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _json_dump(value):
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value):
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def make_unit_key(subscription_key, media_type, season_number=None, episode_number=None):
    subscription_key = str(subscription_key or "").strip()
    media_type = str(media_type or "").strip().lower()
    if not subscription_key or media_type not in {"movie", "tv"}:
        raise ValueError("观察单元需要订阅 key 和媒体类型")
    if media_type == "movie":
        return f"{subscription_key}:movie"
    try:
        season = int(season_number)
    except (TypeError, ValueError) as exc:
        raise ValueError("剧集观察单元需要季号") from exc
    if season <= 0:
        raise ValueError("剧集观察单元季号必须大于 0")
    try:
        episode = int(episode_number)
    except (TypeError, ValueError):
        episode = 0
    return f"{subscription_key}:s{season}:e{episode}" if episode > 0 else f"{subscription_key}:s{season}:blocked"


class QualityWatchRepository:
    def __init__(self, database_path, clock=None):
        self.runtime = SQLiteRuntime(database_path)
        self.database_path = self.runtime.database_path
        self.clock = clock or _utc_now
        self.runtime.initialize()
        self.initialize()

    def initialize(self):
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quality_watch_units ("
                "unit_key TEXT PRIMARY KEY, subscription_key TEXT NOT NULL, season_number INTEGER, episode_number INTEGER, "
                "torra_subscription_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, first_success_at TEXT NOT NULL DEFAULT '', "
                "baseline_ready_at TEXT NOT NULL DEFAULT '', window_hours INTEGER NOT NULL, next_check_at TEXT NOT NULL DEFAULT '', "
                "observation_ends_at TEXT NOT NULL DEFAULT '', attempt_count INTEGER NOT NULL DEFAULT 0, "
                "current_offset_index INTEGER NOT NULL DEFAULT 0, current_evidence_json TEXT NOT NULL DEFAULT '{}', "
                "last_result_json TEXT NOT NULL DEFAULT '{}', target_reached_at TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quality_watch_subscription "
                "ON quality_watch_units(subscription_key, season_number, episode_number)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quality_watch_due "
                "ON quality_watch_units(state, next_check_at, observation_ends_at)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_actions ("
                "action_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, subscription_key TEXT NOT NULL, "
                "unit_key TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL, action_type TEXT NOT NULL, status TEXT NOT NULL, "
                "lease_until TEXT NOT NULL DEFAULT '', external_job_id TEXT NOT NULL DEFAULT '', "
                "request_summary_json TEXT NOT NULL DEFAULT '{}', response_summary_json TEXT NOT NULL DEFAULT '{}', "
                "http_status INTEGER NOT NULL DEFAULT 0, error_code TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT NOT NULL DEFAULT '')"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_provider_actions_subscription "
                "ON provider_actions(subscription_key, provider, action_type, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_provider_actions_lease "
                "ON provider_actions(status, lease_until)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduler_state ("
                "state_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL DEFAULT '{}', "
                "updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
            )

    @staticmethod
    def _watch_unit(row):
        if not row:
            return None
        result = dict(row)
        result["current_evidence"] = _json_load(result.pop("current_evidence_json"))
        result["last_result"] = _json_load(result.pop("last_result_json"))
        return result

    @staticmethod
    def _action(row):
        if not row:
            return None
        result = dict(row)
        result["request_summary"] = _json_load(result.pop("request_summary_json"))
        result["response_summary"] = _json_load(result.pop("response_summary_json"))
        return result

    def get_watch_unit(self, unit_key):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM quality_watch_units WHERE unit_key=?", (str(unit_key),)).fetchone()
        return self._watch_unit(row)

    def list_watch_units(self, subscription_key):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM quality_watch_units WHERE subscription_key=? "
                "ORDER BY season_number, episode_number, created_at",
                (str(subscription_key),),
            ).fetchall()
        return [self._watch_unit(row) for row in rows]

    def list_active_watch_units(self, at=None):
        current = _iso(_as_utc(at or self.clock()))
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM quality_watch_units WHERE state IN ('observing_upgrade', 'search_due', 'search_running') "
                "AND baseline_ready_at<>'' AND observation_ends_at>=? ORDER BY subscription_key, season_number, episode_number",
                (current,),
            ).fetchall()
        return [self._watch_unit(row) for row in rows]

    def list_scheduler_watch_units(self):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM quality_watch_units WHERE state IN ('observing_upgrade', 'search_due', 'search_running') "
                "AND baseline_ready_at<>'' AND observation_ends_at<>'' ORDER BY subscription_key, season_number, episode_number, created_at"
            ).fetchall()
        return [self._watch_unit(row) for row in rows]

    def ensure_watch_unit(
        self,
        subscription_key,
        media_type,
        season_number=None,
        episode_number=None,
        first_success_at=None,
        window_hours=48,
        torra_subscription_id="",
    ):
        window_hours = int(window_hours)
        if window_hours not in {24, 48}:
            raise ValueError("追更洗版窗口只允许 24 或 48 小时")
        unit_key = make_unit_key(subscription_key, media_type, season_number, episode_number)
        blocked = str(media_type).lower() == "tv" and unit_key.endswith(":blocked")
        now = _as_utc(self.clock())
        first_success = _as_utc(first_success_at or now)
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO quality_watch_units ("
                "unit_key, subscription_key, season_number, episode_number, torra_subscription_id, state, "
                "first_success_at, window_hours, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(unit_key) DO NOTHING",
                (
                    unit_key,
                    str(subscription_key),
                    int(season_number) if season_number is not None else None,
                    int(episode_number) if episode_number is not None else None,
                    str(torra_subscription_id or ""),
                    "blocked" if blocked else "waiting_library_baseline",
                    _iso(first_success),
                    window_hours,
                    _iso(now),
                    _iso(now),
                ),
            )
        return self.get_watch_unit(unit_key)

    def mark_baseline_ready(self, unit_key, baseline_ready_at=None, offsets_minutes=None):
        now = _as_utc(baseline_ready_at or self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM quality_watch_units WHERE unit_key=?", (str(unit_key),)).fetchone()
            if not row:
                raise KeyError("观察单元不存在")
            if row["baseline_ready_at"]:
                return self._watch_unit(row)
            if row["state"] != "waiting_library_baseline":
                return self._watch_unit(row)
            window_minutes = int(row["window_hours"]) * 60
            offsets = list(offsets_minutes or ([720, 1440] if window_minutes == 1440 else [720, 1440, 2880]))
            offsets = sorted({int(value) for value in offsets if 30 <= int(value) <= window_minutes})
            if not offsets:
                raise ValueError("观察计划至少需要一个有效检查时间点")
            observation_ends = now + timedelta(minutes=window_minutes)
            next_check = now + timedelta(minutes=offsets[0])
            connection.execute(
                "UPDATE quality_watch_units SET baseline_ready_at=?, state='observing_upgrade', next_check_at=?, "
                "observation_ends_at=?, current_offset_index=0, updated_at=?, version=version+1 WHERE unit_key=?",
                (_iso(now), _iso(next_check), _iso(observation_ends), _iso(self.clock()), str(unit_key)),
            )
        return self.get_watch_unit(unit_key)

    def update_watch_unit(self, unit_key, expected_version, **changes):
        columns = {
            "state": lambda value: str(value) if str(value) in WATCH_STATES else None,
            "next_check_at": str,
            "attempt_count": int,
            "current_offset_index": int,
            "current_evidence_json": _json_dump,
            "last_result_json": _json_dump,
            "target_reached_at": str,
            "torra_subscription_id": str,
        }
        assignments = []
        values = []
        for name, value in changes.items():
            if name not in columns:
                raise ValueError(f"不允许更新观察字段：{name}")
            mapped = columns[name](value)
            if mapped is None:
                raise ValueError("观察状态无效")
            assignments.append(f"{name}=?")
            values.append(mapped)
        if not assignments:
            return self.get_watch_unit(unit_key)
        with self.runtime.transaction(immediate=True) as connection:
            cursor = connection.execute(
                f"UPDATE quality_watch_units SET {', '.join(assignments)}, updated_at=?, version=version+1 "
                "WHERE unit_key=? AND version=?",
                (*values, _iso(self.clock()), str(unit_key), int(expected_version)),
            )
            if cursor.rowcount != 1:
                raise QualityWatchVersionConflict("观察单元版本已变化")
        return self.get_watch_unit(unit_key)

    def get_action(self, action_id):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM provider_actions WHERE action_id=?", (str(action_id),)).fetchone()
        return self._action(row)

    def get_action_by_idempotency(self, idempotency_key):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM provider_actions WHERE idempotency_key=?", (str(idempotency_key),)).fetchone()
        return self._action(row)

    def find_inflight_action(self, provider, action_type):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM provider_actions WHERE provider=? AND action_type=? AND status IN ('claimed', 'submitted', 'polling') ORDER BY created_at LIMIT 1",
                (str(provider), str(action_type)),
            ).fetchone()
        return self._action(row)

    def list_unit_actions_since(self, unit_key, provider, action_type, since):
        since_text = _iso(_as_utc(since))
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM provider_actions WHERE unit_key=? AND provider=? AND action_type=? AND created_at>=? ORDER BY created_at",
                (str(unit_key), str(provider), str(action_type), since_text),
            ).fetchall()
        return [self._action(row) for row in rows]

    def _existing_claim(
        self,
        connection,
        row,
        subscription_key,
        unit_key,
        provider,
        action_type,
        now,
        lease_seconds,
    ):
        action = self._action(row)
        if any((
            action["subscription_key"] != subscription_key,
            action["unit_key"] != unit_key,
            action["provider"] != provider,
            action["action_type"] != action_type,
        )):
            return {"disposition": "conflict", "action": action}
        if action["status"] in TERMINAL_ACTION_STATUSES:
            return {"disposition": "replay", "action": action}
        lease_until = _parse_iso(action["lease_until"])
        if lease_until and lease_until > now:
            return {"disposition": "in_progress", "action": action}
        status = "polling" if action["external_job_id"] else "claimed"
        connection.execute(
            "UPDATE provider_actions SET status=?, lease_until=?, updated_at=? WHERE action_id=?",
            (status, _iso(now + timedelta(seconds=lease_seconds)), _iso(now), action["action_id"]),
        )
        updated = connection.execute(
            "SELECT * FROM provider_actions WHERE action_id=?", (action["action_id"],)
        ).fetchone()
        return {"disposition": "resume" if action["external_job_id"] else "reclaimed", "action": self._action(updated)}

    def claim_action(
        self,
        idempotency_key,
        subscription_key,
        provider,
        action_type,
        unit_key="",
        request_summary=None,
        lease_seconds=60,
        cooldown_seconds=0,
        rate_limits=None,
        require_idle=False,
    ):
        values = [str(value or "").strip() for value in (idempotency_key, subscription_key, provider, action_type)]
        if not all(values):
            raise ValueError("外部动作缺少幂等键、订阅、provider 或动作类型")
        idempotency_key, subscription_key, provider, action_type = values
        unit_key = str(unit_key or "").strip()
        now = _as_utc(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM provider_actions WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                return self._existing_claim(
                    connection,
                    existing,
                    subscription_key,
                    unit_key,
                    provider,
                    action_type,
                    now,
                    int(lease_seconds),
                )
            latest = connection.execute(
                "SELECT created_at FROM provider_actions WHERE subscription_key=? AND provider=? AND action_type=? "
                + ("AND unit_key=? " if unit_key else "")
                + "ORDER BY created_at DESC LIMIT 1",
                (subscription_key, provider, action_type, unit_key) if unit_key else (
                    subscription_key, provider, action_type
                ),
            ).fetchone()
            latest_at = _parse_iso(latest["created_at"]) if latest else None
            elapsed = (now - latest_at).total_seconds() if latest_at else None
            if elapsed is not None and elapsed < int(cooldown_seconds):
                remaining = max(1, int(int(cooldown_seconds) - elapsed))
                return {"disposition": "cooldown", "remaining_seconds": remaining, "action": None}
            limits = rate_limits if isinstance(rate_limits, dict) else {}
            if require_idle:
                inflight = connection.execute(
                    "SELECT * FROM provider_actions WHERE provider=? AND action_type=? AND status IN ('claimed', 'submitted', 'polling') ORDER BY created_at LIMIT 1",
                    (provider, action_type),
                ).fetchone()
                if inflight:
                    return {"disposition": "global_busy", "action": self._action(inflight)}
            for window, seconds in (("hourly", 3600), ("daily", 86400)):
                limit = max(0, int(limits.get(window) or 0))
                if not limit:
                    continue
                since = _iso(now - timedelta(seconds=seconds))
                count = int(connection.execute(
                    "SELECT COUNT(*) AS count FROM provider_actions "
                    "WHERE provider=? AND action_type=? AND created_at>=?",
                    (provider, action_type, since),
                ).fetchone()["count"])
                if count >= limit:
                    return {
                        "disposition": "rate_limited",
                        "window": window,
                        "limit": limit,
                        "action": None,
                    }
            action_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO provider_actions ("
                "action_id, idempotency_key, subscription_key, unit_key, provider, action_type, status, lease_until, "
                "request_summary_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?, ?, ?, ?)",
                (
                    action_id,
                    idempotency_key,
                    subscription_key,
                    unit_key,
                    provider,
                    action_type,
                    _iso(now + timedelta(seconds=int(lease_seconds))),
                    _json_dump(request_summary),
                    _iso(now),
                    _iso(now),
                ),
            )
            row = connection.execute("SELECT * FROM provider_actions WHERE action_id=?", (action_id,)).fetchone()
        return {"disposition": "claimed", "action": self._action(row)}

    def save_external_job(self, action_id, external_job_id, status="submitted", lease_seconds=60):
        if status not in {"submitted", "polling"}:
            raise ValueError("外部 job 只能进入 submitted 或 polling")
        external_job_id = str(external_job_id or "").strip()
        if not external_job_id:
            raise ValueError("外部 job ID 不能为空")
        now = _as_utc(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM provider_actions WHERE action_id=?", (str(action_id),)).fetchone()
            if not row:
                raise KeyError("外部动作不存在")
            if row["status"] in TERMINAL_ACTION_STATUSES:
                raise ExternalJobConflict("外部动作已经进入终态")
            if row["external_job_id"] and row["external_job_id"] != external_job_id:
                raise ExternalJobConflict("外部动作已经绑定其他 job ID")
            connection.execute(
                "UPDATE provider_actions SET external_job_id=?, status=?, lease_until=?, updated_at=? WHERE action_id=?",
                (external_job_id, status, _iso(now + timedelta(seconds=lease_seconds)), _iso(now), str(action_id)),
            )
        return self.get_action(action_id)

    def complete_action(
        self,
        action_id,
        status,
        response_summary=None,
        http_status=0,
        error_code="",
        error_message="",
    ):
        if status not in TERMINAL_ACTION_STATUSES:
            raise ValueError("外部动作终态无效")
        now = _as_utc(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM provider_actions WHERE action_id=?", (str(action_id),)
            ).fetchone()
            if not existing:
                raise KeyError("外部动作不存在")
            if existing["status"] in TERMINAL_ACTION_STATUSES:
                if existing["status"] == status:
                    return self._action(existing)
                raise ExternalJobConflict("外部动作已经进入其他终态")
            cursor = connection.execute(
                "UPDATE provider_actions SET status=?, lease_until='', response_summary_json=?, http_status=?, "
                "error_code=?, error_message=?, completed_at=?, updated_at=? WHERE action_id=?",
                (
                    status,
                    _json_dump(response_summary),
                    int(http_status or 0),
                    str(error_code or "")[:120],
                    str(error_message or "")[:240],
                    _iso(now),
                    _iso(now),
                    str(action_id),
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError("外部动作不存在")
        return self.get_action(action_id)

    def get_scheduler_state(self, state_key):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM scheduler_state WHERE state_key=?", (str(state_key),)).fetchone()
        if not row:
            return None
        return {
            "state_key": row["state_key"],
            "payload": _json_load(row["payload_json"]),
            "updated_at": row["updated_at"],
            "version": int(row["version"]),
        }

    def save_scheduler_state(self, state_key, payload, expected_version=None):
        state_key = str(state_key or "").strip()
        if not state_key:
            raise ValueError("调度状态 key 不能为空")
        now = _iso(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT version FROM scheduler_state WHERE state_key=?", (state_key,)
            ).fetchone()
            if existing:
                if expected_version is not None and int(existing["version"]) != int(expected_version):
                    raise QualityWatchVersionConflict("调度状态版本已变化")
                connection.execute(
                    "UPDATE scheduler_state SET payload_json=?, updated_at=?, version=version+1 WHERE state_key=?",
                    (_json_dump(payload), now, state_key),
                )
            else:
                if expected_version not in {None, 0}:
                    raise QualityWatchVersionConflict("调度状态尚不存在")
                connection.execute(
                    "INSERT INTO scheduler_state (state_key, payload_json, updated_at) VALUES (?, ?, ?)",
                    (state_key, _json_dump(payload), now),
                )
        return self.get_scheduler_state(state_key)
