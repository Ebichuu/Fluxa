from __future__ import annotations

import unittest

from app.rss_shadow_scoring_runtime import (
    ShadowScoringUnsupported,
    rss_artifact_key,
    rss_target_key,
    score_rss_candidate,
    select_subscription_rule,
    stable_payload_hash,
)


def rule(rule_id="anime-rule", category="tv::anime"):
    return {
        "id": rule_id,
        "name": "Anime rule",
        "media_type": "tv",
        "category": [category],
        "videoFormat": {
            "blacklist": [],
            "whitelist": [],
            "screen_2160p": {
                "name": "2160p",
                "pattern": "2160p|4K",
                "score": 10,
            },
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


class RssShadowScoringRuntimeTests(unittest.TestCase):
    def test_selects_one_subscription_rule_without_fuzzy_title_matching(self):
        selected, reason = select_subscription_rule(
            [rule(), rule("movie-rule", "movie::animation")],
            {
                "media_type": "tv",
                "save_path": "/downloads/00-anime",
                "version_control_weight_by_category": True,
            },
        )

        self.assertEqual(reason, "")
        self.assertEqual(selected["id"], "anime-rule")

        selected, reason = select_subscription_rule(
            [rule("one"), rule("two")],
            {"media_type": "tv", "save_path": "/downloads/00-anime"},
        )
        self.assertEqual(reason, "")
        self.assertEqual(selected["id"], "one")

    def test_scores_supported_rule_and_keeps_version_decision_separate(self):
        result = score_rss_candidate(rule(), {
            "title": "Show.S01E01.2160p.WEB-DL.mkv",
            "size_bytes": 2_000_000_000,
        })

        self.assertEqual(result["score"], 30.0)
        self.assertEqual(result["versionState"], "accepted")
        self.assertEqual(result["versionName"], "MKV version")
        self.assertEqual(
            {row["field"] for row in result["breakdown"]},
            {"videoFormat", "file_extension", "custom_attributes", "file_size"},
        )

    def test_missing_candidate_field_and_invalid_rule_never_become_zero(self):
        with self.assertRaisesRegex(ShadowScoringUnsupported, "candidate_size_unconfirmed"):
            score_rss_candidate(rule(), {"title": "Show.S01E01.2160p.WEB-DL.mkv"})

        invalid = rule()
        invalid["custom_attributes"][0]["pattern"] = "["
        with self.assertRaisesRegex(ShadowScoringUnsupported, "rule_pattern_invalid"):
            score_rss_candidate(invalid, {
                "title": "Show.S01E01.2160p.WEB-DL.mkv",
                "size_bytes": 1,
            })

        unconfirmed = score_rss_candidate(rule(), {
            "title": "Show.S01E01.2160p.WEB-DL",
            "size_bytes": 1,
        })
        self.assertEqual(unconfirmed["score"], 28.0)
        self.assertEqual(unconfirmed["versionState"], "unconfirmed")
        self.assertEqual(unconfirmed["versionName"], "")

    def test_range_target_and_artifact_keys_are_stable(self):
        item = {
            "fingerprint": "same-rss-guid",
            "media_type": "tv",
            "tmdb_id": "123",
            "season_number": 1,
            "episode_start": 2,
            "episode_end": 3,
        }

        self.assertEqual(
            rss_target_key(item),
            "tv:tmdb:123:season:1:episodes:2-3",
        )
        self.assertEqual(rss_artifact_key(item), rss_artifact_key(dict(item)))
        self.assertNotIn("same-rss-guid", rss_artifact_key(item))
        self.assertEqual(stable_payload_hash({"b": 2, "a": 1}), stable_payload_hash({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
