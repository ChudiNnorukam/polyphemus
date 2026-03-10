"""
SQLite database for funding bot
Tracks positions, funding history, and bot status
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class FundingBotDatabase:
    """SQLite database for funding bot tracking"""

    def __init__(self, db_path: str = "data/funding.db"):
        """
        Initialize database

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info(f"Database initialized at {db_path}")

    def _init_schema(self):
        """Create tables if they don't exist"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Positions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    spot_qty REAL NOT NULL,
                    perp_qty REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    exit_price REAL,
                    funding_collected REAL DEFAULT 0.0,
                    fees_paid REAL DEFAULT 0.0,
                    pnl REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'OPEN',
                    exit_reason TEXT,
                    spot_order_id TEXT,
                    perp_order_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Funding history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS funding_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    funding_rate REAL NOT NULL,
                    funding_payment REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(position_id) REFERENCES positions(id)
                )
            """)

            # Bot status table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    balance REAL NOT NULL,
                    equity REAL NOT NULL,
                    open_positions INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0.0,
                    total_funding_collected REAL DEFAULT 0.0,
                    uptime_seconds INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'RUNNING',
                    created_at TEXT NOT NULL
                )
            """)

            # Funding rates table for analysis
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS funding_rates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    current_rate REAL NOT NULL,
                    next_rate REAL NOT NULL,
                    avg_7d_rate REAL NOT NULL,
                    apy REAL NOT NULL,
                    consecutive_positive INTEGER DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # Errors table for debugging
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    symbol TEXT,
                    traceback TEXT,
                    timestamp TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            conn.commit()
            logger.debug("Database schema initialized")

    def add_position(
        self,
        position_id: str,
        symbol: str,
        entry_price: float,
        spot_qty: float,
        perp_qty: float,
        entry_time: datetime,
        spot_order_id: Optional[str] = None,
        perp_order_id: Optional[str] = None
    ) -> bool:
        """
        Add a new position to database

        Args:
            position_id: Unique position ID
            symbol: Trading pair
            entry_price: Entry price
            spot_qty: Spot quantity
            perp_qty: Perp quantity
            entry_time: Entry datetime
            spot_order_id: Spot order ID
            perp_order_id: Perp order ID

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO positions (
                        id, symbol, entry_price, spot_qty, perp_qty,
                        entry_time, spot_order_id, perp_order_id,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position_id,
                    symbol,
                    entry_price,
                    spot_qty,
                    perp_qty,
                    entry_time.isoformat(),
                    spot_order_id,
                    perp_order_id,
                    "OPEN",
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                logger.debug(f"Position {position_id} added to database")
                return True
        except Exception as e:
            logger.error(f"Error adding position: {e}")
            self.add_error("add_position", str(e), symbol)
            return False

    def update_position_exit(
        self,
        position_id: str,
        exit_price: float,
        exit_time: datetime,
        pnl: float,
        fees_paid: float,
        funding_collected: float,
        exit_reason: str
    ) -> bool:
        """
        Update position with exit information

        Args:
            position_id: Position ID
            exit_price: Exit price
            exit_time: Exit datetime
            pnl: Profit/loss
            fees_paid: Total fees
            funding_collected: Funding payments collected
            exit_reason: Reason for exit

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE positions
                    SET exit_price = ?, exit_time = ?, pnl = ?,
                        fees_paid = ?, funding_collected = ?,
                        exit_reason = ?, status = ?
                    WHERE id = ?
                """, (
                    exit_price,
                    exit_time.isoformat(),
                    pnl,
                    fees_paid,
                    funding_collected,
                    exit_reason,
                    "CLOSED",
                    position_id
                ))
                conn.commit()
                logger.debug(f"Position {position_id} exit recorded")
                return True
        except Exception as e:
            logger.error(f"Error updating position exit: {e}")
            self.add_error("update_position_exit", str(e))
            return False

    def get_open_positions(self) -> List[Dict]:
        """
        Get all open positions

        Returns:
            List of position dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM positions WHERE status = 'OPEN'")
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting open positions: {e}")
            return []

    def get_position(self, position_id: str) -> Optional[Dict]:
        """
        Get specific position by ID

        Args:
            position_id: Position ID

        Returns:
            Position dictionary or None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM positions WHERE id = ?", (position_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting position: {e}")
            return None

    def add_funding_payment(
        self,
        position_id: str,
        symbol: str,
        funding_rate: float,
        funding_payment: float
    ) -> bool:
        """
        Record funding payment

        Args:
            position_id: Position ID
            symbol: Trading pair
            funding_rate: Funding rate
            funding_payment: Payment amount

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO funding_history (
                        position_id, symbol, funding_rate, funding_payment,
                        timestamp, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    position_id,
                    symbol,
                    funding_rate,
                    funding_payment,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding funding payment: {e}")
            return False

    def add_funding_rate(
        self,
        symbol: str,
        current_rate: float,
        next_rate: float,
        avg_7d_rate: float,
        apy: float,
        consecutive_positive: int
    ) -> bool:
        """
        Record funding rate snapshot

        Args:
            symbol: Trading pair
            current_rate: Current funding rate
            next_rate: Next funding rate
            avg_7d_rate: 7-day average rate
            apy: Annualized APY
            consecutive_positive: Count of consecutive positive periods

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO funding_rates (
                        symbol, current_rate, next_rate, avg_7d_rate,
                        apy, consecutive_positive, timestamp, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    current_rate,
                    next_rate,
                    avg_7d_rate,
                    apy,
                    consecutive_positive,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding funding rate: {e}")
            return False

    def add_bot_status(
        self,
        balance: float,
        equity: float,
        open_positions: int,
        total_pnl: float,
        total_funding_collected: float,
        uptime_seconds: int,
        status: str = "RUNNING"
    ) -> bool:
        """
        Record bot status snapshot

        Args:
            balance: Current USDT balance
            equity: Total equity
            open_positions: Number of open positions
            total_pnl: Total P&L
            total_funding_collected: Total funding collected
            uptime_seconds: Bot uptime
            status: Bot status

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO bot_status (
                        timestamp, balance, equity, open_positions,
                        total_pnl, total_funding_collected,
                        uptime_seconds, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    balance,
                    equity,
                    open_positions,
                    total_pnl,
                    total_funding_collected,
                    uptime_seconds,
                    status,
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding bot status: {e}")
            return False

    def get_total_pnl(self) -> float:
        """
        Calculate total P&L from all closed positions

        Returns:
            Total P&L
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT SUM(pnl) as total FROM positions WHERE status = 'CLOSED'"
                )
                result = cursor.fetchone()
                return result[0] if result[0] is not None else 0.0
        except Exception as e:
            logger.error(f"Error calculating total PnL: {e}")
            return 0.0

    def get_total_funding_collected(self) -> float:
        """
        Calculate total funding collected

        Returns:
            Total funding collected
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT SUM(funding_collected) as total FROM positions WHERE status = 'CLOSED'"
                )
                result = cursor.fetchone()
                return result[0] if result[0] is not None else 0.0
        except Exception as e:
            logger.error(f"Error calculating total funding: {e}")
            return 0.0

    def add_error(
        self,
        error_type: str,
        message: str,
        symbol: Optional[str] = None,
        traceback: Optional[str] = None
    ) -> bool:
        """
        Record error for debugging

        Args:
            error_type: Type of error
            message: Error message
            symbol: Associated symbol if applicable
            traceback: Full traceback

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO errors (
                        error_type, message, symbol, traceback,
                        timestamp, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    error_type,
                    message,
                    symbol,
                    traceback,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding error record: {e}")
            return False

    def get_stats(self) -> Dict:
        """
        Get overall bot statistics

        Returns:
            Dictionary with stats
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get position stats
                cursor.execute("""
                    SELECT
                        COUNT(*) as total_positions,
                        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_positions,
                        COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_positions,
                        SUM(CASE WHEN status = 'CLOSED' THEN pnl ELSE 0 END) as total_pnl,
                        SUM(CASE WHEN status = 'CLOSED' THEN funding_collected ELSE 0 END) as total_funding,
                        AVG(CASE WHEN status = 'CLOSED' THEN pnl ELSE NULL END) as avg_pnl
                    FROM positions
                """)
                stats = cursor.fetchone()

                return {
                    "total_positions": stats[0] or 0,
                    "open_positions": stats[1] or 0,
                    "closed_positions": stats[2] or 0,
                    "total_pnl": stats[3] or 0.0,
                    "total_funding_collected": stats[4] or 0.0,
                    "avg_pnl": stats[5] or 0.0
                }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}
