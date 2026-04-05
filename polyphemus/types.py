"""
Polyphemus Polymarket Trading Bot - Type Definitions and Constants

This module contains ALL dataclasses, enums, and constants used across the project.
It serves as the single type vocabulary for the entire application.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional


# ============================================================================
# Enums
# ============================================================================


class ExitReason(str, Enum):
    """Enumeration of reasons why a position was exited."""
    MARKET_RESOLVED = "market_resolved"
    PROFIT_TARGET = "profit_target"
    TIME_EXIT = "time_exit"
    MAX_HOLD = "max_hold"
    SELL_SIGNAL = "sell_signal"
    STOP_LOSS = "stop_loss"
    MID_PRICE_STOP = "mid_price_stop"
    PRE_RESOLUTION_EXIT = "pre_resolution_exit"
    PROFIT_TARGET_EARLY = "profit_target_early"
    CONFIDENCE_EXIT = "confidence_exit"


class OrderStatus(str, Enum):
    """Enumeration of order statuses returned by CLOB API."""
    LIVE = "LIVE"
    FILLED = "FILLED"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class AccumulatorState(str, Enum):
    """State machine states for the accumulator engine."""
    IDLE = "idle"
    SCANNING = "scanning"
    ACCUMULATING = "accumulating"
    HEDGED = "hedged"
    SETTLING = "settling"


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class MomentumResult:
    """Result of Binance momentum calculation for a crypto asset."""
    direction: str        # "UP", "DOWN", "NEUTRAL", "UNKNOWN"
    momentum_pct: float   # % change over lookback window
    confidence: float     # 0.0-1.0 (ratio of momentum to confidence_threshold)
    age_secs: float       # seconds since last closed candle


@dataclass
class Position:
    """
    Represents an active or historical trading position.

    All datetime fields are UTC-aware (timezone.utc).
    """
    token_id: str
    slug: str
    entry_price: float
    entry_size: float
    entry_time: datetime  # UTC-aware
    entry_tx_hash: str
    market_end_time: datetime  # UTC-aware
    current_price: float = 0.0
    peak_price: float = 0.0
    exit_reason: Optional[str] = None
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None  # UTC-aware if present
    exit_tx_hash: Optional[str] = None
    pnl: Optional[float] = None
    is_resolved: bool = False
    is_redeemed: bool = False
    metadata: dict = field(default_factory=dict)
    outcome: str = ""  # "up" or "down" for direction limiter


@dataclass
class Signal:
    """
    Represents a trading signal from the Signal API.

    All timestamps are Unix epoch seconds (float).
    """
    tx_hash: str
    timestamp: float  # Unix epoch seconds
    direction: str
    outcome: str
    asset: str
    price: float
    usdc_size: float
    market_title: str
    slug: str
    raw: dict = field(default_factory=dict)


@dataclass
class FilterResult:
    """Result of applying filters to a signal."""
    passed: bool
    reasons: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)


@dataclass
class ExitSignal:
    """Signal indicating a position should be exited."""
    token_id: str
    reason: str
    exit_price: Optional[float] = None


@dataclass
class ExecutionResult:
    """Result of executing an order on the CLOB."""
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    fill_size: float = 0.0
    error: str = ""
    reason: str = ""
    fill_time_ms: int = 0


# ============================================================================
# Constants
# ============================================================================

# Entry Price Strategy
MIN_ENTRY_PRICE = 0.60
MAX_ENTRY_PRICE = 0.85

# Order Fill Verification
ORDER_POLL_INTERVAL = 1  # seconds between fill checks
FAK_POLL_INTERVAL = 0.2  # FAK fills instantly, just confirming settlement
ORDER_POLL_MAX = 10  # max polls (10s total) — legacy, use MAKER/TAKER below
MAKER_POLL_MAX = 20  # 20s for maker orders (15m markets need longer to fill)
TAKER_POLL_MAX = 10  # 10s for taker orders and SELL exits
ORDER_TIMEOUT = 60  # seconds for CLOB API calls

# Memory Management
SEEN_TRADES_CAP = 5000
SEEN_TRADES_EVICT_PCT = 0.20

# Connection Backoff
BACKOFF_BASE = 5
BACKOFF_MAX = 60
BACKOFF_MULTIPLIER = 2
HEALTHY_RESET_SECS = 300

# Session Management
SESSION_ROTATE_SECS = 1800
STALE_THRESHOLD_SECS = 120

# Caching
BALANCE_CACHE_TTL = 60

# Monitoring
HEALTH_LOG_INTERVAL = 300
WATCHDOG_INTERVAL = 60
EXIT_CHECK_INTERVAL = 0.05   # 50ms fallback; exit loop is event-driven via WS
PRICE_FEED_INTERVAL = 0.05   # 50ms fallback; price loop uses WS midpoints (dict lookup, no REST)

# Auto-Claim
REDEEM_INTERVAL = 600

# Daily Operations
DAILY_RESTART_HOURS = 20

# Order Sizing
MIN_SHARES_FOR_SELL = 5.0

# Binance Momentum Feed
BINANCE_WS_URL = os.environ.get("BINANCE_WS_URL", "wss://stream.binance.com:9443/stream")
BINANCE_SYMBOLS = ["btcusdt", "ethusdt", "solusdt", "xrpusdt"]
BINANCE_KLINE_INTERVAL = "1m"
BINANCE_BUFFER_SIZE = 10

# Asset name (from RTDS) → Binance symbol
ASSET_TO_BINANCE = {"BTC": "btcusdt", "ETH": "ethusdt", "SOL": "solusdt", "XRP": "xrpusdt"}

# Coinbase Momentum Feed (US-compatible alternative)
COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"
COINBASE_PRODUCTS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
# Coinbase product_id → internal symbol (same as Binance mapping)
COINBASE_TO_SYMBOL = {"BTC-USD": "btcusdt", "ETH-USD": "ethusdt", "SOL-USD": "solusdt", "XRP-USD": "xrpusdt"}

# Feed source: "binance" or "coinbase"
PRICE_FEED_SOURCE = os.environ.get("PRICE_FEED_SOURCE", "binance")

# Binance Futures (liquidation + funding data)
BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
# Map internal asset names to futures symbols
ASSET_TO_FUTURES = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
FUTURES_TO_ASSET = {v: k for k, v in ASSET_TO_FUTURES.items()}

# ============================================================================
# Arbitrage Types & Constants
# ============================================================================

GAMMA_API_URL = "https://gamma-api.polymarket.com"
ARB_SCAN_INTERVAL = 15          # seconds between scans
ARB_SLIPPAGE_BUFFER = 0.005     # 0.5% slippage buffer on pair_cost
ARB_ORDER_VERIFY_SECS = 15      # seconds to wait for fill confirmation
ARB_MARKET_BUFFER_SECS = 180    # skip markets expiring within 3 minutes
ARB_UNWIND_MAX_RETRIES = 3      # retries for orphaned leg unwind
ARB_MIDPOINT_DRIFT_LIMIT = 0.005  # abort leg 2 if midpoint drifted >0.5%
ARB_MIN_UNWIND_PRICE_PCT = 0.80   # circuit breaker: abort unwind if best_bid < 80% of ~0.50


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity on a 15-min market."""
    slug: str
    market_title: str
    up_token_id: str
    down_token_id: str
    up_price: float
    down_price: float
    pair_cost: float
    fee_up: float
    fee_down: float
    net_profit_per_share: float
    shares: float
    expires_at: float


