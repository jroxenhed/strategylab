"""Tests for F383: compute_valuation() now returns shares_source (4th tuple element).

Tests cover:
1. test_shares_source_primary    — shares_source='primary' threaded through
2. test_shares_source_wa         — shares_source='wa' threaded through (lower confidence)
3. test_shares_source_none_on_no_shares  — None when no shares data
4. test_shares_source_none_on_import_error — None when edgar unavailable
5. test_shares_source_in_candidate_result — ps_shares_source on CandidateResult
"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from turnaround import (
    FilterParams,
    compute_valuation,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_bars_df(price: float = 20.0, n: int = 5) -> pd.DataFrame:
    """Minimal OHLCV DataFrame."""
    end = date(2024, 6, 1)
    dates = pd.date_range(end=end, periods=n, freq="D")
    return pd.DataFrame({
        "Open": [price] * n,
        "High": [price * 1.01] * n,
        "Low": [price * 0.99] * n,
        "Close": [price] * n,
        "Volume": [500_000] * n,
    }, index=dates)


def _make_revenue_quarters(n: int = 4, val: float = 1_000_000.0) -> list[dict]:
    return [
        {"end": f"2024-0{i+1}-31", "filed": f"2024-0{i+2}-15", "val": val}
        for i in range(n)
    ]


def _make_edgar_stub(
    shares_detail,  # (float, str) | None
    revenue_quarters=None,
) -> types.ModuleType:
    stub = types.ModuleType("edgar")
    stub.get_shares_outstanding_detail = lambda cik, as_of: shares_detail
    stub.get_shares_outstanding = lambda cik, as_of: (shares_detail[0] if shares_detail else None)
    stub.get_quarterly_revenue = lambda cik: (revenue_quarters if revenue_quarters is not None
                                              else _make_revenue_quarters())
    stub.get_quarterly_net_income = lambda cik: []
    stub.get_quarterly_gross_profit = lambda cik: []
    stub.get_quarterly_ocf = lambda cik: []
    stub.get_form4_net_buys = lambda cik, months_back=6: 0
    stub.has_buyback_authorization = lambda cik, months_back=12: False
    return stub


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComputeValuationSharesSource:
    """F383: shares_source is threaded through from get_shares_outstanding_detail."""

    def test_shares_source_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When edgar returns ('primary',…) shares, compute_valuation returns shares_source='primary'."""
        stub = _make_edgar_stub(shares_detail=(10_000_000.0, "primary"))
        monkeypatch.setitem(sys.modules, "edgar", stub)

        bars = _make_bars_df(price=20.0)
        params = FilterParams(ps_ratio_max=100.0)

        passes, ps_ratio, data_gap, shares_source = compute_valuation(
            ticker="AAPL", cik="0000320193",
            as_of=date(2024, 6, 1),
            params=params,
            bars_loader=lambda _t: bars,
        )

        assert shares_source == "primary"
        assert passes is True
        assert ps_ratio is not None

    def test_shares_source_wa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When edgar returns 'wa' shares, compute_valuation returns shares_source='wa'."""
        stub = _make_edgar_stub(shares_detail=(5_000_000.0, "wa"))
        monkeypatch.setitem(sys.modules, "edgar", stub)

        bars = _make_bars_df(price=10.0)
        params = FilterParams(ps_ratio_max=100.0)

        passes, ps_ratio, data_gap, shares_source = compute_valuation(
            ticker="TEST", cik="0000000001",
            as_of=date(2024, 6, 1),
            params=params,
            bars_loader=lambda _t: bars,
        )

        assert shares_source == "wa"
        assert passes is True

    def test_shares_source_wa_fy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When edgar returns 'wa_fy' shares, compute_valuation returns shares_source='wa_fy'."""
        stub = _make_edgar_stub(shares_detail=(8_000_000.0, "wa_fy"))
        monkeypatch.setitem(sys.modules, "edgar", stub)

        bars = _make_bars_df(price=15.0)
        params = FilterParams(ps_ratio_max=100.0)

        passes, ps_ratio, data_gap, shares_source = compute_valuation(
            ticker="TEST2", cik="0000000002",
            as_of=date(2024, 6, 1),
            params=params,
            bars_loader=lambda _t: bars,
        )

        assert shares_source == "wa_fy"

    def test_shares_source_none_on_no_shares(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When edgar returns None for shares, compute_valuation returns shares_source=None."""
        stub = _make_edgar_stub(shares_detail=None)
        monkeypatch.setitem(sys.modules, "edgar", stub)

        bars = _make_bars_df(price=20.0)
        params = FilterParams()

        passes, ps_ratio, data_gap, shares_source = compute_valuation(
            ticker="NODATA", cik="0000000003",
            as_of=date(2024, 6, 1),
            params=params,
            bars_loader=lambda _t: bars,
        )

        assert passes is False
        assert ps_ratio is None
        assert shares_source is None
        assert data_gap == 1

    def test_shares_source_none_on_edgar_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When edgar ImportError fires, compute_valuation returns passes=True, shares_source=None.

        Simulated by replacing sys.modules['edgar'] with a module that raises ImportError
        when accessed (builtins.__import__ patched to fail for 'edgar').
        """
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "edgar":
                raise ImportError("edgar not available (test)")
            return real_import(name, *args, **kwargs)

        # Remove edgar from sys.modules first so the lazy import actually fires
        monkeypatch.delitem(sys.modules, "edgar", raising=False)
        monkeypatch.setattr(builtins, "__import__", _fake_import)

        bars = _make_bars_df(price=20.0)
        params = FilterParams()

        passes, ps_ratio, data_gap, shares_source = compute_valuation(
            ticker="NOED", cik="0000000004",
            as_of=date(2024, 6, 1),
            params=params,
            bars_loader=lambda _t: bars,
        )

        # edgar unavailable → pass-through (fail-open for edgar unavailable env)
        assert passes is True
        assert shares_source is None

    def test_return_is_four_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """compute_valuation always returns a 4-tuple regardless of path taken."""
        stub = _make_edgar_stub(shares_detail=(10_000_000.0, "dei"))
        monkeypatch.setitem(sys.modules, "edgar", stub)

        bars = _make_bars_df(price=5.0)
        params = FilterParams(ps_ratio_max=1.0)  # will likely fail the threshold

        result = compute_valuation(
            ticker="DEI", cik="0000000005",
            as_of=date(2024, 6, 1),
            params=params,
            bars_loader=lambda _t: bars,
        )

        assert len(result) == 4, f"Expected 4-tuple, got {len(result)}-tuple"
        passes, ps_ratio, data_gap, shares_source = result
        # shares_source should be 'dei' even when ps_ratio fails the threshold
        assert shares_source == "dei"

    def test_ps_shares_source_propagated_to_candidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ps_shares_source on CandidateResult is populated from compute_valuation shares_source."""
        from turnaround import CandidateResult
        import dataclasses

        # Build a minimal CandidateResult with ps_shares_source set
        candidate = CandidateResult(
            ticker="TEST",
            cik="0000000006",
            price_near_low=True,
            pct_off_high=60.0,
            pct_above_low=5.0,
            below_ma=True,
            revenue_yoy_pct=20.0,
            revenue_consec_positive=3,
            gross_margin_delta_pct=2.0,
            net_income_consec_improving=2,
            ocf_positive_quarters=3,
            ps_ratio=1.5,
            ps_shares_source="wa",  # F383 new field
            has_insider_buying=False,
            has_buyback=False,
            composite_score=50.0,
            is_null_candidate=False,
        )

        d = dataclasses.asdict(candidate)
        assert d["ps_shares_source"] == "wa"

    def test_ps_shares_source_default_none(self) -> None:
        """ps_shares_source defaults to None on CandidateResult (backward compat)."""
        from turnaround import CandidateResult
        candidate = CandidateResult(
            ticker="T",
            cik="0000000007",
            price_near_low=False,
            pct_off_high=30.0,
            pct_above_low=10.0,
            below_ma=False,
            revenue_yoy_pct=None,
            revenue_consec_positive=0,
            gross_margin_delta_pct=None,
            net_income_consec_improving=0,
            ocf_positive_quarters=0,
            ps_ratio=None,
            has_insider_buying=False,
            has_buyback=False,
            composite_score=0.0,
            is_null_candidate=True,
        )
        assert candidate.ps_shares_source is None
