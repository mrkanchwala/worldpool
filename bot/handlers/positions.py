"""Handlers: /mypositions, /leaderboard."""
from __future__ import annotations

from telethon import TelegramClient, events

from bot.buttons import positions_menu
from bot.dm import send_private
from db.queries import get_user_positions, get_user


def register(client: TelegramClient, db) -> None:
    @client.on(events.NewMessage(pattern=r"^/mypositions"))
    async def positions_handler(event):
        user = await event.get_sender()
        user_row = await get_user(db, user.id)
        balance = user_row["usdc_balance"] if user_row else 0.0
        positions = await get_user_positions(db, user.id)

        if not positions:
            await send_private(
                client, event, event.sender_id,
                f"📊 *Your Positions*\nBalance: *${balance:.2f}* available\n\nNo positions yet\\. Use /pool to bet\\!",
                buttons=positions_menu(),
                parse_mode="md",
            )
            return

        lines = [f"📊 *Your Positions*\nBalance: *${balance:.2f}* available\n"]
        total_pl = 0.0
        for pos in positions:
            status_emoji = {"open": "⏳", "won": "✅", "lost": "🏳️"}.get(pos["status"], "•")
            lines.append(
                f"{status_emoji} *{pos['home_team']} vs {pos['away_team']}*\n"
                f"   {pos['outcome'].capitalize()} @ {pos['odds_at_time']:.2f} · ${pos['amount_usdc']:.2f}"
            )
            if pos["status"] == "won" and pos["payout_amount"]:
                profit = pos["payout_amount"] - pos["amount_usdc"]
                total_pl += profit
                lines[-1] += f" → *${pos['payout_amount']:.2f}*"
            elif pos["status"] == "lost":
                total_pl -= pos["amount_usdc"]
            lines.append("")

        pl_sign = "🟢" if total_pl >= 0 else "🔴"
        lines.append(f"Total P&L: *{'+' if total_pl >= 0 else ''}{total_pl:.2f}* {pl_sign}")

        await send_private(
            client, event, event.sender_id,
            "\n".join(lines),
            buttons=positions_menu(),
            parse_mode="md",
        )
        raise events.StopPropagation

    @client.on(events.CallbackQuery(data=b"positions"))
    async def positions_callback(event):
        await event.answer()
        # Reuse the positions handler logic via fake event
        fake = await event.get_message()
        user = await event.get_sender()
        user_row = await get_user(db, user.id)
        balance = user_row["usdc_balance"] if user_row else 0.0
        positions = await get_user_positions(db, user.id)
        msg = f"📊 *Your Positions*\nBalance: *${balance:.2f}*\n\n"
        if not positions:
            msg += "No positions yet\\."
        else:
            for pos in positions[:5]:
                emoji = {"open": "⏳", "won": "✅", "lost": "🏳️"}.get(pos["status"], "•")
                msg += f"{emoji} {pos['home_team']} vs {pos['away_team']} · {pos['outcome']} ${pos['amount_usdc']:.0f}\n"
        await send_private(client, event, event.sender_id, msg, buttons=positions_menu(), parse_mode="md")
