#!/usr/bin/env python3
"""Backtester for Polyphemus/Emmanuel signal replay.

Downloads signals.db and performance.db from VPS, replays all signals
through configurable filter combinations, and ranks the results.

Usage:
    python3 tools/backtester.py --instance emmanuel --output results.csv
    python3 tools/backtester.py --instance polyphemus
    python3 tools/backtester.py --instance emmanuel --no-download
    python3 tools/backtester.py --instance emmanuel --asset BTC --window 300
"""

import argparse
import csv
import itertools
import json
import math
import os
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VPS_HOST = "root@82.24.19.114"
VPS_BASE = "/opt/lagbot/instances/{instance}/data"
LOCAL_CACHE = Path(__file__).parent / ".backtest_cache"

# Adjusted to current data availability. Keep the default grid small enough
# to be usable on laptop-sized cached DBs.
FILTER_GRID = {
    "entry_price_min": [0.80, 0.82, 0.84],
    "entry_price_max": [0.85, 0.88, 0.90, 0.92],
    "min_time_remaining": [0, 30, 60, 120],
    "min_momentum_pct": [0.0, 0.001, 0.002, 0.003],
    "allowed_regimes": [
        ("trending", "volatile", "flat"),
        ("trending", "volatile"),
        ("trending",),
    ],
    "post_loss_cooldown": [0, 300, 900, 1800],
    "max_consec_losses": [2, 3, 5, 999],
}

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


