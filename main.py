"""WorldPool — entry point. Single asyncio event loop for bot + TxLINE streams."""
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # MUST precede txline.* imports — they read TXLINE_BASE_URL at import time

from telethon import TelegramClient

from db.schema import init_db
from txline.sse import TxLINEStreamer
from txline.parser import ScoreEvent, OddsEvent
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
logger = logging.getLogger("worldpool")

TG_API_ID = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
TG_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.devnet.solana.com")
ODDS_SHIFT_THRESHOLD = 5.0  # % shift to trigger alert

# Shared in-memory state
odds_cache: dict[str, OddsEvent] = {}  # fixture_id → latest OddsEvent
prev_odds_cache: dict[str, OddsEvent] = {}  # for shift detection
custom_stake_state: dict[int, dict] = {}  # tg_user_id → {pool_id, outcome} for custom-amount flow


async def main() -> None:
    db = await init_db(os.getenv("DB_PATH", "worldpool.db"))
    logger.info("Database initialized")

    client = TelegramClient("worldpool_bot", TG_API_ID, TG_API_HASH)
    await client.start(bot_token=TG_BOT_TOKEN)
    logger.info("Telegram bot started")

    # Register all handlers
    h_start.register(client, db)
    h_deposit.register(client, db, RPC_URL)
    h_pool.register(client, db, odds_cache, custom_stake_state)
    h_positions.register(client, db)
    h_misc.register(client, db)
    h_admin.register(client, db)
    h_wallet.register(client, db)
    h_leverage.register(client, db)

    # TxLINE stream callbacks
    streamer = TxLINEStreamer()

    @streamer.on_score
    async def handle_score(event: ScoreEvent) -> None:
        pool = await queries.get_pool_by_fixture(db, event.fixture_id)
        if not pool:
            return

        if event.event_type == "full_time":
            await queries.close_betting(db, event.fixture_id)
            payouts, total_pool = await queries.mark_positions_settled(db, pool["pool_id"], event.result)
            await queries.settle_pool(db, pool["pool_id"], event.result)

            # Credit winners + attach usernames for alert
            if payouts:
                winner_ids = [p["tg_user_id"] for p in payouts]
                usernames = await queries.get_users_by_ids(db, winner_ids)
                for p in payouts:
                    p["username"] = usernames.get(p["tg_user_id"], "")
                    await queries.credit_balance(db, p["tg_user_id"], p["payout"])

            chats = await queries.get_subscribed_chats(db, pool["pool_id"])
            msg = fulltime_alert(event, payouts, total_pool)
            for chat_id in chats:
                await client.send_message(chat_id, msg, parse_mode="md")

            # Repayment reminder — notify users with open Kamino leverage positions
            all_participants = list({p["tg_user_id"] for p in payouts})
            # Also fetch losers from settled positions
            settled = await queries.get_pool_positions(db, pool["pool_id"])
            for pos in settled:
                uid = pos["tg_user_id"]
                if uid not in all_participants:
                    all_participants.append(uid)
            for uid in all_participants:
                open_borrows = await queries.get_open_leverage_positions(db, uid)
                if open_borrows:
                    total_owed = sum(b["borrow_amount"] for b in open_borrows)
                    await client.send_message(
                        uid,
                        f"⚠️ *Repay your Kamino loan*\n\n"
                        f"You have an open Kamino borrow of *${total_owed:.2f} USDC*\\.\n"
                        f"Repay on [app.kamino.finance](https://app.kamino.finance/lending) "
                        f"to avoid liquidation\\.",
                        parse_mode="md",
                        link_preview=False,
                    )

        elif event.event_type == "half_time":
            await queries.close_betting(db, event.fixture_id)
            chats = await queries.get_subscribed_chats(db, pool["pool_id"])
            msg = halftime_alert(event)
            for chat_id in chats:
                await client.send_message(chat_id, msg, parse_mode="md")

        elif event.event_type == "goal":
            odds = odds_cache.get(event.fixture_id)
            chats = await queries.get_subscribed_chats(db, pool["pool_id"])
            msg = goal_alert(event, odds)
            for chat_id in chats:
                await client.send_message(chat_id, msg, parse_mode="md")

    @streamer.on_odds
    async def handle_odds(event: OddsEvent) -> None:
        prev = odds_cache.get(event.fixture_id)
        odds_cache[event.fixture_id] = event

        if prev and prev.home_odds > 0:
            shift = abs((event.home_odds - prev.home_odds) / prev.home_odds) * 100
            if shift >= ODDS_SHIFT_THRESHOLD:
                pool = await queries.get_pool_by_fixture(db, event.fixture_id)
                if pool:
                    chats = await queries.get_subscribed_chats(db, pool["pool_id"])
                    direction = "↑" if event.home_odds > prev.home_odds else "↓"
                    label = f"{pool['home_team']} vs {pool['away_team']}"
                    msg = odds_shift_alert(label, pool["home_team"], prev.home_odds, event.home_odds, shift)
                    for chat_id in chats:
                        await client.send_message(chat_id, msg, parse_mode="md")

    logger.info("Starting bot + TxLINE streams")
    await asyncio.gather(
        client.run_until_disconnected(),
        streamer.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
