"""Miscellaneous callback handlers — browse, deposit, leaderboard, howto, withdraw, create_pool."""
from __future__ import annotations

from telethon import TelegramClient, events

from bot.buttons import pool_list, deposit_amounts
from db import queries

HOWTO_TEXT = (
    "⚽ *How WorldPool works*\n\n"
    "1️⃣ *Deposit* USDC via Solana Pay\n"
    "2️⃣ *Browse* open matches \\+ live odds\n"
    "3️⃣ *Pick* your outcome — home / draw / away\n"
    "4️⃣ *Stake* your amount, confirm your bet\n"
    "5️⃣ Watch *live updates* as the match plays\n"
    "6️⃣ Winners settle *automatically* via Anchor\n\n"
    "🔐 USDC held in escrow by operator\\.\n"
    "📡 Scores \\+ odds from TxLINE live feed\\.\n"
    "🏆 Winnings credited to your WorldPool balance\\."
)


def register(client: TelegramClient, db) -> None:

    @client.on(events.CallbackQuery(data=b"browse"))
    async def browse_handler(event):
        pools = await queries.get_open_pools(db)
        await event.answer()
        if not pools:
            await client.send_message(
                event.chat_id,
                "⏳ *No matches open yet*\n\n"
                "Upcoming World Cup pools will appear here\\. Check back soon\\!\n\n"
                "_Tip: use /pool for the same list\\._",
                parse_mode="md",
            )
            return
        await client.send_message(
            event.chat_id,
            "🏟️ *Matches* — ⏳ upcoming · 🔴 live\nSelect to bet:",
            buttons=pool_list(pools),
            parse_mode="md",
        )

    @client.on(events.CallbackQuery(data=b"deposit"))
    async def deposit_callback_handler(event):
        await event.answer()
        await client.send_message(
            event.chat_id,
            "💳 *Deposit to WorldPool*\n\nChoose an amount:",
            buttons=deposit_amounts(),
            parse_mode="md",
        )

    @client.on(events.CallbackQuery(data=b"leaderboard"))
    async def leaderboard_handler(event):
        rows = await queries.get_leaderboard(db)
        await event.answer()
        if not rows:
            await client.send_message(
                event.chat_id,
                "🏆 No settled bets yet\\. Be the first to win\\!",
                parse_mode="md",
            )
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 *Leaderboard — Top Bettors*\n"]
        for i, row in enumerate(rows):
            medal = medals[i] if i < 3 else f"{i + 1}\\."
            name = f"@{row['tg_username']}" if row["tg_username"] else f"Player{str(row['tg_user_id'])[-4:]}"
            sign = "+" if row["profit"] >= 0 else ""
            lines.append(f"{medal} {name} · *{sign}${row['profit']:.2f}* \\({row['total_bets']} bets\\)")
        await client.send_message(event.chat_id, "\n".join(lines), parse_mode="md")

    @client.on(events.CallbackQuery(data=b"howto"))
    async def howto_handler(event):
        await event.answer()
        await client.send_message(event.chat_id, HOWTO_TEXT, parse_mode="md")

    @client.on(events.CallbackQuery(data=b"withdraw"))
    async def withdraw_handler(event):
        user = await event.get_sender()
        user_row = await queries.get_user(db, user.id)
        balance = user_row["usdc_balance"] if user_row else 0.0
        await event.answer()
        await client.send_message(
            event.chat_id,
            f"💸 *Withdraw*\n\n"
            f"Available: *${balance:.2f} USDC*\n\n"
            f"Reply with your Solana wallet address to request a withdrawal\\.\n"
            f"Operator processes within 24h\\.",
            parse_mode="md",
        )

    @client.on(events.CallbackQuery(data=b"create_pool"))
    async def create_pool_callback(event):
        await event.answer()
        await client.send_message(
            event.chat_id,
            "➕ *Create a pool*\n\n"
            "Operator command:\n"
            "`/createpool <Home> vs <Away> <fixture\\_id>`\n\n"
            "Example:\n"
            "`/createpool Brazil vs Argentina wc2026\\_001`",
            parse_mode="md",
        )
