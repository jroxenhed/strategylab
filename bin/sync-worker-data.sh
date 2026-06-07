#!/bin/bash
# sync-worker-data.sh — Push gitignored data artifacts from M1 to strategylab-worker.
#
# PURPOSE: The F368 pre-flight manifest hard-fails if these artifacts are absent.
# Run this script from the Mac side before the first remote R-1b run on a new
# worker clone, or after new data is produced locally.
#
# IMPORTANT: nohup dies with wsl.exe — run from the Mac side, keep the session open.
# For large transfers use: nohup bash bin/sync-worker-data.sh [--full] > sync.log 2>&1 &
# then `tail -f sync.log` for progress.
#
# Usage:
#   bash bin/sync-worker-data.sh            # metadata artifacts only (~fast)
#   bash bin/sync-worker-data.sh --full     # + price_cache/v1 (~4.1GB, ~95s on LAN)
#
# Pattern: tar-over-ssh-pipe (rsync not available on the Windows hop).
#   tar -C <local-dir> -cz <item> | ssh john@strategylab-worker 'wsl bash -lc "tar -xz -C ~/strategylab/<dir>"'
#
# Idempotent — tar -xz overwrites on the remote side; existing files are replaced.
# Prints per-item before/after counts for verification.
#
# Items synced (always):
#   backend/data/regime_states.json
#   backend/data/turnaround/fdr_ledger.json
#   backend/data/turnaround/edgar_cache/derived/
#   backend/data/turnaround/edgar_cache/form4_datasets/
#   backend/data/turnaround/edgar_cache/submissions/  (incl. older_pages/)
# Items synced (--full only):
#   backend/data/turnaround/price_cache/v1/           (~4.1GB)

set -euo pipefail

WORKER="john@strategylab-worker"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
DATA="$BACKEND/data"
TURNAROUND="$DATA/turnaround"
EDGAR="$TURNAROUND/edgar_cache"

FULL=0
for arg in "$@"; do
    if [[ "$arg" == "--full" ]]; then
        FULL=1
    fi
done

# ---------------------------------------------------------------------------
# Helper: send one item via tar-over-ssh-pipe with before/after count reporting
# ---------------------------------------------------------------------------
sync_item() {
    local label="$1"           # human label
    local src_dir="$2"         # local directory to tar from
    local src_item="$3"        # item (file or subdir) to include in the tar
    local remote_dest_dir="$4" # remote directory to unpack into (inside ~/strategylab)

    echo ""
    echo "==> Syncing: $label"
    echo "    src: $src_dir/$src_item"
    echo "    dst: strategylab-worker:~/strategylab/$remote_dest_dir"

    # Before count (remote)
    local before
    before=$(ssh "$WORKER" "wsl bash -lc \"find ~/strategylab/$remote_dest_dir -mindepth 1 -maxdepth 2 2>/dev/null | wc -l || echo 0\"" 2>/dev/null || echo "0")
    echo "    remote before: $before items (depth<=2)"

    # Push via tar pipe
    tar -C "$src_dir" -cz "$src_item" \
        | ssh "$WORKER" "wsl bash -lc \"mkdir -p ~/strategylab/$remote_dest_dir && tar -xz -C ~/strategylab/$remote_dest_dir\""

    # After count (remote)
    local after
    after=$(ssh "$WORKER" "wsl bash -lc \"find ~/strategylab/$remote_dest_dir -mindepth 1 -maxdepth 2 2>/dev/null | wc -l || echo 0\"" 2>/dev/null || echo "?")
    echo "    remote after:  $after items (depth<=2)"
    echo "    OK"
}

# ---------------------------------------------------------------------------
# Validate local artifacts exist before attempting to send
# ---------------------------------------------------------------------------
echo "==> Validating local artifacts ..."
ERRORS=0

check_local() {
    local label="$1"; local path="$2"
    if [[ ! -e "$path" ]]; then
        echo "    ERROR: missing locally: $label ($path)"
        ERRORS=$((ERRORS + 1))
    else
        echo "    OK: $label"
    fi
}

check_local "regime_states.json"           "$DATA/regime_states.json"
check_local "fdr_ledger.json"              "$TURNAROUND/fdr_ledger.json"
check_local "edgar_cache/derived"          "$EDGAR/derived"
check_local "edgar_cache/form4_datasets"   "$EDGAR/form4_datasets"
check_local "edgar_cache/submissions"      "$EDGAR/submissions"
if [[ $FULL -eq 1 ]]; then
    check_local "price_cache/v1"           "$TURNAROUND/price_cache/v1"
fi

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo "ERROR: $ERRORS local artifact(s) missing. Fix before syncing."
    exit 1
fi

echo ""
echo "==> All local artifacts present. Starting sync to $WORKER ..."

# ---------------------------------------------------------------------------
# Sync each item
# ---------------------------------------------------------------------------

# 1. regime_states.json (single file — tar the file, unpack to the data dir)
sync_item \
    "regime_states.json" \
    "$DATA" \
    "regime_states.json" \
    "backend/data"

# 2. fdr_ledger.json
sync_item \
    "fdr_ledger.json" \
    "$TURNAROUND" \
    "fdr_ledger.json" \
    "backend/data/turnaround"

# 3. edgar_cache/derived/ (full subtree)
sync_item \
    "edgar_cache/derived" \
    "$EDGAR" \
    "derived" \
    "backend/data/turnaround/edgar_cache"

# 4. edgar_cache/form4_datasets/ (all quarter zips)
sync_item \
    "edgar_cache/form4_datasets" \
    "$EDGAR" \
    "form4_datasets" \
    "backend/data/turnaround/edgar_cache"

# 5. edgar_cache/submissions/ (incl. older_pages/)
sync_item \
    "edgar_cache/submissions (incl. older_pages)" \
    "$EDGAR" \
    "submissions" \
    "backend/data/turnaround/edgar_cache"

# 6. price_cache/v1 — optional, only with --full (~4.1GB)
if [[ $FULL -eq 1 ]]; then
    echo ""
    echo "==> --full: syncing price_cache/v1 (~4.1GB, may take ~90s on LAN) ..."
    sync_item \
        "price_cache/v1" \
        "$TURNAROUND/price_cache" \
        "v1" \
        "backend/data/turnaround/price_cache"
else
    echo ""
    echo "==> --full not set: skipping price_cache/v1 (~4.1GB)."
    echo "    Re-run with --full to include it."
fi

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
echo ""
echo "==> Sync complete."
echo "    On the worker, run the F368 pre-flight check:"
echo "      ssh $WORKER 'wsl bash -lc \"cd ~/strategylab && backend/venv/bin/python3 backend/research/run_r1b_explore.py --calibrate\"'"
echo "    The manifest check will hard-fail early if anything is still missing."
