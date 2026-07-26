from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from app.activity_log import write_activity
from app.http_runtime import current_request_id
from app.task_public_runtime import present_system_issue


SCHEDULE_GRACE_SECONDS = 600
MAX_SCHEDULE_HORIZON_SECONDS = 86400
SYSTEM_ISSUE_ID = "secupload_failures"
SYSTEM_TARGET_KEY = "system:torra:secupload"
ACTION_TYPE = "secupload_retry"
ACTIVE_RUN_STATUSES = {"queued", "pending", "running", "stopping"}
TERMINAL_RUN_STATUSES = {"success", "failed", "cancelled"}
TERMINAL_ACTION_STATUSES = {"succeeded", "failed", "cancelled"}
IDEMPOTENCY_PATTERN = re.compile(r"^.{12,128}$", re.DOTALL)


class SecuploadIssueError(RuntimeError):
    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status = int(status)


def _text(value, limit=240):
    return str(value or "").strip().replace("\r", " ").replace("\n", " ")[:limit]


def _truthy(value):
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _parse_absolute(value):
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _as_utc(value):
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _public_category_id(value):
    digest = hashlib.sha256(_text(value, 300).encode("utf-8")).hexdigest()[:12]
    return f"category:{digest}"


def _safe_label(value):
    label = _text(value, 80)
    if not label or "://" in label or "/" in label or "\\" in label:
        return "未命名分类"
    return label


def _counts(value):
    source = value if isinstance(value, dict) else {}
    return {
        "success": _integer(source.get("success")),
        "failed": _integer(source.get("failed")),
    }


def _run_time(run):
    return _text(run.get("startedAt") or run.get("createdAt"), 80)


def _batch_key(run):
    started = _run_time(run)
    minute = started[:16] if len(started) >= 16 else started
    return "|".join((_text(run.get("taskKey"), 120), _text(run.get("trigger"), 40), minute))


def _category_history(runs, target_item_id):
    grouped = {}
    for run in runs:
        if _text(run.get("targetItemId"), 200) != target_item_id:
            continue
        counts = _counts(run.get("counts"))
        if counts["failed"] is None:
            continue
        key = _batch_key(run)
        current = grouped.setdefault(key, {"failed": 0, "startedAt": _run_time(run)})
        current["failed"] += counts["failed"]
        if _run_time(run) < current["startedAt"]:
            current["startedAt"] = _run_time(run)
    ordered = sorted(grouped.values(), key=lambda row: row["startedAt"])
    return [row["failed"] for row in ordered[-3:]]


def _latest_failed_runs(summary, failed_total):
    runs = [row for row in summary.get("recentRuns") or [] if isinstance(row, dict)]
    batch = summary.get("latestBatch") if isinstance(summary.get("latestBatch"), dict) else {}
    batch_probe = {
        "taskKey": batch.get("taskKey"),
        "trigger": batch.get("trigger"),
        "startedAt": batch.get("startedAt"),
    }
    key = _batch_key(batch_probe)
    matched = [run for run in runs if _batch_key(run) == key]
    failures = [run for run in matched if (_counts(run.get("counts"))["failed"] or 0) > 0]
    if failed_total > 0 and not failures:
        return [], runs
    return failures, runs


def _retry_policy(item):
    failures = _integer(item.get("fallbackUploadAfterFailures"))
    if failures is None:
        return "重试策略未提供"
    if failures == 0:
        return "仅自动重试秒传"
    return f"失败 {failures} 次后转原始上传"


