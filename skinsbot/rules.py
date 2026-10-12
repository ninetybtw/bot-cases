"""Цифры игры из config/skins.json. В коде чисел игры нет: всё берётся отсюда.

CFG — активная версия конфига (словарь). Админка может загрузить новую версию,
тогда activate() подменяет содержимое, а каждый дроп хранит версию, по которой выпал.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "skins.json"

CFG: dict = {}
VERSION = ""

TIER_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret", "divine", "exclusive"]
CASE_TIERS = ["rare", "epic", "legendary", "mythic", "secret"]
WEAR_NAMES = {
    "FN": "Factory New",
    "MW": "Minimal Wear",
    "FT": "Field-Tested",
    "WW": "Well-Worn",
    "BS": "Battle-Scarred",
}


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def activate(cfg: dict) -> None:
    global VERSION
    cfg = json.loads(json.dumps(cfg))  # своя копия: CFG может прийти сюда же
    CFG.clear()
    CFG.update(cfg)
    VERSION = f"{cfg.get('version', '?')}#{config_hash(cfg)}"


def load(path: Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


activate(load())


def rank(tier: str) -> int:
    return TIER_ORDER.index(tier)


def at_least(tier: str, floor: str) -> bool:
    return rank(tier) >= rank(floor)


# --- шансы ---------------------------------------------------------------


def case_odds() -> dict[str, float]:
    """Шкала обычного кейса CS2 в долях единицы."""
    return {t: p / 100 for t, p in CFG["case_odds"].items()}


def level_odds(level: str) -> dict[str, float]:
    odds = CFG["levels"][level]["odds"]
    return case_odds() if odds is None else {t: p / 100 for t, p in odds.items()}


def pity_groups() -> list[tuple[str, tuple[str, ...], int, float, int]]:
    """[(ключ, ступени группы, soft, step, hard)] — сначала внешняя (Легендарный+), потом Эпик+."""
    out = []
    for key in ("legend_plus", "epic_plus"):
        p = CFG["pity"][key]
        group = tuple(t for t in CASE_TIERS if at_least(t, p["from"]))
        out.append((key, group, p["soft"], p["step_pct"] / 100, p["hard"]))
    return out


# --- износ и цены ----------------------------------------------------------


def wear_table() -> dict[str, dict]:
    return {w: {"lo": lo, "hi": hi, "p": p / 100, "mult": m} for w, lo, hi, p, m in CFG["wear"]}


def wear_ranges() -> dict[str, tuple[float, float]]:
    return {w: (v["lo"], v["hi"]) for w, v in wear_table().items()}


def stattrak_chance() -> float:
    return CFG["stattrak"]["pct"] / 100


def price_key(wear: str | None, stattrak: bool) -> str:
    return ("ST_" if stattrak else "") + (wear or "-")


def split_key(key: str) -> tuple[str | None, bool]:
    st, _, wear = key.rpartition("_")
    return (None if wear == "-" else wear), bool(st)


def wear_distribution(item: dict) -> dict[str, float]:
    """Доли износа среди возможных для скина, пересчитанные до 100%."""
    wears = item.get("wears") or []
    if not wears:
        return {"-": 1.0}
    table = wear_table()
    total = sum(table[w]["p"] for w in wears)
    return {w: table[w]["p"] / total for w in wears}


def item_expected_cents(item: dict) -> float:
    st = stattrak_chance() if item.get("stattrak") else 0.0
    ev = 0.0
    for wear, p in wear_distribution(item).items():
        w = None if wear == "-" else wear
        ev += p * (1 - st) * item["prices"][price_key(w, False)]
        if st:
            ev += p * st * item["prices"][price_key(w, True)]
    return ev


def pool_expected_cents(odds: dict[str, float], pools: dict[str, list[str]], items: dict[str, dict]) -> float:
    ev = 0.0
    for tier, p in odds.items():
        pool = pools.get(tier) or []
        if pool and p:
            ev += p * sum(item_expected_cents(items[i]) for i in pool) / len(pool)
    return ev


def quick_sell(price_cents: int) -> int:
    return price_cents * CFG["sell_system_pct"] // 100


# --- валюта ------------------------------------------------------------------


def country_for_language(language_code: str | None) -> str:
    lang = (language_code or "").split("-")[0].lower()
    return CFG["currency"]["language_to_country"].get(lang, CFG["currency"]["default_country"])


def currency_block(country: str) -> dict:
    c = CFG["currency"]["countries"].get(country) or CFG["currency"]["countries"]["RU"]
    return {
        "country": country,
        "code": c["code"],
        "symbol": c["symbol"],
        "rate": c["rate"],
        "decimals": c["decimals"],
        "game_label": CFG["currency"]["game_label"] if CFG["currency"]["show_game_label"] else "",
    }


def format_money(cents: int, country: str = "RU") -> str:
    """Сумма в валюте страны игрока: «1 250 ₽», «50,00 Br». Только для текстов ошибок."""
    c = currency_block(country)
    value = cents / 100 * c["rate"] * CFG["currency"].get("scale", 1)
    if c["decimals"]:
        text = f"{value:,.{c['decimals']}f}".replace(",", " ").replace(".", ",")
    else:
        text = f"{round(value):,}".replace(",", " ")
    return f"{text} {c['symbol']}"
