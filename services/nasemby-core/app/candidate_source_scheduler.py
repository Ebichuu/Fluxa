from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone


SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
SCHEDULE_GRACE = timedelta(hours=2)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _task_time(value) -> tuple[int, int]:
    try:
        hour, minute = [int(part) for part in str(value or "08:30").split(":", 1)]
    except (TypeError, ValueError):
        return 8, 30
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else (8, 30)


def _schedule_context(now: datetime, task_time) -> dict:
    current = _as_utc(now)
    local = current.astimezone(SHANGHAI_TZ)
    hour, minute = _task_time(task_time)
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return {
        "now": current,
        "scheduled": scheduled.astimezone(timezone.utc),
        "nextScheduled": (scheduled + timedelta(days=1)).astimezone(timezone.utc),
        "scheduleKey": f"{scheduled:%Y-%m-%d}@{hour:02d}:{minute:02d}",
        "due": local >= scheduled,
    }


class CandidateSourceScheduler:
    def __init__(
        self,
        repository,
        refresh,
        config_loader,
        *,
        legacy_projector=None,
        clock=None,
    ):
        self.repository = repository
        self.refresh = refresh
        self.config_loader = config_loader
        self.legacy_projector = legacy_projector
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._run_lock = threading.Lock()

    def _config(self) -> tuple[dict, dict]:
        config = self.config_loader() or {}
        config = config if isinstance(config, dict) else {}
        douban = config.get("douban") if isinstance(config.get("douban"), dict) else {}
        return config, douban

    def _state_with_config(self, now=None) -> tuple[dict, dict, dict]:
        current = _as_utc(now or self.clock())
        config, douban = self._config()
        state = self.repository.get_candidate_scheduler_state()
        context = _schedule_context(current, douban.get("task_time"))
        enabled = bool(douban.get("task_enabled"))
        rules_enabled = bool(douban.get("enabled"))
        attempted = state.get("lastAttemptedScheduleKey") == context["scheduleKey"]
        next_run = context["nextScheduled"] if attempted and context["due"] else context["scheduled"]
        if not context["due"]:
            next_run = context["scheduled"]
        return config, douban, {
            **state,
            "enabled": enabled,
            "rulesEnabled": rules_enabled,
            "nextRunAt": _iso(next_run),
            "currentScheduleKey": context["scheduleKey"],
            "currentScheduleAt": _iso(context["scheduled"]),
            "due": bool(context["due"] and not attempted),
            "overdue": bool(
                enabled
                and rules_enabled
                and context["due"]
                and not attempted
                and current > context["scheduled"] + SCHEDULE_GRACE
            ),
            "observedAt": _iso(current),
        }

    def snapshot(self, *, now=None) -> dict:
        _, _, state = self._state_with_config(now)
        if not state["enabled"]:
            status = "disabled"
        elif not state["rulesEnabled"]:
            status = "rules_disabled"
        elif state["running"]:
            status = "running"
        elif state["lastError"]:
            status = "error"
        elif state["overdue"]:
            status = "overdue"
        elif not state["lastRunAt"]:
            status = "waiting_first_run"
        else:
            status = "healthy"
        return {
            "enabled": state["enabled"],
            "rulesEnabled": state["rulesEnabled"],
            "running": state["running"],
            "runId": state["runId"],
            "startedAt": state["startedAt"],
            "finishedAt": state["finishedAt"],
            "lastRunAt": state["lastRunAt"],
            "lastSuccessAt": state["lastSuccessAt"],
            "nextRunAt": state["nextRunAt"],
            "lastError": state["lastError"],
            "lastResult": state["lastResult"],
            "observedAt": state["observedAt"],
            "overdue": state["overdue"],
            "state": status,
        }

    def recover(self) -> dict:
        return self.repository.recover_interrupted_candidate_refresh(
            observed_at=_iso(_as_utc(self.clock()))
        )

    def _schedule_key_for_run(self, trigger, state):
        if trigger == "scheduled":
            return state["currentScheduleKey"]
        if trigger == "manual" and state["enabled"] and state["due"]:
            return state["currentScheduleKey"]
        return ""

    @staticmethod
    def _result_counts(result) -> dict:
        source_counts = result.get("sourceCounts") if isinstance(result, dict) else {}
        source_counts = source_counts if isinstance(source_counts, dict) else {}
        candidates = result.get("candidates") if isinstance(result, dict) else {}
        candidates = candidates if isinstance(candidates, dict) else {}
        return {
            "succeededSources": int(source_counts.get("succeeded") or 0),
            "failedSources": int(source_counts.get("failed") or 0),
            "skippedItems": int(result.get("skipped") or 0) if isinstance(result, dict) else 0,
            "addedCandidates": int(candidates.get("added") or 0),
            "updatedCandidates": int(candidates.get("updated") or 0),
        }

    def _project_legacy(self, state):
        if not self.legacy_projector:
            return None
        try:
            return self.legacy_projector(state)
        except Exception:
            return None

    def _conflict_result(self, reason):
        public_state = self.snapshot()
        running = reason == "already_running"
        return {
            "success": False,
            "status": reason,
            "code": (
                "CANDIDATE_REFRESH_ALREADY_RUNNING"
                if running else "CANDIDATE_REFRESH_ALREADY_ATTEMPTED"
            ),
            "error": "候选来源正在更新" if running else "当前候选更新计划已执行",
            "runId": public_state.get("runId") or "",
            "scheduler": public_state,
        }

    def _claim_run(self, trigger, run_id):
        now = _as_utc(self.clock())
        _, douban, state = self._state_with_config(now)
        if not state["rulesEnabled"]:
            raise RuntimeError("请先启用候选规则")
        schedule_key = self._schedule_key_for_run(trigger, state)
        next_run = (
            _schedule_context(now, douban.get("task_time"))["nextScheduled"]
            if schedule_key
            else datetime.fromisoformat(state["nextRunAt"].replace("Z", "+00:00"))
        )
        claim = self.repository.claim_candidate_refresh(
            run_id=run_id,
            schedule_key=schedule_key,
            enabled=state["enabled"],
            started_at=_iso(now),
            next_run_at=state["nextRunAt"],
        )
        if not claim["claimed"]:
            return None, self._conflict_result(claim["reason"])
        return {"nextRun": next_run}, None

    def _complete_run(self, result, trigger, run_id, context):
        result = result if isinstance(result, dict) else {}
        counts = self._result_counts(result)
        full_success = bool(result.get("success", True)) and counts["failedSources"] == 0
        completed = self.repository.complete_candidate_refresh(
            run_id=run_id,
            finished_at=_iso(_as_utc(self.clock())),
            next_run_at=_iso(context["nextRun"]),
            last_error="" if full_success else "候选来源更新存在失败",
            last_result={**counts, "trigger": trigger},
            succeeded=full_success,
        )
        projected_config = self._project_legacy(completed)
        try:
            public_state = self.snapshot()
        except Exception:
            public_state = {
                key: completed.get(key)
                for key in (
                    "enabled", "running", "runId", "startedAt", "finishedAt", "lastRunAt",
                    "lastSuccessAt", "nextRunAt", "lastError", "lastResult", "observedAt",
                )
            }
        return {
            **result,
            **({"config": projected_config} if isinstance(projected_config, dict) else {}),
            "success": bool(result.get("success", True)),
            "status": "success" if full_success else "partial_failure",
            "trigger": trigger,
            "runId": run_id,
            "sourceCounts": {
                **(result.get("sourceCounts") or {}),
                "succeeded": counts["succeededSources"],
                "failed": counts["failedSources"],
            },
            "scheduler": public_state,
        }

    def _complete_failed_run(self, run_id, context, trigger):
        failed = self.repository.complete_candidate_refresh(
            run_id=run_id,
            finished_at=_iso(_as_utc(self.clock())),
            next_run_at=_iso(context["nextRun"]),
            last_error="候选来源更新失败",
            last_result={"trigger": trigger},
            succeeded=False,
        )
        self._project_legacy(failed)

    def run(self, *, trigger="manual") -> dict:
        trigger = "scheduled" if trigger == "scheduled" else "manual"
        if not self._run_lock.acquire(blocking=False):
            return self._conflict_result("already_running")
        run_id = f"candidate-run:{uuid.uuid4().hex}"
        context = None
        try:
            context, conflict = self._claim_run(trigger, run_id)
            if conflict:
                return conflict
            result = self.refresh(trigger=trigger, run_id=run_id)
            response = self._complete_run(result, trigger, run_id, context)
            context = None
            return response
        except Exception:
            if context:
                self._complete_failed_run(run_id, context, trigger)
            raise
        finally:
            self._run_lock.release()

    def run_due(self) -> dict:
        now = _as_utc(self.clock())
        _, _, state = self._state_with_config(now)
        self.repository.sync_candidate_scheduler_state(
            enabled=state["enabled"],
            next_run_at=state["nextRunAt"],
            observed_at=_iso(now),
        )
        if not state["enabled"] or not state["rulesEnabled"]:
            return {"status": "disabled", "ran": False, "scheduler": self.snapshot(now=now)}
        if not state["due"]:
            return {"status": "not_due", "ran": False, "scheduler": self.snapshot(now=now)}
        result = self.run(trigger="scheduled")
        return {
            "status": result.get("status") or "success",
            "ran": result.get("status") not in {"already_running", "already_attempted"},
            "result": result,
        }


def register_candidate_source_scheduler(app, scheduler):
    app.extensions["mcc_candidate_source_scheduler"] = scheduler
    return scheduler
