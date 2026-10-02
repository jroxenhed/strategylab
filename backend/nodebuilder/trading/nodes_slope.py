"""Slope nodes: how one attribute moves from bar to bar.

rising, falling, rising_over, falling_over, turns_up, turns_down,
turns_up_below, turns_down_above, accelerating, decelerating.

Each reads one attribute ``a`` (empty: what the node wired in writes first)
and writes a true/false signal.  The results match signal_engine.eval_rule
bar for bar, including its quirks:

- rising / falling:        a[i] > a[i-1],  a[i] < a[i-1]
- rising_over / falling_over (bars = n):  a[i] > a[i-n] (False while i < n;
  n = 0 is never true, as in the rule engine)
- turns_up (bars = n): the last n steps rose and the step before them fell.
  A step only counts as "not rising" when it is <= 0, so a step with a NaN
  in it passes, exactly as in the rule engine.  With min_pct > 0 the move
  from the low point a[i-n] must also be at least min_pct percent.
  turns_down is the mirror image.  False while i < n + 1.
- turns_up_below:   a[i-1] < threshold and a[i] > a[i-1]
- turns_down_above: a[i-1] > threshold and a[i] < a[i-1]
- accelerating / decelerating: the change in the step, (a[i]-a[i-1]) -
  (a[i-1]-a[i-2]), is above / below 0.  False while i < 2.
- bar 0 is always False (the rule engine's i < 1 guard).

A rule's ``negated`` flag is a NOT node after these (see nodes_logic), so
the guarded bars invert to True just as eval_rules inverts them.
"""
from __future__ import annotations

import numpy as np

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.trading.nodes_compare import as_float, first_bar_false, previous

# One input; ``a`` defaults to what that input's node writes first.
ONE_INPUT = PortsSpec(ports=(PortSpec("a"),), dynamic=False, min=1, max=1)

# The rule engine clamps a bar count to 0..500 (signal_engine._clamp_lookback).
MAX_BARS = 500

# turns_up / turns_down refuse a low (or high) point this close to zero
# before taking a percent move from it (signal_engine.eval_rule).
_ZERO = 1e-12


def _a_param() -> ParamSpec:
    return ParamSpec("a", "attr", "a", None, dtype="float")


def _out(default: str) -> ParamSpec:
    return ParamSpec("out", "write", "out", default, dtype="bool")


def _check_numbers(ctx) -> None:
    ctx.number_params()


def _signal(inputs, p, values: np.ndarray, guard: int):
    """Write *values* as the node's signal, with the first *guard* bars False."""
    return inputs.with_point(p["out"], first_bar_false(values, guard), p.node_id, "bool")


def _register(name: str, desc: str, params: tuple, impl, lookback, subtitle: str,
              check=_check_numbers) -> None:
    register_node(
        name=name, cat="signal", desc=desc, params=params, inputs=ONE_INPUT,
        impl=impl, check=check, lookback=lookback, subtitle=subtitle,
        ins=1, outs=1, module=__name__,
    )


# ---------------------------------------------------------------------------
# rising, falling
# ---------------------------------------------------------------------------


def _step(up: bool):
    def impl(inputs, p):
        a = as_float(inputs.column(p["a"]))
        with np.errstate(invalid="ignore"):
            result = a > previous(a) if up else a < previous(a)
        return _signal(inputs, p, result, 1)
    return impl


_register("rising", "True when a is higher than on the bar before.",
          (_a_param(), _out("@rising")), _step(True), lambda _p: 1, "rising")
_register("falling", "True when a is lower than on the bar before.",
          (_a_param(), _out("@falling")), _step(False), lambda _p: 1, "falling")


# ---------------------------------------------------------------------------
# rising_over, falling_over
# ---------------------------------------------------------------------------


def _over(up: bool):
    def impl(inputs, p):
        a = as_float(inputs.column(p["a"]))
        bars = int(p["bars"])
        before = previous(a, bars) if bars > 0 else a
        with np.errstate(invalid="ignore"):
            result = a > before if up else a < before
        return _signal(inputs, p, result, max(1, bars))
    return impl


def _bars(default: int, minimum: int) -> ParamSpec:
    return ParamSpec("bars", "int", "bars", default, min=minimum, max=MAX_BARS, unit="bars")


_register("rising_over", "True when a is higher than it was `bars` bars ago. Default bars=10.",
          (_a_param(), _bars(10, 0), _out("@rising_over")), _over(True),
          lambda p: int(p["bars"]), "rising over")
