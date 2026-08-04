# Polymarket BTC-5min Prediction Bot: User Stories

The following user stories are derived from the project Epic (`EPIC.md`) and are ordered chronologically based on the sprint planning to ensure independent development and testing. All storage requirements strictly utilize the single `PolyDB.sqlite` database in Write-Ahead Logging (WAL) mode to maintain high-throughput telemetry and transactional consistency.

---

## Sprint 0: Database Schema & Foundation

### US0.1: As a Database Administrator, I want to create the PolyDB SQLite database in WAL mode so that I can support concurrent read/write operations without locking the main thread.
* **Description:** Initialize the main `PolyDB.sqlite` database file and explicitly enable Write-Ahead Logging (WAL) mode along with connection busy timeouts (`PRAGMA busy_timeout = 5000;`) to ensure high-frequency telemetry writes do not block the ML inference loop or cause `SQLITE_BUSY` errors.
* **Acceptance Criteria:** 
  * A `PolyDB.sqlite` file is created.
  * Querying `PRAGMA journal_mode;` returns `wal`.
  * Querying `PRAGMA busy_timeout;` returns `5000`.
  * Database connections are managed via a thread-safe connection pool with WAL checkpointing executed asynchronously.

### US0.2: As a Database Administrator, I want to create the BTC_OHCLV table so that I can store high-frequency Bitcoin market data and order flow telemetry.
* **Description:** Initialize the `BTC_OHCLV` table in `PolyDB.sqlite` to record 5-minute candlestick data alongside order flow metrics like OBI and liquidations.
* **Acceptance Criteria:** The table is successfully created with all specified columns, data types, and primary keys.
* **Column Definitions & Update Logic:**
    *   **Candle_Start** (DATETIME, PRIMARY KEY):
        *   **Definition:** The exact opening timestamp of the 5-minute candle.
        *   **Update Logic:** Inserted once when the candle is finalized. Never updated.
    *   **Interval** (TEXT, NOT NULL):
        *   **Definition:** The timeframe of the candle (e.g., '5m').
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Open** (REAL, NOT NULL):
        *   **Definition:** The opening price of the candle.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **High** (REAL, NOT NULL):
        *   **Definition:** The highest price during the candle's duration.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Low** (REAL, NOT NULL):
        *   **Definition:** The lowest price during the candle's duration.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Close** (REAL, NOT NULL):
        *   **Definition:** The closing price of the candle.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Volume** (REAL, NOT NULL):
        *   **Definition:** The total trading volume during the candle.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Obi** (REAL, NOT NULL):
        *   **Definition:** Order Book Imbalance calculated over the 5-minute interval.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Short_Liq_Vol** (REAL, NOT NULL):
        *   **Definition:** The total volume of short liquidations that occurred during the candle.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Long_Liq_Vol** (REAL, NOT NULL):
        *   **Definition:** The total volume of long liquidations that occurred during the candle.
        *   **Update Logic:** Inserted at creation. Never updated.

### US0.3: As a Database Administrator, I want to create the Odds_OHCLV table so that I can track minute-by-minute Polymarket token pricing aligned with Bitcoin candles.
* **Description:** Initialize the `Odds_OHCLV` table in `PolyDB.sqlite` to record the price movements (OHCLV) of the Polymarket UP and DOWN tokens associated with each 5-minute Bitcoin candle.
* **Acceptance Criteria:** The table is successfully created with all specified columns for tracking overarching 5-minute token OHCLV as well as minute-by-minute high/low tracking.
* **Column Definitions & Update Logic:**
    *   **Candle_Start** (DATETIME, PRIMARY KEY):
        *   **Definition:** The exact opening timestamp of the associated 5-minute Bitcoin candle.
        *   **Update Logic:** Inserted once when the candle data is finalized. Never updated.
    *   **Up_Token_Id** (TEXT, NOT NULL):
        *   **Definition:** The unique Polymarket token identifier for the UP prediction.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Up_Open** (REAL), **Up_High** (REAL), **Up_Low** (REAL), **Up_Close** (REAL), **Up_Volume** (REAL):
        *   **Definition:** The 5-minute OHCLV metrics for the UP token.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Down_Token_Id** (TEXT, NOT NULL):
        *   **Definition:** The unique Polymarket token identifier for the DOWN prediction.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **Down_Open** (REAL), **Down_High** (REAL), **Down_Low** (REAL), **Down_Close** (REAL), **Down_Volume** (REAL):
        *   **Definition:** The 5-minute OHCLV metrics for the DOWN token.
        *   **Update Logic:** Inserted at creation. Never updated.
    *   **1_Min_Up_High**, **1_Min_Up_Low**, **1_Min_Down_High**, **1_Min_Down_Low**, etc. (REAL):
        *   **Definition:** Minute-by-minute tracking of the high and low prices for both UP and DOWN tokens during the 5-minute window for minutes 1 through 5.
        *   **Update Logic:** Inserted at creation. Never updated.

