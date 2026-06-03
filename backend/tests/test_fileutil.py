"""Tests for fileutil.atomic_write_text (F71 + F74 + F83 + F285)."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import os
import shutil
import pytest
from fileutil import atomic_write_text


def test_atomic_write_text_writes_content(tmp_path):
    """Happy path: file is created with the expected content."""
    target = tmp_path / "out.json"
    atomic_write_text(target, '{"hello": "world"}')
    assert target.exists()
    assert target.read_text() == '{"hello": "world"}'


def test_atomic_write_text_replaces_existing(tmp_path):
    """Pre-existing file is fully replaced with new content."""
    target = tmp_path / "data.json"
    target.write_text("old content")
    atomic_write_text(target, "new content")
    assert target.read_text() == "new content"


def test_atomic_write_text_cleans_tmp_on_failure(tmp_path, monkeypatch):
    """If os.replace raises, the .tmp file is removed and no debris is left."""
    target = tmp_path / "data.json"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "content")

    # No .tmp files should remain in tmp_path
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"Unexpected temp files: {leftover}"


def test_atomic_write_text_fsync_called(tmp_path, monkeypatch):
    """os.fsync() is called with the temp fd's fileno before os.replace."""
    target = tmp_path / "data.json"
    synced_fds = []

    real_fsync = os.fsync

    def recording_fsync(fd):
        synced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    atomic_write_text(target, "durable content")

    # At least one fsync call must have been made
    assert len(synced_fds) >= 1


def test_atomic_write_text_handles_close_failure(tmp_path, monkeypatch):
    """fd.close() raising on the happy write path does NOT abort the rename.

    Design (F83): the inner `try: fd.close() except Exception: pass` intentionally
    swallows close() failures.  The data is already fsync'd at that point, and
    os.replace still works on an open fd on POSIX.  So the function succeeds
    and the target file contains the expected content even if close() raises.
    No .tmp debris should be left behind.
    """
    import tempfile as _tempfile

    target = tmp_path / "data.json"

    original_ntf = _tempfile.NamedTemporaryFile

    class FaultyFileWrapper:
        """Wraps a real NamedTemporaryFile but makes close() raise once."""

        def __init__(self, *args, **kwargs):
            self._inner = original_ntf(*args, **kwargs)
            self._close_count = 0

        def write(self, data):
            return self._inner.write(data)

        def flush(self):
            return self._inner.flush()

        def fileno(self):
            return self._inner.fileno()

        @property
        def name(self):
            return self._inner.name

        def close(self):
            self._close_count += 1
            if self._close_count == 1:
                raise OSError("simulated close failure")
            # Subsequent calls (from except cleanup) succeed silently.
            try:
                self._inner.close()
            except Exception:
                pass

    monkeypatch.setattr(_tempfile, "NamedTemporaryFile", FaultyFileWrapper)

    # close() raises, but atomic_write_text swallows it (F83 design).
    # os.replace still runs; function returns normally.
    atomic_write_text(target, "test content")

    # Target file must exist with correct content.
    assert target.exists()
    assert target.read_text() == "test content"

    # No .tmp debris left behind.
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == [], f"Unexpected temp files: {leftover}"


# ---------------------------------------------------------------------------
# F285 — rotating backup tests
# ---------------------------------------------------------------------------


def test_backup_depth1_created_on_second_write(tmp_path):
    """After a second write, <path>.bak contains the FIRST write's content."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "first", backup_depth=1)
    # No .bak yet (no prior file before the first write).
    assert not (tmp_path / "data.json.bak").exists()

    atomic_write_text(target, "second", backup_depth=1)
    assert target.read_text() == "second"
    assert (tmp_path / "data.json.bak").read_text() == "first"


def test_backup_depth1_overwrites_previous_bak(tmp_path):
    """With depth=1 only one backup slot exists; it's overwritten each call."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "v1", backup_depth=1)
    atomic_write_text(target, "v2", backup_depth=1)
    atomic_write_text(target, "v3", backup_depth=1)

    assert target.read_text() == "v3"
    assert (tmp_path / "data.json.bak").read_text() == "v2"
    # No .bak.2 at depth 1.
    assert not (tmp_path / "data.json.bak.2").exists()


