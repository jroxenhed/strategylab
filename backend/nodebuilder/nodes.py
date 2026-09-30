"""Core 14 node catalog metadata + Unit 7b impl functions.

Unit 2: NODE_CATALOG + helpers (metadata only, no runtime impls).
Unit 7b: adds impl functions (rsi_impl, macd_impl, etc.) + result dataclasses
         + NODE_IMPLS registry.
F435 1.D: every entry carries ParamSpec and PortsSpec (plan section 4.3).
         This file is the one source of truth for the node catalog:
         backend/scripts/export_nodebuilder_catalog.py writes it out as
         frontend/src/features/nodebuilder/catalog.generated.ts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Param and port specs (plan section 4.3)
# ---------------------------------------------------------------------------

# Every kind of param a node can have.  "attr", "attr_list", "write", "path"
# and "time_range" are used from Wave 2 on; they are listed now so the
# frontend type is complete.
PARAM_TYPES: tuple[str, ...] = (
    "number", "int", "string", "select", "bool",
    "attr", "attr_list", "write", "path", "time_range",
)

# Units a number param can be read in.  "frac" is a fraction (1 = 100%).
# "$" and "$/share" are commission amounts, and "% or x ATR" is the trailing
# stop value, which is a percent or an ATR multiple depending on its type.
PARAM_UNITS: tuple[str, ...] = ("%", "bps", "bars", "frac", "$", "$/share", "% or x ATR")

PARAM_DTYPES: tuple[str, ...] = ("float", "bool", "any")


@dataclass(frozen=True)
class ParamSpec:
    """One param of a node type: its kind, label, default and limits.

    min/max are the limits the impl function enforces (for example a period
    of 2 to 500 bars).  code_able is False for params that may never hold
    code (the Ticker's symbol and interval, plan D1).
    """
    name: str
    type: str
    label: str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: Optional[str] = None
    options: Optional[tuple[str, ...]] = None
    dtype: Optional[str] = None
    optional: bool = False
    code_able: bool = True

    def to_json(self) -> dict[str, Any]:
        """The JSON shape of section 4.3.  Unset optional fields are left out."""
        out: dict[str, Any] = {
            "name": self.name, "type": self.type, "label": self.label, "default": self.default,
        }
        for key in ("min", "max", "step", "unit", "dtype"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.options is not None:
            out["options"] = list(self.options)
        if self.optional:
            out["optional"] = True
        if not self.code_able:
            out["code_able"] = False
        return out


@dataclass(frozen=True)
class PortSpec:
    """One input port.  Its id is in<k> by position; label is what the canvas shows."""
    label: str
    optional: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"label": self.label}
        if self.optional:
            out["optional"] = True
        return out


@dataclass(frozen=True)
class PortsSpec:
    """The input ports of a node type.

    ports   : the ports drawn by default, in order (in0, in1, ...).
    dynamic : True when more ports can be added (AND, OR).  Each extra port
              is labeled by its id.
    min/max : how many wired ports the node needs, and how many it can take.
    """
    ports: tuple[PortSpec, ...]
    dynamic: bool
    min: int
    max: int

    def to_json(self) -> dict[str, Any]:
        return {
            "ports": [p.to_json() for p in self.ports],
            "dynamic": self.dynamic,
            "min": self.min,
            "max": self.max,
        }


# Nodes that take no wire in: the Ticker (a source) and Settings nodes.
NO_INPUTS = PortsSpec(ports=(), dynamic=False, min=0, max=0)

# An indicator reads the Ticker's bars.  The wire is optional: compile reads
# the Ticker's bars even when nothing is wired in.
_SOURCE_INPUT = PortsSpec(ports=(PortSpec("source", optional=True),), dynamic=False, min=0, max=1)

# A comparison takes a (the left side) and b (the right side).  With b empty
# it compares a to its threshold param.
_COMPARISON_INPUTS = PortsSpec(
    ports=(PortSpec("a"), PortSpec("b", optional=True)), dynamic=False, min=1, max=2,
)

# AND/OR take one or more signals.  Compile accepts any count, so max is a
# generous cap for the canvas, not a real limit of the engine.
_LOGIC_INPUTS = PortsSpec(ports=(PortSpec("in0"), PortSpec("in1", optional=True)), dynamic=True, min=1, max=16)

_ONE_SIGNAL = PortsSpec(ports=(PortSpec("signal"),), dynamic=False, min=1, max=1)
# Exit may stay unwired (the strategy then only leaves by stops), and the T4
# Size/Stop terminals are ignored while unwired.
_OPTIONAL_SIGNAL = PortsSpec(ports=(PortSpec("signal", optional=True),), dynamic=False, min=0, max=1)


def _period(default: int, name: str = "period") -> ParamSpec:
    """An indicator lookback in bars.  The impls accept 2 to 500."""
    return ParamSpec(name, "int", name, default, min=2, max=500, unit="bars")


def _threshold() -> ParamSpec:
    """A comparison's threshold, used when b is not wired."""
    return ParamSpec("threshold", "number", "threshold", None, optional=True)


@dataclass(frozen=True)
class NodeCatalogEntry:
    """Static description of a node type in the catalog.

    Fields
    ------
    name          : Unique node-type identifier, e.g. "rsi", "crosses_below".
    cat           : Category string — one of the keys in NODE_CATEGORIES.
    desc          : Short human-readable description (shown in Tab-menu search).
    reads         : Stream attributes this node reads, e.g. ("@close",).
                    Empty tuple for source nodes (ticker) and Settings constants.
    writes        : Stream attributes this node produces, e.g. ("@rsi",).
                    Empty tuple for terminal nodes (entry, exit).
    defaults      : Node-instance defaults dict:
                      "params"   – param defaults (may be empty).  Filled in
                                   from `params`; never written by hand.
                      "param_options" – the options of each select param,
                                   also filled in from `params`.
                      "ins"      – expected number of inbound wires.
                      "outs"     – expected number of outbound wires.
                      "subtitle" – optional subtitle rendered in the node body.
                    Settings nodes include "setting_key" so Unit 7a knows which
                    simulator field to populate.
    compile_active: False for catalog-only nodes that render on the canvas but
                    that compile cannot run yet.  Currently only the "size" and
                    "stop" output terminals: compile ignores them while nothing is
                    wired in, and raises UnsupportedNodeError once something is.
                    Node types missing from the catalog are always refused.
    params        : One ParamSpec per param, in display order (section 4.3).
    inputs        : The input ports (section 4.3).
    """
    name: str
    cat: str
    desc: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    defaults: dict
    compile_active: bool = True
    params: tuple[ParamSpec, ...] = ()
    inputs: PortsSpec = NO_INPUTS

    def __post_init__(self) -> None:
        # The param specs are the only place defaults are written, so the
        # "params" dict that compile reads can never disagree with them.
        if "params" in self.defaults or "param_options" in self.defaults:
            raise ValueError(
                f"catalog entry {self.name!r}: give params as ParamSpec, not in defaults"
            )
        self.defaults["params"] = {p.name: p.default for p in self.params}
        options = {p.name: p.options for p in self.params if p.type == "select"}
        if options:
            self.defaults["param_options"] = options

    def to_json(self) -> dict[str, Any]:
        """The entry as plain JSON, for the frontend codegen."""
        return {
            "name": self.name,
            "cat": self.cat,
            "desc": self.desc,
            "compile_active": self.compile_active,
            "inputs": self.inputs.to_json(),
            "params": [p.to_json() for p in self.params],
            "reads": list(self.reads),
            "writes": list(self.writes),
            "subtitle": self.defaults.get("subtitle"),
            "setting_key": self.defaults.get("setting_key"),
            "ins": self.defaults["ins"],
            "outs": self.defaults["outs"],
        }


# Ticker choices.  The sources are the data providers backend/shared.py
# can register (no polygon: there is no such provider).
INTERVAL_OPTIONS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")
SOURCE_OPTIONS: tuple[str, ...] = ("yahoo", "alpaca", "alpaca-iex", "ibkr")

# RSI smoothing types.  Must match what indicators.compute_rsi accepts: it
# uses Wilder smoothing for "wilder" and a plain rolling mean for anything
# else, so only these two are real choices.  The default matches the rule
# builder, which picks Wilder for a new RSI rule.  The frontend gets these
# through catalog.generated.ts (checked by test_catalog_consistency.py).
RSI_TYPE_OPTIONS: tuple[str, ...] = ("sma", "wilder")
RSI_DEFAULT_TYPE: str = "wilder"

# Trailing stop choices.  They match what the simulator reads from
# models.TrailingStopConfig: type "pct" or "atr", and the price ("high" or
# "close") that moves the peak.  The defaults are TrailingStopConfig's own.
# The frontend gets these through catalog.generated.ts.
TRAILING_STOP_TYPE_OPTIONS: tuple[str, ...] = ("pct", "atr")
TRAILING_STOP_SOURCE_OPTIONS: tuple[str, ...] = ("high", "close")
TRAILING_STOP_DEFAULTS: dict[str, Any] = {
    "type": "pct",
    "value": 5.0,
    "source": "high",
    "activate_on_profit": False,
    "activate_pct": 0.0,
}


# ---------------------------------------------------------------------------
# Catalog — Core 14 (compile-active) + Settings (5) + Output terminals (4)
# ---------------------------------------------------------------------------

NODE_CATALOG: list[NodeCatalogEntry] = [

    # ------------------------------------------------------------------
    # Ticker (source — writes OHLCV attributes, reads nothing)
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="ticker",
        cat="ticker",
        desc="Market data source: OHLCV price series for a symbol.",
        reads=(),
        writes=("@open", "@high", "@low", "@close", "@volume"),
        defaults={
            "ins": 0,
            "outs": 5,
            "subtitle": None,
        },
        params=(
            ParamSpec("symbol", "string", "symbol", "AAPL", code_able=False),
            ParamSpec("interval", "select", "interval", "1d", options=INTERVAL_OPTIONS, code_able=False),
            ParamSpec("source", "select", "source", "yahoo", options=SOURCE_OPTIONS),
        ),
        inputs=NO_INPUTS,
    ),

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="rsi",
        cat="indicator",
        desc="Relative Strength Index. Default period=14, type=wilder (sma also available).",
        reads=("@close",),
        writes=("@rsi",),
        defaults={
            "ins": 1,
            "outs": 1,
            "subtitle": "RSI(14)",
        },
        params=(
            _period(14),
            ParamSpec("type", "select", "type", RSI_DEFAULT_TYPE, options=RSI_TYPE_OPTIONS),
        ),
        inputs=_SOURCE_INPUT,
    ),
    NodeCatalogEntry(
        name="macd",
        cat="indicator",
        desc="MACD: line, signal, and histogram series. Defaults: fast=12, slow=26, signal=9.",
        reads=("@close",),
        writes=("@macd_line", "@macd_signal", "@macd_histogram"),
        defaults={
            "ins": 1,
            "outs": 3,
            "subtitle": "MACD(12,26,9)",
        },
        params=(_period(12, "fast"), _period(26, "slow"), _period(9, "signal")),
        inputs=_SOURCE_INPUT,
    ),
    NodeCatalogEntry(
        name="sma",
        cat="indicator",
        desc="Simple Moving Average. Default period=20.",
        reads=("@close",),
        writes=("@sma",),
        defaults={
            "ins": 1,
            "outs": 1,
            "subtitle": "SMA(20)",
        },
        params=(_period(20),),
        inputs=_SOURCE_INPUT,
    ),
    NodeCatalogEntry(
        name="ema",
        cat="indicator",
        desc="Exponential Moving Average. Default period=20.",
        reads=("@close",),
        writes=("@ema",),
        defaults={
            "ins": 1,
            "outs": 1,
            "subtitle": "EMA(20)",
        },
        params=(_period(20),),
        inputs=_SOURCE_INPUT,
    ),
    NodeCatalogEntry(
        name="bollinger",
        cat="indicator",
        desc="Bollinger Bands: upper, middle, lower. Default period=20, stddev=2.",
        reads=("@close",),
        writes=("@bb_upper", "@bb_middle", "@bb_lower"),
        defaults={
            "ins": 1,
            "outs": 3,
            "subtitle": "BB(20,2)",
        },
        params=(
            _period(20),
            # How many standard deviations the bands sit from the middle.
            ParamSpec("stddev", "number", "stddev", 2.0, min=0.5, max=5.0),
        ),
        inputs=_SOURCE_INPUT,
    ),
    NodeCatalogEntry(
        name="atr",
        cat="indicator",
        desc="Average True Range. Default period=14.",
        reads=("@high", "@low", "@close"),
        writes=("@atr",),
        defaults={
            "ins": 3,
            "outs": 1,
            "subtitle": "ATR(14)",
        },
        params=(_period(14),),
        inputs=_SOURCE_INPUT,
    ),

    # ------------------------------------------------------------------
    # Comparisons  — write a boolean stream attribute
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="crosses_above",
        cat="comparison",
        desc="True on the bar where the left series crosses above the right series.",
        reads=("@series",),  # placeholder; actual wires carry typed attrs
        writes=("@bool",),
        defaults={
            "ins": 2,
            "outs": 1,
            "subtitle": "crosses above",
        },
        params=(_threshold(),),
        inputs=_COMPARISON_INPUTS,
    ),
    NodeCatalogEntry(
        name="crosses_below",
        cat="comparison",
        desc="True on the bar where the left series crosses below the right series.",
        reads=("@series",),
        writes=("@bool",),
        defaults={
            "ins": 2,
            "outs": 1,
            "subtitle": "crosses below",
        },
        params=(_threshold(),),
        inputs=_COMPARISON_INPUTS,
    ),
    NodeCatalogEntry(
        name="above",
        cat="comparison",
        desc="True when the left series is above the right series (or a scalar threshold).",
        reads=("@series",),
        writes=("@bool",),
        defaults={
            "ins": 2,
            "outs": 1,
            "subtitle": "above",
        },
        params=(_threshold(),),
        inputs=_COMPARISON_INPUTS,
    ),
    NodeCatalogEntry(
        name="below",
        cat="comparison",
        desc="True when the left series is below the right series (or a scalar threshold).",
        reads=("@series",),
        writes=("@bool",),
        defaults={
            "ins": 2,
            "outs": 1,
            "subtitle": "below",
        },
        params=(_threshold(),),
        inputs=_COMPARISON_INPUTS,
    ),

    # ------------------------------------------------------------------
    # Logic — combine boolean streams
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="and",
        cat="logic",
        desc="True when ALL incoming boolean signals are true.",
        reads=("@bool",),
        writes=("@bool",),
        defaults={
            "ins": 2,
            "outs": 1,
            "subtitle": "AND",
        },
        inputs=_LOGIC_INPUTS,
    ),
    NodeCatalogEntry(
        name="or",
        cat="logic",
        desc="True when ANY incoming boolean signal is true.",
        reads=("@bool",),
        writes=("@bool",),
        defaults={
            "ins": 2,
            "outs": 1,
            "subtitle": "OR",
        },
        inputs=_LOGIC_INPUTS,
    ),
    # NOT — single-input boolean inverter. In the plan NOT was listed as T3 scope,
    # but Unit 3 (auto_render) requires a NOT node to render rule.negated correctly.
    # Adding it here resolves the contradiction; impl in Unit 7b is a trivial ~not~.
    NodeCatalogEntry(
        name="not",
        cat="logic",
        desc="Inverts the incoming boolean signal.",
        reads=("@bool",),
        writes=("@bool",),
        defaults={
            "ins": 1,
            "outs": 1,
            "subtitle": "NOT",
        },
        inputs=_ONE_SIGNAL,
    ),

    # ------------------------------------------------------------------
    # Settings — produce SimulatorSetting at compile time, not per-bar.
    # reads=() because these are constants drawn from the node's params dict.
    # writes=("@setting",) as a semantic marker; compile dispatches on
    # defaults["setting_key"] to determine which simulator field to fill.
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="position_size",
        cat="settings",
        desc="Fraction of allocated capital used per trade, from 0 to 1 (1 = 100%, 0.5 = 50%). Default: 1.",
        reads=(),
        writes=("@setting",),
        defaults={
            "ins": 0,
            "outs": 1,
            # Shows the stored value too, so 1 is not read as 1%.
            "subtitle": "Size: 1 (100%)",
            "setting_key": "position_size",
        },
        # position_size_impl accepts (0, 1]; 0 itself is refused there.
        params=(ParamSpec("size", "number", "size", 1.0, min=0.0, max=1.0, unit="frac"),),
    ),
    NodeCatalogEntry(
        name="stop_loss",
        cat="settings",
        desc="Fixed stop-loss as a percentage below/above entry. Default: 5.0%.",
        reads=(),
        writes=("@setting",),
        defaults={
            "ins": 0,
            "outs": 1,
            "subtitle": "Stop: 5%",
            "setting_key": "stop_loss",
        },
        # Empty turns the stop off (stop_loss_impl); a value must be above 0.
        params=(ParamSpec("pct", "number", "pct", 5.0, min=0.0, unit="%", optional=True),),
    ),
    NodeCatalogEntry(
        name="slippage",
        cat="settings",
        desc="Modeled slippage cost per leg in basis points. Default: 2.0 bps.",
        reads=(),
        writes=("@setting",),
        defaults={
            "ins": 0,
            "outs": 1,
            "subtitle": "Slippage: 2 bps",
            "setting_key": "slippage_bps",
        },
        params=(ParamSpec("bps", "number", "bps", 2.0, min=0.0, unit="bps"),),
    ),
    NodeCatalogEntry(
        name="commission",
        cat="settings",
        desc="Per-share commission rate and minimum per order. Defaults match Alpaca (free).",
        reads=(),
        writes=("@setting",),
        defaults={
            "ins": 0,
            "outs": 1,
            "subtitle": "Commission: free",
            "setting_key": "commission",
        },
        params=(
            ParamSpec("per_share_rate", "number", "per share", 0.0, min=0.0, unit="$/share"),
            ParamSpec("min_per_order", "number", "min per order", 0.0, min=0.0, unit="$"),
        ),
    ),
    # Trailing stop: the same five fields as models.TrailingStopConfig, so a
    # rule strategy's trailing stop renders as this node and runs the same.
    NodeCatalogEntry(
        name="trailing_stop",
        cat="settings",
        desc=(
            "Trailing stop. type=pct trails value % from the peak; type=atr trails "
            "value x ATR(14). Optionally waits until the trade is activate_pct % in profit."
        ),
        reads=(),
        writes=("@setting",),
        defaults={
            "ins": 0,
            "outs": 1,
            "subtitle": "Trail: 5%",
            "setting_key": "trailing_stop",
        },
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
        ),
    ),

    # ------------------------------------------------------------------
    # Output terminals — compile-active (entry, exit)
    # reads=("@bool",): the incoming wire carries the buy/sell signal.
    # writes=(): terminals consume, never produce.
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="entry",
        cat="output",
        desc="Entry terminal. Wire the buy-signal boolean here to trigger long entries.",
        reads=("@bool",),
        writes=(),
        defaults={
            "ins": 1,
            "outs": 0,
            "subtitle": "Entry",
        },
        inputs=_ONE_SIGNAL,
    ),
    NodeCatalogEntry(
        name="exit",
        cat="output",
        desc="Exit terminal. Wire the sell-signal boolean here to trigger exits.",
        reads=("@bool",),
        writes=(),
        defaults={
            "ins": 1,
            "outs": 0,
            "subtitle": "Exit",
        },
        inputs=_OPTIONAL_SIGNAL,
    ),

    # ------------------------------------------------------------------
    # Output terminals — catalog-only at T2 (size, stop)
    # compile_active=False: compile ignores them while unwired and refuses
    # them (UnsupportedNodeError) once wired.  Wired to the simulator at T4.
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="size",
        cat="output",
        desc="(T4) Size terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
        reads=("@bool",),
        writes=(),
        compile_active=False,
        defaults={
            "ins": 1,
            "outs": 0,
            "subtitle": "Size (T4)",
        },
        inputs=_OPTIONAL_SIGNAL,
    ),
    NodeCatalogEntry(
        name="stop",
        cat="output",
        desc="(T4) Stop terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
        reads=("@bool",),
        writes=(),
        compile_active=False,
        defaults={
            "ins": 1,
            "outs": 0,
            "subtitle": "Stop (T4)",
        },
        inputs=_OPTIONAL_SIGNAL,
    ),
]

