"""
Unit & Integration Tests for ML Feature Pipeline & Predictor Engine (US2.1, US2.2)
"""

import pytest
import numpy as np
from src.ml.features import VectorFeaturePipeline
from src.ml.predictor import CalibratedLGBMPredictor


def test_us2_1_vectorized_feature_pipeline_sla_latency():
    """
    US2.1 Verification: Vectorized feature extraction execution time < 40ms SLA limit.
    """
    pipeline = VectorFeaturePipeline()

    # Generate 500 mock historical candle dicts
    mock_history = []
    base_price = 65000.0
    for i in range(500):
        candle = {
            "Candle_Start": f"2026-07-22 00:{i%60:02d}:00",
            "Open": base_price + i * 0.1,
            "High": base_price + i * 0.1 + 10.0,
            "Low": base_price + i * 0.1 - 10.0,
            "Close": base_price + i * 0.1 + 5.0,
            "Volume": 100.0 + i,
            "Obi": 0.1 * (i % 5),
            "Short_Liq_Vol": 1000.0,
            "Long_Liq_Vol": 500.0
        }
        mock_history.append(candle)

    features, latency_ms = pipeline.extract_features(mock_history)

    assert isinstance(features, np.ndarray)
    assert len(features) == 29
    assert not np.isnan(features).any()
    assert not np.isinf(features).any()
    assert latency_ms < 40.0, f"Expected feature calculation latency < 40ms, got {latency_ms:.2f}ms"


def test_us2_2_calibrated_lightgbm_predictor_inference():
    """
    US2.2 Verification: LightGBM probability inference & Isotonic calibration score P_cal.
    """
    predictor = CalibratedLGBMPredictor()

    mock_features = np.array([
        65100.0, 0.002, 0.005, 0.010, # Price & Returns
        65050.0, 65000.0, 64900.0, 64500.0, # EMAs
        0.001, 0.002, 0.003, # EMA Distances
        65.0, 10.0, 5.0, 5.0, # RSI, MACD
        65300.0, 64800.0, 0.60, 25.0, # Bollinger Bands, ATR
        0.45, 0.35, 0.25, # OBI
        10000.0, 2000.0, 0.6667, # Liquidations
        14.0, 3.0, 0.5, 0.866 # Intraday Seasonality (utc_hour, utc_day_of_week, sin_hour, cos_hour)
    ], dtype=np.float64)

    signal, p_cal, p_uncal, tier, status = predictor.predict(mock_features, feature_latency_ms=5.0)

    assert status == "SUCCESS"
    assert signal in ["UP", "DOWN", "NO_TRADE"]
    assert 0.0 <= p_cal <= 1.0
    assert 0.0 <= p_uncal <= 1.0
    assert tier in ["HIGH", "MEDIUM", "LOW"]


def test_us2_2_fail_closed_on_nan_or_inf_features():
    """
    US2.2 Verification: Fail-Closed policy suppresses signals (P_cal = 0.0, NO_TRADE) when features contain NaN/Inf.
    """
    predictor = CalibratedLGBMPredictor()

    nan_features = np.array([65000.0, np.nan, 0.01, 0.0], dtype=np.float64)
    signal, p_cal, p_uncal, tier, status = predictor.predict(nan_features)

    assert signal == "NO_TRADE"
    assert p_cal == 0.0
    assert tier == "FAIL_CLOSED"
    assert status == "FEATURE_INVALID"

    inf_features = np.array([65000.0, np.inf, 0.01, 0.0], dtype=np.float64)
    signal_inf, p_cal_inf, _, tier_inf, status_inf = predictor.predict(inf_features)

    assert signal_inf == "NO_TRADE"
    assert p_cal_inf == 0.0
    assert tier_inf == "FAIL_CLOSED"
    assert status_inf == "FEATURE_INVALID"


def test_us2_2_fail_closed_on_sla_latency_timeout():
    """
    US2.2 Verification: Fail-Closed policy suppresses signals when latency exceeds SLA limit.
    """
    predictor = CalibratedLGBMPredictor()

    valid_features = np.zeros(29, dtype=np.float64)
    signal, p_cal, _, tier, status = predictor.predict(valid_features, feature_latency_ms=105.0) # > 100ms SLA

    assert signal == "NO_TRADE"
    assert p_cal == 0.0
    assert tier == "FAIL_CLOSED"
    assert status == "INFERENCE_TIMEOUT"
