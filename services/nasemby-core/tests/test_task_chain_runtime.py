from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


def qb_summary(tasks):
    return {
        "configured": True,
        "connected": True,
        "webUrl": "http://qb.example.test",
        "lastCheckedAt": "",
        "version": "test",
        "transfer": {"downloadSpeed": 0, "uploadSpeed": 0},
        "counts": {
            "total": len(tasks),
            "active": 0,
            "downloading": 0,
            "stalled": 0,
            "completed": len(tasks),
            "paused": 0,
        },
        "tasks": tasks,
    }


def qb_task(**overrides):
    task = {
        "hash": "hash-1",
        "name": "测试剧.Test.Show.S01E01.1080p.mkv",
        "progress": 1,
        "state": "uploading",
        "stateLabel": "做种中",
        "status": "completed",
        "dlspeed": 0,
        "upspeed": 0,
        "eta": 0,
        "size": 100,
        "downloaded": 100,
        "savePath": "",
        "category": "",
        "tags": "",
        "addedOn": 1_784_000_000,
        "completionOn": 1_784_000_600,
    }
    task.update(overrides)
    return task


class TaskChainRuntimeContractTests(unittest.TestCase):
    def test_legacy_state_is_projected_from_pipeline_facts_only(self):
        from app.pipeline_source_fact_runtime import build_pipeline_source_facts
        from app.task_chain_runtime import _orphan_qb_item, build_task_chain

        now = datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc)
        unknown_facts = build_pipeline_source_facts({
            "mediaType": "tv",
            "tmdbId": "808",
            "seasonNumber": 1,
            "episodeNumber": 1,
            "torra": None,
            "qbTasks": [],
            "cloud115": {},
            "symediaRows": [],
            "embyIndex": None,
        }, observed_at="2026-07-27T04:00:00Z")
        input_data = {
            "subscriptions": [],
            "torraRows": [],
            "qb": qb_summary([]),
            "symediaRows": [{
                "id": "symedia-source-success",
                "title": "单向投影测试",
                "type": "tv",
                "tmdbid": "808",
                "season": 1,
                "episode": 1,
                "src": "/115/Test.Show.S01E01.mkv",
                "dest": "/media/Test.Show.S01E01.mkv",
                "status": True,
                "date": "2026-07-27 03:50:00",
            }],
            "symediaTotal": 1,
            "embyIndex": None,
            "urls": {"qb": "", "torra": "", "symedia": "", "emby": ""},
            "now": now,
        }

        with patch("app.task_chain_runtime.build_pipeline_source_facts", return_value=unknown_facts):
            item = build_task_chain(input_data)["items"][0]
            orphan = _orphan_qb_item(
                qb_task(status="downloading", state="downloading", progress=0.5),
                {"confidence": "unlinked"},
                input_data["urls"],
                now=now,
            )

        self.assertTrue(all(fact["state"] == "unknown" for fact in item["pipelineFacts"]))
        self.assertEqual(item["state"], "waiting")
        self.assertEqual(next(step for step in item["steps"] if step["key"] == "cloud115")["status"], "waiting")
        self.assertEqual(next(step for step in item["steps"] if step["key"] == "library")["status"], "waiting")
        self.assertEqual(orphan["activeDownloadTasks"], 0)
        self.assertEqual(orphan["state"], "waiting")

    def test_symedia_identity_merges_qb_and_library_without_subscription(self):
        from app.task_chain_runtime import build_task_chain
        from app.task_chain_v2_runtime import adapt_task_chain

        input_data = {
            "subscriptions": [],
            "torraRows": [],
            "torraUpload": {"connected": True, "readable": True, "perFileEvidence": False},
            "qb": qb_summary([qb_task(
                hash="hash-cn",
                name="[灿如繁星].Road.to.Success.S01E01.1080p.mkv",
            )]),
            "symediaRows": [{
                "id": "symedia-cn",
                "title": "灿如繁星",
                "type": "tv",
                "tmdbid": "808",
                "season": 1,
                "episode": 1,
                "src": "/115/灿如繁星.S01E01.mkv",
                "dest": "/strm/灿如繁星/S01E01.strm",
                "status": True,
                "date": "2026-07-25 01:00:00",
            }],
            "symediaTotal": 1,
            "embyIndex": {"movies": set(), "series": {"808"}},
            "urls": {"qb": "http://qb", "torra": "", "symedia": "http://symedia", "emby": "http://emby"},
            "now": datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc),
        }
        result = build_task_chain(input_data)

        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["title"], "灿如繁星")
        self.assertEqual(item["tmdbId"], "808")
        self.assertEqual(item["seasonNumber"], 1)
        self.assertEqual(item["sourceIds"]["subscriptionId"], "")
        self.assertEqual(item["sourceIds"]["torraId"], "")
        self.assertEqual(item["sourceIds"]["qbHashes"], ["hash-cn"])
        self.assertEqual(item["sourceIds"]["symediaIds"], ["symedia-cn"])
        self.assertTrue(item["embyIndexed"])
        self.assertEqual(item["embyEvidenceScope"], "title")
        self.assertIn("未发现 Fluxa/Torra 追更订阅", item["steps"][0]["detail"])
        self.assertIn("具体上传方式未确认", item["steps"][2]["detail"])
        self.assertEqual([step["status"] for step in item["steps"]], ["done"] * 4)

        adapted = adapt_task_chain(result, now=datetime(2026, 7, 25, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(len(adapted["items"]), 1)
        self.assertEqual(adapted["items"][0]["targetKey"], "tv:tmdb:808:season:1")
        self.assertEqual(adapted["items"][0]["identityState"], "linked")
        self.assertEqual(adapted["items"][0]["embyEvidenceScope"], "title")
        self.assertEqual(adapted["items"][0]["pipelineOutcome"]["state"], "evidence_insufficient")
        self.assertEqual(
            next(fact for fact in adapted["items"][0]["pipelineFacts"] if fact["stage"] == "symedia")["state"],
            "succeeded",
        )
        self.assertEqual(
            next(fact for fact in adapted["items"][0]["pipelineFacts"] if fact["stage"] == "strm")["state"],
            "unknown",
        )

        input_data["embyIndex"] = {"movies": set(), "series": set()}
        without_emby = adapt_task_chain(
            build_task_chain(input_data),
            now=datetime(2026, 7, 25, 2, 1, tzinfo=timezone.utc),
        )["items"][0]
        self.assertFalse(without_emby["embyIndexed"])
        self.assertEqual(without_emby["embyEvidenceScope"], "none")
        self.assertNotEqual(without_emby["healthState"], "action_required")

    def test_tmdb_file_and_symedia_evidence_form_strong_completed_chain(self):
        from app.task_chain_runtime import build_task_chain

        result = build_task_chain({
            "subscriptions": [{
                "id": "sub-1",
                "title": "测试剧",
                "mediaType": "tv",
                "tmdbId": "123",
                "posterUrl": "",
                "year": "2026",
                "seasonNumber": 1,
                "createdAt": "2026-07-13T00:00:00.000Z",
                "updatedAt": "2026-07-13T00:00:00.000Z",
            }],
            "torraRows": [{
                "id": "torra-1",
                "name": "测试剧",
                "media_type": "tv",
                "tmdb_id": 123,
                "season_number": 1,
                "downloaded_file_names": ["测试剧.Test.Show.S01E01.1080p.mkv"],
            }],
            "qb": qb_summary([qb_task()]),
            "symediaRows": [{
                "id": 1,
                "title": "测试剧",
                "type": "tv",
                "tmdbid": 123,
                "season": 1,
                "episode": 1,
                "src": "/115/测试剧.mkv",
                "dest": "/strm/测试剧.strm",
                "status": True,
                "date": "2026-07-14 00:00:00",
            }],
            "symediaTotal": 1,
            "symediaSummary": {
                "connected": True,
                "totals": {"processedToday": 31, "archivedToday": 24, "protectedToday": 7, "failedToday": 0},
            },
            "embyIndex": {"movies": set(), "series": {"123"}},
            "urls": {
                "qb": "http://qb.example.test",
                "torra": "http://torra.example.test",
                "symedia": "http://symedia.example.test",
                "emby": "http://emby.example.test",
            },
            "now": datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        })

        item = result["items"][0]
        self.assertEqual(item["confidence"], "strong")
        self.assertEqual(item["state"], "completed")
        self.assertTrue(item["embyIndexed"])
        self.assertEqual(item["embyEvidenceScope"], "title")
        self.assertIn("Emby 已收录该作品", item["steps"][-1]["detail"])
        self.assertEqual(result["services"]["symedia"]["totals"]["archivedToday"], 24)
        self.assertEqual([step["status"] for step in item["steps"]], ["done"] * 4)
        self.assertEqual(
            [(row["seasonNumber"], row["episodeStart"], row["stage"]) for row in item["episodeEvidence"]],
            [(1, 1, "download"), (1, 1, "download"), (1, 1, "library")],
        )

    def test_exact_emby_episode_is_the_only_tv_playable_fact(self):
        from app.task_chain_runtime import build_task_chain
        from app.task_chain_v2_runtime import adapt_task_chain

        result = build_task_chain({
            "subscriptions": [],
            "torraRows": [],
            "qb": qb_summary([]),
            "symediaRows": [{
                "id": "symedia-episode",
                "title": "集级测试",
                "type": "tv",
                "tmdbid": "321",
                "season": 1,
                "episode": 3,
                "src": "/115/Test.Show.S01E03.mkv",
                "dest": "/media/Test.Show.S01E03.mkv",
                "status": True,
                "date": "2026-07-27 03:50:00",
            }],
            "symediaTotal": 1,
            "embyIndex": {
                "movies": set(),
                "series": {"321"},
                "episodes": {("321", 1, 3)},
            },
            "urls": {"qb": "", "torra": "", "symedia": "http://symedia", "emby": "http://emby"},
            "now": datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc),
        })

        item = adapt_task_chain(
            result,
            now=datetime(2026, 7, 27, 4, 1, tzinfo=timezone.utc),
        )["items"][0]

        self.assertEqual(item["episodeNumber"], 3)
        self.assertEqual(item["pipelineOutcome"]["state"], "playable")
        self.assertEqual(item["pipelineOutcome"]["stage"], "emby")

    def test_completed_download_without_file_level_upload_evidence_stays_unknown(self):
        from app.task_chain_runtime import build_task_chain

        completed_at = int(datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp())
        task = qb_task(
            hash="hash-2",
            name="等待秒传.2026.mkv",
            addedOn=completed_at - 60,
            completionOn=completed_at,
        )
        result = build_task_chain({
            "subscriptions": [{
                "id": "sub-2",
                "title": "等待秒传",
                "mediaType": "movie",
                "tmdbId": "456",
                "posterUrl": "",
                "year": "2026",
                "createdAt": "2026-07-12T00:00:00.000Z",
                "updatedAt": "2026-07-12T00:00:00.000Z",
            }],
            "torraRows": [{
                "id": "torra-2",
                "name": "等待秒传",
                "media_type": "movie",
                "tmdb_id": 456,
                "downloaded_file_names": ["等待秒传.2026.mkv"],
            }],
            "torraUpload": {
                "connected": True,
                "readable": True,
                "perFileEvidence": False,
                "activeRuns": 0,
            },
            "qb": qb_summary([task]),
            "symediaRows": [],
            "symediaTotal": 0,
            "embyIndex": None,
            "urls": {"qb": "http://qb", "torra": "http://torra", "symedia": "", "emby": ""},
            "now": datetime(2026, 7, 14, tzinfo=timezone.utc),
        })

        cloud = next(step for step in result["items"][0]["steps"] if step["key"] == "cloud115")
        self.assertEqual(result["items"][0]["state"], "waiting")
        self.assertEqual(cloud["status"], "unknown")
        self.assertEqual(cloud["evidence"], "missing")
        self.assertEqual(cloud["reasonCode"], "TORRA_SECUPLOAD_FILE_EVIDENCE_UNAVAILABLE")
        self.assertIn("暂未提供逐文件证据", cloud["detail"])
        self.assertNotIn("推断", cloud["detail"])
        self.assertEqual(result["services"]["torra"]["secupload115"]["readable"], True)

    def test_qb_control_summary_matches_all_related_downloads(self):
        from app.task_chain_runtime import build_task_chain

        tasks = [
            qb_task(hash="hash-a", name="控制测试.mkv", progress=0.5, state="pausedDL", status="paused"),
            qb_task(hash="hash-b", name="控制测试.extra.mkv", progress=0.2, state="downloading", status="downloading"),
        ]
        summary = qb_summary(tasks)
        summary["counts"]["paused"] = 1
        summary["counts"]["completed"] = 0
        result = build_task_chain({
            "subscriptions": [{
                "id": "sub-qb",
                "title": "控制测试",
                "mediaType": "movie",
                "tmdbId": "789",
                "posterUrl": "",
                "year": "2026",
                "createdAt": "",
                "updatedAt": "",
            }],
            "torraRows": [{
                "id": "torra-qb",
                "name": "控制测试",
                "media_type": "movie",
                "tmdb_id": 789,
                "downloaded_file_names": ["控制测试.mkv"],
            }],
            "qb": summary,
            "symediaRows": [],
            "symediaTotal": 0,
            "embyIndex": None,
            "urls": {"qb": "http://qb", "torra": "http://torra", "symedia": "", "emby": ""},
        })

        self.assertEqual(result["items"][0]["qbControl"], {
            "total": 2,
            "paused": 1,
            "canPause": True,
            "canResume": False,
        })

    def test_raw_nasemby_items_map_without_creating_second_ledger(self):
        from app.task_chain_runtime import map_task_subscriptions

        rows = map_task_subscriptions({
            "items": [
                {"key": "movie:test:tmdb:10", "title": "测试电影", "media_type": "movie", "tmdb_id": 10},
                {"key": "tv:test:tmdb:20:season:1", "title": "测试剧", "media_type": "tv", "tmdb_id": 20, "target_season": 1},
            ]
        })

        self.assertEqual([row["id"] for row in rows], [
            "movie:test:tmdb:10",
            "tv:test:tmdb:20:season:1",
        ])
        self.assertEqual(rows[1]["seasonNumber"], 1)

    def test_torra_read_only_subscriptions_fill_empty_local_task_targets(self):
        from app.task_chain_runtime import merge_task_subscriptions

        subscriptions = merge_task_subscriptions([], [{
            "id": "torra-101",
            "name": "远端追更",
            "media_type": "tv",
            "tmdb_id": 101,
            "season_number": 2,
            "year": "2026",
        }])

        self.assertEqual(subscriptions, [{
            "id": "torra:torra-101",
            "title": "远端追更",
            "mediaType": "tv",
            "tmdbId": "101",
            "posterUrl": "",
            "year": "2026",
            "seasonNumber": 2,
            "createdAt": "",
            "updatedAt": "",
            "allowCloudFallback": False,
            "sourceLabel": "Torra 只读订阅",
        }])

    def test_torra_read_only_target_does_not_duplicate_same_local_tmdb_target(self):
        from app.task_chain_runtime import merge_task_subscriptions

        local = [{
            "id": "local-101",
            "title": "本地追更",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 2,
        }]
        result = merge_task_subscriptions(local, [{
            "id": "torra-101",
            "name": "远端追更",
            "media_type": "series",
            "tmdb_id": 101,
            "season_number": 2,
        }])

        self.assertEqual(result, local)

    def test_torra_aliases_merge_into_existing_local_target_without_duplicate(self):
        from app.task_chain_runtime import merge_task_subscriptions

        local = [{
            "id": "local-101",
            "title": "中文剧名",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 1,
        }]
        result = merge_task_subscriptions(local, [{
            "id": "torra-101",
            "name": "English Show",
            "names_json": '["中文剧名", "English Show"]',
            "media_type": "tv",
            "tmdb_id": 101,
            "season_number": 1,
        }])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["aliases"], ["English Show", "中文剧名"])

    def test_torra_zero_season_string_matches_any_requested_season(self):
        from app.task_chain_runtime import build_task_chain

        result = build_task_chain({
            "subscriptions": [{
                "id": "sub-season",
                "title": "季号测试",
                "mediaType": "tv",
                "tmdbId": "321",
                "posterUrl": "",
                "year": "2026",
                "seasonNumber": 2,
                "createdAt": "",
                "updatedAt": "",
            }],
            "torraRows": [{
                "id": "torra-season",
                "name": "季号测试",
                "media_type": "tv",
                "tmdb_id": 321,
                "season_number": "0",
            }],
            "qb": qb_summary([]),
            "symediaRows": [],
            "embyIndex": None,
            "urls": {"qb": "", "torra": "http://torra", "symedia": "", "emby": ""},
        })

        self.assertEqual(result["items"][0]["sourceIds"]["torraId"], "torra-season")

    def test_route_uses_injected_ledger_and_returns_fixed_error_on_ledger_failure(self):
        from flask import Flask

        from app.task_chain_runtime import register_task_chain

        class EmptyQb:
            base_url = ""

            @staticmethod
            def summary():
                return qb_summary([])

        class EmptyRows:
            base_url = ""

            @staticmethod
            def is_configured():
                return False

            @staticmethod
            def list_subscriptions():
                return []

            @staticmethod
            def list_transfer_history(_count):
                return {"rows": [], "total": 0}

        class EmptyEmby(EmptyRows):
            server_url = ""

            @staticmethod
            def get_tmdb_library_index():
                return None

        application = Flask(__name__)
        application.extensions.update({
            "mcc_qbittorrent_client": EmptyQb(),
            "mcc_torra_client": EmptyRows(),
            "mcc_symedia_client": EmptyRows(),
            "mcc_emby_client": EmptyEmby(),
        })
        register_task_chain(application, subscription_loader=lambda: [])
        response = application.test_client().get("/api/tasks/chain")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["counts"]["total"], 0)

        failed = Flask(f"{__name__}-failed")
        failed.extensions.update(application.extensions)
        register_task_chain(
            failed,
            subscription_loader=lambda: (_ for _ in ()).throw(RuntimeError("private ledger path")),
        )
        failed_response = failed.test_client().get("/api/tasks/chain")
        self.assertEqual(failed_response.status_code, 502)
        self.assertEqual(failed_response.get_json(), {
            "code": "TASK_CHAIN_READ_FAILED",
            "error": "任务链读取失败",
        })

    def test_v1_route_presents_only_opaque_external_references(self):
        from flask import Flask

        from app.task_chain_runtime import register_task_chain

        raw_qb = "a" * 40
        raw_torra = "torra-private-id"
        raw_symedia = "symedia-private:/storage/private/file.mkv"
        application = Flask(f"{__name__}-public")
        application.extensions.update({
            "mcc_qbittorrent_client": object(),
            "mcc_torra_client": object(),
            "mcc_symedia_client": object(),
            "mcc_emby_client": object(),
        })
        service = register_task_chain(application, subscription_loader=lambda: [])
        service.get_chain = lambda: {
            "generatedAt": "2026-07-26T03:00:00Z",
            "counts": {"total": 1},
            "services": {
                "qb": {"connected": True, "total": 1, "webUrl": "http://qb.private"},
                "torra": {"connected": True, "total": 1, "webUrl": "http://torra.private"},
            },
            "evidenceOwnership": {
                "summary": {"strong": 1},
                "records": [{"artifactKey": f"artifact:{raw_qb}", "source": "qBittorrent"}],
            },
            "items": [{
                "id": f"qb:{raw_qb}",
                "title": "测试任务",
                "mediaType": "tv",
                "tmdbId": "101",
                "seasonNumber": 1,
                "sourceIds": {
                    "subscriptionId": f"torra:{raw_torra}",
                    "torraId": raw_torra,
                    "qbHashes": [raw_qb],
                    "symediaIds": [raw_symedia],
                },
                "steps": [{
                    "key": "library",
                    "label": "入库",
                    "status": "blocked",
                    "detail": "/storage/private/file.mkv 处理失败 http://internal/job/1",
                    "source": "Symedia",
                }],
                "suggestion": {"label": "打开工具", "url": "http://qb.private"},
            }],
        }

        response = application.test_client().get("/api/tasks/chain")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        serialized = response.get_data(as_text=True)
        for private_value in (raw_qb, raw_torra, raw_symedia, "qb.private", "torra.private", "/storage/private"):
            self.assertNotIn(private_value, serialized)
        item = payload["items"][0]
        self.assertEqual(len(item["sourceIds"]["qbHashes"][0]), 40)
        self.assertTrue(item["sourceIds"]["torraId"].startswith("torra:"))
        self.assertTrue(item["sourceIds"]["symediaIds"][0].startswith("symedia:"))
        self.assertIsNone(item["suggestion"])
        self.assertEqual(payload["services"]["qb"]["webUrl"], "")


if __name__ == "__main__":
    unittest.main()
