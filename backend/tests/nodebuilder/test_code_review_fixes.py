"""Fixes from the W7 review wave (F435, .run/F435/w7/decisions.md, fixer B1).

Each test names the finding it covers and fails on the code before the fix:

- PC-2 / BS-04 / PC-9 / PC-6: one shared rule, param_not_codeable, for every
  param read before any cook (the simulator plan, the bot config, a Ticker,
  an Output Group, lookback_bars, and a promoted default that would reach
  one).
- PC-1 / CR-2: an expression on a window param that reads only params is
  evaluated at compile and sizes the live window from its value; one that
  reads data sizes it for the param's max; a value past what compile
  planned fails the cook loudly.
- PC-3 / PC-4: stored spare values are checked at compile; a bad
  lookback_bars is a diagnostic, never a 500.
- PC-5: a ch() of an expression across Ticker domains is a compile error.
- PC-7: an expression reading a per-bar attribute through a path is
  code_type at compile.
- PC-8: parse_code never 500s on a half-edited node, and uses ``expected``.
- PC-10: a code block may read its own node's output through a path.
- CR-1: a write in an untaken branch is attr_missing on the writer, a 400.
- CR-10: an expression on a vector spare param is refused at compile.
- The Wrangle's default output: one write is its primary write.

Synthetic frames and mocked fetches only: nothing trades, nothing is saved.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.nodebuilder as nb_route
import shared
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.code import CodeError
from nodebuilder.compile import check_graph, compile_with_diagnostics
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program
from nodebuilder.kernel import registry
from nodebuilder.kernel.registry import ParamSpec, register_node
from nodebuilder.models import Graph
from nodebuilder.prepare import live_fetch_start
from nodebuilder.trading.sim_bridge import PLAN_READ_PARAMS
from tests.nodebuilder.code_graphs import daily_frame, graph_data, node, wire

FRAME = daily_frame(400, seed=11)
WINDOW = {"ticker": "AAPL", "start": "2018-01-01", "end": "2020-01-01", "interval": "1d",
          "source": "yahoo"}


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return daily_frame(600, seed=4)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(shared, "_fetch", lambda *a, **k: FRAME)
    monkeypatch.delenv("SL_CODE_NODES", raising=False)
    app = FastAPI()
    app.include_router(nb_route.router)
    return TestClient(app)


def _graph(data: dict) -> Graph:
    return Graph.model_validate(data)


def _diags(data: dict):
    program, diagnostics = compile_with_diagnostics(_graph(data))
    return program, [d for d in diagnostics if d.severity == "error"]


def _ticker(nid: str = "t", symbol: str = "AAPL", **params) -> dict:
    return node(nid, "ticker", {"symbol": symbol, "interval": "1d", **params})


def _base(extra_nodes=(), extra_wires=(), entry_params=None) -> dict:
    """Ticker -> RSI -> below 30 -> Entry, plus *extra_nodes*."""
    nodes = [
        _ticker(),
        node("rsi", "rsi", {"period": 14}),
        node("lo", "below", {"a": "@rsi", "threshold": 30, "out": "@lo"}),
        node("entry", "entry", dict(entry_params or {})),
        *extra_nodes,
    ]
    wires = [wire("w1", "t", "rsi"), wire("w2", "rsi", "lo"), wire("w3", "lo", "entry"),
             *extra_wires]
    return graph_data(nodes, wires)


# ---------------------------------------------------------------------------
# PC-2 / BS-04 / PC-9: params read before the cook
# ---------------------------------------------------------------------------


def _plan_read_cases():
    for type_name, params in sorted(PLAN_READ_PARAMS.items()):
        for param in params:
            yield type_name, param


def _graph_with(type_name: str, param: str) -> dict:
    """_base() plus one node of *type_name* whose *param* holds an expression."""
    target = node("x", type_name, {param: {"expr": "3"}})
    if type_name in ("entry",):
        target = node("entry", "entry", {param: {"expr": "'long'"}})
        return _base(entry_params=target["params"])
    if type_name == "exit":
        return _base(extra_nodes=[node("x", "exit", {param: {"expr": "'long'"}})],
                     extra_wires=[wire("w9", "lo", "x")])
    if type_name == "regime":
        return _base(extra_nodes=[node("x", "regime", {"signal": "@lo", param: {"expr": "'hold'"}})],
                     extra_wires=[wire("w9", "lo", "x")])
    return _base(extra_nodes=[target])


@pytest.mark.parametrize("type_name, param", list(_plan_read_cases()))
def test_every_param_the_plan_reads_refuses_an_expression(type_name, param):
    """PC-2 / BS-04: the plan reads these before any cook, so an expression
    would run its compile-time stand-in.  One rule, on the right param."""
    program, errors = _diags(_graph_with(type_name, param))
    assert program is None
    hits = [d for d in errors if d.code == "param_not_codeable"]
    nid = "entry" if type_name == "entry" else "x"
    assert [(d.node_id, d.param) for d in hits] == [(nid, param)]
    assert "read before the cook, so it cannot be an expression" in hits[0].message


def test_a_time_stop_expression_no_longer_trades_the_stand_in():
    """BS-04: max_bars {"expr": "30"} compiled and traded max_bars_held = 1."""
    data = _base(extra_nodes=[node("ts", "time_stop", {"max_bars": {"expr": "30"}})])
    program, errors = _diags(data)
    assert program is None
    assert [(d.code, d.node_id, d.param) for d in errors] == [
        ("param_not_codeable", "ts", "max_bars")]


def test_a_trailing_stop_expression_names_its_param_without_a_pydantic_dump():
    """PC-9: the message was a raw pydantic error with no param."""
    data = _base(extra_nodes=[node("tr", "trailing_stop", {"value": {"expr": "3.0"}})])
    _program, errors = _diags(data)
    (d,) = errors
    assert (d.code, d.node_id, d.param) == ("param_not_codeable", "tr", "value")
    assert "pydantic" not in d.message and "validation error" not in d.message


def test_lookback_bars_cannot_be_an_expression():
    data = _base(extra_nodes=[node("w", "wrangle", {"lookback_bars": {"expr": "2000"}},
                                   code="@m = @close * 2")],
                 extra_wires=[wire("w9", "t", "w")])
    _program, errors = _diags(data)
    assert [(d.code, d.node_id, d.param) for d in errors] == [
        ("param_not_codeable", "w", "lookback_bars")]
    assert "how many bars are fetched" in errors[0].message


def test_an_output_group_param_expression_is_param_not_codeable():
    data = graph_data([
        node("g", "output_group", {"direction": {"expr": "'long'"}, "ticker": "t"}),
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}, parent="g"),
        node("rsi", "rsi", {"period": 14}, parent="g"),
        node("lo", "below", {"a": "@rsi", "threshold": 30, "out": "@lo"}, parent="g"),
        node("entry", "entry", {}, parent="g"),
    ], [wire("w1", "t", "rsi"), wire("w2", "rsi", "lo"), wire("w3", "lo", "entry")])
    check = check_graph(_graph(data))
    hits = [d for d in check.diagnostics if d.code == "param_not_codeable"]
    assert [(d.node_id, d.param) for d in hits] == [("g", "direction")]


def _subnet(target: str, default) -> dict:
    """A subnet promoting *target* with *default*, and no value of its own."""
    inner = {
        "rsi": node("rsi", "rsi", {"period": 14, "out": "@r"}, parent="sub"),
        "slip": node("slip", "slippage", {"bps": 2.0}, parent="sub"),
    }
    return graph_data([
        _ticker(),
        node("sub", "subnet", {}, promoted=[{"name": "lb", "label": "lb", "target": target,
                                             "type": "number", "default": default}]),
        node("in0", "subnet_input", {"port": 0}, parent="sub"),
        *inner.values(),
        node("out", "subnet_output", {}, parent="sub"),
        node("lo", "below", {"a": "@r", "threshold": 30, "out": "@lo"}),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "sub"), wire("w2", "in0", "rsi"), wire("w3", "rsi", "out"),
        wire("w4", "sub", "lo"), wire("w5", "lo", "entry")])


def test_a_promoted_default_expression_is_never_silently_the_stand_in():
    """PC-6: a promoted param whose default is an expression gave the target
    the stand-in with no diagnostic.  Now it is refused on the promoted
    param (a network's params cannot hold code yet)."""
    check = check_graph(_graph(_subnet("rsi/period", {"expr": "50"})))
    assert check.program is None
    hits = [(d.code, d.node_id, d.param) for d in check.diagnostics if d.severity == "error"]
    assert hits == [("param_invalid", "sub", "lb")]
    # A plain default still works.
    assert check_graph(_graph(_subnet("rsi/period", 50))).program is not None


def test_a_promoted_default_expression_on_a_plan_read_param_is_param_not_codeable():
    """PC-6 with the target's rule: the slippage bps is read before the cook."""
    check = check_graph(_graph(_subnet("slip/bps", {"expr": "3"})))
    hits = [(d.code, d.node_id, d.param) for d in check.diagnostics if d.severity == "error"]
    assert ("param_not_codeable", "sub", "lb") in hits


# ---------------------------------------------------------------------------
# PC-1 / CR-2: expression windows
# ---------------------------------------------------------------------------


def _sma(period) -> dict:
    return graph_data([
        _ticker(),
        node("ma", "sma", {"period": period, "out": "@ma"}),
        node("lo", "below", {"a": "@close", "b": "@ma", "out": "@lo"}),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "ma"), wire("w2", "ma", "lo"), wire("w3", "lo", "entry"),
        wire("w4", "t", "lo", "in1")])


