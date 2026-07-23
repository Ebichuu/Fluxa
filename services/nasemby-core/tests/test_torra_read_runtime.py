from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, urlsplit(url).path, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeTorraClient:
    def get_summary(self):
        return {
            "configured": True,
            "connected": True,
            "webUrl": "http://torra.example.test:9029",
            "lastCheckedAt": "2026-07-16T11:00:00.000Z",
            "counts": {"total": 3, "active": 1, "completed": 1, "running": 1},
        }


class TorraReadRuntimeContractTests(unittest.TestCase):
    def test_unconfigured_summary_and_public_route_boundary(self):
        from app import main

        application = main.create_app(access_environment={})
        response = application.test_client().get("/api/torra/summary")
        routes = {
            (rule.rule, method)
            for rule in application.url_map.iter_rules()
            for method in (rule.methods or set())
        }

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["counts"], {
            "total": 0,
            "active": 0,
            "completed": 0,
            "running": 0,
        })
        self.assertEqual(payload["error"], "未配置 Torra 地址或认证信息")
        self.assertIn(("/api/torra/summary", "GET"), routes)
        self.assertNotIn(("/api/v1/subscriptions/save", "POST"), routes)

    def test_token_summary_extracts_nested_rows_and_counts(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        rows = [
            {"id": "active", "enabled": True, "completed": False},
            {"id": "completed", "enabled": True, "completed": True},
            {"id": "disabled", "enabled": False, "completed": False, "is_running": True},
        ]
        session = FakeSession([FakeResponse(payload={"data": {"subscriptions": rows}})])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test:9029/", token="fixed-token"),
            session=session,
            clock=lambda: datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc),
        )

        summary = client.get_summary()

        self.assertEqual(summary, {
            "configured": True,
            "connected": True,
            "webUrl": "http://torra.example.test:9029",
            "lastCheckedAt": "2026-07-16T11:00:00.000Z",
            "counts": {"total": 3, "active": 1, "completed": 1, "running": 1},
        })
        self.assertEqual(session.requests[0][1], "/api/v1/subscriptions")
        self.assertEqual(session.requests[0][2]["headers"]["Authorization"], "Bearer fixed-token")

    def test_password_auth_relogs_once_after_unauthorized(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([
            FakeResponse(payload={"access_token": "token-one"}),
            FakeResponse(status=401, payload={}),
            FakeResponse(payload={"token": "token-two"}),
            FakeResponse(payload={"subscriptions": []}),
        ])
        client = TorraReadClient(
            TorraReadConfig(
                base_url="http://torra.example.test",
                username="user",
                password="password",
            ),
            session=session,
        )

        self.assertEqual(client.list_subscriptions(), [])
        self.assertEqual([item[0] for item in session.requests], ["POST", "GET", "POST", "GET"])
        self.assertEqual(session.requests[0][2]["data"], {
            "username": "user",
            "password": "password",
        })
        self.assertEqual(session.requests[1][2]["headers"]["Authorization"], "Bearer token-one")
        self.assertEqual(session.requests[3][2]["headers"]["Authorization"], "Bearer token-two")

    def test_secupload_summary_exposes_only_readable_plugin_evidence(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([FakeResponse(payload={
            "success": True,
            "data": {
                "manifest": {"key": "secupload_115", "enabled": True},
                "config_items": [{
                    "item_id": "category-tv",
                    "name": "电视剧",
                    "enabled": True,
                    "values": {"cookie": "must-not-escape", "temp_path": "/private/pending"},
                    "updated_at": "2026-07-23T15:00:00",
                }],
                "tasks": [{
                    "key": "retry_pending",
                    "name": "重试临时目录",
                    "allow_schedule": True,
                    "allow_manual_run": True,
                }],
                "schedules": [{
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "enabled": True,
                    "cron": "0 */8 * * *",
                    "next_run_at": "2026-07-24T00:00:00+08:00",
                    "last_run_at": "2026-07-23T16:00:00",
                }],
                "recent_runs": [{
                    "run_id": "run-1",
                    "task_key": "retry_pending",
                    "target_item_id": "category-tv",
                    "trigger": "schedule",
                    "status": "success",
                    "message": "电视剧 临时目录重试完成，成功 4 个，失败 1 个",
                    "started_at": "2026-07-23T16:00:00",
                    "finished_at": "2026-07-23T16:00:07",
                    "created_at": "2026-07-23T16:00:00",
                }],
            },
        })])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
            clock=lambda: datetime(2026, 7, 23, 8, 30, tzinfo=timezone.utc),
        )

        summary = client.get_secupload_summary()

        self.assertTrue(summary["connected"])
        self.assertTrue(summary["readable"])
        self.assertFalse(summary["perFileEvidence"])
        self.assertEqual(summary["activeRuns"], 0)
        self.assertEqual(summary["latestRun"]["counts"], {"success": 4, "failed": 1})
        self.assertEqual(summary["nextRunAt"], "2026-07-24T00:00:00+08:00")
        self.assertEqual(summary["configItems"], [{
            "itemId": "category-tv",
            "name": "电视剧",
            "enabled": True,
            "updatedAt": "2026-07-23T15:00:00",
        }])
        self.assertNotIn("must-not-escape", str(summary))
        self.assertNotIn("/private/pending", str(summary))
        self.assertEqual(session.requests[0][1], "/api/v1/plugins/secupload_115")

    def test_duplicate_matching_prefers_tmdb_type_and_season(self):
        from app.torra_read_runtime import find_subscription

        rows = [
            {"id": "wrong-season", "tmdb_id": 100, "media_type": "tv", "season_number": 1},
            {"id": "right-season", "tmdb_id": "100", "media_type": "series", "season_number": 2},
            {"id": "movie", "name": "测试 电影", "media_type": "电影", "year": "2026"},
        ]

        self.assertEqual(find_subscription(rows, {
            "mediaType": "tv",
            "tmdbId": "100",
            "seasonNumber": 2,
            "title": "无关标题",
            "year": "2026",
        })["id"], "right-season")
        self.assertEqual(find_subscription(rows, {
            "mediaType": "movie",
            "tmdbId": "",
            "seasonNumber": 0,
            "title": "测试电影",
            "year": "2026",
        })["id"], "movie")

    def test_safe_push_uses_nonempty_path_downloader_and_runs_saved_subscription(self):
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = FakeSession([
            FakeResponse(payload={"subscriptions": []}),
            FakeResponse(payload={"success": True, "message": "saved"}),
            FakeResponse(payload={"success": True, "message": "running"}),
        ])
        client = TorraReadClient(
            TorraReadConfig(base_url="http://torra.example.test", token="fixed-token"),
            session=session,
        )
        subscription = {
            "id": "mcc_tv_100_2",
            "name": "测试剧",
            "media_type": "tv",
            "tmdb_id": 100,
            "season_number": 2,
            "year": "2026",
            "downloader_id": "downloader-1",
            "save_path": "/downloads/03-日韩剧",
        }

        result = client.push_subscription(subscription)

        self.assertTrue(result["success"])
        self.assertTrue(result["pushed"])
        self.assertTrue(result["searchTriggered"])
        self.assertEqual([request[0] for request in session.requests], ["GET", "POST", "POST"])
        self.assertEqual(session.requests[1][1], "/api/v1/subscriptions/save")
        self.assertEqual(session.requests[1][2]["json"], {"subscription": subscription})
        self.assertEqual(session.requests[2][1], "/api/v1/subscriptions/run/mcc_tv_100_2")

    def test_network_error_is_safe_and_injected_route_works(self):
        import requests

        from app import main
        from app.torra_read_runtime import TorraReadClient, TorraReadConfig

        session = Mock()
        session.request.side_effect = requests.ConnectionError(
            "failed http://torra.invalid/api/v1/subscriptions?token=must-not-escape"
        )
        failed = TorraReadClient(
            TorraReadConfig(base_url="http://torra.invalid", token="must-not-escape"),
            session=session,
        ).get_summary()
        self.assertFalse(failed["connected"])
        self.assertEqual(failed["error"], "Torra 请求失败")
        self.assertNotIn("must-not-escape", str(failed))

        response = main.create_app(
            access_environment={},
            torra_client_factory=lambda _config: FakeTorraClient(),
        ).test_client().get("/api/torra/summary")
        self.assertTrue(response.get_json()["connected"])


if __name__ == "__main__":
    unittest.main()
