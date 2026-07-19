import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import admin as admin_cli
from app import main
from app.admin_auth import AdminCredentialStore


class AdminAuthRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.now = 1_700_000_000_000
        self.store = AdminCredentialStore(Path(self.temp_dir.name) / "auth.sqlite3")

    def create_app(self):
        return main.create_app(
            access_environment={},
            admin_store=self.store,
            now_ms=lambda: self.now,
        )

    def initialize(self, client):
        response = client.post(
            "/auth/setup",
            data={
                "username": "admin",
                "password": "correct-password",
                "password_confirmation": "correct-password",
                "next": "/",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("mcc_session=v2.", response.headers["Set-Cookie"])
        self.assertNotIn("; Secure", response.headers["Set-Cookie"])
        return response

    def test_first_visit_requires_setup_and_setup_creates_single_admin(self):
        application = self.create_app()
        client = application.test_client()

        first_page = client.get("/")
        self.assertEqual(first_page.status_code, 303)
        self.assertIn("/auth/setup", first_page.headers["Location"])
        self.assertIn("创建管理员", client.get("/auth/setup").get_data(as_text=True))

        self.initialize(client)
        session = client.get("/api/auth/session").get_json()
        self.assertEqual(session["setupRequired"], False)
        self.assertEqual(session["authenticated"], True)
        self.assertEqual(session["username"], "admin")
        self.assertNotEqual(self.store.read().password_hash, "correct-password")
        self.assertEqual(client.get("/auth/setup").status_code, 303)
        self.assertEqual(
            client.post("/auth/setup", data={
                "username": "second",
                "password": "another-password",
                "password_confirmation": "another-password",
            }).status_code,
            409,
        )

    def test_login_uses_username_and_password_without_origin_configuration(self):
        application = self.create_app()
        client = application.test_client()
        self.initialize(client)
        client.post("/auth/logout")

        denied = client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong-password"},
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(denied.status_code, 403)

        same_origin = client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong-password"},
            headers={"Origin": "https://proxy.example", "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(same_origin.status_code, 401)

        login = client.post(
            "/auth/login",
            data={"username": "admin", "password": "correct-password"},
        )
        self.assertEqual(login.status_code, 303)
        self.assertEqual(client.get("/api/auth/session").get_json()["username"], "admin")

    def test_password_reset_invalidates_existing_session(self):
        application = self.create_app()
        client = application.test_client()
        self.initialize(client)
        auth = application.extensions["mcc_access_auth"]

        self.assertIsNotNone(auth.reset_password("admin", "new-password"))
        self.assertEqual(client.get("/api/auth/session").get_json()["authenticated"], False)

        login = client.post(
            "/auth/login",
            data={"username": "admin", "password": "new-password"},
        )
        self.assertEqual(login.status_code, 303)

    def test_reset_password_command_rotates_credentials(self):
        application = self.create_app()
        client = application.test_client()
        self.initialize(client)

        with patch.object(admin_cli, "AUTH_DB_PATH", self.store.database_path), patch(
            "builtins.input",
            return_value="admin",
        ), patch.object(
            admin_cli.getpass,
            "getpass",
            side_effect=["command-password", "command-password"],
        ):
            self.assertEqual(admin_cli.main(["reset-password"]), 0)

        self.assertEqual(client.get("/api/auth/session").get_json()["authenticated"], False)
        self.assertEqual(
            client.post(
                "/auth/login",
                data={"username": "admin", "password": "command-password"},
            ).status_code,
            303,
        )

    def test_https_proxy_sets_secure_cookie_without_configuration(self):
        application = main.create_app(
            access_environment={"MCC_ENV": "production"},
            admin_store=self.store,
            now_ms=lambda: self.now,
        )
        response = application.test_client().post(
            "/auth/setup",
            data={
                "username": "admin",
                "password": "correct-password",
                "password_confirmation": "correct-password",
            },
            headers={
                "Origin": "https://media.example",
                "Sec-Fetch-Site": "same-origin",
                "X-Forwarded-For": "192.0.2.10",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "media.example",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("; Secure", response.headers["Set-Cookie"])

    def test_invalid_setup_inputs_do_not_create_admin(self):
        application = self.create_app()
        client = application.test_client()

        response = client.post(
            "/auth/setup",
            data={
                "username": "ad",
                "password": "short",
                "password_confirmation": "different",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.store.read())


if __name__ == "__main__":
    unittest.main()
