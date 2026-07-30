from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.quality_watch_baseline_init_runtime import (
    BaselineInitializationError,
    QualityWatchBaselineInitializationService,
)
from app.quality_watch_repository import QualityWatchRepository
from app.quality_watch_runtime import QualityWatchRuntime


NOW = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)


def subscription():
    return {
        "key": "tv:202",
        "title": "测试剧",
        "media_type": "tv",
        "tmdb_id": 202,
        "target_season": 1,
        "torra_remote_id": "torra-202",
    }


def historical_snapshot(artifact="artifact:history-1", occurred_at="2026-07-20T01:00:00Z"):
    target = "tv:tmdb:202:season:1:episode:1"
    return {"items": [{
        "title": "测试剧",
        "mediaType": "tv",
        "tmdbId": "202",
        "seasonNumber": 1,
        "episodeNumber": 1,
        "targetKey": target,
        "identityState": "linked",
        "sourceIds": {"subscriptionId": "tv:202", "torraId": "torra-202"},
        "artifactKeys": [artifact],
        "evidenceOwnership": [{
            "artifactKey": artifact,
            "ownerTargetKey": target,
            "matchMethod": "artifact_exact",
        }],
        "pipelineFacts": [{
            "stage": "symedia",
            "state": "succeeded",
            "scope": "file",
            "evidence": "verified",
            "eventAt": occurred_at,
            "sourceRef": "symedia-history-1",
            "reasonCode": "SYMEDIA_ORGANIZED",
            "units": [{
                "unitKey": artifact,
                "state": "succeeded",
                "scope": "file",
                "evidence": "verified",
                "eventAt": occurred_at,
                "sourceRef": "symedia-history-1",
            }],
        }],
    }]}


class EmptyResourceRepository:
    def list_quality_watch_success_evidence(self, limit=5000):
        return []


class QualityWatchBaselineInitializationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repository = QualityWatchRepository(Path(self.directory.name) / "media.sqlite3", clock=lambda: NOW)
        self.quality = QualityWatchRuntime(
            self.repository,
            config_loader=lambda: {
                "torra_quality_lifecycle_mode": "follow_rss",
                "torra_quality_default_window_hours": 48,
            },
            clock=lambda: NOW,
        )
        self.snapshot = [historical_snapshot()]
        self.service = QualityWatchBaselineInitializationService(
            self.repository,
            EmptyResourceRepository(),
            self.quality,
            snapshot_loader=lambda: self.snapshot[0],
            subscription_loader=lambda: {"items": [subscription()]},
            config_loader=self.quality.config_loader,
            clock=lambda: NOW,
        )

    def test_preview_persists_then_apply_uses_historical_time_and_expires(self):
        preview = self.service.preview()

        self.assertEqual(preview["counts"]["safeToInitialize"], 1)
        self.assertEqual(preview["status"], "previewed")
        self.assertTrue(preview["groups"][0]["id"].startswith("baseline-group:"))
        self.assertNotIn("tv:202", preview["groups"][0]["id"])
        serialized_preview = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn("torra-202", serialized_preview)
        self.assertNotIn("artifact:history-1", serialized_preview)
        self.assertNotIn("tv:tmdb:202:season:1:episode:1", serialized_preview)
        self.assertNotIn("symedia-history-1", serialized_preview)
        target_id = preview["groups"][0]["items"][0]["id"]
        result = self.service.execute({
            "confirm": True,
            "runId": preview["runId"],
            "previewFingerprint": preview["previewFingerprint"],
            "selectedTargetIds": [target_id],
            "idempotencyKey": "baseline-init-history-0001",
        })

        self.assertEqual((result["processed"], result["initialized"], result["expired"]), (1, 0, 1))
        unit = self.repository.list_watch_units("tv:202")[0]
        self.assertEqual(unit["state"], "observation_expired")
        self.assertEqual(unit["first_success_at"], "2026-07-20T01:00:00.000Z")
        self.assertEqual(unit["baseline_ready_at"], "2026-07-20T01:00:00.000Z")
        replay = self.service.execute({
            "confirm": True,
            "runId": preview["runId"],
            "previewFingerprint": preview["previewFingerprint"],
            "selectedTargetIds": [target_id],
            "idempotencyKey": "baseline-init-history-0001",
        })
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.repository.list_watch_units("tv:202")), 1)
        public_run = self.service.get_run(preview["runId"])
        self.assertEqual(public_run["items"][0]["result"], "observation_expired")
        self.assertNotIn("ownerTargetKey", public_run["items"][0])

    def test_any_preview_drift_rolls_back_whole_batch_and_marks_stale(self):
        preview = self.service.preview()
        target_id = preview["groups"][0]["items"][0]["id"]
        self.snapshot[0] = historical_snapshot(artifact="artifact:changed")

        with self.assertRaises(BaselineInitializationError) as raised:
            self.service.execute({
                "confirm": True,
                "runId": preview["runId"],
                "previewFingerprint": preview["previewFingerprint"],
                "selectedTargetIds": [target_id],
                "idempotencyKey": "baseline-init-history-0002",
            })

        self.assertEqual(raised.exception.code, "BASELINE_INITIALIZATION_PREVIEW_STALE")
        self.assertEqual(self.repository.list_watch_units("tv:202"), [])
        self.assertEqual(self.repository.get_baseline_init_run(preview["runId"])["status"], "stale")

    def test_execute_rejects_more_than_two_hundred_selected_targets(self):
        preview = self.service.preview()
        with self.assertRaises(BaselineInitializationError) as raised:
            self.service.execute({
                "confirm": True,
                "runId": preview["runId"],
                "previewFingerprint": preview["previewFingerprint"],
                "selectedTargetIds": [f"target:{index}" for index in range(201)],
                "idempotencyKey": "baseline-init-history-0003",
            })
        self.assertEqual(raised.exception.code, "BASELINE_INITIALIZATION_SELECTION_TOO_LARGE")

    def test_preview_requires_subscription_identity_to_match_evidence(self):
        self.snapshot[0]["items"][0]["tmdbId"] = "999"

        preview = self.service.preview()

        self.assertEqual(preview["counts"]["safeToInitialize"], 0)
        self.assertEqual(preview["counts"]["needsReview"], 1)
        self.assertEqual(preview["groups"][0]["items"][0]["reasonCode"], "identity_conflict")

    def test_preview_stably_orders_multiple_success_facts_at_same_time(self):
        duplicate = dict(self.snapshot[0]["items"][0]["pipelineFacts"][0])
        duplicate["reasonCode"] = "SYMEDIA_ORGANIZED_RECHECKED"
        duplicate["sourceRef"] = "symedia-history-2"
        self.snapshot[0]["items"][0]["pipelineFacts"].append(duplicate)

        preview = self.service.preview()

        self.assertEqual(preview["counts"]["safeToInitialize"], 1)
        self.assertEqual(preview["groups"][0]["items"][0]["baselineReadyAt"], "2026-07-20T01:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
