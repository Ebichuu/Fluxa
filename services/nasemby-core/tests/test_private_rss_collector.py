from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.private_rss_collector import PrivateRssCollector
from app.private_rss_repository import PrivateRssRepository
from tests.test_private_rss_parser import RSS_SAMPLE


class FakeResponse:
    status_code = 200
    headers = {"ETag": "etag-one", "Last-Modified": "Fri, 18 Jul 2026 01:00:00 GMT"}

    def iter_content(self, _size):
        yield RSS_SAMPLE


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


class PrivateRssCollectorTests(unittest.TestCase):
    def test_fetch_persists_parsed_items_without_exposing_url(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss?passkey=secret"})
            session = FakeSession()
            collector = PrivateRssCollector(repository, session=session, url_validator=lambda url, allow_http=False: url)
            result = collector.fetch_source(source["id"], persist=True)
            self.assertEqual(result["items"], 1)
            self.assertEqual(repository.search_items()["total"], 1)
            self.assertEqual(len(session.calls), 1)
            self.assertNotIn("secret", str(result))


if __name__ == "__main__":
    unittest.main()
