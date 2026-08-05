from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app.quality_watch_key_migration as key_migration
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_key_migration import (
    QualityWatchKeyMigrationError,
    run_quality_watch_key_migration,
)
from app.quality_watch_repository import QualityWatchRepository, make_unit_key
from app.torra_subscription_keys import torra_public_subscription_key


NOW = datetime(2026, 8, 6, 2, 30, tzinfo=timezone.utc)


class QualityWatchKeyMigrationTests(unittest.TestCase):
    def repositories(self, directory):
        path = Path(directory) / "media.sqlite3"
        quality = QualityWatchRepository(path, clock=lambda: NOW)
        rss = PrivateRssRepository(path)
        return path, quality, rss

    @staticmethod
    def seed_unit(repository, subscription_key, remote_id, episode=1, **overrides):
        unit_key = make_unit_key(subscription_key, "tv", 1, episode)
        values = {
            "subscription_key": subscription_key,
            "season_number": 1,
            "episode_number": episode,
            "torra_subscription_id": remote_id,
            "state": "observing_upgrade",
            "first_success_at": "2026-08-05T01:00:00Z",
            "baseline_ready_at": "2026-08-05T01:05:00Z",
            "window_hours": 48,
            "next_check_at": "",
            "observation_ends_at": "2026-08-07T01:05:00Z",
            "current_evidence": {},
            "last_result": {},
            "target_reached_at": "",
            "lifecycle_mode": "follow_rss",
        }
        values.update(overrides)
        with repository.runtime.transaction(immediate=True) as connection:
            repository.apply_reconcile_plan(connection, {
                "writes": [{"operation": "insert", "unitKey": unit_key, "values": values}],
            }, now=NOW)
        return unit_key

    @staticmethod
    def seed_match(rss, subscription_key, unit_key, remote_id):
        source = rss.save_source({
            "name": "测试", "feedUrl": "https://tracker.example/rss", "enabled": True,
        })
        rss.upsert_items(source["id"], [{
            "fingerprint": "item-1", "guid": "item-1", "title": "Example S01E01",
            "published_at": "2026-08-05T02:00:00Z",
        }])
        item = rss.search_items(limit=1)["items"][0]
        match = rss.create_match(item["id"], subscription_key, unit_key, {"source": "test"})
        rss.set_match_binding(
            match["id"], torra_subscription_id=remote_id,
            target_key="tv:tmdb:202:season:1:episodes:1-1", artifact_key="artifact:test",
        )
        return match["id"]

    @staticmethod
    def raw_rows(repository, query, parameters=()):
        with closing(repository.runtime.connect()) as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def test_no_legacy_keys_returns_zero_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path, quality, _rss = self.repositories(directory)

            result = run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertEqual(result["updated"], 0)
            self.assertFalse(result["backupCreated"])
            self.assertFalse((path.parent / "migrations").exists())

    def test_unrelated_invalid_json_does_not_create_a_migration_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path, quality, _rss = self.repositories(directory)
            with quality.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO scheduler_state (state_key, payload_json, updated_at, version) "
                    "VALUES ('unrelated', 'not-json', '2026-08-06T02:00:00Z', 1)"
                )

            result = run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertEqual(result["updated"], 0)
            self.assertFalse(result["backupCreated"])
            self.assertFalse((path.parent / "migrations").exists())

    def test_migrates_units_rss_actions_json_and_scheduler_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path, quality, rss = self.repositories(directory)
            remote_id = "remote-private-202"
            public_key = torra_public_subscription_key(remote_id)
            canonical_key = f"torra:{remote_id}"
            public_unit = self.seed_unit(quality, public_key, remote_id)
            canonical_unit = make_unit_key(canonical_key, "tv", 1, 1)
            match_id = self.seed_match(rss, public_key, public_unit, remote_id)
            action = quality.claim_action(
                f"scheduled-rewash-analysis:{public_unit}:0",
                public_key,
                "torra",
                "rewash-analysis",
                unit_key=public_unit,
                request_summary={"source": "quality-watch-scheduler", "unitId": public_unit},
            )["action"]
            quality.complete_action(
                action["action_id"], "succeeded",
                {"subscriptionId": public_key, "unitId": public_unit},
            )
            quality.save_scheduler_state("quality-watch-scheduler", {
                "cursor": public_unit, "lastSubscription": public_key,
            })
            with quality.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO quality_watch_bridge_receipts ("
                    "receipt_id, receipt_key, bridge_version, stage, fact_type, owner_target_key, "
                    "artifact_key, status, created_at, updated_at) VALUES "
                    "('v3-receipt', 'v3-key', '3', 'qb', 'download_completed', 'target', "
                    "'artifact:test', 'applied', '2026-08-05T01:00:00Z', '2026-08-05T01:00:00Z')"
                )

            first = run_quality_watch_key_migration(quality, clock=lambda: NOW)
            backups = list((path.parent / "migrations").glob("*.sqlite3"))
            second = run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertTrue(first["applied"])
            self.assertEqual(second["updated"], 0)
            self.assertEqual(len(backups), 1)
            self.assertEqual(len(list((path.parent / "migrations").glob("*.sqlite3"))), 1)
            self.assertIsNotNone(quality.get_watch_unit(canonical_unit))
            self.assertIsNone(quality.get_watch_unit(public_unit))
            match = rss.get_match(match_id)
            self.assertEqual((match["subscriptionId"], match["unitId"]), (canonical_key, canonical_unit))
            stored_action = quality.get_action(action["action_id"])
            self.assertEqual(stored_action["subscription_key"], canonical_key)
            self.assertEqual(stored_action["unit_key"], canonical_unit)
            self.assertEqual(stored_action["request_summary"]["unitId"], canonical_unit)
            self.assertEqual(stored_action["response_summary"]["subscriptionId"], canonical_key)
            self.assertEqual(
                stored_action["idempotency_key"], f"scheduled-rewash-analysis:{canonical_unit}:0"
            )
            state = quality.get_scheduler_state("quality-watch-scheduler")["payload"]
            self.assertEqual((state["cursor"], state["lastSubscription"]), (canonical_unit, canonical_key))
            self.assertEqual(
                self.raw_rows(quality, "SELECT bridge_version FROM quality_watch_bridge_receipts")[0]["bridge_version"],
                "3",
            )
            serialized = json.dumps({
                "units": self.raw_rows(quality, "SELECT * FROM quality_watch_units"),
                "actions": self.raw_rows(quality, "SELECT * FROM provider_actions"),
                "matches": self.raw_rows(quality, "SELECT * FROM rss_subscription_matches"),
                "scheduler": self.raw_rows(quality, "SELECT * FROM scheduler_state"),
            }, ensure_ascii=False)
            self.assertNotIn(public_key, serialized)

    def test_public_and_canonical_unit_conflict_rolls_back_and_reports_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            path, quality, _rss = self.repositories(directory)
            remote_id = "secret-remote-id"
            public_key = torra_public_subscription_key(remote_id)
            canonical_key = f"torra:{remote_id}"
            public_unit = self.seed_unit(quality, public_key, remote_id)
            canonical_unit = self.seed_unit(quality, canonical_key, remote_id)
            before = self.raw_rows(quality, "SELECT * FROM quality_watch_units ORDER BY unit_key")

            with self.assertRaises(QualityWatchKeyMigrationError) as error:
                run_quality_watch_key_migration(quality, clock=lambda: NOW)

            after = self.raw_rows(quality, "SELECT * FROM quality_watch_units ORDER BY unit_key")
            self.assertEqual(before, after)
            self.assertEqual(error.exception.result["reasonCode"], "migration_conflict")
            self.assertTrue(Path(error.exception.result["backup"]).exists())
            report = Path(error.exception.result["report"]).read_text(encoding="utf-8")
            self.assertNotIn(remote_id, report)
            self.assertNotIn(canonical_key, report)
            self.assertNotIn(public_unit, report)
            self.assertNotIn(canonical_unit, report)
            self.assertEqual(
                self.raw_rows(quality, "SELECT COUNT(*) AS count FROM quality_watch_key_migrations")[0]["count"],
                0,
            )

    def test_unknown_json_reference_blocks_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, _rss = self.repositories(directory)
            remote_id = "remote-json"
            public_key = torra_public_subscription_key(remote_id)
            public_unit = self.seed_unit(
                quality, public_key, remote_id,
                current_evidence={"legacyOwner": public_key},
            )

            with self.assertRaises(QualityWatchKeyMigrationError):
                run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertIsNotNone(quality.get_watch_unit(public_unit))

    def test_public_reference_used_as_json_key_blocks_and_report_stays_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, _rss = self.repositories(directory)
            remote_id = "remote-json-object-key"
            public_key = torra_public_subscription_key(remote_id)
            public_unit = self.seed_unit(
                quality, public_key, remote_id,
                current_evidence={public_key: {"state": "legacy"}},
            )

            with self.assertRaises(QualityWatchKeyMigrationError) as error:
                run_quality_watch_key_migration(quality, clock=lambda: NOW)

            report = Path(error.exception.result["report"]).read_text(encoding="utf-8")
            self.assertIn("unknown_json_key_reference", report)
            self.assertNotIn(public_key, report)
            self.assertNotIn(remote_id, report)
            self.assertIsNotNone(quality.get_watch_unit(public_unit))

    def test_rss_only_candidate_migrates_without_quality_watch_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, rss = self.repositories(directory)
            remote_id = "remote-rss-only"
            public_key = torra_public_subscription_key(remote_id)
            canonical_key = f"torra:{remote_id}"
            public_unit = make_unit_key(public_key, "tv", 1, 2)
            canonical_unit = make_unit_key(canonical_key, "tv", 1, 2)
            source = rss.save_source({
                "name": "测试", "feedUrl": "https://tracker.example/rss", "enabled": True,
            })
            rss.upsert_items(source["id"], [{
                "fingerprint": "rss-only", "guid": "rss-only",
                "title": "Example S01E01-E04", "media_type": "tv",
                "season_number": 1, "episode_start": 1, "episode_end": 4,
                "published_at": "2026-08-05T02:00:00Z",
            }])
            item = rss.search_items(limit=1)["items"][0]
            match = rss.create_match(item["id"], public_key, public_unit, {
                "mediaType": "tv",
                "season": {"item": 1, "unit": 1},
                "episode": {"start": 1, "end": 4, "unit": 2},
            })
            rss.set_match_binding(
                match["id"], torra_subscription_id=remote_id,
                target_key="tv:tmdb:202:season:1:episodes:1-4",
                artifact_key="artifact:rss-only",
            )
            action = quality.claim_action(
                "rss-shadow:rss-only", public_key, "fluxa", "rss-candidate-evaluation",
                unit_key=public_unit, request_summary={"targetKey": public_unit},
            )["action"]
            quality.complete_action(action["action_id"], "succeeded", {})

            result = run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertTrue(result["applied"])
            stored = rss.get_match(match["id"])
            self.assertEqual(stored["subscriptionId"], canonical_key)
            self.assertEqual(stored["unitId"], canonical_unit)
            stored_action = quality.get_action(action["action_id"])
            self.assertEqual(stored_action["subscription_key"], canonical_key)
            self.assertEqual(stored_action["unit_key"], canonical_unit)
            self.assertEqual(stored_action["request_summary"]["targetKey"], canonical_unit)

    def test_unowned_blocked_rss_match_is_archived_without_removing_rss_item(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, rss = self.repositories(directory)
            public_key = torra_public_subscription_key("removed-remote")
            public_unit = make_unit_key(public_key, "tv", 1, 3)
            source = rss.save_source({
                "name": "测试", "feedUrl": "https://tracker.example/rss", "enabled": True,
            })
            rss.upsert_items(source["id"], [{
                "fingerprint": "orphan-rss", "guid": "orphan-rss",
                "title": "Example S01E01-E04", "media_type": "tv",
                "season_number": 1, "episode_start": 1, "episode_end": 4,
                "published_at": "2026-08-05T02:00:00Z",
            }])
            item = rss.search_items(limit=1)["items"][0]
            match = rss.create_match(item["id"], public_key, public_unit, {
                "mediaType": "tv",
                "season": {"item": 1, "unit": 1},
                "episode": {"start": 1, "end": 4, "unit": 3},
            })
            action = quality.claim_action(
                "rss-shadow:orphan", public_key, "fluxa", "rss-candidate-evaluation",
                unit_key=public_unit, request_summary={"targetKey": public_unit},
            )["action"]
            quality.complete_action(action["action_id"], "succeeded", {})
            with quality.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE rss_subscription_matches SET evaluation_status='blocked', "
                    "evaluation_reason='subscription_missing', evaluation_action_id=? WHERE id=?",
                    (action["action_id"], match["id"]),
                )

            run_quality_watch_key_migration(quality, clock=lambda: NOW)

            raw_match = self.raw_rows(
                quality, "SELECT * FROM rss_subscription_matches WHERE id=?", (match["id"],)
            )[0]
            self.assertEqual(raw_match["archive_state"], "archived")
            self.assertEqual(raw_match["archive_reason_code"], "canonical_key_identity_unavailable")
            self.assertTrue(raw_match["subscription_key"].startswith("rss-archive:"))
            self.assertEqual(
                self.raw_rows(quality, "SELECT COUNT(*) AS count FROM rss_items")[0]["count"], 1
            )

    def test_identical_conflict_reuses_first_backup_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path, quality, _rss = self.repositories(directory)
            remote_id = "duplicate-conflict"
            public_key = torra_public_subscription_key(remote_id)
            self.seed_unit(quality, public_key, remote_id)
            self.seed_unit(quality, f"torra:{remote_id}", remote_id)

            with self.assertRaises(QualityWatchKeyMigrationError) as first:
                run_quality_watch_key_migration(quality, clock=lambda: NOW)
            with self.assertRaises(QualityWatchKeyMigrationError) as second:
                run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertEqual(first.exception.result["backup"], second.exception.result["backup"])
            self.assertEqual(first.exception.result["report"], second.exception.result["report"])
            self.assertFalse(second.exception.result["backupCreated"])
            self.assertEqual(len(list((path.parent / "migrations").glob("*.sqlite3"))), 1)
            self.assertEqual(
                len(list(path.parent.glob("*.quality-watch-canonical-key-v4.conflict.*.json"))), 1
            )

    def test_registered_json_field_with_wrong_key_kind_blocks_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, _rss = self.repositories(directory)
            remote_id = "remote-json-kind"
            public_key = torra_public_subscription_key(remote_id)
            public_unit = self.seed_unit(quality, public_key, remote_id)
            quality.claim_action(
                f"scheduled-rewash-analysis:{public_unit}:0",
                public_key,
                "torra",
                "rewash-analysis",
                unit_key=public_unit,
                request_summary={"source": "quality-watch-scheduler", "unitId": public_key},
            )

            with self.assertRaises(QualityWatchKeyMigrationError) as error:
                run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertEqual(error.exception.result["reasonCode"], "migration_conflict")
            self.assertIsNotNone(quality.get_watch_unit(public_unit))

    def test_backup_failure_does_not_enter_migration_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, _rss = self.repositories(directory)
            remote_id = "remote-backup-failure"
            public_key = torra_public_subscription_key(remote_id)
            public_unit = self.seed_unit(quality, public_key, remote_id)
            before = self.raw_rows(quality, "SELECT * FROM quality_watch_units")

            def fail_backup(_repository, _clock):
                raise OSError("disk unavailable")

            with self.assertRaises(QualityWatchKeyMigrationError) as error:
                run_quality_watch_key_migration(
                    quality,
                    clock=lambda: NOW,
                    backup_creator=fail_backup,
                )

            self.assertEqual(error.exception.result["reasonCode"], "backup_failed")
            self.assertEqual(before, self.raw_rows(quality, "SELECT * FROM quality_watch_units"))
            self.assertIsNotNone(quality.get_watch_unit(public_unit))
            self.assertEqual(
                self.raw_rows(
                    quality,
                    "SELECT COUNT(*) AS count FROM quality_watch_key_migrations",
                )[0]["count"],
                0,
            )

    def test_audit_insert_failure_rolls_back_all_business_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, rss = self.repositories(directory)
            remote_id = "remote-audit-failure"
            public_key = torra_public_subscription_key(remote_id)
            public_unit = self.seed_unit(quality, public_key, remote_id)
            self.seed_match(rss, public_key, public_unit, remote_id)
            with quality.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "CREATE TRIGGER reject_quality_watch_key_migration_audit "
                    "BEFORE INSERT ON quality_watch_key_migrations "
                    "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
                )
            before_units = self.raw_rows(quality, "SELECT * FROM quality_watch_units")
            before_matches = self.raw_rows(quality, "SELECT * FROM rss_subscription_matches")

            with self.assertRaises(QualityWatchKeyMigrationError) as error:
                run_quality_watch_key_migration(quality, clock=lambda: NOW)

            self.assertEqual(error.exception.result["reasonCode"], "migration_failed")
            self.assertEqual(before_units, self.raw_rows(quality, "SELECT * FROM quality_watch_units"))
            self.assertEqual(
                before_matches,
                self.raw_rows(quality, "SELECT * FROM rss_subscription_matches"),
            )
            self.assertEqual(
                self.raw_rows(
                    quality,
                    "SELECT COUNT(*) AS count FROM quality_watch_key_migrations",
                )[0]["count"],
                0,
            )

    def test_plan_drift_after_backup_is_rejected_without_migrating(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, _rss = self.repositories(directory)
            remote_id = "remote-plan-drift"
            public_key = torra_public_subscription_key(remote_id)
            public_unit = self.seed_unit(quality, public_key, remote_id)

            def backup_then_drift(repository, clock):
                backup_path = key_migration._backup_database(repository, clock)
                quality.save_scheduler_state("unrelated-drift", {"version": 1})
                return backup_path

            with self.assertRaises(QualityWatchKeyMigrationError) as error:
                run_quality_watch_key_migration(
                    quality,
                    clock=lambda: NOW,
                    backup_creator=backup_then_drift,
                )

            self.assertEqual(error.exception.result["reasonCode"], "migration_plan_stale")
            self.assertIsNotNone(quality.get_watch_unit(public_unit))
            self.assertEqual(
                self.raw_rows(
                    quality,
                    "SELECT COUNT(*) AS count FROM quality_watch_key_migrations",
                )[0]["count"],
                0,
            )

    def test_digest_collision_blocks_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, quality, rss = self.repositories(directory)
            public_key = "torra:0123456789"
            public_unit = self.seed_unit(quality, public_key, "remote-a")
            self.seed_match(rss, public_key, public_unit, "remote-b")

            with mock.patch(
                "app.quality_watch_key_migration.torra_public_subscription_key",
                return_value=public_key,
            ):
                with self.assertRaises(QualityWatchKeyMigrationError) as error:
                    run_quality_watch_key_migration(quality, clock=lambda: NOW)

            report = Path(error.exception.result["report"]).read_text(encoding="utf-8")
            self.assertIn("public_key_digest_collision", report)
            self.assertNotIn("remote-a", report)
            self.assertNotIn("remote-b", report)

    def test_startup_migration_failure_prevents_bridge_and_scheduler_registration(self):
        from app.main import create_app

        with tempfile.TemporaryDirectory() as directory:
            _path, quality, rss = self.repositories(directory)

            def fail_migration(_repository, **_options):
                raise QualityWatchKeyMigrationError({
                    "status": "blocked",
                    "reasonCode": "migration_conflict",
                    "message": "blocked",
                })

            with mock.patch("app.main.QualityWatchBridgeRuntime") as bridge_type, mock.patch(
                "app.main.register_quality_watch_scheduler"
            ) as register_scheduler:
                with self.assertRaises(QualityWatchKeyMigrationError):
                    create_app(
                        access_environment={},
                        private_rss_repository=rss,
                        quality_watch_repository=quality,
                        quality_watch_key_migrator=fail_migration,
                    )

            bridge_type.assert_not_called()
            register_scheduler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
