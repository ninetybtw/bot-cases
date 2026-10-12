"""Апгрейдер и контракты «5 в 1»."""

from __future__ import annotations

import json

from .. import game, rules
from .core import GameError


class Crafting:
    # --- апгрейдер ------------------------------------------------------------

    def upgrade_targets(self, stake_cents: int, mult: float | None = None, limit: int = 30) -> list[dict]:
        lo = stake_cents * (mult or 1.2)
        hi = lo * (1.25 if mult else 50)
        found = self.catalog.targets_between(max(lo, stake_cents + 1), hi)
        if len(found) > limit:
            step = len(found) / limit
            found = [found[int(i * step)] for i in range(limit)]
        out = []
        for price, item_id, key in found:
            it = self.catalog.items[item_id]
            wear, st = rules.split_key(key)
            out.append({"catalog_item_id": item_id, "wear": wear, "stattrak": st, "name": it["name"],
                        "image_url": it["image"], "tier": it["tier"], "price_cents": price})
        return out

    def _upgrade_quote(self, user_id: int, stake_item_ids: list[int], stake_coins: int, target: dict):
        u = rules.CFG["upgrader"]
        if len(stake_item_ids) > u["max_items"]:
            raise GameError("BAD_REQUEST", f"В апгрейдер можно положить до {u['max_items']} предметов.")
        if stake_coins < 0:
            raise GameError("BAD_REQUEST", "Неверная сумма.")
        it = self.catalog.items.get(target.get("catalog_item_id"))
        key = rules.price_key(target.get("wear"), bool(target.get("stattrak")))
        if not it or key not in it["prices"]:
            raise GameError("NOT_FOUND", "Цель не найдена.")
        rows = self._owned(user_id, stake_item_ids) if stake_item_ids else []
        if not rows and not stake_coins:
            raise GameError("BAD_REQUEST", "Положи предмет или монеты.")
        p = self._player(user_id)
        stake = sum(self.item_view(r)["price_cents"] for r in rows) + stake_coins
        target_price = it["prices"][key]
        chance = game.upgrade_chance(stake, target_price, p["upgrade_bonus"])
        if chance <= 0:
            raise GameError("BAD_REQUEST", "Цель должна стоить дороже ставки.")
        formula = f"{stake / 100:.2f} / {target_price / 100:.2f} × {u['k'] * 100:.0f}%"
        if p["upgrade_bonus"]:
            formula += f" × {1 + p['upgrade_bonus'] / 100:g} (бонус после неудачи)"
        confirm = any(rules.at_least(r["tier"], u["confirm_from"]) for r in rows)
        return rows, stake, it, key, target_price, chance, formula, confirm

    def upgrade_quote(self, user_id: int, stake_item_ids: list[int], stake_coins: int, target: dict) -> dict:
        _, stake, _, _, tp, chance, formula, confirm = self._upgrade_quote(user_id, stake_item_ids, stake_coins, target)
        return {"chance_pct": round(chance * 100, 2), "formula": formula, "confirm_required": confirm,
                "stake_cents": stake, "target_cents": tp, "max_chance_pct": rules.CFG["upgrader"]["max_chance_pct"]}

    def upgrade(self, user_id: int, stake_item_ids: list[int], stake_coins: int, target: dict,
                quote_chance_pct: float | None = None, confirmed: bool = False, idem_key: str | None = None) -> dict:
        def run():
            u = rules.CFG["upgrader"]
            if self._counter(user_id, "upg") >= u["per_day"]:
                raise GameError("DAILY_LIMIT", "На сегодня апгрейды закончились. Возвращайся завтра.")
            rows, stake, it, key, tp, chance, formula, confirm = self._upgrade_quote(user_id, stake_item_ids, stake_coins, target)
            if quote_chance_pct is not None and abs(quote_chance_pct - chance * 100) > 0.01:
                raise GameError("CONFIG_CHANGED", "Цены обновились, шанс изменился. Посмотри новый шанс.",
                                chance_pct=round(chance * 100, 2))
            if confirm and not confirmed:
                raise GameError("CONFIRM_REQUIRED", "Точно? Ставка сгорит при неудаче.", pause_s=u["confirm_pause_s"])
            p = self._player(user_id)
            collection = sum(self.item_view(r)["price_cents"] for r in self.conn.execute(
                "SELECT * FROM items WHERE owner=? AND state!='gone'", (user_id,)))
            if stake > (collection + p["balance"]) * u["max_share_of_collection"]:
                raise GameError("BAD_REQUEST", "За раз можно поставить не больше половины коллекции и баланса.")
            if not rows and self._free_slots(user_id) < 1:
                raise GameError("INVENTORY_FULL", "Освободи место в коллекции.")
            if stake_coins:
                self._charge(user_id, stake_coins, "upgrade_stake")
            for r in rows:
                self._update_item(r, "upgrade_in", user_id, {"target": it["id"]}, state="gone", owner=None)
            rng, seed = self._rng(user_id)
            roll = rng.random()
            success = roll < chance
            wear, st = rules.split_key(key)
            res = {"success": success, "roll": round(roll * 100, 4), "chance_pct": round(chance * 100, 2),
                   "formula": formula, "fair": self._fair(seed, rng.nonce)}
            if success:
                drop = game.make_drop(rng, it, stattrak=st, wear=wear)
                new_id = self._insert_item(user_id, drop, "upgrade")
                res["item"] = self.item_view(self._item(new_id))
                self.conn.execute("UPDATE players SET upgrade_bonus=0 WHERE user_id=?", (user_id,))
                res["next_bonus_pct"] = 0
            else:
                parts = stake * u["fail_parts_pct"] // 100
                self._ledger(user_id, parts, "upgrade_parts")
                bonus = min(u["max_bonus_pct"], p["upgrade_bonus"] + u["fail_bonus_pct"])
                self.conn.execute("UPDATE players SET upgrade_bonus=? WHERE user_id=?", (bonus, user_id))
                res.update({"parts_cents": parts, "next_bonus_pct": bonus})
            self.conn.execute(
                "INSERT INTO upgrades (user_id, stake, stake_value, target, target_value, chance, roll, success, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, json.dumps({"items": stake_item_ids, "coins": stake_coins}), stake,
                 json.dumps({"id": it["id"], "key": key}), tp, chance, roll, int(success), self.clock()))
            self._bump_counter(user_id, "upg")
            self._log_roll(user_id, "upgrade", rng, seed, {"chance": chance, "roll": roll, "success": success})
            res["balance_cents"] = self._player(user_id)["balance"]
            return res

        return self.idempotent(user_id, idem_key, "upgrade", run)

    # --- контракт «5 в 1» ------------------------------------------------------

    def contract(self, user_id: int, item_ids: list[int], confirmed: bool = False, idem_key: str | None = None) -> dict:
        def run():
            c = rules.CFG["contracts"]
            if len(item_ids) != c["inputs"]:
                raise GameError("BAD_REQUEST", f"Нужно ровно {c['inputs']} предметов.")
            rows = self._owned(user_id, item_ids)
            tiers = {r["tier"] for r in rows}
            if len(tiers) != 1:
                raise GameError("BAD_REQUEST", "Все 5 предметов должны быть одной ступени.")
            tier = tiers.pop()
            if tier not in c["next"]:
                raise GameError("BAD_REQUEST", "Эту ступень контрактом не поднять.")
            if rules.at_least(tier, "epic") and not confirmed:
                raise GameError("CONFIRM_REQUIRED", "Пять предметов исчезнут, взамен придёт один.", pause_s=3)
            rng, seed = self._rng(user_id)
            inputs = [{"catalog_id": r["catalog_id"], "tier": r["tier"], "float_value": r["float_value"],
                       "stattrak": bool(r["stattrak"])} for r in rows]
            drop, success = game.contract(rng, inputs, self.catalog.items, self.catalog.cases)
            for r in rows:
                self._update_item(r, "contract_in", user_id, state="gone", owner=None)
            new_id = self._insert_item(user_id, drop, "contract")
            self._log_roll(user_id, "contract", rng, seed, {"inputs": item_ids, "item": new_id, "success": success})
            self._quest_progress(user_id, "contract1", 1, weekly=True)
            guaranteed = rules.rank(tier) <= rules.rank(c["guaranteed_up_to"])
            return {"success": success, "chance_pct": 100 if guaranteed else c["success_pct"],
                    "item": self.item_view(self._item(new_id)), "fair": self._fair(seed, rng.nonce)}

        return self.idempotent(user_id, idem_key, "contract", run)
