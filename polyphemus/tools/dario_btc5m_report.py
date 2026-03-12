#!/usr/bin/env python3
"""Build a compact BTC 5m evidence report from signals, trades, and context."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import backtester  # noqa: E402


DEFAULT_CONTEXT_REMOTE = "root@82.24.19.114:/opt/openclaw/data/lagbot_context.json"


def r8_label(n: int) -> str:
    """Apply the repo's sample-size confidence buckets."""
    if n < 30:
        return f"ANECDOTAL n={n}"
    if n < 107:
        return f"LOW n={n}"
    if n < 385:
        return f"MODERATE n={n}"
    return f"SIGNIFICANT n={n}"


def price_bucket(price: Optional[float]) -> str:
    """Return a compact entry-price bucket label."""
    if price is None:
        return "unknown"
    if price < 0.40:
        return "0.00-0.39"
    if price < 0.60:
        return "0.40-0.59"
    if price < 0.80:
        return "0.60-0.79"
    return "0.80-1.00"


def time_bucket(secs: Optional[int]) -> str:
    """Return a compact time-remaining bucket label."""
    if secs is None:
        return "unknown"
    if secs < 60:
        return "<60s"
    if secs < 120:
        return "60-119s"
    if secs < 180:
        return "120-179s"
    return "180s+"


def fear_greed_bucket(value: Optional[float]) -> str:
    """Convert Fear & Greed into a readable regime bucket."""
    if value is None:
        return "unknown"
    if value < 20:
        return "extreme_fear"
    if value < 40:
        return "fear"
    if value < 60:
        return "neutral"
    if value < 80:
        return "greed"
    return "extreme_greed"


