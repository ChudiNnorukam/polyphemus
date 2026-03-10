#!/usr/bin/env python3
"""
Test suite for compare_bots.py

Generates synthetic trade data for both bots and verifies metrics computation.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from compare_bots import Trade, BotMetrics, Analyzer


def create_test_trades_v1():
    """Create synthetic V1 trades for testing."""
    trades = []
    base_time = datetime.now(timezone.utc) - timedelta(hours=24)

    # BTC trades: 12 trades, 75% win rate
    for i in range(12):
        entry_time = base_time + timedelta(minutes=i * 120)
        exit_time = entry_time + timedelta(minutes=15)
        is_win = i < 9  # 9 wins, 3 losses

        trade = Trade(
            bot="V1",
            coin="BTC",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=0.65,
            exit_price=0.95 if is_win else 0.40,
            size_usd=50.0,
            profit_loss=15.0 if is_win else -10.0,
            exit_reason="market_resolved" if is_win else "stop_loss",
            market_slug=f"btc-updown-15m-{int(entry_time.timestamp())}"
        )
        trades.append(trade)

    # ETH trades: 18 trades, 61% win rate
    for i in range(18):
        entry_time = base_time + timedelta(minutes=i * 80 + 1000)
        exit_time = entry_time + timedelta(minutes=15)
        is_win = i < 11  # 11 wins, 7 losses

        trade = Trade(
            bot="V1",
            coin="ETH",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=0.70,
            exit_price=0.92 if is_win else 0.38,
            size_usd=50.0,
            profit_loss=11.0 if is_win else -8.0,
            exit_reason="market_resolved" if is_win else "time_exit",
            market_slug=f"eth-updown-15m-{int(entry_time.timestamp())}"
        )
        trades.append(trade)

    # SOL trades: 15 trades, 53% win rate
    for i in range(15):
        entry_time = base_time + timedelta(minutes=i * 100 + 2500)
        exit_time = entry_time + timedelta(minutes=15)
        is_win = i < 8  # 8 wins, 7 losses

        trade = Trade(
            bot="V1",
            coin="SOL",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=0.60,
            exit_price=0.88 if is_win else 0.35,
            size_usd=50.0,
            profit_loss=14.0 if is_win else -9.0,
            exit_reason="market_resolved" if is_win else "profit_target",
            market_slug=f"sol-updown-15m-{int(entry_time.timestamp())}"
        )
        trades.append(trade)

    return trades


def create_test_trades_v2():
    """Create synthetic V2 trades for testing."""
    trades = []
    base_time = datetime.now(timezone.utc) - timedelta(hours=24)

    # BTC trades: 11 trades, 72% win rate
    for i in range(11):
        entry_time = base_time + timedelta(minutes=i * 130)
        exit_time = entry_time + timedelta(minutes=10)
        is_win = i < 8  # 8 wins, 3 losses

        trade = Trade(
            bot="V2",
            coin="BTC",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=0.70,
            exit_price=0.93 if is_win else 0.42,
            size_usd=45.0,
            profit_loss=10.35 if is_win else -6.75,
            exit_reason="market_resolved" if is_win else "stop_loss",
            market_slug=f"btc-updown-15m-{int(entry_time.timestamp())}"
        )
        trades.append(trade)

    # ETH trades: 14 trades, 64% win rate
    for i in range(14):
        entry_time = base_time + timedelta(minutes=i * 90 + 1200)
        exit_time = entry_time + timedelta(minutes=10)
        is_win = i < 9  # 9 wins, 5 losses

        trade = Trade(
            bot="V2",
            coin="ETH",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=0.72,
            exit_price=0.91 if is_win else 0.40,
            size_usd=45.0,
            profit_loss=8.55 if is_win else -7.2,
            exit_reason="market_resolved" if is_win else "time_exit",
            market_slug=f"eth-updown-15m-{int(entry_time.timestamp())}"
        )
        trades.append(trade)

    # SOL trades: 13 trades, 46% win rate
    for i in range(13):
        entry_time = base_time + timedelta(minutes=i * 110 + 2700)
        exit_time = entry_time + timedelta(minutes=10)
        is_win = i < 6  # 6 wins, 7 losses

        trade = Trade(
            bot="V2",
            coin="SOL",
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=0.62,
            exit_price=0.86 if is_win else 0.38,
            size_usd=45.0,
            profit_loss=10.8 if is_win else -5.4,
            exit_reason="market_resolved" if is_win else "profit_target",
            market_slug=f"sol-updown-15m-{int(entry_time.timestamp())}"
        )
        trades.append(trade)

    return trades


def test_metrics():
    """Test metrics computation."""
    print("=" * 70)
    print("TEST: Metrics Computation")
    print("=" * 70)

    v1_trades = create_test_trades_v1()
    v1_metrics = BotMetrics(v1_trades)

    print("\nV1 Metrics:")
    print(f"  Total Trades: {v1_metrics.total_trades()}")
    print(f"  Win Rate: {v1_metrics.win_rate():.1f}%")
    print(f"  Total P&L: ${v1_metrics.total_pnl():.2f}")
    print(f"  ROI: {v1_metrics.roi():.1f}%")
    print(f"  Avg Win: ${v1_metrics.avg_win():.2f}")
    print(f"  Avg Loss: ${v1_metrics.avg_loss():.2f}")
    print(f"  Profit Factor: {v1_metrics.profit_factor():.2f}")
    print(f"  Max Drawdown: ${v1_metrics.max_drawdown():.2f}")
    print(f"  Max Consecutive Losses: {v1_metrics.max_consecutive_losses()}")
    print(f"  Max Consecutive Wins: {v1_metrics.max_consecutive_wins()}")
    print(f"  Sharpe Ratio: {v1_metrics.sharpe_ratio():.2f}")

    v2_trades = create_test_trades_v2()
    v2_metrics = BotMetrics(v2_trades)

    print("\nV2 Metrics:")
    print(f"  Total Trades: {v2_metrics.total_trades()}")
    print(f"  Win Rate: {v2_metrics.win_rate():.1f}%")
    print(f"  Total P&L: ${v2_metrics.total_pnl():.2f}")
    print(f"  ROI: {v2_metrics.roi():.1f}%")
    print(f"  Avg Win: ${v2_metrics.avg_win():.2f}")
    print(f"  Avg Loss: ${v2_metrics.avg_loss():.2f}")
    print(f"  Profit Factor: {v2_metrics.profit_factor():.2f}")
    print(f"  Max Drawdown: ${v2_metrics.max_drawdown():.2f}")
    print(f"  Max Consecutive Losses: {v2_metrics.max_consecutive_losses()}")
    print(f"  Max Consecutive Wins: {v2_metrics.max_consecutive_wins()}")
    print(f"  Sharpe Ratio: {v2_metrics.sharpe_ratio():.2f}")

    # Per-coin breakdown
    print("\nV1 Per-Coin Stats:")
    for coin, stats in sorted(v1_metrics.per_coin_stats().items()):
        print(f"  {coin}: {stats['trades']} trades, {stats['win_rate']:.0f}% WR, "
              f"${stats['pnl']:.2f} P&L")

    print("\nV2 Per-Coin Stats:")
    for coin, stats in sorted(v2_metrics.per_coin_stats().items()):
        print(f"  {coin}: {stats['trades']} trades, {stats['win_rate']:.0f}% WR, "
              f"${stats['pnl']:.2f} P&L")

    assert v1_metrics.total_trades() == 45, "V1 should have 45 trades"
    assert v2_metrics.total_trades() == 38, "V2 should have 38 trades"
    assert 60 < v1_metrics.win_rate() < 65, "V1 win rate should be around 62%"
    assert 58 < v2_metrics.win_rate() < 63, "V2 win rate should be around 60%"

    print("\n✓ Metrics test passed")


def test_analyzer():
    """Test comparison analyzer."""
    print("\n" + "=" * 70)
    print("TEST: Analyzer & Comparison")
    print("=" * 70)

    v1_trades = create_test_trades_v1()
    v2_trades = create_test_trades_v2()
    analyzer = Analyzer(v1_trades, v2_trades)

    overlapping = analyzer.overlapping_markets()
    print(f"\nOverlapping Markets: {len(overlapping)}")

    corr = analyzer.pnl_correlation()
    print(f"PnL Correlation: {corr:.2f}")

    div = analyzer.diversification_benefit()
    print(f"Diversification Benefit: {div:.1f}%")

    rec, v1_pct, v2_pct, conf = analyzer.recommend_allocation()
    print(f"\nAllocation Recommendation: {rec}")
    print(f"Confidence: {conf}")

    assert len(overlapping) > 0, "Should have overlapping markets"
    assert -1 <= corr <= 1, "Correlation should be between -1 and 1"
    assert 0 <= div <= 100, "Diversification benefit should be 0-100%"
    assert conf in ["LOW", "MEDIUM", "HIGH"], "Confidence should be valid"

    print("\n✓ Analyzer test passed")


def test_trade_normalization():
    """Test Trade object creation and normalization."""
    print("\n" + "=" * 70)
    print("TEST: Trade Normalization")
    print("=" * 70)

    now = datetime.now(timezone.utc)
    entry_time = now - timedelta(hours=1)
    exit_time = now

    trade = Trade(
        bot="V1",
        coin="BTC",
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=0.65,
        exit_price=0.95,
        size_usd=100.0,
        profit_loss=30.0,
        exit_reason="market_resolved",
        market_slug="btc-updown-15m-123456"
    )

    print(f"\nTrade Object:")
    print(f"  Bot: {trade.bot}")
    print(f"  Coin: {trade.coin}")
    print(f"  Entry Time: {trade.entry_time}")
    print(f"  Exit Time: {trade.exit_time}")
    print(f"  Entry Price: {trade.entry_price}")
    print(f"  Exit Price: {trade.exit_price}")
    print(f"  Size USD: {trade.size_usd}")
    print(f"  Profit/Loss: ${trade.profit_loss}")
    print(f"  Hold Seconds: {trade.hold_seconds}")
    print(f"  Exit Reason: {trade.exit_reason}")
    print(f"  Market Slug: {trade.market_slug}")

    assert trade.bot == "V1", "Bot should be V1"
    assert trade.coin == "BTC", "Coin should be BTC"
    assert trade.profit_loss == 30.0, "P&L should be 30"
    assert trade.hold_seconds == 3600, "Hold time should be 1 hour = 3600 seconds"

    print("\n✓ Trade normalization test passed")


def main():
    """Run all tests."""
    try:
        test_trade_normalization()
        test_metrics()
        test_analyzer()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✓")
        print("=" * 70)
        return 0

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
