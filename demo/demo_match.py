"""WorldPool DEMO harness — SEPARATE from production code.

Simulates a full match timeline (kickoff → odds shift → goals → half time →
full time → settlement + payout) so the end-to-end idea can be RECORDED today
WITHOUT waiting for a real World Cup match.

Production code (main.py, txline/sse.py) is NOT touched. This runs on a
separate demo DB (worldpool_demo.db) and only adds two demo-only commands.

Run (stop the systemd bot first — same token can't run twice):
    sudo systemctl stop quadriga-automations-worldpool   # on VPS, if running there
    python3 demo/demo_match.py                            # locally

Then in Telegram (@txodds_mkbot):
    /start
    /demobalance                       → credit yourself $100 demo USDC
    /createpool Brazil vs Argentina demo01
    /pool                              → bet on an outcome (try ✏️ Custom)
    /wallet <your real solana address> → register for Kamino lookup (real mainnet, real position)
    /leverage                          → borrow against your real Kamino position via WalletConnect
    /playmatch <pool_id> home          → watch the scripted match + payout land

Note: /wallet + /leverage are the SAME production handlers as main.py, not
demo-only — they require a real Solana wallet with a real Kamino position and
a real WalletConnect signature. Only the match/odds/settlement data is
synthetic; the borrow itself is real and must not be faked.
"""
from __future__ import annotations
import asyncio
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient, events

from db.schema import init_db
from txline.parser import ScoreEvent
from db import queries
from bot.alerts import goal_alert, halftime_alert, fulltime_alert, odds_shift_alert
import bot.handlers.start as h_start
import bot.handlers.deposit as h_deposit
import bot.handlers.pool as h_pool
import bot.handlers.positions as h_positions
import bot.handlers.misc as h_misc
import bot.handlers.admin as h_admin
import bot.handlers.wallet as h_wallet
import bot.handlers.leverage as h_leverage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worldpool-demo")

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))
DEMO_DB = "worldpool_demo.db"
BEAT = 1.8  # seconds between match events — paced for a clean screen recording

odds_cache: dict = {}
custom_stake_state: dict = {}

# Final scorelines per winning outcome (purely cosmetic for the alert cards)
_SCORELINE = {"home": (2, 1), "draw": (1, 1), "away": (1, 2)}
_PLAYMATCH = re.compile(r"^/playmatch\s+(\S+)\s+(home|draw|away)$", re.IGNORECASE)


def _score_event(pool, hs: int, as_: int, phase: int, minute: int, etype: str) -> ScoreEvent:
    return ScoreEvent(
        fixture_id=pool["fixture_id"],
        competition_id=pool["competition_id"],
        home_team=pool["home_team"],
        away_team=pool["away_team"],
        home_score=hs,
        away_score=as_,
        game_phase=phase,
        minute=minute,
        event_type=etype,
        event_id=f"demo_{pool['pool_id']}_{etype}_{minute}",
        raw={"demo": True},
    )


