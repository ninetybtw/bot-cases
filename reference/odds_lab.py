"""Лаборатория шансов: точный расчёт гарантий, подбор шансов под возврат, «ощущение» сессии.

Запуск:
  python3 odds_lab.py report            — сравнение текущих настроек и предложенных
  python3 odds_lab.py solve 100 90       — подобрать шкалу под цену спина $100 и возврат 90%
  python3 odds_lab.py verify <server_seed_hex> <client_seed> <nonce>  — проверить бросок

Зависимости: numpy. Модель описана в ODDS.md.
"""
import hashlib
import hmac
import json
import sys

import numpy as np

# ---------------------------------------------------------------- модель гарантии
# Две вложенные гарантии: «Эпик и выше» (группа A, ступени >= a_tier) и «Легендарный и выше»
# (группа B, ступени >= b_tier, B входит в A). У каждой: с какого спина подряд растёт шанс (soft),
# на сколько процентных пунктов за спин (step), на каком спине шанс 100% (hard).
# Жёсткая гарантия без мягкой: soft = hard, step = 0.


def group_prob(base, n, soft, step, hard):
    """Шанс группы на n-м спине подряд без неё (n = 1, 2, ...)."""
    if n >= hard:
        return 1.0
    if n >= soft:
        return min(1.0, base + (n - soft + 1) * step)
    return base


def tier_dist(odds, a_tier, b_tier, pa, pb):
    """Распределение ступеней при шансе группы A = pa и группы B = pb (pb <= pa).
    Внутри групп шансы пропорциональны базовым."""
    t = np.array(sorted(odds), dtype=int)
    p = np.array([odds[k] for k in sorted(odds)], dtype=float) / 100
    out = np.zeros_like(p)
    inB = t >= b_tier
    inA = (t >= a_tier) & ~inB
    low = t < a_tier
    pb = min(pb, 1.0)
    pa = max(min(pa, 1.0), pb)
    out[inB] = p[inB] / p[inB].sum() * pb
    if inA.any():
        out[inA] = p[inA] / p[inA].sum() * (pa - pb)
    if low.any():
        out[low] = p[low] / p[low].sum() * (1 - pa)
    return dict(zip(t.tolist(), out.tolist()))


def consolidated(odds, pity, a_tier=4, b_tier=5):
    """Точные долгосрочные шансы ступеней с учётом обеих гарантий.
    Считается через цикл восстановления: цикл длится до выпадения группы B (обнуляет оба счётчика),
    внутри цикла отслеживаем распределение счётчика A. Доля ступени = ожидаемое число выпадений
    за цикл / ожидаемая длина цикла (теорема о восстановлении), без симуляции."""
    A, B = pity["epic_plus"], pity["legend_plus"]
    HA, HB = A["hard"], B["hard"]
    tiers = sorted(odds)
    p = np.array([odds[t] for t in tiers], float) / 100
    t = np.array(tiers)
    mB, mA, mL = t >= b_tier, (t >= a_tier) & (t < b_tier), t < a_tier
    dB = np.where(mB, p, 0) / p[mB].sum()
    dA = np.where(mA, p, 0) / p[mA].sum() if mA.any() else np.zeros_like(p)
    dL = np.where(mL, p, 0) / p[mL].sum()
    baseA, baseB = p[mA | mB].sum(), p[mB].sum()
    qa_e = np.array([group_prob(baseA, e + 1, A["soft"], A["step"] / 100, HA) for e in range(HA)])
    alive = np.zeros(HA)
    alive[0] = 1.0
    counts = np.zeros_like(p)
    steps = 0.0
    for l in range(HB):
        qb = group_prob(baseB, l + 1, B["soft"], B["step"] / 100, HB)
        qa = np.maximum(qa_e, qb)
        m = alive.sum()
        steps += m
        counts += m * qb * dB + (alive * (qa - qb)).sum() * dA + (alive * (1 - qa)).sum() * dL
        nxt = np.zeros(HA)
        nxt[0] = (alive * (qa - qb)).sum()
        nxt[1:] += (alive * (1 - qa))[:-1]
        nxt[-1] += (alive * (1 - qa))[-1]      # не бывает: на HA-м спине qa = 1
        alive = nxt
        if alive.sum() < 1e-15:
            break
    return dict(zip(tiers, (counts / steps * 100).tolist()))


def wait_stats(base_pct, soft, step, hard):
    """Среднее число спинов до группы и шанс упереться в жёсткую гарантию (одна гарантия отдельно)."""
    surv, mean, base = 1.0, 0.0, base_pct / 100
    for k in range(1, hard + 1):
        q = group_prob(base, k, soft, step / 100, hard)
        mean += surv * q * k
        if k == hard:
            p_hard = surv
        surv *= 1 - q
    return mean, p_hard


def rtp(rates, prices, spin_price):
    return sum(rates[t] / 100 * prices[t] for t in rates) / spin_price


