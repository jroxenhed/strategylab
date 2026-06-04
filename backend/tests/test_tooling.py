"""F303 — Fixture-based end-to-end test for the tracking toolchain.

Tests sync-todo-index.py, close-batch.py (Close + New paths, including the
F304 gated-item routing), and archive-todo.py against small synthetic fixtures
in a tmp dir.

Key invariants checked:
- COR-01 (P0): New items with bucket tags land in the correct H2 section.
- F304: New items with [gated: ...] tags land under ## Deferred (gated).
- Close path: correct checkbox flip + (resolved ...) note appended.
- Dissolved-section guard (COR-01 P0): closing an item whose section no
  longer exists must not corrupt the file (hard-fail, not silent corruption).
- archive-todo: moves all [x] items to TODO-archive.md and is idempotent.
- COR-05: Re-running archive-todo does NOT duplicate ## Closed YYYY-MM headers.
- COR-06: ## Deferred (gated) items are excluded from the Open Work count.
- sync-todo-index: Open Work table item count matches actual unchecked items.
- Every open item ID appears exactly once in TODO.md after sync.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths to the bin scripts (resolved relative to this test file)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CLOSE_BATCH = _REPO_ROOT / "bin" / "close-batch.py"
_SYNC_INDEX = _REPO_ROOT / "bin" / "sync-todo-index.py"
_ARCHIVE_TODO = _REPO_ROOT / "bin" / "archive-todo.py"

PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Synthetic fixture content
# ---------------------------------------------------------------------------

# A minimal TODO.md with:
#  - An introductory header line
#  - Generated sections (Critical/Up Next/Open Work) — will be regenerated
#  - Two bucket sections: ## Testing and ## Infra
#  - A ## Deferred (gated) section with one pre-existing item
#  - Two open items (F901 in Testing, F902 in Infra) and one gated item (F903)
#  - One open item in Testing (F911) that will be closed by close-batch
_TODO_FIXTURE = """\
# Test TODO

---

## Critical (P1)

_(none open)_

## Up Next

_(none tagged)_

## Open Work — 0 items

| Section | Open | IDs |
|---|---|---|

## Testing

- [ ] <a id="f901"></a> **F901** Test item one — fixture. [easy] [testing]
- [ ] <a id="f911"></a> **F911** Test item to close — will be checked off. [easy] [testing]

## Infra

- [ ] <a id="f902"></a> **F902** Infra item — fixture. [easy] [infra]

## Deferred (gated)

- [ ] <a id="f903"></a> **F903** Gated item — pre-existing. [arch] [gated: some condition]

"""

# A minimal (empty) TODO-archive.md
_ARCHIVE_FIXTURE = """\
# TODO-archive.md — Closed items from TODO.md

Items moved here once checked off. Anchors are preserved so existing JOURNAL.md
links (`TODO-archive.md#id`) continue to resolve. Content is append-only and
grouped by close month. Never reorder or rewrite previously archived sections.

"""

# A minimal JOURNAL.md (no links that need rewriting)
_JOURNAL_FIXTURE = """\
# Journal

## 2026-06-04

