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
from app.subscription_reconciliation_runtime import torra_public_subscription_key
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
    def __init__(self):
        self.tasks = []

    def summary(self):
        return {"connected": True, "tasks": list(self.tasks)}


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

    def _rss_match_with_analysis(self, suffix):
        source = self.rss.save_source({
            "name": f"RSS {suffix}",
            "feedUrl": f"https://tracker.example/{suffix}.xml",
        })
        self.rss.upsert_items(source["id"], [{
            "fingerprint": f"rss-download-{suffix}",
            "title": f"测试剧 S01E01 {suffix}",
        }])
        item = self.rss.search_items(source_id=source["id"])["items"][0]
        match = self.rss.create_match(
            item["id"],
            "tv:202",
            self.unit["unit_key"],
            {"identity": {"basis": "title"}},
        )
        claimed = self.repository.claim_action(
            f"rss-download-analysis-{suffix}",
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )
        analysis_id = claimed["action"]["action_id"]
        self.repository.complete_action(analysis_id, "succeeded", {
            "analysisId": f"analysis-{suffix}",
            "selectedCandidates": {f"row-{suffix}": f"candidate-{suffix}"},
            "selectedCount": 1,
        })
        self.rss.update_match(match["id"], "triggered", analysis_id)
        return match, analysis_id

    def test_settings_validate_and_persist_effective_deadline_schedule(self):
        current = self.client.get("/api/v2/subscription-automation/settings")
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.get_json()["lifecycleMode"], "follow_rss")
        self.assertFalse(current.get_json()["missingFallbackEnabled"])
        self.assertEqual(current.get_json()["analysisState"], "collecting")
        self.assertEqual(current.get_json()["executionMode"], "disabled")
        self.assertFalse(current.get_json()["executionEnvironmentEnabled"])
        self.assertEqual(current.get_json()["automaticEligibleCount"], 0)
        self.assertEqual(current.get_json()["baselineCounts"], {
            "total": 1,
            "ready": 1,
            "pending": 0,
            "missing": 0,
            "conflict": 0,
            "expired": 0,
        })
        updated = self.client.patch("/api/v2/subscription-automation/settings", json={
            "enabled": True,
            "missingFallbackEnabled": True,
            "lifecycleMode": "fixed_window",
            "defaultWindowHours": 24,
            "scheduleMinutes": [30, 120],
            "minIntervalMinutes": 60,
            "hourlyLimit": 5,
            "dailyLimit": 40,
            "batchSize": 2,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["lifecycleMode"], "fixed_window")
        self.assertTrue(updated.get_json()["missingFallbackEnabled"])
        self.assertEqual(updated.get_json()["scheduleMinutes"], [30, 120, 1440])
        invalid = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"defaultWindowHours": 24, "scheduleMinutes": [120, 30]},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("request_id", invalid.get_json())
        invalid_mode = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"lifecycleMode": "legacy"},
        )
        self.assertEqual(invalid_mode.status_code, 422)
        invalid_fallback = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"missingFallbackEnabled": "true"},
        )
        self.assertEqual(invalid_fallback.status_code, 422)
        unconfirmed_execution = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"executionMode": "manual"},
        )
        self.assertEqual(unconfirmed_execution.status_code, 422)
        automatic_execution = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"executionMode": "automatic", "executionModeConfirm": True},
        )
        self.assertEqual(automatic_execution.status_code, 422)
        manual_execution = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"executionMode": "manual", "executionModeConfirm": True},
        )
        self.assertEqual(manual_execution.status_code, 200)
        self.assertEqual(manual_execution.get_json()["executionMode"], "manual")
        disabled_execution = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"executionMode": "disabled", "executionModeConfirm": True},
        )
        self.assertEqual(disabled_execution.status_code, 200)
        self.assertEqual(disabled_execution.get_json()["executionMode"], "disabled")

    def test_settings_analysis_state_uses_only_local_candidate_projection(self):
        source = self.rss.save_source({
            "name": "Contract RSS",
            "feedUrl": "https://tracker.example/contract.xml",
        })
        self.rss.upsert_items(source["id"], [{
            "fingerprint": "contract-upgrade",
            "title": "测试剧 S01E01 2160p",
        }])
        item = self.rss.search_items(source_id=source["id"])["items"][0]
        match = self.rss.create_match(
            item["id"], "tv:202", self.unit["unit_key"],
            {
                "mediaType": "tv",
                "season": {"item": 1, "unit": 1},
                "episode": {"start": 1, "end": 1, "unit": 1},
            },
        )

        scoring = self.client.get("/api/v2/subscription-automation/settings").get_json()
        self.assertEqual(scoring["analysisState"], "scoring")

        self.rss.set_match_binding(
            match["id"],
            torra_subscription_id="torra-202",
            target_key="tv:tmdb:202:season:1:episodes:1-1",
            artifact_key="rss:contract-upgrade",
        )
        self.rss.save_match_evaluation([match["id"]], {
            "ruleId": "rule-1",
            "ruleHash": "rule-hash-1",
            "candidateScore": 90,
            "baselineScore": 70,
            "status": "scored",
            "decision": "current_best",
        })
        self.rss.save_candidate_decisions([{
            "matchIds": [match["id"]],
            "decision": "current_best",
            "reason": "higher_score",
            "bestCandidate": True,
        }])
        self.config["torra_quality_execution_mode"] = "manual"

        ready = self.client.get("/api/v2/subscription-automation/settings").get_json()

        self.assertEqual(ready["analysisState"], "ready")
        self.assertEqual(ready["executionMode"], "manual")
        self.assertEqual(ready["automaticEligibleCount"], 1)
        self.assertEqual((self.torra.analyses, self.torra.downloads), ([], []))

        self.environment["MCC_TORRA_QUALITY_WATCH_ENABLED"] = "false"
        disabled = self.client.get("/api/v2/subscription-automation/settings").get_json()
        self.assertEqual(disabled["analysisState"], "disabled")

    def test_bridge_rollout_and_baseline_preview_routes_are_local_and_auditable(self):
        summary = self.client.get("/api/v2/subscription-automation/bridge-summary")
        unconfirmed_shadow = self.client.patch(
            "/api/v2/subscription-automation/settings", json={"bridgeMode": "shadow"}
        )
        apply_without_shadow = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"bridgeMode": "apply", "bridgeModeConfirm": True},
        )
        shadow = self.client.patch(
            "/api/v2/subscription-automation/settings",
            json={"bridgeMode": "shadow", "bridgeModeConfirm": True},
        )
        preview = self.client.post(
            "/api/v2/subscription-automation/baseline-initialization-previews", json={}
        )
        audit = self.client.get(preview.headers["Location"])

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.get_json()["mode"], "off")
        self.assertEqual(unconfirmed_shadow.status_code, 422)
        self.assertEqual(apply_without_shadow.status_code, 409)
        self.assertIn("request_id", apply_without_shadow.get_json())
        self.assertEqual(shadow.status_code, 200)
        self.assertEqual(shadow.get_json()["bridgeMode"], "shadow")
        self.assertTrue(shadow.get_json()["missingFallbackEnabled"] is False)
        self.assertEqual(preview.status_code, 201)
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.get_json()["runId"], preview.get_json()["runId"])
        self.assertEqual((self.torra.analyses, self.torra.downloads), ([], []))

        invalid_preview = self.client.post(
            "/api/v2/subscription-automation/baseline-initialization-previews",
            json={"refresh": True},
        )
        invalid_execute = self.client.post(
            "/api/v2/subscription-automation/baseline-initializations",
            json={"confirm": True},
        )
        self.assertEqual((invalid_preview.status_code, invalid_execute.status_code), (422, 422))
        self.assertIn("request_id", invalid_execute.get_json())

        self.environment["NASEMBY_CORE_WRITE_ENABLED"] = "false"
        write_disabled = self.client.post(
            "/api/v2/subscription-automation/baseline-initialization-previews", json={}
        )
        self.assertEqual(write_disabled.status_code, 503)

    def test_quality_watch_projects_latest_missing_episode_fallback_action(self):
        self.config["torra_quality_missing_fallback_enabled"] = True
        claim = self.repository.claim_action(
            "missing-episode-fallback:tv:202:s1:e2,3",
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key="tv:202:s1:missing-fallback",
            request_summary={
                "source": "missing-episode-fallback",
                "tmdbId": "202",
                "seasonNumber": 1,
                "episodeNumbers": [2, 3],
            },
        )

        queued = self.client.get("/api/v2/subscriptions/tv:202/quality-watch").get_json()

        self.assertEqual(queued["missingFallback"]["state"], "queued")
        self.assertEqual(queued["missingFallback"]["episodeNumbers"], [2, 3])
        self.assertTrue(queued["missingFallback"]["enabled"])

        self.repository.complete_action(
            claim["action"]["action_id"],
            "succeeded",
            {"reason": "missing_episode_fallback_checked", "rowCount": 1},
        )
        checked = self.client.get("/api/v2/subscriptions/tv:202/quality-watch").get_json()

        self.assertEqual(checked["missingFallback"]["state"], "checked")
        self.assertEqual(checked["missingFallback"]["actionId"], claim["action"]["action_id"])

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
            json={
                "paused": True,
                "lifecycleMode": "fixed_window",
                "windowHours": 24,
                "scheduleMinutes": [30, 1440],
            },
        )
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(self.repository.get_watch_unit(self.unit["unit_key"])["state"], "paused")
        resumed = self.client.patch("/api/v2/subscriptions/tv:202/quality-watch", json={"paused": False})
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(self.repository.get_watch_unit(self.unit["unit_key"])["state"], "observing_upgrade")
        self.assertEqual(self.subscriptions[0]["torra_quality_watch"]["window_hours"], 24)
        self.assertEqual(self.subscriptions[0]["torra_quality_watch"]["lifecycle_mode"], "fixed_window")

    def test_torra_only_quality_watch_is_readable_without_local_subscription(self):
        torra_unit = self.repository.ensure_watch_unit(
            "torra:torra-202", "tv", 1, 2, window_hours=48, torra_subscription_id="torra-202"
        )
        torra_unit = self.repository.mark_baseline_ready(torra_unit["unit_key"])
        public_key = torra_public_subscription_key("torra-202")
        public_unit_key = torra_unit["unit_key"].replace("torra:torra-202", public_key, 1)

        response = self.client.get(f"/api/v2/subscriptions/{public_key}/quality-watch")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["readOnly"])
        self.assertEqual(response.get_json()["subscriptionId"], public_key)
        self.assertEqual(response.get_json()["units"][0]["id"], public_unit_key)
        self.assertNotIn("torra-202", response.get_data(as_text=True))

        analysis = self.client.post(
            f"/api/v2/subscriptions/{public_key}/torra-rewash-analyses",
            json={"idempotencyKey": "torra-public-analysis-0001", "unitId": public_unit_key},
        )
        self.assertEqual(analysis.status_code, 202)
        self.assertEqual(self.torra.analyses, ["torra-202"])
        self.assertEqual(analysis.get_json()["subscriptionId"], public_key)
        self.assertEqual(analysis.get_json()["unitId"], public_unit_key)
        self.assertNotIn("torra-202", analysis.get_data(as_text=True))
        stored = self.repository.get_action(analysis.get_json()["id"])
        self.assertEqual(stored["subscription_key"], "torra:torra-202")
        self.assertEqual(stored["unit_key"], torra_unit["unit_key"])
        self.assertEqual(stored["request_summary"]["unitId"], torra_unit["unit_key"])

        missing_units = self.client.get("/api/v2/subscriptions/torra:unknown/quality-watch")
        self.assertEqual(missing_units.status_code, 404)
        self.assertEqual(missing_units.get_json()["code"], "SUBSCRIPTION_NOT_FOUND")

        collision_remote_id = public_key.removeprefix("torra:")
        self.torra.rows.append({"id": collision_remote_id, "is_running": False, "is_mutating": False})
        collision = self.client.get(f"/api/v2/subscriptions/{public_key}/quality-watch")
        self.assertEqual(collision.status_code, 409)
        self.assertEqual(collision.get_json()["code"], "TORRA_SUBSCRIPTION_KEY_CONFLICT")

        update = self.client.patch(
            f"/api/v2/subscriptions/{public_key}/quality-watch",
            json={"paused": True},
        )
        self.assertEqual(update.status_code, 404)

    def test_imported_torra_mirror_uses_canonical_observation_keys(self):
        for index, storage_mode in enumerate(("public", "legacy"), start=2):
            with self.subTest(storage_mode=storage_mode):
                remote_id = f"torra-mirror-{storage_mode}"
                public_key = torra_public_subscription_key(remote_id)
                stored_key = public_key if storage_mode == "public" else f"torra:{remote_id}"
                canonical_key = f"torra:{remote_id}"
                self.torra.rows.append({
                    "id": remote_id,
                    "media_type": "tv",
                    "tmdb_id": str(300 + index),
                    "season_number": 1,
                    "is_running": False,
                    "is_mutating": False,
                })
                self.subscriptions.append({
                    "key": stored_key,
                    "subscription_key": stored_key,
                    "title": f"镜像剧 {storage_mode}",
                    "media_type": "tv",
                    "tmdb_id": str(300 + index),
                    "target_season": 1,
                    "origin": "torra",
                    "read_only": True,
                    "torra_remote_id": remote_id,
                })
                unit = self.repository.ensure_watch_unit(
                    canonical_key,
                    "tv",
                    1,
                    index,
                    window_hours=48,
                    torra_subscription_id=remote_id,
                )
                unit = self.repository.mark_baseline_ready(unit["unit_key"])
                public_unit_key = unit["unit_key"].replace(canonical_key, public_key, 1)

                status = self.client.get(f"/api/v2/subscriptions/{public_key}/quality-watch")
                paused = self.client.patch(
                    f"/api/v2/subscriptions/{public_key}/quality-watch",
                    json={"paused": True},
                )

                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.get_json()["subscriptionId"], public_key)
                self.assertEqual(status.get_json()["units"][0]["id"], public_unit_key)
                self.assertEqual(paused.status_code, 200)
                self.assertEqual(self.repository.get_watch_unit(unit["unit_key"])["state"], "paused")
                self.assertIn("torra_quality_watch", self.subscriptions[-1])
                self.assertNotIn(remote_id, status.get_data(as_text=True))
                self.assertNotIn(remote_id, paused.get_data(as_text=True))

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
        self.assertEqual(replayed.get_json()["id"], accepted.get_json()["id"])
        self.assertEqual(len(self.torra.analyses), 1)
        action_id = accepted.get_json()["id"]
        action = self.repository.get_action(action_id)
        self.torra.jobs[action["external_job_id"]] = success_job()
        self.now[0] += timedelta(seconds=61)
        self.service.resume_action(action)
        public = self.client.get(accepted.headers["Location"])
        serialized = public.get_data(as_text=True)
        self.assertEqual(public.get_json()["result"]["selectedCount"], 1)
        self.assertEqual(public.get_json()["result"]["upgradeOptions"][0]["scoreGain"], 10.0)
        self.assertNotIn("candidate-private", serialized)

    def test_manual_analysis_rejects_cross_source_idempotency_collision(self):
        idempotency_key = "analysis-cross-source-collision"
        existing = self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": "rss-match-other"},
        )["action"]
        self.repository.complete_action(existing["action_id"], "cancelled")

        response = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": idempotency_key, "unitId": self.unit["unit_key"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_IDEMPOTENCY_CONFLICT")
        self.assertEqual(self.torra.analyses, [])

    def test_manual_analysis_yields_to_missing_fallback_provider_slot(self):
        self.repository.claim_action(
            "missing-episode-fallback:tv:202:s1:e2",
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key="tv:202:s1:missing-fallback",
            request_summary={
                "source": "missing-episode-fallback",
                "tmdbId": "202",
                "seasonNumber": 1,
                "episodeNumbers": [2],
            },
        )

        response = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": "analysis-manual-provider-slot", "unitId": self.unit["unit_key"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_BUSY")
        self.assertEqual(self.torra.analyses, [])

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

        other_analysis = self.repository.claim_action(
            "analysis-ready-0002",
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
        )["action"]
        self.repository.complete_action(other_analysis["action_id"], "succeeded", {
            "analysisId": "analysis-other",
            "selectedCandidates": {"row-other": "candidate-other"},
            "selectedCount": 1,
        })
        conflict = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewashes",
            json={**body, "analysisActionId": other_analysis["action_id"]},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["code"], "TORRA_REWASH_IDEMPOTENCY_CONFLICT")
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

    def test_rss_match_download_requires_confirmation_and_replays_without_resubmitting(self):
        match, analysis_id = self._rss_match_with_analysis("success")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        body = {
            "confirm": True,
            "idempotencyKey": "rss-download-success-0001",
            "analysisActionId": analysis_id,
        }

        unconfirmed = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewashes",
            json={**body, "confirm": False},
        )
        self.assertEqual(unconfirmed.status_code, 422)
        self.assertEqual(unconfirmed.get_json()["code"], "TORRA_REWASH_CONFIRMATION_REQUIRED")

        accepted = self.client.post(f"/api/v2/rss-matches/{match['id']}/torra-rewashes", json=body)
        replayed = self.client.post(f"/api/v2/rss-matches/{match['id']}/torra-rewashes", json=body)

        self.assertEqual((accepted.status_code, replayed.status_code), (202, 202))
        self.assertEqual(replayed.get_json()["id"], accepted.get_json()["id"])
        self.assertEqual(
            self.torra.downloads,
            [("torra-202", "analysis-success", {"row-success": "candidate-success"})],
        )
        stored = self.rss.get_match(match["id"])
        self.assertEqual(stored["status"], "confirmed")
        self.assertEqual(stored["triggerActionId"], accepted.get_json()["id"])
        listed = self.client.get("/api/v2/rss-matches?status=confirmed").get_json()["items"]
        self.assertEqual(listed[0]["triggerActionId"], accepted.get_json()["id"])
        self.assertNotIn("candidate-success", accepted.get_data(as_text=True))

    def test_rss_exact_download_preview_is_read_only_and_rejects_unknown_fields(self):
        calls = []

        def preview(match_id):
            calls.append(match_id)
            if match_id == "missing":
                return {"status": "missing"}
            return {
                "status": "blocked",
                "ready": False,
                "capabilityState": "unsupported",
                "matchId": match_id,
                "targetKey": "tv:tmdb:202:season:1:episodes:1-1",
                "versionSummary": "Test.Show.S01E01.2160p.mkv",
                "candidateScore": 30,
                "baselineScore": 10,
                "scoreGain": 20,
                "blockers": [{
                    "code": "TORRA_EXACT_RESOURCE_ENDPOINT_UNAVAILABLE",
                    "message": "Torra 未提供订阅绑定的指定 RSS 资源入口",
                }],
                "observedAt": "2026-07-18T01:00:00Z",
            }

        self.rss_runtime.preview_exact_download = preview
        response = self.client.post(
            "/api/v2/rss-matches/public-match/exact-download-previews",
            json={},
        )
        invalid = self.client.post(
            "/api/v2/rss-matches/public-match/exact-download-previews",
            json={"downloadUrl": "https://tracker.example/private"},
        )
        invalid_shape = self.client.post(
            "/api/v2/rss-matches/public-match/exact-download-previews",
            json=[],
        )
        missing = self.client.post(
            "/api/v2/rss-matches/missing/exact-download-previews",
            json={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["ready"])
        self.assertEqual(response.get_json()["capabilityState"], "unsupported")
        self.assertEqual(
            response.get_json()["blockers"][0]["code"],
            "TORRA_EXACT_RESOURCE_ENDPOINT_UNAVAILABLE",
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["code"], "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID")
        self.assertEqual(invalid_shape.status_code, 422)
        self.assertEqual(invalid_shape.get_json()["code"], "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["code"], "RSS_MATCH_NOT_FOUND")
        self.assertEqual(calls, ["public-match", "missing"])
        self.assertEqual((self.torra.analyses, self.torra.downloads), ([], []))

    def test_rss_artifact_exact_download_routes_reject_client_routing_and_redact_action(self):
        calls = []
        claimed = self.repository.claim_action(
            "rss-exact-api-test",
            "torra:remote-private-id",
            "qbittorrent",
            "rss-exact-download",
            unit_key="rss-artifact:public-group",
            request_summary={"source": "manual-rss-artifact"},
        )["action"]
        action = self.repository.complete_action(
            claimed["action_id"],
            "succeeded",
            {
                "accepted": True,
                "savePath": "/downloads/private",
                "downloadUrl": "https://tracker.example/download?passkey=private",
                "groupId": "rss-artifact:public-group",
            },
        )

        def preview(group_id):
            calls.append(("preview", group_id))
            return ({
                "status": "ready",
                "ready": True,
                "capabilityState": "ready",
                "groupId": group_id,
                "matchId": "public-match",
                "episodeLabel": "S01E01–E02",
                "coveredUnitCount": 2,
                "candidateScore": 30,
                "baselineScore": 10,
                "scoreGain": 20,
                "downloadCategory": "anime",
                "downloadCategoryConfigured": True,
                "destinationConfigured": True,
                "previewToken": "public-preview-token",
                "expiresAt": "2026-07-18T01:10:00Z",
                "blockers": [],
                "observedAt": "2026-07-18T01:00:00Z",
            }, "fingerprint", ["public-match"])

        def execute(group_id, preview_token, idempotency_key):
            calls.append(("execute", group_id, preview_token, idempotency_key))
            return action

        self.rss_runtime.preview_artifact_exact_download = preview
        self.rss_runtime.execute_artifact_exact_download = execute
        preview_response = self.client.post(
            "/api/v2/rss-artifact-groups/public-group/exact-download-previews",
            json={},
        )
        injected = self.client.post(
            "/api/v2/rss-artifact-groups/public-group/exact-downloads",
            json={
                "confirm": True,
                "previewToken": "public-preview-token",
                "idempotencyKey": "manual-request-0001",
                "savePath": "/downloads/other",
            },
        )
        accepted = self.client.post(
            "/api/v2/rss-artifact-groups/public-group/exact-downloads",
            json={
                "confirm": True,
                "previewToken": "public-preview-token",
                "idempotencyKey": "manual-request-0001",
            },
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.get_json()["coveredUnitCount"], 2)
        self.assertEqual(injected.status_code, 422)
        self.assertEqual(injected.get_json()["code"], "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID")
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.get_json()["type"], "rss-exact-download")
        public_text = accepted.get_data(as_text=True)
        self.assertNotIn("remote-private-id", public_text)
        self.assertNotIn("/downloads/private", public_text)
        self.assertNotIn("tracker.example", public_text)
        self.assertNotIn("passkey", public_text)
        self.assertEqual(calls, [
            ("preview", "public-group"),
            ("execute", "public-group", "public-preview-token", "manual-request-0001"),
        ])

    def test_rss_resource_download_routes_reject_client_routing_and_redact_action(self):
        calls = []
        claimed = self.repository.claim_action(
            "rss-resource-api-test",
            "rss-item:public-item",
            "qbittorrent",
            "rss-resource-download",
            unit_key="rss-item:public-item",
            request_summary={"source": "manual-rss-resource", "itemId": "public-item"},
        )["action"]
        action = self.repository.complete_action(
            claimed["action_id"],
            "succeeded",
            {
                "accepted": True,
                "savePath": "/downloads/private",
                "downloadUrl": "https://tracker.example/download?passkey=private",
                "itemId": "public-item",
            },
        )

        def preview(item_id):
            calls.append(("preview", item_id))
            return ({
                "status": "ready",
                "ready": True,
                "capabilityState": "ready",
                "itemId": item_id,
                "mediaType": "tv",
                "scopeLabel": "S01 季包",
                "categoryKey": "tv_western",
                "categoryLabel": "欧美剧",
                "categoryDirectory": "04-欧美剧",
                "classificationReason": "沿用唯一 Torra 订阅的八分类",
                "routeSource": "torra_subscription",
                "subscriptionMatched": True,
                "destinationConfigured": True,
                "previewToken": "resource-preview-token",
                "expiresAt": "2026-07-18T01:10:00Z",
                "blockers": [],
                "observedAt": "2026-07-18T01:00:00Z",
            }, "fingerprint")

        def execute(item_id, preview_token, idempotency_key):
            calls.append(("execute", item_id, preview_token, idempotency_key))
            return action

        self.rss_runtime.preview_resource_download = preview
        self.rss_runtime.execute_resource_download = execute
        preview_response = self.client.post(
            "/api/v2/rss-items/public-item/download-previews",
            json={},
        )
        injected = self.client.post(
            "/api/v2/rss-items/public-item/downloads",
            json={
                "confirm": True,
                "previewToken": "resource-preview-token",
                "idempotencyKey": "resource-request-0001",
                "savePath": "/downloads/other",
            },
        )
        accepted = self.client.post(
            "/api/v2/rss-items/public-item/downloads",
            json={
                "confirm": True,
                "previewToken": "resource-preview-token",
                "idempotencyKey": "resource-request-0001",
            },
        )

        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response.get_json()["categoryDirectory"], "04-欧美剧")
        self.assertEqual(injected.status_code, 422)
        self.assertEqual(injected.get_json()["code"], "SUBSCRIPTION_AUTOMATION_FIELDS_INVALID")
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.get_json()["type"], "rss-resource-download")
        public_text = accepted.get_data(as_text=True)
        self.assertNotIn("/downloads/private", public_text)
        self.assertNotIn("tracker.example", public_text)
        self.assertNotIn("passkey", public_text)
        self.assertEqual(calls, [
            ("preview", "public-item"),
            ("execute", "public-item", "resource-preview-token", "resource-request-0001"),
        ])

    def test_rss_match_download_rejects_expired_observation_window(self):
        match, analysis_id = self._rss_match_with_analysis("expired-window")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        self.now[0] += timedelta(hours=49)

        response = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": "rss-download-expired-window",
                "analysisActionId": analysis_id,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "QUALITY_WATCH_WINDOW_EXPIRED")
        self.assertEqual(self.torra.downloads, [])

    def test_rss_match_download_rejects_at_observation_deadline(self):
        match, analysis_id = self._rss_match_with_analysis("window-deadline")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        self.now[0] = datetime.fromisoformat(
            self.unit["observation_ends_at"].replace("Z", "+00:00")
        )

        response = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": "rss-download-window-deadline",
                "analysisActionId": analysis_id,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "QUALITY_WATCH_WINDOW_EXPIRED")
        self.assertEqual(self.torra.downloads, [])

    def test_reclaimed_download_rechecks_torra_before_resubmitting(self):
        match, analysis_id = self._rss_match_with_analysis("reclaimed-torra")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        idempotency_key = "rss-download-reclaimed-torra"
        self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis_id,
            },
        )
        self.now[0] += timedelta(seconds=61)
        self.torra.rows[0]["is_running"] = True

        response = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": idempotency_key,
                "analysisActionId": analysis_id,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_BUSY")
        self.assertEqual(self.torra.downloads, [])
        stored = self.repository.get_action_by_idempotency(idempotency_key)
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(
            self.repository.find_inflight_action("torra", "rewash-download")["action_id"],
            stored["action_id"],
        )

    def test_reclaimed_download_rechecks_qb_before_resubmitting(self):
        match, analysis_id = self._rss_match_with_analysis("reclaimed-qb")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        idempotency_key = "rss-download-reclaimed-qb"
        self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis_id,
            },
        )
        self.now[0] += timedelta(seconds=61)
        self.qb.tasks = [{
            "name": f"{self.subscriptions[0]['title']} S01E01",
            "status": "downloading",
        }]

        response = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": idempotency_key,
                "analysisActionId": analysis_id,
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_QB_BUSY")
        self.assertEqual(self.torra.downloads, [])
        stored = self.repository.get_action_by_idempotency(idempotency_key)
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(
            self.repository.find_inflight_action("torra", "rewash-download")["action_id"],
            stored["action_id"],
        )

    def test_reclaimed_download_http_cancels_expired_window(self):
        _match, analysis_id = self._rss_match_with_analysis("http-expired-window")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        idempotency_key = "download-http-reclaim-expired"
        action = self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis_id,
            },
        )["action"]
        self.now[0] += timedelta(hours=49)

        response = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": idempotency_key,
                "analysisActionId": analysis_id,
                "unitId": self.unit["unit_key"],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "QUALITY_WATCH_WINDOW_EXPIRED")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "QUALITY_WATCH_WINDOW_EXPIRED")
        self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-download"))
        self.assertEqual(self.torra.downloads, [])

    def test_reclaimed_download_http_cancels_missing_torra_subscription(self):
        _match, analysis_id = self._rss_match_with_analysis("http-missing-torra")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        idempotency_key = "download-http-reclaim-missing-torra"
        action = self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis_id,
            },
        )["action"]
        self.torra.rows = []
        self.now[0] += timedelta(seconds=61)

        response = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": idempotency_key,
                "analysisActionId": analysis_id,
                "unitId": self.unit["unit_key"],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_SUBSCRIPTION_MISSING")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "TORRA_REWASH_SUBSCRIPTION_MISSING")
        self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-download"))
        self.assertEqual(self.torra.downloads, [])

    def test_reclaimed_analysis_http_cancels_missing_torra_subscription(self):
        idempotency_key = "analysis-http-reclaim-missing-torra"
        action = self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
            },
        )["action"]
        self.torra.rows = []
        self.now[0] += timedelta(seconds=61)

        response = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": idempotency_key, "unitId": self.unit["unit_key"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_SUBSCRIPTION_MISSING")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "TORRA_REWASH_SUBSCRIPTION_MISSING")
        self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-analysis"))
        self.assertEqual(self.torra.analyses, [])

    def test_reclaimed_analysis_http_keeps_temporary_provider_error_retryable(self):
        idempotency_key = "analysis-http-reclaim-torra-busy"
        action = self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
            },
        )["action"]
        self.torra.rows[0]["is_running"] = True
        self.now[0] += timedelta(seconds=61)

        response = self.client.post(
            "/api/v2/subscriptions/tv:202/torra-rewash-analyses",
            json={"idempotencyKey": idempotency_key, "unitId": self.unit["unit_key"]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "TORRA_REWASH_BUSY")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(
            self.repository.find_inflight_action("torra", "rewash-analysis")["action_id"],
            stored["action_id"],
        )
        self.assertEqual(self.torra.analyses, [])

    def test_reclaimed_download_resume_rejects_expired_observation_window(self):
        _match, analysis_id = self._rss_match_with_analysis("resume-expired-window")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        idempotency_key = "rss-download-resume-expired"
        claimed = self.repository.claim_action(
            idempotency_key,
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis_id,
            },
        )["action"]
        self.now[0] += timedelta(hours=49)

        with self.assertRaises(AutomationApiError) as raised:
            self.service.resume_action(claimed)

        self.assertEqual(raised.exception.code, "QUALITY_WATCH_WINDOW_EXPIRED")
        self.assertEqual(self.torra.downloads, [])
        stored = self.repository.get_action(claimed["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "QUALITY_WATCH_WINDOW_EXPIRED")
        self.assertEqual(stored["response_summary"]["reason"], "reclaim_context_invalid")
        self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-download"))

    def test_reclaimed_download_resume_cancels_missing_subscription_and_unit(self):
        cases = [
            ("missing-subscription", "tv:missing", self.unit["unit_key"], "SUBSCRIPTION_NOT_FOUND"),
            ("missing-unit", "tv:202", "tv:202:missing", "QUALITY_WATCH_UNIT_NOT_FOUND"),
        ]
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        for suffix, subscription_key, unit_key, expected_code in cases:
            with self.subTest(suffix=suffix):
                action = self.repository.claim_action(
                    f"download-reclaim-{suffix}",
                    subscription_key,
                    "torra",
                    "rewash-download",
                    unit_key=unit_key,
                    request_summary={
                        "source": "manual-subscription",
                        "unitId": unit_key,
                        "analysisActionId": "analysis-missing",
                    },
                )["action"]
                self.now[0] += timedelta(seconds=61)

                with self.assertRaises(AutomationApiError) as raised:
                    self.service.resume_action(action)

                self.assertEqual(raised.exception.code, expected_code)
                stored = self.repository.get_action(action["action_id"])
                self.assertEqual(stored["status"], "cancelled")
                self.assertEqual(stored["error_code"], expected_code)
                self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-download"))

    def test_reclaimed_download_resume_cancels_unusable_analysis(self):
        analysis = self.repository.claim_action(
            "analysis-unusable-for-reclaim",
            "tv:202",
            "torra",
            "rewash-analysis",
            unit_key=self.unit["unit_key"],
        )["action"]
        self.repository.complete_action(analysis["action_id"], "failed")
        action = self.repository.claim_action(
            "download-reclaim-unusable-analysis",
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis["action_id"],
            },
        )["action"]
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        self.now[0] += timedelta(seconds=61)

        with self.assertRaises(AutomationApiError) as raised:
            self.service.resume_action(action)

        self.assertEqual(raised.exception.code, "TORRA_ANALYSIS_ACTION_NOT_READY")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "TORRA_ANALYSIS_ACTION_NOT_READY")
        self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-download"))

    def test_reclaimed_download_resume_cancels_missing_analysis(self):
        action = self.repository.claim_action(
            "download-reclaim-missing-analysis",
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": "analysis-does-not-exist",
            },
        )["action"]
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        self.now[0] += timedelta(seconds=61)

        with self.assertRaises(AutomationApiError) as raised:
            self.service.resume_action(action)

        self.assertEqual(raised.exception.code, "TORRA_ANALYSIS_ACTION_NOT_FOUND")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "TORRA_ANALYSIS_ACTION_NOT_FOUND")
        self.assertIsNone(self.repository.find_inflight_action("torra", "rewash-download"))

    def test_reclaimed_download_resume_keeps_temporary_provider_error_retryable(self):
        match, analysis_id = self._rss_match_with_analysis("resume-torra-busy")
        self.assertIsNotNone(match)
        action = self.repository.claim_action(
            "download-reclaim-resume-torra-busy",
            "tv:202",
            "torra",
            "rewash-download",
            unit_key=self.unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "unitId": self.unit["unit_key"],
                "analysisActionId": analysis_id,
            },
        )["action"]
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"
        self.torra.is_configured = lambda: False
        self.now[0] += timedelta(seconds=61)

        with self.assertRaises(AutomationApiError) as torra_error:
            self.service.resume_action(action)

        self.assertEqual(torra_error.exception.code, "TORRA_REWASH_UPSTREAM_UNAVAILABLE")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(
            self.repository.find_inflight_action("torra", "rewash-download")["action_id"],
            stored["action_id"],
        )

        self.torra.is_configured = lambda: True
        self.qb.summary = lambda: {"connected": False, "tasks": []}
        self.now[0] += timedelta(seconds=61)
        with self.assertRaises(AutomationApiError) as qb_error:
            self.service.resume_action(stored)

        self.assertEqual(qb_error.exception.code, "TORRA_REWASH_UPSTREAM_UNAVAILABLE")
        stored = self.repository.get_action(action["action_id"])
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(
            self.repository.find_inflight_action("torra", "rewash-download")["action_id"],
            stored["action_id"],
        )
        self.assertEqual(self.torra.downloads, [])

    def test_rss_match_download_rejects_analysis_from_another_match(self):
        match, _analysis_id = self._rss_match_with_analysis("target")
        _other_match, other_analysis_id = self._rss_match_with_analysis("other")
        self.environment["MCC_TORRA_REWASH_DOWNLOAD_ENABLED"] = "true"

        rejected = self.client.post(
            f"/api/v2/rss-matches/{match['id']}/torra-rewashes",
            json={
                "confirm": True,
                "idempotencyKey": "rss-download-wrong-analysis",
                "analysisActionId": other_analysis_id,
            },
        )

        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(rejected.get_json()["code"], "TORRA_ANALYSIS_ACTION_NOT_FOUND")
        self.assertEqual(self.torra.downloads, [])
        self.assertEqual(self.rss.get_match(match["id"])["status"], "triggered")

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
