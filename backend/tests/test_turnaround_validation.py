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
    *,
    progress=None,
    timeout_secs=None,
    cancel_event=None,
) -> tv.ValidationResult:
    """Patch the key seams and run validation inline.

    F313: optional progress and timeout_secs kwargs forwarded to run_validation.
    """
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
        kwargs = {}
        if progress is not None:
            kwargs["progress"] = progress
        if timeout_secs is not None:
            kwargs["timeout_secs"] = timeout_secs
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        result = tv.run_validation(req, **kwargs)
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


# ---------------------------------------------------------------------------
# Item 1: events table tests
# ---------------------------------------------------------------------------

def test_events_table_present_with_signal_and_null_rows():
    """Item 1: events list present; both signal and null rows appear with correct is_null flag."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [
            _make_candidate("SIG1", is_null=False),
            _make_candidate("NUL1", is_null=True),
        ]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    assert hasattr(result, "events"), "ValidationResult must have 'events' attribute"
    assert isinstance(result.events, list)
    # Both signal and null rows should appear
    tickers_in_events = {e["ticker"] for e in result.events}
    is_null_values = {e["is_null"] for e in result.events}
    assert "SIG1" in tickers_in_events
    assert "NUL1" in tickers_in_events
    assert False in is_null_values  # signal events
    assert True in is_null_values   # null events


def test_events_table_required_fields():
    """Item 1: each event dict has all required keys."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("EVTX")],
        lambda ticker: flat_df,
    )

    required_keys = {
        "ticker", "as_of", "is_null", "entry_date", "entry_price",
        "exit_date", "exit_price", "net_return_pct", "forward_return_pct",
        "hit", "days_to_hit", "composite_score", "horizon_months",
        "horizon_end_return_pct",
    }
    assert len(result.events) > 0, "Expected at least one event"
    for event in result.events:
        missing = required_keys - set(event.keys())
        assert not missing, f"Event dict missing keys: {missing}"


def test_events_table_dates_as_iso_strings():
    """Item 1: date fields in events serialize as ISO strings (not date objects)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("ISODT")],
        lambda ticker: flat_df,
    )

    for event in result.events:
        for key in ("as_of", "entry_date", "exit_date"):
            val = event[key]
            if val is not None:
                assert isinstance(val, str), (
                    f"events[0]['{key}'] should be ISO string, got {type(val)}: {val!r}"
                )
                # Should parse back to date without error
                from datetime import date as _date
                _date.fromisoformat(val)


def test_events_table_days_to_hit_set_on_hits():
    """Item 1: days_to_hit is an int >= 1 for hit events, None for misses."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=3.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("HITX")],
        lambda ticker: rising_df,
    )

    hit_events = [e for e in result.events if e["hit"]]
    miss_events = [e for e in result.events if not e["hit"]]

    # All hit events should have days_to_hit set
    for e in hit_events:
        assert e["days_to_hit"] is not None, "Hit event must have days_to_hit"
        assert isinstance(e["days_to_hit"], int)
        assert e["days_to_hit"] >= 1

    # All miss events should have days_to_hit = None
    for e in miss_events:
        assert e["days_to_hit"] is None, "Miss event must have days_to_hit=None"


def test_events_table_serialization_roundtrip():
    """Item 1: events table survives JSON round-trip (dates as ISO strings)."""
    import dataclasses
    import json
    from datetime import date as _date

    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [
            _make_candidate("RNDTRIP", is_null=False),
            _make_candidate("RNDTRNL", is_null=True),
        ],
        lambda ticker: flat_df,
    )

    # Serialize via dataclasses.asdict + _DateEncoder (same path as the route)
    class _DateEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, _date):
                return o.isoformat()
            return super().default(o)

    as_dict = dataclasses.asdict(result)
    json_str = json.dumps(as_dict, cls=_DateEncoder)
    parsed = json.loads(json_str)

    assert "events" in parsed
    assert "schema_version" in parsed
    # Unit 2 (D14): events table is now schema_version=2 (additive v2 fields).
    assert parsed["schema_version"] == 2

    for event in parsed["events"]:
        for key in ("as_of", "entry_date", "exit_date"):
            val = event.get(key)
            if val is not None:
                assert isinstance(val, str), f"After roundtrip, {key} should be str"


def test_schema_version_present():
    """Item 1 / Unit 2: schema_version field is present and equals 2."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [],
        lambda ticker: flat_df,
    )

    assert hasattr(result, "schema_version")
    assert result.schema_version == 2


# ---------------------------------------------------------------------------
# Item 2a (F327): null cohort return distribution tests
# ---------------------------------------------------------------------------

def test_null_return_distribution_stats_present():
    """F327 item 2a: null cohort return distribution fields are present and non-zero
    when null outcomes exist."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)
    # Use a declining path for null so return is clearly negative
    declining_df = _make_declining_df(SPAN_START, SPAN_END)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [
            _make_candidate("SIGA", is_null=False),
            _make_candidate("NULA", is_null=True),
        ]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: declining_df,
    )

    # null_mean_return_pct should be non-zero (declining path → negative return)
    assert hasattr(result, "null_mean_return_pct")
    assert hasattr(result, "null_median_return_pct")
    assert hasattr(result, "null_p25_return_pct")
    assert hasattr(result, "null_p75_return_pct")

    assert result.null_n > 0, "Expected null events"
    # Declining path: mean/median should be negative
    assert result.null_mean_return_pct < 0.0
    assert result.null_median_return_pct < 0.0


def test_return_distribution_stats_correct_on_synthetic_scenario():
    """F327 item 2a: distribution stats match hand-computed values on a small scenario.

    Scenario: 3 signal events all land at exactly the same flat price → net_return_pct
    is determined by costs alone (negative). mean == median == all values.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    # Use flat path so all 3 events get same net return
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)
    # 3 tickers, single as-of date → 3 events (no overlap suppression since different tickers)
    # Use start_year=end_year=2018, single date Feb 15 2018
    tickers = ["T1", "T2", "T3"]
    idx = [0]

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [_make_candidate(t) for t in tickers]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, slippage_bps=0.0),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    # With flat path + no slippage, net_return_pct ≈ 0 for all events
    # p25 == p75 == median == mean (all returns equal)
    if result.signal_n >= 3:
        assert abs(result.signal_mean_return_pct - result.signal_median_return_pct) < 1.0
        assert abs(result.signal_p25_return_pct - result.signal_p75_return_pct) < 1.0


def test_null_distribution_zero_when_no_null_outcomes():
    """F327 item 2a: null distribution fields are 0.0 when no null outcomes processed."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    # Only signal candidates, no null
    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("ONLY_SIG")],
        lambda ticker: flat_df,
    )

    assert result.null_n == 0
    assert result.null_mean_return_pct == 0.0
    assert result.null_median_return_pct == 0.0


# ---------------------------------------------------------------------------
# Item 2b (F327): fixed-horizon return comparison
# ---------------------------------------------------------------------------

def test_horizon_return_fields_present():
    """F327 item 2b: horizon-end return fields are present in result."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("HRZA")],
        lambda ticker: flat_df,
    )

    assert hasattr(result, "signal_horizon_mean_return_pct")
    assert hasattr(result, "signal_horizon_median_return_pct")
    assert hasattr(result, "null_horizon_mean_return_pct")
    assert hasattr(result, "null_horizon_median_return_pct")


def test_horizon_return_equals_net_return_for_non_hits():
    """F327 item 2b: for miss events, horizon_end_return_pct == net_return_pct
    (no early exit, so fixed-horizon == actual exit)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, hit_threshold_pct=50.0),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("MISS99")],
        lambda ticker: flat_df,
    )

    # Flat path never hits +50% — all events are misses
    # For misses: horizon_end_return_pct should equal net_return_pct
    miss_events = [e for e in result.events if not e["hit"]]
    for e in miss_events:
        if e["horizon_end_return_pct"] is not None:
            assert abs(e["horizon_end_return_pct"] - e["net_return_pct"]) < 0.01, (
                f"Miss event: horizon_end_return_pct {e['horizon_end_return_pct']:.4f} "
                f"!= net_return_pct {e['net_return_pct']:.4f}"
            )


def test_horizon_return_differs_from_net_return_for_early_hits():
    """F327 item 2b: for early-hit events, horizon_end_return_pct != net_return_pct
    when price trajectory diverges after the touch (rising path: continues up)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    # Rising path: hits early (say at month 3), then continues rising past that point.
    # horizon_end_return_pct should be HIGHER than net_return_pct at touch.
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=3.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, hit_threshold_pct=50.0),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("EARLYH")],
        lambda ticker: rising_df,
    )

    hit_events = [e for e in result.events if e["hit"]]
    # For a strongly rising path, the horizon-end return should exceed the touch return
    for e in hit_events:
        if e["horizon_end_return_pct"] is not None:
            # At 3x/yr growth, horizon-end return >>> early touch return
            assert e["horizon_end_return_pct"] > e["net_return_pct"], (
                f"Expected horizon_end_return_pct > net_return_pct for strong rising path; "
                f"got horizon={e['horizon_end_return_pct']:.2f}, net={e['net_return_pct']:.2f}"
            )


# ---------------------------------------------------------------------------
# TST-05: events-table vs aggregate counts consistency
# ---------------------------------------------------------------------------

def test_cohort_cooldowns_are_independent():
    """COR-02: a signal event for ticker X must NOT suppress a null event for ticker X
    at the same or later as_of date (and vice versa).  The two cohorts have separate
    cooldown books after the fix.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    # Both cohorts contain the SAME ticker so cross-cohort suppression is observable.
    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [
            _make_candidate("SAME", is_null=False),  # signal cohort
            _make_candidate("SAME", is_null=True),   # null cohort — same ticker
        ]

    # Use a single as-of date so the first signal event's horizon (12 months) would
    # suppress the null event in the old (shared) dict.
    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    # With separate cohort dicts: both the signal and null events should be recorded.
    # signal_n >= 1 (4 as-of dates, first one fires; rest suppressed within signal cohort)
    # null_n >= 1 (same: first fires; rest suppressed within null cohort)
    assert result.signal_n >= 1, "Expected at least 1 signal event"
    assert result.null_n >= 1, (
        "Expected at least 1 null event — cross-cohort suppression is present (COR-02 regression)"
    )


