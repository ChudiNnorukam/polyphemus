"""regime_classifier.py - Market regime classification based on rolling 1h realized volatility.

Classifies market conditions into 4 regimes: calm, normal, elevated, stress.
Provides regime-adaptive position sizing multipliers for the prophetic strategy.

Regime boundaries (1h realized vol from Binance klines):
  calm:     < 0.5%
  normal:   0.5% - 1.0%
  elevated: 1.0% - 2.0%
  stress:   > 2.0%

Cold-start: returns 'unknown' regime for first 12 epochs (~1 hour at 5m).
"""

import logging
import math
from collections import deque
from typing import Optional


logger = logging.getLogger("polyphemus.regime")

# Regime thresholds (1h realized vol as decimal, e.g. 0.005 = 0.5%)
CALM_UPPER = 0.005       # < 0.5%
NORMAL_UPPER = 0.010     # 0.5% - 1.0%
ELEVATED_UPPER = 0.020   # 1.0% - 2.0%
# stress: > 2.0%

# Sizing multipliers per regime
SIZING_MULTIPLIERS = {
    "calm": 1.00,
    "normal": 0.75,
    "elevated": 0.50,
    "stress": 0.25,
    "unknown": 0.25,
}

# Cold-start: need at least 12 five-minute returns to compute 1h vol
COLD_START_EPOCHS = 12


class RegimeClassifier:
    """Classifies market regime from rolling 1h realized volatility.

    Feed it 5-minute log returns via update(). After cold-start period,
    get_regime() and get_sizing_multiplier() return meaningful values.

    Usage:
        classifier = RegimeClassifier()
        # Each epoch:
        classifier.update(log_return)
        regime = classifier.get_regime()
        multiplier = classifier.get_sizing_multiplier()
    """

    def __init__(self, window: int = COLD_START_EPOCHS) -> None:
        """Initialize the regime classifier.

        Args:
            window: Number of 5-minute returns for 1h rolling vol.
                    Default 12 (12 x 5min = 60min).
        """
        self._window = window
        self._returns: deque = deque(maxlen=window)
        self._epoch_count: int = 0
        self._current_regime: str = "unknown"
        self._current_vol: float = 0.0

    def update(self, log_return: float) -> str:
        """Add a new 5-minute log return and reclassify regime.

        Args:
            log_return: ln(close/open) for the latest 5-minute epoch.
                        E.g. for a 0.3% move: math.log(1.003) ~ 0.003.

        Returns:
            Current regime string after update.
        """
        self._returns.append(log_return)
        self._epoch_count += 1

        if self._epoch_count < self._window:
            self._current_regime = "unknown"
            self._current_vol = 0.0
            logger.debug(
                "Cold start: %d/%d epochs collected",
                self._epoch_count,
                self._window,
            )
            return self._current_regime

        # Compute realized vol: std of log returns, annualized is not needed
        # We use raw 1h realized vol (std of 12 five-minute returns)
        self._current_vol = self._compute_realized_vol()
        self._current_regime = self._classify(self._current_vol)

        logger.info(
            "Regime: %s (1h vol=%.4f%%, epochs=%d)",
            self._current_regime,
            self._current_vol * 100,
            self._epoch_count,
        )
        return self._current_regime

    def update_from_prices(self, open_price: float, close_price: float) -> str:
        """Convenience: compute log return from OHLC prices and update.

        Args:
            open_price: Epoch open price (e.g. BTC price at epoch start).
            close_price: Epoch close price.

        Returns:
            Current regime string after update.
        """
        if open_price <= 0 or close_price <= 0:
            logger.warning("Invalid prices: open=%.2f, close=%.2f", open_price, close_price)
            return self._current_regime
        log_ret = math.log(close_price / open_price)
        return self.update(log_ret)

    def get_regime(self) -> str:
        """Return the current regime classification.

        Returns:
            One of: 'calm', 'normal', 'elevated', 'stress', 'unknown'.
        """
        return self._current_regime

    def get_sizing_multiplier(self) -> float:
        """Return the position sizing multiplier for the current regime.

        Returns:
            Float multiplier: 1.0 (calm), 0.75 (normal), 0.50 (elevated),
            0.25 (stress), 0.25 (unknown/cold-start).
        """
        return SIZING_MULTIPLIERS.get(self._current_regime, 0.25)

    def get_volatility(self) -> float:
        """Return the current 1h realized volatility as a decimal.

        Returns:
            Float, e.g. 0.005 = 0.5%. Returns 0.0 during cold-start.
        """
        return self._current_vol

    def get_epoch_count(self) -> int:
        """Return the number of epochs processed so far."""
        return self._epoch_count

    def is_cold_start(self) -> bool:
        """Return True if still in cold-start period."""
        return self._epoch_count < self._window

    def get_state(self) -> dict:
        """Return full classifier state for logging/serialization."""
        return {
            "regime": self._current_regime,
            "volatility": round(self._current_vol, 6),
            "sizing_multiplier": self.get_sizing_multiplier(),
            "epoch_count": self._epoch_count,
            "cold_start": self.is_cold_start(),
            "window": self._window,
            "returns_buffer_size": len(self._returns),
        }

    def reset(self) -> None:
        """Reset classifier to initial state (cold-start)."""
        self._returns.clear()
        self._epoch_count = 0
        self._current_regime = "unknown"
        self._current_vol = 0.0

    def _compute_realized_vol(self) -> float:
        """Compute realized volatility as std dev of buffered returns."""
        n = len(self._returns)
        if n < 2:
            return 0.0
        mean = sum(self._returns) / n
        variance = sum((r - mean) ** 2 for r in self._returns) / (n - 1)
        return math.sqrt(variance)

    @staticmethod
    def _classify(vol: float) -> str:
        """Classify volatility into regime bucket.

        Args:
            vol: Realized volatility as decimal (e.g. 0.005 = 0.5%).

        Returns:
            Regime string.
        """
        if vol < CALM_UPPER:
            return "calm"
        elif vol < NORMAL_UPPER:
            return "normal"
        elif vol < ELEVATED_UPPER:
            return "elevated"
        else:
            return "stress"
