#!/usr/bin/env bash
# Worker-default compute dispatcher (F374)
#
# Usage:
#   bin/worker-dispatch.sh <outdir> <logname> <python-script-and-args...>
#
# Optional env:
#   WORKER_REQUIRE="path1,path2"  — repo-relative artifacts that MUST exist on
#                                   the worker before launching (pre-flight manifest).
#                                   Missing artifacts → print error + exit 1.
#                                   If the worker is unreachable, WORKER_REQUIRE causes
#                                   dispatch to ABORT (no silent local fallback) unless
#                                   WORKER_REQUIRE_LOCAL_OK=1 is also set.
#   WORKER_SYNC="path1,path2"     — extra repo-relative paths to sync in addition
#                                   to the defaults (backend/research/*.py +
#                                   bin/worker-run.sh).
#
# Output on success:
#   DISPATCHED target=worker|local-fallback pid=<pid> log=<outdir>/<logname>
#   + a one-line tail-poll hint
#
# SSH quoting convention (Windows cmd.exe → wsl → bash):
#   CORRECT:   ssh worker 'wsl bash -lc "inner cmd"'   ← outer single, inner double
#   WRONG:     ssh worker "wsl bash -lc 'inner cmd'"   ← reversed; cmd.exe strips wrong layer

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── args ──────────────────────────────────────────────────────────────────────
if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <outdir> <logname> <python-script-and-args...>" >&2
  exit 1
fi

