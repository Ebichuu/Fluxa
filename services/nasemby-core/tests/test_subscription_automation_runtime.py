from __future__ import annotations

import json
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.main import create_app
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository
from app.rss_subscription_match_runtime import RssAnalysisDependencies, RssSubscriptionMatchRuntime
from app.subscription_automation_api_runtime import AutomationApiError
from app.subscription_automation_runtime import (
    SubscriptionAutomationDependencies,
    SubscriptionAutomationService,
)
from app.torra_quality_runtime import TorraQualityClient


class FakeTorra:
    def __init__(self):
        self.rows = [{"id": "torra-202", "is_running": False, "is_mutating": False}]
        self.analyses = []
        self.downloads = []
        self.jobs = {}

    def is_configured(self):
        return True

    def list_subscriptions(self):
        return list(self.rows)

    def submit_analysis(self, subscription_id):
        self.analyses.append(subscription_id)
        job_id = f"analysis-job-{len(self.analyses)}"
        self.jobs[job_id] = {"status": "pending", "result": None}
        return job_id

    def submit_download(self, subscription_id, analysis_id, selected):
        self.downloads.append((subscription_id, analysis_id, dict(selected)))
        job_id = f"download-job-{len(self.downloads)}"
        self.jobs[job_id] = {"status": "pending", "result": None}
        return job_id

    def get_job(self, job_id):
        return self.jobs[job_id]

    @staticmethod
    def select_upgrade_candidates(job):
        return TorraQualityClient.select_upgrade_candidates(job)


class FakeQb:
    def summary(self):
        return {"connected": True, "tasks": []}


def success_job():
    return {
        "status": "success",
        "result": {
            "analysis_id": "analysis-202",
            "rows": [{
                "row_id": "row-1",
                "library_meta_weight_score": 10,
                "candidates": [{
                    "candidate_id": "candidate-private",
                    "is_upgrade": True,
                    "meta_weight_score": 20,
                }],
            }],
        },
    }


class SubscriptionAutomationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
        database = Path(self.directory.name) / "media_control_center.sqlite3"
        self.repository = QualityWatchRepository(database, clock=lambda: self.now[0])
        self.rss = PrivateRssRepository(database)
        self.environment = {
            "NASEMBY_CORE_WRITE_ENABLED": "true",
            "MCC_TORRA_QUALITY_WATCH_ENABLED": "true",
            "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "false",
        }
        self.config = {
            "torra_quality_watch_enabled": True,
            "torra_quality_default_window_hours": 48,
            "torra_quality_schedule_json": [720, 1440, 2880],
            "torra_quality_min_interval_minutes": 60,
            "torra_quality_hourly_limit": 4,
            "torra_quality_daily_limit": 30,
            "torra_quality_scheduler_batch_size": 2,
        }
        self.subscriptions = [{
            "key": "tv:202",
            "title": "测试剧",
            "media_type": "tv",
            "tmdb_id": "202",
            "target_season": 1,
        }]
        self.torra = FakeTorra()
        self.qb = FakeQb()
        unit = self.repository.ensure_watch_unit(
            "tv:202", "tv", 1, 1, window_hours=48, torra_subscription_id="torra-202"
        )
        self.unit = self.repository.mark_baseline_ready(unit["unit_key"])
        self.rss_runtime = RssSubscriptionMatchRuntime(
            self.rss,
            self.repository,
            lambda: {"items": self.subscriptions},
            clock=lambda: self.now[0],
            analysis=RssAnalysisDependencies(self.environment, self.torra, self.qb, lambda: self.config),
        )
        self.service = SubscriptionAutomationService(SubscriptionAutomationDependencies(
            self.environment,
            self.repository,
            self.torra,
            self.qb,
            lambda: self.config,
            self._save_config,
            lambda: {"items": self.subscriptions},
            self._update_subscription,
            rss_runtime=self.rss_runtime,
            clock=lambda: self.now[0],
        ))
        self.app = create_app(
            access_environment=self.environment,
            private_rss_repository=self.rss,
            quality_watch_repository=self.repository,
            torra_quality_client=self.torra,
            subscription_automation_service=self.service,
        )
        self.client = self.app.test_client()

    def _save_config(self, value):
        self.config.clear()
        self.config.update(value)
        return self.config

    def _update_subscription(self, key, updater):
        item = next((value for value in self.subscriptions if value["key"] == key), None)
        if not item:
            return None
        updater(item)
        return item

    def test_settings_validate_and_persist_effective_deadline_schedule(self):
        current = self.client.get("/api/v2/subscription-automation/settings")
        self.assertEqual(current.status_code, 200)
        updated = self.client.patch("/api/v2/subscription-automation/settings", json={
            "enabled": True,
            "defaultWindowHours": 24,
            "scheduleMinutes": [30, 120],
            "minIntervalMinutes": 60,
            "hourlyLimit": 5,
            "dailyLimit": 40,
            "batchSize": 2,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["scheduleMinutes"], [30, 120, 1440])
        invalid = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"defaultWindowHours": 24, "scheduleMinutes": [120, 30]},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("request_id", invalid.get_json())

    def test_get_routes_do_not_change_settings_units_or_call_providers(self):
        config_before = copy.deepcopy(self.config)
        unit_before = self.repository.get_watch_unit(self.unit["unit_key"])

        settings = self.client.get("/api/v2/subscription-automation/settings")
        watch = self.client.get("/api/v2/subscriptions/tv:202/quality-watch")

        self.assertEqual((settings.status_code, watch.status_code), (200, 200))
        self.assertEqual(self.config, config_before)
        self.assertEqual(self.repository.get_watch_unit(self.unit["unit_key"]), unit_before)
        self.assertEqual((self.torra.analyses, self.torra.downloads), ([], []))

    def test_quality_watch_reads_updates_policy_and_pauses_then_resumes(self):
        status = self.client.get("/api/v2/subscriptions/tv:202/quality-watch")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["units"][0]["id"], self.unit["unit_key"])
        paused = self.client.patch(
            "/api/v2/subscriptions/tv:202/quality-watch",
            json={"paused": True, "windowHours": 24, "scheduleMinutes": [30, 1440]},
        )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(self.repository.get_watch_unit(self.unit["unit_key"])["state"], "paused")
        resumed = self.client.patch("/api/v2/subscriptions/tv:202/quality-watch", json={"paused": False})
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(self.repository.get_watch_unit(self.unit["unit_key"])["state"], "observing_upgrade")
        self.assertEqual(self.subscriptions[0]["torra_quality_watch"]["window_hours"], 24)

    def test_manual_analysis_is_gated_idempotent_async_and_redacted(self):
        self.environment["MCC_TORRA_QUALITY_WATCH_ENABLED"] = "false"
        disabled = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-manual-0001", "unitId": self.unit["unit_key"]},
        )
        self.assertEqual(disabled.status_code, 503)
        self.environment["MCC_TORRA_QUALITY_WATCH_ENABLED"] = "true"
        accepted = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-manual-0001", "unitId": self.unit["unit_key"]},
        )
        self.assertEqual(accepted.status_code, 202)
        self.assertTrue(accepted.headers["Location"].startswith("/api/v2/automation-actions/"))
        replayed = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-manual-0001", "unitId": self.unit["unit_key"]},
        )
        self.assertEqual(replayed.status_code, 202)
        self.assertEqual(len(self.torra.analyses), 1)
        action_id = accepted.get_json()["id"]
        action = self.repository.get_action(action_id)
        self.torra.jobs[action["external_job_id"]] = success_job()
        self.now[0] += timedelta(seconds=61)
        self.service.resume_action(action)
        public = self.client.get(accepted.headers["Location"])
        serialized = public.get_data(as_text=True)
        self.assertEqual(public.get_json()["result"]["selectedCount"], 1)
        self.assertNotIn("candidate-private", serialized)

    def test_download_uses_server_selection_and_independent_confirmation_gate(self):
        analysis = self.repository.claim_action(
            "analysis-ready-0001",
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
        )
        analysis_id = analysis["action"]["action_id"]
        self.repository.complete_action(analysis_id, "succeeded", {
            "analysisId": "analysis-private",
            "selectedCandidates": {"row-private": "candidate-private"},
            "selectedCount": 1,
        })
        body = {
            "confirm": True,
            "idempotencyKey": "download-manual-0001",
            "analysisActionId": analysis_id,
            "unitId": self.unit["unit_key"],
        }
        disabled = self.client.post("/api/v2/subscriptions/tv:202/torra-rewashes", json=body)
        self.assertEqual(disabled.status_code, 503)
        recovery = self.repository.claim_action(
            "download-recovery-0001",
            "tv:recovery",
            "torra",
            "rewash-download",
            unit_key="tv:recovery:s1:e1",
            request_summary={"source": "manual-subscription", "analysisActionId": analysis_id},
        )
        self.now[0] += timedelta(seconds=61)
        with self.assertRaises(AutomationApiError):
            self.service.resume_action(recovery["action"])
        self.repository.complete_action(recovery["action"]["action_id"], "cancelled")
        self.now[0] -= timedelta(seconds=61)
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        missing_confirmation = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewashes", json={**body, "confirm": False}
        )
        self.assertEqual(missing_confirmation.status_code, 422)
        accepted = self.client.post("/api/v2/subscriptions/tv:202/torra-rewashes", json=body)
        self.assertEqual(accepted.status_code, 202)
        self.assertTrue(accepted.headers["Location"].startswith("/api/v2/automation-actions/"))
        replayed = self.client.post("/api/v2/subscriptions/tv:202/torra-rewashes", json=body)
        self.assertEqual(replayed.status_code, 202)
        self.assertEqual(replayed.get_json()["id"], accepted.get_json()["id"])
        self.assertEqual(
            self.torra.downloads,
            [("torra-202", "analysis-private", {"row-private": "candidate-private"})],
        )
        self.assertNotIn("candidate-private", accepted.get_data(as_text=True))

    def test_rss_match_manual_analysis_uses_idempotency_without_collection_gate(self):
        source = self.rss.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
        self.rss.upsert_items(source["id"], [{"fingerprint": "manual-rss", "title": "测试剧 S01E01"}])
        item = self.rss.search_items()["items"][0]
        match = self.rss.create_match(item["id"], "tv:202", self.unit["unit_key"], {"identity": {"basis": "title"}})
        self.environment["MCC_PRIVATE_RSS_ENABLED"] = "false"
        accepted = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewash-analyses",
            json={"idempotencyKey": "rss-manual-analysis-0001"},
        )
        self.assertEqual(accepted.status_code, 202)
        self.assertTrue(accepted.headers["Location"].startswith("/api/v2/automation-actions/"))
        replayed = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewash-analyses",
            json={"idempotencyKey": "rss-manual-analysis-0001"},
        )
        self.assertEqual(replayed.status_code, 202)
        self.assertEqual(replayed.get_json()["id"], accepted.get_json()["id"])
        self.assertEqual(self.torra.analyses, ["torra-202"])

        self.rss.upsert_items(source["id"], [{"fingerprint": "manual-rss-2", "title": "测试剧 S01E01 v2"}])
        other_item = next(candidate for candidate in self.rss.search_items()["items"] if candidate["id"] != item["id"])
        other_match = self.rss.create_match(
            other_item["id"], "tv:202", self.unit["unit_key"], {"identity": {"basis": "title"}}
        )
        conflict = self.client.post(
            f"/api/v2/rss-matches/{other_match['id']}/torra-rewash-analyses",
            json={"idempotencyKey": "rss-manual-analysis-0001"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["code"], "TORRA_REWASH_IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.torra.analyses, ["torra-202"])

    def test_action_errors_use_contract_statuses_and_error_envelope(self):
        cases = []
        invalid = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "short"},
        )
        cases.append((invalid, 422, "TORRA_REWASH_IDEMPOTENCY_INVALID"))

        self.environment["MCC_TORRA_QUALITY_WATCH_ENABLED"] = "false"
        disabled = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-disabled-0001"},
        )
        cases.append((disabled, 503, "TORRA_REWASH_ANALYSIS_DISABLED"))
        self.environment["MCC_TORRA_QUALITY_WATCH_ENABLED"] = "true"

        self.torra.rows[0]["is_running"] = True
        busy = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-busy-000001"},
        )
        cases.append((busy, 409, "TORRA_REWASH_BUSY"))
        self.torra.rows[0]["is_running"] = False

        configured = self.torra.is_configured
        self.torra.is_configured = lambda: False
        upstream = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-upstream-001"},
        )
        cases.append((upstream, 502, "TORRA_REWASH_UPSTREAM_UNAVAILABLE"))
        self.torra.is_configured = configured

        prior = self.repository.claim_action(
            "analysis-rate-prior",
            "tv:prior",
            "torra",
            "rewash-analysis",
            unit_key="tv:prior:s1:e1",
        )
        self.repository.complete_action(prior["action"]["action_id"], "succeeded")
        self.config["torra_quality_hourly_limit"] = 1
        limited = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-limited-0001"},
        )
        cases.append((limited, 429, "TORRA_REWASH_RATE_LIMITED"))

        for response, status, code in cases:
            with self.subTest(status=status, code=code):
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json()["code"], code)
                self.assertEqual(set(response.get_json()), {"code", "error", "request_id"})

    def test_new_routes_require_auth_and_origin(self):
        environment = {
            **self.environment,
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
        }
        protected = create_app(
            access_environment=environment,
            quality_watch_repository=self.repository,
            subscription_automation_service=self.service,
        ).test_client()
        self.assertEqual(protected.get("/api/v2/subscription-automation/settings").status_code, 401)
        login = protected.post("/auth/login", data={"access_key": environment["MCC_ACCESS_KEY"]})
        self.assertEqual(login.status_code, 303)
        denied = protected.patch(
            "/api/v2/subscription-automation/settings",
            json={"enabled": False},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()["code"], "ORIGIN_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
