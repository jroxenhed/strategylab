"""Comparison nodes: above, below, crosses_above, crosses_below.

Each compares attribute ``a`` with attribute ``b``, or with ``threshold``
when ``b`` is empty.  Operand order comes from the ``a``/``b`` params (plan
D4), never from wire order; an empty ``a`` reads what the node on in0
writes first, an empty ``b`` what the node on in1 writes first.

Semantics match signal_engine.eval_rule exactly:
- above / below:   a > b,  a < b
- crosses_above:   a[i-1] < b[i-1] and a[i] >= b[i]   (threshold: a[i-1] < t <= a[i])
- crosses_below:   a[i-1] > b[i-1] and a[i] <= b[i]   (threshold: a[i-1] > t >= a[i])
- bar 0 is False for every comparison (the rule engine's i < 1 guard)
- a comparison with a NaN on either side is False

The helpers at the top (as_float, as_bool, previous, first_bar_false) are
for other comparison-like nodes too.
"""
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.kernel.schema import DISABLED, NONE, UnsupportedNodeError

# ---------------------------------------------------------------------------
# Column helpers (also used by other node modules)
# ---------------------------------------------------------------------------


def as_float(values: np.ndarray) -> np.ndarray:
    """A column as float64 (a bool counts as 0 or 1).  No copy when it is
    float64 already."""
    return np.asarray(values, dtype=np.float64)


def as_bool(values: np.ndarray) -> np.ndarray:
    """A column as numpy bool."""
    return values if values.dtype == np.bool_ else np.asarray(values, dtype=bool)


def previous(values: np.ndarray, bars: int = 1) -> np.ndarray:
    """The value *bars* bars back (NaN where there is none)."""
    out = np.empty(len(values), dtype=np.float64)
    out[:bars] = np.nan
    if bars < len(values):
        out[bars:] = values[: len(values) - bars]
    return out


def first_bar_false(values: np.ndarray, bars: int = 1) -> np.ndarray:
    """*values* as a new bool column with the first *bars* bars False."""
    out = np.array(values, dtype=bool, copy=True)
    out[:bars] = False
    return out


# ---------------------------------------------------------------------------
# Comparison nodes
# ---------------------------------------------------------------------------

COMPARISON_INPUTS = PortsSpec(
    ports=(PortSpec("a"), PortSpec("b", optional=True)), dynamic=False, min=1, max=2,
)


def _a_param() -> ParamSpec:
    return ParamSpec("a", "attr", "a", None, dtype="float")


def _b_param() -> ParamSpec:
    return ParamSpec("b", "attr", "b", None, dtype="float", optional=True)


def _threshold() -> ParamSpec:
    """A comparison's threshold, used when b is empty."""
    return ParamSpec("threshold", "number", "threshold", None, optional=True)


def optional_threshold(ctx, value: Any) -> float | None:
    """A threshold as a float, or None when it is blank (param_invalid when
    it does not parse)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = math.nan
    if isinstance(value, bool) or not math.isfinite(number):
        ctx.fail("param_invalid", f"Threshold on {ctx.node_id!r} needs a number, got {value!r}.",
                 param="threshold")
    return number


def check_comparison(ctx) -> None:
    """What a two-sided comparison needs: a, and b or a threshold.

    auto_render stores rule details it cannot draw as nodes in a
    ``condition_extra`` param (an ATR% rule, say).  Running would compare the
    wrong series, so such a node is refused.
    """
    extra = ctx.raw.get("condition_extra")
    if extra is not None:
        ctx.fail("unsupported_node", "", param="condition_extra",
                 cls=lambda _msg, node_id: UnsupportedNodeError(node_id, f"{ctx.label} ({extra})"))
    threshold = optional_threshold(ctx, ctx.params.get("threshold"))
    ctx.params["threshold"] = threshold
    a, b = ctx.reads["a"], ctx.reads["b"]
    ctx.handled.update(("a", "b"))
    if not ctx.wires and (a.status == NONE or b.status == NONE):
        ctx.fail("missing_input",
                 f"{ctx.label} node {ctx.node_id!r} needs two inputs, or one input and a threshold.",
                 port="in0")
    if a.status == NONE:
        # Only b is wired.  Reading it as a would compare the wrong side.
        ctx.fail("missing_input",
                 f"{ctx.label} node {ctx.node_id!r} has nothing on in0 (a), the left side.  "
                 f"Wire a, or move the wire from in1 to in0.", port=a.port or "in0", param="a")
    if b.status == NONE and threshold is None:
        ctx.fail("missing_input",
                 f"{ctx.label} node {ctx.node_id!r} needs two inputs, or one input and a threshold.",
                 port=b.port or "in1", param="b")
    if a.status == DISABLED:
        # A bypassed a is never replaced by b: the comparison is off.
        ctx.set_off("a comes from a bypassed node")
    elif b.status == DISABLED and threshold is None:
        ctx.set_off("b comes from a bypassed node")


def _comparison(op: Callable[..., np.ndarray], crosses: bool) -> Callable:
    def impl(inputs, p):
        a = as_float(inputs.column(p["a"]))
        if p["b"] is not None:
            b = as_float(inputs.column(p["b"]))
            b_prev = previous(b) if crosses else None
        else:
            b = float(p["threshold"])
            b_prev = b
        with np.errstate(invalid="ignore"):
            result = op(a, b, previous(a) if crosses else None, b_prev)
        return inputs.with_point(p["out"], first_bar_false(result), p.node_id, "bool")
    return impl


def _above(a, b, _ap, _bp):
    return a > b


def _below(a, b, _ap, _bp):
    return a < b


def _crosses_above(a, b, a_prev, b_prev):
    return (a_prev < b_prev) & (a >= b)


def _crosses_below(a, b, a_prev, b_prev):
    return (a_prev > b_prev) & (a <= b)


def _register(name: str, desc: str, subtitle: str, out: str, op, crosses: bool) -> None:
    register_node(
        name=name, cat="comparison", desc=desc,
        params=(_a_param(), _b_param(), _threshold(),
                ParamSpec("out", "write", "out", out, dtype="bool")),
        inputs=COMPARISON_INPUTS, impl=_comparison(op, crosses), check=check_comparison,
        lookback=(lambda _p: 1) if crosses else None,
        subtitle=subtitle, ins=2, outs=1, module=__name__,
    )


_register("crosses_above", "True on the bar where a crosses above b (or the threshold).",
          "crosses above", "@xa", _crosses_above, True)
_register("crosses_below", "True on the bar where a crosses below b (or the threshold).",
          "crosses below", "@xb", _crosses_below, True)
_register("above", "True when a is above b (or the threshold).", "above", "@above", _above, False)
_register("below", "True when a is below b (or the threshold).", "below", "@below", _below, False)
