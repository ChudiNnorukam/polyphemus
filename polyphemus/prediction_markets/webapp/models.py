"""Pydantic models for the trading dashboard."""
from pydantic import BaseModel


class Opportunity(BaseModel):
    id: str
    scanner_type: str          # "weather" | "kalshi" | "arbitrage"
    platform: str              # "polymarket" | "kalshi" | "cross-platform"
    title: str
    city: str | None = None
    city_display: str | None = None
    market_date: str | None = None
    temp: int | None = None
    unit: str | None = None
    direction: str = "BUY"
    market_price: float = 0.0
    forecast_prob: float | None = None
    edge: float = 0.0
    ev_net: float = 0.0
    kelly: float = 0.0
    token_id: str | None = None
    question: str | None = None
    question_type: str | None = None  # "cumulative_higher" | "cumulative_lower" | "bucket"
    countdown: str | None = None
    forecast_temp: float | None = None
    # Arbitrage-specific
    poly_price: float | None = None
    kalshi_price: float | None = None
    net_profit: float | None = None
    confidence: float | None = None
    # Metadata
    scanned_at: str = ""
    cross_validated: bool = False


class PaperTrade(BaseModel):
    id: int
    created_at: str
    city: str
    market_date: str
    temp: int
    unit: str
    direction: str
    question_type: str = "bucket"
    question: str | None = None
    market_price: float
    forecast_prob: float
    forecast_temp: float | None = None
    edge: float
    ev_net: float
    kelly: float
    hypothetical_size: float
    token_id: str | None = None
    resolved: bool = False
    resolution_outcome: str | None = None
    pnl: float | None = None


class AppSettings(BaseModel):
    bankroll: float = 200.0
    weather_threshold: float = 0.10
    weather_min_ev: float = 0.01
    weather_min_kelly: float = 0.05
    arb_min_spread: float = 0.01
    weather_scan_interval_min: int = 5
    kalshi_scan_interval_min: int = 10
    arb_scan_interval_min: int = 15
    max_positions_per_city_date: int = 3
    max_total_deployment_pct: float = 0.50
    max_single_position_pct: float = 0.05
