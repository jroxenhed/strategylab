"""HTTP tests for /api/graphs (F435 W1 item 1.B, decision D2, plan section 4.4).

Every test points STRATEGYLAB_DATA_DIR at a temporary folder (plan 8.4). The
app here holds only the graphs router, so the global body-size middleware in
main.py is not in the way of the route's own 2 MB check.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from nodebuilder import storage
from nodebuilder.storage import canonical_graph
from routes.graphs import MAX_BODY_BYTES, router

URL = "/api/graphs"


def _graph(period: int = 14) -> dict:
    return {
        "_version": 1,
        "nodes": {
            "/t": {"id": "/t", "type": "ticker", "params": {"symbol": "AAPL", "interval": "1d"}},
            "/r": {"id": "/r", "type": "rsi", "params": {"period": period}, "position": [10, 20]},
        },
        "wires": [{"id": "w1", "from": "/t", "to": "/r"}],
    }


def _norm(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _create(client, name="g", graph=None, **extra) -> dict:
    body = {"name": name, **extra}
    if graph is not None:
        body["graph"] = graph
    r = client.post(URL, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _assert_error_shape(body: dict, code: str) -> None:
    assert set(body) == {"detail", "node_id", "code", "diagnostics"}
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["code"] == code
    # Real diagnostics (plan 4.4): the full list, not a one-entry placeholder.
    # Every entry has the 4.2 shape; the first-error fields name one of them.
    assert isinstance(body["diagnostics"], list) and body["diagnostics"]
    for diag in body["diagnostics"]:
        assert set(diag) == {"node_id", "path", "severity", "code", "message", "param",
                             "port", "line", "col", "end_line", "end_col"}
    assert any(
        d["severity"] == "error" and d["code"] == code and d["node_id"] == body["node_id"]
        for d in body["diagnostics"]
    )


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_create_put_twice_get_round_trips(client):
    env = _create(client, "rt", _graph())
    assert env["rev"] == 1

    # The client edits what the server sent back and saves it twice.
    g1 = json.loads(json.dumps(env["graph"]))
    g1["nodes"]["/r"]["params"]["period"] = 21
    r = client.put(f"{URL}/{env['id']}", json={"rev": 1, "graph": g1})
    assert r.status_code == 200 and r.json()["rev"] == 2

    g2 = json.loads(json.dumps(r.json()["graph"]))
    g2["nodes"]["/r"]["position"] = [55.0, 66.0]
    r = client.put(f"{URL}/{env['id']}", json={"rev": 2, "graph": g2, "description": "two"})
    assert r.status_code == 200
    saved = r.json()
    assert saved["rev"] == 3 and saved["description"] == "two"

    got = client.get(f"{URL}/{env['id']}")
    assert got.status_code == 200
    assert _norm(got.json()) == _norm(saved)
    assert _norm(got.json()["graph"]) == _norm(g2)


def test_old_graph_comes_back_in_stored_form(client):
    env = _create(client, "old", _graph())
    assert _norm(env["graph"]) == _norm(canonical_graph(_graph()))


def test_create_envelope_and_list(client):
    env = _create(client, "Alpha", _graph(), description="desc")
    assert set(env) == {"id", "rev", "name", "description", "created_at", "updated_at", "graph"}
    assert env["id"].startswith("g_") and len(env["id"]) == 14
    r = client.get(URL)
    assert r.status_code == 200
    assert r.json() == {"graphs": [{
        "id": env["id"], "rev": 1, "name": "Alpha", "description": "desc",
        "updated_at": env["updated_at"], "node_count": 2, "groups": ["main"],
    }]}


def test_create_without_graph(client):
    env = _create(client, "empty")
    assert env["graph"]["nodes"] == {}


def test_duplicate_of(client):
    src = _create(client, "src", _graph(30), description="s")
    dup = _create(client, "src copy", duplicate_of=src["id"])
    assert dup["id"] != src["id"] and dup["graph"] == src["graph"] and dup["description"] == "s"
    r = client.post(URL, json={"name": "x", "duplicate_of": "g_000000000000"})
    assert r.status_code == 404
    r = client.post(URL, json={"name": "y", "duplicate_of": src["id"], "graph": _graph()})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_get_missing_and_bad_ids(client):
    assert client.get(f"{URL}/g_000000000000").status_code == 404
    assert client.get(f"{URL}/not-an-id").status_code == 404
    assert client.put(f"{URL}/g_000000000000", json={"rev": 1, "graph": _graph()}).status_code == 404


def test_name_taken(client):
    _create(client, "Taken", _graph())
    r = client.post(URL, json={"name": "taken", "graph": _graph()})
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "name_taken"}}

    other = _create(client, "Other", _graph())
    r = client.put(f"{URL}/{other['id']}", json={"rev": 1, "name": "TAKEN", "graph": _graph()})
    assert r.status_code == 409 and r.json() == {"detail": {"code": "name_taken"}}
    # The failed rename did not bump the rev.
    assert client.get(f"{URL}/{other['id']}").json()["rev"] == 1


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_bad_names_are_422(client, name):
    r = client.post(URL, json={"name": name, "graph": _graph()})
    assert r.status_code == 422


def test_rename_through_put(client):
    env = _create(client, "before", _graph())
    r = client.put(f"{URL}/{env['id']}", json={"rev": 1, "name": "after", "graph": env["graph"]})
    assert r.status_code == 200 and r.json()["name"] == "after"


def test_stale_rev_is_409(client):
    env = _create(client, "a", _graph())
    assert client.put(f"{URL}/{env['id']}", json={"rev": 1, "graph": _graph(20)}).status_code == 200
    r = client.put(f"{URL}/{env['id']}", json={"rev": 1, "graph": _graph(30)})
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "rev_conflict", "current_rev": 2}}


def test_concurrent_puts_serialize(client, monkeypatch):
    env = _create(client, "a", _graph())
    real = storage.atomic_write_text

    def slow_write(path, content, **kw):
        time.sleep(0.2)
        return real(path, content, **kw)

    monkeypatch.setattr(storage, "atomic_write_text", slow_write)
    start = threading.Barrier(2)
    codes: list[int] = []

    def put(period: int) -> None:
        start.wait()
        r = client.put(f"{URL}/{env['id']}", json={"rev": 1, "graph": _graph(period)})
        codes.append(r.status_code)

    threads = [threading.Thread(target=put, args=(p,)) for p in (21, 22)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409]
    assert client.get(f"{URL}/{env['id']}").json()["rev"] == 2


def test_cyclic_graph_is_400_with_diagnostics(client):
    bad = _graph()
    bad["wires"].append({"id": "w2", "from": "/r", "to": "/t"})
    r = client.post(URL, json={"name": "cyc", "graph": bad})
    assert r.status_code == 400
    _assert_error_shape(r.json(), "cycle")
    assert r.json()["node_id"] in {"/t", "/r"}
    assert client.get(URL).json() == {"graphs": []}


def test_dangling_wire_on_put_is_400(client):
    env = _create(client, "a", _graph())
    bad = _graph()
    bad["wires"].append({"id": "w2", "from": "/r", "to": "/nowhere"})
    r = client.put(f"{URL}/{env['id']}", json={"rev": 1, "graph": bad})
    assert r.status_code == 400
    _assert_error_shape(r.json(), "dangling_wire")
    assert r.json()["node_id"] == "/r"
    assert client.get(f"{URL}/{env['id']}").json()["rev"] == 1


def test_badly_typed_graph_is_400(client):
    r = client.post(URL, json={"name": "a", "graph": {"nodes": {"/x": {"id": "/y", "type": "rsi"}}}})
    assert r.status_code == 400
    _assert_error_shape(r.json(), "graph_invalid")


def test_bad_body_shapes_are_422(client):
    assert client.post(URL, json={"graph": _graph()}).status_code == 422  # no name
    assert client.post(URL, json={"name": "a", "graph": [1]}).status_code == 422
    assert client.post(URL, json={"name": "a", "extra": 1}).status_code == 422
    assert client.post(URL, content=b"{nope", headers={"content-type": "application/json"}).status_code == 422
    env = _create(client, "b", _graph())
    assert client.put(f"{URL}/{env['id']}", json={"graph": _graph()}).status_code == 422  # no rev


def test_body_over_2mb_is_413(client):
    big = _graph()
    big["nodes"]["/r"]["params"]["blob"] = "x" * (MAX_BODY_BYTES + 10)
    r = client.post(URL, json={"name": "big", "graph": big})
    assert r.status_code == 413
    assert client.get(URL).json() == {"graphs": []}


def test_body_just_under_2mb_is_accepted(client):
    g = _graph()
    g["nodes"]["/r"]["params"]["blob"] = "x" * (MAX_BODY_BYTES - 2000)
    r = client.post(URL, json={"name": "big", "graph": g})
    assert r.status_code == 201


def test_delete(client):
    env = _create(client, "a", _graph())
    client.put(f"{URL}/{env['id']}", json={"rev": 1, "graph": _graph(3)})
    r = client.delete(f"{URL}/{env['id']}", params={"rev": 1})
    assert r.status_code == 409 and r.json() == {"detail": {"code": "rev_conflict", "current_rev": 2}}
    r = client.delete(f"{URL}/{env['id']}", params={"rev": 2})
    assert r.status_code == 204 and r.content == b""
    assert client.get(f"{URL}/{env['id']}").status_code == 404
    assert client.delete(f"{URL}/{env['id']}", params={"rev": 2}).status_code == 404
    assert client.delete(f"{URL}/{env['id']}").status_code == 422  # rev is required


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def test_seed_object_then_again(client):
    legacy = {"first": _graph(10), "second": _graph(11)}
    r = client.post(f"{URL}/seed", json={"legacy": legacy})
    assert r.status_code == 200
    body = r.json()
    assert len(body["imported"]) == 2 and body["skipped"] == []
    r = client.post(f"{URL}/seed", json={"legacy": legacy})
    assert r.json()["imported"] == []
    assert {s["reason"] for s in r.json()["skipped"]} == {"duplicate"}
    assert len(client.get(URL).json()["graphs"]) == 2


def test_seed_array_shape(client):
    r = client.post(f"{URL}/seed", json={"legacy": [{"name": "a", "graph": _graph()}]})
    assert r.status_code == 200 and len(r.json()["imported"]) == 1


@pytest.mark.parametrize("legacy", [None, 7, "garbage", [1, 2], {"a": "b"}])
def test_seed_garbage(client, legacy):
    r = client.post(f"{URL}/seed", json={"legacy": legacy})
    assert r.status_code == 200
    assert r.json() == {"imported": [], "skipped": []}


def test_seed_name_clash(client):
    _create(client, "mine", _graph(99))
    r = client.post(f"{URL}/seed", json={"legacy": {"mine": _graph(1)}})
    gid = r.json()["imported"][0]
    assert client.get(f"{URL}/{gid}").json()["name"] == "mine (imported)"


# ---------------------------------------------------------------------------
# A damaged stored file (DI-03 / BC-09): 409 graph_corrupt, never the body's fault
# ---------------------------------------------------------------------------


def test_damaged_file_is_409_graph_corrupt(client, tmp_path):
    good = _create(client, "good", _graph())
    bad = _create(client, "bad", _graph())
    (tmp_path / "graphs" / f"{bad['id']}.json").write_text('{"id": "trunc')

    for r in (
        client.get(f"{URL}/{bad['id']}"),
        client.put(f"{URL}/{bad['id']}", json={"rev": 1, "graph": _graph()}),
        client.post(URL, json={"name": "copy", "duplicate_of": bad["id"]}),
    ):
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "graph_corrupt"
        assert bad["id"] in detail["detail"]

    # A missing key no longer 500s the whole list.
    (tmp_path / "graphs" / f"{good['id']}.json").write_text(
        json.dumps({"id": good["id"], "name": "no rev", "graph": {}})
    )
    r = client.get(URL)
    assert r.status_code == 200 and r.json() == {"graphs": []}

    # DELETE clears it from the list (moved aside) without a rev check.
    assert client.delete(f"{URL}/{bad['id']}", params={"rev": 7}).status_code == 204
    assert client.get(f"{URL}/{bad['id']}").status_code == 404
