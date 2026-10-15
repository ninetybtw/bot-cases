"""Апгрейды и контракты (как trade-up в CS2, но с любой редкостью)."""

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
                raise GameError("CONFIRM_REQUIRED", "Точно? Ставка сгорит при неудаче.")
            p = self._player(user_id)
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

    # --- контракт ---------------------------------------------------------------

    def _contract_rows(self, user_id: int, item_ids: list[int]):
        c = rules.CFG["contracts"]
        if not c["min_items"] <= len(item_ids) <= c["max_items"]:
            raise GameError("BAD_REQUEST", f"В контракт нужно от {c['min_items']} до {c['max_items']} предметов любой редкости.")
        rows = self._owned(user_id, item_ids)
        total = sum(self.item_view(r)["price_cents"] for r in rows)
        if total > self.catalog.contract_max_total():
            raise GameError("BAD_REQUEST", f"Контракт дороже {self.money(user_id, self.catalog.contract_max_total())} не собрать: "
                            "в каталоге нет таких дорогих наград. Убери самый дорогой предмет.")
        return rows, total

    def contract_quote(self, user_id: int, item_ids: list[int]) -> dict:
        rows, total = self._contract_rows(user_id, item_ids)
        floats = [r["float_value"] for r in rows if r["float_value"] is not None]
        return {"total_cents": total, **game.contract_range(total),
                "avg_float": round(sum(floats) / len(floats), 6) if floats else None,
                "stattrak": all(r["stattrak"] for r in rows),
                "confirm_required": any(rules.at_least(r["tier"], rules.CFG["upgrader"]["confirm_from"]) for r in rows)}

    def contract(self, user_id: int, item_ids: list[int], confirmed: bool = False, idem_key: str | None = None) -> dict:
        def run():
            rows, total = self._contract_rows(user_id, item_ids)
            if any(rules.at_least(r["tier"], rules.CFG["upgrader"]["confirm_from"]) for r in rows) and not confirmed:
                raise GameError("CONFIRM_REQUIRED", f"Предметы на {self.money(user_id, total)} исчезнут, взамен придёт один.")
            rng, seed = self._rng(user_id)
            inputs = [{"price_cents": self.item_view(r)["price_cents"], "float_value": r["float_value"],
                       "stattrak": bool(r["stattrak"])} for r in rows]
            drop, mult = game.contract(rng, inputs, self.catalog.items)
            for r in rows:
                self._update_item(r, "contract_in", user_id, state="gone", owner=None)
            new_id = self._insert_item(user_id, drop, "contract")
            self._log_roll(user_id, "contract", rng, seed, {"inputs": item_ids, "item": new_id, "mult": mult,
                                                             "total": total, "price": drop.price_cents})
            self._quest_progress(user_id, "contract1", 1, weekly=True)
            return {"mult": mult, "total_cents": total, "item": self.item_view(self._item(new_id)),
                    "fair": self._fair(seed, rng.nonce), "balance_cents": self._player(user_id)["balance"]}

        return self.idempotent(user_id, idem_key, "contract", run)
