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


PITY_CASES = [c for c in CAT.case_list if game.group_chance(c["tier_odds"], "epic") > 0
              and game.group_chance(c["tier_odds"], "legendary") > 0]
ROOKIE = CAT.cases["rookie"]


@pytest.mark.parametrize("case", CAT.case_list, ids=lambda c: c["name"])
def test_case_odds_and_weights(case):
    """Шансы ступеней кейса — сумма шансов её предметов, всё вместе 100%; шанс предмета обратно пропорционален цене."""
    assert sum(case["weights"].values()) == pytest.approx(1)
    assert sum(case["tier_odds"].values()) == pytest.approx(1)
    for t, ids in case["pools"].items():
        assert case["tier_odds"][t] == pytest.approx(sum(case["weights"][i] for i in ids))
    by_value = sorted(case["weights"], key=lambda i: rules.item_expected_cents(CAT.items[i]))
    assert case["weights"][by_value[0]] >= case["weights"][by_value[-1]]
    assert "standoff" not in json.dumps(case).lower()


def test_consolidated_matches_reference_odds_lab():
    """consolidated() перенесён из reference/odds_lab.py и совпадает до 3 знаков на шкалах своих кейсов."""
    pytest.importorskip("numpy")
    import odds_lab

    pity = {k: {"soft": v["soft"], "step": v["step_pct"], "hard": v["hard"]} for k, v in rules.CFG["pity"].items()}
    no = {t: rules.CFG["tiers"][t]["no"] for t in rules.TIER_ORDER}
    for case in PITY_CASES[:6]:
        ref = odds_lab.consolidated({no[t]: p * 100 for t, p in case["tier_odds"].items()}, pity,
                                    a_tier=no["epic"], b_tier=no["legendary"])
        ours = odds.consolidated(case["tier_odds"])
        for t, p in ours.items():
            assert p * 100 == pytest.approx(ref[no[t]], abs=1e-3), (case["name"], t)


@pytest.mark.parametrize("case", CAT.case_list, ids=lambda c: c["name"])
def test_case_return_with_pity_in_corridor(case):
    """Возврат каждого своего кейса по рыночным ценам с учётом мягких гарантий 78–82%."""
    lo, hi = rules.CFG["return_corridor"]
    ev = rules.case_expected_cents(case, CAT.items, odds.consolidated(case["tier_odds"]))
    assert lo <= ev / case["price_cents"] <= hi
    assert case["price_cents"] == int(case["price_cents"])


def test_case_simulation_matches_consolidated():
    """200 000 открытий «Новичка» с гарантиями: доли ступеней как у consolidated() (3σ)."""
    n = 200_000
    rng = game.FairRng("ab" * 32, "sim", 0)
    since_e = since_l = 0
    counts: dict[str, int] = {}
    for _ in range(n):
        o, _ = game.spin_odds(ROOKIE["tier_odds"], since_e, since_l)
        t = rng.weighted(o)
        counts[t] = counts.get(t, 0) + 1
        since_e = 0 if rules.at_least(t, "epic") else since_e + 1
        since_l = 0 if rules.at_least(t, "legendary") else since_l + 1
    for t, p in odds.consolidated(ROOKIE["tier_odds"]).items():
        sigma = math.sqrt(n * p * (1 - p)) * 1.5  # гарантии дают слабую зависимость между спинами
        assert abs(counts.get(t, 0) - n * p) < 3 * sigma + 2, t


def test_validate_config():
    """Действующий конфиг проходит; гарантия, которая выводит возврат кейса из коридора, и контракт с EV 1,0 — нет."""
    assert odds.validate_config(rules.CFG, CAT) == []
    cfg = copy.deepcopy(rules.CFG)
    cfg["pity"]["epic_plus"].update(soft=3, hard=5)
    assert any("вне коридора" in e for e in odds.validate_config(cfg, CAT))
    cfg = copy.deepcopy(rules.CFG)
    cfg["contracts"]["multipliers"] = [[1.0, 1]]
    assert any("контракт" in e for e in odds.validate_config(cfg))
    cfg = copy.deepcopy(rules.CFG)
    cfg["currency"]["countries"]["KZ"]["rate"] = 0
    assert any("курс KZ" in e for e in odds.validate_config(cfg))


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
    case = ROOKIE["tier_odds"]
    rng = game.FairRng("ab" * 32, "freq", 0)
    counts = {t: 0 for t in case}
    for _ in range(n):
        counts[rng.weighted(case)] += 1
    for t, p in case.items():
        sigma = math.sqrt(n * p * (1 - p))
        assert abs(counts[t] - n * p) < 3 * sigma, t


@pytest.mark.parametrize("m", [1.5, 2, 3, 5, 10])
def test_luck_multipliers(m):
    """Удача ×m: скины дороже кейса в m раз чаще (пока не упёрлись в границы), возврат с удачей не ниже, чем без неё,
    и не выше luck.max_return; удача не трогает пропорции внутри дешёвых и внутри дорогих скинов."""
    cap = game.luck_max_return(m)
    for case in CAT.case_list:
        base_ret = rules.case_expected_cents(case, CAT.items) / case["price_cents"]
        w, eff = game.lucky_weights(case, CAT.items, m)
        assert sum(w.values()) == pytest.approx(1) and min(w.values()) >= 0
        assert sum(game.tier_odds_of(case, w).values()) == pytest.approx(1)
        ret = sum(w[i] * rules.item_expected_cents(CAT.items[i]) for i in w) / case["price_cents"]
        assert base_ret - 1e-9 <= ret <= max(cap, base_ret) + 1e-6, case["name"]
        assert 1 <= eff <= m
        dear = [i for i in w if rules.item_expected_cents(CAT.items[i]) > case["price_cents"]]
        cheap = [i for i in w if i not in dear]
        if dear and cheap and eff == m and sum(case["weights"][i] for i in dear) * m <= rules.CFG["luck"]["max_boosted_share"]:
            for i in dear:
                assert w[i] == pytest.approx(case["weights"][i] * m)
        if len(cheap) > 1 and dear:
            a, b = cheap[0], cheap[1]
            assert w[a] / w[b] == pytest.approx(case["weights"][a] / case["weights"][b])