# ---------------------------------------------------------------------------
# Category display metadata
# ---------------------------------------------------------------------------

NODE_CATEGORIES: dict[str, str] = {
    "ticker":     "Market Data",
    "data":       "Data",
    "indicator":  "Indicators",
    "signal":     "Signals",
    "comparison": "Comparisons",
    "logic":      "Logic",
    "rules":      "Rules",
    "settings":   "Settings",
    "code":       "Code",
    "output":     "Output Terminals",
}

# Build a name→entry lookup at import time.
_CATALOG_INDEX: dict[str, NodeCatalogEntry] = {e.name: e for e in NODE_CATALOG}


def get_node(name: str) -> NodeCatalogEntry:
    """Return the catalog entry for *name*, or raise KeyError if missing."""
    try:
        return _CATALOG_INDEX[name]
    except KeyError:
        raise KeyError(f"No node named {name!r} in NODE_CATALOG.") from None


def catalog_by_category() -> dict[str, list[NodeCatalogEntry]]:
    """Return NODE_CATALOG entries grouped by category, preserving insertion order."""
    result: dict[str, list[NodeCatalogEntry]] = {}
    for entry in NODE_CATALOG:
        result.setdefault(entry.cat, []).append(entry)
    return result


def catalog_json() -> list[dict[str, Any]]:
    """The whole catalog as plain JSON, in catalog order (section 4.3 shape)."""
    return [e.to_json() for e in NODE_CATALOG]


