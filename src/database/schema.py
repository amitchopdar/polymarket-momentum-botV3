"""
Database Schema Definitions for PolyDB (US0.2, US0.3, US0.4)
"""

import sqlite3

# US0.2: BTC_OHCLV table definition
CREATE_BTC_OHCLV_TABLE = """
CREATE TABLE IF NOT EXISTS BTC_OHCLV (
    Candle_Start TEXT PRIMARY KEY,
    Interval TEXT NOT NULL,
    Open REAL NOT NULL,
    High REAL NOT NULL,
    Low REAL NOT NULL,
    Close REAL NOT NULL,
    Volume REAL NOT NULL,
    Obi REAL NOT NULL,
    Short_Liq_Vol REAL NOT NULL,
    Long_Liq_Vol REAL NOT NULL
);
"""

# US0.3: Odds_OHCLV table definition
CREATE_ODDS_OHCLV_TABLE = """
CREATE TABLE IF NOT EXISTS Odds_OHCLV (
    Candle_Start TEXT PRIMARY KEY,
    Up_Token_Id TEXT NOT NULL,
    Up_Open REAL,
    Up_High REAL,
    Up_Low REAL,
    Up_Close REAL,
    Up_Volume REAL,
    Down_Token_Id TEXT NOT NULL,
    Down_Open REAL,
    Down_High REAL,
    Down_Low REAL,
    Down_Close REAL,
    Down_Volume REAL,
    "1_Min_Up_High" REAL,
    "1_Min_Up_Low" REAL,
    "1_Min_Down_High" REAL,
    "1_Min_Down_Low" REAL,
    "2_Min_Up_High" REAL,
    "2_Min_Up_Low" REAL,
    "2_Min_Down_High" REAL,
    "2_Min_Down_Low" REAL,
    "3_Min_Up_High" REAL,
    "3_Min_Up_Low" REAL,
    "3_Min_Down_High" REAL,
    "3_Min_Down_Low" REAL,
    "4_Min_Up_High" REAL,
    "4_Min_Up_Low" REAL,
    "4_Min_Down_High" REAL,
    "4_Min_Down_Low" REAL,
    "5_Min_Up_High" REAL,
    "5_Min_Up_Low" REAL,
    "5_Min_Down_High" REAL,
    "5_Min_Down_Low" REAL,
    Status TEXT NOT NULL DEFAULT 'RESOLVED'
);
"""

# US0.4: Positions table definition for V2
CREATE_POSITIONS_TABLE = """
CREATE TABLE IF NOT EXISTS Positions (
    Trade_Id INTEGER PRIMARY KEY AUTOINCREMENT,
    Candle_Start TEXT NOT NULL,
    Prob_Cal REAL NOT NULL DEFAULT 0.50,
    Prob_Uncal REAL NOT NULL DEFAULT 0.50,
    Slug TEXT NOT NULL,
    Token_Id TEXT,
    Prediction_Side TEXT NOT NULL,
    Actual_Outcome TEXT DEFAULT NULL,
    Entry_Timestamp DATETIME NOT NULL,
    Trigger_Odds_10s_Ago REAL,
    Entry_Odds REAL,
    Target_Price REAL NOT NULL,
    Target_Quantity REAL NOT NULL,
    Filled_Quantity REAL DEFAULT 0.0,
    Average_Fill_Price REAL,
    Take_Profit_Price REAL,
    Stop_Loss_Price REAL,
    Exit_Timestamp DATETIME,
    Exit_Price REAL,
    Exit_Reason TEXT,
    Trade_Outcome TEXT,
    Order_Id TEXT,
    Position_Status TEXT NOT NULL,
    Cancel_Reason TEXT,
    Transaction_Price REAL,
    Pnl REAL,
    Updated_At DATETIME NOT NULL
);
"""

def create_tables(conn: sqlite3.Connection) -> None:
    """
    Executes table creation DDLs for BTC_OHCLV, Odds_OHCLV, and Positions.
    Migrates Odds_OHCLV and Positions if missing new columns.
    """
    cursor = conn.cursor()
    cursor.execute(CREATE_BTC_OHCLV_TABLE)
    cursor.execute(CREATE_ODDS_OHCLV_TABLE)
    cursor.execute(CREATE_POSITIONS_TABLE)

    # Check and migrate Status column if table existed previously without it
    cursor.execute("PRAGMA table_info(Odds_OHCLV);")
    columns = [row[1] for row in cursor.fetchall()]
    if "Status" not in columns:
        cursor.execute("ALTER TABLE Odds_OHCLV ADD COLUMN Status TEXT DEFAULT 'RESOLVED';")

    # Check and migrate V2 columns for Positions if table existed previously
    cursor.execute("PRAGMA table_info(Positions);")
    pos_columns = [row[1] for row in cursor.fetchall()]
    migration_cols = {
        "Actual_Outcome": "TEXT DEFAULT NULL",
        "Token_Id": "TEXT",
        "Trigger_Odds_10s_Ago": "REAL",
        "Entry_Odds": "REAL",
        "Take_Profit_Price": "REAL",
        "Stop_Loss_Price": "REAL",
        "Exit_Timestamp": "DATETIME",
        "Trade_Outcome": "TEXT"
    }
    for col_name, col_type in migration_cols.items():
        if col_name not in pos_columns:
            cursor.execute(f"ALTER TABLE Positions ADD COLUMN {col_name} {col_type};")

    conn.commit()
