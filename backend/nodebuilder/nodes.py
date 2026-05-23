"""Core 14 node catalog metadata + Unit 7b impl functions.

Unit 2: NODE_CATALOG + helpers (metadata only, no runtime impls).
Unit 7b: adds impl functions (rsi_impl, macd_impl, etc.) + result dataclasses
         + NODE_IMPLS registry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


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
                      "params"   – indicator/comparison param defaults (may be empty).
                      "ins"      – expected number of inbound wires.
                      "outs"     – expected number of outbound wires.
                      "subtitle" – optional subtitle rendered in the node body.
                    Settings nodes include "setting_key" so Unit 7a knows which
                    simulator field to populate.
    compile_active: False for catalog-only nodes that are renderable on the canvas
                    but whose compile step produces no SimulatorSetting or signal.
                    Currently only "size" and "stop" output terminals at T2.
    """
    name: str
    cat: str
    desc: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    defaults: dict
    compile_active: bool = True


# ---------------------------------------------------------------------------
# Catalog — Core 14 (compile-active) + Settings (4) + Output terminals (4)
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
            "params": {"symbol": "AAPL", "interval": "1d", "source": "yahoo"},
            "ins": 0,
            "outs": 5,
            "subtitle": None,
        },
    ),

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="rsi",
        cat="indicator",
        desc="Relative Strength Index. Default period=14, type=sma.",
        reads=("@close",),
        writes=("@rsi",),
        defaults={
            "params": {"period": 14, "type": "sma"},
            "ins": 1,
            "outs": 1,
            "subtitle": "RSI(14)",
        },
    ),
    NodeCatalogEntry(
        name="macd",
        cat="indicator",
        desc="MACD: line, signal, and histogram series. Defaults: fast=12, slow=26, signal=9.",
        reads=("@close",),
        writes=("@macd_line", "@macd_signal", "@macd_histogram"),
        defaults={
            "params": {"fast": 12, "slow": 26, "signal": 9},
            "ins": 1,
            "outs": 3,
            "subtitle": "MACD(12,26,9)",
        },
    ),
    NodeCatalogEntry(
        name="sma",
        cat="indicator",
        desc="Simple Moving Average. Default period=20.",
        reads=("@close",),
        writes=("@sma",),
        defaults={
            "params": {"period": 20},
            "ins": 1,
            "outs": 1,
            "subtitle": "SMA(20)",
        },
    ),
    NodeCatalogEntry(
        name="ema",
        cat="indicator",
        desc="Exponential Moving Average. Default period=20.",
        reads=("@close",),
        writes=("@ema",),
        defaults={
            "params": {"period": 20},
            "ins": 1,
            "outs": 1,
            "subtitle": "EMA(20)",
        },
    ),
    NodeCatalogEntry(
        name="bollinger",
        cat="indicator",
        desc="Bollinger Bands: upper, middle, lower. Default period=20, stddev=2.",
        reads=("@close",),
        writes=("@bb_upper", "@bb_middle", "@bb_lower"),
        defaults={
            "params": {"period": 20, "stddev": 2.0},
            "ins": 1,
            "outs": 3,
            "subtitle": "BB(20,2)",
        },
    ),
    NodeCatalogEntry(
        name="atr",
        cat="indicator",
        desc="Average True Range. Default period=14.",
        reads=("@high", "@low", "@close"),
        writes=("@atr",),
        defaults={
            "params": {"period": 14},
            "ins": 3,
            "outs": 1,
            "subtitle": "ATR(14)",
        },
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
            "params": {"threshold": None},
            "ins": 2,
            "outs": 1,
            "subtitle": "crosses above",
        },
    ),
    NodeCatalogEntry(
        name="crosses_below",
        cat="comparison",
        desc="True on the bar where the left series crosses below the right series.",
        reads=("@series",),
        writes=("@bool",),
        defaults={
            "params": {"threshold": None},
            "ins": 2,
            "outs": 1,
            "subtitle": "crosses below",
        },
    ),
    NodeCatalogEntry(
        name="above",
        cat="comparison",
        desc="True when the left series is above the right series (or a scalar threshold).",
        reads=("@series",),
        writes=("@bool",),
        defaults={
            "params": {"threshold": None},
            "ins": 2,
            "outs": 1,
            "subtitle": "above",
        },
    ),
    NodeCatalogEntry(
        name="below",
        cat="comparison",
        desc="True when the left series is below the right series (or a scalar threshold).",
        reads=("@series",),
        writes=("@bool",),
        defaults={
            "params": {"threshold": None},
            "ins": 2,
            "outs": 1,
            "subtitle": "below",
        },
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
            "params": {},
            "ins": 2,
            "outs": 1,
            "subtitle": "AND",
        },
    ),
    NodeCatalogEntry(
        name="or",
        cat="logic",
        desc="True when ANY incoming boolean signal is true.",
        reads=("@bool",),
        writes=("@bool",),
        defaults={
            "params": {},
            "ins": 2,
            "outs": 1,
            "subtitle": "OR",
        },
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
            "params": {},
            "ins": 1,
            "outs": 1,
            "subtitle": "NOT",
        },
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
        desc="Fraction of allocated capital deployed per trade (0–1). Default: 1.0 (100%).",
        reads=(),
        writes=("@setting",),
        defaults={
            "params": {"size": 1.0},
            "ins": 0,
            "outs": 1,
            "subtitle": "Size: 100%",
            "setting_key": "position_size",
        },
    ),
    NodeCatalogEntry(
        name="stop_loss",
        cat="settings",
        desc="Fixed stop-loss as a percentage below/above entry. Default: 5.0%.",
        reads=(),
        writes=("@setting",),
        defaults={
            "params": {"pct": 5.0},
            "ins": 0,
            "outs": 1,
            "subtitle": "Stop: 5%",
            "setting_key": "stop_loss",
        },
    ),
    NodeCatalogEntry(
        name="slippage",
        cat="settings",
        desc="Modeled slippage cost per leg in basis points. Default: 2.0 bps.",
        reads=(),
        writes=("@setting",),
        defaults={
            "params": {"bps": 2.0},
            "ins": 0,
            "outs": 1,
            "subtitle": "Slippage: 2 bps",
            "setting_key": "slippage_bps",
        },
    ),
    NodeCatalogEntry(
        name="commission",
        cat="settings",
        desc="Per-share commission rate and minimum per order. Defaults match Alpaca (free).",
        reads=(),
        writes=("@setting",),
        defaults={
            "params": {"per_share_rate": 0.0, "min_per_order": 0.0},
            "ins": 0,
            "outs": 1,
            "subtitle": "Commission: free",
            "setting_key": "commission",
        },
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
            "params": {},
            "ins": 1,
            "outs": 0,
            "subtitle": "Entry",
        },
    ),
    NodeCatalogEntry(
        name="exit",
        cat="output",
        desc="Exit terminal. Wire the sell-signal boolean here to trigger exits.",
        reads=("@bool",),
        writes=(),
        defaults={
            "params": {},
            "ins": 1,
            "outs": 0,
            "subtitle": "Exit",
        },
    ),

    # ------------------------------------------------------------------
    # Output terminals — catalog-only at T2 (size, stop)
    # compile_active=False: compiler ignores these at T2.
    # Wired to the simulator at T4.
    # ------------------------------------------------------------------
    NodeCatalogEntry(
        name="size",
        cat="output",
        desc="(T4) Size terminal. Placeholder — compile ignores at T2. Wire a scalar for dynamic sizing.",
        reads=("@bool",),
        writes=(),
        compile_active=False,
        defaults={
            "params": {},
            "ins": 1,
            "outs": 0,
            "subtitle": "Size (T4)",
        },
    ),
    NodeCatalogEntry(
        name="stop",
        cat="output",
        desc="(T4) Stop terminal. Placeholder — compile ignores at T2. Wire a scalar for dynamic stops.",
        reads=("@bool",),
        writes=(),
        compile_active=False,
        defaults={
            "params": {},
            "ins": 1,
            "outs": 0,
            "subtitle": "Stop (T4)",
        },
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
}
