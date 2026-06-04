#!/usr/bin/env bash
# verify-batch.sh — single-call verification gate (F292)
#
# Runs all standard gate checks in order:
#   1. Frontend build  (npm run build)
#   2. Preview server  (start if :4173 not up; reuse if already up)
#   3. Render probe    (bin/render-probe.mjs --url http://localhost:4173)
#   4. Backend smoke   (AST + import-time check on all backend .py files)
#
# Outputs a PASS/FAIL table to stdout and writes it to
#   .run/<task-id>/verify.md
#
# Exit 0 if all gates pass, 1 otherwise.
#
# Usage:
#   bin/verify-batch.sh <task-id>
#
# Prerequisites:
#   - Node / npm installed; frontend/ already has node_modules
#   - backend/venv/bin/python (falls back to python3)
#   - Backend on :8000 must already be running for the render probe
#
# Notes:
#   - Never uses bare `cd` in compound commands (cwd-poisoning guard)
#   - All paths are absolute (resolved from script's own location)
#   - On early gate failure, independent later gates still run where meaningful
#     (backend smoke doesn't depend on build; probe needs both build + preview)
#   - Failure output is bounded to tail -20 per failed gate
#
# Preview-server leave-running policy (COR-03/REL-02):
#   The preview server started by this script is deliberately left running
#   after the script exits so repeated gate runs can reuse it without the
#   30-second startup penalty.  There is NO EXIT trap to kill it.  To stop
#   it manually: kill $(lsof -ti:4173).  Callers that need isolation from
#   a prior build should kill any existing server before invoking this script.

set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
BACKEND_DIR="$REPO_ROOT/backend"
BIN_DIR="$REPO_ROOT/bin"
PYTHON="${BACKEND_DIR}/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi
PORT=4173
PREVIEW_PID=""

# ── Args ──────────────────────────────────────────────────────────────────────

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <task-id>" >&2
  exit 1
fi

