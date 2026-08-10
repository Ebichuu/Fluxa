from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.private_rss_repository import (
    FetchRunRecord,
    PrivateRssRepository,
    RssMatchCleanupStale,
)
from app.subscription_repository import SubscriptionRepository


class PrivateRssRepositoryTests(unittest.TestCase):
    @staticmethod
    def _identified_item(fingerprint, title, tmdb_id, season=1):
        return {
            "fingerprint": fingerprint,
            "title": title,
            "media_type": "tv",
            "season_number": season,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": str(tmdb_id),
            "identity_status": "identified",
            "identity_source": "subscription_match",
            "identity_confidence": "strong",
        }

    @staticmethod
    def _imdb_only_item(fingerprint, title, imdb_id="tt1234567", season=1):
        return {
            "fingerprint": fingerprint,
            "title": title,
            "media_type": "tv",
            "season_number": season,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "",
            "imdb_id": imdb_id,
            "identity_status": "identified",
            "identity_source": "rss_description",
            "identity_confidence": "strong",
        }

    def test_media_metadata_uses_matched_subscription_priority_and_chinese_search(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_item({
                "title": "低优先级标题",
                "media_type": "tv",
                "tmdb_id": "101",
                "target_season": 1,
                "poster_url": "https://image.example/lower.jpg",
                "year": "2025",
            }, "tv:lower")
            subscriptions.upsert_item({
                "title": "匹配订阅中文名",
                "media_type": "tv",
                "tmdb_id": "101",
                "target_season": 1,
                "poster_url": "https://image.example/matched.jpg?token=must-not-leak",
                "year": "2026",
            }, "tv:matched")
            subscriptions.upsert_item({
                "title": "第二部中文作品",
                "media_type": "tv",
                "tmdb_id": "202",
                "target_season": 1,
                "poster_url": "https://image.example/second.jpg",
                "year": "2026",
            }, "tv:second")

            repository = PrivateRssRepository(database)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                self._identified_item("matched", "Release.Name.S01E01.2160p", "101"),
                self._identified_item("second", "Another.Release.S01E01.1080p", "202"),
            ])
            rows = {row["title"]: row for row in repository.search_items(limit=10)["items"]}
            repository.create_match(
                rows["Release.Name.S01E01.2160p"]["id"],
                "tv:matched",
                "tv:matched:s1:e1",
                {},
            )

            matched = repository.search_items(query="匹配订阅中文名")
            self.assertEqual(matched["total"], 1)
            self.assertEqual(matched["items"][0]["mediaTitle"], "匹配订阅中文名")
            self.assertEqual(matched["items"][0]["mediaYear"], "2026")
            self.assertEqual(matched["items"][0]["posterUrl"], "https://image.example/matched.jpg")
            self.assertNotIn("must-not-leak", str(matched))

            first_page = repository.search_items(query="中文", limit=1, offset=0)
            second_page = repository.search_items(query="中文", limit=1, offset=1)
            self.assertEqual(first_page["total"], 2)
            self.assertEqual(second_page["total"], 2)
            self.assertNotEqual(first_page["items"][0]["id"], second_page["items"][0]["id"])

    def test_media_metadata_falls_back_to_discover_and_local_tmdb_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_discover_candidates([{
                "title": "发现候选中文名",
                "media_type": "tv",
                "tmdb_id": "303",
                "season_number": 1,
                "poster_url": "https://image.example/discover.jpg",
                "year": "2026",
            }])
            cache_calls = []

            def cache_loader(identities):
                cache_calls.append(set(identities))
                return {
                    ("tv", "404", 1): {
                        "mediaTitle": "缓存中文名",
                        "mediaYear": "2024",
                        "posterUrl": "https://image.example/cache.jpg",
                    }
                }

            repository = PrivateRssRepository(database, media_metadata_cache_loader=cache_loader)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                self._identified_item("discover", "Discover.Release.S01E01", "303"),
                self._identified_item("cache", "Cache.Release.S01E01", "404"),
            ])

            discover = repository.search_items(query="发现候选中文名")
            cached = repository.search_items(query="缓存中文名")

            self.assertEqual(discover["items"][0]["posterUrl"], "https://image.example/discover.jpg")
            self.assertEqual(cached["items"][0]["mediaTitle"], "缓存中文名")
            self.assertTrue(cache_calls)
            self.assertIn(("tv", "404", 1), set().union(*cache_calls))

    def test_media_metadata_conflict_stops_fallback_and_rejects_private_poster(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_item({
                "title": "冲突标题甲", "media_type": "tv", "tmdb_id": "505", "target_season": 1,
            }, "tv:conflict-a")
            subscriptions.upsert_item({
                "title": "冲突标题乙", "media_type": "tv", "tmdb_id": "505", "target_season": 1,
            }, "tv:conflict-b")
            subscriptions.upsert_item({
                "title": "安全标题",
                "media_type": "tv",
                "tmdb_id": "606",
                "target_season": 1,
                "poster_url": "http://192.168.50.126/private.jpg",
            }, "tv:private-poster")
            subscriptions.upsert_discover_candidates([{
                "title": "不应降级到这里",
                "media_type": "tv",
                "tmdb_id": "505",
                "season_number": 1,
            }])
            repository = PrivateRssRepository(database)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                self._identified_item("conflict", "Conflict.Release.S01E01", "505"),
                self._identified_item("private", "Private.Release.S01E01", "606"),
            ])

            rows = {row["tmdbId"]: row for row in repository.search_items(limit=10)["items"]}

            self.assertNotIn("mediaTitle", rows["505"])
            self.assertNotIn("posterUrl", rows["505"])
            self.assertEqual(rows["606"]["mediaTitle"], "安全标题")
            self.assertNotIn("posterUrl", rows["606"])

    def test_imdb_only_media_metadata_uses_unique_subscription_without_persisting_tmdb(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_item({
                "title": "这一秒过火",
                "media_type": "tv",
                "tmdb_id": "707",
                "target_season": 1,
                "poster_url": "https://image.example/overheat.jpg?token=secret",
                "year": "2026",
            }, "tv:overheat")
            cache_calls = []

            def cache_loader(identities):
                cache_calls.append(set(identities))
                return {}

            repository = PrivateRssRepository(database, media_metadata_cache_loader=cache_loader)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                self._imdb_only_item("imdb-only", "Release.Name.S01E01", season=1),
            ])
            row = repository.search_items(limit=10)["items"][0]
            repository.create_match(row["id"], "tv:overheat", "tv:overheat:s1:e1", {})
            repository.create_match(row["id"], "tv:overheat", "tv:overheat:s1:e2", {})

            result = repository.search_items(limit=10)["items"][0]
            searched = repository.search_items(query="这一秒过火")

            self.assertEqual(result["mediaTitle"], "这一秒过火")
            self.assertEqual(result["mediaYear"], "2026")
            self.assertEqual(result["posterUrl"], "https://image.example/overheat.jpg")
            self.assertEqual(result["tmdbId"], "")
            self.assertEqual(result["imdbId"], "tt1234567")
            self.assertEqual(searched["total"], 1)
            self.assertEqual(searched["items"][0]["id"], result["id"])
            self.assertEqual(cache_calls, [])
            with closing(subscriptions.runtime.connect()) as connection:
                stored = connection.execute(
                    "SELECT tmdb_id, identity_source FROM rss_items WHERE id=?", (row["id"],)
                ).fetchone()
            self.assertEqual((stored["tmdb_id"], stored["identity_source"]), ("", "rss_description"))

    def test_imdb_only_media_metadata_rejects_multiple_subscriptions_and_missing_subscription(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_item({
                "title": "候选标题甲", "media_type": "tv", "tmdb_id": "708", "target_season": 1,
            }, "tv:conflict-a")
            subscriptions.upsert_item({
                "title": "候选标题乙", "media_type": "tv", "tmdb_id": "708", "target_season": 1,
            }, "tv:conflict-b")
            repository = PrivateRssRepository(database)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                self._imdb_only_item("multi-subscription", "Conflict.Release.S01E01"),
                self._imdb_only_item("missing-subscription", "Missing.Release.S01E01", imdb_id="tt7654321"),
            ])
            rows = {row["title"]: row for row in repository.search_items(limit=10)["items"]}
            repository.create_match(rows["Conflict.Release.S01E01"]["id"], "tv:conflict-a", "unit:a", {})
            repository.create_match(rows["Conflict.Release.S01E01"]["id"], "tv:conflict-b", "unit:b", {})
            repository.create_match(rows["Missing.Release.S01E01"]["id"], "tv:missing", "unit:missing", {})

            result = {row["title"]: row for row in repository.search_items(limit=10)["items"]}

            self.assertNotIn("mediaTitle", result["Conflict.Release.S01E01"])
            self.assertNotIn("mediaTitle", result["Missing.Release.S01E01"])

    def test_imdb_only_media_metadata_rejects_subscription_season_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "media_control_center.sqlite3"
            subscriptions = SubscriptionRepository(database)
            subscriptions.upsert_item({
                "title": "季号冲突作品", "media_type": "tv", "tmdb_id": "709", "target_season": 2,
            }, "tv:season-conflict")
            repository = PrivateRssRepository(database)
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                self._imdb_only_item("season-conflict", "Conflict.Release.S01E01", season=1),
            ])
            row = repository.search_items(limit=10)["items"][0]
            repository.create_match(row["id"], "tv:season-conflict", "unit:season-conflict", {})

            result = repository.search_items(limit=10)["items"][0]

            self.assertNotIn("mediaTitle", result)

    def _cleanup_match(self, repository, source_id, fingerprint, subscription_key):
        repository.upsert_items(source_id, [{
            "fingerprint": fingerprint,
            "title": f"Cleanup {fingerprint} S01E03",
        }])
        item = next(
            row for row in repository.search_items(limit=100)["items"]
            if fingerprint in row["title"]
        )
        match = repository.create_match(
            item["id"], subscription_key, f"{subscription_key}:s1:e3", {},
        )
        repository.set_match_binding(
            match["id"],
            torra_subscription_id="upstream-secret-id",
            target_key="tv:tmdb:123:season:1:episodes:3-3",
            artifact_key=f"rss:{fingerprint}",
        )
        repository.save_match_evaluation([match["id"]], {
            "status": "blocked",
            "reason": "subscription_missing",
        })
        return repository.get_match(match["id"])

    def test_match_cleanup_archives_only_invalid_matches_and_keeps_rss_items(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "Cleanup", "feedUrl": "https://tracker.example/rss"})
            match = self._cleanup_match(repository, source["id"], "orphan", "tv:missing:s1")
            before_items = repository.search_items(limit=100)["total"]

            preview = repository.create_match_cleanup_preview([match["id"]])
            result = repository.apply_match_cleanup(
                preview_id=preview["id"],
                fingerprint=preview["fingerprint"],
                match_ids=[match["id"]],
                idempotency_key="cleanup-once",
            )
            replay = repository.apply_match_cleanup(
                preview_id=preview["id"],
                fingerprint=preview["fingerprint"],
                match_ids=[match["id"]],
                idempotency_key="cleanup-once",
            )

            self.assertEqual(preview["itemCount"], 1)
            self.assertEqual(result, replay)
            self.assertEqual(result["archivedCount"], 1)
            self.assertEqual(repository.search_items(limit=100)["total"], before_items)
            self.assertNotIn(
                match["itemId"],
                {row["id"] for row in repository.list_items_for_match(limit=100)},
            )
            self.assertEqual(repository.list_candidate_groups(group_scope="cleanup")["total"], 0)
            archived = repository.get_match(match["id"])
            self.assertEqual(archived["archiveState"], "archived")
            self.assertEqual(archived["archiveReasonCode"], "subscription_missing")
            self.assertEqual(repository.list_match_cleanup_runs()["items"][0]["status"], "applied")
            with closing(repository.runtime.connect()) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) AS count FROM rss_match_cleanup_items").fetchone()["count"],
                    1,
                )

    def test_match_cleanup_drift_rolls_back_the_whole_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "Cleanup", "feedUrl": "https://tracker.example/rss"})
            first = self._cleanup_match(repository, source["id"], "first", "tv:missing:first")
            second = self._cleanup_match(repository, source["id"], "second", "tv:missing:second")
            preview = repository.create_match_cleanup_preview([first["id"], second["id"]])
            repository.save_match_evaluation([second["id"]], {
                "status": "blocked",
                "reason": "subscription_missing",
            })

            with self.assertRaises(RssMatchCleanupStale):
                repository.apply_match_cleanup(
                    preview_id=preview["id"],
                    fingerprint=preview["fingerprint"],
                    match_ids=[first["id"], second["id"]],
                    idempotency_key="cleanup-stale",
                )

            self.assertEqual(repository.get_match(first["id"])["archiveState"], "active")
            self.assertEqual(repository.get_match(second["id"])["archiveState"], "active")
            self.assertEqual(repository.list_match_cleanup_runs()["items"][0]["status"], "stale")

    def test_match_cleanup_preview_rejects_valid_and_conflicted_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "Cleanup", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{"fingerprint": "valid", "title": "Valid S01E01"}])
            item = repository.search_items(limit=10)["items"][0]
            valid = repository.create_match(item["id"], "tv:valid:s1", "tv:valid:s1:e1", {})
            repository.save_match_evaluation([valid["id"]], {
                "status": "scored", "reason": "shadow_only_no_download",
            })
            preview = repository.create_match_cleanup_preview([valid["id"]])

            self.assertEqual(preview["itemCount"], 0)
            self.assertEqual(preview["skipped"][0]["reasonCode"], "candidate_still_active")
            self.assertEqual(repository.get_match(valid["id"])["archiveState"], "active")

    def test_subscription_missing_groups_move_to_cleanup_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "Cleanup", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "scoreable",
                "title": "Linked Show S01E03",
            }, {
                "fingerprint": "orphan",
                "title": "Orphan Show S01E03",
            }])
            items = {item["title"]: item for item in repository.search_items(limit=10)["items"]}
            scoreable = repository.create_match(
                items["Linked Show S01E03"]["id"], "tv:123:s1", "tv:123:s1:s1:e3", {},
            )
            orphan = repository.create_match(
                items["Orphan Show S01E03"]["id"], "tv:missing:s1", "tv:missing:s1:s1:e3", {},
            )
            repository.save_match_evaluation([orphan["id"]], {
                "status": "blocked",
                "reason": "subscription_missing",
            })

            all_groups = repository.list_candidate_groups(limit=10)
            scoreable_groups = repository.list_candidate_groups(group_scope="scoreable", limit=10)
            cleanup_groups = repository.list_candidate_groups(group_scope="cleanup", limit=10)

            self.assertEqual(all_groups["total"], 2)
            self.assertEqual(all_groups["counts"]["total"], 2)
            self.assertEqual(all_groups["counts"]["scoreable_total"], 1)
            self.assertEqual(all_groups["counts"]["needs_cleanup"], 1)
            self.assertEqual(scoreable_groups["total"], 1)
            self.assertEqual(scoreable_groups["groups"][0]["candidates"][0]["id"], scoreable["id"])
            self.assertEqual(cleanup_groups["total"], 1)
            self.assertEqual(cleanup_groups["groups"][0]["state"], "needs_cleanup")
            self.assertEqual(cleanup_groups["groups"][0]["baselineState"], "baseline_missing")
            self.assertEqual(cleanup_groups["groups"][0]["blockerCode"], "subscription_missing")
            self.assertEqual(cleanup_groups["groups"][0]["nextAction"], "review_match_cleanup")
            self.assertEqual(cleanup_groups["groups"][0]["candidates"][0]["id"], orphan["id"])
            with self.assertRaisesRegex(ValueError, "候选组范围"):
                repository.list_candidate_groups(group_scope="unknown")

    def test_candidate_group_paginates_units_and_keeps_all_versions_together(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "Rules",
                "feedUrl": "https://tracker.example/rss",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "candidate-low",
                "title": "Show.S01E02.1080p.mkv",
            }, {
                "fingerprint": "candidate-high",
                "title": "Show.S01E02.2160p.mkv",
            }])
            items = repository.search_items(limit=10)["items"]
            unit_key = "tv:123:s1:s1:e2"
            matches = []
            for item in items:
                match = repository.create_match(item["id"], "tv:123:s1", unit_key, {
                    "mediaType": "tv",
                    "season": {"item": 1, "unit": 1},
                    "episode": {"start": 2, "end": 2, "unit": 2},
                })
                repository.set_match_binding(
                    match["id"],
                    torra_subscription_id="raw-torra-id",
                    target_key="tv:tmdb:123:season:1:episodes:2-2",
                    artifact_key=f"rss:{item['id']}",
                )
                score = 90 if "2160p" in item["title"] else 60
                repository.save_match_evaluation([match["id"]], {
                    "ruleId": "rule-1",
                    "ruleHash": "hash-1",
                    "candidateScore": score,
                    "baselineScore": 50,
                    "status": "scored",
                    "decision": "current_best" if score == 90 else "superseded",
                    "candidateSummary": {"versionSummary": item["title"]},
                })
                matches.append((match, score))
            winner = next(match for match, score in matches if score == 90)
            repository.save_candidate_decisions([{
                "matchIds": [match["id"] for match, _score in matches],
                "decision": "superseded",
                "reason": "higher_scored_candidate",
                "bestCandidate": False,
            }, {
                "matchIds": [winner["id"]],
                "decision": "current_best",
                "reason": "shadow_only_no_download",
                "bestCandidate": True,
            }])

            payload = repository.list_candidate_groups(limit=10, offset=0)

            self.assertEqual(payload["total"], 1)
            self.assertEqual(len(payload["groups"]), 1)
            group = payload["groups"][0]
            self.assertEqual(group["candidateCount"], 2)
            self.assertEqual(group["state"], "upgrade_available")
            self.assertEqual(group["bestMatchId"], winner["id"])
            self.assertEqual(group["bestCandidateScore"], 90)
            self.assertEqual(group["baselineState"], "baseline_ready")
            self.assertEqual(group["blockerCode"], "")
            self.assertEqual(group["nextAction"], "preview_exact_download")
            self.assertEqual([row["candidateScore"] for row in group["candidates"]], [90, 60])
            self.assertEqual(payload["counts"]["total"], 1)
            self.assertEqual(payload["counts"]["upgrade_available"], 1)
            self.assertEqual(
                repository.list_candidate_groups(group_state="upgrade_available")["total"],
                1,
            )
            self.assertEqual(
                repository.list_candidate_groups(group_state="protected")["total"],
                0,
            )
            self.assertEqual(repository.candidate_contract_summary()["automatic_eligible_count"], 1)
            scoped = repository.list_candidate_groups(
                subscription_id="tv:123:s1",
                media_type="tv",
                season_number=1,
                episode_number=2,
            )
            self.assertEqual(scoped["total"], 1)
            self.assertEqual(
                repository.list_candidate_groups(match_id=winner["id"])["groups"][0]["bestMatchId"],
                winner["id"],
            )
            self.assertEqual(
                repository.find_unique_source_match(
                    [f"rss:{winner['itemId']}"],
                    ["tv:123:s1"],
                    "tv:tmdb:123:season:1:episodes:2-2",
                ),
                {"matchId": winner["id"]},
            )
            self.assertIsNone(repository.find_unique_source_match(
                [f"rss:{match['itemId']}" for match, _score in matches],
                ["tv:123:s1"],
                "tv:tmdb:123:season:1:episodes:2-2",
            ))
            with self.assertRaisesRegex(ValueError, "候选组状态"):
                repository.list_candidate_groups(group_state="unknown")

    def test_artifact_groups_merge_range_and_block_partial_winners(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "Artifact Rules", "feedUrl": "https://tracker.example/artifacts.xml",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "range-pack",
                "title": "Range Show S01E02-E03 2160p",
            }, {
                "fingerprint": "episode-three",
                "title": "Range Show S01E03 REMUX",
            }])
            items = {item["title"]: item for item in repository.search_items(limit=10)["items"]}
            range_matches = []
            for episode in (2, 3):
                match = repository.create_match(
                    items["Range Show S01E02-E03 2160p"]["id"],
                    "tv:123:s1",
                    f"tv:123:s1:s1:e{episode}",
                    {
                        "mediaType": "tv",
                        "season": {"item": 1, "unit": 1},
                        "episode": {"start": 2, "end": 3, "unit": episode},
                    },
                )
                repository.set_match_binding(
                    match["id"],
                    torra_subscription_id="torra-123",
                    target_key="tv:tmdb:123:season:1:episodes:2-3",
                    artifact_key="rss:range-pack",
                )
                repository.save_match_evaluation([match["id"]], {
                    "ruleId": "rule-1", "ruleHash": "hash-1",
                    "candidateScore": 80, "baselineScore": 60,
                    "status": "scored", "decision": "current_best" if episode == 2 else "superseded",
                })
                repository.save_candidate_decisions([{
                    "matchIds": [match["id"]],
                    "decision": "current_best" if episode == 2 else "superseded",
                    "reason": "higher_score",
                    "bestCandidate": episode == 2,
                }])
                range_matches.append(match)
            single = repository.create_match(
                items["Range Show S01E03 REMUX"]["id"],
                "tv:123:s1",
                "tv:123:s1:s1:e3",
                {
                    "mediaType": "tv",
                    "season": {"item": 1, "unit": 1},
                    "episode": {"start": 3, "end": 3, "unit": 3},
                },
            )
            repository.set_match_binding(
                single["id"],
                torra_subscription_id="torra-123",
                target_key="tv:tmdb:123:season:1:episodes:3-3",
                artifact_key="rss:episode-three",
            )
            repository.save_match_evaluation([single["id"]], {
                "ruleId": "rule-1", "ruleHash": "hash-1",
                "candidateScore": 90, "baselineScore": 60,
                "status": "scored", "decision": "current_best",
            })
            repository.save_candidate_decisions([{
                "matchIds": [single["id"]], "decision": "current_best",
                "reason": "higher_score", "bestCandidate": True,
            }])

            payload = repository.list_candidate_artifact_groups(limit=10)
            ranged = next(group for group in payload["groups"] if group["candidateCount"] == 2)

            self.assertEqual(payload["total"], 2)
            self.assertEqual(ranged["state"], "partially_best")
            self.assertEqual(ranged["episodeLabel"], "S01E02–E03")
            self.assertEqual(ranged["coveredEpisodeStart"], 2)
            self.assertEqual(ranged["coveredEpisodeEnd"], 3)
            self.assertEqual(len(ranged["coveredUnits"]), 2)
            self.assertFalse(ranged["winsAllCoveredUnits"])
            self.assertEqual(ranged["blockerCode"], "artifact_partially_best")
            self.assertEqual(payload["counts"]["partially_best"], 1)
            self.assertEqual(
                repository.list_candidate_artifact_groups(group_state="partially_best")["total"], 1
            )
            ranged_by_item = repository.list_candidate_artifact_groups(
                item_id=items["Range Show S01E02-E03 2160p"]["id"], limit=2,
            )
            single_by_item = repository.list_candidate_artifact_groups(
                item_id=items["Range Show S01E03 REMUX"]["id"], limit=2,
            )
            self.assertEqual(ranged_by_item["total"], 1)
            self.assertEqual(ranged_by_item["groups"][0]["candidateCount"], 2)
            self.assertEqual(single_by_item["total"], 1)
            self.assertEqual(single_by_item["groups"][0]["candidateCount"], 1)
            self.assertEqual(repository.list_candidate_artifact_groups(item_id="rss:missing")["total"], 0)
            with self.assertRaisesRegex(ValueError, "资源 ID"):
                repository.list_candidate_artifact_groups(item_id="x" * 81)

    def test_needs_review_filter_uses_full_repository_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "Review", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "ready-tv",
                "title": "Ready Show S01E01",
                "media_type": "tv",
                "season_number": 1,
                "tmdb_id": "101",
                "identity_status": "identified",
            }, {
                "fingerprint": "identity-conflict",
                "title": "Conflict Show S01E01",
                "media_type": "tv",
                "season_number": 1,
                "identity_status": "conflict",
            }, {
                "fingerprint": "identity-missing",
                "title": "Unknown Show S01E01",
                "media_type": "tv",
                "season_number": 1,
                "identity_status": "unidentified",
            }, {
                "fingerprint": "season-missing",
                "title": "Season Unknown",
                "media_type": "tv",
                "tmdb_id": "102",
                "identity_status": "identified",
            }, {
                "fingerprint": "ready-movie",
                "title": "Ready Movie 2026",
                "media_type": "movie",
                "tmdb_id": "103",
                "identity_status": "identified",
            }])
            all_items = repository.search_items(limit=10)["items"]
            conflict = next(item for item in all_items if item["title"] == "Conflict Show S01E01")
            repository.create_match(
                conflict["id"], "tv:linked:s1", "tv:linked:s1:s1:e1", {},
            )

            pending = repository.search_items(review_state="needs_review", limit=2, offset=0)
            linked_pending = repository.search_items(review_state="follow_needs_review", limit=10)
            unlinked = repository.search_items(review_state="unlinked", limit=10)
            conflicts = repository.search_items(
                review_state="needs_review",
                identity_status="conflict",
                limit=10,
            )

            self.assertEqual(pending["total"], 3)
            self.assertEqual(len(pending["items"]), 2)
            self.assertEqual(linked_pending["total"], 1)
            self.assertEqual(linked_pending["items"][0]["title"], "Conflict Show S01E01")
            self.assertEqual(linked_pending["items"][0]["followState"], "linked")
            self.assertEqual(unlinked["total"], 4)
            self.assertTrue(all(item["followState"] == "unlinked" for item in unlinked["items"]))
            self.assertEqual(conflicts["total"], 1)
            self.assertEqual(conflicts["items"][0]["identityStatus"], "conflict")
            with self.assertRaisesRegex(ValueError, "复核状态"):
                repository.search_items(review_state="unknown")

    def test_resource_center_summary_and_date_filter_share_same_item_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "Today", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "today-ready",
                "title": "Today Ready S01E01",
                "published_at": "2026-07-30T16:00:00Z",
                "media_type": "tv",
                "season_number": 1,
                "tmdb_id": "201",
                "identity_status": "identified",
            }, {
                "fingerprint": "today-review",
                "title": "Today Review",
                "published_at": "2026-07-31T03:00:00Z",
                "identity_status": "unidentified",
            }, {
                "fingerprint": "old-review",
                "title": "Old Review",
                "published_at": "2026-07-30T15:59:59Z",
                "identity_status": "unidentified",
            }])
            start = "2026-07-30T16:00:00Z"
            end = "2026-07-31T16:00:00Z"
            ready_item = repository.search_items(query="Today Ready", limit=10)["items"][0]
            repository.create_match(
                ready_item["id"], "tv:201:s1", "tv:201:s1:e1", {},
            )

            summary = repository.resource_center_summary(start, end)
            page = repository.search_items(
                published_from=start,
                published_before=end,
                limit=10,
            )
            linked_page = repository.search_items(
                published_from=start,
                published_before=end,
                follow_state="linked",
                limit=10,
            )
            unlinked_page = repository.search_items(
                published_from=start,
                published_before=end,
                follow_state="unlinked",
                limit=10,
            )

            self.assertEqual(summary, {
                "newToday": 2,
                "followNewToday": 1,
                "needsReview": 2,
                "followNeedsReview": 0,
                "unlinkedItems": 2,
                "upgradeAvailable": 0,
                "needsDecision": 0,
            })
            self.assertEqual(page["total"], summary["newToday"])
            self.assertEqual(linked_page["total"], summary["followNewToday"])
            self.assertEqual(linked_page["items"][0]["title"], "Today Ready S01E01")
            self.assertEqual(unlinked_page["total"], 1)
            with self.assertRaisesRegex(ValueError, "发布时间范围"):
                repository.search_items(published_from=end, published_before=start)
            with self.assertRaisesRegex(ValueError, "追更关联状态"):
                repository.search_items(follow_state="unknown")

    def test_match_shadow_fields_and_rule_snapshots_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({
                "name": "Rules",
                "feedUrl": "https://tracker.example/rss",
            })
            repository.upsert_items(source["id"], [{
                "fingerprint": "candidate-one",
                "title": "Show.S01E02.2160p.mkv",
            }])
            item_id = repository.search_items()["items"][0]["id"]
            match = repository.create_match(item_id, "tv:123:s1", "tv:123:s1:s1:e2", {})

            repository.set_match_binding(
                match["id"],
                torra_subscription_id="raw-remote-id",
                target_key="tv:tmdb:123:season:1:episodes:2-2",
                artifact_key="rss:artifact-one",
            )
            updated = repository.save_match_evaluation([match["id"]], {
                "ruleId": "rule-1",
                "ruleHash": "hash-1",
                "candidateScore": 88.5,
                "baselineScore": None,
                "status": "scored",
                "decision": "waiting_baseline",
                "reason": "shadow_only_no_download",
                "actionId": "action-1",
                "evaluatedAt": "2026-07-30T01:00:00Z",
            })[0]

            self.assertTrue(updated["torraLinked"])
            self.assertEqual(updated["candidateScore"], 88.5)
            self.assertIsNone(updated["baselineScore"])
            self.assertEqual(updated["evaluationStatus"], "scored")
            self.assertNotIn("raw-remote-id", str(updated))

            snapshots = [{
                "ruleId": "rule-1",
                "ruleHash": "hash-1",
                "rule": {"id": "rule-1", "name": "Rule"},
            }]
            repository.save_rule_snapshots(snapshots, "2026-07-30T01:00:00Z")
            repository.save_rule_snapshots(snapshots, "2026-07-30T02:00:00Z")
            with closing(repository.runtime.connect()) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM torra_rule_snapshots"
                ).fetchone()["count"]
            self.assertEqual(count, 1)

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

    def test_summary_records_identity_backfill_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")

            before = repository.summary(enabled=True)
            self.assertFalse(before["identityBackfillRan"])

            repository.record_identity_backfill_run({
                "scanned": 50,
                "identified": 3,
                "conflicts": 1,
                "unchanged": 46,
                "remaining": 600,
                "limit": 50,
            })
            after = repository.summary(enabled=True)

            self.assertTrue(after["identityBackfillRan"])
            self.assertEqual(after["lastIdentityBackfillScanned"], 50)
            self.assertEqual(after["lastIdentityBackfillIdentified"], 3)
            self.assertEqual(after["lastIdentityBackfillConflicts"], 1)
            self.assertEqual(after["lastIdentityBackfillUnchanged"], 46)
            self.assertEqual(after["lastIdentityBackfillRemaining"], 600)

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
                "description": "❁ 片 名: 诡秘之主 ❁ 年 代: 2026 ❁ 简 介: 测试简介",
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
            self.assertEqual(result["items"][0]["sourceTitle"], "诡秘之主")

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

    def test_non_ascii_search_tokens_never_fall_back_to_latest_items(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                {"fingerprint": "anime", "title": "ニャニャゴ S01E01", "media_type": "tv", "season_number": 1},
                {"fingerprint": "other", "title": "完全无关的种子", "media_type": "movie"},
            ])

            japanese = repository.search_items(query="ニャニャゴ")
            self.assertEqual(japanese["total"], 1)
            self.assertEqual(japanese["items"][0]["title"], "ニャニャゴ S01E01")

            invalid = repository.search_items(query="🦊")
            self.assertEqual(invalid["total"], 0)
            self.assertEqual(repository.search_items()["total"], 2)

    def test_rss_source_title_is_extracted_from_description_and_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "source-chinese-title",
                "title": "Whispers of Southern Song S01E16 2160p",
                "description": "❁ 片 名: 南戏 ❁ 年 代: 2026 ❁ 简 介: 测试简介",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 16,
                "episode_end": 16,
            }])

            result = repository.search_items(query="南戏")

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["sourceTitle"], "南戏")
            self.assertNotIn("mediaTitle", result["items"][0])

    def test_targeted_search_prefers_tmdb_identity_and_applies_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [
                {
                    "fingerprint": "exact", "title": "English Archive S01E03", "tmdb_id": "279323",
                    "identity_status": "identified", "media_type": "tv", "season_number": 1,
                },
                {
                    "fingerprint": "wrong", "title": "鬼谜东宫 S01E03", "tmdb_id": "999999",
                    "identity_status": "identified", "media_type": "tv", "season_number": 1,
                },
                {
                    "fingerprint": "fallback", "title": "Ghost Palace S01E04", "media_type": "tv", "season_number": 1,
                },
                {
                    "fingerprint": "other-season", "title": "Ghost Palace S02E01", "media_type": "tv", "season_number": 2,
                },
            ])

            exact = repository.search_items(
                query="鬼谜东宫", tmdb_id="279323", media_type="tv", season_number=1,
            )
            self.assertEqual(exact["total"], 1)
            self.assertEqual(exact["items"][0]["tmdbId"], "279323")
            self.assertEqual(exact["items"][0]["matchMethod"], "tmdb_exact")

            fallback = repository.search_items(
                query="Ghost Palace", tmdb_id="279323", media_type="tv", season_number=1,
            )
            self.assertEqual(fallback["total"], 2)
            self.assertEqual({item["title"] for item in fallback["items"]}, {"English Archive S01E03", "Ghost Palace S01E04"})

            wrong_scope = repository.search_items(
                query="Ghost Palace", tmdb_id="279323", media_type="tv", season_number=2,
            )
            self.assertEqual(wrong_scope["total"], 1)
            self.assertEqual(wrong_scope["items"][0]["seasonNumber"], 2)

    def test_tv_targeted_search_allows_no_year_and_unknown_season_without_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "known-season",
                "title": "清醒点，桃子 S01E02 1080p",
                "media_type": "tv",
                "season_number": 1,
            }, {
                "fingerprint": "unknown-season",
                "title": "清醒点，桃子 E03 2160p",
                "media_type": "tv",
            }, {
                "fingerprint": "unknown-type",
                "title": "清醒点，桃子 E04 WEB-DL",
            }, {
                "fingerprint": "wrong-season",
                "title": "清醒点，桃子 S02E01",
                "media_type": "tv",
                "season_number": 2,
            }, {
                "fingerprint": "wrong-tmdb",
                "title": "清醒点，桃子 S01E05",
                "media_type": "tv",
                "season_number": 1,
                "tmdb_id": "999999",
                "identity_status": "identified",
            }])

            result = repository.search_items(
                query="清醒点，桃子",
                tmdb_id="777777",
                media_type="tv",
                season_number=1,
                year="2026",
            )

            self.assertEqual(result["total"], 3)
            self.assertEqual(
                {item["title"] for item in result["items"]},
                {"清醒点，桃子 S01E02 1080p", "清醒点，桃子 E03 2160p", "清醒点，桃子 E04 WEB-DL"},
            )
            unknown = next(item for item in result["items"] if item["title"] == "清醒点，桃子 E03 2160p")
            self.assertEqual(unknown["matchMethod"], "title_media_scope")
            self.assertEqual(unknown["seasonScopeState"], "unknown")

    def test_tv_targeted_search_filters_episode_ranges_and_keeps_season_packs(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "exact-episode",
                "title": "Target Show S01E03 2160p",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 3,
                "episode_end": 3,
                "tmdb_id": "880001",
                "identity_status": "identified",
            }, {
                "fingerprint": "covering-range",
                "title": "Target Show S01E02-E04 1080p",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 2,
                "episode_end": 4,
                "tmdb_id": "880001",
                "identity_status": "identified",
            }, {
                "fingerprint": "season-pack",
                "title": "Target Show S01 2160p",
                "media_type": "tv",
                "season_number": 1,
                "tmdb_id": "880001",
                "identity_status": "identified",
            }, {
                "fingerprint": "outside-episode",
                "title": "Target Show S01E05 2160p",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 5,
                "episode_end": 5,
                "tmdb_id": "880001",
                "identity_status": "identified",
            }])

            result = repository.search_items(
                tmdb_id="880001", media_type="tv", season_number=1, episode_number=3,
            )

            self.assertEqual(result["total"], 3)
            self.assertEqual(
                {item["episodeMatchState"] for item in result["items"]},
                {"exact", "range", "season_pack"},
            )
            self.assertNotIn("Target Show S01E05 2160p", {item["title"] for item in result["items"]})
            with self.assertRaisesRegex(ValueError, "集号无效"):
                repository.search_items(tmdb_id="880001", media_type="tv", episode_number=0)

    def test_tv_targeted_search_merges_subscription_links_with_tmdb_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "linked-imdb",
                "title": "Tracked Show S01E03 2160p",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 3,
                "episode_end": 3,
                "imdb_id": "tt8800001",
                "identity_status": "identified",
            }, {
                "fingerprint": "linked-wrong-episode",
                "title": "Tracked Show S01E04 2160p",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 4,
                "episode_end": 4,
                "imdb_id": "tt8800001",
                "identity_status": "identified",
            }, {
                "fingerprint": "tmdb-candidate",
                "title": "Tracked Show S01E03 1080p",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 3,
                "episode_end": 3,
                "tmdb_id": "880001",
                "identity_status": "identified",
            }])
            rows = {item["title"]: item for item in repository.search_items(limit=10)["items"]}
            repository.create_match(
                rows["Tracked Show S01E03 2160p"]["id"], "tv:tracked", "tv:tracked:s1:e3", {},
            )
            repository.create_match(
                rows["Tracked Show S01E04 2160p"]["id"], "tv:tracked", "tv:tracked:s1:e4", {},
            )

            result = repository.search_items(
                subscription_id="tv:tracked",
                tmdb_id="880001",
                media_type="tv",
                season_number=1,
                episode_number=3,
            )

            self.assertEqual(result["total"], 2)
            self.assertEqual(
                [item["matchMethod"] for item in result["items"]],
                ["subscription_link", "tmdb_exact"],
            )
            self.assertEqual(
                {item["title"] for item in result["items"]},
                {"Tracked Show S01E03 2160p", "Tracked Show S01E03 1080p"},
            )

    def test_movie_targeted_fallback_still_requires_year(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = PrivateRssRepository(Path(directory) / "media_control_center.sqlite3")
            source = repository.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
            repository.upsert_items(source["id"], [{
                "fingerprint": "right-year", "title": "Archive Movie 2026 2160p", "media_type": "movie",
            }, {
                "fingerprint": "wrong-year", "title": "Archive Movie 2025 2160p", "media_type": "movie",
            }, {
                "fingerprint": "no-year", "title": "Archive Movie 2160p", "media_type": "movie",
            }])

            result = repository.search_items(
                query="Archive Movie", tmdb_id="123456", media_type="movie", year="2026",
            )

            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["title"], "Archive Movie 2026 2160p")
            self.assertEqual(result["items"][0]["matchMethod"], "title_media_year")

            without_year = repository.search_items(
                query="Archive Movie", tmdb_id="123456", media_type="movie",
            )
            self.assertEqual(without_year["total"], 0)

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