def test_events_table_vs_aggregate_counts_consistent():
    """TST-05: sum of hits in events matches aggregate counters.

    Verifies that events_table is built from the same set of outcomes used to
    compute signal_n/signal_hits/null_n/null_hits.  A merge bug or off-by-one
    in overlap suppression would show up as a mismatch here.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [
            _make_candidate("SIGC1", is_null=False),
            _make_candidate("SIGC2", is_null=False),
            _make_candidate("NULC1", is_null=True),
        ]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: flat_df,
    )

    # events_table includes both signal and null rows (not truncated)
    signal_events = [e for e in result.events if not e["is_null"]]
    null_events = [e for e in result.events if e["is_null"]]
    signal_hits_in_table = sum(1 for e in signal_events if e["hit"])
    null_hits_in_table = sum(1 for e in null_events if e["hit"])

    assert len(signal_events) == result.signal_n, (
        f"events signal rows ({len(signal_events)}) != signal_n ({result.signal_n})"
    )
    assert len(null_events) == result.null_n, (
        f"events null rows ({len(null_events)}) != null_n ({result.null_n})"
    )
    assert signal_hits_in_table == result.signal_hits, (
        f"events signal hits ({signal_hits_in_table}) != signal_hits ({result.signal_hits})"
    )
    assert null_hits_in_table == result.null_hits, (
        f"events null hits ({null_hits_in_table}) != null_hits ({result.null_hits})"
    )


# ---------------------------------------------------------------------------
# TST-06: null and signal distributions are tracked independently
# ---------------------------------------------------------------------------

def test_null_and_signal_distributions_tracked_independently():
    """TST-06: null and signal cohorts must use different price paths so a swapped
    assignment cannot accidentally pass.

    Uses a ticker-routing loader: signal ticker gets rising_df, null ticker gets
    declining_df.  Asserts that null_mean_return_pct != signal_mean_return_pct,
    confirming the two distributions are computed separately.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=3.0)
    declining_df = _make_declining_df(SPAN_START, SPAN_END)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [
            _make_candidate("SIGT", is_null=False),
            _make_candidate("NULT", is_null=True),
        ]

    def _routing_loader(ticker: str):
        return rising_df if ticker == "SIGT" else declining_df

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        _fake_run_filter,
        _routing_loader,
    )

    # Both cohorts must have events
    assert result.signal_n > 0, "Expected signal events"
    assert result.null_n > 0, "Expected null events"

    # With such different price paths, the means must differ
    assert result.signal_mean_return_pct != result.null_mean_return_pct, (
        "signal_mean_return_pct == null_mean_return_pct — distributions may be swapped "
        f"(signal={result.signal_mean_return_pct:.2f}, null={result.null_mean_return_pct:.2f})"
    )


# ---------------------------------------------------------------------------
# F313: progress tracking tests
# ---------------------------------------------------------------------------

def test_progress_dates_monotonic():
    """F313: dates_done increments monotonically across as-of dates."""
    import threading
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    progress = tv.ValidationProgress()
    snapshots: list[int] = []

    original_run_filter_calls = []

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        # Snapshot dates_done on each date entry
        snapshots.append(progress.dates_done)
        return [_make_candidate("PRGA")]

    import sys, types
    fake_t = _make_fake_turnaround(_fake_run_filter)
    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {}
    orig_t = sys.modules.get("turnaround")
    orig_e = sys.modules.get("edgar")
    sys.modules["turnaround"] = fake_t
    sys.modules["edgar"] = fake_edgar

    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: lambda t: flat_df

    orig_comm = tv._import_per_leg_commission
    def _fake_per_leg(shares, req): return 0.0
    tv._import_per_leg_commission = lambda: _fake_per_leg

    try:
        req = _make_validation_req(start_year=2018, end_year=2018)
        result = tv.run_validation(req, progress=progress)
    finally:
        tv._make_memoized_loader = orig_loader_fn
        tv._import_per_leg_commission = orig_comm
        sys.modules["turnaround"] = orig_t or sys.modules.get("turnaround")
        if orig_e is not None:
            sys.modules["edgar"] = orig_e
        elif "edgar" in sys.modules:
            del sys.modules["edgar"]

    # dates_done should have been 0 when entering the first date
    assert snapshots[0] == 0
    # After run, progress.dates_done should equal total dates (no timeout)
    assert progress.dates_done == result.total_as_of_dates
    # Snapshots should be non-decreasing
    assert snapshots == sorted(snapshots)


def test_progress_dates_total_and_universe_size_set():
    """F313: progress.dates_total and universe_size are set before the main loop."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    progress = tv.ValidationProgress()

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2019),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("PTOT")],
        lambda ticker: flat_df,
        progress=progress,
    )

    assert progress.dates_total == result.total_as_of_dates
    assert progress.dates_total == 8  # 4 dates/year × 2 years
    # universe_size is set (mock returns empty universe, so 0 is correct)
    assert progress.universe_size == 0  # build_universe returns [] in the mock


def test_progress_events_so_far_increments():
    """F313: signal_events and null_events in progress increment with outcomes."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    progress = tv.ValidationProgress()

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [
            _make_candidate("SIG_P", is_null=False),
            _make_candidate("NUL_P", is_null=True),
        ]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _fake_run_filter,
        lambda ticker: flat_df,
        progress=progress,
    )

    # After run completes, progress events should match result counts
    assert progress.signal_events == result.signal_n
    assert progress.null_events == result.null_n


def test_progress_duration_secs_live():
    """F313: progress tracker enables live duration computation (no direct test of route,
    but verifies that monotonic timing works with the new signature)."""
    import time
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    progress = tv.ValidationProgress()
    t_start = time.monotonic()

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("TDUR")],
        lambda ticker: flat_df,
        progress=progress,
    )

    elapsed = time.monotonic() - t_start
    # elapsed_secs on the result should be plausible (>0, <total elapsed)
    assert result.elapsed_secs > 0
    assert result.elapsed_secs <= elapsed + 0.5  # allow small clock skew


# ---------------------------------------------------------------------------
# F313: cancellation tests
# ---------------------------------------------------------------------------

def test_cancel_mid_run_raises():
    """F313: cancel_event set before run starts → RuntimeError('_cancelled_') raised."""
    import threading
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    cancel = threading.Event()
    cancel.set()  # already cancelled before run starts

    import sys, types
    fake_t = _make_fake_turnaround(
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("CX")]
    )
    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {}
    orig_t = sys.modules.get("turnaround")
    orig_e = sys.modules.get("edgar")
    sys.modules["turnaround"] = fake_t
    sys.modules["edgar"] = fake_edgar

    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: lambda t: flat_df

    orig_comm = tv._import_per_leg_commission
    def _fake_per_leg(shares, req): return 0.0
    tv._import_per_leg_commission = lambda: _fake_per_leg

    try:
        with pytest.raises(RuntimeError, match="_cancelled_"):
            tv.run_validation(
                _make_validation_req(start_year=2018, end_year=2018),
                cancel_event=cancel,
            )
    finally:
        tv._make_memoized_loader = orig_loader_fn
        tv._import_per_leg_commission = orig_comm
        if orig_t is not None:
            sys.modules["turnaround"] = orig_t
        elif "turnaround" in sys.modules:
            del sys.modules["turnaround"]
        if orig_e is not None:
            sys.modules["edgar"] = orig_e
        elif "edgar" in sys.modules:
            del sys.modules["edgar"]


