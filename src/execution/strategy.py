"""
Polymarket Bot V4: High-Odds Trend Following Strategy Engine
(84¢-88¢ Entry Trigger, 99¢ Limit Take-Profit with Active Exchange Tracking, 40¢ Slippage-Protected Stop-Loss)
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
    @abstractmethod
    def execute_entry(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def execute_exit(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def check_and_update_positions(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        pass


class V4OddsStrategy(IExecutionStrategy):
    """
    Polymarket Bot V4 Strategy:
    1. Entry: Triggers BUY when UP or DOWN token odds are between 84¢ and 88¢ ($0.84 <= Ask <= $0.88).
       - Max 1 trade per 5m candle (never re-enters in the same candle after Take-Profit/Stop-Loss).
       - Skips entries during startup/boot candle to avoid entering stale mid-candle surges.
    2. Take Profit: Resting Limit Sell order placed at 99 cents ($0.99) with active exchange status polling.
    3. Stop Loss: Triggers when price drops <= 40 cents ($0.40).
       Executes Limit Sell at (current_bid - slippage) with dynamic 3.0s re-chasing loop.
    4. Fill Reconciliation & Zero-Balance Liquidation Guard.
    """

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None, notifier: Optional[Any] = None, live_strategy: Optional[Any] = None):
        self.async_writer = async_writer
        self.notifier = notifier
        self.live_strategy = live_strategy
        self.active_position: Optional[Dict[str, Any]] = None
        self.tick_buffers: Dict[str, List[Tuple[float, float, float]]] = {}
        # Record startup candle boundary so the bot never enters mid-candle on reboot
        self.boot_candle_sec = (int(time.time()) // 300) * 300
        self.last_traded_candle: Optional[str] = None
        self._last_cooldown_log = 0.0

    def cancel_order_on_exchange(self, order_id: str) -> bool:
        if self.live_strategy and hasattr(self.live_strategy, "cancel_order_on_exchange"):
            return self.live_strategy.cancel_order_on_exchange(order_id)
        return False

    def process_tick(
        self,
        candle_start: str,
        slug: str,
        side: str,
        token_id: str,
        current_bid: Optional[float],
        current_ask: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        if not config.trading_active or current_ask is None or current_ask <= 0:
            return None

        now_sec = time.time()

        if token_id not in self.tick_buffers:
            self.tick_buffers[token_id] = []
        buf = self.tick_buffers[token_id]
        buf.append((now_sec, current_bid or 0.0, current_ask))
        self.tick_buffers[token_id] = [t for t in buf if (now_sec - t[0]) <= 30.0]

        # 1. Evaluate PENDING_FILL position for execution confirmation
        if self.active_position and self.active_position.get("Position_Status") == "PENDING_FILL" and self.active_position.get("Token_Id") == token_id:
            self._evaluate_pending_fill(current_bid, current_ask)

        # 2. Evaluate active CLOSING position for sell fill confirmation on exchange
        if self.active_position and self.active_position.get("Position_Status") == "CLOSING" and self.active_position.get("Token_Id") == token_id:
            self._evaluate_closing_position(current_bid, current_ask)

        # 3. Evaluate TP/SL exit if this token has an active open position
        if self.active_position and self.active_position.get("Position_Status") == "OPEN" and self.active_position.get("Token_Id") == token_id:
            self._evaluate_tp_sl_exit(current_bid or current_ask, current_ask)

        # 4. Single Position Guard: Block new entry if active position exists
        if self.active_position is not None:
            return None

        # 5. Single Entry Per Candle Rule: Block re-entering in the same candle after a trade closed
        if self.last_traded_candle == candle_start:
            return None

        # 6. Startup Candle Cooldown: Skip new trades for the candle active during bot boot
        candle_start_sec = 0
        try:
            dt_obj = datetime.strptime(candle_start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            candle_start_sec = int(dt_obj.timestamp())
        except Exception:
            candle_start_sec = (int(now_sec) // 300) * 300

        if candle_start_sec <= self.boot_candle_sec:
            if (now_sec - self._last_cooldown_log) >= 10.0:
                self._last_cooldown_log = now_sec
                logger.info(f"⏳ [STARTUP CANDLE COOLDOWN] Ignoring mid-candle triggers for {candle_start}. New entries start next candle!")
            return None

        # 7. V4 Entry Trigger: 84¢ to 88¢ Odds Window ($0.84 <= Ask <= $0.88)
        min_thresh = getattr(config, "v4_entry_odds_threshold", 0.84)
        max_thresh = getattr(config, "v4_max_entry_odds_ceiling", 0.88)

        if min_thresh <= current_ask <= max_thresh:
            # 15s Candle Entry Cutoff
            sec_in_candle = int(now_sec - candle_start_sec)
            if 285 <= sec_in_candle < 300:
                logger.info(f"⌛ [15s CANDLE ENTRY CUTOFF] Skipping V4 entry at {sec_in_candle}s into candle.")
                return None

            limit_buy_price = current_ask
            raw_qty = round(getattr(config, "max_position_size_usd", 5.0) / limit_buy_price, 4) if limit_buy_price > 0 else 0.0
            target_qty = max(5.0, raw_qty)
            now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            pre_pos = {
                "Candle_Start": candle_start,
                "Slug": slug,
                "Token_Id": token_id,
                "Position_Side": side,
                "Entry_Timestamp": now_dt,
                "Order_Timestamp_Sec": now_sec,
                "Entry_Odds": current_ask,
                "Target_Buy_Price": limit_buy_price,
                "Target_Quantity": target_qty,
                "Filled_Quantity": 0.0,
                "Position_Status": "PENDING_FILL",
                "Buy_Order_Id": f"V4_ORDER_{int(now_sec*1000)}"
            }
            self.active_position = pre_pos
            self.last_traded_candle = candle_start

            if self.live_strategy and self.live_strategy.clob_client:
                pos = self.live_strategy.execute_entry(
                    candle_start=candle_start,
                    slug=slug,
                    side=side,
                    prob_cal=0.50,
                    prob_uncal=0.50,
                    target_price=current_ask,
                    position_usd=getattr(config, "max_position_size_usd", 5.0),
                    token_id=token_id,
                    current_bid=current_bid,
                    current_ask=current_ask
                )
                if pos:
                    pre_pos.update(pos)
                    pre_pos["Position_Status"] = "PENDING_FILL"
                else:
                    self.active_position = None
                    return None

            return self.active_position

        return None

    def _evaluate_pending_fill(self, current_bid: Optional[float], current_ask: Optional[float]) -> None:
        pos = self.active_position
        if not pos or pos.get("Position_Status") != "PENDING_FILL":
            return

        now_sec = time.time()
        now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        order_sec = pos.get("Order_Timestamp_Sec", now_sec)
        elapsed_sec = now_sec - order_sec

        limit_buy_price = pos["Target_Buy_Price"]
        target_qty = pos["Target_Quantity"]
        candle_start = pos["Candle_Start"]
        token_id = pos.get("Token_Id", "")
        buy_order_id = pos.get("Buy_Order_Id")
        timeout_sec = getattr(config, "v4_order_timeout_sec", 5.0)

        eff_price = current_bid if (current_bid is not None and current_bid > 0) else current_ask
        peak_price = max(current_bid or 0.0, current_ask or 0.0)

        # 1. Query Polymarket for exact fill status & real fill price
        size_matched = 0.0
        real_fill_price = None
        if self.live_strategy and self.live_strategy.clob_client and buy_order_id:
            order_info = self.live_strategy.get_order_from_exchange(buy_order_id)
            if order_info and isinstance(order_info, dict):
                size_matched = round(float(order_info.get("sizeMatched") or order_info.get("size_matched") or 0.0), 4)
                making = float(order_info.get("makingAmount") or order_info.get("making_amount") or 0.0)
                taking = float(order_info.get("takingAmount") or order_info.get("taking_amount") or 0.0)
                if making > 0 and taking > 0:
                    r1, r2 = making / taking, taking / making
                    if 0.01 <= r1 <= 1.00:
                        real_fill_price = round(r1, 4)
                    elif 0.01 <= r2 <= 1.00:
                        real_fill_price = round(r2, 4)
                elif order_info.get("price"):
                    try:
                        p_val = float(order_info["price"])
                        if 0.01 <= p_val <= 1.00:
                            real_fill_price = p_val
                    except Exception:
                        pass
        else:
            if elapsed_sec < timeout_sec and eff_price is not None and eff_price <= limit_buy_price:
                size_matched = target_qty

        if size_matched > 0:
            fill_price = real_fill_price if (real_fill_price is not None and real_fill_price > 0) else limit_buy_price
            take_profit_price = getattr(config, "v4_take_profit_price", 0.99)
            stop_loss_price = getattr(config, "v4_stop_loss_price", 0.40)

            pos["Average_Fill_Price"] = fill_price
            pos["Filled_Quantity"] = size_matched
            pos["Take_Profit_Price"] = take_profit_price
            pos["Stop_Loss_Price"] = stop_loss_price
            pos["High_Water_Mark"] = max(fill_price, peak_price)
            pos["Position_Status"] = "OPEN"

            if not pos.get("Entry_Notified"):
                pos["Entry_Notified"] = True
                if hasattr(self, "notifier") and self.notifier:
                    try:
                        self.notifier.notify_v2_trade_entry(
                            candle_start,
                            pos.get("Position_Side", "UP"),
                            pos.get("Entry_Odds", fill_price),
                            fill_price,
                            take_profit_price,
                            stop_loss_price,
                            size_matched,
                            round(fill_price * size_matched, 2)
                        )
                    except Exception as e:
                        logger.warning(f"Failed to dispatch Telegram entry notification: {e}")

            # Instant SL check on entry fill
            if eff_price is not None and eff_price <= stop_loss_price:
                logger.warning(f"🚨 [V4 INSTANT SL TRIGGERED] Price ${eff_price:.4f} <= SL ${stop_loss_price:.4f}! Exiting...")
                if buy_order_id:
                    self.cancel_order_on_exchange(buy_order_id)
                slippage = getattr(config, "v4_stop_loss_slippage_cents", 0.02)
                limit_sell_price = round(max(0.01, min(stop_loss_price, eff_price) - slippage), 4)

                sell_order_id = None
                if self.live_strategy and self.live_strategy.clob_client and token_id:
                    sl_resp = self.live_strategy.post_limit_sell(token_id, limit_sell_price, size_matched)
                    if sl_resp and isinstance(sl_resp, dict) and ("orderID" in sl_resp or "orderId" in sl_resp):
                        sell_order_id = sl_resp.get("orderID") or sl_resp.get("orderId")
                else:
                    sell_order_id = f"V4_SL_{int(now_sec*1000)}"

                pos["Position_Status"] = "CLOSING"
                pos["Closing_Timestamp_Sec"] = now_sec
                pos["Exit_Reason"] = "STOP_LOSS"
                pos["Trade_Outcome"] = "STOP_LOSS_HIT"
                pos["Exit_Price"] = limit_sell_price
                pos["Sell_Limit_Price"] = limit_sell_price
                pos["Exit_Timestamp"] = now_dt
                pos["Sell_Order_Id"] = sell_order_id
                pos["Exit_Quantity"] = size_matched
                pos["Pnl"] = round((limit_sell_price - fill_price) * size_matched, 4)
                pos["Updated_At"] = now_dt

                if self.async_writer:
                    sql = "UPDATE Positions SET Position_Status = 'CLOSING', Exit_Reason = 'STOP_LOSS', Exit_Price = ?, Updated_At = ? WHERE Buy_Order_Id = ?;"
                    self.async_writer.enqueue_write(sql, (stop_loss_price, now_dt, buy_order_id))

                self._evaluate_closing_position(current_bid, current_ask)
                return

            # Resting Take-Profit Limit Sell Order at $0.99
            prev_tp_qty = pos.get("Tp_Qty", 0.0)
            if self.live_strategy and self.live_strategy.clob_client and (not pos.get("Tp_Order_Id") or prev_tp_qty != size_matched):
                if pos.get("Tp_Order_Id"):
                    self.cancel_order_on_exchange(pos["Tp_Order_Id"])
                tp_resp = self.live_strategy.post_limit_sell(token_id, take_profit_price, size_matched)
                if tp_resp and isinstance(tp_resp, dict) and ("orderID" in tp_resp or "orderId" in tp_resp):
                    pos["Tp_Order_Id"] = tp_resp.get("orderID") or tp_resp.get("orderId")
                    pos["Tp_Qty"] = size_matched

        # 2. 5.0-Second Timeout Resolution & Exchange Reconciliation
        if elapsed_sec >= timeout_sec:
            filled_qty = pos.get("Filled_Quantity", 0.0)

            if filled_qty <= 0.0:
                if buy_order_id:
                    self.cancel_order_on_exchange(buy_order_id)

                check_info = None
                if self.live_strategy and self.live_strategy.clob_client and buy_order_id and not str(buy_order_id).startswith("V4_"):
                    check_info = self.live_strategy.get_order_from_exchange(buy_order_id)

                check_matched = 0.0
                check_price = None
                making, taking = 0.0, 0.0
                if check_info and isinstance(check_info, dict):
                    check_matched = round(float(check_info.get("sizeMatched") or check_info.get("size_matched") or 0.0), 4)
                    making = float(check_info.get("makingAmount") or check_info.get("making_amount") or 0.0)
                    taking = float(check_info.get("takingAmount") or check_info.get("taking_amount") or 0.0)
                    if making > 0 and taking > 0:
                        r1, r2 = making / taking, taking / making
                        if 0.01 <= r1 <= 1.00:
                            check_price = round(r1, 4)
                        elif 0.01 <= r2 <= 1.00:
                            check_price = round(r2, 4)
                    elif check_info.get("price"):
                        try:
                            p_v = float(check_info["price"])
                            if 0.01 <= p_v <= 1.00:
                                check_price = p_v
                        except Exception:
                            pass

                is_bought = False
                if check_info and isinstance(check_info, dict):
                    status_upper = str(check_info.get("status", "")).upper()
                    if check_matched > 0 or making > 0 or taking > 0 or status_upper in ("MATCHED", "FILLED", "CLOSED"):
                        is_bought = True

                if is_bought:
                    final_fill_price = check_price if (check_price is not None and check_price > 0) else limit_buy_price
                    calc_qty = check_matched
                    if calc_qty <= 0 and taking > 0:
                        calc_qty = round(taking / 1000000.0, 4) if taking > 1000 else round(taking, 4)
                    final_fill_qty = calc_qty if calc_qty > 0 else target_qty

                    take_profit_price = getattr(config, "v4_take_profit_price", 0.99)
                    stop_loss_price = getattr(config, "v4_stop_loss_price", 0.40)

                    pos["Average_Fill_Price"] = final_fill_price
                    pos["Filled_Quantity"] = final_fill_qty
                    pos["Take_Profit_Price"] = take_profit_price
                    pos["Stop_Loss_Price"] = stop_loss_price
                    pos["High_Water_Mark"] = max(final_fill_price, peak_price)
                    pos["Position_Status"] = "OPEN"
                    logger.info(
                        f"🎯 [V4 TIMEOUT RECONCILIATION] Order {buy_order_id} filled {final_fill_qty:.4f} shares at ${final_fill_price:.4f} on exchange! "
                        f"Transitioning to OPEN position for live TP/SL tracking."
                    )
                    if not pos.get("Entry_Notified"):
                        pos["Entry_Notified"] = True
                        if hasattr(self, "notifier") and self.notifier:
                            try:
                                self.notifier.notify_v2_trade_entry(
                                    candle_start,
                                    pos.get("Position_Side", "UP"),
                                    pos.get("Entry_Odds", final_fill_price),
                                    final_fill_price,
                                    take_profit_price,
                                    stop_loss_price,
                                    final_fill_qty,
                                    round(final_fill_price * final_fill_qty, 2)
                                )
                            except Exception as e:
                                logger.warning(f"Failed to dispatch Telegram entry notification: {e}")
                    return

                pos["Position_Status"] = "CANCELLED"
                pos["Cancel_Reason"] = f"TIMEOUT_{timeout_sec:.1f}S"
                pos["Updated_At"] = now_dt

                if self.async_writer:
                    sql = "UPDATE Positions SET Position_Status = 'CANCELLED', Cancel_Reason = ?, Updated_At = ? WHERE Buy_Order_Id = ?;"
                    self.async_writer.enqueue_write(sql, (pos["Cancel_Reason"], now_dt, buy_order_id))

                logger.warning(
                    f"⏰ [V4 ORDER TIMEOUT] Cancelled! Side={pos.get('Position_Side')} | Candle={candle_start} | "
                    f"Limit_Price=${limit_buy_price:.4f} unfilled after {elapsed_sec:.1f}s. Unlocking position guard."
                )

                if token_id in self.tick_buffers:
                    self.tick_buffers[token_id].clear()
                self.active_position = None
            else:
                if buy_order_id:
                    self.cancel_order_on_exchange(buy_order_id)
                pos["Position_Status"] = "OPEN"
                logger.info(f"🎯 [V4 PARTIAL FILL TIMEOUT] Closed remaining buy balance for {buy_order_id}. Open shares={filled_qty:.4f}")

    def _evaluate_closing_position(self, current_bid: Optional[float], current_ask: Optional[float]) -> None:
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
        slippage = getattr(config, "v4_stop_loss_slippage_cents", 0.02)

        if self.live_strategy and self.live_strategy.clob_client:
            if sell_order_id and not str(sell_order_id).startswith("V4_"):
                order_info = self.live_strategy.get_order_from_exchange(sell_order_id)
                if order_info and isinstance(order_info, dict):
                    size_matched = round(float(order_info.get("size_matched") or order_info.get("sizeMatched") or 0.0), 4)
                    status = str(order_info.get("status", "")).upper()

                    making = float(order_info.get("makingAmount") or order_info.get("making_amount") or 0.0)
                    taking = float(order_info.get("takingAmount") or order_info.get("taking_amount") or 0.0)

                    real_price = 0.0
                    if making > 0 and taking > 0:
                        real_price = round(taking / making, 4)
                    elif order_info.get("price"):
                        real_price = float(order_info["price"])

                    if real_price > 0:
                        pos["Exit_Price"] = real_price
                        entry_p = pos.get("Average_Fill_Price") or pos.get("Target_Buy_Price", 0.0)
                        calc_qty = size_matched if size_matched > 0 else exit_qty
                        calc_pnl = round((real_price - entry_p) * calc_qty, 4)
                        pos["Pnl"] = calc_pnl
                        pos["Trade_Outcome"] = "WIN" if real_price >= entry_p else "LOSS"

                    if status in ("MATCHED", "FILLED", "CLOSED") or size_matched >= (exit_qty - 0.01):
                        is_filled = True
                        pos["Sell_Order_Dispatched"] = True

                # Dynamic re-chasing if price dropped below resting limit or resting > 3s
                if not is_filled:
                    cur_limit_price = pos.get("Sell_Limit_Price") or pos.get("Exit_Price", 0.0)
                    should_rechase = False

                    if current_bid is not None and current_bid <= (cur_limit_price - 0.01):
                        should_rechase = True
                    elif elapsed_sec >= 3.0:
                        should_rechase = True

                    if should_rechase:
                        logger.info(f"🔄 [V4 DYNAMIC RE-CHASE TRIGGERED] Market price dropped (Bid: ${current_bid}) below Limit Sell ${cur_limit_price:.4f}. Re-pricing SL order...")
                        self.cancel_order_on_exchange(sell_order_id)
                        check_info = self.live_strategy.get_order_from_exchange(sell_order_id)
                        if check_info and isinstance(check_info, dict):
                            c_status = str(check_info.get("status", "")).upper()
                            c_matched = round(float(check_info.get("size_matched") or check_info.get("sizeMatched") or 0.0), 4)
                            if c_status in ("MATCHED", "FILLED", "CLOSED") or c_matched >= (exit_qty - 0.01):
                                is_filled = True
                                pos["Sell_Order_Dispatched"] = True

                        if not is_filled and pos.get("Token_Id"):
                            remaining_qty = exit_qty
                            new_limit_price = round(max(0.01, (current_bid or cur_limit_price) - slippage), 4)
                            new_resp = self.live_strategy.post_limit_sell(pos["Token_Id"], new_limit_price, remaining_qty)
                            if new_resp and isinstance(new_resp, dict) and new_resp.get("error") == "ZERO_BALANCE":
                                logger.info("🏁 [V4 POSITION 100% LIQUIDATED] Token balance is 0 on exchange. Trade confirmed closed.")
                                is_filled = True
                            elif new_resp and isinstance(new_resp, dict) and ("orderID" in new_resp or "orderId" in new_resp):
                                new_id = new_resp.get("orderID") or new_resp.get("orderId")
                                pos["Sell_Order_Dispatched"] = True
                                pos["Sell_Order_Id"] = new_id
                                pos["Sell_Limit_Price"] = new_limit_price
                                pos["Closing_Timestamp_Sec"] = now_sec
                                logger.info(f"🎯 [V4 RE-CHASE SL ORDER DISPATCHED] OrderID={new_id} Price=${new_limit_price:.4f} Qty={remaining_qty:.4f}")
                            else:
                                pos["Sell_Order_Id"] = None
            else:
                if pos.get("Token_Id"):
                    cur_limit_price = pos.get("Sell_Limit_Price") or pos.get("Exit_Price") or pos.get("Stop_Loss_Price", 0.01)
                    target_bid = current_bid if (current_bid is not None and current_bid > 0) else cur_limit_price
                    new_limit_price = round(max(0.01, min(cur_limit_price, target_bid) - slippage), 4)
                    new_resp = self.live_strategy.post_limit_sell(pos["Token_Id"], new_limit_price, exit_qty)
                    if new_resp and isinstance(new_resp, dict) and new_resp.get("error") == "ZERO_BALANCE":
                        if pos.get("Sell_Order_Dispatched"):
                            logger.info("🏁 [V4 POSITION 100% LIQUIDATED] Token balance is 0 on exchange. Trade confirmed closed.")
                            is_filled = True
                    elif new_resp and isinstance(new_resp, dict) and ("orderID" in new_resp or "orderId" in new_resp):
                        new_id = new_resp.get("orderID") or new_resp.get("orderId")
                        pos["Sell_Order_Dispatched"] = True
                        pos["Sell_Order_Id"] = new_id
                        pos["Sell_Limit_Price"] = new_limit_price
                        pos["Closing_Timestamp_Sec"] = now_sec
                        logger.info(f"🎯 [V4 SL LIMIT SELL PLACED ON RETRY] OrderID={new_id} Price=${new_limit_price:.4f} Qty={exit_qty:.4f}")
        else:
            is_filled = True

        if is_filled:
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
                f"🏁 [V4 TRADE CLOSED & CONFIRMED] Reason={exit_reason} | Side={pos.get('Position_Side') or pos.get('Prediction_Side', 'UP')} | Candle={candle_start} | "
                f"Exit_Price=${exit_price:.4f} | PnL=${pnl:+.4f}"
            )

            if hasattr(self, "notifier") and self.notifier:
                try:
                    entry_p = pos.get("Average_Fill_Price") or pos.get("Target_Buy_Price", 0.0)
                    self.notifier.notify_v2_trade_exit(
                        candle_start,
                        pos.get('Position_Side') or pos.get('Prediction_Side', 'UP'),
                        exit_price,
                        exit_reason,
                        pnl,
                        entry_price=entry_p,
                        qty=exit_qty
                    )
                except Exception as e:
                    logger.warning(f"Failed to dispatch Telegram exit notification: {e}")

            if pos.get("Token_Id") in self.tick_buffers:
                self.tick_buffers[pos["Token_Id"]].clear()
            self.active_position = None

    def _evaluate_tp_sl_exit(self, current_price: float, current_ask: Optional[float] = None) -> Optional[Dict[str, Any]]:
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

        current_bid = current_price
        peak_price = max(current_bid or 0.0, current_ask or 0.0)
        hwm = max(pos.get("High_Water_Mark", entry_price), peak_price)
        pos["High_Water_Mark"] = hwm

        # 1. Active Resting Take-Profit Exchange Status Polling
        # Real-time exchange verification: check if Polymarket matched our resting 99¢ Limit Sell
        if self.live_strategy and self.live_strategy.clob_client and pos.get("Tp_Order_Id") and not str(pos["Tp_Order_Id"]).startswith("V4_"):
            last_tp_poll = pos.get("_last_tp_poll_sec", 0.0)
            if (now_ts - last_tp_poll) >= 1.5:
                pos["_last_tp_poll_sec"] = now_ts
                tp_order_info = self.live_strategy.get_order_from_exchange(pos["Tp_Order_Id"])
                if tp_order_info and isinstance(tp_order_info, dict):
                    tp_status = str(tp_order_info.get("status", "")).upper()
                    tp_matched = round(float(tp_order_info.get("size_matched") or tp_order_info.get("sizeMatched") or 0.0), 4)
                    tp_making = float(tp_order_info.get("makingAmount") or tp_order_info.get("making_amount") or 0.0)
                    tp_taking = float(tp_order_info.get("takingAmount") or tp_order_info.get("taking_amount") or 0.0)

                    tp_real_price = tp_price
                    if tp_making > 0 and tp_taking > 0:
                        tp_real_price = round(tp_taking / tp_making, 4)
                    elif tp_order_info.get("price"):
                        try:
                            tp_real_price = float(tp_order_info["price"])
                        except Exception:
                            pass

                    if tp_status in ("MATCHED", "FILLED", "CLOSED") or tp_matched >= (filled_qty - 0.01):
                        logger.info(
                            f"🎯 [V4 TP ORDER FILLED ON POLYMARKET] Order {pos['Tp_Order_Id']} filled "
                            f"{tp_matched:.4f}/{filled_qty:.4f} shares at ${tp_real_price:.4f} on exchange!"
                        )
                        pos["Exit_Price"] = tp_real_price
                        pos["Exit_Quantity"] = tp_matched if tp_matched > 0 else filled_qty
                        pos["Exit_Reason"] = "TAKE_PROFIT"
                        pos["Trade_Outcome"] = "WIN"
                        pos["Sell_Order_Id"] = pos["Tp_Order_Id"]
                        pos["Closing_Timestamp_Sec"] = now_ts
                        pos["Position_Status"] = "CLOSING"
                        pnl = round((tp_real_price - entry_price) * pos["Exit_Quantity"], 4)
                        pos["Pnl"] = pnl
                        self._evaluate_closing_position(current_bid, current_ask)
                        return pos

        # 2. Persistent TP limit order retry at $0.99
        prev_tp_qty = pos.get("Tp_Qty", 0.0)
        if self.live_strategy and self.live_strategy.clob_client and (not pos.get("Tp_Order_Id") or prev_tp_qty != filled_qty) and filled_qty > 0:
            if pos.get("Tp_Order_Id"):
                self.cancel_order_on_exchange(pos["Tp_Order_Id"])
            tp_resp = self.live_strategy.post_limit_sell(pos["Token_Id"], tp_price, filled_qty)
            if tp_resp and isinstance(tp_resp, dict) and ("orderID" in tp_resp or "orderId" in tp_resp):
                pos["Tp_Order_Id"] = tp_resp.get("orderID") or tp_resp.get("orderId")
                pos["Tp_Qty"] = filled_qty
                logger.info(f"🎯 [V4 PERSISTENT TP RETRY SUCCESS] OrderID={pos['Tp_Order_Id']} placed for {filled_qty:.4f} shares.")

        is_tp_trigger = current_bid >= tp_price
        is_sl_trigger = current_bid <= sl_price
        is_expired_trigger = False

        order_placed_sec = pos.get("Order_Timestamp_Sec", now_ts)
        elapsed_since_order = now_ts - order_placed_sec

        # 2-Minute Expiration Safety Guard: If trade is > 7 minutes old
        if elapsed_since_order >= 420.0 and not is_tp_trigger and not is_sl_trigger:
            is_expired_trigger = True
            logger.info(f"⌛ [V4 2-MIN EXPIRATION GUARD] Position elapsed {elapsed_since_order:.1f}s. Settling final resolution...")

        if is_tp_trigger or is_sl_trigger or is_expired_trigger:
            if is_tp_trigger:
                exit_reason = "TAKE_PROFIT"
                trade_outcome = "WIN"
                exit_price = tp_price
            elif is_sl_trigger:
                exit_reason = "STOP_LOSS"
                trade_outcome = "LOSS" if sl_price < entry_price else "WIN"
                exit_price = sl_price
            else:
                exit_reason = "MARKET_RESOLVED"
                raw_p = current_bid or current_price or entry_price
                exit_price = 1.00 if raw_p >= 0.50 else 0.00
                trade_outcome = "WIN" if exit_price >= 0.50 else "LOSS"

            sell_order_id = pos.get("Tp_Order_Id") if is_tp_trigger else None
            if not self.live_strategy or not self.live_strategy.clob_client:
                sell_order_id = pos.get("Tp_Order_Id") or f"V4_SELL_{int(now_ts*1000)}"

            if is_sl_trigger and pos.get("Tp_Order_Id"):
                logger.info(f"🛑 [V4 STOP-LOSS EXECUTING] Cancelling resting TP order {pos['Tp_Order_Id']} on exchange...")
                self.cancel_order_on_exchange(pos["Tp_Order_Id"])

            prev_sell_qty = pos.get("Sell_Quantity", 0.0)
            exit_qty = filled_qty - prev_sell_qty
            if exit_qty <= 0:
                exit_qty = filled_qty

            if is_sl_trigger:
                slippage = getattr(config, "v4_stop_loss_slippage_cents", 0.02)
                effective_bid = current_bid if (current_bid is not None and current_bid > 0) else sl_price
                limit_sell_price = round(max(0.01, min(sl_price, effective_bid) - slippage), 4)
                exit_price = limit_sell_price

                if self.live_strategy and self.live_strategy.clob_client and pos.get("Token_Id"):
                    sl_resp = self.live_strategy.post_limit_sell(pos["Token_Id"], limit_sell_price, exit_qty)
                    if sl_resp and isinstance(sl_resp, dict) and ("orderID" in sl_resp or "orderId" in sl_resp):
                        sell_order_id = sl_resp.get("orderID") or sl_resp.get("orderId")
                    else:
                        sell_order_id = None
                else:
                    sell_order_id = f"V4_SL_{int(now_ts*1000)}"

                pos["Sell_Limit_Price"] = limit_sell_price

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

            pnl = round((exit_price - entry_price) * exit_qty, 4)
            pos["Pnl"] = pnl

            self._evaluate_closing_position(current_bid, current_ask)
            return pos

        return None

    def execute_entry(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None

    def execute_exit(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None

    def check_and_update_positions(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return None


class V4LiveExecutionStrategy(IExecutionStrategy):
    """
    Live Execution Strategy Wrapper for Bot V4.
    Integrates Polymarket CLOB REST API client and EIP-712 cryptographic signature handling.
    """

    def __init__(self, async_writer: Optional[AsyncDBWriter] = None, notifier: Optional[Any] = None):
        self.notifier = notifier
        self.dry_strategy = V4OddsStrategy(async_writer, notifier=notifier, live_strategy=self)
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

                sig_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "3" if funder else "0").strip("\"' "))
                host = getattr(config, "polymarket_clob_url", "https://clob.polymarket.com")

                logger.info(f"🔑 [L1 AUTH] Initializing EIP-712 L1 Auth Client (signature_type={sig_type}, funder={funder[:10] if funder else 'None'})...")
                self.clob_client = ClobClient(
                    host=host,
                    key=private_key,
                    chain_id=137,
                    signature_type=sig_type,
                    funder=funder
                )

                creds = None
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
                    logger.info("✓ [V4 CLOB CLIENT INITIALIZED] Authenticated Polymarket Live CLOB client connected.")
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

            except Exception as e:
                logger.error(f"Failed to initialize V4 Live CLOB client: {e}")
                self.clob_client = None

    def process_tick(self, *args, **kwargs) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.process_tick(*args, **kwargs)

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
        if not self.clob_client or not token_id:
            return self.dry_strategy.execute_entry(candle_start, slug, side, prob_cal, prob_uncal, target_price, position_usd, token_id)

        try:
            try:
                from py_clob_client_v2.clob_types import OrderArgsV2 as OrderArgs, OrderType
            except ImportError:
                from py_clob_client.clob_types import OrderArgs, OrderType

            entry_odds = target_price
            limit_buy_price = entry_odds
            raw_qty = round(position_usd / limit_buy_price, 4) if limit_buy_price > 0 else 0.0
            target_qty = max(5.0, raw_qty)

            order_args = OrderArgs(
                price=limit_buy_price,
                size=target_qty,
                side="BUY",
                token_id=token_id
            )

            logger.info(f"⚡ [V4 LIVE CLOB ORDER DISPATCH] Submitting Buy Limit Order for token {token_id[:8]}... Price=${limit_buy_price:.4f} Qty={target_qty}")
            signed_order = self.clob_client.create_order(order_args)
            resp = self.clob_client.post_order(signed_order, OrderType.GTC)

            order_id = None
            if isinstance(resp, dict):
                order_id = resp.get("orderID") or resp.get("orderId")
            elif isinstance(resp, str) and resp.startswith("0x"):
                order_id = resp

            if not order_id:
                logger.error(f"❌ [V4 LIVE CLOB ORDER REJECTED] Response: {resp}")
                return None

            now_ts = time.time()
            now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            pos = {
                "Candle_Start": candle_start,
                "Slug": slug,
                "Token_Id": token_id,
                "Prediction_Side": side,
                "Position_Side": side,
                "Prob_Cal": prob_cal,
                "Prob_Uncal": prob_uncal,
                "Target_Buy_Price": limit_buy_price,
                "Average_Fill_Price": limit_buy_price,
                "Target_Quantity": target_qty,
                "Filled_Quantity": 0.0,
                "Take_Profit_Price": getattr(config, "v4_take_profit_price", 0.99),
                "Stop_Loss_Price": getattr(config, "v4_stop_loss_price", 0.40),
                "High_Water_Mark": limit_buy_price,
                "Entry_Timestamp": now_dt,
                "Order_Timestamp_Sec": now_ts,
                "Buy_Order_Id": order_id,
                "Order_Id": order_id,
                "Position_Status": "PENDING_FILL",
                "Cancel_Reason": None,
                "Pnl": 0.0,
                "Updated_At": now_dt
            }

            logger.info(f"🎯 [V4 LIVE CLOB ORDER PLACED 200 OK] OrderID={order_id} | Side={side} | Price=${limit_buy_price:.4f} | Qty={target_qty}")
            return pos
        except Exception as e:
            logger.error(f"Failed to post V4 Live CLOB Order: {e}")
            return None

    def execute_exit(self, candle_start: str, token_id: str, exit_price: float, reason: str) -> Optional[Dict[str, Any]]:
        return self.dry_strategy.execute_exit(candle_start, token_id, exit_price, reason)

    def post_limit_sell(self, token_id: str, price: float, size: float) -> Optional[Dict[str, Any]]:
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
            logger.info(f"🎯 [V4 LIVE CLOB LIMIT SELL PLACED] Token={token_id[:8]}... Price=${price:.4f} Qty={size:.4f} | OrderID={resp.get('orderID') if isinstance(resp, dict) else resp}")
            return resp if isinstance(resp, dict) else {"orderID": str(resp)}
        except Exception as e:
            logger.error(f"⚠ Failed to post V4 Live CLOB Limit Sell Order: {e}")
            err_str = str(e).lower()
            if "balance is not enough" in err_str or "balance: 0" in err_str:
                return {"error": "ZERO_BALANCE", "message": str(e)}
            return None

    def get_order_from_exchange(self, order_id: str) -> Optional[Dict[str, Any]]:
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
                    if isinstance(resp, dict):
                        canceled_list = resp.get("canceled", [])
                        not_canceled_map = resp.get("not_canceled", {})
                        if buy_order_id in canceled_list or str(buy_order_id) in canceled_list:
                            logger.info(f"⚡ [V4 LIVE CLOB ORDER CANCELLED] OrderID={buy_order_id} confirmed cancelled on exchange.")
                            return True
                        elif buy_order_id in not_canceled_map or str(buy_order_id) in not_canceled_map:
                            reason = not_canceled_map.get(buy_order_id) or not_canceled_map.get(str(buy_order_id))
                            logger.info(f"ℹ [V4 CLOB CANCEL NOTICE] OrderID={buy_order_id}: {reason}")
                            return True
                    return True
                else:
                    cancel_single = getattr(self.clob_client, "cancel_order", getattr(self.clob_client, "cancel", None))
                    if cancel_single:
                        resp = cancel_single(buy_order_id)
                        logger.info(f"⚡ [V4 LIVE CLOB ORDER CANCELLED] OrderID={buy_order_id} | Response={resp}")
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
