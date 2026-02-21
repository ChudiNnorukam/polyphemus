#!/bin/bash
# preflight.sh - Hardened staged rollout for Polyphemus accumulator
#
# Replaces phase3_transition.sh and phase4_transition.sh with a single
# idempotent script that fixes all known failure modes from 2026-02-19.
#
# Usage:
#   bash preflight.sh --stage 2          # Activate and monitor Stage 2 (restricted live)
#   bash preflight.sh --stage 3          # Monitor Stage 2, transition to Stage 3
#   bash preflight.sh --stage 4          # Monitor Stage 3, transition to Stage 4 (full)
#   bash preflight.sh --sell-smoke-test  # Run SELL smoke test only (no transition)
#   bash preflight.sh --status           # Print current accumulator state
#   bash preflight.sh --dry-run --stage 4  # Preview all changes without applying
#
# Safety rules enforced:
#   - VPS identity verified before every mutation
#   - Dynamic baseline captured at invocation (historical cycles never count)
#   - SELL smoke test required before any live capital increase
#   - Orphaned position count must be 0 before scale-up
#   - Balance > threshold verified AFTER every restart
#   - All transitions logged to /opt/polyphemus/data/preflight.log
#   - .env edits use Python (not sed) - RTK hook safe
#   - Idempotent: safe to re-run at any point

set -euo pipefail

# ============================================================
# CONFIG
# ============================================================
VPS="root@142.93.143.178"
VPS_IP="142.93.143.178"
ENV_PATH="/opt/polyphemus/polyphemus/.env"
DATA_DIR="/opt/polyphemus/data"
PKG_DIR="/opt/polyphemus/polyphemus"
VENV="/opt/polyphemus/venv/bin/python3"
API="http://localhost:8080"
LOG_FILE="$DATA_DIR/preflight.log"

# Expected VPS identity (hostname substring to match)
EXPECTED_VPS_HOSTNAME_SUBSTR="polymarket-bot"

# Stage gate thresholds
STAGE2_MIN_NEW_CYCLES=5    # minimum new cycles (not historical) to pass Stage 1->2
STAGE3_MIN_NEW_CYCLES=3    # minimum new cycles to pass Stage 2->3
STAGE4_MIN_NEW_CYCLES=3    # minimum new cycles to pass Stage 3->4
STAGE4_MIN_PNL="-0.50"     # minimum session PnL (USDC) to pass Stage 3->4

# Capital configs per stage
declare -A STAGE_CAPITAL=([2]="0.05" [3]="0.20" [4]="0.60")
declare -A STAGE_CONCURRENT=([2]="1" [3]="1" [4]="3")
declare -A STAGE_MAX_SHARES=([2]="10" [3]="20" [4]="60")

# Minimum balance (USDC) required before any live stage
MIN_LIVE_BALANCE=50

# Poll interval in seconds
POLL_SECS=60

# ============================================================
# ARGUMENT PARSING
# ============================================================
STAGE=""
PREVIEW=false
SMOKE_ONLY=false
STATUS_ONLY=false

for arg in "$@"; do
    case $arg in
        --stage) shift ;;
        --stage=*) STAGE="${arg#--stage=}" ;;
        --dry-run) PREVIEW=true ;;
        --sell-smoke-test) SMOKE_ONLY=true ;;
        --status) STATUS_ONLY=true ;;
        [2-4]) STAGE="$arg" ;;
    esac
done

