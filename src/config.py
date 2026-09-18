"""
Global Application Configuration for Polymarket Bot V3
Single Source of Truth for V3 Momentum Jump Strategy, Database, Telegram, and Live Wallet Settings.
All sensitive credentials are read strictly from environment variables or .env file.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip("\"' \t\r\n")

def _safe_int(val: Any, default: int = 0) -> int:
    try:
        clean = _clean_str(val)
        return int(clean) if clean else default
    except Exception:
        return default

def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        clean = _clean_str(val)
        return float(clean) if clean else default
    except Exception:
        return default

def parse_int_list(raw: Any) -> List[int]:
    clean = _clean_str(raw)
    if not clean:
        return []
    res = []
    for item in clean.split(","):
        item = item.strip("\"' \t\r\n")
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
                    _val = _clean_str(_v)
                    os.environ.setdefault(_k.strip(), _val)
    except Exception:
        pass

# ==============================================================================
# POLYMARKET BOT V3 MOMENTUM JUMP STRATEGY PARAMETERS (Non-Sensitive Defaults)
# ==============================================================================

USER_EXECUTION_MODE = "LIVE"
USER_V2_MOMENTUM_THRESHOLD_CENTS = 0.12   # 15-cent (+0.15) absolute odds increase threshold
USER_V2_MOMENTUM_WINDOW_SEC = 10.0        # Sliding momentum lookback window (10 seconds)
USER_V2_TAKE_PROFIT_CENTS = 0.20          # Take Profit absolute cents gain target (+0.20 / +20 cents for Tier 1)
USER_V2_HIGH_ODDS_CUTOFF = 0.80           # High odds cutoff threshold for Tier 2 ($0.80 / 80 cents)
USER_V2_HIGH_ODDS_TP_TARGET = 0.9900      # Fixed TP target price for Tier 2 ($0.99 / $1.00 max exchange limit price)
USER_V2_TRAILING_SL_ENABLED = True        # Enable Trailing Stop Loss based on High Water Mark
USER_V2_TRAILING_SL_DISTANCE_CENTS = 0.10 # Trailing SL distance from HWM (10 cents)
USER_V2_STOP_LOSS_SLIPPAGE_CENTS = 0.03   # Stop Loss exit slippage for Limit Sell orders (2 cents)
USER_V2_MIN_ENTRY_ODDS_FLOOR = 0.60       # Minimum odds floor required for trade entry ($0.65 / 65 cents)
USER_V2_MAX_ENTRY_ODDS_CEILING = 0.92     # Maximum odds ceiling limit for trade entry ($0.92 / 92 cents)
USER_V3_TRADE_SIZE_SHARES = 5.0           # Number of shares to trade per signal (e.g. 5.0, 10.0, 20.0)
USER_V3_MAX_CANDLE_ENTRY_SEC = 240.0      # Max seconds into 5m candle to allow entry (240s = 4 minutes, blocks trades during last 60s)
USER_V2_MAX_ACTIVE_POSITIONS = 1          # Single active position limit across bot (1 position)

# Polymarket Bot V3 Maker & Timeout Parameters
USER_V3_BUY_SLIPPAGE_CENTS = 0.01          # 1 cent (+0.01) buffer above ask for instant marketable limit buy fill
USER_V3_MAKER_OFFSET_CENTS = 0.01         # Alias for buy offset
USER_V3_MAKER_ORDER_TIMEOUT_SEC = 5.0     # 5 seconds order cancellation timeout
# ==============================================================================


@dataclass
class AppConfig:
    """
    Centralized bot configuration object for Polymarket Bot V3.
    Personal sensitive credentials (Telegram tokens, wallet keys) are read EXCLUSIVELY from .env file.
    """
    # Environment & Mode Toggles
    execution_mode: str = field(default_factory=lambda: _clean_str(os.getenv("EXECUTION_MODE", USER_EXECUTION_MODE)).upper())
    dry_run: bool = field(default_factory=lambda: _clean_str(os.getenv("EXECUTION_MODE", USER_EXECUTION_MODE)).upper() == "DRY_RUN")
    trading_active: bool = True
    
    # Database Settings
    db_path: str = field(default_factory=lambda: _clean_str(os.getenv("DB_PATH", "PolyDB_V3.sqlite")) or "PolyDB_V3.sqlite")
    busy_timeout_ms: int = 30000

    # Polymarket Endpoint URLs
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com/events"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # Polymarket Authentication
    polymarket_api_key: str = field(default_factory=lambda: _clean_str(os.getenv("POLYMARKET_API_KEY", "")))
    polymarket_secret: str = field(default_factory=lambda: _clean_str(os.getenv("POLYMARKET_SECRET", "")))
    polymarket_passphrase: str = field(default_factory=lambda: _clean_str(os.getenv("POLYMARKET_PASSPHRASE", "")))
    polymarket_private_key: str = field(default_factory=lambda: _clean_str(os.getenv("POLYMARKET_PRIVATE_KEY", "")))
    polymarket_funder: str = field(default_factory=lambda: _clean_str(os.getenv("POLYMARKET_FUNDER", "")))
    polymarket_signature_type: int = field(default_factory=lambda: _safe_int(
        os.getenv("POLYMARKET_SIGNATURE_TYPE"), 
        3 if _clean_str(os.getenv("POLYMARKET_FUNDER")) else 0
    ))

    # Telegram Credentials & Routing
    telegram_bot_token: str = field(default_factory=lambda: _clean_str(os.getenv("TELEGRAM_BOT_TOKEN", "")))
    telegram_chat_id: str = field(default_factory=lambda: _clean_str(os.getenv("TELEGRAM_CHAT_ID", "")))
    telegram_authorized_user_ids: List[int] = field(default_factory=lambda: parse_int_list(os.getenv("TELEGRAM_AUTHORIZED_USER_IDS", "")))
    telegram_enabled: bool = field(default_factory=lambda: bool(_clean_str(os.getenv("TELEGRAM_BOT_TOKEN")) and _clean_str(os.getenv("TELEGRAM_CHAT_ID"))))

    # V2 / V3 Strategy Parameters
    v2_momentum_threshold_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V2_MOMENTUM_THRESHOLD_CENTS"), USER_V2_MOMENTUM_THRESHOLD_CENTS))
    v2_momentum_window_sec: float = field(default_factory=lambda: _safe_float(os.getenv("V2_MOMENTUM_WINDOW_SEC"), USER_V2_MOMENTUM_WINDOW_SEC))
    v2_take_profit_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V2_TAKE_PROFIT_CENTS"), USER_V2_TAKE_PROFIT_CENTS))
    v2_high_odds_cutoff: float = field(default_factory=lambda: _safe_float(os.getenv("V2_HIGH_ODDS_CUTOFF"), USER_V2_HIGH_ODDS_CUTOFF))
    v2_high_odds_tp_target: float = field(default_factory=lambda: _safe_float(os.getenv("V2_HIGH_ODDS_TP_TARGET"), USER_V2_HIGH_ODDS_TP_TARGET))
    v2_trailing_sl_enabled: bool = field(default_factory=lambda: _clean_str(os.getenv("V2_TRAILING_SL_ENABLED", str(USER_V2_TRAILING_SL_ENABLED))).lower() in ("true", "1", "yes"))
    v2_trailing_sl_distance_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V2_TRAILING_SL_DISTANCE_CENTS"), USER_V2_TRAILING_SL_DISTANCE_CENTS))
    v2_stop_loss_slippage_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V2_STOP_LOSS_SLIPPAGE_CENTS"), USER_V2_STOP_LOSS_SLIPPAGE_CENTS))
    v2_min_entry_odds_floor: float = field(default_factory=lambda: _safe_float(os.getenv("V2_MIN_ENTRY_ODDS_FLOOR"), USER_V2_MIN_ENTRY_ODDS_FLOOR))
    v2_max_entry_odds_ceiling: float = field(default_factory=lambda: _safe_float(os.getenv("V2_MAX_ENTRY_ODDS_CEILING"), USER_V2_MAX_ENTRY_ODDS_CEILING))
    trade_size_shares: float = field(default_factory=lambda: _safe_float(os.getenv("TRADE_SIZE_SHARES") or os.getenv("MAX_POSITION_SIZE_SHARES"), USER_V3_TRADE_SIZE_SHARES))

    @property
    def max_position_size_usd(self) -> float:
        """Dynamic USD exposure based on share count (at max price $1.00)"""
        return round(self.trade_size_shares * 1.0, 2)
    max_candle_entry_sec: float = field(default_factory=lambda: _safe_float(os.getenv("MAX_CANDLE_ENTRY_SEC"), USER_V3_MAX_CANDLE_ENTRY_SEC))
    max_active_positions: int = field(default_factory=lambda: _safe_int(os.getenv("MAX_ACTIVE_POSITIONS"), USER_V2_MAX_ACTIVE_POSITIONS))

    # V3 Execution Configuration
    v3_buy_slippage_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V3_BUY_SLIPPAGE_CENTS") or os.getenv("V3_MAKER_OFFSET_CENTS"), USER_V3_BUY_SLIPPAGE_CENTS))
    v3_maker_offset_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V3_BUY_SLIPPAGE_CENTS") or os.getenv("V3_MAKER_OFFSET_CENTS"), USER_V3_MAKER_OFFSET_CENTS))
    v3_maker_order_timeout_sec: float = field(default_factory=lambda: _safe_float(os.getenv("V3_MAKER_ORDER_TIMEOUT_SEC"), USER_V3_MAKER_ORDER_TIMEOUT_SEC))

    # Risk Engine Guardrails
    max_daily_drawdown_pct: float = 0.15
    max_consecutive_losses: int = 4
    max_var_limit_usd: float = 100.0
    min_liquidity_threshold_usd: float = 500.0

    def is_dry_run(self) -> bool:
        return self.execution_mode == "DRY_RUN" or self.dry_run

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode.upper()
        self.dry_run = (self.execution_mode == "DRY_RUN")

    def set_trading_active(self, active: bool) -> None:
        self.trading_active = active


config = AppConfig()
