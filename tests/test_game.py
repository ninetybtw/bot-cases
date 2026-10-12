import json
from pathlib import Path

import pytest

from skinsbot import game, rules
from skinsbot.auth import AuthError, sign_init_data, validate_init_data

ROOT = Path(__file__).resolve().parent.parent
CATALOG = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))


def test_case_odds_sum_to_one():
    assert sum(rules.CASE_TIER_ODDS.values()) == pytest.approx(1.0, abs=1e-9)


def test_rng_is_deterministic_and_uniform():
    a = game.FairRng("s", "c", 1)
    b = game.FairRng("s", "c", 1)
    xs = [a.random() for _ in range(5000)]
    assert xs[:5] == [b.random() for _ in range(5)]
    assert 0 <= min(xs) and max(xs) < 1
    assert sum(xs) / len(xs) == pytest.approx(0.5, abs=0.02)


def test_luck_keeps_total_and_boosts_top_tiers():
    odds = game.apply_luck(rules.CASE_TIER_ODDS, 2)
    assert sum(odds.values()) == pytest.approx(1.0)
    assert odds["secret"] == pytest.approx(rules.CASE_TIER_ODDS["secret"] * 2)
    assert odds["rare"] < rules.CASE_TIER_ODDS["rare"]


def test_pity_soft_and_hard():
    base = game.spin_odds(0, 0)
    epic_plus = lambda o: sum(o[t] for t in rules.PITY["epic"][0])  # noqa: E731
    assert epic_plus(game.spin_odds(49, 0)) == pytest.approx(epic_plus(base))
    assert epic_plus(game.spin_odds(50, 0)) == pytest.approx(epic_plus(base) + 0.05)
    assert epic_plus(game.spin_odds(59, 0)) == pytest.approx(1.0)
    leg = game.spin_odds(0, 299)
    assert sum(leg[t] for t in rules.PITY["legendary"][0]) == pytest.approx(1.0)
    for o in (game.spin_odds(55, 0), leg):
        assert sum(o.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("case", CATALOG["cases"], ids=lambda c: c["name"])
def test_case_return_is_about_90_percent(case):
    """Средняя цена дропа / цена спина в коридоре 88–92%."""
    items = CATALOG["items"]
    assert rules.case_expected_value(case, items) / case["price"] == pytest.approx(0.9, abs=0.02)
    # Монте-Карло по ступеням: частоты совпадают с раскрытыми шансами.
    rng = game.FairRng("rtp", case["id"], 0)
    odds = game.spin_odds(0, 0)
    n, counts = 20000, {t: 0 for t in odds}
    for _ in range(n):
        counts[game.open_case(rng, case, items, odds).tier] += 1
    assert counts["rare"] / n == pytest.approx(odds["rare"], abs=0.015)
    assert counts["epic"] / n == pytest.approx(odds["epic"], abs=0.012)


def test_drop_respects_float_range():
    rng = game.FairRng("f", "c", 0)
    case = CATALOG["cases"][0]
    for _ in range(3000):
        d = game.open_case(rng, case, CATALOG["items"], game.spin_odds(0, 0))
        it = CATALOG["items"][d.catalog_id]
        if d.float_value is not None:
            assert it["min_float"] <= d.float_value <= it["max_float"]
            lo, hi = rules.WEAR_RANGES[d.wear]
            assert lo <= d.float_value <= hi


def test_upgrade_return_below_100_percent():
    for mult in rules.UPGRADE_MULTIPLIERS:
        for bonus in (False, True):
            stake, target = 1000, int(1000 * mult)
            c = game.upgrade_chance(stake, target, bonus)
            ev = c * target + (1 - c) * stake * rules.UPGRADE_SCRAP
            assert ev / stake < 1.0
    assert game.upgrade_chance(100, 400, False) == pytest.approx(0.2125)
    assert game.upgrade_chance(500, 400, False) == 0


def test_contract_goes_one_tier_up():
    items, cases = CATALOG["items"], {c["id"]: c for c in CATALOG["cases"]}
    rare_ids = CATALOG["cases"][0]["pools"]["rare"][:5]
    inputs = [{"catalog_id": i, "tier": "rare", "float_value": 0.2, "stattrak": False} for i in rare_ids]
    for n in range(50):
        drop, ok = game.contract(game.FairRng("k", "c", n), inputs, items, cases)
        assert ok and drop.tier == "epic"
        assert CATALOG["cases"][0]["id"] in items[drop.catalog_id]["cases"]


def test_init_data_signature():
    token = "123:ABC"
    data = sign_init_data({"auth_date": "1700000000", "user": json.dumps({"id": 42, "first_name": "Ann"})}, token)
    assert validate_init_data(data, token, now=1700000100)["id"] == 42
    with pytest.raises(AuthError):
        validate_init_data(data, "123:XYZ", now=1700000100)
    with pytest.raises(AuthError):
        validate_init_data(data, token, now=1700000000 + 2 * 86400)
