from __future__ import annotations

import importlib
import importlib.machinery
import json
import os
import re
import shutil
import sys
import time
import hashlib
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

from app import discover_runtime
from app.activity_log import write_activity
from app.config import DATA_DIR, load_runtime_env, read_config


LEGACY_DIR = Path(__file__).resolve().parent / "legacy"
if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))
HDHIVE_DIR = Path(__file__).resolve().parent / "hdhive"
HDHIVE_UNLOCK_RATE_PATH = DATA_DIR / "hdhive_unlock_rate.json"


def _emby_join(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _emby_auth_header(token: str = "") -> dict[str, str]:
    header = 'MediaBrowser Client="NasEmby", Device="NAS2", DeviceId="nasemby", Version="1.0"'
    headers = {"X-Emby-Authorization": header}
    if token:
        headers["X-Emby-Token"] = token
    return headers


def _emby_request(method: str, base_url: str, path: str, **kwargs):
    url = _emby_join(base_url, path)
    response = requests.request(method, url, timeout=15, **kwargs)
    if response.status_code == 404 and not path.lstrip("/").startswith("emby/"):
        response = requests.request(method, _emby_join(base_url, f"/emby/{path.lstrip('/')}"), timeout=15, **kwargs)
    response.raise_for_status()
    return response


def _emby_connection(payload: dict[str, object] | None = None) -> tuple[str, str, str]:
    payload = payload or {}
    cfg = read_config()
    base_url = str(payload.get("server_url") or cfg.get("ENV_EMBY_SERVER_URL") or "").strip()
    api_key = str(payload.get("api_key") or cfg.get("ENV_EMBY_API_KEY") or "").strip()
    username = str(payload.get("username") or cfg.get("ENV_MEDIA_LIBRARY_ADMIN") or "").strip()
    password = str(payload.get("password") or cfg.get("ENV_MEDIA_LIBRARY_PASSWORD") or "").strip()
    if not base_url:
        raise ValueError("请先填写 Emby 服务器地址")

    token = api_key
    auth_method = "api_key" if token else ""
    if not token:
        if not username or not password:
            raise ValueError("请填写 API Key，或填写管理员账户和密码")
        auth = _emby_request(
            "POST",
            base_url,
            "/Users/AuthenticateByName",
            headers={**_emby_auth_header(), "Content-Type": "application/json"},
            json={"Username": username, "Pw": password},
        ).json()
        token = str(auth.get("AccessToken") or "").strip()
        if not token:
            raise RuntimeError("Emby 登录成功但没有返回 AccessToken")
        auth_method = "password"
    return base_url, token, auth_method


def fetch_emby_libraries(payload: dict[str, object] | None = None) -> dict[str, object]:
    base_url, token, auth_method = _emby_connection(payload)
    data = _emby_request("GET", base_url, "/Library/VirtualFolders", headers=_emby_auth_header(token)).json()
    rows = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("name") or "").strip()
        value = str(item.get("ItemId") or item.get("Id") or item.get("Guid") or name).strip()
        if name and value:
            rows.append({
                "id": value,
                "name": name,
                "type": item.get("CollectionType") or "",
                "poster_url": f"/api/emby/library-image/{quote(value, safe='')}",
            })
    return {"ok": True, "auth_method": auth_method, "libraries": rows}


def fetch_emby_library_image(item_id: str) -> tuple[bytes, str]:
    library_id = str(item_id or "").strip()
    if not library_id or "/" in library_id or "\\" in library_id:
        raise ValueError("无效的媒体库 ID")
    base_url, token, _ = _emby_connection({})
    last_error: Exception | None = None
    for image_type in ("Primary", "Thumb", "Backdrop"):
        try:
            response = _emby_request(
                "GET",
                base_url,
                f"/Items/{quote(library_id, safe='')}/Images/{image_type}?fillHeight=360&quality=90",
                headers=_emby_auth_header(token),
            )
            content_type = response.headers.get("Content-Type") or "image/jpeg"
            return response.content, content_type
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(str(last_error or "未获取到媒体库海报"))


def _reload_legacy_module(name: str):
    load_runtime_env()
    old_cwd = Path.cwd()
    os.chdir(Path(__file__).resolve().parents[1])
    try:
        module = importlib.import_module(name)
        return importlib.reload(module)
    finally:
        os.chdir(old_cwd)


def check_115_account() -> dict[str, object]:
    module = _reload_legacy_module("tgto115")
    client = module.init_115_client()
    info = client.user_info()
    return {"ok": True, "user": info}


def extract_115_links(text: str) -> list[str]:
    pattern = r"https?:\/\/(?:115|115cdn|anxia)\.com\/s\/\w+\?password\=\w+"
    return sorted(set(match.strip() for match in re.findall(pattern, text or "", re.IGNORECASE | re.DOTALL)))


def _split_notify_values(value: object) -> list[str]:
    rows = re.split(r"[\n,，;；|]+|\s{2,}", str(value or ""))
    return [item.strip() for item in rows if item and item.strip()]


def _split_notify_chat_ids(value: object) -> list[str]:
    rows = re.split(r"[\s,，;；|]+", str(value or ""))
    return [item.strip() for item in rows if item and item.strip()]


def _default_transfer_template() -> list[dict[str, object]]:
    return [
        {"key": "poster", "label": "海报", "icon": "🖼️", "sample": "TMDB横版背景图", "enabled": True},
        {"key": "title", "label": "标题", "icon": "📺", "sample": "电视剧：绝命毒师 (2008) S01E01-E07", "enabled": True},
        {"key": "entry", "label": "入库", "icon": "📥", "sample": "转存入库: S01E01", "enabled": True},
        {"key": "id", "label": "ID", "icon": "🍿", "sample": "TMDB ID: 1396", "enabled": True},
        {"key": "rating", "label": "评分", "icon": "⭐", "sample": "评分: 8.9", "enabled": True},
        {"key": "genre", "label": "题材", "icon": "🎭", "sample": "题材: 惊悚犯罪", "enabled": True},
        {"key": "region", "label": "地区", "icon": "📂", "sample": "地区: 美国", "enabled": True},
        {"key": "quality", "label": "质量", "icon": "🎞️", "sample": "质量: [4K] [DV&HDR] [DTS] [MKV]", "enabled": True},
        {"key": "size", "label": "大小", "icon": "💾", "sample": "大小: 14.44 GB", "enabled": True},
        {"key": "trigger", "label": "触发", "icon": "🎯", "sample": "触发: 手动订阅: /自动分类/美剧", "enabled": True},
        {"key": "channel", "label": "频道", "icon": "📢", "sample": "频道: 爱影频道", "enabled": True},
        {"key": "link", "label": "链接", "icon": "🔗", "sample": "链接: https://115.com/...", "enabled": True},
        {"key": "plot", "label": "剧情", "icon": "📝", "sample": "剧情简介: 一段简介文本...", "enabled": True},
    ]


