"""
Unit Tests for Polymarket Bot V2 Execution Strategy (V2OddsMomentumStrategy)
"""

import time
import sqlite3
import pytest
from src.database.schema import create_tables
from src.database.connection import PolyDBManager, AsyncDBWriter
from src.execution.strategy import V2OddsMomentumStrategy
from src.config import config

@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()

def test_v2_minimum_odds_floor_filter(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-07-31 17:00:00"
    slug = "btc-updown-5m-1785517200"

    now_sec = time.time()
    
    # 1. Signal at $0.55 (Surge +0.15 >= 0.15, but Ask $0.55 < $0.65 floor) -> NO TRADE
    strat.tick_buffers["TOK_LOW"] = [(now_sec - 10.0, 0.39, 0.40)]
    pos_low = strat.process_tick(candle_start, slug, "UP", "TOK_LOW", 0.54, 0.55)
    assert pos_low is None

    # 2. Signal at $0.70 (Surge +0.16 >= 0.15 AND Ask $0.70 >= $0.65 floor) -> ENTER TRADE
    strat.tick_buffers["TOK_HIGH"] = [(now_sec - 10.0, 0.53, 0.54)]
    pos_high = strat.process_tick(candle_start, slug, "UP", "TOK_HIGH", 0.69, 0.70)
    assert pos_high is not None
    assert pos_high["Entry_Odds"] == 0.70

def test_v2_momentum_trigger_and_tp_sl_calculation(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-07-31 17:00:00"
    slug = "btc-updown-5m-1785517200"
    token_id = "TOK_UP_123"

    now_sec = time.time()
    
    # 1. Feed tick 10 seconds ago at $0.50
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    # 2. Feed tick now at $0.66 (+0.16 shift >= +0.15 AND $0.66 >= $0.65) -> Places PENDING_FILL at Limit $0.64
    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.65, 0.66)
    assert pos is not None
    assert pos["Position_Status"] == "PENDING_FILL"

    # 3. Seller hits $0.64 bid -> Fills position to OPEN
    strat.process_tick(candle_start, slug, "UP", token_id, 0.64, 0.65)
    pos = strat.active_position
    assert pos["Position_Status"] == "OPEN"
    assert pos["Average_Fill_Price"] == 0.64
    assert pos["Take_Profit_Price"] == round(0.64 + getattr(config, "v2_take_profit_cents", 0.05), 4)
    assert pos["Stop_Loss_Price"] == 0.55
    assert strat.active_position is not None

def test_v2_high_odds_tp_target(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-07-31 17:05:00"
    slug = "btc-updown-5m-1785517500"
    token_id = "TOK_UP_99"

    now_sec = time.time()
    
    # Tick 10s ago at $0.64
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.63, 0.64)]

    # Tick now at $0.82 (+0.18 shift >= +0.15) -> Places PENDING_FILL at Limit $0.80 (>= $0.80 cutoff)
    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.81, 0.82)
    assert pos is not None

    # Seller hits $0.80 bid -> Fills position to OPEN
    strat.process_tick(candle_start, slug, "UP", token_id, 0.80, 0.81)
    pos = strat.active_position

    assert pos["Entry_Odds"] == 0.82
    assert pos["Take_Profit_Price"] == 0.995  # Fixed $0.995 target for entry >= $0.80 cutoff
    assert pos["Stop_Loss_Price"] == 0.71  # Initial SL from peak HWM = $0.81

