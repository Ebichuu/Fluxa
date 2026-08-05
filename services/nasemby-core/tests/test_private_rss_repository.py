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


class PrivateRssRepositoryTests(unittest.TestCase):
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
