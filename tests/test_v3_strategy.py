import time
import sqlite3
import pytest
from src.database.schema import create_tables
from unittest.mock import MagicMock
from src.execution.strategy import V2OddsMomentumStrategy, LiveExecutionStrategy
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
    assert pos["Target_Buy_Price"] == round(0.70 + 0.01, 4)  # $0.71 Limit Buy ($0.70 Ask + $0.01 Buffer)
    assert pos["Filled_Quantity"] == 0.0
    assert strat.active_position is not None

def test_v3_successful_maker_fill(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-08-05 00:05:00"
    slug = "btc-updown-5m-1785830300"
    token_id = "TOK_MAKER_FILL"

    now_sec = time.time()
    strat.tick_buffers[token_id] = [(now_sec - 10.0, 0.49, 0.50)]

    # 1. Place Limit Buy at Ask $0.70 -> Limit Buy Price = $0.71
    pos = strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)
    assert pos["Position_Status"] == "PENDING_FILL"

    # 2. Immediate fill at Ask $0.70 (effective price $0.70 <= $0.71 Limit Price)
    strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)

    # 3. Position fills at $0.70 and transitions to OPEN
    assert strat.active_position is not None
    assert strat.active_position["Position_Status"] == "OPEN"
    assert strat.active_position["Average_Fill_Price"] == 0.70
    assert strat.active_position["Take_Profit_Price"] == round(0.70 + getattr(config, "v2_take_profit_cents", 0.20), 4)
    assert strat.active_position["Stop_Loss_Price"] == round(0.70 - getattr(config, "v2_trailing_sl_distance_cents", 0.07), 4)

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
    assert pos["Stop_Loss_Price"] == round(0.5800 - getattr(config, "v2_trailing_sl_distance_cents", 0.07), 4)
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


def test_v3_db_persistence_and_telegram_pnl_summary(tmp_path):
    import sqlite3
    from unittest.mock import MagicMock
    from src.database.connection import PolyDBManager, AsyncDBWriter
    from src.execution.strategy import LiveExecutionStrategy
    from src.notifications.telegram_bot import TelegramCommandRouter
    from src.notifications.notifier import TelegramNotifier

    db_path = str(tmp_path / "test_pnl.sqlite")
    db_mgr = PolyDBManager(db_path=db_path)
    async_writer = AsyncDBWriter(db_mgr)
    async_writer.start()

    notifier = MagicMock()
    strat = LiveExecutionStrategy(async_writer=async_writer, notifier=notifier)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-08-05 00:40:00"
    slug = "btc-updown-5m-1785832400"
    token_id = "TOK_PNL_TEST"

    # 1. Simulate entry trigger in LIVE mode
    mock_clob.create_order.return_value = {"signed": True}
    mock_clob.post_order.return_value = {"orderID": "0xBUY_LIVE_123"}

    # Push historical ticks to generate momentum jump
    for i in range(12):
        strat.dry_strategy.process_tick(candle_start, slug, "UP", token_id, 0.50, 0.50)
    # Jump to 0.70 (20c jump)
    strat.process_tick(candle_start, slug, "UP", token_id, 0.69, 0.70)

    # Allow async writer to flush
    time.sleep(0.3)

    # Verify PENDING_FILL was inserted into DB
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT Buy_Order_Id, Position_Status, Target_Buy_Price FROM Positions WHERE Buy_Order_Id = '0xBUY_LIVE_123';")
    row = c.fetchone()
    assert row is not None
    assert row[0] == "0xBUY_LIVE_123"
    assert row[1] == "PENDING_FILL"
    conn.close()

    # 2. Simulate Order Fill on exchange
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "5.0",
        "makingAmount": "3.40",
        "takingAmount": "5.0",
        "price": "0.6800"
    }
    strat.process_tick(candle_start, slug, "UP", token_id, 0.67, 0.68)
    time.sleep(0.3)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT Position_Status, Average_Fill_Price, Filled_Quantity FROM Positions WHERE Buy_Order_Id = '0xBUY_LIVE_123';")
    row = c.fetchone()
    assert row is not None
    assert row[0] == "OPEN"
    assert row[1] == 0.6800
    assert row[2] == 5.0
    conn.close()

    # 3. Simulate Stop Loss Trigger & Exit Fill (Loss: Entry $0.68 -> Exit $0.58)
    mock_clob.post_order.return_value = {"orderID": "0xSELL_SL_123"}
    # Exchange confirms sell fill at $0.56
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "5.0",
        "makingAmount": "5.0",
        "takingAmount": "2.80", # Real exit price = 2.80 / 5.0 = 0.56
    }
    # Price crashes to $0.55 <= SL $0.58
    strat.process_tick(candle_start, slug, "UP", token_id, 0.55, 0.56)
    time.sleep(0.3)

    # Check DB for CLOSED position and negative PnL
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT Position_Status, Exit_Price, Pnl, Trade_Outcome FROM Positions WHERE Buy_Order_Id = '0xBUY_LIVE_123';")
    row = c.fetchone()
    assert row is not None
    assert row[0] == "CLOSED"
    assert row[1] == 0.56
    # PnL = (0.56 - 0.68) * 5.0 = -0.60
    assert round(row[2], 2) == -0.60
    assert row[3] == "LOSS"
    conn.close()

    # 4. Verify Telegram PnL Summary
    router = TelegramCommandRouter(notifier, db_path=db_path)
    summary = router._calculate_pnl_summary()
    assert summary["total"] == 1
    assert summary["closed"] == 1
    assert summary["wins"] == 0
    assert summary["losses"] == 1
    assert summary["win_rate"] == 0.0
    assert round(summary["total_pnl"], 2) == -0.60

    async_writer.stop()


