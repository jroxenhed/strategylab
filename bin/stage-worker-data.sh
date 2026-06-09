#!/usr/bin/env bash
# stage-worker-data.sh — Stage research data artifacts on mfcore01 (F385)
#
# Usage:
#   bin/stage-worker-data.sh [--dataset DATASET] [--worker WORKER_HOST]
#
# Datasets (--dataset):
#   form4_stratified   Rsync form4_stratified/ XMLs + index.json from local repo
#                      to worker. This is the primary WORKER_REQUIRE gate artifact.
#                      Source: local backend/data/turnaround/edgar_cache/form4_stratified/
#   form4_datasets     Fetch SEC bulk quarterly Form 3/4/5 ZIPs (2015q1..2026q1)
#                      directly on the worker via fetch_form4_datasets.py.
#                      ~480 MB, ~70s on worker (native, no local→worker transfer needed).
#   all                Run both (default).
#
# Options:
#   --worker WORKER_HOST   SSH host to target (default: mfcore01)
#   --dry-run              Print what would run; no SSH or rsync
#
# Idempotency:
#   - form4_stratified: rsync --ignore-existing skips files already on worker
#   - form4_datasets:   fetch_form4_datasets.py skips ZIPs that pass zip integrity check
#
# After staging, verify WORKER_REQUIRE pre-flight passes:
#   bin/worker-probe.sh
#   WORKER_REQUIRE="backend/data/turnaround/edgar_cache/form4_stratified/index.json,\
# backend/data/turnaround/edgar_cache/submissions,\
# backend/data/turnaround/price_cache/v1" \
#     bin/worker-dispatch.sh /tmp/probe-out probe.log echo "pre-flight OK"
#
# Monitoring detached runs:
#   ssh mfcore01 "tail -f ~/strategylab/backend/data/turnaround/edgar_cache/form4_datasets/fetch.log"
#
# Context: F385 — staging unblocks premise explore/confirm for sell premise p-1569aa97
# which was UNTESTABLE on the small local price cache.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_HOST="${WORKER_HOST:-mfcore01}"
DATASET="all"
DRY_RUN=0

# ── arg parse ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dataset)   DATASET="$2";      shift 2 ;;
        --worker)    WORKER_HOST="$2";  shift 2 ;;
        --dry-run)   DRY_RUN=1;         shift   ;;
        -h|--help)
            sed -n '2,40p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)  echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

# ── helpers ───────────────────────────────────────────────────────────────────
log() { echo "[stage-worker-data] $*"; }
run_or_dry() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

WORKER_REPO="~/strategylab"
LOCAL_TURNAROUND="$REPO_ROOT/backend/data/turnaround"
WORKER_TURNAROUND="$WORKER_REPO/backend/data/turnaround"

# ── form4_stratified: rsync local → worker ────────────────────────────────────
stage_form4_stratified() {
    local src="$LOCAL_TURNAROUND/edgar_cache/form4_stratified/"
    local dst="$WORKER_HOST:$WORKER_TURNAROUND/edgar_cache/form4_stratified/"
    if [[ ! -d "$src" ]]; then
        echo "ERROR: local form4_stratified not found at $src" >&2
        echo "  Run insider_stratified.py locally first to generate it." >&2
        return 1
    fi
    local n_local
    n_local=$(ls "$src" | wc -l | tr -d ' ')
    log "form4_stratified: rsyncing $n_local files from local to $WORKER_HOST ..."
    run_or_dry ssh "$WORKER_HOST" "mkdir -p $WORKER_TURNAROUND/edgar_cache/form4_stratified"
    run_or_dry rsync -az --ignore-existing \
        "$src" "$dst"
    log "form4_stratified: done"
    if [[ "$DRY_RUN" -eq 0 ]]; then
        local n_worker
        n_worker=$(ssh "$WORKER_HOST" "ls $WORKER_TURNAROUND/edgar_cache/form4_stratified/ 2>/dev/null | wc -l" | tr -d ' ')
        log "form4_stratified: worker now has $n_worker files (local=$n_local)"
        if ssh "$WORKER_HOST" "test -f $WORKER_TURNAROUND/edgar_cache/form4_stratified/index.json"; then
            log "form4_stratified: WORKER_REQUIRE gate: PASS (index.json present)"
        else
            echo "ERROR: index.json missing after rsync" >&2
            return 1
        fi
    fi
}

# ── form4_datasets: fetch bulk ZIPs on worker ─────────────────────────────────
stage_form4_datasets() {
    local log_path="$WORKER_TURNAROUND/edgar_cache/form4_datasets/fetch.log"
    local fetch_script="$WORKER_REPO/backend/research/fetch_form4_datasets.py"
    local python="$WORKER_REPO/backend/venv/bin/python"
    log "form4_datasets: launching detached fetch on $WORKER_HOST ..."
    log "form4_datasets: log -> ssh $WORKER_HOST 'tail -f $log_path'"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "[DRY-RUN] ssh $WORKER_HOST mkdir -p $WORKER_TURNAROUND/edgar_cache/form4_datasets"
        echo "[DRY-RUN] ssh $WORKER_HOST nohup $python $fetch_script > $log_path 2>&1 &"
        return 0
    fi
    ssh "$WORKER_HOST" "mkdir -p $WORKER_TURNAROUND/edgar_cache/form4_datasets"
    # Check if already complete (all 45 ZIPs present and valid)
    local n_zips
    n_zips=$(ssh "$WORKER_HOST" "ls $WORKER_TURNAROUND/edgar_cache/form4_datasets/*.zip 2>/dev/null | wc -l" | tr -d ' ')
    if [[ "$n_zips" -ge 45 ]]; then
        log "form4_datasets: $n_zips ZIPs already present — skipping fetch (idempotent)"
        return 0
    fi
    local pid
    pid=$(ssh "$WORKER_HOST" "nohup $python $fetch_script > $log_path 2>&1 & echo \$!")
    log "form4_datasets: launched PID=$pid on $WORKER_HOST"
    log "form4_datasets: monitor: ssh $WORKER_HOST 'tail -f $log_path'"
    log "form4_datasets: expected ~45 ZIPs (~480 MB), ~70-120s on worker"
}

# ── main ──────────────────────────────────────────────────────────────────────
log "Worker: $WORKER_HOST  Dataset: $DATASET  DryRun: $DRY_RUN"

case "$DATASET" in
    form4_stratified)  stage_form4_stratified ;;
    form4_datasets)    stage_form4_datasets   ;;
    all)
        stage_form4_stratified
        stage_form4_datasets
        ;;
    *)
        echo "Unknown dataset: $DATASET (choices: form4_stratified, form4_datasets, all)" >&2
        exit 1
        ;;
esac

log "Done. Run 'bin/worker-probe.sh' to confirm worker reachability."
if [[ "$DRY_RUN" -eq 0 ]]; then
    log "Pre-flight summary:"
    ssh "$WORKER_HOST" 'python3 -c "
from pathlib import Path
base = Path.home() / \"strategylab\"
checks = [
    \"backend/data/turnaround/edgar_cache/form4_stratified/index.json\",
    \"backend/data/turnaround/edgar_cache/submissions\",
    \"backend/data/turnaround/price_cache/v1\",
]
all_ok = True
for r in checks:
    p = base / r
    ok = p.exists()
    status = \"PASS\" if ok else \"FAIL\"
    print(\"  \" + status + \": \" + r)
    if not ok:
        all_ok = False
print()
gate = \"PASS\" if all_ok else \"FAIL\"
print(\"  WORKER_REQUIRE pre-flight: \" + gate)
"'
fi