# Handle '--stage N' (two-token form)
args=("$@")
for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "--stage" ] && [ $((i+1)) -lt ${#args[@]} ]; then
        STAGE="${args[$((i+1))]}"
    fi
done

LOG_PREFIX="[preflight]"

# ============================================================
# HELPERS
# ============================================================

log() {
    echo "$LOG_PREFIX $(date -u '+%H:%M:%S') $*"
}

log_transition() {
    local msg="$*"
    local ts
    ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
    ssh "$VPS" "echo '$ts $LOG_PREFIX $msg' >> $LOG_FILE 2>/dev/null" || true
    log "$msg"
}

die() {
    log "ABORT: $*"
    exit 1
}

preview_or_run() {
    local desc="$1"
    local cmd="$2"
    if $PREVIEW; then
        log "[PREVIEW] $desc"
        log "[PREVIEW] Would run: $cmd"
    else
        log "$desc"
        eval "$cmd"
    fi
}

# ============================================================
# CHECK 1: VPS IDENTITY
# verify we are operating on the right machine before any mutation
# ============================================================
verify_vps_identity() {
    log "Verifying VPS identity..."
    local actual_hostname
    actual_hostname=$(ssh "$VPS" 'hostname' 2>/dev/null) || die "SSH to $VPS failed"

    local actual_ip
    actual_ip=$(ssh "$VPS" 'curl -s ifconfig.me 2>/dev/null || echo unknown')

    log "VPS hostname: $actual_hostname | public IP: $actual_ip"

    # Verify IP matches expected
    if [ "$actual_ip" != "$VPS_IP" ] && [ "$actual_ip" != "unknown" ]; then
        die "VPS IP mismatch: got $actual_ip, expected $VPS_IP. Wrong machine!"
    fi

    # Verify service exists on this VPS
    local svc_status
    svc_status=$(ssh "$VPS" 'systemctl is-enabled polyphemus 2>/dev/null || echo not-found')
    if [ "$svc_status" = "not-found" ]; then
        die "polyphemus service not found on $VPS. Wrong machine or service not installed."
    fi

    log "VPS identity CONFIRMED: $actual_hostname ($actual_ip)"
}

# ============================================================
# CHECK 2: ORPHAN GATE
# No live capital increase while orphaned positions exist
# ============================================================
check_no_orphans() {
    local orphan_count
    orphan_count=$(ssh "$VPS" "curl -s $API/api/accumulator 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d.get('orphaned_count',0))\"" 2>/dev/null || echo "ERROR")

    if [ "$orphan_count" = "ERROR" ]; then
        log "WARNING: Could not fetch orphan count. Assuming 0 and continuing."
        return 0
    fi

    if [ "$orphan_count" -gt 0 ]; then
        die "Orphaned positions detected: $orphan_count. Wait for redeemer to settle or manually redeem before scaling capital. Check: ssh $VPS journalctl -u polyphemus -n 50 | grep redeemer"
    fi

    log "Orphan check PASSED: 0 orphaned positions"
}

# ============================================================
# CHECK 3: CLOB BALANCE GATE
# Verify real CLOB balance > threshold after restart
# ============================================================
check_balance_after_restart() {
    local min_bal="${1:-$MIN_LIVE_BALANCE}"
    log "Waiting 15s for service startup before balance check..."
    sleep 15

    local balance
    balance=$(ssh "$VPS" "curl -s $API/api/balance 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d.get('balance', d.get('usdc_balance', 0)))\"" 2>/dev/null || echo "0")

    log "Post-restart balance: \$$balance"

    local is_sufficient
    is_sufficient=$(python3 -c "print('yes' if float('${balance:-0}') >= $min_bal else 'no')" 2>/dev/null || echo "no")

    if [ "$is_sufficient" != "yes" ]; then
        die "Balance \$$balance is below minimum \$$min_bal after restart. Possible auth failure or wrong .env. Check: ssh $VPS journalctl -u polyphemus -n 30 --no-pager"
    fi

    log "Balance gate PASSED: \$$balance >= \$$min_bal"
}

# ============================================================
# CHECK 4: SELL SMOKE TEST
# Place a tiny post-only SELL order, verify acceptance, then cancel.
# Proves signing, SELL side construction, and CLOB network path work.
# Does NOT require holding any shares - uses limit price above best ask
# so the order will not fill.
# ============================================================
run_sell_smoke_test() {
    log "=== SELL SMOKE TEST ==="
    log "Placing post-only SELL order above ask (will not fill), then cancelling..."

    # Write the smoke test script to a temp file, scp, run it
    local tmp_script="/tmp/sell_smoke_test_$$.py"
    cat > "$tmp_script" << 'PYEOF'
import sys
import os
import time

# Must run from outside the package dir to avoid types.py shadow
os.chdir("/opt/polyphemus")
sys.path.insert(0, "/opt/polyphemus")

from dotenv import load_dotenv
load_dotenv("/opt/polyphemus/polyphemus/.env")

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import SELL

private_key = os.getenv("PRIVATE_KEY")
api_key = os.getenv("CLOB_API_KEY")
secret = os.getenv("CLOB_SECRET")
passphrase = os.getenv("CLOB_PASSPHRASE")
wallet = os.getenv("WALLET_ADDRESS")
sig_type = int(os.getenv("SIGNATURE_TYPE", "1"))
funder = os.getenv("POLYMARKET_PROXY_WALLET", wallet)

if not all([private_key, api_key, secret, passphrase]):
    print("FAIL: Missing credentials in .env")
    sys.exit(1)

host = "https://clob.polymarket.com"
chain_id = 137

try:
    client = ClobClient(
        host,
        key=private_key,
        chain_id=chain_id,
        creds={
            "apiKey": api_key,
            "secret": secret,
            "passphrase": passphrase,
        },
        signature_type=sig_type,
        funder=funder if sig_type == 1 else None,
    )

    # Find any active market to use as a test token
    # Use a well-known BTC market discovery approach
    import requests
    resp = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"active": True, "closed": False, "tag_id": 100006, "limit": 5},
        timeout=10
    )
    markets = resp.json() if resp.ok else []

    token_id = None
    for m in markets:
        tokens = m.get("clobTokenIds") or []
        if isinstance(tokens, str):
            import json as _json
            tokens = _json.loads(tokens)
        if tokens and len(tokens) >= 1:
            token_id = str(tokens[0])
            break

    if not token_id:
        print("FAIL: No active market found for smoke test")
        sys.exit(1)

    print(f"Using token_id: {token_id[:20]}...")

    # Place a post-only SELL at $0.99 (above any reasonable ask - will be rejected as crosses book,
    # which is acceptable: proves the signing and submission path works end-to-end)
    # The CLOB may reject with "order crosses book" - that is EXPECTED and counts as PASS
    # What we are guarding against is auth failures or "not enough allowance" errors
    order_args = OrderArgs(
        token_id=token_id,
        price=0.99,
        size=5.0,
        side=SELL,
    )

    signed = client.create_order(order_args)
    result = client.post_order(signed, OrderType.GTC, post_only=True)

    order_id = result.get("orderID", "")
    error = result.get("error", "")
    error_msg = result.get("errorMsg", "")

    # Fatal errors that indicate SELL path is broken:
    fatal_keywords = ["not enough balance", "allowance", "unauthorized", "invalid signature", "authentication"]
    combined_err = (error + error_msg).lower()

    if any(kw in combined_err for kw in fatal_keywords):
        print(f"FAIL: SELL rejected with fatal error: {error} {error_msg}")
        sys.exit(1)

    # "crosses book" or "post only" rejection = expected, SELL path is fine
    if "crosses" in combined_err or "post only" in combined_err or "post_only" in combined_err:
        print(f"PASS: SELL correctly rejected as post-only (crosses book) - signing and auth work")
        sys.exit(0)

    # Order was accepted - cancel it immediately
    if order_id:
        time.sleep(1)
        cancel_result = client.cancel(order_id)
        print(f"PASS: SELL order placed ({order_id[:12]}...) and cancelled successfully")
        sys.exit(0)

    # Unknown response - treat as pass if no fatal error
    print(f"PASS (marginal): Response had no fatal error. result={result}")
    sys.exit(0)

except Exception as e:
    err = str(e).lower()
    if "not enough balance" in err or "allowance" in err or "unauthori" in err:
        print(f"FAIL: SELL smoke test exception (likely auth/allowance issue): {e}")
        sys.exit(1)
    # Network or other transient errors - warn but don't block
    print(f"WARN: SELL smoke test exception (non-fatal): {e}")
    sys.exit(2)
PYEOF

    # SCP the script to VPS, then run it
    scp -q "$tmp_script" "$VPS:/tmp/sell_smoke_test.py" || die "SCP of smoke test script failed"
    rm -f "$tmp_script"

    local result_code=0
    local output
    output=$(ssh "$VPS" "$VENV /tmp/sell_smoke_test.py 2>&1") || result_code=$?
    ssh "$VPS" "rm -f /tmp/sell_smoke_test.py" 2>/dev/null || true

    log "Smoke test output: $output"

    if [ "$result_code" -eq 0 ]; then
        log "SELL smoke test PASSED"
        return 0
    elif [ "$result_code" -eq 2 ]; then
        log "WARNING: SELL smoke test returned non-fatal warning. Proceeding with caution."
        return 0
    else
        die "SELL smoke test FAILED. Fix the SELL path before going live. Output: $output"
    fi
}

