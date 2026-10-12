"""Чистая игровая логика без базы: генератор, шансы, дроп, апгрейдер, контракт, колёса, дуэли, розыгрыш.

Случайность — схема «обещание и раскрытие» (docs/ODDS.md):
u_i = HMAC-SHA256(server_seed, "client_seed:nonce:i"), первые 52 бита / 2^52.
server_seed хранится в hex, ключ HMAC — его байты, как в reference/odds_lab.py roll().
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from . import rules


def new_server_seed() -> str:
    return secrets.token_hex(rules.CFG["fairness"]["server_seed_bytes"])


def seed_hash(server_seed: str) -> str:
    return hashlib.sha256(bytes.fromhex(server_seed)).hexdigest()


def roll(server_seed: str, client_seed: str, nonce: int, i: int = 0) -> float:
    h = hmac.new(bytes.fromhex(server_seed), f"{client_seed}:{nonce}:{i}".encode(), hashlib.sha256).hexdigest()
    return int(h[:13], 16) / 2**52


class FairRng:
    """Поток чисел одного действия: u_0, u_1, … от одной пары зёрен и одного nonce."""

    def __init__(self, server_seed: str, client_seed: str, nonce: int):
        self.server_seed = server_seed
        self.client_seed = client_seed
        self.nonce = nonce
        self.i = 0

    def random(self) -> float:
        u = roll(self.server_seed, self.client_seed, self.nonce, self.i)
        self.i += 1
        return u

    def choice(self, seq):
        return seq[min(int(self.random() * len(seq)), len(seq) - 1)]

    def weighted(self, weights: dict):
        items = [(k, w) for k, w in weights.items() if w > 0]
        r = self.random() * sum(w for _, w in items)
        for k, w in items:
            r -= w
            if r < 0:
                return k
        return items[-1][0]


# --- шансы ---------------------------------------------------------------


def apply_luck(odds: dict[str, float], m: float) -> dict[str, float]:
    """Удача: все ступени кроме двух самых частых ×m, частые отдают разницу пропорционально."""
    if not m or m == 1:
        return dict(odds)
    donors = sorted(odds, key=lambda t: -odds[t])[: rules.CFG["luck"]["donors"]]
    boosted = {t: p * m for t, p in odds.items() if t not in donors}
    left = 1 - sum(boosted.values())
    donor_total = sum(odds[t] for t in donors)
    return {t: boosted[t] if t in boosted else odds[t] / donor_total * left for t in odds}


def apply_pity(odds: dict[str, float], since_epic: int, since_legendary: int) -> tuple[dict[str, float], str | None]:
    """Мягкая гарантия как tier_dist() в reference/odds_lab.py.
    since_* — сколько спинов подряд уже было без группы (без текущего). Шанс группы «Легендарный+» qb
    и «Эпик+» qa считаются по формуле ODDS.md, внутри групп ступени делят шанс пропорционально шкале."""
    groups = {key: (group, soft, step, hard) for key, group, soft, step, hard in rules.pity_groups()}
    b_group, b_soft, b_step, b_hard = groups["legend_plus"]
    a_group, a_soft, a_step, a_hard = groups["epic_plus"]
    base_b = sum(p for t, p in odds.items() if t in b_group)
    base_a = sum(p for t, p in odds.items() if t in a_group)
    if base_b <= 0 or base_a <= 0:
        return dict(odds), None
    from .odds import group_prob

    qb = group_prob(base_b, since_legendary + 1, b_soft, b_step, b_hard)
    qa = max(group_prob(base_a, since_epic + 1, a_soft, a_step, a_hard), qb)
    applied = "legend" if qb > base_b + 1e-12 else "epic" if qa > base_a + 1e-12 else None
    if not applied:
        return dict(odds), None
    only_a = {t: p for t, p in odds.items() if t in a_group and t not in b_group}
    low = {t: p for t, p in odds.items() if t not in a_group}
    out = {}
    for part, total in ((({t: odds[t] for t in odds if t in b_group}), qb), (only_a, qa - qb), (low, 1 - qa)):
        s = sum(part.values())
        for t, p in part.items():
            out[t] = p / s * total if s else 0.0
    return out, applied


def spin_odds(since_epic: int, since_legendary: int, luck: float = 1.0) -> tuple[dict[str, float], str | None]:
    return apply_pity(apply_luck(rules.case_odds(), luck), since_epic, since_legendary)


def one_in(p: float) -> int:
    return max(1, round(1 / p)) if p > 0 else 0


def group_chance(odds: dict[str, float], floor: str) -> float:
    return sum(p for t, p in odds.items() if rules.at_least(t, floor))


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


def item_price(item: dict, wear: str | None, stattrak: bool) -> int:
    return int(item["prices"][rules.price_key(wear, stattrak)])


def roll_wear(rng: FairRng, item: dict) -> tuple[str | None, float | None]:
    if not item.get("wears"):
        return None, None
    wear = rng.weighted(rules.wear_distribution(item))
    lo, hi = rules.wear_ranges()[wear]
    lo, hi = max(lo, item["min_float"]), min(hi, item["max_float"])
    return wear, round(lo + (hi - lo) * rng.random(), 6)


def wear_for_float(item: dict, value: float) -> str | None:
    if not item.get("wears"):
        return None
    for wear, (lo, hi) in rules.wear_ranges().items():
        if lo <= value < hi and wear in item["wears"]:
            return wear
    return item["wears"][-1]


def make_drop(rng: FairRng, item: dict, stattrak: bool | None = None, float_value: float | None = None,
              wear: str | None = None) -> Drop:
    if wear is not None and item.get("wears"):
        lo, hi = rules.wear_ranges()[wear]
        lo, hi = max(lo, item["min_float"]), min(hi, item["max_float"])
        float_value = round(lo + (hi - lo) * rng.random(), 6)
    elif float_value is None:
        wear, float_value = roll_wear(rng, item)
    else:
        wear = wear_for_float(item, float_value)
    if stattrak is None:
        stattrak = bool(item.get("stattrak")) and rng.random() < rules.stattrak_chance()
    return Drop(item["id"], item["tier"], wear, float_value, int(rng.random() * 1000), stattrak,
                item_price(item, wear, stattrak))


def open_pool(rng: FairRng, pools: dict[str, list[str]], items: dict[str, dict], odds: dict[str, float]) -> Drop:
    available = {t: p for t, p in odds.items() if pools.get(t)}
    tier = rng.weighted(available)
    return make_drop(rng, items[rng.choice(pools[tier])])


def reel(rng_cosmetic, pools: dict[str, list[str]], odds: dict[str, float], length: int, stop: int, drop_id: str) -> list[str]:
    """Лента для анимации: соседи по обычным шансам, без подставных редких рядом с остановкой.
    rng_cosmetic — обычный random.Random: лента не влияет на исход и не тратит числа честного броска."""
    available = {t: p for t, p in odds.items() if pools.get(t)}
    tiers, weights = zip(*available.items())
    out = []
    for i in range(length):
        if i == stop:
            out.append(drop_id)
            continue
        t = rng_cosmetic.choices(tiers, weights)[0]
        out.append(rng_cosmetic.choice(pools[t]))
    return out


# --- апгрейдер ------------------------------------------------------------


def upgrade_chance(stake_cents: int, target_cents: int, bonus_pct: float) -> float:
    """Шанс = ставка / цель × 0,8, после неудачи × (1 + 3%) на одну попытку, потолок 80%.

    Бонус относительный и не копится: с прибавкой +3 п.п. за каждую неудачу апгрейд ×10 возвращал бы
    больше 170% ставки и печатал бы монеты (tests/test_odds.py::test_upgrade_return)."""
    u = rules.CFG["upgrader"]
    if target_cents <= stake_cents or stake_cents <= 0:
        return 0.0
    chance = stake_cents / target_cents * u["k"] * (1 + bonus_pct / 100)
    return min(chance, u["max_chance_pct"] / 100)


# --- контракт «5 в 1» ----------------------------------------------------


def contract(rng: FairRng, inputs: list[dict], items: dict[str, dict], cases: dict[str, dict]) -> tuple[Drop, bool]:
    """inputs: экземпляры {catalog_id, tier, float_value, stattrak}. Возвращает (дроп, успех)."""
    c = rules.CFG["contracts"]
    tier = inputs[0]["tier"]
    guaranteed = rules.rank(tier) <= rules.rank(c["guaranteed_up_to"])
    success = guaranteed or rng.random() < c["success_pct"] / 100
    out_tier = c["next"][tier] if success else tier
    source = items[rng.choice(inputs)["catalog_id"]]
    pool = []
    for case_id in source["cases"]:
        if case_id in cases:
            pool += cases[case_id]["pools"].get(out_tier, [])
    if not pool:
        for cs in cases.values():
            pool += cs["pools"].get(out_tier, [])
    item = items[rng.choice(sorted(set(pool)))]
    floats = [i["float_value"] for i in inputs if i.get("float_value") is not None]
    float_value = None
    if item.get("wears") and floats:
        avg = sum(floats) / len(floats)
        float_value = round(item["min_float"] + avg * (item["max_float"] - item["min_float"]), 6)
    stattrak = bool(item.get("stattrak")) and all(i.get("stattrak") for i in inputs)
    return make_drop(rng, item, stattrak=stattrak, float_value=float_value), success


# --- колёса, дуэли, розыгрыш -------------------------------------------------


def spin_wheel(rng: FairRng, sectors: list[dict]) -> dict:
    sid = rng.weighted({s["id"]: s["p"] for s in sectors})
    return next(s for s in sectors if s["id"] == sid)


def duel_strength(rng: FairRng, prices: list[int]) -> float:
    lo, hi = rules.CFG["duels"]["casual"]["rand"]
    return sum(p * (lo + (hi - lo) * rng.random()) for p in prices)


def raffle_winners(seed: str, day: str, tickets: list[tuple[int, int]], prizes: int) -> list[int]:
    """Победители розыгрыша по раскрытому зерну: каждый билет — шанс, один приз на игрока.
    tickets — [(user_id, число билетов)] в порядке user_id, чтобы пересчёт совпадал у всех."""
    pool = sorted(tickets)
    winners: list[int] = []
    k = 0
    while len(winners) < prizes and pool:
        total = sum(n for _, n in pool)
        u = roll(seed, f"raffle-{day}", 0, k)
        k += 1
        x = u * total
        for idx, (uid, n) in enumerate(pool):
            x -= n
            if x < 0:
                winners.append(uid)
                pool.pop(idx)
                break
    return winners
