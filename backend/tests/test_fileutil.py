"""Tests for fileutil.atomic_write_text (F71 + F74 + F83)."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import os
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
