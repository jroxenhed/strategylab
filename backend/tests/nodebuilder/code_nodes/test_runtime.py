"""The code runtime: run(), the stream proxy, ch*() at run time, result
checks, and exception-to-diagnostic mapping (F435 W7 item 7.A, design
note 4.4 to 4.8).  Synthetic streams only; no graph, no bot.
"""
from __future__ import annotations

import ast

import numpy as np
import pandas as pd
import pytest

from nodebuilder.code import (
    AttrMissingError,
    CodeDiagnostic,
    CodeError,
    CodeTimeout,
    pause_reason,
    prepare,
    run,
)
from nodebuilder.code.errors import (
    ATTR_CLASH,
    ATTR_DYNAMIC,
    ATTR_MISSING,
    CH_DYNAMIC,
    CODE_DISABLED,
    CODE_RUNTIME,
    CODE_SYNTAX,
    CODE_TYPE,
    PARAM_INVALID,
    REF_BROKEN,
)
from nodebuilder.kernel.stream import ColumnStore, Stream, merge_streams

N = 30


@pytest.fixture
def stream() -> Stream:
    idx = pd.date_range("2024-01-01", periods=N, freq="D", tz="America/New_York")
    store = ColumnStore(idx)
    close = 100 + np.cumsum(np.random.default_rng(3).normal(0, 1, N))
    s = Stream.empty(store)
    s = s.with_point("@close", close, "/t", "float")
    s = s.with_point("@high", close + 1, "/t", "float")
    s = s.with_point("@low", close - 1, "/t", "float")
    s = s.with_point("@up", close > 100, "/c", "bool")
    s = s.with_detail("@stop_pct", 2.5, "/s")
    return s


def _wrangle(src: str, stream: Stream, node: str = "n_w", **kwargs):
    p = prepare(src, "wrangle", node)
    assert p.ok, p.diagnostics
    return run(p, stream, **kwargs)


def _fail(src: str, stream: Stream, context: str = "wrangle", **kwargs) -> CodeDiagnostic:
    p = prepare(src, context, "n_w", param="period" if context == "expr" else None)
    assert p.ok, p.diagnostics
    with pytest.raises(CodeError) as info:
        run(p, stream, **kwargs)
    return info.value.diagnostic


# ---------------------------------------------------------------------------
# Exceptions become diagnostics at the user's line and column
# ---------------------------------------------------------------------------


def test_zero_division_on_line_3_is_code_runtime_at_3_4(stream):
    d = _fail("a = 1\nb = 2\nx = 1 / 0\n", stream)
    assert (d.code, d.line, d.col) == (CODE_RUNTIME, 3, 4)
    assert d.message == "ZeroDivisionError: division by zero"
    assert d.node_id == "n_w"
    assert (d.end_line, d.end_col) == (3, 9)


def test_an_error_inside_numpy_points_at_the_calling_line(stream):
    d = _fail("a = 1\ny = np.ones(3) + np.ones(4)\n", stream)
    assert (d.code, d.line, d.col) == (CODE_RUNTIME, 2, 4)
    assert d.message.startswith("ValueError: operands could not be broadcast")


def test_an_error_deep_inside_pandas_points_at_the_calling_line(stream):
    d = _fail("a = 1\n\nr = pd.Series([1.0, 2.0]).rolling(-1).mean()\n", stream)
    assert (d.line, d.col) == (3, 4)
    assert d.message.startswith("ValueError:")


def test_an_error_inside_a_user_def_points_inside_the_def(stream):
    src = "def f(a):\n    return a / 0\n\ny = f(1)\n"
    d = _fail(src, stream)
    assert (d.line, d.col) == (2, 11)


def test_an_error_inside_a_lambda_or_comprehension_points_at_it(stream):
    d = _fail("g = lambda v: v[5]\nx = g([1])\n", stream)
    assert (d.line, d.col) == (1, 14)
    d = _fail("x = [1 / (i - 2) for i in range(5)]\n", stream)
    assert (d.line, d.col) == (1, 5)


def test_a_column_after_a_rewritten_attr_maps_back_to_the_users_text(stream):
    d = _fail("@x = @close * undefined_name\n", stream)
    assert d.code == CODE_RUNTIME
    assert (d.line, d.col, d.end_col) == (1, 14, 28)
    assert "NameError" in d.message


def test_runtime_columns_are_characters_not_bytes(stream):
    # Each é is two bytes in UTF-8; the column must count it once.
    d = _fail('s = "éé"; x = 1 / 0\n', stream)
    assert (d.line, d.col) == (1, 14)


