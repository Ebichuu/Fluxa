from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.pipeline_fact_runtime import (
    PIPELINE_STAGES,
    PipelineFactValidationError,
    merge_pipeline_facts,
    normalize_pipeline_fact,
)
from app.task_public_runtime import present_pipeline_fact


NOW = datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc)


def fact(**updates):
    value = {
        "stage": "torra",
        "state": "succeeded",
        "scope": "season",
        "evidence": "verified",
        "eventAt": "2026-07-27T03:58:00Z",
        "observedAt": "2026-07-27T03:59:00Z",
        "freshUntil": "2026-07-27T04:05:00Z",
        "source": "Torra",
        "sourceRef": "subscription-private-1",
        "reasonCode": "TORRA_TARGET_SATISFIED",
        "reasonText": "获取目标已满足",
    }
    value.update(updates)
    return value


class PipelineFactRuntimeTests(unittest.TestCase):
    def test_normalize_keeps_event_time_separate_from_observation_time(self):
        normalized = normalize_pipeline_fact(fact(
            stage="emby",
            scope="episode",
            firstConfirmedPlayableAt="2026-07-27T03:58:00Z",
        ))

        self.assertEqual(normalized["eventAt"], "2026-07-27T03:58:00Z")
        self.assertEqual(normalized["observedAt"], "2026-07-27T03:59:00Z")
        self.assertEqual(normalized["firstConfirmedPlayableAt"], "2026-07-27T03:58:00Z")

    def test_normalize_rejects_invalid_enums_and_unknown_fields(self):
        for field, value in (
            ("stage", "acquisition"),
            ("state", "done"),
            ("scope", "title"),
            ("evidence", "assumed"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(PipelineFactValidationError):
                    normalize_pipeline_fact(fact(**{field: value}))

        with self.assertRaises(PipelineFactValidationError):
            normalize_pipeline_fact(fact(privatePath="/media/private/file.mkv"))

        missing_time = fact()
        missing_time.pop("observedAt")
        with self.assertRaises(PipelineFactValidationError):
            normalize_pipeline_fact(missing_time)

        with self.assertRaises(PipelineFactValidationError):
            normalize_pipeline_fact(fact(state="waiting", evidence="missing"))

    def test_unknown_missing_remains_unknown_instead_of_failed(self):
        normalized = normalize_pipeline_fact(fact(
            stage="strm",
            state="unknown",
            scope="season",
            evidence="missing",
            source="",
            sourceRef="",
            reasonCode="STRM_EVIDENCE_MISSING",
            reasonText="缺少 STRM 服务证据",
        ))

        self.assertEqual(normalized["state"], "unknown")
        self.assertEqual(normalized["evidence"], "missing")

    def test_merge_returns_one_summary_for_each_independent_stage(self):
        facts = merge_pipeline_facts(
            [fact()],
            target_scope="season",
            observed_at="2026-07-27T04:00:00Z",
            now=NOW,
        )

        self.assertEqual(tuple(row["stage"] for row in facts), PIPELINE_STAGES)
        self.assertEqual(next(row for row in facts if row["stage"] == "torra")["state"], "succeeded")
        for row in facts:
            if row["stage"] != "torra":
                self.assertEqual((row["state"], row["evidence"]), ("unknown", "missing"))

    def test_conflicting_current_facts_do_not_select_a_false_winner(self):
        facts = merge_pipeline_facts(
            [
                fact(stage="qb", state="active", scope="file", reasonCode="QB_ACTIVE"),
                fact(stage="qb", state="failed", scope="file", reasonCode="QB_FAILED"),
            ],
            target_scope="season",
            observed_at="2026-07-27T04:00:00Z",
            now=NOW,
        )
        qb = next(row for row in facts if row["stage"] == "qb")

        self.assertEqual((qb["state"], qb["evidence"]), ("unknown", "missing"))
        self.assertEqual(qb["reasonCode"], "EVIDENCE_CONFLICT")

    def test_public_presenter_whitelists_and_redacts_fact_references(self):
        normalized = normalize_pipeline_fact(fact(
            stage="emby",
            state="succeeded",
            scope="episode",
            sourceRef="emby-private-item-123",
            unitKey="/media/private/show/S01E03.mkv",
            reasonText="indexed /media/private/show/S01E03.mkv",
        ))
        normalized["privatePath"] = "/media/private/show/S01E03.mkv"

        public = present_pipeline_fact(normalized)

        self.assertTrue(public["sourceRef"].startswith("fact:emby:"))
        self.assertTrue(public["unitKey"].startswith("unit:"))
        self.assertNotIn("emby-private-item-123", str(public))
        self.assertNotIn("/media/private", str(public))
        self.assertNotIn("privatePath", public)


if __name__ == "__main__":
    unittest.main()
