#!/bin/bash
# Activate versioned git hooks for strategylab.
# Run once after cloning the repo (idempotent — safe to re-run).
#
# Sets git's hooksPath to the in-repo .githooks/ directory rather than
# copying into .git/hooks/ — so future hook updates land via `git pull`
# without needing to re-run this script.

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
hooks_src="$repo_root/.githooks"

if [ ! -d "$hooks_src" ]; then
    echo "Error: $hooks_src missing"
    exit 1
fi

# Ensure all hooks are executable (versioned, but new clones may need this)
chmod +x "$hooks_src"/* 2>/dev/null || true

git -C "$repo_root" config --local core.hooksPath .githooks

echo "Done. core.hooksPath set to .githooks; versioned hooks active in this clone."
