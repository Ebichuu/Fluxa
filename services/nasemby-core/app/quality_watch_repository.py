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
DEFAULT_LIFECYCLE_MODE = "follow_rss"
WATCH_LIFECYCLE_MODES = {DEFAULT_LIFECYCLE_MODE, "fixed_window"}
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
BRIDGE_MODES = {"off", "shadow", "apply"}
BRIDGE_RECEIPT_STATUSES = {
    "pending", "applied", "historical", "needs_review", "rejected", "retryable_failed",
}
WATCH_CANDIDATE_COLUMNS = {
    "baseline_artifact_key": "TEXT NOT NULL DEFAULT ''",
    "baseline_score": "REAL",
    "baseline_rule_hash": "TEXT NOT NULL DEFAULT ''",
    "best_match_id": "TEXT NOT NULL DEFAULT ''",
    "best_candidate_score": "REAL",
    "upgrade_count": "INTEGER NOT NULL DEFAULT 0",
    "last_candidate_at": "TEXT NOT NULL DEFAULT ''",
    "lifecycle_mode": "TEXT NOT NULL DEFAULT 'follow_rss'",
}


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


def _lifecycle_mode(value):
    mode = str(value or "").strip().lower()
    if mode not in WATCH_LIFECYCLE_MODES:
        raise ValueError("观察模式只允许 follow_rss 或 fixed_window")
    return mode


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
                "baseline_artifact_key TEXT NOT NULL DEFAULT '', baseline_score REAL, "
                "baseline_rule_hash TEXT NOT NULL DEFAULT '', best_match_id TEXT NOT NULL DEFAULT '', "
                "best_candidate_score REAL, upgrade_count INTEGER NOT NULL DEFAULT 0, "
                "last_candidate_at TEXT NOT NULL DEFAULT '', lifecycle_mode TEXT NOT NULL DEFAULT 'follow_rss', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
            )
            watch_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(quality_watch_units)").fetchall()
            }
            for name, definition in WATCH_CANDIDATE_COLUMNS.items():
                if name not in watch_columns:
                    connection.execute(
                        f"ALTER TABLE quality_watch_units ADD COLUMN {name} {definition}"
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
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quality_watch_bridge_state ("
                "singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), bridge_version TEXT NOT NULL, "
                "mode TEXT NOT NULL, activated_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quality_watch_bridge_receipts ("
                "receipt_id TEXT PRIMARY KEY, receipt_key TEXT NOT NULL UNIQUE, bridge_version TEXT NOT NULL, "
                "stage TEXT NOT NULL, fact_type TEXT NOT NULL, owner_target_key TEXT NOT NULL, "
                "artifact_key TEXT NOT NULL, source_result_ref TEXT NOT NULL DEFAULT '', "
                "upstream_occurred_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT '', "
                "attempt_count INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT NOT NULL DEFAULT '', "
                "next_retry_at TEXT NOT NULL DEFAULT '', evidence_version TEXT NOT NULL DEFAULT '', "
                "ownership_version TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quality_watch_bridge_receipts_status "
                "ON quality_watch_bridge_receipts(bridge_version, status, updated_at)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quality_watch_baseline_init_runs ("
                "run_id TEXT PRIMARY KEY, status TEXT NOT NULL, preview_fingerprint TEXT NOT NULL, "
                "bridge_version TEXT NOT NULL, policy_version TEXT NOT NULL, idempotency_key TEXT NOT NULL DEFAULT '', "
                "selected_target_count INTEGER NOT NULL DEFAULT 0, preview_json TEXT NOT NULL DEFAULT '{}', "
                "response_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "completed_at TEXT NOT NULL DEFAULT '')"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_watch_baseline_init_idempotency "
                "ON quality_watch_baseline_init_runs(idempotency_key) WHERE idempotency_key<>''"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS quality_watch_baseline_init_items ("
                "run_id TEXT NOT NULL, public_target_id TEXT NOT NULL, owner_target_key TEXT NOT NULL, "
                "artifact_ref TEXT NOT NULL, season_number INTEGER NOT NULL, episode_number INTEGER NOT NULL, "
                "evidence_source TEXT NOT NULL, first_success_at TEXT NOT NULL, baseline_ready_at TEXT NOT NULL, "
                "result TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT '', "
                "PRIMARY KEY(run_id, public_target_id), "
                "FOREIGN KEY(run_id) REFERENCES quality_watch_baseline_init_runs(run_id) ON DELETE CASCADE)"
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

    @staticmethod
    def _bridge_state(row):
        if not row:
            return {
                "bridgeVersion": "1", "mode": "off", "activatedAt": "",
                "createdAt": "", "updatedAt": "", "version": 0,
            }
        value = dict(row)
        return {
            "bridgeVersion": value["bridge_version"],
            "mode": value["mode"],
            "activatedAt": value["activated_at"],
            "createdAt": value["created_at"],
            "updatedAt": value["updated_at"],
            "version": int(value["version"]),
        }

    @staticmethod
    def _bridge_receipt(row):
        if not row:
            return None
        return dict(row)

    def get_bridge_state(self):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM quality_watch_bridge_state WHERE singleton_id=1"
            ).fetchone()
        return self._bridge_state(row)

    def set_bridge_mode(self, mode, *, bridge_version="1"):
        mode = str(mode or "").strip().lower()
        if mode not in BRIDGE_MODES:
            raise ValueError("bridge mode must be off, shadow, or apply")
        now_text = _iso(_as_utc(self.clock()))
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM quality_watch_bridge_state WHERE singleton_id=1"
            ).fetchone()
            if not row:
                if mode == "apply":
                    raise ValueError("shadow mode must be enabled before apply")
                activated_at = now_text if mode == "shadow" else ""
                connection.execute(
                    "INSERT INTO quality_watch_bridge_state ("
                    "singleton_id, bridge_version, mode, activated_at, created_at, updated_at) "
                    "VALUES (1, ?, ?, ?, ?, ?)",
                    (str(bridge_version), mode, activated_at, now_text, now_text),
                )
            else:
                activated_at = row["activated_at"] or (now_text if mode == "shadow" else "")
                if mode == "apply" and not activated_at:
                    raise ValueError("shadow mode must be enabled before apply")
                connection.execute(
                    "UPDATE quality_watch_bridge_state SET bridge_version=?, mode=?, activated_at=?, updated_at=?, "
                    "version=version+1 WHERE singleton_id=1",
                    (str(bridge_version), mode, activated_at, now_text),
                )
            current = connection.execute(
                "SELECT * FROM quality_watch_bridge_state WHERE singleton_id=1"
            ).fetchone()
        return self._bridge_state(current)

    def get_bridge_receipt_in_connection(self, connection, receipt_key):
        return self._bridge_receipt(connection.execute(
            "SELECT * FROM quality_watch_bridge_receipts WHERE receipt_key=?",
            (str(receipt_key),),
        ).fetchone())

    def upsert_bridge_receipt(self, connection, receipt, status, reason_code="", *, retry=False):
        status = str(status or "")
        if status not in BRIDGE_RECEIPT_STATUSES:
            raise ValueError("bridge receipt status invalid")
        now_text = _iso(_as_utc(self.clock()))
        existing = self.get_bridge_receipt_in_connection(connection, receipt["receipt_key"])
        if existing and existing["status"] in {"applied", "historical", "rejected"}:
            return existing
        attempts = int(existing["attempt_count"] if existing else 0) + (1 if retry else 0)
        connection.execute(
            "INSERT INTO quality_watch_bridge_receipts ("
            "receipt_id, receipt_key, bridge_version, stage, fact_type, owner_target_key, artifact_key, "
            "source_result_ref, upstream_occurred_at, status, reason_code, attempt_count, last_attempt_at, "
            "next_retry_at, evidence_version, ownership_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(receipt_key) DO UPDATE SET status=excluded.status, reason_code=excluded.reason_code, "
            "attempt_count=excluded.attempt_count, last_attempt_at=excluded.last_attempt_at, "
            "next_retry_at=excluded.next_retry_at, evidence_version=excluded.evidence_version, "
            "ownership_version=excluded.ownership_version, updated_at=excluded.updated_at",
            (
                receipt["receipt_id"], receipt["receipt_key"], receipt["bridge_version"],
                receipt["stage"], receipt["fact_type"], receipt["owner_target_key"],
                receipt["artifact_key"], receipt.get("source_result_ref", ""),
                receipt.get("upstream_occurred_at", ""), status, str(reason_code or ""), attempts,
                now_text, receipt.get("next_retry_at", ""), receipt.get("evidence_version", ""),
                receipt.get("ownership_version", ""), existing["created_at"] if existing else now_text, now_text,
            ),
        )
        return self.get_bridge_receipt_in_connection(connection, receipt["receipt_key"])

    def record_bridge_retryable_failure(self, receipt, reason_code, *, next_retry_at=""):
        value = {**receipt, "next_retry_at": str(next_retry_at or "")}
        with self.runtime.transaction(immediate=True) as connection:
            return self.upsert_bridge_receipt(
                connection, value, "retryable_failed", reason_code, retry=True
            )

    def list_bridge_receipts(self, *, status="", limit=1000):
        where = " WHERE status=?" if status else ""
        params = (str(status), max(1, min(int(limit), 5000))) if status else (max(1, min(int(limit), 5000)),)
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM quality_watch_bridge_receipts{where} ORDER BY created_at, receipt_id LIMIT ?",
                params,
            ).fetchall()
        return [self._bridge_receipt(row) for row in rows]

    def summarize_bridge_receipts(self, bridge_version="1"):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count, MAX(updated_at) AS last_updated_at "
                "FROM quality_watch_bridge_receipts WHERE bridge_version=? GROUP BY status",
                (str(bridge_version),),
            ).fetchall()
        counts = {status: 0 for status in BRIDGE_RECEIPT_STATUSES}
        last_receipt_at = ""
        for row in rows:
            counts[row["status"]] = int(row["count"] or 0)
            last_receipt_at = max(last_receipt_at, str(row["last_updated_at"] or ""))
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "lastReceiptAt": last_receipt_at,
        }

    @staticmethod
    def _baseline_init_run(row):
        if not row:
            return None
        value = dict(row)
        value["preview"] = _json_load(value.pop("preview_json"))
        value["response"] = _json_load(value.pop("response_json"))
        return value

    def create_baseline_init_preview(
        self, run_id, preview_fingerprint, bridge_version, policy_version, preview
    ):
        now_text = _iso(_as_utc(self.clock()))
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO quality_watch_baseline_init_runs ("
                "run_id, status, preview_fingerprint, bridge_version, policy_version, preview_json, "
                "created_at, updated_at) VALUES (?, 'previewed', ?, ?, ?, ?, ?, ?)",
                (
                    str(run_id), str(preview_fingerprint), str(bridge_version), str(policy_version),
                    _json_dump(preview), now_text, now_text,
                ),
            )
        return self.get_baseline_init_run(run_id)

    def get_baseline_init_run(self, run_id="", *, idempotency_key=""):
        column, value = (
            ("idempotency_key", idempotency_key) if idempotency_key else ("run_id", run_id)
        )
        with closing(self.runtime.connect()) as connection:
            row = connection.execute(
                f"SELECT * FROM quality_watch_baseline_init_runs WHERE {column}=?",
                (str(value),),
            ).fetchone()
        return self._baseline_init_run(row)

    def list_baseline_init_items(self, run_id):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT public_target_id, artifact_ref, season_number, episode_number, "
                "evidence_source, first_success_at, baseline_ready_at, result, reason_code "
                "FROM quality_watch_baseline_init_items WHERE run_id=? "
                "ORDER BY season_number, episode_number, public_target_id",
                (str(run_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_baseline_init_run(self, run_id, status, response=None):
        if status not in {"previewed", "applied", "stale", "failed"}:
            raise ValueError("baseline initialization status invalid")
        now_text = _iso(_as_utc(self.clock()))
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE quality_watch_baseline_init_runs SET status=?, response_json=?, updated_at=?, "
                "completed_at=CASE WHEN ?='previewed' THEN '' ELSE ? END WHERE run_id=?",
                (status, _json_dump(response or {}), now_text, status, now_text, str(run_id)),
            )
        return self.get_baseline_init_run(run_id)

    def apply_baseline_init_run(
        self, connection, run_id, idempotency_key, selected_target_ids, plan_items, response, *, now=None
    ):
        now_text = _iso(_as_utc(now or self.clock()))
        run = connection.execute(
            "SELECT * FROM quality_watch_baseline_init_runs WHERE run_id=?",
            (str(run_id),),
        ).fetchone()
        if not run or run["status"] != "previewed":
            raise QualityWatchVersionConflict("baseline initialization run changed")
        connection.execute(
            "UPDATE quality_watch_baseline_init_runs SET status='applied', idempotency_key=?, "
            "selected_target_count=?, response_json=?, updated_at=?, completed_at=? WHERE run_id=?",
            (
                str(idempotency_key), len(selected_target_ids), _json_dump(response),
                now_text, now_text, str(run_id),
            ),
        )
        for item in plan_items:
            connection.execute(
                "INSERT INTO quality_watch_baseline_init_items ("
                "run_id, public_target_id, owner_target_key, artifact_ref, season_number, episode_number, "
                "evidence_source, first_success_at, baseline_ready_at, result, reason_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(run_id), item["publicTargetId"], item["ownerTargetKey"], item["artifactRef"],
                    int(item["seasonNumber"]), int(item["episodeNumber"]), item["evidenceSource"],
                    item["firstSuccessAt"], item["baselineReadyAt"], item["result"],
                    item.get("reasonCode", ""),
                ),
            )

    def get_watch_unit(self, unit_key):
        with closing(self.runtime.connect()) as connection:
            row = connection.execute("SELECT * FROM quality_watch_units WHERE unit_key=?", (str(unit_key),)).fetchone()
        return self._watch_unit(row)

    def list_watch_units(self, subscription_key):
        with closing(self.runtime.connect()) as connection:
            return self.list_watch_units_in_connection(connection, subscription_key)
        return []

    def list_watch_units_in_connection(self, connection, subscription_key):
        rows = connection.execute(
            "SELECT * FROM quality_watch_units WHERE subscription_key=? "
            "ORDER BY season_number, episode_number, created_at",
            (str(subscription_key),),
        ).fetchall()
        return [self._watch_unit(row) for row in rows]

    def apply_reconcile_plan(self, connection, plan, *, now=None):
        now_text = _iso(_as_utc(now or self.clock()))
        touched = []
        for write in plan.get("writes") or []:
            unit_key = str(write.get("unitKey") or "")
            values = dict(write.get("values") or {})
            if not unit_key:
                raise ValueError("reconcile plan missing unit key")
            if write.get("operation") == "insert":
                cursor = connection.execute(
                    "INSERT INTO quality_watch_units ("
                    "unit_key, subscription_key, season_number, episode_number, torra_subscription_id, state, "
                    "first_success_at, baseline_ready_at, window_hours, next_check_at, observation_ends_at, "
                    "current_evidence_json, last_result_json, target_reached_at, lifecycle_mode, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        unit_key,
                        str(values.get("subscription_key") or ""),
                        values.get("season_number"),
                        values.get("episode_number"),
                        str(values.get("torra_subscription_id") or ""),
                        str(values.get("state") or "waiting_library_baseline"),
                        str(values.get("first_success_at") or ""),
                        str(values.get("baseline_ready_at") or ""),
                        int(values.get("window_hours") or 48),
                        str(values.get("next_check_at") or ""),
                        str(values.get("observation_ends_at") or ""),
                        _json_dump(values.get("current_evidence") or {}),
                        _json_dump(values.get("last_result") or {}),
                        str(values.get("target_reached_at") or ""),
                        _lifecycle_mode(values.get("lifecycle_mode") or DEFAULT_LIFECYCLE_MODE),
                        now_text,
                        now_text,
                    ),
                )
                if cursor.rowcount != 1:
                    raise QualityWatchVersionConflict("quality watch unit already exists")
            elif write.get("operation") == "update":
                allowed = {
                    "torra_subscription_id": str,
                    "state": str,
                    "baseline_ready_at": str,
                    "next_check_at": str,
                    "observation_ends_at": str,
                    "current_evidence": _json_dump,
                    "last_result": _json_dump,
                    "target_reached_at": str,
                    "lifecycle_mode": _lifecycle_mode,
                }
                assignments = []
                parameters = []
                for key, mapper in allowed.items():
                    if key not in values:
                        continue
                    column = f"{key}_json" if key in {"current_evidence", "last_result"} else key
                    assignments.append(f"{column}=?")
                    parameters.append(mapper(values[key]))
                if assignments:
                    cursor = connection.execute(
                        f"UPDATE quality_watch_units SET {', '.join(assignments)}, updated_at=?, version=version+1 "
                        "WHERE unit_key=? AND version=?",
                        (*parameters, now_text, unit_key, int(write.get("expectedVersion") or 0)),
                    )
                    if cursor.rowcount != 1:
                        raise QualityWatchVersionConflict("quality watch unit version changed")
            else:
                raise ValueError("unsupported reconcile plan operation")
            touched.append(unit_key)
        return touched

    def list_active_watch_units(self, at=None):
        current = _iso(_as_utc(at or self.clock()))
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM quality_watch_units WHERE state IN ('observing_upgrade', 'search_due', 'search_running') "
                "AND baseline_ready_at<>'' AND observation_ends_at>=? ORDER BY subscription_key, season_number, episode_number",
                (current,),
            ).fetchall()
        return [self._watch_unit(row) for row in rows]

    def list_candidate_watch_units(self, at=None):
        current = _iso(_as_utc(at or self.clock()))
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM quality_watch_units WHERE first_success_at<>'' AND ("
                "state='waiting_library_baseline' OR ("
                "state IN ('observing_upgrade', 'search_due', 'search_running') "
                "AND observation_ends_at>=?)) "
                "ORDER BY subscription_key, season_number, episode_number",
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
        lifecycle_mode=DEFAULT_LIFECYCLE_MODE,
    ):
        window_hours = int(window_hours)
        if window_hours not in {24, 48}:
            raise ValueError("追更洗版窗口只允许 24 或 48 小时")
        lifecycle_mode = _lifecycle_mode(lifecycle_mode)
        unit_key = make_unit_key(subscription_key, media_type, season_number, episode_number)
        blocked = str(media_type).lower() == "tv" and unit_key.endswith(":blocked")
        now = _as_utc(self.clock())
        first_success = _as_utc(first_success_at or now)
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO quality_watch_units ("
                "unit_key, subscription_key, season_number, episode_number, torra_subscription_id, state, "
                "first_success_at, window_hours, lifecycle_mode, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
                    lifecycle_mode,
                    _iso(now),
                    _iso(now),
                ),
            )
        return self.get_watch_unit(unit_key)

    def mark_baseline_ready(
        self,
        unit_key,
        baseline_ready_at=None,
        offsets_minutes=None,
        lifecycle_mode=DEFAULT_LIFECYCLE_MODE,
    ):
        now = _as_utc(baseline_ready_at or self.clock())
        lifecycle_mode = _lifecycle_mode(lifecycle_mode)
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
            next_check = observation_ends if lifecycle_mode == DEFAULT_LIFECYCLE_MODE else (
                now + timedelta(minutes=offsets[0])
            )
            connection.execute(
                "UPDATE quality_watch_units SET baseline_ready_at=?, state='observing_upgrade', next_check_at=?, "
                "observation_ends_at=?, current_offset_index=0, lifecycle_mode=?, "
                "updated_at=?, version=version+1 WHERE unit_key=?",
                (
                    _iso(now),
                    _iso(next_check),
                    _iso(observation_ends),
                    lifecycle_mode,
                    _iso(self.clock()),
                    str(unit_key),
                ),
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
            "baseline_artifact_key": str,
            "baseline_score": float,
            "baseline_rule_hash": str,
            "best_match_id": str,
            "best_candidate_score": float,
            "upgrade_count": int,
            "last_candidate_at": str,
            "lifecycle_mode": _lifecycle_mode,
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

    def save_baseline(self, unit_keys, artifact_key, score, rule_hash, summary=None):
        keys = sorted({str(value or "").strip() for value in unit_keys if str(value or "").strip()})
        if not keys:
            return []
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("baseline score must be numeric")
        with self.runtime.transaction(immediate=True) as connection:
            for unit_key in keys:
                row = connection.execute(
                    "SELECT current_evidence_json FROM quality_watch_units WHERE unit_key=?",
                    (unit_key,),
                ).fetchone()
                if not row:
                    continue
                evidence = _json_load(row["current_evidence_json"])
                evidence["baselineSummary"] = summary if isinstance(summary, dict) else {}
                connection.execute(
                    "UPDATE quality_watch_units SET baseline_artifact_key=?, baseline_score=?, "
                    "baseline_rule_hash=?, current_evidence_json=?, updated_at=?, version=version+1 "
                    "WHERE unit_key=?",
                    (
                        str(artifact_key or ""),
                        float(score),
                        str(rule_hash or ""),
                        _json_dump(evidence),
                        _iso(self.clock()),
                        unit_key,
                    ),
                )
        return [unit for unit in (self.get_watch_unit(key) for key in keys) if unit]

    def save_candidate_champion(
        self,
        unit_key,
        *,
        match_id,
        score,
        last_candidate_at,
        artifact_key="",
        decision="",
        summary=None,
    ):
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("candidate score must be numeric")
        unit_key = str(unit_key or "").strip()
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT current_evidence_json FROM quality_watch_units WHERE unit_key=?",
                (unit_key,),
            ).fetchone()
            if not row:
                return None
            evidence = _json_load(row["current_evidence_json"])
            evidence.update({
                "candidateDecision": str(decision or ""),
                "bestArtifactKey": str(artifact_key or ""),
                "bestCandidateSummary": summary if isinstance(summary, dict) else {},
            })
            connection.execute(
                "UPDATE quality_watch_units SET best_match_id=?, best_candidate_score=?, "
                "last_candidate_at=?, current_evidence_json=?, updated_at=?, version=version+1 "
                "WHERE unit_key=?",
                (
                    str(match_id or ""),
                    float(score),
                    str(last_candidate_at or ""),
                    _json_dump(evidence),
                    _iso(self.clock()),
                    unit_key,
                ),
            )
        return self.get_watch_unit(unit_key)

    def clear_candidate_champion(self, unit_key, last_candidate_at=""):
        unit_key = str(unit_key or "").strip()
        with self.runtime.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT current_evidence_json FROM quality_watch_units WHERE unit_key=?",
                (unit_key,),
            ).fetchone()
            if not row:
                return None
            evidence = _json_load(row["current_evidence_json"])
            evidence.update({
                "candidateDecision": "",
                "bestArtifactKey": "",
                "bestCandidateSummary": {},
            })
            connection.execute(
                "UPDATE quality_watch_units SET best_match_id='', best_candidate_score=NULL, "
                "last_candidate_at=?, current_evidence_json=?, updated_at=?, version=version+1 "
                "WHERE unit_key=?",
                (
                    str(last_candidate_at or ""),
                    _json_dump(evidence),
                    _iso(self.clock()),
                    unit_key,
                ),
            )
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

    def latest_subscription_action(self, subscription_key, provider, action_type, source=""):
        with closing(self.runtime.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM provider_actions WHERE subscription_key=? AND provider=? "
                "AND action_type=? ORDER BY created_at DESC LIMIT 50",
                (str(subscription_key), str(provider), str(action_type)),
            ).fetchall()
        actions = [self._action(row) for row in rows]
        if source:
            actions = [
                action for action in actions
                if action.get("request_summary", {}).get("source") == str(source)
            ]
        return actions[0] if actions else None

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
        require_provider_idle=False,
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
                idle_sql = (
                    "SELECT * FROM provider_actions WHERE provider=? "
                    "AND status IN ('claimed', 'submitted', 'polling') ORDER BY created_at LIMIT 1"
                    if require_provider_idle
                    else "SELECT * FROM provider_actions WHERE provider=? AND action_type=? "
                    "AND status IN ('claimed', 'submitted', 'polling') ORDER BY created_at LIMIT 1"
                )
                idle_params = (provider,) if require_provider_idle else (provider, action_type)
                inflight = connection.execute(
                    idle_sql,
                    idle_params,
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
