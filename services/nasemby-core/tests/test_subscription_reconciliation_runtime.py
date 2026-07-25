from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.subscription_reconciliation_runtime import (
    SubscriptionReconciliationService,
    resolve_torra_subscription_key,
    torra_public_subscription_key,
)
from app.subscription_repository import SubscriptionRepository


def item_key(item):
    return str(item.get("subscription_key") or "")


class FakeTorraClient:
    def __init__(self, rows, *, configured=True):
        self.rows = rows
        self.configured = configured

    def is_configured(self):
        return self.configured

    def list_subscriptions(self):
        return [dict(row) for row in self.rows]


class FailingTorraClient(FakeTorraClient):
    def list_subscriptions(self):
        raise RuntimeError("Torra connection failed https://torra.invalid/private?token=secret")


class SubscriptionReconciliationRuntimeTests(unittest.TestCase):
    def service(self, repository, client):
        return SubscriptionReconciliationService(
            repository,
            client,
            repository.load_payload,
            item_key,
            clock=lambda: datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc),
        )

    def test_matches_by_tmdb_identity_without_mutating_either_side(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            repository.upsert_item({
                "subscription_key": "tv:202:1",
                "title": "测试剧",
                "media_type": "tv",
                "tmdb_id": "202",
                "target_season": 1,
            }, "tv:202:1")
            service = self.service(repository, FakeTorraClient([{
                "id": "remote-1",
                "name": "测试剧",
                "media_type": "tv",
                "tmdb_id": 202,
                "season_number": 1,
            }]))

            result = service.snapshot()

            self.assertEqual(result["summary"]["reconciliation"]["linked"], 1)
            self.assertEqual(result["items"][0]["healthState"], "normal")
            self.assertEqual(repository.list_torra_links(), [])

    def test_title_only_candidate_is_conflict_not_linked(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            repository.upsert_item({
                "subscription_key": "tv:local:1",
                "title": "同名剧",
                "media_type": "tv",
                "tmdb_id": "101",
                "target_season": 1,
            }, "tv:local:1")
            service = self.service(repository, FakeTorraClient([{
                "id": "remote-2",
                "name": "同名剧",
                "media_type": "tv",
                "tmdb_id": 999,
                "season_number": 1,
            }]))

            result = service.snapshot()

            self.assertEqual(result["summary"]["reconciliation"]["conflict"], 1)
            self.assertEqual(result["items"][0]["healthState"], "action_required")

    def test_mapped_torra_only_subscription_is_normal_following_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            service = self.service(repository, FakeTorraClient([{
                "id": "remote-only-mapped",
                "name": "Torra 已有剧集",
                "media_type": "tv",
                "tmdb_id": 505,
                "season_number": 2,
            }]))

            result = service.snapshot()

            self.assertEqual(result["summary"]["reconciliation"]["only_torra"], 1)
            self.assertEqual(result["summary"]["health"]["normal"], 1)
            self.assertEqual(result["summary"]["health"]["waiting"], 0)
            self.assertEqual(result["items"][0]["fulfillmentState"], "following")
            self.assertEqual(result["items"][0]["healthState"], "normal")
            self.assertEqual(result["items"][0]["reasonCode"], "")
            self.assertEqual(
                result["items"][0]["id"],
                torra_public_subscription_key("remote-only-mapped"),
            )
            self.assertNotIn("remote-only-mapped", str(result["items"][0]))

    def test_public_torra_key_resolves_legacy_missing_and_collision_without_exposing_remote_id(self):
        remote_id = "remote-private-id"
        public_key = torra_public_subscription_key(remote_id)
        rows = [{"id": remote_id, "name": "测试剧"}]

        by_public = resolve_torra_subscription_key(public_key, rows)
        by_legacy = resolve_torra_subscription_key(f"torra:{remote_id}", rows)

        self.assertEqual(by_public["canonicalKey"], f"torra:{remote_id}")
        self.assertEqual(by_public["publicKey"], public_key)
        self.assertEqual(by_legacy["publicKey"], public_key)
        self.assertEqual(resolve_torra_subscription_key("torra:missing", rows), {"status": "missing"})

        collision_id = public_key.removeprefix("torra:")
        collision = resolve_torra_subscription_key(
            public_key,
            [*rows, {"id": collision_id, "name": "冲突剧"}],
        )
        self.assertEqual(collision, {"status": "conflict"})

    def test_unmapped_torra_only_subscription_remains_insufficient(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            service = self.service(repository, FakeTorraClient([{
                "id": "remote-only-unmapped",
                "name": "缺少身份的 Torra 剧集",
                "media_type": "tv",
                "season_number": 1,
            }]))

            result = service.snapshot()

            self.assertEqual(result["summary"]["reconciliation"]["only_torra"], 1)
            self.assertEqual(result["items"][0]["healthState"], "evidence_insufficient")
            self.assertEqual(result["items"][0]["reasonCode"], "TORRA_IDENTITY_INCOMPLETE")

    def test_remote_disappearance_keeps_local_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            repository.upsert_item({
                "subscription_key": "movie:303",
                "title": "消失电影",
                "media_type": "movie",
                "tmdb_id": "303",
                "torra_remote_id": "remote-gone",
            }, "movie:303")
            repository.save_torra_link({
                "subscription_key": "movie:303",
                "remote_id": "remote-gone",
                "origin": "torra_import",
                "mapping_status": "mapped",
                "sync_state": "current",
            })

            result = self.service(repository, FakeTorraClient([])).snapshot()

            self.assertEqual(result["summary"]["reconciliation"]["remote_missing"], 1)
            self.assertEqual(len(repository.load_payload()["items"]), 1)
            self.assertEqual(result["items"][0]["reasonCode"], "TORRA_REMOTE_MISSING")

    def test_unconfigured_torra_reports_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            repository.upsert_item({
                "subscription_key": "movie:404",
                "title": "本地电影",
                "media_type": "movie",
                "tmdb_id": "404",
                "torra_remote_id": "remote-404",
            }, "movie:404")
            repository.save_torra_link({
                "subscription_key": "movie:404",
                "remote_id": "remote-404",
                "origin": "torra_import",
                "mapping_status": "mapped",
                "sync_state": "current",
            })

            result = self.service(repository, FakeTorraClient([], configured=False)).snapshot()

            self.assertFalse(result["configured"])
            self.assertEqual(result["sourceError"], "Torra 尚未配置")
            self.assertEqual(result["summary"]["reconciliation"]["remote_missing"], 0)
            self.assertEqual(result["items"][0]["reconciliationState"], "linked")
            self.assertEqual(result["items"][0]["healthState"], "evidence_insufficient")
            self.assertEqual(result["items"][0]["reasonCode"], "TORRA_NOT_CONFIGURED")

    def test_torra_read_failure_reports_insufficient_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            repository.upsert_item({
                "subscription_key": "movie:606",
                "title": "读取失败时保留的本地电影",
                "media_type": "movie",
                "tmdb_id": "606",
                "torra_remote_id": "remote-606",
            }, "movie:606")
            repository.save_torra_link({
                "subscription_key": "movie:606",
                "remote_id": "remote-606",
                "origin": "torra_import",
                "mapping_status": "mapped",
                "sync_state": "current",
            })

            result = self.service(repository, FailingTorraClient([])).snapshot()

            self.assertEqual(result["sourceError"], "Torra 订阅读取失败")
            self.assertEqual(result["summary"]["reconciliation"]["remote_missing"], 0)
            self.assertEqual(result["items"][0]["reconciliationState"], "linked")
            self.assertEqual(result["items"][0]["healthState"], "evidence_insufficient")
            self.assertEqual(result["items"][0]["reasonCode"], "TORRA_READ_FAILED")
            self.assertNotIn("torra.invalid", str(result))
            self.assertNotIn("secret", str(result))


if __name__ == "__main__":
    unittest.main()
