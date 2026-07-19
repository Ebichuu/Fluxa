import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, main
from app.admin_auth import AdminCredentialStore
from app.config import CONFIG_FIELDS
from app.runtime_settings import build_runtime_settings


def field_by_key(payload, key):
    for group in payload["groups"]:
        for field in group["fields"]:
            if field["key"] == key:
                return field
    raise AssertionError(f"missing field {key}")


class RuntimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.patchers = [
            patch.object(config, "ROOT_DIR", self.root),
            patch.object(config, "WORKSPACE_ENV_PATH", self.root / "workspace.env"),
            patch.object(config, "DATA_DIR", self.root / "data"),
            patch.object(config, "USER_ENV_PATH", self.root / "data" / "user.env"),
            patch.object(config, "LEGACY_DB_DIR", self.root / "db"),
            patch.object(config, "LEGACY_USER_ENV_PATH", self.root / "db" / "user.env"),
            patch.object(config, "SYS_ENV_PATH", self.root / "sys.env"),
            patch.dict(os.environ, {}, clear=False),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.environment = {
            "EMBY_BASE_URL": "http://old-emby.local:8096",
            "EMBY_USERNAME": "admin",
            "EMBY_PASSWORD": "old-password",
            "MCC_PRIVATE_RSS_ENABLED": "false",
        }
        store = AdminCredentialStore(self.root / "db" / "auth.sqlite3")
        application = main.create_app(access_environment=self.environment, admin_store=store)
        self.client = application.test_client()
        response = self.client.post("/auth/setup", data={
            "username": "admin",
            "password": "correct-password",
            "password_confirmation": "correct-password",
        })
        self.assertEqual(response.status_code, 303)
        self.application = application

    def test_read_redacts_secrets_and_lists_all_application_fields(self):
        response = self.client.get("/api/v2/settings/runtime")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        password = field_by_key(payload, "EMBY_PASSWORD")
        self.assertEqual(password["value"], "")
        self.assertTrue(password["hasValue"])
        self.assertEqual(field_by_key(payload, "EMBY_BASE_URL")["value"], "http://old-emby.local:8096")
        keys = [field["key"] for group in payload["groups"] for field in group["fields"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(CONFIG_FIELDS))

    def test_save_updates_environment_clients_and_preserves_blank_secret(self):
        response = self.client.put("/api/v2/settings/runtime", json={
            "values": {
                "EMBY_BASE_URL": "http://new-emby.local:8096",
                "EMBY_PASSWORD": "",
                "MCC_PRIVATE_RSS_ENABLED": True,
                "TORRA_BASE_URL": "http://new-torra.local:9029",
            },
            "clearSecrets": [],
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["changedKeys"], ["EMBY_BASE_URL", "MCC_PRIVATE_RSS_ENABLED", "TORRA_BASE_URL"])
        self.assertEqual(self.environment["EMBY_PASSWORD"], "old-password")
        self.assertEqual(self.environment["MCC_PRIVATE_RSS_ENABLED"], "true")
        emby = self.application.extensions["mcc_emby_client"]
        self.assertEqual(emby.server_url, "http://new-emby.local:8096")
        self.assertEqual(self.application.extensions["mcc_torra_client"].base_url, "http://new-torra.local:9029")
        self.assertEqual(self.application.extensions["mcc_torra_quality_client"].base_url, "http://new-torra.local:9029")
        written = config.USER_ENV_PATH.read_text(encoding="utf-8")
        self.assertIn("EMBY_PASSWORD=old-password", written)
        self.assertNotIn("MCC_DATA_ROOT", written)

    def test_secret_can_be_replaced_or_explicitly_cleared(self):
        replaced = self.client.put("/api/v2/settings/runtime", json={
            "values": {"EMBY_PASSWORD": "new-password"},
            "clearSecrets": [],
        })
        self.assertEqual(replaced.status_code, 200)
        self.assertTrue(field_by_key(replaced.get_json(), "EMBY_PASSWORD")["hasValue"])
        self.assertEqual(self.environment["EMBY_PASSWORD"], "new-password")

        cleared = self.client.put("/api/v2/settings/runtime", json={
            "values": {},
            "clearSecrets": ["EMBY_PASSWORD"],
        })
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(field_by_key(cleared.get_json(), "EMBY_PASSWORD")["hasValue"])
        self.assertEqual(self.environment["EMBY_PASSWORD"], "")

    def test_validation_rejects_unknown_invalid_url_and_newlines(self):
        cases = [
            {"values": {"UNKNOWN_KEY": "value"}},
            {"values": {"EMBY_BASE_URL": "emby.local:8096"}},
            {"values": {"EMBY_USERNAME": "admin\nleak"}},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.put("/api/v2/settings/runtime", json=payload)
                self.assertEqual(response.status_code, 422)

        proxy = self.client.put("/api/v2/settings/runtime", json={
            "values": {"ENV_PROXY": "socks5://proxy.local:1080"},
        })
        self.assertEqual(proxy.status_code, 200)

    def test_catalog_covers_env_example_application_keys(self):
        workspace = Path(__file__).resolve().parents[3]
        keys = {
            line.split("=", 1)[0].strip()
            for line in (workspace / ".env.example").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#") and "=" in line
        }
        keys.remove("MCC_DATA_ROOT")
        payload = build_runtime_settings(self.environment)
        catalog = {field["key"] for group in payload["groups"] for field in group["fields"]}
        self.assertTrue(keys <= catalog)


if __name__ == "__main__":
    unittest.main()
