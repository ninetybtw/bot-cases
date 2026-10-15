"""Админка: экономика, версии конфига, предметы (поиск, история, заморозка, откат), баны, антифрод, возвраты."""

from __future__ import annotations

import datetime as dt
import json

from .. import odds, rules
from .core import MSK, GameError

FAUCETS = {"start", "quest", "wheel_daily", "wheel_weekly", "referral", "raffle", "duel_win", "upgrade_parts", "sell_system",
           "overflow_sale"}
SINKS = {"spin", "upgrade_stake", "piggy_tax", "sell_undo"}
TRANSFERS = {"market_buy", "market_sale", "order_reserve", "order_release", "admin_rollback"}


class Admin:
    def _audit(self, admin_id: int, action: str, target: dict, before=None, after=None) -> None:
        self.conn.execute(
            "INSERT INTO admin_audit (admin_id, action, target, before, after, created_at) VALUES (?,?,?,?,?,?)",
            (admin_id, action, json.dumps(target, ensure_ascii=False), json.dumps(before, ensure_ascii=False, default=str),
             json.dumps(after, ensure_ascii=False, default=str), self.clock()))

    def economy(self, days: int = 14) -> dict:
        since = self.clock() - days * 86400
        rows = self.conn.execute("SELECT ts, delta, reason FROM ledger WHERE ts>=?", (since,)).fetchall()
        by_day: dict[str, dict] = {}
        for r in rows:
            day = dt.datetime.fromtimestamp(r["ts"], MSK).strftime("%Y-%m-%d")
            d = by_day.setdefault(day, {"day": day, "faucet": 0, "sink": 0, "market_fee": 0, "reasons": {}})
            d["reasons"][r["reason"]] = d["reasons"].get(r["reason"], 0) + r["delta"]
            if r["reason"] in TRANSFERS:
                d["market_fee"] -= r["delta"]  # покупатель минус продавец = комиссия
            elif r["delta"] > 0:
                d["faucet"] += r["delta"]
            else:
                d["sink"] -= r["delta"]
        out = sorted(by_day.values(), key=lambda d: d["day"])
        for d in out:
            d["sink"] += max(0, d["market_fee"])
            active = self.conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM ledger WHERE ts>=? AND ts<?",
                (dt.datetime.fromisoformat(d["day"]).replace(tzinfo=MSK).timestamp(),
                 (dt.datetime.fromisoformat(d["day"]) + dt.timedelta(days=1)).replace(tzinfo=MSK).timestamp())).fetchone()[0]
            d["active"] = active
            free = d["faucet"] - d["reasons"].get("sell_system", 0) - d["reasons"].get("upgrade_parts", 0)
            d["faucet_per_active_cents"] = round(free / active) if active else 0
            d["paid_spins_per_active"] = round(d["faucet_per_active_cents"] / self.catalog.unit_cents, 2)
        alert = len(out) >= 14 and all(d["faucet"] > d["sink"] * 1.1 for d in out[-14:])
        banners = {r["banner_id"]: r["n"] for r in self.conn.execute(
            "SELECT banner_id, COUNT(*) AS n FROM banner_clicks WHERE ts>=? GROUP BY banner_id", (since,))}
        return {"days": out, "alert": alert, "rtp": self.catalog.rtps(), "usd_rub": self.catalog.usd_rub,
                "unit_cents": self.catalog.unit_cents, "banner_clicks": banners}

    # --- конфиг -----------------------------------------------------------------------

    def configs(self) -> dict:
        rows = self.conn.execute("SELECT id, version, active, created_by, created_at FROM config_versions ORDER BY id DESC").fetchall()
        return {"active": rules.VERSION, "versions": [dict(r) for r in rows], "config": rules.CFG}

    def check_config(self, cfg: dict) -> list[str]:
        return odds.validate_config(cfg, self.catalog)

    def save_config(self, admin_id: int, cfg: dict, activate: bool = False) -> dict:
        errors = self.check_config(cfg)
        if errors:
            raise GameError("BAD_REQUEST", "Конфиг не прошёл проверку: " + "; ".join(errors), errors=errors)
        version = f"{cfg.get('version', '?')}#{rules.config_hash(cfg)}"
        with self.tx():
            if not self.conn.execute("SELECT 1 FROM config_versions WHERE version=?", (version,)).fetchone():
                self.conn.execute("INSERT INTO config_versions (version, body, created_by, created_at) VALUES (?,?,?,?)",
                                  (version, json.dumps(cfg, ensure_ascii=False), admin_id, self.clock()))
            if activate:
                self.conn.execute("UPDATE config_versions SET active=(version=?)", (version,))
                self._audit(admin_id, "config_activate", {"version": version}, rules.VERSION, version)
                rules.activate(cfg)
        return {"version": version, "active": activate}

    def diff_config(self, cfg: dict) -> dict:
        def flat(d, prefix=""):
            out = {}
            for k, v in d.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, dict):
                    out.update(flat(v, key))
                else:
                    out[key] = v
            return out

        a, b = flat(rules.CFG), flat(cfg)
        return {"changed": {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)},
                "errors": self.check_config(cfg)}

    # --- предметы и пользователи --------------------------------------------------------

    def admin_item(self, item_id: int) -> dict:
        row = self._item(item_id)
        if not row:
            raise GameError("NOT_FOUND", "Предмет не найден.")
        events = [dict(e) for e in self.conn.execute("SELECT * FROM item_events WHERE item_id=? ORDER BY id", (item_id,))]
        drop = self.conn.execute("SELECT * FROM drops WHERE id=?", (row["origin_ref"],)).fetchone() if row["origin_ref"] else None
        return {"item": {**dict(row), **self.item_view(row)}, "events": events, "drop": dict(drop) if drop else None}

    def freeze(self, admin_id: int, item_id: int, frozen: bool) -> dict:
        with self.tx():
            row = self._item(item_id)
            if not row:
                raise GameError("NOT_FOUND", "Предмет не найден.")
            self._update_item(row, "admin_freeze" if frozen else "admin_unfreeze", admin_id, frozen=int(frozen))
            self._audit(admin_id, "item_freeze", {"item_id": item_id}, bool(row["frozen"]), frozen)
        return {"id": item_id, "frozen": frozen}

    def rollback_sale(self, admin_id: int, sale_id: int) -> dict:
        """Откат продажи системе: предметы возвращаются, монеты списываются. История остаётся в журналах."""
        with self.tx():
            sale = self.conn.execute("SELECT * FROM system_sales WHERE id=?", (sale_id,)).fetchone()
            if not sale or sale["undone_at"]:
                raise GameError("NOT_FOUND", "Продажа не найдена или уже отменена.")
            uid = sale["user_id"]
            balance = self._player(uid)["balance"]
            take = min(balance, sale["total"])
            self._ledger(uid, -take, "admin_rollback", "sale", sale_id)
            for item_id in json.loads(sale["item_ids"]):
                row = self._item(item_id)
                self._update_item(row, "admin_rollback", admin_id, {"sale": sale_id}, state="free", owner=uid)
            self.conn.execute("UPDATE system_sales SET undone_at=? WHERE id=?", (self.clock(), sale_id))
            self._audit(admin_id, "sale_rollback", {"sale_id": sale_id}, {"total": sale["total"]}, {"charged": take})
        return {"sale_id": sale_id, "charged_cents": take}

    def ban(self, admin_id: int, user_id: int, banned: bool, reason: str = "") -> dict:
        with self.tx():
            self.conn.execute("UPDATE users SET is_banned=?, ban_reason=? WHERE id=?", (int(banned), reason, user_id))
            self._audit(admin_id, "ban" if banned else "unban", {"user_id": user_id}, None, reason)
        return {"user_id": user_id, "banned": banned}

    def fraud(self) -> dict:
        rows = self.conn.execute("SELECT * FROM fraud_flags WHERE status='open' ORDER BY id DESC LIMIT 200").fetchall()
        return {"flags": [{**dict(r), "user_ids": json.loads(r["user_ids"]), "item_ids": json.loads(r["item_ids"] or "[]"),
                           "data": json.loads(r["data"])} for r in rows]}

    def resolve_flag(self, admin_id: int, flag_id: int) -> dict:
        with self.tx():
            self.conn.execute("UPDATE fraud_flags SET status='resolved' WHERE id=?", (flag_id,))
            self._audit(admin_id, "fraud_resolve", {"flag_id": flag_id})
        return {"id": flag_id, "status": "resolved"}

    def payments(self) -> dict:
        rows = self.conn.execute("SELECT * FROM star_payments ORDER BY id DESC LIMIT 200").fetchall()
        return {"payments": [dict(r) for r in rows]}

    def admin_user(self, user_id: int) -> dict:
        u = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            raise GameError("NOT_FOUND", "Игрок не найден.")
        p = self.conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
        ledger = [dict(r) for r in self.conn.execute("SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))]
        sales = [dict(r) for r in self.conn.execute("SELECT * FROM system_sales WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,))]
        return {"user": dict(u), "player": dict(p), "ledger": ledger, "sales": sales}
