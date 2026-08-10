from __future__ import annotations

import posixpath
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from flask import Flask, jsonify

from app.activity_log import write_activity
from app.sqlite_runtime import SQLiteRuntime
from app.symedia_evidence_runtime import normalize_symedia_status, symedia_protection_rule
from app.task_public_runtime import public_pipeline_ref, safe_public_text


JOB_KIND = "plugin.secupload_observer_candidate"
BEIJING_TZ = timezone(timedelta(hours=8))
TERMINAL_JOB_STATUSES = {"success", "failed", "cancelled"}
ACTIVE_HANDOFF_STATUSES = {"waiting_job", "pending", "submitted"}
MISSING_HISTORY_ERROR = "Symedia 单文件归档在有限重试内未返回历史证据"
MEDIA_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".rmvb", ".ts", ".webm", ".wmv",
}


def _text(value):
    return str(value or "").strip()


def _truthy(value):
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _integer(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_utc(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _torra_job_time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    # Torra serializes job timestamps without an offset in the fnOS local
    # timezone. Treating those values as UTC moves the watermark eight hours
    # into the future and makes pre-enable jobs look new.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(timezone.utc)


def _history_time(row):
    value = _field(row, "date", "created_at", "createdAt", "updated_at", "updatedAt")
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(timezone.utc)


def _field(source, *names):
    if not isinstance(source, dict):
        return None
    for name in names:
        if source.get(name) not in (None, ""):
            return source.get(name)
    return None


def _normalized_path(value):
    value = _text(value).replace("\\", "/")
    if not value.startswith("/"):
        return ""
    return posixpath.normpath(value)


def _path_suffix_matches(candidate, suffix):
    candidate_parts = PurePosixPath(_normalized_path(candidate)).parts
    suffix_parts = PurePosixPath(_normalized_path(suffix)).parts
    if not candidate_parts or not suffix_parts:
        return False
    while suffix_parts and suffix_parts[0] == "/":
        suffix_parts = suffix_parts[1:]
    return bool(suffix_parts) and tuple(candidate_parts[-len(suffix_parts):]) == tuple(suffix_parts)


class SymediaSecuploadHandoffRepository:
    def __init__(self, database_path, clock=None):
        self.runtime = SQLiteRuntime(database_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialize()

    def _initialize(self):
        self.runtime.initialize()
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS symedia_secupload_handoff_state ("
                "id INTEGER PRIMARY KEY CHECK (id=1), cursor_created_at TEXT NOT NULL, "
                "last_polled_at TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', "
                "updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS symedia_secupload_handoff_items ("
                "job_id TEXT PRIMARY KEY, job_created_at TEXT NOT NULL, job_status TEXT NOT NULL, "
                "config_item_id TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '', "
                "target_path TEXT NOT NULL DEFAULT '', transfer_task_id TEXT NOT NULL DEFAULT '', "
                "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL DEFAULT '', "
                "last_attempt_at TEXT NOT NULL DEFAULT '', last_history_at TEXT NOT NULL DEFAULT '', "
                "completed_at TEXT NOT NULL DEFAULT '', "
                "last_error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)"
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(symedia_secupload_handoff_items)"
                ).fetchall()
            }
            if "last_history_at" not in columns:
                connection.execute(
                    "ALTER TABLE symedia_secupload_handoff_items "
                    "ADD COLUMN last_history_at TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_symedia_handoff_status_due "
                "ON symedia_secupload_handoff_items(job_status, next_attempt_at)"
            )

    def state(self):
        with self.runtime.connect() as connection:
            row = connection.execute(
                "SELECT * FROM symedia_secupload_handoff_state WHERE id=1"
            ).fetchone()
        return dict(row) if row else None

    def bootstrap(self, cursor_created_at):
        now = _iso(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO symedia_secupload_handoff_state "
                "(id, cursor_created_at, last_polled_at, updated_at) VALUES (1, ?, ?, ?)",
                (cursor_created_at, now, now),
            )
        return self.state()

    def update_state(self, *, cursor_created_at=None, last_error=None):
        state = self.state()
        if not state:
            raise RuntimeError("秒传交接水位尚未初始化")
        now = _iso(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE symedia_secupload_handoff_state SET cursor_created_at=?, "
                "last_polled_at=?, last_error=?, updated_at=? WHERE id=1",
                (
                    cursor_created_at if cursor_created_at is not None else state["cursor_created_at"],
                    now,
                    last_error if last_error is not None else state["last_error"],
                    now,
                ),
            )

    def add_job(self, job_id, created_at, status="waiting_job"):
        now = _iso(self.clock())
        with self.runtime.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO symedia_secupload_handoff_items "
                "(job_id, job_created_at, job_status, updated_at) VALUES (?, ?, ?, ?)",
                (job_id, created_at, status, now),
            )
        return cursor.rowcount > 0

    def update_item(self, job_id, **changes):
        allowed = {
            "job_status", "config_item_id", "display_name", "target_path",
            "transfer_task_id", "attempts", "next_attempt_at", "last_attempt_at",
            "last_history_at", "completed_at", "last_error",
        }
        fields = {key: value for key, value in changes.items() if key in allowed}
        if not fields:
            return self.item(job_id)
        fields["updated_at"] = _iso(self.clock())
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.runtime.transaction(immediate=True) as connection:
            connection.execute(
                f"UPDATE symedia_secupload_handoff_items SET {assignments} WHERE job_id=?",
                (*fields.values(), job_id),
            )
        return self.item(job_id)

    def item(self, job_id):
        with self.runtime.connect() as connection:
            row = connection.execute(
                "SELECT * FROM symedia_secupload_handoff_items WHERE job_id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def active_items(self):
        placeholders = ",".join("?" for _ in ACTIVE_HANDOFF_STATUSES)
        with self.runtime.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM symedia_secupload_handoff_items WHERE job_status IN ({placeholders}) "
                "ORDER BY job_created_at, job_id",
                tuple(sorted(ACTIVE_HANDOFF_STATUSES)),
            ).fetchall()
        return [dict(row) for row in rows]

    def repairable_items(self):
        placeholders = ",".join("?" for _ in ACTIVE_HANDOFF_STATUSES)
        with self.runtime.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM symedia_secupload_handoff_items "
                f"WHERE job_status IN ({placeholders}) "
                "OR (job_status='failed' AND last_error=?) "
                "ORDER BY job_created_at, job_id",
                (*sorted(ACTIVE_HANDOFF_STATUSES), MISSING_HISTORY_ERROR),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self):
        with self.runtime.connect() as connection:
            rows = connection.execute(
                "SELECT job_status, COUNT(*) AS count FROM symedia_secupload_handoff_items "
                "GROUP BY job_status"
            ).fetchall()
            latest = connection.execute(
                "SELECT job_id, job_status, display_name, attempts, last_error, updated_at "
                "FROM symedia_secupload_handoff_items ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        return {
            "state": self.state(),
            "counts": {row["job_status"]: int(row["count"]) for row in rows},
            "latest": [dict(row) for row in latest],
        }


