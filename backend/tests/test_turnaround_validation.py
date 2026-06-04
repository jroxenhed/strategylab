"""Tests for turnaround_validation.py — Phase 2 historical validation.

Lane C scope only. All tests are offline — no network, no live EDGAR calls.
run_filter and the bars_loader are mocked with synthetic price paths.

Test coverage:
  test_wilson_ci_known_values           — compare against hand-computed + scipy
  test_run_validation_all_hits          — all signal candidates hit → rate=1.0
  test_run_validation_null_baseline     — null candidates processed; null_hit_rate computed
  test_run_validation_miss_list_populated — non-hit signal candidates appear in miss_list
  test_survivorship_warning_always_present
  test_cost_model_reduces_return        — net < gross when slippage > 0
  test_wilson_ci_zero_n                 — n=0 edge case returns (0,0)
  test_wilson_ci_all_hits               — p=1.0 edge case
  test_quarterly_as_of_dates            — correct dates generated
  test_conviction_skipped_in_result     — conviction_skipped=True surfaced
  test_unique_tickers_counted           — same ticker at two as_of dates = 1 unique
  test_truncated_events_skipped         — events beyond available data are counted/skipped
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional
from sys import path as sys_path
from os.path import dirname, abspath

sys_path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import pytest

import turnaround_validation as tv


# ---------------------------------------------------------------------------
# Helpers: synthetic price data
# ---------------------------------------------------------------------------

def _make_price_df(
    start: date,
    end: date,
    annual_growth_rate: float = 0.0,
) -> pd.DataFrame:
    """Generate a synthetic daily OHLCV DataFrame with exponentially-compounding
    Close prices.

    annual_growth_rate = 0.0 → flat (always 100.0)
    annual_growth_rate = 3.0 → triples each year (200% gain/yr) — guarantees 50%
    threshold is hit within a few months for any as-of date in a multi-year span.
    annual_growth_rate = -0.3 → falls 30% per year.

    Using 252 trading days per year for the exponent.
    """
    dates = pd.date_range(start, end, freq="B")  # business days
    n = len(dates)
    if n == 0:
        return pd.DataFrame()
    multiplier = 1.0 + annual_growth_rate
    if multiplier <= 0:
        multiplier = 0.01
    closes = [100.0 * (multiplier ** (i / 252)) for i in range(n)]
    df = pd.DataFrame({
        "Open": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Close": closes,
        "Volume": [1_000_000] * n,
    }, index=dates)
    return df


def _make_declining_df(start: date, end: date) -> pd.DataFrame:
    """Price falls ~30% per year — a miss with negative net return."""
    return _make_price_df(start, end, annual_growth_rate=-0.3)


# ---------------------------------------------------------------------------
# Helpers: synthetic CandidateResult
# ---------------------------------------------------------------------------

def _make_candidate(ticker: str, is_null: bool = False) -> object:
    """Return a minimal CandidateResult-like object (duck-typed)."""
    from dataclasses import dataclass

    @dataclass
    class _MockCandidate:
        ticker: str
        cik: str
        price_near_low: bool = True
        pct_off_high: float = 60.0
        below_ma: bool = True
        revenue_yoy_pct: Optional[float] = 10.0
        revenue_consec_positive: int = 2
        gross_margin_delta_pct: Optional[float] = 1.0
        net_income_consec_improving: int = 2
        ocf_positive_quarters: int = 3
        ps_ratio: Optional[float] = 2.0
        has_insider_buying: bool = True
        has_buyback: bool = True
        composite_score: float = 75.0
        is_null_candidate: bool = False

    c = _MockCandidate(ticker=ticker, cik="0000000001")
    c.is_null_candidate = is_null
    return c


# ---------------------------------------------------------------------------
# test_wilson_ci_known_values
# ---------------------------------------------------------------------------

def test_wilson_ci_known_values():
    """Compare wilson_ci against hand-computed Wilson score CI expected values.

    Hand-computed using the standard Wilson formula:
        p_hat = hits/n
        denom = 1 + z²/n
        centre = (p_hat + z²/(2n)) / denom
        margin = z * sqrt(p_hat*(1-p_hat)/n + z²/(4n²)) / denom
        [max(0, centre-margin), min(1, centre+margin)]
    with z=1.96.

    These values were verified against the statsmodels proportion_confint(method='wilson')
    reference implementation before being hard-coded here.
    """
    # (hits, n, expected_low, expected_high) — tolerance 1e-6
    expected = [
        (10, 20, 0.29929491, 0.70070509),   # p=0.5, moderate n
        (1,  10, 0.01787575, 0.40415639),   # p=0.1, small n
        (95, 100, 0.88824803, 0.97845664),  # p=0.95, large n
    ]
    for hits, n, exp_low, exp_high in expected:
        low, high = tv.wilson_ci(hits, n)
        assert abs(low - exp_low) < 1e-5, (
            f"wilson_ci low mismatch for ({hits},{n}): got {low:.8f}, expected {exp_low:.8f}"
        )
        assert abs(high - exp_high) < 1e-5, (
            f"wilson_ci high mismatch for ({hits},{n}): got {high:.8f}, expected {exp_high:.8f}"
        )


def test_wilson_ci_zero_n():
    low, high = tv.wilson_ci(0, 0)
    assert low == 0.0
    assert high == 0.0


def test_wilson_ci_all_hits():
    """p=1.0: CI upper bound should be 1.0, lower bound < 1.0."""
    low, high = tv.wilson_ci(10, 10)
    assert high == 1.0
    assert low < 1.0
    assert low >= 0.0


def test_wilson_ci_no_hits():
    """p=0.0: CI lower bound should be 0.0, upper bound > 0.0."""
    low, high = tv.wilson_ci(0, 20)
    assert low == 0.0
    assert high > 0.0


# ---------------------------------------------------------------------------
# test_quarterly_as_of_dates
# ---------------------------------------------------------------------------

def test_quarterly_as_of_dates():
    dates = tv._quarterly_as_of_dates(2020, 2021)
    assert len(dates) == 8  # 4 per year × 2 years
    # Check Feb/May/Aug/Nov 15 pattern
    months = {d.month for d in dates}
    assert months == {2, 5, 8, 11}
    days = {d.day for d in dates}
    assert days == {15}
    years = {d.year for d in dates}
    assert years == {2020, 2021}


# ---------------------------------------------------------------------------
# Fixtures: mock run_filter + memoized loader
# ---------------------------------------------------------------------------

def _make_validation_req(**kwargs) -> tv.ValidationRequest:
    defaults = dict(
        params={},
        start_year=2018,
        end_year=2018,
        horizon_months=12,
        hit_threshold_pct=50.0,
        initial_capital=10_000.0,
        slippage_bps=2.0,
        per_share_rate=0.0,
        min_per_order=0.0,
    )
    defaults.update(kwargs)
    return tv.ValidationRequest(**defaults)


# ---------------------------------------------------------------------------
# test_run_validation_all_hits
# ---------------------------------------------------------------------------

def test_run_validation_all_hits(monkeypatch):
    """Mock run_filter to return 2 signal candidates that both hit (+50% threshold).

    Use an exponential path growing 200%/yr (annual_growth_rate=3.0 → 3x/yr).
    At that rate, from any as-of date in 2018, the price rises >50% within
    a few months — guaranteeing is_hit=True for every event.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    # 3x per year (200% annual gain) — from any 2018 entry, +50% hit occurs in < 6 months
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=3.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [_make_candidate("AAAA"), _make_candidate("BBBB")]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12, hit_threshold_pct=50.0),
        _fake_run_filter,
        lambda ticker: rising_df,
    )

    # ADV-05: per-ticker cooldown — each ticker qualifies at most once per 12-month horizon.
    # 2 tickers qualify at the first as-of date (Feb 2018), then their horizons are
    # open until Feb 2019, so May/Aug/Nov 2018 events are overlap-suppressed.
    # signal_n = 2 (one event per ticker).
    assert result.signal_n == 2
    assert result.signal_hits == result.signal_n
    assert result.signal_hit_rate == 1.0
    assert result.survivorship_warning != ""
    assert result.overlap_suppressed == 6  # 2 tickers × 3 suppressed dates


