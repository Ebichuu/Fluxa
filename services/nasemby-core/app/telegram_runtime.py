from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from app.config import DATA_DIR, read_config, write_config


SESSION_PATH = DATA_DIR / "telegram.session"
LOGIN_STATE_PATH = DATA_DIR / "telegram_login.json"
CHANNEL_ICON_DIR = DATA_DIR / "telegram_channel_icons"
CHANNEL_MODE_DEFAULT = "incoming"
CHANNEL_MODE_ALIASES = {
    "manual": "manual",
    "手动": "manual",
    "incoming": "incoming",
    "new": "incoming",
    "入新": "incoming",
    "follow": "follow",
    "追更": "follow",
    "rewash": "rewash",
    "wash": "rewash",
    "洗版": "rewash",
    "complete": "complete",
    "completion": "complete",
    "补全": "complete",
    "full": "full",
    "all": "full",
    "全量": "full",
}
CHANNEL_AUTO_MONITOR_MODES = {"incoming", "full"}


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _telethon():
    try:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 Telethon 依赖，请重新构建容器后再使用 Telegram 登录。") from exc
    return TelegramClient, SessionPasswordNeededError


def _credentials(payload: dict[str, Any] | None = None) -> tuple[str, int, str]:
    payload = payload or {}
    config = read_config()
    phone = str(payload.get("phone") or payload.get("ENV_TG_PHONE") or config.get("ENV_TG_PHONE") or "").strip()
    api_id_raw = str(payload.get("api_id") or payload.get("ENV_TG_API_ID") or config.get("ENV_TG_API_ID") or "").strip()
    api_hash = str(payload.get("api_hash") or payload.get("ENV_TG_API_HASH") or config.get("ENV_TG_API_HASH") or "").strip()
    if not api_id_raw or not api_hash:
        raise ValueError("请先填写 App api_id 和 App api_hash")
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise ValueError("App api_id 必须是数字") from exc
    return phone, api_id, api_hash


def _proxy_config():
    proxy_url = str(read_config().get("ENV_PROXY") or "").strip()
    if not proxy_url:
        return None

    try:
        import socks
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 PySocks 依赖，Telegram 无法使用代理；请安装 PySocks 或重建容器。") from exc

    parsed = urlparse(proxy_url if "://" in proxy_url else f"socks5://{proxy_url}")
    host = parsed.hostname
    if not host:
        raise ValueError("代理地址格式不正确，例如 http://127.0.0.1:7890")

    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        proxy_type = socks.HTTP
        port = parsed.port or 8080
    elif scheme in {"socks", "socks5"}:
        proxy_type = socks.SOCKS5
        port = parsed.port or 1080
    elif scheme == "socks4":
        proxy_type = socks.SOCKS4
        port = parsed.port or 1080
    else:
        raise ValueError("Telegram 代理只支持 http、socks4 或 socks5")

    return (
        proxy_type,
        host,
        port,
        True,
        parsed.username,
        parsed.password,
    )


