# Architectural Specification: Polymarket Bot V3 Live Market Execution Engine

This document provides the complete, production-grade technical specification for transitioning **Polymarket Bot V3** from `DRY_RUN` simulation to **Live Real-Money Trading** on Polymarket's Central Limit Order Book (CLOB).

---

## 1. System Overview & Live Environment Setup

### **A. Polygon Blockchain & Exchange Credentials**
Polymarket trades prediction contracts on the **Polygon PoS Blockchain (Chain ID 137)** using **USDC.e** (Bridged USDC token contract: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`).

To enable live trading, the `.env` file inside `/home/ubuntu/polymarket-momentum-botV3/.env` must contain:

```env
EXECUTION_MODE="LIVE"
TELEGRAM_BOT_TOKEN="8856745669:AAH9UzAjWH2LsUVLrPMFIGkOA-FOQxZ4jH0"
TELEGRAM_CHAT_ID="488798563,835915433"
TELEGRAM_AUTHORIZED_USER_IDS="488798563,835915433"

# Polygon Wallet & Polymarket CLOB Credentials
POLYMARKET_PRIVATE_KEY="0xYOUR_64_HEX_POLYGON_PRIVATE_KEY"
POLYMARKET_API_KEY="YOUR_CLOB_API_KEY_UUID"
POLYMARKET_SECRET="YOUR_CLOB_SECRET_BASE64"
POLYMARKET_PASSPHRASE="YOUR_CLOB_PASSPHRASE"
```

---

## 2. Order Execution Lifecycle & Flowcharts

```
┌────────────────────────────────────────────────────────────────────────┐
│                        STEP 1: SIGNAL DETECTION                        │
│  • 10s Window Ask Surge >= +0.15 AND Current Ask >= $0.65 (Odds Floor) │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  STEP 2: 0% FEE MAKER BUY PLACEMENT                    │
│  • Calculate Limit Buy Price = Current_Best_Ask - $0.02 (Maker Offset) │
│  • Submit EIP-712 Post-Only Buy Order via py-clob-client SDK           │
│  • Status = PENDING_FILL | Start 5.0s Timeout Timer                   │
└────────────────────────────────────────────────────────────────────────┘
                                    │
             ┌──────────────────────┴──────────────────────┐
             ▼                                             ▼
┌──────────────────────────────┐              ┌──────────────────────────────┐
│     SCENARIO A: FILL EVENT   │              │   SCENARIO B: 5s TIMEOUT     │
│ • Price dips to hit bid      │              │ • Unfilled after 5.0 seconds │
│ • Order Fills (Full/Partial) │              │ • Dispatch clob_client.cancel│
└──────────────────────────────┘              │ • Unlock State Guard (0 Lost)│
             │                                └──────────────────────────────┘
             ▼
┌────────────────────────────────────────────────────────────────────────┐
│            STEP 3: INSTANT DUAL LIMIT SELL ORDER PLACEMENT             │
│  For every filled quantity (or partial fill block):                    │
│  1. Submit Resting SELL Limit Order at Take_Profit_Price on CLOB       │
│  2. Submit Resting SELL Limit Order at Initial Stop_Loss_Price on CLOB │
└────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│         STEP 4: DYNAMIC HIGH WATER MARK (HWM) TRAILING SL ENGINE       │
│  • Monitor tick stream for new peak prices (HWM_t = max(HWM_prev, P))  │
│  • Calculate New Trailing SL = HWM_t - $0.10                           │
│  • If New_SL > Current Resting SL Order Price:                         │
│      1. Cancel old SL order on CLOB (clob_client.cancel(old_sl_id))    │
│      2. Place new SELL Limit Order at elevated New_SL price on CLOB    │
└────────────────────────────────────────────────────────────────────────┘
                                    │
             ┌──────────────────────┴──────────────────────┐
             ▼                                             ▼
┌──────────────────────────────┐              ┌──────────────────────────────┐
│  TAKE PROFIT ORDER FILLED    │              │   STOP LOSS ORDER FILLED     │
│ • Exchange fills TP Sell     │              │ • Exchange fills SL Sell     │
│ • Auto-cancel SL Sell Order  │              │ • Auto-cancel TP Sell Order  │
│ • Notify Telegram + Log PnL  │              │ • Notify Telegram + Log PnL  │
└──────────────────────────────┘              └──────────────────────────────┘
```

---

## 3. Detailed Component Technical Specifications

### **A. 0% Fee Maker Buy Entry (`execute_entry_v3`)**
* **Limit Price Calculation:** $\text{Limit\_Buy\_Price} = \text{round}(\text{Best\_Ask} - \text{USER\_V3\_MAKER\_OFFSET\_CENTS}, 4)$
* **SDK Invocation:**
  ```python
  from py_clob_client.client import ClobClient
  from py_clob_client.clob_types import OrderArgs, OrderType

  order_args = OrderArgs(
      price=limit_buy_price,
      size=target_qty,
      side="BUY",
      token_id=token_id
  )
  signed_order = client.create_order(order_args)
  resp = client.post_order(signed_order, OrderType.GTC) # Post-Only Maker
  ```

---

### **B. 5-Second Timeout Auto-Cancellation (`_evaluate_pending_fill`)**
* **Timeout Verification:** If $T_{\text{now}} - T_{\text{placed}} \ge 5.0\text{ seconds}$ and order status is `PENDING_FILL`:
  ```python
  # Dispatch cancellation request to physical exchange
  client.cancel(order_id=pos["Order_Id"])
  
  # Update local database and unlock state guard
  pos["Position_Status"] = "CANCELLED"
  pos["Exit_Reason"] = "CANCELLED_TIMEOUT"
  self.active_position = None
  ```

---

### **C. Instant Dual Limit Order Placement on Fill & Partial Fill Handling**
* **Trigger:** On WebSocket execution push (or tick match where price $\le \text{Limit\_Buy\_Price}$):
* **Partial Fill Logic:** Each filled block of shares $Q_{\text{filled}}$ immediately triggers:
  1. **TP Sell Limit Order:** `client.post_order(side="SELL", price=Take_Profit_Price, size=Q_filled)`
  2. **SL Sell Limit Order:** `client.post_order(side="SELL", price=Stop_Loss_Price, size=Q_filled)`
* **Order Tracking:** Store `TP_Order_Id` and `SL_Order_Id` inside the active position record.

---

### **D. Dynamic High Water Mark Trailing Stop Loss Engine**
* **Peak Tracking:** $HWM_t = \max(HWM_{t-1}, P_{\text{peak}})$
* **Trailing SL Target:** $\text{Candidate\_SL} = \text{round}(HWM_t - \text{USER\_V2\_TRAILING\_SL\_DISTANCE\_CENTS}, 4)$
* **Exchange Order Elevation:**
  When $\text{Candidate\_SL} > \text{Current Resting SL Order Price}$:
  ```python
  # 1. Cancel existing Stop Loss Sell Order on exchange
  client.cancel(order_id=pos["SL_Order_Id"])
  
  # 2. Place elevated Stop Loss Sell Order on exchange
  new_sl_resp = client.post_order(side="SELL", price=Candidate_SL, size=pos["Filled_Quantity"])
  pos["SL_Order_Id"] = new_sl_resp["orderID"]
  pos["Stop_Loss_Price"] = Candidate_SL
  ```

---

### **E. One-Cancels-the-Other (OCO) Reconciliation**
* **If TP Sell Order Fills:**
  - Receive WebSocket fill notification for `TP_Order_Id`.
  - Instantly call `client.cancel(order_id=pos["SL_Order_Id"])` to remove the resting SL order.
  - Transition position status to `CLOSED`, log PnL, and send Telegram notification.
* **If SL Sell Order Fills:**
  - Receive WebSocket fill notification for `SL_Order_Id`.
  - Instantly call `client.cancel(order_id=pos["TP_Order_Id"])` to remove the resting TP order.
  - Transition position status to `CLOSED`, log PnL, and send Telegram notification.

---

## 4. Summary of Configuration Settings (Preserved in `src/config.py`)

```python
USER_EXECUTION_MODE = "DRY_RUN"               # Toggle to "LIVE" for real trading
USER_V2_MOMENTUM_THRESHOLD_CENTS = 0.15      # 15-cent momentum surge
USER_V2_MOMENTUM_WINDOW_SEC = 10.0           # 10s lookback window
USER_V2_MIN_ENTRY_ODDS_FLOOR = 0.65          # $0.65 minimum odds floor
USER_V2_TAKE_PROFIT_CENTS = 0.20             # $0.20 Take Profit target
USER_V2_HIGH_ODDS_CUTOFF = 0.80             # $0.80 Tier 2 cutoff
USER_V2_HIGH_ODDS_TP_TARGET = 0.995         # $0.995 Tier 2 TP target
USER_V2_TRAILING_SL_ENABLED = True          # Enable HWM Trailing SL
USER_V2_TRAILING_SL_DISTANCE_CENTS = 0.10   # 10-cent trailing distance from HWM
USER_V3_MAKER_OFFSET_CENTS = 0.02             # 2-cent Maker offset below Best Ask
USER_V3_MAKER_ORDER_TIMEOUT_SEC = 5.0         # 5.0 seconds order timeout
```
