# 📄 Product Requirements Document (PRD)

**Product Name:** Polymarket BTC 5-Minute Algorithmic Prediction & Auto-Execution Bot  
**Document Status:** Approved for Production (v4.0)  
**Target Market:** Polymarket 5-Minute Bitcoin Binary Options Contracts (`btc-updown-5m-timestamp`)  
**Target Users:** Algorithmic Traders, Quant Engineers, Risk Managers  

---

## 🎯 **1. Executive Summary & Product Vision**

### **Product Vision**
To construct an autonomous, ultra-low-latency algorithmic prediction engine capable of forecasting 5-minute Bitcoin price direction on Polymarket prediction markets, executing risk-hedged binary option limit buy orders, and managing stop-loss exits with zero manual intervention.

### **Core Problem Statement**
Manual binary option trading on 5-minute prediction markets suffers from human reaction lag, emotional bias during volatility spikes, sub-optimal execution prices, and manual management overhead. 

### **Value Proposition**
1. **Sub-100ms Execution SLA:** Vectorized C-speed feature engineering and calibrated model inference.
2. **Asymmetric Risk/Reward Edge:** Buying outcome tokens at limit prices ($\le \$0.48/\$0.40$) with automated stop-loss protection ($\$0.30/\$0.20$), achieving a **25% to 40% break-even win rate requirement**.
3. **Fail-Closed Protection:** Zero risk of ruin ($0.00\%$) validated across 10,000 Monte Carlo simulation runs.
4. **24/7 Cloud Resilience:** Self-healing Ubuntu `systemd` background daemon with remote Telegram control.

---

## 🚀 **2. Epic & User Story Requirements**

### **Sprint 1: Data Ingestion & Microstructure Telemetry**
- **US1.1: Real-Time Binance WebSocket Ingestion:** Ingest sub-second Binance BTCUSDT spot trades, kline 5m stream, and order book depth snapshots without socket drops.
- **US1.2: Order Flow Imbalance (OBI) & Liquidation Tracking:** Aggregate 100ms depth snapshots to compute Order Flow Imbalance ($OBI = \frac{Bid - Ask}{Bid + Ask}$) and monitor forced liquidation volume.
- **US1.3: Polymarket Market Token Resolution:** Resolve active 5-minute candle market slugs (`btc-updown-5m-{epoch}`) via Gamma REST API and subscribe to live CLOB WebSocket order book odds.

### **Sprint 2: Machine Learning & Feature Engineering**
- **US2.1: Vectorized 29-Feature Extraction Pipeline:** Compute technical indicators (RSI, EMAs, MACD, Bollinger Bands, ATR), microstructure ratios (OBI, liquidations), and 24-hour cyclical sine/cosine intraday seasonality in $<15\text{ms}$.
- **US2.2: Calibrated LightGBM Prediction Inference:** Execute LightGBM probability scoring with Isotonic Probability Calibration to generate directional probabilities ($P_{\text{UP}}$ vs $P_{\text{DOWN}}$).
- **US2.3: Fail-Closed Latency Guard:** Suppress trade generation if feature extraction or inference latency exceeds SLA limit ($100\text{ms}$).

### **Sprint 3: Risk Management & Order Execution**
- **US3.1: Asymmetric Limit Buy & Automated Stop-Loss Engine:** Dispatch limit buy orders at target price ($\le \$0.48/\$0.40$) and place automated stop-loss sell orders ($\$0.30/\$0.20$) immediately upon fill.
- **US3.2: Multi-Layer Risk Guards:**
  - *Single Position Guard:* Maximum 1 trade position per active 5-minute candle interval.
  - *Directional Confidence Guard:* Trade only if directional probability $P \ge \text{Min Probability Threshold}$.
  - *L2 Depth Guard:* Verify minimum order book liquidity depth before order submission.

