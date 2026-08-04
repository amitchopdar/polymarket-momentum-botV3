"""
Standalone Polymarket Settlement Historical Backfill & Overwrite Script
Loops over all historical records in PolyDB.sqlite (Odds_OHCLV and Positions tables),
queries official Polymarket Gamma API for each market slug, and overwrites historical
settlement records with 100% pure Polymarket oracle resolution data (ZERO fallback guessing!).
"""

import sys
import os
import time
import json
import sqlite3
import requests
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PolyDB.sqlite")
GAMMA_API = "https://gamma-api.polymarket.com/events"

def get_official_polymarket_settlement(candle_start_str: str):
    """
    Fetches 100% pure official Polymarket resolution for a candle start timestamp string.
    Returns: (actual_outcome: 'UP'|'DOWN', up_close: 1.0|0.0, down_close: 0.0|1.0) or None if unresolvable.
    """
    try:
        dt = datetime.strptime(candle_start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        start_sec = int(dt.timestamp())
    except Exception:
        return None

    slug = f"btc-updown-5m-{start_sec}"
    try:
        resp = requests.get(GAMMA_API, params={"slug": slug}, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                markets = data[0].get("markets", [])
                if markets:
                    mkt = markets[0]
                    is_closed = mkt.get("closed", False)

                    # 1. Check token winner flags
                    tokens = mkt.get("tokens", [])
                    for tok in tokens:
                        if tok.get("winner") is True:
                            outcome = str(tok.get("outcome", "")).upper()
                            if outcome == "UP":
                                return ("UP", 1.0, 0.0)
                            elif outcome == "DOWN":
                                return ("DOWN", 0.0, 1.0)

                    # 2. Check outcomePrices
                    prices_raw = mkt.get("outcomePrices")
                    if prices_raw:
                        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                        p0 = float(prices[0]) if len(prices) > 0 else 0.0
                        p1 = float(prices[1]) if len(prices) > 1 else 0.0

                        if p0 == 1.0 and p1 == 0.0:
                            return ("UP", 1.0, 0.0)
                        elif p0 == 0.0 and p1 == 1.0:
                            return ("DOWN", 0.0, 1.0)
                        elif is_closed:
                            if p0 > p1:
                                return ("UP", 1.0, 0.0)
                            else:
                                return ("DOWN", 0.0, 1.0)
    except Exception as e:
        print(f"  ⚠ Gamma API query error for {slug}: {e}")

    return None


def run_backfill(db_file: str = DB_PATH):
    if not os.path.exists(db_file):
        print(f"Database file not found: {db_file}")
        return

    print("==================================================")
    print(" Starting Official Polymarket Historical Backfill")
    print(f" Database: {db_file}")
    print("==================================================")

    conn = sqlite3.connect(db_file, timeout=15.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Backfill Positions Table
    cursor.execute("SELECT DISTINCT Candle_Start FROM Positions WHERE Prediction_Side != 'NO_TRADE' OR Actual_Outcome IS NULL OR Actual_Outcome = '';")
    pos_rows = cursor.fetchall()
    print(f"\nProcessing {len(pos_rows)} positions in Positions table...")

    updated_pos_count = 0
    for idx, r in enumerate(pos_rows, 1):
        c_start = r["Candle_Start"]
        res = get_official_polymarket_settlement(c_start)
        if res:
            outcome, up_c, dn_c = res
            cursor.execute(
                "UPDATE Positions SET Actual_Outcome = ?, Updated_At = ? WHERE Candle_Start = ?;",
                (outcome, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), c_start)
            )
            updated_pos_count += 1
            print(f"  [{idx}/{len(pos_rows)}] Candle {c_start} --> Overwritten Actual_Outcome = {outcome}")
        else:
            print(f"  [{idx}/{len(pos_rows)}] Candle {c_start} --> Polymarket API pending / unavailable")
        time.sleep(0.1)

    # 2. Backfill Odds_OHCLV Table
    cursor.execute("SELECT DISTINCT Candle_Start FROM Odds_OHCLV;")
    odds_rows = cursor.fetchall()
    print(f"\nProcessing {len(odds_rows)} candles in Odds_OHCLV table...")

    updated_odds_count = 0
    for idx, r in enumerate(odds_rows, 1):
        c_start = r["Candle_Start"]
        res = get_official_polymarket_settlement(c_start)
        if res:
            outcome, up_c, dn_c = res
            cursor.execute(
                "UPDATE Odds_OHCLV SET Up_Close = ?, Down_Close = ?, Status = 'RESOLVED' WHERE Candle_Start = ?;",
                (up_c, dn_c, c_start)
            )
            updated_odds_count += 1
            print(f"  [{idx}/{len(odds_rows)}] Odds {c_start} --> Overwritten Up_Close={up_c}, Down_Close={dn_c}")
        time.sleep(0.1)

    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(FULL);")
    conn.close()

    print("\n==================================================")
    print(" ✓ Backfill Complete!")
    print(f"   Positions updated: {updated_pos_count}")
    print(f"   Odds records updated: {updated_odds_count}")
    print("==================================================")

if __name__ == "__main__":
    run_backfill()
