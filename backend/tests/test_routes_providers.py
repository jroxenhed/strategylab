"""Tests for _persist_env in routes/providers.py — covers F53 atomic env writes."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

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
