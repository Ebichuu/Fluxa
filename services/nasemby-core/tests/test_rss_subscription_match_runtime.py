from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.private_rss_repository import PrivateRssRepository
from app.quality_watch_repository import QualityWatchRepository
from app.rss_subscription_match_runtime import RssAnalysisDependencies, RssSubscriptionMatchRuntime
from app.torra_quality_runtime import TorraQualityClient


class FakeTorra:
    def __init__(self):
        self.rows = [{"id": "torra-202", "is_running": False, "is_mutating": False}]
        self.jobs = []
        self.submissions = []
        self.polls = []

    def is_configured(self):
        return True

    def list_subscriptions(self):
        return list(self.rows)

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

    def summary(self):
        return {"configured": True, "connected": True, "tasks": list(self.tasks)}


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
        }
        config = config if config is not None else {
            "torra_quality_watch_enabled": True,
            "torra_quality_min_interval_minutes": 60,
            "torra_quality_hourly_limit": 4,
            "torra_quality_daily_limit": 30,
        }
        self.runtime.analysis = RssAnalysisDependencies(environment, torra, qb, lambda: config)
        return torra, qb

    def _insert(self, title, media_type="tv", season=1, start=1, end=1, published_at=""):
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
            }],
            on_insert=self.runtime.match_inserted_rows,
        )

    def test_matches_only_active_episode_and_deduplicates_repeated_item(self):
        first = self._watch("tv:202:s1", episode=3)
        second = self._watch("tv:202:s1", episode=4)
        inserted = self._insert("[Group] 测试剧.S01E03-E04.2160p", start=3, end=4)

        self.assertEqual(inserted["inserted"], 1)
        matches = self.rss.list_matches()["items"]
        self.assertEqual({match["unitId"] for match in matches}, {first["unit_key"], second["unit_key"]})
        self.assertEqual({match["status"] for match in matches}, {"candidate"})
        repeated = self._insert("[Group] 测试剧.S01E03-E04.2160p", start=3, end=4)
        self.assertEqual(repeated["inserted"], 0)
        self.assertEqual(self.rss.list_matches()["total"], 2)

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


if __name__ == "__main__":
    unittest.main()
