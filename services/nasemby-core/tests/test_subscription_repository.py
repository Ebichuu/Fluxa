from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.subscription_repository import SubscriptionRepository


def item_key(item):
    return str(item.get("subscription_key") or item.get("id") or "")


class SubscriptionRepositoryTests(unittest.TestCase):
    def test_config_and_payload_round_trip_preserve_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            repository.save_config({"mode": "torra", "future": {"enabled": True}})
            repository.save_payload({
                "last_run_at": "2026-07-18 08:00:00",
                "future_meta": "kept",
                "items": [{
                    "subscription_key": "movie:1",
                    "title": "测试电影",
                    "media_type": "movie",
                    "tmdb_id": "1",
                    "future_field": {"value": 2},
                }],
            }, item_key)
            self.assertEqual(repository.load_config()["future"], {"enabled": True})
            payload = repository.load_payload()
            self.assertEqual(payload["future_meta"], "kept")
            self.assertEqual(payload["items"][0]["future_field"], {"value": 2})

    def test_upsert_mutate_delete_and_clear_are_transactional(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            replaced, saved = repository.upsert_item({"subscription_key": "tv:1", "title": "第一版"}, "tv:1")
            self.assertFalse(replaced)
            self.assertEqual(saved["title"], "第一版")
            replaced, saved = repository.upsert_item({"subscription_key": "tv:1", "title": "第二版"}, "tv:1")
            self.assertTrue(replaced)
            self.assertEqual(saved["title"], "第二版")
            mutated = repository.mutate_item("tv:1", lambda item: item.update({"season_number": 2}), item_key)
            self.assertEqual(mutated["season_number"], 2)
            removed = repository.delete_where(lambda item: item.get("title") == "第二版")
            self.assertEqual(len(removed), 1)
            repository.upsert_item({"subscription_key": "movie:2", "title": "电影"}, "movie:2")
            self.assertEqual(repository.clear_items(), 1)
            self.assertEqual(repository.load_payload()["items"], [])

    def test_duplicate_batch_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            with self.assertRaises(ValueError):
                repository.save_payload({"items": [
                    {"subscription_key": "same", "title": "A"},
                    {"subscription_key": "same", "title": "B"},
                ]}, item_key)
            self.assertEqual(repository.load_payload()["items"], [])


if __name__ == "__main__":
    unittest.main()
