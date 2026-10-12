"""Каталог CS2 с рыночными ценами в копейках игрового рубля: data/catalog.json.

Логика reference/fill_prices.py, версия ТЗ 1.4:
  * каталог — data/skins/cs2_skins.csv (лоты: рисунок × износ × StatTrak), фазы Doppler и картинки
    кейсов — из ByMykel/CSGO-API;
  * цены — медиана Skinport, CS2 Market (market.csgo.com), по желанию Lis-Skins и выгрузки Buff;
    при 3+ источниках цены дальше чем вдвое от медианы отбрасываются;
  * USD → рубли по курсу ЦБ РФ на день пересчёта (если ЦБ недоступен — последний известный курс);
  * цена кейса и уровня крутки = средняя цена выпадения / 0,8, не больше ±30% к цене недельной давности.

Запуск раз в сутки (worker делает это сам в 04:00 МСК, если PRICE_UPDATE=1):
    python tools/build_catalog.py               # скачать цены и курс, пересобрать
    python tools/build_catalog.py --offline     # взять уже скачанное из data/raw/
    python tools/build_catalog.py --lisskins    # добавить Lis-Skins (выгрузка больше 1 ГБ)
    python tools/build_catalog.py --buff buff.csv   # добавить Buff163 (колонки market_hash_name,price_usd)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skinsbot import rules  # noqa: E402

RAW = ROOT / "data" / "raw"
SKINS_CSV = ROOT / "data" / "skins" / "cs2_skins.csv"
OUT = ROOT / "data" / "catalog.json"

SOURCES = {
    "skinport": "https://api.skinport.com/v1/items?app_id=730&currency=USD&tradable=0",
    "csmarket": "https://market.csgo.com/api/v2/prices/USD.json",
    "lisskins": "https://lis-skins.com/market_export_json/api_csgo_full.json",
}
CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CRATES_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/crates.json"

# Кейсы первой версии (ТЗ: 6–10 популярных, дальше по кейсу в неделю).
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
TIER_BY_NAME = {v["name"]: k for k, v in rules.CFG["tiers"].items()}


def fetch(url: str, dest: Path) -> None:
    headers = {"User-Agent": "skins-bot-prices/1.4", "Accept-Encoding": "br" if "skinport" in url else "identity"}
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "br":
                    import brotli  # type: ignore

                    body = brotli.decompress(body)
            dest.write_bytes(body)
            return
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))


def to_float(v) -> float | None:
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def load_quotes(use_lisskins: bool, buff: Path | None) -> dict[str, dict[str, list[float]]]:
    """market_hash_name → {версия/фаза или '' → [цены из разных источников]}."""
    quotes: dict[str, dict[str, list[float]]] = {}

    def add(name, version, price):
        if name and price:
            quotes.setdefault(name, {}).setdefault(version or "", []).append(price)

    sp = RAW / "skinport.json"
    if sp.exists():
        for e in json.loads(sp.read_text(encoding="utf-8")):
            price = to_float(e.get("median_price")) if e.get("quantity") else None
            add(e["market_hash_name"], e.get("version"), price or to_float(e.get("suggested_price")))
    cm = RAW / "csmarket.json"
    if cm.exists():
        for e in json.loads(cm.read_text(encoding="utf-8")).get("items", []):
            add(e.get("market_hash_name"), None, to_float(e.get("price")))
    ls = RAW / "lisskins.json"
    if use_lisskins and ls.exists():
        data = json.loads(ls.read_text(encoding="utf-8"))
        for e in data.get("items", data) if isinstance(data, dict) else data:
            add(e.get("name") or e.get("market_hash_name"), None, to_float(e.get("price")))
    if buff:
        for e in csv.DictReader(buff.open(encoding="utf-8-sig")):
            add(e.get("market_hash_name"), None, to_float(e.get("price_usd")))
    return quotes


def median_price(prices: list[float]) -> float | None:
    if not prices:
        return None
    med = statistics.median(prices)
    if len(prices) >= 3:
        kept = [p for p in prices if med / 2 <= p <= med * 2]
        med = statistics.median(kept or prices)
    return med


def usd_rub(offline: bool) -> tuple[float, str]:
    path = RAW / "cbr.json"
    if not offline:
        try:
            fetch(CBR_URL, path)
        except OSError:
            print("ЦБ недоступен, беру последний известный курс")
    data = json.loads(path.read_text(encoding="utf-8"))
    return float(data["Valute"]["USD"]["Value"]), data["Date"][:10]


def build(offline: bool, use_lisskins: bool, buff: Path | None) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    if not offline:
        names = ["skinport", "csmarket"] + (["lisskins"] if use_lisskins else [])
        for name in names:
            print(f"скачиваю цены {name}…")
            try:
                fetch(SOURCES[name], RAW / f"{name}.json")
            except OSError as e:
                print(f"  {name} недоступен: {e}")
        if not (RAW / "crates.json").exists():
            fetch(CRATES_URL, RAW / "crates.json")
    rate, rate_date = usd_rub(offline)
    quotes = load_quotes(use_lisskins, buff)
    crates = {c["name"]: c for c in json.loads((RAW / "crates.json").read_text(encoding="utf-8")) if c.get("type") == "Case"}
    phases = {e["id"]: e["phase"] for c in crates.values() for e in (c.get("contains_rare") or []) + c["contains"] if e.get("phase")}

    rows = [r for r in csv.DictReader(SKINS_CSV.open(encoding="utf-8-sig"))
            if r["in_case_pool"] == "true" and r["souvenir"] == "false"]
    items: dict[str, dict] = {}
    cases: dict[str, dict] = {}
    for case_name in CASES:
        crate = crates[case_name]
        cases[crate["id"]] = {"id": crate["id"], "name": case_name, "image": crate.get("image"),
                              "released": crate.get("first_sale_date"), "pools": {t: [] for t in rules.CASE_TIERS}}
    case_id_by_name = {c["name"]: cid for cid, c in cases.items()}

    missing: list[tuple[str, str]] = []
    sources_used = {"market": 0, "same_item": 0, "similar": 0, "ladder": 0}
    for r in rows:
        in_cases = [case_id_by_name[n.strip()] for n in r["cases"].split(";") if n.strip() in case_id_by_name]
        if not in_cases:
            continue
        tier = TIER_BY_NAME[r["our_tier"]]
        sid = r["skin_id"]
        phase = phases.get(sid)
        it = items.get(sid)
        if not it:
            vanilla = sid.startswith("skin-vanilla")
            name = f"{r['weapon']} | {r['skin']}" if not vanilla else r["weapon"]
            if r["market_hash_name"].startswith("★") and not name.startswith("★"):
                name = "★ " + name
            it = items[sid] = {
                "id": sid, "name": name + (f" ({phase})" if phase else ""), "weapon": r["weapon"],
                "finish": ("Ванильный" if vanilla else r["skin"]) + (f" ({phase})" if phase else ""),
                "category": r["category"], "tier": tier, "game_rarity": r["rarity_game"],
                "image": r["image_url"], "min_float": to_float(r["min_float"]) if not vanilla else None,
                "max_float": to_float(r["max_float"]) if not vanilla else None,
                "wears": [], "stattrak": False, "cases": [], "prices": {}, "estimated": [],
            }
            if it["min_float"] is None and not vanilla:
                it["min_float"] = 0.0
        for cid in in_cases:
            if cid not in it["cases"]:
                it["cases"].append(cid)
                cases[cid]["pools"][tier].append(sid)
        wear = r["wear_short"] or None
        st = r["stattrak"] == "true"
        if wear and wear not in it["wears"]:
            it["wears"].append(wear)
        it["stattrak"] = it["stattrak"] or st
        name = r["market_hash_name"]
        variants = quotes.get(name, {})
        usd = median_price(variants.get(phase or "", [])) if phase else None
        usd = usd or median_price(variants.get("", [])) or median_price([p for v in variants.values() for p in v])
        key = rules.price_key(wear, st)
        if usd:
            it["prices"][key] = round(usd * rate * 100)
            sources_used["market"] += 1
        else:
            missing.append((sid, key))

    order = list(rules.wear_ranges())
    for it in items.values():
        it["wears"].sort(key=order.index)
        if it["max_float"] is None and it["wears"]:
            it["max_float"] = 1.0

    # Нет рыночной цены: от того же предмета в другом износе по запасным множителям,
    # затем медиана похожих лотов (ступень + износ + StatTrak), в крайнем случае лестница.
    wt = rules.wear_table()

    def mult(key: str) -> float:
        wear, st = rules.split_key(key)
        return (wt[wear]["mult"] if wear else 1.0) * (rules.CFG["stattrak"]["fallback_mult"] if st else 1.0)

    peers: dict[tuple[str, str], list[int]] = {}
    for it in items.values():
        for key, p in it["prices"].items():
            peers.setdefault((it["tier"], key), []).append(p)
    for sid, key in missing:
        it = items[sid]
        own = [p / mult(k) * mult(key) for k, p in it["prices"].items() if k not in it["estimated"]]
        if own:
            price, src = statistics.median(own), "same_item"
        elif peers.get((it["tier"], key)):
            price, src = statistics.median(peers[(it["tier"], key)]), "similar"
        else:
            price, src = rules.CFG["ladder_cents"][it["tier"]] * mult(key), "ladder"
        it["prices"][key] = round(price)
        it["estimated"].append(key)
        sources_used[src] += 1

    # Цены кейсов и уровней: средний дроп / 0,8, не больше ±30% к цене недельной давности.
    previous = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    history = previous.get("price_history", {})
    today = dt.date.today().isoformat()
    week_ago = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    target = rules.CFG["target_return"]
    cap = rules.CFG["max_weekly_price_change"]

    def priced(key: str, ev: float) -> int:
        raw = ev / target
        past = [v for d, v in sorted(history.get(key, {}).items()) if d >= week_ago]
        if past:
            raw = min(max(raw, past[0] * (1 - cap)), past[0] * (1 + cap))
        price = round(raw)
        hist = {d: v for d, v in history.get(key, {}).items() if d >= (dt.date.today() - dt.timedelta(days=30)).isoformat()}
        hist[today] = price
        history[key] = hist
        return price

    case_list = []
    for c in cases.values():
        ev = rules.pool_expected_cents(rules.case_odds(), c["pools"], items)
        c["ev_cents"] = round(ev)
        c["price_cents"] = priced(c["id"], ev)
        case_list.append(c)
    case_list.sort(key=lambda c: c["price_cents"])

    level_pools = {t: sorted({i for c in cases.values() for i in c["pools"][t]}) for t in rules.CASE_TIERS}
    levels = []
    for key, lvl in rules.CFG["levels"].items():
        if lvl["odds"] is None:
            continue
        odds = rules.level_odds(key)
        pools = {t: level_pools[t] for t in odds}
        ev = rules.pool_expected_cents(odds, pools, items)
        levels.append({"id": key, "name": lvl["name"], "what": lvl["what"], "from": lvl["from"],
                       "ev_cents": round(ev), "price_cents": priced(f"level:{key}", ev), "pools": pools})

    return {
        "generated_at": int(time.time()),
        "config_version": rules.VERSION,
        "usd_rub": rate,
        "usd_rub_date": rate_date,
        "sources": {"catalog": "data/skins/cs2_skins.csv", "prices": "Skinport, CS2 Market" + (", Lis-Skins" if use_lisskins else "") + (", Buff" if buff else "")},
        "price_sources": sources_used,
        "cases": case_list,
        "levels": levels,
        "items": items,
        "price_history": history,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="не скачивать, взять data/raw/")
    parser.add_argument("--lisskins", action="store_true", help="добавить Lis-Skins (выгрузка больше 1 ГБ)")
    parser.add_argument("--buff", type=Path, help="CSV с ценами Buff163: market_hash_name,price_usd")
    args = parser.parse_args()
    catalog = build(args.offline, args.lisskins, args.buff)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)
    print(f"курс ЦБ {catalog['usd_rub']} ₽ за $ на {catalog['usd_rub_date']}")
    print(f"кейсов: {len(catalog['cases'])}, предметов: {len(catalog['items'])}, цены: {catalog['price_sources']}")
    for c in catalog["cases"] + catalog["levels"]:
        print(f"  {c['name']:<28} {c['price_cents'] / 100:>10.2f} ₽  средний дроп {c['ev_cents'] / 100:.2f} ₽")


if __name__ == "__main__":
    main()
