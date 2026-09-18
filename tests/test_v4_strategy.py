"""
Unit Tests for Polymarket Bot V4 High-Odds Trend Following Strategy Engine
Adapts dynamically to values defined in src/config.py
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
    Verify V4 triggers when odds are in [min_entry, max_entry] window and rejects outside.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0  # Cooldown passed
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    min_thresh = config.v4_entry_odds_threshold
    max_thresh = config.v4_max_entry_odds_ceiling

    # 1. Ask below min threshold -> NO ENTRY
    res = strat.process_tick(candle_start, slug, "UP", token_id, min_thresh - 0.02, min_thresh - 0.01)
    assert res is None
    assert strat.active_position is None

    # 2. Ask above max ceiling -> NO ENTRY
    res = strat.process_tick(candle_start, slug, "UP", token_id, max_thresh + 0.01, max_thresh + 0.02)
    assert res is None
    assert strat.active_position is None

    # 3. Ask in window -> ENTRY TRIGGERED!
    mid_ask = round((min_thresh + max_thresh) / 2.0, 4)
    res = strat.process_tick(candle_start, slug, "UP", token_id, mid_ask - 0.01, mid_ask)
    assert res is not None
    assert strat.active_position is not None
    assert strat.active_position["Position_Status"] == "PENDING_FILL"
    assert strat.active_position["Target_Buy_Price"] == mid_ask
    assert strat.active_position["Target_Quantity"] == max(5.0, round(config.max_position_size_usd / mid_ask, 4))


def test_v4_single_trade_per_candle_rule(memory_db):
    """
    Verify that once a trade is executed in candle X, the bot never re-enters in that same candle.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    entry_ask = config.v4_entry_odds_threshold

    # 1. First trade enters
    strat.process_tick(candle_start, slug, "UP", token_id, entry_ask - 0.01, entry_ask)
    assert strat.active_position is not None

    # 2. Trade fills
    tp_price = round(min(config.v4_take_profit_price, entry_ask + config.v4_take_profit_offset_cents), 4)
    strat.active_position["Position_Status"] = "OPEN"
    strat.active_position["Filled_Quantity"] = 5.0
    strat.active_position["Average_Fill_Price"] = entry_ask
    strat.active_position["Take_Profit_Price"] = tp_price
    strat.active_position["Stop_Loss_Price"] = config.v4_stop_loss_price

    # Price reaches TP -> TP closes the position
    strat.process_tick(candle_start, slug, "UP", token_id, tp_price, tp_price + 0.01)
    assert strat.active_position is None  # Trade closed!

    # 3. Subsequent tick in SAME candle in entry window -> MUST BE IGNORED!
    res = strat.process_tick(candle_start, slug, "UP", token_id, entry_ask - 0.01, entry_ask)
    assert res is None
    assert strat.active_position is None

    # 4. Next fresh candle arrives -> Bot allows new trade!
    next_candle = "2026-08-05 00:05:00"
    res_next = strat.process_tick(next_candle, "btc-updown-5m-1785830300", "UP", "TOK_UP_2", entry_ask - 0.01, entry_ask)
    assert res_next is not None
    assert strat.active_position is not None


def test_v4_startup_candle_cooldown(memory_db):
    """
    Verify V4 strictly ignores mid-candle signals for the candle running during bot startup.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = int(datetime.strptime("2026-08-05 00:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    entry_ask = config.v4_entry_odds_threshold
    res = strat.process_tick(candle_start, slug, "UP", token_id, entry_ask - 0.01, entry_ask)
    assert res is None
    assert strat.active_position is None


def test_v4_tp_limit_placement_dynamic_offset(memory_db):
    """
    Verify dynamic Take Profit target is placed at Entry + Offset.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    entry_ask = config.v4_entry_odds_threshold
    expected_tp = round(min(config.v4_take_profit_price, entry_ask + config.v4_take_profit_offset_cents), 4)

    # 1. Trigger Entry
    strat.process_tick(candle_start, slug, "UP", token_id, entry_ask - 0.01, entry_ask)
    assert strat.active_position is not None

    # 2. Fill order
    strat.process_tick(candle_start, slug, "UP", token_id, entry_ask, entry_ask + 0.01)
    assert strat.active_position["Position_Status"] == "OPEN"
    assert strat.active_position["Take_Profit_Price"] == expected_tp
    assert strat.active_position["Stop_Loss_Price"] == config.v4_stop_loss_price


def test_v4_sl_trigger_at_sl_price(memory_db):
    """
    Verify Stop Loss triggers immediately when price drops <= stop_loss_price.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_1"

    entry_ask = config.v4_entry_odds_threshold
    sl_price = config.v4_stop_loss_price

    # 1. Open Position
    strat.process_tick(candle_start, slug, "UP", token_id, entry_ask - 0.01, entry_ask)
    strat.process_tick(candle_start, slug, "UP", token_id, entry_ask, entry_ask + 0.01)
    assert strat.active_position["Position_Status"] == "OPEN"

    # 2. Price drops to SL price -> STOP LOSS EXECUTES!
    strat.process_tick(candle_start, slug, "UP", token_id, sl_price - 0.01, sl_price)
    assert strat.active_position is None


def test_v4_single_active_position_guard(memory_db):
    """
    Verify bot will NEVER open a second position while one is active.
    """
    strat = V4OddsStrategy(async_writer=None, notifier=None)
    strat.boot_candle_sec = 0
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"

    entry_ask = config.v4_entry_odds_threshold

    # Open UP position
    strat.process_tick(candle_start, slug, "UP", "TOK_UP_1", entry_ask - 0.01, entry_ask)
    assert strat.active_position is not None

    # DOWN ask in entry window -> REJECTED because UP is active
    res = strat.process_tick(candle_start, slug, "DOWN", "TOK_DN_1", entry_ask - 0.01, entry_ask)
    assert res is None
