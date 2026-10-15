"""Каталог CS2 и свои кейсы с ценами в копейках игрового рубля: data/catalog.json.

Цены — логика reference/fill_prices.py:
  * каталог — data/skins/cs2_skins.csv (лоты: рисунок × износ × StatTrak), фазы Doppler — из ByMykel/CSGO-API;
  * цены — медиана Skinport, CS2 Market (market.csgo.com), по желанию Lis-Skins и выгрузки Buff;
    при 3+ источниках цены дальше чем вдвое от медианы отбрасываются;
  * USD → рубли по курсу ЦБ РФ на день пересчёта (если ЦБ недоступен — последний известный курс).

Кейсы — свои сборки как на кейс-сайтах (config/skins.json → cases.list):
  * предметы подбираются из всего каталога по правилам кейса (оружие, категория, редкость, цена),
    равномерно по шкале цен от дешёвых к дорогим;
  * шанс предмета ∝ 1 / цена^alpha, внутри кейса шанс ступени = сумма шансов её предметов;
  * цена кейса = средний дроп с учётом мягких гарантий / 0,8, не больше ±30% к цене недельной давности.

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

from skinsbot import odds, rules  # noqa: E402

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

TIER_BY_NO = {str(v["no"]): k for k, v in rules.CFG["tiers"].items()}
DROP_TIERS = set(rules.DROP_TIERS)


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


def load_skins(quotes, rate, phases) -> tuple[dict[str, dict], dict]:
    """Все скины каталога (без сувениров и контрабанды) с ценой каждого износа и StatTrak."""
    items: dict[str, dict] = {}
    missing: list[tuple[str, str]] = []
    used = {"market": 0, "same_item": 0, "similar": 0, "ladder": 0}
    for r in csv.DictReader(SKINS_CSV.open(encoding="utf-8-sig")):
        tier = TIER_BY_NO.get(r["our_tier_no"])
        if r["souvenir"] == "true" or tier not in DROP_TIERS:
            continue
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
                "image": r["image_url"], "min_float": None if vanilla else (to_float(r["min_float"]) or 0.0),
                "max_float": None if vanilla else (to_float(r["max_float"]) or 1.0),
                "wears": [], "stattrak": False, "prices": {}, "estimated": [],
            }
        wear = r["wear_short"] or None
        st = r["stattrak"] == "true"
        if wear and wear not in it["wears"]:
            it["wears"].append(wear)
        it["stattrak"] = it["stattrak"] or st
        variants = quotes.get(r["market_hash_name"], {})
        usd = median_price(variants.get(phase or "", [])) if phase else None
        usd = usd or median_price(variants.get("", [])) or median_price([p for v in variants.values() for p in v])
        key = rules.price_key(wear, st)
        if usd:
            it["prices"][key] = round(usd * rate * 100)
            used["market"] += 1
        else:
            missing.append((sid, key))

    order = list(rules.wear_ranges())
    for it in items.values():
        it["wears"].sort(key=order.index)

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
        used[src] += 1
    # Скины без единой рыночной цены в кейсы не берём: их цена была бы выдуманной.
    for it in items.values():
        it["market"] = len(it["estimated"]) < len(it["prices"])
    return items, used


def matches(it: dict, rule: dict, value: float) -> bool:
    if rule.get("weapons") and it["weapon"] not in rule["weapons"]:
        return False
    if rule.get("categories") and it["category"] not in rule["categories"]:
        return False
    if it["category"] in rule.get("exclude_categories", []):
        return False
    if rule.get("tiers") and it["tier"] not in rule["tiers"]:
        return False
    if rule.get("min_rub") is not None and value < rule["min_rub"] * 100:
        return False
    if rule.get("max_rub") is not None and value > rule["max_rub"] * 100:
        return False
    return True


def pick_spread(candidates: list[tuple[float, str]], n: int, taken: set[str]) -> list[str]:
    """n предметов равномерно по шкале цен (в логарифме): от самого дешёвого до самого дорогого."""
    import math

    pool = sorted(c for c in candidates if c[1] not in taken)
    if len(pool) <= n:
        return [sid for _, sid in pool]
    lo, hi = math.log(pool[0][0]), math.log(pool[-1][0])
    chosen: list[str] = []
    for k in range(n):
        target = lo + (hi - lo) * k / (n - 1)
        best = min((c for c in pool if c[1] not in chosen), key=lambda c: abs(math.log(c[0]) - target))
        chosen.append(best[1])
    return chosen


def build_case(spec: dict, items: dict[str, dict], values: dict[str, float]) -> dict:
    alpha = rules.CFG["cases"]["weight_alpha"]
    chosen: list[str] = []
    for rule in spec["rules"]:
        cands = [(values[sid], sid) for sid, it in items.items() if it["market"] and matches(it, rule, values[sid])]
        chosen += pick_spread(cands, rule["n"], set(chosen))
    weights = {sid: values[sid] ** (-alpha) for sid in chosen}
    total = sum(weights.values())
    weights = {sid: w / total for sid, w in weights.items()}
    pools: dict[str, list[str]] = {}
    for sid in sorted(chosen, key=lambda s: values[s]):
        pools.setdefault(items[sid]["tier"], []).append(sid)
    tier_odds = {t: sum(weights[s] for s in ids) for t, ids in pools.items()}
    top = max(chosen, key=lambda s: values[s])
    return {"id": spec["id"], "name": spec["name"], "category": spec["category"], "free": bool(spec.get("free")),
            "image": items[top]["image"], "top_tier": items[top]["tier"], "weights": weights, "pools": pools,
            "tier_odds": tier_odds}


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
    crates = json.loads((RAW / "crates.json").read_text(encoding="utf-8"))
    phases = {e["id"]: e["phase"] for c in crates for e in (c.get("contains_rare") or []) + (c.get("contains") or []) if e.get("phase")}
    all_items, used = load_skins(quotes, rate, phases)
    values = {sid: rules.item_expected_cents(it) for sid, it in all_items.items()}

    cases = [build_case(spec, all_items, values) for spec in rules.CFG["cases"]["list"]]
    in_cases = {sid for c in cases for sid in c["weights"]}
    items = {sid: all_items[sid] for sid in sorted(in_cases)}
    for it in items.values():
        it.pop("market", None)
        it["cases"] = [c["id"] for c in cases if it["id"] in c["weights"]]

    # Цена кейса: средний дроп с гарантиями / 0,8, не больше ±30% к цене недельной давности.
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
        price = max(1, round(raw))
        hist = {d: v for d, v in history.get(key, {}).items() if d >= (dt.date.today() - dt.timedelta(days=30)).isoformat()}
        hist[today] = price
        history[key] = hist
        return price

    for c in cases:
        c["ev_base_cents"] = round(rules.case_expected_cents(c, items))
        ev = rules.case_expected_cents(c, items, odds.consolidated(c["tier_odds"]))
        c["ev_cents"] = round(ev)
        c["price_cents"] = priced(c["id"], ev)

    return {
        "generated_at": int(time.time()),
        "config_version": rules.VERSION,
        "usd_rub": rate,
        "usd_rub_date": rate_date,
        "sources": {"catalog": "data/skins/cs2_skins.csv", "prices": "Skinport, CS2 Market" + (", Lis-Skins" if use_lisskins else "") + (", Buff" if buff else "")},
        "price_sources": used,
        "cases": cases,
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
    for c in catalog["cases"]:
        odds_txt = " ".join(f"{t}:{p * 100:.1f}" for t, p in c["tier_odds"].items())
        print(f"  {c['name']:<16} {c['price_cents'] / 100:>10.2f} ₽  предметов {len(c['weights']):>2}  {odds_txt}")


if __name__ == "__main__":
    main()
