from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.quality_watch_bridge_runtime import QualityWatchBridgeRuntime
from app.quality_watch_repository import QualityWatchRepository
from app.quality_watch_runtime import QualityWatchRuntime


NOW = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)


def subscription():
    return {
        "key": "tv:202",
        "media_type": "tv",
        "tmdb_id": 202,
        "target_season": 1,
        "torra_remote_id": "torra-202",
    }


def snapshot(stage="qb", occurred_at="2026-07-31T01:01:00Z"):
    artifact = "artifact:stable-file-1"
    target = "tv:tmdb:202:season:1:episode:1"
    return {
        "items": [{
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "202",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "targetKey": target,
            "identityState": "linked",
            "sourceIds": {
                "subscriptionId": "tv:202",
                "torraId": "torra-202",
                "qbHashes": ["hash-1"],
                "symediaIds": ["symedia-1"],
            },
            "artifactKeys": [artifact],
            "evidenceOwnership": [{
                "artifactKey": artifact,
                "ownerTargetKey": target,
                "matchMethod": "artifact_exact",
            }],
            "pipelineFacts": [{
                "stage": stage,
                "state": "succeeded",
                "scope": "file",
                "evidence": "verified",
                "eventAt": occurred_at,
                "sourceRef": f"{stage}-result-1",
                "reasonCode": f"{stage.upper()}_SUCCEEDED",
                "units": [{
                    "unitKey": artifact,
                    "state": "succeeded",
                    "scope": "file",
                    "evidence": "verified",
                    "eventAt": occurred_at,
                    "sourceRef": f"{stage}-result-1",
                }],
            }],
        }],
    }


class QualityWatchBridgeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [NOW]
        self.repository = QualityWatchRepository(
            Path(self.directory.name) / "media.sqlite3",
            clock=lambda: self.now[0],
        )
        self.quality = QualityWatchRuntime(
            self.repository,
            config_loader=lambda: {
                "torra_quality_watch_enabled": True,
                "torra_quality_lifecycle_mode": "follow_rss",
                "torra_quality_default_window_hours": 48,
            },
            clock=lambda: self.now[0],
        )
        self.bridge = QualityWatchBridgeRuntime(
            self.repository,
            self.quality,
            subscription_loader=lambda: {"items": [subscription()]},
            config_loader=self.quality.config_loader,
            clock=lambda: self.now[0],
        )

    def test_activated_at_is_written_once_across_mode_changes(self):
        shadow = self.bridge.set_mode("shadow")
        self.now[0] += timedelta(days=1)
        self.bridge.set_mode("off")
        reapplied = self.bridge.set_mode("shadow")

        self.assertEqual(shadow["activatedAt"], "2026-07-31T01:00:00.000Z")
        self.assertEqual(reapplied["activatedAt"], shadow["activatedAt"])

        upgraded = self.repository.set_bridge_mode("off", bridge_version="2")
        self.assertEqual(upgraded["bridgeVersion"], "2")
        self.assertEqual(upgraded["activatedAt"], shadow["activatedAt"])

    def test_shadow_keeps_stage_specific_receipts_without_units(self):
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=3)
        qb = self.bridge.process_snapshot(snapshot("qb"))
        symedia = self.bridge.process_snapshot(snapshot("symedia", "2026-07-31T01:02:00Z"))
        receipts = self.repository.list_bridge_receipts()

        self.assertEqual((qb["pending"], symedia["historical"]), (1, 1))
        self.assertEqual({row["stage"] for row in receipts}, {"qb", "symedia"})
        self.assertEqual(len({row["receipt_key"] for row in receipts}), 2)
        self.assertEqual(self.repository.list_watch_units("tv:202"), [])
        summary = self.bridge.summary()
        self.assertEqual(summary["receiptTotal"], 2)
        self.assertEqual(summary["receiptCounts"]["pending"], 1)
        self.assertEqual(summary["receiptCounts"]["historical"], 1)

    def test_apply_creates_from_qb_then_symedia_advances_baseline(self):
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=2)
        self.bridge.process_snapshot(snapshot("qb"))
        self.bridge.set_mode("apply")

        created = self.bridge.process_snapshot(snapshot("qb"))
        unit = self.repository.list_watch_units("tv:202")[0]

        self.assertEqual(created["applied"], 1)
        self.assertEqual(unit["state"], "waiting_library_baseline")
        self.now[0] += timedelta(minutes=3)
        advanced = self.bridge.process_snapshot(snapshot("symedia", "2026-07-31T01:03:00Z"))
        unit = self.repository.list_watch_units("tv:202")[0]

        self.assertEqual(advanced["applied"], 1)
        self.assertEqual(unit["state"], "observing_upgrade")
        self.assertEqual(unit["baseline_ready_at"], "2026-07-31T01:03:00.000Z")

    def test_fact_at_activation_watermark_is_historical(self):
        self.bridge.set_mode("shadow")
        result = self.bridge.process_snapshot(snapshot("qb", "2026-07-31T01:00:00Z"))

        self.assertEqual(result["historical"], 1)
        self.assertEqual(self.repository.list_bridge_receipts()[0]["status"], "historical")

    def test_apply_failure_rolls_back_unit_and_retries_after_backoff(self):
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=2)
        self.bridge.process_snapshot(snapshot("qb"))
        self.bridge.set_mode("apply")
        original = self.repository.apply_reconcile_plan

        def fail_apply(*_args, **_kwargs):
            raise RuntimeError("injected failure")

        self.repository.apply_reconcile_plan = fail_apply
        failed = self.bridge.process_snapshot(snapshot("qb"))

        self.assertEqual(failed["retryable_failed"], 1)
        self.assertEqual(self.repository.list_watch_units("tv:202"), [])
        receipt = self.repository.list_bridge_receipts()[0]
        self.assertEqual((receipt["status"], receipt["attempt_count"]), ("retryable_failed", 1))

        self.repository.apply_reconcile_plan = original
        deferred = self.bridge.process_snapshot(snapshot("qb"))
        self.assertEqual(deferred["retryable_failed"], 1)
        self.assertEqual(self.repository.list_watch_units("tv:202"), [])

        self.now[0] += timedelta(minutes=5)
        applied = self.bridge.process_snapshot(snapshot("qb"))
        self.assertEqual(applied["applied"], 1)
        self.assertEqual(len(self.repository.list_watch_units("tv:202")), 1)


if __name__ == "__main__":
    unittest.main()