# ===========================================================================
# Unit 7b — Result dataclasses + impl functions + NODE_IMPLS registry
# ===========================================================================
#
# These types are defined locally to avoid a potential import cycle with
# evaluator.py (Unit 7a).  Unit 7a's compile step adapts them to its own
# IndicatorSpec / PerBarOp / SimulatorSetting internally.
# ===========================================================================


@dataclass
class IndicatorImplResult:
    """Returned by indicator impl functions.

    catalog_name : registry key matching indicators.py / signal_engine.py usage.
    params       : validated params dict ready for compute_instance().
    write_attr   : primary attribute written to the bar-data store (e.g. "@rsi").
    """
    catalog_name: str
    params: dict[str, Any]
    write_attr: str


@dataclass
class PerBarImplResult:
    """Returned by comparison / logic impl functions.

    reads  : attribute names consumed by fn.
    writes : attribute name produced by fn (typically "@bool").
    fn     : callable(attrs: dict[str, pd.Series], i: int) -> bool
    """
    reads: tuple[str, ...]
    writes: str
    fn: Callable[[dict, int], bool]


@dataclass
class SimulatorSettingImplResult:
    """Returned by settings impl functions.

    key   : simulator field name (e.g. "position_size", "stop_loss_pct").
    value : scalar or composite value (float | None | dict).
    """
    key: str
    value: Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_nan(v: Any) -> bool:
    """True when v is a float NaN; safe for non-float types."""
    return isinstance(v, float) and math.isnan(v)


