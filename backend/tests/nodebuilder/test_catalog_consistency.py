"""Unit 2 backend tests: NODE_CATALOG consistency + cross-language parity.

Runs with: pytest backend/tests/nodebuilder/test_catalog_consistency.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from nodebuilder.nodes import (
    NODE_CATALOG,
    NODE_CATEGORIES,
    NodeCatalogEntry,
    catalog_by_category,
    get_node,
)

# ---------------------------------------------------------------------------
# Known categories (superset — catalog may not use all of them)
# ---------------------------------------------------------------------------

KNOWN_CATEGORIES = set(NODE_CATEGORIES.keys())

# Minimum set that must be present in NODE_CATALOG per the plan.
REQUIRED_CATEGORIES = {"ticker", "indicator", "comparison", "logic", "settings", "output"}

# The only names allowed to have compile_active=False at T2.
CATALOG_ONLY_NAMES = {"size", "stop"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _names() -> list[str]:
    return [e.name for e in NODE_CATALOG]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCatalogIntegrity:

    def test_catalog_nonempty(self):
        assert len(NODE_CATALOG) > 0, "NODE_CATALOG must not be empty."

    def test_all_entries_are_NodeCatalogEntry(self):
        for entry in NODE_CATALOG:
            assert isinstance(entry, NodeCatalogEntry), (
                f"Entry {entry!r} is not a NodeCatalogEntry instance."
            )

    def test_unique_names(self):
        names = _names()
        assert len(names) == len(set(names)), (
            f"Duplicate names in NODE_CATALOG: "
            f"{[n for n in set(names) if names.count(n) > 1]}"
        )

    def test_all_cats_are_known(self):
        unknown = {e.name: e.cat for e in NODE_CATALOG if e.cat not in KNOWN_CATEGORIES}
        assert not unknown, (
            f"Entries with unknown 'cat' value: {unknown}. "
            f"Known categories: {sorted(KNOWN_CATEGORIES)}"
        )

    def test_reads_or_writes_populated(self):
        """Every entry must have a non-empty reads OR a non-empty writes tuple.

        - Source nodes (ticker): writes only, reads empty. OK.
        - Terminal nodes (entry/exit/size/stop): reads only, writes empty. OK.
        - Settings nodes: writes ("@setting",), reads empty. OK.
        - All others: both non-empty is expected but any one suffices.
        """
        both_empty = [
            e.name for e in NODE_CATALOG
            if not e.reads and not e.writes
        ]
        assert not both_empty, (
            f"These entries have empty reads AND writes: {both_empty}"
        )

    def test_compile_active_false_only_for_catalog_only_nodes(self):
        """compile_active=False is reserved for the T2 catalog-only Size/Stop terminals."""
        inactive = {e.name for e in NODE_CATALOG if not e.compile_active}
        assert inactive == CATALOG_ONLY_NAMES, (
            f"Expected compile_active=False only for {CATALOG_ONLY_NAMES}, "
            f"got: {inactive}"
        )

    def test_required_categories_present(self):
        present = {e.cat for e in NODE_CATALOG}
        missing = REQUIRED_CATEGORIES - present
        assert not missing, (
            f"Required categories missing from NODE_CATALOG: {missing}"
        )

    def test_defaults_has_required_keys(self):
        required_keys = {"params", "ins", "outs", "subtitle"}
        for entry in NODE_CATALOG:
            missing = required_keys - set(entry.defaults.keys())
            assert not missing, (
                f"Entry '{entry.name}' defaults dict is missing keys: {missing}"
            )

    def test_settings_nodes_have_setting_key(self):
        settings_entries = [e for e in NODE_CATALOG if e.cat == "settings"]
        assert settings_entries, "No settings entries found."
        for entry in settings_entries:
            assert "setting_key" in entry.defaults, (
                f"Settings entry '{entry.name}' is missing 'setting_key' in defaults."
            )
            assert isinstance(entry.defaults["setting_key"], str), (
                f"Settings entry '{entry.name}'.defaults['setting_key'] must be a str."
            )

    def test_writes_use_at_prefix(self):
        """All non-empty write attributes must start with '@'."""
        for entry in NODE_CATALOG:
            for attr in entry.writes:
                assert attr.startswith("@"), (
                    f"Entry '{entry.name}' writes attribute {attr!r} lacks '@' prefix."
                )

    def test_reads_use_at_prefix(self):
        """All non-empty read attributes must start with '@'."""
        for entry in NODE_CATALOG:
            for attr in entry.reads:
                assert attr.startswith("@"), (
                    f"Entry '{entry.name}' reads attribute {attr!r} lacks '@' prefix."
                )


class TestHelperFunctions:

    def test_get_node_returns_correct_entry(self):
        entry = get_node("rsi")
        assert entry.name == "rsi"
        assert entry.cat == "indicator"

    def test_get_node_raises_on_missing(self):
        with pytest.raises(KeyError, match="nonexistent"):
            get_node("nonexistent")

    def test_catalog_by_category_covers_all_entries(self):
        grouped = catalog_by_category()
        all_names_grouped = sorted(e.name for entries in grouped.values() for e in entries)
        all_names_catalog = sorted(e.name for e in NODE_CATALOG)
        assert all_names_grouped == all_names_catalog

    def test_catalog_by_category_returns_required_cats(self):
        grouped = catalog_by_category()
        present = set(grouped.keys())
        missing = REQUIRED_CATEGORIES - present
        assert not missing, f"catalog_by_category() is missing categories: {missing}"


class TestCrossLanguageParity:
    """Parse catalog.ts as text and assert name sets match exactly.

    No TypeScript evaluation — we extract `name: "..."` patterns via regex.
    """

    CATALOG_TS = (
        Path(__file__).parents[3]  # repo root: strategylab/
        / "frontend"
        / "src"
        / "features"
        / "nodebuilder"
        / "catalog.ts"
    )

    def _extract_ts_names(self) -> set[str]:
        text = self.CATALOG_TS.read_text(encoding="utf-8")
        # Match:  name: "rsi",  or  name: "rsi"  (with optional trailing comma/space)
        # inside the NODE_CATALOG array block. We rely on the convention that every
        # NodeCatalogEntry object has a `name:` field — match with any leading whitespace.
        matches = re.findall(r'^\s+name:\s*"([^"]+)"', text, re.MULTILINE)
        return set(matches)

    def test_catalog_ts_exists(self):
        assert self.CATALOG_TS.exists(), (
            f"catalog.ts not found at expected path: {self.CATALOG_TS}"
        )

    def test_ts_names_match_python_names(self):
        ts_names = self._extract_ts_names()
        py_names = set(e.name for e in NODE_CATALOG)

        only_in_ts = ts_names - py_names
        only_in_py = py_names - ts_names

        assert not only_in_ts and not only_in_py, (
            f"Cross-language catalog mismatch.\n"
            f"  Only in catalog.ts: {sorted(only_in_ts)}\n"
            f"  Only in nodes.py:   {sorted(only_in_py)}"
        )

    def test_ts_name_count_matches_python(self):
        ts_names = self._extract_ts_names()
        py_count = len(NODE_CATALOG)
        assert len(ts_names) == py_count, (
            f"catalog.ts has {len(ts_names)} unique names, "
            f"nodes.py has {py_count}. They must match."
        )


# ---------------------------------------------------------------------------
# F435 0.G: catalog honesty (RSI smoothing types, data sources)
# ---------------------------------------------------------------------------

class TestRsiAndSourceOptions:
    """The RSI type options must be exactly what indicators.compute_rsi
    treats differently, with the rule builder's default, in both catalogs.
    Data sources offered must be real providers (no polygon)."""

    CATALOG_TS = TestCrossLanguageParity.CATALOG_TS

    def _ts_const_list(self, name: str) -> list[str]:
        text = self.CATALOG_TS.read_text(encoding="utf-8")
        m = re.search(rf"export const {name}\s*=\s*\[(.*?)\]", text, re.DOTALL)
        assert m, f"{name} not found in catalog.ts"
        pairs = re.findall(r"'([^']+)'|\"([^\"]+)\"", m.group(1))
        return [single or double for single, double in pairs]

    def test_backend_rsi_options_match_compute_rsi(self):
        """Each option gives its own RSI series; any other value is just sma."""
        import numpy as np
        import pandas as pd
        from indicators import OHLCVSeries, compute_rsi
        from nodebuilder.nodes import RSI_DEFAULT_TYPE, RSI_TYPE_OPTIONS

        close = pd.Series(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 200)))
        o = OHLCVSeries(close=close, high=close, low=close, volume=close * 0)
        series = {t: compute_rsi(o, {"period": 14, "type": t})["rsi"] for t in RSI_TYPE_OPTIONS}
        assert set(RSI_TYPE_OPTIONS) == {"sma", "wilder"}
        assert not series["sma"].equals(series["wilder"])
        # Unknown types fall back to sma, so offering them would be a lie.
        assert compute_rsi(o, {"period": 14, "type": "ema"})["rsi"].equals(series["sma"])
        assert RSI_DEFAULT_TYPE == "wilder"
        entry = get_node("rsi")
        assert entry.defaults["params"]["type"] == RSI_DEFAULT_TYPE
        assert tuple(entry.defaults["param_options"]["type"]) == RSI_TYPE_OPTIONS

    def test_ts_rsi_options_match_backend(self):
        from nodebuilder.nodes import RSI_DEFAULT_TYPE, RSI_TYPE_OPTIONS
        assert self._ts_const_list("RSI_TYPE_OPTIONS") == list(RSI_TYPE_OPTIONS)
        text = self.CATALOG_TS.read_text(encoding="utf-8")
        m = re.search(r"export const RSI_DEFAULT_TYPE\s*=\s*['\"]([^'\"]+)['\"]", text)
        assert m and m.group(1) == RSI_DEFAULT_TYPE

    def test_ts_sources_have_no_polygon(self):
        assert "polygon" not in self._ts_const_list("SOURCE_OPTIONS")


# ---------------------------------------------------------------------------
# F435 0.A: the trailing_stop settings node
# ---------------------------------------------------------------------------

class TestTrailingStopCatalog:
    """The trailing_stop node carries exactly TrailingStopConfig's fields and
    defaults, and catalog.ts offers the same choices."""

    CATALOG_TS = TestCrossLanguageParity.CATALOG_TS
    _ts_const_list = TestRsiAndSourceOptions._ts_const_list

    def test_backend_defaults_match_trailing_stop_config(self):
        from models import TrailingStopConfig
        from nodebuilder.nodes import (
            TRAILING_STOP_DEFAULTS,
            TRAILING_STOP_SOURCE_OPTIONS,
            TRAILING_STOP_TYPE_OPTIONS,
        )

        assert TRAILING_STOP_DEFAULTS == TrailingStopConfig().model_dump()
        entry = get_node("trailing_stop")
        assert entry.cat == "settings" and entry.compile_active
        assert entry.defaults["params"] == TRAILING_STOP_DEFAULTS
        assert entry.defaults["setting_key"] == "trailing_stop"
        assert tuple(entry.defaults["param_options"]["type"]) == TRAILING_STOP_TYPE_OPTIONS
        assert tuple(entry.defaults["param_options"]["source"]) == TRAILING_STOP_SOURCE_OPTIONS
        assert TrailingStopConfig().type in TRAILING_STOP_TYPE_OPTIONS
        assert TrailingStopConfig().source in TRAILING_STOP_SOURCE_OPTIONS

    def test_ts_options_and_defaults_match_backend(self):
        from nodebuilder.nodes import (
            TRAILING_STOP_DEFAULTS,
            TRAILING_STOP_SOURCE_OPTIONS,
            TRAILING_STOP_TYPE_OPTIONS,
        )

        assert self._ts_const_list("TRAILING_STOP_TYPE_OPTIONS") == list(TRAILING_STOP_TYPE_OPTIONS)
        assert self._ts_const_list("TRAILING_STOP_SOURCE_OPTIONS") == list(TRAILING_STOP_SOURCE_OPTIONS)
        text = self.CATALOG_TS.read_text(encoding="utf-8")
        m = re.search(r"export const TRAILING_STOP_DEFAULTS\s*=\s*\{(.*?)\}", text, re.DOTALL)
        assert m, "TRAILING_STOP_DEFAULTS not found in catalog.ts"
        ts = {}
        for key, raw in re.findall(r"(\w+):\s*([^,\n]+)", m.group(1)):
            raw = raw.strip()
            if raw in ("true", "false"):
                ts[key] = raw == "true"
            elif raw[0] in "'\"":
                ts[key] = raw.strip("'\"")
            else:
                ts[key] = float(raw)
        assert ts == TRAILING_STOP_DEFAULTS
