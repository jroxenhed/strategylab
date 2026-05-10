#!/usr/bin/env python3
"""sync-todo-index.py v5 — Idempotent TODO.md maintenance.

Jobs:
  1. Insert <a id="..."> anchors on every bullet line (idempotent).
  2. Regenerate ## Up Next section.
  3. Regenerate merged ## Open Work table (4 columns: Section | Topic | Open | IDs).
     - Replaces both the old intro Section/Topic table AND the old Open Work table.
     - IDs column uses range notation (F2–F3 for consecutive bare numbers).
     - F section split into 4 rows: Architecture, Hardening, Polish, Testing & Infra.
  4. Re-home items to their correct section letter (A/B/C/D/E/F) and sort numerically.
     - Removes HTML comment markers (<!-- ... -->) that were chronological grouping aids.
     - F section body emits H3 sub-headers by bucket tag (arch/hardening/polish/testing/infra).
  5. (--archive-before DATE) Move old checked items + pre-numbering block to archive file.
     - Uses JOURNAL.md backref-first date heuristic, git log fallback, default-to-old last.
     - Pre-numbering items are always archived unconditionally.

Usage:
  # In-place update (safe default — preserves tags):
  bin/sync-todo-index.py TODO.preview.md
  bin/sync-todo-index.py --output TODO.preview.md

  # Regenerate preview from a different source (explicit, warns if output exists):
  bin/sync-todo-index.py --preview-from TODO.md --output TODO.preview.md

  # Legacy positional / flags still work:
  bin/sync-todo-index.py [--input PATH] [--output PATH]
                         [--archive-before YYYY-MM-DD]
                         [--dry-run]

Input/output defaulting rules:
  - Positional argument or --input sets the input file.
  - --output sets the output file (default: same as input — in-place edit).
  - If neither --input nor a positional arg is given, input defaults to output
    (so --output TODO.preview.md alone reads from TODO.preview.md).
  - --preview-from PATH copies PATH→output first, then runs the update in-place.
  - A warning is printed when input and output differ AND the output file already
    exists with content (covers silent-overwrite of a tagged file).
"""
import argparse
import difflib
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
BULLET_RE = re.compile(
    r'^(- \[[ x]\] )(<a id="[^"]+"></a> )?\*\*([A-Z]+\d+[a-z0-9\-]*(?:\s*\+\s*[A-Z]+\d+[a-z0-9\-]*)?)\*\*'
)
CHECKBOX_RE = re.compile(r'^(- \[[ x]\] )(<a id="[^"]+"></a> )?(?!\*\*[A-Z]+\d)')
NEXT_TAG_RE = re.compile(r'\[next\]', re.IGNORECASE)
DIFF_TAG_RE = re.compile(r'\[(?:easy|medium|hard)\]', re.IGNORECASE)
H2_RE = re.compile(r'^## (.+)$')
H3_RE = re.compile(r'^### (.+)$')
SECTION_LETTER_RE = re.compile(r'^([A-Z])\d')
HTML_COMMENT_RE = re.compile(r'^<!--.*-->$')

GENERATED_SECTION_PREFIXES = ("## Critical (P1)", "## Up Next", "## Open Work")

# Header counter: **N / M shipped.**
# Visible bullets in TODO.md only cover items still in the file; items
# archived out (via --archive-before or earlier manual moves) are gone from
# the parse but should still count toward the "we've shipped X over time"
# vibe-check at the top of the file. The two offsets below capture that
# historical baseline as of 2026-05-11 — visible counts at that time were
# 50 checked / 149 total, while the header read 159 / 235.
# Bump these constants when an archive operation runs.
HISTORICAL_SHIPPED_OFFSET = 109
HISTORICAL_TOTAL_OFFSET = 86
SHIPPED_HEADER_RE = re.compile(r'(\\\*\\\*)(\d+) / (\d+)( shipped\.\\\*\\\*)')
P1_RE = re.compile(r'\[P1\]')

# ---------------------------------------------------------------------------
# F-section bucket configuration (v3)
# ---------------------------------------------------------------------------
BUCKET_TAG_RE = re.compile(r'\[(arch|hardening|polish|testing|infra)\]', re.IGNORECASE)

# Ordered list of (tag_value, H3_label, topic_description)
F_BUCKETS = [
    ('arch',       'F · Architecture',      'Refactors, abstractions, module shape'),
    ('hardening',  'F · Hardening',          'Security, reliability, validation'),
    ('polish',     'F · Polish',             'UI, naming, dead code'),
    ('testing',    'F · Testing and Infra',  'Test gaps, smoke tests, build pipeline'),
    ('infra',      'F · Testing and Infra',  'Test gaps, smoke tests, build pipeline'),
]

# Unique H3 labels in display order (testing+infra share a heading)
F_H3_ORDER = [
    ('arch',      'F · Architecture',      'Refactors, abstractions, module shape'),
    ('hardening', 'F · Hardening',          'Security, reliability, validation'),
    ('polish',    'F · Polish',             'UI, naming, dead code'),
    ('combined',  'F · Testing and Infra',  'Test gaps, smoke tests, build pipeline'),
]

