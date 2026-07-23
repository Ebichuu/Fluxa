from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock


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
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSymediaClient:
    def get_summary(self):
        return {
            "configured": True,
            "connected": True,
            "webUrl": "http://symedia.example.test:8095",
            "lastCheckedAt": "2026-07-16T12:00:00.000Z",
            "totals": {"records": 100, "today": 2, "failedRecent": 1},
            "latest": [],
        }


class SymediaReadRuntimeContractTests(unittest.TestCase):
    def test_unconfigured_summary_and_route_boundary(self):
        from app import main

        application = main.create_app(access_environment={})
        payload = application.test_client().get("/api/symedia/summary").get_json()
        routes = {
            (rule.rule, method)
            for rule in application.url_map.iter_rules()
            for method in (rule.methods or set())
        }
        self.assertFalse(payload["configured"])
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["error"], "未配置 SYMEDIA_BASE_URL")
        self.assertIn(("/api/symedia/summary", "GET"), routes)

    def test_token_summary_paginates_today_and_maps_latest(self):
        from app.symedia_read_runtime import SymediaReadClient, SymediaReadConfig

        first = [
            {
                "title": f"条目 {index}",
                "year": 2026,
                "type": "movie",
                "season_episode": "",
                "mode": "copy",
                "status": index != 1,
                "errmsg": "失败" if index == 1 else "",
                "date": f"2026-07-16 11:{index:02d}:00",
            }
            for index in range(50)
        ]
        second = [
            {"title": "今日补页", "status": True, "date": "2026-07-16 10:00:00"},
            {"title": "昨日", "status": True, "date": "2026-07-15 23:59:00"},
        ]
        session = FakeSession([
            FakeResponse(payload={"data": {"list": first, "total": 53374}}),
            FakeResponse(payload={"data": {"list": second, "total": 53374}}),
        ])
        client = SymediaReadClient(
            SymediaReadConfig(base_url="http://symedia.example.test:8095/", token="fixed-token"),
            session=session,
            clock=lambda: datetime(
                2026,
                7,
                16,
                12,
                0,
                0,
                tzinfo=timezone(timedelta(hours=8)),
            ),
        )

        summary = client.get_summary()

        self.assertTrue(summary["connected"])
        self.assertEqual(summary["totals"], {
            "records": 53374,
            "today": 51,
            "processedToday": 51,
            "archivedToday": 50,
            "protectedToday": 0,
            "failedToday": 1,
            "failedRecent": 1,
            "protectedRecent": 0,
        })
        self.assertEqual(summary["lastCheckedAt"], "2026-07-16T04:00:00.000Z")
        self.assertEqual(len(summary["latest"]), 5)
        self.assertFalse(summary["latest"][1]["status"])
        self.assertEqual(summary["latest"][0]["mediaType"], "movie")
        self.assertEqual(len(session.requests), 2)
        self.assertTrue(all(item[2]["headers"]["Authorization"] == "Bearer fixed-token" for item in session.requests))

    def test_low_score_rejection_is_normal_protection_not_recent_failure(self):
        from app.symedia_read_runtime import SymediaReadClient, SymediaReadConfig

        rows = [{
            "title": "保护测试",
            "status": False,
            "errmsg": "源文件评分低于目标文件，取消覆盖",
            "date": "2026-07-16 11:00:00",
        }]
        session = FakeSession([FakeResponse(payload={"data": {"list": rows, "total": 1}})])
        client = SymediaReadClient(
            SymediaReadConfig(base_url="http://symedia.example.test", token="fixed-token"),
            session=session,
            clock=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=timezone(timedelta(hours=8))),
        )

        totals = client.get_summary()["totals"]

        self.assertEqual(totals["processedToday"], 1)
        self.assertEqual(totals["archivedToday"], 0)
        self.assertEqual(totals["protectedToday"], 1)
        self.assertEqual(totals["failedToday"], 0)
        self.assertEqual(totals["failedRecent"], 0)

    def test_password_token_relogs_once_after_unauthorized(self):
        from app.symedia_read_runtime import SymediaReadClient, SymediaReadConfig

        session = FakeSession([
            FakeResponse(payload={"access_token": "token-one"}),
            FakeResponse(status=401, payload={}),
            FakeResponse(payload={"token": "token-two"}),
            FakeResponse(payload={"data": {"list": [], "total": 0}}),
        ])
        client = SymediaReadClient(
            SymediaReadConfig("http://symedia.example.test", "", "user", "password"),
            session=session,
        )

        self.assertEqual(client.list_transfer_history(), {"rows": [], "total": 0})
        self.assertEqual([item[0] for item in session.requests], ["POST", "GET", "POST", "GET"])
        self.assertEqual(session.requests[1][2]["headers"]["Authorization"], "Bearer token-one")
        self.assertEqual(session.requests[3][2]["headers"]["Authorization"], "Bearer token-two")

    def test_network_error_is_safe_and_injected_route_works(self):
        import requests

        from app import main
        from app.symedia_read_runtime import SymediaReadClient, SymediaReadConfig

        session = Mock()
        session.request.side_effect = requests.ConnectionError(
            "failed http://symedia.invalid/api/v1/history?token=must-not-escape"
        )
        failed = SymediaReadClient(
            SymediaReadConfig(base_url="http://symedia.invalid", token="must-not-escape"),
            session=session,
        ).get_summary()
        self.assertEqual(failed["error"], "Symedia 请求失败")
        self.assertNotIn("must-not-escape", str(failed))

        response = main.create_app(
            access_environment={},
            symedia_client_factory=lambda _config: FakeSymediaClient(),
        ).test_client().get("/api/symedia/summary")
        self.assertTrue(response.get_json()["connected"])


if __name__ == "__main__":
    unittest.main()