def test_an_expression_on_an_sma_period_sizes_the_live_window_for_200_bars():
    """CR-2 / PC-1: SMA(200) by expression got ~36 live daily bars (the
    window was sized for the default 20), so its signal was NaN live."""
    plain = nb_compile(_graph(_sma(200)))
    program = nb_compile(_graph(_sma({"expr": "200"})))
    assert plain.required_lookback_bars == 200
    assert program.required_lookback_bars >= 200
    today = date(2026, 10, 2)
    days = (today - date.fromisoformat(live_fetch_start(program, "1d", None, today=today))).days
    plain_days = (today - date.fromisoformat(live_fetch_start(plain, "1d", None,
                                                               today=today))).days
    assert days >= plain_days      # at least the window SMA(200) gets


def test_a_select_expression_counts_its_widest_option():
    """RSI type by expression: wilder needs 10 x period, so the window takes it."""
    data = _base()
    data["nodes"]["rsi"]["params"]["type"] = {"expr": "'wilder'"}
    data["nodes"]["rsi"]["params"]["period"] = 14
    assert nb_compile(_graph(data)).required_lookback_bars == 141


@pytest.fixture
def window_type():
    """A test node type whose window param has no max (the cook check)."""
    name = "t_window_nomax"
    register_node(
        name=name, cat="math", desc="test window",
        params=(ParamSpec("n", "int", "n", 5, min=1),),
        inputs=registry.PortsSpec(ports=(registry.PortSpec("in"),), dynamic=False, min=1, max=1),
        impl=lambda inputs, params: inputs, lookback=lambda p: int(p["n"]),
        reads=(), writes=(), module="tests.test_code_review_fixes")
    yield name
    registry.unregister(name)


