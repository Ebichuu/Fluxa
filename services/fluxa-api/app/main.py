from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from app.auth import AuthManager, SESSION_COOKIE
from app.config import REPO_ROOT, resolve_data_dirs
from app.domain.rss_auto_download import RssAutoPoller
from app.routes import api
from app.storage import FluxaStore

PUBLIC_API_PATHS = {"/api/auth/session", "/api/auth/setup", "/api/auth/login", "/api/auth/logout"}


def create_app(
    *,
    db_path: str | Path | None = None,
    frontend_dist: str | Path | None = None,
    enable_poller: bool = True,
) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[assignment]

    _, db_dir = resolve_data_dirs()
    resolved_db = Path(db_path) if db_path else (db_dir / "fluxa_v2.sqlite3")
    store = FluxaStore(resolved_db)
    auth = AuthManager(store)

    app.config["FLUXA_STORE"] = store
    app.config["FLUXA_AUTH"] = auth
    if enable_poller:
        poller = RssAutoPoller(store)
        poller.start()
        app.config["FLUXA_RSS_POLLER"] = poller

    dist_dir = Path(
        frontend_dist
        or os.environ.get("FLUXA_FRONTEND_DIST")
        or os.environ.get("MCC_FRONTEND_DIST")
        or (REPO_ROOT / "dist")
    )

    @app.before_request
    def _enforce_auth():
        path = request.path
        if path == "/healthz" or not path.startswith("/api/"):
            return None
        if path in PUBLIC_API_PATHS:
            return None
        if not auth.is_initialized():
            return jsonify({"error": "请先初始化管理员账号", "code": "AUTH_SETUP_REQUIRED"}), 401
        token = request.cookies.get(SESSION_COOKIE, "")
        if not auth.verify_token(token):
            return jsonify({"error": "请先登录", "code": "AUTH_REQUIRED"}), 401
        return None

    app.register_blueprint(api)

    @app.get("/healthz")
    def healthz():
        return jsonify({
            "ok": True,
            "version": "2.0.0",
            "revision": os.environ.get("FLUXA_BUILD_REVISION", ""),
        })

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def serve_frontend(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "接口不存在"}), 404
        if dist_dir.is_dir():
            target = (dist_dir / path).resolve()
            if path and str(target).startswith(str(dist_dir.resolve())) and target.is_file():
                return send_from_directory(dist_dir, path)
            index_file = dist_dir / "index.html"
            if index_file.is_file():
                return send_from_directory(dist_dir, "index.html")
        return jsonify({"ok": True, "message": "Fluxa v2 API running (frontend dist not built yet)"})

    return app


app = create_app()

if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "12388"))
    app.run(host=host, port=port, debug=os.environ.get("FLUXA_DEBUG") == "1")