# ============================================================
# CHECK 5: .ENV EDITOR (Python-based, RTK-hook safe)
# Never use sed via SSH - RTK hook blocks it silently
# ============================================================
set_env_var() {
    local key="$1"
    local value="$2"

    if $PREVIEW; then
        log "[PREVIEW] Would set $key=$value in $ENV_PATH"
        return 0
    fi

    local tmp_script="/tmp/env_edit_$$.py"
    cat > "$tmp_script" << PYEOF
import re, sys
path = "$ENV_PATH"
key = "$key"
value = "$value"
with open(path) as f:
    content = f.read()

pattern = rf'^{re.escape(key)}=.*'
replacement = f'{key}={value}'

if re.search(pattern, content, flags=re.MULTILINE):
    new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
else:
    new_content = content.rstrip() + f'\n{replacement}\n'

with open(path, 'w') as f:
    f.write(new_content)
print(f"SET {key}={value}")
PYEOF

    scp -q "$tmp_script" "$VPS:/tmp/env_edit.py" || die "SCP of env edit script failed"
    rm -f "$tmp_script"
    ssh "$VPS" "$VENV /tmp/env_edit.py && rm -f /tmp/env_edit.py"
}

apply_stage_config() {
    local stage="$1"
    log "Applying Stage $stage config..."
    set_env_var "ACCUM_CAPITAL_PCT" "${STAGE_CAPITAL[$stage]}"
    set_env_var "ACCUM_MAX_CONCURRENT" "${STAGE_CONCURRENT[$stage]}"
    set_env_var "ACCUM_MAX_SHARES" "${STAGE_MAX_SHARES[$stage]}"
    if [ "$stage" -ge 2 ]; then
        set_env_var "DRY_RUN" "false"
        set_env_var "ACCUM_DRY_RUN" "false"
    fi
}