def _detail_window(window_type: str, expr: str, value: float = 50.0) -> dict:
    """_base() plus Ticker -> constant (detail @k = *value*) -> *window_type*
    whose n is *expr*: an expression that can read data."""
    return _base(extra_nodes=[
        node("c", "constant", {"value": value, "as_detail": True, "out": "@k"}),
        node("win", window_type, {"n": {"expr": expr}}),
    ], extra_wires=[wire("w8", "t", "c"), wire("w9", "c", "win")])


def test_a_window_past_what_compile_planned_fails_the_cook_loudly(df, window_type):
    """PC-1: never silent.  The expression reads @k (data), so compile
    cannot know it; n has no max, so compile planned for its default (5);
    the expression gives 50 at the cook."""
    data = _detail_window(window_type, "int(@k)")
    program = nb_compile(_graph(data))
    assert check_graph(_graph(data)).analysis.nodes["win"].lookback == 5
    with pytest.raises(CodeError) as err:
        cook_program(program, df, keep_all=True)
    assert (err.value.code, err.value.node_id, err.value.param) == ("code_type", "win", "n")
    assert "50" in err.value.message and "bars of history" in err.value.message
    # Within the plan: fine.
    cook_program(nb_compile(_graph(_detail_window(window_type, "int(@k)", 4.0))), df,
                 keep_all=True)