def _client(api_id: int, api_hash: str, *, connection_retries: int = 1, request_retries: int = 0, timeout: int = 8):
    TelegramClient, _ = _telethon()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    proxy = _proxy_config()
    kwargs = {
        "connection_retries": connection_retries,
        "request_retries": request_retries,
        "timeout": timeout,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return TelegramClient(str(SESSION_PATH), api_id, api_hash, **kwargs)


@asynccontextmanager
async def _connected_client(api_id: int, api_hash: str, **client_options):
    client = _client(api_id, api_hash, **client_options)
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


def _telegram_request_error(exc: Exception) -> Exception:
    text = str(exc) or exc.__class__.__name__
    if "Request was unsuccessful" in text:
        return RuntimeError(f"Telegram 请求未成功，通常是 NAS 到 Telegram 的代理/网络不稳定；已重试多次仍失败。原始错误：{text}")
    return exc


def _display_user(user) -> str:
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    name = " ".join(part for part in [getattr(user, "first_name", ""), getattr(user, "last_name", "")] if part).strip()
    return name or str(getattr(user, "id", "") or "Telegram 用户")


def _normalize_channel_mode(value: Any) -> str:
    return CHANNEL_MODE_ALIASES.get(str(value or "").strip().lower(), CHANNEL_MODE_DEFAULT)


def _normalize_channel_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled", "停用", "关闭"}


def _normalize_saved_channel(item: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    return {
        "input": str(item.get("input") or fallback.get("input") or "").strip(),
        "name": str(item.get("name") or fallback.get("name") or item.get("username") or item.get("input") or "").strip(),
        "id": str(item.get("id") or fallback.get("id") or "").strip(),
        "username": str(item.get("username") or fallback.get("username") or "").strip(),
        "photo_url": str(item.get("photo_url") or fallback.get("photo_url") or "").strip(),
        "mode": _normalize_channel_mode(item.get("mode", fallback.get("mode", CHANNEL_MODE_DEFAULT))),
        "enabled": _normalize_channel_enabled(item.get("enabled", fallback.get("enabled", True))),
    }


def _read_channels_from_config() -> list[dict[str, Any]]:
    raw = read_config().get("ENV_TG_CHANNELS") or "[]"
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [_normalize_saved_channel(item) for item in data if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass
    legacy = read_config().get("ENV_115_TG_CHANNEL") or ""
    return [
        _normalize_saved_channel({"input": item.strip(), "name": item.strip(), "mode": CHANNEL_MODE_DEFAULT, "enabled": True})
        for item in legacy.split("|")
        if item.strip()
    ]


def _normalize_channel_input(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://t\.me/s/", "", text, flags=re.I)
    text = re.sub(r"^https?://t\.me/", "", text, flags=re.I)
    return text.strip().strip("/")


def _entity_lookup_value(value: str) -> str | int:
    text = _normalize_channel_input(value)
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text.lstrip("@")


def _legacy_channel_value(item: dict[str, Any]) -> str:
    source = str(item.get("input") or item.get("username") or item.get("name") or "").strip()
    username = str(item.get("username") or "").strip().lstrip("@")
    if username:
        return f"https://t.me/s/{username}"
    return source


def _save_channels(channels: list[dict[str, Any]]) -> dict[str, str]:
    normalized = [_normalize_saved_channel(item) for item in channels if isinstance(item, dict)]
    compact = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    legacy = "|".join(
        _legacy_channel_value(item)
        for item in normalized
        if item.get("enabled") is not False
        and _normalize_channel_mode(item.get("mode")) in CHANNEL_AUTO_MONITOR_MODES
        and _legacy_channel_value(item)
    )
    return write_config({"ENV_TG_CHANNELS": compact, "ENV_115_TG_CHANNEL": legacy})


def _channel_match_keys(item: dict[str, Any]) -> set[str]:
    return {
        str(value or "").strip().lower().lstrip("@")
        for value in [item.get("input"), item.get("username"), item.get("id"), item.get("name")]
        if str(value or "").strip()
    }


async def _channel_photo_url(client, entity) -> str:
    if not getattr(entity, "photo", None):
        return ""
    entity_id = str(getattr(entity, "id", "") or "").strip()
    if not entity_id:
        return ""
    CHANNEL_ICON_DIR.mkdir(parents=True, exist_ok=True)
    image = await client.download_profile_photo(entity, file=bytes, download_big=False)
    if not image:
        return ""
    path = CHANNEL_ICON_DIR / f"{re.sub(r'[^0-9A-Za-z_-]+', '_', entity_id)}.jpg"
    path.write_bytes(image)
    return f"/api/telegram/channel-icons/{path.name}"


def _existing_channel_for(row: str, entity, existing_channels: list[dict[str, Any]]) -> dict[str, Any]:
    keys = {row.strip().lower().lstrip("@"), str(getattr(entity, "id", "") or "").lower()}
    username = str(getattr(entity, "username", "") or "").strip().lower().lstrip("@")
    if username:
        keys.add(username)
    for item in existing_channels:
        if keys & _channel_match_keys(item):
            return item
    return {}


async def _resolve_channel(client, row: str, existing_channels: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    existing_channels = existing_channels or []
    entity = await client.get_entity(_entity_lookup_value(row))
    username = getattr(entity, "username", "") or ""
    title = getattr(entity, "title", "") or _display_user(entity)
    existing = _existing_channel_for(row, entity, existing_channels)
    photo_url = await _channel_photo_url(client, entity) or str(existing.get("photo_url") or "")
    return _normalize_saved_channel({
        "input": row,
        "name": title or username or row,
        "id": str(getattr(entity, "id", "") or ""),
        "username": username,
        "photo_url": photo_url,
    }, existing)


async def _refresh_channel_photos(client, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changed = False
    refreshed: list[dict[str, Any]] = []
    for item in channels:
        if item.get("photo_url"):
            refreshed.append(item)
            continue
        source = str(item.get("input") or item.get("username") or item.get("id") or "").strip()
        if not source:
            refreshed.append(item)
            continue
        try:
            resolved = await _resolve_channel(client, source, channels)
        except Exception:
            refreshed.append(item)
            continue
        refreshed.append({**item, **resolved})
        changed = changed or bool(resolved.get("photo_url"))
    if changed:
        _save_channels(refreshed)
    return refreshed


def telegram_status() -> dict[str, Any]:
    async def _inner():
        config = read_config()
        api_id_raw = str(config.get("ENV_TG_API_ID") or "").strip()
        api_hash = str(config.get("ENV_TG_API_HASH") or "").strip()
        if not api_id_raw or not api_hash:
            return {"ok": True, "authorized": False, "user": None, "channels": _read_channels_from_config()}
        try:
            api_id = int(api_id_raw)
        except ValueError:
            return {"ok": True, "authorized": False, "user": None, "channels": _read_channels_from_config()}
        async with _connected_client(api_id, api_hash) as client:
            authorized = await client.is_user_authorized()
            user = None
            channels = _read_channels_from_config()
            if authorized:
                me = await client.get_me()
                user = {
                    "id": getattr(me, "id", ""),
                    "username": getattr(me, "username", "") or "",
                    "display": _display_user(me),
                    "phone": getattr(me, "phone", "") or "",
                }
                channels = await _refresh_channel_photos(client, channels)
            return {"ok": True, "authorized": authorized, "user": user, "channels": channels}

    return _run(_inner())


def send_login_code(payload: dict[str, Any]) -> dict[str, Any]:
    async def _inner():
        phone, api_id, api_hash = _credentials(payload)
        if not phone:
            raise ValueError("请填写手机号，例如 +8618000000000")
        try:
            async with _connected_client(api_id, api_hash, connection_retries=3, request_retries=2, timeout=15) as client:
                sent = await client.send_code_request(phone)
        except Exception as exc:
            raise _telegram_request_error(exc) from exc
        LOGIN_STATE_PATH.write_text(json.dumps({
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "phone_code_hash": sent.phone_code_hash,
        }, ensure_ascii=False), encoding="utf-8")
        write_config({"ENV_TG_PHONE": phone, "ENV_TG_API_ID": str(api_id), "ENV_TG_API_HASH": api_hash})
        return {"ok": True, "message": "验证码已发送"}

    return _run(_inner())


def sign_in(payload: dict[str, Any]) -> dict[str, Any]:
    async def _inner():
        _, SessionPasswordNeededError = _telethon()
        code = str(payload.get("code") or "").strip()
        if not code:
            raise ValueError("请输入验证码")
        if not LOGIN_STATE_PATH.exists():
            raise ValueError("请先发送验证码")
        state = json.loads(LOGIN_STATE_PATH.read_text(encoding="utf-8"))
        phone = state.get("phone") or ""
        api_id = int(state.get("api_id") or 0)
        api_hash = state.get("api_hash") or ""
        try:
            async with _connected_client(api_id, api_hash, connection_retries=2, request_retries=1, timeout=12) as client:
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=state.get("phone_code_hash"))
                except SessionPasswordNeededError as exc:
                    raise ValueError("该 Telegram 账号开启了两步验证，当前页面暂未支持密码登录。") from exc
                me = await client.get_me()
        except ValueError:
            raise
        except Exception as exc:
            raise _telegram_request_error(exc) from exc
        LOGIN_STATE_PATH.unlink(missing_ok=True)
        display = _display_user(me)
        write_config({"ENV_TG_PHONE": display, "ENV_TG_API_ID": str(api_id), "ENV_TG_API_HASH": api_hash})
        return {
            "ok": True,
            "authorized": True,
            "user": {
                "id": getattr(me, "id", ""),
                "username": getattr(me, "username", "") or "",
                "display": display,
                "phone": getattr(me, "phone", "") or "",
            },
        }

    return _run(_inner())


def logout() -> dict[str, Any]:
    async def _inner():
        config = read_config()
        api_id_raw = str(config.get("ENV_TG_API_ID") or "").strip()
        api_hash = str(config.get("ENV_TG_API_HASH") or "").strip()
        if api_id_raw and api_hash:
            try:
                async with _connected_client(int(api_id_raw), api_hash) as client:
                    if await client.is_user_authorized():
                        await client.log_out()
            except Exception:
                pass
        LOGIN_STATE_PATH.unlink(missing_ok=True)
        for path in DATA_DIR.glob("telegram.session*"):
            try:
                path.unlink()
            except OSError:
                pass
        write_config({"ENV_TG_PHONE": ""})
        return {"ok": True, "authorized": False}

    return _run(_inner())


def save_channels(payload: dict[str, Any]) -> dict[str, Any]:
    async def _inner():
        raw_channels = payload.get("channels") or []
        if isinstance(raw_channels, str):
            raw_channels = [line for line in raw_channels.splitlines() if line.strip()]
        if payload.get("resolve") is False and isinstance(raw_channels, list):
            existing = _read_channels_from_config()
            saved: list[dict[str, Any]] = []
            for item in raw_channels:
                if not isinstance(item, dict):
                    continue
                source = _normalize_channel_input(item.get("input") or item.get("username") or item.get("id") or item.get("name") or "")
                if not source:
                    continue
                fallback = {}
                for current in existing:
                    if source.strip().lower().lstrip("@") in _channel_match_keys(current):
                        fallback = current
                        break
                saved.append(_normalize_saved_channel({**item, "input": source}, fallback))
            _save_channels(saved)
            return {"ok": True, "channels": saved}
        _, api_id, api_hash = _credentials(payload)
        rows = [_normalize_channel_input(item.get("input") if isinstance(item, dict) else item) for item in raw_channels]
        rows = [row for row in rows if row]
        resolved: list[dict[str, Any]] = []
        async with _connected_client(api_id, api_hash) as client:
            if not await client.is_user_authorized():
                raise ValueError("请先完成 Telegram 登录")
            existing = _read_channels_from_config()
            for row in rows:
                resolved_item = await _resolve_channel(client, row, existing)
                matched = {}
                for current in existing:
                    if row.strip().lower().lstrip("@") in _channel_match_keys(current):
                        matched = current
                        break
                resolved.append(_normalize_saved_channel(resolved_item, matched))
        _save_channels(resolved)
        return {"ok": True, "channels": resolved}

    return _run(_inner())


def list_channels() -> dict[str, Any]:
    return {"ok": True, "channels": _read_channels_from_config()}


def delete_channel(index: int) -> dict[str, Any]:
    channels = _read_channels_from_config()
    if index < 0 or index >= len(channels):
        raise ValueError("频道不存在")
    channels.pop(index)
    _save_channels(channels)
    return {"ok": True, "channels": channels}


def reorder_channels(source_index: int, target_index: int) -> dict[str, Any]:
    channels = _read_channels_from_config()
    if source_index < 0 or source_index >= len(channels):
        raise ValueError("频道不存在")
    target_index = max(0, min(target_index, len(channels) - 1))
    item = channels.pop(source_index)
    channels.insert(target_index, item)
    _save_channels(channels)
    return {"ok": True, "channels": channels}
