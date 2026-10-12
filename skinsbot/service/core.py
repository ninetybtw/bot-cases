"""Общая основа игровых операций: ошибки, журналы, честный генератор, представление предмета."""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from .. import game, rules
from ..catalog import Catalog
from ..db import connect, transaction

MSK = ZoneInfo("Europe/Moscow")


class GameError(Exception):
    """Ошибка для игрока: code из docs/API.md, message готов для показа."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


class Core:
    def __init__(self, db_path: str, catalog: Catalog, clock=time.time):
        self.conn: sqlite3.Connection = connect(db_path)
        self.catalog = catalog
        self.clock = clock
        self._mutex = threading.RLock()
        self.on_notify = None  # функция отправки уведомлений, ставит бот
        row = self.conn.execute("SELECT body FROM config_versions WHERE active=1").fetchone()
        if row:  # версия, включённая в админке, главнее файла config/skins.json
            rules.activate(json.loads(row["body"]))

    # --- время -------------------------------------------------------------

    def now_msk(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.clock(), MSK)

    def today(self) -> str:
        return self.now_msk().strftime("%Y-%m-%d")

    def week(self) -> str:
        y, w, _ = self.now_msk().isocalendar()
        return f"{y}-W{w:02d}"

    # --- транзакции и идемпотентность --------------------------------------

    @contextmanager
    def tx(self):
        with self._mutex, transaction(self.conn):
            yield

    def idempotent(self, user_id: int, key: str | None, endpoint: str, fn):
        """Один POST — одна транзакция. Повтор с тем же Idempotency-Key возвращает сохранённый ответ."""
        with self.tx():
            if key:
                row = self.conn.execute(
                    "SELECT response FROM idempotency WHERE user_id=? AND key=?", (user_id, key)
                ).fetchone()
                if row:
                    return json.loads(row["response"])
            response = fn()
            if key:
                self.conn.execute(
                    "INSERT INTO idempotency (user_id, key, endpoint, response, created_at) VALUES (?,?,?,?,?)",
                    (user_id, key, endpoint, json.dumps(response, ensure_ascii=False), self.clock()),
                )
            return response

    # --- игрок -------------------------------------------------------------

    def _user(self, user_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise GameError("UNAUTHORIZED", "Профиль не найден. Открой бота заново.")
        if row["is_banned"]:
            raise GameError("UNAUTHORIZED", "Аккаунт заблокирован. Напиши в поддержку /paysupport.")
        return row

    def _player(self, user_id: int) -> sqlite3.Row:
        self._user(user_id)
        return self.conn.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()

    def money(self, user_id: int, cents: int) -> str:
        row = self.conn.execute("SELECT country FROM users WHERE id=?", (user_id,)).fetchone()
        return rules.format_money(cents, row["country"] if row else "RU")

    def _ledger(self, user_id: int, delta: int, reason: str, ref_type: str | None = None, ref_id=None) -> int:
        """Каждое изменение баланса — запись в ledger с балансом после операции."""
        if delta:
            self.conn.execute("UPDATE players SET balance=balance+?, version=version+1 WHERE user_id=?", (delta, user_id))
        balance = self.conn.execute("SELECT balance FROM players WHERE user_id=?", (user_id,)).fetchone()["balance"]
        self.conn.execute(
            "INSERT INTO ledger (user_id, ts, delta, balance_after, reason, ref_type, ref_id) VALUES (?,?,?,?,?,?,?)",
            (user_id, self.clock(), delta, balance, reason, ref_type, None if ref_id is None else str(ref_id)),
        )
        return balance

    def _charge(self, user_id: int, cents: int, reason: str, ref_type=None, ref_id=None) -> int:
        balance = self.conn.execute("SELECT balance FROM players WHERE user_id=?", (user_id,)).fetchone()["balance"]
        if balance < cents:
            need = cents - balance
            raise GameError("NOT_ENOUGH_COINS", f"Не хватает {self.money(user_id, need)}. Задания и колесо дадут монеты.",
                            need_cents=need)
        return self._ledger(user_id, -cents, reason, ref_type, ref_id)

    def _counter(self, user_id: int, prefix: str) -> int:
        """Дневной счётчик (coin, lvl, upg, undo, duel): сбрасывается с новым днём по Москве."""
        p = self.conn.execute(f"SELECT {prefix}_day AS d, {prefix}_count AS c FROM players WHERE user_id=?", (user_id,)).fetchone()
        return p["c"] if p["d"] == self.today() else 0

    def _bump_counter(self, user_id: int, prefix: str, n: int = 1) -> None:
        value = self._counter(user_id, prefix) + n
        self.conn.execute(f"UPDATE players SET {prefix}_day=?, {prefix}_count=? WHERE user_id=?", (self.today(), value, user_id))

    def is_vip(self, player: sqlite3.Row) -> bool:
        return bool(player["vip_until"] and player["vip_until"] > self.clock())

    # --- честный генератор ---------------------------------------------------

    def _seed(self, user_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM seeds WHERE user_id=? AND active=1", (user_id,)).fetchone()
        if row:
            return row
        server_seed = game.new_server_seed()
        self.conn.execute(
            "INSERT INTO seeds (user_id, server_seed, seed_hash, client_seed, created_at) VALUES (?,?,?,?,?)",
            (user_id, server_seed, game.seed_hash(server_seed), secrets.token_hex(8), self.clock()),
        )
        return self.conn.execute("SELECT * FROM seeds WHERE user_id=? AND active=1", (user_id,)).fetchone()

    def _rng(self, user_id: int) -> tuple[game.FairRng, sqlite3.Row]:
        seed = self._seed(user_id)
        self.conn.execute("UPDATE seeds SET nonce=nonce+1 WHERE id=?", (seed["id"],))
        return game.FairRng(seed["server_seed"], seed["client_seed"], seed["nonce"]), seed

    def _log_roll(self, user_id: int, kind: str, rng: game.FairRng, seed: sqlite3.Row, result: dict) -> None:
        self.conn.execute(
            "INSERT INTO rolls (user_id, ts, kind, seed_id, nonce, numbers, result) VALUES (?,?,?,?,?,?,?)",
            (user_id, self.clock(), kind, seed["id"], rng.nonce, rng.i, json.dumps(result, ensure_ascii=False)),
        )

    def _fair(self, seed: sqlite3.Row, nonce: int) -> dict:
        return {"server_seed_hash": seed["seed_hash"], "client_seed": seed["client_seed"], "nonce": nonce}

    # --- предметы ------------------------------------------------------------

    def _event(self, item_id: int, kind: str, actor: int | None = None, data: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO item_events (item_id, ts, kind, actor, data) VALUES (?,?,?,?,?)",
            (item_id, self.clock(), kind, actor, json.dumps(data or {}, ensure_ascii=False)),
        )

    def _insert_item(self, user_id: int, drop: game.Drop, origin: str, origin_ref=None, overflow: bool = False,
                     hold: bool = False) -> int:
        now = self.clock()
        overflow_until = now + rules.CFG["inventory"]["overflow_hours"] * 3600 if overflow else None
        hold_until = now + rules.CFG["market"]["hold_hours"] * 3600 if hold else None
        cur = self.conn.execute(
            "INSERT INTO items (owner, catalog_id, tier, wear, float_value, pattern, stattrak, origin, origin_ref,"
            " drop_price, created_at, overflow_until, hold_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, drop.catalog_id, drop.tier, drop.wear, drop.float_value, drop.pattern, int(drop.stattrak),
             origin, origin_ref, drop.price_cents, now, overflow_until, hold_until),
        )
        self._event(cur.lastrowid, "created", user_id, {"origin": origin, "overflow": overflow})
        return cur.lastrowid

    def _item(self, item_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def _update_item(self, row: sqlite3.Row, kind: str, actor: int | None, data: dict | None = None, **fields) -> None:
        """Смена состояния с проверкой версии: если предмет уже изменился, операция откатывается."""
        sets = ", ".join(f"{k}=?" for k in fields)
        cur = self.conn.execute(
            f"UPDATE items SET {sets}, version=version+1 WHERE id=? AND version=?",
            (*fields.values(), row["id"], row["version"]),
        )
        if cur.rowcount != 1:
            raise GameError("CONFLICT", "Предмет уже изменился. Обнови экран.")
        self._event(row["id"], kind, actor, {**(data or {}), **{k: v for k, v in fields.items()}})

    def _owned(self, user_id: int, item_ids: list[int], *, allow_locked=False, allow_hold=True,
               allow_overflow=True) -> list[sqlite3.Row]:
        if not item_ids or len(set(item_ids)) != len(item_ids):
            raise GameError("BAD_REQUEST", "Выбери предметы.")
        marks = ",".join("?" * len(item_ids))
        rows = self.conn.execute(f"SELECT * FROM items WHERE owner=? AND id IN ({marks})", (user_id, *item_ids)).fetchall()
        if len(rows) != len(item_ids) or any(r["state"] == "gone" for r in rows):
            raise GameError("NOT_FOUND", "Предмет не найден в коллекции.")
        now = self.clock()
        for r in rows:
            if r["frozen"]:
                raise GameError("ITEM_BUSY", "Предмет заморожен до проверки. Напиши в поддержку.")
            if r["state"] != "free":
                where = {"market": "на рынке", "trade": "в обмене", "duel": "в дуэли"}[r["state"]]
                raise GameError("ITEM_BUSY", f"Предмет сейчас {where}. Сначала сними его оттуда.")
            if r["locked"] and not allow_locked:
                raise GameError("ITEM_LOCKED", "Предмет под замком. Сначала сними замок.")
            if not allow_hold and r["hold_until"] and r["hold_until"] > now:
                raise GameError("ITEM_BUSY", "Предмет на удержании 24 часа после покупки или обмена.")
            if not allow_overflow and r["overflow_until"]:
                raise GameError("ITEM_BUSY", "Предмет в переполнении. Освободи место в коллекции.")
        order = {i: n for n, i in enumerate(item_ids)}
        return sorted(rows, key=lambda r: order[r["id"]])

    def item_view(self, row: sqlite3.Row) -> dict:
        it = self.catalog.items[row["catalog_id"]]
        price = game.item_price(it, row["wear"], bool(row["stattrak"]))
        now = self.clock()
        return {
            "id": row["id"],
            "catalog_id": row["catalog_id"],
            "name": it["name"],
            "weapon": it["weapon"],
            "finish": it["finish"],
            "tier": row["tier"],
            "game_rarity": it["game_rarity"],
            "image_url": it["image"],
            "wear": row["wear"],
            "float": row["float_value"],
            "pattern": row["pattern"],
            "stattrak": bool(row["stattrak"]),
            "price_cents": price,
            "sell_cents": rules.quick_sell(price),
            "drop_price_cents": row["drop_price"],
            "one_in": game.one_in(rules.case_odds().get(row["tier"], 0)),
            "origin": row["origin"],
            "created_at": row["created_at"],
            "state": row["state"],
            "locked": bool(row["locked"]),
            "frozen": bool(row["frozen"]),
            "hold_until": row["hold_until"] if row["hold_until"] and row["hold_until"] > now else None,
            "overflow_until": row["overflow_until"],
        }

    # --- уведомления -----------------------------------------------------------

    def _notify(self, user_id: int, kind: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO notifications (user_id, kind, text, created_at) VALUES (?,?,?,?)",
            (user_id, kind, text, self.clock()),
        )
