from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.task_exception_runtime import classify_stage, classify_task


NOW = datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)


def stage(status="done", **overrides):
    value = {
        "key": "download",
        "label": "获取 / 下载",
        "status": status,
        "evidence": "verified",
        "source": "qBittorrent",
        "reasonText": "下载已完成",
        "observedAt": "2026-07-22T05:59:00Z",
        "freshUntil": "2026-07-22T06:05:00Z",
    }
    value.update(overrides)
    return value


class TaskExceptionRuntimeTests(unittest.TestCase):
    def test_blocked_stage_requires_attention(self):
        result = classify_stage(stage("blocked", reasonCode="DOWNLOAD_STALLED", reasonText="qB 卡住在 12%"), now=NOW)
        self.assertEqual(result["healthState"], "action_required")
        self.assertEqual(result["reasonCode"], "DOWNLOAD_STALLED")
        self.assertFalse(result["retryEligible"])

    def test_expired_evidence_is_not_normal(self):
        result = classify_stage(stage(freshUntil="2026-07-22T05:59:00Z"), now=NOW)
        self.assertEqual(result["healthState"], "evidence_insufficient")
        self.assertEqual(result["reasonCode"], "EVIDENCE_EXPIRED")

    def test_active_stage_is_waiting_even_without_downstream_evidence(self):
        result = classify_stage(stage("waiting", evidence="missing", reasonText="等待秒传"), now=NOW)
        self.assertEqual(result["healthState"], "waiting")
        self.assertEqual(result["recommendedAction"], "等待当前阶段完成")

    def test_planned_retry_is_waiting(self):
        result = classify_stage(
            stage("blocked", nextRetryAt="2026-07-22T08:00:00Z", reasonText="秒传失败，已安排下一轮"),
            now=NOW,
        )
        self.assertEqual(result["healthState"], "waiting")
        self.assertEqual(result["reasonCode"], "RETRY_SCHEDULED")
        self.assertEqual(result["plannedRetryAt"], "2026-07-22T08:00:00Z")

    def test_low_score_rejection_is_protected_without_retry(self):
        result = classify_stage(
            stage("blocked", reasonCode="QUALITY_LOW_SCORE", reasonText="现有版本评分更高，已跳过低分源文件"),
            now=NOW,
        )
        self.assertEqual(result["healthState"], "protected")
        self.assertEqual(result["recommendedAction"], "已保留低分源文件，可进入存储清理")
        self.assertFalse(result["retryEligible"])

    def test_legacy_protection_messages_map_to_structured_rules(self):
        cases = {
            "源文件评分低于目标文件，取消覆盖": "QUALITY_SCORE_LOWER",
            "源文件权重低于或等于目标，不执行覆盖": "QUALITY_WEIGHT_NOT_HIGHER",
            "已有更高质量版本": "QUALITY_HIGHER_VERSION_EXISTS",
            "重复资源跳过": "DUPLICATE_RESOURCE_SKIPPED",
        }
        for message, reason_code in cases.items():
            with self.subTest(message=message):
                result = classify_stage(stage("blocked", reasonCode="", reasonText=message), now=NOW)
                self.assertEqual(result["healthState"], "protected")
                self.assertEqual(result["matchedProtectionRule"], reason_code)

    def test_real_failure_wins_over_protection_but_protection_wins_over_unknown(self):
        protected = stage("blocked", key="library", reasonText="评分低于目标文件，取消覆盖")
        missing = stage("unknown", key="emby", evidence="missing", reasonText="尚无 Emby 证据")
        protected_result = classify_task(
            {"state": "blocked", "confidence": "strong", "stages": [protected, missing]},
            now=NOW,
        )
        self.assertEqual(protected_result["healthState"], "protected")

        real_failure = stage("blocked", key="cloud115", reasonCode="UPLOAD_FAILED", reasonText="上传失败")
        failed_result = classify_task(
            {"state": "blocked", "confidence": "strong", "stages": [protected, real_failure]},
            now=NOW,
        )
        self.assertEqual(failed_result["healthState"], "action_required")
        self.assertEqual(failed_result["reasonCode"], "UPLOAD_FAILED")

    def test_completed_task_is_normal_only_when_all_stages_are_verified(self):
        item = {
            "state": "completed",
            "confidence": "strong",
            "stages": [stage(), stage("done", key="cloud115", label="进入 115", source="Symedia")],
        }
        result = classify_task(item, now=NOW, observed_at="2026-07-22T06:00:00Z", fresh_until="2026-07-22T06:05:00Z")
        self.assertEqual(result["healthState"], "normal")

    def test_unlinked_identity_is_evidence_insufficient(self):
        result = classify_task({"state": "waiting", "confidence": "unlinked", "stages": []}, now=NOW)
        self.assertEqual(result["healthState"], "evidence_insufficient")
        self.assertEqual(result["reasonCode"], "TASK_IDENTITY_UNLINKED")

    def test_artifact_identity_conflict_requires_attention(self):
        result = classify_task({
            "state": "blocked",
            "confidence": "strong",
            "reasonCode": "ARTIFACT_CHAIN_CONFLICT",
            "reasonText": "资源产物已经属于其他任务链",
            "stages": [],
        }, now=NOW)
        self.assertEqual(result["healthState"], "action_required")
        self.assertEqual(result["reasonCode"], "ARTIFACT_CHAIN_CONFLICT")


if __name__ == "__main__":
    unittest.main()
