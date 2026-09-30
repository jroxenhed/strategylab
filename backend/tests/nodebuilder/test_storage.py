"""Tests for nodebuilder/storage.py (F435 W1 item 1.B, decision D2).

Every test points STRATEGYLAB_DATA_DIR at a temporary folder (plan 8.4).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import fileutil
from fileutil import cleanup_orphan_tmps
from nodebuilder import storage
from nodebuilder.models import GraphValidationError
from nodebuilder.storage import (
    GraphNotFoundError,
    InvalidNameError,
    NameTakenError,
    RevConflictError,
    canonical_graph,
    get_store,
    graph_dirs,
    is_graph_id,
)


def _graph(period: int = 14) -> dict:
    """A small valid graph (old v1 form; the model migrates it)."""
    return {
        "_version": 1,
        "nodes": {
            "/t": {"id": "/t", "type": "ticker", "params": {"symbol": "AAPL", "interval": "1d"}},
            "/r": {"id": "/r", "type": "rsi", "params": {"period": period}, "position": [10, 20]},
        },
        "wires": [{"id": "w1", "from": "/t", "to": "/r"}],
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    return get_store()


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_store_uses_the_temp_data_dir(store, tmp_path):
    env = store.create("one", _graph())
    assert (tmp_path / "graphs" / f"{env['id']}.json").exists()
    assert graph_dirs() == [tmp_path / "graphs", tmp_path / "graph_library"]


def test_create_envelope_shape(store):
    env = store.create("  My RSI  ", _graph(), description="d")
    assert set(env) == {"id", "rev", "name", "description", "created_at", "updated_at", "graph"}
    assert is_graph_id(env["id"])
    assert env["id"].startswith("g_") and len(env["id"]) == 14
    assert env["rev"] == 1
    assert env["name"] == "My RSI"  # trimmed
    assert env["description"] == "d"
    assert env["created_at"].endswith("Z") and env["created_at"] == env["updated_at"]
    assert env["graph"] == canonical_graph(_graph())
    assert store.get(env["id"]) == env


def test_create_without_graph_stores_an_empty_graph(store):
    env = store.create("empty")
    assert env["graph"]["nodes"] == {}
    assert env["graph"]["wires"] == []


def test_ids_are_well_formed():
    assert is_graph_id("g_0123456789ab")
    for bad in ("g_0123456789AB", "g_0123456789a", "x_0123456789ab", "../etc/passwd", "", "g_01234567890g"):
        assert not is_graph_id(bad)


def test_get_rejects_path_like_ids(store):
    with pytest.raises(GraphNotFoundError):
        store.get("../bots")
    with pytest.raises(GraphNotFoundError):
        store.get("g_000000000000")


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, None, 5])
def test_invalid_names(store, name):
    with pytest.raises(InvalidNameError):
        store.create(name, _graph())


def test_name_of_80_chars_is_allowed(store):
    assert store.create("x" * 80, _graph())["name"] == "x" * 80


def test_names_are_unique_without_case(store):
    store.create("Alpha", _graph())
    with pytest.raises(NameTakenError):
        store.create("alpha", _graph())
    other = store.create("Beta", _graph())
    with pytest.raises(NameTakenError):
        store.update(other["id"], 1, _graph(), name="ALPHA")
    # Changing only the case of its own name is fine.
    env = store.update(other["id"], 1, _graph(), name="BETA")
    assert env["name"] == "BETA" and env["rev"] == 2


def test_graph_that_does_not_parse_is_rejected(store):
    bad = _graph()
    bad["wires"].append({"id": "w2", "from": "/r", "to": "/missing"})
    with pytest.raises(GraphValidationError):
        store.create("bad", bad)
    cyclic = _graph()
    cyclic["wires"].append({"id": "w2", "from": "/r", "to": "/t"})
    with pytest.raises(GraphValidationError):
        store.create("cyclic", cyclic)
    assert store.list() == []


def test_duplicate_of_copies_graph_and_description(store):
    src = store.create("src", _graph(21), description="from src")
    dup = store.create("copy", duplicate_of=src["id"])
    assert dup["id"] != src["id"] and dup["rev"] == 1
    assert dup["graph"] == src["graph"]
    assert dup["description"] == "from src"


def test_list_items(store):
    a = store.create("a", _graph())
    items = store.list()
    assert items == [{
        "id": a["id"], "rev": 1, "name": "a", "description": "",
        "updated_at": a["updated_at"], "node_count": 2, "groups": ["main"],
    }]


def test_list_skips_broken_files(store, tmp_path):
    store.create("ok", _graph())
    (tmp_path / "graphs" / "g_aaaaaaaaaaaa.json").write_text("{not json")
    (tmp_path / "graphs" / "g_bbbbbbbbbbbb.json").write_text('{"id": "g_cccccccccccc"}')
    assert [it["name"] for it in store.list()] == ["ok"]


# ---------------------------------------------------------------------------
# Writes and revs
# ---------------------------------------------------------------------------


def test_writes_go_through_atomic_write_text(store, monkeypatch):
    calls = []
    real = fileutil.atomic_write_text

    def spy(path, content, **kw):
        calls.append(Path(path).name)
        return real(path, content, **kw)

    monkeypatch.setattr(storage, "atomic_write_text", spy)
    env = store.create("a", _graph())
    store.update(env["id"], 1, _graph(7))
    assert calls == [f"{env['id']}.json"] * 2


def test_failed_write_keeps_old_file_and_leaves_no_tmp(store, tmp_path, monkeypatch):
    env = store.create("a", _graph())

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(fileutil.os, "replace", boom)
    with pytest.raises(OSError):
        store.update(env["id"], 1, _graph(7))
    monkeypatch.undo()

    on_disk = json.loads((tmp_path / "graphs" / f"{env['id']}.json").read_text())
    assert on_disk["rev"] == 1
    assert on_disk["graph"] == canonical_graph(_graph())
    assert list((tmp_path / "graphs").glob("*.tmp")) == []


def test_update_bumps_rev(store):
    env = store.create("a", _graph())
    env2 = store.update(env["id"], 1, _graph(20), description="new")
    assert env2["rev"] == 2
    assert env2["description"] == "new"
    assert env2["created_at"] == env["created_at"]
    assert store.get(env["id"])["graph"] == canonical_graph(_graph(20))


def test_stale_rev_conflicts(store):
    env = store.create("a", _graph())
    store.update(env["id"], 1, _graph(20))
    with pytest.raises(RevConflictError) as info:
        store.update(env["id"], 1, _graph(30))
    assert info.value.current_rev == 2
    assert store.get(env["id"])["graph"] == canonical_graph(_graph(20))


def test_update_missing_graph(store):
    with pytest.raises(GraphNotFoundError):
        store.update("g_000000000000", 1, _graph())


def test_two_concurrent_updates_serialize(store, monkeypatch):
    """Both callers hold rev 1. The first to take the lock wins; the other must
    see the new rev and get a conflict, never overwrite the winner."""
    env = store.create("a", _graph())
    real = storage.atomic_write_text

    def slow_write(path, content, **kw):
        time.sleep(0.2)  # hold the lock long enough for the other thread to wait on it
        return real(path, content, **kw)

    monkeypatch.setattr(storage, "atomic_write_text", slow_write)
    start = threading.Barrier(2)
    results: dict[int, object] = {}

    def save(period: int) -> None:
        start.wait()
        try:
            results[period] = store.update(env["id"], 1, _graph(period))
        except RevConflictError as exc:
            results[period] = exc

    threads = [threading.Thread(target=save, args=(p,)) for p in (21, 22)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = [p for p, r in results.items() if isinstance(r, dict)]
    losses = [r for r in results.values() if isinstance(r, RevConflictError)]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0].current_rev == 2
    stored = store.get(env["id"])
    assert stored["rev"] == 2
    assert stored["graph"] == canonical_graph(_graph(wins[0]))


def test_concurrent_creates_cannot_share_a_name(store, monkeypatch):
    real = storage.atomic_write_text

    def slow_write(path, content, **kw):
        time.sleep(0.1)
        return real(path, content, **kw)

    monkeypatch.setattr(storage, "atomic_write_text", slow_write)
    start = threading.Barrier(2)
    outcomes: list[str] = []

    def create(name: str) -> None:
        start.wait()
        try:
            store.create(name, _graph())
            outcomes.append("ok")
        except NameTakenError:
            outcomes.append("taken")

    threads = [threading.Thread(target=create, args=(n,)) for n in ("Same", "same")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "taken"]
    assert len(store.list()) == 1


def test_delete(store, tmp_path):
    env = store.create("a", _graph())
    store.update(env["id"], 1, _graph(3))  # makes a .bak
    with pytest.raises(RevConflictError) as info:
        store.delete(env["id"], 1)
    assert info.value.current_rev == 2
    store.delete(env["id"], 2)
    assert list((tmp_path / "graphs").iterdir()) == []
    with pytest.raises(GraphNotFoundError):
        store.get(env["id"])
    with pytest.raises(GraphNotFoundError):
        store.delete(env["id"], 2)


# ---------------------------------------------------------------------------
# Orphan temp files
# ---------------------------------------------------------------------------


def test_orphan_tmps_in_graph_folders_are_cleaned(tmp_path):
    old = time.time() - 2 * 3600
    paths = []
    for folder in graph_dirs(tmp_path):
        folder.mkdir(parents=True)
        tmp = folder / "tmpabc.tmp"
        tmp.write_text("partial")
        os.utime(tmp, (old, old))
        paths.append(tmp)
    fresh = tmp_path / "graphs" / "fresh.tmp"
    fresh.write_text("in flight")
    keep = tmp_path / "graphs" / "g_aaaaaaaaaaaa.json"
    keep.write_text("{}")

    assert cleanup_orphan_tmps([tmp_path, *graph_dirs(tmp_path)]) == 2
    assert not any(p.exists() for p in paths)
    assert fresh.exists() and keep.exists()


def test_startup_cleanup_includes_graph_folders():
    """main.py's lifespan must pass the graph folders to cleanup_orphan_tmps
    (it does not look in sub-folders). Checked on the source so the test does
    not start the app."""
    src = Path(_BACKEND_DIR, "main.py").read_text()
    assert "cleanup_orphan_tmps([DATA_DIR, *graph_dirs(DATA_DIR), backend_dir])" in src


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def test_seed_object_shape(store):
    out = store.seed({"first": _graph(10), "second": _graph(11)})
    assert len(out["imported"]) == 2 and out["skipped"] == []
    by_name = {it["name"]: it for it in store.list()}
    assert set(by_name) == {"first", "second"}
    assert store.get(by_name["first"]["id"])["graph"] == canonical_graph(_graph(10))


def test_seed_array_shape(store):
    out = store.seed([{"name": "a", "graph": _graph(10)}, {"name": "b", "graph": _graph(11)}])
    assert len(out["imported"]) == 2
    assert {it["name"] for it in store.list()} == {"a", "b"}


def test_seed_accepts_the_raw_localstorage_string(store):
    out = store.seed(json.dumps({"a": _graph()}))
    assert len(out["imported"]) == 1


@pytest.mark.parametrize("garbage", [None, 42, "not json", "[1, 2]", [1, "x", None], {"a": 5}, True])
def test_seed_garbage_imports_nothing(store, garbage):
    out = store.seed(garbage)
    assert out == {"imported": [], "skipped": []}
    assert store.list() == []


def test_seed_skips_entries_that_do_not_parse(store):
    bad = _graph()
    bad["wires"].append({"id": "w2", "from": "/r", "to": "/t"})  # a cycle
    out = store.seed({"good": _graph(), "bad": bad, "worse": {"nodes": "nope"}})
    assert len(out["imported"]) == 1
    assert sorted(out["skipped"], key=lambda s: s["name"]) == [
        {"name": "bad", "reason": "invalid"},
        {"name": "worse", "reason": "invalid"},
    ]


def test_seed_twice_imports_nothing_new(store):
    legacy = {"a": _graph(10), "b": _graph(11)}
    first = store.seed(legacy)
    assert len(first["imported"]) == 2
    second = store.seed(legacy)
    assert second["imported"] == []
    assert sorted(s["name"] for s in second["skipped"]) == ["a", "b"]
    assert all(s["reason"] == "duplicate" for s in second["skipped"])
    assert len(store.list()) == 2


def test_seed_name_clash_gets_imported_suffix_and_stays_idempotent(store):
    store.create("mine", _graph(99))
    out = store.seed({"MINE": _graph(10)})
    assert len(out["imported"]) == 1
    assert store.get(out["imported"][0])["name"] == "MINE (imported)"
    again = store.seed({"MINE": _graph(10)})
    assert again == {"imported": [], "skipped": [{"name": "MINE", "reason": "duplicate"}]}
    assert len(store.list()) == 2


def test_seed_long_and_empty_names_stay_valid(store):
    out = store.seed([{"name": "x" * 200, "graph": _graph()}, {"name": "  ", "graph": _graph(3)}])
    names = sorted(store.get(i)["name"] for i in out["imported"])
    assert all(1 <= len(n) <= 80 for n in names)
    assert "Imported graph" in names


# ---------------------------------------------------------------------------
# W1 review fixes: port order (DI-01), damaged files (DI-03), lock map (DI-07)
# ---------------------------------------------------------------------------


def test_wires_are_stored_in_port_order(store, tmp_path):
    """DI-01: list order equals port order on disk, so Wave 0 code (which
    reads list order) computes the same comparison after a rollback."""
    graph = {
        "_version": 2,
        "nodes": {
            "/t": {"id": "/t", "type": "ticker"},
            "/r": {"id": "/r", "type": "rsi"},
            "/s": {"id": "/s", "type": "sma"},
            "/c": {"id": "/c", "type": "above"},
        },
        "wires": [
            {"id": "w1", "from": "/t", "to": "/r", "to_port": "in0"},
            {"id": "w2", "from": "/t", "to": "/s", "to_port": "in0"},
            {"id": "right", "from": "/s", "to": "/c", "to_port": "in1"},
            {"id": "left", "from": "/r", "to": "/c", "to_port": "in0"},
        ],
    }
    env = store.create("ported", graph)
    on_disk = json.loads((tmp_path / "graphs" / f"{env['id']}.json").read_text())
    assert [w["id"] for w in on_disk["graph"]["wires"]] == ["w1", "w2", "left", "right"]
    assert canonical_graph(on_disk["graph"]) == on_disk["graph"]  # stable


def _corrupt(tmp_path, graph_id: str, text: str) -> None:
    (tmp_path / "graphs" / f"{graph_id}.json").write_text(text)


@pytest.mark.parametrize("text", [
    '{"id": "trunc',
    "[1, 2]",
    '{"id": "WRONG", "rev": 1, "name": "x", "graph": {}}',
    '{"id": "@ID", "name": "x", "graph": {}}',
    '{"id": "@ID", "rev": "1", "name": "x", "graph": {}}',
    '{"id": "@ID", "rev": 1, "graph": {}}',
    '{"id": "@ID", "rev": 1, "name": "x"}',
])
def test_damaged_file_is_graph_corrupt_everywhere(store, tmp_path, text):
    from nodebuilder.storage import GraphCorruptError

    good = store.create("good", _graph())
    bad = store.create("bad", _graph())
    _corrupt(tmp_path, bad["id"], text.replace("@ID", bad["id"]))
    with pytest.raises(GraphCorruptError):
        store.get(bad["id"])
    with pytest.raises(GraphCorruptError):
        store.update(bad["id"], 1, _graph())
    with pytest.raises(GraphCorruptError):
        store.create("copy", duplicate_of=bad["id"])
    # The list skips it and still shows the good one.
    assert [it["id"] for it in store.list()] == [good["id"]]
    # Delete needs no rev: the file is moved aside, not destroyed.
    store.delete(bad["id"], 999)
    graphs = tmp_path / "graphs"
    assert not (graphs / f"{bad['id']}.json").exists()
    assert (graphs / f"{bad['id']}.json.corrupt").exists()
    with pytest.raises(GraphNotFoundError):
        store.get(bad["id"])


def test_duplicate_of_a_stored_graph_that_no_longer_loads_is_corrupt(store, tmp_path):
    from nodebuilder.storage import GraphCorruptError

    env = store.create("old", _graph())
    path = tmp_path / "graphs" / f"{env['id']}.json"
    data = json.loads(path.read_text())
    data["graph"]["_version"] = 99
    path.write_text(json.dumps(data))
    with pytest.raises(GraphCorruptError):
        store.create("copy", duplicate_of=env["id"])


def test_lock_map_does_not_grow_for_unknown_or_deleted_ids(store):
    """DI-07: a malformed id is a 404 before it gets a lock; a missing or
    deleted graph drops its lock."""
    for bad_id in ("nope", "../../etc", "g_zzzzzzzzzzzz", "g_000000000000"):
        with pytest.raises(GraphNotFoundError):
            store.update(bad_id, 1, _graph())
        with pytest.raises(GraphNotFoundError):
            store.delete(bad_id, 1)
    assert store._locks == {}
    env = store.create("gone", _graph())
    store.update(env["id"], 1, _graph())
    assert env["id"] in store._locks
    store.delete(env["id"], 2)
    assert store._locks == {}