def test_a_syntax_error_maps_to_offset_minus_one():
    src = "a = 1\nx = 1 +* 2\n"
    with pytest.raises(SyntaxError) as info:
        ast.parse(src)
    p = prepare(src, "wrangle", "n_w")
    assert not p.ok and p.code_obj is None
    (d,) = p.diagnostics
    assert (d.code, d.line, d.col) == (CODE_SYNTAX, info.value.lineno, info.value.offset - 1)
    assert (d.line, d.col) == (2, 7)


def test_a_syntax_error_after_an_attr_maps_back_through_the_sugar():
    # Python points at the *; the rewrite moved it from column 8 to 17.
    src = "@x = 1 +* @close\n"
    p = prepare(src, "wrangle", "n_w")
    (d,) = p.diagnostics
    assert (d.code, d.line, d.col) == (CODE_SYNTAX, 1, src.index("*"))
    assert src.index("*") == 8


def test_a_compile_phase_syntax_error_counts_characters():
    # 'return' outside a function is found by compile(), which reports UTF-8
    # byte offsets; the diagnostic still counts characters.
    p = prepare('s = "é"; return 1', "wrangle", "n_w")
    (d,) = p.diagnostics
    assert (d.code, d.line, d.col) == (CODE_SYNTAX, 1, 9)


def test_a_bad_sigil_is_code_syntax_at_the_at_sign():
    p = prepare("a = 1\nx = @ close", "wrangle", "n_w")
    (d,) = p.diagnostics
    assert (d.code, d.line, d.col, d.node_id) == (CODE_SYNTAX, 2, 4, "n_w")


def test_source_over_8_kb_is_code_limit():
    p = prepare("x = 1\n" * 1400, "wrangle", "n_w")     # 8,400 bytes
    assert [d.code for d in p.diagnostics] == ["code_limit"]
    assert prepare("x = 1\n" * 1365, "wrangle", "n_w").ok    # 8,190 bytes


def test_an_expression_must_be_one_expression():
    p = prepare("x = 1", "expr", "n_rsi", param="period")
    assert [d.code for d in p.diagnostics] == [CODE_SYNTAX]
    assert p.diagnostics[0].param == "period"


def test_run_refuses_a_snippet_that_did_not_compile(stream):
    p = prepare("x = (", "wrangle", "n_w")
    with pytest.raises(CodeError) as info:
        run(p, stream)
    assert info.value.code == CODE_SYNTAX


def test_system_exit_in_user_code_is_a_failed_cook(stream):
    d = _fail("import sys\nsys.exit(3)", stream)
    assert (d.code, d.line) == (CODE_RUNTIME, 2)
    assert d.message == "SystemExit: 3"


@pytest.mark.parametrize("raise_line, name", [
    ("raise KeyboardInterrupt", "KeyboardInterrupt"),
    ("raise GeneratorExit", "GeneratorExit"),
    ("import asyncio\nraise asyncio.CancelledError()", "CancelledError"),
])
def test_any_base_exception_from_user_code_is_code_runtime_on_a_worker_thread(stream, raise_line,
                                                                              name):
    """W7 fix CR-4: run() catches every exception from user code on a worker
    thread (where bots and routes cook), BaseException included."""
    import concurrent.futures

    p = prepare("x = 1\n" + raise_line, "wrangle", "n_w")
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        with pytest.raises(CodeError) as info:
            pool.submit(run, p, stream).result()
    d = info.value.diagnostic
    assert d.code == CODE_RUNTIME and d.message.startswith(name)
    assert d.line == raise_line.count("\n") + 2


async def test_a_cancelled_error_from_user_code_reaches_the_bot_as_a_code_failure(stream):
    """Under the bot's guard, user code raising CancelledError is a code
    failure (code_runtime), never the awaiting task's own cancellation."""
    from nodebuilder.code import await_guarded

    p = prepare("import asyncio\nraise asyncio.CancelledError()", "wrangle", "n_w")
    with pytest.raises(CodeError) as info:
        await await_guarded(run, p, stream, timeout_s=5)
    assert info.value.code == CODE_RUNTIME


def test_a_keyboard_interrupt_on_the_main_thread_still_stops_a_script(stream):
    p = prepare("raise KeyboardInterrupt", "wrangle", "n_w")
    with pytest.raises(KeyboardInterrupt):
        run(p, stream)


