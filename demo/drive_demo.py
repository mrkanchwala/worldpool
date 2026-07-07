"""WorldPool demo auto-driver — Telethon user client.

Sends every command + clicks every button in @txodds_mkbot so the full
betting flow records itself on screen without any manual Telegram input.

First run: prompts phone number + OTP (one-time — saves demo_driver.session).
Subsequent runs: connects silently from the saved session.

Usage (from worldpool repo root):
    python3 demo/drive_demo.py
"""
from __future__ import annotations
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient
from telethon.tl.custom import Message

from db.schema import init_db
from db.queries import upsert_user, place_position

TG_API_ID  = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
BOT        = "txodds_mkbot"

OUTCOME = "home"   # Brazil wins
STAKE   = 10       # $10 USDC
BEAT    = 1.5      # seconds between steps


# ── helpers ──────────────────────────────────────────────────────────────────

async def _latest_id(client, bot) -> int:
    """Return the id of the most recent message in this chat (0 if empty)."""
    msgs = await client.get_messages(bot, limit=1)
    return msgs[0].id if msgs else 0


async def _wait_new(client, bot, after_id: int, timeout: int = 14,
                    need_buttons: bool = False) -> Message | None:
    """Poll until a message with id > after_id arrives (optionally with buttons)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msgs = await client.get_messages(bot, limit=10)
        for m in msgs:
            if m.id > after_id:
                if not need_buttons or m.buttons:
                    return m
        await asyncio.sleep(0.5)
    return None


def _find_btn(msg: Message | None, data_prefix: bytes) -> object | None:
    """Return the first button whose data starts with data_prefix."""
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            if btn.data and btn.data.startswith(data_prefix):
                return btn
    return None


async def _click_btn(msg: Message | None, data_prefix: bytes, label: str) -> bool:
    btn = _find_btn(msg, data_prefix)
    if not btn:
        print(f"    ⚠️  button not found: {label}")
        return False
    try:
        await btn.click()
        print(f"    ✅ clicked '{btn.text}' ({label})")
        return True
    except Exception as e:
        print(f"    ⚠️  click failed ({label}): {e}")
        return False


# ── main flow ─────────────────────────────────────────────────────────────────

async def main() -> None:
    session_path = os.path.join(os.path.dirname(__file__), "demo_driver")
    client = TelegramClient(session_path, TG_API_ID, TG_API_HASH)
    await client.start()

    me = await client.get_me()
    bot = await client.get_entity(BOT)
    print(f"\n  Connected as: {me.first_name} (@{me.username})")
    print(f"  Driving:      @{BOT}\n")

    # ── 1. /start ────────────────────────────────────────────────────────────
    print("==> [1/10] /start — register account")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/start")
    await _wait_new(client, bot, before)
    await asyncio.sleep(BEAT)

    # ── 2. /demobalance ──────────────────────────────────────────────────────
    print("==> [2/10] /demobalance — credit $100 demo USDC")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/demobalance")
    await _wait_new(client, bot, before)
    await asyncio.sleep(BEAT)

    # ── 3. /createpool — resolve the real pool_id via direct DB lookup ────────
    # Parsing it out of the bot's reply text was unreliable (Telethon's
    # re-rendered text didn't always keep the backticks the regex expected),
    # which silently fed the wrong id into every later step — go straight to
    # the DB instead, keyed by the fixture_id we know we just created ("demo01").
    print("==> [3/10] /createpool Brazil vs Argentina demo01")
    await client.send_message(bot, "/createpool Brazil vs Argentina demo01")
    await asyncio.sleep(2)  # let the bot's INSERT land before we query for it
    lookup_db = await init_db("worldpool_demo.db")
    async with lookup_db.execute(
        "SELECT pool_id FROM pools WHERE fixture_id = ? ORDER BY created_at DESC LIMIT 1", ("demo01",)
    ) as cur:
        row = await cur.fetchone()
    await lookup_db.close()
    pool_id = row["pool_id"] if row else "demo01"
    print(f"    Pool ID: {pool_id}")
    await asyncio.sleep(BEAT)

    # ── 4. /pool → click the match ───────────────────────────────────────────
    print("==> [4/10] /pool — selecting Brazil vs Argentina")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/pool")
    pool_list_msg = await _wait_new(client, bot, before, need_buttons=True)
    before = await _latest_id(client, bot)
    await _click_btn(pool_list_msg, b"pool_", "pool button")
    outcome_msg = await _wait_new(client, bot, before, need_buttons=True)
    await asyncio.sleep(BEAT)

    # ── 5. Pick outcome (Home / Brazil) ──────────────────────────────────────
    print("==> [5/10] Picking outcome: 🏠 Home (Brazil)")
    before = await _latest_id(client, bot)
    if outcome_msg and outcome_msg.buttons:
        for row in outcome_msg.buttons:
            for btn in row:
                if btn.data and btn.data.endswith(b"_home"):
                    try:
                        await btn.click()
                        print(f"    ✅ clicked '{btn.text}' (home outcome)")
                    except Exception as e:
                        print(f"    ⚠️  home click failed: {e}")
                    break
    stake_msg = await _wait_new(client, bot, before, need_buttons=True)
    await asyncio.sleep(BEAT)

    # ── 6. Pick stake ($10) ───────────────────────────────────────────────────
    print(f"==> [6/10] Stake: ${STAKE} USDC")
    before = await _latest_id(client, bot)
    if stake_msg and stake_msg.buttons:
        for row in stake_msg.buttons:
            for btn in row:
                if btn.data and btn.data.endswith(f"_{STAKE}".encode()):
                    try:
                        await btn.click()
                        print(f"    ✅ clicked '{btn.text}' (${STAKE})")
                    except Exception as e:
                        print(f"    ⚠️  stake click failed: {e}")
                    break
    confirm_msg = await _wait_new(client, bot, before, need_buttons=True)
    await asyncio.sleep(BEAT)

    # ── 7. Confirm ────────────────────────────────────────────────────────────
    print("==> [7/10] ✅ Confirming bet")
    before = await _latest_id(client, bot)
    await _click_btn(confirm_msg, b"confirm_", "confirm")
    placed = await _wait_new(client, bot, before, timeout=10)
    if placed:
        print(f"    Bet placed: {(placed.text or '')[:60].strip()}")
    await asyncio.sleep(BEAT)

    # ── 8. Register the real Solana address for Kamino lookup ────────────────
    # Written directly to the demo DB instead of sending "/wallet <address>"
    # as a visible command — the address is real (needed for a real Kamino
    # position lookup) but shouldn't appear as plaintext in the recording.
    demo_wallet = os.environ.get("DEMO_WALLET_ADDRESS", "")
    if demo_wallet:
        print("==> [8/10] Registering Kamino-lookup wallet (off-screen, not sent as a command)")
        demo_db = await init_db("worldpool_demo.db")
        await upsert_user(demo_db, me.id, me.username, solana_wallet=demo_wallet)
        await demo_db.close()
        await asyncio.sleep(BEAT)
    else:
        print("==> [8/10] skipped — set DEMO_WALLET_ADDRESS to drive the /leverage step")

    # ── 9. /leverage — real position lookup + real borrow-tx generation via
    # Kamino, real WalletConnect pairing URI. For this recording (internal team
    # walkthrough, not the hackathon submission) no signature is completed —
    # showing the real URI proves the mechanism isn't faked without moving any
    # funds. The driver shows the URI, waits briefly so it's visible on screen,
    # then moves on — it does NOT block waiting for an approval that isn't coming.
    if demo_wallet:
        print("==> [9/10] /leverage — Kamino borrow via WalletConnect")
        before = await _latest_id(client, bot)
        await client.send_message(bot, "/leverage")
        lev_msg = await _wait_new(client, bot, before, need_buttons=True)
        before = await _latest_id(client, bot)
        clicked = await _click_btn(lev_msg, b"lev_borrow_", "borrow preset")
        if clicked:
            # Bot sends a "Generating..." status first, THEN the WalletConnect
            # URI as a separate message — scan past both to find the real one.
            scan_before = before
            uri_found = False
            scan_deadline = asyncio.get_event_loop().time() + 20
            while asyncio.get_event_loop().time() < scan_deadline:
                msg = await _wait_new(client, bot, scan_before, timeout=2)
                if not msg:
                    continue
                scan_before = msg.id
                if msg.text and "wc:" in msg.text:
                    m = re.search(r"`(wc:[^`]+)`", msg.text)
                    if m:
                        print("\n    ⚡ WalletConnect pairing URI shown on screen (real, not a mockup):")
                        print(f"    {m.group(1)}\n")
                    uri_found = True
                    break
            if not uri_found:
                print("    (No WalletConnect URI seen within scan window — check Telegram manually)")
            print("    Holding for a few seconds so the URI is visible on screen — no signature will be completed.")
            await asyncio.sleep(6)
        await asyncio.sleep(BEAT)
    else:
        print("==> [9/10] skipped — no wallet registered")

    # ── 9b. Seed a few fake bettors so settlement shows real winners AND losers ──
    print("    Seeding 3 fake bettors (off-screen) so settlement has a real crowd")
    demo_db2 = await init_db("worldpool_demo.db")
    fake_bettors = [
        (900001, "sam_bets", "home", 15.0),   # wins with the real user
        (900002, "jess_wagers", "away", 20.0),  # loses
        (900003, "leo_punts", "draw", 8.0),     # loses
    ]
    for uid, uname, outcome, amount in fake_bettors:
        await upsert_user(demo_db2, uid, uname)
        await place_position(demo_db2, pool_id, uid, outcome, amount, 2.0)
    await demo_db2.close()

    # ── 10. /playmatch — scripted match ───────────────────────────────────────
    print(f"==> [10/10] /playmatch {pool_id} home — scripted match + payout")
    await client.send_message(bot, f"/playmatch {pool_id} home")
    print("    Kickoff → odds shift → goal → half time → full time → payout")
    print("    (waiting ~14s for all match events)...")
    await asyncio.sleep(14)

    print("\n  Demo complete! The recording can stop now.\n")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
