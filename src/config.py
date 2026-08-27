"""
Global Configuration for Polymarket Momentum Bot V4
(Sprint 4: High-Odds Trend Following Strategy: 84¢ Entry, 99¢ TP, 40¢ SL)
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==============================================================================
# USER-FACING ADJUSTABLE PARAMETERS (Sprint 4: Bot V4 High-Odds Strategy)
# ==============================================================================
# Bot V4 High-Odds Trend Trigger Parameters
USER_V4_ENTRY_ODDS_THRESHOLD = 0.84        # Enter BUY when UP or DOWN odds >= 84 cents ($0.84)
USER_V4_MAX_ENTRY_ODDS_CEILING = 0.88      # Max entry odds ceiling (do not buy if price has already surged > 88 cents)
USER_V4_TAKE_PROFIT_PRICE = 0.99           # Resting Limit Sell Take Profit target ($0.99)
USER_V4_STOP_LOSS_PRICE = 0.40             # Trigger Stop Loss when odds drop <= 40 cents ($0.40)
USER_V4_STOP_LOSS_SLIPPAGE_CENTS = 0.02    # 2 cents slippage discount for aggressive SL limit sell ($0.02)
USER_V4_MAX_POSITION_SIZE_USD = 5.0        # Max position size per trade ($5.00 USDC)
USER_V4_MAX_ACTIVE_POSITIONS = 1           # Single active position limit across bot (1 position)
USER_V4_ORDER_TIMEOUT_SEC = 5.0            # 5 seconds order cancellation timeout
# ==============================================================================


@dataclass
class BotConfig:
    # Execution & System Mode
    execution_mode: str = os.getenv("EXECUTION_MODE", "LIVE")
    db_path: str = os.getenv("DB_PATH", "PolyDB_V4.sqlite")
    trading_active: bool = field(default_factory=lambda: os.getenv("TRADING_ACTIVE", "true").lower() in ("true", "1", "yes"))

    # Polymarket API Credentials
    polymarket_api_key: str = os.getenv("POLYMARKET_API_KEY", "")
    polymarket_secret: str = os.getenv("POLYMARKET_SECRET", "")
    polymarket_passphrase: str = os.getenv("POLYMARKET_PASSPHRASE", "")
    polymarket_private_key: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    polymarket_funder: str = os.getenv("POLYMARKET_FUNDER", "")

    # Polymarket Market Identifier & Endpoints
    polymarket_condition_id: str = os.getenv("POLYMARKET_CONDITION_ID", "")
    polymarket_clob_url: str = os.getenv("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
    polymarket_ws_url: str = os.getenv("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
    polymarket_gamma_url: str = os.getenv("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")

    # Binance WebSocket & REST Endpoints
    binance_ws_url: str = os.getenv("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws/btcusdt@depth@100ms")
    binance_rest_url: str = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
    binance_symbol: str = os.getenv("BINANCE_SYMBOL", "BTCUSDT")

    # Telegram Notifications & Slash Commands
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_authorized_user_ids: List[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("TELEGRAM_AUTHORIZED_USER_IDS", "").split(",") if x.strip().isdigit()
    ])
    telegram_enabled: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ENABLED", "true").lower() in ("true", "1", "yes"))

    # Bot V4 Strategy Configuration (Overridable via env vars)
    v4_entry_odds_threshold: float = float(os.getenv("V4_ENTRY_ODDS_THRESHOLD", str(USER_V4_ENTRY_ODDS_THRESHOLD)))
    v4_max_entry_odds_ceiling: float = float(os.getenv("V4_MAX_ENTRY_ODDS_CEILING", str(USER_V4_MAX_ENTRY_ODDS_CEILING)))
    v4_take_profit_price: float = float(os.getenv("V4_TAKE_PROFIT_PRICE", str(USER_V4_TAKE_PROFIT_PRICE)))
    v4_stop_loss_price: float = float(os.getenv("V4_STOP_LOSS_PRICE", str(USER_V4_STOP_LOSS_PRICE)))
    v4_stop_loss_slippage_cents: float = float(os.getenv("V4_STOP_LOSS_SLIPPAGE_CENTS", str(USER_V4_STOP_LOSS_SLIPPAGE_CENTS)))
    max_position_size_usd: float = float(os.getenv("MAX_POSITION_SIZE_USD", str(USER_V4_MAX_POSITION_SIZE_USD)))
    max_active_positions: int = int(os.getenv("MAX_ACTIVE_POSITIONS", str(USER_V4_MAX_ACTIVE_POSITIONS)))
    v4_order_timeout_sec: float = float(os.getenv("V4_ORDER_TIMEOUT_SEC", str(USER_V4_ORDER_TIMEOUT_SEC)))

    # Risk Engine & System Limits
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.15"))
    max_consecutive_losses: int = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "4"))
    max_var_limit_usd: float = float(os.getenv("MAX_VAR_LIMIT_USD", "100.0"))
    min_liquidity_threshold_usd: float = float(os.getenv("MIN_LIQUIDITY_THRESHOLD_USD", "500.0"))

    def is_dry_run(self) -> bool:
        return self.execution_mode.upper() in ("DRY_RUN", "SIMULATION", "PAPER")

    def set_trading_active(self, active: bool) -> None:
        self.trading_active = active

    def set_execution_mode(self, mode: str) -> None:
        self.execution_mode = mode.upper()


config = BotConfig()
