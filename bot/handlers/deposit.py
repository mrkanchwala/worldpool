"""Handler: /deposit — generate Solana Pay TxLink + poll PDA for confirmation."""
from __future__ import annotations
import asyncio
import logging
import os
import urllib.parse

import httpx
from telethon import TelegramClient, events

from bot.alerts import deposit_pending, deposit_confirmed
from bot.buttons import deposit_amounts
from db.queries import get_user, credit_balance, upsert_user, is_processed, mark_processed

logger = logging.getLogger(__name__)

OPERATOR_WALLET = os.getenv("OPERATOR_ESCROW_WALLET", "")
# Devnet USDC mint (use USDC_MINT env for mainnet: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v)
USDC_MINT = os.getenv("USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
POLL_INTERVAL = 10  # seconds
POLL_TIMEOUT = 600  # 10 minutes
AUTOCREDIT = os.getenv("DEPOSIT_AUTOCREDIT", "false").lower() == "true"

# Tolerance for floating-point amount comparison (0.01 USDC)
_AMOUNT_TOLERANCE = 0.01
# USDC has 6 decimals
_USDC_DECIMALS = 6


PAY_REDIRECT_BASE = os.getenv(
    "PAY_REDIRECT_BASE", "https://quadrigasolutions.com/api/pay"
)


def _deposit_link(amount: float, memo: str) -> str:
    """Return an HTTPS link that redirects to the solana: pay URI.
    Telegram renders HTTPS links as clickable; mobile Phantom catches the redirect."""
    return f"{PAY_REDIRECT_BASE}/{OPERATOR_WALLET}/{amount:.2f}/{memo}"


def phantom_universal_link(tx_base64: str, cluster: str = "mainnet-beta") -> str:
    """Return a Phantom Universal Link for signing a serialized transaction.

    Works on mobile (redirects to Phantom app) and desktop (opens phantom.app in browser).
    cluster: 'mainnet-beta' | 'devnet'
    """
    encoded = urllib.parse.quote(tx_base64, safe="")
    return (
        f"https://phantom.app/ul/v1/signAndSendTransaction"
        f"?transaction={encoded}&cluster={cluster}"
    )


async def _verify_spl_transfer(rpc_url: str, signature: str, expected_amount: float,
                                tg_user_id: int) -> bool:
    """Verify a transaction is a valid USDC SPL transfer to the operator wallet.

    Checks: correct SPL mint, recipient = operator wallet, amount within tolerance,
    memo contains tg_user_id. Returns True only if ALL checks pass.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(rpc_url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            })
        tx = r.json().get("result")
        if not tx:
            return False

        meta = tx.get("meta", {})
        if meta.get("err") is not None:
            return False  # failed transaction

        instructions = (
            tx.get("transaction", {})
              .get("message", {})
              .get("instructions", [])
        )

        memo_found = False
        transfer_ok = False

        for ix in instructions:
            program = ix.get("program", "")
            parsed = ix.get("parsed", {})
            ix_type = parsed.get("type", "") if isinstance(parsed, dict) else ""
            info = parsed.get("info", {}) if isinstance(parsed, dict) else {}

            # Check memo program for tg_user_id
            if program == "spl-memo":
                memo_str = str(ix.get("parsed", ""))
                if str(tg_user_id) in memo_str.split():
                    memo_found = True

            # Check SPL token transfer
            if program == "spl-token" and ix_type in ("transfer", "transferChecked"):
                mint = info.get("mint", "")
                dest = info.get("destination", "") or info.get("destinationOwner", "")
                raw_amount = info.get("tokenAmount", {}).get("uiAmount") or info.get("amount")

                if mint != USDC_MINT:
                    continue
                if dest != OPERATOR_WALLET:
                    continue

                try:
                    actual = float(raw_amount) if "." in str(raw_amount) else int(raw_amount) / (10 ** _USDC_DECIMALS)
                except (TypeError, ValueError):
                    continue

                if abs(actual - expected_amount) <= _AMOUNT_TOLERANCE:
                    transfer_ok = True

        return memo_found and transfer_ok

    except Exception as e:
        logger.warning("getTransaction verification failed for %s: %s", signature, e)
        return False


async def _poll_deposit(bot: TelegramClient, chat_id: int, tg_user_id: int,
                        amount: float, db, rpc_url: str) -> None:
    """Poll Solana RPC for deposit confirmation with full SPL transfer verification."""
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
                signature = sig_info.get("signature", "")
                if not signature:
                    continue
                if await is_processed(db, signature):
                    continue

                # Full SPL transfer verification before crediting
                if await _verify_spl_transfer(rpc_url, signature, amount, tg_user_id):
                    await mark_processed(db, signature)
                    await credit_balance(db, tg_user_id, amount)
                    user = await get_user(db, tg_user_id)
                    await bot.send_message(
                        chat_id,
                        deposit_confirmed(amount, user["usdc_balance"]),
                        parse_mode="md",
                    )
                    logger.info("Deposit credited: user=%s amount=%.2f sig=%s", tg_user_id, amount, signature)
                    return

        except Exception as e:
            logger.warning("Deposit poll error: %s", e)

    await bot.send_message(
        chat_id,
        "⚠️ Deposit not detected after 10 minutes\\.\n"
        "If you sent USDC, contact support with your tx signature\\.",
        parse_mode="md",
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
            await event.respond("✏️ Reply with the amount in USDC \\(e\\.g\\. 15\\):", parse_mode="md")
            return

        amount = float(amount_str)
        user = await event.get_sender()
        memo = str(user.id)
        pay_url = _deposit_link(amount, memo)

        await event.answer()
        await event.respond(
            deposit_pending(amount, pay_url, memo, OPERATOR_WALLET),
            parse_mode="md",
            link_preview=False,
        )
        if AUTOCREDIT:
            asyncio.create_task(
                _poll_deposit(client, event.chat_id, user.id, amount, db, rpc_url)
            )
