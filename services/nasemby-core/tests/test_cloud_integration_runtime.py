from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from flask import Flask

from app import discover_runtime
from app.cloud_acquisition_runtime import register_cloud_acquisition
from app.integration_runtime import register_integrations
from tests.activity_log_test_support import IsolatedActivityLogMixin


def subscription_config(**cloud_overrides):
    cloud = {
        "enabled": True,
        "auto_fallback_enabled": False,
        "manual_actions_enabled": True,
        "wait_minutes": 360,
        "sources": ["hdhive", "telegram"],
        "auto_select": False,
        **cloud_overrides,
    }
    return {
        "mode": "torra",
        "cloud_acquisition": cloud,
        "douban": {
            "enabled": True,
            "movie_enabled": True,
            "tv_enabled": True,
            "movie_years": ["2026"],
            "tv_min_rating": 0,
            "exclude_titles": [],
            "sources": ["hot_movie"],
            "daily_only": False,
            "task_time": "08:30",
            "task_enabled": False,
            "updated_at": "",
            "last_run_at": "",
        },
    }


class CloudIntegrationRuntimeTests(IsolatedActivityLogMixin, unittest.TestCase):
    def test_old_resource_default_migrates_to_torra_and_cloud_stays_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "subscriptions.json"
            config_path.write_text(json.dumps({"mode": "resource"}), encoding="utf-8")
            with patch.object(discover_runtime, "SUBSCRIPTION_CONFIG_PATH", str(config_path)):
                config = discover_runtime.load_subscription_config()
        self.assertEqual(config["mode"], "torra")
        self.assertEqual(config["cloud_acquisition"]["enabled"], False)
        self.assertEqual(config["cloud_acquisition"]["auto_fallback_enabled"], False)
        self.assertEqual(discover_runtime.normalize_subscription_mode("unknown"), "torra")

    def test_integration_summary_is_masked_and_probe_is_separately_gated(self):
        environment = {
            "MCC_INTEGRATION_PROBE_ENABLED": "false",
            "MCC_INTEGRATION_MANAGEMENT_ENABLED": "false",
        }
        config = {
            "ENV_115_COOKIES": "secret-cookie",
            "ENV_UPLOAD_PID": "123",
            "ENV_TG_API_ID": "12345",
            "ENV_TG_API_HASH": "secret-hash",
            "ENV_HDHIVE_CHECKIN_ENABLED": "1",
            "ENV_MOVIEPILOT_URL": "http://moviepilot.invalid",
            "ENV_MOVIEPILOT_API_TOKEN": "secret-token",
        }
        app = Flask(__name__)
        register_integrations(
            app,
            environment=environment,
            config_reader=lambda: config,
            functions={
                "telegram_list_channels": lambda: {"ok": True, "channels": [{"name": "资源频道"}]},
                "check_115_account": lambda: {"ok": True, "user": {"nickname": "tester"}},
                "telegram_status": lambda: {"ok": True, "authorized": True, "channels": []},
                "hdhive_status": lambda: {"ok": True, "account": {"display_name": "hive"}},
                "moviepilot_status": lambda: {"ok": True},
            },
        )
        client = app.test_client()
        summary = client.get("/api/v2/integrations")
        self.assertEqual(summary.status_code, 200)
        text = summary.get_data(as_text=True)
        self.assertNotIn("secret-cookie", text)
        self.assertNotIn("secret-hash", text)
        self.assertNotIn("secret-token", text)
        self.assertEqual(len(summary.get_json()["services"]), 4)
        self.assertEqual(client.get("/api/v2/integrations?probe=1").status_code, 403)

        environment["MCC_INTEGRATION_PROBE_ENABLED"] = "true"
        probed = client.get("/api/v2/integrations?probe=1")
        self.assertEqual(probed.status_code, 200)
        services = {item["id"]: item for item in probed.get_json()["services"]}
        self.assertTrue(services["cloud115"]["connected"])
        self.assertTrue(services["telegram"]["connected"])

    def test_cloud_preview_is_sanitized_and_transfer_is_gated_idempotent(self):
        environment = {
            "MCC_CLOUD_SEARCH_ENABLED": "true",
            "MCC_CLOUD_TRANSFER_ENABLED": "false",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "subscriptions.json"
            items_path = root / "items.json"
            state_path = root / "actions.json"
            config_path.write_text(json.dumps(subscription_config(), ensure_ascii=False), encoding="utf-8")
            items_path.write_text(json.dumps({
                "items": [{
                    "key": "movie:550",
                    "title": "测试电影",
                    "media_type": "movie",
                    "tmdb_id": "550",
                    "allow_cloud_fallback": True,
                }]
            }, ensure_ascii=False), encoding="utf-8")

            app = Flask(__name__)
            app.extensions["mcc_task_chain_service"] = type("Chain", (), {
                "get_chain": lambda self: {"items": [{
                    "sourceIds": {"subscriptionId": "movie:550", "torraId": "", "qbHashes": [], "symediaIds": []},
                    "embyIndexed": False,
                }]}
            })()
            transfers = []
            with patch.object(discover_runtime, "SUBSCRIPTION_CONFIG_PATH", str(config_path)), patch.object(
                discover_runtime, "SUBSCRIPTION_ITEMS_PATH", str(items_path)
            ):
                register_cloud_acquisition(
                    app,
                    environment=environment,
                    functions={
                        "search_resources": lambda payload: {
                            "success": True,
                            "items": [{
                                "source_key": "hdhive",
                                "source_label": "HDHive",
                                "title": "测试电影 2160P",
                                "share_url": "https://115.com/s/private?password=secret",
                                "password": "secret",
                            }],
                            "errors": [],
                        },
                        "transfer_yingchao_item": lambda item: transfers.append(item) or {"ok": True},
                    },
                    state_path=state_path,
                    clock=lambda: 1_700_000_000.0,
                )
                client = app.test_client()
                preview = client.get("/api/v2/acquisition/cloud/candidates?id=movie:550")
                self.assertEqual(preview.status_code, 200)
                preview_text = preview.get_data(as_text=True)
                self.assertNotIn("115.com", preview_text)
                self.assertNotIn("secret", preview_text)
                candidate_id = preview.get_json()["candidates"][0]["id"]

                body = {"candidateId": candidate_id, "idempotencyKey": "request-key-123456", "confirm": True}
                self.assertEqual(client.post("/api/v2/acquisition/cloud/transfers", json=body).status_code, 403)
                environment["MCC_CLOUD_TRANSFER_ENABLED"] = "true"
                first = client.post("/api/v2/acquisition/cloud/transfers", json=body)
                second = client.post("/api/v2/acquisition/cloud/transfers", json=body)

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(len(transfers), 1)
            self.assertFalse(first.get_json()["replayed"])
            self.assertTrue(second.get_json()["replayed"])
            self.assertTrue(state_path.exists())


if __name__ == "__main__":
    unittest.main()
