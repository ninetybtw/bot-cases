"""Чистая игровая логика без базы: генератор, спин кейса, апгрейдер, контракт, колесо.

Всё случайное берётся из FairRng: HMAC-SHA256(server_seed, "client_seed:nonce:i").
Игрок видит хэш server_seed заранее и сам server_seed после смены пары,
поэтому может пересчитать любой свой спин (экран «Честная игра»).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from . import rules


class FairRng:
    def __init__(self, server_seed: str, client_seed: str, nonce: int):
        self.server_seed = server_seed
        self.client_seed = client_seed
        self.nonce = nonce
        self._i = 0

    def random(self) -> float:
        msg = f"{self.client_seed}:{self.nonce}:{self._i}".encode()
        self._i += 1
        digest = hmac.new(self.server_seed.encode(), msg, hashlib.sha256).digest()
        return (int.from_bytes(digest[:8], "big") >> 11) / float(1 << 53)

    def choice(self, seq):
        return seq[min(int(self.random() * len(seq)), len(seq) - 1)]

    def weighted(self, weights: dict[str, float]) -> str:
        r = self.random() * sum(weights.values())
        acc = 0.0
        last = None
        for key, w in weights.items():
            if w <= 0:
                continue
            acc += w
            last = key
            if r < acc:
                return key
        return last


def seed_hash(server_seed: str) -> str:
    return hashlib.sha256(server_seed.encode()).hexdigest()


# --- шансы ---------------------------------------------------------------


def apply_luck(odds: dict[str, float], m: float) -> dict[str, float]:
    """Удача: старшие ступени ×m, две частые отдают шанс пропорционально долям."""
    if m == 1:
        return dict(odds)
    boosted = {t: p * m for t, p in odds.items() if t not in rules.LUCK_DONORS}
    donors = {t: p for t, p in odds.items() if t in rules.LUCK_DONORS}
    left = 1 - sum(boosted.values())
    scale = left / sum(donors.values())
    return {t: boosted.get(t, donors.get(t, 0) * scale) for t in odds}


def apply_pity(odds: dict[str, float], since_epic: int, since_legendary: int) -> dict[str, float]:
    """Мягкая гарантия. since_* — сколько спинов подряд уже было без ступени (без текущего)."""
    odds = dict(odds)
    for key, counter in (("legendary", since_legendary), ("epic", since_epic)):
        group, soft, hard = rules.PITY[key]
        n = counter + 1  # номер текущего спина в серии
        if n < soft:
            continue
        current = sum(odds[t] for t in group if t in odds)
        target = 1.0 if n >= hard else min(1.0, current + rules.PITY_STEP * (n - soft + 1))
        extra = target - current
        if extra <= 0:
            continue
        below = {t: p for t, p in odds.items() if t not in group}
        total_below = sum(below.values())
        if total_below <= 0:
            continue
        for t, p in below.items():
            odds[t] = p * (1 - extra / total_below)
        odds[group[0]] += extra
    return odds


def spin_odds(since_epic: int, since_legendary: int, luck: float = 1.0) -> dict[str, float]:
    return apply_pity(apply_luck(rules.CASE_TIER_ODDS, luck), since_epic, since_legendary)


def one_in(p: float) -> int:
    return max(1, round(1 / p)) if p > 0 else 0


# --- предмет -------------------------------------------------------------


@dataclass
class Drop:
    catalog_id: str
    tier: str
    wear: str | None
    float_value: float | None
    pattern: int
    stattrak: bool
    price_cents: int


def item_price_cents(item: dict, wear: str | None, stattrak: bool) -> int:
    return round(item["prices"][rules.price_key(wear, stattrak)] * 100)


def roll_wear(rng: FairRng, item: dict) -> tuple[str | None, float | None]:
    if not item.get("wears"):
        return None, None
    wear = rng.weighted(rules.wear_distribution(item))
    lo, hi = rules.WEAR_RANGES[wear]
    lo, hi = max(lo, item["min_float"]), min(hi, item["max_float"])
    return wear, round(lo + (hi - lo) * rng.random(), 6)


def wear_for_float(item: dict, value: float) -> str | None:
    if not item.get("wears"):
        return None
    for wear, (lo, hi) in rules.WEAR_RANGES.items():
        if lo <= value < hi and wear in item["wears"]:
            return wear
    return item["wears"][-1]


def make_drop(rng: FairRng, item: dict, stattrak: bool | None = None, float_value: float | None = None) -> Drop:
    if float_value is None:
        wear, float_value = roll_wear(rng, item)
    else:
        wear = wear_for_float(item, float_value)
    if stattrak is None:
        stattrak = bool(item.get("stattrak")) and rng.random() < rules.STATTRAK_CHANCE
    return Drop(
        catalog_id=item["id"],
        tier=item["tier"],
        wear=wear,
        float_value=float_value,
        pattern=int(rng.random() * 1000),
        stattrak=stattrak,
        price_cents=item_price_cents(item, wear, stattrak),
    )


def open_case(rng: FairRng, case: dict, items: dict[str, dict], odds: dict[str, float]) -> Drop:
    available = {t: p for t, p in odds.items() if case["pools"].get(t)}
    tier = rng.weighted(available)
    item = items[rng.choice(case["pools"][tier])]
    return make_drop(rng, item)


# --- апгрейдер ------------------------------------------------------------


def upgrade_chance(stake_cents: int, target_cents: int, bonus: bool) -> float:
    """Шанс = ставка / цель × 0,85; после неудачи следующий апгрейд получает +3% к шансу."""
    if target_cents <= stake_cents or stake_cents <= 0:
        return 0.0
    chance = stake_cents / target_cents * rules.UPGRADE_HOUSE
    if bonus:
        chance *= 1 + rules.UPGRADE_FAIL_BONUS
    return min(chance, 0.95)


# --- контракт «5 в 1» ----------------------------------------------------


def contract(rng: FairRng, inputs: list[dict], items: dict[str, dict], cases: dict[str, dict]) -> tuple[Drop, bool]:
    """inputs: экземпляры {catalog_id, tier, float_value, stattrak}. Возвращает (дроп, успех)."""
    tier = inputs[0]["tier"]
    next_tier = rules.CONTRACT_NEXT[tier]
    success = rng.random() < rules.CONTRACT_SUCCESS[tier]
    out_tier = next_tier if success else tier

    # Как trade-up в CS2: случайный вход задаёт кейс, из него берётся предмет нужной ступени.
    source = items[rng.choice(inputs)["catalog_id"]]
    pool = []
    for case_id in source["cases"]:
        pool += cases[case_id]["pools"].get(out_tier, [])
    if not pool:
        for c in cases.values():
            pool += c["pools"].get(out_tier, [])
    item = items[rng.choice(sorted(set(pool)))]

    floats = [i["float_value"] for i in inputs if i.get("float_value") is not None]
    float_value = None
    if item.get("wears") and floats:
        avg = sum(floats) / len(floats)
        float_value = round(item["min_float"] + avg * (item["max_float"] - item["min_float"]), 6)
    stattrak = bool(item.get("stattrak")) and all(i.get("stattrak") for i in inputs)
    return make_drop(rng, item, stattrak=stattrak, float_value=float_value), success


# --- колесо ----------------------------------------------------------------


def spin_wheel(rng: FairRng) -> dict:
    sector = rng.weighted({s["id"]: s["p"] for s in rules.DAILY_WHEEL})
    return next(s for s in rules.DAILY_WHEEL if s["id"] == sector)
