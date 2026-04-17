"""Kelly criterion for binary prediction market bets.

Provides optimal fraction calculation, half-Kelly variant,
and position sizing in shares.

Reference: ArXiv 2412.14144 for prediction market Kelly formula.
"""


def kelly_fraction(true_prob: float, market_price: float) -> float:
    """Optimal Kelly fraction for a binary prediction market YES bet.

    Formula from ArXiv 2412.14144:
        f* = (Q - P) / (1 + Q)
    where:
        Q = true_prob / (1 - true_prob)  (true odds)
        P = market_price / (1 - market_price)  (market odds)

    This is equivalent to the classic formula:
        f* = (p*b - q) / b
    where b = (1 - market_price) / market_price (net odds).

    Args:
        true_prob: Our estimated true probability of the YES outcome.
        market_price: Current YES market price (implied probability).

    Returns:
        Fraction of bankroll to bet. Negative means no bet (edge is negative).
    """
    if true_prob <= 0.0 or true_prob >= 1.0:
        return 0.0
    if market_price <= 0.0 or market_price >= 1.0:
        return 0.0

    Q = true_prob / (1.0 - true_prob)
    P = market_price / (1.0 - market_price)
    f = (Q - P) / (1.0 + Q)
    return round(f, 4)


def half_kelly(true_prob: float, market_price: float) -> float:
    """Half-Kelly fraction.

    Betting half-Kelly sacrifices ~25% of long-run growth but dramatically
    reduces drawdowns and is more robust to probability estimation errors.

    Returns 0.0 if Kelly is negative (no bet).
    """
    f = kelly_fraction(true_prob, market_price)
    return round(max(0.0, f / 2.0), 4)


def position_size(
    bankroll: float,
    kelly_frac: float,
    price: float,
    max_bet: float | None = None,
    min_shares: int = 1,
) -> int:
    """Convert a Kelly fraction to a number of shares to purchase.

    Args:
        bankroll: Total available capital in dollars.
        kelly_frac: Kelly fraction in [0, 1] (use half_kelly() typically).
        price: YES token price per share (dollars per share).
        max_bet: Hard cap on dollar amount bet (optional).
        min_shares: Minimum shares to return (default 1; Polymarket min is 1 share).

    Returns:
        Number of shares as integer. Returns 0 if kelly_frac <= 0 or price <= 0.
    """
    if kelly_frac <= 0.0 or price <= 0.0 or bankroll <= 0.0:
        return 0

    dollar_bet = bankroll * kelly_frac
    if max_bet is not None and max_bet > 0:
        dollar_bet = min(dollar_bet, max_bet)

    shares = int(dollar_bet / price)
    if shares < min_shares:
        return 0  # Below minimum; don't place a fractional position

    return shares


def fractional_kelly(
    true_prob: float,
    market_price: float,
    fraction: float = 0.5,
) -> float:
    """Fractional Kelly with configurable fraction (default: half-Kelly).

    Args:
        true_prob: True probability estimate.
        market_price: Market implied probability.
        fraction: Fraction of full Kelly to use (0.5 = half-Kelly, 0.25 = quarter-Kelly).

    Returns:
        Adjusted Kelly fraction (clamped to >= 0).
    """
    f = kelly_fraction(true_prob, market_price)
    return round(max(0.0, f * fraction), 4)


def kelly_with_drawdown_feedback(
    edge: float,
    var: float,
    current_dd: float,
    dd_limit: float,
) -> float:
    """Continuous-approximation Kelly with a linear drawdown brake.

    Raw Kelly fraction under the Gaussian-return approximation is edge/var.
    As current_dd approaches dd_limit, the fraction scales linearly to 0,
    so the bet size shrinks automatically the closer the book is to its
    drawdown budget. At current_dd >= dd_limit the function returns 0 and
    refuses further exposure; the caller should already be in a cool-off.

    Args:
        edge: expected per-trade return (dollars or fraction — caller's unit).
            Must be > 0; negative or zero edge returns 0.0 (no bet).
        var: return variance (same units as edge, squared). Must be > 0;
            undefined variance returns 0.0.
        current_dd: current drawdown magnitude (>= 0, same units as dd_limit).
        dd_limit: drawdown ceiling (> 0). Reaching it halts new exposure.

    Returns:
        Kelly fraction scaled by the drawdown brake, clamped to >= 0 and
        rounded to 4 decimals to match the rest of this module.

    Rationale for linear (not quadratic) scaling:
        Linear is the conservative default at the top of the DD budget —
        it starts throttling immediately, whereas a squared brake would
        stay near full Kelly for most of the range and snap off at the end.
        For prediction-market bets with fat-tailed losses, earlier throttling
        is the right side to err on.
    """
    if edge <= 0.0 or var <= 0.0:
        return 0.0
    if dd_limit <= 0.0:
        return 0.0
    if current_dd >= dd_limit:
        return 0.0

    raw = edge / var
    dd_scale = 1.0 - (max(0.0, current_dd) / dd_limit)
    return round(max(0.0, raw * dd_scale), 4)
