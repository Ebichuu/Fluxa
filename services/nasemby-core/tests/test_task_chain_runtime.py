from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


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
        self.assertEqual([step["status"] for step in item["steps"]], ["done"] * 4)
        self.assertEqual(
            [(row["seasonNumber"], row["episodeStart"], row["stage"]) for row in item["episodeEvidence"]],
            [(1, 1, "download"), (1, 1, "download"), (1, 1, "library")],
        )

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


if __name__ == "__main__":
    unittest.main()
