from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


NOW = datetime(2026, 7, 14, 8, 5, tzinfo=timezone.utc)


class FakeEmby:
    def __init__(self, recent_at="2026-07-14T07:30:00.000Z", fail_refresh=False):
        self.recent_at = recent_at
        self.fail_refresh = fail_refresh
        self.refreshes = 0

    @staticmethod
    def is_configured():
        return True

    def get_recent_items(self, limit=20):
        return [{"dateCreated": self.recent_at}] if self.recent_at else []

    def trigger_library_refresh(self):
        self.refreshes += 1
        if self.fail_refresh:
            raise RuntimeError("Emby 响应异常：502")


class FakeSymedia:
    def __init__(self, latest="2026-07-14 16:00:00"):
        self.latest = latest

    @staticmethod
    def is_configured():
        return True

    def list_transfer_history(self, count=50, page=1):
        rows = [{"status": True, "date": self.latest}] if self.latest else []
        return {"rows": rows, "total": len(rows)}


class EmbyRefreshRuntimeContractTests(unittest.TestCase):
    def test_timestamp_parser_treats_symedia_naive_time_as_beijing(self):
        from app.emby_refresh_runtime import parse_service_timestamp

        self.assertEqual(
            parse_service_timestamp("2026-07-14 16:00:00"),
            "2026-07-14T08:00:00.000Z",
        )
        self.assertEqual(
            parse_service_timestamp("2026-07-14T07:30:00.000Z"),
            "2026-07-14T07:30:00.000Z",
        )
        self.assertEqual(parse_service_timestamp("not-a-time"), "")

    def test_status_distinguishes_ready_up_to_date_cooldown_and_processed_evidence(self):
        from app.emby_refresh_runtime import evaluate_refresh_status

        base = {
            "configured": True,
            "connected": True,
            "latestSymediaAt": "2026-07-14T08:00:00.000Z",
            "latestEmbyAt": "2026-07-14T07:30:00.000Z",
            "stored": {"lastTriggeredAt": "", "evidenceAt": ""},
            "now": NOW,
        }
        self.assertEqual(evaluate_refresh_status(base)["state"], "ready")
        self.assertEqual(evaluate_refresh_status({
            **base,
            "latestEmbyAt": "2026-07-14T08:01:00.000Z",
        })["state"], "up_to_date")
        self.assertEqual(evaluate_refresh_status({
            **base,
            "stored": {
                "lastTriggeredAt": "2026-07-14T08:00:00.000Z",
                "evidenceAt": "",
            },
        })["state"], "cooldown")
        self.assertEqual(evaluate_refresh_status({
            **base,
            "stored": {
                "lastTriggeredAt": "2026-07-14T07:00:00.000Z",
                "evidenceAt": "2026-07-14T08:00:00.000Z",
            },
        })["state"], "up_to_date")

    def test_trigger_persists_cooldown_and_does_not_repeat_same_evidence(self):
        from app.emby_refresh_runtime import (
            EmbyRefreshError,
            EmbyRefreshService,
            EmbyRefreshStateStore,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "emby-refresh-state.json"
            store = EmbyRefreshStateStore(path)
            emby = FakeEmby()
            service = EmbyRefreshService(
                emby,
                FakeSymedia(),
                store=store,
                activity_writer=lambda *args, **kwargs: None,
            )

            result = service.trigger(NOW)

            self.assertTrue(result["triggered"])
            self.assertEqual(result["triggeredAt"], "2026-07-14T08:05:00.000Z")
            self.assertEqual(emby.refreshes, 1)
            self.assertEqual(EmbyRefreshStateStore(path).read(), {
                "lastTriggeredAt": "2026-07-14T08:05:00.000Z",
                "evidenceAt": "2026-07-14T08:00:00.000Z",
            })
            with self.assertRaises(EmbyRefreshError) as raised:
                service.trigger(NOW + timedelta(minutes=11))
            self.assertEqual(raised.exception.code, "EMBY_REFRESH_NOT_READY")
            self.assertEqual(emby.refreshes, 1)

    def test_invalid_persisted_state_falls_back_without_triggering(self):
        from app.emby_refresh_runtime import EmbyRefreshStateStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(EmbyRefreshStateStore(path).read(), {
                "lastTriggeredAt": "",
                "evidenceAt": "",
            })

    def test_concurrent_trigger_is_rejected_from_evidence_check_start(self):
        from app.emby_refresh_runtime import (
            EmbyRefreshError,
            EmbyRefreshService,
            EmbyRefreshStateStore,
        )

        entered = threading.Event()
        release = threading.Event()

        class SlowSymedia(FakeSymedia):
            def list_transfer_history(self, count=50, page=1):
                entered.set()
                release.wait(timeout=2)
                return super().list_transfer_history(count, page)

        with tempfile.TemporaryDirectory() as directory:
            service = EmbyRefreshService(
                FakeEmby(),
                SlowSymedia(),
                store=EmbyRefreshStateStore(Path(directory) / "state.json"),
                activity_writer=lambda *args, **kwargs: None,
            )
            first_error = []

            def first_trigger():
                try:
                    service.trigger(NOW)
                except Exception as exc:
                    first_error.append(exc)

            worker = threading.Thread(target=first_trigger)
            worker.start()
            self.assertTrue(entered.wait(timeout=1))
            with self.assertRaises(EmbyRefreshError) as raised:
                service.trigger(NOW)
            self.assertEqual(raised.exception.code, "EMBY_REFRESH_IN_PROGRESS")
            release.set()
            worker.join(timeout=2)
            self.assertEqual(first_error, [])

    def test_route_returns_202_and_upstream_failure_returns_502(self):
        from flask import Flask

        from app.emby_refresh_runtime import (
            EmbyRefreshService,
            EmbyRefreshStateStore,
            register_emby_refresh,
        )

        with tempfile.TemporaryDirectory() as directory:
            application = Flask(__name__)
            service = EmbyRefreshService(
                FakeEmby(),
                FakeSymedia(),
                store=EmbyRefreshStateStore(Path(directory) / "state.json"),
                activity_writer=lambda *args, **kwargs: None,
                clock=lambda: NOW,
            )
            register_emby_refresh(application, service=service)
            response = application.test_client().post("/api/media/emby/refresh")
            self.assertEqual(response.status_code, 202)

            failed = Flask(f"{__name__}-failed")
            failed_service = EmbyRefreshService(
                FakeEmby(fail_refresh=True),
                FakeSymedia(),
                store=EmbyRefreshStateStore(Path(directory) / "failed-state.json"),
                activity_writer=lambda *args, **kwargs: None,
                clock=lambda: NOW,
            )
            register_emby_refresh(failed, service=failed_service)
            failed_response = failed.test_client().post("/api/media/emby/refresh")
            self.assertEqual(failed_response.status_code, 502)
            self.assertEqual(failed_response.get_json()["code"], "EMBY_REFRESH_FAILED")


if __name__ == "__main__":
    unittest.main()
