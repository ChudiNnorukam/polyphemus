"""test_trader_analyzer.py — Unit + integration tests for trader analysis pipeline.

Tests cover:
- trader_stats.py: hypothesis testing, Bayesian, regression, time series, validation, optimization
- trader_decompose.py: profile computation, entry price extraction, ReplicaStrategy generation
- trader_ingest.py: DB schema creation, trade count helpers

Run: python -m pytest polyphemus/tests/test_trader_analyzer.py -v
"""

import math
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


# ============================================================
#  trader_stats.py — Unit Tests
# ============================================================

class TestHypothesisWR:
    """Test /ds:hypothesis — z-test for win rate vs breakeven."""

    def test_significant_edge(self):
        """80/100 wins at breakeven 0.50 should be highly significant."""
        from polyphemus.tools.trader_stats import hypothesis_test_wr

        result = hypothesis_test_wr(wins=80, total=100, breakeven=0.50)
        assert result["p_value"] < 0.001, f"Expected p < 0.001, got {result['p_value']}"
        assert result["significant"] == True
        assert result["observed_wr"] == 0.80
        # Wilson CI should be roughly [0.71, 0.87]
        ci_low, ci_high = result["wilson_ci"]
        assert 0.70 <= ci_low <= 0.75, f"CI low {ci_low} outside expected range"
        assert 0.85 <= ci_high <= 0.90, f"CI high {ci_high} outside expected range"
        # Effect size should be medium-large
        assert result["effect_magnitude"] in ("medium", "large")
        assert result["r8_label"] == "SUBSTANTIAL"

    def test_insignificant_edge(self):
        """52/100 wins at breakeven 0.50 should not be significant."""
        from polyphemus.tools.trader_stats import hypothesis_test_wr

        result = hypothesis_test_wr(wins=52, total=100, breakeven=0.50)
        assert result["p_value"] > 0.05, f"Expected p > 0.05, got {result['p_value']}"
        assert result["significant"] == False

    def test_zero_trades(self):
        """0 trades should return safe defaults."""
        from polyphemus.tools.trader_stats import hypothesis_test_wr

        result = hypothesis_test_wr(wins=0, total=0)
        assert result["p_value"] == 1.0
        assert result["significant"] is False
        assert result["r8_label"] == "ANECDOTAL"

    def test_small_sample_r8(self):
        """Small sample gets ANECDOTAL label."""
        from polyphemus.tools.trader_stats import hypothesis_test_wr

        result = hypothesis_test_wr(wins=10, total=12, breakeven=0.50)
        assert result["r8_label"] == "ANECDOTAL"

    def test_moderate_sample_r8(self):
        """30-99 trades gets MODERATE label."""
        from polyphemus.tools.trader_stats import hypothesis_test_wr

        result = hypothesis_test_wr(wins=30, total=50, breakeven=0.50)
        assert result["r8_label"] == "MODERATE"


class TestWilsonCI:
    """Test Wilson score confidence interval."""

    def test_basic_ci(self):
        from polyphemus.tools.trader_stats import wilson_ci

        low, high = wilson_ci(80, 100)
        assert 0.70 < low < 0.75
        assert 0.85 < high < 0.90

    def test_zero_trades(self):
        from polyphemus.tools.trader_stats import wilson_ci

        assert wilson_ci(0, 0) == (0.0, 0.0)

    def test_all_wins(self):
        from polyphemus.tools.trader_stats import wilson_ci

        low, high = wilson_ci(100, 100)
        assert high <= 1.0
        assert low > 0.95


class TestBayesianUpdate:
    """Test /ds:bayesian — Beta-Binomial conjugate update."""

    def test_uninformative_prior(self):
        """Beta(1,1) + 8W/2L should give posterior mean near 0.75."""
        from polyphemus.tools.trader_stats import beta_binomial_update

        result = beta_binomial_update(1.0, 1.0, 8, 2)
        post_mean = result["posterior"]["mean"]
        # With Beta(1,1) prior and 8W/2L: posterior Beta(9,3), mean = 9/12 = 0.75
        assert 0.74 <= post_mean <= 0.76, f"Expected ~0.75, got {post_mean}"
        # HDI should be wide with small sample
        hdi_low, hdi_high = result["hdi_95"]
        assert hdi_low < 0.55
        assert hdi_high > 0.90

    def test_no_data(self):
        """No data should return prior."""
        from polyphemus.tools.trader_stats import beta_binomial_update

        result = beta_binomial_update(1.0, 1.0, 0, 0)
        assert result["posterior"]["mean"] == 0.5
        assert "TOTAL" in result["prior_influence"]


