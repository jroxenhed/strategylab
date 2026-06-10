#!/usr/bin/env bash
# Worker reachability probe (F387, extended F406)
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
#   home   strategylab-worker  wsl    UP    host=DESKTOP-... 24 cpu load=4.2/24 sat=17.5%
#   office mfcore01            native DOWN  (unreachable)
#   RECOMMEND: WORKER_HOST=mfcore01 WORKER_SHELL=native
#   # or, if none reachable:
#   RECOMMEND: LOCAL  (no worker reachable — run compute locally)
#
# Saturation ratio: 1-minute load average divided by number of cores.  A ratio
# ≥ SATURATION_THRESHOLD (default 0.8) marks a worker as saturated.  RECOMMEND
# prefers a worker that is BOTH reachable AND unsaturated; among multiple
# unsaturated workers, office (mfcore01) is preferred over home (preference order
# matches the WORKERS array).  If ALL UP workers are saturated, the least-saturated
# UP worker is recommended (still better than local).  Parse failures degrade
# gracefully: a worker with unparseable uptime (sat=?) is ranked BEHIND reachable
# workers with a confirmed parsed ratio but AHEAD of unreachable workers — it is
# still recommended if all others are DOWN or more saturated.
#
# Override threshold: SATURATION_THRESHOLD=0.9 bin/worker-probe.sh
#
# Exit code: 0 if at least one worker is UP, 1 if none (→ run local).

set -uo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

CONNECT_TIMEOUT="${WORKER_PROBE_TIMEOUT:-6}"
# Saturation threshold: load/cores ratio at or above this marks a worker as saturated.
SATURATION_THRESHOLD="${SATURATION_THRESHOLD:-0.8}"

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

# Probe one worker. Echoes a structured line on stdout:
#   UP host=<name> <nproc> cpu load=<1m-avg>/<cores> sat=<ratio%>
# or, when load parsing fails gracefully:
#   UP host=<name> <nproc> cpu load=?/? sat=?
# or on unreachable:
#   DOWN (unreachable)
probe_one() {
  local alias="$1" shell="$2"
  # Single round-trip: confirm shell works + report hostname, cpu count, and uptime.
  # uptime works on both Linux (including WSL) and macOS; output format is slightly
  # different but the load-average fields are in the same trailing position.
  local inner='echo PROBE_OK; hostname; nproc 2>/dev/null || echo "?"; uptime 2>/dev/null || echo "uptime_unavailable"'
  local raw
  raw="$(ssh -o ConnectTimeout="$CONNECT_TIMEOUT" -o BatchMode=yes "$alias" \
         "$(rwrap "$shell" "$inner")" 2>&1 | ssh_clean)"
  if printf '%s\n' "$raw" | grep -q PROBE_OK; then
    local host cpu load1 ratio_pct
    host="$(printf '%s\n' "$raw" | grep -A3 PROBE_OK | sed -n '2p' | tr -d '[:space:]')"
    cpu="$(printf '%s\n' "$raw" | grep -A3 PROBE_OK | sed -n '3p' | tr -d '[:space:]')"
    local uptime_line
    uptime_line="$(printf '%s\n' "$raw" | grep -A3 PROBE_OK | sed -n '4p')"
    # Parse the 1-minute load average from uptime output.
    # Both Linux and macOS end with: "load average: X.XX, Y.YY, Z.ZZ"
    # WSL uses the same Linux format.  Tolerate a missing or malformed line.
    load1="$(printf '%s\n' "$uptime_line" | grep -oE 'load average[s]?: *[0-9]+\.?[0-9]*' | grep -oE '[0-9]+\.?[0-9]*$' || true)"
    if [[ -n "$load1" && -n "$cpu" && "$cpu" != "?" && "$cpu" -gt 0 ]] 2>/dev/null; then
      # Compute ratio% using awk (bash can't do floating point).
      ratio_pct="$(awk "BEGIN { printf \"%.1f\", ($load1 / $cpu) * 100 }" 2>/dev/null || echo "?")"
      echo "UP host=${host:-?} ${cpu:-?} cpu load=${load1}/${cpu:-?} sat=${ratio_pct}%"
    else
      # Graceful degradation: reachability confirmed but load unknown.
      echo "UP host=${host:-?} ${cpu:-?} cpu load=?/? sat=?"
    fi
  else
    echo "DOWN (unreachable)"
  fi
}

