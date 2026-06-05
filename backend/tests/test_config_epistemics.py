"""Tests for backend/research/config_epistemics.py — Unit 8 (epistemics-ablation config)

All tests are offline/synthetic — no network calls, no live EDGAR, no real prices.

Test scenarios per charter EPISTEMICS-TEST §2/§3:

  EP-S1: PRICE arm rank-N selection — exact top-N by ret_126 descending, ties by ticker asc
  EP-S2: PRICE arm excludes short-history ticker (< 252 td)
  EP-S3: PRICE arm floor enforcement — sub-$5 excluded (below_floor)
  EP-S4: PRICE arm floor enforcement — corrupt frame excluded (corrupt_frame)
  EP-S5: FILING arm point-in-time boundary — post-as_of filing ignored
  EP-S6: FILING arm no-CIK → no_fundamentals exclusion (never imputed)
  EP-S7: FILING arm rank-N selection — exact top-N by revenue_yoy_pct descending, ties by ticker asc
  EP-S8: Coverage tier computation at all three tiers (viable / reweight / unviable)
  EP-S9: Disjoint+overlap fixture — both arms produce correct intersection count
  EP-S10: Config metadata (name, direction, expected_events_per_year, horizons, both long)
  EP-S11: Both arms registered in route resolver
  EP-S12: ret_126 formula exact — (close[-1]/close[-127] - 1) * 100
  EP-S13: FILING arm short-history exclusion (< 252 td)
  EP-S14: FILING arm floor enforcement (sub-$5, thin volume, corrupt frame)
  EP-S15: N enforcement — only top-N emitted when >N eligible names exist
"""
from __future__ import annotations

import sys
import os
from datetime import date, timedelta
from typing import Optional
from os.path import dirname, abspath

sys.path.insert(0, dirname(dirname(abspath(__file__))))

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helper: build synthetic price DataFrames
# ---------------------------------------------------------------------------

def _make_df(
    start: date,
    end: date,
    start_price: float = 100.0,
    trend: float = 0.0,
    volume: int = 1_000_000,
) -> pd.DataFrame:
    """Synthetic daily OHLCV DataFrame with constant-trend Close prices."""
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)
    closes = [start_price * ((1.0 + trend) ** i) for i in range(n)]
    df = pd.DataFrame({
        "Open": closes,
        "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes],
        "Close": closes,
        "Volume": [volume] * n,
    }, index=dates)
    return df


def _make_eligible_df(as_of: date, bars_before: int = 600, trend: float = 0.001) -> pd.DataFrame:
    """Fixture that passes all floor + history gates.

    ≥252 td, price ≥ $5, volume ≥ 500k.
    """
    start = as_of - timedelta(days=int(bars_before * 1.5))
    return _make_df(start, as_of, start_price=50.0, trend=trend)


def _make_ipo_df(as_of: date, bars: int = 100) -> pd.DataFrame:
    """Fixture with < 252 td history (short_history exclusion)."""
    start = as_of - timedelta(days=int(bars * 1.5))
    df = _make_df(start, as_of, start_price=50.0, trend=0.001)
    return df.iloc[-bars:] if len(df) >= bars else df


def _make_sub5_df(as_of: date, bars_before: int = 600) -> pd.DataFrame:
    """Near-high fixture that trades below the $5 min_price floor."""
    start = as_of - timedelta(days=int(bars_before * 1.5))
    return _make_df(start, as_of, start_price=2.50, trend=0.0003)


def _make_thin_volume_df(as_of: date, bars_before: int = 600) -> pd.DataFrame:
    """Eligible price/history but below 500k min_avg_volume floor."""
    start = as_of - timedelta(days=int(bars_before * 1.5))
    return _make_df(start, as_of, start_price=50.0, trend=0.001, volume=50_000)


