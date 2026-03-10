#!/bin/bash
# Passivbot Management Script
# Remote control of bot on 142.93.143.178
# Usage: ./manage-bot.sh [command] [args]

VPS_HOST="142.93.143.178"
VPS_USER="root"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

function print_help() {
    cat << EOF
Passivbot Management Script
Usage: ./manage-bot.sh [command] [args]

Commands:
  status              Show bot status and recent logs
  start               Start the bot
  stop                Stop the bot
  restart             Restart the bot
  logs [lines]        Show last N lines of logs (default: 50)
  follow              Follow logs in real-time (Ctrl+C to exit)
  errors              Show only error logs
  config              Show current active configuration
  balance             Check Bybit USDT balance
  positions           Show open positions and P&L
  health              Quick health check
  update-config FILE  Upload and activate new config file
  view-config FILE    View remote config file
  test                Run connection test

Examples:
  ./manage-bot.sh status
  ./manage-bot.sh logs 100
  ./manage-bot.sh follow
  ./manage-bot.sh update-config ./configs/bybit-500-balanced.json
  ./manage-bot.sh test

EOF
}

function check_ssh() {
    if ! ssh -o ConnectTimeout=5 $VPS_USER@$VPS_HOST "echo 'OK'" &>/dev/null; then
        echo -e "${RED}ERROR: Cannot connect to $VPS_HOST${NC}"
        exit 1
    fi
}

function show_status() {
    echo -e "${BLUE}=== Bot Status ===${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
systemctl status passivbot --no-pager | head -20
CMD
    echo ""
    echo -e "${BLUE}=== Recent Logs (last 20 lines) ===${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
journalctl -u passivbot -n 20 --no-pager
CMD
}

function start_bot() {
    echo -e "${YELLOW}Starting Passivbot...${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
systemctl start passivbot
sleep 2
systemctl status passivbot --no-pager | head -5
CMD
    echo -e "${GREEN}Bot started. Checking logs...${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
journalctl -u passivbot -n 10 --no-pager
CMD
}

function stop_bot() {
    echo -e "${YELLOW}Stopping Passivbot...${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
systemctl stop passivbot
sleep 2
systemctl status passivbot --no-pager | head -3
CMD
}

function restart_bot() {
    echo -e "${YELLOW}Restarting Passivbot...${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
systemctl restart passivbot
sleep 2
systemctl status passivbot --no-pager | head -5
CMD
    echo ""
    echo -e "${GREEN}Bot restarted. Checking logs...${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
journalctl -u passivbot -n 10 --no-pager
CMD
}

function show_logs() {
    local lines=${1:-50}
    echo -e "${BLUE}=== Last $lines lines ===${NC}"
    ssh $VPS_USER@$VPS_HOST << CMD
journalctl -u passivbot -n $lines --no-pager
CMD
}

function follow_logs() {
    echo -e "${BLUE}=== Following logs (Ctrl+C to exit) ===${NC}"
    ssh $VPS_USER@$VPS_HOST 'journalctl -u passivbot -f'
}

function show_errors() {
    echo -e "${BLUE}=== Error logs ===${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
journalctl -u passivbot -p err --no-pager
CMD
}

function show_config() {
    echo -e "${BLUE}=== Active Configuration ===${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
cat /opt/passivbot/configs/live/active.json | python3 -m json.tool
CMD
}

function show_balance() {
    echo -e "${BLUE}=== Checking Bybit Balance ===${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
grep -i "balance\|usdt" /opt/passivbot/logs/passivbot.log | tail -5 || echo "No balance info in recent logs"
CMD
}

function show_positions() {
    echo -e "${BLUE}=== Open Positions ===${NC}"
    ssh $VPS_USER@$VPS_HOST << 'CMD'
grep -i "position\|pnl\|entry" /opt/passivbot/logs/passivbot.log | tail -10 || echo "No position info in recent logs"
CMD
}

