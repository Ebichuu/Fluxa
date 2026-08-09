from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import discover_runtime
from app.discover_candidate_runtime import DiscoverCandidateError, DiscoverCandidateService
from app.subscription_repository import SubscriptionRepository


class DiscoverCandidateRuntimeTests(unittest.TestCase):
    NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)

    def test_rss_media_metadata_reads_existing_cache_without_remote_fetch(self):
        discover_runtime.set_discover_item_cache({
            "title": "缓存作品中文名",
            "media_type": "tv",
            "tmdb_id": "908070",
            "first_air_date": "2026-04-03",
            "poster_url": "https://image.tmdb.org/t/p/w342/cached.jpg",
        }, "test")

        with patch.object(
            discover_runtime,
            "http_json",
            side_effect=AssertionError("RSS 卡片读取不得访问 TMDB"),
        ):
            result = discover_runtime.read_cached_rss_media_metadata({("tv", "908070", 2)})

        self.assertEqual(result[("tv", "908070", 2)]["mediaTitle"], "缓存作品中文名")
        self.assertEqual(result[("tv", "908070", 2)]["mediaYear"], "2026")
        self.assertEqual(
            result[("tv", "908070", 2)]["posterUrl"],
            "https://image.tmdb.org/t/p/w342/cached.jpg",
        )

    def add_candidate(self, repository, *, tmdb_id="200", media_type="tv", season=1, **payload):
        source = {
            "title": payload.pop("title", "日播候选"),
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "target_season": season,
            "source_key": "daily_airing",
            **payload,
        }
        repository.upsert_discover_candidates(
            [source],
            observed_at="2026-07-28T07:00:00Z",
            expires_at="2026-08-28T07:00:00Z",
        )
        return next(
            row["candidate_id"]
            for row in repository.list_discover_candidates(state="")["items"]
            if row["media_type"] == media_type and row["tmdb_id"] == tmdb_id
        )

    def service(self, repository, *, subscriptions=None, environment=None, save_callback=None, activity_writer=None):
        return DiscoverCandidateService(
            repository,
            environment if environment is not None else {
                "NASEMBY_CORE_WRITE_ENABLED": "true",
                "TORRA_PUSH_ENABLED": "true",
            },
            subscription_loader=lambda: {"items": list(subscriptions or [])},
            config_loader=lambda: {"mode": "torra"},
            save_callback=save_callback,
            activity_writer=activity_writer or (lambda *_args, **_kwargs: None),
            clock=lambda: self.NOW,
        )

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

    def test_candidate_list_is_public_expiry_aware_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            self.add_candidate(
                repository,
                poster_url="https://image.tmdb.org/t/p/w500/poster.jpg?token=private",
                overview="剧情 token=private https://tracker.invalid/detail?passkey=private",
                source_label="全球日播",
                internal_id="private-row-id",
                download_url="https://tracker.invalid/download?passkey=private",
            )
            repository.upsert_discover_candidates(
                [{
                    "title": "已过期候选",
                    "media_type": "movie",
                    "tmdb_id": "300",
                    "source_key": "movie_hot",
                }],
                observed_at="2026-07-01T00:00:00Z",
                expires_at="2026-07-02T00:00:00Z",
            )

            payload = self.service(repository).list(limit=20)
            serialized = str(payload)

            self.assertEqual(payload["page"]["total"], 1)
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(payload["items"][0]["posterUrl"], "https://image.tmdb.org/t/p/w500/poster.jpg")
            self.assertEqual(payload["items"][0]["overview"], "剧情 token=***")
            self.assertNotIn("https://", payload["items"][0]["overview"])
            self.assertNotIn("payload_json", serialized)
            self.assertNotIn("private-row-id", serialized)
            self.assertNotIn("passkey", serialized)
            self.assertNotIn("download_url", serialized)

    def test_candidate_list_rejects_malformed_image_port(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            self.add_candidate(repository, poster_url="https://image.tmdb.org:not-a-port/poster.jpg")

            payload = self.service(repository).list()

            self.assertEqual(payload["items"][0]["posterUrl"], "")

    def test_preview_is_read_only_and_reports_missing_tv_season(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            candidate_id = self.add_candidate(repository, season=0)
            saves = []
            activities = []
            before = repository.get_discover_candidate(candidate_id)
            service = self.service(
                repository,
                save_callback=lambda payload: saves.append(payload),
                activity_writer=lambda *args, **kwargs: activities.append((args, kwargs)),
            )

            preview = service.preview(candidate_id)

            self.assertFalse(preview["ready"])
            self.assertIn("候选缺少明确季号", preview["blockers"])
            self.assertEqual(repository.get_discover_candidate(candidate_id), before)
            self.assertEqual(saves, [])
            self.assertEqual(activities, [])

    def test_saved_only_capability_remains_ready_and_can_follow(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            candidate_id = self.add_candidate(repository)
            saves = []

            def save(payload):
                saves.append(payload)
                return {
                    "saved_item": {
                        **payload["item"],
                        "subscription_key": "tv:200:season:1",
                    },
                    "replaced": False,
                    "subscription_task": {
                        "mode": "torra",
                        "enabled": False,
                        "queued": 0,
                        "reason": "允许向 Torra 创建订阅已关闭",
                    },
                }

            service = self.service(
                repository,
                environment={
                    "NASEMBY_CORE_WRITE_ENABLED": "true",
                    "TORRA_PUSH_ENABLED": "false",
                },
                save_callback=save,
            )

            preview = service.preview(candidate_id)
            followed = service.follow(candidate_id, {
                "confirm": True,
                "idempotencyKey": "saved-only-key-123",
            })

            self.assertTrue(preview["ready"])
            self.assertEqual(preview["manualFollow"]["state"], "saved_only")
            self.assertEqual(preview["blockers"], [])
            self.assertEqual(followed["activation"]["state"], "saved_only")
            self.assertEqual(len(saves), 1)

    def test_follow_requires_confirmation_valid_key_and_write_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            candidate_id = self.add_candidate(repository)
            cases = [
                ({"idempotencyKey": "candidate-key-123"}, "DISCOVER_CANDIDATE_CONFIRM_REQUIRED"),
                ({"confirm": True, "idempotencyKey": "short"}, "DISCOVER_CANDIDATE_IDEMPOTENCY_INVALID"),
                ({"confirm": True, "idempotencyKey": "candidate-key-123", "extra": True}, "DISCOVER_CANDIDATE_FOLLOW_FIELDS_INVALID"),
            ]
            for body, code in cases:
                with self.subTest(code=code), self.assertRaises(DiscoverCandidateError) as caught:
                    self.service(repository).follow(candidate_id, body)
                self.assertEqual(caught.exception.code, code)

            with self.assertRaises(DiscoverCandidateError) as caught:
                self.service(repository, environment={}).follow(candidate_id, {
                    "confirm": True,
                    "idempotencyKey": "candidate-key-123",
                })
            self.assertEqual(caught.exception.code, "NASEMBY_CORE_WRITE_DISABLED")

    def test_confirmed_follow_is_manual_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            candidate_id = self.add_candidate(repository)
            saves = []
            activities = []

            def save(payload):
                saves.append(payload)
                return {
                    "saved_item": {
                        **payload["item"],
                        "subscription_key": "tv:200:season:1",
                    },
                    "replaced": False,
                    "subscription_task": {
                        "mode": "torra",
                        "enabled": True,
                        "queued": 1,
                        "task_label": "Torra 追更",
                    },
                }

            service = self.service(
                repository,
                save_callback=save,
                activity_writer=lambda *args, **kwargs: activities.append((args, kwargs)),
            )
            body = {"confirm": True, "idempotencyKey": "candidate-key-123"}

            first = service.follow(candidate_id, body)
            replay = service.follow(candidate_id, body)

            self.assertFalse(first["replayed"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(first["activation"]["state"], "saved_and_queued")
            self.assertEqual(len(saves), 1)
            self.assertEqual(len(activities), 1)
            self.assertEqual(saves[0]["item"]["origin"], "manual")
            self.assertEqual(saves[0]["item"]["intent_origin"], "manual")
            self.assertEqual(saves[0]["item"]["source_label"], "手动订阅")
            self.assertEqual(repository.get_discover_candidate(candidate_id)["state"], "followed")
            self.assertEqual(service.list()["page"]["total"], 0)

    def test_follow_rejects_duplicate_and_idempotency_conflicts_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SubscriptionRepository(Path(directory) / "subscriptions.sqlite3")
            first_id = self.add_candidate(repository, tmdb_id="200")
            second_id = self.add_candidate(repository, tmdb_id="201")
            saves = []

            def save(payload):
                saves.append(payload)
                return {"saved_item": {**payload["item"], "subscription_key": "tv:200:season:1"}}

            duplicate_service = self.service(repository, subscriptions=[{
                "media_type": "tv",
                "tmdb_id": "200",
                "target_season": 1,
            }], save_callback=save)
            with self.assertRaises(DiscoverCandidateError) as caught:
                duplicate_service.follow(first_id, {"confirm": True, "idempotencyKey": "duplicate-key-123"})
            self.assertEqual(caught.exception.code, "DISCOVER_CANDIDATE_NOT_READY")
            self.assertEqual(saves, [])

            service = self.service(repository, save_callback=save)
            service.follow(first_id, {"confirm": True, "idempotencyKey": "shared-key-12345"})
            with self.assertRaises(DiscoverCandidateError) as caught:
                service.follow(second_id, {"confirm": True, "idempotencyKey": "shared-key-12345"})
            self.assertEqual(caught.exception.code, "DISCOVER_CANDIDATE_IDEMPOTENCY_CONFLICT")
            with self.assertRaises(DiscoverCandidateError) as caught:
                service.follow(first_id, {"confirm": True, "idempotencyKey": "different-key-123"})
            self.assertEqual(caught.exception.code, "DISCOVER_CANDIDATE_STALE")
            self.assertEqual(len(saves), 1)


if __name__ == "__main__":
    unittest.main()
