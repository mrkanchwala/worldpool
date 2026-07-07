"""Handler: /deposit — dedicated-address deposit flow.

Deposit identity is the receiving address itself (see bot/deposit_wallet.py) —
no wallet self-report, no memo. Any Solana wallet works.
"""
from __future__ import annotations
import asyncio
import logging
import os
import urllib.parse

import httpx
from telethon import TelegramClient, events

from bot.alerts import deposit_pending, deposit_confirmed
from bot.buttons import deposit_amounts
from bot.deposit_wallet import ensure_deposit_address, sweep_deposit, get_associated_token_address, USDC_MINT
from bot.dm import send_private, send_private_bg
from db.queries import get_user, credit_balance, upsert_user, is_processed, mark_processed
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

OPERATOR_WALLET = os.getenv("OPERATOR_ESCROW_WALLET", "")
USDC_MINT_ENV = os.getenv("USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
POLL_INTERVAL = 10   # seconds
POLL_TIMEOUT = 600   # 10 minutes
AUTOCREDIT = os.getenv("DEPOSIT_AUTOCREDIT", "true").lower() == "true"

_AMOUNT_TOLERANCE = 0.01
_USDC_DECIMALS = 6

# tg_user_id → True (waiting for custom USDC amount reply)
_awaiting_custom_amount: dict[int, bool] = {}


def phantom_universal_link(tx_base64: str, cluster: str = "mainnet-beta") -> str:
    encoded = urllib.parse.quote(tx_base64, safe="")
    return (
        f"https://phantom.app/ul/v1/signAndSendTransaction"
        f"?transaction={encoded}&cluster={cluster}"
    )


async def _verify_deposit_arrival(rpc_url: str, signature: str, ata: Pubkey,
                                   expected_amount: float) -> float | None:
    """Check a signature touching the user's own ATA for an incoming USDC
    transfer of any amount. Returns the actual amount if found, else None.
    No sender-identity check needed — the destination IS the identity."""
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
            return None

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

            if info.get("mint", "") not in ("", USDC_MINT_ENV):
                continue
            dest = info.get("destination", "") or info.get("destinationOwner", "")
            if dest != str(ata):
                continue

            raw = info.get("tokenAmount", {}).get("uiAmount") or info.get("amount")
            try:
                actual = float(raw) if "." in str(raw) else int(raw) / (10 ** _USDC_DECIMALS)
            except (TypeError, ValueError):
                continue
            return actual

        return None
    except Exception as e:
        logger.warning("getTransaction verification failed for %s: %s", signature, e)
        return None


async def _poll_deposit(bot: TelegramClient, chat_id: int, tg_user_id: int,
                        amount: float, deposit_address: str, db, rpc_url: str) -> None:
    ata = get_associated_token_address(Pubkey.from_string(deposit_address), Pubkey.from_string(USDC_MINT))
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [str(ata), {"limit": 10}],
                })
            sigs = r.json().get("result", [])
            for sig_info in sigs:
                sig = sig_info.get("signature", "")
                if not sig or await is_processed(db, sig):
                    continue
                actual = await _verify_deposit_arrival(rpc_url, sig, ata, amount)
                if actual is None:
                    continue
                if abs(actual - amount) > _AMOUNT_TOLERANCE:
                    # Real arrival at this user's own address, just a different
                    # amount than the button they clicked — credit the real
                    # amount, it's unambiguously theirs either way.
                    logger.info("Deposit amount mismatch (expected %.2f, got %.2f) — crediting actual, still this user's address", amount, actual)
                await mark_processed(db, sig)
                await credit_balance(db, tg_user_id, actual)
                user = await get_user(db, tg_user_id)
                await send_private_bg(
                    bot, tg_user_id, chat_id,
                    deposit_confirmed(actual, user["usdc_balance"]),
                    parse_mode="md",
                )
                logger.info("Deposit credited: user=%s amount=%.2f sig=%s", tg_user_id, actual, sig)
                if OPERATOR_WALLET:
                    asyncio.create_task(_sweep_and_log(db, tg_user_id, rpc_url, actual))
                return
        except Exception as e:
            logger.warning("Deposit poll error: %s", e)

    await send_private_bg(
        bot, tg_user_id, chat_id,
        "⚠️ Deposit not detected after 10 minutes\\.\n"
        "If you sent USDC, contact support with your tx signature\\.",
        parse_mode="md",
    )


async def _sweep_and_log(db, tg_user_id: int, rpc_url: str, amount: float) -> None:
    try:
        await sweep_deposit(db, tg_user_id, rpc_url, amount, OPERATOR_WALLET)
    except Exception:
        logger.exception("Sweep failed for user=%s amount=%.2f — funds remain safely at their "
                          "own deposit address, DB balance is already credited and correct", tg_user_id, amount)


async def _start_deposit(bot: TelegramClient, event, tg_user_id: int,
                         amount: float, db, rpc_url: str) -> None:
    address = await ensure_deposit_address(db, tg_user_id, rpc_url)
    await send_private(
        bot, event, tg_user_id,
        deposit_pending(amount, address),
        parse_mode="md",
        link_preview=False,
    )
    if AUTOCREDIT:
        asyncio.create_task(
            _poll_deposit(bot, event.chat_id, tg_user_id, amount, address, db, rpc_url)
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
        user = await event.get_sender()

        if amount_str == "custom":
            _awaiting_custom_amount[user.id] = True
            await event.answer()
            await event.respond(
                "✏️ Reply with the amount in USDC \\(e\\.g\\. 15\\):", parse_mode="md"
            )
            return

        amount = float(amount_str)
        await event.answer()
        await _start_deposit(client, event, user.id, amount, db, rpc_url)

    @client.on(events.NewMessage())
    async def custom_amount_reply_handler(event):
        user = await event.get_sender()
        text = (event.raw_text or "").strip()

        # Let slash commands through to their own handlers
        if text.startswith("/"):
            return
        if user.id not in _awaiting_custom_amount:
            return

        _awaiting_custom_amount.pop(user.id)
        try:
            amount = float(text.replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await event.respond(
                "❌ Please enter a valid amount, e\\.g\\. `15` or `7\\.5`", parse_mode="md"
            )
            return

        await _start_deposit(client, event, user.id, amount, db, rpc_url)
        raise events.StopPropagation
