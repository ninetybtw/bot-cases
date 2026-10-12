"""Картинка дропа для шеринга (PNG 1080×1350): рамка цвета ступени, скин, название, «шанс 1 из N».

Строится на сервере, ссылка подписана, чтобы чужие предметы нельзя было перебрать по номеру.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import rules
from .config import ROOT

FONTS = ROOT / "assets" / "fonts"
CACHE = ROOT / "data" / "cache" / "img"
W, H = 1080, 1350


class ShareCards:
    def __init__(self, secret: str, bot_name: str):
        self.secret = secret.encode()
        self.bot_name = bot_name
        self.head = ImageFont.truetype(str(FONTS / "FiraSansExtraCondensed-ExtraBoldItalic.ttf"), 96)
        self.big = ImageFont.truetype(str(FONTS / "FiraSansExtraCondensed-ExtraBoldItalic.ttf"), 64)
        self.body = ImageFont.truetype(str(FONTS / "FiraSansExtraCondensed-Medium.ttf"), 44)
        self.small = ImageFont.truetype(str(FONTS / "FiraSansExtraCondensed-Medium.ttf"), 34)

    def sign(self, item_id: int) -> str:
        return hmac.new(self.secret, str(item_id).encode(), hashlib.sha256).hexdigest()[:16]

    def check(self, item_id: int, token: str) -> bool:
        return hmac.compare_digest(self.sign(item_id), token or "")

    def _image(self, url: str) -> Image.Image | None:
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".png")
        if not path.exists():
            try:
                req = urllib.request.Request(url + "/512fx384f", headers={"User-Agent": "skins-bot-share/1.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    path.write_bytes(resp.read())
            except OSError:
                return None
        return Image.open(path).convert("RGBA")

    def _draw(self, item: dict) -> bytes:
        tier = rules.CFG["tiers"][item["tier"]]
        color = tier["color"]
        img = Image.new("RGB", (W, H), "#121317")
        d = ImageDraw.Draw(img)
        for i in range(0, H, 6):  # мягкое свечение цвета ступени
            alpha = max(0, 1 - abs(i - 560) / 520)
            r, g, b = Image.new("RGB", (1, 1), color).getpixel((0, 0))
            d.line([(0, i), (W, i)], fill=(int(18 + (r - 18) * alpha * 0.28), int(19 + (g - 19) * alpha * 0.28),
                                           int(23 + (b - 23) * alpha * 0.28)), width=6)
        d.rectangle([24, 24, W - 24, H - 24], outline=color, width=10)
        d.text((W // 2, 120), tier["name"].upper(), font=self.head, fill=color, anchor="mm")
        d.text((W // 2, 200), item["game_rarity"], font=self.small, fill="#8f95a3", anchor="mm")
        pic = self._image(item["image_url"]) if item.get("image_url") else None
        if pic:
            pic.thumbnail((900, 640))
            img.paste(pic, ((W - pic.width) // 2, 560 - pic.height // 2), pic)
        name = ("StatTrak™ " if item["stattrak"] else "") + item["name"]
        d.text((W // 2, 950), name, font=self.big if len(name) < 30 else self.body, fill="#eceef3", anchor="mm")
        wear = rules.WEAR_NAMES.get(item["wear"] or "", "")
        sub = " · ".join(x for x in (wear, f"float {item['float']:.4f}" if item.get("float") is not None else "") if x)
        if sub:
            d.text((W // 2, 1020), sub, font=self.body, fill="#8f95a3", anchor="mm")
        d.text((W // 2, 1120), f"Шанс ступени 1 из {item['one_in']}", font=self.body, fill="#eceef3", anchor="mm")
        d.rectangle([24, H - 140, W - 24, H - 24], fill="#e8336e")
        d.text((W // 2, H - 82), self.bot_name.upper() + " · ЗАХОДИ КРУТИТЬ", font=self.big, fill="#ffffff", anchor="mm")
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    async def render(self, item: dict) -> bytes:
        return await asyncio.to_thread(self._draw, item)
