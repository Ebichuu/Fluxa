from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MODULE_ROOT.parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))


class MineradioRuntimeContractTests(unittest.TestCase):
    def test_python_fragments_match_frozen_migration_snapshot(self):
        from app import mineradio_runtime

        fragments = {
            "embed-head.html": "65cc699e6babe4bc602ad2a548680e74ec02b528cc56d398aa297eb887c697d5",
            "embed-tail.html": "dc0cc0d3382c599be6c9ff9acf7e56928cac3d4072a3f7708bef53fe29a88a99",
        }
        for filename, expected in fragments.items():
            content = mineradio_runtime.read_embed_fragment(filename).encode("utf-8")
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected, filename)

    def test_tune_embed_source_preserves_express_replacements(self):
        from app.mineradio_runtime import tune_embed_source

        source = "\n".join((
            "cv.width = 720; cv.height = 360;",
            "var geo = new THREE.PlaneGeometry(2.05, 1.025, 1, 1);",
            "var coverSize = H - pad*2 - 8;",
            "var tx = pad + coverSize + 32;",
            "ctx.drawImage(rec.img, cx, cy, coverSize, coverSize); ctx.restore();",
        ))
        tuned = tune_embed_source(source)

        self.assertIn("cv.width = 660; cv.height = 360;", tuned)
        self.assertIn("new THREE.PlaneGeometry(1.88, 1.025, 1, 1)", tuned)
        self.assertIn("Math.min(H - pad*2 - 8, W * 0.44)", tuned)
        self.assertIn("var tx = pad + coverSize + 24;", tuned)
        self.assertIn("window.mccDrawShelfCoverContain(ctx, rec.img", tuned)

    def test_embed_route_injects_bridge_and_serves_original_assets(self):
        from app import main

        source = """<!doctype html><html><head><title>Mineradio</title></head><body>
<script>
cv.width = 720; cv.height = 360;
var geo = new THREE.PlaneGeometry(2.05, 1.025, 1, 1);
var coverSize = H - pad*2 - 8;
var tx = pad + coverSize + 32;
ctx.drawImage(rec.img, cx, cy, coverSize, coverSize); ctx.restore();
</script></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory)
            (public_dir / "assets").mkdir()
            (public_dir / "index.html").write_text(source, encoding="utf-8")
            (public_dir / "assets" / "contract.js").write_text(
                "window.mineradioContract = true;",
                encoding="utf-8",
            )
            application = main.create_app(mineradio_public_dir=public_dir)
            client = application.test_client()

            response = client.get("/mineradio/embed")
            html = response.get_data(as_text=True)
            asset = client.get("/mineradio/assets/contract.js")
            asset_text = asset.get_data(as_text=True)
            asset.close()
            directory_index = client.get("/mineradio/assets/")
            traversal = client.get("/mineradio/%2e%2e/index.html")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type.split(";", 1)[0], "text/html")
        self.assertIn('<head>\n<base href="/mineradio/">', html)
        self.assertLess(html.index('<base href="/mineradio/">'), html.index("<title>"))
        self.assertLess(html.index("installMediaCenterBridge"), html.index("</body>"))
        self.assertIn("mcc:mineradio-data", html)
        self.assertIn("mineradio:ready", html)
        self.assertIn("mineradio:item-select", html)
        self.assertIn("mineradio:library-select", html)
        self.assertIn("mineradio:visual-fx-change", html)
        self.assertIn("installMediaCenterFetchGuard", html)
        self.assertIn("login|qq|playlist|song|search", html)
        self.assertIn("cv.width = 660; cv.height = 360;", html)
        self.assertNotIn("cv.width = 720; cv.height = 360;", html)
        self.assertEqual(asset.status_code, 200)
        self.assertEqual(asset_text, "window.mineradioContract = true;")
        self.assertEqual(directory_index.status_code, 404)
        self.assertEqual(traversal.status_code, 404)

    def test_public_directory_precedence_and_missing_index(self):
        from app import main, mineradio_runtime

        self.assertEqual(
            mineradio_runtime.BUNDLED_MINERADIO_PUBLIC_DIR,
            (PROJECT_ROOT / "vendor" / "mineradio-public").resolve(),
        )
        self.assertEqual(
            mineradio_runtime.bundled_mineradio_public_dir("C:/app/app/mineradio_runtime.py"),
            Path("C:/app/vendor/mineradio-public").resolve(),
        )

        with (
            tempfile.TemporaryDirectory() as explicit_directory,
            tempfile.TemporaryDirectory() as empty_directory,
        ):
            explicit = Path(explicit_directory)
            empty = Path(empty_directory)
            resolved = mineradio_runtime.resolve_mineradio_public_dir({
                "MINERADIO_PUBLIC_DIR": str(explicit),
            })
            self.assertEqual(resolved, explicit.resolve())

            with patch.object(mineradio_runtime, "BUNDLED_MINERADIO_PUBLIC_DIR", empty), patch.object(
                mineradio_runtime,
                "WINDOWS_MINERADIO_PUBLIC_DIR",
                Path("D:/Mineradio/resources/app/public"),
            ):
                self.assertEqual(
                    mineradio_runtime.resolve_mineradio_public_dir({}),
                    Path("D:/Mineradio/resources/app/public"),
                )

            response = main.create_app(
                mineradio_public_dir=empty,
            ).test_client().get("/mineradio/embed")

        self.assertEqual(response.status_code, 404)
        self.assertIn("Mineradio index.html not found:", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