### US0.4: As a Database Administrator, I want to create the Positions table so that I can meticulously track trade executions, model confidence, order status, and historical profitability.
* **Description:** Initialize the `Positions` table in `PolyDB.sqlite` to log predictive signals, entry/exit executions, partial fills, order timeouts, and financial PnL for each trade cycle.
* **Acceptance Criteria:** The table is successfully created with columns to support asynchronous trade updates from entry to exit, including partial fills and order cancellation tracking.
* **Column Definitions & Update Logic:**
    *   **Candle_Start** (DATETIME, PRIMARY KEY):
        *   **Definition:** The timestamp of the candle that triggered the prediction. Serves as a unique identifier for the trade cycle.
        *   **Update Logic:** Inserted upon entry order creation. Never updated.
    *   **Prob_Cal** (REAL, NOT NULL):
        *   **Definition:** The calibrated probability output by the model.
        *   **Update Logic:** Inserted upon entry. Never updated.
    *   **Prob_Uncal** (REAL, NOT NULL):
        *   **Definition:** The raw, uncalibrated probability output by the model.
        *   **Update Logic:** Inserted upon entry. Never updated.
    *   **Slug** (TEXT, NOT NULL):
        *   **Definition:** The human-readable string identifier of the Polymarket market.
        *   **Update Logic:** Inserted upon entry. Never updated.
    *   **Prediction_Side** (TEXT, NOT NULL):
        *   **Definition:** The side of the market predicted by the model (e.g., 'UP', 'DOWN').
        *   **Update Logic:** Inserted upon entry. Never updated.
    *   **Entry_Timestamp** (DATETIME, NOT NULL):
        *   **Definition:** The exact time the entry order was submitted.
        *   **Update Logic:** Inserted upon entry. Never updated.
    *   **Target_Price** (REAL, NOT NULL):
        *   **Definition:** The target price per share for the entry order.
        *   **Update Logic:** Inserted upon entry. Never updated.
    *   **Target_Quantity** (REAL, NOT NULL):
        *   **Definition:** The number of shares targeted for purchase.
        *   **Update Logic:** Inserted upon entry order creation.
    *   **Filled_Quantity** (REAL, DEFAULT 0.0):
        *   **Definition:** The number of shares successfully purchased.
        *   **Update Logic:** Updated asynchronously once order fills are confirmed by Polymarket WS/REST.
    *   **Average_Fill_Price** (REAL):
        *   **Definition:** Volume-weighted average filled price per share.
        *   **Update Logic:** Calculated and updated upon order fill confirmation.
    *   **Order_Id** (TEXT):
        *   **Definition:** The exchange-provided ID for the entry transaction.
        *   **Update Logic:** Updated once the order is accepted by the exchange.
    *   **Position_Status** (TEXT, NOT NULL):
        *   **Definition:** The current state of the trade (e.g., 'PENDING', 'OPEN', 'PARTIAL_FILL', 'CLOSED', 'CANCELLED', 'FAILED').
        *   **Update Logic:** Inserted as 'PENDING', updated dynamically as the trade lifecycle progresses.
    *   **Cancel_Reason** (TEXT):
        *   **Definition:** Reason for order cancellation or failure (e.g., 'TIMEOUT_EXPIRED', 'INSUFFICIENT_LIQUIDITY', 'SLIPPAGE_EXCEEDED').
        *   **Update Logic:** Updated if the entry order is cancelled before fill.
    *   **Transaction_Price** (REAL):
        *   **Definition:** Total transaction value executed for logging the initial spend.
        *   **Update Logic:** Updated upon fill confirmation (`Filled_Quantity` * `Average_Fill_Price`).
    *   **Exit_Price** (REAL):
        *   **Definition:** The price per share at which the position was closed.
        *   **Update Logic:** Updated when the position is successfully closed.
    *   **Exit_Reason** (TEXT):
        *   **Definition:** The trigger for the exit (e.g., 'STOP_LOSS', 'TAKE_PROFIT', 'END_OF_CANDLE', 'MANUAL_DEACTIVATE').
        *   **Update Logic:** Updated upon exit.
    *   **Pnl** (REAL):
        *   **Definition:** The finalized Profit and Loss amount for the trade.
        *   **Update Logic:** Computed and updated after the exit order is filled.
    *   **Updated_At** (DATETIME, NOT NULL):
        *   **Definition:** Timestamp of the last row modification.
        *   **Update Logic:** Automatically updated by the application layer on any `UPDATE` statement applied to the row.

