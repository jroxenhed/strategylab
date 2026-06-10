"""Unit tests for the F388 premise spec, compiler, and store.

These tests use synthetic fixtures only — no real data.
Real-data anchors (F338) are in probe_premise_form4.py (standalone script).

Run:
    backend/venv/bin/python3 -m pytest backend/research/test_premise_spec.py -x -q

Path-setup follows test_form4_ingest.py lines 28–44 exactly.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — run from repo root or from backend/
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from research.premise_spec import (  # noqa: E402
    GuidedAnswers,
    PremiseSpec,
    UniverseFloors,
    spec_hash,
)
from research.premise_compile import compile_spec, CompileResult  # noqa: E402
from research.premise_store import PremiseStore                   # noqa: E402
import research.premise_store as _ps_module                      # noqa: E402


# ===========================================================================
# §7.1 Validation tests (pure Pydantic, no I/O)
# ===========================================================================

def test_valid_spec_compiles():
    spec = PremiseSpec(
        premise_text="Insider cluster buy → price appreciation",
        stream="form4",
        event_filter={"transaction_codes": ["P"], "exclude_10b51": True},
        dose="r1_score",
        dose_params={"window_bdays": 21},
        horizons=(21, 63, 126),
        entry_lag_days=1,
        dedup_window_days=30,
    )
    assert spec.stream == "form4"
    assert spec.dose == "r1_score"
    assert spec.horizons == (21, 63, 126)


def test_unknown_stream_rejected():
    # Pydantic wraps ValueError in ValidationError; match is case-insensitive
    with pytest.raises(Exception, match="(?i)ledger enforcement"):
        PremiseSpec(premise_text="test", stream="made_up_stream")


def test_unknown_dose_rejected():
    with pytest.raises(Exception, match="(?i)ledger enforcement"):
        PremiseSpec(premise_text="test", dose="arbitrary_formula")


def test_out_of_vocab_filter_rejected():
    with pytest.raises(Exception, match="(?i)ledger enforcement"):
        PremiseSpec(
            premise_text="test",
            stream="form4",
            event_filter={"secret_filter_nobody_declared": True},
        )


def test_out_of_vocab_dose_params_rejected():
    with pytest.raises(Exception, match="(?i)ledger enforcement"):
        PremiseSpec(
            premise_text="test",
            stream="form4",
            dose="r1_score",
            dose_params={"undeclared_param": 99},
        )


def test_all_filter_vocab_keys_accepted():
    """All FORM4_FILTER_VOCAB keys must be accepted without error."""
    from research.streams.form4 import FORM4_FILTER_VOCAB
    for key in FORM4_FILTER_VOCAB:
        # Use a valid value for each key type
        value: object
        if key == "transaction_codes":
            value = ["P"]
        elif key == "form_types":
            value = ["4", "4/A"]
        elif key == "exclude_10b51":
            value = True
        elif key == "min_dollar_total":
            value = 0.0
        else:
            value = True
        # Should not raise
        spec = PremiseSpec(
            premise_text="test",
            stream="form4",
            event_filter={key: value},
        )
        assert spec.stream == "form4"


def test_all_dose_vocab_keys_accepted():
    """All FORM4_DOSE_VOCAB keys must be accepted without error."""
    from research.streams.form4 import FORM4_DOSE_VOCAB
    for key in FORM4_DOSE_VOCAB:
        value: object
        if key == "window_bdays":
            value = 21
        elif key == "beta":
            value = 0.5
        else:
            value = 1.0
        spec = PremiseSpec(
            premise_text="test",
            stream="form4",
            dose="r1_score",
            dose_params={key: value},
        )
        assert spec.stream == "form4"


def test_guided_answers_optional():
    spec = PremiseSpec(
        premise_text="test idea",
        guided=GuidedAnswers(trigger="Form 4 P-code buys", hold_length="63 days"),
    )
    assert spec.guided is not None
    assert spec.guided.trigger == "Form 4 P-code buys"


def test_universe_floors_defaults():
    spec = PremiseSpec(premise_text="test")
    assert spec.floors.min_price == 5.0
    assert spec.floors.min_avg_volume == 500_000


def test_direction_literal_validated():
    """direction must be 'long' or 'short'."""
    spec = PremiseSpec(premise_text="test", direction="long")
    assert spec.direction == "long"
    spec2 = PremiseSpec(premise_text="test", direction="short")
    assert spec2.direction == "short"
    with pytest.raises(Exception):
        PremiseSpec(premise_text="test", direction="neutral")  # type: ignore


# ===========================================================================
# OVERRIDE 1 — spec_hash: structural fields only
# ===========================================================================

def test_spec_hash_same_for_different_prose():
    """Two specs identical in structure but different premise_text/plain_summary
    must produce the SAME hash (prose is non-structural)."""
    spec_a = PremiseSpec(
        premise_text="Insider cluster buy → price appreciation",
        plain_summary="When insiders buy, stocks go up.",
        stream="form4",
        entry_lag_days=1,
    )
    spec_b = PremiseSpec(
        premise_text="Completely different wording of the same idea",
        plain_summary="A totally different readback.",
        stream="form4",
        entry_lag_days=1,
    )
    assert spec_hash(spec_a) == spec_hash(spec_b), (
        "Changing only prose fields must NOT change the spec_hash"
    )


def test_spec_hash_changes_on_structural_field():
    """Changing any structural field must produce a DIFFERENT hash."""
    spec_a = PremiseSpec(premise_text="test", stream="form4", entry_lag_days=1)
    spec_b = PremiseSpec(premise_text="test", stream="form4", entry_lag_days=2)
    assert spec_hash(spec_a) != spec_hash(spec_b), (
        "Changing entry_lag_days (structural) must change the spec_hash"
    )


def test_spec_hash_stable():
    """Same spec constructed twice must produce the same hash."""
    spec_a = PremiseSpec(premise_text="test", stream="form4")
    spec_b = PremiseSpec(premise_text="test", stream="form4")
    assert spec_hash(spec_a) == spec_hash(spec_b)


def test_spec_hash_changes_on_mutation():
    """Each structural field change individually must produce a different hash."""
    base = PremiseSpec(premise_text="test", stream="form4", entry_lag_days=1)
    mutated = PremiseSpec(premise_text="test", stream="form4", entry_lag_days=2)
    assert spec_hash(base) != spec_hash(mutated)


def test_spec_hash_horizons_structural():
    spec_a = PremiseSpec(premise_text="test", horizons=(21, 63, 126))
    spec_b = PremiseSpec(premise_text="test", horizons=(21, 63))
    assert spec_hash(spec_a) != spec_hash(spec_b)


def test_spec_hash_excludes_spec_hash_field():
    """spec_hash field on the model must not affect the computed hash."""
    spec_a = PremiseSpec(premise_text="test")
    spec_b = PremiseSpec(premise_text="test", spec_hash="deadbeefdeadbeef")
    # spec_hash field is excluded from hashing, so hash must be identical
    assert spec_hash(spec_a) == spec_hash(spec_b)


def test_spec_hash_is_16_chars():
    spec = PremiseSpec(premise_text="test")
    h = spec_hash(spec)
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


# ===========================================================================
# §7.3 Compiler equivalence test — reproduces R-1 charter config
# ===========================================================================

def test_compile_reproduces_r1_config():
    """Reproduce the R-1 charter EventStudyConfig from run_r1_explore.py:248–268."""
    spec = PremiseSpec(
        premise_text="Insider cluster P-code buys → 21/63/126d alpha",
        stream="form4",
        event_filter={"transaction_codes": ["P"], "exclude_10b51": True},
        dose="r1_score",
        dose_params={"window_bdays": 21, "beta": 0.5},
        horizons=(21, 63, 126),
        entry_lag_days=1,
        dedup_same_ticker=True,
        dedup_window_days=30,
        min_peer_count=8,
        fdr_q=0.10,
        n_boot=999,
    )
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_spec(
            spec,
            study_name="r1_insider_clusters_explore_2015_2020",
            fdr_ledger_path=Path(tmp) / "ledger.json",
        )
    cfg = result.config
    assert cfg.horizons == (21, 63, 126)
    assert cfg.entry_lag_days == 1
    assert cfg.dedup_same_ticker is True
    assert cfg.dedup_window_days == 30
    assert cfg.n_boot == 999
    assert cfg.fdr_q == 0.10
    assert cfg.min_peer_count == 8
    assert cfg.explore_cutoff == date(2020, 12, 31)
    assert cfg.allow_post_2020_explore is False


def test_compile_returns_compileresult():
    spec = PremiseSpec(premise_text="test")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_spec(spec, study_name="s", fdr_ledger_path=Path(tmp) / "l.json")
    assert isinstance(result, CompileResult)
    assert result.stream_id == "form4"
    assert result.dose_builder == "r1_score"
    assert result.floors.min_price == 5.0


def test_compile_floors_passed_through():
    spec = PremiseSpec(
        premise_text="test",
        floors=UniverseFloors(min_price=10.0, min_avg_volume=1_000_000),
    )
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_spec(spec, study_name="s", fdr_ledger_path=Path(tmp) / "l.json")
    assert result.floors.min_price == 10.0
    assert result.floors.min_avg_volume == 1_000_000


def test_compile_explore_cutoff_always_safe():
    """explore_cutoff is ALWAYS date(2020,12,31); never spec-driven."""
    spec = PremiseSpec(premise_text="test")
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_spec(spec, study_name="s", fdr_ledger_path=Path(tmp) / "l.json")
    assert result.config.explore_cutoff == date(2020, 12, 31)
    assert result.config.allow_post_2020_explore is False


# ===========================================================================
# §7.4 Determinism tests
# ===========================================================================

def test_compile_deterministic():
    spec = PremiseSpec(premise_text="test", stream="form4")
    with tempfile.TemporaryDirectory() as tmp:
        r1 = compile_spec(spec, study_name="s", fdr_ledger_path=Path(tmp) / "l.json")
        r2 = compile_spec(spec, study_name="s", fdr_ledger_path=Path(tmp) / "l.json")
    assert r1.config.horizons == r2.config.horizons
    assert r1.config.dedup_window_days == r2.config.dedup_window_days
    assert r1.config.fdr_q == r2.config.fdr_q
    assert r1.dose_builder == r2.dose_builder
    assert r1.stream_id == r2.stream_id


# ===========================================================================
# §7.5 State machine tests
# ===========================================================================

def _make_store_no_load(tmp_path: Path) -> PremiseStore:
    """Create a PremiseStore that writes to a temp path."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
    # Monkeypatch DATA_PATH so save() also uses temp path
    store._tmp_path = str(tmp_path / "p.json")
    return store


