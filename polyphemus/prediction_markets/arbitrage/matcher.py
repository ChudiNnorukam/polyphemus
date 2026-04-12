"""Match equivalent markets across Polymarket and Kalshi.

Matching strategies (by reliability):
1. Numeric matching: BTC price level markets with same strike
2. Team matching: Sports markets with same teams + date
3. Fuzzy text: Everything else (requires human review)
"""
import re
from difflib import SequenceMatcher

# Common team name normalizations
TEAM_ALIASES = {
    "lakers": "los angeles lakers",
    "celtics": "boston celtics",
    "warriors": "golden state warriors",
    "yankees": "new york yankees",
    "dodgers": "los angeles dodgers",
    # Add more as needed
}

def normalize_text(text: str) -> str:
    """Lowercase, remove punctuation, normalize whitespace."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def similarity(a: str, b: str) -> float:
    """Compute text similarity between two strings."""
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    return SequenceMatcher(None, a_norm, b_norm).ratio()

def extract_btc_strike(title: str) -> float:
    """Extract BTC price strike from a market title.

    Handles: "Bitcoin above $80,000?", "BTC price > 80000", etc.
    """
    title_lower = title.lower()
    if "btc" not in title_lower and "bitcoin" not in title_lower:
        return None

    # Find dollar amounts
    m = re.search(r'\$?([\d,]+(?:\.\d+)?)', title)
    if m:
        return float(m.group(1).replace(",", ""))
    return None

def extract_date(title: str) -> str:
    """Extract date from market title if present."""
    # Match patterns like "April 15", "Apr 15, 2026", "2026-04-15"
    patterns = [
        r'(\d{4}-\d{2}-\d{2})',
        r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}(?:,?\s*\d{4})?)',
    ]
    for pat in patterns:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            return m.group(1)
    return None

def match_markets(poly_markets: list, kalshi_markets: list,
                  min_similarity: float = 0.65) -> list:
    """Find equivalent markets across platforms.

    Returns: list of {
        poly_market: dict, kalshi_market: dict,
        match_type: str, confidence: float,
        resolution_risk: str
    }
    """
    matches = []

    # Index Kalshi markets by BTC strike for fast numeric matching
    kalshi_btc = {}
    for km in kalshi_markets:
        strike = extract_btc_strike(km.get("title", ""))
        if strike:
            date = extract_date(km.get("title", ""))
            key = (strike, date)
            kalshi_btc[key] = km

    for pm in poly_markets:
        poly_title = pm.get("question", "") or pm.get("title", "")

        # Strategy 1: BTC numeric matching
        poly_strike = extract_btc_strike(poly_title)
        if poly_strike:
            poly_date = extract_date(poly_title)
            key = (poly_strike, poly_date)
            if key in kalshi_btc:
                matches.append({
                    "poly_market": pm,
                    "kalshi_market": kalshi_btc[key],
                    "match_type": "numeric_btc",
                    "confidence": 0.95,
                    "resolution_risk": "MEDIUM - verify both use same price feed",
                })
                continue

        # Strategy 2: Fuzzy text matching
        best_match = None
        best_score = 0

        for km in kalshi_markets:
            kalshi_title = km.get("title", "")
            score = similarity(poly_title, kalshi_title)
            if score > best_score:
                best_score = score
                best_match = km

        if best_match and best_score >= min_similarity:
            risk = "LOW" if best_score > 0.85 else "MEDIUM" if best_score > 0.75 else "HIGH"
            matches.append({
                "poly_market": pm,
                "kalshi_market": best_match,
                "match_type": "fuzzy_text",
                "confidence": round(best_score, 3),
                "resolution_risk": f"{risk} - review resolution criteria manually",
            })

    return matches
