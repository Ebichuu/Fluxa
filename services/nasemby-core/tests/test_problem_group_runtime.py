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


if __name__ == "__main__":
    unittest.main()
