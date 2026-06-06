"""F339 — Tests for outcome_table.py ETL and bootstrap math.

Uses synthetic fixtures to test:
- build_outcome_table ETL correctness
- bootstrap_mean_ci statistical properties
- minimum_detectable_effect formula
- analysis functions on controlled data
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Add research directory to path
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from research.outcome_table import (
    analysis_a_effect_ci,
    analysis_c_d2_long,
    analysis_d_top1_concentration,
    bootstrap_mean_ci,
    build_outcome_table,
    minimum_detectable_effect,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_event(
    ticker: str,
    as_of: str,
    excess_63d: float,
    excess_21d: float = 1.0,
    excess_126d: float = 2.0,
    direction: str = "long",
    composite_score: float = 10.0,
    config_name: str = "test_exp",
    fwd_return_63d: float = 5.0,
    fwd_return_21d: float = 2.0,
    fwd_return_126d: float = 8.0,
) -> dict:
    return {
        "ticker": ticker,
        "as_of": as_of,
        "is_null": False,
        "entry_date": as_of,
        "entry_price": 10.0,
        "exit_date": as_of,
        "exit_price": 11.0,
        "net_return_pct": 10.0,
        "forward_return_pct": 10.0,
        "hit": True,
        "days_to_hit": None,
        "composite_score": composite_score,
        "horizon_months": 6,
        "horizon_end_return_pct": 10.0,
        "config_name": config_name,
        "direction": direction,
        "fwd_return_21d": fwd_return_21d,
        "fwd_return_63d": fwd_return_63d,
        "fwd_return_126d": fwd_return_126d,
        "excess_21d": excess_21d,
        "excess_63d": excess_63d,
        "excess_126d": excess_126d,
        "hit_v2_21d": excess_21d > 0,
        "hit_v2_63d": excess_63d > 0,
        "hit_v2_126d": excess_126d > 0,
        "trailing_vol_252d": 0.02,
    }


def _write_artifact(path: Path, events: list[dict]) -> None:
    artifact = {
        "schema_version": 2,
        "signal_n": len(events),
        "events": events,
        "cohort_null_aggregates": {},
        "signal_hit_rate": 0.5,
        "signal_mean_return_pct": 5.0,
        "dates_completed": 2,
    }
    with open(path, "w") as fh:
        json.dump(artifact, fh)


# ---------------------------------------------------------------------------
# Test 1: ETL reads events and produces correct row count (end-to-end)
# ---------------------------------------------------------------------------

def test_build_outcome_table_row_count(tmp_path: Path, monkeypatch) -> None:
    """build_outcome_table should emit one row per event across registered artifacts."""
    import research.outcome_table as ot

    # Patch _DATA_DIR so build_outcome_table reads from tmp_path
    monkeypatch.setattr(ot, "_DATA_DIR", tmp_path)

    events_a = [_make_event(f"A{i}", "2022-02-15", 1.0) for i in range(5)]
    events_b = [_make_event(f"B{i}", "2022-05-15", -2.0) for i in range(3)]

    # Write artifacts matching the _ARTIFACTS registry entries we care about
    _write_artifact(tmp_path / "momentum_M1_explore_result.json", events_a)
    _write_artifact(tmp_path / "epistemics_price_explore_result.json", events_b)
    # Other registered artifacts are missing; they should be skipped with a warning

    output = tmp_path / "out.csv"
    rows = ot.build_outcome_table(output_path=output)

    # 5 + 3 = 8 rows total from the two written artifacts
    assert len(rows) == 8

    # Verify CSV round-trip: correct row count and columns
    with open(output) as fh:
        reader = list(csv.DictReader(fh))
    assert len(reader) == 8
    assert set(reader[0].keys()) == set(ot._COLUMNS)

    # Verify at least one round-tripped numeric value
    first_a = next(r for r in reader if r["experiment"] == "momentum_M1")
    assert abs(float(first_a["fwd_ret_63"]) - 5.0) < 1e-6


# ---------------------------------------------------------------------------
# Test 2: ETL correctly maps column names (end-to-end via build_outcome_table)
# ---------------------------------------------------------------------------

def test_build_outcome_table_columns(tmp_path: Path, monkeypatch) -> None:
    """ETL should emit all expected columns including entry_date."""
    import research.outcome_table as ot
    monkeypatch.setattr(ot, "_DATA_DIR", tmp_path)

    events = [_make_event("AAPL", "2022-02-15", 3.0)]
    _write_artifact(tmp_path / "momentum_M1_explore_result.json", events)

    output = tmp_path / "out.csv"
    ot.build_outcome_table(output_path=output)

    with open(output) as fh:
        reader = csv.DictReader(fh)
        actual_cols = reader.fieldnames
    assert actual_cols == ot._COLUMNS


# ---------------------------------------------------------------------------
# Test 3: bootstrap_mean_ci returns (mean, lo, hi) with correct sign
# ---------------------------------------------------------------------------

def test_bootstrap_mean_ci_sign() -> None:
    """Bootstrap CI for uniformly positive values should have positive mean and lo."""
    values = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0] * 10
    mean, lo, hi = bootstrap_mean_ci(values, n_resamples=1000, seed=1)
    assert mean > 0
    assert lo > 0
    assert hi > lo


def test_bootstrap_mean_ci_negative_values() -> None:
    """Bootstrap CI for uniformly negative values should have negative mean and hi."""
    values = [-5.0, -6.0, -7.0] * 20
    mean, lo, hi = bootstrap_mean_ci(values, n_resamples=1000, seed=2)
    assert mean < 0
    assert hi < 0
    assert lo < hi


def test_bootstrap_mean_ci_empty() -> None:
    """Empty input should return nan tuple."""
    mean, lo, hi = bootstrap_mean_ci([])
    assert math.isnan(mean)
    assert math.isnan(lo)
    assert math.isnan(hi)


# ---------------------------------------------------------------------------
# Test 4: minimum_detectable_effect formula properties
# ---------------------------------------------------------------------------

def test_mde_increases_with_std() -> None:
    """MDE should scale proportionally with std."""
    mde1 = minimum_detectable_effect(n=16, std=1.0)
    mde2 = minimum_detectable_effect(n=16, std=2.0)
    assert abs(mde2 / mde1 - 2.0) < 0.01


def test_mde_decreases_with_n() -> None:
    """MDE should decrease as n increases."""
    mde_small = minimum_detectable_effect(n=4, std=1.0)
    mde_large = minimum_detectable_effect(n=64, std=1.0)
    assert mde_large < mde_small


def test_mde_formula_value() -> None:
    """Check MDE formula: (1.96 + 0.842) * std / sqrt(n)."""
    n, std = 16, 2.0
    expected = (1.96 + 0.842) * std / math.sqrt(n)
    result = minimum_detectable_effect(n=n, std=std)
    assert abs(result - expected) < 1e-9


# ---------------------------------------------------------------------------
# Test 5: analysis_a groups correctly
# ---------------------------------------------------------------------------

def test_analysis_a_grouping() -> None:
    """analysis_a should produce one result per (experiment, arm, horizon)."""
    rows = []
    # 4 cohort dates × 3 picks each = 12 rows for momentum/confirm
    for i, date in enumerate(["2021-02-15", "2021-05-15", "2021-08-15", "2021-11-15"]):
        for j in range(3):
            rows.append({
                "experiment": "momentum_M1",
                "arm": "confirm",
                "cohort_date": date,
                "ticker": f"T{i}{j}",
                "direction": "long",
                "composite_score": float(j),
                "fwd_ret_21": 1.0,
                "fwd_ret_63": 2.0,
                "fwd_ret_126": 3.0,
                "excess_21": 0.5,
                "excess_63": 1.5,
                "excess_126": 2.5,
            })

    results = analysis_a_effect_ci(rows, n_resamples=500)
    horizons = {r["horizon"] for r in results}
    assert horizons == {21, 63, 126}
    for r in results:
        assert r["n_cohorts"] == 4
        assert r["n_picks"] == 12


# ---------------------------------------------------------------------------
# Test 6: analysis_c uses D2 confirm rows only, cohort-level bootstrap
# ---------------------------------------------------------------------------

def _d2_row(ticker: str, cohort_date: str, excess_63: float, arm: str = "confirm") -> dict:
    return {
        "experiment": "deterioration_D2",
        "arm": arm,
        "cohort_date": cohort_date,
        "ticker": ticker,
        "direction": "short",
        "composite_score": 5.0,
        "fwd_ret_21": 1.0,
        "fwd_ret_63": 3.0,
        "fwd_ret_126": 6.0,
        "excess_21": 0.5,
        "excess_63": excess_63,
        "excess_126": 4.0,
    }


def test_analysis_c_filters_d2_confirm() -> None:
    """analysis_c should only use deterioration_D2 confirm rows."""
    rows = [
        _d2_row("X", "2022-02-15", excess_63=2.0, arm="confirm"),
        _d2_row("Y", "2022-05-15", excess_63=4.0, arm="confirm"),
        _d2_row("Z", "2022-02-15", excess_63=-10.0, arm="explore"),  # must be excluded
    ]
    result = analysis_c_d2_long(rows, n_resamples=200)
    # explore row excluded; 2 cohorts × 1 pick each → 2 cohort means: (2.0+4.0)/2 = 3.0
    assert result[63]["n_cohorts"] == 2
    assert result[63]["n_picks"] == 2
    assert abs(result[63]["mean_excess"] - 3.0) < 1e-9


def test_analysis_c_cohort_level_bootstrap() -> None:
    """analysis_c must bootstrap cohort means, not individual picks.

    Fixture: 2 cohorts, 10 picks each.
    - Cohort A (2022-02-15): all picks have excess_63 = 0.0  → cohort mean = 0.0
    - Cohort B (2022-05-15): all picks have excess_63 = 10.0 → cohort mean = 10.0

    Cohort-level bootstrap over [0.0, 10.0] → SE ≈ std/sqrt(2) = 5.0/sqrt(2) ≈ 3.54.
    Pick-level bootstrap over [0.0]*10 + [10.0]*10 → SE ≈ std/sqrt(20) = 5.0/sqrt(20) ≈ 1.12.
    The cohort-level CI must be wider: (ci_hi - ci_lo) should be >> 2 * 1.96 * 1.12 ≈ 4.39.
    """
    rows = []
    for i in range(10):
        rows.append(_d2_row(f"A{i}", "2022-02-15", excess_63=0.0))
    for i in range(10):
        rows.append(_d2_row(f"B{i}", "2022-05-15", excess_63=10.0))

    result = analysis_c_d2_long(rows, n_resamples=5_000)
    r63 = result[63]
    assert r63 is not None
    assert r63["n_cohorts"] == 2
    assert r63["n_picks"] == 20
    # Grand mean should be 5.0 (equal cohorts)
    assert abs(r63["mean_excess"] - 5.0) < 0.5

    ci_width = r63["ci_hi"] - r63["ci_lo"]
    # Cohort-level 95% CI over 2 cohorts should be wide (>> pick-level CI of ~4.4)
    # Pick-level CI would be ≈ 2 * 1.96 * 5.0/sqrt(20) ≈ 4.4; cohort-level ≈ 2*1.96*5/sqrt(2) ≈ 13.9
    # Use a lenient lower bound that only cohort-level bootstrap can satisfy.
    assert ci_width > 8.0, (
        f"CI width {ci_width:.2f} is too narrow — likely using pick-level bootstrap "
        f"instead of cohort-level. Expected > 8.0 for cohort-level."
    )


# ---------------------------------------------------------------------------
# Test 7: analysis_d top-1 concentration picks max score
# ---------------------------------------------------------------------------

def test_analysis_d_top1_picks_max() -> None:
    """analysis_d should pick the row with the highest composite_score per cohort."""
    rows = [
        {
            "experiment": "momentum_M1",
            "arm": "confirm",
            "cohort_date": "2022-02-15",
            "ticker": "LOW",
            "direction": "long",
            "composite_score": 1.0,
            "fwd_ret_63": 5.0,
            "excess_63": -10.0,  # bad performer, low score
            "fwd_ret_21": 1.0,
            "fwd_ret_126": 3.0,
            "excess_21": 0.0,
            "excess_126": 0.0,
        },
        {
            "experiment": "momentum_M1",
            "arm": "confirm",
            "cohort_date": "2022-02-15",
            "ticker": "HIGH",
            "direction": "long",
            "composite_score": 99.0,
            "fwd_ret_63": 5.0,
            "excess_63": 20.0,  # good performer, high score
            "fwd_ret_21": 1.0,
            "fwd_ret_126": 3.0,
            "excess_21": 0.0,
            "excess_126": 0.0,
        },
    ]
    result = analysis_d_top1_concentration(rows)
    r = result["rows"][0]
    assert r["status"] == "ok"
    assert abs(r["top1_mean_excess_63"] - 20.0) < 1e-9
    # all-picks mean should be (-10 + 20) / 2 = 5
    assert abs(r["all_picks_mean_excess_63"] - 5.0) < 1e-9