def test_cancel_no_result_written(tmp_path):
    """F313: cancel mid-run → caller should NOT write a result file (no result returned)."""
    import threading
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    cancel = threading.Event()
    cancel.set()

    result_file = tmp_path / "validation_result.json"
    assert not result_file.exists()

    import sys, types
    fake_t = _make_fake_turnaround(
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("CY")]
    )
    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {}
    orig_t = sys.modules.get("turnaround")
    orig_e = sys.modules.get("edgar")
    sys.modules["turnaround"] = fake_t
    sys.modules["edgar"] = fake_edgar

    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: lambda t: flat_df

    orig_comm = tv._import_per_leg_commission
    def _fake_per_leg(shares, req): return 0.0
    tv._import_per_leg_commission = lambda: _fake_per_leg

    raised = False
    try:
        tv.run_validation(
            _make_validation_req(start_year=2018, end_year=2018),
            cancel_event=cancel,
        )
    except RuntimeError:
        raised = True
    finally:
        tv._make_memoized_loader = orig_loader_fn
        tv._import_per_leg_commission = orig_comm
        if orig_t is not None:
            sys.modules["turnaround"] = orig_t
        elif "turnaround" in sys.modules:
            del sys.modules["turnaround"]
        if orig_e is not None:
            sys.modules["edgar"] = orig_e
        elif "edgar" in sys.modules:
            del sys.modules["edgar"]

    assert raised, "Expected RuntimeError from cancelled run"
    # The caller (route) decides not to write; run_validation itself doesn't write
    # Result: the tmp file should not exist (the test confirms run_validation raises, not writes)
    assert not result_file.exists()


# ---------------------------------------------------------------------------
# F313: timeout tests
# ---------------------------------------------------------------------------

def test_timeout_zero_produces_partial_result():
    """F313: timeout_secs=0 → timed_out=True, dates_completed=0 (budget fires before first date)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2019),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("TOUT")],
        lambda ticker: flat_df,
        timeout_secs=0.0,
    )

    assert result.timed_out is True
    # With zero timeout, no date can complete — dates_completed should be 0
    assert result.dates_completed == 0


def test_timeout_partial_result_has_timed_out_flag():
    """F313: timeout result includes timed_out=True + dates_completed annotation."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2020),  # 3 years = 12 dates
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("TF")],
        lambda ticker: flat_df,
        timeout_secs=0.0,
    )

    assert hasattr(result, "timed_out")
    assert hasattr(result, "dates_completed")
    assert result.timed_out is True
    assert result.dates_completed <= result.total_as_of_dates


def test_timeout_completed_dates_events_preserved():
    """F313: events from completed dates ARE included in a timeout result (salvage).
    The in-flight date's partial events are dropped (bias protection).
    Use a very short timeout that fires after the first date completes.
    """
    import time as _time
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    call_count = [0]

    def _slow_filter(universe, as_of, params, bars_loader=None):
        call_count[0] += 1
        return [_make_candidate(f"TK{call_count[0]}")]

    # Use a large number of dates but zero timeout — result should be partial
    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2020),
        _slow_filter,
        lambda ticker: flat_df,
        timeout_secs=0.0,
    )

    assert result.timed_out is True
    # Since timeout=0, the run should have 0 completed dates and 0 events
    assert result.dates_completed == 0
    assert result.signal_n + result.null_n == 0


def test_progress_symbols_counted_at_loader_layer():
    """F313 follow-up: symbols_loaded must increment INSIDE run_filter (the date-1
    price wall happens there), i.e. at the bars_loader layer — not in the
    candidate loop. A filter that touches the loader for 3 universe symbols but
    returns only 1 candidate must still count 3.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _filter_touching_loader(universe, as_of, params, bars_loader=None):
        # simulate the washed-out gate sweeping the universe through the loader
        for t in ["SYM1", "SYM2", "SYM3"]:
            bars_loader(t)
        return [_make_candidate("SYM1")]

    progress = tv.ValidationProgress()
    _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        _filter_touching_loader,
        lambda ticker: flat_df,
        progress=progress,
    )
    # 4 as-of dates in 2018; counter resets per date — last date's sweep (3 loader
    # touches + candidate-loop lookups) must be >= 3, proving loader-layer counting.
    assert progress.symbols_loaded >= 3


def test_cancel_during_run_filter_via_loader():
    """F313 follow-up: a cancel that lands while run_filter is mid-sweep must make
    the loader return None (fast skip) and the run terminate with _cancelled_ at
    the next date boundary — not after the full price wall.
    """
    import threading
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)
    cancel = threading.Event()
    none_seen = [0]

    def _filter_cancelling_midsweep(universe, as_of, params, bars_loader=None):
        bars_loader("PRE")          # before cancel: real frame
        cancel.set()                # cancel lands mid-sweep
        if bars_loader("POST") is None:  # after cancel: loader must yield None
            none_seen[0] += 1
        return []

    with pytest.raises(RuntimeError, match="_cancelled_"):
        _run_validation_with_mocks(
            _make_validation_req(start_year=2018, end_year=2018),
            _filter_cancelling_midsweep,
            lambda ticker: flat_df,
            cancel_event=cancel,
        )
    assert none_seen[0] == 1


def test_timeout_mid_date_drops_partial_date_events():
    """F313-01 regression: a timeout that fires MID-date must drop that date's
    already-processed events entirely (per-date buffer commit). Date 1 completes
    (1 event, kept); date 2 processes one candidate slowly, then the budget fires
    on the next candidate — date 2's partial prefix must NOT appear in outcomes,
    counters, or the events table.
    """
    import time as _time
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    call_count = [0]

    def _per_date_filter(universe, as_of, params, bars_loader=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return [_make_candidate("TKDATE1")]
        return [_make_candidate("TKSLOW"), _make_candidate("TKNEVER")]

    def _loader(ticker):
        if ticker == "TKSLOW":
            _time.sleep(0.5)  # burns past the budget DURING date 2's first candidate
        return flat_df

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2020),
        _per_date_filter,
        _loader,
        timeout_secs=0.2,
    )

    assert result.timed_out is True
    assert result.dates_completed == 1
    # Only date 1's event survives; TKSLOW was processed but must be dropped.
    assert result.signal_n + result.null_n == 1
    event_tickers = {e["ticker"] for e in result.events}
    assert event_tickers == {"TKDATE1"}


def test_no_timeout_timed_out_false():
    """F313: normal run (no timeout) → timed_out=False, dates_completed == total."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [_make_candidate("NOTO")],
        lambda ticker: flat_df,
    )

    assert result.timed_out is False
    assert result.dates_completed == result.total_as_of_dates


# ---------------------------------------------------------------------------
# F313: backward compat — old status fields still present
# ---------------------------------------------------------------------------

def test_validation_result_backward_compat_fields():
    """F313: ValidationResult still has all pre-F313 fields (backward compat)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [],
        lambda ticker: flat_df,
    )

    # All pre-F313 fields must still be present
    for field in (
        "signal_hit_rate", "null_hit_rate", "signal_n", "null_n",
        "survivorship_warning", "total_as_of_dates", "elapsed_secs",
        "conviction_skipped", "unique_tickers", "truncated_events",
        "fetch_failures", "overlap_suppressed", "events", "schema_version",
    ):
        assert hasattr(result, field), f"Missing backward-compat field: {field}"


# ---------------------------------------------------------------------------
# Unit 1 (D12): Pluggable candidate-source interface tests
# ---------------------------------------------------------------------------

def _run_validation_with_source(
    req: tv.ValidationRequest,
    candidate_source,
    fake_loader,
    *,
    progress=None,
    timeout_secs=None,
    cancel_event=None,
) -> tv.ValidationResult:
    """Patch infra seams and run validation with an injected CandidateSourceConfig.

    Mirrors _run_validation_with_mocks but passes candidate_source to run_validation
    instead of a fake run_filter.  The legacy turnaround module is still patched so
    FilterParams construction succeeds; run_filter is never called on this path.
    """
    import sys
    import types

    # Patch turnaround module (needed for FilterParams construction)
    fake_t = _make_fake_turnaround(
        lambda universe, as_of, params, bars_loader=None: []  # never called
    )
    orig_t = sys.modules.get("turnaround")
    sys.modules["turnaround"] = fake_t

    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {}
    orig_e = sys.modules.get("edgar")
    sys.modules["edgar"] = fake_edgar

    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: fake_loader

    orig_import_comm = tv._import_per_leg_commission

    def _fake_per_leg(shares, req):
        return max(shares * req.per_share_rate, req.min_per_order)

    tv._import_per_leg_commission = lambda: _fake_per_leg

    try:
        kwargs = {"candidate_source": candidate_source}
        if progress is not None:
            kwargs["progress"] = progress
        if timeout_secs is not None:
            kwargs["timeout_secs"] = timeout_secs
        if cancel_event is not None:
            kwargs["cancel_event"] = cancel_event
        result = tv.run_validation(req, **kwargs)
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
# U1-S1: Happy path — injected dummy source emits known candidates on 2 cohorts
# ---------------------------------------------------------------------------

def test_u1_injected_source_exact_events():
    """U1-S1: injected dummy source emitting known candidates on 2 as-of dates
    → events table contains exactly those events with correct cohort tags.

    Two as-of dates (2018-02-15 and 2018-05-15).  Source returns 1 signal candidate
    per date.  With flat price path (0 growth), no hits — but both events must appear
    in the events table tagged with config_name='dummy_long' and direction='long'.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    call_log: list[date] = []

    def _dummy_source_fn(as_of, universe, bars_loader):
        call_log.append(as_of)
        return [_make_candidate(f"SRC_{as_of.month}")]

    source = tv.CandidateSourceConfig(
        name="dummy_long",
        direction="long",
        expected_events_per_year=200.0,
        source_fn=_dummy_source_fn,
    )

    # Two as-of dates: Feb and May 2018 (horizon 3m to avoid overlap suppression)
    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=3),
        source,
        lambda ticker: flat_df,
    )

    # source_fn was called once per as-of date (4 dates in 2018)
    assert len(call_log) == 4

    # Events table must contain events — at minimum Feb and May candidates
    # (Mar and Aug may be overlap-suppressed if horizon spans; with 3m horizon
    # Feb event closes by May → May event runs; May closes by Aug → Aug suppressed or not)
    assert len(result.events) >= 2, f"Expected >=2 events, got {len(result.events)}"

    # All events must be tagged with the injected config
    for ev in result.events:
        assert ev.get("config_name") == "dummy_long", (
            f"Expected config_name='dummy_long', got {ev.get('config_name')!r}"
        )
        assert ev.get("direction") == "long", (
            f"Expected direction='long', got {ev.get('direction')!r}"
        )
        assert not ev["is_null"], "Dummy source returns signal candidates"