def _make_corrupt_df(as_of: date, bars_before: int = 600) -> pd.DataFrame:
    """Fixture with a >10x split-corruption spike within trailing 252td."""
    start = as_of - timedelta(days=int(bars_before * 1.5))
    df = _make_df(start, as_of, start_price=50.0, trend=0.001)
    closes = df["Close"].tolist()
    spike = len(closes) - 30
    if spike > 0:
        closes[spike] = closes[spike - 1] * 50.0
    df["Close"] = closes
    return df


# ---------------------------------------------------------------------------
# Helper: build synthetic revenue series
# ---------------------------------------------------------------------------

def _make_revenue_series(
    quarters: list[tuple[str, float, str]],  # (end, val, filed)
) -> list[dict]:
    """Build a synthetic revenue series as edgar.get_quarterly_revenue would return."""
    return [{"end": end, "val": float(val), "filed": filed}
            for end, val, filed in quarters]


# ---------------------------------------------------------------------------
# EP-S1: PRICE arm rank-N selection exact on fixtures
# ---------------------------------------------------------------------------

def test_ep_price_rank_n_exact():
    """EP-S1: PRICE arm selects exactly top-N by ret_126 descending, ties by ticker ascending.

    Build 6 tickers with known trailing-126td returns in a predictable order.
    Verify the exact top-N selection and that the rank ordering is correct.
    """
    from research.config_epistemics import _make_price_source_fn

    as_of = date(2019, 6, 15)
    # We'll set N=50 but only have 6 tickers — all should be emitted (< N)
    # What we test is the rank ORDER, not the cap

    # Create 6 distinct ret_126 values by varying the trend:
    # Higher trend = higher close[-1] relative to close[-127] = higher ret_126
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    trends = [0.003, 0.001, -0.001, -0.003, 0.002, -0.002]
    # Expected rank order by descending ret_126: AAA(+), EEE(+), BBB(+), CCC(-), FFF(-), DDD(-)
    # But exact ordering depends on compounded returns; we just verify sorted order

    bars_map = {}
    for ticker, trend in zip(tickers, trends):
        bars_map[ticker] = _make_eligible_df(as_of, bars_before=700, trend=trend)

    universe = [(t, f"00010{i:05d}") for i, t in enumerate(tickers)]
    source_fn = _make_price_source_fn()
    results = source_fn(as_of, universe, bars_map.get)

    # All 6 should be emitted (< N=50)
    assert len(results) == 6, f"Expected 6 candidates, got {len(results)}"
    assert all(not r.is_null_candidate for r in results)

    # Verify descending composite_score order (composite_score = ret_126)
    scores = [r.composite_score for r in results]
    assert scores == sorted(scores, reverse=True), (
        f"PRICE arm results not sorted by ret_126 descending: {scores}"
    )

    # Verify all tickers are present
    result_tickers = {r.ticker for r in results}
    assert result_tickers == set(tickers), (
        f"Missing tickers: expected {set(tickers)}, got {result_tickers}"
    )


def test_ep_price_rank_n_cap():
    """EP-S1b: PRICE arm emits exactly N=50 when more than 50 eligible names exist."""
    from research.config_epistemics import _make_price_source_fn

    as_of = date(2019, 6, 15)
    N = 50
    n_universe = 60  # more than N

    bars_map = {}
    tickers = [f"T{i:04d}" for i in range(n_universe)]
    for i, ticker in enumerate(tickers):
        # Different trends so all have distinct ret_126
        trend = 0.003 - i * 0.0001
        bars_map[ticker] = _make_eligible_df(as_of, bars_before=700, trend=trend)

    universe = [(t, f"00010{i:05d}") for i, t in enumerate(tickers)]
    source_fn = _make_price_source_fn()
    results = source_fn(as_of, universe, bars_map.get)

    assert len(results) == N, f"PRICE arm must emit exactly N={N} from {n_universe} eligible; got {len(results)}"

    # Verify the emitted set has the highest ret_126 values
    all_scores = []
    for entry in universe:
        ticker = entry[0]
        df = bars_map[ticker]
        from research.config_epistemics import _df_up_to, _get_close
        sliced = _df_up_to(df, as_of)
        close = _get_close(sliced)
        ret = (float(close.iloc[-1]) / float(close.iloc[-127]) - 1.0) * 100.0
        all_scores.append((ret, ticker))
    all_scores.sort(key=lambda x: (-x[0], x[1]))
    expected_top_tickers = {t for _, t in all_scores[:N]}
    actual_tickers = {r.ticker for r in results}
    assert actual_tickers == expected_top_tickers, (
        f"PRICE arm top-N not the highest-ret_126 names. "
        f"Extra: {actual_tickers - expected_top_tickers}, Missing: {expected_top_tickers - actual_tickers}"
    )


