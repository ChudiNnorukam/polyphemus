---
name: polymarket-pro
description: Production-grade Polymarket trading via py_clob_client with maker orders, position tracking, market discovery, and CTF redemption. Superior to split+CLOB approaches.
user-invocable: true
metadata: {"openclaw": {"emoji": "📈", "requires": {"env": ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_API_KEY", "POLYMARKET_SECRET", "POLYMARKET_PASSPHRASE"]}, "primaryEnv": "POLYMARKET_PRIVATE_KEY"}}
---

# Polymarket Pro Trading Skill

Trade Polymarket prediction markets using production-grade maker orders via py_clob_client. Browse markets, execute trades, track positions, and redeem resolved outcomes.

## Setup

Install dependencies:
```bash
cd {baseDir}/scripts && pip install -r requirements.txt
```

Required environment variables:
- `POLYMARKET_PRIVATE_KEY` — wallet private key (EOA)
- `POLYMARKET_API_KEY` — CLOB API key
- `POLYMARKET_SECRET` — CLOB API secret
- `POLYMARKET_PASSPHRASE` — CLOB API passphrase
- `POLYMARKET_SIGNATURE_TYPE` — 0 (EOA), 1 (Proxy), or 2 (Safe). Default: 0.
- `POLYGON_RPC_URL` — (optional) Polygon RPC. Default: polygon.drpc.org

## Commands

All commands run via: `python3 {baseDir}/scripts/polymarket_cli.py <command> [args]`

### Balance & Wallet

```bash
# Check USDC balance
python3 {baseDir}/scripts/polymarket_cli.py balance

# Check share balance for a token
python3 {baseDir}/scripts/polymarket_cli.py shares <token_id>
```

### Market Discovery

```bash
# Search markets by keyword
python3 {baseDir}/scripts/polymarket_cli.py search "BTC" --limit 10

# Get market details by slug
python3 {baseDir}/scripts/polymarket_cli.py market <slug>

# Discover current BTC 5m updown market
python3 {baseDir}/scripts/polymarket_cli.py discover btc

# Get midpoint price for a token
python3 {baseDir}/scripts/polymarket_cli.py midpoint <token_id>

# Get order book for a token
python3 {baseDir}/scripts/polymarket_cli.py orderbook <token_id>
```

### Trading

```bash
# Buy shares — maker order (post-only, earns fee rebates)
python3 {baseDir}/scripts/polymarket_cli.py buy <token_id> <price> <size> [--taker]

# Sell shares — maker order (post-only)
python3 {baseDir}/scripts/polymarket_cli.py sell <token_id> <price> <size> [--taker]

# Cancel an open order
python3 {baseDir}/scripts/polymarket_cli.py cancel <order_id>

# Cancel all open orders
python3 {baseDir}/scripts/polymarket_cli.py cancel-all
```

### Positions & History

```bash
# List current positions (from Data API)
python3 {baseDir}/scripts/polymarket_cli.py positions

# Trade history (last N days)
python3 {baseDir}/scripts/polymarket_cli.py history --days 7

# PnL summary
python3 {baseDir}/scripts/polymarket_cli.py pnl --days 30
```

### Redemption

```bash
# List redeemable positions
python3 {baseDir}/scripts/polymarket_cli.py redeemable

# Redeem a resolved market by condition ID
python3 {baseDir}/scripts/polymarket_cli.py redeem <condition_id>
```

## Important Trading Notes

- **Maker orders** (default) are post-only — they sit on the order book and earn fee rebates. They may not fill instantly.
- **Taker orders** (--taker flag) fill immediately against resting orders but pay fees (~1-3%).
- Minimum order size is **5 shares**.
- Prices are between 0.01 and 0.99 (probability in dollars).
- Always check the midpoint before placing orders. Buy below midpoint for maker, at/above for taker.
- For crypto updown markets, the slug format is `{asset}-updown-{window}-{epoch}` (e.g., `btc-updown-5m-1739750400`).

## Security

- Never log or display the private key or API credentials.
- Use a dedicated wallet with minimal balance for trading.
- Withdraw profits to a separate secure wallet regularly.

## Error Handling

If a command fails:
1. Check that environment variables are set correctly.
2. Verify the token_id or slug is valid (use `search` or `discover` first).
3. For order failures, check balance with `balance` command.
4. For "Cloudflare" errors, the CLOB API may be rate-limiting — wait and retry.
