import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import discover_runtime
from app import activity_log
from app.contract_mapping import map_calendar_payload, map_subscription_detail, sanitize_resource_payload
from app.main import create_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "docs" / "contracts" / "http-api-contract-v1.json"


class FakeTorraClient:
    def __init__(self, _config):
        self.pushes = []

    def is_configured(self):
        return True

    def inspect_duplicate(self, _target):
        return {"checked": True, "found": False, "subscriptionId": "", "name": ""}

    def get_summary(self):
        return {
            "configured": True,
            "connected": True,
            "webUrl": "",
            "lastCheckedAt": "2026-07-17T00:00:00.000Z",
            "counts": {"total": 0, "active": 0, "completed": 0, "running": 0},
        }

    def list_subscriptions(self):
        return []

    def push_subscription(self, payload):
        self.pushes.append(payload)
        return {
            "success": True,
            "pushed": True,
            "alreadyExists": False,
            "searchTriggered": True,
            "subscriptionId": payload["id"],
            "message": "已推送",
        }


def contract_path_matches(rule, contract_path):
    rule_parts = rule.strip("/").split("/")
    contract_parts = contract_path.strip("/").split("/")
    if len(rule_parts) != len(contract_parts):
        return False
    for left, right in zip(rule_parts, contract_parts):
        if left.startswith("<") and left.endswith(">"):
            continue
        if right.startswith(":"):
            continue
        if left != right:
            return False
    return True


def concrete_path(path):
    replacements = {
        ":itemId": "item-1",
        ":imageType": "Primary",
        ":id": "subscription-1",
        ":source": "tmdb",
    }
    for key, value in replacements.items():
        path = path.replace(key, value)
    return path


