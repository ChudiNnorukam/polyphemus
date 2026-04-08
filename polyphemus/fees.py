"""Canonical Polymarket fee calculations.

Single source of truth for fee math. Both live bot and research code
should import from here. Never hardcode fee constants elsewhere.

Polymarket fee formula (from docs):
    fee = shares * fee_rate * price * (1 - price)

Fee peaks at price=0.50 and approaches zero at extremes (0 or 1).
Makers pay zero fees and receive rebates.

Fee rates by category (global platform, as of April 2026):
    Crypto:                   7.2% taker, 20% maker rebate
    Sports:                   3.0% taker, 25% maker rebate
    Finance/Politics/Tech:    4.0% taker, 25% maker rebate
    Economics/Culture/Weather: 5.0% taker, 25% maker rebate
    Geopolitics/World events: 0.0% taker, no rebate

NOTE: The live accumulator.py uses 0.0624 as the fee rate constant,
which may reflect a different era or fee tier. This module uses the
current documented rates. If live fees diverge, update FEE_RATES here
and all consumers will pick up the change.
"""

from typing import Optional


# --- Fee rate constants (update here when Polymarket changes fees) ---

FEE_RATES: dict[str, float] = {
    "crypto": 0.072,
    "sports": 0.030,
    "finance": 0.040,
    "politics": 0.040,
    "tech": 0.040,
    "economics": 0.050,
    "culture": 0.050,
    "weather": 0.050,
    "geopolitics": 0.000,
    "world_events": 0.000,
}

MAKER_REBATE_PCT: dict[str, float] = {
    "crypto": 0.20,
    "sports": 0.25,
    "finance": 0.25,
    "politics": 0.25,
    "tech": 0.25,
    "economics": 0.25,
    "culture": 0.25,
    "weather": 0.25,
    "geopolitics": 0.00,
    "world_events": 0.00,
}

DEFAULT_CATEGORY = "crypto"


def fee_rate_for_category(category: str = DEFAULT_CATEGORY) -> float:
    """Return the taker fee rate for a market category."""
    return FEE_RATES.get(category.lower(), FEE_RATES[DEFAULT_CATEGORY])


def taker_fee_per_share(price: float, category: str = DEFAULT_CATEGORY) -> float:
    """Fee per share for a taker order at a given price.

    Args:
        price: Entry price (0.01 to 0.99).
        category: Market category (crypto, sports, geopolitics, etc.).

    Returns:
        Fee in USDC per share (always >= 0).
    """
    rate = fee_rate_for_category(category)
    return rate * price * (1.0 - price)


def taker_fee(price: float, shares: float, category: str = DEFAULT_CATEGORY) -> float:
    """Total taker fee for an order.

    Args:
        price: Entry price per share.
        shares: Number of shares.
        category: Market category.

    Returns:
        Total fee in USDC.
    """
    return taker_fee_per_share(price, category) * shares


def maker_rebate_per_share(price: float, category: str = DEFAULT_CATEGORY) -> float:
    """Rebate per share for a maker order (negative cost = income).

    Makers receive a percentage of the fees collected from takers on
    their fills. Returns a positive number (income to the maker).
    """
    rebate_pct = MAKER_REBATE_PCT.get(category.lower(), 0.0)
    # Rebate is a fraction of what a taker would pay
    taker = taker_fee_per_share(price, category)
    return taker * rebate_pct


def maker_rebate(price: float, shares: float, category: str = DEFAULT_CATEGORY) -> float:
    """Total maker rebate for an order (positive = income)."""
    return maker_rebate_per_share(price, category) * shares


def net_fee_per_share(price: float, mode: str = "taker", category: str = DEFAULT_CATEGORY) -> float:
    """Net fee per share: positive for taker (cost), negative for maker (rebate)."""
    if mode == "maker":
        return -maker_rebate_per_share(price, category)
    return taker_fee_per_share(price, category)


def breakeven_wr(
    entry_price: float,
    mode: str = "taker",
    category: str = DEFAULT_CATEGORY,
) -> float:
    """Win rate needed to break even at a given entry price after fees.

    For a binary outcome market (resolves to $1.00 or $0.00):
        Win PnL per share = (1.00 - entry_price - fee)
        Loss PnL per share = (0.00 - entry_price - fee)
        Break-even: WR * win_pnl + (1-WR) * loss_pnl = 0

    Returns:
        Break-even win rate as a float (e.g., 0.52 = 52%).
    """
    fee = net_fee_per_share(entry_price, mode, category)
    # win_pnl = 1.0 - entry - fee, loss_pnl = 0.0 - entry - fee
    # WR * (1 - entry - fee) + (1 - WR) * (-entry - fee) = 0
    # WR * (1 - entry - fee) - entry - fee + WR * (entry + fee) = 0
    # WR * (1 - entry - fee + entry + fee) = entry + fee
    # WR = (entry + fee) / 1.0
    return entry_price + fee


def fee_adjusted_pnl(
    entry_price: float,
    is_win: bool,
    shares: float,
    mode: str = "taker",
    category: str = DEFAULT_CATEGORY,
) -> float:
    """Compute P&L for a single trade including fees.

    Args:
        entry_price: Price paid per share.
        is_win: Whether the market resolved in our favor.
        shares: Number of shares held.
        mode: "taker" or "maker".
        category: Market category.

    Returns:
        Net P&L in USDC (positive = profit).
    """
    fee = net_fee_per_share(entry_price, mode, category)
    if is_win:
        pnl_per_share = 1.0 - entry_price - fee
    else:
        pnl_per_share = 0.0 - entry_price - fee
    return pnl_per_share * shares


def round_trip_cost(
    entry_price: float,
    exit_price: Optional[float] = None,
    entry_mode: str = "taker",
    exit_mode: str = "taker",
    category: str = DEFAULT_CATEGORY,
) -> float:
    """Total fee cost for a round trip (entry + exit) per share.

    For resolved markets, exit is free (market resolves to 0 or 1).
    For early exits via sell order, both legs incur fees.
    """
    entry_fee = net_fee_per_share(entry_price, entry_mode, category)
    if exit_price is not None:
        exit_fee = net_fee_per_share(exit_price, exit_mode, category)
    else:
        exit_fee = 0.0  # resolved markets have no exit fee
    return entry_fee + exit_fee
