"""Собирает каталог кейсов CS2 с рыночными ценами: data/catalog.json.

Источники:
  * состав кейсов и скинов — ByMykel/CSGO-API (данные из файлов игры);
  * цены — публичный API Skinport (медиана продаж, иначе рекомендованная цена).

Запуск раз в сутки (аналог fill_prices.py из ТЗ):
    python tools/build_catalog.py            # скачать и пересобрать
    python tools/build_catalog.py --offline  # пересобрать из кэша data/raw/

Цена спина кейса = средняя цена дропа / 0,9 и меняется не больше чем на ±30%
относительно прошлой сборки.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skinsbot import rules  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
OUT = ROOT / "data" / "catalog.json"

SOURCES = {
    "crates.json": "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/crates.json",
    "skins.json": "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json",
    "skinport.json": "https://api.skinport.com/v1/items?app_id=730&currency=USD",
}

# Кейсы первой версии (6–10 популярных, ТЗ «Объём на старте»).
CASES = [
    "Fever Case",
    "Gallery Case",
    "Kilowatt Case",
    "Revolution Case",
    "Recoil Case",
    "Dreams & Nightmares Case",
    "Fracture Case",
    "Clutch Case",
]

CS_RARITY_TO_TIER = {
    "Mil-Spec Grade": "rare",
    "Restricted": "epic",
    "Classified": "legendary",
    "Covert": "mythic",
}


def fetch(name: str, url: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "skins-bot-catalog/1.0"}
    if "skinport" in url:
        # Skinport отдаёт данные только со сжатием brotli.
        headers["Accept-Encoding"] = "br"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding") == "br":
            import brotli  # type: ignore

            body = brotli.decompress(body)
    (RAW_DIR / name).write_bytes(body)


def load_raw(name: str):
    return json.loads((RAW_DIR / name).read_text(encoding="utf-8"))


def market_price(entry: dict) -> float | None:
    if entry.get("quantity") and entry.get("median_price"):
        return float(entry["median_price"])
    if entry.get("suggested_price"):
        return float(entry["suggested_price"])
    return None


def build_price_index(skinport: list[dict]) -> dict[tuple[str, str | None], float]:
    """(market_hash_name, phase) -> цена; phase=None — цена без учёта фазы."""
    index: dict[tuple[str, str | None], float] = {}
    by_name: dict[str, list[float]] = {}
    for e in skinport:
        price = market_price(e)
        if price is None:
            continue
        name = e["market_hash_name"]
        index[(name, e.get("version"))] = price
        by_name.setdefault(name, []).append(price)
    for name, prices in by_name.items():
        index.setdefault((name, None), statistics.median(prices))
    return index


def hash_name(base: str, wear: str | None, stattrak: bool) -> str:
    name = base
    if stattrak:
        name = name.replace("★ ", "★ StatTrak™ ", 1) if name.startswith("★") else "StatTrak™ " + name
    if wear:
        name += f" ({rules.WEAR_NAMES[wear]})"
    return name


def possible_wears(min_float: float, max_float: float) -> list[str]:
    return [w for w, (lo, hi) in rules.WEAR_RANGES.items() if lo < max_float and hi > min_float]


def build(offline: bool) -> dict:
    if not offline:
        for name, url in SOURCES.items():
            print(f"скачиваю {name}…")
            fetch(name, url)
    crates = load_raw("crates.json")
    skins = {s["id"]: s for s in load_raw("skins.json")}
    prices = build_price_index(load_raw("skinport.json"))

    items: dict[str, dict] = {}
    cases: list[dict] = []
    missing: list[tuple[str, str, bool, str]] = []  # (item_id, wear, st, tier) без рыночной цены

    for case_name in CASES:
        crate = next(c for c in crates if c["name"] == case_name)
        pools: dict[str, list[str]] = {t: [] for t in rules.CASE_TIER_ODDS}
        entries = [(e, CS_RARITY_TO_TIER[e["rarity"]["name"]]) for e in crate["contains"]]
        entries += [(e, "secret") for e in crate.get("contains_rare") or []]
        for entry, tier in entries:
            item_id = entry["id"]
            pools[tier].append(item_id)
            if item_id in items:
                items[item_id]["cases"].append(crate["id"])
                continue
            skin = skins.get(item_id) or skins.get(item_id.rsplit("_", 1)[0], {})
            name = entry["name"]
            phase = entry.get("phase")
            vanilla = item_id.startswith("skin-vanilla")
            weapon, _, finish = name.partition(" | ")
            if phase:
                finish = f"{finish} ({phase})"
            min_f = float(skin.get("min_float") or 0.0)
            max_f = float(skin.get("max_float") or 1.0)
            wears = [] if vanilla else possible_wears(min_f, max_f)
            is_gloves = (skin.get("category") or {}).get("name") == "Gloves"
            stattrak = not is_gloves
            item = {
                "id": item_id,
                "name": name + (f" ({phase})" if phase else ""),
                "weapon": weapon,
                "finish": finish or "Ванильный",
                "tier": tier,
                "game_rarity": "★ Нож/перчатки" if tier == "secret" else entry["rarity"]["name"],
                "image": entry.get("image") or skin.get("image"),
                "min_float": None if vanilla else min_f,
                "max_float": None if vanilla else max_f,
                "wears": wears,
                "stattrak": stattrak,
                "cases": [crate["id"]],
                "prices": {},
            }
            for wear in wears or [None]:
                for st in ([False, True] if stattrak else [False]):
                    key = rules.price_key(wear, st)
                    p = prices.get((hash_name(name, wear, st), phase))
                    if p is None and phase:
                        p = prices.get((hash_name(name, wear, st), None))
                    if p is None:
                        missing.append((item_id, wear, st, tier))
                    else:
                        item["prices"][key] = round(p, 2)
            items[item_id] = item
        cases.append(
            {
                "id": crate["id"],
                "name": crate["name"],
                "image": crate.get("image"),
                "released": crate.get("first_sale_date"),
                "pools": pools,
            }
        )

    # Нет рыночной цены (редкие сочетания ножей) → пересчёт от цены того же предмета
    # в другом износе по запасным множителям; если цен у предмета нет совсем —
    # медиана лотов той же ступени и износа, а лестница только в крайнем случае.
    peers: dict[tuple[str, str], list[float]] = {}
    for it in items.values():
        for key, p in it["prices"].items():
            peers.setdefault((it["tier"], key), []).append(p)

    def mult(key: str) -> float:
        st, _, wear = key.rpartition("_")
        return WEAR_MULT.get(wear, 1.0) * (rules.STATTRAK_FALLBACK_MULT if st else 1.0)

    WEAR_MULT = rules.WEAR_FALLBACK_MULT
    for item_id, wear, st, tier in missing:
        key = rules.price_key(wear, st)
        known = items[item_id]["prices"]
        own = [p / mult(k) * mult(key) for k, p in known.items() if k not in items[item_id].get("estimated", [])]
        group = peers.get((tier, key))
        if own:
            p = statistics.median(own)
        elif group:
            p = statistics.median(group)
        else:
            p = rules.LADDER[tier] * rules.WEAR_FALLBACK_MULT.get(wear, 1.0) * (rules.STATTRAK_FALLBACK_MULT if st else 1)
        items[item_id]["prices"][key] = round(p, 2)
        items[item_id].setdefault("estimated", []).append(key)

    previous = {}
    if OUT.exists():
        previous = {c["id"]: c["price"] for c in json.loads(OUT.read_text(encoding="utf-8"))["cases"]}
    for case in cases:
        ev = rules.case_expected_value(case, items)
        price = ev / rules.TARGET_RTP
        if case["id"] in previous:
            old = previous[case["id"]]
            price = min(max(price, old * 0.7), old * 1.3)
        case["ev"] = round(ev, 4)
        case["price"] = round(price, 2)

    cases.sort(key=lambda c: c["price"])
    return {
        "generated_at": int(time.time()),
        "sources": {"catalog": "ByMykel/CSGO-API", "prices": "Skinport API"},
        "missing_prices": len(missing),
        "cases": cases,
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="не скачивать, взять data/raw/")
    args = parser.parse_args()
    catalog = build(args.offline)
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"кейсов: {len(catalog['cases'])}, предметов: {len(catalog['items'])}, без цены: {catalog['missing_prices']}")
    for c in catalog["cases"]:
        print(f"  {c['name']:<28} спин ${c['price']:>8.2f}  средний дроп ${c['ev']:.2f}")


if __name__ == "__main__":
    main()
