"""Fee calculators for Polymarket and Kalshi prediction markets.

Polymarket uses a dynamic fee: fee = coefficient * price * (1 - price).
Coefficients vary by market category.

Kalshi uses a rounded per-contract fee: ceil(coeff * contracts * price * (1-price)).
"""
import math


# Polymarket fee coefficients by category (as of March 2026).
# geopolitics: 0 (fee-free for geopolitical markets)
POLYMARKET_FEE_COEFFICIENTS: dict[str, float] = {
    "crypto": 0.072,
    "economics": 0.060,
    "sports": 0.030,
    "politics": 0.040,
    "geopolitics": 0.0,
    "weather": 0.050,
    "default": 0.050,
}


def polymarket_fee(price: float, category: str = "weather") -> float:
    """Polymarket taker fee per share.

    Formula: fee = coefficient * price * (1 - price)

    Args:
        price: YES token price in [0, 1].
        category: Market category slug (see POLYMARKET_FEE_COEFFICIENTS).

    Returns:
        Fee in dollars per share (same unit as price).
    """
    if price <= 0.0 or price >= 1.0:
        return 0.0
    coeff = POLYMARKET_FEE_COEFFICIENTS.get(category, POLYMARKET_FEE_COEFFICIENTS["default"])
    return coeff * price * (1.0 - price)


def polymarket_ev_net(
    true_prob: float,
    market_price: float,
    category: str = "weather",
) -> float:
    """Net expected value per share for a Polymarket YES buy after fees.

    EV_gross = true_prob * (1 - market_price) - (1 - true_prob) * market_price
    EV_net   = EV_gross - fee(market_price, category)

    Returns EV per share (positive = profitable bet).
    """
    if market_price <= 0.0 or market_price >= 1.0:
        return 0.0
    ev_gross = true_prob * (1.0 - market_price) - (1.0 - true_prob) * market_price
    fee = polymarket_fee(market_price, category)
    return round(ev_gross - fee, 6)


def kalshi_taker_fee(price: float, contracts: int = 1) -> float:
    """Kalshi taker fee per contract in dollars.

    Formula: fee = ceil(7 * contracts * price * (1 - price)) / 100 / contracts

    The 7 coefficient represents 7 cents per contract at max fee density (p=0.5).

    Args:
        price: Contract price in [0, 1].
        contracts: Number of contracts being purchased (affects rounding).

    Returns:
        Per-contract fee in dollars.
    """
    if price <= 0.0 or price >= 1.0:
        return 0.0
    if contracts < 1:
        contracts = 1
    total_cents = math.ceil(7 * contracts * price * (1.0 - price))
    return total_cents / 100.0 / contracts


def kalshi_maker_fee(price: float, contracts: int = 1) -> float:
    """Kalshi maker fee per contract in dollars (approx 4x lower than taker).

    Formula: fee = ceil(1.75 * contracts * price * (1 - price)) / 100 / contracts
    """
    if price <= 0.0 or price >= 1.0:
        return 0.0
    if contracts < 1:
        contracts = 1
    total_cents = math.ceil(1.75 * contracts * price * (1.0 - price))
    return total_cents / 100.0 / contracts


def arb_break_even_spread(
    poly_price: float,
    kalshi_price: float,
    poly_category: str = "sports",
    contracts: int = 10,
) -> float:
    """Net profit/loss for a cross-platform Polymarket/Kalshi arbitrage.

    Strategy: buy YES cheaper side, buy NO on more expensive side.
    Combined cost = poly_price + (1 - kalshi_price) + poly_fee + kalshi_taker_fee
    Profit = 1.0 - combined_cost (guaranteed $1 payout if one leg wins).

    Args:
        poly_price: Polymarket YES price.
        kalshi_price: Kalshi YES price.
        poly_category: Polymarket market category for fee calculation.
        contracts: Kalshi contract count (affects rounding of Kalshi fees).

    Returns:
        Net profit per dollar invested (positive = profitable arb).
    """
    poly_fee_val = polymarket_fee(poly_price, poly_category)
    kalshi_fee_val = kalshi_taker_fee(kalshi_price, contracts)

    combined_cost = poly_price + (1.0 - kalshi_price) + poly_fee_val + kalshi_fee_val
    profit = 1.0 - combined_cost
    return round(profit, 4)