@dataclass
class ArbResult:
    """Result of an arbitrage execution attempt."""
    success: bool
    up_order_id: str = ""
    down_order_id: str = ""
    shares: float = 0.0
    pair_cost: float = 0.0
    total_fees: float = 0.0
    net_profit: float = 0.0
    error: str = ""
    unwound: bool = False


@dataclass
class AccumulatorPosition:
    """Tracks state of an accumulator pair accumulation cycle."""
    slug: str
    window_secs: int
    state: AccumulatorState
    up_token_id: str
    down_token_id: str
    market_end_time: datetime  # UTC-aware
    entry_time: datetime  # UTC-aware
    up_qty: float = 0.0
    down_qty: float = 0.0
    up_avg_price: float = 0.0
    down_avg_price: float = 0.0
    up_fee_paid: float = 0.0
    down_fee_paid: float = 0.0
    pair_cost: float = 0.0
    is_fully_hedged: bool = False
    pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    up_order_id: Optional[str] = None
    down_order_id: Optional[str] = None
    up_order_time: Optional[datetime] = None
    down_order_time: Optional[datetime] = None
    reprice_count: int = 0
    target_shares: float = 0.0
    pending_order_price: float = 0.0
    pending_up_price: float = 0.0
    pending_down_price: float = 0.0
    up_reprice_count: int = 0
    down_reprice_count: int = 0
    condition_id: str = ""
    blocked_cycles: int = 0
    first_fill_time: Optional[datetime] = None  # set when first leg fills (hedge deadline clock)


@dataclass
class RedemptionEvent:
    """Event pushed to redeemer queue after accumulator settlement."""
    condition_id: str       # hex "0x..." from Gamma API
    slug: str               # market slug for logging
    winning_side: str       # "up" or "down"
    shares: float           # matched shares to redeem
    settled_at: float       # time.time() timestamp
    token_ids: list = field(default_factory=list)  # orphan sweep: token_ids for DB cleanup


def parse_window_from_slug(slug: str) -> int:
    """Parse market window duration (seconds) from slug.

    e.g., "btc-updown-5m-1770937500" -> 300
          "eth-updown-15m-1770937200" -> 900
    Falls back to 900 (15 min) if unparseable.
    """
    parts = slug.split('-')
    for part in parts:
        if part.endswith('m') and part[:-1].isdigit():
            return int(part[:-1]) * 60
    return 900
