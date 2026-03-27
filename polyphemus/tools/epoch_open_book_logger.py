#!/usr/bin/env python3
"""Epoch-open order book logger.

Measures order book state at precise millisecond offsets after each BTC/SOL 5m
epoch opens. Answers two questions before we build epoch-open snipe:
  1. Is there sub-5c liquidity in the book at T+0 to T+500ms?
  2. How fast do market makers requote after epoch transition?

Standalone script - no polyphemus imports (Bug #39 safe).

Usage (on VPS):
    python3 /opt/lagbot/lagbot/tools/epoch_open_book_logger.py
    python3 /opt/lagbot/lagbot/tools/epoch_open_book_logger.py --epochs 20 --asset BTC

Output: /tmp/epoch_open_book_{timestamp}.csv + summary printed to stdout.
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LagbotResearch/1.0)",
    "Accept": "application/json",
}
WINDOW_SECS = 300  # 5m epoch

# Millisecond offsets after epoch open to snapshot
SNAPSHOT_OFFSETS_MS = [0, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000]


# ---------------------------------------------------------------------------
# Market discovery
# ---------------------------------------------------------------------------

def get_market_by_slug(slug: str) -> dict | None:
    """Fetch market token IDs from Gamma API by exact slug."""
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"slug": slug},
            headers=HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        market = (data[0] if isinstance(data, list) and data else data) or {}
        if not market:
            return None
        import json as _json
        token_ids = _json.loads(market.get("clobTokenIds", "[]"))
        outcomes = _json.loads(market.get("outcomes", "[]"))
        if len(token_ids) < 2 or len(outcomes) < 2:
            return None
        up_idx = outcomes.index("Up") if "Up" in outcomes else 0
        down_idx = 1 - up_idx
        return {
            "slug": slug,
            "token_up": token_ids[up_idx],
            "token_down": token_ids[down_idx],
        }
    except Exception as e:
        print(f"WARN gamma-api {slug}: {e}")
        return None


def get_5m_markets(asset: str) -> list:
    """Return current + next epoch market info by computing 300s boundaries."""
    now = int(time.time())
    current_start = (now // 300) * 300
    slugs = [
        f"{asset.lower()}-updown-5m-{current_start}",
        f"{asset.lower()}-updown-5m-{current_start + 300}",
    ]
    results = []
    for slug in slugs:
        m = get_market_by_slug(slug)
        if m:
            epoch_end = int(slug.rsplit("-", 1)[1]) + 300
            m["epoch_end"] = epoch_end
            m["tokens"] = [
                {"outcome": "Up", "token_id": m["token_up"]},
                {"outcome": "Down", "token_id": m["token_down"]},
            ]
            results.append(m)
    return results


def parse_epoch_start(slug: str) -> int | None:
    """Extract epoch start unix timestamp from slug like btc-updown-5m-1774373400."""
    try:
        return int(slug.rsplit("-", 1)[1])
    except Exception:
        return None


def next_5m_boundary() -> int:
    """Next unix timestamp on a 5-minute boundary."""
    now = int(time.time())
    return ((now // 300) + 1) * 300


# ---------------------------------------------------------------------------
# Book snapshot
# ---------------------------------------------------------------------------

def get_book_snapshot(token_id: str) -> dict:
    """REST book snapshot. Returns timing + liquidity stats."""
    t0 = time.perf_counter()
    try:
        r = requests.get(
            f"{CLOB_HOST}/book",
            params={"token_id": token_id},
            headers=HEADERS,
            timeout=5,
        )
        rtt_ms = round((time.perf_counter() - t0) * 1000, 1)
        r.raise_for_status()
        book = r.json()

        bids = sorted(book.get("bids", []), key=lambda x: float(x["price"]), reverse=True)
        asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))

        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 0.0
        mid = round((best_bid + best_ask) / 2, 4) if best_bid and best_ask else 0.0
        spread = round(best_ask - best_bid, 4) if best_bid and best_ask else 0.0

        # Cheap-side liquidity: any asks at <= $0.05
        cheap_ask_size = sum(float(a["size"]) for a in asks if float(a["price"]) <= 0.05)
        cheapest_ask = float(asks[0]["price"]) if asks else None

        return {
            "best_bid": round(best_bid, 4),
            "best_ask": round(best_ask, 4),
            "mid": mid,
            "spread": spread,
            "cheap_ask_size": round(cheap_ask_size, 2),
            "cheapest_ask": cheapest_ask,
            "n_bid_levels": len(bids),
            "n_ask_levels": len(asks),
            "total_bid_size": round(sum(float(b["size"]) for b in bids), 2),
            "total_ask_size": round(sum(float(a["size"]) for a in asks), 2),
            "rtt_ms": rtt_ms,
            "error": None,
        }
    except Exception as e:
        return {"error": str(e), "rtt_ms": round((time.perf_counter() - t0) * 1000, 1)}


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(asset: str = "BTC", n_epochs: int = 10, output_path: str | None = None):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = output_path or f"/tmp/epoch_open_book_{ts}.csv"

    print("=" * 60)
    print(f"EPOCH OPEN BOOK LOGGER | {asset} 5m | {n_epochs} epochs")
    print(f"Offsets (ms): {SNAPSHOT_OFFSETS_MS}")
    print(f"Output: {output_path}")
    print("=" * 60)

    all_rows = []
    epoch_count = 0

    while epoch_count < n_epochs:
        now = time.time()

        # --- Find the next epoch open and its token IDs ---
        markets = get_5m_markets(asset)
        if not markets:
            print("No markets found. Retrying in 10s...")
            time.sleep(10)
            continue

        # Find market whose epoch ends soonest (= next epoch opens then)
        candidates = []
        for m in markets:
            slug = m.get("slug", "")
            epoch_start = parse_epoch_start(slug)
            if not epoch_start:
                continue
            epoch_end = epoch_start + WINDOW_SECS
            if epoch_end <= now:
                continue  # already past
            tokens = m.get("tokens", [])
            candidates.append({
                "slug": slug,
                "epoch_end": epoch_end,
                "tokens": tokens,
            })

        if not candidates:
            # Fall back: use next 5-min boundary, no token lookup yet
            next_open = next_5m_boundary()
            wait = next_open - now
            print(f"No candidate found. Next 5m boundary in {wait:.0f}s. Sleeping...")
            time.sleep(min(wait - 1, 60))
            continue

        target = min(candidates, key=lambda x: x["epoch_end"])
        wait_secs = target["epoch_end"] - now

        if wait_secs > 310:
            print(f"Next open in {wait_secs:.0f}s. Sleeping 60s...")
            time.sleep(60)
            continue

        slug = target["slug"]
        epoch_open_ts = target["epoch_end"]
        tokens = target["tokens"]

        # Resolve token IDs
        token_up = next(
            (t.get("token_id") for t in tokens if "up" in t.get("outcome", "").lower()), None
        ) or (tokens[0].get("token_id") if tokens else None)
        token_down = next(
            (t.get("token_id") for t in tokens if "down" in t.get("outcome", "").lower()), None
        ) or (tokens[1].get("token_id") if len(tokens) > 1 else None)

        open_utc = datetime.fromtimestamp(epoch_open_ts, tz=timezone.utc).strftime("%H:%M:%S")
        print(f"\n[Epoch {epoch_count + 1}/{n_epochs}] {slug}")
        print(f"  Opens at {open_utc} UTC | {wait_secs:.1f}s away")
        print(f"  UP:   {str(token_up)[:20]}...")
        print(f"  DOWN: {str(token_down)[:20]}...")

        if not token_up and not token_down:
            print("  ERROR: no token IDs. Skipping.")
            time.sleep(10)
            continue

        # --- Sleep until 200ms before epoch open ---
        sleep_until = epoch_open_ts - 0.2
        remaining = sleep_until - time.time()
        if remaining > 0:
            time.sleep(remaining)

        # --- Snapshot at each offset ---
        for offset_ms in SNAPSHOT_OFFSETS_MS:
            target_wall = epoch_open_ts + offset_ms / 1000.0
            gap = target_wall - time.time()
            if gap > 0:
                time.sleep(gap)

            actual_offset_ms = round((time.time() - epoch_open_ts) * 1000, 1)

            for outcome, token_id in [("up", token_up), ("down", token_down)]:
                if not token_id:
                    continue
                snap = get_book_snapshot(token_id)
                row = {
                    "epoch": epoch_count + 1,
                    "slug": slug,
                    "epoch_open_ts": epoch_open_ts,
                    "target_offset_ms": offset_ms,
                    "actual_offset_ms": actual_offset_ms,
                    "outcome": outcome,
                    "token_id": token_id[:20],
                    **snap,
                }
                all_rows.append(row)

                cheap = snap.get("cheap_ask_size", 0) or 0
                mid = snap.get("mid", "?")
                spread = snap.get("spread", "?")
                cheapest = snap.get("cheapest_ask", "?")
                err = snap.get("error")
                if err:
                    print(f"  T+{actual_offset_ms:6.0f}ms {outcome:4s} | ERROR: {err}")
                else:
                    flag = " <-- CHEAP LIQUIDITY" if cheap > 0 else ""
                    print(
                        f"  T+{actual_offset_ms:6.0f}ms {outcome:4s} | "
                        f"bid={snap['best_bid']:.3f} ask={snap['best_ask']:.3f} "
                        f"spread={spread:.3f} cheapest={cheapest} "
                        f"cheap_ask={cheap:.1f}sh{flag}"
                    )

        epoch_count += 1
        time.sleep(2)  # avoid re-detecting same epoch

    # --- Write CSV ---
    if all_rows:
        fieldnames = [
            "epoch", "slug", "epoch_open_ts", "target_offset_ms", "actual_offset_ms",
            "outcome", "token_id", "best_bid", "best_ask", "mid", "spread",
            "cheap_ask_size", "cheapest_ask", "n_bid_levels", "n_ask_levels",
            "total_bid_size", "total_ask_size", "rtt_ms", "error",
        ]
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\nData written: {output_path} ({len(all_rows)} rows)")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    valid = [r for r in all_rows if not r.get("error") and r.get("spread") is not None]
    early = [r for r in valid if r["target_offset_ms"] <= 500]

    cheap_early = [r for r in early if (r.get("cheap_ask_size") or 0) > 0]
    if cheap_early:
        avg_cheap = sum(r["cheap_ask_size"] for r in cheap_early) / len(cheap_early)
        print(f"Sub-5c ask liquidity at T+0 to T+500ms:")
        print(f"  Found in {len(cheap_early)}/{len(early)} snapshots")
        print(f"  Avg size when present: {avg_cheap:.1f} shares")
        print(f"  --> EPOCH-OPEN SNIPE: WORTH INVESTIGATING")
    else:
        print(f"Sub-5c ask liquidity at T+0 to T+500ms: NONE (0/{len(early)} snapshots)")
        print(f"  --> EPOCH-OPEN SNIPE: NO EDGE (no cheap orders in book at open)")

    print(f"\nSpread tightening by offset (avg across all epochs + outcomes):")
    spread_by_offset: dict = {}
    for r in valid:
        key = r["target_offset_ms"]
        spread_by_offset.setdefault(key, []).append(r["spread"])
    for offset, spreads in sorted(spread_by_offset.items()):
        avg = sum(spreads) / len(spreads)
        print(f"  T+{offset:5d}ms: spread={avg:.4f} (n={len(spreads)})")

    print(f"\nAvg REST RTT: {sum(r['rtt_ms'] for r in valid) / len(valid):.1f}ms" if valid else "")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Epoch open order book logger")
    parser.add_argument("--asset", default="BTC", choices=["BTC", "SOL", "ETH"])
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    run(asset=args.asset, n_epochs=args.epochs, output_path=args.output)
