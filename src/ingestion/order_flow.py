"""
Order Flow & Order Book Imbalance (OBI) Tracker (US1.2.1)
"""

import threading
import logging
from typing import Tuple, List, Dict, Any

logger = logging.getLogger(__name__)

class OrderFlowTracker:
    """
    Computes Order Book Imbalance (OBI) from top 10 depth levels
    and aggregates short/long liquidation volumes over 5-minute boundaries.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.latest_obi: float = 0.0
        self.latest_spot_price: float = 0.0
        self.short_liq_vol: float = 0.0
        self.long_liq_vol: float = 0.0

    def process_depth(self, bids: List[List[Any]], asks: List[List[Any]]) -> float:
        """
        Calculates Order Book Imbalance (OBI) over top 10 depth levels:
        OBI = (Bid_Vol - Ask_Vol) / (Bid_Vol + Ask_Vol)
        Also updates real-time order book spot mid-price.
        """
        top_bids = bids[:10]
        top_asks = asks[:10]

        bid_vol = sum(float(b[1]) for b in top_bids)
        ask_vol = sum(float(a[1]) for a in top_asks)

        total_vol = bid_vol + ask_vol
        if total_vol > 0:
            obi = round((bid_vol - ask_vol) / total_vol, 4)
        else:
            obi = 0.0

        spot_price = 0.0
        if top_bids and top_asks:
            bid0 = float(top_bids[0][0])
            ask0 = float(top_asks[0][0])
            spot_price = round((bid0 + ask0) / 2.0, 2)

        with self._lock:
            self.latest_obi = obi
            if spot_price > 0:
                self.latest_spot_price = spot_price

        return obi

    def get_current_spot_price(self) -> float:
        """
        Returns real-time spot mid-price from order book depth.
        """
        with self._lock:
            return self.latest_spot_price

    def process_liquidation(self, side: str, qty: float, price: float) -> None:
        """
        Aggregates liquidation volume by side:
        - 'SELL' side force orders reflect short liquidations.
        - 'BUY' side force orders reflect long liquidations.
        """
        vol = float(qty) * float(price)
        with self._lock:
            if side.upper() == "SELL":
                self.short_liq_vol += vol
            elif side.upper() == "BUY":
                self.long_liq_vol += vol

    def get_current_obi(self) -> float:
        with self._lock:
            return self.latest_obi

    def flush_5m_metrics(self) -> Tuple[float, float, float]:
        """
        Flushes and resets 5-minute metrics at the candle boundary.
        Returns: (OBI, Short_Liq_Vol, Long_Liq_Vol)
        """
        with self._lock:
            obi = self.latest_obi
            short_vol = round(self.short_liq_vol, 4)
            long_vol = round(self.long_liq_vol, 4)
            
            # Reset liquidations for the next 5m candle
            self.short_liq_vol = 0.0
            self.long_liq_vol = 0.0
            
            return obi, short_vol, long_vol
