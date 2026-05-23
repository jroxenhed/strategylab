"""Unit 10: CI parity check — rule-builder indicators ⊆ node catalog.

Every non-legacy, non-deferred RuleIndicator value must map to at least one
entry in NODE_CATALOG.  Carve-outs are explicit and documented so future
additions to signal_engine.RuleIndicator are caught immediately.
"""

from __future__ import annotations

import sys
import os

# Ensure backend/ is on sys.path so bare imports work (mirrors conftest pattern).
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ---------------------------------------------------------------------------
# Mapping from rule-builder indicator names → required NODE_CATALOG name(s).
#
# Empty tuple () means "no dedicated node required":
#   - volume/price  → served by Ticker node's @volume / @close stream attrs
#   - stochastic/adx → T3 scope, nodes not yet implemented
# ---------------------------------------------------------------------------
RULE_TO_CATALOG: dict[str, tuple[str, ...]] = {
    "rsi":        ("rsi",),
    "macd":       ("macd",),
    "ma":         ("sma", "ema"),   # runtime dispatches on params.type
    "bb":         ("bollinger",),
    "atr":        ("atr",),
    "atr_pct":    ("atr",),         # reuses the atr node, no separate entry
    "volume":     (),               # served by Ticker @volume stream attr
    "price":      (),               # served by Ticker @close stream attr
    "stochastic": (),               # T3 scope — node impl deferred
    "adx":        (),               # T3 scope — node impl deferred
}

# Legacy migration aliases accepted at validation but converted by migrate_rule()
# before they ever reach the backtester; no catalog node is needed.
LEGACY_MIGRATION_INDICATORS: frozenset[str] = frozenset(
    {"ema20", "ema50", "ema200", "ma8", "ma21"}
)

# Until T3 lands stochastic/adx node impls, carve these out — they have
# rule-side support but no graph-mode equivalent.  Re-enable the parity check
# for these when T3 lands their nodes.
T3_DEFERRED_INDICATORS: frozenset[str] = frozenset({"stochastic", "adx"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_rule_indicators() -> set[str]:
    """Extract the Literal[...] members from RuleIndicator at runtime.

    Using get_args() means this test automatically catches new additions to
    the Literal without requiring a manual list update here.
    """
    from typing import get_args
    from signal_engine import RuleIndicator  # noqa: PLC0415
    return set(get_args(RuleIndicator))


def _get_catalog_names() -> set[str]:
    from nodebuilder.nodes import NODE_CATALOG  # noqa: PLC0415
    return {e.name for e in NODE_CATALOG}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_non_legacy_rule_indicators_have_catalog_entries():
    """Every active (non-legacy, non-deferred) RuleIndicator must map to a
    NODE_CATALOG entry via RULE_TO_CATALOG, or have an explicit empty-tuple
    carve-out in RULE_TO_CATALOG documenting why no node is needed.
    """
    rule_indicators = (
        _get_rule_indicators()
        - LEGACY_MIGRATION_INDICATORS
        - T3_DEFERRED_INDICATORS
    )
    catalog = _get_catalog_names()

    missing: list[tuple[str, str]] = []
    for rule_ind in sorted(rule_indicators):
        # Fall back to (rule_ind,) so brand-new indicators without an explicit
        # mapping are automatically checked — they'd need to be either added to
        # RULE_TO_CATALOG or carved out there before this test goes green.
        targets = RULE_TO_CATALOG.get(rule_ind, (rule_ind,))
        for t in targets:
            if t and t not in catalog:
                missing.append((rule_ind, t))

    assert not missing, (
        f"Rule indicators missing node catalog entries: {missing}. "
        "Add the missing nodes to NODE_CATALOG or document the carve-out in "
        "RULE_TO_CATALOG (empty tuple = no node required) in this test file."
    )


def test_legacy_migration_indicators_are_excluded():
    """LEGACY_MIGRATION_INDICATORS must be a *subset* of the actual Literal
    members so we don't silently carry stale entries if signal_engine.py is
    ever cleaned up.
    """
    rule_indicators = _get_rule_indicators()
    assert LEGACY_MIGRATION_INDICATORS.issubset(rule_indicators), (
        "Legacy carve-out list contains values no longer in RuleIndicator — "
        "remove them from LEGACY_MIGRATION_INDICATORS."
    )


def test_t3_deferred_indicators_documented():
    """T3 carve-outs must be tracked so we don't forget to land their nodes
    later.  If you need to change the set, update both this assertion AND the
    T3 plan to list the new nodes as required exit criteria.
    """
    assert T3_DEFERRED_INDICATORS == frozenset({"stochastic", "adx"}), (
        "If you change T3_DEFERRED_INDICATORS, update the T3 plan to include "
        "the newly-deferred indicators as required nodes for T3 exit."
    )


def test_synthetic_drift_fails():
    """Regression guard: if a new rule indicator is added to signal_engine.py
    without a corresponding NODE_CATALOG entry or RULE_TO_CATALOG carve-out,
    the parity check logic must flag it.

    We cannot mutate the actual Literal at runtime, so we exercise the
    parity-check logic directly with a synthetic fake input.
    """
    fake_rule_indicators = {"newindicator"}
    catalog = _get_catalog_names()

    missing: list[tuple[str, str]] = []
    for rule_ind in fake_rule_indicators:
        targets = RULE_TO_CATALOG.get(rule_ind, (rule_ind,))
        for t in targets:
            if t and t not in catalog:
                missing.append((rule_ind, t))

    assert missing == [("newindicator", "newindicator")], (
        f"Expected synthetic drift to be flagged; got missing={missing}"
    )
