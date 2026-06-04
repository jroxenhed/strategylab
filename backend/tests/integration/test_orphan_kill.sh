#!/usr/bin/env bash
# test_orphan_kill.sh — F279b integration test
#
# Verifies that SIGKILL on the backend mid-WFA leaves zero orphaned
# multiprocessing.spawn workers. Uses a dedicated port (8765) and scopes
# all process assertions to the actual captured worker PIDs (not PGID),
# eliminating false positives from OS PGID recycling.
#
# NETWORK DEPENDENCY: requires live internet access for yfinance (AAPL daily
# data). Not suitable for offline/airgapped CI as-is. Mitigation: pre-seed
# the yfinance cache by hitting /api/ohlcv for AAPL daily before running
# this test, or stand up a yfinance mock/fixture server.
#
# Usage:
#   backend/tests/integration/test_orphan_kill.sh [--port PORT] [--timeout SECS]
#
# Exit codes: 0 = PASS, 1 = FAIL

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BACKEND_PORT=8765
READY_TIMEOUT=20      # seconds to wait for uvicorn to answer
WORKER_WAIT=30        # seconds to wait for spawn workers to appear
BOOTSTRAP_SETTLE=5    # seconds to wait AFTER workers appear for Python import bootstrap to complete
                      # Workers appear in pgrep as soon as the OS creates the process, before
                      # _init_worker runs. The F288 watchdog starts in _init_worker, which runs
                      # after ~2-3s of Python module imports (pandas, numpy, fastapi, etc.).
                      # Killing before bootstrap completes is a narrow edge case not covered by
                      # the watchdog (the pipe-read blocks until EOF anyway). Waiting here
                      # tests the real production scenario: server killed mid-computation.
                      # F294: that bootstrap window is ACCEPTED + documented (see the
                      # _init_worker docstring in backend/routes/wfa_pool.py) — do not
                      # lower BOOTSTRAP_SETTLE to "cover" it; the test would just flake.
GRACE_AFTER_KILL=3    # seconds to let the OS reap children after SIGKILL

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)     BACKEND_PORT="$2"; shift 2 ;;
    --timeout)  READY_TIMEOUT="$2"; shift 2 ;;
    *)          echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve repo/backend root (script may be run from any cwd)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"   # backend/
REPO_DIR="$(cd "$BACKEND_DIR/.." && pwd)"
UVICORN="$BACKEND_DIR/venv/bin/uvicorn"
PYTHON="$BACKEND_DIR/venv/bin/python3"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
for f in "$BACKEND_DIR/main.py" "$BACKEND_DIR/routes/wfa_pool.py" "$UVICORN" "$PYTHON"; do
  if [[ ! -e "$f" ]]; then
    echo "FAIL: required path missing: $f"
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Temp workspace
# ---------------------------------------------------------------------------
TMPDIR_TEST="$(mktemp -d /tmp/f279b_test_XXXXXX)"
BACKEND_LOG="$TMPDIR_TEST/backend.log"
PAYLOAD_FILE="$TMPDIR_TEST/wfa_payload.json"
CURL_OUT="$TMPDIR_TEST/curl_out.txt"

BACKEND_PID=""
CURL_PID=""

# Worker PIDs captured in phase 3; checked in phase 5.
WORKER_PIDS_ARRAY=""   # space-separated string (bash 3.2 compat — no mapfile)

# ---------------------------------------------------------------------------
# Cleanup trap — always runs on exit (normal or error)
# ---------------------------------------------------------------------------
cleanup() {
  local exit_code=$?

  # Kill the background WFA curl if still running (belt-and-suspenders;
  # explicit kills before fail() handle the common paths).
  if [[ -n "$CURL_PID" ]] && kill -0 "$CURL_PID" 2>/dev/null; then
    kill "$CURL_PID" 2>/dev/null || true
  fi

  # Kill the backend's entire process group if it's still alive.
  # Because the backend was launched as its own session leader (setsid via
  # python3 exec wrapper), kill -9 -$BACKEND_PID targets ONLY the backend's
  # group and never reaches the test script's own process group. Safe.
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "[cleanup] Killing backend process group (PGID=$BACKEND_PID)"
    kill -9 -"$BACKEND_PID" 2>/dev/null || true
  fi

  # Belt-and-suspenders: kill any captured worker PIDs that survived.
  for pid in $WORKER_PIDS_ARRAY; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "[cleanup] Killing surviving worker PID $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  done

  rm -rf "$TMPDIR_TEST"
  exit "$exit_code"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "FAIL: $*"; exit 1; }