class TestDeflatedSharpe:
    """Test /ds:validate — Deflated Sharpe Ratio."""

    def test_positive_edge(self):
        """Stable positive returns should have reasonable DSR."""
        from polyphemus.tools.trader_stats import deflated_sharpe

        # 100 trades with net-positive returns (75% WR, asymmetric payoff)
        returns = [0.30] * 75 + [-0.50] * 25  # mean = 0.225 - 0.125 = 0.10
        result = deflated_sharpe(returns, k=3)
        assert result["sharpe_hat"] > 0, f"Expected positive Sharpe, got {result['sharpe_hat']}"
        assert "overfit_risk" in result

    def test_zero_mean_returns(self):
        """Zero-mean returns should have high overfit risk."""
        from polyphemus.tools.trader_stats import deflated_sharpe

        returns = [1.0, -1.0] * 50
        result = deflated_sharpe(returns, k=3)
        # Near-zero Sharpe with k>1 strategies tested should flag overfit
        assert result["sharpe_hat"] < 0.1 or result["overfit_risk"] in ("MODERATE", "HIGH")

    def test_insufficient_data(self):
        """< 5 trades should return safe defaults."""
        from polyphemus.tools.trader_stats import deflated_sharpe

        result = deflated_sharpe([1.0, -1.0], k=3)
        assert result["overfit_risk"] == "UNKNOWN"


class TestKellyCriterion:
    """Test /ds:optimize — Kelly criterion."""

    def test_positive_edge(self):
        """WR=0.75, avg_win=$0.25, avg_loss=$0.75 -> kelly fraction."""
        from polyphemus.tools.trader_stats import kelly_criterion

        result = kelly_criterion(0.75, 0.25, 0.75)
        # Kelly = (p*b - q) / b where b = avg_win/avg_loss = 0.333
        # f* = (0.75 * 0.333 - 0.25) / 0.333 = 0
        # Actually: b = 0.25/0.75 = 0.333, f* = (0.75*0.333 - 0.25)/0.333 = 0
        # Wait: b = avg_win/avg_loss = 0.25/0.75 = 1/3
        # f* = (p*b - q) / b = (0.75 * 1/3 - 0.25) / (1/3) = (0.25 - 0.25) / 0.333 = 0
        # Actually this is a borderline case. Let's use different numbers.
        assert result["full_kelly"] >= 0
        assert result["half_kelly"] >= 0

    def test_strong_edge(self):
        """Strong edge should give meaningful Kelly."""
        from polyphemus.tools.trader_stats import kelly_criterion

        result = kelly_criterion(0.80, 1.0, 1.0)
        # b = 1.0, f* = (0.80*1 - 0.20)/1 = 0.60
        assert 0.55 <= result["full_kelly"] <= 0.65
        assert 0.275 <= result["half_kelly"] <= 0.325

    def test_no_edge(self):
        """WR=0.40, even odds -> Kelly should be 0 (don't bet)."""
        from polyphemus.tools.trader_stats import kelly_criterion

        result = kelly_criterion(0.40, 1.0, 1.0)
        assert result["full_kelly"] == 0.0
        assert result["recommended_bet_pct"] == 0.0


class TestWalkForward:
    """Test /ds:validate — walk-forward cross-validation."""

    def test_stable_edge(self):
        """Consistent 75% WR across splits."""
        from polyphemus.tools.trader_stats import walk_forward_cv

        # 100 trades with stable 75% WR pattern
        returns = ([0.25] * 3 + [-0.75]) * 25  # 75% WR repeating
        result = walk_forward_cv(returns, n_splits=5)
        assert result["mean_test_wr"] > 0.60
        assert result["consistent"] is True

    def test_insufficient_data(self):
        """Too few trades for walk-forward."""
        from polyphemus.tools.trader_stats import walk_forward_cv

        result = walk_forward_cv([1.0, -1.0, 1.0], n_splits=5)
        assert result["consistent"] is False
        assert "Insufficient" in result["interpretation"]


