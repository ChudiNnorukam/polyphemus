"""SignalLogger — SQLite feature logger for ALL momentum signals.

Logs every signal with full feature vectors to data/signals.db.
This is the foundation for ML-based signal scoring and backtesting.

Features captured per signal:
- Momentum metrics (pct, duration, direction)
- Market microstructure (midpoint, spread, book depth)
- Timing (time_remaining, hour_utc, minute_utc)
- Asset metadata (asset, window_secs)
- Regime state (from RegimeDetector if available)
- Outcome (executed, filtered, skipped, win, loss)
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from .config import setup_logger


class SignalLogger:
    """Logs all signals with feature vectors to SQLite for ML training."""

    def __init__(self, db_path: str = "data/signals.db"):
        self._logger = setup_logger("polyphemus.signal_logger")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()
        self._logger.info(f"SignalLogger initialized: {self._db_path}")

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                epoch REAL NOT NULL,

                -- Signal identification
                slug TEXT NOT NULL,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                token_id TEXT,

                -- Momentum features (from Binance)
                momentum_pct REAL,
                momentum_window_secs INTEGER,
                price_start REAL,
                price_end REAL,

                -- Market microstructure (from CLOB)
                midpoint REAL,
                spread REAL,
                book_depth_bid REAL,
                book_depth_ask REAL,
                book_imbalance REAL,

                -- Timing features
                time_remaining_secs INTEGER,
                market_window_secs INTEGER,
                hour_utc INTEGER,
                minute_utc INTEGER,
                day_of_week INTEGER,

                -- Order flow (from Binance WS)
                vpin_5m REAL,
                taker_delta REAL,

                -- Regime (from RegimeDetector)
                regime TEXT,
                volatility_1h REAL,
                trend_1h REAL,

                -- Signal guard result
                guard_passed INTEGER,
                guard_reasons TEXT,

                -- Execution outcome
                outcome TEXT,
                entry_price REAL,
                fill_time_ms INTEGER,
                fill_mode TEXT,

                -- Trade result (updated after exit)
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL,
                pnl_pct REAL,
                hold_secs INTEGER,
                is_win INTEGER,

                -- Scorer output (if available)
                signal_score REAL,
                score_threshold REAL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_slug ON signals(slug)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_asset ON signals(asset)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_epoch ON signals(epoch)
        """)
        self._conn.commit()

        # Epoch coverage: log every 5m epoch for analysis of missed opportunities
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS epoch_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch INTEGER NOT NULL,
                asset TEXT NOT NULL,
                window_secs INTEGER NOT NULL DEFAULT 300,
                oracle_open_price REAL,
                oracle_close_price REAL,
                oracle_delta_pct REAL,
                oracle_direction TEXT,
                binance_open_price REAL,
                binance_close_price REAL,
                binance_delta_pct REAL,
                bot_saw_signal INTEGER DEFAULT 0,
                bot_signal_source TEXT,
                resolved_outcome TEXT,
                resolved_price REAL,
                timestamp TEXT NOT NULL,
                UNIQUE(epoch, asset, window_secs)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_epoch_coverage_epoch ON epoch_coverage(epoch)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_epoch_coverage_asset ON epoch_coverage(asset)
        """)
        self._conn.commit()

        # Additive migrations — try/except pattern (ALTER TABLE IF NOT EXISTS not valid in SQLite)
        _migration_cols = [
            ("strategy_type", "TEXT"), ("pair_cost", "REAL"), ("source", "TEXT"),
            ("fear_greed", "REAL"), ("market_regime", "TEXT"),
            ("oi_change_pct", "REAL"), ("oi_trend", "TEXT"),
            ("dry_run", "INTEGER"),
            ("vpin_5m", "REAL"), ("taker_delta", "REAL"),
        ]
        for col_name, col_def in _migration_cols:
            try:
                self._conn.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_def}")
                self._logger.info(f"Migration: added column signals.{col_name}")
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def log_signal(self, features: Dict[str, Any]) -> int:
        """Log a signal with its feature vector. Returns the signal ID.

        Args:
            features: Dict with any subset of signal table columns.
                      Missing fields default to NULL.

        Returns:
            signal_id: Auto-incremented ID for this signal row.
        """
        now = datetime.now(timezone.utc)
        features.setdefault("timestamp", now.isoformat())
        features.setdefault("epoch", now.timestamp())
        features.setdefault("hour_utc", now.hour)
        features.setdefault("minute_utc", now.minute)
        features.setdefault("day_of_week", now.weekday())

        columns = list(features.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_str = ", ".join(columns)
        values = [features[c] for c in columns]

        try:
            cursor = self._conn.execute(
                f"INSERT INTO signals ({col_str}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()
            signal_id = cursor.lastrowid
            self._logger.debug(
                f"Signal logged: id={signal_id} slug={features.get('slug', '?')} "
                f"outcome={features.get('outcome', 'pending')}"
            )
            return signal_id
        except Exception as e:
            self._logger.error(f"Failed to log signal: {e}")
            return -1

    def update_signal(self, signal_id: int, updates: Dict[str, Any]):
        """Update a signal row (e.g., after execution or exit).

        Args:
            signal_id: The signal row ID from log_signal().
            updates: Dict of column→value to update.
        """
        if signal_id < 0:
            return
        set_clauses = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [signal_id]

        try:
            self._conn.execute(
                f"UPDATE signals SET {set_clauses} WHERE id = ?",
                values,
            )
            self._conn.commit()
        except Exception as e:
            self._logger.error(f"Failed to update signal {signal_id}: {e}")

    def log_epoch(self, epoch: int, asset: str, window_secs: int = 300,
                  oracle_open: float = None, binance_open: float = None,
                  bot_saw_signal: bool = False, bot_signal_source: str = None):
        """Log the start of a new epoch for coverage analysis."""
        now = datetime.now(timezone.utc)
        try:
            self._conn.execute("""
                INSERT OR IGNORE INTO epoch_coverage
                    (epoch, asset, window_secs, oracle_open_price, binance_open_price,
                     bot_saw_signal, bot_signal_source, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (epoch, asset, window_secs, oracle_open, binance_open,
                  1 if bot_saw_signal else 0, bot_signal_source, now.isoformat()))
            self._conn.commit()
        except Exception as e:
            self._logger.error(f"Failed to log epoch {epoch}/{asset}: {e}")

    def update_epoch_outcome(self, epoch: int, asset: str, window_secs: int = 300,
                             oracle_close: float = None, oracle_delta_pct: float = None,
                             oracle_direction: str = None, binance_close: float = None,
                             binance_delta_pct: float = None,
                             resolved_outcome: str = None, resolved_price: float = None,
                             bot_saw_signal: bool = None, bot_signal_source: str = None):
        """Update epoch with close/resolution data at epoch end."""
        updates = {}
        if oracle_close is not None:
            updates["oracle_close_price"] = oracle_close
        if oracle_delta_pct is not None:
            updates["oracle_delta_pct"] = oracle_delta_pct
        if oracle_direction is not None:
            updates["oracle_direction"] = oracle_direction
        if binance_close is not None:
            updates["binance_close_price"] = binance_close
        if binance_delta_pct is not None:
            updates["binance_delta_pct"] = binance_delta_pct
        if resolved_outcome is not None:
            updates["resolved_outcome"] = resolved_outcome
        if resolved_price is not None:
            updates["resolved_price"] = resolved_price
        if bot_saw_signal is not None:
            updates["bot_saw_signal"] = 1 if bot_saw_signal else 0
        if bot_signal_source is not None:
            updates["bot_signal_source"] = bot_signal_source
        if not updates:
            return
        set_clauses = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [epoch, asset, window_secs]
        try:
            self._conn.execute(
                f"UPDATE epoch_coverage SET {set_clauses} WHERE epoch = ? AND asset = ? AND window_secs = ?",
                values,
            )
            self._conn.commit()
        except Exception as e:
            self._logger.error(f"Failed to update epoch {epoch}/{asset}: {e}")

    def get_training_data(self, min_signals: int = 50) -> Optional[list]:
        """Get labeled signals for ML training.

        Returns signals that have been fully resolved (have exit data).
        Returns None if fewer than min_signals are available.
        """
        cursor = self._conn.execute("""
            SELECT
                momentum_pct, midpoint, spread, book_imbalance,
                time_remaining_secs, hour_utc, day_of_week,
                volatility_1h, trend_1h,
                CASE WHEN asset = 'BTC' THEN 1 ELSE 0 END as is_btc,
                CASE WHEN asset = 'ETH' THEN 1 ELSE 0 END as is_eth,
                CASE WHEN asset = 'SOL' THEN 1 ELSE 0 END as is_sol,
                CASE WHEN direction = 'Up' THEN 1 ELSE 0 END as is_up,
                market_window_secs,
                COALESCE(vpin_5m, 0.5) as vpin_5m,
                is_win
            FROM signals
            WHERE is_win IS NOT NULL
              AND momentum_pct IS NOT NULL
              AND midpoint IS NOT NULL
            ORDER BY epoch ASC
        """)
        rows = cursor.fetchall()

        if len(rows) < min_signals:
            self._logger.info(
                f"Not enough labeled signals for training: "
                f"{len(rows)}/{min_signals}"
            )
            return None

        return rows

    def get_feature_columns(self) -> list:
        """Return the ordered list of feature column names for ML."""
        return [
            "momentum_pct", "midpoint", "spread", "book_imbalance",
            "time_remaining_secs", "hour_utc", "day_of_week",
            "volatility_1h", "trend_1h",
            "is_btc", "is_eth", "is_sol", "is_up",
            "market_window_secs",
            "vpin_5m",
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Return signal logging statistics for dashboard."""
        cursor = self._conn.execute("""
            SELECT
                COUNT(*) as total_signals,
                SUM(CASE WHEN outcome = 'executed' THEN 1 ELSE 0 END) as executed,
                SUM(CASE WHEN outcome = 'filtered' THEN 1 ELSE 0 END) as filtered,
                SUM(CASE WHEN outcome = 'skipped' THEN 1 ELSE 0 END) as skipped,
                SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN is_win = 0 THEN 1 ELSE 0 END) as losses,
                AVG(signal_score) as avg_score,
                AVG(CASE WHEN is_win = 1 THEN signal_score END) as avg_win_score,
                AVG(CASE WHEN is_win = 0 THEN signal_score END) as avg_loss_score
            FROM signals
        """)
        row = cursor.fetchone()
        if not row:
            return {}

        return {
            "total_signals": row[0] or 0,
            "executed": row[1] or 0,
            "filtered": row[2] or 0,
            "skipped": row[3] or 0,
            "wins": row[4] or 0,
            "losses": row[5] or 0,
            "avg_score": round(row[6], 2) if row[6] else None,
            "avg_win_score": round(row[7], 2) if row[7] else None,
            "avg_loss_score": round(row[8], 2) if row[8] else None,
        }

    def close(self):
        """Close the database connection."""
        self._conn.close()
