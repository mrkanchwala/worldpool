"""Handlers: /pool — browse matches, place bets, confirm positions."""
from __future__ import annotations
import asyncio

from telethon import TelegramClient, events

from bot.buttons import pool_list, outcome_buttons, stake_buttons, confirm_buttons
from bot.alerts import bet_confirmed
from db import queries


def register(client: TelegramClient, db, odds_cache: dict, custom_stake_state: dict | None = None) -> None:
    """odds_cache: {fixture_id: OddsEvent} updated by SSE stream.
    custom_stake_state: {user_id: {pool_id, outcome}} for custom amount flow.
    """
    if custom_stake_state is None:
        custom_stake_state = {}

    @client.on(events.NewMessage(pattern=r"^/pool"))
    async def pool_handler(event):
        pools = await queries.get_open_pools(db)
        if not pools:
            await event.respond("No open pools right now\\. Check back soon\\!", parse_mode="md")
            return
        await event.respond(
            "🏟️ *Open Matches*\nSelect a match to bet on:",
            buttons=pool_list(pools),
            parse_mode="md",
        )
        raise events.StopPropagation

    @client.on(events.CallbackQuery(pattern=rb"^pool_(.+)$"))
    async def pool_detail_handler(event):
        pool_id = event.data.decode().split("_", 1)[1]
        pool = await queries.get_pool(db, pool_id)
        if not pool:
            await event.answer("Pool not found.", alert=True)
            return

        odds = odds_cache.get(pool["fixture_id"])
        home_odds = odds.home_odds if odds else (pool["home_odds"] or 2.0)
        away_odds = odds.away_odds if odds else (pool["away_odds"] or 2.0)
        draw_odds = odds.draw_odds if odds else (pool["draw_odds"] or 3.0)

        positions = await queries.get_pool_positions(db, pool_id)
        bettors = len(set(p["tg_user_id"] for p in positions))
        total = sum(p["amount_usdc"] for p in positions)

        await event.answer()
        await client.send_message(
            event.chat_id,
            f"🏟️ *{pool['home_team']} vs {pool['away_team']}*\n\n"
            f"📊 Live odds:\n"
            f"🏠 {pool['home_team']}: *{home_odds:.2f}*\n"
            f"🤝 Draw: *{draw_odds:.2f}*\n"
            f"✈️ {pool['away_team']}: *{away_odds:.2f}*\n\n"
            f"💰 Pool: ${total:.2f} · {bettors} bettors\n\n"
            f"Pick your outcome:",
            buttons=outcome_buttons(pool_id, pool["home_team"], pool["away_team"],
                                    home_odds, draw_odds, away_odds),
            parse_mode="md",
        )

    @client.on(events.CallbackQuery(pattern=rb"^bet_(.+)_(home|draw|away)$"))
    async def bet_outcome_handler(event):
        parts = event.data.decode().split("_")
        # format: bet_{pool_id}_{outcome}
        outcome = parts[-1]
        pool_id = "_".join(parts[1:-1])

        pool = await queries.get_pool(db, pool_id)
        if not pool or pool["status"] != "open":
            await event.answer("Betting is closed for this match.", alert=True)
            return

        odds = odds_cache.get(pool["fixture_id"])
        outcome_odds = {
            "home": odds.home_odds if odds else pool["home_odds"] or 2.0,
            "draw": odds.draw_odds if odds else pool["draw_odds"] or 3.0,
            "away": odds.away_odds if odds else pool["away_odds"] or 2.0,
        }

        await event.answer()
        await client.send_message(
            event.chat_id,
            f"*{outcome.capitalize()}* @ {outcome_odds[outcome]:.2f}\n\nChoose your stake:",
            buttons=stake_buttons(pool_id, outcome),
            parse_mode="md",
        )

    @client.on(events.CallbackQuery(pattern=rb"^stake_(.+)_(home|draw|away)_(\d+|custom)$"))
    async def stake_handler(event):
        parts = event.data.decode().split("_")
        amount_str = parts[-1]
        outcome = parts[-2]
        pool_id = "_".join(parts[1:-2])

        if amount_str == "custom":
            custom_stake_state[event.sender_id] = {"pool_id": pool_id, "outcome": outcome}
            await event.answer()
            await client.send_message(event.chat_id, "✏️ Reply with your stake in USDC (e.g. 15):")
            return

        amount = float(amount_str)
        await _send_confirm(event.chat_id, event.sender_id, pool_id, outcome, amount)

    async def _send_confirm(chat_id, sender_id, pool_id: str, outcome: str, amount: float) -> None:
        """Build + send the bet confirmation card. Shared by preset + custom stake flows."""
        pool = await queries.get_pool(db, pool_id)
        odds = odds_cache.get(pool["fixture_id"])
        outcome_odds = {
            "home": odds.home_odds if odds else pool["home_odds"] or 2.0,
            "draw": odds.draw_odds if odds else pool["draw_odds"] or 3.0,
            "away": odds.away_odds if odds else pool["away_odds"] or 2.0,
        }
        locked_odds = outcome_odds[outcome]
        potential = round(amount * locked_odds, 2)

        user_row = await queries.get_user(db, sender_id)
        balance = user_row["usdc_balance"] if user_row else 0.0

        await client.send_message(
            chat_id,
            f"📋 *Confirm your bet*\n\n"
            f"Match: {pool['home_team']} vs {pool['away_team']}\n"
            f"Pick: *{outcome.capitalize()}*\n"
            f"Stake: *${amount:.2f} USDC*\n"
            f"Odds: *{locked_odds:.2f}*\n"
            f"To win: *${potential:.2f}*\n\n"
            f"Balance after: ${balance - amount:.2f}",
            buttons=confirm_buttons(pool_id, outcome, amount),
            parse_mode="md",
        )

    @client.on(events.NewMessage(func=lambda e: e.is_private and not e.raw_text.startswith("/")))
    async def custom_stake_reply_handler(event):
        state = custom_stake_state.get(event.sender_id)
        if not state:
            return  # not in custom-stake flow — let other handlers process
        try:
            amount = round(float(event.raw_text.strip().replace("$", "").replace(",", "")), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await event.respond("Please enter a valid amount (e.g. 15):")
            raise events.StopPropagation

        del custom_stake_state[event.sender_id]
        pool = await queries.get_pool(db, state["pool_id"])
        if not pool or pool["status"] != "open":
            await event.respond("Betting is closed for this match.")
            raise events.StopPropagation

        await _send_confirm(event.chat_id, event.sender_id, state["pool_id"], state["outcome"], amount)
        raise events.StopPropagation

    @client.on(events.CallbackQuery(pattern=rb"^confirm_(.+)_(home|draw|away)_(\d+\.?\d*)$"))
    async def confirm_bet_handler(event):
        parts = event.data.decode().split("_")
        amount = float(parts[-1])
        outcome = parts[-2]
        pool_id = "_".join(parts[1:-2])

        user = await event.get_sender()
        user_row = await queries.get_user(db, user.id)
        if not user_row:
            await event.answer("Please /start first.", alert=True)
            return

        pool = await queries.get_pool(db, pool_id)
        if not pool or pool["status"] != "open":
            await event.answer("Betting is closed.", alert=True)
            return

        odds = odds_cache.get(pool["fixture_id"])
        outcome_odds = {
            "home": odds.home_odds if odds else pool["home_odds"] or 2.0,
            "draw": odds.draw_odds if odds else pool["draw_odds"] or 3.0,
            "away": odds.away_odds if odds else pool["away_odds"] or 2.0,
        }
        locked_odds = outcome_odds[outcome]

        success = await queries.deduct_balance(db, user.id, amount)
        if not success:
            await event.answer(f"Insufficient balance. You have ${user_row['usdc_balance']:.2f}.", alert=True)
            return

        await queries.place_position(db, pool_id, user.id, outcome, amount, locked_odds)
        await queries.subscribe_chat(db, pool_id, event.chat_id)

        updated_user = await queries.get_user(db, user.id)
        await event.answer()
        await client.send_message(
            event.chat_id,
            bet_confirmed(outcome.capitalize(), amount, locked_odds, updated_user["usdc_balance"]),
            parse_mode="md",
        )

    @client.on(events.CallbackQuery(data=b"cancel"))
    async def cancel_handler(event):
        await event.answer("Cancelled.")
        await event.delete()
