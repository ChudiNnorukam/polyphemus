#!/usr/bin/env python3
"""Phase 2 baseline calibration check for pwin-estimator-binance.

Reads `data/pwin_features.csv` (Phase 1 output from build_pwin_dataset.py),
filters to the full-feature cohort (rows with momentum_pct + taker_delta +
vpin_5m all non-null), bins `entry_price` into 10 equal-width bins over
[0.0, 1.0], computes observed win rate per bin with Wilson 95% confidence
interval, and writes:

    docs/research/pwin_baseline_calibration.csv
    docs/research/pwin_baseline_calibration.png

The CSV is the authoritative table; the PNG is the reliability diagram for
human inspection. The PNG overlays y=x (perfect calibration) so any
miscalibration at a band reads off directly.

Stdlib + matplotlib only. Node acceptance criteria (per
docs/codex/nodes/pwin-estimator-binance.md §Phase 2):
  1. Plot persisted at docs/research/pwin_baseline_calibration.png.
  2. Calibration table persisted alongside as CSV.
  3. One-paragraph verdict printed to stdout: entry_price is/is-not a
     calibrated probability on this sample.

Run locally:
  cd /Users/chudinnorukam/Projects/business/polyphemus
  python3 tools/baseline_calibration.py
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless render
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
CSV_IN = REPO / "data" / "pwin_features.csv"
OUT_DIR = REPO / "docs" / "research"
CSV_OUT = OUT_DIR / "pwin_baseline_calibration.csv"
PNG_OUT = OUT_DIR / "pwin_baseline_calibration.png"

N_BINS = 10  # equal-width over [0.0, 1.0]
Z = 1.96  # 95% CI


def wilson_interval(k: int, n: int, z: float = Z) -> tuple[float, float, float]:
    """Return (center_point_estimate, lower, upper) Wilson 95% CI."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = max(0.0, (center - spread) / denom)
    hi = min(1.0, (center + spread) / denom)
    return p, lo, hi


def load_rows(path: Path) -> list[dict]:
    with path.open("r", newline="") as f:
        return list(csv.DictReader(f))


def full_feature_cohort(rows: list[dict]) -> list[dict]:
    """Keep rows where all three Binance features are non-null+non-empty."""
    kept = []
    for r in rows:
        if not r.get("y") or r["y"] == "":
            continue
        mom = r.get("binance_momentum_60s_pct", "")
        td = r.get("taker_delta_60s", "")
        vp = r.get("vpin_300s", "")
        if mom and td and vp:
            kept.append(r)
    return kept


def bin_index(price: float, n_bins: int = N_BINS) -> int:
    if price < 0.0:
        return 0
    if price >= 1.0:
        return n_bins - 1
    return min(n_bins - 1, int(price * n_bins))


def bin_label(idx: int, n_bins: int = N_BINS) -> str:
    lo = idx / n_bins
    hi = (idx + 1) / n_bins
    return f"{lo:.1f}-{hi:.1f}"


def bin_midpoint(idx: int, n_bins: int = N_BINS) -> float:
    return (idx + 0.5) / n_bins


def build_calibration_table(rows: list[dict], label: str) -> list[dict]:
    """Return one row per bin: bin, midpoint, n, wins, wr, wilson_lo, wilson_hi."""
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(N_BINS)]
    for r in rows:
        try:
            price = float(r["entry_price"])
            y = int(r["y"])
        except (ValueError, KeyError):
            continue
        buckets[bin_index(price)].append((price, y))

    table = []
    for idx, bucket in enumerate(buckets):
        n = len(bucket)
        wins = sum(y for _, y in bucket)
        wr, lo, hi = wilson_interval(wins, n)
        mid = bin_midpoint(idx)
        mean_price = sum(p for p, _ in bucket) / n if n else float("nan")
        # gap vs y=x: positive = bin over-performs its price (market under-priced)
        gap = (wr - mid) if n else float("nan")
        table.append({
            "cohort": label,
            "bin": bin_label(idx),
            "bin_midpoint": mid,
            "mean_price_in_bin": mean_price,
            "n": n,
            "wins": wins,
            "wr": wr,
            "wilson_lo": lo,
            "wilson_hi": hi,
            "gap_vs_midpoint": gap,
        })
    return table