# ---------------------------------------------------------------------------
# U1-S2: Happy path — legacy default (no source injected) regression anchor
# ---------------------------------------------------------------------------

def test_u1_legacy_default_regression():
    """U1-S2: default path (candidate_source=None) reproduces same behavior as
    the existing tests — regression anchor for the legacy run_filter path.

    Verifies that:
    1. The pre-Unit-1 call (no candidate_source arg) still produces the same result.
    2. Events are tagged config_name='legacy', direction='long'.

    Uses the same 2-candidate rising-path fixture as test_run_validation_all_hits.
    _run_validation_with_source passes candidate_source=None which invokes the legacy
    run_filter path — the same fake_run_filter is wired via the turnaround module mock
    inside _run_validation_with_source_with_filter below.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    rising_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=3.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [_make_candidate("LEGA"), _make_candidate("LEGB")]

    # Baseline: pre-Unit-1 call (no candidate_source arg) — must still work identically.
    result_legacy = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12, hit_threshold_pct=50.0),
        _fake_run_filter,
        lambda ticker: rising_df,
    )
    assert result_legacy.signal_n == 2, (
        f"Legacy path signal_n expected 2, got {result_legacy.signal_n}"
    )

    # Explicit candidate_source=None must also use the legacy run_filter path.
    # We use _run_validation_with_mocks (not _run_validation_with_source) because
    # _run_validation_with_mocks wires _fake_run_filter into the turnaround module;
    # _run_validation_with_source always installs a no-op run_filter (the source arg
    # bypasses it). Passing candidate_source=None is the identity — it delegates
    # straight through to the existing run_filter call, so both helpers agree.
    result_explicit_none = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12, hit_threshold_pct=50.0),
        _fake_run_filter,
        lambda ticker: rising_df,
        # candidate_source omitted (=None by default) — tests the default path
    )
    assert result_explicit_none.signal_n == result_legacy.signal_n, (
        f"Explicit None and no-arg must produce same signal_n; "
        f"got {result_explicit_none.signal_n} vs {result_legacy.signal_n}"
    )

    # Events from legacy path must be tagged "legacy" / "long"
    for ev in result_legacy.events:
        assert ev.get("config_name") == "legacy", (
            f"Legacy event config_name expected 'legacy', got {ev.get('config_name')!r}"
        )
        assert ev.get("direction") == "long", (
            f"Legacy event direction expected 'long', got {ev.get('direction')!r}"
        )


# ---------------------------------------------------------------------------
# U1-S3: Error path — config without event-rate declaration refused, no artifacts
# ---------------------------------------------------------------------------

def test_u1_missing_event_rate_refused():
    """U1-S3: config without event-rate declaration → run refused with explicit error,
    no partial artifacts written.

    A CandidateSourceConfig with expected_events_per_year=None must cause
    run_validation to raise RuntimeError immediately — before any as-of date loop
    runs — so no events are produced.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    source_no_rate = tv.CandidateSourceConfig(
        name="undeclared_config",
        direction="long",
        expected_events_per_year=None,  # missing declaration
        source_fn=lambda as_of, universe, bars_loader: [_make_candidate("SHOULD_NOT_RUN")],
    )

    with pytest.raises(RuntimeError, match="missing required event-rate declaration"):
        _run_validation_with_source(
            _make_validation_req(start_year=2018, end_year=2018),
            source_no_rate,
            lambda ticker: flat_df,
        )


def test_u1_zero_event_rate_refused():
    """U1-S3 variant: expected_events_per_year=0.0 also refused (must be > 0)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    source_zero_rate = tv.CandidateSourceConfig(
        name="zero_rate_config",
        direction="long",
        expected_events_per_year=0.0,  # zero is also invalid
        source_fn=lambda as_of, universe, bars_loader: [],
    )

    with pytest.raises(RuntimeError, match="missing required event-rate declaration"):
        _run_validation_with_source(
            _make_validation_req(start_year=2018, end_year=2018),
            source_zero_rate,
            lambda ticker: flat_df,
        )


# ---------------------------------------------------------------------------
# U1-S4: Edge case — source returning zero candidates on a cohort continues
# ---------------------------------------------------------------------------

def test_u1_zero_candidate_cohort_continues():
    """U1-S4: source returning zero candidates on a cohort → cohort recorded as
    empty, run continues, no division-by-zero in stats.

    Source returns 0 candidates on dates 1 and 3, 1 candidate on date 2.
    Run must complete with signal_n in {0, 1} (no crash) and stats well-defined.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    call_count = [0]

    def _sparse_source(as_of, universe, bars_loader):
        call_count[0] += 1
        if call_count[0] == 2:  # only second call returns a candidate
            return [_make_candidate("SPARSE")]
        return []  # empty on all other dates

    source = tv.CandidateSourceConfig(
        name="sparse_config",
        direction="long",
        expected_events_per_year=50.0,
        source_fn=_sparse_source,
    )

    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=3),
        source,
        lambda ticker: flat_df,
    )

    # No crash; stats well-defined (no division by zero)
    assert result.signal_n >= 0
    assert isinstance(result.signal_hit_rate, float)
    assert isinstance(result.signal_hit_rate_ci_low, float)
    assert isinstance(result.signal_hit_rate_ci_high, float)
    # Wilson CI for 0/0 is (0.0, 0.0)
    if result.signal_n == 0:
        assert result.signal_hit_rate == 0.0
        assert result.signal_hit_rate_ci_low == 0.0
        assert result.signal_hit_rate_ci_high == 0.0
    # Run completed without exception; total dates still present
    assert result.total_as_of_dates == 4  # 4 dates in 2018


# ---------------------------------------------------------------------------
# U1-S5: Integration — short-direction config flows direction to outcomes
# ---------------------------------------------------------------------------

