"""
Unit & Integration Tests for Sprint 4: ML Retraining Pipeline, Purged Walk-Forward CV, MLOps Registry & Monte Carlo
"""

import os
import time
import pytest
import sqlite3
import numpy as np
import tempfile
from datetime import datetime, timezone

from src.ml.dataset_builder import HistoricalDatasetBuilder
from src.ml.trainer import PurgedWalkForwardCV, ModelTrainer, MonteCarloSimulator
from src.ml.predictor import CalibratedLGBMPredictor
from src.ml.registry import ModelRegistry
from src.execution.strategy import DryExecutionStrategy
from src.database.connection import AsyncDBWriter, PolyDBManager


@pytest.fixture
def populated_db():
    db_fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(db_fd)
    db_manager = PolyDBManager(db_path=db_path)
    db_manager.init_db()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Insert 600 synthetic candles for dataset builder testing
    base_time = 1784800000
    price = 65000.0
    for i in range(600):
        t_str = datetime.fromtimestamp(base_time + i * 300, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        open_p = price + np.random.normal(0, 10)
        close_p = open_p + np.random.normal(0, 15)
        high_p = max(open_p, close_p) + abs(np.random.normal(0, 5))
        low_p = min(open_p, close_p) - abs(np.random.normal(0, 5))
        cursor.execute("""
            INSERT INTO BTC_OHCLV (
                Candle_Start, Interval, Open, High, Low, Close, Volume, Obi, Short_Liq_Vol, Long_Liq_Vol
            ) VALUES (?, '5m', ?, ?, ?, ?, 100.0, 0.25, 0.0, 0.0)
        """, (t_str, open_p, high_p, low_p, close_p))

    conn.commit()
    conn.close()

    yield db_path
    if os.path.exists(db_path):
        os.remove(db_path)


def test_purged_walk_forward_cv():
    X = np.random.randn(200, 29)
    cv = PurgedWalkForwardCV(n_splits=3, purge_window=5, embargo_window=5)
    splits = list(cv.split(X))
    assert len(splits) == 3

    for train_idx, val_idx in splits:
        # Guarantee no overlap between train and val
        assert len(set(train_idx).intersection(set(val_idx))) == 0
        # Guarantee time-ordered property (max train index < min val index)
        assert np.max(train_idx) < np.min(val_idx)


def test_vectorized_monte_carlo_simulation():
    sim = MonteCarloSimulator(n_runs=1000, trades_per_run=200)
    res = sim.simulate(win_rate=0.585, win_payout=0.60, loss_payout=-0.20, position_size_usd=50.0)

    assert res["win_rate"] == 0.585
    assert "mc_median_drawdown_pct" in res
    assert "mc_99th_drawdown_pct" in res
    assert "mc_max_losing_streak" in res
    assert "mc_prob_of_ruin" in res
    assert res["mc_prob_of_ruin"] < 5.0
    assert res["is_safe"] is True


def test_dataset_builder_and_trainer(populated_db):
    builder = HistoricalDatasetBuilder(db_path=populated_db, lookback_candles=500)
    X, y, weights, vol_metrics, candle_starts = builder.build_dataset()

    assert len(X) > 0
    assert X.shape[1] == 29  # Includes 4 Intraday Volatility Seasonality Features
    assert len(y) == len(X)
    assert len(weights) == len(X)
    assert len(vol_metrics) == len(X)

    trainer = ModelTrainer(n_trials=2, purge_window=2, embargo_window=2, holdout_ratio=0.20)
    result_bundle = trainer.train_and_calibrate(X, y, weights, vol_metrics)

    assert result_bundle.get("status") == "SUCCESS"
    assert "gbm_model" in result_bundle
    assert "calibrator" in result_bundle
    assert "vol_squeeze_floor" in result_bundle
    assert "monte_carlo" in result_bundle
    assert result_bundle["monte_carlo"]["mc_prob_of_ruin"] >= 0.0


def test_model_registry_and_predictor_hotswap(populated_db, tmp_path):
    builder = HistoricalDatasetBuilder(db_path=populated_db, lookback_candles=500)
    X, y, weights, vol_metrics, candle_starts = builder.build_dataset()

    trainer = ModelTrainer(n_trials=2, purge_window=2, embargo_window=2, holdout_ratio=0.20)
    result_bundle = trainer.train_and_calibrate(X, y, weights, vol_metrics)

    model_dir = str(tmp_path / "models")
    champion_file = str(tmp_path / "models" / "lgbm_model.pkl")
    registry = ModelRegistry(registry_dir=model_dir, champion_path=champion_file)

    promoted = registry.evaluate_and_promote(result_bundle, min_win_rate=0.0, max_brier_score=1.0)
    assert promoted is True
    assert os.path.exists(champion_file)

    predictor = CalibratedLGBMPredictor(model_asset_path=champion_file)
    assert predictor.model_bundle is not None

    features = np.random.randn(29)
    features[13] = 100.0  # ATR above floor
    signal, p_cal, p_uncal, tier, status = predictor.predict(features, feature_latency_ms=5.0)
    assert signal in ("UP", "DOWN", "NO_TRADE")
    assert status == "SUCCESS"


def test_amendment_3_order_timeout():
    strategy = DryExecutionStrategy(async_writer=None)
    # Entry timestamp set to 320 seconds ago (> 300s timeout limit)
    old_time = datetime.fromtimestamp(time.time() - 320.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    pos = {
        "Candle_Start": "2026-07-24 08:00:00",
        "Target_Price": 0.40,
        "Target_Quantity": 125.0,
        "Position_Status": "PENDING",
        "Entry_Timestamp": old_time
    }
    strategy.active_positions["2026-07-24 08:00:00"] = pos

    updated = strategy.check_and_update_positions("2026-07-24 08:00:00", "tok1", current_bid=0.35, current_ask=0.45)
    assert updated is not None
    assert updated["Position_Status"] == "CANCELLED"
    assert updated["Cancel_Reason"] == "TIMEOUT_300S"
