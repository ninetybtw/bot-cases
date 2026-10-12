"""SQLite: схема и подключение. Деньги в копейках игрового рубля (целые), звёзды целыми.

Схема повторяет reference/schema.sql (PostgreSQL, версия 1.2) в объёме бота скинов без Standoff 2,
хардкора и кланов. Журналы ledger, item_events, drops, rolls и admin_audit только дописываются:
UPDATE и DELETE в них запрещены триггерами.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,            -- Telegram ID
    name             TEXT NOT NULL DEFAULT '',
    username         TEXT,
    lang             TEXT NOT NULL DEFAULT 'ru',
    country          TEXT NOT NULL DEFAULT 'RU',
    country_confirmed INTEGER NOT NULL DEFAULT 0,
    country_set_at   REAL,
    referrer         INTEGER REFERENCES users(id),
    is_banned        INTEGER NOT NULL DEFAULT 0,
    ban_reason       TEXT,
    created_at       REAL NOT NULL,
    last_seen        REAL
);

CREATE TABLE IF NOT EXISTS players (
    user_id          INTEGER PRIMARY KEY REFERENCES users(id),
    balance          INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    star_credit      INTEGER NOT NULL DEFAULT 0,
    xp               INTEGER NOT NULL DEFAULT 0,
    level            INTEGER NOT NULL DEFAULT 1,
    energy           INTEGER NOT NULL DEFAULT 0,
    energy_ts        REAL NOT NULL,
    energy_notified  INTEGER NOT NULL DEFAULT 1,
    bonus_spins      INTEGER NOT NULL DEFAULT 0,      -- спины из колеса, рефералов, розыгрыша
    paid_attempts    INTEGER NOT NULL DEFAULT 0 CHECK (paid_attempts BETWEEN 0 AND 100),
    coin_day         TEXT, coin_count  INTEGER NOT NULL DEFAULT 0,
    lvl_day          TEXT, lvl_count   INTEGER NOT NULL DEFAULT 0,
    since_epic       INTEGER NOT NULL DEFAULT 0,
    since_legendary  INTEGER NOT NULL DEFAULT 0,
    luck_mult        REAL,
    luck_left        INTEGER NOT NULL DEFAULT 0,
    upgrade_bonus    REAL NOT NULL DEFAULT 0,         -- п.п. к шансу следующего апгрейда
    upg_day          TEXT, upg_count   INTEGER NOT NULL DEFAULT 0,
    undo_day         TEXT, undo_count  INTEGER NOT NULL DEFAULT 0,
    duel_day         TEXT, duel_count  INTEGER NOT NULL DEFAULT 0,
    vip_day          TEXT,
    slots            INTEGER NOT NULL DEFAULT 200 CHECK (slots <= 1000),
    vip_until        REAL,
    total_spins      INTEGER NOT NULL DEFAULT 0,
    showcase         TEXT NOT NULL DEFAULT '[]',
    frame            TEXT,
    last_drop        TEXT,                           -- последний ответ спина: показать после обрыва анимации
    version          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS seeds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    server_seed TEXT NOT NULL,
    seed_hash   TEXT NOT NULL,
    client_seed TEXT NOT NULL,
    nonce       INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    revealed_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS seeds_one_active ON seeds(user_id) WHERE active = 1;

CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner          INTEGER REFERENCES users(id),
    catalog_id     TEXT NOT NULL,
    tier           TEXT NOT NULL,
    wear           TEXT,
    float_value    REAL,
    pattern        INTEGER NOT NULL,
    stattrak       INTEGER NOT NULL DEFAULT 0,
    state          TEXT NOT NULL DEFAULT 'free' CHECK (state IN ('free','market','trade','duel','gone')),
    locked         INTEGER NOT NULL DEFAULT 0,
    frozen         INTEGER NOT NULL DEFAULT 0,
    hold_until     REAL,
    overflow_until REAL,
    origin         TEXT NOT NULL,                  -- case:<id>, level:<id>, free, attempt, upgrade, contract, market, trade, wheel, raffle
    origin_ref     INTEGER,
    drop_price     INTEGER NOT NULL,
    created_at     REAL NOT NULL,
    version        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_owner_state ON items(owner, state);
CREATE INDEX IF NOT EXISTS items_catalog ON items(catalog_id);
CREATE INDEX IF NOT EXISTS items_feed ON items(tier, created_at);

CREATE TABLE IF NOT EXISTS item_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL,
    ts       REAL NOT NULL,
    kind     TEXT NOT NULL,
    actor    INTEGER,
    data     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS item_events_item ON item_events(item_id, ts);

CREATE TABLE IF NOT EXISTS drops (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    source         TEXT NOT NULL,                  -- free, coin, attempt
    case_id        TEXT,
    level          TEXT,
    count          INTEGER NOT NULL,
    cost           INTEGER NOT NULL DEFAULT 0,
    config_version TEXT NOT NULL,
    luck_mult      REAL,
    pity_applied   TEXT,
    seed_id        INTEGER NOT NULL,
    seed_hash      TEXT NOT NULL,
    client_seed    TEXT NOT NULL,
    nonce          INTEGER NOT NULL,
    counters       TEXT NOT NULL,                  -- счётчики гарантий до и после
    item_ids       TEXT NOT NULL,
    idem_key       TEXT,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS drops_user ON drops(user_id, created_at);

-- Все броски (спин, апгрейд, контракт, колесо, дуэль) для экрана «Честная игра».
CREATE TABLE IF NOT EXISTS rolls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    seed_id     INTEGER NOT NULL,
    nonce       INTEGER NOT NULL,
    numbers     INTEGER NOT NULL,                  -- сколько чисел u_i взято
    result      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rolls_user ON rolls(user_id, id);

CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    ts            REAL NOT NULL,
    delta         INTEGER NOT NULL,
    balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
    reason        TEXT NOT NULL,
    ref_type      TEXT,
    ref_id        TEXT
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, ts);
CREATE INDEX IF NOT EXISTS ledger_time ON ledger(ts);

CREATE TABLE IF NOT EXISTS idempotency (
    user_id    INTEGER NOT NULL,
    key        TEXT NOT NULL,
    endpoint   TEXT NOT NULL,
    response   TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS system_sales (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    item_ids   TEXT NOT NULL,
    states     TEXT NOT NULL,                      -- замок, удержание, переполнение до продажи
    total      INTEGER NOT NULL,
    created_at REAL NOT NULL,
    undo_until REAL NOT NULL,
    undone_at  REAL
);

CREATE TABLE IF NOT EXISTS upgrades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    stake      TEXT NOT NULL,
    stake_value INTEGER NOT NULL,
    target     TEXT NOT NULL,
    target_value INTEGER NOT NULL,
    chance     REAL NOT NULL,
    roll       REAL NOT NULL,
    success    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    CHECK (stake_value < target_value)
);

CREATE TABLE IF NOT EXISTS market_lots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    INTEGER NOT NULL REFERENCES items(id),
    seller     INTEGER NOT NULL,
    currency   TEXT NOT NULL DEFAULT 'coins',
    price      INTEGER NOT NULL CHECK (price > 0),
    fee_pct    REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',     -- active, sold, cancelled, expired
    buyer      INTEGER,
    sold_at    REAL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS market_one_active ON market_lots(item_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS market_browse ON market_lots(status, price);

CREATE TABLE IF NOT EXISTS buy_orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer      INTEGER NOT NULL,
    catalog_id TEXT NOT NULL,
    wear       TEXT,
    stattrak   INTEGER NOT NULL DEFAULT 0,
    max_price  INTEGER NOT NULL,
    reserved   INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',     -- active, filled, cancelled
    item_id    INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user  INTEGER NOT NULL,
    to_user    INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',    -- pending, accepted, declined, cancelled, expired
    value_from INTEGER NOT NULL,
    value_to   INTEGER NOT NULL,
    created_at REAL NOT NULL,
    closed_at  REAL,
    CHECK (from_user <> to_user)
);
CREATE TABLE IF NOT EXISTS trade_items (
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    item_id  INTEGER NOT NULL,
    side     TEXT NOT NULL CHECK (side IN ('from','to')),
    PRIMARY KEY (trade_id, item_id)
);

CREATE TABLE IF NOT EXISTS star_payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    product    TEXT NOT NULL,
    stars      INTEGER NOT NULL,
    payload    TEXT NOT NULL UNIQUE,
    charge_id  TEXT UNIQUE,
    status     TEXT NOT NULL DEFAULT 'invoiced',   -- invoiced, granted, refunded, failed
    granted    TEXT,
    created_at REAL NOT NULL,
    granted_at REAL,
    refunded_at REAL
);

CREATE TABLE IF NOT EXISTS user_cosmetics (
    user_id    INTEGER NOT NULL,
    code       TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS wheel_spins (
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    period     TEXT NOT NULL,
    sector     TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, kind, period)
);

CREATE TABLE IF NOT EXISTS quests (
    user_id    INTEGER NOT NULL,
    period     TEXT NOT NULL,                      -- 2026-10-12 или 2026-W42
    code       TEXT NOT NULL,
    target     INTEGER NOT NULL,
    progress   INTEGER NOT NULL DEFAULT 0,
    claimed_at REAL,
    PRIMARY KEY (user_id, period, code)
);

CREATE TABLE IF NOT EXISTS raffles (
    day        TEXT PRIMARY KEY,
    seed       TEXT NOT NULL,
    seed_hash  TEXT NOT NULL,
    prizes     TEXT NOT NULL,
    winners    TEXT,
    drawn_at   REAL
);
CREATE TABLE IF NOT EXISTS raffle_tickets (
    day     TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    tickets INTEGER NOT NULL CHECK (tickets > 0),
    PRIMARY KEY (day, user_id)
);

CREATE TABLE IF NOT EXISTS duels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    p1         INTEGER NOT NULL,
    p2         INTEGER,
    friend_id  INTEGER,
    p1_items   TEXT NOT NULL,
    p2_items   TEXT,
    p1_power   REAL,
    p2_power   REAL,
    winner     INTEGER,
    status     TEXT NOT NULL DEFAULT 'open',       -- open, done, cancelled, expired
    created_at REAL NOT NULL,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS referral_rewards (
    referrer     INTEGER NOT NULL,
    invitee      INTEGER NOT NULL UNIQUE,
    created_at   REAL NOT NULL,
    qualified_at REAL,
    PRIMARY KEY (referrer, invitee)
);
CREATE TABLE IF NOT EXISTS referral_tiers (
    referrer   INTEGER NOT NULL,
    tier       INTEGER NOT NULL,
    granted_at REAL NOT NULL,
    PRIMARY KEY (referrer, tier)
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at REAL NOT NULL,
    sent_at    REAL,
    skipped    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS notifications_queue ON notifications(sent_at, skipped);

CREATE TABLE IF NOT EXISTS fraud_flags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    user_ids   TEXT NOT NULL,
    item_ids   TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    before     TEXT,
    after      TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS price_daily (
    catalog_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    day        TEXT NOT NULL,
    cents      INTEGER NOT NULL,
    PRIMARY KEY (catalog_id, key, day)
);

CREATE TABLE IF NOT EXISTS config_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    version    TEXT NOT NULL UNIQUE,
    body       TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    name     TEXT PRIMARY KEY,
    last_run TEXT
);
"""

APPEND_ONLY = ("ledger", "item_events", "drops", "rolls", "admin_audit")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for table in APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} только дописывается'); END"
            )
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE: запись блокируется сразу, как SELECT … FOR UPDATE в Postgres."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
