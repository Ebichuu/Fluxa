from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from app.main import create_app
from app.quality_watch_repository import QualityWatchRepository


class AutomationActionRuntimeTests(unittest.TestCase):
    def test_reads_persisted_action_and_redacts_external_details(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            claimed = repository.claim_action(
                "automation-action-0001",
                "tv:202",
                "torra",
                "rewash-analysis",
            )
            action_id = claimed["action"]["action_id"]
            repository.save_external_job(action_id, "upstream-job-secret-101")
            repository.complete_action(action_id, "failed", {
                "message": "上游失败 https://torra.example/jobs/secret",
                "token": "must-not-escape",
                "candidate_download_url": "https://tracker.example/download?passkey=secret",
                "counts": {"checked": 3},
            }, error_code="TORRA_FAILED", error_message="failed https://torra.example/private")
            client = create_app(
                access_environment={},
                quality_watch_repository=repository,
            ).test_client()

            response = client.get(f"/api/v2/automation-actions/{action_id}")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["id"], action_id)
            self.assertEqual(payload["provider"], "torra")
            self.assertEqual(payload["status"], "failed")
            self.assertTrue(payload["externalJobId"].startswith("sha256:"))
            self.assertEqual(payload["result"]["counts"], {"checked": 3})
            serialized = response.get_data(as_text=True)
            self.assertNotIn("upstream-job-secret-101", serialized)
            self.assertNotIn("must-not-escape", serialized)
            self.assertNotIn("tracker.example", serialized)
            self.assertNotIn("torra.example", serialized)

    def test_missing_action_is_404_and_auth_is_enforced_by_application(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            missing = create_app(
                access_environment={},
                quality_watch_repository=repository,
            ).test_client().get("/api/v2/automation-actions/missing")
            self.assertEqual(missing.status_code, 404)

            protected = create_app(
                access_environment={"MCC_ACCESS_KEY": "contract-access-key-1234567890"},
                quality_watch_repository=repository,
            ).test_client().get("/api/v2/automation-actions/missing")
            self.assertEqual(protected.status_code, 401)
            self.assertEqual(protected.get_json()["code"], "AUTH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
