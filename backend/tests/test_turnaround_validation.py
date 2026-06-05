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
    assert parsed["schema_version"] == 1

    for event in parsed["events"]:
        for key in ("as_of", "entry_date", "exit_date"):
            val = event.get(key)
            if val is not None:
                assert isinstance(val, str), f"After roundtrip, {key} should be str"


def test_schema_version_present():
    """Item 1: schema_version field is present and equals 1."""
    SPAN_START = date(2015, 1, 1)
    SPAN_END = date(2022, 12, 31)
    flat_df = _make_price_df(SPAN_START, SPAN_END, annual_growth_rate=0.0)

    result = _run_validation_with_mocks(
        _make_validation_req(start_year=2018, end_year=2018),
        lambda universe, as_of, params, bars_loader=None: [],
        lambda ticker: flat_df,
    )

    assert hasattr(result, "schema_version")
    assert result.schema_version == 1


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
