#!/usr/bin/env python3
"""OpenClaw Crypto Watcher — 24/7 market intelligence informant.

Polls Binance Futures OI and Fear & Greed index every 5 minutes.
Writes /opt/openclaw/data/market_context.json for all strategies to read.

Usage:
    python3 crypto_watcher.py update   # Poll signals, write context.json (cron target)
    python3 crypto_watcher.py status   # Pretty-print current context
    python3 crypto_watcher.py history  # Show last 5 snapshots
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- Config ---

DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
LOG_DIR = os.environ.get("OPENCLAW_LOG_DIR", "/opt/openclaw/logs")
CONTEXT_PATH = os.path.join(DATA_DIR, "market_context.json")
HISTORY_PATH = os.path.join(DATA_DIR, "market_context_history.json")

ASSETS = ["BTC", "ETH"]
BINANCE_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}USDT"
FG_URL = "https://api.alternative.me/fng/?limit=2"

# F&G: cache until time_until_update expires, but never longer than 48h
FG_MAX_CACHE_SECS = 172_800  # 48 hours hard cap
REQUEST_TIMEOUT = 10  # seconds per HTTP request


# --- HTTP helpers ---

def _fetch_json(url: str) -> dict:
    """Fetch JSON from URL. Raises on HTTP error or timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "OpenClaw/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


# --- OI polling ---

