#!/usr/bin/env python3
"""
check-todo-codes.py — TODO.md item-ID uniqueness checker for StrategyLab.

Catches a reused or duplicated item ID: two different items sharing the same
code (e.g. two "- [ ] **F431** ..." bullets). F### is a monotonic item counter
and IDs are NEVER reused or renumbered, so a gap is fine and expected while a
repeat is a bug. Interactive and overnight sessions can pick the same next F-ID
independently, which is exactly the collision this catches. Runs as a blocking
pre-commit hook.

What counts as an ID DEFINITION (the thing checked for uniqueness):
    a Markdown list item whose bolded lead token is the ID, e.g.
        - [ ] **F431** Gateway alert cooldown ...
        - [ ] <a id="f431"></a> **F431** Gateway alert cooldown ...
    i.e. optional indent, a "-" list marker, an optional "[ ]"/"[x]" checkbox,
    the optional <a id="..."></a> anchor that bin/sync-todo-index.py inserts,
    then "**" immediately followed by the ID. Only the START of a bulleted line
    counts — an ID mentioned mid-sentence (a cross-reference such as "deferred
    from **B6**") is prose, not a second definition, and is deliberately NOT
    matched: the line-start anchor is what keeps this checker from treating
    every backward reference as a duplicate. Index-table rows start with "|",
    so they are out of scope too.

ID shape (canonical — same regex as bin/sync-todo-index.py BULLET_RE, the
pre-commit hook and bin/archive-todo.py): one or more capitals, digits, then an
optional lowercase/digit/dash suffix — A8, B9, D24b, F249c, F249-alt.

A heading whose text contains "cross-reference" opens a glossary subsection:
its bullets point AT IDs defined elsewhere and are skipped, up to the next
heading of any level.

Exit codes: 0 = clean (no duplicate IDs), 1 = duplicate ID(s) found,
2 = usage/env error (not a git repo, TODO.md missing/unreadable).

stdlib-only. Python 3.9+.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Optional indent, "-" list marker, optional "[ ]"/"[x]" checkbox, the optional
# generated <a id="..."></a> anchor, "**", then the ID. Matched right after the
# opening "**" — deliberately NOT requiring an immediate closing "**", since a
# header can bold past the ID.
CODE_DEF_RE = re.compile(
    r'^\s*-\s*(?:\[[ xX]\]\s*)?(?:<a id="[^"]*"></a>\s*)?\*\*([A-Z]+\d+[a-z0-9\-]*)\b'
)

HEADING_RE = re.compile(r'^\s{0,3}#+\s')
CROSS_REF_HEADING_RE = re.compile(r'^\s{0,3}#+\s.*cross-reference', re.IGNORECASE)


def get_repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(out.stdout.strip()).resolve()


def find_definitions(text: str) -> List[Tuple[int, str]]:
    """Return [(lineno, code), ...] for every ID-defining bullet in TODO.md."""
    out: List[Tuple[int, str]] = []
    in_cross_refs = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if HEADING_RE.match(line):
            in_cross_refs = bool(CROSS_REF_HEADING_RE.match(line))
            continue
        if in_cross_refs:
            continue
        m = CODE_DEF_RE.match(line)
        if not m:
            continue
        out.append((lineno, m.group(1)))
    return out


def find_duplicates(defs: List[Tuple[int, str]]) -> Dict[str, List[int]]:
    """Return {code: [lineno, lineno, ...]} for every ID defined 2+ times."""
    by_code: Dict[str, List[int]] = {}
    for lineno, code in defs:
        by_code.setdefault(code, []).append(lineno)
    return {code: lines for code, lines in by_code.items() if len(lines) > 1}


def main(argv: List[str]) -> int:
    try:
        repo_root = get_repo_root()
    except subprocess.CalledProcessError:
        print("check-todo-codes: error — not inside a git repository", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("check-todo-codes: error — git not found on PATH", file=sys.stderr)
        return 2

    todo_path = repo_root / "TODO.md"
    try:
        text = todo_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"check-todo-codes: error — could not read {todo_path}: {exc}", file=sys.stderr)
        return 2

    defs = find_definitions(text)
    dupes = find_duplicates(defs)

    if "--list" in argv:
        for lineno, code in defs:
            print(f"TODO.md:{lineno}: {code}")

    if dupes:
        for code in sorted(dupes):
            lines = ", ".join(f"TODO.md:{n}" for n in dupes[code])
            print(f"duplicate code {code} defined at: {lines}")
        print(f"\n{len(dupes)} duplicate TODO code(s) found — codes are never reused.")
        return 1

    print(f"OK — {len(defs)} TODO codes checked, no duplicates.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