class MccCompatibilityContractTests(unittest.TestCase):
    def test_all_47_frozen_routes_exist_on_python_and_compat_routes_win(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        app = create_app(access_environment={})
        rules = list(app.url_map.iter_rules())
        self.assertEqual(contract["routeCount"], 47)
        for route in contract["routes"]:
            matches = [rule for rule in rules if route["method"] in (rule.methods or ()) and contract_path_matches(rule.rule, route["path"])]
            self.assertTrue(matches, f"missing {route['method']} {route['path']}")
            if route["migrationGroup"] in {"subscriptions", "discover", "nasemby-internal"}:
                self.assertTrue(matches[0].endpoint.startswith("mcc_compat_"), route["path"])

    def test_react_api_references_are_present_in_client_contract(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        source = (REPOSITORY_ROOT / "src" / "services" / "api.ts").read_text(encoding="utf-8")
        references = re.findall(r"['\"`](/(?:api|auth)/[^'\"`?$\s]*)", source)
        client_paths = [route["path"] for route in contract["routes"] if route["client"]]
        self.assertTrue(references)
        for reference in references:
            self.assertTrue(
                any(path == reference or path.startswith(reference) for path in client_paths),
                f"React API 路径未进入 client 契约：{reference}",
            )

    def test_all_protected_contract_routes_require_session_before_business_logic(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        app = create_app(access_environment={
            "MCC_ACCESS_KEY": "contract-access-key-1234567890",
            "MCC_COOKIE_SECURE": "false",
        })
        client = app.test_client()
        for route in contract["routes"]:
            if route["auth"] != "session":
                continue
            response = client.open(
                concrete_path(route["path"]),
                method=route["method"],
                json={} if route["method"] in {"POST", "PATCH", "PUT", "DELETE"} else None,
            )
            self.assertEqual(response.status_code, 401, f"{route['method']} {route['path']}")
            self.assertEqual(response.get_json().get("code"), "AUTH_REQUIRED", route["path"])

    def test_all_protected_writes_reject_untrusted_origin(self):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        access_key = "contract-access-key-1234567890"
        app = create_app(access_environment={
            "MCC_ACCESS_KEY": access_key,
            "MCC_COOKIE_SECURE": "false",
        })
        client = app.test_client()
        self.assertEqual(client.post("/auth/login", data={"access_key": access_key}).status_code, 303)
        for route in contract["routes"]:
            if route["auth"] != "session" or route["effect"] != "write":
                continue
            response = client.open(
                concrete_path(route["path"]),
                method=route["method"],
                headers={"Origin": "https://evil.example.test"},
                json={},
            )
            self.assertEqual(response.status_code, 403, f"{route['method']} {route['path']}")
            self.assertEqual(response.get_json().get("code"), "ORIGIN_FORBIDDEN", route["path"])

    def test_preserved_core_endpoints_are_registered_but_disabled_by_default(self):
        client = create_app(access_environment={}).test_client()
        legacy_static = client.get("/static/app.js")
        self.assertEqual(legacy_static.status_code, 404)
        self.assertNotIn("innerHTML", legacy_static.get_data(as_text=True))
        for method, path in (
            ("POST", "/api/115/transfer"),
            ("POST", "/api/telegram/send-code"),
            ("POST", "/api/hdhive/checkin"),
            ("POST", "/api/discover/cache/preload"),
            ("POST", "/api/torra/subscribe"),
        ):
            response = client.open(path, method=method, json={})
            self.assertEqual(response.status_code, 503, path)
            self.assertEqual(response.get_json()["code"], "PRESERVED_CORE_API_DISABLED", path)

    def test_discover_routes_map_nasemby_payload_to_react_contract(self):
        app = create_app(access_environment={})
        sample = {
            "success": True,
            "source": "TMDB",
            "items": [{
                "id": 101,
                "title": "测试电影",
                "media_type": "movie",
                "year": "2026",
                "poster_url": "https://image.example/poster.jpg",
                "rating": 8.2,
                "original_language": "zh",
                "genre_ids": [18],
                "origin_country": ["CN"],
            }],
            "page": 1,
            "total_pages": 2,
            "total_results": 20,
            "has_next": True,
            "has_prev": False,
        }
        with patch.object(discover_runtime, "fetch_tmdb", return_value=sample):
            payload = app.test_client().get("/api/discover/browse?source=tmdb&type=movie").get_json()
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["totalPages"], 2)
        self.assertEqual(payload["results"][0], {
            "id": 101,
            "mediaType": "movie",
            "title": "测试电影",
            "year": "2026",
            "posterUrl": "https://image.example/poster.jpg",
            "overview": "",
            "rating": 8.2,
            "originalLanguage": "zh",
            "genreIds": [18],
            "originCountry": ["CN"],
            "source": "tmdb",
            "sourceLabel": "TMDB",
            "sourceId": "101",
            "tmdbId": "101",
        })

    def test_detail_calendar_and_resource_payloads_use_react_whitelists(self):
        detail = map_subscription_detail({
            "success": True,
            "item": {"key": "tv:test:tmdb:202:season:1", "title": "测试剧", "media_type": "tv", "tmdb_id": "202"},
            "detail": {
                "title": "测试剧",
                "tmdb_id": "202",
                "cast": [{"name": "演员甲", "character": "角色甲", "profile_url": "/profile.jpg", "token": "secret"}],
                "library_paths": ["/library/test"],
                "token": "secret",
            },
            "seasons": [{
                "season_number": 1,
                "name": "第一季",
                "episodes": [{
                    "episode_number": 1,
                    "title": "第一集",
                    "in_library": True,
                    "library_paths": ["/library/test/S01E01.strm"],
                }],
            }],
        })
        self.assertEqual(detail["detail"]["cast"][0]["name"], "演员甲")
        self.assertEqual(detail["seasons"][0]["episodes"][0]["episodeNumber"], 1)
        self.assertNotIn("secret", json.dumps(detail, ensure_ascii=False))

        calendar = map_calendar_payload({
            "success": True,
            "year": 2026,
            "month": 7,
            "type": "tv",
            "entries": [{
                "date": "2026-07-17",
                "key": "tv:test",
                "title": "测试剧",
                "media_type": "tv",
                "season_number": 1,
                "episode_number": 2,
                "episode_label": "S01E02",
                "in_library": False,
            }],
            "stats": {"entries": 1, "titles": 1, "in_library": 0, "pending": 1},
            "errors": [],
        })
        self.assertEqual(calendar["calendar"]["entries"][0]["episodeLabel"], "S01E02")
        self.assertEqual(calendar["calendar"]["stats"]["pending"], 1)

        resources = sanitize_resource_payload({
            "success": True,
            "title": "测试剧",
            "media_type": "tv",
            "items": [{"title": "资源一", "share_url": "https://share.example/1", "cookie": "secret"}],
            "sources": [{"key": "pansou", "label": "频道", "count": 1}],
            "seasons": [{"season": "1", "episodes": [1, 2]}],
            "errors": [{"raw": "secret"}],
        })
        self.assertEqual(resources["items"][0]["title"], "资源一")
        self.assertEqual(resources["errors"], ["部分资源来源当前不可用"])
        self.assertNotIn("secret", json.dumps(resources, ensure_ascii=False))

    def test_subscription_routes_use_one_core_ledger_and_direct_safe_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items_path = root / "discover_subscription_items.json"
            config_path = root / "discover_subscriptions.json"
            log_path = root / "activity_log.jsonl"
            config_path.write_text(json.dumps({
                "mode": "resource",
                "douban": {
                    "enabled": False,
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
            }, ensure_ascii=False), encoding="utf-8")
            environment = {
                "NASEMBY_CORE_WRITE_ENABLED": "true",
                "TORRA_PUSH_ENABLED": "false",
                "TORRA_DOWNLOADER_ID": "downloader-1",
                "TORRA_DOWNLOAD_ROOT": "/downloads/torra",
            }
            with patch.object(discover_runtime, "SUBSCRIPTION_ITEMS_PATH", str(items_path)), patch.object(
                discover_runtime, "SUBSCRIPTION_CONFIG_PATH", str(config_path)
            ), patch.object(activity_log, "LOG_PATH", log_path):
                app = create_app(access_environment=environment, torra_client_factory=FakeTorraClient)
                client = app.test_client()
                saved = client.post("/api/subscriptions/save", json={
                    "title": "测试剧集",
                    "mediaType": "tv",
                    "tmdbId": "202",
                    "year": "2026",
                    "seasonNumber": 1,
                    "seasonName": "第 1 季",
                    "originalLanguage": "ja",
                    "genreIds": [16],
                    "originCountry": ["JP"],
                })
                self.assertEqual(saved.status_code, 200)
                key = saved.get_json()["item"]["id"]
                listed = client.get("/api/subscriptions/items").get_json()
                self.assertEqual(len(listed["subscriptions"]["items"]), 1)
                self.assertEqual(listed["subscriptions"]["items"][0]["tmdbId"], "202")

                category = client.patch(f"/api/subscriptions/{key}/category", json={"category": "anime_jp"})
                self.assertEqual(category.status_code, 200)
                self.assertEqual(category.get_json()["item"]["mediaCategory"], "anime_jp")
                season = client.post("/api/subscriptions/season", json={"id": key, "seasonNumber": 2})
                self.assertEqual(season.status_code, 200)
                self.assertEqual(season.get_json()["item"]["seasonNumber"], 2)
                preview = client.get(f"/api/subscriptions/push-preview?id={key}").get_json()["preview"]
                self.assertEqual(preview["savePath"], "/downloads/torra/00-日漫")
                self.assertEqual(preview["payload"]["downloader_id"], "downloader-1")
                self.assertIn("TORRA_PUSH_ENABLED 当前关闭", preview["blockers"])

                environment["TORRA_PUSH_ENABLED"] = "true"
                pushed = client.post("/api/subscriptions/push", json={"id": key})
                self.assertEqual(pushed.status_code, 200)
                self.assertTrue(pushed.get_json()["pushed"])
                self.assertEqual(app.extensions["mcc_torra_client"].pushes[0]["save_path"], "/downloads/torra/00-日漫")

                stored = json.loads(items_path.read_text(encoding="utf-8"))["items"]
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0]["media_category"], "anime_jp")
                self.assertEqual(stored[0]["target_season"], 2)

    def test_deployment_defaults_block_subscription_writes_and_pushes(self):
        app = create_app(access_environment={}, torra_client_factory=FakeTorraClient)
        client = app.test_client()
        for method, path, body in (
            ("POST", "/api/subscriptions/save", {"title": "x", "tmdbId": "1", "mediaType": "movie"}),
            ("PATCH", "/api/subscriptions/x/category", {"category": "movie"}),
            ("POST", "/api/subscriptions/run", {}),
            ("POST", "/api/subscriptions/push", {"id": "x"}),
        ):
            response = client.open(path, method=method, json=body)
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.get_json()["code"], "NASEMBY_CORE_WRITE_DISABLED", path)


if __name__ == "__main__":
    unittest.main()
