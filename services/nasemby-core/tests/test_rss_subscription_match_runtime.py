from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository
from app.rss_subscription_match_runtime import (
    RssAnalysisDependencies,
    RssExactDownloadError,
    RssSubscriptionMatchRuntime,
)
from app.subscription_reconciliation_runtime import torra_public_subscription_key
from app.torra_quality_runtime import TorraQualityClient


class FakeTorra:
    def __init__(self):
        self.rows = [{"id": "torra-202", "is_running": False, "is_mutating": False}]
        self.rules = []
        self.jobs = []
        self.submissions = []
        self.polls = []

    def is_configured(self):
        return True

    def list_subscriptions(self):
        return list(self.rows)

    def list_meta_weight_rules(self):
        return list(self.rules)

    def submit_analysis(self, subscription_id):
        self.submissions.append(subscription_id)
        return f"job-{len(self.submissions)}"

    def get_job(self, job_id):
        self.polls.append(job_id)
        return self.jobs.pop(0)

    @staticmethod
    def select_upgrade_candidates(job):
        return TorraQualityClient.select_upgrade_candidates(job)


class FakeQb:
    def __init__(self):
        self.tasks = []
        self.added = []
        self.confirm_added_task = True

    def summary(self):
        return {"configured": True, "connected": True, "tasks": list(self.tasks)}

    def add_torrent(self, download_url, save_path, category, tags):
        self.added.append({
            "downloadUrl": download_url,
            "savePath": save_path,
            "category": category,
            "tags": list(tags),
        })
        if self.confirm_added_task:
            self.tasks.append({
                "hash": f"hash-{len(self.added)}",
                "name": "Test Show S01E02 2160p WEB-DL.mkv",
                "status": "downloading",
                "tags": ",".join(tags),
            })
        return {"accepted": True}


class RssSubscriptionMatchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
        database = Path(self.directory.name) / "media_control_center.sqlite3"
        self.rss = PrivateRssRepository(database)
        self.watch = QualityWatchRepository(database, clock=lambda: self.now[0])
        self.source = self.rss.save_source({"name": "测试站", "feedUrl": "https://tracker.example/rss"})
        self.subscriptions = []
        self.runtime = RssSubscriptionMatchRuntime(
            self.rss,
            self.watch,
            lambda: {"items": self.subscriptions},
            clock=lambda: self.now[0],
        )

    def _watch(
        self,
        key,
        media_type="tv",
        tmdb_id="202",
        season=1,
        episode=None,
        torra_id="torra-202",
        **overrides,
    ):
        subscription = {
            "key": key,
            "title": "测试剧" if media_type == "tv" else "同名电影",
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "target_season": season,
            **overrides,
        }
        self.subscriptions.append(subscription)
        unit = self.watch.ensure_watch_unit(
            key,
            media_type,
            season if media_type == "tv" else None,
            episode,
            window_hours=48,
            torra_subscription_id=torra_id,
        )
        self.watch.mark_baseline_ready(unit["unit_key"])
        return unit

    def _enable_analysis(self, torra=None, qb=None, environment=None, config=None):
        torra = torra or FakeTorra()
        qb = qb or FakeQb()
        environment = environment if environment is not None else {
            "MCC_PRIVATE_RSS_ENABLED": "true",
            "MCC_TORRA_QUALITY_WATCH_ENABLED": "true",
            "TORRA_DOWNLOADER_ID": "qb-main",
        }
        config = config if config is not None else {
            "torra_quality_watch_enabled": True,
            "torra_quality_min_interval_minutes": 60,
            "torra_quality_hourly_limit": 4,
            "torra_quality_daily_limit": 30,
        }
        self.runtime.analysis = RssAnalysisDependencies(environment, torra, qb, lambda: config)
        return torra, qb

    def _configure_shadow_torra(self, torra, rules=None):
        torra.rows = [{
            "id": "torra-202",
            "name": "Test Show",
            "media_type": "tv",
            "tmdb_id": "202",
            "season_number": 1,
            "category": "anime",
            "download_category": "anime",
            "save_path": "/downloads/anime",
            "downloader_id": "qb-main",
            "is_running": False,
            "is_mutating": False,
        }]
        torra.rules = list(rules if rules is not None else [self._shadow_rule()])

    @staticmethod
    def _shadow_rule():
        return {
            "id": "anime-rule",
            "name": "Anime rule",
            "media_type": "tv",
            "category": ["tv::anime"],
            "videoFormat": {
                "blacklist": [],
                "whitelist": [],
                "screen_2160p": {"name": "2160p", "pattern": "2160p", "score": 10},
            },
            "videoFormat_weight": 2,
            "file_extension": {
                "blacklist": [],
                "whitelist": [],
                "mkv": {"name": "MKV", "pattern": r"\.mkv$", "score": 2},
            },
            "file_extension_weight": 1,
            "custom_attributes": [
                {"name": "WEB-DL", "pattern": "WEB[ ._-]*DL", "score": 3},
            ],
            "custom_weight": 1,
            "file_size_score": 5,
            "file_size_weight": 1,
            "always_override_weight": 0,
            "version_control_enabled": True,
            "version_control_entries": [{
                "kind": "local",
                "version": {
                    "name": "MKV version",
                    "include_conditions": [{
                        "attribute": "file_extension",
                        "values": ["mkv"],
                        "match_mode": "any",
                    }],
                    "exclude_conditions": [],
                },
            }],
        }

    def _insert(
        self,
        title,
        media_type="tv",
        season=1,
        start=1,
        end=1,
        published_at="",
        size_bytes=0,
        download_url="",
    ):
        return self.rss.upsert_items(
            self.source["id"],
            [{
                "fingerprint": title + published_at,
                "title": title,
                "published_at": published_at,
                "media_type": media_type,
                "season_number": season if media_type == "tv" else None,
                "episode_start": start if media_type == "tv" else None,
                "episode_end": end if media_type == "tv" else None,
                "size_bytes": size_bytes,
                "download_url": download_url,
            }],
            on_insert=self.runtime.match_inserted_rows,
        )

    def _prepare_ready_exact_download(self, *, confirm_added_task=True):
        self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        qb = FakeQb()
        qb.confirm_added_task = confirm_added_task
        torra, qb = self._enable_analysis(
            qb=qb,
            environment={
                "MCC_PRIVATE_RSS_ENABLED": "true",
                "MCC_TORRA_QUALITY_WATCH_ENABLED": "true",
                "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "true",
                "TORRA_DOWNLOADER_ID": "qb-main",
            },
            config={
                "torra_quality_watch_enabled": True,
                "torra_quality_execution_mode": "manual",
                "torra_quality_min_interval_minutes": 60,
                "torra_quality_hourly_limit": 4,
                "torra_quality_daily_limit": 30,
            },
        )
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [{
            "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
            "size": 2_000_000_000,
            "status": "completed",
        }]
        inserted = self._insert(
            "Test Show S01E02 2160p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
            download_url="https://tracker.example/download?passkey=private",
        )
        self.runtime.wake_matches(inserted["_match_ids"])
        group = self.rss.list_candidate_artifact_groups(
            match_id=inserted["_match_ids"][0], limit=1
        )["groups"][0]
        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(group["id"])
        self.assertTrue(preview["ready"])
        return torra, qb, group, preview

    def test_matches_only_active_episode_and_deduplicates_repeated_item(self):
        first = self._watch("tv:202:s1", episode=3)
        second = self._watch("tv:202:s1", episode=4)
        inserted = self._insert("[Group] 测试剧.S01E03-E04.2160p", start=3, end=4)

        self.assertEqual(inserted["inserted"], 1)
        matches = self.rss.list_matches()["items"]
        self.assertEqual({match["unitId"] for match in matches}, {first["unit_key"], second["unit_key"]})
        self.assertEqual({match["status"] for match in matches}, {"candidate"})
        seed = self.rss.search_items(query="测试剧")["items"][0]
        self.assertEqual(seed["tmdbId"], "202")
        self.assertEqual(seed["identitySource"], "subscription_match")
        self.assertEqual(seed["identityConfidence"], "fallback")
        repeated = self._insert("[Group] 测试剧.S01E03-E04.2160p", start=3, end=4)
        self.assertEqual(repeated["inserted"], 0)
        self.assertEqual(self.rss.list_matches()["total"], 2)

    def test_historical_match_batches_rotate_past_unmatched_items(self):
        self.rss.upsert_items(self.source["id"], [
            {
                "fingerprint": f"unmatched-{number}",
                "title": f"未匹配条目 {number}",
                "published_at": f"2026-07-18T00:0{number}:00Z",
            }
            for number in range(1, 4)
        ])
        first_batch = self.rss.list_items_for_match(2)
        first_ids = {item["id"] for item in first_batch}
        unscanned_id = next(
            item["id"]
            for item in self.rss.list_items_for_match(3)
            if item["id"] not in first_ids
        )

        result = self.runtime.match_existing_items(2)
        second_batch_ids = {
            item["id"] for item in self.rss.list_items_for_match(2)
        }

        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["uncheckedRemaining"], 1)
        self.assertEqual(result["remaining"], 3)
        self.assertIn(unscanned_id, second_batch_ids)

    def test_torra_subscription_scores_initial_rss_candidates_without_watch_units(self):
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        for title, fingerprint in (
            ("测试剧.S01E03.1080p.WEB-DL.mkv", "initial-low"),
            ("测试剧.S01E03.2160p.WEB-DL.mkv", "initial-high"),
        ):
            self.rss.upsert_items(self.source["id"], [{
                "fingerprint": fingerprint,
                "title": title,
                "published_at": "2026-07-18T01:00:00Z",
                "media_type": "tv",
                "season_number": 1,
                "episode_start": 3,
                "episode_end": 3,
                "tmdb_id": "202",
                "identity_status": "identified",
                "size_bytes": 4 * 1024 * 1024 * 1024,
            }], on_insert=self.runtime.match_inserted_rows)

        matches = self.rss.list_matches()["items"]
        self.assertEqual(len(matches), 2)
        self.assertEqual(self.watch.list_candidate_watch_units(self.now[0]), [])

        evaluated = self.runtime.evaluate_matches([match["id"] for match in matches])
        self.assertEqual({match["evaluationStatus"] for match in evaluated}, {"scored"})
        best = next(match for match in evaluated if match["bestCandidate"])
        self.assertEqual(best["decision"], "best_available")
        self.assertIn("2160p", best["candidateSummary"]["versionSummary"])
        self.assertTrue(best["torraLinked"])
        self.assertEqual(best["baselineScore"], None)

        groups = self.rss.list_candidate_groups()["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["state"], "initial_best")
        self.assertEqual(groups[0]["candidateCount"], 2)
        artifact_groups = self.rss.list_candidate_artifact_groups()["groups"]
        self.assertEqual(len(artifact_groups), 2)
        self.assertIn("initial_best", {group["state"] for group in artifact_groups})
        self.assertNotIn("upgrade_available", {group["state"] for group in artifact_groups})
        for group in artifact_groups:
            preview = self.runtime.preview_artifact_exact_download(group["id"])[0]
            self.assertFalse(preview["ready"])
            self.assertIn(
                "RSS_EXACT_BASELINE_UNCONFIRMED",
                {row["code"] for row in preview["blockers"]},
            )
        self.assertEqual(self.watch.list_candidate_watch_units(self.now[0]), [])

    def test_rss_candidate_without_file_extension_is_ranked_but_not_downloadable(self):
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        inserted = self._insert(
            "Test Show S01E03 2160p WEB-DL",
            start=3,
            end=3,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
            download_url="https://tracker.example/download?passkey=private",
        )

        self.runtime.wake_matches(inserted["_match_ids"])
        evaluated = self.rss.list_matches_by_ids(inserted["_match_ids"])

        self.assertEqual(len(evaluated), 1)
        match = evaluated[0]
        self.assertEqual(match["evaluationStatus"], "scored")
        self.assertEqual(match["evaluationReason"], "version_fields_unconfirmed")
        self.assertEqual(match["candidateScore"], 28.0)
        self.assertEqual(match["candidateSummary"]["versionState"], "unconfirmed")
        self.assertEqual(match["decision"], "best_available")
        self.assertTrue(match["bestCandidate"])

        preview = self.runtime.preview_exact_download(match["id"])
        blocker_codes = [row["code"] for row in preview["blockers"]]
        self.assertIn("RSS_EXACT_VERSION_UNCONFIRMED", blocker_codes)
        self.assertNotIn("tracker.example", str(preview))
        self.assertNotIn("private", str(preview))
        self.assertEqual(torra.submissions, [])

    def test_torra_subscription_range_uses_one_artifact_across_explicit_episodes(self):
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["name"] = "测试剧"
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "initial-range",
            "title": "测试剧.S01E03-E04.2160p.WEB-DL.mkv",
            "published_at": "2026-07-18T01:00:00Z",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 3,
            "episode_end": 4,
            "tmdb_id": "202",
            "identity_status": "identified",
            "size_bytes": 8 * 1024 * 1024 * 1024,
        }], on_insert=self.runtime.match_inserted_rows)

        matches = self.rss.list_matches()["items"]
        evaluated = self.runtime.evaluate_matches([match["id"] for match in matches])

        self.assertEqual(len(evaluated), 2)
        self.assertEqual(len({match["artifactKey"] for match in evaluated}), 1)
        self.assertEqual({match["decision"] for match in evaluated}, {"best_available"})
        self.assertEqual({match["bestCandidate"] for match in evaluated}, {True})
        self.assertEqual(
            {match["reason"]["episode"]["unit"] for match in evaluated},
            {3, 4},
        )

    def test_torra_subscription_can_supplement_tmdb_for_imdb_identified_item(self):
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["name"] = "测试剧"
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "imdb-only-candidate",
            "title": "测试剧.S01E03.2160p.WEB-DL.mkv",
            "published_at": "2026-07-18T01:00:00Z",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 3,
            "episode_end": 3,
            "imdb_id": "tt1234567",
            "identity_status": "identified",
            "identity_source": "rss_description",
        }], on_insert=self.runtime.match_inserted_rows)

        item = self.rss.search_items(query="测试剧")["items"][0]
        self.assertEqual(item["imdbId"], "tt1234567")
        self.assertEqual(item["tmdbId"], "202")
        self.assertEqual(item["identitySource"], "torra_subscription_match")
        self.assertEqual(self.rss.list_matches()["total"], 1)

    def test_executable_candidate_requires_bound_unique_strict_upgrade_for_episode(self):
        unit = self._watch("tv:202:s1", episode=3)
        self._insert("[Group] 测试剧.S01E03.2160p", start=3, end=3)
        match = self.rss.list_matches()["items"][0]
        self.rss.set_match_binding(
            match["id"],
            torra_subscription_id="torra-202",
            target_key="tv:tmdb:202:season:1:episode:3",
            artifact_key="rss:artifact-3",
        )
        self.rss.save_match_evaluation([match["id"]], {
            "candidateScore": 80,
            "baselineScore": 60,
            "status": "scored",
            "decision": "upgrade_available",
        })
        self.rss.save_candidate_decisions([{
            "matchIds": [match["id"]],
            "decision": "current_best",
            "reason": "strict_upgrade",
            "bestCandidate": True,
        }])

        self.assertTrue(self.runtime.has_executable_candidate(
            "tv:202:s1",
            media_type="tv",
            season_number=1,
            episode_numbers=[3],
            torra_subscription_id="torra-202",
        ))
        self.assertFalse(self.runtime.has_executable_candidate(
            "tv:202:s1",
            media_type="tv",
            season_number=1,
            episode_numbers=[4],
            torra_subscription_id="torra-202",
        ))
        self.assertEqual(unit["episode_number"], 3)

    def test_aliases_years_and_media_conflicts_are_conservative(self):
        self._watch("movie:2020", media_type="movie", tmdb_id="20", season=None, year="2020")
        self._watch("movie:2021", media_type="movie", tmdb_id="21", season=None, year="2021")
        self._insert("同名电影", media_type="movie", season=None)
        self.assertEqual(self.rss.list_matches()["total"], 0)
        self._insert("同名电影 2021", media_type="movie", season=None)
        self.assertEqual(self.rss.list_matches()["total"], 1)

        self._watch("tv:alias", tmdb_id="", episode=1, aliases=["Alias Show"], title="主标题")
        self._insert("[制作组] Alias.Show.S01E01.1080p")
        self.assertEqual(self.rss.list_matches()["total"], 2)

        self._insert("测试剧 S01E01", media_type="movie", season=None)
        self.assertEqual(self.rss.list_matches()["total"], 2)

    def test_unique_alias_match_writes_subscription_identity(self):
        self._watch("tv:alias", tmdb_id="303", episode=1, aliases=["Alias Show"], title="主标题")
        self._insert("[制作组] Alias.Show.S01E01.1080p")

        item = self.rss.search_items(query="Alias Show")["items"][0]
        self.assertEqual(item["identityStatus"], "identified")
        self.assertEqual(item["tmdbId"], "303")
        self.assertEqual(item["identitySource"], "subscription_match")

    def test_manual_match_revalidates_item_unit_and_torra_ownership(self):
        unit = self._watch("tv:202:s1", episode=1)
        torra, _qb = self._enable_analysis()
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "manual-match",
            "title": "测试剧 S01E01 2160p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "202",
            "identity_status": "identified",
        }])
        item = self.rss.search_items(query="测试剧")["items"][0]

        created = self.runtime.create_manual_match(item["id"], "tv:202:s1", unit["unit_key"])
        repeated = self.runtime.create_manual_match(item["id"], "tv:202:s1", unit["unit_key"])

        self.assertEqual(created["status"], "created")
        self.assertEqual(repeated["status"], "existing")
        self.assertEqual(created["match"]["reason"]["matchSource"], "manual")

        incompatible_rows = [
            {"id": "torra-202", "media_type": "movie"},
            {"id": "torra-202", "media_type": "tv", "season_number": 2},
            {
                "id": "torra-202",
                "media_type": "tv",
                "season_number": 1,
                "name": "Completely Unrelated Show",
            },
        ]
        for torra_row in incompatible_rows:
            with self.subTest(torra_row=torra_row):
                torra.rows = [torra_row]
                rejected = self.runtime.create_manual_match(
                    item["id"], "tv:202:s1", unit["unit_key"]
                )
                self.assertEqual(rejected["status"], "invalid")
                self.assertEqual(rejected["reason"], "torra_subscription_owner_mismatch")

        wrong_owner = self.runtime.create_manual_match(item["id"], "tv:other", unit["unit_key"])
        self.assertEqual(wrong_owner["reason"], "subscription_missing")

    def test_manual_match_uses_canonical_tmdb_title_for_single_sided_tmdb(self):
        unit = self._watch(
            "tv:202:canonical",
            episode=1,
            title="本地展示标题",
            tmdb_title="Canonical Show",
        )
        torra, _qb = self._enable_analysis()
        torra.rows = [{
            "id": "torra-202",
            "name": "Canonical Show",
            "media_type": "tv",
            "season_number": 1,
            "is_running": False,
            "is_mutating": False,
        }]
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "manual-canonical-title",
            "title": "本地展示标题 S01E01 2160p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "202",
            "identity_status": "identified",
        }])
        item = self.rss.search_items(query="本地展示标题")["items"][0]

        created = self.runtime.create_manual_match(
            item["id"], "tv:202:canonical", unit["unit_key"]
        )

        self.assertEqual(created["status"], "created")
        torra.rows[0]["name"] = "Unrelated Show"
        rejected = self.runtime.create_manual_match(
            item["id"], "tv:202:canonical", unit["unit_key"]
        )
        self.assertEqual(rejected, {
            "status": "invalid",
            "reason": "torra_subscription_owner_mismatch",
        })

    def test_manual_match_supports_torra_only_subscription(self):
        torra, _qb = self._enable_analysis()
        torra.rows = [{
            "id": "torra-202",
            "name": "测试剧",
            "media_type": "tv",
            "tmdb_id": "202",
            "season_number": 1,
            "is_running": False,
            "is_mutating": False,
        }]
        unit = self.watch.ensure_watch_unit(
            "torra:torra-202", "tv", 1, 1, window_hours=48, torra_subscription_id="torra-202"
        )
        unit = self.watch.mark_baseline_ready(unit["unit_key"])
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "manual-torra-only",
            "title": "测试剧 S01E01 2160p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "202",
            "identity_status": "identified",
        }])
        item = self.rss.search_items(query="测试剧")["items"][0]
        public_key = torra_public_subscription_key("torra-202")
        public_unit_key = unit["unit_key"].replace("torra:torra-202", public_key, 1)

        result = self.runtime.create_manual_match(
            item["id"], public_key, public_unit_key
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["match"]["subscriptionId"], public_key)
        self.assertEqual(result["match"]["unitId"], public_unit_key)
        self.assertNotIn("torra-202", str(result["match"]))

        analysis = self.runtime.start_analysis(
            result["match"]["id"],
            idempotency_key="torra-public-rss-analysis",
            source="manual-rss",
            require_rss_gate=False,
        )
        self.assertEqual(analysis["status"], "submitted")
        self.assertEqual(torra.submissions, ["torra-202"])
        action = self.watch.get_action(analysis["actionId"])
        self.assertEqual(action["subscription_key"], "torra:torra-202")
        self.assertEqual(action["unit_key"], unit["unit_key"])

        missing = self.runtime.create_manual_match(
            item["id"], "torra:missing", public_unit_key
        )
        self.assertEqual(missing, {"status": "missing", "reason": "subscription_missing"})

        collision_id = public_key.removeprefix("torra:")
        torra.rows.append({
            "id": collision_id,
            "name": "冲突剧",
            "media_type": "tv",
            "tmdb_id": "999",
            "season_number": 1,
        })
        conflict = self.runtime.create_manual_match(item["id"], public_key, public_unit_key)
        self.assertEqual(conflict, {
            "status": "conflict",
            "reason": "torra_subscription_key_conflict",
        })

    def test_legacy_raw_torra_match_projects_existing_but_actions_keep_raw_keys(self):
        remote_id = "legacy-torra-runtime-secret"
        raw_key = f"torra:{remote_id}"
        unit = self._watch(
            raw_key,
            episode=1,
            torra_id=remote_id,
            origin="torra",
            read_only=True,
            torra_remote_id=remote_id,
        )
        torra, _qb = self._enable_analysis()
        torra.rows = [{
            "id": remote_id,
            "name": "测试剧",
            "media_type": "tv",
            "tmdb_id": "202",
            "season_number": 1,
            "is_running": False,
            "is_mutating": False,
        }]
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "legacy-torra-runtime-match",
            "title": "测试剧 S01E01 2160p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "202",
            "identity_status": "identified",
        }])
        item = self.rss.search_items(query="测试剧")["items"][0]
        stored = self.rss.create_match(
            item["id"], raw_key, unit["unit_key"], {"identity": {"basis": "title"}}
        )
        public_key = torra_public_subscription_key(remote_id)
        public_unit_key = unit["unit_key"].replace(raw_key, public_key, 1)

        existing = self.runtime.create_manual_match(item["id"], public_key, public_unit_key)
        analysis = self.runtime.start_analysis(
            stored["id"],
            idempotency_key="legacy-torra-raw-action",
            source="manual-rss",
            require_rss_gate=False,
        )

        self.assertEqual(existing["status"], "existing")
        self.assertEqual(existing["match"]["subscriptionId"], public_key)
        self.assertEqual(existing["match"]["unitId"], public_unit_key)
        self.assertNotIn(remote_id, str(existing["match"]))
        self.assertEqual(analysis["status"], "submitted")
        self.assertEqual(torra.submissions, [remote_id])
        persisted = self.rss.get_match(stored["id"])
        self.assertEqual(persisted["subscriptionId"], raw_key)
        self.assertEqual(persisted["unitId"], unit["unit_key"])
        action = self.watch.get_action(analysis["actionId"])
        self.assertEqual(action["subscription_key"], raw_key)
        self.assertEqual(action["unit_key"], unit["unit_key"])
        self.assertEqual(action["external_job_id"], "job-1")

    def test_manual_match_supports_new_hashed_local_torra_mirror_key(self):
        torra, _qb = self._enable_analysis()
        remote_id = "torra-hashed-mirror"
        public_key = torra_public_subscription_key(remote_id)
        torra.rows = [{
            "id": remote_id,
            "name": "哈希镜像剧",
            "media_type": "tv",
            "tmdb_id": "909",
            "season_number": 1,
            "is_running": False,
            "is_mutating": False,
        }]
        self.subscriptions.append({
            "key": public_key,
            "subscription_key": public_key,
            "title": "哈希镜像剧",
            "media_type": "tv",
            "tmdb_id": "909",
            "target_season": 1,
            "origin": "torra",
            "read_only": True,
            "torra_remote_id": remote_id,
        })
        canonical_key = f"torra:{remote_id}"
        unit = self.watch.ensure_watch_unit(
            canonical_key,
            "tv",
            1,
            1,
            window_hours=48,
            torra_subscription_id=remote_id,
        )
        unit = self.watch.mark_baseline_ready(unit["unit_key"])
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "manual-torra-hashed-mirror",
            "title": "哈希镜像剧 S01E01 2160p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "909",
            "identity_status": "identified",
        }])
        item = self.rss.search_items(query="哈希镜像剧")["items"][0]

        result = self.runtime.create_manual_match(item["id"], public_key, unit["unit_key"])

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["match"]["subscriptionId"], public_key)
        self.assertEqual(
            result["match"]["unitId"],
            unit["unit_key"].replace(canonical_key, public_key, 1),
        )
        self.assertNotIn(remote_id, str(result["match"]))
        stored = self.rss.get_match_internal(result["match"]["id"])
        self.assertEqual(stored["subscription_key"], canonical_key)
        self.assertEqual(stored["unit_key"], unit["unit_key"])
        analysis = self.runtime.start_analysis(
            result["match"]["id"],
            idempotency_key="torra-hashed-rss-analysis",
            source="manual-rss",
            require_rss_gate=False,
        )
        self.assertEqual(analysis["status"], "submitted")
        self.assertEqual(torra.submissions, [remote_id])

    def test_manual_match_rejects_at_observation_deadline(self):
        unit = self._watch("tv:202:s1", episode=1)
        unit = self.watch.get_watch_unit(unit["unit_key"])
        self._enable_analysis()
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "manual-match-window-deadline",
            "title": "测试剧 S01E01 2160p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "202",
            "identity_status": "identified",
        }])
        item = self.rss.search_items(query="测试剧")["items"][0]
        self.now[0] = datetime.fromisoformat(
            unit["observation_ends_at"].replace("Z", "+00:00")
        )

        result = self.runtime.create_manual_match(
            item["id"], "tv:202:s1", unit["unit_key"]
        )

        self.assertEqual(result, {"status": "blocked", "reason": "watch_unit_inactive"})
        self.assertEqual(self.rss.list_matches()["total"], 0)

    def test_torra_remote_subscription_identifies_rss_without_local_mirror(self):
        torra, _qb = self._enable_analysis()
        torra.rows = [{
            "id": "torra-daredevil-s2",
            "name": "夜魔侠：重生",
            "media_type": "tv",
            "tmdb_id": 202555,
            "names_json": '["夜魔侠：重生", "Daredevil: Born Again"]',
            "year": "2025",
            "season_years_json": '{"1": "2025", "2": "2026"}',
            "season_number": 2,
        }]

        self._insert("Daredevil.Born.Again.S02E03.2026.2160p.WEB-DL", season=2, start=3, end=3)

        item = self.rss.search_items(query="Daredevil Born Again")["items"][0]
        self.assertEqual(item["tmdbId"], "202555")
        self.assertEqual(item["identityStatus"], "identified")
        self.assertEqual(item["identitySource"], "torra_subscription_match")
        self.assertEqual(self.rss.list_matches()["total"], 0)

    def test_torra_remote_alias_conflict_does_not_guess(self):
        torra, _qb = self._enable_analysis()
        torra.rows = [{
            "id": "torra-conflict-1",
            "name": "甲剧",
            "media_type": "tv",
            "tmdb_id": 1001,
            "names_json": '["Shared Show"]',
            "season_number": 1,
        }, {
            "id": "torra-conflict-2",
            "name": "乙剧",
            "media_type": "tv",
            "tmdb_id": 1002,
            "names_json": '["Shared Show"]',
            "season_number": 1,
        }]

        self._insert("Shared.Show.S01E01.1080p")

        item = self.rss.search_items(query="Shared Show")["items"][0]
        self.assertEqual(item["identityStatus"], "conflict")
        self.assertEqual(item["tmdbId"], "")

    def test_bounded_identity_backfill_uses_explicit_ids_and_unique_subscription(self):
        self.subscriptions.append({
            "key": "tv:unique:s1",
            "title": "Unique Show",
            "media_type": "tv",
            "tmdb_id": "808",
            "target_season": 1,
        })
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "explicit-imdb",
            "title": "Archive Movie 2024",
            "description": "Public metadata: https://www.imdb.com/title/tt1234567/",
            "media_type": "movie",
        }, {
            "fingerprint": "unique-follow",
            "title": "Unique Show S01E02 1080p",
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 2,
            "episode_end": 2,
        }])

        result = self.runtime.backfill_unidentified_items(limit=2)

        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["identified"], 2)
        explicit = self.rss.search_items(query="Archive Movie")["items"][0]
        unique = self.rss.search_items(query="Unique Show")["items"][0]
        self.assertEqual(explicit["imdbId"], "tt1234567")
        self.assertEqual(explicit["identitySource"], "rss_description")
        self.assertEqual(unique["tmdbId"], "808")
        self.assertEqual(unique["identitySource"], "subscription_match")
        summary = self.rss.summary(enabled=True)
        self.assertTrue(summary["identityBackfillRan"])
        self.assertEqual(summary["lastIdentityBackfillScanned"], 2)
        self.assertEqual(summary["lastIdentityBackfillIdentified"], 2)

    def test_identity_backfill_marks_multiple_reliable_targets_as_conflict(self):
        self.subscriptions.extend([{
            "key": "tv:conflict-a:s1", "title": "Conflict Show", "media_type": "tv",
            "tmdb_id": "901", "target_season": 1,
        }, {
            "key": "tv:conflict-b:s1", "title": "Conflict Show", "media_type": "tv",
            "tmdb_id": "902", "target_season": 1,
        }])
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "conflict-follow", "title": "Conflict Show S01E01",
            "media_type": "tv", "season_number": 1,
        }])

        result = self.runtime.backfill_unidentified_items(limit=1)

        self.assertEqual(result["conflicts"], 1)
        item = self.rss.search_items(query="Conflict Show")["items"][0]
        self.assertEqual(item["identityStatus"], "conflict")
        self.assertEqual(item["tmdbId"], "")

    def test_backfill_rotates_past_unmatched_rows_and_repairs_legacy_scope(self):
        torra, _qb = self._enable_analysis()
        torra.rows = [{
            "id": "torra-variety",
            "name": "爱情保卫战",
            "media_type": "tv",
            "tmdb_id": 909,
            "season_number": 2026,
            "names_json": '["爱情保卫战", "Ai Qing Bao Wei Zhan"]',
            "year": "2026",
        }]
        for index in range(2):
            self.rss.upsert_items(self.source["id"], [{
                "fingerprint": f"unmatched-{index}",
                "title": f"Unknown Release {index}",
                "media_type": "movie",
            }])
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "legacy-variety",
            "title": "Ai Qing Bao Wei Zhan S2026E70 1080p",
            "category": "综艺",
            "media_type": "movie",
        }])
        with self.rss.runtime.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE rss_items SET identity_updated_at='2000-01-01T00:00:00Z' "
                "WHERE fingerprint LIKE 'unmatched-%'"
            )
            connection.execute(
                "UPDATE rss_items SET identity_updated_at='2001-01-01T00:00:00Z' "
                "WHERE fingerprint='legacy-variety'"
            )

        first = self.runtime.backfill_unidentified_items(limit=2)
        second = self.runtime.backfill_unidentified_items(limit=2)

        self.assertEqual(first["identified"], 0)
        self.assertEqual(second["identified"], 1)
        item = self.rss.search_items(query="Ai Qing Bao Wei Zhan")["items"][0]
        self.assertEqual(item["mediaType"], "tv")
        self.assertEqual(item["episodeStart"], 70)
        self.assertEqual(item["tmdbId"], "909")
        self.assertEqual(item["identitySource"], "torra_subscription_match")

    def test_expired_and_pre_baseline_items_are_not_backfilled(self):
        self._watch("tv:202:s1", episode=1)
        old = (self.now[0] - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        self._insert("测试剧 S01E01", published_at=old)
        self.assertEqual(self.rss.list_matches()["total"], 0)

        self.now[0] += timedelta(hours=49)
        self._insert("测试剧 S01E01", published_at=self.now[0].isoformat().replace("+00:00", "Z"))
        self.assertEqual(self.rss.list_matches()["total"], 0)

    def test_analysis_preflight_blocks_gates_torra_and_related_qb_without_writes(self):
        self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        torra, qb = self._enable_analysis(environment={})
        disabled = self.runtime.start_analysis(match["id"])
        self.assertEqual(disabled, {"status": "blocked", "reason": "rss_disabled"})

        self.runtime.analysis = RssAnalysisDependencies(
            {"MCC_PRIVATE_RSS_ENABLED": "true", "MCC_TORRA_QUALITY_WATCH_ENABLED": "true"},
            torra,
            qb,
            lambda: {"torra_quality_watch_enabled": True},
        )
        torra.rows[0]["is_running"] = True
        self.assertEqual(self.runtime.start_analysis(match["id"])["reason"], "torra_busy")
        torra.rows[0]["is_running"] = False
        qb.tasks = [{"name": "测试剧.S01E01.mkv", "status": "downloading"}]
        self.assertEqual(self.runtime.start_analysis(match["id"])["reason"], "qb_busy")
        self.assertEqual(torra.submissions, [])

    def test_manual_analysis_supports_torra_subscription_target_without_watch_unit(self):
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        self._insert(
            "Test Show S01E20 2160p WEB-DL.mkv",
            start=20,
            end=20,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=5_600_000_000,
        )
        match = self.rss.list_matches()["items"][0]

        result = self.runtime.start_analysis(
            match["id"],
            idempotency_key="manual-target-only-analysis",
            source="manual-rss",
            require_rss_gate=False,
        )

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(torra.submissions, ["torra-202"])
        self.assertEqual(self.watch.list_watch_units("torra:torra-202"), [])
        action = self.watch.get_action(result["actionId"])
        self.assertEqual(action["subscription_key"], "torra:torra-202")
        self.assertEqual(action["unit_key"], "torra:torra-202:s1:e20")

    def test_manual_analysis_target_without_watch_unit_preserves_qb_duplicate_check(self):
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        qb.tasks = [{
            "name": "Test Show S01E20 2160p WEB-DL.mkv",
            "status": "queued",
        }]
        self._insert(
            "Test Show S01E20 2160p WEB-DL.mkv",
            start=20,
            end=20,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=5_600_000_000,
        )
        match = self.rss.list_matches()["items"][0]

        result = self.runtime.start_analysis(
            match["id"],
            idempotency_key="manual-target-only-qb-duplicate",
            source="manual-rss",
            require_rss_gate=False,
        )

        self.assertEqual(result, {"status": "blocked", "reason": "qb_busy"})
        self.assertEqual(torra.submissions, [])

    def test_manual_analysis_target_without_watch_unit_rechecks_torra_identity(self):
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        self._insert(
            "Test Show S01E20 2160p WEB-DL.mkv",
            start=20,
            end=20,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=5_600_000_000,
        )
        match = self.rss.list_matches()["items"][0]
        torra.rows[0]["tmdb_id"] = "303"

        result = self.runtime.start_analysis(
            match["id"],
            idempotency_key="manual-target-only-identity-changed",
            source="manual-rss",
            require_rss_gate=False,
        )

        self.assertEqual(result, {"status": "blocked", "reason": "identity_unconfirmed"})
        self.assertEqual(torra.submissions, [])

    def test_analysis_submits_once_and_restart_polls_original_job(self):
        self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        torra, qb = self._enable_analysis()
        torra.jobs = [{"status": "success", "result": {"analysis_id": "analysis-1", "rows": []}}]

        submitted = self.runtime.start_analysis(match["id"])
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(torra.submissions, ["torra-202"])
        self.assertEqual(self.runtime.start_analysis(match["id"])["status"], "in_progress")

        self.now[0] += timedelta(seconds=61)
        restarted = RssSubscriptionMatchRuntime(
            self.rss,
            self.watch,
            lambda: {"items": self.subscriptions},
            clock=lambda: self.now[0],
            analysis=RssAnalysisDependencies(
                {"MCC_PRIVATE_RSS_ENABLED": "true", "MCC_TORRA_QUALITY_WATCH_ENABLED": "true"},
                torra,
                qb,
                lambda: {"torra_quality_watch_enabled": True},
            ),
        )
        completed = restarted.start_analysis(match["id"])
        self.assertEqual(completed["status"], "ignored")
        self.assertEqual(torra.submissions, ["torra-202"])
        self.assertEqual(torra.polls, ["job-1"])
        self.assertEqual(self.rss.get_match(match["id"])["status"], "ignored")

    def test_upgrade_stays_triggered_and_failed_job_is_not_automatically_resubmitted(self):
        self._watch("tv:202:s1", episode=1)
        torra, _qb = self._enable_analysis()
        torra.jobs = [{
            "status": "success",
            "result": {
                "analysis_id": "analysis-upgrade",
                "rows": [{
                    "row_id": "row-1",
                    "library_meta_weight_score": 10,
                    "candidates": [{
                        "candidate_id": "candidate-1",
                        "is_upgrade": True,
                        "meta_weight_score": 20,
                    }],
                }],
            },
        }]
        self._insert("测试剧 S01E01 2160p")
        upgrade = self.rss.list_matches()["items"][0]
        self.runtime.start_analysis(upgrade["id"])
        self.now[0] += timedelta(seconds=61)
        selected = self.runtime.start_analysis(upgrade["id"])
        self.assertEqual(selected["status"], "triggered")
        self.assertEqual(selected["selectedCount"], 1)

        self.now[0] += timedelta(hours=1)
        self._insert("测试剧 S01E01 REMUX")
        failed = next(match for match in self.rss.list_matches()["items"] if match["id"] != upgrade["id"])
        torra.jobs = [{"status": "failed", "result": None}]
        self.runtime.start_analysis(failed["id"])
        self.now[0] += timedelta(seconds=61)
        self.assertEqual(self.runtime.start_analysis(failed["id"])["status"], "failed")
        submission_count = len(torra.submissions)
        self.assertEqual(self.runtime.start_analysis(failed["id"])["status"], "replay")
        self.assertEqual(len(torra.submissions), submission_count)

    def test_terminal_action_replay_repairs_match_state_after_crash(self):
        unit = self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        self._enable_analysis()
        claimed = self.watch.claim_action(
            f"rss-rewash-analysis:{match['id']}",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
        )
        self.watch.complete_action(
            claimed["action"]["action_id"],
            "succeeded",
            {"selectedCount": 0, "rowCount": 1},
        )

        replayed = self.runtime.start_analysis(match["id"])

        self.assertEqual(replayed["status"], "replay")
        self.assertEqual(self.rss.get_match(match["id"])["status"], "ignored")

    def test_reclaimed_analysis_cancels_expired_context_at_deadline(self):
        unit = self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        self._enable_analysis()
        idempotency_key = "manual-rss-reclaim-window-expired"
        action = self.watch.claim_action(
            idempotency_key,
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )["action"]
        stored_unit = self.watch.get_watch_unit(unit["unit_key"])
        self.now[0] = datetime.fromisoformat(
            stored_unit["observation_ends_at"].replace("Z", "+00:00")
        )

        result = self.runtime.start_analysis(
            match["id"],
            idempotency_key=idempotency_key,
            source="manual-rss",
            require_rss_gate=False,
        )

        self.assertEqual(result, {"status": "blocked", "reason": "window_expired"})
        stored = self.watch.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "RSS_REWASH_WINDOW_EXPIRED")
        self.assertEqual(stored["response_summary"]["contextReason"], "window_expired")
        self.assertIsNone(self.watch.find_inflight_action("torra", "rewash-analysis"))

    def test_reclaimed_analysis_cancels_missing_watch_unit(self):
        self._enable_analysis()
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "rss-reclaim-missing-unit",
            "title": "测试剧 S01E01 2160p",
        }])
        item = self.rss.search_items()["items"][0]
        match = self.rss.create_match(
            item["id"], "tv:202:s1", "unit:missing", {"identity": {"basis": "title"}}
        )
        idempotency_key = "manual-rss-reclaim-missing-unit"
        action = self.watch.claim_action(
            idempotency_key,
            match["subscriptionId"],
            "torra",
            "rewash-analysis",
            unit_key=match["unitId"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )["action"]
        self.now[0] += timedelta(seconds=61)

        result = self.runtime.start_analysis(
            match["id"], idempotency_key=idempotency_key, source="manual-rss", require_rss_gate=False
        )

        self.assertEqual(result, {"status": "blocked", "reason": "watch_unit_missing"})
        stored = self.watch.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "RSS_REWASH_WATCH_UNIT_MISSING")
        self.assertEqual(stored["response_summary"]["contextReason"], "watch_unit_missing")
        self.assertIsNone(self.watch.find_inflight_action("torra", "rewash-analysis"))

    def test_reclaimed_analysis_cancels_missing_subscription(self):
        unit = self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        self._enable_analysis()
        idempotency_key = "manual-rss-reclaim-missing-subscription"
        action = self.watch.claim_action(
            idempotency_key,
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )["action"]
        self.subscriptions.clear()
        self.now[0] += timedelta(seconds=61)

        result = self.runtime.start_analysis(
            match["id"], idempotency_key=idempotency_key, source="manual-rss", require_rss_gate=False
        )

        self.assertEqual(result, {"status": "blocked", "reason": "subscription_missing"})
        stored = self.watch.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "RSS_REWASH_SUBSCRIPTION_MISSING")
        self.assertEqual(stored["response_summary"]["contextReason"], "subscription_missing")
        self.assertIsNone(self.watch.find_inflight_action("torra", "rewash-analysis"))

    def test_reclaimed_analysis_cancels_missing_torra_owner(self):
        unit = self._watch("tv:202:s1", episode=1, torra_id="")
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        self._enable_analysis()
        idempotency_key = "manual-rss-reclaim-missing-torra-owner"
        action = self.watch.claim_action(
            idempotency_key,
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )["action"]
        self.now[0] += timedelta(seconds=61)

        result = self.runtime.start_analysis(
            match["id"], idempotency_key=idempotency_key, source="manual-rss", require_rss_gate=False
        )

        self.assertEqual(result, {"status": "blocked", "reason": "torra_subscription_missing"})
        stored = self.watch.get_action(action["action_id"])
        self.assertEqual(stored["status"], "cancelled")
        self.assertEqual(stored["error_code"], "RSS_REWASH_TORRA_SUBSCRIPTION_MISSING")
        self.assertEqual(stored["response_summary"]["contextReason"], "torra_subscription_missing")
        self.assertIsNone(self.watch.find_inflight_action("torra", "rewash-analysis"))

    def test_reclaimed_analysis_keeps_temporary_provider_error_retryable(self):
        unit = self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        torra, _qb = self._enable_analysis()
        idempotency_key = "manual-rss-reclaim-torra-busy"
        action = self.watch.claim_action(
            idempotency_key,
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )["action"]
        torra.rows[0]["is_running"] = True
        self.now[0] += timedelta(seconds=61)

        result = self.runtime.start_analysis(
            match["id"], idempotency_key=idempotency_key, source="manual-rss", require_rss_gate=False
        )

        self.assertEqual(result, {"status": "blocked", "reason": "torra_busy"})
        stored = self.watch.get_action(action["action_id"])
        self.assertEqual(stored["status"], "claimed")
        self.assertEqual(
            self.watch.find_inflight_action("torra", "rewash-analysis")["action_id"],
            stored["action_id"],
        )
        self.assertEqual(torra.submissions, [])

    def test_download_preparation_binds_analysis_to_match_and_records_stable_action(self):
        unit = self._watch("tv:202:s1", episode=1)
        self._insert("测试剧 S01E01 2160p")
        match = self.rss.list_matches()["items"][0]
        analysis = self.watch.claim_action(
            "rss-download-analysis-runtime",
            unit["subscription_key"],
            "torra",
            "rewash-analysis",
            unit_key=unit["unit_key"],
            request_summary={"source": "manual-rss", "matchId": match["id"]},
        )["action"]
        self.watch.complete_action(analysis["action_id"], "succeeded", {
            "analysisId": "analysis-runtime",
            "selectedCandidates": {"row-runtime": "candidate-runtime"},
            "selectedCount": 1,
        })
        self.rss.update_match(match["id"], "triggered", analysis["action_id"])

        prepared = self.runtime.prepare_download(
            match["id"], analysis["action_id"], "rss-download-runtime-0001"
        )
        self.assertEqual(prepared["status"], "ready")

        download = self.watch.claim_action(
            "rss-download-runtime-0001",
            unit["subscription_key"],
            "torra",
            "rewash-download",
            unit_key=unit["unit_key"],
            request_summary={
                "source": "manual-subscription",
                "analysisActionId": analysis["action_id"],
            },
        )["action"]
        recorded = self.runtime.record_download(match["id"], analysis["action_id"], download)

        self.assertEqual(recorded["status"], "confirmed")
        stored = self.rss.get_match(match["id"])
        self.assertEqual(stored["status"], "confirmed")
        self.assertEqual(stored["triggerActionId"], download["action_id"])

        other_source = self.rss.save_source({
            "name": "其他 RSS",
            "feedUrl": "https://tracker.example/other.xml",
        })
        self.rss.upsert_items(other_source["id"], [{
            "fingerprint": "rss-download-other",
            "title": "测试剧 S01E01 1080p",
        }])
        other_item = self.rss.search_items(source_id=other_source["id"])["items"][0]
        other_match = self.rss.create_match(
            other_item["id"], unit["subscription_key"], unit["unit_key"], {"identity": {"basis": "title"}}
        )
        rejected = self.runtime.prepare_download(
            other_match["id"], analysis["action_id"], "rss-download-runtime-0002"
        )
        self.assertEqual(rejected["reason"], "analysis_action_missing")

    def test_multiple_rss_candidates_share_one_global_analysis_slot(self):
        self._watch("tv:202:s1", episode=1)
        torra, _qb = self._enable_analysis()
        self._insert("测试剧 S01E01 2160p")
        self._insert("测试剧 S01E01 REMUX")
        matches = self.rss.list_matches(status="candidate")["items"]

        first = self.runtime.start_analysis(matches[0]["id"])
        second = self.runtime.start_analysis(matches[1]["id"])

        self.assertEqual(first["status"], "submitted")
        self.assertEqual(second["status"], "global_busy")
        self.assertEqual(torra.submissions, ["torra-202"])

    def test_range_candidate_shares_one_artifact_and_one_shadow_action(self):
        self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        self._watch(
            "tv:202:s1",
            episode=3,
            title="Test Show",
            media_category="anime",
        )
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)

        inserted = self._insert(
            "Test Show S01E02-E03 2160p WEB-DL.mkv",
            start=2,
            end=3,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
        )
        results = self.runtime.wake_matches(inserted["_match_ids"])
        matches = self.rss.list_matches()["items"]

        self.assertEqual(len(results), 2)
        self.assertEqual(torra.submissions, [])
        self.assertEqual(len({match["artifactKey"] for match in matches}), 1)
        self.assertEqual(
            {match["targetKey"] for match in matches},
            {"tv:tmdb:202:season:1:episodes:2-3"},
        )
        self.assertEqual(len({match["evaluationActionId"] for match in matches}), 1)
        self.assertEqual({match["evaluationStatus"] for match in matches}, {"scored"})
        self.assertEqual({match["decision"] for match in matches}, {"best_waiting_baseline"})
        self.assertEqual({match["candidateScore"] for match in matches}, {30.0})

    def test_shadow_scoring_uses_torra_rule_order_and_does_not_guess_missing_fields(self):
        self._watch(
            "tv:202:s1",
            episode=1,
            title="Test Show",
            media_category="anime",
        )
        torra, _qb = self._enable_analysis()
        duplicate = {**self._shadow_rule(), "id": "anime-rule-duplicate"}
        self._configure_shadow_torra(torra, [self._shadow_rule(), duplicate])
        ambiguous = self._insert(
            "Test Show S01E01 2160p WEB-DL.mkv",
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
        )
        self.runtime.wake_matches(ambiguous["_match_ids"])
        ambiguous_match = self.rss.list_matches()["items"][0]
        self.assertEqual(ambiguous_match["evaluationStatus"], "scored")
        self.assertEqual(ambiguous_match["ruleId"], "anime-rule")
        self.assertEqual(ambiguous_match["candidateScore"], 30.0)

        self.rss.save_match_evaluation([ambiguous_match["id"]], {
            "status": "blocked",
            "decision": "temporarily_unconfirmed",
            "reason": "rule_ambiguous",
        })
        retried = self.runtime.wake_pending_candidates(limit=2)
        retried_match = self.rss.get_match(ambiguous_match["id"])
        self.assertEqual(retried, [{
            "matchId": ambiguous_match["id"],
            "status": "evaluated",
            "reason": "shadow_only_no_download",
        }])
        self.assertEqual(retried_match["evaluationStatus"], "scored")
        self.assertEqual(retried_match["candidateScore"], 30.0)

        self.rss.save_match_evaluation([ambiguous_match["id"]], {
            "status": "blocked",
            "decision": "temporarily_unconfirmed",
            "reason": "version_fields_unconfirmed",
        })
        retried = self.runtime.wake_pending_candidates(limit=2)
        retried_match = self.rss.get_match(ambiguous_match["id"])
        self.assertEqual(retried, [{
            "matchId": ambiguous_match["id"],
            "status": "evaluated",
            "reason": "shadow_only_no_download",
        }])
        self.assertEqual(retried_match["evaluationStatus"], "scored")
        self.assertEqual(retried_match["candidateScore"], 30.0)

        self.now[0] += timedelta(minutes=1)
        self._configure_shadow_torra(torra)
        missing_size = self._insert(
            "Test Show S01E01 2160p WEB-DL.mkv missing size",
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
        )
        self.runtime.wake_matches(missing_size["_match_ids"])
        missing_match = next(
            match
            for match in self.rss.list_matches()["items"]
            if match["id"] in missing_size["_match_ids"]
        )
        self.assertEqual(missing_match["evaluationStatus"], "blocked")
        self.assertEqual(missing_match["evaluationReason"], "candidate_size_unconfirmed")
        self.assertIsNone(missing_match["candidateScore"])

    def test_watch_unit_backfill_uses_first_download_time_and_is_idempotent(self):
        download_started = self.now[0] - timedelta(hours=1)
        self.subscriptions.append({
            "key": "tv:202:s1",
            "title": "Test Show",
            "media_type": "tv",
            "tmdb_id": "202",
            "target_season": 1,
            "media_category": "anime",
        })
        before = (download_started - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        during = (download_started + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "before-first-download",
            "title": "Test Show S01E01 2160p WEB-DL.mkv",
            "published_at": before,
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "size_bytes": 2_000_000_000,
        }, {
            "fingerprint": "during-first-download",
            "title": "Test Show S01E01 2160p WEB-DL.mkv",
            "published_at": during,
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "size_bytes": 2_000_000_000,
        }])
        unit = self.watch.ensure_watch_unit(
            "tv:202:s1",
            "tv",
            1,
            1,
            first_success_at=download_started,
            torra_subscription_id="torra-202",
        )
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)

        first = self.runtime.backfill_watch_unit(unit["unit_key"])
        repeated = self.runtime.backfill_watch_unit(unit["unit_key"])
        matches = self.rss.list_matches()["items"]

        self.assertEqual(first["created"], 1)
        self.assertEqual(first["evaluated"], 1)
        self.assertEqual(repeated["created"], 0)
        self.assertEqual(len(matches), 1)
        matched_item = self.rss.get_item(matches[0]["itemId"], public=False)
        self.assertEqual(matched_item["fingerprint"], "during-first-download")

    def test_artifact_with_multiple_torra_owners_is_blocked(self):
        first = self._watch(
            "tv:202:first",
            episode=1,
            title="Test Show",
            media_category="anime",
            torra_id="torra-202",
        )
        second = self._watch(
            "tv:202:second",
            episode=1,
            title="Test Show",
            media_category="anime",
            torra_id="torra-203",
        )
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows.append({**torra.rows[0], "id": "torra-203"})
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": "owner-conflict",
            "title": "Test Show S01E01 2160p WEB-DL.mkv",
            "published_at": self.now[0].isoformat().replace("+00:00", "Z"),
            "media_type": "tv",
            "season_number": 1,
            "episode_start": 1,
            "episode_end": 1,
            "tmdb_id": "202",
            "identity_status": "identified",
            "size_bytes": 2_000_000_000,
        }])
        item = self.rss.search_items(query="Test Show")["items"][0]
        matches = [
            self.rss.create_match(
                item["id"],
                unit["subscription_key"],
                unit["unit_key"],
                {"identity": {"basis": "tmdb", "tmdbId": "202"}},
            )
            for unit in (first, second)
        ]

        self.runtime.evaluate_matches([match["id"] for match in matches])
        stored = self.rss.list_matches()["items"]

        self.assertEqual({match["decision"] for match in stored}, {"ownership_conflict"})
        self.assertEqual({match["evaluationReason"] for match in stored}, {"artifact_owner_conflict"})
        self.assertEqual({match["candidateScore"] for match in stored}, {None})
        self.assertEqual(torra.submissions, [])

    def test_exact_current_version_becomes_persisted_baseline_and_upgrade(self):
        unit = self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [{
            "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
            "size": 2_000_000_000,
        }]

        inserted = self._insert(
            "Test Show S01E02 2160p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
        )
        self.runtime.wake_matches(inserted["_match_ids"])

        match = self.rss.get_match(inserted["_match_ids"][0])
        stored_unit = self.watch.get_watch_unit(unit["unit_key"])
        self.assertEqual(match["baselineScore"], 10.0)
        self.assertEqual(match["candidateScore"], 30.0)
        self.assertEqual(match["decision"], "current_best")
        self.assertTrue(match["bestCandidate"])
        self.assertEqual(match["baselineSummary"]["versionSummary"], "Test.Show.S01E02.1080p.WEB-DL.mkv")
        self.assertTrue(stored_unit["baseline_artifact_key"].startswith("baseline:"))
        self.assertEqual(stored_unit["baseline_score"], 10.0)
        self.assertEqual(stored_unit["best_match_id"], match["id"])
        self.assertEqual(stored_unit["best_candidate_score"], 30.0)

    def test_exact_download_preview_returns_ready_without_submitting(self):
        self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [{
            "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
            "size": 2_000_000_000,
            "status": "completed",
        }]
        inserted = self._insert(
            "Test Show S01E02 2160p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
            download_url="https://tracker.example/download?passkey=private",
        )
        self.runtime.wake_matches(inserted["_match_ids"])

        preview = self.runtime.preview_exact_download(inserted["_match_ids"][0])

        self.assertTrue(preview["ready"])
        self.assertEqual(preview["capabilityState"], "ready")
        self.assertEqual(preview["candidateScore"], 30.0)
        self.assertEqual(preview["baselineScore"], 10.0)
        self.assertEqual(preview["scoreGain"], 20.0)
        self.assertEqual(preview["blockers"], [])
        self.assertTrue(preview["previewToken"])
        self.assertEqual(preview["downloadCategory"], "anime")
        self.assertNotIn("tracker.example", str(preview))
        self.assertNotIn("private", str(preview))
        self.assertEqual(torra.submissions, [])

        stored = self.rss.get_match(inserted["_match_ids"][0])
        self.rss.set_match_binding(
            stored["id"],
            torra_subscription_id="torra-202",
            target_key="tv:tmdb:202:season:1:episodes:3-3",
            artifact_key=stored["artifactKey"],
        )
        changed = self.runtime.preview_exact_download(stored["id"])
        self.assertIn("RSS_EXACT_TARGET_CHANGED", [row["code"] for row in changed["blockers"]])
        self.assertEqual(torra.submissions, [])

    def test_exact_download_preview_blocks_when_internal_context_is_missing(self):
        self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        torra, _qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        inserted = self._insert(
            "Test Show S01E02 2160p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
            download_url="https://tracker.example/download?passkey=private",
        )
        self.rss.get_match_internal = lambda _match_id: None

        preview = self.runtime.preview_exact_download(inserted["_match_ids"][0])

        self.assertFalse(preview["ready"])
        self.assertEqual(
            [row["code"] for row in preview["blockers"]],
            [
                "RSS_EXACT_ARTIFACT_NOT_UNIQUE_WINNER",
                "RSS_EXACT_SCORE_UNCONFIRMED",
                "RSS_EXACT_VERSION_UNCONFIRMED",
                "RSS_EXACT_NOT_CURRENT_BEST",
                "RSS_EXACT_BASELINE_UNCONFIRMED",
                "RSS_EXACT_SUBSCRIPTION_UNCONFIRMED",
                "RSS_EXACT_TARGET_UNCONFIRMED",
                "RSS_EXACT_CONTEXT_UNAVAILABLE",
                "RSS_EXACT_ROUTE_UNCONFIRMED",
            ],
        )
        self.assertEqual(torra.submissions, [])
        self.assertNotIn("tracker.example", str(preview))
        self.assertNotIn("private", str(preview))

    def test_exact_download_preview_blocks_all_active_qb_target_states(self):
        _torra, qb, group, _preview = self._prepare_ready_exact_download()
        baseline_task = dict(qb.tasks[0])

        for status in ("downloading", "stalled", "queued", "paused"):
            with self.subTest(status=status):
                qb.tasks = [baseline_task, {
                    "name": "Test Show S01E02 candidate.mkv",
                    "status": status,
                }]

                preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(
                    group["id"], persist=False
                )

                self.assertFalse(preview["ready"])
                self.assertIn(
                    "RSS_EXACT_QB_BUSY",
                    [row["code"] for row in preview["blockers"]],
                )
                self.assertEqual(qb.added, [])

        qb.tasks = [baseline_task, {
            "name": "Test Show S02E02 candidate.mkv",
            "status": "queued",
        }]
        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(
            group["id"], persist=False
        )
        self.assertTrue(preview["ready"])

    def test_legacy_qb_preflight_blocks_queued_and_paused_targets(self):
        unit = self._watch("tv:202:s1", episode=1, title="Test Show")
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        inserted = self._insert("Test Show S01E01 2160p WEB-DL.mkv", start=1, end=1)
        match = self.rss.get_match(inserted["_match_ids"][0])
        context = {
            "subscription": self.subscriptions[0],
            "unit": self.watch.get_watch_unit(unit["unit_key"]),
            "match": match,
            "torra_id": "torra-202",
        }

        for status in ("queued", "paused"):
            with self.subTest(status=status):
                qb.tasks = [{
                    "name": "Test Show S01E01 candidate.mkv",
                    "status": status,
                }]
                self.assertEqual(self.runtime._qb_preflight(context), "qb_busy")

    def test_artifact_exact_download_rejects_download_url_drift(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        representative = group["representativeMatch"]
        item = self.rss.get_item(representative["itemId"], public=False)
        self.rss.upsert_items(self.source["id"], [{
            "fingerprint": item["fingerprint"],
            "title": item["title"],
            "published_at": item["published_at"],
            "media_type": item["media_type"],
            "season_number": item["season_number"],
            "episode_start": item["episode_start"],
            "episode_end": item["episode_end"],
            "size_bytes": item["size_bytes"],
            "download_url": "https://tracker.example/download?passkey=replaced",
        }])

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-url-drift"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_PREVIEW_STALE")
        self.assertEqual(qb.added, [])
        self.assertNotIn("tracker.example", raised.exception.message)
        self.assertNotIn("replaced", raised.exception.message)

    def test_artifact_exact_download_rechecks_qb_occupancy_before_submit(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        baseline_task = dict(qb.tasks[0])

        for status in ("queued", "paused"):
            with self.subTest(status=status):
                qb.tasks = [baseline_task, {
                    "name": "Test Show S01E02 candidate.mkv",
                    "status": status,
                }]

                with self.assertRaises(RssExactDownloadError) as raised:
                    self.runtime.execute_artifact_exact_download(
                        group["id"], preview["previewToken"], f"manual-request-{status}"
                    )

                self.assertEqual(raised.exception.code, "RSS_EXACT_PREVIEW_STALE")
                self.assertEqual(qb.added, [])

    def test_artifact_exact_download_submits_once_and_replays_stable_receipt(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()

        action = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-request-0001"
        )
        replay = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-request-0002"
        )

        self.assertEqual(action["status"], "succeeded")
        self.assertEqual(replay["action_id"], action["action_id"])
        self.assertEqual(len(qb.added), 1)
        self.assertEqual(qb.added[0]["savePath"], "/downloads/anime")
        self.assertEqual(qb.added[0]["category"], "anime")
        self.assertEqual(qb.added[0]["tags"][0], "fluxa-rss")
        stored = self.rss.list_matches_by_ids([
            result["match"]["id"] for result in group["unitResults"]
        ])
        self.assertEqual({row["downloadActionId"] for row in stored}, {action["action_id"]})
        self.assertNotIn("tracker.example", str(action))
        self.assertNotIn("private", str(action))
        self.assertNotIn("/downloads/anime", str(action))

    def test_artifact_exact_download_rejects_unreleased_automatic_mode(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        analysis = self.runtime.analysis
        self.runtime.analysis = RssAnalysisDependencies(
            analysis.environment,
            analysis.torra,
            analysis.qb,
            lambda: {
                "torra_quality_watch_enabled": True,
                "torra_quality_execution_mode": "automatic",
                "torra_quality_min_interval_minutes": 60,
                "torra_quality_hourly_limit": 4,
                "torra_quality_daily_limit": 30,
            },
            symedia=analysis.symedia,
        )

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "automatic-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_EXECUTION_DISABLED")
        self.assertEqual(qb.added, [])

    def test_artifact_exact_download_stays_submitted_until_qb_task_is_observed(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download(
            confirm_added_task=False
        )

        submitted = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-request-0001"
        )

        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(len(qb.added), 1)
        stored = self.rss.list_matches_by_ids([
            result["match"]["id"] for result in group["unitResults"]
        ])
        self.assertEqual({row["downloadActionId"] for row in stored}, {""})

        audit_tag = next(tag for tag in qb.added[0]["tags"] if tag.startswith("fluxa-action-"))
        qb.tasks.append({
            "hash": "confirmed-hash",
            "name": "Test Show S01E02 2160p WEB-DL.mkv",
            "status": "downloading",
            "tags": audit_tag,
        })
        confirmed = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-request-0002"
        )

        self.assertEqual(confirmed["status"], "succeeded")
        self.assertEqual(confirmed["action_id"], submitted["action_id"])
        self.assertEqual(len(qb.added), 1)

    def test_artifact_exact_download_recovers_qb_task_after_runtime_restart(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download(
            confirm_added_task=False
        )
        submitted = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-request-0001"
        )
        audit_tag = next(tag for tag in qb.added[0]["tags"] if tag.startswith("fluxa-action-"))
        qb.tasks.append({
            "hash": "restart-confirmed-hash",
            "name": "Test Show S01E02 2160p WEB-DL.mkv",
            "status": "downloading",
            "tags": audit_tag,
        })
        restarted = RssSubscriptionMatchRuntime(
            self.rss,
            self.watch,
            lambda: {"items": self.subscriptions},
            clock=lambda: self.now[0],
            analysis=self.runtime.analysis,
        )

        recovered = restarted.recover_pending_exact_download()

        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["actionId"], submitted["action_id"])
        self.assertEqual(len(qb.added), 1)
        self.assertEqual(
            self.watch.get_action(submitted["action_id"])["status"], "succeeded"
        )

    def test_artifact_exact_download_preview_expires_at_ten_minutes(self):
        _torra, _qb, group, preview = self._prepare_ready_exact_download()
        self.now[0] += timedelta(minutes=10)

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_PREVIEW_EXPIRED")

    def test_artifact_exact_download_rejects_rule_drift(self):
        torra, qb, group, preview = self._prepare_ready_exact_download()
        torra.rules[0]["custom_weight"] = 2

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_PREVIEW_STALE")
        self.assertEqual(qb.added, [])

    def test_artifact_exact_download_rejects_other_torra_downloader(self):
        _torra, qb, group, _preview = self._prepare_ready_exact_download()
        self.runtime.analysis.environment["TORRA_DOWNLOADER_ID"] = "qb-other"

        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(
            group["id"]
        )

        self.assertFalse(preview["ready"])
        self.assertIn(
            "RSS_EXACT_QB_DOWNLOADER_MISMATCH",
            [row["code"] for row in preview["blockers"]],
        )
        self.assertEqual(qb.added, [])

    def test_artifact_exact_download_omits_media_category_for_qb(self):
        torra, qb, group, _preview = self._prepare_ready_exact_download()
        torra.rows[0].pop("download_category")

        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(
            group["id"]
        )

        self.assertEqual(torra.rows[0]["category"], "anime")
        self.assertTrue(preview["ready"])
        self.assertEqual(preview["downloadCategory"], "")
        self.assertFalse(preview["downloadCategoryConfigured"])

        action = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-request-no-category"
        )

        self.assertEqual(action["status"], "succeeded")
        self.assertEqual(len(qb.added), 1)
        self.assertEqual(qb.added[0]["category"], "")

    def test_artifact_exact_download_honors_global_singleflight(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        self.watch.claim_action(
            "other-exact-action",
            "tv:other:s1",
            "qbittorrent",
            "rss-exact-download",
            unit_key="rss-artifact:other",
        )

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_GLOBAL_BUSY")
        self.assertEqual(qb.added, [])

    def test_artifact_exact_download_honors_subscription_cooldown(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        prior = self.watch.claim_action(
            "prior-exact-action",
            group["subscriptionId"],
            "qbittorrent",
            "rss-exact-download",
            unit_key=group["id"],
        )["action"]
        self.watch.complete_action(prior["action_id"], "succeeded", {"accepted": True})

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_COOLDOWN")
        self.assertEqual(qb.added, [])

    def test_artifact_exact_download_honors_hourly_limit(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        config = self.runtime.analysis.config_loader()
        config["torra_quality_min_interval_minutes"] = 1
        config["torra_quality_hourly_limit"] = 1
        prior = self.watch.claim_action(
            "prior-hourly-action",
            group["subscriptionId"],
            "qbittorrent",
            "rss-exact-download",
            unit_key="rss-artifact:prior",
        )["action"]
        self.watch.complete_action(prior["action_id"], "succeeded", {"accepted": True})
        self.now[0] += timedelta(minutes=2)

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_RATE_LIMITED")
        self.assertEqual(qb.added, [])

    def test_artifact_exact_download_honors_daily_limit(self):
        _torra, qb, group, preview = self._prepare_ready_exact_download()
        config = self.runtime.analysis.config_loader()
        config["torra_quality_min_interval_minutes"] = 1
        config["torra_quality_hourly_limit"] = 4
        config["torra_quality_daily_limit"] = 1
        prior = self.watch.claim_action(
            "prior-daily-action",
            group["subscriptionId"],
            "qbittorrent",
            "rss-exact-download",
            unit_key="rss-artifact:prior",
        )["action"]
        self.watch.complete_action(prior["action_id"], "succeeded", {"accepted": True})
        self.now[0] += timedelta(hours=2)
        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(
            group["id"]
        )

        with self.assertRaises(RssExactDownloadError) as raised:
            self.runtime.execute_artifact_exact_download(
                group["id"], preview["previewToken"], "manual-request-0001"
            )

        self.assertEqual(raised.exception.code, "RSS_EXACT_RATE_LIMITED")
        self.assertEqual(qb.added, [])

    def test_range_artifact_exact_download_creates_one_qb_task(self):
        for episode in (2, 3):
            self._watch(
                "tv:202:s1", episode=episode, title="Test Show", media_category="anime"
            )
        qb = FakeQb()
        torra, qb = self._enable_analysis(
            qb=qb,
            environment={
                "MCC_PRIVATE_RSS_ENABLED": "true",
                "MCC_TORRA_QUALITY_WATCH_ENABLED": "true",
                "MCC_TORRA_REWASH_DOWNLOAD_ENABLED": "true",
                "TORRA_DOWNLOADER_ID": "qb-main",
            },
            config={
                "torra_quality_watch_enabled": True,
                "torra_quality_execution_mode": "manual",
                "torra_quality_min_interval_minutes": 60,
                "torra_quality_hourly_limit": 4,
                "torra_quality_daily_limit": 30,
            },
        )
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            str(episode): [f"/downloads/Test.Show.S01E0{episode}.1080p.WEB-DL.mkv"]
            for episode in (2, 3)
        }
        qb.tasks = [{
            "name": f"Test.Show.S01E0{episode}.1080p.WEB-DL.mkv",
            "size": 2_000_000_000,
            "status": "completed",
        } for episode in (2, 3)]
        inserted = self._insert(
            "Test Show S01E02-E03 2160p WEB-DL.mkv",
            start=2,
            end=3,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=4_000_000_000,
            download_url="https://tracker.example/download?passkey=private",
        )
        self.runtime.wake_matches(inserted["_match_ids"])
        group = self.rss.list_candidate_artifact_groups(
            match_id=inserted["_match_ids"][0], limit=1
        )["groups"][0]
        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(group["id"])

        action = self.runtime.execute_artifact_exact_download(
            group["id"], preview["previewToken"], "manual-range-0001"
        )

        self.assertEqual(action["status"], "succeeded")
        self.assertEqual(preview["coveredUnitCount"], 2)
        self.assertEqual(len(qb.added), 1)
        stored = self.rss.list_matches_by_ids(inserted["_match_ids"])
        self.assertEqual({row["downloadActionId"] for row in stored}, {action["action_id"]})

    def test_new_batch_reconciles_existing_candidates_and_keeps_one_champion(self):
        unit = self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [{
            "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
            "size": 2_000_000_000,
        }]

        first = self._insert(
            "Test Show S01E02 1080p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
        )
        self.runtime.wake_matches(first["_match_ids"])
        self.now[0] += timedelta(minutes=5)
        second = self._insert(
            "Test Show S01E02 2160p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
        )
        self.runtime.wake_matches(second["_match_ids"])

        matches = {match["id"]: match for match in self.rss.list_matches()["items"]}
        self.assertEqual(matches[first["_match_ids"][0]]["decision"], "superseded")
        self.assertFalse(matches[first["_match_ids"][0]]["bestCandidate"])
        self.assertEqual(matches[second["_match_ids"][0]]["decision"], "current_best")
        self.assertTrue(matches[second["_match_ids"][0]]["bestCandidate"])
        stored_unit = self.watch.get_watch_unit(unit["unit_key"])
        self.assertEqual(stored_unit["best_match_id"], second["_match_ids"][0])
        self.assertEqual(stored_unit["best_candidate_score"], 30.0)

    def test_range_champion_uses_one_canonical_match_for_every_projected_unit(self):
        units = [
            self._watch("tv:202:s1", episode=episode, title="Test Show", media_category="anime")
            for episode in (2, 3)
        ]
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
            "3": ["/downloads/Test.Show.S01E03.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [
            {"name": f"Test.Show.S01E0{episode}.1080p.WEB-DL.mkv", "size": 2_000_000_000}
            for episode in (2, 3)
        ]

        inserted = self._insert(
            "Test Show S01E02-E03 2160p WEB-DL.mkv",
            start=2,
            end=3,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=4_000_000_000,
        )
        self.runtime.wake_matches(inserted["_match_ids"])

        stored_matches = [self.rss.get_match(match_id) for match_id in inserted["_match_ids"]]
        self.assertEqual({match["decision"] for match in stored_matches}, {"current_best"})
        self.assertEqual({match["bestCandidate"] for match in stored_matches}, {True})
        canonical_ids = {
            self.watch.get_watch_unit(unit["unit_key"])["best_match_id"]
            for unit in units
        }
        self.assertEqual(len(canonical_ids), 1)
        self.assertTrue(next(iter(canonical_ids)) in inserted["_match_ids"])

    def test_range_artifact_losing_one_episode_is_removed_from_every_champion(self):
        units = [
            self._watch("tv:202:s1", episode=episode, title="Test Show", media_category="anime")
            for episode in (2, 3)
        ]
        torra, qb = self._enable_analysis()
        rule = self._shadow_rule()
        rule["custom_attributes"].append({
            "name": "REMUX",
            "pattern": "REMUX",
            "score": 20,
        })
        self._configure_shadow_torra(torra, [rule])
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
            "3": ["/downloads/Test.Show.S01E03.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [
            {"name": f"Test.Show.S01E0{episode}.1080p.WEB-DL.mkv", "size": 2_000_000_000}
            for episode in (2, 3)
        ]
        ranged = self._insert(
            "Test Show S01E02-E03 2160p WEB-DL.mkv",
            start=2,
            end=3,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=4_000_000_000,
        )
        self.runtime.wake_matches(ranged["_match_ids"])

        self.now[0] += timedelta(minutes=5)
        single = self._insert(
            "Test Show S01E03 2160p WEB-DL REMUX.mkv",
            start=3,
            end=3,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=3_000_000_000,
        )
        self.runtime.wake_matches(single["_match_ids"])

        ranged_matches = [self.rss.get_match(match_id) for match_id in ranged["_match_ids"]]
        single_match = self.rss.get_match(single["_match_ids"][0])
        self.assertEqual({match["decision"] for match in ranged_matches}, {"superseded"})
        self.assertEqual({match["bestCandidate"] for match in ranged_matches}, {False})
        self.assertEqual(single_match["decision"], "current_best")
        self.assertTrue(single_match["bestCandidate"])
        self.assertEqual(self.watch.get_watch_unit(units[1]["unit_key"])["best_match_id"], single_match["id"])
        self.assertNotIn(
            self.watch.get_watch_unit(units[0]["unit_key"])["best_match_id"],
            ranged["_match_ids"],
        )
        self.rss.save_candidate_decisions([{
            "matchIds": [ranged["_match_ids"][0]],
            "decision": "current_best",
            "reason": "test_partial_projection",
            "bestCandidate": True,
        }])
        ranged_group = self.rss.list_candidate_artifact_groups(
            match_id=ranged["_match_ids"][0], limit=1
        )["groups"][0]
        preview, _fingerprint, _match_ids = self.runtime.preview_artifact_exact_download(
            ranged_group["id"]
        )
        self.assertEqual(ranged_group["state"], "partially_best")
        self.assertFalse(preview["ready"])
        self.assertIn(
            "RSS_EXACT_ARTIFACT_NOT_UNIQUE_WINNER",
            [row["code"] for row in preview["blockers"]],
        )

    def test_persisted_baseline_survives_temporary_upstream_evidence_loss(self):
        self._watch(
            "tv:202:s1",
            episode=2,
            title="Test Show",
            media_category="anime",
        )
        torra, qb = self._enable_analysis()
        self._configure_shadow_torra(torra)
        torra.rows[0]["downloaded_episode_files"] = {
            "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
        }
        qb.tasks = [{
            "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
            "size": 2_000_000_000,
        }]
        first = self._insert(
            "Test Show S01E02 2160p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=2_000_000_000,
        )
        self.runtime.wake_matches(first["_match_ids"])

        torra.rows[0].pop("downloaded_episode_files")
        qb.tasks = []
        self.now[0] += timedelta(minutes=5)
        second = self._insert(
            "Test Show S01E02 720p WEB-DL.mkv",
            start=2,
            end=2,
            published_at=self.now[0].isoformat().replace("+00:00", "Z"),
            size_bytes=1_000_000_000,
        )
        self.runtime.wake_matches(second["_match_ids"])

        stored = self.rss.get_match(second["_match_ids"][0])
        self.assertEqual(stored["baselineScore"], 10.0)
        self.assertEqual(stored["baselineSummary"]["versionSummary"], "Test.Show.S01E02.1080p.WEB-DL.mkv")


if __name__ == "__main__":
    unittest.main()
