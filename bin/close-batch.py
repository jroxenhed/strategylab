#!/usr/bin/env python3
"""close-batch.py — Apply a closeout manifest to TODO.md mechanically.

Reads a `.run/<id>/closeout.md` manifest and performs two operations on
TODO.md (or a custom path via --todo-path), then runs sync-todo-index.py.

═══════════════════════════════════════════════════════════════════════
MANIFEST FORMAT  (.run/<id>/closeout.md)
═══════════════════════════════════════════════════════════════════════

  ## Close

  F292 resolved: lean-verify gate script shipped as bin/verify-batch.sh
  F293 resolved: this script; manifest-driven batch closeout

  ## New

  - [ ] **F300** New item title — description goes here. [easy] [infra]
  - [ ] **F301** Another item — body text. [medium] [hardening]

Rules:
- `## Close` section: one item per line, format: `<F-ID> resolved: <note>`
  The resolved note is appended verbatim after the existing line content
  in the established convention: ` (resolved YYYY-MM-DD — <note>)`
  If no note text is given (bare `F292 resolved:` or `F292 resolved:`
  with only whitespace after the colon), just `(resolved YYYY-MM-DD)` is
  appended, matching items that have no additional prose.

- `## New` section: verbatim TODO bullet lines exactly as they'd appear
  in TODO.md. Each line must:
    - Start with `- [ ] **<ID>**`
    - Carry one bucket tag: [arch] / [hardening] / [polish] / [testing] / [infra]
    - NOT carry an `(added YYYY-MM-DD)` stamp — the pre-commit hook adds it on commit.
    - To file in `## Deferred (gated)`, add `[gated: <condition>]` on the same opening
      bullet line — the tag must be on that line for the parser to detect it.
  Lines are appended to the end of the matching F-bucket H3 sub-section.
  sync-todo-index.py re-sorts and re-groups them, so exact placement is
  not critical — they just need to land inside the F section body.

- Blank lines and lines starting with `#` inside a section are skipped.
- Lines starting with `---` are treated as dividers and skipped.
- A line in `## Close` that doesn't match `<ID> resolved:` is a hard error.
- Both sections are optional.

═══════════════════════════════════════════════════════════════════════
EXAMPLE MANIFEST
═══════════════════════════════════════════════════════════════════════

  ## Close

  F292 resolved: lean-verify gate script shipped as bin/verify-batch.sh
  F293 resolved: manifest-driven batch closeout

  ## New

  - [ ] **F300** New thing — description. [easy] [infra]

═══════════════════════════════════════════════════════════════════════
BEHAVIOUR
═══════════════════════════════════════════════════════════════════════

  Hard-fail (exit 1, no writes) if:
    - Any Close ID is not found in TODO.md
    - Any Close ID is already checked (`- [x]`)
    - Any Close ID appears more than once in the manifest
    - Any New item line does not parse as a valid `- [ ] **ID**` bullet
    - Any New item ID is already present in TODO.md

  --dry-run: prints planned edits and exits without writing.
  --no-sync: skip running sync-todo-index.py (useful for tests).

Usage:
  bin/close-batch.py .run/<id>/closeout.md [--todo-path PATH] [--dry-run] [--no-sync]
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

# DIG-01/REL-06: prefer backend's atomic_write_text (tempfile+fsync+os.replace
# in the same dir).  Import it via sys.path manipulation; fileutil.py has no
# FastAPI or heavy-package side effects — only stdlib imports + logging setup.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
try:
    from fileutil import atomic_write_text as _atomic_write  # type: ignore[import]
    _HAS_ATOMIC = True
except ImportError:
    _HAS_ATOMIC = False


def _write_atomic(path: Path, content: str) -> None:
    """Write content to path atomically (tempfile + os.replace)."""
    if _HAS_ATOMIC:
        _atomic_write(path, content, backup_depth=1)
        return
    # Inline fallback: tempfile in same dir + fsync + os.replace
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
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TODO = REPO_ROOT / 'TODO.md'
SYNC_SCRIPT = REPO_ROOT / 'bin' / 'sync-todo-index.py'

BULLET_RE = re.compile(
    r'^(- \[[ x]\] )(<a id="[^"]+"></a> )?\*\*([A-Z]+\d+[a-z0-9\-]*)\*\*'
)
CLOSE_LINE_RE = re.compile(
    r'^([A-Z]+\d+[a-z0-9\-]*)\s+resolved\s*:\s*(.*)$'
)
NEW_BULLET_RE = re.compile(
    r'^- \[ \] \*\*([A-Z]+\d+[a-z0-9\-]*)\*\*'
)
BUCKET_TAG_RE = re.compile(r'\[(arch|hardening|polish|testing|infra|features)\]', re.IGNORECASE)
GATED_TAG_RE = re.compile(r'\[gated:[^\]]*\]', re.IGNORECASE)
H3_RE = re.compile(r'^### (.+)$')
H2_RE = re.compile(r'^## (.+)$')

# COR-01: map bucket tag → H2 section name (new TODO.md structure)
BUCKET_H2 = {
    'arch':      'Architecture',
    'hardening': 'Hardening',
    'polish':    'Polish',
    'testing':   'Testing',
    'infra':     'Infra',
    'features':  'Features',
}


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

def parse_manifest(manifest_path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse closeout.md into (close_items, new_items).

    close_items: list of (item_id, resolved_note_text)
    new_items:   list of raw bullet strings (without trailing newline)
    """
    text = manifest_path.read_text(encoding='utf-8')
    lines = text.splitlines()

    close_items: list[tuple[str, str]] = []
    new_items: list[str] = []

    section = None
    errors: list[str] = []

    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip()

        # Section headers
        h2 = H2_RE.match(line)
        if h2:
            header = h2.group(1).strip()
            if header.lower() == 'close':
                section = 'close'
            elif header.lower() == 'new':
                section = 'new'
            else:
                section = None  # unknown section, ignore
            continue

        # Skip blank lines, comment lines, dividers
        if not line or line.startswith('#') or line.startswith('---'):
            continue

        if section == 'close':
            m = CLOSE_LINE_RE.match(line)
            if not m:
                errors.append(
                    f'Manifest line {lineno}: expected "<ID> resolved: <note>", got: {line!r}'
                )
                continue
            item_id = m.group(1)
            note = m.group(2).strip()
            close_items.append((item_id, note))

        elif section == 'new':
            m = NEW_BULLET_RE.match(line)
            if not m:
                errors.append(
                    f'Manifest line {lineno}: expected "- [ ] **ID** ...", got: {line!r}'
                )
                continue
            new_items.append(line)

    if errors:
        for e in errors:
            print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)

    return close_items, new_items


