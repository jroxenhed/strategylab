"""Topological sort, per-bar evaluation, compute_indicators_from_specs.

Unit 7a — pure functions, no I/O, no side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

# Re-export errors from models so callers can import from a single place.
from nodebuilder.models import (  # noqa: F401
    CyclicGraphError,
    DanglingWireError,
    GraphValidationError,
    IncompatibleGraphVersionError,
    ReadOnlyGraphError,
    Graph,
)

# compile() lives in nodebuilder.compile to avoid a circular import
# (compile imports evaluator types), but the public API requires it to be
# importable from nodebuilder.evaluator as well.  Re-export lazily.
def compile(graph: "Graph") -> "CompiledProgram":  # noqa: A001
    """Compile a Graph into a CompiledProgram. Delegates to nodebuilder.compile.compile."""
    from nodebuilder.compile import compile as _compile
    return _compile(graph)

# ---------------------------------------------------------------------------
# New error types specific to compile / dispatch
# ---------------------------------------------------------------------------

_INDICATOR_FAMILY_CAP = 20


class RegimeUnsupportedError(GraphValidationError):
    """Raised when a graph contains a /regime/ node, which is not supported
    by the graph evaluator at T2."""


class MissingTerminalError(GraphValidationError):
    """Raised when compile() finds no Entry terminal in the graph."""


class HTFGraphNotSupportedError(GraphValidationError):
    """Placeholder — raised by Unit 9 when a graph bot uses HTF intervals."""


class FamilyCapExceededError(GraphValidationError):
    """Raised by compute_indicators_from_specs when too many specs of a single
    indicator family are requested (mirrors signal_engine._INDICATOR_FAMILY_CAP)."""


class GraphTypeError(GraphValidationError, TypeError):
    """Raised by compile() when a wire carries the wrong kind of value, for
    example a price wired straight into Entry, or a crossover that reads a
    derived signal.  It is still a TypeError so older callers keep working."""


class UnsupportedNodeError(GraphValidationError):
    """Raised by compile() for a node type it cannot run yet.

    The read-only viewer still shows these nodes.  Compile refuses them so a
    graph never trades differently from what the canvas shows.
    """

    def __init__(self, node_id: str, node_type: str) -> None:
        super().__init__(
            f"Node {node_id!r} has type {node_type!r}, which the graph backtest "
            f"cannot run yet. Remove it or replace it with a supported node.",
            node_id=node_id,
        )
        self.node_type = node_type


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorSpec:
    """Declared indicator computation: (catalog_name, params) → output attr name."""

    catalog_name: str          # "rsi", "macd", "sma", etc.
    params: dict               # canonical {period:14, type:"sma"} etc.
    write_attr: str            # "@rsi" — the stream attribute name for main result
    node_path: str             # source node path (for debugging)
    # "" for the first distinct spec of a node type, "_2", "_3"... for the
    # next ones.  Keeps two RSIs (or two MACDs) with different params from
    # writing the same attribute and overwriting each other.
    attr_suffix: str = ""


@dataclass
class PerBarOp:
    """One step in the per-bar program: read attrs, write attr, callable that runs."""

    node_path: str
    reads: tuple                      # attribute names this op consumes (tuple[str, ...])
    writes: str                       # attribute name written
    fn: Callable[[dict, int], Any]    # signature: (attrs, i) -> value


@dataclass(frozen=True)
class SimulatorSetting:
    """Compile-time scalar setting (Position Size, Stop Loss, Slippage, Commission)."""

    key: str    # "position_size", "stop_loss", "slippage_bps", "per_share_rate", "min_per_order"
    value: Any


@dataclass(frozen=True)
class CompiledProgram:
    indicator_specs: list             # list[IndicatorSpec]
    per_bar_program: list             # list[PerBarOp]
    simulator_settings: list          # list[SimulatorSetting]
    entry_attr: str                   # name of the boolean stream the Entry terminal reads
    exit_attr: str                    # name of the boolean stream the Exit terminal reads


# ---------------------------------------------------------------------------
# compute_indicators_from_specs — dedicated dispatcher for graph mode
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
    """Dedicated dispatcher for graph-mode indicator evaluation.

    Mirrors the per-family logic of signal_engine.compute_indicators but
    operates on IndicatorSpec objects rather than Rule objects.
    Does NOT import signal_engine — compute_instance is the shared leaf.

    Multi-output indicators (macd, bollinger/bb) spread their sub-series
    under canonical sub-attr names:
      MACD  → @macd_line, @macd_signal, @macd_histogram
      BB    → @bb_upper, @bb_middle, @bb_lower
    Single-output indicators use spec.write_attr.

    Raises FamilyCapExceededError when any family exceeds _INDICATOR_FAMILY_CAP.
    """
    from indicators import compute_instance  # leaf-level, canonical

    # Group specs by registry-level family name for cap enforcement.
    family_counts: dict[str, int] = {}
    for spec in indicator_specs:
        family = _CATALOG_TO_REGISTRY.get(spec.catalog_name, spec.catalog_name)
        family_counts[family] = family_counts.get(family, 0) + 1

    for family, count in family_counts.items():
        if count > _INDICATOR_FAMILY_CAP:
            # Point at the first node past the cap in this family.
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

        # Build cache key
        cache_key = (catalog, frozenset(params.items()))
        if cache is not None and cache_key in cache:
            result = cache[cache_key]
        else:
            # Translate catalog name → registry key + params adjustments
            if catalog == "sma":
                registry_key = "ma"
                actual_params = {**params, "type": "sma"}
            elif catalog == "ema":
                registry_key = "ma"
                actual_params = {**params, "type": "ema"}
            elif catalog == "bollinger":
                registry_key = "bb"
                actual_params = params
            else:
                registry_key = catalog
                actual_params = params

            result = compute_instance(registry_key, actual_params, ohlcv)
            if cache is not None:
                cache[cache_key] = result

        # Spread multi-output results into attrs with canonical sub-attr names
        sfx = spec.attr_suffix
        if catalog == "macd":
            attrs[f"@macd_line{sfx}"] = result["macd"]
            attrs[f"@macd_signal{sfx}"] = result["signal"]
            attrs[f"@macd_histogram{sfx}"] = result["histogram"]
        elif catalog in ("bollinger", "bb"):
            attrs[f"@bb_upper{sfx}"] = result["upper"]
            attrs[f"@bb_middle{sfx}"] = result["middle"]
            attrs[f"@bb_lower{sfx}"] = result["lower"]
        else:
            # Single-output: rsi→"rsi", sma/ema→"ma", atr→"atr", ma→"ma"
            if catalog in ("sma", "ema"):
                attrs[spec.write_attr] = result["ma"]
            elif catalog == "rsi":
                attrs[spec.write_attr] = result["rsi"]
            elif catalog == "atr":
                attrs[spec.write_attr] = result["atr"]
            else:
                # Fallback: use the single key in the result dict
                if len(result) == 1:
                    attrs[spec.write_attr] = next(iter(result.values()))
                else:
                    # Unknown multi-output: store all sub-keys
                    for sub_key, series in result.items():
                        attrs[f"@{sub_key}{sfx}"] = series

    return attrs


# ---------------------------------------------------------------------------
# evaluate_graph — per-bar runner
# ---------------------------------------------------------------------------


def evaluate_graph(
    program: CompiledProgram,
    attrs: dict,         # dict[str, pd.Series] — pre-allocated by caller
    i: int,
) -> dict:
    """Run per_bar_program at bar index i; returns {"entry": bool, "exit": bool}.

    attrs values are pre-allocated pd.Series (float/bool, NaN/False filled).
    Each PerBarOp writes its result into attrs[op.writes].iloc[i].
    Bypassed nodes have no PerBarOp in the program, so their writes are never
    populated — downstream ops will read NaN/False at that index.
    """
    for op in program.per_bar_program:
        val = op.fn(attrs, i)
        # Store as float (1.0/0.0) so pre-allocated float64 Series can hold the value.
        attrs[op.writes].iloc[i] = 1.0 if val else 0.0

    entry_val = (
        attrs[program.entry_attr].iloc[i]
        if program.entry_attr in attrs
        else False
    )
    exit_val = (
        attrs[program.exit_attr].iloc[i]
        if program.exit_attr in attrs
        else False
    )
    return {"entry": bool(entry_val), "exit": bool(exit_val)}