def _safe_float(v: Any) -> Optional[float]:
    """Return float(v) or None when v is None / NaN."""
    if v is None or _is_nan(v):
        return None
    return float(v)


# ---------------------------------------------------------------------------
# Indicator impls
# ---------------------------------------------------------------------------

def rsi_impl(params: dict) -> IndicatorImplResult:
    """RSI node impl.  period ∈ [2, 500], type ∈ {"sma", "wilder"}."""
    period = int(params.get("period", 14))
    if period < 2:
        raise ValueError(f"RSI period must be >= 2, got {period}")
    if period > 500:
        raise ValueError(f"RSI period must be <= 500, got {period}")
    ma_type = str(params.get("type", "sma")).lower()
    return IndicatorImplResult(
        catalog_name="rsi",
        params={"period": period, "type": ma_type},
        write_attr="@rsi",
    )


def macd_impl(params: dict) -> IndicatorImplResult:
    """MACD node impl.  fast/slow/signal each ∈ [2, 500]."""
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    for name, val in (("fast", fast), ("slow", slow), ("signal", signal)):
        if val < 2:
            raise ValueError(f"MACD {name} must be >= 2, got {val}")
        if val > 500:
            raise ValueError(f"MACD {name} must be <= 500, got {val}")
    return IndicatorImplResult(
        catalog_name="macd",
        params={"fast": fast, "slow": slow, "signal": signal},
        write_attr="@macd_line",
    )


