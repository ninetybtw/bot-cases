"""SQLite: схема и подключение. Все денежные суммы в центах игровых долларов."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,           -- Telegram ID
    name            TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    balance         INTEGER NOT NULL DEFAULT 0,    -- центы игровых $
    energy          INTEGER NOT NULL DEFAULT 0,    -- бесплатные спины по таймеру (до 3)
    energy_ts       REAL NOT NULL,
    bonus_spins     INTEGER NOT NULL DEFAULT 0,    -- спины из колеса, рефералов
    since_epic      INTEGER NOT NULL DEFAULT 0,
    since_legendary INTEGER NOT NULL DEFAULT 0,
    upgrade_bonus   INTEGER NOT NULL DEFAULT 0,
    total_spins     INTEGER NOT NULL DEFAULT 0,
    server_seed     TEXT NOT NULL,
    client_seed     TEXT NOT NULL,
    nonce           INTEGER NOT NULL DEFAULT 0,
    referrer        INTEGER,
    referral_paid   INTEGER NOT NULL DEFAULT 0,
    showcase        TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner       INTEGER NOT NULL REFERENCES users(id),
    catalog_id  TEXT NOT NULL,
    tier        TEXT NOT NULL,
    wear        TEXT,
    float_value REAL,
    pattern     INTEGER NOT NULL,
    stattrak    INTEGER NOT NULL DEFAULT 0,
    origin      TEXT NOT NULL,                     -- case:<id>, upgrade, contract, wheel…
    drop_price  INTEGER NOT NULL,                  -- цена на момент получения
    state       TEXT NOT NULL DEFAULT 'free',      -- free | locked | gone
    created_at  REAL NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_owner_state ON items(owner, state);
CREATE INDEX IF NOT EXISTS items_owner_tier ON items(owner, tier);
CREATE INDEX IF NOT EXISTS items_catalog ON items(catalog_id);
CREATE INDEX IF NOT EXISTS items_feed ON items(tier, created_at);

-- Журнал по предметам: записи только добавляются.
CREATE TABLE IF NOT EXISTS item_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL,
    ts       REAL NOT NULL,
    kind     TEXT NOT NULL,
    data     TEXT NOT NULL DEFAULT '{}'
);

-- Журнал денег: каждое изменение баланса.
CREATE TABLE IF NOT EXISTS ledger (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    ts       REAL NOT NULL,
    delta    INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    ref      TEXT
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, ts);

-- Идемпотентность: повтор запроса с тем же ключом возвращает тот же ответ.
CREATE TABLE IF NOT EXISTS requests (
    idem_key   TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    response   TEXT NOT NULL
);

-- Каждый бросок генератора: для разбора споров и проверки честности.
CREATE TABLE IF NOT EXISTS rolls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    seed_hash   TEXT NOT NULL,
    client_seed TEXT NOT NULL,
    nonce       INTEGER NOT NULL,
    odds_version TEXT NOT NULL,
    result      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seeds_revealed (
    user_id     INTEGER NOT NULL,
    server_seed TEXT NOT NULL,
    seed_hash   TEXT NOT NULL,
    client_seed TEXT NOT NULL,
    last_nonce  INTEGER NOT NULL,
    revealed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily (
    user_id   INTEGER NOT NULL,
    day       TEXT NOT NULL,
    wheel     INTEGER NOT NULL DEFAULT 0,
    spins     INTEGER NOT NULL DEFAULT 0,
    sells     INTEGER NOT NULL DEFAULT 0,
    upgrades  INTEGER NOT NULL DEFAULT 0,
    claimed   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, day)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE: запись блокируется сразу, состояние предмета и баланс меняются атомарно."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
