"""Output terminals (plan D7, D11; W5 item 5.A).

Terminals are the last nodes of a group.  Each one hands one thing to the
simulator, and nothing may be wired out of a terminal:

  entry, exit    ``signal`` (bool attr).  ``side`` (long | short) is read only
                 in a ``regime_switch`` group, where each side has its own
                 entry and exit.
  size           ``value`` (number attr, point or detail: a fraction of the
                 group's capital) or ``constant`` (fraction 0.01 to 1.0).
  stop           ``value`` (number attr, percent) or ``constant`` (percent;
                 0 means no stop).
  trailing_stop  ``type``, ``value``, ``source``, ``activate_on_profit``,
                 ``activate_pct``: every models.TrailingStopConfig field, by
                 its own name, so a rule trailing stop renders as this node.
  time_stop      ``max_bars``: close the trade after this many bars
                 (the simulator's max_bars_held).
                 size, stop, trailing_stop and time_stop also take ``side``
                 (both | long | short, default both), read only in a
                 ``regime_switch`` group: a long or short one sets that
                 side's value (the rule backtest's long_stop_loss_pct and
                 so on), one with both sides the shared value.  A per-side
                 size or stop must be a constant (sim_bridge refuses a
                 wired one).
  regime         ``signal`` (bool attr): entries only while it is true.
                 ``on_flip`` (hold | close_only | close_and_reverse): what
                 happens to an open trade when the signal flips.

nodebuilder.trading.sim_bridge reads every terminal's compiled step (its
params, as checked here) and, after the cook, the kept input streams of the
size, stop and regime terminals.  An empty ``constant`` (or ``max_bars``)
means "not set": the request field then applies (plan D11 precedence: graph
node, then request field, then engine default).  A wired ``value`` wins over
the constant.

Until the backtest and the bot read the bridge (W5 items 5.B and 5.D), the
constant terminals also leave ``annotations["settings"]`` the way the
settings nodes do, so the Wave 0 overlay path applies the same constants.

The one-Entry / one-Exit rules live in nodebuilder.compile (they are about
the whole graph).  The group rules (one of each terminal per group) live
with the Output Groups (W5 5.B).  Terminals ignore the bypass flag, except
trailing_stop, which keeps the bypass it had as a settings node.

The type name ``regime`` is this terminal.  A regime *network* (plan D8)
needs another type name.
"""
from __future__ import annotations

import math

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.kernel.schema import DISABLED, LIVE, NONE, GraphTypeError
from nodebuilder.trading.nodes_settings import (
    TRAILING_STOP_DEFAULTS,
    TRAILING_STOP_SOURCE_OPTIONS,
    TRAILING_STOP_TYPE_OPTIONS,
    _detail_out,
    _number,
    _refuse_direction,
    _write_detail,
    trailing_stop_config,
)

# Every terminal type, in the fixed order the group frame lists them (S32b).
TERMINAL_TYPES: tuple[str, ...] = (
    "entry", "exit", "size", "stop", "trailing_stop", "time_stop", "regime",
)

SIDE_OPTIONS: tuple[str, ...] = ("long", "short")
# Size, stop, trailing stop and time stop: "both" (the default) applies to
# both sides of a regime_switch group, long or short to that side only.
BOTH_SIDES = "both"
EITHER_SIDE_OPTIONS: tuple[str, ...] = (BOTH_SIDES,) + SIDE_OPTIONS
ON_FLIP_OPTIONS: tuple[str, ...] = ("hold", "close_only", "close_and_reverse")

ONE_SIGNAL = PortsSpec(ports=(PortSpec("signal"),), dynamic=False, min=1, max=1)
# Exit may stay unwired: the strategy then only leaves by stops.
OPTIONAL_SIGNAL = PortsSpec(ports=(PortSpec("signal", optional=True),), dynamic=False, min=0, max=1)
# Size and Stop read a number from their one input, or use their constant.
OPTIONAL_VALUE = PortsSpec(ports=(PortSpec("value", optional=True),), dynamic=False, min=0, max=1)
# Every terminal has one input port (S32b).  Trailing stop and time stop
# read nothing from it: a wire there only places the terminal in the flow.
OPTIONAL_IN = PortsSpec(ports=(PortSpec("in", optional=True),), dynamic=False, min=0, max=1)


# ---------------------------------------------------------------------------
# Entry and Exit
# ---------------------------------------------------------------------------


