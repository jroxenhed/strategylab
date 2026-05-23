"""Graph -> (indicator_specs, per_bar_program, simulator_settings) compile step.

Unit 7a — pure functions, no I/O, no side effects.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from nodebuilder.models import Graph, topological_sort
from nodebuilder.nodes import NODE_CATALOG, NodeCatalogEntry, get_node
from nodebuilder.evaluator import (
    CompiledProgram,
    FamilyCapExceededError,
    HTFGraphNotSupportedError,
    IndicatorSpec,
    MissingTerminalError,
    PerBarOp,
    RegimeUnsupportedError,
    SimulatorSetting,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CATALOG_INDEX: dict[str, NodeCatalogEntry] = {e.name: e for e in NODE_CATALOG}


def _indicator_spec_key(catalog_name: str, params: dict) -> tuple:
    """Stable dedup key for an indicator (catalog_name, params) pair."""
    return (catalog_name, frozenset(params.items()))


def _make_comparison_fn(condition: str, left_attr: str, right_attr: str | None, threshold: float | None):
    """Return a per-bar callable for a comparison node.

    For series vs series (right_attr is not None), both attrs are read from
    the attrs dict.  For series vs scalar (threshold is not None), the right
    side is the scalar value embedded in the closure.
    """
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        s = attrs.get(left_attr)
        if s is None:
            return False
        v_now = s.iloc[i]
        v_prev = s.iloc[i - 1]

        if right_attr is not None:
            r = attrs.get(right_attr)
            if r is None:
                return False
            r_now = r.iloc[i]
            r_prev = r.iloc[i - 1]

            if condition == "above":
                return bool(v_now > r_now)
            elif condition == "below":
                return bool(v_now < r_now)
            elif condition == "crosses_above":
                return bool(v_prev < r_prev and v_now >= r_now)
            elif condition == "crosses_below":
                return bool(v_prev > r_prev and v_now <= r_now)
        elif threshold is not None:
            if condition == "above":
                return bool(v_now > threshold)
            elif condition == "below":
                return bool(v_now < threshold)
            elif condition == "crosses_above":
                return bool(v_prev < threshold <= v_now)
            elif condition == "crosses_below":
                return bool(v_prev > threshold >= v_now)

        return False

    return _fn


def _make_and_fn(input_attrs: list[str]):
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        return all(bool(attrs[a].iloc[i]) for a in input_attrs if a in attrs)
    return _fn


def _make_or_fn(input_attrs: list[str]):
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        return any(bool(attrs[a].iloc[i]) for a in input_attrs if a in attrs)
    return _fn


def _make_not_fn(input_attr: str):
    def _fn(attrs: dict, i: int) -> bool:
        if i < 1:
            return False
        s = attrs.get(input_attr)
        if s is None:
            return False
        return not bool(s.iloc[i])
    return _fn


# ---------------------------------------------------------------------------
# Wire resolution helpers
# ---------------------------------------------------------------------------

def _inbound_attrs(graph: Graph, node_path: str, attr_written_by: dict[str, str]) -> list[str]:
    """Return the list of @-attr names written by the nodes that wire INTO node_path."""
    result = []
    for wire in graph.wires:
        if wire.to_path == node_path:
            src = wire.from_path
            src_node = graph.nodes.get(src)
            if src_node is None:
                continue
            written = attr_written_by.get(src)
            if written:
                result.append(written)
    return result


def _primary_inbound_attr(graph: Graph, node_path: str, attr_written_by: dict[str, str]) -> str | None:
    """Return the single @-attr written by the first upstream node (for single-input nodes)."""
    attrs = _inbound_attrs(graph, node_path, attr_written_by)
    return attrs[0] if attrs else None


# ---------------------------------------------------------------------------
# Public compile() entry point
# ---------------------------------------------------------------------------

def compile(graph: Graph) -> CompiledProgram:  # noqa: A001 (shadows builtin "compile" intentionally)
    """Compile a Graph into a CompiledProgram.

    Steps:
    1. Detect /regime/ nodes → RegimeUnsupportedError
    2. Topological sort (already validated at Graph construction)
    3. Walk nodes in topo order, emitting IndicatorSpecs, PerBarOps, SimulatorSettings
    4. Require Entry terminal → MissingTerminalError if absent
    5. Verify Entry's input attr is @bool → TypeError if not

    Bypassed nodes: node.bypass=True causes the PerBarOp to be skipped.  The
    node's writes attribute will be absent from attrs after dispatch, so any
    downstream reads will get NaN/False from pre-allocated Series.  This is
    a documented trade-off: bypass is a "soft disable" with no explicit
    pass-through value.
    """
    # 1. Regime check
    for node_path in graph.nodes:
        if node_path.startswith("/regime/"):
            raise RegimeUnsupportedError(
                f"Graph contains a /regime/ node ({node_path!r}). "
                "Regime is not supported in the graph evaluator at T2."
            )

    # 2. Topo sort (Graph.__init__ already ran _assert_acyclic, so no cycles)
    ordered_nodes = topological_sort(graph)

    indicator_specs: list[IndicatorSpec] = []
    per_bar_program: list[PerBarOp] = []
    simulator_settings: list[SimulatorSetting] = []

    # Track which unique indicator (catalog_name, params) specs we've emitted.
    # Maps spec_key → write_attr so we can reuse the same attr for dedup'ed specs.
    indicator_key_to_attr: dict[tuple, str] = {}

    # Maps node_path → the @-attr name that node writes (for wire resolution)
    attr_written_by: dict[str, str] = {}

    entry_attr: str | None = None
    exit_attr: str | None = None

    # Assign unique @-attr names for derived (bool) nodes
    _op_counter: dict[str, int] = {}

    def _next_attr(base: str) -> str:
        _op_counter[base] = _op_counter.get(base, 0) + 1
        return f"@{base}_{_op_counter[base]}"

    for node in ordered_nodes:
        node_type = node.type
        node_path = node.id

        # Ticker: source node — provides raw OHLCV attrs; no spec or op needed.
        # The OHLCV attrs (@open, @high, @low, @close, @volume) are provided
        # externally in the attrs dict before evaluate_graph is called.
        if node_type == "ticker":
            # Record what attrs this node writes so downstream wires resolve
            # correctly.  We mark the primary "close" output as the wire attr.
            # In practice the caller seeds these, but we record @close so that
            # a wire from ticker → rsi resolves to "@close".
            attr_written_by[node_path] = "@close"
            continue

        # --- Indicator nodes ---
        if node_type in ("rsi", "macd", "sma", "ema", "bollinger", "atr"):
            catalog_entry = _CATALOG_INDEX.get(node_type)
            if catalog_entry is None:
                continue

            params = dict(node.params) if node.params else {}
            # Fill in defaults for any missing params
            default_params = catalog_entry.defaults.get("params", {})
            for k, v in default_params.items():
                params.setdefault(k, v)

            spec_key = _indicator_spec_key(node_type, params)
            if spec_key not in indicator_key_to_attr:
                # Determine write_attr (the primary output attr name)
                primary_write = catalog_entry.writes[0] if catalog_entry.writes else f"@{node_type}"
                indicator_key_to_attr[spec_key] = primary_write
                indicator_specs.append(
                    IndicatorSpec(
                        catalog_name=node_type,
                        params=params,
                        write_attr=primary_write,
                        node_path=node_path,
                    )
                )

            # This node "writes" the primary output attr (or macd-specific one)
            attr_written_by[node_path] = indicator_key_to_attr[spec_key]

            # Bypassed indicator: downstream sees no value (attr absent from attrs)
            # Compile still records the spec so compute_indicators_from_specs will
            # compute it — but we skip registering the node's write_attr.
            if node.bypass:
                attr_written_by.pop(node_path, None)

            continue

        # --- Comparison nodes ---
        if node_type in ("above", "below", "crosses_above", "crosses_below"):
            if node.bypass:
                continue

            # Collect the two inbound attrs; wires come in order they were added.
            # Priority: if the wire carries an explicit attr (e.g. "@macd_signal"),
            # use it directly — the evaluator stores multi-output sub-attrs under
            # those canonical names.  Fallback to attr_written_by for wires without
            # an explicit attr.
            inbound: list[str] = []
            for wire in graph.wires:
                if wire.to_path == node_path:
                    if wire.attr and wire.attr.startswith("@"):
                        inbound.append(wire.attr)
                    else:
                        src = wire.from_path
                        written = attr_written_by.get(src)
                        if written:
                            inbound.append(written)

            params = dict(node.params) if node.params else {}
            threshold = params.get("threshold")

            if len(inbound) >= 2:
                left_attr, right_attr = inbound[0], inbound[1]
                fn = _make_comparison_fn(node_type, left_attr, right_attr, None)
                reads = (left_attr, right_attr)
            elif len(inbound) == 1 and threshold is not None:
                left_attr = inbound[0]
                fn = _make_comparison_fn(node_type, left_attr, None, float(threshold))
                reads = (left_attr,)
            else:
                # Not enough wires — skip this op
                continue

            write_attr = _next_attr("bool")
            attr_written_by[node_path] = write_attr
            per_bar_program.append(
                PerBarOp(
                    node_path=node_path,
                    reads=tuple(reads),
                    writes=write_attr,
                    fn=fn,
                )
            )
            continue

        # --- Logic nodes ---
        if node_type in ("and", "or", "not"):
            if node.bypass:
                continue

            inbound = _inbound_attrs(graph, node_path, attr_written_by)

            if not inbound:
                continue

            if node_type == "not":
                fn = _make_not_fn(inbound[0])
                reads = (inbound[0],)
            elif node_type == "and":
                fn = _make_and_fn(inbound)
                reads = tuple(inbound)
            else:  # or
                fn = _make_or_fn(inbound)
                reads = tuple(inbound)

            write_attr = _next_attr("bool")
            attr_written_by[node_path] = write_attr
            per_bar_program.append(
                PerBarOp(
                    node_path=node_path,
                    reads=reads,
                    writes=write_attr,
                    fn=fn,
                )
            )
            continue

        # --- Settings nodes ---
        if node_type in ("position_size", "stop_loss", "slippage", "commission"):
            catalog_entry = _CATALOG_INDEX.get(node_type)
            if catalog_entry is None:
                continue
            setting_key = catalog_entry.defaults.get("setting_key", node_type)
            params = dict(node.params) if node.params else {}

            if node_type == "position_size":
                simulator_settings.append(SimulatorSetting(key="position_size", value=params.get("size", 1.0)))
            elif node_type == "stop_loss":
                simulator_settings.append(SimulatorSetting(key="stop_loss", value=params.get("pct", 5.0)))
            elif node_type == "slippage":
                simulator_settings.append(SimulatorSetting(key="slippage_bps", value=params.get("bps", 2.0)))
            elif node_type == "commission":
                simulator_settings.append(
                    SimulatorSetting(key="per_share_rate", value=params.get("per_share_rate", 0.0))
                )
                simulator_settings.append(
                    SimulatorSetting(key="min_per_order", value=params.get("min_per_order", 0.0))
                )
            continue

        # --- Output terminals ---
        if node_type == "entry":
            # Find the incoming wire attr
            src_attr = _primary_inbound_attr(graph, node_path, attr_written_by)
            if src_attr is not None:
                entry_attr = src_attr
            continue

        if node_type == "exit":
            src_attr = _primary_inbound_attr(graph, node_path, attr_written_by)
            if src_attr is not None:
                exit_attr = src_attr
            continue

        # size / stop terminal: compile_active=False at T2 — silently skip.
        if node_type in ("size", "stop"):
            continue

        # Unknown node type: skip gracefully (forward compat)
        continue

    # 3. Require Entry terminal
    if entry_attr is None:
        raise MissingTerminalError("Graph has no Entry terminal (no 'entry' node found).")

    # 4. Verify Entry's input attr is boolean — it should come from a node whose
    # catalog entry writes ("@bool",).  We walk back via attr_written_by to find
    # the source node type and check its catalog writes.
    _wrote_entry_attr = {v: k for k, v in attr_written_by.items()}.get(entry_attr)
    if _wrote_entry_attr is not None:
        src_node = graph.nodes.get(_wrote_entry_attr)
        if src_node is not None:
            src_catalog = _CATALOG_INDEX.get(src_node.type)
            if src_catalog is not None and src_catalog.writes and "@bool" not in src_catalog.writes:
                raise TypeError(
                    f"Entry terminal expects a boolean input, but the wired node "
                    f"{_wrote_entry_attr!r} (type={src_node.type!r}) writes "
                    f"{src_catalog.writes!r}, not '@bool'."
                )

    # 5. Default exit attr
    if exit_attr is None:
        exit_attr = "@always_false"

    return CompiledProgram(
        indicator_specs=indicator_specs,
        per_bar_program=per_bar_program,
        simulator_settings=simulator_settings,
        entry_attr=entry_attr,
        exit_attr=exit_attr,
    )