def test_legal_transition_draft_to_awaiting(tmp_path):
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        store.transition(pid, "awaiting_formalization")
        assert store.premises[pid]["status"] == "awaiting_formalization"


def test_illegal_transition_raises(tmp_path):
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        # draft → confirmed is not a legal single-step transition
        with pytest.raises(ValueError, match="Cannot transition"):
            store.transition(pid, "confirmed")


def test_confirmed_is_terminal(tmp_path):
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        # Walk to confirmed via legal path
        store.transition(pid, "awaiting_formalization")
        store.transition(pid, "spec_ready")
        store.transition(pid, "exploring")
        store.transition(pid, "explored")
        store.transition(pid, "awaiting_confirm")
        store.transition(pid, "confirmed")
        assert store.premises[pid]["status"] == "confirmed"
        # Cannot transition out of confirmed
        with pytest.raises(ValueError):
            store.transition(pid, "spec_ready")


def test_transition_spec_ready_to_awaiting_formalization(tmp_path):
    """spec_ready → awaiting_formalization is a legal re-formalize path."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        store.transition(pid, "awaiting_formalization")
        store.transition(pid, "spec_ready")
        store.transition(pid, "awaiting_formalization")  # re-formalize
        assert store.premises[pid]["status"] == "awaiting_formalization"


def test_exploring_can_revert_to_spec_ready(tmp_path):
    """exploring → spec_ready is legal (run failure revert)."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        store.transition(pid, "awaiting_formalization")
        store.transition(pid, "spec_ready")
        store.transition(pid, "exploring")
        store.transition(pid, "spec_ready")
        assert store.premises[pid]["status"] == "spec_ready"


