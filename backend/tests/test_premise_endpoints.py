"""Tests for F417/F394 premise endpoints (F-BATCH-0610C IMPL-B scope).

Covers:
  GET  /api/premises/{id}/autopsy
  POST /api/premises/{id}/derive
  POST /api/premises/{id}/reset-stuck-run
  F398 DispositionRequest.disposition Literal validation

Uses FastAPI TestClient with the real app.  Since routes/premises.py uses lazy
imports (PremiseStore imported inside function bodies), we patch the canonical
module path "research.premise_store.PremiseStore".
"""
from __future__ import annotations

import json
import sys
import typing
import unittest.mock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from main import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_PREMISE_ID = "p-aabbccdd"
_SPEC = {
    "analysis_form": "dose_response",
    "direction": "long",
    "horizons": [30],
    "n_boot": 99,
    "fdr_q": 0.05,
    "design_mde_pp": None,
    "dose_builder": "r1_score",
    "event_filter": {},
    "dose_params": {},
    "floors": {},
}
_EXPLORE_VERDICT = {
    "study_name": "premise_p-aabbccdd_explore_123",
    "primary_horizon": 30,
    "explore_decision": "UNTESTABLE — power not evaluable",
    "mde_q5q1_pp": None,
    "mde_gate_passed": False,
    "mde_not_evaluable": True,
    "H1": {"n_q5": 1, "n_q1": 5, "obs_gap_q5q1_pp": 0.3, "bh_rejected": False, "p_boot": 1.0},
    "H1b": {"rho_s": None},
}


def _make_premise(status="explored", has_explore_run=False, output_dir=None) -> dict:
    premise: dict = {
        "premise_id": _PREMISE_ID,
        "status": status,
        "premise_text": "Insider buying predicts outperformance.",
        "spec": dict(_SPEC),
        "run_history": [],
        "error_note": None,
        "disposition": "active",
        "disposition_note": "",
        "derived_from": None,
    }
    if has_explore_run and output_dir:
        premise["run_history"] = [
            {
                "run_type": "explore",
                "verdict_valid": True,
                "output_dir": str(output_dir),
                "verdict": dict(_EXPLORE_VERDICT),
                "census": None,
                "study_name": "premise_p-aabbccdd_explore_123",
            }
        ]
    return premise


def _make_store(premise_data: dict) -> unittest.mock.MagicMock:
    store = unittest.mock.MagicMock()
    store._get.return_value = premise_data
    store.premises = {_PREMISE_ID: premise_data}
    return store


# ---------------------------------------------------------------------------
# GET /api/premises/{id}/autopsy
# ---------------------------------------------------------------------------

