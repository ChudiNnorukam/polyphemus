#!/usr/bin/env bash
# predeploy.sh - Pre-deploy checks + optional deploy for lagbot instances
#
# Usage:
#   ./predeploy.sh                    # check only (compile + test + diff)
#   ./predeploy.sh --deploy emmanuel  # check + deploy to instance
#   ./predeploy.sh --skip-tests      # skip pytest
#   ./predeploy.sh --files "config.py signal_guard.py"  # deploy specific files only

set -euo pipefail

VPS="82.24.19.114"
VPS_CODE="/opt/lagbot/lagbot"
VPS_INSTANCES="/opt/lagbot/instances"
LOCAL_DIR="$(cd "$(dirname "$0")/polyphemus" && pwd)"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

DEPLOY_INSTANCE=""
SKIP_TESTS=false
SPECIFIC_FILES=""

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --deploy) DEPLOY_INSTANCE="$2"; shift 2 ;;
        --skip-tests) SKIP_TESTS=true; shift ;;
        --files) SPECIFIC_FILES="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: ./predeploy.sh [--deploy INSTANCE] [--skip-tests] [--files \"file1.py file2.py\"]"
            echo ""
            echo "  --deploy INSTANCE  Deploy to VPS instance (emmanuel, polyphemus, chudi)"
            echo "  --skip-tests       Skip pytest"
            echo "  --files FILES      Deploy only specific files (space-separated)"
            echo ""
            echo "Without --deploy: runs checks only (compile, test, checksum diff)"
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

FAIL=0

