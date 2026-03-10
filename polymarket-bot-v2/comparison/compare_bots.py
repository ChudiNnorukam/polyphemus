#!/usr/bin/env python3
"""
Polymarket Bot V1 vs V2 Comparison Framework

Extracts trade data from both bots, analyzes performance, and generates
a detailed comparison report with allocation recommendations.

Usage:
    python3 compare_bots.py [--hours N]
"""

import sqlite3
import json
import sys
import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from pathlib import Path
import statistics
import math


class Trade:
    """Normalized trade object for both V1 and V2."""

    def __init__(self, bot, coin, entry_time, exit_time, entry_price,
                 exit_price, size_usd, profit_loss, exit_reason, market_slug):
        self.bot = bot
        self.coin = coin
        self.entry_time = entry_time
        self.exit_time = exit_time
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.size_usd = size_usd
        self.profit_loss = profit_loss
        self.exit_reason = exit_reason
        self.market_slug = market_slug
        self.hold_seconds = (exit_time - entry_time).total_seconds() if exit_time else 0


class BotMetrics:
    """Compute performance metrics for a bot's trade set."""

    def __init__(self, trades, starting_capital=446.0):
        self.trades = trades
        self.starting_capital = starting_capital
        self.coin_breakdown = defaultdict(list)

        for trade in trades:
            self.coin_breakdown[trade.coin].append(trade)

    def total_trades(self):
        return len(self.trades)

    def win_count(self):
        return sum(1 for t in self.trades if t.profit_loss > 0)

    def loss_count(self):
        return sum(1 for t in self.trades if t.profit_loss < 0)

    def break_even_count(self):
        return sum(1 for t in self.trades if t.profit_loss == 0)

    def win_rate(self):
        if not self.trades:
            return 0.0
        return 100.0 * self.win_count() / len(self.trades)

    def total_pnl(self):
        return sum(t.profit_loss for t in self.trades)

    def roi(self):
        if self.starting_capital == 0:
            return 0.0
        return 100.0 * self.total_pnl() / self.starting_capital

    def avg_win(self):
        wins = [t.profit_loss for t in self.trades if t.profit_loss > 0]
        return sum(wins) / len(wins) if wins else 0.0

    def avg_loss(self):
        losses = [t.profit_loss for t in self.trades if t.profit_loss < 0]
        return sum(losses) / len(losses) if losses else 0.0

    def profit_factor(self):
        total_wins = sum(t.profit_loss for t in self.trades if t.profit_loss > 0)
        total_losses = abs(sum(t.profit_loss for t in self.trades if t.profit_loss < 0))
        if total_losses == 0:
            return float('inf') if total_wins > 0 else 0.0
        return total_wins / total_losses

    def max_drawdown(self):
        """Sequential max drawdown from cumulative PnL."""
        if not self.trades:
            return 0.0

        sorted_trades = sorted(self.trades, key=lambda t: t.entry_time)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in sorted_trades:
            cumulative += trade.profit_loss
            if cumulative > peak:
                peak = cumulative
            drawdown = peak - cumulative
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    def max_consecutive_losses(self):
        """Max consecutive losing trades."""
        if not self.trades:
            return 0

        sorted_trades = sorted(self.trades, key=lambda t: t.entry_time)
        current_streak = 0
        max_streak = 0

        for trade in sorted_trades:
            if trade.profit_loss < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def max_consecutive_wins(self):
        """Max consecutive winning trades."""
        if not self.trades:
            return 0

        sorted_trades = sorted(self.trades, key=lambda t: t.entry_time)
        current_streak = 0
        max_streak = 0

        for trade in sorted_trades:
            if trade.profit_loss > 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def sharpe_ratio(self):
        """Annualized Sharpe ratio from 15-min market returns."""
        if len(self.trades) < 2:
            return 0.0

        sorted_trades = sorted(self.trades, key=lambda t: t.entry_time)
        returns = []

        for trade in sorted_trades:
            if trade.size_usd > 0:
                ret = trade.profit_loss / trade.size_usd
                returns.append(ret)

        if not returns or len(returns) < 2:
            return 0.0

        mean_ret = statistics.mean(returns)
        std_ret = statistics.stdev(returns)

        if std_ret == 0:
            return 0.0

        # Annualize: 15-min markets ≈ 96 per day, 365 days = 35,040 periods/year
        daily_periods = 96
        annual_periods = daily_periods * 365
        sharpe = (mean_ret * annual_periods) / (std_ret * math.sqrt(annual_periods))
        return sharpe

    def per_coin_stats(self):
        """Per-coin breakdown."""
        stats = {}
        for coin, trades_list in self.coin_breakdown.items():
            win_cnt = sum(1 for t in trades_list if t.profit_loss > 0)
            total_cnt = len(trades_list)
            win_pct = 100.0 * win_cnt / total_cnt if total_cnt > 0 else 0.0
            pnl = sum(t.profit_loss for t in trades_list)
            stats[coin] = {
                'trades': total_cnt,
                'win_rate': win_pct,
                'pnl': pnl
            }
        return stats


