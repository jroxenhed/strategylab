#!/usr/bin/env bash
# Worker reachability probe (F387)
#
# Answers "which compute worker can I dispatch to RIGHT NOW?" — run this before
# bin/worker-dispatch.sh so an unreachable host doesn't hang the orchestrator on
# an SSH timeout. John moves between home / office / on-the-road-with-laptop, so
# the reachable set changes per session.
#
# Usage:
#   bin/worker-probe.sh            # probe all known workers, print recommendation
#   bin/worker-probe.sh --quiet    # only emit the RECOMMEND line (for scripting)
#
# Output (human + parseable):
#   home   (strategylab-worker, wsl)    UP    host=DESKTOP-... 24 cpu
#   office (mfcore01, native)           DOWN  (unreachable)
#   RECOMMEND: WORKER_HOST=mfcore01 WORKER_SHELL=native
#   # or, if none reachable:
#   RECOMMEND: LOCAL  (no worker reachable — run compute locally)
#
# Exit code: 0 if at least one worker is UP, 1 if none (→ run local).

set -uo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

CONNECT_TIMEOUT="${WORKER_PROBE_TIMEOUT:-6}"

ssh_clean() { grep -vE "post-quantum|store now|openssh|upgraded|vulnerable" || true; }

# Known workers: label|ssh-alias|shell. Preference order top-to-bottom when
# multiple are UP (office mfcore01 first: 32c native RHEL9, no wsl indirection).
WORKERS=(
  "office|mfcore01|native"
  "home|strategylab-worker|wsl"
)

# Wrap an inner command for the worker's shell (mirrors worker-dispatch rwrap).
rwrap() {
  local shell="$1" inner="$2"
  if [[ "$shell" == "native" ]]; then printf '%s' "$inner"
  else printf 'wsl bash -lc "%s"' "$inner"; fi
}

# Probe one worker. Echoes "UP <detail>" or "DOWN <reason>" on stdout.
probe_one() {
  local alias="$1" shell="$2"
  # Single round-trip: confirm shell works + report hostname & cpu count.
  local inner='echo PROBE_OK; hostname; nproc 2>/dev/null || echo "?"'
  local raw
  raw="$(ssh -o ConnectTimeout="$CONNECT_TIMEOUT" -o BatchMode=yes "$alias" \
         "$(rwrap "$shell" "$inner")" 2>&1 | ssh_clean)"
  if printf '%s\n' "$raw" | grep -q PROBE_OK; then
    local host cpu
    host="$(printf '%s\n' "$raw" | grep -A2 PROBE_OK | sed -n '2p' | tr -d '[:space:]')"
    cpu="$(printf '%s\n' "$raw" | grep -A2 PROBE_OK | sed -n '3p' | tr -d '[:space:]')"
    echo "UP host=${host:-?} ${cpu:-?} cpu"
  else
    echo "DOWN (unreachable)"
  fi
}

# Probe all workers in parallel; collect results into temp files keyed by index.
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

i=0
for entry in "${WORKERS[@]}"; do
  IFS='|' read -r label alias shell <<<"$entry"
  probe_one "$alias" "$shell" >"$tmpdir/$i" &
  i=$((i + 1))
done
wait

# Report + pick first UP worker as the recommendation.
rec_host="" rec_shell=""
i=0
for entry in "${WORKERS[@]}"; do
  IFS='|' read -r label alias shell <<<"$entry"
  result="$(cat "$tmpdir/$i")"
  if [[ "$QUIET" == 0 ]]; then
    printf '%-7s %-20s %-7s %s\n' "$label" "$alias" "$shell" "$result"
  fi
  if [[ -z "$rec_host" && "$result" == UP* ]]; then
    rec_host="$alias"; rec_shell="$shell"
  fi
  i=$((i + 1))
done

if [[ -n "$rec_host" ]]; then
  echo "RECOMMEND: WORKER_HOST=$rec_host WORKER_SHELL=$rec_shell"
  exit 0
else
  echo "RECOMMEND: LOCAL  (no worker reachable — run compute locally)"
  exit 1
fi