def sma_impl(params: dict) -> IndicatorImplResult:
    """SMA node impl.  period ∈ [2, 500]."""
    period = int(params.get("period", 20))
    if period < 2:
        raise ValueError(f"SMA period must be >= 2, got {period}")
    if period > 500:
        raise ValueError(f"SMA period must be <= 500, got {period}")
    return IndicatorImplResult(
        catalog_name="sma",
        params={"period": period, "type": "sma"},
        write_attr="@sma",
    )


def ema_impl(params: dict) -> IndicatorImplResult:
    """EMA node impl.  period ∈ [2, 500]."""
    period = int(params.get("period", 20))
    if period < 2:
        raise ValueError(f"EMA period must be >= 2, got {period}")
    if period > 500:
        raise ValueError(f"EMA period must be <= 500, got {period}")
    return IndicatorImplResult(
        catalog_name="ema",
        params={"period": period, "type": "ema"},
        write_attr="@ema",
    )


def bollinger_impl(params: dict) -> IndicatorImplResult:
    """Bollinger Bands impl.  period ∈ [2, 500], stddev ∈ [0.5, 5]."""
    period = int(params.get("period", 20))
    stddev = float(params.get("stddev", 2.0))
    if period < 2:
        raise ValueError(f"Bollinger period must be >= 2, got {period}")
    if period > 500:
        raise ValueError(f"Bollinger period must be <= 500, got {period}")
    if stddev < 0.5:
        raise ValueError(f"Bollinger stddev must be >= 0.5, got {stddev}")
    if stddev > 5.0:
        raise ValueError(f"Bollinger stddev must be <= 5, got {stddev}")
    return IndicatorImplResult(
        catalog_name="bollinger",
        params={"period": period, "stddev": stddev},
        write_attr="@bb_upper",
    )


