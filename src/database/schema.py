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

# US0.4: Positions table definition for V3
CREATE_POSITIONS_TABLE = """
CREATE TABLE IF NOT EXISTS Positions (
    Trade_Id INTEGER PRIMARY KEY AUTOINCREMENT,
    Candle_Start TEXT NOT NULL,
    Slug TEXT NOT NULL,
    Token_Id TEXT NOT NULL,
    Position_Side TEXT NOT NULL,
    Entry_Timestamp DATETIME NOT NULL,
    Trigger_Odds_10s_Ago REAL,
    Entry_Odds REAL,
    Target_Buy_Price REAL NOT NULL,
    Average_Fill_Price REAL,
    Target_Quantity REAL NOT NULL,
    Filled_Quantity REAL DEFAULT 0.0,
    Sell_Quantity REAL DEFAULT 0.0,
    Take_Profit_Price REAL,
    Stop_Loss_Price REAL,
    High_Water_Mark REAL,
    Buy_Order_Id TEXT,
    Sell_Order_Id TEXT,
    Exit_Timestamp DATETIME,
    Exit_Price REAL,
    Exit_Reason TEXT,
    Trade_Outcome TEXT,
    Position_Status TEXT NOT NULL,
    Cancel_Reason TEXT,
    Pnl REAL DEFAULT 0.0,
    Updated_At DATETIME NOT NULL
);
"""

def create_tables(conn: sqlite3.Connection) -> None:
    """
    Executes table creation DDLs for BTC_OHCLV, Odds_OHCLV, and Positions.
    Migrates Odds_OHCLV and Positions if missing new columns or containing legacy NOT NULL constraints.
    """
    cursor = conn.cursor()
    cursor.execute(CREATE_BTC_OHCLV_TABLE)
    cursor.execute(CREATE_ODDS_OHCLV_TABLE)

    # Check if Positions table exists with legacy NOT NULL constraints (Target_Price, Prob_Cal)
    cursor.execute("PRAGMA table_info(Positions);")
    pos_columns = [row[1] for row in cursor.fetchall()]

    if pos_columns and ("Target_Price" in pos_columns or "Prob_Cal" in pos_columns):
        # Auto-migrate legacy Positions table to clean V3 Positions schema
        try:
            cursor.execute("ALTER TABLE Positions RENAME TO Positions_Old;")
            cursor.execute(CREATE_POSITIONS_TABLE)
            
            cursor.execute("PRAGMA table_info(Positions_Old);")
            old_cols = [row[1] for row in cursor.fetchall()]
            side_col = "Position_Side" if "Position_Side" in old_cols else "Prediction_Side"
            target_col = "Target_Buy_Price" if "Target_Buy_Price" in old_cols else "Target_Price"

            cursor.execute(f"""
                INSERT INTO Positions (
                    Trade_Id, Candle_Start, Slug, Token_Id, Position_Side, Entry_Timestamp,
                    Trigger_Odds_10s_Ago, Entry_Odds, Target_Buy_Price, Average_Fill_Price,
                    Target_Quantity, Filled_Quantity, Take_Profit_Price, Stop_Loss_Price,
                    Buy_Order_Id, Exit_Timestamp, Exit_Price, Exit_Reason,
                    Trade_Outcome, Position_Status, Cancel_Reason, Pnl, Updated_At
                )
                SELECT 
                    Trade_Id, Candle_Start, Slug, COALESCE(Token_Id, ''), 
                    COALESCE({side_col}, 'UP'), Entry_Timestamp,
                    Trigger_Odds_10s_Ago, Entry_Odds, COALESCE({target_col}, 0.0), Average_Fill_Price,
                    Target_Quantity, Filled_Quantity, Take_Profit_Price, Stop_Loss_Price,
                    Order_Id, Exit_Timestamp, Exit_Price, Exit_Reason,
                    Trade_Outcome, Position_Status, Cancel_Reason, COALESCE(Pnl, 0.0), Updated_At
                FROM Positions_Old;
            """)
            cursor.execute("DROP TABLE Positions_Old;")
            conn.commit()
        except Exception:
            conn.rollback()
            cursor.execute("DROP TABLE IF EXISTS Positions_Old;")
            cursor.execute(CREATE_POSITIONS_TABLE)
    else:
        cursor.execute(CREATE_POSITIONS_TABLE)

    # Check and migrate Status column if table existed previously without it
    cursor.execute("PRAGMA table_info(Odds_OHCLV);")
    columns = [row[1] for row in cursor.fetchall()]
    if "Status" not in columns:
        cursor.execute("ALTER TABLE Odds_OHCLV ADD COLUMN Status TEXT DEFAULT 'RESOLVED';")

    conn.commit()