class TestGetAutopsy:
    """F417: autopsy endpoint — 404 when no valid explore run, 200 with shape."""

    def test_404_when_no_valid_explore_run(self, tmp_path):
        """Premise exists but has no valid explore run → 404."""
        premise = _make_premise(status="spec_ready", has_explore_run=False)
        store = _make_store(premise)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.get(f"/api/premises/{_PREMISE_ID}/autopsy")
        assert resp.status_code == 404
        assert "explore" in resp.json()["detail"].lower()

    def test_200_returns_autopsy_shape(self, tmp_path):
        """Valid explore run → 200 with required contract fields."""
        # SEC-01: output_dir must be under _STUDIES_DIR; patch it to tmp_path
        studies_dir = tmp_path / "event_studies"
        studies_dir.mkdir()
        events_dir = studies_dir / "study"
        events_dir.mkdir()
        events = [
            {
                "floor_status": "ok",
                "split": "explore",
                "payload": {"score": float(i) / 9},
                "fwd_excess_pct": {"30": 2.0 if i % 2 == 0 else -2.0},
            }
            for i in range(10)
        ]
        (events_dir / "events.ndjson").write_text("\n".join(json.dumps(e) for e in events))

        premise = _make_premise(
            status="explored",
            has_explore_run=True,
            output_dir=events_dir,
        )
        store = _make_store(premise)

        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store), \
             unittest.mock.patch("research.premise_run._STUDIES_DIR", studies_dir):
            resp = client.get(f"/api/premises/{_PREMISE_ID}/autopsy")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        for field in ("premise_id", "study_name", "explore_decision", "analysis_form",
                      "failed_gate", "failed_gate_detail", "suggestions", "plain_summary"):
            assert field in body, f"Missing field: {field}"
        assert body["premise_id"] == _PREMISE_ID
        assert body["explore_decision"] == _EXPLORE_VERDICT["explore_decision"]

    def test_200_census_field_present(self, tmp_path):
        """census key is present in autopsy response (may be null)."""
        # SEC-01: output_dir must be under _STUDIES_DIR; patch it to tmp_path
        studies_dir = tmp_path / "event_studies"
        studies_dir.mkdir()
        events_dir = studies_dir / "study2"
        events_dir.mkdir()
        (events_dir / "events.ndjson").write_text(
            "\n".join(json.dumps({
                "floor_status": "ok", "split": "explore",
                "payload": {"score": 0.5},
                "fwd_excess_pct": {"30": 1.0},
            }) for _ in range(5))
        )
        premise = _make_premise(
            status="explored",
            has_explore_run=True,
            output_dir=events_dir,
        )
        store = _make_store(premise)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store), \
             unittest.mock.patch("research.premise_run._STUDIES_DIR", studies_dir):
            resp = client.get(f"/api/premises/{_PREMISE_ID}/autopsy")
        assert resp.status_code == 200
        assert "census" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/premises/{id}/derive
# ---------------------------------------------------------------------------