def _check_side(ctx) -> None:
    side = ctx.params.get("side")
    if side in (None, ""):
        ctx.params["side"] = "long"
        return
    if side not in SIDE_OPTIONS:
        ctx.fail("param_invalid",
                 f"{ctx.label} {ctx.node_id!r} side must be one of {SIDE_OPTIONS}, got {side!r}.",
                 param="side")


def _check_terminal(ctx) -> None:
    # compile.py turns an empty or bypassed signal into the terminal errors.
    ctx.handled.add("signal")
    _check_side(ctx)


def _side_param() -> ParamSpec:
    # Read only in a regime_switch group (5.B); the canvas hides it elsewhere.
    return ParamSpec("side", "select", "side", "long", options=SIDE_OPTIONS)


def _either_side_param() -> ParamSpec:
    # Size, stop, trailing stop and time stop: both (the default) applies to
    # both sides; long or short to that side only (the rule backtest's
    # per-direction fields).  Read only in a regime_switch group
    # (sim_bridge.SIDED_TERMINALS); the canvas hides it elsewhere, as for
    # Entry and Exit.
    return ParamSpec("side", "select", "side", BOTH_SIDES, options=EITHER_SIDE_OPTIONS)


def _check_either_side(ctx) -> None:
    side = ctx.params.get("side")
    if side in (None, ""):
        ctx.params["side"] = BOTH_SIDES
        return
    if side not in EITHER_SIDE_OPTIONS:
        ctx.fail("param_invalid",
                 f"{ctx.label} {ctx.node_id!r} side must be one of {EITHER_SIDE_OPTIONS}, "
                 f"got {side!r}.", param="side")


def _both_sides(ctx) -> bool:
    return ctx.params.get("side") in (None, "", BOTH_SIDES)


register_node(
    name="entry", cat="output",
    desc="Entry terminal. Opens a position on the bar where the signal is true.",
    params=(ParamSpec("signal", "attr", "signal", None, dtype="bool"), _side_param()),
    inputs=ONE_SIGNAL, impl=None, check=_check_terminal,
    has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Entry", ins=1, outs=0, module=__name__,
)

register_node(
    name="exit", cat="output",
    desc="Exit terminal. Closes the position on the bar where the signal is true.",
    params=(ParamSpec("signal", "attr", "signal", None, dtype="bool", optional=True),
            _side_param()),
    inputs=OPTIONAL_SIGNAL, impl=None, check=_check_terminal,
    has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Exit", ins=1, outs=0, module=__name__,
)


# ---------------------------------------------------------------------------
# Size and Stop: a wired number, or a constant
# ---------------------------------------------------------------------------


def _value_read(ctx, label: str):
    """The attribute a Size or Stop terminal reads, or None for "use the constant".

    Refused, with this terminal named:
    - a value named or wired that has nothing to read (missing_input), so a
      typo never quietly falls back to the constant;
    - a value whose wired input is bypassed (missing_input): bypass would
      otherwise quietly swap a computed size or stop for the constant;
    - a true/false signal (attr_type): it is almost always the Entry signal
      wired into the wrong terminal.
    """
    ctx.handled.add("value")
    info = ctx.reads.get("value")
    raw = ctx.raw.get("value")
    if info is None or info.status == NONE:
        if raw not in (None, "") or ctx.wires:
            ctx.fail("missing_input",
                     f"{label} {ctx.node_id!r} has nothing to read for value: wire a number "
                     f"into it, or clear value and use the constant.",
                     param="value", port=(info.port if info is not None else None) or "in0")
        return None
    if info.status == DISABLED:
        ctx.fail("missing_input",
                 f"{label} {ctx.node_id!r} reads {info.name}, but the node that writes it is "
                 f"bypassed.  Delete the wire to use the constant instead.",
                 param="value", port=info.port or "in0")
    if info.status == LIVE and info.dtype == "bool":
        ctx.fail("attr_type",
                 f"{label} {ctx.node_id!r} reads {info.name}, a true/false signal, but it "
                 f"needs a number.",
                 param="value", port=info.port, cls=GraphTypeError)
    return info.name


def _constant(ctx, label: str, *, allow_zero: bool):
    """The constant as a float, or None when it is empty (not set)."""
    raw = ctx.raw.get("constant")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        ctx.params["constant"] = None
        return None
    number = _number(ctx, label, raw, minimum=0.0, allow_min=allow_zero, param="constant")
    ctx.params["constant"] = number
    return number


