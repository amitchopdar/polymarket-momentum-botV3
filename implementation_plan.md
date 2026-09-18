# Implementation Plan: Share-Based Sizing, Synthetic Stop-Loss & Settlement Tracking

## User Review: What is Changing in `src/config.py`?

### 1. Variables Being REMOVED from `src/config.py`
| Removed Variable | Reason for Removal |
| :--- | :--- |
| `USER_V2_MAX_POSITION_SIZE_USD = 5.0` | **Removed**: Replaced by fixed share quantity (`TRADE_SIZE_SHARES`) to eliminate variable share sizing based on token odds. |
| `max_position_size_usd` (in `AppConfig`) | **Replaced**: Subsumed by `trade_size_shares: float`. |

---

### 2. Variables Being ADDED to `src/config.py`
| Added Variable | Default Value | Purpose |
| :--- | :--- | :--- |
| `USER_V3_TRADE_SIZE_SHARES` | `7.0` | Fixed number of shares to trade per signal (min 5.0). Overridable via `TRADE_SIZE_SHARES` in `.env`. |
| `trade_size_shares` | `7.0` | Dataclass field in `AppConfig` providing the runtime share count across all execution modules. |
| `USER_V3_STOP_LOSS_MODE` | `"SYNTHETIC_HEDGE"` | Selects between Method 3 (`"SYNTHETIC_HEDGE"`) and legacy (`"DIRECT_SELL"`). Overridable via `STOP_LOSS_MODE` in `.env`. |
| `stop_loss_mode` | `"SYNTHETIC_HEDGE"` | Dataclass field in `AppConfig` indicating active stop-loss execution mechanism. |

---

### 3. Variables Being RETAINED (No Changes)
All your other core momentum and risk parameters remain completely unchanged:
- `USER_V2_MOMENTUM_THRESHOLD_CENTS = 0.20` *(20¢ surge in 10s)*
- `USER_V2_MOMENTUM_WINDOW_SEC = 10.0` *(10s sliding window)*
- `USER_V2_TAKE_PROFIT_CENTS = 0.15` *(15¢ TP target)*
- `USER_V2_HIGH_ODDS_CUTOFF = 0.85` *(85¢ cutoff for $0.99 TP target)*
- `USER_V2_HIGH_ODDS_TP_TARGET = 0.9900` *($0.99 target)*
- `USER_V2_TRAILING_SL_ENABLED = True` *(Trailing SL active)*
- `USER_V2_TRAILING_SL_DISTANCE_CENTS = 0.07` *(7¢ trailing distance)*
- `USER_V2_STOP_LOSS_SLIPPAGE_CENTS = 0.01` *(1¢ slippage buffer)*
- `USER_V2_MIN_ENTRY_ODDS_FLOOR = 0.65` *(65¢ minimum entry)*
- `USER_V2_MAX_ENTRY_ODDS_CEILING = 0.92` *(92¢ maximum entry)*
- `USER_V2_MAX_ACTIVE_POSITIONS = 1` *(Single position guard)*
- `USER_V3_BUY_SLIPPAGE_CENTS = 0.01` *(1¢ marketable buy buffer)*
- `USER_V3_MAKER_ORDER_TIMEOUT_SEC = 5.0` *(5s timeout)*

---

## Exact Code Diff for `src/config.py`

