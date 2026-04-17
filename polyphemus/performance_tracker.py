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
        is_dry_run: bool = False,
        # Phase 2 v4 observability pass-throughs (see PerformanceDB.record_entry).
        fill_model: Optional[str] = None,
        fill_model_reason: Optional[str] = None,
        signal_id: Optional[int] = None,
        fill_latency_ms: Optional[int] = None,
        book_spread_at_entry: Optional[float] = None,
        book_depth_bid: Optional[float] = None,
        book_depth_ask: Optional[float] = None,
        entry_mode: Optional[str] = None,
        signal_source: Optional[str] = None,
    ) -> None:
        """Record a trade entry asynchronously.

        Phase 2 kwargs (fill_model, book state, signal_id, etc.) flow through
        unchanged to :meth:`PerformanceDB.record_entry`; see that method for
        semantics. When omitted they remain None and the DB layer will not
        touch the corresponding v4 columns, so older callers keep working.
        """
        if entry_time is None:
            entry_time = asyncio.get_event_loop().time()

        # Derive signal_source from metadata if the caller didn't pass it,
        # so v4 attribution queries work without updating every record_entry
        # site in lock-step.
        if signal_source is None and isinstance(metadata, dict):
            signal_source = metadata.get("source")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.db.record_entry(
                trade_id=trade_id,
                token_id=token_id,
                slug=slug,
                entry_time=entry_time,
                entry_price=entry_price,
                entry_size=entry_size,
                entry_tx_hash=entry_tx_hash,
                outcome=outcome,
                market_title=market_title,
                filter_score=filter_score,
                metadata=metadata,
                strategy="signal_bot",
                fg_at_entry=fg_at_entry,
                is_dry_run=is_dry_run,
                fill_model=fill_model,
                fill_model_reason=fill_model_reason,
                signal_id=signal_id,
                fill_latency_ms=fill_latency_ms,
                book_spread_at_entry=book_spread_at_entry,
                book_depth_bid=book_depth_bid,
                book_depth_ask=book_depth_ask,
                entry_mode=entry_mode,
                signal_source=signal_source,
            ),
        )

        model_str = f' model={fill_model}' if fill_model else ''
        self.logger.info(
            f'Entry recorded: {trade_id} | {outcome} @ ${entry_price:.4f} x {entry_size} shares{model_str}'
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