---

## Sprint 1: Real-Time Foundation (In-Memory Ingestion & Storage)

### US1.1.1: As an Engine, I want an in-memory rolling candlestick cache with connection resilience so that I can compute technical features instantly at the candle boundary without network delay or data loss.
* **Description:** The bot must initiate background threads connecting to Binance Futures WebSockets on start. It must parse incoming candle payloads, monitor connection health with ping/pong heartbeats, automatically reconnect with exponential backoff (1s to 30s) upon disconnection, detect sequence gaps, backfill missing historical candles via REST on reconnect, and update an in-memory double-ended queue.
* **Acceptance Criteria:** 
  * When a 5-minute candle closes, the in-memory deque must contain the finalized candle at index `[-1]` within 500ms of actual close.
  * If the WebSocket connection drops, an auto-reconnect is attempted within 1s (doubling up to 30s max).
  * Upon reconnection, missing candles during the offline window are automatically backfilled via Binance REST API before inference resumes.
  * Websocket ping/pong heartbeats execute every 15 seconds; stale connections (>30s no frame) trigger an immediate reconnect.
* **Tasks:**
  * Develop WebSocket aggregation wrapper with ping/pong heartbeat monitors.
  * Implement circular deque-based in-memory caching.
  * Integrate REST-based warmup bootstrap and backfill gap-filling sequence.

### US1.1.2: As a system, I want to robustly identify and persist finalized candles to ensure no data is lost from high-frequency streams.
* **Description:** Implement a resilient finalization mechanism that stores incoming candlestick updates in an in-memory cache. Finalization happens explicitly (`"x": true`) or implicitly (new candle start time).
* **Acceptance Criteria:** 
  * All real-time updates are stored in-memory.
  * Persist on explicit `"x": true`.
  * Persist implicitly on new candle start time.
  * Database write operations to `PolyDB.sqlite` are non-blocking via an asynchronous writer queue.
* **Tasks:**
  * Implement implicit finalization logic based on incoming timestamps.
  * Integrate circular cache storage garbage collection.
  * Wire asynchronous queue writer for `PolyDB.sqlite`.

### US1.2.1: As an Analyst, I want continuous tracking of order-flow variables with reconnect resilience so that I can feed volume and imbalance indicators into the predictive model without gaps.
* **Description:** Connect to Binance `@depth10@100ms` and `@forceOrder` liquidation feeds with automatic ping/pong connection monitoring. Compute Order Book Imbalance (OBI) on a high-frequency loop and increment liquidation totals. Flush at the 5-minute boundary.
* **Acceptance Criteria:** At the 5-minute interval conclusion, combined liquidation buffers match the sum of individual events processed, OBI is correctly computed to 4 decimal places, and stream disconnections trigger clean reconnects without throwing uncaught exceptions.
* **Tasks:**
  * Implement `@depth10@100ms` parsing and calculation logic.
  * Implement `@forceOrder` liquidation logging.
  * Construct 5-minute boundary flushing routines.

