from __future__ import annotations

import unittest
from datetime import date

from app.config import DEFAULT_SITE_OFFICIAL_GROUPS
from app.domain.release_rules import evaluate_release_group, evaluate_subscription_guardrail


class ReleaseRulesTests(unittest.TestCase):
    def test_site_official_group_matches_same_site_case_insensitive(self):
        res = evaluate_release_group(
            title="Show.Name.S01E01.2026.2160p.WEB-DL.H265-adweb",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="audiences",
            site_name="观众",
            require_site_official=True,
            block_cross_site_repost=True,
        )
        self.assertEqual(res["classification"], "site_official")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["sitePriority"], 1)

    def test_strict_group_boundary_blocks_adwebfake(self):
        # ADWebFAKE MUST NOT match ADWeb!
        res = evaluate_release_group(
            title="Show.Name.S01E01.2026.2160p.WEB-DL.H265-ADWebFAKE",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="audiences",
            site_name="观众",
            require_site_official=True,
            block_cross_site_repost=True,
            no_fallback_to_non_official=True,
        )
        self.assertEqual(res["classification"], "non_official")
        self.assertFalse(res["allowed"])
        self.assertIn("不回退非官组", res["reasonText"])

    def test_cross_site_repost_frds_on_mteam_is_blocked(self):
        # FRDS on 馒头 MUST be flagged as cross-site repost (owner: 朋友) and rejected!
        res = evaluate_release_group(
            title="Movie.Name.2026.2160p.WEB-DL.H265-FRDS",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="mteam",
            site_name="馒头",
            require_site_official=True,
            block_cross_site_repost=True,
        )
        self.assertEqual(res["classification"], "cross_site_repost")
        self.assertFalse(res["allowed"])
        self.assertEqual(res["ownerSite"], "朋友")
        self.assertIn("跨站转载", res["reasonText"])

    def test_official_tag_site_passes_only_with_tag_and_blocks_cross_site(self):
        # 高清视界 uses official_tag mode
        without_tag = evaluate_release_group(
            title="Show.2026.S01E01.2160p-SomeGroup",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="hdvideo",
            has_official_tag=False,
        )
        self.assertFalse(without_tag["allowed"])
        self.assertEqual(without_tag["matchMode"], "official_tag")

        with_tag = evaluate_release_group(
            title="Show.2026.S01E01.2160p-HDVWEB",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="hdvideo",
            has_official_tag=True,
        )
        self.assertTrue(with_tag["allowed"])
        self.assertEqual(with_tag["classification"], "site_official")

        repost_on_tag_site = evaluate_release_group(
            title="Show.2026.S01E01.2160p-FRDS",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="hdvideo",
            has_official_tag=True,
        )
        self.assertFalse(repost_on_tag_site["allowed"])
        self.assertEqual(repost_on_tag_site["classification"], "cross_site_repost")

    def test_old_movie_from_chart_is_blocked_unless_collect(self):
        guardrails = {
            "newReleaseMaxAgeDays": 365,
            "newSeasonMaxAgeDays": 180,
            "blockOldMoviesFromCharts": True,
            "blockFinishedOldSeriesFromCharts": True,
            "onlySubscribeLatestSeasonForReturning": True,
        }
        old_movie = {"mediaType": "movie", "title": "经典老片", "releaseDate": "2012-05-01"}
        blocked = evaluate_subscription_guardrail(old_movie, guardrails, intent="follow", from_chart=True, today=date(2026, 11, 26))
        self.assertEqual(blocked["category"], "old_movie")
        self.assertFalse(blocked["allowed"])

        allowed_collect = evaluate_subscription_guardrail(old_movie, guardrails, intent="collect", from_chart=True, today=date(2026, 11, 26))
        self.assertTrue(allowed_collect["allowed"])

    def test_old_series_with_new_season_only_recommends_latest_season(self):
        guardrails = {
            "newReleaseMaxAgeDays": 365,
            "newSeasonMaxAgeDays": 180,
            "blockOldMoviesFromCharts": True,
            "blockFinishedOldSeriesFromCharts": True,
            "onlySubscribeLatestSeasonForReturning": True,
        }
        returning = {
            "mediaType": "tv",
            "title": "老牌长寿剧",
            "releaseDate": "2019-03-01",
            "status": "Returning Series",
            "lastAirDate": "2026-10-15",
            "seasons": [
                {"seasonNumber": 1, "airDate": "2019-03-01"},
                {"seasonNumber": 2, "airDate": "2021-04-01"},
                {"seasonNumber": 5, "airDate": "2026-10-01"},
            ],
        }
        res = evaluate_subscription_guardrail(returning, guardrails, intent="follow", from_chart=True, today=date(2026, 11, 26))
        self.assertEqual(res["category"], "returning_new_season")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["recommendedSeason"], 5)
        self.assertIn("仅订阅最新第 5 季", res["reasonText"])


if __name__ == "__main__":
    unittest.main()
