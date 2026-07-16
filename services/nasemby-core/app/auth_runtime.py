from __future__ import annotations

import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Flask, g, jsonify, make_response, redirect, render_template, request

from app.access_auth import AccessAuth, COOKIE_NAME
from app.login_page import safe_next_location


DANGEROUS_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_ROUTES = {
    ("GET", "/healthz"),
    ("GET", "/auth/login"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/api/auth/session"),
}


def _json_error(message: str, code: str, status: int):
    return jsonify({"error": message, "code": code}), status


def _is_html_page_request():
    if request.method not in {"GET", "HEAD"}:
        return False
    destination = request.headers.get("Sec-Fetch-Dest", "")
    if destination in {"document", "iframe"}:
        return True
    return "text/html" in request.headers.get("Accept", "").lower()


def _set_session_cookie(response, auth: AccessAuth, token: str):
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=604800,
        httponly=True,
        samesite="Strict",
        secure=auth.config.cookie_secure,
        path="/",
    )


def _clear_session_cookie(response, auth: AccessAuth):
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        samesite="Strict",
        secure=auth.config.cookie_secure,
        path="/",
    )


def _login_page(next_path: str, error: str = "", status: int = 200):
    nonce = secrets.token_urlsafe(16)
    response = make_response(render_template(
        "login.html",
        next_path=next_path,
        nonce=nonce,
        error=error,
    ), status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def _iso_timestamp(milliseconds: int):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def configure_access_runtime(app: Flask, auth: AccessAuth):
    app.extensions["mcc_access_auth"] = auth

    @app.before_request
    def enforce_origin_and_authentication():
        origin = request.headers.get("Origin")
        if origin:
            request_origin = request.host_url.rstrip("/")
            allowed = origin == request_origin or origin in auth.config.allowed_origins
            if allowed:
                g.mcc_cors_origin = origin
            if request.method == "OPTIONS":
                return ("", 204) if allowed else _json_error("来源不允许", "ORIGIN_FORBIDDEN", 403)
            if request.method in DANGEROUS_METHODS and not allowed:
                return _json_error("来源不允许", "ORIGIN_FORBIDDEN", 403)

        if (request.method, request.path) in PUBLIC_ROUTES or not auth.config.enabled:
            return None

        session, invalid_cookie = auth.read_session(request.cookies.get(COOKIE_NAME))
        if session is not None:
            g.mcc_access_session = session
            return None

        if request.path.startswith("/api/") or not _is_html_page_request():
            response = make_response(jsonify({"error": "需要登录", "code": "AUTH_REQUIRED"}), 401)
        else:
            next_path = request.full_path.rstrip("?") or "/"
            response = redirect(
                f"/auth/login?next={quote(next_path, safe='')}",
                code=303,
            )
        if invalid_cookie:
            _clear_session_cookie(response, auth)
        return response

    @app.after_request
    def add_access_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        cors_origin = getattr(g, "mcc_cors_origin", "")
        if cors_origin:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Accept, Content-Type"
            response.vary.add("Origin")
        return response

    @app.get("/healthz")
    def access_healthz():
        return jsonify({"status": "ok"})

    @app.get("/auth/login")
    def access_login_page():
        next_path = safe_next_location(request.args.get("next"))
        session, invalid_cookie = auth.read_session(request.cookies.get(COOKIE_NAME))
        if session is not None:
            return redirect(next_path, code=303)
        response = _login_page(next_path)
        if invalid_cookie:
            _clear_session_cookie(response, auth)
        return response

    @app.post("/auth/login")
    def access_login_submit():
        if request.content_length is not None and request.content_length > 2048:
            return _json_error("请求内容过大", "PAYLOAD_TOO_LARGE", 413)
        form = request.form
        if len(form) > 4:
            return _json_error("请求内容过大", "PAYLOAD_TOO_LARGE", 413)
        next_path = safe_next_location(form.get("next"))
        result = auth.attempt_login(str(form.get("access_key") or ""), request.remote_addr or "unknown")
        if result.status == "success":
            response = redirect(next_path, code=303)
            response.headers["Cache-Control"] = "no-store"
            _set_session_cookie(response, auth, result.token)
            return response
        locked = result.status == "locked"
        response = _login_page(
            next_path,
            "尝试次数过多，请稍后再试" if locked else "访问密钥不正确",
            429 if locked else 401,
        )
        if locked:
            response.headers["Retry-After"] = "900"
        return response

    @app.post("/auth/logout")
    def access_logout():
        response = redirect("/auth/login", code=303)
        response.headers["Cache-Control"] = "no-store"
        _clear_session_cookie(response, auth)
        return response

    @app.get("/api/auth/session")
    def access_session_status():
        session, invalid_cookie = auth.read_session(request.cookies.get(COOKIE_NAME))
        response = make_response(jsonify({
            "enabled": auth.config.enabled,
            "authenticated": session is not None,
            "expiresAt": _iso_timestamp(session.expires_at) if session else None,
        }))
        response.headers["Cache-Control"] = "no-store"
        if invalid_cookie:
            _clear_session_cookie(response, auth)
        return response

    return app
