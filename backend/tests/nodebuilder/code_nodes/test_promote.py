"""The static scan: auto-promoted params, references, reads and writes
(F435 W7 item 7.A, design note 4.3).  prepare() runs the scan; nothing
here runs user code.
"""
from __future__ import annotations

import pytest

from nodebuilder.code import prepare
from nodebuilder.code.errors import (
    ATTR_DYNAMIC,
    CH_DYNAMIC,
    CODE_SYNTAX,
    CODE_TYPE,
    REF_BROKEN,
)
from nodebuilder.code.promote import LOOKBACK_PARAM


def _codes(prepared) -> list[str]:
    return [d.code for d in prepared.diagnostics]


def _one(prepared, code: str):
    found = [d for d in prepared.diagnostics if d.code == code]
    assert found, f"no {code} in {prepared.diagnostics}"
    return found[0]


# ---------------------------------------------------------------------------
# Spare params
# ---------------------------------------------------------------------------


def test_typed_calls_give_specs_in_order_with_lookback_first():
    src = (
        'a = chf("th", default=2.0, min=0, max=10, label="Threshold")\n'
        'b = chi("n", default=14, min=2, max=50)\n'
        'c = chs("mode", options=["fast", "slow"])\n'
        'd = chb("on", default=True)\n'
        'e = chv("weights", default=[1, 2, 3])\n'
    )
    p = prepare(src, "wrangle", "n_w")
    assert p.ok, p.diagnostics
    assert p.params_json() == [
        {"name": "lookback_bars", "type": "int", "default": 500, "min": 1, "max": 100000,
         "label": "lookback bars", "options": None},
        {"name": "th", "type": "float", "default": 2.0, "min": 0, "max": 10,
         "label": "Threshold", "options": None},
        {"name": "n", "type": "int", "default": 14, "min": 2, "max": 50, "label": "n",
         "options": None},
        {"name": "mode", "type": "string", "default": "fast", "min": None, "max": None,
         "label": "mode", "options": ["fast", "slow"]},
        {"name": "on", "type": "bool", "default": True, "min": None, "max": None, "label": "on",
         "options": None},
        {"name": "weights", "type": "vector", "default": [1.0, 2.0, 3.0], "min": None,
         "max": None, "label": "weights", "options": None},
    ]
    assert p.lookback_bars == 500
    assert [r.kind for r in p.refs] == ["spare"] * 5


def test_missing_defaults_take_the_type_default_inside_the_range():
    p = prepare('a = chi("n", min=2)\nb = chf("x")\nc = chs("s")\nd = chb("b")\ne = chv("v")',
                "node_code", "n1", builtin_params=())
    specs = {s.name: s.default for s in p.spare_params}
    assert specs == {"lookback_bars": 500, "n": 2, "x": 0.0, "s": "", "b": False,
                     "v": [0.0, 0.0, 0.0]}


def test_a_builtin_name_is_read_not_promoted():
    # An RSI node's code block reading its own period.
    p = prepare('@rsi_smooth = sl.ema(@rsi, chi("period", default=3))', "node_code",
                {"id": "n_rsi", "type": "rsi"})
    assert p.ok, p.diagnostics
    assert [s.name for s in p.spare_params] == [LOOKBACK_PARAM]
    assert [(r.target, r.kind) for r in p.refs] == [("period", "builtin")]


def test_design_example_node_code_block_promotes_smooth():
    p = prepare('@rsi_smooth = sl.ema(@rsi, chi("smooth", default=3))', "node_code",
                {"id": "n_rsi", "type": "rsi"})
    assert p.ok, p.diagnostics
    assert [(s.name, s.type, s.default) for s in p.spare_params] == [
        (LOOKBACK_PARAM, "int", 500), ("smooth", "int", 3)]
    assert p.reads == ("rsi",)
    assert p.writes == ("rsi_smooth",)


def test_code_may_declare_lookback_bars_itself():
    p = prepare('x = chf("a")\nn = chi("lookback_bars", default=2000)\n@y = @close', "wrangle", "w")
    assert p.ok, p.diagnostics
    assert [s.name for s in p.spare_params] == ["a", LOOKBACK_PARAM]
    assert p.spare_params[1].default == 2000 and p.spare_params[1].max == 100000
    assert p.lookback_bars == 2000
    assert p.effective_lookback({"lookback_bars": "750"}) == 750
    assert p.effective_lookback({}) == 2000


