"""
Historical Dataset Builder for Offline Model Training (Sprint 4: US5.1)
Replays PolyDB.sqlite historical candles through VectorFeaturePipeline with 100% parity,
attaches directional target labels, exponential recency weights, and volatility squeeze indicators.
"""

import os
import sqlite3
import numpy as np
import logging
from typing import Tuple, Dict, Any, List, Optional
from datetime import datetime, timezone

from src.ml.features import VectorFeaturePipeline

logger = logging.getLogger(__name__)


class HistoricalDatasetBuilder:
    """
    Builds leakage-free training datasets from PolyDB.sqlite historical database.
    """

    def __init__(self, db_path: str = "PolyDB.sqlite", lookback_candles: int = 500):
        self.db_path = db_path
        self.lookback_candles = lookback_candles
        self.feature_pipeline = VectorFeaturePipeline()

    def load_raw_history(self) -> List[Dict[str, Any]]:
        """
        Loads chronological BTC_OHCLV candles from PolyDB.sqlite.
        """
        if not os.path.exists(self.db_path):
            logger.warning(f"Database file '{self.db_path}' not found. Returning empty history.")
            return []

        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT Candle_Start, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
            FROM BTC_OHCLV
            ORDER BY Candle_Start ASC
        """)
        rows = cursor.fetchall()
        conn.close()

        history = []
        for r in rows:
            rd = dict(r)
            history.append({
                "Candle_Start": rd["Candle_Start"],
                "Open": float(rd["Open"]),
                "High": float(rd["High"]),
                "Low": float(rd["Low"]),
                "Close": float(rd["Close"]),
                "Volume": float(rd["Volume"]),
                "Obi": float(rd["Obi"]),
                "Short_Liq_Vol": float(rd["Short_Liq_Vol"]),
                "Long_Liq_Vol": float(rd["Long_Liq_Vol"])
            })
        return history

    def build_dataset(
        self,
        decay_factor: float = 0.0001
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str]]:
        """
        Replays historical candles through VectorFeaturePipeline with 100% live feature parity.

        Returns:
        - X: Feature matrix of shape (N, num_features)
        - y: Binary targets (1 for UP win: Close > Open; 0 for DOWN win: Close <= Open)
        - sample_weights: Exponential recency weights e^(-decay_factor * (N - i))
        - vol_metrics: Array of 5-minute ATR / Volatility metrics for Squeeze Guard floor
        - candle_starts: List of Candle_Start strings
        """
        history = self.load_raw_history()
        if len(history) < 30:
            logger.warning(f"Insufficient history rows ({len(history)}) for training. Need at least 30.")
            return np.array([]), np.array([]), np.array([]), np.array([]), []

        X_list = []
        y_list = []
        vol_list = []
        starts_list = []

        window_size = min(self.lookback_candles, 100)

        # Sliding window replay matching live bot execution
        for i in range(window_size, len(history)):
            window = history[i - window_size:i]
            target_candle = history[i]

            features, _ = self.feature_pipeline.extract_features(window)
            if np.isnan(features).any() or np.isinf(features).any():
                continue

            # Skip flat/doji noise candles (< 0.01% price change)
            open_price = float(target_candle.get("Open", 1.0))
            close_price = float(target_candle.get("Close", 1.0))
            if abs(close_price - open_price) < (0.0001 * open_price):
                continue

            # Target Label: 1 if target candle closes > opens (UP win), else 0 (DOWN win)
            target = 1 if close_price > open_price else 0

            # Volatility Squeeze Metric: ATR / Normalized High-Low Range
            atr_vol = features[13] if len(features) > 13 else (target_candle["High"] - target_candle["Low"])

            X_list.append(features)
            y_list.append(target)
            vol_list.append(atr_vol)
            starts_list.append(target_candle["Candle_Start"])

        X = np.array(X_list, dtype=np.float64)
        y = np.array(y_list, dtype=np.int32)
        vol_metrics = np.array(vol_list, dtype=np.float64)

        # Exponential Recency Weights: w_i = exp(-decay_factor * (N - 1 - i))
        n_samples = len(X)
        if n_samples > 0:
            indices = np.arange(n_samples)
            sample_weights = np.exp(-decay_factor * (n_samples - 1 - indices))
            sample_weights /= np.mean(sample_weights)  # Normalize mean to 1.0
        else:
            sample_weights = np.array([])

        logger.info(f"✓ [DATASET BUILDER] Built dataset with {n_samples} samples & {X.shape[1] if n_samples > 0 else 0} features.")
        return X, y, sample_weights, vol_metrics, starts_list