# Extract numeric saturation ratio from a probe result line.
# Returns a large number (999) when the sat field is "?" (unparseable load) so that
# hosts with unknown saturation sort BEHIND reachable+parsed hosts.  This way a host
# whose uptime output we can parse and confirm as unsaturated is preferred over one
# we cannot parse.  If ALL reachable hosts have unparseable load, they all return 999
# and the tiebreaker falls to WORKERS array preference order — still better than LOCAL.
# (R-07: "sat=?" must NOT rank as best-possible unsaturated.)
parse_sat_ratio() {
  local result="$1"
  # result format: "UP host=X N cpu load=L/C sat=R%"
  # Extract the number before the '%' in sat=
  local pct
  pct="$(printf '%s\n' "$result" | grep -oE 'sat=[0-9]+\.[0-9]+%' | grep -oE '[0-9]+\.[0-9]+' || true)"
  if [[ -z "$pct" ]]; then
    # Unknown sat (sat=?) — treat as worst-case (999) so parsed hosts are preferred.
    echo "999"
  else
    echo "$pct"
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

# Report results and pick the best UP worker as the recommendation.
# Preference:
#   1. Reachable AND unsaturated (sat < SATURATION_THRESHOLD * 100)
#   2. Reachable but saturated (pick least-saturated among these)
#   3. None reachable → LOCAL
# Within each tier, preference order from the WORKERS array is the tiebreaker
# (earlier entry wins when saturation ratios are equal).
rec_host="" rec_shell="" rec_sat="9999"
i=0
for entry in "${WORKERS[@]}"; do
  IFS='|' read -r label alias shell <<<"$entry"
  result="$(cat "$tmpdir/$i")"
  if [[ "$QUIET" == 0 ]]; then
    printf '%-7s %-20s %-7s %s\n' "$label" "$alias" "$shell" "$result"
  fi
  if [[ "$result" == UP* ]]; then
    sat="$(parse_sat_ratio "$result")"
    # Threshold in percent (e.g. 0.8 → 80).
    # SEC-A: pass SATURATION_THRESHOLD via awk -v (not string interpolation) so
    # a crafted env value cannot inject awk code.
    threshold_pct="$(awk -v thresh="$SATURATION_THRESHOLD" 'BEGIN { printf "%.1f", thresh * 100 }' 2>/dev/null || echo "80")"
    # Is this worker unsaturated? (sat < threshold)
    is_unsaturated="$(awk "BEGIN { print ($sat < $threshold_pct) ? \"yes\" : \"no\" }" 2>/dev/null || echo "yes")"
    # Replace recommendation if:
    #   a) No recommendation yet, OR
    #   b) This worker is unsaturated AND current recommendation is saturated, OR
    #   c) Both have same saturation status AND this worker has lower sat ratio.
    if [[ -z "$rec_host" ]]; then
      rec_host="$alias"; rec_shell="$shell"; rec_sat="$sat"
    else
      rec_unsaturated="$(awk "BEGIN { print ($rec_sat < $threshold_pct) ? \"yes\" : \"no\" }" 2>/dev/null || echo "yes")"
      if [[ "$is_unsaturated" == "yes" && "$rec_unsaturated" == "no" ]]; then
        # Upgrade: new host is unsaturated, current is not.
        rec_host="$alias"; rec_shell="$shell"; rec_sat="$sat"
      elif [[ "$is_unsaturated" == "$rec_unsaturated" ]]; then
        # Same saturation tier — pick lower sat ratio (preference order already set
        # on first-come so only replace when strictly lower).
        lower="$(awk "BEGIN { print ($sat < $rec_sat) ? \"yes\" : \"no\" }" 2>/dev/null || echo "no")"
        if [[ "$lower" == "yes" ]]; then
          rec_host="$alias"; rec_shell="$shell"; rec_sat="$sat"
        fi
      fi
    fi
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