def atr_impl(params: dict) -> IndicatorImplResult:
    """ATR node impl.  period ∈ [2, 500]."""
    period = int(params.get("period", 14))
    if period < 2:
        raise ValueError(f"ATR period must be >= 2, got {period}")
    if period > 500:
        raise ValueError(f"ATR period must be <= 500, got {period}")
    return IndicatorImplResult(
        catalog_name="atr",
        params={"period": period},
        write_attr="@atr",
    )


# ---------------------------------------------------------------------------
# Comparison impls  — semantics match signal_engine.eval_rule()
# ---------------------------------------------------------------------------

def above_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """True when left > right (or left > scalar threshold).

    Two forms:
      - 1 incoming attr + threshold param : attrs[a].iloc[i] > threshold
      - 2 incoming attrs                  : attrs[a].iloc[i] > attrs[b].iloc[i]
    """
    threshold = params.get("threshold")
    if threshold is None and len(incoming_attrs) < 2:
        raise ValueError("above_impl needs either a threshold param or two incoming attrs")

    if threshold is not None and len(incoming_attrs) >= 1:
        a = incoming_attrs[0]
        thr = float(threshold)

        def fn(attrs: dict, i: int) -> bool:
            v = _safe_float(attrs[a].iloc[i])
            return False if v is None else bool(v > thr)
    else:
        a, b = incoming_attrs[0], incoming_attrs[1]

        def fn(attrs: dict, i: int) -> bool:
            va = _safe_float(attrs[a].iloc[i])
            vb = _safe_float(attrs[b].iloc[i])
            if va is None or vb is None:
                return False
            return bool(va > vb)

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


