"""Settings nodes: position size, stop loss, slippage, commission, trailing stop.

A settings node takes no input.  It writes its value as a detail attribute
(plan 3.2: @size_frac, @stop_pct, @slippage_bps ...), one value per cook.
Its check also leaves the simulator setting it stands for in
``annotations["settings"]`` as (key, value) pairs; nodebuilder.compile turns
those into CompiledProgram.simulator_settings, the out-of-band list the
Wave 0 overlay code reads (removed in W5).

Values are checked at compile time with the node named, so a bad one is a
400 on Run or on deploy rather than a failure on every bot tick.  A
bypassed settings node does not apply.  Nothing may be wired out of a
settings node yet (W4/W5 wire them into the size and stop terminals).
"""
from __future__ import annotations

import math
from typing import Any

from nodebuilder.kernel.registry import NO_INPUTS, ParamSpec, register_node
from nodebuilder.kernel.schema import UnsupportedNodeError

# Trailing stop choices.  They match what the simulator reads from
# models.TrailingStopConfig: type "pct" or "atr", and the price ("high" or
# "close") that moves the peak.  The defaults are TrailingStopConfig's own.
TRAILING_STOP_TYPE_OPTIONS: tuple[str, ...] = ("pct", "atr")
TRAILING_STOP_SOURCE_OPTIONS: tuple[str, ...] = ("high", "close")
TRAILING_STOP_DEFAULTS: dict[str, Any] = {
    "type": "pct",
    "value": 5.0,
    "source": "high",
    "activate_on_profit": False,
    "activate_pct": 0.0,
}


def _detail_out(default: str, name: str = "out") -> ParamSpec:
    return ParamSpec(name, "write", name, default, dtype="float")


def _number(ctx, label: str, value: Any, *, minimum: float, allow_min: bool, param: str) -> float:
    """A settings value as a finite number no lower than *minimum*.

    Raises param_invalid (blank, not a number, infinite) or
    param_out_of_range (too small), naming the node and the param.
    """
    try:
        if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
            raise ValueError
        number = float(value)
    except (TypeError, ValueError):
        ctx.fail("param_invalid", f"{label} on {ctx.node_id!r} needs a number, got {value!r}.",
                 param=param)
    too_small = number < minimum if allow_min else number <= minimum
    if not math.isfinite(number) or too_small:
        bound = f">= {minimum:g}" if allow_min else f"> {minimum:g}"
        ctx.fail("param_out_of_range" if math.isfinite(number) else "param_invalid",
                 f"{label} on {ctx.node_id!r} must be {bound}, got {value!r}.", param=param)
    return number


def _refuse_direction(ctx) -> None:
    # A per-direction stop or trailing stop (from a b23 long/short strategy)
    # cannot be expressed yet: the simulator settings have one value for both
    # sides.  Applying it to both would trade differently, so refuse it.
    if "direction" in ctx.raw:
        direction = ctx.raw["direction"]
        ctx.fail("unsupported_node", "", param="direction",
                 cls=lambda _m, node_id: UnsupportedNodeError(
                     node_id, f"{ctx.label} (direction={direction})"))


def _write_detail(slots: tuple[str, ...], values: tuple[str, ...]):
    def impl(inputs, p):
        out = inputs
        for slot, value in zip(slots, values):
            out = out.with_detail(p[slot], float(p[value]), p.node_id, "float")
        return out
    return impl


# ---------------------------------------------------------------------------
# Position size
# ---------------------------------------------------------------------------


def _check_size(ctx) -> None:
    _refuse_direction(ctx)
    size = _number(ctx, "Size", ctx.raw.get("size", 1.0), minimum=0.0, allow_min=False,
                   param="size")
    if size > 1.0:
        # Size is a fraction of capital (1 = 100%).  50 was most likely meant
        # as 50%.
        ctx.warn("size_unit_suspect",
                 f"Size on {ctx.node_id!r} is {size:g}, but size is a fraction of capital "
                 f"(1 = 100%, 0.5 = 50%).", param="size")
    ctx.params["size"] = size
    ctx.annotations["settings"] = [("position_size", size)]


register_node(
    name="position_size", cat="settings",
    desc="Fraction of allocated capital used per trade, from 0 to 1 (1 = 100%, 0.5 = 50%). Default: 1.",
    params=(ParamSpec("size", "number", "size", 1.0, min=0.0, max=1.0, unit="frac"),
            _detail_out("@size_frac")),
    impl=_write_detail(("out",), ("size",)), check=_check_size,
    detail_writes=("out",), has_output=False,
    reads=(), writes=("@size_frac",), ins=0, outs=1,
    # Shows the stored value too, so 1 is not read as 1%.
    subtitle="Size: 1 (100%)", setting_key="position_size", module=__name__,
)


# ---------------------------------------------------------------------------
# Stop loss
# ---------------------------------------------------------------------------


def _check_stop(ctx) -> None:
    _refuse_direction(ctx)
    pct = _number(ctx, "Stop loss pct", ctx.raw.get("pct", 5.0), minimum=0.0, allow_min=True,
                  param="pct")
    ctx.params["pct"] = pct
    ctx.annotations["settings"] = [("stop_loss", pct)]


