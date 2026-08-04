# 🛠️ Technical Specifications Document & Developer Onboarding Guide

**Project:** Polymarket BTC 5-Minute Algorithmic Prediction & Auto-Execution Bot  
**Architecture Version:** 4.0.0 (Production Candidate)  
**Language & Runtime:** Python 3.10+ / 3.14 (Mac OS & Ubuntu 22.04 LTS)  
**Target Market:** Polymarket 5-Minute Bitcoin Binary Options (`btc-updown-5m-timestamp`)  
**Data Sources:** Binance Futures WebSocket/REST (`BTCUSDT`), Polymarket Gamma API, Polymarket CLOB WebSocket

---

## 📐 **1. System Architecture Overview**

The Polymarket Prediction Bot is an ultra-low-latency, decoupled, multi-threaded algorithmic trading engine. It ingests sub-second market data from Binance and Polymarket, computes a 29-feature vectorized matrix, runs a calibrated LightGBM classifier with Isotonic Probability Calibration, enforces strict EV risk guards, and manages non-blocking limit order execution and stop-loss trailing.

```
                  ┌──────────────────────────────────────────────┐
                  │          Real-Time Data Ingestion            │
                  │  Binance WS (OHCLV, Depth, ForceOrders)     │
                  │  Polymarket CLOB WS (Order Book Bids/Asks)   │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │       Engine Pipeline & In-Memory Cache      │
                  │   CandleCache (500) | OrderFlowTracker (OBI)│
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │      Vectorized Feature Pipeline (29 Feat)   │
                  │   RSI, EMAs, MACD, BB, ATR, Seasonality, OBI │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │       LightGBM Inference Engine (SLA<100ms)  │
                  │ Isotonic Calibration -> Directional P_cal     │
                  └──────────────────────┬───────────────────────┘
                                         │
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │           Risk Engine & Execution            │
                  │  Confidence Guard (>=0.51) | L2 Depth Guard  │
                  │  Limit Buy ($0.48/$0.40) | Stop-Loss ($0.30) │
                  └──────────────────────┬───────────────────────┘
                                         │
                ┌────────────────────────┴────────────────────────┐
                ▼                                                 ▼
┌───────────────────────────────┐                 ┌───────────────────────────────┐
│     PolyDB Manager (SQLite)   │                 │    Telegram Remote Router     │
│   WAL Mode | Async Writer     │                 │   Slash Commands & Push Alerts│
└───────────────────────────────┘                 └───────────────────────────────┘
```

---

## 📁 **2. Codebase Directory & Module Structure**

```text
polymarket-bot/
├── main.py                          # Main orchestrator & real-time execution loop
├── src/
│   ├── config.py                    # Centralized AppConfig (Single Source of Truth)
│   ├── database/
│   │   ├── connection.py            # PolyDBManager (SQLite WAL) & AsyncDBWriter thread
│   │   └── schema.sql               # SQLite DDL tables (BTC_OHCLV, Odds_OHCLV, Positions)
│   ├── ingestion/
│   │   ├── binance_ws.py            # Binance WebSocket Client (Auto-reconnect & exponential backoff)
│   │   ├── candle_cache.py          # Ring-buffer deque cache & wall-clock candle finalizer
│   │   └── order_flow.py            # Microstructure Order Flow Imbalance (OBI) & Liquidation aggregator
│   ├── polymarket/
│   │   ├── token_resolver.py        # Gamma REST API token resolution & slug generator
│   │   └── polymarket_ws.py         # Polymarket CLOB WebSocket market data stream
│   ├── ml/
│   │   ├── features.py              # Vectorized 29-feature NumPy pipeline
│   │   ├── trainer.py               # Purged Walk-Forward CV, Optuna & Monte Carlo Simulator
│   │   ├── predictor.py             # Calibrated LightGBM predictor & Fail-Closed SLA Guard
│   │   ├── dataset_builder.py       # Offline dataset matrix builder from PolyDB.sqlite
│   │   └── registry.py              # Champion/Challenger ModelRegistry & atomic hotswapper
│   ├── execution/
│   │   ├── strategy.py              # Dry-Run & Live Execution Strategies (Entry/Exit/StopLoss)
│   │   ├── risk_engine.py           # Risk Guards (L2 depth, single position/candle, confidence)
│   │   └── reconciler.py            # Startup state reconciler (Recovers open/pending orders)
│   └── notifications/
│       ├── notifier.py              # Non-blocking async Telegram notification worker & IST formatter
│       └── telegram_bot.py          # Remote Slash Command Router (/status, /pnl, /activate, /deactivate)
├── scripts/
│   ├── bootstrap_history.py         # Binance REST API historical candle paginator (20,000 candles)
│   └── train_model.py               # Offline retraining CLI runner
├── models/
│   └── lgbm_model.pkl               # Production champion model artifact bundle
├── tests/                           # 29 Pytest unit & integration test files
│   ├── test_database.py
│   ├── test_ingestion.py
│   ├── test_ml.py
│   ├── test_execution.py
│   ├── test_notifications.py
│   ├── test_polymarket.py
│   └── test_sprint4_ml.py
└── requirements.txt                 # Project Python dependencies
```

