from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


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

    config = _read_json(config_path, {})
    payload = _read_json(items_path, {"items": [], "last_run_at": "", "stats": {"total": 0, "movie": 0, "tv": 0}})
    if not isinstance(config, dict):
        raise RuntimeError("订阅配置 JSON 顶层必须是对象")
    if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
        raise RuntimeError("订阅条目 JSON 结构无效")

    keys = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            raise RuntimeError("订阅条目必须是对象")
        key = str(key_resolver(item) or "").strip()
        if not key:
            raise RuntimeError("订阅条目缺少稳定 key")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise RuntimeError("订阅条目存在重复 key")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = repository.database_path.parent / "migrations" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    for path in (config_path, items_path):
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)

    report = {
        "sourceFingerprint": fingerprint,
        "configPresent": config_path.exists(),
        "itemsPresent": items_path.exists(),
        "itemCount": len(keys),
        "keysUnique": True,
        "status": "success",
    }
    report_path = backup_dir / "migration-report.json"
    report_text_path = backup_dir / "migration-report.zh-CN.txt"
    try:
        repository.import_legacy(config, payload, key_resolver)
        reloaded = repository.load_payload()
        reloaded_keys = [str(key_resolver(item) or "") for item in reloaded.get("items") or []]
        if reloaded_keys != keys:
            raise RuntimeError("SQLite 迁移后订阅 key 对比失败")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_text_path.write_text(
            f"订阅台账迁移成功\n条目数量：{len(keys)}\n来源指纹：{fingerprint}\n",
            encoding="utf-8",
        )
        repository.record_migration(fingerprint, "success", report_path)
    except Exception:
        repository.record_migration(fingerprint, "failed", report_path)
        raise
    return {"migrated": True, "report": str(report_path), "item_count": len(keys)}
