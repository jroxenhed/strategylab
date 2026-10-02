"""More indicator nodes: stochastic, ADX, ATR%, volume, MA (any type), price.

They cover the rest of the rule builder's indicators.  Like the nodes in
nodes_indicators.py, each runs through ``indicators.compute_instance`` (one
computation per cook for the same inputs and args), so the values match the
chart and signal_engine.compute_indicators exactly:

- stochastic: %K and %D from high, low and the source (the close).
- adx: ADX, +DI and -DI from high, low and the source.
- atr_pct: ATR as a percent of the source: atr / close * 100.  It shares
  the ATR column with an ATR node of the same period.
- volume: the volume itself (type raw) or its simple moving average (type
  sma); the source defaults to @volume.
- ma: a moving average of any type indicators.compute_ma accepts.
- price: one price field (default @close), under its own name.
"""
from __future__ import annotations

import numpy as np

from nodebuilder.kernel.registry import ParamSpec, register_node
from nodebuilder.trading.nodes_indicators import (
    RECURSIVE_FACTOR,
    SOURCE_INPUT,
    compute,
    out_param,
    period,
    source_param,
)
from nodebuilder.trading.nodes_math import check_select


def _write(inputs, params, slots: tuple[str, ...], keys: tuple[int, ...]):
    out = inputs
    for slot, key in zip(slots, keys):
        out = out.with_point_key(params[slot], key, "float", params.node_id)
    return out


def _high_low() -> tuple[ParamSpec, ParamSpec]:
    return (ParamSpec("high", "attr", "high", "@high", dtype="float"),
            ParamSpec("low", "attr", "low", "@low", dtype="float"))


def _check_numbers(ctx) -> None:
    ctx.number_params()


# ---------------------------------------------------------------------------
# Stochastic
# ---------------------------------------------------------------------------

STOCH_SLOTS = ("out_k", "out_d")


def _stochastic(inputs, p):
    args = {"k_period": p["k_period"], "d_period": p["d_period"], "smooth_k": p["smooth_k"]}
    keys = compute(inputs, p, "stochastic", args, ("k", "d"),
                   reads=("source", "high", "low"), high="high", low="low")
    return _write(inputs, p, STOCH_SLOTS, keys)


register_node(
    name="stochastic", cat="indicator",
    desc="Stochastic oscillator: %K and %D. Defaults: k_period=14, d_period=3, smooth_k=3.",
    params=(
        period(14, "k_period"),
        period(3, "d_period"),
        # The indicator accepts 1 to 50 for the %K smoothing.
        ParamSpec("smooth_k", "int", "smooth_k", 3, min=1, max=50, unit="bars"),
        source_param(),
        *_high_low(),
        out_param("@stoch_k", "out_k"),
        out_param("@stoch_d", "out_d"),
    ),
    inputs=SOURCE_INPUT, impl=_stochastic, check=_check_numbers,
    # Three rolling windows one after another.
    lookback=lambda p: int(p["k_period"]) + int(p["smooth_k"]) + int(p["d_period"]) - 2,
    reads=("@high", "@low", "@close"), subtitle="Stoch(14,3,3)", ins=1, outs=2,
    meta={"family": "stochastic", "spec": ("k_period", "d_period", "smooth_k")},
    module=__name__,
)


# ---------------------------------------------------------------------------
# ADX
# ---------------------------------------------------------------------------

ADX_SLOTS = ("out_adx", "out_plus_di", "out_minus_di")


def _adx(inputs, p):
    keys = compute(inputs, p, "adx", {"period": p["period"]}, ("adx", "plus_di", "minus_di"),
                   reads=("source", "high", "low"), high="high", low="low")
    return _write(inputs, p, ADX_SLOTS, keys)


register_node(
    name="adx", cat="indicator",
    desc="Average Directional Index with +DI and -DI. Default period=14.",
    params=(
        period(14),
        source_param(),
        *_high_low(),
        out_param("@adx", "out_adx"),
        out_param("@plus_di", "out_plus_di"),
        out_param("@minus_di", "out_minus_di"),
    ),
    inputs=SOURCE_INPUT, impl=_adx, check=_check_numbers,
    # Wilder smoothing twice over (the DI lines, then the ADX).
    lookback=lambda p: RECURSIVE_FACTOR * 2 * int(p["period"]) + 1,
    reads=("@high", "@low", "@close"), subtitle="ADX(14)", ins=1, outs=3,
    meta={"family": "adx", "spec": ("period",)},
    module=__name__,
)


