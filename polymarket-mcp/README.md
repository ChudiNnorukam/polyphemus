# Polymarket CLOB MCP Server

Read-only MCP server that exposes Polymarket market data, portfolio info, and trade analytics as Claude Code tools.

## Setup

```bash
cd polymarket-mcp
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Fill in your credentials in .env
```

Register with Claude Code:
```bash
claude mcp add polymarket-clob --scope user -- /path/to/polymarket-mcp/.venv/bin/python3 /path/to/polymarket-mcp/server.py
```

## Tools (13)

### Market Data (no auth needed)
| Tool | Description |
|------|-------------|
| `get_market` | Market details by condition_id |
| `search_markets` | Browse active/sampling markets (paginated) |
| `get_order_book` | Full order book with bids, asks, spread, midpoint |
| `get_price` | Best bid/ask/mid/spread for a token |
| `get_server_time` | CLOB server timestamp |

### Portfolio (requires CLOB credentials)
| Tool | Description |
|------|-------------|
| `get_balance` | USDC wallet balance |
| `get_positions` | Open positions with live midpoints and unrealized P&L |
| `get_open_orders` | Currently open limit orders |

### Performance Analytics (requires performance.db path)
| Tool | Description |
|------|-------------|
| `get_trade_history` | Recent closed trades with P&L (limit param) |
| `get_stats` | Overall stats: trades, WR, P&L, resolution WR |
| `get_daily_pnl` | Realized P&L for a specific UTC date |
| `get_wr_by_bucket` | Win rate by 0.1-wide entry price buckets per asset |
| `get_trade_summary` | Plain-English performance summary |

## Environment Variables

```
POLYMARKET_PRIVATE_KEY=        # Wallet private key
POLYMARKET_WALLET_ADDRESS=     # Wallet address
POLYMARKET_CLOB_API_KEY=       # From polymarket.com/settings?tab=api
POLYMARKET_CLOB_SECRET=        # Or derive via py_clob_client
POLYMARKET_CLOB_PASSPHRASE=
POLYMARKET_SIG_TYPE=0          # 0=EOA, 1=POLY_PROXY (MagicLink)
POLYMARKET_CHAIN_ID=137        # Polygon mainnet
PERFORMANCE_DB_PATH=           # Path to performance.db
```

## Usage in Claude Code

After registering, ask naturally:
- "What's my Polymarket balance?"
- "Show my recent trades"
- "How am I performing?"
- "What's the order book for [token_id]?"
- "Show BTC win rate by price bucket"
