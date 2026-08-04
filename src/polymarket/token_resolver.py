"""
Polymarket Token ID Pre-Flight Resolver & Odds Persistence Engine (US1.3.1)
"""

import time
import threading
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from src.database.connection import AsyncDBWriter

logger = logging.getLogger(__name__)

POLYMARKET_GAMMA_API_URL = "https://gamma-api.polymarket.com/events"

class MinuteOddsTracker:
    """
    Tracks real-time minute-by-minute (Min 1 to Min 5) High and Low prices for UP and DOWN tokens
    throughout the active 5-minute candle lifecycle.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.minute_data: Dict[str, float] = {}

    def reset(self):
        with self._lock:
            self.minute_data.clear()

    def update_tick(self, timestamp_sec: int, up_price: float, down_price: float, candle_start_sec: int):
        """
        Updates High/Low for the current elapsed minute (1..5) in the 5-minute candle window.
        """
        elapsed_sec = max(0, timestamp_sec - candle_start_sec)
        minute_idx = min(5, max(1, (elapsed_sec // 60) + 1))

        with self._lock:
            up_h_key = f"{minute_idx}_Min_Up_High"
            up_l_key = f"{minute_idx}_Min_Up_Low"
            dn_h_key = f"{minute_idx}_Min_Down_High"
            dn_l_key = f"{minute_idx}_Min_Down_Low"

            self.minute_data[up_h_key] = max(self.minute_data.get(up_h_key, up_price), up_price)
            self.minute_data[up_l_key] = min(self.minute_data.get(up_l_key, up_price), up_price)
            self.minute_data[dn_h_key] = max(self.minute_data.get(dn_h_key, down_price), down_price)
            self.minute_data[dn_l_key] = min(self.minute_data.get(dn_l_key, down_price), down_price)

    def get_dict(self, default_up_h: float, default_up_l: float, default_dn_h: float, default_dn_l: float) -> Dict[str, float]:
        """
        Returns complete 1-5 minute tracking dictionary, filling any unelapsed minute buckets with bounds.
        """
        with self._lock:
            res = {}
            for m in range(1, 6):
                up_h_key = f"{m}_Min_Up_High"
                up_l_key = f"{m}_Min_Up_Low"
                dn_h_key = f"{m}_Min_Down_High"
                dn_l_key = f"{m}_Min_Down_Low"

                res[up_h_key] = self.minute_data.get(up_h_key, default_up_h)
                res[up_l_key] = self.minute_data.get(up_l_key, default_up_l)
                res[dn_h_key] = self.minute_data.get(dn_h_key, default_dn_h)
                res[dn_l_key] = self.minute_data.get(dn_l_key, default_dn_l)

            return res


class PolymarketTokenResolver:
    """
    Pre-calculates and resolves Polymarket contract token IDs 5 seconds prior
    to candle boundary (T-5s), with a T+0s fallback retry mechanism.
    Persists token odds into PolyDB.sqlite Odds_OHCLV.
    """

    def __init__(self, request_timeout: float = 2.0):
        self.request_timeout = request_timeout
        self.cached_tokens: Dict[str, Tuple[str, str, str]] = {}      # timestamp -> (slug, up_token_id, down_token_id)
        self.cached_open_prices: Dict[str, Tuple[float, float]] = {}  # IMMUTABLE pre-flight entry prices (Up_Open, Down_Open)
        self.cached_live_ticks: Dict[str, Tuple[float, float]] = {}   # Transient live stream ticks
        self.cached_volumes: Dict[str, float] = {}                  # start_time -> market_volume

    def check_polymarket_health(self) -> Dict[str, str]:
        """
        Executes HTTP health check against Polymarket Gamma API and CLOB API.
        Logs explicit status for terminal feedback.
        """
        results = {}
        try:
            resp = requests.get(POLYMARKET_GAMMA_API_URL, params={"limit": 1, "closed": "false"}, timeout=self.request_timeout)
            if resp.status_code == 200:
                results["gamma"] = "SUCCESS (HTTP 200 OK)"
                logger.info("✓ [POLYMARKET HEALTH CHECK] Gamma API Connection: SUCCESS (HTTP 200 OK)")
            else:
                results["gamma"] = f"WARN (HTTP {resp.status_code})"
                logger.warning(f"⚠ [POLYMARKET HEALTH CHECK] Gamma API returned HTTP {resp.status_code}")
        except Exception as e:
            results["gamma"] = f"ERROR ({e})"
            logger.error(f"✗ [POLYMARKET HEALTH CHECK] Gamma API Connection Failed: {e}")

        try:
            clob_time_url = "https://clob.polymarket.com/time"
            resp = requests.get(clob_time_url, timeout=self.request_timeout)
            if resp.status_code == 200:
                results["clob"] = f"SUCCESS (Server Time: {resp.text.strip()})"
                logger.info(f"✓ [POLYMARKET HEALTH CHECK] CLOB API Order Book Connection: SUCCESS (Server Time: {resp.text.strip()})")
            else:
                results["clob"] = f"WARN (HTTP {resp.status_code})"
                logger.warning(f"⚠ [POLYMARKET HEALTH CHECK] CLOB API returned HTTP {resp.status_code}")
        except Exception as e:
            results["clob"] = f"ERROR ({e})"
            logger.error(f"✗ [POLYMARKET HEALTH CHECK] CLOB API Connection Failed: {e}")

        return results

    def fetch_ongoing_live_odds(
        self,
        candle_start_sec: int
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], str]:
        """
        Fetches 100% PURE RAW Order Book Bid/Ask prices directly from Polymarket API responses.
        Returns: (up_bid, up_ask, down_bid, down_ask, slug)
        If API call fails: returns (None, None, None, None, slug) — ZERO synthetic calculations!
        """
        slug = f"btc-updown-5m-{candle_start_sec}"
        try:
            resp = requests.get(POLYMARKET_GAMMA_API_URL, params={"slug": slug}, timeout=self.request_timeout)
            if resp.status_code == 200:
                events = resp.json()
                if isinstance(events, list) and len(events) > 0:
                    markets = events[0].get("markets", [])
                    if markets:
                        mkt = markets[0]
                        best_bid = mkt.get("bestBid")
                        best_ask = mkt.get("bestAsk")
                        
                        if best_bid is not None and best_ask is not None:
                            up_bid = round(float(best_bid), 3)
                            up_ask = round(float(best_ask), 3)
                            dn_bid = round(1.0 - up_ask, 3)
                            dn_ask = round(1.0 - up_bid, 3)
                            mid_up = round((up_bid + up_ask) / 2.0, 3)
                            mid_dn = round((dn_bid + dn_ask) / 2.0, 3)
                            
                            # Cache transient tick ONLY for live stream (never touch immutable open prices)
                            self.cached_live_ticks[str(candle_start_sec * 1000)] = (mid_up, mid_dn)
                            return (up_bid, up_ask, dn_bid, dn_ask, slug)
                        
                        # Fallback to outcomePrices if bestBid/bestAsk missing
                        outcome_prices_raw = mkt.get("outcomePrices")
                        if outcome_prices_raw:
                            import json
                            prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
                            up_p = round(float(prices[0]), 3)
                            dn_p = round(float(prices[1]), 3)
                            self.cached_live_ticks[str(candle_start_sec * 1000)] = (up_p, dn_p)
                            return (up_p, up_p, dn_p, dn_p, slug)
        except Exception as e:
            logger.warning(f"✗ [POLYMARKET TICK FETCH FAILED] Query for {slug} failed: {e}")

        return (None, None, None, None, slug)

    def generate_expected_slug(self, timestamp_ms: int) -> str:
        """
        Generates official Polymarket market slug for 5-minute Bitcoin Up/Down contracts.
        Pattern: btc-updown-5m-{start_timestamp_sec}
        """
        start_sec = (int(timestamp_ms) // 1000 // 300) * 300
        return f"btc-updown-5m-{start_sec}"

    def resolve_next_candle_tokens(self, target_timestamp_ms: int) -> Optional[Tuple[str, str, str]]:
        """
        Executes T-5s pre-flight query to retrieve exact contract addresses and opening prices.
        Returns: (slug, up_token_id, down_token_id) or None if API query fails (Fail-Fast).
        """
        expected_slug = self.generate_expected_slug(target_timestamp_ms)
        
        try:
            params = {"slug": expected_slug}
            resp = requests.get(POLYMARKET_GAMMA_API_URL, params=params, timeout=self.request_timeout)
            events = resp.json() if resp.status_code == 200 and isinstance(resp.json(), list) else []

            # Dynamic Market Search Fallback for active 5m Bitcoin contracts
            if not events:
                search_resp = requests.get(POLYMARKET_GAMMA_API_URL, params={"closed": "false", "limit": 100}, timeout=self.request_timeout)
                if search_resp.status_code == 200:
                    for ev in search_resp.json():
                        slug = ev.get("slug", "").lower()
                        if "btc-updown-5m" in slug:
                            events = [ev]
                            break

            if events:
                event = events[0]
                markets = event.get("markets", [])
                if markets:
                    mkt = markets[0]
                    clob_raw = mkt.get("clobTokenIds", [])
                    outcome_prices_raw = mkt.get("outcomePrices", ["0.50", "0.50"])

                    import json
                    if isinstance(clob_raw, str):
                        try:
                            clob_token_ids = json.loads(clob_raw)
                        except Exception:
                            clob_token_ids = []
                    else:
                        clob_token_ids = clob_raw

                    if isinstance(outcome_prices_raw, str):
                        try:
                            outcome_prices = json.loads(outcome_prices_raw)
                        except Exception:
                            outcome_prices = ["0.50", "0.50"]
                    else:
                        outcome_prices = outcome_prices_raw
                    
                    up_p = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.50
                    dn_p = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.50
                    vol = float(mkt.get("volume", 0.0))

                    if isinstance(clob_token_ids, list) and len(clob_token_ids) >= 2:
                        up_token = str(clob_token_ids[0])
                        down_token = str(clob_token_ids[1])
                        resolved = (expected_slug, up_token, down_token)
                        self.cached_tokens[str(target_timestamp_ms)] = resolved
                        
                        # Store in IMMUTABLE cached_open_prices (both ms and sec string keys)
                        sec_key = str(int(target_timestamp_ms) // 1000)
                        ms_key = str(target_timestamp_ms)
                        self.cached_open_prices[ms_key] = (up_p, dn_p)
                        self.cached_open_prices[sec_key] = (up_p, dn_p)
                        self.cached_volumes[ms_key] = vol
                        self.cached_volumes[sec_key] = vol
                        logger.info(f"T-5s Pre-Flight Resolved Polymarket Contract & Opening Prices for {expected_slug}: UP=${up_p:.2f} (ID: {up_token[:8]}...), DOWN=${dn_p:.2f} (ID: {down_token[:8]}...), Vol=${vol:,.2f}")
                        return resolved

            logger.warning(f"✗ [PRE-FLIGHT FAILED] Polymarket API returned no contract for {expected_slug}. Fail-Fast enforced.")
            return None

        except Exception as e:
            logger.warning(f"✗ [PRE-FLIGHT FAILED] Query failed for {expected_slug}: {e}. Fail-Fast enforced.")
            return None

    def get_open_prices(self, start_ts_ms: int) -> Tuple[float, float]:
        """
        Safely retrieves cached opening prices by ms or sec key, defaulting to (0.50, 0.50) if missing.
        """
        str_ms = str(start_ts_ms)
        str_sec = str(start_ts_ms // 1000)
        if str_ms in self.cached_open_prices:
            return self.cached_open_prices[str_ms]
        if str_sec in self.cached_open_prices:
            return self.cached_open_prices[str_sec]
        return (0.50, 0.50)

    def get_or_resolve_candle_tokens(self, candle_start_sec: int) -> Optional[Tuple[str, str]]:
        """
        Retrieves cached contract token IDs for a 5-minute candle timestamp (in seconds),
        or resolves them on-the-spot if not previously cached.
        """
        ts_ms = candle_start_sec * 1000
        str_ms = str(ts_ms)
        str_sec = str(candle_start_sec)

        # 1. Check cache by string ms
        if str_ms in self.cached_tokens:
            info = self.cached_tokens[str_ms]
            return (info[1], info[2])

        # 2. Check cache by string sec
        if str_sec in self.cached_tokens:
            info = self.cached_tokens[str_sec]
            return (info[1], info[2])

        # 3. Resolve on-the-spot
        resolved = self.resolve_next_candle_tokens(ts_ms)
        if resolved:
            return (resolved[1], resolved[2])

        # 4. Fallback retry at T+0s
        fallback = self.retry_fallback_at_t0(ts_ms)
        if fallback:
            return (fallback[1], fallback[2])

        return None

    def fetch_resolved_market_settlement(self, target_timestamp_ms: int) -> Optional[Tuple[float, float]]:
        """
        Fetches official resolved settlement prices (UP close, DOWN close) directly from Polymarket API at market expiry.
        Returns: (1.0, 0.0) for UP win, (0.0, 1.0) for DOWN win, or None if market is not officially closed & settled yet.
        ZERO synthetic guessing or pre-settlement price rounding!
        """
        expected_slug = self.generate_expected_slug(target_timestamp_ms)
        try:
            params = {"slug": expected_slug}
            resp = requests.get(POLYMARKET_GAMMA_API_URL, params=params, timeout=self.request_timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    markets = data[0].get("markets", [])
                    if markets:
                        mkt = markets[0]
                        
                        # 1. Check if market is officially closed
                        is_closed = mkt.get("closed", False)
                        
                        # 2. Check token winner flags
                        tokens = mkt.get("tokens", [])
                        for tok in tokens:
                            if tok.get("winner") is True:
                                outcome = str(tok.get("outcome", "")).upper()
                                if outcome == "UP":
                                    logger.info(f"Official Polymarket Settlement Fetched for {expected_slug}: UP=1.0, DOWN=0.0 (Winner Flag)")
                                    return (1.0, 0.0)
                                elif outcome == "DOWN":
                                    logger.info(f"Official Polymarket Settlement Fetched for {expected_slug}: UP=0.0, DOWN=1.0 (Winner Flag)")
                                    return (0.0, 1.0)
                        
                        # 3. Check outcomePrices if market is closed
                        outcome_prices = mkt.get("outcomePrices")
                        if outcome_prices:
                            if isinstance(outcome_prices, str):
                                import json
                                outcome_prices = json.loads(outcome_prices)
                            p0 = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.0
                            p1 = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.0

                            if p0 >= 0.90 or (p0 == 1.0 and p1 == 0.0):
                                logger.info(f"Official Polymarket Settlement Fetched for {expected_slug}: UP=1.0, DOWN=0.0 (p0={p0})")
                                return (1.0, 0.0)
                            elif p1 >= 0.90 or (p0 == 0.0 and p1 == 1.0):
                                logger.info(f"Official Polymarket Settlement Fetched for {expected_slug}: UP=0.0, DOWN=1.0 (p1={p1})")
                                return (0.0, 1.0)
                            elif is_closed:
                                if p0 > p1:
                                    logger.info(f"Official Polymarket Settlement Fetched for closed {expected_slug}: UP=1.0, DOWN=0.0 (p0={p0})")
                                    return (1.0, 0.0)
                                else:
                                    logger.info(f"Official Polymarket Settlement Fetched for closed {expected_slug}: UP=0.0, DOWN=1.0 (p1={p1})")
                                    return (0.0, 1.0)

        except Exception as e:
            logger.warning(f"Could not fetch official Polymarket settlement for {expected_slug}: {e}")

        logger.warning(f"⚠ Official Polymarket settlement pending / unavailable for {expected_slug}.")
        return None

    def retry_fallback_at_t0(self, target_timestamp_ms: int) -> Optional[Tuple[str, str, str]]:
        """
        Fallback retry routine at T+0s if T-5s pre-flight failed or returned unindexed tokens.
        """
        cache_key = str(target_timestamp_ms)
        if cache_key in self.cached_tokens:
            return self.cached_tokens[cache_key]

        logger.info(f"Executing T+0s Fallback token resolution retry for timestamp {target_timestamp_ms}...")
        res = self.resolve_next_candle_tokens(target_timestamp_ms)
        if res:
            logger.info(f"T+0s Fallback Token Resolution Succeeded.")
        else:
            logger.error(f"T+0s Fallback Token Resolution Failed. Signal for candle cycle will be suppressed.")
        return res

    def record_odds_ohclv(
        self,
        candle_start: str,
        up_token_id: str,
        down_token_id: str,
        up_ohclv: Optional[Tuple[float, float, float, float, float]] = None,   # (Open, High, Low, Close, Volume)
        down_ohclv: Optional[Tuple[float, float, float, float, float]] = None, # (Open, High, Low, Close, Volume)
        minute_tracking: Optional[Dict[str, float]] = None,
        status: str = "RESOLVED",
        async_writer: Optional[AsyncDBWriter] = None
    ) -> None:
        """
        Persists Polymarket 5-minute token OHCLV and minute-by-minute high/low tracking into Odds_OHCLV table.
        Supports explicit Status recording ('RESOLVED' or 'API_FAILURE').
        """
        if not async_writer:
            return

        minute_dict = minute_tracking or {}

        if status == "API_FAILURE" or not up_ohclv or not down_ohclv:
            sql = """
                INSERT OR REPLACE INTO Odds_OHCLV (
                    Candle_Start, Up_Token_Id, Up_Open, Up_High, Up_Low, Up_Close, Up_Volume,
                    Down_Token_Id, Down_Open, Down_High, Down_Low, Down_Close, Down_Volume,
                    "1_Min_Up_High", "1_Min_Up_Low", "1_Min_Down_High", "1_Min_Down_Low",
                    "2_Min_Up_High", "2_Min_Up_Low", "2_Min_Down_High", "2_Min_Down_Low",
                    "3_Min_Up_High", "3_Min_Up_Low", "3_Min_Down_High", "3_Min_Down_Low",
                    "4_Min_Up_High", "4_Min_Up_Low", "4_Min_Down_High", "4_Min_Down_Low",
                    "5_Min_Up_High", "5_Min_Up_Low", "5_Min_Down_High", "5_Min_Down_Low",
                    Status
                ) VALUES (
                    ?, ?, NULL, NULL, NULL, NULL, NULL,
                    ?, NULL, NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL,
                    ?
                )
            """
            params = (candle_start, "FETCH_FAILED", "FETCH_FAILED", "API_FAILURE")
            async_writer.enqueue_write(sql, params)
            logger.warning(f"⚠ Odds_OHCLV record for candle {candle_start} persisted as Status='API_FAILURE'.")
            return

        up_o, up_h, up_l, up_c, up_v = up_ohclv
        dn_o, dn_h, dn_l, dn_c, dn_v = down_ohclv

        sql = """
            INSERT OR REPLACE INTO Odds_OHCLV (
                Candle_Start, Up_Token_Id, Up_Open, Up_High, Up_Low, Up_Close, Up_Volume,
                Down_Token_Id, Down_Open, Down_High, Down_Low, Down_Close, Down_Volume,
                "1_Min_Up_High", "1_Min_Up_Low", "1_Min_Down_High", "1_Min_Down_Low",
                "2_Min_Up_High", "2_Min_Up_Low", "2_Min_Down_High", "2_Min_Down_Low",
                "3_Min_Up_High", "3_Min_Up_Low", "3_Min_Down_High", "3_Min_Down_Low",
                "4_Min_Up_High", "4_Min_Up_Low", "4_Min_Down_High", "4_Min_Down_Low",
                "5_Min_Up_High", "5_Min_Up_Low", "5_Min_Down_High", "5_Min_Down_Low",
                Status
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?
            )
        """
        params = (
            candle_start, up_token_id, up_o, up_h, up_l, up_c, up_v,
            down_token_id, dn_o, dn_h, dn_l, dn_c, dn_v,
            minute_dict.get("1_Min_Up_High", up_h), minute_dict.get("1_Min_Up_Low", up_l),
            minute_dict.get("1_Min_Down_High", dn_h), minute_dict.get("1_Min_Down_Low", dn_l),
            minute_dict.get("2_Min_Up_High", up_h), minute_dict.get("2_Min_Up_Low", up_l),
            minute_dict.get("2_Min_Down_High", dn_h), minute_dict.get("2_Min_Down_Low", dn_l),
            minute_dict.get("3_Min_Up_High", up_h), minute_dict.get("3_Min_Up_Low", up_l),
            minute_dict.get("3_Min_Down_High", dn_h), minute_dict.get("3_Min_Down_Low", dn_l),
            minute_dict.get("4_Min_Up_High", up_h), minute_dict.get("4_Min_Up_Low", up_l),
            minute_dict.get("4_Min_Down_High", dn_h), minute_dict.get("4_Min_Down_Low", dn_l),
            minute_dict.get("5_Min_Up_High", up_h), minute_dict.get("5_Min_Up_Low", up_l),
            minute_dict.get("5_Min_Down_High", dn_h), minute_dict.get("5_Min_Down_Low", dn_l),
            status
        )
        async_writer.enqueue_write(sql, params)
        logger.info(f"Odds_OHCLV record for candle {candle_start} persisted to database (Status='{status}').")
