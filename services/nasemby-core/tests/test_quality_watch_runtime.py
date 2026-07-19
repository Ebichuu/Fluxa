from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from app.quality_watch_repository import QualityWatchRepository
from app.quality_watch_runtime import QualityWatchRuntime


def subscription(key="tv:202", media_type="tv", tmdb_id=202, season=1, **overrides):
    value = {
        "key": key,
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "target_season": season,
    }
    value.update(overrides)
    return value


def task_item(key="tv:202", media_type="tv", tmdb_id=202, season=1, torra_id="torra-202"):
    return {
        "mediaType": media_type,
        "tmdbId": str(tmdb_id),
        "seasonNumber": season,
        "steps": [{
            "key": "download",
            "status": "done",
            "evidence": "verified",
            "source": "Torra",
            "timestamp": "2026-07-18T01:00:00.000Z",
        }],
        "sourceIds": {"subscriptionId": key, "torraId": torra_id, "qbHashes": []},
    }


def torra_row(media_type="tv", tmdb_id=202, season=1, **overrides):
    value = {
        "id": "torra-202",
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "season_number": season,
        "last_added_name": "测试剧.S01E01.1080p.mkv",
        "downloaded_episode_numbers": [1],
        "downloaded_episode_files": {"1": ["测试剧.S01E01.1080p.mkv"]},
        "library_episode_files": {},
        "library_file_names": [],
        "available_episode_numbers": [],
    }
    value.update(overrides)
    return value


class QualityWatchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = [datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)]
        self.repository = QualityWatchRepository(
            Path(self.directory.name) / "media_control_center.sqlite3",
            clock=lambda: self.now[0],
        )
        self.runtime = QualityWatchRuntime(
            self.repository,
            config_loader=lambda: {"torra_quality_default_window_hours": 48},
            clock=lambda: self.now[0],
        )

    def test_first_download_waits_for_torra_visible_emby_baseline_then_keeps_fixed_window(self):
        first = self.runtime.reconcile(
            subscription(),
            task_item(),
            torra_row(),
            {"is_new": True, "observed_at": self.now[0], "source": "torra"},
        )["units"][0]
        self.assertEqual(first["state"], "waiting_library_baseline")
        self.assertEqual(first["baseline_ready_at"], "")

        self.now[0] += timedelta(hours=2)
        ready = self.runtime.reconcile(
            subscription(),
            task_item(),
            torra_row(
                library_episode_files={"1": ["/emby/测试剧.S01E01.mkv"]},
                available_episode_numbers=[1],
            ),
            {"baseline_ready_at": self.now[0]},
        )["units"][0]
        self.assertEqual(ready["state"], "observing_upgrade")
        self.assertEqual(ready["next_check_at"], "2026-07-18T15:00:00.000Z")
        self.assertEqual(ready["observation_ends_at"], "2026-07-20T03:00:00.000Z")

        self.now[0] += timedelta(hours=4)
        repeated = self.runtime.reconcile(
            subscription(), task_item(), torra_row(available_episode_numbers=[1]), {}
        )["units"][0]
        self.assertEqual(repeated["observation_ends_at"], ready["observation_ends_at"])

    def test_new_episodes_are_independent_and_subscription_window_overrides_global_default(self):
        first = self.runtime.reconcile(
            subscription(torra_quality_window_hours=24),
            task_item(),
            torra_row(library_episode_files={"1": ["one.mkv"]}, available_episode_numbers=[1]),
            {"is_new": True, "episode_numbers": [1], "baseline_ready_at": self.now[0]},
        )["units"]
        self.assertEqual(first[0]["window_hours"], 24)
        self.assertEqual(first[0]["observation_ends_at"], "2026-07-19T01:00:00.000Z")

        self.now[0] += timedelta(hours=6)
        units = self.runtime.reconcile(
            subscription(torra_quality_window_hours=24),
            task_item(),
            torra_row(
                last_added_name="测试剧.S01E02.1080p.mkv",
                downloaded_episode_numbers=[1, 2],
                library_episode_files={"1": ["one.mkv"], "2": ["two.mkv"]},
                available_episode_numbers=[1, 2],
            ),
            {
                "is_new": True,
                "episode_numbers": [2],
                "baseline_ready_at": self.now[0],
                "target_reached": True,
            },
        )["units"]
        self.assertEqual([unit["episode_number"] for unit in units], [1, 2])
        self.assertEqual([unit["state"] for unit in units], ["observing_upgrade", "target_reached"])
        self.assertEqual(units[0]["observation_ends_at"], "2026-07-19T01:00:00.000Z")
        self.assertEqual(units[1]["observation_ends_at"], "2026-07-19T07:00:00.000Z")

    def test_movie_uses_one_unit_and_target_reached_is_terminal(self):
        movie_subscription = subscription("movie:9", "movie", 9, 0, torra_quality_window_hours=24)
        movie_task = task_item("movie:9", "movie", 9, 0, "torra-9")
        movie_torra = torra_row(
            media_type="movie",
            tmdb_id=9,
            season=0,
            id="torra-9",
            last_added_name="测试电影.2026.mkv",
            downloaded_episode_numbers=[],
            downloaded_episode_files={},
            library_file_names=["/emby/测试电影.2026.mkv"],
        )
        result = self.runtime.reconcile(
            movie_subscription,
            movie_task,
            movie_torra,
            {"is_new": True, "target_reached": True, "baseline_ready_at": self.now[0]},
        )

        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0]["unit_key"], "movie:9:movie")
        self.assertEqual(result["units"][0]["state"], "target_reached")
        self.assertEqual(result["units"][0]["target_reached_at"], "2026-07-18T01:00:00.000Z")

    def test_qb_evidence_can_create_unit_before_torra_links_and_later_baseline_attaches_id(self):
        qb_task = task_item(torra_id="")
        qb_task["steps"][0]["source"] = "qBittorrent"
        waiting = self.runtime.reconcile(
            subscription(),
            qb_task,
            None,
            {"is_new": True, "episode_numbers": [3], "source": "qbittorrent"},
        )["units"][0]
        self.assertEqual(waiting["state"], "waiting_library_baseline")
        self.assertEqual(waiting["torra_subscription_id"], "")

        linked_task = task_item()
        ready = self.runtime.reconcile(
            subscription(),
            linked_task,
            torra_row(
                last_added_name="测试剧.S01E03.1080p.mkv",
                downloaded_episode_numbers=[3],
                library_episode_files={"3": ["three.mkv"]},
                available_episode_numbers=[3],
            ),
            {"baseline_ready_at": self.now[0]},
        )["units"][0]
        self.assertEqual(ready["state"], "observing_upgrade")
        self.assertEqual(ready["torra_subscription_id"], "torra-202")

    def test_historical_evidence_does_not_create_units_and_missing_episode_is_blocked(self):
        historical = self.runtime.reconcile(subscription(), task_item(), torra_row(), {})
        self.assertEqual(historical, {"status": "ignored", "reason": "historical_evidence", "units": []})

        incomplete_task = task_item()
        incomplete_task["steps"][0]["status"] = "active"
        incomplete = self.runtime.reconcile(
            subscription(), incomplete_task, torra_row(), {"is_new": True, "episode_numbers": [1]}
        )
        self.assertEqual(incomplete, {"status": "ignored", "reason": "download_not_complete", "units": []})

        blocked = self.runtime.reconcile(
            subscription(),
            task_item(),
            torra_row(
                last_added_name="测试剧.1080p.mkv",
                downloaded_episode_numbers=[1, 2],
                downloaded_episode_files={},
            ),
            {"is_new": True},
        )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["reason"], "episode_identity_missing")
        self.assertTrue(blocked["units"][0]["unit_key"].endswith(":blocked"))

    def test_identity_conflict_and_invalid_policy_are_blocked_without_guessing(self):
        conflict = self.runtime.reconcile(
            subscription(),
            task_item(tmdb_id=999),
            torra_row(),
            {"is_new": True, "episode_numbers": [1]},
        )
        self.assertEqual(conflict["status"], "blocked")
        self.assertEqual(conflict["reason"], "identity_conflict")

        invalid = self.runtime.reconcile(
            subscription("tv:303", tmdb_id=303, torra_quality_window_hours=36),
            task_item("tv:303", tmdb_id=303, torra_id="torra-303"),
            torra_row(tmdb_id=303, id="torra-303"),
            {"is_new": True, "episode_numbers": [1]},
        )
        self.assertEqual(invalid["status"], "blocked")
        self.assertEqual(invalid["reason"], "invalid_watch_policy")

    def test_task_season_and_torra_id_conflicts_are_blocked(self):
        wrong_season = task_item(season=2)
        season_result = self.runtime.reconcile(
            subscription(), wrong_season, torra_row(), {"is_new": True, "episode_numbers": [1]}
        )
        self.assertEqual(season_result["reason"], "identity_conflict")

        wrong_torra = task_item(torra_id="torra-other")
        torra_result = self.runtime.reconcile(
            subscription(), wrong_torra, torra_row(), {"is_new": True, "episode_numbers": [1]}
        )
        self.assertEqual(torra_result["reason"], "identity_conflict")

    def test_new_season_evidence_does_not_advance_existing_season_units(self):
        season_one = self.runtime.reconcile(
            subscription(),
            task_item(),
            torra_row(),
            {"is_new": True, "episode_numbers": [1]},
        )["units"][0]

        season_two_result = self.runtime.reconcile(
            subscription(season=2),
            task_item(season=2),
            torra_row(
                season=2,
                last_added_name="测试剧.S02E01.1080p.mkv",
                library_episode_files={"1": ["season-two.mkv"]},
                available_episode_numbers=[1],
            ),
            {"is_new": True, "episode_numbers": [1], "baseline_ready_at": self.now[0]},
        )

        self.assertEqual(season_two_result["units"][0]["season_number"], 2)
        self.assertEqual(season_two_result["units"][0]["state"], "observing_upgrade")
        unchanged = self.repository.get_watch_unit(season_one["unit_key"])
        self.assertEqual(unchanged["state"], "waiting_library_baseline")
        self.assertEqual(unchanged["baseline_ready_at"], "")


if __name__ == "__main__":
    unittest.main()
