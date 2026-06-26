"""Handler: /start — welcome message + main menu."""
from telethon import TelegramClient, events
from bot.buttons import main_menu
from db.queries import upsert_user


def register(client: TelegramClient, db) -> None:
    @client.on(events.NewMessage(pattern=r"^/start"))
    async def start_handler(event):
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
