#!/usr/bin/env python3
"""archive-todo.py — Move checked-off TODO items to TODO-archive.md.

Mechanics
---------
1. Scans TODO.md (or --todo-path) for `- [x]` bullet lines (plus their
   `<a id="..."></a>` anchors).
2. Appends moved items to TODO-archive.md (or --archive-path), grouped into
   `## Closed YYYY-MM` month sections derived from `(resolved YYYY-MM-DD …)`
   in the item text.  If no resolved note is present, the current month is used.
3. Rewrites `TODO.md#<id>` fragment links in JOURNAL.md (or --journal-path)
   to `TODO-archive.md#<id>` — ONLY for the exact IDs moved in this run.
   No other JOURNAL content is touched.
4. Removes the moved lines (and their continuation sub-bullets) from TODO.md.
5. Calls bin/sync-todo-index.py at the end (unless --no-sync).

Safety contract
---------------
- Archive is written and all moved IDs are verified present on disk BEFORE
  TODO.md is rewritten.  If verification fails the script aborts with exit 1
  and TODO.md is left untouched.
- Idempotent: running a second time when all [x] items are already in the
  archive moves 0 items (because the remaining [x] items in TODO.md would be
  zero after the first run).  The summary line reflects this.
- TODO-archive.md is APPEND-ONLY: previously archived content is never
  reordered or rewritten.

Deferred-section design choice
-------------------------------
Items placed in `## Deferred (gated)` manually are left in TODO.md as-is —
the archive script never moves open items ([  ]) regardless of section, so
gated items (which should stay unchecked) are naturally excluded.

Usage
-----
  bin/archive-todo.py [--dry-run] [--no-sync]
                      [--todo-path PATH] [--journal-path PATH]
                      [--archive-path PATH]

Paths default to repo-root relative files (TODO.md, JOURNAL.md, TODO-archive.md).
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical ID pattern — MUST match sync-todo-index.py and pre-commit hook.
# Accepts: A8, B9, F249c, F249-alt, F2, C25b, etc.
# ---------------------------------------------------------------------------
ITEM_ID_RE_STR = r'[A-Z]+\d+[a-z0-9\-]*'

BULLET_RE = re.compile(
    r'^(- \[[ x]\] )(<a id="[^"]+"></a> )?\*\*('
    + ITEM_ID_RE_STR
    + r'(?:\s*\+\s*' + ITEM_ID_RE_STR + r')*)\*\*'
)

# Pattern to find (resolved YYYY-MM-DD ...) anywhere on a line
RESOLVED_RE = re.compile(r'\(resolved (\d{4}-\d{2}-\d{2})')

# JOURNAL link pattern: [TODO.md#<slug>] or (TODO.md#<slug>)
# Covers markdown links of the form: (TODO.md#id) or [text](TODO.md#id)
JOURNAL_LINK_RE = re.compile(r'\(TODO\.md#([a-z0-9\-]+)\)')

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TODO = REPO_ROOT / 'TODO.md'
DEFAULT_JOURNAL = REPO_ROOT / 'JOURNAL.md'
DEFAULT_ARCHIVE = REPO_ROOT / 'TODO-archive.md'
SYNC_SCRIPT = REPO_ROOT / 'bin' / 'sync-todo-index.py'

# COR-02: standalone anchor line (e.g. '<a id="f30"></a>' on its own line)
_STANDALONE_ANCHOR_RE = re.compile(r'^\s*<a id="([^"]+)"></a>\s*$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _anchor_slug(raw_id: str) -> str:
    """Lowercase first token (matches sync-todo-index.py _anchor_slug)."""
    first = re.split(r'\s*\+\s*', raw_id)[0].strip()
    return first.lower()


def _resolved_month(line: str, fallback: str) -> str:
    """Extract YYYY-MM from (resolved YYYY-MM-DD ...) or return fallback."""
    m = RESOLVED_RE.search(line)
    if m:
        return m.group(1)[:7]  # "YYYY-MM"
    return fallback


def _write_atomic(path: Path, content: str) -> None:
    """Write content to path atomically (temp file + os.replace)."""
    dir_ = str(path.parent)
    fd = tempfile.NamedTemporaryFile(
        mode='w', delete=False, dir=dir_, suffix='.tmp', encoding='utf-8'
    )
    try:
        fd.write(content)
        fd.flush()
        os.fsync(fd.fileno())
    except Exception:
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise
    finally:
        try:
            fd.close()
        except OSError:
            pass
    os.replace(fd.name, str(path))


# ---------------------------------------------------------------------------
# Step 1 — collect checked items from TODO.md
# ---------------------------------------------------------------------------

def collect_checked_items(lines: list[str]) -> list[dict]:
    """Return list of dicts describing each `- [x]` item and its continuation lines.

    dict keys:
      line_idx         — 0-based index of the bullet line in `lines`
      standalone_anchor_idx — index of a preceding standalone anchor line, or None (COR-02)
      item_id          — e.g. "F249c"
      slug             — lowercase anchor slug
      resolved_month   — "YYYY-MM" or today's month
      continuations    — list of (idx, line) for indented non-bullet lines below (COR-03:
                         includes blank lines between indented blocks)
    """
    today_month = date.today().strftime('%Y-%m')
    items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = BULLET_RE.match(line)
        if m and 'x' in m.group(1):
            item_id = m.group(3)
            slug = _anchor_slug(item_id)
            month = _resolved_month(line, today_month)

            # COR-02: check if the line immediately before is a standalone anchor
            standalone_anchor_idx = None
            if i > 0 and _STANDALONE_ANCHOR_RE.match(lines[i - 1]):
                standalone_anchor_idx = i - 1

            # COR-03: capture continuation lines including blank lines between
            # indented blocks.  Stop only when a non-blank, non-indented line
            # or a new bullet is encountered.
            continuations = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                # Indented non-bullet continuation: always capture
                if next_line.startswith('  ') and not BULLET_RE.match(next_line):
                    continuations.append((j, next_line))
                    j += 1
                elif next_line.strip() == '':
                    # Blank line — include only if a subsequent indented line follows
                    # (peek ahead to determine whether we're mid-block or at the end)
                    k = j + 1
                    while k < len(lines) and lines[k].strip() == '':
                        k += 1
                    if k < len(lines) and lines[k].startswith('  ') and not BULLET_RE.match(lines[k]):
                        # There's more indented content after the blank — include blank
                        continuations.append((j, next_line))
                        j += 1
                    else:
                        # Blank line ends the body
                        break
                else:
                    break

            items.append({
                'line_idx': i,
                'standalone_anchor_idx': standalone_anchor_idx,
                'item_id': item_id,
                'slug': slug,
                'resolved_month': month,
                'continuations': continuations,
            })
            i = j
        else:
            i += 1
    return items


# ---------------------------------------------------------------------------
# Step 2 — filter items not already in archive
# ---------------------------------------------------------------------------

def already_archived_slugs(archive_path: Path) -> set[str]:
    """Return set of slugs already present in TODO-archive.md."""
    if not archive_path.exists():
        return set()
    text = archive_path.read_text(encoding='utf-8')
    # Look for <a id="slug"></a> patterns (anchors on bullet lines)
    return set(re.findall(r'<a id="([^"]+)"></a>', text))


# ---------------------------------------------------------------------------
# Step 3 — build archive content to append
# ---------------------------------------------------------------------------

ARCHIVE_HEADER = """\
# TODO-archive.md — Closed items from TODO.md