def test_a_declared_write_the_run_did_not_make_is_attr_missing_at_the_write(stream):
    """W7 fix CR-1: a write in an untaken branch is a code diagnostic on the
    writer at the line of the write, never a bare KeyError later."""
    src = "x = 1\nif @close.iloc[-1] > 1e9:\n    @sig: bool = @close > 0\n"
    d = _fail(src, stream)
    assert (d.code, d.node_id, d.line, d.col) == ("attr_missing", "n_w", 3, 4)
    assert "write it on every path" in d.message.lower() and "@sig = False" in d.message
    # Written on every path: fine.
    res = _wrangle("@sig: bool = @close > 1e9\nif @close.iloc[-1] > 1e9:\n    @sig = @close > 0\n",
                   stream)
    assert [w.name for w in res.written] == ["@sig"]


def test_a_missing_float_write_suggests_nan(stream):
    d = _fail("if False:\n    @level = 1.0\n", stream)
    assert d.code == "attr_missing" and "@level = np.nan" in d.message


def test_an_untyped_ch_of_lookback_bars_reads_it(stream):
    """W7 fix CR-8: every code node has lookback_bars, so ch() reads it."""
    p = prepare("n = ch('lookback_bars')\n@x = float(n)\n", "wrangle", "n_w")
    assert p.ok, p.diagnostics
    assert run(p, stream).written[0].value == 500.0
    assert run(p, stream, params={"lookback_bars": 60}).written[0].value == 60.0


def test_an_oversized_snippet_is_not_cached():
    """W7 fix CR-9: a code_limit result is never kept in the prepare cache."""
    from nodebuilder.code import runtime

    text = "x = 1\n" * 1400 + "# one\n"
    p = prepare(text, "wrangle", "n_big")
    assert [d.code for d in p.diagnostics] == ["code_limit"]
    assert all(key[0] != p.sha256 for key in list(runtime._cache))
    assert prepare("x = 1\n", "wrangle", "n_small").sha256 in {k[0] for k in runtime._cache}


@pytest.mark.parametrize("value, want", [
    ("inf", 500), (float("inf"), 500), ("nan", 500), (1e999, 500), ("abc", 500),
    (10**9, 100_000), (0, 1), (-5, 1), (260, 260), ("260", 260),
])
def test_effective_lookback_never_raises_and_stays_in_range(value, want):
    """W7 fix PC-3: compile reads lookback_bars through this; a bad value
    must never crash /validate or /backtest."""
    p = prepare("@x = 1.0", "wrangle", "n_w")
    assert p.effective_lookback({"lookback_bars": value}) == want


def test_the_error_does_not_chain_the_users_frames(stream):
    p = prepare("big = np.zeros(10)\nx = 1 / 0", "wrangle", "n_w")
    with pytest.raises(CodeError) as info:
        run(p, stream)
    assert info.value.__context__ is None and info.value.__cause__ is None


# ---------------------------------------------------------------------------
# Reading the stream
# ---------------------------------------------------------------------------


def test_reads_give_series_on_the_bar_index_and_detail_values(stream):
    res = _wrangle("@a = @close + @stop_pct\n@b = @up\n@c = @stop_pct", stream)
    a = res.stream.series("@a")
    np.testing.assert_array_equal(a.to_numpy(), stream.column("@close") + 2.5)
    assert res.stream.dtype("@b") == "bool"
    assert res.stream.kind("@c") == "detail" and res.stream.value("@c") == 2.5


def test_an_in_place_edit_of_a_read_column_changes_only_the_codes_copy(stream):
    before = stream.column("@close").copy()
    res = _wrangle("s = @close\ns.iloc[0] = 0.0\ns[s > 101] = 1.0\n@y = s\n@z = @close", stream)
    np.testing.assert_array_equal(stream.column("@close"), before)
    assert res.stream.column("@y")[0] == 0.0
    np.testing.assert_array_equal(res.stream.column("@z"), before)


def test_a_missing_attribute_is_attr_missing_at_its_sigil(stream):
    d = _fail("a = 1\n@y = @nope + 1", stream)
    assert (d.code, d.line, d.col) == (ATTR_MISSING, 2, 5)


def test_attr_missing_is_a_key_error_user_code_can_catch(stream):
    res = _wrangle("try:\n    v = @nope\nexcept KeyError:\n    v = 7\n@y = v\n"
                   "@z = 1 if 'close' in stream else 0\n@w = stream.get('nope', 5)", stream)
    assert [res.stream.value(n) for n in ("@y", "@z", "@w")] == [7, 1, 5]


