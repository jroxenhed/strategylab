#!/usr/bin/env python3
"""check-todo-plain.py — hold TODO.md to the shape John can read.

WHY
  TODO.md had become its own journal: on 2026-09-12 it carried 34 open items in
  3,065 words, and the longest single item ran to 236 words of probe dates, review
  finding IDs and riders that had been appended over months. John: "I can't have a
  todo list that just grows and becomes its own journal." The rewrite made every item
  one piece of work in three or four plain sentences. This guard keeps it that way,
  the same way check-now-focused.py keeps NOW.md readable. Appending is easy and
  invisible; removing takes a decision. The check fires at the moment someone is
  adding, which is the only moment it can help.

WHAT IT CHECKS
  - an item is at most MAX_ITEM_CHARS characters, measured on what the reader sees
    (the <a id="..."></a> anchor bin/sync-todo-index.py inserts does not count)
  - no ticked items: a closed item is a JOURNAL bullet, then archived out
  - no nested items: one piece of work per ID, riders become their own item or leave
  - no em dash joining words, no "DONE/CLOSED" narration inside an open item
  - at most MAX_SENTENCES sentences per item, so riders cannot pile up inside the
    character cap
  - the open-item count stays at or under the ceiling in bin/check-todo-plain.ceiling.
    Items under "## Deferred (gated)" count too: parked work is still work John reads
    past. Raising that number is a decision John makes in a diff, never a side effect
    of filing.
  - prose between a section heading and its items stays under MAX_SECTION_PROSE, so
    state cannot migrate out of the items into unchecked paragraphs. The file's intro
    pointer sits under the H1 with no "## " above it, so it is out of scope by
    construction.
  - the whole file stays under MAX_FILE_CHARS

USAGE
    python3 bin/check-todo-plain.py          # exit 0 clean, 1 on violations
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TODO = REPO / "TODO.md"

MAX_ITEM_CHARS = 420
MAX_SENTENCES = 5             # three or four sentences of work, one for a link
CEILING_FILE = REPO / "bin" / "check-todo-plain.ceiling"
SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
MAX_FILE_CHARS = 36000        # growth by any route trips this before it hurts
MAX_SECTION_PROSE = 300       # text between a heading and its first item: a pointer, not a place to park state
ITEM_RE = re.compile(r"^(\s*)- \[( |x|X)\] ")
DONE_WORDS = re.compile(r"\b(DONE|SHIPPED|CLOSED|COMPLETE|COMPLETED|LANDED)\b")
# bin/sync-todo-index.py inserts this anchor; it is markup, not reading load.
ANCHOR_RE = re.compile(r'<a id="[^"]*"></a>\s*')


def ceiling() -> int:
    try:
        return int(CEILING_FILE.read_text().strip())
    except (OSError, ValueError):
        return 40


def check(text: str, max_items: int = None):
    problems = []
    if max_items is None:
        max_items = ceiling()
    open_items = len(re.findall(r"^\s*- \[ \] ", text, re.M))
    if open_items > max_items:
        problems.append(f"{open_items} open items, ceiling is {max_items} (bin/check-todo-plain.ceiling). Close or park something before filing; raise the ceiling only as a deliberate edit")
    if len(text) > MAX_FILE_CHARS:
        problems.append(f"whole file is {len(text)} chars (max {MAX_FILE_CHARS}); prune, do not append")
    for m in re.finditer(r"^## [^\n]*\n((?:(?!^## |^\s*- \[)[^\n]*\n?)*)", text, re.M):
        prose = " ".join(l.strip() for l in m.group(1).split("\n") if l.strip())
        if len(prose) > MAX_SECTION_PROSE:
            head = m.group(0).split("\n")[0][3:40]
            problems.append(f"section '{head}…' carries {len(prose)} chars of prose outside its items (max {MAX_SECTION_PROSE}). A section intro is a pointer; state lives in items, JOURNAL or a doc")
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = ITEM_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, box = m.group(1), m.group(2)
        start = i + 1
        body = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].strip() and not ITEM_RE.match(lines[i]) and not lines[i].startswith("#"):
            body.append(lines[i])
            i += 1
        item = " ".join(l.strip() for l in body)
        # Measure what the reader sees, not the generated anchor markup.
        item = ANCHOR_RE.sub("", item)
        label = item[:50]
        if indent:
            problems.append(f"line {start}: nested item '{label}…'. One piece of work per ID; a rider becomes its own item or leaves")
        if box.lower() == "x":
            problems.append(f"line {start}: ticked item '{label}…'. Closed items are a JOURNAL bullet, then archived out")
        if len(item) > MAX_ITEM_CHARS:
            problems.append(f"line {start}: item '{label}…' is {len(item)} chars (max {MAX_ITEM_CHARS}). Say what it is, where it stands, what it waits on; detail goes to the doc it links or to JOURNAL")
        sentences = len(SENTENCE_END.findall(re.sub(r"\[[^\]]*\]\([^)]*\)", "", item)))
        if sentences > MAX_SENTENCES:
            problems.append(f"line {start}: item '{label}…' has {sentences} sentences (max {MAX_SENTENCES}). Riders do not pile up inside an item; the oldest detail leaves for the doc or JOURNAL, or this is two pieces of work")
        if re.search(r"\w\s?—\s?\w", item):
            problems.append(f"line {start}: em dash in '{label}…'. Use a full stop or a comma")
        if DONE_WORDS.search(item):
            problems.append(f"line {start}: '{label}…' narrates a closure. Finished work is a JOURNAL bullet, not a to-do line")
    return problems


def main() -> int:
    if not TODO.exists():
        print("check-todo-plain: TODO.md not found", file=sys.stderr)
        return 1
    problems = check(TODO.read_text())
    if not problems:
        print("OK — TODO.md is plain: no ticked, nested or over-long items.")
        return 0
    print(f"TODO.md has {len(problems)} shape problem(s):", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
