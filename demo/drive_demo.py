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
import sys

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient
from telethon.tl.types import Message

TG_API_ID  = int(os.environ["TG_API_ID"])
TG_API_HASH = os.environ["TG_API_HASH"]
BOT        = "txodds_mkbot"

# Demo parameters — must match /createpool and /playmatch
POOL_ID = "demo01"
OUTCOME = "home"   # Brazil wins
STAKE   = 10       # $10 USDC

BEAT = 3.5         # seconds between steps (matches demo harness BEAT)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _wait_buttons(client: TelegramClient, bot_entity, timeout: int = 15) -> Message | None:
    """Poll until bot sends a message with inline buttons, then return it."""
    deadline = asyncio.get_event_loop().time() + timeout
    seen_ids: set[int] = set()

    # Seed with current message IDs so we only catch new messages
    seed = await client.get_messages(bot_entity, limit=5)
    for m in seed:
        seen_ids.add(m.id)

    while asyncio.get_event_loop().time() < deadline:
        msgs = await client.get_messages(bot_entity, limit=5)
        for m in msgs:
            if m.id not in seen_ids and m.buttons:
                return m
        await asyncio.sleep(0.6)
    return None


async def _click(msg: Message | None, data: bytes, label: str) -> None:
    if msg is None:
        print(f"    ⚠️  no message to click for: {label}")
        return
    try:
        await msg.click(data=data)
        print(f"    ✅ clicked: {label}")
    except Exception as e:
        print(f"    ⚠️  click failed ({label}): {e}")


# ── main flow ─────────────────────────────────────────────────────────────────

async def main() -> None:
    session_path = os.path.join(os.path.dirname(__file__), "demo_driver")
    client = TelegramClient(session_path, TG_API_ID, TG_API_HASH)
    await client.start()  # phone + OTP on first run; silent after

    me = await client.get_me()
    bot_entity = await client.get_entity(BOT)
    print(f"\n  Connected as: {me.first_name} (@{me.username})")
    print(f"  Driving:      @{BOT}")
    print(f"  Pool:         {POOL_ID}  outcome={OUTCOME}  stake=${STAKE}\n")

    async def send(cmd: str, label: str) -> None:
        print(f"==> {label}")
        await client.send_message(bot_entity, cmd)
        await asyncio.sleep(BEAT)

    # ── Step 1: Register ──────────────────────────────────────────────────────
    await send("/start", "[1/8] /start — register account")

    # ── Step 2: Credit demo balance ───────────────────────────────────────────
    await send("/demobalance", "[2/8] /demobalance — credit $100 demo USDC")

    # ── Step 3: Create pool ───────────────────────────────────────────────────
    await send(
        f"/createpool Brazil vs Argentina {POOL_ID}",
        "[3/8] /createpool Brazil vs Argentina",
    )

    # ── Step 4: Browse pools → click match ───────────────────────────────────
    print("==> [4/8] /pool — selecting match")
    await client.send_message(bot_entity, "/pool")
    await asyncio.sleep(BEAT)
    msg = await _wait_buttons(client, bot_entity)
    await _click(msg, f"pool_{POOL_ID}".encode(), f"pool_{POOL_ID}")
    await asyncio.sleep(BEAT)

    # ── Step 5: Pick outcome (Home / Brazil) ─────────────────────────────────
    print("==> [5/8] Picking outcome: 🏠 Brazil (Home)")
    msg = await _wait_buttons(client, bot_entity)
    await _click(msg, f"bet_{POOL_ID}_{OUTCOME}".encode(), f"bet_{POOL_ID}_{OUTCOME}")
    await asyncio.sleep(BEAT)

    # ── Step 6: Pick stake ────────────────────────────────────────────────────
    print(f"==> [6/8] Stake: ${STAKE} USDC")
    msg = await _wait_buttons(client, bot_entity)
    await _click(msg, f"stake_{POOL_ID}_{OUTCOME}_{STAKE}".encode(), f"stake ${STAKE}")
    await asyncio.sleep(BEAT)

    # ── Step 7: Confirm ───────────────────────────────────────────────────────
    print("==> [7/8] ✅ Confirming bet")
    msg = await _wait_buttons(client, bot_entity)
    confirm_data = f"confirm_{POOL_ID}_{OUTCOME}_{float(STAKE)}".encode()
    await _click(msg, confirm_data, "confirm bet")
    await asyncio.sleep(BEAT)

    # ── Step 8: Play match ────────────────────────────────────────────────────
    await send(
        f"/playmatch {POOL_ID} {OUTCOME}",
        "[8/8] /playmatch — scripted match + payout",
    )
    print("    Kickoff → odds shift → goal → half time → full time → payout")
    print("    (waiting ~25s for all match events)...")
    await asyncio.sleep(26)

    print("\n  Demo complete! The recording can stop now.\n")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