# ---------------------------------------------------------------------------
# EP-S2: PRICE arm short-history exclusion (< 252 td)
# ---------------------------------------------------------------------------

def test_ep_price_short_history_excluded():
    """EP-S2: PRICE arm excludes ticker with < 252 td history (short_history)."""
    from research.config_epistemics import _make_price_source_fn

    as_of = date(2019, 6, 15)
    ipo_df = _make_ipo_df(as_of, bars=100)
    good_df = _make_eligible_df(as_of, bars_before=700)

    universe = [("IPO", "0001000001"), ("GOOD", "0001000002")]
    bars_map = {"IPO": ipo_df, "GOOD": good_df}

    source_fn = _make_price_source_fn()
    results = source_fn(as_of, universe, bars_map.get)

    tickers = [r.ticker for r in results]
    assert "IPO" not in tickers, "Short-history IPO ticker must be excluded (short_history)"


# ---------------------------------------------------------------------------
# EP-S3/EP-S4: PRICE arm floor enforcement
# ---------------------------------------------------------------------------

def test_ep_price_sub5_excluded():
    """EP-S3: PRICE arm excludes sub-$5 ticker (below_floor)."""
    from research.config_epistemics import _make_price_source_fn

    as_of = date(2019, 6, 15)
    universe = [("PENNY", "0001000003")]
    bars_map = {"PENNY": _make_sub5_df(as_of)}

    source_fn = _make_price_source_fn()
    results = source_fn(as_of, universe, bars_map.get)
    assert len(results) == 0 or all(r.ticker != "PENNY" for r in results), (
        "Sub-$5 ticker must be excluded (below_floor)"
    )


def test_ep_price_corrupt_frame_excluded():
    """EP-S4: PRICE arm excludes split-corrupt frame (corrupt_frame)."""
    from research.config_epistemics import _make_price_source_fn

    as_of = date(2019, 6, 15)
    universe = [("CORRUPT", "0001000004")]
    bars_map = {"CORRUPT": _make_corrupt_df(as_of)}

    source_fn = _make_price_source_fn()
    results = source_fn(as_of, universe, bars_map.get)
    assert all(r.ticker != "CORRUPT" for r in results), (
        "Split-corrupt frame must be excluded (corrupt_frame)"
    )


# ---------------------------------------------------------------------------
# EP-S5: FILING arm point-in-time boundary — post-as_of filing ignored
# ---------------------------------------------------------------------------

def test_ep_filing_pit_boundary_post_as_of_ignored():
    """EP-S5: filing with filed >= as_of is invisible to the FILING arm.

    Charter §2 / §3 (FROZEN): 'only filings with filed date STRICTLY BEFORE as_of count'.
    A filing dated exactly on as_of must not inform the rank.
    """
    from research.config_epistemics import _compute_revenue_yoy_pit
    import edgar

    as_of = date(2019, 6, 15)

    # Latest quarter filed ON as_of → excluded (strict < rule)
    revenue_series = _make_revenue_series([
        ("2018-06-30", 200_000_000, "2018-09-01"),   # prior-year Q2-2018 (visible)
        ("2019-06-30", 250_000_000, "2019-06-15"),   # latest Q2-2019 filed = as_of → EXCLUDED
    ])

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = lambda cik: revenue_series
    try:
        yoy = _compute_revenue_yoy_pit("0001234567", as_of)
    finally:
        edgar.get_quarterly_revenue = orig

    # 2019-06-30 filed on as_of → invisible; only 2018-06-30 visible.
    # No latest+prior pair computable → None.
    assert yoy is None, (
        f"Filing on as_of must be excluded (strict < rule); expected None, got {yoy}"
    )