Items moved here once checked off. Anchors are preserved so existing JOURNAL.md
links (`TODO-archive.md#id`) continue to resolve. Content is append-only and
grouped by close month. Never reorder or rewrite previously archived sections.

"""


def build_archive_addition(
    lines: list[str],
    items_to_move: list[dict],
) -> str:
    """Build the text block to append to the archive file.

    Groups items by `resolved_month`, emits `## Closed YYYY-MM` sections
    in chronological order.  Within each month, items appear in the order
    they were encountered in TODO.md.

    NOTE: This function builds self-contained month sections (with headers).
    The caller (main) is responsible for COR-05: merging into an existing
    same-month section via merge_into_archive() instead of naive append.
    """
    from collections import OrderedDict
    # Group by month, preserving order of first appearance
    by_month: dict[str, list[dict]] = OrderedDict()
    for item in items_to_move:
        by_month.setdefault(item['resolved_month'], []).append(item)

    parts = []
    for month in sorted(by_month.keys()):
        parts.append(f'## Closed {month}\n\n')
        for item in by_month[month]:
            # COR-02: prepend standalone anchor line if present
            if item.get('standalone_anchor_idx') is not None:
                parts.append(lines[item['standalone_anchor_idx']])
            parts.append(lines[item['line_idx']])
            for _, cont_line in item['continuations']:
                parts.append(cont_line)
        parts.append('\n')

    return ''.join(parts)


def merge_into_archive(existing: str, addition: str) -> str:
    """Merge new archive content into existing archive, avoiding duplicate month headers.

    COR-05: For each '## Closed YYYY-MM' section in `addition`, if that section
    already exists in `existing`, the new items are inserted at the end of the
    existing section (before the next '## Closed' or EOF) rather than creating
    a duplicate header.  Sections not yet present in `existing` are appended
    verbatim.
    """
    # Parse addition into per-month blocks: list of (month, block_text)
    # Each block includes the '## Closed YYYY-MM' header line.
    MONTH_HEADER_RE = re.compile(r'^## Closed (\d{4}-\d{2})\n', re.MULTILINE)

    addition_blocks: list[tuple[str, str]] = []
    add_matches = list(MONTH_HEADER_RE.finditer(addition))
    for idx, match in enumerate(add_matches):
        month = match.group(1)
        start = match.start()
        end = add_matches[idx + 1].start() if idx + 1 < len(add_matches) else len(addition)
        block_text = addition[start:end]
        addition_blocks.append((month, block_text))

    result = existing

    for month, block_text in addition_blocks:
        # Extract just the items portion (skip the '## Closed YYYY-MM\n\n' header)
        items_only = MONTH_HEADER_RE.sub('', block_text, count=1).lstrip('\n')

        # Find existing section in result
        existing_header = f'## Closed {month}\n'
        header_pos = result.find(existing_header)

        if header_pos == -1:
            # Section doesn't exist yet — append the full block
            if not result.endswith('\n'):
                result += '\n'
            result += block_text
        else:
            # Section exists — find the end of it (next '## Closed' or EOF)
            search_from = header_pos + len(existing_header)
            next_section = MONTH_HEADER_RE.search(result, search_from)
            if next_section:
                insert_pos = next_section.start()
                # Insert before the next section, after stripping trailing \n from items
                result = result[:insert_pos] + items_only + result[insert_pos:]
            else:
                # This is the last section — append items at end
                # Ensure we don't double up trailing newlines
                if not result.endswith('\n'):
                    result += '\n'
                result += items_only

    return result


# ---------------------------------------------------------------------------
# Step 4 — verify IDs present in archive on disk
# ---------------------------------------------------------------------------

def verify_ids_in_archive(archive_path: Path, slugs: set[str]) -> list[str]:
    """Return list of slugs NOT found in archive_path after writing."""
    text = archive_path.read_text(encoding='utf-8')
    present = set(re.findall(r'<a id="([^"]+)"></a>', text))
    return [s for s in slugs if s not in present]


# ---------------------------------------------------------------------------
# Step 5 — rewrite JOURNAL.md links
# ---------------------------------------------------------------------------

def rewrite_journal_links(
    journal_path: Path,
    slug_set: set[str],
    dry_run: bool,
) -> int:
    """Rewrite TODO.md#<slug> → TODO-archive.md#<slug> for slugs in slug_set.

    COR-04: Skips substitutions inside fenced code blocks (``` … ```) so that
    code examples referencing TODO.md#slug are never mutated.

    Returns count of links rewritten.
    """
    if not journal_path.exists():
        return 0
    text = journal_path.read_text(encoding='utf-8')

    count = 0

    # COR-04: split on triple-backtick fences.
    # After splitting, even-indexed chunks are prose; odd-indexed are code fences.
    # We only apply the substitution to prose chunks (even indices).
    fence_parts = text.split('```')

    result_parts = []
    for idx, chunk in enumerate(fence_parts):
        if idx % 2 == 0:
            # Prose — apply substitution
            def _replacer(m: re.Match) -> str:
                nonlocal count
                slug = m.group(1)
                if slug in slug_set:
                    count += 1
                    return f'(TODO-archive.md#{slug})'
                return m.group(0)
            result_parts.append(JOURNAL_LINK_RE.sub(_replacer, chunk))
        else:
            # Inside a code fence — leave unchanged
            result_parts.append(chunk)

    new_text = '```'.join(result_parts)

    if count > 0:
        if dry_run:
            print(f'  [dry-run] would rewrite {count} JOURNAL.md link(s)')
        else:
            _write_atomic(journal_path, new_text)
    return count


# ---------------------------------------------------------------------------
# Step 6 — rewrite TODO.md (remove moved items)
# ---------------------------------------------------------------------------

def remove_items_from_todo(lines: list[str], items_to_move: list[dict]) -> list[str]:
    """Return new lines list with moved items and their continuations removed.

    COR-02: also removes standalone anchor lines that precede the bullet.
    """
    remove_indices: set[int] = set()
    for item in items_to_move:
        # COR-02: remove standalone anchor line if present
        if item.get('standalone_anchor_idx') is not None:
            remove_indices.add(item['standalone_anchor_idx'])
        remove_indices.add(item['line_idx'])
        for idx, _ in item['continuations']:
            remove_indices.add(idx)

    return [line for i, line in enumerate(lines) if i not in remove_indices]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Print what would be moved without writing any files',
    )
    parser.add_argument(
        '--no-sync', action='store_true',
        help='Skip running sync-todo-index.py at the end',
    )
    parser.add_argument(
        '--todo-path', default=str(DEFAULT_TODO),
        help=f'Path to TODO.md (default: {DEFAULT_TODO})',
    )
    parser.add_argument(
        '--journal-path', default=str(DEFAULT_JOURNAL),
        help=f'Path to JOURNAL.md (default: {DEFAULT_JOURNAL})',
    )
    parser.add_argument(
        '--archive-path', default=str(DEFAULT_ARCHIVE),
        help=f'Path to TODO-archive.md (default: {DEFAULT_ARCHIVE})',
    )
    args = parser.parse_args()

    todo_path = Path(args.todo_path)
    journal_path = Path(args.journal_path)
    archive_path = Path(args.archive_path)

    # Safety: never touch TODO-archive.md itself as input
    if todo_path.resolve() == archive_path.resolve():
        print('ERROR: --todo-path and --archive-path must not be the same file', file=sys.stderr)
        sys.exit(1)

    if not todo_path.exists():
        print(f'ERROR: {todo_path} not found', file=sys.stderr)
        sys.exit(1)

    text = todo_path.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    # --- Collect all checked items ---
    all_checked = collect_checked_items(lines)

    # --- Filter out items already in archive ---
    already = already_archived_slugs(archive_path)
    items_to_move = [item for item in all_checked if item['slug'] not in already]

    total_in_archive = len(already) + len(items_to_move)

    if not items_to_move:
        print(f'archived 0 items (0 new; {len(already)} already in archive); rewrote 0 JOURNAL links')
        if not args.no_sync and not args.dry_run:
            _run_sync(todo_path)
        return

    if args.dry_run:
        print(f'[dry-run] would archive {len(items_to_move)} item(s):')
        for item in items_to_move:
            print(f'  {item["item_id"]} → ## Closed {item["resolved_month"]}')
        slug_set = {item['slug'] for item in items_to_move}
        # Count journal links that would be rewritten
        journal_count = rewrite_journal_links(journal_path, slug_set, dry_run=True)
        print(f'archived {len(items_to_move)} items ({total_in_archive} total in archive); rewrote {journal_count} JOURNAL links')
        return

    # --- Build archive addition ---
    archive_addition = build_archive_addition(lines, items_to_move)

    # --- Write/create archive file (FIRST, before touching TODO.md) ---
    if not archive_path.exists():
        # Brand new archive file: write header + first batch
        archive_path.write_text(ARCHIVE_HEADER + archive_addition, encoding='utf-8')
    else:
        # COR-05: merge into existing archive so same-month re-runs don't
        # create duplicate '## Closed YYYY-MM' headers.
        existing = archive_path.read_text(encoding='utf-8')
        _write_atomic(archive_path, merge_into_archive(existing, archive_addition))

    # --- Verify all moved slugs are now present in archive ---
    slug_set = {item['slug'] for item in items_to_move}
    missing = verify_ids_in_archive(archive_path, slug_set)
    if missing:
        print(
            f'ERROR: archive verification failed — these slugs not found in {archive_path}:\n'
            + ''.join(f'  {s}\n' for s in missing)
            + 'TODO.md was NOT modified.',
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Rewrite JOURNAL.md links ---
    journal_count = rewrite_journal_links(journal_path, slug_set, dry_run=False)

    # --- Rewrite TODO.md (remove moved items) ---
    new_lines = remove_items_from_todo(lines, items_to_move)
    _write_atomic(todo_path, ''.join(new_lines))

    print(
        f'archived {len(items_to_move)} items ({total_in_archive} total in archive); '
        f'rewrote {journal_count} JOURNAL links'
    )

    # --- Run sync-todo-index.py ---
    if not args.no_sync:
        _run_sync(todo_path)


def _run_sync(todo_path: Path) -> None:
    if not SYNC_SCRIPT.exists():
        print(f'WARNING: sync script not found: {SYNC_SCRIPT}', file=sys.stderr)
        return
    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), str(todo_path)],
        capture_output=False,
    )
    if result.returncode != 0:
        print(
            f'ERROR: sync-todo-index.py exited {result.returncode}.\n'
            f'TODO.md has been updated. To regenerate the index manually:\n'
            f'  bin/sync-todo-index.py {todo_path}',
            file=sys.stderr,
        )
        sys.exit(result.returncode)


if __name__ == '__main__':
    main()
