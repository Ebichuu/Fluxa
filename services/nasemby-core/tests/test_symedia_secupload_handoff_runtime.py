from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.symedia_secupload_handoff_runtime import (
    SymediaSecuploadHandoffRepository,
    SymediaSecuploadHandoffService,
)


class FakeTorra:
    def __init__(self):
        self.jobs = []
        self.details = {}
        self.routes = [{
            "itemId": "category-variety",
            "name": "综艺",
            "sourcePath": "/downloads/06-variety",
            "destPath": "/115/00-待整理/06-综艺",
        }]
        self.list_calls = 0

    def list_jobs(self, kind_prefix, *, limit, offset):
        self.list_calls += 1
        self.last_query = (kind_prefix, limit, offset)
        return list(self.jobs)

    def get_job_snapshot(self, job_id):
        return self.details[job_id]

    def get_secupload_config_routes(self):
        return list(self.routes)


class FakeSymedia:
    def __init__(self):
        self.tasks = [{
            "id": "transfer-variety",
            "name": "06-综艺",
            "source_dir": "/CloudNAS/CloudDrive/115/00-待整理/06-综艺",
        }]
        self.history = []
        self.submissions = []

    def list_transfer_tasks(self):
        return list(self.tasks)

    def list_transfer_history(self, count, page):
        return {"rows": list(self.history), "total": len(self.history)}

    def manual_transfer_file(self, file_path, transfer_task_id):
        self.submissions.append((file_path, transfer_task_id))
        return {"success": True, "message": "任务已添加"}


def observer_job(job_id, created_at, status="success"):
    return {"id": job_id, "status": status, "created_at": created_at}


def observer_detail(job_id, file_path, status="success"):
    return {
        "id": job_id,
        "status": status,
        "payload": {"config_item_id": "category-variety"},
        "result": {"file_path": file_path, "message": "实时处理完成"},
    }


class SymediaSecuploadHandoffRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc)]
        self.repository = SymediaSecuploadHandoffRepository(
            Path(self.directory.name) / "handoff.sqlite3",
            clock=lambda: self.now[0],
        )
        self.torra = FakeTorra()
        self.symedia = FakeSymedia()
        self.activities = []
        self.environment = {
            "NASEMBY_CORE_WRITE_ENABLED": "true",
            "MCC_SYMEDIA_SECUPLOAD_HANDOFF_ENABLED": "true",
            "MCC_SYMEDIA_SECUPLOAD_SETTLE_SECONDS": "0",
            "MCC_SYMEDIA_SECUPLOAD_RETRY_SECONDS": "60",
        }
        self.service = SymediaSecuploadHandoffService(
            self.repository,
            self.torra,
            self.symedia,
            environment=self.environment,
            clock=lambda: self.now[0],
            activity_writer=lambda *args: self.activities.append(args),
        )

    def test_first_enabled_run_creates_watermark_and_ignores_old_jobs(self):
        old = "2026-08-09T00:30:00.000Z"
        self.torra.jobs = [observer_job("old-job", old)]

        result = self.service.run_once()

        self.assertEqual(result["status"], "bootstrapped")
        self.assertEqual(self.repository.active_items(), [])
        self.assertEqual(self.symedia.submissions, [])
        self.assertEqual(self.repository.state()["cursor_created_at"], "2026-08-09T01:00:00.000Z")

    def test_naive_torra_beijing_timestamp_before_watermark_stays_ignored(self):
        self.torra.jobs = [observer_job("old-local-job", "2026-08-09 08:59:00")]
        self.service.run_once()

        repeated = self.service.run_once()

        self.assertEqual(repeated["discovered"], 0)
        self.assertEqual(self.repository.active_items(), [])
        self.assertEqual(self.symedia.submissions, [])

    def test_naive_torra_beijing_timestamp_after_watermark_is_discovered(self):
        self.service.run_once()
        self.now[0] += timedelta(minutes=1)
        file_path = "/downloads/06-variety/Local.Time.S01E01.mkv"
        self.torra.jobs = [observer_job("new-local-job", "2026-08-09 09:01:00")]
        self.torra.details["new-local-job"] = observer_detail("new-local-job", file_path)

        result = self.service.run_once()

        self.assertEqual(result["discovered"], 1)
        self.assertEqual(result["submitted"], 1)

    def test_job_at_the_exact_cursor_timestamp_is_not_lost(self):
        self.service.run_once()
        created = "2026-08-09T01:00:00.000Z"
        file_path = "/downloads/06-variety/Exact.Cursor.S01E01.mkv"
        self.torra.jobs = [observer_job("exact-cursor-job", created)]
        self.torra.details["exact-cursor-job"] = observer_detail("exact-cursor-job", file_path)

        first = self.service.run_once()
        repeated = self.service.run_once()

        self.assertEqual(first["discovered"], 1)
        self.assertEqual(repeated["discovered"], 0)
        self.assertEqual(len(self.symedia.submissions), 1)

    def test_new_success_job_submits_one_exact_file_and_confirms_history(self):
        self.service.run_once()
        self.now[0] += timedelta(minutes=1)
        created = "2026-08-09T01:01:00.000Z"
        file_path = "/downloads/06-variety/Show.Name.S01E11.2026.1080p.WEB-DL.mkv"
        target_path = "/CloudNAS/CloudDrive/115/00-待整理/06-综艺/Show.Name.S01E11.2026.1080p.WEB-DL.mkv"
        self.torra.jobs = [observer_job("new-job", created)]
        self.torra.details["new-job"] = observer_detail("new-job", file_path)

        submitted = self.service.run_once()

        self.assertEqual(submitted["discovered"], 1)
        self.assertEqual(submitted["submitted"], 1)
        self.assertEqual(self.symedia.submissions, [(target_path, "transfer-variety")])
        self.assertEqual(self.repository.item("new-job")["job_status"], "submitted")

        self.symedia.history = [{"src": target_path, "status": True, "date": "2026-08-09 09:02:00"}]
        self.now[0] += timedelta(seconds=30)
        confirmed = self.service.run_once()

        self.assertEqual(confirmed["confirmed"], 1)
        self.assertEqual(self.repository.item("new-job")["job_status"], "completed")
        self.service.run_once()
        self.assertEqual(len(self.symedia.submissions), 1)

    def test_failed_torra_job_is_ignored_without_symedia_write(self):
        self.service.run_once()
        self.now[0] += timedelta(minutes=1)
        self.torra.jobs = [observer_job("failed-job", "2026-08-09T01:01:00.000Z", "failed")]
        self.torra.details["failed-job"] = {"id": "failed-job", "status": "failed", "error": "upload failed"}

        result = self.service.run_once()

        self.assertEqual(result["submitted"], 0)
        self.assertEqual(self.repository.item("failed-job")["job_status"], "ignored")
        self.assertEqual(self.symedia.submissions, [])

    def test_old_same_path_history_cannot_confirm_a_new_job(self):
        self.service.run_once()
        self.now[0] += timedelta(minutes=1)
        created = "2026-08-09T01:01:00.000Z"
        file_path = "/downloads/06-variety/Repeated.Name.S01E01.mkv"
        target_path = "/CloudNAS/CloudDrive/115/00-待整理/06-综艺/Repeated.Name.S01E01.mkv"
        self.torra.jobs = [observer_job("repeated-job", created)]
        self.torra.details["repeated-job"] = observer_detail("repeated-job", file_path)
        self.symedia.history = [{
            "src": target_path,
            "status": True,
            "date": "2026-08-09 08:50:00",
        }]

        result = self.service.run_once()

        self.assertEqual(result["confirmed"], 0)
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(self.repository.item("repeated-job")["job_status"], "submitted")

    def test_retry_ignores_the_previous_missing_file_history(self):
        self.service.run_once()
        self.now[0] += timedelta(minutes=1)
        file_path = "/downloads/06-variety/Retry.Name.S01E02.mkv"
        target_path = "/CloudNAS/CloudDrive/115/00-待整理/06-综艺/Retry.Name.S01E02.mkv"
        self.torra.jobs = [observer_job("retry-job", "2026-08-09T01:01:00.000Z")]
        self.torra.details["retry-job"] = observer_detail("retry-job", file_path)
        self.service.run_once()

        self.now[0] += timedelta(minutes=1)
        self.symedia.history = [{
            "src": target_path,
            "status": False,
            "errmsg": "源文件不存在",
            "date": "2026-08-09 09:02:00",
        }]
        retry = self.service.run_once()
        self.assertEqual(retry["submitted"], 0)
        self.assertEqual(self.repository.item("retry-job")["job_status"], "pending")

        self.now[0] += timedelta(seconds=61)
        resubmitted = self.service.run_once()
        self.assertEqual(resubmitted["submitted"], 1)
        self.assertEqual(len(self.symedia.submissions), 2)

        self.now[0] += timedelta(seconds=30)
        self.symedia.history.insert(0, {
            "src": target_path,
            "status": True,
            "date": "2026-08-09 09:03:30",
        })
        confirmed = self.service.run_once()
        self.assertEqual(confirmed["confirmed"], 1)
        self.assertEqual(self.repository.item("retry-job")["job_status"], "completed")

    def test_out_of_source_path_fails_closed(self):
        self.service.run_once()
        self.now[0] += timedelta(minutes=1)
        self.torra.jobs = [observer_job("escaped-job", "2026-08-09T01:01:00.000Z")]
        self.torra.details["escaped-job"] = observer_detail(
            "escaped-job", "/downloads/other/Unexpected.S01E01.mkv"
        )

        result = self.service.run_once()

        item = self.repository.item("escaped-job")
        self.assertEqual(result["submitted"], 0)
        self.assertEqual(item["job_status"], "failed")
        self.assertIn("越出", item["last_error"])
        self.assertEqual(self.symedia.submissions, [])

    def test_both_write_gates_are_required_and_status_hides_paths(self):
        self.environment["NASEMBY_CORE_WRITE_ENABLED"] = "false"
        self.assertEqual(self.service.run_once()["status"], "disabled")
        self.assertEqual(self.torra.list_calls, 0)

        self.environment["NASEMBY_CORE_WRITE_ENABLED"] = "true"
        self.repository.bootstrap("2026-08-09T00:00:00.000Z")
        self.repository.add_job("public-job", "2026-08-09T00:30:00.000Z")
        self.repository.update_item(
            "public-job", job_status="failed", display_name="Safe.Show.S01E01.mkv",
            target_path="/CloudNAS/CloudDrive/115/private/Safe.Show.S01E01.mkv",
            last_error="归档失败",
        )

        snapshot = self.service.snapshot()
        serialized = str(snapshot)
        self.assertIn("Safe.Show.S01E01.mkv", serialized)
        self.assertNotIn("public-job", serialized)
        self.assertNotIn("/CloudNAS/CloudDrive", serialized)


if __name__ == "__main__":
    unittest.main()