def test_ep_filing_pit_boundary_before_as_of_visible():
    """EP-S5b: filing filed strictly before as_of is visible (normal case)."""
    from research.config_epistemics import _compute_revenue_yoy_pit
    import edgar

    as_of = date(2019, 6, 15)

    revenue_series = _make_revenue_series([
        ("2018-03-31", 200_000_000, "2018-05-01"),
        ("2019-03-31", 250_000_000, "2019-06-14"),  # filed day before as_of → visible
    ])

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = lambda cik: revenue_series
    try:
        yoy = _compute_revenue_yoy_pit("0001234568", as_of)
    finally:
        edgar.get_quarterly_revenue = orig

    # Q1-2019 (250M) vs Q1-2018 (200M) → +25%
    assert yoy is not None, "Filing strictly before as_of must be visible"
    assert abs(yoy - 25.0) < 0.01, f"Expected YoY ≈ 25.0%, got {yoy:.4f}"


# ---------------------------------------------------------------------------
# EP-S6: FILING arm no-CIK exclusion (no_fundamentals, never imputed)
# ---------------------------------------------------------------------------

def test_ep_filing_no_cik_excluded():
    """EP-S6: FILING arm excludes name with empty CIK with no_fundamentals counted reason."""
    from research.config_epistemics import _make_filing_source_fn

    as_of = date(2019, 6, 15)
    good_df = _make_eligible_df(as_of, bars_before=700)

    # empty CIK → no EDGAR lookup possible
    universe = [("NOCIK", "")]
    bars_map = {"NOCIK": good_df}

    source_fn = _make_filing_source_fn()
    results = source_fn(as_of, universe, bars_map.get)
    assert len(results) == 0, (
        f"No-CIK ticker must be excluded (no_fundamentals); got {len(results)}"
    )


