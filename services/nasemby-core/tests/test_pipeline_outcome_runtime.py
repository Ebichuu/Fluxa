from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline_outcome_runtime import (
    derive_media_result,
    derive_outcome_counts,
    derive_pipeline_outcome,
    derive_residual_issues,
)


NOW = datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc)


def fact(stage, state, *, scope="episode", evidence="verified", **updates):
    value = {
        "stage": stage,
        "state": state,
        "scope": scope,
        "evidence": evidence,
        "observedAt": "2026-07-27T03:59:00Z",
        "freshUntil": "2026-07-27T04:05:00Z",
        "source": stage,
        "sourceRef": f"{stage}-private-ref",
        "reasonCode": f"{stage.upper()}_{state.upper()}",
        "reasonText": state,
    }
    value.update(updates)
    return value


class PipelineOutcomeRuntimeTests(unittest.TestCase):
    def test_torra_symedia_and_strm_success_never_mean_playable(self):
        for stage in ("torra", "symedia", "strm"):
            with self.subTest(stage=stage):
                outcome = derive_pipeline_outcome(
                    [fact(stage, "succeeded", scope="season")],
                    target_scope="season",
                    now=NOW,
                )
                self.assertEqual(outcome["state"], "evidence_insufficient")

    def test_system_category_success_does_not_create_episode_media_result(self):
        result = derive_media_result(
            [fact("cloud115", "succeeded", scope="system-category")],
            target_scope="episode",
            now=NOW,
        )

        self.assertEqual(result["state"], "unknown")

    def test_only_verified_emby_movie_or_target_episode_is_playable(self):
        inferred = derive_pipeline_outcome(
            [fact("emby", "succeeded", evidence="inferred")],
            target_scope="episode",
            now=NOW,
        )
        season = derive_pipeline_outcome(
            [fact("emby", "succeeded", scope="season")],
            target_scope="season",
            now=NOW,
        )
        episode = derive_pipeline_outcome(
            [fact("emby", "succeeded")],
            target_scope="episode",
            now=NOW,
        )
        movie = derive_pipeline_outcome(
            [fact("emby", "succeeded", scope="movie")],
            target_scope="movie",
            now=NOW,
        )

        self.assertEqual(inferred["state"], "evidence_insufficient")
        self.assertEqual(season["state"], "evidence_insufficient")
        self.assertEqual(episode["state"], "playable")
        self.assertEqual(movie["state"], "playable")
        self.assertEqual(episode["playableAt"], "2026-07-27T03:59:00Z")

    def test_playable_time_prefers_first_confirmation_then_event_time(self):
        first_confirmed = derive_pipeline_outcome(
            [fact(
                "emby",
                "succeeded",
                eventAt="2026-07-27T03:30:00Z",
                firstConfirmedPlayableAt="2026-07-27T03:00:00Z",
            )],
            target_scope="episode",
            now=NOW,
        )
        event_time = derive_pipeline_outcome(
            [fact("emby", "succeeded", eventAt="2026-07-27T03:30:00Z")],
            target_scope="episode",
            now=NOW,
        )

        self.assertEqual(first_confirmed["playableAt"], "2026-07-27T03:00:00Z")
        self.assertEqual(event_time["playableAt"], "2026-07-27T03:30:00Z")

    def test_outcome_priority_distinguishes_recovery_failure_and_protection(self):
        recovering = derive_pipeline_outcome([
            fact(
                "cloud115",
                "failed",
                scope="system-category",
                plannedRetryAt="2026-07-27T05:00:00Z",
            )
        ], target_scope="season", now=NOW)
        failed = derive_pipeline_outcome(
            [fact("qb", "failed", scope="file")],
            target_scope="season",
            now=NOW,
        )
        protected = derive_pipeline_outcome(
            [fact("symedia", "protected", scope="file")],
            target_scope="season",
            now=NOW,
        )
        waiting = derive_pipeline_outcome(
            [fact("torra", "waiting", scope="season")],
            target_scope="season",
            now=NOW,
        )

        self.assertEqual(recovering["state"], "in_progress")
        self.assertEqual(failed["state"], "action_required")
        self.assertEqual(protected["state"], "protected")
        self.assertEqual(waiting["state"], "waiting")

        inferred_failure = derive_pipeline_outcome(
            [fact("qb", "failed", scope="file", evidence="inferred")],
            target_scope="season",
            now=NOW,
        )
        self.assertEqual(inferred_failure["state"], "evidence_insufficient")

    def test_outcome_counts_use_only_derived_states(self):
        counts = derive_outcome_counts([
            {"pipelineOutcome": {"state": "playable"}},
            {"pipelineOutcome": {"state": "in_progress"}},
            {"pipelineOutcome": {"state": "evidence_insufficient"}},
        ])

        self.assertEqual(counts["playable"], 1)
        self.assertEqual(counts["in_progress"], 1)
        self.assertEqual(counts["evidence_insufficient"], 1)
        self.assertEqual(sum(counts.values()), 3)

    def test_expired_verified_fact_is_preserved_but_not_treated_as_current(self):
        outcome = derive_pipeline_outcome([
            fact(
                "emby",
                "succeeded",
                observedAt="2026-07-26T03:59:00Z",
                freshUntil="2026-07-26T04:05:00Z",
            )
        ], target_scope="episode", now=NOW)

        self.assertEqual(outcome["state"], "evidence_insufficient")

    def test_media_result_uses_highest_verified_success_without_changing_outcome(self):
        facts = [
            fact("qb", "failed", scope="file", reasonText="qB 下载持续无活动"),
            fact("symedia", "succeeded", scope="file", eventAt="2026-07-27T03:50:00Z"),
        ]

        media_result = derive_media_result(facts, target_scope="episode", now=NOW)
        outcome = derive_pipeline_outcome(facts, target_scope="episode", now=NOW)
        residual = derive_residual_issues(facts, target_scope="episode", now=NOW)

        self.assertEqual(media_result, {
            "state": "archived",
            "stage": "symedia",
            "resultText": "已整理入库",
            "observedAt": "2026-07-27T03:59:00Z",
            "eventAt": "2026-07-27T03:50:00Z",
        })
        self.assertEqual(outcome["state"], "action_required")
        self.assertEqual(residual, [{
            "stage": "qb",
            "reasonCode": "QB_FAILED",
            "reasonText": "qB 下载持续无活动",
            "observedAt": "2026-07-27T03:59:00Z",
            "resourceCount": 1,
        }])

    def test_playable_result_keeps_upstream_failure_as_residual(self):
        facts = [
            fact("qb", "failed", scope="file"),
            fact("emby", "succeeded", scope="episode"),
        ]

        media_result = derive_media_result(facts, target_scope="episode", now=NOW)
        outcome = derive_pipeline_outcome(facts, target_scope="episode", now=NOW)
        residual = derive_residual_issues(facts, target_scope="episode", now=NOW)

        self.assertEqual(media_result["state"], "playable")
        self.assertEqual(outcome["state"], "playable")
        self.assertEqual(residual[0]["stage"], "qb")

    def test_failure_without_downstream_success_is_not_residual(self):
        facts = [fact("qb", "failed", scope="file")]

        self.assertEqual(
            derive_media_result(facts, target_scope="episode", now=NOW)["state"],
            "unknown",
        )
        self.assertEqual(
            derive_pipeline_outcome(facts, target_scope="episode", now=NOW)["state"],
            "action_required",
        )
        self.assertEqual(
            derive_residual_issues(facts, target_scope="episode", now=NOW),
            [],
        )

    def test_later_failure_and_planned_recovery_are_not_residual(self):
        later_failure = [
            fact("symedia", "succeeded", scope="file"),
            fact("strm", "failed", scope="episode"),
        ]
        recovering_upstream = [
            fact(
                "qb", "failed", scope="file",
                plannedRetryAt="2026-07-27T05:00:00Z",
            ),
            fact("symedia", "succeeded", scope="file"),
        ]

        self.assertEqual(
            derive_residual_issues(later_failure, target_scope="episode", now=NOW),
            [],
        )
        self.assertEqual(
            derive_residual_issues(recovering_upstream, target_scope="episode", now=NOW),
            [],
        )


if __name__ == "__main__":
    unittest.main()