def resolve_ssh_key() -> Optional[Path]:
    """Return the preferred SSH key for QuantVPS access if one exists."""
    configured = os.environ.get("LAGBOT_SSH_KEY", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path("~/.ssh/oracle_polymarket").expanduser(),
        Path("~/.ssh/id_ed25519").expanduser(),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def ssh_transport_args() -> List[str]:
    """Build deterministic SSH/SCP transport flags for VPS access."""
    args = [
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    key_path = resolve_ssh_key()
    if key_path:
        args.extend(["-i", str(key_path)])
    return args


def scp_fetch(remote: str, local: Path) -> subprocess.CompletedProcess[str]:
    """Copy a remote artifact using the preferred SSH identity."""
    local.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["scp", *ssh_transport_args(), "-q", remote, str(local)],
        capture_output=True,
        text=True,
    )


@dataclass
class Signal:
    """A single signal row enriched with any matched trade outcome."""

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
    source: str
    market_window_secs: int
    pnl: Optional[float]
    pnl_pct: Optional[float]
    is_win: Optional[int]
    exit_reason: Optional[str]
    evidence_cohort: Optional[str]
    evidence_sample_size: Optional[int]
    evidence_r8_label: Optional[str]
    evidence_expected_pnl: Optional[float]
    evidence_verdict: Optional[str]
    evidence_reason: Optional[str]
    evidence_match_level: Optional[str]


@dataclass
class FilterCombo:
    entry_price_min: float
    entry_price_max: float
    min_time_remaining: int
    min_momentum_pct: float
    allowed_regimes: Tuple[str, ...]
    post_loss_cooldown: int
    max_consec_losses: int

    def label(self) -> str:
        regimes = "+".join(self.allowed_regimes)
        return (
            f"price=[{self.entry_price_min:.2f}-{self.entry_price_max:.2f}] "
            f"time>={self.min_time_remaining}s mom>={self.min_momentum_pct:.4f} "
            f"reg={regimes} cool={self.post_loss_cooldown}s "
            f"maxL={self.max_consec_losses}"
        )


@dataclass
class BacktestResult:
    combo: FilterCombo
    total_signals: int = 0
    trades_taken: int = 0
    trades_filtered: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    pnl_list: List[float] = field(default_factory=list)
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    sharpe: float = 0.0

    def compute_stats(self) -> None:
        if self.trades_taken > 0:
            self.win_rate = self.wins / self.trades_taken
            self.avg_pnl = self.total_pnl / self.trades_taken
        if len(self.pnl_list) >= 2:
            mean = sum(self.pnl_list) / len(self.pnl_list)
            var = sum((x - mean) ** 2 for x in self.pnl_list) / (len(self.pnl_list) - 1)
            std = math.sqrt(var) if var > 0 else 0.001
            self.sharpe = mean / std

        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in self.pnl_list:
            cum += pnl
            if cum > peak:
                peak = cum
            drawdown = peak - cum
            if drawdown > max_dd:
                max_dd = drawdown
        self.max_drawdown = max_dd


def infer_window_from_slug(slug: str) -> int:
    """Infer market window seconds from the Polymarket slug."""
    slug_l = slug.lower()
    if "-5m-" in slug_l:
        return 300
    if "-15m-" in slug_l:
        return 900
    if "-1h-" in slug_l:
        return 3600
    return 0


def get_table_columns(conn: sqlite3.Connection, table: str) -> set:
    """Return a set of available columns for the SQLite table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def optional_expr(columns: set, name: str, alias: Optional[str] = None,
                  default_sql: str = "NULL") -> str:
    """Return a safe SQL expression for optional columns."""
    alias = alias or name
    if name in columns:
        return f"{name} AS {alias}"
    return f"{default_sql} AS {alias}"


def detect_trade_pnl_columns(performance_db: Path) -> Tuple[str, str]:
    """Detect the canonical PnL and PnL% column names in trades."""
    conn = sqlite3.connect(str(performance_db))
    try:
        columns = get_table_columns(conn, "trades")
    finally:
        conn.close()

    pnl_col = "pnl" if "pnl" in columns else "profit_loss"
    if pnl_col not in columns:
        pnl_col = "NULL"

    pnl_pct_col = "pnl_pct" if "pnl_pct" in columns else "profit_loss_pct"
    if pnl_pct_col not in columns:
        pnl_pct_col = "NULL"
    return pnl_col, pnl_pct_col


def download_dbs(instance: str) -> Tuple[Optional[Path], Optional[Path]]:
    """SCP signals.db and performance.db from VPS."""
    local_dir = LOCAL_CACHE / instance
    local_dir.mkdir(parents=True, exist_ok=True)

    remote_base = VPS_BASE.format(instance=instance)
    files: Dict[str, Optional[Path]] = {}
    for db_name in ["signals.db", "performance.db"]:
        remote = f"{VPS_HOST}:{remote_base}/{db_name}"
        local = local_dir / db_name
        result = scp_fetch(remote, local)
        if result.returncode == 0 and local.exists() and local.stat().st_size > 0:
            files[db_name] = local
        else:
            files[db_name] = None
            print(f"Warning: failed to download {db_name}: {result.stderr.strip()}")

    return files.get("signals.db"), files.get("performance.db")


def load_signals(signals_db: Path, performance_db: Optional[Path]) -> List[Signal]:
    """Load all signals, enriching with trade outcomes when available."""
    conn = sqlite3.connect(str(signals_db))
    conn.row_factory = sqlite3.Row
    try:
        columns = get_table_columns(conn, "signals")
        signal_selects = [
            "id",
            "epoch",
            "slug",
            "asset",
            "direction",
            optional_expr(columns, "entry_price"),
            optional_expr(columns, "midpoint"),
            optional_expr(columns, "momentum_pct"),
            optional_expr(columns, "regime"),
            optional_expr(columns, "time_remaining_secs"),
            optional_expr(columns, "guard_passed", default_sql="0"),
            optional_expr(columns, "guard_reasons", default_sql="''"),
            optional_expr(columns, "outcome", default_sql="''"),
            optional_expr(columns, "signal_score"),
            optional_expr(columns, "source", default_sql="''"),
            optional_expr(columns, "market_window_secs", default_sql="0"),
            optional_expr(columns, "pnl"),
            optional_expr(columns, "pnl_pct"),
            optional_expr(columns, "is_win"),
            optional_expr(columns, "exit_reason"),
            optional_expr(columns, "evidence_cohort"),
            optional_expr(columns, "evidence_sample_size"),
            optional_expr(columns, "evidence_r8_label", default_sql="''"),
            optional_expr(columns, "evidence_expected_pnl"),
            optional_expr(columns, "evidence_verdict", default_sql="''"),
            optional_expr(columns, "evidence_reason", default_sql="''"),
            optional_expr(columns, "evidence_match_level", default_sql="''"),
        ]
        select_sql = f"""
            SELECT
                {", ".join(signal_selects)}
            FROM signals
            ORDER BY epoch ASC
        """
        rows = conn.execute(select_sql).fetchall()
    finally:
        conn.close()

    perf_by_slug: Dict[str, Dict[str, Any]] = {}
    if performance_db and performance_db.exists():
        pnl_col, pnl_pct_col = detect_trade_pnl_columns(performance_db)
        pconn = sqlite3.connect(str(performance_db))
        pconn.row_factory = sqlite3.Row
        try:
            columns = get_table_columns(pconn, "trades")
            trade_selects = [
                "slug",
                f"{pnl_col} AS trade_pnl",
                f"{pnl_pct_col} AS trade_pnl_pct",
                optional_expr(columns, "exit_reason", default_sql="''"),
                optional_expr(columns, "outcome", default_sql="''"),
            ]
            rows_perf = pconn.execute(
                f"""
                SELECT
                    {", ".join(trade_selects)}
                FROM trades
                """
            ).fetchall()
        finally:
            pconn.close()

        for row in rows_perf:
            slug = row["slug"]
            if not slug:
                continue
            perf_by_slug[slug] = dict(row)

    signals: List[Signal] = []
    for row in rows:
        pnl = row["pnl"]
        pnl_pct = row["pnl_pct"]
        is_win = row["is_win"]
        exit_reason = row["exit_reason"]

        if pnl is None and row["slug"] in perf_by_slug:
            perf = perf_by_slug[row["slug"]]
            pnl = perf.get("trade_pnl")
            pnl_pct = perf.get("trade_pnl_pct")
            exit_reason = exit_reason or perf.get("exit_reason")
            if pnl is not None:
                is_win = 1 if pnl > 0 else 0

        market_window_secs = int(row["market_window_secs"] or 0)
        if market_window_secs <= 0:
            market_window_secs = infer_window_from_slug(row["slug"])

        signals.append(
            Signal(
                id=int(row["id"]),
                epoch=float(row["epoch"]),
                slug=row["slug"],
                asset=row["asset"],
                direction=row["direction"],
                entry_price=row["entry_price"],
                midpoint=row["midpoint"],
                momentum_pct=row["momentum_pct"],
                regime=row["regime"],
                time_remaining_secs=row["time_remaining_secs"],
                guard_passed=int(row["guard_passed"] or 0),
                guard_reasons=row["guard_reasons"] or "",
                outcome=row["outcome"] or "",
                signal_score=row["signal_score"],
                source=row["source"] or "",
                market_window_secs=market_window_secs,
                pnl=pnl,
                pnl_pct=pnl_pct,
                is_win=is_win,
                exit_reason=exit_reason,
                evidence_cohort=row["evidence_cohort"],
                evidence_sample_size=row["evidence_sample_size"],
                evidence_r8_label=row["evidence_r8_label"] or "",
                evidence_expected_pnl=row["evidence_expected_pnl"],
                evidence_verdict=row["evidence_verdict"] or "",
                evidence_reason=row["evidence_reason"] or "",
                evidence_match_level=row["evidence_match_level"] or "",
            )
        )

    return signals


def filter_signals(signals: Sequence[Signal], asset: Optional[str] = None,
                   window: Optional[int] = None,
                   source: Optional[str] = None) -> List[Signal]:
    """Filter the loaded signals by asset, market window, and source."""
    filtered: List[Signal] = []
    for signal in signals:
        if asset and signal.asset.upper() != asset.upper():
            continue
        if window and signal.market_window_secs != window:
            continue
        if source and signal.source != source:
            continue
        filtered.append(signal)
    return filtered


def signal_passes_filter(sig: Signal, combo: FilterCombo) -> bool:
    """Check if a signal passes the given filter combination."""
    entry_point = sig.entry_price or sig.midpoint
    if entry_point is None:
        return False
    if entry_point < combo.entry_price_min or entry_point > combo.entry_price_max:
        return False

    if sig.time_remaining_secs is not None and sig.time_remaining_secs < combo.min_time_remaining:
        return False

    if sig.momentum_pct is not None and abs(sig.momentum_pct) < combo.min_momentum_pct:
        return False

    if sig.regime and sig.regime not in combo.allowed_regimes:
        return False

    if "not_buy_signal" in sig.guard_reasons:
        return False

    if "market_expired" in sig.guard_reasons:
        return False

    return True


def replay_signals(signals: List[Signal], combo: FilterCombo) -> BacktestResult:
    """Replay all signals through a filter combo, simulating sequential execution."""
    result = BacktestResult(combo=combo, total_signals=len(signals))
    consec_losses = 0
    last_loss_epoch = 0.0

    for sig in signals:
        if not signal_passes_filter(sig, combo):
            result.trades_filtered += 1
            continue

        if combo.post_loss_cooldown > 0 and last_loss_epoch > 0:
            if (sig.epoch - last_loss_epoch) < combo.post_loss_cooldown:
                result.trades_filtered += 1
                continue

        if consec_losses >= combo.max_consec_losses:
            result.trades_filtered += 1
            consec_losses = 0
            continue

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
            result.trades_filtered += 1

    result.compute_stats()
    return result


def generate_combos(full: bool = False) -> List[FilterCombo]:
    """Generate all filter combinations from the configured grid."""
    grid = FILTER_GRID if full else FILTER_GRID_REDUCED
    keys = list(grid.keys())
    values = [grid[key] for key in keys]

    combos: List[FilterCombo] = []
    for vals in itertools.product(*values):
        params = dict(zip(keys, vals))
        if params["entry_price_min"] >= params["entry_price_max"]:
            continue
        combos.append(FilterCombo(**params))
    return combos


def summarize_signals(signals: Sequence[Signal]) -> Dict[str, Any]:
    """Return a compact dataset summary for the filtered signal set."""
    executed = [sig for sig in signals if sig.pnl is not None]
    wins = sum(1 for sig in executed if sig.is_win)
    losses = sum(1 for sig in executed if sig.pnl is not None and not sig.is_win)
    sources = sorted({sig.source for sig in signals if sig.source})
    assets = sorted({sig.asset for sig in signals})
    windows = sorted({sig.market_window_secs for sig in signals if sig.market_window_secs})
    evidence_signals = [sig for sig in signals if sig.evidence_verdict]
    evidence_counts: Dict[str, int] = {}
    evidence_r8_counts: Dict[str, int] = {}
    for signal in evidence_signals:
        evidence_counts[signal.evidence_verdict] = evidence_counts.get(signal.evidence_verdict, 0) + 1
        if signal.evidence_r8_label:
            evidence_r8_counts[signal.evidence_r8_label] = evidence_r8_counts.get(signal.evidence_r8_label, 0) + 1
    return {
        "total_signals": len(signals),
        "signals_with_outcomes": len(executed),
        "wins": wins,
        "losses": losses,
        "assets": assets,
        "windows": windows,
        "sources": sources,
        "signals_with_evidence": len(evidence_signals),
        "evidence_verdicts": evidence_counts,
        "evidence_r8_labels": evidence_r8_counts,
    }


def result_to_dict(rank: int, result: BacktestResult) -> Dict[str, Any]:
    """Serialize a backtest result for JSON/markdown output."""
    return {
        "rank": rank,
        "trades_taken": result.trades_taken,
        "trades_filtered": result.trades_filtered,
        "wins": result.wins,
        "losses": result.losses,
        "win_rate": result.win_rate,
        "avg_pnl": result.avg_pnl,
        "total_pnl": result.total_pnl,
        "max_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "combo": {
            **asdict(result.combo),
            "allowed_regimes": list(result.combo.allowed_regimes),
            "label": result.combo.label(),
        },
    }


def build_output_payload(title: str, instance: str, signals: Sequence[Signal],
                         results: Sequence[BacktestResult], asset: Optional[str],
                         window: Optional[int], source: Optional[str]) -> Dict[str, Any]:
    """Build a structured payload for JSON and markdown outputs."""
    return {
        "title": title,
        "instance": instance,
        "filters": {
            "asset": asset,
            "window": window,
            "source": source,
        },
        "dataset": summarize_signals(signals),
        "results": [result_to_dict(idx + 1, result) for idx, result in enumerate(results)],
    }


def render_markdown(payload: Dict[str, Any], top_n: int = 20) -> str:
    """Render a compact markdown report for the replay results."""
    dataset = payload["dataset"]
    filters = payload["filters"]
    lines = [
        f"# {payload['title']}",
        "",
        f"- Instance: `{payload['instance']}`",
        f"- Asset filter: `{filters['asset'] or 'ALL'}`",
        f"- Window filter: `{filters['window'] or 'ALL'}`",
        f"- Source filter: `{filters['source'] or 'ALL'}`",
        f"- Signals analyzed: `{dataset['total_signals']}`",
        f"- Signals with outcomes: `{dataset['signals_with_outcomes']}`",
        f"- Win/Loss with outcomes: `{dataset['wins']}/{dataset['losses']}`",
        f"- Signals with evidence verdicts: `{dataset.get('signals_with_evidence', 0)}`",
        "",
        "## Evidence Verdicts",
        "",
    ]

    evidence_verdicts = dataset.get("evidence_verdicts", {})
    evidence_r8_labels = dataset.get("evidence_r8_labels", {})
    if evidence_verdicts:
        lines.append(
            "- Verdict counts: "
            + ", ".join(
                f"`{name}`={count}" for name, count in sorted(evidence_verdicts.items())
            )
        )
    else:
        lines.append("- Verdict counts: `none`")

    if evidence_r8_labels:
        lines.append(
            "- R8 labels: "
            + ", ".join(
                f"`{name}`={count}" for name, count in sorted(evidence_r8_labels.items())
            )
        )
    else:
        lines.append("- R8 labels: `none`")

    lines.extend([
        "",
        "## Top Results",
        "",
        "| Rank | Trades | Win Rate | Avg PnL | Total PnL | Max DD | Sharpe | Filter |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])

    for result in payload["results"][:top_n]:
        lines.append(
            "| {rank} | {trades_taken} | {win_rate:.1%} | {avg_pnl:.2f} | "
            "{total_pnl:.2f} | {max_drawdown:.2f} | {sharpe:.3f} | {label} |".format(
                rank=result["rank"],
                trades_taken=result["trades_taken"],
                win_rate=result["win_rate"],
                avg_pnl=result["avg_pnl"],
                total_pnl=result["total_pnl"],
                max_drawdown=result["max_drawdown"],
                sharpe=result["sharpe"],
                label=result["combo"]["label"],
            )
        )
    return "\n".join(lines) + "\n"


def write_csv(results: List[BacktestResult], path: str) -> None:
    """Write results to CSV."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "rank", "trades_taken", "trades_filtered", "wins", "losses",
            "win_rate", "avg_pnl", "total_pnl", "max_drawdown", "sharpe",
            "entry_price_min", "entry_price_max", "min_time_remaining",
            "min_momentum_pct", "allowed_regimes", "post_loss_cooldown",
            "max_consec_losses",
        ])
        for idx, result in enumerate(results):
            writer.writerow([
                idx + 1,
                result.trades_taken,
                result.trades_filtered,
                result.wins,
                result.losses,
                f"{result.win_rate:.4f}",
                f"{result.avg_pnl:.4f}",
                f"{result.total_pnl:.4f}",
                f"{result.max_drawdown:.4f}",
                f"{result.sharpe:.4f}",
                result.combo.entry_price_min,
                result.combo.entry_price_max,
                result.combo.min_time_remaining,
                result.combo.min_momentum_pct,
                "+".join(result.combo.allowed_regimes),
                result.combo.post_loss_cooldown,
                result.combo.max_consec_losses,
            ])


