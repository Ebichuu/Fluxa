from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import discover_runtime
from app.subscription_repository import SubscriptionRepository


class DiscoverCandidateRuntimeTests(unittest.TestCase):
    def test_daily_airing_refresh_only_writes_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            repository.upsert_item({
                "subscription_key": "tv:manual",
                "title": "人工追更",
                "media_type": "tv",
                "tmdb_id": "100",
                "target_season": 1,
                "origin": "manual",
            }, "tv:manual")
            source = {
                "title": "日播候选",
                "media_type": "tv",
                "tmdb_id": "200",
                "target_season": 1,
            }
            with patch.object(discover_runtime, "subscription_repository", return_value=repository), patch.object(
                discover_runtime, "fetch_daily_airing_subscription_source", return_value=[source]
            ), patch.object(
                discover_runtime, "normalize_subscription_item_metadata", side_effect=lambda row, **_kwargs: dict(row)
            ), patch.object(
                discover_runtime, "merge_cached_discover_item", side_effect=lambda row: dict(row)
            ), patch.object(
                discover_runtime, "load_subscription_config", return_value={"douban": {"exclude_titles": []}}
            ), patch.object(
                discover_runtime, "write_activity"
            ), patch.object(
                discover_runtime, "write_subscription_items_data", side_effect=AssertionError("日播刷新不得改写追更")
            ), patch.object(
                discover_runtime, "queue_subscription_resource_rule_transfer", side_effect=AssertionError("日播刷新不得触发 provider")
            ):
                result = discover_runtime.sync_daily_airing_subscriptions()

            self.assertEqual([item["title"] for item in repository.load_payload()["items"]], ["人工追更"])
            self.assertEqual(repository.list_discover_candidates()["total"], 1)
            self.assertEqual(result["added_count"], 1)
            self.assertNotIn("subscription_task", result)


if __name__ == "__main__":
    unittest.main()
