"""Tests for backend/research/premise_autopsy.py (F417).

Fixture-driven: tests use minimal verdict dicts matching real verdict shapes.
Real-data sanity test guards on skipif-not-exists.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from research.premise_autopsy import (  # noqa: E402
    AutopsyResult,
    DescendantSuggestion,
    _CIRCULARITY_CAVEAT,
    build_autopsy,
)
from research.premise_census import CensusResult, HorizonCensus  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _r1_untestable_not_evaluable() -> dict:
    """r1 verdict: UNTESTABLE — power not evaluable (Q5 arm n=1)."""
    return {
        "study_name": "premise_p-test_explore_000",
        "primary_horizon": 30,
        "analysis_version": "r1_analysis_v1",
        "n_valid_events": 18,
        "n_score_undefined": 2,
        "mde_q5q1_pp": None,
        "mde_gate_passed": False,
        "mde_not_evaluable": True,
        "H1": {
            "obs_gap_q5q1_pp": 0.37,
            "n_q5": 1,
            "n_q1": 5,
            "bh_rejected": False,
            "boot_degenerate": True,
            "p_boot": 1.0,
        },
        "H1b": {"rho_s": None},
        "explore_decision": "UNTESTABLE — power not evaluable",
        "explore_decision_rationale": {
            "gap_positive": True,
            "rho_positive": True,
            "band_sign_stable": False,
            "mde_gate_passed": False,
            "mde_not_evaluable": True,
            "mde_abort_threshold_pp": 1.0,
        },
    }


def _r1_untestable_underpowered() -> dict:
    """r1 verdict: UNTESTABLE — dose MDE gate fails (mde > 1.0pp)."""
    return {
        "study_name": "premise_p-test2_explore_001",
        "primary_horizon": 30,
        "analysis_version": "r1_analysis_v1",
        "n_valid_events": 20,
        "n_score_undefined": 0,
        "mde_q5q1_pp": 3.5,
        "mde_gate_passed": False,
        "mde_not_evaluable": False,
        "H1": {
            "obs_gap_q5q1_pp": 0.8,
            "n_q5": 4,
            "n_q1": 4,
            "bh_rejected": False,
            "boot_degenerate": False,
            "p_boot": 0.8,
        },
        "H1b": {"rho_s": 0.1},
        "explore_decision": "UNTESTABLE-underpowered",
        "explore_decision_rationale": {
            "gap_positive": True,
            "rho_positive": True,
            "band_sign_stable": False,
            "mde_gate_passed": False,
            "mde_not_evaluable": False,
            "mde_abort_threshold_pp": 1.0,
        },
    }


def _s1_advance_verdict() -> dict:
    """s1 verdict: ADVANCE — all gates passed."""
    return {
        "study_name": "premise_p-advance_explore_002",
        "primary_horizon": 30,
        "analysis_version": "s1_onesample_v1",
        "n_valid_events": 20,
        "mde_1samp_pp": 6.59,
        "design_mde_pp": 8.0,
        "power_gate_passed": True,
        "H_mean_excess_30d": {
            "mean_excess_pct": -11.74,
            "p_bootstrap": 0.02,
            "bh_rejected": True,
        },
        "explore_decision": "ADVANCE",
        "explore_decision_rationale": {
            "direction_correct": True,
            "bh_rejected": True,
            "power_gate_passed": True,
            "note": "direction correct, BH rejected, power gate passed",
        },
    }


def _s1_not_supported_negative_mean() -> dict:
    """s1 verdict: NOT-SUPPORTED — mean is negative, spec direction = long."""
    return {
        "study_name": "premise_p-notsup_explore_003",
        "primary_horizon": 30,
        "analysis_version": "s1_onesample_v1",
        "n_valid_events": 20,
        "mde_1samp_pp": 6.59,
        "design_mde_pp": 8.0,
        "power_gate_passed": True,
        "H_mean_excess_30d": {
            "mean_excess_pct": -8.5,  # negative while direction=long → opposite sign
            "p_bootstrap": 0.4,
            "bh_rejected": False,
        },
        "explore_decision": "NOT-SUPPORTED",
        "explore_decision_rationale": {
            "direction_correct": False,
            "bh_rejected": False,
            "power_gate_passed": True,
            "note": "direction incorrect",
        },
    }


def _long_spec() -> dict:
    return {"analysis_form": "one_sample", "direction": "long", "horizons": [30], "design_mde_pp": 8.0}


def _dose_spec() -> dict:
    return {"analysis_form": "dose_response", "direction": "long", "horizons": [30], "design_mde_pp": None}


def _dummy_study_dir(tmp_path: Path, verdict: dict) -> Path:
    """Write a minimal events.ndjson + verdict file to tmp_path."""
    # Write 20 dummy events
    events = []
    for i in range(20):
        events.append({
            "floor_status": "ok",
            "split": "explore",
            "payload": {"score": float(i) / 19.0},
            "fwd_excess_pct": {"30": -11.74, "63": -20.0},
        })
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "events.ndjson").write_text("\n".join(json.dumps(e) for e in events))
    return study_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDoseUntestableSuggestsOneSample:
    """r1 verdict UNTESTABLE — power not evaluable → one_sample reformulation suggested."""

    def test_suggestion_present(self, tmp_path):
        verdict = _r1_untestable_not_evaluable()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert len(result.suggestions) == 1
        s = result.suggestions[0]
        assert "one_sample" in s.rationale.lower()
        assert s.spec_overrides.get("analysis_form") == "one_sample"
        assert "design_mde_pp" in s.spec_overrides

    def test_circularity_caveat_in_suggestion(self, tmp_path):
        verdict = _r1_untestable_not_evaluable()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert len(result.suggestions) == 1
        assert "circularity" in result.suggestions[0].caveat.lower()
        assert result.suggestions[0].caveat == _CIRCULARITY_CAVEAT

    def test_predicted_block_present(self, tmp_path):
        verdict = _r1_untestable_not_evaluable()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        s = result.suggestions[0]
        assert "n_events" in s.predicted
        assert "mde_pp" in s.predicted
        assert "observed_pp" in s.predicted
        assert "powered" in s.predicted

    def test_design_mde_pp_is_null_in_overrides(self, tmp_path):
        """design_mde_pp must be None in overrides — user must set it."""
        verdict = _r1_untestable_not_evaluable()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert result.suggestions[0].spec_overrides["design_mde_pp"] is None


class TestDoseUntestableUnderpoweredMDEGate:
    """r1 verdict UNTESTABLE-underpowered (MDE gate fails) → same suggestion branch."""

    def test_suggestion_generated(self, tmp_path):
        verdict = _r1_untestable_underpowered()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test2",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert len(result.suggestions) == 1
        assert "one_sample" in result.suggestions[0].rationale.lower()

    def test_failed_gate_contains_dose_mde(self, tmp_path):
        verdict = _r1_untestable_underpowered()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test2",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert result.failed_gate is not None
        assert "dose" in result.failed_gate.lower() or "mde" in result.failed_gate.lower()


class TestAdvanceNoSuggestions:
    """s1 verdict ADVANCE → suggestions=[] (no descendants needed)."""

    def test_no_suggestions_on_advance(self, tmp_path):
        verdict = _s1_advance_verdict()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-advance",
            study_dir=study_dir,
            verdict=verdict,
            spec=_long_spec(),
        )
        assert result.suggestions == []

    def test_failed_gate_is_none_on_advance(self, tmp_path):
        verdict = _s1_advance_verdict()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-advance",
            study_dir=study_dir,
            verdict=verdict,
            spec=_long_spec(),
        )
        assert result.failed_gate is None


class TestNotSupportedOppositeSuggestsInverted:
    """s1 NOT-SUPPORTED + negative mean for long spec → inverted direction suggestion."""

    def test_inverted_direction_suggested(self, tmp_path):
        verdict = _s1_not_supported_negative_mean()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-notsup",
            study_dir=study_dir,
            verdict=verdict,
            spec=_long_spec(),
        )
        assert len(result.suggestions) == 1
        s = result.suggestions[0]
        assert s.spec_overrides.get("direction") == "short"

    def test_inverted_suggestion_has_caveat(self, tmp_path):
        verdict = _s1_not_supported_negative_mean()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-notsup",
            study_dir=study_dir,
            verdict=verdict,
            spec=_long_spec(),
        )
        assert result.suggestions[0].caveat == _CIRCULARITY_CAVEAT


class TestPlainSummaryContainsCircularity:
    """Any UNTESTABLE verdict → plain_summary contains "circularity"."""

    def test_circularity_in_plain_summary(self, tmp_path):
        verdict = _r1_untestable_not_evaluable()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert "circularity" in result.plain_summary.lower()


class TestFailedGateDoseMDEGate:
    """r1 verdict mde_gate_passed=False, mde_q5q1_pp=3.5 → failed_gate contains 'dose MDE gate'."""

    def test_failed_gate_string(self, tmp_path):
        verdict = _r1_untestable_underpowered()
        study_dir = _dummy_study_dir(tmp_path, verdict)
        result = build_autopsy(
            premise_id="p-test2",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
        )
        assert result.failed_gate is not None
        assert "dose" in result.failed_gate.lower()


class TestCensusReusedFromRunHistory:
    """When cached_census dict is passed, build_autopsy uses it (no file read)."""

    def test_cached_census_used(self, tmp_path):
        verdict = _r1_untestable_not_evaluable()
        # Point study_dir at a non-existent path — if census is recomputed from disk it will fail
        study_dir = tmp_path / "nonexistent_study"
        # Build a cached census dict matching the contract shape
        cached = {
            "n_valid": 20,
            "n_score_defined": 18,
            "primary_horizon": 30,
            "analysis_form": "dose_response",
            "testable_1samp": None,
            "testable_gap": False,
            "design_mde_pp": None,
            "note": "Dose gap underpowered",
            "horizons": {
                "30": {
                    "horizon_td": 30,
                    "mean_excess_pp": -11.74,
                    "std_excess_pp": 10.52,
                    "mde_1samp_pp": 6.59,
                    "mde_gap_pp": 17.5,
                    "arm_size": 3,
                }
            },
        }
        # Should NOT raise FileNotFoundError even though study_dir doesn't exist
        result = build_autopsy(
            premise_id="p-cached",
            study_dir=study_dir,
            verdict=verdict,
            spec=_dose_spec(),
            cached_census=cached,
        )
        assert result.census is not None
        assert result.census.n_valid == 20
        assert result.census.n_score_defined == 18


# ---------------------------------------------------------------------------
# Real-data sanity test
# ---------------------------------------------------------------------------

_REAL_STUDY = (
    _BACKEND / "data" / "turnaround" / "event_studies"
    / "premise_p-1569aa97_explore_1781050404"
)

@pytest.mark.skipif(
    not (_REAL_STUDY / "events.ndjson").exists() or
    not (_REAL_STUDY / "r1_explore_verdict.json").exists(),
    reason="Real study dir not present on this machine",
)
class TestRealDataAutopsy:
    """Sanity-check autopsy on the committed p-1569aa97 study dir."""

    def test_real_autopsy_decision(self):
        verdict = json.loads((_REAL_STUDY / "r1_explore_verdict.json").read_text())
        spec = {
            "analysis_form": "dose_response",
            "direction": "short",
            "horizons": [30, 63],
            "design_mde_pp": None,
        }
        result = build_autopsy(
            premise_id="p-1569aa97",
            study_dir=_REAL_STUDY,
            verdict=verdict,
            spec=spec,
        )
        assert result.explore_decision == "UNTESTABLE — power not evaluable"
        # Should suggest one_sample reformulation
        assert len(result.suggestions) == 1
        assert result.suggestions[0].spec_overrides.get("analysis_form") == "one_sample"
        # Census anchors reproduced
        assert result.census is not None
        assert result.census.n_valid == 20
        ph30 = result.census.horizons.get(30)
        assert ph30 is not None
        assert ph30.mde_1samp_pp is not None
        assert abs(ph30.mde_1samp_pp - 6.591) <= 0.05
        assert ph30.mde_gap_pp is not None
        assert abs(ph30.mde_gap_pp - 17.5) <= 1.0
