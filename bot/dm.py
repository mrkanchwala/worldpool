"""Route personal/sensitive content (addresses, balances, signable links) to a
DM instead of a group-visible reply. In the intended group-chat deployment,
anything sent via event.respond()/chat_id lands in front of every member —
that's how the CSO wallet-registration finding leaked addresses to the whole
group, and it also just looks messy for anything personal.
"""
from __future__ import annotations
import logging

from telethon import TelegramClient
from telethon.tl.types import User

logger = logging.getLogger(__name__)


async def send_private(client: TelegramClient, event, sender_id: int, text: str, **kwargs) -> None:
    """Send `text` as a DM to sender_id. If invoked from a group, leave a short
    pointer there; if the DM can't be delivered (user never started the bot
    privately), fall back to telling them so in the group."""
    if event.is_private:
        await client.send_message(sender_id, text, **kwargs)
        return
    try:
        await client.send_message(sender_id, text, **kwargs)
        await event.respond("📬 Sent you a DM with the details\\.", parse_mode="md")
    except Exception:
        logger.info("DM delivery failed for %s, falling back to group instruction", sender_id)
        me = await client.get_me()
        await event.respond(
            f"📬 I need to message you this privately — start a chat with me first: "
            f"https://t.me/{me.username}\\. Then run this again\\.",
            parse_mode="md",
        )


async def send_private_bg(client: TelegramClient, tg_user_id: int, fallback_chat_id: int,
                           text: str, **kwargs) -> None:
    """Same idea for background tasks with no live event (e.g. a deposit poller
    that started in a group). Falls back to the original chat if DM fails."""
    try:
        await client.send_message(tg_user_id, text, **kwargs)
    except Exception:
        logger.info("Background DM failed for %s, falling back to originating chat", tg_user_id)
        await client.send_message(fallback_chat_id, text, **kwargs)
