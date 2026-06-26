"""WorldPool DB queries — all CRUD in one module, aiosqlite, typed."""
from __future__ import annotations
import asyncio
import uuid
from typing import Optional

import aiosqlite

_balance_lock = asyncio.Lock()


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(db: aiosqlite.Connection, tg_user_id: int, tg_username: str | None = None, solana_wallet: str | None = None) -> None:
    await db.execute(
        """INSERT INTO users(tg_user_id, tg_username, solana_wallet)
           VALUES(?, ?, ?)
           ON CONFLICT(tg_user_id) DO UPDATE SET
             tg_username = COALESCE(excluded.tg_username, tg_username),
             solana_wallet = COALESCE(excluded.solana_wallet, solana_wallet)""",
        (tg_user_id, tg_username, solana_wallet),
    )
    await db.commit()


async def get_user(db: aiosqlite.Connection, tg_user_id: int) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT * FROM users WHERE tg_user_id = ?", (tg_user_id,)) as cur:
        return await cur.fetchone()


async def credit_balance(db: aiosqlite.Connection, tg_user_id: int, amount: float) -> None:
    await db.execute(
        "UPDATE users SET usdc_balance = usdc_balance + ? WHERE tg_user_id = ?",
        (amount, tg_user_id),
    )
    await db.commit()


