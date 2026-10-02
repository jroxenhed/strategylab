"""Node catalog consistency + cross-language parity.

Since F435 W2 the catalog is read from the kernel registry: every module in
backend/nodebuilder/trading/ registers its node types on import.

Runs with: pytest backend/tests/nodebuilder/test_catalog_consistency.py -v
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import re
import sys
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

# The only names allowed to have compile_active=False.  Empty since W5:
# the Size and Stop terminals run (plan W5, 5.A).
CATALOG_ONLY_NAMES: set[str] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _names() -> list[str]:
    return [e.name for e in NODE_CATALOG]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# Params that are required and have no default on purpose (see
# test_param_kinds_units_and_names_are_valid).
REQUIRED_WITHOUT_DEFAULT = {("turns_up_below", "threshold"), ("turns_down_above", "threshold")}


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
        - Pass-through nodes (merge, W2): no params, so nothing of their
          own to read or write; they pass the union of their inputs on.
        - Network and boundary nodes (W5: subnet, subnet_input,
          subnet_output, output_group): flatten removes them and splices
          the streams through, so they read and write nothing of their own
          (5.0 Needs 4, 5.B Needs 9).  Told apart by registry meta.
        - All others: both non-empty is expected but any one suffices.
        """
        from nodebuilder.kernel import registry

        pass_through = {"merge"}
        for name in pass_through:
            entry = next(e for e in NODE_CATALOG if e.name == name)
            assert not entry.params, f"{name} is exempt only while it has no params"

        def _structural(name: str) -> bool:
            nt = registry.get(name)
            return bool(nt is not None and (nt.meta.get("network") or nt.meta.get("boundary")))

        both_empty = [
            e.name for e in NODE_CATALOG
            if not e.reads and not e.writes and e.name not in pass_through
            and not _structural(e.name)
        ]
        assert not both_empty, (
            f"These entries have empty reads AND writes: {both_empty}"
        )

    def test_compile_active_false_only_for_catalog_only_nodes(self):
        """compile_active=False is reserved for catalog-only nodes (none since W5)."""
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


REPO_ROOT = Path(__file__).parents[3]  # repo root: strategylab/
NODEBUILDER_TS_DIR = REPO_ROOT / "frontend" / "src" / "features" / "nodebuilder"
EXPORT_SCRIPT = REPO_ROOT / "backend" / "scripts" / "export_nodebuilder_catalog.py"