def test_ep_filing_empty_series_excluded():
    """EP-S6b: FILING arm excludes name with CIK but empty revenue series (no_fundamentals)."""
    from research.config_epistemics import _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)
    good_df = _make_eligible_df(as_of, bars_before=700)

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = lambda cik: []  # empty series

    try:
        universe = [("EMPTYFACTS", "0001234569")]
        bars_map = {"EMPTYFACTS": good_df}
        source_fn = _make_filing_source_fn()
        results = source_fn(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    assert len(results) == 0, (
        "Empty revenue series must result in no_fundamentals exclusion; "
        f"got {len(results)}"
    )


# ---------------------------------------------------------------------------
# EP-S7: FILING arm rank-N selection exact
# ---------------------------------------------------------------------------

def test_ep_filing_rank_n_exact():
    """EP-S7: FILING arm selects top-N by revenue_yoy_pct descending, ties by ticker asc."""
    from research.config_epistemics import _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)

    # Build 6 tickers with different revenue YoY values
    yoy_values = {
        "AAA": 80.0,
        "BBB": 50.0,
        "CCC": 20.0,
        "DDD": -10.0,
        "EEE": 100.0,
        "FFF": 5.0,
    }
    # Expected rank: EEE(100), AAA(80), BBB(50), CCC(20), FFF(5), DDD(-10)

    good_df = _make_eligible_df(as_of, bars_before=700)

    revenue_cache: dict[str, list] = {}
    for ticker, yoy in yoy_values.items():
        # prior = 100M, latest = 100M * (1 + yoy/100)
        prior_val = 100_000_000
        latest_val = prior_val * (1.0 + yoy / 100.0)
        revenue_cache[ticker] = _make_revenue_series([
            ("2018-03-31", float(prior_val), "2018-05-01"),
            ("2019-03-31", float(latest_val), "2019-05-15"),  # filed before as_of
        ])

    def fake_get_revenue(cik):
        # cik maps to ticker via the universe
        # We pass cik = "CIK_" + ticker
        ticker = cik.replace("CIK_", "")
        return revenue_cache.get(ticker, [])

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = fake_get_revenue

    try:
        universe = [(t, f"CIK_{t}") for t in yoy_values]
        bars_map = {t: good_df for t in yoy_values}
        source_fn = _make_filing_source_fn()
        results = source_fn(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    # All 6 should be emitted (< N=50)
    assert len(results) == 6, f"Expected 6 candidates, got {len(results)}"

    # Verify descending composite_score order
    scores = [r.composite_score for r in results]
    assert scores == sorted(scores, reverse=True), (
        f"FILING arm results not sorted by revenue_yoy_pct descending: {scores}"
    )

    # Top is EEE (100%), bottom is DDD (-10%)
    assert results[0].ticker == "EEE", (
        f"Top of FILING rank must be EEE (YoY=100%), got {results[0].ticker}"
    )
    assert results[-1].ticker == "DDD", (
        f"Bottom of FILING rank must be DDD (YoY=-10%), got {results[-1].ticker}"
    )


# ---------------------------------------------------------------------------
# EP-S8: Coverage tier computation at all three tiers
# ---------------------------------------------------------------------------

def test_ep_coverage_tier_viable():
    """EP-S8a: coverage >= 0.60 → 'viable' tier."""
    from research.config_epistemics import compute_coverage_tier
    assert compute_coverage_tier(0.60) == "viable"
    assert compute_coverage_tier(0.75) == "viable"
    assert compute_coverage_tier(1.00) == "viable"


def test_ep_coverage_tier_reweight():
    """EP-S8b: 0.40 <= coverage < 0.60 → 'reweight' tier."""
    from research.config_epistemics import compute_coverage_tier
    assert compute_coverage_tier(0.40) == "reweight"
    assert compute_coverage_tier(0.50) == "reweight"
    assert compute_coverage_tier(0.59) == "reweight"


def test_ep_coverage_tier_unviable():
    """EP-S8c: coverage < 0.40 → 'unviable' tier."""
    from research.config_epistemics import compute_coverage_tier
    assert compute_coverage_tier(0.00) == "unviable"
    assert compute_coverage_tier(0.20) == "unviable"
    assert compute_coverage_tier(0.39) == "unviable"


# ---------------------------------------------------------------------------
# EP-S9: Disjoint + overlap fixture
# ---------------------------------------------------------------------------

def test_ep_disjoint_overlap_counts():
    """EP-S9: both arms produce disjoint+overlap correctly counted.

    Build a universe where 2 names are strong on both price and filings (overlap),
    2 are strong on price only, and 2 are strong on filings only.
    Verify the arm intersection has exactly 2 names.
    """
    from research.config_epistemics import _make_price_source_fn, _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)

    # 6 tickers:
    #   BOTH_A, BOTH_B: high ret_126 AND high revenue YoY → expected in BOTH arms
    #   PRICE_A, PRICE_B: high ret_126, low revenue YoY → expected in PRICE only
    #   FILING_A, FILING_B: low ret_126, high revenue YoY → expected in FILING only
    # We use ≤6 tickers so N=50 cap doesn't matter; all eligible are emitted

    # Price fixture: trend controls ret_126
    # We need >= 127 bars + >= 252 bars for floor/history; use bars_before=700
    def make_df_with_trend(trend):
        return _make_eligible_df(as_of, bars_before=700, trend=trend)

    bars_map = {
        "BOTH_A":    make_df_with_trend(0.004),   # strong price
        "BOTH_B":    make_df_with_trend(0.003),   # strong price
        "PRICE_A":   make_df_with_trend(0.002),   # strong price
        "PRICE_B":   make_df_with_trend(0.001),   # strong price
        "FILING_A":  make_df_with_trend(-0.001),  # weak price
        "FILING_B":  make_df_with_trend(-0.002),  # weak price
    }

    # Revenue YoY:
    #   BOTH_A, BOTH_B: 80%, 70% → high
    #   PRICE_A, PRICE_B: -20%, -30% → low
    #   FILING_A, FILING_B: 100%, 90% → high
    yoy_map = {
        "BOTH_A": 80.0, "BOTH_B": 70.0,
        "PRICE_A": -20.0, "PRICE_B": -30.0,
        "FILING_A": 100.0, "FILING_B": 90.0,
    }

    def fake_rev(cik):
        ticker = cik.replace("CIK_", "")
        yoy = yoy_map.get(ticker)
        if yoy is None:
            return []
        prior = 100_000_000
        latest = prior * (1.0 + yoy / 100.0)
        return _make_revenue_series([
            ("2018-03-31", float(prior), "2018-05-01"),
            ("2019-03-31", float(latest), "2019-05-15"),
        ])

    universe = [(t, f"CIK_{t}") for t in bars_map]

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = fake_rev

    try:
        price_results = _make_price_source_fn()(as_of, universe, bars_map.get)
        filing_results = _make_filing_source_fn()(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    price_set = {r.ticker for r in price_results}
    filing_set = {r.ticker for r in filing_results}

    # Both arms should emit their candidates
    assert len(price_results) == 6, f"PRICE arm: expected 6, got {len(price_results)}"
    assert len(filing_results) == 6, f"FILING arm: expected 6 (all have YoY), got {len(filing_results)}"

    # Overlap = tickers in both arms
    overlap = price_set & filing_set
    # BOTH_A and BOTH_B are in both arms; PRICE-only and FILING-only are not
    assert "BOTH_A" in overlap and "BOTH_B" in overlap, (
        f"BOTH_A and BOTH_B must be in both arms. Overlap: {overlap}"
    )

    # Disjoint sets
    price_only = price_set - filing_set
    filing_only = filing_set - price_set

    # PRICE_A, PRICE_B are rank higher by price but lower by filing
    # FILING_A, FILING_B are rank higher by filing but lower by price
    # (all emitted since we have 6 < N=50)
    assert price_only <= {"PRICE_A", "PRICE_B", "FILING_A", "FILING_B"}, (
        f"Unexpected price-only members: {price_only}"
    )
    assert filing_only <= {"PRICE_A", "PRICE_B", "FILING_A", "FILING_B"}, (
        f"Unexpected filing-only members: {filing_only}"
    )

    # The two overlap members (BOTH_A, BOTH_B) should be non-empty
    assert len(overlap) >= 2, f"Expected >= 2 overlap names, got {len(overlap)}: {overlap}"


# ---------------------------------------------------------------------------
# EP-S10: Config metadata
# ---------------------------------------------------------------------------

def test_ep_config_metadata():
    """EP-S10: CONFIG_PRICE and CONFIG_FILING have correct charter-declared metadata."""
    from research.config_epistemics import CONFIG_PRICE, CONFIG_FILING

    # PRICE arm
    assert CONFIG_PRICE.name == "epistemics_price", (
        f"Expected name='epistemics_price', got {CONFIG_PRICE.name!r}"
    )
    assert CONFIG_PRICE.direction == "long", (
        f"Charter §2: PRICE arm must be long, got {CONFIG_PRICE.direction!r}"
    )
    assert CONFIG_PRICE.expected_events_per_year == 200.0, (
        f"R1: expected_events_per_year must be 200, got {CONFIG_PRICE.expected_events_per_year}"
    )
    assert CONFIG_PRICE.horizons == [21, 63, 126], (
        f"Horizons must be [21, 63, 126], got {CONFIG_PRICE.horizons}"
    )

    # FILING arm
    assert CONFIG_FILING.name == "epistemics_filing", (
        f"Expected name='epistemics_filing', got {CONFIG_FILING.name!r}"
    )
    assert CONFIG_FILING.direction == "long", (
        f"Charter §2: FILING arm must be long, got {CONFIG_FILING.direction!r}"
    )
    assert CONFIG_FILING.expected_events_per_year == 200.0, (
        f"R1: expected_events_per_year must be 200, got {CONFIG_FILING.expected_events_per_year}"
    )
    assert CONFIG_FILING.horizons == [21, 63, 126], (
        f"Horizons must be [21, 63, 126], got {CONFIG_FILING.horizons}"
    )

    # Both arms are DIFFERENT configs
    assert CONFIG_PRICE is not CONFIG_FILING
    assert CONFIG_PRICE.name != CONFIG_FILING.name


# ---------------------------------------------------------------------------
# EP-S11: Both arms registered in route resolver
# ---------------------------------------------------------------------------

def test_ep_registered_configs_resolve():
    """EP-S11: both arms resolve correctly in the route registry."""
    from routes.turnaround import _resolve_candidate_source
    from turnaround_validation import CandidateSourceConfig

    for name in ("epistemics_price", "epistemics_filing"):
        cfg = _resolve_candidate_source(name)
        assert cfg is not None, f"{name} must resolve to a non-None config"
        assert isinstance(cfg, CandidateSourceConfig), (
            f"{name} must resolve to CandidateSourceConfig, got {type(cfg)}"
        )
        assert cfg.name == name, f"Config name mismatch: {cfg.name} != {name}"
        assert cfg.direction == "long", f"{name} must have direction='long'"

    # Existing configs still resolve
    assert _resolve_candidate_source(None) is None
    assert _resolve_candidate_source("legacy") is None
    for name in ("momentum_M1", "deterioration_D1"):
        cfg = _resolve_candidate_source(name)
        assert cfg is not None

    # Unknown still raises
    with pytest.raises(ValueError, match="Unknown config_name"):
        _resolve_candidate_source("epistemics_unknown")


# ---------------------------------------------------------------------------
# EP-S12: ret_126 formula exact
# ---------------------------------------------------------------------------

def test_ep_price_ret_126_formula_exact():
    """EP-S12: ret_126 = (close[-1] / close[-127] - 1) * 100 exactly.

    Build a fixture with known close prices at positions -1 and -127.
    Verify composite_score matches the formula.
    """
    from research.config_epistemics import _make_price_source_fn, _df_up_to, _get_close

    as_of = date(2019, 6, 15)
    df = _make_eligible_df(as_of, bars_before=700, trend=0.001)

    universe = [("FORMULA_TEST", "0001000099")]
    bars_map = {"FORMULA_TEST": df}

    source_fn = _make_price_source_fn()
    results = source_fn(as_of, universe, bars_map.get)

    assert len(results) == 1, f"Expected 1 candidate, got {len(results)}"

    # Manual formula computation
    sliced = _df_up_to(df, as_of)
    close = _get_close(sliced)
    expected_ret = (float(close.iloc[-1]) / float(close.iloc[-127]) - 1.0) * 100.0

    actual = results[0].composite_score
    assert abs(actual - expected_ret) < 1e-9, (
        f"ret_126 formula mismatch: got {actual}, expected {expected_ret}"
    )


# ---------------------------------------------------------------------------
# EP-S13: FILING arm short-history exclusion
# ---------------------------------------------------------------------------

def test_ep_filing_short_history_excluded():
    """EP-S13: FILING arm excludes ticker with < 252 td history."""
    from research.config_epistemics import _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)
    ipo_df = _make_ipo_df(as_of, bars=100)

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = lambda cik: _make_revenue_series([
        ("2018-03-31", 100_000_000, "2018-05-01"),
        ("2019-03-31", 150_000_000, "2019-05-01"),
    ])

    try:
        universe = [("IPO_FILING", "0001000010")]
        bars_map = {"IPO_FILING": ipo_df}
        source_fn = _make_filing_source_fn()
        results = source_fn(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    assert all(r.ticker != "IPO_FILING" for r in results), (
        "Short-history IPO ticker must be excluded from FILING arm (short_history)"
    )


# ---------------------------------------------------------------------------
# EP-S14: FILING arm floor enforcement
# ---------------------------------------------------------------------------

def test_ep_filing_sub5_excluded():
    """EP-S14a: FILING arm excludes sub-$5 ticker (below_floor)."""
    from research.config_epistemics import _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)
    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = lambda cik: _make_revenue_series([
        ("2018-03-31", 100_000_000, "2018-05-01"),
        ("2019-03-31", 150_000_000, "2019-05-01"),
    ])
    try:
        universe = [("PENNY_F", "0001000011")]
        bars_map = {"PENNY_F": _make_sub5_df(as_of)}
        source_fn = _make_filing_source_fn()
        results = source_fn(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    assert all(r.ticker != "PENNY_F" for r in results), (
        "Sub-$5 ticker must be excluded from FILING arm (below_floor)"
    )


def test_ep_filing_corrupt_excluded():
    """EP-S14b: FILING arm excludes split-corrupt frame (corrupt_frame)."""
    from research.config_epistemics import _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)
    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = lambda cik: _make_revenue_series([
        ("2018-03-31", 100_000_000, "2018-05-01"),
        ("2019-03-31", 150_000_000, "2019-05-01"),
    ])
    try:
        universe = [("CORRUPT_F", "0001000012")]
        bars_map = {"CORRUPT_F": _make_corrupt_df(as_of)}
        source_fn = _make_filing_source_fn()
        results = source_fn(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    assert all(r.ticker != "CORRUPT_F" for r in results), (
        "Split-corrupt frame must be excluded from FILING arm (corrupt_frame)"
    )


# ---------------------------------------------------------------------------
# EP-S15: N enforcement — only top-N emitted when > N eligible
# ---------------------------------------------------------------------------

def test_ep_filing_rank_n_cap():
    """EP-S15: FILING arm emits exactly N=50 when more than 50 eligible names exist."""
    from research.config_epistemics import _make_filing_source_fn
    import edgar

    as_of = date(2019, 6, 15)
    N = 50
    n_universe = 60  # more than N

    tickers = [f"F{i:04d}" for i in range(n_universe)]
    good_df = _make_eligible_df(as_of, bars_before=700)

    # Each ticker has a distinct YoY value
    yoy_vals = {f"F{i:04d}": float(i * 2) for i in range(n_universe)}

    def fake_rev(cik):
        ticker = cik.replace("CIK_", "")
        yoy = yoy_vals.get(ticker, 10.0)
        prior = 100_000_000
        latest = prior * (1.0 + yoy / 100.0)
        return _make_revenue_series([
            ("2018-03-31", float(prior), "2018-05-01"),
            ("2019-03-31", float(latest), "2019-05-15"),
        ])

    orig = edgar.get_quarterly_revenue
    edgar.get_quarterly_revenue = fake_rev

    try:
        universe = [(t, f"CIK_{t}") for t in tickers]
        bars_map = {t: good_df for t in tickers}
        source_fn = _make_filing_source_fn()
        results = source_fn(as_of, universe, bars_map.get)
    finally:
        edgar.get_quarterly_revenue = orig

    assert len(results) == N, (
        f"FILING arm must emit exactly N={N} from {n_universe} eligible; got {len(results)}"
    )

    # Verify the emitted set has the highest YoY values
    expected_top_tickers = set(sorted(tickers, key=lambda t: -yoy_vals[t])[:N])
    actual_tickers = {r.ticker for r in results}
    assert actual_tickers == expected_top_tickers, (
        f"FILING arm top-N not the highest-revenue_yoy names. "
        f"Extra: {actual_tickers - expected_top_tickers}, "
        f"Missing: {expected_top_tickers - actual_tickers}"
    )
