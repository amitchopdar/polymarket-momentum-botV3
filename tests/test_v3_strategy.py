import time
import sqlite3
import pytest
from src.database.schema import create_tables
from src.execution.strategy import V2OddsMomentumStrategy
from src.config import config

@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()

def test_v3_maker_offset_placement(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:00:00"
    slug = "btc-updown-5m-1785830000"
    token_id = "TOK_MAKER_1"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    # Trigger V3 Maker Entry at Ask = $0.70 (+0.20 surge >= +0.15)
    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)

    assert pos is not None
    assert pos["Position_Status"] == "PENDING_FILL"
    assert pos["Entry_Odds"] == 0.70
    assert pos["Position_Side"] == "UP"
    assert pos["Target_Buy_Price"] == round(0.70 - 0.02, 4)  # $0.68 Limit Buy ($0.70 Ask - $0.02 Offset)
    assert pos["Filled_Quantity"] == 0.0
    assert strat.active_position is not None

def test_v3_successful_maker_fill(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:05:00"
    slug = "btc-updown-5m-1785830300"
    token_id = "TOK_MAKER_FILL"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    # 1. Place Limit Buy at Ask $0.70 -> Limit Buy Price = $0.68
    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)
    assert pos["Position_Status"] == "PENDING_FILL"

    # 2. Market price dips to touch $0.68 bid (bid = $0.68, ask = $0.68)
    strat.process_tick(candle_start, slug, "UP", token_id, 0.68, 0.68)

    # 3. Position fills at $0.68 and transitions to OPEN
    assert strat.active_position is not None
    assert strat.active_position["Position_Status"] == "OPEN"
    assert strat.active_position["Average_Fill_Price"] == 0.68
    assert strat.active_position["Take_Profit_Price"] == round(0.68 + getattr(config, "v2_take_profit_cents", 0.05), 4)
    assert strat.active_position["Stop_Loss_Price"] == 0.58    # HWM = $0.68 -> SL = $0.58 ($0.68 - 0.10)

def test_v3_order_timeout_cancellation(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:10:00"
    slug = "btc-updown-5m-1785830600"
    token_id = "TOK_TIMEOUT"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    # 1. Place Limit Buy at Ask $0.70 -> Limit Buy Price = $0.68 (PENDING_FILL)
    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)
    assert pos["Position_Status"] == "PENDING_FILL"

    # Fast-forward order timestamp back by 5.1 seconds to simulate timeout
    pos["Order_Timestamp_Sec"] = time.time() - 5.1

    # 2. Feed next tick (Bid $0.65 is below $0.68 -> no fill, but 5.1s timeout triggers!)
    strat.process_tick(candle_start, slug, "UP", token_id, 0.64, 0.75)

    # 3. Order is cancelled (CANCELLED_TIMEOUT) and single position guard is unlocked!
    assert strat.active_position is None

def test_v3_single_active_order_guard(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:15:00"
    slug = "btc-updown-5m-1785830900"
    token_up = "TOK_UP_GUARD"
    token_dn = "TOK_DN_GUARD"

    now_sec = time.time()
    strat.tick_buffers[token_up] = [(now_sec - 10.0, 0.49, 0.50)]
    strat.tick_buffers[token_dn] = [(now_sec - 10.0, 0.49, 0.50)]

    # 1. Place UP Limit Buy order (PENDING_FILL)
    pos_up = strat.process_tick(candle_start, slug, "UP", token_up, 0.69, 0.70)
    assert pos_up is not None
    assert pos_up["Position_Status"] == "PENDING_FILL"

    # 2. Attempt DOWN entry signal while UP order is still PENDING_FILL
    pos_dn = strat.process_tick(candle_start, slug, "DOWN", "TOK_DN_GUARD", 0.74, 0.75)

    # Single order guard must block the second order!
    assert pos_dn is None