- **[F901]** Filed as test fixture.
"""


def _run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command, capturing stdout+stderr, not raising on non-zero."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _count_open_items(text: str) -> int:
    """Count unchecked bullet lines matching the canonical bullet pattern."""
    return len(re.findall(r"^- \[ \]", text, re.MULTILINE))


def _open_item_ids(text: str) -> list[str]:
    """Return list of all item IDs that appear on unchecked bullet lines."""
    return re.findall(
        r"^- \[ \] (?:<a id=\"[^\"]+\"></a> )?\*\*([A-Z]+\d+[a-z0-9\-]*)\*\*",
        text,
        re.MULTILINE,
    )


def _open_work_count(text: str) -> int | None:
    """Extract the item count from the ## Open Work — N items header."""
    m = re.search(r"^## Open Work — (\d+) items", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def _section_contains(text: str, section_h2: str, item_id: str) -> bool:
    """Return True if item_id appears inside the named H2 section."""
    # Find the section start
    pattern = re.compile(
        r"^## " + re.escape(section_h2) + r"$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return False
    # Find the next H2 (or EOF)
    next_h2 = re.search(r"^## ", text[m.end():], re.MULTILINE)
    section_body = text[m.end(): m.end() + next_h2.start()] if next_h2 else text[m.end():]
    return bool(re.search(r"\*\*" + re.escape(item_id) + r"\*\*", section_body))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path):
    """Set up a temporary workspace with synthetic fixture files."""
    todo_path = tmp_path / "TODO.md"
    archive_path = tmp_path / "TODO-archive.md"
    journal_path = tmp_path / "JOURNAL.md"

    todo_path.write_text(_TODO_FIXTURE, encoding="utf-8")
    archive_path.write_text(_ARCHIVE_FIXTURE, encoding="utf-8")
    journal_path.write_text(_JOURNAL_FIXTURE, encoding="utf-8")

    return {
        "root": tmp_path,
        "todo": todo_path,
        "archive": archive_path,
        "journal": journal_path,
    }


# ---------------------------------------------------------------------------
# Helper: manifest file
# ---------------------------------------------------------------------------

def _make_manifest(tmp_path: Path, content: str, name: str = "closeout.md") -> Path:
    """Write a manifest file to tmp_path.

    Pass a unique ``name`` when you need two distinct manifests in the same test
    (the default 'closeout.md' is always overwritten if called twice).
    """
    m = tmp_path / name
    m.write_text(content, encoding="utf-8")
    return m


# ---------------------------------------------------------------------------
# Tests — close-batch.py Close path
# ---------------------------------------------------------------------------

class TestCloseBatchClose:
    def test_close_flips_checkbox(self, workspace, tmp_path):
        """close-batch closes a known item: checkbox flipped, resolved note appended."""
        manifest = _make_manifest(tmp_path, """\
## Close

F911 resolved: fixture test verified close path
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        # Checkbox must be [x]
        assert re.search(r"- \[x\].*\*\*F911\*\*", text), "F911 not checked off"
        # Resolved note present
        assert "(resolved " in text, "resolved note missing"
        assert "fixture test verified close path" in text

    def test_close_nonexistent_id_fails(self, workspace, tmp_path):
        """close-batch exits 1 if the Close ID doesn't exist in TODO.md."""
        manifest = _make_manifest(tmp_path, """\
## Close

F999 resolved: this id does not exist
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode != 0, "Should have failed for missing ID"

    def test_close_already_checked_fails(self, workspace, tmp_path):
        """close-batch exits 1 when trying to close an already-checked item."""
        # First close it
        manifest1 = _make_manifest(tmp_path, """\
## Close

F911 resolved: first close
""", name="closeout1.md")
        r1 = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest1),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert r1.returncode == 0

        # Try to close again (use a distinct filename to avoid silent overwrite)
        manifest2 = _make_manifest(tmp_path, """\
## Close

F911 resolved: second close attempt
""", name="closeout2.md")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest2),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode != 0, "Should fail on double-close"


# ---------------------------------------------------------------------------
# Tests — close-batch.py New path (non-gated)
# ---------------------------------------------------------------------------

class TestCloseBatchNew:
    def test_new_item_lands_in_bucket_section(self, workspace, tmp_path):
        """A new item with [arch] routes to ## Architecture (not Infra or Testing)."""
        # Our fixture doesn't have ## Architecture; add it.
        todo_text = workspace["todo"].read_text(encoding="utf-8")
        todo_text += "\n## Architecture\n\n_(empty)_\n"
        workspace["todo"].write_text(todo_text, encoding="utf-8")

        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F920** New arch item — description. [arch]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        # F920 must appear in the file
        assert "**F920**" in text, "F920 not inserted"
        # Must be inside ## Architecture section
        assert _section_contains(text, "Architecture", "F920"), \
            "F920 not in ## Architecture"
        # Must NOT be inside ## Deferred (gated)
        assert not _section_contains(text, "Deferred (gated)", "F920"), \
            "F920 incorrectly landed in ## Deferred (gated)"

    def test_new_item_infra_routes_to_infra(self, workspace, tmp_path):
        """A new item with [infra] routes to ## Infra."""
        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F921** New infra item — description. [infra]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        assert "**F921**" in text
        assert _section_contains(text, "Infra", "F921"), "F921 not in ## Infra"
        assert not _section_contains(text, "Deferred (gated)", "F921"), \
            "F921 wrongly in Deferred"

    def test_new_item_duplicate_id_fails(self, workspace, tmp_path):
        """close-batch exits 1 if the New item ID already exists in TODO.md."""
        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F901** Duplicate — already exists. [testing]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode != 0, "Should fail for duplicate ID"

    def test_dissolved_section_fails_gracefully(self, workspace, tmp_path):
        """COR-01 P0: inserting into a bucket whose H2 section doesn't exist
        exits 1 without corrupting the file."""
        # [hardening] maps to ## Hardening, which is not in our fixture
        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F930** Missing section item — description. [hardening]
""")
        original_text = workspace["todo"].read_text(encoding="utf-8")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        # Must exit non-zero
        assert result.returncode != 0, "Should fail when section is dissolved"
        # Error output must mention the missing section — distinguishes a real
        # "section not found" failure from an unrelated crash or import error.
        combined = result.stderr + result.stdout
        assert "Hardening" in combined or "Could not find H2 section" in combined, (
            f"Expected 'Hardening' or 'Could not find H2 section' in output; got:\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
        # TODO.md must be unchanged
        after_text = workspace["todo"].read_text(encoding="utf-8")
        assert after_text == original_text, "TODO.md was corrupted by the failed run"


# ---------------------------------------------------------------------------
# Tests — F304: gated-item routing via close-batch New
# ---------------------------------------------------------------------------

class TestCloseBatchGatedRouting:
    def test_gated_item_lands_in_deferred(self, workspace, tmp_path):
        """F304: A new item with [gated: ...] routes to ## Deferred (gated)."""
        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F940** Gated new item — locked behind condition. [gated: needs UI spec] [arch]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        assert "**F940**" in text, "F940 not inserted"
        # Must be inside ## Deferred (gated)
        assert _section_contains(text, "Deferred (gated)", "F940"), \
            "F940 did not land in ## Deferred (gated)"

    def test_gated_item_not_in_bucket_section(self, workspace, tmp_path):
        """F304: Gated item should NOT appear in its bucket section."""
        # Add ## Architecture so a non-gated arch item would normally go there
        todo_text = workspace["todo"].read_text(encoding="utf-8")
        todo_text += "\n## Architecture\n\n"
        workspace["todo"].write_text(todo_text, encoding="utf-8")

        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F941** Another gated item — description. [gated: future feature] [arch]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        assert "**F941**" in text
        # Must be in Deferred, NOT in Architecture
        assert _section_contains(text, "Deferred (gated)", "F941"), \
            "F941 not in Deferred (gated)"
        assert not _section_contains(text, "Architecture", "F941"), \
            "F941 incorrectly landed in Architecture"

    def test_gated_item_with_infra_bucket(self, workspace, tmp_path):
        """F304: gated routing overrides any bucket (infra here), lands in Deferred."""
        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F942** Gated infra item — description. [gated: gating condition] [infra]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        assert _section_contains(text, "Deferred (gated)", "F942"), \
            "F942 not in Deferred (gated)"
        assert not _section_contains(text, "Infra", "F942"), \
            "F942 incorrectly in Infra"

    def test_non_gated_item_not_in_deferred(self, workspace, tmp_path):
        """Sanity: non-gated item must NOT land in Deferred (gated)."""
        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F943** Normal testing item — no gate. [testing]
""")
        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"close-batch failed:\n{result.stderr}"

        text = workspace["todo"].read_text(encoding="utf-8")
        assert not _section_contains(text, "Deferred (gated)", "F943"), \
            "Non-gated item F943 wrongly landed in Deferred (gated)"
        assert _section_contains(text, "Testing", "F943"), \
            "F943 not in ## Testing"

    def test_gated_item_missing_deferred_section_fails_preflight(self, tmp_path):
        """FIX-02: if TODO.md has no ## Deferred (gated) section, a manifest with a
        gated item must fail with a clear error BEFORE any write occurs (pre-flight)."""
        # Build a TODO.md without ## Deferred (gated)
        todo_no_deferred = tmp_path / "TODO_no_deferred.md"
        todo_no_deferred.write_text("""\
# Test TODO

## Testing

- [ ] **F901** Existing item. [easy] [testing]

## Infra

_(empty)_
""", encoding="utf-8")
        original_text = todo_no_deferred.read_text(encoding="utf-8")

        manifest = _make_manifest(tmp_path, """\
## New

- [ ] **F944** Gated item but no Deferred section. [gated: needs spec] [arch]
""", name="gated_no_deferred.md")

        result = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(todo_no_deferred),
             "--no-sync"],
        )
        assert result.returncode != 0, "Should fail when ## Deferred (gated) is missing"
        # Must name the missing section in the error output
        combined = result.stderr + result.stdout
        assert "Deferred (gated)" in combined, (
            f"Expected 'Deferred (gated)' in error output; got:\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
        # File must be untouched
        after_text = todo_no_deferred.read_text(encoding="utf-8")
        assert after_text == original_text, "TODO.md was written despite pre-flight failure"


# ---------------------------------------------------------------------------
# Tests — archive-todo.py
# ---------------------------------------------------------------------------

class TestArchiveTodo:
    def _close_item(self, workspace, tmp_path, item_id: str) -> None:
        """Helper: use close-batch to check off an item, then confirm it's [x]."""
        manifest = _make_manifest(
            tmp_path,
            f"## Close\n\n{item_id} resolved: test archive run\n",
        )
        r = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert r.returncode == 0, f"close failed: {r.stderr}"

    def test_archive_moves_checked_items(self, workspace, tmp_path):
        """archive-todo moves [x] items to TODO-archive.md and removes from TODO.md."""
        self._close_item(workspace, tmp_path, "F911")

        result = _run(
            [PYTHON, str(_ARCHIVE_TODO),
             "--todo-path", str(workspace["todo"]),
             "--archive-path", str(workspace["archive"]),
             "--journal-path", str(workspace["journal"]),
             "--no-sync"],
        )
        assert result.returncode == 0, f"archive-todo failed:\n{result.stderr}"

        todo_text = workspace["todo"].read_text(encoding="utf-8")
        archive_text = workspace["archive"].read_text(encoding="utf-8")

        # F911 gone from TODO.md
        assert "**F911**" not in todo_text, "F911 still in TODO.md after archive"
        # F911 present in archive
        assert "**F911**" in archive_text, "F911 not found in TODO-archive.md"
        # Archive has a ## Closed YYYY-MM section
        assert re.search(r"^## Closed \d{4}-\d{2}$", archive_text, re.MULTILINE), \
            "No ## Closed YYYY-MM section in archive"

    def test_archive_is_idempotent(self, workspace, tmp_path):
        """COR-05: Running archive-todo twice does NOT duplicate ## Closed headers."""
        self._close_item(workspace, tmp_path, "F911")

        run_args = [
            PYTHON, str(_ARCHIVE_TODO),
            "--todo-path", str(workspace["todo"]),
            "--archive-path", str(workspace["archive"]),
            "--journal-path", str(workspace["journal"]),
            "--no-sync",
        ]
        r1 = _run(run_args)
        assert r1.returncode == 0, f"first archive run failed: {r1.stderr}"

        r2 = _run(run_args)
        assert r2.returncode == 0, f"second archive run failed: {r2.stderr}"

        archive_text = workspace["archive"].read_text(encoding="utf-8")

        # Count ## Closed YYYY-MM headers — must appear exactly once per month
        closed_headers = re.findall(r"^## Closed \d{4}-\d{2}$", archive_text, re.MULTILINE)
        month_counts: dict[str, int] = {}
        for h in closed_headers:
            month_counts[h] = month_counts.get(h, 0) + 1
        duplicates = {h: c for h, c in month_counts.items() if c > 1}
        assert not duplicates, f"Duplicate ## Closed headers after second run: {duplicates}"

    def test_archive_leaves_open_items_in_todo(self, workspace, tmp_path):
        """Open items are NOT moved by archive-todo."""
        self._close_item(workspace, tmp_path, "F911")

        _run(
            [PYTHON, str(_ARCHIVE_TODO),
             "--todo-path", str(workspace["todo"]),
             "--archive-path", str(workspace["archive"]),
             "--journal-path", str(workspace["journal"]),
             "--no-sync"],
        )

        todo_text = workspace["todo"].read_text(encoding="utf-8")
        # F901 and F902 are still open — must remain in TODO.md
        assert "**F901**" in todo_text, "Open item F901 was incorrectly archived"
        assert "**F902**" in todo_text, "Open item F902 was incorrectly archived"

    def test_gated_items_not_archived(self, workspace, tmp_path):
        """Gated items are open (unchecked) — archive-todo must leave them in TODO.md."""
        # F903 is an open gated item in the fixture; archive-todo should ignore it
        _run(
            [PYTHON, str(_ARCHIVE_TODO),
             "--todo-path", str(workspace["todo"]),
             "--archive-path", str(workspace["archive"]),
             "--journal-path", str(workspace["journal"]),
             "--no-sync"],
        )
        todo_text = workspace["todo"].read_text(encoding="utf-8")
        assert "**F903**" in todo_text, "Gated open item F903 was incorrectly archived"


# ---------------------------------------------------------------------------
# Tests — sync-todo-index.py
# ---------------------------------------------------------------------------

class TestSyncTodoIndex:
    def test_sync_runs_on_fixture(self, workspace):
        """sync-todo-index.py runs without error on the fixture file."""
        result = _run(
            [PYTHON, str(_SYNC_INDEX), str(workspace["todo"])],
        )
        assert result.returncode == 0, (
            f"sync-todo-index failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )

    def test_open_work_count_matches_actual(self, workspace):
        """COR-06: Open Work table count == actual unchecked items
        (Deferred items are EXCLUDED from the count)."""
        result = _run(
            [PYTHON, str(_SYNC_INDEX), str(workspace["todo"])],
        )
        assert result.returncode == 0

        text = workspace["todo"].read_text(encoding="utf-8")
        table_count = _open_work_count(text)
        assert table_count is not None, "## Open Work — N items header not found"

        # Count open items not under ## Deferred (gated)
        # Split at the Deferred section and count only items BEFORE it
        deferred_pos = text.find("## Deferred (gated)")
        if deferred_pos != -1:
            non_deferred_text = text[:deferred_pos]
        else:
            non_deferred_text = text

        # Count unchecked bullets in non-deferred regions
        # (exclude generated section headers like Critical/Up Next/Open Work)
        non_deferred_open = len(re.findall(
            r"^- \[ \] (?:<a id=\"[^\"]+\"></a> )?\*\*[A-Z]+\d+",
            non_deferred_text,
            re.MULTILINE,
        ))
        assert table_count == non_deferred_open, (
            f"Open Work table says {table_count} but counted {non_deferred_open} "
            f"non-deferred open items"
        )

    def test_no_duplicate_ids_after_sync(self, workspace):
        """Every open item ID appears exactly once in TODO.md after sync."""
        _run([PYTHON, str(_SYNC_INDEX), str(workspace["todo"])])
        text = workspace["todo"].read_text(encoding="utf-8")
        ids = _open_item_ids(text)
        seen: dict[str, int] = {}
        for iid in ids:
            seen[iid] = seen.get(iid, 0) + 1
        duplicates = {k: v for k, v in seen.items() if v > 1}
        assert not duplicates, f"Duplicate open item IDs found: {duplicates}"

    def test_deferred_items_excluded_from_open_work_table(self, workspace):
        """COR-06: ## Deferred (gated) items must NOT appear in the Open Work table rows."""
        _run([PYTHON, str(_SYNC_INDEX), str(workspace["todo"])])
        text = workspace["todo"].read_text(encoding="utf-8")

        # Find the ## Open Work table (between ## Open Work and the next ## section)
        table_m = re.search(r"^## Open Work.*$", text, re.MULTILINE)
        assert table_m, "sync-todo-index did not produce a ## Open Work header"
        next_section = re.search(r"^## ", text[table_m.end():], re.MULTILINE)
        if next_section:
            table_body = text[table_m.end(): table_m.end() + next_section.start()]
        else:
            table_body = text[table_m.end():]

        # F903 is a gated item — it must not appear in the Open Work table rows
        assert "F903" not in table_body, \
            "Gated item F903 appears in the Open Work table (COR-06 violation)"

    def test_deferred_work_section_not_skipped(self, tmp_path):
        """FIX-03: a hypothetical '## Deferred Work' section is NOT excluded from the
        Open Work count — only '## Deferred (gated)' is excluded."""
        # Build a TODO.md with a '## Deferred Work' section that contains real items.
        # After sync, those items should appear in the Open Work count.
        todo_path = tmp_path / "TODO_deferred_work.md"
        todo_path.write_text("""\
# Test TODO

## Open Work — 0 items

| Section | Open | IDs |
|---|---|---|

## Testing

- [ ] **F801** Normal testing item. [easy] [testing]

## Deferred Work

- [ ] **F802** Item in Deferred Work (not gated). [easy] [infra]

## Deferred (gated)

- [ ] **F803** Actually gated item. [arch] [gated: condition]

""", encoding="utf-8")

        result = _run(
            [PYTHON, str(_SYNC_INDEX), str(todo_path)],
        )
        assert result.returncode == 0, f"sync-todo-index failed:\n{result.stderr}"

        text = todo_path.read_text(encoding="utf-8")
        table_count = _open_work_count(text)
        assert table_count is not None, "## Open Work header not found after sync"
        # F801 (Testing) + F802 (Deferred Work) must be counted; F803 (gated) must not
        assert table_count == 2, (
            f"Expected 2 open items (F801 + F802); got {table_count}. "
            f"'Deferred Work' items must NOT be excluded from Open Work count."
        )
        # F802 from Deferred Work must appear in the table
        table_m = re.search(r"^## Open Work.*$", text, re.MULTILINE)
        assert table_m, "## Open Work header not found"
        next_section = re.search(r"^## ", text[table_m.end():], re.MULTILINE)
        table_body = (
            text[table_m.end(): table_m.end() + next_section.start()]
            if next_section else text[table_m.end():]
        )
        assert "F802" in table_body, (
            "F802 from '## Deferred Work' does not appear in the Open Work table — "
            "SKIP_SECTION_RE is over-matching"
        )
        assert "F803" not in table_body, (
            "F803 (gated) must be excluded from the Open Work table"
        )


# ---------------------------------------------------------------------------
# Tests — End-to-end pipeline
# ---------------------------------------------------------------------------

class TestEndToEndPipeline:
    """Run the full pipeline: close-batch → archive-todo → sync-todo-index."""

    def test_full_pipeline(self, workspace, tmp_path):
        """Full pipeline: close an item, add new items (one gated), archive, sync."""
        # Step 1: close-batch — close F911, add F950 (testing), add F951 (gated arch)
        manifest = _make_manifest(tmp_path, """\
## Close

F911 resolved: end-to-end pipeline test

## New

- [ ] **F950** New testing item — e2e test. [testing]
- [ ] **F951** Gated new item — e2e. [gated: test condition] [arch]
""")
        # Need ## Architecture for the gated route (overridden to Deferred anyway)
        todo_text = workspace["todo"].read_text(encoding="utf-8")
        todo_text += "\n## Architecture\n\n"
        workspace["todo"].write_text(todo_text, encoding="utf-8")

        r1 = _run(
            [PYTHON, str(_CLOSE_BATCH), str(manifest),
             "--todo-path", str(workspace["todo"]),
             "--no-sync"],
        )
        assert r1.returncode == 0, f"close-batch failed:\n{r1.stderr}"

        # Step 2: archive-todo — moves F911 to archive
        r2 = _run(
            [PYTHON, str(_ARCHIVE_TODO),
             "--todo-path", str(workspace["todo"]),
             "--archive-path", str(workspace["archive"]),
             "--journal-path", str(workspace["journal"]),
             "--no-sync"],
        )
        assert r2.returncode == 0, f"archive-todo failed:\n{r2.stderr}"

        # Step 3: sync-todo-index — regenerate Open Work table
        r3 = _run(
            [PYTHON, str(_SYNC_INDEX), str(workspace["todo"])],
        )
        assert r3.returncode == 0, f"sync-todo-index failed:\n{r3.stderr}"

        todo_text = workspace["todo"].read_text(encoding="utf-8")
        archive_text = workspace["archive"].read_text(encoding="utf-8")

        # F911: archived, gone from TODO.md
        assert "**F911**" not in todo_text
        assert "**F911**" in archive_text

        # F950: in TODO.md under Testing
        assert _section_contains(todo_text, "Testing", "F950"), \
            "F950 not in Testing after pipeline"

        # F951: in TODO.md under Deferred (gated), not Architecture
        assert _section_contains(todo_text, "Deferred (gated)", "F951"), \
            "F951 not in Deferred (gated) after pipeline"
        assert not _section_contains(todo_text, "Architecture", "F951"), \
            "F951 wrongly in Architecture after pipeline"

        # Open Work count must match actual non-deferred open items
        table_count = _open_work_count(todo_text)
        deferred_pos = todo_text.find("## Deferred (gated)")
        non_deferred_text = todo_text[:deferred_pos] if deferred_pos != -1 else todo_text
        non_deferred_open = len(re.findall(
            r"^- \[ \] (?:<a id=\"[^\"]+\"></a> )?\*\*[A-Z]+\d+",
            non_deferred_text,
            re.MULTILINE,
        ))
        assert table_count == non_deferred_open, (
            f"Open Work table count ({table_count}) != actual open items ({non_deferred_open})"
        )
