"""Коллекция: список, карточка, продажа системе с отменой, замок, витрина, дубли."""

from __future__ import annotations

import datetime as dt
import json

from .. import rules
from .core import GameError


class Inventory:
    def inventory(self, user_id: int, state: str | None = None, tier: str | None = None, q: str | None = None,
                  sort: str = "new") -> dict:
        with self.tx():
            self._sync_player(user_id)
        p = self._player(user_id)
        rows = self.conn.execute("SELECT * FROM items WHERE owner=? AND state!='gone' ORDER BY id DESC", (user_id,)).fetchall()
        items = [self.item_view(r) for r in rows]
        total = sum(i["price_cents"] for i in items)
        week_ago = (self.now_msk() - dt.timedelta(days=7)).strftime("%Y-%m-%d")
        before = 0
        for r in rows:
            key = rules.price_key(r["wear"], bool(r["stattrak"]))
            old = self.conn.execute("SELECT cents FROM price_daily WHERE catalog_id=? AND key=? AND day<=? ORDER BY day DESC LIMIT 1",
                                    (r["catalog_id"], key, week_ago)).fetchone()
            before += old["cents"] if old else self.catalog.price(r["catalog_id"], r["wear"], bool(r["stattrak"]))
        if state:
            items = [i for i in items if (i["state"] == state) or (state == "locked" and i["locked"])
                     or (state == "overflow" and i["overflow_until"])]
        if tier:
            items = [i for i in items if i["tier"] == tier]
        if q:
            items = [i for i in items if q.lower() in i["name"].lower()]
        growth = lambda i: (i["price_cents"] - i["drop_price_cents"]) / max(1, i["drop_price_cents"])  # noqa: E731
        keyf = {"new": lambda i: -i["id"], "price": lambda i: -i["price_cents"],
                "tier": lambda i: (-rules.rank(i["tier"]), -i["price_cents"]), "growth": lambda i: -growth(i)}.get(sort)
        if keyf:
            items.sort(key=keyf)
        main, over = self._used_slots(user_id)
        return {"items": items, "summary": {"used": main, "total": self._slots(p), "overflow": over,
                                            "value_cents": total, "week_change_cents": total - before}}

    def item_card(self, user_id: int, item_id: int) -> dict:
        row = self._item(item_id)
        if not row or row["owner"] != user_id:
            raise GameError("NOT_FOUND", "Предмет не найден.")
        view = self.item_view(row)
        key = rules.price_key(row["wear"], bool(row["stattrak"]))
        since = (self.now_msk() - dt.timedelta(days=30)).strftime("%Y-%m-%d")
        chart = [{"day": r["day"], "cents": r["cents"]} for r in self.conn.execute(
            "SELECT day, cents FROM price_daily WHERE catalog_id=? AND key=? AND day>=? ORDER BY day", (row["catalog_id"], key, since))]
        events = self.conn.execute("SELECT * FROM item_events WHERE item_id=? ORDER BY id", (item_id,)).fetchall()
        owners = []
        for e in events:
            if e["kind"] in ("created", "owner"):
                owners.append({"ts": e["ts"], "kind": e["kind"], "data": json.loads(e["data"])})
        view.update({
            "chart": chart,
            "history": [{"ts": e["ts"], "kind": e["kind"]} for e in events],
            "owners": len({o["data"].get("to", user_id) for o in owners}) or 1,
        })
        return view

    # --- продажа системе ------------------------------------------------------------

    def sell(self, user_id: int, item_ids: list[int], confirmed: bool = False, idem_key: str | None = None) -> dict:
        return self.idempotent(user_id, idem_key, "sell", lambda: self._sell(user_id, item_ids, confirmed))

    def _sell(self, user_id: int, item_ids: list[int], confirmed: bool, reason: str = "sell_system") -> dict:
        rows = self._owned(user_id, item_ids)
        if any(rules.at_least(r["tier"], "legendary") for r in rows) and not confirmed:
            total = sum(self.item_view(r)["sell_cents"] for r in rows)
            raise GameError("CONFIRM_REQUIRED", f"Продать за {self.money(user_id, total)}? Отменить можно 10 минут.",
                            total_cents=total)
        total = 0
        states = {}
        for r in rows:
            price = self.item_view(r)["sell_cents"]
            total += price
            states[r["id"]] = {"hold_until": r["hold_until"], "overflow_until": r["overflow_until"]}
            self._update_item(r, "sold_system", user_id, {"price": price}, state="gone", owner=None)
        now = self.clock()
        undo_until = now + rules.CFG["undo_sell"]["minutes"] * 60
        cur = self.conn.execute(
            "INSERT INTO system_sales (user_id, item_ids, states, total, created_at, undo_until) VALUES (?,?,?,?,?,?)",
            (user_id, json.dumps(item_ids), json.dumps(states), total, now, undo_until))
        balance = self._ledger(user_id, total, reason, "sale", cur.lastrowid)
        if reason == "sell_system" and self._has_duplicates_sold(user_id, rows):
            self._quest_progress(user_id, "sell1", 1)
        return {"sale_id": cur.lastrowid, "total_cents": total, "undo_until": undo_until, "balance_cents": balance}

    def _has_duplicates_sold(self, user_id: int, rows) -> bool:
        for r in rows:
            if self.conn.execute("SELECT 1 FROM items WHERE owner=? AND state!='gone' AND catalog_id=?",
                                 (user_id, r["catalog_id"])).fetchone():
                return True
        return any(sum(1 for x in rows if x["catalog_id"] == r["catalog_id"]) > 1 for r in rows)

    def undo_sale(self, user_id: int, sale_id: int, idem_key: str | None = None) -> dict:
        def run():
            sale = self.conn.execute("SELECT * FROM system_sales WHERE id=? AND user_id=?", (sale_id, user_id)).fetchone()
            if not sale or sale["undone_at"]:
                raise GameError("NOT_FOUND", "Продажа не найдена или уже отменена.")
            if sale["undo_until"] < self.clock():
                raise GameError("NOT_ALLOWED_YET", "Отменить продажу можно только в течение 10 минут.")
            if self._counter(user_id, "undo") >= rules.CFG["undo_sell"]["per_day"]:
                raise GameError("DAILY_LIMIT", "Отмен продажи сегодня больше нельзя: лимит 5 в сутки.")
            self._charge(user_id, sale["total"], "sell_undo", "sale", sale_id)
            states = json.loads(sale["states"])
            for item_id in json.loads(sale["item_ids"]):
                row = self._item(item_id)
                st = states.get(str(item_id), {})
                self._update_item(row, "undo", user_id, state="free", owner=user_id,
                                  hold_until=st.get("hold_until"), overflow_until=st.get("overflow_until"))
            self.conn.execute("UPDATE system_sales SET undone_at=? WHERE id=?", (self.clock(), sale_id))
            self._bump_counter(user_id, "undo")
            return {"sale_id": sale_id, "restored": json.loads(sale["item_ids"]),
                    "balance_cents": self._player(user_id)["balance"]}

        return self.idempotent(user_id, idem_key, "undo", run)

    def duplicates(self, user_id: int) -> list[int]:
        """«Продать дубли»: остаётся лучший экземпляр каждого вида; под замком не продаются."""
        rows = self.conn.execute(
            "SELECT * FROM items WHERE owner=? AND state='free' AND locked=0 AND frozen=0", (user_id,)).fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["catalog_id"], r["wear"], r["stattrak"]), []).append(r)
        out = []
        for g in groups.values():
            g.sort(key=lambda r: (r["float_value"] if r["float_value"] is not None else 1, -r["id"]))
            out += [r["id"] for r in g[1:]]
        return out

    def duplicates_quote(self, user_id: int) -> dict:
        ids = self.duplicates(user_id)
        total = sum(self.item_view(self._item(i))["sell_cents"] for i in ids)
        return {"item_ids": ids, "total_cents": total}

    def sell_cheaper(self, user_id: int, max_cents: int) -> dict:
        rows = self.conn.execute(
            "SELECT * FROM items WHERE owner=? AND state='free' AND locked=0 AND frozen=0", (user_id,)).fetchall()
        ids = [r["id"] for r in rows if self.item_view(r)["price_cents"] < max_cents]
        total = sum(self.item_view(self._item(i))["sell_cents"] for i in ids)
        return {"item_ids": ids, "total_cents": total}

    def sell_group_quote(self, user_id: int, group: str) -> dict:
        """Быстрая продажа: все скины или одна редкость (sell_quick.groups). Переполнение продаётся;
        под замком, на рынке, в обмене, в дуэли, на удержании и замороженные — нет."""
        groups = {g["id"] for g in rules.CFG["sell_quick"]["groups"]}
        if group not in groups:
            raise GameError("BAD_REQUEST", "Такой группы нет.")
        now = self.clock()
        rows = self.conn.execute(
            "SELECT * FROM items WHERE owner=? AND state='free' AND locked=0 AND frozen=0 ORDER BY id", (user_id,)).fetchall()
        rows = [r for r in rows if (group == "all" or r["tier"] == group) and not (r["hold_until"] and r["hold_until"] > now)]
        return {"group": group, "item_ids": [r["id"] for r in rows], "count": len(rows),
                "total_cents": sum(self.item_view(r)["sell_cents"] for r in rows),
                "confirm_required": any(rules.at_least(r["tier"], "legendary") for r in rows)}

    def sell_groups(self, user_id: int) -> list[dict]:
        """Кнопки быстрой продажи с числом предметов и суммой."""
        out = []
        for g in rules.CFG["sell_quick"]["groups"]:
            q = self.sell_group_quote(user_id, g["id"])
            out.append({"id": g["id"], "label": g["label"], "count": q["count"], "total_cents": q["total_cents"]})
        return out

    # --- замок и витрина --------------------------------------------------------------

    def lock(self, user_id: int, item_id: int, locked: bool, confirm: bool = False) -> dict:
        with self.tx():
            row = self._owned(user_id, [item_id], allow_locked=True)[0]
            if not locked and row["locked"] and not confirm:
                raise GameError("CONFIRM_REQUIRED", "Подтверди снятие замка.")
            if bool(row["locked"]) != locked:
                self._update_item(row, "lock" if locked else "unlock", user_id, locked=int(locked))
        return {"id": item_id, "locked": locked}

    def set_showcase(self, user_id: int, item_ids: list[int]) -> dict:
        if len(item_ids) > 3:
            raise GameError("BAD_REQUEST", "На витрине до 3 предметов.")
        with self.tx():
            if item_ids:
                rows = self.conn.execute(
                    f"SELECT id FROM items WHERE owner=? AND state!='gone' AND id IN ({','.join('?' * len(item_ids))})",
                    (user_id, *item_ids)).fetchall()
                if len(rows) != len(set(item_ids)):
                    raise GameError("NOT_FOUND", "Предмет не найден в коллекции.")
            self.conn.execute("UPDATE players SET showcase=? WHERE user_id=?", (json.dumps(item_ids), user_id))
        return {"showcase": item_ids}

    def showcase_view(self, user_id: int) -> list[dict]:
        p = self._player(user_id)
        out = []
        for i in json.loads(p["showcase"]):
            row = self._item(i)
            if row and row["owner"] == user_id and row["state"] != "gone":
                out.append(self.item_view(row))
        return out