def test_v3_timeout_cancel_match_and_token_balance_recovery(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-09-18 10:05:00"
    slug = "btc-updown-5m-1789712700"
    token_id = "TOK_MATCHED_AT_TIMEOUT"

    # Setup PENDING_FILL position whose 5.0s timeout is triggered
    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_id,
        "Position_Side": "UP",
        "Position_Status": "PENDING_FILL",
        "Target_Buy_Price": 0.72,
        "Target_Quantity": 6.9444,
        "Filled_Quantity": 0.0,
        "Buy_Order_Id": "0xb43cd9ce73ffde2a5299ce9d1693ef81a3f711f10488421f01030025dbaf9dd0",
        "Order_Timestamp_Sec": now_sec - 5.2, # 5.2s elapsed!
    }

    # 1. Simulate exact Polymarket cancel response: "order can't be found - already canceled or matched"
    mock_clob.cancel_orders.return_value = {
        "canceled": [],
        "not_canceled": {
            "0xb43cd9ce73ffde2a5299ce9d1693ef81a3f711f10488421f01030025dbaf9dd0": "order can't be found - already canceled or matched"
        }
    }
    # get_order returns empty or delayed data
    mock_clob.get_order.return_value = {"status": "CANCELED", "size_matched": "0.0"}
    # BUT get_balance_allowance confirms user holds 6.9444 shares!
    mock_clob.get_balance_allowance.return_value = {"balance": "6944400", "allowance": "10000000"}

    # Tick arrives at timeout boundary
    strat.process_tick(candle_start, slug, "UP", token_id, 0.71, 0.72)

    # Position MUST NOT be CANCELLED/abandoned; it MUST be transitioned to OPEN with Stop Loss active!
    pos = strat.dry_strategy.active_position
    assert pos is not None
    assert pos["Position_Status"] == "OPEN"
    assert pos["Filled_Quantity"] == 6.9444
    assert pos["Average_Fill_Price"] == 0.7200
    assert pos["Stop_Loss_Price"] == round(0.7200 - getattr(config, "v2_trailing_sl_distance_cents", 0.07), 4)

    # 2. Price crashes to $0.60 <= Stop Loss $0.62 -> Stop Loss MUST trigger!
    mock_clob.post_order.return_value = {"orderID": "0xSL_EXIT_999"}
    mock_clob.get_order.return_value = {
        "status": "FILLED",
        "size_matched": "6.9444",
        "makingAmount": "6.9444",
        "takingAmount": "4.1666", # Real exit price = 4.1666 / 6.9444 = 0.6000
    }
    # Once sold, exchange token balance becomes 0
    mock_clob.get_balance_allowance.return_value = {"balance": "0"}

    strat.process_tick(candle_start, slug, "UP", token_id, 0.59, 0.60)

    # Position is successfully closed at Stop Loss and cleared!
    assert strat.dry_strategy.active_position is None


