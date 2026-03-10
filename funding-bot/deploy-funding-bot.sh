#!/bin/bash
# Funding Rate Bot Deployment Script
# Deploy to VPS 142.93.143.178

set -e

VPS_HOST="142.93.143.178"
VPS_USER="root"
INSTALL_DIR="/opt/funding-bot"

echo "========================================="
echo "Funding Rate Arbitrage Bot Deployment"
echo "========================================="
echo "Target: $VPS_USER@$VPS_HOST:$INSTALL_DIR"
echo ""

# Check SSH connectivity
echo "[1/3] Checking SSH connectivity..."
if ! ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST "echo 'SSH OK'" &>/dev/null; then
    echo "ERROR: Cannot SSH to $VPS_HOST"
    exit 1
fi

# Deploy installation
echo ""
echo "[2/3] Setting up environment..."
ssh $VPS_USER@$VPS_HOST << 'REMOTE_INSTALL'
set -e
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev build-essential git
mkdir -p /opt/funding-bot/{logs,data}
cd /opt/funding-bot
python3.11 -m venv venv
source venv/bin/activate
pip install -U pip setuptools wheel
REMOTE_INSTALL

# Copy files
echo ""
echo "[3/3] Uploading bot files..."
scp -q config.py $VPS_USER@$VPS_HOST:/opt/funding-bot/
scp -q rate_scanner.py $VPS_USER@$VPS_HOST:/opt/funding-bot/
scp -q position_manager.py $VPS_USER@$VPS_HOST:/opt/funding-bot/
scp -q main.py $VPS_USER@$VPS_HOST:/opt/funding-bot/
scp -q requirements.txt $VPS_USER@$VPS_HOST:/opt/funding-bot/
scp -q .env.example $VPS_USER@$VPS_HOST:/opt/funding-bot/
scp -q funding-bot.service $VPS_USER@$VPS_HOST:/etc/systemd/system/

ssh $VPS_USER@$VPS_HOST << 'REMOTE_SERVICE'
cd /opt/funding-bot
source venv/bin/activate
pip install -q -r requirements.txt
systemctl daemon-reload
systemctl enable funding-bot
REMOTE_SERVICE

echo ""
echo "========================================="
echo "Deployment Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. SSH to VPS: ssh root@$VPS_HOST"
echo "2. Configure API keys:"
echo "   cp /opt/funding-bot/.env.example /opt/funding-bot/.env"
echo "   nano /opt/funding-bot/.env"
echo "3. Start bot: systemctl start funding-bot"
echo "4. Monitor: journalctl -u funding-bot -f"
echo ""