def _template_rows(config_value: object, default_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    try:
        parsed = json.loads(str(config_value or ""))
    except Exception:
        parsed = []
    saved = {str(item.get("key") or ""): item for item in parsed if isinstance(item, dict)} if isinstance(parsed, list) else {}
    rows = []
    for default in default_rows:
        key = str(default.get("key") or "")
        merged = dict(default)
        if key in saved:
            merged.update({k: v for k, v in saved[key].items() if k in {"enabled", "label", "icon", "sample"}})
        rows.append(merged)
    return rows


def _first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _label_value(label: str, value: object) -> str:
    text = str(value or "").strip()
    return f"{label}: {text}" if text else ""


def _media_tag_text(item: dict[str, object]) -> str:
    parts = []
    for key in (
        "frame_rate",
        "audio_codec",
        "resolution",
        "video_codec",
        "dolby_vision",
        "dynamic_range",
        "resource_medium",
        "release_method",
        "streaming_platform",
        "file_extension",
    ):
        value = item.get(key)
        if isinstance(value, list):
            parts.extend(str(v).strip() for v in value if str(v or "").strip())
        elif str(value or "").strip():
            parts.append(str(value).strip())
    enhancement = item.get("enhancement_tags")
    if isinstance(enhancement, list):
        parts.extend(str(v).strip() for v in enhancement if str(v or "").strip())
    seen = set()
    rows = []
    for part in parts:
        token = re.sub(r"[\W_]+", "", part.lower())
        if token and token not in seen:
            seen.add(token)
            rows.append(part)
    return " ".join(f"[{part}]" for part in rows)


def _extract_line_value(text: str, *names: str) -> str:
    for name in names:
        match = re.search(rf"(?m)^[^\w\u4e00-\u9fff]*{re.escape(name)}\s*[：:]\s*(.+)$", text or "")
        if match:
            return match.group(1).strip()
    return ""


def _transfer_notify_context(
    share_url: str,
    target_pid: object,
    result: dict[str, object] | None = None,
    item: dict[str, object] | None = None,
    source: str = "",
) -> dict[str, str]:
    item = item or {}
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    full_text = str(item.get("full_text") or raw.get("text") or raw.get("note") or "").strip()
    title = _first_text(item.get("title"), item.get("name"), raw.get("title"), raw.get("name"), "115 分享转存")
    media_type = str(item.get("media_type") or item.get("type") or raw.get("media_type") or "").strip()
    title_prefix = "电视剧" if media_type in {"tv", "series", "电视剧"} else ("电影" if media_type in {"movie", "电影"} else "资源")
    tmdb_id = _first_text(item.get("tmdb_id"), raw.get("tmdb_id"), _extract_line_value(full_text, "TMDB ID"))
    quality = _media_tag_text(item) or _extract_line_value(full_text, "质量", "画质")
    size = _first_text(item.get("size"), raw.get("share_size"), _extract_line_value(full_text, "大小", "总大小"))
    target_value = str(target_pid or (result or {}).get("target_pid") or "-")
    rating = _first_text(item.get("rating"), raw.get("rating"), _extract_line_value(full_text, "评分"))
    genre = _first_text(item.get("genre"), item.get("genres"), raw.get("genre"), _extract_line_value(full_text, "题材"))
    region = _first_text(item.get("region"), item.get("country"), raw.get("region"), _extract_line_value(full_text, "地区"))
    trigger = _first_text(item.get("trigger"), item.get("source_label"), source, _extract_line_value(full_text, "触发"), "手动转存")
    channel = _first_text(item.get("channel"), item.get("source_label"), raw.get("channel"), _extract_line_value(full_text, "频道"))
    plot = _first_text(item.get("overview"), item.get("plot"), raw.get("overview"), _extract_line_value(full_text, "剧情简介"))
    return {
        "poster": _first_text(item.get("backdrop_url"), item.get("poster_url"), raw.get("backdrop_url"), raw.get("poster_url")),
        "title": f"{title_prefix}：{title}",
        "entry": f"转存入库: {target_value}",
        "id": f"TMDB ID: {tmdb_id}" if tmdb_id else "",
        "rating": _label_value("评分", rating),
        "genre": _label_value("题材", genre),
        "region": _label_value("地区", region),
        "quality": f"质量: {quality}" if quality else "",
        "size": f"大小: {size}" if size else "",
        "trigger": _label_value("触发", trigger),
        "channel": _label_value("频道", channel),
        "link": f"链接: {share_url}",
        "plot": _label_value("剧情简介", plot),
        "signature": "- Powered by NasEmby",
        "filter_text": " ".join(str(v or "") for v in [title, share_url, full_text, item.get("source_label"), source]),
    }


def _render_transfer_notify_message(context: dict[str, str]) -> str:
    cfg = read_config()
    rows = _template_rows(cfg.get("ENV_TG_TRANSFER_NOTIFY_TEMPLATE"), _default_transfer_template())
    lines = []
    for row in rows:
        if not _truthy(row.get("enabled")):
            continue
        key = str(row.get("key") or "")
        value = str(context.get(key) or "").strip()
        if not value:
            continue
        if key == "poster":
            continue
        icon = str(row.get("icon") or "").strip()
        lines.append(f"{icon} {value}".strip())
    return "\n".join(lines).strip()


def _notify_filter_allows(context: dict[str, str]) -> tuple[bool, str]:
    cfg = read_config()
    haystack = str(context.get("filter_text") or "").lower()
    whitelist = [item.lower() for item in _split_notify_values(cfg.get("ENV_TG_TRANSFER_NOTIFY_WHITELIST"))]
    blacklist = [item.lower() for item in _split_notify_values(cfg.get("ENV_TG_TRANSFER_NOTIFY_BLACKLIST"))]
    if blacklist and any(item in haystack for item in blacklist):
        return False, "blacklist"
    if whitelist and not any(item in haystack for item in whitelist):
        return False, "whitelist"
    return True, ""


def _send_telegram_bot_message(message: str, context: dict[str, str]) -> dict[str, object]:
    cfg = read_config()
    token = str(cfg.get("ENV_TG_BOT_TOKEN") or "").strip()
    chat_ids = _split_notify_chat_ids(cfg.get("ENV_TG_TRANSFER_NOTIFY_CHAT_IDS")) or _split_notify_chat_ids(cfg.get("ENV_TG_ADMIN_USER_ID"))
    if not token:
        return {"ok": False, "skipped": "未配置 Bot Token"}
    if not chat_ids:
        return {"ok": False, "skipped": "未配置 TG ID"}
    allowed, reason = _notify_filter_allows(context)
    if not allowed:
        return {"ok": True, "skipped": f"过滤规则跳过：{reason}"}
    if not message:
        return {"ok": False, "skipped": "通知内容为空"}
    sent = 0
    errors = []
    for chat_id in chat_ids:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "disable_web_page_preview": False},
                timeout=12,
            )
            if response.ok and response.json().get("ok"):
                sent += 1
            else:
                errors.append(f"{chat_id}: {response.text[:160]}")
        except Exception as exc:
            errors.append(f"{chat_id}: {exc}")
    return {"ok": sent > 0, "sent": sent, "errors": errors}


def _notify_transfer_success(share_url: str, target_pid: object, result: dict[str, object], item: dict[str, object] | None = None, source: str = "") -> None:
    cfg = read_config()
    if not _truthy(cfg.get("ENV_TG_TRANSFER_NOTIFY_ENABLED")):
        return
    context = _transfer_notify_context(share_url, target_pid, result, item, source)
    message = _render_transfer_notify_message(context)
    outcome = _send_telegram_bot_message(message, context)
    write_activity(
        "transfer",
        "telegram_transfer_notify",
        "success" if outcome.get("ok") else "error",
        "TG 转存通知已发送" if outcome.get("ok") and not outcome.get("skipped") else str(outcome.get("skipped") or "TG 转存通知发送失败"),
        title=(item or {}).get("title") or "",
        share_url=share_url,
        sent=outcome.get("sent") or 0,
        errors="; ".join(outcome.get("errors") or [])[:240],
    )


def transfer_115_share(share_url: str, target_pid: str | int | None = None, notify: bool = True, notify_context: dict[str, object] | None = None) -> dict[str, object]:
    target = target_pid or os.getenv("ENV_UPLOAD_PID") or os.getenv("ENV_115_LINK_UPLOAD_PID") or os.getenv("ENV_115_UPLOAD_PID") or "0"
    write_activity("transfer", "115_transfer", "start", "开始转存 115 分享", share_url=share_url, target_pid=str(target))
    try:
        module = _reload_legacy_module("tgto115")
        client = module.init_115_client()
        ok = bool(module.transfer_shared_link(client, share_url, target))
        write_activity("transfer", "115_transfer", "success" if ok else "error", "115 转存完成" if ok else "115 转存失败", share_url=share_url, target_pid=str(target))
        result = {"ok": ok, "target_pid": str(target)}
        if ok and notify:
            _notify_transfer_success(share_url, str(target), result, notify_context or {}, "115 分享")
        return result
    except Exception as exc:
        write_activity("transfer", "115_transfer", "error", f"115 转存异常：{exc}", share_url=share_url, target_pid=str(target))
        raise


def _is_115_link(url: str) -> bool:
    return bool(re.search(r"https?://(?:115|115cdn|anxia)\.com/s/\w+\?password=\w+", url or "", re.I))


def _load_hdhive_unlock_module():
    if str(HDHIVE_DIR) not in sys.path:
        sys.path.insert(0, str(HDHIVE_DIR))
    path = HDHIVE_DIR / "hdhive_unlock.pyc"
    if not path.exists():
        raise RuntimeError("缺少影巢解锁模块 hdhive_unlock.pyc")
    return importlib.machinery.SourcelessFileLoader("hdhive_unlock", str(path)).load_module()


