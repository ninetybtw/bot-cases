"""Профиль: вход, страна и валюта, энергия, уровень, VIP, переполнение, честная игра."""

from __future__ import annotations

import json

from .. import game, rules
from .core import GameError


class Players:
    def ensure_user(self, user_id: int, name: str = "", username: str | None = None, lang: str | None = None,
                    referrer: int | None = None) -> None:
        with self.tx():
            row = self.conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            now = self.clock()
            if row:
                self.conn.execute("UPDATE users SET name=COALESCE(NULLIF(?, ''), name), username=?, last_seen=? WHERE id=?",
                                  (name, username, now, user_id))
                return
            if referrer == user_id or (referrer and not self.conn.execute("SELECT 1 FROM users WHERE id=?", (referrer,)).fetchone()):
                referrer = None
            if referrer:
                today_refs = self.conn.execute(
                    "SELECT COUNT(*) FROM referral_rewards WHERE referrer=? AND created_at>?",
                    (referrer, now - 86400)).fetchone()[0]
                if today_refs >= rules.CFG["referral"]["per_day_max"]:
                    referrer = None
            self.conn.execute(
                "INSERT INTO users (id, name, username, lang, country, referrer, created_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                (user_id, name, username, lang or "ru", rules.country_for_language(lang), referrer, now, now),
            )
            energy = rules.CFG["energy"]["start"]
            self.conn.execute(
                "INSERT INTO players (user_id, energy, energy_ts, slots, bonus_spins) VALUES (?,?,?,?,?)",
                (user_id, energy, now, rules.CFG["inventory"]["slots"],
                 rules.CFG["referral"]["invitee_spins"] if referrer else 0),
            )
            start = self.catalog.unit_cents * rules.CFG["start_balance_cases"]
            self._ledger(user_id, start, "start")
            if referrer:
                self.conn.execute("INSERT INTO referral_rewards (referrer, invitee, created_at) VALUES (?,?,?)",
                                  (referrer, user_id, now))
            self._seed(user_id)

    # --- энергия, уровень, VIP ------------------------------------------------

    def _energy(self, p) -> tuple[int, float]:
        cfg = rules.CFG["energy"]
        period = cfg["regen_minutes"] * 60
        energy, ts, now = p["energy"], p["energy_ts"], self.clock()
        if energy >= cfg["max"]:
            return energy, now
        gained = int((now - ts) // period)
        if gained:
            energy = min(cfg["max"], energy + gained)
            ts = now if energy >= cfg["max"] else ts + gained * period
        return energy, ts

    def _sync_player(self, user_id: int) -> None:
        """Пересчитать энергию, выдать дневные попытки VIP, перенести предметы из переполнения на свободные места."""
        p = self.conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
        energy, ts = self._energy(p)
        if (energy, ts) != (p["energy"], p["energy_ts"]):
            self.conn.execute("UPDATE players SET energy=?, energy_ts=? WHERE user_id=?", (energy, ts, user_id))
        if self.is_vip(p) and p["vip_day"] != self.today():
            stock = min(rules.CFG["stars"]["attempts_stock_max"], p["paid_attempts"] + rules.CFG["stars"]["vip_daily_attempts"])
            self.conn.execute("UPDATE players SET vip_day=?, paid_attempts=? WHERE user_id=?", (self.today(), stock, user_id))
        free = self._free_slots(user_id)
        if free > 0:
            rows = self.conn.execute(
                "SELECT * FROM items WHERE owner=? AND state!='gone' AND overflow_until IS NOT NULL ORDER BY id LIMIT ?",
                (user_id, free)).fetchall()
            for r in rows:
                self._update_item(r, "overflow_out", user_id, overflow_until=None)

    def _slots(self, p) -> int:
        bonus = rules.CFG["inventory"]["slots_vip_bonus"] if self.is_vip(p) else 0
        return min(rules.CFG["inventory"]["slots_max"], p["slots"] + bonus)

    def _used_slots(self, user_id: int) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT SUM(overflow_until IS NULL) AS main, SUM(overflow_until IS NOT NULL) AS over FROM items"
            " WHERE owner=? AND state!='gone'", (user_id,)).fetchone()
        return row["main"] or 0, row["over"] or 0

    def _free_slots(self, user_id: int) -> int:
        p = self.conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
        return self._slots(p) - self._used_slots(user_id)[0]

    def _add_xp(self, user_id: int, xp: int) -> None:
        p = self.conn.execute("SELECT xp, level FROM players WHERE user_id=?", (user_id,)).fetchone()
        total, level = p["xp"] + xp, p["level"]
        per = rules.CFG["xp"]["per_level"]
        while total >= per * level * (level + 1) // 2:
            level += 1
        self.conn.execute("UPDATE players SET xp=?, level=? WHERE user_id=?", (total, level, user_id))

    def coin_spins_limit(self, level: int) -> int:
        s = rules.CFG["spin"]
        return min(s["coin_per_day_max"], s["coin_per_day_base"] + s["coin_per_level"] * (level - 1))

    # --- профиль ---------------------------------------------------------------

    def me(self, user_id: int) -> dict:
        with self.tx():
            self._sync_player(user_id)
        u, p = self._user(user_id), self._player(user_id)
        seed = self._seed(user_id)
        energy, ts = self._energy(p)
        cfg_e = rules.CFG["energy"]
        main, over = self._used_slots(user_id)
        per = rules.CFG["xp"]["per_level"]
        change_after = (u["country_set_at"] or 0) + rules.CFG["currency"]["change_days"] * 86400
        week_days = self._quest_days_this_week(user_id)
        return {
            "user": {"id": u["id"], "name": u["name"], "level": p["level"], "xp": p["xp"],
                     "xp_next": per * p["level"] * (p["level"] + 1) // 2, "xp_prev": per * (p["level"] - 1) * p["level"] // 2,
                     "vip_until": p["vip_until"] if self.is_vip(p) else None, "frame": p["frame"],
                     "created_at": u["created_at"]},
            "country": u["country"],
            "country_confirmed": bool(u["country_confirmed"]),
            "country_change_after": change_after if u["country_confirmed"] and change_after > self.clock() else None,
            "currency": rules.currency_block(u["country"]),
            "balance_cents": p["balance"],
            "star_credit": p["star_credit"],
            "energy": {"now": energy, "max": cfg_e["max"],
                       "next_at": None if energy >= cfg_e["max"] else ts + cfg_e["regen_minutes"] * 60},
            "attempts": p["paid_attempts"],
            "bonus_spins": p["bonus_spins"],
            "coin_spins_left": max(0, self.coin_spins_limit(p["level"]) - self._counter(user_id, "coin")),
            "luck": {"mult": p["luck_mult"], "spins_left": p["luck_left"],
                     "max_case_cents": rules.CFG["luck"]["max_case_cents"],
                     "max_return": game.luck_max_return(p["luck_mult"]),
                     "cases": {c["id"]: round(game.lucky_weights(c, self.catalog.items, p["luck_mult"])[1], 1)
                               for c in self.catalog.case_list if self.luck_applies(c)}} if p["luck_left"] else None,
            "pity": {cid: v for cid in self.catalog.cases if (v := self.case_pity(user_id, cid))},
            "slots": {"used": main, "total": self._slots(p), "overflow": over,
                      "overflow_max": rules.CFG["inventory"]["overflow_slots"]},
            "upgrades_left": rules.CFG["upgrader"]["per_day"] - self._counter(user_id, "upg"),
            "upgrade_bonus_pct": p["upgrade_bonus"],
            "duels_left": rules.CFG["duels"]["casual"]["per_day"] - self._counter(user_id, "duel"),
            "total_spins": p["total_spins"],
            "break_reminder": self._spins_today(user_id) >= rules.CFG["spin"]["break_reminder_spins"],
            "today": {
                "wheel_daily": not self._wheel_used(user_id, "daily", self.today()),
                "wheel_weekly_unlocked": week_days >= rules.CFG["wheel"]["weekly_unlock_days"]
                and not self._wheel_used(user_id, "weekly", self.week()),
                "quest_days_week": week_days,
                "quests_done": self.conn.execute(
                    "SELECT COUNT(*) FROM quests WHERE user_id=? AND period=? AND claimed_at IS NOT NULL",
                    (user_id, self.today())).fetchone()[0],
                "raffle_tickets": self._tickets(user_id),
            },
            "fair": {"server_seed_hash": seed["seed_hash"], "client_seed": seed["client_seed"], "nonce": seed["nonce"]},
            "showcase": json.loads(p["showcase"]),
            "last_drop": json.loads(p["last_drop"]) if p["last_drop"] else None,
            "shop_enabled": rules.CFG["stars"]["shop_enabled"],
        }

    def _spins_today(self, user_id: int) -> int:
        row = self.conn.execute("SELECT progress FROM quests WHERE user_id=? AND period=? AND code='_spins'",
                                (user_id, self.today())).fetchone()
        return row["progress"] if row else 0

    def set_country(self, user_id: int, country: str) -> dict:
        if country not in rules.CFG["currency"]["countries"]:
            raise GameError("BAD_REQUEST", "Такой страны нет в списке.")
        with self.tx():
            u = self._user(user_id)
            now = self.clock()
            if u["country_confirmed"] and u["country"] != country:
                after = (u["country_set_at"] or 0) + rules.CFG["currency"]["change_days"] * 86400
                if after > now:
                    days = int((after - now) // 86400) + 1
                    raise GameError("NOT_ALLOWED_YET", f"Страну можно менять раз в 30 дней. Осталось {days} дн.")
            if not u["country_confirmed"] or u["country"] != country:
                self.conn.execute("UPDATE users SET country=?, country_confirmed=1, country_set_at=? WHERE id=?",
                                  (country, now, user_id))
        return {"country": country, "currency": rules.currency_block(country)}

    def ack_last_drop(self, user_id: int) -> dict:
        with self.tx():
            self.conn.execute("UPDATE players SET last_drop=NULL WHERE user_id=?", (user_id,))
        return {"ok": True}

    # --- честная игра ------------------------------------------------------------

    def fair(self, user_id: int) -> dict:
        seed = self._seed(user_id)
        rows = self.conn.execute(
            "SELECT r.*, s.server_seed, s.seed_hash, s.client_seed, s.active FROM rolls r JOIN seeds s ON s.id=r.seed_id"
            " WHERE r.user_id=? ORDER BY r.id DESC LIMIT ?", (user_id, rules.CFG["fairness"]["history"])).fetchall()
        return {
            "server_seed_hash": seed["seed_hash"], "client_seed": seed["client_seed"], "nonce": seed["nonce"],
            "formula": rules.CFG["fairness"]["roll"],
            "rolls": [{
                "kind": r["kind"], "ts": r["ts"], "nonce": r["nonce"], "numbers": r["numbers"],
                "seed_hash": r["seed_hash"], "client_seed": r["client_seed"],
                "server_seed": None if r["active"] else r["server_seed"],
                "result": json.loads(r["result"]),
            } for r in rows],
        }

    def rotate_seed(self, user_id: int, client_seed: str | None) -> dict:
        import secrets

        client_seed = (client_seed or "").strip()[:64] or secrets.token_hex(8)
        with self.tx():
            old = self._seed(user_id)
            self.conn.execute("UPDATE seeds SET active=0, revealed_at=? WHERE id=?", (self.clock(), old["id"]))
            server_seed = game.new_server_seed()
            self.conn.execute(
                "INSERT INTO seeds (user_id, server_seed, seed_hash, client_seed, created_at) VALUES (?,?,?,?,?)",
                (user_id, server_seed, game.seed_hash(server_seed), client_seed, self.clock()))
        return {
            "revealed": {"server_seed": old["server_seed"], "seed_hash": old["seed_hash"],
                         "client_seed": old["client_seed"], "last_nonce": old["nonce"]},
            "next": {"server_seed_hash": game.seed_hash(server_seed), "client_seed": client_seed, "nonce": 0},
        }