def test_lookback_bars_must_be_an_int_in_range():
    assert _codes(prepare('chf("lookback_bars")', "wrangle", "w")) == [CODE_TYPE]
    assert _codes(prepare('chi("lookback_bars", max=1000000)', "wrangle", "w")) == [CODE_TYPE]
    # 10**6 is not a literal at all.
    assert _codes(prepare('chi("lookback_bars", max=10**6)', "wrangle", "w")) == [CH_DYNAMIC]


def test_expressions_have_no_lookback():
    p = prepare('chf("x", default=1.5) * 2', "expr", "n1", param="threshold")
    assert p.ok, p.diagnostics
    assert [s.name for s in p.spare_params] == ["x"]
    assert p.lookback_bars is None and p.effective_lookback({"lookback_bars": 9}) is None


def test_a_later_call_without_keywords_or_an_untyped_ch_just_reads():
    p = prepare('a = chf("th", default=2.0)\nb = chf("th")\nc = ch("th")', "wrangle", "w")
    assert p.ok, p.diagnostics
    assert [s.name for s in p.spare_params] == [LOOKBACK_PARAM, "th"]


def test_conflicting_specs_are_code_type_at_the_later_call():
    p = prepare('a = chf("th", default=2.0)\nb = chi("th")', "wrangle", "w")
    d = _one(p, CODE_TYPE)
    assert (d.line, d.col) == (2, 4)
    p = prepare('a = chf("th", default=2.0)\nb = chf("th", default=3.0)', "wrangle", "w")
    d = _one(p, CODE_TYPE)
    assert (d.line, d.col) == (2, 4)
    assert not p.ok


@pytest.mark.parametrize("src", [
    'chf(name)',
    'chf("a" + "b")',
    'chf(f"x{1}")',
    'chf(*names)',
    'chf("x", **opts)',
    'chf("x", default=y)',
    'chf("x", min=lo)',
    'chs("x", options=opts)',
    'chf("x", label=str(1))',
    'ch("../" + node + "/p")',
])
def test_non_literal_names_and_keywords_are_ch_dynamic(src):
    p = prepare(src, "wrangle", "w")
    assert _codes(p) == [CH_DYNAMIC], p.diagnostics
    assert p.diagnostics[0].line == 1
    assert not p.ok


@pytest.mark.parametrize("src", [
    'chf("x", dflt=1)',            # unknown keyword
    'chf("x", 1.0, 2.0)',          # too many positionals
    'chf("x", default="a")',       # wrong default type
    'chi("x", default=1.5)',
    'chs("x", default="c", options=["a", "b"])',
    'chb("x", default=1)',
    'chv("x", default=[1])',
    'chf("x", min=5, max=1)',
    'chi("x", default=1, min=2)',
    'chb("x", min=0)',
    'chf("x", options=["a"])',
    'chf("Bad Name")',
    'chf("@close")',
])
def test_bad_specs_are_code_type(src):
    assert _codes(prepare(src, "wrangle", "w")) == [CODE_TYPE]


def test_an_untyped_ch_of_an_undeclared_name_is_ref_broken():
    p = prepare('x = ch("nothing")', "wrangle", "w")
    d = _one(p, REF_BROKEN)
    assert (d.line, d.col) == (1, 4)


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_paths_are_references_with_user_positions():
    src = '@z = @close + chf("../vol/threshold")\nq = ch("/shared/spread/@spread")'
    p = prepare(src, "wrangle", "w")
    assert p.ok, p.diagnostics
    assert [(r.func, r.target, r.kind, r.line, r.col) for r in p.paths] == [
        ("chf", "../vol/threshold", "path", 1, 14),
        ("ch", "/shared/spread/@spread", "path", 2, 4),
    ]
    assert [s.name for s in p.spare_params] == [LOOKBACK_PARAM]
    # A path's end column is in the user's text too.
    assert p.paths[0].end_col == len(src.split("\n")[0])


def test_name_keyword_works_like_the_first_argument():
    p = prepare('a = chf(name="th", default=1.0)', "wrangle", "w")
    assert p.ok and [s.name for s in p.spare_params] == [LOOKBACK_PARAM, "th"]


# ---------------------------------------------------------------------------
# Reads, writes and dtypes
# ---------------------------------------------------------------------------


