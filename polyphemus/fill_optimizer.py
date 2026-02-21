"""FillOptimizer — Thompson Sampling for maker order offset selection.

Uses a multi-armed bandit (Beta-Bernoulli model) to dynamically choose
the optimal maker offset for post-only orders on 15-min markets.

Arms represent different offset amounts below the midpoint:
- 0.005 (aggressive: closer to mid, higher fill rate, worse price)
- 0.01  (default: current setting)
- 0.015 (moderate: balanced)
- 0.02  (conservative: better price, lower fill rate)

The optimizer learns which offset maximizes expected profit (not just
fill rate) by tracking both fill success and trade outcome.

State persists to SQLite so learning carries across restarts.
"""

import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import setup_logger


@dataclass
class ArmState:
    """State of a single bandit arm."""
    offset: float
    alpha: float  # successes + 1 (Beta prior)
    beta: float   # failures + 1 (Beta prior)
    total_pulls: int
    total_fills: int
    total_profit: float

    @property
    def fill_rate(self) -> float:
        if self.total_pulls == 0:
            return 0.0
        return self.total_fills / self.total_pulls

    @property
    def avg_profit(self) -> float:
        if self.total_fills == 0:
            return 0.0
        return self.total_profit / self.total_fills

    def sample(self) -> float:
        """Draw from Beta posterior."""
        return random.betavariate(self.alpha, self.beta)


class FillOptimizer:
    """Thompson Sampling optimizer for maker order offset."""

    DEFAULT_OFFSETS = [0.001, 0.003, 0.005, 0.008, 0.01, 0.015, 0.02]

    def __init__(
        self,
        offsets: Optional[List[float]] = None,
        db_path: str = "data/fill_optimizer.db",
    ):
        self._logger = setup_logger("polyphemus.fill_optimizer")
        self._offsets = offsets or self.DEFAULT_OFFSETS
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._arms: Dict[float, ArmState] = {}
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._load_state()

        self._logger.info(
            f"FillOptimizer initialized: {len(self._arms)} arms, "
            f"offsets={self._offsets}"
        )

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS arms (
                offset REAL PRIMARY KEY,
                alpha REAL NOT NULL DEFAULT 1.0,
                beta REAL NOT NULL DEFAULT 1.0,
                total_pulls INTEGER NOT NULL DEFAULT 0,
                total_fills INTEGER NOT NULL DEFAULT 0,
                total_profit REAL NOT NULL DEFAULT 0.0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pull_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch REAL NOT NULL,
                offset REAL NOT NULL,
                slug TEXT,
                asset TEXT,
                midpoint REAL,
                filled INTEGER,
                fill_time_secs REAL,
                profit REAL
            )
        """)
        self._conn.commit()

    def _load_state(self):
        """Load arm states from DB, creating any missing arms."""
        cursor = self._conn.execute("SELECT * FROM arms")
        rows = {row[0]: row for row in cursor.fetchall()}

        for offset in self._offsets:
            if offset in rows:
                row = rows[offset]
                self._arms[offset] = ArmState(
                    offset=row[0],
                    alpha=row[1],
                    beta=row[2],
                    total_pulls=row[3],
                    total_fills=row[4],
                    total_profit=row[5],
                )
            else:
                self._arms[offset] = ArmState(
                    offset=offset,
                    alpha=1.0,  # Uniform prior
                    beta=1.0,
                    total_pulls=0,
                    total_fills=0,
                    total_profit=0.0,
                )
                self._conn.execute(
                    "INSERT INTO arms (offset) VALUES (?)", (offset,)
                )
                self._conn.commit()

    def _save_arm(self, arm: ArmState):
        """Persist arm state to DB."""
        self._conn.execute(
            """UPDATE arms SET alpha=?, beta=?, total_pulls=?,
               total_fills=?, total_profit=? WHERE offset=?""",
            (arm.alpha, arm.beta, arm.total_pulls,
             arm.total_fills, arm.total_profit, arm.offset),
        )
        self._conn.commit()

    def select_offset(self) -> float:
        """Select the best offset using Thompson Sampling.

        Draws from each arm's Beta posterior and returns the offset
        with the highest sample. This naturally balances exploration
        (uncertain arms get wide samples) and exploitation (good arms
        get high samples).

        Returns:
            Optimal offset for the next maker order.
        """
        best_offset = self._offsets[4]  # default 0.01
        best_sample = -1.0

        samples = {}
        for offset, arm in self._arms.items():
            sample = arm.sample()
            samples[offset] = sample
            if sample > best_sample:
                best_sample = sample
                best_offset = offset

        self._logger.debug(
            f"Thompson samples: {', '.join(f'{o}={s:.3f}' for o, s in samples.items())} "
            f"→ selected {best_offset}"
        )
        return best_offset

    def record_outcome(
        self,
        offset: float,
        filled: bool,
        slug: str = "",
        asset: str = "",
        midpoint: float = 0.0,
        fill_time_secs: float = 0.0,
        profit: float = 0.0,
    ):
        """Record the outcome of a maker order.

        Args:
            offset: The offset used for this order.
            filled: Whether the order filled.
            slug: Market slug for logging.
            asset: Asset name.
            midpoint: Midpoint at order time.
            fill_time_secs: Time to fill (0 if not filled).
            profit: Realized profit (updated later via update_profit).
        """
        arm = self._arms.get(offset)
        if not arm:
            self._logger.warning(f"Unknown offset {offset}, ignoring")
            return

        arm.total_pulls += 1

        if filled:
            arm.alpha += 1.0  # success
            arm.total_fills += 1
        else:
            arm.beta += 1.0  # failure

        self._save_arm(arm)

        # Log to history
        self._conn.execute(
            """INSERT INTO pull_history
               (epoch, offset, slug, asset, midpoint, filled, fill_time_secs, profit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), offset, slug, asset, midpoint,
             1 if filled else 0, fill_time_secs, profit),
        )
        self._conn.commit()

        self._logger.info(
            f"Fill outcome: offset={offset}, filled={filled}, "
            f"slug={slug}, arm_fill_rate={arm.fill_rate:.1%} "
            f"({arm.total_fills}/{arm.total_pulls})"
        )

    def update_profit(self, slug: str, profit: float):
        """Update the profit for a completed trade (called after exit).

        This allows the optimizer to learn which offsets lead to
        profitable trades, not just fills.
        """
        # Update the most recent pull for this slug
        self._conn.execute(
            """UPDATE pull_history SET profit = ?
               WHERE slug = ? AND id = (
                   SELECT MAX(id) FROM pull_history WHERE slug = ?
               )""",
            (profit, slug, slug),
        )

        # Also update the arm's total profit
        cursor = self._conn.execute(
            """SELECT offset FROM pull_history
               WHERE slug = ? ORDER BY id DESC LIMIT 1""",
            (slug,),
        )
        row = cursor.fetchone()
        if row:
            offset = row[0]
            arm = self._arms.get(offset)
            if arm:
                arm.total_profit += profit
                self._save_arm(arm)

        self._conn.commit()

    def get_stats(self) -> Dict[str, dict]:
        """Get optimizer stats for dashboard."""
        result = {}
        for offset, arm in sorted(self._arms.items()):
            result[f"${offset:.3f}"] = {
                "pulls": arm.total_pulls,
                "fills": arm.total_fills,
                "fill_rate": f"{arm.fill_rate:.1%}",
                "avg_profit": f"${arm.avg_profit:.2f}",
                "alpha": round(arm.alpha, 1),
                "beta": round(arm.beta, 1),
            }
        return result

    def close(self):
        """Close DB connection."""
        self._conn.close()
