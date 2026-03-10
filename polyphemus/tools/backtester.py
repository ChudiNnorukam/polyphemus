#!/usr/bin/env python3
"""Backtester for Polyphemus/Emmanuel signal replay.

Downloads signals.db and performance.db from VPS, replays all signals
through configurable filter combinations, and ranks the results.

Usage:
    python3 tools/backtester.py --instance emmanuel --output results.csv
    python3 tools/backtester.py --instance polyphemus
    python3 tools/backtester.py --instance emmanuel --no-download  # use cached DBs
"""

import argparse
import csv
import itertools
import math
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

VPS_HOST = "root@82.24.19.114"
VPS_BASE = "/opt/lagbot/instances/{instance}/data"
LOCAL_CACHE = Path(__file__).parent / ".backtest_cache"

# ── Filter dimensions ──────────────────────────────────────────────
# Adjusted to actual data availability (VPIN/taker_delta are NULL in current data)

FILTER_GRID = {
    "entry_price_min": [0.80, 0.82, 0.84],
    "entry_price_max": [0.85, 0.88, 0.90, 0.92],
    "min_time_remaining": [0, 30, 60, 120],        # seconds before market close
    "min_momentum_pct": [0.0, 0.001, 0.002, 0.003],
    "allowed_regimes": [
        ("trending", "volatile", "flat"),           # all
        ("trending", "volatile"),                   # no flat
        ("trending",),                              # trending only
    ],
    "post_loss_cooldown": [0, 300, 900, 1800],      # seconds (0, 5m, 15m, 30m)
    "max_consec_losses": [2, 3, 5, 999],            # 999 = no limit
}

# For full grid: too many combos. Use a reduced set for speed.
FILTER_GRID_REDUCED = {
    "entry_price_min": [0.80, 0.82],
    "entry_price_max": [0.88, 0.90],
    "min_time_remaining": [0, 60],
    "min_momentum_pct": [0.0, 0.002],
    "allowed_regimes": [
        ("trending", "volatile", "flat"),
        ("trending", "volatile"),
    ],
    "post_loss_cooldown": [0, 900],
    "max_consec_losses": [3, 999],
}


@dataclass
class Signal:
    """A single signal from signals.db."""
    id: int
    epoch: float
    slug: str
    asset: str
    direction: str
    entry_price: Optional[float]
    midpoint: Optional[float]
    momentum_pct: Optional[float]
    regime: Optional[str]
    time_remaining_secs: Optional[int]
    guard_passed: int
    guard_reasons: str
    outcome: str
    signal_score: Optional[float]
    # Trade outcome (from signals.db or joined from performance.db)
    pnl: Optional[float]
    pnl_pct: Optional[float]
    is_win: Optional[int]
    exit_reason: Optional[str]


@dataclass
class FilterCombo:
    entry_price_min: float
    entry_price_max: float
    min_time_remaining: int
    min_momentum_pct: float
    allowed_regimes: Tuple[str, ...]
    post_loss_cooldown: int  # seconds
    max_consec_losses: int

    def label(self) -> str:
        regimes = "+".join(self.allowed_regimes)
        return (f"price=[{self.entry_price_min:.2f}-{self.entry_price_max:.2f}] "
                f"time>={self.min_time_remaining}s mom>={self.min_momentum_pct:.4f} "
                f"reg={regimes} cool={self.post_loss_cooldown}s maxL={self.max_consec_losses}")


@dataclass
class BacktestResult:
    combo: FilterCombo
    total_signals: int = 0
    trades_taken: int = 0
    trades_filtered: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    pnl_list: list = field(default_factory=list)
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    sharpe: float = 0.0

    def compute_stats(self):
        if self.trades_taken > 0:
            self.win_rate = self.wins / self.trades_taken
            self.avg_pnl = self.total_pnl / self.trades_taken
        if len(self.pnl_list) >= 2:
            mean = sum(self.pnl_list) / len(self.pnl_list)
            var = sum((x - mean) ** 2 for x in self.pnl_list) / (len(self.pnl_list) - 1)
            std = math.sqrt(var) if var > 0 else 0.001
            self.sharpe = mean / std
        # Max drawdown from cumulative PnL
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in self.pnl_list:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd


def download_dbs(instance: str) -> Tuple[Optional[Path], Optional[Path]]:
    """SCP signals.db and performance.db from VPS."""
    local_dir = LOCAL_CACHE / instance
    local_dir.mkdir(parents=True, exist_ok=True)

    remote_base = VPS_BASE.format(instance=instance)
    files = {}
    for db_name in ["signals.db", "performance.db"]:
        remote = f"{VPS_HOST}:{remote_base}/{db_name}"
        local = local_dir / db_name
        print(f"  Downloading {remote} -> {local}")
        result = subprocess.run(
            ["scp", "-q", remote, str(local)],
            capture_output=True, text=True
        )
        if result.returncode == 0 and local.stat().st_size > 0:
            files[db_name] = local
        else:
            print(f"  Warning: Failed to download {db_name}: {result.stderr.strip()}")
            files[db_name] = None

    return files.get("signals.db"), files.get("performance.db")


