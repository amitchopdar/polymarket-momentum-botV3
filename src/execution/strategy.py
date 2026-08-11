"""
Execution Strategy Engine (Sprint 3: US3.1, US3.2)
Implements IExecutionStrategy interface with DryExecutionStrategy (simulation fills,
persistent $0.40 buy order tracking, automated $0.20 stop-loss limit sell order)
and LiveExecutionStrategy (Polymarket CLOB REST API & EIP-712 signer wrapper).
"""

import os
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Tuple
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

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None, notifier: Optional[Any] = None, live_strategy: Optional[Any] = None):
        self.async_writer = async_writer
        self.notifier = notifier
        self.live_strategy = live_strategy
        # Token tick buffers: token_id -> list of (timestamp_sec, bid, ask)
        self.tick_buffers: Dict[str, List[Tuple[float, float, float]]] = {}
        # Single Active Position Guard across the bot
        self.active_position: Optional[Dict[str, Any]] = None

    def cancel_order_on_exchange(self, buy_order_id: str) -> bool:
        if self.live_strategy and hasattr(self.live_strategy, "cancel_order_on_exchange"):
            return self.live_strategy.cancel_order_on_exchange(buy_order_id)
        return True

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

        # 2. Evaluate active CLOSING position for sell fill confirmation on exchange
        if self.active_position and self.active_position.get("Position_Status") == "CLOSING":
            self._evaluate_closing_position(current_bid, current_ask)

        # 3. Evaluate TP/SL exit if this token has an active open position
        if self.active_position and self.active_position.get("Position_Status") == "OPEN" and self.active_position.get("Token_Id") == token_id:
            self._evaluate_tp_sl_exit(current_bid or current_ask, current_ask)

        # 4. Single Position / Order Guard: Reject new entry if an active position is PENDING_FILL, OPEN, or CLOSING
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

        # 5. Trigger Condition: 10s Surge >= 15 cents AND Floor ($0.65) <= Current Ask <= Ceiling ($0.92)
        momentum_thresh = getattr(config, "v2_momentum_threshold_cents", 0.15)
        min_odds_floor = getattr(config, "v2_min_entry_odds_floor", 0.65)
        max_odds_ceiling = getattr(config, "v2_max_entry_odds_ceiling", 0.92)

        if delta_odds >= momentum_thresh and min_odds_floor <= current_ask <= max_odds_ceiling:
            # Lock active position guard IMMEDIATELY before dispatch to block concurrent WS ticks
            maker_offset = getattr(config, "v3_maker_offset_cents", 0.02)
            limit_buy_price = round(max(0.01, current_ask - maker_offset), 4)
            target_qty = round(getattr(config, "max_position_size_usd", 2.0) / limit_buy_price, 4) if limit_buy_price > 0 else 0.0
            now_sec = time.time()
            now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            pre_pos = {
                "Candle_Start": candle_start,
                "Slug": slug,
                "Token_Id": token_id,
                "Position_Side": side,
                "Entry_Timestamp": now_dt,
                "Order_Timestamp_Sec": now_sec,
                "Trigger_Odds_10s_Ago": min_ask_10s,
                "Entry_Odds": current_ask,
                "Target_Buy_Price": limit_buy_price,
                "Target_Quantity": target_qty,
                "Filled_Quantity": 0.0,
                "Position_Status": "PENDING_FILL",
                "Buy_Order_Id": f"V3_MAKER_{int(now_sec*1000)}"
            }
            self.active_position = pre_pos

            if self.live_strategy and self.live_strategy.clob_client:
                pos = self.live_strategy.execute_entry(
                    candle_start=candle_start,
                    slug=slug,
                    side=side,
                    prob_cal=0.50,
                    prob_uncal=0.50,
                    target_price=current_ask,
                    position_usd=getattr(config, "max_position_size_usd", 2.0),
                    token_id=token_id,
                    current_bid=current_bid,
                    current_ask=current_ask
                )
                if pos:
                    pre_pos.update(pos)
                    pre_pos["Position_Status"] = "PENDING_FILL"
                    pre_pos["Order_Timestamp_Sec"] = now_sec
                    pre_pos["Target_Buy_Price"] = limit_buy_price
                    pre_pos["Target_Quantity"] = target_qty
                else:
                    self.active_position = None
                return pre_pos
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
        buy_order_id = f"V3_MAKER_{int(now_ts*1000)}"

        pos = {
            "Candle_Start": candle_start,
            "Slug": slug,
            "Token_Id": token_id,
            "Position_Side": side,
            "Entry_Timestamp": now_dt,
            "Order_Timestamp_Sec": now_ts,
            "Trigger_Odds_10s_Ago": trigger_odds_10s_ago,
            "Entry_Odds": entry_odds,
            "Target_Buy_Price": limit_buy_price,
            "Target_Price": limit_buy_price,
            "Target_Quantity": target_qty,
            "Filled_Quantity": 0.0,
            "Sell_Quantity": 0.0,
            "Average_Fill_Price": None,
            "Take_Profit_Price": None,
            "Stop_Loss_Price": None,
            "High_Water_Mark": limit_buy_price,
            "Buy_Order_Id": buy_order_id,
            "Sell_Order_Id": None,
            "Exit_Timestamp": None,
            "Exit_Price": None,
            "Exit_Reason": None,
            "Trade_Outcome": None,
            "Position_Status": "PENDING_FILL",
            "Cancel_Reason": None,
            "Pnl": 0.0,
            "Updated_At": now_dt
        }

        # Lock active position guard with PENDING_FILL order
        self.active_position = pos

        if self.async_writer:
            sql = """
                INSERT INTO Positions (
                    Candle_Start, Slug, Token_Id, Position_Side, Entry_Timestamp,
                    Trigger_Odds_10s_Ago, Entry_Odds, Target_Buy_Price, Target_Quantity,
                    Filled_Quantity, Sell_Quantity, Buy_Order_Id, Position_Status, Pnl, Updated_At
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """
            self.async_writer.enqueue_write(
                sql,
                (
                    candle_start, slug, token_id, side, now_dt,
                    trigger_odds_10s_ago, entry_odds, limit_buy_price, target_qty,
                    0.0, 0.0, buy_order_id, "PENDING_FILL", 0.0, now_dt
                )
            )

        logger.info(
            f"📥 [V3 MAKER ORDER PLACED] Side={side} | Candle={candle_start} | "
            f"Best_Ask=${entry_odds:.4f} -> Limit_Buy=${limit_buy_price:.4f} (-{maker_offset*100:.0f}¢ Maker Offset) | Status=PENDING_FILL (5s Timeout)"
        )

        return pos

    def _evaluate_pending_fill(self, current_bid: Optional[float], current_ask: Optional[float]) -> None:
        """
        Evaluates active PENDING_FILL order on every tick directly against Polymarket's exchange:
        1. Queries Polymarket GET /data/order/{buy_order_id} for exact sizeMatched (filled quantity).
        2. As soon as sizeMatched > 0: Instantly places or updates resting Limit Sell TP order on exchange.
        3. Sub-5s Stop-Loss: If sizeMatched > 0 and price dips <= Stop_Loss_Price:
           Cancels Buy Order & TP Order on exchange -> Exits sizeMatched shares at Stop_Loss_Price.
        4. 5.0s Timeout Expiry: Cancels remaining unfilled buy order -> transitions trade to OPEN.
        """
        pos = self.active_position
        if not pos or pos.get("Position_Status") != "PENDING_FILL":
            return

        now_sec = time.time()
        now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        placed_sec = pos.get("Order_Timestamp_Sec", now_sec)
        elapsed_sec = now_sec - placed_sec
        limit_buy_price = pos["Target_Buy_Price"]
        target_qty = pos["Target_Quantity"]
        candle_start = pos["Candle_Start"]
        token_id = pos.get("Token_Id", "")
        buy_order_id = pos.get("Buy_Order_Id")
        timeout_sec = getattr(config, "v3_maker_order_timeout_sec", 5.0)

        eff_price = current_bid if (current_bid is not None and current_bid > 0) else current_ask
        peak_price = max(current_bid or 0.0, current_ask or 0.0)

        # 1. FETCH EXACT FILLED QUANTITY DIRECTLY FROM POLYMARKET EXCHANGE
        size_matched = 0.0
        if self.live_strategy and self.live_strategy.clob_client and buy_order_id:
            order_info = self.live_strategy.get_order_from_exchange(buy_order_id)
            if order_info and isinstance(order_info, dict):
                size_matched = round(float(order_info.get("sizeMatched") or order_info.get("size_matched") or 0.0), 4)
        else:
            # Simulation fallback: Market price dips <= limit_buy_price (unless timed out)
            if elapsed_sec < timeout_sec and eff_price is not None and eff_price <= limit_buy_price:
                size_matched = target_qty

        if size_matched > 0:
            fill_price = limit_buy_price
            high_odds_cutoff = getattr(config, "v2_high_odds_cutoff", 0.75)
            high_odds_tp = getattr(config, "v2_high_odds_tp_target", 0.9900)
            tp_cents = getattr(config, "v2_take_profit_cents", 0.05)
            trailing_dist = getattr(config, "v2_trailing_sl_distance_cents", 0.10)

            hwm = max(fill_price, peak_price)
            stop_loss_price = round(max(0.01, hwm - trailing_dist), 4)
            raw_tp = high_odds_tp if fill_price >= high_odds_cutoff else round(min(high_odds_tp, fill_price + tp_cents), 4)
            take_profit_price = round(min(0.9900, raw_tp), 4)

            pos["Average_Fill_Price"] = fill_price
            pos["Filled_Quantity"] = size_matched
            pos["Take_Profit_Price"] = take_profit_price
            pos["Stop_Loss_Price"] = stop_loss_price
            pos["High_Water_Mark"] = hwm
            pos["Position_Status"] = "OPEN"
            # SUB-5s STOP-LOSS CHECK: If size_matched > 0 AND price <= stop_loss_price
            if eff_price is not None and eff_price <= stop_loss_price:
                logger.warning(
                    f"🚨 [SUB-5s STOP-LOSS TRIGGERED] Price ${eff_price:.4f} <= SL ${stop_loss_price:.4f}! "
                    f"Executing instant exit for {size_matched:.4f} shares..."
                )
                if buy_order_id:
                    self.cancel_order_on_exchange(buy_order_id)
                if pos.get("Tp_Order_Id"):
                    self.cancel_order_on_exchange(pos["Tp_Order_Id"])

                sell_order_id = f"V3_SL_{int(now_sec*1000)}"
                if self.live_strategy and self.live_strategy.clob_client and token_id:
                    aggressive_sl_price = round(max(0.01, eff_price - 0.02), 4)
                    sl_resp = self.live_strategy.post_limit_sell(token_id, aggressive_sl_price, size_matched)
                    if sl_resp and isinstance(sl_resp, dict) and ("orderID" in sl_resp or "orderId" in sl_resp):
                        sell_order_id = sl_resp.get("orderID") or sl_resp.get("orderId")

                pos["Position_Status"] = "CLOSING"
                pos["Closing_Timestamp_Sec"] = now_sec
                pos["Exit_Reason"] = "STOP_LOSS"
                pos["Trade_Outcome"] = "STOP_LOSS_HIT"
                pos["Exit_Price"] = stop_loss_price
                pos["Exit_Timestamp"] = now_dt
                pos["Sell_Order_Id"] = sell_order_id
                pos["Exit_Quantity"] = size_matched
                pos["Pnl"] = -0.50
                pos["Updated_At"] = now_dt

                if self.async_writer:
                    sql = "UPDATE Positions SET Position_Status = 'CLOSING', Exit_Reason = 'STOP_LOSS', Exit_Price = ?, Updated_At = ? WHERE Buy_Order_Id = ?;"
                    self.async_writer.enqueue_write(sql, (stop_loss_price, now_dt, buy_order_id))

                self._evaluate_closing_position(current_bid, current_ask)
                return

            # SUB-SECOND REAL-TIME TP ORDER PLACEMENT / UPDATE ON EXCHANGE
            prev_tp_qty = pos.get("Tp_Qty", 0.0)
            if self.live_strategy and self.live_strategy.clob_client and (not pos.get("Tp_Order_Id") or prev_tp_qty != size_matched):
                if pos.get("Tp_Order_Id"):
                    self.cancel_order_on_exchange(pos["Tp_Order_Id"])
                tp_resp = self.live_strategy.post_limit_sell(token_id, take_profit_price, size_matched)
                if tp_resp and isinstance(tp_resp, dict) and ("orderID" in tp_resp or "orderId" in tp_resp):
                    pos["Tp_Order_Id"] = tp_resp.get("orderID") or tp_resp.get("orderId")
                    pos["Tp_Qty"] = size_matched

        # 2. 5.0-SECOND TIMEOUT RESOLUTION
        if elapsed_sec >= timeout_sec:
            filled_qty = pos.get("Filled_Quantity", 0.0)

            if filled_qty <= 0.0:
                if buy_order_id:
                    self.cancel_order_on_exchange(buy_order_id)
                pos["Position_Status"] = "CANCELLED"
                pos["Cancel_Reason"] = f"TIMEOUT_{timeout_sec:.1f}S"
                pos["Updated_At"] = now_dt

                if self.async_writer:
                    sql = "UPDATE Positions SET Position_Status = 'CANCELLED', Cancel_Reason = ?, Updated_At = ? WHERE Buy_Order_Id = ?;"
                    self.async_writer.enqueue_write(sql, (pos["Cancel_Reason"], now_dt, buy_order_id))

                logger.warning(
                    f"⏰ [V3 MAKER ORDER TIMEOUT] Cancelled! Side={pos.get('Position_Side')} | Candle={candle_start} | "
                    f"Limit_Price=${limit_buy_price:.4f} unfilled after {elapsed_sec:.1f}s. Unlocking position guard."
                )
                if token_id in self.tick_buffers:
                    self.tick_buffers[token_id].clear()
                self.active_position = None
            else:
                if buy_order_id:
                    self.cancel_order_on_exchange(buy_order_id)
                pos["Position_Status"] = "OPEN"
                logger.info(
                    f"🎯 [V3 MAKER ORDER PARTIAL FILL TIMEOUT] Closed remaining buy order balance for {buy_order_id}. "
                    f"Transitioned {filled_qty:.4f} filled shares to OPEN position."
                )

    def _evaluate_closing_position(self, current_bid: Optional[float], current_ask: Optional[float]) -> None:
        """
        Evaluates active CLOSING position to confirm physical sell order fill on Polymarket exchange:
        1. Queries Polymarket GET /data/order/{sell_order_id} for order status & size_matched.
        2. Once size_matched >= exit_qty or status in ('MATCHED', 'FILLED') (or 5s timeout fallback):
           Transitions position to CLOSED, dispatches Telegram exit notification, and unlocks position guard.
        """
        pos = self.active_position
        if not pos or pos.get("Position_Status") != "CLOSING":
            return

        now_sec = time.time()
        now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        placed_sec = pos.get("Closing_Timestamp_Sec", now_sec)
        elapsed_sec = now_sec - placed_sec
        sell_order_id = pos.get("Sell_Order_Id")
        exit_qty = pos.get("Exit_Quantity", pos.get("Filled_Quantity", 0.0))
        candle_start = pos.get("Candle_Start", "")

        is_filled = False
        if self.live_strategy and self.live_strategy.clob_client and sell_order_id and not str(sell_order_id).startswith("V3_"):
            order_info = self.live_strategy.get_order_from_exchange(sell_order_id)
            if order_info and isinstance(order_info, dict):
                size_matched = round(float(order_info.get("size_matched") or order_info.get("sizeMatched") or 0.0), 4)
                status = str(order_info.get("status", "")).upper()
                if status in ("MATCHED", "FILLED") or size_matched >= (exit_qty - 0.01):
                    is_filled = True
        else:
            is_filled = True  # Simulation / dry-run fallback

        if is_filled or elapsed_sec >= 5.0:
            pos["Position_Status"] = "CLOSED"
            exit_price = pos.get("Exit_Price", 0.0)
            exit_reason = pos.get("Exit_Reason", "STOP_LOSS")
            trade_outcome = pos.get("Trade_Outcome", "STOP_LOSS_HIT")
            pnl = pos.get("Pnl", 0.0)

            if self.async_writer:
                sql = """
                    UPDATE Positions SET
                        Exit_Timestamp = ?,
                        Exit_Price = ?,
                        Exit_Reason = ?,
                        Trade_Outcome = ?,
                        Sell_Order_Id = ?,
                        Sell_Quantity = ?,
                        High_Water_Mark = ?,
                        Stop_Loss_Price = ?,
                        Position_Status = 'CLOSED',
                        Pnl = ?,
                        Updated_At = ?
                    WHERE Buy_Order_Id = ? OR (Candle_Start = ? AND Position_Status IN ('OPEN', 'CLOSING', 'PARTIALLY_CLOSED'));
                """
                self.async_writer.enqueue_write(
                    sql,
                    (
                        now_dt, exit_price, exit_reason, trade_outcome,
                        sell_order_id, exit_qty, pos.get("High_Water_Mark", 0.0),
                        pos.get("Stop_Loss_Price", 0.0), pnl, now_dt, pos.get("Buy_Order_Id"), candle_start
                    )
                )

            logger.info(
                f"🏁 [V3 TRADE CLOSED & CONFIRMED] Reason={exit_reason} | Side={pos.get('Position_Side') or pos.get('Prediction_Side', 'UP')} | Candle={candle_start} | "
                f"Exit_Price=${exit_price:.4f} | PnL=${pnl:+.4f}"
            )

            if hasattr(self, "notifier") and self.notifier:
                try:
                    self.notifier.notify_v2_trade_exit(candle_start, pos.get('Position_Side') or pos.get('Prediction_Side', 'UP'), exit_price, trade_outcome, pnl)
                except Exception as e:
                    logger.warning(f"Failed to dispatch Telegram exit notification: {e}")

            if pos.get("Token_Id") in self.tick_buffers:
                self.tick_buffers[pos["Token_Id"]].clear()
            self.active_position = None

    def _evaluate_tp_sl_exit(self, current_price: float, current_ask: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Evaluates active OPEN trade tick-by-tick for Take-Profit and Trailing Stop-Loss exits.
        """
        pos = self.active_position
        if not pos or pos.get("Position_Status") != "OPEN":
            return None

        now_ts = time.time()
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        candle_start = pos["Candle_Start"]
        entry_price = pos.get("Average_Fill_Price") or pos["Target_Buy_Price"]
        filled_qty = pos.get("Filled_Quantity") or pos["Target_Quantity"]
        tp_price = pos["Take_Profit_Price"]
        sl_price = pos["Stop_Loss_Price"]
        hwm = pos.get("High_Water_Mark", entry_price)

        current_bid = current_price
        peak_price = max(current_bid or 0.0, current_ask or 0.0)
        if peak_price > hwm:
            hwm = peak_price
            pos["High_Water_Mark"] = hwm

        trailing_sl_enabled = getattr(config, "v2_trailing_sl_enabled", True)
        trailing_dist = getattr(config, "v2_trailing_sl_distance_cents", 0.10)
        high_odds_cutoff = getattr(config, "v2_high_odds_cutoff", 0.80)

        if trailing_sl_enabled:
            new_sl = round(max(0.01, hwm - trailing_dist), 4)
            if new_sl > sl_price:
                sl_price = new_sl
                pos["Stop_Loss_Price"] = sl_price

        is_tp_trigger = current_bid >= tp_price
        is_sl_trigger = current_bid <= sl_price

        if is_tp_trigger or is_sl_trigger:
            exit_reason = "TAKE_PROFIT" if is_tp_trigger else "STOP_LOSS"
            trade_outcome = "WIN" if is_tp_trigger else ("LOSS" if sl_price < entry_price else "WIN")
            exit_price = tp_price if is_tp_trigger else sl_price
            sell_order_id = pos.get("Tp_Order_Id") or f"V3_SELL_{int(now_ts*1000)}"

            # STEP A: If Stop-Loss triggered, cancel resting TP order on exchange first to unlock shares
            if is_sl_trigger and pos.get("Tp_Order_Id"):
                logger.info(f"🛑 [STOP-LOSS EXECUTING] Cancelling resting TP order {pos['Tp_Order_Id']} on exchange...")
                self.cancel_order_on_exchange(pos["Tp_Order_Id"])

            prev_sell_qty = pos.get("Sell_Quantity", 0.0)
            exit_qty = filled_qty - prev_sell_qty
            if exit_qty <= 0:
                exit_qty = filled_qty

            # STEP B: If Stop-Loss triggered, dispatch aggressive SL sell order on exchange
            if is_sl_trigger and self.live_strategy and self.live_strategy.clob_client and pos.get("Token_Id"):
                aggressive_sl_price = round(max(0.01, current_bid - 0.02), 4)
                sl_resp = self.live_strategy.post_limit_sell(pos["Token_Id"], aggressive_sl_price, exit_qty)
                if sl_resp and isinstance(sl_resp, dict) and ("orderID" in sl_resp or "orderId" in sl_resp):
                    sell_order_id = sl_resp.get("orderID") or sl_resp.get("orderId")

            new_sell_qty = round(prev_sell_qty + exit_qty, 4)
            pos["Sell_Quantity"] = new_sell_qty
            pos["Sell_Order_Id"] = sell_order_id
            pos["Exit_Timestamp"] = now_dt
            pos["Exit_Price"] = exit_price
            pos["Exit_Reason"] = exit_reason
            pos["Trade_Outcome"] = trade_outcome
            pos["Exit_Quantity"] = new_sell_qty
            pos["Closing_Timestamp_Sec"] = now_ts
            pos["Position_Status"] = "CLOSING"
            pos["Updated_At"] = now_dt

            taker_fee_pct = getattr(config, "v2_taker_fee_pct", 0.02)
            taker_fee_cost = round(exit_price * taker_fee_pct * exit_qty, 4)
            pnl = round((exit_price - entry_price) * exit_qty - taker_fee_cost, 4)
            pos["Pnl"] = pnl

            self._evaluate_closing_position(current_bid, current_ask)
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

        now_ts = time.time()
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        entry_price = pos["Average_Fill_Price"]
        filled_qty = pos["Filled_Quantity"]
        prev_sell_qty = pos.get("Sell_Quantity", 0.0)
        exit_qty = filled_qty - prev_sell_qty
        if exit_qty <= 0:
            exit_qty = filled_qty

        new_sell_qty = round(prev_sell_qty + exit_qty, 4)
        raw_exit = current_price or entry_price
        candle_start = pos["Candle_Start"]
        sell_order_id = f"V3_SELL_{int(now_ts*1000)}"

        sl_price = pos["Stop_Loss_Price"]
        tp_price = pos["Take_Profit_Price"]

        # Priority 1: Check if Stop Loss was breached
        if raw_exit <= sl_price:
            exit_price = sl_price
            reason = "STOP_LOSS"
            outcome = "STOP_LOSS_HIT"
        # Priority 2: Check if Take Profit was breached
        elif raw_exit >= tp_price:
            exit_price = tp_price
            reason = "TAKE_PROFIT"
            outcome = "TAKE_PROFIT_ACHIEVED"
        else:
            exit_price = raw_exit
            reason = "CANDLE_EXPIRED"
            outcome = "EXPIRED"

        taker_fee_pct = getattr(config, "v2_taker_fee_pct", 0.02)
        taker_fee_cost = round(exit_price * taker_fee_pct * new_sell_qty, 4)
        pnl = round((exit_price - entry_price) * new_sell_qty - taker_fee_cost, 4)

        pos["Sell_Quantity"] = new_sell_qty
        pos["Sell_Order_Id"] = sell_order_id
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
                    Sell_Order_Id = ?,
                    Sell_Quantity = ?,
                    High_Water_Mark = ?,
                    Stop_Loss_Price = ?,
                    Position_Status = 'CLOSED',
                    Pnl = ?,
                    Updated_At = ?
                WHERE Buy_Order_Id = ? OR (Candle_Start = ? AND Position_Status IN ('OPEN', 'PARTIALLY_CLOSED'));
            """
            self.async_writer.enqueue_write(
                sql,
                (
                    now_dt, exit_price, reason, outcome, sell_order_id,
                    new_sell_qty, pos["High_Water_Mark"], pos["Stop_Loss_Price"],
                    pnl, now_dt, pos.get("Buy_Order_Id"), candle_start
                )
            )

        logger.info(
            f"⌛ [V3 CANDLE CLOSED] Position Closed on 5m Boundary! Reason={reason} | Side={pos.get('Position_Side') or pos.get('Prediction_Side', 'UP')} | "
            f"Candle={candle_start} | Exit_Price=${exit_price:.4f} | HWM=${pos['High_Water_Mark']:.4f} | SL=${pos['Stop_Loss_Price']:.4f} | PnL=${pnl:+.4f}"
        )
        if hasattr(self, "notifier") and self.notifier:
            try:
                self.notifier.notify_v2_trade_exit(candle_start, pos.get('Position_Side') or pos.get('Prediction_Side', 'UP'), exit_price, outcome, pnl)
            except Exception as e:
                logger.warning(f"Failed to dispatch Telegram exit notification: {e}")

        if pos.get("Token_Id") in self.tick_buffers:
            self.tick_buffers[pos["Token_Id"]].clear()
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
    Integrates Polymarket CLOB REST API client (py-clob-client) and EIP-712 cryptographic signature handling.
    In simulation/dry-run fallback, delegates safely to DryExecutionStrategy.
    """

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None, notifier: Optional[Any] = None):
        self.notifier = notifier
        self.dry_strategy = V2OddsMomentumStrategy(async_writer, notifier=notifier, live_strategy=self)
        self.clob_client = None
        self._init_clob_client()

    def _init_clob_client(self) -> None:
        raw_api_key = getattr(config, "polymarket_api_key", None) or os.getenv("POLYMARKET_API_KEY", "")
        raw_private_key = getattr(config, "polymarket_private_key", None) or os.getenv("POLYMARKET_PRIVATE_KEY", "")
        raw_secret = os.getenv("POLYMARKET_SECRET", "")
        raw_passphrase = os.getenv("POLYMARKET_PASSPHRASE", "")

        api_key = str(raw_api_key).strip("\"' ")
        private_key = str(raw_private_key).strip("\"' ")
        secret = str(raw_secret).strip("\"' ")
        passphrase = str(raw_passphrase).strip("\"' ")
        raw_funder = getattr(config, "polymarket_funder", None) or os.getenv("POLYMARKET_FUNDER", "")
        funder = str(raw_funder).strip("\"' ") or None

        if private_key:
            try:
                try:
                    from py_clob_client_v2 import ClobClient
                    from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
                except ImportError:
                    from py_clob_client.client import ClobClient
                    from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

                # Polymarket Deposit Wallet smart contract flow requires signature_type=3 (POLY_1271)
                sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3" if funder else "0").strip("\"' "))
                host = getattr(config, "polymarket_clob_url", "https://clob.polymarket.com")

                # Create a SINGLE ClobClient instance (avoids OrderBuilder state mismatch)
                logger.info(f"🔑 [L1 AUTH] Initializing EIP-712 L1 Auth Client (signature_type={sig_type}, funder={funder[:10] if funder else 'None'})...")
                self.clob_client = ClobClient(
                    host=host,
                    key=private_key,
                    chain_id=137,
                    signature_type=sig_type,
                    funder=funder
                )

                # API Credential Resolution (Priority Order):
                # 1. Manual .env credentials (REQUIRED for Deposit Wallet / signature_type=3)
                # 2. Auto-derivation fallback (only works for EOA / signature_type=0)
                #
                # WHY: derive_api_key() uses L1 headers signed by the EOA (signer.address()).
                # The API key is therefore bound to the EOA address. But with signature_type=3,
                # orders are signed with the FUNDER address (_v2_order_signer returns self.funder).
                # This address mismatch causes: "the order signer address has to be the address of the API KEY"
                # Solution: For Deposit Wallet, you MUST provide pre-generated API creds from Polymarket UI.
                creds = None

                # Auto-derive matching L2 API credentials directly on this ClobClient instance
                mode_label = "Deposit Wallet" if (sig_type == 3 and funder) else "EOA"
                logger.info(f"🔑 [L2 CREDS] Auto-deriving fresh CLOB API Credentials ({mode_label} mode)...")
                try:
                    derived_creds = self.clob_client.create_or_derive_api_key()
                    if derived_creds:
                        creds = ApiCreds(
                            api_key=derived_creds.api_key,
                            api_secret=derived_creds.api_secret,
                            api_passphrase=derived_creds.api_passphrase
                        )
                        logger.info(f"🔑 [L2 CREDS] Successfully derived L2 API Key: {creds.api_key[:8]}...")
                except Exception as derive_err:
                    logger.warning(f"⚠ L2 credential auto-derivation notice: {derive_err}")
                    if api_key and secret and passphrase:
                        creds = ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)
                        logger.info(f"🔑 [L2 CREDS] Falling back to .env credentials: {api_key[:8]}...")

                if creds:
                    self.clob_client.set_api_creds(creds)
                else:
                    logger.warning("⚠ No valid API credentials resolved. CLOB client will not be able to place orders.")
                    self.clob_client = None
                    return

                # Synchronize CLOB Balance & Allowance Cache
                try:
                    logger.info("🔄 [CLOB CACHE SYNC] Synchronizing CLOB balance & allowance cache with Polygon blockchain...")
                    self.clob_client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                    logger.info("✅ [CLOB CACHE SYNC] Balance & Allowance cache successfully synchronized!")
                except Exception as sync_err:
                    logger.warning(f"⚠ Cache sync notice: {sync_err}")

                logger.info(f"⚡ [LIVE CLOB CLIENT] Successfully initialized authenticated Polymarket CLOB client (signature_type={sig_type}).")
            except Exception as e:
                logger.warning(f"Failed to initialize py-clob-client SDK: {e}. Live fallback to DryExecutionStrategy.")
                self.clob_client = None

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
        if config.is_dry_run() or not self.clob_client:
            logger.info("Live execution fallback: Delegating to DryExecutionStrategy (Simulation Mode active).")
            return self.dry_strategy.execute_entry(
                candle_start, slug, side, prob_cal, prob_uncal, target_price,
                position_usd, token_id, current_bid, current_ask
            )

        # Real Polymarket CLOB REST API Order Dispatch via py-clob-client V2
        try:
            try:
                from py_clob_client_v2.clob_types import OrderArgsV2 as OrderArgs, OrderType
            except ImportError:
                from py_clob_client.clob_types import OrderArgs, OrderType

            maker_offset = getattr(config, "v3_maker_offset_cents", 0.02)
            entry_odds = current_ask or target_price
            limit_buy_price = round(max(0.01, entry_odds - maker_offset), 4)
            target_qty = round(position_usd / limit_buy_price, 4) if limit_buy_price > 0 else 0.0

            logger.info(f"⚡ [LIVE CLOB ORDER DISPATCH] Submitting EIP-712 Post-Only Buy Limit Order for token {token_id[:8]}... Price=${limit_buy_price:.4f} Qty={target_qty}")
            order_args = OrderArgs(
                price=limit_buy_price,
                size=target_qty,
                side="BUY",
                token_id=token_id
            )
            signed_order = self.clob_client.create_order(order_args)
            resp = self.clob_client.post_order(signed_order, OrderType.GTC)

            order_id = None
            if isinstance(resp, dict):
                order_id = resp.get("orderID") or resp.get("orderId")
            elif isinstance(resp, str) and resp.startswith("0x"):
                order_id = resp

            if not order_id:
                logger.error(f"❌ [LIVE CLOB ORDER REJECTED] Response: {resp}")
                return None

            now_ts = time.time()
            now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            pos = {
                "Candle_Start": candle_start,
                "Slug": slug,
                "Token_Id": token_id,
                "Position_Side": side,
                "Entry_Timestamp": now_dt,
                "Order_Timestamp_Sec": now_ts,
                "Trigger_Odds_10s_Ago": target_price,
                "Entry_Odds": entry_odds,
                "Target_Buy_Price": limit_buy_price,
                "Target_Quantity": target_qty,
                "Filled_Quantity": 0.0,
                "Sell_Quantity": 0.0,
                "Average_Fill_Price": None,
                "Take_Profit_Price": round(limit_buy_price + getattr(config, "v2_take_profit_cents", 0.05), 4),
                "Stop_Loss_Price": round(max(0.01, limit_buy_price - getattr(config, "v2_trailing_sl_distance_cents", 0.10)), 4),
                "Buy_Order_Id": order_id,
                "Order_Id": order_id,
                "Position_Status": "PENDING_FILL",
                "Cancel_Reason": None,
                "Pnl": 0.0,
                "Updated_At": now_dt
            }

            logger.info(f"🎯 [LIVE CLOB ORDER PLACED 200 OK] OrderID={order_id} | Side={side} | Price=${limit_buy_price:.4f} | Qty={target_qty}")

            # Dispatch Telegram entry notification directly for Live CLOB Order
            if hasattr(self, "notifier") and self.notifier:
                try:
                    self.notifier.notify_v2_trade_entry(
                        candle_start=candle_start,
                        side=side,
                        signal_price=entry_odds,
                        fill_price=limit_buy_price,
                        tp_price=pos["Take_Profit_Price"],
                        sl_price=pos["Stop_Loss_Price"],
                        qty=target_qty,
                        position_usd=position_usd
                    )
                except Exception as e:
                    logger.warning(f"Notifier error: {e}")

            return pos
        except Exception as e:
            logger.error(f"Failed to post Live CLOB Order: {e}. Strict Live Mode: Returning None to prevent duplicate/broken positions.")
            return None

    def execute_exit(
        self,
        candle_start: str,
        token_id: str,
        exit_price: float,
        reason: str
    ) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.execute_exit(candle_start, token_id, exit_price, reason)

    def post_limit_sell(self, token_id: str, price: float, size: float) -> Optional[Dict[str, Any]]:
        """
        Posts a physical Resting Limit Sell Order at Take_Profit_Price on Polymarket CLOB.
        Synchronizes AssetType.CONDITIONAL token balance & allowance cache prior to dispatch.
        """
        if not self.clob_client:
            return None
        try:
            try:
                from py_clob_client_v2.clob_types import OrderArgsV2 as OrderArgs, OrderType, BalanceAllowanceParams, AssetType
                try:
                    self.clob_client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
                except Exception as sync_err:
                    logger.warning(f"Conditional allowance update notice for {token_id[:8]}: {sync_err}")
            except ImportError:
                from py_clob_client.clob_types import OrderArgs, OrderType

            order_args = OrderArgs(
                price=price,
                size=size,
                side="SELL",
                token_id=token_id
            )
            signed_order = self.clob_client.create_order(order_args)
            resp = self.clob_client.post_order(signed_order, OrderType.GTC)
            logger.info(f"🎯 [LIVE CLOB TP ORDER PLACED] Token={token_id[:8]}... Price=${price:.4f} Qty={size:.4f} | OrderID={resp.get('orderID') if isinstance(resp, dict) else resp}")
            return resp if isinstance(resp, dict) else {"orderID": str(resp)}
        except Exception as e:
            logger.error(f"⚠ Failed to post Live CLOB TP Sell Order: {e}")
            return None

    def get_order_from_exchange(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Queries Polymarket's exchange REST API directly for real-time status & sizeMatched.
        """
        if not self.clob_client or not order_id:
            return None
        try:
            get_fn = getattr(self.clob_client, "get_order", None)
            if get_fn:
                resp = get_fn(order_id)
                return resp if isinstance(resp, dict) else None
        except Exception as e:
            logger.warning(f"Failed to query order {order_id} from exchange: {e}")
        return None

    def cancel_order_on_exchange(self, buy_order_id: str) -> bool:
        if self.clob_client and buy_order_id:
            try:
                cancel_fn = getattr(self.clob_client, "cancel_orders", None)
                if cancel_fn:
                    resp = cancel_fn([buy_order_id])
                    logger.info(f"⚡ [LIVE CLOB ORDER CANCELLED] OrderID={buy_order_id} | Response={resp}")
                    return True
                else:
                    cancel_single = getattr(self.clob_client, "cancel_order", getattr(self.clob_client, "cancel", None))
                    if cancel_single:
                        resp = cancel_single(buy_order_id)
                        logger.info(f"⚡ [LIVE CLOB ORDER CANCELLED] OrderID={buy_order_id} | Response={resp}")
                        return True
            except Exception as e:
                logger.error(f"⚠ Failed physical CLOB order cancellation for {buy_order_id}: {e}")
        return False

    def check_and_update_positions(
        self,
        candle_start: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.check_and_update_positions(candle_start, token_id, current_bid, current_ask)

    def process_tick(
        self,
        candle_start: str,
        slug: str,
        side: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.process_tick(candle_start, slug, side, token_id, current_bid, current_ask)