class V1Extractor:
    """Extract V1 trades from SQLite database."""

    def __init__(self, db_path):
        self.db_path = db_path

    def extract_trades(self, hours=24):
        """Extract trades from the past N hours."""
        trades = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        cutoff_unix = cutoff_time.timestamp()

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT trade_id, token_id, entry_time, entry_price, entry_size,
                       side, entry_amount, exit_time, exit_price, exit_amount,
                       exit_reason, profit_loss, slug, outcome, metadata
                FROM trades
                WHERE exit_time IS NOT NULL
                ORDER BY entry_time DESC
            """)

            for row in cursor.fetchall():
                entry_unix = row['entry_time']
                exit_unix = row['exit_time']

                if exit_unix < cutoff_unix:
                    continue

                # Extract coin from slug (e.g., "btc-updown-15m-xxx" -> "BTC")
                slug = row['slug'] or ""
                coin = slug.split('-')[0].upper() if slug else "UNKNOWN"

                entry_dt = datetime.fromtimestamp(entry_unix, tz=timezone.utc)
                exit_dt = datetime.fromtimestamp(exit_unix, tz=timezone.utc)

                trade = Trade(
                    bot="V1",
                    coin=coin,
                    entry_time=entry_dt,
                    exit_time=exit_dt,
                    entry_price=row['entry_price'] or 0.0,
                    exit_price=row['exit_price'] or 0.0,
                    size_usd=row['entry_amount'] or 0.0,
                    profit_loss=row['profit_loss'] or 0.0,
                    exit_reason=row['exit_reason'] or "unknown",
                    market_slug=slug
                )
                trades.append(trade)

            conn.close()
            return trades

        except Exception as e:
            print(f"[ERROR] V1 extraction failed: {e}", file=sys.stderr)
            return []


class V2Extractor:
    """Extract V2 trades from JSONL logs (4coinsbot format)."""

    def __init__(self, logs_dir):
        self.logs_dir = Path(logs_dir)

    def _find_trade_files(self):
        """Find all trades.jsonl files across per-coin subdirs."""
        files = list(self.logs_dir.glob("*/trades.jsonl"))
        if not files:
            # Fallback: single trades.jsonl in logs dir
            single = self.logs_dir / "trades.jsonl"
            if single.exists():
                files = [single]
        return files

    def extract_trades(self, hours=24):
        """Extract trades from the past N hours."""
        trades = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        trade_files = self._find_trade_files()
        if not trade_files:
            print(f"[!] No V2 trade files found in {self.logs_dir}", file=sys.stderr)
            return []

        for fpath in trade_files:
            try:
                with open(fpath, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # 4coinsbot format: close_time (unix), duration (secs),
                        # close_timestamp (str), total_cost, pnl, exit_price, exit_reason
                        close_unix = record.get('close_time')
                        duration = record.get('duration', 0)
                        close_str = record.get('close_timestamp')

                        if not close_unix and not close_str:
                            continue

                        try:
                            if close_unix:
                                exit_dt = datetime.fromtimestamp(float(close_unix), tz=timezone.utc)
                            else:
                                exit_dt = self._parse_datetime(close_str)

                            entry_dt = exit_dt - timedelta(seconds=float(duration))
                        except Exception:
                            continue

                        if exit_dt < cutoff_time:
                            continue

                        slug = record.get('market_slug') or record.get('slug') or ""
                        coin = slug.split('-')[0].upper() if slug else "UNKNOWN"

                        trade = Trade(
                            bot="V2",
                            coin=coin,
                            entry_time=entry_dt,
                            exit_time=exit_dt,
                            entry_price=float(record.get('exit_price', 0.0)),
                            exit_price=float(record.get('exit_price', 0.0)),
                            size_usd=float(record.get('total_cost', 0.0)),
                            profit_loss=float(record.get('pnl', 0.0)),
                            exit_reason=record.get('exit_reason', 'unknown'),
                            market_slug=slug
                        )
                        trades.append(trade)

            except Exception as e:
                print(f"[ERROR] V2 extraction from {fpath}: {e}", file=sys.stderr)

        return trades

    @staticmethod
    def _parse_datetime(dt_str):
        """Parse datetime from various formats."""
        if not dt_str:
            raise ValueError("Empty datetime string")

        # Try ISO format first
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except Exception:
            pass

        # Try unix timestamp
        try:
            return datetime.fromtimestamp(float(dt_str), tz=timezone.utc)
        except Exception:
            pass

        raise ValueError(f"Cannot parse datetime: {dt_str}")


class Analyzer:
    """Analyze and compare bots."""

    def __init__(self, v1_trades, v2_trades):
        self.v1_trades = v1_trades
        self.v2_trades = v2_trades
        self.v1_metrics = BotMetrics(v1_trades)
        self.v2_metrics = BotMetrics(v2_trades)

    def overlapping_markets(self):
        """Find markets traded by both bots."""
        v1_slugs = set(t.market_slug for t in self.v1_trades)
        v2_slugs = set(t.market_slug for t in self.v2_trades)
        return v1_slugs & v2_slugs

    def pnl_correlation(self):
        """Compute PnL correlation for overlapping markets."""
        overlapping = self.overlapping_markets()
        if not overlapping:
            return 0.0

        v1_pnls = {}
        v2_pnls = {}

        for trade in self.v1_trades:
            if trade.market_slug in overlapping:
                if trade.market_slug not in v1_pnls:
                    v1_pnls[trade.market_slug] = 0.0
                v1_pnls[trade.market_slug] += trade.profit_loss

        for trade in self.v2_trades:
            if trade.market_slug in overlapping:
                if trade.market_slug not in v2_pnls:
                    v2_pnls[trade.market_slug] = 0.0
                v2_pnls[trade.market_slug] += trade.profit_loss

        common_slugs = set(v1_pnls.keys()) & set(v2_pnls.keys())
        if len(common_slugs) < 2:
            return 0.0

        v1_values = [v1_pnls[s] for s in common_slugs]
        v2_values = [v2_pnls[s] for s in common_slugs]

        mean_v1 = statistics.mean(v1_values)
        mean_v2 = statistics.mean(v2_values)
        std_v1 = statistics.stdev(v1_values) if len(v1_values) > 1 else 1.0
        std_v2 = statistics.stdev(v2_values) if len(v2_values) > 1 else 1.0

        if std_v1 == 0 or std_v2 == 0:
            return 0.0

        covariance = sum((v1_values[i] - mean_v1) * (v2_values[i] - mean_v2)
                        for i in range(len(v1_values))) / len(v1_values)
        correlation = covariance / (std_v1 * std_v2)
        return correlation

    def diversification_benefit(self):
        """Estimate diversification benefit from portfolio combination."""
        if not self.v1_trades or not self.v2_trades:
            return 0.0

        v1_pnl = self.v1_metrics.total_pnl()
        v2_pnl = self.v2_metrics.total_pnl()
        combined_pnl = v1_pnl + v2_pnl

        v1_vol = self._estimate_volatility(self.v1_trades)
        v2_vol = self._estimate_volatility(self.v2_trades)

        if v1_vol == 0 or v2_vol == 0:
            return 0.0

        corr = self.pnl_correlation()

        # Portfolio volatility with 50/50 split
        portfolio_vol = math.sqrt(
            0.25 * v1_vol ** 2 + 0.25 * v2_vol ** 2 + 2 * 0.5 * 0.5 * v1_vol * v2_vol * corr
        )

        avg_vol = 0.5 * v1_vol + 0.5 * v2_vol

        if avg_vol == 0:
            return 0.0

        return 100.0 * (1.0 - portfolio_vol / avg_vol)

    @staticmethod
    def _estimate_volatility(trades):
        """Estimate volatility from trade returns."""
        if not trades or len(trades) < 2:
            return 0.0

        returns = []
        for trade in trades:
            if trade.size_usd > 0:
                ret = trade.profit_loss / trade.size_usd
                returns.append(ret)

        if len(returns) < 2:
            return 0.0

        return statistics.stdev(returns)

    def recommend_allocation(self):
        """Recommend bot allocation based on weighted metrics."""
        v1_trades = self.v1_metrics.total_trades()
        v2_trades = self.v2_metrics.total_trades()
        total_trades = v1_trades + v2_trades

        # Check sample size — need minimum per bot for valid comparison
        if total_trades < 30 or min(v1_trades, v2_trades) < 10:
            return ("INSUFFICIENT DATA", 0.0, 0.0, "LOW")

        # Score each bot
        v1_score = self._compute_score(self.v1_metrics)
        v2_score = self._compute_score(self.v2_metrics)

        total_score = v1_score + v2_score
        if total_score == 0:
            return ("NO VALID DATA", 0.0, 0.0, "LOW")

        v1_pct = 100.0 * v1_score / total_score
        v2_pct = 100.0 * v2_score / total_score

        confidence = self._compute_confidence(v1_trades, v2_trades, v1_score, v2_score)

        recommendation = f"V1: {v1_pct:.0f}% / V2: {v2_pct:.0f}%"
        return (recommendation, v1_pct, v2_pct, confidence)

    @staticmethod
    def _compute_score(metrics):
        """Compute composite score for a bot."""
        if metrics.total_trades() == 0:
            return 0.0

        score = 0.0

        # Win rate: 0-30 points (30% min WR = 0)
        wr = metrics.win_rate()
        if wr >= 30:
            score += min(30.0, (wr - 30.0) / 2.0)

        # Profit factor: 0-25 points (>2.0 = max)
        pf = metrics.profit_factor()
        if pf > 0 and pf != float('inf'):
            score += min(25.0, pf * 10.0)

        # ROI: 0-25 points (10% ROI = max)
        roi = metrics.roi()
        if roi > 0:
            score += min(25.0, roi)

        # Sharpe: 0-20 points (1.0+ = 20, negative = 0)
        sharpe = metrics.sharpe_ratio()
        if sharpe > 0:
            score += min(20.0, sharpe * 20.0)

        return score

    @staticmethod
    def _compute_confidence(v1_trades, v2_trades, v1_score, v2_score):
        """Determine confidence level."""
        total_trades = v1_trades + v2_trades
        min_trades = min(v1_trades, v2_trades)
        score_diff = abs(v1_score - v2_score)

        if total_trades < 50:
            return "LOW"
        if min_trades < 15:
            return "LOW"
        if score_diff < 5:
            return "MEDIUM"
        return "HIGH"


def format_report(v1_metrics, v2_metrics, analyzer, hours, report_time):
    """Format the comparison report."""
    lines = []
    lines.append("=" * 66)
    lines.append("  POLYMARKET BOT COMPARISON REPORT")
    lines.append(f"  Window: {hours} hours")
    lines.append(f"  Generated: {report_time}")
    lines.append("=" * 66)
    lines.append("")

    # V1 Section
    lines.append("--- V1: Signal Following Bot ---")
    v1_trades = v1_metrics.total_trades()
    v1_wr = v1_metrics.win_rate()
    v1_pnl = v1_metrics.total_pnl()
    v1_roi = v1_metrics.roi()
    lines.append(f"  Trades: {v1_trades} | Win Rate: {v1_wr:.1f}% | "
                f"P&L: ${v1_pnl:.2f} | ROI: {v1_roi:.1f}%")
    lines.append(f"  Avg Win: ${v1_metrics.avg_win():.2f} | "
                f"Avg Loss: ${v1_metrics.avg_loss():.2f} | "
                f"Profit Factor: {v1_metrics.profit_factor():.2f}")
    lines.append(f"  Max Drawdown: ${v1_metrics.max_drawdown():.2f} | "
                f"Max Consecutive Losses: {v1_metrics.max_consecutive_losses()}")
    lines.append(f"  Sharpe Ratio: {v1_metrics.sharpe_ratio():.2f}")

    v1_per_coin = v1_metrics.per_coin_stats()
    coin_strs = []
    for coin in sorted(v1_per_coin.keys()):
        stats = v1_per_coin[coin]
        coin_strs.append(f"{coin}({stats['trades']}t, {stats['win_rate']:.0f}% WR, "
                        f"${stats['pnl']:.0f})")
    if coin_strs:
        lines.append(f"  Per Coin: {' | '.join(coin_strs)}")
    lines.append("")

    # V2 Section
    lines.append("--- V2: Late-Entry 4coinsbot ---")
    v2_trades = v2_metrics.total_trades()
    v2_wr = v2_metrics.win_rate()
    v2_pnl = v2_metrics.total_pnl()
    v2_roi = v2_metrics.roi()
    lines.append(f"  Trades: {v2_trades} | Win Rate: {v2_wr:.1f}% | "
                f"P&L: ${v2_pnl:.2f} | ROI: {v2_roi:.1f}%")
    lines.append(f"  Avg Win: ${v2_metrics.avg_win():.2f} | "
                f"Avg Loss: ${v2_metrics.avg_loss():.2f} | "
                f"Profit Factor: {v2_metrics.profit_factor():.2f}")
    lines.append(f"  Max Drawdown: ${v2_metrics.max_drawdown():.2f} | "
                f"Max Consecutive Losses: {v2_metrics.max_consecutive_losses()}")
    lines.append(f"  Sharpe Ratio: {v2_metrics.sharpe_ratio():.2f}")

    v2_per_coin = v2_metrics.per_coin_stats()
    coin_strs = []
    for coin in sorted(v2_per_coin.keys()):
        stats = v2_per_coin[coin]
        coin_strs.append(f"{coin}({stats['trades']}t, {stats['win_rate']:.0f}% WR, "
                        f"${stats['pnl']:.0f})")
    if coin_strs:
        lines.append(f"  Per Coin: {' | '.join(coin_strs)}")
    lines.append("")

    # Head-to-Head
    lines.append("--- Head-to-Head ---")
    overlapping = analyzer.overlapping_markets()
    overlap_pct = 0.0
    if v1_trades + v2_trades > 0:
        overlap_pct = 100.0 * len(overlapping) / (v1_trades + v2_trades)
    lines.append(f"  Overlapping Markets: {len(overlapping)} "
                f"({overlap_pct:.1f}% of total trades)")

    corr = analyzer.pnl_correlation()
    lines.append(f"  PnL Correlation: {corr:.2f}")

    div = analyzer.diversification_benefit()
    lines.append(f"  Diversification Benefit: {div:.1f}%")
    lines.append("")

    # Recommendation
    lines.append("--- Recommendation ---")
    rec, v1_pct, v2_pct, confidence = analyzer.recommend_allocation()
    lines.append(f"  {rec}")
    lines.append(f"  Confidence: {confidence}")
    lines.append("")

    lines.append("=" * 66)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Compare Polymarket Bot V1 and V2 performance"
    )
    parser.add_argument("--hours", type=int, default=24,
                       help="Comparison window in hours (default: 24)")
    args = parser.parse_args()

    # Data source paths
    v1_db = "/opt/polymarket-bot/data/performance.db"
    v2_logs_dir = "/opt/polymarket-bot-v2/4coinsbot/logs"
    report_path = "/opt/polymarket-bot-v2/comparison/COMPARISON_REPORT.txt"

    # Extract trades
    print(f"[*] Extracting V1 trades from {v1_db}...", file=sys.stderr)
    v1_extractor = V1Extractor(v1_db)
    v1_trades = v1_extractor.extract_trades(args.hours)
    print(f"[+] V1: {len(v1_trades)} trades extracted", file=sys.stderr)

    print(f"[*] Extracting V2 trades from {v2_logs_dir}...", file=sys.stderr)
    v2_extractor = V2Extractor(v2_logs_dir)
    v2_trades = v2_extractor.extract_trades(args.hours)
    print(f"[+] V2: {len(v2_trades)} trades extracted", file=sys.stderr)

    # Analyze
    print("[*] Computing metrics...", file=sys.stderr)
    analyzer = Analyzer(v1_trades, v2_trades)

    # Generate report
    report_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    report = format_report(
        analyzer.v1_metrics,
        analyzer.v2_metrics,
        analyzer,
        args.hours,
        report_time
    )

    # Output
    print(report)
    print("", file=sys.stderr)

    # Save report
    try:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, 'w') as f:
            f.write(report)
        print(f"[+] Report saved to {report_path}", file=sys.stderr)
    except Exception as e:
        print(f"[!] Failed to save report: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
