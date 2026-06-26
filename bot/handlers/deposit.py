"""Handler: /deposit — generate Solana Pay TxLink + poll PDA for confirmation."""
from __future__ import annotations
import asyncio
import os
import urllib.parse

from telethon import TelegramClient, events, Button

from bot.alerts import deposit_pending, deposit_confirmed
from bot.buttons import deposit_amounts
from db.queries import get_user, credit_balance, upsert_user, is_processed, mark_processed

OPERATOR_WALLET = os.getenv("OPERATOR_ESCROW_WALLET", "")
USDC_MINT = os.getenv("USDC_MINT_DEVNET", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
POLL_INTERVAL = 10  # seconds
POLL_TIMEOUT = 600  # 10 minutes
# SECURITY: memo-based auto-credit does NOT verify transfer amount, recipient, or SPL mint.
# It is spoofable/replayable and MUST NOT be enabled with real funds. Default OFF — operator
# credits manually after confirming the on-chain transfer. Before mainnet: replace with full
# getTransaction parsing (verify SPL transfer to operator ATA + exact amount + signature dedup).
AUTOCREDIT = os.getenv("DEPOSIT_AUTOCREDIT", "false").lower() == "true"


def _solana_pay_url(amount: float, memo: str) -> str:
    params = urllib.parse.urlencode({
        "amount": f"{amount:.2f}",
        "spl-token": USDC_MINT,
        "memo": memo,
        "label": "WorldPool Deposit",
    })
    return f"solana:{OPERATOR_WALLET}?{params}"


async def _poll_deposit(bot: TelegramClient, chat_id: int, tg_user_id: int,
                        amount: float, db, rpc_url: str) -> None:
    """Poll Solana RPC for deposit confirmation via memo matching."""
    import httpx
    elapsed = 0
    while elapsed < POLL_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            # Simplified: check recent transactions on operator wallet for memo=tg_user_id
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [OPERATOR_WALLET, {"limit": 5}],
                })
                sigs = r.json().get("result", [])
            for sig in sigs:
                memo = sig.get("memo", "") or ""
                signature = sig.get("signature", "")
                # Exact memo match (was substring — user 12 matched "123") + per-signature dedup
                # so one on-chain tx cannot be replayed into multiple credits.
                if memo.strip().endswith(str(tg_user_id)) and str(tg_user_id) in memo.split():
                    if signature and await is_processed(db, signature):
                        continue
                    if signature:
                        await mark_processed(db, signature)
                    await credit_balance(db, tg_user_id, amount)
                    user = await get_user(db, tg_user_id)
                    await bot.send_message(chat_id, deposit_confirmed(amount, user["usdc_balance"]), parse_mode="md")
                    return
        except Exception:
            pass

    await bot.send_message(chat_id,
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
        pay_url = _solana_pay_url(amount, memo)

        await event.answer()
        await event.respond(
            deposit_pending(amount, pay_url),
            parse_mode="md",
            link_preview=False,
        )
        # Auto-credit is OFF by default (unsafe memo-based detection — see AUTOCREDIT note).
        # When disabled, the operator credits the balance manually after verifying the transfer.
        if AUTOCREDIT:
            asyncio.create_task(
                _poll_deposit(client, event.chat_id, user.id, amount, db, rpc_url)
            )
        else:
            await event.respond(
                "ℹ️ After payment, your balance is credited once the operator confirms the "
                "transfer on\\-chain \\(usually within a few minutes\\)\\.",
                parse_mode="md",
            )
