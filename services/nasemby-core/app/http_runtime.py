from __future__ import annotations

import logging
import re
from uuid import uuid4

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException


logger = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HTTP_ERRORS = {
    400: ("BAD_REQUEST", "请求无效"),
    401: ("UNAUTHORIZED", "请求未授权"),
    403: ("FORBIDDEN", "请求被拒绝"),
    404: ("NOT_FOUND", "请求的接口不存在"),
    405: ("METHOD_NOT_ALLOWED", "请求方法不允许"),
    413: ("PAYLOAD_TOO_LARGE", "请求内容过大"),
    429: ("TOO_MANY_REQUESTS", "请求过于频繁"),
}


def _request_id():
    candidate = str(request.headers.get(REQUEST_ID_HEADER) or "").strip()
    if REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def current_request_id():
    return str(getattr(g, "request_id", "") or uuid4().hex)


def configure_http_runtime(app: Flask):
    @app.before_request
    def assign_request_id():
        g.request_id = _request_id()

    @app.after_request
    def attach_request_id(response):
        response.headers[REQUEST_ID_HEADER] = current_request_id()
        return response

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        if not request.path.startswith("/api/"):
            return error
        status = int(error.code or 500)
        code, message = HTTP_ERRORS.get(status, (f"HTTP_{status}", "请求失败"))
        return jsonify({
            "code": code,
            "error": message,
            "request_id": current_request_id(),
        }), status

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        request_id = current_request_id()
        safe_path = request.path[:200].replace("\r", "").replace("\n", "")
        logger.error(
            "unhandled request error request_id=%s method=%s path=%s error_type=%s",
            request_id,
            request.method,
            safe_path,
            type(error).__name__,
        )
        return jsonify({
            "code": "INTERNAL_ERROR",
            "error": "服务内部错误",
            "request_id": request_id,
        }), 500

    return app