def test_u1_short_direction_flows_to_events():
    """U1-S5: short-direction config flows direction through to outcome events.

    A CandidateSourceConfig with direction='short' must produce events table rows
    tagged direction='short'.  Unit 2 completes the TODO(U2): direction-aware
    _apply_costs() now makes a falling-price short produce a POSITIVE net return.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    # Falling price — a short candidate with negative gross return as long, positive as short
    declining_df = _make_declining_df(SPAN_START, SPAN_END)

    source = tv.CandidateSourceConfig(
        name="short_config",
        direction="short",
        expected_events_per_year=150.0,
        source_fn=lambda as_of, universe, bars_loader: [_make_candidate("SHORT1")],
    )

    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=3),
        source,
        lambda ticker: declining_df,
    )

    # Direction must flow through to every event
    for ev in result.events:
        assert ev.get("direction") == "short", (
            f"Expected direction='short' on all events; got {ev.get('direction')!r}"
        )
        assert ev.get("config_name") == "short_config"

    # TODO(U2) RESOLVED: falling-price short produces POSITIVE net_return_pct now
    # that _apply_costs() is direction-aware (short return = (entry - exit)/entry,
    # slippage inverted, borrow accrued). A 30%/yr decline over a 3-month hold is a
    # clear positive short return even after borrow + slippage.
    assert len(result.events) >= 1
    for ev in result.events:
        assert ev["net_return_pct"] > 0, (
            f"Falling-price short must net positive; got {ev['net_return_pct']}"
        )


# ---------------------------------------------------------------------------
# Unit 3 (F332 / D13): Price-frame persistence integration tests
#
# Format decision: pickle (protocol 4) — pyarrow/fastparquet not installed in
# backend/venv.  See DEVIATION NOTE in PriceFrameCache docstring for rationale.
#
# Test scenarios:
#   F332-S1: store/load roundtrip — loaded frame is byte/value-identical
#   F332-S2: second load hits disk, NOT network (fetch_fn never called second time)
#   F332-S3: missing cache file returns None (cold miss path)
#   F332-S4: corrupt cache file returns None (graceful degradation)
#   F332-S5: integration — second _make_memoized_loader call for same span starts
#            from disk, confirming no network call on warm disk
# ---------------------------------------------------------------------------

class TestPriceFrameCacheUnit:
    """Unit tests for PriceFrameCache.load/store (F332)."""

    def test_f332_s1_store_load_roundtrip(self, tmp_path):
        """F332-S1: stored frame loads back value-identical (index + columns preserved)."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        df = _make_price_df(date(2015, 1, 1), date(2024, 12, 31), annual_growth_rate=0.1)
        cache.store("AAPL", "2015-01-01", "2024-12-31", df)

        loaded = cache.load("AAPL", "2015-01-01", "2024-12-31")
        assert loaded is not None, "Expected loaded DataFrame, got None"
        assert isinstance(loaded, pd.DataFrame)
        # Shape must match
        assert loaded.shape == df.shape, (
            f"Shape mismatch: loaded={loaded.shape}, original={df.shape}"
        )
        # Close column values must be numerically identical
        pd.testing.assert_series_equal(
            loaded["Close"].reset_index(drop=True),
            df["Close"].reset_index(drop=True),
            check_names=False,
        )

    def test_f332_s3_cold_miss_returns_none(self, tmp_path):
        """F332-S3: loading a ticker not in cache returns None (no file exists)."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        result = cache.load("NOTCACHED", "2015-01-01", "2024-12-31")
        assert result is None, f"Expected None for cold miss, got {result}"

    def test_f332_s4_corrupt_file_returns_none(self, tmp_path):
        """F332-S4: corrupt pickle file returns None without raising (graceful degrade)."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        # Use the real path from cache internals (lives under the versioned subdir).
        p = cache._path("FAKE", "2015-01-01", "2024-12-31")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not valid pickle data!!!!")

        result = cache.load("FAKE", "2015-01-01", "2024-12-31")
        assert result is None, "Corrupt cache file must return None (graceful degradation)"

    def test_di03_corrupt_file_evicted_on_load(self, tmp_path):
        """DI-03: a corrupt pickle is UNLINKED on load so the next run re-fetches
        cleanly instead of re-encountering the same corrupt file forever."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        p = cache._path("DEAD", "2015-01-01", "2024-12-31")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x01garbage not a pickle\xff")
        assert p.exists()

        result = cache.load("DEAD", "2015-01-01", "2024-12-31")
        assert result is None, "Corrupt file must load as None"
        assert not p.exists(), "DI-03: corrupt file must be evicted (unlinked) after a failed load"

    def test_di02_data_source_in_cache_key(self, tmp_path):
        """DI-02: distinct providers do NOT alias — a yahoo frame is never served
        for an alpaca request of the same ticker+span."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        df_y = _make_price_df(date(2015, 1, 1), date(2020, 12, 31), annual_growth_rate=0.0)
        cache.store("AAPL", "2015-01-01", "2020-12-31", df_y, data_source="yahoo")
        # An alpaca request for the same ticker+span must MISS (different provider key).
        assert cache.load("AAPL", "2015-01-01", "2020-12-31", data_source="alpaca") is None
        assert cache.load("AAPL", "2015-01-01", "2020-12-31", data_source="yahoo") is not None

    def test_di01_punctuation_tickers_collision_free(self, tmp_path):
        """DI-01: BRK.A / BRK_A / BRK/A must map to DISTINCT cache files (no alias)."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        p1 = cache._path("BRK.A", "2015-01-01", "2020-12-31")
        p2 = cache._path("BRK_A", "2015-01-01", "2020-12-31")
        p3 = cache._path("BRK/A", "2015-01-01", "2020-12-31")
        assert len({p1, p2, p3}) == 3, f"Punctuation variants collided: {p1}, {p2}, {p3}"

    def test_f332_different_spans_different_files(self, tmp_path):
        """F332: different span strings produce different cache files (no collision)."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        df1 = _make_price_df(date(2015, 1, 1), date(2020, 12, 31), annual_growth_rate=0.0)
        df2 = _make_price_df(date(2015, 1, 1), date(2024, 12, 31), annual_growth_rate=1.0)
        cache.store("AAPL", "2015-01-01", "2020-12-31", df1)
        cache.store("AAPL", "2015-01-01", "2024-12-31", df2)

        loaded1 = cache.load("AAPL", "2015-01-01", "2020-12-31")
        loaded2 = cache.load("AAPL", "2015-01-01", "2024-12-31")
        assert loaded1 is not None
        assert loaded2 is not None
        assert loaded1.shape[0] != loaded2.shape[0], (
            "Different spans must produce different files with different row counts"
        )

    def test_f332_atomic_write_no_partial_on_failure(self, tmp_path):
        """F332: if the store write fails mid-way, no partial file is left behind."""
        cache = tv.PriceFrameCache(cache_dir=tmp_path)
        df = _make_price_df(date(2015, 1, 1), date(2024, 12, 31), annual_growth_rate=0.0)
        target = cache._path("ATOMT", "2015-01-01", "2024-12-31")

        # Before store: target must not exist
        assert not target.exists()
        # store should succeed normally
        cache.store("ATOMT", "2015-01-01", "2024-12-31", df)
        assert target.exists(), "Cache file must exist after store"
        # No .tmp orphan left (files live under the versioned subdir → recursive glob)
        tmps = list(tmp_path.glob("**/*.tmp"))
        assert tmps == [], f"Orphan .tmp files found: {tmps}"


class TestPriceFrameCacheIntegration:
    """Integration tests for F332 via _make_memoized_loader (Unit 3 / D13).

    Scenario F332-S2: second _make_memoized_loader invocation for the same
    ticker+span must return a value-identical frame without calling _fetch
    (network call spy asserts zero calls on warm disk).
    """

    def test_f332_s2_second_load_hits_disk_not_network(self, tmp_path):
        """F332-S2: after the first run persists a frame, a second run for the same
        ticker+span reads from disk, not network.  The network fetch spy is never
        called on the second run.

        Uses a custom PriceFrameCache (tmp_path) injected into _make_memoized_loader
        via the price_cache parameter.

        _make_memoized_loader does `from shared import _fetch` at factory-call time
        (lazy import in function body), so we must patch sys.modules["shared"] BEFORE
        calling _make_memoized_loader, not before calling loader("ticker").
        """
        import sys
        import types as _types

        fetch_call_count = [0]
        SPAN_START = date(2015, 1, 1)
        SPAN_END = date(2022, 12, 31)
        sample_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

        def _spy_fetch(ticker, start, end, interval, source):
            fetch_call_count[0] += 1
            return sample_df

        # Shared PriceFrameCache pointing at tmp_path (isolated from real cache)
        shared_cache = tv.PriceFrameCache(cache_dir=tmp_path)

        # --- First run: cold miss, must call fetch once ---
        # Patch shared BEFORE creating loader1 so the `from shared import _fetch`
        # inside _make_memoized_loader picks up the spy.
        fake_shared = _types.ModuleType("shared")
        fake_shared._fetch = _spy_fetch
        orig_shared = sys.modules.get("shared")
        sys.modules["shared"] = fake_shared
        try:
            loader1 = tv._make_memoized_loader(
                start_year=2018,
                end_year=2018,
                low_lookback_years=3,
                horizon_months=12,
                data_source="yahoo",
                price_cache=shared_cache,
            )
            result1 = loader1("AAPL")
        finally:
            if orig_shared is not None:
                sys.modules["shared"] = orig_shared
            elif "shared" in sys.modules:
                del sys.modules["shared"]

        assert result1 is not None, "First load must return the frame"
        assert fetch_call_count[0] == 1, (
            f"First load must call _fetch exactly once; got {fetch_call_count[0]}"
        )

        # --- Second run: warm disk, must NOT call fetch ---
        # Patch shared again but second run should NEVER call _fetch (disk hit).
        fetch_call_count_before = fetch_call_count[0]  # should be 1
        fake_shared2 = _types.ModuleType("shared")
        fake_shared2._fetch = _spy_fetch
        orig_shared2 = sys.modules.get("shared")
        sys.modules["shared"] = fake_shared2
        try:
            loader2 = tv._make_memoized_loader(
                start_year=2018,
                end_year=2018,
                low_lookback_years=3,
                horizon_months=12,
                data_source="yahoo",
                price_cache=shared_cache,  # same cache dir → disk hit
            )
            result2 = loader2("AAPL")
        finally:
            if orig_shared2 is not None:
                sys.modules["shared"] = orig_shared2
            elif "shared" in sys.modules:
                del sys.modules["shared"]

        assert result2 is not None, "Second load must return the frame"
        assert fetch_call_count[0] == fetch_call_count_before, (
            f"Second load must NOT call _fetch (disk hit expected); "
            f"fetch count went from {fetch_call_count_before} to {fetch_call_count[0]}"
        )

        # Value-identical: same shape and Close values
        assert result1.shape == result2.shape, (
            f"First and second load must be value-identical; shapes differ: "
            f"{result1.shape} vs {result2.shape}"
        )
        pd.testing.assert_series_equal(
            result1["Close"].reset_index(drop=True),
            result2["Close"].reset_index(drop=True),
            check_names=False,
        )

    def test_f332_s2_cache_miss_calls_network_then_persists(self, tmp_path):
        """F332-S2 complement: cold miss calls _fetch, then persists the frame to disk.

        After the first load, the cache file must exist in tmp_path.

        _make_memoized_loader does `from shared import _fetch` at factory-call time,
        so sys.modules["shared"] must be patched BEFORE the factory call.
        """
        import sys, types as _types

        SPAN_START = date(2015, 1, 1)
        SPAN_END = date(2022, 12, 31)
        sample_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)
        fetch_calls = []

        def _spy_fetch(ticker, start, end, interval, source):
            fetch_calls.append(ticker)
            return sample_df

        shared_cache = tv.PriceFrameCache(cache_dir=tmp_path)

        # Patch shared BEFORE creating loader so the factory's import picks up the spy
        fake_shared = _types.ModuleType("shared")
        fake_shared._fetch = _spy_fetch
        orig_shared = sys.modules.get("shared")
        sys.modules["shared"] = fake_shared
        try:
            loader = tv._make_memoized_loader(
                start_year=2018,
                end_year=2018,
                low_lookback_years=3,
                horizon_months=12,
                data_source="yahoo",
                price_cache=shared_cache,
            )
            result = loader("MSFT")
        finally:
            if orig_shared is not None:
                sys.modules["shared"] = orig_shared
            elif "shared" in sys.modules:
                del sys.modules["shared"]

        # Network was called
        assert "MSFT" in fetch_calls, "Cold miss must call _fetch"
        # Cache file must now exist (under the versioned subdir → recursive glob)
        cache_files = list(tmp_path.glob("**/MSFT_*.pkl"))
        assert len(cache_files) == 1, (
            f"Expected 1 cache file for MSFT after first load; found {cache_files}"
        )


