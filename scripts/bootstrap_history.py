#!/usr/bin/env python3
"""
Historical Bootstrap CLI Script for Polymarket Prediction Bot
Fetches historical 5-minute BTC OHCLV candles from Binance Futures REST API
and populates PolyDB.sqlite (INSERT OR IGNORE) without corrupting live data.
"""

import sys
import time
import sqlite3
import argparse
import urllib.request
import urllib.parse
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from src.config import config
from src.database.connection import PolyDBManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("BootstrapHistory")


def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 1500, end_time_ms: Optional[int] = None) -> list:
    """
    Fetches up to 1500 5-minute candles from Binance Futures REST API.
    """
    base_url = "https://fapi.binance.com/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    if end_time_ms:
        params["endTime"] = end_time_ms

    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "PolymarketBot/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Error fetching Binance klines: {e}")
    return []


def bootstrap_historical_candles(target_candles: int = 20000, db_path: str = "PolyDB.sqlite"):
    """
    Paginates backward through Binance Futures REST API to fetch historical 5-minute candles
    and bulk-inserts them into PolyDB.sqlite using INSERT OR IGNORE.
    """
    logger.info("==================================================")
    logger.info(f" Starting Historical Bootstrap for {target_candles} Candles")
    logger.info(f" Database Target: {db_path}")
    logger.info("==================================================")

    # Initialize DB schema if not already initialized
    db_mgr = PolyDBManager(db_path=db_path)
    db_mgr.init_db()

    conn = sqlite3.connect(db_path, timeout=30.0)
    cursor = conn.cursor()

    total_inserted = 0
    end_time_ms = None
    candles_collected = []

    req_count = 0
    while len(candles_collected) < target_candles:
        req_count += 1
        limit = min(1500, target_candles - len(candles_collected))
        logger.info(f"Fetching batch #{req_count} ({limit} candles) backward from Binance REST...")

        klines = fetch_binance_klines(symbol="BTCUSDT", interval="5m", limit=limit, end_time_ms=end_time_ms)
        if not klines:
            logger.warning("Empty batch received or API rate-limit hit. Stopping pagination.")
            break

        # Kline structure: [OpenTime, Open, High, Low, Close, Volume, CloseTime, ...]
        candles_collected.extend(klines)
        first_open_time = klines[0][0]
        end_time_ms = first_open_time - 1

        logger.info(f"Batch #{req_count} retrieved: {len(klines)} candles. Total collected: {len(candles_collected)} / {target_candles}")
        time.sleep(0.2)  # Respect API rate limits

    logger.info(f"✓ Total raw candles collected: {len(candles_collected)}. Inserting into PolyDB.sqlite...")

    sql = """
        INSERT OR IGNORE INTO BTC_OHCLV (
            Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
        ) VALUES (?, '5m', ?, ?, ?, ?, ?, 0.0, 0.0, 0.0)
    """

    insert_rows = []
    for k in candles_collected:
        open_time_ms = k[0]
        dt = datetime.fromtimestamp(open_time_ms / 1000.0, tz=timezone.utc)
        candle_start = dt.strftime("%Y-%m-%d %H:%M:%S")

        open_p = float(k[1])
        high_p = float(k[2])
        low_p = float(k[3])
        close_p = float(k[4])
        vol = float(k[5])

        insert_rows.append((candle_start, open_p, high_p, low_p, close_p, vol))

    cursor.executemany(sql, insert_rows)
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM BTC_OHCLV;")
    final_db_count = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(Candle_Start), MAX(Candle_Start) FROM BTC_OHCLV;")
    min_date, max_date = cursor.fetchone()
    conn.close()

    logger.info("==================================================")
    logger.info(" ✓ HISTORICAL BOOTSTRAP COMPLETE")
    logger.info(f"   - Total Rows in BTC_OHCLV: {final_db_count}")
    logger.info(f"   - Earliest Candle: {min_date}")
    logger.info(f"   - Latest Candle:   {max_date}")
    logger.info("==================================================")


def main():
    parser = argparse.ArgumentParser(description="Polymarket Historical Bootstrap CLI")
    parser.add_argument("--candles", type=int, default=20000, help="Number of historical 5-minute candles to fetch")
    args = parser.parse_args()

    bootstrap_historical_candles(target_candles=args.candles, db_path=config.db_path)


if __name__ == "__main__":
    main()