def test_v3_synthetic_hedge_instant_fill(memory_db):
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-09-18 11:00:00"
    slug = "btc-updown-5m-1789716000"
    token_up = "TOK_UP_HEDGE"
    token_dn = "TOK_DN_HEDGE"

    # Setup an OPEN position on UP
    strat.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.70,
        "Average_Fill_Price": 0.70,
        "Target_Quantity": 7.0,
        "Filled_Quantity": 7.0,
        "Take_Profit_Price": 0.90,
        "Stop_Loss_Price": 0.63,
        "High_Water_Mark": 0.70,
        "Tp_Order_Id": "0xTP_RESTING_123",
        "Order_Timestamp_Sec": time.time(),
    }

    # Price drops to $0.62 <= SL $0.63
    strat.process_tick(candle_start, slug, "UP", token_up, 0.61, 0.62)

    # Position must transition to HEDGED_LOCKED
    pos = strat.active_position
    assert pos is not None
    assert pos["Position_Status"] == "HEDGED_LOCKED"
    assert pos["Hedge_Token_Id"] == token_dn
    assert pos["Hedge_Quantity"] == 7.0
    assert pos["Hedge_Buy_Price"] == 0.37  # 1.00 - 0.63
    # PnL = (1.00 * 7.0) - (0.70 * 7.0 + 0.37 * 7.0) = 7.0 - (4.90 + 2.59) = 7.0 - 7.49 = -0.49
    assert pos["Pnl"] == -0.49
    assert pos["Exit_Reason"] == "SYNTHETIC_HEDGE_LOCK"


def test_v3_synthetic_hedge_rejection_with_bounce(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-09-18 11:05:00"
    slug = "btc-updown-5m-1789716300"
    token_up = "TOK_UP_REJ_BOUNCE"
    token_dn = "TOK_DN_REJ_BOUNCE"

    # Setup OPEN position
    pos = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.70,
        "Average_Fill_Price": 0.70,
        "Target_Quantity": 7.0,
        "Filled_Quantity": 7.0,
        "Take_Profit_Price": 0.90,
        "Stop_Loss_Price": 0.63,
        "High_Water_Mark": 0.70,
        "Tp_Order_Id": "0xTP_RESTING_REJ",
        "Order_Timestamp_Sec": time.time(),
    }
    strat.dry_strategy.active_position = pos

    # Simulate CLOB error on hedge buy dispatch
    mock_clob.create_order.return_value = {"signed": True}
    mock_clob.post_order.side_effect = Exception("HTTP 400 Bad Request: order size below min tick")

    # Primary token price bounced back to Bid $0.66 > SL $0.63
    res = strat.dry_strategy.execute_synthetic_hedge_exit(pos, current_bid=0.66, current_ask=0.67)

    # Position must NOT be liquidated; it must bounce back to OPEN!
    assert res is not None
    assert res["Position_Status"] == "OPEN"
    assert res["Hedge_Order_Id"] is None


def test_v3_synthetic_hedge_rejection_without_bounce(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-09-18 11:10:00"
    slug = "btc-updown-5m-1789716600"
    token_up = "TOK_UP_REJ_NOBOUNCE"
    token_dn = "TOK_DN_REJ_NOBOUNCE"

    pos = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.70,
        "Average_Fill_Price": 0.70,
        "Target_Quantity": 7.0,
        "Filled_Quantity": 7.0,
        "Take_Profit_Price": 0.90,
        "Stop_Loss_Price": 0.63,
        "High_Water_Mark": 0.70,
        "Tp_Order_Id": "0xTP_RESTING_NOB",
        "Order_Timestamp_Sec": time.time(),
    }
    strat.dry_strategy.active_position = pos

    mock_clob.create_order.return_value = {"signed": True}
    mock_clob.post_order.side_effect = Exception("HTTP 500 Network Error")

    # Primary token price still <= SL ($0.60 <= $0.63)
    res = strat.dry_strategy.execute_synthetic_hedge_exit(pos, current_bid=0.60, current_ask=0.61)

    # Strategy must execute EMERGENCY_DIRECT_SL and transition to CLOSING
    assert res is not None
    assert res["Position_Status"] == "CLOSING"
    assert res["Exit_Reason"] == "EMERGENCY_DIRECT_SL"


