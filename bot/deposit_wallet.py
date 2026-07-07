"""Per-user Solana deposit address — closes the CSO CRITICAL wallet-spoofing
finding (2026-07-07) by making deposit identity the receiving address itself,
never a client-supplied claim.

Each Telegram user gets their own dedicated USDC-receiving address. Its keypair
is deterministically re-derived on demand from the operator keyfile
(OPERATOR_KEYPAIR_PATH) + the user's Telegram ID — it is NEVER stored anywhere.
Only the resulting public address is written to the DB. Anyone who wants to
recreate a given user's deposit keypair must already have the operator keyfile,
which is the one secret this whole bot already depends on end to end — no new
credential-storage surface is introduced.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import logging
import os

import httpx
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from spl.token.instructions import (
    create_idempotent_associated_token_account,
    get_associated_token_address,
    transfer_checked,
    TransferCheckedParams,
)

from db.queries import get_user, set_deposit_address, upsert_user

logger = logging.getLogger(__name__)

USDC_MINT = os.getenv("USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
USDC_DECIMALS = 6
_DERIVE_LABEL = b"worldpool-deposit-v1"


def _load_operator_keypair() -> Keypair:
    path = os.getenv("OPERATOR_KEYPAIR_PATH", "keys/txline-dev.json")
    with open(path) as f:
        secret = json.load(f)
    return Keypair.from_bytes(bytes(secret))


def derive_deposit_keypair(operator_kp: Keypair, tg_user_id: int) -> Keypair:
    """Deterministic, never-persisted per-user deposit keypair. Same operator
    keyfile + same tg_user_id always reproduces the same address."""
    seed = hmac.new(
        bytes(operator_kp)[:32],  # operator's own secret half, never exposed
        _DERIVE_LABEL + str(tg_user_id).encode(),
        hashlib.sha256,
    ).digest()
    return Keypair.from_seed(seed)


async def _get_latest_blockhash(rpc_url: str) -> Hash:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "getLatestBlockhash", "params": [],
        })
    return Hash.from_string(r.json()["result"]["value"]["blockhash"])


async def _send_raw_transaction(rpc_url: str, tx: Transaction) -> str:
    b64 = base64.b64encode(bytes(tx)).decode()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [b64, {"encoding": "base64"}],
        })
    result = r.json()
    if "error" in result:
        raise RuntimeError(f"sendTransaction failed: {result['error']}")
    return result["result"]


async def get_token_balance(rpc_url: str, ata: Pubkey) -> int:
    """Raw USDC balance (base units) of a token account. 0 if it doesn't exist."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountBalance",
            "params": [str(ata)],
        })
    result = r.json().get("result")
    if not result:
        return 0
    return int(result["value"]["amount"])


async def ensure_deposit_address(db, tg_user_id: int, rpc_url: str) -> str:
    """Return this user's dedicated deposit address. On first call, derives the
    keypair, creates its USDC associated-token-account on-chain (rent paid by
    the operator), and stores the (public) address."""
    user = await get_user(db, tg_user_id)
    if user and user["deposit_address"]:
        return user["deposit_address"]

    # Some entry points (e.g. the main-menu "Deposit" button, misc.py) reach
    # here without having called upsert_user first — without this, the UPDATE
    # in set_deposit_address silently affects 0 rows and the address is never
    # actually persisted, surfacing later as a crash when crediting tries to
    # read a user row that doesn't exist. Caught live 2026-07-07.
    if not user:
        await upsert_user(db, tg_user_id)

    operator_kp = _load_operator_keypair()
    user_kp = derive_deposit_keypair(operator_kp, tg_user_id)
    mint = Pubkey.from_string(USDC_MINT)
    owner = user_kp.pubkey()
    address = str(owner)

    ix = create_idempotent_associated_token_account(
        payer=operator_kp.pubkey(), owner=owner, mint=mint,
    )
    blockhash = await _get_latest_blockhash(rpc_url)
    msg = Message.new_with_blockhash([ix], operator_kp.pubkey(), blockhash)
    tx = Transaction([operator_kp], msg, blockhash)
    sig = await _send_raw_transaction(rpc_url, tx)
    logger.info("Created deposit ATA: user=%s address=%s sig=%s", tg_user_id, address, sig)

    await set_deposit_address(db, tg_user_id, address)
    return address


async def sweep_deposit(db, tg_user_id: int, rpc_url: str, amount_ui: float, operator_wallet: str) -> str:
    """Move a just-confirmed deposit from the user's dedicated address into the
    shared operator escrow wallet. Re-derives the user's keypair on demand —
    nothing new is read from storage to do this."""
    operator_kp = _load_operator_keypair()
    user_kp = derive_deposit_keypair(operator_kp, tg_user_id)
    mint = Pubkey.from_string(USDC_MINT)

    source_ata = get_associated_token_address(user_kp.pubkey(), mint)
    dest_owner = Pubkey.from_string(operator_wallet)
    dest_ata = get_associated_token_address(dest_owner, mint)

    amount_base_units = round(amount_ui * (10 ** USDC_DECIMALS))

    ensure_dest_ix = create_idempotent_associated_token_account(
        payer=operator_kp.pubkey(), owner=dest_owner, mint=mint,
    )
    transfer_ix = transfer_checked(TransferCheckedParams(
        program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
        source=source_ata,
        mint=mint,
        dest=dest_ata,
        owner=user_kp.pubkey(),
        amount=amount_base_units,
        decimals=USDC_DECIMALS,
        signers=[],
    ))

    blockhash = await _get_latest_blockhash(rpc_url)
    # operator pays fees + rent-if-needed; user_kp signs the transfer authority
    msg = Message.new_with_blockhash([ensure_dest_ix, transfer_ix], operator_kp.pubkey(), blockhash)
    tx = Transaction([operator_kp, user_kp], msg, blockhash)
    sig = await _send_raw_transaction(rpc_url, tx)
    logger.info("Swept deposit: user=%s amount=%.2f sig=%s", tg_user_id, amount_ui, sig)
    return sig
