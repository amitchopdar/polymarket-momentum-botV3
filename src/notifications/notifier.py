"""
Telegram Notifier System (Sprint 3: US4.2)
Provides a non-blocking, rate-limited notification queue to send trade signals, fills,
stop-loss triggers, and closed PnL alerts to Telegram.
"""

import time
import queue
import logging
import threading
import urllib.request
import urllib.parse
import json
from typing import Optional, Dict, Any

from datetime import datetime, timezone, timedelta

from src.config import config

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

def format_ist(dt_or_str: Any) -> str:
    if not dt_or_str:
        return ""
    if isinstance(dt_or_str, str):
        try:
            dt = datetime.strptime(dt_or_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return f"{dt_or_str} IST"
    elif isinstance(dt_or_str, datetime):
        dt = dt_or_str if dt_or_str.tzinfo else dt_or_str.replace(tzinfo=timezone.utc)
    else:
        return str(dt_or_str)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")

class TelegramNotifier:
    """
    Non-blocking, thread-safe Telegram Notification Dispatcher with rate limiting.
    Queue-backed async sender prevents network delays from blocking the trading engine loop.
    """

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        raw_token = bot_token or getattr(config, "telegram_bot_token", "")
        raw_chat = chat_id or getattr(config, "telegram_chat_id", "")
        self.bot_token = str(raw_token).strip("\"' ") if raw_token else ""
        self.chat_id = str(raw_chat).strip("\"' ") if raw_chat else ""
        self.msg_queue: queue.Queue = queue.Queue()
        self.running = False
        self.worker_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """
        Starts background worker thread for sending queued notifications.
        """
        if not self.bot_token or not self.chat_id:
            logger.info("Telegram Notifier disabled (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured).")
            return

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="TelegramNotifierWorker")
        self.worker_thread.start()
        logger.info("✓ [TELEGRAM NOTIFIER] Background worker thread started successfully.")

    def stop(self) -> None:
        """
        Stops background worker thread.
        """
        self.running = False

    def send_message(self, html_text: str) -> None:
        """
        Enqueues HTML formatted text message for async dispatch.
        """
        if not self.bot_token or not self.chat_id:
            return
        self.msg_queue.put(html_text)

    def _worker_loop(self) -> None:
        while self.running:
            try:
                msg = self.msg_queue.get(timeout=1.0)
                self._dispatch_http_request(msg)
                time.sleep(0.05)  # Enforce rate limit (< 30 msgs/sec)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in TelegramNotifier worker loop: {e}")

    def _dispatch_http_request(self, html_text: str) -> None:
        chat_ids = set()
        if self.chat_id:
            for item in str(self.chat_id).split(","):
                cleaned = item.strip("\"' ")
                if cleaned:
                    chat_ids.add(cleaned)
        for uid in getattr(config, "telegram_authorized_user_ids", []):
            if uid:
                cleaned = str(uid).strip("\"' ")
                if cleaned:
                    chat_ids.add(cleaned)

        for cid in chat_ids:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": cid,
                "text": html_text,
                "parse_mode": "HTML"
            }).encode("utf-8")

            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": "PolymarketBot/1.0"})
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    if resp.status != 200:
                        logger.warning(f"Telegram API response HTTP {resp.status} for chat_id {cid}")
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    logger.warning(f"Telegram user {cid} has not initialized conversation with the bot (click /start in Telegram).")
                else:
                    logger.warning(f"Failed to dispatch Telegram message to chat_id {cid}: HTTP {e.code}")
            except Exception as e:
                logger.error(f"Failed to dispatch Telegram message to chat_id {cid}: {e}")

    # Convenience Formatted Alert Helpers

    def notify_signal(self, candle_start: str, side: str, p_cal: float, p_uncal: float, target_price: float) -> None:
        ist_str = format_ist(candle_start)
        p_up = p_cal * 100.0
        p_dn = (1.0 - p_cal) * 100.0
        text = (
            f"🎯 <b>SIGNAL GENERATED</b>\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• 📈 <b>UP Prob:</b> <code>{p_up:.1f}%</code> | 📉 <b>DOWN Prob:</b> <code>{p_dn:.1f}%</code>\n"
            f"• <b>Prediction:</b> <code>{side}</code>\n"
            f"• <b>Limit Target:</b> <code>${target_price:.2f}</code>\n"
            f"• <b>Mode:</b> <code>{config.execution_mode}</code>"
        )
        self.send_message(text)

    def notify_fill(self, candle_start: str, side: str, fill_price: float, qty: float, tx_usd: float) -> None:
        ist_str = format_ist(candle_start)
        text = (
            f"✅ <b>ORDER FILLED & STOP-LOSS ACTIVE</b>\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• <b>Side:</b> <code>{side}</code>\n"
            f"• <b>Fill Price:</b> <code>${fill_price:.2f}</code>\n"
            f"• <b>Shares:</b> <code>{qty}</code> (Spend: ${tx_usd:.2f})\n"
            f"• <b>Automated Stop-Loss:</b> <code>${config.stop_loss_price:.2f}</code>"
        )
        self.send_message(text)

    def notify_stop_loss(self, candle_start: str, exit_price: float, pnl: float) -> None:
        ist_str = format_ist(candle_start)
        text = (
            f"🛑 <b>STOP-LOSS TRIGGERED</b>\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• <b>Exit Price:</b> <code>${exit_price:.2f}</code>\n"
            f"• <b>PnL:</b> <code>${pnl:+.2f}</code>"
        )
        self.send_message(text)

    def notify_exit(self, candle_start: str, exit_price: float, reason: str, pnl: float) -> None:
        ist_str = format_ist(candle_start)
        emoji = "📈" if pnl >= 0 else "📉"
        text = (
            f"{emoji} <b>TRADE CLOSED</b>\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• <b>Exit Price:</b> <code>${exit_price:.2f}</code>\n"
            f"• <b>Reason:</b> <code>{reason}</code>\n"
            f"• <b>Final PnL:</b> <code>${pnl:+.2f}</code>"
        )
        self.send_message(text)

    def notify_no_trade(self, candle_start: str, p_cal: float, p_uncal: float, tier: str, reason: str) -> None:
        ist_str = format_ist(candle_start)
        p_up = p_cal * 100.0
        p_dn = (1.0 - p_cal) * 100.0
        text = (
            f"⚪ <b>NO TRADE (HOLD)</b>\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• 📈 <b>UP Prob:</b> <code>{p_up:.1f}%</code> | 📉 <b>DOWN Prob:</b> <code>{p_dn:.1f}%</code>\n"
            f"• <b>Confidence Tier:</b> <code>{tier}</code>\n"
            f"• <b>Reason:</b> <code>{reason}</code>"
        )
        self.send_message(text)

    def notify_v2_trade_entry(self, candle_start: str, side: str, fill_price: float, buy_ceiling: float, tp_price: float, sl_price: float, qty: float, position_usd: float) -> None:
        ist_str = format_ist(candle_start)
        emoji = "🟢" if side == "UP" else "🔴"
        text = (
            f"🚀 <b>V2 MOMENTUM TRADE ENTERED</b>\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• <b>Prediction Side:</b> {emoji} <code>{side}</code>\n"
            f"• <b>Fill Price:</b> <code>${fill_price:.3f}</code>\n"
            f"• <b>Buy Ceiling Cap:</b> <code>${buy_ceiling:.3f}</code>\n"
            f"• 🎯 <b>Take Profit:</b> <code>${tp_price:.4f}</code>\n"
            f"• 🛑 <b>Stop Loss:</b> <code>${sl_price:.4f}</code>\n"
            f"• 💰 <b>Position Size:</b> <code>${position_usd:.2f}</code> ({qty:.2f} shares)\n"
            f"• ⚡ <b>Execution Mode:</b> <code>{config.execution_mode}</code>"
        )
        self.send_message(text)

    def notify_v2_trade_exit(self, candle_start: str, side: str, exit_price: float, reason: str, pnl: float) -> None:
        ist_str = format_ist(candle_start)
        if reason == "TAKE_PROFIT_ACHIEVED":
            header = "🎯 <b>V2 TAKE PROFIT HIT</b>"
        elif reason == "STOP_LOSS_HIT":
            header = "🛑 <b>V2 STOP LOSS TRIGGERED</b>"
        else:
            header = "⌛ <b>V2 CANDLE EXPIRED</b>"
        
        emoji = "📈" if pnl >= 0 else "📉"
        text = (
            f"{header}\n"
            f"• <b>Candle:</b> <code>{ist_str}</code>\n"
            f"• <b>Side:</b> <code>{side}</code>\n"
            f"• <b>Exit Price:</b> <code>${exit_price:.4f}</code>\n"
            f"• <b>Exit Reason:</b> <code>{reason}</code>\n"
            f"• {emoji} <b>Net PnL:</b> <code>${pnl:+.4f}</code>"
        )
        self.send_message(text)

    def notify_model_retrained(self, promoted: bool, trained_at_ist: str, win_rate: float, mc_dd: float = 0.0, ruin: float = 0.0, reason: str = "") -> None:
        if promoted:
            text = (
                f"🤖 <b>CHAMPION MODEL PROMOTED</b>\n"
                f"• <b>Status:</b> <code>PROMOTED TO CHAMPION ✅</code>\n"
                f"• <b>Trained At:</b> <code>{trained_at_ist}</code>\n"
                f"• <b>Holdout Win Rate:</b> <code>{win_rate*100.0 if win_rate <= 1.0 else win_rate:.1f}%</code>\n"
                f"• <b>Monte Carlo 99% Max DD:</b> <code>-{mc_dd:.1f}%</code>\n"
                f"• <b>Risk of Ruin:</b> <code>{ruin:.2f}% (SAFE)</code>"
            )
        else:
            text = (
                f"🤖 <b>MODEL RETRAINING EVALUATION</b>\n"
                f"• <b>Status:</b> <code>REJECTED (Gate Not Met) ⚠️</code>\n"
                f"• <b>Reason:</b> <code>{reason}</code>\n"
                f"• <b>Action:</b> <code>Existing Champion Model Retained</code>"
            )
        self.send_message(text)