### US4.1: As a Database Administrator, I want to manage high-volume telemetry and trade state within PolyDB.sqlite via an asynchronous queue so that write activities never lock the database or slow down model execution.
* **Description:** Maintain `PolyDB.sqlite` in WAL mode as the single unified storage engine. Route all write operations (candle telemetry, order flow, and trade position updates) through a thread-safe, asynchronous background writing queue with `PRAGMA busy_timeout = 5000;`.
* **Acceptance Criteria:** Given continuous high-frequency telemetry logging, when a trade execution signal simultaneously updates the `Positions` table in `PolyDB.sqlite`, both writes must complete cleanly via the queue without raising `SQLITE_BUSY` or database lock errors, keeping main thread latency impact under 5ms.
* **Tasks:**
  * Setup thread-safe SQLite connection pool in WAL mode with `busy_timeout = 5000`.
  * Implement background asynchronous database writing queue.
  * Verify non-blocking execution under simultaneous telemetry and position update loads.

---

## Sprint 2: Core ML Engine & Smart Pre-Flight Sync

### US1.3.1: As an Execution Trader, I want the bot to pre-calculate and subscribe to Polymarket token IDs before the new candle opens, with fallback retries, so that I do not miss critical initial entry liquidity.
* **Description:** Query the Polymarket API 5 seconds prior to the 5-minute clock boundary ($T-5s$) to retrieve exact contract addresses. At $T+0s$, fire a WS unsubscribe for old tokens and subscribe for new tokens. If the pre-flight request fails or returns unindexed tokens, execute an immediate fallback retry at $T+0s$.
* **Acceptance Criteria:** 
  * Given clock is 11:59:55 ($T-5s$), the pre-flight routine fetches and validates next-candle token IDs.
  * If $T-5s$ fetch succeeds, token IDs are primed in memory for execution at 12:00:00.
  * If $T-5s$ fetch fails or times out, a fallback REST request fires at 12:00:00 ($T+0s$) with a 2-second timeout.
  * If token resolution fails after fallback, signal generation for the candle cycle is safely skipped and logged.
* **Tasks:**
  * Implement Polymarket token ID pre-calculation logic at $T-5s$.
  * Build $T+0s$ fallback retry and unindexed market validator.
  * Build dynamic WebSocket unsubscribe/subscribe packet assembly.
  * Write clock synchronizer matching NTP clock drifts.

### US2.1: As a Quantitative Developer, I want to calculate model features using optimized vector processes so that processing times are kept under 40 milliseconds.
* **Description:** Convert in-memory candlestick deques directly into multi-dimensional NumPy arrays and perform vectorized technical indicator calculations in C-speed arrays using single-pass concatenation.
* **Acceptance Criteria:** Given a valid candle history, when the feature calculation is executed, then the execution must complete in < 40ms and produce a zero-loss identical array to the offline training standard.
* **Tasks:**
  * Port technical indicator calculations from Pandas to raw vectorized NumPy.
  * Implement dynamic circular buffer array wrappers.
  * Write unit tests verifying vector outputs against Pandas baselines.

### US2.2: As an Algorithmic Trader, I want calibrated prediction probabilities with a Fail-Closed safety policy so that I only take trades when the model possesses a high-confidence mathematical edge and valid data.
* **Description:** Wrap the trained LightGBM model with a single-pass probability calibrator. Model outputs a calibrated probability $P_{cal}$ and determines confidence tier. Implement a **Fail-Closed** guard: if feature calculation contains `NaN`/`Null`/Inf values, feature computation exceeds 40ms, or model inference throws an exception, set $P_{cal} = 0.0$ and suppress order generation.
* **Acceptance Criteria:** 
  * Given a valid feature array, inference completes in < 40ms with accurate calibrated probability output $P_{cal}$.
  * If feature array contains `NaN`, `Inf`, or missing values, the system logs a `FEATURE_INVALID` warning, sets $P_{cal} = 0.0$, and skips trade execution.
  * If inference execution times out (>40ms), the system logs an `INFERENCE_TIMEOUT` event and suppresses signals.
* **Tasks:**
  * Integrate LightGBM inference within bot lifecycle.
  * Implement Isotonic Calibration curve lookup wrapper.
  * Implement Fail-Closed validation layer for inputs and inference execution.

---