register_node(
    name="stop_loss", cat="settings",
    desc="Fixed stop-loss as a percentage below/above entry. Default: 5.0%.",
    # A stop of 0 means "no stop" on both backtest and live.
    params=(ParamSpec("pct", "number", "pct", 5.0, min=0.0, unit="%", optional=True),
            _detail_out("@stop_pct")),
    impl=_write_detail(("out",), ("pct",)), check=_check_stop,
    detail_writes=("out",), has_output=False,
    reads=(), writes=("@stop_pct",), ins=0, outs=1,
    subtitle="Stop: 5%", setting_key="stop_loss", module=__name__,
)


# ---------------------------------------------------------------------------
# Slippage
# ---------------------------------------------------------------------------


def _check_slippage(ctx) -> None:
    _refuse_direction(ctx)
    bps = _number(ctx, "Slippage bps", ctx.raw.get("bps", 2.0), minimum=0.0, allow_min=True,
                  param="bps")
    ctx.params["bps"] = bps
    ctx.annotations["settings"] = [("slippage_bps", bps)]


register_node(
    name="slippage", cat="settings",
    desc="Modeled slippage cost per leg in basis points. Default: 2.0 bps.",
    params=(ParamSpec("bps", "number", "bps", 2.0, min=0.0, unit="bps"),
            _detail_out("@slippage_bps")),
    impl=_write_detail(("out",), ("bps",)), check=_check_slippage,
    detail_writes=("out",), has_output=False,
    reads=(), writes=("@slippage_bps",), ins=0, outs=1,
    subtitle="Slippage: 2 bps", setting_key="slippage_bps", module=__name__,
)


# ---------------------------------------------------------------------------
# Commission
# ---------------------------------------------------------------------------


def _check_commission(ctx) -> None:
    _refuse_direction(ctx)
    rate = _number(ctx, "Per-share rate", ctx.raw.get("per_share_rate", 0.0), minimum=0.0,
                   allow_min=True, param="per_share_rate")
    min_order = _number(ctx, "Min per order", ctx.raw.get("min_per_order", 0.0), minimum=0.0,
                        allow_min=True, param="min_per_order")
    ctx.params["per_share_rate"] = rate
    ctx.params["min_per_order"] = min_order
    ctx.annotations["settings"] = [("per_share_rate", rate), ("min_per_order", min_order)]


register_node(
    name="commission", cat="settings",
    desc="Per-share commission rate and minimum per order. Defaults match Alpaca (free).",
    params=(
        ParamSpec("per_share_rate", "number", "per share", 0.0, min=0.0, unit="$/share"),
        ParamSpec("min_per_order", "number", "min per order", 0.0, min=0.0, unit="$"),
        _detail_out("@per_share_rate", "out_rate"),
        _detail_out("@min_per_order", "out_min"),
    ),
    impl=_write_detail(("out_rate", "out_min"), ("per_share_rate", "min_per_order")),
    check=_check_commission, detail_writes=("out_rate", "out_min"), has_output=False,
    reads=(), writes=("@per_share_rate", "@min_per_order"), ins=0, outs=1,
    subtitle="Commission: free", setting_key="commission", module=__name__,
)


# ---------------------------------------------------------------------------
# Trailing stop: the same five fields as models.TrailingStopConfig, so a rule
# strategy's trailing stop renders as this node and runs the same.
# ---------------------------------------------------------------------------


def trailing_stop_config(params: dict):
    """The TrailingStopConfig the simulator reads, from the node's params.

    Missing params take TrailingStopConfig's defaults.  Raises ValueError for
    a type or source the simulator does not know, and for a bad field value.
    """
    from models import TrailingStopConfig  # local: keep this module import-light

    fields = {k: v for k, v in params.items() if k in TRAILING_STOP_DEFAULTS}
    config = TrailingStopConfig(**fields)  # pydantic errors are ValueErrors
    if config.type not in TRAILING_STOP_TYPE_OPTIONS:
        raise ValueError(
            f"trailing stop type must be one of {TRAILING_STOP_TYPE_OPTIONS}, got {config.type!r}"
        )
    if config.source not in TRAILING_STOP_SOURCE_OPTIONS:
        raise ValueError(
            f"trailing stop source must be one of {TRAILING_STOP_SOURCE_OPTIONS}, got {config.source!r}"
        )
    return config


def _check_trailing(ctx) -> None:
    _refuse_direction(ctx)
    try:
        config = trailing_stop_config(ctx.raw)
    except ValueError as exc:
        ctx.fail("param_invalid", f"Trailing stop {ctx.node_id!r} has invalid params: {exc}")
    _number(ctx, "Trailing stop value", config.value, minimum=0.0, allow_min=False, param="value")
    _number(ctx, "Trailing stop activate_pct", config.activate_pct, minimum=0.0, allow_min=True,
            param="activate_pct")
    ctx.params["value"] = float(config.value)
    ctx.annotations["settings"] = [("trailing_stop", config)]


register_node(
    name="trailing_stop", cat="settings",
    desc=(
        "Trailing stop. type=pct trails value % from the peak; type=atr trails "
        "value x ATR(14). Optionally waits until the trade is activate_pct % in profit."
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
        _detail_out("@trail_value"),
    ),
    impl=_write_detail(("out",), ("value",)), check=_check_trailing,
    detail_writes=("out",), has_output=False,
    reads=(), writes=("@trail_value",), ins=0, outs=1,
    subtitle="Trail: 5%", setting_key="trailing_stop", module=__name__,
)
