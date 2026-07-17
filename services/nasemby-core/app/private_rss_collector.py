from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

from app.private_rss_parser import MAX_FEED_BYTES, parse_private_feed


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


class PrivateRssCollector:
    def __init__(self, repository, session=None, url_validator=None):
        self.repository = repository
        self.session = session or requests.Session()
        self.url_validator = url_validator or validate_feed_url

    def fetch(self, source, persist=True):
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
        response = None
        for _ in range(4):
            self.url_validator(url, allow_http=allow_http)
            response = self.session.get(url, headers=headers, timeout=(5, 20), allow_redirects=False, stream=True)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = str(response.headers.get("Location") or "").strip()
                getattr(response, "close", lambda: None)()
                if not location:
                    raise RuntimeError("RSS 重定向缺少目标")
                url = urljoin(url, location)
                continue
            break
        if response is None or response.status_code in {301, 302, 303, 307, 308}:
            raise RuntimeError("RSS 重定向次数过多")
        if response.status_code == 304:
            if persist:
                self.repository.record_fetch(
                    source["id"], "success", 0, "",
                    response.headers.get("ETag") or "", response.headers.get("Last-Modified") or "",
                )
            getattr(response, "close", lambda: None)()
            return {"status": "not_modified", "items": 0, "title": ""}
        if response.status_code != 200:
            getattr(response, "close", lambda: None)()
            raise RuntimeError(f"RSS 上游返回 {response.status_code}")
        content = bytearray()
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > MAX_FEED_BYTES:
                getattr(response, "close", lambda: None)()
                raise RuntimeError("RSS 响应超过 2 MiB")
        getattr(response, "close", lambda: None)()
        parsed = parse_private_feed(bytes(content))
        result = {"status": "success", "items": len(parsed["items"]), "title": parsed.get("title") or ""}
        if persist:
            changes = self.repository.upsert_items(source["id"], parsed["items"])
            self.repository.record_fetch(
                source["id"], "success", len(parsed["items"]), "",
                response.headers.get("ETag") or "", response.headers.get("Last-Modified") or "",
            )
            result.update(changes)
        return result

    def fetch_source(self, source_id, persist=True):
        source = self.repository.get_source(source_id, public=False)
        if not source:
            raise KeyError("RSS 来源不存在")
        try:
            return self.fetch(source, persist=persist)
        except Exception as exc:
            if persist:
                self.repository.record_fetch(source_id, "error", 0, type(exc).__name__)
            raise RuntimeError("RSS 获取或解析失败") from exc

    def run_due(self):
        results = []
        for source in self.repository.due_sources():
            try:
                results.append({"sourceId": source["id"], **self.fetch(source, persist=True)})
            except Exception:
                results.append({"sourceId": source["id"], "status": "error"})
        self.repository.cleanup()
        return results
