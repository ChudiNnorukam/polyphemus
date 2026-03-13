#!/usr/bin/env python3
"""Multi-strategy BTC 5m shadow scan against resolved Polymarket epochs.

Loads cached `signals.db` files for emmanuel/polyphemus, resolves actual market
outcomes from Gamma by slug, and simulates several strategy profiles over the
same candidate set.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import ssl
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".backtest_cache"
RESOLUTION_CACHE_PATH = CACHE_DIR / "btc5m_resolution_cache.json"
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"


def r8_label(n: int) -> str:
    if n < 30:
        return f"ANECDOTAL n={n}"
    if n < 107:
        return f"LOW n={n}"
    if n < 385:
        return f"MODERATE n={n}"
    return f"SIGNIFICANT n={n}"


def price_bucket(price: float) -> str:
    if price < 0.40:
        return "0.00-0.39"
    if price < 0.60:
        return "0.40-0.59"
    if price < 0.80:
        return "0.60-0.79"
    return "0.80-1.00"


def time_bucket(secs: Optional[int]) -> str:
    if secs is None:
        return "unknown"
    if secs < 60:
        return "<60s"
    if secs < 120:
        return "60-119s"
    if secs < 180:
        return "120-179s"
    if secs < 240:
        return "180-239s"
    return "240s+"


@dataclass
class Candidate:
    instance: str
    epoch: float
    slug: str
    direction: str
    status: str
    source: str
    price: float
    momentum_pct: float
    time_remaining_secs: Optional[int]
    regime: str
    volatility_1h: Optional[float]
    trend_1h: Optional[float]
    fear_greed: Optional[float]
    market_regime: str
    oi_trend: str
    taker_delta: Optional[float]
    vpin_5m: Optional[float]
    book_imbalance: Optional[float]
    guard_passed: int
    guard_reasons: str
    config_era: str
    config_label: str
    instance_name: str
    shadow_current_guarded: int
    shadow_ensemble_candidate: int
    shadow_ensemble_selected: int


@dataclass
class StrategyProfile:
    name: str
    principle: str
    description: str
    predicate: Callable[[Candidate], bool]
    ranker: Callable[[Candidate], float]


@dataclass
class StrategyResult:
    profile: StrategyProfile
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_gross_pnl: float
    avg_gross_pnl: float
    gross_roi_on_cost: float
    total_net_pnl: float
    avg_net_pnl: float
    net_roi_on_cost: float
    max_drawdown: float
    trades_per_day: float
    median_price: float
    median_time_remaining: float
    top_sources: List[str]
    top_price_buckets: List[str]
    status_mix: List[str]
    r8: str
    net_pnl_series: List[float]
    cost_series: List[float]
    rolling_5_worst_loss: float


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def load_candidates(instance: str) -> List[Candidate]:
    db_path = CACHE_DIR / instance / "signals.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}

        def expr(name: str, default_sql: str = "NULL") -> str:
            return name if name in columns else f"{default_sql} AS {name}"

        rows = conn.execute(
            f"""
            SELECT
                epoch,
                slug,
                direction,
                COALESCE(outcome, '') AS status,
                COALESCE(source, '') AS source,
                COALESCE(entry_price, midpoint, 0) AS price,
                COALESCE(momentum_pct, 0) AS momentum_pct,
                time_remaining_secs,
                COALESCE(regime, '') AS regime,
                volatility_1h,
                trend_1h,
                fear_greed,
                COALESCE(market_regime, '') AS market_regime,
                COALESCE(oi_trend, '') AS oi_trend,
                taker_delta,
                vpin_5m,
                book_imbalance,
                COALESCE(guard_passed, 0) AS guard_passed,
                COALESCE(guard_reasons, '') AS guard_reasons,
                {expr("config_era", "''")},
                {expr("config_label", "''")},
                {expr("instance_name", "''")},
                {expr("shadow_current_guarded", "0")},
                {expr("shadow_ensemble_candidate", "0")},
                {expr("shadow_ensemble_selected", "0")}
            FROM signals
            WHERE asset = 'BTC' AND market_window_secs = 300
              AND COALESCE(direction, '') IN ('Up', 'Down')
            ORDER BY epoch ASC
            """
        ).fetchall()
        return [
            Candidate(
                instance=instance,
                epoch=row["epoch"],
                slug=row["slug"],
                direction=row["direction"],
                status=row["status"],
                source=row["source"] or "unknown",
                price=safe_float(row["price"]),
                momentum_pct=safe_float(row["momentum_pct"]),
                time_remaining_secs=safe_int(row["time_remaining_secs"]),
                regime=row["regime"] or "unknown",
                volatility_1h=(safe_float(row["volatility_1h"], None) if row["volatility_1h"] is not None else None),
                trend_1h=(safe_float(row["trend_1h"], None) if row["trend_1h"] is not None else None),
                fear_greed=(safe_float(row["fear_greed"], None) if row["fear_greed"] is not None else None),
                market_regime=row["market_regime"] or "unknown",
                oi_trend=row["oi_trend"] or "unknown",
                taker_delta=(safe_float(row["taker_delta"], None) if row["taker_delta"] is not None else None),
                vpin_5m=(safe_float(row["vpin_5m"], None) if row["vpin_5m"] is not None else None),
                book_imbalance=(safe_float(row["book_imbalance"], None) if row["book_imbalance"] is not None else None),
                guard_passed=int(row["guard_passed"] or 0),
                guard_reasons=row["guard_reasons"] or "",
                config_era=row["config_era"] or "",
                config_label=row["config_label"] or "",
                instance_name=row["instance_name"] or instance,
                shadow_current_guarded=int(row["shadow_current_guarded"] or 0),
                shadow_ensemble_candidate=int(row["shadow_ensemble_candidate"] or 0),
                shadow_ensemble_selected=int(row["shadow_ensemble_selected"] or 0),
            )
            for row in rows
            if row["slug"]
        ]
    finally:
        conn.close()


def directionality(candidate: Candidate) -> float:
    if candidate.volatility_1h in (None, 0) or candidate.trend_1h is None:
        return 0.0
    return abs(candidate.trend_1h) / candidate.volatility_1h


def aligned_book(candidate: Candidate) -> bool:
    if candidate.book_imbalance is None:
        return False
    if candidate.direction == "Up":
        return candidate.book_imbalance >= 0.53
    return candidate.book_imbalance <= 0.47


def aligned_taker(candidate: Candidate) -> bool:
    if candidate.taker_delta is None:
        return False
    if candidate.direction == "Up":
        return candidate.taker_delta > 0
    return candidate.taker_delta < 0


def base_score(candidate: Candidate) -> float:
    score = 0.0
    score += max(0.0, 1.0 - candidate.price) * 2.2
    score += min(max(abs(candidate.momentum_pct), 0.0), 0.01) * 45.0
    if candidate.time_remaining_secs is not None:
        score += min(candidate.time_remaining_secs, 300) / 300.0
    score += min(directionality(candidate), 1.0) * 0.8
    if aligned_book(candidate):
        score += 0.35
    if aligned_taker(candidate):
        score += 0.25
    if candidate.regime in {"trending", "volatile"}:
        score += 0.15
    if candidate.source == "resolution_snipe":
        score -= 0.30
    return score


def load_resolution_cache() -> Dict[str, str]:
    if RESOLUTION_CACHE_PATH.exists():
        return json.loads(RESOLUTION_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_resolution_cache(cache: Dict[str, str]) -> None:
    RESOLUTION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESOLUTION_CACHE_PATH.write_text(
        json.dumps(cache, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def fetch_resolution(slug: str, retries: int = 3) -> Optional[str]:
    query = urllib.parse.urlencode({"slug": slug})
    context = ssl._create_unverified_context()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{GAMMA_API_URL}?{query}",
                headers={"User-Agent": "curl/8.0"},
            )
            with urllib.request.urlopen(req, timeout=20, context=context) as response:
                payload = json.load(response)
            if not payload:
                return None
            market = payload[0]
            outcomes_raw = market.get("outcomes") or []
            try:
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else list(outcomes_raw)
            except Exception:
                outcomes = []
            prices_raw = market.get("outcomePrices") or []
            try:
                prices = [float(value) for value in json.loads(prices_raw) if value is not None] if isinstance(prices_raw, str) else [float(value) for value in prices_raw]
            except Exception:
                prices = []
            if len(outcomes) != len(prices):
                return None
            for outcome, price in zip(outcomes, prices):
                if price >= 0.999:
                    return str(outcome)
            return None
        except Exception:
            if attempt == retries - 1:
                return None
            time.sleep(0.2 * (attempt + 1))
    return None


def resolve_outcomes(slugs: Iterable[str], workers: int = 4) -> Dict[str, str]:
    target_slugs = sorted(set(slugs))
    cache = load_resolution_cache()
    unresolved = [slug for slug in target_slugs if slug not in cache]
    if unresolved:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_resolution, slug): slug for slug in unresolved}
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    resolved = future.result()
                except Exception:
                    resolved = None
                if resolved:
                    cache[slug] = resolved
        save_resolution_cache(cache)
    return {slug: outcome for slug, outcome in cache.items() if slug in target_slugs}


def simulate_profile(
    profile: StrategyProfile,
    candidates: List[Candidate],
    resolutions: Dict[str, str],
) -> StrategyResult:
    selected_by_slug: Dict[str, tuple[float, Candidate]] = {}
    for candidate in candidates:
        if candidate.slug not in resolutions:
            continue
        if not profile.predicate(candidate):
            continue
        score = profile.ranker(candidate)
        current = selected_by_slug.get(candidate.slug)
        if current is None or score > current[0]:
            selected_by_slug[candidate.slug] = (score, candidate)

    selected = [item[1] for item in sorted(selected_by_slug.values(), key=lambda item: item[1].epoch)]
    gross_pnl_list: List[float] = []
    net_pnl_list: List[float] = []
    costs: List[float] = []
    wins = 0
    sources = Counter()
    prices: List[float] = []
    times: List[int] = []
    statuses = Counter()
    price_buckets = Counter()
    config_eras = Counter()

    for candidate in selected:
        resolved = resolutions[candidate.slug]
        win = candidate.direction == resolved
        gross_pnl = (1.0 - candidate.price) if win else -candidate.price
        effective_cost = candidate.price + fee_adjustment(candidate)
        net_pnl = (1.0 - effective_cost) if win else -effective_cost
        gross_pnl_list.append(gross_pnl)
        net_pnl_list.append(net_pnl)
        costs.append(candidate.price)
        wins += 1 if win else 0
        sources[candidate.source] += 1
        statuses[candidate.status or "unlabeled"] += 1
        price_buckets[price_bucket(candidate.price)] += 1
        if candidate.config_era:
            config_eras[candidate.config_era] += 1
        prices.append(candidate.price)
        if candidate.time_remaining_secs is not None:
            times.append(candidate.time_remaining_secs)

    total_gross_pnl = sum(gross_pnl_list)
    total_net_pnl = sum(net_pnl_list)
    losses = len(selected) - wins
    win_rate = wins / len(selected) if selected else 0.0
    avg_gross_pnl = total_gross_pnl / len(selected) if selected else 0.0
    avg_net_pnl = total_net_pnl / len(selected) if selected else 0.0
    gross_roi_on_cost = total_gross_pnl / sum(costs) if costs and sum(costs) > 0 else 0.0
    net_roi_on_cost = total_net_pnl / sum(costs) if costs and sum(costs) > 0 else 0.0

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for pnl in net_pnl_list:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    rolling_5_worst_loss = 0.0
    if net_pnl_list:
        for idx in range(len(net_pnl_list)):
            rolling_5_worst_loss = min(
                rolling_5_worst_loss,
                sum(net_pnl_list[idx:idx + 5]),
            )

    if selected:
        span_days = max(1.0 / 24.0, (selected[-1].epoch - selected[0].epoch) / 86400.0)
        trades_per_day = len(selected) / span_days
    else:
        trades_per_day = 0.0

    return StrategyResult(
        profile=profile,
        trades=len(selected),
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        total_gross_pnl=total_gross_pnl,
        avg_gross_pnl=avg_gross_pnl,
        gross_roi_on_cost=gross_roi_on_cost,
        total_net_pnl=total_net_pnl,
        avg_net_pnl=avg_net_pnl,
        net_roi_on_cost=net_roi_on_cost,
        max_drawdown=max_drawdown,
        trades_per_day=trades_per_day,
        median_price=statistics.median(prices) if prices else 0.0,
        median_time_remaining=statistics.median(times) if times else 0.0,
        top_sources=[f"{source} ({count})" for source, count in sources.most_common(3)],
        top_price_buckets=[f"{bucket} ({count})" for bucket, count in price_buckets.most_common(3)],
        status_mix=[f"{status} ({count})" for status, count in statuses.most_common(3)],
        r8=r8_label(len(selected)),
        net_pnl_series=net_pnl_list,
        cost_series=costs,
        rolling_5_worst_loss=rolling_5_worst_loss,
    )


def fee_adjustment(candidate: Candidate) -> float:
    """Approximate fee/slippage cost per 1-share shadow trade."""
    fee_rate = 0.001
    slippage = 0.0
    if candidate.source in {"window_delta", "resolution_snipe"}:
        slippage += 0.002
    elif candidate.source == "binance_momentum":
        slippage += 0.0005
    if candidate.price >= 0.90:
        slippage += 0.001
    return (candidate.price * fee_rate) + slippage


def profiles() -> List[StrategyProfile]:
    return [
        StrategyProfile(
            name="baseline_binance_all",
            principle="Raw trend-following baseline",
            description="All BTC 5m binance_momentum candidates, no cost or timing discipline.",
            predicate=lambda c: c.source == "binance_momentum",
            ranker=base_score,
        ),
        StrategyProfile(
            name="current_guarded_shadow",
            principle="Cost and timing discipline",
            description="Binance momentum with price <= 0.80 and at least 210s remaining.",
            predicate=lambda c: c.source == "binance_momentum" and c.price <= 0.80 and (c.time_remaining_secs or 0) >= 210,
            ranker=base_score,
        ),
        StrategyProfile(
            name="early_quality_momentum",
            principle="Trend strength plus early entry",
            description="Binance momentum with >=0.30% momentum, price <= 0.75, and at least 240s remaining.",
            predicate=lambda c: c.source == "binance_momentum" and abs(c.momentum_pct) >= 0.003 and c.price <= 0.75 and (c.time_remaining_secs or 0) >= 240,
            ranker=base_score,
        ),
        StrategyProfile(
            name="microstructure_confirmed",
            principle="Order-flow confirmation",
            description="Binance momentum with cheap-ish entry plus aligned book imbalance and taker flow.",
            predicate=lambda c: c.source == "binance_momentum" and c.price <= 0.82 and (c.time_remaining_secs or 0) >= 180 and aligned_book(c) and aligned_taker(c),
            ranker=lambda c: base_score(c) + 0.5,
        ),
        StrategyProfile(
            name="trend_regime_only",
            principle="Regime-aware trend trading",
            description="Binance momentum with directionality >= 0.20 and non-flat regime.",
            predicate=lambda c: c.source == "binance_momentum" and c.price <= 0.80 and (c.time_remaining_secs or 0) >= 180 and directionality(c) >= 0.20 and c.regime != "flat",
            ranker=lambda c: base_score(c) + directionality(c),
        ),
        StrategyProfile(
            name="discount_window_delta",
            principle="Late-entry value hunting",
            description="Window-delta candidates only when the binary price is still <= 0.70.",
            predicate=lambda c: c.source == "window_delta" and c.price <= 0.70,
            ranker=lambda c: base_score(c) + max(0.0, 0.75 - c.price),
        ),
        StrategyProfile(
            name="ensemble_ranked",
            principle="Cross-source quality ranking",
            description="Take the single highest-scoring candidate per epoch across momentum, window_delta, and snipe, with a hard price cap of 0.82.",
            predicate=lambda c: c.source in {"binance_momentum", "window_delta", "resolution_snipe"} and c.price <= 0.82 and (c.time_remaining_secs or 0) >= 60,
            ranker=base_score,
        ),
        StrategyProfile(
            name="late_certainty_snipe",
            principle="High-certainty late entry",
            description="Resolution snipes only, inside the last minute and below 0.95.",
            predicate=lambda c: c.source == "resolution_snipe" and c.price <= 0.95 and (c.time_remaining_secs or 999) < 60,
            ranker=lambda c: base_score(c) + 0.1,
        ),
    ]


def format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_money(value: float) -> str:
    return f"${value:.3f}"


def build_report(
    candidates: List[Candidate],
    resolutions: Dict[str, str],
    results: List[StrategyResult],
    date_from: Optional[float],
    date_to: Optional[float],
) -> str:
    lines = [
        "# BTC 5m Multi-Strategy Shadow Scan",
        "",
        f"- Generated: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        f"- Candidates scanned: `{len(candidates)}`",
        f"- Resolved epochs available: `{len(resolutions)}`",
        f"- Date range: `{datetime.fromtimestamp(date_from, tz=timezone.utc).isoformat() if date_from else 'unknown'}` -> `{datetime.fromtimestamp(date_to, tz=timezone.utc).isoformat() if date_to else 'unknown'}`",
        "- Simulation style: `1 share per chosen epoch, hold to resolution`",
        "- Net replay assumption: `0.10% fee on cost plus small per-source slippage penalty (window_delta/snipe > momentum)`",
        "- Note: `Still no partial fills, cancellation risk, or opposite-side price reconstruction included`",
        "",
        "## Strategy Grid",
        "",
    ]

    lines.extend([
        "| Strategy | Principle | Trades | Win Rate | Avg Net | Total Net | Net ROI | Total Gross | Gross ROI | Max DD | Trades/Day | Median Price | Median Time Left | R8 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for result in sorted(results, key=lambda row: (row.net_roi_on_cost, row.total_net_pnl), reverse=True):
        lines.append(
            "| {name} | {principle} | {trades} | {wr} | {avg_net} | {total_net} | {net_roi} | {total_gross} | {gross_roi} | {dd} | {tpd:.1f} | {price:.3f} | {secs:.0f}s | {r8} |".format(
                name=result.profile.name,
                principle=result.profile.principle,
                trades=result.trades,
                wr=format_pct(result.win_rate),
                avg_net=format_money(result.avg_net_pnl),
                total_net=format_money(result.total_net_pnl),
                net_roi=format_pct(result.net_roi_on_cost),
                total_gross=format_money(result.total_gross_pnl),
                gross_roi=format_pct(result.gross_roi_on_cost),
                dd=format_money(result.max_drawdown),
                tpd=result.trades_per_day,
                price=result.median_price,
                secs=result.median_time_remaining,
                r8=result.r8,
            )
        )

    top = sorted(results, key=lambda row: (row.net_roi_on_cost, row.total_net_pnl), reverse=True)[:3]
    lines.extend([
        "",
        "## Best Candidates",
        "",
    ])
    for result in top:
        lines.extend([
            f"### {result.profile.name}",
            f"- Principle: `{result.profile.principle}`",
            f"- Description: {result.profile.description}",
            f"- Result: `{result.trades}` trades, `{format_pct(result.win_rate)}` WR, `{format_money(result.total_net_pnl)}` total net PnL, `{format_pct(result.net_roi_on_cost)}` net ROI on deployed cost",
            f"- Trade mix: `{', '.join(result.top_sources) or 'none'}`",
            f"- Price buckets: `{', '.join(result.top_price_buckets) or 'none'}`",
            f"- Original statuses: `{', '.join(result.status_mix) or 'none'}`",
            "",
        ])

    lines.extend([
        "## Dataset Gaps",
        "",
        "- `epoch_coverage` is absent in both live snapshots, so this scan cannot score truly missed BTC 5m epochs that produced no logged signal at all.",
        "- Net replay is still approximate: fees/slippage are modeled heuristically, not from actual order-book fills.",
        "- Opposite-side prices are not stored per signal, so this scan cannot fairly simulate mean-reversion or side-flip strategies off the same order book.",
        "- There is no single terminal decision state per candidate across the whole history, so strategy attribution still depends on `status` plus stage logs rather than one canonical decision enum.",
        "- Config-era tagging is still incomplete, so this scan blends multiple live rule eras unless you filter by time range.",
        "",
        "## Recommendation",
        "",
        "Promote the top 1-2 strategies to a dedicated shadow comparison first. Do not treat the best gross backtest as production-ready until it survives fee-aware replay and current live shadow observation.",
    ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instances",
        nargs="+",
        default=["emmanuel", "polyphemus"],
        help="Instances to include from tools/.backtest_cache",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Lookback window in days",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown report output path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    all_candidates: List[Candidate] = []
    for instance in args.instances:
        all_candidates.extend(load_candidates(instance))

    if not all_candidates:
        print("No candidates found.", file=sys.stderr)
        return 1

    latest_epoch = max(candidate.epoch for candidate in all_candidates)
    cutoff = latest_epoch - (args.days * 86400)
    candidates = [candidate for candidate in all_candidates if candidate.epoch >= cutoff]
    resolutions = resolve_outcomes(candidate.slug for candidate in candidates)
    candidates = [candidate for candidate in candidates if candidate.slug in resolutions]

    results = [simulate_profile(profile, candidates, resolutions) for profile in profiles()]
    date_from = min((candidate.epoch for candidate in candidates), default=None)
    date_to = max((candidate.epoch for candidate in candidates), default=None)
    report = build_report(candidates, resolutions, results, date_from, date_to)

    output_path = args.output
    if output_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = ROOT.parent / "dario_output" / f"btc5m_strategy_shadow_scan_{stamp}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
