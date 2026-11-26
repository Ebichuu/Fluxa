from __future__ import annotations

import unittest
from datetime import date

from app.config import DEFAULT_SITE_OFFICIAL_GROUPS
from app.domain.release_rules import evaluate_release_group, evaluate_subscription_guardrail


class ReleaseRulesTests(unittest.TestCase):
    def test_site_official_group_matches_same_site(self):
        res = evaluate_release_group(
            title="Show.Name.S01E01.2026.2160p.WEB-DL.H265-HDHWEB",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="hdhome",
            site_name="HDHome",
            require_site_official=True,
            block_cross_site_repost=True,
        )
        self.assertEqual(res["classification"], "site_official")
        self.assertTrue(res["allowed"])
        self.assertEqual(res["releaseGroup"], "HDHWEB")

    def test_cross_site_repost_is_flagged_and_blocked(self):
        # M-Team site posting an HDHome official group => cross-site repost, NOT site official!
        res = evaluate_release_group(
            title="Show.Name.S01E01.2026.2160p.WEB-DL.H265-HDHWEB",
            site_official_groups=DEFAULT_SITE_OFFICIAL_GROUPS,
            site_id="mteam",
            site_name="M-Team",
            require_site_official=False,
            block_cross_site_repost=True,
        )
        self.assertEqual(res["classification"], "cross_site_repost")
        self.assertFalse(res["allowed"])
        self.assertEqual(res["ownerSite"], "HDHome")
        self.assertIn("跨站转载", res["reasonText"])

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
