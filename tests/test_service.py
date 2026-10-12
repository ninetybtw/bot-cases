from pathlib import Path

import pytest

from skinsbot import rules
from skinsbot.service import Catalog, Game, GameError

ROOT = Path(__file__).resolve().parent.parent


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def g(tmp_path):
    clock = Clock()
    game = Game(str(tmp_path / "t.sqlite3"), Catalog(ROOT / "data" / "catalog.json"), clock=clock)
    game.clock_obj = clock
    game.ensure_user(1, "Ann")
    return game


def test_new_user_has_start_balance_and_free_spins(g):
    s = g.state(1)
    assert s["user"]["balance"] == g.catalog.unit_cents * rules.START_BALANCE_CASES
    assert s["user"]["energy"] == 3
    assert s["items"] == []


def test_free_spin_uses_energy_and_regenerates(g):
    free = g.catalog.free_case_id
    g.spin(1, free, 1, free=True)
    g.spin(1, free, 1, free=True)
    g.spin(1, free, 1, free=True)
    with pytest.raises(GameError):
        g.spin(1, free, 1, free=True)
    g.clock_obj.t += rules.ENERGY_PERIOD + 1
    assert g.state(1)["user"]["energy"] == 1
    assert len(g.state(1)["items"]) == 3


def test_paid_spin_charges_and_is_idempotent(g):
    case = g.catalog.case_list[0]["id"]
    before = g.state(1)["user"]["balance"]
    r1 = g.spin(1, case, 1, idem_key="k1")
    r2 = g.spin(1, case, 1, idem_key="k1")
    assert r1 == r2
    after = g.state(1)
    assert after["user"]["balance"] == before - g.catalog.case_price_cents(case)
    assert len(after["items"]) == 1


def test_not_enough_money(g):
    case = g.catalog.case_list[-1]["id"]
    with pytest.raises(GameError) as e:
        for _ in range(20):
            g.spin(1, case, 10)
    assert e.value.code == "no_money"


def test_sell_and_lock(g):
    item = g.spin(1, g.catalog.free_case_id, 1, free=True)["items"][0]
    g.lock(1, item["id"], True)
    with pytest.raises(GameError):
        g.sell(1, [item["id"]])
    g.lock(1, item["id"], False)
    bal = g.state(1)["user"]["balance"]
    res = g.sell(1, [item["id"]])
    assert res["total"] == item["sell_price"] == round(item["price"] * 0.6)
    assert g.state(1)["user"]["balance"] == bal + res["total"]
    with pytest.raises(GameError):
        g.sell(1, [item["id"]])


def test_contract_five_rare(g):
    g.ensure_user(2, "Bob")
    case = g.catalog.case_list[0]["id"]
    rare = []
    while len(rare) < 5:
        g.conn.execute("UPDATE users SET balance=100000000 WHERE id=2")
        for it in g.spin(2, case, 10)["items"]:
            if it["tier"] == "rare":
                rare.append(it["id"])
    res = g.contract(2, rare[:5])
    assert res["success"] and res["item"]["tier"] == "epic"
    with pytest.raises(GameError):
        g.contract(2, rare[:5])


def test_upgrade_flow(g):
    item = g.spin(1, g.catalog.free_case_id, 1, free=True)["items"][0]
    g.spin(1, g.catalog.free_case_id, 1, free=True)
    targets = g.upgrade_targets(item["price"], 2)
    assert targets and all(t["price"] > item["price"] for t in targets)
    t = targets[0]
    res = g.upgrade(1, [item["id"]], 0, t["catalog_id"], t["key"])
    assert 0 < res["chance"] < 1
    ids = {i["id"] for i in g.state(1)["items"]}
    assert item["id"] not in ids
    assert res["success"] == ("item" in res)


def test_wheel_once_per_day(g):
    g.wheel(1)
    with pytest.raises(GameError):
        g.wheel(1)


def test_task_claim(g):
    with pytest.raises(GameError):
        g.claim_task(1, "spin3")
    for _ in range(3):
        g.spin(1, g.catalog.free_case_id, 1, free=True)
    res = g.claim_task(1, "spin3")
    assert res["coins"] > 0


def test_referral_reward_after_10_spins(g):
    g.ensure_user(3, "Friend", referrer=1)
    g.conn.execute("UPDATE users SET balance=100000000 WHERE id=3")
    g.spin(3, g.catalog.case_list[0]["id"], 10)
    assert g.state(1)["user"]["bonus_spins"] == 3


def test_fair_rotate_reveals_seed(g):
    import hashlib

    before = g.state(1)["user"]["fair"]
    res = g.rotate_seed(1, "my-seed")
    assert hashlib.sha256(res["revealed"]["server_seed"].encode()).hexdigest() == before["server_seed_hash"]
    assert res["next"]["client_seed"] == "my-seed"