def below_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """True when left < right (or left < scalar threshold).

    Two forms:
      - 1 incoming attr + threshold param : attrs[a].iloc[i] < threshold
      - 2 incoming attrs                  : attrs[a].iloc[i] < attrs[b].iloc[i]
    """
    threshold = params.get("threshold")
    if threshold is None and len(incoming_attrs) < 2:
        raise ValueError("below_impl needs either a threshold param or two incoming attrs")

    if threshold is not None and len(incoming_attrs) >= 1:
        a = incoming_attrs[0]
        thr = float(threshold)

        def fn(attrs: dict, i: int) -> bool:
            v = _safe_float(attrs[a].iloc[i])
            return False if v is None else bool(v < thr)
    else:
        a, b = incoming_attrs[0], incoming_attrs[1]

        def fn(attrs: dict, i: int) -> bool:
            va = _safe_float(attrs[a].iloc[i])
            vb = _safe_float(attrs[b].iloc[i])
            if va is None or vb is None:
                return False
            return bool(va < vb)

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


def crosses_above_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """True on the exact bar where series crosses above reference (or threshold).

    Matches signal_engine crossover_up semantics:
      threshold form : v_prev < threshold <= v_now
      two-series form: v_prev < ref_prev  AND v_now >= ref_now
    Guard: i == 0 always returns False.
    """
    threshold = params.get("threshold")
    if threshold is None and len(incoming_attrs) < 2:
        raise ValueError("crosses_above_impl needs either a threshold param or two incoming attrs")

    if threshold is not None and len(incoming_attrs) >= 1:
        a = incoming_attrs[0]
        thr = float(threshold)

        def fn(attrs: dict, i: int) -> bool:
            if i == 0:
                return False
            v_now = _safe_float(attrs[a].iloc[i])
            v_prev = _safe_float(attrs[a].iloc[i - 1])
            if v_now is None or v_prev is None:
                return False
            return bool(v_prev < thr <= v_now)
    else:
        a, b = incoming_attrs[0], incoming_attrs[1]

        def fn(attrs: dict, i: int) -> bool:
            if i == 0:
                return False
            va_now = _safe_float(attrs[a].iloc[i])
            va_prev = _safe_float(attrs[a].iloc[i - 1])
            vb_now = _safe_float(attrs[b].iloc[i])
            vb_prev = _safe_float(attrs[b].iloc[i - 1])
            if any(v is None for v in (va_now, va_prev, vb_now, vb_prev)):
                return False
            return bool(va_prev < vb_prev and va_now >= vb_now)

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


def crosses_below_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """True on the exact bar where series crosses below reference (or threshold).

    Matches signal_engine crossover_down semantics:
      threshold form : v_prev > threshold >= v_now
      two-series form: v_prev > ref_prev  AND v_now <= ref_now
    Guard: i == 0 always returns False.
    """
    threshold = params.get("threshold")
    if threshold is None and len(incoming_attrs) < 2:
        raise ValueError("crosses_below_impl needs either a threshold param or two incoming attrs")

    if threshold is not None and len(incoming_attrs) >= 1:
        a = incoming_attrs[0]
        thr = float(threshold)

        def fn(attrs: dict, i: int) -> bool:
            if i == 0:
                return False
            v_now = _safe_float(attrs[a].iloc[i])
            v_prev = _safe_float(attrs[a].iloc[i - 1])
            if v_now is None or v_prev is None:
                return False
            return bool(v_prev > thr >= v_now)
    else:
        a, b = incoming_attrs[0], incoming_attrs[1]

        def fn(attrs: dict, i: int) -> bool:
            if i == 0:
                return False
            va_now = _safe_float(attrs[a].iloc[i])
            va_prev = _safe_float(attrs[a].iloc[i - 1])
            vb_now = _safe_float(attrs[b].iloc[i])
            vb_prev = _safe_float(attrs[b].iloc[i - 1])
            if any(v is None for v in (va_now, va_prev, vb_now, vb_prev)):
                return False
            return bool(va_prev > vb_prev and va_now <= vb_now)

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


# ---------------------------------------------------------------------------
# Logic impls
# ---------------------------------------------------------------------------

def and_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """True when ALL incoming boolean attrs are truthy."""
    def fn(attrs: dict, i: int) -> bool:
        return all(bool(attrs[a].iloc[i]) for a in incoming_attrs)

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


