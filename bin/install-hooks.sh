#!/bin/bash
# Install local git hooks for strategylab.
# Run once after cloning the repo.

set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
hooks_src="$repo_root/.githooks"
hooks_dst="$repo_root/.git/hooks"

if [ ! -d "$hooks_src" ]; then
    echo "Error: $hooks_src missing"
    exit 1
fi

for hook in "$hooks_src"/*; do
    name=$(basename "$hook")
    cp "$hook" "$hooks_dst/$name"
    chmod +x "$hooks_dst/$name"
    echo "Installed $name"
done

echo "Done. Hooks active in this clone."
