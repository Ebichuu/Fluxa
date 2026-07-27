from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline_outcome_runtime import derive_pipeline_outcome
from app.pipeline_source_fact_runtime import build_pipeline_source_facts


OBSERVED_AT = "2026-07-27T04:00:00Z"


def context(**updates):
    value = {
        "mediaType": "tv",
        "tmdbId": "100",
        "seasonNumber": 1,
        "episodeNumber": 3,
        "torra": None,
        "qbTasks": [],
        "cloud115": {"readable": True, "perFileEvidence": False},
        "symediaRows": [],
        "embyIndex": {"movies": set(), "series": set(), "episodes": set()},
    }
    value.update(updates)
    return value


def by_stage(facts, stage):
    return next(fact for fact in facts if fact["stage"] == stage)


class PipelineSourceFactRuntimeTests(unittest.TestCase):
    def test_torra_completed_means_target_satisfied_only(self):
        facts = build_pipeline_source_facts(context(
            torra={"id": "torra-private-1", "completed": True, "is_running": False},
        ), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(facts, "torra")["state"], "succeeded")
        self.assertEqual(by_stage(facts, "torra")["reasonCode"], "TORRA_TARGET_SATISFIED")
        self.assertEqual(by_stage(facts, "qb")["state"], "unknown")
        self.assertEqual(by_stage(facts, "cloud115")["state"], "unknown")
        outcome = derive_pipeline_outcome(
            facts,
            target_scope="episode",
            now=datetime(2026, 7, 27, 4, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(outcome["state"], "waiting")
        self.assertEqual(outcome["stage"], "emby")

    def test_qb_summary_uses_file_units_and_does_not_complete_cloud115(self):
        facts = build_pipeline_source_facts(context(qbTasks=[
            {"hash": "hash-a", "status": "completed", "state": "uploading", "progress": 1},
            {"hash": "hash-b", "status": "downloading", "state": "downloading", "progress": 0.5},
        ]), observed_at=OBSERVED_AT)
        qb = by_stage(facts, "qb")

        self.assertEqual(qb["state"], "active")
        self.assertEqual([unit["state"] for unit in qb["units"]], ["succeeded", "active"])
        self.assertEqual(by_stage(facts, "cloud115")["state"], "unknown")

    def test_symedia_success_does_not_infer_strm_or_emby_episode(self):
        facts = build_pipeline_source_facts(context(
            symediaRows=[{
                "id": "symedia-private-1",
                "status": True,
                "dest": "/strm/Test.Show/S01E03.strm",
            }],
            embyIndex={"movies": set(), "series": {"100"}, "episodes": set()},
        ), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(facts, "symedia")["state"], "succeeded")
        self.assertEqual(by_stage(facts, "strm")["state"], "unknown")
        self.assertEqual(by_stage(facts, "emby")["state"], "unknown")
        self.assertEqual(by_stage(facts, "emby")["reasonCode"], "EMBY_EPISODE_EVIDENCE_MISSING")

    def test_symedia_protection_and_real_failure_remain_distinct(self):
        protected = build_pipeline_source_facts(context(symediaRows=[{
            "id": "protected",
            "status": False,
            "reasonCode": "QUALITY_HIGHER_VERSION_EXISTS",
            "errmsg": "higher quality version exists",
        }]), observed_at=OBSERVED_AT)
        failed = build_pipeline_source_facts(context(symediaRows=[{
            "id": "failed",
            "status": False,
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "errmsg": "media lookup failed",
        }]), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(protected, "symedia")["state"], "protected")
        self.assertEqual(by_stage(failed, "symedia")["state"], "failed")

    def test_symedia_numeric_status_is_normalized_and_missing_status_stays_unknown(self):
        facts = build_pipeline_source_facts(context(symediaRows=[
            {"id": "success", "status": 1},
            {"id": "protected", "status": 0, "errmsg": "源文件评分低于目标文件，取消覆盖"},
            {"id": "unknown"},
        ]), observed_at=OBSERVED_AT)

        symedia = by_stage(facts, "symedia")

        self.assertEqual(
            [unit["state"] for unit in symedia["units"]],
            ["succeeded", "protected", "unknown"],
        )
        self.assertEqual(symedia["state"], "succeeded")

    def test_emby_requires_movie_or_exact_episode_evidence(self):
        movie = build_pipeline_source_facts(context(
            mediaType="movie",
            tmdbId="200",
            seasonNumber=0,
            episodeNumber=None,
            embyIndex={"movies": {"200"}, "series": set(), "episodes": set()},
        ), observed_at=OBSERVED_AT)
        episode = build_pipeline_source_facts(context(
            embyIndex={"movies": set(), "series": {"100"}, "episodes": {("100", 1, 3)}},
        ), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(movie, "emby")["scope"], "movie")
        self.assertEqual(by_stage(movie, "emby")["state"], "succeeded")
        self.assertEqual(by_stage(episode, "emby")["scope"], "episode")
        self.assertEqual(by_stage(episode, "emby")["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
