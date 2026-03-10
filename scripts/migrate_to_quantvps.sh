#!/usr/bin/env bash
# Migrate Lagbot to QuantVPS Amsterdam
# Usage: ./migrate_to_quantvps.sh <NEW_VPS_IP>
#
# Prerequisites: SSH key already added to new VPS

set -euo pipefail

NEW_VPS="${1:?Usage: $0 <NEW_VPS_IP>}"
OLD_VPS="159.223.236.50"
PKG_DIR="/opt/lagbot"
VENV_DIR="/opt/lagbot/venv"
DATA_DIR="/opt/lagbot/data"
SERVICE="lagbot"

echo "=== Lagbot Migration: $OLD_VPS -> $NEW_VPS ==="

# 1. Setup new VPS
echo "[1/7] Setting up new VPS..."
ssh root@"$NEW_VPS" bash <<'SETUP'
set -e
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl build-essential

# Create directories
mkdir -p /opt/lagbot/lagbot /opt/lagbot/data /opt/lagbot/venv

# Install Rust toolchain (for Phase 3)
if ! command -v rustup &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    echo 'source $HOME/.cargo/env' >> ~/.bashrc
fi

echo "Setup complete"
SETUP

# 2. Copy code from old VPS
echo "[2/7] Copying lagbot code from old VPS..."
ssh root@"$OLD_VPS" "tar czf /tmp/lagbot_code.tar.gz -C /opt/lagbot lagbot/"
scp root@"$OLD_VPS":/tmp/lagbot_code.tar.gz /tmp/lagbot_code.tar.gz
scp /tmp/lagbot_code.tar.gz root@"$NEW_VPS":/tmp/
ssh root@"$NEW_VPS" "tar xzf /tmp/lagbot_code.tar.gz -C /opt/lagbot/"

# 3. Copy .env (contains API keys)
echo "[3/7] Copying .env..."
scp root@"$OLD_VPS":/opt/lagbot/lagbot/.env /tmp/lagbot_env_migration.txt
scp /tmp/lagbot_env_migration.txt root@"$NEW_VPS":/opt/lagbot/lagbot/.env
rm -f /tmp/lagbot_env_migration.txt

# 4. Setup Python venv
echo "[4/7] Setting up Python venv..."
ssh root@"$NEW_VPS" bash <<'VENV'
set -e
cd /opt/lagbot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install py-clob-client pydantic pydantic-settings python-dotenv aiohttp web3
# Optional ML deps
pip install xgboost scikit-learn pandas numpy 2>/dev/null || true
echo "Venv setup complete"
VENV

# 5. Copy data (performance.db, signals.db, state files)
echo "[5/7] Copying data files..."
ssh root@"$OLD_VPS" "tar czf /tmp/lagbot_data.tar.gz -C /opt/lagbot data/"
scp root@"$OLD_VPS":/tmp/lagbot_data.tar.gz /tmp/lagbot_data.tar.gz
scp /tmp/lagbot_data.tar.gz root@"$NEW_VPS":/tmp/
ssh root@"$NEW_VPS" "tar xzf /tmp/lagbot_data.tar.gz -C /opt/lagbot/"

# 6. Install systemd service
echo "[6/7] Installing systemd service..."
ssh root@"$NEW_VPS" bash <<'SYSTEMD'
cat > /etc/systemd/system/lagbot.service <<EOF
[Unit]
Description=Lagbot - Crypto Momentum Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
WorkingDirectory=/opt/lagbot
ExecStart=/opt/lagbot/venv/bin/python -m lagbot.main
Restart=on-failure
RestartSec=10
WatchdogSec=120
NotifyAccess=all
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable lagbot
echo "Systemd service installed (not started yet)"
SYSTEMD

# 7. Latency benchmark
echo "[7/7] Running latency benchmark..."
ssh root@"$NEW_VPS" bash <<'BENCH'
echo "=== Latency Benchmark ==="
echo "Ping to Polymarket CLOB (clob.polymarket.com):"
ping -c 5 clob.polymarket.com 2>/dev/null | tail -1 || echo "ping blocked, trying curl..."
echo ""
echo "Curl latency to CLOB API:"
for i in 1 2 3 4 5; do
    curl -so /dev/null -w "Attempt $i: %{time_connect}s connect, %{time_total}s total\n" \
        https://clob.polymarket.com/time 2>/dev/null || true
done
echo ""
echo "Curl latency to Gamma API:"
curl -so /dev/null -w "Gamma API: %{time_connect}s connect, %{time_total}s total\n" \
    https://gamma-api.polymarket.com/markets?limit=1 2>/dev/null || true
echo ""
echo "Curl latency to Binance WS endpoint:"
curl -so /dev/null -w "Binance: %{time_connect}s connect, %{time_total}s total\n" \
    https://stream.binance.com:9443 2>/dev/null || true
BENCH

echo ""
echo "=== Migration Complete ==="
echo "New VPS: $NEW_VPS"
echo ""
echo "Next steps:"
echo "  1. Review latency numbers above"
echo "  2. Stop bot on OLD VPS: ssh root@$OLD_VPS 'systemctl stop lagbot'"
echo "  3. Start bot on NEW VPS: ssh root@$NEW_VPS 'systemctl start lagbot'"
echo "  4. Monitor: ssh root@$NEW_VPS 'journalctl -u lagbot -f'"
echo "  5. Once stable, decommission old VPS"
echo ""
echo "IMPORTANT: Only ONE VPS should run lagbot at a time!"
echo "           Both would trade the same wallet = double exposure."
