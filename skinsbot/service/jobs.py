"""Фоновые задачи (worker в docs/ARCHITECTURE.md). Каждая идемпотентна: повторный запуск ничего не ломает."""

from __future__ import annotations

import datetime as dt
import json

from .. import rules


class Jobs:
    def _job_due(self, name: str, period: str) -> bool:
        row = self.conn.execute("SELECT last_run FROM jobs WHERE name=?", (name,)).fetchone()
        return not row or row["last_run"] != period

    def _job_done(self, name: str, period: str) -> None:
        self.conn.execute("INSERT INTO jobs (name, last_run) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET last_run=excluded.last_run",
                          (name, period))

    def record_prices(self) -> int:
        """Цены дня для графика 30 дней на карточке и изменения коллекции за неделю."""
        day = dt.datetime.fromtimestamp(self.catalog.generated_at, rules_tz()).strftime("%Y-%m-%d")
        with self.tx():
            if not self._job_due("prices.record", day):
                return 0
            rows = [(cid, key, day, int(p)) for cid, it in self.catalog.items.items() for key, p in it["prices"].items()]
            self.conn.executemany("INSERT OR REPLACE INTO price_daily (catalog_id, key, day, cents) VALUES (?,?,?,?)", rows)
            self._job_done("prices.record", day)
        return len(rows)

    def overflow_expire(self) -> int:
        """Переполнение старше 48 ч продаётся системе по 60%, игроку приходит уведомление."""
        n = 0
        with self.tx():
            rows = self.conn.execute("SELECT * FROM items WHERE state='free' AND overflow_until IS NOT NULL AND overflow_until<?",
                                     (self.clock(),)).fetchall()
            by_user: dict[int, int] = {}
            for r in rows:
                price = rules.quick_sell(self.catalog.price(r["catalog_id"], r["wear"], bool(r["stattrak"])))
                self._update_item(r, "overflow_sold", None, {"price": price}, state="gone", owner=None)
                self._ledger(r["owner"], price, "overflow_sale", "item", r["id"])
                by_user[r["owner"]] = by_user.get(r["owner"], 0) + price
                n += 1
            for uid, total in by_user.items():
                self._notify(uid, "overflow", f"Предметы из переполнения проданы системе за {self.money(uid, total)}.")
        return n

    def market_expire(self) -> int:
        n = 0
        now = self.clock()
        with self.tx():
            for lot in self.conn.execute("SELECT * FROM market_lots WHERE status='active' AND expires_at<?", (now,)).fetchall():
                self._close_lot(lot, "expired")
                n += 1
            hours = rules.CFG["trades"]["expire_hours"] * 3600
            for t in self.conn.execute("SELECT * FROM trades WHERE status='pending' AND created_at<?", (now - hours,)).fetchall():
                self._close_trade(t, "expired")
                n += 1
            dh = rules.CFG["duels"]["casual"]["expire_hours"] * 3600
            for d in self.conn.execute("SELECT * FROM duels WHERE status='open' AND created_at<?", (now - dh,)).fetchall():
                self._end_duel(d, "expired")
                n += 1
        return n

    def piggy_tax(self) -> int:
        """Налог на копилку: со всего, что выше порога, сгорает pct_day% в сутки."""
        cfg = rules.CFG["piggy_tax"]
        day = self.today()
        n = 0
        with self.tx():
            if not self._job_due("piggy.tax", day):
                return 0
            for p in self.conn.execute("SELECT user_id, balance FROM players WHERE balance>?", (cfg["above_cents"],)).fetchall():
                tax = (p["balance"] - cfg["above_cents"]) * cfg["pct_day"] // 100
                if tax:
                    self._ledger(p["user_id"], -tax, "piggy_tax")
                    n += 1
            self._job_done("piggy.tax", day)
        return n

    def raffle_due(self) -> list[dict]:
        out = []
        now = self.now_msk()
        days = [(now.date() - dt.timedelta(days=1)).isoformat()]
        if now.hour >= rules.CFG["raffle"]["draw_hour_msk"]:
            days.append(now.date().isoformat())
        for day in days:
            exists = self.conn.execute("SELECT drawn_at FROM raffles WHERE day=?", (day,)).fetchone()
            if exists and not exists["drawn_at"]:
                res = self.draw_raffle(day)
                if res:
                    out.append(res)
        return out

    def energy_notify(self) -> int:
        n = 0
        with self.tx():
            for p in self.conn.execute("SELECT * FROM players WHERE energy_notified=0").fetchall():
                energy, _ = self._energy(p)
                if energy >= rules.CFG["energy"]["max"]:
                    self.conn.execute("UPDATE players SET energy_notified=1 WHERE user_id=?", (p["user_id"],))
                    self._notify(p["user_id"], "energy", "Спин готов: бесплатные спины накопились, заходи крутить.")
                    n += 1
        return n

    def fraud_scan(self) -> int:
        """Предмет прошёл через 3+ аккаунта за сутки — в очередь проверки."""
        since = self.clock() - 86400
        n = 0
        with self.tx():
            rows = self.conn.execute(
                "SELECT item_id, COUNT(*) AS c FROM item_events WHERE kind='owner' AND ts>? GROUP BY item_id HAVING c>=2",
                (since,)).fetchall()
            for r in rows:
                owners = set()
                for e in self.conn.execute("SELECT data FROM item_events WHERE item_id=? AND kind='owner' AND ts>?", (r["item_id"], since)):
                    d = json.loads(e["data"])
                    owners.update([d.get("from"), d.get("to")])
                owners.discard(None)
                if len(owners) >= 3 and not self.conn.execute(
                        "SELECT 1 FROM fraud_flags WHERE kind='item_hops' AND item_ids=? AND created_at>?",
                        (json.dumps([r["item_id"]]), since)).fetchone():
                    self._flag("item_hops", sorted(owners), [r["item_id"]], {"owners": len(owners)})
                    n += 1
        return n

    def pending_notifications(self) -> list[dict]:
        """Что можно отправить сейчас: не больше 2 в сутки на игрока, тишина 21:00–08:00 по Москве."""
        cfg = rules.CFG["notifications"]
        hour = self.now_msk().hour
        if hour >= cfg["quiet_from"] or hour < cfg["quiet_to"]:
            return []
        out = []
        with self.tx():
            day_start = self.now_msk().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            self.conn.execute("UPDATE notifications SET skipped=1 WHERE sent_at IS NULL AND skipped=0 AND created_at<?",
                              (self.clock() - 86400,))
            for nrow in self.conn.execute("SELECT * FROM notifications WHERE sent_at IS NULL AND skipped=0 ORDER BY id").fetchall():
                sent = self.conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND sent_at>=?",
                                         (nrow["user_id"], day_start)).fetchone()[0]
                sent += sum(1 for o in out if o["user_id"] == nrow["user_id"])
                if sent >= cfg["per_day"]:
                    continue
                out.append(dict(nrow))
        return out

    def mark_sent(self, notification_id: int, ok: bool) -> None:
        with self.tx():
            if ok:
                self.conn.execute("UPDATE notifications SET sent_at=? WHERE id=?", (self.clock(), notification_id))
            else:
                self.conn.execute("UPDATE notifications SET skipped=1 WHERE id=?", (notification_id,))

    def run_jobs(self) -> dict:
        """Один проход worker: вызывается раз в минуту из __main__."""
        if self.catalog.reload_if_changed():
            pass
        return {
            "prices": self.record_prices(),
            "overflow": self.overflow_expire(),
            "market": self.market_expire(),
            "piggy": self.piggy_tax(),
            "raffle": self.raffle_due(),
            "energy": self.energy_notify(),
            "fraud": self.fraud_scan(),
        }


def rules_tz():
    from .core import MSK

    return MSK