def test_v3_synthetic_hedge_timeout_with_bounce(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-09-18 11:15:00"
    slug = "btc-updown-5m-1789716900"
    token_up = "TOK_UP_TO_BOUNCE"
    token_dn = "TOK_DN_TO_BOUNCE"

    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "PENDING_HEDGE",
        "Target_Buy_Price": 0.70,
        "Average_Fill_Price": 0.70,
        "Target_Quantity": 7.0,
        "Filled_Quantity": 7.0,
        "Take_Profit_Price": 0.90,
        "Stop_Loss_Price": 0.63,
        "Hedge_Token_Id": token_dn,
        "Hedge_Order_Id": "0xHEDGE_UNFILLED_1",
        "Hedge_Timestamp_Sec": now_sec - 2.5,  # 2.5s elapsed (> 2.0s timeout)
        "Hedge_Quantity": 7.0,
        "Hedge_Buy_Price": 0.37,
    }

    # Hedge order unfilled and 0 balance
    mock_clob.get_balance_allowance.return_value = {"balance": "0"}
    mock_clob.get_order.return_value = {"status": "OPEN", "size_matched": "0.0"}

    # Primary token bounced up to Bid $0.68 > SL $0.63
    strat.process_tick(candle_start, slug, "UP", token_up, 0.68, 0.69)

    # Strategy must cancel hedge order and revert position to OPEN!
    mock_clob.cancel_orders.assert_called()
    pos = strat.dry_strategy.active_position
    assert pos is not None
    assert pos["Position_Status"] == "OPEN"
    assert pos["Hedge_Order_Id"] is None


def test_v3_synthetic_hedge_timeout_without_bounce(memory_db):
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-09-18 11:20:00"
    slug = "btc-updown-5m-1789717200"
    token_up = "TOK_UP_TO_NOBOUNCE"
    token_dn = "TOK_DN_TO_NOBOUNCE"

    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "PENDING_HEDGE",
        "Target_Buy_Price": 0.70,
        "Average_Fill_Price": 0.70,
        "Target_Quantity": 7.0,
        "Filled_Quantity": 7.0,
        "Take_Profit_Price": 0.90,
        "Stop_Loss_Price": 0.63,
        "Hedge_Token_Id": token_dn,
        "Hedge_Order_Id": "0xHEDGE_UNFILLED_2",
        "Hedge_Timestamp_Sec": now_sec - 2.5,  # 2.5s elapsed (> 2.0s timeout)
        "Hedge_Quantity": 7.0,
        "Hedge_Buy_Price": 0.37,
    }

    mock_clob.get_balance_allowance.return_value = {"balance": "0"}
    mock_clob.get_order.return_value = {"status": "OPEN", "size_matched": "0.0"}

    # Primary token still <= SL ($0.58 <= $0.63)
    strat.process_tick(candle_start, slug, "UP", token_up, 0.58, 0.59)

    # Strategy must cancel hedge order and execute emergency direct sell
    mock_clob.cancel_orders.assert_called()
    pos = strat.dry_strategy.active_position
    assert pos is not None
    assert pos["Position_Status"] == "CLOSING"
    assert pos["Exit_Reason"] == "EMERGENCY_DIRECT_SL"


