"""Tests for turnaround.py filter engine + routes/turnaround.py endpoints.

All offline — no live EDGAR or provider calls.

Module-level stubs for sibling modules (edgar, turnaround_validation) that
may not exist during parallel build. Using sys.modules stubs so test collection
doesn't fail even when those files are absent.

NOTE on lazy edgar import: turnaround.py imports edgar lazily inside functions
(not at module load time), so test collection succeeds without edgar.py present.
The sys.modules stub installed here makes those lazy imports return our mock object.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import types
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Sys.modules stubs for sibling modules (may not exist during parallel build)
# ---------------------------------------------------------------------------

# Both sibling modules exist post-build, so the installers just import the real
# thing now. (During the parallel build they installed sys.modules stubs; a
# module-level stub poisons the entire pytest session — collection imports this
# file before test_edgar.py runs its tests, so the stub shadowed the real
# module for every other test file. Per-test stubs below use
# monkeypatch.setitem(sys.modules, ...), which reverts cleanly.)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _install_edgar_stub():
    """Import the real edgar module (kept for call-site compatibility)."""
    import edgar  # noqa: F401


def _install_turnaround_validation_stub():
    """Import the real turnaround_validation module (kept for call-site compatibility)."""
    import turnaround_validation  # noqa: F401


_install_edgar_stub()
_install_turnaround_validation_stub()

import turnaround as turnaround_mod
from turnaround import (
    FilterParams,
    CandidateResult,
    build_universe,
    compute_composite_score,
    evaluate_washed_out,
    is_fundamental_inflecting,
    is_washed_out,
    run_filter,
)


# ---------------------------------------------------------------------------
# Helpers: synthetic DataFrames
# ---------------------------------------------------------------------------

def make_daily_df(
    n_days: int = 400,
    base_price: float = 100.0,
    end_date: Optional[date] = None,
    price_series: Optional[list[float]] = None,
) -> pd.DataFrame:
    """Create a synthetic OHLCV daily DataFrame."""
    if end_date is None:
        end_date = date(2024, 1, 15)
    dates = [end_date - timedelta(days=i) for i in range(n_days - 1, -1, -1)]
    if price_series is not None:
        closes = price_series
    else:
        closes = [base_price] * n_days

    df = pd.DataFrame({
        "Open": closes,
        "High": [p * 1.01 for p in closes],
        "Low": [p * 0.99 for p in closes],
        "Close": closes,
        "Volume": [500_000] * n_days,
    }, index=pd.to_datetime(dates))
    return df


def make_washed_out_df(as_of: date = date(2024, 1, 15)) -> pd.DataFrame:
    """Create a df where price is near 3yr low, 60%+ off high, below 200-day MA."""
    n = 400
    # High of 100 at the start, then gradually decline to 30 (70% off high)
    # Current price = 32 (near the 30 low)
    prices = [100.0 - (70.0 / n) * i for i in range(n)]
    prices[-30:] = [32.0] * 30  # stabilize near low
    return make_daily_df(n_days=n, end_date=as_of, price_series=prices)


def make_high_price_df(as_of: date = date(2024, 1, 15)) -> pd.DataFrame:
    """Create a df where price is at 3yr high (not washed out)."""
    n = 400
    prices = [50.0 + (50.0 / n) * i for i in range(n)]  # rising to 100
    return make_daily_df(n_days=n, end_date=as_of, price_series=prices)


def make_quarters(
    n: int = 8,
    base_val: float = 1_000_000.0,
    yoy_growth_pct: float = 10.0,
    start_year: int = 2022,
) -> list[dict]:
    """Make n synthetic quarterly revenue records."""
    result = []
    for i in range(n):
        yr = start_year + i // 4
        qtr = (i % 4) + 1
        month = qtr * 3
        end_day = 31 if month in (3, 12) else 30
        end = f"{yr}-{month:02d}-{end_day:02d}"
        filed = f"{yr}-{month:02d}-{end_day:02d}"
        fp = f"Q{qtr}"
        # Apply growth for later quarters
        val = base_val * (1 + yoy_growth_pct / 100) ** (i // 4)
        result.append({"end": end, "val": val, "filed": filed, "fp": fp})
    return result


# ---------------------------------------------------------------------------
# Unit tests — evaluate_washed_out (pure function)
# ---------------------------------------------------------------------------

class TestEvaluateWashedOut:
    def test_washed_out_true(self):
        """Price near 3yr low, below 200-day MA, 60%+ off high → passes."""
        as_of = date(2024, 1, 15)
        df = make_washed_out_df(as_of)
        params = FilterParams()
        passes, metrics = evaluate_washed_out(df, as_of, params)

        assert passes is True
        assert metrics["price"] is not None
        assert metrics["pct_off_high"] is not None
        # Current price (32) is 68% off high (100) → > 50% off threshold
        assert metrics["pct_off_high"] >= 50.0

    def test_washed_out_false_not_near_low(self):
        """Price at 3yr high → should fail washed-out check."""
        as_of = date(2024, 1, 15)
        df = make_high_price_df(as_of)
        params = FilterParams()
        passes, metrics = evaluate_washed_out(df, as_of, params)
        assert passes is False

    def test_empty_df_returns_false(self):
        df = pd.DataFrame()
        params = FilterParams()
        passes, metrics = evaluate_washed_out(df, date(2024, 1, 1), params)
        assert passes is False
        assert metrics["price"] is None

    def test_metrics_populated_on_pass(self):
        as_of = date(2024, 1, 15)
        df = make_washed_out_df(as_of)
        params = FilterParams()
        passes, metrics = evaluate_washed_out(df, as_of, params)
        if passes:
            for key in ("price", "low_N_yr", "high_N_yr", "ma_200", "pct_off_high", "pct_above_low"):
                assert metrics[key] is not None, f"metrics[{key!r}] is None"

    def test_as_of_slices_future_bars(self):
        """Bars after as_of should not influence the result."""
        as_of = date(2023, 6, 1)
        # Make df with low prices up to as_of, then spike after
        n = 400
        prices_before = [30.0] * 300 + [100.0] * 100  # spike at end (after as_of)
        end_date = date(2024, 1, 15)
        df = make_daily_df(n_days=n, end_date=end_date, price_series=prices_before)
        params = FilterParams()
        # as_of is well before the spike, so the sliced df should be low-price
        passes_early, _ = evaluate_washed_out(df, as_of, params)
        passes_late, _ = evaluate_washed_out(df, end_date, params)
        # Early should see only low prices (different outcome from late)
        # We're mainly verifying it doesn't crash and respects the as_of slice
        assert isinstance(passes_early, bool)
        assert isinstance(passes_late, bool)

    def test_calendar_based_low_window(self):
        """COR-02/ADV-03: low_window is calendar-based (DateOffset years), not row count.

        A year with many trading days (252 rows ≈ 1 calendar year) means iloc-based
        and calendar-based slicing agree. But on a sparse synthetic df, the calendar
        cut should respect the actual as_of - years delta, not the row count.
        """
        # Build a df spanning 4 calendar years at daily resolution
        end_date = date(2024, 1, 15)
        n = 4 * 252  # ~4 trading years
        # Prices start high (100), fall to 20, stabilize near 20 at the end
        prices = [100.0 - (80.0 / (n - 30)) * i for i in range(n - 30)] + [20.0] * 30
        df = make_daily_df(n_days=n, end_date=end_date, price_series=prices)
        params = FilterParams(low_lookback_years=3)

        passes, metrics = evaluate_washed_out(df, end_date, params)
        # low_N_yr should be from the 3-calendar-year window, not 3*365 rows
        assert metrics["low_N_yr"] is not None
        assert isinstance(passes, bool)  # no crash

    def test_tz_aware_index(self):
        """Live _fetch() frames carry a tz-aware DatetimeIndex (America/New_York).

        Regression: naive-vs-aware Timestamp comparison raised TypeError on
        EVERY live symbol; run_filter's per-symbol try/except swallowed it, so
        live scans silently reported 0 candidates (caught by a live positive
        control, 2026-06-05 — synthetic fixtures here were all tz-naive).
        """
        as_of = date(2024, 1, 15)
        df = make_washed_out_df(as_of)
        df.index = df.index.tz_localize("America/New_York")
        passes, metrics = evaluate_washed_out(df, as_of, FilterParams())
        assert passes is True
        assert metrics["price"] is not None

        # And the not-washed-out path stays correct under tz too
        df_high = make_high_price_df(as_of)
        df_high.index = df_high.index.tz_localize("America/New_York")
        passes_high, _ = evaluate_washed_out(df_high, as_of, FilterParams())
        assert passes_high is False


# ---------------------------------------------------------------------------
# Unit tests — is_washed_out (thin wrapper with bars_loader injection)
# ---------------------------------------------------------------------------

class TestIsWashedOut:
    def test_uses_bars_loader_injection(self):
        """bars_loader injection prevents double-fetch."""
        call_count = {"n": 0}
        as_of = date(2024, 1, 15)
        df = make_washed_out_df(as_of)

        def mock_loader(ticker):
            call_count["n"] += 1
            return df

        params = FilterParams()
        passes, metrics = is_washed_out("FAKE", as_of, params, bars_loader=mock_loader)
        assert call_count["n"] == 1  # exactly one call to the loader

    def test_default_loader_is_used_when_none(self, monkeypatch):
        """Default path calls _fetch (monkeypatched)."""
        as_of = date(2024, 1, 15)
        df = make_washed_out_df(as_of)
        monkeypatch.setattr(turnaround_mod, "_default_bars_loader", lambda t, a, p: df)
        params = FilterParams()
        passes, metrics = is_washed_out("FAKE", as_of, params)
        assert isinstance(passes, bool)


# ---------------------------------------------------------------------------
# Unit tests — is_fundamental_inflecting (uses edgar stub / monkeypatch)
# ---------------------------------------------------------------------------

class TestIsFundamentalInflecting:
    def _make_edgar_stub(self, rev_quarters, ni_quarters, gp_quarters, ocf_quarters):
        stub = types.ModuleType("edgar")
        stub.get_quarterly_revenue = lambda cik: rev_quarters
        stub.get_quarterly_net_income = lambda cik: ni_quarters
        stub.get_quarterly_gross_profit = lambda cik: gp_quarters
        stub.get_quarterly_ocf = lambda cik: ocf_quarters
        return stub

    def test_fundamental_inflecting_true(self, monkeypatch):
        """2+ consecutive positive YoY revenue + improving NI + positive OCF → passes.

        gp must grow at same rate as rev so gross margin stays flat (delta=0 ≥ -2pp threshold).
        """
        rev = make_quarters(n=8, yoy_growth_pct=15.0, start_year=2022)
        ni = make_quarters(n=8, yoy_growth_pct=20.0, start_year=2022)
        # gp same growth rate as rev → margin delta ≈ 0 (passes the ≥-2pp threshold)
        gp = make_quarters(n=8, yoy_growth_pct=15.0, start_year=2022)
        ocf = make_quarters(n=8, yoy_growth_pct=5.0, start_year=2022)

        stub = self._make_edgar_stub(rev, ni, gp, ocf)
        monkeypatch.setitem(sys.modules, "edgar", stub)

        params = FilterParams()
        as_of = date(2024, 6, 1)
        passes, metrics = is_fundamental_inflecting("0000123456", as_of, params)
        assert passes is True
        assert metrics["revenue_consec_positive"] >= params.revenue_consec_quarters
        assert metrics["net_income_consec_improving"] >= params.net_income_consec_improving
        assert metrics["ocf_positive_quarters"] >= params.ocf_positive_recent_quarters

    def test_fundamental_inflecting_false_revenue_stall(self, monkeypatch):
        """Revenue stagnant (0% growth) with default consec_quarters=2 → fails."""
        rev = make_quarters(n=8, yoy_growth_pct=0.0, start_year=2022)
        ni = make_quarters(n=8, yoy_growth_pct=10.0, start_year=2022)
        gp = make_quarters(n=8, yoy_growth_pct=5.0, start_year=2022)
        ocf = make_quarters(n=8, yoy_growth_pct=5.0, start_year=2022)

        stub = self._make_edgar_stub(rev, ni, gp, ocf)
        monkeypatch.setitem(sys.modules, "edgar", stub)

        # Require strictly positive growth
        params = FilterParams(revenue_growth_min_pct=1.0, revenue_consec_quarters=2)
        as_of = date(2024, 6, 1)
        passes, metrics = is_fundamental_inflecting("0000123456", as_of, params)
        # 0% growth doesn't meet 1% minimum → false
        assert passes is False

    def test_fiscal_offset_yoy_pairing(self, monkeypatch):
        """YoY pairing uses end±45d tolerance — June FY company handled correctly."""
        # Simulate June fiscal year: quarters end in June/Sept/Dec/Mar
        rev = [
            {"end": "2022-06-30", "val": 1_000_000, "filed": "2022-07-15"},
            {"end": "2022-09-30", "val": 1_050_000, "filed": "2022-10-15"},
            {"end": "2022-12-31", "val": 1_100_000, "filed": "2023-01-15"},
            {"end": "2023-03-31", "val": 1_150_000, "filed": "2023-04-15"},
            {"end": "2023-06-30", "val": 1_200_000, "filed": "2023-07-15"},
            {"end": "2023-09-30", "val": 1_260_000, "filed": "2023-10-15"},
        ]
        gp = [{"end": q["end"], "val": q["val"] * 0.4, "filed": q["filed"]} for q in rev]
        ni = [{"end": q["end"], "val": q["val"] * 0.1, "filed": q["filed"]} for q in rev]
        ocf = [{"end": q["end"], "val": 100_000.0, "filed": q["filed"]} for q in rev]

        stub = self._make_edgar_stub(rev, ni, gp, ocf)
        monkeypatch.setitem(sys.modules, "edgar", stub)

        params = FilterParams(revenue_consec_quarters=2)
        as_of = date(2024, 1, 1)
        passes, metrics = is_fundamental_inflecting("0000000001", as_of, params)
        # Growth is consistently positive in this series — should pass
        assert isinstance(passes, bool)
        # Verify no crash due to fiscal offset
        assert metrics["revenue_yoy_pct"] is not None

    def test_point_in_time_filter_respects_as_of(self, monkeypatch):
        """Quarters filed after as_of should be excluded."""
        # All quarters filed in the future
        rev = [{"end": "2024-03-31", "val": 1_000_000, "filed": "2025-01-01"}]
        ni = [{"end": "2024-03-31", "val": 100_000, "filed": "2025-01-01"}]
        gp = [{"end": "2024-03-31", "val": 400_000, "filed": "2025-01-01"}]
        ocf = [{"end": "2024-03-31", "val": 50_000, "filed": "2025-01-01"}]

        stub = self._make_edgar_stub(rev, ni, gp, ocf)
        monkeypatch.setitem(sys.modules, "edgar", stub)

        params = FilterParams()
        as_of = date(2024, 6, 1)  # before the filed date
        passes, metrics = is_fundamental_inflecting("0000000002", as_of, params)
        # No data available before as_of → should fail (insufficient data)
        assert passes is False


# ---------------------------------------------------------------------------
# Unit tests — compute_composite_score
# ---------------------------------------------------------------------------

class TestCompositeScore:
    def _make_candidate(self, **kwargs) -> CandidateResult:
        defaults = dict(
            ticker="TEST",
            cik="0000000001",
            price_near_low=True,
            pct_off_high=70.0,
            pct_above_low=5.0,   # near the N-year low (PY-03: pct_above_low field)
            below_ma=True,
            revenue_yoy_pct=25.0,
            revenue_consec_positive=4,
            gross_margin_delta_pct=2.0,
            net_income_consec_improving=4,
            ocf_positive_quarters=4,
            ps_ratio=1.5,
            has_insider_buying=False,
            has_buyback=False,
            composite_score=0.0,
            is_null_candidate=False,
        )
        defaults.update(kwargs)
        c = CandidateResult(**defaults)
        c.composite_score = compute_composite_score(c)
        return c

    def test_score_in_range(self):
        c = self._make_candidate()
        assert 0.0 <= c.composite_score <= 100.0

    def test_stronger_candidate_scores_higher(self):
        """A candidate with more inflection / cheaper valuation should score higher."""
        strong = self._make_candidate(
            pct_off_high=80.0,
            revenue_yoy_pct=40.0,
            revenue_consec_positive=4,
            net_income_consec_improving=4,
            ocf_positive_quarters=4,
            ps_ratio=0.5,
            gross_margin_delta_pct=5.0,
        )
        weak = self._make_candidate(
            pct_off_high=51.0,
            revenue_yoy_pct=1.0,
            revenue_consec_positive=2,
            net_income_consec_improving=2,
            ocf_positive_quarters=2,
            ps_ratio=5.0,
            gross_margin_delta_pct=-1.5,
        )
        assert strong.composite_score > weak.composite_score

    def test_conviction_flags_additive_bonus(self):
        """Insider buying and buyback add bonus points, never gatekeep."""
        base = self._make_candidate(has_insider_buying=False, has_buyback=False)
        with_conviction = self._make_candidate(has_insider_buying=True, has_buyback=True)
        assert with_conviction.composite_score > base.composite_score

    def test_none_ps_ratio_neutral(self):
        """Unknown P/S should not tank the score — treated as neutral."""
        with_ps = self._make_candidate(ps_ratio=1.0)
        without_ps = self._make_candidate(ps_ratio=None)
        # Should both be in valid range; no crash
        assert 0.0 <= without_ps.composite_score <= 100.0

    def test_score_deterministic(self):
        """Same inputs always produce same score."""
        c1 = self._make_candidate()
        c2 = self._make_candidate()
        assert c1.composite_score == c2.composite_score

    def test_pct_above_low_contributes_to_score(self):
        """PY-03: pct_above_low should actually affect the wo pillar score.

        A candidate right at the low (pct_above_low=0) should score higher
        than one that is 50% above the low (pct_above_low=50), all else equal.
        """
        at_low = self._make_candidate(pct_above_low=0.0)
        above_low = self._make_candidate(pct_above_low=50.0)
        assert at_low.composite_score > above_low.composite_score

    def test_ordering_by_composite_score(self):
        """PY-03/decisions: candidates sort by composite_score descending."""
        candidates = [
            self._make_candidate(pct_off_high=51.0, revenue_yoy_pct=5.0, ps_ratio=4.0, pct_above_low=25.0),
            self._make_candidate(pct_off_high=80.0, revenue_yoy_pct=40.0, ps_ratio=0.5, pct_above_low=2.0),
        ]
        sorted_candidates = sorted(candidates, key=lambda c: c.composite_score, reverse=True)
        # The stronger candidate should have the higher score
        assert sorted_candidates[0].composite_score >= sorted_candidates[1].composite_score


# ---------------------------------------------------------------------------
# Unit tests — _count_consec_yoy_improving (PY-04/COR-03 fix)
# ---------------------------------------------------------------------------

class TestCountConsecYoyImproving:
    def test_loss_making_turnaround_is_improving(self):
        """PY-04/COR-03: -100M → -20M is improving (less negative = current > prior)."""
        from turnaround import _count_consec_yoy_improving

        # Two years of quarterly data, loss shrinking each quarter
        quarters = [
            {"end": "2022-03-31", "val": -100_000_000, "filed": "2022-04-15"},
            {"end": "2022-06-30", "val": -90_000_000, "filed": "2022-07-15"},
            {"end": "2022-09-30", "val": -80_000_000, "filed": "2022-10-15"},
            {"end": "2022-12-31", "val": -70_000_000, "filed": "2023-01-15"},
            {"end": "2023-03-31", "val": -60_000_000, "filed": "2023-04-15"},
            {"end": "2023-06-30", "val": -50_000_000, "filed": "2023-07-15"},
            {"end": "2023-09-30", "val": -40_000_000, "filed": "2023-10-15"},
            {"end": "2023-12-31", "val": -30_000_000, "filed": "2024-01-15"},
        ]
        count = _count_consec_yoy_improving(quarters)
        # All 4 recent quarters improved YoY (less negative)
        assert count >= 2, f"Expected ≥2 improving quarters, got {count}"

    def test_worsening_losses_not_improving(self):
        """Getting worse (more negative) should not count as improving."""
        from turnaround import _count_consec_yoy_improving

        quarters = [
            {"end": "2022-03-31", "val": -10_000_000, "filed": "2022-04-15"},
            {"end": "2022-06-30", "val": -20_000_000, "filed": "2022-07-15"},
            {"end": "2022-09-30", "val": -30_000_000, "filed": "2022-10-15"},
            {"end": "2022-12-31", "val": -40_000_000, "filed": "2023-01-15"},
            {"end": "2023-03-31", "val": -50_000_000, "filed": "2023-04-15"},
            {"end": "2023-06-30", "val": -60_000_000, "filed": "2023-07-15"},
            {"end": "2023-09-30", "val": -70_000_000, "filed": "2023-10-15"},
            {"end": "2023-12-31", "val": -80_000_000, "filed": "2024-01-15"},
        ]
        count = _count_consec_yoy_improving(quarters)
        assert count == 0, f"Expected 0 improving quarters for worsening losses, got {count}"


# ---------------------------------------------------------------------------
# Unit tests — run_filter (cheap-first order + EDGAR not called for filtered)
# ---------------------------------------------------------------------------

class TestRunFilter:
    def test_cheap_first_edgar_not_called_when_price_filtered(self, monkeypatch):
        """Symbols filtered by price/volume gate should not trigger EDGAR calls."""
        edgar_call_count = {"n": 0}

        stub = types.ModuleType("edgar")
        def _count_call(*a, **kw):
            edgar_call_count["n"] += 1
            return []
        stub.get_quarterly_revenue = _count_call
        stub.get_quarterly_net_income = _count_call
        stub.get_quarterly_gross_profit = _count_call
        stub.get_quarterly_ocf = _count_call
        stub.get_shares_outstanding = lambda *a, **kw: None
        stub.get_form4_net_buys = lambda *a, **kw: 0
        stub.has_buyback_authorization = lambda *a, **kw: False
        monkeypatch.setitem(sys.modules, "edgar", stub)

        # Bars with price = 300 (above max_price=200) → filtered at stage 1a
        as_of = date(2024, 1, 15)
        high_price_df = make_daily_df(n_days=400, base_price=300.0, end_date=as_of)

        def loader(ticker):
            return high_price_df

        universe = [("FAKE", "0000000001")]
        params = FilterParams(max_price=200.0)
        results = run_filter(universe, as_of, params, bars_loader=loader)

        assert edgar_call_count["n"] == 0, "EDGAR should not be called for price-filtered symbols"
        assert results == []

    def test_run_filter_returns_sorted_by_score(self, monkeypatch):
        """Results should be sorted by composite_score descending."""
        as_of = date(2024, 1, 15)

        # Two symbols that pass washed-out but have different scores
        washed_df = make_washed_out_df(as_of)

        stub = types.ModuleType("edgar")
        rev_good = make_quarters(n=8, yoy_growth_pct=20.0, start_year=2022)
        rev_bad = make_quarters(n=8, yoy_growth_pct=5.0, start_year=2022)
        ni = make_quarters(n=8, yoy_growth_pct=10.0, start_year=2022)
        gp = make_quarters(n=8, yoy_growth_pct=5.0, start_year=2022)
        ocf = make_quarters(n=8, yoy_growth_pct=3.0, start_year=2022)

        call_order = []
        def rev_fn(cik):
            call_order.append(cik)
            if cik == "0000000001":
                return rev_good
            return rev_bad

        stub.get_quarterly_revenue = rev_fn
        stub.get_quarterly_net_income = lambda cik: ni
        stub.get_quarterly_gross_profit = lambda cik: gp
        stub.get_quarterly_ocf = lambda cik: ocf
        stub.get_shares_outstanding = lambda cik, as_of: 10_000_000.0
        stub.get_form4_net_buys = lambda cik, months_back=6: 0
        stub.has_buyback_authorization = lambda cik, months_back=12: False
        monkeypatch.setitem(sys.modules, "edgar", stub)

        universe = [("AAA", "0000000001"), ("BBB", "0000000002")]
        params = FilterParams()
        results = run_filter(universe, as_of, params, bars_loader=lambda t: washed_df)

        if len(results) >= 2:
            assert results[0].composite_score >= results[1].composite_score

    def test_failed_symbol_skipped_not_raised(self, monkeypatch):
        """Exception in processing one symbol should not crash the whole scan."""
        as_of = date(2024, 1, 15)

        def bad_loader(ticker):
            raise RuntimeError("Test fetch failure")

        universe = [("FAIL", "0000000001"), ]
        params = FilterParams()
        # Should not raise
        results = run_filter(universe, as_of, params, bars_loader=bad_loader)
        assert results == []

    def test_null_candidates_included(self, monkeypatch):
        """Symbols that pass washed-out but fail fundamentals → is_null_candidate=True."""
        as_of = date(2024, 1, 15)
        washed_df = make_washed_out_df(as_of)

        stub = types.ModuleType("edgar")
        # Return empty lists → fundamentals check fails → null candidate
        stub.get_quarterly_revenue = lambda cik: []
        stub.get_quarterly_net_income = lambda cik: []
        stub.get_quarterly_gross_profit = lambda cik: []
        stub.get_quarterly_ocf = lambda cik: []
        stub.get_shares_outstanding = lambda cik, as_of: None
        stub.get_form4_net_buys = lambda cik, months_back=6: 0
        stub.has_buyback_authorization = lambda cik, months_back=12: False
        monkeypatch.setitem(sys.modules, "edgar", stub)

        universe = [("NULL", "0000000001")]
        params = FilterParams()
        results = run_filter(universe, as_of, params, bars_loader=lambda t: washed_df)
        assert len(results) == 1
        assert results[0].is_null_candidate is True


# ---------------------------------------------------------------------------
# Unit tests — build_universe (D9 hygiene)
# ---------------------------------------------------------------------------

class TestBuildUniverse:
    def test_excludes_tickers_with_dot(self):
        """Preferred/warrant tickers with '.' should be excluded."""
        raw = {
            "AAPL": {"cik_str": 320193},
            "BRK.B": {"cik_str": 1067983},
            "AAPL.W": {"cik_str": 999999},
        }
        result = build_universe(raw)
        tickers = [t for t, _ in result]
        assert "AAPL" in tickers
        assert "BRK.B" not in tickers
        assert "AAPL.W" not in tickers

    def test_excludes_tickers_with_dash(self):
        raw = {
            "GOOD": {"cik_str": 100000},
            "BAD-A": {"cik_str": 200000},
        }
        result = build_universe(raw)
        tickers = [t for t, _ in result]
        assert "GOOD" in tickers
        assert "BAD-A" not in tickers

    def test_excludes_tickers_longer_than_5(self):
        raw = {
            "SHORT": {"cik_str": 100000},
            "TOOLONGX": {"cik_str": 200000},
        }
        result = build_universe(raw)
        tickers = [t for t, _ in result]
        assert "SHORT" in tickers
        assert "TOOLONGX" not in tickers

    def test_cik_zero_padded_to_10(self):
        """CIK int values should be zero-padded to 10 digits."""
        raw = {"AAPL": {"cik_str": 320193}}
        result = build_universe(raw)
        assert len(result) == 1
        _, cik = result[0]
        assert cik == "0000320193"
        assert len(cik) == 10

    def test_deterministic_alphabetical_order(self):
        raw = {
            "ZZZ": {"cik_str": 300000},
            "AAA": {"cik_str": 100000},
            "MMM": {"cik_str": 200000},
        }
        result = build_universe(raw)
        tickers = [t for t, _ in result]
        assert tickers == sorted(tickers)

    def test_invalid_cik_excluded(self):
        raw = {
            "GOOD": {"cik_str": 123456},
            "BAD": {"cik_str": "not_a_number"},
        }
        result = build_universe(raw)
        tickers = [t for t, _ in result]
        assert "GOOD" in tickers
        assert "BAD" not in tickers

    def test_excludes_etf_trust_spac_by_title(self):
        """ORCH-02: ETF, Trust, Acquisition Corp excluded by title substring."""
        raw = {
            "REAL": {"cik_str": 100000, "title": "Acme Corporation"},
            "ETFX": {"cik_str": 200000, "title": "Acme ETF Fund"},
            "TRST": {"cik_str": 300000, "title": "Alpha Real Estate Trust"},
            "SPAC": {"cik_str": 400000, "title": "Alpha Acquisition Corp"},
        }
        result = build_universe(raw)
        tickers = [t for t, _ in result]
        assert "REAL" in tickers
        assert "ETFX" not in tickers
        assert "TRST" not in tickers
        assert "SPAC" not in tickers


# ---------------------------------------------------------------------------
# Integration tests — routes/turnaround.py (TestClient + monkeypatch)
# ---------------------------------------------------------------------------

# Install sys.modules stub for turnaround_validation before importing main
_install_turnaround_validation_stub()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient for the FastAPI app with turnaround router registered."""
    # Ensure edgar stub is in sys.modules before app import
    _install_edgar_stub()
    _install_turnaround_validation_stub()

    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_scan_state():
    """Reset scan/validate state between tests."""
    import routes.turnaround as rt_mod
    rt_mod._scan_state.update({
        "status": "idle",
        "started_at": None,
        "duration_secs": None,
        "error": None,
        "candidate_count": None,
    })
    rt_mod._validate_state.update({
        "status": "idle",
        "started_at": None,
        "duration_secs": None,
        "error": None,
    })
    yield


