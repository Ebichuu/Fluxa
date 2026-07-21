from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from flask import Flask

from app import discover_runtime
from app.subscription_workbench_runtime import SubscriptionWorkbenchService, register_subscription_workbench


class FakeTaskService:
    def get_chain(self):
        return {
            "generatedAt": "2026-07-21T08:00:00Z",
            "items": [{
                "id": "subscription:movie:manual",
                "title": "测试电影",
                "state": "blocked",
                "progress": 42,
                "sourceIds": {
                    "subscriptionId": "movie:manual",
                    "torraId": "torra-1",
                    "qbHashes": ["hash-1"],
                    "symediaIds": [],
                },
                "steps": [
                    {"key": "subscription", "status": "done", "detail": "Torra 已关联"},
                    {"key": "download", "status": "blocked", "detail": "qB 下载卡住"},
                    {"key": "cloud115", "status": "waiting", "detail": "等待下载完成"},
                    {"key": "library", "status": "waiting", "detail": "尚未入库"},
                ],
            }],
            "services": {
                "torra": {"connected": True, "error": "", "total": 1, "webUrl": "http://torra"},
            },
        }


class FakeTorraClient:
    def is_configured(self):
        return True


class FakeTorraSync:
    def status(self):
        return {
            "ok": True,
            "enabled": True,
            "linked": 1,
            "current": 1,
            "remoteMissing": 0,
            "errors": 0,
            "lastSyncedAt": "2026-07-21T07:55:00Z",
        }


class FakeRssRepository:
    def summary(self, enabled=False):
        return {
            "enabled": enabled,
            "sources": 2,
            "activeSources": 2,
            "errorSources": 0,
            "items": 36,
            "lastSuccessAt": "2026-07-21T07:50:00Z",
        }


class FakeRssService:
    repository = FakeRssRepository()

    def collection_enabled(self):
        return True


