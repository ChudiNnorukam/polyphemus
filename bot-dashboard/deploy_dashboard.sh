#!/usr/bin/env bash
# Deploy dashboard API to VPS 82.24.19.114
set -euo pipefail

VPS="82.24.19.114"
REMOTE_DIR="/opt/dashboard"

echo "=== Deploying Dashboard API to $VPS ==="

# 1. Ensure remote dir exists
ssh root@$VPS "mkdir -p $REMOTE_DIR"

# 2. Copy API files
scp api.py requirements.txt root@$VPS:$REMOTE_DIR/

# 3. Copy systemd service
scp dashboard.service root@$VPS:/etc/systemd/system/dashboard.service

# 4. Setup venv + install deps (first time only if venv missing)
ssh root@$VPS "
  if [ ! -d $REMOTE_DIR/venv ]; then
    echo 'Creating venv...'
    python3 -m venv $REMOTE_DIR/venv
  fi
  $REMOTE_DIR/venv/bin/pip install -q -r $REMOTE_DIR/requirements.txt
"

# 5. Verify syntax
ssh root@$VPS "$REMOTE_DIR/venv/bin/python -m py_compile $REMOTE_DIR/api.py"
echo "Syntax OK"

# 6. Reload and restart
ssh root@$VPS "
  systemctl daemon-reload
  systemctl restart dashboard
  systemctl enable dashboard
"

echo "=== Dashboard deployed. Check: ssh root@$VPS journalctl -u dashboard -f ==="
