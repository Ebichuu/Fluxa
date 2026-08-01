from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.candidate_source_scheduler import CandidateSourceScheduler
from app.subscription_repository import SubscriptionRepository


class CandidateSourceSchedulerTests(unittest.TestCase):
    def build_scheduler(self, directory, *, now, config=None, refresh=None, projector=None):
        repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
        settings = config or {
            "douban": {"enabled": True, "task_enabled": True, "task_time": "08:30"}
        }
        scheduler = CandidateSourceScheduler(
            repository,
            refresh or (lambda **_kwargs: {
                "success": True,
                "sourceCounts": {"succeeded": 2, "failed": 0},
                "candidates": {"added": 3, "updated": 4},
                "skipped": 1,
            }),
            lambda: settings,
            legacy_projector=projector,
            clock=lambda: now,
        )
        return repository, scheduler

    def test_due_run_is_applied_once_and_projects_complete_state(self):
        with tempfile.TemporaryDirectory() as directory:
            projected = []
            repository, scheduler = self.build_scheduler(
                directory,
                now=datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc),
                projector=lambda state: projected.append(state),
            )

            first = scheduler.run_due()
            second = scheduler.run_due()
            state = repository.get_candidate_scheduler_state()

            self.assertTrue(first["ran"])
            self.assertEqual(first["status"], "success")
            self.assertFalse(second["ran"])
            self.assertEqual(second["status"], "not_due")
            self.assertEqual(state["lastAttemptedScheduleKey"], "2026-08-01@08:30")
            self.assertEqual(state["lastSuccessAt"], "2026-08-01T01:00:00Z")
            self.assertEqual(state["nextRunAt"], "2026-08-02T00:30:00Z")
            self.assertEqual(len(projected), 1)

    def test_disabled_scheduler_does_not_refresh_or_report_overdue(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            _, scheduler = self.build_scheduler(
                directory,
                now=datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc),
                config={"douban": {"enabled": True, "task_enabled": False, "task_time": "08:30"}},
                refresh=lambda **_kwargs: calls.append(True),
            )

            result = scheduler.run_due()
            state = scheduler.snapshot()

            self.assertFalse(result["ran"])
            self.assertEqual(result["status"], "disabled")
            self.assertEqual(calls, [])
            self.assertEqual(state["state"], "disabled")
            self.assertFalse(state["overdue"])
            self.assertNotIn("lastAttemptedScheduleKey", state)
            self.assertNotIn("scheduleKey", state)
            self.assertNotIn("version", state)

    def test_partial_source_failure_keeps_success_counts_and_records_error(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, scheduler = self.build_scheduler(
                directory,
                now=datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc),
                refresh=lambda **_kwargs: {
                    "success": True,
                    "sourceCounts": {"succeeded": 1, "failed": 1},
                    "candidates": {"added": 2, "updated": 0},
                    "skipped": 3,
                },
            )

            result = scheduler.run_due()["result"]
            state = repository.get_candidate_scheduler_state()

            self.assertEqual(result["status"], "partial_failure")
            self.assertEqual(result["sourceCounts"], {"succeeded": 1, "failed": 1})
            self.assertEqual(state["lastSuccessAt"], "")
            self.assertEqual(state["lastResult"]["addedCandidates"], 2)
            self.assertEqual(state["lastError"], "候选来源更新存在失败")

    def test_manual_and_scheduled_runs_share_one_nonblocking_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            entered = threading.Event()
            release = threading.Event()

            def refresh(**_kwargs):
                entered.set()
                release.wait(timeout=5)
                return {
                    "success": True,
                    "sourceCounts": {"succeeded": 1, "failed": 0},
                    "candidates": {},
                }

            _, scheduler = self.build_scheduler(
                directory,
                now=datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc),
                refresh=refresh,
            )
            results = []
            worker = threading.Thread(target=lambda: results.append(scheduler.run(trigger="manual")))
            worker.start()
            self.assertTrue(entered.wait(timeout=5))

            duplicate = scheduler.run(trigger="scheduled")
            release.set()
            worker.join(timeout=5)

            self.assertEqual(duplicate["status"], "already_running")
            self.assertEqual(duplicate["code"], "CANDIDATE_REFRESH_ALREADY_RUNNING")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "success")

    def test_interrupted_schedule_is_not_automatically_retried_after_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, scheduler = self.build_scheduler(
                directory,
                now=datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc),
            )
            repository.claim_candidate_refresh(
                run_id="interrupted",
                schedule_key="2026-08-01@08:30",
                enabled=True,
                started_at="2026-08-01T00:31:00Z",
                next_run_at="2026-08-01T00:30:00Z",
            )

            recovered = scheduler.recover()
            result = scheduler.run_due()

            self.assertEqual(recovered["lastError"], "上次候选来源更新中断")
            self.assertFalse(result["ran"])
            self.assertEqual(result["status"], "not_due")


if __name__ == "__main__":
    unittest.main()
