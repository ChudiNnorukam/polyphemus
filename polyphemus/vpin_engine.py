"""VPIN Engine - Volume-Synchronized Probability of Informed Trading.

Production VPIN calculator with Bulk Volume Classification,
adaptive quote engine, and LOB imbalance for Polymarket market making.

Reference: Easley, Lopez de Prado, O'Hara (2012) -
"Flow Toxicity and Liquidity in a High Frequency World"
"""

import math
import time
from collections import deque
from typing import Optional


class VPINCalculator:
    """Volume-synchronized VPIN using Bulk Volume Classification.

    Samples in equal-volume buckets (not time buckets) so each sample
    contains comparable information content regardless of activity level.

    Args:
        bucket_volume: Volume threshold per bucket (USDC).
            For a market with ~$100k daily volume, try $2000.
            For crypto 5-min markets, try $500-$2000.
        n_buckets: Rolling window size. 50 is standard.
            Lower = more reactive but noisier.
            Higher = smoother but slower regime detection.
    """

    def __init__(self, bucket_volume: float = 1000.0, n_buckets: int = 50):
        self.bucket_volume = max(bucket_volume, 1.0)
        self.n_buckets = max(n_buckets, 5)
        self.buckets: deque = deque(maxlen=n_buckets)

        # Current bucket accumulation
        self._current_buy = 0.0
        self._current_sell = 0.0
        self._current_volume = 0.0

        # Sigma estimation from recent price changes
        self._recent_deltas: deque = deque(maxlen=200)
        self._sigma = 0.01  # default until calibrated

        # Timing
        self._last_update = 0.0
        self._total_volume_processed = 0.0

    def update(self, price_change: float, volume: float) -> Optional[float]:
        """Feed a trade or bar into the calculator.

        Args:
            price_change: Price delta (close - open of bar, or trade price change).
            volume: Volume in USDC.

        Returns:
            Current VPIN (0-1) or None if insufficient data.
        """
        if volume <= 0:
            return self._current_vpin()

        self._last_update = time.time()
        self._total_volume_processed += volume

        # Update sigma estimate
        self._recent_deltas.append(price_change)
        if len(self._recent_deltas) >= 20:
            deltas = list(self._recent_deltas)
            mean = sum(deltas) / len(deltas)
            variance = sum((d - mean) ** 2 for d in deltas) / len(deltas)
            self._sigma = max(math.sqrt(variance), 0.0001)

        # Bulk Volume Classification using CDF approximation
        z = price_change / self._sigma if self._sigma > 0 else 0.0
        buy_frac = _norm_cdf(z)

        self._current_buy += volume * buy_frac
        self._current_sell += volume * (1.0 - buy_frac)
        self._current_volume += volume

        # Fill buckets
        while self._current_volume >= self.bucket_volume:
            scale = self.bucket_volume / self._current_volume
            bucket_buy = self._current_buy * scale
            bucket_sell = self._current_sell * scale
            bucket_total = bucket_buy + bucket_sell

            if bucket_total > 0:
                imbalance = abs(bucket_buy - bucket_sell) / bucket_total
                self.buckets.append(imbalance)

            # Carry overflow
            self._current_buy *= (1.0 - scale)
            self._current_sell *= (1.0 - scale)
            self._current_volume -= self.bucket_volume

        return self._current_vpin()

    def _current_vpin(self) -> Optional[float]:
        """Return current VPIN if enough buckets collected."""
        if len(self.buckets) >= self.n_buckets:
            return sum(self.buckets) / len(self.buckets)
        return None

    def get_vpin(self) -> Optional[float]:
        """Return the latest VPIN reading."""
        return self._current_vpin()

    def get_sustained_alert(self, threshold: float = 0.7, min_bars: int = 8) -> bool:
        """Check if VPIN has been above threshold for min_bars consecutive buckets.

        A single spike is noise. Sustained elevation is a signal.
        """
        if len(self.buckets) < min_bars:
            return False
        recent = list(self.buckets)[-min_bars:]
        return all(v > threshold for v in recent)

    def is_stale(self, max_age_secs: float = 30.0) -> bool:
        """True if no updates received within max_age_secs."""
        if self._last_update == 0:
            return True
        return (time.time() - self._last_update) > max_age_secs

    @property
    def buckets_filled(self) -> int:
        return len(self.buckets)

    @property
    def ready(self) -> bool:
        return len(self.buckets) >= self.n_buckets


