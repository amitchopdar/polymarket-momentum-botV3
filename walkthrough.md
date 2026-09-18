# Walkthrough: Share-Based Sizing, Synthetic Stop-Loss & Settlement Tracker

## 1. Overview of Delivered Features

### A. Share-Based Position Sizing
- **Replaced USD Sizing**: Replaced `USER_V2_MAX_POSITION_SIZE_USD` ($5.00) with fixed share counts:
  $$\text{Target\_Quantity} = \max(5.0, \text{config.trade\_size\_shares})$$
- Default is set to **`7.0 shares`** (overridable via `TRADE_SIZE_SHARES=7.0` in `.env`).
- Every entry order now requests the exact configured share count regardless of token odds.

---

### B. Method 3: Synthetic Stop-Loss (Cross-Hedge Par Lock)
- **Problem Solved**: Direct selling during a crash often experiences 2–5¢ negative slippage on depleted bid books.
- **Solution**: When the stop-loss threshold ($0.63$) is touched:
  1. The bot buys the exact matching **7.0 shares of the opposite token** at $(\$1.00 - \text{SL\_Price} + \text{Slippage})$.
  2. The position becomes **100% Delta-Neutral** ($7.0\text{ UP} + 7.0\text{ DOWN} = \$7.00\text{ USDC}$ guaranteed par value).
  3. Status transitions to **`HEDGED_LOCKED`** and locks the predetermined PnL.
  4. All TP/SL triggers are disabled to prevent any recursion or duplicate orders.
  5. The single-position guard remains locked, blocking any new trades for the remainder of that 5-minute candle.

---

### C. 2-Minute Asynchronous Settlement Tracking
- **Problem Solved**: Polymarket oracle settlement takes ~1 to 2 minutes after candle expiry.
- **Solution**:
  1. At the 5-minute candle boundary, the hedged position is moved to the **`SettlementTracker` background queue**.
  2. `self.active_position` is reset to `None` **instantly**, allowing the bot to scan and enter trades in the **new 5-minute candle with zero downtime**.
  3. A background thread polls Polymarket Gamma API every 15s. Once the oracle settlement transaction confirms (`closed: true`, `resolved: true`), it updates the SQLite record to `RESOLVED_SETTLED` and sends a Telegram settlement receipt.

---

## 2. Test Verification Results

All 23 automated unit tests passed with 100% green coverage:

```bash
============================= test session starts ==============================
platform darwin -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/kamalasahu/polymarket-bot-v3
plugins: anyio-4.14.2
collected 23 items

tests/test_v2_strategy.py ..........                                     [ 43%]
tests/test_v3_strategy.py .........                                      [ 82%]
tests/test_settlement_tracker.py ....                                    [100%]

============================== 23 passed in 1.32s ==============================
```

---

## 3. Server Deployment Commands

To deploy this update to your Oracle Cloud server:

```bash
# 1. Navigate to the repository
cd /home/ubuntu/polymarket-momentum-botV3

# 2. Pull the latest code from GitHub
git pull origin main

# 3. Stop and remove existing container
sudo docker stop polymarket-bot-v3 2>/dev/null || true
sudo docker rm polymarket-bot-v3 2>/dev/null || true

# 4. Clean and create fresh database file
rm -f PolyDB_V3.sqlite* && touch PolyDB_V3.sqlite

# 5. Build and launch live container (24/7 auto-restart)
sudo docker build -t polymarket-bot-v3 .
sudo docker run -d \
  --name polymarket-bot-v3 \
  --restart unless-stopped \
  --env-file .env \
  polymarket-bot-v3

# 6. Stream live logs
sudo docker logs -f polymarket-bot-v3
```