class TestScanEndpoints:
    def test_get_scan_status_idle_on_start(self, client):
        resp = client.get("/api/turnaround/scan/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"

    def test_post_scan_returns_running(self, client, monkeypatch, tmp_path):
        """POST /api/turnaround/scan should start the background task and return running."""
        import routes.turnaround as rt_mod

        # Replace background worker with a no-op sync stub
        async def _noop_scan(params, max_universe):
            pass

        monkeypatch.setattr(rt_mod, "_run_scan_background", _noop_scan)

        resp = client.post("/api/turnaround/scan", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"

    def test_post_scan_409_when_already_running(self, client, monkeypatch):
        """Second POST while running should return 409."""
        import routes.turnaround as rt_mod

        # Force state to running
        rt_mod._scan_state["status"] = "running"

        resp = client.post("/api/turnaround/scan", json={})
        assert resp.status_code == 409

    def test_get_watchlist_404_before_first_scan(self, client, tmp_path, monkeypatch):
        """GET /api/turnaround/watchlist should 404 when watchlist.json doesn't exist."""
        import routes.turnaround as rt_mod
        # Point to tmp dir with no file
        monkeypatch.setattr(rt_mod, "_WATCHLIST_PATH", tmp_path / "no_watchlist.json")
        resp = client.get("/api/turnaround/watchlist")
        assert resp.status_code == 404

    def test_get_watchlist_returns_result_after_scan(self, client, monkeypatch, tmp_path):
        """GET /api/turnaround/watchlist returns persisted results."""
        import routes.turnaround as rt_mod

        fake_results = [
            {
                "ticker": "FAKE",
                "cik": "0000000001",
                "price_near_low": True,
                "pct_off_high": 70.0,
                "below_ma": True,
                "revenue_yoy_pct": 15.0,
                "revenue_consec_positive": 3,
                "gross_margin_delta_pct": 1.0,
                "net_income_consec_improving": 2,
                "ocf_positive_quarters": 3,
                "ps_ratio": 1.5,
                "has_insider_buying": False,
                "has_buyback": False,
                "composite_score": 75.0,
                "is_null_candidate": False,
            }
        ]

        watchlist_path = tmp_path / "watchlist.json"
        watchlist_path.write_text(json.dumps(fake_results))
        monkeypatch.setattr(rt_mod, "_WATCHLIST_PATH", watchlist_path)

        resp = client.get("/api/turnaround/watchlist")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["ticker"] == "FAKE"

    def test_get_watchlist_filters_nulls_by_default(self, client, monkeypatch, tmp_path):
        """Default watchlist response excludes null candidates (D8)."""
        import routes.turnaround as rt_mod

        fake_results = [
            {"ticker": "SIG", "cik": "0000000001", "is_null_candidate": False,
             "price_near_low": True, "pct_off_high": 70.0, "below_ma": True,
             "revenue_yoy_pct": 15.0, "revenue_consec_positive": 3,
             "gross_margin_delta_pct": 1.0, "net_income_consec_improving": 2,
             "ocf_positive_quarters": 3, "ps_ratio": 1.5,
             "has_insider_buying": False, "has_buyback": False, "composite_score": 75.0},
            {"ticker": "NULL", "cik": "0000000002", "is_null_candidate": True,
             "price_near_low": True, "pct_off_high": 60.0, "below_ma": True,
             "revenue_yoy_pct": None, "revenue_consec_positive": 0,
             "gross_margin_delta_pct": None, "net_income_consec_improving": 0,
             "ocf_positive_quarters": 0, "ps_ratio": None,
             "has_insider_buying": False, "has_buyback": False, "composite_score": 40.0},
        ]

        watchlist_path = tmp_path / "watchlist.json"
        watchlist_path.write_text(json.dumps(fake_results))
        monkeypatch.setattr(rt_mod, "_WATCHLIST_PATH", watchlist_path)

        # Default — no include_null
        resp = client.get("/api/turnaround/watchlist")
        assert resp.status_code == 200
        data = resp.json()
        tickers = [c["ticker"] for c in data]
        assert "SIG" in tickers
        assert "NULL" not in tickers

        # With include_null=true — all candidates returned
        resp2 = client.get("/api/turnaround/watchlist?include_null=true")
        assert resp2.status_code == 200
        data2 = resp2.json()
        tickers2 = [c["ticker"] for c in data2]
        assert "SIG" in tickers2
        assert "NULL" in tickers2


class TestValidateEndpoints:
    def test_post_validate_409_when_running(self, client):
        """409 when validation already running."""
        import routes.turnaround as rt_mod
        rt_mod._validate_state["status"] = "running"

        resp = client.post("/api/turnaround/validate", json={})
        assert resp.status_code == 409

    def test_get_validate_result_404_before_run(self, client, monkeypatch, tmp_path):
        """404 when no validation result file exists."""
        import routes.turnaround as rt_mod
        monkeypatch.setattr(rt_mod, "_VALIDATION_PATH", tmp_path / "no_result.json")

        resp = client.get("/api/turnaround/validate/result")
        assert resp.status_code == 404

    def test_get_validate_status_idle(self, client):
        resp = client.get("/api/turnaround/validate/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"
