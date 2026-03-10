"""
Hyperliquid Funding Rate Farmer - Configuration
Delta-neutral: short HL perps + hold spot on Binance
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Hyperliquid Configuration
HL_API_URL = "https://api.hyperliquid.xyz"
HL_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
HL_PRIVATE_KEY = os.getenv("HL_PRIVATE_KEY", "")
HL_ACCOUNT_ADDRESS = os.getenv("HL_ACCOUNT_ADDRESS", "")
USE_TESTNET = os.getenv("HL_USE_TESTNET", "true").lower() == "true"

# Coins to monitor (Hyperliquid perp symbols)
# Tier 1: blue chips (easy to hedge on Binance spot)
WATCH_COINS_T1 = ["BTC", "ETH", "SOL", "ARB", "DOGE", "AVAX", "OP", "SUI", "APT", "BNB", "LTC"]
# Tier 2: mid-caps (hedgeable but thinner liquidity)
WATCH_COINS_T2 = ["INJ", "DYDX", "CRV", "MATIC", "LINK", "UNI", "AAVE", "MKR", "NEAR", "FTM"]
# Combined watch list
WATCH_COINS = WATCH_COINS_T1 + WATCH_COINS_T2
# Full universe scan: also check ALL coins for extreme rates (>50% APR)
FULL_UNIVERSE_SCAN = True
FULL_UNIVERSE_MIN_OI_USD = 500_000   # Only coins with >$500K open interest
FULL_UNIVERSE_MIN_APR = 50           # Only flag coins >50% APR in full scan

# Funding Rate Thresholds
# HL funding is hourly. 0.0001 = 0.01%/hr = ~88% APR
MIN_FUNDING_RATE = 0.0001       # 0.01%/hr minimum to consider
MIN_ANNUALIZED_RETURN = 0.05    # 5% net APR minimum after fees (realistic for current market)
RATE_LOOKBACK_HOURS = 24        # Average over last 24h for stability

# Position Sizing
CAPITAL_PER_PAIR = 500           # $500 per coin (split: $250 spot + $250 short)
MAX_CONCURRENT_PAIRS = 3         # Max 3 delta-neutral pairs at once
MAX_TOTAL_CAPITAL = 2000         # Hard cap on total deployed capital
LEVERAGE = 1                     # 1x leverage (delta neutral, no extra leverage)

# Entry/Exit Conditions
MIN_SUSTAINED_HOURS = 6          # Rate must be positive for 6+ consecutive hours
EXIT_WHEN_RATE_BELOW = 0.00005  # Exit if rate drops below 0.005%/hr
REBALANCE_DELTA_PCT = 0.03      # Rebalance if spot/perp delta exceeds 3%

# Fee Assumptions (for profit calculation)
HL_MAKER_FEE = 0.0002           # 0.02% maker
HL_TAKER_FEE = 0.0005           # 0.05% taker
BINANCE_SPOT_FEE = 0.001        # 0.1% spot trading fee
ROUND_TRIP_FEES = 2 * (HL_TAKER_FEE + BINANCE_SPOT_FEE)  # Entry + exit both sides

# Monitoring
SCAN_INTERVAL_SECS = 300         # Scan every 5 minutes
HISTORY_FETCH_INTERVAL = 3600    # Fetch full history every hour
LOG_TOP_N = 10                   # Log top N opportunities each scan

# Data & Logging
DATA_DIR = os.getenv("FUNDING_DATA_DIR", "funding-bot/data")
DATABASE_PATH = os.path.join(DATA_DIR, "funding_rates.db")
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Development
DRY_RUN = os.getenv("FUNDING_DRY_RUN", "true").lower() == "true"

# Constants
HOURS_PER_YEAR = 8760
BOT_VERSION = "2.0.0"
