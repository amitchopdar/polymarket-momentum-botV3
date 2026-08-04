# Polymarket Bot V3 - Advanced Prediction Market Engine

High-frequency, tick-by-tick odds momentum and custom strategy execution engine for Polymarket 5-minute Bitcoin prediction markets.

---

## 🚀 Environment & Database Isolation

* **Isolated Mac Workspace:** `/Users/kamalasahu/polymarket-bot-v3`
* **Isolated Database:** `PolyDB_V3.sqlite` (Keeps V3 position records 100% separate from V2 and V1).
* **Virtual Environment:** `./venv/`

---

## 📖 Local Mac Execution Commands

```bash
cd /Users/kamalasahu/polymarket-bot-v3
PYTHONPATH=. ./venv/bin/pytest tests/ -v
PYTHONPATH=. python3 main.py
```