OUTDIR="$1"; shift
LOGNAME="$1"; shift
# remaining: python script + its flags
# Normalize: strip leading "python3"/"python" — worker-run.sh always uses backend/venv/bin/python3
PYTHON_ARGS=("$@")
if [[ ${#PYTHON_ARGS[@]} -gt 0 && ( "${PYTHON_ARGS[0]}" == "python3" || "${PYTHON_ARGS[0]}" == "python" ) ]]; then
  PYTHON_ARGS=("${PYTHON_ARGS[@]:1}")
fi

# ── path validation helper ────────────────────────────────────────────────────
# Validates a repo-relative path: only safe characters, no ".." segments.
# Usage: validate_path "context-label" "the/path/value"
validate_path() {
  local label="$1"
  local val="$2"
  if [[ ! "$val" =~ ^[A-Za-z0-9_./-]+$ ]]; then
    echo "ERROR: $label contains invalid characters: $val" >&2
    echo "  Allowed: A-Z a-z 0-9 _ . / -" >&2
    exit 1
  fi
  # Reject any ".." segment (path traversal guard)
  if [[ "$val" == *..* ]]; then
    echo "ERROR: $label contains a '..' path segment (traversal rejected): $val" >&2
    exit 1
  fi
}

# ── SSH filter helper ─────────────────────────────────────────────────────────
# Strips post-quantum / OpenSSH upgrade warnings so parseable output stays clean
ssh_clean() {
  grep -vE "post-quantum|store now|openssh|upgraded|vulnerable" || true
}

# ── worker host + remote-shell wrapper ────────────────────────────────────────
# WORKER_HOST     — ssh alias of the compute box (default: home 14900k).
# WORKER_SHELL    — "wsl" (Windows host: cmd.exe → wsl → bash, the home worker)
#                   or "native" (Linux/macOS host: ssh runs the inner command
#                   directly under the remote login shell, e.g. office mfcore01).
# Examples:
#   WORKER_HOST=mfcore01 WORKER_SHELL=native bin/worker-dispatch.sh …
WORKER_HOST="${WORKER_HOST:-strategylab-worker}"
WORKER_SHELL="${WORKER_SHELL:-wsl}"

# Wrap an inner bash command for the remote host, returning the string to pass
# as the SINGLE ssh command argument. The wsl form keeps the hand-tuned
# cmd.exe → wsl → bash quoting exactly as before; native hands the inner command
# straight to the remote login shell. Inner commands must not contain literal
# double-quotes (none do today).
rwrap() {
  local inner="$1"
  if [[ "$WORKER_SHELL" == "native" ]]; then
    printf '%s' "$inner"
  else
    printf 'wsl bash -lc "%s"' "$inner"
  fi
}

# ── worker probe ─────────────────────────────────────────────────────────────
worker_reachable() {
  ssh -o ConnectTimeout=8 -o BatchMode=yes "$WORKER_HOST" \
    "$(rwrap 'echo ok')" 2>&1 | ssh_clean | grep -q '^ok$'
}

# ── pre-flight manifest check (shared: worker + local-fallback) ───────────────
# Checks WORKER_REQUIRE artifacts exist on the worker.
# On error: exits 1.
check_worker_require() {
  if [[ -z "${WORKER_REQUIRE:-}" ]]; then
    return 0
  fi
  IFS=',' read -ra required <<< "$WORKER_REQUIRE"
  local missing=()
  for rp in "${required[@]}"; do
    validate_path "WORKER_REQUIRE entry" "$rp"
    result=$(ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 "$WORKER_HOST" \
      "$(rwrap "test -e ~/strategylab/$rp && echo present || echo absent")" \
      2>&1 | ssh_clean)
    if [[ "$result" != "present" ]]; then
      missing+=("$rp")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "" >&2
    echo "ERROR: Pre-flight manifest failed — required artifacts missing on worker:" >&2
    for m in "${missing[@]}"; do
      echo "  MISSING: $m" >&2
    done
    echo "" >&2
    echo "Ensure these artifacts exist on the worker before dispatching." >&2
    echo "Use bin/worker-pull.sh to copy artifacts worker→local and check existence." >&2
    exit 1
  fi
  echo "→ Pre-flight manifest OK (${#required[@]} artifact(s) verified)" >&2
}

# ── WORKER branch ─────────────────────────────────────────────────────────────
dispatch_worker() {
  # 1. Build sync list: defaults + WORKER_SYNC extras
  local sync_paths=()

  # Always sync bin/worker-run.sh and bin/worker-dispatch.sh
  sync_paths+=("bin/worker-run.sh" "bin/worker-dispatch.sh")

  # Always sync backend/research/**/*.py — research grew subpackages (e.g.
  # backend/research/streams/, F388); a -maxdepth 1 glob left them behind and
  # the worker died on ModuleNotFoundError: research.streams (F409 dispatch).
  while IFS= read -r f; do
    sync_paths+=("$f")
  done < <(cd "$REPO_ROOT" && find backend/research -name '*.py' -not -path '*/__pycache__/*')

  # Always sync backend-root modules that research code imports transitively.
  # Without this, adding a symbol to (e.g.) fileutil.py leaves the worker on
  # a stale copy until the caller remembers to pass WORKER_SYNC — the F348
  # probe crashed exactly this way (missing file_lock in fileutil.py).
  #
  # MAINT-02 — ALLOWLIST DRIFT COST:
  # Adding a new backend-root module that research code imports transitively
  # will cause a silent ModuleNotFoundError on the worker until that file is
  # added here.  To audit: run
  #   grep -rh "^import\|^from" backend/research/*.py | grep -v "research\." \
  #     | awk '{print $2}' | cut -d. -f1 | sort -u
  # and cross-check against this list.  Review whenever a new top-level module
  # is added to backend/ that research code imports.  WORKER_SYNC is the
  # escape hatch for one-off extras (no allowlist edit needed for short-lived
  # scripts).
  #
  # Allowlist is the known set; WORKER_SYNC remains the escape hatch for extras.
  local backend_root_deps=(
    "backend/edgar.py"
    "backend/fileutil.py"
    "backend/shared.py"
    "backend/turnaround.py"
    "backend/turnaround_validation.py"
  )
  for dep in "${backend_root_deps[@]}"; do
    if [[ -f "$REPO_ROOT/$dep" ]]; then
      sync_paths+=("$dep")
    fi
  done

  # Extra paths from WORKER_SYNC env — validate each entry
  if [[ -n "${WORKER_SYNC:-}" ]]; then
    IFS=',' read -ra extra <<< "$WORKER_SYNC"
    for p in "${extra[@]}"; do
      validate_path "WORKER_SYNC entry" "$p"
      sync_paths+=("$p")
    done
  fi

  # 2. Sync via tar-over-ssh-pipe
  # Inner command uses double-quotes (outer single-quotes pass through Windows cmd.exe)
  # Errors from tar are NOT suppressed — a missing source file aborts dispatch.
  # A failed sync MUST abort: never launch on stale code.
  echo "→ Syncing ${#sync_paths[@]} files to $WORKER_HOST …" >&2
  if ! (cd "$REPO_ROOT" && tar -czf - "${sync_paths[@]}") | \
      ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        "$WORKER_HOST" \
        "$(rwrap 'cd ~/strategylab && tar -xzf -')" 2>&1 | ssh_clean; then
    echo "" >&2
    echo "ERROR: Code sync to $WORKER_HOST FAILED — aborting dispatch." >&2
    echo "  Never launching on stale code. Check network / worker disk / tar errors above." >&2
    exit 1
  fi
  echo "→ Code sync OK" >&2

  # 3. Pre-flight manifest — check WORKER_REQUIRE artifacts exist on worker
  check_worker_require

  # 4. Write a launcher script that encodes all args safely, sync it, then execute.
  # This avoids the cmd.exe → wsl → bash triple-quoting trap: the remote command
  # is a single, simple file path — no args need escaping through two shell layers.
  local launch_name=".worker-launch-$$.sh"
  local launch_script="$REPO_ROOT/.run/$launch_name"
  mkdir -p "$REPO_ROOT/.run"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'set -euo pipefail\n'
    # printf '%q ' produces bash-safe quoting inside the script file itself
    printf 'exec bash ~/strategylab/bin/worker-run.sh '
    printf '%q ' "$OUTDIR" "$LOGNAME" "${PYTHON_ARGS[@]}"
    printf '\n'
  } > "$launch_script"

  # Sync the launcher script (single-file tar) — also abort on failure
  if ! (cd "$REPO_ROOT" && tar -czf - ".run/$launch_name") | \
      ssh -o ConnectTimeout=30 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        "$WORKER_HOST" \
        "$(rwrap 'cd ~/strategylab && tar -xzf -')" 2>&1 | ssh_clean; then
    rm -f "$launch_script"
    echo "" >&2
    echo "ERROR: Launcher sync to $WORKER_HOST FAILED — aborting dispatch." >&2
    exit 1
  fi

  # Cleanup local temp launcher
  rm -f "$launch_script"

  # Execute on the worker — just a file path, no embedded args to escape
  # Use timeouts so a hung WSL session can't block the orchestrator indefinitely.
  local launch_out
  launch_out=$(ssh \
    -o ConnectTimeout=30 \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    "$WORKER_HOST" \
    "$(rwrap "bash ~/strategylab/.run/$launch_name")" \
    2>&1 | ssh_clean)

  # Cleanup remote launcher script after execution
  ssh -o ConnectTimeout=15 "$WORKER_HOST" \
    "$(rwrap "rm -f ~/strategylab/.run/$launch_name")" 2>&1 | ssh_clean || true

  # Extract PID — only print DISPATCHED when we received a real WORKER_RUN_LAUNCHED token
  local PID
  PID=$(printf '%s\n' "$launch_out" | grep 'WORKER_RUN_LAUNCHED' \
    | grep -oE 'pid=[0-9]+' | cut -d= -f2 || true)

  if [[ -z "$PID" ]]; then
    echo "" >&2
    echo "ERROR: worker-run.sh did not emit WORKER_RUN_LAUNCHED — job may not have started." >&2
    echo "  Worker output:" >&2
    printf '%s\n' "$launch_out" | sed 's/^/    /' >&2
    exit 1
  fi

  echo ""
  echo "DISPATCHED target=worker pid=$PID log=$OUTDIR/$LOGNAME done=$OUTDIR/.${LOGNAME}.done"
  echo "Status: WORKER_HOST=$WORKER_HOST WORKER_SHELL=$WORKER_SHELL bin/worker-status.sh $OUTDIR $LOGNAME [--wait]"
  local poll_inner
  # G6: quote path vars in the informational poll-hint string
  poll_inner="$(rwrap "tail -f ~/strategylab/\"${OUTDIR}\"/\"${LOGNAME}\"")"
  echo "Poll: ssh $WORKER_HOST '$poll_inner'"
}

# ── LOCAL FALLBACK branch ─────────────────────────────────────────────────────
dispatch_local() {
  echo "" >&2
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" >&2
  echo "!!! WORKER UNAVAILABLE — falling back to LOCAL compute          !!!" >&2
  echo "!!!   Host: $WORKER_HOST unreachable (ConnectTimeout=8s)        !!!" >&2
  echo "!!!   Running on THIS machine — may be significantly slower.    !!!" >&2
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" >&2
  echo "" >&2

  # If WORKER_REQUIRE is set, artifacts are presumed worker-only — refuse local fallback
  # unless the caller explicitly opts in with WORKER_REQUIRE_LOCAL_OK=1.
  if [[ -n "${WORKER_REQUIRE:-}" ]]; then
    if [[ "${WORKER_REQUIRE_LOCAL_OK:-}" != "1" ]]; then
      echo "ERROR: worker unreachable but WORKER_REQUIRE is set — refusing local fallback." >&2
      echo "  The job depends on worker-side artifacts: $WORKER_REQUIRE" >&2
      echo "  Fix the worker connection or set WORKER_REQUIRE_LOCAL_OK=1 to override." >&2
      exit 1
    fi
    echo "WARNING: WORKER_REQUIRE_LOCAL_OK=1 set — checking worker artifacts locally…" >&2
    # Can't SSH to worker, check locally instead
    IFS=',' read -ra required <<< "$WORKER_REQUIRE"
    local missing=()
    for rp in "${required[@]}"; do
      validate_path "WORKER_REQUIRE entry" "$rp"
      if [[ ! -e "$REPO_ROOT/$rp" ]]; then
        missing+=("$rp")
      fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
      echo "" >&2
      echo "ERROR: Pre-flight manifest failed — required artifacts missing locally:" >&2
      for m in "${missing[@]}"; do
        echo "  MISSING: $m" >&2
      done
      exit 1
    fi
    echo "→ Pre-flight manifest OK locally (${#required[@]} artifact(s) verified)" >&2
  fi

  local venv_py="$REPO_ROOT/backend/venv/bin/python3"

  # Verify local deps
  if [[ ! -x "$venv_py" ]]; then
    echo "ERROR: local venv not found at $venv_py" >&2
    exit 1
  fi
  "$venv_py" -c "import pandas, numpy, pyarrow; print('local_deps_ok')" >/dev/null 2>&1 || {
    echo "ERROR: local venv missing required deps (pandas/numpy/pyarrow)" >&2
    exit 1
  }

  mkdir -p "$OUTDIR"
  local log="$OUTDIR/$LOGNAME"

  nohup "$venv_py" "${PYTHON_ARGS[@]}" >>"$log" 2>&1 &
  local PID=$!

  echo "host=$(hostname) cores=$(sysctl -n hw.logicalcpu 2>/dev/null || nproc 2>/dev/null || echo ?) started=$(date -u +%FT%TZ)" >>"$log"

  echo ""
  echo "DISPATCHED target=local-fallback pid=$PID log=$OUTDIR/$LOGNAME"
  echo "Poll: tail -f $OUTDIR/$LOGNAME"
}

# ── Main dispatch ─────────────────────────────────────────────────────────────
if worker_reachable; then
  dispatch_worker
else
  dispatch_local
fi
