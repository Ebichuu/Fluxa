from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from app.private_rss_collector import PrivateRssCollector, SourceFetchInProgressError
from app.private_rss_repository import PrivateRssRepository
from tests.test_private_rss_parser import HDHOME_FIXTURE, MTEAM_FIXTURE, QINGWA_FIXTURE, RSS_SAMPLE, ZMPT_FIXTURE


class FakeResponse:
    def __init__(self, status_code=200, headers=None, payload=RSS_SAMPLE):
        self.status_code = status_code
        self.headers = headers or {"ETag": "etag-one", "Last-Modified": "Fri, 18 Jul 2026 01:00:00 GMT"}
        self.payload = payload

    def iter_content(self, _size):
        yield self.payload

    def close(self):
        return None


class FakeSession:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ConcurrentSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def get(self, url, **kwargs):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.08)
            return super().get(url, **kwargs)
        finally:
            with self.lock:
                self.active -= 1


class BlockingSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def get(self, url, **kwargs):
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("test timeout")
        return super().get(url, **kwargs)


class PrivateRssCollectorTests(unittest.TestCase):
    def test_fetch_persists_parsed_items_without_exposing_url(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            cases = (
                ("标准样本", RSS_SAMPLE, 1),
                ("M-Team", MTEAM_FIXTURE.read_bytes(), 5),
                ("HDHome", HDHOME_FIXTURE.read_bytes(), 5),
                ("织梦", ZMPT_FIXTURE.read_bytes(), 5),
                ("青蛙", QINGWA_FIXTURE.read_bytes(), 5),
            )
            for index, (name, payload, expected_items) in enumerate(cases):
                source = repository.save_source({
                    "name": name,
                    "feedUrl": f"https://tracker{index}.example/rss?passkey=secret-{index}",
                })
                session = FakeSession(FakeResponse(payload=payload))
                collector = PrivateRssCollector(
                    repository,
                    session=session,
                    url_validator=lambda url, allow_http=False: url,
                )
                result = collector.fetch_source(source["id"], persist=True)
                self.assertEqual(result["items"], expected_items)
                self.assertEqual(result["inserted"], expected_items)
                self.assertEqual(len(session.calls), 1)
                self.assertNotIn("secret", str(result))

            public_items = repository.search_items(limit=100)
            self.assertEqual(public_items["total"], 21)
            self.assertNotIn("tracker.example", str(public_items))
            self.assertNotIn("secret", str(public_items))

    def test_429_uses_retry_after_without_recording_sensitive_url(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss?passkey=secret"})
            session = FakeSession(FakeResponse(429, {"Retry-After": "120"}))
            collector = PrivateRssCollector(repository, session=session, url_validator=lambda url, allow_http=False: url)

            with self.assertRaisesRegex(RuntimeError, "RSS 获取或解析失败"):
                collector.fetch_source(source["id"], persist=True)

            internal = repository.get_source(source["id"], public=False)
            self.assertEqual(internal["failure_count"], 1)
            self.assertEqual(internal["last_error"], "http_429")
            self.assertEqual(internal["backoff_until"], internal["next_poll_at"])
            self.assertNotIn("secret", str(repository.get_source(source["id"])))
            self.assertEqual(repository.due_sources(), [])
            with closing(repository.runtime.connect()) as connection:
                run = connection.execute("SELECT http_status, message FROM rss_fetch_runs").fetchone()
            self.assertEqual(run["http_status"], 429)
            self.assertEqual(run["message"], "http_429")

    def test_due_fetches_use_two_slots_and_same_source_is_not_overlapped(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            for index in range(3):
                repository.save_source({
                    "name": f"测试站 {index}",
                    "feedUrl": f"https://tracker{index}.example/rss?passkey=secret",
                })
            concurrent_session = ConcurrentSession()
            collector = PrivateRssCollector(
                repository,
                session=concurrent_session,
                url_validator=lambda url, allow_http=False: url,
            )
            results = collector.run_due()
            self.assertEqual([result["status"] for result in results], ["success", "success", "success"])
            self.assertEqual(concurrent_session.max_active, 2)

            source_id = repository.list_sources()[0]["id"]
            blocking_session = BlockingSession()
            collector = PrivateRssCollector(
                repository,
                session=blocking_session,
                url_validator=lambda url, allow_http=False: url,
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(collector.fetch_source, source_id, False)
                self.assertTrue(blocking_session.started.wait(1))
                with self.assertRaises(SourceFetchInProgressError):
                    collector.fetch_source(source_id, persist=False)
                blocking_session.release.set()
                self.assertEqual(first.result()["status"], "success")

    def test_new_match_is_woken_only_after_atomic_item_insert(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            wake_calls = []

            def create_match(connection, rows):
                stored = connection.execute("SELECT COUNT(*) AS count FROM rss_items").fetchone()["count"]
                self.assertEqual(stored, len(rows))
                return [{"id": "match-one"}]

            collector = PrivateRssCollector(
                repository,
                session=FakeSession(),
                url_validator=lambda url, allow_http=False: url,
                item_matcher=create_match,
                match_waker=lambda match_ids: wake_calls.append(list(match_ids)),
            )
            result = collector.fetch_source(source["id"], persist=True)

            self.assertEqual(result["inserted"], 1)
            self.assertNotIn("_match_ids", result)
            self.assertEqual(wake_calls, [["match-one"]])


if __name__ == "__main__":
    unittest.main()
