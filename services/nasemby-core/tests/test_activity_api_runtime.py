from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import activity_log
from app.activity_api_runtime import register_activity_api
from app.activity_log import write_activity
from app.http_runtime import configure_http_runtime


def _make_app():
    app = Flask(__name__)
    configure_http_runtime(app)
    register_activity_api(app)
    return app


class ActivityApiRuntimeTests(unittest.TestCase):
    def test_v2_activity_api_reads_logs_and_requires_clear_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activity.jsonl"
            with patch.object(activity_log, "LOG_PATH", log_path):
                app = _make_app()
                write_activity("torra_sync", "torra_sync_import", "success", "已导入 3 条")
                client = app.test_client()

                listed = client.get("/api/v2/activity/logs?category=torra_sync")
                denied = client.delete("/api/v2/activity/logs", json={})
                cleared = client.delete("/api/v2/activity/logs", json={"confirm": True})

                self.assertEqual(listed.status_code, 200)
                self.assertEqual(len(listed.get_json()["logs"]), 1)
                self.assertEqual(denied.status_code, 400)
                self.assertEqual(cleared.status_code, 200)
                self.assertEqual(len(client.get("/api/v2/activity/logs").get_json()["logs"]), 1)

    def test_activity_writer_redacts_secrets_and_url_query_values(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activity.jsonl"
            with patch.object(activity_log, "LOG_PATH", log_path):
                write_activity(
                    "operation",
                    "test",
                    "error",
                    "Bearer secret-token https://rss.example.test/feed?passkey=private-value&uid=private-uid&rows=10 password=plain-secret",
                    token="private-token",
                    nested={"password": "private-password", "safe": "kept"},
                )
                text = log_path.read_text(encoding="utf-8")

                self.assertNotIn("secret-token", text)
                self.assertNotIn("private-value", text)
                self.assertNotIn("private-token", text)
                self.assertNotIn("private-password", text)
                self.assertNotIn("private-uid", text)
                self.assertNotIn("plain-secret", text)
                self.assertIn("rows=***", text)
                self.assertIn("kept", text)


class ActivityImportantViewTests(unittest.TestCase):
    """view=important 在应用 limit 前折叠重复后台活动；raw 行为完全不变。"""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self._patch = patch.object(
            activity_log, "LOG_PATH", Path(self._directory.name) / "activity.jsonl"
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.app = _make_app()
        self.client = self.app.test_client()

    def test_important_view_folds_background_repeats_before_limit(self):
        # 第 1 条：人工失败，随后 200 条重复后台同步
        write_activity("push", "manual_push", "error", "人工推送失败", request_id="req-human-1")
        for index in range(200):
            write_activity(
                "push", "subscription_postprocess_background", "success",
                f"订阅后处理完成 {index}", request_id="background",
            )

        body = self.client.get("/api/v2/activity/logs?view=important&limit=100").get_json()

        self.assertTrue(body["ok"])
        self.assertEqual(body["view"], "important")
        logs = body["logs"]
        # 200 条后台同步折叠成 1 条，人工失败不被挤掉
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["action"], "subscription_postprocess_background")
        self.assertEqual(logs[0]["repeatCount"], 200)
        self.assertEqual(logs[0]["message"], "订阅后处理完成 199")
        self.assertLessEqual(logs[0]["firstTime"], logs[0]["lastTime"])
        self.assertEqual(logs[1]["action"], "manual_push")
        self.assertEqual(logs[1]["status"], "error")
        self.assertNotIn("repeatCount", logs[1])

    def test_raw_view_order_and_count_are_unchanged(self):
        write_activity("push", "manual_push", "error", "人工推送失败", request_id="req-human-1")
        for index in range(200):
            write_activity(
                "push", "subscription_postprocess_background", "success",
                f"订阅后处理完成 {index}", request_id="background",
            )

        default_body = self.client.get("/api/v2/activity/logs?limit=100").get_json()
        raw_body = self.client.get("/api/v2/activity/logs?view=raw&limit=100").get_json()

        # raw 默认行为不变：仍是倒序 100 条，全部是后台同步
        for body in (default_body, raw_body):
            self.assertEqual(len(body["logs"]), 100)
            self.assertEqual(body["logs"][0]["message"], "订阅后处理完成 199")
            self.assertEqual(body["logs"][99]["message"], "订阅后处理完成 100")
            self.assertTrue(all("repeatCount" not in row for row in body["logs"]))
        self.assertEqual(default_body["view"], "raw")

    def test_background_errors_and_human_requests_are_never_folded(self):
        for index in range(3):
            write_activity(
                "push", "subscription_postprocess_background", "error",
                f"后台同步失败 {index}", request_id="background", error=f"错误 {index}",
            )
        for index in range(3):
            write_activity(
                "push", "subscription_postprocess_queue", "success",
                f"人工排队 {index}", request_id=f"req-human-{index}",
            )

        logs = self.client.get("/api/v2/activity/logs?view=important").get_json()["logs"]

        # 3 条不同 error + 3 条人工请求全部保留，不合并
        self.assertEqual(len(logs), 6)
        self.assertTrue(all("repeatCount" not in row for row in logs))
        messages = [row["message"] for row in logs]
        self.assertEqual(messages, [
            "人工排队 2", "人工排队 1", "人工排队 0",
            "后台同步失败 2", "后台同步失败 1", "后台同步失败 0",
        ])

    def test_important_view_filters_category_before_folding(self):
        write_activity("transfer", "other_background", "success", "转存后台", request_id="background")
        for index in range(5):
            write_activity(
                "push", "subscription_postprocess_background", "skip",
                f"跳过 {index}", request_id="background",
            )

        logs = self.client.get("/api/v2/activity/logs?view=important&category=push").get_json()["logs"]

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["category"], "push")
        self.assertEqual(logs[0]["repeatCount"], 5)

    def test_important_view_rejects_unknown_view_value(self):
        response = self.client.get("/api/v2/activity/logs?view=other")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "ACTIVITY_VIEW_INVALID")


if __name__ == "__main__":
    unittest.main()
