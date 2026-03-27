"""trader_ingest.py — Data ingestion + resolution caching for trader analysis.

Inherits PolymarketActivityPoller for API access. Adds:
- SQLite storage with source_trader column (unified performance_db schema)
- 3-tier resolution cache: Gamma API (primary), RTDS cache (secondary), on-chain (fallback)
- Username-to-address resolution stub (Chrome MCP orchestrated by agent)

Phase 1 scope: crypto-updown markets only (BTC/ETH/SOL/XRP 5m/15m).
Non-crypto trades marked resolution=UNKNOWN and excluded from WR.
"""

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiohttp

from polyphemus.tools.polymarket_activity_poller import PolymarketActivityPoller


# Known wallet aliases (from MEMORY.md)
KNOWN_ALIASES: dict[str, str] = {
    "tugao9": "0x970e744a34cd0795ff7b4ba844018f17b7fd5c26",
    "gabagool": "0x3e14d0e319f1613edb9d42c07f44c4b0b325e582",
    "vague-sourdough": "",  # Placeholder - resolve via Chrome MCP
}

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Resolution cache schema
RESOLUTION_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS resolution_cache (
    market_slug TEXT NOT NULL,
    epoch_start INTEGER NOT NULL,
    resolved_direction TEXT NOT NULL,
    resolution_source TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (market_slug, epoch_start)
);
"""

# Trader trades schema (extends performance_db pattern)
TRADER_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS trader_trades (
    trade_id TEXT PRIMARY KEY,
    source_trader TEXT NOT NULL,
    analyst_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    slug TEXT NOT NULL,
    asset TEXT,
    side TEXT,
    outcome TEXT,
    price REAL,
    size REAL,
    size_unit TEXT DEFAULT 'unknown',
    market_type TEXT,
    window TEXT,
    maker_address TEXT DEFAULT '',
    resolution TEXT DEFAULT 'PENDING',
    analysis_confidence REAL DEFAULT 0.0,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trader_trades_source ON trader_trades(source_trader);
CREATE INDEX IF NOT EXISTS idx_trader_trades_slug ON trader_trades(slug);
"""


@dataclass
class IngestionResult:
    """Summary of an ingestion run."""
    db_path: str
    source_trader: str
    total_trades: int
    crypto_updown_trades: int
    other_trades: int
    date_range: tuple[str, str]  # (earliest, latest) ISO timestamps
    assets_seen: list[str]
    resolution_stats: dict = field(default_factory=dict)


