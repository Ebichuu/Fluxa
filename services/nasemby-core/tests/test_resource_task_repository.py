from __future__ import annotations

import tempfile
import unittest
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.resource_task_repository import ResourceIdentityConflict, ResourceTaskRepository


NOW = datetime(2026, 7, 22, 6, 0, tzinfo=timezone.utc)


def qb_pipeline_facts(hashes, reason_text="下载完成"):
    hashes = list(hashes)
    if not hashes:
        return []
    return [{
        "stage": "qb", "state": "succeeded", "scope": "file", "evidence": "verified",
        "eventAt": "2026-07-22T05:58:00Z",
        "observedAt": "2026-07-22T05:59:00Z", "freshUntil": "2026-07-22T06:05:00Z",
        "source": "qBittorrent", "sourceRef": hashes[0] if len(hashes) == 1 else "",
        "reasonCode": "DOWNLOAD_DONE", "reasonText": reason_text,
        "units": [{
            "unitKey": value,
            "state": "succeeded",
            "scope": "file",
            "evidence": "verified",
            "eventAt": "2026-07-22T05:58:00Z",
            "observedAt": "2026-07-22T05:59:00Z",
            "freshUntil": "2026-07-22T06:05:00Z",
            "sourceRef": value,
            "reasonCode": "DOWNLOAD_DONE",
            "reasonText": reason_text,
        } for value in hashes] if len(hashes) > 1 else [],
    }]


def snapshot(reason_text="下载完成"):
    return {
        "items": [{
            "chainId": "chain:test",
            "mediaKey": "tv:tmdb:100",
            "targetKey": "tv:tmdb:100:season:2",
            "subscriptionId": "subscription:test",
            "mediaType": "tv",
            "tmdbId": "100",
            "title": "测试剧",
            "origin": "subscription",
            "state": "active",
            "healthState": "waiting",
            "observedAt": "2026-07-22T06:00:00Z",
            "freshUntil": "2026-07-22T06:05:00Z",
            "source": "task-chain",
            "reasonCode": "TASK_IN_PROGRESS",
            "reasonText": "正在处理",
            "sourceIds": {"qbHashes": ["hash-1"], "symediaIds": []},
            "pipelineFacts": qb_pipeline_facts(["hash-1"], reason_text),
            "stages": [{
                "stage": "download",
                "status": "done",
                "healthState": "normal",
                "evidence": "verified",
                "observedAt": "2026-07-22T05:59:00Z",
                "freshUntil": "2026-07-22T06:05:00Z",
                "source": "qBittorrent",
                "reasonCode": "DOWNLOAD_DONE",
                "reasonText": reason_text,
            }],
        }],
    }


def archive_snapshot(*refs, chain_id="chain:archive"):
    units = [{
        "unitKey": ref,
        "state": "succeeded" if index < 2 else "failed",
        "scope": "file",
        "evidence": "verified",
        "eventAt": f"2026-07-28T02:0{index}:00Z",
        "observedAt": "2026-07-28T03:00:00Z",
        "freshUntil": "2026-07-28T03:05:00Z",
        "sourceRef": ref,
        "resultRef": f"result-{index}",
        "reasonCode": "SYMEDIA_ORGANIZED" if index < 2 else "SYMEDIA_LIBRARY_FAILED",
        "reasonText": "整理入库完成" if index < 2 else "媒体识别失败",
    } for index, ref in enumerate(refs)]
    return {
        "items": [{
            "chainId": chain_id,
            "mediaKey": "tv:tmdb:900",
            "targetKey": "tv:tmdb:900:season:1",
            "subscriptionId": "subscription:archive",
            "mediaType": "tv",
            "tmdbId": "900",
            "title": "归档测试剧",
            "origin": "library",
            "confidence": "strong",
            "identityState": "linked",
            "state": "blocked",
            "healthState": "action_required",
            "observedAt": "2026-07-28T03:00:00Z",
            "freshUntil": "2026-07-28T03:05:00Z",
            "source": "task-chain",
            "reasonCode": "SYMEDIA_LIBRARY_FAILED",
            "reasonText": "部分文件整理失败",
            "sourceIds": {"qbHashes": [], "symediaIds": list(refs)},
            "pipelineFacts": [{
                "stage": "symedia",
                "state": "failed",
                "scope": "file",
                "evidence": "verified",
                "eventAt": "2026-07-28T02:02:00Z",
                "observedAt": "2026-07-28T03:00:00Z",
                "freshUntil": "2026-07-28T03:05:00Z",
                "source": "Symedia",
                "sourceRef": "",
                "reasonCode": "SYMEDIA_LIBRARY_FAILED",
                "reasonText": "部分文件整理失败",
                "units": units,
            }],
            "stages": [],
        }],
    }


