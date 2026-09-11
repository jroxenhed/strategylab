#!/usr/bin/env python3
"""check-now-focused.py — hold NOW.md to the rules NOW.md states about itself.

WHY THIS EXISTS

`NOW.md` is the short page carrying only the genuinely time-critical work, so the rest
of the backlog can be ignored without guilt. It exists because TODO.md stopped being
readable: by 2026-09-12 it carried **34 open items in 3,065 words, with a single item
running to 236 words** of probe dates, review-finding IDs and riders appended over
months. A list that long is not a list, it is a second journal, and John stops opening
it.

The mechanism behind that growth is not mysterious: **appending is easy and invisible,
removing requires a decision.** Every session added status because that felt like
keeping John informed; nobody ever subtracted. Splitting out a short NOW.md fixes
today's state, and nothing stops the same drift from eating NOW.md next. This fixes the
mechanism: the rule fails a commit, at the moment someone is adding to the file, which
is the only moment it can help. Its sibling bin/check-todo-plain.py does the same job
for TODO.md.

WHAT IT ENFORCES (all of these are NOW.md's OWN stated rules)

  * <= 4 open items per section        — the file's explicit rule of thumb.
  * bullets stay bitesize              — a focus line needs a pointer, not a paragraph.
    Detail belongs in TODO.md; that is what the file says it is a view OF.
  * no completion vocabulary           — DONE / SHIPPED / CLOSED / COMPLETE / LANDED
    in an item means the item is finished, and finished work belongs in JOURNAL.md.
    This is the exact drift that ate TODO.md, so it is the loudest check.
  * whole file stays short             — the point is that it can be read in a minute.

NOT a general doc linter. It applies to NOW.md alone, because NOW.md alone makes these
promises about itself.

    python3 bin/check-now-focused.py          # exit 0 clean, 1 on violations

Deliberately has no baseline file. A baseline would let the next violation be accepted
instead of fixed, which is precisely how a 3,065-word TODO.md happens.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOW = REPO / "NOW.md"

MAX_ITEMS_PER_SECTION = 4
MAX_ITEM_CHARS = 420          # ~4 lines. Enough for a pointer and a why, not a history.
MAX_FILE_CHARS = 8000         # readable in a minute.
DONE_WORDS = re.compile(r"\b(DONE|SHIPPED|CLOSED|COMPLETE|COMPLETED|LANDED)\b")

# Sections that legitimately list things which are NOT open work items, so the
# item cap does not apply. Kept tiny on purpose — every exemption is a way back in.
UNCAPPED = ("waiting on someone else", "waiting on john", "everything else", "the clock")


def sections(text: str):
    """[(heading, [item_lines])] for '## ' headings and their '- [ ]' items."""
    out, cur = [], None
    for line in text.split("\n"):
        if line.startswith("## "):
            cur = (line[3:].strip(), [])
            out.append(cur)
        elif cur is not None and re.match(r"^\s*- \[ \]", line):
            cur[1].append(line.strip())
    return out


def check(text: str):
    problems = []

    if len(text) > MAX_FILE_CHARS:
        problems.append(
            f"whole file is {len(text)} chars (max {MAX_FILE_CHARS}). It is meant to be "
            f"readable in a minute — move detail into TODO.md and link to it.")

    for heading, items in sections(text):
        capped = not any(k in heading.lower() for k in UNCAPPED)
        if capped and len(items) > MAX_ITEMS_PER_SECTION:
            problems.append(
                f"section '{heading}' has {len(items)} open items (max "
                f"{MAX_ITEMS_PER_SECTION}). NOW.md's own rule: something needs to move "
                f"DOWN a tier, not up.")
        for item in items:
            label = re.sub(r"^\s*- \[ \]\s*\**", "", item)[:48]
            if len(item) > MAX_ITEM_CHARS:
                problems.append(
                    f"item '{label}…' is {len(item)} chars (max {MAX_ITEM_CHARS}). A "
                    f"focus line points at detail; it does not carry it. Put the body in "
                    f"TODO.md and leave a link.")
            if DONE_WORDS.search(item):
                found = ", ".join(sorted(set(DONE_WORDS.findall(item))))
                problems.append(
                    f"item '{label}…' records completed work ({found}). Finished work "
                    f"belongs in JOURNAL.md — this is the drift that turned TODO.md into "
                    f"a second journal once already.")
    return problems


def main() -> int:
    if not NOW.exists():
        print("NOW.md not found", file=sys.stderr)
        return 1
    problems = check(NOW.read_text())

    if problems:
        print(f"{len(problems)} NOW.md focus violation(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}\n", file=sys.stderr)
        print("NOW.md exists so the rest of TODO.md can be ignored without guilt.\n"
              "Every line added here spends that. There is deliberately no baseline —\n"
              "fix it rather than accept it.", file=sys.stderr)
        return 1

    print("OK — NOW.md is still a focus list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
