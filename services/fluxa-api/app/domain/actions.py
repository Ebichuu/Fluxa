from __future__ import annotations

from typing import Any

import requests

from app.adapters.http_utils import sanitize_text
from app.domain.pipeline import build_adapters
from app.storage import FluxaStore


def preview_action_impact(
    *,
    action_type: str,
    payload: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Return explicit impact scope before executing any potentially risky action.

    Never supports blind 'fix all', global restart, or full-library rescan.
    """
    adapters = build_adapters(settings)
    safety = settings.get("safety") or {}

    if action_type == "qb_pause":
        torrent_hash = str(payload.get("hash") or "").strip().lower()
        tasks = adapters["qbittorrent"].list_torrents() if adapters["qbittorrent"].configured else []
        task = next((t for t in tasks if t["hash"] == torrent_hash), None)
        is_seeding = bool(task and task.get("isSeeding"))
        requires_confirm = is_seeding and bool(safety.get("requireConfirmForSeedingActions", True))
        return {
            "actionType": action_type,
            "allowed": bool(torrent_hash),
            "requiresConfirm": requires_confirm,
            "riskLevel": "high" if is_seeding else "low",
            "title": "停止保种确认" if is_seeding else "暂停单个 qB 下载任务",
            "impactSummary": (
                "该任务当前处于保种状态！暂停将停止做种并可能影响 PT 站点保种积分/H&R；不会删除本地源文件。"
                if is_seeding
                else "仅暂停这一个 qB 下载任务，不会删除文件，也不会影响其他任务。"
            ),
            "affectedItems": [
                {
                    "label": "种子名称",
                    "value": (task or {}).get("name") or str(payload.get("name") or torrent_hash),
                },
                {"label": "当前状态", "value": (task or {}).get("statusLabel") or "未知"},
                {"label": "保存路径", "value": (task or {}).get("savePath") or "未知"},
                {"label": "是否删文件", "value": "否（仅暂停，保留源文件）"},
            ],
        }

    if action_type == "qb_resume":
        return {
            "actionType": action_type,
            "allowed": bool(payload.get("hash")),
            "requiresConfirm": False,
            "riskLevel": "low",
            "title": "恢复单个 qB 任务",
            "impactSummary": "仅恢复这一个 qB 任务，不修改路径或规则。",
            "affectedItems": [{"label": "种子名称", "value": str(payload.get("name") or payload.get("hash") or "")}],
        }

    if action_type == "mp_search":
        return {
            "actionType": action_type,
            "allowed": bool(payload.get("subscriptionId")),
            "requiresConfirm": False,
            "riskLevel": "low",
            "title": "触发 MoviePilot Neo 单条订阅搜索",
            "impactSummary": "仅触发该订阅一次搜索，沿用 MoviePilot Neo 现有规则，不触发全库扫描。",
            "affectedItems": [{"label": "订阅", "value": str(payload.get("title") or payload.get("subscriptionId") or "")}],
        }

    if action_type == "torra_retry_upload":
        return {
            "actionType": action_type,
            "allowed": bool(payload.get("taskKey")),
            "requiresConfirm": True,
            "riskLevel": "medium",
            "title": "重试单个 Torra 115 上传任务",
            "impactSummary": "仅重跑该 115 秒传/上传任务，不删除 qB 源文件，也不重启 Torra。",
            "affectedItems": [
                {"label": "文件/任务", "value": str(payload.get("fileName") or payload.get("taskKey") or "")},
                {"label": "影响范围", "value": "仅此单条上传任务"},
            ],
        }

    raise ValueError(f"不支持的操作类型：{action_type}")


def execute_bounded_action(
    *,
    action_type: str,
    payload: dict[str, Any],
    confirmed: bool,
    settings: dict[str, Any],
    store: FluxaStore,
) -> dict[str, Any]:
    preview = preview_action_impact(action_type=action_type, payload=payload, settings=settings)
    if not preview["allowed"]:
        raise ValueError("缺少必要的任务标识，无法执行操作")
    if preview["requiresConfirm"] and not confirmed:
        return {"ok": False, "requiresConfirm": True, "preview": preview}

    adapters = build_adapters(settings)
    media_key = str(payload.get("mediaKey") or "")

    try:
        if action_type == "qb_pause":
            res = adapters["qbittorrent"].pause_or_resume(str(payload["hash"]), "pause")
            target_service, target_id = "qbittorrent", str(payload["hash"])
        elif action_type == "qb_resume":
            res = adapters["qbittorrent"].pause_or_resume(str(payload["hash"]), "resume")
            target_service, target_id = "qbittorrent", str(payload["hash"])
        elif action_type == "mp_search":
            res = adapters["moviepilot"].trigger_subscribe_search(str(payload["subscriptionId"]))
            target_service, target_id = "moviepilot", str(payload["subscriptionId"])
        elif action_type == "torra_retry_upload":
            res = adapters["torra"].retry_secupload_task(
                task_key=str(payload["taskKey"]),
                target_item_id=str(payload.get("targetItemId") or ""),
            )
            target_service, target_id = "torra", str(payload["taskKey"])
        else:
            raise ValueError(f"不支持的操作类型：{action_type}")

        store.add_audit(
            action_type=action_type,
            target_service=target_service,
            target_id=target_id,
            media_key=media_key,
            summary=res.get("message") or preview["title"],
            status="success" if res.get("ok") else "error",
        )
        return {"ok": bool(res.get("ok", True)), "message": res.get("message") or "操作已执行"}
    except Exception as exc:
        msg = sanitize_text(exc)
        store.add_audit(
            action_type=action_type,
            target_service=action_type.split("_")[0],
            target_id=str(payload.get("hash") or payload.get("subscriptionId") or payload.get("taskKey") or ""),
            media_key=media_key,
            summary=preview["title"],
            status="error",
            error_text=msg,
        )
        raise RuntimeError(msg) from exc


def send_telegram_test(settings: dict[str, Any]) -> dict[str, Any]:
    tg = (settings.get("services") or {}).get("telegram") or {}
    token = str(tg.get("botToken") or "").strip()
    chat_id = str(tg.get("chatId") or "").strip()
    if not token or not chat_id:
        raise ValueError("请先填写 Telegram Bot Token 和 Chat ID")
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "✅ Fluxa v2 Telegram 通知连通测试成功"},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Telegram 推送失败 ({resp.status_code})")
    return {"ok": True, "message": "测试消息已发送到 Telegram"}