class TestACFAnalysis:
    """Test /ds:timeseries — autocorrelation analysis."""

    def test_independent_outcomes(self):
        """Random-looking data should have low ACF at lag 1."""
        from polyphemus.tools.trader_stats import acf_analysis

        # Pseudo-random but deterministic
        import random
        rng = random.Random(42)
        series = [rng.choice([0, 1]) for _ in range(100)]
        result = acf_analysis(series)
        # ACF at lag 1 should be near zero for independent data
        lag1 = result["acf"].get("lag_1", 0)
        assert abs(lag1) < 0.3, f"Expected near-zero ACF, got {lag1}"

    def test_insufficient_data(self):
        from polyphemus.tools.trader_stats import acf_analysis

        result = acf_analysis([1, 0])
        assert "Insufficient" in result["interpretation"]


class TestRegimeStability:
    """Test regime stability check."""

    def test_stable_across_regimes(self):
        from polyphemus.tools.trader_stats import regime_stability_check

        regimes = {
            "calm": {"wr": 0.80, "n": 50},
            "volatile": {"wr": 0.65, "n": 30},
            "crisis": {"wr": 0.55, "n": 20},
        }
        result = regime_stability_check(regimes)
        assert result["stable"] is True
        assert result["positive_regimes"] == 3

    def test_regime_dependent(self):
        from polyphemus.tools.trader_stats import regime_stability_check

        regimes = {
            "calm": {"wr": 0.90, "n": 50},
            "volatile": {"wr": 0.30, "n": 30},
        }
        result = regime_stability_check(regimes)
        assert result["positive_regimes"] == 1


# ============================================================
#  trader_decompose.py — Integration Tests (with temp SQLite)
# ============================================================

@pytest.fixture
def sample_db(tmp_path):
    """Create a sample trades DB with known data for testing."""
    db_path = str(tmp_path / "test_trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Create schema (v2: includes maker_address and size_unit)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trader_trades (
            trade_id TEXT PRIMARY KEY,
            source_trader TEXT NOT NULL,
            analyst_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            slug TEXT NOT NULL,
            asset TEXT,
            side TEXT,
            outcome TEXT,
            price REAL,
            size REAL,
            size_unit TEXT DEFAULT 'unknown',
            market_type TEXT,
            window TEXT,
            maker_address TEXT DEFAULT '',
            resolution TEXT DEFAULT 'PENDING',
            analysis_confidence REAL DEFAULT 0.0,
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS resolution_cache (
            market_slug TEXT NOT NULL,
            epoch_start INTEGER NOT NULL,
            resolved_direction TEXT NOT NULL,
            resolution_source TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (market_slug, epoch_start)
        );
    """)

    trader = "0xTEST1234567890"
    base_ts = 1710000000.0  # Arbitrary timestamp

    # Insert 100 trades: 75 wins, 25 losses
    # Mix of assets, prices, sizes
    trades = []
    for i in range(100):
        is_win = i < 75  # First 75 are wins
        asset = ["BTC", "ETH", "SOL"][i % 3]
        # Price distribution: cluster around 0.48-0.55 (deep entries)
        price = 0.45 + (i % 20) * 0.005
        size = 30 + (i % 5) * 10
        outcome = "Up" if i % 2 == 0 else "Down"
        resolution = outcome if is_win else ("Down" if outcome == "Up" else "Up")

        trades.append((
            f"trade_{i:04d}",
            trader,
            "test_analyst",
            base_ts + i * 300,  # 5 min apart
            f"{asset.lower()}-updown-5m-epoch-{i}",
            asset,
            "BUY",
            outcome,
            round(price, 4),
            size,
            "unknown",  # size_unit
            "updown_5m",
            "5m",
            "",  # maker_address (empty = no ground truth)
            resolution,
            0.95,
            "2026-03-15T00:00:00Z",
        ))

    conn.executemany(
        """INSERT INTO trader_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        trades,
    )
    conn.commit()
    conn.close()

    return db_path, trader


