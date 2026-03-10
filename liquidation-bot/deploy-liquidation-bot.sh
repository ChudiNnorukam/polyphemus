#!/bin/bash
# Liquidation Bot Deployment Script for Aave V3
# Deploy to VPS 142.93.143.178
# Usage: ./deploy-liquidation-bot.sh

set -e

VPS_HOST="142.93.143.178"
VPS_USER="root"
INSTALL_DIR="/opt/liquidation-bot"

echo "========================================="
echo "Liquidation Bot Deployment"
echo "========================================="
echo "Target: $VPS_USER@$VPS_HOST:$INSTALL_DIR"
echo ""

# Check SSH connectivity
echo "[1/3] Checking SSH connectivity..."
if ! ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST "echo 'SSH OK'" &>/dev/null; then
    echo "ERROR: Cannot SSH to $VPS_HOST"
    exit 1
fi
echo "SSH OK"

# Deploy base installation
echo ""
echo "[2/3] Installing dependencies and setting up environment..."
ssh $VPS_USER@$VPS_HOST << 'REMOTE_INSTALL'
set -e

echo "Updating package manager..."
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev build-essential git curl

echo "Creating bot installation directory..."
mkdir -p /opt/liquidation-bot/{logs,data}
cd /opt/liquidation-bot

echo "Creating Python virtual environment..."
python3.11 -m venv venv
source venv/bin/activate

echo "Upgrading pip and installing dependencies..."
pip install -U pip setuptools wheel
pip install -r requirements.txt 2>/dev/null || echo "requirements.txt not yet deployed"

echo "Setting permissions..."
chmod 755 /opt/liquidation-bot

echo "Installation complete!"
REMOTE_INSTALL

# Copy source files
echo ""
echo "[3/3] Uploading bot files..."
scp -q config.py $VPS_USER@$VPS_HOST:/opt/liquidation-bot/config.py
scp -q monitor.py $VPS_USER@$VPS_HOST:/opt/liquidation-bot/monitor.py
scp -q executor.py $VPS_USER@$VPS_HOST:/opt/liquidation-bot/executor.py
scp -q main.py $VPS_USER@$VPS_HOST:/opt/liquidation-bot/main.py
scp -q requirements.txt $VPS_USER@$VPS_HOST:/opt/liquidation-bot/requirements.txt
scp -q .env.example $VPS_USER@$VPS_HOST:/opt/liquidation-bot/.env.example
scp -q liquidation-bot.service $VPS_USER@$VPS_HOST:/etc/systemd/system/liquidation-bot.service

ssh $VPS_USER@$VPS_HOST << 'REMOTE_SERVICE'
cd /opt/liquidation-bot
source venv/bin/activate
pip install -q -r requirements.txt

systemctl daemon-reload
systemctl enable liquidation-bot
echo "Service installed and enabled"
REMOTE_SERVICE

echo ""
echo "========================================="
echo "Deployment Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. SSH to VPS: ssh root@$VPS_HOST"
echo "2. Configure environment:"
echo "   cp /opt/liquidation-bot/.env.example /opt/liquidation-bot/.env"
echo "   nano /opt/liquidation-bot/.env"
echo "3. Start bot:"
echo "   systemctl start liquidation-bot"
echo "4. Monitor logs:"
echo "   journalctl -u liquidation-bot -f"
echo ""
echo "Configuration: /opt/liquidation-bot/.env"
echo "Logs: /opt/liquidation-bot/logs/"
echo ""