```diff
 # ==============================================================================
 # POLYMARKET BOT V3 MOMENTUM JUMP STRATEGY PARAMETERS (Non-Sensitive Defaults)
 # ==============================================================================
 
 USER_EXECUTION_MODE = "LIVE"
 USER_V2_MOMENTUM_THRESHOLD_CENTS = 0.20   # 20-cent (+0.20) absolute odds increase threshold
 USER_V2_MOMENTUM_WINDOW_SEC = 10.0        # Sliding momentum lookback window (10 seconds)
 USER_V2_TAKE_PROFIT_CENTS = 0.15          # Take Profit absolute cents gain target (+0.15 / +15 cents for Tier 1)
 USER_V2_HIGH_ODDS_CUTOFF = 0.85           # High odds cutoff threshold for Tier 2 ($0.85 / 85 cents)
 USER_V2_HIGH_ODDS_TP_TARGET = 0.9900      # Fixed TP target price for Tier 2 ($0.99 / $1.00 max exchange limit price)
 USER_V2_TRAILING_SL_ENABLED = True        # Enable Trailing Stop Loss based on High Water Mark
 USER_V2_TRAILING_SL_DISTANCE_CENTS = 0.07 # Trailing SL distance from HWM (7 cents)
 USER_V2_STOP_LOSS_SLIPPAGE_CENTS = 0.01   # Stop Loss exit slippage for Limit Sell orders (1 cent)
 USER_V2_MIN_ENTRY_ODDS_FLOOR = 0.65       # Minimum odds floor required for trade entry ($0.65 / 65 cents)
 USER_V2_MAX_ENTRY_ODDS_CEILING = 0.92     # Maximum odds ceiling limit for trade entry ($0.92 / 92 cents)
-USER_V2_MAX_POSITION_SIZE_USD = 5.0       # Max position size per trade ($5.00)
+USER_V3_TRADE_SIZE_SHARES = 7.0           # Fixed number of shares to trade per signal (min 5.0)
+USER_V3_STOP_LOSS_MODE = "SYNTHETIC_HEDGE" # Stop loss mode: SYNTHETIC_HEDGE or DIRECT_SELL
 USER_V2_MAX_ACTIVE_POSITIONS = 1          # Single active position limit across bot (1 position)
 
 # Polymarket Bot V3 Execution & Timeout Parameters
 USER_V3_BUY_SLIPPAGE_CENTS = 0.01          # 1 cent (+0.01) buffer above ask for instant marketable limit buy fill
 USER_V3_MAKER_OFFSET_CENTS = 0.01         # Alias for buy offset
 USER_V3_MAKER_ORDER_TIMEOUT_SEC = 5.0     # 5 seconds order cancellation timeout
 # ==============================================================================
@@ -134,7 +136,8 @@
     v2_stop_loss_slippage_cents: float = field(default_factory=lambda: _safe_float(os.getenv("V2_STOP_LOSS_SLIPPAGE_CENTS"), USER_V2_STOP_LOSS_SLIPPAGE_CENTS))
     v2_min_entry_odds_floor: float = field(default_factory=lambda: _safe_float(os.getenv("V2_MIN_ENTRY_ODDS_FLOOR"), USER_V2_MIN_ENTRY_ODDS_FLOOR))
     v2_max_entry_odds_ceiling: float = field(default_factory=lambda: _safe_float(os.getenv("V2_MAX_ENTRY_ODDS_CEILING"), USER_V2_MAX_ENTRY_ODDS_CEILING))
-    max_position_size_usd: float = field(default_factory=lambda: _safe_float(os.getenv("MAX_POSITION_SIZE_USD"), USER_V2_MAX_POSITION_SIZE_USD))
+    trade_size_shares: float = field(default_factory=lambda: _safe_float(os.getenv("TRADE_SIZE_SHARES") or os.getenv("MAX_POSITION_SIZE_SHARES"), USER_V3_TRADE_SIZE_SHARES))
+    stop_loss_mode: str = field(default_factory=lambda: _clean_str(os.getenv("STOP_LOSS_MODE", USER_V3_STOP_LOSS_MODE)).upper())
     max_active_positions: int = field(default_factory=lambda: _safe_int(os.getenv("MAX_ACTIVE_POSITIONS"), USER_V2_MAX_ACTIVE_POSITIONS))
```

---

## Complete Feature Integration Plan

1. **`src/config.py`**: Apply the exact diff above.
2. **`src/execution/strategy.py`**:
   - `target_qty = max(5.0, round(float(getattr(config, "trade_size_shares", 7.0)), 2))`
   - When SL triggers: Execute `execute_synthetic_hedge_exit()` by buying `target_qty` of the opposite token.
   - Transition status to `HEDGED_LOCKED` and record `Locked_Pnl`.
   - Prevent any additional SL/TP triggers or new trade entries during the candle.
   - At candle close, transfer position to `settlement_tracker` and reset `self.active_position = None` immediately for the next candle.
3. **`src/execution/settlement_tracker.py`**:
   - Background worker to poll Polymarket Gamma API every 15s during the 2-minute resolution delay, updating DB to `RESOLVED_SETTLED`.
4. **`tests/test_v2_strategy.py` & `tests/test_v3_strategy.py`**:
   - Update all test suites for fixed share sizing and verify 100% green tests.