def test_a_param_only_window_past_the_default_is_sized_at_compile(df, window_type):
    """The same 50 as a param-only expression: compile evaluates it, so the
    window is 50 bars and the cook passes (no max needed)."""
    data = _base(extra_nodes=[node("win", window_type, {"n": {"expr": "50"}})],
                 extra_wires=[wire("w9", "t", "win")])
    check = check_graph(_graph(data))
    assert check.analysis.nodes["win"].lookback == 50
    assert check.program.required_lookback_bars == 141   # RSI(14) Wilder is longer
    cook_program(check.program, df, keep_all=True)


# ---------------------------------------------------------------------------
# Param-only expressions on window params are evaluated at compile
# ---------------------------------------------------------------------------


def _chain(ma_expr: str, ma2_expr: str = 'chi("../w/n") * 2', n: int = 30) -> dict:
    """Ticker -> w (Wrangle, spare n) -> Entry; Ticker -> ma2 (SMA, period by
    expression) and Ticker -> ma (SMA, period by expression)."""
    code = 'n = chi("n", default=3, min=1, max=100)\n@sig: bool = @close > n\n'
    return graph_data([
        _ticker(),
        node("w", "wrangle", {"n": n, "lookback_bars": 10}, code=code),
        node("ma2", "sma", {"period": {"expr": ma2_expr}, "out": "@ma2"}),
        node("ma", "sma", {"period": {"expr": ma_expr}, "out": "@ma"}),
        node("entry", "entry", {"signal": "@sig"}),
    ], [wire("w1", "t", "w"), wire("w2", "w", "entry"), wire("w3", "t", "ma2"),
        wire("w4", "t", "ma")])


def test_a_param_only_ch_chain_is_evaluated_at_compile(df):
    """ma reads ma2's expression, which reads the Wrangle's spare param:
    n = 30, ma2 = 60, ma = 65, all known before the cook."""
    check = check_graph(_graph(_chain('chi("../ma2/period") + 5')))
    assert check.program is not None, check.diagnostics
    nodes = check.analysis.nodes
    assert (nodes["ma2"].lookback, nodes["ma"].lookback) == (60, 65)
    assert check.program.required_lookback_bars == 65
    result = cook_program(check.program, df, keep_all=True)   # the cook agrees
    close = df["Close"].to_numpy(dtype=float)
    ma = np.asarray(result.streams["ma"].column("@ma"), dtype=float)
    np.testing.assert_allclose(ma[64:], pd.Series(close).rolling(65).mean().to_numpy()[64:])
    # A new spare value is a new window.
    assert check_graph(_graph(_chain('chi("../ma2/period") + 5', n=10))).program \
        .required_lookback_bars == 25


