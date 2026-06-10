"""Tests for backend/research/s1_onesample_analysis.py (F414).

Tests cover:
1. test_supported_verdict        — supported: direction correct + BH-rejected + power gate
2. test_not_supported_direction  — not supported: direction wrong (mean positive when expect negative)
3. test_not_supported_bh         — not supported: p_boot > fdr_q even with correct direction
4. test_underpowered             — underpowered: mde_1samp_pp > design_mde_pp
5. test_spec_validator_requires_mde  — one_sample spec without design_mde_pp raises
6. test_spec_validator_mde_positive  — design_mde_pp=0 raises
7. test_spec_validator_dose_response_ok — dose_response without design_mde_pp is OK
8. test_structural_hash_differs  — analysis_form in _STRUCTURAL_FIELDS: hashes differ
9. test_ledger_entry_keys        — ledger entry has expected keys
10. test_63td_comparability_present — 63td comparability row always in verdict
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Add backend to sys.path
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from research.s1_onesample_analysis import (
    run_s1_onesample_analysis,
    _build_s1_ledger_entry,
)
from research.premise_spec import PremiseSpec, spec_hash


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_meta(
    horizons: list[int],
    per_horizon: dict,
    study_name: str = "test_s1",
) -> dict:
    """Build a minimal meta.json dict that run_s1_onesample_analysis reads."""
    return {
        "study_name": study_name,
        "schema_version": 2,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "horizons": horizons,
        "n_events": 20,
        "n_explore": 20,
        "n_confirm": 0,
        "per_horizon": per_horizon,
        "era_consistency": {},
        "regime_breakdown": {},
        "survivorship": {"n_events": 20, "no_price_data": 0},
    }


def _write_meta_study(tmp_path: Path, meta: dict, study_name: str = "test_s1") -> Path:
    """Write a meta.json-only study_dir (s1_onesample_analysis only needs meta.json)."""
    study_dir = tmp_path / study_name
    study_dir.mkdir(parents=True, exist_ok=True)
    (study_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Also write empty events.ndjson for completeness
    (study_dir / "events.ndjson").write_text("", encoding="utf-8")
    return study_dir


def _make_per_horizon_entry(
    mean_excess_pct: float,
    std_excess_pct: float,
    p_bootstrap: float,
    p_nw: float,
    mde_ppt: float,
    n: int = 20,
    peer_median_excess_pct: Optional[float] = None,
) -> dict:
    """Build a single per_horizon stats block."""
    return {
        "mean_excess_pct": mean_excess_pct,
        "std_excess_pct": std_excess_pct,
        "p_bootstrap": p_bootstrap,
        "p_nw": p_nw,
        "mde_ppt": mde_ppt,
        "n_explore_valid": n,
        "peer_median_excess_pct": peer_median_excess_pct,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestS1OnesampleVerdicts:
    """Core verdict logic tests."""

    def test_supported_verdict(self, tmp_path: Path) -> None:
        """ADVANCE: direction negative (short), BH-rejected, power gate passes."""
        # Harness emits MARKET-convention excess regardless of spec.direction
        # (its _forward_return direction param is hardcoded "long" at every call
        # site — event_study.py:1636, 2155), so a short thesis is supported by
        # NEGATIVE mean_excess.
        # mde_ppt=5.0pp < design_mde_pp=8.0pp (power gate passes)
        # p_boot=0.001 <= fdr_q=0.10 (BH-rejected)
        per_h = {
            10: _make_per_horizon_entry(mean_excess_pct=-3.0, std_excess_pct=5.0,
                                        p_bootstrap=0.20, p_nw=0.22, mde_ppt=4.0),
            21: _make_per_horizon_entry(mean_excess_pct=-7.0, std_excess_pct=5.5,
                                        p_bootstrap=0.03, p_nw=0.04, mde_ppt=4.5),
            30: _make_per_horizon_entry(mean_excess_pct=-11.74, std_excess_pct=7.0,
                                        p_bootstrap=0.001, p_nw=0.002, mde_ppt=5.0),
            63: _make_per_horizon_entry(mean_excess_pct=-22.0, std_excess_pct=15.0,
                                        p_bootstrap=0.001, p_nw=0.001, mde_ppt=10.5),
        }
        meta = _make_meta([10, 21, 30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_supported")

        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(10, 21, 30),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )

        assert result["explore_decision"] == "ADVANCE"
        h_key = "H_mean_excess_30d"
        assert result[h_key]["mean_excess_pct"] == pytest.approx(-11.74)
        assert result[h_key]["bh_rejected"] is True
        assert result["power_gate_passed"] is True
        # Verdict file should exist
        assert (study_dir / "s1_onesample_verdict.json").exists()
        # 63td comparability present
        assert result["comparability_63td"] is not None
        assert result["comparability_63td"]["mean_excess_pct"] == pytest.approx(-22.0)

    def test_not_supported_direction(self, tmp_path: Path) -> None:
        """NOT-SUPPORTED: direction wrong — mean_excess POSITIVE when direction='short'.

        Market-convention excess: positive = the stocks OUTperformed, i.e. the
        short/underperformance thesis is wrong. → NOT-SUPPORTED.
        """
        per_h = {
            30: _make_per_horizon_entry(mean_excess_pct=5.0, std_excess_pct=6.0,
                                        p_bootstrap=0.02, p_nw=0.03, mde_ppt=5.0),
            63: _make_per_horizon_entry(mean_excess_pct=10.0, std_excess_pct=10.0,
                                        p_bootstrap=0.02, p_nw=0.03, mde_ppt=8.0),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_wrong_direction")

        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )

        assert result["explore_decision"] == "NOT-SUPPORTED"
        assert result["explore_decision_rationale"]["direction_correct"] is False

    def test_not_supported_bh(self, tmp_path: Path) -> None:
        """NOT-SUPPORTED: BH not rejected even with correct direction and power."""
        per_h = {
            30: _make_per_horizon_entry(mean_excess_pct=-5.0, std_excess_pct=10.0,
                                        p_bootstrap=0.25, p_nw=0.30, mde_ppt=5.0),
            63: _make_per_horizon_entry(mean_excess_pct=-8.0, std_excess_pct=12.0,
                                        p_bootstrap=0.15, p_nw=0.18, mde_ppt=7.0),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_bh_not_rejected")

        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )

        assert result["explore_decision"] == "NOT-SUPPORTED"
        assert result["explore_decision_rationale"]["bh_rejected"] is False
        assert result["explore_decision_rationale"]["direction_correct"] is True

    def test_underpowered(self, tmp_path: Path) -> None:
        """UNTESTABLE-underpowered: mde_ppt > design_mde_pp."""
        per_h = {
            30: _make_per_horizon_entry(mean_excess_pct=11.74, std_excess_pct=25.0,
                                        p_bootstrap=0.001, p_nw=0.001, mde_ppt=9.5,
                                        n=5),
            63: _make_per_horizon_entry(mean_excess_pct=15.0, std_excess_pct=30.0,
                                        p_bootstrap=0.001, p_nw=0.001, mde_ppt=12.0),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_underpowered")

        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,  # mde_ppt=9.5 > 8.0 → underpowered
            fdr_q=0.10,
        )

        assert result["explore_decision"] == "UNTESTABLE-underpowered"
        assert result["power_gate_passed"] is False

    def test_fdr_family_of_one(self, tmp_path: Path) -> None:
        """BH with family of 1: p_raw <= fdr_q is both necessary and sufficient."""
        per_h = {
            30: _make_per_horizon_entry(mean_excess_pct=5.0, std_excess_pct=5.0,
                                        p_bootstrap=0.10, p_nw=0.12, mde_ppt=4.0),
            63: _make_per_horizon_entry(mean_excess_pct=8.0, std_excess_pct=7.0,
                                        p_bootstrap=0.08, p_nw=0.09, mde_ppt=5.5),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_fdr_boundary")

        # p_boot=0.10 exactly at fdr_q=0.10 → should be BH-rejected (<=)
        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )
        assert result["H_mean_excess_30d"]["bh_rejected"] is True  # 0.10 <= 0.10

        # p_boot=0.101 > fdr_q=0.10 → not rejected
        per_h2 = {
            30: _make_per_horizon_entry(mean_excess_pct=5.0, std_excess_pct=5.0,
                                        p_bootstrap=0.101, p_nw=0.12, mde_ppt=4.0),
            63: _make_per_horizon_entry(mean_excess_pct=8.0, std_excess_pct=7.0,
                                        p_bootstrap=0.10, p_nw=0.09, mde_ppt=5.5),
        }
        meta2 = _make_meta([30, 63], per_h2)
        study_dir2 = _write_meta_study(tmp_path, meta2, "s1_fdr_boundary2")
        result2 = run_s1_onesample_analysis(
            study_dir=study_dir2,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )
        assert result2["H_mean_excess_30d"]["bh_rejected"] is False  # 0.101 > 0.10


class TestS1SpecValidator:
    """PremiseSpec validator tests for analysis_form and design_mde_pp."""

    def test_one_sample_requires_design_mde(self) -> None:
        """one_sample without design_mde_pp raises ValidationError."""
        with pytest.raises(Exception, match="design_mde_pp"):
            PremiseSpec(premise_text="t", analysis_form="one_sample")

    def test_one_sample_mde_must_be_positive(self) -> None:
        """design_mde_pp=0 raises ValidationError."""
        with pytest.raises(Exception, match="must be > 0"):
            PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=0.0)

    def test_one_sample_negative_mde_raises(self) -> None:
        """Negative design_mde_pp raises ValidationError."""
        with pytest.raises(Exception):
            PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=-1.0)

    def test_dose_response_no_mde_ok(self) -> None:
        """dose_response (default) without design_mde_pp is valid."""
        s = PremiseSpec(premise_text="t")
        assert s.analysis_form == "dose_response"
        assert s.design_mde_pp is None

    def test_dose_response_with_mde_ok(self) -> None:
        """dose_response with design_mde_pp is valid (optional)."""
        s = PremiseSpec(premise_text="t", analysis_form="dose_response", design_mde_pp=5.0)
        assert s.design_mde_pp == pytest.approx(5.0)

    def test_one_sample_valid(self) -> None:
        """one_sample with positive design_mde_pp is valid."""
        s = PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=8.0)
        assert s.analysis_form == "one_sample"
        assert s.design_mde_pp == pytest.approx(8.0)


class TestS1StructuralHash:
    """analysis_form and design_mde_pp are structural — changing them changes the hash."""

    def test_hash_differs_dose_response_vs_one_sample(self) -> None:
        """Two specs with same params but different analysis_form have different hashes."""
        s_dr = PremiseSpec(premise_text="t", analysis_form="dose_response")
        s_os = PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=8.0)
        h_dr = spec_hash(s_dr)
        h_os = spec_hash(s_os)
        assert h_dr != h_os, f"Expected different hashes, got {h_dr!r} == {h_os!r}"

    def test_hash_differs_different_design_mde(self) -> None:
        """Two one_sample specs with different design_mde_pp have different hashes."""
        s1 = PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=6.0)
        s2 = PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=8.0)
        assert spec_hash(s1) != spec_hash(s2)

    def test_hash_stable_for_same_spec(self) -> None:
        """Same spec produced twice gives same hash."""
        s = PremiseSpec(premise_text="t", analysis_form="one_sample", design_mde_pp=8.0)
        assert spec_hash(s) == spec_hash(s)

    def test_prose_does_not_change_hash(self) -> None:
        """Changing premise_text or plain_summary does not change hash."""
        s1 = PremiseSpec(premise_text="original text", analysis_form="one_sample", design_mde_pp=8.0)
        s2 = PremiseSpec(premise_text="reworded text", analysis_form="one_sample", design_mde_pp=8.0,
                         plain_summary="different summary")
        assert spec_hash(s1) == spec_hash(s2)


class TestS1LedgerEntry:
    """Ledger entry builder produces expected structure."""

    def test_ledger_entry_keys(self, tmp_path: Path) -> None:
        """Ledger entry has all required keys and correct study_name suffix."""
        per_h = {
            30: _make_per_horizon_entry(mean_excess_pct=11.74, std_excess_pct=7.0,
                                        p_bootstrap=0.001, p_nw=0.001, mde_ppt=5.0),
            63: _make_per_horizon_entry(mean_excess_pct=20.0, std_excess_pct=12.0,
                                        p_bootstrap=0.001, p_nw=0.001, mde_ppt=8.0),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_ledger_test")

        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )

        entry = _build_s1_ledger_entry(
            result=result,
            study_name="s1_ledger_test",
            cfg_hash=result["config_hash"],
            primary_horizon=30,
            all_horizons=(30, 63),
            spec_horizons=(30,),
        )

        # Required keys
        for k in ("study_name", "analysis_form", "created_at", "study_config_hash",
                  "fdr_q", "primary_horizon", "all_horizons", "design_mde_pp",
                  "per_test", "bh_rejection_set", "explore_decision", "mde_1samp_pp"):
            assert k in entry, f"Missing key {k!r} in ledger entry"

        # Study name suffix
        assert entry["study_name"].endswith("_s1_onesample_family")

        # analysis_form recorded
        assert entry["analysis_form"] == "one_sample"

        # Per-test key
        assert "H_mean_excess_30d" in entry["per_test"]
        test_entry = entry["per_test"]["H_mean_excess_30d"]
        for k in ("p_boot", "p_nw", "n", "mean_excess_pct", "mde_ppt"):
            assert k in test_entry, f"Missing per_test key {k!r}"

        # spec_horizons recorded when different from all_horizons
        assert "spec_horizons" in entry

    def test_ledger_spec_horizons_always_present(self, tmp_path: Path) -> None:
        """F416: spec_horizons always present in ledger entry (even when equal to all_horizons)."""
        per_h = {
            30: _make_per_horizon_entry(5.0, 5.0, 0.05, 0.06, 4.0),
            63: _make_per_horizon_entry(8.0, 7.0, 0.03, 0.04, 5.5),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_ledger_test2")
        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30, 63),
            direction="short",
            design_mde_pp=8.0,
            fdr_q=0.10,
        )
        # Case 1: spec_horizons == all_horizons — still recorded (F416 convention)
        entry_same = _build_s1_ledger_entry(
            result=result,
            study_name="s1_ledger_test2",
            cfg_hash=result["config_hash"],
            primary_horizon=30,
            all_horizons=(30, 63),
            spec_horizons=(30, 63),  # same as all_horizons
        )
        assert "spec_horizons" in entry_same, "spec_horizons must be present even when equal to all_horizons"
        assert entry_same["spec_horizons"] == [30, 63]

        # Case 2: spec_horizons=None — recorded as None
        entry_none = _build_s1_ledger_entry(
            result=result,
            study_name="s1_ledger_test2",
            cfg_hash=result["config_hash"],
            primary_horizon=30,
            all_horizons=(30, 63),
            spec_horizons=None,
        )
        assert "spec_horizons" in entry_none, "spec_horizons key must be present even when None"
        assert entry_none["spec_horizons"] is None


class TestS1Misc:
    """Miscellaneous tests."""

    def test_63td_comparability_present_when_in_meta(self, tmp_path: Path) -> None:
        """comparability_63td populated when 63 is in meta per_horizon."""
        per_h = {
            30: _make_per_horizon_entry(11.74, 7.0, 0.001, 0.001, 5.0),
            63: _make_per_horizon_entry(22.86, 15.0, 0.001, 0.001, 10.0),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_63td")
        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
        )
        assert result["comparability_63td"] is not None
        assert result["comparability_63td"]["mean_excess_pct"] == pytest.approx(22.86)
        note = result["comparability_63td"]["note"]
        assert "Report-only" in note
        assert "Never a bar" in note

    def test_63td_comparability_absent_when_not_in_meta(self, tmp_path: Path) -> None:
        """comparability_63td is None when 63 not in meta per_horizon."""
        per_h = {
            30: _make_per_horizon_entry(11.74, 7.0, 0.001, 0.001, 5.0),
        }
        meta = _make_meta([30], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_no63")
        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
        )
        assert result["comparability_63td"] is None

    def test_verdict_file_written(self, tmp_path: Path) -> None:
        """s1_onesample_verdict.json written to study_dir."""
        per_h = {
            30: _make_per_horizon_entry(5.0, 5.0, 0.05, 0.06, 4.0),
            63: _make_per_horizon_entry(8.0, 7.0, 0.03, 0.04, 5.5),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_file_test")
        run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
        )
        verdict_path = study_dir / "s1_onesample_verdict.json"
        assert verdict_path.exists()
        with open(verdict_path, encoding="utf-8") as f:
            written = json.load(f)
        assert written["analysis_version"] == "s1_onesample_analysis_v1"
        assert written["analysis_form"] == "one_sample"

    def test_perturbation_note_present(self, tmp_path: Path) -> None:
        """perturbation_note always present in verdict."""
        per_h = {
            30: _make_per_horizon_entry(5.0, 5.0, 0.05, 0.06, 4.0),
            63: _make_per_horizon_entry(8.0, 7.0, 0.03, 0.04, 5.5),
        }
        meta = _make_meta([30, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_perturb_note")
        result = run_s1_onesample_analysis(
            study_dir=study_dir,
            primary_horizon=30,
            horizons=(30,),
            direction="short",
            design_mde_pp=8.0,
        )
        assert "perturbation_note" in result
        assert len(result["perturbation_note"]) > 0

    def test_missing_meta_raises(self, tmp_path: Path) -> None:
        """FileNotFoundError raised when meta.json is absent."""
        study_dir = tmp_path / "empty_study"
        study_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="meta.json not found"):
            run_s1_onesample_analysis(
                study_dir=study_dir,
                primary_horizon=30,
                design_mde_pp=8.0,
            )

    def test_missing_horizon_in_meta_raises(self, tmp_path: Path) -> None:
        """ValueError raised when primary horizon missing from meta per_horizon."""
        per_h = {
            21: _make_per_horizon_entry(5.0, 5.0, 0.05, 0.06, 4.0),
            63: _make_per_horizon_entry(8.0, 7.0, 0.03, 0.04, 5.5),
        }
        meta = _make_meta([21, 63], per_h)
        study_dir = _write_meta_study(tmp_path, meta, "s1_missing_horizon")
        with pytest.raises(ValueError, match="meta.json missing per_horizon"):
            run_s1_onesample_analysis(
                study_dir=study_dir,
                primary_horizon=30,  # not in meta
                horizons=(30,),
                design_mde_pp=8.0,
            )