def _hdhive_int_config(key: str, default: int) -> int:
    cfg = read_config()
    try:
        return int(str(cfg.get(key, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _hdhive_item_points(item: dict[str, object]) -> int:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    for key in ("points", "unlock_points", "required_points", "cost", "price"):
        value = item.get(key, raw.get(key))
        if value in (None, ""):
            continue
        match = re.search(r"\d+", str(value))
        if match:
            return int(match.group(0))
    return 0


def _read_hdhive_unlock_timestamps() -> list[float]:
    try:
        if HDHIVE_UNLOCK_RATE_PATH.exists():
            data = json.loads(HDHIVE_UNLOCK_RATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [float(item) for item in data if isinstance(item, (int, float, str))]
    except Exception:
        pass
    return []


def _write_hdhive_unlock_timestamps(timestamps: list[float]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HDHIVE_UNLOCK_RATE_PATH.write_text(json.dumps(timestamps), encoding="utf-8")


def _check_hdhive_unlock_policy(item: dict[str, object]) -> None:
    points = _hdhive_item_points(item)
    points_limit = _hdhive_int_config("ENV_HDHIVE_UNLOCK_POINTS_LIMIT", 100)
    if points_limit > 0 and points > points_limit:
        raise ValueError(f"影巢资源需要 {points} 积分，超过当前解锁积分上限 {points_limit}")

    rate_limit = _hdhive_int_config("ENV_HDHIVE_UNLOCK_RATE_LIMIT", 3)
    if rate_limit <= 0:
        return
    now = time.time()
    recent = [ts for ts in _read_hdhive_unlock_timestamps() if now - ts < 60]
    if len(recent) >= rate_limit:
        raise RuntimeError(f"影巢解锁过于频繁，当前限制为每分钟 {rate_limit} 次")


def _record_hdhive_unlock() -> None:
    now = time.time()
    recent = [ts for ts in _read_hdhive_unlock_timestamps() if now - ts < 60]
    recent.append(now)
    _write_hdhive_unlock_timestamps(recent)


def _hdhive_resource_slug(item: dict[str, object]) -> str:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    slug = str(raw.get("slug") or item.get("slug") or "").strip()
    if slug:
        return slug
    for value in (item.get("url"), item.get("preview_url"), raw.get("url"), raw.get("media_url")):
        text = str(value or "")
        match = re.search(r"/resource/([a-f0-9]{16,64})", text, re.I)
        if match:
            return match.group(1)
    return ""


def unlock_hdhive_share(item: dict[str, object]) -> dict[str, object]:
    slug = _hdhive_resource_slug(item)
    if not slug:
        raise ValueError("缺少影巢资源标识，无法解锁")
    mod = _load_hdhive_unlock_module()
    client = mod.HDHiveUserAPIClient()
    result = client.unlock_resource(slug)
    data = result.get("data") if isinstance(result, dict) else {}
    share_url = ""
    if isinstance(data, dict):
        share_url = str(data.get("full_url") or data.get("url") or "").strip()
    if not _is_115_link(share_url):
        raise ValueError(result.get("message") if isinstance(result, dict) else "影巢解锁后没有返回可转存的 115 链接")
    return {
        "ok": True,
        "slug": slug,
        "share_url": share_url,
        "unlock_result": result,
    }


def search_yingchao_resources(title: str, media_type: str = "tv", tmdb_id: str = "") -> dict[str, object]:
    if not title.strip():
        raise ValueError("缺少搜索标题")
    params = {
        "title": title.strip(),
        "type": "tv" if media_type in ("tv", "电视剧") else "movie",
    }
    if tmdb_id:
        params["tmdb_id"] = str(tmdb_id).strip()
    data = discover_runtime.search_resources(params)
    if not data.get("success"):
        raise RuntimeError(data.get("error") or "影巢搜索失败")
    rows = []
    for item in data.get("items") or []:
        if item.get("source_key") != "hdhive":
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        share_url = raw.get("share_url") or item.get("preview_url") or item.get("url") or ""
        rows.append({
            **item,
            "share_url": share_url,
            "can_transfer": _is_115_link(share_url),
        })
    return {
        "ok": True,
        "title": title,
        "type": params["type"],
        "items": rows,
        "count": len(rows),
        "source": "影巢",
    }


def transfer_yingchao_item(item: dict[str, object], target_pid: str | int | None = None) -> dict[str, object]:
    title = str(item.get("title") or item.get("name") or "").strip()
    write_activity("transfer", "yingchao_transfer", "start", "开始影巢转存", title=title)
    try:
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        share_url = str(item.get("share_url") or raw.get("share_url") or item.get("preview_url") or item.get("url") or "").strip()
        unlocked_flag = False
        if not _is_115_link(share_url):
            write_activity("transfer", "yingchao_unlock", "start", "开始解锁影巢资源", title=title)
            _check_hdhive_unlock_policy(item)
            unlocked = unlock_hdhive_share(item)
            _record_hdhive_unlock()
            share_url = str(unlocked.get("share_url") or "").strip()
            unlocked_flag = True
            write_activity("transfer", "yingchao_unlock", "success", "影巢资源解锁成功", title=title, share_url=share_url)
        result = transfer_115_share(share_url, target_pid, notify=False, notify_context=item)
        result["share_url"] = share_url
        result["title"] = title
        result["unlocked"] = unlocked_flag
        write_activity("transfer", "yingchao_transfer", "success" if result.get("ok") else "error", "影巢转存完成" if result.get("ok") else "影巢转存失败", title=title, share_url=share_url)
        if result.get("ok"):
            _notify_transfer_success(share_url, result.get("target_pid") or target_pid or "", result, item, "影巢")
        return result
    except Exception as exc:
        write_activity("transfer", "yingchao_transfer", "error", f"影巢转存异常：{exc}", title=title)
        raise


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _moviepilot_config() -> dict[str, object]:
    cfg = read_config()
    return {
        "url": str(cfg.get("ENV_MOVIEPILOT_URL") or "").strip().rstrip("/"),
        "token": str(cfg.get("ENV_MOVIEPILOT_API_TOKEN") or "").strip(),
        "username": str(cfg.get("ENV_MOVIEPILOT_USERNAME") or "NasEmby").strip() or "NasEmby",
        "auto_subscribe": _truthy(cfg.get("ENV_MOVIEPILOT_AUTO_SUBSCRIBE")),
    }


def _moviepilot_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def moviepilot_status() -> dict[str, object]:
    cfg = _moviepilot_config()
    base_url = str(cfg["url"])
    token = str(cfg["token"])
    if not base_url or not token:
        return {
            "ok": True,
            "configured": False,
            "message": "未配置 MoviePilot 地址或 API Token",
            "auto_subscribe": bool(cfg["auto_subscribe"]),
        }
    response = requests.get(
        _moviepilot_url(base_url, "/api/v1/subscribe/list"),
        params={"token": token},
        timeout=12,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        data = []
    return {
        "ok": True,
        "configured": True,
        "message": "MoviePilot 连接正常",
        "subscribe_count": len(data) if isinstance(data, list) else None,
        "auto_subscribe": bool(cfg["auto_subscribe"]),
    }


def _moviepilot_media_type(item: dict[str, object]) -> str:
    media_type = str(item.get("media_type") or item.get("type") or "").strip().lower()
    if media_type in {"tv", "series"} or "剧" in media_type:
        return "tv"
    return "movie"


def _moviepilot_year(item: dict[str, object]) -> str:
    for key in ("year", "date", "air_date", "release_date", "first_air_date"):
        match = re.search(r"(?:19|20)\d{2}", str(item.get(key) or ""))
        if match:
            return match.group(0)
    return ""


def _moviepilot_tmdb_id(item: dict[str, object], media_type: str, year: str) -> int:
    explicit = item.get("tmdb_id") or item.get("tmdbid")
    if explicit and str(explicit).isdigit():
        return int(explicit)
    if str(item.get("source") or "").lower() == "tmdb" and str(item.get("id") or "").isdigit():
        return int(item["id"])
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        return 0
    candidates = discover_runtime.search_tmdb_candidates(title, media_type, year)
    for candidate_type, candidate_id in candidates:
        if candidate_type == media_type:
            return int(candidate_id)
    return int(candidates[0][1]) if candidates else 0


def _push_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value or "").strip() or default)
    except Exception:
        return default


def _push_compact(value: object) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").strip().lower())


def _moviepilot_type(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"tv", "series", "show", "电视剧", "剧集"} or "tv" in raw or "剧" in raw:
        return "tv"
    return "movie"


def _moviepilot_list_subscribes(cfg: dict[str, object]) -> list[dict[str, object]]:
    response = requests.get(
        _moviepilot_url(str(cfg["url"]), "/api/v1/subscribe/list"),
        params={"token": str(cfg["token"])},
        headers={"Authorization": str(cfg["token"])},
        timeout=15,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [row for row in data.get("data") if isinstance(row, dict)]
    return []


def _moviepilot_subscribe_matches(row: dict[str, object], media_type: str, tmdb_id: int, seasons: list[int], title: str, year: str) -> bool:
    row_tmdb_id = _push_int(row.get("tmdbid") or row.get("tmdb_id") or row.get("tmdbId"))
    row_type = _moviepilot_type(row.get("type") or row.get("media_type") or row.get("mtype"))
    if tmdb_id and row_tmdb_id:
        if row_tmdb_id != tmdb_id:
            return False
        if row_type != media_type:
            return False
        if media_type == "tv":
            row_season = _push_int(row.get("season") or row.get("season_number"), -1)
            return row_season <= 0 or not seasons or row_season in seasons
        return True

    row_title = _push_compact(row.get("name") or row.get("title"))
    if not row_title or row_title != _push_compact(title):
        return False
    row_year = str(row.get("year") or row.get("date") or "").strip()
    if year and row_year and year != row_year:
        return False
    return row_type == media_type


def _moviepilot_find_subscribe(cfg: dict[str, object], media_type: str, tmdb_id: int, seasons: list[int], title: str, year: str) -> dict[str, object] | None:
    for row in _moviepilot_list_subscribes(cfg):
        if _moviepilot_subscribe_matches(row, media_type, tmdb_id, seasons, title, year):
            return row
    return None


def _extract_moviepilot_subscribe_id(value: object) -> int:
    if isinstance(value, dict):
        for key in ("id", "subscribe_id", "subscribeId", "subscription_id", "subscriptionId"):
            found = _push_int(value.get(key))
            if found:
                return found
        for key in ("data", "subscribe", "subscription", "result"):
            found = _extract_moviepilot_subscribe_id(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _extract_moviepilot_subscribe_id(item)
            if found:
                return found
    return 0


def _moviepilot_already_subscribed(result: object, message: str) -> bool:
    blob = f"{message} {json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else result}".lower()
    return any(token in blob for token in ("已存在", "已经存在", "已订阅", "重复", "already", "exist"))


def _moviepilot_trigger_subscribe_search(
    cfg: dict[str, object],
    subscribe_id: int,
    title: str,
    reason: str = "",
    safe_audit: bool = False,
) -> dict[str, object]:
    if not subscribe_id:
        return {"ok": False, "message": "未找到 MoviePilot 订阅 ID"}
    response = requests.get(
        _moviepilot_url(str(cfg["url"]), f"/api/v1/subscribe/search/{subscribe_id}"),
        params={"token": str(cfg["token"])},
        headers={"Authorization": str(cfg["token"])},
        timeout=30,
    )
    response.raise_for_status()
    try:
        result = response.json()
    except Exception:
        result = {"success": True, "message": response.text[:200]}
    success = bool(result.get("success", True)) if isinstance(result, dict) else True
    message = str(result.get("message") or ("MoviePilot 搜索已触发" if success else "MoviePilot 搜索触发失败")) if isinstance(result, dict) else "MoviePilot 搜索已触发"
    audit_meta = {"title": title, "reason": reason}
    if not safe_audit:
        audit_meta["subscribe_id"] = subscribe_id
    write_activity(
        "push",
        "moviepilot_subscribe_search",
        "success" if success else "error",
        ("MoviePilot 搜索已触发" if success else "MoviePilot 搜索触发失败") if safe_audit else message,
        **audit_meta,
    )
    return {
        "ok": success,
        "message": message,
        "subscribe_id": subscribe_id,
        "moviepilot_response": result,
    }


def _moviepilot_seasons(item: dict[str, object], payload: dict[str, object]) -> list[int]:
    raw = payload.get("seasons")
    if isinstance(raw, list):
        seasons = [int(value) for value in raw if str(value).isdigit()]
    elif isinstance(raw, str):
        seasons = [int(value) for value in re.findall(r"\d+", raw)]
    else:
        seasons = []
    if seasons:
        return sorted(set(seasons))
    for key in ("target_season", "current_season", "season", "season_number"):
        value = item.get(key)
        if str(value).strip().isdigit():
            number = int(str(value).strip())
            if number >= 0:
                return [number]
    title = str(item.get("title") or item.get("name") or "").strip()
    tmdb_id = item.get("tmdb_id") or item.get("tmdbid")
    if not tmdb_id and str(item.get("source") or "").lower() == "tmdb":
        tmdb_id = item.get("id")
    if str(tmdb_id or "").isdigit():
        try:
            probe = {"media_type": "tv", "tmdb_id": str(tmdb_id)}
            discover_runtime.enrich_tmdb_tv_progress_fields(probe, title, str(tmdb_id))
            latest = int(probe.get("current_season") or 0)
            if latest > 0:
                return [latest]
        except Exception:
            pass
    return [1]


def moviepilot_subscribe(payload: dict[str, object] | None = None) -> dict[str, object]:
    payload = payload or {}
    auto = bool(payload.get("auto"))
    skip_existing = bool(payload.get("skip_existing"))
    safe_audit = bool(payload.get("_safe_audit"))
    cfg = _moviepilot_config()
    if auto and not cfg["auto_subscribe"]:
        return {"ok": True, "configured": bool(cfg["url"] and cfg["token"]), "pushed": False, "skipped": "MoviePilot 自动推送未启用"}
    if not cfg["url"] or not cfg["token"]:
        if auto:
            return {"ok": True, "configured": False, "pushed": False, "skipped": "未配置 MoviePilot"}
        raise ValueError("请先在系统设置里配置 MoviePilot 地址和 API Token")

    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if not isinstance(item, dict):
        raise ValueError("缺少订阅内容")
    title = str(item.get("title") or item.get("name") or "").strip()
    write_activity("push", "moviepilot_subscribe", "start", "开始推送到 MoviePilot", title=title, auto=auto)
    if not title:
        raise ValueError("缺少订阅标题")
    media_type = _moviepilot_media_type(item)
    year = _moviepilot_year(item)
    tmdb_id = _moviepilot_tmdb_id(item, media_type, year)
    if not tmdb_id:
        write_activity("push", "moviepilot_subscribe", "skip", f"没有匹配到 TMDB：{title}", title=title, auto=auto)
        if auto:
            return {"ok": True, "configured": True, "pushed": False, "skipped": f"没有匹配到 TMDB：{title}"}
        raise ValueError(f"没有匹配到 TMDB：{title}")

    request_body: dict[str, object] = {
        "notification_type": "MEDIA_AUTO_APPROVED",
        "subject": title,
        "media": {
            "media_type": "movie" if media_type == "movie" else "tv",
            "tmdbId": tmdb_id,
        },
        "request": {
            "requestedBy_username": str(cfg["username"]),
        },
    }
    seasons: list[int] = []
    if media_type == "tv":
        seasons = _moviepilot_seasons(item, payload)
        request_body["extra"] = [{"name": "Requested Seasons", "value": ", ".join(str(season) for season in seasons)}]

    existing_before_push = None
    lookup_error = ""
    try:
        existing_before_push = _moviepilot_find_subscribe(cfg, media_type, tmdb_id, seasons, title, year)
    except Exception as exc:
        lookup_error = "MoviePilot 查重失败" if safe_audit else str(exc)
        if safe_audit:
            raise RuntimeError(lookup_error) from exc
    if skip_existing and existing_before_push:
        subscribe_id = _push_int((existing_before_push or {}).get("id"))
        message = f"MoviePilot 已有订阅，跳过推送：{title}"
        audit_meta = {
            "title": title,
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "seasons": seasons,
            "already_exists": True,
            "lookup_error": lookup_error,
        }
        if not safe_audit:
            audit_meta["subscribe_id"] = subscribe_id
        write_activity(
            "push",
            "moviepilot_subscribe",
            "skip",
            message,
            **audit_meta,
        )
        return {
            "ok": True,
            "configured": True,
            "pushed": False,
            "already_exists": True,
            "skipped": message,
            "message": message,
            "title": title,
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "seasons": seasons,
            "subscribe_id": subscribe_id,
            "matched_subscribe": existing_before_push,
            "lookup_error": lookup_error,
        }

    response = requests.post(
        _moviepilot_url(str(cfg["url"]), "/api/v1/subscribe/seerr"),
        headers={
            "Authorization": str(cfg["token"]),
            "Content-Type": "application/json",
        },
        json=request_body,
        timeout=20,
    )
    response.raise_for_status()
    try:
        result = response.json()
    except Exception:
        result = {"success": True, "message": response.text[:200]}
    success = bool(result.get("success", True)) if isinstance(result, dict) else True
    message = str(result.get("message") or ("已推送到 MoviePilot" if success else "MoviePilot 返回失败")) if isinstance(result, dict) else "已推送到 MoviePilot"
    already_exists = _moviepilot_already_subscribed(result, message)
    subscribe_id = _extract_moviepilot_subscribe_id(result)
    matched_subscribe = existing_before_push
    search_result = None
    search_error = ""
    if not subscribe_id:
        try:
            matched_subscribe = matched_subscribe or _moviepilot_find_subscribe(cfg, media_type, tmdb_id, seasons, title, year)
            subscribe_id = _push_int((matched_subscribe or {}).get("id"))
        except Exception as exc:
            search_error = "MoviePilot 订阅匹配失败" if safe_audit else f"订阅匹配失败：{exc}"
    if subscribe_id:
        try:
            search_result = _moviepilot_trigger_subscribe_search(
                cfg,
                subscribe_id,
                title,
                "已有订阅触发搜索" if already_exists else "推送订阅后触发搜索",
                safe_audit=safe_audit,
            )
        except Exception as exc:
            search_error = "MoviePilot 搜索触发失败" if safe_audit else str(exc)
            audit_meta = {"title": title}
            if not safe_audit:
                audit_meta["subscribe_id"] = subscribe_id
            write_activity(
                "push",
                "moviepilot_subscribe_search",
                "error",
                "MoviePilot 搜索触发失败" if safe_audit else f"MoviePilot 搜索触发失败：{exc}",
                **audit_meta,
            )
    search_triggered = bool(search_result and search_result.get("ok"))
    if search_triggered:
        message = f"{message}，已触发搜索"
    elif already_exists and not search_error:
        message = f"{message}，未找到订阅 ID，无法触发搜索"
    elif search_error:
        message = f"{message}，搜索触发失败：{search_error}"
    success = bool(success or (already_exists and search_triggered))
    audit_meta = {
        "title": title,
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "seasons": seasons,
        "already_exists": already_exists,
        "search_triggered": search_triggered,
        "search_error": search_error,
    }
    if not safe_audit:
        audit_meta["subscribe_id"] = subscribe_id
    write_activity(
        "push",
        "moviepilot_subscribe",
        "success" if success else "error",
        ("MoviePilot 备用订阅动作已完成" if success else "MoviePilot 备用订阅动作失败") if safe_audit else message,
        **audit_meta,
    )
    return {
        "ok": success,
        "configured": True,
        "pushed": success,
        "already_exists": already_exists,
        "search_triggered": search_triggered,
        "search_error": search_error,
        "message": message,
        "title": title,
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "seasons": seasons,
        "subscribe_id": subscribe_id,
        "matched_subscribe": matched_subscribe,
        "search_result": search_result,
        "moviepilot_response": result,
    }


def moviepilot_backup_inspect(item: dict[str, object]) -> dict[str, object]:
    cfg = _moviepilot_config()
    if not cfg["url"] or not cfg["token"]:
        raise RuntimeError("MoviePilot 未配置")
    title = str(item.get("title") or "").strip()
    media_type = _moviepilot_type(item.get("media_type"))
    tmdb_id = _push_int(item.get("tmdb_id"))
    seasons = [
        int(value)
        for value in item.get("seasons") or []
        if str(value).isdigit() and int(value) > 0
    ]
    existing = _moviepilot_find_subscribe(
        cfg,
        media_type,
        tmdb_id,
        seasons,
        title,
        str(item.get("year") or ""),
    )
    subscribe_id = _extract_moviepilot_subscribe_id(existing)
    if existing and not subscribe_id:
        raise RuntimeError("MoviePilot 已有订阅缺少 ID")
    return {
        "exists": existing is not None,
        "subscribe_id": subscribe_id,
    }


def moviepilot_backup_search_existing(
    item: dict[str, object],
    inspection: dict[str, object],
) -> dict[str, object]:
    cfg = _moviepilot_config()
    if not cfg["url"] or not cfg["token"]:
        raise RuntimeError("MoviePilot 未配置")
    subscribe_id = _push_int(inspection.get("subscribe_id"))
    if not subscribe_id:
        raise RuntimeError("MoviePilot 已有订阅缺少 ID")
    return _moviepilot_trigger_subscribe_search(
        cfg,
        subscribe_id,
        str(item.get("title") or "").strip(),
        "人工备用入口触发已有订阅搜索",
        safe_audit=True,
    )


def moviepilot_backup_create(item: dict[str, object]) -> dict[str, object]:
    return moviepilot_subscribe({
        "item": {
            "title": str(item.get("title") or "").strip(),
            "media_type": str(item.get("media_type") or "").strip(),
            "tmdb_id": _push_int(item.get("tmdb_id")),
            "year": str(item.get("year") or "").strip(),
        },
        "seasons": list(item.get("seasons") or []),
        "_safe_audit": True,
    })


def _torra_config() -> dict[str, object]:
    cfg = read_config()
    return {
        "url": str(cfg.get("ENV_TORRA_URL") or "").strip().rstrip("/"),
        "token": _torra_clean_token(cfg.get("ENV_TORRA_TOKEN") or ""),
        "auto_subscribe": _truthy(cfg.get("ENV_TORRA_AUTO_SUBSCRIBE")),
    }


def _torra_clean_token(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if token.startswith("{"):
        try:
            data = json.loads(token)
            if isinstance(data, dict):
                token = str(data.get("token") or data.get("access_token") or token).strip()
        except Exception:
            pass
    token = token.strip().strip('"').strip("'").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token.strip().strip('"').strip("'").strip()


def _torra_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _torra_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _torra_request(method: str, url: str, **kwargs) -> requests.Response:
    session = requests.Session()
    session.trust_env = False
    try:
        return session.request(method, url, **kwargs)
    finally:
        session.close()


def torra_status() -> dict[str, object]:
    cfg = _torra_config()
    base_url = str(cfg["url"])
    token = str(cfg["token"])
    if not base_url or not token:
        return {
            "ok": True,
            "configured": False,
            "message": "未配置 Torra 地址或 Token",
            "auto_subscribe": bool(cfg["auto_subscribe"]),
        }
    headers = _torra_headers(token)
    response = _torra_request("GET", _torra_url(base_url, "/api/v1/subscriptions"), headers=headers, timeout=12)
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code in {401, 403}:
        return {
            "ok": False,
            "configured": True,
            "message": "Torra Token 无效或已过期，请重新从 Torra 登录后的 localStorage.auth.token 获取",
            "auto_subscribe": bool(cfg["auto_subscribe"]),
            "status_code": response.status_code,
        }
    response.raise_for_status()
    settings_data: object = {}
    settings_warning = ""
    try:
        settings_response = _torra_request("GET", _torra_url(base_url, "/api/v1/subscriptions/settings"), headers=headers, timeout=12)
        if settings_response.ok:
            try:
                settings_data = settings_response.json()
            except Exception:
                settings_data = {}
        else:
            settings_warning = f"settings 接口返回 {settings_response.status_code}，已跳过"
    except Exception as exc:
        settings_warning = f"settings 接口检测失败，已跳过：{exc}"
    return {
        "ok": True,
        "configured": True,
        "message": "Torra 连接正常",
        "auto_subscribe": bool(cfg["auto_subscribe"]),
        "torra_response": data,
        "settings": settings_data,
        "settings_warning": settings_warning,
    }


def _torra_safe_id(title: str, media_type: str, tmdb_id: int, season: int) -> str:
    key = f"{media_type}:{tmdb_id}:{season}:{title}"
    digest = hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"nasemby_{media_type}_{tmdb_id or digest}_{season or 0}"


def _torra_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value or "").strip() or default)
    except Exception:
        return default


def _torra_subscription_payload(item: dict[str, object], extra: dict[str, object]) -> dict[str, object]:
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        raise ValueError("缺少订阅标题")
    media_type = _moviepilot_media_type(item)
    year = _moviepilot_year(item)
    tmdb_id = _moviepilot_tmdb_id(item, media_type, year)
    seasons = _moviepilot_seasons(item, extra) if media_type == "tv" else [0]
    season = seasons[0] if seasons else (1 if media_type == "tv" else 0)
    poster = str(item.get("poster_path") or item.get("poster_url") or item.get("poster") or "").strip()
    backdrop = str(item.get("backdrop_path") or item.get("backdrop_url") or "").strip()
    total_episodes = _torra_int(item.get("episode_total") or item.get("total_episodes") or item.get("total_episode_count") or item.get("episode_count"), 0)
    return {
        "id": _torra_safe_id(title, media_type, tmdb_id, season),
        "name": title,
        "keyword": str(extra.get("keyword") or item.get("keyword") or title).strip(),
        "main_title_pattern": "",
        "media_type": media_type,
        "is_anime": bool(item.get("is_anime")),
        "tmdb_id": tmdb_id,
        "names": [title],
        "year": year,
        "poster_path": poster,
        "backdrop_path": backdrop,
        "season_years": {str(season): year} if media_type == "tv" and year else {},
        "season_number": season if media_type == "tv" else 0,
        "episode_group": "",
        "start_episode": 1,
        "end_episode": 0,
        "total_episode_count": total_episodes,
        "available_episode_numbers": [],
        "downloaded_episode_numbers": [],
        "downloaded_episode_files": {},
        "downloaded_file_names": [],
        "library_episode_files": {},
        "library_file_names": [],
        "site_ids": [],
        "downloader_id": str(extra.get("downloader_id") or item.get("downloader_id") or "").strip(),
        "save_path": str(extra.get("save_path") or item.get("save_path") or "").strip(),
        "version_control_enabled": False,
        "version_control_entries": [],
        "version_control_mode": "include",
        "enabled": True,
        "completed": False,
        "auto_rewash_status": "",
        "auto_rewash_started_at": "",
        "auto_rewash_finished_at": "",
        "auto_rewash_finish_reason": "",
        "auto_rewash_finish_message": "",
        "auto_rewash_pushed_episode_numbers": [],
        "auto_rewash_pushed_target_keys": [],
        "auto_rewash_pushed_urls": [],
        "initial_auto_search_pending": True,
        "initial_auto_search_running": False,
        "is_running": False,
        "is_mutating": False,
        "created_at": "",
        "updated_at": "",
        "last_checked_at": "",
        "last_result_count": 0,
        "last_matched_title": "",
        "last_added_at": "",
        "last_added_name": "",
        "last_error": "",
        "downloaded_target_keys": [],
        "downloaded_urls": [],
    }


def _torra_list_subscriptions(cfg: dict[str, object]) -> list[dict[str, object]]:
    response = _torra_request(
        "GET",
        _torra_url(str(cfg["url"]), "/api/v1/subscriptions"),
        headers=_torra_headers(str(cfg["token"])),
        timeout=15,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        return []
    rows = []
    if isinstance(data, dict):
        body = data.get("data")
        if isinstance(body, dict) and isinstance(body.get("subscriptions"), list):
            rows = body.get("subscriptions") or []
        elif isinstance(data.get("subscriptions"), list):
            rows = data.get("subscriptions") or []
    elif isinstance(data, list):
        rows = data
    return [row for row in rows if isinstance(row, dict)]


def _torra_subscription_matches(row: dict[str, object], subscription: dict[str, object]) -> bool:
    row_tmdb_id = _push_int(row.get("tmdb_id") or row.get("tmdbid"))
    target_tmdb_id = _push_int(subscription.get("tmdb_id") or subscription.get("tmdbid"))
    row_type = _moviepilot_type(row.get("media_type") or row.get("type"))
    target_type = _moviepilot_type(subscription.get("media_type") or subscription.get("type"))
    if row_tmdb_id and target_tmdb_id:
        if row_tmdb_id != target_tmdb_id or row_type != target_type:
            return False
        if target_type == "tv":
            row_season = _push_int(row.get("season_number") or row.get("season"), -1)
            target_season = _push_int(subscription.get("season_number") or subscription.get("season"), -1)
            return row_season <= 0 or target_season <= 0 or row_season == target_season
        return True

    row_title = _push_compact(row.get("name") or row.get("keyword"))
    target_title = _push_compact(subscription.get("name") or subscription.get("keyword"))
    if not row_title or row_title != target_title or row_type != target_type:
        return False
    row_year = str(row.get("year") or "").strip()
    target_year = str(subscription.get("year") or "").strip()
    return not row_year or not target_year or row_year == target_year


def _torra_find_subscription(cfg: dict[str, object], subscription: dict[str, object]) -> dict[str, object] | None:
    for row in _torra_list_subscriptions(cfg):
        if _torra_subscription_matches(row, subscription):
            return row
    return None


def _torra_merge_existing_subscription(existing: dict[str, object], incoming: dict[str, object]) -> dict[str, object]:
    merged = dict(existing)
    for key in (
        "name",
        "keyword",
        "main_title_pattern",
        "media_type",
        "is_anime",
        "tmdb_id",
        "names",
        "year",
        "poster_path",
        "backdrop_path",
        "season_years",
        "season_number",
        "episode_group",
        "start_episode",
        "end_episode",
        "total_episode_count",
    ):
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    merged["id"] = existing.get("id") or incoming.get("id") or ""
    merged["enabled"] = True
    merged["initial_auto_search_pending"] = True
    merged["initial_auto_search_running"] = False
    merged["is_running"] = False
    return merged


def _torra_trigger_subscription_run(cfg: dict[str, object], subscription_id: str, title: str, mode: str = "auto") -> dict[str, object]:
    subscription_id = str(subscription_id or "").strip()
    if not subscription_id:
        return {"ok": False, "message": "未找到 Torra 订阅 ID"}
    response = _torra_request(
        "POST",
        _torra_url(str(cfg["url"]), f"/api/v1/subscriptions/run/{quote(subscription_id, safe='')}"),
        headers=_torra_headers(str(cfg["token"])),
        params={"mode": mode},
        timeout=30,
    )
    response.raise_for_status()
    try:
        result = response.json()
    except Exception:
        result = {"success": True, "message": response.text[:200]}
    success = bool(result.get("success", True)) if isinstance(result, dict) else True
    message = str(result.get("message") or ("Torra 搜索已触发" if success else "Torra 搜索触发失败")) if isinstance(result, dict) else "Torra 搜索已触发"
    write_activity(
        "push",
        "torra_subscribe_search",
        "success" if success else "error",
        message,
        title=title,
        subscription_id=subscription_id,
        mode=mode,
    )
    return {
        "ok": success,
        "message": message,
        "subscription_id": subscription_id,
        "mode": mode,
        "torra_response": result,
    }


def torra_subscribe(payload: dict[str, object] | None = None) -> dict[str, object]:
    payload = payload or {}
    auto = bool(payload.get("auto"))
    skip_existing = bool(payload.get("skip_existing"))
    cfg = _torra_config()
    if auto and not cfg["auto_subscribe"]:
        return {"ok": True, "configured": bool(cfg["url"] and cfg["token"]), "pushed": False, "skipped": "Torra 自动推送未启用"}
    if not cfg["url"] or not cfg["token"]:
        if auto:
            return {"ok": True, "configured": False, "pushed": False, "skipped": "未配置 Torra"}
        raise ValueError("请先在系统设置里配置 Torra 地址和 Token")

    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if not isinstance(item, dict):
        raise ValueError("缺少订阅内容")
    subscription = _torra_subscription_payload(item, payload)
    existing_subscription = None
    lookup_error = ""
    try:
        existing_subscription = _torra_find_subscription(cfg, subscription)
    except Exception as exc:
        lookup_error = str(exc)
    already_exists = bool(existing_subscription)
    if skip_existing and existing_subscription:
        message = f"Torra 已有订阅，跳过推送：{existing_subscription.get('name') or subscription.get('name') or ''}"
        write_activity(
            "push",
            "torra_subscribe",
            "skip",
            message,
            title=subscription.get("name") or "",
            media_type=subscription.get("media_type") or "",
            tmdb_id=subscription.get("tmdb_id") or 0,
            season=subscription.get("season_number") or 0,
            subscription_id=(existing_subscription or {}).get("id") or "",
            already_exists=True,
            lookup_error=lookup_error,
        )
        return {
            "ok": True,
            "configured": True,
            "pushed": False,
            "already_exists": True,
            "skipped": message,
            "message": message,
            "title": subscription.get("name") or "",
            "media_type": subscription.get("media_type") or "",
            "tmdb_id": subscription.get("tmdb_id") or 0,
            "season": subscription.get("season_number") or 0,
            "subscription_id": (existing_subscription or {}).get("id") or "",
            "matched_subscription_id": (existing_subscription or {}).get("id") or "",
            "lookup_error": lookup_error,
        }
    if existing_subscription:
        subscription = _torra_merge_existing_subscription(existing_subscription, subscription)
    write_activity(
        "push",
        "torra_subscribe",
        "start",
        "开始推送到 Torra",
        title=subscription.get("name") or "",
        auto=auto,
        subscription_id=subscription.get("id") or "",
        already_exists=already_exists,
        lookup_error=lookup_error,
    )
    response = _torra_request(
        "POST",
        _torra_url(str(cfg["url"]), "/api/v1/subscriptions/save"),
        headers=_torra_headers(str(cfg["token"])),
        json={"subscription": subscription},
        timeout=20,
    )
    response.raise_for_status()
    try:
        result = response.json()
    except Exception:
        result = {"success": True, "message": response.text[:200]}
    success = bool(result.get("success", True)) if isinstance(result, dict) else True
    message = str(result.get("message") or ("已推送到 Torra" if success else "Torra 返回失败")) if isinstance(result, dict) else "已推送到 Torra"
    search_result = None
    search_error = ""
    if success:
        try:
            search_result = _torra_trigger_subscription_run(
                cfg,
                str(subscription.get("id") or ""),
                str(subscription.get("name") or ""),
                "auto",
            )
        except Exception as exc:
            search_error = str(exc)
            write_activity(
                "push",
                "torra_subscribe_search",
                "error",
                f"Torra 搜索触发失败：{exc}",
                title=subscription.get("name") or "",
                subscription_id=subscription.get("id") or "",
            )
    search_triggered = bool(search_result and search_result.get("ok"))
    if search_triggered:
        message = f"{message}，已触发搜索"
    elif search_error:
        message = f"{message}，搜索触发失败：{search_error}"
    elif success:
        message = f"{message}，未触发搜索"
    write_activity(
        "push",
        "torra_subscribe",
        "success" if success else "error",
        message,
        title=subscription.get("name") or "",
        media_type=subscription.get("media_type") or "",
        tmdb_id=subscription.get("tmdb_id") or 0,
        season=subscription.get("season_number") or 0,
        subscription_id=subscription.get("id") or "",
        already_exists=already_exists,
        search_triggered=search_triggered,
        search_error=search_error,
        lookup_error=lookup_error,
    )
    return {
        "ok": success,
        "configured": True,
        "pushed": success,
        "already_exists": already_exists,
        "search_triggered": search_triggered,
        "search_error": search_error,
        "lookup_error": lookup_error,
        "message": message,
        "title": subscription.get("name") or "",
        "media_type": subscription.get("media_type") or "",
        "tmdb_id": subscription.get("tmdb_id") or 0,
        "season": subscription.get("season_number") or 0,
        "subscription_id": subscription.get("id") or "",
        "matched_subscription_id": (existing_subscription or {}).get("id") or "",
        "search_result": search_result,
        "torra_response": result,
    }


def _symedia_find_token_in_json(value: object) -> str:
    if isinstance(value, dict):
        for key in ("token", "access_token", "accessToken", "authToken", "jwt"):
            token = str(value.get(key) or "").strip()
            if token:
                return token
        for item in value.values():
            found = _symedia_find_token_in_json(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _symedia_find_token_in_json(item)
            if found:
                return found
    return ""


def _symedia_clean_token(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if token.startswith("{") or token.startswith("["):
        try:
            parsed = json.loads(token)
            token = _symedia_find_token_in_json(parsed) or token
        except Exception:
            pass
    token = token.strip().strip('"').strip("'").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token.strip().strip('"').strip("'").strip()


def _symedia_channel_type(value: object) -> str:
    channel_type = str(value or "").strip()
    allowed = {"channel_115", "channel_189", "channel_123", "channel_alipan", "channel_quark"}
    return channel_type if channel_type in allowed else "channel_115"


def _symedia_split_values(value: object) -> list[str]:
    if isinstance(value, list):
        rows = value
    else:
        rows = re.split(r"[\s,，;；|]+", str(value or ""))
    result = []
    seen = set()
    for row in rows:
        text = str(row or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _symedia_config() -> dict[str, object]:
    cfg = read_config()
    return {
        "url": str(cfg.get("ENV_SYMEDIA_URL") or "").strip().rstrip("/"),
        "token": _symedia_clean_token(cfg.get("ENV_SYMEDIA_TOKEN") or ""),
        "username": str(cfg.get("ENV_SYMEDIA_USERNAME") or "").strip(),
        "password": str(cfg.get("ENV_SYMEDIA_PASSWORD") or "").strip(),
        "channel_type": _symedia_channel_type(cfg.get("ENV_SYMEDIA_CHANNEL_TYPE") or ""),
        "channel_ids": _symedia_split_values(cfg.get("ENV_SYMEDIA_CHANNEL_IDS") or ""),
        "parent_id": str(cfg.get("ENV_SYMEDIA_PARENT_ID") or "").strip(),
        "rule_id": str(cfg.get("ENV_SYMEDIA_RULE_ID") or "").strip(),
        "auto_subscribe": _truthy(cfg.get("ENV_SYMEDIA_AUTO_SUBSCRIBE")),
    }


def _symedia_url(base_url: str, path: str) -> str:
    path = path.lstrip("/")
    if path.startswith("api/v1/"):
        return f"{base_url.rstrip('/')}/{path}"
    return f"{base_url.rstrip('/')}/api/v1/{path}"


def _symedia_request(method: str, cfg: dict[str, object], path: str, **kwargs) -> requests.Response:
    token = _symedia_access_token(cfg)
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Accept", "application/json")
    if kwargs.get("json") is not None:
        headers.setdefault("Content-Type", "application/json")
    session = requests.Session()
    session.trust_env = False
    try:
        return session.request(method, _symedia_url(str(cfg["url"]), path), headers=headers, **kwargs)
    finally:
        session.close()


def _symedia_access_token(cfg: dict[str, object]) -> str:
    cached = str(cfg.get("_access_token") or "").strip()
    if cached:
        return cached
    token = str(cfg.get("token") or "").strip()
    if token:
        cfg["_access_token"] = token
        return token
    username = str(cfg.get("username") or "").strip()
    password = str(cfg.get("password") or "").strip()
    if not username or not password:
        return ""
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            _symedia_url(str(cfg["url"]), "/login/access-token"),
            data={"username": username, "password": password},
            headers={"Accept": "application/json"},
            timeout=15,
        )
    finally:
        session.close()
    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        data = {}
    token = str(data.get("access_token") or data.get("token") or "").strip() if isinstance(data, dict) else ""
    if not token:
        raise RuntimeError("Symedia 登录成功但没有返回 access_token")
    cfg["_access_token"] = token
    return token


def _symedia_configured(cfg: dict[str, object]) -> bool:
    return bool(cfg.get("url") and (cfg.get("token") or (cfg.get("username") and cfg.get("password"))))


def _symedia_json(response: requests.Response) -> object:
    try:
        return response.json()
    except Exception:
        return {"success": response.ok, "message": response.text[:200]}


def _symedia_payload(data: object) -> object:
    if isinstance(data, dict) and "data" in data and (data.get("success") is not None or not any(str(key).startswith("channel_") for key in data.keys())):
        return data.get("data")
    return data


def _symedia_rows(data: object) -> list[dict[str, object]]:
    body = _symedia_payload(data)
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        for key in ("items", "tasks", "rows", "results"):
            if isinstance(body.get(key), list):
                return [row for row in body.get(key) if isinstance(row, dict)]
    return []


def _symedia_item_value(item: object) -> str:
    if isinstance(item, dict):
        for key in ("value", "id", "key", "name", "title"):
            text = str(item.get(key) or "").strip()
            if text:
                return text
    return str(item or "").strip()


def _symedia_list_tasks(cfg: dict[str, object]) -> list[dict[str, object]]:
    response = _symedia_request("GET", cfg, "/telegram_subscribe/tasks", timeout=15)
    response.raise_for_status()
    return _symedia_rows(_symedia_json(response))


def _symedia_transfer_folders(cfg: dict[str, object]) -> dict[str, list[object]]:
    response = _symedia_request("GET", cfg, "/telegram_subscribe/transfer-folders", timeout=15)
    response.raise_for_status()
    data = _symedia_payload(_symedia_json(response))
    if isinstance(data, dict):
        return {str(key): value for key, value in data.items() if isinstance(value, list)}
    return {}


def _symedia_search_channels(cfg: dict[str, object]) -> dict[str, list[object]]:
    response = _symedia_request("GET", cfg, "/system/settings/telegram_search_channel", timeout=15)
    response.raise_for_status()
    data = _symedia_payload(_symedia_json(response))
    if isinstance(data, dict):
        return {str(key): value for key, value in data.items() if isinstance(value, list)}
    return {}


def _symedia_rules(cfg: dict[str, object]) -> list[dict[str, object]]:
    response = _symedia_request("GET", cfg, "/system/settings/telegram_subscribe_rule", timeout=15)
    response.raise_for_status()
    return _symedia_rows(_symedia_json(response))


def _symedia_default_channels(cfg: dict[str, object], channel_type: str) -> list[str]:
    configured = [str(item).strip() for item in (cfg.get("channel_ids") or []) if str(item).strip()]
    if configured:
        return configured
    channels = _symedia_search_channels(cfg).get(channel_type) or []
    first = _symedia_item_value(channels[0]) if channels else ""
    return [first] if first else []


def _symedia_default_parent_id(cfg: dict[str, object], channel_type: str) -> str:
    configured = str(cfg.get("parent_id") or "").strip()
    if configured:
        return configured
    folders = _symedia_transfer_folders(cfg).get(channel_type) or []
    return _symedia_item_value(folders[0]) if folders else ""


def _symedia_task_matches(row: dict[str, object], task: dict[str, object]) -> bool:
    row_type = _moviepilot_type(row.get("type") or row.get("media_type"))
    task_type = _moviepilot_type(task.get("type") or task.get("media_type"))
    if row_type != task_type:
        return False
    row_channel = _symedia_channel_type(row.get("channel_type") or "")
    task_channel = _symedia_channel_type(task.get("channel_type") or "")
    if row_channel != task_channel:
        return False
    row_tmdb = str(row.get("tmdbid") or row.get("tmdb_id") or "").strip()
    task_tmdb = str(task.get("tmdbid") or task.get("tmdb_id") or "").strip()
    if row_tmdb and task_tmdb:
        if row_tmdb != task_tmdb:
            return False
        if task_type == "tv":
            return _push_int(row.get("season"), 1) == _push_int(task.get("season"), 1)
        return True
    row_title = _push_compact(row.get("title") or row.get("name"))
    task_title = _push_compact(task.get("title") or task.get("name"))
    if not row_title or row_title != task_title:
        return False
    row_year = str(row.get("year") or "").strip()
    task_year = str(task.get("year") or "").strip()
    if row_year and task_year and row_year != task_year:
        return False
    if task_type == "tv":
        return _push_int(row.get("season"), 1) == _push_int(task.get("season"), 1)
    return True


def _symedia_find_task(cfg: dict[str, object], task: dict[str, object]) -> dict[str, object] | None:
    for row in _symedia_list_tasks(cfg):
        if _symedia_task_matches(row, task):
            return row
    return None


def _symedia_total_episodes(item: dict[str, object], media_type: str, season: int) -> int:
    for key in ("total_episode_count", "episode_total", "total_episodes", "episode_count", "episodes"):
        number = _push_int(item.get(key), 0)
        if number > 0:
            return number
    season_counts = item.get("season_episode_count")
    if isinstance(season_counts, dict):
        number = _push_int(season_counts.get(str(season)) or season_counts.get(season), 0)
        if number > 0:
            return number
    return 1 if media_type == "movie" else 250


def _symedia_subscription_payload(item: dict[str, object], extra: dict[str, object], cfg: dict[str, object]) -> dict[str, object]:
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        raise ValueError("缺少订阅标题")
    media_type = _moviepilot_media_type(item)
    year = _moviepilot_year(item) or time.strftime("%Y")
    tmdb_id = _moviepilot_tmdb_id(item, media_type, year)
    if not tmdb_id:
        raise ValueError(f"没有匹配到 TMDB：{title}")
    season = 1
    if media_type == "tv":
        seasons = _moviepilot_seasons(item, extra)
        season = max(1, int(seasons[0] if seasons else 1))
    total_episodes = _symedia_total_episodes(item, media_type, season)
    channel_type = _symedia_channel_type(extra.get("channel_type") or cfg.get("channel_type") or "")
    channels = _symedia_split_values(extra.get("channels")) if "channels" in extra else _symedia_default_channels(cfg, channel_type)
    subscribe_url = str(extra.get("subscribe_url") or item.get("subscribe_url") or "").strip()
    parent_id = str(extra.get("parent_id") or cfg.get("parent_id") or "").strip() or _symedia_default_parent_id(cfg, channel_type)
    if not channels and not subscribe_url:
        raise ValueError("Symedia 未找到可用订阅频道，请在设置里填写频道 ID")
    if not parent_id:
        raise ValueError("Symedia 未找到可用转存目录，请在设置里填写转存目录 ID")
    poster = str(item.get("poster_path") or item.get("poster_url") or item.get("poster") or "").strip()
    backdrop = str(item.get("backdrop_path") or item.get("backdrop_url") or "").strip()
    start_episode = _push_int(extra.get("start_episode") or item.get("start_episode"), 1)
    return {
        "tmdbid": str(tmdb_id),
        "type": "movie" if media_type == "movie" else "tv",
        "title": title,
        "year": _push_int(year, _push_int(time.strftime("%Y"), 2026)),
        "poster_path": poster,
        "backdrop_path": backdrop,
        "rating": {},
        "channel_type": channel_type,
        "channels": channels,
        "subscribe_url": subscribe_url,
        "season_episode_count": {str(season): total_episodes} if media_type == "tv" and total_episodes else {},
        "total_episode_count": total_episodes,
        "start_episode": max(1, start_episode),
        "overview": str(item.get("overview") or item.get("plot") or "").strip(),
        "end_words": "全|完结" if media_type == "movie" else r"全集|完结|全\d+集",
        "parent_id": parent_id,
        "id": "",
        "rule_id": str(extra.get("rule_id") or cfg.get("rule_id") or "").strip(),
        "season": season,
        "invalid_urls": [],
        "last_urls": [],
        "last_update": "",
        "latest_episode": 0,
        "transfered_episodes": [],
        "message_include_words": [],
        "message_exclude_words": [],
    }


def _symedia_save_task(cfg: dict[str, object], task: dict[str, object]) -> dict[str, object]:
    response = _symedia_request(
        "POST",
        cfg,
        "/telegram_subscribe/save_telegram_subscribe_task",
        json=task,
        timeout=20,
    )
    response.raise_for_status()
    result = _symedia_json(response)
    if isinstance(result, dict) and result.get("success") is False:
        return {"ok": False, "message": str(result.get("message") or "Symedia 保存订阅失败"), "symedia_response": result}
    return {"ok": True, "message": "已推送到 Symedia", "symedia_response": result, "task": _symedia_payload(result) if isinstance(_symedia_payload(result), dict) else task}


def _symedia_trigger_manual_search(cfg: dict[str, object], task: dict[str, object], title: str, reason: str = "") -> dict[str, object]:
    response = _symedia_request(
        "POST",
        cfg,
        "/telegram_subscribe/manual-search",
        json={"task": task},
        timeout=30,
    )
    response.raise_for_status()
    result = _symedia_json(response)
    success = bool(result.get("success", True)) if isinstance(result, dict) else True
    message = str(result.get("message") or ("Symedia 搜索已触发" if success else "Symedia 搜索触发失败")) if isinstance(result, dict) else "Symedia 搜索已触发"
    write_activity(
        "push",
        "symedia_subscribe_search",
        "success" if success else "error",
        message,
        title=title,
        task_id=task.get("id") or "",
        reason=reason,
    )
    return {
        "ok": success,
        "message": message,
        "task_id": task.get("id") or "",
        "symedia_response": result,
    }


def symedia_status() -> dict[str, object]:
    cfg = _symedia_config()
    if not _symedia_configured(cfg):
        return {
            "ok": True,
            "configured": False,
            "message": "未配置 Symedia 地址和 Token/账号密码",
            "auto_subscribe": bool(cfg["auto_subscribe"]),
        }
    try:
        tasks = _symedia_list_tasks(cfg)
        folders = _symedia_transfer_folders(cfg)
        channels = _symedia_search_channels(cfg)
        rules = _symedia_rules(cfg)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        if status_code in {401, 403}:
            return {
                "ok": False,
                "configured": True,
                "message": "Symedia 登录态无效，请更新 Token 或账号密码",
                "auto_subscribe": bool(cfg["auto_subscribe"]),
                "status_code": status_code,
            }
        raise
    channel_type = str(cfg.get("channel_type") or "channel_115")
    return {
        "ok": True,
        "configured": True,
        "message": "Symedia 连接正常",
        "auto_subscribe": bool(cfg["auto_subscribe"]),
        "task_count": len(tasks),
        "channel_type": channel_type,
        "channel_count": len(channels.get(channel_type) or []),
        "folder_count": len(folders.get(channel_type) or []),
        "rule_count": len(rules),
    }


def symedia_subscribe(payload: dict[str, object] | None = None) -> dict[str, object]:
    payload = payload or {}
    auto = bool(payload.get("auto"))
    skip_existing = bool(payload.get("skip_existing"))
    cfg = _symedia_config()
    if auto and not cfg["auto_subscribe"]:
        return {"ok": True, "configured": _symedia_configured(cfg), "pushed": False, "skipped": "Symedia 自动推送未启用"}
    if not _symedia_configured(cfg):
        if auto:
            return {"ok": True, "configured": False, "pushed": False, "skipped": "未配置 Symedia"}
        raise ValueError("请先在系统设置里配置 Symedia 地址和 Token/账号密码")
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if not isinstance(item, dict):
        raise ValueError("缺少订阅内容")
    task = _symedia_subscription_payload(item, payload, cfg)
    title = str(task.get("title") or "")
    existing_task = None
    lookup_error = ""
    try:
        existing_task = _symedia_find_task(cfg, task)
    except Exception as exc:
        lookup_error = str(exc)
    already_exists = bool(existing_task)
    if skip_existing and existing_task:
        message = f"Symedia 已有订阅，跳过推送：{title}"
        write_activity(
            "push",
            "symedia_subscribe",
            "skip",
            message,
            title=title,
            media_type=task.get("type") or "",
            tmdb_id=task.get("tmdbid") or "",
            season=task.get("season") or "",
            task_id=(existing_task or {}).get("id") or "",
            already_exists=True,
            lookup_error=lookup_error,
        )
        return {
            "ok": True,
            "configured": True,
            "pushed": False,
            "already_exists": True,
            "skipped": message,
            "message": message,
            "title": title,
            "media_type": task.get("type") or "",
            "tmdb_id": task.get("tmdbid") or "",
            "season": task.get("season") or "",
            "task_id": (existing_task or {}).get("id") or "",
            "matched_task_id": (existing_task or {}).get("id") or "",
            "lookup_error": lookup_error,
        }
    write_activity(
        "push",
        "symedia_subscribe",
        "start",
        "开始推送到 Symedia",
        title=title,
        auto=auto,
        task_id=(existing_task or {}).get("id") or "",
        already_exists=already_exists,
        lookup_error=lookup_error,
    )
    result: dict[str, object] = {"ok": True, "message": "已有 Symedia 订阅"}
    task_for_search = dict(existing_task or task)
    if not existing_task:
        result = _symedia_save_task(cfg, task)
        if not result.get("ok"):
            message = str(result.get("message") or "Symedia 保存订阅失败")
            write_activity(
                "push",
                "symedia_subscribe",
                "error",
                message,
                title=title,
                media_type=task.get("type") or "",
                tmdb_id=task.get("tmdbid") or "",
                season=task.get("season") or "",
                lookup_error=lookup_error,
            )
            return {
                "ok": False,
                "configured": True,
                "pushed": False,
                "already_exists": False,
                "search_triggered": False,
                "search_error": "",
                "lookup_error": lookup_error,
                "message": message,
                "title": title,
                "media_type": task.get("type") or "",
                "tmdb_id": task.get("tmdbid") or "",
                "season": task.get("season") or "",
                "symedia_response": result.get("symedia_response"),
            }
        if isinstance(result.get("task"), dict):
            task_for_search = dict(result.get("task") or {})
    search_result = None
    search_error = ""
    try:
        search_result = _symedia_trigger_manual_search(
            cfg,
            task_for_search,
            title,
            "已有订阅触发搜索" if already_exists else "推送订阅后触发搜索",
        )
        if search_result and not search_result.get("ok"):
            search_error = str(search_result.get("message") or "Symedia 搜索触发失败")
    except Exception as exc:
        search_error = str(exc)
        write_activity(
            "push",
            "symedia_subscribe_search",
            "error",
            f"Symedia 搜索触发失败：{exc}",
            title=title,
            task_id=task_for_search.get("id") or "",
        )
    search_triggered = bool(search_result and search_result.get("ok"))
    message = str(result.get("message") or ("已有 Symedia 订阅" if already_exists else "已推送到 Symedia"))
    if search_triggered:
        message = f"{message}，已触发搜索"
    elif search_error:
        message = f"{message}，搜索触发失败：{search_error}"
    else:
        message = f"{message}，未触发搜索"
    success = bool((already_exists or result.get("ok")) and not search_error)
    write_activity(
        "push",
        "symedia_subscribe",
        "success" if success else "error",
        message,
        title=title,
        media_type=task.get("type") or "",
        tmdb_id=task.get("tmdbid") or "",
        season=task.get("season") or "",
        task_id=task_for_search.get("id") or "",
        already_exists=already_exists,
        search_triggered=search_triggered,
        search_error=search_error,
        lookup_error=lookup_error,
    )
    return {
        "ok": success,
        "configured": True,
        "pushed": bool(success),
        "already_exists": already_exists,
        "search_triggered": search_triggered,
        "search_error": search_error,
        "lookup_error": lookup_error,
        "message": message,
        "title": title,
        "media_type": task.get("type") or "",
        "tmdb_id": task.get("tmdbid") or "",
        "season": task.get("season") or "",
        "task_id": task_for_search.get("id") or "",
        "matched_task_id": (existing_task or {}).get("id") or "",
        "search_result": search_result,
        "symedia_response": result.get("symedia_response"),
    }


def run_115_monitor_once() -> dict[str, object]:
    module = _reload_legacy_module("tgto115")
    module.tg_115monitor()
    return {"ok": True}


def run_115_cleanup() -> dict[str, object]:
    module = _reload_legacy_module("tgto115")
    module.clean_task()
    return {"ok": True}


def run_115_invite_boost(text: str) -> dict[str, object]:
    module = _reload_legacy_module("zhuli115")
    ok = bool(module.accept_invite(text or ""))
    return {"ok": ok}


def _read_cpu_totals() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8", errors="ignore") as fp:
        parts = fp.readline().split()[1:]
    values = [int(part) for part in parts if part.isdigit()]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _cpu_percent(interval: float = 0.15) -> float:
    idle_a, total_a = _read_cpu_totals()
    time.sleep(interval)
    idle_b, total_b = _read_cpu_totals()
    total_delta = max(1, total_b - total_a)
    idle_delta = max(0, idle_b - idle_a)
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def _memory_usage() -> dict[str, object]:
    values: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as fp:
        for line in fp:
            key, _, raw = line.partition(":")
            match = re.search(r"\d+", raw)
            if match:
                values[key] = int(match.group(0)) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    used = max(0, total - available)
    percent = round((used / total * 100) if total else 0, 1)
    return {"total": total, "used": used, "available": available, "percent": percent}


def _network_totals() -> tuple[int, int]:
    rx = 0
    tx = 0
    with open("/proc/net/dev", "r", encoding="utf-8", errors="ignore") as fp:
        for line in fp:
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            if name.strip() == "lo":
                continue
            parts = raw.split()
            if len(parts) >= 16:
                rx += int(parts[0])
                tx += int(parts[8])
    return rx, tx


def _network_speed(interval: float = 0.5) -> dict[str, object]:
    rx_a, tx_a = _network_totals()
    time.sleep(interval)
    rx_b, tx_b = _network_totals()
    elapsed = max(interval, 0.1)
    return {
        "down_bps": max(0, int((rx_b - rx_a) / elapsed)),
        "up_bps": max(0, int((tx_b - tx_a) / elapsed)),
        "rx_total": rx_b,
        "tx_total": tx_b,
    }


def dashboard_system_metrics() -> dict[str, object]:
    disk_path = DATA_DIR if DATA_DIR.exists() else Path("/")
    disk = shutil.disk_usage(disk_path)
    emby: dict[str, object]
    try:
        emby_data = fetch_emby_libraries({})
        libraries = emby_data.get("libraries") if isinstance(emby_data, dict) else []
        emby = {
            "ok": True,
            "count": len(libraries or []),
            "libraries": libraries or [],
            "auth_method": emby_data.get("auth_method") if isinstance(emby_data, dict) else "",
        }
    except Exception as exc:
        emby = {"ok": False, "count": 0, "libraries": [], "error": str(exc)}
    return {
        "ok": True,
        "cpu": {"percent": _cpu_percent()},
        "memory": _memory_usage(),
        "disk": {
            "path": str(disk_path),
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": round((disk.used / disk.total * 100) if disk.total else 0, 1),
        },
        "network": _network_speed(),
        "emby": emby,
    }


def project_status() -> dict[str, object]:
    return {
        "ok": True,
        "data_dir": str(DATA_DIR),
        "features": [
            "今日播出",
            "发现资源",
            "订阅管理",
            "我的订阅",
            "订阅推送",
        ],
    }