def test_an_expression_that_reads_an_attr_keeps_the_spec_max_rule(window_type):
    """@k is data: the SMA's window is sized for its period's max."""
    data = _sma({"expr": "int(@k)"})
    data["nodes"]["c"] = node("c", "constant", {"value": 50.0, "as_detail": True, "out": "@k"})
    data["wires"] = [w for w in data["wires"] if w["id"] != "w1"] + [
        wire("w5", "t", "c"), wire("w6", "c", "ma")]
    check = check_graph(_graph(data))
    assert check.program is not None, check.diagnostics
    spec_max = registry.get("sma").param("period").max
    assert check.analysis.nodes["ma"].lookback == spec_max
    # A ch() path to the attribute reads data too.
    data["nodes"]["ma"]["params"]["period"] = {"expr": 'int(ch("../c/@k"))'}
    assert check_graph(_graph(data)).analysis.nodes["ma"].lookback == spec_max


@pytest.mark.parametrize("expr", ["np.random.randint(5, 9)", "__import__('random').randint(5, 9)",
                                  "len(str(stream))"])
def test_an_expression_that_may_change_between_runs_keeps_the_spec_max_rule(expr):
    """Only names with no state are evaluated at compile, so the window never
    depends on one draw."""
    check = check_graph(_graph(_sma({"expr": expr})))
    assert check.analysis.nodes["ma"].lookback == registry.get("sma").param("period").max


def test_a_param_only_expression_that_fails_gives_the_cooks_diagnostic(df):
    """1/0 fails at every cook: compile reports the cook's own error (code,
    node, param, line, message), marked as a code failure for the bot."""
    from bot_runner import is_code_refusal

    data = _sma({"expr": "(20 +\n 1/0)"})
    program, errors = _diags(data)
    assert program is None
    assert [(d.code, d.node_id, d.param) for d in errors] == [("code_runtime", "ma", "period")]
    # The cook of the same graph (compiled without running code) fails the same way.
    unrun = check_graph(_graph(data), code_switch=False).program
    with pytest.raises(CodeError) as err:
        cook_program(unrun, df, keep_all=True)
    assert (errors[0].message, errors[0].line) == (err.value.message, err.value.line)
    with pytest.raises(Exception) as compiled:
        nb_compile(_graph(data))
    assert compiled.value.code == "code_runtime" and is_code_refusal(compiled.value)


def test_no_code_runs_at_compile_when_code_is_off(monkeypatch):
    """SL_CODE_NODES=0 and code_switch False: the widest-value rule."""
    from nodebuilder.code import runtime as code_runtime

    spec_max = registry.get("sma").param("period").max
    assert check_graph(_graph(_sma({"expr": "30"})), code_switch=False) \
        .analysis.nodes["ma"].lookback == spec_max
    monkeypatch.setattr(code_runtime, "code_enabled", lambda: False)
    monkeypatch.setattr("nodebuilder.code.code_enabled", lambda: False)
    assert check_graph(_graph(_sma({"expr": "30"}))).analysis.nodes["ma"].lookback == spec_max


def test_a_compile_evaluation_timeout_keeps_the_spec_max_rule(monkeypatch):
    from nodebuilder.code import runtime as code_runtime
    from nodebuilder.code import CodeTimeout

    def timeout(*_a, **_k):
        raise CodeTimeout("too slow", timeout_s=2.0)

    monkeypatch.setattr(code_runtime, "call_guarded", timeout)
    check = check_graph(_graph(_sma({"expr": "30"})))
    assert check.program is not None
    assert check.analysis.nodes["ma"].lookback == registry.get("sma").param("period").max


# ---------------------------------------------------------------------------
# PC-3 / PC-4: stored spare values
# ---------------------------------------------------------------------------


def _wrangle(params: dict, code: str = "@sig: bool = @close > 0\n") -> dict:
    return graph_data([
        _ticker(),
        node("w", "wrangle", params, code=code),
        node("entry", "entry", {"signal": "@sig"}),
    ], [wire("w1", "t", "w"), wire("w2", "w", "entry")])


