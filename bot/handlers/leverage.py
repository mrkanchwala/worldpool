"""Handler: /leverage — Kamino Finance borrow-to-bet via subprocess bridge."""
from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import KeyboardButtonCallback

from db.queries import (
    get_user,
    upsert_user,
    create_leverage_position,
    get_open_leverage_positions,
)
from bot.dm import send_private
from bot.handlers.wallet import mask_address
from bot.wallet_connect import sign_via_walletconnect, broadcast_signed_transaction

logger = logging.getLogger(__name__)

# Path to the Node.js Kamino bridge script
_KAMINO_MJS = Path(__file__).resolve().parents[2] / "scripts" / "kamino_leverage.mjs"
_SUBPROCESS_TIMEOUT = 35  # seconds — Kamino RPC can be slow

# Borrow presets (USDC)
_BORROW_PRESETS = [5, 10, 25]


# ── Subprocess bridge ──────────────────────────────────────────────────────────

_NODE_BIN = os.environ.get("NODE_BIN", "node")  # override via systemd env for nvm installs

# Per-user cooldown: prevent subprocess spam (DoS gate)
_leverage_cooldown: dict[int, float] = {}
_LEVERAGE_COOLDOWN_SECS = 60


async def _call_kamino(mode: str, wallet: str, amount: float | None = None) -> dict:
    """Run kamino_leverage.mjs and return parsed JSON output."""
    args = [_NODE_BIN, str(_KAMINO_MJS), "--mode", mode, "--wallet", wallet]
    if amount is not None:
        args += ["--amount", str(amount)]

    # Allowlist only what kamino_leverage.mjs needs — never forward full env (secrets leakage)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", ""),
        "NODE_PATH": os.environ.get("NODE_PATH", ""),
        "KAMINO_RPC_URL": os.environ.get("KAMINO_RPC_URL", "https://api.mainnet-beta.solana.com"),
        "KAMINO_CLUSTER": os.environ.get("KAMINO_CLUSTER", "mainnet-beta"),
    }

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROCESS_TIMEOUT)
        output = stdout.decode().strip()
        if not output:
            err = stderr.decode().strip()
            logger.warning("kamino_leverage.mjs no output. stderr=%s", err[:1000])
            return {"ok": False, "reason": "no_output", "message": "Kamino service unavailable. Try again shortly."}
        return json.loads(output.splitlines()[-1])  # last line is the JSON result
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.communicate()  # reap zombie
            except ProcessLookupError:
                pass
        return {"ok": False, "reason": "timeout", "message": "Kamino RPC timed out. Try again in a moment."}
    except Exception as e:
        logger.exception("kamino_leverage.mjs call failed")
        return {"ok": False, "reason": "error", "message": str(e)}


# ── Message builders ───────────────────────────────────────────────────────────

def _info_message(data: dict) -> str:
    collateral = data.get("collateral_usd", 0)
    borrowed   = data.get("borrowed_usd", 0)
    available  = data.get("available_usd", 0)
    apy        = data.get("borrow_apy_pct")
    ltv        = data.get("ltv_pct")

    apy_str = f"{apy:.1f}%" if apy is not None else "—"
    ltv_str = f"{ltv:.1f}%" if ltv is not None else "—"

    return (
        "⚡ *Kamino Leverage*\n\n"
        f"Collateral: *${collateral:.2f}*\n"
        f"Borrowed: *${borrowed:.2f}*\n"
        f"Available to borrow: *${available:.2f}*\n"
        f"Current LTV: *{ltv_str}*\n"
        f"USDC borrow APY: *{apy_str}*\n\n"
        "_Borrow USDC against your Kamino collateral and deposit it to WorldPool._\n"
        "_Repay after your bet settles to avoid liquidation._"
    )


def _borrow_buttons(available: float):
    """Generate preset borrow buttons filtered by available amount."""
    from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonRow
    presets = [p for p in _BORROW_PRESETS if p <= available]
    buttons = [[KeyboardButtonCallback(f"Borrow ${p}", f"lev_borrow_{p}".encode())]
               for p in presets]
    buttons.append([KeyboardButtonCallback("Custom amount", b"lev_custom")])
    return buttons


