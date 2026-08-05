from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.quality_watch_bridge_runtime import QualityWatchBridgeRuntime
from app.quality_watch_repository import QualityWatchRepository
from app.quality_watch_runtime import QualityWatchRuntime
from app.task_chain_v2_runtime import adapt_task_chain
from app.torra_subscription_keys import torra_public_subscription_key


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


def production_season_snapshot(stage="qb", occurred_at="2026-07-31T01:01:00Z"):
    raw_identity = "hash-1" if stage == "qb" else "symedia-1"
    artifact = (
        "artifact:hash-1"
        if stage == "qb"
        else "artifact:symedia:symedia-1"
    )
    target = "tv:tmdb:202:season:1"
    return adapt_task_chain({
        "generatedAt": "2026-07-31T01:02:00Z",
        "services": {},
        "items": [{
            "id": "subscription:tv:202",
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "202",
            "seasonNumber": 1,
            "confidence": "strong",
            "origin": "subscription",
            "steps": [],
            "sourceIds": {
                "subscriptionId": "tv:202",
                "torraId": "torra-202",
                "qbHashes": ["hash-1"] if stage == "qb" else [],
                "symediaIds": ["symedia-1"] if stage == "symedia" else [],
            },
            "evidenceOwnership": [{
                "artifactKey": artifact,
                "ownerTargetKey": target,
                "matchMethod": "artifact_exact",
                "confidence": "strong",
            }],
            "episodeEvidence": [{
                "seasonNumber": 1,
                "episodeStart": 2,
                "episodeEnd": 3,
                "numberingScheme": "season_episode",
                "stage": "download" if stage == "qb" else "library",
                "artifactKey": artifact,
                "status": "done",
                "ownerTargetKey": f"{target}:episodes:2-3",
                "parentTargetKey": target,
            }],
            "pipelineFacts": [{
                "stage": stage,
                "state": "succeeded",
                "scope": "file",
                "evidence": "verified",
                "observedAt": "2026-07-31T01:02:00Z",
                "freshUntil": "2026-07-31T01:07:00Z",
                "source": "qBittorrent" if stage == "qb" else "Symedia",
                "sourceRef": raw_identity,
                "reasonCode": "QB_DOWNLOAD_SUCCEEDED" if stage == "qb" else "SYMEDIA_ORGANIZED",
                "reasonText": "",
                "eventAt": occurred_at,
                "units": [{
                    "unitKey": raw_identity,
                    "state": "succeeded",
                    "scope": "file",
                    "evidence": "verified",
                    "observedAt": "2026-07-31T01:02:00Z",
                    "freshUntil": "2026-07-31T01:07:00Z",
                    "sourceRef": raw_identity,
                    "reasonCode": "QB_DOWNLOAD_SUCCEEDED" if stage == "qb" else "SYMEDIA_ORGANIZED",
                    "reasonText": "",
                    "eventAt": occurred_at,
                }],
            }],
        }],
    }, now=datetime(2026, 7, 31, 1, 2, tzinfo=timezone.utc))


