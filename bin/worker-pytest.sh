#!/usr/bin/env bash
# Worker-route a pytest suite to the reachable compute worker (F421).
#
# Usage:
#   bin/worker-pytest.sh <outdir> [pytest-args...]
#
# Examples:
#   bin/worker-pytest.sh .run/F421                        # full backend suite INCLUDING slow (worker default)
#   bin/worker-pytest.sh .run/F421 -m slow                # slow tests only, explicit override
#   bin/worker-pytest.sh .run/F421 backend/tests/test_wfa_pool.py -m slow
#
# How it works:
#   1. Sets WORKER_SYNC to include backend/tests AND backend/research (the latter covers
#      research/test_*.py which are excluded from the normal dispatch — see Sync note below).
#   2. Passes "--override-ini=addopts=" to pytest so the worker runs the FULL suite including
#      @pytest.mark.slow tests (pytest.ini addopts=-m "not slow" is the fast-local default).
#      If the caller passes their own -m flag, it takes precedence over the override.
#   3. Passes "-m pytest" as the python invocation to bin/worker-dispatch.sh, which hands it to
#      worker-run.sh as: backend/venv/bin/python3 -m pytest <args>
#   4. Strips leading "pytest" or "python3 -m pytest" from the user's args if present (normalizes
#      accidental duplication).
#
# DRY-RUN mode (env WORKER_DRYRUN=1):
#   Prints the would-be dispatch command without executing it.  Use this to verify argument
#   construction locally since we cannot reach the worker from the orchestrator machine.
#
# SSH quoting convention (critical — Windows cmd.exe → wsl → bash):
#   Outer single-quotes pass through cmd.exe; inner double-quotes do tilde/variable expansion.
#   This script delegates entirely to bin/worker-dispatch.sh which owns the quoting layer.
#
# Sync note:
#   worker-dispatch.sh normally excludes test_*.py (REL-01: bandwidth).  For pytest we MUST sync:
#     - backend/tests  — the main test directory
#     - backend/research  — includes research/test_*.py (test_premise_run.py,
#       test_premise_spec.py, test_premise_history.py) which are EXCLUDED from the normal
#       worker-dispatch.sh sync (find backend/research -name "*.py" -not -name "test_*.py").
#       Adding the whole research/ dir is safe: rsync handles duplicates for non-test .py files.
#   WORKER_SYNC only affects the pytest wrapper; it is NOT set globally.
#
# Live validation pending:
#   This script has been validated with bash -n (syntax only) and WORKER_DRYRUN=1 (arg construction).
#   A real worker dispatch was NOT possible at impl time (worker unreachable from orchestrator
#   machine).  First live run should pass --collect-only to confirm test discovery before running.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <outdir> [pytest-args...]" >&2
  exit 1
fi

OUTDIR="$1"; shift
# remaining args: pytest flags (optional)

# Normalize: strip leading "pytest" or "python3 -m pytest" / "python -m pytest"
PYTEST_ARGS=("$@")
if [[ ${#PYTEST_ARGS[@]} -gt 0 && "${PYTEST_ARGS[0]}" == "pytest" ]]; then
  PYTEST_ARGS=("${PYTEST_ARGS[@]:1}")
elif [[ ${#PYTEST_ARGS[@]} -gt 2 && "${PYTEST_ARGS[0]}" == "python3" && "${PYTEST_ARGS[1]}" == "-m" && "${PYTEST_ARGS[2]}" == "pytest" ]]; then
  PYTEST_ARGS=("${PYTEST_ARGS[@]:3}")
elif [[ ${#PYTEST_ARGS[@]} -gt 2 && "${PYTEST_ARGS[0]}" == "python" && "${PYTEST_ARGS[1]}" == "-m" && "${PYTEST_ARGS[2]}" == "pytest" ]]; then
  PYTEST_ARGS=("${PYTEST_ARGS[@]:3}")
fi

# Add backend/tests AND backend/research to the sync list.
# backend/research non-test .py files are already synced by worker-dispatch.sh, but
# research/test_*.py are explicitly excluded (REL-01 bandwidth filter).  Including the
# whole backend/research directory here is safe — rsync deduplicates the overlap.
export WORKER_SYNC="${WORKER_SYNC:+${WORKER_SYNC},}backend/tests,backend/research"

# KP-11/R-04: default to the FULL suite including @pytest.mark.slow on the worker.
# pytest.ini addopts=-m "not slow" is a fast-local default; we override it here so
# slow tests (ProcessPool / real I/O) are covered on every worker run.
# If the caller passes their own -m flag, it is appended after the override and wins
# (pytest last-wins for --override-ini and the caller's -m is more specific).
WORKER_PYTEST_ARGS=("--override-ini=addopts=" "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}")

# Generate a log name from the outdir basename + timestamp so multiple runs don't clobber.
LOGNAME="pytest-$(basename "$OUTDIR")-$(date -u +%Y%m%dT%H%M%S).log"

if [[ "${WORKER_DRYRUN:-}" == "1" ]]; then
  echo "DRY-RUN — would execute:"
  echo "  WORKER_SYNC=$WORKER_SYNC \\"
  echo "  bin/worker-dispatch.sh $OUTDIR $LOGNAME -m pytest ${WORKER_PYTEST_ARGS[*]+"${WORKER_PYTEST_ARGS[*]}"}"
  exit 0
fi

exec "$REPO_ROOT/bin/worker-dispatch.sh" "$OUTDIR" "$LOGNAME" -m pytest "${WORKER_PYTEST_ARGS[@]}"
