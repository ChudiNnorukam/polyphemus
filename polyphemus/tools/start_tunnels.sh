#!/bin/bash
# Start persistent SSH tunnels to the current production VPS dashboards.
# This helper reads the live instance dashboard ports instead of assuming 8080.

set -euo pipefail

VPS="root@82.24.19.114"
INSTANCES=("emmanuel" "polyphemus")

get_remote_port() {
  local instance="$1"
  ssh "$VPS" "grep '^DASHBOARD_PORT=' /opt/lagbot/instances/$instance/.env 2>/dev/null | tail -1 | cut -d= -f2"
}

for instance in "${INSTANCES[@]}"; do
  remote_port="$(get_remote_port "$instance")"
  if [ -z "${remote_port:-}" ]; then
    echo "Could not determine DASHBOARD_PORT for $instance"
    exit 1
  fi

  lsof -ti:"$remote_port" | xargs kill -9 2>/dev/null || true
  sleep 1
  ssh -fNL "$remote_port":localhost:"$remote_port" "$VPS"
  echo "  localhost:$remote_port -> $instance ($VPS)"
done

echo "Tunnels active:"
echo ""
echo "Run: python3 tools/bot_monitor.py"
