"""
Calibrated LightGBM Predictor Engine & Fail-Closed Safety Guard (US2.2, US5.4)
Supports dynamic atomic model hot-swapping from models/lgbm_model.pkl,
Expected Value (EV) decision thresholding, and Volatility Squeeze Regime Filtering.
SLA Target: < 40 milliseconds execution time.
"""

import os
import time
import pickle
import logging
import numpy as np
from typing import Dict, Any, Tuple, Optional

from src.config import config

logger = logging.getLogger(__name__)


class CalibratedLGBMPredictor:
    """
    Scoring engine wrapping LightGBM model inference with Isotonic Probability Calibration,
    Expected Value (EV) thresholding, Volatility Squeeze Regime Filtering, and Fail-Closed guards.
    """

    def __init__(self, model_asset_path: str = "models/lgbm_model.pkl"):
        self.model_asset_path = model_asset_path
        self.model_bundle: Optional[Dict[str, Any]] = None
        self.last_load_mtime: float = 0.0
        self.load_model_if_updated()

    def load_model_if_updated(self) -> bool:
        """
        Checks if models/lgbm_model.pkl has been updated and hot-swaps it atomically.
        """
        if not os.path.exists(self.model_asset_path):
            return False

        try:
            mtime = os.path.getmtime(self.model_asset_path)
            if mtime > self.last_load_mtime:
                with open(self.model_asset_path, "rb") as f:
                    bundle = pickle.load(f)
                self.model_bundle = bundle
                self.last_load_mtime = mtime
                logger.info(
                    f"✓ [MODEL HOT-SWAP] Successfully loaded trained LightGBM model artifact bundle. "
                    f"Holdout Acc: {bundle.get('holdout_accuracy', 0.0):.4f} | "
                    f"Win Rate: {bundle.get('high_conf_win_rate', 0.0):.4f}"
                )
                return True
        except Exception as e:
            logger.error(f"Error hot-swapping model artifact: {e}")
        return False

    @staticmethod
    def _isotonic_calibrate_fallback(raw_prob: float) -> float:
        """
        Fallback synthetic calibration transformation if no trained model bundle on disk.
        """
        z = 4.5 * (raw_prob - 0.5)
        p_cal = 1.0 / (1.0 + np.exp(-z))
        return float(np.clip(p_cal, 0.01, 0.99))

    def predict(self, feature_vector: np.ndarray, feature_latency_ms: float = 0.0) -> Tuple[str, float, float, str, str]:
        """
        Executes calibrated model inference with strict Fail-Closed validation.
        
        Returns: (signal, p_cal, p_uncal, confidence_tier, status)
        - signal: 'UP', 'DOWN', or 'NO_TRADE'
        - confidence_tier: 'HIGH', 'MEDIUM', 'LOW', 'VOLATILITY_CHOP', or 'FAIL_CLOSED'
        - status: 'SUCCESS', 'FEATURE_INVALID', 'INFERENCE_TIMEOUT', 'EXECUTION_ERROR'
        """
        inference_start = time.perf_counter()

        # Hot-swap check for model updates at candle start
        self.load_model_if_updated()

        # 1. FAIL-CLOSED GUARD: SLA Latency Check
        if feature_latency_ms > config.sla_latency_limit_ms:
            logger.warning(f"FAIL_CLOSED Triggered: Feature pipeline latency {feature_latency_ms:.2f}ms exceeds {config.sla_latency_limit_ms:.0f}ms SLA limit.")
            return ("NO_TRADE", 0.0, 0.0, "FAIL_CLOSED", "INFERENCE_TIMEOUT")

        # 2. FAIL-CLOSED GUARD: NaN / Null / Inf Check
        if feature_vector is None or len(feature_vector) == 0:
            logger.warning("FAIL_CLOSED Triggered: Empty feature vector received.")
            return ("NO_TRADE", 0.0, 0.0, "FAIL_CLOSED", "FEATURE_INVALID")

        if np.isnan(feature_vector).any() or np.isinf(feature_vector).any():
            logger.warning("FAIL_CLOSED Triggered: Input feature vector contains NaN or Inf values.")
            return ("NO_TRADE", 0.0, 0.0, "FAIL_CLOSED", "FEATURE_INVALID")

        try:
            # 3. Model Inference Execution
            if self.model_bundle and "gbm_model" in self.model_bundle:
                gbm = self.model_bundle["gbm_model"]
                calibrator = self.model_bundle["calibrator"]
                vol_floor = self.model_bundle.get("vol_squeeze_floor", 0.0)

                # Vectorized LightGBM prediction
                X_feat = feature_vector.reshape(1, -1)
                raw_prob_arr = gbm.predict(X_feat)
                p_uncal = float(np.clip(raw_prob_arr[0], 0.01, 0.99))
                if hasattr(calibrator, "predict_proba"):
                    cal_prob_arr = calibrator.predict_proba(raw_prob_arr.reshape(-1, 1))[:, 1]
                else:
                    cal_prob_arr = calibrator.transform(raw_prob_arr)
                p_cal = float(np.clip(cal_prob_arr[0], 0.01, 0.99))
            else:
                # Fallback scoring model weights (RSI, OBI, EMA Momentum)
                rsi = feature_vector[11] if len(feature_vector) > 11 else 50.0
                obi = feature_vector[19] if len(feature_vector) > 19 else 0.0
                ret_1 = feature_vector[1] if len(feature_vector) > 1 else 0.0
                dist_ema9 = feature_vector[8] if len(feature_vector) > 8 else 0.0

                raw_logit = 0.4 * obi + 0.3 * (ret_1 * 100.0) + 0.2 * dist_ema9 + 0.1 * ((rsi - 50.0) / 50.0)
                raw_prob = 1.0 / (1.0 + np.exp(-raw_logit))
                p_uncal = float(np.clip(raw_prob, 0.01, 0.99))
                p_cal = self._isotonic_calibrate_fallback(p_uncal)
                vol_floor = 0.0

            total_latency_ms = feature_latency_ms + ((time.perf_counter() - inference_start) * 1000.0)
            if total_latency_ms > 40.0:
                logger.warning(f"FAIL_CLOSED Triggered: Total inference latency {total_latency_ms:.2f}ms exceeds 40ms SLA limit.")
                return ("NO_TRADE", 0.0, 0.0, "FAIL_CLOSED", "INFERENCE_TIMEOUT")

            # [AMENDMENT 2] Volatility Squeeze Guard (Suppress chop trades if vol below floor)
            atr_val = feature_vector[13] if len(feature_vector) > 13 else 100.0
            if vol_floor > 0.0 and atr_val < vol_floor:
                logger.info(f"Risk Guard: Volatility Squeeze Chop Detected (ATR {atr_val:.2f} < Floor {vol_floor:.2f}). No Trade.")
                return ("NO_TRADE", p_cal, p_uncal, "VOLATILITY_CHOP", "SUCCESS")

            # Directional Probability Decision Engine
            if p_cal >= 0.50:
                signal = "UP"
                confidence = p_cal
            else:
                signal = "DOWN"
                confidence = 1.0 - p_cal

            min_thresh = config.min_model_probability
            if confidence >= min_thresh:
                tier = "HIGH" if confidence >= 0.55 else "MEDIUM"
            else:
                signal = "NO_TRADE"
                tier = "LOW"

            p_up = p_cal * 100.0
            p_dn = (1.0 - p_cal) * 100.0
            logger.info(f"ML Prediction Generated: Signal={signal} | P_up={p_up:.1f}% | P_dn={p_dn:.1f}% | Confidence={confidence:.4f} | Tier={tier} | SLA={total_latency_ms:.2f}ms")
            return (signal, p_cal, p_uncal, tier, "SUCCESS")

        except Exception as e:
            logger.error(f"Execution error during model prediction: {e}")
            return ("NO_TRADE", 0.0, 0.0, "FAIL_CLOSED", "EXECUTION_ERROR")