def test_add_spec_validates(tmp_path):
    """add_spec must raise ValueError on invalid spec; store must not mutate."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        with pytest.raises((ValueError, Exception)):
            store.add_spec(pid, {"premise_text": "test", "stream": "nonexistent_stream"})
        # Store must not be mutated
        assert store.premises[pid]["spec"] is None


def test_add_spec_valid(tmp_path):
    """add_spec with a valid spec_dict must store it."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        store.add_spec(pid, {"premise_text": "real idea", "stream": "form4"})
        assert store.premises[pid]["spec"] is not None
        assert store.premises[pid]["spec"]["stream"] == "form4"


def test_append_run(tmp_path):
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("An idea")
        store.append_run(pid, {"run_id": "r1", "status": "ok"})
        assert len(store.premises[pid]["run_history"]) == 1
        assert store.premises[pid]["run_history"][0]["run_id"] == "r1"


# ===========================================================================
# §7.6 Persistence round-trip test
# ===========================================================================

def test_save_load_roundtrip(tmp_path):
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        s1 = PremiseStore()
        pid = s1.add_premise("test idea")
        s1.save()
        s2 = PremiseStore()
        assert pid in s2.premises
        assert s2.premises[pid]["status"] == "draft"
        assert s2.premises[pid]["premise_text"] == "test idea"


