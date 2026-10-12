"""Модель шансов из docs/ODDS.md: точный возврат с мягкими гарантиями, подбор шкалы уровня, проверка конфига.

Перенос reference/odds_lab.py на чистый Python. consolidated() совпадает с образцом до 3 знаков
(тест tests/test_odds.py сверяет их на шкале кейса).
"""

from __future__ import annotations

import json

from . import rules


def group_prob(base: float, n: int, soft: int, step: float, hard: int) -> float:
    """Шанс группы на n-м спине подряд без неё (n = 1, 2, …)."""
    if n >= hard:
        return 1.0
    if n >= soft:
        return min(1.0, base + (n - soft + 1) * step)
    return base


def consolidated(odds: dict[str, float], pity: dict | None = None) -> dict[str, float]:
    """Долгосрочные шансы ступеней с учётом двух вложенных гарантий (доли единицы).

    Цикл восстановления длится до выпадения группы «Легендарный+»; внутри цикла хранится
    распределение счётчика «Эпик+». Доля ступени = ожидаемое число выпадений за цикл / длина цикла.
    """
    pity = pity or rules.CFG["pity"]
    a_from, b_from = pity["epic_plus"]["from"], pity["legend_plus"]["from"]
    A, B = pity["epic_plus"], pity["legend_plus"]
    tiers = sorted(odds, key=rules.rank)
    p = [odds[t] for t in tiers]
    inB = [rules.at_least(t, b_from) for t in tiers]
    inA = [rules.at_least(t, a_from) and not inB[i] for i, t in enumerate(tiers)]
    low = [not (inA[i] or inB[i]) for i in range(len(tiers))]

    def norm(mask):
        s = sum(pi for pi, m in zip(p, mask) if m)
        return [pi / s if m and s else 0.0 for pi, m in zip(p, mask)]

    dB, dA, dL = norm(inB), norm(inA), norm(low)
    baseA = sum(pi for pi, a, b in zip(p, inA, inB) if a or b)
    baseB = sum(pi for pi, b in zip(p, inB) if b)
    HA, HB = A["hard"], B["hard"]
    qa_e = [group_prob(baseA, e + 1, A["soft"], A["step_pct"] / 100, HA) for e in range(HA)]
    alive = [0.0] * HA
    alive[0] = 1.0
    counts = [0.0] * len(tiers)
    steps = 0.0
    for l in range(HB):
        qb = group_prob(baseB, l + 1, B["soft"], B["step_pct"] / 100, HB)
        qa = [max(q, qb) for q in qa_e]
        m = sum(alive)
        steps += m
        to_a = sum(al * (q - qb) for al, q in zip(alive, qa))
        to_l = sum(al * (1 - q) for al, q in zip(alive, qa))
        for i in range(len(tiers)):
            counts[i] += m * qb * dB[i] + to_a * dA[i] + to_l * dL[i]
        nxt = [0.0] * HA
        nxt[0] = to_a
        for e in range(HA - 1):
            nxt[e + 1] += alive[e] * (1 - qa[e])
        nxt[-1] += alive[-1] * (1 - qa[-1])
        alive = nxt
        if sum(alive) < 1e-15:
            break
    return {t: c / steps for t, c in zip(tiers, counts)}


def rtp(odds: dict[str, float], tier_value: dict[str, float], price: float) -> float:
    return sum(p * tier_value[t] for t, p in odds.items()) / price


def shape(values: dict[str, float], alpha: float) -> dict[str, float]:
    """Шанс ступени обратно пропорционален её цене в степени α."""
    w = {t: v ** (-alpha) for t, v in values.items()}
    s = sum(w.values())
    return {t: x / s for t, x in w.items()}


def solve_level(values: dict[str, float], price: float, target: float | None = None) -> dict[str, float]:
    """Шкала уровня: подобрать α так, чтобы Σ p·L = target × цена (ТЗ, «Алгоритм построения уровня»)."""
    target = target or rules.CFG["target_return"]
    lo, hi = 0.01, 6.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if rtp(shape(values, mid), values, price) > target:
            lo = mid
        else:
            hi = mid
    odds = shape(values, (lo + hi) / 2)
    rounded = {t: round(p * 100, 2) for t, p in odds.items()}
    bottom = min(rounded, key=rules.rank)
    rounded[bottom] = round(rounded[bottom] + 100 - sum(rounded.values()), 2)
    return {t: p / 100 for t, p in rounded.items()}


def validate_config(cfg: dict, tier_values: dict[str, float] | None = None, case_rtps: dict[str, float] | None = None) -> list[str]:
    """Проверки новой версии конфига перед включением (админка, M0/M8). Пустой список — всё хорошо."""
    errors = []
    lo, hi = cfg["return_corridor"]

    def check_sum(name, odds):
        total = sum(odds.values())
        if abs(total - 100) > 0.01:
            errors.append(f"{name}: сумма шансов {total:.2f}%, а должна быть 100%")

    check_sum("кейс CS2", cfg["case_odds"])
    for key, lvl in cfg["levels"].items():
        if lvl["odds"] is None:
            continue
        odds = lvl["odds"]
        check_sum(f"уровень {lvl['name']}", odds)
        ordered = sorted(odds, key=rules.rank)
        if odds[ordered[0]] < cfg["level_checks"]["min_bottom_pct"]:
            errors.append(f"уровень {lvl['name']}: нижняя ступень меньше {cfg['level_checks']['min_bottom_pct']}%")
        if any(odds[a] < odds[b] for a, b in zip(ordered, ordered[1:])):
            errors.append(f"уровень {lvl['name']}: шансы должны убывать со ступенью")
    for code, c in cfg["currency"]["countries"].items():
        if not c.get("rate") or c["rate"] <= 0:
            errors.append(f"курс {code} должен быть больше нуля")
    if tier_values:
        old = json.loads(json.dumps(rules.CFG))
        rules.activate(cfg)
        try:
            odds = consolidated(rules.case_odds())
            ladder_price = sum(p * tier_values[t] for t, p in rules.case_odds().items()) / cfg["target_return"]
            r = rtp(odds, tier_values, ladder_price)
            if not lo <= r <= hi:
                errors.append(f"возврат кейса с гарантиями {r:.1%} вне коридора {lo:.0%}–{hi:.0%}")
        finally:
            rules.activate(old)
    for name, r in (case_rtps or {}).items():
        if not lo <= r <= hi:
            errors.append(f"{name}: возврат {r:.1%} вне коридора {lo:.0%}–{hi:.0%}")
    return errors
