from __future__ import annotations

import unittest

from app.adapters.moviepilot import MoviePilotAdapter, normalize_api_root


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.content = b"1"

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "params": params, "json": json})
        return _FakeResponse(
            200,
            [
                {
                    "id": 42,
                    "name": "测试剧集",
                    "type": "电视剧",
                    "tmdbid": 12345,
                    "season": 2,
                    "year": "2026",
                    "filter_groups": ["本站官组优先"],
                }
            ],
        )


class MoviePilotAdapterTests(unittest.TestCase):
    def test_normalize_api_root_accepts_with_or_without_api_v1(self):
        self.assertEqual(normalize_api_root("http://192.168.50.50:3001/api/v1"), "http://192.168.50.50:3001/api/v1")
        self.assertEqual(normalize_api_root("http://192.168.50.50:3001/api/v1/"), "http://192.168.50.50:3001/api/v1")
        self.assertEqual(normalize_api_root("http://192.168.50.50:3001"), "http://192.168.50.50:3001/api/v1")

    def test_uses_x_api_key_header_and_verified_subscribe_endpoint(self):
        sess = _FakeSession()
        adapter = MoviePilotAdapter(
            {
                "baseUrl": "http://192.168.50.50:3001/api/v1",
                "webUrl": "http://192.168.50.50:3000",
                "apiToken": "secret-token-xyz",
            },
            session=sess,
        )
        subs = adapter.list_subscriptions()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["mediaKey"], "tv:12345")
        self.assertIn("本站官组优先", subs[0]["ruleSummary"])
        self.assertEqual(subs[0]["webUrl"], "http://192.168.50.50:3000/subscribe/tv")

        self.assertEqual(len(sess.calls), 1)
        call = sess.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], "http://192.168.50.50:3001/api/v1/subscribe/")
        self.assertEqual(call["headers"].get("X-API-KEY"), "secret-token-xyz")
        # Token must NEVER leak into query string params
        self.assertIsNone(call["params"])


if __name__ == "__main__":
    unittest.main()
