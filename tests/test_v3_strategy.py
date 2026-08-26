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

def test_v3_live_sl_retry_and_no_synthetic_closure(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-08-05 00:20:00"
    slug = "btc-updown-5m-1785831200"
    token_id = "TOK_LIVE_SL"

    # 1. Setup OPEN position on the internal strategy
    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_id,
        "Position_Side": "DOWN",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.65,
        "Average_Fill_Price": 0.65,
        "Target_Quantity": 6.15,
        "Filled_Quantity": 6.15,
        "Take_Profit_Price": 0.74,
        "Stop_Loss_Price": 0.59,
        "High_Water_Mark": 0.65,
        "Tp_Order_Id": None,
        "Tp_Qty": 0.0,
        "Order_Timestamp_Sec": now_sec,
    }

    # Simulate post_limit_sell failing on initial SL trigger (e.g. 400 Bad Request balance: 0)
    mock_clob.post_order.side_effect = Exception("balance: 0")

    # 2. Market price crashes to $0.55 <= SL $0.59 -> SL Trigger fires
    strat.process_tick(candle_start, slug, "DOWN", token_id, 0.55, 0.56)

    # Position MUST be in CLOSING state and Sell_Order_Id must be None (NEVER falsely marked CLOSED!)
    assert strat.dry_strategy.active_position is not None
    assert strat.dry_strategy.active_position["Position_Status"] == "CLOSING"
    assert strat.dry_strategy.active_position["Sell_Order_Id"] is None

    # 3. Simulate next tick: Polygon balance settles, post_order now returns 200 OK with orderID
    mock_clob.post_order.side_effect = None
    mock_clob.create_order.return_value = {"signed": True}
    mock_clob.post_order.return_value = {"orderID": "0xREAL_EXCHANGE_SL_ORDER_123"}
    mock_clob.get_order.return_value = {"status": "OPEN", "size_matched": "0.0", "price": "0.53"}

    strat.process_tick(candle_start, slug, "DOWN", token_id, 0.55, 0.56)

    # Order ID is updated to the real exchange order ID and remains in CLOSING
    assert strat.dry_strategy.active_position is not None
    assert strat.dry_strategy.active_position["Position_Status"] == "CLOSING"
    assert strat.dry_strategy.active_position["Sell_Order_Id"] == "0xREAL_EXCHANGE_SL_ORDER_123"

    # 4. Exchange returns FILLED on subsequent tick with weighted average taking/making
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "6.15",
        "makingAmount": "6.15",
        "takingAmount": "3.2595", # Fill price = 3.2595 / 6.15 = 0.5300
    }

    strat.process_tick(candle_start, slug, "DOWN", token_id, 0.55, 0.56)

    # Position is now officially CLOSED on exchange confirmation!
    assert strat.dry_strategy.active_position is None

def test_v3_buy_fill_price_extraction_and_zero_balance_liquidation(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-08-05 00:25:00"
    slug = "btc-updown-5m-1785831500"
    token_id = "TOK_BUY_FILL"

    # 1. Setup PENDING_FILL position (Limit buy cap was $0.68)
    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_id,
        "Position_Side": "UP",
        "Position_Status": "PENDING_FILL",
        "Target_Buy_Price": 0.68,
        "Target_Quantity": 5.88,
        "Filled_Quantity": 0.0,
        "Buy_Order_Id": "0xBUY_ORDER_TEST",
        "Order_Timestamp_Sec": now_sec,
    }

    # Exchange reports order filled with price improvement at $0.5800!
    # makingAmount = 3.4104 USDC, takingAmount = 5.88 shares -> 3.4104 / 5.88 = 0.5800
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "5.88",
        "makingAmount": "3.4104",
        "takingAmount": "5.88",
        "price": "0.5800"
    }

    # Tick arrives at $0.57 (Below limit buy $0.68, but above new SL $0.48)
    strat.process_tick(candle_start, slug, "UP", token_id, 0.57, 0.58)

    # Position must be OPEN with Average_Fill_Price = 0.5800 and Stop_Loss_Price = 0.4800 (NOT 0.5800!)
    pos = strat.dry_strategy.active_position
    assert pos is not None
    assert pos["Position_Status"] == "OPEN"
    assert pos["Average_Fill_Price"] == 0.5800
    assert pos["Stop_Loss_Price"] == 0.4800
    assert pos["Take_Profit_Price"] == round(0.58 + getattr(config, "v2_take_profit_cents", 0.20), 4)

    # 2. Re-Chase / Zero Balance reconciliation test:
    # Transition to CLOSING with a dispatched sell order
    pos["Position_Status"] = "CLOSING"
    pos["Sell_Order_Id"] = "0xSELL_RESTING"
    pos["Sell_Order_Dispatched"] = True
    pos["Closing_Timestamp_Sec"] = now_sec - 3.5

    # Simulate re-chase cancel: Polymarket returns ZERO_BALANCE error because order already matched on book
    mock_clob.cancel_orders.return_value = {"canceled": [], "not_canceled": {"0xSELL_RESTING": "already matched"}}
    mock_clob.get_order.return_value = {"status": "MATCHED", "size_matched": "5.88", "makingAmount": "5.88", "takingAmount": "2.8812"}
    mock_clob.post_order.side_effect = Exception("balance is not enough -> balance: 0")

    strat.process_tick(candle_start, slug, "UP", token_id, 0.49, 0.50)

    # Position must be successfully marked CLOSED and cleared!
    assert strat.dry_strategy.active_position is None

def test_v3_timeout_fill_reconciliation(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-08-05 00:30:00"
    slug = "btc-updown-5m-1785831800"
    token_id = "TOK_TIMEOUT_RECON"

    # Setup PENDING_FILL position whose 5.0s timeout is about to expire
    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_id,
        "Position_Side": "DOWN",
        "Position_Status": "PENDING_FILL",
        "Target_Buy_Price": 0.75,
        "Target_Quantity": 5.33,
        "Filled_Quantity": 0.0,
        "Buy_Order_Id": "0x8740abd04381619559866b115f82c4a56394f93aa039ee7fab94660a07cc5ec5",
        "Order_Timestamp_Sec": now_sec - 5.2, # 5.2s elapsed!
    }

    # Exchange reports cancel notice says "already matched" and get_order confirms FILLED
    mock_clob.cancel_orders.return_value = {
        "canceled": [],
        "not_canceled": {"0x8740abd04381619559866b115f82c4a56394f93aa039ee7fab94660a07cc5ec5": "already canceled or matched"}
    }
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "5.33",
        "makingAmount": "3.9975",
        "takingAmount": "5.33",
        "price": "0.7500"
    }

    # Tick arrives at timeout boundary
    strat.process_tick(candle_start, slug, "DOWN", token_id, 0.74, 0.75)

    # Position MUST NOT be CANCELLED/abandoned; it MUST be transitioned to OPEN for live tracking!
    pos = strat.dry_strategy.active_position
    assert pos is not None
    assert pos["Position_Status"] == "OPEN"
    assert pos["Filled_Quantity"] == 5.33
    assert pos["Average_Fill_Price"] == 0.7500



