"""
SQLite database for tracking liquidations and performance metrics.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class LiquidationDatabase:
    """Manages liquidation tracking database."""

    def __init__(self, db_path: str):
        """Initialize database."""
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Liquidations table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS liquidations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user TEXT NOT NULL,
                collateral_asset TEXT NOT NULL,
                debt_asset TEXT NOT NULL,
                debt_amount REAL NOT NULL,
                tx_hash TEXT UNIQUE,
                status TEXT DEFAULT 'pending',
                estimated_profit REAL,
                actual_profit REAL,
                gas_cost REAL,
                error_msg TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Opportunities table (all scanned opportunities)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                user TEXT NOT NULL,
                total_collateral_usd REAL,
                total_debt_usd REAL,
                health_factor REAL,
                liquidatable INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Health check logs
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS health_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                uptime_seconds INTEGER,
                borrowers_scanned INTEGER,
                liquidatable_found INTEGER,
                liquidations_executed INTEGER,
                total_profit REAL,
                balance_usdc REAL,
                error_count INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Scan metrics
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                scan_duration_ms REAL,
                borrowers_checked INTEGER,
                liquidatable_found INTEGER,
                batch_size INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {self.db_path}")

    def log_liquidation(
        self,
        user: str,
        collateral_asset: str,
        debt_asset: str,
        debt_amount: float,
        estimated_profit: float,
        tx_hash: Optional[str] = None,
        status: str = "pending",
        error_msg: Optional[str] = None,
    ) -> int:
        """Log a liquidation attempt."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO liquidations
            (user, collateral_asset, debt_asset, debt_amount, estimated_profit, tx_hash, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (user, collateral_asset, debt_asset, debt_amount, estimated_profit, tx_hash, status, error_msg),
        )

        conn.commit()
        liquidation_id = cursor.lastrowid
        conn.close()

        return liquidation_id

    def update_liquidation_result(
        self,
        liquidation_id: int,
        status: str,
        actual_profit: Optional[float] = None,
        gas_cost: Optional[float] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        """Update liquidation result after execution."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE liquidations
            SET status = ?, actual_profit = ?, gas_cost = ?, error_msg = ?
            WHERE id = ?
        """,
            (status, actual_profit, gas_cost, error_msg, liquidation_id),
        )

        conn.commit()
        conn.close()

    def log_opportunity(
        self,
        user: str,
        total_collateral_usd: float,
        total_debt_usd: float,
        health_factor: float,
        liquidatable: bool,
    ) -> None:
        """Log a scanned opportunity."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO opportunities
            (user, total_collateral_usd, total_debt_usd, health_factor, liquidatable)
            VALUES (?, ?, ?, ?, ?)
        """,
            (user, total_collateral_usd, total_debt_usd, health_factor, int(liquidatable)),
        )

        conn.commit()
        conn.close()

    def log_scan_metric(
        self,
        scan_duration_ms: float,
        borrowers_checked: int,
        liquidatable_found: int,
        batch_size: int,
    ) -> None:
        """Log scan performance metrics."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO scan_metrics
            (scan_duration_ms, borrowers_checked, liquidatable_found, batch_size)
            VALUES (?, ?, ?, ?)
        """,
            (scan_duration_ms, borrowers_checked, liquidatable_found, batch_size),
        )

        conn.commit()
        conn.close()

    def log_health_check(
        self,
        uptime_seconds: int,
        borrowers_scanned: int,
        liquidatable_found: int,
        liquidations_executed: int,
        total_profit: float,
        balance_usdc: float,
        error_count: int,
    ) -> None:
        """Log health check status."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO health_checks
            (uptime_seconds, borrowers_scanned, liquidatable_found, liquidations_executed, total_profit, balance_usdc, error_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (uptime_seconds, borrowers_scanned, liquidatable_found, liquidations_executed, total_profit, balance_usdc, error_count),
        )

        conn.commit()
        conn.close()

    def get_liquidation_stats(self) -> Dict:
        """Get liquidation statistics."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Total liquidations
        cursor.execute("SELECT COUNT(*) as count, SUM(estimated_profit) as total_estimated FROM liquidations")
        row = cursor.fetchone()
        total_count = row["count"] or 0
        total_estimated = row["total_estimated"] or 0.0

        # Successful liquidations
        cursor.execute(
            "SELECT COUNT(*) as count, SUM(actual_profit) as total_profit FROM liquidations WHERE status = 'success'"
        )
        row = cursor.fetchone()
        successful_count = row["count"] or 0
        total_profit = row["total_profit"] or 0.0

        # Average metrics
        cursor.execute(
            "SELECT AVG(gas_cost) as avg_gas FROM liquidations WHERE status = 'success' AND gas_cost IS NOT NULL"
        )
        row = cursor.fetchone()
        avg_gas_cost = row["avg_gas"] or 0.0

        # Opportunities scanned
        cursor.execute("SELECT COUNT(*) as count FROM opportunities")
        row = cursor.fetchone()
        total_opportunities = row["count"] or 0

        conn.close()

        return {
            "total_liquidations": total_count,
            "successful_liquidations": successful_count,
            "total_estimated_profit": total_estimated,
            "total_actual_profit": total_profit,
            "average_gas_cost": avg_gas_cost,
            "opportunities_scanned": total_opportunities,
            "success_rate": (successful_count / total_count * 100) if total_count > 0 else 0,
        }

    def get_recent_liquidations(self, limit: int = 10) -> List[Dict]:
        """Get recent liquidations."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM liquidations ORDER BY created_at DESC LIMIT ?
        """,
            (limit,),
        )

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_24h_stats(self) -> Dict:
        """Get last 24 hours statistics."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Count liquidations in last 24 hours
        cursor.execute(
            """
            SELECT COUNT(*) as count, SUM(actual_profit) as total_profit
            FROM liquidations
            WHERE status = 'success' AND created_at > datetime('now', '-1 day')
        """
        )
        row = cursor.fetchone()

        conn.close()

        return {
            "liquidations_24h": row["count"] or 0,
            "profit_24h": row["total_profit"] or 0.0,
        }
