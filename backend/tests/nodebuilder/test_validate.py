"""POST /api/nodebuilder/validate and compile diagnostics (F435 W1 item 1.C).

Contract (plan 4.2 and 4.4):
  - /validate returns {"ok", "diagnostics", "streams"}; ok is False when any
    diagnostic is an error.  It never fetches market data.
  - compile collects every problem as a diagnostic with a code and node_id,
    while compile() still raises the first one with its Wave 0 class.
  - every 400 from the nodebuilder and graphs routes carries
    {detail, node_id, code, diagnostics}.
  - a comparison reads its inputs by port (in0 left, in1 right), not by
    the order the wires were drawn in.

No test fetches data, starts a bot or places an order.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import shared
from nodebuilder import evaluator as evaluator_mod
from nodebuilder import run as run_mod
from nodebuilder.compile import compile as compile_graph
from nodebuilder.compile import check_graph, compile_with_diagnostics
from nodebuilder.diagnostics import CODES, SEVERITY_BY_CODE, validate_graph_data
from nodebuilder.evaluator import (
    GraphTypeError,
    MissingTerminalError,
    UnknownNodeTypeError,
    UnsupportedNodeError,
)
from nodebuilder.migrate import CURRENT_GRAPH_VERSION
from nodebuilder.models import Graph
from routes.graphs import router as graphs_router
from routes.nodebuilder import router as nodebuilder_router

VALIDATE = "/api/nodebuilder/validate"
BACKTEST = "/api/nodebuilder/backtest"
GRAPHS = "/api/graphs"

DIAG_KEYS = {
    "node_id", "path", "severity", "code", "message", "param", "port",
    "line", "col", "end_line", "end_col",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _n(node_id: str, node_type: str, **params) -> dict:
    return {"id": node_id, "type": node_type, "params": params}


def _w(wire_id: str, src: str, dst: str, port: str | None = None, attr: str | None = None) -> dict:
    wire = {"id": wire_id, "from": src, "to": dst, "attr": attr}
    if port is not None:
        wire["to_port"] = port
    return wire


def _graph(nodes: list[dict], wires: list[dict], version: int = 2) -> dict:
    return {"_version": version, "nodes": {n["id"]: n for n in nodes}, "wires": wires}


def _rsi_graph() -> dict:
    """Ticker -> RSI -> below 30 -> Entry; RSI above 70 -> Exit.  Valid."""
    return _graph(
        [
            _n("/ticker", "ticker"),
            _n("/rsi", "rsi", period=14, type="sma"),
            _n("/below", "below", threshold=30.0),
            _n("/above", "above", threshold=70.0),
            _n("/entry", "entry"),
            _n("/exit", "exit"),
        ],
        [
            _w("w1", "/ticker", "/rsi"),
            _w("w2", "/rsi", "/below"),
            _w("w3", "/below", "/entry"),
            _w("w4", "/rsi", "/above"),
            _w("w5", "/above", "/exit"),
        ],
    )


def _codes(diags) -> list[str]:
    return [d.code for d in diags]


def _find(diags, code: str):
    matches = [d for d in diags if d.code == code]
    assert matches, f"no {code!r} in {[(d.code, d.node_id, d.message) for d in diags]}"
    return matches[0]


def _df(n: int = 120) -> pd.DataFrame:
    t = np.arange(n)
    close = 100.0 + 10.0 * np.sin(t / 6.0)
    idx = pd.date_range("2023-01-02", periods=n, freq="B", tz="America/New_York", name="Date")
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1_000_000},
        index=idx,
    )


@pytest.fixture
def no_data(monkeypatch):
    """Any attempt to fetch data or run a backtest fails the test."""
    def _boom(*_a, **_k):
        raise AssertionError("validate must not fetch data or run a backtest")

    import routes.nodebuilder as routes_mod

    monkeypatch.setattr(shared, "_fetch", _boom)
    monkeypatch.setattr(run_mod, "run_graph_backtest", _boom)
    # Since W2 a cook is bar prep (prepare.build_graph_attrs) plus the column
    # engine (evaluator.cook_program, which evaluate_graph goes through).
    monkeypatch.setattr(evaluator_mod, "cook_program", _boom)
    # The names the callers actually bound at import (BC-11): patching only
    # the defining modules would never intercept these calls.
    monkeypatch.setattr(routes_mod, "run_graph_backtest", _boom)
    # W4: the backtest route calls the keep-all variant.
    monkeypatch.setattr(routes_mod, "run_graph_backtest_cooked", _boom)
    monkeypatch.setattr(run_mod, "build_graph_attrs", _boom)


@pytest.fixture
def client(no_data):
    app = FastAPI()
    app.include_router(nodebuilder_router)
    return TestClient(app)


@pytest.fixture
def backtest_client(monkeypatch):
    monkeypatch.setattr(shared, "_fetch", lambda *a, **k: _df())
    app = FastAPI()
    app.include_router(nodebuilder_router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def graphs_client(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(graphs_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# The plan's route test and the response shape
# ---------------------------------------------------------------------------

def test_unconnected_entry_is_missing_input_with_entry_node_id(client):
    graph = _rsi_graph()
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/entry"]
    r = client.post(VALIDATE, json={"graph": graph})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"ok", "diagnostics", "streams", "stream_schema"}
    assert body["ok"] is False
    assert body["stream_schema"] == 1
    # The nodes above the unwired Entry are still described (plan 3.3 shape).
    assert {a["name"] for a in body["streams"]["/rsi"]["points"]} >= {"@close", "@rsi"}
    hits = [d for d in body["diagnostics"] if d["code"] == "missing_input"]
    assert hits and hits[0]["node_id"] == "/entry"
    assert hits[0]["severity"] == "error"
    assert hits[0]["path"] == "/entry"
    assert hits[0]["port"] == "in0"
    for d in body["diagnostics"]:
        assert set(d) == DIAG_KEYS


def test_valid_graph_is_ok_with_no_diagnostics(client):
    r = client.post(VALIDATE, json={"graph": _rsi_graph()})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ok", "diagnostics", "streams", "stream_schema"}
    assert body["ok"] is True and body["diagnostics"] == [] and body["stream_schema"] == 1
    graph = _rsi_graph()
    assert set(body["streams"]) == set(graph["nodes"])
    for schema in body["streams"].values():
        assert {"stream_schema", "points", "detail", "prims"} <= set(schema)
        assert schema["stream_schema"] == 1
    assert {"name": "@rsi", "dtype": "float", "written_by": "/rsi"} in body["streams"]["/rsi"]["points"]


def test_warnings_alone_keep_ok_true(client):
    graph = _rsi_graph()
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/exit"]
    body = client.post(VALIDATE, json={"graph": graph}).json()
    assert body["ok"] is True
    assert [d["code"] for d in body["diagnostics"]] == ["exit_unconnected"]
    assert body["diagnostics"][0]["severity"] == "warning"


def test_validate_without_graph_key_is_422(client):
    assert client.post(VALIDATE, json={"nodes": {}}).status_code == 422


def test_unparseable_graph_is_200_with_graph_invalid(client):
    body = client.post(VALIDATE, json={"graph": [1, 2]}).json()
    assert body["ok"] is False
    assert body["diagnostics"][0]["code"] == "graph_invalid"


def test_validate_never_fetches_data(client):
    """The no_data fixture makes any fetch or backtest raise; a full valid
    graph with every settings node still validates."""
    graph = _rsi_graph()
    graph["nodes"]["/size"] = _n("/size", "position_size", size=0.5)
    graph["nodes"]["/sl"] = _n("/sl", "stop_loss", pct=3.0)
    r = client.post(VALIDATE, json={"graph": graph})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_every_code_has_a_severity():
    assert set(SEVERITY_BY_CODE) == CODES
    assert set(SEVERITY_BY_CODE.values()) <= {"error", "warning", "info"}


# ---------------------------------------------------------------------------
# One test per emitted code
# ---------------------------------------------------------------------------

def test_code_missing_terminal():
    graph = _rsi_graph()
    del graph["nodes"]["/entry"]
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/entry"]
    d = _find(validate_graph_data(graph), "missing_terminal")
    assert d.node_id is None and d.severity == "error"


def test_code_missing_input_under_wired_comparison():
    graph = _rsi_graph()
    graph["nodes"]["/below"]["params"] = {}  # one input and no threshold
    d = _find(validate_graph_data(graph), "missing_input")
    assert d.node_id == "/below" and d.port == "in1"


def test_code_missing_input_empty_logic_node():
    graph = _rsi_graph()
    graph["nodes"]["/and"] = _n("/and", "and")
    d = _find(validate_graph_data(graph), "missing_input")
    assert d.node_id == "/and"


def test_code_missing_input_entry_fed_only_by_bypassed_node():
    graph = _rsi_graph()
    graph["nodes"]["/below"]["bypass"] = True
    d = _find(validate_graph_data(graph), "missing_input")
    assert d.node_id == "/entry"


def test_code_unsupported_node():
    """A rule condition auto_render emits but compile cannot run yet."""
    graph = _rsi_graph()
    graph["nodes"]["/above"]["type"] = "is_above_signal"  # a rule name no node registers (rising is real since W2)
    d = _find(validate_graph_data(graph), "unsupported_node")
    assert d.node_id == "/above"
    assert "unknown_node_type" not in _codes(validate_graph_data(graph))


def test_code_unsupported_node_for_condition_extra():
    graph = _rsi_graph()
    graph["nodes"]["/below"]["params"]["condition_extra"] = "atr_pct"
    d = _find(validate_graph_data(graph), "unsupported_node")
    assert d.node_id == "/below" and d.param == "condition_extra"


def test_code_unsupported_node_for_wired_size_terminal():
    graph = _rsi_graph()
    graph["nodes"]["/size_t"] = _n("/size_t", "size")
    graph["wires"].append(_w("ws", "/below", "/size_t"))
    d = _find(validate_graph_data(graph), "unsupported_node")
    assert d.node_id == "/size_t"


def test_code_unknown_node_type():
    graph = _rsi_graph()
    graph["nodes"]["/above"]["type"] = "no_such_node"
    d = _find(validate_graph_data(graph), "unknown_node_type")
    assert d.node_id == "/above"
    assert "unsupported_node" not in _codes(validate_graph_data(graph))


def test_code_dangling_wire():
    graph = _rsi_graph()
    graph["wires"].append(_w("wx", "/rsi", "/nowhere"))
    d = _find(validate_graph_data(graph), "dangling_wire")
    assert d.node_id == "/rsi"


def test_code_cycle_lists_every_node_on_the_loop():
    graph = _rsi_graph()
    graph["nodes"]["/and"] = _n("/and", "and")
    graph["nodes"]["/or"] = _n("/or", "or")
    graph["wires"] += [_w("wa", "/and", "/or"), _w("wb", "/or", "/and")]
    diags = validate_graph_data(graph)
    assert sorted(d.node_id for d in diags if d.code == "cycle") == ["/and", "/or"]


def test_code_param_invalid_rsi_type():
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["params"]["type"] = "polygon"
    d = _find(validate_graph_data(graph), "param_invalid")
    assert d.node_id == "/rsi" and d.param == "type"


def test_code_param_invalid_threshold():
    graph = _rsi_graph()
    graph["nodes"]["/below"]["params"]["threshold"] = "abc"
    d = _find(validate_graph_data(graph), "param_invalid")
    assert d.node_id == "/below" and d.param == "threshold"


def test_code_param_out_of_range():
    graph = _rsi_graph()
    graph["nodes"]["/sl"] = _n("/sl", "stop_loss", pct=-1.0)
    d = _find(validate_graph_data(graph), "param_out_of_range")
    assert d.node_id == "/sl" and d.param == "pct"


def test_code_family_cap():
    graph = _rsi_graph()
    for p in range(2, 23):  # 21 more distinct RSI specs: 22 in all, cap is 20
        graph["nodes"][f"/rsi{p}"] = _n(f"/rsi{p}", "rsi", period=p, type="sma")
        graph["wires"].append(_w(f"wr{p}", "/ticker", f"/rsi{p}"))
    d = _find(validate_graph_data(graph), "family_cap")
    assert d.node_id is not None and d.node_id.startswith("/rsi")


def test_code_name_invalid():
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["name"] = "Bad Name"
    d = _find(validate_graph_data(graph), "name_invalid")
    assert d.node_id == "/rsi"
    # Compile still runs on a graph whose only problem is a name.
    assert _codes(validate_graph_data(graph)) == ["name_invalid"]


def test_code_name_duplicate():
    graph = _rsi_graph()
    graph["nodes"]["/below"]["name"] = "cmp"
    graph["nodes"]["/above"]["name"] = "cmp"
    d = _find(validate_graph_data(graph), "name_duplicate")
    assert d.node_id == "/above"


def test_code_parent_missing():
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["parent"] = "/nope"
    d = _find(validate_graph_data(graph), "parent_missing")
    assert d.node_id == "/rsi" and d.path is None


def test_code_parent_cycle():
    graph = _rsi_graph()
    graph["nodes"]["/net_a"] = {**_n("/net_a", "and"), "parent": "/net_b"}
    graph["nodes"]["/net_b"] = {**_n("/net_b", "and"), "parent": "/net_a"}
    diags = validate_graph_data(graph)
    assert {d.node_id for d in diags if d.code == "parent_cycle"} == {"/net_a", "/net_b"}


def test_code_regime_unsupported():
    graph = _rsi_graph()
    graph["nodes"]["/regime/sma"] = _n("/regime/sma", "sma", period=200)
    graph["wires"].append(_w("wr", "/ticker", "/regime/sma"))
    d = _find(validate_graph_data(graph), "regime_unsupported")
    assert d.node_id == "/regime/sma"


def test_code_port_duplicate():
    graph = _rsi_graph()
    graph["nodes"]["/sma"] = _n("/sma", "sma", period=20)
    graph["nodes"]["/cmp"] = _n("/cmp", "above")
    graph["wires"] += [
        _w("wt", "/ticker", "/sma"),
        _w("wc1", "/rsi", "/cmp", port="in0"),
        _w("wc2", "/sma", "/cmp", port="in0"),
    ]
    d = _find(validate_graph_data(graph), "port_duplicate")
    assert d.node_id == "/cmp" and d.port == "in0"


def test_code_duplicate_terminal():
    graph = _rsi_graph()
    graph["nodes"]["/entry2"] = _n("/entry2", "entry")
    graph["wires"].append(_w("we2", "/above", "/entry2"))
    diags = validate_graph_data(graph)
    dups = [d for d in diags if d.code == "duplicate_terminal"]
    # Whichever Entry the walk meets second is the duplicate.
    assert len(dups) == 1 and dups[0].node_id in {"/entry", "/entry2"}


def test_code_graph_invalid_for_a_bad_node():
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["position"] = "left"
    diags = validate_graph_data(graph)
    d = _find(diags, "graph_invalid")
    assert d.node_id == "/rsi"
    # Compile is skipped: it would call the Entry chain broken for no reason.
    assert "missing_input" not in _codes(diags)


def test_code_attr_type_price_into_entry():
    graph = _rsi_graph()
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/entry"]
    graph["wires"].append(_w("wp", "/ticker", "/entry"))
    d = _find(validate_graph_data(graph), "attr_type")
    assert d.node_id == "/ticker"


def test_indicator_of_indicator_is_not_attr_type():
    """Since W2 an indicator reads its wired source (plan 2.A), so SMA of RSI
    is allowed: no attr_type, and the SMA is computed over @rsi."""
    graph = _rsi_graph()
    graph["nodes"]["/sma"] = _n("/sma", "sma", period=20)
    graph["wires"].append(_w("ws", "/rsi", "/sma"))
    assert "attr_type" not in _codes(validate_graph_data(graph))
    assert tuple(compile_graph(Graph.model_validate(graph)).step("/sma").reads) == ("@rsi",)
    points = check_graph(Graph.model_validate(graph)).streams_json()["/sma"]["points"]
    assert {"name": "@sma", "dtype": "float", "written_by": "/sma"} in points


def test_code_port_unknown_third_comparison_input():
    graph = _rsi_graph()
    graph["nodes"]["/sma"] = _n("/sma", "sma", period=20)
    graph["nodes"]["/ema"] = _n("/ema", "ema", period=20)
    graph["wires"] += [
        _w("wt1", "/ticker", "/sma"), _w("wt2", "/ticker", "/ema"),
        _w("wb1", "/sma", "/below"), _w("wb2", "/ema", "/below"),
    ]
    d = _find(validate_graph_data(graph), "port_unknown")
    assert d.node_id == "/below" and d.port == "in2"


def test_code_port_unknown_wire_out_of_a_terminal():
    graph = _rsi_graph()
    graph["nodes"]["/not"] = _n("/not", "not")
    graph["wires"].append(_w("wn", "/entry", "/not"))
    d = _find(validate_graph_data(graph), "port_unknown")
    assert d.node_id == "/entry"


def test_code_exit_unconnected():
    graph = _rsi_graph()
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/exit"]
    d = _find(validate_graph_data(graph), "exit_unconnected")
    assert d.node_id == "/exit" and d.severity == "warning"


def test_code_exit_unconnected_without_exit_node():
    graph = _rsi_graph()
    del graph["nodes"]["/exit"]
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/exit"]
    d = _find(validate_graph_data(graph), "exit_unconnected")
    assert d.node_id is None


def test_code_size_unit_suspect():
    graph = _rsi_graph()
    graph["nodes"]["/size"] = _n("/size", "position_size", size=50)
    diags = validate_graph_data(graph)
    d = _find(diags, "size_unit_suspect")
    assert d.node_id == "/size" and d.param == "size" and d.severity == "warning"


def test_code_request_invalid_on_backtest(backtest_client):
    body = {"graph": _rsi_graph(), "ticker": "SYN", "start": "2023-01-01",
            "end": "2023-07-01", "source": "not-a-provider"}
    r = backtest_client.post(BACKTEST, json=body)
    assert r.status_code == 400
    j = r.json()
    assert j["code"] == "request_invalid" and j["node_id"] is None
    assert [d["code"] for d in j["diagnostics"]] == ["request_invalid"]


# ---------------------------------------------------------------------------
# Collection: many problems at once, no cascades
# ---------------------------------------------------------------------------

def test_independent_problems_are_all_listed():
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["params"]["type"] = "polygon"          # param_invalid
    graph["nodes"]["/sl"] = _n("/sl", "stop_loss", pct=-2.0)       # out of range
    graph["nodes"]["/x"] = _n("/x", "no_such_node")                 # unknown type
    graph["nodes"]["/rsi"]["name"] = "BAD"                          # name_invalid
    codes = _codes(validate_graph_data(graph))
    for code in ("param_invalid", "param_out_of_range", "unknown_node_type", "name_invalid"):
        assert code in codes


def test_an_upstream_error_does_not_cascade():
    """A broken RSI makes both comparisons and both terminals unusable, but
    only the RSI is reported."""
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["params"]["type"] = "polygon"
    diags = validate_graph_data(graph)
    assert [(d.code, d.node_id) for d in diags] == [("param_invalid", "/rsi")]


def test_compile_raises_the_first_error_with_its_wave0_class():
    graph = _rsi_graph()
    graph["nodes"]["/above"]["type"] = "is_above_signal"  # a rule name no node registers
    graph["nodes"]["/z_sl"] = _n("/z_sl", "stop_loss", pct=-2.0)
    g = Graph.model_validate(graph)
    program, diags = compile_with_diagnostics(g)
    assert program is None
    assert {"unsupported_node", "param_out_of_range"} <= set(_codes(diags))
    # compile() raises the first diagnostic's error (topological order: the
    # unwired stop loss node comes first).
    with pytest.raises(Exception) as info:
        compile_graph(g)
    assert (info.value.code, info.value.node_id) == (diags[0].code, diags[0].node_id)

    # With only the unsupported node, the Wave 0 class is raised.
    del graph["nodes"]["/z_sl"]
    with pytest.raises(UnsupportedNodeError) as info2:
        compile_graph(Graph.model_validate(graph))
    assert info2.value.node_id == "/above" and info2.value.code == "unsupported_node"


def test_compile_error_classes_keep_their_wave0_types():
    g = _rsi_graph()
    g["wires"] = [w for w in g["wires"] if w["to"] != "/entry"]
    with pytest.raises(MissingTerminalError) as info:
        compile_graph(Graph.model_validate(g))
    assert info.value.code == "missing_input" and info.value.node_id == "/entry"

    g = _rsi_graph()
    g["nodes"]["/above"]["type"] = "no_such_node"
    with pytest.raises(UnknownNodeTypeError) as info2:
        compile_graph(Graph.model_validate(g))
    assert isinstance(info2.value, UnsupportedNodeError)

    g = _rsi_graph()
    g["wires"] = [w for w in g["wires"] if w["to"] != "/entry"] + [_w("wp", "/ticker", "/entry")]
    with pytest.raises(TypeError):
        compile_graph(Graph.model_validate(g))
    with pytest.raises(GraphTypeError):
        compile_graph(Graph.model_validate(g))


def test_valid_compile_gives_a_program_and_no_diagnostics():
    program, diags = compile_with_diagnostics(Graph.model_validate(_rsi_graph()))
    assert program is not None and diags == []


# ---------------------------------------------------------------------------
# Comparison input order follows to_port (Wave 0 deferral)
# ---------------------------------------------------------------------------

def _rsi_above_sma() -> dict:
    return _graph(
        [
            _n("/ticker", "ticker"),
            _n("/rsi", "rsi", period=14, type="sma"),
            _n("/sma", "sma", period=20),
            _n("/above", "above"),
            _n("/entry", "entry"),
        ],
        [
            _w("w1", "/ticker", "/rsi", port="in0"),
            _w("w2", "/ticker", "/sma", port="in0"),
            _w("left", "/rsi", "/above", port="in0"),
            _w("right", "/sma", "/above", port="in1"),
            _w("w5", "/above", "/entry", port="in0"),
        ],
    )


def _comparison_reads(graph: dict) -> tuple:
    program = compile_graph(Graph.model_validate(graph))
    return tuple(program.step("/above").reads)


def test_redrawn_left_wire_does_not_flip_the_comparison():
    graph = _rsi_above_sma()
    before = _comparison_reads(graph)
    assert before == ("@rsi", "@sma")

    # Delete the left wire and draw it again: the editor appends the new wire
    # to the end of the list, on the free port in0.
    graph["wires"] = [w for w in graph["wires"] if w["id"] != "left"]
    graph["wires"].append(_w("left_again", "/rsi", "/above", port="in0"))
    assert [w["id"] for w in graph["wires"] if w["to"] == "/above"] == ["right", "left_again"]
    assert _comparison_reads(graph) == before


def test_ports_decide_order_even_when_listed_backwards():
    graph = _rsi_above_sma()
    for w in graph["wires"]:
        if w["id"] == "left":
            w["to_port"] = "in1"
        elif w["id"] == "right":
            w["to_port"] = "in0"
    assert _comparison_reads(graph) == ("@sma", "@rsi")


def test_numbered_ports_sort_as_numbers():
    """in10 comes after in2 (an OR with many inputs)."""
    from nodebuilder.compile import _wires_into

    nodes = [_n("/ticker", "ticker"), _n("/or", "or")]
    wires = [_w(f"w{k}", "/ticker", "/or", port=f"in{k}") for k in (10, 2, 0)]
    g = Graph.model_validate(_graph(nodes, wires))
    assert [w.to_port for w in _wires_into(g, "/or")] == ["in0", "in2", "in10"]


# ---------------------------------------------------------------------------
# 400 bodies carry the full 4.4 shape
# ---------------------------------------------------------------------------

def test_backtest_400_has_code_and_full_diagnostics(backtest_client):
    graph = _rsi_graph()
    graph["wires"] = [w for w in graph["wires"] if w["to"] != "/entry"]
    graph["nodes"]["/sl"] = _n("/sl", "stop_loss", pct=-1.0)
    body = {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01"}
    r = backtest_client.post(BACKTEST, json=body)
    assert r.status_code == 400, r.text
    j = r.json()
    assert set(j) == {"detail", "node_id", "code", "diagnostics"}
    codes = {(d["code"], d["node_id"]) for d in j["diagnostics"]}
    assert ("param_out_of_range", "/sl") in codes
    assert ("missing_input", "/entry") in codes
    assert j["code"] in {"param_out_of_range", "missing_input"}
    for d in j["diagnostics"]:
        assert set(d) == DIAG_KEYS


def test_backtest_parse_error_400_lists_every_structural_problem(backtest_client):
    graph = _rsi_graph()
    graph["wires"].append(_w("wx", "/rsi", "/nowhere"))
    graph["nodes"]["/rsi"]["name"] = "Bad Name"
    body = {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01"}
    j = backtest_client.post(BACKTEST, json=body).json()
    assert j["code"] == "name_invalid" and j["node_id"] == "/rsi"
    assert {"dangling_wire", "name_invalid"} <= {d["code"] for d in j["diagnostics"]}


def test_graphs_route_400_lists_every_structural_problem(graphs_client):
    graph = _rsi_graph()
    graph["wires"].append(_w("wx", "/rsi", "/nowhere"))
    graph["nodes"]["/and"] = _n("/and", "and")
    graph["nodes"]["/or"] = _n("/or", "or")
    graph["wires"] += [_w("wa", "/and", "/or"), _w("wb", "/or", "/and")]
    r = graphs_client.post(GRAPHS, json={"name": "broken", "graph": graph})
    assert r.status_code == 400, r.text
    j = r.json()
    assert set(j) == {"detail", "node_id", "code", "diagnostics"}
    codes = [d["code"] for d in j["diagnostics"]]
    assert "dangling_wire" in codes and codes.count("cycle") == 2


# ---------------------------------------------------------------------------
# W1 review fixes: params, ports, malformed shapes (BC-01..05, BC-08, BC-12)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, code", [
    (1, "param_out_of_range"),
    (501, "param_out_of_range"),
    (0, "param_out_of_range"),
    (-5, "param_out_of_range"),
    ("abc", "param_invalid"),
    (True, "param_invalid"),
    (14.5, "param_invalid"),
    (None, "param_invalid"),
    ([14], "param_invalid"),
    ({"expr": "ch('x')"}, "param_invalid"),
])
def test_indicator_period_is_checked_before_run(client, value, code):
    """BC-03 / BC-02: a bad period is a diagnostic on the param, not a
    request_invalid (or a 500) at Run."""
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["params"]["period"] = value
    r = client.post(VALIDATE, json={"graph": graph})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    hits = [d for d in body["diagnostics"] if d["code"] == code]
    assert hits and hits[0]["node_id"] == "/rsi" and hits[0]["param"] == "period"


@pytest.mark.parametrize("value", [2, 500, 14.0, "14"])
def test_indicator_period_limits_and_forms_that_run(client, value):
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["params"]["period"] = value
    assert client.post(VALIDATE, json={"graph": graph}).json()["ok"] is True


def test_bollinger_stddev_and_unknown_params_are_checked(client):
    graph = _rsi_graph()
    graph["nodes"]["/bb"] = _n("/bb", "bollinger", period=20, stddev=9.0)
    graph["nodes"]["/sma"] = _n("/sma", "sma", period=20, extra=[1, 2])
    graph["wires"] += [_w("wb", "/ticker", "/bb"), _w("ws", "/ticker", "/sma")]
    diags = validate_graph_data(graph)
    assert _find(diags, "param_out_of_range").param == "stddev"
    bad = _find(diags, "param_invalid")
    assert (bad.node_id, bad.param) == ("/sma", "extra")


def test_unhashable_param_on_backtest_is_400_not_500(backtest_client):
    """BC-02: /backtest (and the 400 body's own validate) no longer 500."""
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["params"]["period"] = [14]
    body = {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01"}
    r = backtest_client.post(BACKTEST, json=body)
    assert r.status_code == 400, r.text
    j = r.json()
    assert j["code"] == "param_invalid" and j["node_id"] == "/rsi"


def test_comparison_with_only_b_wired_is_missing_a():
    """BC-04: a lone wire on in1 is not read as a, even with a threshold."""
    graph = _rsi_graph()
    for w in graph["wires"]:
        if w["to"] == "/below":
            w["to_port"] = "in1"
    diags = validate_graph_data(graph)
    d = _find(diags, "missing_input")
    assert (d.node_id, d.port) == ("/below", "in0")
    with pytest.raises(Exception) as info:
        compile_graph(Graph.model_validate(graph))
    assert getattr(info.value, "code", None) == "missing_input"


def test_comparison_with_bypassed_a_is_off_not_b():
    """BC-04: with a bypassed, the comparison is off; b never stands in for a."""
    graph = _rsi_above_sma()
    graph["nodes"]["/rsi"]["bypass"] = True
    graph["nodes"]["/above"]["params"]["threshold"] = 50
    program, diags = compile_with_diagnostics(Graph.model_validate(graph))
    # The comparison is off, so the Entry it feeds gets nothing.  Before the
    # fix it compared SMA (b) to 50 and the program ran.
    assert program is None
    d = _find(diags, "missing_input")
    assert d.node_id == "/entry" and "bypassed" in d.message
    # b bypassed with a threshold still compares a to the threshold (as before).
    graph = _rsi_above_sma()
    graph["nodes"]["/sma"]["bypass"] = True
    graph["nodes"]["/above"]["params"]["threshold"] = 50
    assert _comparison_reads(graph) == ("@rsi",)


def test_comparison_wire_on_a_port_it_does_not_have(client):
    """BC-05: a comparison has in0 and in1 only."""
    graph = _rsi_graph()
    for w in graph["wires"]:
        if w["to"] == "/below":
            w["to_port"] = "in5"
    d = _find(validate_graph_data(graph), "port_unknown")
    assert (d.node_id, d.port) == ("/below", "in5")


def test_port_duplicate_is_refused_everywhere_and_listed_once(backtest_client, graphs_client):
    """DI-05 / BC-05: /validate, compile, storage and Run all refuse two wires
    on one port, and /validate lists it once."""
    graph = _rsi_graph()
    graph["nodes"]["/sma"] = _n("/sma", "sma", period=20)
    graph["nodes"]["/cmp"] = _n("/cmp", "above")
    graph["wires"] += [
        _w("wt", "/ticker", "/sma"),
        _w("wc1", "/rsi", "/cmp", port="in0"),
        _w("wc2", "/sma", "/cmp", port="in0"),
    ]
    diags = validate_graph_data(graph)
    assert [(d.node_id, d.port) for d in diags if d.code == "port_duplicate"] == [("/cmp", "in0")]

    # compile on a graph built without validation (the /validate fallback)
    from nodebuilder.models import Node, Wire
    g = Graph.model_construct(
        nodes={k: Node.model_validate(v) for k, v in graph["nodes"].items()},
        wires=[Wire.model_validate(w) for w in graph["wires"]],
    )
    with pytest.raises(Exception) as info:
        compile_graph(g)
    assert getattr(info.value, "code", None) == "port_duplicate"

    body = {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01"}
    r = backtest_client.post(BACKTEST, json=body)
    assert r.status_code == 400 and r.json()["code"] == "port_duplicate"
    r = graphs_client.post(GRAPHS, json={"name": "dup", "graph": graph})
    assert r.status_code == 400 and r.json()["code"] == "port_duplicate"


@pytest.mark.parametrize("patch", [{"nodes": [1, 2]}, {"wires": 5}, {"wires": {"a": 1}}])
def test_malformed_graph_shapes_are_400_graph_invalid(backtest_client, graphs_client, patch):
    """BC-01: nodes as a list or wires as a number is a 400 graph_invalid on
    every route, not a 500 or a request_invalid."""
    graph = {**_rsi_graph(), **patch}
    r = graphs_client.post(GRAPHS, json={"name": "bad", "graph": graph})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "graph_invalid"
    body = {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01"}
    r = backtest_client.post(BACKTEST, json=body)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "graph_invalid"


def test_badly_typed_graph_on_backtest_is_the_graphs_400(backtest_client):
    """BC-08: a graph-level field error is the plan 4.4 400, like /api/graphs;
    request fields keep FastAPI's 422."""
    graph = _rsi_graph()
    graph["nodes"]["/rsi"]["position"] = "abc"
    body = {"graph": graph, "ticker": "SYN", "start": "2023-01-01", "end": "2023-07-01"}
    r = backtest_client.post(BACKTEST, json=body)
    assert r.status_code == 400, r.text
    j = r.json()
    assert set(j) == {"detail", "node_id", "code", "diagnostics"}
    assert j["code"] == "graph_invalid"
    # A bad request field (no ticker) is still a 422, and so is a missing graph.
    assert backtest_client.post(BACKTEST, json={**body, "ticker": None}).status_code == 422
    no_graph = {k: v for k, v in body.items() if k != "graph"}
    assert backtest_client.post(BACKTEST, json=no_graph).status_code == 422


def test_newer_graph_version_is_400_graph_invalid(graphs_client, client):
    """BC-10 / DI-04: a newer graph is refused on save with a clear message."""
    graph = {**_rsi_graph(), "_version": CURRENT_GRAPH_VERSION + 1}
    r = graphs_client.post(GRAPHS, json={"name": "future", "graph": graph})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "graph_invalid"
    assert "newer StrategyLab" in r.json()["detail"]
    body = client.post(VALIDATE, json={"graph": graph}).json()
    assert body["ok"] is False and body["diagnostics"][0]["code"] == "graph_invalid"


def test_cycle_finder_is_linear_and_exact():
    """BC-12: one SCC pass.  Flags loop members (and self-loops) only, and a
    long chain neither recurses too deep nor walks from every node."""
    from nodebuilder.diagnostics import _nodes_on_cycles
    from nodebuilder.models import Node, Wire

    def build(edges, names):
        nodes = {n: Node(id=n, type="and", name=n.strip("/")) for n in names}
        wires = [Wire(id=f"w{i}", **{"from": a, "to": b}) for i, (a, b) in enumerate(edges)]
        return nodes, wires

    nodes, wires = build(
        [("/a", "/b"), ("/b", "/a"), ("/b", "/c"), ("/d", "/d"), ("/e", "/f")],
        ["/a", "/b", "/c", "/d", "/e", "/f"],
    )
    assert _nodes_on_cycles(nodes, wires) == ["/a", "/b", "/d"]

    n = 20_000
    names = [f"/n{i}" for i in range(n)]
    nodes, wires = build([(names[i], names[i + 1]) for i in range(n - 1)], names)
    assert _nodes_on_cycles(nodes, wires) == []
    wires.append(Wire(id="back", **{"from": names[-1], "to": names[0]}))
    assert len(_nodes_on_cycles(nodes, wires)) == n