class TraderIngestor(PolymarketActivityPoller):
    """Ingest a trader's Polymarket activity into SQLite for analysis."""

    def __init__(
        self,
        address: str,
        db_path: Optional[str] = None,
        analyst_id: str = "default",
        session: Optional[aiohttp.ClientSession] = None,
        logger=None,
    ):
        super().__init__(address=address, session=session, logger=logger)
        self._analyst_id = analyst_id
        if db_path is None:
            addr_short = address[:8].lower()
            base = Path(".omc/trader-analysis") / addr_short
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "trades.db")
        self._db_path = db_path

    def _init_db(self) -> sqlite3.Connection:
        """Initialize SQLite DB with required schemas."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(RESOLUTION_CACHE_SCHEMA)
        conn.executescript(TRADER_TRADES_SCHEMA)
        return conn

    async def ingest_to_db(self, max_trades: int = 10000) -> IngestionResult:
        """Paginate all trades and store in SQLite.

        Returns IngestionResult with summary statistics.
        """
        trades = await self.ingest_all(max_trades=max_trades)
        conn = self._init_db()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        crypto_count = 0
        other_count = 0
        assets = set()
        timestamps = []

        for t in trades:
            is_crypto = t["market_type"] in ("updown_5m", "updown_15m")
            if is_crypto:
                crypto_count += 1
            else:
                other_count += 1

            if t["asset"]:
                assets.add(t["asset"])
            timestamps.append(t["timestamp"])

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO trader_trades
                       (trade_id, source_trader, analyst_id, timestamp, slug, asset,
                        side, outcome, price, size, size_unit, market_type, window,
                        maker_address, resolution, analysis_confidence, ingested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        t["trade_id"],
                        self._address,
                        self._analyst_id,
                        t["timestamp"],
                        t["slug"],
                        t["asset"],
                        t["side"],
                        t["outcome"],
                        t["price"],
                        t["size"],
                        t.get("size_unit", "unknown"),
                        t["market_type"],
                        t["window"],
                        t.get("maker_address", ""),
                        "PENDING" if is_crypto else "UNKNOWN",
                        0.0,
                        now_iso,
                    ),
                )
            except sqlite3.Error:
                continue

        conn.commit()
        conn.close()

        # Date range
        if timestamps:
            from datetime import datetime, timezone
            earliest = datetime.fromtimestamp(min(timestamps), tz=timezone.utc).isoformat()
            latest = datetime.fromtimestamp(max(timestamps), tz=timezone.utc).isoformat()
        else:
            earliest = latest = ""

        return IngestionResult(
            db_path=self._db_path,
            source_trader=self._address,
            total_trades=len(trades),
            crypto_updown_trades=crypto_count,
            other_trades=other_count,
            date_range=(earliest, latest),
            assets_seen=sorted(assets),
        )

    async def build_resolution_cache(self, slugs: Optional[list[str]] = None) -> dict:
        """Build resolution cache for ingested trades using Gamma API.

        Args:
            slugs: specific slugs to resolve. If None, resolves all PENDING.

        Returns:
            dict with resolved/pending/failed counts.
        """
        conn = self._init_db()

        if slugs is None:
            rows = conn.execute(
                "SELECT DISTINCT slug FROM trader_trades WHERE source_trader = ? AND resolution = 'PENDING'",
                (self._address,),
            ).fetchall()
            slugs = [r[0] for r in rows if r[0]]

        resolved = 0
        pending = 0
        failed = 0

        await self._ensure_session()

        for slug in slugs:
            direction = await self._resolve_via_gamma(slug)
            if direction:
                # Update resolution cache
                now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                conn.execute(
                    """INSERT OR REPLACE INTO resolution_cache
                       (market_slug, epoch_start, resolved_direction, resolution_source, cached_at)
                       VALUES (?, 0, ?, 'gamma_api', ?)""",
                    (slug, direction, now_iso),
                )
                # Update trade resolutions
                conn.execute(
                    "UPDATE trader_trades SET resolution = ? WHERE slug = ? AND source_trader = ?",
                    (direction, slug, self._address),
                )
                resolved += 1
            else:
                pending += 1

            await asyncio.sleep(0.1)  # Rate limit courtesy

        conn.commit()
        conn.close()

        stats = {"resolved": resolved, "pending": pending, "failed": failed, "total_slugs": len(slugs)}

        # Update ingestion result resolution stats
        return stats

    async def _resolve_via_gamma(self, slug: str) -> Optional[str]:
        """Resolve market outcome via Gamma API.

        Returns 'Up', 'Down', or None if not yet resolved.

        WARNING: Gamma API does NOT resolve individual 5m/15m epoch markets.
        It only resolves parent market slugs. For crypto-updown use cases,
        this returns None 100% of the time. Use build_resolution_cache_binance()
        instead. Kept for non-crypto market types.
        """
        try:
            url = f"{GAMMA_API_URL}/markets"
            async with self._session.get(
                url,
                params={"slug": slug},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not isinstance(data, list) or len(data) == 0:
                    return None
                market = data[0]
                if market.get("resolved") and market.get("resolution"):
                    return market["resolution"]
                return None
        except Exception:
            return None

    async def _resolve_via_binance_kline(
        self, asset: str, epoch_start_ts: float, window_secs: int = 300
    ) -> Optional[str]:
        """Resolve epoch direction using Binance kline close vs open.

        Compares kline close price to open price for the epoch interval.
        Returns 'Up' if close > open, 'Down' if close < open, None if flat or error.

        NOTE: This is a PROXY for Chainlink RTDS resolution. Binance close and
        Chainlink RTDS can diverge by 2-5% of close-call epochs due to timing
        differences. Flag resolution_source='binance_kline' to distinguish from
        ground-truth RTDS resolution.
        """
        SYMBOL_MAP = {
            "BTC": "BTCUSDT", "ETH": "ETHUSDT",
            "SOL": "SOLUSDT", "XRP": "XRPUSDT",
            "DOGE": "DOGEUSDT",
        }
        symbol = SYMBOL_MAP.get(asset.upper())
        if not symbol:
            return None

        interval = "5m" if window_secs == 300 else "15m"
        start_ms = int(epoch_start_ts * 1000)
        end_ms = start_ms + window_secs * 1000

        try:
            await self._ensure_session()
            url = "https://api.binance.com/api/v3/klines"
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": str(start_ms),
                "endTime": str(end_ms),
                "limit": "1",
            }
            async with self._session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data or len(data) == 0:
                    return None
                kline = data[0]
                open_price = float(kline[1])
                close_price = float(kline[4])
                if close_price > open_price:
                    return "Up"
                elif close_price < open_price:
                    return "Down"
                else:
                    return None  # Flat epoch - ambiguous resolution
        except Exception:
            return None

    async def build_resolution_cache_binance(self) -> dict:
        """Resolve trade outcomes using Binance kline closes as RTDS proxy.

        Groups trades by (asset, epoch_start) to minimize API calls.
        Only resolves trades with resolution='PENDING'.

        Returns dict with resolved/flat/failed/total counts and source tag.
        """
        conn = self._init_db()
        rows = conn.execute(
            """SELECT trade_id, slug, asset, timestamp, window
               FROM trader_trades
               WHERE source_trader = ? AND resolution = 'PENDING'
               AND market_type IN ('updown_5m', 'updown_15m')""",
            (self._address,),
        ).fetchall()

        if not rows:
            conn.close()
            return {"resolved": 0, "flat": 0, "failed": 0, "total_trades": 0,
                    "unique_epochs": 0, "source": "binance_kline"}

        await self._ensure_session()

        resolved = 0
        flat = 0
        failed = 0
        epoch_cache: dict[tuple, Optional[str]] = {}

        for trade_id, slug, asset, ts, window in rows:
            if not asset:
                failed += 1
                continue

            window_secs = 300 if window == "5m" else 900 if window == "15m" else 300
            epoch_start = (int(ts) // window_secs) * window_secs
            cache_key = (asset, epoch_start, window_secs)

            if cache_key not in epoch_cache:
                direction = await self._resolve_via_binance_kline(
                    asset, epoch_start, window_secs
                )
                epoch_cache[cache_key] = direction
                await asyncio.sleep(0.05)  # Binance rate limit: 1200 req/min

            direction = epoch_cache[cache_key]
            if direction:
                now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                conn.execute(
                    """INSERT OR REPLACE INTO resolution_cache
                       (market_slug, epoch_start, resolved_direction,
                        resolution_source, cached_at)
                       VALUES (?, ?, ?, 'binance_kline', ?)""",
                    (slug, epoch_start, direction, now_iso),
                )
                conn.execute(
                    "UPDATE trader_trades SET resolution = ? WHERE trade_id = ?",
                    (direction, trade_id),
                )
                resolved += 1
            else:
                flat += 1

        conn.commit()
        conn.close()

        return {
            "resolved": resolved,
            "flat": flat,
            "failed": failed,
            "total_trades": len(rows),
            "unique_epochs": len(epoch_cache),
            "source": "binance_kline",
        }

    @property
    def db_path(self) -> str:
        return self._db_path


def get_cached_resolution(db_path: str, slug: str, epoch_start: int = 0) -> Optional[str]:
    """Look up cached resolution for a market slug.

    Args:
        db_path: path to trades.db containing resolution_cache table
        slug: market slug
        epoch_start: epoch start timestamp (0 for slug-level resolution)

    Returns:
        Resolved direction string or None.
    """
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT resolved_direction FROM resolution_cache WHERE market_slug = ? AND epoch_start = ?",
            (slug, epoch_start),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def resolve_alias(name: str) -> Optional[str]:
    """Resolve a known alias or @username to wallet address.

    For @usernames not in KNOWN_ALIASES, returns None (agent must use Chrome MCP).
    """
    clean = name.lstrip("@").lower()
    return KNOWN_ALIASES.get(clean)


def resolution_health(db_path: str, source_trader: str) -> dict:
    """Check resolution cache health for a trader.

    Returns dict with total, resolved, pending, last resolution timestamp,
    resolution rate, and source breakdown. Use this to monitor whether
    the resolution pipeline is working.
    """
    try:
        conn = sqlite3.connect(db_path)
        total = conn.execute(
            "SELECT COUNT(*) FROM trader_trades WHERE source_trader = ?",
            (source_trader,),
        ).fetchone()[0]

        resolved = conn.execute(
            """SELECT COUNT(*) FROM trader_trades
               WHERE source_trader = ? AND resolution NOT IN ('PENDING', 'UNKNOWN')""",
            (source_trader,),
        ).fetchone()[0]

        pending = conn.execute(
            "SELECT COUNT(*) FROM trader_trades WHERE source_trader = ? AND resolution = 'PENDING'",
            (source_trader,),
        ).fetchone()[0]

        # Last resolution timestamp
        last_row = conn.execute(
            """SELECT MAX(cached_at) FROM resolution_cache
               WHERE market_slug IN (
                   SELECT DISTINCT slug FROM trader_trades WHERE source_trader = ?
               )""",
            (source_trader,),
        ).fetchone()
        last_resolution_ts = last_row[0] if last_row and last_row[0] else None

        # Source breakdown
        sources = conn.execute(
            """SELECT resolution_source, COUNT(*) FROM resolution_cache
               WHERE market_slug IN (
                   SELECT DISTINCT slug FROM trader_trades WHERE source_trader = ?
               )
               GROUP BY resolution_source""",
            (source_trader,),
        ).fetchall()
        source_breakdown = {row[0]: row[1] for row in sources}

        conn.close()

        resolution_rate = round(resolved / total * 100, 1) if total > 0 else 0

        return {
            "total_trades": total,
            "resolved": resolved,
            "pending": pending,
            "unknown": total - resolved - pending,
            "resolution_rate_pct": resolution_rate,
            "last_resolution_ts": last_resolution_ts,
            "source_breakdown": source_breakdown,
            "healthy": resolution_rate > 50,
        }
    except sqlite3.Error:
        return {
            "total_trades": 0, "resolved": 0, "pending": 0, "unknown": 0,
            "resolution_rate_pct": 0, "last_resolution_ts": None,
            "source_breakdown": {}, "healthy": False,
        }


def get_trade_counts(db_path: str, source_trader: str) -> dict:
    """Get trade counts and resolution status for a trader.

    Returns dict with total, resolved, pending, unknown counts.
    """
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """SELECT resolution, COUNT(*) FROM trader_trades
               WHERE source_trader = ? GROUP BY resolution""",
            (source_trader,),
        ).fetchall()
        conn.close()
        counts = {r[0]: r[1] for r in rows}
        total = sum(counts.values())
        return {
            "total": total,
            "resolved": total - counts.get("PENDING", 0) - counts.get("UNKNOWN", 0),
            "pending": counts.get("PENDING", 0),
            "unknown": counts.get("UNKNOWN", 0),
            "provisional_pct": round(counts.get("PENDING", 0) / total * 100, 1) if total > 0 else 0,
        }
    except sqlite3.Error:
        return {"total": 0, "resolved": 0, "pending": 0, "unknown": 0, "provisional_pct": 0}
