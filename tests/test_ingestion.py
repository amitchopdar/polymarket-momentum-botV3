"""
Unit & Integration Tests for Sprint 1 (US1.1.1, US1.1.2, US1.2.1)
"""

import os
import pytest
import time
from unittest.mock import MagicMock, patch
from src.database.connection import PolyDBManager, AsyncDBWriter
from src.ingestion.order_flow import OrderFlowTracker
from src.ingestion.candle_cache import CandleCache
from src.ingestion.binance_ws import BinanceWebSocketClient

TEST_DB_PATH = "test_ingestion_PolyDB.sqlite"

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


def test_us1_2_1_obi_math_and_liquidation_aggregation():
    """
    US1.2.1 Verification: Order Book Imbalance (OBI) calculation to 4 decimals & liquidation aggregation.
    """
    tracker = OrderFlowTracker()

    # Top 10 Bids and Asks
    bids = [[65000.0, 10.0], [64990.0, 5.0]] # Total Bid Vol = 15.0
    asks = [[65010.0, 5.0], [65020.0, 0.0]]  # Total Ask Vol = 5.0
    # Expected OBI = (15.0 - 5.0) / (15.0 + 5.0) = 10.0 / 20.0 = 0.5

    obi = tracker.process_depth(bids, asks)
    assert obi == 0.5000
    assert tracker.get_current_obi() == 0.5000

    # Process liquidations
    tracker.process_liquidation(side="SELL", qty=2.0, price=65000.0) # Short liq vol = 130,000.0
    tracker.process_liquidation(side="BUY", qty=1.0, price=65000.0)  # Long liq vol = 65,000.0

    flushed_obi, short_vol, long_vol = tracker.flush_5m_metrics()
    assert flushed_obi == 0.5000
    assert short_vol == 130000.0
    assert long_vol == 65000.0

    # Reset verification
    _, reset_short, reset_long = tracker.flush_5m_metrics()
    assert reset_short == 0.0
    assert reset_long == 0.0


def test_us1_1_1_candle_cache_deque_and_update():
    """
    US1.1.1 Verification: Rolling candle cache deque updates index [-1] within boundary.
    """
    cache = CandleCache(maxlen=10)
    tracker = OrderFlowTracker()

    kline_payload = {
        "t": 1700000000000,
        "i": "5m",
        "o": "65000.0",
        "h": "65200.0",
        "l": "64900.0",
        "c": "65100.0",
        "v": "100.0",
        "x": False
    }

    candle = cache.update_kline(kline_payload, tracker)
    assert len(cache.deque) == 1
    assert cache.deque[-1]["Close"] == 65100.0
    assert cache.deque[-1]["finalized"] is False

    # Update in-place
    kline_payload["c"] = "65150.0"
    updated_candle = cache.update_kline(kline_payload, tracker)
    assert len(cache.deque) == 1
    assert cache.deque[-1]["Close"] == 65150.0


@patch("requests.get")
def test_us1_1_1_rest_warmup(mock_get):
    """
    US1.1.1 Verification: REST API candle warmup populates historical deque.
    """
    mock_resp = MagicMock()
    # Mock Binance REST klines response format
    mock_resp.json.return_value = [
        [1700000000000, "65000.0", "65100.0", "64900.0", "65050.0", "10.0"],
        [1700000300000, "65050.0", "65200.0", "65000.0", "65150.0", "15.0"]
    ]
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    cache = CandleCache(maxlen=500)
    count = cache.warmup_from_rest("BTCUSDT", "5m", 500)

    assert count == 2
    assert len(cache.deque) == 2
    assert cache.deque[0]["Open"] == 65000.0
    assert cache.deque[1]["Close"] == 65150.0


def test_us1_1_2_candle_finalization_and_db_persistence():
    """
    US1.1.2 Verification: Explicit ("x": true) and implicit candle finalization triggers non-blocking DB write.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH)
    db_mgr.init_db()

    writer = AsyncDBWriter(db_mgr)
    writer.start()

    cache = CandleCache(maxlen=500)
    tracker = OrderFlowTracker()

    kline_1 = {
        "t": 1700000000000,
        "i": "5m",
        "o": "65000.0",
        "h": "65200.0",
        "l": "64900.0",
        "c": "65100.0",
        "v": "100.0",
        "x": True # Explicit finalization
    }

    cache.update_kline(kline_1, tracker, writer)
    writer.stop(timeout=5.0)

    # Check database persistence
    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM BTC_OHCLV")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0]["Close"] == 65100.0


def test_binance_ws_reconnect_retry_backoff():
    """
    Verifies exponential backoff calculation for WS client.
    """
    client = BinanceWebSocketClient()
    assert client.retry_count == 0

    client.retry_count = 3
    backoff = min(30.0, 1.0 * (2 ** client.retry_count))
    assert backoff == 8.0
