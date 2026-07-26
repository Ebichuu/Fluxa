from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from app.quality_watch_repository import QualityWatchRepository
from app.secupload_issue_runtime import (
    SecuploadIssueError,
    SecuploadIssueService,
    build_secupload_issue,
    register_secupload_issue,
)


NOW = datetime(2026, 7, 26, 4, 0, tzinfo=timezone.utc)
BEIJING = timezone(timedelta(hours=8))
RAW_CATEGORY_ID = "plugin_item_private_category"


def summary(*, next_run_at="2026-07-26T18:00:00+08:00", schedule_enabled=True,
            allow_schedule=True, allow_manual=True, plugin_enabled=True,
            active=False, failed=1):
    latest_status = "running" if active else "success"
    runs = [
        {
            "runId": "run-latest",
            "taskKey": "retry_pending",
            "targetItemId": RAW_CATEGORY_ID,
            "trigger": "schedule",
            "status": latest_status,
            "message": "任务完成",
            "counts": {"success": 0, "failed": failed},
            "startedAt": "2026-07-26T12:00:00+08:00",
            "finishedAt": "2026-07-26T12:00:03+08:00",
            "createdAt": "2026-07-26T12:00:00+08:00",
        },
        {
            "runId": "run-middle",
            "taskKey": "retry_pending",
            "targetItemId": RAW_CATEGORY_ID,
            "trigger": "schedule",
            "status": "success",
            "message": "任务完成",
            "counts": {"success": 6, "failed": 1},
            "startedAt": "2026-07-26T06:00:00+08:00",
            "finishedAt": "2026-07-26T06:00:09+08:00",
            "createdAt": "2026-07-26T06:00:00+08:00",
        },
        {
            "runId": "run-oldest",
            "taskKey": "retry_pending",
            "targetItemId": RAW_CATEGORY_ID,
            "trigger": "schedule",
            "status": "success",
            "message": "任务完成",
            "counts": {"success": 4, "failed": 3},
            "startedAt": "2026-07-26T00:00:00+08:00",
            "finishedAt": "2026-07-26T00:00:06+08:00",
            "createdAt": "2026-07-26T00:00:00+08:00",
        },
    ]
    return {
        "configured": True,
        "connected": True,
        "pluginKey": "secupload_115",
        "pluginEnabled": plugin_enabled,
        "readable": True,
        "perFileEvidence": False,
        "configItems": [{
            "itemId": RAW_CATEGORY_ID,
            "name": "00-日漫",
            "enabled": True,
            "fallbackUploadAfterFailures": 3,
            "notifyAfterFailures": 3,
            "sourcePath": "/private/source",
            "tempPath": "/private/temp",
        }],
        "tasks": [{
            "key": "retry_pending",
            "name": "重试临时目录",
            "allowSchedule": allow_schedule,
            "allowManualRun": allow_manual,
        }],
        "schedules": [{
            "taskKey": "retry_pending",
            "targetItemId": RAW_CATEGORY_ID,
            "enabled": schedule_enabled,
            "nextRunAt": next_run_at,
            "lastRunAt": "2026-07-26T12:00:00+08:00",
        }],
        "recentRuns": runs,
        "recentBatches": [{
            "batchKey": "private-batch-key",
            "taskKey": "retry_pending",
            "trigger": "schedule",
            "status": "running" if active else "failed",
            "runCount": 1,
            "targetItemIds": [RAW_CATEGORY_ID],
            "counts": {"success": 0, "failed": failed},
            "startedAt": "2026-07-26T12:00:00+08:00",
            "finishedAt": "2026-07-26T12:00:03+08:00",
        }],
        "activeRuns": 1 if active else 0,
        "latestBatch": {
            "batchKey": "private-batch-key",
            "taskKey": "retry_pending",
            "trigger": "schedule",
            "status": "running" if active else "failed",
            "runCount": 1,
            "targetItemIds": [RAW_CATEGORY_ID],
            "counts": {"success": 0, "failed": failed},
            "startedAt": "2026-07-26T12:00:00+08:00",
            "finishedAt": "2026-07-26T12:00:03+08:00",
        },
        "lastRunAt": "2026-07-26T12:00:03+08:00",
        "nextRunAt": next_run_at,
        "lastCheckedAt": "2026-07-26T12:00:04+08:00",
        "error": "",
    }


