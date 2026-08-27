"""
Unit Tests for Polymarket Bot V4: High-Odds Trend Strategy
(84¢ Entry Trigger, 99¢ TP Limit, 40¢ SL Slippage-Protected Exit)
"""

import time
import pytest
import sqlite3
from unittest.mock import MagicMock
from src.config import config
from src.execution.strategy import V4OddsStrategy, V4LiveExecutionStrategy


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Positions (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Candle_Start TEXT,
            Slug TEXT,
            Token_Id TEXT,
            Prediction_Side TEXT,
            Position_Side TEXT,
            Entry_Timestamp TEXT,
            Target_Price REAL,
            Target_Quantity REAL,
            Filled_Quantity REAL,
            Average_Fill_Price REAL,
            Order_Id TEXT,
            Buy_Order_Id TEXT,
            Sell_Order_Id TEXT,
            Position_Status TEXT,
            Cancel_Reason TEXT,
            Take_Profit_Price REAL,
            Stop_Loss_Price REAL,
            Exit_Price REAL,
            Exit_Timestamp TEXT,
            Exit_Reason TEXT,
            Trade_Outcome TEXT,
            High_Water_Mark REAL,
            Sell_Quantity REAL,
            Pnl REAL,
            Updated_At TEXT
        );
    """)
    conn.commit()
    return conn


def test_v4_entry_odds_threshold_trigger(memory_db):
    """
    Verify V4 only triggers when odds >= 0.84 and rejects anything below 0.84.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    # 1. Ask is $0.83 (< 0.84 threshold) -> NO ENTRY
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.82, 0.83)
    assert res is None
    assert strat.active_position is None

    # 2. Ask crosses to $0.84 (>= 0.84 threshold) -> ENTRY TRIGGERED!
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.83, 0.84)
    assert res is not None
    assert strat.active_position is not None
    assert strat.active_position["Position_Status"] == "PENDING_FILL"
    assert strat.active_position["Target_Buy_Price"] == 0.84
    assert strat.active_position["Target_Quantity"] == max(5.0, round(4.0 / 0.84, 4))


def test_v4_tp_limit_placement_at_99(memory_db):
    """
    Verify V4 sets TP at $0.99 and places resting Limit Sell order at $0.99 upon fill.
    """
    live_strat = V4LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    live_strat.clob_client = mock_clob

    candle_start = "2026-08-05 00:05:00"
    slug = "btc-updown-5m-1785830300"
    token_id = "TOK_UP_2"

    mock_clob.post_order.return_value = {"orderID": "0xTP_ORDER_99"}
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "5.0",
        "makingAmount": "4.25",
        "takingAmount": "5.0",
        "price": "0.8500"
    }

    # Setup PENDING_FILL position
    now_sec = time.time()
    live_strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_id,
        "Position_Side": "UP",
        "Position_Status": "PENDING_FILL",
        "Target_Buy_Price": 0.85,
        "Target_Quantity": 5.0,
        "Filled_Quantity": 0.0,
        "Buy_Order_Id": "0xBUY_V4_1",
        "Order_Timestamp_Sec": now_sec,
    }

    # Tick arrives confirming fill
    live_strat.process_tick(candle_start, slug, "UP", token_id, 0.85, 0.86)

    pos = live_strat.dry_strategy.active_position
    assert pos is not None
    assert pos["Position_Status"] == "OPEN"
    assert pos["Average_Fill_Price"] == 0.8500
    assert pos["Take_Profit_Price"] == 0.9900
    assert pos["Stop_Loss_Price"] == 0.4000
    assert pos["Tp_Order_Id"] == "0xTP_ORDER_99"


def test_v4_sl_trigger_at_40_cents(memory_db):
    """
    Verify V4 triggers Stop Loss when price drops <= $0.40, cancels TP order, and exits with slippage.
    """
    live_strat = V4LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    live_strat.clob_client = mock_clob

    candle_start = "2026-08-05 00:10:00"
    slug = "btc-updown-5m-1785830600"
    token_id = "TOK_DOWN_1"

    now_sec = time.time()
    live_strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_id,
        "Position_Side": "DOWN",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.85,
        "Average_Fill_Price": 0.85,
        "Target_Quantity": 5.0,
        "Filled_Quantity": 5.0,
        "Take_Profit_Price": 0.99,
        "Stop_Loss_Price": 0.40,
        "High_Water_Mark": 0.85,
        "Tp_Order_Id": "0xTP_RESTING",
        "Tp_Qty": 5.0,
        "Order_Timestamp_Sec": now_sec,
    }

    mock_clob.cancel_orders.return_value = {"canceled": ["0xTP_RESTING"]}
    mock_clob.post_order.return_value = {"orderID": "0xSL_ORDER_EXIT"}
    mock_clob.get_order.return_value = {"status": "MATCHED", "size_matched": "5.0", "makingAmount": "5.0", "takingAmount": "1.85"}

    # Price drops to $0.39 (<= $0.40 SL trigger)
    live_strat.process_tick(candle_start, slug, "DOWN", token_id, 0.39, 0.40)

    # Position must transition through CLOSING to CLOSED
    assert live_strat.dry_strategy.active_position is None


def test_v4_single_active_position_guard(memory_db):
    """
    Verify Single Position Guard blocks concurrent positions while one is open.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    candle_start = "2026-08-05 00:15:00"
    slug = "btc-updown-5m-1785830900"

    strat.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": "TOK_1",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.85,
        "Filled_Quantity": 5.0,
        "Take_Profit_Price": 0.99,
        "Stop_Loss_Price": 0.40
    }

    # Second token crosses $0.86 threshold -> MUST BE BLOCKED
    res = strat.process_tick(candle_start, slug, "DOWN", "TOK_2", 0.85, 0.86)
    assert res is None
    assert strat.active_position["Token_Id"] == "TOK_1"
