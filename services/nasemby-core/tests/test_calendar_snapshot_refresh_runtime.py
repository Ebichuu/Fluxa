from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.calendar_snapshot_refresh_runtime import CalendarSnapshotRefreshRuntime
from app.calendar_snapshot_repository import CalendarSnapshotRepository


NOW = datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)


class CalendarSnapshotRefreshRuntimeTests(unittest.TestCase):
    def make_repository(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CalendarSnapshotRepository(Path(directory.name) / "calendar.sqlite3", clock=lambda: NOW)

    def test_default_scope_uses_shanghai_month(self):
        repository = self.make_repository()
        runtime = CalendarSnapshotRefreshRuntime(repository, lambda *_args, **_kwargs: {}, clock=lambda: NOW)
        value = runtime.request_default_scope()
        self.assertEqual(value["scopeKey"], "2026-08:all:0")

    def test_run_builds_outside_repository_and_persists_result(self):
        repository = self.make_repository()
        repository.request_refresh(2026, 8, "tv", False, now=NOW, idempotency_key="run")
        calls = []
        runtime = CalendarSnapshotRefreshRuntime(
            repository,
            lambda year, month, media_type, **kwargs: calls.append((year, month, media_type, kwargs)) or {
                "ok": True, "confirmation": "confirmed", "calendar": {"entries": []}
            },
            clock=lambda: NOW,
        )
        result = runtime.run_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, [(2026, 8, "tv", {"include_unlinked": False})])
        self.assertEqual(repository.get(2026, 8, "tv")["payload"]["calendar"]["entries"], [])

    def test_process_lock_prevents_parallel_builds(self):
        repository = self.make_repository()
        repository.request_refresh(2026, 8, "all", False, now=NOW, idempotency_key="parallel")
        gate = threading.Event()
        started = threading.Event()

        def builder(*_args, **_kwargs):
            started.set()
            gate.wait(timeout=2)
            return {"ok": True, "calendar": {"entries": []}}

        runtime = CalendarSnapshotRefreshRuntime(repository, builder, clock=lambda: NOW)
        first = []
        thread = threading.Thread(target=lambda: first.append(runtime.run_once()))
        thread.start()
        started.wait(timeout=2)
        self.assertEqual(runtime.run_once(), {"status": "already_running", "ran": False})
        gate.set()
        thread.join(timeout=2)
        self.assertEqual(first[0]["status"], "success")

    def test_failure_is_recorded_without_raising(self):
        repository = self.make_repository()
        repository.request_refresh(2026, 8, "all", False, now=NOW, idempotency_key="failed")
        runtime = CalendarSnapshotRefreshRuntime(
            repository, lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("secret")), clock=lambda: NOW
        )
        result = runtime.run_once()
        self.assertEqual(result["status"], "failed")
        queue = repository.queue_state(2026, 8)
        self.assertEqual(queue["status"], "failed")
        self.assertEqual(queue["lastErrorText"], "日历刷新暂时失败")


if __name__ == "__main__":
    unittest.main()
