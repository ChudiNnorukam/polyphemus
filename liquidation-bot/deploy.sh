#!/bin/bash
set -e

# Deployment script for Aave V3 Liquidation Bot
# Usage: ./deploy.sh <vps_host> <vps_user> <private_key> <liquidator_contract>

if [ $# -lt 4 ]; then
    echo "Usage: $0 <vps_host> <vps_user> <private_key> <liquidator_contract>"
    echo "Example: $0 142.93.143.178 root 0x... 0x..."
    exit 1
fi

VPS_HOST=$1
VPS_USER=$2
PRIVATE_KEY=$3
LIQUIDATOR_CONTRACT=$4

DEST="/opt/liquidation-bot"

echo "Deploying Liquidation Bot to $VPS_HOST..."

# 1. Create remote directory
echo "Creating remote directory..."
ssh "$VPS_USER@$VPS_HOST" "mkdir -p $DEST/data && mkdir -p $DEST/contracts"

# 2. Copy Python files
echo "Copying Python files..."
scp config.py monitor.py executor.py database.py healthcheck.py run_liquidation_bot.py \
    "$VPS_USER@$VPS_HOST:$DEST/"

# 3. Copy contract
echo "Copying contract..."
scp contracts/FlashLiquidator.sol "$VPS_USER@$VPS_HOST:$DEST/contracts/"

# 4. Copy requirements
echo "Copying requirements..."
scp requirements.txt "$VPS_USER@$VPS_HOST:$DEST/"

# 5. Copy service file
echo "Copying service file..."
scp liquidation-bot.service "$VPS_USER@$VPS_HOST:/tmp/"

# 6. Create .env file on remote
echo "Creating .env file..."
ssh "$VPS_USER@$VPS_HOST" cat > "$DEST/.env" <<EOF
ARBITRUM_RPC=https://arb1.arbitrum.io/rpc
PRIVATE_KEY=$PRIVATE_KEY
LIQUIDATOR_CONTRACT=$LIQUIDATOR_CONTRACT
MIN_PROFIT_USD=5.0
CHECK_INTERVAL=12
LOG_LEVEL=INFO
EOF

# 7. Setup Python environment
echo "Setting up Python environment..."
ssh "$VPS_USER@$VPS_HOST" <<'SCRIPT'
    cd /opt/liquidation-bot

    # Check Python version
    python3 --version

    # Create virtual environment
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi

    # Activate and install
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt

    # Syntax check
    python3 -m py_compile config.py
    python3 -m py_compile monitor.py
    python3 -m py_compile executor.py
    python3 -m py_compile database.py
    python3 -m py_compile healthcheck.py
    python3 -m py_compile run_liquidation_bot.py
SCRIPT

# 8. Install systemd service
echo "Installing systemd service..."
ssh "$VPS_USER@$VPS_HOST" <<'SCRIPT'
    # Create user if doesn't exist
    id -u liquidbot &>/dev/null || useradd -r -s /bin/bash liquidbot

    # Copy service file
    sudo cp /tmp/liquidation-bot.service /etc/systemd/system/

    # Set permissions
    sudo chown -R liquidbot:liquidbot /opt/liquidation-bot

    # Reload systemd
    sudo systemctl daemon-reload
SCRIPT

echo ""
echo "✅ Deployment complete!"
echo ""
echo "To start the bot:"
echo "  ssh $VPS_USER@$VPS_HOST sudo systemctl start liquidation-bot"
echo ""
echo "To check status:"
echo "  ssh $VPS_USER@$VPS_HOST sudo systemctl status liquidation-bot"
echo ""
echo "To view logs:"
echo "  ssh $VPS_USER@$VPS_HOST sudo journalctl -u liquidation-bot -f"
echo ""
echo "To enable auto-start:"
echo "  ssh $VPS_USER@$VPS_HOST sudo systemctl enable liquidation-bot"
echo ""
