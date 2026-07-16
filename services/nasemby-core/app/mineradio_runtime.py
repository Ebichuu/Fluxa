from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, Response, send_from_directory
from werkzeug.utils import safe_join


def bundled_mineradio_public_dir(module_file=__file__) -> Path:
    core_root = Path(module_file).resolve().parents[1]
    install_root = core_root.parent.parent if core_root.parent.name == "services" else core_root
    return (install_root / "vendor" / "mineradio-public").resolve()


BUNDLED_MINERADIO_PUBLIC_DIR = bundled_mineradio_public_dir()
WINDOWS_MINERADIO_PUBLIC_DIR = Path(r"D:\Mineradio\resources\app\public")
EMBED_FRAGMENT_DIR = Path(__file__).resolve().with_name("mineradio_fragments")
EMBED_FRAGMENT_NAMES = frozenset({"embed-head.html", "embed-tail.html"})


def resolve_mineradio_public_dir(environment=None) -> Path:
    environment = os.environ if environment is None else environment
    configured = str(environment.get("MINERADIO_PUBLIC_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if (BUNDLED_MINERADIO_PUBLIC_DIR / "index.html").is_file():
        return BUNDLED_MINERADIO_PUBLIC_DIR
    return WINDOWS_MINERADIO_PUBLIC_DIR


def read_embed_fragment(name: str, fragment_dir=None) -> str:
    if name not in EMBED_FRAGMENT_NAMES:
        raise ValueError(f"不支持的 Mineradio 注入片段：{name}")
    directory = EMBED_FRAGMENT_DIR if fragment_dir is None else Path(fragment_dir)
    return (directory / name).read_text(encoding="utf-8")


def tune_embed_source(html: str) -> str:
    replacements = (
        ("cv.width = 720; cv.height = 360;", "cv.width = 660; cv.height = 360;"),
        (
            "var geo = new THREE.PlaneGeometry(2.05, 1.025, 1, 1);",
            "var geo = new THREE.PlaneGeometry(1.88, 1.025, 1, 1);",
        ),
        (
            "var coverSize = H - pad*2 - 8;",
            "var coverSize = Math.min(H - pad*2 - 8, W * 0.44);",
        ),
        ("var tx = pad + coverSize + 32;", "var tx = pad + coverSize + 24;"),
        (
            "ctx.drawImage(rec.img, cx, cy, coverSize, coverSize); ctx.restore();",
            "if (window.mccDrawShelfCoverContain) "
            "window.mccDrawShelfCoverContain(ctx, rec.img, cx, cy, coverSize, coverSize); "
            "else ctx.drawImage(rec.img, cx, cy, coverSize, coverSize); ctx.restore();",
        ),
    )
    for original, replacement in replacements:
        html = html.replace(original, replacement, 1)
    return html


def build_embed_html(source: str, fragment_dir=None) -> str:
    tuned = tune_embed_source(source)
    head = read_embed_fragment("embed-head.html", fragment_dir)
    tail = read_embed_fragment("embed-tail.html", fragment_dir)
    return tuned.replace("<head>", f"<head>{head}", 1).replace(
        "</body>",
        f"{tail}</body>",
        1,
    )


def register_mineradio(app: Flask, public_dir=None, environment=None, fragment_dir=None) -> Path:
    directory = (
        resolve_mineradio_public_dir(environment)
        if public_dir is None
        else Path(public_dir).expanduser().resolve()
    )

    @app.get("/mineradio/embed")
    def mineradio_embed():
        index_path = directory / "index.html"
        if not index_path.is_file():
            return Response(
                f"Mineradio index.html not found: {index_path}",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        html = build_embed_html(index_path.read_text(encoding="utf-8"), fragment_dir)
        response = Response(html, content_type="text/html; charset=utf-8")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/mineradio/<path:asset_path>")
    def mineradio_asset(asset_path):
        resolved = safe_join(str(directory), asset_path)
        if not resolved or not Path(resolved).is_file():
            return Response("Not Found", status=404, content_type="text/plain; charset=utf-8")
        response = send_from_directory(directory, asset_path)
        response.headers["Cache-Control"] = "public, max-age=0"
        return response

    return directory