## Sprint 3: Safe Execution & System Orchestration

### US3.1: As an Operations Manager, I want to easily toggle between dry-run and live trading using a single configuration parameter so that I can control financial risk dynamically.
* **Description:** Create an `IExecutionStrategy` interface with concrete `DryExecutionStrategy` (simulating order fills and slippage) and `LiveExecutionStrategy` (Polymarket CLOB integration).
* **Acceptance Criteria:** Given dry_run is set to true, when a signal is triggered, then no external order calls are made to Polymarket, and a simulated entry is written to `PolyDB.sqlite` `Positions` table.
* **Tasks:**
  * Define `IExecutionStrategy` interface.
  * Implement `DryExecutionStrategy` simulating fills and random slippage.
  * Implement `LiveExecutionStrategy` cryptographic order-signing and CLOB REST transmission.

### US3.2: As a Risk Officer, I want my bot to place limit entry orders with liquidity depth verification, persistent execution tracking, an automated 20-cent stop-loss, and a single position per candle guard so that risk is strictly controlled.
* **Description:** Before submitting an entry limit order, verify L2 order book depth for adequate liquidity. Place a limit buy order at target price (e.g. $0.40) and keep it active tracking for execution throughout the 5-minute candle (no 5-second auto-cancel). Limit execution to a maximum of 1 buy position per active 5-minute candle. The exact moment a buy position is filled (`Filled_Quantity` > 0), place an immediate stop-loss limit sell order at $0.20 per contract and monitor position state continuously.
* **Acceptance Criteria:** 
  * Given a trade signal, L2 order book depth is checked; if available liquidity < target quantity, entry is aborted or scaled down.
  * Maximum of 1 buy order / position is allowed per active 5-minute candle interval.
  * Entry limit buy order is dispatched at target price ($0.40) and tracked for execution throughout the 5-minute candle.
  * As soon as a buy position is filled (full or partial fill), an automated stop-loss limit sell order at $0.20 per contract is submitted instantly (<1s) and monitored throughout the active candle.
  * If token price drops to $0.20, stop-loss sell execution completes in under 1 second.
* **Tasks:**
  * Implement L2 order book depth check and single position per active candle guard.
  * Build persistent limit buy tracking engine ($0.40 entry limit order).
  * Build partial-fill tracking and automated $0.20 stop-loss limit sell order dispatch module within `RiskEngine`.

### US3.3: As an Execution Trader, I want cold-start position reconciliation and state recovery on bot startup so that active trades and candlestick deques are restored seamlessly after a restart without duplicate orders.
* **Description:** On bot startup, query Polymarket CLOB REST API and `PolyDB.sqlite` `Positions` table to reconcile open positions, sync pending order states, resume stop-loss monitoring for active trades, and backfill in-memory candle deques via Binance REST API.
* **Acceptance Criteria:** 
  * Upon bot boot, existing `OPEN` or `PARTIAL_FILL` positions in `PolyDB.sqlite` are reconciled against live Polymarket exchange orders.
  * Active trades missing stop-loss protection have stop-loss orders re-issued immediately.
  * In-memory candlestick deques are populated with historical candles via REST API before turning on signal evaluation.
  * No duplicate entry orders are issued for an already active candle or position cycle.
* **Tasks:**
  * Implement `StateReconciler` module for boot startup sequence.
  * Build Polymarket REST API position/order auditor.
  * Integrate boot-sequence candle deque warmer.

### US4.2: As an Operator, I want to receive live updates of bot signals and closed trades via Telegram with rate-limiting so that I can monitor profitability in real time without notification drops.
* **Description:** Establish a non-blocking Telegram API gateway with an asynchronous dispatch queue and rate limiter (max 30 msgs/sec) to send signal generation, fill updates, and trade closure reports.
* **Acceptance Criteria:** Given a closed position, a formatted Telegram message (e.g. "Trade Closed: UP | Entry $0.40 | Exit $1.00 | PnL: +150%") is queued and dispatched asynchronously within 2 seconds without blocking execution threads or exceeding API rate limits.
* **Tasks:**
  * Write `notifier.py` async dispatch queue with rate limiting.
  * Format template-rich messages using HTML markdown styling.