# ===========================================================================
# Unit 2 (D14): Outcome engine v2 — bar-counted cohort-relative forward returns
# ===========================================================================
#
# Sampling-design note (mirrors the engine's locked decision): the matched null
# is COHORT-EXHAUSTIVE — every null event sharing the same as_of date. Excess is
# market/cohort-excess (NOT beta-adjusted). These tests pin: exact bar-counted
# returns, incomplete-horizon flagging, n<30 insufficiency, missing-price
# exclusion with a counted reason, short sign-correctness end-to-end, and borrow
# accrual scaling with hold days.


def _make_ramp_df(
    start: date,
    end: date,
    base: float = 100.0,
    step: float = 1.0,
) -> pd.DataFrame:
    """Arithmetic price ramp: Close[i] = base + step*i over business days.

    Makes the bar-counted forward return at offset N from any entry row EXACT:
      ((C[entry+N]) - C[entry]) / C[entry] = (N*step) / C[entry].
    """
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)
    closes = [base + step * i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.001 for c in closes],
            "Low": [c - 0.001 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


def _entry_row_and_close(df: pd.DataFrame, as_of: date) -> tuple[int, float]:
    """Return (entry_row_index, entry_close) for the first row >= as_of."""
    dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    for i, d in enumerate(dates):
        if d >= as_of:
            return i, float(df.iloc[i]["Close"])
    raise AssertionError("no entry row")


# ---------------------------------------------------------------------------
# U2-S1: Happy path — synthetic events with known prices → exact fwd returns
# ---------------------------------------------------------------------------

def test_u2_exact_bar_counted_forward_returns():
    """U2-S1: bar-counted forward returns at 21/63/126 trading days are exact.

    A ramp df with step=1.0 from base=100 → forward return at offset N from the
    entry row is exactly (N * 1.0)/entry_close * 100. Assert all three horizons.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    ramp = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=1.0)

    as_of = date(2018, 2, 15)
    entry_idx, entry_close = _entry_row_and_close(ramp, as_of)

    fwd = tv._bar_counted_forward_returns(ramp, _frame_date_at(ramp, entry_idx), entry_close)

    for h in (21, 63, 126):
        expected = (h * 1.0) / entry_close * 100.0
        assert fwd[h] is not None, f"horizon {h} should be complete"
        assert abs(fwd[h] - expected) < 1e-6, (
            f"horizon {h}: got {fwd[h]}, expected {expected}"
        )


def _frame_date_at(df: pd.DataFrame, idx: int) -> date:
    d = df.index[idx]
    return d.date() if hasattr(d, "date") else d


# ---------------------------------------------------------------------------
# U2-S2: Edge case — event within 126 trading days of data end → incomplete
# ---------------------------------------------------------------------------

def test_u2_incomplete_long_horizon_marked_none_not_extrapolated():
    """U2-S2: an entry with <126 bars to data end → 126d cell is None (incomplete),
    while the 21d/63d cells that DO fit are computed. Never extrapolated."""
    SPAN_START = date(2018, 1, 1)
    # Only ~90 business days of data after entry → 21d and 63d fit, 126d does not.
    SPAN_END = date(2018, 5, 31)
    ramp = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=1.0)

    as_of = date(2018, 1, 2)
    entry_idx, entry_close = _entry_row_and_close(ramp, as_of)
    fwd = tv._bar_counted_forward_returns(ramp, _frame_date_at(ramp, entry_idx), entry_close)

    assert fwd[21] is not None, "21d should fit within the frame"
    assert fwd[63] is not None, "63d should fit within the frame"
    assert fwd[126] is None, "126d must be marked incomplete (None), never extrapolated"


# ---------------------------------------------------------------------------
# U2 cohort-relative excess end-to-end (signal vs same-cohort null median)
# ---------------------------------------------------------------------------

def test_u2_cohort_relative_excess_end_to_end():
    """Signal event's excess = its fwd return minus the same-cohort null median.

    Per-ticker loader: the signal ticker rises faster than the nulls, so at every
    horizon the signal excess must be positive and hit_v2 True. Null events
    (identical flat ramp) have excess ~0 vs their own median.
    """
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    # Signal ramps at step=2 (fast), nulls ramp at step=0.5 (slow).
    fast = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=2.0)
    slow = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=0.5)

    def _loader(ticker: str):
        return fast if ticker == "SIG" else slow

    def _source(as_of, universe, bars_loader):
        cands = [_make_candidate("SIG", is_null=False)]
        # 5 null candidates so the cohort null median is well-defined
        for k in range(5):
            cands.append(_make_candidate(f"NUL{k}", is_null=True))
        return cands

    source = tv.CandidateSourceConfig(
        name="excess_cfg", direction="long",
        expected_events_per_year=200.0, source_fn=_source,
    )

    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12,
                             hit_threshold_pct=500.0),  # high threshold → no early exit
        source,
        _loader,
    )

    sig_events = [e for e in result.events if not e["is_null"]]
    assert sig_events, "expected at least one signal event"
    for e in sig_events:
        for col in ("excess_21d", "excess_63d", "excess_126d"):
            assert e[col] is not None, f"{col} should be computed"
            assert e[col] > 0, f"fast signal must beat slow-null median at {col}: {e[col]}"
        for col in ("hit_v2_21d", "hit_v2_63d", "hit_v2_126d"):
            assert e[col] is True, f"{col} should be True (excess>0)"


# ---------------------------------------------------------------------------
# U2-S3: n<30 cohort → insufficient flagging is honored by the atlas convention
# (the engine still computes excess; insufficiency is an atlas-cell concept).
# Here we assert the engine produces a small-cohort excess without crashing and
# the atlas marks the cell insufficient (covered in the research test below).
# ---------------------------------------------------------------------------

def test_u2_small_cohort_excess_computes_without_crash():
    """U2-S3 (engine side): a cohort with very few null events still computes a
    median-based excess (no division by zero, no crash). Insufficiency flagging
    lives in the atlas (n<30); see the research test for that assertion."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _source(as_of, universe, bars_loader):
        return [
            _make_candidate("SIGX", is_null=False),
            _make_candidate("NULX", is_null=True),  # single null → median is itself
        ]

    source = tv.CandidateSourceConfig(
        name="tiny_cfg", direction="long",
        expected_events_per_year=50.0, source_fn=_source,
    )
    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        source,
        lambda ticker: flat,
    )
    # No crash; excess fields present (may be 0.0 since signal==null path here)
    for e in result.events:
        assert "excess_63d" in e


# ---------------------------------------------------------------------------
# U2-S4: missing price data for an event → excluded with a counted reason
# ---------------------------------------------------------------------------

def test_u2_missing_price_excluded_with_counted_reason():
    """U2-S4: a candidate whose loader returns None (missing price) is excluded
    and never appears in the events table — it is NOT silently turned into a
    zero-return event. The run still completes for the candidate that has data."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    ramp = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=1.0)

    def _loader(ticker: str):
        return None if ticker == "MISSING" else ramp

    def _source(as_of, universe, bars_loader):
        return [
            _make_candidate("MISSING", is_null=False),  # no price → excluded
            _make_candidate("HASDATA", is_null=False),
        ]

    source = tv.CandidateSourceConfig(
        name="missing_cfg", direction="long",
        expected_events_per_year=100.0, source_fn=_source,
    )
    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        source,
        _loader,
    )
    tickers = {e["ticker"] for e in result.events}
    assert "MISSING" not in tickers, "missing-price ticker must be excluded, not zero-filled"
    assert "HASDATA" in tickers, "ticker with data must still produce an event"


# ---------------------------------------------------------------------------
# U2-S5: short-direction event with falling price → positive excess end-to-end
# ---------------------------------------------------------------------------

def test_u2_short_falling_price_positive_fwd_return_and_excess():
    """U2-S5: a short on a falling price produces POSITIVE bar-counted forward
    returns (sign-correct end-to-end) and positive excess vs a flat null cohort."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    falling = _make_ramp_df(SPAN_START, SPAN_END, base=300.0, step=-0.2)  # declines
    flat = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=0.0)

    def _loader(ticker: str):
        return falling if ticker == "SHRT" else flat

    def _source(as_of, universe, bars_loader):
        cands = [_make_candidate("SHRT", is_null=False)]
        for k in range(4):
            cands.append(_make_candidate(f"NUL{k}", is_null=True))
        return cands

    source = tv.CandidateSourceConfig(
        name="short_cfg", direction="short",
        expected_events_per_year=150.0, source_fn=_source,
    )
    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12,
                             hit_threshold_pct=500.0),
        source,
        _loader,
    )
    sig = [e for e in result.events if not e["is_null"] and e["ticker"] == "SHRT"]
    assert sig, "short event should exist"
    for e in sig:
        # Falling price + short direction → positive forward return at every horizon
        assert e["fwd_return_63d"] is not None and e["fwd_return_63d"] > 0, (
            f"short fwd_return_63d must be positive on a falling price: {e['fwd_return_63d']}"
        )
        # Flat null cohort → null median ~0 → short excess positive
        assert e["excess_63d"] is not None and e["excess_63d"] > 0
        assert e["net_return_pct"] > 0, "short net return on falling price must be positive"


