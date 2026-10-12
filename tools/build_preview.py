"""Собирает автономное превью бота в preview/dist/: чат Telegram + мини-приложение в демо-режиме.

Мини-приложение то же, что в webapp/, но вместо сервера работает webapp/mock.js,
а картинки скинов вшиты в demo-data.js (страница превью не может грузить их со Steam CDN).

    python tools/build_preview.py
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import io
import json
import shutil
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "webapp"
DIST = ROOT / "preview" / "dist"
CACHE = ROOT / "data" / "raw" / "img"
SIZE = (200, 150)


def fetch_image(url: str) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".png")
    if not path.exists():
        req = urllib.request.Request(url + "/256fx192f", headers={"User-Agent": "skins-bot-preview/1.0"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    path.write_bytes(resp.read())
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
    img = Image.open(path).convert("RGBA")
    img.thumbnail(SIZE)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=72, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    catalog = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    urls = {it["image"] for it in catalog["items"].values() if it.get("image")}
    urls |= {c["image"] for c in catalog["cases"] if c.get("image")}
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        data = dict(zip(urls, pool.map(fetch_image, urls)))
    for it in catalog["items"].values():
        it["image"] = data.get(it.get("image"), "")
    for c in catalog["cases"]:
        c["image"] = data.get(c.get("image"), "")

    app = DIST / "app"
    if DIST.exists():
        shutil.rmtree(DIST)
    app.mkdir(parents=True)
    for name in ("style.css", "api.js", "app.js", "mock.js"):
        shutil.copy(WEBAPP / name, app / name)
    (app / "demo-data.js").write_text(
        "window.DEMO_CATALOG=" + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + ";", encoding="utf-8"
    )
    html = (WEBAPP / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        '<script src="api.js"></script>',
        '<script>window.SKINS_DEMO=true</script>\n<script src="demo-data.js"></script>\n'
        '<script src="mock.js"></script>\n<script src="api.js"></script>',
    )
    (app / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(ROOT / "preview" / "index.html", DIST / "index.html")
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"превью: {DIST} ({size / 1e6:.1f} МБ, картинок {len(data)})")


if __name__ == "__main__":
    main()