def test_v2_single_position_guard(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-07-31 17:10:00"
    slug = "btc-updown-5m-1785517800"

    now_sec = time.time()
    token_up = "TOK_UP"
    token_dn = "TOK_DN"
    strat.tick_buffers[token_up] = [(now_sec - 10.0, 0.49, 0.50)]
    strat.tick_buffers[token_dn] = [(now_sec - 10.0, 0.49, 0.50)]

    pos_up = strat.process_tick(candle_start, slug, "UP", token_up, 0.69, 0.70)
    assert pos_up is not None

    pos_dn = strat.process_tick(candle_start, slug, "DOWN", token_dn, 0.74, 0.75)
    assert pos_dn is None

def test_v2_tp_and_sl_exits(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-07-31 17:12:00"
    slug = "btc-updown-5m-1785517900"
    token_id = "TOK_EXIT_TEST"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)
    assert pos is not None

    # 2. Fill position at bid $0.68 -> OPEN
    strat.process_tick(candle_start, slug, "UP", token_id, 0.68, 0.69)
    assert strat.active_position["Position_Status"] == "OPEN"

    # 3. Feed tick below SL ($0.54 <= $0.58) -> Instantly closed with STOP_LOSS_HIT
    strat.process_tick(candle_start, slug, "UP", token_id, 0.54, 0.55)
    assert strat.active_position is None

def test_v2_candle_expiry_rollover(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_1 = "2026-07-31 17:15:00"
    candle_2 = "2026-07-31 17:20:00"
    slug_1 = "btc-updown-5m-1785518100"
    slug_2 = "btc-updown-5m-1785518400"
    token_1 = "TOK_CANDLE_1"
    token_2 = "TOK_CANDLE_2"

    now_sec = time.time()
    strat.tick_buffers[token_1] = [(now_sec - 10.0, 0.49, 0.50)]

    # Enter position in Candle 1 & Fill it to OPEN
    pos_1 = strat.process_tick(candle_1, slug_1, "UP", token_1, 0.64, 0.65)
    strat.process_tick(candle_1, slug_1, "UP", token_1, 0.63, 0.64)
    assert pos_1 is not None
    assert strat.active_position is not None

    # Candle 2 starts (new candle start timestamp). Position from Candle 1 should auto-expire & unlock!
    strat.tick_buffers[token_2] = [(now_sec - 10.0, 0.49, 0.50)]
    pos_2 = strat.process_tick(candle_2, slug_2, "DOWN", token_2, 0.65, 0.66)

    # Candle 2 trade should successfully enter!
    assert pos_2 is not None
    assert pos_2["Candle_Start"] == candle_2
    assert (pos_2.get("Position_Side") or pos_2.get("Prediction_Side")) == "DOWN"

def test_v2_mid_candle_dip_below_sl_and_expiry_validation(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_1 = "2026-08-03 23:00:00"
    slug_1 = "btc-updown-5m-1785800000"
    token_id = "TOK_HIGH_ODDS"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.63, 0.64)]

    # Enter position at Ask $0.80 -> Limit $0.78 (PENDING_FILL)
    pos = strat.process_tick(candle_1, slug_1, "UP", token_id, 0.79, 0.80)
    assert pos is not None

    # Fill position at bid $0.78 -> OPEN (SL = $0.69)
    strat.process_tick(candle_1, slug_1, "UP", token_id, 0.78, 0.79)
    assert strat.active_position["Position_Status"] == "OPEN"

    # Mid-candle tick dips to $0.68 (breaching SL $0.69)
    strat.process_tick(candle_1, slug_1, "UP", token_id, 0.68, 0.69)
    
    # Assert position was instantly closed as STOP_LOSS_HIT
    assert strat.active_position is None

def test_v2_trailing_stop_loss_hwm(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle = "2026-08-04 00:00:00"
    slug = "btc-updown-5m-1785804000"
    token_id = "TOK_TRAIL_1"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    # 1. Entry at $0.66 (Initial SL = $0.56, TP = $0.995 for test)
    pos = strat.execute_entry_v2(candle, slug, "UP", token_id, 0.50, 0.66)
    pos["Take_Profit_Price"] = 0.995
    strat.active_position = pos

    assert pos["Stop_Loss_Price"] == 0.56
    assert pos["High_Water_Mark"] == 0.66

    # 2. Price rises to $0.69 (HWM = $0.69, Trailing SL updates to $0.59)
    strat.process_tick(candle, slug, "UP", token_id, 0.68, 0.69)
    assert strat.active_position["High_Water_Mark"] == 0.69
    assert strat.active_position["Stop_Loss_Price"] == 0.59

    # 3. Price rises to $0.72 (HWM = $0.72, Trailing SL updates to $0.62)
    strat.process_tick(candle, slug, "UP", token_id, 0.71, 0.72)
    assert strat.active_position["High_Water_Mark"] == 0.72
    assert strat.active_position["Stop_Loss_Price"] == 0.62

    # 4. Pullback to $0.68 (HWM stays $0.72, SL stays locked at $0.62)
    strat.process_tick(candle, slug, "UP", token_id, 0.67, 0.68)
    assert strat.active_position["High_Water_Mark"] == 0.72
    assert strat.active_position["Stop_Loss_Price"] == 0.62

    # 5. Drop to $0.61 (breaches $0.62 Trailing SL -> EXITS with STOP_LOSS_HIT)
    exit_pos = strat.process_tick(candle, slug, "UP", token_id, 0.60, 0.61)
    assert strat.active_position is None

def test_v2_high_odds_trailing_sl(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle = "2026-08-04 00:05:00"
    slug = "btc-updown-5m-1785804300"
    token_id = "TOK_TRAIL_HIGH"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.63, 0.64)]

    # Entry at Ask $0.80 -> Limit $0.78 (PENDING_FILL)
    pos = strat.process_tick(candle, slug, "UP", token_id, 0.79, 0.80)
    assert pos is not None

    # Fill position at bid $0.78 -> OPEN (SL = $0.69)
    strat.process_tick(candle, slug, "UP", token_id, 0.78, 0.79)
    assert strat.active_position["Position_Status"] == "OPEN"

    # Price advances to $0.92 (HWM = $0.92, Trailing SL updates to $0.82)
    strat.process_tick(candle, slug, "UP", token_id, 0.91, 0.92)
    assert strat.active_position["High_Water_Mark"] == 0.92
    assert strat.active_position["Stop_Loss_Price"] == 0.82

    # Pullback to $0.80 breaches $0.82 Trailing SL -> EXITS with STOP_LOSS_HIT
    strat.process_tick(candle, slug, "UP", token_id, 0.79, 0.80)
    assert strat.active_position is None
