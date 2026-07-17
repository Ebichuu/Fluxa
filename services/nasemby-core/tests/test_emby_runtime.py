from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MODULE_ROOT.parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from tests.activity_log_test_support import IsolatedActivityLogMixin


class FakeEmbyClient:
    def __init__(self, *, configured=True, fail=False):
        self.configured = configured
        self.fail = fail
        self.server_url = "http://emby.example.test"
        self.library_requests = []

    def is_configured(self):
        return self.configured

    def get_libraries(self):
        if self.fail:
            raise RuntimeError("Emby 响应异常：503")
        return [
            {
                "id": "movies",
                "name": "电影库",
                "collectionType": "movies",
                "posterUrl": "",
                "backdropUrl": "",
                "itemCount": 42,
            },
            {
                "id": "series",
                "name": "剧集库",
                "collectionType": "tvshows",
                "posterUrl": "/api/media/image/series/primary?maxWidth=780",
                "backdropUrl": "/api/media/image/series/backdrop?maxWidth=1920",
                "itemCount": 12,
            },
        ]

    def get_home_media(self, library_id=None, limit=20):
        if self.fail:
            raise RuntimeError("Emby 响应异常：503")
        self.library_requests.append((library_id, limit))
        return [
            {
                "id": "movie-1",
                "title": "测试电影",
                "year": "2026",
                "type": "Movie",
                "genres": ["剧情"],
                "rating": "8.2",
                "posterUrl": "/api/media/image/movie-1/primary?maxWidth=780",
                "backdropUrl": "/api/media/image/movie-1/backdrop?maxWidth=1920",
                "overview": "固定样本",
                "sourceName": "Emby",
            }
        ]

    def get_counts(self):
        if self.fail:
            raise RuntimeError("Emby 响应异常：503")
        return {"movies": 4030, "series": 3006, "episodes": 88000}

    def get_recent_items(self, limit=8):
        if self.fail:
            raise RuntimeError("Emby 响应异常：503")
        self.recent_limit = limit
        return [
            {
                "id": "episode-1",
                "title": "第一集",
                "type": "Episode",
                "seriesName": "测试剧集",
                "dateCreated": "2026-07-16T08:00:00.000Z",
            }
        ]

    def fetch_image(self, item_id, image_type, max_width):
        self.image_request = (item_id, image_type, max_width)
        return self.image_result


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"", content_type="application/json"):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = {"Content-Type": content_type}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


