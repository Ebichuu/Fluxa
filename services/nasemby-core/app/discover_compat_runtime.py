from __future__ import annotations

import re

from flask import Flask, jsonify, request

from app import discover_runtime
from app.contract_mapping import map_discover_payload, sanitize_resource_payload


DISCOVER_SOURCES = {"tmdb", "daily", "douban", "tencent", "youku", "iqiyi", "mango", "streaming"}
CORE_DISCOVER_SOURCES = {"tmdb", "streaming", "douban", "platform-hot", "daily-airing"}
PLATFORM_LABELS = {
    "tencent": "腾讯视频",
    "youku": "优酷",
    "iqiyi": "爱奇艺",
    "mango": "芒果",
}
TREND_LABELS = {"all": "全部", "day": "日榜", "week": "周榜"}
SORT_LABELS = {
    "popularity_desc": "热度降序",
    "popularity_asc": "热度升序",
    "date_desc": "上映时间降序",
    "date_asc": "上映时间升序",
    "rating_desc": "评分最高",
    "rating_asc": "评分最低",
}
LANGUAGE_LABELS = {
    "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语",
    "de": "德语", "es": "西语", "it": "意语", "ru": "俄语", "pt": "葡语",
    "ar": "阿语", "hi": "印地语", "th": "泰语",
}
GENRE_LABELS = {
    "adventure": "冒险", "fantasy": "奇幻", "animation": "动画", "drama": "剧情",
    "horror": "恐怖", "action": "动作", "comedy": "喜剧", "history": "历史",
    "western": "西部", "thriller": "惊悚", "crime": "犯罪", "documentary": "纪录片",
    "scifi": "科幻", "mystery": "悬疑", "music": "音乐", "romance": "爱情",
    "family": "家庭", "war": "战争",
}


