from __future__ import annotations

import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


class FakeResponse:
    def __init__(self, status=200, payload=None, text="", content_type="application/json", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.lock = threading.Lock()

    def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        with self.lock:
            self.requests.append((method, path, kwargs))
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response


class FakeQbClient:
    def summary(self):
        return {
            "configured": True,
            "connected": True,
            "webUrl": "http://qb.example.test:8080",
            "lastCheckedAt": "2026-07-16T10:00:00.000Z",
            "version": "v4.3.9",
            "transfer": {"downloadSpeed": 1024, "uploadSpeed": 512},
            "counts": {
                "total": 1,
                "active": 1,
                "downloading": 1,
                "stalled": 0,
                "completed": 0,
                "paused": 0,
            },
            "tasks": [],
        }


class QbittorrentRuntimeContractTests(unittest.TestCase):
    def test_unconfigured_summary_and_route_boundary(self):
        from app import main

        application = main.create_app(access_environment={})
        response = application.test_client().get("/api/qbittorrent/summary")
        routes = {
            (rule.rule, method)
            for rule in application.url_map.iter_rules()
            for method in (rule.methods or set())
        }

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "configured": False,
            "connected": False,
            "webUrl": "",
            "lastCheckedAt": response.get_json()["lastCheckedAt"],
            "version": "",
            "transfer": {"downloadSpeed": 0, "uploadSpeed": 0},
            "counts": {
                "total": 0,
                "active": 0,
                "downloading": 0,
                "stalled": 0,
                "completed": 0,
                "paused": 0,
            },
            "tasks": [],
            "error": "未配置 QB_BASE_URL",
        })
        self.assertIn(("/api/qbittorrent/summary", "GET"), routes)
        self.assertIn(("/api/qbittorrent/actions/pause", "POST"), routes)
        self.assertIn(("/api/qbittorrent/actions/resume", "POST"), routes)
        action_response = application.test_client().post(
            "/api/qbittorrent/actions/pause",
            json={"hashes": ["a" * 40]},
        )
        self.assertEqual(action_response.status_code, 503)
        self.assertEqual(action_response.get_json()["code"], "QB_NOT_CONFIGURED")

    def test_summary_logs_in_once_maps_and_sorts_tasks(self):
        from app.qbittorrent_runtime import QbittorrentClient, QbittorrentConfig

        tasks = [
            {"hash": "completed", "name": "E", "progress": 1, "state": "uploading", "completion_on": 5},
            {"hash": "paused", "name": "D", "progress": 0.4, "state": "pausedDL", "added_on": 4},
            {"hash": "queued", "name": "C", "progress": 0.2, "state": "queuedDL", "added_on": 3},
            {"hash": "downloading", "name": "B", "progress": 0.5, "state": "downloading", "dlspeed": 4096, "added_on": 2},
            {
                "hash": "stalled",
                "name": "A",
                "progress": 0.3,
                "state": "stalledDL",
                "save_path": "/downloads/a",
                "category": "movie",
                "tags": "pt",
                "size": 1000,
                "downloaded": 300,
                "eta": 3600,
                "added_on": 1,
            },
        ]
        session = FakeSession({
            "/api/v2/auth/login": FakeResponse(
                text="Ok.",
                content_type="text/plain",
                headers={"Set-Cookie": "SID=contract-cookie; HttpOnly; path=/"},
            ),
            "/api/v2/app/version": FakeResponse(text="v4.3.9", content_type="text/plain"),
            "/api/v2/transfer/info": FakeResponse(payload={
                "dl_info_speed": 1024,
                "up_info_speed": 512,
            }),
            "/api/v2/torrents/info": FakeResponse(payload=tasks),
        })
        client = QbittorrentClient(
            QbittorrentConfig(
                base_url="http://qb.example.test:8080/",
                username="mcc-user",
                password="mcc-password",
            ),
            session=session,
            clock=lambda: datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc),
        )

        summary = client.summary()

        self.assertTrue(summary["configured"])
        self.assertTrue(summary["connected"])
        self.assertEqual(summary["webUrl"], "http://qb.example.test:8080")
        self.assertEqual(summary["version"], "v4.3.9")
        self.assertEqual(summary["transfer"], {"downloadSpeed": 1024, "uploadSpeed": 512})
        self.assertEqual(summary["counts"], {
            "total": 5,
            "active": 2,
            "downloading": 1,
            "stalled": 1,
            "completed": 1,
            "paused": 1,
        })
        self.assertEqual(
            [item["status"] for item in summary["tasks"]],
            ["stalled", "downloading", "queued", "paused", "completed"],
        )
        self.assertEqual(summary["tasks"][0]["stateLabel"], "卡住")
        self.assertEqual(summary["tasks"][0]["savePath"], "/downloads/a")
        self.assertEqual(summary["lastCheckedAt"], "2026-07-16T10:00:00.000Z")
        self.assertEqual([item[1] for item in session.requests].count("/api/v2/auth/login"), 1)
        get_requests = [item for item in session.requests if item[0] == "GET"]
        self.assertEqual(len(get_requests), 3)
        self.assertTrue(all(item[2]["headers"]["Cookie"] == "SID=contract-cookie" for item in get_requests))
        self.assertEqual(session.requests[0][2]["data"], {
            "username": "mcc-user",
            "password": "mcc-password",
        })

    def test_login_failure_and_network_error_return_safe_offline_summary(self):
        import requests

        from app.qbittorrent_runtime import QbittorrentClient, QbittorrentConfig

        login_failure = QbittorrentClient(
            QbittorrentConfig("http://qb.example.test", "user", "password"),
            session=FakeSession({
                "/api/v2/auth/login": FakeResponse(status=403, text="Fails.", content_type="text/plain"),
            }),
        ).summary()
        self.assertFalse(login_failure["connected"])
        self.assertEqual(login_failure["error"], "qBittorrent 登录失败：403")

        session = Mock()
        session.request.side_effect = requests.ConnectionError(
            "failed http://qb.example.test/api/v2/app/version?password=must-not-escape"
        )
        network_failure = QbittorrentClient(
            QbittorrentConfig(base_url="http://qb.example.test"),
            session=session,
        ).summary()
        self.assertFalse(network_failure["connected"])
        self.assertEqual(network_failure["error"], "qBittorrent 请求失败")
        self.assertNotIn("must-not-escape", str(network_failure))

    def test_pause_posts_form_with_single_request_cookie(self):
        from app.qbittorrent_runtime import QbittorrentClient, QbittorrentConfig

        session = FakeSession({
            "/api/v2/auth/login": FakeResponse(
                text="Ok.",
                content_type="text/plain",
                headers={"Set-Cookie": "SID=action-cookie; HttpOnly; path=/"},
            ),
            "/api/v2/torrents/pause": FakeResponse(
                text="",
                content_type="text/plain",
            ),
        })
        client = QbittorrentClient(
            QbittorrentConfig(
                base_url="http://qb.example.test:8080",
                username="mcc-user",
                password="mcc-password",
            ),
            session=session,
        )

        client.set_paused("pause", ["a" * 40, "b" * 40])

        action_request = next(
            item for item in session.requests if item[1] == "/api/v2/torrents/pause"
        )
        self.assertEqual(action_request[0], "POST")
        self.assertEqual(action_request[2]["headers"]["Cookie"], "SID=action-cookie")
        self.assertEqual(action_request[2]["data"], {"hashes": f"{'a' * 40}|{'b' * 40}"})

    def test_public_route_uses_injected_read_client(self):
        from app import main

        response = main.create_app(
            access_environment={},
            qb_client_factory=lambda _config: FakeQbClient(),
        ).test_client().get("/api/qbittorrent/summary")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["connected"])
        self.assertEqual(response.get_json()["version"], "v4.3.9")


if __name__ == "__main__":
    unittest.main()