def torra_only_pack_snapshot():
    target = "tv:tmdb:303:season:1"
    qb_artifact = "artifact:pack-hash"
    symedia_ids = [f"symedia-{episode}" for episode in range(1, 5)]
    ownership = [{
        "artifactKey": qb_artifact,
        "ownerTargetKey": target,
        "matchMethod": "artifact_exact",
    }]
    ownership.extend({
        "artifactKey": f"artifact:symedia:{source_id}",
        "ownerTargetKey": target,
        "matchMethod": "artifact_exact",
    } for source_id in symedia_ids)
    episode_evidence = [{
        "seasonNumber": 1,
        "episodeStart": episode,
        "episodeEnd": episode,
        "stage": "library",
        "artifactKey": f"artifact:symedia:{source_id}",
        "status": "done",
        "ownerTargetKey": f"{target}:episode:{episode}",
        "parentTargetKey": target,
    } for episode, source_id in enumerate(symedia_ids, 1)]
    return {
        "items": [{
            "title": "狂怒追缉",
            "mediaType": "tv",
            "tmdbId": "303",
            "seasonNumber": 1,
            "targetKey": target,
            "identityState": "linked",
            "sourceIds": {
                "subscriptionId": "",
                "torraId": "torra-303",
                "qbHashes": ["pack-hash"],
                "symediaIds": symedia_ids,
            },
            "artifactKeys": [
                qb_artifact,
                *(f"artifact:symedia:{source_id}" for source_id in symedia_ids),
            ],
            "evidenceOwnership": ownership,
            "episodeEvidence": episode_evidence,
            "pipelineFacts": [{
                "stage": "qb",
                "state": "succeeded",
                "scope": "file",
                "evidence": "verified",
                "eventAt": "2026-07-31T01:01:00Z",
                "sourceRef": "pack-hash",
                "units": [{
                    "unitKey": "pack-hash",
                    "sourceRef": "pack-hash",
                    "eventAt": "2026-07-31T01:01:00Z",
                }],
            }, {
                "stage": "symedia",
                "state": "succeeded",
                "scope": "file",
                "evidence": "verified",
                "eventAt": "2026-07-31T01:03:00Z",
                "units": [{
                    "unitKey": source_id,
                    "sourceRef": source_id,
                    "eventAt": "2026-07-31T01:03:00Z",
                } for source_id in symedia_ids],
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
        legacy = self.repository.set_bridge_mode("shadow", bridge_version="2")
        shadow = self.bridge.set_mode("shadow")
        self.now[0] += timedelta(days=1)
        self.bridge.set_mode("off")
        reapplied = self.bridge.set_mode("shadow")

        self.assertEqual(shadow["activatedAt"], "2026-07-31T01:00:00.000Z")
        self.assertEqual(shadow["bridgeVersion"], "4")
        self.assertEqual(shadow["activatedAt"], legacy["activatedAt"])
        self.assertEqual(reapplied["activatedAt"], shadow["activatedAt"])

    def test_shadow_keeps_stage_specific_receipts_without_units(self):
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=3)
        qb = self.bridge.process_snapshot(snapshot("qb"))
        symedia = self.bridge.process_snapshot(snapshot("symedia", "2026-07-31T01:02:00Z"))
        receipts = self.repository.list_bridge_receipts()

        self.assertEqual((qb["pending"], symedia["pending"]), (1, 1))
        self.assertEqual({row["stage"] for row in receipts}, {"qb", "symedia"})
        self.assertEqual(len({row["receipt_key"] for row in receipts}), 2)
        self.assertEqual(self.repository.list_watch_units("tv:202"), [])
        summary = self.bridge.summary()
        self.assertEqual(summary["receiptTotal"], 2)
        self.assertEqual(summary["receiptCounts"]["pending"], 2)
        self.assertEqual(summary["receiptCounts"]["historical"], 0)

    def test_symedia_after_activation_can_create_confirmed_baseline(self):
        self.bridge.set_mode("shadow")
        self.bridge.set_mode("apply")
        self.now[0] += timedelta(minutes=3)

        applied = self.bridge.process_snapshot(snapshot("symedia", "2026-07-31T01:02:00Z"))
        unit = self.repository.list_watch_units("tv:202")[0]

        self.assertEqual(applied["applied"], 1)
        self.assertEqual(unit["state"], "observing_upgrade")
        self.assertEqual(unit["first_success_at"], "2026-07-31T01:02:00.000Z")
        self.assertEqual(unit["baseline_ready_at"], "2026-07-31T01:02:00.000Z")

    def test_torra_only_pack_uses_qb_files_and_is_idempotent(self):
        requested_hashes = []

        def load_files(torrent_hash):
            requested_hashes.append(torrent_hash)
            return [
                {"name": f"Rage.Pursuit.S01E{episode:02d}.mkv"}
                for episode in range(1, 5)
            ]

        self.bridge = QualityWatchBridgeRuntime(
            self.repository,
            self.quality,
            subscription_loader=lambda: {"items": []},
            torra_subscription_loader=lambda: [{
                "id": "torra-303",
                "name": "狂怒追缉",
                "media_type": "tv",
                "tmdb_id": 303,
                "season_number": 1,
            }],
            qb_task_loader=lambda: {
                "connected": True,
                "tasks": [{
                    "hash": "pack-hash",
                    "name": "Rage.Pursuit.S01.Complete",
                    "status": "completed",
                }, {
                    "hash": "unrelated-hash",
                    "name": "Other.Show.S01.Complete",
                    "status": "completed",
                }],
            },
            qb_file_loader=load_files,
            config_loader=self.quality.config_loader,
            clock=lambda: self.now[0],
        )
        self.bridge.set_mode("shadow")
        self.bridge.set_mode("apply")
        self.now[0] += timedelta(minutes=5)

        first = self.bridge.process_snapshot(torra_only_pack_snapshot())
        second = self.bridge.process_snapshot(torra_only_pack_snapshot())
        key = "torra:torra-303"
        units = self.repository.list_watch_units(key)

        self.assertEqual((first["processed"], first["applied"]), (8, 8))
        self.assertEqual((second["processed"], second["applied"]), (8, 8))
        self.assertEqual(len(units), 4)
        self.assertEqual({unit["episode_number"] for unit in units}, {1, 2, 3, 4})
        self.assertEqual({unit["state"] for unit in units}, {"observing_upgrade"})
        self.assertEqual({unit["first_success_at"] for unit in units}, {"2026-07-31T01:01:00.000Z"})
        self.assertEqual({unit["baseline_ready_at"] for unit in units}, {"2026-07-31T01:03:00.000Z"})
        self.assertEqual(requested_hashes, ["pack-hash", "pack-hash"])
        self.assertEqual(
            len([row for row in self.repository.list_bridge_receipts() if row["bridge_version"] == "4"]),
            8,
        )

    def test_qb_pack_files_are_not_read_until_subscription_is_resolved(self):
        requested_hashes = []
        payload = torra_only_pack_snapshot()
        payload["items"][0]["pipelineFacts"] = payload["items"][0]["pipelineFacts"][:1]
        self.bridge = QualityWatchBridgeRuntime(
            self.repository,
            self.quality,
            subscription_loader=lambda: {"items": []},
            torra_subscription_loader=lambda: [{
                "id": "torra-303",
                "media_type": "tv",
                "tmdb_id": 999,
                "season_number": 1,
            }],
            qb_task_loader=lambda: {
                "connected": True,
                "tasks": [{
                    "hash": "pack-hash",
                    "name": "Rage.Pursuit.S01.Complete",
                    "status": "completed",
                }],
            },
            qb_file_loader=lambda torrent_hash: requested_hashes.append(torrent_hash) or [],
            config_loader=self.quality.config_loader,
            clock=lambda: self.now[0],
        )
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=5)

        result = self.bridge.process_snapshot(payload)

        self.assertEqual((result["processed"], result["needs_review"]), (1, 1))
        self.assertEqual(requested_hashes, [])

    def test_cross_season_qb_files_do_not_project_episode_targets(self):
        requested_hashes = []
        payload = torra_only_pack_snapshot()
        payload["items"][0]["pipelineFacts"] = payload["items"][0]["pipelineFacts"][:1]
        self.bridge = QualityWatchBridgeRuntime(
            self.repository,
            self.quality,
            subscription_loader=lambda: {"items": []},
            torra_subscription_loader=lambda: [{
                "id": "torra-303",
                "media_type": "tv",
                "tmdb_id": 303,
                "season_number": 1,
            }],
            qb_task_loader=lambda: {
                "connected": True,
                "tasks": [{
                    "hash": "pack-hash",
                    "name": "Rage.Pursuit.S01.Complete",
                    "status": "completed",
                }],
            },
            qb_file_loader=lambda torrent_hash: requested_hashes.append(torrent_hash) or [
                {"name": "Rage.Pursuit.S01E01.mkv"},
                {"name": "Rage.Pursuit.S02E01.mkv"},
            ],
            config_loader=self.quality.config_loader,
            clock=lambda: self.now[0],
        )
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=5)

        result = self.bridge.process_snapshot(payload)

        self.assertEqual((result["processed"], result["needs_review"]), (1, 1))
        self.assertEqual(requested_hashes, ["pack-hash"])
        self.assertEqual(self.repository.list_watch_units("torra:torra-303"), [])

    def test_qb_pack_ignores_skipped_and_incomplete_files(self):
        payload = torra_only_pack_snapshot()
        payload["items"][0]["pipelineFacts"] = payload["items"][0]["pipelineFacts"][:1]
        self.bridge = QualityWatchBridgeRuntime(
            self.repository,
            self.quality,
            subscription_loader=lambda: {"items": []},
            torra_subscription_loader=lambda: [{
                "id": "torra-303",
                "media_type": "tv",
                "tmdb_id": 303,
                "season_number": 1,
            }],
            qb_task_loader=lambda: {
                "connected": True,
                "tasks": [{
                    "hash": "pack-hash",
                    "name": "Rage.Pursuit.S01.Complete",
                    "status": "completed",
                }],
            },
            qb_file_loader=lambda _torrent_hash: [
                {"name": "Rage.Pursuit.S01E01.mkv", "progress": 1, "priority": 1},
                {"name": "Rage.Pursuit.S01E02.mkv", "progress": 0, "priority": 0},
                {"name": "Rage.Pursuit.S01E03.mkv", "progress": 0.5, "priority": 1},
            ],
            config_loader=self.quality.config_loader,
            clock=lambda: self.now[0],
        )
        self.bridge.set_mode("shadow")
        self.bridge.set_mode("apply")
        self.now[0] += timedelta(minutes=5)

        result = self.bridge.process_snapshot(payload)
        units = self.repository.list_watch_units("torra:torra-303")

        self.assertEqual((result["processed"], result["applied"]), (1, 1))
        self.assertEqual([unit["episode_number"] for unit in units], [1])

    def test_summary_ignores_v3_receipts(self):
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=2)
        self.bridge.process_snapshot(snapshot("qb"))
        legacy = dict(self.repository.list_bridge_receipts()[0])
        legacy.update({
            "receipt_id": "bridge:legacy-v3",
            "receipt_key": "legacy-v3-key",
            "bridge_version": "3",
        })
        with self.repository.runtime.transaction(immediate=True) as connection:
            self.repository.upsert_bridge_receipt(connection, legacy, "needs_review", "legacy")

        summary = self.bridge.summary()

        self.assertEqual(summary["bridgeVersion"], "4")
        self.assertEqual(summary["receiptTotal"], 1)
        self.assertEqual(
            len([row for row in self.repository.list_bridge_receipts() if row["bridge_version"] == "3"]),
            1,
        )

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

    def test_production_season_snapshot_resolves_artifact_and_projects_each_episode(self):
        self.bridge.set_mode("shadow")
        self.now[0] += timedelta(minutes=2)

        shadow = self.bridge.process_snapshot(production_season_snapshot("qb"))
        receipts = self.repository.list_bridge_receipts()

        self.assertEqual((shadow["processed"], shadow["pending"]), (2, 2))
        self.assertEqual(len(receipts), 2)
        self.assertEqual({row["artifact_key"] for row in receipts}, {"artifact:hash-1"})

        self.bridge.set_mode("apply")
        applied = self.bridge.process_snapshot(production_season_snapshot("qb"))
        units = self.repository.list_watch_units("tv:202")

        self.assertEqual(applied["applied"], 2)
        self.assertEqual(
            {(unit["season_number"], unit["episode_number"]) for unit in units},
            {(1, 2), (1, 3)},
        )
        self.assertEqual({unit["state"] for unit in units}, {"waiting_library_baseline"})

        self.now[0] += timedelta(minutes=3)
        advanced = self.bridge.process_snapshot(
            production_season_snapshot("symedia", "2026-07-31T01:03:00Z")
        )
        units = self.repository.list_watch_units("tv:202")

        self.assertEqual(advanced["applied"], 2)
        self.assertEqual({unit["state"] for unit in units}, {"observing_upgrade"})

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
