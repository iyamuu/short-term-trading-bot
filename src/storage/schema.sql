-- SQLite schema for in-flight state + log outbox.
-- Short-term state lives in SQLite; historical/analytical logs live in parquet.
-- The outbox is the bridge: close/record operations commit here transactionally,
-- then a separate flush writes idempotently to parquet.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Open position(s) in one-way mode. The "at most one open position" invariant is
-- enforced in StateStore.open_trade() (not by the PK, which is per trade_id).
-- contracts is ALWAYS positive, even for SHORT; direction is carried by `side`.
CREATE TABLE IF NOT EXISTS open_position (
    trade_id        TEXT PRIMARY KEY,
    signal_id       TEXT,
    side            TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    entry_time      TEXT,
    entry_price     REAL,
    contracts       REAL NOT NULL CHECK (contracts >= 0),
    initial_sl      REAL,
    initial_tp1     REAL,
    initial_tp2     REAL,
    final_sl        REAL,
    hit_tp1         INTEGER DEFAULT 0,
    hit_tp2         INTEGER DEFAULT 0,
    moved_to_breakeven INTEGER DEFAULT 0,
    regime          TEXT,
    setup_name      TEXT,
    confidence      REAL,
    ml_score        REAL,
    risk_size_multiplier REAL,
    payload_json    TEXT
);

-- Working orders for the open trade (entry/TP1/TP2/SL).
CREATE TABLE IF NOT EXISTS open_orders (
    order_role      TEXT NOT NULL CHECK (order_role IN ('ENTRY','TP1','TP2','SL')),
    trade_id        TEXT NOT NULL,
    order_id        TEXT,
    client_oid      TEXT,
    reduce_only     INTEGER DEFAULT 0,
    status          TEXT,
    PRIMARY KEY (trade_id, order_role)
);

-- Running high/low for the open trade (MAE/MFE source).
CREATE TABLE IF NOT EXISTS mae_mfe_track (
    trade_id        TEXT PRIMARY KEY,
    side            TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    initial_sl      REAL NOT NULL,
    running_high    REAL,
    running_low     REAL,
    seen            INTEGER DEFAULT 0
);

-- Risk accounting (Phase 4 器を先行用意).
CREATE TABLE IF NOT EXISTS risk_session (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    day_key             TEXT,
    week_key            TEXT,
    daily_pnl           REAL DEFAULT 0,
    weekly_pnl          REAL DEFAULT 0,
    session_pnl         REAL DEFAULT 0,
    consecutive_losses  INTEGER DEFAULT 0,
    cooldown_until      TEXT,
    manual_stop         INTEGER DEFAULT 0
);

-- Append-only outbox. close/record write here inside the same transaction that
-- mutates state; outbox.flush() drains it to parquet idempotently.
CREATE TABLE IF NOT EXISTS log_outbox (
    outbox_id       TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('TRADE','ORDER','FILL','CANDIDATE')),
    payload_json    TEXT NOT NULL,
    created_at      TEXT,
    flushed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_unflushed ON log_outbox (flushed_at);