def test_a_value_the_code_wrote_can_be_read_back(stream):
    res = _wrangle("@a = @close * 2\n@b = @a + 1\n@n = 3\n@m = @n * 2", stream)
    np.testing.assert_array_equal(res.stream.column("@b"), stream.column("@close") * 2 + 1)
    assert res.stream.value("@m") == 6


def test_reading_a_hidden_clash_is_attr_clash(stream):
    other = Stream.empty(stream.store).with_point("@close", np.zeros(N), "/t2", "float")
    merged = merge_streams([stream, other])
    d = _fail("@y = @close", merged)
    assert (d.code, d.line, d.col) == (ATTR_CLASH, 1, 5)


# ---------------------------------------------------------------------------
# Writing the stream
# ---------------------------------------------------------------------------


def test_what_each_kind_of_written_value_becomes(stream):
    src = (
        "@f = @close * 1\n"
        "@i = pd.Series(np.arange(len(@close)), index=@close.index)\n"
        "@b = @close > 100\n"
        "@w = np.where(@up, 1.0, 0.0)\n"
        "@wb = np.where(@up, True, False)\n"
        "@d_int = 3\n"
        "@d_float = np.float64(1.5)\n"
        "@d_bool = np.bool_(True)\n"
        "@d_str = 'x'\n"
        "@nullable = pd.Series([1, None] * 15, index=@close.index, dtype='Int64')\n"
    )
    res = _wrangle(src, stream)
    kinds = {w.name: (w.kind, w.dtype) for w in res.written}
    assert kinds == {
        "@f": ("point", "float"), "@i": ("point", "float"), "@b": ("point", "bool"),
        "@w": ("point", "float"), "@wb": ("point", "bool"),
        "@d_int": ("detail", "int"), "@d_float": ("detail", "float"),
        "@d_bool": ("detail", "bool"), "@d_str": ("detail", "str"),
        "@nullable": ("point", "float"),
    }
    out = res.stream
    assert out.column("@i").dtype == np.float64
    assert np.isnan(out.column("@nullable")[1])
    assert type(out.value("@d_float")) is float and type(out.value("@d_bool")) is bool
    # The writer is the node.
    assert {out.written_by[n] for n in kinds} == {"n_w"}
    # The input attributes flow on.
    assert "@close" in out and out.written_by["@close"] == "/t"


def test_annotations_fix_the_dtype(stream):
    res = _wrangle("@x: float = @up\n@y: float = 3", stream)
    assert res.stream.dtype("@x") == "float" and res.stream.column("@x").dtype == np.float64
    assert res.stream.value("@y") == 3.0 and res.stream.dtype("@y") == "float"
    d = _fail("@z: bool = @close * 1", stream)
    assert (d.code, d.line) == (CODE_TYPE, 1)


@pytest.mark.parametrize("src,needle", [
    ("@x = pd.Series([1.0, 2.0])", "aligned to the bar index"),
    ("@x = @close.iloc[1:]", "aligned to the bar index"),
    ("@x = np.zeros(3)", "1-D with one value per bar"),
    ("@x = np.zeros((30, 2))", "1-D with one value per bar"),
    ("@x = pd.Series(['a'] * 30, index=@close.index)", "numbers or true/false"),
    ("@x = pd.Series(pd.date_range('2024-01-01', periods=30), index=@close.index)",
     "numbers or true/false"),
    ("@x = pd.DataFrame({'a': @close})", "not a DataFrame"),
    ("@x = [1.0] * 30", "write a Series"),
    ("@x = None", "write a Series"),
    ("@x = pd.Series([True, None] * 15, index=@close.index, dtype='boolean')",
     "cannot hold missing values"),
])
def test_bad_writes_are_code_type_at_the_write(stream, src, needle):
    d = _fail(src, stream)
    assert (d.code, d.line, d.col) == (CODE_TYPE, 1, 0)
    assert needle in d.message


def test_a_write_the_scan_did_not_see_is_attr_dynamic(stream):
    d = _fail("a = 1\nstream.__setitem__('x', 1)", stream)
    assert (d.code, d.line) == (ATTR_DYNAMIC, 2)


def test_writing_an_upstream_name_is_attr_clash_unless_allowed(stream):
    d = _fail("@close = @close * 2", stream)
    assert (d.code, d.line, d.col) == (ATTR_CLASH, 1, 0)
    res = _wrangle("@close = @close * 2", stream, allow_shadow=True)
    assert res.stream.written_by["@close"] == "n_w"


