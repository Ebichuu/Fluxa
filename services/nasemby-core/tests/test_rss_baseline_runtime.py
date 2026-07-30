from __future__ import annotations

import unittest

from app.rss_baseline_runtime import resolve_baseline_artifact


class RssBaselineRuntimeTests(unittest.TestCase):
    def test_torra_episode_file_uses_exact_qb_name_only_to_fill_size(self):
        result = resolve_baseline_artifact(
            {"media_type": "tv", "tmdb_id": "202", "target_season": 1},
            {
                "id": "torra-202",
                "media_type": "tv",
                "tmdb_id": "202",
                "season_number": 1,
                "downloaded_episode_files": {
                    "2": ["/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv"],
                },
            },
            {"season_number": 1, "episode_number": 2},
            qb_summary={
                "connected": True,
                "tasks": [{
                    "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
                    "size": 2_000_000_000,
                }],
            },
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["versionSummary"], "Test.Show.S01E02.1080p.WEB-DL.mkv")
        self.assertEqual(result["sizeBytes"], 2_000_000_000)
        self.assertEqual(result["sources"], ["qb", "torra"])
        self.assertTrue(result["artifactKey"].startswith("baseline:"))

    def test_symedia_requires_success_and_exact_media_identity(self):
        result = resolve_baseline_artifact(
            {"media_type": "tv", "tmdb_id": "202", "target_season": 1},
            {"id": "torra-202", "media_type": "tv", "tmdb_id": "202", "season_number": 1},
            {"season_number": 1, "episode_number": 2},
            symedia_rows=[
                {
                    "id": "ignored-failure",
                    "tmdbid": "202",
                    "type": "tv",
                    "season": 1,
                    "season_episode": "S01E02",
                    "src": "/downloads/Test.Show.S01E02.bad.mkv",
                    "status": False,
                },
                {
                    "id": "exact-success",
                    "tmdbid": "202",
                    "type": "tv",
                    "season": 1,
                    "season_episode": "S01E02",
                    "src": "/downloads/Test.Show.S01E02.1080p.WEB-DL.mkv",
                    "status": True,
                    "size": 2_000_000_000,
                },
                {
                    "id": "wrong-title-only",
                    "tmdbid": "",
                    "type": "tv",
                    "season": 1,
                    "season_episode": "S01E02",
                    "src": "/downloads/Test.Show.S01E02.2160p.mkv",
                    "status": True,
                },
            ],
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["versionSummary"], "Test.Show.S01E02.1080p.WEB-DL.mkv")
        self.assertEqual(result["sources"], ["symedia"])

    def test_distinct_exact_versions_are_a_conflict(self):
        result = resolve_baseline_artifact(
            {"media_type": "tv", "tmdb_id": "202", "target_season": 1},
            {
                "id": "torra-202",
                "media_type": "tv",
                "tmdb_id": "202",
                "season_number": 1,
                "downloaded_episode_files": {
                    "2": [
                        "/downloads/Test.Show.S01E02.1080p.mkv",
                        "/downloads/Test.Show.S01E02.2160p.mkv",
                    ],
                },
            },
            {"season_number": 1, "episode_number": 2},
        )

        self.assertEqual(result, {
            "status": "blocked",
            "reason": "baseline_artifact_conflict",
        })

    def test_qb_title_alone_never_becomes_a_baseline(self):
        result = resolve_baseline_artifact(
            {"media_type": "tv", "tmdb_id": "202", "target_season": 1},
            {"id": "torra-202", "media_type": "tv", "tmdb_id": "202", "season_number": 1},
            {"season_number": 1, "episode_number": 2},
            qb_summary={
                "connected": True,
                "tasks": [{
                    "name": "Test.Show.S01E02.1080p.WEB-DL.mkv",
                    "size": 2_000_000_000,
                }],
            },
        )

        self.assertEqual(result, {
            "status": "unconfirmed",
            "reason": "baseline_version_unconfirmed",
        })


if __name__ == "__main__":
    unittest.main()