---

## 🧮 **3. Vectorized Feature Pipeline Specification (`src/ml/features.py`)**

The feature extraction pipeline operates in pure NumPy vectorized C-speed arrays, computing 29 numerical features in $<15\text{ ms}$:

| Feature Index | Symbol Name | Mathematical Formula / Description |
| :--- | :--- | :--- |
| `0` | `close_price` | Current candle closing price |
| `1` | `return_1m` | 1-candle fractional log return: $\ln(C_t / C_{t-1})$ |
| `2` | `return_3m` | 3-candle fractional log return |
| `3` | `return_5m` | 5-candle fractional log return |
| `4` | `ema_9` | 9-period Exponential Moving Average |
| `5` | `ema_21` | 21-period Exponential Moving Average |
| `6` | `ema_50` | 50-period Exponential Moving Average |
| `7` | `ema_200` | 200-period Exponential Moving Average |
| `8` | `dist_ema9` | Fractional distance to EMA-9: $(C_t - \text{EMA}_9) / \text{EMA}_9$ |
| `9` | `dist_ema21` | Fractional distance to EMA-21 |
| `10` | `dist_ema50` | Fractional distance to EMA-50 |
| `11` | `rsi_14` | 14-period Relative Strength Index ($0 - 100$) |
| `12` | `macd_diff` | MACD Histogram Difference: $\text{MACD}_{\text{line}} - \text{Signal}_{\text{line}}$ |
| `13` | `atr_14` | 14-period Average True Range |
| `14` | `bb_upper` | Bollinger Band Upper: $\text{SMA}_{20} + 2\cdot\sigma_{20}$ |
| `15` | `bb_lower` | Bollinger Band Lower: $\text{SMA}_{20} - 2\cdot\sigma_{20}$ |
| `16` | `bb_pct_b` | Bollinger Band Percent B: $(C_t - \text{BB}_{\text{lower}}) / (\text{BB}_{\text{upper}} - \text{BB}_{\text{lower}})$ |
| `17` | `volume_sma20` | 20-period Simple Moving Average of Volume |
| `18` | `rel_volume` | Relative Volume Ratio: $V_t / \text{SMA}_{20}(V)$ |
| `19` | `obi_ratio` | Order Flow Imbalance Ratio: $(\text{Bid}_{\text{vol}} - \text{Ask}_{\text{vol}}) / (\text{Bid}_{\text{vol}} + \text{Ask}_{\text{vol}})$ |
| `20` | `buy_liquidation_vol`| Aggregated buy liquidation volume in past 5m window |
| `21` | `sell_liquidation_vol`| Aggregated sell liquidation volume in past 5m window |
| `22` | `net_liquidation_ratio`| Net liquidation pressure: $(V_{\text{buy\_liq}} - V_{\text{sell\_liq}}) / (V_{\text{buy\_liq}} + V_{\text{sell\_liq}} + 1\text{e-6})$ |
| `23` | `candle_body_ratio` | Candle body to total range ratio: $|C - O| / (H - L + 1\text{e-6})$ |
| `24` | `upper_wick_ratio` | Upper wick length ratio: $(H - \max(O, C)) / (H - L + 1\text{e-6})$ |
| `25` | `utc_hour` | UTC hour of day ($0 - 23$) |
| `26` | `utc_day_of_week` | UTC day of week ($0 - 6$, Monday=0) |
| `27` | `sin_hour` | 24-hour cyclical sine encoding: $\sin(2\pi \cdot \text{hour} / 24)$ |
| `28` | `cos_hour` | 24-hour cyclical cosine encoding: $\cos(2\pi \cdot \text{hour} / 24)$ |

