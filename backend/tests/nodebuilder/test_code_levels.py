"""Code at three levels in compile and the cook (F435 W7 item 7.C, design
note docs/plans/2026-09-29-node-builder-code-nodes-design.md section 3).

- Level 1, a parameter expression: a scalar of the param's type; detail
  attributes only (a point read is attr_missing); a Series is code_type
  ("use a Wrangle"); a Ticker's symbol, interval and prefix are
  param_not_codeable (the shared rule for params read before the cook).
- Level 2, a code block: runs after its node, on the node's output stream;
  may overwrite the node's own outputs; writing an upstream name is
  attr_clash; bypass skips the node and its code together; a block where
  none can run (a terminal, a network) is param_invalid.
- Level 3, the Wrangle: merges in0 to in3; several writes; a scalar is a
  detail attribute; lookback_bars enters required_lookback_bars.
- compile: a syntax error is a coded GraphValidationError with its line and
  column (so every bot route refuses it the usual way); a code write whose
  dtype is unknown before the cook is checked against what reads it.
- The acceptance greps: exec/eval only in code/runtime.py; no "slx".

Pure cooks on synthetic frames: no data is fetched, nothing is written.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.code import CodeError
from nodebuilder.compile import check_graph, compile_with_diagnostics
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program
from nodebuilder.kernel import schema as kschema
from nodebuilder.models import Graph, GraphValidationError
from tests.nodebuilder.code_graphs import daily_frame, graph_data, node, wire

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return daily_frame(300, seed=4)


def _graph(data: dict) -> Graph:
    return Graph.model_validate(data)


def _cook(data: dict, df: pd.DataFrame):
    program = nb_compile(_graph(data))
    return program, cook_program(program, df, keep_all=True)


def _col(result, nid: str, name: str) -> np.ndarray:
    return np.asarray(result.streams[nid].column(name), dtype=float)


def _codes(diagnostics) -> list[str]:
    return [d.code for d in diagnostics]


def _rsi_data(period, *, extra_nodes=(), extra_wires=(), rsi_params=None, code=None) -> dict:
    """Ticker -> RSI (period *period*) -> below 30 -> Entry, plus a plain
    RSI(9) next to it for comparisons."""
    params = {"period": period, **(rsi_params or {})}
    rsi = node("rsi", "rsi", params, code=code) if code is not None else node("rsi", "rsi", params)
    nodes = [
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        rsi,
        node("ref9", "rsi", {"period": 9, "out": "@rsi9"}),
        node("lo", "below", {"a": "@rsi", "threshold": 30, "out": "@lo"}),
        node("entry", "entry", {}),
        *extra_nodes,
    ]
    wires = [wire("w1", "t", "rsi"), wire("w2", "t", "ref9"), wire("w3", "rsi", "lo"),
             wire("w4", "lo", "entry"), *extra_wires]
    return graph_data(nodes, wires)


# ---------------------------------------------------------------------------
# Level 1: parameter expressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["3 * 3", "9.0", "int('9')", "max(2, 9)"])
def test_an_expression_gives_the_param_a_scalar_of_its_type(df, source):
    _program, result = _cook(_rsi_data({"expr": source}), df)
    np.testing.assert_array_equal(_col(result, "rsi", "@rsi"), _col(result, "ref9", "@rsi9"))


def test_an_int_param_refuses_a_fraction_at_cook(df):
    """The cook refuses 7.5 for an int period.  The expression reads only
    params, so compile evaluates it to size the window and gives the cook's
    own diagnostic first (kernel.params.static_windows)."""
    data = _rsi_data({"expr": "7.5"})
    program = check_graph(_graph(data), code_switch=False).program   # runs no code
    with pytest.raises(CodeError) as err:
        cook_program(program, df)
    assert err.value.code == "code_type"
    assert (err.value.node_id, err.value.param) == ("rsi", "period")
    with pytest.raises(GraphValidationError) as compiled:
        nb_compile(_graph(data))
    assert (compiled.value.code, compiled.value.node_id, compiled.value.param) == \
        ("code_type", "rsi", "period")
    assert str(compiled.value) == str(err.value)


def test_a_point_read_in_an_expression_is_attr_missing_with_the_wrangle_hint(df):
    program = nb_compile(_graph(_rsi_data({"expr": "@close"})))
    with pytest.raises(CodeError) as err:
        cook_program(program, df)
    assert err.value.code == "attr_missing"
    assert "use a Wrangle for per-bar logic" in str(err.value)
    assert (err.value.node_id, err.value.param, err.value.line) == ("rsi", "period", 1)


def test_a_series_result_is_code_type_with_the_wrangle_hint(df):
    program = nb_compile(_graph(_rsi_data({"expr": "pd.Series([1.0, 2.0])"})))
    with pytest.raises(CodeError) as err:
        cook_program(program, df)
    assert err.value.code == "code_type"
    assert "use a Wrangle for per-bar logic" in str(err.value)


def test_an_expression_reads_a_detail_attribute_of_its_input(df):
    """A constant written as a detail value flows on the stream; the RSI's
    period expression reads it."""
    data = _rsi_data({"expr": "int(@th)"}, rsi_params={"source": "@close"})
    data["nodes"]["c"] = node("c", "constant", {"value": 9, "as_detail": True, "out": "@th"})
    data["wires"] = [w for w in data["wires"] if w["id"] != "w1"] + [
        wire("wc", "t", "c"), wire("w1", "c", "rsi")]
    _program, result = _cook(data, df)
    np.testing.assert_array_equal(_col(result, "rsi", "@rsi"), _col(result, "ref9", "@rsi9"))


@pytest.mark.parametrize("param", ["symbol", "interval", "prefix"])
def test_a_ticker_param_cannot_hold_code(param):
    data = _rsi_data(14)
    data["nodes"]["t"]["params"][param] = {"expr": "'AAPL'"}
    program, diagnostics = compile_with_diagnostics(_graph(data))
    assert program is None
    # One shared rule for every param read before the cook (W7 fix PC-2);
    # ticker_param_not_codeable stays registered as its old name.
    hits = [d for d in diagnostics if d.code == "param_not_codeable"]
    assert [(d.node_id, d.param) for d in hits] == [("t", param)]
    assert "read before the cook, so it cannot be an expression" in hits[0].message
    # Reported once: the kernel does not add its own param_invalid for it.
    assert not [d for d in diagnostics if d.node_id == "t" and d.code == "param_invalid"]
    with pytest.raises(GraphValidationError) as err:
        nb_compile(_graph(data))
    assert err.value.code == "param_not_codeable"


# ---------------------------------------------------------------------------
# Level 2: a code block on a built-in node
# ---------------------------------------------------------------------------


def test_a_code_block_runs_after_its_node_on_its_output(df):
    code = '@rsi_smooth = sl.ema(@rsi, chi("smooth", default=3))'
    data = _rsi_data(14, code=code, extra_nodes=[
        node("ema", "ma", {"period": 3, "type": "ema", "source": "@rsi", "out": "@ema3"})],
        extra_wires=[wire("w5", "rsi", "ema")])
    _program, result = _cook(data, df)
    np.testing.assert_allclose(_col(result, "rsi", "@rsi_smooth"), _col(result, "ema", "@ema3"),
                               equal_nan=True)
    # The write is on the node's output schema before any cook (dtype any).
    check = check_graph(_graph(data))
    info = check.streams["rsi"].lookup("@rsi_smooth")
    assert (info.kind, info.dtype, info.written_by) == ("point", "any", "rsi")


def test_a_code_block_reads_its_own_params_and_writes_a_detail(df):
    data = _rsi_data(14, code='@p = chi("period") * 2')
    _program, result = _cook(data, df)
    stream = result.streams["rsi"]
    assert stream.kind("@p") == "detail" and stream.value("@p") == 28


def test_a_code_block_may_overwrite_its_own_output(df):
    data = _rsi_data(14, code="@rsi = @rsi * 0 + 50.0")
    _program, result = _cook(data, df)
    values = _col(result, "rsi", "@rsi")
    assert np.all(values[~np.isnan(values)] == 50.0)
    assert not np.isnan(values).all()


def test_a_code_block_writing_an_upstream_name_is_attr_clash():
    data = _rsi_data(14, code="@close = @close * 2")
    program, diagnostics = compile_with_diagnostics(_graph(data))
    assert program is None
    assert [(d.code, d.node_id) for d in diagnostics if d.severity == "error"] == [
        ("attr_clash", "rsi")]


def test_bypass_skips_the_node_and_its_code_block(df):
    data = _rsi_data(14, code="x = 1 / 0\n@z = @close")
    data["nodes"]["rsi"]["bypass"] = True
    data["nodes"]["lo"]["params"]["a"] = "@close"
    _program, result = _cook(data, df)  # the division never runs
    assert "@z" not in result.streams["rsi"].names()


def test_a_code_block_on_a_terminal_is_refused():
    data = _rsi_data(14)
    data["nodes"]["entry"]["code"] = "@x = 1.0"
    _program, diagnostics = compile_with_diagnostics(_graph(data))
    assert ("param_invalid", "entry") in [(d.code, d.node_id) for d in diagnostics]


def test_a_code_block_on_a_network_node_is_refused():
    data = _rsi_data(14)
    data["nodes"]["net"] = node("net", "subnet", {}, code="@x = 1.0")
    data["nodes"]["inner"] = node("inner", "constant", {"value": 1.0, "out": "@one"}, parent="net")
    _program, diagnostics = compile_with_diagnostics(_graph(data))
    assert ("param_invalid", "net") in [(d.code, d.node_id) for d in diagnostics]


# ---------------------------------------------------------------------------
# Level 3: the Wrangle
# ---------------------------------------------------------------------------


def _wrangle_data(code: str, *, inputs: int = 1, params=None, entry_signal="@sig") -> dict:
    nodes = [node("t", "ticker", {"symbol": "AAPL", "interval": "1d"})]
    wires = []
    for k in range(inputs):
        nodes.append(node(f"c{k}", "constant", {"value": float(k + 1), "out": f"@c{k}"}))
        wires.append(wire(f"wt{k}", "t", f"c{k}"))
        wires.append(wire(f"wc{k}", f"c{k}", "w", f"in{k}"))
    nodes.append(node("w", "wrangle", params or {}, code=code))
    nodes.append(node("entry", "entry", {"signal": entry_signal}))
    wires.append(wire("we", "w", "entry"))
    return graph_data(nodes, wires)


def test_a_wrangle_merges_in0_to_in3(df):
    code = ("@total = @c0 + @c1 + @c2 + @c3\n"
            "@sig: bool = @total > 0\n"
            "@n_bars = len(@close)\n")
    _program, result = _cook(_wrangle_data(code, inputs=4), df)
    stream = result.streams["w"]
    np.testing.assert_array_equal(_col(result, "w", "@total"), np.full(len(df), 10.0))
    assert stream.dtype("@sig") == "bool"
    assert stream.kind("@n_bars") == "detail" and stream.value("@n_bars") == len(df)
    assert stream.written_by["@total"] == "w"


def test_a_wrangle_takes_four_inputs_at_most():
    data = _wrangle_data("@sig: bool = @close > 0", inputs=4)
    data["nodes"]["c4"] = node("c4", "constant", {"value": 5.0, "out": "@c4"})
    data["wires"] += [wire("wt4", "t", "c4"), wire("wc4", "c4", "w", "in4")]
    _program, diagnostics = compile_with_diagnostics(_graph(data))
    assert ("port_unknown", "w") in [(d.code, d.node_id) for d in diagnostics]


def test_a_wrangle_without_code_passes_its_input_on(df):
    data = _wrangle_data("", inputs=1, entry_signal="@sig")
    data["nodes"]["w"].pop("code")
    data["nodes"]["s"] = node("s", "above", {"a": "@c0", "threshold": 0, "out": "@sig"})
    data["wires"] = [w for w in data["wires"] if w["id"] != "we"] + [
        wire("ws", "w", "s"), wire("we", "s", "entry")]
    _program, result = _cook(data, df)
    assert "@c0" in result.streams["w"].names()


@pytest.mark.parametrize("params, want", [({}, 500), ({"lookback_bars": 50}, 50)])
def test_lookback_bars_enters_the_program_lookback(params, want):
    program = nb_compile(_graph(_wrangle_data("@sig: bool = @close > 0", params=params)))
    assert program.required_lookback_bars == want
    assert program.has_code


def test_a_program_without_code_has_no_code():
    assert not nb_compile(_graph(_rsi_data(14))).has_code


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


def test_a_syntax_error_is_a_coded_graph_error_with_its_place():
    data = _wrangle_data("x = 1\ny = (2 +\n@sig: bool = @close > 0")
    with pytest.raises(GraphValidationError) as err:
        nb_compile(_graph(data))
    assert err.value.code == "code_syntax" and err.value.node_id == "w"
    assert err.value.line == 2
    _program, diagnostics = compile_with_diagnostics(_graph(data))
    (d,) = [d for d in diagnostics if d.code == "code_syntax"]
    assert (d.node_id, d.line) == ("w", 2) and d.col is not None
    # The Entry below misses @sig only because the code is broken: not
    # reported on top of the syntax error.
    assert [d.code for d in diagnostics if d.severity == "error"] == ["code_syntax"]


def test_a_broken_expression_is_reported_once():
    data = _rsi_data({"expr": "7 if"})
    _program, diagnostics = compile_with_diagnostics(_graph(data))
    assert [(d.code, d.node_id, d.param) for d in diagnostics if d.severity == "error"] == [
        ("code_syntax", "rsi", "period")]


def test_an_unannotated_overwrite_keeps_its_nodes_dtype_and_the_cook_holds_it(df):
    data = _rsi_data(14, code="@rsi = @rsi * 0 + 50.0")
    assert check_graph(_graph(data)).streams["rsi"].lookup("@rsi").dtype == "float"
    data = _rsi_data(14)
    data["nodes"]["lo"]["code"] = "@lo = @lo * 1.0"  # the below node writes bool
    program = nb_compile(_graph(data))
    with pytest.raises(CodeError) as err:
        cook_program(program, df)
    assert (err.value.code, err.value.node_id) == ("attr_type", "lo")


def test_a_runtime_error_points_at_the_users_line(df):
    data = _wrangle_data("x = 1\ny = 2\nz = x / (y - 2)\n@sig: bool = @close > 0")
    program = nb_compile(_graph(data))
    with pytest.raises(CodeError) as err:
        cook_program(program, df)
    e = err.value
    assert (e.code, e.node_id, e.line, e.col) == ("code_runtime", "w", 3, 4)
    assert "ZeroDivisionError" in str(e)


def test_an_unannotated_write_read_as_a_signal_is_checked_at_cook(df, monkeypatch):
    """Design note 4.3: a reader that needs bool gets no compile error for a
    code write of dtype any; the cook checks the real value and reports
    attr_type on the writer.  The kernel's read check is relaxed here to
    accept "any" (see the 7.C report, Needs from others)."""
    real = kschema._type_ok
    monkeypatch.setattr(kschema, "_type_ok",
                        lambda want, info: info.dtype == "any" or real(want, info))
    good = nb_compile(_graph(_wrangle_data("@sig = @close > 0")))
    cook_program(good, df)  # a bool column: fine
    bad = nb_compile(_graph(_wrangle_data("@sig = @close * 1.0")))
    with pytest.raises(CodeError) as err:
        cook_program(bad, df)
    assert (err.value.code, err.value.node_id) == ("attr_type", "w")
    assert "@sig: bool" in str(err.value)


def test_a_graph_without_code_compiles_to_the_same_steps_as_before():
    """The W7 compile changes nothing for a graph with no code: the steps
    are the kernel's own (no code impl, no plan)."""
    program = nb_compile(_graph(_rsi_data(14)))
    assert all(s.plan is None and s.param_hook is None for s in program.steps)
    from nodebuilder.kernel.registry import get
    rsi = next(s for s in program.steps if s.node_id == "rsi")
    assert rsi.impl is get("rsi").impl


