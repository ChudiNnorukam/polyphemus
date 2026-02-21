"""Adaptive Tuner — autonomous parameter optimization for the accumulator.

Every 15 minutes, analyzes own performance + gabagool insights,
adjusts ONE parameter at a time with hard guardrails.

Overrides persist to adaptive_state.json between restarts.
On startup, loaded overrides are validated and clamped to TUNABLE_PARAMS bounds.
"""

import asyncio
import json
import os
import time
import tempfile
from dataclasses import dataclass
from typing import Optional

from .config import Settings, setup_logger
from .accumulator_metrics import AccumulatorMetrics, MetricsSnapshot
from .gabagool_tracker import GabagoolTracker, GabagoolInsights


@dataclass
class TunableBounds:
    """Hard min/max/step for a tunable parameter."""
    min_val: float
    max_val: float
    step: float


# Hard guardrails — cannot be overridden
TUNABLE_PARAMS = {
    "accum_max_pair_cost":       TunableBounds(0.990, 0.998, 0.001),
    "accum_order_timeout":       TunableBounds(5, 30, 2),
    "accum_reprice_limit":       TunableBounds(5, 25, 2),
    "accum_scan_interval":       TunableBounds(1, 10, 1),
    "accum_min_profit_per_share": TunableBounds(0.002, 0.02, 0.001),
}


@dataclass
class Adjustment:
    """Record of a single parameter adjustment."""
    param: str
    old_value: float
    new_value: float
    reason: str
    timestamp: float
    metrics_before: dict  # Snapshot at time of decision