class TestComputeProfile:
    """Test profile computation from resolved trades."""

    def test_basic_profile(self, sample_db):
        from polyphemus.tools.trader_decompose import compute_profile

        db_path, trader = sample_db
        profile = compute_profile(db_path, trader)

        assert profile["total_trades"] == 100
        assert profile["wins"] == 75
        assert profile["losses"] == 25
        assert 0.74 <= profile["wr"] <= 0.76
        assert profile["r8_label"] == "SUBSTANTIAL"
        assert profile["is_provisional"] is False

    def test_wr_wilson_ci(self, sample_db):
        from polyphemus.tools.trader_decompose import compute_profile

        db_path, trader = sample_db
        profile = compute_profile(db_path, trader)
        ci_low, ci_high = profile["wilson_ci"]
        # 75% WR with n=100 should have CI roughly [0.65, 0.83]
        assert ci_low > 0.60
        assert ci_high < 0.90
        assert ci_low < 0.75 < ci_high


class TestEntryPriceDistribution:
    """Test entry price dimension extraction."""

    def test_price_stats(self, sample_db):
        from polyphemus.tools.trader_decompose import extract_entry_price_distribution

        db_path, trader = sample_db
        result = extract_entry_price_distribution(db_path, trader)

        assert result["n"] == 100
        assert result["entry_mode"] == "deep"  # Prices clustered around 0.45-0.55
        assert result["mean"] > 0.40
        assert result["mean"] < 0.60
        assert result["r8_label"] == "SUBSTANTIAL"


class TestTimingPattern:
    """Test entry timing extraction."""

    def test_timing(self, sample_db):
        from polyphemus.tools.trader_decompose import extract_timing_pattern

        db_path, trader = sample_db
        result = extract_timing_pattern(db_path, trader)
        assert result["n"] == 100
        assert "timing_mode" in result


class TestDirectionBias:
    """Test direction bias detection."""

    def test_balanced_direction(self, sample_db):
        from polyphemus.tools.trader_decompose import extract_direction_bias

        db_path, trader = sample_db
        result = extract_direction_bias(db_path, trader)
        # Our test data alternates Up/Down, so should be ~neutral
        assert result["bias"] == "NEUTRAL"


class TestReplicaStrategy:
    """Test ReplicaStrategy generation."""

    def test_deep_entry_replica(self, sample_db):
        from polyphemus.tools.trader_decompose import (
            compute_profile,
            extract_entry_price_distribution,
            extract_timing_pattern,
            extract_asset_preference,
            extract_direction_bias,
            extract_sizing_pattern,
            infer_order_type,
            extract_regime_conditioning,
            infer_exit_pattern,
            generate_replica,
            render_env_template,
        )

        db_path, trader = sample_db
        profile = compute_profile(db_path, trader)

        decomposition = {
            "entry_price": extract_entry_price_distribution(db_path, trader),
            "timing": extract_timing_pattern(db_path, trader),
            "asset_preference": extract_asset_preference(db_path, trader),
            "direction_bias": extract_direction_bias(db_path, trader),
            "sizing": extract_sizing_pattern(db_path, trader),
            "order_type": infer_order_type(db_path, trader),
            "regime": extract_regime_conditioning(db_path, trader),
            "exit_pattern": infer_exit_pattern(db_path, trader),
        }

        replica = generate_replica(decomposition, balance=500.0, profile=profile)

        # Deep entries should classify as rtds or pair_cost_arb
        assert replica.strategy_type in ("rtds", "pair_cost_arb", "mixed")
        assert "MIN_ENTRY_PRICE" in replica.env_overrides or "MM_MAX_PAIR_COST" in replica.env_overrides
        assert "DRY_RUN" in replica.env_overrides
        assert replica.env_overrides["DRY_RUN"] == "true"
        assert replica.confidence > 0
        assert replica.n_trades_analyzed > 0

        # Test env template rendering
        template = render_env_template(replica)
        assert "Reverse-engineered" in template
        assert "DRY_RUN=true" in template
        # No dangling placeholders
        assert "${" not in template
        assert "{{" not in template

    def test_momentum_replica(self):
        """High entry prices should generate momentum config."""
        from polyphemus.tools.trader_decompose import generate_replica

        decomposition = {
            "entry_price": {"entry_mode": "high", "min": 0.80, "max": 0.95, "n": 50},
            "timing": {"timing_mode": "early", "mean_offset_secs": 30},
            "asset_preference": {"asset_counts": {"BTC": 40, "ETH": 10}},
            "direction_bias": {"bias": "NEUTRAL"},
            "sizing": {"mean_size": 50},
            "order_type": {"dominant_type": "maker"},
            "regime": {"worst_hours": [3, 4, 5]},
            "exit_pattern": {"exit_mode": "hold_to_resolution"},
        }

        profile = {"wr": 0.70, "resolved_trades": 50, "wins": 35, "losses": 15,
                    "est_pnl": 100, "source_trader": "0xTEST", "is_provisional": False}

        replica = generate_replica(decomposition, balance=500.0, profile=profile)
        assert replica.strategy_type == "momentum"
        assert "MIN_ENTRY_PRICE" in replica.env_overrides
        assert "ASSET_FILTER" in replica.env_overrides
        assert "BTC" in replica.env_overrides["ASSET_FILTER"]
        assert "BLACKOUT_HOURS" in replica.env_overrides
        assert "MOMENTUM_MAX_EPOCH_ELAPSED_SECS" in replica.env_overrides

    def test_pair_arb_replica(self):
        """Very deep entries near 0.50 should generate pair_cost_arb config."""
        from polyphemus.tools.trader_decompose import generate_replica

        decomposition = {
            "entry_price": {"entry_mode": "deep", "min": 0.45, "max": 0.55, "n": 80},
            "timing": {"timing_mode": "late", "mean_offset_secs": 250},
            "asset_preference": {"asset_counts": {"BTC": 80}},
            "direction_bias": {"bias": "NEUTRAL"},
            "sizing": {"mean_size": 100},
            "order_type": {"dominant_type": "taker"},
            "regime": {},
            "exit_pattern": {"exit_mode": "hold_to_resolution"},
        }

        replica = generate_replica(decomposition, balance=500.0)
        # Deep + HTR should be rtds (since entry_mode="deep" triggers that path)
        assert replica.strategy_type in ("rtds", "pair_cost_arb")