# ---------------------------------------------------------------- подбор шкалы
def shape(prices, alpha, tiers):
    """Форма шкалы: шанс ступени обратно пропорционален её цене в степени alpha."""
    w = np.array([prices[t] ** (-alpha) for t in tiers])
    w = w / w.sum() * 100
    return dict(zip(tiers, w.tolist()))


def solve_alpha(prices, spin_price, target, tiers, pity=None):
    lo, hi = 0.3, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2
        o = shape(prices, mid, tiers)
        r = rtp(consolidated(o, pity) if pity else o, prices, spin_price)
        if r > target:
            lo = mid
        else:
            hi = mid
    return shape(prices, (lo + hi) / 2, tiers), (lo + hi) / 2


def calibrate(odds, prices, spin_price, target, pity, keep=(1, 2)):
    """Оставить форму текущей шкалы, но умножить все ступени, кроме самых частых, на f,
    чтобы возврат С УЧЁТОМ гарантий был равен target. Частые ступени забирают остаток."""
    def apply(f):
        o = {t: (v * f if t not in keep else v) for t, v in odds.items()}
        rest = 100 - sum(v for t, v in o.items() if t not in keep)
        ks = sum(odds[t] for t in keep)
        for t in keep:
            o[t] = odds[t] / ks * rest
        return o
    lo, hi = 0.3, 1.5
    for _ in range(60):
        mid = (lo + hi) / 2
        r = rtp(consolidated(apply(mid), pity), prices, spin_price)
        if r > target:
            hi = mid
        else:
            lo = mid
    return apply((lo + hi) / 2)


# ---------------------------------------------------------------- ощущение сессии
def session_feel(odds, prices, spin_price, pity, spins=30, sessions=40000, seed=7, warmup=400):
    """Монте-Карло: игрок делает `spins` спинов подряд. Возвращает медиану возврата,
    долю сессий в плюсе, долю «пустых» (возврат меньше 30%) и долю сессий с Эпиком+."""
    rng = np.random.default_rng(seed)
    A, B = pity["epic_plus"], pity["legend_plus"]
    tiers = sorted(odds)
    baseA = sum(v for k, v in odds.items() if k >= 4) / 100
    baseB = sum(v for k, v in odds.items() if k >= 5) / 100
    pr = np.array([prices[t] for t in tiers])
    tot = np.zeros(sessions)
    epic = np.zeros(sessions, bool)
    e = np.zeros(sessions, int)
    l = np.zeros(sessions, int)
    p = np.array([odds[t] for t in tiers]) / 100
    lowmask = np.array([t < 4 for t in tiers])
    amask = np.array([4 <= t < 5 for t in tiers])
    bmask = np.array([t >= 5 for t in tiers])
    cl = np.cumsum(p[lowmask]) / p[lowmask].sum()
    ca = np.cumsum(p[amask]) / p[amask].sum() if amask.any() else None
    cb = np.cumsum(p[bmask]) / p[bmask].sum()
    low_t, a_t, b_t = np.where(lowmask)[0], np.where(amask)[0], np.where(bmask)[0]
    gp = np.vectorize(group_prob)
    for step_no in range(warmup + spins):
        if step_no == warmup:
            tot[:] = 0
            epic[:] = False
        qb = gp(baseB, l + 1, B["soft"], B["step"] / 100, B["hard"])
        qa = np.maximum(gp(baseA, e + 1, A["soft"], A["step"] / 100, A["hard"]), qb)
        u = rng.random(sessions)
        v = rng.random(sessions)
        isB = u < qb
        isA = (~isB) & (u < qa)
        isL = ~(isB | isA)
        ti = np.empty(sessions, int)
        ti[isB] = b_t[np.searchsorted(cb, v[isB], side="right").clip(0, len(b_t) - 1)]
        if ca is not None:
            ti[isA] = a_t[np.searchsorted(ca, v[isA], side="right").clip(0, len(a_t) - 1)]
        ti[isL] = low_t[np.searchsorted(cl, v[isL], side="right").clip(0, len(low_t) - 1)]
        tot += pr[ti]
        epic |= isA | isB
        e = np.where(isA | isB, 0, e + 1)
        l = np.where(isB, 0, l + 1)
    r = tot / (spins * spin_price)
    return {"median": float(np.median(r)), "profit": float((r >= 1).mean()),
            "empty": float((r < 0.3).mean()), "epic": float(epic.mean())}


# ---------------------------------------------------------------- честный бросок
def roll(server_seed_hex, client_seed, nonce, i=0):
    """u в [0, 1): HMAC-SHA256(server_seed, f"{client_seed}:{nonce}:{i}"), первые 52 бита / 2^52."""
    h = hmac.new(bytes.fromhex(server_seed_hex), f"{client_seed}:{nonce}:{i}".encode(), hashlib.sha256).hexdigest()
    return int(h[:13], 16) / 2 ** 52


def pick_tier(u, rates):
    acc = 0.0
    for t in sorted(rates):
        acc += rates[t] / 100
        if u < acc:
            return t
    return max(rates)


