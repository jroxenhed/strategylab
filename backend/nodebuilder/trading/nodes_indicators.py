"""Indicator nodes: RSI, MACD, SMA, EMA, Bollinger, ATR.

Each one reads its ``source`` attribute from its input stream (by default
the primary write of the node wired in: a Ticker's @close, or an RSI's @rsi,
so EMA of RSI works) and calls ``indicators.compute_instance``, the same
leaf the chart and the rule engine use, so values match them exactly.

The values the node writes are added to the stream it got, so everything
upstream (the bars, other indicators) flows on below it.
"""
from __future__ import annotations

from typing import Any

from indicators import OHLCVSeries, compute_instance
from nodebuilder.kernel.evaluate import memo_columns
from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node

# RSI smoothing types.  Must match what indicators.compute_rsi accepts: it
# uses Wilder smoothing for "wilder" and a plain rolling mean for anything
# else, so only these two are real choices.  The default matches the rule
# builder, which picks Wilder for a new RSI rule.
RSI_TYPE_OPTIONS: tuple[str, ...] = ("sma", "wilder")
RSI_DEFAULT_TYPE: str = "wilder"

# An indicator has one input; its source defaults to what that input's node
# writes first.
SOURCE_INPUT = PortsSpec(ports=(PortSpec("source"),), dynamic=False, min=1, max=1)

# Recursive smoothers (EMA family, Wilder RSI) remember the whole past.
# (ATR is a plain rolling mean of the true range, so its lookback is exactly
# period + 1: one previous close for the first true range.)
# A cook over required_lookback_bars bars must give the full-history value,
# so the lookback counts them at this many times their period.  Wilder
# smoothing (alpha 1/n) forgets its seed about twice as slowly as an EMA
# (alpha 2/(n+1)), so it sets the factor.  Measured on the parity fixtures
# (SPY/AAPL daily, last value of a cook over exactly the lookback against the
# full history): Wilder RSI 14 was up to 8.0 RSI points off at factor 3, 1.6
# at 5, 0.24 at 7 and 0.012 at 10; EMA 50 and MACD are within 1e-7 relative
# at 10.  test_live_window_parity.py::test_lookback_alone_matches_full_history
# pins this.  (F435 W2 integration; 3 was the D5 first guess.)
RECURSIVE_FACTOR = 10


def period(default: int, name: str = "period") -> ParamSpec:
    """An indicator lookback in bars.  The indicators accept 2 to 500."""
    return ParamSpec(name, "int", name, default, min=2, max=500, unit="bars")


def source_param() -> ParamSpec:
    """The attribute an indicator is computed on (empty: the wired node's
    primary write)."""
    return ParamSpec("source", "attr", "source", None, dtype="float")


def out_param(default: str, name: str = "out") -> ParamSpec:
    return ParamSpec(name, "write", name, default, dtype="float")


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _attr_key(inputs, name: str) -> tuple:
    """What identifies the values of *name* in this cook (for the memo)."""
    key = inputs.column_key(name)
    if key is not None:
        return ("col", key)
    return ("detail", name, inputs.written_by.get(name), repr(inputs.value(name)))


def ohlcv_for(inputs, params, close_param: str = "source",
              high_param: str | None = None, low_param: str | None = None) -> OHLCVSeries:
    """An OHLCVSeries for compute_instance: close is the source attribute;
    high and low come from their params when the node has them."""
    # As floats: a bool signal or an int volume can be a source too.  A
    # float column is passed as it is (no copy).
    close = inputs.series(params[close_param], float)
    high = inputs.series(params[high_param], float) if high_param else close
    low = inputs.series(params[low_param], float) if low_param else close
    return OHLCVSeries(close=close, high=high, low=low, volume=close)


def compute(inputs, params, family: str, args: dict[str, Any], outputs: tuple[str, ...],
            reads: tuple[str, ...] = ("source",), high: str | None = None,
            low: str | None = None) -> tuple[int, ...]:
    """Run one indicator through compute_instance (once per cook for the same
    family, args and inputs) and return the column keys of *outputs*."""
    key = (family, tuple(sorted(args.items())), tuple(_attr_key(inputs, params[r]) for r in reads))

    def _build():
        result = compute_instance(family, args, ohlcv_for(inputs, params, "source", high, low))
        return tuple(result[o].to_numpy(dtype=float, copy=False) for o in outputs)

    return memo_columns(inputs, params, key, _build)


def _write(inputs, params, slots: tuple[str, ...], keys: tuple[int, ...]):
    out = inputs
    for slot, key in zip(slots, keys):
        out = out.with_point_key(params[slot], key, "float", params.node_id)
    return out


def _check_numbers(ctx) -> None:
    ctx.number_params()


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def _check_rsi(ctx) -> None:
    # The RSI indicator runs any unknown type as a plain rolling mean, so a
    # typo would quietly change the strategy.  Refuse it.
    rsi_type = ctx.params.get("type")
    if rsi_type not in RSI_TYPE_OPTIONS:
        ctx.fail("param_invalid",
                 f"RSI {ctx.node_id!r} has type {rsi_type!r}; use one of {list(RSI_TYPE_OPTIONS)}.",
                 param="type")
    ctx.number_params()


def _rsi(inputs, p):
    keys = compute(inputs, p, "rsi", {"period": p["period"], "type": p["type"]}, ("rsi",))
    return _write(inputs, p, ("out",), keys)


def _rsi_lookback(p) -> int:
    n = int(p["period"])
    return (RECURSIVE_FACTOR * n if p.get("type") == "wilder" else n) + 1