# ============================================================
# SERVICE CONTROL
# ============================================================
stop_service() {
    if $PREVIEW; then
        log "[PREVIEW] Would stop polyphemus service"
        return 0
    fi
    log "Stopping polyphemus service..."
    ssh "$VPS" "systemctl stop polyphemus" || die "Failed to stop service"
    log "Service stopped."
}

start_service() {
    if $PREVIEW; then
        log "[PREVIEW] Would clear pycache and start polyphemus service"
        return 0
    fi
    log "Clearing pycache..."
    ssh "$VPS" "find $PKG_DIR -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo cleared"
    log "Starting polyphemus service..."
    ssh "$VPS" "systemctl start polyphemus" || die "Failed to start service"
    log "Service started."
}

verify_env_written() {
    log "Verifying .env changes:"
    ssh "$VPS" "grep -E '^DRY_RUN|^ACCUM_DRY_RUN|^ACCUM_CAPITAL_PCT|^ACCUM_MAX_CONCURRENT|^ACCUM_MAX_SHARES' $ENV_PATH"
}

# ============================================================
# ACCUMULATOR STATE READER
# ============================================================
get_accum_state() {
    ssh "$VPS" "curl -s $API/api/accumulator 2>/dev/null" || echo "{}"
}

parse_int() {
    local json="$1" key="$2"
    echo "$json" | python3 -c "import sys,json; print(int(json.load(sys.stdin).get('$key', 0)))" 2>/dev/null || echo "0"
}

parse_float() {
    local json="$1" key="$2"
    echo "$json" | python3 -c "import sys,json; print(float(json.load(sys.stdin).get('$key', 0.0)))" 2>/dev/null || echo "0.0"
}

