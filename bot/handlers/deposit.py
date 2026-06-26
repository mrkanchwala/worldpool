"""Handler: /deposit — wallet-based deposit flow. No memo required."""
from __future__ import annotations
import asyncio
import logging
import os
import re
import urllib.parse

import httpx
from telethon import TelegramClient, events

from bot.alerts import deposit_pending, deposit_confirmed
from bot.buttons import deposit_amounts
from db.queries import get_user, credit_balance, upsert_user, is_processed, mark_processed

logger = logging.getLogger(__name__)

OPERATOR_WALLET = os.getenv("OPERATOR_ESCROW_WALLET", "")
USDC_MINT = os.getenv("USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
POLL_INTERVAL = 10   # seconds
POLL_TIMEOUT = 600   # 10 minutes
AUTOCREDIT = os.getenv("DEPOSIT_AUTOCREDIT", "true").lower() == "true"

_AMOUNT_TOLERANCE = 0.01
_USDC_DECIMALS = 6
_BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# tg_user_id → pending deposit amount (waiting for wallet address reply)
_awaiting_wallet: dict[int, float] = {}


def phantom_universal_link(tx_base64: str, cluster: str = "mainnet-beta") -> str:
    encoded = urllib.parse.quote(tx_base64, safe="")
    return (
        f"https://phantom.app/ul/v1/signAndSendTransaction"
        f"?transaction={encoded}&cluster={cluster}"
    )


async def _verify_spl_transfer(rpc_url: str, signature: str,
                                expected_amount: float, sender_wallet: str) -> bool:
    """Verify USDC transfer: correct mint, recipient = operator, amount matches, sender matches."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed",
                                       "maxSupportedTransactionVersion": 0}],
            })
        tx = r.json().get("result")
        if not tx or tx.get("meta", {}).get("err") is not None:
            return False

        instructions = (
            tx.get("transaction", {}).get("message", {}).get("instructions", [])
        )
        for ix in instructions:
            if ix.get("program") != "spl-token":
                continue
            parsed = ix.get("parsed", {})
            if not isinstance(parsed, dict):
                continue
            if parsed.get("type") not in ("transfer", "transferChecked"):
                continue
            info = parsed.get("info", {})

            if info.get("mint", "") != USDC_MINT:
                continue
            dest = info.get("destination", "") or info.get("destinationOwner", "")
            if dest != OPERATOR_WALLET:
                continue
            if info.get("authority", "") != sender_wallet:
                continue

            raw = info.get("tokenAmount", {}).get("uiAmount") or info.get("amount")
            try:
                actual = float(raw) if "." in str(raw) else int(raw) / (10 ** _USDC_DECIMALS)
            except (TypeError, ValueError):
                continue
            if abs(actual - expected_amount) <= _AMOUNT_TOLERANCE:
                return True

        return False
    except Exception as e:
        logger.warning("getTransaction verification failed for %s: %s", signature, e)
        return False


async def _poll_deposit(bot: TelegramClient, chat_id: int, tg_user_id: int,
                        amount: float, sender_wallet: str, db, rpc_url: str) -> None:
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [OPERATOR_WALLET, {"limit": 10}],
                })
            sigs = r.json().get("result", [])
            for sig_info in sigs:
                sig = sig_info.get("signature", "")
                if not sig or await is_processed(db, sig):
                    continue
                if await _verify_spl_transfer(rpc_url, sig, amount, sender_wallet):
                    await mark_processed(db, sig)
                    await credit_balance(db, tg_user_id, amount)
                    user = await get_user(db, tg_user_id)
                    await bot.send_message(
                        chat_id,
                        deposit_confirmed(amount, user["usdc_balance"]),
                        parse_mode="md",
                    )
                    logger.info("Deposit credited: user=%s amount=%.2f sig=%s",
                                tg_user_id, amount, sig)
                    return
        except Exception as e:
            logger.warning("Deposit poll error: %s", e)

    await bot.send_message(
        chat_id,
        "⚠️ Deposit not detected after 10 minutes\\.\n"
        "If you sent USDC, contact support with your tx signature\\.",
        parse_mode="md",
    )


async def _start_deposit(bot: TelegramClient, chat_id: int, tg_user_id: int,
                         amount: float, sender_wallet: str, db, rpc_url: str) -> None:
    await bot.send_message(
        chat_id,
        deposit_pending(amount, sender_wallet, OPERATOR_WALLET),
        parse_mode="md",
        link_preview=False,
    )
    if AUTOCREDIT:
        asyncio.create_task(
            _poll_deposit(bot, chat_id, tg_user_id, amount, sender_wallet, db, rpc_url)
        )


def register(client: TelegramClient, db, rpc_url: str) -> None:

    @client.on(events.NewMessage(pattern=r"^/deposit"))
    async def deposit_handler(event):
        user = await event.get_sender()
        await upsert_user(db, user.id, getattr(user, "username", None))
        await event.respond(
            "💳 *Deposit to WorldPool*\n\nChoose an amount:",
            buttons=deposit_amounts(),
            parse_mode="md",
        )
        raise events.StopPropagation

    @client.on(events.CallbackQuery(pattern=rb"^dep_(\d+|custom)$"))
    async def deposit_amount_handler(event):
        data = event.data.decode()
        amount_str = data.split("_")[1]
        if amount_str == "custom":
            await event.answer()
            await event.respond(
                "✏️ Reply with the amount in USDC \\(e\\.g\\. 15\\):", parse_mode="md"
            )
            return

        amount = float(amount_str)
        user = await event.get_sender()
        db_user = await get_user(db, user.id)
        wallet = db_user["solana_wallet"] if db_user else None

        await event.answer()

        if wallet:
            await _start_deposit(client, event.chat_id, user.id, amount, wallet, db, rpc_url)
        else:
            _awaiting_wallet[user.id] = amount
            await event.respond(
                "👛 *Enter your Solana wallet address*\n\n"
                "The bot will confirm your deposit once USDC arrives from this address\\.\n"
                "You only need to do this once\\.",
                parse_mode="md",
            )

    @client.on(events.NewMessage())
    async def wallet_reply_handler(event):
        user = await event.get_sender()
        if user.id not in _awaiting_wallet:
            return
        text = (event.raw_text or "").strip()
        if not _BASE58.match(text):
            await event.respond(
                "❌ That doesn't look like a valid Solana address\\. Please try again\\.",
                parse_mode="md",
            )
            return

        amount = _awaiting_wallet.pop(user.id)
        await upsert_user(db, user.id, getattr(user, "username", None), solana_wallet=text)
        await _start_deposit(client, event.chat_id, user.id, amount, text, db, rpc_url)
        raise events.StopPropagation