# ---------------------------------------------------------------------------
# ATR %
# ---------------------------------------------------------------------------


def _atr_pct(inputs, p):
    # The same memo key as an ATR node, so both share one ATR column.
    (atr_key,) = compute(inputs, p, "atr", {"period": p["period"]}, ("atr",),
                         reads=("source", "high", "low"), high="high", low="low")
    atr = inputs.store.get(atr_key)
    close = np.asarray(inputs.column(p["source"]), dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        # Same order of operations as signal_engine: atr / close * 100.
        pct = atr / close * 100
    return inputs.with_point(p["out"], pct, p.node_id, "float")


register_node(
    name="atr_pct", cat="indicator",
    desc="ATR as a percent of the close (atr / close * 100). Default period=14.",
    params=(
        period(14),
        source_param(),
        *_high_low(),
        out_param("@atr_pct"),
    ),
    inputs=SOURCE_INPUT, impl=_atr_pct, check=_check_numbers,
    # A rolling mean of the true range (as ATR): exact after period + 1 bars.
    lookback=lambda p: int(p["period"]) + 1,
    reads=("@high", "@low", "@close"), subtitle="ATR%(14)", ins=1, outs=1,
    meta={"family": "atr", "spec": ("period",)},
    module=__name__,
)


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

VOLUME_TYPES: tuple[str, ...] = ("raw", "sma")


def _check_volume(ctx) -> None:
    check_select(ctx, "type")
    ctx.number_params()


def _volume(inputs, p):
    if p["type"] == "sma":
        # signal_engine takes volume.rolling(period).mean(); compute_ma with
        # type sma on the volume is that same call.
        keys = compute(inputs, p, "ma", {"period": p["period"], "type": "sma"}, ("ma",))
    else:
        keys = compute(inputs, p, "volume", {}, ("volume",))
    return _write(inputs, p, ("out",), keys)


register_node(
    name="volume", cat="indicator",
    desc="Volume: the raw volume (type raw) or its simple moving average (type sma, "
         "default period=20).",
    params=(
        ParamSpec("type", "select", "type", "raw", options=VOLUME_TYPES),
        period(20),
        ParamSpec("source", "attr", "source", "@volume", dtype="float"),
        out_param("@vol"),
    ),
    inputs=SOURCE_INPUT, impl=_volume, check=_check_volume,
    lookback=lambda p: int(p["period"]) if p.get("type") == "sma" else 0,
    subtitle="volume", ins=1, outs=1,
    meta={"family": "volume", "spec": ("type", "period")},
    module=__name__,
)


# ---------------------------------------------------------------------------
# MA (any type)
# ---------------------------------------------------------------------------

# Every type indicators.compute_ma tells apart: sma (rolling mean), rma
# (Wilder) and ema.  It runs any other name as an EMA, so a typo is refused.
MA_TYPES: tuple[str, ...] = ("sma", "ema", "rma")
# signal_engine reads a rule MA with no type as an EMA.
MA_DEFAULT_TYPE = "ema"


def _check_ma(ctx) -> None:
    check_select(ctx, "type")
    ctx.number_params()


def _ma(inputs, p):
    keys = compute(inputs, p, "ma", {"period": p["period"], "type": p["type"]}, ("ma",))
    return _write(inputs, p, ("out",), keys)


def _ma_lookback(p) -> int:
    n = int(p["period"])
    return n if p.get("type") == "sma" else RECURSIVE_FACTOR * n


register_node(
    name="ma", cat="indicator",
    desc="Moving average of any type: sma, ema or rma (Wilder). Default period=20, type=ema.",
    params=(
        period(20),
        ParamSpec("type", "select", "type", MA_DEFAULT_TYPE, options=MA_TYPES),
        source_param(),
        out_param("@ma"),
    ),
    inputs=SOURCE_INPUT, impl=_ma, check=_check_ma, lookback=_ma_lookback,
    reads=("@close",), subtitle="MA(20, ema)", ins=1, outs=1,
    meta={"family": "ma", "spec": ("period", "type")},
    module=__name__,
)


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------


def _price(inputs, p):
    values = np.asarray(inputs.column(p["field"]), dtype=np.float64)
    return inputs.with_point(p["out"], values, p.node_id, "float")


register_node(
    name="price", cat="data",
    desc="One price field (default @close) under its own name.",
    params=(
        ParamSpec("field", "attr", "field", "@close", dtype="float"),
        out_param("@price"),
    ),
    inputs=SOURCE_INPUT, impl=_price, check=_check_numbers,
    subtitle="close", ins=1, outs=1, module=__name__,
)
