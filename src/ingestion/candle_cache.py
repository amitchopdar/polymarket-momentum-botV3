"""
Rolling Candlestick Cache, REST Warmup, and Persistence Manager (US1.1.1, US1.1.2)
"""

import time
import threading
import logging
import requests
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from src.database.connection import AsyncDBWriter
from .order_flow import OrderFlowTracker

logger = logging.getLogger(__name__)

BINANCE_REST_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

class CandleCache:
    """
    Maintains an in-memory double-ended queue (deque) of 5-minute candles,
    handles REST warmup/backfill, and triggers non-blocking database persistence on finalization.
    """

    def __init__(self, maxlen: int = 500):
        self.maxlen = maxlen
        self.deque: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.last_finalized_time: Optional[int] = None

    def warmup_from_rest(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "5m",
        limit: int = 500,
        async_writer: Optional[AsyncDBWriter] = None
    ) -> int:
        """
        Fetches historical candles from Binance REST API on startup/reconnect to warm up deque
        and persist initial historical dataset into database.
        """
        try:
            params = {"symbol": symbol, "interval": interval, "limit": limit}
            resp = requests.get(BINANCE_REST_KLINES_URL, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            with self._lock:
                self.deque.clear()
                total_count = len(data)
                for idx, item in enumerate(data):
                    start_ts_ms = item[0]
                    dt_start = datetime.fromtimestamp(start_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    is_active = (idx == total_count - 1)
                    candle = {
                        "Candle_Start": dt_start,
                        "Interval": interval,
                        "Open": float(item[1]),
                        "High": float(item[2]),
                        "Low": float(item[3]),
                        "Close": float(item[4]),
                        "Volume": float(item[5]),
                        "Obi": 0.0,
                        "Short_Liq_Vol": 0.0,
                        "Long_Liq_Vol": 0.0,
                        "finalized": not is_active,
                        "start_ts_ms": start_ts_ms
                    }
                    self.deque.append(candle)

                    if async_writer:
                        sql = """
                            INSERT OR IGNORE INTO BTC_OHCLV (
                                Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """
                        params_tuple = (
                            candle["Candle_Start"],
                            candle["Interval"],
                            candle["Open"],
                            candle["High"],
                            candle["Low"],
                            candle["Close"],
                            candle["Volume"],
                            candle["Obi"],
                            candle["Short_Liq_Vol"],
                            candle["Long_Liq_Vol"]
                        )
                        async_writer.enqueue_write(sql, params_tuple)
                
                if self.deque:
                    self.last_finalized_time = self.deque[-1]["start_ts_ms"]

            logger.info(f"CandleCache warmed up & persisted {len(self.deque)} candles from REST API.")
            return len(self.deque)
        except Exception as e:
            logger.error(f"Failed to warm up CandleCache via REST API: {e}")
            return 0

    def update_kline(
        self,
        kline_data: Dict[str, Any],
        order_flow: Optional[OrderFlowTracker] = None,
        async_writer: Optional[AsyncDBWriter] = None
    ) -> Dict[str, Any]:
        """
        Processes real-time kline message from WebSocket stream.
        Updates deque index [-1] and handles explicit/implicit finalization.
        """
        start_ts_ms = kline_data["t"]
        dt_start = datetime.fromtimestamp(start_ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        is_final_ws = kline_data.get("x", False)

        candle = {
            "Candle_Start": dt_start,
            "Interval": kline_data.get("i", "5m"),
            "Open": float(kline_data["o"]),
            "High": float(kline_data["h"]),
            "Low": float(kline_data["l"]),
            "Close": float(kline_data["c"]),
            "Volume": float(kline_data["v"]),
            "Obi": order_flow.get_current_obi() if order_flow else 0.0,
            "Short_Liq_Vol": 0.0,
            "Long_Liq_Vol": 0.0,
            "finalized": False,
            "start_ts_ms": start_ts_ms
        }

        with self._lock:
            if not self.deque:
                self.deque.append(candle)
            else:
                last_candle = self.deque[-1]
                if last_candle["start_ts_ms"] == start_ts_ms:
                    # Update active candle in place
                    self.deque[-1] = candle
                elif start_ts_ms > last_candle["start_ts_ms"]:
                    # Implicit finalization of previous candle
                    if not last_candle["finalized"]:
                        self._finalize_candle(self.deque[-1], order_flow, async_writer)
                    self.deque.append(candle)

            # Explicit finalization
            if is_final_ws and not self.deque[-1]["finalized"]:
                self._finalize_candle(self.deque[-1], order_flow, async_writer)

            return dict(self.deque[-1])

    def check_clock_boundary(
        self,
        order_flow: Optional[OrderFlowTracker] = None,
        async_writer: Optional[AsyncDBWriter] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Guarantees candle finalization at 5-minute wall-clock boundaries (:00, :05, :10...)
        even if WebSocket messages are momentarily delayed.
        """
        now_ts = int(time.time())
        current_5m_boundary = (now_ts // 300) * 300

        with self._lock:
            if not self.deque:
                return None

            last_candle = self.deque[-1]
            last_candle_start_sec = last_candle["start_ts_ms"] // 1000

            if current_5m_boundary > last_candle_start_sec:
                finalized_dict = None
                if not last_candle["finalized"]:
                    self._finalize_candle(last_candle, order_flow, async_writer)
                    logger.info(f"=== CANDLE FINALIZED (Wall-Clock Boundary) === {last_candle['Candle_Start']} | Close: ${last_candle['Close']} | OBI: {last_candle['Obi']}")
                    finalized_dict = dict(last_candle)

                # Append new active candle for current 5m boundary interval
                new_start_ms = current_5m_boundary * 1000
                dt_new = datetime.fromtimestamp(current_5m_boundary, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                new_candle = {
                    "Candle_Start": dt_new,
                    "Interval": last_candle.get("Interval", "5m"),
                    "Open": last_candle["Close"],
                    "High": last_candle["Close"],
                    "Low": last_candle["Close"],
                    "Close": last_candle["Close"],
                    "Volume": 0.0,
                    "Obi": order_flow.get_current_obi() if order_flow else 0.0,
                    "Short_Liq_Vol": 0.0,
                    "Long_Liq_Vol": 0.0,
                    "finalized": False,
                    "start_ts_ms": new_start_ms
                }
                self.deque.append(new_candle)
                return finalized_dict

        return None

    def _finalize_candle(
        self,
        candle: Dict[str, Any],
        order_flow: Optional[OrderFlowTracker],
        async_writer: Optional[AsyncDBWriter]
    ) -> None:
        """
        Marks candle finalized, pulls flushed order flow metrics, and enqueues DB write.
        """
        candle["finalized"] = True
        self.last_finalized_time = candle["start_ts_ms"]

        if order_flow:
            obi, short_vol, long_vol = order_flow.flush_5m_metrics()
            candle["Obi"] = obi
            candle["Short_Liq_Vol"] = short_vol
            candle["Long_Liq_Vol"] = long_vol

        if async_writer:
            sql = """
                INSERT OR REPLACE INTO BTC_OHCLV (
                    Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                candle["Candle_Start"],
                candle["Interval"],
                candle["Open"],
                candle["High"],
                candle["Low"],
                candle["Close"],
                candle["Volume"],
                candle["Obi"],
                candle["Short_Liq_Vol"],
                candle["Long_Liq_Vol"]
            )
            async_writer.enqueue_write(sql, params)
            logger.info(f"Finalized candle {candle['Candle_Start']} persisted to database.")

    def get_latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self.deque[-1]) if self.deque else None

    def get_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(c) for c in self.deque]
