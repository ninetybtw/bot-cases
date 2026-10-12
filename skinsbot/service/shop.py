"""Магазин Telegram Stars: попытки, удача, VIP, места, набор «Старт». Включается флагом stars.shop_enabled."""

from __future__ import annotations

import json
import secrets

from .. import rules
from .core import GameError


class Shop:
    def _month_spent(self, user_id: int) -> int:
        start = self.now_msk().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        return self.conn.execute("SELECT COALESCE(SUM(stars),0) FROM star_payments WHERE user_id=? AND status='granted'"
                                 " AND granted_at>=?", (user_id, start)).fetchone()[0]

    def _bought(self, user_id: int, product: str, since: float | None = None) -> int:
        sql = "SELECT COUNT(*) FROM star_payments WHERE user_id=? AND product=? AND status='granted'"
        args = [user_id, product]
        if since is not None:
            sql += " AND granted_at>=?"
            args.append(since)
        return self.conn.execute(sql, args).fetchone()[0]

    def _attempts_bought_today(self, user_id: int) -> int:
        start = self.now_msk().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        total = 0
        for r in self.conn.execute("SELECT granted FROM star_payments WHERE user_id=? AND status='granted' AND granted_at>=?",
                                   (user_id, start)):
            total += json.loads(r["granted"] or "{}").get("attempts", 0)
        return total

    def _check_product(self, user_id: int, code: str) -> dict:
        s = rules.CFG["stars"]
        if not s["shop_enabled"]:
            raise GameError("DISABLED", "Магазин откроется после юридической проверки.")
        prod = s["products"].get(code)
        if not prod:
            raise GameError("NOT_FOUND", "Товар не найден.")
        p = self._player(user_id)
        if self._month_spent(user_id) + prod["stars"] > s["month_cap"]:
            raise GameError("STARS_MONTH_CAP", f"Месячный лимит {s['month_cap']} ⭐ исчерпан. Повысить можно через /paysupport.")
        g, lim = prod["grants"], prod.get("limits", {})
        if g.get("attempts"):
            if p["paid_attempts"] + g["attempts"] > s["attempts_stock_max"]:
                raise GameError("DAILY_LIMIT", f"Запас попыток не больше {s['attempts_stock_max']}.")
            if self._attempts_bought_today(user_id) + g["attempts"] > s["attempts_buy_per_day"]:
                raise GameError("DAILY_LIMIT", f"За сутки можно купить не больше {s['attempts_buy_per_day']} попыток.")
        if g.get("luck") and p["luck_left"] and p["luck_mult"] and p["luck_mult"] >= g["luck"]["mult"]:
            raise GameError("BAD_REQUEST", "Удача уже действует. Усиления не складываются.")
        if lim.get("once") and self._bought(user_id, code):
            raise GameError("BAD_REQUEST", "Этот набор можно купить один раз.")
        if lim.get("max_times") and self._bought(user_id, code) >= lim["max_times"]:
            raise GameError("BAD_REQUEST", "Больше этот товар купить нельзя.")
        if lim.get("per_day"):
            start = self.now_msk().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            if self._bought(user_id, code, start) >= lim["per_day"]:
                raise GameError("DAILY_LIMIT", f"Не больше {lim['per_day']} покупок в сутки.")
        if g.get("slots") and p["slots"] + g["slots"] > rules.CFG["inventory"]["slots_max"]:
            raise GameError("BAD_REQUEST", "Коллекция уже максимального размера.")
        return prod

    def shop(self, user_id: int) -> dict:
        s = rules.CFG["stars"]
        products = []
        for code, prod in s["products"].items():
            try:
                self._check_product(user_id, code)
                available, reason = True, None
            except GameError as e:
                available, reason = False, e.message
            products.append({"code": code, "title": prod["title"], "stars": prod["stars"], "grants": prod["grants"],
                             "available": available, "reason": reason})
        return {"enabled": s["shop_enabled"], "products": products, "month_spent": self._month_spent(user_id),
                "month_cap": s["month_cap"], "warn": self._month_spent(user_id) >= s["month_cap"] * s["warn_at_pct"] / 100}

    def create_invoice(self, user_id: int, code: str) -> dict:
        """Запись star_payments (invoiced). Ссылку на счёт создаёт бот через createInvoiceLink."""
        with self.tx():
            prod = self._check_product(user_id, code)
            payload = f"p:{user_id}:{code}:{secrets.token_hex(6)}"
            self.conn.execute("INSERT INTO star_payments (user_id, product, stars, payload, created_at) VALUES (?,?,?,?,?)",
                              (user_id, code, prod["stars"], payload, self.clock()))
        return {"payload": payload, "title": prod["title"], "stars": prod["stars"]}

    def precheckout(self, payload: str, user_id: int, stars: int) -> str | None:
        """Проверка на шаге pre_checkout_query. Возвращает текст ошибки или None."""
        row = self.conn.execute("SELECT * FROM star_payments WHERE payload=?", (payload,)).fetchone()
        if not row or row["user_id"] != user_id or row["status"] != "invoiced" or row["stars"] != stars:
            return "Счёт устарел. Открой магазин заново."
        try:
            with self.tx():
                self._check_product(user_id, row["product"])
        except GameError as e:
            return e.message
        return None

    def grant_payment(self, payload: str, charge_id: str, stars: int) -> dict:
        """successful_payment: выдать покупку один раз. Повтор с тем же charge_id ничего не выдаёт."""
        with self.tx():
            if self.conn.execute("SELECT 1 FROM star_payments WHERE charge_id=?", (charge_id,)).fetchone():
                return {"granted": False, "reason": "duplicate"}
            row = self.conn.execute("SELECT * FROM star_payments WHERE payload=?", (payload,)).fetchone()
            if not row or row["status"] != "invoiced" or row["stars"] != stars:
                return {"granted": False, "reason": "unknown_payload"}
            prod = rules.CFG["stars"]["products"][row["product"]]
            g = prod["grants"]
            uid = row["user_id"]
            now = self.clock()
            if g.get("attempts"):
                self.conn.execute("UPDATE players SET paid_attempts=MIN(?, paid_attempts+?) WHERE user_id=?",
                                  (rules.CFG["stars"]["attempts_stock_max"], g["attempts"], uid))
            if g.get("luck"):
                self.conn.execute("UPDATE players SET luck_mult=?, luck_left=? WHERE user_id=?",
                                  (g["luck"]["mult"], g["luck"]["spins"], uid))
            if g.get("vip_days"):
                p = self._player(uid)
                base = max(now, p["vip_until"] or 0)
                self.conn.execute("UPDATE players SET vip_until=? WHERE user_id=?", (base + g["vip_days"] * 86400, uid))
            if g.get("slots"):
                self.conn.execute("UPDATE players SET slots=MIN(?, slots+?) WHERE user_id=?",
                                  (rules.CFG["inventory"]["slots_max"], g["slots"], uid))
            self.conn.execute("UPDATE star_payments SET status='granted', charge_id=?, granted=?, granted_at=? WHERE id=?",
                              (charge_id, json.dumps(g), now, row["id"]))
            return {"granted": True, "user_id": uid, "product": row["product"], "title": prod["title"]}

    def refund_payment(self, payment_id: int) -> dict:
        """Снять неиспользованную покупку перед refundStarPayment. Использованные попытки и удачу не возвращаем."""
        with self.tx():
            row = self.conn.execute("SELECT * FROM star_payments WHERE id=?", (payment_id,)).fetchone()
            if not row or row["status"] != "granted":
                raise GameError("NOT_FOUND", "Платёж не найден или уже возвращён.")
            g = json.loads(row["granted"] or "{}")
            p = self._player(row["user_id"])
            if g.get("attempts") and p["paid_attempts"] < g["attempts"]:
                raise GameError("BAD_REQUEST", "Попытки из этой покупки уже использованы.")
            if g.get("luck") and p["luck_left"] < g["luck"]["spins"]:
                raise GameError("BAD_REQUEST", "Удача из этой покупки уже использована.")
            if g.get("attempts"):
                self.conn.execute("UPDATE players SET paid_attempts=paid_attempts-? WHERE user_id=?", (g["attempts"], row["user_id"]))
            if g.get("luck"):
                self.conn.execute("UPDATE players SET luck_left=0, luck_mult=NULL WHERE user_id=?", (row["user_id"],))
            if g.get("vip_days"):
                self.conn.execute("UPDATE players SET vip_until=MAX(?, vip_until-?) WHERE user_id=?",
                                  (self.clock(), g["vip_days"] * 86400, row["user_id"]))
            if g.get("slots"):
                self.conn.execute("UPDATE players SET slots=MAX(?, slots-?) WHERE user_id=?",
                                  (rules.CFG["inventory"]["slots"], g["slots"], row["user_id"]))
            self.conn.execute("UPDATE star_payments SET status='refunded', refunded_at=? WHERE id=?", (self.clock(), payment_id))
            return {"user_id": row["user_id"], "charge_id": row["charge_id"], "stars": row["stars"]}
