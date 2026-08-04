"""
Unit & Integration Tests for Polymarket Token Resolver (US1.3.1)
"""

import os
import pytest
from src.database.connection import PolyDBManager, AsyncDBWriter
from src.polymarket.token_resolver import PolymarketTokenResolver

TEST_DB_PATH = "test_polymarket_PolyDB.sqlite"

@pytest.fixture(autouse=True)
def clean_test_db():
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    wal_file = f"{TEST_DB_PATH}-wal"
    shm_file = f"{TEST_DB_PATH}-shm"
    if os.path.exists(wal_file):
        os.remove(wal_file)
    if os.path.exists(shm_file):
        os.remove(shm_file)

    yield

    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    if os.path.exists(wal_file):
        os.remove(wal_file)
    if os.path.exists(shm_file):
        os.remove(shm_file)


def test_us1_3_1_preflight_token_resolution_and_slug_generation(monkeypatch):
    """
    US1.3.1 Verification: T-5s Pre-Flight token resolution & slug format.
    """
    resolver = PolymarketTokenResolver()
    target_ts = 1700000000000

    slug = resolver.generate_expected_slug(target_ts)
    assert "btc-updown-5m-" in slug

    # Mock Polymarket API response for offline unit test
    mock_payload = [{
        "slug": slug,
        "markets": [{
            "clobTokenIds": ["tok_up_test_123", "tok_down_test_123"],
            "outcomePrices": ["0.55", "0.45"],
            "volume": 1250.0
        }]
    }]

    class MockResponse:
        status_code = 200
        def json(self):
            return mock_payload

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockResponse())

    res = resolver.resolve_next_candle_tokens(target_ts)
    assert res is not None
    res_slug, up_tok, dn_tok = res
    assert res_slug == slug
    assert up_tok == "tok_up_test_123"
    assert dn_tok == "tok_down_test_123"
    assert resolver.cached_open_prices[str(target_ts)] == (0.55, 0.45)


def test_us1_3_1_t0_fallback_retry(monkeypatch):
    """
    US1.3.1 Verification: T+0s Fallback retry and Fail-Fast handling.
    """
    resolver = PolymarketTokenResolver()
    target_ts = 1700000300000

    # 1. Test Fail-Fast when API returns no events
    class MockEmptyResponse:
        status_code = 200
        def json(self):
            return []

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockEmptyResponse())
    res = resolver.retry_fallback_at_t0(target_ts)
    assert res is None

    # 2. Test Success when API returns valid contract
    mock_payload = [{
        "slug": resolver.generate_expected_slug(target_ts),
        "markets": [{
            "clobTokenIds": ["tok_up_fb", "tok_down_fb"],
            "outcomePrices": ["0.51", "0.49"],
            "volume": 500.0
        }]
    }]

    class MockValidResponse:
        status_code = 200
        def json(self):
            return mock_payload

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: MockValidResponse())
    res_valid = resolver.retry_fallback_at_t0(target_ts)
    assert res_valid is not None
    assert str(target_ts) in resolver.cached_tokens


def test_us1_3_1_record_odds_ohclv_db_persistence():
    """
    US1.3.1 Verification: Odds_OHCLV database table persistence & Status recording.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH)
    db_mgr.init_db()

    writer = AsyncDBWriter(db_mgr)
    writer.start()

    resolver = PolymarketTokenResolver()
    candle_start = "2026-07-22 00:00:00"

    up_ohclv = (0.50, 0.65, 0.48, 0.60, 1500.0)
    dn_ohclv = (0.50, 0.52, 0.35, 0.40, 1500.0)

    resolver.record_odds_ohclv(
        candle_start=candle_start,
        up_token_id="tok_up_test",
        down_token_id="tok_dn_test",
        up_ohclv=up_ohclv,
        down_ohclv=dn_ohclv,
        status="RESOLVED",
        async_writer=writer
    )

    # Test API_FAILURE status record
    fail_candle = "2026-07-22 00:05:00"
    resolver.record_odds_ohclv(
        candle_start=fail_candle,
        up_token_id="FETCH_FAILED",
        down_token_id="FETCH_FAILED",
        status="API_FAILURE",
        async_writer=writer
    )

    writer.stop(timeout=5.0)

    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM Odds_OHCLV WHERE Candle_Start = ?", (candle_start,))
        row = cursor.fetchone()
        assert row is not None
        assert row["Up_Token_Id"] == "tok_up_test"
        assert row["Up_Close"] == 0.60
        assert row["Down_Close"] == 0.40
        assert row["Status"] == "RESOLVED"

        cursor.execute("SELECT * FROM Odds_OHCLV WHERE Candle_Start = ?", (fail_candle,))
        fail_row = cursor.fetchone()
        assert fail_row is not None
        assert fail_row["Up_Token_Id"] == "FETCH_FAILED"
        assert fail_row["Up_Close"] is None
        assert fail_row["Status"] == "API_FAILURE"


def test_polymarket_ws_client():
    """
    Verification of PolymarketWSClient frame parsing and live bid/ask tracking.
    """
    from src.polymarket.polymarket_ws import PolymarketWSClient

    ws_client = PolymarketWSClient()
    ws_client.subscribe_tokens("tok_up_123", "tok_dn_123")

    # Simulate WS book snapshot message
    book_msg = '{"asset_id": "tok_up_123", "bids": [{"price": "0.52"}], "asks": [{"price": "0.53"}]}'
    ws_client._process_message(book_msg)

    up_b, up_a, dn_b, dn_a = ws_client.get_live_bid_ask()
    assert up_b == 0.52
    assert up_a == 0.53
    assert dn_b == 0.47
    assert dn_a == 0.48

    # Simulate WS level price change event
    change_msg = '{"price_changes": [{"asset_id": "tok_up_123", "price": "0.55", "side": "BUY"}]}'
    ws_client._process_message(change_msg)

    up_b2, up_a2, dn_b2, dn_a2 = ws_client.get_live_bid_ask()
    assert up_b2 == 0.55
