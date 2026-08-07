from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository
from app.rss_scope_repair import (
    RssScopeRepairError,
    RssScopeRepairService,
    _backup_database,
)


class RssScopeRepairTests(unittest.TestCase):
    NOW = datetime(2026, 8, 7, 3, 0, tzinfo=timezone.utc)

    def repository(self, directory):
        repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
        source = repository.save_source({
            "name": "Scope repair",
            "feedUrl": "https://tracker.example/rss?passkey=private-value",
        })
        return repository, source["id"]

    @staticmethod
    def add_item(repository, source_id, fingerprint, title, scope):
        repository.upsert_items(source_id, [{
            "fingerprint": fingerprint,
            "guid": f"https://tracker.example/details/{fingerprint}?passkey=private-value",
            "title": title,
            "category": "TV" if scope[0] == "tv" else "Movie",
            "download_url": f"https://tracker.example/download/{fingerprint}?passkey=private-value",
            "media_type": scope[0],
            "season_number": scope[1],
            "episode_start": scope[2],
            "episode_end": scope[3],
        }])
        return next(
            item for item in repository.search_items(limit=100)["items"]
            if item["title"] == title
        )

    def service(self, repository, **kwargs):
        return RssScopeRepairService(
            repository,
            clock=lambda: self.NOW,
            **kwargs,
        )

    @staticmethod
    def raw_scope(repository, item_id):
        with closing(repository.runtime.connect()) as connection:
            row = connection.execute(
                "SELECT media_type, season_number, episode_start, episode_end "
                "FROM rss_items WHERE id=?",
                (item_id,),
            ).fetchone()
        return tuple(row) if row else None

    def seed_mixed_changes(self, repository, source_id):
        unmatched = self.add_item(
            repository,
            source_id,
            "unmatched",
            "Unmatched Show S01E15-S01E16",
            ("tv", 1, 15, 15),
        )
        candidate = self.add_item(
            repository,
            source_id,
            "candidate",
            "Candidate Show S01E15-S01E16",
            ("tv", 1, 15, 15),
        )
        candidate_match = repository.create_match(
            candidate["id"], "tv:123:s1", "tv:123:s1:e15", {"source": "test"}
        )
        triggered = self.add_item(
            repository,
            source_id,
            "triggered",
            "Triggered Show S01E15-S01E16",
            ("tv", 1, 15, 15),
        )
        triggered_match = repository.create_match(
            triggered["id"], "tv:456:s1", "tv:456:s1:e15", {"source": "test"}
        )
        repository.update_match(triggered_match["id"], "triggered", "provider-action-private")
        return unmatched, candidate, candidate_match, triggered, triggered_match

    def test_preview_is_read_only_grouped_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            self.seed_mixed_changes(repository, source_id)
            with closing(repository.runtime.connect()) as connection:
                before_items = [tuple(row) for row in connection.execute(
                    "SELECT id, media_type, season_number, episode_start, episode_end FROM rss_items ORDER BY id"
                ).fetchall()]
                before_runs = connection.execute(
                    "SELECT COUNT(*) FROM rss_scope_repair_runs"
                ).fetchone()[0]

            first = self.service(repository).preview()
            second = self.service(repository).preview()
            serialized = json.dumps(first)

            self.assertEqual(first, second)
            self.assertEqual(first["counts"]["changedItems"], 3)
            self.assertEqual(first["counts"]["safeItems"], 2)
            self.assertEqual(first["counts"]["needsReviewItems"], 1)
            self.assertEqual(first["counts"]["affectedMatches"], 2)
            self.assertEqual(first["counts"]["eligibleMatches"], 1)
            self.assertEqual(first["counts"]["needsReviewMatches"], 1)
            self.assertEqual(first["counts"]["changeTypes"]["repeatedSeasonRange"], 3)
            self.assertNotIn("Candidate Show", serialized)
            self.assertNotIn("tracker.example", serialized)
            self.assertNotIn("passkey", serialized)
            self.assertNotIn("provider-action-private", serialized)
            with closing(repository.runtime.connect()) as connection:
                after_items = [tuple(row) for row in connection.execute(
                    "SELECT id, media_type, season_number, episode_start, episode_end FROM rss_items ORDER BY id"
                ).fetchall()]
                after_runs = connection.execute(
                    "SELECT COUNT(*) FROM rss_scope_repair_runs"
                ).fetchone()[0]
            self.assertEqual(after_items, before_items)
            self.assertEqual(after_runs, before_runs)

    def test_apply_updates_safe_items_archives_candidates_and_replays_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            unmatched, candidate, candidate_match, triggered, triggered_match = self.seed_mixed_changes(
                repository, source_id
            )
            watch_repository = QualityWatchRepository(repository.runtime.database_path)
            claim = watch_repository.claim_action(
                "scope-repair-local-evaluation",
                "tv:123:s1",
                "fluxa",
                "rss-candidate-evaluation",
                request_summary={"matchId": candidate_match["id"]},
            )
            action = watch_repository.complete_action(
                claim["action"]["action_id"], "succeeded", {"evaluationStatus": "scored"}
            )
            repository.save_match_evaluation(
                [candidate_match["id"]],
                {"status": "scored", "actionId": action["action_id"]},
            )
            preview = self.service(repository).preview()

            result = self.service(repository).apply(preview["previewFingerprint"])
            replay = self.service(repository).apply(preview["previewFingerprint"])

            self.assertEqual(result["updatedItems"], 2)
            self.assertEqual(result["archivedMatches"], 1)
            self.assertEqual(result["needsReviewItems"], 1)
            self.assertTrue(replay["replayed"])
            self.assertEqual(self.raw_scope(repository, unmatched["id"]), ("tv", 1, 15, 16))
            self.assertEqual(self.raw_scope(repository, candidate["id"]), ("tv", 1, 15, 16))
            self.assertEqual(self.raw_scope(repository, triggered["id"]), ("tv", 1, 15, 15))
            self.assertEqual(repository.get_match(candidate_match["id"])["archiveState"], "archived")
            self.assertEqual(repository.get_match(triggered_match["id"])["archiveState"], "active")
            self.assertIn(
                candidate["id"],
                {row["id"] for row in repository.list_items_for_match(limit=200)},
            )

            revived = repository.create_match(
                candidate["id"], "tv:123:s1", "tv:123:s1:e15", {"source": "rematch"}
            )
            self.assertEqual(revived["id"], candidate_match["id"])
            self.assertEqual(revived["archiveState"], "active")
            self.assertEqual(revived["status"], "candidate")
            with closing(repository.runtime.connect()) as connection:
                run_count = connection.execute(
                    "SELECT COUNT(*) FROM rss_scope_repair_runs WHERE status='succeeded'"
                ).fetchone()[0]
                item_count = connection.execute(
                    "SELECT COUNT(*) FROM rss_scope_repair_items WHERE run_id=?",
                    (result["runId"],),
                ).fetchone()[0]
            self.assertEqual(run_count, 1)
            self.assertEqual(item_count, 3)
            backups = list((Path(directory) / "migrations").glob("rss-scope-repair-v1.*.sqlite3"))
            self.assertEqual(len(backups), 1)

    def test_preview_drift_is_rejected_before_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            item = self.add_item(
                repository, source_id, "drift", "Drift S01E15-S01E16", ("tv", 1, 15, 15)
            )
            preview = self.service(repository).preview()
            backup_calls = []
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE rss_items SET title='Drift S01E15-E17' WHERE id=?",
                    (item["id"],),
                )

            with self.assertRaises(RssScopeRepairError) as caught:
                self.service(
                    repository,
                    backup_creator=lambda *_args: backup_calls.append(True),
                ).apply(preview["previewFingerprint"])

            self.assertEqual(caught.exception.code, "RSS_SCOPE_REPAIR_PREVIEW_STALE")
            self.assertEqual(backup_calls, [])
            self.assertEqual(self.raw_scope(repository, item["id"]), ("tv", 1, 15, 15))

    def test_backup_failure_never_changes_rss_data(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            item = self.add_item(
                repository, source_id, "backup", "Backup S01E15-S01E16", ("tv", 1, 15, 15)
            )
            preview = self.service(repository).preview()

            def fail_backup(*_args):
                raise OSError("backup failed")

            with self.assertRaises(RssScopeRepairError) as caught:
                self.service(repository, backup_creator=fail_backup).apply(
                    preview["previewFingerprint"]
                )

            self.assertEqual(caught.exception.code, "RSS_SCOPE_REPAIR_BACKUP_FAILED")
            self.assertEqual(self.raw_scope(repository, item["id"]), ("tv", 1, 15, 15))
            self.assertEqual(list((Path(directory) / "migrations").glob("*.sqlite3")), [])

    def test_change_after_backup_is_rechecked_without_partial_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            item = self.add_item(
                repository, source_id, "after-backup", "Before S01E15-S01E16", ("tv", 1, 15, 15)
            )
            preview = self.service(repository).preview()

            def mutate_after_backup(target, fingerprint, clock):
                backup = _backup_database(target, fingerprint, clock)
                with target.runtime.transaction(immediate=True) as connection:
                    connection.execute(
                        "UPDATE rss_items SET title='After S01E15-E17' WHERE id=?",
                        (item["id"],),
                    )
                return backup

            with self.assertRaises(RssScopeRepairError) as caught:
                self.service(repository, backup_creator=mutate_after_backup).apply(
                    preview["previewFingerprint"]
                )

            self.assertEqual(caught.exception.code, "RSS_SCOPE_REPAIR_PREVIEW_STALE")
            self.assertEqual(self.raw_scope(repository, item["id"]), ("tv", 1, 15, 15))
            self.assertEqual(
                len(list((Path(directory) / "migrations").glob("rss-scope-repair-v1.*.sqlite3"))),
                1,
            )

    def test_transaction_error_rolls_back_the_entire_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            first = self.add_item(
                repository, source_id, "first", "First S01E15-S01E16", ("tv", 1, 15, 15)
            )
            second = self.add_item(
                repository, source_id, "second", "Second S01E15-S01E16", ("tv", 1, 15, 15)
            )
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "CREATE TRIGGER fail_second_scope_repair BEFORE UPDATE OF media_type ON rss_items "
                    "WHEN NEW.id='" + second["id"] + "' BEGIN SELECT RAISE(ABORT, 'forced'); END"
                )
            preview = self.service(repository).preview()

            with self.assertRaises(RssScopeRepairError) as caught:
                self.service(repository).apply(preview["previewFingerprint"])

            self.assertEqual(caught.exception.code, "RSS_SCOPE_REPAIR_FAILED")
            self.assertEqual(self.raw_scope(repository, first["id"]), ("tv", 1, 15, 15))
            self.assertEqual(self.raw_scope(repository, second["id"]), ("tv", 1, 15, 15))
            with closing(repository.runtime.connect()) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM rss_scope_repair_runs WHERE status='succeeded'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM rss_scope_repair_runs WHERE status='failed'"
                    ).fetchone()[0],
                    1,
                )

    def test_backup_is_complete_and_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            self.add_item(
                repository, source_id, "backup-valid", "Valid S01E15-S01E16", ("tv", 1, 15, 15)
            )
            fingerprint = self.service(repository).preview()["previewFingerprint"]

            backup = _backup_database(repository, fingerprint, lambda: self.NOW)

            with closing(__import__("sqlite3").connect(backup)) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM rss_items").fetchone()[0], 1)

    def test_confirmed_and_action_linked_matches_require_manual_review(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            confirmed = self.add_item(
                repository, source_id, "confirmed", "Confirmed Show S01E15-S01E16", ("tv", 1, 15, 15)
            )
            confirmed_match = repository.create_match(
                confirmed["id"], "tv:confirmed:s1", "tv:confirmed:s1:e15", {"source": "test"}
            )
            repository.update_match(confirmed_match["id"], "triggered", "external-trigger")
            repository.update_match(confirmed_match["id"], "confirmed")

            linked = self.add_item(
                repository, source_id, "linked", "Linked Show S01E15-S01E16", ("tv", 1, 15, 15)
            )
            linked_match = repository.create_match(
                linked["id"], "tv:linked:s1", "tv:linked:s1:e15", {"source": "test"}
            )
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE rss_subscription_matches SET download_action_id=? WHERE id=?",
                    ("external-download", linked_match["id"]),
                )

            unverified = self.add_item(
                repository, source_id, "unverified", "Unverified Show S01E15-S01E16", ("tv", 1, 15, 15)
            )
            unverified_match = repository.create_match(
                unverified["id"], "tv:unverified:s1", "tv:unverified:s1:e15", {"source": "test"}
            )
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE rss_subscription_matches SET evaluation_action_id=? WHERE id=?",
                    ("missing-evaluation-action", unverified_match["id"]),
                )

            preview = self.service(repository).preview()

            self.assertEqual(preview["counts"]["changedItems"], 3)
            self.assertEqual(preview["counts"]["safeItems"], 0)
            self.assertEqual(preview["counts"]["needsReviewItems"], 3)
            self.assertEqual(preview["counts"]["needsReviewMatches"], 3)
            result = self.service(repository).apply(preview["previewFingerprint"])
            self.assertEqual(result["status"], "not_needed")
            self.assertFalse(result["backupCreated"])
            self.assertEqual(self.raw_scope(repository, confirmed["id"]), ("tv", 1, 15, 15))
            self.assertEqual(self.raw_scope(repository, linked["id"]), ("tv", 1, 15, 15))
            self.assertEqual(self.raw_scope(repository, unverified["id"]), ("tv", 1, 15, 15))

    def test_no_changes_do_not_create_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, source_id = self.repository(directory)
            item = self.add_item(
                repository, source_id, "stable", "Stable Show S01E15", ("tv", 1, 15, 15)
            )
            preview = self.service(repository).preview()

            self.assertEqual(preview["counts"]["changedItems"], 0)
            result = self.service(repository).apply(preview["previewFingerprint"])

            self.assertEqual(result["status"], "not_needed")
            self.assertFalse(result["backupCreated"])
            self.assertEqual(self.raw_scope(repository, item["id"]), ("tv", 1, 15, 15))
            self.assertFalse((Path(directory) / "migrations").exists())


if __name__ == "__main__":
    unittest.main()
