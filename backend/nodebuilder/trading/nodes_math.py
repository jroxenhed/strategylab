"""Math and stream nodes: constant, math, shift, rolling, xor, merge.

- constant: writes one number, as a column (every bar the same) or, with
  ``as_detail``, as a single detail value.  Its one input is optional: the
  stream wired in flows on with the constant added.
- math: ``a <op> b`` bar by bar (add, sub, mul, div, min, max), or ``abs``
  and ``neg`` of a alone.  Dividing by zero gives NaN, not infinity, so a
  comparison below it is False on that bar.  min and max keep a NaN.
- shift: a, ``bars`` bars back (NaN for the first bars).
- rolling: mean, min, max, std or sum of a over the last ``window`` bars
  (pandas rolling, as the indicators use: NaN until the window is full, or
  when the window holds a NaN; std is the sample std, as Bollinger uses).
- xor: True when an odd number of the signals in ``terms`` are true (for
  two signals: exactly one).  Bar 0 is False, as for AND and OR.
- merge: passes on the union of the streams on its inputs, so one node
  below it can read attributes from several branches.  It writes nothing,
  so a param reading from it must name the attribute.

Inputs that are true/false signals count as 0 and 1 in math, shift and
rolling.
"""
from __future__ import annotations

import numpy as np

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.kernel.schema import DISABLED, NONE, ReadInfo
from nodebuilder.trading.nodes_compare import as_bool, as_float, first_bar_false, previous
from nodebuilder.trading.nodes_logic import LOGIC_INPUTS

# A node whose one input is optional: on its own it is a source; wired, the
# stream it gets flows on with its writes added.
OPTIONAL_INPUT = PortsSpec(ports=(PortSpec("in", optional=True),), dynamic=False, min=0, max=1)

ONE_INPUT = PortsSpec(ports=(PortSpec("a"),), dynamic=False, min=1, max=1)
TWO_INPUTS = PortsSpec(ports=(PortSpec("a"), PortSpec("b", optional=True)),
                       dynamic=False, min=1, max=2)

# Merge takes any number of streams.
MERGE_INPUTS = PortsSpec(ports=(PortSpec("in0"), PortSpec("in1", optional=True)),
                         dynamic=True, min=1, max=16)

MAX_BARS = 500


def _a_param() -> ParamSpec:
    return ParamSpec("a", "attr", "a", None, dtype="float")


def _out(default: str, dtype: str = "float") -> ParamSpec:
    return ParamSpec("out", "write", "out", default, dtype=dtype)


def check_select(ctx, name: str) -> str:
    """A select param must hold one of its options (a typo would quietly
    change what the node does)."""
    spec = ctx.node_type.param(name)
    value = ctx.params.get(name)
    if value not in (spec.options or ()):
        ctx.fail("param_invalid",
                 f"{ctx.label} {ctx.node_id!r} has {name} {value!r}; use one of "
                 f"{list(spec.options or ())}.", param=name)
    return value


def check_bool(ctx, name: str) -> bool:
    """A bool param.  The editor stores a bool as the text "true" / "false" (a
    select, see catalog.ts toParamTypeSpec), so that text is read as the bool."""
    value = ctx.params.get(name)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        value = value.strip().lower() == "true"
        ctx.params[name] = value
    if not isinstance(value, bool):
        ctx.fail("param_invalid",
                 f"{ctx.label} {ctx.node_id!r} {name} needs true or false, got {value!r}.",
                 param=name)
    return value


# ---------------------------------------------------------------------------
# constant
# ---------------------------------------------------------------------------


def _check_constant(ctx) -> None:
    ctx.number_params()
    check_bool(ctx, "as_detail")


def _constant(inputs, p):
    value = float(p["value"])
    if p["as_detail"]:
        return inputs.with_detail(p["out"], value, p.node_id, "float")
    column = np.full(inputs.store.length, value, dtype=np.float64)
    return inputs.with_point(p["out"], column, p.node_id, "float")


register_node(
    name="constant", cat="signal",
    desc="A fixed number, on every bar (or as one detail value with as_detail).",
    params=(
        ParamSpec("value", "number", "value", 0.0),
        ParamSpec("as_detail", "bool", "as detail", False),
        _out("@const"),
    ),
    inputs=OPTIONAL_INPUT, impl=_constant, check=_check_constant,
    subtitle="constant", ins=1, outs=1,
    # "out" is a detail value when as_detail is on; the kernel's schema reads
    # this (kernel.schema._write_list), so /validate shows it as detail.
    meta={"detail_if": {"out": "as_detail"}},
    module=__name__,
)


# ---------------------------------------------------------------------------
# math
# ---------------------------------------------------------------------------

MATH_OPS: tuple[str, ...] = ("add", "sub", "mul", "div", "min", "max", "abs", "neg")
UNARY_OPS: frozenset[str] = frozenset({"abs", "neg"})


