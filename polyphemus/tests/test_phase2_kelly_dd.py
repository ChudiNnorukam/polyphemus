"""Phase 2 tests for kelly_with_drawdown_feedback.

Linear drawdown brake on the Gaussian-approximation Kelly fraction. The
brake must be monotonic (no DD means no brake; at DD limit no bet), and
must fail closed on undefined inputs (negative edge, zero variance, zero
DD limit).
"""

import pytest

from polyphemus.prediction_markets.shared.kelly import kelly_with_drawdown_feedback


class TestKellyDDBasic:
    def test_no_drawdown_returns_full_kelly(self):
        # edge=0.05, var=0.10 -> raw Kelly = 0.5
        assert kelly_with_drawdown_feedback(0.05, 0.10, 0.0, 100.0) == 0.5

    def test_half_drawdown_halves_fraction(self):
        # Linear scale: current/limit = 0.5 -> multiplier = 0.5
        assert kelly_with_drawdown_feedback(0.05, 0.10, 50.0, 100.0) == 0.25

    def test_at_drawdown_limit_returns_zero(self):
        assert kelly_with_drawdown_feedback(0.05, 0.10, 100.0, 100.0) == 0.0

    def test_beyond_drawdown_limit_returns_zero(self):
        assert kelly_with_drawdown_feedback(0.05, 0.10, 150.0, 100.0) == 0.0

    def test_monotonic_decreasing_in_drawdown(self):
        """As current_dd grows, fraction can only shrink."""
        dds = [0, 10, 25, 50, 75, 90, 100]
        fractions = [
            kelly_with_drawdown_feedback(0.05, 0.10, dd, 100.0)
            for dd in dds
        ]
        for prev, nxt in zip(fractions, fractions[1:]):
            assert nxt <= prev, f"fraction must be monotone decreasing; got {fractions}"


class TestKellyDDFailClosed:
    def test_negative_edge_returns_zero(self):
        assert kelly_with_drawdown_feedback(-0.05, 0.10, 0.0, 100.0) == 0.0

    def test_zero_edge_returns_zero(self):
        assert kelly_with_drawdown_feedback(0.0, 0.10, 0.0, 100.0) == 0.0

    def test_zero_variance_returns_zero(self):
        """Zero variance is undefined for Gaussian-approx Kelly; never bet."""
        assert kelly_with_drawdown_feedback(0.05, 0.0, 0.0, 100.0) == 0.0

    def test_negative_variance_returns_zero(self):
        assert kelly_with_drawdown_feedback(0.05, -0.10, 0.0, 100.0) == 0.0

    def test_zero_dd_limit_returns_zero(self):
        """No drawdown budget -> no bet. Avoids div-by-zero and signals
        misconfiguration to the caller."""
        assert kelly_with_drawdown_feedback(0.05, 0.10, 0.0, 0.0) == 0.0

    def test_negative_current_dd_treated_as_zero(self):
        """A negative current DD is spurious input (we're in profit, not DD).
        Clamp to 0 so the brake is off rather than crashing or amplifying."""
        assert kelly_with_drawdown_feedback(0.05, 0.10, -25.0, 100.0) == 0.5


class TestKellyDDLinearity:
    def test_scaling_factor_is_linear(self):
        """The fraction should scale as (1 - current_dd/dd_limit)."""
        raw = 0.05 / 0.10  # 0.5
        for dd in [0, 20, 40, 60, 80, 100]:
            expected = round(raw * (1 - dd / 100.0), 4)
            actual = kelly_with_drawdown_feedback(0.05, 0.10, float(dd), 100.0)
            assert actual == pytest.approx(expected, abs=1e-4), \
                f"dd={dd}: expected {expected}, got {actual}"
