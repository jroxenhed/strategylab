"""Tests for research/fundamental_surprise.py — F348.

Coverage:
  Formula correctness
    test_revenue_yoy_positive          — positive revenue growth
    test_revenue_yoy_negative          — revenue contraction
    test_revenue_yoy_zero_denominator  — zero prior revenue → None
    test_earnings_yoy_positive         — positive earnings growth (prior > 0)
    test_earnings_yoy_prior_le_zero    — prior ni <= 0 → None (uninterpretable sign)
    test_net_margin_level              — net_margin = ni_t / rev_t
    test_net_margin_zero_revenue       — rev_t = 0 → None
    test_net_margin_infl_pp            — pp formula and sign
    test_gross_margin_infl_pp          — pp formula and sign
    test_dilution_yoy_positive         — share count grew → dilution > 0
    test_dilution_yoy_negative         — buyback → dilution < 0
    test_ocf_accrual_ratio             — ocf_t / ni_t when ni_t > 0
    test_ocf_accrual_ratio_neg_ni      — ni_t <= 0 → None (spec guard)

  Look-ahead (the critical test)
    test_no_lookahead_restatement      — restated prior filed AFTER as_of is ignored;
                                         pre-event value used instead

  Calendar-window matching
    test_yoy_within_tolerance          — off-by-a-few-days end date matches within ±45d
    test_yoy_outside_tolerance         — end date outside ±45d → None (no YoY)
    test_qoq_within_tolerance          — within ±30d for QoQ

  Missing data
    test_missing_prior_revenue         — no prior revenue → revenue_yoy = None (not 0)
    test_missing_current_revenue       — no current revenue → all None
    test_empty_derived                 — empty derived dict → all None, no crash

  Revenue acceleration
    test_revenue_accel_positive        — accelerating revenue growth
    test_revenue_accel_missing_qoq     — no QoQ prior → revenue_accel = None

  Meta coverage counts
    test_n_nonnull_count               — n_nonnull counts only non-None numeric fields

  build_pead_surprise_events
    test_build_pead_events_meta        — meta keys present and population-scoped counts correct
    test_build_pead_events_skip_out_of_universe — tickers absent from universe are skipped
    test_build_pead_events_span_filter — filings outside date range excluded
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup (mirrors test_r1b_dose.py pattern)
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
_RESEARCH = _BACKEND / "research"
for _p in [str(_BACKEND), str(_RESEARCH)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from research.fundamental_surprise import (
    compute_surprise_payload,
    build_pead_surprise_events,
    _filter_pit,
    _find_prior_entry,
    _latest_entry,
    NUMERIC_KEYS,
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic derived dicts
# ---------------------------------------------------------------------------

def _entry(end: str, filed: str, val: float) -> dict:
    return {"end": end, "filed": filed, "val": val}


def _shares_entry(end: str, filed: str, val: float, form: str = "10-Q") -> dict:
    return {"end": end, "filed": filed, "val": val, "form": form}


def _make_derived(
    *,
    revenue: list[dict] | None = None,
    net_income: list[dict] | None = None,
    gross_profit: list[dict] | None = None,
    ocf: list[dict] | None = None,
    shares: list[dict] | None = None,
) -> dict:
    return {
        "revenue": revenue or [],
        "net_income": net_income or [],
        "gross_profit": gross_profit or [],
        "ocf": ocf or [],
        "shares": shares or [],
    }


def _mock_derived(derived: dict):
    """Return a context manager that patches _load_derived_disk_only to return `derived`."""
    return patch("research.fundamental_surprise._load_derived_disk_only", return_value=derived)


# ---------------------------------------------------------------------------
# Formula correctness
# ---------------------------------------------------------------------------

def test_revenue_yoy_positive():
    """Revenue grows 25%: (125-100)/100 = 0.25."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 100.0),  # prior year
            _entry("2020-09-30", "2020-11-01", 125.0),  # current
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_yoy"] == pytest.approx(0.25)


