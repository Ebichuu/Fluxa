from __future__ import annotations

import ipaddress
import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote, urlencode, urljoin, urlsplit

import requests

from app.config import read_config


REQUEST_TIMEOUT_SECONDS = 12
LIBRARY_CACHE_TTL_SECONDS = 10 * 60
AUTHORIZATION_HEADER = (
    'MediaBrowser Client="MediaControlCenter", Device="mcc-server", '
    'DeviceId="mcc-server", Version="0.1"'
)


@dataclass(frozen=True)
class EmbyConfig:
    base_url: str = ""
    api_key: str = ""
    user_id: str = ""
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class EmbyCredentials:
    token: str
    user_id: str


@dataclass(frozen=True)
class ImageFetchResult:
    status: int
    content: bytes
    content_type: str


_auth_cache: dict[str, EmbyCredentials] = {}
_auth_cache_lock = threading.Lock()


def resolve_emby_config(environment=None, runtime_config=None) -> EmbyConfig:
    use_process_environment = environment is None or environment is os.environ
    environment = os.environ if environment is None else environment
    if runtime_config is None:
        runtime_config = read_config() if use_process_environment else {}
    return EmbyConfig(
        base_url=str(
            environment.get("EMBY_BASE_URL")
            or runtime_config.get("ENV_EMBY_SERVER_URL")
            or ""
        ).strip(),
        api_key=str(
            environment.get("EMBY_API_KEY")
            or runtime_config.get("ENV_EMBY_API_KEY")
            or ""
        ).strip(),
        user_id=str(environment.get("EMBY_USER_ID") or "").strip(),
        username=str(
            environment.get("EMBY_USERNAME")
            or runtime_config.get("ENV_MEDIA_LIBRARY_ADMIN")
            or ""
        ).strip(),
        password=str(
            environment.get("EMBY_PASSWORD")
            or runtime_config.get("ENV_MEDIA_LIBRARY_PASSWORD")
            or ""
        ).strip(),
    )


def normalize_base_url(base_url: str) -> str:
    return base_url if base_url.endswith("/") else f"{base_url}/"


def is_image_bytes(content: bytes) -> bool:
    if len(content) < 12:
        return False
    return (
        content.startswith(b"\xff\xd8\xff")
        or content.startswith(b"\x89PNG")
        or content.startswith(b"GIF")
        or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
        or b"ftypavif" in content[4:12]
    )


def validate_external_image_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        _ = parsed.port
        hostname = parsed.hostname.lower()
        if hostname == "localhost":
            return None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return value
        if _is_unsafe_address(address):
            return None
        return value
    except (TypeError, ValueError):
        return None


def _is_unsafe_address(address) -> bool:
    return any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


def _ensure_public_hostname(hostname: str):
    addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("外部图片地址无法解析")
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if _is_unsafe_address(address):
            raise ValueError("外部图片地址不允许访问内网")