class AdaptiveQuoteEngine:
    """Adjusts spread and quoting based on VPIN toxicity level.

    Tighter spreads = more maker rebate rewards (quadratic scoring).
    But tight spreads during toxic flow = getting picked off by informed traders.
    VPIN tells us when to widen or pull.
    """

    def __init__(
        self,
        base_spread: float = 0.02,
        min_spread: float = 0.01,
        max_spread: float = 0.10,
        vpin_safe: float = 0.40,
        vpin_elevated: float = 0.55,
        vpin_high: float = 0.65,
        vpin_kill: float = 0.75,
    ):
        self.base_spread = base_spread
        self.min_spread = min_spread
        self.max_spread = max_spread
        self.vpin_safe = vpin_safe
        self.vpin_elevated = vpin_elevated
        self.vpin_high = vpin_high
        self.vpin_kill = vpin_kill

    def get_spread_multiplier(self, vpin: Optional[float]) -> float:
        """Return spread multiplier based on current VPIN.

        Returns:
            1.0 = normal, 1.5 = elevated, 2.0 = high, inf = pull quotes.
        """
        if vpin is None:
            return 1.5  # Conservative when no data

        if vpin < self.vpin_safe:
            return 1.0
        elif vpin < self.vpin_elevated:
            return 1.3
        elif vpin < self.vpin_high:
            return 1.8
        elif vpin < self.vpin_kill:
            return 2.5
        else:
            return float('inf')  # Pull quotes

    def should_pull_quotes(self, vpin: Optional[float], vpin_calc: Optional[VPINCalculator] = None) -> bool:
        """Hard stop: pull all quotes when toxicity is dangerous."""
        if vpin is not None and vpin >= self.vpin_kill:
            return True
        if vpin_calc and vpin_calc.get_sustained_alert(self.vpin_high, 5):
            return True
        return False

    def compute_quotes(
        self,
        mid: float,
        vpin: Optional[float],
        inventory_ratio: float = 0.5,
        urgency: float = 1.0,
    ) -> Optional[tuple]:
        """Compute bid/ask prices given VPIN and inventory state.

        Args:
            mid: Current midpoint price.
            vpin: Current VPIN reading (0-1 or None).
            inventory_ratio: YES / (YES + NO). 0.5 = balanced.
            urgency: Multiplier for inventory skew (increases near resolution).

        Returns:
            (bid, ask) tuple, or None if quotes should be pulled.
        """
        multiplier = self.get_spread_multiplier(vpin)
        if multiplier == float('inf'):
            return None

        spread = min(self.base_spread * multiplier, self.max_spread)
        spread = max(spread, self.min_spread)

        # Inventory skew: shift quotes to attract rebalancing flow
        max_skew = spread * urgency
        skew = (inventory_ratio - 0.5) * 2.0 * max_skew

        half_spread = spread / 2.0
        bid = mid - half_spread - skew
        ask = mid + half_spread - skew

        # Clamp to valid price range
        bid = max(0.01, min(bid, mid - 0.005))
        ask = min(0.99, max(ask, mid + 0.005))

        # Ensure bid < ask
        if bid >= ask:
            return None

        return (round(bid, 2), round(ask, 2))


def calculate_lob_imbalance(bids: list, asks: list, levels: int = 5) -> float:
    """Calculate multi-level limit order book imbalance.

    Args:
        bids: List of bid orders [{price, size}, ...] sorted best-first.
        asks: List of ask orders [{price, size}, ...] sorted best-first.
        levels: Number of price levels to consider.

    Returns:
        -1 (heavy ask pressure) to +1 (heavy bid pressure).
        Extreme values suggest directional pressure.
    """
    bid_depth = 0.0
    for order in bids[:levels]:
        size = order.get("size", 0)
        if isinstance(size, str):
            size = float(size)
        bid_depth += size

    ask_depth = 0.0
    for order in asks[:levels]:
        size = order.get("size", 0)
        if isinstance(size, str):
            size = float(size)
        ask_depth += size

    total = bid_depth + ask_depth
    if total == 0:
        return 0.0

    return (bid_depth - ask_depth) / total


def resolution_urgency(minutes_to_resolution: float, urgency_start_minutes: float = 3.0) -> float:
    """Compute urgency multiplier for inventory skew as resolution approaches.

    For crypto 5-min markets: urgency_start_minutes = 3
    For crypto 15-min markets: urgency_start_minutes = 10

    Returns:
        1.0 (normal) to 4.0 (emergency rebalance).
    """
    if minutes_to_resolution >= urgency_start_minutes:
        return 1.0

    if minutes_to_resolution <= 0:
        return 4.0

    fraction_remaining = minutes_to_resolution / urgency_start_minutes
    return 1.0 + (3.0 * (1.0 - fraction_remaining))


def _norm_cdf(x: float) -> float:
    """Fast approximation of the standard normal CDF.

    Abramowitz & Stegun approximation (error < 7.5e-8).
    Avoids scipy dependency for production use.
    """
    if x >= 0:
        t = 1.0 / (1.0 + 0.2316419 * x)
        d = 0.3989422804014327  # 1/sqrt(2*pi)
        poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
        return 1.0 - d * math.exp(-0.5 * x * x) * poly
    else:
        return 1.0 - _norm_cdf(-x)