# GitHub-style anchor slugs for the H3 sub-headers
F_H3_ANCHORS = {
    'arch':      'f-architecture',
    'hardening': 'f-hardening',
    'polish':    'f-polish',
    'combined':  'f-testing-and-infra',
}

# Repo root relative to this script (bin/sync-todo-index.py → project root)
REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / 'JOURNAL.md'


def github_slug(header: str) -> str:
    """Convert an H2 header text to GitHub's anchor slug format."""
    s = header.lower()
    s = re.sub(r'[^\w\s\-]', '', s)
    s = re.sub(r'\s+', '-', s.strip())
    return s


def section_slug(section_name: str) -> str:
    return github_slug(section_name)


# ---------------------------------------------------------------------------
# Sort key for item IDs
# ---------------------------------------------------------------------------

def sort_key(item_id: str):
    """Numeric sort within a section: A8 < A14b < A14c < A15.
    Sub-letters sort AFTER the bare number: F28 < F28b < F28c.
    Combined IDs (D6 + D7) use first token.
    """
    first = re.split(r'\s*\+\s*', item_id)[0].strip()
    m = re.match(r'^([A-Z]+)(\d+)([a-z]?)$', first)
    if m:
        return (m.group(1), int(m.group(2)), m.group(3))
    return (first, 0, '')


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_lines(lines: list[str]):
    """Return structured data extracted from the lines."""
    bullets: list[dict] = []
    h2_headers: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        h2 = H2_RE.match(line.rstrip())
        if h2:
            h2_headers.append((i, h2.group(1)))

        m = BULLET_RE.match(line)
        if not m:
            continue

        prefix = m.group(1)
        item_id = m.group(3)
        checked = 'x' in prefix

        diff_m = DIFF_TAG_RE.search(line)
        diff_tag = diff_m.group(0) if diff_m else ''

        bucket_m = BUCKET_TAG_RE.search(line)
        bucket_tag = bucket_m.group(1).lower() if bucket_m else ''

        rest = re.sub(
            r'- \[[ x]\] (?:<a id="[^"]+"></a> )?\*\*[A-Z]+\d+[a-z0-9\-]*(?:\s*\+\s*[A-Z]+\d+[a-z0-9\-]*)?\*\*\s*',
            '', line, count=1
        )
        em_dash_pos = rest.find(' — ')
        if em_dash_pos != -1:
            short_title = rest[:em_dash_pos].strip()
        else:
            short_title = rest.strip()[:60]

        letter_m = SECTION_LETTER_RE.match(item_id)
        letter = letter_m.group(1) if letter_m else item_id[0]

        bullets.append({
            'line_idx': i,
            'item_id': item_id,
            'checked': checked,
            'has_next': bool(NEXT_TAG_RE.search(line)),
            'has_p1': bool(P1_RE.search(line)),
            'diff_tag': diff_tag,
            'bucket_tag': bucket_tag,
            'short_title': short_title,
            'letter': letter,
        })

    return bullets, h2_headers


# ---------------------------------------------------------------------------
# Job 1 — Insert anchors
# ---------------------------------------------------------------------------

def _anchor_slug(raw_id: str) -> str:
    first = re.split(r'\s*\+\s*', raw_id)[0].strip()
    return first.lower()


def _fallback_slug(text: str) -> str:
    clean = re.sub(r'[^\w\s]', '', text.lower())
    slug = re.sub(r'\s+', '-', clean.strip())[:40].rstrip('-')
    return slug or 'item'


def insert_anchors(lines: list[str]) -> list[str]:
    result = []
    fallback_seen: set[str] = set()
    for line in lines:
        m = BULLET_RE.match(line)
        if m and not m.group(2):
            item_id = m.group(3)
            slug = _anchor_slug(item_id)
            anchor = f'<a id="{slug}"></a> '
            prefix_end = m.end(1)
            line = line[:prefix_end] + anchor + line[prefix_end:]
        elif not m:
            fb = CHECKBOX_RE.match(line)
            if fb and not fb.group(2):
                rest = line[fb.end():]
                slug = _fallback_slug(rest)
                base = slug
                counter = 1
                while slug in fallback_seen:
                    slug = f'{base}-{counter}'
                    counter += 1
                fallback_seen.add(slug)
                anchor = f'<a id="{slug}"></a> '
                prefix_end = fb.end(1)
                line = line[:prefix_end] + anchor + line[prefix_end:]
        result.append(line)
    return result


# ---------------------------------------------------------------------------
# Job 2a — Critical (P1) section
# ---------------------------------------------------------------------------

def update_shipped_counter(lines: list[str], bullets: list[dict]) -> list[str]:
    """Update the **N / M shipped.** header to reflect current visible state.

    Counter formula: visible_checked + HISTORICAL_SHIPPED_OFFSET in the
    numerator, visible_total + HISTORICAL_TOTAL_OFFSET in the denominator.
    The offsets are module constants — bump them only when an archive
    operation removes items from TODO.md. Day-to-day shipping and item
    additions are absorbed by the visible-count terms.
    """
    visible_checked = sum(1 for b in bullets if b['checked'])
    visible_total = len(bullets)
    numerator = visible_checked + HISTORICAL_SHIPPED_OFFSET
    denominator = visible_total + HISTORICAL_TOTAL_OFFSET

    for i, line in enumerate(lines):
        if SHIPPED_HEADER_RE.search(line):
            lines[i] = SHIPPED_HEADER_RE.sub(
                lambda m: f'{m.group(1)}{numerator} / {denominator}{m.group(4)}',
                line,
                count=1,
            )
            break
    return lines