def legacy_snapshot(*hashes, chain_id="chain:legacy"):
    return {
        "items": [{
            "chainId": chain_id,
            "mediaKey": "unknown:title:灿如繁星",
            "targetKey": "unknown:title:灿如繁星:season:1",
            "subscriptionId": "",
            "mediaType": "unknown",
            "tmdbId": "",
            "seasonNumber": 1,
            "title": "[灿如繁星].Road.to.Success.S01",
            "origin": "download",
            "state": "completed",
            "healthState": "evidence_insufficient",
            "identityState": "unidentified",
            "observedAt": "2026-07-22T06:00:00Z",
            "freshUntil": "2026-07-22T06:05:00Z",
            "source": "task-chain",
            "reasonCode": "TASK_IDENTITY_UNLINKED",
            "reasonText": "下载证据尚未关联到媒体目标",
            "sourceIds": {"qbHashes": list(hashes), "symediaIds": []},
            "pipelineFacts": qb_pipeline_facts(hashes),
            "evidenceOwnership": [],
            "stages": [{
                "stage": "download",
                "status": "done",
                "healthState": "normal",
                "evidence": "verified",
                "observedAt": "2026-07-22T05:59:00Z",
                "freshUntil": "2026-07-22T06:05:00Z",
                "source": "qBittorrent",
                "reasonCode": "DOWNLOAD_DONE",
                "reasonText": "下载完成",
            }],
        }],
    }


def canonical_snapshot(*hashes, include_anchor=True, season=1, method="symedia_title_season_unique"):
    target = f"tv:tmdb:808:season:{season}"
    records = [{
        "artifactKey": f"artifact:{value}",
        "ownerTargetKey": target,
        "matchMethod": method,
        "confidence": "fallback" if method == "symedia_title_season_unique" else "strong",
        "conflictCandidates": [],
        "source": "qBittorrent",
        "mediaType": "tv",
        "seasonNumber": season,
    } for value in hashes]
    if include_anchor:
        records.append({
            "artifactKey": "artifact:symedia:anchor-1",
            "ownerTargetKey": target,
            "matchMethod": "symedia_tmdb_anchor",
            "confidence": "strong",
            "conflictCandidates": [],
            "source": "Symedia",
            "mediaType": "tv",
            "seasonNumber": season,
        })
    return {
        "items": [{
            "chainId": "chain:canonical",
            "mediaKey": "tv:tmdb:808",
            "targetKey": target,
            "subscriptionId": "",
            "mediaType": "tv",
            "tmdbId": "808",
            "seasonNumber": season,
            "title": "灿如繁星",
            "origin": "download",
            "state": "completed",
            "healthState": "normal",
            "identityState": "linked",
            "observedAt": "2026-07-22T06:00:00Z",
            "freshUntil": "2026-07-22T06:05:00Z",
            "source": "task-chain",
            "reasonCode": "TASK_COMPLETED",
            "reasonText": "处理完成",
            "sourceIds": {"qbHashes": list(hashes), "symediaIds": []},
            "pipelineFacts": qb_pipeline_facts(hashes),
            "evidenceOwnership": records,
            "stages": [{
                "stage": "download",
                "status": "done",
                "healthState": "normal",
                "evidence": "verified",
                "observedAt": "2026-07-22T05:59:00Z",
                "freshUntil": "2026-07-22T06:05:00Z",
                "source": "qBittorrent",
                "reasonCode": "DOWNLOAD_DONE",
                "reasonText": "下载完成",
            }],
        }],
    }


def rows(repository, statement, parameters=()):
    with closing(repository.runtime.connect()) as connection:
        return [dict(row) for row in connection.execute(statement, parameters).fetchall()]


