"""
Unit Tests for Polymarket Bot V4 High-Odds Trend Following Strategy Engine
Tests:
1. Entry Trigger at >= 84¢ and <= 88¢ odds window.
2. Entry Rejection when price is above 88¢ ceiling ($0.90+).
3. Startup Candle Cooldown (no mid-candle entries on boot).
4. Single Trade Per Candle Rule (no duplicate re-entries in the same candle after Take Profit).
5. Take Profit Resting Limit Sell at 99¢.
6. Stop Loss Trigger at <= 40¢ with dynamic slippage.
7. Single Active Position Guard.
"""

import os
import time
import pytest
import sqlite3
from datetime import datetime, timezone
from src.config import config
from src.execution.strategy import V4OddsStrategy


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE Positions (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Candle_Start TEXT NOT NULL,
            Slug TEXT NOT NULL,
            Token_Id TEXT NOT NULL,
            Prediction_Side TEXT,
            Position_Side TEXT,
            Prob_Cal REAL,
            Prob_Uncal REAL,
            Target_Buy_Price REAL,
            Average_Fill_Price REAL,
            Target_Quantity REAL,
            Filled_Quantity REAL,
            Sell_Quantity REAL,
            Take_Profit_Price REAL,
            Stop_Loss_Price REAL,
            High_Water_Mark REAL,
            Exit_Price REAL,
            Exit_Reason TEXT,
            Trade_Outcome TEXT,
            Entry_Timestamp TEXT,
            Exit_Timestamp TEXT,
            Buy_Order_Id TEXT,
            Sell_Order_Id TEXT,
            Position_Status TEXT,
            Cancel_Reason TEXT,
            Pnl REAL,
            Updated_At TEXT
        );
    """)
    conn.commit()
    yield conn
    conn.close()


def test_v4_entry_odds_threshold_and_ceiling(memory_db):
    """
    Verify V4 only triggers when odds are between 84¢ and 88¢ and rejects anything < 0.84 or > 0.88.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0  # Cooldown passed
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    # 1. Ask is $0.83 (< 0.84 threshold) -> NO ENTRY
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.82, 0.83)
    assert res is None
    assert strat.active_position is None

    # 2. Ask is $0.90 (> 0.88 ceiling) -> NO ENTRY (Prevents buying at the top)
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.89, 0.90)
    assert res is None
    assert strat.active_position is None

    # 3. Ask is $0.85 (in 0.84-0.88 window) -> ENTRY TRIGGERED!
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.84, 0.85)
    assert res is not None
    assert strat.active_position is not None
    assert strat.active_position["Position_Status"] == "PENDING_FILL"
    assert strat.active_position["Target_Buy_Price"] == 0.85
    assert strat.active_position["Target_Quantity"] == max(5.0, round(getattr(config, "max_position_size_usd", 5.0) / 0.85, 4))


def test_v4_single_trade_per_candle_rule(memory_db):
    """
    Verify that once a trade is executed in candle X, the bot never re-enters in that same candle.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    # 1. First trade enters
    strat.process_tick(candle_start, slug, "UP", token_id, 0.84, 0.85)
    assert strat.active_position is not None

    # 2. Trade fills & hits Take Profit
    strat.active_position["Position_Status"] = "OPEN"
    strat.active_position["Filled_Quantity"] = 5.0
    strat.active_position["Average_Fill_Price"] = 0.85
    strat.active_position["Take_Profit_Price"] = 0.99
    strat.active_position["Stop_Loss_Price"] = 0.40

    # Price reaches $0.99 -> TP closes the position
    strat.process_tick(candle_start, slug, "UP", token_id, 0.99, 1.00)
    assert strat.active_position is None  # Trade closed!

    # 3. Subsequent tick in SAME candle at $0.85 -> MUST BE IGNORED!
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.84, 0.85)
    assert res is None
    assert strat.active_position is None

    # 4. Next fresh candle arrives -> Bot allows new trade!
    next_candle = "2026-08-05 00:05:00"
    res_next = strat.process_tick(next_candle, "btc-updown-5m-1785830300", "UP", "TOK_UP_2", 0.84, 0.85)
    assert res_next is not None
    assert strat.active_position is not None


def test_v4_startup_candle_cooldown(memory_db):
    """
    Verify V4 strictly ignores mid-candle signals for the candle running during bot startup.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = int(datetime.strptime("2026-08-05 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())  # Bot booted during this candle
    candle_start = "2026-08-05 00:00:00"  # matches boot_candle_sec
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    # Ask is 0.86 (normally valid trigger), but during boot candle -> SKIPPED!
    res = strat.process_tick(candle_start, slug, "UP", token_id, 0.85, 0.86)
    assert res is None
    assert strat.active_position is None


def test_v4_tp_limit_placement_at_99(memory_db):
    """
    Verify resting Limit Sell is configured at $0.99 upon fill.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    # 1. Trigger Entry
    strat.process_tick(candle_start, slug, "UP", token_id, 0.83, 0.84)
    assert strat.active_position is not None

    # 2. Fill order at $0.84
    strat.process_tick(candle_start, slug, "UP", token_id, 0.84, 0.85)
    assert strat.active_position["Position_Status"] == "OPEN"
    assert strat.active_position["Take_Profit_Price"] == 0.99
    assert strat.active_position["Stop_Loss_Price"] == 0.40


def test_v4_sl_trigger_at_40_cents(memory_db):
    """
    Verify Stop Loss triggers immediately when price drops <= 0.40.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    # 1. Open Position
    strat.process_tick(candle_start, slug, "UP", token_id, 0.83, 0.84)
    strat.process_tick(candle_start, slug, "UP", token_id, 0.84, 0.85)
    assert strat.active_position["Position_Status"] == "OPEN"

    # 2. Price drops to $0.40 -> STOP LOSS EXECUTES!
    strat.process_tick(candle_start, slug, "UP", token_id, 0.39, 0.40)
    # Since dry run closes synchronously
    assert strat.active_position is None


def test_v4_single_active_position_guard(memory_db):
    """
    Verify bot will NEVER open a second position while one is active.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"

    # Open UP position
    strat.process_tick(candle_start, slug, "UP", "TOK_UP_1", 0.83, 0.84)
    assert strat.active_position is not None

    # DOWN ask reaches 0.86 in same or other candle -> REJECTED because UP is active
    res = strat.process_tick(candle_start, slug, "DOWN", "TOK_DN_1", 0.85, 0.86)
    assert res is None