### US4.3: As an Operator, I want to interact with the bot via Telegram commands (/start, /activate, /deactivate, /status, /pnl, /dryrun, /help) so that I can control and monitor the bot remotely.
* **Description:** Implement command handlers responding to slash commands, adapting to the environment via Webhooks or Polling, restricted to authorized Telegram user IDs.
* **Acceptance Criteria:** Slash commands successfully trigger their respective actions via polling or webhook, restricted to authorized user IDs, returning current system status, open position metrics, and aggregate PnL.
* **Tasks:**
  * Setup Webhook receiver endpoint using Flask.
  * Build long-polling fallback loop.
  * Implement slash commands parser, router, and authorization middleware.

---

## Sprint 4: Asynchronous Model Training & Lifecycle Automation

### US5.1: As a Quantitative Researcher, I want an automated, leakage-free feature and label generation job so that every training dataset is guaranteed consistent with what the live bot actually observes at decision time.
* **Description:** Reuse the production `VectorPipeline` module to replay historical data from `PolyDB.sqlite` into feature vectors identically to the live pipeline, attach labels, and apply exponential recency weights.
* **Acceptance Criteria:** Given a configured lookback window, when the dataset build job runs, then it produces a versioned dataset whose feature-parity checksum matches the live pipeline and whose row-level timestamps are strictly monotonic with no gaps.
* **Tasks:**
  * Setup dataset build job executing weekly.
  * Integrate shared vectorized pipeline imports.
  * Write parity-validation hashes.

### US5.2: As a Quantitative Researcher, I want purged walk-forward cross-validation with automated hyperparameter optimization so that the promoted model maximizes out-of-sample win rate without overfitting to historical noise.
* **Description:** Split dataset into purged walk-forward folds; run Optuna study; refit isotonic calibration; score final candidate against untouched holdout.
* **Acceptance Criteria:** Given a registered dataset, when the training job completes, then a calibrated candidate model with an attached holdout evaluation report is registered with status `CANDIDATE`.
* **Tasks:**
  * Implement purged, embargoed walk-forward splitter.
  * Implement composite objective Optuna optimizer.
  * Write calibration fitting routines.

### US5.3: As a Site Reliability Engineer, I want retraining to run as an isolated, resource-capped process on a hybrid schedule so that live trading latency and throughput are never affected by model training workloads.
* **Description:** Run the full training pipeline as an isolated subprocess/container with configured CPU and memory ceilings, scheduled on a weekly baseline plus event-driven triggers.
* **Acceptance Criteria:** Live inference latency measurements taken during the run show zero measurable deviation from the pre-training baseline, and the bot's decision loop in `PolyDB.sqlite` is never blocked.
* **Tasks:**
  * Write CLI scripts to execute decoupled training pipelines.
  * Implement weekly scheduler loop using subprocess wrappers.

### US5.4: As an ML Platform Engineer, I want a versioned champion/challenger model registry with atomic promotion and instant rollback so that only validated models serve live predictions and any regression can be reversed immediately.
* **Description:** Maintain an immutable, versioned model registry. Candidates clear a holdout gate and a shadow-mode soak period before atomic promotion.
* **Acceptance Criteria:** When promotion executes, then the live inference engine begins using the new model on the very next candle boundary with zero added latency, and a rollback can take effect within a single candle cycle.
* **Tasks:**
  * Build model registry directory structure.
  * Implement double-buffered pointer swapping in prediction core.
  * Write manual and automatic rollback hooks.

### US5.5: As a Risk Manager, I want continuous live tracking of prediction accuracy, calibration, and feature drift so that retraining is triggered automatically when real performance degrades rather than waiting on a fixed calendar schedule.
* **Description:** Compute feature-drift (PSI), calibration error, and win rate from `PolyDB.sqlite` trade logs. Compare against expectations and raise retraining or rollback alerts.
* **Acceptance Criteria:** A retraining-trigger or rollback-alert event is emitted within one monitoring cycle upon sustained threshold breaches, with zero measurable added latency to live predictions.
* **Tasks:**
  * Implement feature distribution tracking loops.
  * Implement Brier score calibration metric calculator.
  * Build trigger events dispatcher hooks.
