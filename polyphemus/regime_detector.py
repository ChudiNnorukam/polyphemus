"""RegimeDetector — Rule-based market regime classification.

Classifies current market conditions using Binance price data already
streaming through BinanceMomentumFeed. Exposes get_regime() for use
by signal scorer and signal guard.

Regimes:
- TRENDING: Sustained directional move (>0.5% in 1h)
- VOLATILE: Large swings both directions (>1% range in 1h)
- FLAT: Low movement (<0.2% range in 1h) — noise signals likely
- SHOCK: Sudden extreme move (>2% in 5min) — be cautious
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict

from .config import setup_logger


@dataclass
class RegimeState:
    """Current market regime assessment."""
    regime: str  # "trending", "volatile", "flat", "shock"
    volatility_1h: float  # 1h price range as %
    trend_1h: float  # 1h net directional move as % (signed)
    trend_5m: float  # 5m net move as %
    confidence: float  # 0-1 confidence in classification
    updated_at: float  # epoch timestamp
    funding_rate: float = 0.0  # latest 8h funding rate (signed)
    funding_updated_at: float = 0.0  # when funding was last fetched
    liq_volume_60s: float = 0.0  # rolling 60s liquidation volume (USD)
    liq_bias: str = ""  # "long" (longs liquidated = bearish) or "short" or ""


class RegimeDetector:
    """Classifies market regime from streaming Binance price data.

    Consumes the same price updates as BinanceMomentumFeed without
    requiring a separate WebSocket connection. Call update() with each
    new price, and get_regime() to read the current state.
    """

    # Thresholds (can be tuned)
    SHOCK_THRESHOLD_5M = 0.02      # 2% move in 5min = shock
    TRENDING_THRESHOLD_1H = 0.005  # 0.5% net move in 1h = trending
    VOLATILE_THRESHOLD_1H = 0.01   # 1% range in 1h = volatile
    FLAT_THRESHOLD_1H = 0.002      # 0.2% range in 1h = flat

    def __init__(self):
        self._logger = setup_logger("polyphemus.regime_detector")
        # Price buffers per asset (deque of (epoch, price))
        self._buffers: Dict[str, deque] = {}
        self._regimes: Dict[str, RegimeState] = {}
        self._logger.info("RegimeDetector initialized")

    def update(self, asset: str, price: float, epoch: Optional[float] = None):
        """Feed a new price tick. Called from BinanceMomentumFeed.

        Args:
            asset: "BTC", "ETH", or "SOL"
            price: Current price from Binance
            epoch: Optional timestamp (defaults to now)
        """
        now = epoch or time.time()
        asset = asset.upper()

        if asset not in self._buffers:
            self._buffers[asset] = deque(maxlen=3600)  # 1h at 1/sec

        self._buffers[asset].append((now, price))

        # Recalculate regime every 10 ticks (not every tick)
        if len(self._buffers[asset]) % 10 == 0:
            self._classify(asset, now)

    def _classify(self, asset: str, now: float):
        """Classify the current regime for an asset."""
        buf = self._buffers.get(asset)
        if not buf or len(buf) < 30:
            return  # Need at least 30 data points

        prices = [p for _, p in buf]
        times = [t for t, _ in buf]

        # Current price
        current = prices[-1]

        # 5-minute window
        cutoff_5m = now - 300
        prices_5m = [p for t, p in buf if t >= cutoff_5m]
        if prices_5m:
            trend_5m = (current - prices_5m[0]) / prices_5m[0]
        else:
            trend_5m = 0.0

        # 1-hour window (or all available data)
        cutoff_1h = now - 3600
        prices_1h = [p for t, p in buf if t >= cutoff_1h]
        if len(prices_1h) < 10:
            prices_1h = prices  # Use all available

        high_1h = max(prices_1h)
        low_1h = min(prices_1h)
        range_1h = (high_1h - low_1h) / low_1h if low_1h > 0 else 0
        trend_1h = (current - prices_1h[0]) / prices_1h[0] if prices_1h[0] > 0 else 0

        # Classify
        regime = "volatile"  # default
        confidence = 0.5

        if abs(trend_5m) >= self.SHOCK_THRESHOLD_5M:
            regime = "shock"
            confidence = min(abs(trend_5m) / self.SHOCK_THRESHOLD_5M, 1.0)
        elif range_1h < self.FLAT_THRESHOLD_1H:
            regime = "flat"
            confidence = 1.0 - (range_1h / self.FLAT_THRESHOLD_1H)
        elif abs(trend_1h) >= self.TRENDING_THRESHOLD_1H:
            regime = "trending"
            confidence = min(abs(trend_1h) / (self.TRENDING_THRESHOLD_1H * 2), 1.0)
        elif range_1h >= self.VOLATILE_THRESHOLD_1H:
            regime = "volatile"
            confidence = min(range_1h / (self.VOLATILE_THRESHOLD_1H * 2), 1.0)

        self._regimes[asset] = RegimeState(
            regime=regime,
            volatility_1h=round(range_1h, 6),
            trend_1h=round(trend_1h, 6),
            trend_5m=round(trend_5m, 6),
            confidence=round(confidence, 3),
            updated_at=now,
        )

    def get_regime(self, asset: str = "BTC") -> RegimeState:
        """Get current regime for an asset.

        Args:
            asset: Asset name (default "BTC" as market leader)

        Returns:
            RegimeState with current classification.
            Returns default "volatile" if no data yet.
        """
        asset = asset.upper()
        if asset in self._regimes:
            return self._regimes[asset]

        return RegimeState(
            regime="volatile",
            volatility_1h=0.0,
            trend_1h=0.0,
            trend_5m=0.0,
            confidence=0.0,
            updated_at=0.0,
        )

    def should_trade(self, asset: str = "BTC") -> bool:
        """Quick check: should we trade in this regime?

        Returns False only for 'flat' regime (noise signals).
        Shock regime still trades — momentum is real, just be cautious.
        """
        state = self.get_regime(asset)

        if state.regime == "flat" and state.confidence > 0.7:
            self._logger.debug(
                f"Regime FLAT for {asset} (vol={state.volatility_1h:.4f}, "
                f"conf={state.confidence:.2f}) — suppressing signals"
            )
            return False

        return True

    def update_funding(self, asset: str, rate: float):
        """Update funding rate for an asset. Called from async polling task.

        Args:
            asset: "BTC", "ETH", etc.
            rate: 8-hour funding rate (e.g., 0.0001 = 0.01%)
        """
        asset = asset.upper()
        now = time.time()
        state = self.get_regime(asset)
        state.funding_rate = rate
        state.funding_updated_at = now
        self._regimes[asset] = state
        self._logger.info(
            f"Funding rate updated: {asset} = {rate:.6f} "
            f"({'OVERHEATED' if abs(rate) > 0.0005 else 'normal'})"
        )

    def update_liquidation(self, asset: str, volume_60s: float, bias: str):
        """Update rolling 60s liquidation volume. Called from liquidation WS.

        Args:
            asset: "BTC", "ETH", etc.
            volume_60s: Total USD volume liquidated in last 60s.
            bias: "long" (longs liquidated = sell pressure) or "short" or "".
        """
        asset = asset.upper()
        state = self.get_regime(asset)
        state.liq_volume_60s = volume_60s
        state.liq_bias = bias
        self._regimes[asset] = state

    def is_overheated(self, asset: str = "BTC") -> bool:
        """Check if funding rate indicates overheated leverage.

        Returns True when |funding_rate| > 0.05% (8h rate),
        indicating crowded positioning likely to cascade.
        """
        state = self.get_regime(asset)
        return abs(state.funding_rate) > 0.0005  # 0.05%

    def get_liquidation_conviction(self, asset: str, direction: str) -> float:
        """Get conviction boost from liquidation data.

        Returns 0.0-1.0 boost. Higher when liquidation cascade
        aligns with signal direction.

        Args:
            asset: "BTC", "ETH", etc.
            direction: "UP" or "DOWN" (our signal direction)
        """
        state = self.get_regime(asset)
        if state.liq_volume_60s <= 0:
            return 0.0

        # Longs liquidated = sell pressure = DOWN signal aligns
        # Shorts liquidated = buy pressure = UP signal aligns
        if state.liq_bias == "long" and direction == "DOWN":
            return min(state.liq_volume_60s / 10_000_000, 1.0)  # normalize to $10M
        elif state.liq_bias == "short" and direction == "UP":
            return min(state.liq_volume_60s / 10_000_000, 1.0)
        return 0.0

    def get_all_regimes(self) -> Dict[str, dict]:
        """Get all regime states for dashboard."""
        result = {}
        for asset, state in self._regimes.items():
            result[asset] = {
                "regime": state.regime,
                "volatility_1h": state.volatility_1h,
                "trend_1h": state.trend_1h,
                "trend_5m": state.trend_5m,
                "confidence": state.confidence,
                "age_secs": round(time.time() - state.updated_at, 1),
                "funding_rate": state.funding_rate,
                "liq_volume_60s": state.liq_volume_60s,
                "liq_bias": state.liq_bias,
            }
        return result
