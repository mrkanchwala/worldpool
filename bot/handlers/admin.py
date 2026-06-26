"""Admin commands — pool creation (operator only)."""
from __future__ import annotations
import os
import re

from telethon import TelegramClient, events

from db import queries

ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))

# /createpool Brazil vs Argentina wc2026_001 [2026-07-09T20:00]
_CREATE_PATTERN = re.compile(
    r"^/createpool\s+(.+?)\s+vs\s+(.+?)\s+(\S+)(?:\s+(.+))?$",
    re.IGNORECASE,
)


def register(client: TelegramClient, db) -> None:

    @client.on(events.NewMessage(pattern=r"^/createpool"))
    async def createpool_handler(event):
        user = await event.get_sender()
        if ADMIN_TG_ID and user.id != ADMIN_TG_ID:
            await event.respond("🚫 Operator command only\\.", parse_mode="md")
            raise events.StopPropagation

        m = _CREATE_PATTERN.match(event.raw_text.strip())
        if not m:
            await event.respond(
                "Usage: `/createpool <Home> vs <Away> <fixture_id> [kickoff]`\n\n"
                "Example:\n`/createpool Brazil vs Argentina wc2026\\_001 2026\\-07\\-09T20:00`",
                parse_mode="md",
            )
            raise events.StopPropagation

        home_team = m.group(1).strip()
        away_team = m.group(2).strip()
        fixture_id = m.group(3).strip()
        kickoff = m.group(4).strip() if m.group(4) else None

        await queries.upsert_user(db, user.id, getattr(user, "username", None))

        pool_id = await queries.create_pool(
            db,
            fixture_id=fixture_id,
            competition_id="wc2026",
            home_team=home_team,
            away_team=away_team,
            creator_tg_id=user.id,
            kickoff_time=kickoff,
            home_odds=2.00,
            away_odds=2.50,
            draw_odds=3.20,
        )

        await event.respond(
            f"✅ *Pool created*\n\n"
            f"🏟️ {home_team} vs {away_team}\n"
            f"🆔 Pool: `{pool_id}`\n"
            f"🔖 Fixture: `{fixture_id}`\n"
            f"⏰ Kickoff: {kickoff or 'TBD'}\n"
            f"📊 Opening odds: 2\\.00 / 3\\.20 / 2\\.50\n\n"
            f"Users can now bet via /pool",
            parse_mode="md",
        )
        raise events.StopPropagation

    @client.on(events.NewMessage(pattern=r"^/pools"))
    async def list_pools_handler(event):
        user = await event.get_sender()
        if ADMIN_TG_ID and user.id != ADMIN_TG_ID:
            raise events.StopPropagation
        pools = await queries.get_open_pools(db)
        if not pools:
            await event.respond("No open pools\\.", parse_mode="md")
            raise events.StopPropagation
        lines = ["📋 *Open pools:*\n"]
        for p in pools:
            lines.append(f"• `{p['pool_id']}` — {p['home_team']} vs {p['away_team']} \\(fixture: {p['fixture_id']}\\)")
        await event.respond("\n".join(lines), parse_mode="md")
        raise events.StopPropagation
