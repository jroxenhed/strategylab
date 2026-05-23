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
