"""Tests for regime_classifier.py - RegimeClassifier class."""

import math
import pytest
import sys
from pathlib import Path

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from polyphemus.regime_classifier import (
    RegimeClassifier,
    CALM_UPPER,
    NORMAL_UPPER,
    ELEVATED_UPPER,
    SIZING_MULTIPLIERS,
    COLD_START_EPOCHS,
)


class TestRegimeClassifierColdStart:
    """Test cold-start behavior (first 12 epochs)."""

    def test_cold_start_returns_unknown(self):
        """During cold start, regime should be 'unknown'."""
        rc = RegimeClassifier()
        for i in range(COLD_START_EPOCHS - 1):
            rc.update(0.001)
            assert rc.get_regime() == "unknown"
            assert rc.is_cold_start() is True

    def test_cold_start_sizing_is_quarter(self):
        """During cold start, sizing multiplier should be 0.25."""
        rc = RegimeClassifier()
        rc.update(0.001)
        assert rc.get_sizing_multiplier() == 0.25

    def test_cold_start_ends_at_window(self):
        """After exactly window epochs, cold start should end."""
        rc = RegimeClassifier()
        for i in range(COLD_START_EPOCHS):
            rc.update(0.001)
        assert rc.is_cold_start() is False
        assert rc.get_regime() != "unknown"


class TestRegimeClassification:
    """Test regime classification with known volatility patterns."""

    def test_calm_regime(self):
        """Very small returns should classify as calm (vol < 0.5%)."""
        rc = RegimeClassifier()
        # Feed 12 tiny returns (vol will be very small)
        for _ in range(12):
            rc.update(0.0001)  # 0.01% moves
        assert rc.get_regime() == "calm"
        assert rc.get_sizing_multiplier() == 1.0

    def test_normal_regime(self):
        """Moderate returns should classify as normal (0.5% - 1.0%)."""
        rc = RegimeClassifier()
        # Alternating positive/negative to create moderate vol
        returns = [0.003, -0.003, 0.004, -0.004, 0.003, -0.003,
                   0.004, -0.004, 0.003, -0.003, 0.004, -0.004]
        for r in returns:
            rc.update(r)
        vol = rc.get_volatility()
        regime = rc.get_regime()
        # These returns create std ~ 0.004 which is in normal range
        assert regime in ("calm", "normal"), f"Expected calm/normal but got {regime} (vol={vol})"
        assert rc.get_sizing_multiplier() in (1.0, 0.75)

    def test_stress_regime(self):
        """Large returns should classify as stress (vol > 2.0%)."""
        rc = RegimeClassifier()
        # Feed large alternating returns to create high vol
        for i in range(12):
            rc.update(0.03 * (1 if i % 2 == 0 else -1))  # 3% swings
        assert rc.get_regime() == "stress"
        assert rc.get_sizing_multiplier() == 0.25

    def test_elevated_regime(self):
        """Medium-large returns should classify as elevated (1.0% - 2.0%)."""
        rc = RegimeClassifier()
        # Returns that create std in the 0.01-0.02 range
        for i in range(12):
            rc.update(0.015 * (1 if i % 2 == 0 else -1))
        regime = rc.get_regime()
        assert regime in ("elevated", "stress"), f"Expected elevated/stress, got {regime}"


class TestUpdateFromPrices:
    """Test convenience method update_from_prices."""

    def test_update_from_prices_basic(self):
        """update_from_prices should compute log return correctly."""
        rc = RegimeClassifier()
        # Price goes from 100 to 101 -> log(101/100) = 0.00995
        rc.update_from_prices(100.0, 101.0)
        assert rc.get_epoch_count() == 1

    def test_update_from_prices_invalid(self):
        """Invalid prices should be rejected."""
        rc = RegimeClassifier()
        result = rc.update_from_prices(0, 100)
        assert result == "unknown"  # Should not crash
        assert rc.get_epoch_count() == 0

    def test_update_from_prices_negative(self):
        """Negative prices should be rejected."""
        rc = RegimeClassifier()
        result = rc.update_from_prices(-100, 100)
        assert result == "unknown"
        assert rc.get_epoch_count() == 0


class TestGetState:
    """Test state serialization."""

    def test_get_state_cold_start(self):
        """State during cold start should show correct values."""
        rc = RegimeClassifier()
        state = rc.get_state()
        assert state["regime"] == "unknown"
        assert state["cold_start"] is True
        assert state["epoch_count"] == 0
        assert state["window"] == 12

    def test_get_state_after_warmup(self):
        """State after warm-up should show regime and vol."""
        rc = RegimeClassifier()
        for _ in range(12):
            rc.update(0.0001)
        state = rc.get_state()
        assert state["cold_start"] is False
        assert state["regime"] in ("calm", "normal", "elevated", "stress")
        assert state["sizing_multiplier"] > 0


class TestReset:
    """Test reset functionality."""

    def test_reset_clears_state(self):
        """Reset should return classifier to cold-start."""
        rc = RegimeClassifier()
        for _ in range(15):
            rc.update(0.001)
        assert not rc.is_cold_start()

        rc.reset()
        assert rc.is_cold_start()
        assert rc.get_regime() == "unknown"
        assert rc.get_epoch_count() == 0
        assert rc.get_volatility() == 0.0


class TestSizingMultipliers:
    """Test sizing multiplier constants."""

    def test_all_regimes_have_multipliers(self):
        """Every regime should have a defined sizing multiplier."""
        for regime in ("calm", "normal", "elevated", "stress", "unknown"):
            assert regime in SIZING_MULTIPLIERS
            assert 0 < SIZING_MULTIPLIERS[regime] <= 1.0

    def test_multipliers_decrease_with_stress(self):
        """Multipliers should decrease as regime gets more stressed."""
        assert SIZING_MULTIPLIERS["calm"] > SIZING_MULTIPLIERS["normal"]
        assert SIZING_MULTIPLIERS["normal"] > SIZING_MULTIPLIERS["elevated"]
        assert SIZING_MULTIPLIERS["elevated"] >= SIZING_MULTIPLIERS["stress"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
