
import asyncio
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

import websockets

sys.stdout.reconfigure(encoding="utf-8")

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

DB_NAME = "btc_prices.db"

# ── BTC Price (Chainlink) ──
WS_PRICE_URL = "wss://ws-live-data.polymarket.com"
PRICE_SUBSCRIBE = {
    "action": "subscribe",
    "subscriptions": [
        {
            "topic": "crypto_prices_chainlink",
            "type": "*",
            "filters": '{"symbol":"BTC/USD"}',
        }
    ],
}

# ── CLOB (Odds) ──
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

STALE_TIMEOUT = 60  # seconds without data before forcing reconnect


# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

conn = sqlite3.connect(DB_NAME, check_same_thread=False)
cur = conn.cursor()

# BTC spot price candles (existing)
cur.execute("""
CREATE TABLE IF NOT EXISTS btc_1m(
    candle_start INTEGER PRIMARY KEY,
    candle_end   INTEGER NOT NULL,
    open  REAL NOT NULL,
    high  REAL NOT NULL,
    low   REAL NOT NULL,
    close REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

cur.execute("""
CREATE TABLE IF NOT EXISTS btc_5m(
    candle_start INTEGER PRIMARY KEY,
    candle_end   INTEGER NOT NULL,
    open  REAL NOT NULL,
    high  REAL NOT NULL,
    low   REAL NOT NULL,
    close REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

# UP/DOWN token odds (new)
cur.execute("""
CREATE TABLE IF NOT EXISTS Odds(
    Candle_Start        TEXT PRIMARY KEY,
    Up_Token_Id         TEXT NOT NULL,
    Up_Open             REAL,
    Up_High             REAL,
    Up_Low              REAL,
    Up_Close            REAL,
    Up_Volume_Dollars   REAL DEFAULT 0,
    Down_Token_Id       TEXT NOT NULL,
    Down_Open           REAL,
    Down_High           REAL,
    Down_Low            REAL,
    Down_Close          REAL,
    Down_Volume_Dollars REAL DEFAULT 0,
    "1_Min_Up_High"   REAL, "1_Min_Up_Low"   REAL,
    "2_Min_Up_High"   REAL, "2_Min_Up_Low"   REAL,
    "3_Min_Up_High"   REAL, "3_Min_Up_Low"   REAL,
    "4_Min_Up_High"   REAL, "4_Min_Up_Low"   REAL,
    "5_Min_Up_High"   REAL, "5_Min_Up_Low"   REAL,
    "1_Min_Down_High" REAL, "1_Min_Down_Low" REAL,
    "2_Min_Down_High" REAL, "2_Min_Down_Low" REAL,
    "3_Min_Down_High" REAL, "3_Min_Down_Low" REAL,
    "4_Min_Down_High" REAL, "4_Min_Down_Low" REAL,
    "5_Min_Down_High" REAL, "5_Min_Down_Low" REAL,
    Status TEXT DEFAULT 'OPEN'
)""")
conn.commit()


# ═══════════════════════════════════════════════════════════════
# BTC PRICE CANDLE
# ═══════════════════════════════════════════════════════════════

class Candle:
    def __init__(self, timeframe):
        self.tf = timeframe
        self.reset()

    def reset(self):
        self.start = self.end = None
        self.open = self.high = self.low = self.close = None

    def process(self, ts_ms, price):
        ts = ts_ms // 1000
        bucket = (ts // self.tf) * self.tf
        if self.start is None:
            self._new(bucket, price)
            return
        if bucket != self.start:
            self._save()
            self._new(bucket, price)
            return
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price

    def _new(self, bucket, price):
        self.start = bucket
        self.end = bucket + self.tf - 1
        self.open = self.high = self.low = self.close = price

    def _save(self):
        table = "btc_1m" if self.tf == 60 else "btc_5m"
        cur.execute(
            f"INSERT OR REPLACE INTO {table}"
            " (candle_start,candle_end,open,high,low,close)"
            " VALUES (?,?,?,?,?,?)",
            (self.start, self.end, self.open, self.high, self.low, self.close),
        )
        conn.commit()
        label = f"{self.tf // 60}M"
        s = datetime.fromtimestamp(self.start).strftime("%H:%M:%S")
        e = datetime.fromtimestamp(self.end).strftime("%H:%M:%S")
        print(f"\n{'='*60}")
        print(f"[{label} BTC CLOSED] {s} -> {e}")
        print(f"O:{self.open:.2f} H:{self.high:.2f} L:{self.low:.2f} C:{self.close:.2f}")
        print(f"Saved to SQLite ({table})")
        print(f"{'='*60}\n")

    def flush(self):
        if self.start is not None:
            self._save()
            self.reset()


one_m = Candle(60)
five_m = Candle(300)


# ═══════════════════════════════════════════════════════════════
# HTTP HELPER
# ═══════════════════════════════════════════════════════════════

def _http_get(url):
    """Blocking HTTP GET — call via asyncio.to_thread()."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "PredictUP/1.0")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ═══════════════════════════════════════════════════════════════
# MARKET DISCOVERY
# ═══════════════════════════════════════════════════════════════

def _parse_market(raw, window_ts):
    """Parse a Gamma API market object into our internal dict."""
    raw_tokens = raw.get("clobTokenIds", "[]")
    raw_outcomes = raw.get("outcomes", "[]")

    tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
    outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes

    up_idx = down_idx = None
    for i, o in enumerate(outcomes):
        ol = o.strip().lower()
        if ol in ("up", "yes"):
            up_idx = i
        elif ol in ("down", "no"):
            down_idx = i

    if up_idx is None or down_idx is None or up_idx >= len(tokens) or down_idx >= len(tokens):
        return None

    return {
        "condition_id": raw.get("conditionId") or raw.get("condition_id", ""),
        "question": raw.get("question", "BTC 5-Min Up or Down"),
        "up_token": tokens[up_idx],
        "down_token": tokens[down_idx],
        "window_ts": window_ts,
        "end_time": window_ts + 300,
    }


async def fetch_active_market():
    """Find the currently active BTC 5-min up/down market."""
    now_ts = int(time.time())
    window_ts = (now_ts // 300) * 300

    # ── Strategy 1: computed slug ──
    slug = f"btc-updown-5m-{window_ts}"
    try:
        data = await asyncio.to_thread(_http_get, f"{GAMMA_API}/markets?slug={slug}")
        if data:
            items = data if isinstance(data, list) else [data]
            for item in items:
                m = _parse_market(item, window_ts)
                if m:
                    return m
    except Exception:
        pass

    # ── Strategy 2: events endpoint ──
    try:
        events = await asyncio.to_thread(
            _http_get,
            f"{GAMMA_API}/events?slug=will-btc-go-up-in-the-next-5-minutes",
        )
        if events:
            event = events[0] if isinstance(events, list) else events
            markets = event.get("markets", [])

            # Prefer markets explicitly marked active
            for raw in markets:
                if raw.get("active") and not raw.get("closed"):
                    m = _parse_market(raw, window_ts)
                    if m:
                        return m

            # Fallback: verify via CLOB REST
            for raw in reversed(markets):  # newest first
                if raw.get("closed"):
                    continue
                cid = raw.get("conditionId", "")
                if not cid:
                    continue
                try:
                    clob = await asyncio.to_thread(_http_get, f"{CLOB_API}/markets/{cid}")
                    if clob and clob.get("active") and not clob.get("closed"):
                        m = _parse_market(raw, window_ts)
                        if m:
                            return m
                except Exception:
                    continue
    except Exception:
        pass

    return None


# ═══════════════════════════════════════════════════════════════
# ODDS TRACKER
# ═══════════════════════════════════════════════════════════════

class OddsTracker:
    """
    Tracks best bid/ask for UP and DOWN tokens.
    Uses best_ask as the canonical price for OHLC candles.
    """

    def __init__(self):
        self.market = None
        self.up_token = self.down_token = None
        self.window_ts = self.end_time = None
        self._reset_prices()

    def _reset_prices(self):
        # Current best bid / best ask
        self.up_bid = self.up_ask = None
        self.down_bid = self.down_ask = None

        # 5-min OHLC (best ask)
        self.up_open = self.up_high = self.up_low = self.up_close = None
        self.down_open = self.down_high = self.down_low = self.down_close = None

        # Per-minute {1..5} → {up_high, up_low, down_high, down_low}
        self.mins = {
            m: dict(up_high=None, up_low=None, down_high=None, down_low=None)
            for m in range(1, 6)
        }

    def set_market(self, market):
        self.market = market
        self.up_token = market["up_token"]
        self.down_token = market["down_token"]
        self.window_ts = market["window_ts"]
        self.end_time = market["end_time"]
        self._reset_prices()

    # ── Which minute (1-5) are we in? ──
    def _minute_num(self):
        elapsed = int(time.time()) - self.window_ts
        return min(max(elapsed // 60 + 1, 1), 5)

    # ── Price update ──
    def update(self, token_id, best_bid, best_ask):
        if token_id == self.up_token:
            self.up_bid, self.up_ask = best_bid, best_ask
            self._tick("up", best_ask)
        elif token_id == self.down_token:
            self.down_bid, self.down_ask = best_bid, best_ask
            self._tick("down", best_ask)

    def _tick(self, side, price):
        # 5-min OHLC
        o, h, l, c = f"{side}_open", f"{side}_high", f"{side}_low", f"{side}_close"
        if getattr(self, o) is None:
            setattr(self, o, price)
            setattr(self, h, price)
            setattr(self, l, price)
            setattr(self, c, price)
        else:
            setattr(self, h, max(getattr(self, h), price))
            setattr(self, l, min(getattr(self, l), price))
            setattr(self, c, price)

        # Per-minute
        m = self._minute_num()
        md = self.mins[m]
        kh, kl = f"{side}_high", f"{side}_low"
        if md[kh] is None:
            md[kh] = md[kl] = price
        else:
            md[kh] = max(md[kh], price)
            md[kl] = min(md[kl], price)

    # ── Persist to SQLite ──
    def save(self):
        if self.up_open is None and self.down_open is None:
            return

        candle_start = datetime.fromtimestamp(self.window_ts).strftime("%Y-%m-%d %H:%M:%S")
        vals = (
            candle_start,
            self.up_token or "",
            self.up_open, self.up_high, self.up_low, self.up_close, 0,
            self.down_token or "",
            self.down_open, self.down_high, self.down_low, self.down_close, 0,
            self.mins[1]["up_high"], self.mins[1]["up_low"],
            self.mins[2]["up_high"], self.mins[2]["up_low"],
            self.mins[3]["up_high"], self.mins[3]["up_low"],
            self.mins[4]["up_high"], self.mins[4]["up_low"],
            self.mins[5]["up_high"], self.mins[5]["up_low"],
            self.mins[1]["down_high"], self.mins[1]["down_low"],
            self.mins[2]["down_high"], self.mins[2]["down_low"],
            self.mins[3]["down_high"], self.mins[3]["down_low"],
            self.mins[4]["down_high"], self.mins[4]["down_low"],
            self.mins[5]["down_high"], self.mins[5]["down_low"],
            "OPEN",
        )

        cur.execute(
            'INSERT OR REPLACE INTO Odds ('
            ' Candle_Start,'
            ' Up_Token_Id, Up_Open, Up_High, Up_Low, Up_Close, Up_Volume_Dollars,'
            ' Down_Token_Id, Down_Open, Down_High, Down_Low, Down_Close, Down_Volume_Dollars,'
            ' "1_Min_Up_High","1_Min_Up_Low","2_Min_Up_High","2_Min_Up_Low",'
            ' "3_Min_Up_High","3_Min_Up_Low","4_Min_Up_High","4_Min_Up_Low",'
            ' "5_Min_Up_High","5_Min_Up_Low",'
            ' "1_Min_Down_High","1_Min_Down_Low","2_Min_Down_High","2_Min_Down_Low",'
            ' "3_Min_Down_High","3_Min_Down_Low","4_Min_Down_High","4_Min_Down_Low",'
            ' "5_Min_Down_High","5_Min_Down_Low",'
            ' Status'
            ') VALUES (' + ','.join(['?'] * len(vals)) + ')',
            vals,
        )
        conn.commit()

        s = datetime.fromtimestamp(self.window_ts).strftime("%H:%M:%S")
        e = datetime.fromtimestamp(self.end_time - 1).strftime("%H:%M:%S")
        print(f"\n{'━'*60}")
        print(f"[5M ODDS CLOSED] {s} -> {e}")
        if self.up_open is not None:
            print(f"UP   O:{self.up_open:.2f} H:{self.up_high:.2f} L:{self.up_low:.2f} C:{self.up_close:.2f}")
        if self.down_open is not None:
            print(f"DOWN O:{self.down_open:.2f} H:{self.down_high:.2f} L:{self.down_low:.2f} C:{self.down_close:.2f}")
        print("Saved to SQLite (Odds)")
        print(f"{'━'*60}\n")


tracker = OddsTracker()


# ═══════════════════════════════════════════════════════════════
# RESOLUTION CHECKER (background)
# ═══════════════════════════════════════════════════════════════

async def check_resolution(condition_id, candle_start_str):
    """Poll CLOB API after market closes to update Status → UP/DOWN."""
    await asyncio.sleep(30)

    for _ in range(6):
        try:
            data = await asyncio.to_thread(
                _http_get, f"{CLOB_API}/markets/{condition_id}"
            )
            if data and data.get("closed"):
                for tok in data.get("tokens", []):
                    if tok.get("winner"):
                        outcome = tok.get("outcome", "").strip().lower()
                        status = "UP" if outcome in ("up", "yes") else "DOWN"
                        cur.execute(
                            "UPDATE Odds SET Status=? WHERE Candle_Start=?",
                            (status, candle_start_str),
                        )
                        conn.commit()
                        print(f"  [Resolved] {candle_start_str} -> {status}")
                        return
        except Exception:
            pass
        await asyncio.sleep(15)


# ═══════════════════════════════════════════════════════════════
# BTC PRICE STREAM
# ═══════════════════════════════════════════════════════════════

def _extract_price_ticks(msg):
    ticks = []
    payload = msg.get("payload")
    if not payload:
        return ticks
    if payload.get("symbol", "").lower() not in ("btc/usd", "btcusd"):
        return ticks

    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            ts = item.get("timestamp")
            val = item.get("value") or item.get("price")
            if ts is not None and val is not None:
                ticks.append((int(ts), float(val)))
    elif isinstance(data, dict):
        ts = data.get("timestamp")
        val = data.get("value") or data.get("price")
        if ts is not None and val is not None:
            ticks.append((int(ts), float(val)))
    else:
        ts = payload.get("timestamp")
        val = payload.get("value") or payload.get("price")
        if ts is not None and val is not None:
            ticks.append((int(ts), float(val)))
    return ticks


async def btc_price_stream():
    """Stream BTC/USD spot prices from Chainlink via Polymarket."""
    while True:
        try:
            print("[BTC] Connecting...")
            async with websockets.connect(
                WS_PRICE_URL, ping_interval=5, ping_timeout=10, max_size=None
            ) as ws:
                await ws.send(json.dumps(PRICE_SUBSCRIBE))
                print("[BTC] Connected & Subscribed\n")
                last_data = time.monotonic()

                async for raw in ws:
                    if time.monotonic() - last_data > STALE_TIMEOUT:
                        print("[BTC] Stale — reconnecting...")
                        break

                    if isinstance(raw, bytes):
                        try:
                            raw = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    ticks = _extract_price_ticks(msg)
                    if not ticks:
                        continue
                    last_data = time.monotonic()
                    ticks.sort(key=lambda x: x[0])

                    for ts, price in ticks:
                        dt = datetime.fromtimestamp(ts / 1000)
                        print(f"[BTC] {dt.strftime('%H:%M:%S.%f')[:-3]} | {price:.2f}")
                        one_m.process(ts, price)
                        five_m.process(ts, price)

        except Exception as e:
            print(f"[BTC] Disconnected: {e}")
            print("[BTC] Reconnecting in 5s...\n")
            await asyncio.sleep(5)


# ═══════════════════════════════════════════════════════════════
# ODDS STREAM
# ═══════════════════════════════════════════════════════════════

_last_odds_print = 0.0


def _print_odds():
    """Throttled console output for bid/ask (max once per second)."""
    global _last_odds_print
    now = time.time()
    if now - _last_odds_print < 1.0:
        return
    _last_odds_print = now

    ts = datetime.now().strftime("%H:%M:%S")
    parts = []
    if tracker.up_bid is not None and tracker.up_ask is not None:
        parts.append(f"UP  Bid:{tracker.up_bid:.2f} Ask:{tracker.up_ask:.2f}")
    if tracker.down_bid is not None and tracker.down_ask is not None:
        parts.append(f"DOWN Bid:{tracker.down_bid:.2f} Ask:{tracker.down_ask:.2f}")
    if parts:
        print(f"{ts} | {' | '.join(parts)}")


def _handle_clob_event(msg):
    """
    Parse a CLOB WebSocket event and update the tracker.
    Returns True if any price was updated.
    """
    event_type = msg.get("event_type")
    updated = False

    if event_type == "book":
        asset_id = msg.get("asset_id", "")
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])
        bb = float(bids[0]["price"]) if bids else None
        ba = float(asks[0]["price"]) if asks else None
        if bb is not None and ba is not None:
            tracker.update(asset_id, bb, ba)
            updated = True

    elif event_type == "price_change":
        # Format A: price_changes array with per-asset best_bid/best_ask
        changes = msg.get("price_changes", [])
        for ch in changes:
            aid = ch.get("asset_id", "")
            bb = ch.get("best_bid")
            ba = ch.get("best_ask")
            if bb is not None and ba is not None:
                tracker.update(aid, float(bb), float(ba))
                updated = True

        # Format B: top-level asset_id (single asset)
        if not changes:
            aid = msg.get("asset_id", "")
            bb = msg.get("best_bid")
            ba = msg.get("best_ask")
            if aid and bb is not None and ba is not None:
                tracker.update(aid, float(bb), float(ba))
                updated = True

    elif event_type == "best_bid_ask":
        aid = msg.get("asset_id", "")
        bb = msg.get("best_bid")
        ba = msg.get("best_ask")
        if bb is not None and ba is not None:
            tracker.update(aid, float(bb), float(ba))
            updated = True

    elif event_type == "last_trade_price":
        # Placeholder for future volume tracking
        pass

    return updated


async def odds_stream():
    """Stream UP/DOWN token odds from the CLOB WebSocket."""
    while True:
        try:
            # ── Discover active market ──
            print("[ODDS] Discovering BTC 5-min market...")
            market = None
            for attempt in range(12):
                market = await fetch_active_market()
                if market:
                    break
                wait = min(3 * (attempt + 1), 15)
                print(f"[ODDS] Not found yet — retry in {wait}s (attempt {attempt+1})")
                await asyncio.sleep(wait)

            if not market:
                print("[ODDS] Could not find market. Retrying in 60s...")
                await asyncio.sleep(60)
                continue

            tracker.set_market(market)
            end_dt = datetime.fromtimestamp(market["end_time"]).strftime("%H:%M:%S")

            print(f"\n{'━'*60}")
            print(f"[ODDS] {market['question']}")
            print(f"[ODDS] UP   token: {market['up_token'][:24]}...")
            print(f"[ODDS] DOWN token: {market['down_token'][:24]}...")
            print(f"[ODDS] Window ends: {end_dt}")
            print(f"{'━'*60}\n")

            # ── Connect CLOB WebSocket ──
            async with websockets.connect(
                CLOB_WS_URL, ping_interval=5, ping_timeout=10, max_size=None
            ) as ws:
                sub = {
                    "assets_ids": [market["up_token"], market["down_token"]],
                    "type": "market",
                    "custom_feature_enabled": True,
                }
                await ws.send(json.dumps(sub))
                print("[ODDS] Subscribed to CLOB stream\n")

                last_data = time.monotonic()

                while True:
                    remaining = market["end_time"] - time.time()
                    if remaining <= 0:
                        break

                    try:
                        timeout = min(max(remaining, 0.1), 5.0)
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        if time.monotonic() - last_data > STALE_TIMEOUT:
                            print("[ODDS] Stale — reconnecting...")
                            break
                        continue

                    if isinstance(raw, bytes):
                        try:
                            raw = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    # Handle arrays of events
                    events = msg if isinstance(msg, list) else [msg]
                    for ev in events:
                        if _handle_clob_event(ev):
                            last_data = time.monotonic()

                    _print_odds()

                # ── Window ended: save candle ──
                tracker.save()

                # Background resolution check
                cs = datetime.fromtimestamp(market["window_ts"]).strftime("%Y-%m-%d %H:%M:%S")
                asyncio.create_task(check_resolution(market["condition_id"], cs))

        except Exception as e:
            print(f"[ODDS] Error: {e}")
            print("[ODDS] Retrying in 5s...\n")
            await asyncio.sleep(5)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

async def main():
    print(f"{'═'*60}")
    print("  PredictUP Bot")
    print("  Streams: BTC/USD Price + UP/DOWN Odds")
    print(f"  Database: {DB_NAME}")
    print(f"{'═'*60}\n")

    await asyncio.gather(
        btc_price_stream(),
        odds_stream(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nFlushing data...")
        one_m.flush()
        five_m.flush()
        tracker.save()
        print("Shutting down...")
        conn.close()
