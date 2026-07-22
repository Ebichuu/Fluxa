from __future__ import annotations

import unittest

from app.episode_evidence_runtime import build_episode_evidence, parse_episode_ranges


def record(source, key):
    return {
        "artifactKey": key,
        "source": source,
        "observedAt": "2026-07-23T01:00:00Z",
        "matchMethod": "artifact_exact",
    }


class EpisodeEvidenceRuntimeTests(unittest.TestCase):
    def test_parses_single_range_multi_episode_and_specials(self):
        self.assertEqual(parse_episode_ranges("Show.S01E03.1080p"), [{
            "seasonNumber": 1,
            "episodeStart": 3,
            "episodeEnd": 3,
            "numberingScheme": "season_episode",
        }])
        self.assertEqual(parse_episode_ranges("Show.S01E03-E05.1080p")[0]["episodeEnd"], 5)
        self.assertEqual(parse_episode_ranges("Special.S00E02")[0]["numberingScheme"], "special")
        self.assertEqual(parse_episode_ranges("Anime EP 126")[0], {
            "seasonNumber": 0,
            "episodeStart": 126,
            "episodeEnd": 126,
            "numberingScheme": "absolute",
        })
        self.assertEqual(parse_episode_ranges("第 3 至 5 集", default_season=2)[0]["episodeEnd"], 5)

    def test_season_pack_without_file_episode_list_creates_no_episode_evidence(self):
        values = build_episode_evidence(
            torra_pairs=[(
                {
                    "id": "torra-pack",
                    "season_number": 1,
                    "downloaded_file_names": ["Example.Show.Season.1.Complete.1080p"],
                },
                record("Torra", "artifact:torra-pack"),
            )],
        )
        self.assertEqual(values, [])

    def test_season_pack_uses_explicit_internal_file_ranges(self):
        values = build_episode_evidence(
            torra_pairs=[(
                {
                    "id": "torra-pack",
                    "season_number": 1,
                    "downloaded_episode_files": [
                        "Example.Show.S01E01.mkv",
                        "Example.Show.S01E02-E03.mkv",
                    ],
                },
                record("Torra", "artifact:torra-pack"),
            )],
        )
        self.assertEqual(
            [(item["episodeStart"], item["episodeEnd"]) for item in values],
            [(1, 1), (2, 3)],
        )

    def test_qb_and_symedia_keep_artifact_stage_and_protection(self):
        values = build_episode_evidence(
            qb_pairs=[(
                {"name": "Example.Show.S01E03.mkv", "status": "completed"},
                record("qBittorrent", "artifact:hash"),
            )],
            symedia_pairs=[(
                {
                    "season": 1,
                    "episode": 3,
                    "status": False,
                    "errmsg": "源文件评分低于目标文件，取消覆盖",
                },
                record("Symedia", "artifact:symedia"),
            )],
        )
        self.assertEqual([item["stage"] for item in values], ["download", "library"])
        library = values[1]
        self.assertEqual(library["reasonCode"], "QUALITY_SCORE_LOWER")
        self.assertEqual(library["artifactKey"], "artifact:symedia")


if __name__ == "__main__":
    unittest.main()
