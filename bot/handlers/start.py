"""Handler: /start — welcome message + main menu."""
import time

from telethon import TelegramClient, events
from bot.buttons import main_menu
from db.queries import upsert_user

_DEBOUNCE_SECS = 3  # collapses double-sends regardless of cause (duplicate
# Telegram update delivery, not just a duplicate event id — an id-only guard
# didn't catch a real double-reply seen in production 2026-07-07).


def register(client: TelegramClient, db) -> None:
    _last_start_ts: dict[int, float] = {}

    @client.on(events.NewMessage(pattern=r"^/start"))
    async def start_handler(event):
        now = time.monotonic()
        last = _last_start_ts.get(event.sender_id, 0)
        if now - last < _DEBOUNCE_SECS:
            raise events.StopPropagation
        _last_start_ts[event.sender_id] = now

        user = await event.get_sender()
        await upsert_user(db, user.id, getattr(user, "username", None))
        await event.respond(
            "⚽ *Welcome to WorldPool*\n\n"
            "Self-running World Cup pools\\. Deposit once\\. Bet on any match\\. "
            "Solana settles winners automatically\\.\n\n"
            "📡 Powered by TxLINE live data\n"
            "🔐 USDC escrow on Solana\n"
            "🏆 104 matches · open now",
            buttons=main_menu(),
            parse_mode="md",
        )
        raise events.StopPropagation
