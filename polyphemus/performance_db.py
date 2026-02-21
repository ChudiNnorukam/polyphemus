"""
Polyphemus Polymarket Trading Bot - Performance Database Layer

This module provides a SQLite database layer with per-operation connections and WAL mode.
Each operation creates a fresh connection to ensure ACID compliance and prevent locking issues.
"""

import json
import sqlite3
from typing import List, Dict, Optional
from .config import setup_logger


logger = setup_logger('polyphemus.performance_db')


class PerformanceDB:
    """SQLite database layer for tracking trades with WAL mode for concurrent access."""

    def __init__(self, db_path: str):
        """
        Initialize the database layer.

        Args:
            db_path: Path to the SQLite database file (string, NOT a connection).
        """
        self.db_path = db_path
        self.logger = setup_logger('polyphemus.performance_db')
        self._init_schema()
        self._columns = self._get_existing_columns()

    def _get_conn(self) -> sqlite3.Connection:
        """
        Create a fresh SQLite connection with WAL mode enabled.

        Returns:
            sqlite3.Connection: A new connection to the database.

        Note:
            Caller is responsible for closing the connection in a finally block.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def _detect_pnl_column(self) -> str:
        """Detect whether the DB uses 'pnl' (V2) or 'profit_loss' (V1) column name."""
        conn = self._get_conn()
        try:
            cursor = conn.execute('PRAGMA table_info(trades)')
            columns = {row[1] for row in cursor.fetchall()}
            if 'pnl' in columns:
                return 'pnl'
            elif 'profit_loss' in columns:
                return 'profit_loss'
            return 'pnl'  # default for new DBs
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """
        Initialize database schema and run migrations.

        Creates tables if they don't exist and adds any missing columns (idempotent).
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # Create trades table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    token_id TEXT,
                    slug TEXT,
                    entry_time REAL,
                    entry_price REAL,
                    entry_size REAL,
                    entry_tx_hash TEXT,
                    exit_time REAL,
                    exit_price REAL,
                    exit_size REAL,
                    exit_reason TEXT,
                    exit_tx_hash TEXT,
                    pnl REAL,
                    pnl_pct REAL,
                    outcome TEXT,
                    filter_score REAL,
                    market_title TEXT,
                    is_resolved INTEGER DEFAULT 0,
                    is_redeemed INTEGER DEFAULT 0
                )
            ''')

            # Migrations: add new columns if they don't exist (idempotent)
            # Covers V1→V2 schema migration + future additions
            columns_to_add = [
                ('entry_tx_hash', 'TEXT'),
                ('exit_tx_hash', 'TEXT'),
                ('outcome', 'TEXT'),
                ('market_title', 'TEXT'),
                ('filter_score', 'REAL'),
                ('pnl_pct', 'REAL'),
                ('is_resolved', 'INTEGER DEFAULT 0'),
                ('is_redeemed', 'INTEGER DEFAULT 0'),
                ('side', "TEXT DEFAULT 'BUY'"),
                ('exit_size', 'REAL'),
                ('strategy', "TEXT DEFAULT 'signal_bot'"),
                ('entry_amount', 'REAL DEFAULT 0'),
                ('profit_loss', 'REAL'),
                ('profit_loss_pct', 'REAL'),
                ('exit_amount', 'REAL'),
                ('hold_seconds', 'INTEGER'),
                ('pnl', 'REAL'),
                ('metadata', 'TEXT DEFAULT NULL'),
            ]

            for col_name, col_def in columns_to_add:
                try:
                    cursor.execute(f'ALTER TABLE trades ADD COLUMN {col_name} {col_def}')
                    self.logger.info(f'Migration: added column {col_name}')
                except sqlite3.OperationalError:
                    # Column already exists, skip
                    pass

            conn.commit()
            self.logger.info('Database schema initialized')
        finally:
            conn.close()

    def _get_existing_columns(self) -> set:
        """Return set of column names in trades table."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(trades)")
            return {row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

    def record_entry(
        self,
        trade_id: str,
        token_id: str,
        slug: str,
        entry_time: float,
        entry_price: float,
        entry_size: float,
        entry_tx_hash: str,
        outcome: str,
        market_title: str,
        filter_score: float = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Record a new trade entry.

        Args:
            trade_id: Unique identifier for the trade.
            token_id: Token ID from Polymarket.
            slug: Market slug.
            entry_time: Entry timestamp (Unix epoch seconds).
            entry_price: Entry price (0.0 - 1.0).
            entry_size: Size of position in shares.
            entry_tx_hash: Transaction hash of the entry order.
            outcome: Outcome name (e.g., 'YES', 'NO').
            market_title: Human-readable market title.
            filter_score: Signal quality score from XGBoost model (0-100).
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            metadata_json = json.dumps(metadata) if metadata else None
            cursor.execute('''
                INSERT INTO trades (
                    trade_id, token_id, slug, entry_time, entry_price, entry_size,
                    entry_tx_hash, outcome, market_title, is_resolved, is_redeemed,
                    side, entry_amount, strategy, filter_score, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_id, token_id, slug, entry_time, entry_price, entry_size,
                entry_tx_hash, outcome, market_title, 0, 0,
                'BUY', entry_price * entry_size, 'signal_bot', filter_score, metadata_json
            ))
            conn.commit()
            self.logger.info(f'Recorded entry: {trade_id} @ {entry_price:.4f} x {entry_size} score={filter_score}')
        finally:
            conn.close()

    def record_exit(
        self,
        trade_id: str,
        exit_time: float,
        exit_price: float,
        exit_size: float,
        exit_reason: str,
        exit_tx_hash: str,
        pnl: float,
        pnl_pct: float
    ) -> None:
        """
        Record the exit of a trade.

        Args:
            trade_id: Trade ID to update.
            exit_time: Exit timestamp (Unix epoch seconds).
            exit_price: Exit price (0.0 - 1.0).
            exit_size: Size exited in shares.
            exit_reason: Reason for exit (e.g., 'profit_target', 'market_resolved').
            exit_tx_hash: Transaction hash of the exit order.
            pnl: Profit/loss in USDC.
            pnl_pct: Profit/loss as percentage (0.0 - 1.0).
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # Base columns (always exist)
            updates = {
                'exit_time': exit_time,
                'exit_price': exit_price,
                'exit_reason': exit_reason,
                'exit_tx_hash': exit_tx_hash,
            }

            # V1 columns (legacy)
            if 'profit_loss' in self._columns:
                updates['profit_loss'] = pnl
            if 'profit_loss_pct' in self._columns:
                updates['profit_loss_pct'] = pnl_pct
            if 'exit_amount' in self._columns:
                updates['exit_amount'] = exit_price * exit_size
            if 'hold_seconds' in self._columns:
                updates['hold_seconds'] = None  # Will be set via SQL expression

            # V2 columns
            if 'pnl' in self._columns:
                updates['pnl'] = pnl
            if 'pnl_pct' in self._columns:
                updates['pnl_pct'] = pnl_pct
            if 'exit_size' in self._columns:
                updates['exit_size'] = exit_size

            # Build dynamic UPDATE
            set_clauses = []
            values = []
            for col, val in updates.items():
                if col == 'hold_seconds':
                    set_clauses.append(f"hold_seconds = CAST(? - entry_time AS INTEGER)")
                    values.append(exit_time)
                else:
                    set_clauses.append(f"{col} = ?")
                    values.append(val)

            values.append(trade_id)  # WHERE clause
            sql = f"UPDATE trades SET {', '.join(set_clauses)} WHERE trade_id = ?"
            cursor.execute(sql, values)
            conn.commit()
            self.logger.info(f'Recorded exit: {trade_id} @ {exit_price:.4f} | P&L: ${pnl:.2f} ({pnl_pct*100:.1f}%)')
        finally:
            conn.close()

    def force_close_trade(self, slug: str, exit_reason: str, exit_price: float = 0.0) -> bool:
        """
        Force-close a trade by slug, bypassing PerformanceTracker lookup.

        Used for ghost positions where record_exit fails because the trade
        was from a prior bot instance and isn't tracked by PerformanceTracker.

        Args:
            slug: Market slug to close (e.g., 'btc-updown-5m-1770944400')
            exit_reason: Reason for closing (e.g., 'market_resolved', 'ghost_cleanup')
            exit_price: Exit price (default 0.0 for expired markets)

        Returns:
            True if a trade was updated, False if no matching open trade found
        """
        conn = self._get_conn()
        try:
            import time as _time
            now = _time.time()
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE trades SET
                    exit_time = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    exit_tx_hash = 'force_closed',
                    pnl = 0.0,
                    pnl_pct = 0.0,
                    hold_seconds = CAST(? - entry_time AS INTEGER)
                WHERE slug = ? AND exit_time IS NULL""",
                (now, exit_price, exit_reason, now, slug)
            )
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self.logger.info(f'Force-closed trade: {slug} | reason={exit_reason}')
            else:
                self.logger.debug(f'No open trade found for slug: {slug}')
            return updated
        finally:
            conn.close()

    def get_open_trades(self) -> List[Dict]:
        """
        Retrieve all open (non-exited) trades.

        Returns:
            List of dictionaries with trade data. Each dict has keys:
            trade_id, token_id, slug, entry_time, entry_price, entry_size, etc.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM trades WHERE exit_time IS NULL')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """
        Retrieve the most recent trades (open and closed).

        Args:
            limit: Maximum number of trades to return.

        Returns:
            List of trade dictionaries ordered by entry_time DESC.
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?',
                (limit,)
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_daily_pnl(self, utc_date) -> float:
        """Sum of realized P&L for all trades exited on a given UTC date.

        Only counts closed trades (exit_time IS NOT NULL).
        Open/unrealized positions are excluded.

        Args:
            utc_date: A datetime.date object for the UTC day to query.

        Returns:
            Cumulative P&L in USDC (negative = loss).
        """
        from datetime import datetime as dt, timezone as tz
        pnl_col = self._detect_pnl_column()
        start_epoch = int(dt.combine(utc_date, dt.min.time(), tzinfo=tz.utc).timestamp())
        end_epoch = start_epoch + 86400
        conn = self._get_conn()
        try:
            row = conn.execute(
                f"SELECT COALESCE(SUM({pnl_col}), 0.0) FROM trades "
                f"WHERE exit_time >= ? AND exit_time < ?",
                (start_epoch, end_epoch)
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        """
        Compute comprehensive trade statistics.

        Returns:
            Dictionary with keys:
            - total_trades: Count of all trades
            - winning_trades: Count of trades with pnl > 0
            - losing_trades: Count of trades with pnl < 0
            - total_profit_loss: Sum of all P&L
            - win_rate: Percentage of winning trades (0.0 - 1.0)
            - avg_profit_loss: Average P&L per trade
            - resolution_wins: Count of market_resolved exits with pnl > 0
            - resolution_losses: Count of market_resolved exits with pnl < 0
            - resolution_wr: Win rate for market_resolved exits (0.0 - 1.0)
        """
        pnl_col = self._detect_pnl_column()
        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # Total trades and P&L stats
            cursor.execute(f'''
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN {pnl_col} < 0 THEN 1 ELSE 0 END) as losing_trades,
                    SUM({pnl_col}) as total_pnl,
                    AVG({pnl_col}) as avg_pnl
                FROM trades
                WHERE exit_time IS NOT NULL
            ''')
            result = cursor.fetchone()

            total_trades = result['total_trades'] or 0
            winning_trades = result['winning_trades'] or 0
            losing_trades = result['losing_trades'] or 0
            total_pnl = result['total_pnl'] or 0.0
            avg_pnl = result['avg_pnl'] or 0.0

            win_rate = (winning_trades / total_trades) if total_trades > 0 else 0.0

            # Resolution-specific stats
            cursor.execute(f'''
                SELECT
                    COUNT(*) as resolution_total,
                    SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as resolution_wins,
                    SUM(CASE WHEN {pnl_col} < 0 THEN 1 ELSE 0 END) as resolution_losses
                FROM trades
                WHERE exit_reason = 'market_resolved' AND exit_time IS NOT NULL
            ''')
            res_result = cursor.fetchone()

            resolution_wins = res_result['resolution_wins'] or 0
            resolution_losses = res_result['resolution_losses'] or 0
            resolution_total = res_result['resolution_total'] or 0
            resolution_wr = (resolution_wins / resolution_total) if resolution_total > 0 else 0.0

            return {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'total_profit_loss': total_pnl,
                'win_rate': win_rate,
                'avg_profit_loss': avg_pnl,
                'resolution_wins': resolution_wins,
                'resolution_losses': resolution_losses,
                'resolution_wr': resolution_wr,
            }
        finally:
            conn.close()