@pytest.mark.parametrize("value, code", [
    ("inf", "param_invalid"), (float("inf"), "param_invalid"), ("nan", "param_invalid"),
    ("abc", "param_invalid"), (10**9, "param_out_of_range"), (0, "param_out_of_range"),
    (2.5, "param_invalid"),
])
def test_a_bad_lookback_bars_is_a_compile_diagnostic(value, code):
    """PC-3: "inf" crashed compile (OverflowError -> /validate 500); PC-4:
    10**9 compiled and broke every bot fetch."""
    program, errors = _diags(_wrangle({"lookback_bars": value}))
    assert program is None
    assert [(d.code, d.node_id, d.param) for d in errors] == [(code, "w", "lookback_bars")]


def test_a_bad_stored_spare_value_is_refused_at_compile():
    """PC-4: chi("n", default=3) with a stored "abc" compiled and failed the cook."""
    code = 'n = chi("n", default=3, min=1, max=10)\n@sig: bool = @close > n\n'
    _p, errors = _diags(_wrangle({"n": "abc"}, code))
    assert [(d.code, d.param) for d in errors] == [("param_invalid", "n")]
    _p, errors = _diags(_wrangle({"n": 11}, code))
    assert [(d.code, d.param) for d in errors] == [("param_out_of_range", "n")]
    program, errors = _diags(_wrangle({"n": 4}, code))
    assert program is not None and not errors


def test_validate_never_500s_on_an_infinite_lookback(client):
    data = _wrangle({"lookback_bars": "inf"})
    r = client.post("/api/nodebuilder/validate", json={"graph": data})
    assert r.status_code == 200, r.text
    assert [(d["code"], d["param"]) for d in r.json()["diagnostics"]
            if d["severity"] == "error"] == [("param_invalid", "lookback_bars")]


def test_the_live_window_never_overflows_the_calendar():
    """A huge summed lookback asks for all the history there is, never a
    date before year 1 (OverflowError on every tick)."""
    class Huge:
        required_lookback_bars = 10**9
    assert live_fetch_start(Huge(), "1d", None, today=date(2026, 10, 2)) == "1900-01-01"


# ---------------------------------------------------------------------------
# CR-10: vector spare params
# ---------------------------------------------------------------------------


def test_an_expression_on_a_vector_spare_is_refused_at_compile():
    code = 'v = chv("v", default=[1.0, 2.0])\n@sig: bool = @close > v[0]\n'
    _p, errors = _diags(_wrangle({"v": {"expr": "(1.0, 2.0)"}}, code))
    assert [(d.code, d.node_id, d.param) for d in errors] == [("param_invalid", "w", "v")]
    assert "vector" in errors[0].message


# ---------------------------------------------------------------------------
# PC-5: ch() of an expression across Ticker domains
# ---------------------------------------------------------------------------


def _domains(threshold) -> dict:
    return graph_data([
        _ticker(),
        _ticker("spy", "SPY", prefix="spy"),
        node("rs2", "rsi", {"period": {"expr": "10"}, "out": "@rs2"}),
        node("lo", "below", {"a": "@close", "threshold": threshold, "out": "@lo"}),
        node("both", "and", {"terms": ["@lo", "@lo"], "out": "@both"}),
        node("entry", "entry", {"signal": "@lo"}),
    ], [wire("w1", "t", "lo"), wire("w2", "spy", "rs2"), wire("w3", "lo", "entry")])


def test_a_ch_of_an_expression_in_a_reference_domain_is_a_compile_error():
    """PC-5: compile said fine, then every cook failed with ref_broken."""
    data = _domains({"expr": 'float(chi("../rs2/period"))'})
    data["nodes"].pop("both")
    check = check_graph(_graph(data))
    assert check.program is None
    errors = [d for d in check.diagnostics if d.severity == "error"]
    assert [(d.code, d.node_id, d.param) for d in errors] == [("ref_broken", "lo", "threshold")]
    assert "reference Ticker" in errors[0].message and errors[0].line == 1


def test_a_static_param_across_domains_still_compiles():
    data = _domains({"expr": 'float(chi("../rs2/period"))'})
    data["nodes"].pop("both")
    data["nodes"]["rs2"]["params"]["period"] = 10
    assert check_graph(_graph(data)).program is not None


