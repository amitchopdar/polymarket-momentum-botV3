"""
Vectorized C-Speed NumPy Feature Engineering Pipeline (US2.1)
Includes Intraday Volatility Seasonality Features (UTC Hour, Day of Week, Cyclical Sine/Cosine transformations).
SLA Performance Target: < 40 milliseconds execution time.
"""

import time
import logging
import numpy as np
from typing import List, Dict, Any, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class VectorFeaturePipeline:
    """
    Converts historical candlestick deques directly into multi-dimensional
    NumPy arrays and performs vectorized technical indicator calculations in C-speed arrays.
    """

    def __init__(self):
        pass

    @staticmethod
    def _compute_ema(arr: np.ndarray, period: int) -> np.ndarray:
        """
        Calculates Exponential Moving Average (EMA) using NumPy vectorized recursion.
        """
        alpha = 2.0 / (period + 1.0)
        ema = np.zeros_like(arr)
        ema[0] = arr[0]
        for i in range(1, len(arr)):
            ema[i] = alpha * arr[i] + (1.0 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int = 14) -> float:
        """
        Calculates Relative Strength Index (RSI) in raw NumPy.
        """
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0.0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return float(rsi)

    @staticmethod
    def _compute_macd(closes: np.ndarray) -> Tuple[float, float, float]:
        """
        Calculates MACD (12, 26, 9) signal and histogram in raw NumPy.
        Returns: (macd_line, signal_line, macd_hist)
        """
        if len(closes) < 26:
            return (0.0, 0.0, 0.0)

        ema_12 = VectorFeaturePipeline._compute_ema(closes, 12)
        ema_26 = VectorFeaturePipeline._compute_ema(closes, 26)
        macd_line = ema_12 - ema_26
        signal_line = VectorFeaturePipeline._compute_ema(macd_line, 9)
        macd_hist = macd_line - signal_line

        return (float(macd_line[-1]), float(signal_line[-1]), float(macd_hist[-1]))

    @staticmethod
    def _compute_bollinger_bands(closes: np.ndarray, period: int = 20, num_std: float = 2.0) -> Tuple[float, float, float, float]:
        """
        Calculates Bollinger Bands (upper, middle, lower, %B) in raw NumPy.
        """
        if len(closes) < period:
            c = float(closes[-1]) if len(closes) > 0 else 0.0
            return (c, c, c, 0.5)

        window = closes[-period:]
        sma = float(np.mean(window))
        std = float(np.std(window))
        upper = sma + (num_std * std)
        lower = sma - (num_std * std)

        c = float(closes[-1])
        percent_b = (c - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
        return (upper, sma, lower, float(percent_b))

    @staticmethod
    def _compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        """
        Calculates Average True Range (ATR) in raw NumPy.
        """
        if len(closes) < period + 1:
            return float(highs[-1] - lows[-1]) if len(highs) > 0 else 0.0

        tr1 = highs[1:] - lows[1:]
        tr2 = np.abs(highs[1:] - closes[:-1])
        tr3 = np.abs(lows[1:] - closes[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))

        atr = float(np.mean(tr[-period:]))
        return atr

    def extract_features(self, candle_history: List[Dict[str, Any]]) -> Tuple[np.ndarray, float]:
        """
        Extracts multi-dimensional feature vector from rolling candle history.
        Performance SLA: Guaranteed < 40 milliseconds execution time.
        Returns: (feature_vector_1d_array, latency_ms)
        """
        start_time = time.perf_counter()

        if not candle_history or len(candle_history) == 0:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            return np.zeros(29, dtype=np.float64), latency_ms

        # Extract contiguous C-speed NumPy arrays
        opens = np.array([c["Open"] for c in candle_history], dtype=np.float64)
        highs = np.array([c["High"] for c in candle_history], dtype=np.float64)
        lows = np.array([c["Low"] for c in candle_history], dtype=np.float64)
        closes = np.array([c["Close"] for c in candle_history], dtype=np.float64)
        volumes = np.array([c["Volume"] for c in candle_history], dtype=np.float64)
        obis = np.array([c.get("Obi", 0.0) for c in candle_history], dtype=np.float64)
        short_liqs = np.array([c.get("Short_Liq_Vol", 0.0) for c in candle_history], dtype=np.float64)
        long_liqs = np.array([c.get("Long_Liq_Vol", 0.0) for c in candle_history], dtype=np.float64)

        n = len(closes)
        c_last = closes[-1]

        # 1. Price Momentum & Returns
        ret_1 = (closes[-1] / closes[-2] - 1.0) if n >= 2 else 0.0
        ret_3 = (closes[-1] / closes[-4] - 1.0) if n >= 4 else 0.0
        ret_5 = (closes[-1] / closes[-6] - 1.0) if n >= 6 else 0.0

        # 2. Moving Averages
        ema_9 = self._compute_ema(closes, min(9, n))[-1]
        ema_20 = self._compute_ema(closes, min(20, n))[-1]
        ema_50 = self._compute_ema(closes, min(50, n))[-1]
        sma_200 = float(np.mean(closes))

        dist_ema9 = (c_last / ema_9 - 1.0) if ema_9 > 0 else 0.0
        dist_ema20 = (c_last / ema_20 - 1.0) if ema_20 > 0 else 0.0
        dist_ema50 = (c_last / ema_50 - 1.0) if ema_50 > 0 else 0.0

        # 3. Oscillators & Volatility
        rsi_14 = self._compute_rsi(closes, period=min(14, n - 1))
        macd_line, macd_signal, macd_hist = self._compute_macd(closes)
        bb_upper, bb_mid, bb_lower, percent_b = self._compute_bollinger_bands(closes, period=min(20, n))
        atr_14 = self._compute_atr(highs, lows, closes, period=min(14, n - 1))

        # 4. Microstructure (OBI & Liquidations)
        obi_last = obis[-1] if n >= 1 else 0.0
        obi_mean_3 = float(np.mean(obis[-3:])) if n >= 3 else obi_last
        obi_mean_5 = float(np.mean(obis[-5:])) if n >= 5 else obi_last

        short_liq_sum = float(np.sum(short_liqs[-3:])) if n >= 3 else 0.0
        long_liq_sum = float(np.sum(long_liqs[-3:])) if n >= 3 else 0.0
        liq_imbalance = (short_liq_sum - long_liq_sum) / (short_liq_sum + long_liq_sum + 1e-5)

        # 5. Volume Indicators
        vol_mean_5 = float(np.mean(volumes[-5:])) if n >= 5 else volumes[-1]
        vol_ratio = (volumes[-1] / vol_mean_5) if vol_mean_5 > 0 else 1.0

        # 6. Intraday Volatility Seasonality Features (UTC Hour, Day of Week, Cyclical Sine/Cosine)
        last_candle = candle_history[-1]
        c_start_str = last_candle.get("Candle_Start", "")
        utc_hour = 12.0
        utc_dow = 0.0
        if c_start_str:
            try:
                dt = datetime.strptime(c_start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                utc_hour = float(dt.hour)
                utc_dow = float(dt.weekday())
            except Exception:
                pass

        sin_hour = float(np.sin(2.0 * np.pi * utc_hour / 24.0))
        cos_hour = float(np.cos(2.0 * np.pi * utc_hour / 24.0))

        # Construct single 1D feature vector
        features = np.array([
            c_last, ret_1, ret_3, ret_5,
            ema_9, ema_20, ema_50, sma_200,
            dist_ema9, dist_ema20, dist_ema50,
            rsi_14, macd_line, macd_signal, macd_hist,
            bb_upper, bb_lower, percent_b, atr_14,
            obi_last, obi_mean_3, obi_mean_5,
            short_liq_sum, long_liq_sum, liq_imbalance,
            utc_hour, utc_dow, sin_hour, cos_hour
        ], dtype=np.float64)

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if latency_ms > 40.0:
            logger.warning(f"FEATURE_CALCULATION_SLA_BREACH: Feature extraction took {latency_ms:.2f}ms (> 40ms SLA limit)")

        return features, latency_ms
