from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.main import create_app
from app.private_rss_repository import PrivateRssRepository


class FakeCollector:
    def fetch_source(self, source_id, persist=False):
        return {"status": "success", "items": 2, "title": "测试 RSS"}


class PrivateRssApiRuntimeTests(unittest.TestCase):
    def test_crud_is_local_and_collection_test_has_separate_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            environment = {"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"}
            app = create_app(
                access_environment=environment,
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
            )
            client = app.test_client()
            created = client.post("/api/v2/rss-sources", json={
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=secret-value",
                "intervalMinutes": 3,
                "retentionDays": 7,
            })
            self.assertEqual(created.status_code, 201)
            source_id = created.get_json()["id"]
            self.assertNotIn("secret-value", created.get_data(as_text=True))
            detail = client.get(created.headers["Location"])
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.get_json()["id"], source_id)
            self.assertNotIn("secret-value", detail.get_data(as_text=True))
            self.assertEqual(client.get("/api/v2/rss-sources").get_json()["summary"]["sources"], 1)
            disabled = client.post(f"/api/v2/rss-sources/{source_id}/tests")
            self.assertEqual(disabled.status_code, 503)
            environment["MCC_PRIVATE_RSS_ENABLED"] = "true"
            accepted = client.post(f"/api/v2/rss-sources/{source_id}/tests")
            self.assertEqual(accepted.status_code, 202)
            self.assertTrue(accepted.headers["Location"].startswith("/api/v2/automation-actions/"))
            action = client.get(accepted.headers["Location"])
            self.assertEqual(action.get_json()["status"], "succeeded")
            deleted = client.delete(f"/api/v2/rss-sources/{source_id}")
            self.assertEqual(deleted.status_code, 204)


if __name__ == "__main__":
    unittest.main()
