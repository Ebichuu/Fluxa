from __future__ import annotations

import ipaddress
import math
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import requests

from app.private_rss_parser import MAX_FEED_BYTES, parse_private_feed
from app.private_rss_repository import FetchRunRecord


REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _unsafe_address(address):
    return any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


def validate_feed_url(url, allow_http=False):
    parsed = urlsplit(str(url or ""))
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("RSS 地址不符合访问规则")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443} and not allow_http:
        raise ValueError("非标准端口需要明确允许")
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("RSS 域名无法解析")
    for entry in addresses:
        if _unsafe_address(ipaddress.ip_address(entry[4][0])):
            raise ValueError("RSS 地址不允许访问内网")
    return str(url)


class SourceFetchInProgressError(RuntimeError):
    pass


class UpstreamHttpError(RuntimeError):
    def __init__(self, status_code, retry_after_seconds=None):
        super().__init__(f"RSS 上游返回 {status_code}")
        self.status_code = int(status_code)
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(value, now=None):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0, int(text))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0, math.ceil((target - current).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class PrivateRssCollector:
    def __init__(self, repository, session=None, url_validator=None, item_matcher=None, match_waker=None):
        self.repository = repository
        self.session = session
        self.url_validator = url_validator or validate_feed_url
        self.item_matcher = item_matcher
        self.match_waker = match_waker
        self._global_slots = threading.BoundedSemaphore(2)
        self._source_locks = {}
        self._source_locks_guard = threading.Lock()

    def _source_lock(self, source_id):
        with self._source_locks_guard:
            return self._source_locks.setdefault(str(source_id), threading.Lock())

    def fetch(self, source, persist=True):
        source_lock = self._source_lock(source.get("id"))
        if not source_lock.acquire(blocking=False):
            raise SourceFetchInProgressError("RSS 来源正在抓取")
        try:
            with self._global_slots:
                if self.session is not None:
                    return self._fetch(source, self.session, persist=persist)
                with requests.Session() as session:
                    return self._fetch(source, session, persist=persist)
        finally:
            source_lock.release()

    def _fetch(self, source, session, persist=True):
        url = str(source.get("feed_url") or "")
        allow_http = bool(source.get("allow_http"))
        headers = {
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.2",
            "User-Agent": "MediaControlCenter-RSS/1.0",
        }
        if source.get("etag"):
            headers["If-None-Match"] = str(source["etag"])
        if source.get("last_modified"):
            headers["If-Modified-Since"] = str(source["last_modified"])
        response = self._request(session, url, headers, allow_http)
        if response.status_code == 304:
            return self._not_modified(source, response, persist)
        self._raise_for_status(response)
        content = self._read_content(response)
        parsed = parse_private_feed(content)
        return self._store_result(source, response, parsed, persist)

    def _not_modified(self, source, response, persist):
        if persist:
            self.repository.record_fetch(
                source["id"],
                "success",
                FetchRunRecord(
                    etag=response.headers.get("ETag") or "",
                    last_modified=response.headers.get("Last-Modified") or "",
                    http_status=304,
                ),
            )
        getattr(response, "close", lambda: None)()
        return {"status": "not_modified", "items": 0, "title": ""}

    @staticmethod
    def _raise_for_status(response):
        if response.status_code == 200:
            return
        status_code = int(response.status_code)
        retry_after = _retry_after_seconds(response.headers.get("Retry-After")) if status_code == 429 else None
        getattr(response, "close", lambda: None)()
        raise UpstreamHttpError(status_code, retry_after_seconds=retry_after)

    def _store_result(self, source, response, parsed, persist):
        result = {"status": "success", "items": len(parsed["items"]), "title": parsed.get("title") or ""}
        if persist:
            changes = self.repository.upsert_items(
                source["id"],
                parsed["items"],
                on_insert=self.item_matcher,
            )
            match_ids = changes.pop("_match_ids", [])
            if match_ids and self.match_waker:
                self.match_waker(match_ids)
            self.repository.record_fetch(
                source["id"],
                "success",
                FetchRunRecord(
                    item_count=len(parsed["items"]),
                    etag=response.headers.get("ETag") or "",
                    last_modified=response.headers.get("Last-Modified") or "",
                    http_status=200,
                ),
            )
            result.update(changes)
        return result

    def _request(self, session, url, headers, allow_http):
        response = None
        for _ in range(4):
            self.url_validator(url, allow_http=allow_http)
            response = session.get(url, headers=headers, timeout=(5, 20), allow_redirects=False, stream=True)
            if response.status_code in REDIRECT_STATUSES:
                location = str(response.headers.get("Location") or "").strip()
                getattr(response, "close", lambda: None)()
                if not location:
                    raise RuntimeError("RSS 重定向缺少目标")
                url = urljoin(url, location)
                continue
            return response
        if response is None or response.status_code in REDIRECT_STATUSES:
            raise RuntimeError("RSS 重定向次数过多")

    @staticmethod
    def _read_content(response):
        content = bytearray()
        try:
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > MAX_FEED_BYTES:
                    raise RuntimeError("RSS 响应超过 2 MiB")
            return bytes(content)
        finally:
            getattr(response, "close", lambda: None)()

    def fetch_source(self, source_id, persist=True):
        source = self.repository.get_source(source_id, public=False)
        if not source:
            raise KeyError("RSS 来源不存在")
        try:
            return self.fetch(source, persist=persist)
        except SourceFetchInProgressError:
            raise
        except UpstreamHttpError as exc:
            if persist:
                self.repository.record_fetch(
                    source_id,
                    "error",
                    FetchRunRecord(
                        message=f"http_{exc.status_code}",
                        http_status=exc.status_code,
                        retry_after_seconds=exc.retry_after_seconds,
                    ),
                )
            raise RuntimeError("RSS 获取或解析失败") from exc
        except Exception as exc:
            if persist:
                self.repository.record_fetch(
                    source_id,
                    "error",
                    FetchRunRecord(message=type(exc).__name__),
                )
            raise RuntimeError("RSS 获取或解析失败") from exc

    def run_due(self):
        def fetch_one(source):
            try:
                return {"sourceId": source["id"], **self.fetch_source(source["id"], persist=True)}
            except SourceFetchInProgressError:
                return {"sourceId": source["id"], "status": "skipped"}
            except Exception:
                return {"sourceId": source["id"], "status": "error"}

        sources = self.repository.due_sources()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="private-rss-fetch") as executor:
            results = list(executor.map(fetch_one, sources))
        self.repository.cleanup()
        return results
