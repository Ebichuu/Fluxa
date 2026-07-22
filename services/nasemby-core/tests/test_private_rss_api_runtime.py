from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.main import create_app
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository


class FakeCollector:
    def fetch_source(self, source_id, persist=False):
        return {"status": "success", "items": 2, "title": "测试 RSS"}


class PrivateRssApiRuntimeTests(unittest.TestCase):
    def test_crud_is_local_and_collection_test_has_separate_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            action_repository = QualityWatchRepository(Path(directory) / "media_control_center.sqlite3")
            environment = {"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"}
            app = create_app(
                access_environment=environment,
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=action_repository,
            )
            client = app.test_client()
            self.assertIn("mcc_rss_subscription_match_runtime", app.extensions)
            created = client.post("/api/v2/rss-sources", json={
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=secret-value",
                "intervalMinutes": 30,
                "retentionDays": 7,
            })
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.get_json()["intervalMinutes"], 30)
            source_id = created.get_json()["id"]
            self.assertNotIn("secret-value", created.get_data(as_text=True))
            detail = client.get(created.headers["Location"])
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.get_json()["id"], source_id)
            self.assertNotIn("secret-value", detail.get_data(as_text=True))
            self.assertEqual(client.get("/api/v2/rss-sources").get_json()["summary"]["sources"], 1)
            repository.upsert_items(source_id, [{"fingerprint": "match-one", "title": "测试条目"}])
            item_id = repository.search_items()["items"][0]["id"]
            item_detail = client.get(f"/api/v2/rss-items/{item_id}")
            self.assertEqual(item_detail.status_code, 200)
            self.assertEqual(item_detail.get_json()["identityStatus"], "unidentified")
            self.assertNotIn("download_url", item_detail.get_data(as_text=True))
            self.assertNotIn("detail_url", item_detail.get_data(as_text=True))
            self.assertEqual(client.get("/api/v2/rss-items?identityStatus=unidentified").get_json()["total"], 1)
            self.assertEqual(client.get("/api/v2/rss-items?identityStatus=invalid").status_code, 422)
            self.assertEqual(client.post("/api/v2/rss-items/identity-backfills", json={"limit": 201}).status_code, 422)
            repository.upsert_items(source_id, [{
                "fingerprint": "history-imdb",
                "title": "History Movie 2024",
                "description": "https://www.imdb.com/title/tt7654321/",
                "media_type": "movie",
            }])
            backfill = client.post("/api/v2/rss-items/identity-backfills", json={"limit": 50})
            self.assertEqual(backfill.status_code, 200)
            self.assertEqual(backfill.get_json()["identified"], 1)
            self.assertEqual(repository.search_items(query="History Movie")["items"][0]["imdbId"], "tt7654321")
            repository.create_match(item_id, "tv:202:s1", "tv:202:s1:s1:e1", {"identity": {"basis": "title"}})
            listed_matches = client.get("/api/v2/rss-matches?status=candidate").get_json()
            self.assertEqual(listed_matches["total"], 1)
            self.assertEqual(listed_matches["items"][0]["unitId"], "tv:202:s1:s1:e1")
            disabled = client.post(f"/api/v2/rss-sources/{source_id}/tests")
            self.assertEqual(disabled.status_code, 503)
            environment["MCC_PRIVATE_RSS_ENABLED"] = "true"
            accepted = client.post(f"/api/v2/rss-sources/{source_id}/tests")
            self.assertEqual(accepted.status_code, 202)
            self.assertTrue(accepted.headers["Location"].startswith("/api/v2/automation-actions/"))
            action = client.get(accepted.headers["Location"])
            self.assertEqual(action.get_json()["status"], "succeeded")
            self.assertEqual(action.get_json()["result"]["items"], 2)
            deleted = client.delete(f"/api/v2/rss-sources/{source_id}")
            self.assertEqual(deleted.status_code, 204)

    def test_rss_items_accept_target_identity_and_scope_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            app = create_app(
                access_environment={"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"},
                private_rss_repository=repository,
                private_rss_collector=FakeCollector(),
                quality_watch_repository=QualityWatchRepository(Path(directory) / "media_control_center.sqlite3"),
            )
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "target", "title": "Unrelated English Name S01E01", "tmdb_id": "279323",
                "identity_status": "identified", "media_type": "tv", "season_number": 1,
            }])

            response = app.test_client().get(
                "/api/v2/rss-items?query=%E9%AC%BC%E8%B0%9C%E4%B8%9C%E5%AE%AB"
                "&tmdbId=279323&mediaType=tv&seasonNumber=1&year=2026"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["total"], 1)
            self.assertEqual(response.get_json()["items"][0]["matchMethod"], "tmdb_exact")

            self.assertEqual(app.test_client().get("/api/v2/rss-items?mediaType=invalid").status_code, 422)
            self.assertEqual(app.test_client().get("/api/v2/rss-items?seasonNumber=bad").status_code, 422)


if __name__ == "__main__":
    unittest.main()
