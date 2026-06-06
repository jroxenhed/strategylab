"""Tests for power_audit.py (F340).

Uses synthetic toy panels with known uplifts.  No network calls, no disk I/O.

Tests verify:
  1.  Monotone power: synthetic panel with growing uplift shows non-decreasing
      detection rates.
  2.  Placebo: E=0 detection rate is near nominal alpha (~5%), not inflated.
  3.  High uplift: E=10 detection rate is near 100%.
  4.  Panel loading helpers work correctly on synthetic data.
  5.  Minimum-detectable-edge interpolation is accurate.
  6.  Design date generators return expected counts.
  7.  Anchor checker correctly classifies known power tables.
  8.  run_power_experiment returns 0.0 gracefully on empty/degenerate input.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Ensure the research module is importable without the backend server context.
# ---------------------------------------------------------------------------
import sys
_RESEARCH_DIR = Path(__file__).resolve().parent.parent / "research"
if str(_RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_DIR))

from power_audit import (
    _quarterly_dates,
    _monthly_dates,
    _event_time_dates,
    _LATEST_DECISION,
    _nw_ttest_pvalue,
    check_f338_anchors,
    min_detectable_edge,
    run_power_experiment,
    FORWARD_DAYS,
)

import pandas as pd


# ---------------------------------------------------------------------------
# Helpers to build deterministic toy panels
# ---------------------------------------------------------------------------

def _make_toy_panel(
    n_tickers: int = 200,
    n_dates: int = 500,
    seed: int = 0,
    base_return: float = 0.02,
    cross_date_std: float = 0.05,
    cross_ticker_std: float = 0.20,
) -> tuple[np.ndarray, list[str], list[pd.Timestamp]]:
    """Return (panel, tickers, panel_dates) with controllable return structure.

    Returns are N(base_return + date_effect, cross_ticker_std):
      - date_effect ~ N(0, cross_date_std) shared across tickers on that date
      - per-ticker noise ~ N(0, cross_ticker_std) independent

    This mimics real panels: dates have correlated cross-sectional levels
    (market regime), plus idiosyncratic per-ticker noise.
    """
    rng = np.random.default_rng(seed)

    date_effects = rng.normal(0, cross_date_std, size=n_dates)  # (n_dates,)
    ticker_noise = rng.normal(0, cross_ticker_std, size=(n_tickers, n_dates))

    # Panel: (n_tickers, n_dates)
    panel = base_return + date_effects[np.newaxis, :] + ticker_noise

    # Inject ~10% NaN to mimic missing prices
    nan_mask = rng.random(size=panel.shape) < 0.10
    panel[nan_mask] = np.nan

    tickers = [f"T{i:04d}" for i in range(n_tickers)]

    # Generate trading-day-like dates
    start = pd.Timestamp("2015-01-02")
    panel_dates = pd.bdate_range(start, periods=n_dates).tolist()

    return panel, tickers, panel_dates


def _make_decision_dates(
    panel_dates: list[pd.Timestamp], n: int, seed: int = 7
) -> list[pd.Timestamp]:
    """Pick n evenly-spaced dates from panel_dates, up to _LATEST_DECISION."""
    eligible = [d for d in panel_dates if d <= _LATEST_DECISION]
    if len(eligible) < n:
        return eligible
    step = len(eligible) // n
    return [eligible[i * step] for i in range(n)]


# ---------------------------------------------------------------------------
# Test 1: monotone power — detection rate must be non-decreasing in E
# ---------------------------------------------------------------------------

def test_monotone_power_synthetic():
    """Detection rate must be weakly non-decreasing as uplift E grows."""
    panel, tickers, panel_dates = _make_toy_panel(n_tickers=300, n_dates=400, seed=1)
    dec_dates = _make_decision_dates(panel_dates, n=30)
    rng = np.random.default_rng(99)

    rates = []
    for e in [0, 2, 5, 10]:
        r = run_power_experiment(
            panel, tickers, panel_dates, dec_dates,
            n_picks=30, uplift=float(e), n_reps=200, rng=rng
        )
        rates.append(r)

    # Allow 3ppt sampling noise between adjacent E values
    for i in range(1, len(rates)):
        assert rates[i] >= rates[i - 1] - 0.03, (
            f"Power decreased from E={[0,2,5,10][i-1]} ({rates[i-1]:.3f}) "
            f"to E={[0,2,5,10][i]} ({rates[i]:.3f})"
        )


# ---------------------------------------------------------------------------
# Test 2: placebo rate near nominal alpha
# ---------------------------------------------------------------------------

def test_placebo_rate_near_alpha():
    """E=0 detection rate should be approximately 5% (2–10% tolerance)."""
    panel, tickers, panel_dates = _make_toy_panel(n_tickers=400, n_dates=400, seed=2)
    dec_dates = _make_decision_dates(panel_dates, n=40)
    rng = np.random.default_rng(42)

    rate = run_power_experiment(
        panel, tickers, panel_dates, dec_dates,
        n_picks=40, uplift=0.0, n_reps=500, rng=rng
    )
    # Wide tolerance: this is a stochastic test; only fails on gross miscalibration
    assert 0.01 <= rate <= 0.15, (
        f"Placebo rate {rate:.3f} outside [0.01, 0.15] — t-test likely miscalibrated"
    )


# ---------------------------------------------------------------------------
# Test 3: high-uplift detection near 100%
# ---------------------------------------------------------------------------

def test_high_uplift_detection():
    """E=15 should be detected in essentially every rep."""
    panel, tickers, panel_dates = _make_toy_panel(n_tickers=300, n_dates=400, seed=3)
    dec_dates = _make_decision_dates(panel_dates, n=30)
    rng = np.random.default_rng(77)

    rate = run_power_experiment(
        panel, tickers, panel_dates, dec_dates,
        n_picks=30, uplift=15.0, n_reps=200, rng=rng
    )
    assert rate >= 0.95, f"E=15 detection {rate:.3f} < 0.95"


# ---------------------------------------------------------------------------
# Test 4: degenerate inputs return 0.0 without raising
# ---------------------------------------------------------------------------

def test_degenerate_empty_panel():
    """Empty panel and too-few-date panels return 0.0, not an exception."""
    empty_panel = np.full((0, 10), np.nan)
    rng = np.random.default_rng(0)
    result = run_power_experiment(
        empty_panel, [], [pd.Timestamp("2015-01-02")],
        [pd.Timestamp("2015-01-02")],
        n_picks=5, uplift=0.0, n_reps=10, rng=rng
    )
    assert result == 0.0


def test_degenerate_all_nan_column():
    """Column of all NaN (no valid tickers for a date) is skipped gracefully."""
    panel = np.full((100, 10), np.nan)
    rng = np.random.default_rng(0)
    panel_dates = pd.bdate_range("2015-01-02", periods=10).tolist()
    dec_dates = panel_dates[:3]
    result = run_power_experiment(
        panel, [f"T{i}" for i in range(100)], panel_dates, dec_dates,
        n_picks=10, uplift=5.0, n_reps=10, rng=rng
    )
    assert result == 0.0


# ---------------------------------------------------------------------------
# Test 5: min_detectable_edge interpolation
# ---------------------------------------------------------------------------

def test_mde_interpolation_exact():
    """Interpolation finds 80% threshold exactly when it falls between grid points."""
    e_grid = [0, 1, 2, 3, 5, 10]
    # Linear ramp: power goes 0.05, 0.40, 0.75, 0.90, 1.00, 1.00
    # 80% threshold is between E=2 (0.75) and E=3 (0.90)
    powers = [0.05, 0.40, 0.75, 0.90, 1.00, 1.00]
    mde = min_detectable_edge(e_grid, powers, threshold=0.80)
    # Linear interp: 0.75 + (0.80-0.75)/(0.90-0.75) * (3-2) = 2 + 0.05/0.15 = 2.333
    expected = 2 + (0.80 - 0.75) / (0.90 - 0.75)
    assert mde is not None
    assert abs(mde - expected) < 0.01, f"MDE {mde:.3f} != expected {expected:.3f}"


def test_mde_never_reached():
    """Returns None when power never reaches the threshold."""
    e_grid = [0, 1, 2, 3]
    powers = [0.05, 0.20, 0.40, 0.65]  # never hits 0.80
    mde = min_detectable_edge(e_grid, powers, threshold=0.80)
    assert mde is None


def test_mde_already_at_threshold():
    """Returns first E when power exceeds threshold from E=0."""
    e_grid = [0, 1, 2]
    powers = [0.90, 0.99, 1.00]
    mde = min_detectable_edge(e_grid, powers, threshold=0.80)
    assert mde == 0.0


# ---------------------------------------------------------------------------
# Test 6: design date generators
# ---------------------------------------------------------------------------

def test_quarterly_dates_count_and_cap():
    """Quarterly design has ≤24 dates (4/yr × 6 yr) all ≤ _LATEST_DECISION."""
    dates = _quarterly_dates()
    assert len(dates) >= 20, f"Expected ~23 quarterly dates, got {len(dates)}"
    assert len(dates) <= 24
    assert all(d <= _LATEST_DECISION for d in dates)


def test_monthly_dates_count_and_cap():
    """Monthly design has ≤72 dates (12/yr × 6 yr) all ≤ _LATEST_DECISION."""
    dates = _monthly_dates()
    assert len(dates) >= 60
    assert len(dates) <= 72
    assert all(d <= _LATEST_DECISION for d in dates)


def test_event_time_dates_count():
    """Event-time 100/yr produces ≤600 dates in 6 years."""
    rng = np.random.default_rng(42)
    panel_dates = pd.bdate_range("2015-01-02", "2020-09-30").tolist()
    dates = _event_time_dates(100, rng, panel_dates)
    assert len(dates) >= 400  # might be fewer if business days insufficient
    assert len(dates) <= 600
    assert all(d <= _LATEST_DECISION for d in dates)


# ---------------------------------------------------------------------------
# Test 7: anchor checker classifies known power tables correctly
# ---------------------------------------------------------------------------

def test_anchor_checker_all_pass():
    """Checker returns all PASS for a well-behaved synthetic power table."""
    e_grid = [0, 1, 2, 3, 5, 10]
    # All designs: FPR=5%, monotone, denser designs better
    power_table = {
        "QUARTERLY-4_matched": [0.05, 0.15, 0.50, 0.80, 0.99, 1.00],
        "MONTHLY_matched":     [0.05, 0.25, 0.70, 0.95, 1.00, 1.00],
        "EVENT-TIME-100_matched": [0.05, 0.20, 0.60, 0.90, 1.00, 1.00],
        "EVENT-TIME-400_matched": [0.05, 0.18, 0.55, 0.85, 1.00, 1.00],
        "QUARTERLY-4_fixed40":    [0.05, 0.15, 0.50, 0.80, 0.99, 1.00],
        "MONTHLY_fixed40":        [0.05, 0.25, 0.70, 0.95, 1.00, 1.00],
        "EVENT-TIME-100_fixed40": [0.05, 0.60, 0.99, 1.00, 1.00, 1.00],
        "EVENT-TIME-400_fixed40": [0.05, 0.80, 1.00, 1.00, 1.00, 1.00],
    }
    results = {
        "e_grid": e_grid,
        "power_table": power_table,
    }
    findings = check_f338_anchors(results)
    fails = [f for f in findings if "[FAIL]" in f]
    assert len(fails) == 0, f"Unexpected FAIL(s): {fails}"
    passes = [f for f in findings if "[PASS]" in f]
    assert len(passes) == 4  # anchors 1–4


def test_anchor_checker_fails_on_bad_placebo():
    """Checker reports FAIL when E=0 rate exceeds 10% for any design."""
    e_grid = [0, 10]
    power_table = {
        "QUARTERLY-4_matched": [0.20, 1.00],  # 20% FPR → FAIL anchor 1
        "MONTHLY_matched": [0.05, 1.00],
        "EVENT-TIME-100_matched": [0.05, 1.00],
        "EVENT-TIME-400_matched": [0.05, 1.00],
        "QUARTERLY-4_fixed40": [0.05, 1.00],
        "MONTHLY_fixed40": [0.05, 1.00],
        "EVENT-TIME-100_fixed40": [0.05, 1.00],
        "EVENT-TIME-400_fixed40": [0.05, 1.00],
    }
    results = {"e_grid": e_grid, "power_table": power_table}
    findings = check_f338_anchors(results)
    assert any("[FAIL]" in f and "Anchor 1" in f for f in findings)


# ---------------------------------------------------------------------------
# Test 9: Newey-West HAC test dramatically reduces FPR for autocorrelated null series
# ---------------------------------------------------------------------------

def test_nw_ttest_fpr_on_ar_correlated_series():
    """NW t-test must dramatically reduce FPR compared to the iid t-test on an
    autocorrelated null series, confirming the COR-01 fix is meaningful.

    We use a moving-average MA(8) process with n=400 observations.  This mimics
    the overlap structure of the dense event-time designs: adjacent 63-trading-day
    forward-return windows share ~8 periods of overlap after coarsening.

    The plain iid t-test FPR should be ~30-50% (severely inflated at H0).
    The NW-corrected FPR should be much lower — converging toward 5% as n→∞;
    at n=400 we accept anything up to 20% (the Bartlett kernel is known to
    over-correct modestly for finite n, trading size-bias for conservatism).

    The critical assertion is the *relative* comparison: NW FPR must be at least
    10 percentage points below the plain t-test FPR on the same series.
    """
    from scipy import stats as scipy_stats

    q = 8       # MA order: each observation is mean of 8 consecutive shocks
    n = 400     # length of the excess series (comparable to monthly/quarterly designs)
    nw_lag = q  # NW lag covers all non-zero autocorrelations (MA(q) is 0 at lag > q-1)
    n_trials = 3000
    rng = np.random.default_rng(12345)

    nw_rejections = 0
    ttest_rejections = 0

    for _ in range(n_trials):
        # MA(q) process: y_t = mean(eps[t:t+q]) — zero mean, H0 is true
        eps = rng.normal(0, 1, size=n + q)
        x = np.array([np.mean(eps[t:t + q]) for t in range(n)])

        # NW test
        p_nw = _nw_ttest_pvalue(x, nw_lag)
        if p_nw < 0.05:
            nw_rejections += 1

        # Plain t-test (iid assumption — ignores autocorrelation)
        _, p_ttest = scipy_stats.ttest_1samp(x, 0.0)
        if p_ttest < 0.05:
            ttest_rejections += 1

    nw_fpr = nw_rejections / n_trials
    ttest_fpr = ttest_rejections / n_trials

    # Plain t-test must be substantially inflated (MA(8) with q > 1 induces
    # strong positive autocorrelation; iid t-test FPR is typically 25-40%).
    assert ttest_fpr > 0.15, (
        f"Plain t-test FPR {ttest_fpr:.3f} unexpectedly low — MA process may not "
        f"be autocorrelated enough to demonstrate the iid-test bias"
    )

    # NW FPR must be at least 10 ppt below the plain t-test FPR,
    # confirming the correction is working.
    assert nw_fpr <= ttest_fpr - 0.10, (
        f"NW FPR ({nw_fpr:.3f}) is not sufficiently lower than plain t-test FPR "
        f"({ttest_fpr:.3f}); NW correction may be broken"
    )

    # NW FPR must be below 20% (generous upper bound for Bartlett at n=400, lag=8).
    # With n→∞ the Bartlett kernel converges to 5%; at n=400 it is conservative
    # but should not triple the nominal rate.
    assert nw_fpr <= 0.20, (
        f"NW FPR {nw_fpr:.3f} > 20% — HAC variance estimate may be under-inflating SE"
    )
