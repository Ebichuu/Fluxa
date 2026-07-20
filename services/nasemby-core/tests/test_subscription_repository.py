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

    def test_torra_mirror_is_idempotent_and_marks_remote_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            candidates = [{
                "subscription_key": "torra:remote-1",
                "remote_id": "remote-1",
                "origin": "torra_import",
                "mapping_status": "mapped",
                "remote_status": {"enabled": True},
                "remote_fingerprint": "fingerprint-1",
                "item": {
                    "subscription_key": "torra:remote-1",
                    "title": "镜像电影",
                    "media_type": "movie",
                    "tmdb_id": "100",
                    "origin": "torra",
                    "read_only": True,
                },
            }]
            first = repository.apply_torra_mirror(candidates, item_key)
            second = repository.apply_torra_mirror(candidates, item_key)
            missing = repository.apply_torra_mirror([], item_key, import_new=False)

            self.assertEqual(first["imported"], 1)
            self.assertEqual(second["updated"], 1)
            self.assertEqual(len(repository.load_payload()["items"]), 1)
            self.assertEqual(missing["remoteMissing"], 1)
            self.assertEqual(repository.get_torra_link(remote_id="remote-1")["sync_state"], "remote_missing")

    def test_torra_remote_id_cannot_link_two_local_subscriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            repository.save_torra_link({"subscription_key": "movie:1", "remote_id": "remote-1"})
            with self.assertRaises(ValueError):
                repository.save_torra_link({"subscription_key": "movie:2", "remote_id": "remote-1"})

    def test_torra_sync_run_replays_saved_response(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            repository.record_torra_sync_run("idempotency-0001", {"ok": True, "imported": 3})
            repository.record_torra_sync_run("idempotency-0001", {"ok": False})
            self.assertEqual(
                repository.get_torra_sync_run("idempotency-0001"),
                {"ok": True, "imported": 3},
            )

    def test_torra_mirror_and_idempotency_response_share_one_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            candidates = [{
                "subscription_key": "torra:remote-atomic",
                "remote_id": "remote-atomic",
                "item": {
                    "subscription_key": "torra:remote-atomic",
                    "title": "原子导入",
                    "media_type": "movie",
                    "origin": "torra",
                    "read_only": True,
                },
            }]

            with self.assertRaises(RuntimeError):
                repository.apply_torra_mirror_once(
                    candidates,
                    item_key,
                    "idempotency-atomic-failure",
                    lambda _result: (_ for _ in ()).throw(RuntimeError("response failed")),
                )

            self.assertEqual(repository.load_payload()["items"], [])
            self.assertIsNone(repository.get_torra_sync_run("idempotency-atomic-failure"))

            first, replayed = repository.apply_torra_mirror_once(
                candidates,
                item_key,
                "idempotency-atomic-success",
                lambda result: {"ok": True, "summary": result},
            )
            second, second_replayed = repository.apply_torra_mirror_once(
                candidates,
                item_key,
                "idempotency-atomic-success",
                lambda _result: (_ for _ in ()).throw(AssertionError("replay must not rebuild")),
            )

            self.assertFalse(replayed)
            self.assertTrue(second_replayed)
            self.assertEqual(first, second)
            self.assertEqual(len(repository.load_payload()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
