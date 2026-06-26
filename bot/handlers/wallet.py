"""Handler: /wallet — register Solana wallet address for Kamino leverage."""
from __future__ import annotations
import re

from telethon import TelegramClient, events

from db.queries import upsert_user, get_user

# Base58 alphabet — Solana addresses are 32–44 base58 chars
_BASE58_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')


def _is_valid_solana_address(addr: str) -> bool:
    return bool(_BASE58_RE.match(addr))


def register(client: TelegramClient, db) -> None:

    @client.on(events.NewMessage(pattern=r"^/wallet(?:\s+(.+))?$"))
    async def wallet_handler(event):
        args = event.pattern_match.group(1)

        if not args or not args.strip():
            user_row = await get_user(db, event.sender_id)
            current = user_row["solana_wallet"] if user_row and user_row["solana_wallet"] else None
            if current:
                await event.respond(
                    f"🔑 *Your registered wallet*\n\n`{current}`\n\n"
                    f"To update it: `/wallet <new address>`",
                    parse_mode="md",
                )
            else:
                await event.respond(
                    "🔑 *Register your Solana wallet*\n\n"
                    "Required for Kamino leverage. Usage:\n"
                    "`/wallet <your Solana address>`\n\n"
                    "Example:\n"
                    "`/wallet 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM`",
                    parse_mode="md",
                )
            raise events.StopPropagation

        addr = args.strip()
        if not _is_valid_solana_address(addr):
            await event.respond(
                "❌ Invalid Solana address\\. Must be 32–44 base58 characters\\.\n\n"
                "Example: `/wallet 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM`",
                parse_mode="md",
            )
            raise events.StopPropagation

        await upsert_user(db, event.sender_id, solana_wallet=addr)
        await event.respond(
            f"✅ *Wallet registered*\n\n`{addr}`\n\n"
            f"Use /leverage to borrow USDC via Kamino and amplify your bets\\.",
            parse_mode="md",
        )
        raise events.StopPropagation
