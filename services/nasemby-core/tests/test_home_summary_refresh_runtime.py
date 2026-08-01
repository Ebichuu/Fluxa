from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask

from app.home_summary_runtime import register_home_summary
from app.home_summary_refresh_runtime import HomeSummaryRefreshRuntime
from app.home_summary_repository import HomeSummaryRepository, MODULE_KEYS


NOW = datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc)


class FakeCollector:
    def __init__(self, gate=None):
        self.calls = 0
        self.gate = gate

    def snapshot_modules(self):
        self.calls += 1
        if self.gate:
            self.gate.wait(timeout=2)
        return {key: {"module": key} for key in MODULE_KEYS}


class BrokenCollector:
    def snapshot_modules(self):
        raise TimeoutError("stop")


class FailingRepository(HomeSummaryRepository):
    def write_success(self, module_key, *args, **kwargs):
        if module_key == "secupload":
            raise RuntimeError("stop")
        return super().write_success(module_key, *args, **kwargs)


class HomeSummaryRefreshRuntimeTests(unittest.TestCase):
    def make_repository(self, repository_type=HomeSummaryRepository):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return repository_type(Path(directory.name) / "summary.sqlite3", clock=lambda: NOW)

    def test_run_writes_global_and_shanghai_date_modules(self):
        repository = self.make_repository()
        collector = FakeCollector()
        result = HomeSummaryRefreshRuntime(repository, collector, clock=lambda: NOW).run_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(collector.calls, 1)
        self.assertIsNotNone(repository.get("task_pipeline", "global"))
        self.assertIsNotNone(repository.get("archive_today", "date:2026-08-02"))
        self.assertFalse(repository.refresh_state()["running"])

    def test_process_lock_prevents_parallel_collection(self):
        repository = self.make_repository()
        gate = threading.Event()
        collector = FakeCollector(gate)
        runtime = HomeSummaryRefreshRuntime(repository, collector, clock=lambda: NOW)
        first_result = []
        thread = threading.Thread(target=lambda: first_result.append(runtime.run_once()))
        thread.start()
        while collector.calls < 1:
            pass
        second = runtime.run_once()
        gate.set()
        thread.join(timeout=2)
        self.assertEqual(second, {"status": "already_running", "ran": False})
        self.assertEqual(first_result[0]["status"], "success")
        self.assertEqual(collector.calls, 1)

    def test_module_write_failure_does_not_discard_other_modules(self):
        repository = self.make_repository(FailingRepository)
        result = HomeSummaryRefreshRuntime(repository, FakeCollector(), clock=lambda: NOW).run_once()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failedModules"], ["secupload"])
        self.assertIsNotNone(repository.get("task_pipeline", "global"))
        self.assertEqual(repository.get("secupload", "global")["confirmation"], "unknown")
        self.assertIsNotNone(repository.get("service_health", "global"))

    def test_empty_cache_get_is_complete_and_does_not_collect_live_state(self):
        repository = self.make_repository()
        app = Flask(__name__)
        service = register_home_summary(app, clock=lambda: NOW, repository=repository)
        service.live_snapshot = lambda: self.fail("GET must not collect live state")
        response = app.test_client().get("/api/v2/home/summary")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["healthState"], "evidence_insufficient")
        self.assertEqual(len(payload["focusItems"]), 6)
        self.assertTrue(all(row["confirmation"] == "unknown" for row in payload["modules"].values()))

    def test_cached_get_reads_current_shanghai_date_only(self):
        repository = self.make_repository()
        repository.write_success(
            "archive_today", "date:2026-08-01", {"archivedToday": 81},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5),
        )
        repository.write_success(
            "archive_today", "date:2026-08-02", {"archivedToday": 6},
            observed_at=NOW, fresh_until=NOW + timedelta(minutes=5),
        )
        app = Flask(__name__)
        register_home_summary(app, clock=lambda: NOW, repository=repository)
        payload = app.test_client().get("/api/v2/home/summary").get_json()
        self.assertEqual(payload["counts"]["archivedToday"], 6)
        self.assertEqual(payload["modules"]["archive_today"]["confirmation"], "confirmed")

    def test_whole_collection_failure_marks_each_module_without_deleting_old_payload(self):
        repository = self.make_repository()
        repository.write_success(
            "task_pipeline", "global", {"headline": "上次可靠结果"},
            observed_at=NOW, fresh_until=NOW,
        )
        result = HomeSummaryRefreshRuntime(repository, BrokenCollector(), clock=lambda: NOW).run_once()
        self.assertEqual(result["status"], "failed")
        task = repository.get("task_pipeline", "global")
        self.assertEqual(task["payload"], {"headline": "上次可靠结果"})
        self.assertEqual(task["confirmation"], "partial")
        self.assertEqual(repository.get("archive_today", "date:2026-08-02")["confirmation"], "unknown")

    def test_expired_cache_is_not_presented_as_currently_confirmed(self):
        repository = self.make_repository()
        repository.write_success(
            "service_health", "global", {"healthState": "normal", "headline": "上次运行正常"},
            observed_at=NOW, fresh_until=NOW,
        )
        app = Flask(__name__)
        register_home_summary(
            app,
            clock=lambda: datetime(2026, 8, 2, 0, 5, tzinfo=timezone.utc),
            repository=repository,
        )
        payload = app.test_client().get("/api/v2/home/summary").get_json()
        self.assertEqual(payload["modules"]["service_health"]["confirmation"], "partial")
        self.assertEqual(payload["healthState"], "evidence_insufficient")


if __name__ == "__main__":
    unittest.main()
