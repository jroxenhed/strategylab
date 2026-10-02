"""The node catalog (plan D9), read from the kernel registry.

Since W2 the registry is the single source of node types: each module in
backend/nodebuilder/trading/ registers its types (impl, ParamSpec list,
PortsSpec, catalog metadata) when it is imported.  This module is a facade
over it for the catalog's readers: backend/scripts/export_nodebuilder_catalog.py
(which writes frontend/src/features/nodebuilder/catalog.generated.ts),
compile, the rule-coverage sweep and the tests.

The per-bar impl table and the *_impl functions that used to live here
are gone: every node type's impl and checks live with its registration.
"""
from __future__ import annotations

from typing import Any

from nodebuilder import trading as _trading  # noqa: F401  (registers every node type)
from nodebuilder.kernel import registry as _registry
from nodebuilder.kernel.registry import (  # noqa: F401  (re-exported)
    NO_INPUTS,
    PARAM_DTYPES,
    PARAM_TYPES,
    NodeCatalogEntry,
    NodeType,
    ParamSpec,
    PortSpec,
    PortsSpec,
)
from nodebuilder.trading.nodes_data import INTERVAL_OPTIONS, SOURCE_OPTIONS  # noqa: F401
from nodebuilder.trading.nodes_indicators import RSI_DEFAULT_TYPE, RSI_TYPE_OPTIONS  # noqa: F401
from nodebuilder.trading.nodes_settings import (  # noqa: F401
    TRAILING_STOP_DEFAULTS,
    TRAILING_STOP_SOURCE_OPTIONS,
    TRAILING_STOP_TYPE_OPTIONS,
)

# Units a number param can be read in.  "frac" is a fraction (1 = 100%).
# "$" and "$/share" are commission amounts, and "% or x ATR" is the trailing
# stop value, which is a percent or an ATR multiple depending on its type.
PARAM_UNITS: tuple[str, ...] = ("%", "bps", "bars", "frac", "$", "$/share", "% or x ATR")

# Category display metadata, in palette order.  The catalog is sorted by
# this order, then by registration order inside a category.
NODE_CATEGORIES: dict[str, str] = {
    "ticker":     "Market Data",
    "data":       "Data",
    "indicator":  "Indicators",
    "signal":     "Math & Signal",
    "comparison": "Comparisons",
    "logic":      "Logic",
    "rules":      "Rules",
    "settings":   "Settings",
    "code":       "Code",
    "output":     "Output Terminals",
    "network":    "Networks",
}


def node_types() -> list[NodeType]:
    """Every registered node type, in catalog order."""
    rank = {cat: i for i, cat in enumerate(NODE_CATEGORIES)}
    types = _registry.all_types()
    order = {t.name: i for i, t in enumerate(types)}
    return sorted(types, key=lambda t: (rank.get(t.entry.cat, len(rank)), order[t.name]))


def catalog_entries() -> list[NodeCatalogEntry]:
    """Every registered node type's catalog entry, in catalog order."""
    return [t.entry for t in node_types()]


# The catalog as it stood once every node module was imported.  Read the
# registry (catalog_entries, get_node) for anything registered later.
NODE_CATALOG: list[NodeCatalogEntry] = catalog_entries()


def get_node(name: str) -> NodeCatalogEntry:
    """Return the catalog entry for *name*, or raise KeyError if missing."""
    node_type = _registry.get(name)
    if node_type is None:
        raise KeyError(f"No node named {name!r} in the node catalog.")
    return node_type.entry


def catalog_by_category() -> dict[str, list[NodeCatalogEntry]]:
    """Catalog entries grouped by category, keeping catalog order."""
    result: dict[str, list[NodeCatalogEntry]] = {}
    for entry in catalog_entries():
        result.setdefault(entry.cat, []).append(entry)
    return result


def catalog_json() -> list[dict[str, Any]]:
    """The whole catalog as plain JSON, in catalog order (section 4.3 shape)."""
    return [e.to_json() for e in catalog_entries()]