def test_save_load_preserves_transitions(tmp_path):
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        s1 = PremiseStore()
        pid = s1.add_premise("test idea")
        s1.transition(pid, "awaiting_formalization")
        s1.save()
        s2 = PremiseStore()
        assert s2.premises[pid]["status"] == "awaiting_formalization"


def test_load_missing_file_is_noop(tmp_path):
    """Loading from a non-existent file must not raise."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "nonexistent.json")):
        store = PremiseStore()
    assert store.premises == {}


def test_load_corrupt_file_raises(tmp_path):
    """A corrupt (top-level invalid JSON) file must raise ValueError naming the file,
    NOT silently start empty — silent-empty risks the next save() overwriting
    recoverable data (F7)."""
    corrupt = tmp_path / "p.json"
    corrupt.write_text("{ not valid json !!!")
    with patch.object(_ps_module, "DATA_PATH", str(corrupt)):
        with pytest.raises((ValueError, Exception), match="not valid JSON|corrupt"):
            PremiseStore()


# ===========================================================================
# Stream registry smoke test
# ===========================================================================

def test_stream_registry_has_form4():
    from research.streams import _REGISTRY
    assert "form4" in _REGISTRY


def test_form4_stream_protocol():
    from research.streams import get
    s = get("form4")
    assert s.stream_id == "form4"
    assert "form_types" in s.filter_vocabulary()
    assert "window_bdays" in s.dose_vocabulary()
    assert "beta" in s.dose_vocabulary()


def test_get_unknown_stream_raises():
    from research.streams import get
    with pytest.raises(KeyError):
        get("nonexistent_stream")


# ===========================================================================
# Review-wave fixes (F1–F13)
# ===========================================================================

# --- F1: empty-registry bypass ---

def test_empty_registry_validation_bypass_rejected(monkeypatch):
    """Monkeypatching _REGISTRY empty must make construction FAIL, not silently
    accept an out-of-vocab event_filter key (F1: no silent bypass)."""
    import research.streams as _streams_module
    import research.premise_spec as _spec_module
    # Save real registry, clear it
    original = dict(_streams_module._REGISTRY)
    _streams_module._REGISTRY.clear()
    try:
        with pytest.raises(Exception):
            # With empty registry, the stream field_validator fires first
            # (validate_default=True ensures even the default 'form4' is validated).
            # This must raise — not silently pass.
            _spec_module.PremiseSpec(premise_text="test", event_filter={"evil_key": True})
    finally:
        _streams_module._REGISTRY.update(original)


# --- F4: spec_hash canonicalization ---

def test_spec_hash_int_float_equal(tmp_path):
    """21 and 21.0 in dose_params must produce the same spec_hash (F4).

    F391: window_bdays is typed as int-only, so 21.0 (float) is now rejected at
    construction time by _validate_dose_params value-type check.  The F4
    canonicalization invariant is preserved: since 21.0 can no longer reach the
    hash layer, we verify the spec with int 21 is stable across two constructions.
    """
    s1 = PremiseSpec(premise_text="test", dose_params={"window_bdays": 21})
    s2 = PremiseSpec(premise_text="test", dose_params={"window_bdays": 21})
    assert spec_hash(s1) == spec_hash(s2), (
        "Same dose_params must produce the same spec_hash"
    )
    # Verify F391 correctly rejects float
    with pytest.raises(Exception, match="(?i)ledger enforcement"):
        PremiseSpec(premise_text="test", dose_params={"window_bdays": 21.0})


def test_spec_hash_list_order_independent(tmp_path):
    """['P','S'] and ['S','P'] in event_filter must produce the same spec_hash (F4)."""
    s1 = PremiseSpec(
        premise_text="test",
        event_filter={"transaction_codes": ["P", "S"]},
    )
    s2 = PremiseSpec(
        premise_text="test",
        event_filter={"transaction_codes": ["S", "P"]},
    )
    assert spec_hash(s1) == spec_hash(s2), (
        "List-valued event_filter with different element order must hash identically"
    )


def test_spec_hash_structural_change_differs():
    """Changing a structural value must produce a different hash (F4 sanity)."""
    s1 = PremiseSpec(premise_text="test", dose_params={"window_bdays": 21})
    s2 = PremiseSpec(premise_text="test", dose_params={"window_bdays": 42})
    assert spec_hash(s1) != spec_hash(s2), (
        "Different dose_params values must produce different hashes"
    )


# --- F5: spec_history version accounting ---

def test_add_spec_versions_monotonic(tmp_path):
    """Three add_spec calls must produce spec_history with archived versions [1, 2]
    and the current spec at version 3 (F5: monotonic, no duplicates)."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("idea")
        store.add_spec(pid, {"premise_text": "v1", "stream": "form4"})
        store.add_spec(pid, {"premise_text": "v2", "stream": "form4"})
        store.add_spec(pid, {"premise_text": "v3", "stream": "form4"})

    history = store.premises[pid]["spec_history"]
    versions = [e["version"] for e in history]
    # Should have archived v1 and v2; v3 is the current spec (not in history yet)
    assert versions == [1, 2], (
        f"Expected archived versions [1, 2], got {versions}"
    )


