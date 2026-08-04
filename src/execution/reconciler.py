"""
State Reconciler & Boot Recovery (Sprint 3: US3.3)
Restores unresolved trade states from PolyDB.sqlite on startup, re-establishes
stop-loss monitoring, and prevents duplicate trade orders.
"""

import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from src.execution.strategy import DryExecutionStrategy
from src.execution.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

class StateReconciler:
    """
    Cold-Start Boot Reconciler.
    Audits PolyDB.sqlite Positions table on startup to restore PENDING/OPEN trades,
    resume stop-loss protection, and prevent duplicate order executions.
    """

    def __init__(self, db_path: str = "PolyDB.sqlite"):
        self.db_path = db_path

    def reconcile_on_boot(
        self,
        strategy: DryExecutionStrategy,
        risk_engine: RiskEngine
    ) -> int:
        """
        Loads unresolved positions from SQLite on bot boot and populates strategy and risk engine memory.
        """
        reconciled_count = 0
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            now_sec = int(time.time())
            curr_candle_sec = (now_sec // 300) * 300
            curr_active_candle = datetime.fromtimestamp(curr_candle_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            now_dt = datetime.fromtimestamp(now_sec, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
                SELECT * FROM Positions 
                WHERE Position_Status IN ('PENDING', 'OPEN')
            """)
            rows = cursor.fetchall()

            for row in rows:
                row_dict = dict(row)
                candle_start = row_dict["Candle_Start"]
                status = row_dict["Position_Status"]

                # Only restore if position belongs to the CURRENT ACTIVE candle (or recent window)
                if candle_start == curr_active_candle or abs((datetime.strptime(candle_start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()) - curr_candle_sec) < 300:
                    strategy.active_positions[candle_start] = row_dict
                    risk_engine.executed_candles.add(candle_start)
                    reconciled_count += 1
                    logger.info(
                        f"✓ [STATE RECONCILER] Restored active trade on boot: Candle={candle_start} | "
                        f"Side={row_dict.get('Prediction_Side')} | Status={status}"
                    )
                else:
                    # Auto-close stale past candle position in SQLite
                    cursor.execute("""
                        UPDATE Positions SET Position_Status = 'CLOSED', Exit_Reason = 'EXPIRED_BOOT', Updated_At = ?
                        WHERE Candle_Start = ?
                    """, (now_dt, candle_start))
                    conn.commit()
                    logger.info(
                        f"✓ [STATE RECONCILER] Auto-closed stale past trade on boot: Candle={candle_start} | "
                        f"Side={row_dict.get('Prediction_Side')} | Status=CLOSED (EXPIRED_BOOT)"
                    )

            conn.close()
            logger.info(f"✓ [STATE RECONCILER] Cold-start reconciliation complete. Total active restored positions: {reconciled_count}")

        except Exception as e:
            logger.error(f"⚠ Error executing StateReconciler on boot: {e}", exc_info=True)

        return reconciled_count