def _check_math(ctx) -> None:
    op = check_select(ctx, "op")
    b = ctx.reads["b"]
    ctx.handled.add("b")
    if op in UNARY_OPS:
        # b plays no part; never let it turn the node off or fail it.
        ctx.reads["b"] = ReadInfo("b", None, NONE, b.explicit, b.port)
        return
    if b.status == NONE:
        ctx.fail("missing_input",
                 f"math {ctx.node_id!r} ({op}) needs b: wire in1 or name an attribute.",
                 param="b", port=b.port or "in1")
    if b.status == DISABLED and ctx.off_reason is None:
        ctx.set_off(f"b reads {b.name}, which a bypassed node writes")


def _divide(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a / b, with NaN where b is 0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = a / b
    out[b == 0] = np.nan
    return out


_BINARY = {
    "add": np.add,
    "sub": np.subtract,
    "mul": np.multiply,
    "div": _divide,
    "min": np.minimum,
    "max": np.maximum,
}


def _math(inputs, p):
    op = p["op"]
    a = as_float(inputs.column(p["a"]))
    if op == "abs":
        result = np.abs(a)
    elif op == "neg":
        result = np.negative(a)
    else:
        b = as_float(inputs.column(p["b"]))
        with np.errstate(invalid="ignore", over="ignore"):
            result = _BINARY[op](a, b)
    return inputs.with_point(p["out"], result, p.node_id, "float")


register_node(
    name="math", cat="signal",
    desc="a op b bar by bar: add, sub, mul, div, min, max; or abs / neg of a. "
         "Dividing by zero gives NaN.",
    params=(
        ParamSpec("op", "select", "op", "add", options=MATH_OPS),
        _a_param(),
        ParamSpec("b", "attr", "b", None, dtype="float", optional=True),
        _out("@math"),
    ),
    inputs=TWO_INPUTS, impl=_math, check=_check_math,
    subtitle="a + b", ins=2, outs=1, module=__name__,
)


# ---------------------------------------------------------------------------
# shift
# ---------------------------------------------------------------------------


def _check_numbers(ctx) -> None:
    ctx.number_params()


def _shift(inputs, p):
    a = as_float(inputs.column(p["a"]))
    return inputs.with_point(p["out"], previous(a, int(p["bars"])), p.node_id, "float")


register_node(
    name="shift", cat="signal", desc="a as it was `bars` bars ago. Default bars=1.",
    params=(
        _a_param(),
        ParamSpec("bars", "int", "bars", 1, min=1, max=MAX_BARS, unit="bars"),
        _out("@shift"),
    ),
    inputs=ONE_INPUT, impl=_shift, check=_check_numbers,
    lookback=lambda p: int(p["bars"]),
    subtitle="shift 1", ins=1, outs=1, module=__name__,
)


# ---------------------------------------------------------------------------
# rolling
# ---------------------------------------------------------------------------

ROLLING_OPS: tuple[str, ...] = ("mean", "min", "max", "std", "sum")


def _check_rolling(ctx) -> None:
    check_select(ctx, "op")
    ctx.number_params()


def _rolling(inputs, p):
    a = inputs.series(p["a"], float)
    window = a.rolling(int(p["window"]))
    result = getattr(window, p["op"])()
    return inputs.with_point(p["out"], result.to_numpy(dtype=float), p.node_id, "float")


register_node(
    name="rolling", cat="signal",
    desc="mean, min, max, std or sum of a over the last `window` bars. Default mean over 20.",
    params=(
        _a_param(),
        ParamSpec("op", "select", "op", "mean", options=ROLLING_OPS),
        ParamSpec("window", "int", "window", 20, min=2, max=MAX_BARS, unit="bars"),
        _out("@rolling"),
    ),
    inputs=ONE_INPUT, impl=_rolling, check=_check_rolling,
    lookback=lambda p: int(p["window"]),
    subtitle="mean(20)", ins=1, outs=1, module=__name__,
)


# ---------------------------------------------------------------------------
# xor
# ---------------------------------------------------------------------------


def _xor(inputs, p):
    columns = [as_bool(inputs.column(name)) for name in p["terms"]]
    result = np.logical_xor.reduce(columns) if len(columns) > 1 else columns[0]
    return inputs.with_point(p["out"], first_bar_false(result), p.node_id, "bool")


register_node(
    name="xor", cat="logic",
    desc="True when an odd number of incoming boolean signals are true (two signals: exactly one).",
    params=(ParamSpec("terms", "attr_list", "terms", None, dtype="bool"),
            _out("@xor", "bool")),
    inputs=LOGIC_INPUTS, impl=_xor,
    reads=("@bool",), subtitle="XOR", ins=2, outs=1, module=__name__,
)


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _merge(inputs, _p):
    return inputs


register_node(
    name="merge", cat="data",
    desc="Joins the streams on its inputs into one, so a node below can read from all of them.",
    params=(), inputs=MERGE_INPUTS, impl=_merge,
    reads=(), writes=(), subtitle="merge", ins=2, outs=0, module=__name__,
)