register_node(
    name="rsi", cat="indicator",
    desc="Relative Strength Index. Default period=14, type=wilder (sma also available).",
    params=(
        period(14),
        ParamSpec("type", "select", "type", RSI_DEFAULT_TYPE, options=RSI_TYPE_OPTIONS),
        source_param(),
        out_param("@rsi"),
    ),
    inputs=SOURCE_INPUT, impl=_rsi, check=_check_rsi, lookback=_rsi_lookback,
    reads=("@close",), subtitle="RSI(14)", ins=1, outs=1,
    legacy_ignore=("source",), meta={"family": "rsi", "spec": ("period", "type")},
    module=__name__,
)


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

MACD_SLOTS = ("out_line", "out_signal", "out_hist")


def _macd(inputs, p):
    args = {"fast": p["fast"], "slow": p["slow"], "signal": p["signal"]}
    keys = compute(inputs, p, "macd", args, ("macd", "signal", "histogram"))
    return _write(inputs, p, MACD_SLOTS, keys)


register_node(
    name="macd", cat="indicator",
    desc="MACD: line, signal, and histogram series. Defaults: fast=12, slow=26, signal=9.",
    params=(
        period(12, "fast"), period(26, "slow"), period(9, "signal"),
        source_param(),
        out_param("@macd_line", "out_line"),
        out_param("@macd_signal", "out_signal"),
        out_param("@macd_histogram", "out_hist"),
    ),
    inputs=SOURCE_INPUT, impl=_macd, check=_check_numbers,
    lookback=lambda p: RECURSIVE_FACTOR * (int(p["slow"]) + int(p["signal"])),
    reads=("@close",), subtitle="MACD(12,26,9)", ins=1, outs=3,
    legacy_reads={"@macd_line": "out_line", "@macd_signal": "out_signal",
                  "@macd_histogram": "out_hist"},
    legacy_strict=True, legacy_ignore=("source",),
    meta={"family": "macd", "spec": ("fast", "slow", "signal")},
    module=__name__,
)


# ---------------------------------------------------------------------------
# SMA and EMA (indicators.compute_ma with a fixed type)
# ---------------------------------------------------------------------------


def _ma(ma_type: str):
    def impl(inputs, p):
        keys = compute(inputs, p, "ma", {"period": p["period"], "type": ma_type}, ("ma",))
        return _write(inputs, p, ("out",), keys)
    impl.__name__ = f"_{ma_type}"
    return impl


register_node(
    name="sma", cat="indicator", desc="Simple Moving Average. Default period=20.",
    params=(period(20), source_param(), out_param("@sma")),
    inputs=SOURCE_INPUT, impl=_ma("sma"), check=_check_numbers,
    lookback=lambda p: int(p["period"]),
    reads=("@close",), subtitle="SMA(20)", ins=1, outs=1,
    legacy_ignore=("source",), meta={"family": "ma", "spec": ("period",)},
    module=__name__,
)

register_node(
    name="ema", cat="indicator", desc="Exponential Moving Average. Default period=20.",
    params=(period(20), source_param(), out_param("@ema")),
    inputs=SOURCE_INPUT, impl=_ma("ema"), check=_check_numbers,
    lookback=lambda p: RECURSIVE_FACTOR * int(p["period"]),
    reads=("@close",), subtitle="EMA(20)", ins=1, outs=1,
    legacy_ignore=("source",), meta={"family": "ma", "spec": ("period",)},
    module=__name__,
)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

BB_SLOTS = ("out_upper", "out_middle", "out_lower")


def _bollinger(inputs, p):
    keys = compute(inputs, p, "bb", {"period": p["period"], "stddev": p["stddev"]},
                   ("upper", "middle", "lower"))
    return _write(inputs, p, BB_SLOTS, keys)


register_node(
    name="bollinger", cat="indicator",
    desc="Bollinger Bands: upper, middle, lower. Default period=20, stddev=2.",
    params=(
        period(20),
        # How many standard deviations the bands sit from the middle.
        ParamSpec("stddev", "number", "stddev", 2.0, min=0.5, max=5.0),
        source_param(),
        out_param("@bb_upper", "out_upper"),
        out_param("@bb_middle", "out_middle"),
        out_param("@bb_lower", "out_lower"),
    ),
    inputs=SOURCE_INPUT, impl=_bollinger, check=_check_numbers,
    lookback=lambda p: int(p["period"]),
    reads=("@close",), subtitle="BB(20,2)", ins=1, outs=3,
    legacy_reads={"@bb_upper": "out_upper", "@bb_middle": "out_middle", "@bb_lower": "out_lower"},
    legacy_strict=True, legacy_ignore=("source",),
    meta={"family": "bb", "spec": ("period", "stddev")},
    module=__name__,
)


# ---------------------------------------------------------------------------
# ATR (reads high and low as well as its source, the close)
# ---------------------------------------------------------------------------


def _atr(inputs, p):
    keys = compute(inputs, p, "atr", {"period": p["period"]}, ("atr",),
                   reads=("source", "high", "low"), high="high", low="low")
    return _write(inputs, p, ("out",), keys)


register_node(
    name="atr", cat="indicator", desc="Average True Range. Default period=14.",
    params=(
        period(14),
        source_param(),
        ParamSpec("high", "attr", "high", "@high", dtype="float"),
        ParamSpec("low", "attr", "low", "@low", dtype="float"),
        out_param("@atr"),
    ),
    inputs=SOURCE_INPUT, impl=_atr, check=_check_numbers,
    # compute_atr is tr.rolling(period).mean(): exact after period + 1 bars.
    lookback=lambda p: int(p["period"]) + 1,
    reads=("@high", "@low", "@close"), subtitle="ATR(14)", ins=3, outs=1,
    legacy_ignore=("source", "high", "low"), meta={"family": "atr", "spec": ("period",)},
    module=__name__,
)