def _check_size(ctx) -> None:
    _check_either_side(ctx)
    attr = _value_read(ctx, "Size")
    constant = _constant(ctx, "Size", allow_zero=False)
    if constant is not None and constant > 1.0:
        # Size is a fraction of capital (1 = 100%).  50 was most likely meant
        # as 50%.  The simulator clamps it to 1.0.
        ctx.warn("size_unit_suspect",
                 f"Size on {ctx.node_id!r} is {constant:g}, but size is a fraction of capital "
                 f"(1 = 100%, 0.5 = 50%).", param="constant")
    if attr is None and constant is not None and _both_sides(ctx):
        ctx.annotations["settings"] = [("position_size", constant)]


def _check_stop(ctx) -> None:
    _check_either_side(ctx)
    attr = _value_read(ctx, "Stop")
    constant = _constant(ctx, "Stop", allow_zero=True)
    if attr is None and constant is not None and _both_sides(ctx):
        ctx.annotations["settings"] = [("stop_loss", constant)]


register_node(
    name="size", cat="output",
    desc=(
        "Size terminal. Share of the group's capital to use per trade, 1% to 100%, as a "
        "fraction (0.5 = 50%). Wire a number in, or set the constant. A wired value is "
        "read at the entry bar; no value there means no entry."
    ),
    params=(
        ParamSpec("value", "attr", "value", None, dtype="float", optional=True),
        ParamSpec("constant", "number", "size", None, min=0.0, max=1.0, unit="frac",
                  optional=True),
        _either_side_param(),
    ),
    inputs=OPTIONAL_VALUE, impl=None, check=_check_size,
    has_output=False, bypassable=False,
    reads=("@float",), writes=(), subtitle="Size", ins=1, outs=0, module=__name__,
)

register_node(
    name="stop", cat="output",
    desc=(
        "Stop terminal. Fixed stop as a percent from the entry price (above entry for a "
        "short). Wire a number in, or set the constant; 0 means no stop. A wired value is "
        "read at the entry bar and holds for the whole trade; no value there means no entry."
    ),
    params=(
        ParamSpec("value", "attr", "value", None, dtype="float", optional=True),
        ParamSpec("constant", "number", "pct", None, min=0.0, unit="%", optional=True),
        _either_side_param(),
    ),
    inputs=OPTIONAL_VALUE, impl=None, check=_check_stop,
    has_output=False, bypassable=False,
    reads=("@float",), writes=(), subtitle="Stop", ins=1, outs=0, module=__name__,
)


# ---------------------------------------------------------------------------
# Trailing stop: every TrailingStopConfig field, by its own name.  It was a
# settings node until W5; the type name and the params are unchanged, so
# stored graphs and from_rules keep working.
# ---------------------------------------------------------------------------


def _check_trailing(ctx) -> None:
    _refuse_direction(ctx)
    _check_either_side(ctx)
    if "pct" in ctx.raw:
        # The plan's terminal table calls the trail size "pct"; the node
        # keeps TrailingStopConfig's name "value".  Refuse "pct" rather than
        # ignore it, so a trail set under that name is never silently lost.
        ctx.fail("param_invalid",
                 f"Trailing stop {ctx.node_id!r} sets the trail size with value "
                 f"(TrailingStopConfig's name), not pct.", param="pct")
    try:
        config = trailing_stop_config(ctx.raw)
    except ValueError as exc:
        ctx.fail("param_invalid", f"Trailing stop {ctx.node_id!r} has invalid params: {exc}")
    _number(ctx, "Trailing stop value", config.value, minimum=0.0, allow_min=False, param="value")
    _number(ctx, "Trailing stop activate_pct", config.activate_pct, minimum=0.0, allow_min=True,
            param="activate_pct")
    ctx.params["value"] = float(config.value)
    if _both_sides(ctx):
        ctx.annotations["settings"] = [("trailing_stop", config)]


