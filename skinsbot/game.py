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


def lucky_weights(case: dict, items: dict[str, dict], m: float) -> tuple[dict[str, float], float]:
    """Удача ×m: скины дороже цены кейса выпадают в m раз чаще, остальные реже пропорционально.

    Дорогие вместе не больше luck.max_boosted_share. Если средний возврат кейса с удачей вышел бы больше
    luck.max_return[×m], множитель на этом кейсе снижается до этой границы. Удача никогда не снижает возврат:
    шанс переходит только от скинов не дороже кейса к скинам дороже кейса. Возвращает (веса, действующий множитель)."""
    w = case["weights"]
    if not m or m <= 1:
        return w, 1.0
    cfg = rules.CFG["luck"]
    price = case["price_cents"]
    value = {i: rules.item_expected_cents(items[i]) for i in w}
    boosted = [i for i in w if value[i] > price]
    rest = 1 - sum(w[i] for i in boosted)
    if not boosted or rest <= 0:
        return w, 1.0

    def apply(k: float) -> dict[str, float]:
        b = {i: w[i] * k for i in boosted}
        total = sum(b.values())
        if total > cfg["max_boosted_share"]:
            b = {i: x / total * cfg["max_boosted_share"] for i, x in b.items()}
            total = cfg["max_boosted_share"]
        return {i: b[i] if i in b else w[i] / rest * (1 - total) for i in w}

    def ret(o: dict[str, float]) -> float:
        return sum(o[i] * value[i] for i in o) / price

    cap = luck_max_return(m)
    if ret(apply(m)) <= cap:
        return apply(m), float(m)
    if ret(w) >= cap:
        return w, 1.0
    lo, hi = 1.0, float(m)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ret(apply(mid)) <= cap else (lo, mid)
    return apply(lo), lo


def luck_max_return(m: float) -> float:
    """Потолок среднего возврата для удачи ×m (luck.max_return, ближайший множитель не больше m)."""
    caps = {float(k): v for k, v in rules.CFG["luck"]["max_return"].items()}
    below = [k for k in caps if k <= m]
    return caps[max(below)] if below else 1.0


def tier_odds_of(case: dict, weights: dict[str, float]) -> dict[str, float]:
    return {t: sum(weights[i] for i in ids) for t, ids in case["pools"].items()}


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


def spin_odds(tier_odds: dict[str, float], since_epic: int, since_legendary: int) -> tuple[dict[str, float], str | None]:
    """Шкала кейса с мягкой гарантией (счётчики гарантий у каждого кейса свои). Удача уже в tier_odds."""
    return apply_pity(tier_odds, since_epic, since_legendary)


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


def open_case(rng: FairRng, case: dict, items: dict[str, dict], odds: dict[str, float],
              weights: dict[str, float] | None = None) -> Drop:
    """Ступень по шкале (с удачей и гарантией), затем предмет ступени по его шансу в кейсе (с удачей)."""
    weights = weights or case["weights"]
    available = {t: p for t, p in odds.items() if case["pools"].get(t)}
    tier = rng.weighted(available)
    item_id = rng.weighted({i: weights[i] for i in case["pools"][tier]})
    return make_drop(rng, items[item_id])


def reel(rng_cosmetic, case: dict, length: int, stop: int, drop_id: str) -> list[str]:
    """Лента для анимации: соседи по шансам кейса, без подставных редких рядом с остановкой.
    rng_cosmetic — обычный random.Random: лента не влияет на исход и не тратит числа честного броска."""
    ids, weights = zip(*case["weights"].items())
    picks = rng_cosmetic.choices(ids, weights, k=length)
    picks[stop] = drop_id
    return list(picks)


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


# --- контракт ---------------------------------------------------------------


def contract(rng: FairRng, inputs: list[dict], items: dict[str, dict]) -> tuple[Drop, float]:
    """Контракт как trade-up в CS2, но с любой редкостью входов.

    inputs: [{price_cents, float_value, stattrak}] — 3–10 предметов. Цена результата = сумма входов × множитель
    (contracts.multipliers, средний ≈ 0,8). Float результата = min + средний float входов × (max − min),
    из него износ; StatTrak, если все входы StatTrak. Результат — ближайший предмет дешевле или дороже цели,
    дороже с шансом (цель − дешевле) / (дороже − дешевле): средняя цена результата ровно равна цели.
    Возвращает (дроп, множитель)."""
    import bisect

    c = rules.CFG["contracts"]
    total = sum(i["price_cents"] for i in inputs)
    mult = float(rng.weighted({str(m): w for m, w in c["multipliers"]}))
    target = max(1.0, total * mult)
    floats = [i["float_value"] for i in inputs if i.get("float_value") is not None]
    avg = sum(floats) / len(floats) if floats else rng.random()
    want_st = all(i.get("stattrak") for i in inputs)
    cands = []
    for item_id in sorted(items):
        it = items[item_id]
        fv = wear = None
        if it.get("wears"):
            fv = round(it["min_float"] + avg * (it["max_float"] - it["min_float"]), 6)
            wear = wear_for_float(it, fv)
        st = want_st and bool(it.get("stattrak"))
        key = rules.price_key(wear, st)
        if key in it["prices"]:
            cands.append((it["prices"][key], item_id, fv, st))
    cands.sort()
    prices = [x[0] for x in cands]
    k = bisect.bisect_left(prices, target)
    lo, hi = cands[max(0, k - 1)], cands[min(k, len(cands) - 1)]
    u = rng.random()
    if hi[0] > lo[0] and lo[0] <= target <= hi[0]:
        pick = hi if u < (target - lo[0]) / (hi[0] - lo[0]) else lo
    else:
        pick = hi if k < len(cands) and prices[k] == target else (lo if k else hi)
    _, item_id, fv, st = pick
    return make_drop(rng, items[item_id], stattrak=st, float_value=fv), mult


def contract_range(total_cents: int) -> dict:
    ms = rules.CFG["contracts"]["multipliers"]
    w = sum(x for _, x in ms)
    return {"min_cents": round(total_cents * min(m for m, _ in ms)), "max_cents": round(total_cents * max(m for m, _ in ms)),
            "ev_cents": round(total_cents * sum(m * x for m, x in ms) / w),
            "table": [{"mult": m, "p": x / w} for m, x in ms]}


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
