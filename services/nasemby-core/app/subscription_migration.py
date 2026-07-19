from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path


EMPTY_SUBSCRIPTION_PAYLOAD = {
    "items": [],
    "last_run_at": "",
    "stats": {"total": 0, "movie": 0, "tv": 0},
}


def _read_json(path, fallback):
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.resolve()).encode("utf-8"))
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _remove_sidecars(database_path):
    for suffix in ("-wal", "-shm"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)


def _checkpoint(runtime):
    with closing(runtime.connect()) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _clone_database(repository, temporary_path):
    with closing(repository.runtime.connect()) as source, closing(sqlite3.connect(temporary_path)) as target:
        source.backup(target)


def _validate_items(payload, key_resolver):
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise RuntimeError("订阅条目 JSON 结构无效")
    if any(not isinstance(item, dict) for item in items):
        raise RuntimeError("订阅条目必须是对象")
    keys = [str(key_resolver(item) or "").strip() for item in items]
    if any(not key for key in keys):
        raise RuntimeError("订阅条目缺少稳定 key")
    if len(keys) != len(set(keys)):
        raise RuntimeError("订阅条目存在重复 key")
    return items, keys


def _load_legacy(config_path, items_path, key_resolver):
    config = _read_json(config_path, None)
    payload = _read_json(items_path, EMPTY_SUBSCRIPTION_PAYLOAD)
    if config is not None and not isinstance(config, dict):
        raise RuntimeError("订阅配置 JSON 顶层必须是对象")
    if not isinstance(payload, dict):
        raise RuntimeError("订阅条目 JSON 结构无效")
    items, keys = _validate_items(payload, key_resolver)
    normalized_payload = dict(payload)
    normalized_payload["items"] = list(items)
    return config, normalized_payload, keys


def _backup_legacy(repository, config_path, items_path):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = repository.database_path.parent / "migrations" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in (config_path, items_path):
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def _prepare_report(repository, config_path, items_path, fingerprint, item_count):
    backup_dir = _backup_legacy(repository, config_path, items_path)
    report = {
        "sourceFingerprint": fingerprint,
        "configPresent": config_path.exists(),
        "itemsPresent": items_path.exists(),
        "itemCount": item_count,
        "keysUnique": True,
        "checks": {
            "configPayloadMatch": False,
            "subscriptionPayloadMatch": False,
            "subscriptionKeysMatch": False,
        },
        "status": "verifying",
    }
    return report, backup_dir / "migration-report.json", backup_dir / "migration-report.zh-CN.txt"


def _verify_import(repository, config, payload, keys, key_resolver):
    reloaded = repository.load_payload()
    reloaded_keys = [str(key_resolver(item) or "") for item in reloaded.get("items") or []]
    checks = {
        "configPayloadMatch": _canonical(repository.load_config()) == _canonical(config),
        "subscriptionPayloadMatch": _canonical(reloaded) == _canonical(payload),
        "subscriptionKeysMatch": reloaded_keys == keys,
    }
    failed_checks = [name for name, matched in checks.items() if not matched]
    if failed_checks:
        raise RuntimeError(f"SQLite 迁移后差异检查失败：{','.join(failed_checks)}")
    return checks


def _write_report(report, report_path, report_text_path, succeeded):
    report["status"] = "success" if succeeded else "failed"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fingerprint = report["sourceFingerprint"]
    item_count = report["itemCount"]
    if succeeded:
        text = (
            f"订阅台账迁移成功\n条目数量：{item_count}\n配置逐字段一致：是\n"
            f"订阅逐字段一致：是\n订阅 key 一致：是\n来源指纹：{fingerprint}\n"
        )
    else:
        text = (
            f"订阅台账迁移失败\n条目数量：{item_count}\n失败类型：{report['failureType']}\n"
            f"来源指纹：{fingerprint}\n"
        )
    report_text_path.write_text(text, encoding="utf-8")


def _publish_database(repository, temporary_repository, temporary_path):
    _checkpoint(temporary_repository.runtime)
    _checkpoint(repository.runtime)
    if repository.database_path.exists():
        shutil.copymode(repository.database_path, temporary_path)
    _remove_sidecars(temporary_path)
    _remove_sidecars(repository.database_path)
    os.replace(temporary_path, repository.database_path)


def migrate_legacy_subscription_files(repository, config_path, items_path, key_resolver):
    config_path = Path(config_path)
    items_path = Path(items_path)
    if repository.has_config() or repository.has_items():
        return {"migrated": False, "reason": "sqlite_not_empty"}
    if not config_path.exists() and not items_path.exists():
        return {"migrated": False, "reason": "legacy_files_missing"}

    fingerprint = _fingerprint((config_path, items_path))
    if repository.migration_completed(fingerprint):
        return {"migrated": False, "reason": "already_migrated"}

    config, normalized_payload, keys = _load_legacy(config_path, items_path, key_resolver)
    report, report_path, report_text_path = _prepare_report(
        repository, config_path, items_path, fingerprint, len(keys)
    )
    temporary_path = repository.database_path.with_name(
        f".{repository.database_path.name}.migration-{uuid.uuid4().hex}.tmp"
    )
    try:
        _clone_database(repository, temporary_path)
        temporary_repository = type(repository)(temporary_path)
        temporary_repository.import_legacy(config, normalized_payload, key_resolver)
        report["checks"] = _verify_import(
            temporary_repository, config, normalized_payload, keys, key_resolver
        )
        _write_report(report, report_path, report_text_path, True)
        temporary_repository.record_migration(fingerprint, "success", report_path)
        _publish_database(repository, temporary_repository, temporary_path)
    except Exception as exc:
        report["failureType"] = type(exc).__name__
        _write_report(report, report_path, report_text_path, False)
        repository.record_migration(fingerprint, "failed", report_path)
        raise
    finally:
        temporary_path.unlink(missing_ok=True)
        _remove_sidecars(temporary_path)
    return {"migrated": True, "report": str(report_path), "item_count": len(keys)}