def render_critical_p1(bullets: list[dict]) -> str:
    p1_items = sorted(
        [b for b in bullets if b['has_p1'] and not b['checked']],
        key=lambda b: sort_key(b['item_id']),
    )
    lines = ['## Critical (P1)', '']
    if not p1_items:
        lines.append('_(none open)_')
    else:
        for b in p1_items:
            anchor = b['item_id'].lower()
            diff = f' {b["diff_tag"]}' if b['diff_tag'] else ''
            lines.append(f'- [{b["item_id"]}](#{anchor}) — {b["short_title"]}{diff}')
    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Job 2 — Up Next section
# ---------------------------------------------------------------------------

def render_up_next(bullets: list[dict]) -> str:
    next_items = [b for b in bullets if b['has_next'] and not b['checked']]
    lines = ['## Up Next', '']
    if not next_items:
        lines.append('_(none tagged)_')
    else:
        for b in next_items:
            anchor = b['item_id'].lower()
            diff = f' {b["diff_tag"]}' if b['diff_tag'] else ''
            lines.append(f'- [{b["item_id"]}](#{anchor}) — {b["short_title"]}{diff}')
    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Job 3 — Merged Open Work table (4 columns, range notation)
# ---------------------------------------------------------------------------

def _build_id_ranges(items: list[dict]) -> str:
    """Build IDs column with range notation for consecutive bare numbers.

    Algorithm:
    - Sort items by sort_key (already done by caller, but re-sort to be safe).
    - A 'bare' ID is one where the sub-letter suffix is empty: F28 (not F28b).
    - Consecutive bare numbers form ranges: F2, F3 → [F2](#f2)–[F3](#f3).
    - Sub-lettered IDs are atoms that never collapse into ranges.
    - En-dash (–) between range endpoints, comma+space between atoms.
    """
    sorted_items = sorted(items, key=lambda b: sort_key(b['item_id']))

    # Build list of (letter, num, subletter, item_id) tuples
    parsed = []
    for b in sorted_items:
        first = re.split(r'\s*\+\s*', b['item_id'])[0].strip()
        m = re.match(r'^([A-Z]+)(\d+)([a-z]?)$', first)
        if m:
            parsed.append((m.group(1), int(m.group(2)), m.group(3), b['item_id']))
        else:
            parsed.append((first, 0, '', b['item_id']))

    # Group into run segments
    segments = []  # each is a list of (letter, num, subletter, item_id)
    for item in parsed:
        letter, num, sub, raw_id = item
        if not segments:
            segments.append([item])
            continue
        prev = segments[-1][-1]
        pletter, pnum, psub, _ = prev
        # Can extend a run only if: same letter, no sub on either, consecutive nums
        if letter == pletter and sub == '' and psub == '' and num == pnum + 1:
            segments[-1].append(item)
        else:
            segments.append([item])

    parts = []
    for seg in segments:
        if len(seg) == 1:
            _, _, _, raw_id = seg[0]
            anchor = _anchor_slug(raw_id)
            parts.append(f'[{raw_id}](#{anchor})')
        else:
            # Range
            first_raw = seg[0][3]
            last_raw = seg[-1][3]
            a1 = _anchor_slug(first_raw)
            a2 = _anchor_slug(last_raw)
            parts.append(f'[{first_raw}](#{a1})–[{last_raw}](#{a2})')

    return ', '.join(parts)


def render_open_work(bullets: list[dict], h2_headers: list[tuple[int, str]]) -> str:
    open_items = [b for b in bullets if not b['checked']]
    total = len(open_items)

    groups: dict[str, list[dict]] = {}
    for b in open_items:
        groups.setdefault(b['letter'], []).append(b)

    for letter in groups:
        groups[letter].sort(key=lambda b: sort_key(b['item_id']))

    # Map letter → (header_text, topic)
    letter_to_header: dict[str, str] = {}
    for _, hdr in h2_headers:
        lm = re.match(r'^([A-Z])\s*[—\-]\s*(.+)$', hdr)
        if lm:
            letter_to_header[lm.group(1)] = hdr

    lines = [f'## Open Work — {total} items', '']
    lines.append('| Section | Topic | Open | IDs |')
    lines.append('|---|---|---|---|')

    for letter in sorted(groups.keys()):
        items = groups[letter]
        hdr = letter_to_header.get(letter, letter)
        slug = section_slug(hdr)

        # Extract topic (part after "X — ")
        topic_m = re.match(r'^[A-Z]\s*[—\-]\s*(.+)$', hdr)
        topic = topic_m.group(1) if topic_m else hdr

        if letter == 'F':
            # Emit 4 sub-rows for F section buckets
            # Buckets: arch, hardening, polish, then testing+infra combined
            bucket_groups: dict[str, list[dict]] = {}
            untagged: list[dict] = []
            for b in items:
                bt = b.get('bucket_tag', '')
                if bt in ('arch', 'hardening', 'polish', 'testing', 'infra'):
                    bucket_groups.setdefault(bt, []).append(b)
                else:
                    untagged.append(b)

            for key, h3_label, topic_desc in F_H3_ORDER:
                anchor = F_H3_ANCHORS[key]
                if key == 'combined':
                    # testing + infra share a row
                    bucket_items = sorted(
                        bucket_groups.get('testing', []) + bucket_groups.get('infra', []),
                        key=lambda b: sort_key(b['item_id'])
                    )
                else:
                    bucket_items = sorted(
                        bucket_groups.get(key, []),
                        key=lambda b: sort_key(b['item_id'])
                    )
                if not bucket_items:
                    continue
                id_col = _build_id_ranges(bucket_items)
                lines.append(f'| [{h3_label}](#{anchor}) | {topic_desc} | {len(bucket_items)} | {id_col} |')

            if untagged:
                id_col = _build_id_ranges(untagged)
                lines.append(f'| [F · Untagged](#f-untagged) | (needs tagging) | {len(untagged)} | {id_col} |')
        else:
            id_col = _build_id_ranges(items)
            lines.append(f'| [{letter}](#{slug}) | {topic} | {len(items)} | {id_col} |')

    lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Section replacement helpers