class SymediaSecuploadHandoffService:
    def __init__(self, repository, torra, symedia, *, environment=None, clock=None, activity_writer=None):
        self.repository = repository
        self.torra = torra
        self.symedia = symedia
        self.environment = environment or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.activity_writer = activity_writer or write_activity

    def enabled(self):
        return _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")) and _truthy(
            self.environment.get("MCC_SYMEDIA_SECUPLOAD_HANDOFF_ENABLED")
        )

    def _settle_seconds(self):
        return max(0, _integer(self.environment.get("MCC_SYMEDIA_SECUPLOAD_SETTLE_SECONDS"), 150))

    def _retry_seconds(self):
        return max(60, _integer(self.environment.get("MCC_SYMEDIA_SECUPLOAD_RETRY_SECONDS"), 900))

    def _max_attempts(self):
        return max(1, min(5, _integer(self.environment.get("MCC_SYMEDIA_SECUPLOAD_MAX_ATTEMPTS"), 3)))

    @staticmethod
    def _job_id(job):
        return _text(_field(job, "id", "job_id", "jobId"))

    @staticmethod
    def _job_created_at(job):
        return _torra_job_time(_field(job, "created_at", "createdAt", "started_at", "startedAt"))

    @staticmethod
    def _job_status(job):
        return _text(_field(job, "status")).lower()

    def _discover(self, rows, started_at):
        state = self.repository.state()
        if not state:
            self.repository.bootstrap(_iso(started_at))
            return {"bootstrapped": True, "discovered": 0}
        cursor = _as_utc(state.get("cursor_created_at")) or datetime.min.replace(tzinfo=timezone.utc)
        discovered = 0
        newest = cursor
        for row in rows:
            job_id = self._job_id(row)
            created_at = _as_utc(self._job_created_at(row))
            if not job_id or not created_at or created_at < cursor:
                continue
            discovered += int(self.repository.add_job(job_id, _iso(created_at)))
            newest = max(newest, created_at)
        self.repository.update_state(cursor_created_at=_iso(newest), last_error="")
        return {"bootstrapped": False, "discovered": discovered}

    @staticmethod
    def _job_result(detail):
        data = detail.get("data") if isinstance(detail.get("data"), dict) else detail
        return data.get("result") if isinstance(data.get("result"), dict) else {}

    @staticmethod
    def _job_payload(detail):
        data = detail.get("data") if isinstance(detail.get("data"), dict) else detail
        return data.get("payload") if isinstance(data.get("payload"), dict) else {}

    def _resolve_target(self, detail):
        result = self._job_result(detail)
        payload = self._job_payload(detail)
        file_path = _normalized_path(_field(result, "file_path", "filePath"))
        config_item_id = _text(_field(payload, "config_item_id", "configItemId"))
        if not file_path or not config_item_id:
            raise RuntimeError("Torra 秒传任务缺少文件路径或配置项")
        if PurePosixPath(file_path).suffix.lower() not in MEDIA_EXTENSIONS:
            raise RuntimeError("Torra 秒传任务不是受支持的媒体文件")
        routes = self.torra.get_secupload_config_routes()
        route = next((row for row in routes if _text(row.get("itemId")) == config_item_id), None)
        if not route:
            raise RuntimeError("Torra 秒传配置项已不存在或未启用")
        source_path = _normalized_path(route.get("sourcePath"))
        dest_path = _normalized_path(route.get("destPath"))
        if not source_path or not dest_path:
            raise RuntimeError("Torra 秒传配置缺少源目录或目标目录")
        try:
            if posixpath.commonpath((source_path, file_path)) != source_path:
                raise RuntimeError("Torra 秒传文件越出配置源目录")
        except ValueError as exc:
            raise RuntimeError("Torra 秒传文件路径无法确认") from exc
        relative_path = posixpath.relpath(file_path, source_path)
        if relative_path in {".", ".."} or relative_path.startswith("../"):
            raise RuntimeError("Torra 秒传文件相对路径无效")
        tasks = []
        for task in self.symedia.list_transfer_tasks():
            task_source = _normalized_path(_field(task, "source_dir", "sourceDir"))
            if task_source and _path_suffix_matches(task_source, dest_path):
                tasks.append((task, task_source))
        if len(tasks) != 1:
            raise RuntimeError("Torra 秒传目标未能唯一匹配 Symedia 归档任务")
        task, task_source = tasks[0]
        task_id = _text(_field(task, "id"))
        # Torra uploads each observed file into the configured remote directory;
        # the local torrent directory is not recreated in 115.
        display_name = PurePosixPath(file_path).name
        target_path = posixpath.normpath(posixpath.join(task_source, display_name))
        if not task_id or posixpath.commonpath((task_source, target_path)) != task_source:
            raise RuntimeError("Symedia 单文件目标路径无效")
        return {
            "configItemId": config_item_id,
            "displayName": display_name,
            "targetPath": target_path,
            "transferTaskId": task_id,
        }

    def _resolve_waiting_jobs(self):
        resolved = 0
        for item in self.repository.active_items():
            if item["job_status"] != "waiting_job":
                continue
            try:
                detail = self.torra.get_job_snapshot(item["job_id"])
                status = self._job_status(detail.get("data") if isinstance(detail.get("data"), dict) else detail)
                if status not in TERMINAL_JOB_STATUSES:
                    continue
                if status != "success":
                    self.repository.update_item(
                        item["job_id"], job_status="ignored", completed_at=_iso(self.clock()),
                        last_error=f"Torra 秒传任务终态：{status}",
                    )
                    continue
                target = self._resolve_target(detail)
                due_at = self.clock() + timedelta(seconds=self._settle_seconds())
                self.repository.update_item(
                    item["job_id"], job_status="pending",
                    config_item_id=target["configItemId"], display_name=target["displayName"],
                    target_path=target["targetPath"], transfer_task_id=target["transferTaskId"],
                    next_attempt_at=_iso(due_at), last_error="",
                )
                resolved += 1
            except Exception as exc:
                self.repository.update_item(
                    item["job_id"], job_status="failed", completed_at=_iso(self.clock()),
                    last_error=safe_public_text(str(exc), "秒传交接目标解析失败"),
                )
        return resolved

    def _repair_nested_targets(self):
        candidates = self.repository.repairable_items()
        if not candidates:
            return 0
        task_sources = {
            _text(_field(task, "id")): _normalized_path(_field(task, "source_dir", "sourceDir"))
            for task in self.symedia.list_transfer_tasks()
        }
        repaired = 0
        for item in candidates:
            display_name = _text(item.get("display_name"))
            task_source = task_sources.get(_text(item.get("transfer_task_id")), "")
            current_path = _normalized_path(item.get("target_path"))
            if (
                not display_name
                or PurePosixPath(display_name).name != display_name
                or PurePosixPath(display_name).suffix.lower() not in MEDIA_EXTENSIONS
                or not task_source
                or not current_path
            ):
                continue
            expected_path = posixpath.normpath(posixpath.join(task_source, display_name))
            if current_path == expected_path or PurePosixPath(current_path).name != display_name:
                continue
            try:
                if posixpath.commonpath((task_source, current_path)) != task_source:
                    continue
            except ValueError:
                continue
            self.repository.update_item(
                item["job_id"], job_status="pending", target_path=expected_path,
                attempts=0, next_attempt_at=_iso(self.clock()), last_attempt_at="",
                last_history_at="", completed_at="", last_error="",
            )
            repaired += 1
        return repaired

    @staticmethod
    def _history_source_paths(row):
        paths = []
        for key in ("src", "source", "source_path", "file_path"):
            value = _normalized_path(row.get(key))
            if value:
                paths.append(value)
        for key in ("src_detail", "source_detail"):
            detail = row.get(key) if isinstance(row.get(key), dict) else {}
            for path_key in ("file_path", "path", "src"):
                value = _normalized_path(detail.get(path_key))
                if value:
                    paths.append(value)
        return paths

    def _history_match(self, history_rows, item):
        normalized = _normalized_path(item["target_path"])
        job_created_at = _as_utc(item.get("job_created_at"))
        last_attempt_at = _as_utc(item.get("last_attempt_at"))
        last_history_at = _as_utc(item.get("last_history_at"))
        cutoff = job_created_at
        if int(item.get("attempts") or 0) > 0:
            cutoff = last_attempt_at or job_created_at
        for row in history_rows:
            observed_at = _history_time(row)
            if not observed_at or (cutoff and observed_at < cutoff):
                continue
            if last_history_at and observed_at <= last_history_at:
                continue
            if normalized in self._history_source_paths(row):
                return row, observed_at
        return None, None

    def _finish_from_history(self, item, row, observed_at):
        status = normalize_symedia_status(row.get("status"))
        if status is True:
            final_status = "completed"
            error = ""
        elif symedia_protection_rule(row):
            final_status = "protected"
            error = safe_public_text(row.get("errmsg"))
        elif status is False:
            message = safe_public_text(row.get("errmsg"), "Symedia 单文件归档失败")
            retryable = any(token in message.lower() for token in ("不存在", "not found", "no such file", "missing"))
            if retryable and int(item["attempts"] or 0) < self._max_attempts():
                self.repository.update_item(
                    item["job_id"], job_status="pending",
                    next_attempt_at=_iso(self.clock() + timedelta(seconds=self._retry_seconds())),
                    last_history_at=_iso(observed_at),
                    last_error=message,
                )
                return "retry"
            final_status = "failed"
            error = message
        else:
            return "unknown"
        self.repository.update_item(
            item["job_id"], job_status=final_status, completed_at=_iso(self.clock()),
            next_attempt_at="", last_error=error,
        )
        self.activity_writer(
            "symedia", "secupload_handoff", "success" if final_status in {"completed", "protected"} else "error",
            f"秒传单文件交接{('完成' if final_status == 'completed' else '已处理' if final_status == 'protected' else '失败')}：{item['display_name']}",
        )
        return final_status

    def _process_due(self):
        active = [item for item in self.repository.active_items() if item["job_status"] in {"pending", "submitted"}]
        if not active:
            return {"submitted": 0, "confirmed": 0}
        history_rows = self.symedia.list_transfer_history(count=500, page=1)["rows"]
        submitted = 0
        confirmed = 0
        now = self.clock()
        for item in active:
            history, observed_at = self._history_match(history_rows, item)
            if history:
                outcome = self._finish_from_history(item, history, observed_at)
                if outcome not in {"retry", "unknown"}:
                    confirmed += 1
                continue
            due_at = _as_utc(item["next_attempt_at"])
            if due_at and due_at > now:
                continue
            attempts = int(item["attempts"] or 0)
            if attempts >= self._max_attempts():
                self.repository.update_item(
                    item["job_id"], job_status="failed", completed_at=_iso(now),
                    last_error=MISSING_HISTORY_ERROR,
                )
                continue
            try:
                self.symedia.manual_transfer_file(item["target_path"], item["transfer_task_id"])
                attempted_at = now.replace(microsecond=0)
                self.repository.update_item(
                    item["job_id"], job_status="submitted", attempts=attempts + 1,
                    last_attempt_at=_iso(attempted_at),
                    next_attempt_at=_iso(now + timedelta(seconds=self._retry_seconds())),
                    last_error="",
                )
                if attempts == 0:
                    self.activity_writer(
                        "symedia", "secupload_handoff", "success",
                        f"已提交秒传单文件归档：{item['display_name']}",
                    )
                submitted += 1
            except Exception as exc:
                attempted_at = now.replace(microsecond=0)
                self.repository.update_item(
                    item["job_id"], job_status="pending", attempts=attempts + 1,
                    last_attempt_at=_iso(attempted_at),
                    next_attempt_at=_iso(now + timedelta(seconds=self._retry_seconds())),
                    last_error=safe_public_text(str(exc), "Symedia 单文件归档提交失败"),
                )
        return {"submitted": submitted, "confirmed": confirmed}

    def run_once(self):
        if not self.enabled():
            return {"status": "disabled", "discovered": 0, "submitted": 0, "confirmed": 0}
        started_at = self.clock()
        try:
            rows = self.torra.list_jobs(JOB_KIND, limit=200, offset=0)
            discovery = self._discover(rows, started_at)
            if discovery["bootstrapped"]:
                return {"status": "bootstrapped", "discovered": 0, "submitted": 0, "confirmed": 0}
            resolved = self._resolve_waiting_jobs()
            repaired = self._repair_nested_targets()
            processed = self._process_due()
            self.repository.update_state(last_error="")
            return {
                "status": "ok", "discovered": discovery["discovered"], "resolved": resolved,
                "repaired": repaired,
                **processed,
            }
        except Exception as exc:
            message = safe_public_text(str(exc), "秒传单文件交接失败")
            if self.repository.state():
                self.repository.update_state(last_error=message)
            self.activity_writer("symedia", "secupload_handoff", "error", message)
            return {"status": "error", "error": message, "discovered": 0, "submitted": 0, "confirmed": 0}

    def snapshot(self):
        data = self.repository.snapshot()
        state = data.get("state") or {}
        counts = Counter(data.get("counts") or {})
        return {
            "enabled": self.enabled(),
            "initialized": bool(state),
            "cursorCreatedAt": _text(state.get("cursor_created_at")),
            "lastPolledAt": _text(state.get("last_polled_at")),
            "lastError": safe_public_text(state.get("last_error")),
            "counts": {
                "active": sum(counts[key] for key in ACTIVE_HANDOFF_STATUSES),
                "completed": counts["completed"],
                "protected": counts["protected"],
                "failed": counts["failed"],
                "ignored": counts["ignored"],
            },
            "latest": [{
                "jobRef": public_pipeline_ref("symedia-handoff", row["job_id"]),
                "status": row["job_status"],
                "displayName": safe_public_text(row["display_name"]),
                "attempts": int(row["attempts"] or 0),
                "lastError": safe_public_text(row["last_error"]),
                "updatedAt": row["updated_at"],
            } for row in data.get("latest") or []],
        }


def register_symedia_secupload_handoff(app: Flask, service):
    app.extensions["mcc_symedia_secupload_handoff"] = service

    @app.get("/api/symedia/secupload-handoff")
    def symedia_secupload_handoff_status():
        return jsonify(service.snapshot())

    return service