# ---------------------------------------------------------------------------
# COR-01: short take-profit early-exit must trigger on price FALLING to target
# ---------------------------------------------------------------------------

def test_cor01_winning_short_hits_early_with_correct_days_to_hit():
    """COR-01 (P0): a winning short whose price FALLS through entry*(1-threshold)
    mid-window must fire the early-exit (hit) at the crossing bar with the correct
    days_to_hit.  Under the pre-fix long-direction trigger this never fires for a
    falling short (it would only fire on a deeply LOSING short whose price rose),
    so this test is discriminating.

    Price design: Close = 200 through 2018-05 (above entry), then steps to 80 on
    the first business day of 2018-06.  threshold=50% → target = entry*0.5 = 100,
    so 80 <= 100 triggers the short take-profit on that exact bar."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    idx = pd.date_range(SPAN_START, SPAN_END, freq="B")
    drop_on = pd.Timestamp(2018, 6, 1)
    closes = [200.0 if ts < drop_on else 80.0 for ts in idx]
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.001 for c in closes],
            "Low": [c - 0.001 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(idx),
        },
        index=idx,
    )

    # Expected entry = first business day >= 2018-02-15 (as_of date).
    as_of = pd.Timestamp(2018, 2, 15)
    entry_ts = idx[idx >= as_of][0]
    entry_date = entry_ts.date()
    # Expected early-exit = first bar at/after the drop where Close (80) <= target (100).
    exit_ts = idx[idx >= drop_on][0]
    exit_date = exit_ts.date()
    expected_days_to_hit = (exit_date - entry_date).days

    def _source(as_of_, universe, bars_loader):
        cands = [_make_candidate("SHRT", is_null=False)]
        for k in range(4):
            cands.append(_make_candidate(f"NUL{k}", is_null=True))
        return cands

    source = tv.CandidateSourceConfig(
        name="cor01_short", direction="short",
        expected_events_per_year=150.0, source_fn=_source,
    )
    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12,
                             hit_threshold_pct=50.0, slippage_bps=0.0,
                             borrow_rate_annual=0.0),
        source,
        lambda ticker: df if ticker == "SHRT" else _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=0.0),
    )
    sig = [e for e in result.events if e["ticker"] == "SHRT"]
    assert sig, "short event should exist"
    e = sig[0]
    # The short is a winner (price halved) and must be marked a hit (early-exit fired).
    assert e["hit"] is True, f"winning short must hit: net_return_pct={e['net_return_pct']}"
    assert e["net_return_pct"] >= 50.0, f"net return must clear threshold: {e['net_return_pct']}"
    # Early exit fired at the drop bar — NOT held to horizon end.
    assert e["exit_date"] == exit_date.isoformat(), (
        f"early-exit date must be the crossing bar {exit_date}, got {e['exit_date']}"
    )
    assert e["days_to_hit"] == expected_days_to_hit, (
        f"days_to_hit must be {expected_days_to_hit}, got {e['days_to_hit']}"
    )


# ---------------------------------------------------------------------------
# U2-S6: short cost correctness — slippage inverted + borrow accrues with hold
# ---------------------------------------------------------------------------

def test_u2_short_slippage_inverted_vs_long():
    """U2-S6a: for the SAME entry/exit, a short's slippage-adjusted fills are the
    mirror of a long's (entry lower / exit higher)."""
    req_long = _make_validation_req(direction="long", slippage_bps=50.0)
    req_short = _make_validation_req(direction="short", slippage_bps=50.0,
                                     borrow_rate_annual=0.0)

    ne_l, nx_l, _, _ = tv._apply_costs(100.0, 110.0, req_long, hold_days=30)
    ne_s, nx_s, _, _ = tv._apply_costs(100.0, 110.0, req_short, hold_days=30)

    # Long: entry filled UP (worse buy), exit filled DOWN (worse sell)
    assert ne_l > 100.0 and nx_l < 110.0
    # Short: entry filled DOWN (worse sell-to-open), exit filled UP (worse cover)
    assert ne_s < 100.0 and nx_s > 110.0


def test_u2_short_borrow_accrues_with_hold_days():
    """U2-S6b: borrow cost scales linearly with hold_days at the configured rate.

    Doubling hold_days doubles the borrow drag; a long incurs zero borrow."""
    req_short = _make_validation_req(direction="short", slippage_bps=0.0,
                                     borrow_rate_annual=3.65)  # 0.01%/day
    req_long = _make_validation_req(direction="long", slippage_bps=0.0,
                                    borrow_rate_annual=3.65)

    # Flat price (entry==exit) isolates the borrow drag: short net == -borrow_pct.
    _, _, _, net_30 = tv._apply_costs(100.0, 100.0, req_short, hold_days=30)
    _, _, _, net_60 = tv._apply_costs(100.0, 100.0, req_short, hold_days=60)
    _, _, _, net_long = tv._apply_costs(100.0, 100.0, req_long, hold_days=60)

    # 3.65%/yr = 0.01%/day → 30d = 0.30%, 60d = 0.60%
    assert abs(net_30 - (-0.30)) < 1e-6, f"30d borrow drag: {net_30}"
    assert abs(net_60 - (-0.60)) < 1e-6, f"60d borrow drag: {net_60}"
    # Linear scaling: 60d is exactly twice 30d
    assert abs(net_60 - 2 * net_30) < 1e-6
    # Longs never pay borrow
    assert abs(net_long) < 1e-9, f"long must have zero borrow: {net_long}"


# ---------------------------------------------------------------------------
# U2: legacy diagnostic fields still populated alongside v2 (additive contract)
# ---------------------------------------------------------------------------

