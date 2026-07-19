from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.quality_watch_repository import QualityWatchRepository
from app.quality_watch_runtime import resolve_watch_policy
from app.quality_watch_scheduler import (
    QualityWatchScheduler,
    QualityWatchSchedulerDependencies,
    deterministic_jitter_minutes,
)
from app.torra_quality_runtime import TorraQualityClient


class FakeTorra:
    def __init__(self):
        self.rows = []
        self.jobs = {}
        self.submissions = []
        self.polls = []

    def is_configured(self):
        return True

    def list_subscriptions(self):
        return list(self.rows)

    def submit_analysis(self, subscription_id):
        self.submissions.append(subscription_id)
        job_id = f"job-{len(self.submissions)}"
        self.jobs.setdefault(job_id, {"status": "pending", "result": None})
        return job_id

    def get_job(self, job_id):
        self.polls.append(job_id)
        return self.jobs[job_id]

    @staticmethod
    def select_upgrade_candidates(job):
        return TorraQualityClient.select_upgrade_candidates(job)


class FakeQb:
    def __init__(self):
        self.calls = 0
        self.tasks = []

    def summary(self):
        self.calls += 1
        return {"connected": True, "tasks": list(self.tasks)}


class FakeRssRuntime:
    def __init__(self):
        self.matches = []
        self.pending = []

    def start_analysis(self, match_id):
        self.matches.append(match_id)
        return {"status": "polling", "actionId": "rss-action"}

    def wake_pending_candidates(self):
        return list(self.pending)


class FakeAutomationRuntime:
    def __init__(self):
        self.actions = []

    def resume_action(self, action):
        self.actions.append(action)
        return {"status": "polling", "actionId": action["action_id"]}


def success_job(selected=False):
    candidates = []
    if selected:
        candidates = [{"candidate_id": "candidate-1", "is_upgrade": True, "meta_weight_score": 20}]
    return {
        "status": "success",
        "result": {
            "analysis_id": "analysis-1",
            "rows": [{
                "row_id": "row-1",
                "library_meta_weight_score": 10,
                "candidates": candidates,
            }],
        },
    }


class QualityWatchSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
        self.repository = QualityWatchRepository(
            Path(self.directory.name) / "media_control_center.sqlite3",
            clock=lambda: self.now[0],
        )
        self.torra = FakeTorra()
        self.qb = FakeQb()
        self.subscriptions = []
        self.config = {
            "torra_quality_watch_enabled": True,
            "torra_quality_default_window_hours": 24,
            "torra_quality_schedule_json": [30, 1440],
            "torra_quality_min_interval_minutes": 60,
            "torra_quality_hourly_limit": 4,
            "torra_quality_daily_limit": 30,
            "torra_quality_scheduler_batch_size": 2,
        }
        self.scheduler = self._scheduler()

    def _scheduler(self, environment=None, rss_runtime=None, automation_runtime=None):
        return QualityWatchScheduler(
            self.repository,
            QualityWatchSchedulerDependencies(
                environment if environment is not None else {"MCC_TORRA_QUALITY_WATCH_ENABLED": "true"},
                self.torra,
                self.qb,
                lambda: {"items": self.subscriptions},
                lambda: self.config,
                rss_runtime=rss_runtime,
                automation_runtime=automation_runtime,
            ),
            clock=lambda: self.now[0],
        )

    def _unit(self, key, episode, torra_id, busy=False):
        subscription = {
            "key": key,
            "title": f"测试剧 {key}",
            "media_type": "tv",
            "tmdb_id": key.split(":")[-1],
            "target_season": 1,
            "torra_quality_window_hours": 24,
        }
        self.subscriptions.append(subscription)
        self.torra.rows.append({"id": torra_id, "is_running": busy, "is_mutating": False})
        unit = self.repository.ensure_watch_unit(
            key,
            "tv",
            1,
            episode,
            window_hours=24,
            torra_subscription_id=torra_id,
        )
        return self.repository.mark_baseline_ready(unit["unit_key"], offsets_minutes=[30, 1440])

    def _make_due(self):
        self.now[0] += timedelta(minutes=46)

    def test_environment_and_sqlite_gates_disable_all_provider_calls(self):
        self._unit("tv:101", 1, "torra-101")
        self._make_due()
        disabled = self._scheduler(environment={}).run_once()
        self.assertEqual(disabled, {"status": "disabled", "processed": []})
        self.config["torra_quality_watch_enabled"] = False
        self.assertEqual(self.scheduler.run_once()["status"], "disabled")
        self.assertEqual(self.torra.submissions, [])
        self.assertEqual(self.qb.calls, 0)

    def test_due_batch_is_fair_and_keeps_global_analysis_concurrency_at_one(self):
        first = self._unit("tv:101", 1, "torra-101", busy=True)
        self._unit("tv:101", 2, "torra-101", busy=True)
        other = self._unit("tv:202", 1, "torra-202")
        self._make_due()

        result = self.scheduler.run_once()

        self.assertEqual(result["selected"], 2)
        self.assertEqual(
            [item["unitId"] for item in result["processed"]],
            [first["unit_key"], other["unit_key"]],
        )
        self.assertEqual(result["processed"][0]["reason"], "torra_busy")
        self.assertEqual(self.torra.submissions, ["torra-202"])
        inflight = self.repository.find_inflight_action("torra", "rewash-analysis")
        self.assertEqual(inflight["unit_key"], other["unit_key"])

    def test_rss_analysis_in_current_interval_skips_scheduled_check(self):
        unit = self._unit("tv:101", 1, "torra-101")
        claim = self.repository.claim_action(
            "rss-rewash-analysis:match-1",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "private-rss", "matchId": "match-1"},
        )
        self.repository.save_external_job(claim["action"]["action_id"], "rss-job-complete")
        self.repository.complete_action(claim["action"]["action_id"], "succeeded", {"selectedCount": 0})
        self._make_due()

        result = self.scheduler.run_once()

        self.assertEqual(result["processed"][0]["status"], "rss_skipped")
        updated = self.repository.get_watch_unit(unit["unit_key"])
        self.assertEqual(updated["current_offset_index"], 1)
        self.assertEqual(updated["last_result"]["reason"], "rss_analysis_in_interval")
        self.assertEqual(self.torra.submissions, [])

    def test_inflight_rss_job_is_resumed_by_the_shared_scheduler(self):
        unit = self._unit("tv:101", 1, "torra-101")
        claim = self.repository.claim_action(
            "rss-rewash-analysis:match-running",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "private-rss", "matchId": "match-running"},
        )
        self.repository.save_external_job(claim["action"]["action_id"], "rss-job")
        rss_runtime = FakeRssRuntime()

        result = self._scheduler(rss_runtime=rss_runtime).run_once()["processed"][0]

        self.assertEqual(result["status"], "polling")
        self.assertEqual(result["source"], "private-rss")
        self.assertEqual(rss_runtime.matches, ["match-running"])
        self.assertEqual(self.torra.submissions, [])

    def test_sqlite_pause_still_allows_read_only_resume_of_existing_rss_job(self):
        unit = self._unit("tv:101", 1, "torra-101")
        claim = self.repository.claim_action(
            "rss-rewash-analysis:match-paused",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "private-rss", "matchId": "match-paused"},
        )
        self.repository.save_external_job(claim["action"]["action_id"], "rss-job-paused")
        self.config["torra_quality_watch_enabled"] = False
        rss_runtime = FakeRssRuntime()

        result = self._scheduler(rss_runtime=rss_runtime).run_once()["processed"][0]

        self.assertEqual(result["status"], "polling")
        self.assertEqual(rss_runtime.matches, ["match-paused"])

    def test_manual_subscription_analysis_is_resumed_by_the_shared_scheduler(self):
        unit = self._unit("tv:101", 1, "torra-101")
        claim = self.repository.claim_action(
            "manual-analysis-running",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-subscription"},
        )
        self.repository.save_external_job(claim["action"]["action_id"], "manual-analysis-job")
        automation_runtime = FakeAutomationRuntime()

        result = self._scheduler(automation_runtime=automation_runtime).run_once()["processed"][0]

        self.assertEqual((result["source"], result["status"]), ("manual-subscription", "polling"))
        self.assertEqual(automation_runtime.actions, [self.repository.get_action(claim["action"]["action_id"])])
        self.assertEqual(self.torra.submissions, [])

    def test_manual_subscription_download_is_resumed_by_the_shared_scheduler(self):
        unit = self._unit("tv:101", 1, "torra-101")
        claim = self.repository.claim_action(
            "manual-download-running",
            unit["subscription_key"],
            "torra",
            "rewash-download",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-subscription", "analysisActionId": "analysis-action"},
        )
        self.repository.save_external_job(claim["action"]["action_id"], "manual-download-job")
        automation_runtime = FakeAutomationRuntime()

        result = self._scheduler(automation_runtime=automation_runtime).run_once()["processed"][0]

        self.assertEqual((result["source"], result["status"]), ("manual-subscription", "polling"))
        self.assertEqual(automation_runtime.actions[0]["action_type"], "rewash-download")
        self.assertEqual(self.torra.submissions, [])

    def test_manual_rss_analysis_is_resumed_by_the_shared_scheduler(self):
        unit = self._unit("tv:101", 1, "torra-101")
        claim = self.repository.claim_action(
            "manual-rss-analysis-running",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": "match-manual"},
        )
        self.repository.save_external_job(claim["action"]["action_id"], "manual-rss-job")
        automation_runtime = FakeAutomationRuntime()

        result = self._scheduler(automation_runtime=automation_runtime).run_once()["processed"][0]

        self.assertEqual((result["source"], result["status"]), ("manual-rss", "polling"))
        self.assertEqual(automation_runtime.actions[0]["request_summary"]["matchId"], "match-manual")
        self.assertEqual(self.torra.submissions, [])

    def test_pending_rss_candidate_is_prioritized_before_scheduled_fallback(self):
        self._unit("tv:101", 1, "torra-101")
        self._make_due()
        rss_runtime = FakeRssRuntime()
        rss_runtime.pending = [{"matchId": "match-next", "status": "submitted", "actionId": "rss-next"}]

        result = self._scheduler(rss_runtime=rss_runtime).run_once()

        self.assertEqual(result["processed"], [{
            "source": "private-rss",
            "matchId": "match-next",
            "status": "submitted",
            "actionId": "rss-next",
        }])
        self.assertEqual(self.torra.submissions, [])

    def test_custom_offsets_validate_and_always_keep_the_window_deadline(self):
        policy = resolve_watch_policy(
            {"torra_quality_window_hours": 24, "torra_quality_schedule_json": [30, 120]},
            {},
        )
        self.assertEqual(policy["offsets_minutes"], [30, 120, 1440])
        for invalid in ([120, 30], [30, 30], [29, 120], [30, 1441]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                resolve_watch_policy(
                    {"torra_quality_window_hours": 24, "torra_quality_schedule_json": invalid},
                    {},
                )
        jitter = deterministic_jitter_minutes("tv:101:s1:e1", 0)
        self.assertEqual(jitter, deterministic_jitter_minutes("tv:101:s1:e1", 0))
        self.assertIn(jitter, range(16))

    def test_limits_defer_without_submitting_and_do_not_block_other_rounds(self):
        unit = self._unit("tv:101", 1, "torra-101")
        prior = self.repository.claim_action(
            "prior-analysis",
            "tv:999",
            "torra",
            "rewash-analysis",
            unit_key="tv:999:s1:e1",
        )
        self.repository.complete_action(prior["action"]["action_id"], "succeeded")
        self.config["torra_quality_hourly_limit"] = 4
        self.config["torra_quality_daily_limit"] = 1
        self._make_due()

        result = self.scheduler.run_once()["processed"][0]

        self.assertEqual((result["status"], result["reason"]), ("deferred", "rate_limited"))
        self.assertEqual(self.repository.get_watch_unit(unit["unit_key"])["state"], "search_due")
        self.assertEqual(self.torra.submissions, [])

    def test_deadline_runs_only_final_check_then_expires_after_restart_poll(self):
        unit = self._unit("tv:101", 1, "torra-101")
        self.config["torra_quality_schedule_json"] = [30, 120]
        self.now[0] += timedelta(hours=25)

        submitted = self.scheduler.run_once()["processed"][0]
        self.assertEqual(submitted["status"], "submitted")
        action = self.repository.get_action(submitted["actionId"])
        self.assertEqual(action["request_summary"]["offsetIndex"], 2)
        self.torra.jobs[action["external_job_id"]] = success_job(selected=True)
        self.now[0] += timedelta(seconds=61)

        restarted = self._scheduler().run_once()["processed"][0]

        self.assertEqual(restarted["status"], "upgrade_found")
        self.assertEqual(self.torra.submissions, ["torra-101"])
        self.assertEqual(self.torra.polls, [action["external_job_id"]])
        updated = self.repository.get_watch_unit(unit["unit_key"])
        self.assertEqual(updated["state"], "observation_expired")
        self.assertEqual(updated["last_result"]["selectedCount"], 1)


if __name__ == "__main__":
    unittest.main()
