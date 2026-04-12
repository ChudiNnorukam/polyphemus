"""Divergence detector: compare forecast probability distribution against market prices.

Identifies mispricings above a configurable threshold and computes
edge, expected value (net of fees), and Kelly fraction for each opportunity.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Polymarket fee coefficient for weather markets
_WEATHER_FEE_COEFF = 0.050

# Estimated half-spread for weather markets (conservative).
# Paper trades at midpoint overstate edge; this haircut accounts for
# the cost of crossing the spread when actually executing.
_SPREAD_HAIRCUT = 0.015


def detect_divergences(
    market_data: dict,
    forecast_dist: dict[int, float],
    threshold: float = 0.10,
) -> list[dict]:
    """Compare market prices against forecast probabilities.

    Args:
        market_data: Parsed market dict with "buckets" list (from scanner.parse_temperature_markets).
        forecast_dist: dict mapping integer temperature -> probability (from forecast.forecast_to_distribution).
        threshold: Minimum |edge| = |forecast_prob - market_price| to flag.

    Returns:
        List of opportunity dicts sorted by ev_net descending:
        {
            temp: int,
            market_price: float,
            forecast_prob: float,
            edge: float,           # positive = market is cheap (BUY)
            direction: "BUY"|"SELL",
            ev_gross: float,       # EV before fees per share
            ev_net: float,         # EV after fees per share
            token_id: str | None,
            question: str | None,
        }
    """
    opportunities: list[dict] = []

    from .forecast import forecast_cumulative_prob

    # We need forecast_temp, unit, and days_until for cumulative probabilities
    forecast_temp = market_data.get("_forecast_temp")
    unit = market_data.get("_unit", "C")
    days_until = market_data.get("_days_until", 1)

    for bucket in market_data.get("buckets", []):
        temp = parse_temp_from_label(bucket.get("temp_label", ""))
        if temp is None:
            logger.debug("Could not parse temp from label: %r", bucket.get("temp_label"))
            continue

        market_price = bucket.get("yes_price")
        if market_price is None:
            continue

        # Clamp to avoid log(0) / division-by-zero downstream
        # Skip extreme prices (settled/untradeable markets)
        market_price = float(market_price)
        if not (0.02 < market_price < 0.98):
            continue

        # Detect question type: "or higher", "or lower", or bucket ("between X-Y")
        question = (bucket.get("question") or "").lower()
        q_type = classify_question(question)

        if q_type == "cumulative_higher" and forecast_temp is not None:
            forecast_prob = forecast_cumulative_prob(temp, forecast_temp, unit, days_until=days_until)
        elif q_type == "cumulative_lower" and forecast_temp is not None:
            forecast_prob = forecast_cumulative_prob(temp, forecast_temp, unit, direction="or_lower", days_until=days_until)
        else:
            # Bucket/exact probability
            forecast_prob = forecast_dist.get(temp, 0.0)

        raw_edge = forecast_prob - market_price

        if abs(raw_edge) < threshold:
            continue

        direction = "BUY" if raw_edge > 0 else "SELL"

        # Apply spread haircut: BUY pays ask (higher), SELL receives bid (lower)
        if direction == "BUY":
            execution_price = min(market_price + _SPREAD_HAIRCUT, 0.98)
        else:
            execution_price = max(market_price - _SPREAD_HAIRCUT, 0.02)

        edge = forecast_prob - execution_price if direction == "BUY" else raw_edge

        # Expected value per share (using execution price, not midpoint)
        if direction == "BUY":
            ev_gross = forecast_prob * (1.0 - execution_price) - (1.0 - forecast_prob) * execution_price
        else:
            ev_gross = (1.0 - forecast_prob) * execution_price - forecast_prob * (1.0 - execution_price)

        # Polymarket weather fee = coeff * price * (1 - price)
        fee = _WEATHER_FEE_COEFF * execution_price * (1.0 - execution_price)
        ev_net = ev_gross - fee

        opportunities.append({
            "temp": temp,
            "market_price": round(execution_price, 4),  # use execution price (spread-adjusted)
            "midpoint_price": round(market_price, 4),    # original midpoint for reference
            "forecast_prob": round(forecast_prob, 4),
            "edge": round(edge, 4),
            "direction": direction,
            "ev_gross": round(ev_gross, 4),
            "ev_net": round(ev_net, 4),
            "token_id": bucket.get("yes_token_id"),
            "question": bucket.get("question"),
        })

    # Sort by ev_net descending (best opportunities first)
    opportunities.sort(key=lambda x: x["ev_net"], reverse=True)
    return opportunities


def classify_question(question: str) -> str:
    """Classify a market question as bucket, cumulative_higher, or cumulative_lower.

    Returns:
        "cumulative_higher" for "X or higher", "at least X", "above X"
        "cumulative_lower" for "X or lower", "at most X", "below X"
        "bucket" for "between X-Y", "exactly X", or unrecognized
    """
    q = question.lower()
    if "or higher" in q or "at least" in q or "above" in q or "≥" in q:
        return "cumulative_higher"
    if "or lower" in q or "at most" in q or "below" in q or "≤" in q:
        return "cumulative_lower"
    return "bucket"


def parse_temp_from_label(label: str) -> int | None:
    """Parse integer temperature from a bucket label.

    Handles formats:
      "26 deg C", "26degC", "26°C", "80°F", "80 F", "26", "above 30°C",
      "Below 60°F", "≥ 26", "80-85°F" (takes lower bound).
    Returns None if no integer can be parsed.
    """
    if not label:
        return None

    # Match first integer in the label (handles all the above patterns)
    m = re.search(r"(\d+)", label)
    if m:
        return int(m.group(1))

    return None


def compute_kelly(edge: float, market_price: float, direction: str = "BUY") -> float:
    """Optimal Kelly fraction for a prediction market bet.

    For BUY (YES bet):
      p = forecast_prob, b = (1 - market_price) / market_price
    For SELL (NO bet):
      p = 1 - forecast_prob, b = market_price / (1 - market_price)

    Formula: f* = (p*b - q) / b
    Returns fraction of bankroll (clamped to [0, 1]).
    """
    if market_price <= 0.0 or market_price >= 1.0:
        return 0.0

    forecast_prob = market_price + edge
    if forecast_prob <= 0.0 or forecast_prob >= 1.0:
        return 0.0

    if direction == "SELL":
        # NO bet: we believe event WON'T happen
        p = 1.0 - forecast_prob  # true prob of NO
        b = market_price / (1.0 - market_price)  # net odds for NO
    else:
        # YES bet: we believe event WILL happen
        p = forecast_prob
        b = (1.0 - market_price) / market_price  # net odds for YES

    q = 1.0 - p
    kelly = (p * b - q) / b
    return round(max(0.0, min(1.0, kelly)), 4)
