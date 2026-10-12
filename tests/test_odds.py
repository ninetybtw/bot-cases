"""Экономика и шансы: проверки M0, M1, M2 и M6 из docs/TASKS.md."""

import copy
import json
import math
import random
import sys
from pathlib import Path

import pytest

from skinsbot import game, odds, rules
from skinsbot.catalog import Catalog

ROOT = Path(__file__).resolve().parent.parent
CAT = Catalog(ROOT / "data" / "catalog.json")
sys.path.insert(0, str(ROOT / "reference"))


def test_odds_sum_to_100():
    assert sum(rules.CFG["case_odds"].values()) == pytest.approx(100, abs=1e-9)
    for key, lvl in rules.CFG["levels"].items():
        if lvl["odds"]:
            assert sum(lvl["odds"].values()) == pytest.approx(100, abs=1e-9), key


def test_consolidated_matches_reference_odds_lab():
    """consolidated() перенесён из reference/odds_lab.py и совпадает до 3 знаков."""
    pytest.importorskip("numpy")
    import odds_lab

    num = {rules.CFG["tiers"][t]["no"]: p for t, p in rules.CFG["case_odds"].items()}
    pity = {k: {"soft": v["soft"], "step": v["step_pct"], "hard": v["hard"]} for k, v in rules.CFG["pity"].items()}
    ref = odds_lab.consolidated(num, pity, a_tier=4, b_tier=5)
    ours = odds.consolidated(rules.case_odds())
    for t, p in ours.items():
        assert p * 100 == pytest.approx(ref[rules.CFG["tiers"][t]["no"]], abs=1e-3)

    # Шкала с низкой базой, где гарантия реально работает.
    low = {1: 63.8, 2: 24.7, 3: 7.4, 4: 2.85, 5: 1.0, 6: 0.25}
    ref = odds_lab.consolidated(low, pity, a_tier=4, b_tier=5)
    names = {1: "common", 2: "uncommon", 3: "rare", 4: "epic", 5: "legendary", 6: "mythic"}
    ours = odds.consolidated({names[k]: v / 100 for k, v in low.items()})
    for k, v in ref.items():
        assert ours[names[k]] * 100 == pytest.approx(v, abs=1e-3)


@pytest.mark.parametrize("case", CAT.case_list, ids=lambda c: c["name"])
def test_case_return_with_pity_in_corridor(case):
    """Возврат кейса по рыночным ценам с учётом мягких гарантий 78–82%."""
    lo, hi = rules.CFG["return_corridor"]
    long_run = odds.consolidated(rules.case_odds())
    ev = rules.pool_expected_cents(long_run, case["pools"], CAT.items)
    assert lo <= ev / case["price_cents"] <= hi


@pytest.mark.parametrize("level", list(CAT.levels.values()), ids=lambda lv: lv["name"])
def test_level_return_in_corridor(level):
    lo, hi = rules.CFG["return_corridor"]
    ev = rules.pool_expected_cents(rules.level_odds(level["id"]), level["pools"], CAT.items)
    assert lo <= ev / level["price_cents"] <= hi
    checks = rules.CFG["level_checks"]
    bottom = level["from"]
    ref = sum(rules.item_expected_cents(CAT.items[i]) for i in level["pools"][bottom]) / len(level["pools"][bottom])
    assert level["price_cents"] >= checks["min_price_vs_bottom"] * ref


def test_ladder_case_price_about_505():
    """По лестнице цен кейс CS2 стоит около 505 ₽ (ТЗ, «Шансы по шкалам спина»)."""
    ladder = {t: v for t, v in rules.CFG["ladder_cents"].items() if t in rules.CFG["case_odds"]}
    wear_mult = sum(w["p"] * w["mult"] for w in rules.wear_table().values())
    st = 1 + rules.stattrak_chance() * (rules.CFG["stattrak"]["fallback_mult"] - 1)
    ev = sum(p * ladder[t] for t, p in rules.case_odds().items()) * wear_mult * st
    assert ev / rules.CFG["target_return"] / 100 == pytest.approx(505, abs=8)


def test_pity_on_low_base_scale_is_caught():
    """Гарантия на шкале с низкой базой (как коробка Standoff 2) выводит возврат из коридора — проверка это ловит."""
    cfg = copy.deepcopy(rules.CFG)
    cfg["case_odds"] = {"rare": 98.42, "epic": 1.42, "legendary": 0.14, "mythic": 0.02, "secret": 0.0}
    values = {"rare": 100, "epic": 4000, "legendary": 20000, "mythic": 100000, "secret": 500000}
    errors = odds.validate_config(cfg, values)
    assert any("вне коридора" in e for e in errors)


