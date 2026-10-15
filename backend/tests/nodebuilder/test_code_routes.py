"""The W7 node-builder routes (F435 item 7.C, plan "Wave 7" contracts).

- POST /api/nodebuilder/parse_code: prepare() only (never runs code); the
  spare params (lookback_bars first), reads with the class and dtype the
  graph gives, writes with dtype bool, float or "any"; diagnostics at
  Python's line and offset - 1; ref_broken for a ch() path that points at
  nothing; code_disabled while SL_CODE_NODES=0.
- GET /api/nodebuilder/code_capabilities.
- POST /api/nodebuilder/validate gains param_deps (and "any" dtypes in
  streams).
- /backtest, /inspect and /preview answer a code failure while cooking with
  the plan 4.4 400 body (code, node_id, line, column), never a 500; a cook
  past the 60 s guard (0.2 s here) is a 400 code_timeout.

The data fetch is a stub returning a synthetic frame: nothing is fetched,
no bot exists, nothing is written to the data folder.
"""
from __future__ import annotations

import ast
import copy
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.nodebuilder as nb_route
import shared
from nodebuilder.code import leaked_cooks
from nodebuilder.code import runtime as code_runtime
from tests.nodebuilder.code_graphs import (VOL_CODE, daily_frame, graph_data, node,
                                           vision_data, wire)