def _load_export_script():
    """Import backend/scripts/export_nodebuilder_catalog.py by path."""
    spec = importlib.util.spec_from_file_location("export_nodebuilder_catalog", EXPORT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestCrossLanguageParity:
    """F435 1.D (D9): the frontend catalog is generated from this one.

    The drift check renders catalog.generated.ts again in memory and diffs it
    against the committed file.  catalog.ts must read from the generated file
    and never list nodes or option lists of its own.
    """

    CATALOG_TS = NODEBUILDER_TS_DIR / "catalog.ts"
    GENERATED_TS = NODEBUILDER_TS_DIR / "catalog.generated.ts"

    def test_files_exist(self):
        assert self.CATALOG_TS.exists(), f"catalog.ts not found at {self.CATALOG_TS}"
        assert self.GENERATED_TS.exists(), (
            f"catalog.generated.ts not found at {self.GENERATED_TS}. "
            f"Run: backend/venv/bin/python backend/scripts/export_nodebuilder_catalog.py"
        )

    def test_generated_ts_matches_backend_catalog(self):
        """The committed catalog.generated.ts is exactly what the script writes now."""
        expected = _load_export_script().render_catalog_ts()
        committed = self.GENERATED_TS.read_text(encoding="utf-8")
        if committed != expected:
            diff = "".join(difflib.unified_diff(
                committed.splitlines(keepends=True), expected.splitlines(keepends=True),
                fromfile="catalog.generated.ts (committed)", tofile="catalog.generated.ts (from nodes.py)",
                n=2,
            ))
            pytest.fail(
                "catalog.generated.ts is stale. Regenerate it with:\n"
                "  backend/venv/bin/python backend/scripts/export_nodebuilder_catalog.py\n\n"
                + diff[:4000]
            )

    def test_script_writes_and_checks_a_file(self, tmp_path):
        """The script's own write and --check paths agree with the in-memory render."""
        script = _load_export_script()
        out = tmp_path / "catalog.generated.ts"
        assert script.main(["--check", "--out", str(out)]) == 1  # missing file is stale
        assert script.main(["--out", str(out)]) == 0
        assert out.read_text(encoding="utf-8") == script.render_catalog_ts()
        assert script.main(["--check", "--out", str(out)]) == 0
        out.write_text(out.read_text(encoding="utf-8") + "// hand edit\n", encoding="utf-8")
        assert script.main(["--check", "--out", str(out)]) == 1

    def test_generated_ts_says_it_is_generated(self):
        head = self.GENERATED_TS.read_text(encoding="utf-8")[:600]
        assert "GENERATED FILE" in head
        assert "export_nodebuilder_catalog.py" in head

    def test_generated_names_match_python_names(self):
        text = self.GENERATED_TS.read_text(encoding="utf-8")
        body = text[text.index("export const GENERATED_CATALOG"):]
        ts_names = re.findall(r'^    "name":\s*"([^"]+)"', body, re.MULTILINE)
        assert ts_names == [e.name for e in NODE_CATALOG]

    def test_catalog_ts_reads_the_generated_file_only(self):
        """catalog.ts may add UI extras but must not define nodes or options itself."""
        text = self.CATALOG_TS.read_text(encoding="utf-8")
        assert "from './catalog.generated'" in text
        assert "GENERATED_CATALOG.map(" in text
        assert not re.search(r'^\s+name:\s*["\']', text, re.MULTILINE), (
            "catalog.ts lists a node of its own; add it to backend nodes.py instead"
        )
        for const in ("INTERVAL_OPTIONS", "SOURCE_OPTIONS", "RSI_TYPE_OPTIONS", "RSI_DEFAULT_TYPE",
                      "TRAILING_STOP_TYPE_OPTIONS", "TRAILING_STOP_SOURCE_OPTIONS",
                      "TRAILING_STOP_DEFAULTS"):
            assert not re.search(rf"export const {const}\b", text), (
                f"catalog.ts defines {const}; it must re-export it from catalog.generated.ts"
            )
            assert re.search(rf"^\s+{const},$", text, re.MULTILINE), (
                f"catalog.ts no longer re-exports {const}"
            )


# ---------------------------------------------------------------------------
# F435 0.G: catalog honesty (RSI smoothing types, data sources)
# ---------------------------------------------------------------------------

class TestRsiAndSourceOptions:
    """The RSI type options must be exactly what indicators.compute_rsi
    treats differently, with the rule builder's default, in both catalogs.
    Data sources offered must be real providers (no polygon).  The frontend
    reads these lists from catalog.generated.ts (catalog.ts re-exports them)."""

    CATALOG_TS = TestCrossLanguageParity.GENERATED_TS

    def _ts_const_list(self, name: str) -> list[str]:
        text = self.CATALOG_TS.read_text(encoding="utf-8")
        m = re.search(rf"export const {name}\s*=\s*\[(.*?)\]", text, re.DOTALL)
        assert m, f"{name} not found in catalog.generated.ts"
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

    def test_backend_sources_are_real_providers(self):
        """Every offered source is a provider name backend/shared.py registers.

        Since W2 the data source is the sidebar's (or the spawn dialog's), not
        a Ticker param (plan D11); the list stays for the frontend."""
        from nodebuilder.nodes import SOURCE_OPTIONS
        shared_py = (REPO_ROOT / "backend" / "shared.py").read_text(encoding="utf-8")
        registered = set(re.findall(r'_providers\["([^"]+)"\]', shared_py))
        registered |= set(re.findall(r"_providers\['([^']+)'\]", shared_py))
        # Yahoo is registered in the dict literal that creates _providers.
        registered |= set(re.findall(r'_providers[^=\n]*=\s*\{"([^"]+)"', shared_py))
        assert "polygon" not in SOURCE_OPTIONS
        assert set(SOURCE_OPTIONS) <= registered, (
            f"sources not registered in shared.py: {set(SOURCE_OPTIONS) - registered}"
        )
        assert "source" not in {p.name for p in get_node("ticker").params}


# ---------------------------------------------------------------------------
# F435 0.A: the trailing_stop settings node
# ---------------------------------------------------------------------------

class TestTrailingStopCatalog:
    """The trailing_stop node carries exactly TrailingStopConfig's fields and
    defaults, and catalog.generated.ts offers the same choices."""

    CATALOG_TS = TestCrossLanguageParity.GENERATED_TS
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
        # W5 (5.A): trailing_stop is a group terminal in the output category.
        assert entry.cat == "output" and entry.compile_active
        assert {k: v for k, v in entry.defaults["params"].items() if k in TRAILING_STOP_DEFAULTS} == (
            TRAILING_STOP_DEFAULTS
        )
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
        m = re.search(r"export const TRAILING_STOP_DEFAULTS\s*=\s*(\{.*?\})\s*as const;", text, re.DOTALL)
        assert m, "TRAILING_STOP_DEFAULTS not found in catalog.generated.ts"
        assert json.loads(m.group(1)) == TRAILING_STOP_DEFAULTS


# ---------------------------------------------------------------------------
# F435 1.D: ParamSpec and PortsSpec on every entry (plan section 4.3)
# ---------------------------------------------------------------------------

class TestParamAndPortSpecs:
    """Every entry describes its params and ports, and the specs tell the truth."""

    def test_every_entry_has_specs(self):
        from nodebuilder.nodes import ParamSpec, PortsSpec
        for entry in NODE_CATALOG:
            assert isinstance(entry.inputs, PortsSpec), entry.name
            assert all(isinstance(p, ParamSpec) for p in entry.params), entry.name

    def test_required_without_default_list_is_exact(self):
        """The exemption below names real, required, default-less params only."""
        for type_name, param_name in REQUIRED_WITHOUT_DEFAULT:
            entry = next(e for e in NODE_CATALOG if e.name == type_name)
            spec = next(p for p in entry.params if p.name == param_name)
            assert spec.default is None and not spec.optional

    def test_param_kinds_units_and_names_are_valid(self):
        from nodebuilder.nodes import PARAM_DTYPES, PARAM_TYPES, PARAM_UNITS
        for entry in NODE_CATALOG:
            names = [p.name for p in entry.params]
            assert len(names) == len(set(names)), f"{entry.name}: duplicate param names"
            for p in entry.params:
                where = f"{entry.name}.{p.name}"
                assert p.type in PARAM_TYPES, where
                assert p.unit is None or p.unit in PARAM_UNITS, where
                assert p.dtype is None or p.dtype in PARAM_DTYPES, where
                assert p.label, where
                if p.type == "select":
                    assert p.options, f"{where}: a select needs options"
                    if isinstance(p.default, list):
                        # A multi-select (day_of_week.days, W2): every item is an option.
                        assert all(d in p.options for d in p.default), f"{where}: default item is not an option"
                    else:
                        assert p.default in p.options, f"{where}: default is not an option"
                else:
                    assert p.options is None, f"{where}: only a select has options"
                if p.min is not None and p.max is not None:
                    assert p.min <= p.max, where
                if p.default is not None and p.type in ("number", "int"):
                    assert p.min is None or p.default >= p.min, where
                    assert p.max is None or p.default <= p.max, where
                if p.type == "int" and p.default is not None:
                    assert isinstance(p.default, int), where
                if p.default is None and p.type not in ("attr", "attr_list"):
                    # An attr param with no default reads what the wired node
                    # writes first (plan D4), so it may still be required.
                    # The turn-at thresholds (W2) are required on purpose: the
                    # rule engine is never true without one, so a fresh node
                    # shows param_invalid until a value is typed
                    # (test_nodes_slope pins that).
                    if (entry.name, p.name) in REQUIRED_WITHOUT_DEFAULT:
                        continue
                    assert p.optional, f"{where}: a param with no default must be optional"
                if p.type in ("attr", "attr_list", "write"):
                    assert p.dtype in ("float", "bool", "any"), f"{where}: say what type it reads or writes"
                if p.type == "write":
                    assert re.match(r"^@[a-z_][a-z0-9_]{0,63}$", p.default), where

    def test_defaults_params_come_from_specs(self):
        """compile reads defaults['params']; it must equal the spec defaults, in order."""
        for entry in NODE_CATALOG:
            assert list(entry.defaults["params"].items()) == [(p.name, p.default) for p in entry.params]
            selects = {p.name: p.options for p in entry.params if p.type == "select"}
            assert entry.defaults.get("param_options", {}) == selects, entry.name

    def test_params_cannot_also_be_written_in_defaults(self):
        from nodebuilder.nodes import NodeCatalogEntry, ParamSpec
        with pytest.raises(ValueError, match="ParamSpec"):
            NodeCatalogEntry(
                name="x", cat="indicator", desc="", reads=(), writes=("@x",),
                defaults={"params": {"period": 3}, "ins": 1, "outs": 1, "subtitle": None},
                params=(ParamSpec("period", "int", "period", 3),),
            )

    def test_ports_are_coherent(self):
        for entry in NODE_CATALOG:
            spec = entry.inputs
            required = sum(1 for p in spec.ports if not p.optional)
            assert 0 <= spec.min <= spec.max, entry.name
            assert required == spec.min, f"{entry.name}: required ports must equal min"
            if not spec.dynamic:
                assert len(spec.ports) == spec.max, entry.name
            else:
                assert len(spec.ports) <= spec.max, entry.name

    def test_ports_match_what_compile_accepts(self):
        """Sources and settings take no wire; terminals take one; comparisons a and b."""
        for entry in NODE_CATALOG:
            if entry.cat in ("ticker", "settings"):
                assert entry.inputs.max == 0, entry.name
            if entry.cat == "output":
                assert entry.inputs.max == 1, entry.name
            if entry.cat == "comparison":
                assert [p.label for p in entry.inputs.ports] == ["a", "b"], entry.name
                assert (entry.inputs.min, entry.inputs.max) == (1, 2), entry.name
        assert get_node("entry").inputs.min == 1
        assert get_node("exit").inputs.min == 0  # an unwired Exit is a warning, not an error
        assert get_node("and").inputs.dynamic and get_node("or").inputs.dynamic
        assert not get_node("not").inputs.dynamic and get_node("not").inputs.max == 1

    def test_ticker_symbol_and_interval_are_not_code_able(self):
        """Plan D1: a Ticker's symbol and interval may never hold code.

        An output group's direction, ticker and capital weight are
        structural like the Ticker's (they decide which bots spawn and what
        is fetched), so they are not code-able either (5.B Needs 9).
        """
        specs = {p.name: p for p in get_node("ticker").params}
        assert not specs["symbol"].code_able
        assert not specs["interval"].code_able
        group = {p.name: p.code_able for p in get_node("output_group").params}
        assert group == {"direction": False, "ticker": False, "capital_weight": False}
        for entry in NODE_CATALOG:
            if entry.name not in ("ticker", "output_group"):
                assert all(p.code_able for p in entry.params), entry.name

    def test_int_limits_match_what_compile_accepts(self):
        """A period at min and max compiles; one step outside is refused."""
        from nodebuilder.compile import compile_with_diagnostics
        from nodebuilder.models import Graph

        def _codes(node_type, params):
            graph = Graph.model_validate({
                "_version": 2,
                "nodes": {
                    "/t": {"id": "/t", "type": "ticker", "params": {}},
                    "/n": {"id": "/n", "type": node_type, "params": params},
                },
                "wires": [{"id": "w", "from": "/t", "to": "/n", "to_port": "in0"}],
            })
            _program, diags = compile_with_diagnostics(graph)
            return {(d.code, d.param) for d in diags if d.node_id == "/n"}

        checked = 0
        for entry in NODE_CATALOG:
            if entry.cat != "indicator":
                continue
            base = {p.name: p.default for p in entry.params if p.type in ("int", "number", "select")}
            for p in entry.params:
                if p.type != "int" or p.min is None or p.max is None:
                    continue
                checked += 1
                assert not _codes(entry.name, {**base, p.name: int(p.min)}), (entry.name, p.name)
                assert not _codes(entry.name, {**base, p.name: int(p.max)}), (entry.name, p.name)
                assert ("param_out_of_range", p.name) in _codes(entry.name, {**base, p.name: int(p.min) - 1})
                assert ("param_out_of_range", p.name) in _codes(entry.name, {**base, p.name: int(p.max) + 1})
        assert checked >= 8

    def test_setting_limits_match_what_compile_accepts(self):
        from nodebuilder.compile import compile_with_diagnostics
        from nodebuilder.models import Graph

        def _diags(node_type, params):
            graph = Graph.model_validate({"_version": 2, "nodes": {
                "/s": {"id": "/s", "type": node_type, "params": params}}, "wires": []})
            return {(d.code, d.param, d.severity)
                    for d in compile_with_diagnostics(graph)[1] if d.node_id == "/s"}

        assert not _diags("position_size", {"size": 1.0})
        assert ("size_unit_suspect", "size", "warning") in _diags("position_size", {"size": 1.01})
        assert ("param_out_of_range", "size", "error") in _diags("position_size", {"size": 0.0})
        assert ("param_out_of_range", "bps", "error") in _diags("slippage", {"bps": -0.1})
        assert ("param_out_of_range", "per_share_rate", "error") in _diags(
            "commission", {"per_share_rate": -0.01})
        # An empty stop-loss field turns the stop off in the editor, so the
        # spec says optional; a stored None is refused by compile.
        assert next(p for p in get_node("stop_loss").params if p.name == "pct").optional

    def test_every_numeric_settings_param_has_a_unit(self):
        """Wave 0 honesty: a settings number always says how it is read."""
        for entry in NODE_CATALOG:
            if entry.cat != "settings":
                continue
            for p in entry.params:
                if p.type in ("number", "int"):
                    assert p.unit, f"{entry.name}.{p.name} has no unit"
        size = get_node("position_size").params[0]
        assert (size.unit, size.min, size.max) == ("frac", 0.0, 1.0)

    def test_to_json_has_the_section_4_3_shape(self):
        rsi = get_node("rsi").to_json()
        assert list(rsi) == [
            "name", "cat", "desc", "compile_active", "inputs", "params", "reads", "writes",
            "subtitle", "setting_key", "ins", "outs",
        ]
        assert rsi["params"][0] == {
            "name": "period", "type": "int", "label": "period", "default": 14,
            "min": 2, "max": 500, "unit": "bars",
        }
        assert rsi["params"][1]["options"] == ["sma", "wilder"]
        cmp = get_node("crosses_above").to_json()
        assert cmp["inputs"] == {
            "ports": [{"label": "a"}, {"label": "b", "optional": True}],
            "dynamic": False, "min": 1, "max": 2,
        }
        assert cmp["params"] == [
            {"name": "a", "type": "attr", "label": "a", "default": None, "dtype": "float"},
            {"name": "b", "type": "attr", "label": "b", "default": None, "dtype": "float",
             "optional": True},
            {"name": "threshold", "type": "number", "label": "threshold", "default": None, "optional": True},
            {"name": "out", "type": "write", "label": "out", "default": "@xa", "dtype": "bool"},
        ]
        json.dumps([e.to_json() for e in NODE_CATALOG])  # plain JSON all the way down


# ---------------------------------------------------------------------------
# F435 W2 (2.A): the registry is the single source of node types
# ---------------------------------------------------------------------------


class TestRegistryIsTheSource:

    def test_catalog_is_the_registry_in_category_order(self):
        from nodebuilder.kernel import registry
        from nodebuilder.nodes import NODE_CATEGORIES, catalog_entries

        registered = {t.name for t in registry.all_types()}
        assert {e.name for e in catalog_entries()} == registered
        ranks = [list(NODE_CATEGORIES).index(e.cat) for e in catalog_entries()]
        assert ranks == sorted(ranks)

    def test_every_compile_active_type_runs_or_passes_its_input_on(self):
        from nodebuilder.kernel import registry

        for t in registry.all_types():
            if t.compile_active and t.has_output:
                assert t.impl is not None, f"{t.name} writes something, so it needs an impl"

    def test_a_new_module_needs_no_edit_anywhere_else(self):
        """Registering a type is enough for compile, the catalog and the export."""
        from nodebuilder.compile import compile_with_diagnostics
        from nodebuilder.kernel import registry
        from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec
        from nodebuilder.models import Graph
        from nodebuilder.nodes import catalog_json

        def _double(inputs, params):
            return inputs.with_point(params["out"], inputs.column(params["a"]) * 2,
                                     params.node_id, "float")

        registry.register_node(
            name="zz_double", cat="signal", desc="Twice a.",
            params=(ParamSpec("a", "attr", "a", None, dtype="float"),
                    ParamSpec("out", "write", "out", "@double", dtype="float")),
            inputs=PortsSpec(ports=(PortSpec("a"),), dynamic=False, min=1, max=1),
            impl=_double, module="test_catalog_consistency",
        )
        try:
            assert "zz_double" in {e["name"] for e in catalog_json()}
            assert '"name": "zz_double"' in _load_export_script().render_catalog_ts()
            graph = Graph.model_validate({"_version": 2, "nodes": {
                "/t": {"id": "/t", "type": "ticker", "params": {}},
                "/d": {"id": "/d", "type": "zz_double", "params": {}},
            }, "wires": [{"id": "w", "from": "/t", "to": "/d", "to_port": "in0"}]})
            diags = compile_with_diagnostics(graph)[1]
            assert not [d for d in diags if d.node_id == "/d"]
        finally:
            registry.unregister("zz_double")

    def test_two_modules_cannot_claim_one_type(self):
        from nodebuilder.kernel import registry

        with pytest.raises(ValueError, match="registered by both"):
            registry.register_node(name="rsi", cat="indicator", desc="x", module="somewhere_else")
