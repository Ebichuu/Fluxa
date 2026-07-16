from __future__ import annotations

import re

from flask import Flask, jsonify, request

from app.activity_log import write_activity


HASH_PATTERN = re.compile(r"^[a-f0-9]{40}$", re.IGNORECASE)
MAX_HASHES = 20


class QbittorrentActionError(RuntimeError):
    def __init__(self, message: str, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


def validate_action_hashes(value) -> list[str]:
    if not isinstance(value, list) or not value:
        raise QbittorrentActionError(
            "至少需要一个 qBittorrent 任务 hash",
            400,
            "QB_HASHES_REQUIRED",
        )
    hashes = []
    for raw_hash in value:
        normalized = raw_hash.strip().lower() if isinstance(raw_hash, str) else ""
        if normalized not in hashes:
            hashes.append(normalized)
    if len(hashes) > MAX_HASHES:
        raise QbittorrentActionError(
            f"一次最多操作 {MAX_HASHES} 个 qBittorrent 任务",
            400,
            "QB_HASH_LIMIT_EXCEEDED",
        )
    if any(not HASH_PATTERN.fullmatch(hash_value) for hash_value in hashes):
        raise QbittorrentActionError(
            "qBittorrent 任务 hash 格式无效",
            400,
            "QB_HASH_INVALID",
        )
    return hashes


def _is_paused(task: dict) -> bool:
    return "pause" in str(task.get("state") or "").lower()


def _safe_title(value) -> str:
    title = value.strip() if isinstance(value, str) else ""
    return title[:120] or "未命名媒体任务"


class QbittorrentActionService:
    def __init__(self, client, activity_writer=None):
        self.client = client
        self.activity_writer = activity_writer or write_activity

    def _activity(self, action: str, status: str, message: str):
        self.activity_writer("qbittorrent", action, status, message)

    def execute(self, action: str, input_data: dict) -> dict:
        if action not in {"pause", "resume"}:
            raise QbittorrentActionError(
                "不支持的 qBittorrent 动作",
                400,
                "QB_ACTION_INVALID",
            )
        hashes = validate_action_hashes(input_data.get("hashes"))
        title = _safe_title(input_data.get("title"))
        before = self.client.summary()
        if not before.get("configured"):
            raise QbittorrentActionError(
                "qBittorrent 尚未配置",
                503,
                "QB_NOT_CONFIGURED",
            )
        if not before.get("connected"):
            raise QbittorrentActionError(
                before.get("error") or "qBittorrent 当前不可用",
                503,
                "QB_UNAVAILABLE",
            )

        before_by_hash = {
            str(task.get("hash") or "").lower(): task
            for task in before.get("tasks") or []
        }
        missing = [hash_value for hash_value in hashes if hash_value not in before_by_hash]
        if missing:
            raise QbittorrentActionError(
                "部分 qBittorrent 任务已不存在，请刷新任务链后重试",
                404,
                "QB_TASK_NOT_FOUND",
            )

        eligible = [
            hash_value
            for hash_value in hashes
            if (_is_paused(before_by_hash[hash_value])) == (action == "resume")
        ]
        skipped = [hash_value for hash_value in hashes if hash_value not in eligible]
        if not eligible:
            self._activity(
                action,
                "skip",
                f"{title}：{len(hashes)} 个任务已处于目标状态",
            )
            return {
                "action": action,
                "requested": len(hashes),
                "submitted": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": len(skipped),
                "confirmed": True,
                "tasks": [
                    {
                        "hash": hash_value,
                        "status": before_by_hash[hash_value].get("status") or "",
                        "state": before_by_hash[hash_value].get("state") or "",
                        "outcome": "skipped",
                    }
                    for hash_value in skipped
                ],
            }

        verb = "暂停" if action == "pause" else "恢复"
        short_hashes = "、".join(hash_value[:8] for hash_value in eligible)
        self._activity(
            action,
            "start",
            f"{title}：准备{verb} {len(eligible)} 个任务（{short_hashes}）",
        )
        try:
            self.client.set_paused(action, eligible)
        except Exception as exc:
            message = str(exc) or "qBittorrent 动作提交失败"
            self._activity(action, "error", f"{title}：{message}")
            raise QbittorrentActionError(message, 502, "QB_ACTION_FAILED") from exc

        after = self.client.summary()
        if not after.get("connected"):
            message = f"{title}：动作已提交，但无法确认最新状态"
            self._activity(action, "error", message)
            return {
                "action": action,
                "requested": len(hashes),
                "submitted": len(eligible),
                "succeeded": 0,
                "failed": len(eligible),
                "skipped": len(skipped),
                "confirmed": False,
                "tasks": [
                    {
                        "hash": hash_value,
                        "status": before_by_hash[hash_value].get("status") or "",
                        "state": before_by_hash[hash_value].get("state") or "",
                        "outcome": "skipped" if hash_value in skipped else "failed",
                    }
                    for hash_value in hashes
                ],
            }

        after_by_hash = {
            str(task.get("hash") or "").lower(): task
            for task in after.get("tasks") or []
        }
        tasks = []
        for hash_value in hashes:
            current = after_by_hash.get(hash_value) or before_by_hash[hash_value]
            if hash_value in skipped:
                outcome = "skipped"
            else:
                reached_target = _is_paused(current) if action == "pause" else not _is_paused(current)
                outcome = "success" if reached_target else "failed"
            tasks.append({
                "hash": hash_value,
                "status": current.get("status") or "",
                "state": current.get("state") or "",
                "outcome": outcome,
            })
        succeeded = sum(task["outcome"] == "success" for task in tasks)
        failed = sum(task["outcome"] == "failed" for task in tasks)
        confirmed = failed == 0
        self._activity(
            action,
            "success" if confirmed else "error",
            f"{title}：成功 {succeeded}，跳过 {len(skipped)}，失败 {failed}",
        )
        return {
            "action": action,
            "requested": len(hashes),
            "submitted": len(eligible),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": len(skipped),
            "confirmed": confirmed,
            "tasks": tasks,
        }


def register_qbittorrent_actions(app: Flask, client, activity_writer=None):
    service = QbittorrentActionService(client, activity_writer=activity_writer)
    app.extensions["mcc_qbittorrent_actions"] = service

    for action in ("pause", "resume"):
        endpoint = f"qbittorrent_{action}"

        def action_route(selected_action=action):
            try:
                result = service.execute(
                    selected_action,
                    request.get_json(silent=True) or {},
                )
                return jsonify(result), 200 if result["confirmed"] else 202
            except QbittorrentActionError as exc:
                return jsonify({"code": exc.code, "error": str(exc)}), exc.status

        app.add_url_rule(
            f"/api/qbittorrent/actions/{action}",
            endpoint,
            action_route,
            methods=["POST"],
        )
    return service