class SubscriptionWorkbenchRuntimeTests(unittest.TestCase):
    def test_automatic_source_merge_preserves_manual_and_torra_rows(self):
        existing = [
            {
                "subscription_key": "movie:manual",
                "title": "测试电影",
                "media_type": "movie",
                "origin": "manual",
                "source_label": "手动订阅",
            },
            {
                "subscription_key": "torra:remote-1",
                "title": "Torra 剧集",
                "media_type": "tv",
                "origin": "torra",
                "read_only": True,
                "torra_remote_id": "remote-1",
            },
        ]
        incoming = [
            {
                "subscription_key": "movie:manual",
                "title": "测试电影",
                "media_type": "movie",
                "origin": "auto",
                "source_label": "榜单来源",
                "rating": 8.8,
            },
            {
                "subscription_key": "movie:auto-new",
                "title": "自动新增",
                "media_type": "movie",
                "origin": "auto",
            },
        ]

        merged, stats = discover_runtime.merge_subscription_source_items(existing, incoming)
        by_key = {item["subscription_key"]: item for item in merged}

        self.assertEqual(set(by_key), {"movie:manual", "torra:remote-1", "movie:auto-new"})
        self.assertEqual(by_key["movie:manual"]["origin"], "manual")
        self.assertEqual(by_key["movie:manual"]["source_label"], "手动订阅")
        self.assertEqual(by_key["movie:manual"]["rating"], 8.8)
        self.assertTrue(by_key["torra:remote-1"]["read_only"])
        self.assertEqual(by_key["torra:remote-1"]["torra_remote_id"], "remote-1")
        self.assertEqual(stats, {"added": 1, "updated": 1, "preserved": 1})

    def test_automatic_source_refresh_queues_only_current_source_rows(self):
        existing = {
            "subscription_key": "movie:manual",
            "title": "手动保留",
            "media_type": "movie",
            "origin": "manual",
        }
        incoming = {
            "subscription_key": "movie:auto-new",
            "dedupe_key": "movie:auto-new",
            "title": "本轮新增",
            "media_type": "movie",
            "year": "2026",
            "origin": "auto",
        }
        config = {
            "mode": "torra",
            "douban": {
                "enabled": True,
                "movie_enabled": True,
                "tv_enabled": True,
                "movie_years": [],
                "tv_min_rating": 0,
                "exclude_titles": [],
                "sources": ["hot_movie"],
            },
        }
        with patch.object(discover_runtime, "load_subscription_config", return_value=deepcopy(config)), patch.object(
            discover_runtime, "fetch_subscription_source", return_value=[dict(incoming)]
        ), patch.object(
            discover_runtime, "normalize_subscription_item_metadata", side_effect=lambda row, **_kwargs: dict(row)
        ), patch.object(
            discover_runtime, "load_subscription_items", return_value={"items": [dict(existing)], "errors": []}
        ), patch.object(
            discover_runtime, "enrich_subscription_items", side_effect=lambda payload, **_kwargs: payload
        ), patch.object(
            discover_runtime, "write_subscription_items_data", side_effect=lambda payload: payload
        ), patch.object(
            discover_runtime, "write_subscription_config_data"
        ), patch.object(
            discover_runtime, "set_discover_item_cache"
        ), patch.object(
            discover_runtime, "write_activity"
        ), patch.object(
            discover_runtime, "queue_subscription_resource_rule_transfer", return_value={"enabled": True, "queued": 1}
        ) as queue:
            result = discover_runtime.run_subscription_now()

        self.assertEqual({item["title"] for item in result["items"]}, {"手动保留", "本轮新增"})
        queued_rows = queue.call_args.args[0]
        self.assertEqual([item["title"] for item in queued_rows], ["本轮新增"])

    def test_snapshot_returns_real_capabilities_stats_and_chain_evidence(self):
        app = Flask(__name__)
        app.extensions.update({
            "mcc_task_chain_service": FakeTaskService(),
            "mcc_torra_client": FakeTorraClient(),
            "mcc_torra_subscription_sync": FakeTorraSync(),
            "mcc_private_rss": FakeRssService(),
        })
        service = SubscriptionWorkbenchService(app, {"NASEMBY_CORE_WRITE_ENABLED": "true"})
        row = {
            "subscription_key": "movie:manual",
            "title": "测试电影",
            "media_type": "movie",
            "tmdb_id": "1",
            "origin": "manual",
            "in_library": False,
        }
        config = {
            "douban": {
                "enabled": True,
                "task_enabled": True,
                "task_time": "08:30",
                "last_run_at": "2026-07-21 08:30:00",
            },
        }

        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": [row], "errors": []}), patch.object(
            discover_runtime, "load_subscription_config", return_value=config
        ), patch.object(discover_runtime, "subscription_blocked_titles", return_value=[]):
            snapshot = service.snapshot()

        states = {item["key"]: item["state"] for item in snapshot["capabilities"]}
        self.assertEqual(states, {
            "local_write": "ready",
            "torra_connection": "ready",
            "torra_mirror": "ready",
            "rss": "ready",
            "scheduler": "ready",
        })
        self.assertEqual(snapshot["stats"]["movie"], 1)
        self.assertEqual(snapshot["stats"]["pending"], 1)
        self.assertEqual(snapshot["items"][0]["torra"]["status"], "linked")
        self.assertEqual(snapshot["items"][0]["qb"]["status"], "blocked")
        self.assertEqual(snapshot["items"][0]["blockingReason"], "qB 下载卡住")

    def test_route_returns_502_without_leaking_internal_exception(self):
        app = Flask(__name__)
        service = register_subscription_workbench(app, {})
        with patch.object(service, "snapshot", side_effect=RuntimeError("secret")):
            response = app.test_client().get("/api/v2/subscriptions/workbench")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "SUBSCRIPTION_WORKBENCH_READ_FAILED")
        self.assertNotIn("secret", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
