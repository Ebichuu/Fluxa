from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app import services as provider_services
from app.main import create_app
from app.moviepilot_backup_runtime import (
    MOVIEPILOT_BACKUP_ACTION_TYPE,
    MoviePilotBackupDependencies,
    MoviePilotBackupService,
)
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository


class FakeTorra:
    def __init__(self):
        self.configured = True
        self.calls = 0
        self.rows = [{"id": "torra-private-202", "is_running": False, "is_mutating": False}]
        self.error = None

    def is_configured(self):
        return self.configured

    def list_subscriptions(self):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.rows)


class FakeQb:
    def __init__(self):
        self.calls = 0
        self.connected = True
        self.tasks = []
        self.error = None

    def summary(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {"connected": self.connected, "tasks": list(self.tasks)}


class FakeMoviePilot:
    def __init__(self):
        self.exists = True
        self.inspect_calls = 0
        self.search_calls = 0
        self.create_calls = 0
        self.inspect_error = None
        self.search_error = None
        self.create_error = None

    def inspect(self, target):
        self.inspect_calls += 1
        if self.inspect_error:
            raise self.inspect_error
        return {
            "exists": self.exists,
            "subscribe_id": "moviepilot-private-777",
            "url": "https://moviepilot.private.example",
            "token": "moviepilot-private-token",
            "raw": {"secret": "private-response"},
        }

    def search(self, target, inspection):
        self.search_calls += 1
        if self.search_error:
            raise self.search_error
        return {
            "ok": True,
            "search_triggered": True,
            "subscribe_id": inspection.get("subscribe_id"),
            "moviepilot_response": {"token": "moviepilot-private-token"},
        }

    def create(self, target):
        self.create_calls += 1
        if self.create_error:
            raise self.create_error
        return {
            "ok": True,
            "already_exists": False,
            "search_triggered": True,
            "subscribe_id": "moviepilot-created-778",
            "moviepilot_response": {"url": "https://moviepilot.private.example"},
        }


class MoviePilotBackupRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)]
        database = Path(self.directory.name) / "media_control_center.sqlite3"
        self.repository = QualityWatchRepository(database, clock=lambda: self.now[0])
        self.rss = PrivateRssRepository(database)
        self.environment = {"MCC_MOVIEPILOT_BACKUP_ENABLED": "true"}
        self.subscriptions = [{
            "key": "tv:202",
            "title": "测试剧",
            "media_type": "tv",
            "tmdb_id": "202",
            "target_season": 1,
        }]
        unit = self.repository.ensure_watch_unit(
            "tv:202",
            "tv",
            1,
            1,
            torra_subscription_id="torra-private-202",
        )
        self.unit = self.repository.update_watch_unit(
            unit["unit_key"],
            unit["version"],
            state="observation_expired",
        )
        self.torra = FakeTorra()
        self.qb = FakeQb()
        self.moviepilot = FakeMoviePilot()
        self.service = self._service()
        self.client = self._client()

    def _service(self, environment=None):
        return MoviePilotBackupService(MoviePilotBackupDependencies(
            environment if environment is not None else self.environment,
            self.repository,
            lambda: {"items": self.subscriptions},
            self.torra,
            self.qb,
            self.moviepilot.inspect,
            self.moviepilot.search,
            self.moviepilot.create,
        ))

    def _client(self, environment=None, service=None):
        values = environment if environment is not None else self.environment
        return create_app(
            access_environment=values,
            private_rss_repository=self.rss,
            quality_watch_repository=self.repository,
            moviepilot_backup_service=service or self.service,
        ).test_client()

    def _push(self, key="moviepilot-manual-0001"):
        return self.client.post(
            "/api/v2/subscriptions/tv:202/moviepilot-pushes",
            json={"confirm": True, "idempotencyKey": key},
        )

    def test_disabled_gate_returns_503_without_any_provider_call(self):
        self.environment["MCC_MOVIEPILOT_BACKUP_ENABLED"] = "false"
        preview = self.client.post(
            "/api/v2/subscriptions/tv:202/moviepilot-previews",
            json={},
        )
        pushed = self._push()

        self.assertEqual((preview.status_code, pushed.status_code), (503, 503))
        self.assertEqual(preview.get_json()["code"], "MOVIEPILOT_BACKUP_DISABLED")
        self.assertEqual(
            (self.torra.calls, self.qb.calls, self.moviepilot.inspect_calls, self.moviepilot.search_calls),
            (0, 0, 0, 0),
        )

    def test_request_and_subscription_validation_use_v2_errors(self):
        unknown = self.client.post(
            "/api/v2/subscriptions/tv:202/moviepilot-previews",
            json={"token": "not-allowed"},
        )
        missing = self.client.post(
            "/api/v2/subscriptions/tv:missing/moviepilot-previews",
            json={},
        )
        self.subscriptions[0].pop("tmdb_id")
        invalid = self.client.post(
            "/api/v2/subscriptions/tv:202/moviepilot-previews",
            json={},
        )

        self.assertEqual((unknown.status_code, missing.status_code, invalid.status_code), (422, 404, 422))
        for response in (unknown, missing, invalid):
            self.assertEqual(set(response.get_json()), {"code", "error", "request_id"})
        self.assertEqual(self.moviepilot.inspect_calls, 0)

    def test_local_torra_and_qb_business_blockers_are_safe_previews(self):
        active = self.repository.update_watch_unit(
            self.unit["unit_key"],
            self.unit["version"],
            state="observing_upgrade",
        )
        local = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertEqual(local.status_code, 200)
        self.assertFalse(local.get_json()["ready"])
        self.assertEqual((self.torra.calls, self.qb.calls), (0, 0))

        self.unit = self.repository.update_watch_unit(
            active["unit_key"],
            active["version"],
            state="observation_expired",
        )
        self.torra.rows = []
        missing_mapping = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertEqual(missing_mapping.status_code, 200)
        self.assertIn("Torra 订阅映射不存在", missing_mapping.get_json()["blockers"])

        self.torra.rows = [{"id": "torra-private-202", "is_running": True}]
        busy = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertIn("Torra 正在处理该订阅", busy.get_json()["blockers"])

        self.torra.rows = [{"id": "torra-private-202", "is_running": False}]
        self.qb.tasks = [{"name": "测试剧 S01E01", "status": "downloading"}]
        downloading = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertIn("该订阅已有活动下载", downloading.get_json()["blockers"])
        blocked_push = self._push()
        self.assertEqual(blocked_push.status_code, 409)
        self.assertEqual(blocked_push.get_json()["code"], "MOVIEPILOT_BACKUP_BLOCKED")
        self.assertEqual(self.moviepilot.inspect_calls, 0)

    def test_provider_unavailable_is_502_and_does_not_fall_through(self):
        self.torra.configured = False
        torra = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertEqual(torra.status_code, 502)
        self.assertEqual(torra.get_json()["code"], "MOVIEPILOT_TORRA_UNAVAILABLE")
        self.assertEqual((self.qb.calls, self.moviepilot.inspect_calls), (0, 0))

        self.torra.configured = True
        self.qb.connected = False
        qb = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertEqual(qb.status_code, 502)
        self.assertEqual(qb.get_json()["code"], "MOVIEPILOT_QB_UNAVAILABLE")
        self.assertEqual(self.moviepilot.inspect_calls, 0)

    def test_existing_and_create_paths_are_idempotent_audited_and_redacted(self):
        preview = self.client.post("/api/v2/subscriptions/tv:202/moviepilot-previews", json={})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["mode"], "search-existing")
        self.assertNotIn("moviepilot-private", preview.get_data(as_text=True))

        existing = self._push("moviepilot-existing-0001")
        replay = self._push("moviepilot-existing-0001")
        self.assertEqual((existing.status_code, replay.status_code), (200, 200))
        self.assertEqual(existing.get_json(), replay.get_json())
        self.assertEqual((self.moviepilot.search_calls, self.moviepilot.create_calls), (1, 0))
        serialized = existing.get_data(as_text=True)
        for secret in ("moviepilot-private", "private-response", "https://"):
            self.assertNotIn(secret, serialized)

        action = self.repository.get_action(existing.get_json()["actionId"])
        self.assertEqual((action["provider"], action["action_type"]), ("moviepilot", MOVIEPILOT_BACKUP_ACTION_TYPE))
        action_blob = str(action)
        for secret in ("moviepilot-private", "private-response", "https://"):
            self.assertNotIn(secret, action_blob)

        self.now[0] += timedelta(seconds=61)
        self.moviepilot.exists = False
        created = self._push("moviepilot-created-0002")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.get_json()["mode"], "create-and-search")
        self.assertFalse(created.get_json()["alreadyExists"])
        self.assertEqual((self.moviepilot.search_calls, self.moviepilot.create_calls), (1, 1))

        upstream = Mock()
        upstream.raise_for_status.return_value = None
        upstream.json.return_value = {
            "success": True,
            "id": 888,
            "message": "token=moviepilot-private-token https://moviepilot.private.example",
        }
        with (
            patch.object(provider_services, "_moviepilot_config", return_value={
                "url": "https://moviepilot.private.example",
                "token": "moviepilot-private-token",
                "username": "NasEmby",
                "auto_subscribe": False,
            }),
            patch.object(provider_services, "_moviepilot_find_subscribe", return_value=None),
            patch.object(provider_services.requests, "post", return_value=upstream),
            patch.object(provider_services.requests, "get", return_value=upstream),
            patch.object(provider_services, "write_activity") as activity,
        ):
            provider_services.moviepilot_backup_create({
                "title": "测试剧",
                "media_type": "tv",
                "tmdb_id": 202,
                "seasons": [1],
                "year": "2026",
            })
            provider_services.moviepilot_backup_search_existing(
                {"title": "测试剧"},
                {"subscribe_id": 999},
            )
        activity_blob = str(activity.call_args_list)
        for secret in ("888", "999", "moviepilot-private-token", "https://"):
            self.assertNotIn(secret, activity_blob)

        upstream.json.return_value = {"success": True, "message": "created"}
        with (
            patch.object(provider_services, "_moviepilot_config", return_value={
                "url": "https://moviepilot.private.example",
                "token": "moviepilot-private-token",
                "username": "NasEmby",
                "auto_subscribe": False,
            }),
            patch.object(
                provider_services,
                "_moviepilot_find_subscribe",
                side_effect=RuntimeError("token=moviepilot-private-token https://moviepilot.private.example"),
            ),
            patch.object(provider_services.requests, "post", return_value=upstream),
            patch.object(provider_services, "write_activity") as failed_lookup_activity,
        ):
            with self.assertRaises(RuntimeError):
                provider_services.moviepilot_backup_create({
                    "title": "测试剧",
                    "media_type": "tv",
                    "tmdb_id": 202,
                    "seasons": [1],
                })
        failed_lookup_blob = str(failed_lookup_activity.call_args_list)
        self.assertNotIn("moviepilot-private-token", failed_lookup_blob)
        self.assertNotIn("https://", failed_lookup_blob)

    def test_cooldown_conflict_in_progress_and_upstream_failure_are_terminal(self):
        first = self._push("moviepilot-first-0001")
        self.assertEqual(first.status_code, 200)
        cooldown = self._push("moviepilot-second-0002")
        self.assertEqual(cooldown.status_code, 409)
        self.assertEqual(cooldown.get_json()["code"], "MOVIEPILOT_COOLDOWN")

        conflict_claim = self.repository.claim_action(
            "moviepilot-conflict-0003",
            "tv:other",
            "moviepilot",
            MOVIEPILOT_BACKUP_ACTION_TYPE,
        )
        conflict = self._push("moviepilot-conflict-0003")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["code"], "MOVIEPILOT_IDEMPOTENCY_CONFLICT")
        self.repository.complete_action(conflict_claim["action"]["action_id"], "cancelled")

        inflight = self.repository.claim_action(
            "moviepilot-progress-0004",
            "tv:202",
            "moviepilot",
            MOVIEPILOT_BACKUP_ACTION_TYPE,
        )
        progress = self._push("moviepilot-progress-0004")
        self.assertEqual(progress.status_code, 409)
        self.assertEqual(progress.get_json()["code"], "MOVIEPILOT_IN_PROGRESS")
        self.repository.complete_action(inflight["action"]["action_id"], "cancelled")

        self.now[0] += timedelta(seconds=61)
        self.moviepilot.search_error = RuntimeError(
            "token=moviepilot-private-token url=https://moviepilot.private.example"
        )
        failed = self._push("moviepilot-failed-0005")
        failed_replay = self._push("moviepilot-failed-0005")
        self.assertEqual((failed.status_code, failed_replay.status_code), (502, 502))
        self.assertEqual(failed.get_json()["code"], "MOVIEPILOT_PUSH_FAILED")
        self.assertEqual(set(failed.get_json()), {"code", "error", "request_id"})
        self.assertNotIn("moviepilot-private", failed.get_data(as_text=True))
        stored = self.repository.get_action_by_idempotency("moviepilot-failed-0005")
        self.assertEqual(stored["status"], "failed")
        self.assertNotIn("moviepilot-private", str(stored))

    def test_routes_require_session_and_reject_wrong_origin(self):
        environment = {
            **self.environment,
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
        }
        service = self._service(environment)
        protected = self._client(environment, service)
        path = "/api/v2/subscriptions/tv:202/moviepilot-previews"
        self.assertEqual(protected.post(path, json={}).status_code, 401)
        login = protected.post("/auth/login", data={"access_key": environment["MCC_ACCESS_KEY"]})
        self.assertEqual(login.status_code, 303)
        denied = protected.post(path, json={}, headers={"Origin": "https://evil.example"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()["code"], "ORIGIN_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