def test_validate_config_rejects_bad_sum():
    cfg = copy.deepcopy(rules.CFG)
    cfg["levels"]["rare"]["odds"]["rare"] = 80
    errors = odds.validate_config(cfg)
    assert any("сумма шансов" in e for e in errors)
    assert odds.validate_config(rules.CFG, CAT.tier_values(), CAT.rtps()) == []


def test_roll_matches_reference_verify():
    pytest.importorskip("numpy")
    import odds_lab

    seed = "9f" * 32
    for nonce in range(5):
        for i in range(3):
            assert game.roll(seed, "artem", nonce, i) == odds_lab.roll(seed, "artem", nonce, i)


def test_frequencies_one_million():
    """1 000 000 бросков ступени кейса совпадают со шкалой в пределах 3 стандартных отклонений."""
    n = 1_000_000
    case = rules.case_odds()
    rng = game.FairRng("ab" * 32, "freq", 0)
    counts = {t: 0 for t in case}
    for _ in range(n):
        counts[rng.weighted(case)] += 1
    for t, p in case.items():
        sigma = math.sqrt(n * p * (1 - p))
        assert abs(counts[t] - n * p) < 3 * sigma, t


def test_luck_x2_case():
    lucky = game.apply_luck(rules.case_odds(), 2)
    base = rules.case_odds()
    assert sum(lucky.values()) == pytest.approx(1)
    for t in ("legendary", "mythic", "secret"):
        assert lucky[t] == pytest.approx(base[t] * 2)
    assert lucky["rare"] / lucky["epic"] == pytest.approx(base["rare"] / base["epic"])


def test_pity_soft_and_hard():
    epic_plus = lambda o: game.group_chance(o, "epic")  # noqa: E731
    base = epic_plus(rules.case_odds())
    assert epic_plus(game.spin_odds(49, 0)[0]) == pytest.approx(base)
    assert epic_plus(game.spin_odds(50, 0)[0]) == pytest.approx(base + 0.05)
    assert epic_plus(game.spin_odds(59, 0)[0]) == pytest.approx(1.0)
    leg, applied = game.spin_odds(0, 299)
    assert game.group_chance(leg, "legendary") == pytest.approx(1.0) and applied == "legend"
    o = game.spin_odds(55, 0)[0]
    assert sum(o.values()) == pytest.approx(1)
    assert o["legendary"] / o["mythic"] == pytest.approx(rules.case_odds()["legendary"] / rules.case_odds()["mythic"])


def test_floats_inside_item_range():
    rng = game.FairRng("cd" * 32, "float", 0)
    for case in CAT.case_list:
        for _ in range(2000):
            d = game.open_pool(rng, case["pools"], CAT.items, rules.case_odds())
            it = CAT.items[d.catalog_id]
            if d.float_value is not None:
                assert it["min_float"] <= d.float_value <= it["max_float"]
                lo, hi = rules.wear_ranges()[d.wear]
                assert lo <= d.float_value <= hi


def test_upgrade_return():
    """100 000 апгрейдов со случайным множителем ×1,5…×10 возвращают в среднем 80–90% ставки."""
    rng = random.Random(1)
    u = rules.CFG["upgrader"]
    bonus, ret, stake = 0, 0, 100_000
    for _ in range(100_000):
        target = int(stake * rng.choice(u["multipliers"]))
        c = game.upgrade_chance(stake, target, bonus)
        if rng.random() < c:
            ret, bonus = ret + target, 0
        else:
            ret, bonus = ret + stake * u["fail_parts_pct"] // 100, min(u["max_bonus_pct"], bonus + u["fail_bonus_pct"])
    assert 0.80 <= ret / (100_000 * stake) <= 0.90
    assert game.upgrade_chance(100, 400, 0) == pytest.approx(0.20)
    assert game.upgrade_chance(100, 101, 3) == pytest.approx(0.80)  # потолок 80%


def test_daily_wheel_mean_about_108():
    """Среднее дневного колеса ≈ 1,08 цены самого дешёвого кейса (108 ₽ при спине 100 ₽)."""
    sectors = rules.CFG["wheel"]["daily"]
    rng = game.FairRng("ef" * 32, "wheel", 0)
    total = 0.0
    for _ in range(100_000):
        s = game.spin_wheel(rng, sectors)
        total += s.get("cases", 0) + s.get("spins", 0)
    assert total / 100_000 == pytest.approx(1.08, abs=0.03)


def test_raffle_reproducible():
    tickets = [(1, 3), (2, 1), (3, 5), (4, 2)]
    a = game.raffle_winners("12" * 32, "2026-10-12", tickets, 3)
    b = game.raffle_winners("12" * 32, "2026-10-12", list(reversed(tickets)), 3)
    assert a == b and len(set(a)) == 3


def test_catalog_prices_in_kopecks():
    data = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    assert data["usd_rub"] > 10
    assert all(isinstance(p, int) for it in data["items"].values() for p in it["prices"].values())
