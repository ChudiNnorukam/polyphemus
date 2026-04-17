"""Monte Carlo ruin analysis for weather SELL strategy.

Simulates thousands of trading paths at various win rates to answer:
1. What's the probability of ruin (losing X% of bankroll) at each WR?
2. What's the expected drawdown trajectory?
3. How many trades until we can statistically distinguish edge from noise?

Parameters are locked to the Think Tank gate (2026-04-13):
- Direction: SELL only, cumulative markets
- Price range: $0.10-$0.50 execution price
- Position size: $20 per trade (flat)
- Fee: 0.05 * p * (1-p) per share
- Bankroll: $500

Usage:
    python -m polyphemus.prediction_markets.weather.ruin_analysis
    python -m polyphemus.prediction_markets.weather.ruin_analysis --bankroll 500 --trades 200
"""
import argparse
import random
from dataclasses import dataclass


@dataclass
class TradeParams:
    """Parameters for a single SELL trade."""
    entry_price: float   # YES price we're selling against (buying NO)
    stake: float         # dollars risked per trade
    fee_coeff: float = 0.05

    @property
    def no_price(self) -> float:
        return 1.0 - self.entry_price

    @property
    def shares(self) -> float:
        return self.stake / self.no_price if self.no_price > 0 else 0

    @property
    def fee(self) -> float:
        return self.fee_coeff * self.no_price * (1.0 - self.no_price) * self.shares

    def win_pnl(self) -> float:
        """P&L when outcome is NO (we win)."""
        return self.shares * self.entry_price - self.fee

    def loss_pnl(self) -> float:
        """P&L when outcome is YES (we lose)."""
        return -self.stake


def simulate_path(
    win_rate: float,
    bankroll: float,
    n_trades: int,
    trade_params: TradeParams,
    ruin_threshold: float = 0.5,
) -> dict:
    """Simulate one trading path.

    Args:
        win_rate: Probability of winning each trade.
        bankroll: Starting capital.
        n_trades: Number of trades to simulate.
        trade_params: Trade parameters (price, stake, fees).
        ruin_threshold: Fraction of bankroll loss that counts as ruin.

    Returns:
        Dict with final_bankroll, max_drawdown, ruined, trades_to_ruin, peak.
    """
    balance = bankroll
    peak = bankroll
    max_drawdown = 0.0
    ruined = False
    trades_to_ruin = None

    for i in range(n_trades):
        if balance < trade_params.stake:
            ruined = True
            trades_to_ruin = i
            break

        if random.random() < win_rate:
            balance += trade_params.win_pnl()
        else:
            balance += trade_params.loss_pnl()

        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

        if balance <= bankroll * (1 - ruin_threshold):
            ruined = True
            trades_to_ruin = i + 1
            break

    return {
        "final_bankroll": round(balance, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 1),
        "ruined": ruined,
        "trades_to_ruin": trades_to_ruin,
        "peak": round(peak, 2),
        "pnl": round(balance - bankroll, 2),
    }