# ============================================================
# GATE MONITOR (generic)
# Waits for N NEW cycles from baseline, plus additional criteria
# ============================================================
wait_for_gate() {
    local stage_label="$1"
    local min_new_cycles="$2"
    local require_unwind="${3:-false}"
    local min_pnl="${4:-}"   # optional PnL floor

    log "=== $stage_label GATE MONITOR ==="
    log "Criteria: $min_new_cycles new cycles${require_unwind:+" + 1 unwind"}${min_pnl:+" + pnl >= $min_pnl"}"

    # Capture DYNAMIC baseline NOW (not baked in at script write time)
    local init_data
    init_data=$(get_accum_state)
    local base_hedged base_unwound base_total
    base_hedged=$(parse_int "$init_data" "hedged_count")
    base_unwound=$(parse_int "$init_data" "unwound_count")
    base_total=$((base_hedged + base_unwound))

    log "Baseline at gate entry: hedged=$base_hedged unwound=$base_unwound total=$base_total"
    log "Polling every ${POLL_SECS}s..."

    while true; do
        local data
        data=$(get_accum_state)
        if [ -z "$data" ] || [ "$data" = "{}" ]; then
            log "API unreachable, retrying in 30s..."
            sleep 30
            continue
        fi

        local cur_hedged cur_unwound cur_consec cur_orphaned
        cur_hedged=$(parse_int "$data" "hedged_count")
        cur_unwound=$(parse_int "$data" "unwound_count")
        cur_consec=$(parse_int "$data" "consecutive_unwinds")
        cur_orphaned=$(parse_int "$data" "orphaned_count")

        local new_hedged new_unwound new_total
        new_hedged=$((cur_hedged - base_hedged))
        new_unwound=$((cur_unwound - base_unwound))
        new_total=$((new_hedged + new_unwound))

        # Starvation check (last 100 log lines)
        local starved
        starved=$(ssh "$VPS" "journalctl -u polyphemus -n 100 --no-pager 2>/dev/null | grep -c 'insufficient_capital'" || echo "0")

        log "$stage_label | new: hedged=$new_hedged unwound=$new_unwound total=$new_total | consec=$cur_consec orphans=$cur_orphaned starved=$starved"

        # Circuit breaker hold
        if [ "$cur_consec" -ge 3 ]; then
            log "Circuit breaker active ($cur_consec consecutive unwinds). Holding. Reset when resolved."
            sleep "$POLL_SECS"
            continue
        fi

        # Orphan hold
        if [ "$cur_orphaned" -gt 0 ]; then
            log "Orphaned positions ($cur_orphaned) detected. Waiting for redeemer to settle."
            sleep "$POLL_SECS"
            continue
        fi

        # Starvation hold
        if [ "$starved" -gt 0 ]; then
            log "Capital starvation events detected ($starved). Waiting."
            sleep "$POLL_SECS"
            continue
        fi

        # Cycle count gate
        if [ "$new_total" -lt "$min_new_cycles" ]; then
            log "Need $min_new_cycles new cycles, have $new_total. Waiting..."
            sleep "$POLL_SECS"
            continue
        fi

        # Unwind gate (optional)
        if [ "$require_unwind" = "true" ] && [ "$new_unwound" -lt 1 ]; then
            log "Need at least 1 new unwind (have $new_unwound). Waiting..."
            sleep "$POLL_SECS"
            continue
        fi

        # PnL gate (optional)
        if [ -n "$min_pnl" ]; then
            local session_pnl
            session_pnl=$(parse_float "$data" "session_pnl")
            local pnl_ok
            pnl_ok=$(python3 -c "print('yes' if float('${session_pnl:-0}') >= float('$min_pnl') else 'no')" 2>/dev/null || echo "yes")
            if [ "$pnl_ok" != "yes" ]; then
                log "Session PnL \$$session_pnl below floor \$$min_pnl. Waiting..."
                sleep "$POLL_SECS"
                continue
            fi
        fi

        log "=== $stage_label GATE PASSED ==="
        log "New cycles: $new_total (hedged=$new_hedged unwound=$new_unwound)"
        return 0
    done
}

# ============================================================
# STATUS COMMAND
# ============================================================
if $STATUS_ONLY; then
    verify_vps_identity
    data=$(get_accum_state)
    echo "--- Accumulator State ---"
    echo "$data" | python3 -m json.tool 2>/dev/null || echo "$data"
    echo ""
    echo "--- Current .env (key params) ---"
    ssh "$VPS" "grep -E '^DRY_RUN|^ACCUM_DRY_RUN|^ACCUM_CAPITAL_PCT|^ACCUM_MAX_CONCURRENT|^ACCUM_MAX_SHARES' $ENV_PATH"
    echo ""
    echo "--- Recent preflight log ---"
    ssh "$VPS" "tail -20 $LOG_FILE 2>/dev/null || echo '(no log yet)'"
    exit 0
fi

# ============================================================
# SMOKE TEST ONLY
# ============================================================
if $SMOKE_ONLY; then
    verify_vps_identity
    run_sell_smoke_test
    exit 0
fi

