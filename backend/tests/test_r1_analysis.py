"""Tests for backend/research/r1_analysis.py (Agent B, R-1 explore build).

Fixtures use synthetic study_dir payloads (tmp_path + hand-constructed events.ndjson + meta.json).
No real data is read in tests — synthetic fixtures test the statistical and decision logic.

Test inventory:
1. test_monotone_dose_advance — higher score → higher excess → ADVANCE decision
2. test_antimonotone_dose_weakened — reversed ladder → WEAKENED-IN-EXPLORE
3. test_underpowered_untestable — tiny n/huge σ → UNTESTABLE-underpowered
4. test_spearman_exact_p — perfectly monotone 5 quintiles → rho_s=1.0, p=1/120
5. test_perturbation_sign_flip — band_sign_stable=False when perturb flip
6. test_ledger_append_idempotence — ledger grows by one entry per call, not truncated
7. test_bit_reproducibility — two runs produce identical output dict
8. test_missing_score_excluded — rows with score=None excluded from valid_events
9. test_thin_years_quintile_assignment — years with <5 events still get quintile assignments
10. test_peer_lens_reads_peer_excess — peer lens uses fwd_peer_excess_pct not fwd_excess_pct
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Add backend to sys.path so imports work
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from research.r1_analysis import run_r1_analysis  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_event_row(
    ticker: str,
    entry_date: str,
    score: Optional[float],
    excess_63: Optional[float],
    excess_21: Optional[float] = None,
    excess_126: Optional[float] = None,
    peer_excess_63: Optional[float] = None,
    peer_excess_21: Optional[float] = None,
    peer_excess_126: Optional[float] = None,
    split: str = "explore",
    regime_state: str = "NEUTRAL",
    score_perturb: Optional[dict] = None,
    peer_sic_fallback_level: str = "3_digit",
    extra_horizons: Optional[dict] = None,  # F410: {horizon_int: excess_float_or_None}
) -> dict:
    """Build a synthetic event row matching the real events.ndjson schema.

    extra_horizons: optional dict of additional {horizon: excess} entries to populate
    in fwd_excess_pct and fwd_return_pct (e.g. {10: 1.5, 30: 3.2} for premise specs).
    Keeps backward compatibility — existing callers unaffected.
    """
    payload: dict = {
        "form_type": "4",
        "accession": f"test-{ticker}-{entry_date}",
        "filing_date": entry_date,
    }
    if score is not None:
        payload["score"] = score
    # Always include score_perturb (even if None values) — mirrors real schema
    if score_perturb is not None:
        payload["score_perturb"] = score_perturb
    else:
        # Default: all 9 keys have the same score as the primary
        if score is not None:
            payload["score_perturb"] = {
                f"W{w}_F{f}": score
                for w in (20, 21, 22)
                for f in ("0", "40k", "60k")
            }
        else:
            payload["score_perturb"] = {}

    fwd_excess: dict = {
        "21": excess_21,
        "63": excess_63,
        "126": excess_126,
    }
    fwd_return: dict = {
        "21": (excess_21 or 0.0) + 1.0,
        "63": (excess_63 or 0.0) + 2.0,
        "126": (excess_126 or 0.0) + 3.0,
    }
    universe_n: dict = {"21": 50, "63": 50, "126": 50}
    peer_n: dict = {"21": 10, "63": 10, "126": 10}
    fwd_peer_excess: dict = {
        "21": peer_excess_21,
        "63": peer_excess_63,
        "126": peer_excess_126,
    }
    # F410: populate additional horizons (e.g. 10, 30 for premise specs)
    if extra_horizons:
        for h, exc in extra_horizons.items():
            fwd_excess[str(h)] = exc
            fwd_return[str(h)] = (exc or 0.0) + 1.0
            universe_n[str(h)] = 50
            peer_n[str(h)] = 10
            fwd_peer_excess[str(h)] = exc  # mirror for peer tests

    return {
        "ticker": ticker,
        "event_ts": f"{entry_date}T16:00:00+00:00",
        "entry_date": entry_date,
        "entry_price": 100.0,
        "payload": payload,
        "split": split,
        "fwd_return_pct": fwd_return,
        "fwd_excess_pct": fwd_excess,
        "floor_status": "ok",
        "universe_n": universe_n,
        "fwd_peer_excess_pct": fwd_peer_excess,
        "peer_n": peer_n,
        "peer_sic": "3674",
        "peer_sic_fallback_level": peer_sic_fallback_level,
        "no_price_data": False,
        "is_fallback": False,
        "regime_state": regime_state,
    }


def _write_study(tmp_path: Path, rows: list[dict], study_name: str = "test_study") -> Path:
    """Write a synthetic study_dir with events.ndjson + meta.json."""
    study_dir = tmp_path / study_name
    study_dir.mkdir(parents=True, exist_ok=True)

    ndjson_lines = [json.dumps(r) for r in rows]
    (study_dir / "events.ndjson").write_text("\n".join(ndjson_lines) + "\n", encoding="utf-8")

    meta = {
        "study_name": study_name,
        "schema_version": 2,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "horizons": [21, 63, 126],
        "n_events": len(rows),
        "n_explore": len([r for r in rows if r.get("split") == "explore"]),
        "n_confirm": 0,
    }
    (study_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return study_dir


def _day_from_idx(base_year: int, idx: int) -> date:
    """Convert a sequential index to a valid date, cycling through months."""
    from datetime import timedelta
    return date(base_year, 1, 1) + timedelta(days=idx * 3)  # every 3 days, avoids weekend-run issues


def _make_monotone_rows(n_per_quintile: int = 8) -> list[dict]:
    """Build 5 quintiles × n_per_quintile events with monotone dose-response.

    Q1 score=0.1 → excess≈-5pp; Q5 score=5.0 → excess≈+15pp, smooth ladder.
    All in 2018 (one year) so within-year quintile assignment is straightforward.
    """
    rows = []
    # 5 groups with clearly separated scores and excesses
    # Scores: Q1=0.1, Q2=0.5, Q3=1.0, Q4=2.0, Q5=5.0
    # Excesses: Q1=-5, Q2=0, Q3=5, Q4=10, Q5=15 (positive slope)
    groups = [
        (0.10, -5.0),
        (0.50,  0.0),
        (1.00,  5.0),
        (2.00, 10.0),
        (5.00, 15.0),
    ]
    idx = 0
    for q_grp, (score_val, excess_val) in enumerate(groups):
        for j in range(n_per_quintile):
            entry_dt = _day_from_idx(2018, idx)
            idx += 1
            ticker = f"TICK{q_grp}{j:02d}"
            rows.append(_make_event_row(
                ticker=ticker,
                entry_date=entry_dt.isoformat(),
                score=score_val + j * 0.001,  # tiny jitter so ties don't complicate things
                excess_63=excess_val + np.random.default_rng(42 + idx).normal(0, 0.1),
                excess_21=excess_val * 0.5,
                excess_126=excess_val * 1.5,
                peer_excess_63=excess_val * 0.8,
                peer_excess_21=excess_val * 0.4,
                peer_excess_126=excess_val * 1.2,
                regime_state="NEUTRAL",
            ))
    return rows


def _make_antimonotone_rows(n_per_quintile: int = 8) -> list[dict]:
    """Reversed ladder: higher score → lower excess."""
    rows = []
    groups = [
        (0.10,  15.0),  # Q1 (lowest score) has highest excess — reversed
        (0.50,  10.0),
        (1.00,   5.0),
        (2.00,   0.0),
        (5.00,  -5.0),  # Q5 (highest score) has lowest excess
    ]
    idx = 0
    for q_grp, (score_val, excess_val) in enumerate(groups):
        for j in range(n_per_quintile):
            entry_dt = _day_from_idx(2018, idx)
            idx += 1
            ticker = f"ATICK{q_grp}{j:02d}"
            rows.append(_make_event_row(
                ticker=ticker,
                entry_date=entry_dt.isoformat(),
                score=score_val + j * 0.001,
                excess_63=excess_val + np.random.default_rng(99 + idx).normal(0, 0.1),
                excess_21=excess_val * 0.5,
                excess_126=excess_val * 1.5,
                peer_excess_63=excess_val * 0.8,
                peer_excess_21=excess_val * 0.4,
                peer_excess_126=excess_val * 1.2,
            ))
    return rows


def _make_underpowered_rows(n_total: int = 6) -> list[dict]:
    """Very few events + huge variance → MDE >> 1.0pp → UNTESTABLE-underpowered."""
    rows = []
    scores = [0.1, 0.5, 1.0, 2.0, 5.0, 3.0][:n_total]
    excesses = [-200.0, -100.0, 0.0, 100.0, 200.0, 150.0][:n_total]
    for i, (score_val, excess_val) in enumerate(zip(scores, excesses)):
        entry_dt = _day_from_idx(2018, i * 10)
        rows.append(_make_event_row(
            ticker=f"UTICK{i:02d}",
            entry_date=entry_dt.isoformat(),
            score=score_val,
            excess_63=excess_val,
            excess_21=excess_val * 0.5,
            excess_126=excess_val * 1.5,
            peer_excess_63=excess_val * 0.8,
            peer_excess_21=excess_val * 0.4,
            peer_excess_126=excess_val * 1.2,
        ))
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMonotoneDoseAdvance:
    """Monotone dose fixture: higher score → higher excess → should ADVANCE."""

    def test_decision_is_advance(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "mono_study")
        ledger_path = tmp_path / "ledger.json"
        result = run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)

        assert result["explore_decision"] == "ADVANCE", (
            f"Expected ADVANCE but got {result['explore_decision']}; "
            f"gap={result['H1']['obs_gap_q5q1_pp']}, rho_s={result['H1b']['rho_s']}, "
            f"band_stable={result['perturbation_band']['band_sign_stable']}, "
            f"mde_ok={result['mde_gate_passed']}"
        )

    def test_positive_gap(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "mono_study2")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["H1"]["obs_gap_q5q1_pp"] > 0, "Q5-Q1 gap must be positive for monotone fixture"

    def test_rho_s_positive(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "mono_study3")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["H1b"]["rho_s"] is not None
        assert result["H1b"]["rho_s"] > 0, "Spearman ρ_s must be positive for monotone fixture"

    def test_verdict_json_written(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "mono_study4")
        run_r1_analysis(study_dir, seed=20260606)
        verdict_path = study_dir / "r1_explore_verdict.json"
        assert verdict_path.exists(), "r1_explore_verdict.json must be written"
        verdict = json.loads(verdict_path.read_text())
        assert verdict["explore_decision"] == "ADVANCE"

    def test_n_valid_events(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "mono_study5")
        result = run_r1_analysis(study_dir, seed=20260606)
        # 5 quintiles × 8 events = 40 valid
        assert result["n_valid_events"] == 40


class TestAntimonotonicWeakened:
    """Reversed ladder → WEAKENED-IN-EXPLORE."""

    def test_decision_is_weakened(self, tmp_path):
        rows = _make_antimonotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "anti_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["explore_decision"] == "WEAKENED-IN-EXPLORE", (
            f"Expected WEAKENED-IN-EXPLORE but got {result['explore_decision']}; "
            f"gap={result['H1']['obs_gap_q5q1_pp']}, rho_s={result['H1b']['rho_s']}"
        )

    def test_negative_gap(self, tmp_path):
        rows = _make_antimonotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "anti_study2")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["H1"]["obs_gap_q5q1_pp"] < 0

    def test_negative_rho_s(self, tmp_path):
        rows = _make_antimonotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "anti_study3")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["H1b"]["rho_s"] is not None
        assert result["H1b"]["rho_s"] < 0


class TestUnderpoweredUntestable:
    """Tiny n + huge variance → MDE not evaluable or >> 1.0pp → UNTESTABLE."""

    def test_decision_is_untestable(self, tmp_path):
        rows = _make_underpowered_rows(n_total=6)
        study_dir = _write_study(tmp_path, rows, "under_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        # ADV-03: n_total=6 with 5 quintiles → some quintiles get n=1 → mde_not_evaluable
        # or the MDE is above threshold — either way decision must be UNTESTABLE
        assert "UNTESTABLE" in result["explore_decision"], (
            f"Expected UNTESTABLE (any variant) but got {result['explore_decision']!r}; "
            f"mde={result['mde_q5q1_pp']}, n={result['n_valid_events']}, "
            f"mde_not_evaluable={result.get('mde_not_evaluable')}"
        )

    def test_mde_gate_false_or_not_evaluable(self, tmp_path):
        """With n_total=6 and 5 quintiles, either MDE>1.0pp or MDE not evaluable."""
        rows = _make_underpowered_rows(n_total=6)
        study_dir = _write_study(tmp_path, rows, "under_study2")
        result = run_r1_analysis(study_dir, seed=20260606)
        mde_gate_ok = result["mde_gate_passed"]
        mde_not_eval = result.get("mde_not_evaluable", False)
        # At least one of these must be True to produce UNTESTABLE
        assert (not mde_gate_ok) or mde_not_eval, (
            "With 6 events / 5 quintiles, MDE gate should fail or MDE should be non-evaluable"
        )

    def test_mde_gate_false(self, tmp_path):
        rows = _make_underpowered_rows(n_total=6)
        study_dir = _write_study(tmp_path, rows, "under_study3")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["mde_gate_passed"] is False


class TestSpearmanExactPermutation:
    """Perfectly monotone 5 quintile means → rho_s = 1.0, p = 1/120."""

    def test_exact_p(self, tmp_path):
        """Perfectly monotone fixture: rho_s=1.0, p_exact=1/120."""
        rows = _make_monotone_rows(n_per_quintile=20)
        study_dir = _write_study(tmp_path, rows, "spearman_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        rho = result["H1b"]["rho_s"]
        p = result["H1b"]["p_exact_onesided"]
        assert rho is not None
        assert rho > 0.99, f"ρ_s should be ~1.0 for perfectly monotone fixture, got {rho}"
        # Exact permutation: for perfectly monotone, only 1/120 permutations have ρ >= 1.0
        # The result is rounded to 6 decimal places, so allow rounding tolerance.
        assert abs(p - 1 / 120) < 1e-5, (
            f"Exact p for perfectly monotone should be ~1/120={1/120:.8f}, got {p}"
        )

    def test_spearman_uses_five_points(self, tmp_path):
        """H1b is computed over n=5 quintile means, not individual events."""
        rows = _make_monotone_rows(n_per_quintile=10)
        study_dir = _write_study(tmp_path, rows, "spearman5_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        # The p_exact_onesided can only take values k/120 for k=0..120
        # (result is rounded to 6 decimal places, so tolerance accounts for rounding)
        p = result["H1b"]["p_exact_onesided"]
        # Check it's close to a valid fraction of 120 (rounding tolerance: 0.5/120 ~ 0.004)
        p_as_frac = round(p * 120)
        assert abs(p - p_as_frac / 120) < 1e-4, (
            f"p={p} is not close to a fraction of 1/120 (exact permutation over 5 points)"
        )


class TestPerturbationSignFlip:
    """band_sign_stable=False when a perturb key flips the sign."""

    def test_band_sign_unstable(self, tmp_path):
        """Rows where primary score produces positive gap, but W20_F40k flips it."""
        rows = _make_monotone_rows(n_per_quintile=8)
        # Flip W20_F40k scores so they produce reversed ordering
        for row in rows:
            score = row["payload"].get("score")
            if score is not None:
                primary_score_perturb = row["payload"]["score_perturb"].copy()
                # Invert the W20_F40k score (negates the dose-response for this variant)
                primary_score_perturb["W20_F40k"] = 10.0 - score  # inverted
                row["payload"]["score_perturb"] = primary_score_perturb

        study_dir = _write_study(tmp_path, rows, "perturb_flip_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["perturbation_band"]["band_sign_stable"] is False, (
            "Expected band_sign_stable=False when a perturb key flips sign"
        )

    def test_band_sign_stable_for_consistent_perturbations(self, tmp_path):
        """When all perturb keys produce same-sign gap, band is stable."""
        rows = _make_monotone_rows(n_per_quintile=8)
        # All rows have identical score_perturb values (same sign, same ladder)
        # Default fixture already has all keys = primary score, so should be stable
        study_dir = _write_study(tmp_path, rows, "perturb_stable_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["perturbation_band"]["band_sign_stable"] is True


class TestLedgerAppend:
    """Ledger is appended to (not truncated); idempotent entry structure."""

    def test_ledger_grows_by_one(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "ledger_study")
        ledger_path = tmp_path / "test_ledger.json"

        # First run
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        assert ledger_path.exists()
        ledger1 = json.loads(ledger_path.read_text())
        assert len(ledger1) == 1

        # Second run — ledger should grow to 2 (not be truncated to 1)
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        ledger2 = json.loads(ledger_path.read_text())
        assert len(ledger2) == 2, (
            f"Expected 2 entries after second append, got {len(ledger2)} — was ledger truncated?"
        )

    def test_ledger_entry_has_r1_family_suffix(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "suffix_study")
        ledger_path = tmp_path / "suffix_ledger.json"
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        ledger = json.loads(ledger_path.read_text())
        assert len(ledger) == 1
        assert ledger[0]["study_name"].endswith("_r1_family"), (
            f"study_name should end in '_r1_family', got {ledger[0]['study_name']!r}"
        )

    def test_ledger_contains_required_fields(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "fields_study")
        ledger_path = tmp_path / "fields_ledger.json"
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        entry = json.loads(ledger_path.read_text())[0]
        for required_key in [
            "study_name", "study_config_hash", "fdr_q", "n_boot", "horizons",
            "per_test", "per_quintile_counts", "perturbation_sign_table",
            "bh_rejection_set", "explore_decision",
            "analysis_form", "spec_horizons",  # F416: always present
        ]:
            assert required_key in entry, f"Ledger entry missing required key: {required_key}"

    def test_ledger_analysis_form_is_dose_response(self, tmp_path):
        """F416: r1-family ledger entry must have analysis_form='dose_response'."""
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "af_study")
        ledger_path = tmp_path / "af_ledger.json"
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        entry = json.loads(ledger_path.read_text())[0]
        assert entry["analysis_form"] == "dose_response", (
            f"Expected analysis_form='dose_response', got {entry.get('analysis_form')!r}"
        )

    def test_ledger_spec_horizons_always_present(self, tmp_path):
        """F416: spec_horizons must be present in ledger entry (even when None).

        The default path (no spec context, spec_horizons=None) records None explicitly —
        the key must still be present so downstream audit code can iterate over it
        without a KeyError.
        """
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "sh_study")
        ledger_path = tmp_path / "sh_ledger.json"
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        entry = json.loads(ledger_path.read_text())[0]
        # Key must be present; value is None when no spec_horizons passed
        assert "spec_horizons" in entry, "spec_horizons key missing from ledger entry"
        # When spec_horizons is explicitly passed, it should be recorded as a list
        from research.r1_analysis import _build_r1_ledger_entry
        result = run_r1_analysis(study_dir, seed=20260606)
        dummy_entry = _build_r1_ledger_entry(
            result, "test", "deadbeef",
            primary_horizon=63, all_horizons=(21, 63, 126),
            spec_horizons=(21, 63),
        )
        assert dummy_entry["spec_horizons"] == [21, 63], (
            f"spec_horizons not recorded correctly, got {dummy_entry['spec_horizons']!r}"
        )

    def test_existing_ledger_not_truncated(self, tmp_path):
        """Pre-existing non-r1 entry in ledger is preserved."""
        ledger_path = tmp_path / "existing_ledger.json"
        pre_existing = [{"study_name": "prior_study", "note": "existing entry"}]
        ledger_path.write_text(json.dumps(pre_existing))

        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "notruncate_study")
        run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)
        ledger = json.loads(ledger_path.read_text())
        assert len(ledger) == 2, "Pre-existing ledger entry must not be truncated"
        assert ledger[0]["study_name"] == "prior_study"


class TestBitReproducibility:
    """Two identical runs with same seed produce identical output dicts."""

    def test_identical_results(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "repro_study")

        result1 = run_r1_analysis(
            study_dir, seed=20260606,
            ledger_path=tmp_path / "repro_ledger.json"
        )
        result2 = run_r1_analysis(
            study_dir, seed=20260606,
            ledger_path=tmp_path / "repro_ledger.json"
        )

        # Compare key numeric outputs — must be bit-identical
        assert result1["H1"]["p_boot"] == result2["H1"]["p_boot"], "p_boot not reproducible"
        assert result1["H1"]["obs_gap_q5q1_pp"] == result2["H1"]["obs_gap_q5q1_pp"], "gap not reproducible"
        assert result1["H1b"]["rho_s"] == result2["H1b"]["rho_s"], "rho_s not reproducible"
        assert result1["H1b"]["p_exact_onesided"] == result2["H1b"]["p_exact_onesided"], "p_h1b not reproducible"
        assert result1["H2"]["p_boot"] == result2["H2"]["p_boot"], "p_h2 not reproducible"
        assert result1["explore_decision"] == result2["explore_decision"], "decision not reproducible"
        assert result1["mde_q5q1_pp"] == result2["mde_q5q1_pp"], "MDE not reproducible"

    def test_different_seeds_can_differ(self, tmp_path):
        """Sanity: different seeds can produce different bootstrap p-values."""
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "seed_study")
        r1 = run_r1_analysis(study_dir, seed=20260606)
        r2 = run_r1_analysis(study_dir, seed=99999)
        # Decision must be the same (monotone fixture → ADVANCE), but p-values may differ
        assert r1["explore_decision"] == r2["explore_decision"]


class TestMissingScoreExcluded:
    """Rows with score=None are excluded from valid_events."""

    def test_score_none_excluded(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        # Add 3 rows with no score
        for i in range(3):
            rows.append(_make_event_row(
                ticker=f"NOSCORE{i:02d}",
                entry_date=f"2018-09-{10 + i}",
                score=None,
                excess_63=5.0,
                excess_21=2.0,
                excess_126=8.0,
                peer_excess_63=4.0,
                peer_excess_21=1.5,
                peer_excess_126=6.0,
            ))

        study_dir = _write_study(tmp_path, rows, "noscore_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        # n_valid_events should be 40 (original monotone), not 43
        assert result["n_valid_events"] == 40
        assert result["n_score_undefined"] == 3

    def test_score_undefined_counted(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        rows.append(_make_event_row(
            ticker="UNDEF01",
            entry_date="2018-10-01",
            score=None,
            excess_63=10.0,
            excess_21=5.0,
            excess_126=15.0,
            peer_excess_63=8.0,
            peer_excess_21=4.0,
            peer_excess_126=12.0,
        ))
        study_dir = _write_study(tmp_path, rows, "undef_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["n_score_undefined"] >= 1


class TestThinYearsQuintileAssignment:
    """Years with <5 events still get quintile assignments (1 or 0 members per quintile)."""

    def test_thin_year_gets_quintiles(self, tmp_path):
        """A year with only 3 events still has quintile assignments (some quintiles empty)."""
        rows = _make_monotone_rows(n_per_quintile=8)  # base: 2018

        # Add a thin year (2019) with only 3 events
        thin_year_rows = [
            _make_event_row(
                ticker=f"THIN{i:02d}",
                entry_date=f"2019-02-{10 + i}",
                score=float(i + 1),
                excess_63=float(i * 2 - 2),
                excess_21=float(i),
                excess_126=float(i * 3 - 3),
                peer_excess_63=float(i * 1.5 - 1.5),
                peer_excess_21=float(i * 0.7),
                peer_excess_126=float(i * 2.2 - 2.2),
            )
            for i in range(3)
        ]
        rows.extend(thin_year_rows)
        study_dir = _write_study(tmp_path, rows, "thin_year_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        # Should not crash and 2019 year counts should appear
        year_counts = result["year_quintile_counts"]
        assert "2019" in year_counts, "2019 thin year should appear in year_quintile_counts"
        # 3 events → total assigned = 3
        total_2019 = sum(year_counts["2019"].values())
        assert total_2019 == 3, f"Expected 3 total assigned for thin year 2019, got {total_2019}"


class TestPeerLens:
    """Peer lens reads fwd_peer_excess_pct, not fwd_excess_pct."""

    def test_peer_gap_uses_peer_excess(self, tmp_path):
        """Construct rows where peer_excess is always positive but univ_excess may be mixed."""
        rows = []
        groups = [
            (0.10, -5.0, -4.0),   # score, univ_excess, peer_excess
            (0.50,  0.0, -1.0),
            (1.00,  5.0,  2.0),
            (2.00, 10.0,  6.0),
            (5.00, 15.0, 10.0),
        ]
        idx = 0
        for q_grp, (score_val, univ_exc, peer_exc) in enumerate(groups):
            for j in range(8):
                entry_dt = _day_from_idx(2018, idx)
                idx += 1
                rows.append(_make_event_row(
                    ticker=f"PEER{q_grp}{j:02d}",
                    entry_date=entry_dt.isoformat(),
                    score=score_val + j * 0.001,
                    excess_63=univ_exc,
                    excess_21=univ_exc * 0.5,
                    excess_126=univ_exc * 1.5,
                    peer_excess_63=peer_exc,
                    peer_excess_21=peer_exc * 0.5,
                    peer_excess_126=peer_exc * 1.5,
                ))

        study_dir = _write_study(tmp_path, rows, "peer_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        peer = result["peer_lens"]
        univ_gap = result["H1"]["obs_gap_q5q1_pp"]
        peer_gap = peer["gap_q5q1"]

        # Peer and universe gaps should be different (they are from different columns)
        assert peer_gap is not None, "peer gap should not be None"
        # Peer excess: Q5 has ~10, Q1 has ~-4 → gap ~14; univ: Q5~15, Q1~-5 → gap ~20
        # They should differ meaningfully
        assert univ_gap is not None
        assert abs(peer_gap - univ_gap) > 1.0, (
            f"Peer gap ({peer_gap:.2f}) and universe gap ({univ_gap:.2f}) should differ "
            "since peer_excess != universe_excess in this fixture"
        )

    def test_peer_fallback_rate_computed(self, tmp_path):
        """Peer fallback rate is computed (fraction with non-3digit peer_sic_fallback_level)."""
        rows = _make_monotone_rows(n_per_quintile=8)
        # Set half rows to fallback level
        for i, row in enumerate(rows):
            if i % 2 == 0:
                row["peer_sic_fallback_level"] = "2_digit"  # fallback

        study_dir = _write_study(tmp_path, rows, "fallback_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        peer = result["peer_lens"]
        assert peer["fallback_rate"] is not None
        assert peer["fallback_rate"] > 0, "Expected non-zero fallback rate"


class TestOutputStructure:
    """Verify key output fields exist and have the right types."""

    def test_required_fields_present(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "struct_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        required_fields = [
            "study_name", "config_hash", "seed", "n_valid_events",
            "per_quintile", "H1", "H1b", "H2",
            "mde_q5q1_pp", "mde_gate_passed", "fdr_report",
            "perturbation_band", "H3_secondary_horizons",
            "era_lens", "peer_lens", "regime_lens",
            "explore_decision", "explore_decision_rationale",
        ]
        for f in required_fields:
            assert f in result, f"Missing required field: {f}"

    def test_fdr_has_three_hypotheses(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "fdr_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        fdr = result["fdr_report"]
        assert len(fdr) == 3, f"Expected 3 FDR hypotheses, got {len(fdr)}"
        # F410: keys are dynamic (f-string with primary horizon); default primary=63
        assert f"H1_Q5Q1_{63}d" in fdr
        assert f"H1b_spearman_{63}d" in fdr
        assert f"H2_Q5abs_{63}d" in fdr

    def test_h3_has_all_horizons(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "h3_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        h3 = result["H3_secondary_horizons"]
        assert "21" in h3
        assert "63" in h3
        assert "126" in h3

    def test_perturbation_band_has_nine_keys(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "band9_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        band_table = result["perturbation_band"]["band_table"]
        assert len(band_table) == 9, f"Expected 9 perturbation keys, got {len(band_table)}"

    def test_regime_lens_has_four_states(self, tmp_path):
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "regime_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        regime = result["regime_lens"]["per_state"]
        for state in ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"):
            assert state in regime, f"Missing regime state: {state}"


class TestRegimeLens:
    """Regime lens: STRESS and RISK_OFF are always non-evidential; RISK_ON/NEUTRAL gate at n>=15."""

    def test_rare_state_always_non_evidential(self, tmp_path):
        """RISK_OFF is non-evidential even with many events (always-non-evidential).

        F367 charter fix: STRESS is also ALWAYS non-evidential regardless of n —
        it is a rare crisis state; the n>=15 gate applies only to RISK_ON/NEUTRAL.
        """
        groups = [
            (0.10, -5.0), (0.50, 0.0), (1.00, 5.0), (2.00, 10.0), (5.00, 15.0)
        ]

        def _rows_in_state(state: str, prefix: str) -> list:
            rows, idx = [], 0
            for q_grp, (score_val, excess_val) in enumerate(groups):
                for j in range(10):
                    entry_dt = _day_from_idx(2018, idx)
                    idx += 1
                    rows.append(_make_event_row(
                        ticker=f"{prefix}{q_grp}{j:02d}",
                        entry_date=entry_dt.isoformat(),
                        score=score_val + j * 0.001,
                        excess_63=excess_val,
                        excess_21=excess_val * 0.5,
                        excess_126=excess_val * 1.5,
                        peer_excess_63=excess_val * 0.8,
                        peer_excess_21=excess_val * 0.4,
                        peer_excess_126=excess_val * 1.2,
                        regime_state=state,
                    ))
            return rows

        # RISK_OFF: always non-evidential (3 days / 6 years in real data)
        study_dir = _write_study(
            tmp_path, _rows_in_state("RISK_OFF", "RARE"), "rare_state_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        rare = result["regime_lens"]["per_state"]["RISK_OFF"]
        assert rare["is_stress_non_evidential"] is True
        assert rare["is_evidential"] is False

        # F367: STRESS is ALWAYS non-evidential regardless of n (n=50 here, well above 15)
        study_dir2 = _write_study(
            tmp_path, _rows_in_state("STRESS", "STR"), "stress_non_evidential_study")
        result2 = run_r1_analysis(study_dir2, seed=20260606)
        stress = result2["regime_lens"]["per_state"]["STRESS"]
        assert stress["is_stress_non_evidential"] is True, (
            "STRESS must always be non-evidential regardless of n (charter §4 / F367)"
        )
        assert stress["is_evidential"] is False, (
            "STRESS must never be evidential — it is a rare crisis state (F367)"
        )

    def test_stress_non_evidential_large_n(self, tmp_path):
        """F367: STRESS cell with n=607 must be non-evidential.

        This asserts the F367 fix: before the fix, n>=15 STRESS would be marked
        evidential.  Charter §4 intent is that STRESS is ALWAYS non-evidential.
        """
        # Build 607 STRESS events (5 score quintiles × 122 events + 7 overflow to q1)
        rows = []
        groups = [
            (0.10, -5.0),   # quintile 1 (low score)
            (0.50,  0.0),   # quintile 2
            (1.00,  5.0),   # quintile 3
            (2.00, 10.0),   # quintile 4
            (5.00, 15.0),   # quintile 5 (high score)
        ]
        idx = 0
        for q_grp, (score_val, excess_val) in enumerate(groups):
            n_in_group = 122 if q_grp < 3 else 120  # yields 122+122+122+120+120 = 606; add 1 more
            for j in range(n_in_group):
                entry_dt = _day_from_idx(2015, idx)
                idx += 1
                rows.append(_make_event_row(
                    ticker=f"STR{q_grp}{j:04d}",
                    entry_date=entry_dt.isoformat(),
                    score=score_val + j * 0.0001,
                    excess_63=excess_val,
                    excess_21=excess_val * 0.5,
                    excess_126=excess_val * 1.5,
                    peer_excess_63=excess_val * 0.8,
                    peer_excess_21=excess_val * 0.4,
                    peer_excess_126=excess_val * 1.2,
                    regime_state="STRESS",
                ))
        # Add one more to reach 607
        rows.append(_make_event_row(
            ticker="STR_extra",
            entry_date=_day_from_idx(2015, idx).isoformat(),
            score=1.5,
            excess_63=5.0,
            excess_21=2.5,
            excess_126=7.5,
            peer_excess_63=4.0,
            peer_excess_21=2.0,
            peer_excess_126=6.0,
            regime_state="STRESS",
        ))
        assert len(rows) == 607

        study_dir = _write_study(tmp_path, rows, "stress_607_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        stress = result["regime_lens"]["per_state"]["STRESS"]
        assert stress["n_total_quintile_valid"] == 607, (
            f"Expected 607 STRESS events, got {stress['n_total_quintile_valid']}"
        )
        assert stress["is_stress_non_evidential"] is True, (
            "STRESS(n=607): is_stress_non_evidential must be True (F367)"
        )
        assert stress["is_evidential"] is False, (
            "STRESS(n=607) must be non-evidential — STRESS is always non-evidential (F367)"
        )

    def test_low_count_regime_non_evidential(self, tmp_path):
        """Regime cell with <15 events is non-evidential."""
        rows = _make_monotone_rows(n_per_quintile=8)  # all NEUTRAL
        # Total n in NEUTRAL = 40, but per-regime count for RISK_ON = 0 < 15
        study_dir = _write_study(tmp_path, rows, "lowcount_regime_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        risk_on = result["regime_lens"]["per_state"]["RISK_ON"]
        assert risk_on["is_evidential"] is False


# ---------------------------------------------------------------------------
# Required new tests (decisions.md)
# ---------------------------------------------------------------------------

class TestReversedFixtureNotLedgerRejected:
    """COR-01/COR-02: REVERSED fixture (Q5 mean < 0, Q1 mean > 0) must NOT be
    ledger-rejected for H1 — the positive-side direction guard prevents it."""

    def test_h1_not_rejected_when_gap_negative(self, tmp_path):
        """Reversed dose-response: H1 bh_rejected must be False (gap < 0)."""
        rows = _make_antimonotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "reversed_h1_study")
        ledger_path = tmp_path / "reversed_ledger.json"
        result = run_r1_analysis(study_dir, seed=20260606, ledger_path=ledger_path)

        assert result["H1"]["obs_gap_q5q1_pp"] < 0, "Reversed fixture must have negative gap"
        # COR-01: even if one-sided p is low, bh_rejected must be False when gap<0
        assert result["H1"]["bh_rejected"] is False, (
            f"H1 bh_rejected must be False for negative gap; "
            f"gap={result['H1']['obs_gap_q5q1_pp']}, p_boot={result['H1']['p_boot']}"
        )

    def test_h2_not_rejected_when_q5_mean_negative(self, tmp_path):
        """Reversed fixture: Q5 mean < 0 → H2 bh_rejected must be False."""
        rows = _make_antimonotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "reversed_h2_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        q5_abs = result["H2"]["q5_abs_mean_pp"]
        assert q5_abs is not None
        # Q5 has lowest excess in reversed fixture → should be negative
        if q5_abs < 0:
            assert result["H2"]["bh_rejected"] is False, (
                f"H2 bh_rejected must be False when Q5 mean < 0; q5_mean={q5_abs}"
            )

    def test_decision_weakened_not_advance(self, tmp_path):
        """Reversed fixture must produce WEAKENED-IN-EXPLORE, not ADVANCE."""
        rows = _make_antimonotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "reversed_decision_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["explore_decision"] == "WEAKENED-IN-EXPLORE"


class TestConstantMeansSpearman:
    """ADV-01: constant quintile means → Spearman returns nan → p=1.0, flagged."""

    def _make_constant_means_rows(self, n_per_quintile: int = 8) -> list[dict]:
        """All events have identical score AND identical excess → degenerate."""
        rows = []
        idx = 0
        for q_grp in range(5):
            for j in range(n_per_quintile):
                entry_dt = _day_from_idx(2018, idx)
                idx += 1
                rows.append(_make_event_row(
                    ticker=f"CONST{q_grp}{j:02d}",
                    entry_date=entry_dt.isoformat(),
                    score=1.0,  # all same score
                    excess_63=0.0,  # all same excess → constant quintile means
                    excess_21=0.0,
                    excess_126=0.0,
                    peer_excess_63=0.0,
                    peer_excess_21=0.0,
                    peer_excess_126=0.0,
                ))
        return rows

    def test_spearman_p_is_one_for_constant_means(self, tmp_path):
        """Constant quintile means → p_exact_onesided=1.0 (not spurious 0.0)."""
        rows = self._make_constant_means_rows()
        study_dir = _write_study(tmp_path, rows, "const_means_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        p = result["H1b"]["p_exact_onesided"]
        assert p == pytest.approx(1.0), (
            f"Constant quintile means should give p=1.0 (not spurious 0.0), got p={p}"
        )

    def test_rho_s_is_none_for_constant_means(self, tmp_path):
        """Constant quintile means → rho_s is None (degenerate, not nan)."""
        rows = self._make_constant_means_rows()
        study_dir = _write_study(tmp_path, rows, "const_means_rho_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        rho = result["H1b"]["rho_s"]
        assert rho is None, f"Constant quintile means should give rho_s=None, got {rho!r}"

    def test_h1b_not_rejected_for_constant_means(self, tmp_path):
        """Constant quintile means → H1b bh_rejected must be False."""
        rows = self._make_constant_means_rows()
        study_dir = _write_study(tmp_path, rows, "const_means_rejected_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["H1b"]["bh_rejected"] is False, (
            "H1b must not be BH-rejected when quintile means are constant (degenerate)"
        )


class TestOneEventPerQuintileUntestable:
    """ADV-03: n=1 per quintile → MDE=inf → UNTESTABLE not ADVANCE."""

    def _make_one_event_per_quintile_rows(self) -> list[dict]:
        """Exactly 5 events (1 per quintile group), clear monotone signal."""
        rows = []
        groups = [
            (0.10, -5.0),
            (0.50,  0.0),
            (1.00,  5.0),
            (2.00, 10.0),
            (5.00, 15.0),
        ]
        for i, (score_val, excess_val) in enumerate(groups):
            entry_dt = _day_from_idx(2018, i * 15)
            rows.append(_make_event_row(
                ticker=f"ONE{i:02d}",
                entry_date=entry_dt.isoformat(),
                score=score_val,
                excess_63=excess_val,
                excess_21=excess_val * 0.5,
                excess_126=excess_val * 1.5,
                peer_excess_63=excess_val * 0.8,
                peer_excess_21=excess_val * 0.4,
                peer_excess_126=excess_val * 1.2,
            ))
        return rows

    def test_decision_is_untestable_not_advance(self, tmp_path):
        """n=1 per quintile (Q5 n=1, Q1 n=1) → UNTESTABLE, not ADVANCE."""
        rows = self._make_one_event_per_quintile_rows()
        study_dir = _write_study(tmp_path, rows, "one_per_q_study")
        result = run_r1_analysis(study_dir, seed=20260606)

        decision = result["explore_decision"]
        assert "UNTESTABLE" in decision, (
            f"n=1 per quintile should give UNTESTABLE (MDE=inf), got: {decision!r}; "
            f"mde={result['mde_q5q1_pp']}, n_q5={result['H1']['n_q5']}, n_q1={result['H1']['n_q1']}"
        )

    def test_mde_not_evaluable_flag(self, tmp_path):
        """mde_not_evaluable is True when any quintile has n<2."""
        rows = self._make_one_event_per_quintile_rows()
        study_dir = _write_study(tmp_path, rows, "one_per_q_mde_study")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["mde_not_evaluable"] is True


class TestNanSanitizedJsonRoundTrip:
    """ADV-07: nan/inf values → null in JSON output; round-trips through strict parser."""

    def test_verdict_json_strict_parse(self, tmp_path):
        """r1_explore_verdict.json must be parseable by a strict JSON parser (no NaN literals)."""
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "nan_json_study")
        run_r1_analysis(study_dir, seed=20260606)

        verdict_path = study_dir / "r1_explore_verdict.json"
        assert verdict_path.exists()
        raw_text = verdict_path.read_text(encoding="utf-8")

        # Strict: no NaN or Infinity literals allowed in standard JSON
        assert "NaN" not in raw_text, "Verdict JSON must not contain NaN literal"
        assert "Infinity" not in raw_text, "Verdict JSON must not contain Infinity literal"
        assert "-Infinity" not in raw_text, "Verdict JSON must not contain -Infinity literal"

        # Must be parseable by json.loads (Python's parser is lenient but this catches
        # any other malformed structure)
        parsed = json.loads(raw_text)
        assert isinstance(parsed, dict)

    def test_constant_means_verdict_no_nan(self, tmp_path):
        """Constant-means fixture (which triggers rho_s=nan path) writes valid JSON."""
        # Reuse the constant-means helper from TestConstantMeansSpearman
        rows = []
        idx = 0
        for q_grp in range(5):
            for j in range(8):
                entry_dt = _day_from_idx(2018, idx)
                idx += 1
                rows.append(_make_event_row(
                    ticker=f"CJSON{q_grp}{j:02d}",
                    entry_date=entry_dt.isoformat(),
                    score=1.0,
                    excess_63=0.0,
                    excess_21=0.0,
                    excess_126=0.0,
                    peer_excess_63=0.0,
                    peer_excess_21=0.0,
                    peer_excess_126=0.0,
                ))
        study_dir = _write_study(tmp_path, rows, "const_json_study")
        run_r1_analysis(study_dir, seed=20260606)

        verdict_path = study_dir / "r1_explore_verdict.json"
        raw_text = verdict_path.read_text(encoding="utf-8")
        assert "NaN" not in raw_text, "Constant-means verdict JSON must not contain NaN"
        parsed = json.loads(raw_text)
        assert parsed["H1b"]["rho_s"] is None  # must be null, not NaN


# ---------------------------------------------------------------------------
# F410: Premise-horizon-aware verdict tests
# ---------------------------------------------------------------------------

def _make_rows_for_horizons(
    n_per_quintile: int = 8,
    primary_h: int = 30,
    secondary_h: tuple = (10, 21, 30),
    include_63: bool = True,
) -> list[dict]:
    """Build monotone rows with excess populated for a non-standard spec horizon set.

    primary_h: the primary horizon (e.g. 30). Rows have non-null excess for this horizon.
    secondary_h: all spec horizons populated.
    include_63: if True, also populate 63td excess (simulates harness having injected 63).
    Returns rows with null for 126td and null for 21td (unless specified in secondary_h).
    """
    groups = [
        (0.10, -5.0),
        (0.50,  0.0),
        (1.00,  5.0),
        (2.00, 10.0),
        (5.00, 15.0),
    ]
    rows = []
    idx = 0
    for q_grp, (score_val, excess_val) in enumerate(groups):
        for j in range(n_per_quintile):
            entry_dt = _day_from_idx(2018, idx)
            idx += 1
            ticker = f"PH{q_grp}{j:02d}"
            extra: dict = {}
            for h in secondary_h:
                if h not in (21, 63, 126):  # already handled by positional args
                    extra[h] = excess_val + np.random.default_rng(42 + idx + h).normal(0, 0.1)
            # 63td secondary: use distinct value from primary for realism
            excess_63_val = (excess_val * 0.9) if include_63 else None
            rows.append(_make_event_row(
                ticker=ticker,
                entry_date=entry_dt.isoformat(),
                score=score_val + j * 0.001,
                excess_63=excess_63_val,
                excess_21=excess_val * 0.5 if 21 in secondary_h else None,
                excess_126=None,  # not in spec
                peer_excess_63=excess_63_val,
                peer_excess_21=excess_val * 0.25 if 21 in secondary_h else None,
                peer_excess_126=None,
                extra_horizons=extra,
            ))
    return rows


class TestPremiseHorizonSupport:
    """F410: premise-horizon-aware verdict — primary=max(spec.horizons), 63td secondary."""

    def test_premise_spec_primary_horizon_used_as_primary(self, tmp_path):
        """Spec with horizons=(10,21,30): primary=30, verdict judged at 30td, not 63td.

        Fixture: rows have non-null 30td excess but null 63td excess.
        Expected: n_valid_events > 0, fdr_report has H1_Q5Q1_30d, not H1_Q5Q1_63d.
        """
        rows = _make_rows_for_horizons(
            n_per_quintile=8, primary_h=30, secondary_h=(10, 21, 30), include_63=False
        )
        study_dir = _write_study(tmp_path, rows, "ph_primary_study")
        result = run_r1_analysis(study_dir, seed=20260606, primary_horizon=30, horizons=(10, 21, 30))

        assert result["primary_horizon"] == 30
        assert result["n_valid_events"] > 0, (
            f"Expected >0 valid events at 30td primary, got 0 "
            f"(check that _is_valid_event uses primary_horizon param)"
        )
        fdr = result["fdr_report"]
        assert "H1_Q5Q1_30d" in fdr, f"FDR report missing H1_Q5Q1_30d; keys={list(fdr.keys())}"
        assert "H1b_spearman_30d" in fdr
        assert "H2_Q5abs_30d" in fdr
        assert "H1_Q5Q1_63d" not in fdr, (
            "FDR report must NOT have H1_Q5Q1_63d when primary=30 "
            "(63td is secondary/reporting only)"
        )

    def test_63td_secondary_populated_when_spec_omits_63(self, tmp_path):
        """When primary_horizon=30, H3_secondary_horizons must include key '63'.

        Fixture: rows have 63td excess (harness adds it) plus 30td primary.
        Expected: '63' in H3_secondary_horizons, is_primary=False for 63.
        """
        rows = _make_rows_for_horizons(
            n_per_quintile=8, primary_h=30, secondary_h=(10, 21, 30), include_63=True
        )
        study_dir = _write_study(tmp_path, rows, "ph_secondary63_study")
        result = run_r1_analysis(study_dir, seed=20260606, primary_horizon=30, horizons=(10, 21, 30))

        h3 = result["H3_secondary_horizons"]
        assert "63" in h3, f"63td secondary must always be in H3; keys={list(h3.keys())}"
        assert h3["63"]["is_primary"] is False, "63td must NOT be marked is_primary when primary=30"
        # Primary should be marked correctly
        assert h3["30"]["is_primary"] is True, "30td must be marked is_primary when primary=30"

    def test_r1_default_unchanged_bit_identical(self, tmp_path):
        """R-1 charter default: two calls produce bit-identical numeric output.

        run_r1_analysis(dir) and run_r1_analysis(dir, primary_horizon=63, horizons=(21,63,126))
        must produce identical result dicts including config_hash.
        """
        rows = _make_monotone_rows(n_per_quintile=8)

        # Run 1: default (no horizon kwargs)
        study_dir1 = _write_study(tmp_path, rows, "ph_bitid_default")
        r1 = run_r1_analysis(study_dir1, seed=20260606)

        # Run 2: explicit default values
        study_dir2 = _write_study(tmp_path, rows, "ph_bitid_explicit")
        r2 = run_r1_analysis(study_dir2, seed=20260606, primary_horizon=63, horizons=(21, 63, 126))

        # config_hash must be identical (hard constraint from F410 spec)
        assert r1["config_hash"] == r2["config_hash"], (
            f"config_hash differs! default={r1['config_hash']!r} vs "
            f"explicit={r2['config_hash']!r} — hash inputs must be identical for defaults"
        )
        # primary_horizon must be 63 in both
        assert r1["primary_horizon"] == 63
        assert r2["primary_horizon"] == 63
        # FDR keys must be 63d-suffixed in both
        assert "H1_Q5Q1_63d" in r1["fdr_report"]
        assert "H1_Q5Q1_63d" in r2["fdr_report"]
        # Numeric results must be identical
        assert r1["H1"]["p_boot"] == r2["H1"]["p_boot"]
        assert r1["n_valid_events"] == r2["n_valid_events"]

    def test_r1_default_config_hash_literal(self, tmp_path):
        """Config hash for default (seed=20260606, primary=63, horizons=(21,63,126)) must
        equal the pre-F410 literal value d1b9f7150f850e6a — bit-identity constraint (F410)."""
        rows = _make_monotone_rows(n_per_quintile=8)
        study_dir = _write_study(tmp_path, rows, "ph_hash_literal")
        result = run_r1_analysis(study_dir, seed=20260606)
        assert result["config_hash"] == "d1b9f7150f850e6a", (
            f"Default config_hash changed! Got {result['config_hash']!r}. "
            "Adding horizon params to hash when they equal the defaults is NOT allowed (F410)."
        )

    def test_no_second_pass_fail_bar_from_63td_secondary(self, tmp_path):
        """63td secondary row must NOT gate the verdict.

        Fixture: rows have good 30td excess (expect ADVANCE/WEAKENED) but null 63td excess.
        Expected: explore_decision is decided by 30td primary, NOT UNTESTABLE from missing 63td.
        """
        rows = _make_rows_for_horizons(
            n_per_quintile=8, primary_h=30, secondary_h=(10, 21, 30), include_63=False
        )
        study_dir = _write_study(tmp_path, rows, "ph_no_second_bar_study")
        result = run_r1_analysis(study_dir, seed=20260606, primary_horizon=30, horizons=(10, 21, 30))

        # Key requirement: n_valid_events > 0 (not blocked by missing 63td)
        assert result["n_valid_events"] > 0, (
            "n_valid_events must be >0 when 30td excess is present and primary=30 "
            "(63td secondary must not gate valid-event filtering)"
        )
        # explore_decision must not be UNTESTABLE due to missing 63td
        decision = result["explore_decision"]
        assert decision != "UNTESTABLE — power not evaluable" or result["mde_not_evaluable"], (
            "explore_decision is UNTESTABLE but mde_not_evaluable is False — "
            "this suggests 63td secondary is incorrectly gating the decision"
        )
        # The FDR family must be 3 hypotheses at the 30td primary horizon
        assert len(result["fdr_report"]) == 3
        assert "H1_Q5Q1_30d" in result["fdr_report"]

    def test_fdr_has_three_hypotheses_premise_horizon(self, tmp_path):
        """FDR family must be exactly 3 hypotheses for premise-specific primary horizon.

        DI-6: mirrors TestOutputStructure.test_fdr_has_three_hypotheses but for the
        non-default path (primary_horizon=30, horizons=(10,21,30)).  Guards the
        'no 4th bar' constraint (see R-1 charter) on the F410 premise-horizon code path.
        """
        rows = _make_rows_for_horizons(
            n_per_quintile=8, primary_h=30, secondary_h=(10, 21, 30), include_63=True
        )
        study_dir = _write_study(tmp_path, rows, "fdr_premise_horizon_study")
        result = run_r1_analysis(study_dir, seed=20260606, primary_horizon=30, horizons=(10, 21, 30))
        fdr = result["fdr_report"]
        assert len(fdr) == 3, (
            f"Expected exactly 3 FDR hypotheses for premise-horizon path "
            f"(primary=30), got {len(fdr)}: {list(fdr.keys())}"
        )
        assert f"H1_Q5Q1_30d" in fdr
        assert f"H1b_spearman_30d" in fdr
        assert f"H2_Q5abs_30d" in fdr
