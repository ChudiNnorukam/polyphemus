"""
Polyphemus Polymarket Trading Bot - Performance Tracking Layer

This module provides high-level performance tracking with survivorship bias detection.
It wraps the PerformanceDB layer and adds business logic for entry/exit recording and analytics.
"""

import asyncio
from typing import Dict, Optional
from .performance_db import PerformanceDB
from .config import setup_logger, assert_metric_matches_db


logger = setup_logger('polyphemus.performance_tracker')


class PerformanceTracker:
    """High-level performance tracking with survivorship bias detection and metrics validation."""

    def __init__(self, db_path: str):
        """
        Initialize the performance tracker.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db = PerformanceDB(db_path)
        self.logger = setup_logger('polyphemus.performance_tracker')

    async def record_entry(
        self,
        trade_id: str,
        token_id: str,
        slug: str,
        entry_price: float,
        entry_size: float,
        entry_tx_hash: str,
        outcome: str,
        market_title: str,
        entry_time: Optional[float] = None,
        filter_score: Optional[float] = None,
        metadata: Optional[dict] = None,
        fg_at_entry: Optional[float] = None,
    ) -> None:
        """
        Record a trade entry asynchronously.

        Args:
            trade_id: Unique identifier for the trade.
            token_id: Token ID from Polymarket.
            slug: Market slug.
            entry_price: Entry price (0.0 - 1.0).
            entry_size: Size of position in shares.
            entry_tx_hash: Transaction hash of the entry order.
            outcome: Outcome name (e.g., 'YES', 'NO').
            market_title: Human-readable market title.
            entry_time: Entry timestamp (Unix epoch seconds). If None, uses current time.
            filter_score: Signal quality score from XGBoost model (0-100).
            fg_at_entry: Fear & Greed index value at time of entry (0-100).
        """
        if entry_time is None:
            entry_time = asyncio.get_event_loop().time()

        # Delegate to DB layer (run in executor to avoid blocking)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self.db.record_entry,
            trade_id, token_id, slug, entry_time, entry_price, entry_size,
            entry_tx_hash, outcome, market_title, filter_score, metadata,
            "signal_bot", fg_at_entry
        )

        self.logger.info(
            f'Entry recorded: {trade_id} | {outcome} @ ${entry_price:.4f} x {entry_size} shares'
        )

    async def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_size: float,
        exit_reason: str,
        exit_tx_hash: str,
        exit_time: Optional[float] = None,
    ) -> None:
        """
        Record a trade exit with automatic P&L calculation.

        Args:
            trade_id: Trade ID to update.
            exit_price: Exit price (0.0 - 1.0).
            exit_size: Size exited in shares.
            exit_reason: Reason for exit (e.g., 'profit_target', 'market_resolved').
            exit_tx_hash: Transaction hash of the exit order.
            exit_time: Exit timestamp (Unix epoch seconds). If None, uses current time.

        Raises:
            ValueError: If trade_id not found or entry_price is invalid.
        """
        if exit_time is None:
            exit_time = asyncio.get_event_loop().time()

        # Fetch entry data from DB to calculate P&L
        loop = asyncio.get_event_loop()
        open_trades = await loop.run_in_executor(None, self.db.get_open_trades)

        trade = None
        for t in open_trades:
            if t['trade_id'] == trade_id:
                trade = t
                break

        if not trade:
            self.logger.error(f'Trade {trade_id} not found in open trades')
            raise ValueError(f'Trade {trade_id} not found')

        entry_price = trade['entry_price']
        entry_size = trade['entry_size']

        if entry_price <= 0:
            self.logger.error(f'Invalid entry_price for {trade_id}: {entry_price}')
            raise ValueError(f'Invalid entry_price: {entry_price}')

        # Calculate P&L
        pnl = (exit_price - entry_price) * entry_size
        pnl_pct = pnl / (entry_price * entry_size) if (entry_price * entry_size) > 0 else 0.0

        # Record exit in DB
        await loop.run_in_executor(
            None,
            self.db.record_exit,
            trade_id, exit_time, exit_price, exit_size, exit_reason,
            exit_tx_hash, pnl, pnl_pct
        )

        self.logger.info(
            f'Exit recorded: {trade_id} | {exit_reason} @ ${exit_price:.4f} | P&L: ${pnl:.2f} ({pnl_pct*100:.1f}%)'
        )

    async def get_stats(self) -> Dict:
        """
        Retrieve comprehensive statistics with survivorship bias detection.

        Returns:
            Dictionary with keys:
            - total_trades: Count of all trades
            - winning_trades: Count of winning trades
            - losing_trades: Count of losing trades
            - total_profit_loss: Sum of all P&L
            - win_rate: Win rate (0.0 - 1.0)
            - avg_profit_loss: Average P&L per trade
            - resolution_wins: Wins from market_resolved exits
            - resolution_losses: Losses from market_resolved exits
            - resolution_wr: Win rate for market_resolved exits
        """
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(None, self.db.get_stats)

        # Check for survivorship bias
        self.check_survivorship_bias(stats)

        return stats

    def get_wr_by_bucket(self, asset: str, bucket: float) -> tuple:
        """Return (wr, n) for resolved trades in a price bucket. Sync, no await needed."""
        return self.db.get_wr_for_bucket(asset, bucket)

    def get_source_stats(self, source: str) -> dict:
        """Return (total, wins, wr, pnl) for trades with a given source in metadata."""
        return self.db.get_source_stats(source)

    def check_survivorship_bias(self, stats: Dict) -> None:
        """
        Detect and warn about survivorship bias in statistics.

        If the win rate for market_resolved exits is significantly higher than
        the overall win rate, this suggests survivorship bias (i.e., positions
        that were exited early due to losses are not counted in resolution stats).

        Args:
            stats: Statistics dictionary from get_stats().
        """
        resolution_wr = stats.get('resolution_wr', 0.0)
        overall_wr = stats.get('win_rate', 0.0)

        if resolution_wr > 0 and overall_wr > 0:
            diff_pp = (resolution_wr - overall_wr) * 100

            # Alert if difference is >10 percentage points
            if diff_pp > 10:
                self.logger.warning(
                    f'SURVIVORSHIP BIAS ALERT: resolution WR {resolution_wr*100:.1f}% > '
                    f'overall WR {overall_wr*100:.1f}% by {diff_pp:.1f}pp '
                    f'(suggests early exits due to losses)'
                )