def test_a_code_step_keeps_its_node_types_impl_underneath():
    program = nb_compile(_graph(_rsi_data(14, code="@p = 1.0")))
    rsi = next(s for s in program.steps if s.node_id == "rsi")
    assert rsi.impl.__name__.startswith("code_")
    assert "@p" in rsi.writes


def test_compile_never_runs_code(tmp_path):
    marker = tmp_path / "ran.txt"
    code = f"open({str(marker)!r}, 'w').write('x')\n@sig: bool = @close > 0"
    nb_compile(_graph(_wrangle_data(code)))
    check_graph(_graph(_wrangle_data(code)))
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Acceptance greps (plan W7 "Acceptance (scripted)")
# ---------------------------------------------------------------------------


def _python_files(*roots: Path):
    for root in roots:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def test_one_execution_path_exec_and_eval_only_in_the_code_runtime():
    pattern = re.compile(r"\b(exec|eval)\(")
    hits = []
    for path in _python_files(BACKEND / "nodebuilder", BACKEND / "bot_runner.py",
                              BACKEND / "routes"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append((path.relative_to(BACKEND).as_posix(), n))
    assert hits and {p for p, _n in hits} == {"nodebuilder/code/runtime.py"}, hits


def test_no_slx_anywhere():
    hits = []
    for root in (BACKEND / "nodebuilder", REPO / "frontend" / "src" / "features" / "nodebuilder"):
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if re.search("slx", text, re.IGNORECASE):
                hits.append(str(path.relative_to(REPO)))
    assert hits == []


def test_the_wrangle_catalog_entry():
    from nodebuilder.nodes import get_node

    entry = get_node("wrangle").to_json()
    assert entry["cat"] == "code"
    assert entry["desc"] == "Code that reads the merged input stream and writes new attributes."
    assert entry["inputs"] == {"ports": [{"label": "in0"}, {"label": "in1", "optional": True}],
                               "dynamic": True, "min": 1, "max": 4}
    assert entry["params"] == []
    copy.deepcopy(entry)  # plain JSON


def test_a_size_reading_an_unannotated_write_refuses_true_false_at_cook(df, monkeypatch):
    """Like the Size terminal's own compile check for a known dtype: once
    the kernel accepts a read of dtype any, the cook refuses a true/false
    column as a size (attr_type on the writer)."""
    real = kschema._type_ok
    monkeypatch.setattr(kschema, "_type_ok",
                        lambda want, info: info.dtype == "any" or real(want, info))
    data = _wrangle_data("@sig: bool = @close > 0\n@frac = @close > 0")
    data["nodes"]["size"] = node("size", "size", {"value": "@frac"})
    data["wires"].append(wire("wsz", "w", "size"))
    program = nb_compile(_graph(data))
    with pytest.raises(CodeError) as err:
        cook_program(program, df, keep={"size"})
    assert (err.value.code, err.value.node_id) == ("attr_type", "w")
    assert "size or a stop" in str(err.value)
    data["nodes"]["w"]["code"] = "@sig: bool = @close > 0\n@frac = @close * 0 + 0.5"
    cook_program(nb_compile(_graph(data)), df, keep={"size"})  # a number: fine
