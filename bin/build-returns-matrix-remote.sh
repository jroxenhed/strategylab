#!/bin/bash
# build-returns-matrix-remote.sh — F357 matrix build on strategylab-worker.
#
# VERIFY: All assumptions about the remote host are marked with # VERIFY:.
# Run from the repo root on the M1 Mac.
#
# Usage:
#   bash bin/build-returns-matrix-remote.sh [--dry-run]
#
# Prerequisites:
#   - ssh strategylab-worker resolves to 192.168.1.195
#   - Remote user: john (NOT jroxenhed)
#   - Remote has Python 3.10+ and the venv at ~/strategylab/backend/venv
#   - Price cache (~15-20 GB) already on remote at ~/strategylab/backend/data/turnaround/price_cache/

set -euo pipefail

# VERIFY: confirm these paths are correct on the remote host
WORKER_HOST="strategylab-worker"
WORKER_USER="john"
# VERIFY: confirm remote repo path
REMOTE_REPO_DIR="$HOME/strategylab"  # This expands on LOCAL — see note below
REMOTE_REPO_PATH="~/strategylab"     # Tilde expands on remote

LOCAL_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$LOCAL_REPO_ROOT/.run/F357"
LOG_FILE="$RUN_DIR/remote_build.log"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
    echo "[dry-run] Would execute remote build on $WORKER_HOST"
fi

mkdir -p "$RUN_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [INFO] Starting remote build script" | tee -a "$LOG_FILE"

# ---------------------------------------------------------------------------
# Step 1: Sync code to remote (research module + returns_matrix.py)
# ---------------------------------------------------------------------------
echo "[1] Syncing research module + turnaround_validation.py to $WORKER_HOST..." | tee -a "$LOG_FILE"

if [[ $DRY_RUN -eq 0 ]]; then
    # VERIFY: confirm remote venv exists at this path
    rsync -avz --progress \
        "$LOCAL_REPO_ROOT/backend/research/" \
        "$WORKER_USER@$WORKER_HOST:$REMOTE_REPO_PATH/backend/research/" \
        2>&1 | tee -a "$LOG_FILE"

    rsync -avz --progress \
        "$LOCAL_REPO_ROOT/backend/turnaround_validation.py" \
        "$LOCAL_REPO_ROOT/backend/fileutil.py" \
        "$WORKER_USER@$WORKER_HOST:$REMOTE_REPO_PATH/backend/" \
        2>&1 | tee -a "$LOG_FILE"
else
    echo "[dry-run] rsync research/ + turnaround_validation.py -> $WORKER_HOST"
fi

# Note: price cache is NOT synced here — assumes it's already on the remote.
# To sync the price cache (15-20 GB, one-time):
#   rsync -avz --progress \
#       "$LOCAL_REPO_ROOT/backend/data/turnaround/price_cache/" \
#       "$WORKER_USER@$WORKER_HOST:$REMOTE_REPO_PATH/backend/data/turnaround/price_cache/"

# ---------------------------------------------------------------------------
# Step 2: Run matrix builder on remote
# ---------------------------------------------------------------------------
# VERIFY: confirm venv path on remote (may differ on Windows + Linux)
REMOTE_PYTHON="$REMOTE_REPO_PATH/backend/venv/bin/python3"
REMOTE_OUTPUT="$REMOTE_REPO_PATH/backend/data/universe_matrix.parquet"
REMOTE_LOG="$REMOTE_REPO_PATH/.run/F357/run.log"

echo "[2] Launching matrix build on $WORKER_HOST (remote log: $REMOTE_LOG)..." | tee -a "$LOG_FILE"
echo "    Tailing remote log is the supported progress channel:" | tee -a "$LOG_FILE"
echo "    ssh $WORKER_HOST 'tail -f $REMOTE_LOG'" | tee -a "$LOG_FILE"