# ---------------------------------------------------------------------------
# TODO.md mutations
# ---------------------------------------------------------------------------

def find_item_line(lines: list[str], item_id: str) -> int:
    """Return 0-based index of the bullet line for item_id, or -1."""
    for i, line in enumerate(lines):
        m = BULLET_RE.match(line)
        if m and m.group(3) == item_id:
            return i
    return -1


def build_resolved_suffix(note: str) -> str:
    """Build the (resolved YYYY-MM-DD ...) suffix string."""
    today = date.today().isoformat()
    if note:
        return f' (resolved {today} — {note})'
    else:
        return f' (resolved {today})'


def apply_close(lines: list[str], item_id: str, note: str, dry_run: bool) -> list[str]:
    """Flip checkbox and append resolved note. Returns new lines list."""
    # KPY-02/DIG-02: raise ValueError instead of sys.exit so control flow is
    # honest and functions remain unit-testable.  validate_manifest() pre-flight
    # already guarantees these paths are dead code in normal operation.
    idx = find_item_line(lines, item_id)
    if idx == -1:
        raise ValueError(f'{item_id} not found in TODO.md')

    line = lines[idx]
    m = BULLET_RE.match(line)
    assert m is not None  # guaranteed by find_item_line
    if 'x' in m.group(1):
        raise ValueError(f'{item_id} is already checked off in TODO.md')

    suffix = build_resolved_suffix(note)
    # DIG-04: flip checkbox via match group position, not raw str.replace,
    # so line content containing '- [ ]' is never accidentally mutated.
    new_line = line[:m.start(1)] + '- [x] ' + line[m.end(1):]
    # Append resolved note before trailing newline
    if new_line.endswith('\n'):
        new_line = new_line.rstrip('\n') + suffix + '\n'
    else:
        new_line = new_line + suffix

    if dry_run:
        print(f'  CLOSE {item_id}:')
        print(f'    - {line.rstrip()!r}')
        print(f'    + {new_line.rstrip()!r}')

    new_lines = list(lines)
    new_lines[idx] = new_line
    return new_lines