def fetch_external_image(url: str) -> ImageFetchResult:
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError("无效的外部图片地址")
    _ensure_public_hostname(parsed.hostname)
    response = requests.get(
        url,
        headers={"Accept": "image/*", "User-Agent": "MediaControlCenter/0.1"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    return ImageFetchResult(
        status=response.status_code,
        content=response.content,
        content_type=response.headers.get("Content-Type") or "image/jpeg",
    )


def _year(item: dict) -> str:
    production_year = item.get("ProductionYear")
    if production_year:
        return str(production_year)
    premiere_date = str(item.get("PremiereDate") or "").strip()
    if premiere_date:
        try:
            return str(datetime.fromisoformat(premiere_date.replace("Z", "+00:00")).year)
        except ValueError:
            pass
    return "未知年份"


def _media_kind(value) -> str:
    return value if value in {"Series", "Episode", "Video"} else "Movie"


def _rating(value) -> str:
    if not value:
        return "N/A"
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return "N/A"


def _image_url(item_id: str, image_type: str) -> str:
    max_width = "1920" if image_type == "Backdrop" else "780"
    return f"/api/media/image/{quote(item_id, safe='')}/{image_type.lower()}?maxWidth={max_width}"


def _tmdb_id(item: dict) -> str:
    provider_ids = item.get("ProviderIds")
    if not isinstance(provider_ids, dict):
        return ""
    return str(provider_ids.get("Tmdb") or provider_ids.get("tmdb") or "").strip()


class EmbyClient:
    def __init__(self, config: EmbyConfig, session=None):
        self.config = config
        self.server_url = config.base_url
        self.http = session or requests
        self._library_cache: dict[str, tuple[float, object]] = {}
        self._library_cache_lock = threading.Lock()

    def reconfigure(self, config: EmbyConfig) -> None:
        self.config = config
        self.server_url = config.base_url
        with _auth_cache_lock:
            _auth_cache.clear()
        with self._library_cache_lock:
            self._library_cache.clear()

    def _use_api_key(self) -> bool:
        return bool(self.config.api_key and self.config.user_id)

    def _use_password(self) -> bool:
        return bool(self.config.username and self.config.password)

    def is_configured(self) -> bool:
        return bool(self.config.base_url and (self._use_api_key() or self._use_password()))

    def _auth_cache_key(self) -> str:
        return f"{self.config.base_url}|{self.config.username}"

    def get_credentials(self) -> EmbyCredentials | None:
        if not self.config.base_url:
            return None
        if self._use_api_key():
            return EmbyCredentials(self.config.api_key, self.config.user_id)
        if not self._use_password():
            return None
        cache_key = self._auth_cache_key()
        with _auth_cache_lock:
            cached = _auth_cache.get(cache_key)
        if cached:
            return cached
        try:
            response = self.http.request(
                "POST",
                urljoin(normalize_base_url(self.config.base_url), "Users/AuthenticateByName"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Emby-Authorization": AUTHORIZATION_HEADER,
                },
                json={"Username": self.config.username, "Pw": self.config.password},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Emby 登录请求失败") from exc
        self._raise_for_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Emby 登录返回了无效 JSON") from exc
        token = str(payload.get("AccessToken") or "") if isinstance(payload, dict) else ""
        user = payload.get("User") if isinstance(payload, dict) else {}
        user_id = str(user.get("Id") or "") if isinstance(user, dict) else ""
        if not token or not user_id:
            raise RuntimeError("Emby 登录失败：未返回 AccessToken")
        credentials = EmbyCredentials(token, user_id)
        with _auth_cache_lock:
            _auth_cache[cache_key] = credentials
        return credentials

    def _clear_password_credentials(self):
        with _auth_cache_lock:
            _auth_cache.pop(self._auth_cache_key(), None)

    @staticmethod
    def _raise_for_status(response):
        if response.status_code >= 400:
            raise RuntimeError(f"Emby 响应异常：{response.status_code}")

    def _request(self, pathname: str, params=None, retry=True):
        credentials = self.get_credentials()
        if not credentials:
            raise RuntimeError("Emby 未配置")
        query = {"api_key": credentials.token, **(params or {})}
        url = urljoin(
            normalize_base_url(self.config.base_url),
            pathname.replace("{userId}", quote(credentials.user_id, safe="")),
        )
        url = f"{url}?{urlencode(query)}"
        try:
            response = self.http.request(
                "GET",
                url,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Emby 请求失败") from exc
        if retry and self._use_password() and response.status_code in {401, 403}:
            self._clear_password_credentials()
            return self._request(pathname, params, retry=False)
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Emby 返回了无效 JSON") from exc

    def get_libraries(self) -> list[dict]:
        if not self.is_configured():
            return []
        payload = self._request("Users/{userId}/Views")
        rows = payload.get("Items") if isinstance(payload, dict) else []
        result = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("Id") or "")
            name = str(item.get("Name") or "")
            if not item_id or not name:
                continue
            primary = _image_url(item_id, "Primary") if (item.get("ImageTags") or {}).get("Primary") else ""
            backdrop = _image_url(item_id, "Backdrop") if item.get("BackdropImageTags") else primary
            result.append({
                "id": item_id,
                "name": name,
                "collectionType": item.get("CollectionType") or item.get("Type") or "library",
                "posterUrl": primary or backdrop,
                "backdropUrl": backdrop or primary,
                "itemCount": item.get("RecursiveItemCount") if item.get("RecursiveItemCount") is not None else item.get("ChildCount"),
            })
        return result

    def get_home_media(self, library_id=None, limit=20) -> list[dict]:
        if not self.is_configured():
            return []
        params = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series",
            "Fields": ",".join((
                "PrimaryImageAspectRatio", "PremiereDate", "CommunityRating",
                "Genres", "Overview", "DateCreated", "BackdropImageTags", "ImageTags",
            )),
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "Limit": str(limit),
        }
        if library_id:
            params["ParentId"] = library_id
        payload = self._request("Users/{userId}/Items", params)
        rows = payload.get("Items") if isinstance(payload, dict) else []
        result = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("Id") or "")
            name = str(item.get("Name") or item.get("SeriesName") or "")
            if not item_id or not name:
                continue
            primary = _image_url(item_id, "Primary") if (item.get("ImageTags") or {}).get("Primary") else ""
            backdrop = _image_url(item_id, "Backdrop") if item.get("BackdropImageTags") else primary
            mapped = {
                "id": item_id,
                "title": name,
                "year": _year(item),
                "type": _media_kind(item.get("Type")),
                "genres": list(item.get("Genres") or [])[:3],
                "rating": _rating(item.get("CommunityRating")),
                "posterUrl": primary or backdrop,
                "backdropUrl": backdrop or primary,
                "overview": item.get("Overview"),
                "libraryId": library_id,
                "sourceName": "Emby",
            }
            if mapped["posterUrl"] and mapped["backdropUrl"]:
                result.append(mapped)
        return result

    def get_counts(self) -> dict:
        payload = self._request("Items/Counts")
        return {
            "movies": (payload.get("MovieCount") or 0) if isinstance(payload, dict) else 0,
            "series": (payload.get("SeriesCount") or 0) if isinstance(payload, dict) else 0,
            "episodes": (payload.get("EpisodeCount") or 0) if isinstance(payload, dict) else 0,
        }

    def get_recent_items(self, limit=8) -> list[dict]:
        payload = self._request("Users/{userId}/Items", {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Episode",
            "Fields": "DateCreated",
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "Limit": str(limit),
        })
        rows = payload.get("Items") if isinstance(payload, dict) else []
        result = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict) or not item.get("Id") or not (item.get("Name") or item.get("SeriesName")):
                continue
            item_type = item.get("Type")
            result.append({
                "id": item["Id"],
                "title": item.get("Name") or "",
                "type": item_type if item_type in {"Series", "Episode"} else "Movie",
                "seriesName": item.get("SeriesName") or "",
                "dateCreated": item.get("DateCreated") or "",
            })
        return result

    def get_tmdb_library_index(self) -> dict[str, set[str]]:
        return self._cached("tmdb-library-index", lambda: self._load_tmdb_library_index())

    def _load_tmdb_library_index(self) -> dict[str, set[str]]:
        payload = self._request("Users/{userId}/Items", {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series",
            "Fields": "ProviderIds,Path",
            "Limit": "10000",
        })
        movies: set[str] = set()
        series: set[str] = set()
        rows = payload.get("Items") if isinstance(payload, dict) else []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict) or not item.get("Path"):
                continue
            tmdb_id = _tmdb_id(item)
            if tmdb_id and item.get("Type") == "Movie":
                movies.add(tmdb_id)
            if tmdb_id and item.get("Type") == "Series":
                series.add(tmdb_id)
        return {"movies": movies, "series": series}

    def _cached(self, key: str, loader):
        now = datetime.now().timestamp()
        with self._library_cache_lock:
            cached = self._library_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        value = loader()
        with self._library_cache_lock:
            self._library_cache[key] = (
                datetime.now().timestamp() + LIBRARY_CACHE_TTL_SECONDS,
                value,
            )
        return value

    def trigger_library_refresh(self, retry=True):
        credentials = self.get_credentials()
        if not credentials:
            raise RuntimeError("Emby 未配置")
        url = urljoin(normalize_base_url(self.config.base_url), "Library/Refresh")
        url = f"{url}?{urlencode({'api_key': credentials.token})}"
        try:
            response = self.http.request(
                "POST",
                url,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Emby 刷新请求失败") from exc
        if retry and self._use_password() and response.status_code in {401, 403}:
            self._clear_password_credentials()
            return self.trigger_library_refresh(retry=False)
        self._raise_for_status(response)
        with self._library_cache_lock:
            self._library_cache.clear()

    def fetch_image(self, item_id: str, image_type: str, max_width: str, retry=True) -> ImageFetchResult:
        credentials = self.get_credentials()
        if not credentials:
            return ImageFetchResult(404, b"", "")
        image_path = "Backdrop/0" if image_type == "Backdrop" else "Primary"
        pathname = f"Items/{quote(item_id, safe='')}/Images/{image_path}"
        query = urlencode({"maxWidth": max_width, "quality": "90", "api_key": credentials.token})
        url = f"{urljoin(normalize_base_url(self.config.base_url), pathname)}?{query}"
        response = self.http.request(
            "GET",
            url,
            headers={"Accept": "image/*"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if retry and self._use_password() and response.status_code in {401, 403}:
            self._clear_password_credentials()
            return self.fetch_image(item_id, image_type, max_width, retry=False)
        return ImageFetchResult(
            status=response.status_code,
            content=response.content,
            content_type=response.headers.get("Content-Type") or "image/jpeg",
        )
