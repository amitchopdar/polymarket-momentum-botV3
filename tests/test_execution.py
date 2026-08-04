"""
Unit & Integration Tests for Sprint 3 Execution Engine, Risk Guards, and State Reconciler.
"""

import os
import time
import pytest
import sqlite3
import tempfile
from datetime import datetime, timezone

from src.config import config, AppConfig
from src.database.connection import AsyncDBWriter
from src.execution.strategy import DryExecutionStrategy, LiveExecutionStrategy
from src.execution.risk_engine import RiskEngine
from src.execution.reconciler import StateReconciler


@pytest.fixture
def temp_db():
    db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(db_fd)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE Positions (
            Candle_Start DATETIME PRIMARY KEY,
            Prob_Cal REAL NOT NULL,
            Prob_Uncal REAL NOT NULL,
            Slug TEXT NOT NULL,
            Prediction_Side TEXT NOT NULL,
            Entry_Timestamp DATETIME NOT NULL,
            Target_Price REAL NOT NULL,
            Target_Quantity REAL NOT NULL,
            Filled_Quantity REAL DEFAULT 0.0,
            Average_Fill_Price REAL,
            Order_Id TEXT,
            Position_Status TEXT NOT NULL,
            Cancel_Reason TEXT,
            Transaction_Price REAL,
            Exit_Price REAL,
            Exit_Reason TEXT,
            Pnl REAL,
            Updated_At DATETIME NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    from src.database.connection import PolyDBManager
    db_mgr = PolyDBManager(db_path=db_path)
    writer = AsyncDBWriter(db_mgr)
    writer.start()

    yield db_path, writer

    writer.stop()
    if os.path.exists(db_path):
        os.remove(db_path)


def test_sprint3_bot_config():
    test_cfg = AppConfig(execution_mode="DRY_RUN", target_buy_price=0.40, stop_loss_price=0.20)
    assert test_cfg.is_dry_run() is True
    assert test_cfg.target_buy_price == 0.40
    assert test_cfg.stop_loss_price == 0.20

    test_cfg.set_execution_mode("LIVE")
    assert test_cfg.is_dry_run() is False

    test_cfg.set_trading_active(False)
    assert test_cfg.trading_active is False


def test_dry_execution_lifecycle(temp_db):
    db_path, writer = temp_db
    strategy = DryExecutionStrategy(writer)

    candle_start = "2026-07-24 00:00:00"
    slug = "btc-updown-5m-1784841600"
    side = "UP"
    prob_cal = 0.65
    prob_uncal = 0.62
    token_id = "1234567890"

    # 1. Entry Limit Order Dispatch at $0.40
    pos = strategy.execute_entry(
        candle_start=candle_start,
        slug=slug,
        side=side,
        prob_cal=prob_cal,
        prob_uncal=prob_uncal,
        target_price=0.40,
        position_usd=50.0,
        token_id=token_id,
        current_bid=0.45,
        current_ask=0.46
    )

    assert pos is not None
    assert pos["Position_Status"] == "PENDING"
    assert pos["Target_Quantity"] == 125.0

    # 2. Limit Buy Order Fill when Ask <= $0.40
    pos_updated = strategy.check_and_update_positions(candle_start, token_id, current_bid=0.39, current_ask=0.40)
    assert pos_updated["Position_Status"] == "OPEN"
    assert pos_updated["Filled_Quantity"] == 125.0
    assert pos_updated["Average_Fill_Price"] == 0.40
    assert pos_updated["Transaction_Price"] == 50.0
    assert pos_updated["Stop_Loss_Order_Id"] is not None

    # 3. Stop-Loss Trigger when Bid <= config.stop_loss_price
    sl_price = config.stop_loss_price
    pos_closed = strategy.check_and_update_positions(candle_start, token_id, current_bid=sl_price, current_ask=sl_price + 0.01)
    assert pos_closed["Position_Status"] == "CLOSED"
    assert pos_closed["Exit_Price"] == sl_price
    assert pos_closed["Exit_Reason"] == "STOP_LOSS"
    expected_pnl = (sl_price - 0.40) * 125.0
    assert abs(pos_closed["Pnl"] - expected_pnl) < 1e-4


def test_risk_engine_guards(temp_db):
    db_path, writer = temp_db
    strategy = DryExecutionStrategy(writer)
    risk_engine = RiskEngine(strategy)

    candle_start = "2026-07-24 00:05:00"

    # 1. Low Probability Guard (< 0.55)
    res_low_prob = risk_engine.evaluate_and_execute_entry(
        candle_start=candle_start, slug="test", side="UP",
        prob_cal=0.50, prob_uncal=0.48, token_id="tok1", current_ask=0.39
    )
    assert res_low_prob is None

    # 2. Insufficient L2 Depth Guard (< 10 shares)
    res_low_depth = risk_engine.evaluate_and_execute_entry(
        candle_start=candle_start, slug="test", side="UP",
        prob_cal=0.60, prob_uncal=0.58, token_id="tok1", current_ask=0.39, depth_shares=5.0
    )
    assert res_low_depth is None

    # 3. Valid Execution & Single Position Guard Enforcement
    res_valid = risk_engine.evaluate_and_execute_entry(
        candle_start=candle_start, slug="test", side="UP",
        prob_cal=0.60, prob_uncal=0.58, token_id="tok1", current_ask=0.40, depth_shares=100.0
    )
    assert res_valid is not None
    assert res_valid["Position_Status"] in ("PENDING", "OPEN")

    # Second buy order attempt on same candle MUST be blocked by single position guard
    res_duplicate = risk_engine.evaluate_and_execute_entry(
        candle_start=candle_start, slug="test", side="UP",
        prob_cal=0.70, prob_uncal=0.68, token_id="tok1", current_ask=0.40, depth_shares=100.0
    )
    assert res_duplicate is None


def test_state_reconciler_boot_recovery(temp_db):
    db_path, writer = temp_db
    strategy = DryExecutionStrategy(writer)
    risk_engine = RiskEngine(strategy)

    now_sec = int(time.time())
    curr_candle_sec = (now_sec // 300) * 300
    active_candle = datetime.fromtimestamp(curr_candle_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Insert pre-existing open position directly into SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO Positions (
            Candle_Start, Prob_Cal, Prob_Uncal, Slug, Prediction_Side,
            Entry_Timestamp, Target_Price, Target_Quantity, Filled_Quantity,
            Average_Fill_Price, Order_Id, Position_Status, Cancel_Reason,
            Transaction_Price, Exit_Price, Exit_Reason, Pnl, Updated_At
        ) VALUES (
            ?, 0.65, 0.62, 'slug1', 'UP',
            '2026-07-24 00:10:01', 0.40, 125.0, 125.0,
            0.40, 'SIM123', 'OPEN', NULL,
            50.0, NULL, NULL, 0.0, '2026-07-24 00:10:01'
        );
    """, (active_candle,))
    conn.commit()
    conn.close()

    reconciler = StateReconciler(db_path)
    restored = reconciler.reconcile_on_boot(strategy, risk_engine)

    assert restored == 1
    assert active_candle in strategy.active_positions
    assert active_candle in risk_engine.executed_candles