class TestDerivePremise:
    """F417: derive endpoint — 404 on missing source, 201 with circularity caveat."""

    def test_201_no_overrides(self):
        """derive with empty overrides → 201 with premise_id and derived_from."""
        new_id = "p-12345678"
        source_premise = _make_premise(status="explored")
        derived_premise = {
            **_make_premise(status="draft"),
            "premise_id": new_id,
            "premise_text": source_premise["premise_text"],
        }

        def _get_side_effect(pid):
            if pid == _PREMISE_ID:
                return source_premise
            elif pid == new_id:
                return derived_premise
            raise KeyError(pid)

        store = _make_store(source_premise)
        store._get.side_effect = _get_side_effect
        store.duplicate_premise.return_value = new_id
        store.save.return_value = None
        store.transition.return_value = None

        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.post(f"/api/premises/{_PREMISE_ID}/derive", json={"spec_overrides": {}})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "premise_id" in body
        assert body["derived_from"] == _PREMISE_ID

    def test_circularity_caveat_appended(self):
        """Circularity caveat is AUTO-APPENDED to premise_text server-side."""
        new_id = "p-12345678"
        source_premise = _make_premise(status="explored")
        derived_premise = {
            **_make_premise(status="draft"),
            "premise_id": new_id,
            "premise_text": "Original text.",
        }
        captured = {}

        def _get_side_effect(pid):
            if pid == _PREMISE_ID:
                return source_premise
            elif pid == new_id:
                return derived_premise
            raise KeyError(pid)

        def _save_side_effect():
            captured["text"] = derived_premise.get("premise_text", "")

        store = _make_store(source_premise)
        store._get.side_effect = _get_side_effect
        store.duplicate_premise.return_value = new_id
        store.save.side_effect = _save_side_effect
        store.transition.return_value = None

        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            client.post(f"/api/premises/{_PREMISE_ID}/derive", json={"spec_overrides": {}})

        assert "CIRCULARITY CAVEAT" in captured.get("text", ""), (
            f"Circularity caveat not in premise_text: {captured.get('text', '')!r}"
        )

    def test_404_when_source_not_found(self):
        store = unittest.mock.MagicMock()
        store._get.side_effect = KeyError(_PREMISE_ID)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.post(f"/api/premises/{_PREMISE_ID}/derive", json={"spec_overrides": {}})
        assert resp.status_code == 404

    def test_422_on_invalid_spec_overrides(self):
        """spec_overrides that fail PremiseSpec validation → 422."""
        new_id = "p-12345678"
        source_premise = _make_premise(status="explored")
        derived_premise = {
            **_make_premise(status="draft"),
            "premise_id": new_id,
            "premise_text": "Original.",
            "spec": dict(_SPEC),
        }

        def _get_side_effect(pid):
            if pid == _PREMISE_ID:
                return source_premise
            elif pid == new_id:
                return derived_premise
            raise KeyError(pid)

        store = _make_store(source_premise)
        store._get.side_effect = _get_side_effect
        store.duplicate_premise.return_value = new_id
        store.save.return_value = None

        # Pass an invalid analysis_form that PremiseSpec will reject
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.post(
                f"/api/premises/{_PREMISE_ID}/derive",
                json={"spec_overrides": {"analysis_form": "invalid_form_xyz"}},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/premises/{id}/reset-stuck-run (F394)
# ---------------------------------------------------------------------------

class TestResetStuckRun:
    """F394: reset-stuck-run endpoint — state guard and success path."""

    def test_404_when_not_found(self):
        store = unittest.mock.MagicMock()
        store._get.side_effect = KeyError(_PREMISE_ID)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.post(f"/api/premises/{_PREMISE_ID}/reset-stuck-run")
        assert resp.status_code == 404

    def test_409_when_not_exploring(self):
        """Status is not 'exploring' → 409."""
        premise = _make_premise(status="spec_ready")
        store = _make_store(premise)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.post(f"/api/premises/{_PREMISE_ID}/reset-stuck-run")
        assert resp.status_code == 409
        assert "exploring" in resp.json()["detail"].lower()

    def test_409_when_active_job(self):
        """Active in-memory job → 409 (refuse reset to avoid clobbering a live run)."""
        import research.premise_run as pr
        premise = _make_premise(status="exploring")
        store = _make_store(premise)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store), \
             unittest.mock.patch.object(pr, "_jobs", {_PREMISE_ID: {"status": "running"}}):
            resp = client.post(f"/api/premises/{_PREMISE_ID}/reset-stuck-run")
        assert resp.status_code == 409

    def test_200_resets_to_spec_ready(self):
        """No active job + exploring status → 200 spec_ready."""
        import research.premise_run as pr
        premise = _make_premise(status="exploring")
        store = _make_store(premise)
        store.set_error_note.return_value = None
        store.transition.return_value = None

        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store), \
             unittest.mock.patch.object(pr, "_jobs", {}):
            resp = client.post(f"/api/premises/{_PREMISE_ID}/reset-stuck-run")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "spec_ready"
        assert body["premise_id"] == _PREMISE_ID
        store.transition.assert_called_once_with(_PREMISE_ID, "spec_ready")


# ---------------------------------------------------------------------------
# F398: DispositionRequest.disposition is a static Literal type alias
# ---------------------------------------------------------------------------

class TestDispositionRequestLiteral:
    """F398: static Literal on disposition field — enforces valid values."""

    def test_disposition_field_is_literal_type(self):
        """DispositionRequest.disposition annotation must be Literal[...] not str."""
        from routes.premises import DispositionRequest
        hints = typing.get_type_hints(DispositionRequest)
        disposition_type = hints.get("disposition")
        origin = typing.get_origin(disposition_type)
        assert origin is typing.Literal, (
            f"DispositionRequest.disposition should be Literal[...], got {disposition_type}"
        )
        args = set(typing.get_args(disposition_type))
        expected = {"active", "parked_needs_data", "parked_sharpen", "rejected", "promising"}
        assert args == expected, f"Literal args mismatch: {args} != {expected}"

    def test_invalid_disposition_returns_422(self):
        """Pydantic rejects unknown disposition at deserialization → 422."""
        premise = _make_premise(status="explored")
        store = _make_store(premise)
        with unittest.mock.patch("research.premise_store.PremiseStore", return_value=store):
            resp = client.put(
                f"/api/premises/{_PREMISE_ID}/disposition",
                json={"disposition": "not_a_real_disposition", "note": ""},
            )
        assert resp.status_code == 422