def run_monte_carlo(
    win_rate: float,
    bankroll: float = 500,
    n_trades: int = 200,
    n_sims: int = 10_000,
    stake: float = 20,
    avg_price: float = 0.28,
    ruin_threshold: float = 0.5,
) -> dict:
    """Run Monte Carlo simulation at a given win rate.

    Returns summary statistics across all simulated paths.
    """
    params = TradeParams(entry_price=avg_price, stake=stake)
    results = []

    for _ in range(n_sims):
        r = simulate_path(win_rate, bankroll, n_trades, params, ruin_threshold)
        results.append(r)

    ruin_count = sum(1 for r in results if r["ruined"])
    final_bankrolls = [r["final_bankroll"] for r in results]
    max_drawdowns = [r["max_drawdown_pct"] for r in results]
    pnls = [r["pnl"] for r in results]

    final_bankrolls.sort()
    pnls.sort()

    return {
        "win_rate": win_rate,
        "n_sims": n_sims,
        "n_trades": n_trades,
        "ruin_pct": round(100 * ruin_count / n_sims, 1),
        "avg_pnl": round(sum(pnls) / n_sims, 2),
        "median_pnl": round(pnls[n_sims // 2], 2),
        "p5_pnl": round(pnls[int(n_sims * 0.05)], 2),
        "p95_pnl": round(pnls[int(n_sims * 0.95)], 2),
        "avg_max_dd": round(sum(max_drawdowns) / n_sims, 1),
        "p95_max_dd": round(sorted(max_drawdowns)[int(n_sims * 0.95)], 1),
        "avg_final": round(sum(final_bankrolls) / n_sims, 2),
        "median_final": round(final_bankrolls[n_sims // 2], 2),
        "p5_final": round(final_bankrolls[int(n_sims * 0.05)], 2),
        "win_pnl_per_trade": round(params.win_pnl(), 4),
        "loss_pnl_per_trade": round(params.loss_pnl(), 4),
    }


def print_report(
    bankroll: float = 500,
    n_trades: int = 200,
    n_sims: int = 10_000,
    stake: float = 20,
    avg_price: float = 0.28,
) -> None:
    """Print comprehensive ruin analysis report."""
    params = TradeParams(entry_price=avg_price, stake=stake)

    print("=" * 75)
    print("MONTE CARLO RUIN ANALYSIS - Weather SELL Strategy")
    print("=" * 75)
    print(f"\nParameters:")
    print(f"  Bankroll:        ${bankroll}")
    print(f"  Position size:   ${stake}")
    print(f"  Avg entry price: ${avg_price} (YES price)")
    print(f"  NO price:        ${params.no_price:.2f}")
    print(f"  Win P&L:         ${params.win_pnl():+.4f} per trade")
    print(f"  Loss P&L:        ${params.loss_pnl():+.4f} per trade")
    print(f"  Fee per trade:   ${params.fee:.4f}")
    print(f"  Trades:          {n_trades}")
    print(f"  Simulations:     {n_sims:,}")
    print(f"  Ruin threshold:  50% of bankroll (${bankroll * 0.5:.0f})")

    # Break-even WR
    # E[trade] = WR * win_pnl + (1-WR) * loss_pnl = 0
    # WR = -loss_pnl / (win_pnl - loss_pnl)
    be_wr = -params.loss_pnl() / (params.win_pnl() - params.loss_pnl())
    print(f"\n  Break-even WR:   {be_wr * 100:.1f}%")

    # Kelly optimal fraction
    # f* = (p * b - q) / b where b = win_pnl / stake, p = WR, q = 1-WR
    # For various WRs, show Kelly sizing
    print(f"\n  Kelly optimal stake at various WRs:")
    for wr in [0.70, 0.75, 0.80, 0.85, 0.90]:
        b = params.win_pnl() / stake
        f_star = (wr * b - (1 - wr)) / b
        kelly_stake = max(0, f_star * bankroll)
        print(f"    WR={wr*100:.0f}%: f*={f_star:.3f}, optimal=${kelly_stake:.0f}, half-Kelly=${kelly_stake/2:.0f}")

    # Run at multiple win rates
    win_rates = [0.60, 0.65, 0.70, 0.725, 0.75, 0.80, 0.85, 0.90, 0.917]

    print(f"\n{'WR':>6s} {'Ruin%':>6s} {'AvgP&L':>8s} {'MedP&L':>8s} "
          f"{'5th%':>8s} {'95th%':>8s} {'AvgDD%':>7s} {'95DD%':>6s}")
    print("-" * 75)

    for wr in win_rates:
        result = run_monte_carlo(
            win_rate=wr,
            bankroll=bankroll,
            n_trades=n_trades,
            n_sims=n_sims,
            stake=stake,
            avg_price=avg_price,
        )
        label = ""
        if abs(wr - be_wr) < 0.01:
            label = " <- break-even"
        elif abs(wr - 0.917) < 0.001:
            label = " <- observed (n=12)"
        elif abs(wr - 0.75) < 0.001:
            label = " <- go-live threshold"

        print(f"{wr*100:5.1f}% {result['ruin_pct']:5.1f}% "
              f"${result['avg_pnl']:+7.2f} ${result['median_pnl']:+7.2f} "
              f"${result['p5_pnl']:+7.2f} ${result['p95_pnl']:+7.2f} "
              f"{result['avg_max_dd']:5.1f}% {result['p95_max_dd']:4.1f}%{label}")

    # Statistical power analysis
    print(f"\n{'=' * 75}")
    print("STATISTICAL POWER: How many trades to detect edge?")
    print(f"{'=' * 75}")
    print(f"\nTo distinguish true WR from break-even ({be_wr*100:.1f}%) at 95% confidence:")

    for true_wr in [0.75, 0.80, 0.85, 0.90]:
        # n needed for proportion test: n = (z_alpha + z_beta)^2 * p(1-p) / (p - p0)^2
        # z_alpha = 1.645 (one-sided 95%), z_beta = 0.842 (80% power)
        z = 1.645 + 0.842
        p = true_wr
        p0 = be_wr
        if p > p0:
            n_needed = z**2 * p * (1 - p) / (p - p0)**2
            print(f"  True WR={true_wr*100:.0f}%: need n={int(n_needed)+1} trades "
                  f"(~{int(n_needed/3)+1} weeks at 3 trades/day)")
        else:
            print(f"  True WR={true_wr*100:.0f}%: below break-even, no edge to detect")

    # Practical scenarios
    print(f"\n{'=' * 75}")
    print("PRACTICAL SCENARIOS")
    print(f"{'=' * 75}")

    scenarios = [
        ("Pessimistic (WR=70%)", 0.70),
        ("Conservative (WR=75%, go-live)", 0.75),
        ("Moderate (WR=80%)", 0.80),
        ("Optimistic (WR=85%)", 0.85),
        ("Observed paper (WR=91.7%, n=12)", 0.917),
    ]

    for name, wr in scenarios:
        r = run_monte_carlo(wr, bankroll, n_trades, n_sims, stake, avg_price)
        ev_per_trade = wr * params.win_pnl() + (1 - wr) * params.loss_pnl()
        monthly_trades = 90  # ~3/day
        monthly_ev = ev_per_trade * monthly_trades

        print(f"\n  {name}:")
        print(f"    EV per trade:    ${ev_per_trade:+.4f}")
        print(f"    Monthly EV:      ${monthly_ev:+.2f} (~90 trades/month)")
        print(f"    Ruin prob (200): {r['ruin_pct']}%")
        print(f"    Median P&L:      ${r['median_pnl']:+.2f}")
        print(f"    5th percentile:  ${r['p5_pnl']:+.2f}")
        print(f"    95th percentile: ${r['p95_pnl']:+.2f}")
        print(f"    Avg max DD:      {r['avg_max_dd']}%")


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo ruin analysis for weather SELL strategy")
    parser.add_argument("--bankroll", type=float, default=500, help="Starting bankroll (default: $500)")
    parser.add_argument("--trades", type=int, default=200, help="Trades per simulation (default: 200)")
    parser.add_argument("--sims", type=int, default=10_000, help="Number of simulations (default: 10,000)")
    parser.add_argument("--stake", type=float, default=20, help="Position size per trade (default: $20)")
    parser.add_argument("--price", type=float, default=0.28, help="Average entry YES price (default: 0.28)")
    args = parser.parse_args()

    print_report(
        bankroll=args.bankroll,
        n_trades=args.trades,
        n_sims=args.sims,
        stake=args.stake,
        avg_price=args.price,
    )


if __name__ == "__main__":
    main()