def test_reads_writes_and_annotation_dtypes():
    src = (
        '@atr_pct = @high - @low\n'
        '@vol_regime: bool = @atr_pct > 2\n'
        'stream["z"]: float = stream["close"] * 1\n'
        '@count += 1\n'
        'v = stream.get("volume")\n'
    )
    p = prepare(src, "wrangle", "w")
    assert p.ok, p.diagnostics
    assert p.reads == ("high", "low", "atr_pct", "close", "count", "volume")
    assert p.writes == ("atr_pct", "vol_regime", "z", "count")
    assert dict(p.write_dtypes) == {"vol_regime": "bool", "z": "float"}
    assert p.writes_json() == [
        {"name": "@atr_pct", "class": "point", "dtype": "any"},
        {"name": "@vol_regime", "class": "point", "dtype": "bool"},
        {"name": "@z", "class": "point", "dtype": "float"},
        {"name": "@count", "class": "point", "dtype": "any"},
    ]
    assert p.reads_json(lambda n: ("point", "float") if n == "close" else None)[3] == \
        {"name": "@close", "class": "point", "dtype": "float"}


def test_bad_or_conflicting_annotations_are_code_type():
    assert _codes(prepare("@x: int = 1", "wrangle", "w")) == [CODE_TYPE]
    p = prepare("@x: bool = True\n@x: float = 1.0", "wrangle", "w")
    assert _codes(p) == [CODE_TYPE] and p.diagnostics[0].line == 2


def test_a_non_literal_write_is_attr_dynamic_and_a_non_literal_read_is_allowed():
    p = prepare('name = "x"\nstream[name] = 1', "wrangle", "w")
    d = _one(p, ATTR_DYNAMIC)
    assert (d.line, d.col) == (2, 0)
    p = prepare('name = "close"\nv = stream[name]', "wrangle", "w")
    assert p.ok and p.reads == ()


def test_a_write_with_a_bad_attribute_name_is_code_syntax():
    p = prepare('stream["Bad Name"] = 1', "wrangle", "w")
    assert _codes(p) == [CODE_SYNTAX]


@pytest.mark.parametrize("src,line,col", [
    ("stream = 1", 1, 0),
    ("x = 1\nfor stream in []: pass", 2, 4),
    ("def stream(): pass", 1, 0),
    ("def f(stream): pass", 1, 6),
    ("g = lambda stream: 1", 1, 11),
    ("import numpy as stream", 1, 7),
    ("from os import stream", 1, 15),
    ("with open('x') as stream: pass", 1, 18),
    ("try:\n    pass\nexcept Exception as stream:\n    pass", 3, 0),
    ("class stream: pass", 1, 0),
    ("(stream := 3)", 1, 1),
    ("def f():\n    global stream", 2, 4),
])
def test_binding_stream_is_code_syntax_at_the_binding(src, line, col):
    p = prepare(src, "wrangle", "w")
    assert _codes(p) == [CODE_SYNTAX], p.diagnostics
    assert (p.diagnostics[0].line, p.diagnostics[0].col) == (line, col)


def test_positions_after_a_rewritten_attr_are_in_the_users_text():
    # The ch_dynamic call sits after two @attr rewrites on the same line.
    p = prepare("@x = @close + chf(n)", "wrangle", "w")
    d = _one(p, CH_DYNAMIC)
    assert (d.line, d.col, d.end_line, d.end_col) == (1, 14, 1, 20)


def test_the_diagnostics_carry_the_node_and_the_expression_param():
    p = prepare("chf(n)", "expr", "n_rsi", param="period")
    assert (p.diagnostics[0].node_id, p.diagnostics[0].param) == ("n_rsi", "period")


@pytest.mark.parametrize("src", [
    "def f[stream]():\n    return 1\n@x = 1.0\n",
    "class C[*stream]:\n    pass\n@x = 1.0\n",
    "def g[**stream]():\n    return 1\n@x = 1.0\n",
    "type A[stream] = list[stream]\n@x = 1.0\n",
])
def test_a_type_parameter_named_stream_is_code_syntax(src):
    """W7 fix CR-11: a PEP 695 type parameter binds the reserved name too."""
    p = prepare(src, "wrangle", "n_w")
    assert CODE_SYNTAX in _codes(p)
    assert "stream is reserved" in _one(p, CODE_SYNTAX).message


def test_an_untyped_ch_of_lookback_bars_is_not_ref_broken():
    """W7 fix CR-8: lookback_bars exists on every code node."""
    p = prepare("n = ch('lookback_bars')\n@x = 1.0\n", "wrangle", "n_w")
    assert p.ok, p.diagnostics
    # An expression has no lookback_bars: still a broken bare read there.
    assert not prepare("ch('lookback_bars')", "expr", "n_r", param="period").ok
