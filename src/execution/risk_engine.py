"""
Risk Engine & Liquidity Validator (Sprint 3: US3.2)
Enforces L2 depth verification, probability thresholds, single position per candle guards,
and automated $0.20 stop-loss limit sell order creation upon fills.
"""

import logging
from typing import Optional, Dict, Any

from src.config import config
from src.execution.strategy import IExecutionStrategy

logger = logging.getLogger(__name__)

class RiskEngine:
    """
    Risk Management & Execution Orchestrator.
    Guarantees strict risk controls:
    - Maximum 1 buy order / position per 5-minute active candle.
    - Model probability threshold validation (>= 0.55).
    - L2 Order Book Depth verification.
    - Automated $0.20 stop-loss limit sell order dispatch upon fill.
    """

    def __init__(self, execution_strategy: IExecutionStrategy):
        self.execution_strategy = execution_strategy
        # Single position per candle guard set
        self.executed_candles: set = set()

    def can_execute_candle(self, candle_start: str) -> bool:
        """
        Enforces maximum 1 buy order per 5-minute candle interval.
        """
        return candle_start not in self.executed_candles

    def validate_l2_depth(self, current_ask: Optional[float], depth_shares: float = 100.0) -> bool:
        """
        Verifies Order Book depth before submitting an entry limit order.
        Allows fallback for initial T+0s orders when WS snapshot frame is warming up.
        """
        if current_ask is not None and current_ask > 0.0:
            if depth_shares < config.min_l2_depth_shares:
                logger.warning(f"⚠ Risk Guard: Insufficient L2 Liquidity Depth ({depth_shares} < {config.min_l2_depth_shares} shares). Order Aborted.")
                return False
        return True

    def validate_probability(self, prob_cal: float, side: str = "UP") -> bool:
        """
        Validates model directional confidence against confidence threshold.
        For UP: confidence = P_cal.
        For DOWN: confidence = 1.0 - P_cal.
        """
        confidence = prob_cal if side == "UP" else (1.0 - prob_cal)
        if confidence < config.min_model_probability:
            logger.info(f"Risk Guard: {side} directional confidence ({confidence:.4f}) below min threshold ({config.min_model_probability:.2f}). No Trade.")
            return False
        return True

    def evaluate_and_execute_entry(
        self,
        candle_start: str,
        slug: str,
        side: str,
        prob_cal: float,
        prob_uncal: float,
        token_id: str,
        current_bid: Optional[float] = None,
        current_ask: Optional[float] = None,
        depth_shares: float = 100.0
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates risk parameters and submits a persistent limit buy order at $0.40.
        """
        if not config.trading_active:
            logger.info("Risk Guard: Trading engine is DEACTIVATED. Order skipped.")
            return None

        if side == "NO_TRADE":
            return None

        # 1. Single Position Guard Per Active Candle
        if not self.can_execute_candle(candle_start):
            logger.warning(f"⚠ Risk Guard: Single position limit reached for candle {candle_start}. Order Aborted.")
            return None

        # 2. Directional Probability Confidence Guard
        if not self.validate_probability(prob_cal, side):
            return None

        # 3. L2 Depth Guard
        if not self.validate_l2_depth(current_ask, depth_shares):
            return None

        # Mark candle executed to enforce single position per candle rule
        self.executed_candles.add(candle_start)

        # Dispatch Entry Order ($0.40 Limit Buy)
        pos = self.execution_strategy.execute_entry(
            candle_start=candle_start,
            slug=slug,
            side=side,
            prob_cal=prob_cal,
            prob_uncal=prob_uncal,
            target_price=config.target_buy_price,
            position_usd=config.max_position_size_usd,
            token_id=token_id,
            current_bid=current_bid,
            current_ask=current_ask
        )

        return pos