def or_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """True when ANY incoming boolean attr is truthy."""
    def fn(attrs: dict, i: int) -> bool:
        return any(bool(attrs[a].iloc[i]) for a in incoming_attrs)

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


def not_impl(params: dict, incoming_attrs: tuple[str, ...]) -> PerBarImplResult:
    """Inverts the single incoming boolean attr.

    Guard: i == 0 returns False (matches eval_rules guard for negated rules).
    """
    if len(incoming_attrs) < 1:
        raise ValueError("not_impl requires exactly one incoming attr")
    a = incoming_attrs[0]

    def fn(attrs: dict, i: int) -> bool:
        if i == 0:
            return False
        return not bool(attrs[a].iloc[i])

    return PerBarImplResult(reads=incoming_attrs, writes="@bool", fn=fn)


# ---------------------------------------------------------------------------
# Settings impls
# ---------------------------------------------------------------------------

def position_size_impl(params: dict) -> SimulatorSettingImplResult:
    """Fraction of capital per trade (0.0–1.0). Default: 1.0 (100%)."""
    size = float(params.get("size", 1.0))
    if not (0.0 < size <= 1.0):
        raise ValueError(f"position_size must be in (0, 1], got {size}")
    return SimulatorSettingImplResult(key="position_size", value=size)


def stop_loss_impl(params: dict) -> SimulatorSettingImplResult:
    """Fixed stop-loss percentage below/above entry. None disables the stop."""
    pct = params.get("pct")
    value = float(pct) if pct is not None else None
    if value is not None and value <= 0:
        raise ValueError(f"stop_loss pct must be > 0, got {value}")
    return SimulatorSettingImplResult(key="stop_loss_pct", value=value)


def slippage_impl(params: dict) -> SimulatorSettingImplResult:
    """Modeled slippage cost per leg in basis points. Default: 2.0 bps."""
    bps = float(params.get("bps", 2.0))
    if bps < 0:
        raise ValueError(f"slippage bps must be >= 0, got {bps}")
    return SimulatorSettingImplResult(key="slippage_bps", value=bps)


def commission_impl(params: dict) -> SimulatorSettingImplResult:
    """Per-share commission rate + minimum per order. Defaults match Alpaca (free)."""
    per_share_rate = float(params.get("per_share_rate", 0.0))
    min_per_order = float(params.get("min_per_order", 0.0))
    if per_share_rate < 0:
        raise ValueError(f"per_share_rate must be >= 0, got {per_share_rate}")
    if min_per_order < 0:
        raise ValueError(f"min_per_order must be >= 0, got {min_per_order}")
    return SimulatorSettingImplResult(
        key="commission",
        value={"per_share_rate": per_share_rate, "min_per_order": min_per_order},
    )


def trailing_stop_impl(params: dict) -> SimulatorSettingImplResult:
    """Trailing stop as a TrailingStopConfig, the object the simulator reads.

    Missing params take TrailingStopConfig's defaults.  Raises ValueError for
    a type or source the simulator does not know, and for a bad field value.
    """
    from models import TrailingStopConfig  # local: keep this module import-light

    config = TrailingStopConfig(**params)  # pydantic errors are ValueErrors
    if config.type not in TRAILING_STOP_TYPE_OPTIONS:
        raise ValueError(
            f"trailing stop type must be one of {TRAILING_STOP_TYPE_OPTIONS}, got {config.type!r}"
        )
    if config.source not in TRAILING_STOP_SOURCE_OPTIONS:
        raise ValueError(
            f"trailing stop source must be one of {TRAILING_STOP_SOURCE_OPTIONS}, got {config.source!r}"
        )
    return SimulatorSettingImplResult(key="trailing_stop", value=config)


# ---------------------------------------------------------------------------
# NODE_IMPLS registry — maps catalog name → impl callable
# ---------------------------------------------------------------------------
# Comparison and logic impls have signature (params, incoming_attrs).
# Indicator and settings impls have signature (params,).
# The compile step (Unit 7a) is responsible for passing the right arguments.

NODE_IMPLS: dict[str, Callable] = {
    # Indicators
    "rsi": rsi_impl,
    "macd": macd_impl,
    "sma": sma_impl,
    "ema": ema_impl,
    "bollinger": bollinger_impl,
    "atr": atr_impl,
    # Comparisons
    "above": above_impl,
    "below": below_impl,
    "crosses_above": crosses_above_impl,
    "crosses_below": crosses_below_impl,
    # Logic
    "and": and_impl,
    "or": or_impl,
    "not": not_impl,
    # Settings
    "position_size": position_size_impl,
    "stop_loss": stop_loss_impl,
    "slippage": slippage_impl,
    "commission": commission_impl,
    "trailing_stop": trailing_stop_impl,
}
