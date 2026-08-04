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
        self.bot_token = config.telegram_bot_token
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
        except Exception:
            pass
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
        from src.notifications.notifier import format_ist
        from datetime import datetime, timezone, timedelta

        active_str = "ACTIVE" if config.trading_active else "DEACTIVATED"
        pending_cnt, open_cnt, closed_cnt = self._get_position_counts()

        model_text = "• <b>Champion Model Profile:</b> <code>NOT_LOADED</code>\n"
        if os.path.exists("models/lgbm_model.pkl"):
            try:
                with open("models/lgbm_model.pkl", "rb") as f:
                    bundle = pickle.load(f)
                
                trained_at_str = bundle.get("trained_at", "")
                trained_ist = format_ist(trained_at_str) if trained_at_str else "N/A"

                # Next Retraining Calculation (7 days from trained_at)
                next_retrain_text = "N/A"
                if trained_at_str:
                    try:
                        trained_dt = datetime.strptime(trained_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        next_dt = trained_dt + timedelta(days=7)
                        next_ist = format_ist(next_dt)
                        
                        now_utc = datetime.now(timezone.utc)
                        remaining = next_dt - now_utc
                        rem_days = max(0.0, remaining.total_seconds() / 86400.0)
                        next_retrain_text = f"In {rem_days:.1f} days ({next_ist})"
                    except Exception as e:
                        logger.error(f"Error computing next retrain date: {e}")
                        next_retrain_text = "In ~7 days"

                # Current Model PnL
                curr_pnl = self._get_current_model_pnl(trained_at_str)
                curr_pnl_str = f"${curr_pnl['total_pnl']:+.2f} ({curr_pnl['wins']}W / {curr_pnl['losses']}L)"

                mc = bundle.get("monte_carlo", {})
                ruin_str = f"{mc.get('mc_prob_of_ruin', 0.0):.2f}% (SAFE)" if mc.get("is_safe", True) else f"{mc.get('mc_prob_of_ruin', 0.0):.2f}% (RISK)"

                win_rate_val = bundle.get("high_conf_win_rate") or mc.get("win_rate", 0.55)
                win_rate_pct = win_rate_val * 100.0 if win_rate_val <= 1.0 else win_rate_val

                model_text = (
                    f"🤖 <b>CURRENT CHAMPION MODEL PROFILE</b>\n"
                    f"• <b>Trained At:</b> <code>{trained_ist}</code>\n"
                    f"• <b>Model Win Rate:</b> <code>{win_rate_pct:.1f}%</code>\n"
                    f"• <b>Current Model PnL:</b> <code>{curr_pnl_str}</code>\n"
                    f"• <b>Next Retraining Due:</b> <code>{next_retrain_text}</code>\n"
                    f"• <b>Monte Carlo 99% Max DD:</b> <code>-{mc.get('mc_99th_drawdown_pct', 0.0):.1f}%</code>\n"
                    f"• <b>Risk of Ruin:</b> <code>{ruin_str}</code>\n"
                )
            except Exception as e:
                logger.error(f"Error building status model profile: {e}")
                pass

        text = (
            "📊 <b>SYSTEM ENGINE STATUS</b>\n"
            f"• <b>Engine Status:</b> <code>{active_str}</code>\n"
            f"• <b>Momentum Surge Thresh:</b> <code>+${config.v2_momentum_threshold_cents:.2f}</code>\n"
            f"• <b>Take Profit (Tier 1):</b> <code>+${config.v2_take_profit_cents:.2f}</code>\n"
            f"• <b>Stop Loss (Tier 1):</b> <code>-${config.v2_stop_loss_cents:.2f}</code>\n"
            f"• <b>High Odds Cutoff:</b> <code>${config.v2_high_odds_cutoff:.2f}</code>\n"
            f"• <b>Positions:</b> PENDING={pending_cnt} | OPEN={open_cnt} | CLOSED={closed_cnt}\n\n"
            f"{model_text}"
        )
        self._reply(chat_id, text)

    def _handle_pnl(self, chat_id: str) -> None:
        summary = self._calculate_pnl_summary()
        text = (
            "💰 <b>OVERALL LIFETIME FINANCIAL PERFORMANCE</b>\n"
            f"• <b>Total Lifetime Trades:</b> <code>{summary['total']}</code>\n"
            f"• <b>Closed Trades:</b> <code>{summary['closed']}</code>\n"
            f"• <b>Overall Win Rate:</b> <code>{summary['win_rate']:.1f}%</code> ({summary['wins']}W / {summary['losses']}L)\n"
            f"• <b>Cumulative Lifetime Net PnL:</b> <code>${summary['total_pnl']:+.2f}</code>"
        )
        self._reply(chat_id, text)

    def _get_position_counts(self) -> (int, int, int):
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT Position_Status, COUNT(*) FROM Positions GROUP BY Position_Status;")
            rows = dict(cursor.fetchall())
            conn.close()
            return rows.get("PENDING", 0), rows.get("OPEN", 0), rows.get("CLOSED", 0)
        except Exception:
            return 0, 0, 0

    def _get_current_model_pnl(self, trained_at_str: str) -> Dict[str, Any]:
        if not trained_at_str:
            return {"closed": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0}
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT Pnl FROM Positions WHERE Position_Status = 'CLOSED' AND Candle_Start >= ?;", (trained_at_str,))
            pnls = [r[0] for r in cursor.fetchall() if r[0] is not None]
            conn.close()

            closed = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            losses = sum(1 for p in pnls if p <= 0)
            total_pnl = sum(pnls)
            win_rate = (wins / closed * 100.0) if closed > 0 else 0.0

            return {
                "closed": closed,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "total_pnl": total_pnl
            }
        except Exception:
            return {"closed": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0}

    def _calculate_pnl_summary(self) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM Positions WHERE Position_Status != 'CANCELLED';")
            total = cursor.fetchone()[0] or 0

            cursor.execute("SELECT Pnl FROM Positions WHERE Position_Status = 'CLOSED';")
            pnls = [r[0] for r in cursor.fetchall() if r[0] is not None]
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
                "win_rate": win_rate,
                "total_pnl": total_pnl
            }
        except Exception:

            return {"total": 0, "closed": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0}