# ---------------------------------------------------------------------------
# PC-10: a code block reading its own node's output through a path
# ---------------------------------------------------------------------------


def test_a_code_block_may_read_its_own_output_through_a_path(df):
    data = _base()
    data["nodes"]["rsi"]["code"] = '@double = ch("../rsi/@rsi") * 2\n'
    program = nb_compile(_graph(data))
    result = cook_program(program, df, keep_all=True)
    rsi = np.asarray(result.streams["rsi"].column("@rsi"), dtype=float)
    double = np.asarray(result.streams["rsi"].column("@double"), dtype=float)
    np.testing.assert_array_equal(double, rsi * 2)


# ---------------------------------------------------------------------------
# CR-1: a declared write the run did not make
# ---------------------------------------------------------------------------

BRANCH = "x = 1\nif @close.iloc[-1] > 1e9:\n    @sig: bool = @close > 0\n"


def test_a_branch_guarded_write_is_attr_missing_on_the_writer(df):
    program = nb_compile(_graph(_wrangle({}, BRANCH)))
    with pytest.raises(CodeError) as err:
        cook_program(program, df, keep_all=True)
    assert not isinstance(err.value, KeyError)
    assert (err.value.code, err.value.node_id, err.value.line) == ("attr_missing", "w", 3)


def test_a_branch_guarded_write_is_a_400_on_backtest(client):
    r = client.post("/api/nodebuilder/backtest", json={"graph": _wrangle({}, BRANCH), **WINDOW})
    assert r.status_code == 400, r.text
    body = r.json()
    assert (body["code"], body["node_id"]) == ("attr_missing", "w")
    assert "every path" in body["detail"]