def test_a_code_block_may_overwrite_its_own_outputs(stream):
    # The node's own output stream: @rsi written by n_rsi itself.
    own = stream.with_point("@rsi", np.full(N, 50.0), "n_rsi", "float")
    p = prepare("@rsi = @rsi + 1\n@rsi_flag = @rsi > 50", "node_code", "n_rsi",
                builtin_params=("period",))
    res = run(p, own)
    assert res.stream.column("@rsi")[0] == 51.0
    assert res.stream.written_by["@rsi"] == "n_rsi"


def test_deleting_an_attribute_is_refused(stream):
    p = prepare("del @close", "wrangle", "n_w")
    assert p.ok
    with pytest.raises(CodeError) as info:
        run(p, stream)
    assert info.value.code == CODE_RUNTIME and "cannot be deleted" in info.value.message


# ---------------------------------------------------------------------------
# The namespace
# ---------------------------------------------------------------------------


def test_the_namespace_is_fresh_for_every_run(stream):
    src = "count = globals().get('count', 0) + 1\n@n = count\nsl.rsi = None\n@r = sl.ema(@close, 3)"
    p = prepare(src, "wrangle", "n_w")
    for _ in range(2):
        res = run(p, stream)
        assert res.stream.value("@n") == 1
    import nodebuilder.code.sl as sl_module
    assert callable(sl_module.rsi)


def test_full_python_imports_and_the_listed_modules_work(stream):
    src = ("import statistics\n@m = statistics.mean([1, 2, 3])\n"
           "@t = ta.sma(@close, length=5)\n@q = math.sqrt(16)\n")
    res = _wrangle(src, stream)
    assert res.stream.value("@m") == 2 and res.stream.value("@q") == 4.0
    np.testing.assert_allclose(res.stream.column("@t")[4:],
                               pd.Series(stream.column("@close")).rolling(5).mean()[4:])


def test_run_needs_the_stream_for_code_blocks_and_wrangles():
    p = prepare("@x = 1", "wrangle", "n_w")
    with pytest.raises(ValueError):
        run(p, None)


def test_run_refuses_when_code_is_turned_off(stream, monkeypatch):
    monkeypatch.setenv("SL_CODE_NODES", "0")
    p = prepare("@x = 1", "wrangle", "n_w")
    with pytest.raises(CodeError) as info:
        run(p, stream)
    assert info.value.code == CODE_DISABLED


# ---------------------------------------------------------------------------
# ch*() at run time
# ---------------------------------------------------------------------------


def test_bare_names_read_params_with_spare_defaults_and_coercion(stream):
    src = ('@a = chf("th", default=2.0)\n@b = chi("n", default=3)\n@c = chb("on")\n'
           '@d = chs("mode", options=["x", "y"])\n@e = chv("v", default=[1, 2])[1]\n'
           '@f = chi("period")')
    p = prepare(src, "node_code", "n_rsi", builtin_params=("period",))
    assert p.ok, p.diagnostics
    res = run(p, stream, params={"n": "14", "on": "true", "period": 14.0})
    got = [res.stream.value(n) for n in ("@a", "@b", "@c", "@d", "@e", "@f")]
    assert got == [2.0, 14, True, "x", 2.0, 14]


def test_paths_resolve_through_the_given_resolver(stream):
    src = '@a = chf("../vol/threshold")\n@b = ch("/shared/spread/@spread")'
    p = prepare(src, "wrangle", "n_w")
    res = run(p, stream, resolve={"../vol/threshold": "2.5", "/shared/spread/@spread": 7})
    assert (res.stream.value("@a"), res.stream.value("@b")) == (2.5, 7)
    res = run(p, stream, resolve=lambda path: 1.0)
    assert res.stream.value("@a") == 1.0
    d = _fail(src, stream, resolve={})
    assert (d.code, d.line, d.col) == (REF_BROKEN, 1, 5)
    d = _fail(src, stream)
    assert d.code == REF_BROKEN


def test_an_alias_call_the_scan_did_not_see_is_ch_dynamic(stream):
    d = _fail('f = chf\nv = f("thr" + "eshold")', stream)
    assert (d.code, d.line, d.col) == (CH_DYNAMIC, 2, 4)
    # An alias call of a name the scan did see is fine.
    res = _wrangle('a = chf("th", default=1.5)\nf = chf\n@v = f("th")', stream)
    assert res.stream.value("@v") == 1.5


