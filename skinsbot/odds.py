"""Модель шансов из docs/ODDS.md: точный возврат с мягкими гарантиями, проверка конфига.

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
    if baseA <= 0 or baseB <= 0:  # в кейсе нет одной из групп — гарантии не действуют (game.apply_pity)
        return dict(odds)
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


def validate_config(cfg: dict, catalog=None) -> list[str]:
    """Проверки новой версии конфига перед включением (админка, M0/M8). Пустой список — всё хорошо.

    Возврат каждого кейса с гарантиями и средний множитель контракта должны быть в коридоре return_corridor."""
    errors = []
    lo, hi = cfg["return_corridor"]
    for code, c in cfg["currency"]["countries"].items():
        if not c.get("rate") or c["rate"] <= 0:
            errors.append(f"курс {code} должен быть больше нуля")
    for key in ("epic_plus", "legend_plus"):
        p = cfg["pity"][key]
        if not 0 < p["soft"] <= p["hard"]:
            errors.append(f"гарантия {key}: нужно 0 < soft ≤ hard")
    if not 0 < cfg["luck"]["max_boosted_share"] < 1:
        errors.append("удача: max_boosted_share должен быть меньше 1")
    caps = sorted((float(k), v) for k, v in cfg["luck"]["max_return"].items())
    if any(b[1] < a[1] for a, b in zip(caps, caps[1:])):
        errors.append("удача: потолок возврата должен расти с множителем")
    for code, prod in cfg["stars"]["products"].items():
        m = (prod["grants"].get("luck") or {}).get("mult")
        if m and not any(float(k) <= m for k in cfg["luck"]["max_return"]):
            errors.append(f"удача: нет потолка возврата для {code}")
    c = cfg["contracts"]
    if not 1 <= c["min_items"] <= c["max_items"]:
        errors.append("контракт: нужно 1 ≤ min_items ≤ max_items")
    total = sum(w for _, w in c["multipliers"])
    ev = sum(m * w for m, w in c["multipliers"]) / total if total else 0
    if not lo <= ev <= hi:
        errors.append(f"контракт: средний множитель {ev:.3f} вне коридора {lo:.0%}–{hi:.0%}")
    if cfg["upgrader"]["max_chance_pct"] > 100 or cfg["upgrader"]["k"] > 1:
        errors.append("апгрейд: шанс не может быть больше 100% и k больше 1")
    if catalog is not None:
        old = json.loads(json.dumps(rules.CFG))
        rules.activate(cfg)
        try:
            for name, r in catalog.rtps().items():
                if not lo <= r <= hi:
                    errors.append(f"кейс {name}: возврат с гарантиями {r:.1%} вне коридора {lo:.0%}–{hi:.0%}")
        finally:
            rules.activate(old)
    return errors
