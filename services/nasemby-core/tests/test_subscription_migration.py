from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.subscription_migration import migrate_legacy_subscription_files
from app.subscription_repository import SubscriptionRepository


class SubscriptionMigrationTests(unittest.TestCase):
    def test_migrates_backs_up_and_reports_without_deleting_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "discover_subscriptions.json"
            items_path = root / "discover_subscription_items.json"
            config_path.write_text(json.dumps({"mode": "torra"}, ensure_ascii=False), encoding="utf-8")
            items_path.write_text(json.dumps({"items": [{
                "subscription_key": "movie:1", "title": "测试电影", "media_type": "movie", "tmdb_id": "1"
            }]}, ensure_ascii=False), encoding="utf-8")
            repository = SubscriptionRepository(root / "media_control_center.sqlite3")
            result = migrate_legacy_subscription_files(
                repository, config_path, items_path, lambda item: item.get("subscription_key")
            )
            self.assertTrue(result["migrated"])
            self.assertTrue(config_path.exists())
            self.assertTrue(items_path.exists())
            self.assertEqual(repository.load_config()["mode"], "torra")
            self.assertEqual(repository.load_payload()["items"][0]["title"], "测试电影")
            report = Path(result["report"])
            self.assertTrue(report.exists())
            self.assertTrue((report.parent / config_path.name).exists())
            self.assertTrue((report.parent / items_path.name).exists())

    def test_invalid_legacy_data_does_not_partially_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "discover_subscriptions.json"
            items_path = root / "discover_subscription_items.json"
            config_path.write_text(json.dumps({"mode": "torra"}), encoding="utf-8")
            items_path.write_text(json.dumps({"items": [{"title": "missing key"}]}), encoding="utf-8")
            repository = SubscriptionRepository(root / "media_control_center.sqlite3")
            with self.assertRaises(RuntimeError):
                migrate_legacy_subscription_files(repository, config_path, items_path, lambda item: "")
            self.assertFalse(repository.has_config())
            self.assertFalse(repository.has_items())


if __name__ == "__main__":
    unittest.main()
