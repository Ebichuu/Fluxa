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
    def get_chain(self):
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


if __name__ == "__main__":
    unittest.main()
