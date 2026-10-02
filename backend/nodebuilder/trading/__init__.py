"""Trading node types (plan D5: the domain side of the kernel/domain split).

Every ``nodes_*.py`` module in this package registers its node types with
the kernel registry when it is imported, and this file imports all of them.

Registration order is the catalog order inside a category (nodes.node_types),
so it is spelled out in ``MODULE_ORDER`` rather than left to import side
effects: a module that imports a helper from another nodes_ module would
otherwise register that module first, and moving one helper import would
silently reorder the generated catalog and the Tab menu (F435 W2 KC-6).
``MODULE_ORDER`` is the order the catalog has had since W2.  A new
``nodes_<something>.py`` file still loads with no edit here (after the
listed ones, in name order), but test_w2_kc_fixes.py fails until it is
placed in ``MODULE_ORDER``.

Other modules here (helpers, later ``sim_bridge.py`` and ``align.py``) are
not imported automatically.
"""
from __future__ import annotations

import importlib
import pkgutil

MODULE_ORDER: tuple[str, ...] = (
    "nodes_compare",
    "nodes_data",
    "nodes_indicators",
    "nodes_logic",
    "nodes_math",
    "nodes_indicators_more",
    "nodes_settings",
    "nodes_slope",
    "nodes_terminals",
    "nodes_time",
    "nodes_network",
    "nodes_groups",
)


def node_module_names() -> list[str]:
    """Every ``nodes_*`` module in this package, in name order."""
    return sorted(m.name for m in pkgutil.iter_modules(__path__) if m.name.startswith("nodes_"))


def _load_node_modules() -> list[str]:
    present = set(node_module_names())
    names = [m for m in MODULE_ORDER if m in present]
    names += [m for m in node_module_names() if m not in MODULE_ORDER]
    for name in names:
        importlib.import_module(f"{__name__}.{name}")
    return names


NODE_MODULES: list[str] = _load_node_modules()