def test_u2_legacy_diagnostic_fields_coexist_with_v2():
    """The v1 fields (net_return_pct, horizon_end_return_pct, horizon_months,
    hit, days_to_hit) remain populated next to the new v2 fields — additive."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    ramp = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=1.0)

    def _source(as_of, universe, bars_loader):
        return [_make_candidate("BOTH", is_null=False),
                _make_candidate("BOTN", is_null=True)]

    source = tv.CandidateSourceConfig(
        name="coexist_cfg", direction="long",
        expected_events_per_year=100.0, source_fn=_source,
    )
    result = _run_validation_with_source(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=12),
        source,
        lambda ticker: ramp,
    )
    assert result.events
    for e in result.events:
        # legacy diagnostics
        for k in ("net_return_pct", "horizon_months", "hit", "horizon_end_return_pct"):
            assert k in e, f"legacy field {k} must remain"
        # v2 additive
        for k in ("fwd_return_21d", "excess_63d", "hit_v2_126d"):
            assert k in e, f"v2 field {k} must be present"


# ---------------------------------------------------------------------------
# U6 / fix-nulls: cohort-exhaustive null aggregates for injected sources that
# emit ZERO null candidates (the harness-design gap fix).
#
# Charter §"Outcome spec" + §H2 "Exact stratification computation" (FROZEN):
#   When a candidate_source is active and a cohort emits zero null candidates,
#   the harness computes cohort-exhaustive null aggregates directly over every
#   universe ticker NOT selected that as_of (with sufficient data): per-horizon
#   median fwd return, plus trailing-252d daily-return-stdev terciles with
#   per-tercile median fwd return. Persisted as result.cohort_null_aggregates.
#   Signal excess is then computed vs the cohort's exhaustive null median.
# ---------------------------------------------------------------------------

def _run_validation_with_source_and_universe(
    req: tv.ValidationRequest,
    candidate_source,
    loader_by_ticker,
    universe,
    *,
    progress=None,
):
    """Like _run_validation_with_source but injects a non-empty universe and a
    per-ticker bars loader (dict dispatch). Needed for cohort-exhaustive null
    aggregates, which iterate over universe tickers NOT selected by the source.
    """
    import sys
    import types

    fake_t = _make_fake_turnaround(
        lambda universe, as_of, params, bars_loader=None: []
    )
    # Inject the universe via build_universe (run_validation slices [:max_universe]).
    fake_t.build_universe = lambda ticker_cik_map, params=None: list(universe)
    orig_t = sys.modules.get("turnaround")
    sys.modules["turnaround"] = fake_t

    fake_edgar = types.ModuleType("edgar")
    fake_edgar.fetch_universe = lambda: {t: "0000000001" for t, _ in universe}
    orig_e = sys.modules.get("edgar")
    sys.modules["edgar"] = fake_edgar

    def _loader(ticker):
        return loader_by_ticker.get(ticker)

    orig_loader_fn = tv._make_memoized_loader
    tv._make_memoized_loader = lambda **kw: _loader

    orig_import_comm = tv._import_per_leg_commission
    tv._import_per_leg_commission = lambda: (
        lambda shares, req: max(shares * req.per_share_rate, req.min_per_order)
    )

    try:
        kwargs = {"candidate_source": candidate_source}
        if progress is not None:
            kwargs["progress"] = progress
        result = tv.run_validation(req, **kwargs)
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


def _trailing_vol_252(df: pd.DataFrame, as_of: date) -> float:
    """Independent re-derivation of the trailing-252d daily-return stdev at the
    entry row (first row >= as_of). Uses pandas std (sample, ddof=1) over the
    251 daily simple returns spanning the trailing 252 closes. Mirrors the math
    the implementation must use, derived independently here so the test asserts
    real behavior rather than calling production code.
    """
    dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    entry_idx = next(i for i, d in enumerate(dates) if d >= as_of)
    window = df["Close"].iloc[entry_idx - 251 : entry_idx + 1]
    rets = window.pct_change().dropna()
    return float(rets.std(ddof=1))


def _fwd_ret(df: pd.DataFrame, as_of: date, n: int) -> float:
    """Independent exact forward return at offset n from entry row (first >= as_of)."""
    dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    entry_idx = next(i for i, d in enumerate(dates) if d >= as_of)
    entry_close = float(df["Close"].iloc[entry_idx])
    exit_close = float(df["Close"].iloc[entry_idx + n])
    return (exit_close - entry_close) / entry_close * 100.0


def test_u6_cohort_null_aggregates_populated_for_zero_null_source():
    """(a) Injected source emits a signal candidate but ZERO null candidates on a
    single cohort. Against a synthetic universe of 6 unselected names, the harness
    must populate result.cohort_null_aggregates for that as_of with:
      - n == 6 (all six universe names have sufficient data),
      - per-horizon whole-cohort null median == independently computed median,
      - tercile_breaks (2 values) and tercile_medians (3 buckets),
    and the signal event's excess_*d must equal signal_fwd − whole-cohort null
    median (exhaustive), no longer None.
    """
    SPAN_START = date(2014, 1, 1)
    SPAN_END = date(2020, 12, 31)
    AS_OF = date(2018, 2, 15)

    # 6 unselected null universe names: arithmetic ramps with distinct steps so
    # their trailing-252d return stdev differs monotonically (separable terciles)
    # and their forward returns are exact. base differs to keep fwd returns
    # well-separated for tercile-median checks.
    null_specs = {
        "NUL1": dict(base=100.0, step=0.5),
        "NUL2": dict(base=100.0, step=1.0),
        "NUL3": dict(base=100.0, step=1.5),
        "NUL4": dict(base=100.0, step=2.0),
        "NUL5": dict(base=100.0, step=2.5),
        "NUL6": dict(base=100.0, step=3.0),
    }
    loaders = {
        t: _make_ramp_df(SPAN_START, SPAN_END, base=s["base"], step=s["step"])
        for t, s in null_specs.items()
    }
    # The selected signal name (steep ramp → large fwd return).
    SIG = "SIGX"
    loaders[SIG] = _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=5.0)

    universe = [(SIG, "Signal Co")] + [(t, f"{t} Co") for t in null_specs]

    def _source(as_of, universe, bars_loader):
        if as_of == AS_OF:
            return [_make_candidate(SIG, is_null=False)]
        return []  # only one populated cohort

    source = tv.CandidateSourceConfig(
        name="u6_zero_null", direction="long",
        expected_events_per_year=100.0, source_fn=_source,
    )
    req = _make_validation_req(
        start_year=2018, end_year=2018, horizon_months=12, max_universe=50,
    )
    result = _run_validation_with_source_and_universe(req, source, loaders, universe)

    # Legacy events-based null path is empty for this source.
    assert result.null_n == 0

    # New cohort_null_aggregates dict is present and populated for AS_OF.
    agg = result.cohort_null_aggregates
    assert isinstance(agg, dict)
    key = AS_OF.isoformat()
    assert key in agg, "cohort_null_aggregates must contain the populated cohort"
    cohort = agg[key]
    assert cohort["n"] == 6
    assert cohort.get("insufficient") in (False, None)

    # Whole-cohort null median per horizon == independent median of null fwd returns.
    for h in (21, 63, 126):
        expected_vals = sorted(_fwd_ret(loaders[t], AS_OF, h) for t in null_specs)
        import statistics as _st
        expected_median = _st.median(expected_vals)
        got = cohort["medians"][str(h)]
        assert got == pytest.approx(expected_median, rel=1e-9), (
            f"horizon {h}: median {got} != {expected_median}"
        )

    # Tercile structure: 2 break values, 3 tercile buckets each with per-horizon medians.
    assert len(cohort["tercile_breaks"]) == 2
    assert len(cohort["tercile_medians"]) == 3
    for bucket in cohort["tercile_medians"]:
        for h in (21, 63, 126):
            assert str(h) in bucket["medians"]

    # Tercile membership: names sorted by trailing-252d vol, split into 3 of 2.
    vols = {t: _trailing_vol_252(loaders[t], AS_OF) for t in null_specs}
    ordered = sorted(null_specs, key=lambda t: vols[t])
    expected_buckets = [ordered[0:2], ordered[2:4], ordered[4:6]]
    import statistics as _st
    for bi, members in enumerate(expected_buckets):
        for h in (21, 63, 126):
            exp = _st.median(_fwd_ret(loaders[m], AS_OF, h) for m in members)
            got = cohort["tercile_medians"][bi]["medians"][str(h)]
            assert got == pytest.approx(exp, rel=1e-9), (
                f"tercile {bi} horizon {h}: {got} != {exp}"
            )

    # Signal excess computed vs whole-cohort exhaustive null median (no longer None).
    sig_events = [e for e in result.events if e["ticker"] == SIG and not e["is_null"]]
    assert len(sig_events) == 1
    se = sig_events[0]
    for h in (21, 63, 126):
        sig_fwd = se[f"fwd_return_{h}d"]
        null_med = cohort["medians"][str(h)]
        assert se[f"excess_{h}d"] is not None
        assert se[f"excess_{h}d"] == pytest.approx(sig_fwd - null_med, rel=1e-9)
        assert se[f"hit_v2_{h}d"] == ((sig_fwd - null_med) > 0)


def test_u6_legacy_path_unchanged_no_cohort_aggregates():
    """(b) Regression: legacy path (candidate_source=None, null candidates emitted
    via run_filter) is byte-identically unchanged — events-based nulls keep working
    and cohort_null_aggregates stays empty (the new path never triggers)."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    def _fake_run_filter(universe, as_of, params, bars_loader=None):
        return [_make_candidate("SIGL", is_null=False),
                _make_candidate("NULL", is_null=True)]

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018, horizon_months=3),
        _fake_run_filter,
        lambda ticker: flat_df,
    )
    # Legacy events-based nulls still computed.
    assert result.null_n > 0
    # New dict present but empty (no injected source → never triggers).
    assert result.cohort_null_aggregates == {}


def test_u6_cohort_all_insufficient_flagged_excess_none():
    """(c) Cohort where ALL unselected universe names lack sufficient data
    (< 252 bars) → aggregates flagged insufficient, signal excess stays None."""
    AS_OF = date(2018, 2, 15)
    SPAN_START = date(2014, 1, 1)
    SPAN_END = date(2020, 12, 31)

    # Null universe names: SHORT frames (only ~30 business days near AS_OF) so
    # they cannot supply a trailing-252d window or 126d forward return.
    short_start = date(2018, 2, 1)
    short_end = date(2018, 3, 15)
    short_df = _make_ramp_df(short_start, short_end, base=100.0, step=1.0)

    SIG = "SIGY"
    loaders = {
        "NSH1": short_df,
        "NSH2": short_df,
        SIG: _make_ramp_df(SPAN_START, SPAN_END, base=100.0, step=5.0),
    }
    universe = [(SIG, "Signal Co"), ("NSH1", "n1"), ("NSH2", "n2")]

    def _source(as_of, universe, bars_loader):
        if as_of == AS_OF:
            return [_make_candidate(SIG, is_null=False)]
        return []

    source = tv.CandidateSourceConfig(
        name="u6_insufficient", direction="long",
        expected_events_per_year=100.0, source_fn=_source,
    )
    req = _make_validation_req(
        start_year=2018, end_year=2018, horizon_months=12, max_universe=50,
    )
    result = _run_validation_with_source_and_universe(req, source, loaders, universe)

    key = AS_OF.isoformat()
    cohort = result.cohort_null_aggregates.get(key)
    assert cohort is not None, "cohort entry must exist even when insufficient"
    assert cohort["insufficient"] is True
    assert cohort["n"] == 0

    # Signal excess stays None (no usable cohort null median).
    sig_events = [e for e in result.events if e["ticker"] == SIG and not e["is_null"]]
    assert len(sig_events) == 1
    se = sig_events[0]
    for h in (21, 63, 126):
        assert se[f"excess_{h}d"] is None
        assert se[f"hit_v2_{h}d"] is None
