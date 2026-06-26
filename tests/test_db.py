"""Unit tests — DB layer: users, pools, positions, settlement."""
import asyncio
import pytest
import aiosqlite
from db.schema import init_db
from db import queries


@pytest.fixture
async def db():
    conn = await init_db(":memory:")
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_upsert_and_get_user(db):
    await queries.upsert_user(db, 12345, "alice", "So1ana...")
    user = await queries.get_user(db, 12345)
    assert user["tg_username"] == "alice"
    assert user["usdc_balance"] == 0.0


@pytest.mark.asyncio
async def test_credit_and_deduct_balance(db):
    await queries.upsert_user(db, 1, "bob")
    await queries.credit_balance(db, 1, 50.0)
    user = await queries.get_user(db, 1)
    assert user["usdc_balance"] == 50.0

    ok = await queries.deduct_balance(db, 1, 20.0)
    assert ok is True
    user = await queries.get_user(db, 1)
    assert user["usdc_balance"] == 30.0


@pytest.mark.asyncio
async def test_deduct_insufficient_balance(db):
    await queries.upsert_user(db, 2, "charlie")
    await queries.credit_balance(db, 2, 5.0)
    ok = await queries.deduct_balance(db, 2, 10.0)
    assert ok is False
    user = await queries.get_user(db, 2)
    assert user["usdc_balance"] == 5.0  # unchanged


@pytest.mark.asyncio
async def test_create_pool_and_get(db):
    await queries.upsert_user(db, 1, "alice")
    pool_id = await queries.create_pool(db, "fx_001", "wc2026", "France", "Argentina", 1,
                                        home_odds=1.85, draw_odds=3.40, away_odds=2.10)
    pool = await queries.get_pool(db, pool_id)
    assert pool["home_team"] == "France"
    assert pool["status"] == "open"


@pytest.mark.asyncio
async def test_place_position_and_settle(db):
    await queries.upsert_user(db, 1, "alice")
    await queries.credit_balance(db, 1, 100.0)
    pool_id = await queries.create_pool(db, "fx_001", "wc2026", "France", "Argentina", 1)

    await queries.deduct_balance(db, 1, 20.0)
    pos_id = await queries.place_position(db, pool_id, 1, "home", 20.0, 1.85)
    assert pos_id is not None

    # Add a second user picking away
    await queries.upsert_user(db, 2, "bob")
    await queries.credit_balance(db, 2, 100.0)
    await queries.deduct_balance(db, 2, 10.0)
    await queries.place_position(db, pool_id, 2, "away", 10.0, 2.10)

    # Settle: France wins (home)
    payouts, total_pool = await queries.mark_positions_settled(db, pool_id, "home")
    await queries.settle_pool(db, pool_id, "home")

    assert total_pool == pytest.approx(30.0, rel=0.01)  # 20 + 10
    assert len(payouts) == 1
    assert payouts[0]["tg_user_id"] == 1
    assert payouts[0]["payout"] == pytest.approx(30.0, rel=0.01)  # entire pool goes to winner


@pytest.mark.asyncio
async def test_event_deduplication(db):
    await queries.mark_processed(db, "evt_001")
    assert await queries.is_processed(db, "evt_001") is True
    assert await queries.is_processed(db, "evt_002") is False


@pytest.mark.asyncio
async def test_chat_subscriptions(db):
    await queries.upsert_user(db, 1, "alice")
    pool_id = await queries.create_pool(db, "fx_001", "wc2026", "Spain", "Brazil", 1)
    await queries.subscribe_chat(db, pool_id, -100123)
    await queries.subscribe_chat(db, pool_id, -100456)
    chats = await queries.get_subscribed_chats(db, pool_id)
    assert set(chats) == {-100123, -100456}
