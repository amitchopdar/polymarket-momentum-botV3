"""
Unit & Integration Tests for Sprint 0 (US0.1 - US0.4)
"""

import os
import sqlite3
import pytest
import time
import threading
from datetime import datetime, timezone
from src.database.connection import PolyDBManager, AsyncDBWriter

TEST_DB_PATH = "test_PolyDB.sqlite"

@pytest.fixture(autouse=True)
def clean_test_db():
    """
    Fixture to ensure a fresh test database for each test run.
    """
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    wal_file = f"{TEST_DB_PATH}-wal"
    shm_file = f"{TEST_DB_PATH}-shm"
    if os.path.exists(wal_file):
        os.remove(wal_file)
    if os.path.exists(shm_file):
        os.remove(shm_file)

    yield

    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    if os.path.exists(wal_file):
        os.remove(wal_file)
    if os.path.exists(shm_file):
        os.remove(shm_file)


def test_us0_1_wal_mode_and_busy_timeout():
    """
    US0.1 Verification: PolyDB SQLite file created in WAL mode with busy_timeout = 5000.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH, busy_timeout_ms=5000)
    db_mgr.init_db()

    assert os.path.exists(TEST_DB_PATH), "Database file test_PolyDB.sqlite was not created."

    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.lower() == "wal", f"Expected WAL mode, got {journal_mode}"

        cursor.execute("PRAGMA busy_timeout;")
        busy_timeout = cursor.fetchone()[0]
        assert busy_timeout == 5000, f"Expected busy_timeout 5000, got {busy_timeout}"


def test_us0_2_btc_ohclv_table_schema_and_insertion():
    """
    US0.2 Verification: BTC_OHCLV table creation, column structure, primary key constraints.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH)
    db_mgr.init_db()

    candle_start = "2026-07-22 00:00:00"
    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO BTC_OHCLV (
                Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (candle_start, "5m", 65000.0, 65200.0, 64900.0, 65150.0, 120.5, 0.2345, 15000.0, 5000.0))
        conn.commit()

        cursor.execute("SELECT * FROM BTC_OHCLV WHERE Candle_Start = ?", (candle_start,))
        row = cursor.fetchone()
        assert row is not None
        assert row["Interval"] == "5m"
        assert row["Open"] == 65000.0
        assert row["Obi"] == 0.2345
        assert row["Short_Liq_Vol"] == 15000.0

        # Verify Primary Key Uniqueness
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute("""
                INSERT INTO BTC_OHCLV (
                    Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (candle_start, "5m", 65000.0, 65200.0, 64900.0, 65150.0, 120.5, 0.2345, 15000.0, 5000.0))
            conn.commit()


def test_us0_3_odds_ohclv_table_schema_and_minute_tracking():
    """
    US0.3 Verification: Odds_OHCLV table creation and 5-min/1-min token OHCLV tracking columns.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH)
    db_mgr.init_db()

    candle_start = "2026-07-22 00:00:00"
    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()

        # Insert record with 1-min tracking
        cursor.execute("""
            INSERT INTO Odds_OHCLV (
                Candle_Start, Up_Token_Id, Up_Open, Up_High, Up_Low, Up_Close, Up_Volume,
                Down_Token_Id, Down_Open, Down_High, Down_Low, Down_Close, Down_Volume,
                "1_Min_Up_High", "1_Min_Up_Low", "1_Min_Down_High", "1_Min_Down_Low"
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            candle_start, "tok_up_123", 0.50, 0.60, 0.45, 0.58, 1000.0,
            "tok_down_123", 0.50, 0.55, 0.40, 0.42, 1000.0,
            0.52, 0.48, 0.52, 0.48
        ))
        conn.commit()

        cursor.execute('SELECT "1_Min_Up_High", Up_Token_Id FROM Odds_OHCLV WHERE Candle_Start = ?', (candle_start,))
        row = cursor.fetchone()
        assert row is not None
        assert row["Up_Token_Id"] == "tok_up_123"
        assert row["1_Min_Up_High"] == 0.52


def test_us0_4_positions_table_schema_and_lifecycle_updates():
    """
    US0.4 Verification: Positions table structure, default Filled_Quantity = 0.0, and trade lifecycle updates.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH)
    db_mgr.init_db()

    candle_start = "2026-07-22 00:00:00"
    now_str = datetime.now(timezone.utc).isoformat()

    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()

        # 1. Entry insertion with PENDING status
        cursor.execute("""
            INSERT INTO Positions (
                Candle_Start, Prob_Cal, Prob_Uncal, Slug, Prediction_Side,
                Entry_Timestamp, Target_Price, Target_Quantity, Position_Status, Updated_At
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            candle_start, 0.72, 0.68, "btc-above-65k-5m", "UP",
            now_str, 0.40, 100.0, "PENDING", now_str
        ))
        conn.commit()

        cursor.execute("SELECT Filled_Quantity, Position_Status FROM Positions WHERE Candle_Start = ?", (candle_start,))
        row = cursor.fetchone()
        assert row["Filled_Quantity"] == 0.0
        assert row["Position_Status"] == "PENDING"

        # 2. Async order fill update
        fill_time = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            UPDATE Positions
            SET Filled_Quantity = ?, Average_Fill_Price = ?, Transaction_Price = ?, Position_Status = ?, Order_Id = ?, Updated_At = ?
            WHERE Candle_Start = ?
        """, (100.0, 0.40, 40.0, "OPEN", "ord_999", fill_time, candle_start))
        conn.commit()

        cursor.execute("SELECT Filled_Quantity, Position_Status, Order_Id FROM Positions WHERE Candle_Start = ?", (candle_start,))
        updated_row = cursor.fetchone()
        assert updated_row["Filled_Quantity"] == 100.0
        assert updated_row["Position_Status"] == "OPEN"
        assert updated_row["Order_Id"] == "ord_999"


def test_async_db_writer_and_concurrency():
    """
    Verifies AsyncDBWriter operates without blocking main thread and handles high-frequency writes safely.
    """
    db_mgr = PolyDBManager(db_path=TEST_DB_PATH)
    db_mgr.init_db()

    writer = AsyncDBWriter(db_mgr)
    writer.start()

    # Enqueue multiple telemetry writes asynchronously
    num_writes = 50
    for i in range(num_writes):
        start_time = f"2026-07-22 01:{i:02d}:00"
        writer.enqueue_write("""
            INSERT INTO BTC_OHCLV (
                Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (start_time, "5m", 65000.0 + i, 65100.0 + i, 64900.0, 65050.0, 10.0, 0.1, 0.0, 0.0))

    # Stop writer, which waits for queue to flush
    writer.stop(timeout=5.0)

    # Check that all writes landed in DB
    with db_mgr.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM BTC_OHCLV")
        count = cursor.fetchone()[0]
        assert count == num_writes, f"Expected {num_writes} written rows, found {count}"
