from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.private_rss_repository import PrivateRssRepository


class PrivateRssRepositoryTests(unittest.TestCase):
    def test_source_urls_stay_internal_and_items_are_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=secret-value",
                "intervalMinutes": 5,
                "retentionDays": 7,
            })
            self.assertNotIn("feedUrl", source)
            self.assertNotIn("secret-value", str(source))
            internal = repository.get_source(source["id"], public=False)
            self.assertIn("secret-value", internal["feed_url"])
            repository.upsert_items(source["id"], [{
                "fingerprint": "one",
                "guid": "one",
                "title": "诡秘之主 S01E03 2160p HDR",
                "published_at": "2026-07-18T01:00:00Z",
                "download_url": "https://tracker.example/download?passkey=secret-value",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 3,
                "episode_end": 3,
                "version_summary": "2160P · HDR",
            }])
            result = repository.search_items(query="诡秘 HDR")
            self.assertEqual(result["total"], 1)
            self.assertNotIn("secret-value", str(result))
            self.assertTrue(result["items"][0]["hasDownload"])

    def test_duplicate_source_and_item_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            payload = {"name": "站点", "feedUrl": "https://tracker.example/rss?passkey=one"}
            source = repository.save_source(payload)
            with self.assertRaises(Exception):
                repository.save_source(payload)
            first = repository.upsert_items(source["id"], [{"fingerprint": "same", "title": "A"}])
            second = repository.upsert_items(source["id"], [{"fingerprint": "same", "title": "A2"}])
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["updated"], 1)
            self.assertEqual(repository.search_items()["total"], 1)


if __name__ == "__main__":
    unittest.main()