def test_live_execution_strategy_entry_opposite_token_and_fallback():
    """
    Verifies LiveExecutionStrategy.execute_entry accepts opposite_token_id,
    retains it across live execution dicts, forwards it during simulation fallback,
    and returns None if live order is rejected.
    """
    notifier = MagicMock()
    async_writer = MagicMock()

    # 1. Live mode placement
    strat = LiveExecutionStrategy(async_writer=async_writer, notifier=notifier)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob
    mock_clob.create_order.return_value = {"signed": True}
    mock_clob.post_order.return_value = {"orderID": "0xLIVE_123"}

    res = strat.execute_entry(
        candle_start="2026-08-05 00:40:00",
        slug="btc-updown-5m-1785832400",
        side="UP",
        prob_cal=0.50,
        prob_uncal=0.50,
        target_price=0.70,
        position_usd=5.0,
        token_id="UP_TOKEN_1",
        current_bid=0.69,
        current_ask=0.70,
        opposite_token_id="DOWN_TOKEN_1"
    )
    assert res is not None
    assert res["Opposite_Token_Id"] == "DOWN_TOKEN_1"
    assert res["Buy_Order_Id"] == "0xLIVE_123"

    # 2. Simulation fallback
    strat_dry = LiveExecutionStrategy(async_writer=async_writer, notifier=notifier)
    strat_dry.clob_client = None
    res_dry = strat_dry.execute_entry(
        candle_start="2026-08-05 00:40:00",
        slug="btc-updown-5m-1785832400",
        side="UP",
        prob_cal=0.50,
        prob_uncal=0.50,
        target_price=0.70,
        position_usd=5.0,
        token_id="UP_TOKEN_1",
        current_bid=0.69,
        current_ask=0.70,
        opposite_token_id="DOWN_TOKEN_1"
    )
    assert res_dry is not None
    assert res_dry["Opposite_Token_Id"] == "DOWN_TOKEN_1"

    # 3. Rejected live order returns None and resets active_position
    strat_fail = LiveExecutionStrategy(async_writer=async_writer, notifier=notifier)
    mock_clob_fail = MagicMock()
    strat_fail.clob_client = mock_clob_fail
    mock_clob_fail.create_order.return_value = {"signed": True}
    mock_clob_fail.post_order.return_value = None

    strat_fail.dry_strategy.tick_buffers["UP_TOK"] = [(100.0, 0.50, 0.50)] * 10
    res_fail = strat_fail.process_tick("2026-08-05 00:40:00", "btc-updown-5m-1785832400", "UP", "UP_TOK", 0.69, 0.70, opposite_token_id="DN_TOK")
    assert res_fail is None
    assert strat_fail.dry_strategy.active_position is None


def test_settlement_tracker_wiring_and_injection():
    """
    Verifies SettlementTracker is properly accepted by LiveExecutionStrategy and forwarded
    to V2OddsMomentumStrategy.
    """
    mock_tracker = MagicMock()
    mock_writer = MagicMock()
    mock_notifier = MagicMock()

    live_strat = LiveExecutionStrategy(async_writer=mock_writer, notifier=mock_notifier, settlement_tracker=mock_tracker)
    assert live_strat.settlement_tracker is mock_tracker
    assert live_strat.dry_strategy.settlement_tracker is mock_tracker


def test_telegram_enqueue_notification_alias():
    """
    Verifies TelegramNotifier implements enqueue_notification without raising AttributeError.
    """
    from src.notifications.notifier import TelegramNotifier
    notifier = TelegramNotifier(bot_token="FAKE_TOKEN", chat_id="123456")
    notifier.enqueue_notification("<b>Test Notification</b>")
    assert notifier.msg_queue.qsize() == 1


