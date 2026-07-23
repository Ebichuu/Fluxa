from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from flask import Flask

from app import discover_runtime
from app.health_state_runtime import SchedulerStatusRegistry
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
            "matches": 3,
            "matcherRan": True,
            "lastMatchAt": "2026-07-21T07:50:01Z",
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

    def test_capability_route_reports_push_and_runtime_scheduler_separately(self):
        app = Flask(__name__)
        registry = SchedulerStatusRegistry(clock=lambda: "2026-07-22T08:00:00Z")
        registry.register("subscription-task", enabled=True)
        registry.mark_started("subscription-task")
        app.extensions["mcc_scheduler_status"] = registry
        register_subscription_workbench(app, {
            "NASEMBY_CORE_WRITE_ENABLED": "true",
            "TORRA_PUSH_ENABLED": "false",
            "MCC_SUBSCRIPTION_SCHEDULER_ENABLED": "true",
        })

        response = app.test_client().get("/api/v2/subscriptions/capabilities")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["localWrite"]["enabled"])
        self.assertFalse(payload["torraPush"]["enabled"])
        self.assertTrue(payload["scheduler"]["running"])

    def test_scheduler_state_uses_global_runtime_gate_instead_of_source_config(self):
        app = Flask(__name__)
        registry = SchedulerStatusRegistry(clock=lambda: "2026-07-22T08:00:00Z")
        registry.register("subscription-task", enabled=False)
        app.extensions["mcc_scheduler_status"] = registry
        service = SubscriptionWorkbenchService(app, {
            "MCC_SUBSCRIPTION_SCHEDULER_ENABLED": "false",
        })
        config = {"douban": {"enabled": True, "task_enabled": True, "task_time": "08:30"}}
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": [], "errors": []}), patch.object(
            discover_runtime, "load_subscription_config", return_value=config
        ), patch.object(discover_runtime, "subscription_blocked_titles", return_value=[]):
            snapshot = service.snapshot()

        scheduler = next(item for item in snapshot["capabilities"] if item["key"] == "scheduler")
        self.assertEqual(scheduler["state"], "disabled")
        self.assertEqual(scheduler["detail"], "系统定时任务总开关已关闭")
        self.assertFalse(snapshot["scheduler"]["enabled"])

    def test_snapshot_paginates_after_media_type_and_query_filters(self):
        app = Flask(__name__)
        service = SubscriptionWorkbenchService(app, {})
        rows = [
            {"subscription_key": "tv:1", "title": "测试剧一", "media_type": "tv", "tmdb_id": "1"},
            {"subscription_key": "movie:2", "title": "测试电影", "media_type": "movie", "tmdb_id": "2"},
            {"subscription_key": "tv:3", "title": "测试剧二", "media_type": "tv", "tmdb_id": "3"},
        ]
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": rows, "errors": []}), patch.object(
            discover_runtime, "load_subscription_config", return_value={}
        ), patch.object(discover_runtime, "subscription_blocked_titles", return_value=[]):
            first = service.snapshot(limit=1, offset=0, media_type="tv", query="测试剧")
            second = service.snapshot(limit=1, offset=1, media_type="tv", query="测试剧")

        self.assertEqual(first["stats"]["total"], 3)
        self.assertEqual(first["page"], {"total": 2, "limit": 1, "offset": 0, "nextOffset": 1, "hasMore": True})
        self.assertEqual(first["items"][0]["title"], "测试剧一")
        self.assertEqual(second["items"][0]["title"], "测试剧二")
        self.assertFalse(second["page"]["hasMore"])

    def test_snapshot_uses_cached_poster_and_requests_local_visual_backfill(self):
        app = Flask(__name__)
        service = SubscriptionWorkbenchService(app, {"NASEMBY_CORE_WRITE_ENABLED": "true"})
        rows = [{
            "subscription_key": "tv:poster:tmdb:101:season:1",
            "title": "海报测试剧",
            "media_type": "tv",
            "tmdb_id": "101",
            "target_season": 1,
            "poster_url": "",
        }]
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": rows, "errors": []}), patch.object(
            discover_runtime,
            "resolve_subscription_visuals",
            return_value={"poster_url": "https://image.tmdb.org/t/p/w342/poster.jpg"},
        ) as resolve_visuals, patch.object(
            discover_runtime, "load_subscription_config", return_value={}
        ), patch.object(discover_runtime, "subscription_blocked_titles", return_value=[]):
            snapshot = service.snapshot(limit=24)

        self.assertEqual(snapshot["items"][0]["posterUrl"], "https://image.tmdb.org/t/p/w342/poster.jpg")
        self.assertEqual(snapshot["posterBackfillIds"], ["tv:poster:tmdb:101:season:1"])
        resolve_visuals.assert_called_once()
        self.assertEqual(resolve_visuals.call_args.args[0]["tmdb_id"], "101")
        self.assertFalse(resolve_visuals.call_args.kwargs["fetch"])

    def test_snapshot_requests_response_only_visual_backfill_for_remote_torra_item(self):
        app = Flask(__name__)
        app.extensions["mcc_subscription_reconciliation"] = type("Reconciliation", (), {
            "snapshot": lambda _self: {
                "items": [{
                    "id": "torra:remote-only",
                    "localId": "",
                    "title": "Torra 只读剧",
                    "mediaType": "tv",
                    "tmdbId": "202",
                    "seasonNumber": 1,
                    "reconciliationState": "only_torra",
                }],
            },
        })()
        service = SubscriptionWorkbenchService(app, {"NASEMBY_CORE_WRITE_ENABLED": "true"})
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": [], "errors": []}), patch.object(
            discover_runtime, "resolve_subscription_visuals", return_value={}
        ), patch.object(discover_runtime, "load_subscription_config", return_value={}), patch.object(
            discover_runtime, "subscription_blocked_titles", return_value=[]
        ):
            snapshot = service.snapshot(limit=24)

        self.assertEqual(snapshot["items"][0]["id"], "torra:remote-only")
        self.assertTrue(snapshot["items"][0]["readOnly"])
        self.assertEqual(snapshot["posterBackfillIds"], ["torra:remote-only"])

    def test_remote_completed_subscription_is_not_reported_as_pending(self):
        app = Flask(__name__)
        app.extensions["mcc_subscription_reconciliation"] = type("Reconciliation", (), {
            "snapshot": lambda _self: {
                "items": [{
                    "id": "torra:completed",
                    "localId": "",
                    "title": "已完成远端剧",
                    "mediaType": "tv",
                    "tmdbId": "303",
                    "seasonNumber": 1,
                    "reconciliationState": "only_torra",
                    "fulfillmentState": "completed",
                }],
            },
        })()
        service = SubscriptionWorkbenchService(app, {})
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": [], "errors": []}), patch.object(
            discover_runtime, "resolve_subscription_visuals", return_value={}
        ), patch.object(discover_runtime, "load_subscription_config", return_value={}), patch.object(
            discover_runtime, "subscription_blocked_titles", return_value=[]
        ):
            snapshot = service.snapshot(limit=24)

        item = snapshot["items"][0]
        self.assertEqual(item["progressText"], "Torra 订阅已完成")
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["chainState"], "completed")
        self.assertEqual(snapshot["stats"]["pending"], 0)
        self.assertEqual(snapshot["stats"]["inLibrary"], 0)

    def test_visual_backfill_updates_only_local_rows_with_exact_tmdb_identity(self):
        app = Flask(__name__)
        service = SubscriptionWorkbenchService(app, {"NASEMBY_CORE_WRITE_ENABLED": "true"})
        rows = [{
            "subscription_key": "tv:poster:tmdb:101:season:1",
            "title": "海报测试剧",
            "media_type": "tv",
            "tmdb_id": "101",
            "poster_url": "",
        }, {
            "subscription_key": "tv:no-identity",
            "title": "没有身份",
            "media_type": "tv",
            "poster_url": "",
        }]
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": rows, "errors": []}), patch.object(
            discover_runtime,
            "resolve_subscription_visuals",
            side_effect=lambda row, fetch=False: (
                {"poster_url": "https://image.tmdb.org/t/p/w342/poster.jpg"}
                if fetch and row.get("tmdb_id") == "101" else {}
            ),
        ), patch.object(
            discover_runtime,
            "supplement_subscription_visuals",
            return_value={**rows[0], "poster_url": "https://image.tmdb.org/t/p/w342/poster.jpg"},
        ) as supplement:
            result = service.backfill_visuals([
                "tv:poster:tmdb:101:season:1",
                "tv:no-identity",
                "torra:remote-only",
            ])

        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["items"][0]["posterUrl"], "https://image.tmdb.org/t/p/w342/poster.jpg")
        supplement.assert_called_once()

    def test_visual_backfill_returns_remote_only_poster_without_persisting_local_row(self):
        app = Flask(__name__)
        service = SubscriptionWorkbenchService(app, {"NASEMBY_CORE_WRITE_ENABLED": "true"})
        app.extensions["mcc_subscription_reconciliation"] = type("Reconciliation", (), {
            "snapshot": lambda _self: {
                "items": [{
                    "id": "torra:remote-only",
                    "title": "Torra 只读剧",
                    "mediaType": "tv",
                    "tmdbId": "202",
                    "reconciliationState": "only_torra",
                }],
            },
        })()
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": [], "errors": []}), patch.object(
            discover_runtime,
            "resolve_subscription_visuals",
            return_value={"poster_url": "https://image.tmdb.org/t/p/w342/remote.jpg"},
        ) as resolve_visuals, patch.object(
            discover_runtime,
            "supplement_subscription_visuals",
        ) as supplement:
            result = service.backfill_visuals(["torra:remote-only"])

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["items"], [{
            "id": "torra:remote-only",
            "posterUrl": "https://image.tmdb.org/t/p/w342/remote.jpg",
            "backdropUrl": "",
        }])
        self.assertEqual(resolve_visuals.call_args.args[0]["tmdb_id"], "202")
        self.assertTrue(resolve_visuals.call_args.kwargs["fetch"])
        supplement.assert_not_called()

    def test_visual_backfill_route_allows_response_only_when_local_write_is_disabled(self):
        app = Flask(__name__)
        service = register_subscription_workbench(app, {"NASEMBY_CORE_WRITE_ENABLED": "false"})

        with patch.object(service, "backfill_visuals", return_value={
            "ok": True,
            "scanned": 1,
            "updated": 1,
            "unchanged": 0,
            "items": [{"id": "tv:1", "posterUrl": "https://image.tmdb.org/poster.jpg"}],
            "errors": [],
        }):
            response = app.test_client().post("/api/v2/subscriptions/visual-backfills", json={"ids": ["tv:1"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["updated"], 1)

    def test_visual_backfill_does_not_persist_local_row_when_write_is_disabled(self):
        app = Flask(__name__)
        service = SubscriptionWorkbenchService(app, {"NASEMBY_CORE_WRITE_ENABLED": "false"})
        rows = [{
            "subscription_key": "tv:poster:tmdb:101:season:1",
            "title": "海报测试剧",
            "media_type": "tv",
            "tmdb_id": "101",
            "poster_url": "",
        }]
        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": rows, "errors": []}), patch.object(
            discover_runtime,
            "resolve_subscription_visuals",
            return_value={"poster_url": "https://image.tmdb.org/t/p/w342/poster.jpg"},
        ), patch.object(discover_runtime, "supplement_subscription_visuals") as supplement:
            result = service.backfill_visuals(["tv:poster:tmdb:101:season:1"])

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["items"][0]["posterUrl"], "https://image.tmdb.org/t/p/w342/poster.jpg")
        supplement.assert_not_called()

    def test_route_rejects_invalid_pagination(self):
        app = Flask(__name__)
        register_subscription_workbench(app, {})
        response = app.test_client().get("/api/v2/subscriptions/workbench?limit=500")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "SUBSCRIPTION_PAGE_INVALID")


if __name__ == "__main__":
    unittest.main()
