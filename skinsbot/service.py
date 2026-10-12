"""Игровые операции поверх базы. Каждая операция — одна транзакция."""

from __future__ import annotations

import bisect
import json
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import game, rules
from .db import connect, transaction

MSK = ZoneInfo("Europe/Moscow")
ODDS_VERSION = "cs2-case-2026-10-12"


class GameError(Exception):
    """Понятная игроку ошибка: текст уходит прямо на экран."""

    def __init__(self, message: str, code: str = "error", **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


class Catalog:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.items: dict[str, dict] = data["items"]
        self.cases: dict[str, dict] = {c["id"]: c for c in data["cases"]}
        self.case_list: list[dict] = data["cases"]
        self.generated_at = data["generated_at"]
        cheapest = min(self.case_list, key=lambda c: c["price"])
        self.free_case_id = cheapest["id"]
        self.unit_cents = round(cheapest["price"] * 100)  # «цена кейса» для наград
        # Все сочетания предмет+износ+StatTrak с ценой — цели апгрейдера.
        self.targets = sorted(
            (round(p * 100), item_id, key)
            for item_id, it in self.items.items()
            for key, p in it["prices"].items()
        )

    def case_price_cents(self, case_id: str) -> int:
        return round(self.cases[case_id]["price"] * 100)


def today() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


class Game:
    def __init__(self, db_path: str, catalog: Catalog, clock=time.time):
        self.conn: sqlite3.Connection = connect(db_path)
        self.catalog = catalog
        self.clock = clock

    # --- пользователь ---------------------------------------------------

    def ensure_user(self, user_id: int, name: str = "", referrer: int | None = None) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row:
            if name and row["name"] != name:
                self.conn.execute("UPDATE users SET name=? WHERE id=?", (name, user_id))
            return row
        now = self.clock()
        start = self.catalog.unit_cents * rules.START_BALANCE_CASES
        if referrer == user_id:
            referrer = None
        if referrer and not self.conn.execute("SELECT 1 FROM users WHERE id=?", (referrer,)).fetchone():
            referrer = None
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO users (id, name, created_at, balance, energy, energy_ts, server_seed, client_seed, referrer)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, name, now, 0, rules.START_FREE_SPINS, now, secrets.token_hex(32), secrets.token_hex(8), referrer),
            )
            self._ledger(user_id, start, "start")
        return self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def _user(self, user_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise GameError("Профиль не найден. Открой бота заново.", "no_user")
        return row

    def _ledger(self, user_id: int, delta: int, kind: str, ref: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO ledger (user_id, ts, delta, kind, ref) VALUES (?,?,?,?,?)",
            (user_id, self.clock(), delta, kind, ref),
        )
        if delta:
            self.conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (delta, user_id))

    def _energy(self, user: sqlite3.Row) -> tuple[int, float]:
        """Пересчитать энергию по таймеру. Возвращает (энергия, момент последнего начисления)."""
        energy, ts, now = user["energy"], user["energy_ts"], self.clock()
        if energy >= rules.ENERGY_MAX:
            return energy, now
        gained = int((now - ts) // rules.ENERGY_PERIOD)
        if gained:
            energy = min(rules.ENERGY_MAX, energy + gained)
            ts = now if energy >= rules.ENERGY_MAX else ts + gained * rules.ENERGY_PERIOD
        return energy, ts

    def _daily(self, user_id: int) -> sqlite3.Row:
        day = today()
        self.conn.execute("INSERT OR IGNORE INTO daily (user_id, day) VALUES (?,?)", (user_id, day))
        return self.conn.execute("SELECT * FROM daily WHERE user_id=? AND day=?", (user_id, day)).fetchone()

    def _bump_daily(self, user_id: int, field: str, n: int = 1) -> None:
        self._daily(user_id)
        self.conn.execute(f"UPDATE daily SET {field}={field}+? WHERE user_id=? AND day=?", (n, user_id, today()))

    def _rng(self, user: sqlite3.Row, kind: str) -> game.FairRng:
        nonce = user["nonce"]
        self.conn.execute("UPDATE users SET nonce=nonce+1 WHERE id=?", (user["id"],))
        rng = game.FairRng(user["server_seed"], user["client_seed"], nonce)
        rng.kind = kind
        return rng

    def _log_roll(self, user: sqlite3.Row, rng: game.FairRng, result: dict) -> None:
        self.conn.execute(
            "INSERT INTO rolls (user_id, ts, kind, seed_hash, client_seed, nonce, odds_version, result)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (user["id"], self.clock(), rng.kind, game.seed_hash(rng.server_seed), rng.client_seed, rng.nonce,
             ODDS_VERSION, json.dumps(result, ensure_ascii=False)),
        )

    def _insert_item(self, user_id: int, drop: game.Drop, origin: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO items (owner, catalog_id, tier, wear, float_value, pattern, stattrak, origin, drop_price, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, drop.catalog_id, drop.tier, drop.wear, drop.float_value, drop.pattern, int(drop.stattrak),
             origin, drop.price_cents, self.clock()),
        )
        self._event(cur.lastrowid, "created", {"origin": origin, "owner": user_id})
        return cur.lastrowid

    def _event(self, item_id: int, kind: str, data: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO item_events (item_id, ts, kind, data) VALUES (?,?,?,?)",
            (item_id, self.clock(), kind, json.dumps(data or {}, ensure_ascii=False)),
        )

    def _set_state(self, row: sqlite3.Row, state: str, kind: str, data: dict | None = None) -> None:
        cur = self.conn.execute(
            "UPDATE items SET state=?, version=version+1 WHERE id=? AND version=?",
            (state, row["id"], row["version"]),
        )
        if cur.rowcount != 1:
            raise GameError("Предмет уже изменился. Обнови экран.", "conflict")
        self._event(row["id"], kind, data)

    def _free_slots(self, user_id: int) -> int:
        used = self.conn.execute(
            "SELECT COUNT(*) FROM items WHERE owner=? AND state!='gone'", (user_id,)
        ).fetchone()[0]
        return rules.INVENTORY_SLOTS - used

    def _owned(self, user_id: int, item_ids: list[int], allow_locked: bool = False) -> list[sqlite3.Row]:
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise GameError("Выбери предметы.", "bad_items")
        marks = ",".join("?" * len(item_ids))
        rows = self.conn.execute(
            f"SELECT * FROM items WHERE owner=? AND id IN ({marks})", (user_id, *item_ids)
        ).fetchall()
        if len(rows) != len(item_ids) or any(r["state"] == "gone" for r in rows):
            raise GameError("Предмет не найден в коллекции.", "bad_items")
        if not allow_locked and any(r["state"] == "locked" for r in rows):
            raise GameError("Предмет под замком. Сначала сними замок.", "locked")
        return rows

    def _idempotent(self, user_id: int, key: str | None):
        if not key:
            return None
        row = self.conn.execute("SELECT response, user_id FROM requests WHERE idem_key=?", (key,)).fetchone()
        if row and row["user_id"] == user_id:
            return json.loads(row["response"])
        return None

    def _remember(self, user_id: int, key: str | None, response: dict) -> None:
        if key:
            self.conn.execute(
                "INSERT INTO requests (idem_key, user_id, created_at, response) VALUES (?,?,?,?)",
                (key, user_id, self.clock(), json.dumps(response, ensure_ascii=False)),
            )

    # --- представление ---------------------------------------------------

    def item_view(self, row: sqlite3.Row) -> dict:
        it = self.catalog.items[row["catalog_id"]]
        price = game.item_price_cents(it, row["wear"], bool(row["stattrak"]))
        return {
            "id": row["id"],
            "catalog_id": row["catalog_id"],
            "name": it["name"],
            "weapon": it["weapon"],
            "finish": it["finish"],
            "tier": row["tier"],
            "game_rarity": it["game_rarity"],
            "image": it["image"],
            "wear": row["wear"],
            "float": row["float_value"],
            "pattern": row["pattern"],
            "stattrak": bool(row["stattrak"]),
            "price": price,
            "sell_price": round(price * rules.QUICK_SELL),
            "drop_price": row["drop_price"],
            "origin": row["origin"],
            "created_at": row["created_at"],
            "locked": row["state"] == "locked",
        }

    def state(self, user_id: int) -> dict:
        user = self._user(user_id)
        energy, energy_ts = self._energy(user)
        daily = self._daily(user_id)
        claimed = json.loads(daily["claimed"])
        progress = {"spin3": daily["spins"], "sell1": daily["sells"], "upgrade1": daily["upgrades"]}
        tasks = [
            {
                "id": t["id"],
                "title": t["title"],
                "goal": t["goal"],
                "progress": min(progress[t["id"]], t["goal"]),
                "reward": round(self.catalog.unit_cents * t["reward_cases"]),
                "claimed": t["id"] in claimed,
            }
            for t in rules.DAILY_TASKS
        ]
        rows = self.conn.execute(
            "SELECT * FROM items WHERE owner=? AND state!='gone' ORDER BY id DESC", (user_id,)
        ).fetchall()
        items = [self.item_view(r) for r in rows]
        odds = game.spin_odds(user["since_epic"], user["since_legendary"])
        return {
            "user": {
                "id": user_id,
                "name": user["name"],
                "balance": user["balance"],
                "energy": energy,
                "energy_max": rules.ENERGY_MAX,
                "next_energy_in": 0 if energy >= rules.ENERGY_MAX else round(energy_ts + rules.ENERGY_PERIOD - self.clock()),
                "bonus_spins": user["bonus_spins"],
                "total_spins": user["total_spins"],
                "slots": rules.INVENTORY_SLOTS,
                "upgrade_bonus": bool(user["upgrade_bonus"]),
                "upgrades_left": rules.UPGRADE_DAILY_LIMIT - daily["upgrades"],
                "epic_chance_now": round(sum(odds[t] for t in rules.PITY["epic"][0]), 4),
                "break_reminder": daily["spins"] >= rules.BREAK_REMINDER_SPINS,
                "fair": {
                    "server_seed_hash": game.seed_hash(user["server_seed"]),
                    "client_seed": user["client_seed"],
                    "nonce": user["nonce"],
                },
            },
            "items": items,
            "collection_value": sum(i["price"] for i in items),
            "wheel_available": not daily["wheel"],
            "tasks": tasks,
            "feed": self.feed(),
        }

    def feed(self, limit: int = 12) -> list[dict]:
        rows = self.conn.execute(
            "SELECT i.*, u.name AS owner_name FROM items i JOIN users u ON u.id=i.owner"
            " WHERE i.tier IN ('legendary','mythic','secret') OR (i.tier='epic' AND i.origin LIKE 'case:%')"
            " ORDER BY i.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{**self.item_view(r), "owner_name": r["owner_name"] or "Игрок"} for r in rows]

    def cases_view(self) -> dict:
        cases = []
        for c in self.catalog.case_list:
            pools = {t: [self._catalog_brief(i) for i in ids] for t, ids in c["pools"].items()}
            cases.append({
                "id": c["id"], "name": c["name"], "image": c["image"], "price": self.catalog.case_price_cents(c["id"]),
                "ev": round(c["ev"] * 100), "free": c["id"] == self.catalog.free_case_id, "pools": pools,
            })
        odds = [
            {"tier": t, "p": p, "one_in": game.one_in(p)} for t, p in rules.CASE_TIER_ODDS.items()
        ]
        return {"cases": cases, "odds": odds, "tiers": rules.TIERS, "wheel": rules.DAILY_WHEEL,
                "unit": self.catalog.unit_cents, "generated_at": self.catalog.generated_at}

    def _catalog_brief(self, item_id: str) -> dict:
        it = self.catalog.items[item_id]
        prices = it["prices"].values()
        return {"id": item_id, "name": it["name"], "image": it["image"], "tier": it["tier"],
                "min": round(min(prices) * 100), "max": round(max(prices) * 100)}

    # --- спин -----------------------------------------------------------

    def spin(self, user_id: int, case_id: str, count: int = 1, free: bool = False, idem_key: str | None = None) -> dict:
        if count not in rules.SPINS_PER_REQUEST:
            raise GameError("Можно крутить ×1, ×5 или ×10.", "bad_count")
        if case_id not in self.catalog.cases:
            raise GameError("Такого кейса нет.", "bad_case")
        with transaction(self.conn):
            cached = self._idempotent(user_id, idem_key)
            if cached:
                return cached
            user = self._user(user_id)
            if self._free_slots(user_id) < count:
                raise GameError("Коллекция заполнена. Продай лишнее, чтобы крутить дальше.", "full")
            energy, energy_ts = self._energy(user)
            if free:
                if case_id != self.catalog.free_case_id:
                    raise GameError("Бесплатные спины работают на самом дешёвом кейсе.", "bad_case")
                if energy + user["bonus_spins"] < count:
                    raise GameError("Бесплатные спины закончились.", "no_spins")
                use_energy = min(energy, count)
                was_full = energy >= rules.ENERGY_MAX
                energy -= use_energy
                if was_full and use_energy:
                    energy_ts = self.clock()  # таймер стартует, когда запас перестал быть полным
                self.conn.execute(
                    "UPDATE users SET energy=?, energy_ts=?, bonus_spins=bonus_spins-? WHERE id=?",
                    (energy, energy_ts, count - use_energy, user_id),
                )
            else:
                cost = self.catalog.case_price_cents(case_id) * count
                if user["balance"] < cost:
                    raise GameError(f"Не хватает ${(cost - user['balance']) / 100:.2f}", "no_money",
                                    need=cost - user["balance"])
                self.conn.execute("UPDATE users SET energy=?, energy_ts=? WHERE id=?", (energy, energy_ts, user_id))
                self._ledger(user_id, -cost, "spin", case_id)

            case = self.catalog.cases[case_id]
            since_epic, since_leg = user["since_epic"], user["since_legendary"]
            rng = self._rng(user, "spin")
            drops = []
            for _ in range(count):
                odds = game.spin_odds(since_epic, since_leg)
                drop = game.open_case(rng, case, self.catalog.items, odds)
                rank = rules.TIER_ORDER.index(drop.tier)
                since_epic = 0 if rank >= rules.TIER_ORDER.index("epic") else since_epic + 1
                since_leg = 0 if rank >= rules.TIER_ORDER.index("legendary") else since_leg + 1
                item_id = self._insert_item(user_id, drop, f"case:{case_id}")
                drops.append((item_id, odds[drop.tier]))
            self.conn.execute(
                "UPDATE users SET since_epic=?, since_legendary=?, total_spins=total_spins+? WHERE id=?",
                (since_epic, since_leg, count, user_id),
            )
            self._bump_daily(user_id, "spins", count)
            self._log_roll(user, rng, {"case": case_id, "items": [d[0] for d in drops]})
            self._referral_check(user_id)

            result_items = []
            for item_id, p in drops:
                row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
                result_items.append({**self.item_view(row), "chance": round(p, 6), "one_in": game.one_in(p)})
            response = {"items": result_items, "nonce": rng.nonce, "case_id": case_id}
            self._remember(user_id, idem_key, response)
            return response

    def _referral_check(self, user_id: int) -> None:
        user = self._user(user_id)
        if user["referrer"] and not user["referral_paid"] and user["total_spins"] >= 10:
            self.conn.execute("UPDATE users SET referral_paid=1 WHERE id=?", (user_id,))
            self.conn.execute("UPDATE users SET bonus_spins=bonus_spins+3 WHERE id=?", (user["referrer"],))

    # --- продажа и замок --------------------------------------------------

    def sell(self, user_id: int, item_ids: list[int], idem_key: str | None = None) -> dict:
        with transaction(self.conn):
            cached = self._idempotent(user_id, idem_key)
            if cached:
                return cached
            rows = self._owned(user_id, item_ids)
            if len(rows) > 1 and any(rules.TIER_ORDER.index(r["tier"]) >= rules.TIER_ORDER.index("legendary") for r in rows):
                raise GameError("Легендарные и выше продаются только по одному.", "single_only")
            total = 0
            for r in rows:
                price = self.item_view(r)["sell_price"]
                total += price
                self._set_state(r, "gone", "sold", {"price": price})
            self._ledger(user_id, total, "sell", ",".join(str(i) for i in item_ids))
            self._bump_daily(user_id, "sells", len(rows))
            response = {"sold": item_ids, "total": total, "balance": self._user(user_id)["balance"]}
            self._remember(user_id, idem_key, response)
            return response

    def lock(self, user_id: int, item_id: int, locked: bool) -> dict:
        with transaction(self.conn):
            row = self._owned(user_id, [item_id], allow_locked=True)[0]
            if (row["state"] == "locked") != locked:
                self._set_state(row, "locked" if locked else "free", "lock" if locked else "unlock")
        return {"id": item_id, "locked": locked}

    # --- апгрейдер ------------------------------------------------------

    def upgrade_targets(self, stake_cents: int, multiplier: float | None = None, limit: int = 30) -> list[dict]:
        lo = stake_cents * (multiplier or 1.2)
        hi = lo * (1.25 if multiplier else 50)
        out = []
        start = bisect.bisect_left(self.catalog.targets, (int(lo),))
        for price, item_id, key in self.catalog.targets[start:]:
            if price > hi:
                break
            it = self.catalog.items[item_id]
            st, _, wear = key.rpartition("_")
            out.append({
                "catalog_id": item_id, "key": key, "name": it["name"], "image": it["image"], "tier": it["tier"],
                "wear": None if wear == "-" else wear, "stattrak": bool(st), "price": price,
            })
        # Равномерная выборка по диапазону, чтобы список не забивался одним ценником.
        if len(out) > limit:
            step = len(out) / limit
            out = [out[int(i * step)] for i in range(limit)]
        return out

    def upgrade(self, user_id: int, item_ids: list[int], coins: int, target_id: str, target_key: str,
                idem_key: str | None = None) -> dict:
        if len(item_ids) > rules.UPGRADE_MAX_ITEMS:
            raise GameError("В апгрейдер можно положить до 3 предметов.", "too_many")
        if coins < 0:
            raise GameError("Неверная сумма.", "bad_coins")
        target = self.catalog.items.get(target_id)
        if not target or target_key not in target["prices"]:
            raise GameError("Цель не найдена.", "bad_target")
        with transaction(self.conn):
            cached = self._idempotent(user_id, idem_key)
            if cached:
                return cached
            user = self._user(user_id)
            daily = self._daily(user_id)
            if daily["upgrades"] >= rules.UPGRADE_DAILY_LIMIT:
                raise GameError("На сегодня апгрейды закончились. Возвращайся завтра.", "limit")
            rows = self._owned(user_id, item_ids) if item_ids else []
            if not rows and not coins:
                raise GameError("Положи предмет или монеты.", "empty")
            if user["balance"] < coins:
                raise GameError("Не хватает монет.", "no_money")
            stake = sum(self.item_view(r)["price"] for r in rows) + coins
            all_value = sum(
                self.item_view(r)["price"]
                for r in self.conn.execute("SELECT * FROM items WHERE owner=? AND state!='gone'", (user_id,))
            ) + user["balance"]
            if stake > all_value / 2:
                raise GameError("За раз можно поставить не больше половины коллекции и баланса.", "too_big")
            st, _, wear = target_key.rpartition("_")
            wear = None if wear == "-" else wear
            target_price = game.item_price_cents(target, wear, bool(st))
            chance = game.upgrade_chance(stake, target_price, bool(user["upgrade_bonus"]))
            if chance <= 0:
                raise GameError("Цель должна стоить дороже ставки.", "bad_target")
            if not rows and self._free_slots(user_id) < 1:
                raise GameError("Коллекция заполнена.", "full")

            rng = self._rng(user, "upgrade")
            roll = rng.random()
            success = roll < chance
            for r in rows:
                self._set_state(r, "gone", "upgrade_stake", {"target": target_id})
            if coins:
                self._ledger(user_id, -coins, "upgrade_stake")
            result: dict = {"success": success, "chance": chance, "roll": roll, "stake": stake,
                            "target_price": target_price}
            if success:
                lo, hi = rules.WEAR_RANGES[wear] if wear else (0, 1)
                fv = None
                if wear:
                    lo, hi = max(lo, target["min_float"]), min(hi, target["max_float"])
                    fv = round(lo + (hi - lo) * rng.random(), 6)
                drop = game.Drop(target_id, target["tier"], wear, fv, int(rng.random() * 1000), bool(st), target_price)
                new_id = self._insert_item(user_id, drop, "upgrade")
                result["item"] = self.item_view(self.conn.execute("SELECT * FROM items WHERE id=?", (new_id,)).fetchone())
                self.conn.execute("UPDATE users SET upgrade_bonus=0 WHERE id=?", (user_id,))
            else:
                scrap = round(stake * rules.UPGRADE_SCRAP)
                self._ledger(user_id, scrap, "upgrade_scrap")
                result["scrap"] = scrap
                self.conn.execute("UPDATE users SET upgrade_bonus=1 WHERE id=?", (user_id,))
            self._bump_daily(user_id, "upgrades")
            self._log_roll(user, rng, {"chance": chance, "roll": roll, "success": success})
            result["balance"] = self._user(user_id)["balance"]
            self._remember(user_id, idem_key, result)
            return result

    # --- контракт «5 в 1» ------------------------------------------------

    def contract(self, user_id: int, item_ids: list[int], idem_key: str | None = None) -> dict:
        if len(item_ids) != rules.CONTRACT_SIZE:
            raise GameError("Нужно ровно 5 предметов.", "bad_items")
        with transaction(self.conn):
            cached = self._idempotent(user_id, idem_key)
            if cached:
                return cached
            user = self._user(user_id)
            rows = self._owned(user_id, item_ids)
            tiers = {r["tier"] for r in rows}
            if len(tiers) != 1:
                raise GameError("Все 5 предметов должны быть одной ступени.", "mixed")
            tier = tiers.pop()
            if tier not in rules.CONTRACT_NEXT:
                raise GameError("Эту ступень контрактом не поднять.", "top")
            rng = self._rng(user, "contract")
            inputs = [{"catalog_id": r["catalog_id"], "tier": r["tier"], "float_value": r["float_value"],
                       "stattrak": bool(r["stattrak"])} for r in rows]
            drop, success = game.contract(rng, inputs, self.catalog.items, self.catalog.cases)
            for r in rows:
                self._set_state(r, "gone", "contract")
            new_id = self._insert_item(user_id, drop, "contract")
            self._log_roll(user, rng, {"inputs": item_ids, "item": new_id, "success": success})
            response = {
                "success": success,
                "chance": rules.CONTRACT_SUCCESS[tier],
                "item": self.item_view(self.conn.execute("SELECT * FROM items WHERE id=?", (new_id,)).fetchone()),
            }
            self._remember(user_id, idem_key, response)
            return response

    # --- колесо и задания -------------------------------------------------

    def wheel(self, user_id: int) -> dict:
        with transaction(self.conn):
            user = self._user(user_id)
            if self._daily(user_id)["wheel"]:
                raise GameError("Колесо уже крутили сегодня. Приходи завтра.", "used")
            rng = self._rng(user, "wheel")
            sector = game.spin_wheel(rng)
            self.conn.execute("UPDATE daily SET wheel=1 WHERE user_id=? AND day=?", (user_id, today()))
            reward = {"sector": sector["id"], "label": sector["label"]}
            if "spins" in sector:
                self.conn.execute("UPDATE users SET bonus_spins=bonus_spins+? WHERE id=?", (sector["spins"], user_id))
                reward["spins"] = sector["spins"]
            else:
                amount = round(self.catalog.unit_cents * sector["cases"])
                self._ledger(user_id, amount, "wheel")
                reward["coins"] = amount
            self._log_roll(user, rng, reward)
            return reward

    def claim_task(self, user_id: int, task_id: str) -> dict:
        task = next((t for t in rules.DAILY_TASKS if t["id"] == task_id), None)
        if not task:
            raise GameError("Задание не найдено.", "bad_task")
        with transaction(self.conn):
            daily = self._daily(user_id)
            claimed = json.loads(daily["claimed"])
            progress = {"spin3": daily["spins"], "sell1": daily["sells"], "upgrade1": daily["upgrades"]}[task_id]
            if task_id in claimed:
                raise GameError("Награда уже получена.", "claimed")
            if progress < task["goal"]:
                raise GameError("Задание ещё не выполнено.", "not_done")
            claimed.append(task_id)
            self.conn.execute("UPDATE daily SET claimed=? WHERE user_id=? AND day=?",
                              (json.dumps(claimed), user_id, today()))
            amount = round(self.catalog.unit_cents * task["reward_cases"])
            self._ledger(user_id, amount, "task", task_id)
            return {"task": task_id, "coins": amount}

    # --- честная игра -------------------------------------------------------

    def rotate_seed(self, user_id: int, client_seed: str | None = None) -> dict:
        client_seed = (client_seed or "").strip()[:64] or secrets.token_hex(8)
        with transaction(self.conn):
            user = self._user(user_id)
            self.conn.execute(
                "INSERT INTO seeds_revealed (user_id, server_seed, seed_hash, client_seed, last_nonce, revealed_at)"
                " VALUES (?,?,?,?,?,?)",
                (user_id, user["server_seed"], game.seed_hash(user["server_seed"]), user["client_seed"],
                 user["nonce"], self.clock()),
            )
            new_seed = secrets.token_hex(32)
            self.conn.execute("UPDATE users SET server_seed=?, client_seed=?, nonce=0 WHERE id=?",
                              (new_seed, client_seed, user_id))
        return {
            "revealed": {"server_seed": user["server_seed"], "client_seed": user["client_seed"],
                         "seed_hash": game.seed_hash(user["server_seed"]), "last_nonce": user["nonce"]},
            "next": {"server_seed_hash": game.seed_hash(new_seed), "client_seed": client_seed, "nonce": 0},
        }
