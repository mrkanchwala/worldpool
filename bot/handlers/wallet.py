"""Handlers: /wallet (Kamino/leverage wallet) + /setkamino (Kamino leverage wallet).

/wallet no longer identifies deposits — deposit identity is each user's own
dedicated address (see bot/deposit_wallet.py). It's kept only as the wallet
used to look up / borrow against a Kamino lending position when /setkamino
hasn't been set separately."""
from __future__ import annotations
import re

from telethon import TelegramClient, events

from bot.dm import send_private
from db.queries import upsert_user, get_user

_BASE58_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def _valid(addr: str) -> bool:
    return bool(_BASE58_RE.match(addr))


def mask_address(addr: str) -> str:
    """Shorten a Solana address for display (e.g. in a recorded demo) — the
    full value stays in the DB and is still usable, just not shown in chat."""
    if len(addr) <= 8:
        return addr
    return f"{addr[:4]}...{addr[-4:]}"


def register(client: TelegramClient, db) -> None:

    @client.on(events.NewMessage(pattern=r"^/wallet(?:\s+(.+))?$"))
    async def wallet_handler(event):
        """Register a fallback Kamino-lookup wallet. Not used for deposits —
        use /deposit for your personal deposit address instead."""
        args = event.pattern_match.group(1)

        if not args or not args.strip():
            user_row = await get_user(db, event.sender_id)
            current = user_row["solana_wallet"] if user_row and user_row["solana_wallet"] else None
            if current:
                await send_private(
                    client, event, event.sender_id,
                    f"🔑 *Your Kamino\\-lookup wallet*\n\n`{mask_address(current)}`\n\n"
                    f"To update: `/wallet <new address>`\n"
                    f"Dedicated Kamino wallet instead: `/setkamino <address>`\n\n"
                    f"_Note: this is not your deposit address — use /deposit for that\\._",
                    parse_mode="md",
                )
            else:
                await event.respond(
                    "🔑 *Set a Kamino\\-lookup wallet*\n\n"
                    "Used only for /leverage — the wallet you deposited collateral "
                    "on app\\.kamino\\.finance FROM\\.\n"
                    "`/wallet <your Solana address>`\n\n"
                    "_Not related to /deposit — that generates your own dedicated address\\._",
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
        await send_private(
            client, event, event.sender_id,
            f"✅ *Kamino\\-lookup wallet set*\n\n`{mask_address(addr)}`\n\n"
            f"Used for /leverage when no dedicated /setkamino wallet is set\\.\n"
            f"For deposits, use /deposit instead\\.",
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
                await send_private(
                    client, event, event.sender_id,
                    f"⚡ *Your Kamino wallet*\n\n`{mask_address(current)}`\n\n"
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
        await send_private(
            client, event, event.sender_id,
            f"✅ *Kamino wallet set*\n\n`{mask_address(addr)}`\n\n"
            f"Use /leverage to check your Kamino position\\.",
            parse_mode="md",
        )
        raise events.StopPropagation
