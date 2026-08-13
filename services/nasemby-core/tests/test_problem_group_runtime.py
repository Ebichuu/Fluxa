import unittest

from app.problem_group_runtime import derive_problem_groups


def problem_item(
    chain_id,
    *,
    title="测试剧",
    media_type="tv",
    tmdb_id="123",
    season=1,
    episode=1,
    identity_state="linked",
    stage="symedia",
    reason_code="SYMEDIA_LIBRARY_FAILED",
):
    return {
        "id": chain_id,
        "chainId": chain_id,
        "targetKey": f"target:{chain_id}",
        "title": title,
        "mediaType": media_type,
        "tmdbId": tmdb_id,
        "seasonNumber": season,
        "episodeNumber": episode,
        "identityState": identity_state,
        "outcomeState": "action_required",
        "pipelineOutcome": {
            "state": "action_required",
            "stage": stage,
            "reasonCode": reason_code,
            "reasonText": "当前任务需要处理",
        },
    }


class ProblemGroupRuntimeTests(unittest.TestCase):
    def test_reliable_identity_groups_by_season_stage_and_reason(self):
        items = [problem_item(f"s1e{episode}", episode=episode) for episode in range(1, 11)]
        items.extend([
            problem_item("other-reason", episode=11, reason_code="SYMEDIA_PATH_UNAVAILABLE"),
            problem_item("other-season", season=2, episode=1),
        ])

        projection = derive_problem_groups(items)

        self.assertEqual(projection["summary"], {
            "actionRequiredGroups": 3,
            "actionRequiredResources": 12,
            "actionRequiredIdentityUnconfirmedResources": 0,
        })
        largest = max(projection["groups"], key=lambda group: group["resourceCount"])
        self.assertEqual(largest["resourceCount"], 10)
        self.assertEqual(largest["episodeNumbers"], list(range(1, 11)))

    def test_unlinked_titles_use_only_mechanical_grouping(self):
        items = [
            problem_item("unknown-1", tmdb_id="", episode=1, title="Show A", identity_state="unidentified"),
            problem_item("unknown-2", tmdb_id="", episode=2, title="ＳＨＯＷ　Ａ！", identity_state="unidentified"),
            problem_item("alias", tmdb_id="", episode=3, title="Show A Extra", identity_state="unidentified"),
            problem_item("conflict", episode=4, title="Show A", identity_state="conflict"),
            problem_item("untitled", tmdb_id="", episode=5, title="", identity_state="unidentified"),
        ]

        projection = derive_problem_groups(items)

        self.assertEqual(projection["summary"], {
            "actionRequiredGroups": 4,
            "actionRequiredResources": 5,
            "actionRequiredIdentityUnconfirmedResources": 5,
        })
        grouped = next(group for group in projection["groups"] if group["resourceCount"] == 2)
        self.assertEqual(grouped["episodeNumbers"], [1, 2])

    def test_missing_stage_or_reason_stays_per_resource(self):
        first = problem_item("missing-stage", stage="")
        second = problem_item("missing-reason", reason_code="")

        projection = derive_problem_groups([first, second])

        self.assertEqual(projection["summary"]["actionRequiredGroups"], 2)
        self.assertTrue(all(group["resourceCount"] == 1 for group in projection["groups"]))

    def test_non_actionable_items_do_not_enter_problem_groups(self):
        actionable = problem_item("actionable")
        waiting = problem_item("waiting")
        waiting["outcomeState"] = "waiting"
        waiting["pipelineOutcome"]["state"] = "waiting"

        projection = derive_problem_groups([actionable, waiting])

        self.assertEqual(projection["summary"]["actionRequiredResources"], 1)

    def test_pipeline_outcome_reason_overrides_stale_top_level_qb_text(self):
        item = problem_item("symedia-failure")
        item["reasonCode"] = "QB_STALLED"
        item["reasonText"] = "qB 下载卡住"
        item["userReasonText"] = "qB 下载需要检查"
        item["pipelineOutcome"] = {
            "state": "action_required",
            "stage": "symedia",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "reasonText": "Symedia 作品识别失败",
        }

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["stage"], "symedia")
        self.assertEqual(group["reasonCode"], "SYMEDIA_LIBRARY_FAILED")
        self.assertEqual(group["reasonText"], "Symedia 作品识别失败")

    def test_episode_falls_back_to_exact_target_key(self):
        item = problem_item("target-key-fallback", season=1, episode=None)
        item["targetKey"] = "tv:tmdb:123:season:1:episode:43"

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [43])
        self.assertEqual(group["members"][0]["episodeNumber"], 43)

    def test_episode_falls_back_to_single_same_season_pipeline_unit(self):
        item = problem_item("fact-fallback", season=1, episode=None)
        item["targetKey"] = "tv:tmdb:123:season:1"
        item["pipelineFacts"] = [{
            "stage": "emby",
            "scope": "season",
            "unitKey": "tv:123:season:1",
            "units": [{
                "scope": "episode",
                "unitKey": "tv:123:s1:e6",
            }],
        }]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [6])
        self.assertEqual(group["members"][0]["episodeNumber"], 6)

    def test_pipeline_fact_fallback_prefers_the_explicit_target_unit(self):
        item = problem_item("target-unit-fallback", season=1, episode=None)
        item["targetKey"] = "tv:tmdb:123:season:1"
        item["targetUnitKey"] = "episode:6"
        item["pipelineFacts"] = [{
            "stage": "emby",
            "scope": "season",
            "units": [
                {"scope": "episode", "unitKey": "episode:6"},
                {"scope": "episode", "unitKey": "episode:7"},
            ],
        }]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [6])
        self.assertEqual(group["members"][0]["episodeNumber"], 6)

    def test_episode_falls_back_to_exact_owned_episode_evidence(self):
        item = problem_item("evidence-fallback", season=1, episode=None)
        item["targetKey"] = "tv:tmdb:123:season:1"
        item["episodeEvidence"] = [{
            "seasonNumber": 1,
            "episodeStart": 6,
            "episodeEnd": 6,
            "ownerScope": "episode",
            "ownerTargetKey": "tv:tmdb:123:season:1:episode:6",
        }]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [6])
        self.assertEqual(group["members"][0]["episodeNumber"], 6)

    def test_episode_falls_back_to_current_failed_file_evidence(self):
        item = problem_item("unowned-current-file", season=1, episode=None)
        item["targetKey"] = "tv:title:bleach:season:1"
        item["pipelineFacts"] = [{
            "stage": "symedia",
            "state": "failed",
            "scope": "file",
            "evidence": "verified",
            "sourceRef": "62851",
            "resultRef": "62851",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
        }]
        item["episodeEvidence"] = [{
            "seasonNumber": 1,
            "episodeStart": 43,
            "episodeEnd": 43,
            "stage": "library",
            "status": "blocked",
            "artifactKey": "artifact:symedia:62851",
            "ownerScope": "unlinked",
            "ownerTargetKey": "",
        }]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [43])
        self.assertEqual(group["members"][0]["episodeNumber"], 43)

    def test_current_failed_file_selects_latest_episode_from_history(self):
        item = problem_item("unowned-history", season=1, episode=None)
        item["targetKey"] = "tv:title:my-show:season:1"
        item["pipelineFacts"] = [{
            "stage": "symedia",
            "state": "failed",
            "scope": "file",
            "evidence": "verified",
            "sourceRef": "62933",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "units": [{
                "unitKey": "62933",
                "state": "failed",
                "scope": "file",
                "evidence": "verified",
                "sourceRef": "62933",
                "resultRef": "62933",
            }],
        }]
        item["episodeEvidence"] = [
            {
                "seasonNumber": 1,
                "episodeStart": episode,
                "episodeEnd": episode,
                "stage": "library",
                "status": "blocked",
                "artifactKey": f"artifact:symedia:{artifact}",
                "ownerScope": "unlinked",
                "ownerTargetKey": "",
            }
            for episode, artifact in ((4, "62724"), (5, "62801"), (6, "62933"))
        ]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [6])
        self.assertEqual(group["members"][0]["episodeNumber"], 6)

    def test_multiple_current_failed_files_do_not_guess_an_episode(self):
        item = problem_item("ambiguous-current-files", season=1, episode=None)
        item["targetKey"] = "tv:title:my-show:season:1"
        item["pipelineFacts"] = [{
            "stage": "symedia",
            "state": "failed",
            "scope": "file",
            "evidence": "verified",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "units": [
                {
                    "unitKey": artifact,
                    "state": "failed",
                    "scope": "file",
                    "evidence": "verified",
                    "sourceRef": artifact,
                }
                for artifact in ("62933", "62934")
            ],
        }]
        item["episodeEvidence"] = [
            {
                "seasonNumber": 1,
                "episodeStart": episode,
                "episodeEnd": episode,
                "stage": "library",
                "status": "blocked",
                "artifactKey": f"artifact:symedia:{artifact}",
                "ownerScope": "unlinked",
                "ownerTargetKey": "",
            }
            for episode, artifact in ((6, "62933"), (7, "62934"))
        ]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [])
        self.assertEqual(group["members"][0]["episodeNumber"], 0)

    def test_season_wide_or_ambiguous_facts_do_not_guess_an_episode(self):
        item = problem_item("broad-facts", season=1, episode=None)
        item["targetKey"] = "tv:tmdb:123:season:1"
        item["pipelineFacts"] = [{
            "stage": "emby",
            "scope": "season",
            "unitKey": "tv:123:season:1",
            "units": [
                {"scope": "episode", "unitKey": "tv:123:s1:e6"},
                {"scope": "episode", "unitKey": "tv:123:s1:e7"},
                {"scope": "episode", "unitKey": "tv:123:s2:e8"},
            ],
        }]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [])
        self.assertEqual(group["members"][0]["episodeNumber"], 0)

    def test_range_or_unowned_episode_evidence_does_not_guess_an_episode(self):
        item = problem_item("broad-evidence", season=1, episode=None)
        item["targetKey"] = "tv:tmdb:123:season:1"
        item["episodeEvidence"] = [
            {
                "seasonNumber": 1,
                "episodeStart": 6,
                "episodeEnd": 7,
                "ownerScope": "season",
                "ownerTargetKey": "tv:tmdb:123:season:1",
            },
            {
                "seasonNumber": 1,
                "episodeStart": 8,
                "episodeEnd": 8,
                "ownerScope": "season",
                "ownerTargetKey": "tv:tmdb:123:season:1",
            },
            {
                "seasonNumber": 1,
                "episodeStart": 9,
                "episodeEnd": 9,
                "ownerScope": "episode",
                "ownerTargetKey": "tv:tmdb:123:season:2:episode:9",
            },
        ]

        group = derive_problem_groups([item])["groups"][0]

        self.assertEqual(group["episodeNumbers"], [])
        self.assertEqual(group["members"][0]["episodeNumber"], 0)


if __name__ == "__main__":
    unittest.main()