function health_check() {
    echo -e "${BLUE}=== Health Check ===${NC}"

    check_ssh
    echo -e "${GREEN}✓ SSH connection OK${NC}"

    ssh $VPS_USER@$VPS_HOST << 'CMD'
# Check if bot is running
if systemctl is-active --quiet passivbot; then
    echo -e "\033[0;32m✓ Bot service running\033[0m"
else
    echo -e "\033[0;31m✗ Bot service NOT running\033[0m"
fi

# Check if config exists
if [ -f "/opt/passivbot/configs/live/active.json" ]; then
    echo -e "\033[0;32m✓ Active config found\033[0m"
else
    echo -e "\033[0;31m✗ Active config NOT found\033[0m"
fi

# Check if API keys exist
if [ -f "/opt/passivbot/api_keys/api-keys.json" ]; then
    echo -e "\033[0;32m✓ API keys configured\033[0m"
else
    echo -e "\033[0;31m✗ API keys NOT configured\033[0m"
fi

# Check recent errors
ERROR_COUNT=$(journalctl -u passivbot -p err --since "1 hour ago" --no-pager | wc -l)
if [ "$ERROR_COUNT" -eq 0 ]; then
    echo -e "\033[0;32m✓ No errors in last hour\033[0m"
else
    echo -e "\033[0;31m✗ $ERROR_COUNT errors in last hour\033[0m"
fi

# Check disk space
DISK_USAGE=$(df /opt/passivbot | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 80 ]; then
    echo -e "\033[0;32m✓ Disk usage: ${DISK_USAGE}%\033[0m"
else
    echo -e "\033[0;31m✗ Disk usage HIGH: ${DISK_USAGE}%\033[0m"
fi

# Check memory
MEMORY=$(free | grep Mem | awk '{printf("%.0f", ($3/$2) * 100)}')
if [ "$MEMORY" -lt 80 ]; then
    echo -e "\033[0;32m✓ Memory usage: ${MEMORY}%\033[0m"
else
    echo -e "\033[0;31m✗ Memory usage HIGH: ${MEMORY}%\033[0m"
fi

# Uptime
UPTIME=$(systemctl show -p ActiveEnterTimestamp passivbot | cut -d= -f2)
echo -e "\033[0;34mBot started: $UPTIME\033[0m"
CMD
}

function update_config() {
    local config_file=$1

    if [ -z "$config_file" ]; then
        echo -e "${RED}ERROR: No config file specified${NC}"
        echo "Usage: ./manage-bot.sh update-config /path/to/config.json"
        exit 1
    fi

    if [ ! -f "$config_file" ]; then
        echo -e "${RED}ERROR: Config file not found: $config_file${NC}"
        exit 1
    fi

    # Validate JSON
    if ! python3 -m json.tool "$config_file" > /dev/null 2>&1; then
        echo -e "${RED}ERROR: Invalid JSON in config file${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Uploading and activating config: $config_file${NC}"

    # Upload config
    scp "$config_file" $VPS_USER@$VPS_HOST:/opt/passivbot/configs/live/active.json

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ Config uploaded successfully${NC}"
        echo -e "${YELLOW}Restarting bot...${NC}"
        restart_bot
    else
        echo -e "${RED}ERROR: Failed to upload config${NC}"
        exit 1
    fi
}

function view_config() {
    local config_file=$1

    if [ -z "$config_file" ]; then
        config_file="active.json"
    fi

    echo -e "${BLUE}=== Config: $config_file ===${NC}"
    ssh $VPS_USER@$VPS_HOST << CMD
cat /opt/passivbot/configs/live/$config_file 2>/dev/null || cat /opt/passivbot/configs/$config_file | python3 -m json.tool
CMD
}

function test_connection() {
    echo -e "${BLUE}=== Connection Test ===${NC}"

    if check_ssh; then
        echo -e "${GREEN}✓ SSH OK${NC}"
    fi

    ssh $VPS_USER@$VPS_HOST << 'CMD'
# Test installation
if [ -d "/opt/passivbot" ]; then
    echo -e "\033[0;32m✓ Installation directory exists\033[0m"
else
    echo -e "\033[0;31m✗ Installation directory NOT found\033[0m"
fi

# Test venv
if [ -f "/opt/passivbot/venv/bin/python3" ]; then
    echo -e "\033[0;32m✓ Virtual environment OK\033[0m"
else
    echo -e "\033[0;31m✗ Virtual environment NOT found\033[0m"
fi

# Test Python imports
/opt/passivbot/venv/bin/python3 -c "import passivbot; print('\033[0;32m✓ Passivbot module importable\033[0m')" 2>/dev/null || echo -e "\033[0;31m✗ Passivbot module import failed\033[0m"

# Test system dependencies
echo -e "\033[0;34mSystem Info:\033[0m"
echo "  Python: $(/opt/passivbot/venv/bin/python3 --version)"
echo "  OS: $(lsb_release -ds 2>/dev/null || echo 'Linux')"
echo "  Kernel: $(uname -r)"
CMD
}

# Main script
if [ $# -eq 0 ]; then
    print_help
    exit 0
fi

case "$1" in
    status)
        check_ssh
        show_status
        ;;
    start)
        check_ssh
        start_bot
        ;;
    stop)
        check_ssh
        stop_bot
        ;;
    restart)
        check_ssh
        restart_bot
        ;;
    logs)
        check_ssh
        show_logs "$2"
        ;;
    follow)
        check_ssh
        follow_logs
        ;;
    errors)
        check_ssh
        show_errors
        ;;
    config)
        check_ssh
        show_config
        ;;
    balance)
        check_ssh
        show_balance
        ;;
    positions)
        check_ssh
        show_positions
        ;;
    health)
        check_ssh
        health_check
        ;;
    update-config)
        check_ssh
        update_config "$2"
        ;;
    view-config)
        check_ssh
        view_config "$2"
        ;;
    test)
        check_ssh
        test_connection
        ;;
    -h|--help|help)
        print_help
        ;;
    *)
        echo "Unknown command: $1"
        print_help
        exit 1
        ;;
esac
