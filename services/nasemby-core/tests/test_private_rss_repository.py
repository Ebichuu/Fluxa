from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.private_rss_repository import FetchRunRecord, PrivateRssRepository


class PrivateRssRepositoryTests(unittest.TestCase):
    def test_summary_distinguishes_matcher_not_run_from_zero_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            before = repository.summary(enabled=True)
            self.assertFalse(before["matcherRan"])
            self.assertEqual(before["matches"], 0)

            repository.record_match_run(scanned_count=347, match_count=0)
            after = repository.summary(enabled=True)
            self.assertTrue(after["matcherRan"])
            self.assertEqual(after["lastMatchScanned"], 347)
            self.assertEqual(after["lastMatchCreated"], 0)
            self.assertEqual(after["lastMatchStatus"], "success")

    def test_source_urls_stay_internal_and_items_are_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "测试站",
                "feedUrl": "https://tracker.example/rss?passkey=secret-value",
                "intervalMinutes": 5,
                "retentionDays": 7,
            })
            self.assertNotIn("feedUrl", source)
            self.assertNotIn("secret-value", str(source))
            internal = repository.get_source(source["id"], public=False)
            self.assertIn("secret-value", internal["feed_url"])
            repository.upsert_items(source["id"], [{
                "fingerprint": "one",
                "guid": "one",
                "title": "诡秘之主 S01E03 2160p HDR",
                "published_at": "2026-07-18T01:00:00Z",
                "download_url": "https://tracker.example/download?passkey=secret-value",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 3,
                "episode_end": 3,
                "version_summary": "2160P · HDR",
            }])
            result = repository.search_items(query="诡秘 HDR")
            self.assertEqual(result["total"], 1)
            self.assertNotIn("secret-value", str(result))
            self.assertTrue(result["items"][0]["hasDownload"])

    def test_identity_columns_migrate_filter_and_preserve_reliable_supplement(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "media_control_center.sqlite3"
            repository = PrivateRssRepository(database_path)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "identified",
                "title": "明确身份 2026",
                "tmdb_id": "12345",
                "identity_status": "identified",
                "identity_source": "rss_description",
                "identity_confidence": "strong",
            }, {
                "fingerprint": "supplemented",
                "title": "追更补充 S01E01",
            }])
            items = repository.search_items(identity_status="identified")
            self.assertEqual(items["total"], 1)
            self.assertEqual(items["items"][0]["tmdbId"], "12345")

            supplemented_id = repository.search_items(query="追更补充")["items"][0]["id"]
            with repository.runtime.transaction(immediate=True) as connection:
                changed = repository.supplement_item_identity(
                    connection,
                    supplemented_id,
                    tmdb_id="98765",
                    source="subscription_match",
                    confidence="fallback",
                )
            self.assertTrue(changed)
            supplemented = repository.get_item(supplemented_id)
            self.assertEqual(supplemented["identityStatus"], "identified")
            self.assertEqual(supplemented["identitySource"], "subscription_match")

            repository.upsert_items(source["id"], [{
                "fingerprint": "supplemented",
                "title": "追更补充 S01E01",
                "identity_status": "unidentified",
            }])
            self.assertEqual(repository.get_item(supplemented_id)["tmdbId"], "98765")

            with self.assertRaisesRegex(ValueError, "身份状态"):
                repository.search_items(identity_status="unknown")

    def test_custom_poll_interval_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "自定义周期站",
                "feedUrl": "https://tracker.example/custom-rss",
                "intervalMinutes": 30,
            })
            self.assertEqual(source["intervalMinutes"], 30)

            with self.assertRaisesRegex(ValueError, "1 到 1440"):
                repository.save_source({
                    "name": "无效周期站",
                    "feedUrl": "https://tracker.example/invalid-rss",
                    "intervalMinutes": 1441,
                })
            with self.assertRaisesRegex(ValueError, "整数分钟"):
                repository.save_source({
                    "name": "小数周期站",
                    "feedUrl": "https://tracker.example/fractional-rss",
                    "intervalMinutes": 1.5,
                })

    def test_duplicate_source_and_item_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            payload = {"name": "站点", "feedUrl": "https://tracker.example/rss?passkey=one"}
            source = repository.save_source(payload)
            with self.assertRaises(Exception):
                repository.save_source(payload)
            first = repository.upsert_items(source["id"], [{"fingerprint": "same", "title": "A"}])
            second = repository.upsert_items(source["id"], [{"fingerprint": "same", "title": "A2"}])
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["updated"], 1)
            self.assertEqual(repository.search_items()["total"], 1)

    def test_changing_feed_url_resets_conditional_request_and_backoff_state(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "站点",
                "feedUrl": "https://tracker.example/rss?passkey=old",
            })
            repository.record_fetch(source["id"], "error", FetchRunRecord(message="timeout"))
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "UPDATE rss_sources SET etag='old-etag', last_modified='old-date' WHERE id=?",
                    (source["id"],),
                )

            repository.save_source({
                "feedUrl": "https://tracker.example/rss?passkey=new",
            }, source_id=source["id"])

            changed = repository.get_source(source["id"], public=False)
            for field in (
                "etag", "last_modified", "last_success_at", "last_error", "backoff_until", "next_poll_at"
            ):
                self.assertEqual(changed[field], "")
            self.assertEqual(changed["failure_count"], 0)

    def test_insert_match_callback_failure_rolls_back_new_items(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "站点", "feedUrl": "https://tracker.example/rss"})

            def fail_match(_connection, _rows):
                raise RuntimeError("match failed")

            with self.assertRaisesRegex(RuntimeError, "match failed"):
                repository.upsert_items(
                    source["id"],
                    [{"fingerprint": "rollback", "title": "不会入库"}],
                    on_insert=fail_match,
                )
            self.assertEqual(repository.search_items()["total"], 0)

    def test_failure_backoff_resets_after_success_and_fetch_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "站点", "feedUrl": "https://tracker.example/rss?passkey=one"})
            started = datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)

            repository.record_fetch(source["id"], "error", FetchRunRecord(message="timeout", now=started))
            first = repository.get_source(source["id"], public=False)
            self.assertEqual(first["failure_count"], 1)
            self.assertEqual(first["backoff_until"], "2026-07-18T01:01:00Z")
            repository.record_fetch(
                source["id"],
                "error",
                FetchRunRecord(message="timeout", now=started + timedelta(minutes=1)),
            )
            second = repository.get_source(source["id"], public=False)
            self.assertEqual(second["failure_count"], 2)
            self.assertEqual(second["backoff_until"], "2026-07-18T01:03:00Z")
            repository.record_fetch(
                source["id"],
                "success",
                FetchRunRecord(now=started + timedelta(minutes=3)),
            )
            recovered = repository.get_source(source["id"], public=False)
            self.assertEqual(recovered["failure_count"], 0)
            self.assertEqual(recovered["backoff_until"], "")
            self.assertEqual(recovered["last_error"], "")

            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            with repository.runtime.transaction(immediate=True) as connection:
                connection.executemany(
                    "INSERT INTO rss_fetch_runs (source_id, status, item_count, http_status, message, created_at) "
                    "VALUES (?, 'success', 0, 200, '', ?)",
                    ((source["id"], created_at) for _ in range(1000)),
                )
            repository.record_fetch(source["id"], "success")
            with closing(repository.runtime.connect()) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM rss_fetch_runs WHERE source_id=?", (source["id"],)
                ).fetchone()["count"]
            self.assertEqual(count, 1000)


if __name__ == "__main__":
    unittest.main()