def test_bigger_luck_is_never_worse():
    """×10 на любом кейсе даёт не меньше, чем ×5, ×5 не меньше ×3 и так далее: дорогая удача не обманка."""
    for case in CAT.case_list:
        rets = []
        for m in (1, 1.5, 2, 3, 5, 10):
            w, _ = game.lucky_weights(case, CAT.items, m)
            rets.append(sum(w[i] * rules.item_expected_cents(CAT.items[i]) for i in w))
        assert all(b >= a - 1e-6 for a, b in zip(rets, rets[1:])), case["name"]


def test_luck_only_on_cheaper_cases():
    limit = rules.CFG["luck"]["max_case_cents"]
    assert any(c["price_cents"] > limit for c in CAT.case_list)
    assert any(c["price_cents"] <= limit for c in CAT.case_list)


def test_pity_soft_and_hard():
    epic_plus = lambda o: game.group_chance(o, "epic")  # noqa: E731
    odds_ = ROOKIE["tier_odds"]
    base = epic_plus(odds_)
    assert epic_plus(game.spin_odds(odds_, 49, 0)[0]) == pytest.approx(base)
    assert epic_plus(game.spin_odds(odds_, 50, 0)[0]) == pytest.approx(base + 0.05)
    assert epic_plus(game.spin_odds(odds_, 59, 0)[0]) == pytest.approx(1.0)
    leg, applied = game.spin_odds(odds_, 0, 299)
    assert game.group_chance(leg, "legendary") == pytest.approx(1.0) and applied == "legend"
    o = game.spin_odds(odds_, 55, 0)[0]
    assert sum(o.values()) == pytest.approx(1)
    assert o["rare"] / o["uncommon"] == pytest.approx(odds_["rare"] / odds_["uncommon"])
    free = CAT.cases[CAT.free_case_id]["tier_odds"]
    assert game.spin_odds(free, 59, 299) == (free, None)  # в кейсе без Запрещённого гарантий нет


def test_floats_inside_item_range_and_items_from_case():
    rng = game.FairRng("cd" * 32, "float", 0)
    for case in CAT.case_list:
        ids = {i for p in case["pools"].values() for i in p}
        for _ in range(1000):
            d = game.open_case(rng, case, CAT.items, case["tier_odds"])
            assert d.catalog_id in ids
            it = CAT.items[d.catalog_id]
            if d.float_value is not None:
                assert it["min_float"] <= d.float_value <= it["max_float"]
                lo, hi = rules.wear_ranges()[d.wear]
                assert lo <= d.float_value <= hi


def test_reel_has_drop_at_stop_and_only_case_items():
    rng = random.Random(5)
    case = CAT.cases["knife"]
    strip = game.reel(rng, case, 60, 52, "X")
    assert strip[52] == "X" and len(strip) == 60
    assert all(i in case["weights"] for k, i in enumerate(strip) if k != 52)


def test_contract_return_about_80():
    """Контракт: средний множитель конфига 0,78–0,82; 10 000 контрактов возвращают 77–84% суммы входов."""
    lo, hi = rules.CFG["return_corridor"]
    ms = rules.CFG["contracts"]["multipliers"]
    assert lo <= sum(m * w for m, w in ms) / sum(w for _, w in ms) <= hi
    r = random.Random(7)
    ids = [i for i in sorted(CAT.items)]
    tin = tout = 0
    for k in range(10_000):
        rng = game.FairRng("ee" * 32, "contract", k)
        inputs = []
        for _ in range(r.randint(3, 10)):
            d = game.make_drop(rng, CAT.items[r.choice(ids)])
            inputs.append({"price_cents": d.price_cents, "float_value": d.float_value, "stattrak": d.stattrak})
        total = sum(i["price_cents"] for i in inputs)
        if total > CAT.contract_max_total():
            continue
        drop, mult = game.contract(rng, inputs, CAT.items)
        tin, tout = tin + total * mult, tout + drop.price_cents
    assert 0.98 <= tout / tin <= 1.02  # выбор результата не сдвигает цену от цели


def test_contract_float_and_stattrak():
    rng = game.FairRng("aa" * 32, "c", 1)
    inputs = [{"price_cents": 5000, "float_value": 0.02, "stattrak": True} for _ in range(5)]
    drop, _ = game.contract(rng, inputs, CAT.items)
    it = CAT.items[drop.catalog_id]
    if it.get("wears"):
        assert drop.float_value == pytest.approx(it["min_float"] + 0.02 * (it["max_float"] - it["min_float"]), abs=1e-6)
    assert drop.stattrak == bool(it.get("stattrak"))
    inputs[0]["stattrak"] = False
    assert not game.contract(rng, inputs, CAT.items)[0].stattrak
    r = game.contract_range(10000)
    assert r["min_cents"] == 2500 and r["max_cents"] == 100000 and 7800 <= r["ev_cents"] <= 8200


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
    """Среднее дневного колеса ≈ 1,08 цены опорного кейса (cases.unit_case)."""
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
