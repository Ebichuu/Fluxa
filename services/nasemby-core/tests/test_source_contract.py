import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.config import DEFAULT_CONFIG
from app import discover_runtime
from tests.activity_log_test_support import IsolatedActivityLogMixin


MODULE_ROOT = Path(__file__).resolve().parents[1]


class SourceContractTest(IsolatedActivityLogMixin, unittest.TestCase):
    def test_external_automatic_actions_default_to_disabled(self):
        keys = (
            "ENV_115_TGMONITOR_SWITCH",
            "ENV_115_AUTO_CLASSIFY",
            "ENV_PTTO115_SWITCH",
            "ENV_PTTO123_SWITCH",
            "ENV_MOVIEPILOT_AUTO_SUBSCRIBE",
            "ENV_TORRA_AUTO_SUBSCRIBE",
            "ENV_SYMEDIA_AUTO_SUBSCRIBE",
            "ENV_TG_SUBSCRIPTION_NOTIFY_ENABLED",
            "ENV_HDHIVE_CHECKIN_ENABLED",
        )
        self.assertEqual({key: DEFAULT_CONFIG[key] for key in keys}, {key: "0" for key in keys})

    def test_workspace_env_path_supports_local_and_container_roots(self):
        from app.config import resolve_workspace_env_path

        with tempfile.TemporaryDirectory() as temp_dir:
            container_root = Path(temp_dir) / "app"
            container_root.mkdir()
            self.assertEqual(resolve_workspace_env_path(container_root), container_root / ".env")

            workspace_root = Path(temp_dir) / "workspace"
            workspace_root.mkdir()
            (workspace_root / "package.json").write_text("{}", encoding="utf-8")
            service_root = workspace_root / "services" / "nasemby-core"
            service_root.mkdir(parents=True)
            self.assertEqual(resolve_workspace_env_path(service_root), workspace_root / ".env")

    def test_unified_entry_registers_current_runtimes_and_preserves_core_routes(self):
        main_source = (MODULE_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        for registrar in (
            "register_discover_compat(application)",
            "register_subscription_compat(",
            "register_task_chain(application)",
            "register_torra_read(",
            "register_quality_watch(",
            "register_moviepilot_backup(",
            "register_private_rss(",
            "register_symedia_read(",
        ):
            self.assertIn(registrar, main_source)

        for preserved_route in (
            "/api/115/check",
            "/api/telegram/status",
            "/api/hdhive/status",
            "/api/torra/subscribe",
            "/api/symedia/subscribe",
        ):
            self.assertIn(preserved_route, main_source)

        self.assertIn("PRESERVED_CORE_API_PATHS", main_source)
        self.assertIn("MCC_PRESERVED_CORE_API_ENABLED", main_source)
        self.assertFalse((MODULE_ROOT / "Dockerfile").exists())
        self.assertFalse((MODULE_ROOT / ".env.example").exists())

    def test_preserved_core_routes_are_disabled_by_default_but_remain_testable(self):
        from app import main

        disabled = main.create_app().test_client().post("/api/115/check", json={})
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(disabled.get_json()["code"], "PRESERVED_CORE_API_DISABLED")

        with patch.dict(
            main.os.environ,
            {"MCC_PRESERVED_CORE_API_ENABLED": "true"},
            clear=False,
        ), patch.object(
            main,
            "check_115_account",
            return_value={"ok": True, "account": "mocked"},
        ):
            enabled = main.create_app().test_client().post("/api/115/check", json={})

        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(enabled.get_json()["ok"])

    def test_all_preserved_core_capability_routes_remain_registered(self):
        from app import main

        routes = {
            (method, rule.rule)
            for rule in main.create_app().url_map.iter_rules()
            for method in (rule.methods or ())
            if method not in {"HEAD", "OPTIONS"}
        }
        expected = {
            ("GET", "/api/dashboard/system"),
            ("GET", "/api/hdhive/authorize"),
            ("GET", "/api/hdhive/status"),
            ("GET", "/api/hdhive/identity"),
            ("POST", "/api/hdhive/config"),
            ("POST", "/api/hdhive/account"),
            ("POST", "/api/hdhive/checkin"),
            ("GET", "/api/config"),
            ("POST", "/api/config"),
            ("POST", "/api/emby/libraries"),
            ("GET", "/api/emby/library-image/<path:item_id>"),
            ("GET", "/api/telegram/status"),
            ("POST", "/api/telegram/send-code"),
            ("POST", "/api/telegram/sign-in"),
            ("POST", "/api/telegram/logout"),
            ("GET", "/api/telegram/channels"),
            ("GET", "/api/telegram/channel-icons/<path:filename>"),
            ("POST", "/api/telegram/channels"),
            ("DELETE", "/api/telegram/channels/<int:index>"),
            ("POST", "/api/telegram/channels/reorder"),
            ("GET", "/api/activity/logs"),
            ("POST", "/api/activity/clear"),
            ("POST", "/api/activity/event"),
            ("POST", "/api/115/check"),
            ("POST", "/api/115/extract"),
            ("POST", "/api/115/transfer"),
            ("POST", "/api/115/monitor/run"),
            ("POST", "/api/115/cleanup/run"),
            ("POST", "/api/115/boost"),
            ("POST", "/api/yingchao/search"),
            ("POST", "/api/yingchao/transfer"),
            ("GET", "/api/moviepilot/status"),
            ("POST", "/api/moviepilot/subscribe"),
            ("GET", "/api/torra/status"),
            ("POST", "/api/torra/subscribe"),
            ("GET", "/api/symedia/status"),
            ("POST", "/api/symedia/subscribe"),
        }

        self.assertEqual(expected - routes, set())

    def test_preserved_core_route_requires_auth_and_origin_before_disabled_state(self):
        from app import main

        access_key = "contract-access-key-1234567890"
        application = main.create_app(access_environment={
            "MCC_ACCESS_KEY": access_key,
            "MCC_COOKIE_SECURE": "false",
            "MCC_PRESERVED_CORE_API_ENABLED": "false",
        })
        client = application.test_client()

        unauthenticated = client.post("/api/115/check", json={})
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated.get_json()["code"], "AUTH_REQUIRED")

        self.assertEqual(
            client.post("/auth/login", data={"access_key": access_key}).status_code,
            303,
        )
        wrong_origin = client.post(
            "/api/115/check",
            headers={"Origin": "https://evil.example.test"},
            json={},
        )
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(wrong_origin.get_json()["code"], "ORIGIN_FORBIDDEN")

        disabled = client.post("/api/115/check", json={})
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(disabled.get_json(), {
            "ok": False,
            "error": "该核心接口已保留，等待统一页面完成安全接入",
            "code": "PRESERVED_CORE_API_DISABLED",
        })

    def test_streaming_providers_use_verified_us_watch_provider_ids(self):
        expected = {
            "netflix": "8",
            "disney": "337",
            "max": "1899",
            "prime": "9",
            "apple": "350",
            "hulu": "15",
            "paramount": "2303|2616",
            "peacock": "386",
        }
        self.assertEqual(
            {key: value["id"] for key, value in discover_runtime.STREAMING_PROVIDERS.items()},
            expected,
        )

        for provider_key, provider_id in expected.items():
            captured = {}

            def fake_fetch(query):
                captured.update(query)
                return {"success": True, "source": "TMDB", "items": [{"source": "TMDB"}]}

            def bypass_cache(_category, _query, loader, ttl):
                self.assertGreater(ttl, 0)
                return loader()

            with patch.object(discover_runtime, "_fetch_tmdb_uncached", side_effect=fake_fetch), patch.object(
                discover_runtime,
                "cached_discover_call",
                side_effect=bypass_cache,
            ):
                payload = discover_runtime.fetch_streaming({"provider": provider_key, "type": "tv"})

            self.assertEqual(captured["_watch_provider_ids"], provider_id)
            self.assertEqual(captured["trend"], "全部")
            self.assertEqual(payload["provider"], provider_key)
            self.assertEqual(payload["watch_region"], "US")
            self.assertTrue(payload["source"].startswith("JustWatch · "))
            self.assertEqual(payload["items"][0]["source"], payload["source"])

    def test_unknown_streaming_provider_falls_back_to_netflix(self):
        captured = {}

        def fake_fetch(query):
            captured.update(query)
            return {"success": True, "items": []}

        with patch.object(discover_runtime, "_fetch_tmdb_uncached", side_effect=fake_fetch), patch.object(
            discover_runtime,
            "cached_discover_call",
            side_effect=lambda _category, _query, loader, ttl: loader(),
        ):
            payload = discover_runtime.fetch_streaming({"provider": "unknown"})

        self.assertEqual(captured["_watch_provider_ids"], "8")
        self.assertEqual(payload["provider"], "netflix")

    def test_read_only_progress_does_not_remove_or_persist_subscriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            items_path = Path(directory) / "discover_subscription_items.json"
            items_path.write_text(json.dumps({
                "items": [{"title": "测试电影", "media_type": "movie", "tmdb_id": "10"}],
                "last_run_at": "",
                "stats": {"total": 1, "movie": 1, "tv": 0},
            }, ensure_ascii=False), encoding="utf-8")

            def fake_enrich(data, remove_completed=False):
                result = dict(data)
                result["remove_completed"] = remove_completed
                return result

            with patch.object(discover_runtime, "SUBSCRIPTION_ITEMS_PATH", str(items_path)), patch.object(
                discover_runtime,
                "enrich_subscription_items",
                side_effect=fake_enrich,
            ), patch.object(discover_runtime, "write_subscription_items_data") as write_data:
                payload = discover_runtime.load_subscription_items(
                    with_progress=True,
                    remove_completed=False,
                    persist_progress=False,
                )

            self.assertFalse(payload["remove_completed"])
            self.assertEqual(len(payload["items"]), 1)
            write_data.assert_not_called()

    def test_organize_history_dynamic_columns_are_safely_quoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            organize_dir = root / "db" / "organize"
            organize_dir.mkdir(parents=True)
            database = organize_dir / "organize_history.db"
            connection = sqlite3.connect(database)
            connection.execute(
                'create table organize_history_records ('
                '"status" text, "tmdb_id" text, "media_type" text, '
                '"target""path" text)'
            )
            connection.execute(
                'insert into organize_history_records values (?, ?, ?, ?)',
                ("success", "42", "movie", "/library/test.strm"),
            )
            connection.commit()
            connection.close()

            with patch.object(discover_runtime, "PROJECT_ROOT", root):
                paths = discover_runtime.get_organize_library_paths("测试", "42", "movie")

            self.assertEqual(paths, ["/library/test.strm"])
            self.assertEqual(
                discover_runtime.quote_sqlite_identifier('target"path'),
                '"target""path"',
            )
            with self.assertRaises(ValueError):
                discover_runtime.quote_sqlite_identifier("bad\x00column")

    def test_media_center_subscription_persists_and_reloads_from_core_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            items_path = Path(directory) / "discover_subscription_items.json"
            item = {
                "title": "测试电影",
                "media_type": "movie",
                "tmdb_id": "100",
            }

            with patch.object(discover_runtime, "SUBSCRIPTION_ITEMS_PATH", str(items_path)), patch.object(
                discover_runtime,
                "normalize_subscription_item_metadata",
                side_effect=lambda value, resolve_tmdb=False: dict(value),
            ), patch.object(
                discover_runtime,
                "merge_cached_discover_item",
                side_effect=lambda value: dict(value),
            ), patch.object(discover_runtime, "set_discover_item_cache"), patch.object(
                discover_runtime,
                "queue_subscription_resource_rule_transfer",
                return_value={"enabled": False, "queued": 0},
            ) as queue_task, patch.object(discover_runtime, "_send_subscription_tg_notify"), patch.object(
                discover_runtime,
                "write_activity",
            ):
                saved = discover_runtime.save_subscription_item({"item": item})
                reloaded = discover_runtime.load_subscription_items()

            self.assertTrue((items_path.parent / "media_control_center.sqlite3").is_file())
            self.assertFalse(items_path.is_file())
            self.assertEqual(saved["stats"]["total"], 1)
            self.assertEqual(len(reloaded["items"]), 1)
            self.assertEqual(reloaded["items"][0]["title"], "测试电影")
            self.assertEqual(reloaded["items"][0]["tmdb_id"], "100")
            queue_task.assert_called_once()

    def test_compose_is_single_service_read_only_and_persists_all_core_directories(self):
        project_root = MODULE_ROOT.parents[1]
        compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
        env_example = (project_root / ".env.example").read_text(encoding="utf-8")

        self.assertIn("env_file:\n      - .env", compose)
        for setting in (
            "MCC_SUBSCRIPTION_SCHEDULER_ENABLED=false",
            "NASEMBY_CORE_WRITE_ENABLED=false",
            "MCC_PRIVATE_RSS_ENABLED=false",
            "MCC_TORRA_QUALITY_WATCH_ENABLED=false",
            "MCC_TORRA_REWASH_DOWNLOAD_ENABLED=false",
            "MCC_MOVIEPILOT_BACKUP_ENABLED=false",
            "MCC_PRESERVED_CORE_API_ENABLED=false",
            "TORRA_PUSH_ENABLED=false",
            "MCC_INTEGRATION_PROBE_ENABLED=false",
            "MCC_INTEGRATION_MANAGEMENT_ENABLED=false",
            "MCC_TELEGRAM_MANAGEMENT_ENABLED=false",
            "MCC_HDHIVE_MANAGEMENT_ENABLED=false",
            "MCC_CLOUD_SEARCH_ENABLED=false",
            "MCC_CLOUD_TRANSFER_ENABLED=false",
        ):
            self.assertIn(setting, env_example)
        self.assertNotIn("EMBY_BASE_URL:", compose)
        self.assertNotIn("TORRA_TOKEN:", compose)
        self.assertNotIn("\n  nasemby-core:", compose)
        self.assertIn("${MCC_DATA_ROOT:-./runtime}/data:/app/data", compose)
        self.assertIn("${MCC_DATA_ROOT:-./runtime}/db:/app/db", compose)
        self.assertIn("${MCC_DATA_ROOT:-./runtime}/upload:/app/upload", compose)
        self.assertIn("http://127.0.0.1:8987/healthz", compose)

    def test_root_container_builds_react_but_runs_only_python(self):
        dockerfile = (MODULE_ROOT.parents[1] / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM node:20-slim AS web-build", dockerfile)
        self.assertIn("FROM python:3.13-slim AS runtime", dockerfile)
        self.assertIn('CMD ["gunicorn", "--config", "app/gunicorn.conf.py", "app.main:app"]', dockerfile)
        self.assertNotIn('CMD ["npm"', dockerfile)
        self.assertNotIn("dist-server", dockerfile)

    def test_streaming_provider_reaches_tmdb_discover_query(self):
        requested_urls = []

        def fake_http_json(url, timeout=20):
            self.assertGreater(timeout, 0)
            requested_urls.append(url)
            return {"results": [], "total_results": 0, "total_pages": 1}

        with patch.object(
            discover_runtime,
            "load_tmdb_config",
            return_value={"api_key": "test-key", "api_base_url": "https://api.themoviedb.org/3"},
        ), patch.object(discover_runtime, "http_json", side_effect=fake_http_json):
            discover_runtime._fetch_tmdb_uncached({
                "type": "tv",
                "trend": "全部",
                "limit": "16",
                "_watch_provider_ids": "2303|2616",
            })

        self.assertEqual(len(requested_urls), 1)
        self.assertIn("/discover/tv?", requested_urls[0])
        self.assertIn("with_watch_providers=2303%7C2616", requested_urls[0])
        self.assertIn("watch_region=US", requested_urls[0])

    def test_container_uses_python_313(self):
        dockerfile = (MODULE_ROOT.parents[1] / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.13-slim", dockerfile)

    def test_container_uses_single_worker_gunicorn_runtime(self):
        requirements = (MODULE_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        dockerfile = (MODULE_ROOT.parents[1] / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("gunicorn>=23.0,<24.0", requirements)
        self.assertIn(
            'CMD ["gunicorn", "--config", "app/gunicorn.conf.py", "app.main:app"]',
            dockerfile,
        )
        self.assertNotIn('CMD ["python", "-m", "app.main"]', dockerfile)

    def test_gunicorn_runtime_is_single_worker_threaded_and_starts_background_runtime(self):
        config_path = MODULE_ROOT / "app" / "gunicorn.conf.py"
        spec = importlib.util.spec_from_file_location("nasemby_gunicorn_config", config_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)

        self.assertEqual(config.bind, "0.0.0.0:8987")
        self.assertEqual(config.workers, 1)
        self.assertEqual(config.worker_class, "gthread")
        self.assertEqual(config.threads, 4)
        self.assertEqual(config.timeout, 120)
        self.assertEqual(config.graceful_timeout, 30)
        self.assertEqual(config.keepalive, 5)
        self.assertFalse(config.reload)
        self.assertFalse(config.preload_app)

        from app import main

        worker = Mock()
        with patch.object(main, "start_background_runtime", return_value=["hdhive-checkin"]) as start_runtime:
            config.post_worker_init(worker)

        start_runtime.assert_called_once_with()
        worker.log.info.assert_called_once_with("background runtime started schedulers=%s", "hdhive-checkin")

    def test_background_runtime_starts_each_scheduler_once(self):
        from app import main

        started_threads = []

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                self.target = target
                self.name = name
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        flag_names = (
            "_hdhive_scheduler_started",
            "_discover_preload_started",
            "_subscription_scheduler_started",
            "_private_rss_collector_started",
            "_quality_watch_scheduler_started",
            "_background_runtime_started",
        )
        previous_flags = {name: getattr(main, name) for name in flag_names}
        try:
            for name in flag_names:
                setattr(main, name, False)
            with patch.object(main.threading, "Thread", FakeThread), patch.dict(
                main.os.environ,
                {},
                clear=True,
            ):
                started = main.start_background_runtime()
                repeated = main.start_background_runtime()
        finally:
            for name, value in previous_flags.items():
                setattr(main, name, value)

        self.assertEqual(
            [thread.name for thread in started_threads],
            ["hdhive-checkin", "discover-cache-preload"],
        )
        self.assertTrue(all(thread.daemon for thread in started_threads))
        self.assertEqual(started, ["hdhive-checkin", "discover-cache-preload"])
        self.assertEqual(repeated, [])

    def test_background_runtime_starts_subscription_scheduler_only_when_enabled(self):
        from app import main

        started_threads = []

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                self.name = name

            def start(self):
                started_threads.append(self.name)

        flag_names = (
            "_hdhive_scheduler_started",
            "_discover_preload_started",
            "_subscription_scheduler_started",
            "_private_rss_collector_started",
            "_quality_watch_scheduler_started",
            "_background_runtime_started",
        )
        previous_flags = {name: getattr(main, name) for name in flag_names}
        try:
            for name in flag_names:
                setattr(main, name, False)
            with patch.object(main.threading, "Thread", FakeThread), patch.dict(
                main.os.environ,
                {"MCC_SUBSCRIPTION_SCHEDULER_ENABLED": "true"},
                clear=True,
            ):
                started = main.start_background_runtime()
        finally:
            for name, value in previous_flags.items():
                setattr(main, name, value)

        self.assertEqual(started_threads, ["hdhive-checkin", "discover-cache-preload", "subscription-task"])
        self.assertEqual(started, started_threads)

    def test_background_runtime_starts_quality_watch_only_when_environment_gate_is_enabled(self):
        from app import main

        started_threads = []

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                self.name = name

            def start(self):
                started_threads.append(self.name)

        flag_names = (
            "_hdhive_scheduler_started",
            "_discover_preload_started",
            "_subscription_scheduler_started",
            "_private_rss_collector_started",
            "_quality_watch_scheduler_started",
            "_background_runtime_started",
        )
        previous_flags = {name: getattr(main, name) for name in flag_names}
        try:
            for name in flag_names:
                setattr(main, name, False)
            with patch.object(main.threading, "Thread", FakeThread), patch.dict(
                main.os.environ,
                {"MCC_TORRA_QUALITY_WATCH_ENABLED": "true"},
                clear=True,
            ):
                started = main.start_background_runtime()
        finally:
            for name, value in previous_flags.items():
                setattr(main, name, value)

        self.assertEqual(started_threads, ["hdhive-checkin", "discover-cache-preload", "quality-watch"])
        self.assertEqual(started, started_threads)

    def test_flask_app_factory_keeps_routes_and_does_not_start_schedulers(self):
        from app import main

        with patch.object(main, "start_background_runtime") as start_runtime, patch.object(
            main,
            "project_status",
            return_value={"ok": True, "features": ["subscriptions"]},
        ):
            first = main.create_app()
            second = main.create_app()
            response = second.test_client().get(
                "/api/status",
                headers={"X-Request-ID": "contract-request-1"},
            )

        self.assertIsNot(first, second)
        self.assertEqual(
            sorted((rule.rule, tuple(sorted(rule.methods or ()))) for rule in first.url_map.iter_rules()),
            sorted((rule.rule, tuple(sorted(rule.methods or ()))) for rule in second.url_map.iter_rules()),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "contract-request-1")
        self.assertEqual(response.headers["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0")
        start_runtime.assert_not_called()

    def test_flask_runtime_returns_request_id_and_redacted_json_errors(self):
        from app import main

        application = main.create_app()

        @application.get("/api/test/unhandled")
        def unhandled_for_test():
            raise RuntimeError("token=must-not-escape")

        with self.assertLogs("app.http_runtime", level="ERROR") as captured:
            response = application.test_client().get(
                "/api/test/unhandled",
                headers={"X-Request-ID": "../../invalid"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["code"], "INTERNAL_ERROR")
        self.assertEqual(payload["error"], "服务内部错误")
        self.assertRegex(payload["request_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(response.headers["X-Request-ID"], payload["request_id"])
        self.assertNotIn("must-not-escape", response.get_data(as_text=True))
        self.assertNotIn("must-not-escape", "\n".join(captured.output))

        not_found = application.test_client().get(
            "/api/test/missing",
            headers={"X-Request-ID": "not-found-request"},
        )
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(not_found.get_json(), {
            "code": "NOT_FOUND",
            "error": "请求的接口不存在",
            "request_id": "not-found-request",
        })

        method_not_allowed = application.test_client().post(
            "/api/status",
            headers={"X-Request-ID": "method-request"},
        )
        self.assertEqual(method_not_allowed.status_code, 405)
        self.assertEqual(method_not_allowed.get_json(), {
            "code": "METHOD_NOT_ALLOWED",
            "error": "请求方法不允许",
            "request_id": "method-request",
        })

        page_not_found = application.test_client().get("/missing-page")
        self.assertEqual(page_not_found.status_code, 404)
        self.assertEqual(page_not_found.content_type.split(";", 1)[0], "text/html")

    def test_unified_health_reports_configuration_without_credentials(self):
        from app import main

        fake_config = {
            "ENV_EMBY_SERVER_URL": "http://emby.invalid",
            "ENV_EMBY_API_KEY": "core-api-key-must-not-escape",
            "ENV_TORRA_URL": "http://torra.invalid",
            "ENV_TORRA_TOKEN": "torra-token-must-not-escape",
            "ENV_SYMEDIA_URL": "http://symedia.invalid",
            "ENV_SYMEDIA_TOKEN": "symedia-token-must-not-escape",
        }
        environment = {
            "EMBY_USER_ID": "user-id-must-not-escape",
            "QB_BASE_URL": "http://qb.invalid",
            "TMDB_API_KEY": "tmdb-key-must-not-escape",
        }
        with patch.object(main, "read_config", return_value=fake_config), patch.dict(
            main.os.environ,
            environment,
            clear=True,
        ):
            response = main.create_app().test_client().get("/api/health")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["app"], "media-control-center")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["runtime"], "python")
        self.assertEqual(
            {item["id"]: item["configured"] for item in payload["services"]},
            {
                "emby": True,
                "qbittorrent": True,
                "torra": True,
                "symedia": True,
                "subscriptions": True,
                "nasemby-core": True,
                "cloud115": False,
                "telegram": False,
                "hdhive": False,
                "moviepilot": False,
            },
        )
        serialized = response.get_data(as_text=True)
        for secret in (*fake_config.values(), *environment.values()):
            self.assertNotIn(secret, serialized)

    def test_python_access_config_and_cookie_match_express_v1_contract(self):
        from app.access_auth import AccessAuth, AccessConfig, resolve_access_config

        with self.assertRaisesRegex(ValueError, "MCC_ACCESS_KEY"):
            resolve_access_config({"NODE_ENV": "production", "MCC_ACCESS_KEY": "too-short"})
        with self.assertRaisesRegex(ValueError, "MCC_ALLOWED_ORIGINS"):
            resolve_access_config({
                "NODE_ENV": "production",
                "MCC_ACCESS_KEY": "contract-access-key-1234567890",
                "MCC_ALLOWED_ORIGINS": "https://*.example.test",
            })

        disabled = resolve_access_config({})
        self.assertFalse(disabled.enabled)
        self.assertFalse(disabled.cookie_secure)

        production = resolve_access_config({
            "NODE_ENV": "production",
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
        })
        self.assertTrue(production.enabled)
        self.assertTrue(production.cookie_secure)

        auth = AccessAuth(AccessConfig(
            enabled=True,
            access_key="contract-access-key-1234567890",
            allowed_origins=(),
            cookie_secure=False,
        ), now_ms=lambda: 1700000000000)
        token = auth.issue_token(1893456000000, nonce="AAAAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(token, "v1.1893456000000.AAAAAAAAAAAAAAAAAAAAAA.X9yZi655y5MY_ZSW2VAaz4u_FEAmAgJiwpLWbbM-hDM")
        session, invalid = auth.read_session(token)
        self.assertFalse(invalid)
        self.assertEqual(session.expires_at, 1893456000000)
        tampered, invalid = auth.read_session(f"{token}x")
        self.assertIsNone(tampered)
        self.assertTrue(invalid)
        non_canonical, invalid = auth.read_session(f"{token[:-1]}N")
        self.assertIsNone(non_canonical)
        self.assertTrue(invalid)
        expired_auth = AccessAuth(auth.config, now_ms=lambda: 1893456000001)
        expired, invalid = expired_auth.read_session(token)
        self.assertIsNone(expired)
        self.assertTrue(invalid)

    def test_python_whole_site_auth_protects_api_pages_and_react_assets(self):
        from app import main

        now = 1700000000000
        environment = {
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
            "MCC_COOKIE_SECURE": "false",
        }
        with tempfile.TemporaryDirectory() as directory:
            frontend = Path(directory)
            (frontend / "assets").mkdir()
            (frontend / "index.html").write_text("<!doctype html><title>React Contract</title>", encoding="utf-8")
            (frontend / "assets" / "app-test.js").write_text("window.contract = true;", encoding="utf-8")
            application = main.create_app(
                access_environment=environment,
                frontend_dist=frontend,
                now_ms=lambda: now,
            )
            client = application.test_client()

            self.assertEqual(client.get("/healthz").get_json(), {"status": "ok"})
            session = client.get("/api/auth/session")
            self.assertEqual(session.status_code, 200)
            self.assertEqual(session.get_json(), {
                "enabled": True,
                "authenticated": False,
                "expiresAt": None,
            })

            api_denied = client.get("/api/status")
            self.assertEqual(api_denied.status_code, 401)
            self.assertEqual(api_denied.get_json(), {"error": "需要登录", "code": "AUTH_REQUIRED"})
            for method, path, payload in (
                ("GET", "/api/tasks/chain", None),
                ("GET", "/api/media/emby/refresh-status", None),
                ("POST", "/api/qbittorrent/actions/pause", {"hashes": ["a" * 40]}),
                ("POST", "/api/media/emby/refresh", {}),
            ):
                denied = client.open(path, method=method, json=payload)
                self.assertEqual(denied.status_code, 401, path)
                self.assertEqual(denied.get_json()["code"], "AUTH_REQUIRED", path)
            page_denied = client.get("/", headers={"Accept": "text/html"})
            self.assertEqual(page_denied.status_code, 303)
            self.assertEqual(page_denied.headers["Location"], "/auth/login?next=%2F")
            asset_denied = client.get("/assets/app-test.js", headers={"Accept": "application/javascript"})
            self.assertEqual(asset_denied.status_code, 401)
            mineradio_denied = client.get(
                "/mineradio/embed",
                headers={"Accept": "text/html"},
            )
            self.assertEqual(mineradio_denied.status_code, 303)
            self.assertEqual(
                mineradio_denied.headers["Location"],
                "/auth/login?next=%2Fmineradio%2Fembed",
            )

            rejected = client.post("/auth/login", data={"access_key": "wrong", "next": "/"})
            self.assertEqual(rejected.status_code, 401)
            self.assertIn("访问密钥不正确", rejected.get_data(as_text=True))

            accepted = client.post(
                "/auth/login",
                data={"access_key": environment["MCC_ACCESS_KEY"], "next": "/tasks"},
            )
            self.assertEqual(accepted.status_code, 303)
            self.assertEqual(accepted.headers["Location"], "/tasks")
            cookie = accepted.headers["Set-Cookie"]
            self.assertIn("mcc_session=v1.", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertIn("Path=/", cookie)
            self.assertIn("Max-Age=604800", cookie)
            self.assertNotIn("; Secure", cookie)

            authenticated = client.get("/api/auth/session").get_json()
            self.assertTrue(authenticated["authenticated"])
            self.assertEqual(authenticated["expiresAt"], "2023-11-21T22:13:20.000Z")
            wrong_origin = client.post(
                "/api/media/emby/refresh",
                headers={"Origin": "https://evil.example.test"},
            )
            self.assertEqual(wrong_origin.status_code, 403)
            self.assertEqual(wrong_origin.get_json()["code"], "ORIGIN_FORBIDDEN")
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("React Contract", page.get_data(as_text=True))
            self.assertEqual(page.headers["Cache-Control"], "no-store")
            spa = client.get("/tasks")
            self.assertEqual(spa.status_code, 200)
            self.assertIn("React Contract", spa.get_data(as_text=True))
            missing_api = client.get("/api/contract-missing")
            self.assertEqual(missing_api.status_code, 404)
            self.assertEqual(missing_api.get_json()["code"], "NOT_FOUND")
            self.assertEqual(client.get("/static/app.js").status_code, 404)
            asset = client.get("/assets/app-test.js")
            self.assertEqual(asset.status_code, 200)
            self.assertEqual(asset.headers["Cache-Control"], "public, max-age=31536000, immutable")
            page.close()
            spa.close()
            asset.close()

            logout = client.post("/auth/logout")
            self.assertEqual(logout.status_code, 303)
            self.assertIn("Max-Age=0", logout.headers["Set-Cookie"])
            self.assertEqual(client.get("/api/status").status_code, 401)

    def test_python_auth_enforces_safe_next_lockout_and_origin_policy(self):
        from app import main
        from app.login_page import safe_next_location

        for unsafe in (
            "https://evil.invalid",
            "//evil.invalid",
            "/\\evil",
            "/auth/login",
            "/%61uth/login",
            "/bad\npath",
        ):
            self.assertEqual(safe_next_location(unsafe), "/")
        self.assertEqual(safe_next_location("/tasks?filter=stuck"), "/tasks?filter=stuck")

        environment = {
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
            "MCC_ALLOWED_ORIGINS": "https://media.example.test",
            "MCC_COOKIE_SECURE": "false",
        }
        application = main.create_app(access_environment=environment, now_ms=lambda: 1700000000000)
        client = application.test_client()

        for attempt in range(1, 6):
            response = client.post("/auth/login", data={"access_key": "wrong"})
            self.assertEqual(response.status_code, 429 if attempt == 5 else 401)
        self.assertEqual(response.headers["Retry-After"], "900")

        forbidden = client.post(
            "/api/status",
            headers={"Origin": "https://evil.example.test"},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.get_json(), {"error": "来源不允许", "code": "ORIGIN_FORBIDDEN"})

        preflight = client.options(
            "/api/status",
            headers={"Origin": "https://media.example.test"},
        )
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight.headers["Access-Control-Allow-Origin"], "https://media.example.test")
        self.assertEqual(preflight.headers["Access-Control-Allow-Credentials"], "true")

        safe_get = client.get(
            "/api/status",
            headers={"Origin": "https://evil.example.test"},
        )
        self.assertEqual(safe_get.status_code, 401)
        self.assertNotIn("Access-Control-Allow-Origin", safe_get.headers)

        production = main.create_app(access_environment={
            "NODE_ENV": "production",
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
        })
        proxied = production.test_client().post(
            "/auth/login",
            data={"access_key": "contract-access-key-1234567890"},
            headers={
                "Origin": "https://media.example.test",
                "X-Forwarded-For": "192.0.2.10",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "media.example.test",
            },
        )
        self.assertEqual(proxied.status_code, 303)
        self.assertIn("; Secure", proxied.headers["Set-Cookie"])

    def test_python_login_page_uses_strict_csp_and_no_external_resources(self):
        from app import main

        application = main.create_app(access_environment={
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
            "MCC_COOKIE_SECURE": "false",
        })
        response = application.test_client().get("/auth/login?next=/tasks")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("form-action 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn('type="password"', html)
        self.assertIn('name="next" type="hidden" value="/tasks"', html)


if __name__ == "__main__":
    unittest.main()
