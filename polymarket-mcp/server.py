"""Polymarket CLOB MCP Server - Read-Only V1.

Exposes Polymarket market data, portfolio, and trade analytics
as Claude Code MCP tools via stdio transport.
"""

import os
from datetime import date, datetime, timezone

from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env from the same directory as this script
_script_dir = Path(__file__).resolve().parent
load_dotenv(_script_dir / ".env")

mcp = FastMCP("Polymarket")

# Lazy singletons
_clob = None
_db = None

_READ_ONLY = {"readOnlyHint": True}


def _get_clob():
    global _clob
    if _clob is None:
        from clob_read import ClobReader
        _clob = ClobReader()
    return _clob


def _get_db():
    global _db
    if _db is None:
        from db_read import DBReader
        _db = DBReader()
    return _db


# ── Market Data (no auth needed) ───────────────────────────────────


@mcp.tool(annotations=_READ_ONLY)
def get_market(condition_id: str) -> dict:
    """Get market details by condition ID.

    Returns title, outcomes, active status, end date, and tokens.
    """
    return _get_clob().get_market(condition_id)


@mcp.tool(annotations=_READ_ONLY)
def search_markets(next_cursor: str = "") -> dict:
    """Search active/sampling markets on Polymarket.

    Returns paginated list of markets with condition IDs and current prices.
    Pass next_cursor from previous response to get the next page.
    """
    return _get_clob().search_markets(next_cursor)


@mcp.tool(annotations=_READ_ONLY)
def get_order_book(token_id: str) -> dict:
    """Get full order book for a token.

    Returns bids, asks, spread, and midpoint.
    """
    return _get_clob().get_order_book(token_id)


@mcp.tool(annotations=_READ_ONLY)
def get_price(token_id: str) -> dict:
    """Get current best bid, ask, midpoint, and spread for a token."""
    clob = _get_clob()
    mid = clob.get_midpoint(token_id)
    spread_data = clob.get_spread(token_id)
    return {
        "midpoint": mid,
        "bid": spread_data.get("bid"),
        "ask": spread_data.get("ask"),
        "spread": spread_data.get("spread"),
    }


@mcp.tool(annotations=_READ_ONLY)
def get_server_time() -> dict:
    """Get CLOB server timestamp. Useful for latency checks."""
    return {"server_time": _get_clob().get_server_time()}


# ── Portfolio (authenticated, read-only) ───────────────────────────


@mcp.tool(annotations=_READ_ONLY)
def get_balance() -> dict:
    """Get USDC wallet balance on Polymarket."""
    balance = _get_clob().get_balance()
    return {"balance_usdc": round(balance, 2)}


@mcp.tool(annotations=_READ_ONLY)
def get_positions() -> dict:
    """Get all open positions from the performance database.

    Returns entry details and current live midpoint for each position.
    """
    db = _get_db()
    clob = _get_clob()
    open_trades = db.get_open_trades()

    positions = []
    for t in open_trades:
        token_id = t.get("token_id", "")
        entry_price = t.get("entry_price", 0)
        entry_size = t.get("entry_size", 0)
        entry_amount = entry_price * entry_size

        current_mid = 0.0
        if token_id:
            try:
                current_mid = clob.get_midpoint(token_id)
            except Exception:
                pass

        unrealized_pnl = (current_mid - entry_price) * entry_size if current_mid > 0 else None

        entry_time = t.get("entry_time")
        entry_dt = (
            datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()
            if entry_time
            else None
        )

        positions.append({
            "slug": t.get("slug", ""),
            "market_title": t.get("market_title", ""),
            "outcome": t.get("outcome", ""),
            "entry_price": round(entry_price, 4),
            "entry_size": round(entry_size, 2),
            "entry_amount": round(entry_amount, 2),
            "entry_time": entry_dt,
            "current_mid": round(current_mid, 4) if current_mid > 0 else None,
            "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
        })

    return {"count": len(positions), "positions": positions}


@mcp.tool(annotations=_READ_ONLY)
def get_open_orders() -> dict:
    """Get currently open limit orders on the CLOB."""
    orders = _get_clob().get_open_orders()
    return {"count": len(orders), "orders": orders}


