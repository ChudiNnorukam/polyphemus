"""
Per-band efficiency verdict for emmanuel's trades.

Applies P5 rules (n>=30 + Wilson LB) to each entry_band. Splits live (is_dry_run=0)
vs dry-run (is_dry_run=1) because live data is ground truth; phantom_resolved
data is still valuable but subject to simulated-fill bias.

Reports per band:
  - n, wins, losses, raw WR
  - Wilson lower bound (95%)
  - Raw PnL, avg_win, avg_loss
  - Breakeven WR (at band midpoint)
  - Gap = raw_WR - breakeven_WR
  - Verdict per P5: UNDECIDABLE (n<30), DEFENSIBLE (Wilson LB >= breakeven),
                    FAILED (Wilson LB < breakeven), EXPLORE (n>=30 but mixed evidence)

Read-only, stdlib only.
"""
import math
import sqlite3
import sys

DB = "/opt/lagbot/instances/emmanuel/data/performance.db"

BANDS = [
    ("00-55",  0.00, 0.55),
    ("55-70",  0.55, 0.70),
    ("70-85",  0.70, 0.85),
    ("85-93",  0.85, 0.93),
    ("93-97",  0.93, 0.97),
    ("97+",    0.97, 1.01),
]


def wilson_lower(p: float, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    denom = 1 + z*z/n
    center = p + z*z/(2*n)
    spread = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return max(0.0, (center - spread) / denom)


def breakeven_wr(mid_price: float) -> float:
    # Long: pay mid_price to win (1-mid_price). Breakeven: p * (1-mid) = (1-p) * mid
    # → p = mid
    return mid_price


def verdict_band(label: str, lo: float, hi: float, conn) -> dict:
    mid = (lo + hi) / 2
    be = breakeven_wr(mid)

    rows = conn.execute("""
        SELECT pnl, is_dry_run, exit_reason, signal_source, entry_price
        FROM trades
        WHERE entry_price >= ? AND entry_price < ?
          AND exit_reason IN ('market_resolved', 'phantom_resolved')
          AND pnl IS NOT NULL
    """, (lo, hi)).fetchall()

    n = len(rows)
    if n == 0:
        return {"label": label, "n": 0, "verdict": "NO_DATA"}

    wins_pnl = [r["pnl"] for r in rows if r["pnl"] > 0]
    losses_pnl = [r["pnl"] for r in rows if r["pnl"] <= 0]
    wins = len(wins_pnl)
    losses = n - wins
    raw_wr = wins / n
    wlb = wilson_lower(raw_wr, n)
    pnl_total = sum(r["pnl"] for r in rows)
    avg_win = sum(wins_pnl) / wins if wins else 0.0
    avg_loss = sum(losses_pnl) / losses if losses else 0.0

    # Splits
    live = [r for r in rows if r["is_dry_run"] == 0]
    dry = [r for r in rows if r["is_dry_run"] == 1]
    live_n = len(live)
    live_wins = sum(1 for r in live if r["pnl"] > 0)
    live_wr = live_wins / live_n if live_n else 0.0
    live_pnl = sum(r["pnl"] for r in live)
    dry_n = len(dry)
    dry_wins = sum(1 for r in dry if r["pnl"] > 0)
    dry_wr = dry_wins / dry_n if dry_n else 0.0
    dry_pnl = sum(r["pnl"] for r in dry)

    # P5 verdict
    if n < 30:
        verdict = "UNDECIDABLE"
        note = f"n={n} < 30"
    elif wlb >= be and pnl_total > 0:
        verdict = "DEFENSIBLE"
        note = f"Wilson LB {wlb:.3f} >= breakeven {be:.3f}, PnL +${pnl_total:.0f}"
    elif wlb < be:
        verdict = "FAILED"
        note = f"Wilson LB {wlb:.3f} < breakeven {be:.3f} (gap {(wlb-be):+.3f})"
    elif pnl_total <= 0:
        verdict = "AMBIGUOUS"
        note = f"WR OK (Wilson {wlb:.3f} >= {be:.3f}) but PnL ${pnl_total:+.0f} — avg_loss dominates"
    else:
        verdict = "UNKNOWN"
        note = "unhandled branch"

    return {
        "label": label, "mid": mid, "be": be, "n": n, "wins": wins, "losses": losses,
        "raw_wr": raw_wr, "wilson_lb": wlb, "pnl": pnl_total,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "live_n": live_n, "live_wr": live_wr, "live_pnl": live_pnl,
        "dry_n": dry_n, "dry_wr": dry_wr, "dry_pnl": dry_pnl,
        "verdict": verdict, "note": note,
    }


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print(f"{'band':>6} {'mid':>5} {'BE':>5} {'n':>4} {'W':>3} {'L':>3} "
          f"{'raw_WR':>7} {'Wilson':>7} {'PnL':>10} {'avg_W':>7} {'avg_L':>7} "
          f"{'verdict':>13}")
    print("-" * 105)

    results = []
    for label, lo, hi in BANDS:
        r = verdict_band(label, lo, hi, conn)
        results.append(r)
        if r.get("n", 0) == 0:
            print(f"{label:>6} {'—':>5} {'—':>5} {'0':>4} {'—':>3} {'—':>3} "
                  f"{'—':>7} {'—':>7} {'$0':>10} {'—':>7} {'—':>7} {'NO_DATA':>13}")
            continue
        print(f"{r['label']:>6} {r['mid']:>5.3f} {r['be']:>5.3f} {r['n']:>4d} "
              f"{r['wins']:>3d} {r['losses']:>3d} "
              f"{r['raw_wr']:>7.3f} {r['wilson_lb']:>7.3f} "
              f"{'$'+format(r['pnl'],'+.0f'):>10} "
              f"{'$'+format(r['avg_win'],'+.2f'):>7} "
              f"{'$'+format(r['avg_loss'],'+.2f'):>7} "
              f"{r['verdict']:>13}")

    print()
    print("Live vs dry-run split (live = is_dry_run=0):")
    print(f"{'band':>6} {'live_n':>7} {'live_WR':>8} {'live_PnL':>10} "
          f"{'dry_n':>6} {'dry_WR':>7} {'dry_PnL':>10}")
    print("-" * 70)
    for r in results:
        if r.get("n", 0) == 0:
            continue
        print(f"{r['label']:>6} {r['live_n']:>7d} {r['live_wr']:>8.3f} "
              f"{'$'+format(r['live_pnl'],'+.0f'):>10} "
              f"{r['dry_n']:>6d} {r['dry_wr']:>7.3f} "
              f"{'$'+format(r['dry_pnl'],'+.0f'):>10}")

    print()
    print("Verdict narrative:")
    for r in results:
        if r.get("n", 0) == 0:
            print(f"  {r['label']}: NO DATA")
            continue
        print(f"  {r['label']}: {r['verdict']} — {r['note']}")

    # Special diagnostic for AMBIGUOUS bands (WR high but PnL negative)
    print()
    print("AMBIGUOUS bands — per-band sample of losers (avg_loss dominates):")
    for r in results:
        if r.get("verdict") != "AMBIGUOUS":
            continue
        label = r["label"]
        lo = next(b[1] for b in BANDS if b[0] == label)
        hi = next(b[2] for b in BANDS if b[0] == label)
        print(f"\n  {label}:")
        losers = conn.execute("""
            SELECT trade_id, entry_price, pnl, exit_reason, signal_source, is_dry_run
            FROM trades
            WHERE entry_price >= ? AND entry_price < ?
              AND exit_reason IN ('market_resolved', 'phantom_resolved')
              AND pnl < 0
            ORDER BY pnl ASC
            LIMIT 5
        """, (lo, hi)).fetchall()
        for L in losers:
            print(f"    {L['trade_id'][:12]}... entry={L['entry_price']:.3f} "
                  f"pnl={L['pnl']:+.2f} source={L['signal_source'] or '<null>':16s} "
                  f"is_dry_run={L['is_dry_run']}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
