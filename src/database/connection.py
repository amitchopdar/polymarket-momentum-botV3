"""
PolyDB Database Manager and Asynchronous Connection Framework (US0.1, US4.1)
"""

import os
import time
import sqlite3
import threading
import queue
import logging
from typing import Generator, Tuple, Any, Optional, List
from contextlib import contextmanager
from .schema import create_tables

logger = logging.getLogger(__name__)

class PolyDBManager:
    """
    Manages SQLite database connections with WAL mode and PRAGMA settings.
    """

    def __init__(self, db_path: str = "PolyDB.sqlite", busy_timeout_ms: int = 30000):
        self.db_path = os.path.abspath(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self._local = threading.local()

    def get_raw_connection(self) -> sqlite3.Connection:
        """
        Creates and configures a raw sqlite3 connection with WAL mode and pragmas.
        """
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000.0)
        conn.row_factory = sqlite3.Row
        
        # Configure Pragmas for high performance & concurrency (US0.1)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms};")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.close()
        create_tables(conn)
        return conn

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager that provides a thread-local SQLite connection.
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self.get_raw_connection()
        
        try:
            yield self._local.conn
        except Exception:
            self._local.conn.rollback()
            raise

    def init_db(self) -> None:
        """
        Initializes schema tables in PolyDB.sqlite.
        """
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True) if os.path.dirname(self.db_path) else None
        with self.get_connection() as conn:
            create_tables(conn)

    def close_thread_connection(self) -> None:
        """
        Closes the thread-local connection if open.
        """
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception as e:
                logger.error(f"Error closing DB connection: {e}")
            finally:
                self._local.conn = None


class AsyncDBWriter:
    """
    Asynchronous background queue writer for non-blocking database operations.
    """

    def __init__(self, db_manager: PolyDBManager):
        self.db_manager = db_manager
        self.write_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """
        Starts the background database writer thread.
        """
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._run, daemon=True, name="AsyncDBWriterThread")
            self._worker_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """
        Stops the worker thread safely after flushing remaining items in queue.
        """
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

    def enqueue_write(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        """
        Pushes a write query into the async queue (non-blocking).
        """
        self.write_queue.put((sql, params))

    def checkpoint(self) -> None:
        """
        Enqueues a FULL WAL checkpoint query to flush all WAL entries to PolyDB.sqlite disk immediately.
        """
        self.enqueue_write("PRAGMA wal_checkpoint(FULL);")

    def flush_and_checkpoint(self, timeout: float = 5.0) -> None:
        """
        Synchronously waits for all pending queued write queries to be executed by the worker thread,
        then executes a FULL WAL checkpoint to flush all entries to disk instantly.
        """
        self.write_queue.join()
        self.checkpoint()
        self.write_queue.join()

    def _run(self) -> None:
        """
        Main worker thread loop processing write queries with locked retry resilience.
        """
        conn = self.db_manager.get_raw_connection()
        while not self._stop_event.is_set() or not self.write_queue.empty():
            try:
                try:
                    sql, params = self.write_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                success = False
                for attempt in range(5):
                    try:
                        cursor = conn.cursor()
                        cursor.execute(sql, params)
                        conn.commit()
                        success = True
                        break
                    except sqlite3.OperationalError as op_err:
                        if "locked" in str(op_err).lower():
                            time.sleep(0.2 * (attempt + 1))
                        else:
                            logger.error(f"Error executing async DB write: {op_err} | Query: {sql}")
                            conn.rollback()
                            break
                    except Exception as e:
                        logger.error(f"Error executing async DB write: {e} | Query: {sql}")
                        conn.rollback()
                        break

                if not success and 'op_err' in locals():
                    conn.rollback()

                self.write_queue.task_done()
            except Exception as outer_e:
                logger.error(f"Unexpected error in AsyncDBWriter loop: {outer_e}")

        try:
            conn.close()
        except Exception:
            pass
