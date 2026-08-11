"""
Global Application Configuration for Polymarket Bot V2
Single Source of Truth for V2 Dynamic Strategy, Database, Telegram, and Live Wallet Settings.
All sensitive credentials are read strictly from environment variables or .env file.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

def parse_int_list(raw: str) -> List[int]:
    if not raw:
        return []
    res = []
    for item in raw.split(","):
        item = item.strip()
        if item.isdigit():
            res.append(int(item))
    return res

# Auto-load local .env file if present
_env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_file):
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _val = _v.strip().strip("\"' ")
                    os.environ.setdefault(_k.strip(), _val)
    except Exception:
        pass

# ==============================================================================
# POLYMARKET BOT V2 STRATEGY PARAMETERS (Non-Sensitive Defaults)
# ==============================================================================

USER_EXECUTION_MODE = "DRY_RUN"
USER_V2_MOMENTUM_THRESHOLD_CENTS = 0.15   # 15-cent (+0.15) absolute odds increase threshold
USER_V2_MOMENTUM_WINDOW_SEC = 10.0        # Sliding momentum lookback window (10 seconds)
USER_V2_TAKE_PROFIT_CENTS = 0.20            # Take Profit absolute cents gain target (+0.05 / +5 cents for Tier 1)
USER_V2_HIGH_ODDS_CUTOFF = 0.80            # High odds cutoff threshold for Tier 2 ($0.75 / 75 cents)
USER_V2_HIGH_ODDS_TP_TARGET = 0.9900        # Fixed TP target price for Tier 2 ($0.99 / $1.00 max exchange limit price)
USER_V2_TRAILING_SL_ENABLED = True         # Enable Trailing Stop Loss based on High Water Mark
USER_V2_TRAILING_SL_DISTANCE_CENTS = 0.10  # Trailing SL distance from HWM (10 cents)
USER_V2_MIN_ENTRY_ODDS_FLOOR = 0.65       # Minimum odds floor required for trade entry ($0.65 / 65 cents)
USER_V2_MAX_ENTRY_ODDS_CEILING = 0.92     # Maximum odds ceiling limit for trade entry ($0.92 / 92 cents)
USER_V2_MAX_POSITION_SIZE_USD = 5.0        # Max position size per trade ($2.00)
USER_V2_MAX_ACTIVE_POSITIONS = 1          # Single active position limit across bot (1 position)

# Polymarket Bot V3 Maker & Timeout Parameters
USER_V3_MAKER_OFFSET_CENTS = 0.02          # 2 cents below best ask for Maker status (0.01 or 0.02)
USER_V3_MAKER_ORDER_TIMEOUT_SEC = 5.0      # 5 seconds order cancellation timeout
# ==============================================================================


@dataclass
class AppConfig:
    """
    Centralized bot configuration object for Polymarket Bot V3.
    Personal sensitive credentials (Telegram tokens, wallet keys) are read EXCLUSIVELY from .env file.
    """
    # Environment & Mode Toggles
    execution_mode: str = field(default_factory=lambda: os.getenv("EXECUTION_MODE", USER_EXECUTION_MODE).upper())
    dry_run: bool = field(default_factory=lambda: os.getenv("EXECUTION_MODE", USER_EXECUTION_MODE).upper() == "DRY_RUN")
    trading_active: bool = True
    
    # Database Settings
    db_path: str = "PolyDB_V3.sqlite"
    busy_timeout_ms: int = 30000

    # Polymarket Endpoint URLs
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com/events"
    polymarket_clob_url: str = "https://clob.polymarket.com"

    # V2 / V3 Dynamic Strategy Fields
    v2_momentum_threshold_cents: float = field(default_factory=lambda: float(os.getenv("V2_MOMENTUM_THRESHOLD_CENTS", str(USER_V2_MOMENTUM_THRESHOLD_CENTS))))
    v2_momentum_window_sec: float = field(default_factory=lambda: float(os.getenv("V2_MOMENTUM_WINDOW_SEC", str(USER_V2_MOMENTUM_WINDOW_SEC))))
    v2_take_profit_cents: float = field(default_factory=lambda: float(os.getenv("V2_TAKE_PROFIT_CENTS", str(USER_V2_TAKE_PROFIT_CENTS))))
    v2_high_odds_cutoff: float = field(default_factory=lambda: float(os.getenv("V2_HIGH_ODDS_CUTOFF", str(USER_V2_HIGH_ODDS_CUTOFF))))
    v2_high_odds_tp_target: float = field(default_factory=lambda: float(os.getenv("V2_HIGH_ODDS_TP_TARGET", str(USER_V2_HIGH_ODDS_TP_TARGET))))
    v2_trailing_sl_enabled: bool = field(default_factory=lambda: os.getenv("V2_TRAILING_SL_ENABLED", "TRUE").upper() == "TRUE")
    v2_trailing_sl_distance_cents: float = field(default_factory=lambda: float(os.getenv("V2_TRAILING_SL_DISTANCE_CENTS", str(USER_V2_TRAILING_SL_DISTANCE_CENTS))))
    v2_min_entry_odds_floor: float = field(default_factory=lambda: float(os.getenv("V2_MIN_ENTRY_ODDS_FLOOR", str(USER_V2_MIN_ENTRY_ODDS_FLOOR))))
    v2_max_entry_odds_ceiling: float = field(default_factory=lambda: float(os.getenv("V2_MAX_ENTRY_ODDS_CEILING", str(USER_V2_MAX_ENTRY_ODDS_CEILING))))
    max_position_size_usd: float = field(default_factory=lambda: float(os.getenv("MAX_POSITION_SIZE_USD", str(USER_V2_MAX_POSITION_SIZE_USD))))
    v2_max_active_positions: int = field(default_factory=lambda: int(os.getenv("V2_MAX_ACTIVE_POSITIONS", str(USER_V2_MAX_ACTIVE_POSITIONS))))

    v2_taker_fee_pct: float = field(default_factory=lambda: float(os.getenv("V2_TAKER_FEE_PCT", "0.02")))

    # V3 Maker Strategy Specific Fields
    v3_maker_offset_cents: float = field(default_factory=lambda: float(os.getenv("V3_MAKER_OFFSET_CENTS", str(USER_V3_MAKER_OFFSET_CENTS))))
    v3_maker_order_timeout_sec: float = field(default_factory=lambda: float(os.getenv("V3_MAKER_ORDER_TIMEOUT_SEC", str(USER_V3_MAKER_ORDER_TIMEOUT_SEC))))

    # Legacy V1 Compatibility Fallbacks (for V1 test suite compatibility)
    target_buy_price: float = 0.48
    target_entry_price: float = 0.48
    stop_loss_price: float = 0.30
    min_model_probability: float = 0.5001
    min_l2_depth_shares: float = 10.0
    max_slippage_tolerance: float = 0.02
    sla_latency_limit_ms: float = 100.0
    order_timeout_sec: float = 300.0
    min_required_win_rate: float = 0.55

    # Sensitive Personal Credentials (LOADED EXCLUSIVELY FROM .env / ENVIRONMENT)
    telegram_enabled: bool = True
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    telegram_authorized_user_ids: List[int] = field(
        default_factory=lambda: parse_int_list(os.getenv("TELEGRAM_AUTHORIZED_USER_IDS", ""))
    )

    polymarket_api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    polymarket_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_SECRET", ""))
    polymarket_passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_PASSPHRASE", ""))
    polymarket_private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    polymarket_funder: str = field(default_factory=lambda: os.getenv("POLYMARKET_FUNDER", ""))

    def is_dry_run(self) -> bool:
        return self.execution_mode == "DRY_RUN" or self.dry_run

    def set_execution_mode(self, mode: str) -> bool:
        mode_upper = mode.upper()
        if mode_upper in ("DRY_RUN", "LIVE"):
            self.execution_mode = mode_upper
            self.dry_run = (mode_upper == "DRY_RUN")
            logger.info(f"✓ Execution mode set to: {self.execution_mode}")
            return True
        return False

    def set_trading_active(self, active: bool) -> None:
        self.trading_active = active
        status_str = "ACTIVE" if active else "DEACTIVATED"
        logger.info(f"✓ Bot trading engine status set to: {status_str}")


config = AppConfig()
