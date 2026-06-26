"""Handlers: /wallet (deposit wallet) + /setkamino (Kamino leverage wallet)."""
from __future__ import annotations
import re

from telethon import TelegramClient, events

from db.queries import upsert_user, get_user

_BASE58_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def _valid(addr: str) -> bool:
    return bool(_BASE58_RE.match(addr))


def register(client: TelegramClient, db) -> None:

    @client.on(events.NewMessage(pattern=r"^/wallet(?:\s+(.+))?$"))
    async def wallet_handler(event):
        """Register the wallet you send USDC deposits FROM."""
        args = event.pattern_match.group(1)

        if not args or not args.strip():
            user_row = await get_user(db, event.sender_id)
            current = user_row["solana_wallet"] if user_row and user_row["solana_wallet"] else None
            if current:
                await event.respond(
                    f"🔑 *Your deposit wallet*\n\n`{current}`\n\n"
                    f"To update: `/wallet <new address>`\n"
                    f"For Kamino leverage wallet: `/setkamino <address>`",
                    parse_mode="md",
                )
            else:
                await event.respond(
                    "🔑 *Register your deposit wallet*\n\n"
                    "This is the wallet you send USDC FROM when depositing\\.\n"
                    "`/wallet <your Solana address>`",
                    parse_mode="md",
                )
            raise events.StopPropagation

        addr = args.strip()
        if not _valid(addr):
            await event.respond(
                "❌ Invalid Solana address \\(32–44 base58 chars\\)\\.",
                parse_mode="md",
            )
            raise events.StopPropagation

        await upsert_user(db, event.sender_id, solana_wallet=addr)
        await event.respond(
            f"✅ *Deposit wallet set*\n\n`{addr}`\n\n"
            f"Deposits sent from this address will be auto\\-detected\\.\n"
            f"For Kamino leverage: `/setkamino <your Kamino wallet>`",
            parse_mode="md",
        )
        raise events.StopPropagation

    @client.on(events.NewMessage(pattern=r"^/setkamino(?:\s+(.+))?$"))
    async def setkamino_handler(event):
        """Register the wallet that holds your Kamino lending position."""
        args = event.pattern_match.group(1)

        if not args or not args.strip():
            user_row = await get_user(db, event.sender_id)
            current = user_row["kamino_wallet"] if user_row and user_row["kamino_wallet"] else None
            if current:
                await event.respond(
                    f"⚡ *Your Kamino wallet*\n\n`{current}`\n\n"
                    f"To update: `/setkamino <new address>`",
                    parse_mode="md",
                )
            else:
                await event.respond(
                    "⚡ *Set your Kamino wallet*\n\n"
                    "This is the wallet you deposited collateral on app\\.kamino\\.finance FROM\\.\n"
                    "`/setkamino <your Kamino wallet address>`",
                    parse_mode="md",
                )
            raise events.StopPropagation

        addr = args.strip()
        if not _valid(addr):
            await event.respond(
                "❌ Invalid Solana address \\(32–44 base58 chars\\)\\.",
                parse_mode="md",
            )
            raise events.StopPropagation

        await upsert_user(db, event.sender_id, kamino_wallet=addr)
        await event.respond(
            f"✅ *Kamino wallet set*\n\n`{addr}`\n\n"
            f"Use /leverage to check your Kamino position\\.",
            parse_mode="md",
        )
        raise events.StopPropagation
