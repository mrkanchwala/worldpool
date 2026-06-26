"""Tests for wallet + Kamino leverage features (T1–T10)."""
from __future__ import annotations
import asyncio
import json
import re
import sys
import types
import pytest
import aiosqlite

from db.schema import init_db
from db import queries


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    conn = await init_db(":memory:")
    yield conn
    await conn.close()


# ── T1: Wallet registration stored in DB ─────────────────────────────────────

@pytest.mark.asyncio
async def test_wallet_registration(db):
    """T1: upsert_user stores solana_wallet; get_user retrieves it."""
    await queries.upsert_user(db, 1001, "alice", "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")
    user = await queries.get_user(db, 1001)
    assert user["solana_wallet"] == "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


@pytest.mark.asyncio
async def test_wallet_update(db):
    """T1b: Registering a new wallet overwrites the old one."""
    await queries.upsert_user(db, 1002, "bob", "So1anaWa11etAddressXXXXXXXXXXXXXXXXXXXXXXXX")
    await queries.upsert_user(db, 1002, solana_wallet="NewWa11etAddressYYYYYYYYYYYYYYYYYYYYYYYYY1")
    user = await queries.get_user(db, 1002)
    assert user["solana_wallet"] == "NewWa11etAddressYYYYYYYYYYYYYYYYYYYYYYYYY1"


# ── T2: Address validation ─────────────────────────────────────────────────────