async def deduct_balance(db: aiosqlite.Connection, tg_user_id: int, amount: float) -> bool:
    """Atomically deduct balance using asyncio lock. Returns False if insufficient funds."""
    async with _balance_lock:
        async with db.execute(
            "SELECT usdc_balance FROM users WHERE tg_user_id = ?", (tg_user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row or row["usdc_balance"] < amount:
            return False
        await db.execute(
            "UPDATE users SET usdc_balance = usdc_balance - ?, total_wagered = total_wagered + ? WHERE tg_user_id = ?",
            (amount, amount, tg_user_id),
        )
        await db.commit()
        return True


# ── Pools ─────────────────────────────────────────────────────────────────────

async def create_pool(
    db: aiosqlite.Connection,
    fixture_id: str,
    competition_id: str,
    home_team: str,
    away_team: str,
    creator_tg_id: int,
    kickoff_time: str | None = None,
    home_odds: float | None = None,
    away_odds: float | None = None,
    draw_odds: float | None = None,
) -> str:
    pool_id = str(uuid.uuid4())[:8]
    await db.execute(
        """INSERT INTO pools(pool_id, competition_id, fixture_id, home_team, away_team,
             creator_tg_id, kickoff_time, home_odds, away_odds, draw_odds)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (pool_id, competition_id, fixture_id, home_team, away_team,
         creator_tg_id, kickoff_time, home_odds, away_odds, draw_odds),
    )
    await db.commit()
    return pool_id


async def get_open_pools(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM pools WHERE status = 'open' ORDER BY created_at DESC LIMIT 10") as cur:
        return await cur.fetchall()


async def get_pool(db: aiosqlite.Connection, pool_id: str) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT * FROM pools WHERE pool_id = ?", (pool_id,)) as cur:
        return await cur.fetchone()


async def get_pool_by_fixture(db: aiosqlite.Connection, fixture_id: str) -> Optional[aiosqlite.Row]:
    async with db.execute("SELECT * FROM pools WHERE fixture_id = ? AND status IN ('open','betting_closed')", (fixture_id,)) as cur:
        return await cur.fetchone()


async def close_betting(db: aiosqlite.Connection, fixture_id: str) -> None:
    await db.execute(
        "UPDATE pools SET status = 'betting_closed' WHERE fixture_id = ? AND status = 'open'",
        (fixture_id,),
    )
    await db.commit()


async def settle_pool(db: aiosqlite.Connection, pool_id: str, winning_outcome: str, anchor_tx_sig: str | None = None) -> None:
    await db.execute(
        "UPDATE pools SET status = 'settled', winning_outcome = ?, anchor_tx_sig = ?, settled_at = datetime('now') WHERE pool_id = ?",
        (winning_outcome, anchor_tx_sig, pool_id),
    )
    await db.commit()


# ── Positions ─────────────────────────────────────────────────────────────────

async def place_position(
    db: aiosqlite.Connection,
    pool_id: str,
    tg_user_id: int,
    outcome: str,
    amount_usdc: float,
    odds_at_time: float,
) -> str:
    position_id = str(uuid.uuid4())[:8]
    potential_win = round(amount_usdc * odds_at_time, 2)
    await db.execute(
        """INSERT INTO positions(position_id, pool_id, tg_user_id, outcome, amount_usdc, odds_at_time, potential_win)
           VALUES(?,?,?,?,?,?,?)""",
        (position_id, pool_id, tg_user_id, outcome, amount_usdc, odds_at_time, potential_win),
    )
    await db.commit()
    return position_id


async def get_user_positions(db: aiosqlite.Connection, tg_user_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """SELECT p.*, pl.home_team, pl.away_team, pl.status as pool_status, pl.winning_outcome
           FROM positions p JOIN pools pl ON p.pool_id = pl.pool_id
           WHERE p.tg_user_id = ?
           ORDER BY p.created_at DESC LIMIT 20""",
        (tg_user_id,),
    ) as cur:
        return await cur.fetchall()


async def get_pool_positions(db: aiosqlite.Connection, pool_id: str) -> list[aiosqlite.Row]:
    async with db.execute("SELECT * FROM positions WHERE pool_id = ?", (pool_id,)) as cur:
        return await cur.fetchall()


async def mark_positions_settled(
    db: aiosqlite.Connection, pool_id: str, winning_outcome: str
) -> tuple[list[dict], float]:
    """Mark positions won/lost. Returns (payouts, total_pool_usdc)."""
    async with db.execute("SELECT * FROM positions WHERE pool_id = ?", (pool_id,)) as cur:
        positions = await cur.fetchall()

    winners = [p for p in positions if p["outcome"] == winning_outcome]
    losers = [p for p in positions if p["outcome"] != winning_outcome]
    total_pool = round(sum(p["amount_usdc"] for p in positions), 2)
    total_winning_stake = sum(p["amount_usdc"] for p in winners)

    payouts = []
    for pos in winners:
        payout = round((pos["amount_usdc"] / total_winning_stake) * total_pool, 2) if total_winning_stake > 0 else 0.0
        await db.execute(
            "UPDATE positions SET status = 'won', payout_amount = ?, settled_at = datetime('now') WHERE position_id = ?",
            (payout, pos["position_id"]),
        )
        payouts.append({"tg_user_id": pos["tg_user_id"], "payout": payout, "stake": pos["amount_usdc"]})

    for pos in losers:
        await db.execute(
            "UPDATE positions SET status = 'lost', payout_amount = 0, settled_at = datetime('now') WHERE position_id = ?",
            (pos["position_id"],),
        )

    await db.commit()
    return payouts, total_pool


# ── Chat subscriptions ─────────────────────────────────────────────────────────

async def subscribe_chat(db: aiosqlite.Connection, pool_id: str, chat_id: int) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO pool_chats(pool_id, chat_id) VALUES(?,?)",
        (pool_id, chat_id),
    )
    await db.commit()


async def get_subscribed_chats(db: aiosqlite.Connection, pool_id: str) -> list[int]:
    async with db.execute("SELECT chat_id FROM pool_chats WHERE pool_id = ?", (pool_id,)) as cur:
        rows = await cur.fetchall()
    return [r["chat_id"] for r in rows]


# ── Event deduplication ────────────────────────────────────────────────────────

async def is_processed(db: aiosqlite.Connection, event_id: str) -> bool:
    async with db.execute("SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)) as cur:
        return await cur.fetchone() is not None


async def mark_processed(db: aiosqlite.Connection, event_id: str) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO processed_events(event_id) VALUES(?)",
        (event_id,),
    )
    await db.commit()


# ── Leaderboard ────────────────────────────────────────────────────────────────

async def get_leaderboard(db: aiosqlite.Connection, limit: int = 10) -> list[aiosqlite.Row]:
    """Top users by net profit across all settled positions."""
    async with db.execute(
        """SELECT u.tg_username, u.tg_user_id,
                  ROUND(SUM(CASE
                    WHEN p.status = 'won' THEN p.payout_amount - p.amount_usdc
                    WHEN p.status = 'lost' THEN -p.amount_usdc
                    ELSE 0
                  END), 2) AS profit,
                  COUNT(*) AS total_bets
           FROM users u
           JOIN positions p ON u.tg_user_id = p.tg_user_id
           WHERE p.status IN ('won', 'lost')
           GROUP BY u.tg_user_id
           ORDER BY profit DESC
           LIMIT ?""",
        (limit,),
    ) as cur:
        return await cur.fetchall()


async def get_users_by_ids(db: aiosqlite.Connection, user_ids: list[int]) -> dict[int, str]:
    """Return {tg_user_id: tg_username} for the given IDs."""
    if not user_ids:
        return {}
    placeholders = ",".join("?" * len(user_ids))
    async with db.execute(
        f"SELECT tg_user_id, tg_username FROM users WHERE tg_user_id IN ({placeholders})",
        user_ids,
    ) as cur:
        rows = await cur.fetchall()
    return {r["tg_user_id"]: (r["tg_username"] or "") for r in rows}