FRAME = daily_frame(400, seed=11)
WINDOW = {"ticker": "AAPL", "start": "2018-01-01", "end": "2020-01-01", "interval": "1d",
          "source": "yahoo"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(shared, "_fetch", lambda *a, **k: FRAME)
    monkeypatch.delenv("SL_CODE_NODES", raising=False)
    app = FastAPI()
    app.include_router(nb_route.router)
    return TestClient(app)


def _parse(client, code, context="wrangle", graph=None, node_id=None, **extra):
    body = {"code": code, "context": context, "expected": None, "graph": graph,
            "node_id": node_id, **extra}
    r = client.post("/api/nodebuilder/parse_code", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _wrangle_graph(code: str) -> dict:
    return graph_data([
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        node("w", "wrangle", {"lookback_bars": 20}, code=code),
        node("entry", "entry", {"signal": "@sig"}),
    ], [wire("w1", "t", "w"), wire("w2", "w", "entry")])


# ---------------------------------------------------------------------------
# parse_code
# ---------------------------------------------------------------------------


def test_parse_code_reports_params_reads_and_writes(client):
    graph = vision_data()
    body = _parse(client, VOL_CODE, graph=graph, node_id="vol")
    assert body["ok"] is True and body["result_type"] is None and body["diagnostics"] == []
    assert [p["name"] for p in body["params"]] == ["lookback_bars", "atr_period", "threshold"]
    assert body["params"][0] == {"name": "lookback_bars", "type": "int", "default": 500,
                                 "min": 1, "max": 100000, "label": "lookback bars",
                                 "options": None}
    assert body["params"][1]["min"] == 2 and body["params"][1]["max"] == 50
    reads = {r["name"]: (r["class"], r["dtype"]) for r in body["reads"]}
    assert reads["@close"] == ("point", "float")
    assert reads["@atr_pct"] == ("point", "float")  # its own earlier write
    assert body["writes"] == [{"name": "@atr_pct", "class": "point", "dtype": "float"},
                              {"name": "@vol_regime", "class": "point", "dtype": "bool"}]


def test_parse_code_reports_an_unannotated_write_as_any(client):
    body = _parse(client, "@z = @close * 2")
    assert body["writes"] == [{"name": "@z", "class": "point", "dtype": "any"}]


def test_parse_code_syntax_error_is_at_pythons_line_and_offset_minus_one(client):
    code = "x = 1\ny = 2 +* 3\n"
    with pytest.raises(SyntaxError) as py:
        ast.parse(code)
    body = _parse(client, code, node_id="w")
    assert body["ok"] is False
    (d,) = body["diagnostics"]
    assert (d["code"], d["node_id"], d["line"], d["col"]) == (
        "code_syntax", "w", py.value.lineno, py.value.offset - 1)


def test_parse_code_a_column_after_an_attr_maps_back_to_the_users_text(client):
    body = _parse(client, "@x = @close +* 2", node_id="w")
    (d,) = body["diagnostics"]
    # The user's text has "+*" at column 12; the rewritten text is longer.
    assert (d["code"], d["line"]) == ("code_syntax", 1)
    assert "@x = @close +* 2"[d["col"]] in "+*"


def test_parse_code_flags_a_path_that_points_at_nothing(client):
    graph = vision_data()
    code = '7 if chf("../nope/threshold") > 2 else 21'
    body = _parse(client, code, context="expr", graph=graph, node_id="rsi", param="period",
                  expected={"type": "int"})
    assert body["ok"] is False
    (d,) = body["diagnostics"]
    assert (d["code"], d["node_id"], d["param"], d["line"], d["col"]) == (
        "ref_broken", "rsi", "period", 1, 5)
    # The working path gives no diagnostic.
    ok = _parse(client, '7 if chf("../vol/threshold") > 2 else 21', context="expr",
                graph=graph, node_id="rsi", param="period", expected={"type": "int"})
    assert ok["ok"] is True and ok["diagnostics"] == []


def test_parse_code_flags_an_attribute_nobody_writes(client):
    graph = vision_data()
    body = _parse(client, '@x = ch("../vol/@nope")', graph=graph, node_id="both")
    assert [d["code"] for d in body["diagnostics"]] == ["ref_broken"]


def test_parse_code_reads_a_detail_attribute_class_from_the_graph(client):
    graph = graph_data([
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        node("c", "constant", {"value": 9, "as_detail": True, "out": "@th"}),
        node("r", "rsi", {"period": 14, "source": "@close"}),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "c"), wire("w2", "c", "r"), wire("w3", "r", "entry")])
    body = _parse(client, "int(@th)", context="expr", graph=graph, node_id="r", param="period")
    assert body["reads"] == [{"name": "@th", "class": "detail", "dtype": "float"}]
    assert body["params"] == []  # an expression has no lookback_bars


def test_parse_code_never_runs_the_code(client, tmp_path):
    marker = tmp_path / "ran.txt"
    body = _parse(client, f"open({str(marker)!r}, 'w').write('x')\n@sig: bool = @close > 0",
                  graph=_wrangle_graph("@sig: bool = @close > 0"), node_id="w")
    assert body["ok"] is True
    assert not marker.exists()


def test_parse_code_tolerates_a_graph_that_does_not_parse(client):
    body = _parse(client, "@z = 1.0", graph={"nodes": "not a map"}, node_id="w")
    assert body["ok"] is True


def test_parse_code_refuses_an_unknown_context(client):
    r = client.post("/api/nodebuilder/parse_code", json={"code": "1", "context": "nope"})
    assert r.status_code == 422


def test_parse_code_says_code_disabled_when_the_switch_is_off(client, monkeypatch):
    monkeypatch.setenv("SL_CODE_NODES", "0")
    body = _parse(client, "@z = 1.0", node_id="w")
    assert body["ok"] is False
    assert [d["code"] for d in body["diagnostics"]] == ["code_disabled"]


# ---------------------------------------------------------------------------
# code_capabilities and validate
# ---------------------------------------------------------------------------


def test_code_capabilities(client, monkeypatch):
    body = client.get("/api/nodebuilder/code_capabilities").json()
    assert body["enabled"] is True and body["language"] == "python"
    assert body["limits"] == {"max_source_bytes": 8192, "default_lookback_bars": 500,
                              "cook_timeout_s": {"bot": 10, "backtest": 60}}
    assert body["modules"][:4] == ["np", "pd", "math", "sl"]
    assert "sl.rsi" in [f["name"] for f in body["functions"]]
    assert isinstance(body["leaked_cooks"], int)
    monkeypatch.setenv("SL_CODE_NODES", "0")
    assert client.get("/api/nodebuilder/code_capabilities").json()["enabled"] is False


def test_validate_returns_param_deps_and_any_dtypes(client):
    graph = vision_data()
    graph["nodes"]["vol"]["code"] = VOL_CODE + "@spare = @atr_pct * 2\n"
    body = client.post("/api/nodebuilder/validate", json={"graph": graph}).json()
    assert body["ok"] is True, body["diagnostics"]
    assert body["param_deps"] == [{"reader_id": "rsi", "reader_param": "period",
                                   "target_id": "vol", "target": "threshold"}]
    vol = {p["name"]: p["dtype"] for p in body["streams"]["vol"]["points"]}
    assert (vol["@atr_pct"], vol["@vol_regime"], vol["@spare"]) == ("float", "bool", "any")


def test_validate_param_deps_is_empty_without_references(client):
    body = client.post("/api/nodebuilder/validate",
                       json={"graph": _wrangle_graph("@sig: bool = @close > 0")}).json()
    assert body["ok"] is True and body["param_deps"] == []


# ---------------------------------------------------------------------------
# Cooking routes: a code failure is a 400 with its place, never a 500
# ---------------------------------------------------------------------------


RAISES = "x = 1\ny = 0\n@sig: bool = @close > x / y\n"


def _backtest(client, graph):
    return client.post("/api/nodebuilder/backtest", json={"graph": graph, **WINDOW})


def test_backtest_runs_code(client):
    r = _backtest(client, vision_data())
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["num_trades"] >= 1


def test_backtest_code_failure_is_a_400_with_its_place(client):
    r = _backtest(client, _wrangle_graph(RAISES))
    assert r.status_code == 400, r.text
    body = r.json()
    assert (body["code"], body["node_id"]) == ("code_runtime", "w")
    (top,) = [d for d in body["diagnostics"] if d["code"] == "code_runtime"]
    assert (top["line"], top["col"]) == (3, RAISES.splitlines()[2].index("x / y"))
    assert "ZeroDivisionError" in body["detail"]


@pytest.mark.parametrize("path, extra", [
    ("/api/nodebuilder/inspect", {"target": {"node_id": "w"}}),
    ("/api/nodebuilder/preview", {}),
])
def test_inspect_and_preview_code_failure_is_a_400(client, path, extra):
    r = client.post(path, json={"graph": _wrangle_graph(RAISES), "window": WINDOW, **extra})
    assert r.status_code == 400, r.text
    assert (r.json()["code"], r.json()["node_id"]) == ("code_runtime", "w")


def test_inspect_shows_what_a_wrangle_wrote(client):
    r = client.post("/api/nodebuilder/inspect", json={
        "graph": vision_data(), "window": WINDOW, "target": {"node_id": "vol"}})
    assert r.status_code == 200, r.text
    columns = {c["name"]: c for c in r.json()["columns"]}
    assert {"@atr_pct", "@vol_regime"} <= set(columns)
    assert columns["@vol_regime"]["dtype"] == "bool"


def test_a_route_cook_past_the_guard_is_code_timeout(client, monkeypatch):
    monkeypatch.setattr(code_runtime, "ROUTE_COOK_TIMEOUT_S", 0.2)
    graph = _wrangle_graph("import time\ntime.sleep(1.0)\n@sig: bool = @close > 0")
    graph["nodes"]["w"]["name"] = "sleepy"
    before = leaked_cooks()
    started = time.perf_counter()
    r = _backtest(client, graph)
    assert time.perf_counter() - started < 0.9
    assert r.status_code == 400, r.text
    assert (r.json()["code"], r.json()["node_id"]) == ("code_timeout", "w")
    assert "sleepy ran longer than 0.2 s" in r.json()["detail"]
    assert leaked_cooks() == before + 1
    deadline = time.monotonic() + 5
    while leaked_cooks() > before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert leaked_cooks() == before  # the sleep ended by itself; nothing leaks


def test_a_graph_without_code_is_not_guarded(client, monkeypatch):
    calls = []
    real = code_runtime.call_guarded

    def spy(*a, **k):
        # "compile": the RSI period reads only params, so compile evaluates
        # it on its own pool to size the window (kernel.params.static_windows).
        calls.append("compile" if k.get("executor") is code_runtime.compile_pool() else "cook")
        return real(*a, **k)

    monkeypatch.setattr(code_runtime, "call_guarded", spy)
    plain = copy.deepcopy(vision_data())
    plain["nodes"]["rsi"]["params"]["period"] = 14
    plain["nodes"].pop("vol")
    plain["nodes"]["both"]["params"]["terms"] = ["@rsi_low"]
    plain["wires"] = [w for w in plain["wires"] if w["id"] not in ("w1", "w6")]
    assert _backtest(client, plain).status_code == 200
    assert calls == []
    assert _backtest(client, vision_data()).status_code == 200
    assert calls == ["compile", "cook"]
