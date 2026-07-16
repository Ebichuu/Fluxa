from __future__ import annotations

import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


HASH_A = "a" * 40
HASH_B = "b" * 40


def task(hash_value, state, status):
    return {"hash": hash_value, "state": state, "status": status}


def summary(tasks, *, configured=True, connected=True, error=""):
    return {
        "configured": configured,
        "connected": connected,
        "webUrl": "http://qb.example.test",
        "lastCheckedAt": "",
        "version": "test",
        "transfer": {"downloadSpeed": 0, "uploadSpeed": 0},
        "counts": {
            "total": len(tasks),
            "active": 0,
            "downloading": 0,
            "stalled": 0,
            "completed": 0,
            "paused": 0,
        },
        "tasks": tasks,
        **({"error": error} if error else {}),
    }


class FakeActionClient:
    def __init__(self, summaries):
        self.summaries = list(summaries)
        self.actions = []

    def summary(self):
        return self.summaries.pop(0)

    def set_paused(self, action, hashes):
        self.actions.append((action, list(hashes)))


class QbittorrentActionRuntimeContractTests(unittest.TestCase):
    def test_hash_validation_deduplicates_and_rejects_invalid_or_excessive_inputs(self):
        from app.qbittorrent_action_runtime import (
            QbittorrentActionError,
            validate_action_hashes,
        )

        self.assertEqual(validate_action_hashes([HASH_A.upper(), HASH_A]), [HASH_A])
        for value, code in [
            ([], "QB_HASHES_REQUIRED"),
            (["not-a-hash"], "QB_HASH_INVALID"),
            ([f"{index:040x}" for index in range(21)], "QB_HASH_LIMIT_EXCEEDED"),
        ]:
            with self.subTest(code=code), self.assertRaises(QbittorrentActionError) as raised:
                validate_action_hashes(value)
            self.assertEqual(raised.exception.code, code)

    def test_pause_submits_only_eligible_tasks_and_redacts_activity_hashes(self):
        from app.qbittorrent_action_runtime import QbittorrentActionService

        before = summary([
            task(HASH_A, "downloading", "downloading"),
            task(HASH_B, "pausedDL", "paused"),
        ])
        after = summary([
            task(HASH_A, "pausedDL", "paused"),
            task(HASH_B, "pausedDL", "paused"),
        ])
        client = FakeActionClient([before, after])
        activities = []
        service = QbittorrentActionService(
            client,
            activity_writer=lambda *args, **kwargs: activities.append((args, kwargs)),
        )

        result = service.execute("pause", {
            "hashes": [HASH_A, HASH_B],
            "taskId": "subscription:test",
            "title": "测试媒体",
        })

        self.assertEqual(client.actions, [("pause", [HASH_A])])
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(result["confirmed"])
        activity_text = str(activities)
        self.assertIn(HASH_A[:8], activity_text)
        self.assertNotIn(HASH_A, activity_text)
        self.assertNotIn(HASH_B, activity_text)

    def test_missing_hash_rejects_entire_request_without_writing(self):
        from app.qbittorrent_action_runtime import (
            QbittorrentActionError,
            QbittorrentActionService,
        )

        client = FakeActionClient([summary([task(HASH_A, "downloading", "downloading")])])
        service = QbittorrentActionService(client, activity_writer=lambda *args, **kwargs: None)

        with self.assertRaises(QbittorrentActionError) as raised:
            service.execute("pause", {"hashes": [HASH_A, HASH_B]})

        self.assertEqual(raised.exception.status, 404)
        self.assertEqual(raised.exception.code, "QB_TASK_NOT_FOUND")
        self.assertEqual(client.actions, [])

    def test_unconfirmed_action_returns_202_and_fixed_contract(self):
        from flask import Flask

        from app.qbittorrent_action_runtime import register_qbittorrent_actions

        client = FakeActionClient([
            summary([task(HASH_A, "downloading", "downloading")]),
            summary([], connected=False, error="qBittorrent 请求失败"),
        ])
        application = Flask(__name__)
        register_qbittorrent_actions(
            application,
            client,
            activity_writer=lambda *args, **kwargs: None,
        )

        response = application.test_client().post(
            "/api/qbittorrent/actions/pause",
            json={"hashes": [HASH_A], "title": "测试媒体"},
        )

        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.get_json()["confirmed"])
        self.assertEqual(response.get_json()["failed"], 1)


if __name__ == "__main__":
    unittest.main()