def find_h2_section_bounds(lines: list[str], section_name: str) -> tuple[int, int]:
    """Return (start, end) indices of the body of the H2 section named section_name.

    COR-01: Locates sections by their plain H2 name (e.g. 'Architecture'),
    not the old '## F — ...' pattern.

    start: line after the matching '## <section_name>' header
    end: line index of the next ## header (or EOF)
    Returns (-1, -1) if not found.
    """
    in_section = False
    sec_start = -1
    target = section_name.strip().lower()
    for i, line in enumerate(lines):
        h2 = H2_RE.match(line.rstrip())
        if h2:
            hdr = h2.group(1).strip().lower()
            if hdr == target:
                in_section = True
                sec_start = i + 1
                continue
            elif in_section:
                return sec_start, i
    if in_section:
        return sec_start, len(lines)
    return -1, -1


def find_insert_position_in_section(lines: list[str], sec_start: int, sec_end: int) -> int:
    """Return the index to insert a new bullet at the end of a section body.

    COR-01: Scans backward from sec_end to find the last non-blank line,
    then inserts after it (leaving a trailing blank line before the next section).
    Falls back to sec_end if section is empty.
    """
    insert_at = sec_end
    for i in range(sec_end - 1, sec_start - 1, -1):
        stripped = lines[i].rstrip()
        if stripped:
            insert_at = i + 1
            break
    return insert_at