def write_csv(all_tables: list[dict], path: Path) -> None:
    fields = [
        "cohort", "bin", "bin_midpoint", "mean_price_in_bin",
        "n", "wins", "wr", "wilson_lo", "wilson_hi", "gap_vs_midpoint",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in all_tables:
            out = dict(row)
            # round floats for readability
            for k in ("bin_midpoint", "mean_price_in_bin", "wr",
                       "wilson_lo", "wilson_hi", "gap_vs_midpoint"):
                v = out[k]
                if isinstance(v, float) and not math.isnan(v):
                    out[k] = round(v, 4)
            w.writerow(out)


def plot_calibration(tables: dict[str, list[dict]], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 8))

    # y=x perfect calibration line
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray", alpha=0.6,
            label="y=x (perfect calibration)")

    colors = {"full_707": "#1f77b4", "feature_cohort": "#d62728"}
    markers = {"full_707": "o", "feature_cohort": "s"}

    for label, table in tables.items():
        xs, ys, los, his, ns = [], [], [], [], []
        for row in table:
            if row["n"] == 0:
                continue
            xs.append(row["bin_midpoint"])
            ys.append(row["wr"])
            los.append(row["wr"] - row["wilson_lo"])
            his.append(row["wilson_hi"] - row["wr"])
            ns.append(row["n"])
        if not xs:
            continue
        color = colors.get(label, "black")
        marker = markers.get(label, "o")
        ax.errorbar(
            xs, ys, yerr=[los, his], fmt=marker, color=color,
            ecolor=color, elinewidth=1.2, capsize=3, markersize=8,
            label=f"{label} (n per bin shown)",
        )
        # annotate n alongside each point
        for x, y, n in zip(xs, ys, ns):
            ax.annotate(
                f"n={n}", xy=(x, y), xytext=(6, 6),
                textcoords="offset points", fontsize=8, color=color,
            )

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("entry_price (bin midpoint)")
    ax.set_ylabel("observed win rate (WR)")
    ax.set_title(
        "Polymarket entry_price vs observed WR\n"
        "pwin-estimator-binance §Phase 2 baseline calibration"
    )
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def verdict_sentence(table: list[dict], label: str) -> str:
    """Summarize the table into one line per cohort."""
    populated = [r for r in table if r["n"] > 0]
    if not populated:
        return f"{label}: NO DATA."
    total_n = sum(r["n"] for r in populated)
    # Weighted mean absolute gap
    wgap = sum(abs(r["gap_vs_midpoint"]) * r["n"] for r in populated) / total_n
    # Bins where Wilson CI entirely misses y=x
    misses = [
        r for r in populated
        if r["wilson_hi"] < r["bin_midpoint"] or r["wilson_lo"] > r["bin_midpoint"]
    ]
    # Focus on the 93-97 area — bin 0.9-1.0 is the only one fully above 0.9
    deep_fav = next((r for r in populated if r["bin"] == "0.9-1.0"), None)
    deep_note = ""
    if deep_fav and deep_fav["n"] >= 10:
        mid = deep_fav["bin_midpoint"]
        gap = deep_fav["gap_vs_midpoint"]
        ci_miss = deep_fav["wilson_hi"] < mid
        deep_note = (
            f" Deep-favorite (0.9-1.0, n={deep_fav['n']}): "
            f"WR={deep_fav['wr']:.3f} vs midpoint {mid:.2f}, "
            f"gap {gap:+.3f}, Wilson CI "
            f"[{deep_fav['wilson_lo']:.3f},{deep_fav['wilson_hi']:.3f}]"
            f"{' — CI EXCLUDES y=x (miscalibrated low)' if ci_miss else ''}."
        )
    return (
        f"{label}: n={total_n}, weighted mean |gap| = {wgap:.3f}, "
        f"{len(misses)}/{len(populated)} bins have Wilson CI excluding y=x.{deep_note}"
    )


def main() -> int:
    if not CSV_IN.exists():
        print(f"MISSING: {CSV_IN}", file=sys.stderr)
        return 2

    rows = load_rows(CSV_IN)
    print(f"loaded {len(rows)} rows from {CSV_IN.relative_to(REPO)}")

    # Cohort A: full labelled set (entry_price exists + y defined)
    labelled = [r for r in rows if r.get("y") not in ("", None) and r.get("entry_price")]
    print(f"full_707 cohort (all labelled rows): n={len(labelled)}")

    # Cohort B: Binance-feature cohort (momentum+taker_delta+vpin all non-null)
    cohort = full_feature_cohort(rows)
    print(f"feature_cohort (3 Binance features non-null): n={len(cohort)}")

    table_full = build_calibration_table(labelled, "full_707")
    table_feat = build_calibration_table(cohort, "feature_cohort")

    write_csv(table_full + table_feat, CSV_OUT)
    plot_calibration({"full_707": table_full, "feature_cohort": table_feat}, PNG_OUT)

    print(f"\nwrote {CSV_OUT.relative_to(REPO)}")
    print(f"wrote {PNG_OUT.relative_to(REPO)}")

    print("\n=== Per-bin table (feature_cohort) ===")
    print(f"{'bin':<10}{'n':>5}{'wins':>6}{'wr':>8}{'wlo':>8}{'whi':>8}{'gap':>8}")
    for r in table_feat:
        if r["n"] == 0:
            continue
        print(
            f"{r['bin']:<10}{r['n']:>5}{r['wins']:>6}"
            f"{r['wr']:>8.3f}{r['wilson_lo']:>8.3f}{r['wilson_hi']:>8.3f}"
            f"{r['gap_vs_midpoint']:>+8.3f}"
        )

    print("\n=== Per-bin table (full_707) ===")
    print(f"{'bin':<10}{'n':>5}{'wins':>6}{'wr':>8}{'wlo':>8}{'whi':>8}{'gap':>8}")
    for r in table_full:
        if r["n"] == 0:
            continue
        print(
            f"{r['bin']:<10}{r['n']:>5}{r['wins']:>6}"
            f"{r['wr']:>8.3f}{r['wilson_lo']:>8.3f}{r['wilson_hi']:>8.3f}"
            f"{r['gap_vs_midpoint']:>+8.3f}"
        )

    print("\n=== Summary ===")
    print(verdict_sentence(table_feat, "feature_cohort"))
    print(verdict_sentence(table_full, "full_707"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
