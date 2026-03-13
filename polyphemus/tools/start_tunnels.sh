#!/bin/bash
# Start a persistent SSH tunnel to the current production VPS.
# This helper intentionally avoids dead/backup hosts.

# Kill any existing tunnel on the monitor port
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
sleep 1

ssh -fNL 8080:localhost:8080 root@82.24.19.114

echo "Tunnels active:"
echo "  localhost:8080 -> Lagbot (82.24.19.114)"
echo ""
echo "Run: python3 tools/bot_monitor.py"
