from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.home_summary_repository import HomeSummaryRepository


NOW = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)


class HomeSummaryRepositoryTests(unittest.TestCase):
    def make_repository(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return HomeSummaryRepository(Path(directory.name) / "summary.sqlite3", clock=lambda: NOW)

    def test_success_write_is_atomic_and_round_trips_payload(self):
        repository = self.make_repository()
        value = repository.write_success(
            "task_pipeline", "global", {"value": 2, "items": ["safe"]},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5),
        )
        self.assertEqual(value["payload"], {"value": 2, "items": ["safe"]})
        self.assertEqual(value["confirmation"], "confirmed")
        self.assertEqual(value["version"], 1)
        updated = repository.write_success(
            "task_pipeline", "global", {"value": 3},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5),
        )
        self.assertEqual(updated["payload"], {"value": 3})
        self.assertEqual(updated["version"], 2)

    def test_failure_preserves_last_success_and_marks_partial(self):
        repository = self.make_repository()
        repository.write_success(
            "qb_activity", "global", {"active": 2},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5),
        )
        value = repository.write_failure(
            "qb_activity", "global", "QB_TIMEOUT", "qB 暂时无法确认", now=NOW + timedelta(minutes=1)
        )
        self.assertEqual(value["payload"], {"active": 2})
        self.assertEqual(value["confirmation"], "partial")
        self.assertEqual(value["lastErrorCode"], "QB_TIMEOUT")
        self.assertEqual(value["lastSuccessAt"], "2026-08-02T00:00:00.000Z")

    def test_failure_without_success_is_unknown_and_has_no_fake_payload(self):
        repository = self.make_repository()
        value = repository.write_failure(
            "archive_today", "date:2026-08-02", "ARCHIVE_TIMEOUT", "归档状态暂时无法确认", now=NOW
        )
        self.assertEqual(value["payload"], {})
        self.assertEqual(value["confirmation"], "unknown")
        self.assertEqual(value["lastSuccessAt"], "")

    def test_refresh_lease_allows_one_owner_and_recovers_after_expiry(self):
        repository = self.make_repository()
        token = repository.claim_refresh(now=NOW, lease_seconds=30, token="owner-a")
        self.assertEqual(token, "owner-a")
        self.assertIsNone(repository.claim_refresh(now=NOW + timedelta(seconds=1), token="owner-b"))
        self.assertFalse(repository.finish_refresh("owner-b", now=NOW + timedelta(seconds=2)))
        recovered = repository.claim_refresh(now=NOW + timedelta(seconds=31), token="owner-b")
        self.assertEqual(recovered, "owner-b")
        self.assertFalse(repository.finish_refresh("owner-a", now=NOW + timedelta(seconds=32)))
        self.assertTrue(repository.finish_refresh("owner-b", now=NOW + timedelta(seconds=33)))
        self.assertFalse(repository.refresh_state()["running"])

    def test_get_many_uses_explicit_date_scope_without_falling_back(self):
        repository = self.make_repository()
        repository.write_success(
            "archive_today", "date:2026-08-01", {"archivedFiles": 81},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5),
        )
        values = repository.get_many({"archive_today": "date:2026-08-02"})
        self.assertEqual(values, {})


if __name__ == "__main__":
    unittest.main()