def fetch_oi(asset: str) -> dict:
    """Fetch current open interest for asset from Binance Futures.

    Returns dict with oi_contracts and oi_updated_at.
    Returns None fields on failure (never raises).
    """
    try:
        url = BINANCE_OI_URL.format(symbol=asset)
        data = _fetch_json(url)
        oi = float(data["openInterest"])
        return {
            "oi_contracts": oi,
            "oi_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        _log(f"OI fetch error for {asset}: {e}")
        return {
            "oi_contracts": None,
            "oi_updated_at": None,
        }


# --- Fear & Greed polling ---

def _load_fg_cache(ctx: dict) -> tuple:
    """Extract cached F&G from existing context. Returns (fg_dict, fetched_at)."""
    macro = ctx.get("macro", {})
    fg_value = macro.get("fear_greed")
    fetched_at = macro.get("fear_greed_fetched_at", 0)
    return macro, fetched_at


def fetch_fear_greed(existing_ctx: dict) -> dict:
    """Fetch Fear & Greed index, using cache when fresh.

    Cache strategy:
    - Use cached value if time_until_update hasn't elapsed AND cache < 48h old
    - If API fails, return cached value with stale=True
    - If no cache and API fails, return None with stale=True
    """
    now = time.time()
    macro_cache, fg_fetched_at = _load_fg_cache(existing_ctx)
    cache_age = now - fg_fetched_at if fg_fetched_at else float("inf")

    # Check if cache is still fresh
    fg_next_update = macro_cache.get("fear_greed_next_update_secs", 0)
    cache_fresh = (
        fg_fetched_at > 0
        and cache_age < FG_MAX_CACHE_SECS  # never older than 48h
        and cache_age < fg_next_update      # respect API's own next-update hint
    )

    if cache_fresh:
        return {**macro_cache, "fear_greed_stale": False}

    # Try to fetch fresh data
    try:
        data = _fetch_json(FG_URL)
        entries = data.get("data", [])
        if not entries:
            raise ValueError("Empty F&G response")

        current = entries[0]
        value = int(current["value"])
        label = current["value_classification"]
        ts_str = datetime.fromtimestamp(
            int(current["timestamp"]), tz=timezone.utc
        ).strftime("%Y-%m-%d")
        next_update = int(current.get("time_until_update", 86400))

        result = {
            "fear_greed": value,
            "fear_greed_label": label,
            "fear_greed_date": ts_str,
            "fear_greed_next_update_secs": next_update,
            "fear_greed_fetched_at": now,
            "fear_greed_stale": False,
        }
        _log(f"F&G fetched: {value} ({label}), next update in {next_update}s")
        return result

    except Exception as e:
        _log(f"F&G fetch error: {e} — using cached value")
        if macro_cache.get("fear_greed") is not None:
            return {**macro_cache, "fear_greed_stale": True}
        # No cache at all
        return {
            "fear_greed": None,
            "fear_greed_label": None,
            "fear_greed_date": None,
            "fear_greed_next_update_secs": 86400,
            "fear_greed_fetched_at": 0,
            "fear_greed_stale": True,
        }


# --- Context load/save ---

def load_context() -> dict:
    """Load existing market_context.json. Returns empty dict if missing or corrupt."""
    try:
        with open(CONTEXT_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_context(data: dict) -> None:
    """Atomically write context to disk. Uses rename (atomic) with shutil.move fallback."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = CONTEXT_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    try:
        os.rename(tmp_path, CONTEXT_PATH)
    except OSError:
        # Cross-device link (tmpfs or mount edge case)
        shutil.move(tmp_path, CONTEXT_PATH)


def save_history(data: dict) -> None:
    """Append snapshot to history file. Keep last 10 entries."""
    try:
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    history.append({"snapshot_at": data["updated_at"], "data": data})
    history = history[-10:]  # keep last 10
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2)
    try:
        os.rename(tmp, HISTORY_PATH)
    except OSError:
        shutil.move(tmp, HISTORY_PATH)


# --- Logging ---

def _log(msg: str) -> None:
    """Write timestamped log line to stdout (cron captures to watcher.log)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


# --- Commands ---

def cmd_update(_args) -> None:
    """Poll all signals and write market_context.json."""
    _log("update start")
    existing = load_context()

    # Poll OI for each asset (independent — continue if one fails)
    asset_data = {}
    for asset in ASSETS:
        oi = fetch_oi(asset)
        # Compute delta vs previous value
        prev = existing.get(asset, {}).get("oi_contracts")
        if prev and oi["oi_contracts"] is not None:
            oi["oi_prev_contracts"] = prev
            oi["oi_change_pct"] = round((oi["oi_contracts"] - prev) / prev, 6)
        else:
            oi["oi_prev_contracts"] = None
            oi["oi_change_pct"] = None
        asset_data[asset] = oi
        if oi["oi_contracts"] is not None:
            change_str = (
                f"{oi['oi_change_pct']:+.3%}" if oi["oi_change_pct"] is not None else "n/a"
            )
            _log(f"OI {asset}: {oi['oi_contracts']:,.1f} contracts ({change_str})")

    # Poll F&G (cached when fresh)
    macro = fetch_fear_greed(existing)

    # Build context
    ctx = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": 1,
        **asset_data,
        "macro": macro,
    }

    save_context(ctx)
    save_history(ctx)
    _log(f"context.json written: BTC OI={asset_data['BTC']['oi_contracts']}, "
         f"F&G={macro.get('fear_greed')} ({macro.get('fear_greed_label', 'n/a')})")


def cmd_status(_args) -> None:
    """Pretty-print current market context."""
    ctx = load_context()
    if not ctx:
        print("No context.json found. Run: crypto_watcher.py update")
        return

    updated = ctx.get("updated_at", "unknown")
    # Age in seconds
    try:
        updated_dt = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        age_secs = int(time.time() - updated_dt.timestamp())
        age_str = f"{age_secs // 60}m{age_secs % 60}s ago"
    except Exception:
        age_str = "unknown age"

    print(f"\nOpenClaw Market Context — {updated} ({age_str})")
    print("-" * 55)

    for asset in ASSETS:
        d = ctx.get(asset, {})
        oi = d.get("oi_contracts")
        change = d.get("oi_change_pct")
        if oi is not None:
            change_str = f" ({change:+.3%} vs prev)" if change is not None else ""
            print(f"{asset} OI: {oi:>12,.1f} contracts{change_str}")
        else:
            print(f"{asset} OI: unavailable")

    macro = ctx.get("macro", {})
    fg = macro.get("fear_greed")
    fg_label = macro.get("fear_greed_label", "")
    fg_date = macro.get("fear_greed_date", "")
    stale = " [STALE]" if macro.get("fear_greed_stale") else ""
    if fg is not None:
        print(f"Fear & Greed:  {fg:>3} — {fg_label}  [updated {fg_date}]{stale}")
    else:
        print("Fear & Greed:  unavailable")
    print()


def cmd_history(_args) -> None:
    """Show last 5 context snapshots."""
    try:
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("No history found. Run update a few times first.")
        return

    print(f"\nLast {min(5, len(history))} snapshots:")
    print("-" * 55)
    for entry in history[-5:]:
        snap = entry.get("data", {})
        ts = snap.get("updated_at", "?")
        btc_oi = snap.get("BTC", {}).get("oi_contracts")
        btc_delta = snap.get("BTC", {}).get("oi_change_pct")
        fg = snap.get("macro", {}).get("fear_greed")
        delta_str = f"({btc_delta:+.3%})" if btc_delta is not None else ""
        oi_str = f"{btc_oi:,.0f}" if btc_oi is not None else "n/a"
        print(f"  {ts}  BTC OI={oi_str} {delta_str}  F&G={fg}")
    print()


# --- Main ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenClaw Crypto Watcher — 24/7 market intelligence"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("update", help="Poll signals and write context.json")
    sub.add_parser("status", help="Pretty-print current context")
    sub.add_parser("history", help="Show last 5 snapshots")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "update": cmd_update,
        "status": cmd_status,
        "history": cmd_history,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