def test_candle_rollover_unlocks_position_guard_and_enqueues_settlement():
    """
    Verifies that on 5-minute candle boundary rollover:
    1. An active filled position is handed off to SettlementTracker.
    2. Any resting TP order is cancelled.
    3. active_position is set to None, unlocking the bot to trade subsequent candles without deadlock.
    """
    mock_tracker = MagicMock()
    mock_writer = MagicMock()
    mock_notifier = MagicMock()

    strat = V2OddsMomentumStrategy(async_writer=mock_writer, notifier=mock_notifier, settlement_tracker=mock_tracker)
    strat.cancel_order_on_exchange = MagicMock()

    # Simulate an active OPEN position from Candle 1
    strat.active_position = {
        "Candle_Start": "2026-08-05 00:40:00",
        "Slug": "btc-updown-5m-1785832400",
        "Token_Id": "TOK_UP_1",
        "Opposite_Token_Id": "TOK_DN_1",
        "Position_Side": "UP",
        "Position_Status": "OPEN",
        "Buy_Order_Id": "BUY_123",
        "Tp_Order_Id": "TP_456",
        "Filled_Quantity": 5.0,
        "Target_Buy_Price": 0.70
    }

    # Tick arrives for Candle 2 (different candle_start)
    res = strat._close_expired_position()

    # Verify: Handed off to settlement tracker
    mock_tracker.enqueue_settlement.assert_called_once()
    # Verify: Resting TP order cancelled
    strat.cancel_order_on_exchange.assert_called_with("TP_456")
    # Verify: active_position cleared (no deadlock)
    assert strat.active_position is None


