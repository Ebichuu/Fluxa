from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.qbittorrent_assessment_runtime import assess_qb_task, summarize_qb_assessments


OBSERVED_AT = datetime(2026, 7, 29, 6, 0, tzinfo=timezone.utc)


def task(**updates):
    value = {
        "hash": "hash-test",
        "status": "stalled",
        "state": "stalledDL",
        "progress": 0.4,
        "dlspeed": 0,
    }
    value.update(updates)
    return value


class QbittorrentAssessmentRuntimeTests(unittest.TestCase):
    def test_observation_window_changes_at_exactly_900_seconds(self):
        observing = assess_qb_task(task(
            lastActivity=(OBSERVED_AT - timedelta(seconds=899)).isoformat(),
        ), OBSERVED_AT)
        action_required = assess_qb_task(task(
            lastActivity=(OBSERVED_AT - timedelta(seconds=900)).isoformat(),
        ), OBSERVED_AT)

        self.assertEqual(
            (observing["state"], observing["factState"], observing["inactiveSeconds"]),
            ("observing", "waiting", 899),
        )
        self.assertEqual(observing["reasonCode"], "QB_DOWNLOAD_STALLED_OBSERVING")
        self.assertEqual(
            (action_required["state"], action_required["factState"], action_required["inactiveSeconds"]),
            ("action_required", "failed", 900),
        )
        self.assertEqual(action_required["reasonCode"], "QB_DOWNLOAD_STALLED")

    def test_task_priority_covers_errors_activity_and_stable_states(self):
        cases = (
            (task(state="missingFiles", dlspeed=2048), "action_required", "failed", "QB_MISSING_FILES"),
            (task(state="error", dlspeed=2048), "action_required", "failed", "QB_DOWNLOAD_FAILED"),
            (task(status="queued", state="checkingDL"), "normal", "active", "QB_CHECKING"),
            (task(status="completed", state="uploading", progress=1), "normal", "succeeded", "QB_SEEDING"),
            (task(status="paused", state="pausedDL"), "normal", "waiting", "QB_DOWNLOAD_PAUSED"),
            (task(status="queued", state="queuedDL"), "normal", "waiting", "QB_DOWNLOAD_QUEUED"),
            (task(state="stalledDL", dlspeed=2048), "normal", "active", "QB_DOWNLOAD_ACTIVE"),
        )

        for source, state, fact_state, code in cases:
            with self.subTest(code=code):
                result = assess_qb_task(source, OBSERVED_AT)
                self.assertEqual((result["state"], result["factState"], result["reasonCode"]), (
                    state, fact_state, code,
                ))

    def test_missing_invalid_or_future_activity_stays_observing(self):
        cases = (
            task(),
            task(lastActivity="invalid"),
            task(lastActivity=(OBSERVED_AT + timedelta(seconds=1)).isoformat()),
        )

        for source in cases:
            with self.subTest(last_activity=source.get("lastActivity")):
                result = assess_qb_task(source, OBSERVED_AT)
                self.assertEqual((result["state"], result["factState"]), ("observing", "waiting"))
                self.assertIsNone(result["inactiveSeconds"])
                self.assertEqual(result["durationText"], "持续时间暂未确认")

    def test_unrecognized_status_is_unknown(self):
        result = assess_qb_task(task(status="", state="unknownState"), OBSERVED_AT)

        self.assertEqual((result["state"], result["factState"], result["countCategory"]), (
            "unknown", "unknown", "unknown",
        ))
        self.assertEqual(result["evidence"], "missing")

    def test_aggregate_priority_and_counts_for_mixed_tasks(self):
        results = [
            assess_qb_task(task(hash="observing", lastActivity=(OBSERVED_AT - timedelta(seconds=30)).isoformat()), OBSERVED_AT),
            assess_qb_task(task(hash="unknown", status="", state="unknownState"), OBSERVED_AT),
            assess_qb_task(task(hash="failed", state="error"), OBSERVED_AT),
            assess_qb_task(task(hash="active", status="downloading", state="downloading", dlspeed=1024), OBSERVED_AT),
            assess_qb_task(task(hash="waiting", status="paused", state="pausedDL"), OBSERVED_AT),
        ]

        summary = summarize_qb_assessments(results, OBSERVED_AT)

        self.assertEqual(summary["state"], "action_required")
        self.assertEqual(summary["counts"], {
            "processing": 1,
            "waiting": 1,
            "observing": 1,
            "actionRequired": 1,
            "unknown": 1,
        })
        self.assertEqual(summary["reasonCode"], "QB_DOWNLOAD_FAILED")
        without_failure = summarize_qb_assessments(results[:-3] + results[-2:], OBSERVED_AT)
        self.assertEqual(without_failure["state"], "unknown")
        without_unknown = summarize_qb_assessments([results[0], *results[-2:]], OBSERVED_AT)
        self.assertEqual(without_unknown["state"], "observing")

    def test_same_task_and_observed_at_are_deterministic(self):
        source = task(lastActivity=(OBSERVED_AT - timedelta(seconds=42)).isoformat())

        first = assess_qb_task(source, OBSERVED_AT)
        second = assess_qb_task(source, OBSERVED_AT)

        self.assertEqual(first, second)
        self.assertEqual(
            summarize_qb_assessments([first], OBSERVED_AT),
            summarize_qb_assessments([second], OBSERVED_AT),
        )


if __name__ == "__main__":
    unittest.main()
