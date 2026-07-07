"""Admin commands — pool creation (operator only)."""
from __future__ import annotations
import os
import re

from telethon import TelegramClient, events

from db import queries
from bot.alerts import fulltime_alert
from txline.parser import ScoreEvent

ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))
if not ADMIN_TG_ID:
    raise RuntimeError(
        "ADMIN_TG_ID env var must be set — all admin commands (/settle, /createpool) are unprotected without it"
    )

# /createpool Brazil vs Argentina wc2026_001 [2026-07-09T20:00]
_CREATE_PATTERN = re.compile(
    r"^/createpool\s+(.+?)\s+vs\s+(.+?)\s+(\S+)(?:\s+(.+))?$",
    re.IGNORECASE,
)

# /settle <pool_id> <home|draw|away>
_SETTLE_PATTERN = re.compile(r"^/settle\s+(\S+)\s+(home|draw|away)$", re.IGNORECASE)


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

    @client.on(events.NewMessage(pattern=r"^/settle"))
    async def settle_handler(event):
        """Manual settlement — admin only. Mirrors the TxLINE full_time path for
        demos when live SSE scores aren't available. Same DB writes + payout logic."""
        user = await event.get_sender()
        if ADMIN_TG_ID and user.id != ADMIN_TG_ID:
            await event.respond("🚫 Operator command only\\.", parse_mode="md")
            raise events.StopPropagation

        m = _SETTLE_PATTERN.match(event.raw_text.strip())
        if not m:
            await event.respond(
                "Usage: `/settle <pool_id> <home|draw|away>`", parse_mode="md"
            )
            raise events.StopPropagation

        pool_id = m.group(1).strip()
        outcome = m.group(2).strip().lower()

        pool = await queries.get_pool(db, pool_id)
        if not pool:
            await event.respond("Pool not found\\.", parse_mode="md")
            raise events.StopPropagation
        if pool["status"] == "settled":
            await event.respond("Pool already settled\\.", parse_mode="md")
            raise events.StopPropagation

        # Same sequence as main.py handle_score full_time branch
        await queries.close_betting(db, pool["fixture_id"])
        payouts, losses, total_pool = await queries.mark_positions_settled(db, pool_id, outcome)
        await queries.settle_pool(db, pool_id, outcome)

        all_ids = [p["tg_user_id"] for p in payouts] + [p["tg_user_id"] for p in losses]
        usernames = await queries.get_users_by_ids(db, all_ids)
        for p in payouts:
            p["username"] = usernames.get(p["tg_user_id"], "")
            await queries.credit_balance(db, p["tg_user_id"], p["payout"])
        for p in losses:
            p["username"] = usernames.get(p["tg_user_id"], "")

        # Synthetic score reflecting the declared outcome (for the alert card)
        scoreline = {"home": (1, 0), "away": (0, 1), "draw": (1, 1)}[outcome]
        score_event = ScoreEvent(
            fixture_id=pool["fixture_id"],
            competition_id=pool["competition_id"],
            home_team=pool["home_team"],
            away_team=pool["away_team"],
            home_score=scoreline[0],
            away_score=scoreline[1],
            game_phase=5,
            minute=90,
            event_type="full_time",
            event_id=f"manual_{pool_id}",
            raw={"manual": True},
        )
        msg = fulltime_alert(score_event, payouts, total_pool)
        chats = await queries.get_subscribed_chats(db, pool_id)
        for chat_id in chats:
            await client.send_message(chat_id, msg, parse_mode="md")

        await event.respond(
            f"✅ Settled `{pool_id}` → *{outcome}*\\. "
            f"Pool ${total_pool:.2f}, {len(payouts)} winner\\(s\\) paid\\.",
            parse_mode="md",
        )
        raise events.StopPropagation
