"""WorldPool SQLite schema — aiosqlite, WAL mode, single init."""
import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    tg_user_id      INTEGER PRIMARY KEY,
    tg_username     TEXT,
    solana_wallet   TEXT,
    usdc_balance    REAL    NOT NULL DEFAULT 0.0,
    total_wagered   REAL    NOT NULL DEFAULT 0.0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pools (
    pool_id         TEXT    PRIMARY KEY,
    competition_id  TEXT    NOT NULL,
    fixture_id      TEXT    NOT NULL,
    home_team       TEXT    NOT NULL,
    away_team       TEXT    NOT NULL,
    kickoff_time    TEXT,
    creator_tg_id   INTEGER NOT NULL REFERENCES users(tg_user_id),
    status          TEXT    NOT NULL DEFAULT 'open',  -- open | betting_closed | settling | settled
    winning_outcome TEXT,
    home_odds       REAL,
    away_odds       REAL,
    draw_odds       REAL,
    anchor_tx_sig   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    settled_at      TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    position_id     TEXT    PRIMARY KEY,
    pool_id         TEXT    NOT NULL REFERENCES pools(pool_id),
    tg_user_id      INTEGER NOT NULL REFERENCES users(tg_user_id),
    outcome         TEXT    NOT NULL,  -- home | away | draw
    amount_usdc     REAL    NOT NULL,
    odds_at_time    REAL    NOT NULL,
    potential_win   REAL    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open',  -- open | won | lost
    payout_amount   REAL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    settled_at      TEXT
);

CREATE TABLE IF NOT EXISTS pool_chats (
    pool_id         TEXT    NOT NULL REFERENCES pools(pool_id),
    chat_id         INTEGER NOT NULL,
    PRIMARY KEY (pool_id, chat_id)
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id        TEXT    PRIMARY KEY,
    processed_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settlements (
    pool_id         TEXT    PRIMARY KEY REFERENCES pools(pool_id),
    winning_outcome TEXT    NOT NULL,
    total_pool_usdc REAL    NOT NULL,
    anchor_tx_sig   TEXT,
    txline_proof    TEXT,
    settled_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leverage_positions (
    lev_id          TEXT    PRIMARY KEY,
    tg_user_id      INTEGER NOT NULL REFERENCES users(tg_user_id),
    borrow_amount   REAL    NOT NULL,
    kamino_tx_sig   TEXT,
    repaid          INTEGER NOT NULL DEFAULT 0,
    repay_tx_sig    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    repaid_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pools_fixture ON pools(fixture_id);
CREATE INDEX IF NOT EXISTS idx_positions_pool ON positions(pool_id);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(tg_user_id);
CREATE INDEX IF NOT EXISTS idx_pool_chats_pool ON pool_chats(pool_id);
CREATE INDEX IF NOT EXISTS idx_lev_user ON leverage_positions(tg_user_id);
"""


_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN kamino_wallet TEXT",
]


async def init_db(db_path: str = "worldpool.db") -> aiosqlite.Connection:
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    for sql in _MIGRATIONS:
        try:
            await conn.execute(sql)
        except Exception:
            pass  # column already exists
    await conn.commit()
    return conn
