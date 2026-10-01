"""F435 W2 MD-02: the server always hands out graphs in the current version.

A graph file written by Wave 1 holds a v2 graph (no write names, wire attr
labels).  GET must return it migrated, with every write param set, so the
editor never edits an un-migrated graph.  The file on disk changes only on
the next save.  Uses a temporary STRATEGYLAB_DATA_DIR.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nodebuilder.kernel import registry
from nodebuilder.migrate import CURRENT_GRAPH_VERSION
from nodebuilder.storage import get_store
from routes.graphs import router

_HERE = Path(os.path.dirname(__file__))
_CORPUS = json.loads((_HERE / "fixtures" / "v2_autorender_corpus.json").read_text())["cases"]


def _v2_case() -> dict:
    """A Wave 1 (v2) auto_render graph that compiles, with wire labels."""
    for case in _CORPUS:
        g = case["graph"]
        if case.get("compiles") and g.get("_version") == 2 and any(w.get("attr") for w in g["wires"]):
            return g
    raise AssertionError("corpus has no v2 graph with wire labels")


def _write_v2_file(store, graph: dict) -> tuple[str, Path]:
    env = store.create("w1 graph", {"_version": 3, "nodes": {}, "wires": []})
    path = store.graphs_dir / f"{env['id']}.json"
    data = json.loads(path.read_text())
    data["graph"] = graph  # what Wave 1 wrote: stored verbatim, still v2
    path.write_text(json.dumps(data, indent=2))
    return env["id"], path


def _assert_v3_with_write_names(graph: dict) -> None:
    import nodebuilder.trading  # noqa: F401  (registers node types)

    assert graph["_version"] == CURRENT_GRAPH_VERSION
    checked = 0
    for node in graph["nodes"].values():
        nt = registry.get(node["type"])
        if nt is None:
            continue
        for spec in nt.write_params():
            assert node["params"].get(spec.name), (node["id"], spec.name)
            checked += 1
    assert checked > 0
    assert all("attr" not in w or w["attr"] is None for w in graph["wires"])


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    return get_store()


def test_store_get_returns_a_stored_v2_graph_as_v3(store):
    gid, path = _write_v2_file(store, _v2_case())
    before = path.read_bytes()
    env = store.get(gid)
    _assert_v3_with_write_names(env["graph"])
    assert path.read_bytes() == before, "GET must not rewrite the file"


def test_route_get_returns_v3_and_save_rewrites_the_file(store, tmp_path):
    gid, path = _write_v2_file(store, _v2_case())
    before = path.read_bytes()
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    res = client.get(f"/api/graphs/{gid}")
    assert res.status_code == 200
    body = res.json()
    _assert_v3_with_write_names(body["graph"])
    assert path.read_bytes() == before

    # The list still shows it (and reads nothing it would have to migrate).
    listed = client.get("/api/graphs").json()["graphs"]
    assert [it["id"] for it in listed] == [gid]

    # Saving what GET gave back stores v3.
    res = client.put(f"/api/graphs/{gid}", json={"rev": body["rev"], "graph": body["graph"]})
    assert res.status_code == 200, res.text
    stored = json.loads(path.read_text())["graph"]
    assert stored["_version"] == CURRENT_GRAPH_VERSION


def test_get_of_a_graph_that_does_not_load_returns_it_as_stored(store):
    newer = {"_version": CURRENT_GRAPH_VERSION + 1, "nodes": {}, "wires": []}
    gid, _path = _write_v2_file(store, newer)
    assert store.get(gid)["graph"] == newer