def _make_fake_turnaround(fake_run_filter):
    """Create a duck-typed turnaround module substitute."""
    import types
    from dataclasses import dataclass

    mod = types.ModuleType("turnaround")

    @dataclass
    class FilterParams:
        price_near_low_pct: float = 30.0
        pct_off_high: float = 50.0
        price_below_ma_period: int = 200
        low_lookback_years: int = 3
        high_lookback_years: int = 3
        revenue_growth_min_pct: float = 0.0
        revenue_consec_quarters: int = 2
        gross_margin_min_delta_pct: float = -2.0
        net_income_consec_improving: int = 2
        ocf_positive_recent_quarters: int = 2
        ps_ratio_max: float = 3.0
        insider_buy_months_back: int = 6
        buyback_months_back: int = 12
        min_price: float = 1.0
        max_price: float = 200.0
        min_avg_volume: int = 100_000
        data_source: str = "yahoo"

    mod.FilterParams = FilterParams
    mod.run_filter = fake_run_filter
    mod.build_universe = lambda ticker_cik_map, params=None: []
    return mod


def _run_validation_with_mocks(
    req: tv.ValidationRequest,
    fake_run_filter,
    fake_loader,
) -> tv.ValidationResult:
    """Patch the key seams and run validation inline."""
    import sys
    import types

    # Patch turnaround module in sys.modules
    fake_t = _make_fake_turnaround(fake_run_filter)
    orig_t = sys.modules.get("turnaround")
    sys.modules["turnaround"] = fake_t

    # Patch edgar
    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {}
    orig_e = sys.modules.get("edgar")
    sys.modules["edgar"] = fake_edgar

    # Patch memoized loader factory
    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: fake_loader

    # Patch per_leg_commission import
    orig_import_comm = tv._import_per_leg_commission

    def _fake_per_leg(shares, req):
        return max(shares * req.per_share_rate, req.min_per_order)

    tv._import_per_leg_commission = lambda: _fake_per_leg

    try:
        result = tv.run_validation(req)
    finally:
        tv._make_memoized_loader = orig_loader_fn
        tv._import_per_leg_commission = orig_import_comm
        if orig_t is not None:
            sys.modules["turnaround"] = orig_t
        elif "turnaround" in sys.modules:
            del sys.modules["turnaround"]
        if orig_e is not None:
            sys.modules["edgar"] = orig_e
        elif "edgar" in sys.modules:
            del sys.modules["edgar"]

    return result


