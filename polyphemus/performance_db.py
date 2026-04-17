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
                ('binance_price_at_fill', 'REAL'),
                ('binance_price_30s', 'REAL'),
                ('adverse_fill', 'INTEGER'),
                ('adverse_fill_bps', 'REAL'),
                ('check_window_secs', 'INTEGER'),
            ]

            cursor.execute('PRAGMA table_info(trades)')
            pre_migration_cols = {row[1] for row in cursor.fetchall()}

            for col_name, col_def in columns_to_add:
                try:
                    cursor.execute(f'ALTER TABLE trades ADD COLUMN {col_name} {col_def}')
                    self.logger.info(f'Migration: added column {col_name}')
                except sqlite3.OperationalError:
                    # Column already exists, skip
                    pass

            # is_dry_run: added 2026-04-16 for dry/live segregation. Separate from columns_to_add
            # because it requires explicit backfill of existing rows (historical data is dry-run).
            # NOT NULL DEFAULT 0 matches accumulator_metrics.cycles and rejects NULL writes
            # (NULL rows would match neither is_dry_run=0 nor =1, silently dropping from get_stats —
            # same bug class as Apr 10).
            if 'is_dry_run' not in pre_migration_cols:
                try:
                    cursor.execute(
                        'ALTER TABLE trades ADD COLUMN is_dry_run INTEGER NOT NULL DEFAULT 0'
                    )
                    cursor.execute('SELECT COUNT(*) FROM trades')
                    pre_existing = cursor.fetchone()[0]
                    if pre_existing > 0:
                        cursor.execute('UPDATE trades SET is_dry_run = 1')
                        self.logger.warning(
                            f'Migration: added is_dry_run column and backfilled {pre_existing} trades to 1. '
                            'Historical trades assumed dry-run; update manually if any live trades pre-migration.'
                        )
                    else:
                        self.logger.info('Migration: added is_dry_run column (no pre-existing rows)')
                except sqlite3.OperationalError as e:
                    self.logger.error(f'is_dry_run migration failed: {e}')

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
        strategy: str = "signal_bot",
        fg_at_entry: float = None,
        is_dry_run: bool = False,
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
            fg_at_entry: Fear & Greed index value at time of entry (0-100).
            is_dry_run: True when caller is running in DRY_RUN mode — required
                for segregating dry vs live aggregates. Default False (fail-closed
                towards live; caller must opt-in to dry-run flagging).
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            metadata_json = json.dumps(metadata) if metadata else None
            cursor.execute('''
                INSERT INTO trades (
                    trade_id, token_id, slug, entry_time, entry_price, entry_size,
                    entry_tx_hash, outcome, market_title, is_resolved, is_redeemed,
                    side, entry_amount, strategy, filter_score, metadata, fg_at_entry,
                    is_dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_id, token_id, slug, entry_time, entry_price, entry_size,
                entry_tx_hash, outcome, market_title, 0, 0,
                'BUY', entry_price * entry_size, strategy, filter_score, metadata_json,
                fg_at_entry, 1 if is_dry_run else 0,
            ))
            conn.commit()
            fg_str = f' fg={fg_at_entry}' if fg_at_entry is not None else ''
            tag = ' [DRY]' if is_dry_run else ''
            self.logger.info(f'Recorded entry{tag}: {trade_id} @ {entry_price:.4f} x {entry_size} score={filter_score}{fg_str}')
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

    def force_close_trade(
        self,
        slug: str,
        exit_reason: str,
        exit_price: float = 0.0,
        book_snapshot: Optional[dict] = None,
        worthless_threshold: float = 0.05,
    ) -> bool:
        """
        Force-close a trade by slug, bypassing PerformanceTracker lookup.

        Used for ghost positions where record_exit fails because the trade
        was from a prior bot instance and isn't tracked by PerformanceTracker.

        Args:
            slug: Market slug to close (e.g., 'btc-updown-5m-1770944400')
            exit_reason: Reason for closing (e.g., 'market_resolved', 'ghost_cleanup')
            exit_price: Exit price (default 0.0 for expired markets)
            book_snapshot: Optional CLOB book dict ({"bids": [{price, size}, ...]}).
                When exit_price <= 0 and a book is provided, confirm the position
                is truly worthless (bid_value < worthless_threshold) before writing
                a total-loss P&L. If the bid side could have absorbed the position,
                estimate exit via top bid and tag exit_reason with '_unconfirmed'.
                Omit to preserve legacy total-loss behavior.
            worthless_threshold: Dollar cutoff below which a position is dust
                (default $0.05 per Phase 1.5 plan).

        Returns:
            True if a trade was updated, False if no matching open trade found
        """
        effective_exit_price = exit_price
        effective_reason = exit_reason
        if exit_price <= 0 and book_snapshot is not None:
            from .force_close_confirmation import is_position_worthless, top_bid_price
            worthless, bid_value = is_position_worthless(book_snapshot, worthless_threshold)
            if not worthless:
                estimated = top_bid_price(book_snapshot)
                if estimated > 0:
                    effective_exit_price = estimated
                    effective_reason = f"{exit_reason}_unconfirmed"
                    self.logger.warning(
                        f"Force-close of {slug} not confirmed dust "
                        f"(bid value ${bid_value:.2f} >= ${worthless_threshold:.2f}); "
                        f"estimating exit at top bid ${estimated:.4f} and tagging '_unconfirmed'"
                    )
        conn = self._get_conn()
        try:
            import time as _time
            now = _time.time()
            cursor = conn.cursor()
            # Compute real P&L from entry data when exit_price is meaningful.
            # When exit_price=0 (market resolved, outcome unknown), assume LOSS:
            # pnl = -(entry_price * entry_size). Redeemer updates to win if redeemed.
            cursor.execute(
                """UPDATE trades SET
                    exit_time = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    exit_tx_hash = 'force_closed',
                    pnl = CASE
                        WHEN ? > 0 THEN (? - entry_price) * entry_size
                        ELSE -(entry_price * entry_size)
                    END,
                    pnl_pct = CASE
                        WHEN ? > 0 AND entry_price > 0 THEN (? - entry_price) / entry_price
                        WHEN entry_price > 0 THEN -1.0
                        ELSE 0.0
                    END,
                    hold_seconds = CAST(? - entry_time AS INTEGER)
                WHERE slug = ? AND exit_time IS NULL""",
                (now, effective_exit_price, effective_reason,
                 effective_exit_price, effective_exit_price,
                 effective_exit_price, effective_exit_price,
                 now, slug)
            )
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self.logger.info(
                    f'Force-closed trade: {slug} | reason={effective_reason} '
                    f'exit_price={effective_exit_price:.4f}'
                )
            else:
                self.logger.debug(f'No open trade found for slug: {slug}')
            return updated
        finally:
            conn.close()

    def force_close_by_token_id(self, token_id: str, exit_reason: str,
                               exit_price: float = 0.0) -> bool:
        """Force-close a trade by token_id (used by orphan sweep)."""
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
                    pnl = CASE
                        WHEN ? > 0 THEN (? - entry_price) * entry_size
                        ELSE -(entry_price * entry_size)
                    END,
                    pnl_pct = CASE
                        WHEN ? > 0 AND entry_price > 0 THEN (? - entry_price) / entry_price
                        WHEN entry_price > 0 THEN -1.0
                        ELSE 0.0
                    END,
                    hold_seconds = CAST(? - entry_time AS INTEGER)
                WHERE token_id = ? AND exit_time IS NULL""",
                (now, exit_price, exit_reason,
                 exit_price, exit_price,
                 exit_price, exit_price,
                 now, token_id)
            )
            updated = cursor.rowcount > 0
            conn.commit()
            if updated:
                self.logger.info(
                    f'Force-closed trade by token_id: {token_id[:16]}... | reason={exit_reason}'
                )
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
                f"WHERE exit_time >= ? AND exit_time < ? "
                f"AND NOT (exit_tx_hash = 'force_closed' AND exit_price <= 0)",
                (start_epoch, end_epoch)
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def get_stats(self, dry_run_only: Optional[bool] = None) -> Dict:
        """
        Compute comprehensive trade statistics.

        Args:
            dry_run_only: If True, include only dry-run trades (is_dry_run=1).
                If False, include only live trades (is_dry_run=0).
                If None (default), include all trades regardless of flag.

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
        if dry_run_only is True:
            dry_filter = ' AND is_dry_run = 1'
        elif dry_run_only is False:
            dry_filter = ' AND is_dry_run = 0'
        else:
            dry_filter = ''
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
                WHERE exit_time IS NOT NULL{dry_filter}
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
                WHERE exit_reason = 'market_resolved' AND exit_time IS NOT NULL{dry_filter}
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

    def get_wr_for_bucket(self, asset: str, bucket: float) -> tuple:
        """Return (wr, n) for market_resolved trades in a price bucket for an asset.

        Args:
            asset: Asset name (e.g. 'BTC', 'ETH'). Matched via slug prefix.
            bucket: Price bucket (0.1 wide, e.g. 0.4, 0.5, 0.6).

        Returns:
            (win_rate, n) where n is the number of resolved trades in the bucket.
            Returns (0.0, 0) if no trades found or on error.
        """
        slug_prefix = f"{asset.lower()}-%"
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS wr
                FROM trades
                WHERE slug LIKE ?
                  AND exit_reason = 'market_resolved'
                  AND ROUND(entry_price * 10.0) / 10.0 = ?
                  AND pnl IS NOT NULL
                """,
                (slug_prefix, round(bucket, 1)),
            )
            row = cur.fetchone()
            n = row["n"] or 0
            wr = row["wr"] or 0.0
            return float(wr), int(n)
        except Exception:
            return 0.0, 0
        finally:
            conn.close()

    def get_direction_wr(self, last_n: int = 50) -> dict:
        """Return rolling WR by direction from last N resolved trades.

        Returns:
            {"up": (wr, n), "down": (wr, n)} where wr is float 0-1, n is count.
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """
                SELECT json_extract(metadata, '$.direction') AS dir,
                       COUNT(*) AS n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS wr
                FROM (
                    SELECT metadata, pnl FROM trades
                    WHERE exit_time IS NOT NULL
                      AND pnl IS NOT NULL
                      AND metadata IS NOT NULL
                    ORDER BY exit_time DESC
                    LIMIT ?
                )
                WHERE dir IS NOT NULL
                GROUP BY dir
                """,
                (last_n,),
            )
            result = {"up": (0.0, 0), "down": (0.0, 0)}
            for row in cur.fetchall():
                d = (row["dir"] or "").lower()
                if d in result:
                    result[d] = (float(row["wr"] or 0), int(row["n"] or 0))
            return result
        except Exception:
            return {"up": (0.0, 0), "down": (0.0, 0)}
        finally:
            conn.close()

    def update_adverse_selection(
        self,
        trade_id: str,
        binance_at_fill: float,
        binance_at_check: Optional[float],
        direction: str,
        check_window_secs: int,
    ) -> None:
        """Record Binance adverse selection data after fill.

        Args:
            trade_id: Trade to update.
            binance_at_fill: Binance spot price at fill time.
            binance_at_check: Binance spot price at check time. None on timeout.
            direction: 'up' or 'down' (signal direction).
            check_window_secs: Actual measurement window used (capped at epoch boundary).
        """
        if binance_at_fill <= 0:
            return

        adverse_fill_bps: Optional[float] = None
        adverse: Optional[int] = None
        if binance_at_check is not None and binance_at_check > 0:
            move = (binance_at_check - binance_at_fill) / binance_at_fill
            adverse_fill_bps = round(move * 10000, 4)  # signed bps delta
            adverse = int(
                (direction.lower() == "up" and move < 0) or
                (direction.lower() == "down" and move > 0)
            )

        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE trades SET binance_price_at_fill=?, binance_price_30s=?, "
                "adverse_fill=?, adverse_fill_bps=?, check_window_secs=? "
                "WHERE trade_id=?",
                (binance_at_fill, binance_at_check, adverse, adverse_fill_bps,
                 check_window_secs, trade_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_source_stats(self, source: str) -> dict:
        """Return stats for completed trades matching a source in metadata JSON.

        Args:
            source: Source name to match in metadata (e.g. 'oracle_flip').

        Returns:
            Dict with keys: total, wins, wr (%), pnl.
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), "
                "COALESCE(SUM(pnl), 0) "
                "FROM trades WHERE metadata LIKE ? AND exit_time IS NOT NULL",
                (f'%"source": "{source}"%',)
            )
            row = cur.fetchone()
            total = row[0] or 0
            wins = row[1] or 0
            pnl = row[2] or 0.0
            wr = (wins / total * 100) if total > 0 else 0.0
            return {"total": total, "wins": wins, "wr": wr, "pnl": pnl}
        except Exception:
            return {"total": 0, "wins": 0, "wr": 0.0, "pnl": 0.0}
        finally:
            conn.close()
