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
        "embyIndex": {
            "movies": set(), "series": set(), "episodes": set(),
            "strmMovies": set(), "strmEpisodes": set(),
        },
    }
    value.update(updates)
    return value


def by_stage(facts, stage):
    return next(fact for fact in facts if fact["stage"] == stage)


class PipelineSourceFactRuntimeTests(unittest.TestCase):
    def test_torra_completed_means_target_satisfied_only(self):
        facts = build_pipeline_source_facts(context(
            torra={
                "id": "torra-private-1",
                "completed": True,
                "is_running": False,
                "updated_at": "2026-07-27T03:30:00Z",
            },
        ), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(facts, "torra")["state"], "succeeded")
        self.assertEqual(by_stage(facts, "torra")["reasonCode"], "TORRA_TARGET_SATISFIED")
        self.assertNotIn("eventAt", by_stage(facts, "torra"))
        self.assertEqual(by_stage(facts, "qb")["state"], "unknown")
        self.assertEqual(by_stage(facts, "cloud115")["state"], "unknown")
        outcome = derive_pipeline_outcome(
            facts,
            target_scope="episode",
            now=datetime(2026, 7, 27, 4, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(outcome["state"], "waiting")
        self.assertEqual(outcome["stage"], "emby")

    def test_torra_completed_uses_only_explicit_completion_time(self):
        facts = build_pipeline_source_facts(context(
            torra={
                "id": "torra-private-1",
                "completed": True,
                "completedAt": "2026-07-27T03:30:00Z",
                "updated_at": "2026-07-27T03:45:00Z",
            },
        ), observed_at=OBSERVED_AT)

        self.assertEqual(
            by_stage(facts, "torra")["eventAt"],
            "2026-07-27T03:30:00Z",
        )

    def test_qb_summary_uses_file_units_and_does_not_complete_cloud115(self):
        facts = build_pipeline_source_facts(context(qbTasks=[
            {"hash": "hash-a", "status": "completed", "state": "uploading", "progress": 1, "completionOn": 1785121200},
            {"hash": "hash-b", "status": "downloading", "state": "downloading", "progress": 0.5, "dlspeed": 1024},
        ]), observed_at=OBSERVED_AT)
        qb = by_stage(facts, "qb")

        self.assertEqual(qb["state"], "active")
        self.assertEqual([unit["state"] for unit in qb["units"]], ["succeeded", "active"])
        self.assertEqual(qb["units"][0]["eventAt"], "2026-07-27T03:00:00Z")
        self.assertEqual(by_stage(facts, "cloud115")["state"], "unknown")

    def test_qb_single_task_explains_status_activity_age_and_action(self):
        facts = build_pipeline_source_facts(context(qbTasks=[{
            "hash": "hash-stalled",
            "status": "stalled",
            "state": "stalledDL",
            "progress": 0.4,
            "dlspeed": 0,
            "lastActivity": "2026-07-27T02:00:00Z",
        }]), observed_at=OBSERVED_AT)

        qb = by_stage(facts, "qb")

        self.assertEqual(qb["state"], "failed")
        self.assertEqual(qb["reasonCode"], "QB_DOWNLOAD_STALLED")
        self.assertIn("qB 下载持续无活动", qb["reasonText"])
        self.assertIn("2 小时无下载活动", qb["reasonText"])
        self.assertIn("建议检查 Tracker、网络和可用做种", qb["reasonText"])

    def test_qb_facts_distinguish_waiting_no_speed_checking_seeding_and_failure(self):
        cases = (
            ({"status": "downloading", "state": "downloading", "progress": 0.4, "dlspeed": 0}, "waiting", "QB_DOWNLOAD_STALLED_OBSERVING", "短暂无下载活动"),
            ({"status": "queued", "state": "queuedDL", "progress": 0.2}, "waiting", "QB_DOWNLOAD_QUEUED", "等待下载"),
            ({"status": "queued", "state": "checkingDL", "progress": 0.2}, "active", "QB_CHECKING", "正在校验"),
            ({"status": "completed", "state": "uploading", "progress": 1}, "succeeded", "QB_SEEDING", "正在做种"),
            ({"status": "stalled", "state": "error", "progress": 0.2}, "failed", "QB_DOWNLOAD_FAILED", "发生错误"),
        )

        for index, (task, state, code, text) in enumerate(cases):
            with self.subTest(code=code):
                facts = build_pipeline_source_facts(context(qbTasks=[{
                    "hash": f"hash-{index}",
                    **task,
                }]), observed_at=OBSERVED_AT)
                qb = by_stage(facts, "qb")
                self.assertEqual((qb["state"], qb["reasonCode"]), (state, code))
                self.assertIn(text, qb["reasonText"])
                if code != "QB_DOWNLOAD_STALLED":
                    self.assertIn("持续时间暂未确认", qb["reasonText"])

    def test_qb_observation_window_boundaries_and_priority(self):
        cases = (
            (
                {"status": "stalled", "state": "stalledDL", "dlspeed": 0, "lastActivity": "2026-07-27T03:45:01Z"},
                "waiting", "QB_DOWNLOAD_STALLED_OBSERVING", "14 分钟无下载活动",
            ),
            (
                {"status": "stalled", "state": "stalledDL", "dlspeed": 0, "lastActivity": "2026-07-27T03:45:00Z"},
                "failed", "QB_DOWNLOAD_STALLED", "15 分钟无下载活动",
            ),
            (
                {"status": "stalled", "state": "stalledDL", "dlspeed": 1024, "lastActivity": "2026-07-27T02:00:00Z"},
                "active", "QB_DOWNLOAD_ACTIVE", "正在下载",
            ),
            (
                {"status": "stalled", "state": "missingFiles", "dlspeed": 1024},
                "failed", "QB_MISSING_FILES", "文件缺失",
            ),
            (
                {"status": "stalled", "state": "error", "dlspeed": 1024},
                "failed", "QB_DOWNLOAD_FAILED", "发生错误",
            ),
            (
                {"status": "stalled", "state": "stalledDL", "dlspeed": 0},
                "waiting", "QB_DOWNLOAD_STALLED_OBSERVING", "持续时间暂未确认",
            ),
            (
                {"status": "stalled", "state": "stalledDL", "dlspeed": 0, "lastActivity": "2026-07-27T04:01:00Z"},
                "waiting", "QB_DOWNLOAD_STALLED_OBSERVING", "持续时间暂未确认",
            ),
        )

        for index, (task, state, code, text) in enumerate(cases):
            with self.subTest(index=index, code=code):
                qb = by_stage(build_pipeline_source_facts(context(qbTasks=[{
                    "hash": f"window-{index}", "progress": 0.4, **task,
                }]), observed_at=OBSERVED_AT), "qb")
                self.assertEqual((qb["state"], qb["reasonCode"]), (state, code))
                self.assertIn(text, qb["reasonText"])

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

    def test_symedia_explicit_strm_destination_confirms_strm(self):
        facts = build_pipeline_source_facts(context(
            symediaRows=[{
                "id": "symedia-private-1",
                "status": True,
                "date": "2026-07-27 11:30:00",
                "dest": "/strm/Test.Show/S01E03.strm",
            }],
            symediaStrmStats=[{"date": "2026-07-27", "count": 8}],
            embyIndex={"movies": set(), "series": {"100"}, "episodes": set()},
        ), observed_at=OBSERVED_AT)

        self.assertEqual(by_stage(facts, "symedia")["state"], "succeeded")
        self.assertEqual(by_stage(facts, "symedia")["eventAt"], "2026-07-27T03:30:00Z")
        self.assertEqual(by_stage(facts, "strm")["state"], "succeeded")
        self.assertEqual(by_stage(facts, "strm")["reasonCode"], "STRM_CREATED")
        self.assertEqual(by_stage(facts, "emby")["state"], "unknown")
        self.assertEqual(by_stage(facts, "emby")["reasonCode"], "EMBY_EPISODE_EVIDENCE_MISSING")

    def test_cloud115_success_requires_path_bound_success_file(self):
        from app.torra_read_runtime import secupload_file_path_key

        path = "/downloads/tv/Show.S01E03.mkv"
        facts = build_pipeline_source_facts(context(
            qbTasks=[{
                "hash": "hash-a",
                "name": "Show.S01E03.mkv",
                "savePath": "/downloads/tv",
                "status": "completed",
            }],
            cloud115={"readable": True, "successFiles": [{
                "fileKey": "success-file",
                "batchKey": "success-batch",
                "pathKey": secupload_file_path_key(path),
                "observedAt": OBSERVED_AT,
            }]},
        ), observed_at=OBSERVED_AT)

        cloud = by_stage(facts, "cloud115")
        self.assertEqual(
            (cloud["state"], cloud["evidence"], cloud["reasonCode"]),
            ("succeeded", "verified", "CLOUD115_FILE_UPLOADED"),
        )

    def test_cloud115_arrival_can_be_confirmed_by_current_symedia_115_source(self):
        facts = build_pipeline_source_facts(context(symediaRows=[{
            "id": "symedia-arrival-1",
            "status": True,
            "date": "2026-07-27 11:30:00",
            "src": "/CloudNAS/CloudDrive/115/00-待整理/03-日韩剧/Show.S01E03.mkv",
            "dest": "/CloudNAS/CloudDrive/115/媒体库/电视剧/Show/Show.S01E03.mkv",
        }]), observed_at=OBSERVED_AT)

        cloud = by_stage(facts, "cloud115")
        self.assertEqual(
            (cloud["state"], cloud["evidence"], cloud["reasonCode"]),
            ("succeeded", "verified", "CLOUD115_FILE_ARRIVED"),
        )
        self.assertIn("上传方式未确认", cloud["reasonText"])
        self.assertEqual(cloud["eventAt"], "2026-07-27T03:30:00Z")

        local_source = build_pipeline_source_facts(context(symediaRows=[{
            "id": "symedia-local-1",
            "status": True,
            "date": "2026-07-27 11:30:00",
            "src": "/downloads/Show.S01E03.mkv",
            "dest": "/library/Show.S01E03.mkv",
        }]), observed_at=OBSERVED_AT)
        self.assertEqual(by_stage(local_source, "cloud115")["state"], "unknown")

    def test_cloud115_explicit_failure_wins_unless_a_later_arrival_is_verified(self):
        from app.secupload_result_runtime import secupload_file_path_key

        failure = {
            "fileKey": "failed-file",
            "batchKey": "failed-batch",
            "pathKey": secupload_file_path_key("/downloads/Show.S01E03.mkv"),
            "displayName": "Show.S01E03.mkv",
            "errorCategory": "retry_failed",
            "errorLabel": "重试后仍失败",
        }
        values = {
            "qbTasks": [{"name": "Show.S01E03.mkv", "savePath": "/downloads"}],
            "cloud115": {"readable": True, "failureFiles": [failure]},
            "symediaRows": [{
                "id": "symedia-arrival-1",
                "status": True,
                "date": "2026-07-27 11:30:00",
                "src": "/CloudNAS/CloudDrive/115/00-待整理/Show.S01E03.mkv",
            }],
        }
        missing_failure_time = build_pipeline_source_facts(
            context(**values), observed_at=OBSERVED_AT,
        )
        failure["observedAt"] = "2026-07-27T03:30:00Z"
        simultaneous_arrival = build_pipeline_source_facts(
            context(**values), observed_at=OBSERVED_AT,
        )
        failure["observedAt"] = "2026-07-27T03:00:00Z"
        later_arrival = build_pipeline_source_facts(
            context(**values), observed_at=OBSERVED_AT,
        )

        self.assertEqual(by_stage(missing_failure_time, "cloud115")["state"], "failed")
        self.assertEqual(by_stage(simultaneous_arrival, "cloud115")["state"], "failed")
        self.assertEqual(by_stage(later_arrival, "cloud115")["state"], "succeeded")

        values["symediaRows"].append({
            "id": "symedia-arrival-2",
            "status": True,
            "date": "2026-07-27 11:45:00",
            "src": "/CloudNAS/CloudDrive/115/00-待整理/Show.S01E03.alt.mkv",
        })
        ambiguous_arrivals = build_pipeline_source_facts(
            context(**values), observed_at=OBSERVED_AT,
        )
        self.assertEqual(by_stage(ambiguous_arrivals, "cloud115")["state"], "failed")

    def test_emby_strm_path_confirms_only_the_exact_target(self):
        exact = build_pipeline_source_facts(context(embyIndex={
            "movies": set(),
            "series": {"100"},
            "episodes": {("100", 1, 3)},
            "strmMovies": set(),
            "strmEpisodes": {("100", 1, 3)},
        }), observed_at=OBSERVED_AT)
        ordinary_media = build_pipeline_source_facts(context(embyIndex={
            "movies": set(),
            "series": {"100"},
            "episodes": {("100", 1, 3)},
            "strmMovies": set(),
            "strmEpisodes": set(),
        }), observed_at=OBSERVED_AT)

        strm = by_stage(exact, "strm")
        self.assertEqual(
            (strm["state"], strm["evidence"], strm["reasonCode"]),
            ("succeeded", "verified", "STRM_INDEXED_BY_EMBY"),
        )
        self.assertEqual(strm["reasonText"], "Emby 已索引目标 STRM 播放入口")
        self.assertEqual(by_stage(ordinary_media, "strm")["state"], "unknown")

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
        self.assertEqual(
            by_stage(protected, "symedia")["reasonCode"],
            "QUALITY_HIGHER_VERSION_EXISTS",
        )
        self.assertEqual(by_stage(failed, "symedia")["state"], "failed")
        self.assertEqual(by_stage(failed, "symedia")["reasonText"], "media lookup failed")

    def test_symedia_version_rule_rejection_is_protected_without_hiding_real_failures(self):
        protected = build_pipeline_source_facts(context(symediaRows=[{
            "id": "version-rule-protected",
            "status": False,
            "errmsg": "源文件 Test.S01E07.mkv 未命中任何允许入库的版本规则，取消入库",
        }]), observed_at=OBSERVED_AT)
        failed = build_pipeline_source_facts(context(symediaRows=[{
            "id": "recognition-failed",
            "status": False,
            "errmsg": "Symedia 未查询到对应媒体信息",
        }]), observed_at=OBSERVED_AT)

        protected_fact = by_stage(protected, "symedia")
        failed_fact = by_stage(failed, "symedia")
        protected_outcome = derive_pipeline_outcome(
            protected,
            target_scope="episode",
            now=datetime(2026, 7, 27, 4, 1, tzinfo=timezone.utc),
        )
        failed_outcome = derive_pipeline_outcome(
            failed,
            target_scope="episode",
            now=datetime(2026, 7, 27, 4, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(protected_fact["state"], "protected")
        self.assertEqual(
            protected_fact["units"][0]["reasonCode"],
            "QUALITY_VERSION_RULE_NOT_MATCHED",
        )
        self.assertEqual(
            protected_fact["reasonText"],
            "源文件 Test.S01E07.mkv 未命中任何允许入库的版本规则，取消入库",
        )
        self.assertEqual(protected_outcome["state"], "protected")
        self.assertEqual(failed_fact["state"], "failed")
        self.assertEqual(failed_outcome["state"], "action_required")

    def test_symedia_mixed_protection_and_real_failure_requires_action(self):
        facts = build_pipeline_source_facts(context(symediaRows=[
            {
                "id": "protected",
                "status": False,
                "reasonCode": "QUALITY_HIGHER_VERSION_EXISTS",
                "errmsg": "已有更高质量版本",
            },
            {
                "id": "unidentified",
                "status": False,
                "reasonCode": "DUPLICATE_RESOURCE_SKIPPED",
                "errmsg": "Symedia 未查询到对应媒体信息",
            },
        ]), observed_at=OBSERVED_AT)

        symedia = by_stage(facts, "symedia")

        self.assertEqual(symedia["state"], "failed")
        self.assertEqual([unit["state"] for unit in symedia["units"]], ["protected", "failed"])
        self.assertIn("未查询到对应媒体信息", symedia["reasonText"])

    def test_symedia_multiple_failures_group_real_reasons(self):
        facts = build_pipeline_source_facts(context(symediaRows=[
            {"id": "failed-1", "status": False, "date": "2026-07-27 11:30:00", "errmsg": "媒体识别失败"},
            {"id": "failed-2", "status": False, "date": "2026-07-27 11:31:00", "errmsg": "媒体识别失败"},
            {"id": "failed-3", "status": False, "date": "2026-07-27 11:32:00", "errmsg": "目标目录只读"},
        ]), observed_at=OBSERVED_AT)

        symedia = by_stage(facts, "symedia")

        self.assertEqual(symedia["state"], "failed")
        self.assertEqual(
            symedia["reasonText"],
            "媒体识别失败（2 个文件）；目标目录只读（1 个文件）",
        )
        self.assertEqual(
            [unit["reasonText"] for unit in symedia["units"]],
            ["媒体识别失败", "媒体识别失败", "目标目录只读"],
        )

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