class EmbyRuntimeContractTests(IsolatedActivityLogMixin, unittest.TestCase):
    def test_unconfigured_home_keeps_existing_sample_contract(self):
        from app import main
        from app.fallback_media import FALLBACK_MEDIA

        self.assertEqual([item["id"] for item in FALLBACK_MEDIA], [
            "sample-dune-part-two",
            "sample-blade-runner-2049",
            "sample-interstellar",
            "sample-arrival",
            "sample-oppenheimer",
            "sample-the-batman",
            "sample-inception",
            "sample-mad-max-fury-road",
            "sample-severance",
        ])

        application = main.create_app(access_environment={})
        client = application.test_client()
        routes = {
            (rule.rule, method)
            for rule in application.url_map.iter_rules()
            for method in (rule.methods or set())
        }

        home = client.get("/api/media/home").get_json()
        overview = client.get("/api/media/emby/overview").get_json()
        refresh_status = client.get("/api/media/emby/refresh-status").get_json()
        refresh_response = client.post("/api/media/emby/refresh")

        self.assertEqual(home["source"], "sample")
        self.assertFalse(home["configured"])
        self.assertEqual(home["activeLibraryId"], "sample-library-movies")
        self.assertEqual(len(home["libraries"]), 2)
        self.assertEqual(len(home["items"]), 8)
        self.assertEqual(home["items"][0]["id"], "sample-dune-part-two")
        self.assertEqual(overview, {"configured": False})
        self.assertEqual(refresh_status["state"], "service_unavailable")
        self.assertFalse(refresh_status["canRefresh"])
        self.assertEqual(refresh_response.status_code, 503)
        self.assertEqual(refresh_response.get_json()["code"], "EMBY_REFRESH_UNAVAILABLE")
        for path in (
            "/api/media/home",
            "/api/media/emby/overview",
            "/api/media/external-image",
            "/api/media/image/<item_id>/<image_type>",
        ):
            self.assertIn((path, "GET"), routes)
        self.assertIn(("/api/media/emby/refresh-status", "GET"), routes)
        self.assertIn(("/api/media/emby/refresh", "POST"), routes)

    def test_environment_overrides_legacy_emby_config(self):
        from app.emby_runtime import resolve_emby_config

        with patch.dict(os.environ, {}, clear=True), patch(
            "app.emby_runtime.read_config",
            return_value={
                "ENV_EMBY_SERVER_URL": "http://legacy-emby.example.test",
                "ENV_EMBY_API_KEY": "legacy-key",
            },
        ):
            legacy = resolve_emby_config(os.environ)

        self.assertEqual(legacy.base_url, "http://legacy-emby.example.test")
        self.assertEqual(legacy.api_key, "legacy-key")

        config = resolve_emby_config(
            {
                "EMBY_BASE_URL": "http://new-emby.example.test",
                "EMBY_API_KEY": "new-key",
                "EMBY_USER_ID": "new-user",
            },
            runtime_config={
                "ENV_EMBY_SERVER_URL": "http://legacy-emby.example.test",
                "ENV_EMBY_API_KEY": "legacy-key",
            },
        )

        self.assertEqual(config.base_url, "http://new-emby.example.test")
        self.assertEqual(config.api_key, "new-key")
        self.assertEqual(config.user_id, "new-user")

    def test_home_and_overview_keep_react_field_contract(self):
        from app import main

        fake = FakeEmbyClient()
        application = main.create_app(
            access_environment={},
            emby_client_factory=lambda _config: fake,
            emby_clock=lambda: datetime(2026, 7, 16, 9, 30, tzinfo=timezone.utc),
        )
        client = application.test_client()

        home = client.get("/api/media/home?libraryId=movies").get_json()
        overview = client.get("/api/media/emby/overview").get_json()

        self.assertEqual(home["source"], "emby")
        self.assertTrue(home["configured"])
        self.assertEqual(home["activeLibraryId"], "movies")
        self.assertEqual(home["items"][0]["libraryId"], "movies")
        self.assertEqual(home["items"][0]["libraryName"], "电影库")
        self.assertEqual(home["libraries"][0]["posterUrl"], home["items"][0]["posterUrl"])
        self.assertEqual(fake.library_requests, [("movies", 20)])
        self.assertEqual(overview, {
            "configured": True,
            "connected": True,
            "counts": {"movies": 4030, "series": 3006, "episodes": 88000},
            "recent": [{
                "id": "episode-1",
                "title": "第一集",
                "type": "Episode",
                "seriesName": "测试剧集",
                "dateCreated": "2026-07-16T08:00:00.000Z",
            }],
            "serverUrl": "http://emby.example.test",
            "lastCheckedAt": "2026-07-16T09:30:00.000Z",
        })

    def test_configured_offline_home_and_overview_do_not_fake_connection(self):
        from app import main

        fake = FakeEmbyClient(fail=True)
        client = main.create_app(
            access_environment={},
            emby_client_factory=lambda _config: fake,
        ).test_client()

        home = client.get("/api/media/home?libraryId=movies").get_json()
        overview = client.get("/api/media/emby/overview").get_json()

        self.assertEqual(home["source"], "sample")
        self.assertTrue(home["configured"])
        self.assertIn("503", home["error"])
        self.assertTrue(overview["configured"])
        self.assertFalse(overview["connected"])
        self.assertIn("503", overview["error"])

    def test_emby_client_maps_views_items_counts_recent_and_tmdb_index(self):
        from app.emby_runtime import EmbyClient, EmbyConfig

        session = FakeSession([
            FakeResponse(payload={"Items": [{
                "Id": "movies",
                "Name": "电影库",
                "CollectionType": "movies",
                "RecursiveItemCount": 42,
                "ImageTags": {"Primary": "tag"},
                "BackdropImageTags": ["backdrop"],
            }]}),
            FakeResponse(payload={"Items": [{
                "Id": "movie-1",
                "Name": "测试电影",
                "Type": "Movie",
                "ProductionYear": 2026,
                "Genres": ["剧情", "悬疑", "动作", "多余"],
                "CommunityRating": 8.25,
                "ImageTags": {"Primary": "tag"},
                "BackdropImageTags": ["backdrop"],
            }]}),
            FakeResponse(payload={"MovieCount": 4, "SeriesCount": 3, "EpisodeCount": 20}),
            FakeResponse(payload={"Items": [{
                "Id": "episode-1",
                "Name": "第一集",
                "Type": "Episode",
                "SeriesName": "测试剧集",
                "DateCreated": "2026-07-16T08:00:00.000Z",
            }]}),
            FakeResponse(payload={"Items": [
                {"Id": "movie-1", "Type": "Movie", "ProviderIds": {"Tmdb": "100"}, "Path": "/movies/a.mkv"},
                {"Id": "series-1", "Type": "Series", "ProviderIds": {"tmdb": "200"}, "Path": "/series/a"},
                {"Id": "missing", "Type": "Movie", "ProviderIds": {"Tmdb": "300"}},
            ]}),
        ])
        client = EmbyClient(EmbyConfig(
            base_url="http://emby.example.test",
            api_key="api-key",
            user_id="user-id",
        ), session=session)

        libraries = client.get_libraries()
        items = client.get_home_media("movies")
        counts = client.get_counts()
        recent = client.get_recent_items(8)
        index = client.get_tmdb_library_index()

        self.assertEqual(libraries[0]["posterUrl"], "/api/media/image/movies/primary?maxWidth=780")
        self.assertEqual(items[0]["genres"], ["剧情", "悬疑", "动作"])
        self.assertEqual(items[0]["rating"], "8.3")
        self.assertEqual(counts, {"movies": 4, "series": 3, "episodes": 20})
        self.assertEqual(recent[0]["seriesName"], "测试剧集")
        self.assertEqual(index, {"movies": {"100"}, "series": {"200"}})
        self.assertEqual(len(session.requests), 5)
        for _method, url, _kwargs in session.requests:
            self.assertIn("api_key=api-key", url)
            self.assertNotIn("api-key", str(_kwargs))

    def test_library_refresh_posts_once_and_clears_lookup_cache(self):
        from urllib.parse import parse_qs, urlsplit

        from app.emby_runtime import EmbyClient, EmbyConfig

        session = FakeSession([FakeResponse(status_code=204)])
        client = EmbyClient(
            EmbyConfig(
                base_url="http://emby.example.test",
                api_key="test-api-key",
                user_id="user-1",
            ),
            session=session,
        )
        client._library_cache["tmdb-library-index"] = (
            9999999999,
            {"movies": {"10"}},
        )

        client.trigger_library_refresh()

        method, url, request_options = session.requests[0]
        parsed = urlsplit(url)
        self.assertEqual(method, "POST")
        self.assertEqual(parsed.path, "/Library/Refresh")
        self.assertEqual(parse_qs(parsed.query), {"api_key": ["test-api-key"]})
        self.assertEqual(request_options["timeout"], 12)
        self.assertEqual(client._library_cache, {})

    def test_password_authentication_retries_one_unauthorized_read(self):
        from app.emby_runtime import EmbyClient, EmbyConfig

        session = FakeSession([
            FakeResponse(payload={"AccessToken": "token-one", "User": {"Id": "user-one"}}),
            FakeResponse(status_code=401, payload={"secret": "must-not-escape"}),
            FakeResponse(payload={"AccessToken": "token-two", "User": {"Id": "user-two"}}),
            FakeResponse(payload={"MovieCount": 1, "SeriesCount": 2, "EpisodeCount": 3}),
        ])
        client = EmbyClient(EmbyConfig(
            base_url="http://emby.example.test",
            username="test-user",
            password="test-password",
        ), session=session)

        self.assertEqual(client.get_counts(), {"movies": 1, "series": 2, "episodes": 3})
        self.assertEqual([item[0] for item in session.requests], ["POST", "GET", "POST", "GET"])
        self.assertEqual(session.requests[0][2]["json"], {
            "Username": "test-user",
            "Pw": "test-password",
        })
        self.assertIn("api_key=token-one", session.requests[1][1])
        self.assertIn("api_key=token-two", session.requests[3][1])
        self.assertNotIn("test-password", " ".join(item[1] for item in session.requests))

    def test_network_errors_do_not_expose_api_key(self):
        import requests

        from app.emby_runtime import EmbyClient, EmbyConfig

        session = Mock()
        session.request.side_effect = requests.ConnectionError(
            "failed https://emby.invalid/Items/Counts?api_key=must-not-escape"
        )
        client = EmbyClient(EmbyConfig(
            base_url="https://emby.invalid",
            api_key="must-not-escape",
            user_id="user-id",
        ), session=session)

        with self.assertRaisesRegex(RuntimeError, "^Emby 请求失败$") as captured:
            client.get_counts()
        self.assertNotIn("must-not-escape", str(captured.exception))

    def test_default_external_fetch_rejects_private_dns_resolution(self):
        from app.emby_runtime import fetch_external_image

        with patch(
            "app.emby_runtime.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
        ), patch("app.emby_runtime.requests.get") as get:
            with self.assertRaisesRegex(ValueError, "不允许访问内网"):
                fetch_external_image("https://images.example.test/poster.jpg")
        get.assert_not_called()

    def test_image_routes_validate_sources_magic_and_proxy_contract(self):
        from app import main
        from app.emby_runtime import ImageFetchResult

        fake = FakeEmbyClient()
        fake.image_result = ImageFetchResult(
            status=200,
            content=b"\xff\xd8\xff" + b"x" * 20,
            content_type="image/jpeg",
        )
        external_results = iter((
            ImageFetchResult(status=404, content=b"", content_type=""),
            ImageFetchResult(status=200, content=b"not-an-image", content_type="text/plain"),
            ImageFetchResult(status=200, content=b"\x89PNG" + b"x" * 20, content_type="image/png"),
        ))
        application = main.create_app(
            access_environment={},
            emby_client_factory=lambda _config: fake,
            external_image_fetcher=lambda _url: next(external_results),
        )
        client = application.test_client()

        self.assertEqual(client.get(
            "/api/media/external-image?src=http://127.0.0.1/private.jpg"
        ).status_code, 400)
        placeholder = client.get(
            "/api/media/external-image?src=https://images.example.test/missing.jpg"
        )
        non_image = client.get(
            "/api/media/external-image?src=https://images.example.test/not-image"
        )
        external = client.get(
            "/api/media/external-image?src=https://images.example.test/poster.png"
        )
        emby = client.get("/api/media/image/movie%201/backdrop?maxWidth=1920")

        self.assertEqual(placeholder.status_code, 200)
        self.assertEqual(placeholder.content_type.split(";", 1)[0], "image/svg+xml")
        self.assertEqual(non_image.status_code, 204)
        self.assertEqual(external.status_code, 200)
        self.assertEqual(external.content_type, "image/png")
        self.assertEqual(emby.status_code, 200)
        self.assertEqual(emby.content_type, "image/jpeg")
        self.assertEqual(fake.image_request, ("movie 1", "Backdrop", "1920"))


if __name__ == "__main__":
    unittest.main()
