import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from app.subscription_workbench_cache import (
    SubscriptionWorkbenchCacheRepository,
    SubscriptionWorkbenchRefreshRuntime,
)
from app.subscription_workbench_runtime import SubscriptionWorkbenchService


NOW = datetime(2026, 8, 5, 2, 0, tzinfo=timezone.utc)


def payload():
    return {
        "ok": True,
        "lastReadAt": "2026-08-05T02:00:00Z",
        "capabilities": [],
        "stats": {
            "total": 2, "movie": 0, "tv": 2, "pending": 2, "following": 0,
            "completed": 0, "playable": 0, "actionRequired": 0,
            "reconciliationActionRequired": 1, "inLibrary": 0, "linked": 0,
            "onlyTorra": 0, "onlyFluxa": 0, "attention": 2, "unclassified": 0,
        },
        "statisticsMeta": {},
        "items": [
            {"id": "a", "title": "小芳", "mediaType": "tv", "tmdbId": "296003", "seasonNumber": 1, "reconciliationState": "remote_missing"},
            {"id": "b", "title": "小芳", "mediaType": "tv", "tmdbId": "296003", "seasonNumber": 1, "reconciliationState": "conflict"},
        ],
        "posterBackfillIds": [],
        "page": {"total": 2, "limit": 2, "offset": 0, "nextOffset": None, "hasMore": False},
        "blockedTitles": [], "errors": [], "torraSync": {}, "rss": {}, "scheduler": {},
        "reconciliation": {"ok": True, "summary": {}, "items": []},
    }


class Collector:
    def __init__(self, value=None, error=None):
        self.value = value or payload()
        self.error = error
        self.calls = 0

    def live_snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


class SubscriptionWorkbenchCacheTests(unittest.TestCase):
    def build_repository(self, directory, now=NOW):
        return SubscriptionWorkbenchCacheRepository(
            Path(directory) / "media_control_center.sqlite3",
            clock=lambda: now,
        )

    def test_success_survives_repository_restart_and_projects_page(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            repository.write_success(payload(), now=NOW)
            restarted = self.build_repository(directory, NOW + timedelta(seconds=10))
            service = SubscriptionWorkbenchService(
                Flask(__name__),
                cache_repository=restarted,
                clock=lambda: NOW,
                local_subscription_loader=lambda **_kwargs: {"items": []},
            )

            result = service.snapshot(limit=1, offset=1, media_type="tv", query="小芳")

            self.assertEqual([item["id"] for item in result["items"]], ["b"])
            self.assertEqual(result["page"], {"total": 2, "limit": 1, "offset": 1, "nextOffset": None, "hasMore": False})
            self.assertEqual(result["confirmation"], "confirmed")
            self.assertFalse(result["stale"])

    def test_failure_preserves_last_reliable_payload_and_marks_it_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            repository.write_success(payload(), now=NOW)
            token = repository.claim_refresh(now=NOW + timedelta(seconds=5))
            repository.write_failure("Torra secret endpoint failed", now=NOW + timedelta(seconds=5), token=token)

            cached = repository.get(now=NOW + timedelta(seconds=6))

            self.assertEqual(len(cached["payload"]["items"]), 2)
            self.assertTrue(cached["stale"])
            self.assertEqual(cached["refreshState"], "failed")
            self.assertTrue(cached["lastError"])
            self.assertNotIn("secret", cached["lastError"])

    def test_runtime_is_single_flight_and_writes_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            collector = Collector()
            runtime = SubscriptionWorkbenchRefreshRuntime(repository, collector, clock=lambda: NOW)

            result = runtime.run_once()

            self.assertEqual(result["status"], "success")
            self.assertEqual(collector.calls, 1)
            self.assertEqual(repository.get(now=NOW)["payload"]["stats"]["total"], 2)

    def test_runtime_uses_collection_completion_time_for_generated_at(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            collector = Collector()
            moments = iter((NOW, NOW + timedelta(seconds=30)))
            runtime = SubscriptionWorkbenchRefreshRuntime(repository, collector, clock=lambda: next(moments))

            runtime.run_once()

            cached = repository.get(now=NOW + timedelta(seconds=30))
            self.assertEqual(cached["generatedAt"], "2026-08-05T02:00:30.000Z")
            self.assertFalse(cached["stale"])

    def test_empty_cache_returns_immediate_unknown_shape_without_live_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            service = SubscriptionWorkbenchService(
                Flask(__name__),
                cache_repository=repository,
                clock=lambda: NOW,
                local_subscription_loader=lambda **_kwargs: {"items": []},
            )

            result = service.snapshot(limit=24)

            self.assertEqual(result["confirmation"], "unknown")
            self.assertEqual(result["items"], [])
            self.assertEqual(result["stats"]["total"], 0)

    def test_cached_snapshot_overlays_new_local_subscription_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            repository.write_success(payload(), now=NOW)
            local_row = {
                "subscription_key": "tv:genius:tmdb:289116:season:1",
                "title": "天才，女友",
                "media_type": "tv",
                "tmdb_id": "289116",
                "season_number": 1,
                "origin": "manual",
                "source_label": "手动订阅",
                "updated_at": "2026-08-05 10:01:00",
            }
            service = SubscriptionWorkbenchService(
                Flask(__name__),
                {"TORRA_PUSH_ENABLED": "false"},
                cache_repository=repository,
                clock=lambda: NOW,
                local_subscription_loader=lambda **_kwargs: {"items": [local_row]},
            )

            result = service.snapshot(limit=24, media_type="tv", query="天才")

            self.assertEqual(result["page"]["total"], 1)
            self.assertEqual(result["items"][0]["title"], "天才，女友")
            self.assertEqual(result["items"][0]["reconciliationState"], "only_fluxa")
            self.assertEqual(result["items"][0]["torra"]["pushState"], "disabled")
            self.assertEqual(result["stats"]["total"], 3)
            self.assertEqual(result["stats"]["onlyFluxa"], 1)

    def test_cached_snapshot_keeps_external_evidence_when_local_metadata_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.build_repository(directory)
            value = payload()
            value["items"][0].update({
                "origin": "manual",
                "qb": {"status": "active", "detail": "下载中"},
            })
            repository.write_success(value, now=NOW)
            local_row = {
                "subscription_key": "a",
                "title": "小芳（新标题）",
                "media_type": "tv",
                "tmdb_id": "296003",
                "season_number": 1,
                "origin": "manual",
            }
            service = SubscriptionWorkbenchService(
                Flask(__name__),
                cache_repository=repository,
                clock=lambda: NOW,
                local_subscription_loader=lambda **_kwargs: {"items": [local_row]},
            )

            result = service.snapshot(limit=24, query="新标题")

            self.assertEqual(result["items"][0]["title"], "小芳（新标题）")
            self.assertEqual(result["items"][0]["qb"], {"status": "active", "detail": "下载中"})


if __name__ == "__main__":
    unittest.main()