def _issue_context(value, now=None):
    now = _as_utc(now)
    summary = value if isinstance(value, dict) else {}
    base = {
        "id": SYSTEM_ISSUE_ID,
        "state": "unknown",
        "stateReason": "plugin_unreadable",
        "failedTotal": None,
        "nextRunAt": "",
        "observedAt": _text(summary.get("lastCheckedAt"), 80),
        "scheduleGraceSeconds": SCHEDULE_GRACE_SECONDS,
        "maxScheduleHorizonSeconds": MAX_SCHEDULE_HORIZON_SECONDS,
        "categories": [],
        "fileEvidenceAvailable": False,
        "evidenceLimitText": "Torra 当前未返回失败文件名、具体错误和单文件重试次数。",
        "manualRetry": {"supported": False, "allowed": False, "reason": "秒传状态尚不可确认"},
        "primaryAction": {"kind": "none", "label": "等待状态恢复", "available": False},
    }
    private = {"targets": {}, "runIds": set(), "summary": summary}
    if summary.get("readable") is not True or summary.get("connected") is False:
        return {"issue": base, **private}

    latest = summary.get("latestBatch")
    if not isinstance(latest, dict):
        return {"issue": {**base, "stateReason": "latest_batch_missing"}, **private}
    latest_counts = _counts(latest.get("counts"))
    failed_total = latest_counts["failed"]
    if failed_total is None:
        return {"issue": {**base, "stateReason": "failure_count_missing"}, **private}

    config_items = {
        _text(item.get("itemId"), 200): item
        for item in summary.get("configItems") or []
        if isinstance(item, dict) and _text(item.get("itemId"), 200)
    }
    tasks = {
        _text(task.get("key"), 120): task
        for task in summary.get("tasks") or []
        if isinstance(task, dict) and _text(task.get("key"), 120)
    }
    schedules = [row for row in summary.get("schedules") or [] if isinstance(row, dict)]
    failed_runs, runs = _latest_failed_runs(summary, failed_total)
    private["runIds"] = {
        _text(run.get("runId"), 200)
        for run in runs
        if _text(run.get("runId"), 200)
    }

    categories = []
    schedule_checks = []
    next_times = []
    for run in failed_runs:
        target_item_id = _text(run.get("targetItemId"), 200)
        item = config_items.get(target_item_id) or {}
        public_id = _public_category_id(target_item_id)
        schedule = next((
            row for row in schedules
            if _text(row.get("taskKey"), 120) == "retry_pending"
            and _text(row.get("targetItemId"), 200) == target_item_id
        ), {})
        next_run_at = _text(schedule.get("nextRunAt"), 80)
        parsed_next = _parse_absolute(next_run_at)
        if parsed_next is not None:
            next_times.append((parsed_next, next_run_at))
        task = tasks.get("retry_pending") or {}
        schedule_checks.append(bool(
            summary.get("pluginEnabled") is True
            and task.get("allowSchedule") is True
            and schedule.get("enabled") is True
            and parsed_next is not None
            and now.timestamp() - SCHEDULE_GRACE_SECONDS <= parsed_next.timestamp()
            and parsed_next.timestamp() <= now.timestamp() + MAX_SCHEDULE_HORIZON_SECONDS
        ))
        category = {
            "id": public_id,
            "label": _safe_label(item.get("name")),
            "latest": {
                "success": _counts(run.get("counts"))["success"],
                "failed": _counts(run.get("counts"))["failed"],
                "finishedAt": _text(run.get("finishedAt"), 80),
            },
            "recentFailedCounts": _category_history(runs, target_item_id),
            "retryPolicyText": _retry_policy(item),
            "nextRunAt": next_run_at,
            "fileEvidenceAvailable": False,
        }
        categories.append(category)
        private["targets"][public_id] = target_item_id

    next_run_at = min(next_times, key=lambda row: row[0])[1] if next_times else ""
    task = tasks.get("retry_pending") or {}
    manual_supported = task.get("allowManualRun") is True and bool(categories)
    active_runs = _integer(summary.get("activeRuns")) or 0
    issue = {
        **base,
        "failedTotal": failed_total,
        "nextRunAt": next_run_at,
        "observedAt": _text(summary.get("lastRunAt") or summary.get("lastCheckedAt"), 80),
        "categories": categories,
    }

    if failed_total == 0:
        issue.update({
            "state": "normal",
            "stateReason": "latest_batch_clear",
            "manualRetry": {"supported": task.get("allowManualRun") is True, "allowed": False, "reason": "最近批次没有失败"},
            "primaryAction": {"kind": "none", "label": "无需处理", "available": False},
        })
    elif active_runs > 0:
        issue.update({
            "state": "recovering",
            "stateReason": "active_run",
            "manualRetry": {"supported": manual_supported, "allowed": False, "reason": "Torra 秒传任务正在运行"},
            "primaryAction": {"kind": "wait_for_retry", "label": "等待当前重试完成", "available": False},
        })
    elif categories and schedule_checks and all(schedule_checks):
        issue.update({
            "state": "recovering",
            "stateReason": "scheduled_retry",
            "manualRetry": {"supported": manual_supported, "allowed": False, "reason": "自动重试计划有效"},
            "primaryAction": {"kind": "wait_for_retry", "label": "等待自动重试", "available": False},
        })
    else:
        issue.update({
            "state": "action_required",
            "stateReason": "automatic_retry_unavailable",
            "manualRetry": {"supported": manual_supported, "allowed": manual_supported, "reason": "自动恢复计划不可用"},
            "primaryAction": {
                "kind": "retry_failed_queue" if manual_supported else "none",
                "label": "重试失败队列" if manual_supported else "检查 Torra 秒传配置",
                "available": manual_supported,
            },
        })
    return {"issue": issue, **private}


