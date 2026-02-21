#!/bin/bash
# Polyphemus Deployment Script — VPS 142.93.143.178
# Deploys code only. NEVER overwrites .env on VPS.
#
# Usage: bash deploy_polyphemus.sh

set -euo pipefail

VPS="root@142.93.143.178"
BOT_DIR="/opt/polyphemus"
LOCAL_SRC="/Users/chudinnorukam/Projects/business/polyphemus"

echo "=== Polyphemus Deploy ==="

# Step 1: Stop service
echo "[1/5] Stopping service..."
ssh "$VPS" "systemctl stop polyphemus"

# Step 2: Copy code (NOT .env)
echo "[2/5] Copying code..."
scp "${LOCAL_SRC}"/*.py "$VPS:${BOT_DIR}/polyphemus/"
scp "${LOCAL_SRC}/requirements.txt" "$VPS:${BOT_DIR}/"

# Step 3: Clear __pycache__ (stale .pyc = AttributeError on new methods)
echo "[3/5] Clearing __pycache__..."
ssh "$VPS" "find ${BOT_DIR}/polyphemus -name __pycache__ -exec rm -rf {} + 2>/dev/null || true"

# Step 4: Verify compilation
echo "[4/5] Verifying compilation..."
ssh "$VPS" "
    cd ${BOT_DIR}
    venv/bin/python -c '
import py_compile, glob, sys
ok = True
for f in glob.glob(\"polyphemus/*.py\"):
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

# Step 5: Start service
echo "[5/5] Starting service..."
ssh "$VPS" "systemctl start polyphemus && sleep 2 && systemctl is-active polyphemus"

echo ""
echo "=== Deploy Complete ==="
echo "Monitor: ssh $VPS journalctl -u polyphemus -f"
echo ""
echo "NOTE: .env was NOT touched. To update config, edit directly on VPS:"
echo "  ssh $VPS nano ${BOT_DIR}/polyphemus/.env"
