"""
Unit Tests for SettlementTracker and Synthetic Stop-Loss (Method 3) in Polymarket Bot V3.
"""

import time
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from src.database.schema import create_tables
from src.database.connection import PolyDBManager, AsyncDBWriter
from src.execution.strategy import V2OddsMomentumStrategy, LiveExecutionStrategy
from src.execution.settlement_tracker import SettlementTracker
from src.config import config


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()


def test_fixed_share_based_sizing(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_UP_SHARES"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.44, 0.45)]

    # Set fixed share count to 10.0 shares
    config.trade_size_shares = 10.0

    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)
    assert pos is not None
    assert pos["Target_Quantity"] == 10.0  # Exact 10.0 shares!
    assert pos["Target_Buy_Price"] == 0.71


def test_synthetic_stop_loss_hedge_lock(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:05:00"
    slug = "btc-updown-5m-1785830300"
    up_tok = "TOK_UP_HEDGE"
    dn_tok = "TOK_DN_HEDGE"

    now_sec = time.time()
    strat.tick_buffers[up_tok] = [(now_sec - 10.0, 0.44, 0.45)]

    config.trade_size_shares = 7.0
    config.stop_loss_mode = "SYNTHETIC_HEDGE"

    # 1. Enter UP at Ask $0.70 -> Limit $0.71 (PENDING_FILL with Opposite_Token_Id)
    pos = strat.process_tick(candle_start, slug, "UP", up_tok, 0.69, 0.70, opposite_token_id=dn_tok)
    assert pos["Position_Status"] == "PENDING_FILL"
    assert pos["Opposite_Token_Id"] == dn_tok

    # 2. Market tick fills UP at $0.70 -> Transitions to OPEN
    strat.process_tick(candle_start, slug, "UP", up_tok, 0.69, 0.70, opposite_token_id=dn_tok)
    assert strat.active_position["Position_Status"] == "OPEN"
    assert strat.active_position["Stop_Loss_Price"] == 0.63  # $0.70 - 0.07 trailing SL

    # 3. Market crashes: Bid drops to $0.62 (<= $0.63 Stop Loss)
    # Strategy MUST execute Synthetic Hedge: Buy 7.0 shares of DOWN and lock position!
    strat.process_tick(candle_start, slug, "UP", up_tok, 0.62, 0.63, opposite_token_id=dn_tok)

    active_pos = strat.active_position
    assert active_pos is not None
    assert active_pos["Position_Status"] == "HEDGED_LOCKED"
    assert active_pos["Hedge_Token_Id"] == dn_tok
    assert active_pos["Hedge_Quantity"] == 7.0
    assert active_pos["Hedge_Buy_Price"] == 0.37  # 1.00 - 0.63 = 0.37
    # Primary Cost: 7.0 * 0.70 = 4.90, Hedge Cost: 7.0 * 0.37 = 2.59 -> Total: 7.49
    # Guaranteed Settlement Payout: 7.0 * 1.00 = 7.00 -> Locked PnL = 7.00 - 7.49 = -0.49
    assert active_pos["Pnl"] == round(7.00 - (4.90 + 2.59), 4)


def test_hedged_position_prevents_recursive_orders_and_new_entries(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:10:00"
    slug = "btc-updown-5m-1785830600"
    up_tok = "TOK_UP_RECUR"
    dn_tok = "TOK_DN_RECUR"

    strat.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": up_tok,
        "Opposite_Token_Id": dn_tok,
        "Position_Side": "UP",
        "Position_Status": "HEDGED_LOCKED",
        "Target_Quantity": 7.0,
        "Filled_Quantity": 7.0,
        "Hedge_Quantity": 7.0,
        "Average_Fill_Price": 0.70,
        "Hedge_Buy_Price": 0.37,
        "Stop_Loss_Price": 0.63,
        "Take_Profit_Price": 0.85,
        "Pnl": -0.49
    }

    # 1. Feed further price drop on UP token (Bid $0.50 < $0.63)
    strat.process_tick(candle_start, slug, "UP", up_tok, 0.49, 0.50, opposite_token_id=dn_tok)
    # Position status MUST remain HEDGED_LOCKED without making any duplicate orders!
    assert strat.active_position["Position_Status"] == "HEDGED_LOCKED"
    assert strat.active_position["Pnl"] == -0.49

    # 2. Feed massive breakout surge on DOWN token (+0.30 surge)
    now_sec = time.time()
    strat.tick_buffers[dn_tok] = [(now_sec - 10.0, 0.30, 0.31)]
    pos_dn = strat.process_tick(candle_start, slug, "DOWN", dn_tok, 0.74, 0.75, opposite_token_id=up_tok)
    # Single Position guard must BLOCK any new trade entry while HEDGED_LOCKED!
    assert pos_dn is None


def test_candle_rollover_hands_off_to_settlement_tracker(memory_db):
    mock_tracker = MagicMock()
    strat = V2OddsMomentumStrategy(async_writer=None, settlement_tracker=mock_tracker)

    candle_1 = "2026-08-05 00:15:00"
    candle_2 = "2026-08-05 00:20:00"
    slug_1 = "btc-updown-5m-1785830900"
    slug_2 = "btc-updown-5m-1785831200"

    strat.active_position = {
        "Candle_Start": candle_1,
        "Slug": slug_1,
        "Token_Id": "TOK_UP_1",
        "Position_Side": "UP",
        "Position_Status": "HEDGED_LOCKED",
        "Filled_Quantity": 7.0,
        "Hedge_Quantity": 7.0,
        "Pnl": -0.49
    }

    # Tick arrives from Candle 2
    strat.tick_buffers["TOK_UP_2"] = [(time.time() - 10.0, 0.44, 0.45)]
    pos_2 = strat.process_tick(candle_2, slug_2, "UP", "TOK_UP_2", 0.69, 0.70, opposite_token_id="TOK_DN_2")

    # 1. HEDGED_LOCKED trade was handed off to Settlement Tracker
    mock_tracker.enqueue_settlement.assert_called_once()
    # 2. self.active_position was reset and new trade in Candle 2 was allowed to enter with ZERO delay!
    assert pos_2 is not None
    assert pos_2["Candle_Start"] == candle_2
    assert pos_2["Position_Status"] == "PENDING_FILL"
