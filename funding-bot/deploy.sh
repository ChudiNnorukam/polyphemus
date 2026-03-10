#!/bin/bash
# Deployment script for Funding Rate Arbitrage Bot
# Deploys to VPS 142.93.143.178

set -e

# Configuration
VPS_HOST="142.93.143.178"
VPS_USER="root"
DEPLOY_PATH="/opt/funding-bot"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Funding Rate Bot Deployment Script"
echo "======================================"
echo "Target: $VPS_HOST:$DEPLOY_PATH"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Step 1: Validate configuration
log_info "Step 1: Validating configuration..."
if [ ! -f "$LOCAL_DIR/.env" ]; then
    log_error ".env file not found. Copy from .env.example and configure."
    exit 1
fi

if ! grep -q "BYBIT_API_KEY" "$LOCAL_DIR/.env"; then
    log_error ".env missing BYBIT_API_KEY"
    exit 1
fi

log_info ".env configuration valid"

# Step 2: Create deployment package
log_info "Step 2: Creating deployment package..."
PACKAGE_DIR="/tmp/funding-bot-deploy"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

# Copy files
cp -r "$LOCAL_DIR"/*.py "$PACKAGE_DIR/"
cp "$LOCAL_DIR"/.env "$PACKAGE_DIR/"
cp "$LOCAL_DIR"/requirements.txt "$PACKAGE_DIR/"
cp "$LOCAL_DIR"/funding-bot.service "$PACKAGE_DIR/"

log_info "Package created at $PACKAGE_DIR"

# Step 3: Deploy to VPS
log_info "Step 3: Deploying to VPS..."
ssh -o StrictHostKeyChecking=no "$VPS_USER@$VPS_HOST" "mkdir -p $DEPLOY_PATH"
scp -r "$PACKAGE_DIR"/* "$VPS_USER@$VPS_HOST:$DEPLOY_PATH/"

log_info "Files deployed to VPS"

# Step 4: Setup virtual environment
log_info "Step 4: Setting up Python environment..."
ssh "$VPS_USER@$VPS_HOST" "cd $DEPLOY_PATH && python3 -m venv venv && source venv/bin/activate && pip install --upgrade pip setuptools"

log_info "Virtual environment created"

# Step 5: Install dependencies
log_info "Step 5: Installing dependencies..."
ssh "$VPS_USER@$VPS_HOST" "cd $DEPLOY_PATH && source venv/bin/activate && pip install -r requirements.txt"

log_info "Dependencies installed"

# Step 6: Validate Python syntax
log_info "Step 6: Validating Python syntax..."
ssh "$VPS_USER@$VPS_HOST" "cd $DEPLOY_PATH && python3 -m py_compile *.py"

log_info "Python syntax valid"

# Step 7: Create directories
log_info "Step 7: Creating data and log directories..."
ssh "$VPS_USER@$VPS_HOST" "mkdir -p $DEPLOY_PATH/data $DEPLOY_PATH/logs && chmod 755 $DEPLOY_PATH/data $DEPLOY_PATH/logs"

log_info "Directories created"

# Step 8: Create systemd user
log_info "Step 8: Setting up systemd service..."
ssh "$VPS_USER@$VPS_HOST" "useradd -r -s /bin/bash funding-bot 2>/dev/null || true"

log_info "User created (or already exists)"

# Step 9: Install systemd service
log_info "Step 9: Installing systemd service..."
ssh "$VPS_USER@$VPS_HOST" "cp $DEPLOY_PATH/funding-bot.service /etc/systemd/system/ && systemctl daemon-reload"

log_info "Systemd service installed"

# Step 10: Set permissions
log_info "Step 10: Setting file permissions..."
ssh "$VPS_USER@$VPS_HOST" "chown -R funding-bot:funding-bot $DEPLOY_PATH && chmod 755 $DEPLOY_PATH/run_funding_bot.py"

log_info "Permissions set"

# Step 11: Start service
log_info "Step 11: Starting bot service..."
ssh "$VPS_USER@$VPS_HOST" "systemctl start funding-bot && systemctl enable funding-bot"

log_info "Bot service started"

# Step 12: Verify deployment
log_info "Step 12: Verifying deployment..."
sleep 2
STATUS=$(ssh "$VPS_USER@$VPS_HOST" "systemctl status funding-bot --no-pager" 2>/dev/null || echo "failed")

if echo "$STATUS" | grep -q "active (running)"; then
    log_info "✅ Bot is running successfully!"
else
    log_warn "Bot status unclear. Checking logs..."
    ssh "$VPS_USER@$VPS_HOST" "tail -20 $DEPLOY_PATH/logs/funding_bot.log"
fi

# Step 13: Setup monitoring
log_info "Step 13: Setting up monitoring..."
ssh "$VPS_USER@$VPS_HOST" "cat > /opt/funding-bot/monitor.sh << 'EOF'
#!/bin/bash
# Monitor bot health
while true; do
    if systemctl is-active --quiet funding-bot; then
        echo \"✅ Bot is running\"
        if [ -f /opt/funding-bot/logs/health.json ]; then
            cat /opt/funding-bot/logs/health.json
        fi
    else
        echo \"❌ Bot is NOT running\"
        systemctl status funding-bot
    fi
    sleep 300
done
EOF
chmod +x /opt/funding-bot/monitor.sh"

log_info "Monitor script created"

# Final output
echo ""
echo "======================================"
echo -e "${GREEN}🎉 Deployment Complete!${NC}"
echo "======================================"
echo ""
echo "Bot Information:"
echo "  Location: $VPS_HOST:$DEPLOY_PATH"
echo "  Service: funding-bot"
echo "  Config: $DEPLOY_PATH/.env"
echo "  Logs: $DEPLOY_PATH/logs/funding_bot.log"
echo "  Health: $DEPLOY_PATH/logs/health.json"
echo ""
echo "Common Commands:"
echo "  View logs: ssh $VPS_USER@$VPS_HOST 'tail -f $DEPLOY_PATH/logs/funding_bot.log'"
echo "  Check status: ssh $VPS_USER@$VPS_HOST 'systemctl status funding-bot'"
echo "  Stop bot: ssh $VPS_USER@$VPS_HOST 'systemctl stop funding-bot'"
echo "  Start bot: ssh $VPS_USER@$VPS_HOST 'systemctl start funding-bot'"
echo "  Restart bot: ssh $VPS_USER@$VPS_HOST 'systemctl restart funding-bot'"
echo "  Check health: ssh $VPS_USER@$VPS_HOST 'cat $DEPLOY_PATH/logs/health.json | jq'"
echo ""
echo "Next Steps:"
echo "  1. Monitor the logs for any errors"
echo "  2. Configure Telegram alerts (optional)"
echo "  3. Set up monitoring/alerting dashboard"
echo "  4. Start with DRY_RUN=true to verify strategy"
echo ""
