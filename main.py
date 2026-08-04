"""
Polymarket Bot V2 Main Entry Point
Tick-by-Tick Odds Momentum Strategy Engine (Standalone Polymarket Ingestion)
"""

import sys
import time
import signal
import logging
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from src.config import config
from src.database.connection import PolyDBManager, AsyncDBWriter
from src.polymarket.token_resolver import PolymarketTokenResolver, MinuteOddsTracker
from src.polymarket.polymarket_ws import PolymarketWSClient
from src.execution.strategy import V2OddsMomentumStrategy
from src.notifications.notifier import TelegramNotifier
from src.notifications.telegram_bot import TelegramCommandRouter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("PolyBotMainV2")


class PolymarketBot:
    """
    Polymarket Bot V2 Orchestration Engine.
    Operates strictly on Polymarket CLOB WebSocket ticks (Binance feeds and ML models removed).
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.db_path
        self.db_manager = PolyDBManager(db_path=self.db_path)
        self.async_writer = AsyncDBWriter(self.db_manager)

        # Polymarket Stream & Token Resolver
        self.polymarket_ws = PolymarketWSClient()
        self.token_resolver = PolymarketTokenResolver()
        self.minute_tracker = MinuteOddsTracker()

        # Telegram Notifications & Remote Command Router
        self.notifier = TelegramNotifier()
        self.telegram_bot = TelegramCommandRouter(self.notifier, db_path=self.db_path)

        # V2 Odds Momentum Strategy Engine
        self.v2_strategy = V2OddsMomentumStrategy(self.async_writer, notifier=self.notifier)

        self.running = False
        self._last_preflight_sec = -1
        self._last_tick_log = 0.0

    def start(self) -> None:
        """
        Starts database writer, Telegram services, and initial Polymarket WebSocket subscription.
        """
        logger.info("==================================================================")
        logger.info("🚀 STARTING POLYMARKET BOT V2 (Standalone Odds Momentum Engine)")
        logger.info(f"   Execution Mode : {config.execution_mode}")
        logger.info(f"   Database Path  : {self.db_path}")
        logger.info(f"   Strategy       : +{config.v2_momentum_threshold_cents} Cent Shift in {config.v2_momentum_window_sec}s | Min Odds Floor=${config.v2_min_entry_odds_floor:.2f}")
        logger.info(f"   Risk Exits     : TP=+${config.v2_take_profit_cents:.2f} (+{config.v2_take_profit_cents*100:.0f}¢) | Trailing SL=-${config.v2_trailing_sl_distance_cents:.2f} (-{config.v2_trailing_sl_distance_cents*100:.0f}¢)")
        logger.info("==================================================================")

        self.running = True
        self.async_writer.start()

        if getattr(config, "telegram_enabled", True):
            self.notifier.start()
            self.telegram_bot.start()

        # Initial T0 token resolution for current active 5m candle
        now_ts = int(time.time())
        curr_candle_sec = (now_ts // 300) * 300
        curr_candle_ts_ms = curr_candle_sec * 1000

        resolved = self.token_resolver.get_or_resolve_candle_tokens(curr_candle_sec)
        if resolved:
            up_tok, dn_tok = resolved[0], resolved[1]
            logger.info(f"Resolved current 5m active tokens for candle {curr_candle_sec}: UP={up_tok} | DOWN={dn_tok}")
            self.polymarket_ws.subscribe_tokens(up_tok, dn_tok)
            self.polymarket_ws.start()
        else:
            logger.error("Failed initial Polymarket token resolution. Retrying in loop...")
            self.polymarket_ws.start()

    def stop(self) -> None:
        """
        Gracefully stops background services and flushes pending writes.
        """
        logger.info("Initiating graceful shutdown...")
        self.running = False

        if hasattr(self, "telegram_bot"):
            logger.info("Stopping Telegram command router...")
            self.telegram_bot.stop()

        if hasattr(self, "notifier"):
            logger.info("Stopping Telegram notifier...")
            self.notifier.stop()

        if hasattr(self, "polymarket_ws"):
            logger.info("Stopping Polymarket WebSocket client...")
            self.polymarket_ws.stop()

        if hasattr(self, "async_writer"):
            logger.info("Flushing pending database write queue...")
            self.async_writer.stop(timeout=5.0)

        if hasattr(self, "db_manager"):
            self.db_manager.close_thread_connection()

        logger.info("Shutdown complete. Polymarket Bot V2 stopped.")

    def _process_live_ticks(self) -> None:
        """
        Fetches live orderbook bids/asks from Polymarket WebSocket stream, evaluates V2 strategy,
        and logs real-time tick status.
        """
        now = time.time()
        candle_start_sec = (int(now) // 300) * 300
        dt = datetime.fromtimestamp(candle_start_sec, tz=timezone.utc)
        candle_start_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        slug = f"btc-updown-5m-{candle_start_sec}"

        tokens_tuple = self.token_resolver.get_or_resolve_candle_tokens(candle_start_sec)
        up_tok = tokens_tuple[0] if tokens_tuple else None
        dn_tok = tokens_tuple[1] if tokens_tuple else None

        up_bid, up_ask, dn_bid, dn_ask = self.polymarket_ws.get_live_bid_ask(up_tok, dn_tok)

        # 1. Feed real-time ticks into V2 Odds Momentum Strategy Engine
        if up_tok and up_ask is not None:
            self.v2_strategy.process_tick(candle_start_str, slug, "UP", up_tok, up_bid, up_ask)
        if dn_tok and dn_ask is not None:
            self.v2_strategy.process_tick(candle_start_str, slug, "DOWN", dn_tok, dn_bid, dn_ask)

        # 2. Log status every 3 seconds
        if (now - self._last_tick_log) >= 3.0:
            self._last_tick_log = now
            if up_bid is not None and up_ask is not None:
                mid_up = round((up_bid + up_ask) / 2.0, 3)
                mid_dn = round((dn_bid + dn_ask) / 2.0, 3)
                self.minute_tracker.update_tick(int(now), mid_up, mid_dn, candle_start_sec)

                logger.info(
                    f"[V2 STREAM TICK] Candle ({candle_start_str}) | "
                    f"UP (Bid: ${up_bid:.3f} / Ask: ${up_ask:.3f}) | "
                    f"DOWN (Bid: ${dn_bid:.3f} / Ask: ${dn_ask:.3f})"
                )
            else:
                logger.info(f"[V2 STREAM TICK] Candle ({candle_start_str}) | Polymarket Odds: [WAITING FOR WS TIK]")

    def run_loop(self) -> None:
        """
        Main execution loop listening for system signals and orchestrating components.
        """
        self.start()

        def signal_handler(sig, frame):
            logger.info(f"Signal {sig} received.")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            logger.info("Press Ctrl+C in your terminal to stop the bot.")
            while self.running:
                now_ts = int(time.time())
                now_sec = now_ts % 300

                # T-5s Pre-Flight Token Resolution Trigger (at 4m55s / 295s into interval)
                if now_sec == 295 and self._last_preflight_sec != now_ts:
                    self._last_preflight_sec = now_ts
                    curr_candle_ts_ms = (now_ts // 300) * 300 * 1000
                    curr_tok_info = self.token_resolver.cached_tokens.get(str(curr_candle_ts_ms))
                    curr_up = curr_tok_info[1] if curr_tok_info else None
                    curr_dn = curr_tok_info[2] if curr_tok_info else None

                    next_candle_ts_ms = (curr_candle_ts_ms // 1000 + 300) * 1000
                    logger.info("T-5s Pre-Flight Trigger Fired. Resolving Polymarket token contract IDs...")
                    resolved = self.token_resolver.resolve_next_candle_tokens(next_candle_ts_ms)
                    next_up = resolved[1] if resolved else None
                    next_dn = resolved[2] if resolved else None

                    # Subscribe to BOTH current active candle tokens AND upcoming candle tokens
                    self.polymarket_ws.subscribe_tokens(curr_up, curr_dn, next_up, next_dn)

                # T=0s 5m Candle Boundary Handoff Trigger (reconnect for new candle tokens)
                if now_sec == 0 and getattr(self, "_last_boundary_sec", -1) != now_ts:
                    self._last_boundary_sec = now_ts
                    new_active_ts_ms = (now_ts // 300) * 300 * 1000
                    new_tok_info = self.token_resolver.get_or_resolve_candle_tokens(now_ts // 300 * 300)
                    if new_tok_info:
                        new_up, new_dn = new_tok_info[0], new_tok_info[1]
                        logger.info(f"🔄 5m Candle Boundary Handoff: Reconnecting WebSocket for active candle tokens: UP={new_up[:12]}... | DOWN={new_dn[:12]}...")
                        self.polymarket_ws.reconnect_for_tokens(new_up, new_dn)

                # Process live ticks on every loop iteration
                self._process_live_ticks()
                time.sleep(0.5)

        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            logger.error(f"Unexpected error in bot main loop: {e}", exc_info=True)
            self.stop()


def main():
    bot = PolymarketBot()
    bot.run_loop()


if __name__ == "__main__":
    main()
