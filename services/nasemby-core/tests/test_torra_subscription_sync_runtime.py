from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

from app.contract_mapping import map_subscription_item
from app.http_runtime import configure_http_runtime
from app.subscription_compat_runtime import _push_preview
from app.subscription_repository import SubscriptionRepository
from app.torra_subscription_sync_runtime import (
    TorraSubscriptionSyncService,
    normalize_torra_subscription,
    register_torra_subscription_sync,
)
from tests.activity_log_test_support import IsolatedActivityLogMixin


def item_key(item):
    return str(item.get("subscription_key") or "")


class FakeTorraClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0

    def list_subscriptions(self):
        self.calls += 1
        return [dict(row) for row in self.rows]


class TorraSubscriptionSyncRuntimeTests(IsolatedActivityLogMixin, unittest.TestCase):
    def service(self, repository, client, environment=None):
        return TorraSubscriptionSyncService(
            environment or {"MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED": "true"},
            repository,
            client,
            repository.load_payload,
            item_key,
            clock=lambda: datetime(2026, 7, 20, 2, 0, tzinfo=timezone.utc),
        )

    def test_normalize_torra_subscription_keeps_only_safe_status(self):
        normalized = normalize_torra_subscription({
            "id": "remote-1",
            "name": "测试剧集",
            "media_type": "series",
            "tmdb_id": 202,
            "season_number": 2,
            "enabled": True,
            "completed": False,
            "token": "must-not-persist",
        })

        self.assertEqual(normalized["mapping_status"], "mapped")
        self.assertEqual(normalized["item"]["target_season"], 2)
        self.assertNotIn("token", normalized["item"])
        self.assertNotIn("token", normalized["remote_status"])

    def test_public_mirror_payloads_do_not_expose_remote_id(self):
        item = normalize_torra_subscription({
            "id": "remote-private-id",
            "name": "只读电影",
            "media_type": "movie",
            "tmdb_id": 303,
        })["item"]

        mapped = map_subscription_item(item)
        preview = _push_preview(item, {}, FakeTorraClient([]))

        self.assertNotIn("torraRemoteId", mapped)
        self.assertEqual(preview["duplicate"]["subscriptionId"], "")

    def test_preview_import_replay_and_missing_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            client = FakeTorraClient([
                {"id": "remote-movie", "name": "测试电影", "media_type": "movie", "tmdb_id": 101},
                {"id": "remote-tv", "name": "测试剧集", "media_type": "tv", "tmdb_id": 202, "season_number": 1},
            ])
            service = self.service(repository, client)

            preview = service.preview()
            first, error = service.import_all({
                "confirm": True,
                "idempotencyKey": "torra-import-0001",
            })
            replay, replay_error = service.import_all({
                "confirm": True,
                "idempotencyKey": "torra-import-0001",
            })
            client.rows = [client.rows[0]]
            synced = service.sync_existing()

            self.assertEqual(preview["summary"]["new"], 2)
            self.assertIsNone(error)
            self.assertEqual(first["summary"]["imported"], 2)
            self.assertIsNone(replay_error)
            self.assertTrue(replay["replayed"])
            self.assertEqual(len(repository.load_payload()["items"]), 2)
            self.assertEqual(synced["summary"]["remoteMissing"], 1)

    def test_disabled_import_does_not_access_torra(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            client = FakeTorraClient([])
            service = self.service(
                repository,
                client,
                {"MCC_TORRA_SUBSCRIPTION_SYNC_ENABLED": "false"},
            )

            response, error = service.import_all({
                "confirm": True,
                "idempotencyKey": "torra-import-0002",
            })

            self.assertIsNone(response)
            self.assertEqual(error[0], "TORRA_SUBSCRIPTION_SYNC_DISABLED")
            self.assertEqual(client.calls, 0)

    def test_two_remote_rows_matching_one_local_item_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")
            repository.upsert_item({
                "subscription_key": "tv:202:1",
                "title": "重复剧集",
                "media_type": "tv",
                "tmdb_id": "202",
                "target_season": 1,
            }, "tv:202:1")
            client = FakeTorraClient([
                {"id": "remote-1", "name": "重复剧集", "media_type": "tv", "tmdb_id": 202, "season_number": 1},
                {"id": "remote-2", "name": "重复剧集", "media_type": "tv", "tmdb_id": 202, "season_number": 1},
            ])
            service = self.service(repository, client)

            preview = service.preview()
            response, error = service.import_all({
                "confirm": True,
                "idempotencyKey": "torra-import-0003",
            })

            self.assertEqual(preview["summary"]["conflicts"], 1)
            self.assertNotIn("remoteIds", preview["conflictItems"][0])
            self.assertEqual(len(preview["conflictItems"][0]["remoteRefs"]), 2)
            self.assertNotIn("remote-1", preview["conflictItems"][0]["remoteRefs"])
            self.assertIsNone(response)
            self.assertEqual(error[0], "TORRA_SYNC_IDENTITY_CONFLICT")
            self.assertEqual(repository.list_torra_links(), [])

    def test_routes_return_stable_upstream_and_conflict_errors(self):
        class FailingClient:
            def __init__(self, error):
                self.error = error

            def list_subscriptions(self):
                raise RuntimeError(self.error)

        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "media_control_center.sqlite3")

            def response_for(client, path, method="GET"):
                app = Flask(__name__)
                configure_http_runtime(app)
                register_torra_subscription_sync(app, self.service(repository, client))
                return app.test_client().open(path, method=method, json={} if method == "POST" else None)

            unconfigured = response_for(
                FailingClient("未配置 Torra 地址或认证信息"),
                "/api/v2/torra/subscription-sync/preview",
            )
            auth_failed = response_for(
                FailingClient("Torra Token 无效或已过期"),
                "/api/v2/torra/subscription-sync/preview",
            )

            repository.upsert_item({
                "subscription_key": "tv:202:1",
                "title": "重复剧集",
                "media_type": "tv",
                "tmdb_id": "202",
                "target_season": 1,
            }, "tv:202:1")
            conflict = response_for(FakeTorraClient([
                {"id": "remote-1", "name": "重复剧集", "media_type": "tv", "tmdb_id": 202, "season_number": 1},
                {"id": "remote-2", "name": "重复剧集", "media_type": "tv", "tmdb_id": 202, "season_number": 1},
            ]), "/api/v2/torra/subscription-sync/runs", method="POST")

            self.assertEqual(unconfigured.status_code, 503)
            self.assertEqual(unconfigured.get_json()["code"], "TORRA_NOT_CONFIGURED")
            self.assertTrue(unconfigured.get_json()["request_id"])
            self.assertEqual(auth_failed.status_code, 502)
            self.assertEqual(auth_failed.get_json()["code"], "TORRA_AUTH_FAILED")
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.get_json()["code"], "TORRA_SYNC_IDENTITY_CONFLICT")


if __name__ == "__main__":
    unittest.main()
