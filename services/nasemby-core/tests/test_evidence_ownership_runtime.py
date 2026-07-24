from __future__ import annotations

import unittest

from app.evidence_ownership_runtime import adjudicate_task_evidence, compare_legacy_ownership


def subscription(item_id, title, media_type, tmdb_id="", year="", season=0, aliases=None):
    return {
        "id": item_id,
        "title": title,
        "mediaType": media_type,
        "tmdbId": tmdb_id,
        "year": year,
        "seasonNumber": season,
        "aliases": aliases or [],
    }


class EvidenceOwnershipRuntimeTests(unittest.TestCase):
    def test_empty_tmdb_never_forms_exact_match(self):
        result = adjudicate_task_evidence(
            [subscription("movie-a", "痴迷", "movie", year="2026")],
            [],
            [],
            [{
                "id": "symedia-1",
                "title": "云月大陆",
                "type": "movie",
                "tmdbid": "",
                "status": False,
                "errmsg": "识别失败",
            }],
        )

        record = result["records"][0]
        self.assertEqual(record["ownerTargetKey"], "")
        self.assertEqual(record["confidence"], "unlinked")

    def test_one_symedia_failure_is_not_shared_by_unrelated_movies(self):
        movies = [
            subscription(f"movie-{index}", title, "movie", year="2026")
            for index, title in enumerate(["痴迷", "后室", "扣篮梦工厂", "火遮眼"] * 9)
        ]
        result = adjudicate_task_evidence(
            movies,
            [],
            [],
            [{
                "id": "symedia-cloud-moon",
                "title": "云月大陆",
                "type": "movie",
                "tmdbid": "",
                "season_episode": "S01E05",
                "status": False,
            }],
        )

        self.assertEqual(result["summary"], {"owned": 0, "conflicts": 0, "unlinked": 1})
        self.assertTrue(all(not bucket["symedia"] for bucket in result["owned"].values()))

    def test_read_only_comparison_reports_legacy_shared_claims_without_details(self):
        movies = [
            subscription(f"movie-{index}", f"无关电影{index}", "movie", year="2026")
            for index in range(36)
        ]
        symedia = [{
            "id": "symedia-cloud-moon",
            "title": "云月大陆",
            "type": "movie",
            "tmdbid": "",
            "season_episode": "S01E05",
            "src": "/private/path/云月大陆.S01E05.mkv",
            "status": False,
        }]
        adjudicated = adjudicate_task_evidence(movies, [], [], symedia)

        comparison = compare_legacy_ownership(movies, [], [], symedia, adjudicated)

        self.assertEqual(comparison["legacySharedEvidence"], 1)
        self.assertEqual(comparison["releasedEvidence"], 1)
        self.assertEqual(comparison["newOwnedEvidence"], 0)
        self.assertNotIn("path", str(comparison).lower())
        self.assertNotIn("云月大陆", str(comparison))

    def test_tv_fallback_requires_unique_title_type_and_season(self):
        result = adjudicate_task_evidence(
            [
                subscription("tv-s1", "同名剧", "tv", year="2025", season=1),
                subscription("tv-s2", "同名剧", "tv", year="2026", season=2),
            ],
            [{
                "id": "torra-s2",
                "name": "同名剧",
                "media_type": "tv",
                "tmdb_id": "",
                "season_number": 2,
            }],
            [],
            [],
        )

        record = result["records"][0]
        self.assertTrue(record["ownerTargetKey"].endswith(":season:2"))
        self.assertEqual(record["matchMethod"], "title_season_unique")

    def test_movie_fallback_requires_year_and_unique_candidate(self):
        no_year = adjudicate_task_evidence(
            [subscription("movie-a", "同名电影", "movie", year="")],
            [{"id": "torra-a", "name": "同名电影", "media_type": "movie", "tmdb_id": ""}],
            [],
            [],
        )
        self.assertEqual(no_year["records"][0]["confidence"], "unlinked")

        conflict = adjudicate_task_evidence(
            [
                subscription("movie-a", "同名电影", "movie", year="2026"),
                subscription("movie-b", "同名电影", "movie", tmdb_id="999", year="2026"),
            ],
            [{"id": "torra-a", "name": "同名电影 2026", "media_type": "movie", "tmdb_id": ""}],
            [],
            [],
        )
        self.assertEqual(conflict["records"][0]["ownerTargetKey"], "")
        self.assertEqual(conflict["records"][0]["confidence"], "conflict")
        self.assertEqual(len(conflict["records"][0]["conflictCandidates"]), 2)

    def test_tmdb_exact_owner_is_stable_when_inputs_are_reordered(self):
        subscriptions = [
            subscription("tv-a", "甲剧", "tv", tmdb_id="101", season=1),
            subscription("tv-b", "乙剧", "tv", tmdb_id="202", season=1),
        ]
        torra = [
            {"id": "torra-b", "name": "任意标题", "media_type": "tv", "tmdb_id": "202", "season_number": 1},
            {"id": "torra-a", "name": "任意标题", "media_type": "tv", "tmdb_id": "101", "season_number": 1},
        ]

        first = adjudicate_task_evidence(subscriptions, torra, [], [])
        second = adjudicate_task_evidence(list(reversed(subscriptions)), list(reversed(torra)), [], [])

        first_owners = sorted((record["artifactKey"], record["ownerTargetKey"]) for record in first["records"])
        second_owners = sorted((record["artifactKey"], record["ownerTargetKey"]) for record in second["records"])
        self.assertEqual(first_owners, second_owners)
        self.assertTrue(all(record["matchMethod"] == "tmdb_exact" for record in first["records"]))

    def test_qb_artifact_inherits_single_torra_owner(self):
        result = adjudicate_task_evidence(
            [subscription("tv-a", "测试剧", "tv", tmdb_id="101", season=1)],
            [{
                "id": "torra-a",
                "name": "测试剧",
                "media_type": "tv",
                "tmdb_id": "101",
                "season_number": 1,
                "downloaded_file_names": ["Test.Show.S01E01.1080p.mkv"],
            }],
            [{"hash": "hash-a", "name": "Test.Show.S01E01.1080p.mkv"}],
            [],
        )

        qb_record = next(record for record in result["records"] if record["source"] == "qBittorrent")
        self.assertEqual(qb_record["matchMethod"], "artifact_exact")
        self.assertTrue(qb_record["ownerTargetKey"])

    def test_qb_alias_from_torra_names_json_binds_to_same_target(self):
        result = adjudicate_task_evidence(
            [subscription("tv-a", "中文剧名", "tv", tmdb_id="101", season=1, aliases=["English Show"])],
            [{
                "id": "torra-a",
                "name": "English Show",
                "names_json": '["中文剧名", "English Show"]',
                "media_type": "tv",
                "tmdb_id": "",
                "season_number": 1,
            }],
            [{"hash": "hash-alias", "name": "English.Show.S01E01.1080p.mkv"}],
            [],
        )

        qb_record = next(record for record in result["records"] if record["source"] == "qBittorrent")
        self.assertEqual(qb_record["ownerTargetKey"], "tv:tmdb:101:season:1")
        self.assertEqual(qb_record["matchMethod"], "title_season_unique")

    def test_qb_bracketed_chinese_title_binds_and_negative_completion_uses_added_time(self):
        from app.evidence_ownership_runtime import _qb_evidence

        name = "[灿如繁星].Road.to.Success.S01E01.1080p.mkv"
        result = adjudicate_task_evidence(
            [subscription("tv-cn", "灿如繁星", "tv", tmdb_id="808", season=1)],
            [],
            [{"hash": "hash-cn", "name": name, "completionOn": -28800, "addedOn": 1700000000}],
            [],
        )

        qb_record = next(record for record in result["records"] if record["source"] == "qBittorrent")
        self.assertEqual(qb_record["ownerTargetKey"], "tv:tmdb:808:season:1")
        evidence = _qb_evidence({"hash": "hash-cn", "name": name, "completionOn": -28800, "addedOn": 1700000000}, 0)
        self.assertEqual(evidence["observedAt"], "2023-11-14T22:13:20Z")
        self.assertNotIn("1969", evidence["observedAt"])

    def test_symedia_artifact_inherits_only_exact_torra_file_owner(self):
        result = adjudicate_task_evidence(
            [subscription("tv-a", "测试剧", "tv", tmdb_id="101", season=1)],
            [{
                "id": "torra-a",
                "name": "测试剧",
                "media_type": "tv",
                "tmdb_id": "101",
                "season_number": 1,
                "downloaded_file_names": ["Test.Show.S01E01.1080p.WEB-DL.mkv"],
            }],
            [],
            [{
                "id": "symedia-a",
                "title": "无关标题",
                "type": "tv",
                "src": "/115/Test.Show.S01E01.1080p.WEB-DL.mkv",
                "status": True,
            }],
        )

        symedia_record = next(record for record in result["records"] if record["source"] == "Symedia")
        self.assertEqual(symedia_record["matchMethod"], "artifact_exact")
        self.assertTrue(symedia_record["ownerTargetKey"])

    def test_symedia_partial_file_name_does_not_inherit_torra_owner(self):
        result = adjudicate_task_evidence(
            [subscription("tv-a", "测试剧", "tv", tmdb_id="101", season=1)],
            [{
                "id": "torra-a",
                "name": "测试剧",
                "media_type": "tv",
                "tmdb_id": "101",
                "season_number": 1,
                "downloaded_file_names": ["Test.Show.S01E01.1080p.WEB-DL.mkv"],
            }],
            [],
            [{
                "id": "symedia-a",
                "title": "无关标题",
                "type": "tv",
                "src": "/115/Test.Show.S01E01.1080p.mkv",
                "status": True,
            }],
        )

        symedia_record = next(record for record in result["records"] if record["source"] == "Symedia")
        self.assertEqual(symedia_record["ownerTargetKey"], "")
        self.assertEqual(symedia_record["confidence"], "unlinked")


if __name__ == "__main__":
    unittest.main()
