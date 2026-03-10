#!/usr/bin/env python3
"""
Self-tuning position sizing with anti-death-spiral protections.

Adjusts sizing multipliers per entry-price bucket based on rolling
performance. All adjustments bounded by hard floor/ceiling.
Safety-first: defaults to 1.0x (current fixed sizing) on any error.
"""

import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger("self_tuner")

STATE_VERSION = 1

# Bucket boundaries: [low, high) — price >= low AND price < high
BUCKETS = {
    "0.65-0.70": (0.65, 0.70),
    "0.70-0.80": (0.70, 0.80),
}


class SelfTuner:
    # --- Hard Safety Bounds ---
    HARD_FLOOR = 0.5       # Multiplier can NEVER go below 0.5x
    HARD_CEILING = 1.5     # Multiplier can NEVER go above 1.5x

    # --- Adjustment Thresholds ---
    TIGHTEN_DELTA = -0.15  # WR must drop 15% below baseline to tighten
    LOOSEN_DELTA = +0.10   # WR must rise 10% above baseline to loosen
    MAX_STEP = 0.10        # Max ±10% change per cycle
    DECAY_RATE = 0.15      # 15% exponential decay toward 1.0 per cycle

    # --- Sample Requirements ---
    MIN_SAMPLE = 20        # Trades per bucket before adjusting
    ROLLING_WINDOW = 30    # Last N trades per bucket for current WR
    BASELINE_WINDOW = 100  # Last N trades per bucket for baseline WR

    # --- Timing ---
    COOLDOWN_SEC = 900     # 15 min between cycles
    CB_LIMIT = 3           # Consecutive same-dir adjustments before freeze
    CB_PAUSE_SEC = 3600    # 1 hour freeze on circuit breaker

    # --- Kill Switch ---
    DRAWDOWN_KILL_PCT = 0.20  # 20% drawdown from peak freezes all

    def __init__(self, db_path: str, state_path: str):
        self.db_path = db_path
        self.state_path = state_path
        self.state = self._load_state()
        self._ensure_db_schema()
        # Cache multipliers in memory for hot-path access
        self._cached_multipliers = dict(self.state.get("multipliers", {}))

    # --- State Persistence (Atomic) ---

    def _defaults(self) -> dict:
        return {
            "version": STATE_VERSION,
            "multipliers": {b: 1.0 for b in BUCKETS},
            "last_cycle_time": 0,
            "session_peak_pnl": None,  # None = not yet initialized
            "consecutive_adj": {b: {"direction": "neutral", "count": 0} for b in BUCKETS},
            "cb_until": {b: 0 for b in BUCKETS},
            "kill_switch_active": False,
        }

    def _load_state(self) -> dict:
        """Load state with version check and backup fallback."""
        for path in [self.state_path, self.state_path + ".backup"]:
            try:
                with open(path, "r") as f:
                    state = json.load(f)
                if state.get("version") != STATE_VERSION:
                    logger.warning(f"State version mismatch in {path}, resetting")
                    continue
                return state
            except (FileNotFoundError, json.JSONDecodeError, KeyError):
                continue
        logger.info("No valid state file, starting with defaults")
        return self._defaults()

    def _save_state(self):
        """Atomic write: .tmp → rename. Keep one backup."""
        try:
            # Backup current state
            if os.path.exists(self.state_path):
                backup = self.state_path + ".backup"
                if os.path.exists(backup):
                    os.remove(backup)
                os.rename(self.state_path, backup)
            # Atomic write
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=2)
            os.rename(tmp, self.state_path)
        except Exception as e:
            logger.error(f"Failed to save tuning state: {e}")

    # --- DB Schema ---

    def _ensure_db_schema(self):
        try:
            db = sqlite3.connect(self.db_path, timeout=5)
            db.execute("""
                CREATE TABLE IF NOT EXISTS tuning_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    parameter TEXT NOT NULL,
                    old_value REAL NOT NULL,
                    new_value REAL NOT NULL,
                    reason TEXT NOT NULL,
                    window_size INTEGER,
                    window_wr REAL,
                    baseline_wr REAL,
                    circuit_breaker INTEGER DEFAULT 0
                )
            """)
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"Failed to create tuning_log table: {e}")

    # --- Hot Path: Cache-Only Multiplier Lookup ---

    def get_multiplier(self, entry_price: float) -> float:
        """
        Returns cached multiplier for the given entry price.
        ZERO DB access — safe to call in trade execution hot path.
        Returns 1.0 on any error (safe default).
        """
        try:
            bucket = self._price_to_bucket(entry_price)
            if bucket is None:
                return 1.0
            return self._cached_multipliers.get(bucket, 1.0)
        except Exception:
            return 1.0

    @staticmethod
    def _price_to_bucket(price: float) -> str:
        """Map entry price to bucket. Returns None if no bucket matches."""
        for name, (low, high) in BUCKETS.items():
            if low <= price < high:
                return name
        return None

    # --- Main Tuning Cycle ---

    def run_cycle(self):
        """
        Main tuning cycle. Called every ~15 min from health check loop.
        All adjustments bounded, logged, and persisted atomically.
        """
        now = int(time.time())

        # Cooldown check
        if now - self.state["last_cycle_time"] < self.COOLDOWN_SEC:
            return

        # Kill switch check
        if self._check_kill_switch():
            logger.info("TUNER: Kill switch active — all multipliers frozen")
            self.state["last_cycle_time"] = now
            self._save_state()
            return

        try:
            db = sqlite3.connect(self.db_path, timeout=5)
            db.row_factory = sqlite3.Row

            for bucket, (low, high) in BUCKETS.items():
                self._tune_bucket(db, bucket, low, high, now)

            db.close()
        except sqlite3.OperationalError as e:
            logger.warning(f"TUNER: DB busy, skipping cycle: {e}")
            return
        except Exception as e:
            logger.error(f"TUNER: Unexpected error in cycle: {e}")
            return

        # Update cache and persist
        self._cached_multipliers = dict(self.state["multipliers"])
        self.state["last_cycle_time"] = now
        self._save_state()

        # Log summary
        mults = self.state["multipliers"]
        logger.info(f"TUNER: Cycle complete. Multipliers: {mults}")

    def _tune_bucket(self, db, bucket: str, low: float, high: float, now: int):
        """Tune a single bucket's multiplier."""
        current = self.state["multipliers"].get(bucket, 1.0)

        # Circuit breaker: FREEZE (no changes at all, not even mean reversion)
        cb_until = self.state["cb_until"].get(bucket, 0)
        if now < cb_until:
            self._log(db, bucket, current, current, "circuit_breaker_frozen", 0, 0, 0, True)
            return

        # Get rolling window WR for this bucket
        rolling = db.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins
            FROM (
                SELECT profit_loss FROM trades
                WHERE entry_price >= ? AND entry_price < ?
                  AND exit_time IS NOT NULL
                ORDER BY exit_time DESC LIMIT ?
            )
        """, (low, high, self.ROLLING_WINDOW)).fetchone()

        window_size = rolling["total"] or 0

        # Not enough data: mean revert only
        if window_size < self.MIN_SAMPLE:
            new = self._mean_revert(current)
            new = self._clamp(new)
            if abs(new - current) > 0.001:
                self._log(db, bucket, current, new, f"mean_revert_insufficient_data_n={window_size}", window_size, 0, 0, False)
                self.state["multipliers"][bucket] = new
            return

        window_wr = (rolling["wins"] or 0) / window_size

        # Get baseline WR for this bucket
        baseline = db.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins
            FROM (
                SELECT profit_loss FROM trades
                WHERE entry_price >= ? AND entry_price < ?
                  AND exit_time IS NOT NULL
                ORDER BY exit_time DESC LIMIT ?
            )
        """, (low, high, self.BASELINE_WINDOW)).fetchone()

        baseline_total = baseline["total"] or 0
        baseline_wr = (baseline["wins"] or 0) / baseline_total if baseline_total > 0 else window_wr

        delta = window_wr - baseline_wr

        # Determine adjustment
        new = current
        reason = "neutral"

        if delta > self.LOOSEN_DELTA:
            # Outperforming: boost (max +10%)
            step = min(current * self.MAX_STEP, self.HARD_CEILING - current)
            new = current + step
            reason = f"outperforming_delta={delta:+.3f}"
            self._track_direction(bucket, "up", now)
        elif delta < self.TIGHTEN_DELTA:
            # Underperforming: reduce (max -10%)
            step = min(current * self.MAX_STEP, current - self.HARD_FLOOR)
            new = current - step
            reason = f"underperforming_delta={delta:+.3f}"
            self._track_direction(bucket, "down", now)
        else:
            # Neutral: mean revert toward 1.0
            new = self._mean_revert(current)
            reason = f"mean_revert_delta={delta:+.3f}"
            # Reset consecutive count on neutral
            self.state["consecutive_adj"][bucket] = {"direction": "neutral", "count": 0}

        new = self._clamp(new)

        if abs(new - current) > 0.001:
            cb_active = now < self.state["cb_until"].get(bucket, 0)
            self._log(db, bucket, current, new, reason, window_size, window_wr, baseline_wr, cb_active)
            self.state["multipliers"][bucket] = new

    def _mean_revert(self, current: float) -> float:
        """Exponential decay toward 1.0 at DECAY_RATE per cycle."""
        return current + (1.0 - current) * self.DECAY_RATE

    def _clamp(self, value: float) -> float:
        """Enforce hard floor and ceiling."""
        return round(max(self.HARD_FLOOR, min(self.HARD_CEILING, value)), 3)

    def _track_direction(self, bucket: str, direction: str, now: int):
        """Track consecutive same-direction adjustments for circuit breaker."""
        adj = self.state["consecutive_adj"].get(bucket, {"direction": "neutral", "count": 0})
        if adj["direction"] == direction:
            adj["count"] += 1
        else:
            adj["direction"] = direction
            adj["count"] = 1

        # Circuit breaker: FREEZE on 3 consecutive same direction
        if adj["count"] >= self.CB_LIMIT:
            self.state["cb_until"][bucket] = now + self.CB_PAUSE_SEC
            # DON'T reset count — it stays at 3 until direction changes
            logger.warning(f"TUNER: Circuit breaker FROZEN for {bucket} (3x {direction})")

        self.state["consecutive_adj"][bucket] = adj

    # --- Kill Switch ---

    def _check_kill_switch(self) -> bool:
        """Freeze all multipliers if drawdown exceeds threshold."""
        try:
            db = sqlite3.connect(self.db_path, timeout=5)
            cumulative = db.execute(
                "SELECT SUM(profit_loss) FROM trades WHERE exit_time IS NOT NULL"
            ).fetchone()[0] or 0
            db.close()
        except Exception:
            return self.state.get("kill_switch_active", False)

        # Initialize peak on first cycle
        if self.state["session_peak_pnl"] is None:
            self.state["session_peak_pnl"] = cumulative
            return False

        # Update peak
        if cumulative > self.state["session_peak_pnl"]:
            self.state["session_peak_pnl"] = cumulative
            self.state["kill_switch_active"] = False  # Clear on new high
            return False

        # Check drawdown
        peak = self.state["session_peak_pnl"]
        if peak > 0:
            drawdown = (peak - cumulative) / peak
            if drawdown > self.DRAWDOWN_KILL_PCT:
                self.state["kill_switch_active"] = True
                logger.warning(f"TUNER: Kill switch ACTIVATED (drawdown {drawdown:.1%})")
                return True

        return self.state.get("kill_switch_active", False)

    # --- Logging ---

    def _log(self, db, param, old, new, reason, window_size, window_wr, baseline_wr, cb_active):
        """Write adjustment to tuning_log table."""
        try:
            db.execute("""
                INSERT INTO tuning_log
                    (timestamp, parameter, old_value, new_value, reason,
                     window_size, window_wr, baseline_wr, circuit_breaker)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (int(time.time()), param, old, new, reason,
                  window_size, round(window_wr, 4) if window_wr else 0,
                  round(baseline_wr, 4) if baseline_wr else 0, int(cb_active)))
            db.commit()
        except Exception as e:
            logger.error(f"TUNER: Failed to log adjustment: {e}")