# ---------------------------------------------------------------- отчёт
HARD = {"epic_plus": {"soft": 60, "step": 0, "hard": 60}, "legend_plus": {"soft": 300, "step": 0, "hard": 300}}
SOFT = {"epic_plus": {"soft": 51, "step": 5, "hard": 60}, "legend_plus": {"soft": 281, "step": 5, "hard": 300}}   # принято в конфигах
SOFT_WIDE = {"epic_plus": {"soft": 41, "step": 2.5, "hard": 60}, "legend_plus": {"soft": 221, "step": 0.5, "hard": 300}}


def fmt(o):
    return " ".join(f"{t}:{v:.3g}" for t, v in sorted(o.items()))


def block(name, odds, prices, price, pity, target=0.90):
    c = consolidated(odds, pity)
    f = session_feel(odds, prices, price, pity)
    print(f"  {name}")
    print(f"    шкала: {fmt(odds)}")
    print(f"    итоговые шансы с гарантией: {fmt(c)}")
    print(f"    возврат без гарантии {rtp(odds, prices, price):.1%}, с гарантией {rtp(c, prices, price):.1%}")
    print(f"    30 спинов: медиана {f['median']:.0%}, в плюсе {f['profit']:.1%}, пустых (<30%) {f['empty']:.1%}, с Эпиком+ {f['epic']:.0%}")
    return c


def report():
    here = __file__.rsplit("/", 1)[0] or "."
    ph = json.load(open(f"{here}/../config/phones.json", encoding="utf-8"))
    lad = {int(t): v / 100 for t, v in ph["ladder_cents"].items()}
    n = {int(t): v for t, v in ph["spins"]["normal"]["odds"].items()}
    price = ph["spins"]["normal"]["price_cents"] / 100
    print("НОМЕРА, обычный спин $100")
    block("сейчас: жёсткие гарантии 60/300", n, lad, price, HARD)
    block("ПРИНЯТО: мягкие гарантии 51+5 / 281+5, шкала прежняя", n, lad, price, SOFT)
    block("вариант: широкие мягкие 41+2,5 / 221+0,5 на прежней шкале", n, lad, price, SOFT_WIDE)
    cal = calibrate(n, lad, price, 0.90, SOFT_WIDE)
    block("вариант: широкие мягкие + шкала пересчитана на 90%", cal, lad, price, SOFT_WIDE)
    for nm, s, st, h in (("Эпик+", 51, 5, 60), ("Легендарный+", 281, 5, 300)):
        bp = sum(v for t, v in n.items() if t >= (4 if nm == "Эпик+" else 5))
        m0, p0 = wait_stats(bp, h, 0, h)
        m1, p1 = wait_stats(bp, s, st, h)
        print(f"    {nm}: ждать в среднем {m0:.1f} → {m1:.1f} спина, до жёсткой гарантии доходят {p0:.1%} → {p1:.2%}")
    al, a = solve_alpha(lad, price, 0.90, [1, 2, 3, 4, 5, 6], SOFT)
    print(f"  форма «шанс ~ 1/цена^α» под 90% с гарантией: α={a:.3f}: {fmt(al)}")

    ga = json.load(open(f"{here}/../config/garage.json", encoding="utf-8"))
    s = ga["spins"]["normal"]
    car = {int(t): v for t, v in s["odds_car"].items()}
    plate = {int(t): v for t, v in s["odds_plate"].items()}
    cl = {int(t): v / 100 for t, v in ga["ladder_cents"]["car"].items()}
    mult = sum(plate[t] / 100 * ga["plate_mult"][str(t)] for t in plate)
    gp = s["price_cents"] / 100
    print("\nГАРАЖ, спин $700 (гарантии по машине; цена пары = машина × средний множитель номера)")
    for nm, pity in (("сейчас: жёсткие 60/300", HARD), ("мягкие", SOFT)):
        c = consolidated(car, pity)
        print(f"  {nm}: возврат с гарантией {rtp(c, cl, gp / mult):.1%}")
    calg = calibrate(car, cl, gp / mult, 0.888, SOFT)
    print(f"  мягкие + шкала машин пересчитана на 88,8%: {fmt(calg)}")

    print("\nЧЕСТНЫЙ БРОСОК (пример)")
    ss = "9f" * 32
    print(f"  server_seed_hash = {hashlib.sha256(bytes.fromhex(ss)).hexdigest()[:16]}…  client_seed=artem nonce=1")
    u = roll(ss, "artem", 1)
    print(f"  u = {u:.6f} → ступень {pick_tier(u, consolidated(cal, SOFT))}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "report":
        report()
    elif cmd == "solve":
        price, target = float(sys.argv[2]), float(sys.argv[3]) / 100
        lad = {1: 10, 2: 30, 3: 100, 4: 400, 5: 2000, 6: 10000}
        o, a = solve_alpha(lad, price, target, sorted(lad), SOFT)
        print(f"α={a:.3f}", fmt(o))
    elif cmd == "verify":
        print(roll(sys.argv[2], sys.argv[3], int(sys.argv[4])))
