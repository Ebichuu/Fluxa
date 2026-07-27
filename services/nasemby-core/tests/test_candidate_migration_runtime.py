from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from app.candidate_migration_runtime import (
    CandidateMigrationError,
    CandidateMigrationService,
    register_candidate_migrations,
)
from app.resource_identity_runtime import target_key
from app.subscription_repository import SubscriptionRepository


class CandidateMigrationRuntimeTests(unittest.TestCase):
    NOW = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)

    def repository(self, directory):
        return SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")

    @staticmethod
    def add(repository, key, *, origin="", media_type="tv", tmdb_id="", season=1, title="测试作品", **fields):
        item = {
            "subscription_key": key,
            "title": title,
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "target_season": season,
            "origin": origin,
            **fields,
        }
        repository.upsert_item(item, key)
        return item

    def service(self, repository, *, backup_callback=None):
        return CandidateMigrationService(
            repository,
            {"NASEMBY_CORE_WRITE_ENABLED": "true"},
            backup_callback=backup_callback,
            activity_writer=lambda *_args, **_kwargs: None,
            clock=lambda: self.NOW,
        )

    def seed_categories(self, repository):
        self.add(repository, "tv:manual", origin="manual", tmdb_id="101", title="人工追更")
        self.add(repository, "tv:torra", origin="auto", tmdb_id="102", title="Torra 已接管")
        repository.save_torra_link({"subscription_key": "tv:torra", "remote_id": "remote-102"})
        self.add(repository, "tv:resource", origin="auto", tmdb_id="103", title="已有任务链")
        self.add(
            repository,
            "tv:auto",
            origin="auto",
            tmdb_id="104",
            title="榜单污染",
            source_label="榜单 https://tracker.invalid/path?passkey=private",
            file_path="/private/library/path",
        )
        self.add(repository, "tv:review", origin="", tmdb_id="105", title="来源不明")
        with repository.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "CREATE TABLE resource_chains ("
                "chain_id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL DEFAULT '', "
                "target_key TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO resource_chains (chain_id, subscription_id, target_key, version, updated_at) "
                "VALUES ('chain-private-103', '', ?, 1, '2026-07-28T08:00:00Z')",
                (target_key("tv", "103", "已有任务链", 1),),
            )

    def test_preview_classifies_four_categories_without_writes_or_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.seed_categories(repository)
            before = repository.load_payload()

            first = self.service(repository).preview()
            second = self.service(repository).preview()
            serialized = json.dumps(first, ensure_ascii=False)

            self.assertEqual(first["previewFingerprint"], second["previewFingerprint"])
            self.assertEqual(first["counts"], {
                "manual": 1,
                "downstream-owned": 2,
                "candidate-eligible": 1,
                "migration-review": 1,
            })
            self.assertTrue(first["canExecute"])
            self.assertEqual(repository.load_payload(), before)
            self.assertEqual(repository.list_discover_candidates(state="")["total"], 0)
            self.assertIsNone(repository.get_candidate_migration_run(run_id="missing"))
            self.assertNotIn("tv:auto", serialized)
            self.assertNotIn("chain-private-103", serialized)
            self.assertNotIn("tracker.invalid", serialized)
            self.assertNotIn("passkey", serialized)
            self.assertNotIn("/private/library/path", serialized)

    def test_confirmed_execution_migrates_only_eligible_and_replays_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.seed_categories(repository)
            backups = []

            def backup(fingerprint):
                backups.append(fingerprint)
                return repository.ensure_candidate_migration_backup(fingerprint)

            service = self.service(repository, backup_callback=backup)
            preview = service.preview()
            body = {
                "confirm": True,
                "idempotencyKey": "candidate-migration-key-123",
                "previewFingerprint": preview["previewFingerprint"],
            }

            result = service.execute(body)
            replay = service.execute(body)
            remaining = {item["subscription_key"] for item in repository.load_payload()["items"]}
            candidates = repository.list_discover_candidates(state="")
            run = repository.get_candidate_migration_run(run_id=result["runId"])

            self.assertEqual(result["migratedCount"], 1)
            self.assertEqual(result["preservedCount"], 4)
            self.assertFalse(result["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(len(backups), 1)
            self.assertNotIn("tv:auto", remaining)
            self.assertEqual(remaining, {"tv:manual", "tv:torra", "tv:resource", "tv:review"})
            self.assertEqual(candidates["total"], 1)
            self.assertEqual(candidates["items"][0]["title"], "榜单污染")
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(self.service(repository).get_run(result["runId"]), result)
            self.assertEqual(
                len(list(Path(directory).glob("*.candidate-migration-v1.*.sqlite3"))),
                1,
            )
            with closing(repository.runtime.connect()) as connection:
                stored = connection.execute(
                    "SELECT compensation_json FROM candidate_migration_runs WHERE run_id=?",
                    (result["runId"],),
                ).fetchone()
            compensation = json.loads(stored["compensation_json"])
            self.assertEqual(compensation[0]["subscriptionKey"], "tv:auto")
            self.assertNotIn("subscriptionKey", json.dumps(result))
            with self.assertRaises(CandidateMigrationError) as caught:
                service.execute({
                    "confirm": True,
                    "idempotencyKey": body["idempotencyKey"],
                    "previewFingerprint": "0" * 64,
                })
            self.assertEqual(caught.exception.code, "CANDIDATE_MIGRATION_IDEMPOTENCY_CONFLICT")

    def test_backup_failure_and_stale_preview_never_partially_migrate(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.add(repository, "tv:auto", origin="auto", tmdb_id="201", title="自动来源")
            preview = self.service(repository).preview()

            def fail_backup(_fingerprint):
                raise OSError("backup failed")

            with self.assertRaises(CandidateMigrationError) as caught:
                self.service(repository, backup_callback=fail_backup).execute({
                    "confirm": True,
                    "idempotencyKey": "backup-failure-key-123",
                    "previewFingerprint": preview["previewFingerprint"],
                })
            self.assertEqual(caught.exception.code, "CANDIDATE_MIGRATION_BACKUP_FAILED")
            self.assertEqual(len(repository.load_payload()["items"]), 1)
            self.assertEqual(repository.list_discover_candidates(state="")["total"], 0)
            self.assertIsNone(repository.get_candidate_migration_run(idempotency_key="backup-failure-key-123"))

            repository.upsert_item({"title": "自动来源已变化"}, "tv:auto")
            backup_calls = []
            with self.assertRaises(CandidateMigrationError) as caught:
                self.service(repository, backup_callback=lambda value: backup_calls.append(value)).execute({
                    "confirm": True,
                    "idempotencyKey": "stale-preview-key-123",
                    "previewFingerprint": preview["previewFingerprint"],
                })
            self.assertEqual(caught.exception.code, "CANDIDATE_MIGRATION_PREVIEW_STALE")
            self.assertEqual(backup_calls, [])
            self.assertEqual(len(repository.load_payload()["items"]), 1)

    def test_change_after_backup_is_rechecked_inside_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.add(repository, "tv:auto", origin="auto", tmdb_id="301", title="并发候选")
            preview = self.service(repository).preview()

            def mutate_after_backup(_fingerprint):
                repository.upsert_item({"title": "并发变化"}, "tv:auto")
                return "candidate-migration-v1:test"

            with self.assertRaises(CandidateMigrationError) as caught:
                self.service(repository, backup_callback=mutate_after_backup).execute({
                    "confirm": True,
                    "idempotencyKey": "concurrent-change-key-123",
                    "previewFingerprint": preview["previewFingerprint"],
                })

            self.assertEqual(caught.exception.code, "CANDIDATE_MIGRATION_PREVIEW_STALE")
            self.assertEqual(repository.list_discover_candidates(state="")["total"], 0)
            self.assertIsNone(repository.get_candidate_migration_run(idempotency_key="concurrent-change-key-123"))

    def test_execute_validates_confirmation_key_fingerprint_and_write_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.add(repository, "tv:auto", origin="auto", tmdb_id="401", title="待迁候选")
            fingerprint = self.service(repository).preview()["previewFingerprint"]
            cases = [
                ({"idempotencyKey": "candidate-key-123", "previewFingerprint": fingerprint}, "CANDIDATE_MIGRATION_CONFIRM_REQUIRED"),
                ({"confirm": True, "idempotencyKey": "short", "previewFingerprint": fingerprint}, "CANDIDATE_MIGRATION_IDEMPOTENCY_INVALID"),
                ({"confirm": True, "idempotencyKey": "candidate-key-123", "previewFingerprint": "bad"}, "CANDIDATE_MIGRATION_FINGERPRINT_INVALID"),
            ]
            for body, code in cases:
                with self.subTest(code=code), self.assertRaises(CandidateMigrationError) as caught:
                    self.service(repository).execute(body)
                self.assertEqual(caught.exception.code, code)

            disabled = CandidateMigrationService(repository, {}, activity_writer=lambda *_args, **_kwargs: None)
            with self.assertRaises(CandidateMigrationError) as caught:
                disabled.execute({
                    "confirm": True,
                    "idempotencyKey": "candidate-key-123",
                    "previewFingerprint": fingerprint,
                })
            self.assertEqual(caught.exception.code, "NASEMBY_CORE_WRITE_DISABLED")

    def test_unverified_torra_origin_is_preserved_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.add(repository, "tv:torra-stale", origin="torra", tmdb_id="501", title="旧 Torra 来源", read_only=True)

            preview = self.service(repository).preview()

            self.assertEqual(preview["counts"]["migration-review"], 1)
            self.assertEqual(preview["counts"]["candidate-eligible"], 0)
            self.assertEqual(preview["items"][0]["reasonCode"], "DOWNSTREAM_ORIGIN_UNVERIFIED")

    def test_http_preview_is_paginated_and_created_run_has_location(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            self.add(repository, "tv:auto-1", origin="auto", tmdb_id="601", title="自动一")
            self.add(repository, "tv:auto-2", origin="auto", tmdb_id="602", title="自动二")
            app = Flask(__name__)
            register_candidate_migrations(
                app,
                {"NASEMBY_CORE_WRITE_ENABLED": "true"},
                repository=repository,
                activity_writer=lambda *_args, **_kwargs: None,
                clock=lambda: self.NOW,
            )
            client = app.test_client()

            invalid = client.get("/api/v2/subscriptions/candidate-migrations/preview?limit=500")
            preview_response = client.get("/api/v2/subscriptions/candidate-migrations/preview?limit=1")
            preview = preview_response.get_json()
            body = {
                "confirm": True,
                "idempotencyKey": "http-migration-key-123",
                "previewFingerprint": preview["previewFingerprint"],
            }
            created = client.post("/api/v2/subscriptions/candidate-migrations", json=body)
            replay = client.post("/api/v2/subscriptions/candidate-migrations", json=body)
            location = created.headers["Location"]
            fetched = client.get(location)

            self.assertEqual(invalid.status_code, 400)
            self.assertEqual(preview_response.status_code, 200)
            self.assertEqual(len(preview["items"]), 1)
            self.assertTrue(preview["page"]["hasMore"])
            self.assertEqual(preview["page"]["total"], 2)
            self.assertEqual(created.status_code, 201)
            self.assertEqual(replay.status_code, 200)
            self.assertTrue(location.startswith("/api/v2/subscriptions/candidate-migrations/candidate-migration:"))
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.get_json()["runId"], created.get_json()["runId"])

    def test_backup_rejects_unsafe_fingerprint_and_replaces_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.repository(directory)
            fingerprint = "a" * 64
            backup_path = Path(directory) / "media_control_center.candidate-migration-v1.aaaaaaaaaaaa.sqlite3"
            backup_path.write_text("not a sqlite database", encoding="utf-8")

            with self.assertRaises(ValueError):
                repository.ensure_candidate_migration_backup("../unsafe-value")
            backup_id = repository.ensure_candidate_migration_backup(fingerprint)

            self.assertEqual(backup_id, "candidate-migration-v1:aaaaaaaaaaaa")
            self.assertTrue(repository._valid_candidate_migration_backup(backup_path))


if __name__ == "__main__":
    unittest.main()
