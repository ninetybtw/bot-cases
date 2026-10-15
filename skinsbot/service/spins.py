"""Спин по схеме «Спин: порядок действий» из docs/ARCHITECTURE.md."""

from __future__ import annotations

import json
import random

from .. import game, rules
from .core import GameError

REEL_LEN, REEL_STOP = 60, 52


class Spins:
    def spin(self, user_id: int, case_id: str | None = None, source: str = "coin", count: int = 1,
             confirmed: bool = False, idem_key: str | None = None) -> dict:
        return self.idempotent(user_id, idem_key, "spin",
                               lambda: self._spin(user_id, case_id, source, count, confirmed, idem_key))

    def _pity(self, user_id: int, case_id: str) -> tuple[int, int]:
        row = self.conn.execute("SELECT since_epic, since_legendary FROM pity_counters WHERE user_id=? AND case_id=?",
                                (user_id, case_id)).fetchone()
        return (row["since_epic"], row["since_legendary"]) if row else (0, 0)

    def luck_applies(self, case: dict) -> bool:
        return case["price_cents"] <= rules.CFG["luck"]["max_case_cents"]

    def _spin(self, user_id, case_id, source, count, confirmed, idem_key) -> dict:
        cfg = rules.CFG["spin"]
        if count not in cfg["counts"]:
            raise GameError("BAD_REQUEST", "Можно открыть ×1, ×3, ×5 или ×10.")
        if source not in ("free", "coin", "attempt"):
            raise GameError("BAD_REQUEST", "Неизвестный источник спина.")
        case_id = case_id or (self.catalog.free_case_id if source != "coin" else None)
        if case_id not in self.catalog.cases:
            raise GameError("BAD_REQUEST", "Такого кейса нет.")
        case = self.catalog.cases[case_id]
        if source in ("free", "attempt") and case_id != self.catalog.free_case_id:
            raise GameError("BAD_REQUEST", f"Бесплатные спины и попытки открывают кейс «{self.catalog.cases[self.catalog.free_case_id]['name']}».")
        self._sync_player(user_id)
        p = self._player(user_id)
        price = case["price_cents"]

        # Источник: энергия, попытки или монеты с дневным лимитом.
        cost = 0
        energy, energy_ts = self._energy(p)
        if source == "free":
            if energy + p["bonus_spins"] < count:
                raise GameError("NO_ENERGY", "Бесплатные спины закончились. Новый будет через 3 часа.")
            use = min(energy, count)
            if energy >= rules.CFG["energy"]["max"] and use:
                energy_ts = self.clock()
            self.conn.execute("UPDATE players SET energy=?, energy_ts=?, energy_notified=0, bonus_spins=bonus_spins-? WHERE user_id=?",
                              (energy - use, energy_ts, count - use, user_id))
        elif source == "attempt":
            if p["paid_attempts"] < count:
                raise GameError("NO_ENERGY", "Попыток не хватает.")
            self.conn.execute("UPDATE players SET paid_attempts=paid_attempts-? WHERE user_id=?", (count, user_id))
        else:
            left = self.coin_spins_limit(p["level"]) - self._counter(user_id, "coin")
            if count > left:
                raise GameError("DAILY_LIMIT", f"За монеты сегодня можно открыть ещё {max(0, left)}. "
                                "Лимит растёт с уровнем, а бесплатные спины не ограничены.")
            cost = price * count
            if not confirmed and (cost >= cfg["confirm_from_cents"] or cost > p["balance"] * cfg["confirm_balance_share"]):
                if p["balance"] >= cost:
                    raise GameError("CONFIRM_REQUIRED", f"Открыть за {self.money(user_id, cost)}?", cost_cents=cost)
            self._charge(user_id, cost, "spin", "case", case_id)
            self._bump_counter(user_id, "coin", count)

        # Место: коллекция, затем переполнение.
        free_slots = self._free_slots(user_id)
        overflow_used = self._used_slots(user_id)[1]
        to_overflow = max(0, count - max(0, free_slots))
        if overflow_used + to_overflow > rules.CFG["inventory"]["overflow_slots"]:
            raise GameError("INVENTORY_FULL", "Освободи место: коллекция и переполнение заполнены. Продай лишнее в Инвентаре.")

        # Бросок: удача (на кейсах до luck.max_case_cents) и гарантии этого кейса.
        rng, seed = self._rng(user_id)
        since_epic, since_leg = self._pity(user_id, case_id)
        lucky = self.luck_applies(case)
        luck_left, luck = p["luck_left"], p["luck_mult"]
        luck_used = None
        counters_before = {"epic": since_epic, "legend": since_leg, "luck_left": luck_left}
        cosmetic = random.Random(f"{seed['seed_hash']}:{rng.nonce}")
        results, pity_applied = [], None
        for k in range(count):
            weights, tier_odds = case["weights"], case["tier_odds"]
            if lucky and luck_left > 0:
                luck_left -= 1
                weights, luck_used = game.lucky_weights(case, self.catalog.items, luck)
                tier_odds = game.tier_odds_of(case, weights)
            odds, applied = game.spin_odds(tier_odds, since_epic, since_leg)
            pity_applied = pity_applied or applied
            drop = game.open_case(rng, case, self.catalog.items, odds, weights)
            since_epic = 0 if rules.at_least(drop.tier, "epic") else since_epic + 1
            since_leg = 0 if rules.at_least(drop.tier, "legendary") else since_leg + 1
            overflow = k >= count - to_overflow
            item_id = self._insert_item(user_id, drop, f"case:{case_id}", overflow=overflow)
            strip = game.reel(cosmetic, case, REEL_LEN, REEL_STOP, drop.catalog_id)
            results.append((item_id, drop, strip, applied))

        self.conn.execute(
            "INSERT INTO pity_counters (user_id, case_id, since_epic, since_legendary) VALUES (?,?,?,?)"
            " ON CONFLICT(user_id, case_id) DO UPDATE SET since_epic=excluded.since_epic, since_legendary=excluded.since_legendary",
            (user_id, case_id, since_epic, since_leg))
        self.conn.execute(
            "UPDATE players SET luck_left=?, luck_mult=CASE WHEN ?>0 THEN luck_mult END, total_spins=total_spins+? WHERE user_id=?",
            (luck_left, luck_left, count, user_id))
        self._add_xp(user_id, rules.CFG["xp"]["per_spin"] * count)
        self._quest_progress(user_id, "spin3", count)
        self._quest_progress(user_id, "_spins", count)
        self._referral_check(user_id)

        item_ids = [r[0] for r in results]
        cur = self.conn.execute(
            "INSERT INTO drops (user_id, source, case_id, level, count, cost, config_version, luck_mult, pity_applied,"
            " seed_id, seed_hash, client_seed, nonce, counters, item_ids, idem_key, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, source, case_id, None, count, cost, rules.VERSION, luck_used, pity_applied,
             seed["id"], seed["seed_hash"], seed["client_seed"], rng.nonce,
             json.dumps({"before": counters_before, "after": {"epic": since_epic, "legend": since_leg, "luck_left": luck_left}}),
             json.dumps(item_ids), idem_key, self.clock()))
        drop_id = cur.lastrowid
        for item_id in item_ids:
            self.conn.execute("UPDATE items SET origin_ref=? WHERE id=?", (drop_id, item_id))
        self._log_roll(user_id, "spin", rng, seed, {"drop_id": drop_id, "case_id": case_id,
                                                     "tiers": [r[1].tier for r in results]})

        drops = []
        for item_id, drop, strip, applied in results:
            view = self.item_view(self._item(item_id))
            drops.append({
                "drop_id": drop_id,
                "items": [view],
                "reel": [self._reel_cell(c) for c in strip],
                "stop_index": REEL_STOP,
                "pity_applied": applied,
            })
            if rules.at_least(drop.tier, "legendary"):
                self._friend_rare_drop(user_id, view)
        p2 = self._player(user_id)
        energy, ts = self._energy(p2)
        response = {
            "drops": drops,
            "fair": self._fair(seed, rng.nonce),
            "balance_cents": p2["balance"],
            "energy": {"now": energy, "max": rules.CFG["energy"]["max"],
                       "next_at": None if energy >= rules.CFG["energy"]["max"] else ts + rules.CFG["energy"]["regen_minutes"] * 60},
            "overflow": to_overflow,
            "luck_used": round(luck_used, 2) if luck_used else None,
            "pity": self.case_pity(user_id, case_id),
        }
        self.conn.execute("UPDATE players SET last_drop=? WHERE user_id=?",
                          (json.dumps({"drop_id": drop_id, "item_ids": item_ids}), user_id))
        return response

    def case_pity(self, user_id: int, case_id: str) -> dict | None:
        """Гарантии кейса для экрана: через сколько спинов точно Запрещённое+ и Засекреченное+."""
        case = self.catalog.cases[case_id]
        odds = case["tier_odds"]
        if game.group_chance(odds, "epic") <= 0 or game.group_chance(odds, "legendary") <= 0:
            return None
        since_epic, since_leg = self._pity(user_id, case_id)
        now, _ = game.spin_odds(odds, since_epic, since_leg)
        return {"epic_in": rules.CFG["pity"]["epic_plus"]["hard"] - since_epic,
                "legend_in": rules.CFG["pity"]["legend_plus"]["hard"] - since_leg,
                "epic_chance_pct": round(game.group_chance(now, "epic") * 100, 2),
                "legend_chance_pct": round(game.group_chance(now, "legendary") * 100, 2)}

    def _reel_cell(self, catalog_id: str) -> dict:
        it = self.catalog.items[catalog_id]
        return {"tier": it["tier"], "image_url": it["image"], "name": it["name"]}

    def _friend_rare_drop(self, user_id: int, view: dict) -> None:
        name = self.conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()["name"] or "Друг"
        friends = self._friend_ids(user_id)
        tier = rules.CFG["tiers"][view["tier"]]["name"]
        for f in friends:
            self._notify(f, "friend_drop", f"У {name} выпал {tier}: {view['name']}. Посмотри в ленте дропов.")