class FakeTorra:
    def __init__(self, snapshots, run_id="run-manual", error=None):
        self.snapshots = list(snapshots)
        self.run_id = run_id
        self.error = error
        self.reads = 0
        self.runs = []

    def get_secupload_summary(self):
        value = self.snapshots[min(self.reads, len(self.snapshots) - 1)]
        self.reads += 1
        return value

    def run_secupload_retry(self, target_item_id, previous_run_ids=None):
        self.runs.append((target_item_id, set(previous_run_ids or [])))
        if self.error:
            raise self.error
        return {"runId": self.run_id}


class SecuploadIssueRuntimeTests(unittest.TestCase):
    def test_scheduled_retry_builds_safe_category_summary(self):
        issue = build_secupload_issue(summary(), now=NOW)

        self.assertEqual(issue["state"], "recovering")
        self.assertEqual(issue["stateReason"], "scheduled_retry")
        self.assertEqual(issue["failedTotal"], 1)
        self.assertEqual(issue["nextRunAt"], "2026-07-26T18:00:00+08:00")
        self.assertEqual(issue["categories"][0]["label"], "00-日漫")
        self.assertEqual(issue["categories"][0]["recentFailedCounts"], [3, 1, 1])
        self.assertEqual(issue["categories"][0]["retryPolicyText"], "失败 3 次后转原始上传")
        serialized = str(issue)
        for private_value in (RAW_CATEGORY_ID, "private-batch-key", "/private/source", "/private/temp"):
            self.assertNotIn(private_value, serialized)

    def test_schedule_time_boundaries_use_absolute_time(self):
        for seconds, expected in ((-599, "recovering"), (-600, "recovering"), (-601, "action_required"), (86400, "recovering"), (86401, "action_required")):
            with self.subTest(seconds=seconds):
                next_run = (NOW + timedelta(seconds=seconds)).astimezone(BEIJING).isoformat()
                issue = build_secupload_issue(summary(next_run_at=next_run), now=NOW)
                self.assertEqual(issue["state"], expected)

    def test_active_run_recovers_without_schedule(self):
        issue = build_secupload_issue(
            summary(next_run_at="", schedule_enabled=False, active=True),
            now=NOW,
        )
        self.assertEqual((issue["state"], issue["stateReason"]), ("recovering", "active_run"))

    def test_missing_or_invalid_evidence_is_unknown(self):
        for patch in (
            {"readable": False, "error": "private upstream detail"},
            {"latestBatch": None},
            {"latestBatch": {"counts": {"success": 0, "failed": None}}},
        ):
            with self.subTest(patch=patch):
                value = summary()
                value.update(patch)
                issue = build_secupload_issue(value, now=NOW)
                self.assertEqual(issue["state"], "unknown")
                self.assertNotIn("private upstream detail", str(issue))

    def test_disabled_recovery_capabilities_require_action(self):
        cases = (
            summary(plugin_enabled=False),
            summary(allow_schedule=False),
            summary(schedule_enabled=False),
            summary(next_run_at=""),
        )
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(build_secupload_issue(value, now=NOW)["state"], "action_required")

    def test_zero_failures_are_normal(self):
        issue = build_secupload_issue(summary(failed=0), now=NOW)
        self.assertEqual((issue["state"], issue["failedTotal"]), ("normal", 0))

    def build_service(self, client, *, write_enabled=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = QualityWatchRepository(
            Path(temporary.name) / "media.sqlite3",
            clock=lambda: NOW,
        )
        activities = []
        service = SecuploadIssueService(
            client,
            repository,
            environment={"NASEMBY_CORE_WRITE_ENABLED": "true" if write_enabled else "false"},
            clock=lambda: NOW,
            activity_writer=lambda *args, **kwargs: activities.append((args, kwargs)),
        )
        return service, repository, activities

    def test_preview_blocks_manual_retry_while_automatic_recovery_is_valid(self):
        client = FakeTorra([summary()])
        service, _, _ = self.build_service(client)

        preview = service.preview({})

        self.assertFalse(preview["allowed"])
        self.assertEqual(preview["reasonCode"], "SECUPLOAD_AUTOMATIC_RETRY_ACTIVE")
        self.assertEqual(client.runs, [])

    def test_retry_is_idempotent_and_competing_action_is_locked(self):
        blocked = summary(next_run_at="", schedule_enabled=False)
        client = FakeTorra([blocked])
        service, _, _ = self.build_service(client)
        category_id = service.snapshot()["categories"][0]["id"]

        first, first_status = service.retry({
            "confirm": True,
            "categoryId": category_id,
            "idempotencyKey": "secupload-key-0001",
        })
        replay, replay_status = service.retry({
            "confirm": True,
            "categoryId": category_id,
            "idempotencyKey": "secupload-key-0001",
        })

        self.assertEqual((first_status, replay_status), (202, 202))
        self.assertEqual(first["action"]["id"], replay["action"]["id"])
        self.assertEqual(len(client.runs), 1)
        with self.assertRaises(SecuploadIssueError) as raised:
            service.retry({
                "confirm": True,
                "categoryId": category_id,
                "idempotencyKey": "secupload-key-0002",
            })
        self.assertEqual((raised.exception.code, raised.exception.status), ("SECUPLOAD_RETRY_BUSY", 409))

    def test_upstream_failure_is_persisted_and_releases_lease(self):
        blocked = summary(next_run_at="", schedule_enabled=False)
        client = FakeTorra([blocked], error=RuntimeError("http://private/path token=secret"))
        service, repository, activities = self.build_service(client)
        category_id = service.snapshot()["categories"][0]["id"]

        with self.assertRaises(SecuploadIssueError) as raised:
            service.retry({
                "confirm": True,
                "categoryId": category_id,
                "idempotencyKey": "secupload-key-0003",
            })

        self.assertEqual((raised.exception.code, raised.exception.status), ("SECUPLOAD_RETRY_FAILED", 502))
        action = repository.get_action_by_idempotency("secupload-key-0003")
        self.assertEqual(action["status"], "failed")
        self.assertEqual(action["lease_until"], "")
        self.assertNotIn("private/path", str(action))
        self.assertTrue(activities)

    def test_action_poll_tracks_run_and_refreshes_issue(self):
        blocked = summary(next_run_at="", schedule_enabled=False)
        running = summary(next_run_at="", schedule_enabled=False, active=True)
        running["recentRuns"][0].update({
            "runId": "run-manual", "trigger": "manual", "status": "running",
        })
        running["latestBatch"]["trigger"] = "manual"
        finished = summary(failed=0)
        finished["recentRuns"][0].update({
            "runId": "run-manual", "trigger": "manual", "status": "success",
            "counts": {"success": 1, "failed": 0},
        })
        finished["latestBatch"]["trigger"] = "manual"
        client = FakeTorra([blocked, blocked, running, finished])
        service, _, _ = self.build_service(client)
        category_id = service.snapshot()["categories"][0]["id"]
        submitted, _ = service.retry({
            "confirm": True,
            "categoryId": category_id,
            "idempotencyKey": "secupload-key-0004",
        })

        polling = service.action(submitted["action"]["id"])
        completed = service.action(submitted["action"]["id"])

        self.assertEqual(polling["action"]["status"], "polling")
        self.assertEqual(completed["action"]["status"], "succeeded")
        self.assertEqual(completed["issue"]["state"], "normal")

    def test_routes_use_v2_semantics_and_validate_body(self):
        blocked = summary(next_run_at="", schedule_enabled=False)
        client = FakeTorra([blocked])
        service, _, _ = self.build_service(client)
        app = Flask(__name__)
        register_secupload_issue(app, service)
        http = app.test_client()

        issue = http.get("/api/v2/system-issues/secupload-failures")
        invalid = http.post(
            "/api/v2/system-issues/secupload-failures/retry-previews",
            json={"unsupported": True},
        )

        self.assertEqual(issue.status_code, 200)
        self.assertEqual(issue.get_json()["id"], "secupload_failures")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.get_json()["code"], "SECUPLOAD_PREVIEW_FIELDS_INVALID")


if __name__ == "__main__":
    unittest.main()