def test_backup_depth2_rotation_order(tmp_path):
    """Depth 2 keeps .bak (prev-1) and .bak.2 (prev-2) in correct order."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "v1", backup_depth=2)  # no prior file → no bak
    atomic_write_text(target, "v2", backup_depth=2)  # v1 → .bak
    atomic_write_text(target, "v3", backup_depth=2)  # v2 → .bak, old .bak (v1) → .bak.2

    assert target.read_text() == "v3"
    assert (tmp_path / "data.json.bak").read_text() == "v2"
    assert (tmp_path / "data.json.bak.2").read_text() == "v1"

    atomic_write_text(target, "v4", backup_depth=2)  # v3 → .bak, v2 → .bak.2 (v1 evicted)
    assert target.read_text() == "v4"
    assert (tmp_path / "data.json.bak").read_text() == "v3"
    assert (tmp_path / "data.json.bak.2").read_text() == "v2"


def test_backup_depth0_creates_nothing(tmp_path):
    """depth=0 disables backups entirely."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "v1", backup_depth=0)
    atomic_write_text(target, "v2", backup_depth=0)

    assert target.read_text() == "v2"
    bak_files = list(tmp_path.glob("*.bak*"))
    assert bak_files == [], f"Unexpected backup files: {bak_files}"


def test_backup_failure_does_not_abort_write(tmp_path, monkeypatch):
    """If shutil.copy2 raises, the primary write still completes successfully."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "initial", backup_depth=1)

    def exploding_copy2(src, dst):
        raise OSError("simulated disk full")

    # COR-04: patch at fileutil's own module reference so the interception works
    # regardless of whether fileutil uses 'import shutil; shutil.copy2' or
    # 'from shutil import copy2'.
    import fileutil as _fileutil
    monkeypatch.setattr(_fileutil.shutil, "copy2", exploding_copy2)

    # Should NOT raise — backup failure is non-fatal.
    atomic_write_text(target, "updated", backup_depth=1)

    assert target.read_text() == "updated"
    # No .bak because copy2 was blocked.
    assert not (tmp_path / "data.json.bak").exists()


def test_backup_default_is_depth1(tmp_path):
    """Callers that pass no backup_depth argument get depth=1 behaviour."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "original")
    atomic_write_text(target, "replacement")

    assert target.read_text() == "replacement"
    assert (tmp_path / "data.json.bak").read_text() == "original"


def test_no_backup_on_first_write(tmp_path):
    """On the very first write (no prior file) no .bak is created."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "first", backup_depth=1)

    assert target.read_text() == "first"
    assert not (tmp_path / "data.json.bak").exists()


def test_backup_depth_negative_raises(tmp_path):
    """K2: backup_depth < 0 must raise ValueError, not silently skip backups."""
    target = tmp_path / "data.json"
    with pytest.raises(ValueError, match="backup_depth must be >= 0"):
        atomic_write_text(target, "content", backup_depth=-1)


def test_backup_depth3_rotation_order(tmp_path):
    """K7: depth=3 keeps .bak, .bak.2, .bak.3; oldest evicted on 4th write."""
    target = tmp_path / "data.json"
    atomic_write_text(target, "v1", backup_depth=3)  # no prior file
    atomic_write_text(target, "v2", backup_depth=3)  # v1 → .bak
    atomic_write_text(target, "v3", backup_depth=3)  # v2 → .bak, v1 → .bak.2
    atomic_write_text(target, "v4", backup_depth=3)  # v3 → .bak, v2 → .bak.2, v1 → .bak.3

    assert target.read_text() == "v4"
    assert (tmp_path / "data.json.bak").read_text() == "v3"
    assert (tmp_path / "data.json.bak.2").read_text() == "v2"
    assert (tmp_path / "data.json.bak.3").read_text() == "v1"

    # A 5th write evicts v1 (.bak.3 is dropped).
    atomic_write_text(target, "v5", backup_depth=3)
    assert target.read_text() == "v5"
    assert (tmp_path / "data.json.bak").read_text() == "v4"
    assert (tmp_path / "data.json.bak.2").read_text() == "v3"
    assert (tmp_path / "data.json.bak.3").read_text() == "v2"
    assert not (tmp_path / "data.json.bak.4").exists()


def test_bak_file_mode_restricted(tmp_path):
    """DI-01 regression: .bak created by atomic_write_text respects shutil.copy2 mode.

    Callers that care about mode (e.g. providers.py for .env.bak) must chmod
    the .bak themselves; this test documents the raw behavior so we detect any
    accidental change.
    """
    target = tmp_path / "secret.env"
    target.write_text("KEY=value")
    # Give the source a 0o600 mode to simulate a pre-existing secured .env.
    os.chmod(str(target), 0o600)

    atomic_write_text(target, "KEY=newvalue", backup_depth=1)
    bak = tmp_path / "secret.env.bak"
    assert bak.exists()
    # shutil.copy2 copies the source mode, so .bak should be 0o600 when source is.
    bak_mode = oct(os.stat(str(bak)).st_mode & 0o777)
    assert bak_mode == oct(0o600), f"Expected 0o600, got {bak_mode}"
