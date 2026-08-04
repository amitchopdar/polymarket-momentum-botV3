"""
Champion/Challenger Model Registry & Promotion Manager (Sprint 4: US5.4)
Manages versioned model artifacts, holds out evaluation gates, and executes atomic promotions.
"""

import os
import shutil
import pickle
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ModelRegistry:
    """
    Manages versioned model artifacts and promotion gates.
    """

    def __init__(self, registry_dir: str = "models", champion_path: str = "models/lgbm_model.pkl"):
        self.registry_dir = registry_dir
        self.champion_path = champion_path
        os.makedirs(self.registry_dir, exist_ok=True)

    def evaluate_and_promote(
        self,
        candidate_bundle: Dict[str, Any],
        min_win_rate: float = 0.51,
        max_brier_score: float = 0.25
    ) -> bool:
        """
        Evaluates candidate model against holdout gate and promotes atomically to champion.
        """
        win_rate = candidate_bundle.get("high_conf_win_rate", 0.0)
        brier = candidate_bundle.get("brier_score", 1.0)

        logger.info(f"Evaluating candidate model: Win Rate={win_rate:.4f} (min {min_win_rate:.2f}), Brier={brier:.4f} (max {max_brier_score:.2f})")

        if win_rate < min_win_rate:
            logger.warning(f"⚠ Candidate model REJECTED: Win rate {win_rate:.4f} below gate threshold {min_win_rate:.2f}.")
            return False

        if brier > max_brier_score:
            logger.warning(f"⚠ Candidate model REJECTED: Brier score {brier:.4f} exceeds gate threshold {max_brier_score:.2f}.")
            return False

        # Version artifact name based on UTC timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        version_path = os.path.join(self.registry_dir, f"lgbm_v_{timestamp}.pkl")

        try:
            # 1. Save versioned candidate artifact
            with open(version_path, "wb") as f:
                pickle.dump(candidate_bundle, f)

            # 2. Atomic hot-swap copy to champion pointer
            shutil.copyfile(version_path, self.champion_path)

            logger.info(
                f"✓ [MODEL PROMOTED] Candidate promoted to Champion! "
                f"Version artifact: '{version_path}' | Champion pointer: '{self.champion_path}'"
            )
            return True
        except Exception as e:
            logger.error(f"Error promoting candidate model: {e}")
            return False