_register("falling_over", "True when a is lower than it was `bars` bars ago. Default bars=10.",
          (_a_param(), _bars(10, 0), _out("@falling_over")), _over(False),
          lambda p: int(p["bars"]), "falling over")


# ---------------------------------------------------------------------------
# turns_up, turns_down
# ---------------------------------------------------------------------------


def run_length(mask: np.ndarray) -> np.ndarray:
    """How many bars in a row *mask* has been True, ending at each bar."""
    idx = np.arange(len(mask))
    last_false = np.where(mask, -1, idx)
    np.maximum.accumulate(last_false, out=last_false)
    return idx - last_false


def _turn(up: bool):
    def impl(inputs, p):
        a = as_float(inputs.column(p["a"]))
        bars = int(p["bars"])
        step = a - previous(a)
        with np.errstate(invalid="ignore", divide="ignore"):
            # A step fails only when the comparison is True, so a NaN step
            # passes (the rule engine tests "<= 0" and returns early on True).
            moving = ~(step <= 0) if up else ~(step >= 0)
            result = run_length(moving) >= bars
            before = previous(step, bars)
            result &= ~(before >= 0) if up else ~(before <= 0)
            pct = p.get("min_pct")
            if pct is not None and pct > 0:
                pivot = previous(a, bars)          # the low (or high) point
                scale = np.abs(pivot)
                move = (a - pivot) if up else (pivot - a)
                result &= ~(scale < _ZERO)
                result &= ~(move / scale * 100 < pct)
        return _signal(inputs, p, result, bars + 1)
    return impl


def _turn_params(out: str) -> tuple:
    return (
        _a_param(),
        # How many bars in a row a must have moved the new way.
        _bars(1, 1),
        # Smallest move from the turning point, in percent (empty: any move).
        ParamSpec("min_pct", "number", "min move", None, min=0.0, unit="%", optional=True),
        _out(out),
    )


_register("turns_up",
          "True when a starts rising after falling: the last `bars` steps rose and the one "
          "before fell. Optional min move in percent from the low point.",
          _turn_params("@turns_up"), _turn(True), lambda p: int(p["bars"]) + 1, "turns up")
_register("turns_down",
          "True when a starts falling after rising: the last `bars` steps fell and the one "
          "before rose. Optional min move in percent from the high point.",
          _turn_params("@turns_down"), _turn(False), lambda p: int(p["bars"]) + 1, "turns down")


# ---------------------------------------------------------------------------
# turns_up_below, turns_down_above
# ---------------------------------------------------------------------------


def _turn_at(up: bool):
    def impl(inputs, p):
        a = as_float(inputs.column(p["a"]))
        prev = previous(a)
        t = float(p["threshold"])
        with np.errstate(invalid="ignore"):
            result = (prev < t) & (a > prev) if up else (prev > t) & (a < prev)
        return _signal(inputs, p, result, 1)
    return impl


def _threshold() -> ParamSpec:
    """Required: the rule engine is never true without one."""
    return ParamSpec("threshold", "number", "threshold", None)


_register("turns_up_below", "True when a rises from a bar that was below the threshold.",
          (_a_param(), _threshold(), _out("@turns_up_below")), _turn_at(True),
          lambda _p: 1, "turns up below")
_register("turns_down_above", "True when a falls from a bar that was above the threshold.",
          (_a_param(), _threshold(), _out("@turns_down_above")), _turn_at(False),
          lambda _p: 1, "turns down above")


# ---------------------------------------------------------------------------
# accelerating, decelerating
# ---------------------------------------------------------------------------


def _accel(up: bool):
    def impl(inputs, p):
        a = as_float(inputs.column(p["a"]))
        a1 = previous(a)
        a2 = previous(a, 2)
        with np.errstate(invalid="ignore"):
            change = (a - a1) - (a1 - a2)
            result = change > 0 if up else change < 0
        return _signal(inputs, p, result, 2)
    return impl


_register("accelerating", "True when a's step from the bar before is bigger than the step before it.",
          (_a_param(), _out("@accelerating")), _accel(True), lambda _p: 2, "accelerating")
_register("decelerating", "True when a's step from the bar before is smaller than the step before it.",
          (_a_param(), _out("@decelerating")), _accel(False), lambda _p: 2, "decelerating")
