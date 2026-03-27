#!/usr/bin/env python3
"""API Usage Telemetry — log token usage from Anthropic API calls to SQLite.

Thin wrapper: call log_usage() after every messages.create() call.
Query get_summary() for per-script cost breakdowns.

Usage as standalone:
    python3 tools/api_telemetry.py                # 7-day summary
    python3 tools/api_telemetry.py --days 30      # 30-day summary
"""

import os
import sqlite3
from datetime import datetime, timezone

# Pricing per 1M tokens (as of Mar 2026)
_PRICING = {
    "claude-opus-4-6-20260205": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6-20260217": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
}

_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "data", "api_usage.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY,
    ts TEXT DEFAULT (datetime('now')),
    script TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    estimated_cost_usd REAL DEFAULT 0.0
);
"""


def _get_db(db_path: str = "") -> sqlite3.Connection:
    path = db_path or os.environ.get("API_TELEMETRY_DB", _DEFAULT_DB)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def _estimate_cost(model: str, input_tokens: int, output_tokens: int,
                   cache_creation: int, cache_read: int) -> float:
    """Estimate cost in USD based on model pricing."""
    pricing = _PRICING.get(model)
    if not pricing:
        # Fallback: use Haiku pricing (cheapest)
        pricing = _PRICING["claude-haiku-4-5-20251001"]

    cost = (
        input_tokens * pricing["input"] / 1e6
        + output_tokens * pricing["output"] / 1e6
        + cache_creation * pricing["cache_write"] / 1e6
        + cache_read * pricing["cache_read"] / 1e6
    )
    return round(cost, 8)


def log_usage(script_name: str, response, db_path: str = "") -> None:
    """Log token usage from an Anthropic API response.

    Args:
        script_name: Identifier for the calling script (e.g., "repurpose", "cmo_engine")
        response: The response object from client.messages.create()
        db_path: Optional override for the SQLite database path
    """
    try:
        usage = response.usage
        model = response.model
        input_tokens = getattr(usage, 'input_tokens', 0)
        output_tokens = getattr(usage, 'output_tokens', 0)
        cache_creation = getattr(usage, 'cache_creation_input_tokens', 0)
        cache_read = getattr(usage, 'cache_read_input_tokens', 0)

        cost = _estimate_cost(model, input_tokens, output_tokens, cache_creation, cache_read)

        conn = _get_db(db_path)
        try:
            conn.execute(
                "INSERT INTO api_usage (script, model, input_tokens, output_tokens, "
                "cache_creation_tokens, cache_read_tokens, estimated_cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (script_name, model, input_tokens, output_tokens,
                 cache_creation, cache_read, cost)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Telemetry must never break the calling script
        pass


def get_summary(days: int = 7, db_path: str = "") -> list:
    """Return per-script usage summary for the last N days."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute("""
            SELECT script, model,
                   COUNT(*) as calls,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(cache_creation_tokens) as total_cache_write,
                   SUM(estimated_cost_usd) as total_cost
            FROM api_usage
            WHERE ts > datetime('now', ?)
            GROUP BY script, model
            ORDER BY total_cost DESC
        """, (f"-{days} days",)).fetchall()
        return [
            {
                "script": r[0], "model": r[1], "calls": r[2],
                "input_tokens": r[3], "output_tokens": r[4],
                "cache_read_tokens": r[5], "cache_write_tokens": r[6],
                "cost_usd": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="API Usage Telemetry Summary")
    parser.add_argument("--days", type=int, default=7, help="Lookback period in days")
    args = parser.parse_args()

    summary = get_summary(args.days)
    if not summary:
        print(f"No API usage recorded in the last {args.days} days.")
    else:
        total_cost = 0.0
        total_calls = 0
        print(f"\n{'Script':<25} {'Model':<30} {'Calls':>6} {'Input':>10} {'Output':>10} {'Cache Read':>12} {'Cost':>10}")
        print("-" * 110)
        for row in summary:
            print(f"{row['script']:<25} {row['model']:<30} {row['calls']:>6} "
                  f"{row['input_tokens']:>10} {row['output_tokens']:>10} "
                  f"{row['cache_read_tokens']:>12} ${row['cost_usd']:>9.6f}")
            total_cost += row['cost_usd']
            total_calls += row['calls']
        print("-" * 110)
        print(f"{'TOTAL':<25} {'':<30} {total_calls:>6} {'':<10} {'':<10} {'':<12} ${total_cost:>9.6f}")
        print(f"\nPeriod: last {args.days} days")
