"""
Binance Futures Resilient WebSocket Client (US1.1.1, US1.2.1)
"""

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Optional, Dict, Any
import websockets

logger = logging.getLogger(__name__)

BINANCE_WS_COMBINED_URL = "wss://fstream.binance.com/stream?streams=btcusdt@kline_5m/btcusdt@depth10@100ms/btcusdt@forceOrder"

class BinanceWebSocketClient:
    """
    Resilient WebSocket client for Binance Futures streams.
    Includes 15s ping/pong heartbeats, exponential backoff (1s-30s), and callback routing.
    """

    def __init__(
        self,
        url: str = BINANCE_WS_COMBINED_URL,
        on_kline_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_depth_callback: Optional[Callable[[list, list], None]] = None,
        on_liquidation_callback: Optional[Callable[[str, float, float], None]] = None,
        on_reconnect_callback: Optional[Callable[[], None]] = None
    ):
        self.url = url
        self.on_kline = on_kline_callback
        self.on_depth = on_depth_callback
        self.on_liquidation = on_liquidation_callback
        self.on_reconnect = on_reconnect_callback

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.is_connected = False
        self.retry_count = 0

    def start(self) -> None:
        """
        Starts the WebSocket client thread.
        """
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BinanceWSThread")
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stops the WebSocket client thread safely.
        """
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._cancel_tasks)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _cancel_tasks(self) -> None:
        """
        Cancels all running asyncio tasks on worker loop.
        """
        if self._loop and self._loop.is_running():
            for task in asyncio.all_tasks(self._loop):
                task.cancel()

    def _run_loop(self) -> None:
        """
        Runs the asyncio event loop in worker thread.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_listen())
        except Exception as e:
            logger.debug(f"BinanceWebSocketClient loop stopped: {e}")
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                self._loop.close()

    async def _connect_and_listen(self) -> None:
        """
        Main connection and reconnection loop with exponential backoff.
        """
        while not self._stop_event.is_set():
            try:
                logger.info(f"Connecting to Binance Futures WS: {self.url}")
                async with websockets.connect(
                    self.url,
                    ping_interval=15,
                    ping_timeout=15,
                    close_timeout=5
                ) as ws:
                    self.is_connected = True
                    self.retry_count = 0
                    logger.info("Connected to Binance Futures WebSocket stream.")

                    if self.on_reconnect:
                        try:
                            self.on_reconnect()
                        except Exception as rec_err:
                            logger.error(f"Error in on_reconnect callback: {rec_err}")

                    while not self._stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            self._handle_message(message)
                        except asyncio.TimeoutError:
                            logger.warning("No WebSocket frames received for 30s. Ping heartbeat check.")
                            await ws.ping()

            except asyncio.CancelledError:
                self.is_connected = False
                break
            except Exception as e:
                self.is_connected = False
                if self._stop_event.is_set():
                    break

                backoff_delay = min(30.0, 1.0 * (2 ** self.retry_count))
                self.retry_count += 1
                logger.error(f"WebSocket error: {e}. Reconnecting in {backoff_delay:.1f}s (retry #{self.retry_count})...")
                await asyncio.sleep(backoff_delay)

    def _handle_message(self, raw_msg: str) -> None:
        """
        Parses incoming JSON payloads and dispatches to callbacks.
        """
        try:
            payload = json.loads(raw_msg)
            stream_name = payload.get("stream", "")
            data = payload.get("data", {})

            if "kline" in stream_name and "k" in data and self.on_kline:
                try:
                    self.on_kline(data["k"])
                except Exception as k_err:
                    logger.error(f"Error in on_kline callback: {k_err}", exc_info=True)
            elif "depth" in stream_name and self.on_depth:
                try:
                    bids = data.get("b", [])
                    asks = data.get("a", [])
                    self.on_depth(bids, asks)
                except Exception as d_err:
                    logger.error(f"Error in on_depth callback: {d_err}", exc_info=True)
            elif "forceOrder" in stream_name and self.on_liquidation:
                try:
                    order = data.get("o", {})
                    side = order.get("S", "")
                    qty = float(order.get("q", 0.0))
                    price = float(order.get("p", 0.0))
                    self.on_liquidation(side, qty, price)
                except Exception as l_err:
                    logger.error(f"Error in on_liquidation callback: {l_err}", exc_info=True)

        except Exception as e:
            logger.error(f"Error parsing WebSocket message: {e}")