---

## 🤖 **4. Machine Learning & Probability Calibration Engine (`src/ml/`)**

### **Purged Walk-Forward Cross-Validation (`src/ml/trainer.py`)**
To prevent financial data leakage, the training pipeline utilizes Purged Walk-Forward Cross Validation:
- **Purge Window (6 candles / 30m):** Strips overlapping sample boundaries preceding validation splits.
- **Embargo Window (6 candles / 30m):** Strips post-validation splits to eliminate serial autocorrelation.

### **Isotonic Probability Calibration**
Raw LightGBM outputs ($P_{\text{uncal}}$) are mapped via `sklearn.isotonic.IsotonicRegression(out_of_bounds="clip")` into calibrated probabilities ($P_{\text{cal}}$):
- Directional decision logic:
  - If $P_{\text{cal}} \ge 0.50 \implies \text{Signal = UP}$, Directional Confidence = $P_{\text{cal}}$.
  - If $P_{\text{cal}} < 0.50 \implies \text{Signal = DOWN}$, Directional Confidence = $1.0 - P_{\text{cal}}$.

### **Vectorized Monte Carlo Stress Simulator (`MonteCarloSimulator`)**
Simulates 10,000 independent account journeys (1,000 trades each) in NumPy memory ($< 0.23\text{ seconds}$):
$$\text{Expected Value (EV)} = (P_{\text{win}} \cdot \text{Payout}_{\text{win}}) - ((1 - P_{\text{win}}) \cdot \text{Loss}_{\text{cost}})$$
For `$0.40` entry price and `$0.20` stop loss:
$$\text{Break-Even Win Rate} = \frac{\$0.20}{\$0.60 + \$0.20} = 25.0\%$$

---

## 🗄️ **5. Database WAL Architecture (`src/database/`)**

SQLite database (`PolyDB.sqlite`) is initialized with high-concurrency WAL mode settings:

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
PRAGMA synchronous = NORMAL;
```

### **Core Schema Tables (`src/database/schema.sql`):**

1. **`BTC_OHCLV` Table:**
   - Primary Key: `Candle_Start` (ISO UTC text timestamp).
   - Stores 5-minute spot candle metrics (`Open`, `High`, `Low`, `Close`, `Volume`, `OBI_Ratio`).

2. **`Odds_OHCLV` Table:**
   - Primary Key: (`Candle_Start`, `Slug`).
   - Tracks minute-by-minute order book odds (`Up_Token_Ask`, `Down_Token_Ask`, `Status`).

3. **`Positions` Table:**
   - Primary Key: `Position_Id` (UUID text).
   - Lifecycle Statuses: `PENDING`, `OPEN`, `CLOSED`, `CANCELLED`.
   - Tracks `Target_Quantity`, `Filled_Quantity`, `Average_Fill_Price`, `Exit_Price`, `Exit_Reason`, `Pnl`.

---

## ⚡ **6. Developer Onboarding: Setup & Verification Workflow**

For a new developer joining the project:

### **1. Environment Setup**
```bash
git clone https://github.com/amitchopdar/polymarket-bot.git
cd polymarket-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### **2. Running Unit & Integration Test Suite**
Run the complete 29-test Pytest suite:
```bash
PYTHONPATH=. pytest tests/ -v
```
*Expected Result: `29 passed in ~12.5s (100% Green)`.*

### **3. Bootstrap Historical Data & Train Champion Model**
```bash
# Fetch 20,000 historical 5-minute candles (~70 days)
PYTHONPATH=. python3 scripts/bootstrap_history.py

# Train LightGBM model with 30 Optuna trials
PYTHONPATH=. python3 scripts/train_model.py --force --trials 30
```

### **4. Local Dry-Run Execution**
```bash
PYTHONPATH=. python3 main.py
```