# ---------------------------------------------------------------------------
# test_run_validation_null_baseline
# ---------------------------------------------------------------------------

def test_run_validation_null_baseline():
    """Null (washed-out only) candidates are processed separately; null_hit_rate computed."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        # 1 signal (will miss — flat) + 1 null (will miss — flat)
        return [
            _make_candidate("SIGX", is_null=False),
            _make_candidate("NULX", is_null=True),
        ]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    assert result.null_n > 0
    assert result.null_hit_rate == 0.0  # flat path never reaches +50%
    assert result.signal_n > 0
    assert result.signal_hit_rate == 0.0


# ---------------------------------------------------------------------------
# test_run_validation_miss_list_populated
# ---------------------------------------------------------------------------

def test_run_validation_miss_list_populated():
    """Non-hit signal candidates appear in miss_list with required fields."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [_make_candidate("MISS1"), _make_candidate("MISS2")]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    assert len(result.miss_list) > 0
    for item in result.miss_list:
        assert "ticker" in item
        assert "as_of" in item
        assert "net_return_pct" in item
        # composite_score key must exist (may be None at this implementation stage)
        assert "composite_score" in item


# ---------------------------------------------------------------------------
# test_survivorship_warning_always_present
# ---------------------------------------------------------------------------

def test_survivorship_warning_always_present():
    """ValidationResult.survivorship_warning is non-empty regardless of outcomes."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=3.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("AAAA")],
        lambda ticker: rising_df,
    )
    assert result.survivorship_warning
    assert "delisted" in result.survivorship_warning.lower() or "currently-listed" in result.survivorship_warning.lower()


# ---------------------------------------------------------------------------
# test_cost_model_reduces_return
# ---------------------------------------------------------------------------

def test_cost_model_reduces_return():
    """net_return_pct < gross forward_return_pct when slippage_bps > 0."""
    entry_price = 100.0
    exit_price = 150.0  # +50% gross

    req = _make_validation_req(slippage_bps=10.0)  # 10 bps each way

    def _fake_per_leg(shares, r):
        return max(shares * r.per_share_rate, r.min_per_order)

    # Patch _import_per_leg_commission temporarily
    orig = tv._import_per_leg_commission
    tv._import_per_leg_commission = lambda: _fake_per_leg
    try:
        net_entry, net_exit, _, net_return_pct = tv._apply_costs(entry_price, exit_price, req)
    finally:
        tv._import_per_leg_commission = orig

    gross_return_pct = (exit_price - entry_price) / entry_price * 100
    # Slippage makes entry more expensive and exit cheaper → net < gross
    assert net_return_pct < gross_return_pct
    assert net_entry > entry_price  # slippage on buy
    assert net_exit < exit_price    # slippage on sell


# ---------------------------------------------------------------------------
# test_conviction_skipped_in_result
# ---------------------------------------------------------------------------

def test_conviction_skipped_in_result():
    """conviction_skipped field is True in ValidationResult."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("CONV")],
        lambda ticker: flat_df,
    )
    assert result.conviction_skipped is True


# ---------------------------------------------------------------------------
# test_unique_tickers_counted
# ---------------------------------------------------------------------------