def test_pnl_summary_includes_hedged_and_settled():
    """
    Verifies TelegramCommandRouter._calculate_pnl_summary queries HEDGED_LOCKED and RESOLVED_SETTLED.
    """
    from src.notifications.telegram_bot import TelegramCommandRouter
    import sqlite3
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
        conn = sqlite3.connect(tmp.name)
        from src.database.schema import create_tables
        create_tables(conn)

        # Insert 1 CLOSED win, 1 HEDGED_LOCKED loss, 1 RESOLVED_SETTLED win
        now_dt = "2026-08-05 00:40:00"
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Positions (Candle_Start, Slug, Token_Id, Position_Side, Entry_Timestamp, Target_Buy_Price, Target_Quantity, Position_Status, Pnl, Updated_At)
            VALUES 
                ('2026-08-05 00:40:00', 's1', 't1', 'UP', ?, 0.70, 5.0, 'CLOSED', 1.50, ?),
                ('2026-08-05 00:45:00', 's2', 't2', 'UP', ?, 0.70, 5.0, 'HEDGED_LOCKED', -0.35, ?),
                ('2026-08-05 00:50:00', 's3', 't3', 'DOWN', ?, 0.70, 5.0, 'RESOLVED_SETTLED', 2.00, ?);
        """, (now_dt, now_dt, now_dt, now_dt, now_dt, now_dt))
        conn.commit()
        conn.close()

        router = TelegramCommandRouter(MagicMock(), db_path=tmp.name)
        summary = router._calculate_pnl_summary()
        assert summary["total"] == 3
        assert summary["closed"] == 3
        assert summary["wins"] == 2
        assert summary["losses"] == 1
        assert summary["total_pnl"] == 3.15


def test_v3_marketable_taker_hedge_pricing():
    """
    Verifies that when opposite token ask is known ($0.54),
    the synthetic hedge limit buy is priced as a marketable taker at Opposite_Ask + Buffer ($0.55).
    """
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-09-18 11:00:00"
    slug = "btc-updown-5m-1789716000"
    token_up = "TOK_UP_TAKER"
    token_dn = "TOK_DN_TAKER"

    # Setup OPEN position on UP
    strat.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.71,
        "Average_Fill_Price": 0.71,
        "Target_Quantity": 5.0,
        "Filled_Quantity": 5.0,
        "Take_Profit_Price": 0.91,
        "Stop_Loss_Price": 0.62,
        "High_Water_Mark": 0.71,
        "Tp_Order_Id": "0xTP_RESTING_999",
        "Order_Timestamp_Sec": time.time(),
    }

    # Simulate price drop on UP to 0.46 (<= SL 0.62), while DOWN ask surged to 0.54
    strat.process_tick(
        candle_start, slug, "UP", token_up, 0.45, 0.46,
        opposite_token_id=token_dn, opposite_bid=0.53, opposite_ask=0.54
    )

    pos = strat.active_position
    assert pos is not None
    assert pos["Position_Status"] == "HEDGED_LOCKED"
    # Marketable Limit Buy Price = max(1 - 0.62, 0.54) + 0.01 = 0.54 + 0.01 = 0.55
    assert pos["Hedge_Limit_Buy_Price"] == 0.55
    assert pos["Hedge_Buy_Price"] == 0.54
    assert pos["Hedge_Token_Id"] == token_dn
    assert pos["Hedge_Quantity"] == 5.0


def test_v3_hedge_max_price_cap_fallback():
    """
    Verifies that when opposite token ask surges past max hedge price cap ($0.65),
    the bot skips the hedge to protect capital and liquidates directly.
    """
    strat = V2OddsMomentumStrategy(async_writer=None)
    candle_start = "2026-09-18 11:00:00"
    slug = "btc-updown-5m-1789716000"
    token_up = "TOK_UP_CAP"
    token_dn = "TOK_DN_CAP"

    strat.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "OPEN",
        "Target_Buy_Price": 0.71,
        "Average_Fill_Price": 0.71,
        "Target_Quantity": 5.0,
        "Filled_Quantity": 5.0,
        "Take_Profit_Price": 0.91,
        "Stop_Loss_Price": 0.62,
        "High_Water_Mark": 0.71,
        "Tp_Order_Id": "0xTP_RESTING_888",
        "Order_Timestamp_Sec": time.time(),
    }

    pos = strat.active_position
    # DOWN ask is 0.75 (exceeds v3_max_hedge_price of 0.65)
    strat.process_tick(
        candle_start, slug, "UP", token_up, 0.45, 0.46,
        opposite_token_id=token_dn, opposite_bid=0.74, opposite_ask=0.75
    )

    # In dry simulation mode, emergency liquidation directly closes position and clears active_position
    assert pos["Position_Status"] == "CLOSED"
    assert pos["Exit_Reason"] == "EMERGENCY_DIRECT_SL"
    assert strat.active_position is None


def test_v3_hedge_subsecond_timeout():
    """
    Verifies that _evaluate_pending_hedge uses the sub-second timeout threshold (0.8s).
    0.9s elapsed triggers timeout (whereas previous 2.0s timeout would not have triggered).
    """
    from unittest.mock import MagicMock
    from src.execution.strategy import LiveExecutionStrategy

    strat = LiveExecutionStrategy(async_writer=None, notifier=None)
    mock_clob = MagicMock()
    strat.clob_client = mock_clob

    candle_start = "2026-09-18 11:20:00"
    slug = "btc-updown-5m-1789717200"
    token_up = "TOK_UP_SUBSEC"
    token_dn = "TOK_DN_SUBSEC"

    now_sec = time.time()
    strat.dry_strategy.active_position = {
        "Candle_Start": candle_start,
        "Slug": slug,
        "Token_Id": token_up,
        "Opposite_Token_Id": token_dn,
        "Position_Side": "UP",
        "Position_Status": "PENDING_HEDGE",
        "Target_Buy_Price": 0.71,
        "Average_Fill_Price": 0.71,
        "Target_Quantity": 5.0,
        "Filled_Quantity": 5.0,
        "Take_Profit_Price": 0.91,
        "Stop_Loss_Price": 0.62,
        "Hedge_Token_Id": token_dn,
        "Hedge_Order_Id": "0xHEDGE_SUBSEC_1",
        "Hedge_Timestamp_Sec": now_sec - 0.9,  # 0.9s elapsed (> 0.8s timeout, but < 2.0s)
        "Hedge_Quantity": 5.0,
        "Hedge_Buy_Price": 0.38,
    }

    mock_clob.get_balance_allowance.return_value = {"balance": "0"}
    mock_clob.get_order.return_value = {"status": "OPEN", "size_matched": "0.0"}

    # Primary price still <= SL ($0.45 <= $0.62)
    res = strat.dry_strategy._evaluate_pending_hedge(current_bid=0.45, current_ask=0.46)

    # 0.9s exceeded 0.8s timeout -> Order cancelled and transitioned to CLOSING
    mock_clob.cancel_orders.assert_called()
    assert res is not None
    assert res["Position_Status"] == "CLOSING"
    assert res["Exit_Reason"] == "EMERGENCY_DIRECT_SL"
