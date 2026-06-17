#!/usr/bin/env bash
# Robust worker-job status — reads the completion sentinel written by
# worker-run.sh (NOT pgrep, which races and can match the poller itself).
#
# Usage:
#   [WORKER_HOST=… WORKER_SHELL=wsl|native] bin/worker-status.sh <outdir> <logname> [--wait [timeout_secs]]
#
# Prints one STATUS line + the last few log lines:
#   STATUS=RUNNING            — sentinel absent, job still going
#   STATUS=DONE exit=0        — finished cleanly
#   STATUS=FAILED exit=N      — finished with a non-zero exit code
# --wait polls every 15s until DONE/FAILED or timeout (default 3600s), then
# exits 0 (DONE), 1 (FAILED), or 2 (TIMEOUT) so scripts can branch on it.

set -uo pipefail

WORKER_HOST="${WORKER_HOST:-strategylab-worker}"
WORKER_SHELL="${WORKER_SHELL:-wsl}"

rwrap() {
  local inner="$1"
  if [[ "$WORKER_SHELL" == "native" ]]; then printf '%s' "$inner"
  else printf 'wsl bash -lc "%s"' "$inner"; fi
}
ssh_clean() { grep -vE "post-quantum|store now|openssh|upgraded|vulnerable" || true; }

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <outdir> <logname> [--wait [timeout_secs]]" >&2
  exit 64
fi
OUTDIR="$1"; LOGNAME="$2"; shift 2
WAIT=0; TIMEOUT=3600; INTERVAL=15
if [[ "${1:-}" == "--wait" ]]; then WAIT=1; [[ -n "${2:-}" ]] && TIMEOUT="$2"; fi

DONE_REL="$OUTDIR/.${LOGNAME}.done"
LOG_REL="$OUTDIR/$LOGNAME"

# One ssh round-trip: emit the sentinel content (if any) then the log tail,
# bracketed by markers so the LOCAL side interprets — keeps the remote command
# trivial (echo/cat/tail), no remote command-substitution to re-quote.
_remote_probe() {
  # G6: quote path vars inside the remote command to prevent word-splitting/injection
  local inner="echo ===EXIT===; cat ~/strategylab/\"$DONE_REL\" 2>/dev/null; echo ===TAIL===; tail -4 ~/strategylab/\"$LOG_REL\" 2>/dev/null"
  ssh -o ConnectTimeout=15 -o BatchMode=yes "$WORKER_HOST" "$(rwrap "$inner")" 2>&1 | ssh_clean
}

# Echoes the STATUS line + tail; returns 0=DONE, 1=FAILED, 3=RUNNING.
check() {
  local raw ec tail_lines
  raw="$(_remote_probe)"
  ec="$(printf '%s\n' "$raw" | sed -n '/===EXIT===/,/===TAIL===/p' | grep -vE '===EXIT===|===TAIL===' | head -1 | tr -d '[:space:]')"
  tail_lines="$(printf '%s\n' "$raw" | sed -n '/===TAIL===/,$p' | grep -v '===TAIL===')"
  local rc
  if [[ -z "$ec" ]]; then echo "STATUS=RUNNING"; rc=3
  elif [[ "$ec" == "0" ]]; then echo "STATUS=DONE exit=0"; rc=0
  else echo "STATUS=FAILED exit=$ec"; rc=1; fi
  [[ -n "$tail_lines" ]] && { echo "---"; printf '%s\n' "$tail_lines"; }
  return $rc
}

if [[ "$WAIT" == 0 ]]; then
  check; exit $?
fi

elapsed=0
while (( elapsed < TIMEOUT )); do
  out="$(check)"; rc=$?
  printf '%s\n' "$out"
  if [[ $rc -ne 3 ]]; then exit $rc; fi
  sleep "$INTERVAL"; elapsed=$(( elapsed + INTERVAL ))
done
echo "STATUS=TIMEOUT after ${TIMEOUT}s" >&2
exit 2