def register_demo(client: TelegramClient, db) -> None:

    @client.on(events.NewMessage(pattern=r"^/demobalance"))
    async def demobalance(event):
        """Demo-only: instantly credit the caller $100 so no real USDC is needed."""
        await queries.upsert_user(db, event.sender_id, getattr(await event.get_sender(), "username", None))
        await queries.credit_balance(db, event.sender_id, 100.0)
        user = await queries.get_user(db, event.sender_id)
        await event.respond(
            f"🎁 *Demo balance credited*\nBalance: *${user['usdc_balance']:.2f} USDC*\n"
            f"\\(demo only — no real funds\\)",
            parse_mode="md",
        )
        raise events.StopPropagation

    @client.on(events.NewMessage(pattern=r"^/playmatch"))
    async def playmatch(event):
        """Demo-only: drive a scripted match for <pool_id> ending in <home|draw|away>."""
        if ADMIN_TG_ID and event.sender_id != ADMIN_TG_ID:
            raise events.StopPropagation
        m = _PLAYMATCH.match(event.raw_text.strip())
        if not m:
            await event.respond("Usage: `/playmatch <pool_id> <home|draw|away>`", parse_mode="md")
            raise events.StopPropagation

        pool_id, result = m.group(1).strip(), m.group(2).lower()
        pool = await queries.get_pool(db, pool_id)
        if not pool:
            # Also try looking up by fixture_id (so /playmatch demo01 works too)
            async with db.execute(
                "SELECT * FROM pools WHERE fixture_id = ? AND status != 'settled' ORDER BY created_at DESC LIMIT 1",
                (pool_id,),
            ) as cur:
                pool = await cur.fetchone()
        if not pool:
            await event.respond("Pool not found\\.", parse_mode="md")
            raise events.StopPropagation
        # pool_id may still be the fixture_id typed by the admin (fallback path
        # above) — every DB call below needs the real pool_id, or settlement
        # silently updates 0 rows (no error, pool just never reaches 'settled').
        pool_id = pool["pool_id"]
        if pool["status"] == "settled":
            await event.respond("Pool already settled\\.", parse_mode="md")
            raise events.StopPropagation

        chats = await queries.get_subscribed_chats(db, pool_id) or [event.chat_id]

        async def broadcast(text: str) -> None:
            for chat_id in chats:
                await client.send_message(chat_id, text, parse_mode="md")

        label = f"{pool['home_team']} vs {pool['away_team']}"
        fhs, fas = _SCORELINE[result]

        # 1) Kickoff — include a pool summary so viewers see who's in before it closes
        positions_at_kickoff = await queries.get_pool_positions(db, pool_id)
        bettors_at_kickoff = len(set(p["tg_user_id"] for p in positions_at_kickoff))
        total_at_kickoff = sum(p["amount_usdc"] for p in positions_at_kickoff)
        await broadcast(
            f"⚽ *KICKOFF* — {label}\nLive via TxLINE \\(demo replay\\)\n\n"
            f"💰 Pool: ${total_at_kickoff:.2f} · {bettors_at_kickoff} bettors"
        )
        await asyncio.sleep(BEAT)

        # 2) Odds shift (drama)
        await broadcast(odds_shift_alert(label, pool["home_team"], 2.00, 1.75, -12))
        await asyncio.sleep(BEAT)

        # 3) First goal — toward the winning side (away if away wins, else home)
        if result == "away":
            await broadcast(goal_alert(_score_event(pool, 0, 1, 2, 23, "goal")))
        else:
            await broadcast(goal_alert(_score_event(pool, 1, 0, 2, 23, "goal")))
        await asyncio.sleep(BEAT)

        # 4) Half time — betting closes
        await queries.close_betting(db, pool["fixture_id"])
        ht = _score_event(pool, (0 if result == "away" else 1), (1 if result == "away" else 0), 3, 45, "half_time")
        await broadcast(halftime_alert(ht))
        await broadcast("🔒 *Betting closed* — match underway\\.")
        await asyncio.sleep(BEAT)

        # 5) Full time — settle + pay winners (same path as production handle_score)
        payouts, losses, total_pool = await queries.mark_positions_settled(db, pool_id, result)
        await queries.settle_pool(db, pool_id, result)
        all_ids = [p["tg_user_id"] for p in payouts] + [p["tg_user_id"] for p in losses]
        usernames = await queries.get_users_by_ids(db, all_ids)
        for p in payouts:
            p["username"] = usernames.get(p["tg_user_id"], "")
            await queries.credit_balance(db, p["tg_user_id"], p["payout"])
        for p in losses:
            p["username"] = usernames.get(p["tg_user_id"], "")

        ft = _score_event(pool, fhs, fas, 5, 90, "full_time")
        await broadcast(fulltime_alert(ft, payouts, total_pool, losses))
        raise events.StopPropagation


async def main() -> None:
    db = await init_db(DEMO_DB)
    logger.info("Demo database initialized (%s)", DEMO_DB)

    client = TelegramClient("worldpool_demo", TG_API_ID, TG_API_HASH)
    await client.start(bot_token=TG_BOT_TOKEN)
    logger.info("DEMO bot started — production code untouched, separate DB")

    # Real handlers — full betting UI works exactly as in production
    h_start.register(client, db)
    h_deposit.register(client, db, os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com"))
    h_pool.register(client, db, odds_cache, custom_stake_state)
    h_positions.register(client, db)
    h_misc.register(client, db)
    h_admin.register(client, db)
    h_wallet.register(client, db)
    h_leverage.register(client, db)
    # Demo-only commands
    register_demo(client, db)

    logger.info("Ready. In Telegram: /start → /demobalance → /createpool → /pool → /playmatch <id> <outcome>")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