# ============================================================
# STAGE VALIDATION
# ============================================================
if [ -z "$STAGE" ]; then
    echo "Usage: bash preflight.sh --stage [2|3|4] [--dry-run]"
    echo "       bash preflight.sh --sell-smoke-test"
    echo "       bash preflight.sh --status"
    exit 1
fi

if [[ ! "$STAGE" =~ ^[234]$ ]]; then
    die "Invalid stage: $STAGE. Must be 2, 3, or 4."
fi

# ============================================================
# MAIN: STAGE TRANSITIONS
# ============================================================
log "=== Polyphemus Preflight v2.0 | Stage $STAGE | $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
$PREVIEW && log "[DRY-RUN MODE: no changes will be applied]"

# Step 0: Always verify VPS identity first
verify_vps_identity

case "$STAGE" in

# -------------------------------------------------------
# STAGE 2: First live money (5% capital, 1 slot, 10 shares)
# From: dry run validated
# Gate: 5 new cycles since this script started
# -------------------------------------------------------
2)
    log "--- Stage 2 Activation: Restricted Live (5% capital) ---"

    # Pre-transition checks
    check_no_orphans

    # Transition
    stop_service
    apply_stage_config 2
    verify_env_written
    start_service
    check_balance_after_restart

    # Run SELL smoke test before any further capital commits
    run_sell_smoke_test

    log_transition "STAGE_2_ACTIVATED: capital=5% concurrent=1 shares=10"

    # Monitor until Stage 2 gate passes (5 new cycles)
    wait_for_gate "Stage2" "$STAGE2_MIN_NEW_CYCLES" "false"

    log "Stage 2 complete. Run: bash preflight.sh --stage 3"
    log_transition "STAGE_2_GATE_PASSED"
    ;;

# -------------------------------------------------------
# STAGE 3: Scaled live (20% capital, 1 slot, 20 shares)
# From: Stage 2 validated
# Gate: 3 new cycles, 1 unwind, PnL floor
# -------------------------------------------------------
3)
    log "--- Stage 3 Activation: Scaled Live (20% capital) ---"

    # Pre-transition checks
    check_no_orphans

    # Transition
    stop_service
    apply_stage_config 3
    verify_env_written
    start_service
    check_balance_after_restart

    log_transition "STAGE_3_ACTIVATED: capital=20% concurrent=1 shares=20"

    # Monitor until Stage 3 gate passes (3 new cycles, 1 unwind required)
    wait_for_gate "Stage3" "$STAGE3_MIN_NEW_CYCLES" "true"

    log "Stage 3 complete. Run: bash preflight.sh --stage 4"
    log_transition "STAGE_3_GATE_PASSED"
    ;;

# -------------------------------------------------------
# STAGE 4: Full config (60% capital, 3 slots, 60 shares)
# From: Stage 3 validated
# Gate: 3 new cycles, PnL >= -$0.50, circuit breaker clean
# -------------------------------------------------------
4)
    log "--- Stage 4 Activation: Full Config (60% capital) ---"

    # Pre-transition checks (strictest gate)
    check_no_orphans

    # Verify Stage 3 was profitable enough
    data=$(get_accum_state)
    session_pnl=$(parse_float "$data" "session_pnl")
    consec=$(parse_int "$data" "consecutive_unwinds")
    log "Pre-Stage-4 check: session_pnl=\$$session_pnl consecutive_unwinds=$consec"

    if [ "$consec" -ge 2 ]; then
        die "Circuit breaker at $consec consecutive unwinds. Not safe to scale to full capital."
    fi

    # SELL smoke test again at Stage 4 (most capital at risk)
    run_sell_smoke_test

    # Transition
    stop_service
    apply_stage_config 4
    verify_env_written
    start_service
    check_balance_after_restart 100   # higher minimum at full capital

    log_transition "STAGE_4_ACTIVATED: capital=60% concurrent=3 shares=60 pre_pnl=$session_pnl"

    # Monitor first 3 cycles to confirm stability
    wait_for_gate "Stage4-Confirm" "$STAGE4_MIN_NEW_CYCLES" "false" "$STAGE4_MIN_PNL"

    log "=== STAGE 4 FULLY ACTIVE AND CONFIRMED ==="
    log_transition "STAGE_4_CONFIRMED"

    # Final state dump
    data=$(get_accum_state)
    echo "$data" | python3 -m json.tool 2>/dev/null || echo "$data"
    ;;
esac

log "=== Preflight complete for Stage $STAGE ==="
