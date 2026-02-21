#!/bin/bash
# Start persistent SSH tunnels to both VPSes
# Run once before starting bot_monitor.py

# Kill any existing tunnels on these ports
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
lsof -ti:8081 | xargs kill -9 2>/dev/null || true
sleep 1

ssh -fNL 8080:localhost:8080 root@142.93.143.178
ssh -fNL 8081:localhost:8080 root@82.24.19.114

echo "Tunnels active:"
echo "  localhost:8080 -> Polyphemus (142.93.143.178)"
echo "  localhost:8081 -> Lagbot     (82.24.19.114)"
echo ""
echo "Run: python3 tools/bot_monitor.py"
