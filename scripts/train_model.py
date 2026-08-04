#!/usr/bin/env python3
"""
CLI Runner for Offline Model Retraining & Optuna Hyperparameter Optimization (Sprint 4: US5.3)
Executes dataset building, Purged Walk-Forward CV, Optuna tuning, Isotonic Calibration, and Model Promotion.
"""

import sys
import argparse
import logging

from src.config import config
from src.ml.dataset_builder import HistoricalDatasetBuilder
from src.ml.trainer import ModelTrainer
from src.ml.registry import ModelRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TrainModelCLI")


def main():
    parser = argparse.ArgumentParser(description="Polymarket LightGBM Retraining CLI Pipeline")
    parser.add_argument("--force", action="store_true", help="Force immediate retraining regardless of interval")
    parser.add_argument("--trials", type=int, default=30, help="Number of Optuna optimization trials")
    args = parser.parse_args()

    logger.info("==================================================")
    logger.info(" Starting Polymarket ML Offline Retraining Pipeline")
    logger.info(f" Mode: {'FORCE' if args.force else 'SCHEDULED'} | Optuna Trials: {args.trials}")
    logger.info("==================================================")

    # 1. Build Dataset from PolyDB.sqlite
    builder = HistoricalDatasetBuilder(db_path=config.db_path, lookback_candles=500)
    X, y, sample_weights, vol_metrics, candle_starts = builder.build_dataset(decay_factor=0.0001)

    if len(X) < 50:
        logger.error(f"❌ Aborting retraining: Insufficient samples ({len(X)}) in '{config.db_path}'. Need at least 50.")
        sys.exit(1)

    # 2. Execute Purged Walk-Forward CV & Optuna Tuning
    trainer = ModelTrainer(n_trials=args.trials, purge_window=6, embargo_window=6, holdout_ratio=0.20)
    result_bundle = trainer.train_and_calibrate(X, y, sample_weights, vol_metrics)

    if result_bundle.get("status") != "SUCCESS":
        logger.error("❌ Model training pipeline failed.")
        sys.exit(1)

    # 3. Evaluate Holdout Gate & Atomic Promotion via Registry
    registry = ModelRegistry(registry_dir="models", champion_path="models/lgbm_model.pkl")
    promoted = registry.evaluate_and_promote(result_bundle, min_win_rate=config.min_required_win_rate, max_brier_score=0.25)

    from src.notifications.notifier import TelegramNotifier, format_ist
    notifier = TelegramNotifier()

    if promoted:
        logger.info("==================================================")
        logger.info(" ✓ SUCCESS: Model Promoted to Production Champion")
        logger.info(f"   - Holdout Accuracy: {result_bundle['holdout_accuracy']:.2%}")
        logger.info(f"   - High-Conf Win Rate: {result_bundle['high_conf_win_rate']:.2%}")
        logger.info(f"   - Brier Score: {result_bundle['brier_score']:.4f}")
        logger.info(f"   - Volatility Squeeze Floor: {result_bundle['vol_squeeze_floor']:.4f}")
        logger.info("==================================================")

        mc = result_bundle.get("monte_carlo", {})
        trained_at_ist = format_ist(result_bundle.get("trained_at", ""))
        notifier.notify_model_retrained(
            promoted=True,
            trained_at_ist=trained_at_ist,
            win_rate=result_bundle.get("high_conf_win_rate", 0.55),
            mc_dd=mc.get("mc_99th_drawdown_pct", 14.0),
            ruin=mc.get("mc_prob_of_ruin", 0.0)
        )
    else:
        logger.warning("⚠ Candidate model did not clear holdout evaluation gate. Champion model retained.")
        win_rate = result_bundle.get("high_conf_win_rate", 0.0)
        reason = f"Win Rate {win_rate*100.0 if win_rate <= 1.0 else win_rate:.1f}% below {config.min_model_probability*100.0:.0f}% threshold"
        notifier.notify_model_retrained(promoted=False, trained_at_ist="", win_rate=0.0, reason=reason)


if __name__ == "__main__":
    main()