def test_revenue_yoy_negative():
    """Revenue falls 20%: (80-100)/100 = -0.20."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 100.0),
            _entry("2020-09-30", "2020-11-01", 80.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_yoy"] == pytest.approx(-0.20)


def test_revenue_yoy_zero_denominator():
    """Prior revenue = 0 → revenue_yoy must be None."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 0.0),
            _entry("2020-09-30", "2020-11-01", 50.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_yoy"] is None


def test_earnings_yoy_positive():
    """Earnings grow 50%: prior ni=200, current ni=300, prior>0."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 1000.0),
            _entry("2020-09-30", "2020-11-01", 1100.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", 200.0),
            _entry("2020-09-30", "2020-11-01", 300.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["earnings_yoy"] == pytest.approx(0.5)


def test_earnings_yoy_prior_le_zero():
    """Prior ni = 0 → earnings_yoy must be None (sign uninterpretable)."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 1000.0),
            _entry("2020-09-30", "2020-11-01", 1000.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", 0.0),   # prior ni = 0
            _entry("2020-09-30", "2020-11-01", 100.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["earnings_yoy"] is None


def test_earnings_yoy_prior_negative():
    """Prior ni = -50 (loss) → earnings_yoy must be None."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 1000.0),
            _entry("2020-09-30", "2020-11-01", 1000.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", -50.0),
            _entry("2020-09-30", "2020-11-01", 100.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["earnings_yoy"] is None


def test_net_margin_level():
    """net_margin = ni_t / rev_t."""
    derived = _make_derived(
        revenue=[_entry("2020-09-30", "2020-11-01", 400.0)],
        net_income=[_entry("2020-09-30", "2020-11-01", 80.0)],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["net_margin"] == pytest.approx(0.20)


def test_net_margin_zero_revenue():
    """rev_t = 0 → net_margin must be None."""
    derived = _make_derived(
        revenue=[_entry("2020-09-30", "2020-11-01", 0.0)],
        net_income=[_entry("2020-09-30", "2020-11-01", 50.0)],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["net_margin"] is None


def test_net_margin_infl_pp():
    """Margin expanded from 20% to 25% → infl = +5.0 pp."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 1000.0),
            _entry("2020-09-30", "2020-11-01", 1000.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", 200.0),  # 20%
            _entry("2020-09-30", "2020-11-01", 250.0),  # 25%
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["net_margin_infl_pp"] == pytest.approx(5.0)


def test_gross_margin_infl_pp():
    """Gross margin shrank from 60% to 50% → infl = -10.0 pp."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 1000.0),
            _entry("2020-09-30", "2020-11-01", 1000.0),
        ],
        gross_profit=[
            _entry("2019-09-30", "2019-11-01", 600.0),  # 60%
            _entry("2020-09-30", "2020-11-01", 500.0),  # 50%
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["gross_margin_infl_pp"] == pytest.approx(-10.0)


def test_dilution_yoy_positive():
    """Shares grew from 100M to 110M → dilution_yoy = +0.10 (BAD = dilution)."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 500.0),
            _entry("2020-09-30", "2020-11-01", 500.0),
        ],
        shares=[
            _shares_entry("2019-09-30", "2019-11-01", 100_000_000.0),
            _shares_entry("2020-09-30", "2020-11-01", 110_000_000.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["dilution_yoy"] == pytest.approx(0.10)


def test_dilution_yoy_negative():
    """Buyback: shares fell from 100M to 90M → dilution_yoy = -0.10."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 500.0),
            _entry("2020-09-30", "2020-11-01", 500.0),
        ],
        shares=[
            _shares_entry("2019-09-30", "2019-11-01", 100_000_000.0),
            _shares_entry("2020-09-30", "2020-11-01", 90_000_000.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["dilution_yoy"] == pytest.approx(-0.10)


def test_ocf_accrual_ratio():
    """OCF > NI → ratio > 1 (cash-backed earnings)."""
    derived = _make_derived(
        revenue=[_entry("2020-09-30", "2020-11-01", 1000.0)],
        net_income=[_entry("2020-09-30", "2020-11-01", 200.0)],
        ocf=[_entry("2020-09-30", "2020-11-01", 300.0)],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["ocf_accrual_ratio"] == pytest.approx(1.5)


def test_ocf_accrual_ratio_neg_ni():
    """ni_t <= 0 → ocf_accrual_ratio must be None (spec guard)."""
    derived = _make_derived(
        revenue=[_entry("2020-09-30", "2020-11-01", 1000.0)],
        net_income=[_entry("2020-09-30", "2020-11-01", -10.0)],
        ocf=[_entry("2020-09-30", "2020-11-01", 50.0)],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["ocf_accrual_ratio"] is None


# ---------------------------------------------------------------------------
# The critical look-ahead test
# ---------------------------------------------------------------------------

def test_no_lookahead_restatement():
    """Restated prior value filed AFTER as_of must be ignored.

    Scenario:
      - Current period ends 2020-09-30, filed 2020-11-01 (as_of = 2020-11-15)
      - Prior period (YoY) ends 2019-09-30:
          - Original value: 100.0, filed 2019-11-01 (within as_of)
          - Restatement:   120.0, filed 2020-12-01 (AFTER as_of)  ← must be EXCLUDED

    The function must use 100.0 (not 120.0) for the prior, so:
      revenue_yoy = (200 - 100) / 100 = 1.0
    If the restatement leaked: (200 - 120) / 120 ≈ 0.667 (wrong answer)
    """
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 100.0),   # original prior
            _entry("2019-09-30", "2020-12-01", 120.0),   # restatement AFTER as_of — must be ignored
            _entry("2020-09-30", "2020-11-01", 200.0),   # current
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)

    # Must use 100.0 (original), not 120.0 (restatement filed after as_of)
    assert result["revenue_yoy"] == pytest.approx(1.0), (
        f"Look-ahead leak detected: revenue_yoy={result['revenue_yoy']} "
        f"(expected 1.0 from pre-as_of prior=100; 0.667 would indicate restatement leak)"
    )

    # Verify the restatement itself did not sneak into the current-period either
    # (current_end must be 2020-09-30, not 2019-09-30)
    assert result["current_end"] == "2020-09-30"
    assert result["yoy_end"] == "2019-09-30"


def test_no_lookahead_current_period():
    """An entry for the current period filed AFTER as_of must be excluded entirely.

    Scenario:
      - Only revenue entry for current period: filed 2021-02-01 (after as_of 2021-01-15)
      - So no current-period data is visible → all outputs must be None.
    """
    derived = _make_derived(
        revenue=[
            _entry("2020-09-30", "2019-11-01", 100.0),   # prior year (OK)
            _entry("2021-09-30", "2021-02-01", 200.0),   # filed after as_of — excluded
        ],
    )
    as_of = date(2021, 1, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)

    # current period data was filed after as_of → current_end must be the older entry
    # (2020-09-30 is the latest visible entry)
    assert result["current_end"] == "2020-09-30"
    # YoY prior would be 2019-09-30 — which we don't have, so revenue_yoy should be None
    assert result["revenue_yoy"] is None


# ---------------------------------------------------------------------------
# Calendar-window matching
# ---------------------------------------------------------------------------

def test_yoy_within_tolerance():
    """Prior 'end' off by 10 days from exact YoY → still matches within ±45d."""
    derived = _make_derived(
        revenue=[
            # Prior: end 2019-10-10 is 355 days before 2020-09-30 (within ±45d of 365)
            _entry("2019-10-10", "2019-11-15", 100.0),
            _entry("2020-09-30", "2020-11-01", 120.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    # Should compute revenue_yoy using 2019-10-10 as prior
    assert result["revenue_yoy"] is not None
    assert result["revenue_yoy"] == pytest.approx(0.20)


def test_yoy_outside_tolerance():
    """Prior 'end' 50 days from exact YoY (outside ±45d) → YoY is None."""
    derived = _make_derived(
        revenue=[
            # Prior: 2019-08-01 is ~60 days before exact 365d prior of 2020-09-30 (2019-10-01)
            _entry("2019-08-01", "2019-09-01", 100.0),
            _entry("2020-09-30", "2020-11-01", 120.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_yoy"] is None


def test_qoq_within_tolerance():
    """QoQ prior within ±30d matches (for revenue_accel computation).

    Exact expected values (F379 — formula regression guard, not just None-ness):
      revenue_yoy  = (120 - 90) / 90  = 1/3  ≈ 0.3333...
      prior_yoy    = (100 - 80) / 80  = 1/4  = 0.25
      revenue_accel = 1/3 - 1/4 = 1/12 ≈ 0.08333...
    """
    # Current: 2020-09-30 (Q3 2020)
    # QoQ prior should be near 2020-09-30 - 91d = 2020-07-01, we use 2020-06-30 (1 day off)
    # YoY priors needed for accel: 2019-09-30 and 2019-06-30
    derived = _make_derived(
        revenue=[
            _entry("2019-06-30", "2019-08-01", 80.0),    # prior of QoQ prior
            _entry("2019-09-30", "2019-11-01", 90.0),    # YoY prior of current
            _entry("2020-06-30", "2020-08-01", 100.0),   # QoQ prior of current (1d inside ±30d)
            _entry("2020-09-30", "2020-11-01", 120.0),   # current
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    # Both YoY and QoQ should be found → revenue_accel should be computable
    assert result["qoq_end"] == "2020-06-30"
    assert result["revenue_accel"] is not None
    # Exact value — catches formula regressions beyond mere None-ness (F379)
    assert result["revenue_yoy"] == pytest.approx(1 / 3)
    assert result["revenue_accel"] == pytest.approx(1 / 3 - 1 / 4)  # 1/12 ≈ 0.08333


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------

def test_missing_prior_revenue():
    """No prior-year revenue entry → revenue_yoy must be None, not 0."""
    derived = _make_derived(
        revenue=[
            _entry("2020-09-30", "2020-11-01", 200.0),  # only current; no YoY prior
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_yoy"] is None
    assert result["revenue_yoy"] != 0  # must be None, never 0


def test_missing_current_revenue():
    """No revenue data at all → all fields None, no crash."""
    derived = _make_derived()  # empty
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    # Everything should be None
    for k in ("revenue_yoy", "earnings_yoy", "net_margin", "dilution_yoy"):
        assert result[k] is None
    assert result["n_nonnull"] == 0


def test_empty_derived():
    """Empty derived dict returns all-None without crashing."""
    as_of = date(2020, 11, 15)
    with _mock_derived({}):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["n_nonnull"] == 0
    assert result["current_end"] is None


# ---------------------------------------------------------------------------
# Revenue acceleration
# ---------------------------------------------------------------------------

def test_revenue_accel_positive():
    """Revenue is accelerating: YoY(t) > YoY(t-1q).

    YoY(t):     (120 - 90) / 90  = 1/3  ≈ 0.3333  (current vs year-ago)
    YoY(t-1q):  (100 - 80) / 80  = 1/4  = 0.2500  (prior quarter vs its year-ago)
    accel = 1/3 - 1/4 = 1/12 ≈ 0.0833  (accelerating — exact, F379)
    """
    derived = _make_derived(
        revenue=[
            _entry("2019-06-30", "2019-08-01", 80.0),    # year-ago of QoQ
            _entry("2019-09-30", "2019-11-01", 90.0),    # year-ago of current
            _entry("2020-06-30", "2020-08-01", 100.0),   # QoQ prior of current
            _entry("2020-09-30", "2020-11-01", 120.0),   # current
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_accel"] is not None
    # Exact value — catches formula regressions beyond sign-only checks (F379)
    assert result["revenue_accel"] == pytest.approx(1 / 3 - 1 / 4)  # 1/12 ≈ 0.0833


def test_revenue_accel_missing_qoq():
    """No QoQ prior → revenue_accel must be None."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 90.0),   # YoY prior only
            _entry("2020-09-30", "2020-11-01", 120.0),  # current (no QoQ prior)
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["revenue_accel"] is None


# ---------------------------------------------------------------------------
# n_nonnull count
# ---------------------------------------------------------------------------

def test_n_nonnull_count():
    """n_nonnull equals the count of non-None numeric output fields."""
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 100.0),
            _entry("2020-09-30", "2020-11-01", 120.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", 20.0),
            _entry("2020-09-30", "2020-11-01", 25.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)

    # Manually count non-None numeric keys — uses imported module-level NUMERIC_KEYS (F377)
    expected_nonnull = sum(1 for k in NUMERIC_KEYS if result[k] is not None)
    assert result["n_nonnull"] == expected_nonnull
    assert result["n_nonnull"] > 0  # we should have at least revenue_yoy, earnings_yoy, margins


# ---------------------------------------------------------------------------
# build_pead_surprise_events — meta and enumeration
# ---------------------------------------------------------------------------

def _write_submission(path: Path, cik: str, ticker: str, filings: list[dict]) -> None:
    """Write a minimal EDGAR submissions JSON to path for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "tickers": [ticker],
        "filings": {
            "recent": {
                "form": [f["form"] for f in filings],
                "acceptanceDateTime": [f["adt"] for f in filings],
                "reportDate": [f.get("reportDate", "") for f in filings],
            }
        },
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def test_build_pead_events_meta(tmp_path):
    """build_pead_surprise_events returns correct meta keys and population-scoped counts."""
    subs_dir = tmp_path / "submissions"

    # AAPL (in universe) — one 10-Q in range
    _write_submission(
        subs_dir / "0000320193.json",
        cik="0000320193",
        ticker="AAPL",
        filings=[
            {"form": "10-Q", "adt": "2019-05-01T21:30:00.000Z", "reportDate": "2019-03-31"},
        ],
    )
    # MSFT (in universe) — one 10-K in range
    _write_submission(
        subs_dir / "0000789019.json",
        cik="0000789019",
        ticker="MSFT",
        filings=[
            {"form": "10-K", "adt": "2019-07-30T21:00:00.000Z", "reportDate": "2019-06-30"},
        ],
    )
    # UNKNOWN (not in universe) — one 10-Q in range
    _write_submission(
        subs_dir / "0000999999.json",
        cik="0000999999",
        ticker="UNKN",
        filings=[
            {"form": "10-Q", "adt": "2019-03-01T20:00:00.000Z", "reportDate": "2019-01-31"},
        ],
    )

    universe_tickers = ["AAPL", "MSFT"]

    # Minimal derived data so compute_surprise_payload doesn't crash
    def _fake_load_derived(cik):
        return {}  # empty → all-None payload, but no crash

    with patch("research.fundamental_surprise._load_derived_disk_only", side_effect=_fake_load_derived):
        events, meta = build_pead_surprise_events(
            universe_tickers=universe_tickers,
            span_start="2019-01-01",
            span_end="2019-12-31",
            submissions_dir=subs_dir,
        )

    # Population-scope checks
    assert meta["n_filings_seen"] == 3   # all 3 filings in date range (AAPL + MSFT + UNKN)
    assert meta["n_in_universe"] == 2    # only AAPL + MSFT are in universe
    assert meta["n_events"] == 2         # EventRecord produced for each in-universe filing
    assert meta["n_skipped_no_ticker"] == 0  # all files had ticker mappings
    # n_no_derived should be 2 (empty derived returned for both)
    assert meta["n_no_derived"] == 2

    # Events should be AAPL and MSFT
    tickers_seen = {e.ticker for e in events}
    assert tickers_seen == {"AAPL", "MSFT"}

    # Required meta keys present and coverage_population is the exact in-universe count (F379)
    for key in ("n_filings_seen", "n_in_universe", "n_events", "n_no_derived",
                "n_skipped_no_ticker", "coverage_population", "field_nonnull",
                "field_coverage_frac"):
        assert key in meta, f"missing meta key: {key}"
    # coverage_population must equal n_in_universe (2) — not just be present (F379)
    assert meta["coverage_population"] == 2, (
        f"coverage_population={meta['coverage_population']} expected 2 (== n_in_universe)"
    )


def test_build_pead_events_skip_out_of_universe(tmp_path):
    """Tickers absent from the universe are not included in events."""
    subs_dir = tmp_path / "submissions"
    _write_submission(
        subs_dir / "0000111111.json",
        cik="0000111111",
        ticker="INUNIVERSE",
        filings=[{"form": "10-Q", "adt": "2019-06-01T20:00:00.000Z", "reportDate": "2019-03-31"}],
    )
    _write_submission(
        subs_dir / "0000222222.json",
        cik="0000222222",
        ticker="NOTINUNIVERSE",
        filings=[{"form": "10-Q", "adt": "2019-06-01T20:00:00.000Z", "reportDate": "2019-03-31"}],
    )

    with patch("research.fundamental_surprise._load_derived_disk_only", return_value={}):
        events, meta = build_pead_surprise_events(
            universe_tickers=["INUNIVERSE"],
            span_start="2019-01-01",
            span_end="2019-12-31",
            submissions_dir=subs_dir,
        )

    assert len(events) == 1
    assert events[0].ticker == "INUNIVERSE"
    assert meta["n_in_universe"] == 1
    assert meta["n_filings_seen"] == 2  # both filings seen before universe filter


def test_build_pead_events_span_filter(tmp_path):
    """Filings outside span_start/span_end are excluded."""
    subs_dir = tmp_path / "submissions"
    _write_submission(
        subs_dir / "0000320193.json",
        cik="0000320193",
        ticker="AAPL",
        filings=[
            # In range
            {"form": "10-Q", "adt": "2019-05-01T21:30:00.000Z", "reportDate": "2019-03-31"},
            # Before span
            {"form": "10-Q", "adt": "2017-05-01T21:30:00.000Z", "reportDate": "2017-03-31"},
            # After span
            {"form": "10-Q", "adt": "2021-05-01T21:30:00.000Z", "reportDate": "2021-03-31"},
        ],
    )

    with patch("research.fundamental_surprise._load_derived_disk_only", return_value={}):
        events, meta = build_pead_surprise_events(
            universe_tickers=["AAPL"],
            span_start="2019-01-01",
            span_end="2019-12-31",
            submissions_dir=subs_dir,
        )

    assert meta["n_events"] == 1
    assert len(events) == 1
    assert meta["n_filings_seen"] == 1  # only 1 filing counted within span


# ---------------------------------------------------------------------------
# C1 — _val_at_end latest-filed tie-break (FAILS without the fix)
# ---------------------------------------------------------------------------

def test_val_at_end_latest_filed_wins():
    """Among same-end entries both filed <= as_of, the latest-filed value is used (C1).

    Scenario:
      - Two revenue entries for current period (end 2020-09-30):
          - filed 2020-11-01, val=100.0  (original)
          - filed 2020-11-15, val=110.0  (amendment, filed before as_of 2020-12-01)
      - Without the fix, the first-seen entry (100.0) would be returned.
      - With the fix, the latest-filed entry (110.0) must be returned.
    """
    derived = _make_derived(
        revenue=[
            _entry("2020-09-30", "2020-11-01", 100.0),   # original
            _entry("2020-09-30", "2020-11-15", 110.0),   # amendment (latest-filed, before as_of)
        ],
    )
    as_of = date(2020, 12, 1)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    # net_margin uses rev_t — should be based on 110.0, not 100.0
    # With only current-period data (no prior), revenue_yoy=None but current_end is present.
    assert result["current_end"] == "2020-09-30"
    # No prior → revenue_yoy None; but net_margin (if ni present) would use 110.0
    # Verify indirectly: add ni entry and check net_margin
    derived2 = _make_derived(
        revenue=[
            _entry("2020-09-30", "2020-11-01", 100.0),
            _entry("2020-09-30", "2020-11-15", 110.0),  # latest-filed
        ],
        net_income=[_entry("2020-09-30", "2020-11-01", 22.0)],
    )
    with patch("research.fundamental_surprise._load_derived_disk_only", return_value=derived2):
        result2 = compute_surprise_payload("0000320193", as_of)
    # net_margin = ni_t / rev_t; if latest-filed (110.0) used: 22/110 ≈ 0.2
    # if original (100.0) used: 22/100 = 0.22
    assert result2["net_margin"] == pytest.approx(22.0 / 110.0)


# ---------------------------------------------------------------------------
# K10 — rev_prior > 0 guard for margin metrics (FAILS without the fix)
# ---------------------------------------------------------------------------

def test_margin_infl_negative_rev_prior_is_none():
    """Negative rev_prior → both net_margin_infl_pp and gross_margin_infl_pp must be None (K10).

    With the old rev_prior != 0 guard, a negative prior revenue produces a
    sign-flipped margin (dividing by a negative number reverses the sign).
    With the fix (rev_prior > 0 strict), both must be None.
    """
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", -100.0),  # negative prior revenue
            _entry("2020-09-30", "2020-11-01", 100.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", -20.0),
            _entry("2020-09-30", "2020-11-01", 25.0),
        ],
        gross_profit=[
            _entry("2019-09-30", "2019-11-01", -60.0),
            _entry("2020-09-30", "2020-11-01", 60.0),
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["net_margin_infl_pp"] is None, (
        f"net_margin_infl_pp should be None with negative rev_prior, got {result['net_margin_infl_pp']}"
    )
    assert result["gross_margin_infl_pp"] is None, (
        f"gross_margin_infl_pp should be None with negative rev_prior, got {result['gross_margin_infl_pp']}"
    )


# ---------------------------------------------------------------------------
# DI-01 — probe_a2_no_lookahead covers all 5 series
# ---------------------------------------------------------------------------

def test_no_lookahead_net_income_prior_restatement():
    """net_income prior restated with filed > as_of must be ignored (DI-01).

    Scenario:
      - Current period ends 2020-09-30, filed 2020-11-01 (as_of = 2020-11-15)
      - net_income prior (end 2019-09-30):
          - Original: 100.0, filed 2019-11-01 (within as_of)
          - Restatement: 200.0, filed 2020-12-01 (AFTER as_of — must be excluded)
      - earnings_yoy must be computed from original prior (100.0), not the restatement.
        earnings_yoy = (150 - 100) / 100 = 0.50
        If restatement leaked: (150 - 200) / 200 = -0.25 (wrong answer)
    """
    derived = _make_derived(
        revenue=[
            _entry("2019-09-30", "2019-11-01", 1000.0),
            _entry("2020-09-30", "2020-11-01", 1100.0),
        ],
        net_income=[
            _entry("2019-09-30", "2019-11-01", 100.0),   # original prior
            _entry("2019-09-30", "2020-12-01", 200.0),   # restatement AFTER as_of — must be ignored
            _entry("2020-09-30", "2020-11-01", 150.0),   # current
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        result = compute_surprise_payload("0000320193", as_of)
    assert result["earnings_yoy"] == pytest.approx(0.50), (
        f"Look-ahead leak in net_income prior: earnings_yoy={result['earnings_yoy']} "
        f"(expected 0.50 from pre-as_of prior=100; -0.25 indicates restatement leak)"
    )


# ---------------------------------------------------------------------------
# K9/DI-02 — deterministic ET as_of (FAILS without the fix on non-UTC offsets)
# ---------------------------------------------------------------------------

def test_build_pead_events_et_as_of_from_non_utc_offset(tmp_path):
    """as_of must be the ET calendar date regardless of the acceptanceDateTime offset (K9/DI-02).

    A filing with acceptanceDateTime "2019-11-01T21:30:00-04:00" (EDT offset):
      - UTC equiv: 2019-11-02T01:30:00Z
      - ET: 2019-11-01T21:30:00 → ET date = 2019-11-01
    With the old parser (strip fractional, replace with +00:00), the offset is lost:
      - Treated as UTC: 2019-11-01T21:30:00Z → ET: 2019-11-01T17:30:00 → ET date = 2019-11-01 (OK by coincidence)
    But with a UTC-boundary case: "2019-02-08T00:30:00-05:00" (EST):
      - UTC equiv: 2019-02-08T05:30:00Z → ET: 2019-02-08T00:30:00 → ET date = 2019-02-08
      - Old parser (loses offset): treated as UTC 2019-02-08T00:30:00Z → ET: 2019-02-07T19:30:00 → ET date = 2019-02-07 (WRONG day!)
    This test catches that: filing at 00:30 EST is on 2019-02-08 ET, so span includes it.
    """
    subs_dir = tmp_path / "submissions"
    # Filing at 00:30 local time EST (-05:00) on 2019-02-08, which is UTC 2019-02-08T05:30
    # ET date = 2019-02-08 (correct). Old parser would give ET date = 2019-02-07 (wrong).
    _write_submission(
        subs_dir / "0000320193.json",
        cik="0000320193",
        ticker="AAPL",
        filings=[
            {"form": "10-Q", "adt": "2019-02-08T00:30:00-05:00", "reportDate": "2018-12-31"},
        ],
    )
    universe_tickers = ["AAPL"]
    with patch("research.fundamental_surprise._load_derived_disk_only", return_value={}):
        events, meta = build_pead_surprise_events(
            universe_tickers=universe_tickers,
            span_start="2019-02-08",   # span starts on the correct ET date
            span_end="2019-02-08",
            submissions_dir=subs_dir,
        )
    assert meta["n_events"] == 1, (
        f"Expected 1 event (ET date 2019-02-08 in span); got {meta['n_events']}. "
        "Offset likely discarded — filing mapped to wrong ET day."
    )


# ---------------------------------------------------------------------------
# DI-03 — _val_at_end non-numeric val guard (FAILS without the fix)
# ---------------------------------------------------------------------------

def test_val_at_end_non_numeric_val_is_none():
    """Non-numeric val in an entry must be treated as missing — no TypeError crash (DI-03)."""
    derived = _make_derived(
        revenue=[
            _entry("2020-09-30", "2020-11-01", "N/A"),   # non-numeric val
        ],
    )
    as_of = date(2020, 11, 15)
    with _mock_derived(derived):
        # Must not raise; current_end should still be found (entry exists) but val is None
        result = compute_surprise_payload("0000320193", as_of)
    # current_end is set from _latest_entry (which doesn't call float()), but _val_at_end
    # must guard and return None for the non-numeric val → no crash and revenue_yoy=None
    assert result["revenue_yoy"] is None


# ---------------------------------------------------------------------------
# T3 — build_pead_surprise_events skips CIK with no ticker mapping
# ---------------------------------------------------------------------------

def test_build_pead_events_skip_no_ticker(tmp_path):
    """CIK whose ticker is absent from the universe's ticker map is skipped and counted (T3).

    A submission file with no "tickers" key (or empty list) has no ticker mapping.
    Such files should be counted in n_skipped_no_ticker and produce no events.
    """
    subs_dir = tmp_path / "submissions"

    # File with no tickers — no CIK→ticker mapping possible
    no_ticker_path = subs_dir / "0000111111.json"
    no_ticker_path.parent.mkdir(parents=True, exist_ok=True)
    no_ticker_path.write_text(json.dumps({
        "tickers": [],   # empty — no ticker
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "acceptanceDateTime": ["2019-06-01T20:00:00.000Z"],
                "reportDate": ["2019-03-31"],
            }
        },
    }), encoding="utf-8")

    # File with a valid ticker (in universe)
    _write_submission(
        subs_dir / "0000222222.json",
        cik="0000222222",
        ticker="INUNIVERSE",
        filings=[{"form": "10-Q", "adt": "2019-06-01T20:00:00.000Z", "reportDate": "2019-03-31"}],
    )

    with patch("research.fundamental_surprise._load_derived_disk_only", return_value={}):
        events, meta = build_pead_surprise_events(
            universe_tickers=["INUNIVERSE"],
            span_start="2019-01-01",
            span_end="2019-12-31",
            submissions_dir=subs_dir,
        )

    assert len(events) == 1
    assert events[0].ticker == "INUNIVERSE"
    assert meta["n_skipped_no_ticker"] == 1, (
        f"Expected 1 skipped-no-ticker file, got {meta['n_skipped_no_ticker']}"
    )
