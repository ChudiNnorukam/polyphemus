"""Run fason2 re-analysis with Binance kline resolution proxy.

Resolves the 3,347 pending BTC 5m trades, then runs full decomposition + stats.
"""

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import aiohttp


DB_PATH = ".omc/trader-analysis/0x02b3ff/trades.db"
SOURCE_TRADER = "0x02b3ffa8200feaf6263b88b69b70947cd20af446"
BINANCE_KLINE_URL = "https://api.binance.us/api/v3/klines"


def migrate_schema(db_path: str):
    """Add maker_address and size_unit columns if missing."""
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trader_trades)").fetchall()]

    if "maker_address" not in cols:
        conn.execute("ALTER TABLE trader_trades ADD COLUMN maker_address TEXT DEFAULT ''")
        print("Added maker_address column")

    if "size_unit" not in cols:
        conn.execute("ALTER TABLE trader_trades ADD COLUMN size_unit TEXT DEFAULT 'unknown'")
        print("Added size_unit column")

    conn.commit()
    conn.close()


async def resolve_via_binance(session: aiohttp.ClientSession,
                               asset: str, epoch_start_ts: float,
                               window_secs: int = 300) -> str | None:
    """Fetch Binance kline and determine UP/DOWN resolution."""
    symbol_map = {
        "BTC": "BTCUSDT", "ETH": "ETHUSDT",
        "SOL": "SOLUSDT", "XRP": "XRPUSDT",
    }
    symbol = symbol_map.get(asset.upper())
    if not symbol:
        return None

    start_ms = int(epoch_start_ts * 1000)
    end_ms = int((epoch_start_ts + window_secs) * 1000)

    try:
        async with session.get(
            BINANCE_KLINE_URL,
            params={
                "symbol": symbol,
                "interval": "5m",
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": "1",
            },
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            kline = data[0]
            open_price = float(kline[1])
            close_price = float(kline[4])

            if close_price > open_price:
                return "Up"
            elif close_price < open_price:
                return "Down"
            else:
                return None  # Flat - can't determine
    except Exception as e:
        print(f"  Error resolving {asset} epoch {epoch_start_ts}: {e}")
        return None


async def build_resolution_cache(db_path: str):
    """Resolve all pending crypto-updown trades via Binance klines."""
    conn = sqlite3.connect(db_path)

    # Get unique (asset, epoch_start) pairs for pending trades
    rows = conn.execute("""
        SELECT DISTINCT asset, slug FROM trader_trades
        WHERE resolution = 'PENDING' AND market_type IN ('updown_5m', 'updown_15m')
        AND asset != ''
    """).fetchall()

    # Parse epoch start from slug: "btc-updown-5m-{epoch_start_ts}"
    epochs = {}
    for asset, slug in rows:
        parts = slug.split("-")
        try:
            epoch_ts = float(parts[-1])
            key = (asset, epoch_ts)
            if key not in epochs:
                epochs[key] = slug
        except (ValueError, IndexError):
            continue

    print(f"Unique epochs to resolve: {len(epochs)}")

    # Ensure resolution_cache table exists
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS resolution_cache (
            market_slug TEXT NOT NULL,
            epoch_start INTEGER NOT NULL,
            resolved_direction TEXT NOT NULL,
            resolution_source TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (market_slug, epoch_start)
        );
    """)

    # Check what's already cached
    cached = set()
    for row in conn.execute("SELECT market_slug, epoch_start FROM resolution_cache").fetchall():
        cached.add((row[0], row[1]))

    to_resolve = [(asset, ts, slug) for (asset, ts), slug in epochs.items()
                  if (slug, int(ts)) not in cached]

    print(f"Already cached: {len(cached)}, need to resolve: {len(to_resolve)}")

    if not to_resolve:
        print("All epochs already cached!")
        conn.close()
        return

    resolved_count = 0
    flat_count = 0
    error_count = 0
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    async with aiohttp.ClientSession(
        headers={"Accept-Encoding": "gzip, deflate"}
    ) as session:
        # Process in batches to respect rate limits
        batch_size = 5
        for i in range(0, len(to_resolve), batch_size):
            batch = to_resolve[i:i + batch_size]
            tasks = []
            for asset, ts, slug in batch:
                window = 300 if "5m" in slug else 900
                tasks.append(resolve_via_binance(session, asset, ts, window))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for (asset, ts, slug), result in zip(batch, results):
                if isinstance(result, Exception):
                    error_count += 1
                    continue
                if result is None:
                    flat_count += 1
                    continue

                # Cache the resolution
                conn.execute(
                    """INSERT OR REPLACE INTO resolution_cache
                       (market_slug, epoch_start, resolved_direction, resolution_source, cached_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (slug, int(ts), result, "binance_kline", now_iso),
                )

                # Update all trades for this slug
                conn.execute(
                    "UPDATE trader_trades SET resolution = ? WHERE slug = ? AND resolution = 'PENDING'",
                    (result, slug),
                )
                resolved_count += 1

            conn.commit()

            # Rate limit: ~10 req/s, 5 per batch
            if i + batch_size < len(to_resolve):
                await asyncio.sleep(0.5)

            # Progress
            done = i + len(batch)
            if done % 100 == 0 or done == len(to_resolve):
                print(f"  Progress: {done}/{len(to_resolve)} epochs "
                      f"({resolved_count} resolved, {flat_count} flat, {error_count} errors)")

    conn.close()
    print(f"\nResolution complete: {resolved_count} resolved, {flat_count} flat, {error_count} errors")


def run_analysis(db_path: str, source_trader: str) -> dict:
    """Run full decomposition + stats pipeline."""
    from polyphemus.tools.trader_decompose import (
        compute_profile,
        extract_entry_price_distribution,
        extract_timing_pattern,
        extract_asset_preference,
        extract_direction_bias,
        extract_sizing_pattern,
        infer_order_type,
        extract_regime_conditioning,
        infer_exit_pattern,
        generate_replica,
        render_env_template,
    )
    from polyphemus.tools.trader_stats import (
        hypothesis_test_wr,
        deflated_sharpe,
        kelly_criterion,
        walk_forward_cv,
        acf_analysis,
    )
    from polyphemus.tools.trader_ingest import resolution_health

    # Resolution health check
    health = resolution_health(db_path, source_trader)
    print(f"\nResolution health: {health['resolved']}/{health['total_trades']} "
          f"({health['resolution_rate_pct']}%), healthy={health['healthy']}")

    # Profile
    profile = compute_profile(db_path, source_trader)
    print(f"\nProfile: {profile['wins']}W/{profile['losses']}L "
          f"({profile['wr']*100:.1f}% WR), est P&L: ${profile['est_pnl']:.2f}")
    print(f"  P&L reliable: {profile['pnl_reliable']}")
    print(f"  Wilson CI: [{profile['wilson_ci'][0]*100:.1f}%, {profile['wilson_ci'][1]*100:.1f}%]")
    print(f"  Bayesian WR: {profile['bayesian_wr']*100:.1f}%")
    print(f"  R8 label: {profile['r8_label']}")
    print(f"  Provisional: {profile['is_provisional']} ({profile['provisional_pct']}% pending)")

    # 8-dimension decomposition
    print("\n--- 8-Dimension Decomposition ---")

    entry = extract_entry_price_distribution(db_path, source_trader)
    print(f"1. Entry: mode={entry.get('entry_mode')}, "
          f"range=[{entry.get('min', 0):.2f}, {entry.get('max', 0):.2f}], "
          f"median={entry.get('median', 0):.2f}")

    timing = extract_timing_pattern(db_path, source_trader)
    print(f"2. Timing: mode={timing.get('timing_mode')}, "
          f"mean_offset={timing.get('mean_offset_secs', 0):.0f}s")

    asset_pref = extract_asset_preference(db_path, source_trader)
    print(f"3. Asset: {asset_pref.get('asset_counts', {})}")

    direction = extract_direction_bias(db_path, source_trader)
    print(f"4. Direction: bias={direction.get('bias')}")

    sizing = extract_sizing_pattern(db_path, source_trader)
    print(f"5. Sizing: mean={sizing.get('mean_size', 0):.1f}")

    order_type = infer_order_type(db_path, source_trader)
    print(f"6. Order type: {order_type.get('dominant_type')} "
          f"(reliable={order_type.get('reliable')}, "
          f"ground_truth={order_type.get('ground_truth_pct', 0)*100:.0f}%)")

    regime = extract_regime_conditioning(db_path, source_trader)
    print(f"7. Regime: worst_hours={regime.get('worst_hours', [])}")

    exit_pat = infer_exit_pattern(db_path, source_trader)
    print(f"8. Exit: mode={exit_pat.get('exit_mode')}")

    # Statistical edge tests
    print("\n--- Statistical Edge Tests ---")

    if profile["resolved_trades"] > 0:
        # Test 1: Hypothesis test
        ht = hypothesis_test_wr(
            wins=profile["wins"],
            total=profile["resolved_trades"],
            breakeven=0.50,
        )
        print(f"Z-test: p={ht['p_value']:.4f}, significant={ht['significant']}, "
              f"effect={ht['effect_magnitude']}")

        # Test 2: DSR
        returns = []
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        resolved_trades = conn.execute(
            """SELECT price, size, resolution, outcome FROM trader_trades
               WHERE source_trader = ? AND resolution NOT IN ('PENDING', 'UNKNOWN')
               AND TRIM(market_type) IN ('updown_5m', 'updown_15m')
               AND side IN ('BUY', 'SELL') AND price > 0""",
            (source_trader,),
        ).fetchall()
        conn.close()

        for t in resolved_trades:
            p = t["price"] or 0
            s = t["size"] or 0
            is_win = (t["outcome"] or "").lower() == (t["resolution"] or "").lower()
            if is_win:
                returns.append((1 - p) * s)
            else:
                returns.append(-p * s)

        if len(returns) >= 10:
            dsr = deflated_sharpe(returns, k=1)
            print(f"DSR: sharpe={dsr.get('sharpe_hat', 0):.4f}, "
                  f"dsr={dsr.get('dsr_value', 0):.3f}, "
                  f"overfit_risk={dsr.get('overfit_risk')}")

        # Test 3: Kelly
        kelly = kelly_criterion(
            win_rate=profile["wr"],
            avg_win=sum(r for r in returns if r > 0) / max(sum(1 for r in returns if r > 0), 1),
            avg_loss=abs(sum(r for r in returns if r < 0) / max(sum(1 for r in returns if r < 0), 1)),
        )
        print(f"Kelly: full={kelly.get('full_kelly', 0)*100:.1f}%, "
              f"half={kelly.get('half_kelly', 0)*100:.1f}%, "
              f"recommended_pct={kelly.get('recommended_bet_pct', 0)*100:.1f}%")

        # Test 4: Walk-forward CV
        if profile["resolved_trades"] >= 50:
            outcomes = [1 if (t["outcome"] or "").lower() == (t["resolution"] or "").lower() else 0
                       for t in resolved_trades]
            wf = walk_forward_cv(outcomes, n_splits=5)
            print(f"Walk-forward: mean_test_wr={wf.get('mean_test_wr', 0)*100:.1f}%, "
                  f"splits_positive={wf.get('splits_positive', 0)}/5, "
                  f"consistent={wf.get('consistent')}")

        # Test 5: ACF
        if profile["resolved_trades"] >= 30:
            outcomes = [1 if (t["outcome"] or "").lower() == (t["resolution"] or "").lower() else 0
                       for t in resolved_trades]
            acf_result = acf_analysis(outcomes)
            acf_vals = acf_result.get('acf', {})
            print(f"ACF: lag1={acf_vals.get('lag_1', 0):.3f}, "
                  f"significant_lags={acf_result.get('significant_lags', [])}")

    # Generate replica
    decomposition = {
        "entry_price": entry,
        "timing": timing,
        "asset_preference": asset_pref,
        "direction_bias": direction,
        "sizing": sizing,
        "order_type": order_type,
        "regime": regime,
        "exit_pattern": exit_pat,
    }

    replica = generate_replica(decomposition, balance=500.0, profile=profile)
    print(f"\n--- Replica Strategy ---")
    print(f"Type: {replica.strategy_type}")
    print(f"Confidence: {replica.confidence:.2f}")
    print(f"Warnings: {replica.warnings}")

    env_template = render_env_template(replica)
    print(f"\n--- .env Template ---")
    print(env_template[:500])

    return {
        "health": health,
        "profile": profile,
        "decomposition": decomposition,
        "replica": {
            "strategy_type": replica.strategy_type,
            "confidence": replica.confidence,
            "env_overrides": replica.env_overrides,
            "warnings": replica.warnings,
        },
        "env_template": env_template,
    }


async def main():
    print("=== Fason2 Re-Analysis Pipeline ===\n")

    # Step 1: Migrate schema
    print("Step 1: Migrating DB schema...")
    migrate_schema(DB_PATH)

    # Step 2: Build resolution cache
    print("\nStep 2: Resolving trades via Binance klines...")
    await build_resolution_cache(DB_PATH)

    # Step 3: Run analysis
    print("\nStep 3: Running full analysis pipeline...")
    results = run_analysis(DB_PATH, SOURCE_TRADER)

    # Step 4: Save results
    output_path = Path(DB_PATH).parent / "analysis_results.json"
    # Convert non-serializable types
    serializable = json.loads(json.dumps(results, default=str))
    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\nResults saved to {output_path}")

    return results


if __name__ == "__main__":
    asyncio.run(main())