TASK_ID="$1"
# DIG-05: sanitise TASK_ID to prevent path traversal out of .run/
if [[ ! "$TASK_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: task-id must match ^[A-Za-z0-9._-]+\$ (got: ${TASK_ID})" >&2
  echo "Usage: $0 <task-id>" >&2
  exit 2
fi
RUN_DIR="$REPO_ROOT/.run/$TASK_ID"
mkdir -p "$RUN_DIR"
REPORT_FILE="$RUN_DIR/verify.md"

# ── Gate tracking ─────────────────────────────────────────────────────────────

declare -a GATE_NAMES=()
declare -a GATE_STATUS=()
declare -a GATE_DETAIL=()

record_gate() {
  local name="$1"
  local status="$2"   # PASS or FAIL
  local detail="${3:-}"
  GATE_NAMES+=("$name")
  GATE_STATUS+=("$status")
  GATE_DETAIL+=("$detail")
}

# ── Helpers ───────────────────────────────────────────────────────────────────

print_table() {
  printf '\n%-28s  %-6s  %s\n' "Gate" "Status" "Detail"
  printf '%s\n' "$(printf '%0.s-' {1..68})"
  local i
  for i in "${!GATE_NAMES[@]}"; do
    printf '%-28s  %-6s  %s\n' "${GATE_NAMES[$i]}" "${GATE_STATUS[$i]}" "${GATE_DETAIL[$i]:-}"
  done
  printf '%s\n\n' "$(printf '%0.s-' {1..68})"
}

write_report() {
  # REL-04: write to a temp file then rename atomically so a mid-write crash
  # never leaves a truncated report that could be misread as all-pass.
  local tmp_file="${REPORT_FILE}.tmp"
  {
    echo "# verify-batch — $TASK_ID"
    echo ""
    echo "Run: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo ""
    echo "| Gate | Status | Detail |"
    echo "|------|--------|--------|"
    local i
    for i in "${!GATE_NAMES[@]}"; do
      echo "| ${GATE_NAMES[$i]} | ${GATE_STATUS[$i]} | ${GATE_DETAIL[$i]:-} |"
    done
    echo ""
    # Append per-gate failure output if any
    for i in "${!GATE_NAMES[@]}"; do
      local log_file="$RUN_DIR/gate-${i}.log"
      if [[ "${GATE_STATUS[$i]}" == "FAIL" && -f "$log_file" ]]; then
        echo "### ${GATE_NAMES[$i]} failure output (tail -20)"
        echo '```'
        tail -20 "$log_file"
        echo '```'
        echo ""
      fi
    done
  } > "$tmp_file" && mv "$tmp_file" "$REPORT_FILE"
}

# cleanup_preview — manual helper only; NOT registered as an EXIT trap.
# Per the leave-running policy (see header), the preview server is left alive
# so repeated gate runs can reuse it.  Call manually only if you need to stop
# it between builds: cleanup_preview; or kill $(lsof -ti:4173).
cleanup_preview() {
  if [[ -n "$PREVIEW_PID" ]]; then
    kill "$PREVIEW_PID" 2>/dev/null || true
  fi
  local port_pids
  port_pids=$(lsof -ti:"$PORT" 2>/dev/null || true)
  if [[ -n "$port_pids" ]]; then
    # shellcheck disable=SC2086
    kill $port_pids 2>/dev/null || true
  fi
}

# ── Gate 1: Frontend build ────────────────────────────────────────────────────

echo "==> Gate 1: frontend build"
BUILD_LOG="$RUN_DIR/gate-0.log"
BUILD_OK=0

if npm --prefix "$FRONTEND_DIR" run build > "$BUILD_LOG" 2>&1; then
  BUILD_OK=1
  record_gate "1. frontend build" "PASS"
  echo "    PASS"
else
  record_gate "1. frontend build" "FAIL" "see gate-0.log"
  echo "    FAIL — tail -20:"
  tail -20 "$BUILD_LOG" | sed 's/^/    /'
fi

# ── Gate 2: Preview server (start or reuse) ───────────────────────────────────
# Only attempt if build passed (preview needs dist/)

echo ""
echo "==> Gate 2: preview server (:$PORT)"
PREVIEW_OK=0
PREVIEW_STARTED_HERE=0

if [[ "$BUILD_OK" -eq 0 ]]; then
  record_gate "2. preview server" "FAIL" "skipped — build failed"
  echo "    SKIPPED (build failed)"
else
  # Check if :4173 is already responding
  if curl -fsS "http://localhost:$PORT/" > /dev/null 2>&1; then
    PREVIEW_OK=1
    record_gate "2. preview server" "PASS" "already up on :$PORT"
    echo "    PASS (reused existing server on :$PORT)"
  else
    # Start preview server in background
    # COR-02: use gate-1.log so write_report() finds it by index convention
    # COR-03: direct background (no nohup subshell) so $! is the real npm PID;
    #         PREVIEW_PID is stored but cleanup relies on lsof fallback since
    #         npm spawns vite as a child — see leave-running policy in header.
    PREVIEW_STARTED_HERE=1
    PREVIEW_LOG="$RUN_DIR/gate-1.log"
    echo "    starting vite preview on :$PORT …"
    npm --prefix "$FRONTEND_DIR" run preview > "$PREVIEW_LOG" 2>&1 &
    PREVIEW_PID=$!

    # Poll until port responds (up to 15s)
    PREVIEW_READY=0
    for _i in $(seq 1 30); do
      if curl -fsS "http://localhost:$PORT/" > /dev/null 2>&1; then
        PREVIEW_READY=1
        break
      fi
      sleep 0.5
    done

    if [[ "$PREVIEW_READY" -eq 1 ]]; then
      # COR-03: capture the real vite PID (npm may spawn a child process)
      PREVIEW_PID=$(lsof -ti:"$PORT" 2>/dev/null | head -1 || true)
      PREVIEW_OK=1
      record_gate "2. preview server" "PASS" "started on :$PORT"
      echo "    PASS (server started on :$PORT)"
    else
      record_gate "2. preview server" "FAIL" "port $PORT never became ready after 15s"
      echo "    FAIL — server log tail:"
      tail -10 "$PREVIEW_LOG" 2>/dev/null | sed 's/^/    /' || true
    fi
  fi
fi

# ── Gate 3: Render probe ──────────────────────────────────────────────────────
# Needs both build and preview to be up

echo ""
echo "==> Gate 3: render probe (bin/render-probe.mjs --url http://localhost:$PORT)"
PROBE_LOG="$RUN_DIR/gate-2.log"

if [[ "$BUILD_OK" -eq 0 || "$PREVIEW_OK" -eq 0 ]]; then
  record_gate "3. render probe" "FAIL" "skipped — build or preview failed"
  echo "    SKIPPED (build or preview not ready)"
else
  if node "$BIN_DIR/render-probe.mjs" --url "http://localhost:$PORT" > "$PROBE_LOG" 2>&1; then
    record_gate "3. render probe" "PASS"
    echo "    PASS"
  else
    EXIT_CODE=$?
    if [[ "$EXIT_CODE" -eq 2 ]]; then
      record_gate "3. render probe" "FAIL" "environment unreachable (exit 2)"
    else
      record_gate "3. render probe" "FAIL" "one or more probe checks failed (exit $EXIT_CODE)"
    fi
    echo "    FAIL (exit $EXIT_CODE) — tail -20:"
    tail -20 "$PROBE_LOG" | sed 's/^/    /'
  fi
fi

# Per leave-running policy: do NOT kill the preview server here.
# It stays alive for reuse by the next gate run.

# ── Gate 4: Backend smoke (AST + import-time) ─────────────────────────────────
# Independent of frontend gates — always runs

echo ""
echo "==> Gate 4: backend smoke (AST + import-time check)"
SMOKE_LOG="$RUN_DIR/gate-3.log"

{
  cd_cmd="PYTHONPATH=$BACKEND_DIR"
  echo "--- AST parse check on all backend .py files ---"

  SMOKE_FAIL=0

  # AST parse every .py file in backend/ (excluding venv)
  while IFS= read -r -d '' pyfile; do
    if ! "$PYTHON" -c "import ast; ast.parse(open('$pyfile').read())" 2>&1; then
      echo "AST FAIL: $pyfile"
      SMOKE_FAIL=1
    fi
  done < <(find "$BACKEND_DIR" -name '*.py' -not -path '*/venv/*' -print0)

  if [[ "$SMOKE_FAIL" -eq 0 ]]; then
    echo "AST parse: all files OK"
  fi

  echo ""
  echo "--- Import-time check on all backend modules ---"

  # Build module list: backend/*.py and backend/**/*.py (excluding venv, __pycache__)
  MODULE_LIST=$( \
    find "$BACKEND_DIR" -name '*.py' \
      -not -path '*/venv/*' \
      -not -path '*/__pycache__/*' \
      -not -name '__init__.py' \
      -print \
    | while IFS= read -r f; do
        # Convert absolute path to dotted module name relative to backend/
        rel="${f#$BACKEND_DIR/}"
        mod="${rel%.py}"
        mod="${mod//\//.}"
        echo "$mod"
      done \
    | sort
  )

  IMPORT_FAIL=0
  while IFS= read -r mod; do
    if ! PYTHONPATH="$BACKEND_DIR" "$PYTHON" -c "import importlib; importlib.import_module('$mod'); print('  imported: $mod')" 2>&1; then
      echo "IMPORT FAIL: $mod"
      IMPORT_FAIL=1
    fi
  done <<< "$MODULE_LIST"

  # COR-01: do NOT call exit inside a bash { } group — it would terminate
  # the whole script before the summary table and verify.md are written.
  # Instead, propagate failure via a non-zero last command so SMOKE_EXIT
  # (captured after the group) reflects the real outcome.
  if [[ "$SMOKE_FAIL" -ne 0 || "$IMPORT_FAIL" -ne 0 ]]; then
    false  # makes the group's exit status non-zero → SMOKE_EXIT set correctly
  else
    echo "Import-time check passed."
  fi
} > "$SMOKE_LOG" 2>&1
SMOKE_EXIT=$?

if [[ "$SMOKE_EXIT" -eq 0 ]]; then
  record_gate "4. backend smoke" "PASS"
  echo "    PASS"
else
  record_gate "4. backend smoke" "FAIL" "AST or import-time error — see gate-3.log"
  echo "    FAIL — tail -20:"
  tail -20 "$SMOKE_LOG" | sed 's/^/    /'
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
print_table
write_report
echo "Report written → $REPORT_FILE"

# Determine overall exit code
OVERALL=0
for status in "${GATE_STATUS[@]}"; do
  if [[ "$status" == "FAIL" ]]; then
    OVERALL=1
    break
  fi
done

if [[ "$OVERALL" -eq 0 ]]; then
  echo "All gates PASS."
else
  echo "One or more gates FAILED."
fi

exit "$OVERALL"
