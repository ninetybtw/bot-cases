"""Операции с балансом и предметами: проверки M3, M5, M6, M7, M8 из docs/TASKS.md."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from skinsbot import rules
from skinsbot.service import Catalog, Game, GameError

ROOT = Path(__file__).resolve().parent.parent
CATALOG = Catalog(ROOT / "data" / "catalog.json")
DAY = 86400


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0  # 15.01.2027, 11:00 МСК

    def __call__(self):
        return self.t


@pytest.fixture
def g(tmp_path):
    clock = Clock()
    game = Game(str(tmp_path / "t.sqlite3"), CATALOG, clock=clock)
    game.clock_obj = clock
    game.ensure_user(1, "Ann")
    return game


def rich(g, uid, cents=100_000_000):
    """Выдать монеты через ledger, чтобы сумма журнала совпадала с балансом."""
    with g.tx():
        current = g.conn.execute("SELECT balance FROM players WHERE user_id=?", (uid,)).fetchone()[0]
        g._ledger(uid, cents - current, "test_grant")


def ledger_sum(g, uid):
    return g.conn.execute("SELECT SUM(delta) FROM ledger WHERE user_id=?", (uid,)).fetchone()[0]


def balance(g, uid):
    return g.me(uid)["balance_cents"]


def cheap_case(g):
    return g.catalog.free_case_id


# --- профиль и валюта --------------------------------------------------------------------


def test_new_player(g):
    me = g.me(1)
    assert me["balance_cents"] == g.catalog.unit_cents * rules.CFG["start_balance_cases"]
    assert me["energy"]["now"] == 3 and me["slots"]["total"] == 200
    assert me["currency"]["symbol"] == "₽" and me["country"] == "RU"
    assert ledger_sum(g, 1) == me["balance_cents"]


def test_country_by_language_and_30_days(g):
    g.ensure_user(5, "Asel", lang="kk")
    assert g.me(5)["country"] == "KZ"
    g.set_country(5, "KZ")
    with pytest.raises(GameError) as e:
        g.set_country(5, "BY")
    assert e.value.code == "NOT_ALLOWED_YET"
    g.clock_obj.t += 31 * DAY
    assert g.set_country(5, "BY")["currency"]["decimals"] == 2


def test_kz_and_ru_pay_same_kopecks(g):
    g.ensure_user(2, "Bob", lang="kk")
    case = g.catalog.case_list[0]["id"]
    before = {u: balance(g, u) for u in (1, 2)}
    for u in (1, 2):
        g.spin(u, case_id=case, count=1, confirmed=True)
    assert before[1] - balance(g, 1) == before[2] - balance(g, 2) == g.catalog.case_price(case)


def test_error_text_in_player_currency(g):
    g.ensure_user(3, "Bek", lang="kk")
    g.conn.execute("UPDATE players SET balance=0 WHERE user_id=3")
    with pytest.raises(GameError) as e:
        g.spin(3, case_id=cheap_case(g), count=1)
    assert e.value.code == "NOT_ENOUGH_COINS" and "₸" in e.value.message


# --- спин ----------------------------------------------------------------------------------


def test_free_spins_and_energy(g):
    g.spin(1, case_id=cheap_case(g), source="free", count=3)
    with pytest.raises(GameError) as e:
        g.spin(1, case_id=cheap_case(g), source="free", count=1)
    assert e.value.code == "NO_ENERGY"
    g.clock_obj.t += 3 * 3600 + 1
    assert g.me(1)["energy"]["now"] == 1


def test_free_only_on_free_case(g):
    other = next(c["id"] for c in g.catalog.case_list if c["id"] != cheap_case(g))
    with pytest.raises(GameError):
        g.spin(1, case_id=other, source="free")


def test_idempotency_50_parallel_same_key(g):
    rich(g, 1)
    case = cheap_case(g)
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda _: g.spin(1, case_id=case, count=1, confirmed=True, idem_key="same"), range(50)))
    assert all(r == results[0] for r in results)
    assert g.conn.execute("SELECT COUNT(*) FROM drops WHERE user_id=1").fetchone()[0] == 1


def test_50_parallel_spins_balance_for_10(g):
    case = cheap_case(g)
    price = g.catalog.case_price(case)
    rich(g, 1, price * 10)
    errors = []

    def go(n):
        try:
            g.spin(1, case_id=case, count=1, confirmed=True, idem_key=f"k{n}")
        except GameError as e:
            errors.append(e.code)

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(go, range(50)))
    assert g.conn.execute("SELECT COUNT(*) FROM drops WHERE user_id=1").fetchone()[0] == 10
    assert balance(g, 1) == 0
    assert set(errors) == {"NOT_ENOUGH_COINS"}
    assert ledger_sum(g, 1) == 0


def test_multi_spin_counts(g):
    rich(g, 1)
    for count in (3, 5, 10):
        r = g.spin(1, case_id=cheap_case(g), count=count, confirmed=True)
        assert len(r["drops"]) == count
        assert all(d["reel"][d["stop_index"]]["name"] == d["items"][0]["name"] for d in r["drops"])
    with pytest.raises(GameError):
        g.spin(1, case_id=cheap_case(g), count=2)


def test_confirm_required_without_pause(g):
    """Подтверждение дорогой покупки осталось, паузы 3 секунды больше нет."""
    rich(g, 1, 400_000)
    case = max(g.catalog.case_list, key=lambda c: c["price_cents"] if c["price_cents"] * 10 <= 400_000 else 0)["id"]
    with pytest.raises(GameError) as e:
        g.spin(1, case_id=case, count=10)
    assert e.value.code == "CONFIRM_REQUIRED" and "pause_s" not in e.value.extra
    g.spin(1, case_id=case, count=10, confirmed=True)


def test_pity_per_case(g):
    """Гарантии у каждого кейса свои: счётчик дешёвого кейса не переносится на дорогой."""
    rich(g, 1)
    with g.tx():
        g.conn.execute("INSERT INTO pity_counters (user_id, case_id, since_epic, since_legendary) VALUES (1,'rookie',59,0)")
    assert g.me(1)["pity"]["rookie"]["epic_chance_pct"] == 100
    assert g.me(1)["pity"]["knife"]["epic_in"] == 60
    r = g.spin(1, case_id="knife", count=1, confirmed=True)
    assert r["pity"]["epic_in"] in (59, 60)
    assert g.me(1)["pity"]["rookie"]["epic_in"] == 1
    r = g.spin(1, case_id="rookie", count=1, confirmed=True)
    assert rules.at_least(r["drops"][0]["items"][0]["tier"], "epic") and r["drops"][0]["pity_applied"] == "epic"
    assert g.me(1)["pity"]["rookie"]["epic_in"] == 60


def test_item_one_in_from_its_case(g):
    rich(g, 1)
    item = g.spin(1, case_id="rookie", count=1, confirmed=True)["drops"][0]["items"][0]
    assert item["one_in"] == max(1, round(1 / g.catalog.cases["rookie"]["weights"][item["catalog_id"]]))


def test_coin_daily_limit_grows_with_level(g):
    rich(g, 1)
    case = cheap_case(g)
    while (left := g.me(1)["coin_spins_left"]) > 0:
        g.spin(1, case_id=case, count=next(c for c in (10, 5, 3, 1) if c <= left), confirmed=True)
    with pytest.raises(GameError) as e:
        g.spin(1, case_id=case, count=1, confirmed=True)
    assert e.value.code == "DAILY_LIMIT"
    assert g.me(1)["user"]["level"] > 1  # опыт за спины поднял уровень, а с ним и лимит
    assert g.coin_spins_limit(1) == 30 and g.coin_spins_limit(31) == 60


def test_overflow_and_expiry(g):
    rich(g, 1)
    g.conn.execute("UPDATE players SET slots=5 WHERE user_id=1")
    r = g.spin(1, case_id=cheap_case(g), count=10, confirmed=True)
    assert r["overflow"] == 5
    me = g.me(1)
    assert me["slots"]["used"] == 5 and me["slots"]["overflow"] == 5
    g.spin(1, case_id=cheap_case(g), count=10, confirmed=True)
    g.spin(1, case_id=cheap_case(g), count=5, confirmed=True)
    with pytest.raises(GameError) as e:
        g.spin(1, case_id=cheap_case(g), count=1, confirmed=True)
    assert e.value.code == "INVENTORY_FULL"
    bal = balance(g, 1)
    g.clock_obj.t += 49 * 3600
    assert g.overflow_expire() == 20
    assert balance(g, 1) > bal and g.me(1)["slots"]["overflow"] == 0


def test_luck_spends_per_spin_and_only_on_cheaper_cases(g):
    rich(g, 1)
    g.conn.execute("UPDATE players SET luck_mult=5, luck_left=10 WHERE user_id=1")
    r = g.spin(1, case_id="rookie", count=3, confirmed=True)
    assert g.me(1)["luck"]["spins_left"] == 7 and 1 < r["luck_used"] <= 5
    dear = next(c["id"] for c in g.catalog.case_list if c["price_cents"] > rules.CFG["luck"]["max_case_cents"])
    r = g.spin(1, case_id=dear, count=1, confirmed=True)
    assert g.me(1)["luck"]["spins_left"] == 7 and r["luck_used"] is None


# --- коллекция ----------------------------------------------------------------------------------


def test_sell_and_undo_restore_state(g):
    item = g.spin(1, case_id=cheap_case(g), source="free")["drops"][0]["items"][0]
    before = g.conn.execute("SELECT state, owner, locked, hold_until, overflow_until FROM items WHERE id=?", (item["id"],)).fetchone()
    bal = balance(g, 1)
    sale = g.sell(1, [item["id"]])
    assert sale["total_cents"] == item["sell_cents"] == item["price_cents"] * 60 // 100
    assert balance(g, 1) == bal + sale["total_cents"]
    g.undo_sale(1, sale["sale_id"])
    after = g.conn.execute("SELECT state, owner, locked, hold_until, overflow_until FROM items WHERE id=?", (item["id"],)).fetchone()
    assert tuple(before) == tuple(after) and balance(g, 1) == bal
    with pytest.raises(GameError):
        g.undo_sale(1, sale["sale_id"])
    assert ledger_sum(g, 1) == balance(g, 1)


def test_undo_window_and_daily_limit(g):
    rich(g, 1)
    items = [d["items"][0]["id"] for d in g.spin(1, case_id=cheap_case(g), count=10, confirmed=True)["drops"]]
    for i in items[:5]:
        g.undo_sale(1, g.sell(1, [i], confirmed=True)["sale_id"])
    with pytest.raises(GameError) as e:
        g.undo_sale(1, g.sell(1, [items[5]], confirmed=True)["sale_id"])
    assert e.value.code == "DAILY_LIMIT"
    sale = g.sell(1, [items[6]], confirmed=True)
    g.clock_obj.t += 11 * 60
    with pytest.raises(GameError):
        g.undo_sale(1, sale["sale_id"])


def test_lock_blocks_sell_and_unlock_needs_confirm(g):
    item = g.spin(1, case_id=cheap_case(g), source="free")["drops"][0]["items"][0]
    g.lock(1, item["id"], True)
    with pytest.raises(GameError) as e:
        g.sell(1, [item["id"]])
    assert e.value.code == "ITEM_LOCKED"
    with pytest.raises(GameError):
        g.lock(1, item["id"], False)
    g.lock(1, item["id"], False, confirm=True)
    g.sell(1, [item["id"]], confirmed=True)


def test_showcase_and_duplicates(g):
    rich(g, 1)
    ids = [d["items"][0]["id"] for d in g.spin(1, case_id=cheap_case(g), count=10, confirmed=True)["drops"]]
    g.set_showcase(1, ids[:3])
    assert g.me(1)["showcase"] == ids[:3]
    quote = g.duplicates_quote(1)
    assert all(i in ids for i in quote["item_ids"])


# --- контракты и апгрейдер -------------------------------------------------------------------------


def spin_items(g, uid, n=10, case="rookie"):
    rich(g, uid)
    out = []
    while len(out) < n:
        out += [d["items"][0] for d in g.spin(uid, case_id=case, count=10, confirmed=True)["drops"]]
        g.conn.execute("UPDATE players SET coin_count=0 WHERE user_id=?", (uid,))
    return out[:n]


def test_contract_any_rarity(g):
    """Контракт: 3–10 предметов любой редкости, вместо них один предмет, баланс и журнал сходятся."""
    items = spin_items(g, 1, 12)
    tiers = {i["tier"] for i in items[:10]}
    ids = [i["id"] for i in items[:10]]
    with pytest.raises(GameError):
        g.contract(1, ids[:2])
    with pytest.raises(GameError):
        g.contract(1, [i["id"] for i in items[:11]])
    q = g.contract_quote(1, ids)
    assert q["total_cents"] == sum(i["price_cents"] for i in items[:10])
    assert q["min_cents"] < q["ev_cents"] < q["max_cents"]
    res = g.contract(1, ids, confirmed=True, idem_key="c1")
    assert res == g.contract(1, ids, confirmed=True, idem_key="c1")
    assert res["total_cents"] == q["total_cents"] and res["mult"] in [m for m, _ in rules.CFG["contracts"]["multipliers"]]
    left = {i["id"] for i in g.inventory(1)["items"]}
    assert not left & set(ids) and res["item"]["id"] in left
    assert len(tiers) >= 1
    with pytest.raises(GameError):
        g.contract(1, ids, confirmed=True)
    assert ledger_sum(g, 1) == balance(g, 1)


def test_upgrade_flow_and_config_changed(g):
    stake = spin_items(g, 1, 1)[0]
    target = g.upgrade_targets(stake["price_cents"], 2)[0]
    t = {"catalog_item_id": target["catalog_item_id"], "wear": target["wear"], "stattrak": target["stattrak"]}
    q = g.upgrade_quote(1, [stake["id"]], 0, t)
    with pytest.raises(GameError) as e:
        g.upgrade(1, [stake["id"]], 0, t, quote_chance_pct=q["chance_pct"] + 5)
    assert e.value.code == "CONFIG_CHANGED"
    res = g.upgrade(1, [stake["id"]], 0, t, quote_chance_pct=q["chance_pct"], confirmed=True)
    assert res["chance_pct"] <= 80
    assert res["success"] == ("item" in res)
    if not res["success"]:
        assert res["parts_cents"] == stake["price_cents"] * 10 // 100 and res["next_bonus_pct"] == 3


def test_upgrade_stake_has_no_collection_share_limit(g):
    """Ставить можно сколько угодно от коллекции и баланса: лимита «трети/половины» больше нет."""
    rich(g, 1, 1_000_000)
    target = g.upgrade_targets(1_500_000, 2)[0]
    t = {"catalog_item_id": target["catalog_item_id"], "wear": target["wear"], "stattrak": target["stattrak"]}
    res = g.upgrade(1, [], 1_000_000, t, confirmed=True)
    assert "success" in res and ledger_sum(g, 1) == balance(g, 1)


def test_quick_sell_by_rarity(g):
    items = spin_items(g, 1, 20)
    g.lock(1, items[0]["id"], True)
    groups = {x["id"]: x for x in g.sell_groups(1)}
    assert groups["all"]["count"] == 19
    tier = items[1]["tier"]
    q = g.sell_group_quote(1, tier)
    assert set(q["item_ids"]) == {i["id"] for i in items[1:] if i["tier"] == tier}
    assert q["total_cents"] == groups[tier]["total_cents"]
    res = g.sell(1, q["item_ids"], confirmed=True, idem_key="s1")
    assert res["total_cents"] == q["total_cents"]
    rest = g.sell_group_quote(1, "all")
    g.sell(1, rest["item_ids"], confirmed=True, idem_key="s2")
    assert [i["id"] for i in g.inventory(1)["items"]] == [items[0]["id"]]  # под замком остался
    assert ledger_sum(g, 1) == balance(g, 1)
    with pytest.raises(GameError):
        g.sell_group_quote(1, "standoff")


def test_bulk_sell_of_rare_needs_confirm(g):
    rich(g, 1)
    leg = next(i for i in sorted(g.catalog.items) if g.catalog.items[i]["tier"] == "legendary")
    from skinsbot import game as gm
    with g.tx():
        ids = [g._insert_item(1, gm.make_drop(gm.FairRng("ab" * 32, "x", n), g.catalog.items[leg]), "test") for n in range(2)]
    with pytest.raises(GameError) as e:
        g.sell(1, ids)
    assert e.value.code == "CONFIRM_REQUIRED"
    assert g.sell(1, ids, confirmed=True)["total_cents"] > 0


def test_banner_click_logged_without_reward(g):
    bal = balance(g, 1)
    r = g.banner_click(1, rules.CFG["banners"]["list"][0]["id"])
    assert r["url"].startswith("https://t.me/")
    assert balance(g, 1) == bal
    assert g.economy()["banner_clicks"] == {rules.CFG["banners"]["list"][0]["id"]: 1}
    with pytest.raises(GameError):
        g.banner_click(1, "nope")


# --- рынок и обмены ------------------------------------------------------------------------------------


def two_players(g):
    g.ensure_user(2, "Bob")
    rich(g, 1)
    rich(g, 2)
    g.clock_obj.t += 2 * DAY
    item = g.spin(1, case_id=cheap_case(g), count=1, confirmed=True)["drops"][0]["items"][0]
    return item


def test_market_band_fee_hold(g):
    item = two_players(g)
    with pytest.raises(GameError) as e:
        g.create_lot(1, item["id"], item["price_cents"] * 2)
    assert e.value.code == "PRICE_OUT_OF_BAND"
    lot = g.create_lot(1, item["id"], item["price_cents"])
    with pytest.raises(GameError) as e:
        g.sell(1, [item["id"]])
    assert e.value.code == "ITEM_BUSY"
    b1, b2 = balance(g, 1), balance(g, 2)
    res = g.buy_lot(2, lot["id"])
    fee = item["price_cents"] * 10 // 100
    assert res["fee_cents"] == fee
    assert balance(g, 1) == b1 + item["price_cents"] - fee and balance(g, 2) == b2 - item["price_cents"]
    with pytest.raises(GameError) as e:
        g.create_lot(2, item["id"], item["price_cents"])
    assert e.value.code == "ITEM_BUSY"  # удержание 24 ч
    assert ledger_sum(g, 1) == balance(g, 1) and ledger_sum(g, 2) == balance(g, 2)


def test_new_account_cannot_trade(g):
    g.ensure_user(2, "Bob")
    item = g.spin(2, case_id=cheap_case(g), source="free")["drops"][0]["items"][0]
    with pytest.raises(GameError) as e:
        g.create_lot(2, item["id"], item["price_cents"])
    assert e.value.code == "NOT_ALLOWED_YET"


def test_item_not_on_market_and_trade_at_once(g):
    item = two_players(g)
    g.create_lot(1, item["id"], item["price_cents"])
    with pytest.raises(GameError) as e:
        g.propose_trade(1, 2, [item["id"]], [])
    assert e.value.code == "ITEM_BUSY"


def test_race_market_vs_trade(g):
    item = two_players(g)
    results = []
    barrier = threading.Barrier(2)

    def lot():
        barrier.wait()
        try:
            g.create_lot(1, item["id"], item["price_cents"])
            results.append("lot")
        except GameError:
            pass

    def trade():
        barrier.wait()
        try:
            g.propose_trade(1, 2, [item["id"]], [])
            results.append("trade")
        except GameError:
            pass

    threads = [threading.Thread(target=lot), threading.Thread(target=trade)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1
    state = g.conn.execute("SELECT state FROM items WHERE id=?", (item["id"],)).fetchone()[0]
    assert state in ("market", "trade")


def test_buy_order_fills_from_lot(g):
    item = two_players(g)
    b2 = balance(g, 2)
    g.create_order(2, item["catalog_id"], item["wear"], item["stattrak"], item["price_cents"])
    assert balance(g, 2) == b2 - item["price_cents"]
    g.create_lot(1, item["id"], item["price_cents"] * 9 // 10)
    order = g.buy_orders(2)["orders"][0]
    assert order["status"] == "filled"
    assert balance(g, 2) == b2 - item["price_cents"] * 9 // 10
    assert ledger_sum(g, 2) == balance(g, 2)


def test_trade_accept(g):
    item = two_players(g)
    drops = g.spin(2, case_id=cheap_case(g), count=10, confirmed=True)["drops"]
    other = min((d["items"][0] for d in drops), key=lambda i: abs(i["price_cents"] - item["price_cents"]))
    if abs(item["price_cents"] - other["price_cents"]) / max(item["price_cents"], other["price_cents"]) > 0.5:
        with pytest.raises(GameError) as e:
            g.propose_trade(1, 2, [item["id"]], [other["id"]])
        assert e.value.code == "PRICE_OUT_OF_BAND"
        return
    t = g.propose_trade(1, 2, [item["id"]], [other["id"]])
    g.answer_trade(2, t["id"], "accept")
    assert g.conn.execute("SELECT owner FROM items WHERE id=?", (item["id"],)).fetchone()[0] == 2
    assert g.conn.execute("SELECT owner FROM items WHERE id=?", (other["id"],)).fetchone()[0] == 1


# --- удержание ----------------------------------------------------------------------------------------


def test_referral_after_10_spins(g):
    g.ensure_user(3, "Friend", referrer=1)
    rich(g, 3)
    assert g.me(3)["bonus_spins"] == 3
    g.spin(3, case_id=cheap_case(g), count=5, confirmed=True)
    assert g.me(1)["bonus_spins"] == 0
    g.spin(3, case_id=cheap_case(g), count=5, confirmed=True)
    assert g.me(1)["bonus_spins"] == 3


def test_wheel_daily_once_and_weekly_locked(g):
    g.wheel(1, "daily")
    with pytest.raises(GameError):
        g.wheel(1, "daily")
    with pytest.raises(GameError) as e:
        g.wheel(1, "weekly")
    assert e.value.code == "NOT_ALLOWED_YET"


def test_quests_tickets_and_raffle(g):
    g.spin(1, case_id=cheap_case(g), source="free", count=3)
    g.claim_quest(1, "spin3")
    assert g.raffle_today(1)["my_tickets"] == 1
    day = g.today()
    res = g.draw_raffle(day)
    assert res["winners"][0]["user_id"] == 1
    assert g.draw_raffle(day) is None
    from skinsbot import game as gm

    assert gm.seed_hash(res["seed"]) == res["seed_hash"]


def test_duel(g):
    g.ensure_user(2, "Bob")
    rich(g, 1)
    rich(g, 2)
    a = [d["items"][0]["id"] for d in g.spin(1, case_id=cheap_case(g), count=3, confirmed=True)["drops"]]
    b = [d["items"][0]["id"] for d in g.spin(2, case_id=cheap_case(g), count=3, confirmed=True)["drops"]]
    d = g.create_duel(1, a)
    with pytest.raises(GameError):
        g.sell(1, [a[0]])
    res = g.join_duel(2, d["id"], b)
    assert res["winner"] in (1, 2)
    assert g.conn.execute("SELECT state FROM items WHERE id=?", (a[0],)).fetchone()[0] == "free"


# --- Stars ----------------------------------------------------------------------------------------------


def test_payments_idempotent_and_month_cap(g):
    rules.CFG["stars"]["shop_enabled"] = True
    try:
        inv = g.create_invoice(1, "attempts_20")
        assert g.precheckout(inv["payload"], 1, 89) is None
        assert g.grant_payment(inv["payload"], "charge-1", 89)["granted"]
        assert not g.grant_payment(inv["payload"], "charge-1", 89)["granted"]
        assert g.me(1)["attempts"] == 20
        for n, code in enumerate(("vip_30", "luck_2", "slots_100")):
            p = g.create_invoice(1, code)
            assert g.grant_payment(p["payload"], f"c{n}", p["stars"])["granted"]
        assert g.shop(1)["month_spent"] == 89 + 249 + 49 + 99
        assert g.me(1)["luck"]["mult"] == 2.0 and g.me(1)["luck"]["spins_left"] == 10
        with pytest.raises(GameError) as e:
            g.create_invoice(1, "slots_100")  # 486 + 99 > 500
        assert e.value.code == "STARS_MONTH_CAP"
    finally:
        rules.CFG["stars"]["shop_enabled"] = False


def test_attempt_spin(g):
    g.conn.execute("UPDATE players SET paid_attempts=2 WHERE user_id=1")  # не деньги, ledger не нужен
    g.spin(1, case_id=cheap_case(g), source="attempt", count=1)
    assert g.me(1)["attempts"] == 1


# --- админка и журналы -----------------------------------------------------------------------------------


def test_append_only_journals(g):
    import sqlite3

    g.spin(1, case_id=cheap_case(g), source="free")
    for sql in ("UPDATE ledger SET delta=0", "DELETE FROM ledger", "DELETE FROM item_events"):
        with pytest.raises(sqlite3.DatabaseError):
            g.conn.execute(sql)


def test_rollback_keeps_history(g):
    item = g.spin(1, case_id=cheap_case(g), source="free")["drops"][0]["items"][0]
    sale = g.sell(1, [item["id"]])
    events_before = g.conn.execute("SELECT COUNT(*) FROM item_events WHERE item_id=?", (item["id"],)).fetchone()[0]
    g.rollback_sale(99, sale["sale_id"])
    events_after = g.conn.execute("SELECT COUNT(*) FROM item_events WHERE item_id=?", (item["id"],)).fetchone()[0]
    assert events_after == events_before + 1
    assert g.conn.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0] == 1
    assert ledger_sum(g, 1) == balance(g, 1)


def test_admin_config_validation(g):
    import copy

    bad = copy.deepcopy(rules.CFG)
    bad["contracts"]["multipliers"] = [[2.0, 1]]
    with pytest.raises(GameError):
        g.save_config(1, bad)
    ok = copy.deepcopy(rules.CFG)
    assert g.save_config(1, ok)["version"]


def test_piggy_tax(g):
    rich(g, 1, 6_000_000)
    g.piggy_tax()
    assert balance(g, 1) == 6_000_000 - 10_000
    g.piggy_tax()
    assert balance(g, 1) == 6_000_000 - 10_000


def test_notifications_limit_and_quiet(g):
    for n in range(4):
        with g.tx():
            g._notify(1, "test", f"n{n}")
    pending = g.pending_notifications()
    assert len(pending) == 2
    for p in pending:
        g.mark_sent(p["id"], True)
    assert g.pending_notifications() == []
    g.clock_obj.t += 11 * 3600  # 22:00 МСК — тишина
    with g.tx():
        g._notify(1, "test", "ночь")
    assert g.pending_notifications() == []


def test_fair_rolls_recomputable(g):
    from skinsbot import game as gm

    g.spin(1, case_id=cheap_case(g), source="free")
    fair = g.fair(1)
    roll = fair["rolls"][0]
    assert roll["server_seed"] is None
    revealed = g.rotate_seed(1, "my-seed")["revealed"]
    u0 = gm.roll(revealed["server_seed"], roll["client_seed"], roll["nonce"], 0)
    assert 0 <= u0 < 1
    assert gm.seed_hash(revealed["server_seed"]) == roll["seed_hash"]
    assert json.loads(json.dumps(g.fair(1)))["rolls"][0]["server_seed"] == revealed["server_seed"]
