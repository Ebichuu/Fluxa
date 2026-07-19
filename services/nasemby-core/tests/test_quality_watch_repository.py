from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.quality_watch_repository import (
    ExternalJobConflict,
    QualityWatchRepository,
    QualityWatchVersionConflict,
)


class QualityWatchRepositoryTests(unittest.TestCase):
    def test_watch_units_keep_independent_fixed_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3", clock=lambda: now[0])
            first = repository.ensure_watch_unit("tv:202", "tv", 1, 1, window_hours=48)
            ready = repository.mark_baseline_ready(first["unit_key"])
            self.assertEqual(ready["baseline_ready_at"], "2026-07-18T01:00:00.000Z")
            self.assertEqual(ready["next_check_at"], "2026-07-18T13:00:00.000Z")
            self.assertEqual(ready["observation_ends_at"], "2026-07-20T01:00:00.000Z")

            now[0] += timedelta(hours=6)
            unchanged = repository.mark_baseline_ready(first["unit_key"])
            self.assertEqual(unchanged["observation_ends_at"], ready["observation_ends_at"])
            second = repository.ensure_watch_unit("tv:202", "tv", 1, 2, window_hours=24)
            second_ready = repository.mark_baseline_ready(second["unit_key"])
            self.assertNotEqual(first["unit_key"], second["unit_key"])
            self.assertEqual(second_ready["observation_ends_at"], "2026-07-19T07:00:00.000Z")
            self.assertEqual(repository.get_watch_unit(first["unit_key"])["observation_ends_at"], ready["observation_ends_at"])

            blocked = repository.ensure_watch_unit("tv:202", "tv", 1, None)
            self.assertEqual(blocked["state"], "blocked")
            self.assertEqual(repository.mark_baseline_ready(blocked["unit_key"])["state"], "blocked")
            updated = repository.update_watch_unit(
                first["unit_key"], ready["version"], state="search_due", current_evidence_json={"source": "rss"}
            )
            self.assertEqual(updated["state"], "search_due")
            self.assertEqual(updated["current_evidence"], {"source": "rss"})
            with self.assertRaises(QualityWatchVersionConflict):
                repository.update_watch_unit(first["unit_key"], ready["version"], state="paused")

    def test_action_claims_use_leases_idempotency_cooldown_and_external_job_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3", clock=lambda: now[0])
            first = repository.claim_action("idem-action-0001", "tv:202", "torra", "rewash-analysis")
            self.assertEqual(first["disposition"], "claimed")
            self.assertEqual(
                repository.claim_action("idem-action-0001", "tv:202", "torra", "rewash-analysis")["disposition"],
                "in_progress",
            )
            now[0] += timedelta(seconds=61)
            reclaimed = repository.claim_action("idem-action-0001", "tv:202", "torra", "rewash-analysis")
            self.assertEqual(reclaimed["disposition"], "reclaimed")
            action_id = reclaimed["action"]["action_id"]
            repository.save_external_job(action_id, "job-101")
            with self.assertRaises(ExternalJobConflict):
                repository.save_external_job(action_id, "job-other")
            now[0] += timedelta(seconds=61)
            resumed = repository.claim_action("idem-action-0001", "tv:202", "torra", "rewash-analysis")
            self.assertEqual(resumed["disposition"], "resume")
            self.assertEqual(resumed["action"]["external_job_id"], "job-101")
            completed = repository.complete_action(
                action_id, "succeeded", {"message": "完成"}, http_status=200
            )
            self.assertEqual(completed["status"], "succeeded")
            replayed = repository.claim_action("idem-action-0001", "tv:202", "torra", "rewash-analysis")
            self.assertEqual(replayed["disposition"], "replay")
            conflict = repository.claim_action("idem-action-0001", "movie:9", "torra", "rewash-analysis")
            self.assertEqual(conflict["disposition"], "conflict")

            unit_first = repository.claim_action(
                "idem-unit-action", "tv:202", "torra", "rewash-analysis", unit_key="tv:202:s1:e1"
            )
            self.assertEqual(unit_first["disposition"], "claimed")
            unit_conflict = repository.claim_action(
                "idem-unit-action", "tv:202", "torra", "rewash-analysis", unit_key="tv:202:s1:e2"
            )
            self.assertEqual(unit_conflict["disposition"], "conflict")

            cooldown = repository.claim_action(
                "idem-action-0002", "tv:202", "torra", "rewash-analysis", cooldown_seconds=300
            )
            self.assertEqual(cooldown["disposition"], "cooldown")
            now[0] += timedelta(seconds=301)
            self.assertEqual(
                repository.claim_action(
                    "idem-action-0002", "tv:202", "torra", "rewash-analysis", cooldown_seconds=300
                )["disposition"],
                "claimed",
            )

    def test_terminal_actions_cannot_return_to_running_or_change_terminal_result(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            claimed = repository.claim_action("terminal-action", "movie:9", "torra", "rewash-analysis")
            action_id = claimed["action"]["action_id"]
            completed = repository.complete_action(action_id, "succeeded", {"message": "完成"})

            with self.assertRaises(ExternalJobConflict):
                repository.save_external_job(action_id, "late-job")
            same = repository.complete_action(action_id, "succeeded", {"message": "迟到结果"})
            self.assertEqual(same["response_summary"], completed["response_summary"])
            with self.assertRaises(ExternalJobConflict):
                repository.complete_action(action_id, "failed", {"message": "迟到失败"})

    def test_action_rate_limits_are_atomic_and_cooldown_is_scoped_to_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
            repository = QualityWatchRepository(
                Path(directory) / "media_control_center.sqlite3", clock=lambda: now[0]
            )
            first = repository.claim_action(
                "rate-first-action",
                "tv:202",
                "torra",
                "rewash-analysis",
                unit_key="tv:202:s1:e1",
                cooldown_seconds=300,
                rate_limits={"hourly": 1, "daily": 3},
            )
            self.assertEqual(first["disposition"], "claimed")
            other_unit = repository.claim_action(
                "same-subscription-other-unit",
                "tv:202",
                "torra",
                "rewash-analysis",
                unit_key="tv:202:s1:e2",
                cooldown_seconds=300,
            )
            self.assertEqual(other_unit["disposition"], "claimed")
            limited = repository.claim_action(
                "rate-second-action",
                "tv:303",
                "torra",
                "rewash-analysis",
                unit_key="tv:303:s1:e1",
                rate_limits={"hourly": 1, "daily": 3},
            )
            self.assertEqual((limited["disposition"], limited["window"]), ("rate_limited", "hourly"))

            now[0] += timedelta(hours=1, seconds=1)
            second = repository.claim_action(
                "rate-second-action",
                "tv:303",
                "torra",
                "rewash-analysis",
                unit_key="tv:303:s1:e1",
                rate_limits={"hourly": 1, "daily": 3},
            )
            self.assertEqual(second["disposition"], "claimed")
            daily = repository.claim_action(
                "rate-third-action",
                "tv:404",
                "torra",
                "rewash-analysis",
                unit_key="tv:404:s1:e1",
                rate_limits={"hourly": 4, "daily": 3},
            )
            self.assertEqual((daily["disposition"], daily["window"]), ("rate_limited", "daily"))

    def test_action_claim_enforces_global_inflight_limit_inside_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            first = repository.claim_action(
                "global-slot-first",
                "tv:101",
                "torra",
                "rewash-analysis",
                unit_key="tv:101:s1:e1",
                require_idle=True,
            )
            second = repository.claim_action(
                "global-slot-second",
                "tv:202",
                "torra",
                "rewash-analysis",
                unit_key="tv:202:s1:e1",
                require_idle=True,
            )
            self.assertEqual(first["disposition"], "claimed")
            self.assertEqual(second["disposition"], "global_busy")
            self.assertEqual(second["action"]["action_id"], first["action"]["action_id"])

            repository.complete_action(first["action"]["action_id"], "succeeded")
            retried = repository.claim_action(
                "global-slot-second",
                "tv:202",
                "torra",
                "rewash-analysis",
                unit_key="tv:202:s1:e1",
                require_idle=True,
            )
            self.assertEqual(retried["disposition"], "claimed")


    def test_scheduler_state_uses_optimistic_version(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            first = repository.save_scheduler_state("quality-watch", {"cursor": 1})
            second = repository.save_scheduler_state("quality-watch", {"cursor": 2}, expected_version=first["version"])
            self.assertEqual(second["payload"], {"cursor": 2})
            with self.assertRaises(QualityWatchVersionConflict):
                repository.save_scheduler_state("quality-watch", {"cursor": 3}, expected_version=first["version"])


if __name__ == "__main__":
    unittest.main()
