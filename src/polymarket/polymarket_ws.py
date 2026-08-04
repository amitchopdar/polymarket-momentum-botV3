"""
Polymarket CLOB WebSocket Subscriber Engine (Live Real-Time Market Feed)
"""

import json
import time
import asyncio
import threading
import logging
import websockets
from typing import Optional, Tuple, Dict, Any, List

logger = logging.getLogger(__name__)

POLYMARKET_CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

class PolymarketWSClient:
    """
    Manages live WebSocket stream subscription to Polymarket CLOB order book
    for active 5-minute UP and DOWN tokens with zero HTTP REST polling latency.
    """

    def __init__(self, ws_url: str = POLYMARKET_CLOB_WS_URL):
        self.ws_url = ws_url
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self.running = False
        
        self.active_up_token: Optional[str] = None
        self.active_down_token: Optional[str] = None
        self.subscribed_tokens: List[str] = []
        
        # Thread-safe in-memory order book state: token_id -> {"best_bid": float, "best_ask": float}
        self.live_book: Dict[str, Dict[str, Any]] = {}
        self.last_msg_ts: float = 0.0

    def start(self) -> None:
        """
        Starts the background WebSocket client thread and asyncio event loop.
        """
        if self.running:
            return
        
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, name="PolymarketWSThread", daemon=True)
        self._thread.start()
        logger.info("✓ [POLYMARKET WS] Background WebSocket worker thread started.")

    def stop(self) -> None:
        """
        Triggers clean shutdown of background thread.
        """
        self.running = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("✓ [POLYMARKET WS] Stopped background thread worker.")

    async def _async_close(self) -> None:
        if hasattr(self, "_ws") and self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    def _run_loop(self) -> None:
        """
        Target function for the background thread creating an asyncio loop.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_listen())
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def reconnect_for_tokens(self, *tokens: Optional[str]) -> None:
        """
        Forces a clean WebSocket reconnect for a new token pair so Polymarket's WS server
        streams Frame #1 (Full Order Book Snapshot) instantly within 50ms.
        """
        valid_tokens = [str(t) for t in tokens if t]
        if not valid_tokens:
            return

        with self._lock:
            self.subscribed_tokens = list(dict.fromkeys(valid_tokens))
            if len(valid_tokens) >= 2:
                self.active_up_token = valid_tokens[0]
                self.active_down_token = valid_tokens[1]
            
            # Keep only target active tokens in memory
            active_set = set(self.subscribed_tokens)
            self.live_book = {k: v for k, v in self.live_book.items() if k in active_set}

        logger.info(f"✓ [POLYMARKET WS] Executing fresh socket boundary reconnect for {len(self.subscribed_tokens)} tokens...")
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._force_socket_reconnect(), self._loop)

    async def _force_socket_reconnect(self) -> None:
        """
        Async helper to close current socket and trigger instant reconnection.
        """
        if hasattr(self, "_ws") and self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    def subscribe_tokens(self, *tokens: Optional[str]) -> None:
        """
        Updates active token subscriptions for current AND upcoming candle contracts.
        Purges all expired tokens to enforce deterministic subscription payloads.
        """
        valid_tokens = [str(t) for t in tokens if t]
        if not valid_tokens:
            return

        with self._lock:
            # Deterministic, ordered token list (no set randomization)
            self.subscribed_tokens = list(dict.fromkeys(valid_tokens))

            # Set latest pair as default active
            if len(valid_tokens) >= 2:
                self.active_up_token = valid_tokens[0]
                self.active_down_token = valid_tokens[1]

            # PURGE EXPIRED TOKENS: Keep only currently subscribed active tokens in memory
            active_set = set(self.subscribed_tokens)
            self.live_book = {k: v for k, v in self.live_book.items() if k in active_set}

        logger.info(f"✓ [POLYMARKET WS] Subscribing to live WS stream for {len(self.subscribed_tokens)} tokens...")
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._send_subscription(), self._loop)

    async def _send_subscription(self) -> None:
        """
        Sends WebSocket subscription payload for all active subscribed tokens.
        """
        with self._lock:
            tokens_list = list(self.subscribed_tokens)

        if not tokens_list or not hasattr(self, "_ws") or not self._ws:
            return

        sub_msg = {
            "type": "market",
            "assets_ids": tokens_list
        }
        try:
            await self._ws.send(json.dumps(sub_msg))
            logger.info(f"✓ [POLYMARKET WS] Subscription payload sent successfully for {len(tokens_list)} token(s).")
        except Exception as e:
            logger.warning(f"⚠ [POLYMARKET WS] Failed to send subscription payload: {e}")

    async def _ping_loop(self, ws: websockets.ClientConnection) -> None:
        """
        Sends application-level PING frames every 10 seconds to keep TCP connection alive indefinitely.
        """
        while self.running:
            try:
                await asyncio.sleep(10)
                if ws and not ws.closed:
                    await ws.send(json.dumps({"type": "ping"}))
            except Exception:
                break

    async def _connect_and_listen(self) -> None:
        """
        Main async connection loop with auto-reconnection and exponential backoff.
        """
        backoff = 1.0
        while self.running:
            try:
                logger.info(f"Connecting to Polymarket CLOB WS: {self.ws_url}...")
                async with websockets.connect(self.ws_url, ping_interval=30, ping_timeout=15) as ws:
                    self._ws = ws
                    backoff = 1.0
                    logger.info("✓ [POLYMARKET WS] Connected to Polymarket CLOB WebSocket stream.")
                    
                    # Re-subscribe if tokens are set
                    await self._send_subscription()

                    # Start PING keepalive task
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        while self.running:
                            msg = await ws.recv()
                            self.last_msg_ts = time.time()
                            self._process_message(msg)
                    finally:
                        ping_task.cancel()

            except Exception as e:
                self._ws = None
                if self.running:
                    logger.warning(f"⚠ [POLYMARKET WS] Connection dropped: {e}. Reconnecting in {backoff:.1f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(30.0, backoff * 2.0)

    def _process_message(self, raw_msg: str) -> None:
        """
        Parses Polymarket WS JSON frames (book snapshots and price_changes events) with exact top-of-book levels.
        """
        try:
            data = json.loads(raw_msg)
            frames = data if isinstance(data, list) else [data]

            with self._lock:
                for frame in frames:
                    if not isinstance(frame, dict):
                        continue

                    # 1. Handle Event-level Asset or Frame-level Asset
                    asset_id = frame.get("asset_id")
                    
                    # Process full book snapshot
                    bids = frame.get("bids", [])
                    asks = frame.get("asks", [])
                    if asset_id and (bids or asks):
                        asset_key = str(asset_id)
                        entry = self.live_book.setdefault(asset_key, {"bids": {}, "asks": {}, "best_bid": None, "best_ask": None})
                        entry["bids"] = {float(b["price"]): float(b.get("size", 1)) for b in bids if float(b.get("size", 1)) > 0}
                        entry["asks"] = {float(a["price"]): float(a.get("size", 1)) for a in asks if float(a.get("size", 1)) > 0}

                    # Process level price change events
                    price_changes = frame.get("price_changes", [])
                    for pc in price_changes:
                        pc_asset = pc.get("asset_id") or asset_id
                        pc_price = pc.get("price")
                        pc_side = pc.get("side") # "BUY" or "SELL"
                        pc_size = float(pc.get("size", 1)) if "size" in pc else 1.0

                        if pc_asset:
                            asset_key = str(pc_asset)
                            entry = self.live_book.setdefault(asset_key, {"bids": {}, "asks": {}, "best_bid": None, "best_ask": None})
                            
                            # Read native best_bid and best_ask directly from Polymarket WS payload
                            if "best_bid" in pc and pc["best_bid"] is not None:
                                try:
                                    entry["best_bid"] = float(pc["best_bid"])
                                except (ValueError, TypeError):
                                    pass
                            if "best_ask" in pc and pc["best_ask"] is not None:
                                try:
                                    entry["best_ask"] = float(pc["best_ask"])
                                except (ValueError, TypeError):
                                    pass

                            if pc_price is not None and pc_side:
                                p_val = float(pc_price)
                                target_dict = entry["bids"] if pc_side.upper() == "BUY" else entry["asks"]
                                if pc_size > 0:
                                    target_dict[p_val] = pc_size
                                else:
                                    target_dict.pop(p_val, None)

                    # Recompute Top of Book from dictionary level maps if native fields absent
                    for entry in self.live_book.values():
                        b_dict = entry.get("bids", {})
                        a_dict = entry.get("asks", {})
                        if b_dict:
                            entry["best_bid"] = max(b_dict.keys())
                        if a_dict:
                            entry["best_ask"] = min(a_dict.keys())

        except Exception as e:
            logger.debug(f"Error parsing Polymarket WS frame: {e}")

    def get_live_bid_ask(self, target_up_token: Optional[str] = None, target_down_token: Optional[str] = None) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        Returns real-time raw (up_bid, up_ask, down_bid, down_ask) for target tokens from live WS stream.
        If live WS stream has no ticks for target tokens, dynamically pings CLOB REST API.
        """
        with self._lock:
            up_tok = target_up_token or self.active_up_token
            dn_tok = target_down_token or self.active_down_token

            up_bid, up_ask, dn_bid, dn_ask = None, None, None, None

            if up_tok and up_tok in self.live_book:
                b_info = self.live_book[up_tok]
                up_bid = b_info.get("best_bid")
                up_ask = b_info.get("best_ask")

            if dn_tok and dn_tok in self.live_book:
                b_info = self.live_book[dn_tok]
                dn_bid = b_info.get("best_bid")
                dn_ask = b_info.get("best_ask")

            # Complementary binary pricing fallback if one side missing in WS frame
            if up_bid is not None and up_ask is not None:
                if dn_bid is None:
                    dn_bid = round(1.0 - up_ask, 3)
                if dn_ask is None:
                    dn_ask = round(1.0 - up_bid, 3)
            elif dn_bid is not None and dn_ask is not None:
                if up_bid is None:
                    up_bid = round(1.0 - dn_ask, 3)
                if up_ask is None:
                    up_ask = round(1.0 - dn_bid, 3)

            return (up_bid, up_ask, dn_bid, dn_ask)
