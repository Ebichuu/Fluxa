from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.private_rss_api_runtime import register_private_rss
from app.private_rss_parser import parse_private_feed
from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository


RSS_ACCEPTANCE_SAMPLE = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="https://example.invalid/media"><channel><title>Identity Acceptance</title>
<item><title>Field Movie 2026</title><guid>field-movie</guid><category>Movie</category>
<media:tmdb_id>111</media:tmdb_id></item>
<item><title>IMDb Movie 2025</title><guid>imdb-movie</guid><category>Movie</category>
<description><![CDATA[Public reference: https://www.imdb.com/title/tt2222222/]]></description></item>
<item><title>Unique Show S01E02 1080p</title><guid>unique-show</guid><category>TV</category></item>
<item><title>Conflict Show S01E01 2160p</title><guid>conflict-show</guid><category>TV</category></item>
</channel></rss>'''


class RssIdentityAcceptanceTests(unittest.TestCase):
    def test_parser_repository_backfill_and_api_complete_identity_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "media_control_center.sqlite3"
            repository = PrivateRssRepository(database_path)
            source = repository.save_source({"name": "验收站", "feedUrl": "https://tracker.example/rss"})
            parsed = parse_private_feed(RSS_ACCEPTANCE_SAMPLE)
            repository.upsert_items(source["id"], parsed["items"])

            subscriptions = [{
                "key": "tv:unique:s1",
                "title": "Unique Show",
                "media_type": "tv",
                "tmdb_id": "333",
                "target_season": 1,
            }, {
                "key": "tv:conflict-a:s1",
                "title": "Conflict Show",
                "media_type": "tv",
                "tmdb_id": "444",
                "target_season": 1,
            }, {
                "key": "tv:conflict-b:s1",
                "title": "Conflict Show",
                "media_type": "tv",
                "tmdb_id": "445",
                "target_season": 1,
            }]
            app = Flask(__name__)
            app.extensions["mcc_quality_watch_repository"] = QualityWatchRepository(database_path)
            register_private_rss(
                app,
                database_path,
                environment={"NASEMBY_CORE_WRITE_ENABLED": "true", "MCC_PRIVATE_RSS_ENABLED": "false"},
                repository=repository,
                subscription_loader=lambda: subscriptions,
            )
            client = app.test_client()

            backfill = client.post("/api/v2/rss-items/identity-backfills", json={"limit": 50})
            self.assertEqual(backfill.status_code, 200)
            self.assertEqual(backfill.get_json()["scanned"], 2)
            self.assertEqual(backfill.get_json()["identified"], 1)
            self.assertEqual(backfill.get_json()["conflicts"], 1)

            field = repository.search_items(query="Field Movie")["items"][0]
            imdb = repository.search_items(query="IMDb Movie")["items"][0]
            unique = repository.search_items(query="Unique Show")["items"][0]
            conflict = repository.search_items(query="Conflict Show")["items"][0]

            self.assertEqual((field["tmdbId"], field["identitySource"]), ("111", "rss_field"))
            self.assertEqual((imdb["imdbId"], imdb["identitySource"]), ("tt2222222", "rss_description"))
            self.assertEqual((unique["tmdbId"], unique["identitySource"]), ("333", "subscription_match"))
            self.assertEqual(conflict["identityStatus"], "conflict")
            self.assertEqual(conflict["tmdbId"], "")

            exact = client.get(
                "/api/v2/rss-items?query=Different%20Title&tmdbId=111&mediaType=movie&year=2026"
            )
            self.assertEqual(exact.status_code, 200)
            self.assertEqual(exact.get_json()["items"][0]["matchMethod"], "tmdb_exact")

            summary = client.get("/api/v2/rss-sources").get_json()["summary"]
            self.assertTrue(summary["identityBackfillRan"])
            self.assertEqual(summary["lastIdentityBackfillIdentified"], 1)
            self.assertEqual(summary["lastIdentityBackfillConflicts"], 1)


if __name__ == "__main__":
    unittest.main()
