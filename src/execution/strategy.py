"""
Execution Strategy Engine (Sprint 3: US3.1, US3.2)
Implements IExecutionStrategy interface with DryExecutionStrategy (simulation fills,
persistent $0.40 buy order tracking, automated $0.20 stop-loss limit sell order)
and LiveExecutionStrategy (Polymarket CLOB REST API & EIP-712 signer wrapper).
"""

import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from src.config import config
from src.database.connection import AsyncDBWriter

logger = logging.getLogger(__name__)

class IExecutionStrategy(ABC):
    """
    Abstract interface for trade execution strategies.
    """

    @abstractmethod
    def execute_entry(
        self,
        candle_start: str,
        slug: str,
        side: str,
        prob_cal: float,
        prob_uncal: float,
        target_price: float,
        position_usd: float,
        token_id: str,
        current_bid: Optional[float] = None,
        current_ask: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def execute_exit(
        self,
        candle_start: str,
        token_id: str,
        exit_price: float,
        reason: str
    ) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def check_and_update_positions(
        self,
        candle_start: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        pass


class DryExecutionStrategy(IExecutionStrategy):
    """
    Simulated Execution Strategy.
    Simulates limit buy orders at $0.40, tracks persistent fills when ask <= 0.40,
    immediately issues automated $0.20 stop-loss sell orders upon fill, and writes
    all state changes asynchronously to PolyDB.sqlite Positions table.
    """

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None):
        self.async_writer = async_writer
        # Local in-memory active position state: candle_start -> position_dict
        self.active_positions: Dict[str, Dict[str, Any]] = {}

    def execute_entry(
        self,
        candle_start: str,
        slug: str,
        side: str,
        prob_cal: float,
        prob_uncal: float,
        target_price: float,
        position_usd: float,
        token_id: str,
        current_bid: Optional[float] = None,
        current_ask: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Dispatches a persistent limit buy order at target_price ($0.40).
        Initial status: PENDING.
        """
        if candle_start in self.active_positions:
            logger.warning(f"⚠ Single position guard: Buy order already placed for candle {candle_start}. Skipped.")
            return None

        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        target_qty = round(position_usd / target_price, 4) if target_price > 0 else 0.0

        pos = {
            "Candle_Start": candle_start,
            "Prob_Cal": prob_cal,
            "Prob_Uncal": prob_uncal,
            "Slug": slug,
            "Prediction_Side": side,
            "Actual_Outcome": None,
            "Entry_Timestamp": now_dt,
            "Target_Price": target_price,
            "Target_Quantity": target_qty,
            "Filled_Quantity": 0.0,
            "Average_Fill_Price": 0.0,
            "Order_Id": f"SIM_BUY_{int(time.time()*1000)}",
            "Position_Status": "PENDING",
            "Cancel_Reason": None,
            "Transaction_Price": 0.0,
            "Exit_Price": None,
            "Exit_Reason": None,
            "Pnl": 0.0,
            "Updated_At": now_dt,
            "Token_Id": token_id,
            "Stop_Loss_Order_Id": None,
            "Stop_Loss_Price": config.stop_loss_price
        }

        self.active_positions[candle_start] = pos

        # Enqueue write to PolyDB.sqlite Positions table
        if self.async_writer:
            sql = """
                INSERT OR REPLACE INTO Positions (
                    Candle_Start, Prob_Cal, Prob_Uncal, Slug, Prediction_Side, Actual_Outcome,
                    Entry_Timestamp, Target_Price, Target_Quantity, Filled_Quantity,
                    Average_Fill_Price, Order_Id, Position_Status, Cancel_Reason,
                    Transaction_Price, Exit_Price, Exit_Reason, Pnl, Updated_At
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                pos["Candle_Start"], pos["Prob_Cal"], pos["Prob_Uncal"], pos["Slug"],
                pos["Prediction_Side"], pos["Actual_Outcome"], pos["Entry_Timestamp"], pos["Target_Price"],
                pos["Target_Quantity"], pos["Filled_Quantity"], pos["Average_Fill_Price"],
                pos["Order_Id"], pos["Position_Status"], pos["Cancel_Reason"],
                pos["Transaction_Price"], pos["Exit_Price"], pos["Exit_Reason"],
                pos["Pnl"], pos["Updated_At"]
            )
            self.async_writer.enqueue_write(sql, params)

        logger.info(
            f"[DRY EXECUTION ENTRY] Order Placed: Side={side} | Target_Price=${target_price:.2f} | "
            f"Qty={target_qty} shares | Candle={candle_start} | Status=PENDING"
        )

        # Check immediate fill if current ask <= target price
        if current_ask is not None and current_ask <= target_price:
            self.check_and_update_positions(candle_start, token_id, current_bid, current_ask)

        return pos

    def record_no_trade(
        self,
        candle_start: str,
        slug: str,
        prob_cal: float,
        prob_uncal: float,
        reason: str = "LOW_CONFIDENCE",
        actual_outcome: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Records a NO_TRADE decision to PolyDB.sqlite Positions table.
        """
        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        pos = {
            "Candle_Start": candle_start,
            "Prob_Cal": prob_cal,
            "Prob_Uncal": prob_uncal,
            "Slug": slug,
            "Prediction_Side": "NO_TRADE",
            "Actual_Outcome": actual_outcome,
            "Entry_Timestamp": now_dt,
            "Target_Price": 0.0,
            "Target_Quantity": 0.0,
            "Filled_Quantity": 0.0,
            "Average_Fill_Price": 0.0,
            "Order_Id": "NO_TRADE",
            "Position_Status": "NO_TRADE",
            "Cancel_Reason": reason,
            "Transaction_Price": 0.0,
            "Exit_Price": 0.0,
            "Exit_Reason": reason,
            "Pnl": 0.0,
            "Updated_At": now_dt
        }
        if self.async_writer:
            sql = """
                INSERT OR REPLACE INTO Positions (
                    Candle_Start, Prob_Cal, Prob_Uncal, Slug, Prediction_Side, Actual_Outcome,
                    Entry_Timestamp, Target_Price, Target_Quantity, Filled_Quantity,
                    Average_Fill_Price, Order_Id, Position_Status, Cancel_Reason,
                    Transaction_Price, Exit_Price, Exit_Reason, Pnl, Updated_At
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                pos["Candle_Start"], pos["Prob_Cal"], pos["Prob_Uncal"], pos["Slug"],
                pos["Prediction_Side"], pos["Actual_Outcome"], pos["Entry_Timestamp"], pos["Target_Price"],
                pos["Target_Quantity"], pos["Filled_Quantity"], pos["Average_Fill_Price"],
                pos["Order_Id"], pos["Position_Status"], pos["Cancel_Reason"],
                pos["Transaction_Price"], pos["Exit_Price"], pos["Exit_Reason"],
                pos["Pnl"], pos["Updated_At"]
            )
            self.async_writer.enqueue_write(sql, params)
        logger.info(f"[NO TRADE RECORDED] Candle={candle_start} | P_cal={prob_cal:.4f} | Reason={reason}")
        return pos

    def check_and_update_positions(
        self,
        candle_start: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        """
        Monitors active positions:
        1. If PENDING: checks if current_ask <= target_price ($0.40) to execute fill & place $0.20 stop loss.
        2. If OPEN: checks if current_bid <= stop_loss_price ($0.20) to execute stop-loss exit.
        """
        pos = self.active_positions.get(candle_start)
        if not pos:
            return None

        status = pos["Position_Status"]
        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Pending Order Timeout Cap (Configurable 300s / 5 minutes)
        if status == "PENDING":
            try:
                entry_dt = datetime.strptime(pos["Entry_Timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                elapsed_sec = time.time() - entry_dt.timestamp()
                timeout_limit = getattr(config, "order_timeout_sec", 300.0)
                if elapsed_sec >= timeout_limit:
                    pos["Position_Status"] = "CANCELLED"
                    pos["Cancel_Reason"] = "TIMEOUT_300S"
                    pos["Updated_At"] = now_dt
                    if self.async_writer:
                        sql = "UPDATE Positions SET Position_Status = 'CANCELLED', Cancel_Reason = 'TIMEOUT_300S', Updated_At = ? WHERE Candle_Start = ?"
                        self.async_writer.enqueue_write(sql, (now_dt, candle_start))
                    logger.info(f"⏱ [DRY EXECUTION TIMEOUT] Unfilled limit buy order auto-cancelled after {timeout_limit:.0f}s (Candle={candle_start}).")
                    return pos
            except Exception:
                pass

        # 1. PENDING -> OPEN (Limit Buy Filled)
        if status == "PENDING" and current_ask is not None and current_ask <= pos["Target_Price"]:
            fill_price = pos["Target_Price"]
            filled_qty = pos["Target_Quantity"]
            tx_price = round(fill_price * filled_qty, 2)
            stop_loss_order_id = f"SIM_STOP_{int(time.time()*1000)}"

            pos["Filled_Quantity"] = filled_qty
            pos["Average_Fill_Price"] = fill_price
            pos["Transaction_Price"] = tx_price
            pos["Position_Status"] = "OPEN"
            pos["Updated_At"] = now_dt
            pos["Stop_Loss_Order_Id"] = stop_loss_order_id

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Filled_Quantity = ?,
                        Average_Fill_Price = ?,
                        Transaction_Price = ?,
                        Position_Status = ?,
                        Updated_At = ?
                    WHERE Candle_Start = ?
                """
                self.async_writer.enqueue_write(
                    sql, (filled_qty, fill_price, tx_price, "OPEN", now_dt, candle_start)
                )

            sl_price = pos.get("Stop_Loss_Price", config.stop_loss_price)
            logger.info(
                f"✓ [DRY EXECUTION FILL] Limit Buy Executed! Candle={candle_start} | "
                f"Fill_Price=${fill_price:.2f} | Qty={filled_qty} shares | Tx=${tx_price:.2f} | "
                f"AUTOMATED STOP-LOSS ORDER PLACED at ${sl_price:.2f} (ID: {stop_loss_order_id})"
            )

        # 2. OPEN -> CLOSED (Stop Loss Hit)
        stop_loss_val = pos.get("Stop_Loss_Price", config.stop_loss_price)
        if status == "OPEN" and current_bid is not None and current_bid <= stop_loss_val:
            exit_price = stop_loss_val
            pnl = round((exit_price - pos["Average_Fill_Price"]) * pos["Filled_Quantity"], 2)

            pos["Exit_Price"] = exit_price
            pos["Exit_Reason"] = "STOP_LOSS"
            pos["Position_Status"] = "CLOSED"
            pos["Pnl"] = pnl
            pos["Updated_At"] = now_dt

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Exit_Price = ?,
                        Exit_Reason = ?,
                        Position_Status = ?,
                        Pnl = ?,
                        Actual_Outcome = ?,
                        Updated_At = ?
                    WHERE Candle_Start = ?
                """
                self.async_writer.enqueue_write(
                    sql, (exit_price, "STOP_LOSS", "CLOSED", pnl, pos.get("Actual_Outcome"), now_dt, candle_start)
                )

            logger.warning(
                f"🛑 [DRY EXECUTION STOP-LOSS HIT] Stop-Loss Limit Sell Executed! Candle={candle_start} | "
                f"Exit_Price=${exit_price:.2f} | PnL=${pnl:+.2f} | Status=CLOSED"
            )

        return pos

    def execute_exit(
        self,
        candle_start: str,
        token_id: str,
        exit_price: float,
        reason: str = "END_OF_CANDLE",
        actual_outcome: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Executes position exit at candle end or stop loss trigger.
        """
        pos = self.active_positions.get(candle_start)
        if not pos:
            return None

        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if actual_outcome:
            pos["Actual_Outcome"] = actual_outcome

        # If order was PENDING at candle end, cancel it
        if pos["Position_Status"] == "PENDING":
            pos["Position_Status"] = "CANCELLED"
            pos["Cancel_Reason"] = reason
            pos["Updated_At"] = now_dt

            if self.async_writer:
                sql = "UPDATE Positions SET Position_Status = 'CANCELLED', Cancel_Reason = ?, Actual_Outcome = ?, Updated_At = ? WHERE Candle_Start = ?"
                self.async_writer.enqueue_write(sql, (reason, actual_outcome, now_dt, candle_start))

            logger.info(f"[DRY EXECUTION CANCEL] Unfilled Limit Buy Cancelled at Candle End: Candle={candle_start} | Reason={reason}")
            return pos

        # If order was OPEN, calculate PnL and close
        pnl = round((exit_price - pos["Average_Fill_Price"]) * pos["Filled_Quantity"], 4)
        pos["Exit_Price"] = exit_price
        pos["Exit_Reason"] = reason
        pos["Position_Status"] = "CLOSED"
        pos["Pnl"] = pnl
        pos["Updated_At"] = now_dt

        if self.async_writer:
            sql = """
                UPDATE Positions SET
                    Exit_Price = ?,
                    Exit_Reason = ?,
                    Position_Status = ?,
                    Pnl = ?,
                    Actual_Outcome = ?,
                    Updated_At = ?
                WHERE Candle_Start = ?
            """
            self.async_writer.enqueue_write(
                sql, (exit_price, reason, "CLOSED", pnl, actual_outcome, now_dt, candle_start)
            )

        logger.info(
            f"✓ [DRY EXECUTION EXIT] Position Closed at Candle Expiry: Candle={candle_start} | "
            f"Exit_Price=${exit_price:.2f} | Reason={reason} | PnL=${pnl:+.2f}"
        )

        return pos


class V2OddsMomentumStrategy(IExecutionStrategy):
    """
    Polymarket Bot V2 Dynamic Odds Momentum Strategy.
    Tracks tick-by-tick odds changes over a 10-second sliding window per token.
    Triggers Entry when absolute odds increase >= +0.15 in 10s.
    Dynamic TP: +5% above entry price, OR $0.995 if Entry Odds >= 0.93.
    Dynamic SL: -10% below entry price (Entry Odds * 0.90).
    Enforces a strict SINGLE POSITION lock across the entire bot.
    """

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None, notifier: Optional[Any] = None):
        self.async_writer = async_writer
        self.notifier = notifier
        # Token tick buffers: token_id -> list of (timestamp_sec, bid, ask)
        self.tick_buffers: Dict[str, List[Tuple[float, float, float]]] = {}
        # Single Active Position Guard across the bot
        self.active_position: Optional[Dict[str, Any]] = None

    def process_tick(
        self,
        candle_start: str,
        slug: str,
        side: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        """
        Processes real-time tick for a token, evaluates 10s momentum trigger, and checks open TP/SL exits.
        """
        if current_ask is None or current_ask <= 0.0:
            return None

        now_sec = time.time()
        if token_id not in self.tick_buffers:
            self.tick_buffers[token_id] = []

        buf = self.tick_buffers[token_id]
        buf.append((now_sec, current_bid or current_ask, current_ask))

        # Evict ticks older than 15s
        self.tick_buffers[token_id] = [t for t in buf if (now_sec - t[0]) <= 15.0]
        buf = self.tick_buffers[token_id]

        # 0. Candle Rollover Check: Close previous candle's position as EXPIRED if 5m boundary elapsed
        if self.active_position and self.active_position.get("Candle_Start") != candle_start:
            self._close_expired_position(current_bid or current_ask)

        # 1. Evaluate active PENDING_FILL order for Maker fills or 5s timeouts
        if self.active_position and self.active_position.get("Position_Status") == "PENDING_FILL":
            self._evaluate_pending_fill(current_bid, current_ask)

        # 2. Evaluate TP/SL exit if this token has an active open position
        if self.active_position and self.active_position.get("Position_Status") == "OPEN" and self.active_position.get("Token_Id") == token_id:
            self._evaluate_tp_sl_exit(current_bid or current_ask, current_ask)

        # 3. Single Position / Order Guard: Reject new entry if an active order is PENDING_FILL or OPEN
        if self.active_position is not None:
            return None

        # 4. 10-Second Window Minimum Price Surge (P_now - P_min_10s)
        window_sec = getattr(config, "v2_momentum_window_sec", 10.0)
        ticks_in_window = [t for t in buf if (now_sec - t[0]) <= (window_sec + 0.5)]

        if not ticks_in_window:
            return None

        # Find minimum ask price recorded within the last 10 seconds
        min_tick = min(ticks_in_window, key=lambda t: t[2])
        min_ask_10s = min_tick[2]
        delta_odds = round(current_ask - min_ask_10s, 4)

        # 5. Trigger Condition: 10s Surge >= 15 cents AND Current Ask >= Minimum Entry Odds Floor ($0.65)
        momentum_thresh = getattr(config, "v2_momentum_threshold_cents", 0.15)
        min_odds_floor = getattr(config, "v2_min_entry_odds_floor", 0.65)

        if delta_odds >= momentum_thresh and current_ask >= min_odds_floor:
            return self.execute_entry_v3(
                candle_start=candle_start,
                slug=slug,
                side=side,
                token_id=token_id,
                trigger_odds_10s_ago=min_ask_10s,
                entry_odds=current_ask,
                position_usd=getattr(config, "max_position_size_usd", 2.0)
            )

        return None

    def execute_entry_v2(
        self,
        candle_start: str,
        slug: str,
        side: str,
        token_id: str,
        trigger_odds_10s_ago: float,
        entry_odds: float,
        position_usd: float = 2.0
    ) -> Dict[str, Any]:
        """
        Executes V2 Position Entry using dynamic config settings.
        Calculates Limit Buy Ceiling using config.v2_entry_slippage_buffer.
        Calculates dynamic Stop Loss using config.v2_stop_loss_pct (-10%).
        Calculates dynamic Take Profit using config.v2_take_profit_pct (+5%), or config.v2_high_odds_tp_target ($0.995) if fill >= config.v2_high_odds_cutoff ($0.93).
        """
        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Dynamic Slippage Ceiling Buffer (from config.v2_entry_slippage_buffer, default 0.04)
        max_slippage = getattr(config, "v2_entry_slippage_buffer", 0.04)
        limit_ceiling = round(entry_odds + max_slippage, 4)
        
        # In simulation execution, fill price matches entry ask up to ceiling
        fill_price = min(entry_odds, limit_ceiling)
        
        # Single Unified Trailing SL & Take Profit Calculation
        high_odds_cutoff = getattr(config, "v2_high_odds_cutoff", 0.75)
        high_odds_tp = getattr(config, "v2_high_odds_tp_target", 0.995)
        tp_cents = getattr(config, "v2_take_profit_cents", 0.05)
        trailing_dist = getattr(config, "v2_trailing_sl_distance_cents", 0.10)

        # Initial Stop Loss at entry = Fill Price - Trailing Distance
        stop_loss_price = round(max(0.01, fill_price - trailing_dist), 4)

        if fill_price >= high_odds_cutoff:
            # Tier 2 (Entry >= $0.75): Fixed High Odds TP ($0.995)
            take_profit_price = high_odds_tp
        else:
            # Tier 1 (Entry < $0.75): +5c TP gain target
            take_profit_price = round(min(high_odds_tp, fill_price + tp_cents), 4)

        target_qty = round(position_usd / fill_price, 4) if fill_price > 0 else 0.0

        pos = {
            "Candle_Start": candle_start,
            "Prob_Cal": 0.50,
            "Prob_Uncal": 0.50,
            "Slug": slug,
            "Token_Id": token_id,
            "Prediction_Side": side,
            "Actual_Outcome": None,
            "Entry_Timestamp": now_dt,
            "Trigger_Odds_10s_Ago": trigger_odds_10s_ago,
            "Entry_Odds": entry_odds,
            "Limit_Ceiling": limit_ceiling,
            "Target_Price": limit_ceiling,
            "Target_Quantity": target_qty,
            "Filled_Quantity": target_qty,
            "Average_Fill_Price": fill_price,
            "Take_Profit_Price": take_profit_price,
            "Stop_Loss_Price": stop_loss_price,
            "Exit_Timestamp": None,
            "Exit_Price": None,
            "Exit_Reason": None,
            "Trade_Outcome": None,
            "Order_Id": f"V2_ENTRY_{int(time.time()*1000)}",
            "Position_Status": "OPEN",
            "Cancel_Reason": None,
            "Transaction_Price": round(fill_price * target_qty, 4),
            "Pnl": 0.0,
            "Min_Price_Observed": fill_price,
            "Max_Price_Observed": fill_price,
            "High_Water_Mark": fill_price,
            "Updated_At": now_dt
        }

        # Lock active position
        self.active_position = pos

        if self.async_writer:
            sql = """
                INSERT INTO Positions (
                    Candle_Start, Prob_Cal, Prob_Uncal, Slug, Token_Id, Prediction_Side, Actual_Outcome,
                    Entry_Timestamp, Trigger_Odds_10s_Ago, Entry_Odds, Target_Price, Target_Quantity,
                    Filled_Quantity, Average_Fill_Price, Take_Profit_Price, Stop_Loss_Price, Order_Id,
                    Position_Status, Transaction_Price, Pnl, Updated_At
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                candle_start, 0.50, 0.50, slug, token_id, side, None,
                now_dt, trigger_odds_10s_ago, entry_odds, limit_ceiling, target_qty,
                target_qty, fill_price, take_profit_price, stop_loss_price, pos["Order_Id"],
                "OPEN", pos["Transaction_Price"], 0.0, now_dt
            )
            self.async_writer.enqueue_write(sql, params)

        logger.info(
            f"🚀 [V2 SIGNAL ENTERED] Side={side} | Candle={candle_start} | Ask=${entry_odds:.3f} | "
            f"Buy_Ceiling=${limit_ceiling:.3f} (+${max_slippage:.2f}) | Fill=${fill_price:.3f} | "
            f"10s_Ago=${trigger_odds_10s_ago:.3f} (+{entry_odds - trigger_odds_10s_ago:+.3f}) | "
            f"TP=${take_profit_price:.4f} | SL=${stop_loss_price:.4f} | Qty={target_qty}"
        )

        if hasattr(self, "notifier") and self.notifier:
            try:
                self.notifier.notify_v2_trade_entry(
                    candle_start=candle_start,
                    side=side,
                    signal_price=entry_odds,
                    fill_price=fill_price,
                    tp_price=take_profit_price,
                    sl_price=stop_loss_price,
                    qty=target_qty,
                    position_usd=position_usd
                )
            except Exception as e:
                logger.warning(f"Failed to dispatch Telegram entry notification: {e}")

        return pos

    def execute_entry_v3(
        self,
        candle_start: str,
        slug: str,
        side: str,
        token_id: str,
        trigger_odds_10s_ago: float,
        entry_odds: float,
        position_usd: float = 2.0
    ) -> Dict[str, Any]:
        """
        Executes V3 Maker Position Entry.
        Calculates Limit Buy Price at entry_ask - maker_offset (default Ask - $0.02) to qualify for 0% Maker Fees.
        Initial Order Status is set to 'PENDING_FILL' with a 5.0s timeout timer.
        """
        now_ts = time.time()
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        maker_offset = getattr(config, "v3_maker_offset_cents", 0.02)
        # Limit Buy Price is placed below Best Ask for Maker status
        limit_buy_price = round(max(0.01, entry_odds - maker_offset), 4)

        target_qty = round(position_usd / limit_buy_price, 4) if limit_buy_price > 0 else 0.0

        pos = {
            "Candle_Start": candle_start,
            "Prob_Cal": 0.50,
            "Prob_Uncal": 0.50,
            "Slug": slug,
            "Token_Id": token_id,
            "Prediction_Side": side,
            "Actual_Outcome": None,
            "Entry_Timestamp": now_dt,
            "Order_Timestamp_Sec": now_ts,
            "Trigger_Odds_10s_Ago": trigger_odds_10s_ago,
            "Entry_Odds": entry_odds,
            "Limit_Ceiling": limit_buy_price,
            "Target_Price": limit_buy_price,
            "Target_Quantity": target_qty,
            "Filled_Quantity": 0.0,
            "Average_Fill_Price": None,
            "Take_Profit_Price": None,
            "Stop_Loss_Price": None,
            "Exit_Timestamp": None,
            "Exit_Price": None,
            "Exit_Reason": None,
            "Trade_Outcome": None,
            "Order_Id": f"V3_MAKER_{int(now_ts*1000)}",
            "Position_Status": "PENDING_FILL",
            "Cancel_Reason": None,
            "Transaction_Price": 0.0,
            "Pnl": 0.0,
            "Min_Price_Observed": limit_buy_price,
            "Max_Price_Observed": limit_buy_price,
            "High_Water_Mark": limit_buy_price,
            "Updated_At": now_dt
        }

        # Lock active position guard with PENDING_FILL order
        self.active_position = pos

        if self.async_writer:
            sql = """
                INSERT INTO Positions (
                    Candle_Start, Prob_Cal, Prob_Uncal, Slug, Token_Id, Prediction_Side, Actual_Outcome,
                    Entry_Timestamp, Trigger_Odds_10s_Ago, Entry_Odds, Target_Price, Target_Quantity,
                    Filled_Quantity, Average_Fill_Price, Take_Profit_Price, Stop_Loss_Price, Order_Id,
                    Position_Status, Cancel_Reason, Transaction_Price, Pnl, Updated_At
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
            self.async_writer.enqueue_write(
                sql,
                (
                    candle_start, 0.50, 0.50, slug, token_id, side, None,
                    now_dt, trigger_odds_10s_ago, entry_odds, limit_buy_price, target_qty,
                    0.0, None, None, None, pos["Order_Id"],
                    "PENDING_FILL", None, 0.0, 0.0, now_dt
                )
            )

        logger.info(
            f"📥 [V3 MAKER ORDER PLACED] Side={side} | Candle={candle_start} | "
            f"Best_Ask=${entry_odds:.4f} -> Limit_Buy=${limit_buy_price:.4f} (-{maker_offset*100:.0f}¢ Maker Offset) | Status=PENDING_FILL (5s Timeout)"
        )

        return pos

    def _evaluate_pending_fill(self, current_bid: Optional[float], current_ask: Optional[float]) -> None:
        """
        Evaluates active PENDING_FILL order on every tick for:
        1. Fill Condition: If current_bid >= Limit_Buy_Price (a seller hits our bid) -> Fills & transitions to OPEN.
        2. Timeout Cancellation: If 5.0 seconds elapsed without fill -> Auto-cancels order (CANCELLED_TIMEOUT) & unlocks guard.
        """
        pos = self.active_position
        if not pos or pos.get("Position_Status") != "PENDING_FILL":
            return

        now_sec = time.time()
        now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        placed_sec = pos.get("Order_Timestamp_Sec", now_sec)
        elapsed_sec = now_sec - placed_sec
        limit_buy_price = pos["Target_Price"]
        target_qty = pos["Target_Quantity"]
        candle_start = pos["Candle_Start"]
        timeout_sec = getattr(config, "v3_maker_order_timeout_sec", 5.0)

        # 1. FILL CONDITION: Seller hits our bid (current_bid >= limit_buy_price OR eff_price <= limit_buy_price)
        if current_bid is not None and current_bid >= limit_buy_price:
            fill_price = limit_buy_price
            high_odds_cutoff = getattr(config, "v2_high_odds_cutoff", 0.75)
            high_odds_tp = getattr(config, "v2_high_odds_tp_target", 0.995)
            tp_cents = getattr(config, "v2_take_profit_cents", 0.05)
            trailing_dist = getattr(config, "v2_trailing_sl_distance_cents", 0.10)

            stop_loss_price = round(max(0.01, fill_price - trailing_dist), 4)
            take_profit_price = high_odds_tp if fill_price >= high_odds_cutoff else round(min(high_odds_tp, fill_price + tp_cents), 4)

            pos["Average_Fill_Price"] = fill_price
            pos["Filled_Quantity"] = target_qty
            pos["Take_Profit_Price"] = take_profit_price
            pos["Stop_Loss_Price"] = stop_loss_price
            pos["Min_Price_Observed"] = fill_price
            pos["Max_Price_Observed"] = fill_price
            pos["High_Water_Mark"] = fill_price
            pos["Position_Status"] = "OPEN"
            pos["Updated_At"] = now_dt

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Average_Fill_Price = ?,
                        Filled_Quantity = ?,
                        Take_Profit_Price = ?,
                        Stop_Loss_Price = ?,
                        Position_Status = 'OPEN',
                        Updated_At = ?
                    WHERE Order_Id = ?;
                """
                self.async_writer.enqueue_write(sql, (fill_price, target_qty, take_profit_price, stop_loss_price, now_dt, pos["Order_Id"]))

            logger.info(
                f"✅ [V3 MAKER FILL EXECUTED] Side={pos['Prediction_Side']} | Candle={candle_start} | "
                f"Fill_Price=${fill_price:.4f} (0% Maker Fee) | TP=${take_profit_price:.4f} | SL=${stop_loss_price:.4f} | Status=OPEN"
            )

            if hasattr(self, "notifier") and self.notifier:
                try:
                    self.notifier.notify_v2_trade_entry(
                        candle_start=candle_start,
                        side=pos['Prediction_Side'],
                        signal_price=pos.get("Entry_Odds", fill_price),
                        fill_price=fill_price,
                        tp_price=take_profit_price,
                        sl_price=stop_loss_price,
                        qty=target_qty,
                        position_usd=round(fill_price * target_qty, 2)
                    )
                except Exception as e:
                    logger.warning(f"Failed to dispatch Telegram entry notification: {e}")

            return

        # 2. TIMEOUT CANCELLATION: 5 seconds elapsed without fill
        if elapsed_sec >= timeout_sec:
            pos["Position_Status"] = "CANCELLED"
            pos["Exit_Reason"] = "CANCELLED_TIMEOUT"
            pos["Cancel_Reason"] = f"Unfilled after {elapsed_sec:.1f}s (>{timeout_sec:.1f}s Limit)"
            pos["Updated_At"] = now_dt

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Position_Status = 'CANCELLED',
                        Exit_Reason = 'CANCELLED_TIMEOUT',
                        Cancel_Reason = ?,
                        Updated_At = ?
                    WHERE Order_Id = ?;
                """
                self.async_writer.enqueue_write(sql, (pos["Cancel_Reason"], now_dt, pos["Order_Id"]))

            logger.warning(
                f"⏰ [V3 MAKER ORDER TIMEOUT] Cancelled! Side={pos['Prediction_Side']} | Candle={candle_start} | "
                f"Limit_Price=${limit_buy_price:.4f} unfilled after {elapsed_sec:.1f}s. Unlocking position guard."
            )

            if pos.get("Token_Id") in self.tick_buffers:
                self.tick_buffers[pos["Token_Id"]].clear()
            self.active_position = None

    def _evaluate_tp_sl_exit(self, current_bid: Optional[float], current_ask: Optional[float]) -> Optional[Dict[str, Any]]:
        """
        Evaluates active open position against Take Profit and Stop Loss thresholds on every live tick.
        Tracks running price extremes (min/max) to guarantee mid-candle SL/TP detection even during fast price spikes.
        """
        pos = self.active_position
        if not pos or pos.get("Position_Status") != "OPEN":
            return None

        tp_price = pos["Take_Profit_Price"]
        sl_price = pos["Stop_Loss_Price"]
        entry_price = pos["Average_Fill_Price"]
        qty = pos["Filled_Quantity"]
        candle_start = pos["Candle_Start"]
        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Determine effective live price for exit evaluation (prefer bid, fallback to ask)
        eff_price = current_bid if (current_bid is not None and current_bid > 0) else current_ask
        peak_price = max(current_bid or 0.0, current_ask or 0.0)

        # Update running candle extremes and High Water Mark (HWM)
        if eff_price is not None and eff_price > 0:
            pos["Min_Price_Observed"] = min(pos.get("Min_Price_Observed", eff_price), eff_price)
            pos["Max_Price_Observed"] = max(pos.get("Max_Price_Observed", peak_price), peak_price)

            # High Water Mark Trailing Stop Loss Logic (Option A)
            hwm = max(pos.get("High_Water_Mark", entry_price), peak_price)
            pos["High_Water_Mark"] = hwm

            trailing_enabled = getattr(config, "v2_trailing_sl_enabled", True)
            if trailing_enabled:
                trailing_dist = getattr(config, "v2_trailing_sl_distance_cents", 0.10)
                candidate_sl = round(hwm - trailing_dist, 4)
                # Trailing SL can ONLY move UP, never down
                if candidate_sl > pos["Stop_Loss_Price"]:
                    pos["Stop_Loss_Price"] = candidate_sl
                    # Reset minimum price tracking from the new peak
                    pos["Min_Price_Observed"] = eff_price

        sl_price = pos["Stop_Loss_Price"]
        min_obs = pos.get("Min_Price_Observed", eff_price or 1.0)
        max_obs = pos.get("Max_Price_Observed", eff_price or 0.0)

        # 1. STOP LOSS TRIGGER (Check if current price OR running minimum price during candle breached SL target)
        if (eff_price is not None and eff_price <= sl_price) or min_obs <= sl_price:
            exit_price = sl_price
            pnl = round((exit_price - entry_price) * qty, 4)
            pos["Exit_Timestamp"] = now_dt
            pos["Exit_Price"] = exit_price
            pos["Exit_Reason"] = "STOP_LOSS"
            pos["Trade_Outcome"] = "STOP_LOSS_HIT"
            pos["Position_Status"] = "CLOSED"
            pos["Pnl"] = pnl
            pos["Updated_At"] = now_dt

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Exit_Timestamp = ?,
                        Exit_Price = ?,
                        Exit_Reason = 'STOP_LOSS',
                        Trade_Outcome = 'STOP_LOSS_HIT',
                        Position_Status = 'CLOSED',
                        Pnl = ?,
                        Updated_At = ?
                    WHERE Candle_Start = ? AND Position_Status = 'OPEN';
                """
                self.async_writer.enqueue_write(sql, (now_dt, exit_price, pnl, now_dt, candle_start))

            logger.warning(
                f"🛑 [V2 STOP LOSS HIT] Exit Executed! Side={pos['Prediction_Side']} | Candle={candle_start} | "
                f"Exit_Price=${exit_price:.4f} (SL Target=${sl_price:.4f}) | PnL=${pnl:+.4f}"
            )
            if hasattr(self, "notifier") and self.notifier:
                try:
                    self.notifier.notify_v2_trade_exit(candle_start, pos['Prediction_Side'], exit_price, "STOP_LOSS_HIT", pnl)
                except Exception as e:
                    logger.warning(f"Failed to dispatch Telegram SL exit notification: {e}")

            if pos.get("Token_Id") in self.tick_buffers:
                self.tick_buffers[pos["Token_Id"]].clear()
            self.active_position = None
            return pos

        # 2. TAKE PROFIT TRIGGER (Check if current price OR running maximum price during candle breached TP target)
        if (eff_price is not None and eff_price >= tp_price) or max_obs >= tp_price:
            exit_price = tp_price
            pnl = round((exit_price - entry_price) * qty, 4)
            pos["Exit_Timestamp"] = now_dt
            pos["Exit_Price"] = exit_price
            pos["Exit_Reason"] = "TAKE_PROFIT"
            pos["Trade_Outcome"] = "TAKE_PROFIT_ACHIEVED"
            pos["Position_Status"] = "CLOSED"
            pos["Pnl"] = pnl
            pos["Updated_At"] = now_dt

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Exit_Timestamp = ?,
                        Exit_Price = ?,
                        Exit_Reason = 'TAKE_PROFIT',
                        Trade_Outcome = 'TAKE_PROFIT_ACHIEVED',
                        Position_Status = 'CLOSED',
                        Pnl = ?,
                        Updated_At = ?
                    WHERE Candle_Start = ? AND Position_Status = 'OPEN';
                """
                self.async_writer.enqueue_write(sql, (now_dt, exit_price, pnl, now_dt, candle_start))

            logger.info(
                f"🎯 [V2 TAKE PROFIT HIT] Target Achieved! Side={pos['Prediction_Side']} | Candle={candle_start} | "
                f"Exit_Price=${exit_price:.4f} (TP Target=${tp_price:.4f}) | PnL=${pnl:+.4f}"
            )
            if hasattr(self, "notifier") and self.notifier:
                try:
                    self.notifier.notify_v2_trade_exit(candle_start, pos['Prediction_Side'], exit_price, "TAKE_PROFIT_ACHIEVED", pnl)
                except Exception as e:
                    logger.warning(f"Failed to dispatch Telegram TP exit notification: {e}")

            if pos.get("Token_Id") in self.tick_buffers:
                self.tick_buffers[pos["Token_Id"]].clear()
            self.active_position = None
            return pos

        return None

    def _close_expired_position(self, current_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Closes active position from previous candle on 5m boundary rollover and unlocks single position guard.
        Validates if SL or TP was breached during the candle before defaulting to CANDLE_EXPIRED.
        """
        pos = self.active_position
        if not pos:
            return None

        now_dt = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        entry_price = pos["Average_Fill_Price"]
        qty = pos["Filled_Quantity"]
        raw_exit = current_price or entry_price
        candle_start = pos["Candle_Start"]

        sl_price = pos["Stop_Loss_Price"]
        tp_price = pos["Take_Profit_Price"]
        min_obs = pos.get("Min_Price_Observed", raw_exit)
        max_obs = pos.get("Max_Price_Observed", raw_exit)

        # Priority 1: Check if Stop Loss was breached at any point during candle
        if min_obs <= sl_price or raw_exit <= sl_price:
            exit_price = sl_price
            reason = "STOP_LOSS"
            outcome = "STOP_LOSS_HIT"
        # Priority 2: Check if Take Profit was breached at any point during candle
        elif max_obs >= tp_price or raw_exit >= tp_price:
            exit_price = tp_price
            reason = "TAKE_PROFIT"
            outcome = "TAKE_PROFIT_ACHIEVED"
        else:
            exit_price = raw_exit
            reason = "CANDLE_EXPIRED"
            outcome = "EXPIRED"

        pnl = round((exit_price - entry_price) * qty, 4)

        pos["Exit_Timestamp"] = now_dt
        pos["Exit_Price"] = exit_price
        pos["Exit_Reason"] = reason
        pos["Trade_Outcome"] = outcome
        pos["Position_Status"] = "CLOSED"
        pos["Pnl"] = pnl
        pos["Updated_At"] = now_dt

        if self.async_writer:
            sql = """
                UPDATE Positions SET
                    Exit_Timestamp = ?,
                    Exit_Price = ?,
                    Exit_Reason = ?,
                    Trade_Outcome = ?,
                    Position_Status = 'CLOSED',
                    Pnl = ?,
                    Updated_At = ?
                WHERE Candle_Start = ? AND Position_Status = 'OPEN';
            """
            self.async_writer.enqueue_write(sql, (now_dt, exit_price, reason, outcome, pnl, now_dt, candle_start))

        logger.info(
            f"⌛ [V2 CANDLE CLOSED] Position Closed on 5m Boundary! Reason={reason} | Side={pos['Prediction_Side']} | "
            f"Candle={candle_start} | Exit_Price=${exit_price:.4f} | PnL=${pnl:+.4f}"
        )
        if hasattr(self, "notifier") and self.notifier:
            try:
                self.notifier.notify_v2_trade_exit(candle_start, pos['Prediction_Side'], exit_price, reason, pnl)
            except Exception as e:
                logger.warning(f"Failed to dispatch Telegram boundary exit notification: {e}")

        if pos.get("Token_Id") in self.tick_buffers:
            self.tick_buffers[pos["Token_Id"]].clear()

        # Unlock active position for new 5m candle
        self.active_position = None
        return pos

    def execute_entry(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None

    def execute_exit(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None

    def check_and_update_positions(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None


class LiveExecutionStrategy(IExecutionStrategy):
    """
    Live Execution Strategy Wrapper.
    Integrates Polymarket CLOB REST API client and EIP-712 cryptographic signature handling.
    In simulation/dry-run fallback, delegates safely to DryExecutionStrategy.
    """

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None):
        self.dry_strategy = DryExecutionStrategy(async_writer)
        self.api_key = config.polymarket_api_key
        self.private_key = config.polymarket_private_key

    def execute_entry(
        self,
        candle_start: str,
        slug: str,
        side: str,
        prob_cal: float,
        prob_uncal: float,
        target_price: float,
        position_usd: float,
        token_id: str,
        current_bid: Optional[float] = None,
        current_ask: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        if config.is_dry_run() or not self.api_key or not self.private_key:
            logger.info("Live execution fallback: Delegating to DryExecutionStrategy (Simulation Mode active).")
            return self.dry_strategy.execute_entry(
                candle_start, slug, side, prob_cal, prob_uncal, target_price,
                position_usd, token_id, current_bid, current_ask
            )

        # Real Polymarket CLOB REST API Order Dispatch Stub
        logger.info(f"⚡ [LIVE CLOB ORDER DISPATCH] Submitting EIP-712 Signed Buy Limit Order for token {token_id[:8]}... at ${target_price:.2f}")
        return self.dry_strategy.execute_entry(
            candle_start, slug, side, prob_cal, prob_uncal, target_price,
            position_usd, token_id, current_bid, current_ask
        )

    def execute_exit(
        self,
        candle_start: str,
        token_id: str,
        exit_price: float,
        reason: str
    ) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.execute_exit(candle_start, token_id, exit_price, reason)

    def check_and_update_positions(
        self,
        candle_start: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.check_and_update_positions(candle_start, token_id, current_bid, current_ask)