def hash_file(path: Optional[Path]) -> Optional[str]:
    """Return a short SHA-256 hash for a config snapshot."""
    if not path or not path.exists():
        return None
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def parse_env(path: Optional[Path]) -> Dict[str, str]:
    """Parse a .env-style snapshot without executing it."""
    if not path or not path.exists():
        return {}
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def read_market_context(path: Optional[Path]) -> Dict[str, Any]:
    """Read the market context snapshot if it exists."""
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def download_support_file(remote: str, local: Path) -> Optional[Path]:
    """Best-effort download of a support artifact via scp."""
    local.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["scp", "-q", remote, str(local)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and local.exists() and local.stat().st_size > 0:
        return local
    return None


def resolve_support_paths(instance: str, no_download: bool,
                          config_path: Optional[Path],
                          market_context_path: Optional[Path]) -> Tuple[Optional[Path], Optional[Path]]:
    """Resolve config/context snapshots from explicit paths, cache, or VPS."""
    if config_path or market_context_path:
        return config_path, market_context_path

    cache_dir = backtester.LOCAL_CACHE / instance
    cached_config = cache_dir / ".env"
    cached_context = cache_dir / "lagbot_context.json"

    if no_download:
        return (
            cached_config if cached_config.exists() else None,
            cached_context if cached_context.exists() else None,
        )

    config_remote = f"{backtester.VPS_HOST}:/opt/lagbot/instances/{instance}/.env"
    config_local = download_support_file(config_remote, cached_config)
    context_local = download_support_file(DEFAULT_CONTEXT_REMOTE, cached_context)
    return config_local, context_local


def load_epoch_coverage(signals_db: Path, asset: str = "BTC", window: int = 300) -> List[Dict[str, Any]]:
    """Load epoch_coverage rows if available."""
    conn = sqlite3.connect(str(signals_db))
    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='epoch_coverage'"
        ).fetchall()
        if not tables:
            return []
        rows = conn.execute(
            """
            SELECT epoch, asset, window_secs, bot_saw_signal, bot_signal_source,
                   resolved_outcome, oracle_delta_pct, binance_delta_pct
            FROM epoch_coverage
            WHERE asset = ? AND window_secs = ?
            ORDER BY epoch ASC
            """,
            (asset, window),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def signal_range(signals: Sequence[backtester.Signal]) -> Tuple[Optional[float], Optional[float]]:
    """Return the min/max epoch values for the filtered signal set."""
    if not signals:
        return None, None
    epochs = [signal.epoch for signal in signals]
    return min(epochs), max(epochs)


def aggregate_trade_cohorts(signals: Sequence[backtester.Signal], key_fn) -> List[Dict[str, Any]]:
    """Aggregate executed signal cohorts by an arbitrary key."""
    buckets: Dict[str, List[backtester.Signal]] = defaultdict(list)
    for signal in signals:
        if signal.pnl is None:
            continue
        buckets[key_fn(signal)].append(signal)

    rows: List[Dict[str, Any]] = []
    for key, grouped in sorted(buckets.items()):
        wins = sum(1 for signal in grouped if signal.is_win)
        total_pnl = sum(signal.pnl or 0.0 for signal in grouped)
        rows.append({
            "cohort": key,
            "n": len(grouped),
            "wins": wins,
            "win_rate": wins / len(grouped) if grouped else 0.0,
            "avg_pnl": total_pnl / len(grouped) if grouped else 0.0,
            "total_pnl": total_pnl,
            "r8": r8_label(len(grouped)),
        })
    return rows


def aggregate_guard_reasons(signals: Sequence[backtester.Signal]) -> List[Dict[str, Any]]:
    """Aggregate filtered signals by guard reason."""
    counts = Counter()
    for signal in signals:
        if signal.guard_passed == 1 and signal.outcome not in {"filtered", "shadow"}:
            continue
        reasons = [reason.strip() for reason in signal.guard_reasons.split(",") if reason.strip()]
        if not reasons:
            counts["unspecified_filter"] += 1
            continue
        for reason in reasons:
            counts[reason] += 1
    rows = [{"guard_reason": reason, "count": count, "r8": r8_label(count)}
            for reason, count in counts.most_common()]
    return rows


def aggregate_evidence_verdicts(signals: Sequence[backtester.Signal]) -> List[Dict[str, Any]]:
    """Aggregate runtime evidence verdicts that were logged on signals."""
    buckets: Dict[str, List[backtester.Signal]] = defaultdict(list)
    for signal in signals:
        if not signal.evidence_verdict:
            continue
        buckets[signal.evidence_verdict].append(signal)

    rows: List[Dict[str, Any]] = []
    for verdict, grouped in sorted(buckets.items()):
        expected_values = [
            signal.evidence_expected_pnl for signal in grouped
            if signal.evidence_expected_pnl is not None
        ]
        sample_sizes = [
            int(signal.evidence_sample_size) for signal in grouped
            if signal.evidence_sample_size is not None
        ]
        match_levels = Counter(
            signal.evidence_match_level or "unknown"
            for signal in grouped
        )
        rows.append({
            "verdict": verdict,
            "count": len(grouped),
            "avg_expected_pnl": (
                sum(expected_values) / len(expected_values)
                if expected_values else 0.0
            ),
            "median_sample_size": (
                sorted(sample_sizes)[len(sample_sizes) // 2]
                if sample_sizes else 0
            ),
            "top_match_level": (
                match_levels.most_common(1)[0][0]
                if match_levels else "unknown"
            ),
            "r8": r8_label(len(grouped)),
        })
    return rows


def aggregate_evidence_reasons(signals: Sequence[backtester.Signal]) -> List[Dict[str, Any]]:
    """Aggregate concise evidence-reason prefixes for logged verdicts."""
    counts = Counter()
    for signal in signals:
        reason = (signal.evidence_reason or "").strip()
        if not reason:
            continue
        prefix = reason.split("|", 1)[0].strip()
        counts[prefix] += 1
    return [
        {"reason": reason, "count": count, "r8": r8_label(count)}
        for reason, count in counts.most_common()
    ]


def aggregate_epoch_coverage(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate executed vs filtered vs no-signal epoch coverage."""
    total = len(rows)
    if total == 0:
        return {"total_epochs": 0, "signaled_epochs": 0, "no_signal_epochs": 0}
    signaled = sum(1 for row in rows if int(row.get("bot_saw_signal") or 0) == 1)
    return {
        "total_epochs": total,
        "signaled_epochs": signaled,
        "no_signal_epochs": total - signaled,
        "signal_sources": Counter(
            row.get("bot_signal_source") or "unknown"
            for row in rows if int(row.get("bot_saw_signal") or 0) == 1
        ),
    }


def select_reference_reports(dario_dir: Path, explicit: Optional[Sequence[str]]) -> List[Path]:
    """Return the DARIO report paths to reference."""
    if explicit:
        paths = [dario_dir / name for name in explicit]
    else:
        default_names = [
            "dario_btceth_5m_filter_20260220.md",
            "dario_full_codebase_audit_20260219.md",
            "dario_preflight_stages_20260219.md",
        ]
        paths = [dario_dir / name for name in default_names if (dario_dir / name).exists()]
        if not paths:
            paths = sorted(dario_dir.glob("dario_*.md"))[:3]
    return [path for path in paths if path.exists()]


def extract_reference_highlight(path: Path) -> str:
    """Extract a short highlight from a DARIO markdown report."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("**Verdict:") or stripped.startswith("**Recommendation"):
            return stripped.strip("*")
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:180]
    return "No concise highlight found."


def format_markdown_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    """Render a markdown table."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def fmt_pct(value: float) -> str:
    """Format a ratio as a percentage string."""
    return f"{value * 100:.1f}%"


def fmt_money(value: float) -> str:
    """Format a float as a compact dollar string."""
    return f"${value:.2f}"


def build_report(instance: str, signals: Sequence[backtester.Signal],
                 coverage_rows: Sequence[Dict[str, Any]],
                 config_path: Optional[Path],
                 market_context_path: Optional[Path],
                 dario_reports: Sequence[Path],
                 report_title: str) -> str:
    """Build the BTC 5m markdown report."""
    coverage = aggregate_epoch_coverage(coverage_rows)
    price_rows = aggregate_trade_cohorts(signals, lambda signal: price_bucket(signal.entry_price or signal.midpoint))
    time_rows = aggregate_trade_cohorts(signals, lambda signal: time_bucket(signal.time_remaining_secs))
    source_rows = aggregate_trade_cohorts(signals, lambda signal: signal.source or "unknown")
    exit_rows = aggregate_trade_cohorts(signals, lambda signal: signal.exit_reason or "unknown")
    regime_rows = aggregate_trade_cohorts(signals, lambda signal: signal.regime or "unknown")
    guard_rows = aggregate_guard_reasons(signals)
    evidence_rows = aggregate_evidence_verdicts(signals)
    evidence_reason_rows = aggregate_evidence_reasons(signals)

    config_hash = hash_file(config_path)
    env = parse_env(config_path)
    market_context = read_market_context(market_context_path)
    data_start, data_end = signal_range(signals)
    executed = [signal for signal in signals if signal.pnl is not None]
    filtered = [signal for signal in signals if signal.guard_passed == 0 or signal.outcome == "filtered"]

    lines = [
        f"# {report_title}",
        "",
        f"- Generated: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        f"- Instance: `{instance}`",
        "- Scope: `BTC 5m only`",
        f"- Signals in scope: `{len(signals)}`",
        f"- Executed signals with outcomes: `{len(executed)}`",
        f"- Filtered signals: `{len(filtered)}`",
        f"- Signals with runtime evidence verdicts: `{sum(1 for signal in signals if signal.evidence_verdict)}`",
        f"- Epoch coverage rows: `{coverage['total_epochs']}`",
        f"- Config snapshot hash: `{config_hash or 'unavailable'}`",
        f"- Config label: `{env.get('CONFIG_LABEL', 'unknown')}`",
        f"- Data window: `{data_start or 'unknown'}` → `{data_end or 'unknown'}`",
        "",
        "## Current Snapshot",
        "",
        f"- `ASSET_FILTER`: `{env.get('ASSET_FILTER', 'unknown')}`",
        f"- `MARKET_WINDOW_SECS`: `{env.get('MARKET_WINDOW_SECS', 'unknown')}`",
        f"- `ENTRY_MODE`: `{env.get('ENTRY_MODE', 'unknown')}`",
        f"- `DRY_RUN`: `{env.get('DRY_RUN', 'unknown')}`",
        f"- Market context updated: `{market_context.get('updated_at', 'unavailable')}`",
        f"- Fear & Greed snapshot: `{market_context.get('fear_greed', 'unknown')}` "
        f"(`{fear_greed_bucket(market_context.get('fear_greed'))}`)",
        f"- BTC OI trend: `{(market_context.get('BTC') or {}).get('oi_trend', 'unknown')}`",
        "",
        "## Executive Summary",
        "",
        (
            f"Executed BTC 5m signals with outcomes: `{len(executed)}`. "
            f"Filtered BTC 5m signals: `{len(filtered)}`. "
            f"No-signal epochs in coverage: `{coverage['no_signal_epochs']}`."
        ),
        (
            "This report is read-only and evidence-focused. Treat all low-sample cohorts as "
            "shadow-only inputs until lifecycle testing is complete."
        ),
        "",
        "## Executed Cohorts By Price Bucket",
        "",
    ]

    if price_rows:
        lines.extend(format_markdown_table(
            ["Bucket", "N", "WR", "Avg PnL", "Total PnL", "R8"],
            [
                [
                    row["cohort"],
                    str(row["n"]),
                    fmt_pct(row["win_rate"]),
                    fmt_money(row["avg_pnl"]),
                    fmt_money(row["total_pnl"]),
                    row["r8"],
                ]
                for row in price_rows
            ],
        ))
    else:
        lines.append("No executed BTC 5m cohorts with outcomes were found.")

    lines.extend([
        "",
        "## Executed Cohorts By Time Remaining",
        "",
    ])
    if time_rows:
        lines.extend(format_markdown_table(
            ["Time Remaining", "N", "WR", "Avg PnL", "Total PnL", "R8"],
            [
                [
                    row["cohort"],
                    str(row["n"]),
                    fmt_pct(row["win_rate"]),
                    fmt_money(row["avg_pnl"]),
                    fmt_money(row["total_pnl"]),
                    row["r8"],
                ]
                for row in time_rows
            ],
        ))
    else:
        lines.append("No time-remaining cohorts were found.")

    lines.extend([
        "",
        "## Executed Cohorts By Source",
        "",
    ])
    if source_rows:
        lines.extend(format_markdown_table(
            ["Source", "N", "WR", "Avg PnL", "Total PnL", "R8"],
            [
                [
                    row["cohort"],
                    str(row["n"]),
                    fmt_pct(row["win_rate"]),
                    fmt_money(row["avg_pnl"]),
                    fmt_money(row["total_pnl"]),
                    row["r8"],
                ]
                for row in source_rows
            ],
        ))
    else:
        lines.append("No source cohorts were found.")

    lines.extend([
        "",
        "## Executed Cohorts By Exit Reason",
        "",
    ])
    if exit_rows:
        lines.extend(format_markdown_table(
            ["Exit Reason", "N", "WR", "Avg PnL", "Total PnL", "R8"],
            [
                [
                    row["cohort"],
                    str(row["n"]),
                    fmt_pct(row["win_rate"]),
                    fmt_money(row["avg_pnl"]),
                    fmt_money(row["total_pnl"]),
                    row["r8"],
                ]
                for row in exit_rows
            ],
        ))
    else:
        lines.append("No exit-reason cohorts were found.")

    lines.extend([
        "",
        "## Filtered Signals By Guard Reason",
        "",
    ])
    if guard_rows:
        lines.extend(format_markdown_table(
            ["Guard Reason", "Count", "R8"],
            [[row["guard_reason"], str(row["count"]), row["r8"]] for row in guard_rows],
        ))
    else:
        lines.append("No filtered guard reasons were found.")

    lines.extend([
        "",
        "## Runtime Evidence Verdicts",
        "",
    ])
    if evidence_rows:
        lines.extend(format_markdown_table(
            ["Verdict", "Count", "Avg Expected PnL", "Median Sample", "Top Match Level", "R8"],
            [
                [
                    row["verdict"],
                    str(row["count"]),
                    fmt_money(row["avg_expected_pnl"]),
                    str(row["median_sample_size"]),
                    row["top_match_level"],
                    row["r8"],
                ]
                for row in evidence_rows
            ],
        ))
    else:
        lines.append("No runtime evidence verdicts were logged in this dataset.")

    lines.extend([
        "",
        "## Runtime Evidence Reasons",
        "",
    ])
    if evidence_reason_rows:
        lines.extend(format_markdown_table(
            ["Reason Prefix", "Count", "R8"],
            [[row["reason"], str(row["count"]), row["r8"]] for row in evidence_reason_rows],
        ))
    else:
        lines.append("No runtime evidence reasons were found.")

    lines.extend([
        "",
        "## Context Slices",
        "",
    ])
    if regime_rows:
        lines.extend(format_markdown_table(
            ["Regime", "N", "WR", "Avg PnL", "Total PnL", "R8"],
            [
                [
                    row["cohort"],
                    str(row["n"]),
                    fmt_pct(row["win_rate"]),
                    fmt_money(row["avg_pnl"]),
                    fmt_money(row["total_pnl"]),
                    row["r8"],
                ]
                for row in regime_rows
            ],
        ))
    else:
        lines.append("No regime slices were found.")

    lines.extend([
        "",
        "## Epoch Coverage",
        "",
        f"- Total BTC 5m epochs recorded: `{coverage['total_epochs']}`",
        f"- Epochs with at least one signal: `{coverage['signaled_epochs']}`",
        f"- Epochs with no signal: `{coverage['no_signal_epochs']}`",
    ])
    if coverage.get("signal_sources"):
        lines.append(
            "- Signal sources in coverage: "
            + ", ".join(
                f"`{source}`={count}"
                for source, count in coverage["signal_sources"].most_common()
            )
        )

    lines.extend([
        "",
        "## DARIO Reference Highlights",
        "",
    ])
    if dario_reports:
        for path in dario_reports:
            lines.append(f"- `{path.name}`: {extract_reference_highlight(path)}")
    else:
        lines.append("No reference DARIO reports were found.")

    lines.extend([
        "",
        "## Report Boundaries",
        "",
        "- This report does not assert OpenFang is present. OpenFang remains unverified until live VPS inspection confirms it.",
        "- The config snapshot hash is a current-point-in-time tag. Historical DB rows may still span older mixed config eras.",
        "- Any live BTC 5m strategy change still requires lifecycle evidence before deployment.",
    ])
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Build a BTC 5m DARIO evidence report")
    parser.add_argument("--instance", required=True, help="Instance name, e.g. emmanuel")
    parser.add_argument("--signals-db", type=Path,
                        help="Explicit signals.db path")
    parser.add_argument("--performance-db", type=Path,
                        help="Explicit performance.db path")
    parser.add_argument("--config-path", type=Path,
                        help="Explicit .env snapshot path")
    parser.add_argument("--market-context-path", type=Path,
                        help="Explicit lagbot_context.json path")
    parser.add_argument("--dario-dir", type=Path, default=Path("dario_output"),
                        help="Directory containing selected DARIO markdown reports")
    parser.add_argument("--reference-report", action="append",
                        help="Specific DARIO report filename to reference (repeatable)")
    parser.add_argument("--no-download", action="store_true",
                        help="Use cached data instead of downloading support files")
    parser.add_argument("--output", "-o", type=Path,
                        help="Optional markdown output path")
    parser.add_argument("--report-title", default=None,
                        help="Custom report title")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    signals_db, performance_db = backtester.resolve_db_paths(
        instance=args.instance,
        no_download=args.no_download,
        signals_db=args.signals_db,
        performance_db=args.performance_db,
    )
    config_path, market_context_path = resolve_support_paths(
        instance=args.instance,
        no_download=args.no_download,
        config_path=args.config_path,
        market_context_path=args.market_context_path,
    )

    signals = backtester.load_signals(signals_db, performance_db)
    signals = backtester.filter_signals(signals, asset="BTC", window=300, source=None)
    coverage_rows = load_epoch_coverage(signals_db, asset="BTC", window=300)
    dario_reports = select_reference_reports(args.dario_dir, args.reference_report)
    report_title = args.report_title or f"BTC 5m Evidence Report: {args.instance}"
    markdown = build_report(
        instance=args.instance,
        signals=signals,
        coverage_rows=coverage_rows,
        config_path=config_path,
        market_context_path=market_context_path,
        dario_reports=dario_reports,
        report_title=report_title,
    )

    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(markdown)


if __name__ == "__main__":
    main()
