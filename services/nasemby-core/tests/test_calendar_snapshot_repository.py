from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.calendar_snapshot_repository import CalendarRefreshConflict, CalendarSnapshotRepository


NOW = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)


class CalendarSnapshotRepositoryTests(unittest.TestCase):
    def make_repository(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CalendarSnapshotRepository(Path(directory.name) / "calendar.sqlite3", clock=lambda: NOW)

    def claim(self, repository, **scope):
        repository.request_refresh(**scope, now=NOW, idempotency_key="refresh-1")
        return repository.claim_next(now=NOW, owner="worker-1")

    def test_success_round_trips_scope_and_payload(self):
        repository = self.make_repository()
        claim = self.claim(repository, year=2026, month=8, media_type="tv", include_unlinked=True)
        value = repository.complete_success(
            claim,
            {"ok": True, "calendar": {"entries": [{"title": "测试"}]}},
            observed_at=NOW,
            fresh_until=NOW + timedelta(minutes=5),
            now=NOW,
        )
        self.assertEqual(value["scopeKey"], "2026-08:tv:1")
        self.assertEqual(value["payload"]["calendar"]["entries"][0]["title"], "测试")
        self.assertEqual(value["effectiveConfirmation"], "confirmed")

    def test_failure_preserves_last_reliable_payload(self):
        repository = self.make_repository()
        first = self.claim(repository, year=2026, month=8, media_type="all", include_unlinked=False)
        repository.complete_success(
            first, {"ok": True, "calendar": {"entries": [{"title": "可靠结果"}]}},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5), now=NOW,
        )
        repository.request_refresh(
            2026, 8, "all", False, now=NOW + timedelta(minutes=6), idempotency_key="refresh-2"
        )
        second = repository.claim_next(now=NOW + timedelta(minutes=6), owner="worker-2")
        value = repository.complete_failure(
            second, "TimeoutError", "日历刷新暂时失败", now=NOW + timedelta(minutes=6)
        )
        self.assertEqual(value["payload"]["calendar"]["entries"][0]["title"], "可靠结果")
        self.assertEqual(value["effectiveConfirmation"], "partial")
        self.assertEqual(value["lastErrorCode"], "TimeoutError")

    def test_empty_cache_stays_absent_after_refresh_failure(self):
        repository = self.make_repository()
        claim = self.claim(repository, year=2026, month=8, media_type="all", include_unlinked=False)
        repository.complete_failure(claim, "TimeoutError", "失败", now=NOW)
        self.assertIsNone(repository.get(2026, 8, "all", False, now=NOW))
        self.assertEqual(repository.queue_state(2026, 8)["status"], "failed")

    def test_scope_isolation_and_single_lease(self):
        repository = self.make_repository()
        repository.request_refresh(2026, 8, "tv", False, now=NOW, idempotency_key="tv")
        repository.request_refresh(2026, 8, "movie", False, now=NOW, idempotency_key="movie")
        first = repository.claim_next(now=NOW, owner="one")
        second = repository.claim_next(now=NOW, owner="two")
        self.assertNotEqual(first["scopeKey"], second["scopeKey"])
        self.assertIsNone(repository.claim_next(now=NOW, owner="three"))

    def test_idempotency_key_cannot_cross_scopes(self):
        repository = self.make_repository()
        repository.request_refresh(2026, 8, "tv", False, now=NOW, idempotency_key="same")
        with self.assertRaises(CalendarRefreshConflict):
            repository.request_refresh(2026, 8, "movie", False, now=NOW, idempotency_key="same")


if __name__ == "__main__":
    unittest.main()
