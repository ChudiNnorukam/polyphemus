#!/bin/bash
# Passivbot API Key Setup Script
# Run this on the VPS to configure Bybit API credentials
# Usage: bash /opt/passivbot/setup-api-keys.sh

set -e

API_KEYS_DIR="/opt/passivbot/api_keys"
API_KEYS_FILE="$API_KEYS_DIR/api-keys.json"

echo "========================================="
echo "Passivbot API Key Setup"
echo "========================================="
echo ""
echo "This script will securely store your Bybit API credentials."
echo ""
echo "To get API keys from Bybit:"
echo "1. Log in to your Bybit account"
echo "2. Go to Account > API Management"
echo "3. Create new API Key with these permissions:"
echo "   - Trade: Enabled"
echo "   - Position: Enabled"
echo "   - Account: Read-only"
echo "   - IP Whitelist: Add your server IP (142.93.143.178)"
echo ""

# Ensure directory exists
mkdir -p "$API_KEYS_DIR"
chmod 700 "$API_KEYS_DIR"

# Prompt for API credentials
read -p "Enter your Bybit API Key: " API_KEY
if [ -z "$API_KEY" ]; then
    echo "ERROR: API Key cannot be empty"
    exit 1
fi

read -sp "Enter your Bybit API Secret: " API_SECRET
echo ""
if [ -z "$API_SECRET" ]; then
    echo "ERROR: API Secret cannot be empty"
    exit 1
fi

read -sp "Enter your Bybit API Secret (confirm): " API_SECRET_CONFIRM
echo ""
if [ "$API_SECRET" != "$API_SECRET_CONFIRM" ]; then
    echo "ERROR: API Secrets do not match"
    exit 1
fi

echo ""
echo "Creating API keys file..."

# Create api-keys.json with proper JSON formatting
cat > "$API_KEYS_FILE" << EOF
{
  "bybit-usd": {
    "key": "$API_KEY",
    "secret": "$API_SECRET"
  }
}
EOF

# Secure file permissions
chmod 600 "$API_KEYS_FILE"
chown root:root "$API_KEYS_FILE"

echo "✓ API keys saved successfully"
echo ""
echo "File location: $API_KEYS_FILE"
echo "Permissions: 600 (readable by root only)"
echo ""
echo "You can now:"
echo "1. Copy a config: cp /opt/passivbot/configs/bybit-*.json /opt/passivbot/configs/live/active.json"
echo "2. Backtest: cd /opt/passivbot && python3 src/passivbot.py --backtest --config /opt/passivbot/configs/live/active.json"
echo "3. Start bot: systemctl start passivbot"
echo ""