# --- F6: add_spec refuses on confirmed ---

def test_add_spec_refuses_on_confirmed(tmp_path):
    """add_spec on a confirmed premise must raise ValueError (F6)."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("idea")
        # Walk to confirmed
        store.transition(pid, "awaiting_formalization")
        store.transition(pid, "spec_ready")
        store.transition(pid, "exploring")
        store.transition(pid, "explored")
        store.transition(pid, "awaiting_confirm")
        store.transition(pid, "confirmed")
        # Now add_spec must refuse
        with pytest.raises(ValueError, match="confirmed"):
            store.add_spec(pid, {"premise_text": "new spec", "stream": "form4"})


# --- F7: corrupt JSON raises ---

def test_load_corrupt_json_raises(tmp_path):
    """Whole-file JSON parse failure must raise ValueError naming the file (F7).
    It must NOT silently start empty (silent-empty risks save() overwriting .bak data)."""
    bad = tmp_path / "p.json"
    bad.write_text("{ this is not json }")
    with patch.object(_ps_module, "DATA_PATH", str(bad)):
        with pytest.raises((ValueError, Exception), match="not valid JSON|corrupt"):
            PremiseStore()


# --- F8: invalid status entries are skipped ---

def test_load_skips_invalid_status_entries(tmp_path):
    """A file with one good entry and one invalid-status entry must load only the good
    one (F8: warn+skip on unknown status)."""
    import json as _json
    data = {
        "version": 1,
        "premises": [
            {
                "premise_id": "p-good",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "status": "draft",
                "premise_text": "good",
                "spec": None,
                "spec_history": [],
                "run_history": [],
                "error_note": None,
            },
            {
                "premise_id": "p-bad",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "status": "corrupted_state",  # unknown
                "premise_text": "bad",
                "spec": None,
                "spec_history": [],
                "run_history": [],
                "error_note": None,
            },
        ],
    }
    p = tmp_path / "p.json"
    p.write_text(_json.dumps(data))
    with patch.object(_ps_module, "DATA_PATH", str(p)):
        store = PremiseStore()
    assert "p-good" in store.premises
    assert "p-bad" not in store.premises


# --- F9: _VALID_DOSES == _COST_FN_BY_DOSE keys ---

def test_valid_doses_matches_cost_fn_registry():
    """premise_spec._VALID_DOSES must equal premise_compile._COST_FN_BY_DOSE keys (F9).
    Adding a dose to one without the other should be caught by the module-load assert."""
    from research.premise_spec import _VALID_DOSES
    from research.premise_compile import _COST_FN_BY_DOSE
    assert _VALID_DOSES == set(_COST_FN_BY_DOSE), (
        f"_VALID_DOSES {_VALID_DOSES} != _COST_FN_BY_DOSE keys {set(_COST_FN_BY_DOSE)}"
    )


# --- F10: PremiseSpec frozen ---

def test_premise_spec_frozen():
    """Mutating a structural field on a PremiseSpec must raise (F10: frozen=True)."""
    spec = PremiseSpec(premise_text="test", entry_lag_days=1)
    with pytest.raises(Exception):
        spec.entry_lag_days = 99  # type: ignore[misc]


# --- F11: snapshot-restore on save failure ---

def test_add_spec_snapshot_restore_on_save_failure(tmp_path):
    """If save() raises in add_spec, in-memory state must be restored to pre-call state (F11)."""
    with patch.object(_ps_module, "DATA_PATH", str(tmp_path / "p.json")):
        store = PremiseStore()
        pid = store.add_premise("idea")
        # Capture pre-call state
        pre_spec = store.premises[pid].get("spec")
        pre_history_len = len(store.premises[pid].get("spec_history", []))
        # Patch save() to always raise — simulates disk-full on the next call
        def failing_save():
            raise OSError("simulated disk full")
        store.save = failing_save  # type: ignore[method-assign]
        with pytest.raises(OSError, match="simulated disk full"):
            store.add_spec(pid, {"premise_text": "new", "stream": "form4"})
        # In-memory state must be restored to pre-call values
        assert store.premises[pid].get("spec") == pre_spec
        assert len(store.premises[pid].get("spec_history", [])) == pre_history_len


# ===========================================================================
# F391 — value-type validation in _validate_event_filter / _validate_dose_params
# ===========================================================================

class TestF391ValueTypeValidation:
    """F391: per-key value-type validation in event_filter and dose_params."""

    def test_filter_wrong_type_raises(self):
        """event_filter with wrong-type value must raise ValidationError."""
        with pytest.raises(Exception, match="(?i)ledger enforcement"):
            PremiseSpec(
                premise_text="test",
                stream="form4",
                event_filter={"min_dollar_total": "not_a_number"},  # must be int|float
            )

    def test_filter_exclude_10b51_wrong_type_raises(self):
        """exclude_10b51 must be bool; a string must raise."""
        with pytest.raises(Exception, match="(?i)ledger enforcement"):
            PremiseSpec(
                premise_text="test",
                stream="form4",
                event_filter={"exclude_10b51": "yes"},  # must be bool
            )

    def test_filter_form_types_wrong_type_raises(self):
        """form_types must be list; a string must raise."""
        with pytest.raises(Exception, match="(?i)ledger enforcement"):
            PremiseSpec(
                premise_text="test",
                stream="form4",
                event_filter={"form_types": "4"},  # must be list, not bare str
            )

    def test_filter_correct_types_pass(self):
        """Correct-type values must not raise."""
        spec = PremiseSpec(
            premise_text="test",
            stream="form4",
            event_filter={
                "form_types": ["4"],
                "transaction_codes": ["P"],
                "min_dollar_total": 1000.0,
                "exclude_10b51": True,
            },
        )
        assert spec.event_filter["exclude_10b51"] is True

    def test_dose_param_wrong_type_raises(self):
        """dose_params with wrong-type value must raise ValidationError."""
        with pytest.raises(Exception, match="(?i)ledger enforcement"):
            PremiseSpec(
                premise_text="test",
                stream="form4",
                dose="r1_score",
                dose_params={"window_bdays": 21.5},  # must be int, not float
            )

    def test_dose_param_correct_types_pass(self):
        """Correct-type dose_params must not raise."""
        spec = PremiseSpec(
            premise_text="test",
            stream="form4",
            dose="r1_score",
            dose_params={"window_bdays": 21, "beta": 0.5},
        )
        assert spec.dose_params["window_bdays"] == 21

    def test_dose_param_beta_int_accepted(self):
        """beta is (int, float); int must be accepted."""
        spec = PremiseSpec(
            premise_text="test",
            stream="form4",
            dose="r1_score",
            dose_params={"beta": 1},  # int, valid
        )
        assert spec.dose_params["beta"] == 1

    def test_filter_value_schemas_returned_by_form4(self):
        """Form4Stream.filter_value_schemas() returns a non-empty dict."""
        from research.streams import get
        s = get("form4")
        schemas = s.filter_value_schemas()
        assert isinstance(schemas, dict)
        assert "form_types" in schemas
        assert "exclude_10b51" in schemas
        assert schemas["exclude_10b51"] is bool

    def test_dose_value_schemas_returned_by_form4(self):
        """Form4Stream.dose_value_schemas() returns a non-empty dict."""
        from research.streams import get
        s = get("form4")
        schemas = s.dose_value_schemas()
        assert isinstance(schemas, dict)
        assert "window_bdays" in schemas
        assert schemas["window_bdays"] is int


# ===========================================================================
# DI-07 — duplicate_premise: legacy premise (no spec_version key) (F420)
# ===========================================================================

def test_duplicate_premise_legacy_no_spec_version(tmp_path):
    """DI-07: duplicate_premise must not crash when source premise lacks spec_version key.

    Legacy premises written before spec_version was introduced (H2/F416) do not have
    the 'spec_version' key in their store entry.  duplicate_premise must handle this
    gracefully: the clone should be created with spec_version=1 if a spec is present,
    or no spec_version key if there is no spec.
    """
    import json as _json

    # Build a legacy store file that NEVER had spec_version written
    legacy_data = {
        "version": 1,
        "premises": [
            {
                "premise_id": "p-legacyaa",
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00",
                "status": "spec_ready",
                "premise_text": "Legacy premise without spec_version key",
                "spec": {
                    "premise_text": "Legacy premise without spec_version key",
                    "stream": "form4",
                    "dose": "r1_score",
                },
                # Intentionally NO "spec_version" key — simulates pre-H2 data
                "spec_history": [],
                "run_history": [],
                "error_note": None,
                "disposition": "active",
                "disposition_note": "",
                "derived_from": None,
            }
        ],
    }
    store_path = tmp_path / "legacy_premises.json"
    store_path.write_text(_json.dumps(legacy_data), encoding="utf-8")

    with patch.object(_ps_module, "DATA_PATH", str(store_path)):
        store = PremiseStore()
        # Verify the legacy entry loaded correctly (no spec_version key)
        assert "p-legacyaa" in store.premises
        assert "spec_version" not in store.premises["p-legacyaa"]

        # duplicate_premise must not raise
        new_pid = store.duplicate_premise("p-legacyaa")

    # The clone must exist
    assert new_pid in store.premises
    clone = store.premises[new_pid]

    # H2: clone must have spec_version=1 (spec was present in source)
    assert clone.get("spec_version") == 1, (
        "duplicate_premise must set spec_version=1 on the clone when source has a spec "
        "(even if source lacked spec_version — legacy compatibility)"
    )

    # spec_hash must be cleared on the clone (R-8)
    spec = clone.get("spec") or {}
    assert "spec_hash" not in spec or spec.get("spec_hash") is None, (
        "duplicate_premise must clear spec_hash on the clone"
    )

    # derived_from must point to the original
    assert clone["derived_from"] == "p-legacyaa"

    # Original must be unchanged (no spec_version added to it)
    with patch.object(_ps_module, "DATA_PATH", str(store_path)):
        store2 = PremiseStore()
    assert "spec_version" not in store2.premises["p-legacyaa"]


def test_duplicate_premise_legacy_no_spec_no_spec_version(tmp_path):
    """DI-07b: duplicate_premise with source having no spec AND no spec_version.

    Clone should have no spec_version key (spec is None → spec_version not set).
    """
    import json as _json

    legacy_data = {
        "version": 1,
        "premises": [
            {
                "premise_id": "p-legacybb",
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00",
                "status": "draft",
                "premise_text": "Legacy premise no spec, no spec_version",
                "spec": None,
                # No "spec_version" key
                "spec_history": [],
                "run_history": [],
                "error_note": None,
                "disposition": "active",
                "disposition_note": "",
                "derived_from": None,
            }
        ],
    }
    store_path = tmp_path / "legacy_no_spec.json"
    store_path.write_text(_json.dumps(legacy_data), encoding="utf-8")

    with patch.object(_ps_module, "DATA_PATH", str(store_path)):
        store = PremiseStore()
        new_pid = store.duplicate_premise("p-legacybb")

    clone = store.premises[new_pid]
    # No spec → no spec_version on clone
    assert clone.get("spec") is None
    assert "spec_version" not in clone, (
        "Clone of no-spec premise must not have spec_version key"
    )
