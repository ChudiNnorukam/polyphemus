#!/bin/bash
# Start persistent SSH tunnels to both VPSes.
# Port 8080 -> Polyphemus (142.93.143.178, proxy wallet)
# Port 8081 -> Lagbot    (82.24.19.114, EOA wallet)
# Run this once, then run: python3 tools/bot_monitor.py

# Kill any existing tunnels on these ports first
lsof -ti:8080 | xargs kill -9 2>/dev/null
lsof -ti:8081 | xargs kill -9 2>/dev/null

echo "Starting SSH tunnels..."

ssh -fNL 8080:localhost:8080 root@142.93.143.178
if [ $? -eq 0 ]; then
    echo "  [OK] Polyphemus -> localhost:8080 (142.93.143.178, proxy wallet)"
else
    echo "  [FAIL] Could not connect Polyphemus tunnel"
fi

ssh -fNL 8081:localhost:8080 root@82.24.19.114
if [ $? -eq 0 ]; then
    echo "  [OK] Lagbot     -> localhost:8081 (82.24.19.114, EOA wallet)"
else
    echo "  [WARN] Lagbot tunnel failed (OK if lagbot not running)"
fi

echo ""
echo "Tunnels active. Run: python3 tools/bot_monitor.py"