register_node(
    name="trailing_stop", cat="output",
    desc=(
        "Trailing stop terminal. type=pct trails value % from the peak; type=atr trails "
        "value x ATR(14). Starts at once, or (activate on profit) once the trade is "
        "activate_pct % in profit."
    ),
    params=(
        ParamSpec("type", "select", "type", TRAILING_STOP_DEFAULTS["type"],
                  options=TRAILING_STOP_TYPE_OPTIONS),
        # A percent when type=pct, a multiple of ATR when type=atr.
        ParamSpec("value", "number", "value", TRAILING_STOP_DEFAULTS["value"],
                  min=0.0, unit="% or x ATR"),
        ParamSpec("source", "select", "source", TRAILING_STOP_DEFAULTS["source"],
                  options=TRAILING_STOP_SOURCE_OPTIONS),
        ParamSpec("activate_on_profit", "bool", "activate on profit",
                  TRAILING_STOP_DEFAULTS["activate_on_profit"]),
        ParamSpec("activate_pct", "number", "activate pct", TRAILING_STOP_DEFAULTS["activate_pct"],
                  min=0.0, unit="%"),
        _either_side_param(),
        _detail_out("@trail_value"),
    ),
    inputs=OPTIONAL_IN, impl=_write_detail(("out",), ("value",)), check=_check_trailing,
    # Still bypassable, unlike the other terminals: as a settings node a
    # bypassed trailing stop did not apply, and a stored graph that has one
    # must not start trailing after the move.
    detail_writes=("out",), has_output=False,
    reads=(), writes=("@trail_value",), ins=1, outs=1,
    subtitle="Trail: 5%", setting_key="trailing_stop", module=__name__,
)


# ---------------------------------------------------------------------------
# Time stop: evaluated inside the simulator (divergence V6), as max_bars_held.
# ---------------------------------------------------------------------------


def _check_time_stop(ctx) -> None:
    _check_either_side(ctx)
    raw = ctx.raw.get("max_bars")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        ctx.params["max_bars"] = None
        return
    ctx.number_params()  # a whole number >= 1, stored as an int
    if _both_sides(ctx):
        ctx.annotations["settings"] = [("max_bars_held", int(ctx.params["max_bars"]))]


def _write_max_bars(inputs, p):
    # The detail shows the cook's time stop in the data sheet (NaN: none).
    value = p.get("max_bars")
    return inputs.with_detail(p["out"], float(value) if value is not None else math.nan,
                              p.node_id, "float")


register_node(
    name="time_stop", cat="output",
    desc="Time stop terminal. Closes the position after this many bars.",
    params=(
        ParamSpec("max_bars", "int", "max bars", None, min=1, unit="bars", optional=True),
        _either_side_param(),
        _detail_out("@max_bars"),
    ),
    inputs=OPTIONAL_IN, impl=_write_max_bars, check=_check_time_stop,
    detail_writes=("out",), has_output=False, bypassable=False,
    reads=(), writes=("@max_bars",), ins=1, outs=1, subtitle="Time stop", module=__name__,
)


# ---------------------------------------------------------------------------
# Regime: entries only while the signal is true.
# ---------------------------------------------------------------------------


def _check_regime(ctx) -> None:
    # Never let a bypass or an empty read quietly drop the regime filter: the
    # strategy would then trade in every regime.
    ctx.handled.add("signal")
    info = ctx.reads.get("signal")
    if info is None or info.status == NONE:
        ctx.fail("missing_input",
                 f"Regime terminal {ctx.node_id!r} is not wired to a signal.",
                 param="signal", port=(info.port if info is not None else None) or "in0")
    if info.status == DISABLED:
        ctx.fail("missing_input",
                 f"Regime terminal {ctx.node_id!r} gets no signal: its input is bypassed.",
                 param="signal", port=info.port or "in0")
    on_flip = ctx.params.get("on_flip")
    if on_flip in (None, ""):
        ctx.params["on_flip"] = "hold"
    elif on_flip not in ON_FLIP_OPTIONS:
        ctx.fail("param_invalid",
                 f"Regime terminal {ctx.node_id!r} on_flip must be one of {ON_FLIP_OPTIONS}, "
                 f"got {on_flip!r}.", param="on_flip")


register_node(
    name="regime", cat="output",
    desc=(
        "Regime terminal. Trading is allowed only while the signal is true. On flip says "
        "what happens to an open position: hold it, close it, or close and reverse."
    ),
    params=(
        ParamSpec("signal", "attr", "signal", None, dtype="bool"),
        ParamSpec("on_flip", "select", "on flip", "hold", options=ON_FLIP_OPTIONS),
    ),
    inputs=ONE_SIGNAL, impl=None, check=_check_regime,
    has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Regime", ins=1, outs=0, module=__name__,
)