class TestRecommendation:
    """Test REPLICATE / STUDY_MORE / SKIP logic."""

    def test_provisional_forces_study_more(self):
        from polyphemus.tools.trader_decompose import get_recommendation

        profile = {"wr": 0.90, "resolved_trades": 50, "is_provisional": True}
        assert get_recommendation(profile) == "STUDY_MORE"

    def test_anecdotal_forces_study_more(self):
        from polyphemus.tools.trader_decompose import get_recommendation

        profile = {"wr": 0.90, "resolved_trades": 10, "is_provisional": False}
        assert get_recommendation(profile) == "STUDY_MORE"

    def test_strong_edge_replicates(self):
        from polyphemus.tools.trader_decompose import get_recommendation

        profile = {"wr": 0.75, "resolved_trades": 50, "is_provisional": False}
        edge_test = {"dsr_value": 0.8, "consistent": True}
        assert get_recommendation(profile, edge_test) == "REPLICATE"

    def test_low_wr_skips(self):
        from polyphemus.tools.trader_decompose import get_recommendation

        profile = {"wr": 0.45, "resolved_trades": 100, "is_provisional": False}
        assert get_recommendation(profile) == "SKIP"


# ============================================================
#  trader_ingest.py — Schema + Helper Tests
# ============================================================

class TestTradeCountHelpers:
    """Test get_trade_counts and resolve_alias."""

    def test_trade_counts(self, sample_db):
        from polyphemus.tools.trader_ingest import get_trade_counts

        db_path, trader = sample_db
        counts = get_trade_counts(db_path, trader)
        assert counts["total"] == 100
        # All trades in sample_db have resolution set (none PENDING)
        assert counts["resolved"] == 100
        assert counts["pending"] == 0

    def test_resolve_known_alias(self):
        from polyphemus.tools.trader_ingest import resolve_alias

        addr = resolve_alias("tugao9")
        assert addr == "0x970e744a34cd0795ff7b4ba844018f17b7fd5c26"

    def test_resolve_with_at_sign(self):
        from polyphemus.tools.trader_ingest import resolve_alias

        addr = resolve_alias("@tugao9")
        assert addr == "0x970e744a34cd0795ff7b4ba844018f17b7fd5c26"

    def test_resolve_unknown(self):
        from polyphemus.tools.trader_ingest import resolve_alias

        addr = resolve_alias("@unknown_trader_xyz")
        assert addr is None

    def test_get_cached_resolution(self, sample_db):
        from polyphemus.tools.trader_ingest import get_cached_resolution

        db_path, _ = sample_db
        # No entries in resolution_cache in sample_db
        result = get_cached_resolution(db_path, "nonexistent-slug")
        assert result is None


