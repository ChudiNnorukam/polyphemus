#!/bin/bash
# Marketing Agent — Entrypoint
# Usage: ./run.sh <script> <command>
# Example: ./run.sh email_sequence send
#          ./run.sh marketing_resolve
#          ./run.sh enrich_lead run

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/scripts" && pwd)"
ENV_FILE="$(dirname "$0")/.env"

# Load env
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    source "$ENV_FILE"
    set +o allexport
fi

SCRIPT="$1"
shift || true

if [ -z "$SCRIPT" ]; then
    echo "Usage: ./run.sh <script> [command]"
    echo ""
    echo "Wings 1 + 2 (Cold Email + LinkedIn):"
    echo "  init_db            -- Initialize database"
    echo "  load_prospects     -- Load ICP CSV into DB"
    echo "  enrich_lead        -- Find + verify emails (scan|run|credits)"
    echo "  email_sequence     -- Email cadence (scan|send|status|replies)"
    echo "  check_accepted     -- Manage pending connections"
    echo "  marketing_resolve  -- Print outreach funnel stats"
    echo ""
    echo "Wing 3 (Social Media Manager):"
    echo "  funnel_db_init     -- Extend DB with Wings 3+4 tables (extend|status)"
    echo "  repurpose          -- Convert blog posts to social content (--file|--dir)"
    echo "  publisher          -- Post queued social content (--dry-run)"
    echo "  token_manager      -- Check OAuth token expiry (--check|--refresh-pinterest)"
    echo "  social_analytics   -- Pull Pinterest pin analytics (--days N)"
    echo "  social_resolve     -- Print social media stats"
    echo "  social_review      -- Review pending posts before they go live (--list|--platform|--slug)"
    echo ""
    echo "Wing 4 (Digital Product Funnel):"
    echo "  funnel_webhook     -- Start webhook server (Gumroad + Stripe)"
    echo "  funnel_sequence    -- Fire due funnel emails (send|status)"
    echo "  upsell_trigger     -- Enroll engaged buyers in upsell sequence"
    echo "  funnel_resolve     -- Print funnel stats"
    echo ""
    echo "Autonomous C-Suite (CMO + COO + CTO + CEO):"
    echo "  cmo_engine         -- CMO: 5-lens marketing assessment (daily|history|undo)"
    echo "  coo_engine         -- COO: 4-lens ops assessment (daily|trading|infra)"
    echo "  cto_engine         -- CTO: 4-lens tech health (daily|history|--focus code|deploy|deps|git)"
    echo "  ceo_engine         -- CEO: 4-lens strategic brief (weekly|history|--focus revenue|ops_health|tech_debt|pipeline)"
    echo ""
    echo "Evolution (Memory + Reflection + Coordination):"
    echo "  run_all            -- Full orchestrator loop: CMO->CTO->COO->Memory->CEO (--skip-ceo|--only)"
    echo "  memory_engine      -- Level 1: pattern detection, trends (scan|trends|recurring)"
    echo "  reflection_engine  -- Level 2: self-critique, accuracy tracking (reflect|history|accuracy)"
    echo ""
    echo "Dashboard:"
    echo "  dashboard          -- Live agent architecture dashboard (--port 8086)"
    exit 1
fi

PYTHON="${VENV_PYTHON:-python3}"
if [ -f "/opt/lagbot/venv/bin/python3" ]; then
    PYTHON="/opt/lagbot/venv/bin/python3"
fi

if [ "$SCRIPT" = "dashboard" ]; then
    echo "Starting OpenClaw Agent Dashboard on http://localhost:8086"
    exec "$PYTHON" "$(dirname "$0")/dashboard/server.py" "$@"
fi

exec "$PYTHON" "$SCRIPT_DIR/${SCRIPT}.py" "$@"