# ---------------------------------------------------------------------------

def _is_generated_h2(stripped: str) -> bool:
    return any(stripped.startswith(p) for p in GENERATED_SECTION_PREFIXES)


def find_generated_block(lines: list[str]) -> tuple[int, int]:
    start = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if _is_generated_h2(stripped):
            if start == -1:
                start = i
        elif start != -1:
            if stripped.startswith('## ') and not _is_generated_h2(stripped):
                return start, i
    return start, len(lines)


def first_real_h2(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped.startswith('## ') and not _is_generated_h2(stripped):
            return i
    return len(lines)


def replace_generated_sections(lines: list[str], new_block: str) -> list[str]:
    start, end = find_generated_block(lines)
    new_lines = [l + '\n' for l in new_block.split('\n')]
    while new_lines and new_lines[-1].strip() == '':
        new_lines.pop()
    new_lines.append('\n')

    if start != -1:
        return lines[:start] + new_lines + lines[end:]
    else:
        insert_at = first_real_h2(lines)
        return lines[:insert_at] + new_lines + lines[insert_at:]


# ---------------------------------------------------------------------------
# Job 4 — JOURNAL date heuristic for archive decisions
# ---------------------------------------------------------------------------

def _load_journal_dates(journal_path: Path) -> dict[str, date]:
    """Parse JOURNAL.md and return {item_id: earliest_ship_date}.

    Scans for bold IDs like **[F18]** or **[B5c]** and finds the H2 date
    header (## YYYY-MM-DD ...) immediately above each mention.
    """
    if not journal_path.exists():
        return {}

    text = journal_path.read_text(encoding='utf-8')
    lines = text.splitlines()

    # Find all H2 date headers and their line positions
    # We want the nearest H2 header ABOVE a mention.
    h2_dates: list[tuple[int, date]] = []
    for i, line in enumerate(lines):
        m = re.match(r'^## (\d{4}-\d{2}-\d{2})', line)
        if m:
            try:
                h2_dates.append((i, date.fromisoformat(m.group(1))))
            except ValueError:
                pass

    # For each bold ID mention, find the date
    id_dates: dict[str, list[date]] = {}
    id_pattern = re.compile(r'\*\*\[([A-Z]+\d+[a-z0-9\-]*)\]')

    for i, line in enumerate(lines):
        for m in id_pattern.finditer(line):
            item_id = m.group(1)
            # Find the nearest H2 date header at or before this line
            best_date = None
            for hidx, hdate in h2_dates:
                if hidx <= i:
                    best_date = hdate
                # H2 dates may not be sorted due to the out-of-order entries at
                # the bottom of JOURNAL.md — find the last one at/before line i
            # Actually scan all and pick the one with highest line index <= i
            candidate = None
            for hidx, hdate in h2_dates:
                if hidx <= i:
                    if candidate is None or hidx > candidate[0]:
                        candidate = (hidx, hdate)
            if candidate:
                id_dates.setdefault(item_id, []).append(candidate[1])

    # Return earliest mention date per ID
    return {k: min(v) for k, v in id_dates.items()}


def _get_git_ship_dates(item_ids: list[str], todo_path: Path) -> dict[str, date]:
    """For each item ID, find the earliest commit that added '- [x] ...**ID**'.

    Uses git log -p to parse the patch output.
    """
    result: dict[str, date] = {}
    if not item_ids:
        return result

    try:
        proc = subprocess.run(
            ['git', 'log', '--format=COMMIT:%ci', '-p', '--', str(todo_path)],
            capture_output=True, text=True,
            cwd=todo_path.parent,
            timeout=30,
        )
    except Exception:
        return result

    if proc.returncode != 0:
        return result

    # Build a mapping: for each added checked line, record earliest commit date
    current_date = None
    for line in proc.stdout.splitlines():
        if line.startswith('COMMIT:'):
            try:
                dt_str = line[7:].strip()[:19]
                current_date = datetime.fromisoformat(dt_str).date()
            except ValueError:
                current_date = None
            continue
        if current_date is None:
            continue
        # Lines starting with '+' in patch that match a checked bullet
        if line.startswith('+- [x] '):
            for iid in item_ids:
                if iid not in result:
                    # Check if this ID appears on this added line
                    # e.g. "- [x] <a id="..."></a> **F18**" or "- [x] **F18**"
                    if re.search(r'\*\*' + re.escape(iid) + r'\*\*', line):
                        result[iid] = current_date

    return result


# ---------------------------------------------------------------------------
# Job 5 — Archive old checked items + pre-numbering block
# ---------------------------------------------------------------------------

def _find_pre_numbering_block(lines: list[str]) -> tuple[int, int]:
    """Find the ### Pre-numbering H3 and return (start, end) indices.

    start: index of the '### Pre-numbering' line
    end: index of the line AFTER the last bullet of the block
         (i.e. the next H2/H3 or EOF)
    Returns (-1, -1) if not found.
    """
    start = -1
    for i, line in enumerate(lines):
        if H3_RE.match(line.rstrip()) and 'Pre-numbering' in line:
            start = i
            break
    if start == -1:
        return -1, -1

    for i in range(start + 1, len(lines)):
        stripped = lines[i].rstrip()
        if stripped.startswith('## ') or stripped.startswith('### '):
            return start, i
    return start, len(lines)


def archive_old_items(
    lines: list[str],
    bullets: list[dict],
    h2_headers: list[tuple[int, str]],
    cutoff: date,
    archive_path: Path,
    input_path: Path,
) -> list[str]:
    """Move checked items older than cutoff + pre-numbering block to archive.

    Date heuristic order:
    1. JOURNAL.md backref grep (earliest mention under a date H2)
    2. git log fallback (earliest commit adding '- [x] ...**ID**')
    3. Default-to-old (treat as old enough to archive)
    """
    # Build sets of IDs needing date resolution
    checked_bullets = [b for b in bullets if b['checked']]

    # Step 1: JOURNAL dates
    journal_dates = _load_journal_dates(JOURNAL_PATH)

    # Step 2: git log dates for IDs not found in journal
    missing_ids = [b['item_id'] for b in checked_bullets if b['item_id'] not in journal_dates]
    git_dates = _get_git_ship_dates(missing_ids, input_path)

    def ship_date(item_id: str) -> date | None:
        if item_id in journal_dates:
            return journal_dates[item_id]
        if item_id in git_dates:
            return git_dates[item_id]
        return None  # default-to-old: None means "archive it"

    # Determine which checked bullets to archive
    to_archive_indices: set[int] = set()
    for b in checked_bullets:
        d = ship_date(b['item_id'])
        if d is None or d < cutoff:
            to_archive_indices.add(b['line_idx'])

    # Pre-numbering block: always archive unconditionally
    pn_start, pn_end = _find_pre_numbering_block(lines)
    pre_numbering_lines: list[str] = []
    if pn_start != -1:
        pre_numbering_lines = lines[pn_start:pn_end]

    if not to_archive_indices and not pre_numbering_lines:
        return lines

    # Map line_idx → letter using h2_headers
    def letter_for_line(line_idx: int) -> str:
        letter = 'Z'
        for hidx, hdr in h2_headers:
            if hidx <= line_idx:
                lm = re.match(r'^([A-Z])\s*[—\-]', hdr)
                if lm:
                    letter = lm.group(1)
        return letter

    letter_to_header: dict[str, str] = {}
    for _, hdr in h2_headers:
        lm = re.match(r'^([A-Z])\s*[—\-]', hdr)
        if lm:
            letter_to_header[lm.group(1)] = hdr

    grouped: dict[str, list[str]] = {}
    for idx in sorted(to_archive_indices):
        letter = letter_for_line(idx)
        grouped.setdefault(letter, []).append(lines[idx])

    # Build archive file content
    cutoff_str = cutoff.isoformat()
    archive_lines = [
        f'# Archived TODO items — shipped before {cutoff_str}\n',
        '\n',
        'These items were checked off in TODO.md before the cutoff and moved here to keep '
        'the active file lean. Bold ID cross-references in JOURNAL.md still work — items '
        'remain greppable across both files.\n',
        '\n',
    ]
    for letter in sorted(grouped.keys()):
        hdr = letter_to_header.get(letter, f'{letter} — Unknown')
        archive_lines.append(f'## {hdr}\n')
        archive_lines.extend(grouped[letter])
        archive_lines.append('\n')

    # Pre-numbering block in archive
    if pre_numbering_lines:
        archive_lines.append('## Pre-numbering (legacy)\n')
        # Skip the H3 header line itself, include the bullets
        for line in pre_numbering_lines:
            if H3_RE.match(line.rstrip()) and 'Pre-numbering' in line:
                continue
            archive_lines.append(line)
        archive_lines.append('\n')

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(''.join(archive_lines), encoding='utf-8')
    total_archived = len(to_archive_indices) + (len(pre_numbering_lines) - 1 if pre_numbering_lines else 0)
    print(f'Archived {len(to_archive_indices)} checked items + pre-numbering block ({len(pre_numbering_lines)} lines) → {archive_path}')

    # Remove archived lines + pre-numbering block from working copy
    # Build set of all indices to remove
    all_remove: set[int] = set(to_archive_indices)
    if pn_start != -1:
        all_remove.update(range(pn_start, pn_end))

    # Also skip continuation lines (indented sub-bullets) of archived bullets
    pruned = []
    skip_indent = False
    for i, line in enumerate(lines):
        if i in all_remove:
            # For numbered bullets, set skip_indent
            if i in to_archive_indices:
                skip_indent = True
            continue
        if skip_indent:
            if line.startswith('  ') and not BULLET_RE.match(line):
                continue
            else:
                skip_indent = False
        pruned.append(line)

    return pruned


# ---------------------------------------------------------------------------
# Job 4a — Re-home items to correct section + sort + remove comment markers
# ---------------------------------------------------------------------------

def _get_section_order(h2_headers: list[tuple[int, str]]) -> list[str]:
    """Return section letters in their current order from h2_headers."""
    letters = []
    for _, hdr in h2_headers:
        lm = re.match(r'^([A-Z])\s*[—\-]', hdr)
        if lm:
            letter = lm.group(1)
            if letter not in letters:
                letters.append(letter)
    return letters


def rehome_and_sort(lines: list[str]) -> list[str]:
    """Re-home bullets to their correct section letter, sort numerically,
    and remove HTML comment markers.

    Algorithm:
    1. Parse the file into segments: header lines, section blocks.
    2. For each section block, extract its bullets.
    3. Re-assign bullets to section blocks based on their ID letter.
    4. Within each section, sort bullets numerically (sort_key).
       - Multi-line bullets (indented continuation lines) travel with their parent.
    5. Rebuild the file.

    Only modifies the letter-keyed sections (A–F). Other content preserved.
    """

    # --- Pass 1: Identify section boundaries ---
    # Build list of segments:
    # ('pre', lines_list) — content before first lettered H2
    # ('section', letter, header_line, content_lines) — A, B, C, D, E, F sections
    # ('post', lines_list) — content after last lettered section (E — Discovery block, etc.)

    # Find all lettered H2 headers
    section_starts: list[tuple[int, str, str]] = []  # (line_idx, letter, full_line)
    for i, line in enumerate(lines):
        h2 = H2_RE.match(line.rstrip())
        if h2:
            lm = re.match(r'^([A-Z])\s*[—\-]', h2.group(1))
            if lm:
                section_starts.append((i, lm.group(1), line))

    if not section_starts:
        return lines

    # Build per-section content lists
    pre_lines = list(lines[:section_starts[0][0]])

    sections: dict[str, dict] = {}
    letter_order: list[str] = []
    for idx, (sidx, letter, hdr_line) in enumerate(section_starts):
        end_idx = section_starts[idx + 1][0] if idx + 1 < len(section_starts) else len(lines)
        # Content: everything between this header and the next section header
        content = list(lines[sidx + 1:end_idx])
        sections[letter] = {
            'header_line': hdr_line,
            'content': content,
        }
        letter_order.append(letter)

    # --- Pass 2: Extract bullets with their continuation lines ---
    # A "bullet group" = the bullet line + all immediately following indented non-bullet lines
    def extract_bullet_groups(content_lines: list[str]) -> tuple[list[tuple[str, list[str]]], list[str]]:
        """Returns (bullet_groups, non_bullet_lines).
        bullet_groups: list of (bullet_line, continuation_lines)
        non_bullet_lines: lines that are neither bullets nor continuations
        """
        groups = []
        non_bullets = []
        i = 0
        while i < len(content_lines):
            line = content_lines[i]
            m = BULLET_RE.match(line)
            if m:
                bullet_line = line
                continuations = []
                i += 1
                while i < len(content_lines):
                    next_line = content_lines[i]
                    if next_line.startswith('  ') and not BULLET_RE.match(next_line):
                        continuations.append(next_line)
                        i += 1
                    else:
                        break
                groups.append((bullet_line, continuations))
            else:
                # Skip HTML comment markers entirely
                stripped = line.rstrip()
                if HTML_COMMENT_RE.match(stripped):
                    i += 1
                    continue
                non_bullets.append(line)
                i += 1
        return groups, non_bullets

    # Collect all bullet groups across all sections
    all_bullet_groups: list[tuple[str, list[str], str]] = []  # (bullet_line, continuations, current_letter)
    section_non_bullets: dict[str, list[str]] = {}

    # H3 sub-headers we auto-generate in the F section (strip on re-run to stay idempotent)
    F_GENERATED_H3 = {
        f'### {h3_label}' for _, h3_label, _ in F_H3_ORDER
    } | {'### F · Untagged', '### F · Shipped'}  # keep '### F · Shipped' for idempotent strip of old previews

    for letter in letter_order:
        groups, non_bullets = extract_bullet_groups(sections[letter]['content'])
        if letter == 'F':
            # Strip auto-generated H3 sub-headers so Pass 5 can re-emit them cleanly
            non_bullets = [
                nl for nl in non_bullets
                if nl.rstrip() not in F_GENERATED_H3
            ]
        section_non_bullets[letter] = non_bullets
        for bullet_line, continuations in groups:
            all_bullet_groups.append((bullet_line, continuations, letter))

    # --- Pass 3: Re-assign each bullet to correct section letter ---
    section_bullets: dict[str, list[tuple[str, list[str]]]] = {l: [] for l in letter_order}

    for bullet_line, continuations, _current_letter in all_bullet_groups:
        m = BULLET_RE.match(bullet_line)
        if m:
            item_id = m.group(3)
            lm = SECTION_LETTER_RE.match(item_id)
            if lm:
                target_letter = lm.group(1)
                if target_letter in section_bullets:
                    section_bullets[target_letter].append((bullet_line, continuations))
                    continue
        # Fall through: keep in original section (shouldn't happen for numbered items)
        section_bullets[_current_letter].append((bullet_line, continuations))

    # --- Pass 4: Sort bullets numerically within each section ---
    for letter in letter_order:
        section_bullets[letter].sort(key=lambda g: sort_key(
            BULLET_RE.match(g[0]).group(3) if BULLET_RE.match(g[0]) else ''
        ))

    # --- Pass 5: Rebuild ---
    result = list(pre_lines)
    for letter in letter_order:
        result.append(sections[letter]['header_line'])
        # Non-bullet lines for this section (section description, blank lines, etc.)
        for nbl in section_non_bullets[letter]:
            result.append(nbl)

        if letter == 'F':
            # F section: all bullets (open and shipped) grouped under H3 sub-headers
            # by bucket tag, sorted numerically within each bucket regardless of status.
            by_bucket: dict[str, list] = {}
            untagged: list = []

            for bullet_line, continuations in section_bullets[letter]:
                bt_m = BUCKET_TAG_RE.search(bullet_line)
                bt = bt_m.group(1).lower() if bt_m else ''
                if bt in ('arch', 'hardening', 'polish', 'testing', 'infra'):
                    by_bucket.setdefault(bt, []).append((bullet_line, continuations))
                else:
                    untagged.append((bullet_line, continuations))

            # Emit all bullets (open + shipped) under H3 sub-headers, sorted by ID
            for key, h3_label, _topic in F_H3_ORDER:
                if key == 'combined':
                    bucket_bullets = (
                        by_bucket.get('testing', []) +
                        by_bucket.get('infra', [])
                    )
                else:
                    bucket_bullets = by_bucket.get(key, [])
                # Sort numerically by item ID regardless of open/checked status
                bucket_bullets.sort(key=lambda g: sort_key(
                    BULLET_RE.match(g[0]).group(3) if BULLET_RE.match(g[0]) else ''
                ))
                if not bucket_bullets:
                    continue
                result.append(f'\n### {h3_label}\n')
                for bullet_line, continuations in bucket_bullets:
                    result.append(bullet_line)
                    result.extend(continuations)

            # Untagged F-items (should be empty after backfill)
            if untagged:
                result.append('\n### F · Untagged\n')
                for bullet_line, continuations in untagged:
                    result.append(bullet_line)
                    result.extend(continuations)
        else:
            # Non-F sections: bullets as before
            for bullet_line, continuations in section_bullets[letter]:
                result.append(bullet_line)
                result.extend(continuations)

    # Collapse runs of 2+ consecutive blank lines to a single blank line
    result = _collapse_blank_lines(result)

    return result


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    """Replace runs of 2+ consecutive blank lines with a single blank line."""
    out = []
    blank_run = 0
    for line in lines:
        if line.strip() == '':
            blank_run += 1
            if blank_run <= 1:
                out.append(line)
        else:
            blank_run = 0
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# Intro paragraph cleanup
# ---------------------------------------------------------------------------

def strip_intro_section_table(lines: list[str]) -> list[str]:
    """Remove the old Section/Topic intro table (lines between the intro paragraph
    and the first real H2).

    The intro paragraph (line 1-3) and the --- divider are kept.
    The | Section | Topic | table block is dropped.
    The reference to ### Pre-numbering in the intro paragraph is cleaned up.
    """
    # Find the first real H2
    first_h2 = -1
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped.startswith('## ') and not _is_generated_h2(stripped):
            first_h2 = i
            break
    if first_h2 == -1:
        return lines

    # Find intro region: lines 0..first_h2
    intro = lines[:first_h2]

    # Remove lines that are part of the Section/Topic table
    # Pattern: lines starting with '| Section |' or '|---------|' or '| **A** |' etc.
    table_re = re.compile(r'^\s*\|')

    cleaned_intro = []
    prev_was_blank = False
    for line in intro:
        if table_re.match(line):
            continue  # Drop table rows
        # Clean up the mention of Pre-numbering from the intro paragraph
        # Preserve trailing newline by applying the substitution only to the content part
        has_newline = line.endswith('\n')
        stripped_line = line.rstrip('\n')
        stripped_line = re.sub(r'\s*Items below `### Pre-numbering` predate the addressing scheme\.?', '', stripped_line).rstrip()
        line = (stripped_line + '\n') if has_newline else stripped_line
        is_blank = line.strip() == ''
        # Ensure a blank line precedes the --- divider
        if line.rstrip() == '---' and not prev_was_blank:
            cleaned_intro.append('\n')
        cleaned_intro.append(line)
        prev_was_blank = is_blank

    # Remove trailing blank lines + --- divider from intro (will be regenerated if needed)
    # Actually keep the --- divider if present
    return cleaned_intro + lines[first_h2:]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process(
    input_path: Path,
    output_path: Path,
    archive_before: date | None,
    dry_run: bool,
):
    text = input_path.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    # Strip old intro Section/Topic table
    lines = strip_intro_section_table(lines)

    # Job 1 — anchors
    lines = insert_anchors(lines)

    # Parse after anchoring
    bullets, h2_headers = parse_lines(lines)

    # Job 5 — archive (modifies lines + writes archive file)
    if archive_before:
        archive_name = f'shipped-pre-{archive_before.isoformat()}.md'
        archive_path = output_path.parent / 'docs' / 'todo-archive' / archive_name
        lines = archive_old_items(lines, bullets, h2_headers, archive_before, archive_path, input_path)
        bullets, h2_headers = parse_lines(lines)

    # Job 4a — re-home items to correct section + sort + remove comment markers
    lines = rehome_and_sort(lines)
    bullets, h2_headers = parse_lines(lines)

    # Job 2a + 2 + 3 — regenerate generated sections
    critical_p1 = render_critical_p1(bullets)
    up_next = render_up_next(bullets)
    open_work = render_open_work(bullets, h2_headers)
    new_block = critical_p1 + '\n' + up_next + '\n' + open_work

    lines = replace_generated_sections(lines, new_block)

    # Job 6 — update the **N / M shipped.** header counter
    lines = update_shipped_counter(lines, bullets)

    new_text = ''.join(lines)

    if dry_run:
        diff = difflib.unified_diff(
            text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(input_path),
            tofile=str(output_path),
        )
        sys.stdout.writelines(diff)
        return

    output_path.write_text(new_text, encoding='utf-8')
    print(f'Wrote {output_path} ({len(new_text.splitlines())} lines)')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file', nargs='?', default=None, help='Input file (positional shorthand; same as --input)')
    parser.add_argument('--input', default=None, help='Input file path')
    parser.add_argument('--output', default=None, help='Output file (default: same as input — in-place edit)')
    parser.add_argument('--preview-from', metavar='PATH', dest='preview_from',
                        help='Regenerate output from this source file. Explicit clean-slate workflow; '
                             'warns and aborts if output already has content unless confirmed.')
    parser.add_argument('--archive-before', metavar='YYYY-MM-DD', help='Archive checked items older than this date')
    parser.add_argument('--dry-run', action='store_true', help='Print diff to stdout instead of writing')
    args = parser.parse_args()

    # --- Resolve input/output paths ---
    #
    # Priority for input:  positional > --input > (derived from --output) > TODO.md
    # Priority for output: --output > input_path (in-place)
    #
    # Key invariant: running `script --output TODO.preview.md` (with no --input /
    # positional) reads FROM TODO.preview.md, not from TODO.md.  This makes
    # in-place updates the safe default and prevents silent tag-wipe.

    raw_input  = args.file or args.input   # None if neither given
    raw_output = args.output               # None if not given

    if args.preview_from:
        # Explicit regeneration workflow: read from preview_from, write to output.
        # --preview-from PATH [--output OUT]  (--input is ignored / disallowed)
        if raw_input:
            parser.error('--preview-from cannot be combined with --input / positional file')
        input_path  = Path(args.preview_from)
        output_path = Path(raw_output) if raw_output else input_path
    else:
        if raw_output and not raw_input:
            # `--output TODO.preview.md` with no input: read from output file (in-place)
            input_path  = Path(raw_output)
            output_path = Path(raw_output)
        elif raw_input:
            input_path  = Path(raw_input)
            output_path = Path(raw_output) if raw_output else input_path
        else:
            # Bare invocation with no args: fall back to legacy TODO.md default
            input_path  = Path('TODO.md')
            output_path = Path('TODO.md')

    # --- Safety warning: input ≠ output AND output exists with content ---
    if not args.dry_run and input_path.resolve() != output_path.resolve():
        if output_path.exists() and output_path.stat().st_size > 0:
            print(
                f'WARNING: input ({input_path}) and output ({output_path}) differ, '
                f'and {output_path} already has content.\n'
                f'         Any tags backfilled in {output_path} will be OVERWRITTEN '
                f'from {input_path}.\n'
                f'         Use --preview-from {input_path} --output {output_path} '
                f'if regeneration from a different source is intentional.\n'
                f'         Aborting. Re-run with --dry-run to preview, or remove '
                f'--output to edit {input_path} in place.',
                file=sys.stderr,
            )
            sys.exit(1)

    archive_before = date.fromisoformat(args.archive_before) if args.archive_before else None

    if not input_path.exists():
        print(f'Error: {input_path} not found', file=sys.stderr)
        sys.exit(1)

    process(input_path, output_path, archive_before, args.dry_run)


if __name__ == '__main__':
    main()