pass() { echo "PASS: $*"; exit 0; }

# ---------------------------------------------------------------------------
# Phase 1: Write WFA payload
# ---------------------------------------------------------------------------
# 6 combos (3 stop_loss × 2 position_size) across ~10 windows (2018-2024,
# step=100 daily bars each). Expected runtime ~15–40s — plenty of time to
# catch workers mid-flight. min_trades_is=0 so every window has an IS winner.
cat > "$PAYLOAD_FILE" <<'EOF'
{
  "base": {
    "ticker": "AAPL",
    "start": "2018-01-01",
    "end": "2024-06-01",
    "interval": "1d",
    "buy_rules":  [{"indicator": "rsi", "condition": "below", "value": 30}],
    "sell_rules": [{"indicator": "rsi", "condition": "above", "value": 70}],
    "initial_capital": 10000.0,
    "min_per_order": 0.0,
    "per_share_rate": 0.0,
    "position_size": 1.0
  },
  "params": [
    {"path": "stop_loss_pct",  "values": [2.0, 3.0, 5.0]},
    {"path": "position_size",  "values": [0.5, 1.0]}
  ],
  "is_bars":        150,
  "oos_bars":       100,
  "gap_bars":       0,
  "step_bars":      100,
  "metric":         "sharpe_ratio",
  "min_trades_is":  0
}
EOF

# ---------------------------------------------------------------------------
# Phase 2: Start backend as its own session leader (C-01 / R-03 fix)
# ---------------------------------------------------------------------------
# macOS has no `setsid` binary. We use a python3 exec wrapper so the backend
# becomes a session/process-group leader. Its PGID == its PID deterministically,
# which eliminates the fragile `ps -o pgid` capture race (R-03, C-05) and
# ensures cleanup's `kill -9 -$BACKEND_PID` never reaches the test script's
# own process group (C-01).
log "Phase 2 — Starting backend on port $BACKEND_PORT"

# Ensure port is free
if lsof -iTCP:"$BACKEND_PORT" -sTCP:LISTEN -t &>/dev/null; then
  fail "Port $BACKEND_PORT already in use. Pass --port to pick another."
fi

"$PYTHON" -c \
  'import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
  "$UVICORN" main:app \
  --port "$BACKEND_PORT" \
  --log-level warning \
  --app-dir "$BACKEND_DIR" \
  >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

# With setsid, the backend is its own session leader so PGID == PID exactly.
log "  Backend PID=$BACKEND_PID (PGID=$BACKEND_PID, own session leader)"

# Poll for readiness
log "Phase 2 — Waiting for backend to be ready (up to ${READY_TIMEOUT}s)..."
DEADLINE=$(( $(date +%s) + READY_TIMEOUT ))
while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "[backend log]"; cat "$BACKEND_LOG" || true
    fail "Backend process died before becoming ready"
  fi
  if curl -sf --max-time 2 "http://127.0.0.1:$BACKEND_PORT/openapi.json" -o /dev/null 2>/dev/null; then
    log "  Backend ready"
    break
  fi
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    echo "[backend log]"; cat "$BACKEND_LOG" || true
    fail "Backend did not become ready within ${READY_TIMEOUT}s"
  fi
  sleep 1
done

# ---------------------------------------------------------------------------
# Phase 3: Kick off WFA run in background
# ---------------------------------------------------------------------------
log "Phase 3 — POSTing WFA request (background)"
curl -s --max-time 120 \
  -X POST "http://127.0.0.1:$BACKEND_PORT/api/backtest/walk_forward" \
  -H "Content-Type: application/json" \
  -d @"$PAYLOAD_FILE" \
  -o "$CURL_OUT" &
CURL_PID=$!