def build_secupload_issue(value, now=None):
    return _issue_context(value, now=now)["issue"]


def _public_action(action):
    if not isinstance(action, dict):
        return None
    response = action.get("response_summary") if isinstance(action.get("response_summary"), dict) else {}
    counts = response.get("counts") if isinstance(response.get("counts"), dict) else {}
    error_code = _text(action.get("error_code"), 120)
    return {
        "id": _text(action.get("action_id"), 80),
        "status": _text(action.get("status"), 30),
        "categoryId": _text(action.get("unit_key"), 80),
        "createdAt": _text(action.get("created_at"), 80),
        "updatedAt": _text(action.get("updated_at"), 80),
        "completedAt": _text(action.get("completed_at"), 80),
        "result": {
            "runStatus": _text(response.get("runStatus"), 30),
            "counts": {"success": _integer(counts.get("success")), "failed": _integer(counts.get("failed"))},
        } if response else None,
        "error": {"code": error_code, "message": "Torra 秒传重试失败"} if error_code else None,
    }


class SecuploadIssueService:
    def __init__(self, torra, repository, environment=None, clock=None, activity_writer=None):
        self.torra = torra
        self.repository = repository
        self.environment = environment or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.activity_writer = activity_writer or write_activity

    @staticmethod
    def _body(body, allowed, code):
        if body is None:
            body = {}
        if not isinstance(body, dict) or set(body) - set(allowed):
            raise SecuploadIssueError(code, "请求包含不支持的字段", 422)
        return body

    def _read_context(self, summary=None):
        if summary is None:
            try:
                summary = self.torra.get_secupload_summary()
            except Exception:
                summary = {"readable": False, "connected": False}
        return _issue_context(summary, now=self.clock())

    def snapshot(self, summary=None):
        return self._read_context(summary)["issue"]

    @staticmethod
    def _select_category(context, category_id):
        categories = context["issue"].get("categories") or []
        category_id = _text(category_id, 80)
        if not category_id and len(categories) == 1:
            category_id = categories[0]["id"]
        category = next((row for row in categories if row.get("id") == category_id), None)
        if not category or category_id not in context["targets"]:
            raise SecuploadIssueError("SECUPLOAD_CATEGORY_NOT_FOUND", "秒传失败分类不存在，请刷新后重试", 404)
        return category

    def preview(self, body):
        body = self._body(body, {"categoryId"}, "SECUPLOAD_PREVIEW_FIELDS_INVALID")
        context = self._read_context()
        category = self._select_category(context, body.get("categoryId"))
        return self._preview_context(context, category)

    def _preview_context(self, context, category):
        issue = context["issue"]
        base = {
            "allowed": False,
            "requiresConfirmation": True,
            "issue": present_system_issue(issue),
            "category": category,
            "reasonCode": "SECUPLOAD_RETRY_BLOCKED",
            "reasonText": "当前不能手动重试秒传失败队列",
        }
        if not _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")):
            return {**base, "reasonCode": "SECUPLOAD_WRITE_DISABLED", "reasonText": "Fluxa 外部写入当前未启用"}
        if issue.get("state") == "recovering":
            return {**base, "reasonCode": "SECUPLOAD_AUTOMATIC_RETRY_ACTIVE", "reasonText": "自动重试计划有效，请等待自动恢复"}
        if issue.get("state") != "action_required":
            return {**base, "reasonCode": "SECUPLOAD_RETRY_NOT_REQUIRED", "reasonText": "当前没有可确认的终态秒传失败"}
        if not (issue.get("manualRetry") or {}).get("supported"):
            return {**base, "reasonCode": "SECUPLOAD_MANUAL_RETRY_UNAVAILABLE", "reasonText": "Torra 未开放失败队列手动重试"}
        return {
            **base,
            "allowed": True,
            "reasonCode": "SECUPLOAD_RETRY_ALLOWED",
            "reasonText": f"将重试 {category['label']} 的失败队列",
        }

    @staticmethod
    def _idempotency(body):
        value = _text(body.get("idempotencyKey"), 200)
        if not IDEMPOTENCY_PATTERN.fullmatch(value):
            raise SecuploadIssueError("SECUPLOAD_IDEMPOTENCY_INVALID", "幂等键长度必须为 12 到 128 个字符", 422)
        return value

    @staticmethod
    def _validate_action(action, category_id=None):
        if not action or any((
            action.get("subscription_key") != SYSTEM_TARGET_KEY,
            action.get("provider") != "torra",
            action.get("action_type") != ACTION_TYPE,
            category_id and action.get("unit_key") != category_id,
        )):
            raise SecuploadIssueError("SECUPLOAD_ACTION_NOT_FOUND", "秒传重试动作不存在", 404)
        return action

    def _response(self, action, issue, status=None):
        action_status = _text(action.get("status"), 30)
        http_status = status if status is not None else (200 if action_status in TERMINAL_ACTION_STATUSES else 202)
        return {"action": _public_action(action), "issue": present_system_issue(issue)}, http_status

    def _existing(self, idempotency_key, category_id):
        action = self.repository.get_action_by_idempotency(idempotency_key)
        if not action:
            return None
        if any((
            action.get("subscription_key") != SYSTEM_TARGET_KEY,
            action.get("provider") != "torra",
            action.get("action_type") != ACTION_TYPE,
            action.get("unit_key") != category_id,
        )):
            raise SecuploadIssueError("SECUPLOAD_IDEMPOTENCY_CONFLICT", "幂等键已用于其他动作", 409)
        updated, summary = self._poll(action)
        return self._response(updated, build_secupload_issue(summary, now=self.clock()))

    def _claim(self, idempotency_key, category_id):
        claim = self.repository.claim_action(
            idempotency_key,
            SYSTEM_TARGET_KEY,
            "torra",
            ACTION_TYPE,
            unit_key=category_id,
            request_summary={"source": "manual", "categoryId": category_id},
            lease_seconds=120,
            require_idle=True,
        )
        disposition = claim.get("disposition")
        if disposition == "claimed":
            return claim["action"]
        if disposition in {"replay", "in_progress", "resume"}:
            return claim["action"]
        if disposition == "conflict":
            raise SecuploadIssueError("SECUPLOAD_IDEMPOTENCY_CONFLICT", "幂等键已用于其他动作", 409)
        raise SecuploadIssueError("SECUPLOAD_RETRY_BUSY", "另一个秒传重试正在执行", 409)

    def retry(self, body):
        body = self._body(body, {"confirm", "idempotencyKey", "categoryId"}, "SECUPLOAD_RETRY_FIELDS_INVALID")
        if body.get("confirm") is not True:
            raise SecuploadIssueError("SECUPLOAD_CONFIRMATION_REQUIRED", "需要明确确认秒传失败队列重试", 422)
        if not _truthy(self.environment.get("NASEMBY_CORE_WRITE_ENABLED")):
            raise SecuploadIssueError("SECUPLOAD_WRITE_DISABLED", "Fluxa 外部写入当前未启用", 503)
        idempotency_key = self._idempotency(body)
        category_id = _text(body.get("categoryId"), 80)
        existing = self._existing(idempotency_key, category_id)
        if existing is not None:
            return existing
        context = self._read_context()
        category = self._select_category(context, category_id)
        category_id = category["id"]
        preview = self._preview_context(context, category)
        if not preview["allowed"]:
            raise SecuploadIssueError(preview["reasonCode"], preview["reasonText"], 409)
        action = self._claim(idempotency_key, category_id)
        if action.get("external_job_id") or action.get("status") in TERMINAL_ACTION_STATUSES:
            updated, snapshot = self._poll(action)
            return self._response(updated, build_secupload_issue(snapshot, now=self.clock()))
        action_id = action["action_id"]
        try:
            result = self.torra.run_secupload_retry(
                context["targets"][category_id],
                previous_run_ids=context["runIds"],
            )
            run_id = _text((result or {}).get("runId"), 200)
            if not run_id:
                raise RuntimeError("Torra 未返回 run ID")
            action = self.repository.save_external_job(action_id, run_id, status="polling", lease_seconds=120)
        except Exception as exc:
            self.repository.complete_action(
                action_id,
                "failed",
                {"runStatus": "failed", "counts": {"success": None, "failed": None}},
                http_status=502,
                error_code="SECUPLOAD_RETRY_FAILED",
                error_message="Torra 秒传重试失败",
            )
            self.activity_writer("operation", "secupload_retry", "error", f"{category['label']} 秒传重试提交失败")
            raise SecuploadIssueError("SECUPLOAD_RETRY_FAILED", "Torra 秒传重试提交失败", 502) from exc
        self.activity_writer("operation", "secupload_retry", "start", f"{category['label']} 秒传重试已提交")
        return self._response(action, context["issue"], status=202)

    def _poll(self, action):
        if action.get("status") in TERMINAL_ACTION_STATUSES:
            return action, self.torra.get_secupload_summary()
        summary = self.torra.get_secupload_summary()
        run_id = _text(action.get("external_job_id"), 200)
        if not run_id:
            return action, summary
        run = next((
            row for row in summary.get("recentRuns") or []
            if isinstance(row, dict) and _text(row.get("runId"), 200) == run_id
        ), None)
        if not run or _text(run.get("status"), 30).lower() in ACTIVE_RUN_STATUSES:
            return self.repository.save_external_job(
                action["action_id"], run_id, status="polling", lease_seconds=120,
            ), summary
        run_status = _text(run.get("status"), 30).lower()
        counts = _counts(run.get("counts"))
        succeeded = run_status == "success" and (counts["failed"] or 0) == 0
        action_status = "succeeded" if succeeded else "failed"
        updated = self.repository.complete_action(
            action["action_id"],
            action_status,
            {"runStatus": run_status, "counts": counts, "categoryId": action.get("unit_key")},
            http_status=200 if succeeded else 502,
            error_code="" if succeeded else "SECUPLOAD_RETRY_FAILED",
            error_message="" if succeeded else "Torra 秒传重试失败",
        )
        self.activity_writer(
            "operation", "secupload_retry", "success" if succeeded else "error",
            "秒传重试完成" if succeeded else "秒传重试仍有失败",
        )
        return updated, summary

    def action(self, action_id):
        action = self._validate_action(self.repository.get_action(action_id))
        updated, summary = self._poll(action)
        payload, _ = self._response(updated, build_secupload_issue(summary, now=self.clock()))
        return payload


def _error_response(error):
    return jsonify({
        "code": error.code,
        "error": error.message,
        "request_id": current_request_id(),
    }), error.status


def register_secupload_issue(app: Flask, service):
    app.extensions["mcc_secupload_issue"] = service

    def execute(callback):
        try:
            return callback()
        except SecuploadIssueError as exc:
            return _error_response(exc)

    @app.get("/api/v2/system-issues/secupload-failures")
    def secupload_issue_detail():
        return jsonify(present_system_issue(service.snapshot()))

    @app.post("/api/v2/system-issues/secupload-failures/retry-previews")
    def secupload_retry_preview():
        return execute(lambda: jsonify(service.preview(request.get_json(silent=True))))

    @app.post("/api/v2/system-issues/secupload-failures/retries")
    def secupload_retry():
        def response():
            payload, status = service.retry(request.get_json(silent=True))
            return jsonify(payload), status
        return execute(response)

    @app.get("/api/v2/system-issues/secupload-failures/retries/<action_id>")
    def secupload_retry_status(action_id):
        return execute(lambda: jsonify(service.action(action_id)))

    return service
