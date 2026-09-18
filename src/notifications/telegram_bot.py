"""
Telegram Slash Commands Parser & Remote Command Router (Sprint 3: US4.3)
Processes slash commands (/start, /activate, /deactivate, /status, /pnl, /dryrun, /help)
with user ID authorization middleware to control and monitor the bot remotely.
"""

import os
import time
import pickle
import sqlite3
import logging
import threading
import urllib.request
import urllib.parse
import json
from typing import Optional, Dict, Any, List

from src.config import config
from src.notifications.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

class TelegramCommandRouter:
    """
    Remote Telegram Command Router.
    Executes long-polling loop against Telegram Bot API to parse user commands,
    enforces authorization checks, and executes system commands remotely.
    """

    def __init__(self, notifier: TelegramNotifier, db_path: str = "PolyDB.sqlite"):
        self.notifier = notifier
        self.db_path = db_path
        raw_token = getattr(config, "telegram_bot_token", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.bot_token = str(raw_token).strip("\"' ") if raw_token else ""
        self.running = False
        self.last_update_id = 0
        self.poll_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """
        Starts long-polling worker thread for Telegram slash commands.
        """
        if not self.bot_token:
            logger.info("Telegram Command Router disabled (TELEGRAM_BOT_TOKEN not configured).")
            return

        self.running = True
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="TelegramCommandRouterPoll")
        self.poll_thread.start()
        logger.info("✓ [TELEGRAM COMMAND ROUTER] Long-polling worker started successfully.")

    def stop(self) -> None:
        """
        Stops long-polling worker thread.
        """
        self.running = False

    def _poll_loop(self) -> None:
        while self.running:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._process_update(update)
                time.sleep(1.0)
            except Exception as e:
                logger.error(f"Error in TelegramCommandRouter poll loop: {e}")
                time.sleep(3.0)

    def _get_updates(self) -> List[Dict[str, Any]]:
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={self.last_update_id + 1}&timeout=2"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PolymarketBot/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("ok"):
                        return data.get("result", [])
        except Exception as e:
            logger.debug(f"[TELEGRAM POLL] Notice fetching updates: {e}")
        return []

    def _is_authorized(self, user_id: int) -> bool:
        auth_users = config.telegram_authorized_user_ids
        if not auth_users:
            return True  # If whitelist empty, allow caller
        return user_id in auth_users

    def _process_update(self, update: Dict[str, Any]) -> None:
        update_id = update.get("update_id", 0)
        if update_id > self.last_update_id:
            self.last_update_id = update_id

        msg = update.get("message")
        if not msg:
            return

        user_id = msg.get("from", {}).get("id", 0)
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        if not text.startswith("/"):
            return

        logger.info(f"📱 Telegram Command Received: Text='{text}' From User={user_id}")

        # Check authorization middleware
        if not self._is_authorized(user_id):
            logger.warning(f"⚠ Unauthorized command attempt from user ID {user_id}: '{text}'")
            self._reply(chat_id, "❌ <b>Unauthorized User ID.</b> Command rejected.")
            return

        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        logger.info(f"📱 Telegram Command Received from User ID {user_id}: '{text}'")

        if cmd in ("/start", "/help"):
            self._handle_help(chat_id)
        elif cmd == "/activate":
            self._handle_activate(chat_id)
        elif cmd == "/deactivate":
            self._handle_deactivate(chat_id)
        elif cmd == "/dryrun":
            self._handle_dryrun(chat_id, args)
        elif cmd == "/status":
            self._handle_status(chat_id)
        elif cmd == "/pnl":
            self._handle_pnl(chat_id)
        else:
            self._reply(chat_id, f"❓ Unknown command <code>{cmd}</code>. Type /help for available commands.")

    def _reply(self, chat_id: str, html_text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": html_text,
            "parse_mode": "HTML"
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "PolymarketBot/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                pass
        except Exception as e:
            logger.error(f"Failed to reply to Telegram command: {e}")

    # Command Handlers

    def _handle_help(self, chat_id: str) -> None:
        text = (
            "🤖 <b>Polymarket BTC-5m Prediction Bot Controls</b>\n\n"
            "• <code>/status</code> — System status, active mode, & position counts\n"
            "• <code>/pnl</code> — Total trades, win rate %, & financial PnL summary\n"
            "• <code>/activate</code> — Activate trading engine signal generation\n"
            "• <code>/deactivate</code> — Deactivate trading engine signal generation\n"
            "• <code>/dryrun on</code> — Switch execution mode to DRY_RUN (Simulation)\n"
            "• <code>/dryrun off</code> — Switch execution mode to LIVE\n"
            "• <code>/help</code> — Show this command menu"
        )
        self._reply(chat_id, text)

    def _handle_activate(self, chat_id: str) -> None:
        config.set_trading_active(True)
        self._reply(chat_id, "✅ <b>Trading Engine ACTIVATED.</b> Signal evaluation is live.")

    def _handle_deactivate(self, chat_id: str) -> None:
        config.set_trading_active(False)
        self._reply(chat_id, "⏹ <b>Trading Engine DEACTIVATED.</b> Order generation suppressed.")

    def _handle_dryrun(self, chat_id: str, args: List[str]) -> None:
        if not args:
            self._reply(chat_id, f"Current Execution Mode: <code>{config.execution_mode}</code>\nUsage: <code>/dryrun on</code> or <code>/dryrun off</code>")
            return

        sub = args[0].lower()
        if sub in ("on", "true", "1"):
            config.set_execution_mode("DRY_RUN")
            self._reply(chat_id, "⚙ Mode updated: <code>DRY_RUN</code> (Simulation Active).")
        elif sub in ("off", "false", "0"):
            config.set_execution_mode("LIVE")
            self._reply(chat_id, "⚡ Mode updated: <code>LIVE</code> (Real Execution Mode Active).")
        else:
            self._reply(chat_id, "Usage: <code>/dryrun on</code> or <code>/dryrun off</code>")

    def _handle_status(self, chat_id: str) -> None:
        active_str = "ACTIVE" if config.trading_active else "DEACTIVATED"
        pending_cnt, open_cnt, closed_cnt = self._get_position_counts()
        summary = self._calculate_pnl_summary()
        pnl_emoji = "🟢" if summary["total_pnl"] >= 0 else "🔴"

        text = (
            "📊 <b>POLYMARKET BOT V3 ENGINE STATUS</b>\n"
            f"• <b>Engine State:</b> <code>{active_str}</code>\n"
            f"• <b>Execution Mode:</b> <code>{config.execution_mode}</code>\n"
            f"• <b>Momentum Trigger:</b> <code>+${config.v2_momentum_threshold_cents:.2f} in {config.v2_momentum_window_sec:.0f}s</code>\n"
            f"• <b>Odds Entry Range:</b> <code>${config.v2_min_entry_odds_floor:.2f} – ${config.v2_max_entry_odds_ceiling:.2f}</code>\n"
            f"• <b>Maker Limit Offset:</b> <code>-${config.v3_maker_offset_cents:.2f} (0% Maker Fee)</code>\n"
            f"• <b>Take Profit Target:</b> <code>+${config.v2_take_profit_cents:.2f}</code>\n"
            f"• <b>Trailing Stop Loss:</b> <code>-${config.v2_trailing_sl_distance_cents:.2f} (from Peak)</code>\n"
            f"• <b>Max Position Size:</b> <code>${config.max_position_size_usd:.2f}</code>\n"
            f"• <b>Active Positions:</b> <code>PENDING={pending_cnt} | OPEN={open_cnt} | CLOSED={closed_cnt}</code>\n\n"
            "💰 <b>LIFETIME PERFORMANCE</b>\n"
            f"• <b>Total Trades:</b> <code>{summary['total']}</code> (Closed: {summary['closed']})\n"
            f"• <b>Win Rate:</b> <code>{summary['win_rate']:.1f}%</code> ({summary['wins']}W / {summary['losses']}L)\n"
            f"• <b>Lifetime Net PnL:</b> {pnl_emoji} <code>${summary['total_pnl']:+.2f}</code>"
        )
        self._reply(chat_id, text)

    def _handle_pnl(self, chat_id: str) -> None:
        summary = self._calculate_pnl_summary()
        pnl_emoji = "🟢" if summary["total_pnl"] >= 0 else "🔴"
        text = (
            "💰 <b>POLYMARKET BOT V3 FINANCIAL PERFORMANCE</b>\n"
            f"• <b>Total Lifetime Trades:</b> <code>{summary['total']}</code>\n"
            f"• <b>Closed Trades:</b> <code>{summary['closed']}</code>\n"
            f"• <b>Overall Win Rate:</b> <code>{summary['win_rate']:.1f}%</code> ({summary['wins']}W / {summary['losses']}L)\n"
            f"• <b>Cumulative Lifetime Net PnL:</b> {pnl_emoji} <code>${summary['total_pnl']:+.2f}</code>"
        )
        self._reply(chat_id, text)

    def _get_position_counts(self) -> (int, int, int):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT Position_Status, COUNT(*) FROM Positions GROUP BY Position_Status;")
            rows = dict(cursor.fetchall())
            conn.close()
            pending = rows.get("PENDING_FILL", 0) + rows.get("PENDING", 0)
            open_pos = rows.get("OPEN", 0) + rows.get("PARTIALLY_CLOSED", 0) + rows.get("CLOSING", 0)
            closed = rows.get("CLOSED", 0)
            return pending, open_pos, closed
        except Exception:
            return 0, 0, 0

    def _calculate_pnl_summary(self) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Positions WHERE Position_Status NOT IN ('CANCELLED');")
            total_row = cursor.fetchone()
            total = total_row[0] if total_row and total_row[0] is not None else 0

            cursor.execute("SELECT Pnl FROM Positions WHERE Position_Status = 'CLOSED';")
            pnls = []
            for r in cursor.fetchall():
                if r[0] is not None and str(r[0]).lower() != "null":
                    try:
                        pnls.append(float(r[0]))
                    except Exception:
                        pass
            conn.close()

            closed = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            losses = sum(1 for p in pnls if p <= 0)
            total_pnl = sum(pnls)
            win_rate = (wins / closed * 100.0) if closed > 0 else 0.0

            return {
                "total": total,
                "closed": closed,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 1),
                "total_pnl": round(total_pnl, 4)
            }
        except Exception as e:
            logger.error(f"Error calculating PnL summary: {e}")
            return {"total": 0, "closed": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0}