if [[ $DRY_RUN -eq 0 ]]; then
    # VERIFY: confirm remote python path and module invocation style
    # --log-file must be set for long runs (John: always have a way to follow progress)
    ssh "$WORKER_USER@$WORKER_HOST" "
        set -euo pipefail
        mkdir -p \$(dirname $REMOTE_LOG)
        cd $REMOTE_REPO_PATH/backend
        $REMOTE_PYTHON -m research.returns_matrix \
            --start 2015-01-02 \
            --end 2024-12-31 \
            --output $REMOTE_OUTPUT \
            --log-file $REMOTE_LOG \
            2>&1 | tee -a $REMOTE_LOG
    " 2>&1 | tee -a "$LOG_FILE"
else
    echo "[dry-run] Would run: $REMOTE_PYTHON -m research.returns_matrix --start 2015-01-02 --end 2024-12-31 --output $REMOTE_OUTPUT --log-file $REMOTE_LOG"
fi

# ---------------------------------------------------------------------------
# Step 3: Pull artifact back
# ---------------------------------------------------------------------------
LOCAL_ARTIFACT="$LOCAL_REPO_ROOT/backend/data/universe_matrix.parquet"
LOCAL_META="$LOCAL_REPO_ROOT/backend/data/universe_matrix_meta.json"

echo "[3] Pulling artifact from $WORKER_HOST..." | tee -a "$LOG_FILE"

if [[ $DRY_RUN -eq 0 ]]; then
    # VERIFY: rsync of a directory (partitioned parquet) — note trailing slash behavior
    rsync -avz --progress \
        "$WORKER_USER@$WORKER_HOST:$REMOTE_OUTPUT/" \
        "$LOCAL_ARTIFACT/" \
        2>&1 | tee -a "$LOG_FILE"

    # VERIFY: meta.json is a sibling of the parquet dir
    REMOTE_META="${REMOTE_OUTPUT%.*}_meta.json"
    scp "$WORKER_USER@$WORKER_HOST:$REMOTE_META" "$LOCAL_META" \
        2>&1 | tee -a "$LOG_FILE"
else
    echo "[dry-run] Would pull $REMOTE_OUTPUT -> $LOCAL_ARTIFACT"
fi

# ---------------------------------------------------------------------------
# Step 4: Local verification
# ---------------------------------------------------------------------------
echo "[4] Verifying local artifact..." | tee -a "$LOG_FILE"

if [[ $DRY_RUN -eq 0 ]]; then
    "$LOCAL_REPO_ROOT/backend/venv/bin/python3" - <<'EOF' 2>&1 | tee -a "$LOG_FILE"
import sys
from pathlib import Path
import pandas as pd
import json

matrix_path = Path("backend/data/universe_matrix.parquet")
meta_path = Path("backend/data/universe_matrix_meta.json")

if not matrix_path.exists():
    print("ERROR: matrix not found at", matrix_path)
    sys.exit(1)

print(f"Matrix dir: {matrix_path}")
total_size = sum(f.stat().st_size for f in matrix_path.rglob("*") if f.is_file())
print(f"Total size: {total_size / 1e6:.1f} MB")

# Read one partition as a quick check
df = pd.read_parquet(str(matrix_path), filters=[("horizon_days", "=", 21)])
print(f"  horizon=21: {len(df)} rows, {df['symbol'].nunique()} tickers, "
      f"dates {df['entry_date'].min()} to {df['entry_date'].max()}")

if meta_path.exists():
    meta = json.loads(meta_path.read_text())
    print(f"\nMetadata:")
    print(f"  build_date: {meta.get('build_date')}")
    print(f"  row_count: {meta.get('row_count')}")
    print(f"  seal_status: {meta.get('seal_status')}")
    print(f"  float_precision: {meta.get('float_precision')}")
    print(f"  data_range: {meta.get('data_range')}")
EOF
else
    echo "[dry-run] Would verify artifact at $LOCAL_ARTIFACT"
fi

echo "[5] Remote build script complete." | tee -a "$LOG_FILE"
echo "    Log: $LOG_FILE" | tee -a "$LOG_FILE"
