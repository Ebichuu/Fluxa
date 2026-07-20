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


class ActivityApiRuntimeTests(unittest.TestCase):
    def test_v2_activity_api_reads_logs_and_requires_clear_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "activity.jsonl"
            with patch.object(activity_log, "LOG_PATH", log_path):
                app = Flask(__name__)
                configure_http_runtime(app)
                register_activity_api(app)
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


if __name__ == "__main__":
    unittest.main()