def apply_new(lines: list[str], bullet: str, dry_run: bool) -> list[str]:
    """Insert a new TODO bullet into the appropriate H2 section.

    COR-01: Uses BUCKET_H2 mapping to locate the target H2 section by its
    plain name (Architecture, Hardening, etc.) instead of the old '## F — ...'
    pattern. Warns and defaults to Infra if no bucket tag is present.
    """
    m = NEW_BULLET_RE.match(bullet)
    item_id = m.group(1)

    # Validate not already present (KPY-02/DIG-02: raise, not sys.exit)
    if find_item_line(lines, item_id) != -1:
        raise ValueError(f'{item_id} already exists in TODO.md')

    # DIG-03: require exactly one bucket tag (silent first-match was wrong)
    all_tags = BUCKET_TAG_RE.findall(bullet)
    if len(all_tags) == 0:
        # COR-01: warn and default to Infra rather than hard-failing,
        # so manifests without a tag still land somewhere sensible.
        print(
            f'WARNING: New item {item_id} has no bucket tag '
            f'([arch]/[hardening]/[polish]/[testing]/[infra]/[features]); '
            f'defaulting to Infra',
            file=sys.stderr,
        )
        bucket = 'infra'
    elif len(all_tags) > 1:
        raise ValueError(
            f'New item {item_id} has multiple bucket tags: {all_tags!r} — '
            f'exactly one is required'
        )
    else:
        bucket = all_tags[0].lower()

    section_name = BUCKET_H2.get(bucket, 'Infra')

    # F304: if the bullet carries a [gated: ...] tag, route to Deferred (gated)
    # regardless of its bucket.  A missing bucket tag warns and defaults to Infra
    # (same behaviour as non-gated items — the section_name override below takes
    # precedence so the item still lands in Deferred, not Infra).
    if GATED_TAG_RE.search(bullet):
        section_name = 'Deferred (gated)'

    sec_start, sec_end = find_h2_section_bounds(lines, section_name)
    if sec_start == -1:
        raise ValueError(f'Could not find H2 section "{section_name}" in TODO.md')

    insert_at = find_insert_position_in_section(lines, sec_start, sec_end)

    new_line = bullet if bullet.endswith('\n') else bullet + '\n'

    if dry_run:
        print(f'  NEW {item_id} → ## {section_name} (insert at line {insert_at + 1}):')
        print(f'    + {bullet!r}')

    new_lines = list(lines)
    new_lines.insert(insert_at, new_line)
    return new_lines


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_manifest(
    close_items: list[tuple[str, str]],
    new_items: list[str],
    lines: list[str],
) -> None:
    """Pre-flight checks. Exits 1 on any error, prints all violations."""
    errors: list[str] = []

    # Check for duplicate IDs in Close section
    seen_close: set[str] = set()
    for item_id, _ in close_items:
        if item_id in seen_close:
            errors.append(f'Duplicate Close ID in manifest: {item_id}')
        seen_close.add(item_id)

    # Check for duplicate IDs in New section
    seen_new: set[str] = set()
    for bullet in new_items:
        m = NEW_BULLET_RE.match(bullet)
        item_id = m.group(1)
        if item_id in seen_new:
            errors.append(f'Duplicate New ID in manifest: {item_id}')
        seen_new.add(item_id)

    # Check Close IDs exist and are unchecked
    for item_id, _ in close_items:
        idx = find_item_line(lines, item_id)
        if idx == -1:
            errors.append(f'Close: {item_id} not found in TODO.md')
        else:
            line = lines[idx]
            m = BULLET_RE.match(line)
            if m and 'x' in m.group(1):
                errors.append(f'Close: {item_id} is already checked off')

    # Check New IDs don't already exist
    for bullet in new_items:
        m = NEW_BULLET_RE.match(bullet)
        item_id = m.group(1)
        idx = find_item_line(lines, item_id)
        if idx != -1:
            errors.append(f'New: {item_id} already exists in TODO.md')

    # Pre-flight: if any new item carries a [gated:] tag it will be routed to
    # "## Deferred (gated)".  Verify that section exists NOW so the error is
    # surfaced here (clear name + context) rather than as a raw ValueError
    # mid-apply after all other validations pass.
    has_gated = any(GATED_TAG_RE.search(b) for b in new_items)
    if has_gated:
        deferred_start, _ = find_h2_section_bounds(lines, 'Deferred (gated)')
        if deferred_start == -1:
            errors.append(
                'TODO.md is missing the "## Deferred (gated)" section, which is '
                'required to file gated items. Add the section before running this manifest.'
            )

    if errors:
        for e in errors:
            print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:  # KPY-04: explicit return-type annotation
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'manifest',
        help='Path to closeout.md manifest file',
    )
    parser.add_argument(
        '--todo-path',
        default=str(DEFAULT_TODO),
        help=f'Path to TODO.md (default: {DEFAULT_TODO})',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print planned edits without writing anything',
    )
    parser.add_argument(
        '--no-sync',
        action='store_true',
        help='Skip running sync-todo-index.py (useful for isolated tests)',
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    todo_path = Path(args.todo_path)

    if not manifest_path.exists():
        print(f'ERROR: manifest not found: {manifest_path}', file=sys.stderr)
        sys.exit(1)
    if not todo_path.exists():
        print(f'ERROR: TODO file not found: {todo_path}', file=sys.stderr)
        sys.exit(1)

    # Parse manifest
    close_items, new_items = parse_manifest(manifest_path)

    if not close_items and not new_items:
        print('Nothing to do (manifest has no Close or New items).')
        sys.exit(0)

    # Read TODO
    text = todo_path.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    # Pre-flight validation (all-or-nothing before any mutation)
    validate_manifest(close_items, new_items, lines)

    if args.dry_run:
        print(f'DRY RUN — no writes to {todo_path}')
        print()

    # Apply Close operations
    if close_items:
        if args.dry_run:
            print('=== Close operations ===')
        try:
            for item_id, note in close_items:
                lines = apply_close(lines, item_id, note, dry_run=args.dry_run)
        except ValueError as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            sys.exit(1)

    # Apply New operations
    if new_items:
        if args.dry_run:
            print()
            print('=== New items ===')
        try:
            for bullet in new_items:
                lines = apply_new(lines, bullet, dry_run=args.dry_run)
        except ValueError as exc:
            print(f'ERROR: {exc}', file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        print()
        print('(no files written)')
        return

    # DIG-06: structural guard — all writes and side-effects live here so
    # --dry-run cannot accidentally leak through if code is inserted above.
    if not args.dry_run:
        # DIG-01/REL-06: atomic write (tempfile + fsync + os.replace)
        new_text = ''.join(lines)
        _write_atomic(todo_path, new_text)
        print(f'Wrote {todo_path}')

        # Run sync-todo-index
        if not args.no_sync:
            if not SYNC_SCRIPT.exists():
                print(f'WARNING: sync script not found: {SYNC_SCRIPT}', file=sys.stderr)
            else:
                result = subprocess.run(
                    [sys.executable, str(SYNC_SCRIPT), str(todo_path)],
                    capture_output=False,
                )
                if result.returncode != 0:
                    # REL-07: TODO.md is already written; give operator a
                    # clear re-run hint so they don't re-run close-batch.py
                    # (which would fail pre-flight because IDs are now checked).
                    print(
                        f'ERROR: sync-todo-index.py exited {result.returncode}.\n'
                        f'TODO.md has already been updated. To regenerate the\n'
                        f'index manually, run:\n'
                        f'  bin/sync-todo-index.py {todo_path}',
                        file=sys.stderr,
                    )
                    sys.exit(result.returncode)
        else:
            print('(skipped sync-todo-index.py)')


if __name__ == '__main__':
    main()
