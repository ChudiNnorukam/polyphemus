#!/bin/bash
# Passivbot Deployment Script for Bybit
# Deploy to VPS 142.93.143.178
# Usage: ./deploy-passivbot.sh

set -e

VPS_HOST="142.93.143.178"
VPS_USER="root"
INSTALL_DIR="/opt/passivbot"
REPO_URL="https://github.com/enarjord/passivbot.git"

echo "========================================="
echo "Passivbot Deployment to VPS"
echo "========================================="
echo "Target: $VPS_USER@$VPS_HOST:$INSTALL_DIR"
echo ""

# Check SSH connectivity
echo "[1/4] Checking SSH connectivity..."
if ! ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST "echo 'SSH OK'" &>/dev/null; then
    echo "ERROR: Cannot SSH to $VPS_HOST"
    exit 1
fi
echo "SSH OK"

# Deploy base installation
echo ""
echo "[2/4] Installing system dependencies and cloning Passivbot..."
ssh $VPS_USER@$VPS_HOST << 'REMOTE_INSTALL'
set -e

echo "Updating package manager..."
apt-get update
apt-get install -y python3.12 python3.12-venv python3.12-dev build-essential git curl wget ca-certificates

echo "Installing Rust toolchain..."
if ! command -v rustc &> /dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source $HOME/.cargo/env
fi

echo "Creating Passivbot installation directory..."
mkdir -p /opt/passivbot
cd /opt/passivbot

echo "Cloning Passivbot repository..."
if [ ! -d ".git" ]; then
    git clone --depth 1 https://github.com/enarjord/passivbot.git .
else
    echo "Repository already exists, updating..."
    git pull
fi

echo "Creating Python virtual environment..."
python3.12 -m venv venv
source venv/bin/activate

echo "Installing Python dependencies..."
pip install -U pip setuptools wheel
pip install -r requirements.txt

echo "Attempting to install Rust acceleration (optional)..."
pip install maturin 2>/dev/null || true
maturin develop --release 2>/dev/null || echo "Note: Rust extension build skipped (optional)"

echo "Creating directories..."
mkdir -p api_keys configs/live data logs

echo "Setting permissions..."
chmod 755 /opt/passivbot

echo "Installation complete!"
REMOTE_INSTALL

# Copy service file
echo ""
echo "[3/4] Installing systemd service..."
scp -q /Users/chudinnorukam/Projects/business/passivbot-deploy/passivbot.service \
    $VPS_USER@$VPS_HOST:/etc/systemd/system/passivbot.service

ssh $VPS_USER@$VPS_HOST << 'REMOTE_SERVICE'
systemctl daemon-reload
systemctl enable passivbot
echo "Service installed and enabled"
REMOTE_SERVICE

# Copy configuration files
echo ""
echo "[4/4] Uploading configuration templates..."
scp -q /Users/chudinnorukam/Projects/business/passivbot-deploy/configs/bybit-100-conservative.json \
    $VPS_USER@$VPS_HOST:/opt/passivbot/configs/bybit-100-conservative.json

scp -q /Users/chudinnorukam/Projects/business/passivbot-deploy/configs/bybit-500-balanced.json \
    $VPS_USER@$VPS_HOST:/opt/passivbot/configs/bybit-500-balanced.json

echo ""
echo "========================================="
echo "Deployment Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. SSH to VPS: ssh root@$VPS_HOST"
echo "2. Set up API keys: bash /opt/passivbot/setup-api-keys.sh"
echo "3. Choose a config:"
echo "   - Conservative ($100): configs/bybit-100-conservative.json"
echo "   - Balanced ($500):      configs/bybit-500-balanced.json"
echo "4. Copy to live: cp /opt/passivbot/configs/{chosen}.json /opt/passivbot/configs/live/active.json"
echo "5. Backtest: cd /opt/passivbot && python3 src/passivbot.py --backtest --config /opt/passivbot/configs/live/active.json"
echo "6. Start bot: systemctl start passivbot"
echo "7. Monitor: journalctl -u passivbot -f"
echo ""
echo "API keys location: /opt/passivbot/api_keys/api-keys.json"
echo "Live config:      /opt/passivbot/configs/live/active.json"
echo ""