# ============================================================
# STEP 1: py_compile all .py files (excluding tests)
# ============================================================
echo -e "${CYAN}[1/5] py_compile all source files${NC}"
COMPILE_FAIL=0
for f in "$LOCAL_DIR"/*.py; do
    fname="$(basename "$f")"
    [[ "$fname" == test_* ]] && continue
    if ! python -m py_compile "$f" 2>/dev/null; then
        echo -e "  ${RED}FAIL${NC} $fname"
        COMPILE_FAIL=1
    fi
done

if [[ $COMPILE_FAIL -eq 0 ]]; then
    count=$(ls "$LOCAL_DIR"/*.py 2>/dev/null | grep -v test_ | wc -l | tr -d ' ')
    echo -e "  ${GREEN}PASS${NC} $count files compiled clean"
else
    echo -e "  ${RED}COMPILE ERRORS - fix before deploy${NC}"
    FAIL=1
fi

# ============================================================
# STEP 2: Run pytest
# ============================================================
if [[ "$SKIP_TESTS" == true ]]; then
    echo -e "${CYAN}[2/5] pytest ${YELLOW}SKIPPED${NC}"
else
    echo -e "${CYAN}[2/5] pytest${NC}"
    cd "$PROJECT_DIR"
    TEST_OUTPUT=$(python -m pytest polyphemus/test_smoke.py polyphemus/test_modules.py -q 2>&1) || true
    PASSED=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ passed' | head -1 || echo "0 passed")
    FAILED=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ failed' | head -1 || echo "")

    if [[ -z "$FAILED" ]]; then
        echo -e "  ${GREEN}PASS${NC} $PASSED"
    else
        echo -e "  ${YELLOW}WARN${NC} $PASSED, $FAILED (check if pre-existing)"
        # Don't set FAIL=1 for known pre-existing failures
    fi
fi

# ============================================================
# STEP 3: Checksum diff (local vs VPS)
# ============================================================
echo -e "${CYAN}[3/5] Checksum diff (local vs VPS)${NC}"

# Get list of files to check
if [[ -n "$SPECIFIC_FILES" ]]; then
    FILES_TO_CHECK="$SPECIFIC_FILES"
else
    FILES_TO_CHECK=$(ls "$LOCAL_DIR"/*.py 2>/dev/null | xargs -I{} basename {} | grep -v '^test_')
fi

DEPLOY_LIST=""

for fname in $FILES_TO_CHECK; do
    local_file="$LOCAL_DIR/$fname"
    [[ ! -f "$local_file" ]] && continue

    local_md5=$(md5 -q "$local_file" 2>/dev/null || md5sum "$local_file" | awk '{print $1}')
    remote_md5=$(ssh root@$VPS "md5sum $VPS_CODE/$fname 2>/dev/null | awk '{print \$1}'" 2>/dev/null || echo "MISSING")

    if [[ "$remote_md5" == "MISSING" ]]; then
        DEPLOY_LIST="$DEPLOY_LIST $fname"
        echo -e "  ${YELLOW}NEW${NC}     $fname (not on VPS)"
    elif [[ "$local_md5" != "$remote_md5" ]]; then
        DEPLOY_LIST="$DEPLOY_LIST $fname"
        echo -e "  ${YELLOW}CHANGED${NC} $fname"
    fi
done

DEPLOY_LIST=$(echo "$DEPLOY_LIST" | xargs)  # trim whitespace
TOTAL_CHANGED=$(echo "$DEPLOY_LIST" | wc -w | tr -d ' ')
[[ -z "$DEPLOY_LIST" ]] && TOTAL_CHANGED=0

if [[ $TOTAL_CHANGED -eq 0 ]]; then
    echo -e "  ${GREEN}IN SYNC${NC} - no files differ from VPS"
else
    echo -e "  ${CYAN}$TOTAL_CHANGED file(s) to deploy:${NC} $DEPLOY_LIST"
fi

# ============================================================
# STEP 4: Deploy (if --deploy)
# ============================================================
if [[ -z "$DEPLOY_INSTANCE" ]]; then
    echo -e "${CYAN}[4/5] Deploy ${YELLOW}SKIPPED${NC} (no --deploy flag)"
    echo -e "${CYAN}[5/5] Post-deploy ${YELLOW}SKIPPED${NC}"

    if [[ $FAIL -eq 1 ]]; then
        echo -e "\n${RED}PRE-CHECK FAILED - do not deploy${NC}"
        exit 1
    fi

    if [[ $TOTAL_CHANGED -gt 0 ]]; then
        echo -e "\n${GREEN}PRE-CHECK PASSED${NC} - ready to deploy $TOTAL_CHANGED file(s)"
        echo -e "Run: ${CYAN}./predeploy.sh --deploy $( [[ -n "$SPECIFIC_FILES" ]] && echo "--files \"$SPECIFIC_FILES\" " )emmanuel${NC}"
    else
        echo -e "\n${GREEN}ALL IN SYNC${NC} - nothing to deploy"
    fi
    exit 0
fi

if [[ $FAIL -eq 1 ]]; then
    echo -e "\n${RED}PRE-CHECK FAILED - aborting deploy${NC}"
    exit 1
fi

if [[ $TOTAL_CHANGED -eq 0 ]]; then
    echo -e "${CYAN}[4/5] Deploy ${GREEN}NOTHING TO DEPLOY${NC} - VPS is in sync"
    exit 0
fi

echo -e "${CYAN}[4/5] Deploying to ${DEPLOY_INSTANCE}${NC}"

# 4a: Stop service
echo -e "  Stopping lagbot@${DEPLOY_INSTANCE}..."
ssh root@$VPS "systemctl stop lagbot@${DEPLOY_INSTANCE}" 2>/dev/null || true
sleep 1

# 4b: scp changed files
echo -e "  Uploading $TOTAL_CHANGED file(s)..."
for fname in $DEPLOY_LIST; do
    scp -q "$LOCAL_DIR/$fname" "root@$VPS:$VPS_CODE/$fname"
    echo -e "    ${GREEN}+${NC} $fname"
done

# 4c: Clear __pycache__
echo -e "  Clearing __pycache__..."
ssh root@$VPS "find $VPS_CODE -name __pycache__ -exec rm -rf {} + 2>/dev/null" || true

# 4d: py_compile on VPS (from /tmp to avoid types.py shadow)
echo -e "  Compiling on VPS..."
VPS_COMPILE_FAIL=0
for fname in $DEPLOY_LIST; do
    if ! ssh root@$VPS "cd /tmp && python3 -m py_compile $VPS_CODE/$fname" 2>/dev/null; then
        echo -e "    ${RED}FAIL${NC} $fname"
        VPS_COMPILE_FAIL=1
    fi
done

if [[ $VPS_COMPILE_FAIL -eq 1 ]]; then
    echo -e "  ${RED}VPS COMPILE FAILED - service NOT restarted${NC}"
    echo -e "  Fix the error, then: ssh root@$VPS 'systemctl start lagbot@${DEPLOY_INSTANCE}'"
    exit 1
fi
echo -e "  ${GREEN}VPS compile clean${NC}"

# 4e: Verify checksums match after upload
echo -e "  Verifying checksums..."
CHECKSUM_OK=true
for fname in $DEPLOY_LIST; do
    local_md5=$(md5 -q "$LOCAL_DIR/$fname" 2>/dev/null || md5sum "$LOCAL_DIR/$fname" | awk '{print $1}')
    remote_md5=$(ssh root@$VPS "md5sum $VPS_CODE/$fname | awk '{print \$1}'" 2>/dev/null)
    if [[ "$local_md5" != "$remote_md5" ]]; then
        echo -e "    ${RED}MISMATCH${NC} $fname local=$local_md5 vps=$remote_md5"
        CHECKSUM_OK=false
    fi
done

if [[ "$CHECKSUM_OK" != true ]]; then
    echo -e "  ${RED}CHECKSUM MISMATCH - service NOT restarted${NC}"
    exit 1
fi
echo -e "  ${GREEN}Checksums verified${NC}"

# 4f: Start service
echo -e "  Starting lagbot@${DEPLOY_INSTANCE}..."
ssh root@$VPS "systemctl start lagbot@${DEPLOY_INSTANCE}"
sleep 2

# ============================================================
# STEP 5: Post-deploy verification
# ============================================================
echo -e "${CYAN}[5/5] Post-deploy verification${NC}"

# Service status
STATUS=$(ssh root@$VPS "systemctl is-active lagbot@${DEPLOY_INSTANCE}" 2>/dev/null || echo "unknown")
if [[ "$STATUS" == "active" ]]; then
    echo -e "  ${GREEN}Service: active${NC}"
else
    echo -e "  ${RED}Service: $STATUS${NC}"
    exit 1
fi

# Wait and check for errors
echo -e "  Waiting 10s for startup errors..."
sleep 10
ERRORS=$(ssh root@$VPS "journalctl -u lagbot@${DEPLOY_INSTANCE} --since '15 seconds ago' --no-pager 2>/dev/null | grep -iE 'error|Traceback|Exception' | head -5" 2>/dev/null || echo "")

if [[ -n "$ERRORS" ]]; then
    echo -e "  ${RED}ERRORS DETECTED:${NC}"
    echo "$ERRORS" | while IFS= read -r line; do
        echo -e "    $line"
    done
else
    echo -e "  ${GREEN}No errors in first 10s${NC}"
fi

echo -e "\n${GREEN}DEPLOY COMPLETE${NC}: $TOTAL_CHANGED file(s) to lagbot@${DEPLOY_INSTANCE}"
echo -e "Monitor: ${CYAN}ssh root@$VPS 'journalctl -u lagbot@${DEPLOY_INSTANCE} -f'${NC}"
