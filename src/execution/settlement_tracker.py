"""
Background Settlement Tracker for Polymarket Bot V3.
Asynchronously polls Polymarket Gamma API to verify oracle market resolution (~1-2 minutes post-candle),
reconciles hedged & expired positions, updates SQLite database, and dispatches settlement notifications.
"""

import time
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import httpx

from src.config import config
from src.database.connection import PolyDBManager, AsyncDBWriter
from src.notifications.notifier import TelegramNotifier

logger = logging.getLogger("SettlementTracker")


class SettlementTracker:
    """
    Manages post-candle asynchronous resolution and settlement tracking.
    Operates in a background daemon thread so the main execution strategy can immediately
    trade the next 5-minute candle with zero downtime.
    """

    def __init__(
        self,
        db_manager: Optional[PolyDBManager] = None,
        async_writer: Optional[AsyncDBWriter] = None,
        notifier: Optional[TelegramNotifier] = None
    ):
        self.db_manager = db_manager
        self.async_writer = async_writer
        self.notifier = notifier
        self._pending_positions: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._running = True
        self._poll_interval_sec = 15.0

        # Start background polling thread
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="SettlementTrackerThread")
        self._worker_thread.start()
        logger.info("✓ [SETTLEMENT TRACKER] Background resolution worker initialized (15s polling cycle).")

    def enqueue_settlement(self, position: Dict[str, Any]) -> None:
        """
        Enqueues a hedged or expired position to be tracked for oracle resolution.
        """
        if not position:
            return

        with self._lock:
            trade_id = position.get("Trade_Id") or position.get("Buy_Order_Id")
            for p in self._pending_positions:
                if (p.get("Trade_Id") and p.get("Trade_Id") == trade_id) or (p.get("Buy_Order_Id") and p.get("Buy_Order_Id") == trade_id):
                    return

            pos_copy = dict(position)
            pos_copy["Enqueued_At_Sec"] = time.time()
            self._pending_positions.append(pos_copy)

            slug = pos_copy.get("Slug", "unknown")
            side = pos_copy.get("Position_Side", "UNKNOWN")
            status = pos_copy.get("Position_Status", "PENDING_SETTLEMENT")
            logger.info(
                f"📥 [SETTLEMENT QUEUE] Enqueued position for background resolution tracking: "
                f"Slug={slug} | Side={side} | Status={status}"
            )

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self._pending_positions)

    def start(self) -> None:
        """
        Starts background resolution polling thread if not already running.
        """
        with self._lock:
            self._running = True
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="SettlementTrackerThread")
                self._worker_thread.start()
                logger.info("✓ [SETTLEMENT TRACKER] Background resolution worker started (15s polling cycle).")

    def stop(self) -> None:
        self._running = False

    def _run_loop(self) -> None:
        """
        Periodically checks Polymarket Gamma API for resolution status of all queued positions.
        """
        while self._running:
            try:
                time.sleep(self._poll_interval_sec)
                self._check_pending_resolutions()
            except Exception as e:
                logger.error(f"Error in settlement tracker run loop: {e}", exc_info=True)

    def _check_pending_resolutions(self) -> None:
        with self._lock:
            if not self._pending_positions:
                return
            current_queue = list(self._pending_positions)

        resolved_items = []

        for pos in current_queue:
            slug = pos.get("Slug")
            if not slug:
                continue

            try:
                event_data = self._fetch_gamma_event(slug)
                if not event_data:
                    if (time.time() - pos.get("Enqueued_At_Sec", time.time())) > 600.0:
                        self._finalize_settlement(pos, winning_outcome="EXPIRED_FORCE", is_hedged=pos.get("Position_Status") == "HEDGED_LOCKED")
                        resolved_items.append(pos)
                    continue

                is_closed = event_data.get("closed", False)
                markets = event_data.get("markets", [])
                
                market = markets[0] if markets else {}
                market_closed = market.get("closed", False) or is_closed
                winning_outcome = None

                if market_closed:
                    outcome_prices = market.get("outcomePrices")
                    if outcome_prices:
                        try:
                            prices_list = outcome_prices if isinstance(outcome_prices, list) else eval(outcome_prices)
                            if len(prices_list) >= 2:
                                p_up = float(prices_list[0])
                                p_down = float(prices_list[1])
                                if p_up > 0.90:
                                    winning_outcome = "UP"
                                elif p_down > 0.90:
                                    winning_outcome = "DOWN"
                        except Exception:
                            pass

                    if not winning_outcome and market.get("winningOutcome"):
                        winning_outcome = str(market.get("winningOutcome")).upper()

                if market_closed and winning_outcome:
                    is_hedged = (pos.get("Position_Status") == "HEDGED_LOCKED")
                    self._finalize_settlement(pos, winning_outcome=winning_outcome, is_hedged=is_hedged)
                    resolved_items.append(pos)
                elif (time.time() - pos.get("Enqueued_At_Sec", time.time())) > 300.0:
                    if pos.get("Position_Status") == "HEDGED_LOCKED":
                        self._finalize_settlement(pos, winning_outcome="PAR_SETTLED", is_hedged=True)
                        resolved_items.append(pos)

            except Exception as e:
                logger.debug(f"Resolution check failed for slug {slug}: {e}")

        if resolved_items:
            with self._lock:
                self._pending_positions = [p for p in self._pending_positions if p not in resolved_items]

    def _fetch_gamma_event(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Queries Polymarket Gamma API for event/market details.
        """
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        return data[0]
                    elif isinstance(data, dict):
                        return data
        except Exception as e:
            logger.debug(f"HTTP GET failed for {url}: {e}")
        return None

    def _finalize_settlement(
        self,
        pos: Dict[str, Any],
        winning_outcome: str,
        is_hedged: bool = False
    ) -> None:
        """
        Calculates final settlement PnL, updates SQLite, and sends Telegram alert.
        """
        now_dt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        slug = pos.get("Slug", "unknown")
        side = pos.get("Position_Side", "UP")
        filled_qty = float(pos.get("Filled_Quantity") or pos.get("Target_Quantity") or 0.0)
        entry_price = float(pos.get("Average_Fill_Price") or pos.get("Target_Buy_Price") or 0.0)
        primary_cost = round(filled_qty * entry_price, 4)

        if is_hedged:
            hedge_qty = float(pos.get("Hedge_Quantity") or filled_qty)
            hedge_price = float(pos.get("Hedge_Buy_Price") or 0.35)
            hedge_cost = round(hedge_qty * hedge_price, 4)
            total_cost = primary_cost + hedge_cost
            payout = round(1.00 * hedge_qty, 4)
            pnl = round(payout - total_cost, 4)
            outcome_str = "HEDGED_RESOLVED"
            reason_str = f"Synthetic Cross-Hedge Resolved ({winning_outcome})"
        else:
            is_win = (winning_outcome == side)
            payout = round(1.00 * filled_qty, 4) if is_win else 0.0
            pnl = round(payout - primary_cost, 4)
            outcome_str = "WIN" if is_win else "LOSS"
            reason_str = f"Candle Settled ({winning_outcome})"

        logger.info(
            f"🏁 [SETTLEMENT CONFIRMED] Slug={slug} | Winning_Side={winning_outcome} | "
            f"Type={'HEDGED' if is_hedged else 'STANDARD'} | Payout=${payout:.4f} | PnL=${pnl:+.4f} USDC"
        )

        buy_order_id = pos.get("Buy_Order_Id")
        trade_id = pos.get("Trade_Id")

        if self.async_writer:
            sql = """
                UPDATE Positions
                SET Position_Status = 'RESOLVED_SETTLED',
                    Exit_Timestamp = ?,
                    Exit_Price = ?,
                    Exit_Reason = ?,
                    Trade_Outcome = ?,
                    Pnl = ?,
                    Updated_At = ?
                WHERE (Trade_Id = ? OR Buy_Order_Id = ?);
            """
            self.async_writer.enqueue_write(
                sql,
                (now_dt, 1.0 if is_hedged or winning_outcome == side else 0.0, reason_str, outcome_str, pnl, now_dt, trade_id, buy_order_id)
            )

        if self.notifier and getattr(config, "telegram_enabled", False):
            msg = (
                f"🏁 <b>Polymarket Candle Settlement Confirmed</b>\n"
                f"• <b>Market:</b> <code>{slug}</code>\n"
                f"• <b>Winning Side:</b> <b>{winning_outcome}</b>\n"
                f"• <b>Position Type:</b> {'🛡️ Synthetic Hedge Lock' if is_hedged else f'Single {side}'}\n"
                f"• <b>Shares:</b> <code>{filled_qty:.2f}</code>\n"
                f"• <b>Gross Payout:</b> <code>${payout:.2f} USDC</code>\n"
                f"• <b>Net PnL:</b> <code>${pnl:+.2f} USDC</code>\n"
                f"• <b>Status:</b> <code>RESOLVED_SETTLED</code>"
            )
            try:
                self.notifier.enqueue_notification(msg)
            except Exception as e:
                logger.debug(f"Failed to enqueue Telegram settlement notice: {e}")
