#!/bin/bash
# Lagbot Deployment Script — Private EOA bot
# Deploys code only. NEVER overwrites .env on VPS.
#
# Usage: bash deploy_private_lagbot.sh [VPS_IP]
# Default: 159.223.236.50

set -euo pipefail

AGENCY_VPS="142.93.143.178"
PRIVATE_VPS="${1:-159.223.236.50}"

if [ "$PRIVATE_VPS" = "$AGENCY_VPS" ]; then
    echo "ERROR: Cannot deploy lagbot to agency VPS ($AGENCY_VPS)!"
    exit 1
fi

VPS="root@${PRIVATE_VPS}"
BOT_DIR="/opt/lagbot"
LOCAL_SRC="/Users/chudinnorukam/Projects/business/polyphemus"

echo "=== Lagbot Deploy ==="
echo "Target: ${VPS}"

# Step 1: Stop service
echo "[1/6] Stopping service..."
ssh "$VPS" "systemctl stop lagbot"

# Step 2: Copy code (NOT .env)
echo "[2/6] Copying code..."
ssh "$VPS" "mkdir -p ${BOT_DIR}/lagbot"
scp "${LOCAL_SRC}"/*.py "$VPS:${BOT_DIR}/lagbot/"
scp "${LOCAL_SRC}/requirements.txt" "$VPS:${BOT_DIR}/"

# Step 3: Rename package imports polyphemus -> lagbot
echo "[3/6] Renaming package imports..."
ssh "$VPS" "
    cd ${BOT_DIR}/lagbot
    sed -i 's/from polyphemus\./from lagbot./g; s/from \.polyphemus/from .lagbot/g; s/import polyphemus/import lagbot/g' *.py
    sed -i \"s/polyphemus\\./lagbot./g\" *.py
    sed -i 's/-m polyphemus/-m lagbot/g' *.py 2>/dev/null || true
"

# Step 4: Clear __pycache__
echo "[4/6] Clearing __pycache__..."
ssh "$VPS" "find ${BOT_DIR}/lagbot -name __pycache__ -exec rm -rf {} + 2>/dev/null || true"

# Step 5: Verify compilation
echo "[5/6] Verifying compilation..."
ssh "$VPS" "
    cd ${BOT_DIR}
    venv/bin/python -c '
import py_compile, glob, sys
ok = True
for f in glob.glob(\"lagbot/*.py\"):
    if \"test_\" in f:
        continue
    try:
        py_compile.compile(f, doraise=True)
        print(f\"OK: {f}\")
    except py_compile.PyCompileError as e:
        print(f\"FAIL: {f} -> {e}\")
        ok = False
sys.exit(0 if ok else 1)
'
"

# Step 6: Start service
echo "[6/6] Starting service..."
ssh "$VPS" "systemctl start lagbot && sleep 2 && systemctl is-active lagbot"

echo ""
echo "=== Deploy Complete ==="
echo "Monitor: ssh $VPS journalctl -u lagbot -f"
echo ""
echo "NOTE: .env was NOT touched. To update config, edit directly on VPS:"
echo "  ssh $VPS nano ${BOT_DIR}/lagbot/.env"
