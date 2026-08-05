from __future__ import annotations

import unittest
from unittest import mock

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
        self.assertEqual(resolved["canonicalKey"], "torra:torra-202")
        self.assertEqual(resolved["publicKey"], torra_public_subscription_key("torra-202"))
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

        expected = "torra:torra-only-202"
        self.assertEqual((resolved["status"], resolved["subscriptionKey"]), ("resolved", expected))
        self.assertEqual(resolved["publicKey"], torra_public_subscription_key("torra-only-202"))
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

    def test_public_key_collision_is_rejected(self):
        first = {
            "id": "remote-a", "media_type": "tv", "tmdb_id": 202, "season_number": 1,
        }
        second = {
            "id": "remote-b", "media_type": "tv", "tmdb_id": 303, "season_number": 1,
        }
        with mock.patch(
            "app.quality_watch_subscription_runtime.torra_public_subscription_key",
            return_value="torra:0123456789",
        ):
            resolver = QualityWatchSubscriptionResolver([], [first, second])
            resolved = resolver.resolve(fact("torra:0123456789", remote_id="remote-a"))

        self.assertEqual(resolved["reason"], "torra_subscription_key_conflict")

    def test_subscription_map_merges_local_and_torra_only_with_canonical_keys(self):
        resolver = QualityWatchSubscriptionResolver([{
            "key": "tv:202", "media_type": "tv", "tmdb_id": 202,
            "target_season": 1, "torra_remote_id": "remote-local",
        }], [{
            "id": "remote-local", "media_type": "tv", "tmdb_id": 202, "season_number": 1,
        }, {
            "id": "remote-only", "media_type": "tv", "tmdb_id": 303, "season_number": 2,
        }])

        subscriptions = resolver.subscription_map()

        self.assertEqual(set(subscriptions), {"tv:202", "torra:remote-only"})
        self.assertTrue(subscriptions["torra:remote-only"]["read_only"])

    def test_read_only_torra_mirror_never_becomes_internal_public_key(self):
        remote_id = "remote-mirror"
        public_key = torra_public_subscription_key(remote_id)
        resolver = QualityWatchSubscriptionResolver([{
            "key": public_key,
            "subscription_key": public_key,
            "media_type": "tv",
            "tmdb_id": 404,
            "target_season": 2,
            "torra_remote_id": remote_id,
            "origin": "torra",
            "read_only": True,
        }], [{
            "id": remote_id,
            "media_type": "tv",
            "tmdb_id": 404,
            "season_number": 2,
        }])

        resolved = resolver.resolve(fact(
            public_key,
            tmdb_id="404",
            remote_id=remote_id,
        ) | {"season_number": 2})
        subscriptions = resolver.subscription_map()

        self.assertEqual(resolved["subscriptionKey"], f"torra:{remote_id}")
        self.assertEqual(set(subscriptions), {f"torra:{remote_id}"})


if __name__ == "__main__":
    unittest.main()
