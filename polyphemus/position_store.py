"""PositionStore — Single source of truth for all position state.

No asyncio.Lock needed: Polyphemus is a single-threaded asyncio bot.
All coroutine context switches happen at await points, and PositionStore
methods are synchronous (no awaits), so they execute atomically.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from .config import setup_logger
from .types import Position


class PositionStore:
    """Thread-safe (single-threaded context) position tracker."""

    def __init__(self) -> None:
        """Initialize empty position store."""
        self._positions: Dict[str, Position] = {}
        self._logger = setup_logger('polyphemus.store')

    def add(self, pos: Position) -> None:
        """Store position by token_id."""
        self._positions[pos.token_id] = pos

    def update(self, token_id: str, **kw) -> None:
        """Update position fields using setattr (only if hasattr)."""
        if token_id not in self._positions:
            return
        pos = self._positions[token_id]
        for key, value in kw.items():
            if hasattr(pos, key):
                setattr(pos, key, value)

    def remove(self, token_id: str) -> Optional[Position]:
        """Pop and return position, or None if not found."""
        return self._positions.pop(token_id, None)

    def get(self, token_id: str) -> Optional[Position]:
        """Get position by token_id, or None."""
        return self._positions.get(token_id)

    def get_open(self) -> List[Position]:
        """Return all positions where exit_time is None."""
        return [pos for pos in self._positions.values() if pos.exit_time is None]

    def get_by_slug(self, slug: str) -> Optional[Position]:
        """Find first open position matching slug, or None."""
        for pos in self._positions.values():
            if pos.slug == slug and pos.exit_time is None:
                return pos
        return None

    def count_open(self) -> int:
        """Count positions where exit_time is None."""
        return sum(1 for pos in self._positions.values() if pos.exit_time is None)

    def get_all(self) -> List[Position]:
        """Return all positions (open and closed)."""
        return list(self._positions.values())

    def reconcile_with_db(self, db_path: str) -> Dict[str, Any]:
        """Reconcile memory state with database.

        Returns dict of discrepancies where memory and DB disagree about open/closed status.
        """
        # Snapshot memory state FIRST
        memory_state = {
            token_id: pos.exit_time is None
            for token_id, pos in self._positions.items()
        }

        db_state = {}
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT token_id, exit_time IS NULL as is_open FROM trades"
            )
            for token_id, is_open in cursor.fetchall():
                db_state[token_id] = bool(is_open)
        finally:
            if conn:
                conn.close()

        # Find discrepancies
        discrepancies = {}
        all_tokens = set(memory_state.keys()) | set(db_state.keys())

        for token_id in all_tokens:
            mem_open = memory_state.get(token_id)
            db_open = db_state.get(token_id)

            if mem_open != db_open:
                discrepancies[token_id] = {
                    "memory_open": mem_open,
                    "db_open": db_open,
                }

        return discrepancies

    def load_from_db(self, db_path: str) -> int:
        """Load all open positions from database.

        Args:
            db_path: Path to SQLite database.

        Returns count of positions loaded.
        """
        conn = None
        count = 0
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT token_id, slug, entry_price, entry_size, entry_time, outcome, metadata
                FROM trades
                WHERE exit_time IS NULL
                """
            )
            for row in cursor.fetchall():
                token_id, slug, entry_price, entry_size, entry_time, outcome, metadata_json = row

                # Convert entry_time from Unix timestamp to aware datetime
                if entry_time is not None:
                    entry_dt = datetime.fromtimestamp(
                        entry_time, tz=timezone.utc
                    )
                else:
                    entry_dt = None

                # Compute market_end_time from slug epoch
                # Skip position if slug cannot be parsed
                parts = slug.rsplit('-', 1) if slug else []
                if len(parts) == 2 and parts[1].isdigit():
                    from .types import parse_window_from_slug
                    market_epoch = int(parts[1])
                    market_end_time = datetime.fromtimestamp(market_epoch + parse_window_from_slug(slug), tz=timezone.utc)
                else:
                    # Cannot parse epoch from slug - skip this position
                    self._logger.error(f"Cannot parse epoch from slug '{slug}', skipping position {token_id}")
                    continue

                # Create Position object
                pos_metadata = {}
                if metadata_json:
                    try:
                        pos_metadata = json.loads(metadata_json)
                    except (json.JSONDecodeError, TypeError):
                        pass
                pos = Position(
                    token_id=token_id,
                    slug=slug,
                    entry_price=entry_price,
                    entry_size=entry_size,
                    entry_time=entry_dt,
                    entry_tx_hash='',
                    market_end_time=market_end_time,
                    metadata=pos_metadata,
                )

                self.add(pos)
                count += 1

        finally:
            if conn:
                conn.close()

        return count
