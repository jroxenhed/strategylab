"""Tests for _persist_env in routes/providers.py — covers F53 atomic env writes."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pathlib
import pytest
from routes import providers as providers_mod
from routes.providers import _persist_env


def test_persist_env_creates_new_file(tmp_path):
    """When no .env exists, _persist_env creates it with the new key=value."""
    env_file = tmp_path / ".env"
    _persist_env("FOO", "bar", env_path=env_file)
    assert env_file.exists()
    content = env_file.read_text()
    assert "FOO=bar" in content


def test_persist_env_updates_existing_key(tmp_path):
    """Existing key is updated in-place; order of other keys is preserved."""
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=old\nBAR=baz\n")
    _persist_env("FOO", "new", env_path=env_file)
    assert env_file.read_text() == "FOO=new\nBAR=baz\n"


def test_persist_env_appends_new_key(tmp_path):
    """New key is appended after existing content."""
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\n")
    _persist_env("BAZ", "qux", env_path=env_file)
    assert env_file.read_text() == "FOO=bar\nBAZ=qux\n"


def test_persist_env_cleanup_on_replace_failure(tmp_path, monkeypatch):
    """When os.replace raises, no .tmp file remains and original .env is unchanged."""
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\n")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(providers_mod.os, "replace", _boom)

    with pytest.raises(OSError, match="disk full"):
        _persist_env("BAZ", "qux", env_path=env_file)

    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Leftover .tmp files: {tmp_files}"

    # Original content must be preserved
    assert env_file.read_text() == "FOO=bar\n"


def test_persist_env_concurrent_writes_dont_clobber(tmp_path):
    """F70: concurrent _persist_env calls for *different* keys must not lose updates.

    Pre-F70 both threads could read the original file before either wrote, then
    each os.replace would clobber the other's update. The module-level lock
    serializes the read-modify-write block so both keys end up persisted.

    Loop the race N times so a GIL-lucky serialization can't false-green. With
    no lock, empirical measurements showed ~1% per-iteration false-pass rate;
    50 iterations drives that to ~0.6 ppm.
    """
    import threading

    barrier = threading.Barrier(2)

    def writer(env_file, key, value):
        barrier.wait()  # release both threads at the same instant
        _persist_env(key, value, env_path=env_file)

    for i in range(50):
        env_file = tmp_path / f".env_iter_{i}"
        env_file.write_text("EXISTING=keep\n")
        barrier.reset()

        t1 = threading.Thread(target=writer, args=(env_file, "KEY_A", "1"))
        t2 = threading.Thread(target=writer, args=(env_file, "KEY_B", "2"))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not t1.is_alive() and not t2.is_alive(), f"hung writer thread on iter {i}"

        content = env_file.read_text()
        assert "EXISTING=keep" in content, f"iter {i}: {content!r}"
        assert "KEY_A=1" in content, f"iter {i} KEY_A lost: {content!r}"
        assert "KEY_B=2" in content, f"iter {i} KEY_B lost: {content!r}"


def test_persist_env_handles_disappearing_file_toctou(tmp_path, monkeypatch):
    """F76: .env disappearing between exists() and read_text() must not crash.

    Pre-F76 _persist_env did `if env_path.exists(): lines = env_path.read_text()`,
    so a concurrent unlink between the two calls produced an unhandled
    FileNotFoundError. We simulate that race by stubbing read_text to raise it
    even though the file was created above.

    Note on contract: when the file disappears mid-read, _persist_env treats it
    as a fresh file — pre-existing keys are *not* preserved. This is the
    intentional trade-off (don't crash the request). The assertion below
    documents that behavior so a future "preserve original on race" rewrite
    has to consciously break this test.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=v\n")

    real_read_text = pathlib.Path.read_text
    raised = {"once": False}

    def flaky_read_text(self, *a, **k):
        if not raised["once"] and self == env_file:
            raised["once"] = True
            raise FileNotFoundError(env_file)
        return real_read_text(self, *a, **k)

    # Scope the patch via providers_mod so a parallel pytest worker reading some
    # other Path can't accidentally trip flaky_read_text.
    monkeypatch.setattr(providers_mod.pathlib.Path, "read_text", flaky_read_text)
    _persist_env("NEW_KEY", "v", env_path=env_file)

    assert raised["once"], "the simulated TOCTOU race didn't fire"
    final = env_file.read_text()
    assert "NEW_KEY=v" in final
    # Documents the trade-off: we treat the disappeared file as fresh, so the
    # original key is dropped rather than preserved. Crash-safety wins over
    # data-preservation for this rare race.
    assert "EXISTING=v" not in final
