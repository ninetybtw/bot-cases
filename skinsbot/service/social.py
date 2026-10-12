"""Удержание и социальное: задания, колёса, розыгрыш, рефералы, друзья, рейтинги, дуэли, косметика, лента."""

from __future__ import annotations

import datetime as dt
import json
import secrets

from .. import game, rules
from .core import GameError


class Social:
    # --- задания ------------------------------------------------------------------------

    def _quest_cfg(self, code: str):
        for weekly, key in ((False, "daily"), (True, "weekly")):
            for q in rules.CFG["quests"][key]:
                if q["code"] == code:
                    return q, weekly
        return None, False

    def _quest_progress(self, user_id: int, code: str, n: int = 1, weekly: bool = False) -> None:
        q, is_weekly = self._quest_cfg(code)
        period = self.week() if (weekly or is_weekly) else self.today()
        target = q["target"] if q else 0
        self.conn.execute(
            "INSERT INTO quests (user_id, period, code, target, progress) VALUES (?,?,?,?,?)"
            " ON CONFLICT(user_id, period, code) DO UPDATE SET progress=progress+excluded.progress",
            (user_id, period, code, target, n))

    def quests(self, user_id: int) -> dict:
        def rows(key, period):
            out = []
            for q in rules.CFG["quests"][key]:
                r = self.conn.execute("SELECT * FROM quests WHERE user_id=? AND period=? AND code=?",
                                      (user_id, period, q["code"])).fetchone()
                progress = min(r["progress"], q["target"]) if r else 0
                out.append({"code": q["code"], "title": q["title"], "target": q["target"], "progress": progress,
                            "reward_cents": round(self.catalog.unit_cents * q["reward_cases"]),
                            "tickets": q.get("tickets", 0), "cosmetic": q.get("cosmetic"),
                            "claimed": bool(r and r["claimed_at"])})
            return out

        return {"daily": rows("daily", self.today()), "weekly": rows("weekly", self.week()),
                "week_days": self._quest_days_this_week(user_id),
                "weekly_wheel_unlock_days": rules.CFG["wheel"]["weekly_unlock_days"]}

    def claim_quest(self, user_id: int, code: str, idem_key: str | None = None) -> dict:
        def run():
            q, weekly = self._quest_cfg(code)
            if not q:
                raise GameError("NOT_FOUND", "Задание не найдено.")
            period = self.week() if weekly else self.today()
            r = self.conn.execute("SELECT * FROM quests WHERE user_id=? AND period=? AND code=?", (user_id, period, code)).fetchone()
            if not r or r["progress"] < q["target"]:
                raise GameError("BAD_REQUEST", "Задание ещё не выполнено.")
            if r["claimed_at"]:
                raise GameError("BAD_REQUEST", "Награда уже получена.")
            self.conn.execute("UPDATE quests SET claimed_at=? WHERE user_id=? AND period=? AND code=?",
                              (self.clock(), user_id, period, code))
            coins = round(self.catalog.unit_cents * q["reward_cases"])
            self._ledger(user_id, coins, "quest", "quest", code)
            if q.get("tickets"):
                self._add_tickets(user_id, q["tickets"])
            if q.get("cosmetic"):
                self._grant_cosmetic(user_id, q["cosmetic"], "quest")
            return {"code": code, "coins_cents": coins, "tickets": q.get("tickets", 0), "cosmetic": q.get("cosmetic")}

        return self.idempotent(user_id, idem_key, "quest_claim", run)

    def _quest_days_this_week(self, user_id: int) -> int:
        start = self.now_msk().date() - dt.timedelta(days=self.now_msk().weekday())
        days = [(start + dt.timedelta(days=i)).isoformat() for i in range(7)]
        marks = ",".join("?" * 7)
        daily_codes = [q["code"] for q in rules.CFG["quests"]["daily"]]
        return self.conn.execute(
            f"SELECT COUNT(DISTINCT period) FROM quests WHERE user_id=? AND period IN ({marks}) AND claimed_at IS NOT NULL"
            f" AND code IN ({','.join('?' * len(daily_codes))})", (user_id, *days, *daily_codes)).fetchone()[0]

    def visit_rating(self, user_id: int) -> None:
        with self.tx():
            r = self.conn.execute("SELECT progress FROM quests WHERE user_id=? AND period=? AND code='rating1'",
                                  (user_id, self.today())).fetchone()
            if not r:
                self._quest_progress(user_id, "rating1", 1)

    # --- колёса -------------------------------------------------------------------------

    def _wheel_used(self, user_id: int, kind: str, period: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM wheel_spins WHERE user_id=? AND kind=? AND period=?",
                                      (user_id, kind, period)).fetchone())

    def wheel(self, user_id: int, kind: str = "daily", idem_key: str | None = None) -> dict:
        def run():
            period = self.today() if kind == "daily" else self.week()
            if kind not in ("daily", "weekly"):
                raise GameError("BAD_REQUEST", "Такого колеса нет.")
            if self._wheel_used(user_id, kind, period):
                raise GameError("DAILY_LIMIT", "Колесо уже крутили. Приходи завтра." if kind == "daily"
                                else "Недельное колесо уже крутили на этой неделе.")
            if kind == "weekly" and self._quest_days_this_week(user_id) < rules.CFG["wheel"]["weekly_unlock_days"]:
                raise GameError("NOT_ALLOWED_YET", "Недельное колесо откроется, когда выполнишь задания в 5 днях из 7.")
            rng, seed = self._rng(user_id)
            sector = game.spin_wheel(rng, rules.CFG["wheel"][kind])
            self.conn.execute("INSERT INTO wheel_spins (user_id, kind, period, sector, created_at) VALUES (?,?,?,?,?)",
                              (user_id, kind, period, sector["id"], self.clock()))
            reward = {"sector": sector["id"], "label": sector["label"], "fair": self._fair(seed, rng.nonce)}
            reward.update(self._grant_reward(user_id, sector, f"wheel_{kind}", rng))
            self._log_roll(user_id, f"wheel_{kind}", rng, seed, {"sector": sector["id"]})
            return reward

        return self.idempotent(user_id, idem_key, f"wheel_{kind}", run)

    def _grant_reward(self, user_id: int, r: dict, reason: str, rng=None) -> dict:
        out = {}
        if r.get("cases"):
            coins = round(self.catalog.unit_cents * r["cases"])
            self._ledger(user_id, coins, reason)
            out["coins_cents"] = coins
        if r.get("spins"):
            self.conn.execute("UPDATE players SET bonus_spins=bonus_spins+? WHERE user_id=?", (r["spins"], user_id))
            out["spins"] = r["spins"]
        if r.get("cosmetic"):
            self._grant_cosmetic(user_id, r["cosmetic"], reason)
            out["cosmetic"] = r["cosmetic"]
        if r.get("drop_from") or r.get("level"):
            if rng is None:
                rng, _ = self._rng(user_id)
            if r.get("level"):
                lv = self.catalog.levels[r["level"]]
                odds, pools = rules.level_odds(r["level"]), lv["pools"]
            else:
                base = {t: p for t, p in rules.case_odds().items() if rules.at_least(t, r["drop_from"])}
                s = sum(base.values())
                odds = {t: p / s for t, p in base.items()}
                pools = {t: sorted({i for c in self.catalog.case_list for i in c["pools"][t]}) for t in odds}
            drop = game.open_pool(rng, pools, self.catalog.items, odds)
            overflow = self._free_slots(user_id) < 1
            item_id = self._insert_item(user_id, drop, reason.split("_")[0], overflow=overflow)
            out["item"] = self.item_view(self._item(item_id))
        return out

    # --- косметика -----------------------------------------------------------------------

    def _grant_cosmetic(self, user_id: int, code: str, source: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO user_cosmetics (user_id, code, source, created_at) VALUES (?,?,?,?)",
                          (user_id, code, source, self.clock()))

    def cosmetics(self, user_id: int) -> dict:
        p = self._player(user_id)
        own = {r["code"] for r in self.conn.execute("SELECT code FROM user_cosmetics WHERE user_id=?", (user_id,))}
        if self.is_vip(p):
            own.add("badge_vip")
        return {"items": [{"code": c, **v, "owned": c in own, "equipped": p["frame"] == c}
                          for c, v in rules.CFG["cosmetics"].items()]}

    def equip(self, user_id: int, code: str | None) -> dict:
        with self.tx():
            if code and not self.conn.execute("SELECT 1 FROM user_cosmetics WHERE user_id=? AND code=?", (user_id, code)).fetchone():
                raise GameError("NOT_FOUND", "Этой рамки у тебя нет.")
            self.conn.execute("UPDATE players SET frame=? WHERE user_id=?", (code, user_id))
        return {"frame": code}

    # --- розыгрыш ------------------------------------------------------------------------

    def _raffle(self, day: str):
        row = self.conn.execute("SELECT * FROM raffles WHERE day=?", (day,)).fetchone()
        if row:
            return row
        seed = secrets.token_hex(32)
        self.conn.execute("INSERT INTO raffles (day, seed, seed_hash, prizes) VALUES (?,?,?,?)",
                          (day, seed, game.seed_hash(seed), json.dumps(rules.CFG["raffle"]["prizes"], ensure_ascii=False)))
        return self.conn.execute("SELECT * FROM raffles WHERE day=?", (day,)).fetchone()

    def _tickets(self, user_id: int, day: str | None = None) -> int:
        r = self.conn.execute("SELECT tickets FROM raffle_tickets WHERE day=? AND user_id=?",
                              (day or self.today(), user_id)).fetchone()
        return r["tickets"] if r else 0

    def _add_tickets(self, user_id: int, n: int) -> None:
        day = self.today()
        self._raffle(day)
        self.conn.execute("INSERT INTO raffle_tickets (day, user_id, tickets) VALUES (?,?,?)"
                          " ON CONFLICT(day, user_id) DO UPDATE SET tickets=tickets+excluded.tickets", (day, user_id, n))

    def raffle_today(self, user_id: int) -> dict:
        with self.tx():
            r = self._raffle(self.today())
        yesterday = (self.now_msk().date() - dt.timedelta(days=1)).isoformat()
        prev = self.conn.execute("SELECT * FROM raffles WHERE day=? AND drawn_at IS NOT NULL", (yesterday,)).fetchone()
        total = self.conn.execute("SELECT COALESCE(SUM(tickets),0) FROM raffle_tickets WHERE day=?", (self.today(),)).fetchone()[0]
        return {
            "day": r["day"], "seed_hash": r["seed_hash"], "prizes": json.loads(r["prizes"]),
            "my_tickets": self._tickets(user_id), "total_tickets": total,
            "draw_hour_msk": rules.CFG["raffle"]["draw_hour_msk"],
            "drawn": bool(r["drawn_at"]), "winners": json.loads(r["winners"]) if r["winners"] else None,
            "seed": r["seed"] if r["drawn_at"] else None,
            "previous": {"day": prev["day"], "seed": prev["seed"], "seed_hash": prev["seed_hash"],
                         "winners": json.loads(prev["winners"])} if prev else None,
        }

    def draw_raffle(self, day: str) -> dict | None:
        """Розыгрыш по раскрытому зерну. Повторный вызов ничего не делает."""
        with self.tx():
            r = self._raffle(day)
            if r["drawn_at"]:
                return None
            tickets = [(row["user_id"], row["tickets"]) for row in
                       self.conn.execute("SELECT user_id, tickets FROM raffle_tickets WHERE day=?", (day,))]
            prizes = [p for p in json.loads(r["prizes"]) for _ in range(p["count"])]
            winners = game.raffle_winners(r["seed"], day, tickets, len(prizes))
            out = []
            for uid, prize in zip(winners, prizes):
                reward = {"cases": prize.get("cases"), "spins": prize.get("n") if prize["kind"] == "spins" else None,
                          "level": prize.get("level") if prize["kind"] == "level_drop" else None}
                granted = self._grant_reward(uid, {k: v for k, v in reward.items() if v}, "raffle")
                name = self.conn.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()["name"]
                out.append({"user_id": uid, "name": name or "Игрок", "prize": prize["label"],
                            "item": granted.get("item", {}).get("name")})
                self._notify(uid, "raffle", f"Ты выиграл в розыгрыше: {prize['label']}!")
            self.conn.execute("UPDATE raffles SET winners=?, drawn_at=? WHERE day=?",
                              (json.dumps(out, ensure_ascii=False), self.clock(), day))
            return {"day": day, "seed": r["seed"], "seed_hash": r["seed_hash"], "winners": out}

    # --- рефералы и друзья -------------------------------------------------------------------

    def _referral_check(self, user_id: int) -> None:
        ref = self.conn.execute("SELECT * FROM referral_rewards WHERE invitee=? AND qualified_at IS NULL", (user_id,)).fetchone()
        if not ref:
            return
        p = self.conn.execute("SELECT total_spins FROM players WHERE user_id=?", (user_id,)).fetchone()
        if p["total_spins"] < rules.CFG["referral"]["qualify_spins"]:
            return
        self.conn.execute("UPDATE referral_rewards SET qualified_at=? WHERE invitee=?", (self.clock(), user_id))
        referrer = ref["referrer"]
        n = self.conn.execute("SELECT COUNT(*) FROM referral_rewards WHERE referrer=? AND qualified_at IS NOT NULL",
                              (referrer,)).fetchone()[0]
        for tier, reward in rules.CFG["referral"]["rewards"].items():
            if n >= int(tier) and not self.conn.execute("SELECT 1 FROM referral_tiers WHERE referrer=? AND tier=?",
                                                        (referrer, int(tier))).fetchone():
                self.conn.execute("INSERT INTO referral_tiers VALUES (?,?,?)", (referrer, int(tier), self.clock()))
                self._grant_reward(referrer, reward, "referral")
                self._notify(referrer, "referral", f"Друзей в игре: {n}. Награда уже у тебя.")

    def _friend_ids(self, user_id: int) -> list[int]:
        rows = self.conn.execute("SELECT referrer AS f FROM referral_rewards WHERE invitee=? UNION "
                                 "SELECT invitee AS f FROM referral_rewards WHERE referrer=?", (user_id, user_id)).fetchall()
        return [r["f"] for r in rows]

    def friends(self, user_id: int) -> dict:
        refs = self.conn.execute(
            "SELECT r.*, u.name FROM referral_rewards r JOIN users u ON u.id=r.invitee WHERE r.referrer=? ORDER BY r.created_at DESC",
            (user_id,)).fetchall()
        qualified = sum(1 for r in refs if r["qualified_at"])
        tiers = [{"friends": int(k), **v, "granted": bool(self.conn.execute(
            "SELECT 1 FROM referral_tiers WHERE referrer=? AND tier=?", (user_id, int(k))).fetchone())}
            for k, v in rules.CFG["referral"]["rewards"].items()]
        return {"invited": [{"id": r["invitee"], "name": r["name"] or "Игрок", "qualified": bool(r["qualified_at"]),
                             "spins": self.conn.execute("SELECT total_spins FROM players WHERE user_id=?",
                                                        (r["invitee"],)).fetchone()["total_spins"]} for r in refs],
                "qualified": qualified, "tiers": tiers, "qualify_spins": rules.CFG["referral"]["qualify_spins"],
                "friends": self._friend_ids(user_id)}

    # --- рейтинги --------------------------------------------------------------------------

    def leaderboard(self, user_id: int, board: str = "capital", scope: str = "friends") -> dict:
        if scope == "friends":
            ids = [user_id] + self._friend_ids(user_id)
            users = self.conn.execute(
                f"SELECT u.id, u.name, p.level, p.xp, p.frame FROM users u JOIN players p ON p.user_id=u.id"
                f" WHERE u.id IN ({','.join('?' * len(ids))}) AND u.is_banned=0", ids).fetchall()
        else:
            users = self.conn.execute("SELECT u.id, u.name, p.level, p.xp, p.frame FROM users u JOIN players p ON p.user_id=u.id"
                                      " WHERE u.is_banned=0").fetchall()
        rows = []
        for u in users:
            items = self.conn.execute("SELECT catalog_id, wear, stattrak, tier FROM items WHERE owner=? AND state!='gone'",
                                      (u["id"],)).fetchall()
            prices = [self.catalog.price(i["catalog_id"], i["wear"], bool(i["stattrak"])) for i in items]
            value = {"capital": sum(prices), "rarest": max(prices, default=0), "level": u["xp"],
                     "collection": len({i["catalog_id"] for i in items})}.get(board, sum(prices))
            rows.append({"user_id": u["id"], "name": u["name"] or "Игрок", "level": u["level"], "frame": u["frame"],
                         "value": value, "me": u["id"] == user_id})
        rows.sort(key=lambda r: -r["value"])
        for n, r in enumerate(rows, 1):
            r["place"] = n
        mine = next((r for r in rows if r["me"]), None)
        return {"board": board, "scope": scope, "rows": rows[:100], "me": mine}

    # --- дуэли без ставок -------------------------------------------------------------------

    def duels(self, user_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT d.*, u1.name AS n1, u2.name AS n2 FROM duels d JOIN users u1 ON u1.id=d.p1 LEFT JOIN users u2 ON u2.id=d.p2"
            " WHERE (d.status='open' AND (d.friend_id IS NULL OR d.friend_id=? OR d.p1=?)) OR d.p1=? OR d.p2=?"
            " ORDER BY d.id DESC LIMIT 40", (user_id, user_id, user_id, user_id)).fetchall()
        out = []
        for d in rows:
            p1_items = [self.item_view(self._item(i)) for i in json.loads(d["p1_items"])]
            out.append({"id": d["id"], "status": d["status"], "p1": d["p1"], "p2": d["p2"], "p1_name": d["n1"] or "Игрок",
                        "p2_name": d["n2"] or "Игрок", "p1_items": p1_items,
                        "p2_items": [self.item_view(self._item(i)) for i in json.loads(d["p2_items"])] if d["p2_items"] else [],
                        "p1_power": d["p1_power"], "p2_power": d["p2_power"], "winner": d["winner"],
                        "friend_id": d["friend_id"], "created_at": d["created_at"]})
        return {"duels": out, "left_today": rules.CFG["duels"]["casual"]["per_day"] - self._counter(user_id, "duel")}

    def create_duel(self, user_id: int, item_ids: list[int], friend_id: int | None = None, idem_key: str | None = None) -> dict:
        def run():
            c = rules.CFG["duels"]["casual"]
            if len(item_ids) != c["items"]:
                raise GameError("BAD_REQUEST", "Выбери 3 предмета для дуэли.")
            if self._counter(user_id, "duel") >= c["per_day"]:
                raise GameError("DAILY_LIMIT", "Дуэлей на сегодня больше нет: лимит 5 в сутки.")
            rows = self._owned(user_id, item_ids, allow_locked=True, allow_overflow=False)
            cur = self.conn.execute("INSERT INTO duels (p1, friend_id, p1_items, created_at) VALUES (?,?,?,?)",
                                    (user_id, friend_id, json.dumps(item_ids), self.clock()))
            for r in rows:
                self._update_item(r, "duel_in", user_id, {"duel": cur.lastrowid}, state="duel")
            self._bump_counter(user_id, "duel")
            if friend_id:
                name = self._user(user_id)["name"] or "Друг"
                self._notify(friend_id, "duel", f"{name} зовёт на дуэль коллекций. Открой «Друзья» → «Дуэли».")
            return {"id": cur.lastrowid, "status": "open"}

        return self.idempotent(user_id, idem_key, "duel_create", run)

    def join_duel(self, user_id: int, duel_id: int, item_ids: list[int], idem_key: str | None = None) -> dict:
        def run():
            c = rules.CFG["duels"]["casual"]
            d = self.conn.execute("SELECT * FROM duels WHERE id=? AND status='open'", (duel_id,)).fetchone()
            if not d:
                raise GameError("NOT_FOUND", "Дуэль уже закончилась.")
            if d["p1"] == user_id or (d["friend_id"] and d["friend_id"] != user_id):
                raise GameError("BAD_REQUEST", "Эта дуэль не для тебя.")
            if len(item_ids) != c["items"]:
                raise GameError("BAD_REQUEST", "Выбери 3 предмета для дуэли.")
            if self._counter(user_id, "duel") >= c["per_day"]:
                raise GameError("DAILY_LIMIT", "Дуэлей на сегодня больше нет: лимит 5 в сутки.")
            mine = self._owned(user_id, item_ids, allow_locked=True, allow_overflow=False)
            theirs = [self._item(i) for i in json.loads(d["p1_items"])]
            rng, seed = self._rng(user_id)
            p1 = game.duel_strength(rng, [self.catalog.price(r["catalog_id"], r["wear"], bool(r["stattrak"])) for r in theirs])
            p2 = game.duel_strength(rng, [self.catalog.price(r["catalog_id"], r["wear"], bool(r["stattrak"])) for r in mine])
            winner = d["p1"] if p1 >= p2 else user_id
            for r in theirs:
                if r["state"] == "duel":
                    self._update_item(r, "duel_out", d["p1"], {"duel": duel_id}, state="free")
            for r in mine:
                self._event(r["id"], "duel", user_id, {"duel": duel_id})
            prize = round(self.catalog.unit_cents * c["win_cases"])
            self._ledger(winner, prize, "duel_win", "duel", duel_id)
            self.conn.execute("UPDATE duels SET p2=?, p2_items=?, p1_power=?, p2_power=?, winner=?, status='done', finished_at=?"
                              " WHERE id=?", (user_id, json.dumps(item_ids), round(p1), round(p2), winner, self.clock(), duel_id))
            self._bump_counter(user_id, "duel")
            self._log_roll(user_id, "duel", rng, seed, {"duel": duel_id, "p1": round(p1), "p2": round(p2)})
            self._notify(d["p1"], "duel", "Дуэль сыграна: " + ("ты победил!" if winner == d["p1"] else "соперник оказался сильнее."))
            return {"id": duel_id, "winner": winner, "you_won": winner == user_id, "p1_power": round(p1), "p2_power": round(p2),
                    "prize_cents": prize, "fair": self._fair(seed, rng.nonce)}

        return self.idempotent(user_id, idem_key, "duel_join", run)

    def cancel_duel(self, user_id: int, duel_id: int) -> dict:
        with self.tx():
            d = self.conn.execute("SELECT * FROM duels WHERE id=? AND p1=? AND status='open'", (duel_id, user_id)).fetchone()
            if not d:
                raise GameError("NOT_FOUND", "Дуэль не найдена.")
            self._end_duel(d, "cancelled")
        return {"id": duel_id, "status": "cancelled"}

    def _end_duel(self, d, status: str) -> None:
        self.conn.execute("UPDATE duels SET status=?, finished_at=? WHERE id=?", (status, self.clock(), d["id"]))
        for i in json.loads(d["p1_items"]):
            r = self._item(i)
            if r["state"] == "duel":
                self._update_item(r, "duel_out", d["p1"], state="free")

    # --- лента дропов -------------------------------------------------------------------------

    def feed(self, limit: int = 12) -> list[dict]:
        rows = self.conn.execute(
            "SELECT i.*, u.name AS owner_name FROM items i JOIN users u ON u.id=i.owner"
            " WHERE (i.tier IN ('legendary','mythic','secret') OR i.tier='epic') AND (i.origin LIKE 'case:%' OR i.origin LIKE 'level:%')"
            " ORDER BY i.created_at DESC LIMIT ?", (limit,)).fetchall()
        return [{**self.item_view(r), "owner_name": r["owner_name"] or "Игрок"} for r in rows]
