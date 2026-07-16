from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, send_from_directory
from werkzeug.utils import safe_join


RESERVED_PREFIXES = ("api/", "auth/", "mineradio/", "static/")


def register_frontend(app: Flask, frontend_dist):
    if not frontend_dist:
        return False
    directory = Path(frontend_dist).resolve()
    if not (directory / "index.html").is_file():
        return False

    def send_index():
        response = send_from_directory(directory, "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def react_index():
        return send_index()

    @app.get("/<path:asset_path>")
    def react_asset_or_spa(asset_path):
        if asset_path.startswith(RESERVED_PREFIXES):
            abort(404)
        resolved = safe_join(str(directory), asset_path)
        if resolved and Path(resolved).is_file():
            response = send_from_directory(directory, asset_path)
            if asset_path.startswith("assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=3600"
            return response
        return send_index()

    return True
