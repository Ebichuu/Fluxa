from __future__ import annotations

import unittest

from app.quality_watch_subscription_runtime import QualityWatchSubscriptionResolver
from app.torra_subscription_keys import torra_public_subscription_key


def fact(subscription_key="", tmdb_id="202", remote_id="torra-202"):
    return {
        "subscription_key": subscription_key,
        "torra_subscription_id": remote_id,
        "media_type": "tv",
        "tmdb_id": tmdb_id,
        "season_number": 1,
    }


class QualityWatchSubscriptionResolverTests(unittest.TestCase):
    def test_local_subscription_is_preferred_without_title_matching(self):
        resolver = QualityWatchSubscriptionResolver([{
            "key": "tv:202",
            "title": "任意标题",
            "media_type": "tv",
            "tmdb_id": 202,
            "target_season": 1,
            "torra_remote_id": "torra-202",
        }], [{
            "id": "torra-202",
            "name": "另一个标题",
            "media_type": "tv",
            "tmdb_id": 202,
            "season_number": 1,
        }])

        resolved = resolver.resolve(fact("tv:202"))

        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["subscriptionKey"], "tv:202")
        self.assertNotIn("title", fact("tv:202"))

    def test_local_subscription_wins_when_task_uses_torra_public_key(self):
        resolver = QualityWatchSubscriptionResolver([{
            "key": "tv:202",
            "media_type": "tv",
            "tmdb_id": 202,
            "target_season": 1,
            "torra_remote_id": "torra-202",
        }], [{
            "id": "torra-202",
            "media_type": "tv",
            "tmdb_id": 202,
            "season_number": 1,
        }])

        resolved = resolver.resolve(fact(torra_public_subscription_key("torra-202")))

        self.assertEqual((resolved["status"], resolved["subscriptionKey"]), ("resolved", "tv:202"))

    def test_duplicate_local_torra_binding_remains_conflicted(self):
        local = {
            "media_type": "tv",
            "tmdb_id": 202,
            "target_season": 1,
            "torra_remote_id": "torra-202",
        }
        resolver = QualityWatchSubscriptionResolver([
            {**local, "key": "tv:202:a"},
            {**local, "key": "tv:202:b"},
        ], [])

        resolved = resolver.resolve(fact("tv:202:a"))

        self.assertEqual(resolved["reason"], "subscription_identity_conflict")

    def test_torra_only_subscription_uses_stable_key_without_local_write(self):
        remote = {
            "id": "torra-only-202",
            "name": "只读订阅",
            "media_type": "tv",
            "tmdb_id": 202,
            "season_number": 1,
        }
        resolver = QualityWatchSubscriptionResolver([], [remote])

        resolved = resolver.resolve(fact("torra:torra-only-202", remote_id="torra-only-202"))

        expected = torra_public_subscription_key("torra-only-202")
        self.assertEqual((resolved["status"], resolved["subscriptionKey"]), ("resolved", expected))
        self.assertTrue(resolved["subscription"]["read_only"])
        self.assertEqual(remote, {
            "id": "torra-only-202",
            "name": "只读订阅",
            "media_type": "tv",
            "tmdb_id": 202,
            "season_number": 1,
        })

    def test_remote_identity_conflict_is_not_repaired_by_title(self):
        resolver = QualityWatchSubscriptionResolver([], [{
            "id": "torra-202",
            "name": "完全相同标题",
            "media_type": "tv",
            "tmdb_id": 999,
            "season_number": 1,
        }])

        resolved = resolver.resolve(fact())

        self.assertEqual(resolved["status"], "needs_review")
        self.assertEqual(resolved["reason"], "torra_subscription_identity_conflict")

    def test_duplicate_remote_id_remains_conflicted(self):
        row = {
            "id": "torra-202", "media_type": "tv", "tmdb_id": 202, "season_number": 1,
        }
        resolver = QualityWatchSubscriptionResolver([], [row, dict(row)])

        resolved = resolver.resolve(fact())

        self.assertEqual(resolved["reason"], "torra_subscription_identity_conflict")


if __name__ == "__main__":
    unittest.main()
