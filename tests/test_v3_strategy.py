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





