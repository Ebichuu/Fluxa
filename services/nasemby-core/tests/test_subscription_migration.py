from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.private_rss_repository import PrivateRssRepository
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
            report_payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(report_payload["status"], "success")
            self.assertTrue(all(report_payload["checks"].values()))

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

    def test_atomic_migration_preserves_shared_tables_and_does_not_publish_failed_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "media_control_center.sqlite3"
            rss_repository = PrivateRssRepository(database_path)
            rss_source = rss_repository.save_source({
                "name": "保留站点",
                "feedUrl": "https://tracker.example/rss?passkey=must-not-leak",
            })
            config_path = root / "discover_subscriptions.json"
            items_path = root / "discover_subscription_items.json"
            config_path.write_text(json.dumps({"mode": "torra", "token": "must-not-leak"}), encoding="utf-8")
            items_path.write_text(json.dumps({"items": [{
                "subscription_key": "movie:1",
                "title": "测试电影",
                "media_type": "movie",
                "tmdb_id": "1",
                "unknown": {"kept": True},
            }]}), encoding="utf-8")
            repository = SubscriptionRepository(database_path)

            result = migrate_legacy_subscription_files(
                repository, config_path, items_path, lambda item: item.get("subscription_key")
            )

            reopened_rss = PrivateRssRepository(database_path)
            self.assertEqual(reopened_rss.get_source(rss_source["id"])["name"], "保留站点")
            reopened_subscriptions = SubscriptionRepository(database_path)
            self.assertTrue(reopened_subscriptions.load_payload()["items"][0]["unknown"]["kept"])
            report_text = Path(result["report"]).read_text(encoding="utf-8")
            self.assertNotIn("must-not-leak", report_text)
            self.assertEqual(list(root.glob(".*.migration-*.tmp*")), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "media_control_center.sqlite3"
            rss_repository = PrivateRssRepository(database_path)
            rss_source = rss_repository.save_source({
                "name": "失败后仍保留",
                "feedUrl": "https://tracker.example/rss?passkey=still-safe",
            })
            config_path = root / "discover_subscriptions.json"
            items_path = root / "discover_subscription_items.json"
            config_path.write_text(json.dumps({"mode": "torra"}), encoding="utf-8")
            items_path.write_text(json.dumps({"items": [{"subscription_key": "movie:1", "title": "测试"}]}), encoding="utf-8")
            repository = SubscriptionRepository(database_path)
            calls = 0

            def unstable_key(item):
                nonlocal calls
                calls += 1
                return "movie:1" if calls < 3 else "changed-after-import"

            with self.assertRaisesRegex(RuntimeError, "差异检查失败"):
                migrate_legacy_subscription_files(repository, config_path, items_path, unstable_key)
            self.assertFalse(repository.has_config())
            self.assertFalse(repository.has_items())
            self.assertEqual(PrivateRssRepository(database_path).get_source(rss_source["id"])["name"], "失败后仍保留")
            self.assertEqual(list(root.glob(".*.migration-*.tmp*")), [])


if __name__ == "__main__":
    unittest.main()