class AdaptiveTuner:
    """Autonomous parameter tuner with one-variable-at-a-time discipline."""

    TUNE_INTERVAL = 900  # 15 minutes
    REVERT_CHECK_CYCLES = 3  # Revert if PnL drops after 3 cycles
    HOURLY_LOSS_FREEZE = -10.0  # Freeze tuning if hourly PnL below this

    def __init__(
        self,
        metrics: AccumulatorMetrics,
        tracker: Optional[GabagoolTracker],
        config: Settings,
        state_path: str = "data/adaptive_state.json",
    ):
        self._metrics = metrics
        self._tracker = tracker
        self._config = config
        self._state_path = state_path
        self._logger = setup_logger("polyphemus.adaptive_tuner")

        # In-memory overrides (lost on restart = safe)
        self.current_overrides: dict[str, float] = {}

        # Adjustment history
        self._adjustments: list[Adjustment] = []
        self._frozen: bool = False
        self._frozen_reason: str = ""
        self._last_tune_ts: float = 0.0
        self._tune_count: int = 0
        self._last_adjustment_tune_count: int = 0

        # Load persisted state (overrides + history)
        self._load_state()

    async def start(self):
        """Main tuning loop — runs every 15 minutes."""
        self._logger.info(
            f"Adaptive tuner started | interval={self.TUNE_INTERVAL}s | "
            f"overrides={self.current_overrides}"
        )
        while True:
            await asyncio.sleep(self.TUNE_INTERVAL)
            try:
                await self._tune_cycle()
            except Exception as e:
                self._logger.error(f"Tune cycle error: {e}")

    async def _tune_cycle(self):
        """One tuning cycle: analyze → decide → adjust (one param only)."""
        self._tune_count += 1
        stats = self._metrics.get_stats(window_mins=60)

        # Safety: freeze if losing too much
        if stats.total_pnl < self.HOURLY_LOSS_FREEZE and stats.total_cycles > 3:
            self._frozen = True
            self._frozen_reason = f"hourly_pnl=${stats.total_pnl:.2f} < ${self.HOURLY_LOSS_FREEZE}"
            self._logger.warning(f"Tuning FROZEN: {self._frozen_reason}")
            return

        if self._frozen:
            # Unfreeze if PnL recovered
            if stats.total_pnl >= 0:
                self._frozen = False
                self._frozen_reason = ""
                self._logger.info("Tuning UNFROZEN: PnL recovered")
            else:
                return

        # Check if last adjustment should be reverted
        if self._should_revert(stats):
            self._revert_last()
            return

        # Get gabagool insights
        insights = self._tracker.get_insights() if self._tracker else None

        # Decision priority chain (one-variable-at-a-time)
        adjusted = False

        # P1: Fix high orphan rate (most destructive)
        if stats.total_cycles >= 3 and stats.orphan_rate > 0.30:
            adjusted = self._adjust(
                "accum_reprice_limit", +1,
                f"orphan_rate={stats.orphan_rate:.0%} > 30%",
                stats,
            )
            if adjusted:
                return

        # P2: Fix low hedge rate
        if stats.total_cycles >= 3 and stats.hedge_rate < 0.50:
            adjusted = self._adjust(
                "accum_max_pair_cost", -1,
                f"hedge_rate={stats.hedge_rate:.0%} < 50%",
                stats,
            )
            if adjusted:
                return

        # P3: Learn from gabagool pair cost (use median — robust to outliers)
        gabagool_ceiling = 0.998  # default max
        if insights and insights.total_pairs >= 5:
            ref_pair_cost = insights.median_pair_cost if insights.median_pair_cost > 0 else insights.avg_pair_cost
            if ref_pair_cost > 0:
                gabagool_ceiling = ref_pair_cost + 0.015
                current_limit = self._get_current("accum_max_pair_cost")
                gap = current_limit - ref_pair_cost
                if gap > 0.01:
                    steps = 2 if gap > 0.03 else 1
                    for _ in range(steps):
                        adjusted = self._adjust(
                            "accum_max_pair_cost", -1,
                            f"gabagool_median=${ref_pair_cost:.4f} < our_limit=${current_limit:.4f} (gap={gap:.3f})",
                            stats,
                        )
                    if adjusted:
                        return

        # P4: Calibrate order timeout from gabagool fill gap timing
        if insights and insights.avg_fill_gap_secs > 0 and insights.total_pairs >= 5:
            target_timeout = max(5, int(insights.avg_fill_gap_secs * 1.5))
            current_timeout = self._get_current("accum_order_timeout")
            if abs(current_timeout - target_timeout) > 3:
                direction = 1 if target_timeout > current_timeout else -1
                adjusted = self._adjust(
                    "accum_order_timeout", direction,
                    f"gabagool_fill_gap={insights.avg_fill_gap_secs:.1f}s → target_timeout={target_timeout}s (current={current_timeout:.0f}s)",
                    stats,
                )
                if adjusted:
                    return
        elif stats.total_cycles >= 3 and stats.avg_reprices > 5:
            adjusted = self._adjust(
                "accum_order_timeout", +1,
                f"avg_reprices={stats.avg_reprices:.1f} > 5 (orders timing out)",
                stats,
            )
            if adjusted:
                return

        # P5: Expand if profitable — but NEVER above gabagool ceiling
        if stats.total_cycles >= 5 and stats.hedge_rate > 0.80 and stats.avg_pnl_per_hedged > 0:
            current_limit = self._get_current("accum_max_pair_cost")
            if current_limit < gabagool_ceiling:
                adjusted = self._adjust(
                    "accum_max_pair_cost", +1,
                    f"hedge_rate={stats.hedge_rate:.0%} > 80% & profitable, expanding (ceiling={gabagool_ceiling:.4f})",
                    stats,
                )
                if adjusted:
                    return

        # P6: Learn scan interval from gabagool activity
        if insights and insights.trades_per_hour > 20:
            adjusted = self._adjust(
                "accum_scan_interval", -1,
                f"gabagool_tph={insights.trades_per_hour:.0f} > 20, scanning faster",
                stats,
            )
            if adjusted:
                return

        self._logger.debug(
            f"Tune #{self._tune_count}: no adjustment needed | "
            f"cycles={stats.total_cycles} hedge_rate={stats.hedge_rate:.0%} "
            f"pnl=${stats.total_pnl:.2f}"
        )

    def _adjust(self, param: str, direction: int, reason: str, stats: MetricsSnapshot) -> bool:
        """Adjust a parameter by one step in the given direction.

        Returns True if adjustment was made, False if at bounds.
        """
        bounds = TUNABLE_PARAMS.get(param)
        if not bounds:
            return False

        current = self._get_current(param)
        new_val = current + (direction * bounds.step)

        # Clamp to bounds
        new_val = max(bounds.min_val, min(bounds.max_val, new_val))

        # No change? At bounds already
        if abs(new_val - current) < bounds.step * 0.1:
            self._logger.debug(f"At bounds: {param}={current} (limit={bounds.min_val}-{bounds.max_val})")
            return False

        # Apply override
        adj = Adjustment(
            param=param,
            old_value=current,
            new_value=new_val,
            reason=reason,
            timestamp=time.time(),
            metrics_before={
                "hedge_rate": stats.hedge_rate,
                "orphan_rate": stats.orphan_rate,
                "total_pnl": stats.total_pnl,
                "total_cycles": stats.total_cycles,
            },
        )
        self.current_overrides[param] = new_val
        self._adjustments.append(adj)
        self._last_adjustment_tune_count = self._tune_count

        self._logger.info(
            f"ADJUSTED: {param} {current:.4f} → {new_val:.4f} | reason: {reason}"
        )

        self._save_state()
        return True

    def _should_revert(self, stats: MetricsSnapshot) -> bool:
        """Check if last adjustment made things worse."""
        if not self._adjustments:
            return False

        last = self._adjustments[-1]
        cycles_since = self._tune_count - self._last_adjustment_tune_count

        # Only check after REVERT_CHECK_CYCLES
        if cycles_since < self.REVERT_CHECK_CYCLES:
            return False

        before_pnl = last.metrics_before.get("total_pnl", 0)
        # If PnL got worse by more than $2, revert
        if stats.total_pnl < before_pnl - 2.0:
            return True

        return False

    def _revert_last(self):
        """Revert the most recent adjustment."""
        if not self._adjustments:
            return

        last = self._adjustments[-1]
        self._logger.warning(
            f"REVERTING: {last.param} {last.new_value:.4f} → {last.old_value:.4f} | "
            f"original reason was: {last.reason}"
        )

        # Restore old value
        if last.old_value == getattr(self._config, last.param, None):
            # Revert to config default — remove override
            self.current_overrides.pop(last.param, None)
        else:
            self.current_overrides[last.param] = last.old_value

        # Record revert as an adjustment
        self._adjustments.append(Adjustment(
            param=last.param,
            old_value=last.new_value,
            new_value=last.old_value,
            reason=f"revert (pnl deteriorated after: {last.reason})",
            timestamp=time.time(),
            metrics_before={},
        ))

        self._save_state()

    def _get_current(self, param: str) -> float:
        """Get current effective value for a parameter."""
        if param in self.current_overrides:
            return self.current_overrides[param]
        return getattr(self._config, param, 0.0)

    def get_param(self, param: str) -> float:
        """Public accessor for accumulator to read tuned params."""
        return self._get_current(param)

    # ========================================================================
    # State Persistence
    # ========================================================================

    def _save_state(self):
        """Save overrides + last 20 adjustments to disk."""
        try:
            state = {
                "current_overrides": self.current_overrides,
                "adjustments": [
                    {
                        "param": a.param,
                        "old": a.old_value,
                        "new": a.new_value,
                        "reason": a.reason,
                        "ts": a.timestamp,
                    }
                    for a in self._adjustments[-20:]
                ],
                "frozen": self._frozen,
                "frozen_reason": self._frozen_reason,
                "tune_count": self._tune_count,
            }
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(self._state_path),
                prefix=".adaptive_",
                suffix=".json",
            )
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
            os.rename(tmp, self._state_path)
        except Exception as e:
            self._logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """Load persisted state on startup."""
        try:
            with open(self._state_path, "r") as f:
                state = json.load(f)
            self.current_overrides = state.get("current_overrides", {})
            self._frozen = state.get("frozen", False)
            self._frozen_reason = state.get("frozen_reason", "")
            self._tune_count = state.get("tune_count", 0)

            # Reconstruct adjustment history
            for a in state.get("adjustments", []):
                self._adjustments.append(Adjustment(
                    param=a["param"],
                    old_value=a["old"],
                    new_value=a["new"],
                    reason=a["reason"],
                    timestamp=a["ts"],
                    metrics_before={},
                ))

            # Clamp loaded overrides to TUNABLE_PARAMS bounds, drop unknown params
            for param in list(self.current_overrides.keys()):
                if param not in TUNABLE_PARAMS:
                    self._logger.warning(f"Dropping unknown override '{param}' from loaded state")
                    del self.current_overrides[param]
                    continue
                bounds = TUNABLE_PARAMS[param]
                val = self.current_overrides[param]
                clamped = max(bounds.min_val, min(bounds.max_val, val))
                if clamped != val:
                    self._logger.warning(f"Clamped override {param}: {val} → {clamped}")
                    self.current_overrides[param] = clamped

            if self.current_overrides:
                self._logger.info(f"Loaded {len(self.current_overrides)} overrides: {self.current_overrides}")
        except FileNotFoundError:
            pass
        except Exception as e:
            self._logger.warning(f"Failed to load state: {e}")

    # ========================================================================
    # Dashboard
    # ========================================================================

    def get_state(self) -> dict:
        """Full state for dashboard API."""
        stats = self._metrics.get_stats(window_mins=60)
        return {
            "frozen": self._frozen,
            "frozen_reason": self._frozen_reason,
            "tune_count": self._tune_count,
            "current_overrides": self.current_overrides,
            "last_5_adjustments": [
                {
                    "param": a.param,
                    "old": round(a.old_value, 4),
                    "new": round(a.new_value, 4),
                    "reason": a.reason,
                    "ts": a.timestamp,
                }
                for a in self._adjustments[-5:]
            ],
            "hourly_stats": {
                "total_cycles": stats.total_cycles,
                "hedge_rate": round(stats.hedge_rate, 3),
                "orphan_rate": round(stats.orphan_rate, 3),
                "total_pnl": round(stats.total_pnl, 2),
                "avg_pair_cost": round(stats.avg_pair_cost, 4),
            },
            "param_bounds": {
                k: {"min": v.min_val, "max": v.max_val, "step": v.step}
                for k, v in TUNABLE_PARAMS.items()
            },
        }