# ── Performance Analytics (from performance.db) ───────────────────


@mcp.tool(annotations=_READ_ONLY)
def get_trade_history(limit: int = 20) -> dict:
    """Get recent closed trades with P&L.

    Args:
        limit: Number of trades to return (default 20, max 100).
    """
    limit = min(max(limit, 1), 100)
    db = _get_db()
    trades = db.get_recent_trades(limit)

    formatted = []
    for t in trades:
        entry_time = t.get("entry_time")
        exit_time = t.get("exit_time")
        formatted.append({
            "slug": t.get("slug", ""),
            "market_title": t.get("market_title", ""),
            "outcome": t.get("outcome", ""),
            "entry_price": round(t.get("entry_price", 0), 4),
            "exit_price": round(t.get("exit_price", 0), 4),
            "entry_size": round(t.get("entry_size", 0), 2),
            "pnl": round(t.get("pnl") or t.get("profit_loss") or 0, 2),
            "exit_reason": t.get("exit_reason", ""),
            "entry_time": (
                datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat()
                if entry_time else None
            ),
            "exit_time": (
                datetime.fromtimestamp(exit_time, tz=timezone.utc).isoformat()
                if exit_time else None
            ),
            "hold_seconds": t.get("hold_seconds"),
        })

    return {"count": len(formatted), "trades": formatted}


@mcp.tool(annotations=_READ_ONLY)
def get_stats() -> dict:
    """Get overall trading statistics: total trades, win rate, P&L, resolution stats."""
    return _get_db().get_stats()


@mcp.tool(annotations=_READ_ONLY)
def get_daily_pnl(utc_date: str = "") -> dict:
    """Get realized P&L for a specific UTC date.

    Args:
        utc_date: Date in YYYY-MM-DD format. Defaults to today UTC.
    """
    if utc_date:
        d = date.fromisoformat(utc_date)
    else:
        d = datetime.now(timezone.utc).date()
    pnl = _get_db().get_daily_pnl(d)
    return {"date": d.isoformat(), "pnl": pnl}


@mcp.tool(annotations=_READ_ONLY)
def get_wr_by_bucket(asset: str) -> dict:
    """Get win rate broken down by entry price buckets for an asset.

    Args:
        asset: Asset name (BTC, ETH, SOL). Matched via slug prefix.
    """
    buckets = _get_db().get_all_buckets(asset)
    return {"asset": asset.upper(), "buckets": buckets}


@mcp.tool(annotations=_READ_ONLY)
def get_trade_summary() -> dict:
    """Get a plain-English trading performance summary.

    Combines stats, best/worst buckets, and recent P&L into a readable overview.
    """
    db = _get_db()
    stats = db.get_stats()
    today_pnl = db.get_daily_pnl(datetime.now(timezone.utc).date())

    total = stats["total_trades"]
    wr = stats["win_rate"]
    net = stats["total_pnl"]
    avg = stats["avg_pnl"]
    res_wr = stats["resolution_wr"]

    summary_parts = [
        f"{total} closed trades, {wr*100:.1f}% win rate, ${net:.2f} net P&L.",
        f"Average trade: ${avg:.2f}.",
        f"Resolution win rate: {res_wr*100:.1f}% ({stats['resolution_wins']}W / {stats['resolution_losses']}L).",
        f"Today's P&L: ${today_pnl:.2f}.",
    ]

    for asset in ["BTC", "ETH", "SOL"]:
        try:
            buckets = db.get_all_buckets(asset)
            if buckets:
                best = max(buckets, key=lambda b: b["win_rate"])
                summary_parts.append(
                    f"{asset} best bucket: {best['bucket']} "
                    f"({best['win_rate']*100:.0f}% WR, {best['n']} trades, ${best['pnl']:.2f})."
                )
        except Exception:
            pass

    return {"summary": " ".join(summary_parts), "stats": stats, "today_pnl": today_pnl}


if __name__ == "__main__":
    mcp.run()