def _bounded_positive(value, fallback, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return str(min(maximum, max(1, parsed)))


def _year_label(value):
    value = str(value or "")
    if re.fullmatch(r"\d{4}", value):
        return value
    match = re.fullmatch(r"(\d{4})s", value)
    if not match:
        return ""
    return "90年代" if match.group(1) == "1990" else "80年代" if match.group(1) == "1980" else f"{match.group(1)}年代"


def _safe_query(args):
    result = {}
    for key, value in list(args.items())[:20]:
        if re.fullmatch(r"[a-z_][a-z0-9_]*", key, re.I) and isinstance(value, str) and len(value) <= 200:
            result[key] = value
    return result


def _browse_target(source, args):
    params = {
        "page": _bounded_positive(args.get("page"), 1, 500),
        "limit": _bounded_positive(args.get("limit"), 16, 24),
    }
    if source == "daily":
        params["timezone"] = "Asia/Shanghai"
        return discover_runtime.fetch_daily_airing, params
    if source in PLATFORM_LABELS:
        params["platform"] = PLATFORM_LABELS[source]
        return discover_runtime.fetch_platform_hot, params
    if source == "douban":
        return discover_runtime.fetch_douban, params
    params["type"] = "movie" if args.get("type") == "movie" else "tv"
    if source == "tmdb":
        params["trend"] = TREND_LABELS.get(str(args.get("trend") or ""), "全部")
    for key, catalog in (("sort", SORT_LABELS), ("language", LANGUAGE_LABELS), ("genre", GENRE_LABELS)):
        mapped = catalog.get(str(args.get(key) or ""))
        if mapped:
            params[key] = mapped
    year = _year_label(args.get("year"))
    if year:
        params["year"] = year
    if source == "streaming":
        params["provider"] = str(args.get("provider") or "netflix")[:40]
        return discover_runtime.fetch_streaming, params
    return discover_runtime.fetch_tmdb, params


def _raw_discover(source, query):
    if source == "tmdb":
        return discover_runtime.fetch_tmdb(query)
    if source == "streaming":
        return discover_runtime.fetch_streaming(query)
    if source == "douban":
        return discover_runtime.fetch_douban(query)
    if source == "platform-hot":
        return discover_runtime.fetch_platform_hot(query)
    if source == "daily-airing":
        return discover_runtime.fetch_daily_airing(query)
    raise LookupError("不支持的 NasEmby 发现来源")


def _discover_error(code, message, status=502):
    return jsonify({"code": code, "error": message}), status


def register_discover_compat(app: Flask):
    @app.get("/api/discover/browse", endpoint="mcc_compat_discover_browse")
    def discover_browse():
        source = request.args.get("source", "tmdb")
        if source not in DISCOVER_SOURCES:
            source = "tmdb"
        try:
            loader, query = _browse_target(source, request.args)
            return jsonify(map_discover_payload(loader(query), source))
        except Exception:
            return _discover_error("NASEMBY_DISCOVER_UNAVAILABLE", "内容发现服务暂不可用")

    @app.get("/api/discover/trending", endpoint="mcc_compat_discover_trending")
    def discover_trending():
        try:
            if not discover_runtime.load_tmdb_config().get("api_key"):
                return jsonify({"configured": False, "results": []})
            media = "movie" if request.args.get("type") == "movie" else "tv"
            payload = discover_runtime.fetch_tmdb({"type": media, "trend": "日榜", "page": "1", "limit": "20"})
            return jsonify(map_discover_payload(payload, "tmdb"))
        except Exception:
            return _discover_error("TMDB_UNAVAILABLE", "TMDB 内容暂不可用")

    @app.get("/api/discover/search", endpoint="mcc_compat_discover_search")
    def discover_search():
        query = str(request.args.get("query") or "").strip()
        if not query:
            return jsonify({"configured": True, "results": []})
        try:
            if not discover_runtime.load_tmdb_config().get("api_key"):
                return jsonify({"configured": False, "results": []})
            payload = discover_runtime.search_media({
                "title": query[:200],
                "page": _bounded_positive(request.args.get("page"), 1, 500),
                "limit": "16",
            })
            return jsonify(map_discover_payload(payload, "tmdb"))
        except Exception:
            return _discover_error("NASEMBY_DISCOVER_SEARCH_UNAVAILABLE", "内容搜索服务暂不可用")

    @app.get("/api/discover/resources/search", endpoint="mcc_compat_discover_resources_search")
    def discover_resources_search():
        title = str(request.args.get("title") or "").strip()
        if not title:
            return _discover_error("DISCOVER_RESOURCE_TITLE_REQUIRED", "缺少资源搜索标题", 400)
        query = {
            "title": title[:200],
            "type": "tv" if request.args.get("type") == "tv" else "movie",
        }
        year = str(request.args.get("year") or "")
        tmdb_id = str(request.args.get("tmdb_id") or "")
        source = str(request.args.get("source") or "")
        if re.fullmatch(r"\d{4}", year):
            query["year"] = year
        if tmdb_id.isdigit():
            query["tmdb_id"] = tmdb_id
        if re.fullmatch(r"[a-z0-9_-]{1,40}", source, re.I):
            query["source"] = source
        try:
            return jsonify(sanitize_resource_payload(discover_runtime.search_resources(query)))
        except Exception:
            return _discover_error("NASEMBY_RESOURCE_SEARCH_UNAVAILABLE", "资源搜索服务暂不可用")

    @app.get("/api/internal/nasemby-core/discover/search", endpoint="mcc_compat_internal_discover_search")
    def internal_discover_search():
        try:
            return jsonify(discover_runtime.search_media(_safe_query(request.args)))
        except Exception:
            return _discover_error("NASEMBY_CORE_UNAVAILABLE", "NasEmby Core 搜索暂不可用")

    @app.get("/api/internal/nasemby-core/discover/resources/search", endpoint="mcc_compat_internal_resource_search")
    def internal_resource_search():
        try:
            return jsonify(discover_runtime.search_resources(_safe_query(request.args)))
        except Exception:
            return _discover_error("NASEMBY_CORE_UNAVAILABLE", "NasEmby Core 资源搜索暂不可用")

    @app.get("/api/internal/nasemby-core/discover/<source>", endpoint="mcc_compat_internal_discover_source")
    def internal_discover_source(source):
        if source not in CORE_DISCOVER_SOURCES:
            return _discover_error("NASEMBY_DISCOVER_SOURCE_NOT_FOUND", "不支持的 NasEmby 发现来源", 404)
        try:
            return jsonify(_raw_discover(source, _safe_query(request.args)))
        except Exception:
            return _discover_error("NASEMBY_CORE_UNAVAILABLE", "NasEmby Core 发现来源暂不可用")

    return app