# ── Handlers ───────────────────────────────────────────────────────────────────

def register(client: TelegramClient, db) -> None:

    async def _run_leverage(client, event, sender):
        """Shared leverage info flow — used by both /leverage command and ⚡ button."""
        chat_id = event.chat_id
        loop = asyncio.get_event_loop()
        now = loop.time()
        last = _leverage_cooldown.get(sender.id, 0)
        if now - last < _LEVERAGE_COOLDOWN_SECS:
            remaining = int(_LEVERAGE_COOLDOWN_SECS - (now - last))
            await client.send_message(
                chat_id,
                f"⏳ Please wait {remaining}s before checking leverage again\\.",
                parse_mode="md",
            )
            return
        _leverage_cooldown[sender.id] = now

        await upsert_user(db, sender.id, getattr(sender, "username", None))
        user = await get_user(db, sender.id)
        # Use dedicated Kamino wallet if set; fall back to deposit wallet
        wallet = (user["kamino_wallet"] if user and user["kamino_wallet"] else
                  user["solana_wallet"] if user else None)

        if not wallet:
            await client.send_message(
                chat_id,
                "🔑 *No Kamino wallet set*\n\n"
                "Tell me which wallet holds your Kamino position:\n"
                "`/setkamino <your Kamino wallet address>`\n\n"
                "This is the wallet you deposited collateral from on app\\.kamino\\.finance\\.",
                parse_mode="md",
            )
            return

        await client.send_message(chat_id, "⏳ Fetching your Kamino position…", parse_mode="md")
        data = await _call_kamino("info", wallet)

        if not data.get("ok"):
            reason = data.get("reason", "error")
            if reason == "no_obligation":
                await send_private(
                    client, event, sender.id,
                    "📭 *No Kamino position found*\n\n"
                    f"Wallet checked: `{mask_address(wallet)}`\n\n"
                    "If your collateral is on a different wallet, update it:\n"
                    "`/setkamino <your Kamino wallet address>`\n\n"
                    "Otherwise, deposit collateral on [app\\.kamino\\.finance](https://app.kamino.finance) first\\.",
                    parse_mode="md",
                    link_preview=False,
                )
            else:
                msg = data.get("message", "Unknown error.")
                await client.send_message(chat_id, f"❌ Kamino error: {msg}", parse_mode="md")
            return

        available = data.get("available_usd", 0)
        if available < 1:
            await client.send_message(
                chat_id,
                "📉 *Borrow capacity too low*\n\n"
                "Your available borrow amount is below $1 USDC\\. "
                "Add more collateral on [app.kamino.finance](https://app.kamino.finance)\\.",
                parse_mode="md",
                link_preview=False,
            )
            return

        # Reveals collateral/borrow figures — personal financial info, DM it.
        await send_private(
            client, event, sender.id,
            _info_message(data), buttons=_borrow_buttons(available), parse_mode="md",
        )

    @client.on(events.CallbackQuery(data=b"leverage"))
    async def leverage_callback_handler(event):
        await event.answer()
        sender = await event.get_sender()
        await _run_leverage(client, event, sender)

    @client.on(events.NewMessage(pattern=r"^/leverage$"))
    async def leverage_handler(event):
        sender = await event.get_sender()
        await _run_leverage(client, event, sender)
        raise events.StopPropagation

    @client.on(events.CallbackQuery(pattern=rb"^lev_borrow_(\d+)$"))
    async def lev_borrow_preset_handler(event):
        amount = float(event.data.decode().split("_")[-1])
        await _handle_borrow(event, db, amount)

    @client.on(events.CallbackQuery(pattern=rb"^lev_custom$"))
    async def lev_custom_handler(event):
        await event.answer()
        await event.respond(
            "✏️ Reply with the USDC amount you want to borrow \\(e\\.g\\. 15\\)\\.\n"
            "Use `/leverage` first to see your available capacity\\.",
            parse_mode="md",
        )