def _is_valid(addr: str) -> bool:
    """Replicate wallet.py validation logic."""
    return bool(re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$').match(addr))


def test_valid_solana_address():
    """T2a: Well-formed 44-char base58 address passes."""
    assert _is_valid("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM") is True


def test_valid_short_address():
    """T2b: Minimum-length valid address (32 chars) passes."""
    assert _is_valid("1" * 32) is True


def test_invalid_address_too_short():
    """T2c: 31 chars → rejected."""
    assert _is_valid("1" * 31) is False


def test_invalid_address_contains_0():
    """T2d: '0' is not in base58 alphabet → rejected."""
    assert _is_valid("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAW0M") is False


def test_invalid_address_contains_O():
    """T2e: 'O' is not in base58 alphabet → rejected."""
    assert _is_valid("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWOM") is False


# ── T3: Leverage position CRUD ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_leverage_position(db):
    """T3a: create_leverage_position → get_open_leverage_positions returns it."""
    await queries.upsert_user(db, 2001, "carol")
    lev_id = await queries.create_leverage_position(db, 2001, 25.0, "KaminoTxSig123")
    assert lev_id is not None

    positions = await queries.get_open_leverage_positions(db, 2001)
    assert len(positions) == 1
    assert positions[0]["borrow_amount"] == pytest.approx(25.0)
    assert positions[0]["repaid"] == 0


@pytest.mark.asyncio
async def test_mark_leverage_repaid(db):
    """T3b: mark_leverage_repaid → position no longer in open list."""
    await queries.upsert_user(db, 2002, "dave")
    lev_id = await queries.create_leverage_position(db, 2002, 10.0)
    await queries.mark_leverage_repaid(db, lev_id, "RepayTxSig456")

    positions = await queries.get_open_leverage_positions(db, 2002)
    assert positions == []


@pytest.mark.asyncio
async def test_multiple_leverage_positions(db):
    """T3c: Two open borrows → both returned; repaying one leaves one open."""
    await queries.upsert_user(db, 2003, "eve")
    id1 = await queries.create_leverage_position(db, 2003, 5.0)
    id2 = await queries.create_leverage_position(db, 2003, 15.0)

    open_pos = await queries.get_open_leverage_positions(db, 2003)
    assert len(open_pos) == 2

    await queries.mark_leverage_repaid(db, id1)
    open_pos = await queries.get_open_leverage_positions(db, 2003)
    assert len(open_pos) == 1
    assert open_pos[0]["lev_id"] == id2


# ── T4: Kamino subprocess mock — info mode (happy path) ───────────────────────

@pytest.mark.asyncio
async def test_call_kamino_info_ok(monkeypatch):
    """T4: _call_kamino info mode returns parsed JSON when subprocess succeeds."""
    from bot.handlers import leverage as lev_module

    mock_result = {
        "ok": True,
        "collateral_usd": 100.0,
        "borrowed_usd": 0.0,
        "available_usd": 75.0,
        "ltv_pct": 0.0,
        "borrow_apy_pct": 9.8,
        "obligation": "ObligationPDA123",
    }

    class _FakeProc:
        async def communicate(self):
            return (json.dumps(mock_result).encode(), b"")

    async def _fake_exec(*args, stdout=None, stderr=None, env=None):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = await lev_module._call_kamino("info", "FakeWallet111111111111111111111111111111")
    assert result["ok"] is True
    assert result["available_usd"] == pytest.approx(75.0)
    assert result["borrow_apy_pct"] == pytest.approx(9.8)


# ── T5: Kamino subprocess — no obligation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_call_kamino_no_obligation(monkeypatch):
    """T5: _call_kamino returns ok=False with reason=no_obligation when wallet has no position."""
    from bot.handlers import leverage as lev_module

    mock_result = {"ok": False, "reason": "no_obligation", "message": "No Kamino lending position."}

    class _FakeProc:
        async def communicate(self):
            return (json.dumps(mock_result).encode(), b"")

    async def _fake_exec(*args, stdout=None, stderr=None, env=None):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = await lev_module._call_kamino("info", "FakeWallet111111111111111111111111111111")
    assert result["ok"] is False
    assert result["reason"] == "no_obligation"


# ── T6: Kamino subprocess — borrow mode (happy path) ──────────────────────────

@pytest.mark.asyncio
async def test_call_kamino_borrow_ok(monkeypatch):
    """T6: _call_kamino borrow mode returns tx_base64 + phantom_url."""
    from bot.handlers import leverage as lev_module

    mock_result = {
        "ok": True,
        "amount_usdc": 10.0,
        "tx_base64": "AAAAAAAAAAAAAAAA==",
        "phantom_url": "https://phantom.app/ul/v1/signAndSendTransaction?transaction=AAAAAAAAAAAAAAAA%3D%3D&cluster=mainnet-beta",
        "estimated_apy_pct": 9.5,
    }

    class _FakeProc:
        async def communicate(self):
            return (json.dumps(mock_result).encode(), b"")

    async def _fake_exec(*args, stdout=None, stderr=None, env=None):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = await lev_module._call_kamino("borrow", "FakeWallet111111111111111111111111111111", 10.0)
    assert result["ok"] is True
    assert "tx_base64" in result
    assert result["phantom_url"].startswith("https://phantom.app/ul/v1/")


# ── T7: Borrow exceeds capacity ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_kamino_exceeds_limit(monkeypatch):
    """T7: _call_kamino returns ok=False when borrow amount exceeds available capacity."""
    from bot.handlers import leverage as lev_module

    mock_result = {
        "ok": False,
        "reason": "exceeds_limit",
        "message": "Requested $1000.00 exceeds available borrow capacity $75.00.",
    }

    class _FakeProc:
        async def communicate(self):
            return (json.dumps(mock_result).encode(), b"")

    async def _fake_exec(*args, stdout=None, stderr=None, env=None):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = await lev_module._call_kamino("borrow", "FakeWallet111111111111111111111111111111", 1000.0)
    assert result["ok"] is False
    assert result["reason"] == "exceeds_limit"


# ── T8: Repayment reminder trigger ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_repayment_reminder_detected(db):
    """T8: After settlement, open leverage positions are detectable for reminder."""
    await queries.upsert_user(db, 3001, "frank")
    pool_id = await queries.create_pool(db, "fx_rep_001", "wc2026", "England", "Spain", 3001)

    # User places a bet and has open leverage
    await queries.credit_balance(db, 3001, 50.0)
    await queries.deduct_balance(db, 3001, 20.0)
    await queries.place_position(db, pool_id, 3001, "home", 20.0, 2.0)
    await queries.create_leverage_position(db, 3001, 20.0, "KaminoBorrowTx")

    # Settle pool
    payouts, total_pool = await queries.mark_positions_settled(db, pool_id, "home")
    assert payouts[0]["tg_user_id"] == 3001

    # Confirm leverage position still open (not auto-repaid)
    open_borrows = await queries.get_open_leverage_positions(db, 3001)
    assert len(open_borrows) == 1
    total_owed = sum(b["borrow_amount"] for b in open_borrows)
    assert total_owed == pytest.approx(20.0)


# ── T9: Phantom Universal Link format ─────────────────────────────────────────

def test_phantom_universal_link_format():
    """T9: phantom_universal_link produces correct Phantom UL URL."""
    from bot.handlers.deposit import phantom_universal_link
    import urllib.parse

    tx_b64 = "AQAAAAAAAAAA=="
    url = phantom_universal_link(tx_b64, "mainnet-beta")
    assert url.startswith("https://phantom.app/ul/v1/signAndSendTransaction")
    assert "cluster=mainnet-beta" in url
    assert urllib.parse.quote(tx_b64, safe="") in url


def test_phantom_universal_link_devnet():
    """T9b: phantom_universal_link respects cluster parameter."""
    from bot.handlers.deposit import phantom_universal_link

    url = phantom_universal_link("AQAA==", "devnet")
    assert "cluster=devnet" in url


# ── T10: SPL transfer verification ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spl_verify_missing_memo(monkeypatch):
    """T10a: _verify_spl_transfer returns False when memo doesn't contain tg_user_id."""
    import httpx
    import bot.handlers.deposit as deposit_mod
    from bot.handlers.deposit import _verify_spl_transfer

    test_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    test_wallet = "OperatorWalletXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
    monkeypatch.setattr(deposit_mod, "OPERATOR_WALLET", test_wallet)
    monkeypatch.setattr(deposit_mod, "USDC_MINT", test_mint)

    # Valid SPL transfer but memo contains wrong user ID
    mock_tx = {
        "result": {
            "meta": {"err": None},
            "transaction": {
                "message": {
                    "instructions": [
                        {
                            "program": "spl-memo",
                            "parsed": "999999",  # wrong user ID
                        },
                        {
                            "program": "spl-token",
                            "parsed": {
                                "type": "transferChecked",
                                "info": {
                                    "mint": test_mint,
                                    "destination": test_wallet,
                                    "tokenAmount": {"uiAmount": 10.0},
                                },
                            },
                        },
                    ]
                }
            },
        }
    }

    class _FakeResp:
        def json(self): return mock_tx

    class _FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    result = await _verify_spl_transfer("https://fake.rpc", "SomeSig", 10.0, 12345)
    assert result is False  # memo mismatch


@pytest.mark.asyncio
async def test_spl_verify_happy_path(monkeypatch):
    """T10b: _verify_spl_transfer returns True when memo + transfer both match."""
    import httpx
    import bot.handlers.deposit as deposit_mod
    from bot.handlers.deposit import _verify_spl_transfer

    tg_user_id = 12345
    test_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    test_wallet = "OperatorWalletXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

    # Patch module-level constants (set at import time from env)
    monkeypatch.setattr(deposit_mod, "OPERATOR_WALLET", test_wallet)
    monkeypatch.setattr(deposit_mod, "USDC_MINT", test_mint)

    mock_tx = {
        "result": {
            "meta": {"err": None},
            "transaction": {
                "message": {
                    "instructions": [
                        {
                            "program": "spl-memo",
                            "parsed": str(tg_user_id),
                        },
                        {
                            "program": "spl-token",
                            "parsed": {
                                "type": "transferChecked",
                                "info": {
                                    "mint": test_mint,
                                    "destination": test_wallet,
                                    "tokenAmount": {"uiAmount": 10.0},
                                },
                            },
                        },
                    ]
                }
            },
        }
    }

    class _FakeResp:
        def json(self): return mock_tx

    class _FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient())

    result = await _verify_spl_transfer("https://fake.rpc", "SomeSig", 10.0, tg_user_id)
    assert result is True
