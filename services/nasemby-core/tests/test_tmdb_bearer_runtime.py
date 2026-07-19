import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import discover_runtime


class TmdbBearerRuntimeTest(unittest.TestCase):
    def tearDown(self):
        discover_runtime.TMDB_CONFIG = None

    def test_v4_token_replaces_query_api_key(self):
        module = SimpleNamespace(
            resolve_tmdb_api_key=lambda: "legacy-key",
            resolve_tmdb_api_base_url=lambda: "https://api.themoviedb.org/3",
            resolve_tmdb_image_base_url=lambda: "https://image.tmdb.org/t/p",
        )
        loader = MagicMock()
        loader.load_module.return_value = module
        discover_runtime.TMDB_CONFIG = None

        with patch.dict(os.environ, {"TMDB_API_TOKEN": "v4-token"}, clear=True), patch(
            "app.config.load_runtime_env"
        ), patch.object(discover_runtime, "SourcelessFileLoader", return_value=loader):
            config = discover_runtime.load_tmdb_config()

        self.assertEqual(config["api_token"], "v4-token")
        self.assertEqual(config["api_key"], "")

    def test_bearer_header_is_limited_to_tmdb_base_url(self):
        discover_runtime.TMDB_CONFIG = {
            "api_key": "",
            "api_token": "v4-token",
            "api_base_url": "https://api.themoviedb.org/3",
        }
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok": true}'

        with patch.object(discover_runtime.urllib.request, "urlopen", return_value=response) as urlopen:
            discover_runtime.http_json("https://api.themoviedb.org/3/configuration")
            tmdb_request = urlopen.call_args.args[0]
            discover_runtime.http_json("https://example.test/status")
            other_request = urlopen.call_args.args[0]

        self.assertEqual(tmdb_request.get_header("Authorization"), "Bearer v4-token")
        self.assertIsNone(other_request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