# Wait until multiprocessing.spawn workers appear (R-01 fix: capture actual PIDs).
log "Phase 3 — Polling for spawn workers (up to ${WORKER_WAIT}s)..."
DEADLINE=$(( $(date +%s) + WORKER_WAIT ))
WORKER_COUNT=0
while true; do
  if [[ $(date +%s) -ge $DEADLINE ]]; then
    # Kill curl before fail() to avoid 90s stall (R-02 fix)
    kill "$CURL_PID" 2>/dev/null || true
    # Last-chance: dump what we see
    log "  Timeout waiting for workers. Process snapshot:"
    ps -g "$BACKEND_PID" 2>/dev/null || ps aux | grep -E "multiprocessing|uvicorn" | grep -v grep || true
    fail "No multiprocessing.spawn workers appeared within ${WORKER_WAIT}s. WFA may be running serially (< 4 windows) or completed too fast."
  fi

  # Check if WFA already finished (curl_pid gone) — too fast to catch mid-flight
  if ! kill -0 "$CURL_PID" 2>/dev/null; then
    fail "WFA request completed before workers were detected. Grid too small or yfinance cache too fast. Increase grid size."
  fi

  # Look for spawn workers whose PGID matches the backend's process group.
  # With setsid, backend PGID == BACKEND_PID.
  WORKER_PIDS_RAW=$(pgrep -g "$BACKEND_PID" -f "multiprocessing.spawn" 2>/dev/null || true)

  if [[ -n "$WORKER_PIDS_RAW" ]]; then
    # Capture into space-separated string for PID-specific checking in phase 5
    # (R-01 fix; bash 3.2 compat — no mapfile, use word splitting on iteration)
    WORKER_PIDS_ARRAY="$WORKER_PIDS_RAW"
    WORKER_COUNT=$(echo "$WORKER_PIDS_RAW" | wc -l | tr -d ' ')
    log "  Found $WORKER_COUNT spawn worker(s): $(echo "$WORKER_PIDS_RAW" | tr '\n' ' ')"
    break
  fi
  sleep 0.5
done

# F288: Wait for workers to finish Python bootstrap (module imports) and reach
# _init_worker where the parent-death watchdog thread is started. Workers appear
# in pgrep immediately after OS process creation, but the watchdog only starts
# after ~2-3s of Python imports. Killing earlier tests the pipe-EOF path, not
# the watchdog path. The real production scenario (server killed mid-computation)
# always has workers past bootstrap, so this wait gives a true signal.
log "Phase 3 — Waiting ${BOOTSTRAP_SETTLE}s for workers to finish bootstrap (watchdog start)"
sleep "$BOOTSTRAP_SETTLE"

# ---------------------------------------------------------------------------
# Phase 4: SIGKILL backend mid-flight
# ---------------------------------------------------------------------------
log "Phase 4 — SIGKILL backend PID $BACKEND_PID"
kill -9 "$BACKEND_PID" 2>/dev/null || true
BACKEND_PID=""   # prevent cleanup from double-killing

# Wait for curl to notice the connection drop
wait "$CURL_PID" 2>/dev/null || true
CURL_PID=""

# Grace period for OS to reap children
log "Phase 4 — Waiting ${GRACE_AFTER_KILL}s for OS to reap children..."
sleep "$GRACE_AFTER_KILL"

# ---------------------------------------------------------------------------
# Phase 5: Assert zero orphans — by PID, not PGID (R-01 fix)
# ---------------------------------------------------------------------------
# Check each captured worker PID individually:
#   1. kill -0 to see if still alive
#   2. If alive, verify it's actually a multiprocessing.spawn process (not a
#      recycled PID running something unrelated) before declaring orphan.
log "Phase 5 — Checking for orphaned spawn workers (by captured PID)"

ORPHAN_COUNT=0
ORPHAN_PIDS=""   # space-separated

for pid in $WORKER_PIDS_ARRAY; do
  if kill -0 "$pid" 2>/dev/null; then
    # PID is alive — confirm it's still a multiprocessing.spawn worker
    # (guards against PID reuse by an unrelated process)
    if ps -o command= -p "$pid" 2>/dev/null | grep -q "multiprocessing"; then
      ORPHAN_PIDS="$ORPHAN_PIDS $pid"
      ORPHAN_COUNT=$(( ORPHAN_COUNT + 1 ))
    else
      log "  PID $pid alive but no longer a multiprocessing.spawn process (PID reuse) — ignoring"
    fi
  fi
done

if [[ "$ORPHAN_COUNT" -eq 0 ]]; then
  log "  Orphan count: 0"
  pass "F279 orphan-kill works correctly — zero surviving workers after SIGKILL"
else
  log "  FAIL — $ORPHAN_COUNT orphan process(es) survived SIGKILL:"
  for pid in $ORPHAN_PIDS; do
    ps -p "$pid" -o pid,ppid,pgid,command 2>/dev/null || echo "  PID $pid (already gone)"
  done
  exit 1
fi
