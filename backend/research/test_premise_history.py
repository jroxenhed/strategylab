"""Tests for F397 premise idea-history + dispositions.

Covers:
  - disposition validation (valid set / ValueError + HTTP 422)
  - duplicate_premise clones text+spec, sets derived_from, leaves original untouched
  - new premise_id is format-valid (p-XXXXXXXX)
  - machine_outcome derivation for no-run / failed / untestable / explored states
  - ?disposition= query filter (valid + 422 on junk)

Run:
    backend/venv/bin/python3 -m pytest backend/research/test_premise_history.py -x -q
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup — run from repo root or from backend/
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import research.premise_store as _ps_module  # noqa: E402
from research.premise_store import (  # noqa: E402
    PremiseStore,
    _VALID_DISPOSITIONS,
    derive_machine_outcome,
)


# ===========================================================================
# Helpers
# ===========================================================================

def make_store(tmp_path: Path) -> PremiseStore:
    """Create a fresh PremiseStore backed by a temp file."""
    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    return PremiseStore()


def _run(coro):
    """Run a coroutine synchronously (Python 3.9+ compatible)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ===========================================================================
# § Disposition validation
# ===========================================================================

def test_valid_dispositions_accepted(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("test idea")
    for d in _VALID_DISPOSITIONS:
        store.set_disposition(pid, d)
        p = store._get(pid)
        assert p["disposition"] == d


def test_invalid_disposition_raises(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("test idea")
    with pytest.raises(ValueError, match="Unknown disposition"):
        store.set_disposition(pid, "super_bullish")


def test_disposition_note_saved(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("test idea")
    store.set_disposition(pid, "rejected", note="not enough events")
    p = store._get(pid)
    assert p["disposition_note"] == "not enough events"


def test_disposition_note_capped_at_2000(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("test idea")
    long_note = "x" * 3000
    store.set_disposition(pid, "active", note=long_note)
    p = store._get(pid)
    assert len(p["disposition_note"]) == 2000


def test_disposition_allowed_in_any_state(tmp_path):
    """Disposition is metadata — can be set regardless of status."""
    store = make_store(tmp_path)
    pid = store.add_premise("test idea")
    # In draft state
    store.set_disposition(pid, "promising")
    assert store._get(pid)["disposition"] == "promising"
    # Advance to awaiting_formalization
    store.transition(pid, "awaiting_formalization")
    store.set_disposition(pid, "parked_needs_data")
    assert store._get(pid)["disposition"] == "parked_needs_data"


# ===========================================================================
# § duplicate_premise
# ===========================================================================

def test_duplicate_clones_text(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("original idea text")
    new_pid = store.duplicate_premise(pid)
    new_p = store._get(new_pid)
    assert new_p["premise_text"] == "original idea text"


def test_duplicate_sets_derived_from(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("original")
    new_pid = store.duplicate_premise(pid)
    assert store._get(new_pid)["derived_from"] == pid


def test_duplicate_original_untouched(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("original")
    store.duplicate_premise(pid)
    # Original should not have derived_from set
    original = store._get(pid)
    assert original.get("derived_from") is None


def test_duplicate_new_id_format_valid(tmp_path):
    import re
    store = make_store(tmp_path)
    pid = store.add_premise("original")
    new_pid = store.duplicate_premise(pid)
    assert re.match(r"^p-[0-9a-f]{8}$", new_pid), f"Bad format: {new_pid!r}"


def test_duplicate_new_premise_starts_as_draft(tmp_path):
    store = make_store(tmp_path)
    pid = store.add_premise("original")
    new_pid = store.duplicate_premise(pid)
    assert store._get(new_pid)["status"] == "draft"


def test_duplicate_clones_spec(tmp_path):
    from research.premise_spec import PremiseSpec
    store = make_store(tmp_path)
    pid = store.add_premise("original with spec")
    spec = PremiseSpec(premise_text="original with spec", stream="form4")
    store.add_spec(pid, spec.model_dump())
    new_pid = store.duplicate_premise(pid)
    new_spec = store._get(new_pid).get("spec")
    assert new_spec is not None
    assert new_spec["stream"] == "form4"


def test_duplicate_missing_premise_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.duplicate_premise("p-00000000")


# ===========================================================================
# § derive_machine_outcome
# ===========================================================================

def test_machine_outcome_no_runs():
    p = {"run_history": []}
    assert derive_machine_outcome(p) == "—"


def test_machine_outcome_missing_run_history():
    p = {}
    assert derive_machine_outcome(p) == "—"


def test_machine_outcome_failed_run():
    p = {
        "run_history": [
            {
                "run_type": "explore",
                "verdict": None,
                "error": "timeout after 300s",
                "status": "failed",
            }
        ]
    }
    outcome = derive_machine_outcome(p)
    assert outcome.startswith("failed:")
    assert "timeout" in outcome


def test_machine_outcome_failed_status_field():
    p = {
        "run_history": [
            {
                "run_type": "explore",
                "verdict": {"explore_decision": None, "n_valid_events": 0},
                "error": "bad data",
            }
        ]
    }
    outcome = derive_machine_outcome(p)
    assert outcome.startswith("failed:")


def test_machine_outcome_untestable():
    p = {
        "run_history": [
            {
                "run_type": "explore",
                "verdict": {
                    "explore_decision": "UNTESTABLE — insufficient events",
                    "n_valid_events": 3,
                },
                "error": None,
            }
        ]
    }
    outcome = derive_machine_outcome(p)
    assert "UNTESTABLE" in outcome
    assert "3" in outcome


def test_machine_outcome_explored_with_mde():
    p = {
        "run_history": [
            {
                "run_type": "explore",
                "verdict": {
                    "explore_decision": "EXPLORE",
                    "n_valid_events": 120,
                    "mde_q5q1_pp": 4.56,
                },
                "error": None,
            }
        ]
    }
    outcome = derive_machine_outcome(p)
    assert "EXPLORE" in outcome
    assert "4.56" in outcome


def test_machine_outcome_explored_fallback_to_n_events():
    """When mde_q5q1_pp is absent, fall back to n_valid_events."""
    p = {
        "run_history": [
            {
                "run_type": "explore",
                "verdict": {
                    "explore_decision": "EXPLORE",
                    "n_valid_events": 88,
                },
                "error": None,
            }
        ]
    }
    outcome = derive_machine_outcome(p)
    assert "EXPLORE" in outcome
    assert "88" in outcome


def test_machine_outcome_uses_latest_run():
    """Multiple runs — outcome should reflect the latest."""
    p = {
        "run_history": [
            {
                "run_type": "explore",
                "verdict": {"explore_decision": "OLD_DECISION", "n_valid_events": 5},
                "error": None,
            },
            {
                "run_type": "explore",
                "verdict": {"explore_decision": "NEW_DECISION", "n_valid_events": 50},
                "error": None,
            },
        ]
    }
    outcome = derive_machine_outcome(p)
    assert "NEW_DECISION" in outcome
    assert "OLD_DECISION" not in outcome


# ===========================================================================
# § ?disposition= query filter (HTTP layer)
# ===========================================================================

def test_disposition_filter_http_valid(tmp_path):
    """Valid disposition filter returns 200."""
    import importlib
    import fastapi.testclient

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    # Pre-create the file so the store loads cleanly
    store = PremiseStore()
    pid1 = store.add_premise("promising idea")
    store.set_disposition(pid1, "promising")
    pid2 = store.add_premise("active idea")
    # pid2 stays "active" (default)

    import main as main_module
    client = fastapi.testclient.TestClient(main_module.app)

    resp = client.get("/api/premises?disposition=promising")
    assert resp.status_code == 200
    data = resp.json()
    ids = [item["premise_id"] for item in data]
    assert pid1 in ids
    assert pid2 not in ids


def test_disposition_filter_http_junk_422(tmp_path):
    """Unknown disposition value returns 422."""
    import fastapi.testclient
    import main as main_module

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")

    client = fastapi.testclient.TestClient(main_module.app)
    resp = client.get("/api/premises?disposition=junk_value")
    assert resp.status_code == 422


def test_disposition_put_endpoint_valid(tmp_path):
    """PUT /api/premises/{id}/disposition sets disposition."""
    import fastapi.testclient
    import main as main_module

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    store = PremiseStore()
    pid = store.add_premise("test idea")

    client = fastapi.testclient.TestClient(main_module.app)
    resp = client.put(f"/api/premises/{pid}/disposition", json={"disposition": "rejected", "note": "no events"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["disposition"] == "rejected"

    # Re-load store and confirm persistence
    store2 = PremiseStore()
    p = store2._get(pid)
    assert p["disposition"] == "rejected"
    assert p["disposition_note"] == "no events"


def test_disposition_put_endpoint_invalid_422(tmp_path):
    """PUT /api/premises/{id}/disposition with junk disposition returns 422."""
    import fastapi.testclient
    import main as main_module

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    store = PremiseStore()
    pid = store.add_premise("test idea")

    client = fastapi.testclient.TestClient(main_module.app)
    resp = client.put(f"/api/premises/{pid}/disposition", json={"disposition": "not_a_real_disposition"})
    assert resp.status_code == 422


def test_duplicate_post_endpoint(tmp_path):
    """POST /api/premises/{id}/duplicate creates a new premise."""
    import fastapi.testclient
    import main as main_module

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    store = PremiseStore()
    pid = store.add_premise("original idea")

    client = fastapi.testclient.TestClient(main_module.app)
    resp = client.post(f"/api/premises/{pid}/duplicate")
    assert resp.status_code == 201
    data = resp.json()
    assert "premise_id" in data
    assert data["derived_from"] == pid
    assert data["premise_id"] != pid

    # Verify original untouched
    store2 = PremiseStore()
    original = store2._get(pid)
    assert original.get("derived_from") is None


def test_lazy_defaults_on_load(tmp_path):
    """Existing premises without disposition fields get defaults on load."""
    data_path = str(tmp_path / "premises.json")
    _ps_module.DATA_PATH = data_path

    # Write a premise dict WITHOUT disposition fields (simulates old data)
    legacy = {
        "version": 1,
        "premises": [
            {
                "premise_id": "p-aabbccdd",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "status": "draft",
                "premise_text": "legacy premise",
                "spec": None,
                "spec_history": [],
                "run_history": [],
                "error_note": None,
            }
        ],
    }
    Path(data_path).write_text(json.dumps(legacy))

    store = PremiseStore()
    p = store._get("p-aabbccdd")
    assert p.get("disposition") == "active"
    assert p.get("disposition_note") == ""
    assert p.get("derived_from") is None


# ===========================================================================
# § H1: note max-length enforced at HTTP boundary (422 before the store)
# ===========================================================================

def test_disposition_note_over_2000_returns_422(tmp_path):
    """PUT /api/premises/{id}/disposition with a >2000-char note → 422 (Pydantic Field)."""
    import fastapi.testclient
    import main as main_module

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    store = PremiseStore()
    pid = store.add_premise("test idea for note truncation")

    client = fastapi.testclient.TestClient(main_module.app)
    long_note = "z" * 2001
    resp = client.put(
        f"/api/premises/{pid}/disposition",
        json={"disposition": "active", "note": long_note},
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


def test_disposition_put_echoes_saved_note(tmp_path):
    """PUT /api/premises/{id}/disposition response includes the saved note (H1)."""
    import fastapi.testclient
    import main as main_module

    _ps_module.DATA_PATH = str(tmp_path / "premises.json")
    store = PremiseStore()
    pid = store.add_premise("test idea")

    client = fastapi.testclient.TestClient(main_module.app)
    resp = client.put(
        f"/api/premises/{pid}/disposition",
        json={"disposition": "parked_sharpen", "note": "sharpen the entry filter"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "note" in data
    assert data["note"] == "sharpen the entry filter"


# ===========================================================================
# § H2: duplicate_premise spec_version reset → clean v1 history on first add_spec
# ===========================================================================

def test_duplicate_spec_version_reset_clean_history(tmp_path):
    """Clone of a versioned premise starts at spec_version=0; first add_spec yields
    a clean single-entry spec_history (no phantom archived entry)."""
    from research.premise_spec import PremiseSpec

    store = make_store(tmp_path)
    # Build original with spec_version >= 1 (two add_spec calls)
    pid = store.add_premise("original with versioned spec")
    spec_v1 = PremiseSpec(premise_text="original with versioned spec", stream="form4")
    store.add_spec(pid, spec_v1.model_dump())
    # Advance to spec_ready (required to allow a second add_spec)
    store.transition(pid, "awaiting_formalization")
    store.transition(pid, "spec_ready")
    spec_v2 = PremiseSpec(premise_text="original with versioned spec", stream="form4", entry_lag_days=2)
    store.add_spec(pid, spec_v2.model_dump())

    # Confirm original has spec_version >= 1
    original = store._get(pid)
    assert original.get("spec_version", 0) >= 1

    # Duplicate
    clone_pid = store.duplicate_premise(pid)
    clone = store._get(clone_pid)

    # Clone spec_version must be 0 (reset)
    assert clone.get("spec_version") == 0, (
        f"Expected spec_version=0 on clone, got {clone.get('spec_version')}"
    )
    # Clone spec_history must be empty (no phantom archived entry)
    assert clone.get("spec_history") == [], (
        f"Expected empty spec_history on fresh clone, got {clone.get('spec_history')}"
    )

    # First add_spec on clone should produce spec_version=1 with empty spec_history
    # (nothing archived yet — only the current spec is set)
    new_spec = PremiseSpec(premise_text="original with versioned spec", stream="form4", entry_lag_days=3)
    store.add_spec(clone_pid, new_spec.model_dump())
    clone_after = store._get(clone_pid)
    assert clone_after.get("spec_version") == 1, (
        f"Expected spec_version=1 after first add_spec on clone, got {clone_after.get('spec_version')}"
    )
    # spec_history should still be empty — nothing was archived (v0 → v1 is the first set)
    assert clone_after.get("spec_history") == [], (
        f"Expected empty spec_history after first add_spec on clone, got {clone_after.get('spec_history')}"
    )


# ===========================================================================
# § H3: duplicate_premise spec mutation isolation
# ===========================================================================

def test_duplicate_spec_mutation_isolation(tmp_path):
    """Mutating the clone's nested spec fields must NOT affect the original (deepcopy)."""
    from research.premise_spec import PremiseSpec

    store = make_store(tmp_path)
    pid = store.add_premise("original idea")
    spec = PremiseSpec(premise_text="original idea", stream="form4", horizons=[21, 63])
    store.add_spec(pid, spec.model_dump())

    clone_pid = store.duplicate_premise(pid)

    # Retrieve clone spec and mutate nested fields in-memory via the store's live dict
    clone_entry = store._get(clone_pid)
    # Mutate a scalar key on the top-level spec dict
    clone_entry["spec"]["stream"] = "MUTATED"
    # Mutate a nested dict field (floors sub-dict)
    clone_entry["spec"]["floors"]["min_price"] = 999.0

    # Original spec must be unchanged (deepcopy isolation)
    original_entry = store._get(pid)
    assert original_entry["spec"]["stream"] == "form4", (
        "stream mutation on clone leaked into original"
    )
    assert original_entry["spec"]["floors"]["min_price"] != 999.0, (
        "floors mutation on clone leaked into original"
    )


# ===========================================================================
# § DI-8: duplicate_premise snapshot/rollback restores pre-call state on save failure
# ===========================================================================

def test_duplicate_rollback_on_save_failure(tmp_path, monkeypatch):
    """If save() raises during duplicate_premise, the new premise_id is NOT in self.premises."""
    store = make_store(tmp_path)
    pid = store.add_premise("original idea")

    def _raise(*_a, **_kw):
        raise OSError("disk full")

    # Patch atomic_write_text in the premise_store module namespace (it was
    # imported directly via 'from fileutil import atomic_write_text').
    monkeypatch.setattr(_ps_module, "atomic_write_text", _raise)

    with pytest.raises(OSError, match="disk full"):
        store.duplicate_premise(pid)

    # Only the original premise should remain
    assert list(store.premises.keys()) == [pid], (
        f"Expected only [{pid!r}], got {list(store.premises.keys())}"
    )
