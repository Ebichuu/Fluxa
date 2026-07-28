from __future__ import annotations

import tempfile
import unittest
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
            self.assertEqual(len(download_events), 1)

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
                "SELECT event_id FROM resource_events WHERE chain_id='chain:legacy' AND artifact_key=''",
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
