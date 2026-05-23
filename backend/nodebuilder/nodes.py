"""Core 14 node catalog metadata.

Stub — populated by Unit 2 (catalog) and Unit 7b (impls).
Unit 2: NODE_CATALOG + helpers (metadata only, no runtime impls).
Unit 7b: adds impl functions (rsi_impl, macd_impl, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
        reads=("@close", "@close"),  # placeholder; actual wires carry typed attrs
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
        reads=("@close", "@close"),
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
        reads=("@close", "@close"),
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
        reads=("@close", "@close"),
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