class ResourceTaskRepositoryTests(unittest.TestCase):
    def test_symedia_archive_reads_successful_file_units_from_mixed_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(archive_snapshot("file-a", "file-b", "file-c"))

            events = repository.list_symedia_archive_events("2026-07-28")

            self.assertEqual(len(events), 2)
            self.assertEqual({event["chainId"] for event in events}, {"chain:archive"})
            self.assertEqual(len({event["fileKey"] for event in events}), 2)

    def test_symedia_archive_reads_success_units_from_legacy_failed_parent_event(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            payload = archive_snapshot()
            payload["items"][0]["sourceIds"] = {"qbHashes": [], "symediaIds": []}
            payload["items"][0]["pipelineFacts"] = []
            repository.record_snapshot(payload)
            with repository.runtime.transaction(immediate=True) as connection:
                repository._append_event(connection, "chain:archive", "", {
                    "stage": "symedia",
                    "status": "failed",
                    "healthState": "action_required",
                    "evidence": "verified",
                    "eventAt": "2026-07-28T02:02:00Z",
                    "observedAt": "2026-07-28T03:00:00Z",
                    "freshUntil": "2026-07-28T03:05:00Z",
                    "source": "Symedia",
                    "reasonCode": "SYMEDIA_LIBRARY_FAILED",
                    "reasonText": "部分文件整理失败",
                    "_eventPayload": {
                        "kind": "pipeline_fact",
                        "units": [
                            {
                                "state": "succeeded",
                                "evidence": "verified",
                                "eventAt": "2026-07-28T02:00:00Z",
                                "sourceRef": "fact-symedia:file-a",
                            },
                            {
                                "state": "failed",
                                "evidence": "verified",
                                "eventAt": "2026-07-28T02:01:00Z",
                                "sourceRef": "fact-symedia:file-b",
                            },
                        ],
                    },
                }, "2026-07-28T03:00:00Z")

            events = repository.list_symedia_archive_events("2026-07-28")

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["fileKey"], "fact-symedia:file-a")

    def test_event_time_is_persisted_and_existing_database_is_upgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "media.sqlite3"
            repository = ResourceTaskRepository(path, clock=lambda: NOW)
            repository.record_snapshot(snapshot())

            event = repository.list_events("chain:test")[0]
            self.assertEqual(event["event_at"], "2026-07-22T05:58:00Z")
            self.assertEqual(event["observed_at"], "2026-07-22T05:59:00Z")
            self.assertIn("event_at", {
                row["name"] for row in rows(repository, "PRAGMA table_info(resource_events)")
            })

    def test_existing_emby_success_preserves_first_confirmed_playable_time(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            payload = snapshot()
            payload["items"][0]["pipelineFacts"] = [{
                "stage": "emby", "state": "succeeded", "scope": "episode", "evidence": "verified",
                "eventAt": "2026-07-22T05:00:00Z",
                "firstConfirmedPlayableAt": "2026-07-22T05:00:00Z",
                "observedAt": "2026-07-22T05:00:00Z", "freshUntil": "2026-07-22T06:05:00Z",
                "source": "Emby", "sourceRef": "episode-private", "reasonCode": "EMBY_EPISODE_INDEXED",
                "reasonText": "Emby 已收录目标集",
            }]
            repository.record_snapshot(payload)
            refreshed = snapshot()
            refreshed["items"][0]["pipelineFacts"] = [{
                **payload["items"][0]["pipelineFacts"][0],
                "eventAt": "2026-07-22T06:00:00Z",
                "firstConfirmedPlayableAt": "2026-07-22T06:00:00Z",
                "observedAt": "2026-07-22T06:00:00Z",
            }]

            repository.project_historical_fact_times(refreshed)

            fact = refreshed["items"][0]["pipelineFacts"][0]
            self.assertEqual(fact["eventAt"], "2026-07-22T05:00:00Z")
            self.assertEqual(fact["firstConfirmedPlayableAt"], "2026-07-22T05:00:00Z")

    def test_episode_history_survives_later_snapshot_without_current_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            payload = snapshot()
            payload["items"][0]["episodeEvidence"] = [{
                "seasonNumber": 2,
                "episodeStart": 2,
                "episodeEnd": 3,
                "numberingScheme": "season_episode",
                "stage": "library",
                "artifactKey": "artifact:hash-1",
                "source": "Symedia",
                "eventAt": "2026-07-22T05:30:00Z",
                "observedAt": "2026-07-22T05:40:00Z",
                "matchMethod": "artifact_exact",
                "status": "done",
                "reasonCode": "SYMEDIA_ORGANIZED",
                "reasonText": "Symedia 整理入库完成",
                "ownerScope": "episode_range",
                "ownerTargetKey": "tv:tmdb:100:season:2:episodes:2-3",
                "parentTargetKey": "tv:tmdb:100:season:2",
            }]
            repository.record_snapshot(payload)
            later = snapshot()
            later["items"][0]["episodeEvidence"] = []
            repository.record_snapshot(later)

            events = repository.list_episode_events("chain:test")

            library = next(event for event in events if event["kind"] == "episode_evidence")
            self.assertEqual(library["artifactKey"], "artifact:hash-1")
            self.assertEqual(library["eventAt"], "2026-07-22T05:30:00Z")
            self.assertTrue(library["freshUntil"])
            self.assertEqual((library["episodeStart"], library["episodeEnd"]), (2, 3))

    def test_migration_preview_requires_same_snapshot_symedia_anchor_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(legacy_snapshot("hash-a"))

            rejected = repository.preview_snapshot_migrations(
                canonical_snapshot("hash-a", include_anchor=False)
            )
            allowed = repository.preview_snapshot_migrations(canonical_snapshot("hash-a"))

            self.assertEqual(rejected["artifactMigrations"], 0)
            self.assertEqual(rejected["migrationSkipReasons"], {"SYMEDIA_ANCHOR_MISSING": 1})
            self.assertEqual(allowed["artifactMigrations"], 1)
            self.assertEqual(allowed["chainAliases"], 1)
            self.assertEqual(rows(
                repository,
                "SELECT chain_id FROM resource_artifacts WHERE artifact_key='artifact:hash-a'",
            )[0]["chain_id"], "chain:legacy")
            self.assertIsNone(repository.get_chain("chain:canonical"))

    def test_migration_preview_rejects_qb_season_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(legacy_snapshot("hash-a"))
            payload = canonical_snapshot("hash-a")
            payload["items"][0]["evidenceOwnership"][0]["seasonNumber"] = 2

            result = repository.preview_snapshot_migrations(payload)

            self.assertEqual(result["artifactMigrations"], 0)
            self.assertEqual(result["migrationSkipReasons"], {"TARGET_SCOPE_MISMATCH": 1})

    def test_whole_chain_migration_moves_history_aliases_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(canonical_snapshot())
            repository.record_snapshot(legacy_snapshot("hash-a", "hash-b"))

            first = repository.record_snapshot(canonical_snapshot("hash-a", "hash-b"))
            second = repository.record_snapshot(canonical_snapshot("hash-a", "hash-b"))

            self.assertEqual(first["artifactMigrations"], 2)
            self.assertEqual(first["chainAliases"], 1)
            self.assertEqual(first["deletedEmptyChains"], 1)
            self.assertEqual(first["artifactConflicts"], 0)
            self.assertEqual(second["artifactMigrations"], 0)
            self.assertEqual(second["chainAliases"], 0)
            self.assertEqual(repository.resolve_chain_id("chain:legacy"), "chain:canonical")
            self.assertEqual(repository.get_chain("chain:legacy")["chain_id"], "chain:canonical")
            self.assertEqual(rows(
                repository,
                "SELECT count(*) n FROM resource_chains WHERE chain_id='chain:legacy'",
            )[0]["n"], 0)
            self.assertTrue(all(
                row["chain_id"] == "chain:canonical"
                for row in rows(repository, "SELECT chain_id FROM resource_artifacts")
            ))
            download_events = [
                event for event in repository.list_events("chain:canonical")
                if event["reason_code"] == "DOWNLOAD_DONE"
            ]
            self.assertEqual(len(download_events), 2)

    def test_partial_artifact_migration_keeps_chain_level_history_and_old_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(legacy_snapshot("hash-a"))
            repository.record_snapshot(legacy_snapshot("hash-a", "hash-b"))

            result = repository.record_snapshot(canonical_snapshot("hash-a"))

            self.assertEqual(result["artifactMigrations"], 1)
            self.assertEqual(result["chainAliases"], 0)
            self.assertEqual(result["deletedEmptyChains"], 0)
            owners = {
                row["artifact_key"]: row["chain_id"]
                for row in rows(repository, "SELECT artifact_key, chain_id FROM resource_artifacts")
            }
            self.assertEqual(owners["artifact:hash-a"], "chain:canonical")
            self.assertEqual(owners["artifact:hash-b"], "chain:legacy")
            self.assertIsNotNone(repository.get_chain("chain:legacy"))
            self.assertTrue(rows(
                repository,
                "SELECT event_id FROM resource_events "
                "WHERE chain_id='chain:legacy' AND artifact_key='artifact:hash-b'",
            ))
            self.assertTrue(rows(
                repository,
                "SELECT event_id FROM resource_events WHERE chain_id='chain:canonical' AND artifact_key='artifact:hash-a'",
            ))

    def test_backup_failure_prevents_snapshot_and_migration_writes(self):
        class BackupFailureRepository(ResourceTaskRepository):
            def _ensure_migration_backup(self):
                raise OSError("backup failed")

        with tempfile.TemporaryDirectory() as directory:
            repository = BackupFailureRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(legacy_snapshot("hash-a"))

            result = repository.record_snapshot(canonical_snapshot("hash-a"))

            self.assertFalse(result["persisted"])
            self.assertEqual(result["migrationSkipReasons"], {"BACKUP_FAILED": 1})
            self.assertIsNone(repository.get_chain("chain:canonical"))
            self.assertEqual(rows(
                repository,
                "SELECT chain_id FROM resource_artifacts WHERE artifact_key='artifact:hash-a'",
            )[0]["chain_id"], "chain:legacy")

    def test_conditional_owner_change_rolls_back_entire_snapshot(self):
        class RacingRepository(ResourceTaskRepository):
            def _conditional_reassign_artifact(self, connection, plan, now_text):
                connection.execute(
                    "UPDATE resource_artifacts SET chain_id='chain:racer' WHERE artifact_key=?",
                    (plan["artifactKey"],),
                )
                return super()._conditional_reassign_artifact(connection, plan, now_text)

        with tempfile.TemporaryDirectory() as directory:
            repository = RacingRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(legacy_snapshot("hash-a"))
            racer = legacy_snapshot(chain_id="chain:racer")
            racer["items"][0]["sourceIds"] = {"qbHashes": [], "symediaIds": []}
            repository.record_snapshot(racer)

            result = repository.record_snapshot(canonical_snapshot("hash-a"))

            self.assertFalse(result["persisted"])
            self.assertEqual(result["migrationSkipReasons"], {"OWNER_CHANGED_CONCURRENTLY": 1})
            self.assertIsNone(repository.get_chain("chain:canonical"))
            self.assertEqual(rows(
                repository,
                "SELECT chain_id FROM resource_artifacts WHERE artifact_key='artifact:hash-a'",
            )[0]["chain_id"], "chain:legacy")

    def test_snapshot_is_idempotent_and_redacts_event_text(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: NOW,
            )
            payload = snapshot("failed https://rss.example/feed?passkey=secret password=plain")

            first = repository.record_snapshot(payload)
            second = repository.record_snapshot(payload)
            events = repository.list_events("chain:test")

            self.assertEqual(first["events"], 1)
            self.assertEqual(second["events"], 0)
            self.assertEqual(repository.get_chain("chain:test")["target_key"], "tv:tmdb:100:season:2")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["stage"], "qb")
            self.assertEqual(events[0]["status"], "succeeded")
            self.assertNotIn("secret", events[0]["reason_text"])
            self.assertNotIn("plain", events[0]["reason_text"])
            self.assertIn("passkey=***", events[0]["reason_text"])
            self.assertNotIn("hash-1", events[0]["payload_json"])
            self.assertIn('"kind":"pipeline_fact"', events[0]["payload_json"])

    def test_pipeline_event_whitelist_and_stable_failure_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            payload = snapshot()
            common = {
                "scope": "file",
                "observedAt": "2026-07-22T05:59:00Z",
                "freshUntil": "2026-07-22T06:05:00Z",
                "source": "qBittorrent",
                "sourceRef": "hash-1",
            }
            payload["items"][0]["pipelineFacts"] = [
                {
                    **common, "stage": "qb", "state": "active", "evidence": "verified",
                    "reasonCode": "QB_DOWNLOAD_ACTIVE", "reasonText": "正在下载",
                },
                {
                    **common, "stage": "cloud115", "state": "waiting", "evidence": "verified",
                    "reasonCode": "CLOUD115_WAITING", "reasonText": "等待处理",
                },
                {
                    **common, "stage": "strm", "state": "unknown", "evidence": "missing",
                    "reasonCode": "STRM_UNKNOWN", "reasonText": "暂未确认",
                },
                {
                    **common, "stage": "torra", "state": "not_applicable", "evidence": "verified",
                    "reasonCode": "TORRA_DISABLED", "reasonText": "订阅已停用",
                },
                {
                    **common, "stage": "symedia", "state": "failed", "evidence": "verified",
                    "reasonCode": "SYMEDIA_FAILED", "reasonText": "没有稳定发生依据",
                },
            ]

            first = repository.record_snapshot(payload)
            events = repository.list_events("chain:test")

            self.assertEqual(first["events"], 1)
            self.assertEqual([(event["stage"], event["status"]) for event in events], [
                ("torra", "not_applicable"),
            ])
            reloaded = ResourceTaskRepository(repository.database_path, clock=lambda: NOW)
            self.assertEqual(
                [(event["stage"], event["status"]) for event in reloaded.list_events("chain:test")],
                [("torra", "not_applicable")],
            )

            failed = {
                **common,
                "stage": "symedia",
                "state": "failed",
                "evidence": "verified",
                "eventAt": "2026-07-22T05:50:00Z",
                "resultRef": "symedia-run-1",
                "reasonCode": "SYMEDIA_FAILED",
                "reasonText": "媒体信息匹配失败",
            }
            payload["items"][0]["pipelineFacts"] = [failed]
            repeated = repository.record_snapshot(payload)
            payload["items"][0]["pipelineFacts"] = [{
                **failed,
                "observedAt": "2026-07-22T06:00:00Z",
                "freshUntil": "2026-07-22T06:05:00Z",
                "reasonText": "媒体信息匹配失败（重复轮询）",
            }]
            duplicate = repository.record_snapshot(payload)
            payload["items"][0]["pipelineFacts"] = [{
                **failed,
                "eventAt": "2026-07-22T05:55:00Z",
                "resultRef": "symedia-run-2",
            }]
            new_failure = repository.record_snapshot(payload)

            self.assertEqual(repeated["events"], 1)
            self.assertEqual(duplicate["events"], 0)
            self.assertEqual(new_failure["events"], 1)
            self.assertEqual(sum(
                event["stage"] == "symedia" and event["status"] == "failed"
                for event in repository.list_events("chain:test")
            ), 2)

    def test_transient_event_cleanup_is_scoped_audited_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "media.sqlite3"
            repository = ResourceTaskRepository(path, clock=lambda: NOW)
            repository.record_snapshot(snapshot())
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "DELETE FROM resource_ledger_migrations WHERE migration_id='resource-events-transient-v1'"
                )
                for index, (stage, status, kind) in enumerate((
                    ("qb", "active", "pipeline_fact"),
                    ("torra", "waiting", "pipeline_fact_unit"),
                    ("symedia", "active", "episode_evidence"),
                    ("scheduler", "waiting", "operation"),
                )):
                    repository._append_event(connection, "chain:test", "", {
                        "stage": stage,
                        "status": status,
                        "healthState": "waiting",
                        "evidence": "verified",
                        "observedAt": f"2026-07-22T05:5{index}:00Z",
                        "freshUntil": "2026-07-22T06:05:00Z",
                        "source": "test",
                        "reasonCode": f"TEST_{index}",
                        "reasonText": "test",
                        "_eventPayload": {"kind": kind, "unitKey": f"unit:{index}"},
                    }, "2026-07-22T06:00:00Z")

            migrated = ResourceTaskRepository(path, clock=lambda: NOW)
            repeated = migrated._run_transient_event_cleanup()
            statuses = [(row["stage"], row["status"]) for row in rows(
                migrated, "SELECT stage, status FROM resource_events ORDER BY stage"
            )]
            audits = rows(migrated, "SELECT * FROM resource_ledger_migrations")

            self.assertEqual(migrated.transient_event_cleanup["deletedEvents"], 3)
            self.assertEqual(migrated.transient_event_cleanup["deletedByStage"], {
                "qb": 1, "symedia": 1, "torra": 1,
            })
            self.assertTrue(migrated.transient_event_cleanup["backupCreated"])
            self.assertEqual(repeated["deletedEvents"], 0)
            self.assertTrue(repeated["alreadyApplied"])
            self.assertEqual(len(audits), 1)
            self.assertEqual(json.loads(audits[0]["stage_counts_json"]), {
                "qb": 1, "symedia": 1, "torra": 1,
            })
            self.assertIn(("scheduler", "waiting"), statuses)
            self.assertIn(("qb", "succeeded"), statuses)

    def test_transient_cleanup_backup_or_audit_failure_rolls_back(self):
        class BackupFailureRepository(ResourceTaskRepository):
            def _ensure_transient_event_backup(self):
                raise OSError("backup failed")

        class AuditFailureRepository(ResourceTaskRepository):
            @staticmethod
            def _write_transient_cleanup_audit(connection, result, now_text):
                raise sqlite3.OperationalError("audit failed")

        def prepare(path):
            repository = ResourceTaskRepository(path, clock=lambda: NOW)
            repository.record_snapshot(snapshot())
            with repository.runtime.transaction(immediate=True) as connection:
                connection.execute(
                    "DELETE FROM resource_ledger_migrations WHERE migration_id='resource-events-transient-v1'"
                )
                repository._append_event(connection, "chain:test", "", {
                    "stage": "qb", "status": "active", "healthState": "waiting", "evidence": "verified",
                    "observedAt": "2026-07-22T05:59:00Z", "freshUntil": "2026-07-22T06:05:00Z",
                    "source": "test", "reasonCode": "QB_ACTIVE", "reasonText": "test",
                    "_eventPayload": {"kind": "pipeline_fact", "unitKey": "unit:test"},
                }, "2026-07-22T06:00:00Z")

        with tempfile.TemporaryDirectory() as directory:
            backup_path = Path(directory) / "backup.sqlite3"
            prepare(backup_path)
            backup_failed = BackupFailureRepository(backup_path, clock=lambda: NOW)
            self.assertEqual(backup_failed.transient_event_cleanup["status"], "failed")
            self.assertEqual(rows(
                backup_failed, "SELECT count(*) n FROM resource_events WHERE status='active'"
            )[0]["n"], 1)
            self.assertEqual(rows(
                backup_failed, "SELECT count(*) n FROM resource_ledger_migrations"
            )[0]["n"], 0)

            audit_path = Path(directory) / "audit.sqlite3"
            prepare(audit_path)
            audit_failed = AuditFailureRepository(audit_path, clock=lambda: NOW)
            self.assertEqual(audit_failed.transient_event_cleanup["status"], "failed")
            self.assertEqual(rows(
                audit_failed, "SELECT count(*) n FROM resource_events WHERE status='active'"
            )[0]["n"], 1)
            self.assertEqual(rows(
                audit_failed, "SELECT count(*) n FROM resource_ledger_migrations"
            )[0]["n"], 0)

    def test_identity_upgrade_keeps_one_chain_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(
                Path(directory) / "media.sqlite3",
                clock=lambda: NOW,
            )
            repository.record_snapshot(snapshot())

            first = repository.record_identity_alias(
                "chain:test",
                "artifact:anonymous:old",
                "artifact:remote-1",
                artifact={"type": "remote_file", "source": "Symedia", "externalId": "remote-1"},
            )
            second = repository.record_identity_alias(
                "chain:test",
                "artifact:anonymous:old",
                "artifact:remote-1",
                artifact={"type": "remote_file", "source": "Symedia", "externalId": "remote-1"},
            )
            events = repository.list_events("chain:test")

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(sum(event["reason_code"] == "ARTIFACT_IDENTITY_UPGRADED" for event in events), 1)
            self.assertEqual(repository.get_chain("chain:test")["chain_id"], "chain:test")

    def test_artifact_cannot_be_silently_moved_to_another_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = ResourceTaskRepository(Path(directory) / "media.sqlite3", clock=lambda: NOW)
            repository.record_snapshot(snapshot())
            other = snapshot()
            other["items"][0].update({
                "chainId": "chain:other",
                "mediaKey": "tv:tmdb:200",
                "targetKey": "tv:tmdb:200:season:1",
            })

            result = repository.record_snapshot(other)

            self.assertEqual(result["artifactConflicts"], 1)
            self.assertEqual(repository.get_chain("chain:other")["health_state"], "action_required")
            self.assertEqual(repository.list_events("chain:other")[0]["reason_code"], "ARTIFACT_CHAIN_CONFLICT")
            with self.assertRaises(ResourceIdentityConflict):
                repository.record_identity_alias(
                    "chain:other",
                    "artifact:old",
                    "artifact:hash-1",
                )


if __name__ == "__main__":
    unittest.main()
