from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import create_app


class AuthAndApiTests(unittest.TestCase):
    def test_healthz_includes_build_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"FLUXA_BUILD_REVISION": "abc1234"}):
                app = create_app(db_path=Path(tmp) / "fluxa.sqlite3")
                client = app.test_client()
                res = client.get("/healthz").get_json()
                self.assertTrue(res["ok"])
                self.assertEqual(res["version"], "2.0.0")
                self.assertEqual(res["revision"], "abc1234")

    def test_first_run_setup_settings_dashboard_and_guardrails(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(db_path=Path(tmp) / "fluxa.sqlite3")
            client = app.test_client()

            # Uninitialized state
            sess = client.get("/api/auth/session").get_json()
            self.assertFalse(sess["initialized"])
            self.assertFalse(sess["authenticated"])

            # Protected API blocked before setup
            self.assertEqual(client.get("/api/settings").status_code, 401)

            # Setup admin
            res = client.post("/api/auth/setup", json={"username": "admin", "password": "password123"})
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()["ok"])

            # Settings readable
            settings_resp = client.get("/api/settings")
            self.assertEqual(settings_resp.status_code, 200)
            data = settings_resp.get_json()
            self.assertIn("siteOfficialGroups", data["guardrails"])

            # Dashboard works cleanly even before services are configured
            dash = client.get("/api/dashboard").get_json()
            self.assertEqual(dash["counts"]["issues"], 0)
            self.assertEqual(len(dash["services"]), 6)

            # Check release group endpoint: cross-site repost blocked
            check = client.post(
                "/api/guardrails/check-release",
                json={"siteId": "mteam", "title": "Movie.2026.2160p-HDHome"},
            ).get_json()
            self.assertEqual(check["classification"], "cross_site_repost")
            self.assertFalse(check["allowed"])

            # Check old movie guardrail blocks auto chart subscription unless overridden/collect
            blocked = client.post(
                "/api/subscribe",
                json={
                    "mediaType": "movie",
                    "tmdbId": "550",
                    "title": "Fight Club",
                    "year": "1999",
                    "intent": "follow",
                    "fromChart": True,
                },
            )
            self.assertEqual(blocked.status_code, 409)
            self.assertTrue(blocked.get_json()["blocked"])

            # Decision log recorded the block explanation
            subs_view = client.get("/api/subscriptions").get_json()
            self.assertEqual(len(subs_view["decisionLogs"]), 1)
            self.assertEqual(subs_view["decisionLogs"][0]["outcome"], "blocked")

            # Bounded action preview for torra_retry_upload requires confirm
            preview = client.post(
                "/api/actions/preview",
                json={"actionType": "torra_retry_upload", "payload": {"taskKey": "secupload_run", "fileName": "a.mkv"}},
            ).get_json()
            self.assertTrue(preview["requiresConfirm"])
            self.assertTrue(preview["allowed"])


if __name__ == "__main__":
    unittest.main()