# ============================================================
#  Fix A-3: Maker/Taker Ground Truth Tests
# ============================================================

@pytest.fixture
def sample_db_with_maker(tmp_path):
    """DB with maker_address data for ground-truth maker/taker testing."""
    db_path = str(tmp_path / "test_maker.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trader_trades (
            trade_id TEXT PRIMARY KEY,
            source_trader TEXT NOT NULL,
            analyst_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            slug TEXT NOT NULL,
            asset TEXT,
            side TEXT,
            outcome TEXT,
            price REAL,
            size REAL,
            size_unit TEXT DEFAULT 'unknown',
            market_type TEXT,
            window TEXT,
            maker_address TEXT DEFAULT '',
            resolution TEXT DEFAULT 'PENDING',
            analysis_confidence REAL DEFAULT 0.0,
            ingested_at TEXT NOT NULL
        );
    """)

    trader = "0xAABBCCDD11223344"
    other = "0x9999888877776666"
    base_ts = 1710000000.0

    trades = []
    for i in range(100):
        # 80 trades where trader IS the maker, 20 where they're taker
        if i < 80:
            maker_addr = trader  # trader is maker
        else:
            maker_addr = other   # someone else is maker, trader is taker

        trades.append((
            f"maker_trade_{i:04d}",
            trader,
            "test",
            base_ts + i * 300,
            f"btc-updown-5m-epoch-{i}",
            "BTC",
            "BUY",
            "Up",
            0.50,
            50,
            "shares",       # size_unit from CLOB
            "updown_5m",
            "5m",
            maker_addr,     # ground truth
            "Up",           # resolution
            0.95,
            "2026-03-15T00:00:00Z",
        ))

    conn.executemany(
        "INSERT INTO trader_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        trades,
    )
    conn.commit()
    conn.close()
    return db_path, trader


class TestMakerTakerGroundTruth:
    """Test A-3 fix: infer_order_type uses maker_address ground truth."""

    def test_ground_truth_maker(self, sample_db_with_maker):
        """80/100 trades have trader as maker => 80% maker."""
        from polyphemus.tools.trader_decompose import infer_order_type

        db_path, trader = sample_db_with_maker
        result = infer_order_type(db_path, trader)

        assert result["reliable"] is True
        assert result["ground_truth_pct"] == 1.0  # all 100 have maker_address
        assert result["maker_signals"] == 80
        assert result["taker_signals"] == 20
        assert result["no_data"] == 0
        assert 0.79 <= result["maker_pct"] <= 0.81
        assert result["dominant_type"] == "maker"

    def test_no_maker_address_unreliable(self, sample_db):
        """sample_db has empty maker_address => should be UNRELIABLE."""
        from polyphemus.tools.trader_decompose import infer_order_type

        db_path, trader = sample_db
        result = infer_order_type(db_path, trader)

        assert result["reliable"] is False
        assert result["no_data"] == 100
        assert result["dominant_type"] == "unknown"
        assert "UNRELIABLE" in result["interpretation"]

    def test_mixed_maker_data(self, tmp_path):
        """Half trades have maker_address, half don't => reliable if >= 50%."""
        from polyphemus.tools.trader_decompose import infer_order_type

        db_path = str(tmp_path / "test_mixed.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE trader_trades (
                trade_id TEXT PRIMARY KEY,
                source_trader TEXT NOT NULL,
                analyst_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                slug TEXT NOT NULL,
                asset TEXT, side TEXT, outcome TEXT,
                price REAL, size REAL, size_unit TEXT,
                market_type TEXT, window TEXT,
                maker_address TEXT DEFAULT '',
                resolution TEXT, analysis_confidence REAL,
                ingested_at TEXT NOT NULL
            );
        """)

        trader = "0xMIXEDTRADER0001"
        base_ts = 1710000000.0
        trades = []
        for i in range(100):
            # First 60 have maker_address (50 maker, 10 taker), last 40 empty
            if i < 50:
                maker = trader
            elif i < 60:
                maker = "0xOTHER"
            else:
                maker = ""

            trades.append((
                f"mixed_{i:04d}", trader, "test", base_ts + i * 300,
                f"btc-updown-5m-epoch-{i}", "BTC", "BUY", "Up",
                0.50, 50, "shares", "updown_5m", "5m", maker,
                "Up", 0.95, "2026-03-15T00:00:00Z",
            ))

        conn.executemany(
            "INSERT INTO trader_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            trades,
        )
        conn.commit()
        conn.close()

        result = infer_order_type(db_path, trader)
        assert result["reliable"] is True  # 60/100 >= 50%
        assert result["ground_truth_pct"] == 0.6
        assert result["maker_signals"] == 50
        assert result["taker_signals"] == 10
        assert result["no_data"] == 40
        # maker_pct based on ground truth only: 50/60
        assert abs(result["maker_pct"] - 50 / 60) < 0.01


# ============================================================
#  Fix A-4: Size Semantics Tests
# ============================================================

class TestSizeSemantics:
    """Test A-4 fix: compute_profile tracks size_unit reliability."""

    def test_unknown_size_unit_flagged(self, sample_db):
        """sample_db has size_unit='unknown' => pnl_reliable=False."""
        from polyphemus.tools.trader_decompose import compute_profile

        db_path, trader = sample_db
        profile = compute_profile(db_path, trader)

        assert profile["pnl_reliable"] is False
        assert "APPROXIMATE" in profile["interpretation"]

    def test_shares_size_unit_reliable(self, sample_db_with_maker):
        """sample_db_with_maker has size_unit='shares' => pnl_reliable=True."""
        from polyphemus.tools.trader_decompose import compute_profile

        db_path, trader = sample_db_with_maker
        profile = compute_profile(db_path, trader)

        assert profile["pnl_reliable"] is True
        assert "APPROXIMATE" not in profile["interpretation"]


# ============================================================
#  Fix A-1: Resolution Health Tests
# ============================================================

class TestResolutionHealth:
    """Test resolution_health() function."""

    def test_fully_resolved(self, sample_db):
        """sample_db has all trades resolved."""
        from polyphemus.tools.trader_ingest import resolution_health

        db_path, trader = sample_db
        health = resolution_health(db_path, trader)

        assert health["total_trades"] == 100
        assert health["resolved"] == 100
        assert health["pending"] == 0
        assert health["healthy"] is True  # 100% resolution rate > 50

    def test_pending_trades(self, tmp_path):
        """DB with PENDING trades should report unhealthy."""
        from polyphemus.tools.trader_ingest import resolution_health

        db_path = str(tmp_path / "test_pending.db")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE trader_trades (
                trade_id TEXT PRIMARY KEY,
                source_trader TEXT NOT NULL,
                analyst_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                slug TEXT NOT NULL,
                asset TEXT, side TEXT, outcome TEXT,
                price REAL, size REAL, size_unit TEXT,
                market_type TEXT, window TEXT,
                maker_address TEXT DEFAULT '',
                resolution TEXT DEFAULT 'PENDING',
                analysis_confidence REAL DEFAULT 0.0,
                ingested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resolution_cache (
                market_slug TEXT NOT NULL,
                epoch_start INTEGER NOT NULL,
                resolved_direction TEXT NOT NULL,
                resolution_source TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (market_slug, epoch_start)
            );
        """)

        trader = "0xPENDINGTEST"
        for i in range(10):
            # 3 resolved, 7 pending
            res = "Up" if i < 3 else "PENDING"
            conn.execute(
                "INSERT INTO trader_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"p_{i}", trader, "test", 1710000000.0 + i * 300,
                 f"btc-updown-5m-epoch-{i}", "BTC", "BUY", "Up",
                 0.50, 50, "shares", "updown_5m", "5m", "",
                 res, 0.0, "2026-03-15T00:00:00Z"),
            )
        conn.commit()
        conn.close()

        health = resolution_health(db_path, trader)
        assert health["total_trades"] == 10
        assert health["resolved"] == 3
        assert health["pending"] == 7
        assert health["healthy"] is False  # 30% resolution rate <= 50
