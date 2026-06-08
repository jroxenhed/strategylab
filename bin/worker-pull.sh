#!/usr/bin/env bash
# Pull an artifact from the worker back to local disk (F374)
#
# Usage:
#   bin/worker-pull.sh <remote-relpath> <local-path>
#
#   remote-relpath — repo-relative path on the worker (e.g. .run/F369/results.parquet)
#   local-path     — local destination file or directory
#
# The transfer uses cat-over-ssh (binary-safe, no rsync required).
# Post-quantum / OpenSSH upgrade warnings are filtered from stderr.

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <remote-relpath> <local-path>" >&2
  exit 1
fi

REMOTE_REL="$1"
LOCAL_PATH="$2"
WORKER_HOST="strategylab-worker"

# Validate REMOTE_REL: only safe characters, no ".." path traversal.
if [[ ! "$REMOTE_REL" =~ ^[A-Za-z0-9_./-]+$ ]]; then
  echo "ERROR: REMOTE_REL contains invalid characters: $REMOTE_REL" >&2
  echo "  Allowed: A-Z a-z 0-9 _ . / -" >&2
  exit 1
fi
if [[ "$REMOTE_REL" == *..* ]]; then
  echo "ERROR: REMOTE_REL contains a '..' path segment (traversal rejected): $REMOTE_REL" >&2
  exit 1
fi

# Transfer via cat-over-ssh.
# Windows SSH quoting: outer double-quotes allow variable expansion; inner
# escaped double-quotes are passed to wsl bash -lc for path expansion.
# Post-quantum/OpenSSH upgrade warnings are filtered from stderr before display.
do_pull() {
  # Use mktemp for stderr capture to avoid symlink-race on world-writable /tmp
  local _stderr_tmp
  _stderr_tmp=$(mktemp /tmp/_worker_pull_stderr_XXXXXX)
  # shellcheck disable=SC2064
  trap "rm -f '$_stderr_tmp'" RETURN

  local inner="cat ~/strategylab/$REMOTE_REL"
  local rc=0
  ssh "$WORKER_HOST" "wsl bash -lc \"$inner\"" 2>"$_stderr_tmp" || rc=$?
  grep -vE "post-quantum|store now|openssh|upgraded|vulnerable" "$_stderr_tmp" >&2 || true
  return $rc
}

# If local-path is a directory, derive filename from remote
if [[ -d "$LOCAL_PATH" ]]; then
  LOCAL_PATH="$LOCAL_PATH/$(basename "$REMOTE_REL")"
fi

# Ensure parent dir exists
mkdir -p "$(dirname "$LOCAL_PATH")"

echo "→ Pulling $WORKER_HOST:~/strategylab/$REMOTE_REL → $LOCAL_PATH" >&2

# Write to a temp file; atomically rename on success to avoid partial-write.
TMP_PATH="${LOCAL_PATH}.tmp$$"
# Ensure temp is cleaned up if we exit early
trap "rm -f '$TMP_PATH'" EXIT

do_pull > "$TMP_PATH"

# Sanity check: refuse to install a zero-byte file
SIZE=$(wc -c < "$TMP_PATH" 2>/dev/null || echo "0")
if [[ "$SIZE" -eq 0 ]]; then
  echo "ERROR: Transfer produced an empty file — aborting (remote path may not exist)." >&2
  exit 1
fi

# Atomic rename: only replace destination once transfer is complete and non-empty
mv "$TMP_PATH" "$LOCAL_PATH"

# Clear the EXIT trap now that rename succeeded
trap - EXIT

echo "→ Done (${SIZE} bytes) → $LOCAL_PATH" >&2
