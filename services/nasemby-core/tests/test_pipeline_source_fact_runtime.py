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
            {"hash": "hash-a", "status": "completed", "state": "uploading", "progress": 1, "completionOn": 1785121200},
            {"hash": "hash-b", "status": "downloading", "state": "downloading", "progress": 0.5},
        ]), observed_at=OBSERVED_AT)
        qb = by_stage(facts, "qb")

        self.assertEqual(qb["state"], "active")
        self.assertEqual([unit["state"] for unit in qb["units"]], ["succeeded", "active"])
        self.assertEqual(qb["units"][0]["eventAt"], "2026-07-27T03:00:00Z")
        self.assertEqual(by_stage(facts, "cloud115")["state"], "unknown")

    def test_cloud115_file_failure_requires_exact_qb_path_evidence(self):
        from app.torra_read_runtime import secupload_file_path_key

        failure_file = {
            "fileKey": "private-file-key",
            "batchKey": "private-batch-key",
            "pathKey": secupload_file_path_key("/downloads/tv/Show.S01E03.mkv"),
            "displayName": "Show.S01E03.mkv",
            "errorCategory": "retry_failed",
            "errorLabel": "重试后仍失败",
            "retryCount": 3,
            "plannedRetryAt": "2026-07-28T08:00:00+08:00",
        }
        facts = build_pipeline_source_facts(context(
            qbTasks=[{
                "hash": "hash-a",
                "name": "Show.S01E03.mkv",
                "savePath": "/downloads/tv",
                "status": "completed",
                "state": "uploading",
                "progress": 1,
            }],
            cloud115={
                "readable": True,
                "perFileEvidence": True,
                "failureFiles": [failure_file],
            },
        ), observed_at=OBSERVED_AT)

        cloud = by_stage(facts, "cloud115")
        self.assertEqual((cloud["state"], cloud["scope"], cloud["evidence"]), ("failed", "file", "verified"))
        self.assertEqual(cloud["units"][0]["retryEligible"], True)
        self.assertEqual(cloud["units"][0]["plannedRetryAt"], "2026-07-28T08:00:00+08:00")

        unmatched = build_pipeline_source_facts(context(
            qbTasks=[{"hash": "hash-a", "name": "Other.mkv", "savePath": "/downloads/tv"}],
            cloud115={"readable": True, "perFileEvidence": True, "failureFiles": [failure_file]},
        ), observed_at=OBSERVED_AT)
        self.assertEqual(by_stage(unmatched, "cloud115")["state"], "unknown")

    def test_symedia_success_does_not_infer_strm_or_emby_episode(self):
        facts = build_pipeline_source_facts(context(
            symediaRows=[{
                "id": "symedia-private-1",
                "status": True,
                "date": "2026-07-27 11:30:00",
                "dest": "/strm/Test.Show/S01E03.strm",
            }],
            embyIndex={"movies": set(), "series": {"100"}, "episodes": set()},
        ), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(facts, "symedia")["state"], "succeeded")
        self.assertEqual(by_stage(facts, "symedia")["eventAt"], "2026-07-27T03:30:00Z")
        self.assertEqual(by_stage(facts, "strm")["state"], "unknown")
        self.assertEqual(by_stage(facts, "strm")["reasonCode"], "STRM_INDEPENDENT_RESULT_MISSING")
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
        self.assertEqual(by_stage(episode, "emby")["firstConfirmedPlayableAt"], OBSERVED_AT)

    def test_emby_range_keeps_parent_unknown_and_emits_episode_units(self):
        facts = build_pipeline_source_facts(context(
            episodeNumber=None,
            episodeEvidence=[{
                "ownerTargetKey": "tv:tmdb:100:season:1:episodes:2-3",
                "seasonNumber": 1,
                "episodeStart": 2,
                "episodeEnd": 3,
            }],
            embyIndex={
                "movies": set(),
                "series": {"100"},
                "episodes": {("100", 1, 2), ("100", 1, 3)},
            },
        ), observed_at=OBSERVED_AT)

        emby = by_stage(facts, "emby")
        self.assertEqual((emby["state"], emby["evidence"]), ("unknown", "missing"))
        self.assertEqual([unit["state"] for unit in emby["units"]], ["succeeded", "succeeded"])
        self.assertTrue(all(unit["eventAt"] == OBSERVED_AT for unit in emby["units"]))


if __name__ == "__main__":
    unittest.main()