def load_signals(signals_db: Path, performance_db: Optional[Path]) -> List[Signal]:
    """Load all signals, enriching with trade outcomes from performance.db."""
    conn = sqlite3.connect(str(signals_db))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, epoch, slug, asset, direction, entry_price, midpoint,
               momentum_pct, regime, time_remaining_secs, guard_passed,
               guard_reasons, outcome, signal_score, pnl, pnl_pct, is_win,
               exit_reason
        FROM signals
        ORDER BY epoch ASC
    """).fetchall()
    conn.close()

    # Build lookup from performance.db for enrichment
    perf_by_slug: Dict[str, Dict[str, Any]] = {}
    if performance_db and performance_db.exists():
        pconn = sqlite3.connect(str(performance_db))
        pconn.row_factory = sqlite3.Row
        for row in pconn.execute("SELECT slug, pnl, pnl_pct, exit_reason, outcome FROM trades"):
            perf_by_slug[row["slug"]] = dict(row)
        pconn.close()

    signals = []
    for r in rows:
        pnl = r["pnl"]
        pnl_pct = r["pnl_pct"]
        is_win = r["is_win"]
        exit_reason = r["exit_reason"]

        # Enrich from performance.db if signal data is missing
        if pnl is None and r["slug"] in perf_by_slug:
            p = perf_by_slug[r["slug"]]
            pnl = p.get("pnl")
            pnl_pct = p.get("pnl_pct")
            exit_reason = p.get("exit_reason")
            if pnl is not None:
                is_win = 1 if pnl > 0 else 0

        signals.append(Signal(
            id=r["id"], epoch=r["epoch"], slug=r["slug"], asset=r["asset"],
            direction=r["direction"], entry_price=r["entry_price"],
            midpoint=r["midpoint"], momentum_pct=r["momentum_pct"],
            regime=r["regime"], time_remaining_secs=r["time_remaining_secs"],
            guard_passed=r["guard_passed"], guard_reasons=r["guard_reasons"] or "",
            outcome=r["outcome"] or "", signal_score=r["signal_score"],
            pnl=pnl, pnl_pct=pnl_pct, is_win=is_win, exit_reason=exit_reason,
        ))

    return signals


def signal_passes_filter(sig: Signal, combo: FilterCombo) -> bool:
    """Check if a signal passes the given filter combination (static filters only)."""
    # Entry price range
    ep = sig.entry_price or sig.midpoint
    if ep is None:
        return False
    if ep < combo.entry_price_min or ep > combo.entry_price_max:
        return False

    # Time remaining
    tr = sig.time_remaining_secs
    if tr is not None and tr < combo.min_time_remaining:
        return False

    # Momentum
    mp = sig.momentum_pct
    if mp is not None and abs(mp) < combo.min_momentum_pct:
        return False

    # Regime
    if sig.regime and sig.regime not in combo.allowed_regimes:
        return False

    # Must be a buy signal (direction-based) - skip "not_buy_signal" filtered ones
    if "not_buy_signal" in sig.guard_reasons:
        return False

    # Skip expired markets
    if "market_expired" in sig.guard_reasons:
        return False

    return True


def replay_signals(signals: List[Signal], combo: FilterCombo) -> BacktestResult:
    """Replay all signals through a filter combo, simulating sequential execution."""
    result = BacktestResult(combo=combo, total_signals=len(signals))
    consec_losses = 0
    last_loss_epoch = 0.0

    for sig in signals:
        # Static filter
        if not signal_passes_filter(sig, combo):
            result.trades_filtered += 1
            continue

        # Post-loss cooldown
        if combo.post_loss_cooldown > 0 and last_loss_epoch > 0:
            if (sig.epoch - last_loss_epoch) < combo.post_loss_cooldown:
                result.trades_filtered += 1
                continue

        # Max consecutive losses
        if consec_losses >= combo.max_consec_losses:
            result.trades_filtered += 1
            # Reset after skipping one (the bot would resume)
            consec_losses = 0
            continue

        # This signal would be taken - but do we have outcome data?
        # If the signal was actually executed, use real outcome.
        # If it was filtered by the bot but passes OUR filter, we can't know the outcome.
        # We'll only count signals that were actually executed (have real PnL).
        if sig.pnl is not None:
            result.trades_taken += 1
            result.total_pnl += sig.pnl
            result.pnl_list.append(sig.pnl)
            if sig.is_win:
                result.wins += 1
                consec_losses = 0
            else:
                result.losses += 1
                consec_losses += 1
                last_loss_epoch = sig.epoch
        else:
            # Signal passes our filter but has no outcome data (was filtered by bot or no exit)
            # We can count it as "would have taken" but skip from PnL calc
            result.trades_filtered += 1

    result.compute_stats()
    return result


def generate_combos(full: bool = False) -> List[FilterCombo]:
    """Generate all filter combinations from the grid."""
    grid = FILTER_GRID if full else FILTER_GRID_REDUCED
    keys = list(grid.keys())
    values = [grid[k] for k in keys]

    combos = []
    for vals in itertools.product(*values):
        params = dict(zip(keys, vals))
        # Skip invalid combos (min >= max)
        if params["entry_price_min"] >= params["entry_price_max"]:
            continue
        combos.append(FilterCombo(**params))
    return combos


def run_backtest(instance: str, no_download: bool = False, full_grid: bool = False,
                 output: Optional[str] = None) -> List[BacktestResult]:
    """Main backtest runner."""
    print(f"=== Backtesting {instance} ===")

    # Step 1: Download DBs
    if no_download:
        signals_db = LOCAL_CACHE / instance / "signals.db"
        performance_db = LOCAL_CACHE / instance / "performance.db"
        if not signals_db.exists():
            print(f"Error: {signals_db} not found. Run without --no-download first.")
            sys.exit(1)
    else:
        print("Downloading databases from VPS...")
        signals_db, performance_db = download_dbs(instance)
        if not signals_db:
            print("Error: Could not download signals.db")
            sys.exit(1)

    # Step 2: Load signals
    print("Loading signals...")
    signals = load_signals(signals_db, performance_db)
    executed = [s for s in signals if s.pnl is not None]
    print(f"  Total signals: {len(signals)}")
    print(f"  With outcomes: {len(executed)}")
    print(f"  Win/Loss: {sum(1 for s in executed if s.is_win)}/{sum(1 for s in executed if not s.is_win)}")

    # Step 3: Generate filter combos
    combos = generate_combos(full=full_grid)
    print(f"  Filter combinations: {len(combos)}")

    # Step 4: Replay
    print("Running backtest...")
    results = []
    for i, combo in enumerate(combos):
        r = replay_signals(signals, combo)
        results.append(r)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(combos)} combos evaluated...")

    # Sort by total PnL descending
    results.sort(key=lambda r: r.total_pnl, reverse=True)

    # Step 5: Print top results
    print(f"\n{'='*120}")
    print(f"TOP 20 FILTER COMBINATIONS (by total PnL)")
    print(f"{'='*120}")
    print(f"{'Rank':>4} {'Trades':>6} {'WinR':>6} {'AvgPnL':>8} {'TotalPnL':>10} "
          f"{'MaxDD':>8} {'Sharpe':>7} | Filter")
    print(f"{'-'*120}")
    for i, r in enumerate(results[:20]):
        print(f"{i+1:>4} {r.trades_taken:>6} {r.win_rate:>6.1%} {r.avg_pnl:>8.2f} "
              f"{r.total_pnl:>10.2f} {r.max_drawdown:>8.2f} {r.sharpe:>7.3f} | "
              f"{r.combo.label()}")

    # Also show worst 5
    print(f"\nBOTTOM 5:")
    for r in results[-5:]:
        if r.trades_taken > 0:
            print(f"  Trades={r.trades_taken} WR={r.win_rate:.1%} PnL={r.total_pnl:.2f} | {r.combo.label()}")

    # Step 6: Output CSV
    if output:
        write_csv(results, output)
        print(f"\nResults written to {output}")

    return results


def write_csv(results: List[BacktestResult], path: str):
    """Write results to CSV."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "rank", "trades_taken", "trades_filtered", "wins", "losses",
            "win_rate", "avg_pnl", "total_pnl", "max_drawdown", "sharpe",
            "entry_price_min", "entry_price_max", "min_time_remaining",
            "min_momentum_pct", "allowed_regimes", "post_loss_cooldown",
            "max_consec_losses",
        ])
        for i, r in enumerate(results):
            w.writerow([
                i + 1, r.trades_taken, r.trades_filtered, r.wins, r.losses,
                f"{r.win_rate:.4f}", f"{r.avg_pnl:.4f}", f"{r.total_pnl:.4f}",
                f"{r.max_drawdown:.4f}", f"{r.sharpe:.4f}",
                r.combo.entry_price_min, r.combo.entry_price_max,
                r.combo.min_time_remaining, r.combo.min_momentum_pct,
                "+".join(r.combo.allowed_regimes),
                r.combo.post_loss_cooldown, r.combo.max_consec_losses,
            ])


def main():
    parser = argparse.ArgumentParser(description="Backtest signal filter combinations")
    parser.add_argument("--instance", required=True, choices=["emmanuel", "polyphemus"],
                        help="Bot instance to backtest")
    parser.add_argument("--output", "-o", help="Output CSV path")
    parser.add_argument("--no-download", action="store_true",
                        help="Use cached DBs instead of downloading")
    parser.add_argument("--full-grid", action="store_true",
                        help="Use full filter grid (slower, more combos)")
    args = parser.parse_args()

    run_backtest(
        instance=args.instance,
        no_download=args.no_download,
        full_grid=args.full_grid,
        output=args.output,
    )


if __name__ == "__main__":
    main()
