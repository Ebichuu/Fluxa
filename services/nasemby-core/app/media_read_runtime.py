from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request

from app.emby_runtime import (
    EmbyClient,
    fetch_external_image,
    is_image_bytes,
    resolve_emby_config,
    validate_external_image_url,
)
from app.fallback_media import FALLBACK_LIBRARIES, FALLBACK_MEDIA


IMAGE_PLACEHOLDER = "".join((
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 450">',
    '<rect width="320" height="450" fill="#101417"/>',
    '<path d="M62 332h196" stroke="#2a3238" stroke-width="10" stroke-linecap="round"/>',
    '<rect x="78" y="74" width="164" height="214" rx="18" fill="#171d22" ',
    'stroke="#344048" stroke-width="6"/>',
    '<circle cx="160" cy="164" r="34" fill="#26313a"/>',
    '<path d="M96 262l48-58 35 38 26-28 37 48z" fill="#293640"/>',
    "</svg>",
))


def _sample_library_id(library_id=None):
    if library_id and any(item["id"] == library_id for item in FALLBACK_LIBRARIES):
        return library_id
    return FALLBACK_LIBRARIES[0]["id"] if FALLBACK_LIBRARIES else None


def sample_home_media(library_id=None, error=None):
    active_library_id = _sample_library_id(library_id)
    items = [item for item in FALLBACK_MEDIA if item.get("libraryId") == active_library_id]
    return {
        "items": deepcopy(items or FALLBACK_MEDIA),
        "libraries": deepcopy(FALLBACK_LIBRARIES),
        "activeLibraryId": active_library_id,
        "source": "sample",
        "configured": False,
        **({"error": error} if error else {}),
    }


def get_home_media(client, library_id=None):
    if not client.is_configured():
        return sample_home_media(library_id)
    try:
        libraries = client.get_libraries()
        active_library_id = (
            library_id
            if library_id and any(item.get("id") == library_id for item in libraries)
            else (libraries[0].get("id") if libraries else None)
        )
        active_library = next(
            (item for item in libraries if item.get("id") == active_library_id),
            None,
        )
        items = []
        for item in client.get_home_media(active_library_id, 20):
            mapped = dict(item)
            mapped["libraryId"] = (active_library or {}).get("id") or item.get("libraryId")
            mapped["libraryName"] = (active_library or {}).get("name") or item.get("libraryName")
            items.append(mapped)
        if not libraries and not items:
            result = sample_home_media(error="Emby 已配置，但没有返回可展示媒体库。")
            result["configured"] = True
            return result
        first_item = items[0] if items else None
        mapped_libraries = []
        for library in libraries:
            mapped = dict(library)
            if first_item and library.get("id") == active_library_id:
                mapped["posterUrl"] = library.get("posterUrl") or first_item.get("posterUrl")
                mapped["backdropUrl"] = library.get("backdropUrl") or first_item.get("backdropUrl")
            mapped_libraries.append(mapped)
        return {
            "items": items,
            "libraries": mapped_libraries,
            "activeLibraryId": active_library_id,
            "source": "emby",
            "configured": True,
        }
    except Exception as exc:
        result = sample_home_media(error=str(exc) or "Emby 首页数据读取失败。")
        result["configured"] = True
        return result


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _placeholder_response():
    response = Response(IMAGE_PLACEHOLDER, content_type="image/svg+xml; charset=utf-8")
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


def _image_response(result, strict=False):
    if not 200 <= result.status < 300:
        return Response(status=result.status)
    if not is_image_bytes(result.content):
        return Response(status=204) if strict else _placeholder_response()
    response = Response(result.content, content_type=result.content_type or "image/jpeg")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def _safe_max_width(value, fallback):
    return value if isinstance(value, str) and value.isdigit() and 2 <= len(value) <= 4 else fallback


def register_emby_reads(
    app: Flask,
    environment=None,
    client_factory=None,
    external_image_fetcher=None,
    clock=None,
):
    config = resolve_emby_config(environment)
    client = (client_factory or EmbyClient)(config)
    fetch_external = external_image_fetcher or fetch_external_image
    now = clock or (lambda: datetime.now(timezone.utc))
    app.extensions["mcc_emby_client"] = client

    @app.get("/api/media/home")
    def media_home():
        return jsonify(get_home_media(client, request.args.get("libraryId")))

    @app.get("/api/media/emby/overview")
    def emby_overview():
        if not client.is_configured():
            return jsonify({"configured": False})
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                counts_future = executor.submit(client.get_counts)
                recent_future = executor.submit(client.get_recent_items, 8)
                counts = counts_future.result()
                recent = recent_future.result()
            return jsonify({
                "configured": True,
                "connected": True,
                "counts": counts,
                "recent": recent,
                "serverUrl": client.server_url,
                "lastCheckedAt": _iso_timestamp(now()),
            })
        except Exception as exc:
            return jsonify({
                "configured": True,
                "connected": False,
                "error": str(exc) or "Emby 读取失败",
                "lastCheckedAt": _iso_timestamp(now()),
            })

    @app.get("/api/media/external-image")
    def external_image():
        image_url = validate_external_image_url(request.args.get("src"))
        if not image_url:
            return Response(status=400)
        result = fetch_external(image_url)
        if not 200 <= result.status < 300:
            return _placeholder_response()
        return _image_response(result, strict=True)

    @app.get("/api/media/image/<item_id>/<image_type>")
    def emby_image(item_id, image_type):
        if not client.is_configured():
            return Response(status=404)
        resolved_type = "Backdrop" if image_type == "backdrop" else "Primary"
        max_width = _safe_max_width(
            request.args.get("maxWidth"),
            "1920" if resolved_type == "Backdrop" else "780",
        )
        result = client.fetch_image(item_id, resolved_type, max_width)
        return _image_response(result, strict=request.args.get("strict") == "1")

    return client
