#!/usr/bin/env python3
"""Walk-forward validation for the two survivor (band, signal_source) pairs
surfaced in the 2026-04-22 entry-band verdict:

  1) 55-70 band + flat_regime_rtds  (n=39, 100% WR in-sample)
  2) 70-85 band + binance_momentum  (n=41, 97.6% WR in-sample)

Method:
  - Filter to the same resolved-only universe as verdict_all_bands.py.
  - Sort by entry_time ASC.
  - Split at the median entry_time (first half = training, second half = OOS).
  - Per half: n, wins, losses, raw WR, Wilson LB (95%), PnL, avg_win, avg_loss.
  - Breakeven = band midpoint (Polymarket maker fee = 0).

Classification per P5 (sample-size + Wilson LB):
  - CONFIRMED  : both halves n>=20 AND both halves Wilson LB >= breakeven
  - LOOKAHEAD  : first half clears, second half Wilson LB < breakeven
  - UNDERPOWERED : n/half < 20 (e.g. 39 / 2 = 19 rows per half)
  - DEGRADED   : WR drops >=15 pp between halves even if Wilson LB still clears

Read-only, stdlib only. Run on emmanuel (where the verdict data lives):
  scp tools/walk_forward_survivors.py root@82.24.19.114:/tmp/
  ssh root@82.24.19.114 '/opt/lagbot/venv/bin/python3 /tmp/walk_forward_survivors.py'
"""
import math
import sqlite3
import sys
from datetime import datetime, timezone

DB = "/opt/lagbot/instances/emmanuel/data/performance.db"

SURVIVORS = [
    # (label,   band_lo, band_hi,  signal_source)
    ("55-70 + flat_regime_rtds",  0.55, 0.70, "flat_regime_rtds"),
    ("70-85 + binance_momentum",  0.70, 0.85, "binance_momentum"),
]

MIN_HALF = 20  # P5 halving threshold


def wilson_lower(p: float, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    denom = 1 + z*z/n
    center = p + z*z/(2*n)
    spread = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return max(0.0, (center - spread) / denom)


def fmt_iso(ts) -> str:
    # entry_time stored as unix seconds in polyphemus schema
    if ts is None:
        return "<null>"
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="minutes")
    except (TypeError, ValueError, OSError):
        return str(ts)


def summarize(rows):
    n = len(rows)
    if n == 0:
        return {"n": 0, "w": 0, "l": 0, "wr": 0.0, "wilson": 0.0,
                "pnl": 0.0, "avg_w": 0.0, "avg_l": 0.0,
                "t_lo": None, "t_hi": None}
    wins = [r[1] for r in rows if r[1] > 0]
    losses = [r[1] for r in rows if r[1] <= 0]
    w = len(wins)
    l = len(losses)
    wr = w / n
    return {
        "n": n,
        "w": w,
        "l": l,
        "wr": wr,
        "wilson": wilson_lower(wr, n),
        "pnl": sum(r[1] for r in rows),
        "avg_w": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_l": (sum(losses) / len(losses)) if losses else 0.0,
        "t_lo": rows[0][0],
        "t_hi": rows[-1][0],
    }


def classify(first: dict, second: dict, breakeven: float) -> str:
    if min(first["n"], second["n"]) < MIN_HALF:
        return "UNDERPOWERED"
    if second["wilson"] < breakeven:
        return "LOOKAHEAD" if first["wilson"] >= breakeven else "NO-EDGE-EITHER-HALF"
    if first["wr"] - second["wr"] >= 0.15:
        return "DEGRADED"
    return "CONFIRMED"


def main():
    conn = sqlite3.connect(DB)
    print(f"walk-forward survivors test | db={DB} | min_half={MIN_HALF}\n")

    for label, lo, hi, src in SURVIVORS:
        mid = (lo + hi) / 2
        be = mid  # breakeven WR at midpoint (fee=0)

        rows = conn.execute("""
            SELECT entry_time, pnl
            FROM trades
            WHERE exit_reason IN ('market_resolved','phantom_resolved')
              AND pnl IS NOT NULL
              AND entry_price >= ? AND entry_price < ?
              AND signal_source = ?
              AND entry_time IS NOT NULL
            ORDER BY entry_time ASC
        """, (lo, hi, src)).fetchall()

        if not rows:
            print(f"=== {label} === NO ROWS\n")
            continue

        n = len(rows)
        half = n // 2
        first = summarize(rows[:half])
        second = summarize(rows[half:])
        verdict = classify(first, second, be)

        print(f"=== {label} === n={n}  breakeven={be:.3f}  verdict={verdict}")
        print(f"  t_span: {fmt_iso(rows[0][0])}  →  {fmt_iso(rows[-1][0])}")
        print(f"  split_at: {fmt_iso(rows[half-1][0])} / {fmt_iso(rows[half][0])}"
              f"  (idx {half-1}/{half})")

        def line(tag, s):
            print(f"  {tag:<7}  n={s['n']:>3}  W={s['w']:>3}  L={s['l']:>3}  "
                  f"WR={s['wr']:.3f}  Wilson={s['wilson']:.3f}  "
                  f"PnL={s['pnl']:+.2f}  avgW={s['avg_w']:+.2f}  avgL={s['avg_l']:+.2f}")

        line("FIRST",  first)
        line("SECOND", second)

        # interpretation pointer
        if verdict == "CONFIRMED":
            print(f"  → P5 PASS. Edge survives time-split.")
        elif verdict == "DEGRADED":
            print(f"  → WR dropped {(first['wr']-second['wr'])*100:.1f} pp across halves. "
                  f"Still above breakeven but showing decay.")
        elif verdict == "LOOKAHEAD":
            print(f"  → First half carried the in-sample finding. Second half "
                  f"Wilson LB {second['wilson']:.3f} below breakeven {be:.3f}. "
                  f"Treat as overfit.")
        elif verdict == "NO-EDGE-EITHER-HALF":
            print(f"  → Neither half clears breakeven on Wilson LB. "
                  f"In-sample win rate was noise.")
        elif verdict == "UNDERPOWERED":
            print(f"  → n/half < {MIN_HALF}. Wilson LB has too much variance "
                  f"to verdict. Gather more trades before trusting.")

        print()

    conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
