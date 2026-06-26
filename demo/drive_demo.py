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

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient
from telethon.tl.custom import Message

TG_API_ID  = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
BOT        = "txodds_mkbot"

OUTCOME = "home"   # Brazil wins
STAKE   = 10       # $10 USDC
BEAT    = 3.0      # seconds between steps


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
    print("==> [1/8] /start — register account")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/start")
    await _wait_new(client, bot, before)
    await asyncio.sleep(BEAT)

    # ── 2. /demobalance ──────────────────────────────────────────────────────
    print("==> [2/8] /demobalance — credit $100 demo USDC")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/demobalance")
    await _wait_new(client, bot, before)
    await asyncio.sleep(BEAT)

    # ── 3. /createpool — capture the real pool_id from response ──────────────
    print("==> [3/8] /createpool Brazil vs Argentina demo01")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/createpool Brazil vs Argentina demo01")
    resp = await _wait_new(client, bot, before)
    pool_id = None
    if resp and resp.text:
        m = re.search(r"Pool[:\s*`]+([a-f0-9]{6,36})", resp.text)
        if m:
            pool_id = m.group(1)
            print(f"    Pool ID: {pool_id}")
    if not pool_id:
        pool_id = "demo01"   # fallback — /playmatch also accepts fixture_id now
        print(f"    Using fixture_id fallback: {pool_id}")
    await asyncio.sleep(BEAT)

    # ── 4. /pool → click the match ───────────────────────────────────────────
    print("==> [4/8] /pool — selecting Brazil vs Argentina")
    before = await _latest_id(client, bot)
    await client.send_message(bot, "/pool")
    pool_list_msg = await _wait_new(client, bot, before, need_buttons=True)
    before = await _latest_id(client, bot)
    await _click_btn(pool_list_msg, b"pool_", "pool button")
    outcome_msg = await _wait_new(client, bot, before, need_buttons=True)
    await asyncio.sleep(BEAT)

    # ── 5. Pick outcome (Home / Brazil) ──────────────────────────────────────
    print("==> [5/8] Picking outcome: 🏠 Home (Brazil)")
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
    print(f"==> [6/8] Stake: ${STAKE} USDC")
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
    print("==> [7/8] ✅ Confirming bet")
    before = await _latest_id(client, bot)
    await _click_btn(confirm_msg, b"confirm_", "confirm")
    placed = await _wait_new(client, bot, before, timeout=10)
    if placed:
        print(f"    Bet placed: {(placed.text or '')[:60].strip()}")
    await asyncio.sleep(BEAT)

    # ── 8. /playmatch — scripted match ───────────────────────────────────────
    print(f"==> [8/8] /playmatch {pool_id} home — scripted match + payout")
    await client.send_message(bot, f"/playmatch {pool_id} home")
    print("    Kickoff → odds shift → goal → half time → full time → payout")
    print("    (waiting ~26s for all match events)...")
    await asyncio.sleep(26)

    print("\n  Demo complete! The recording can stop now.\n")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