# ---------------------------------------------------------------------------
# PC-8: parse_code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_type", [["x"], {"a": 1}, 5, None])
def test_parse_code_never_500s_on_a_half_edited_node(client, bad_type):
    graph = {"nodes": {"w": {"id": "w", "type": bad_type}}, "wires": "oops"}
    r = client.post("/api/nodebuilder/parse_code",
                    json={"code": "@x = 1.0", "context": "wrangle", "graph": graph,
                          "node_id": "w"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


@pytest.mark.parametrize("code, expected, want", [
    ("2.5", {"type": "int"}, ["code_type"]),
    ("7", {"type": "int"}, []),
    ("'x'", {"type": "number"}, ["code_type"]),
    ("'ema'", {"type": "select", "options": ["sma", "ema"]}, []),
    ("'wma'", {"type": "select", "options": ["sma", "ema"]}, ["param_invalid"]),
    ("7 if True else 2.5", {"type": "int"}, []),     # not one literal: known when it runs
])
def test_parse_code_checks_a_literal_expression_against_expected(client, code, expected, want):
    r = client.post("/api/nodebuilder/parse_code",
                    json={"code": code, "context": "expr", "expected": expected,
                          "node_id": "rsi", "param": "period"})
    assert r.status_code == 200, r.text
    got = r.json()["diagnostics"]
    assert [d["code"] for d in got] == want
    if want:
        assert (got[0]["param"], got[0]["line"], got[0]["col"]) == ("period", 1, 0)


def test_parse_code_caps_the_request_size(client):
    r = client.post("/api/nodebuilder/parse_code",
                    json={"code": "x" * (nb_route.PARSE_CODE_MAX_CHARS + 1),
                          "context": "wrangle"})
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# The Wrangle's default output
# ---------------------------------------------------------------------------


def test_a_wrangle_with_one_write_feeds_a_reader_that_names_nothing(df):
    data = graph_data([
        _ticker(),
        node("w", "wrangle", {}, code="@up: bool = @close > @close.shift(1)\n"),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "w"), wire("w2", "w", "entry")])
    program = nb_compile(_graph(data))
    assert program.entry_attr == "@up"
    result = cook_program(program, df, keep_all=True)
    up = np.asarray(result.streams["w"].column("@up"))
    close = df["Close"].to_numpy()
    np.testing.assert_array_equal(up[1:], close[1:] > close[:-1])


def test_a_wrangle_with_several_writes_needs_the_reader_to_name_one():
    data = graph_data([
        _ticker(),
        node("w", "wrangle", {}, code="@up: bool = @close > 0\n@down: bool = @close < 0\n"),
        node("entry", "entry", {}),
    ], [wire("w1", "t", "w"), wire("w2", "w", "entry")])
    program, errors = _diags(data)
    assert program is None
    assert [(d.code, d.node_id) for d in errors] == [("missing_input", "entry")]
    data["nodes"]["entry"]["params"]["signal"] = "@down"
    assert _diags(data)[0] is not None


# ---------------------------------------------------------------------------
# CR-7: a timeout inside a locked asset names the instance
# ---------------------------------------------------------------------------


class _Library:
    def __init__(self, asset: dict):
        self.asset = asset

    def __call__(self, name, version):
        import copy
        if (name, version) == (self.asset["name"], self.asset["version"]):
            return copy.deepcopy(self.asset)
        return None


def _slow_asset() -> dict:
    nodes = [
        node("in0", "subnet_input", {"port": 0}),
        node("sma", "sma", {"period": 5, "out": "@rf_ma"},
             code="import time\ntime.sleep(0.7)\n@rf_ma = @rf_ma\n"),
        node("on", "above", {"a": "@close", "b": "@rf_ma", "out": "@regime_on"}),
        node("out", "subnet_output"),
    ]
    return {
        "name": "slow_filter", "version": 1, "description": "", "stream_schema": 1,
        "interface": {"reads": [{"name": "@close", "class": "point", "dtype": "float"}],
                      "writes": [{"name": "@regime_on", "class": "point", "dtype": "bool"}]},
        "promoted": [],
        "palette": {"category": "rules", "label": "Slow Filter", "glyph": "S"},
        "network": {"nodes": {n["id"]: n for n in nodes},
                    "wires": [wire("w1", "in0", "sma"), wire("w2", "sma", "on"),
                              wire("w3", "on", "out")]},
        "created_at": "2026-10-02T09:00:00Z",
    }


def test_a_timeout_inside_a_locked_asset_names_the_instance(df):
    import time

    from nodebuilder.code import CodeTimeout, call_guarded, leaked_cooks

    data = graph_data([
        _ticker(),
        node("rf", "subnet", {}, name="my_filter", asset_ref={"name": "slow_filter", "version": 1},
             locked=True),
        node("entry", "entry", {"signal": "@regime_on"}),
    ], [wire("w1", "t", "rf"), wire("w2", "rf", "entry")])
    check = check_graph(_graph(data), resolve=_Library(_slow_asset()))
    assert check.program is not None, [(d.code, d.message) for d in check.diagnostics]
    before = leaked_cooks()
    with pytest.raises(CodeTimeout) as err:
        call_guarded(cook_program, check.program, df, timeout_s=0.2)
    assert (err.value.node_id, err.value.node_name) == ("rf", "my_filter")
    deadline = time.monotonic() + 5
    while leaked_cooks() > before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert leaked_cooks() == before


def test_compile_evaluates_on_its_own_pool_never_the_cooks(monkeypatch):
    """A /validate never waits behind a long backtest cook: compile's
    evaluations run on code.runtime.compile_pool, and a graph with no
    param-only window expression runs nothing."""
    from nodebuilder.code import runtime as code_runtime

    seen: list = []
    real = code_runtime.call_guarded

    def spy(fn, *args, **kwargs):
        seen.append(kwargs.get("executor"))
        return real(fn, *args, **kwargs)

    monkeypatch.setattr(code_runtime, "call_guarded", spy)
    assert check_graph(_graph(_sma({"expr": "30"}))).analysis.nodes["ma"].lookback == 30
    assert seen == [code_runtime.compile_pool()]
    check_graph(_graph(_sma(30)))
    assert len(seen) == 1
