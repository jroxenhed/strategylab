"""Compiled program types, compile errors, and running a compiled graph.

Since W2 (plan D5) a graph cooks as columns: ``cook_program`` runs every
node once over the full index (kernel.evaluate), and ``cook_signals`` gives
the Entry and Exit columns.  ``evaluate_graph(program, attrs, i)`` stays as
a thin adapter for callers written for the per-bar engine (the
``buy_signal_fn`` contract of ``_run_simulation``): its first call on an
attrs dict cooks the whole program, later calls read bar ``i``.

This module is a facade: nodebuilder.compile builds the program, the kernel
runs it.  Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

# Re-export errors from models so callers can import from a single place.
from nodebuilder.models import (  # noqa: F401
    CyclicGraphError,
    DanglingWireError,
    Graph,
    GraphValidationError,
    IncompatibleGraphVersionError,
    ReadOnlyGraphError,
)
# The kernel's generic compile errors, under their Wave 0 import path.
from nodebuilder.kernel.schema import (  # noqa: F401
    GraphTypeError,
    UnknownNodeTypeError,
    UnsupportedNodeError,
)
from nodebuilder.kernel.stream import STREAM_SCHEMA_VERSION


# compile() lives in nodebuilder.compile to avoid a circular import
# (compile imports evaluator types), but the public API requires it to be
# importable from nodebuilder.evaluator as well.  Re-export lazily.
def compile(graph: "Graph") -> "CompiledProgram":  # noqa: A001
    """Compile a Graph into a CompiledProgram. Delegates to nodebuilder.compile.compile."""
    from nodebuilder.compile import compile as _compile
    return _compile(graph)


# ---------------------------------------------------------------------------
# Compile errors of the trading layer
# ---------------------------------------------------------------------------

_INDICATOR_FAMILY_CAP = 20

NO_EXIT_ATTR = "@always_false"
"""The exit attribute of a program whose Exit gets no signal."""


class RegimeUnsupportedError(GraphValidationError):
    """Raised when a graph contains a /regime/ node, which is not supported
    by the graph evaluator yet (W5)."""

    code = "regime_unsupported"


class MissingTerminalError(GraphValidationError):
    """Raised when compile() finds no Entry terminal in the graph.

    Compile sets code "missing_input" on it when the Entry exists but gets
    no signal."""

    code = "missing_terminal"


class HTFGraphNotSupportedError(GraphValidationError):
    """Raised by the bot runner when a graph bot uses HTF intervals."""

    code = "unsupported_node"


class FamilyCapExceededError(GraphValidationError):
    """Raised when too many distinct specs of one indicator family are in a
    graph (mirrors signal_engine._INDICATOR_FAMILY_CAP)."""

    code = "family_cap"


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorSpec:
    """Legacy (Wave 0/1): an indicator computation for compute_indicators_from_specs.

    Compiled programs no longer carry these (CompiledProgram.indicator_specs
    is always empty); indicators are nodes of the column program.
    """

    catalog_name: str          # "rsi", "macd", "sma", etc.
    params: dict               # canonical {period:14, type:"sma"} etc.
    write_attr: str            # "@rsi" — the stream attribute name for main result
    node_path: str             # source node path (for debugging)
    attr_suffix: str = ""


@dataclass(frozen=True)
class SimulatorSetting:
    """Compile-time scalar setting (Position Size, Stop Loss, Slippage, Commission).

    A derived view of the settings nodes' detail attributes, kept for the
    Wave 0 overlay code (sim_settings.py); removed in W5 (plan D5)."""

    key: str    # "position_size", "stop_loss", "slippage_bps", "per_share_rate", "min_per_order", "trailing_stop"
    value: Any


@dataclass(frozen=True)
class CompiledProgram:
    """A graph ready to cook.

    steps        : kernel Steps, one per node, in topological order.
    entry_attr   : the attribute the Entry terminal reads.
    exit_attr    : the attribute the Exit terminal reads, or NO_EXIT_ATTR.
    entry_node / exit_node : the terminals whose input streams carry them.
    simulator_settings : derived from the settings nodes (see SimulatorSetting).
    required_lookback_bars : bars of history the graph needs before its
        signals are good (the bot's fetch window, plan D5).
    stream_schema: the stream format version (plan 3.1).
    schemas      : each node's output StreamSchema, by node id.
    indicator_specs / per_bar_program : legacy, always empty.  Kept so
        callers written for the per-bar engine still run.
    """

    steps: tuple
    entry_attr: str
    exit_attr: str
    simulator_settings: list
    entry_node: Optional[str] = None
    exit_node: Optional[str] = None
    required_lookback_bars: int = 0
    stream_schema: int = STREAM_SCHEMA_VERSION
    schemas: Mapping[str, Any] = field(default_factory=dict)
    indicator_specs: tuple = ()
    per_bar_program: tuple = ()

    def step(self, node_id: str):
        """The Step for *node_id* (KeyError when it has none)."""
        for s in self.steps:
            if s.node_id == node_id:
                return s
        raise KeyError(node_id)

    def reads_attr(self, name: str) -> bool:
        """True when any running node reads *name* (say @volume)."""
        return any(name in s.reads for s in self.steps)

    def stream_schemas_json(self) -> dict[str, dict]:
        """Each node's output stream in the /validate form (plan 3.3)."""
        return {nid: schema.to_json() for nid, schema in self.schemas.items()}


# ---------------------------------------------------------------------------
# Cooking
# ---------------------------------------------------------------------------

_BAR_COLUMNS = (("@open", "Open"), ("@high", "High"), ("@low", "Low"),
                ("@close", "Close"), ("@volume", "Volume"))


def bars_from_frame(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """The bars a Ticker node reads, from an OHLCV DataFrame (no copy).

    A frame without Volume gets zeros, as run.py does."""
    bars: dict[str, np.ndarray] = {}
    for name, col in _BAR_COLUMNS:
        if col in df.columns:
            bars[name] = df[col].to_numpy(copy=False)
        elif col == "Volume":
            bars[name] = np.zeros(len(df), dtype=np.float64)
        else:
            raise KeyError(f"the frame has no {col} column")
    return bars


def bars_from_attrs(attrs: Mapping[str, Any]) -> tuple[pd.Index, dict[str, np.ndarray]]:
    """The bars and index from a Wave 0 attrs dict (@open ... @volume)."""
    close = attrs.get("@close")
    if close is None:
        raise KeyError("attrs has no @close series")
    index = close.index if isinstance(close, pd.Series) else pd.RangeIndex(len(close))
    bars: dict[str, np.ndarray] = {}
    for name, _col in _BAR_COLUMNS:
        series = attrs.get(name)
        if series is None:
            bars[name] = np.full(len(index), np.nan if name != "@volume" else 0.0)
        else:
            bars[name] = np.asarray(series)
    return index, bars


def cook_program(
    program: CompiledProgram,
    df: Optional[pd.DataFrame] = None,
    *,
    index: Optional[pd.Index] = None,
    bars: Optional[Mapping[str, Any]] = None,
    keep: Optional[set] = None,
    keep_all: bool = False,
):
    """Run every node of *program* once over the frame.  Returns a
    kernel.evaluate.CookResult.

    Pass an OHLCV DataFrame, or an index plus bars (as bars_from_frame
    gives).  By default only the terminals' streams are kept (plus any node
    ids in *keep*); keep_all keeps every node's output stream (an inspector).
    CPU work: never call this on the bot's event loop.
    """
    from nodebuilder.kernel.evaluate import cook

    if df is not None:
        index = df.index
        bars = bars_from_frame(df)
    if index is None or bars is None:
        raise ValueError("cook_program needs a DataFrame, or an index and bars")
    if keep_all:
        keep_ids = None
    else:
        keep_ids = {n for n in (program.entry_node, program.exit_node) if n} | set(keep or ())
    return cook(program.steps, index, {"bars": bars}, keep=keep_ids)


def signal_columns(program: CompiledProgram, result) -> tuple[np.ndarray, np.ndarray]:
    """(entry, exit) as numpy bool columns from a cook result."""
    n = result.store.length

    def _column(node: Optional[str], name: str) -> np.ndarray:
        if node is None or name == NO_EXIT_ATTR:
            return np.zeros(n, dtype=bool)
        return np.asarray(result.column(node, name), dtype=bool)

    return _column(program.entry_node, program.entry_attr), _column(program.exit_node, program.exit_attr)


def cook_signals(program: CompiledProgram, df: Optional[pd.DataFrame] = None, *,
                 index: Optional[pd.Index] = None,
                 bars: Optional[Mapping[str, Any]] = None) -> tuple[np.ndarray, np.ndarray]:
    """Cook *program* and return its (entry, exit) bool columns."""
    return signal_columns(program, cook_program(program, df, index=index, bars=bars))


# ---------------------------------------------------------------------------
# evaluate_graph — the per-bar adapter
# ---------------------------------------------------------------------------

_COOK_KEY = "__nodebuilder_cook__"


def evaluate_graph(
    program: CompiledProgram,
    attrs: dict,         # dict[str, pd.Series] — at least @close; @open... @volume
    i: int,
) -> dict:
    """{"entry": bool, "exit": bool} at bar *i*.

    The first call on an attrs dict cooks the whole program from its bars
    (@open @high @low @close @volume) and keeps the Entry and Exit columns
    in attrs under a private key; later calls on the same dict only read
    bar i.  So run.py's per-bar callbacks cost one cook in total.
    """
    cached = attrs.get(_COOK_KEY)
    if cached is None or cached[0] is not program:
        index, bars = bars_from_attrs(attrs)
        entry, exit_ = cook_signals(program, index=index, bars=bars)
        cached = (program, entry, exit_)
        attrs[_COOK_KEY] = cached
    _prog, entry, exit_ = cached
    return {"entry": bool(entry[i]), "exit": bool(exit_[i])}


# ---------------------------------------------------------------------------
# compute_indicators_from_specs — legacy
# ---------------------------------------------------------------------------

# Catalog-name → registry-key translation table (when they differ)
_CATALOG_TO_REGISTRY: dict[str, str] = {
    "sma": "ma",
    "ema": "ma",     # with type=ema injected by dispatch below
    "bollinger": "bb",
    # all others map 1:1 (rsi, macd, atr, ma, bb)
}


def compute_indicators_from_specs(
    indicator_specs: list,  # list[IndicatorSpec]
    ohlcv,                  # OHLCVSeries — pre-built at call site
    cache: dict | None = None,
) -> dict:  # dict[str, pd.Series]
    """Legacy (Wave 0/1) indicator dispatcher for IndicatorSpec lists.

    Compiled programs carry no specs since W2 (indicators are nodes), so
    callers that still pass ``program.indicator_specs`` get an empty dict.

    Multi-output indicators (macd, bollinger/bb) spread their sub-series
    under canonical sub-attr names:
      MACD  → @macd_line, @macd_signal, @macd_histogram
      BB    → @bb_upper, @bb_middle, @bb_lower
    Single-output indicators use spec.write_attr.

    Raises FamilyCapExceededError when any family exceeds _INDICATOR_FAMILY_CAP.
    """
    from indicators import compute_instance  # leaf-level, canonical

    family_counts: dict[str, int] = {}
    for spec in indicator_specs:
        family = _CATALOG_TO_REGISTRY.get(spec.catalog_name, spec.catalog_name)
        family_counts[family] = family_counts.get(family, 0) + 1

    for family, count in family_counts.items():
        if count > _INDICATOR_FAMILY_CAP:
            over = [
                s.node_path for s in indicator_specs
                if _CATALOG_TO_REGISTRY.get(s.catalog_name, s.catalog_name) == family
            ][_INDICATOR_FAMILY_CAP]
            raise FamilyCapExceededError(
                f"Too many distinct {family!r} specs ({count}); "
                f"max {_INDICATOR_FAMILY_CAP} per request",
                node_id=over,
            )

    attrs: dict[str, pd.Series] = {}
    for spec in indicator_specs:
        catalog = spec.catalog_name
        params = spec.params
        cache_key = (catalog, frozenset(params.items()))
        if cache is not None and cache_key in cache:
            result = cache[cache_key]
        else:
            if catalog == "sma":
                registry_key, actual_params = "ma", {**params, "type": "sma"}
            elif catalog == "ema":
                registry_key, actual_params = "ma", {**params, "type": "ema"}
            elif catalog == "bollinger":
                registry_key, actual_params = "bb", params
            else:
                registry_key, actual_params = catalog, params
            result = compute_instance(registry_key, actual_params, ohlcv)
            if cache is not None:
                cache[cache_key] = result

        sfx = spec.attr_suffix
        if catalog == "macd":
            attrs[f"@macd_line{sfx}"] = result["macd"]
            attrs[f"@macd_signal{sfx}"] = result["signal"]
            attrs[f"@macd_histogram{sfx}"] = result["histogram"]
        elif catalog in ("bollinger", "bb"):
            attrs[f"@bb_upper{sfx}"] = result["upper"]
            attrs[f"@bb_middle{sfx}"] = result["middle"]
            attrs[f"@bb_lower{sfx}"] = result["lower"]
        elif catalog in ("sma", "ema"):
            attrs[spec.write_attr] = result["ma"]
        elif catalog in ("rsi", "atr"):
            attrs[spec.write_attr] = result[catalog]
        elif len(result) == 1:
            attrs[spec.write_attr] = next(iter(result.values()))
        else:
            for sub_key, series in result.items():
                attrs[f"@{sub_key}{sfx}"] = series
    return attrs