def resolve_db_paths(instance: str, no_download: bool = False,
                     signals_db: Optional[Path] = None,
                     performance_db: Optional[Path] = None) -> Tuple[Path, Optional[Path]]:
    """Resolve the database paths either from overrides, cache, or the VPS."""
    if signals_db:
        return signals_db, performance_db

    if no_download:
        cached_signals = LOCAL_CACHE / instance / "signals.db"
        cached_performance = LOCAL_CACHE / instance / "performance.db"
        if not cached_signals.exists():
            raise FileNotFoundError(
                f"{cached_signals} not found. Run without --no-download first or pass --signals-db."
            )
        return cached_signals, cached_performance if cached_performance.exists() else None

    downloaded_signals, downloaded_performance = download_dbs(instance)
    if not downloaded_signals:
        raise FileNotFoundError("Could not download signals.db from VPS")
    return downloaded_signals, downloaded_performance


def run_backtest(instance: str, no_download: bool = False,
                 full_grid: bool = False, output: Optional[str] = None,
                 output_format: str = "csv", asset: Optional[str] = None,
                 window: Optional[int] = None, source: Optional[str] = None,
                 report_title: Optional[str] = None,
                 signals_db: Optional[Path] = None,
                 performance_db: Optional[Path] = None) -> List[BacktestResult]:
    """Run the filtered signal replay and optionally emit structured output."""
    signals_path, performance_path = resolve_db_paths(
        instance=instance,
        no_download=no_download,
        signals_db=signals_db,
        performance_db=performance_db,
    )

    signals = load_signals(signals_path, performance_path)
    signals = filter_signals(signals, asset=asset, window=window, source=source)
    combos = generate_combos(full=full_grid)

    results: List[BacktestResult] = []
    for combo in combos:
        results.append(replay_signals(signals, combo))
    results.sort(key=lambda result: result.total_pnl, reverse=True)

    title = report_title or f"Backtest Results: {instance}"
    payload = build_output_payload(title, instance, signals, results, asset, window, source)

    if output:
        output_path = Path(output)
        if output_format == "csv":
            write_csv(results, str(output_path))
        elif output_format == "json":
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        elif output_format == "markdown":
            output_path.write_text(render_markdown(payload), encoding="utf-8")
        else:
            raise ValueError(f"Unsupported output format: {output_format}")

    print(f"=== Backtesting {instance} ===")
    print(f"Signals analyzed: {payload['dataset']['total_signals']}")
    print(f"Signals with outcomes: {payload['dataset']['signals_with_outcomes']}")
    print(f"Sources present: {', '.join(payload['dataset']['sources']) or 'none'}")
    print(f"Top result: {results[0].combo.label()} | total_pnl={results[0].total_pnl:.2f}"
          if results else "No results")
    if output:
        print(f"Output written to {output} ({output_format})")

    return results


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Backtest signal filter combinations")
    parser.add_argument("--instance", required=True, help="Bot instance name")
    parser.add_argument("--output", "-o", help="Optional output path")
    parser.add_argument("--output-format", choices=["csv", "json", "markdown"],
                        default="csv", help="Structured output format when --output is set")
    parser.add_argument("--report-title", default=None,
                        help="Title to use for markdown/json output")
    parser.add_argument("--no-download", action="store_true",
                        help="Use cached DBs instead of downloading")
    parser.add_argument("--full-grid", action="store_true",
                        help="Use the full filter grid (slower)")
    parser.add_argument("--asset", help="Restrict analysis to a single asset, e.g. BTC")
    parser.add_argument("--window", type=int,
                        help="Restrict analysis to a market window in seconds, e.g. 300")
    parser.add_argument("--source", help="Restrict analysis to a single signal source")
    parser.add_argument("--signals-db", type=Path,
                        help="Explicit signals.db path (bypasses download/cache resolution)")
    parser.add_argument("--performance-db", type=Path,
                        help="Explicit performance.db path (optional when --signals-db is set)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_backtest(
        instance=args.instance,
        no_download=args.no_download,
        full_grid=args.full_grid,
        output=args.output,
        output_format=args.output_format,
        asset=args.asset,
        window=args.window,
        source=args.source,
        report_title=args.report_title,
        signals_db=args.signals_db,
        performance_db=args.performance_db,
    )


if __name__ == "__main__":
    main()
