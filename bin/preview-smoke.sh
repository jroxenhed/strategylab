#!/usr/bin/env bash
# preview-smoke.sh — Vite preview-server smoke test
#
# Starts `npm run preview` in the frontend directory, waits for port 4173,
# fetches the root page, and asserts the HTML contains the expected markers.
# Exits non-zero on any failure so the overnight chain halts.
#
# Usage:
#   cd /path/to/strategylab
#   bash bin/preview-smoke.sh
#
# Prerequisites: frontend/ must already be built (`npm run build`).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
PORT=4173
OUT="/tmp/preview-smoke-out.html"
PID=""

# Kill the preview server on exit, regardless of success or failure.
# `npm run preview` spawns a vite child that outlives the npm wrapper, so we
# kill by port rather than by the npm PID to catch both processes.
cleanup() {
  # Kill the npm wrapper first (best-effort).
  if [[ -n "$PID" ]]; then
    kill "$PID" 2>/dev/null || true
  fi
  # Kill anything still holding the port (covers the vite child).
  local port_pids
  port_pids=$(lsof -ti:"$PORT" 2>/dev/null || true)
  if [[ -n "$port_pids" ]]; then
    # shellcheck disable=SC2086
    kill $port_pids 2>/dev/null || true
  fi
  # Brief wait so the port is released before the caller inspects it.
  for _i in $(seq 1 10); do
    lsof -ti:"$PORT" > /dev/null 2>&1 || break
    sleep 0.2
  done
}
trap cleanup EXIT

# ── 1. Start preview server ───────────────────────────────────────────────────

echo "preview-smoke: starting vite preview on port $PORT …"
(cd "$FRONTEND_DIR" && npm run preview --silent > /tmp/preview-smoke-server.log 2>&1) &
PID=$!

# ── 2. Wait for the port to accept connections (up to ~10s) ──────────────────

READY=0
for i in $(seq 1 20); do
  if curl -fsS "http://localhost:$PORT/" > "$OUT" 2>/dev/null; then
    READY=1
    break
  fi
  sleep 0.5
done

if [[ "$READY" -eq 0 ]]; then
  echo "preview-smoke: port $PORT never became ready after 10s" >&2
  echo "  server log:" >&2
  cat /tmp/preview-smoke-server.log >&2
  exit 1
fi

# ── 3. Assert expected markers ────────────────────────────────────────────────

FAIL=0

if ! grep -q '<div id="root"' "$OUT"; then
  echo 'preview-smoke: missing <div id="root"> marker' >&2
  FAIL=1
fi

if ! grep -qE '<script[^>]+src="[^"]+\.js"' "$OUT"; then
  echo 'preview-smoke: no bundled <script src="*.js"> tag found' >&2
  FAIL=1
fi

# Reject known crash/error body markers.
for MARKER in "Error" "Cannot find module" "Failed to fetch"; do
  if grep -q "$MARKER" "$OUT"; then
    echo "preview-smoke: response body contains error marker: '$MARKER'" >&2
    FAIL=1
  fi
done

if [[ "$FAIL" -ne 0 ]]; then
  echo "preview-smoke: HTML response head:" >&2
  head -30 "$OUT" >&2
  exit 1
fi

echo "preview-smoke: OK — #root present, bundled script present, no error markers."
