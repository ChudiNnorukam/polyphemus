# Quick Start Guide

Get the liquidation bot running in 10 minutes.

## Step 1: Setup Local Environment (2 min)

```bash
cd /Users/chudinnorukam/Projects/business/liquidation-bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Verify installation
python3 -c "import web3; print('✅ web3 installed')"
```

## Step 2: Deploy Smart Contract (3 min)

### Option A: One-liner with Forge

```bash
# Install Foundry (if not already installed)
curl -L https://foundry.paradigm.xyz | bash
source ~/.bashrc

# Compile and deploy
forge create --rpc-url https://arb1.arbitrum.io/rpc \
  --private-key <YOUR_PRIVATE_KEY> \
  --constructor-args 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb 0xE592427A0AEce92De3Edee1F18E0157C05861564 \
  contracts/FlashLiquidator.sol:FlashLiquidator
```

This outputs something like:
```
Deployed to: 0xAbCdEf123456...
```

Copy that address.

### Option B: Via Remix (Even Easier)

1. Go to https://remix.ethereum.org
2. Create file: `contracts/FlashLiquidator.sol`
3. Copy-paste contract code from our file
4. Click "Compile"
5. Click "Deploy" with:
   - PoolAddressesProvider: `0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb`
   - UniswapRouter: `0xE592427A0AEce92De3Edee1F18E0157C05861564`
6. Copy deployed address

## Step 3: Configure Bot (2 min)

```bash
# Create .env file
cp .env.example .env

# Edit with your values
nano .env
```

Fill in:
- `ARBITRUM_RPC`: https://arb1.arbitrum.io/rpc (or your own)
- `PRIVATE_KEY`: Private key of account with 0.1+ ARB for gas
- `LIQUIDATOR_CONTRACT`: Contract address from Step 2
- `MIN_PROFIT_USD`: 5 (adjust based on your risk tolerance)

## Step 4: Test the Bot (2 min)

```bash
# Run bot with verbose logging
LOG_LEVEL=DEBUG python3 run_liquidation_bot.py

# You should see:
# ✅ Connected to Arbitrum (block XXXXX)
# ✅ Starting scan...
# ℹ️ Fetched XXX unique borrowers...
```

Press `Ctrl+C` to stop.

## Step 5: Deploy to VPS (1 min)

```bash
# One command deployment
./deploy.sh 142.93.143.178 root <PRIVATE_KEY> <LIQUIDATOR_CONTRACT>

# Start the bot
ssh root@142.93.143.178 sudo systemctl start liquidation-bot

# Check it's running
ssh root@142.93.143.178 sudo systemctl status liquidation-bot
```

## Done!

Your bot is now running 24/7. Check status:

```bash
# View recent activity
ssh root@142.93.143.178 sudo journalctl -u liquidation-bot -n 20

# View real-time logs
ssh root@142.93.143.178 sudo journalctl -u liquidation-bot -f

# Check performance
ssh root@142.93.143.178 cat /opt/liquidation-bot/data/health_status.json | jq .
```

## Troubleshooting

### "connection failed"
Make sure you have the correct RPC URL. Test it:
```bash
curl https://arb1.arbitrum.io/rpc -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
```

### "Invalid private key"
Private key must:
- Start with `0x`
- Be 66 characters total (0x + 64 hex chars)
- Only be in SINGLE account (no comma-separated)

### "No liquidations found"
This is normal! Liquidatable positions are rare. The bot will find them when they appear.

### "Contract reverted"
Make sure contract has correct constructor args:
- PoolAddressesProvider: `0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb`
- UniswapRouter: `0xE592427A0AEce92De3Edee1F18E0157C05861564`

## Next Steps

1. **Enable Telegram notifications** (optional):
   - Get bot token: https://t.me/BotFather
   - Get chat ID: Send message to bot, check `curl https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Update `.env` with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`

2. **Monitor from anywhere**:
   - Check status: `ssh root@VPS cat /opt/liquidation-bot/data/health_status.json`
   - View database: `ssh root@VPS sqlite3 /opt/liquidation-bot/data/liquidations.db ".tables"`

3. **Optimize profitability**:
   - Lower `MIN_PROFIT_USD` if gas is cheap
   - Increase `CHECK_INTERVAL` for less RPC calls
   - Monitor different collateral/debt pairs

## Common Questions

**Q: Is my private key safe?**
A: Yes, it's only stored in `.env` which is never committed to git. The account should only have ~0.1 ARB for gas.

**Q: How much profit can I make?**
A: Depends on market conditions. Typically $50-500 per liquidation on Arbitrum. See examples in README.md.

**Q: Can I run multiple bots?**
A: Yes, use different accounts with different `LIQUIDATOR_CONTRACT` addresses.

**Q: What if contract runs out of gas?**
A: Bot estimates gas + 30% buffer. Very unlikely. If it happens, increase `gas_buffer_multiplier` in config.py.

**Q: How do I withdraw profits?**
A: Contract accumulates profits in USDC. Call `withdraw()` or `withdrawToken(usdc_address)` from contract owner account.

---

**Get started now:** `source venv/bin/activate && python3 run_liquidation_bot.py`