_KAMINO_MAINNET_RPC = os.environ.get("KAMINO_RPC_URL", "https://api.mainnet-beta.solana.com")


async def _handle_borrow(event, db, amount: float) -> None:
    """Shared borrow flow: call Kamino bridge, sign via WalletConnect (wallet-
    agnostic — Phantom/Solflare/Backpack/Trust/etc., not Phantom-only), broadcast."""
    await event.answer()

    sender = await event.get_sender()
    user = await get_user(db, sender.id)
    # Same priority as _run_leverage's lookup — a user who only ran /setkamino
    # (the documented, dedicated way to register a Kamino wallet) must borrow
    # against that same wallet, not silently fall through to a missing/wrong one.
    wallet = (user["kamino_wallet"] if user and user["kamino_wallet"] else
              user["solana_wallet"] if user else None)

    if not wallet:
        await event.respond("❌ No wallet registered\\. Use `/setkamino <address>` or `/wallet <address>` first\\.", parse_mode="md")
        return

    await event.respond(f"⏳ Generating borrow transaction for ${amount:.2f} USDC…", parse_mode="md")

    data = await _call_kamino("borrow", wallet, amount)

    if not data.get("ok"):
        reason = data.get("reason", "error")
        msg = data.get("message", "Unknown error.")
        await event.respond(f"❌ Borrow failed: {msg}", parse_mode="md")
        return

    tx_b64   = data["tx_base64"]
    apy      = data.get("estimated_apy_pct")
    apy_str  = f" (APY: {apy:.1f}%)" if apy is not None else ""

    # Record pending leverage position (before tx confirmed)
    lev_id = await create_leverage_position(db, sender.id, amount)

    client = event.client

    async def _send_connect_link(uri: str) -> None:
        await send_private(
            client, event, sender.id,
            f"⚡ *Borrow ${amount:.2f} USDC via Kamino*{apy_str}\n\n"
            f"1\\. Open your Solana wallet \\(Phantom, Solflare, Backpack, Trust, etc\\.\\) "
            f"and connect via WalletConnect:\n`{uri}`\n\n"
            f"2\\. Approve the connection, then approve the transaction\\.\n\n"
            f"3\\. Once signed, USDC arrives in your wallet\\. Use `/deposit` to send it "
            f"to WorldPool and start betting\\.\n\n"
            f"⚠️ _Remember to repay your Kamino loan after your bet settles to avoid liquidation\\._\n\n"
            f"_Leverage position ID: `{lev_id}`_",
            parse_mode="md",
            link_preview=False,
        )

    result = await sign_via_walletconnect(tx_b64, "mainnet", _send_connect_link)

    if result.get("stage") != "signed":
        reason = result.get("reason", "error")
        msg = result.get("message", "Unknown error.")
        await send_private(
            client, event, sender.id,
            f"❌ *Borrow not completed* \\({reason}\\)\n{msg}\n\n"
            f"_Leverage position ID: `{lev_id}`_ — no funds moved\\.",
            parse_mode="md",
        )
        return

    try:
        sig = await broadcast_signed_transaction(_KAMINO_MAINNET_RPC, result["signed_tx_base64"])
    except Exception as e:
        logger.exception("Broadcast of WalletConnect-signed borrow tx failed")
        await send_private(
            client, event, sender.id,
            f"❌ *Signed, but broadcast failed:* {e}\n\n"
            f"_Leverage position ID: `{lev_id}`_ — your wallet was not debited\\.",
            parse_mode="md",
        )
        return

    await send_private(
        client, event, sender.id,
        f"✅ *Borrow confirmed* — ${amount:.2f} USDC on the way to your wallet\\.\n"
        f"Tx: `{sig}`\n\n"
        f"Use `/deposit` to send it to WorldPool and start betting\\.",
        parse_mode="md",
    )