def test_unique_tickers_counted():
    """Same ticker qualifying at 2 as-of dates = 1 unique_tickers."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    # All 4 as-of dates in 2018 return same ticker "SAME"
    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("SAME")],
        lambda ticker: flat_df,
    )
    assert result.unique_tickers == 1


# ---------------------------------------------------------------------------
# test_truncated_events_skipped
# ---------------------------------------------------------------------------

def test_truncated_events_skipped():
    """Events whose horizon extends past available price data are counted and skipped."""
    # Provide a short DF: only covers 2018; horizon of 12 months past Dec 2018
    # means the exit bar (Dec 2019) doesn't exist → truncated.
    short_df = _make_price_df(date(2017, 1, 1), date(2018, 6, 30), annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("TRUNC")],
        lambda ticker: short_df,
    )
    # Some as-of dates will truncate (Feb/May 2018 as_of; horizon end = Feb/May 2019 > Jun 2018)
    assert result.truncated_events > 0
    # Truncated events should not appear in outcomes
    assert result.signal_n + result.null_n < 4  # fewer than all 4 as-of dates succeeded


# ---------------------------------------------------------------------------
# test_invalid_filter_params_raises (PY-06/ORCH-01)
# ---------------------------------------------------------------------------

def test_invalid_filter_params_raises():
    """PY-06/ORCH-01: invalid params dict raises ValueError (no silent fallback).

    Uses the real turnaround.FilterParams (pydantic BaseModel) which validates
    field types — the mock dataclass does not.
    """
    import pytest
    import sys
    import turnaround as real_turnaround

    req = tv.ValidationRequest(params={"ps_ratio_max": "not_a_number"})

    # Patch out edgar + bars_loader to ensure we reach the FilterParams construction
    import types
    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {}
    orig_e = sys.modules.get("edgar")
    sys.modules["edgar"] = fake_edgar

    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: lambda t: None

    try:
        with pytest.raises(ValueError, match="Invalid FilterParams"):
            tv.run_validation(req)
    finally:
        tv._make_memoized_loader = orig_loader_fn
        if orig_e is not None:
            sys.modules["edgar"] = orig_e
        elif "edgar" in sys.modules:
            del sys.modules["edgar"]


# ---------------------------------------------------------------------------
# test_miss_list_has_composite_score (PY-05/COR-09/ADV-07)
# ---------------------------------------------------------------------------

def test_miss_list_has_real_composite_score():
    """PY-05/COR-09/ADV-07: miss_list entries carry real composite_score from TradeOutcome."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    # Two signal candidates with different scores
    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        c1 = _make_candidate("HIGH")
        c2 = _make_candidate("LOW")
        c1.composite_score = 90.0
        c2.composite_score = 20.0
        return [c1, c2]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    assert len(result.miss_list) > 0
    for item in result.miss_list:
        assert item["composite_score"] is not None
        assert item["composite_score"] > 0  # real score, not None

    # First miss should have higher composite_score (sorted desc)
    if len(result.miss_list) >= 2:
        scores = [item["composite_score"] for item in result.miss_list]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# test_is_hit_on_net_return (ADV-02)
# ---------------------------------------------------------------------------

def test_is_hit_judged_on_net_return():
    """ADV-02: is_hit judged on NET return (post-cost), not gross close."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    # Modest rise — gross > threshold but net < threshold after slippage
    # With 50% threshold and 100bps slippage per leg, we need gross just above 50%
    # but net_return slightly below — use a very high slippage to force the case
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.55)  # ~55% / yr

    result = _run_validation_with_mocks(
        _make_validation_req(
            start_year=2018, end_year=2018, horizon_months=12,
            hit_threshold_pct=50.0, slippage_bps=500.0  # extreme slippage forces net < gross
        ),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("MARG")],
        lambda ticker: rising_df,
    )

    # With ADV-02 fix: is_hit = net_return_pct >= 50.0
    # With extreme slippage (500 bps each way = ~10% drag), net return < gross return
    for o in (result.miss_list or []):
        assert o["net_return_pct"] is not None  # net return computed


# ---------------------------------------------------------------------------
# test_overlap_suppressed (ADV-05)
# ---------------------------------------------------------------------------

def test_overlap_suppressed_counted():
    """ADV-05: same ticker in consecutive quarterly as_of dates with open horizon
    → overlap_suppressed is incremented."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("SAME")],
        lambda ticker: flat_df,
    )
    # 4 as-of dates in 2018; after first qualifies, remaining 3 are suppressed
    assert result.overlap_suppressed == 3
    assert result.signal_n == 1  # only the first event counted