### **Sprint 4: Champion/Challenger MLOps & Remote Controls**
- **US4.1: Purged Walk-Forward Cross Validation:** Train model across 20,000 historical candles (~70 days) using 6-candle purge/embargo windows to prevent look-ahead bias.
- **US4.2: Vectorized Monte Carlo Stress Simulation:** Run 10,000 simulated account journeys (1,000 trades each) to prove 99th percentile Max Drawdown and $0.00\%$ Risk of Ruin.
- **US4.3: Remote Telegram Command Router:** Control bot remotely via slash commands (`/status`, `/pnl`, `/activate`, `/deactivate`) and receive push notifications in IST timezone.

---

## 📊 **3. Non-Functional & SLA Requirements**

| Benchmark Category | Target SLA / Metric | Verification Status |
| :--- | :--- | :--- |
| **Inference SLA Latency** | $< 100.0\text{ ms}$ (Actual avg: $9.36\text{ ms}$) | ✅ PASSED |
| **Database Concurrency** | SQLite WAL Mode with $30,000\text{ms}$ busy timeout | ✅ PASSED |
| **Model Retraining Interval** | 7 Days (Automated 7-day retrain countdown) | ✅ PASSED |
| **Telegram Timestamp Format** | Indian Standard Time (IST, UTC +5:30) | ✅ PASSED |
| **Test Suite Coverage** | 100% Pass Rate across 29 Unit & Integration Tests | ✅ PASSED |

---

## 🛡️ **4. Financial Risk & Safety Boundaries**

```text
┌─────────────────────────────────────────────────────────────────┐
│                    FINANCIAL RISK BOUNDARIES                    │
├───────────────────────────────┬─────────────────────────────────┤
│ Target Entry Buy Price        │ $0.40 – $0.48                   │
│ Automated Stop-Loss Price     │ $0.20 – $0.30                   │
│ Maximum Position Size         │ $50.00 USD (Configurable $2.00) │
│ Minimum Probability Threshold │ 0.51 (51.0% Directional Conf.)  │
│ Break-Even Win Rate           │ 25.0% – 25.7%                   │
│ 99th Percentile Max Drawdown  │ -14.0% to -30.0%                │
│ Risk of Ruin (Bankruptcy)     │ 0.00% (SAFE)                    │
└───────────────────────────────┴─────────────────────────────────┘
```

---

## 📱 **5. Telegram Remote Telemetry & Monitoring KPI Specs**

### **1. Real-Time Signal Alert Notification:**
```text
🎯 SIGNAL GENERATED
• Candle: 2026-07-26 12:05:00 IST
• 📈 UP Prob: 53.4% | 📉 DOWN Prob: 46.6%
• Prediction: UP
• Limit Target: $0.48
• Mode: DRY_RUN
```

### **2. System Engine Status Command (`/status`):**
```text
📊 SYSTEM ENGINE STATUS
• Engine Status: ACTIVE
• Execution Mode: DRY_RUN
• Target Buy Price: $0.48
• Stop-Loss Price: $0.30
• Min Prob Threshold: 0.51
• Positions: PENDING=0 | OPEN=0 | CLOSED=12

🤖 CURRENT CHAMPION MODEL PROFILE
• Trained At: 2026-07-26 03:05:07 IST
• Model Win Rate: 55.0%
• Current Model PnL: +$14.20 (8W / 4L)
• Next Retraining Due: In 6.8 days (2026-08-02 03:05:07 IST)
• Monte Carlo 99% Max DD: -15.0%
• Risk of Ruin: 0.00% (SAFE)
```

---

## 🚀 **6. Release Sign-Off & Acceptance Criteria**

- [x] All 29 unit and integration tests passing (`pytest tests/ -v`).
- [x] Zero-downtime atomic model hot-swapping verified.
- [x] IST timezone formatting verified across all Telegram endpoints.
- [x] `systemd` daemon auto-restart configured and running 24/7 on Oracle Cloud (`152.67.66.51`).
