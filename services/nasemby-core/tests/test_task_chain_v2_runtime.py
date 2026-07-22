from __future__ import annotations

import unittest
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from app.resource_identity_runtime import chain_id
from app.resource_task_repository import ResourceTaskRepository
from app.task_chain_v2_runtime import TaskChainV2Service, adapt_task_chain, register_task_chain_v2


class FakeTaskChain:
    def __init__(self):
        self.calls = 0

    def get_chain(self):
        self.calls += 1
        return {
            "generatedAt": "2026-07-22T03:00:00Z",
            "items": [{
                "id": "subscription:1", "title": "测试剧", "mediaType": "tv", "tmdbId": "101", "seasonNumber": 2,
                "state": "blocked", "confidence": "strong",
                "steps": [{"key": "download", "label": "获取 / 下载", "status": "blocked", "evidence": "verified", "detail": "qB 卡住", "source": "qBittorrent"}],
                "sourceIds": {"subscriptionId": "sub-1", "qbHashes": ["hash-1"], "symediaIds": []},
            }],
            "services": {},
        }


class TaskChainV2RuntimeTests(unittest.TestCase):
    def test_identity_keys_are_stable_and_health_is_independent(self):
        item = adapt_task_chain(FakeTaskChain().get_chain(), now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))["items"][0]
        self.assertEqual(item["mediaKey"], "tv:tmdb:101")
        self.assertEqual(item["targetKey"], "tv:tmdb:101:season:2")
        self.assertEqual(item["artifactKeys"], ["artifact:hash-1"])
        self.assertTrue(item["chainId"].startswith("chain:"))
        self.assertEqual(item["healthState"], "action_required")
        self.assertEqual(item["stages"][0]["reasonCode"], "DOWNLOAD_BLOCKED")
        self.assertFalse(item["stages"][0]["actions"]["retry"])
        self.assertEqual(
            chain_id(item["mediaKey"], item["targetKey"], ["artifact:old"]),
            chain_id(item["mediaKey"], item["targetKey"], ["artifact:new"]),
        )

    def test_health_filter_is_applied_after_identity_adaptation(self):
        chain = FakeTaskChain().get_chain()
        self.assertEqual(len(adapt_task_chain(chain, health_filter="normal")["items"]), 0)
        self.assertEqual(len(adapt_task_chain(chain, health_filter="action_required")["items"]), 1)

    def test_route_rejects_invalid_filter_and_returns_v2_contract(self):
        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()
        invalid = client.get("/api/v2/tasks/chains?health=bad")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(set(invalid.get_json()), {"code", "error", "request_id"})
        response = client.get("/api/v2/tasks/chains?health=action_required")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["contractVersion"], 2)
        self.assertEqual(len(response.get_json()["items"]), 1)

    def test_filtered_snapshot_persists_full_chain_before_response_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Flask(__name__)
            app.extensions["mcc_task_chain_service"] = FakeTaskChain()
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )
            service = TaskChainV2Service(
                app,
                repository=repository,
                clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc),
            )

            payload = service.snapshot(health_filter="normal")
            expected_chain_id = chain_id("tv:tmdb:101", "tv:tmdb:101:season:2")

            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["ledger"]["chains"], 1)
            self.assertEqual(repository.get_chain(expected_chain_id)["health_state"], "action_required")

    def test_duplicate_target_records_are_merged_into_one_chain(self):
        chain = FakeTaskChain().get_chain()
        chain["items"].append({
            "id": "qb:hash-2", "title": "测试剧", "mediaType": "tv", "tmdbId": "101", "seasonNumber": 2,
            "state": "completed", "confidence": "strong", "origin": "download", "progress": 100,
            "steps": [{"key": "download", "label": "获取 / 下载", "status": "done", "evidence": "verified", "detail": "下载完成", "source": "qBittorrent"}],
            "sourceIds": {"subscriptionId": "", "qbHashes": ["hash-2"], "symediaIds": []},
        })

        payload = adapt_task_chain(chain, now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))

        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["counts"]["total"], 1)
        self.assertEqual(payload["items"][0]["relatedRecords"], 2)
        self.assertEqual(payload["items"][0]["sourceIds"]["qbHashes"], ["hash-1", "hash-2"])
        self.assertEqual(payload["items"][0]["artifactKeys"], ["artifact:hash-1", "artifact:hash-2"])
        self.assertEqual(payload["items"][0]["healthState"], "action_required")

    def test_merged_progress_uses_the_whole_stage_chain(self):
        chain = FakeTaskChain().get_chain()
        chain["items"][0]["progress"] = 100
        chain["items"][0]["steps"] = [
            {"key": "subscription", "label": "订阅", "status": "unknown", "evidence": "missing"},
            {"key": "download", "label": "qB 下载", "status": "done", "evidence": "verified"},
            {"key": "cloud115", "label": "115 接管", "status": "active", "evidence": "inferred"},
            {"key": "library", "label": "整理与入库", "status": "waiting", "evidence": "missing"},
        ]

        item = adapt_task_chain(chain, now=datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))["items"][0]

        self.assertEqual(item["progress"], 38)
        self.assertNotEqual(item["progress"], chain["items"][0]["progress"])

    def test_list_is_paginated_summary_and_detail_keeps_evidence(self):
        app = Flask(__name__)
        fake = FakeTaskChain()
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        listing = client.get("/api/v2/tasks/chains?limit=1").get_json()
        chain_id_value = listing["items"][0]["chainId"]
        self.assertEqual(listing["page"], {"total": 1, "offset": 0, "limit": 1, "nextOffset": None, "hasMore": False})
        self.assertNotIn("stages", listing["items"][0])
        self.assertIn("stageSummary", listing["items"][0])

        detail = client.get(f"/api/v2/tasks/chains/{chain_id_value}").get_json()
        self.assertEqual(detail["item"]["chainId"], chain_id_value)
        self.assertTrue(detail["item"]["stages"])
        self.assertTrue(detail["item"]["artifactKeys"])
        self.assertEqual(fake.calls, 1)

    def test_summary_and_conditional_list_share_cached_snapshot(self):
        app = Flask(__name__)
        fake = FakeTaskChain()
        app.extensions["mcc_task_chain_service"] = fake
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        summary = client.get("/api/v2/tasks/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.get_json()["counts"]["total"], 1)
        listing = client.get("/api/v2/tasks/chains")
        unchanged = client.get("/api/v2/tasks/chains", headers={"If-None-Match": listing.headers["ETag"]})
        self.assertEqual(unchanged.status_code, 304)
        self.assertEqual(fake.calls, 1)

    def test_route_validates_pagination_and_missing_detail(self):
        app = Flask(__name__)
        app.extensions["mcc_task_chain_service"] = FakeTaskChain()
        register_task_chain_v2(app, clock=lambda: datetime(2026, 7, 22, 3, 1, tzinfo=timezone.utc))
        client = app.test_client()

        invalid = client.get("/api/v2/tasks/chains?limit=0")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "TASK_PAGINATION_INVALID")
        missing = client.get("/api/v2/tasks/chains/chain:missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["code"], "TASK_CHAIN_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