def test_a_channel_value_of_the_wrong_type_is_code_type(stream):
    p = prepare('@a = chi("n", default=1)', "wrangle", "n_w")
    with pytest.raises(CodeError) as info:
        run(p, stream, params={"n": 2.5})
    assert (info.value.code, info.value.line, info.value.col) == (CODE_TYPE, 1, 5)
    with pytest.raises(CodeError) as info:
        run(p, stream, params={"n": {"expr": "3"}})
    assert info.value.code == CODE_TYPE


# ---------------------------------------------------------------------------
# Parameter expressions
# ---------------------------------------------------------------------------


def _expr(src, stream, expected, **kwargs):
    p = prepare(src, "expr", "n_rsi", param="period", builtin_params=("period", "type"))
    assert p.ok, p.diagnostics
    return run(p, stream, expected=expected, **kwargs).value


def test_the_vision_example_expression(stream):
    src = '7 if chf("../vol/threshold") > 2 else 21'
    assert _expr(src, stream, "int", resolve={"../vol/threshold": 2.5}) == 7
    assert _expr(src, stream, "int", resolve={"../vol/threshold": 1.0}) == 21


def test_expression_results_are_scalars_of_the_params_type(stream):
    assert _expr("14.0", stream, "int") == 14 and isinstance(_expr("14.0", stream, "int"), int)
    assert _expr("np.int64(3)", stream, "int") == 3
    assert _expr("3", stream, "float") == 3.0 and isinstance(_expr("3", stream, "float"), float)
    assert _expr("@stop_pct > 2", stream, "bool") is True
    assert _expr("'wil' + 'der'", stream, "select", options=("sma", "wilder")) == "wilder"
    assert _expr("@stop_pct * 2", stream, "number") == 5.0
    assert _expr("'x'", stream, None) == "x"


@pytest.mark.parametrize("src,expected,code", [
    ("14.5", "int", CODE_TYPE),
    ("True", "int", CODE_TYPE),
    ("'a'", "float", CODE_TYPE),
    ("float('nan')", "float", CODE_TYPE),
    ("1", "bool", CODE_TYPE),
    ("3", "string", CODE_TYPE),
    ("'ema'", "select", PARAM_INVALID),
    ("[1, 2]", None, CODE_TYPE),
    ("None", "int", CODE_TYPE),
])
def test_bad_expression_results(stream, src, expected, code):
    p = prepare(src, "expr", "n_rsi", param="period")
    with pytest.raises(CodeError) as info:
        run(p, stream, expected=expected, options=("sma", "wilder"))
    d = info.value.diagnostic
    assert (d.code, d.node_id, d.param, d.line, d.col) == (code, "n_rsi", "period", 1, 0)
    assert d.end_col == len(src)


def test_a_series_result_says_use_a_wrangle(stream):
    p = prepare("pd.Series([1.0])", "expr", "n_rsi", param="period")
    with pytest.raises(CodeError) as info:
        run(p, stream, expected="float")
    assert info.value.code == CODE_TYPE
    assert "use a Wrangle for per-bar logic" in info.value.message


def test_an_expression_reads_detail_attributes_only(stream):
    p = prepare("@close", "expr", "n_rsi", param="period")
    with pytest.raises(AttrMissingError) as info:
        run(p, stream, expected="float")
    d = info.value.diagnostic
    assert (d.code, d.line, d.col, d.param) == (ATTR_MISSING, 1, 0, "period")
    assert "parameter expressions read detail attributes only" in d.message
    assert "use a Wrangle for per-bar logic" in d.message
    # A detail read is fine, and so is no stream at all for a constant.
    assert _expr("@stop_pct * 4", stream, "float") == 10.0
    p = prepare("21", "expr", "n_rsi", param="period")
    assert run(p, None, expected="int").value == 21


# ---------------------------------------------------------------------------
# pause_reason
# ---------------------------------------------------------------------------


def test_pause_reason_texts(stream):
    d = _fail("a = 1\nb = 2\nx = 1 / 0", stream)
    assert pause_reason(d, "spread_z") == \
        "code_runtime: spread_z line 3: ZeroDivisionError: division by zero"
    syntax = prepare("a = 1\nb = 2\nx = (", "wrangle", "n_w").diagnostics[0]
    assert pause_reason(syntax, "spread_z") == "code_syntax: spread_z line 3"
    timeout = CodeTimeout("x", timeout_s=10, node_id="n_w")
    assert pause_reason(timeout, "spread_z") == "code_timeout: spread_z ran longer than 10 s"
    assert pause_reason(CodeDiagnostic(CODE_DISABLED, "off")) == "code_disabled"
