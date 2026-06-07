#!/usr/bin/env bash
# Generic worker job launcher — run a backend/research driver on the WSL worker
# with a followable log, backgrounded so the ssh call returns immediately.
#
# Usage (on the worker, via: ssh strategylab-worker 'wsl bash -lc "bash ~/strategylab/bin/worker-run.sh <outdir> <logname> <module-args...>"'):
#   bash bin/worker-run.sh .run/F369 census_worker.log backend/research/premise_power_census.py --family all --out .run/F369
#
# Avoids nested-quote hell through cmd.exe -> wsl -> bash by living in a file.
set -euo pipefail
cd "$HOME/strategylab"

OUTDIR="$1"; shift
LOGNAME="$1"; shift
# remaining args = the python invocation (script + its flags)

mkdir -p "$OUTDIR"
LOG="$OUTDIR/$LOGNAME"

# Verify deps before launching (fail loud, in the log)
backend/venv/bin/python3 - <<'PY' >"$LOG" 2>&1
import pandas, numpy, pyarrow
print(f"deps_ok pandas={pandas.__version__} numpy={numpy.__version__} pyarrow={pyarrow.__version__}")
PY

nohup backend/venv/bin/python3 "$@" >>"$LOG" 2>&1 &
PID=$!
echo "WORKER_RUN_LAUNCHED pid=$PID log=$LOG"
echo "host=$(hostname) cores=$(nproc) started=$(date -u +%FT%TZ)" >>"$LOG"
