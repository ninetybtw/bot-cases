"""Рынок за монеты (лоты, заявки, сделки) и обмены между игроками. Рынок за Stars — за флагом, выключен."""

from __future__ import annotations

import json

from .. import rules
from .core import GameError


class Market:
    def _reference_price(self, row) -> int:
        return self.catalog.price(row["catalog_id"], row["wear"], bool(row["stattrak"]))

    def _account_age_ok(self, user_id: int, hours: int) -> bool:
        return self._user(user_id)["created_at"] + hours * 3600 <= self.clock()

    def _lot_view(self, lot) -> dict:
        item = self.item_view(self._item(lot["item_id"]))
        seller = self.conn.execute("SELECT name FROM users WHERE id=?", (lot["seller"],)).fetchone()
        return {"id": lot["id"], "item": item, "price_cents": lot["price"], "currency": lot["currency"],
                "seller_name": (seller["name"] if seller else "") or "Игрок", "seller_id": lot["seller"],
                "status": lot["status"], "created_at": lot["created_at"], "expires_at": lot["expires_at"],
                "buyer_id": lot["buyer"], "sold_at": lot["sold_at"],
                "price_pct": round(lot["price"] / max(1, item["price_cents"]) * 100)}

    # --- витрина ---------------------------------------------------------------------

    def market_lots(self, user_id: int, currency: str = "coins", tier: str | None = None, q: str | None = None,
                    sort: str = "new", mine: bool = False) -> dict:
        sql = "SELECT l.* FROM market_lots l JOIN items i ON i.id=l.item_id WHERE l.currency=?"
        args: list = [currency]
        if mine:
            sql += " AND l.seller=? AND l.status='active'"
            args.append(user_id)
        else:
            sql += " AND l.status='active'"
        if tier:
            sql += " AND i.tier=?"
            args.append(tier)
        sql += {"price": " ORDER BY l.price", "price_desc": " ORDER BY l.price DESC"}.get(sort, " ORDER BY l.id DESC")
        lots = [self._lot_view(r) for r in self.conn.execute(sql + " LIMIT 200", args)]
        if q:
            lots = [lot for lot in lots if q.lower() in lot["item"]["name"].lower()]
        return {"lots": lots, "stars_enabled": rules.CFG["market"]["stars"]["enabled"]}

    def market_deals(self, user_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT * FROM market_lots WHERE status='sold' AND (seller=? OR buyer=?) ORDER BY sold_at DESC LIMIT 50",
            (user_id, user_id)).fetchall()
        return {"deals": [{**self._lot_view(r), "side": "sell" if r["seller"] == user_id else "buy"} for r in rows]}

    # --- лоты ------------------------------------------------------------------------

    def create_lot(self, user_id: int, item_id: int, price_cents: int, currency: str = "coins",
                   idem_key: str | None = None) -> dict:
        def run():
            m = rules.CFG["market"]
            if currency == "stars" and not m["stars"]["enabled"]:
                raise GameError("DISABLED", "Рынок за Stars откроется после проверки юристом.")
            if currency != "coins":
                raise GameError("BAD_REQUEST", "Неизвестная валюта лота.")
            if not self._account_age_ok(user_id, m["new_account_hours"]):
                raise GameError("NOT_ALLOWED_YET", "Новый аккаунт может торговать через 24 часа после первого входа.")
            row = self._owned(user_id, [item_id], allow_hold=False, allow_overflow=False)[0]
            active = self.conn.execute("SELECT COUNT(*) FROM market_lots WHERE seller=? AND status='active'", (user_id,)).fetchone()[0]
            if active >= m["lots_max"]:
                raise GameError("DAILY_LIMIT", f"Можно держать не больше {m['lots_max']} лотов.")
            ref = self._reference_price(row)
            lo, hi = ref * m["band_pct"][0] // 100, ref * m["band_pct"][1] // 100
            if not lo <= price_cents <= hi:
                raise GameError("PRICE_OUT_OF_BAND",
                                f"Цена лота от {self.money(user_id, lo)} до {self.money(user_id, hi)} (50–150% справочной).",
                                min_cents=lo, max_cents=hi)
            p = self._player(user_id)
            fee = m["fee_vip_pct"] if self.is_vip(p) else m["fee_pct"]
            now = self.clock()
            cur = self.conn.execute(
                "INSERT INTO market_lots (item_id, seller, currency, price, fee_pct, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                (item_id, user_id, currency, price_cents, fee, now, now + m["lot_days"] * 86400))
            self._update_item(row, "market_list", user_id, {"lot": cur.lastrowid}, state="market")
            lot_id = cur.lastrowid
            self._match_orders_for_lot(lot_id)
            return self._lot_view(self.conn.execute("SELECT * FROM market_lots WHERE id=?", (lot_id,)).fetchone())

        return self.idempotent(user_id, idem_key, "lot_create", run)

    def cancel_lot(self, user_id: int, lot_id: int, idem_key: str | None = None) -> dict:
        def run():
            lot = self.conn.execute("SELECT * FROM market_lots WHERE id=? AND seller=? AND status='active'",
                                    (lot_id, user_id)).fetchone()
            if not lot:
                raise GameError("NOT_FOUND", "Лот не найден.")
            self._close_lot(lot, "cancelled")
            return {"id": lot_id, "status": "cancelled"}

        return self.idempotent(user_id, idem_key, "lot_cancel", run)

    def _close_lot(self, lot, status: str) -> None:
        self.conn.execute("UPDATE market_lots SET status=? WHERE id=? AND status='active'", (status, lot["id"]))
        row = self._item(lot["item_id"])
        if row["state"] == "market":
            self._update_item(row, f"market_{status}", lot["seller"], state="free")

    def buy_lot(self, user_id: int, lot_id: int, idem_key: str | None = None) -> dict:
        def run():
            lot = self.conn.execute("SELECT * FROM market_lots WHERE id=?", (lot_id,)).fetchone()
            if not lot or lot["status"] != "active":
                raise GameError("NOT_FOUND", "Лот уже купили или сняли.")
            if lot["currency"] != "coins":
                raise GameError("DISABLED", "Рынок за Stars откроется после проверки юристом.")
            if lot["seller"] == user_id:
                raise GameError("BAD_REQUEST", "Это твой лот.")
            if not self._account_age_ok(user_id, rules.CFG["market"]["new_account_hours"]):
                raise GameError("NOT_ALLOWED_YET", "Новый аккаунт может торговать через 24 часа после первого входа.")
            return self._execute_sale(lot, user_id, lot["price"])

        return self.idempotent(user_id, idem_key, "lot_buy", run)

    def _execute_sale(self, lot, buyer: int, price: int, reserved: bool = False) -> dict:
        """Покупка лота: монеты покупателя → продавцу за вычетом комиссии, комиссия уходит из игры."""
        if self._free_slots(buyer) < 1:
            raise GameError("INVENTORY_FULL", "Освободи место в коллекции, чтобы купить предмет.")
        if not reserved:
            self._charge(buyer, price, "market_buy", "lot", lot["id"])
        fee = int(price * lot["fee_pct"]) // 100
        self._ledger(lot["seller"], price - fee, "market_sale", "lot", lot["id"])
        # Комиссия это слив: покупатель заплатил price, продавец получил price - fee, разница ушла из игры.
        now = self.clock()
        self.conn.execute("UPDATE market_lots SET status='sold', buyer=?, sold_at=? WHERE id=? AND status='active'",
                          (buyer, now, lot["id"]))
        row = self._item(lot["item_id"])
        hold = now + rules.CFG["market"]["hold_hours"] * 3600
        self._update_item(row, "owner", buyer, {"from": lot["seller"], "to": buyer, "lot": lot["id"], "price": price},
                          state="free", owner=buyer, hold_until=hold, locked=0)
        self._notify(lot["seller"], "market_sold", f"Твой лот купили за {self.money(lot['seller'], price)}. "
                     f"Комиссия {lot['fee_pct']:g}%.")
        return {"lot_id": lot["id"], "item": self.item_view(self._item(lot["item_id"])), "price_cents": price,
                "fee_cents": fee, "balance_cents": self._player(buyer)["balance"]}

    # --- заявки на покупку -------------------------------------------------------------

    def buy_orders(self, user_id: int) -> dict:
        rows = self.conn.execute("SELECT * FROM buy_orders WHERE buyer=? ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()
        out = []
        for r in rows:
            it = self.catalog.items.get(r["catalog_id"], {})
            out.append({"id": r["id"], "catalog_item_id": r["catalog_id"], "name": it.get("name"), "image_url": it.get("image"),
                        "tier": it.get("tier"), "wear": r["wear"], "stattrak": bool(r["stattrak"]),
                        "max_price_cents": r["max_price"], "status": r["status"], "item_id": r["item_id"]})
        return {"orders": out}

    def create_order(self, user_id: int, catalog_item_id: str, wear: str | None, stattrak: bool, max_price_cents: int,
                     idem_key: str | None = None) -> dict:
        def run():
            m = rules.CFG["market"]
            it = self.catalog.items.get(catalog_item_id)
            key = rules.price_key(wear, stattrak)
            if not it or key not in it["prices"]:
                raise GameError("NOT_FOUND", "Предмет не найден.")
            ref = it["prices"][key]
            lo, hi = ref * m["band_pct"][0] // 100, ref * m["band_pct"][1] // 100
            if not lo <= max_price_cents <= hi:
                raise GameError("PRICE_OUT_OF_BAND", f"Цена заявки от {self.money(user_id, lo)} до {self.money(user_id, hi)}.",
                                min_cents=lo, max_cents=hi)
            active = self.conn.execute("SELECT COUNT(*) FROM buy_orders WHERE buyer=? AND status='active'", (user_id,)).fetchone()[0]
            if active >= m["orders_max"]:
                raise GameError("DAILY_LIMIT", f"Можно держать не больше {m['orders_max']} заявок.")
            if not self._account_age_ok(user_id, m["new_account_hours"]):
                raise GameError("NOT_ALLOWED_YET", "Новый аккаунт может торговать через 24 часа после первого входа.")
            self._charge(user_id, max_price_cents, "order_reserve")
            cur = self.conn.execute(
                "INSERT INTO buy_orders (buyer, catalog_id, wear, stattrak, max_price, reserved, created_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, catalog_item_id, wear, int(stattrak), max_price_cents, max_price_cents, self.clock()))
            order_id = cur.lastrowid
            lot = self.conn.execute(
                "SELECT l.* FROM market_lots l JOIN items i ON i.id=l.item_id WHERE l.status='active' AND l.currency='coins'"
                " AND i.catalog_id=? AND i.wear IS ? AND i.stattrak=? AND l.price<=? AND l.seller!=? ORDER BY l.price, l.id LIMIT 1",
                (catalog_item_id, wear, int(stattrak), max_price_cents, user_id)).fetchone()
            if lot:
                self._fill_order(order_id, lot)
            return self.buy_orders(user_id)["orders"][0]

        return self.idempotent(user_id, idem_key, "order_create", run)

    def cancel_order(self, user_id: int, order_id: int, idem_key: str | None = None) -> dict:
        def run():
            o = self.conn.execute("SELECT * FROM buy_orders WHERE id=? AND buyer=? AND status='active'", (order_id, user_id)).fetchone()
            if not o:
                raise GameError("NOT_FOUND", "Заявка не найдена.")
            self.conn.execute("UPDATE buy_orders SET status='cancelled' WHERE id=?", (order_id,))
            self._ledger(user_id, o["reserved"], "order_release", "order", order_id)
            return {"id": order_id, "status": "cancelled"}

        return self.idempotent(user_id, idem_key, "order_cancel", run)

    def _match_orders_for_lot(self, lot_id: int) -> None:
        lot = self.conn.execute("SELECT * FROM market_lots WHERE id=?", (lot_id,)).fetchone()
        item = self._item(lot["item_id"])
        o = self.conn.execute(
            "SELECT * FROM buy_orders WHERE status='active' AND catalog_id=? AND wear IS ? AND stattrak=? AND max_price>=?"
            " AND buyer!=? ORDER BY max_price DESC, id LIMIT 1",
            (item["catalog_id"], item["wear"], item["stattrak"], lot["price"], lot["seller"])).fetchone()
        if o:
            self._fill_order(o["id"], lot)

    def _fill_order(self, order_id: int, lot) -> None:
        o = self.conn.execute("SELECT * FROM buy_orders WHERE id=?", (order_id,)).fetchone()
        if self._free_slots(o["buyer"]) < 1:
            return
        self._execute_sale(lot, o["buyer"], lot["price"], reserved=True)
        # Заблокированные монеты: цена ушла продавцу, разница возвращается покупателю.
        if o["reserved"] > lot["price"]:
            self._ledger(o["buyer"], o["reserved"] - lot["price"], "order_release", "order", order_id)
        self.conn.execute("UPDATE buy_orders SET status='filled', item_id=? WHERE id=?", (lot["item_id"], order_id))
        self._notify(o["buyer"], "order_filled", "Заявка исполнена: предмет уже в коллекции.")

    # --- обмены --------------------------------------------------------------------------

    def trades(self, user_id: int) -> dict:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE (from_user=? OR to_user=?) ORDER BY id DESC LIMIT 50", (user_id, user_id)).fetchall()
        out = []
        for t in rows:
            items = self.conn.execute("SELECT * FROM trade_items WHERE trade_id=?", (t["id"],)).fetchall()
            names = {r["id"]: r["name"] for r in self.conn.execute(
                "SELECT id, name FROM users WHERE id IN (?,?)", (t["from_user"], t["to_user"]))}
            out.append({
                "id": t["id"], "status": t["status"], "incoming": t["to_user"] == user_id,
                "from_user": t["from_user"], "to_user": t["to_user"],
                "from_name": names.get(t["from_user"]) or "Игрок", "to_name": names.get(t["to_user"]) or "Игрок",
                "give": [self.item_view(self._item(r["item_id"])) for r in items if r["side"] == "from"],
                "take": [self.item_view(self._item(r["item_id"])) for r in items if r["side"] == "to"],
                "value_from_cents": t["value_from"], "value_to_cents": t["value_to"], "created_at": t["created_at"],
            })
        return {"trades": out}

    def propose_trade(self, user_id: int, to_user: int, give: list[int], take: list[int], idem_key: str | None = None) -> dict:
        def run():
            t = rules.CFG["trades"]
            if to_user == user_id:
                raise GameError("BAD_REQUEST", "Нельзя меняться с собой.")
            if not self.conn.execute("SELECT 1 FROM users WHERE id=?", (to_user,)).fetchone():
                raise GameError("NOT_FOUND", "Игрок не найден.")
            if not give or len(give) > t["max_items"] or len(take) > t["max_items"]:
                raise GameError("BAD_REQUEST", f"В обмене от 1 до {t['max_items']} предметов с каждой стороны.")
            for uid in (user_id, to_user):
                if not self._account_age_ok(uid, t["new_account_hours"]):
                    raise GameError("NOT_ALLOWED_YET", "Обмены доступны через 24 часа после первого входа обоих игроков.")
            week = self.clock() - 7 * 86400
            pair = self.conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='accepted' AND closed_at>? AND"
                " ((from_user=? AND to_user=?) OR (from_user=? AND to_user=?))",
                (week, user_id, to_user, to_user, user_id)).fetchone()[0]
            if pair >= t["per_pair_week"]:
                raise GameError("DAILY_LIMIT", "С этим игроком уже было 3 обмена за неделю.")
            mine = self._owned(user_id, give, allow_hold=False, allow_overflow=False)
            theirs = self._owned(to_user, take, allow_hold=False, allow_overflow=False) if take else []
            v_from = sum(self._reference_price(r) for r in mine)
            v_to = sum(self._reference_price(r) for r in theirs)
            if max(v_from, v_to) and abs(v_from - v_to) / max(v_from, v_to) * 100 > t["max_value_gap_pct"]:
                raise GameError("PRICE_OUT_OF_BAND", f"Слишком большая разница в цене: не больше {t['max_value_gap_pct']}%.")
            cur = self.conn.execute(
                "INSERT INTO trades (from_user, to_user, value_from, value_to, created_at) VALUES (?,?,?,?,?)",
                (user_id, to_user, v_from, v_to, self.clock()))
            tid = cur.lastrowid
            for r in mine:
                self.conn.execute("INSERT INTO trade_items VALUES (?,?,?)", (tid, r["id"], "from"))
                self._update_item(r, "trade_offer", user_id, {"trade": tid}, state="trade")
            for r in theirs:
                self.conn.execute("INSERT INTO trade_items VALUES (?,?,?)", (tid, r["id"], "to"))
            name = self._user(user_id)["name"] or "Игрок"
            self._notify(to_user, "trade", f"{name} предлагает обмен. Открой «Рынок» → «Обмены».")
            return {"id": tid, "status": "pending"}

        return self.idempotent(user_id, idem_key, "trade", run)

    def answer_trade(self, user_id: int, trade_id: int, action: str, idem_key: str | None = None) -> dict:
        def run():
            t = self.conn.execute("SELECT * FROM trades WHERE id=? AND status='pending'", (trade_id,)).fetchone()
            if not t:
                raise GameError("NOT_FOUND", "Обмен не найден или уже закрыт.")
            if action == "cancel" and t["from_user"] != user_id or action in ("accept", "decline") and t["to_user"] != user_id:
                raise GameError("BAD_REQUEST", "Это действие недоступно.")
            items = self.conn.execute("SELECT * FROM trade_items WHERE trade_id=?", (trade_id,)).fetchall()
            if action in ("decline", "cancel"):
                self._close_trade(t, "declined" if action == "decline" else "cancelled")
                return {"id": trade_id, "status": "declined" if action == "decline" else "cancelled"}
            if action != "accept":
                raise GameError("BAD_REQUEST", "Неизвестное действие.")
            take = [r["item_id"] for r in items if r["side"] == "to"]
            theirs = self._owned(user_id, take, allow_hold=False, allow_overflow=False) if take else []
            give_rows = [self._item(r["item_id"]) for r in items if r["side"] == "from"]
            if any(r["state"] != "trade" or r["owner"] != t["from_user"] for r in give_rows):
                raise GameError("CONFLICT", "Предметы обмена изменились.")
            if self._free_slots(user_id) + len(theirs) < len(give_rows) or self._free_slots(t["from_user"]) < len(theirs):
                raise GameError("INVENTORY_FULL", "У одного из игроков нет места в коллекции.")
            hold = self.clock() + rules.CFG["market"]["hold_hours"] * 3600
            for r in give_rows:
                self._update_item(r, "owner", user_id, {"from": t["from_user"], "to": user_id, "trade": trade_id},
                                  state="free", owner=user_id, hold_until=hold, locked=0)
            for r in theirs:
                self._update_item(r, "owner", user_id, {"from": user_id, "to": t["from_user"], "trade": trade_id},
                                  state="free", owner=t["from_user"], hold_until=hold, locked=0)
            self.conn.execute("UPDATE trades SET status='accepted', closed_at=? WHERE id=?", (self.clock(), trade_id))
            self._notify(t["from_user"], "trade", "Обмен принят: предметы уже в коллекции.")
            if t["value_from"] and t["value_to"] and abs(t["value_from"] - t["value_to"]) / max(t["value_from"], t["value_to"]) > 0.3:
                self._flag("trade_value_gap", [t["from_user"], user_id], [r["id"] for r in give_rows + theirs],
                           {"trade": trade_id, "from": t["value_from"], "to": t["value_to"]})
            return {"id": trade_id, "status": "accepted"}

        return self.idempotent(user_id, idem_key, "trade_answer", run)

    def _close_trade(self, t, status: str) -> None:
        self.conn.execute("UPDATE trades SET status=?, closed_at=? WHERE id=?", (status, self.clock(), t["id"]))
        for r in self.conn.execute("SELECT * FROM trade_items WHERE trade_id=? AND side='from'", (t["id"],)).fetchall():
            row = self._item(r["item_id"])
            if row["state"] == "trade":
                self._update_item(row, f"trade_{status}", t["from_user"], state="free")

    def _flag(self, kind: str, user_ids: list[int], item_ids: list[int] | None, data: dict) -> None:
        self.conn.execute("INSERT INTO fraud_flags (kind, user_ids, item_ids, data, created_at) VALUES (?,?,?,?,?)",
                          (kind, json.dumps(user_ids), json.dumps(item_ids or []), json.dumps(data), self.clock()))
