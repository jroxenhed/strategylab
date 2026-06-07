#!/bin/bash
# build-returns-matrix-remote.sh — F357 matrix build on strategylab-worker.
#
# REWRITTEN 2026-06-07 from measured reality (the original's # VERIFY: assumptions
# all failed verification — see JOURNAL 2026-06-07):
#   - SSH to the worker lands in Windows cmd.exe, NOT a POSIX shell.
#     All remote work runs inside WSL2 Ubuntu via:  ssh ... 'wsl bash -lc "..."'
#   - No rsync on the Windows hop — bulk data moves via tar-over-ssh pipe.
#   - Code syncs via git (worker repo is a clone at ~/strategylab inside WSL).
#   - Price cache measured 4.1GB (not 15-20GB); syncs in ~95s on LAN.
#   - nohup does NOT survive wsl.exe exit — long runs hold the SSH session open
#     (run this script under nohup/screen on the MAC side instead).
#
# Worker env (provisioned 2026-06-07, ~4 min from scratch):
#   WSL2 Ubuntu 24.04, python3.12, venv at ~/strategylab/backend/venv,
#   full backend/requirements.txt installed (research stack imports reach
#   fastapi/httpx via shared.py + edgar.py — lean subsets fail).
#
# Usage (from repo root on the Mac):
#   bash bin/build-returns-matrix-remote.sh [--skip-cache-sync]
#
# Progress channels (per working agreement — always followable):
#   - chunk log:    ssh strategylab-worker 'wsl bash -lc "tail -f ~/strategylab/.run/F357/remote_build.log"'
#   - within-chunk: ssh strategylab-worker 'wsl bash -lc "cat ~/strategylab/backend/data/universe_matrix.parquet.progress/chunk_*.txt | tail"'

set -euo pipefail

WORKER="john@strategylab-worker"
WSL() { ssh "$WORKER" "wsl bash -lc \"$1\""; }

LOCAL_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$LOCAL_REPO_ROOT/.run/F357"
mkdir -p "$RUN_DIR"

SKIP_CACHE=0
[[ "${1:-}" == "--skip-cache-sync" ]] && SKIP_CACHE=1

echo "[1/4] Code sync: git pull on worker (repo must be pushed first)..."
WSL "cd ~/strategylab && git pull --ff-only && git log --oneline -1"

if [[ $SKIP_CACHE -eq 0 ]]; then
    echo "[2/4] Price cache sync (~4GB, ~2 min on LAN; tar-pipe — no rsync on Windows hop)..."
    (cd "$LOCAL_REPO_ROOT/backend/data/turnaround" && tar cf - price_cache) | \
        ssh "$WORKER" 'wsl bash -lc "mkdir -p ~/strategylab/backend/data/turnaround && cd ~/strategylab/backend/data/turnaround && tar xf - && ls price_cache/v1 | wc -l"'
else
    echo "[2/4] Cache sync skipped (--skip-cache-sync)."
fi

echo "[3/4] Matrix build on worker (SSH session held open — do not close; ~6 min cache-hot)..."
# Seal guard: --end 2024-12-31 (2025+ entry dates need a charter unlocking confirm).
WSL "cd ~/strategylab/backend && mkdir -p ~/strategylab/.run/F357 && ./venv/bin/python3 -m research.returns_matrix \
    --start 2015-01-02 --end 2024-12-31 \
    --output ~/strategylab/backend/data/universe_matrix.parquet \
    --log-file ~/strategylab/.run/F357/remote_build.log \
    --max-workers 31" 2>&1 | tee -a "$RUN_DIR/remote_build_local_echo.log"

echo "[4/4] Pull artifact back (parquet dir contains _meta.json sidecar)..."
ssh "$WORKER" 'wsl bash -lc "cd ~/strategylab/backend/data && tar cf - universe_matrix.parquet"' | \
    (cd "$LOCAL_REPO_ROOT/backend/data" && tar xf -)

"$LOCAL_REPO_ROOT/backend/venv/bin/python3" - <<'EOF'
import json
from pathlib import Path
p = Path("backend/data/universe_matrix.parquet")
meta = json.loads((p / "_meta.json").read_text())
size_mb = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6
print(f"Artifact: {p} ({size_mb:.0f} MB)")
print(f"  status={meta['status']}  rows={meta['row_count']:,}")
print(f"  range={meta['data_range']['entry_date_first']} → {meta['data_range']['entry_date_last']}")
cov = meta.get("ticker_coverage", {})
print(f"  coverage: {cov.get('tickers_with_rows')} with rows, "
      f"{cov.get('no_frame_count')} no-frame (nonzero ⇒ rerun and compare!), "
      f"{cov.get('no_rows_count')} no-rows")
EOF

echo "Done. Determinism check: rerun with --skip-cache-sync and diff the two artifacts."
