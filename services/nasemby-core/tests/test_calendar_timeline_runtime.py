from __future__ import annotations

import unittest
import json
import threading
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import discover_runtime
from app.calendar_timeline_runtime import _entry_status, _merge_calendar_entries, register_calendar_timeline
from app.calendar_snapshot_repository import CalendarSnapshotRepository


def pipeline_fact(
    stage,
    state,
    *,
    scope="episode",
    observed_at="2026-07-22T01:00:00Z",
    fresh_until="2026-07-22T02:00:00Z",
    reason_code="",
):
    return {
        "stage": stage,
        "state": state,
        "scope": scope,
        "evidence": "missing" if state == "unknown" else "verified",
        "observedAt": observed_at,
        "freshUntil": fresh_until,
        "source": {
            "torra": "Torra",
            "qb": "qBittorrent",
            "symedia": "Symedia",
            "emby": "Emby",
        }.get(stage, ""),
        "sourceRef": f"{stage}:public",
        "reasonCode": reason_code or f"{stage.upper()}_{state.upper()}",
        "reasonText": "",
        "retryEligible": False,
    }


class FakeTaskService:
    def __init__(self, items=None):
        self.items = items

    def full_snapshot(self):
        items = self.items if self.items is not None else [{
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 2,
            "episodeNumber": 3,
            "chainId": "chain:101",
            "targetKey": "tv:tmdb:101:season:2:episode:3",
            "subscriptionId": "sub-1",
            "sourceIds": {"subscriptionIds": ["sub-1"]},
            "healthState": "waiting",
            "reasonCode": "STAGE_IN_PROGRESS",
            "reasonText": "正在下载",
            "observedAt": "2026-07-22T01:30:00Z",
            "freshUntil": "2026-07-22T01:35:00Z",
            "pipelineFacts": [
                pipeline_fact("torra", "waiting"),
                pipeline_fact("qb", "active"),
                pipeline_fact("cloud115", "unknown", scope="file"),
                pipeline_fact("symedia", "unknown", scope="file"),
                pipeline_fact("strm", "unknown"),
                pipeline_fact("emby", "unknown"),
            ],
            "pipelineOutcome": {
                "state": "in_progress",
                "stage": "qb",
                "reasonCode": "QB_ACTIVE",
                "reasonText": "qB 正在下载",
                "observedAt": "2026-07-22T01:00:00Z",
                "playableAt": "",
            },
            "episodeEvidence": [{
                "seasonNumber": 2,
                "episodeStart": 3,
                "episodeEnd": 3,
                "numberingScheme": "season_episode",
                "stage": "download",
                "artifactKey": "artifact:hash-1",
                "source": "qBittorrent",
                "observedAt": "2026-07-22T01:00:00Z",
                "matchMethod": "artifact_exact",
                "status": "active",
                "reasonCode": "",
                "reasonText": "",
            }],
            "stages": [
                {
                    "stage": "download", "status": "active", "evidence": "verified",
                    "observedAt": "2026-07-22T01:00:00Z", "source": "qBittorrent",
                },
                {
                    "stage": "library", "status": "waiting", "evidence": "missing",
                    "observedAt": "2026-07-22T01:30:00Z", "source": "",
                },
            ],
        }]
        return {
            "version": "tasks-v1",
            "items": items,
        }


class FakeReconciliationService:
    def __init__(self, rows, source_error=""):
        self.rows = rows
        self.source_error = source_error
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {
            "sourceError": self.source_error,
            "items": self.rows,
        }


class FakeRssRepository:
    def __init__(self, counts):
        self.counts = counts
        self.targets = []

    def resource_counts_for_targets(self, targets):
        self.targets = targets
        return {target["targetId"]: dict(self.counts) for target in targets}


class FakeRssService:
    def __init__(self, repository):
        self.repository = repository


def calendar_loader(year, month, media_type):
    return {
        "success": True,
        "year": year,
        "month": month,
        "type": media_type,
        "entries": [{
            "date": "2026-07-22",
            "key": "sub-1",
            "title": "测试剧",
            "media_type": "tv",
            "tmdb_id": "101",
            "season_number": 2,
            "episode_number": 3,
            "episode_label": "S02E03",
            "poster_url": "https://image.tmdb.org/t/p/w342/example.jpg",
            "in_library": False,
            "subscription_origin": "manual",
            "follow_scope_explicit": True,
        }],
        "stats": {"entries": 1, "titles": 1, "in_library": 0, "pending": 1},
        "errors": [],
    }


class CalendarTimelineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTaskService()
        register_calendar_timeline(
            self.app,
            calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )
        self.client = self.app.test_client()

    def test_calendar_combines_air_acquisition_and_task_identity(self):
        response = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        entry = payload["calendar"]["entries"][0]
        self.assertEqual(entry["airAt"], "2026-07-22T00:00:00+08:00")
        self.assertEqual(entry["acquiredAt"], "2026-07-22T01:00:00Z")
        self.assertEqual(entry["libraryAt"], "")
        self.assertEqual(entry["chainId"], "chain:101")
        self.assertEqual(entry["targetKey"], "tv:tmdb:101:season:2:episode:3")
        self.assertEqual(payload["calendar"]["timeZone"], "Asia/Shanghai")
        self.assertEqual(payload["calendar"]["stats"]["acquired"], 1)
        self.assertEqual(payload["calendar"]["statisticsMeta"]["playable"], {
            "scope": "calendar_query",
            "unit": "episode_event",
            "observedAt": "2026-07-22T01:31:00Z",
            "confirmation": "confirmed",
        })

    def test_calendar_without_task_service_marks_playable_statistic_unknown(self):
        application = Flask(f"{__name__}-no-task-service")
        register_calendar_timeline(
            application,
            calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )

        calendar = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]

        self.assertEqual(calendar["stats"]["playable"], 0)
        self.assertEqual(calendar["statisticsMeta"]["playable"]["confirmation"], "unknown")
        self.assertEqual(calendar["statisticsMeta"]["entries"]["confirmation"], "confirmed")

    def test_calendar_exposes_rss_candidates_without_faking_a_task_chain(self):
        application = Flask(f"{__name__}-rss-candidates")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService(items=[])
        rss_repository = FakeRssRepository({
            "total": 4,
            "exactEpisode": 1,
            "multiEpisode": 0,
            "seasonPack": 3,
            "scopePending": 0,
        })
        application.extensions["mcc_private_rss"] = FakeRssService(rss_repository)
        register_calendar_timeline(
            application,
            calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["rssResourceCount"], 4)
        self.assertEqual(entry["rssExactEpisodeCount"], 1)
        self.assertEqual(entry["rssSeasonPackCount"], 3)
        self.assertEqual(entry["chainId"], "")
        self.assertEqual(entry["reasonCode"], "RSS_CANDIDATES_AVAILABLE")
        self.assertIn("4 个 RSS 候选", entry["reasonText"])
        self.assertEqual(rss_repository.targets[0]["tmdbId"], "101")

    def test_calendar_validates_query_and_supports_etag(self):
        invalid = self.client.get("/api/v2/calendar?month=13")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "CALENDAR_RANGE_INVALID")
        first = self.client.get("/api/v2/calendar?year=2026&month=7")
        unchanged = self.client.get(
            "/api/v2/calendar?year=2026&month=7",
            headers={"If-None-Match": first.headers["ETag"]},
        )
        self.assertEqual(unchanged.status_code, 304)

    def test_persistent_calendar_get_is_read_only_and_cold_scope_is_unknown(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repository = CalendarSnapshotRepository(Path(directory.name) / "calendar.sqlite3")
        application = Flask(f"{__name__}-cached-cold")
        service = register_calendar_timeline(
            application,
            calendar_loader=lambda *_args: self.fail("GET must not call calendar loader"),
            repository=repository,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )
        service.snapshot = lambda *_args, **_kwargs: self.fail("GET must not build snapshot")

        response = application.test_client().get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["confirmation"], "unknown")
        self.assertEqual(payload["cache"]["status"], "cold")
        self.assertIsNone(payload["calendar"]["stats"]["entries"])
        self.assertIsNone(repository.queue_state(2026, 7, "tv", False))

    def test_refresh_request_only_queues_and_is_idempotent(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repository = CalendarSnapshotRepository(Path(directory.name) / "calendar.sqlite3")
        application = Flask(f"{__name__}-cached-refresh")
        register_calendar_timeline(application, calendar_loader=calendar_loader, repository=repository)
        client = application.test_client()
        body = {
            "year": 2026, "month": 7, "mediaType": "tv",
            "includeUnlinked": False, "idempotencyKey": "browser:2026-07:tv:0",
        }

        first = client.post("/api/v2/calendar/refresh-requests", json=body)
        second = client.post("/api/v2/calendar/refresh-requests", json=body)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.get_json()["refresh"]["scopeKey"], "2026-07:tv:0")
        self.assertEqual(repository.queue_state(2026, 7, "tv", False)["attemptCount"], 0)

    def test_season_level_stage_does_not_mark_episode_acquired_or_library(self):
        season_only = FakeTaskService().full_snapshot()["items"][0]
        season_only["episodeNumber"] = 0
        season_only["targetKey"] = "tv:tmdb:101:season:2"
        season_only["pipelineFacts"] = [pipeline_fact(
            "symedia", "succeeded", scope="file", observed_at="2026-07-22T02:00:00Z",
        )]
        season_only["pipelineOutcome"] = {
            "state": "evidence_insufficient",
            "stage": "",
            "reasonCode": "EVIDENCE_INSUFFICIENT",
            "reasonText": "缺少当前目标的明确可播放证据",
            "observedAt": "",
            "playableAt": "",
        }
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([season_only])

        entry = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["acquiredAt"], "")
        self.assertEqual(entry["libraryAt"], "")
        self.assertEqual(entry["healthState"], "evidence_insufficient")
        self.assertEqual(entry["reasonCode"], "CALENDAR_TASK_NOT_FOUND")

    def test_library_evidence_sets_in_library_and_removes_later_acquisition_time(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["pipelineFacts"] = [
            pipeline_fact(
                "qb", "succeeded", observed_at="2026-07-22T10:17:00Z",
                fresh_until="2026-07-22T11:00:00Z",
            ),
            pipeline_fact(
                "symedia", "succeeded", scope="file", observed_at="2026-07-22T08:01:00Z",
                fresh_until="2026-07-22T11:00:00Z",
            ),
        ]
        self.app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])

        calendar = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]
        entry = calendar["entries"][0]

        self.assertEqual(entry["libraryAt"], "2026-07-22T08:01:00Z")
        self.assertEqual(entry["acquiredAt"], "")
        self.assertTrue(entry["inLibrary"])
        self.assertEqual(entry["status"], "library")
        self.assertEqual(calendar["stats"]["inLibrary"], 1)
        self.assertEqual(calendar["stats"]["pending"], 0)

    def test_expired_episode_evidence_does_not_mark_episode_acquired_or_in_library(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["pipelineFacts"] = [pipeline_fact(
            "symedia", "succeeded", scope="file",
            observed_at="2026-07-22T01:30:00Z", fresh_until="2026-07-22T01:35:00Z",
        )]
        application = Flask(__name__)
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(
            application,
            calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["libraryAt"], "")
        self.assertEqual(entry["acquiredAt"], "")
        self.assertFalse(entry["inLibrary"])
        self.assertEqual(entry["status"], "unknown")

    def test_range_owner_projects_historical_library_time_after_freshness_expires(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["episodeNumber"] = 0
        item["targetKey"] = "tv:tmdb:101:season:2"
        item["pipelineFacts"] = [pipeline_fact(
            "symedia", "succeeded", scope="file",
            observed_at="2026-07-22T01:30:00Z", fresh_until="2026-07-22T01:35:00Z",
        )]
        item["pipelineOutcome"] = {
            "state": "evidence_insufficient", "stage": "", "reasonCode": "EVIDENCE_INSUFFICIENT",
            "reasonText": "缺少当前目标的明确可播放证据", "observedAt": "", "playableAt": "",
        }
        item["episodeEvidence"] = [{
            "seasonNumber": 2, "episodeStart": 2, "episodeEnd": 3,
            "numberingScheme": "season_episode", "stage": "library",
            "artifactKey": "artifact:symedia-range", "source": "Symedia",
            "eventAt": "2026-07-22T01:20:00Z", "observedAt": "2026-07-22T01:30:00Z",
            "matchMethod": "artifact_exact", "status": "done", "reasonCode": "SYMEDIA_ORGANIZED",
            "reasonText": "Symedia 整理入库完成", "ownerScope": "episode_range",
            "ownerTargetKey": "tv:tmdb:101:season:2:episodes:2-3",
            "parentTargetKey": "tv:tmdb:101:season:2",
        }]
        application = Flask(f"{__name__}-range-history")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(
            application,
            calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["libraryAt"], "2026-07-22T01:20:00Z")
        self.assertTrue(entry["inLibrary"])

    def test_emby_episode_history_remains_when_current_confirmation_expires(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["episodeNumber"] = 0
        item["targetKey"] = "tv:tmdb:101:season:2"
        item["episodeEvidence"] = [{
            "seasonNumber": 2, "episodeStart": 3, "episodeEnd": 3,
            "numberingScheme": "season_episode", "stage": "library",
            "artifactKey": "artifact:symedia-3", "source": "Symedia",
            "eventAt": "2026-07-22T01:10:00Z", "observedAt": "2026-07-22T01:15:00Z",
            "matchMethod": "artifact_exact", "status": "done", "reasonCode": "SYMEDIA_ORGANIZED",
            "reasonText": "Symedia 整理入库完成", "ownerScope": "episode",
            "ownerTargetKey": "tv:tmdb:101:season:2:episode:3",
            "parentTargetKey": "tv:tmdb:101:season:2",
        }]
        item["pipelineFacts"] = [{
            **pipeline_fact(
                "emby", "unknown", scope="season", observed_at="2026-07-22T01:20:00Z",
                fresh_until="2026-07-22T02:00:00Z",
            ),
            "units": [{
                "unitKey": "tv:101:s2:e3", "state": "succeeded", "scope": "episode",
                "evidence": "verified", "eventAt": "2026-07-22T01:20:00Z",
                "observedAt": "2026-07-22T01:20:00Z", "freshUntil": "2026-07-22T02:00:00Z",
                "sourceRef": "tv:101:s2:e3", "reasonCode": "EMBY_EPISODE_INDEXED",
                "reasonText": "Emby 已收录目标集", "retryEligible": False,
            }],
        }]
        item["pipelineOutcome"] = {
            "state": "evidence_insufficient", "stage": "", "reasonCode": "EVIDENCE_INSUFFICIENT",
            "reasonText": "缺少当前目标的明确可播放证据", "observedAt": "", "playableAt": "",
        }

        current_app = Flask(f"{__name__}-emby-current")
        current_app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(
            current_app, calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )
        stale_app = Flask(f"{__name__}-emby-stale")
        stale_app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(
            stale_app, calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 23, 1, 31, tzinfo=timezone.utc),
        )

        current_entry = current_app.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]
        stale_entry = stale_app.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(current_entry["outcomeState"], "playable")
        self.assertEqual(stale_entry["outcomeState"], "evidence_insufficient")
        self.assertEqual(current_entry["playableAt"], "2026-07-22T01:20:00Z")
        self.assertEqual(stale_entry["playableAt"], "2026-07-22T01:20:00Z")
        self.assertEqual(stale_entry["firstConfirmedPlayableAt"], "2026-07-22T01:20:00Z")

    def test_historical_emby_event_does_not_replace_current_missing_snapshot(self):
        class HistoricalRepository:
            def list_episode_events(self, chain_id, limit=1000):
                return [{
                    "kind": "pipeline_fact_unit",
                    "stage": "emby",
                    "status": "succeeded",
                    "seasonNumber": 2,
                    "episodeStart": 3,
                    "episodeEnd": 3,
                    "eventAt": "2026-07-22T01:20:00Z",
                    "observedAt": "2026-07-22T01:20:00Z",
                    "freshUntil": "2026-07-22T02:00:00Z",
                    "source": "Emby",
                }]

        item = FakeTaskService().full_snapshot()["items"][0]
        item["pipelineFacts"] = [pipeline_fact(
            "emby", "unknown", observed_at="2026-07-22T01:30:00Z",
            fresh_until="2026-07-22T02:00:00Z",
        )]
        item["pipelineOutcome"] = {
            "state": "evidence_insufficient", "stage": "", "reasonCode": "EVIDENCE_INSUFFICIENT",
            "reasonText": "当前状态暂未确认", "observedAt": "2026-07-22T01:30:00Z", "playableAt": "",
        }
        task_service = FakeTaskService([item])
        task_service.repository = HistoricalRepository()
        application = Flask(f"{__name__}-emby-current-missing")
        application.extensions["mcc_task_chain_v2_service"] = task_service
        register_calendar_timeline(
            application, calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["outcomeState"], "evidence_insufficient")
        self.assertEqual(entry["playableAt"], "2026-07-22T01:20:00Z")

    def test_expired_failure_keeps_history_without_current_red_state(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["episodeNumber"] = 0
        item["targetKey"] = "tv:tmdb:101:season:2"
        item["pipelineFacts"] = []
        item["pipelineOutcome"] = {
            "state": "evidence_insufficient", "stage": "", "reasonCode": "EVIDENCE_INSUFFICIENT",
            "reasonText": "当前状态暂未确认", "observedAt": "", "playableAt": "",
        }
        item["episodeEvidence"] = [{
            "seasonNumber": 2, "episodeStart": 3, "episodeEnd": 3,
            "numberingScheme": "season_episode", "stage": "library",
            "artifactKey": "artifact:failed-3", "source": "Symedia",
            "eventAt": "2026-07-22T01:20:00Z", "observedAt": "2026-07-22T01:30:00Z",
            "matchMethod": "artifact_exact", "status": "blocked", "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "reasonText": "Symedia 整理失败", "ownerScope": "episode",
            "ownerTargetKey": "tv:tmdb:101:season:2:episode:3",
            "parentTargetKey": "tv:tmdb:101:season:2",
        }]
        application = Flask(f"{__name__}-failure-history")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(
            application, calendar_loader=calendar_loader,
            clock=lambda: datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["healthState"], "evidence_insufficient")
        self.assertEqual(entry["reasonCode"], "HISTORICAL_FAILURE_CURRENT_UNKNOWN")
        self.assertIn("曾于 2026-07-22T01:20:00Z 失败", entry["reasonText"])

    def test_historical_failure_recovery_requires_same_artifact_and_stage(self):
        item = FakeTaskService().full_snapshot()["items"][0]
        item["episodeNumber"] = 0
        item["targetKey"] = "tv:tmdb:101:season:2"
        item["pipelineFacts"] = []
        item["pipelineOutcome"] = {
            "state": "evidence_insufficient", "stage": "", "reasonCode": "EVIDENCE_INSUFFICIENT",
            "reasonText": "当前状态暂未确认", "observedAt": "", "playableAt": "",
        }
        failed = {
            "seasonNumber": 2, "episodeStart": 3, "episodeEnd": 3,
            "numberingScheme": "season_episode", "stage": "library",
            "artifactKey": "artifact:failed-3", "source": "Symedia",
            "eventAt": "2026-07-22T01:20:00Z", "observedAt": "2026-07-22T01:30:00Z",
            "matchMethod": "artifact_exact", "status": "blocked", "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "reasonText": "Symedia 整理失败", "ownerScope": "episode",
            "ownerTargetKey": "tv:tmdb:101:season:2:episode:3",
            "parentTargetKey": "tv:tmdb:101:season:2",
        }
        succeeded = {
            **failed,
            "artifactKey": "artifact:other-3",
            "eventAt": "2026-07-22T02:20:00Z",
            "observedAt": "2026-07-22T02:30:00Z",
            "status": "done",
            "reasonCode": "SYMEDIA_ORGANIZED",
            "reasonText": "Symedia 整理入库完成",
        }
        item["episodeEvidence"] = [failed, succeeded]
        application = Flask(f"{__name__}-failure-other-artifact")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(application, calendar_loader=calendar_loader)

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["reasonCode"], "HISTORICAL_FAILURE_CURRENT_UNKNOWN")

        item["episodeEvidence"] = [failed, {**succeeded, "artifactKey": failed["artifactKey"]}]
        recovered_app = Flask(f"{__name__}-failure-same-artifact")
        recovered_app.extensions["mcc_task_chain_v2_service"] = FakeTaskService([item])
        register_calendar_timeline(recovered_app, calendar_loader=calendar_loader)

        recovered_entry = recovered_app.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertNotEqual(recovered_entry["reasonCode"], "HISTORICAL_FAILURE_CURRENT_UNKNOWN")

    def test_legacy_in_library_flag_does_not_replace_exact_symedia_evidence(self):
        def legacy_library_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            payload["entries"][0]["in_library"] = True
            return payload

        application = Flask(f"{__name__}-legacy-library")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(
            application,
            calendar_loader=legacy_library_loader,
            clock=lambda: datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["libraryAt"], "")
        self.assertFalse(entry["inLibrary"])
        self.assertEqual(entry["status"], "unknown")

    def test_summary_and_date_detail_keep_legacy_request_compatible(self):
        legacy = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv")
        summary = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")
        detail = self.client.get("/api/v2/calendar?date=2026-07-22&type=tv&view=detail")

        self.assertTrue(legacy.get_json()["calendar"]["entries"])
        self.assertEqual(summary.get_json()["calendar"]["entries"], [])
        self.assertEqual(summary.get_json()["calendar"]["days"][0]["date"], "2026-07-22")
        self.assertEqual(summary.get_json()["calendar"]["days"][0]["statusCounts"]["acquiring"], 1)
        self.assertEqual(summary.get_json()["calendar"]["searchIndex"][0]["title"], "测试剧")
        self.assertEqual(
            summary.get_json()["calendar"]["days"][0]["preview"][0]["posterUrl"],
            "https://image.tmdb.org/t/p/w342/example.jpg",
        )
        self.assertEqual(len(detail.get_json()["calendar"]["entries"]), 1)
        self.assertEqual(
            detail.get_json()["calendar"]["entries"][0]["posterUrl"],
            "https://image.tmdb.org/t/p/w342/example.jpg",
        )
        self.assertEqual(detail.get_json()["calendar"]["view"], "detail")

    def test_summary_request_populates_complete_month_cache(self):
        service = self.app.extensions["mcc_calendar_timeline"]
        self.assertIsNone(service.cached_snapshot(2026, 7, "tv"))

        with patch("app.calendar_timeline_runtime.time.monotonic", return_value=100.0):
            response = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")
        with patch("app.calendar_timeline_runtime.time.monotonic", return_value=399.9):
            cached = service.cached_snapshot(2026, 7, "tv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["calendar"]["entries"], [])
        self.assertEqual(len(cached["calendar"]["entries"]), 1)
        cached["calendar"]["entries"].clear()
        with patch("app.calendar_timeline_runtime.time.monotonic", return_value=399.9):
            self.assertEqual(len(service.cached_snapshot(2026, 7, "tv")["calendar"]["entries"]), 1)
        with patch("app.calendar_timeline_runtime.time.monotonic", return_value=400.0):
            self.assertIsNone(service.cached_snapshot(2026, 7, "tv"))
        with patch("app.calendar_timeline_runtime.time.monotonic", return_value=401.0):
            self.assertIsNone(service.cached_snapshot(2026, 7, "tv"))

    def test_date_detail_projects_cached_month_without_reloading_sources_or_tasks(self):
        calls = {"calendar": 0, "tasks": 0}

        def counted_loader(year, month, media_type):
            calls["calendar"] += 1
            return calendar_loader(year, month, media_type)

        class CountingTaskService(FakeTaskService):
            def full_snapshot(self):
                calls["tasks"] += 1
                return super().full_snapshot()

        application = Flask(f"{__name__}-date-fast-path")
        application.extensions["mcc_task_chain_v2_service"] = CountingTaskService()
        register_calendar_timeline(
            application,
            calendar_loader=counted_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )
        client = application.test_client()

        summary = client.get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")
        detail = client.get("/api/v2/calendar?date=2026-07-22&type=tv&view=detail")

        self.assertEqual(summary.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(calls, {"calendar": 1, "tasks": 1})
        self.assertEqual(detail.get_json()["calendar"]["view"], "detail")
        self.assertEqual(detail.get_json()["calendar"]["stats"]["entries"], 1)

    def test_date_detail_cache_keeps_unlinked_scope_isolated_and_recounts_day(self):
        def unlinked_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            payload["entries"][0]["tmdb_id"] = ""
            payload["entries"][0]["key"] = "unlinked"
            payload["entries"][0]["follow_scope_explicit"] = False
            payload["entries"][0]["subscription_origin"] = ""
            return payload

        application = Flask(f"{__name__}-date-unlinked-cache")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=unlinked_loader)
        client = application.test_client()

        client.get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")
        hidden = client.get("/api/v2/calendar?date=2026-07-22&type=tv&view=detail").get_json()["calendar"]
        client.get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary&includeUnlinked=1")
        visible = client.get(
            "/api/v2/calendar?date=2026-07-22&type=tv&view=detail&includeUnlinked=1"
        ).get_json()["calendar"]

        self.assertEqual(hidden["entries"], [])
        self.assertEqual(hidden["stats"]["totalEntries"], 1)
        self.assertEqual(hidden["stats"]["unlinkedEntries"], 1)
        self.assertEqual(hidden["stats"]["excludedUnlinked"], 1)
        self.assertEqual(len(visible["entries"]), 1)
        self.assertEqual(visible["stats"]["totalEntries"], 1)
        self.assertEqual(visible["stats"]["excludedUnlinked"], 0)

    def test_concurrent_month_requests_share_one_snapshot_build(self):
        calls = 0
        calls_lock = threading.Lock()

        def slow_loader(year, month, media_type):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.05)
            return calendar_loader(year, month, media_type)

        application = Flask(f"{__name__}-calendar-single-flight")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=slow_loader)
        responses = []

        def load():
            with application.test_client() as client:
                responses.append(client.get(
                    "/api/v2/calendar?year=2026&month=7&type=tv&view=summary"
                ).status_code)

        workers = [threading.Thread(target=load) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)

        self.assertEqual(sorted(responses), [200, 200])
        self.assertEqual(calls, 1)

    def test_week_summary_populates_date_fast_path(self):
        calls = {"calendar": 0, "tasks": 0}

        def counted_loader(year, month, media_type):
            calls["calendar"] += 1
            return calendar_loader(year, month, media_type)

        class CountingTaskService(FakeTaskService):
            def full_snapshot(self):
                calls["tasks"] += 1
                return super().full_snapshot()

        application = Flask(f"{__name__}-week-date-fast-path")
        application.extensions["mcc_task_chain_v2_service"] = CountingTaskService()
        register_calendar_timeline(application, calendar_loader=counted_loader)
        client = application.test_client()

        week = client.get(
            "/api/v2/calendar?year=2026&month=7&type=tv&view=summary&from=2026-07-20&to=2026-07-26"
        )
        detail = client.get("/api/v2/calendar?date=2026-07-22&type=tv&view=detail")

        self.assertEqual(week.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(calls, {"calendar": 1, "tasks": 1})
        self.assertEqual(detail.get_json()["calendar"]["stats"]["entries"], 1)

    def test_calendar_reads_each_task_history_once_per_snapshot(self):
        class CountingRepository:
            def __init__(self):
                self.calls = 0

            def list_episode_events(self, chain_id, limit=1000):
                self.calls += 1
                return []

        def repeated_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            source = payload["entries"][0]
            payload["entries"] = [
                {**source, "episode_number": episode, "episode_label": f"S02E{episode:02d}"}
                for episode in range(1, 5)
            ]
            return payload

        task_service = FakeTaskService()
        task_service.repository = CountingRepository()
        application = Flask(f"{__name__}-history-index")
        application.extensions["mcc_task_chain_v2_service"] = task_service
        register_calendar_timeline(application, calendar_loader=repeated_loader)

        response = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(task_service.repository.calls, 1)

    def test_local_subscription_calendar_generation_is_bounded_concurrent_and_complete(self):
        items = [{"id": index, "title": f"测试剧 {index:02d}"} for index in range(12)]
        lock = threading.Lock()
        active = 0
        max_active = 0

        def slow_entries(item, year, month, media_type):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02 + (11 - item["id"]) * 0.001)
            with lock:
                active -= 1
            return ([{
                "date": "2026-07-22",
                "key": f"sub-{item['id']}",
                "title": item["title"],
                "media_type": "tv",
                "tmdb_id": str(1000 + item["id"]),
                "season_number": 1,
                "episode_number": 1,
                "in_library": False,
            }], "")

        with patch.object(discover_runtime, "load_subscription_items", return_value={"items": items}), patch.object(
            discover_runtime,
            "build_subscription_calendar_entries_for_item",
            side_effect=slow_entries,
        ):
            payload = discover_runtime.build_subscription_calendar(2026, 7, "tv")

        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 8)
        self.assertEqual(len(payload["entries"]), len(items))
        self.assertEqual(
            [row["title"] for row in payload["entries"]],
            sorted(item["title"] for item in items),
        )

    def test_past_episode_requires_explicit_follow_scope_before_marking_missing(self):
        base = {
            "date": "2001-01-01",
            "airAt": "2001-01-01T00:00:00+08:00",
            "subscriptionCreatedAt": "2000-12-01T00:00:00Z",
            "allowedDelayHours": 24,
        }

        self.assertEqual(_entry_status(base, "2026-07-23"), "unknown")
        self.assertEqual(_entry_status({**base, "followScopeExplicit": True}, "2026-07-23"), "missing")
        self.assertEqual(_entry_status({
            **base,
            "followScopeExplicit": True,
            "subscriptionCreatedAt": "2002-01-01T00:00:00Z",
        }, "2026-07-23"), "unknown")

    def test_calendar_hides_episode_aired_before_subscription_without_history_scope(self):
        def pre_subscription_loader(year, month, media_type):
            return {
                "success": True,
                "year": year,
                "month": month,
                "type": media_type,
                "entries": [{
                    "date": "2026-07-18",
                    "key": "sub-late",
                    "title": "晚订阅剧",
                    "media_type": "tv",
                    "tmdb_id": "404",
                    "season_number": 1,
                    "episode_number": 1,
                    "subscription_created_at": "2026-07-24T00:00:00Z",
                    "follow_scope_explicit": True,
                    "include_past_episodes": False,
                }],
                "stats": {"entries": 1, "titles": 1, "in_library": 0, "pending": 1},
                "errors": [],
            }

        application = Flask(f"{__name__}-pre-subscription")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=pre_subscription_loader)
        payload = application.test_client().get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]

        self.assertEqual(payload["entries"], [])
        self.assertEqual(payload["stats"]["excludedBeforeSubscription"], 1)
        self.assertEqual(payload["stats"]["entries"], 0)
        self.assertEqual(payload["stats"]["titles"], 0)
        self.assertEqual(payload["stats"]["statusCounts"]["unknown"], 0)

    def test_protection_evidence_is_not_counted_as_missing(self):
        entry = {
            "date": "2001-01-01",
            "followScopeExplicit": True,
            "healthState": "protected",
            "reasonCode": "QUALITY_LOWER_THAN_TARGET",
        }

        self.assertEqual(_entry_status(entry, "2026-07-23"), "protected")

    def test_month_summary_stays_below_size_target(self):
        def large_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            source = payload["entries"][0]
            payload["entries"] = [
                {
                    **source,
                    "key": f"sub-{index}",
                    "title": f"测试剧 {index}",
                    "episode_number": index + 1,
                    "episode_label": f"S02E{index + 1:03d}",
                }
                for index in range(892)
            ]
            return payload

        application = Flask(f"{__name__}-large")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=large_loader)
        response = application.test_client().get("/api/v2/calendar?year=2026&month=7&type=tv&view=summary")

        self.assertEqual(response.status_code, 200)
        self.assertLess(len(json.dumps(response.get_json(), ensure_ascii=False).encode("utf-8")), 200_000)
        self.assertEqual(response.get_json()["calendar"]["days"][0]["total"], 892)
        self.assertEqual(len(response.get_json()["calendar"]["searchIndex"]), 892)

    def test_month_search_index_contains_entries_beyond_three_item_preview(self):
        def four_item_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            source = payload["entries"][0]
            payload["entries"] = [
                {
                    **source,
                    "key": f"sub-{index}",
                    "title": f"测试剧 {index + 1}",
                    "episode_number": index + 1,
                    "episode_label": f"S02E{index + 1:02d}",
                }
                for index in range(4)
            ]
            return payload

        application = Flask(f"{__name__}-search-index")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=four_item_loader)
        calendar = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv&view=summary"
        ).get_json()["calendar"]

        self.assertEqual(len(calendar["days"][0]["preview"]), 3)
        self.assertTrue(any(item["title"] == "测试剧 4" for item in calendar["searchIndex"]))

    def test_calendar_includes_only_torra_rows_without_creating_local_subscription(self):
        reconciliation = FakeReconciliationService([{
            "id": "torra:remote-ref",
            "remoteRef": "remote-ref",
            "title": "远端追更",
            "mediaType": "tv",
            "tmdbId": "202",
            "seasonNumber": 1,
            "reconciliationState": "only_torra",
            "observedAt": "2026-07-23T00:00:00Z",
        }])
        self.app.extensions["mcc_subscription_reconciliation"] = reconciliation

        def remote_entries(item, year, month, media_type):
            self.assertTrue(item["read_only"])
            self.assertEqual(item["source_label"], "Torra 只读追更")
            self.assertEqual(item["tmdb_id"], "202")
            return ([{
                "date": "2026-07-23",
                "key": item["subscription_key"],
                "title": item["title"],
                "media_type": "tv",
                "tmdb_id": item["tmdb_id"],
                "source_label": item["source_label"],
                "season_number": 1,
                "episode_number": 1,
                "episode_label": "S01E01",
                "in_library": False,
                "subscription_created_at": item["subscribed_at"],
                "follow_scope_explicit": True,
                "include_past_episodes": False,
                "allowed_delay_hours": 24,
            }], "")

        with patch("app.calendar_timeline_runtime.discover_runtime.build_subscription_calendar_entries_for_item", remote_entries):
            payload = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()

        remote = next(entry for entry in payload["calendar"]["entries"] if entry["tmdbId"] == "202")
        self.assertEqual(remote["sourceLabel"], "Torra 只读追更")
        self.assertEqual(remote["subscriptionCreatedAt"], "2026-07-23T00:00:00Z")
        self.assertEqual(reconciliation.calls, 1)

    def test_calendar_merges_duplicate_episode_sources_before_all_stats(self):
        def duplicate_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            source = payload["entries"][0]
            payload["entries"] = [
                {
                    **source,
                    "key": "sub-auto",
                    "source_label": "Fluxa 自动追更",
                    "subscription_origin": "auto",
                    "subscription_created_at": "2026-07-10T00:00:00Z",
                },
                {
                    **source,
                    "key": "sub-manual",
                    "source_label": "Fluxa 手动追更",
                    "subscription_origin": "manual",
                    "subscription_created_at": "2026-07-12T00:00:00Z",
                },
            ]
            return payload

        reconciliation = FakeReconciliationService([{
            "id": "torra:duplicate-ref",
            "remoteRef": "duplicate-ref",
            "title": "测试剧",
            "mediaType": "tv",
            "tmdbId": "101",
            "seasonNumber": 2,
            "reconciliationState": "only_torra",
            "observedAt": "2026-07-20T00:00:00Z",
        }])
        application = Flask(f"{__name__}-calendar-dedup")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService()
        application.extensions["mcc_subscription_reconciliation"] = reconciliation
        register_calendar_timeline(
            application,
            calendar_loader=duplicate_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )

        def remote_entries(item, year, month, media_type):
            return ([{
                "date": "2026-07-22",
                "key": item["subscription_key"],
                "title": item["title"],
                "media_type": "tv",
                "tmdb_id": "101",
                "source_label": item["source_label"],
                "season_number": 2,
                "episode_number": 3,
                "episode_label": "S02E03",
                "subscription_origin": "torra",
                "torra_linked": True,
                "follow_scope_explicit": True,
                "subscription_created_at": item["subscribed_at"],
            }], "")

        with patch("app.calendar_timeline_runtime.discover_runtime.build_subscription_calendar_entries_for_item", remote_entries):
            client = application.test_client()
            calendar = client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()["calendar"]
            summary = client.get(
                "/api/v2/calendar?year=2026&month=7&type=tv&view=summary"
            ).get_json()["calendar"]

        self.assertEqual(len(calendar["entries"]), 1)
        entry = calendar["entries"][0]
        self.assertEqual(entry["key"], "sub-manual")
        self.assertEqual(entry["sourceCount"], 3)
        self.assertEqual(
            entry["sourceLabels"],
            ["Fluxa 手动追更", "Fluxa 自动追更", "Torra 只读追更"],
        )
        self.assertEqual(entry["subscriptionCreatedAt"], "2026-07-10T00:00:00Z")
        self.assertTrue(entry["torraLinked"])
        self.assertEqual(
            {key: calendar["stats"][key] for key in ("entries", "linkedEntries", "unlinkedEntries", "totalEntries")},
            {"entries": 1, "linkedEntries": 1, "unlinkedEntries": 0, "totalEntries": 1},
        )
        self.assertEqual(summary["days"][0]["total"], 1)
        self.assertEqual(len(summary["searchIndex"]), 1)

    def test_calendar_does_not_merge_entries_without_reliable_tmdb_identity(self):
        def unidentified_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            source = payload["entries"][0]
            payload["entries"] = [
                {**source, "key": "unknown-1", "tmdb_id": "", "subscription_origin": "manual"},
                {**source, "key": "unknown-2", "tmdb_id": "", "subscription_origin": "manual"},
            ]
            return payload

        application = Flask(f"{__name__}-calendar-unidentified-dedup")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=unidentified_loader)
        calendar = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv&includeUnlinked=1"
        ).get_json()["calendar"]

        self.assertEqual(len(calendar["entries"]), 2)
        self.assertEqual(calendar["stats"]["totalEntries"], 2)
        self.assertTrue(all(entry["sourceCount"] == 1 for entry in calendar["entries"]))

    def test_calendar_source_order_does_not_change_primary_entry(self):
        entries = [
            {
                "date": "2026-07-22", "key": "torra:public", "title": "测试剧", "mediaType": "tv",
                "tmdbId": "101", "seasonNumber": 2, "episodeNumber": 3, "sourceLabel": "Torra 只读追更",
                "subscriptionOrigin": "torra", "followScopeExplicit": True,
            },
            {
                "date": "2026-07-22", "key": "sub-manual", "title": "测试剧", "mediaType": "tv",
                "tmdbId": "101", "seasonNumber": 2, "episodeNumber": 3, "sourceLabel": "Fluxa 手动追更",
                "subscriptionOrigin": "manual", "followScopeExplicit": True,
            },
        ]

        forward = _merge_calendar_entries(entries)[0]
        reverse = _merge_calendar_entries(list(reversed(entries)))[0]

        self.assertEqual(forward, reverse)
        self.assertEqual(forward["key"], "sub-manual")

    def test_default_calendar_hides_unlinked_rows_and_explicit_query_can_read_them(self):
        def unlinked_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            payload["entries"][0]["subscription_origin"] = "auto"
            return payload

        application = Flask(f"{__name__}-unlinked")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([])
        register_calendar_timeline(application, calendar_loader=unlinked_loader)
        client = application.test_client()

        default_calendar = client.get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]
        advanced_calendar = client.get(
            "/api/v2/calendar?year=2026&month=7&type=tv&includeUnlinked=1"
        ).get_json()["calendar"]

        self.assertEqual(default_calendar["entries"], [])
        self.assertEqual(default_calendar["stats"]["excludedUnlinked"], 1)
        self.assertEqual(
            {
                key: default_calendar["stats"][key]
                for key in ("linkedEntries", "unlinkedEntries", "totalEntries")
            },
            {"linkedEntries": 0, "unlinkedEntries": 1, "totalEntries": 1},
        )
        self.assertEqual(
            {
                key: advanced_calendar["stats"][key]
                for key in ("linkedEntries", "unlinkedEntries", "totalEntries")
            },
            {"linkedEntries": 0, "unlinkedEntries": 1, "totalEntries": 1},
        )
        self.assertEqual(advanced_calendar["entries"][0]["status"], "unlinked")

    def test_exact_emby_episode_fact_marks_only_that_episode_playable(self):
        def two_episode_loader(year, month, media_type):
            payload = calendar_loader(year, month, media_type)
            first = payload["entries"][0]
            payload["entries"] = [
                {**first, "episode_number": 3, "episode_label": "S02E03"},
                {**first, "episode_number": 4, "episode_label": "S02E04"},
            ]
            return payload

        playable = FakeTaskService().full_snapshot()["items"][0]
        playable["pipelineFacts"] = [pipeline_fact(
            "emby", "succeeded", scope="episode", observed_at="2026-07-22T01:20:00Z",
        )]
        playable["pipelineOutcome"] = {
            "state": "playable",
            "stage": "emby",
            "reasonCode": "EMBY_EPISODE_INDEXED",
            "reasonText": "Emby 已收录目标集",
            "observedAt": "2026-07-22T01:20:00Z",
            "playableAt": "2026-07-22T01:20:00Z",
        }
        application = Flask(f"{__name__}-playable")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([playable])
        register_calendar_timeline(
            application,
            calendar_loader=two_episode_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )

        entries = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"]
        by_episode = {entry["episodeNumber"]: entry for entry in entries}

        self.assertEqual(by_episode[3]["status"], "playable")
        self.assertEqual(by_episode[3]["playableAt"], "2026-07-22T01:20:00Z")
        self.assertNotEqual(by_episode[4]["status"], "playable")

    def test_calendar_matches_public_torra_key_to_raw_task_without_tmdb(self):
        from app.torra_subscription_keys import torra_public_subscription_key

        remote_id = "remote-private-calendar"
        public_key = torra_public_subscription_key(remote_id)
        task_item = {
            "title": "远端无 TMDB 追更",
            "mediaType": "tv",
            "tmdbId": "",
            "seasonNumber": 1,
            "chainId": "chain:remote-calendar",
            "targetKey": "tv:title:remote-calendar:season:1",
            "subscriptionId": f"torra:{remote_id}",
            "sourceIds": {"subscriptionIds": [f"torra:{remote_id}"]},
            "healthState": "waiting",
            "updatedAt": "2026-07-22T01:30:00Z",
            "freshUntil": "2026-07-23T01:30:00Z",
            "episodeNumber": 1,
            "pipelineFacts": [pipeline_fact(
                "qb", "succeeded", observed_at="2026-07-22T01:00:00Z",
                fresh_until="2026-07-23T01:30:00Z",
            )],
            "pipelineOutcome": {
                "state": "evidence_insufficient",
                "stage": "",
                "reasonCode": "EVIDENCE_INSUFFICIENT",
                "reasonText": "缺少当前目标的明确可播放证据",
                "observedAt": "",
                "playableAt": "",
            },
            "episodeEvidence": [{
                "seasonNumber": 1,
                "episodeStart": 1,
                "episodeEnd": 1,
                "numberingScheme": "season_episode",
                "stage": "download",
                "source": "qBittorrent",
                "observedAt": "2026-07-22T01:00:00Z",
                "status": "done",
            }],
            "stages": [],
        }

        def remote_loader(year, month, media_type):
            return {
                "success": True,
                "year": year,
                "month": month,
                "type": media_type,
                "entries": [{
                    "date": "2026-07-22",
                    "key": public_key,
                    "title": task_item["title"],
                    "media_type": "tv",
                    "tmdb_id": "",
                    "season_number": 1,
                    "episode_number": 1,
                    "episode_label": "S01E01",
                    "in_library": False,
                    "subscription_origin": "torra",
                    "torra_linked": True,
                    "follow_scope_explicit": True,
                }],
                "stats": {"entries": 1, "titles": 1, "in_library": 0, "pending": 1},
                "errors": [],
            }

        application = Flask(f"{__name__}-public-torra-calendar")
        application.extensions["mcc_task_chain_v2_service"] = FakeTaskService([task_item])
        register_calendar_timeline(
            application,
            calendar_loader=remote_loader,
            clock=lambda: datetime(2026, 7, 22, 1, 31, tzinfo=timezone.utc),
        )

        entry = application.test_client().get(
            "/api/v2/calendar?year=2026&month=7&type=tv"
        ).get_json()["calendar"]["entries"][0]

        self.assertEqual(entry["chainId"], "chain:remote-calendar")
        self.assertEqual(entry["acquiredAt"], "2026-07-22T01:00:00Z")

    def test_torra_calendar_generation_reuses_bounded_concurrency_and_keeps_all_entries(self):
        rows = [{
            "id": f"torra:remote-{index}",
            "remoteRef": f"remote-{index}",
            "title": f"远端追更 {index:02d}",
            "mediaType": "tv",
            "tmdbId": str(3000 + index),
            "seasonNumber": 1,
            "reconciliationState": "only_torra",
            "observedAt": "2026-07-23T00:00:00Z",
        } for index in range(10)]
        self.app.extensions["mcc_subscription_reconciliation"] = FakeReconciliationService(rows)
        lock = threading.Lock()
        active = 0
        max_active = 0

        def remote_entries(item, year, month, media_type):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return ([{
                "date": "2026-07-23",
                "key": item["subscription_key"],
                "title": item["title"],
                "media_type": "tv",
                "tmdb_id": item["tmdb_id"],
                "source_label": item["source_label"],
                "season_number": 1,
                "episode_number": 1,
                "episode_label": "S01E01",
                "in_library": False,
                "subscription_created_at": item["subscribed_at"],
                "follow_scope_explicit": True,
                "include_past_episodes": False,
                "allowed_delay_hours": 24,
            }], "")

        with patch.object(
            discover_runtime,
            "build_subscription_calendar_entries_for_item",
            side_effect=remote_entries,
        ):
            payload = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()

        remote_entries_result = [
            entry for entry in payload["calendar"]["entries"]
            if entry.get("sourceLabel") == "Torra 只读追更"
        ]
        self.assertGreater(max_active, 1)
        self.assertLessEqual(max_active, 8)
        self.assertEqual(len(remote_entries_result), len(rows))
        self.assertEqual(
            [entry["title"] for entry in remote_entries_result],
            sorted(row["title"] for row in rows),
        )

    def test_torra_calendar_source_error_is_public_and_not_raw(self):
        self.app.extensions["mcc_subscription_reconciliation"] = FakeReconciliationService(
            [],
            source_error="token=must-not-escape /private/source",
        )

        payload = self.client.get("/api/v2/calendar?year=2026&month=7&type=tv").get_json()

        errors = payload["calendar"]["errors"]
        self.assertIn("Torra 只读追更暂时无法读取", errors)
        self.assertNotIn("must-not-escape", str(errors))
        self.assertNotIn("/private/source", str(errors))

    def test_invalid_detail_date_and_range_are_rejected(self):
        invalid_date = self.client.get("/api/v2/calendar?date=2026-02-30&view=detail")
        invalid_range = self.client.get("/api/v2/calendar?from=2026-07-01&to=2027-01-01&view=summary")
        missing_detail_date = self.client.get("/api/v2/calendar?view=detail")

        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(invalid_range.status_code, 400)
        self.assertEqual(missing_detail_date.status_code, 400)


if __name__ == "__main__":
    unittest.main()
