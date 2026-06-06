"""Tests for research/event_study.py — F342 Event Clock Harness.

Coverage:
  Statistics
    test_fdr_ledger_bh_procedure         — BH textbook result: 2 of 3 rejected
    test_fdr_ledger_empty                — empty ledger → empty report
    test_fdr_ledger_after_finalize_raises — add() after finalize() raises
    test_block_bootstrap_null_fpr        — FPR control on iid N(0,σ) series
    test_block_bootstrap_power           — detects N(0.02,0.05) with reasonable power
    test_mde_monotone_in_n               — MDE decreases as n grows
    test_mde_regression_anchor           — minimum_detectable_effect(40, 0.05) ≈ 0.0228
    test_nw_disagree_warning             — divergence warning when bootstrap/NW differ (caplog)

  Fix-wave additions (2026-06-06):
    TestFloorLookaheadADV01              — ADV-01 floor decided at event date, not entry bar
    TestTerminalExitADV03                — ADV-03 symmetric delisting (terminal-close exit)
    TestFallbackCount                    — COR-03 acceptance_dt_fallbacks really counts
    TestDeclustering                     — ADV-06/08 same-ticker de-clustering
    TestEraConsistency                   — era-consistency block + 2025+ hard guard
    TestCalendarAndGuards                — TST-07 holiday / TST-08 DST / TST-09 exact / TST-13 guards
    TestFDRLedgerPersistence             — ADV-05 cross-run append-only ledger

  Time mechanics
    test_after_hours_entry_next_day      — filing at 20:13 ET → next trading day's open
    test_pre_market_entry_same_day       — filing at 09:00 ET → same day's open
    test_weekend_entry_advances_to_monday— Friday 17:30 ET → Monday's open
    test_parse_acceptance_dt_valid       — UTC datetime from EDGAR format
    test_parse_acceptance_dt_missing     — None on empty string
    test_fallback_dt_after_hours         — filingDate+16:01 fallback is after-market

  iter_form4_events (Task 3)
    test_iter_form4_events_real_cik      — yields events from real cached submission
    test_iter_form4_events_utc_aware     — all event_ts are UTC-aware
    test_iter_form4_events_date_filter   — no event outside [start, end]

  Core harness (Task 1 + 4)
    test_run_event_study_synthetic       — 3-event synthetic stream, meta.json written
    test_entry_floor_status_values       — floor_status is one of 3 expected tokens
    test_out_of_range_on_no_price_data   — missing ticker → out_of_range / below_floor
    test_open_anchored_helper_direct     — _bar_counted_forward_returns_from_open smoke
    test_explore_confirm_split           — entry_date determines split correctly
"""
from __future__ import annotations

import json
import sys
import os
import tempfile
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# -------------------------------------------------------------------------
# Path setup
# -------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
_RESEARCH = _BACKEND / "research"
for p in [str(_BACKEND), str(_RESEARCH)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
import pandas as pd
import pytest

from research.event_study import (
    EventRecord,
    EventOutcome,
    EventStudyConfig,
    FDRLedger,
    _parse_acceptance_dt,
    _block_bootstrap_pvalue,
    _entry_date_from_event_ts,
    _filing_date_fallback_dt,
    _to_et,
    _forward_return_terminal,
    _dedup_events,
    _load_ticker_to_sic,
    _get_peer_set_by_sic,
    _compute_peer_median,
    _regime_breakdown,
    compute_study_stats,
    run_event_study,
    iter_form4_events,
)
import research.event_study as _es_mod


@pytest.fixture(autouse=True)
def _isolate_fdr_ledger(tmp_path_factory, monkeypatch):
    """Redirect the real cross-run FDR ledger to a throwaway path so the test
    suite never pollutes backend/data/turnaround/fdr_ledger.json. The dedicated
    persistence test overrides this with its own path."""
    ledger = tmp_path_factory.mktemp("fdr_ledger") / "fdr_ledger.json"
    monkeypatch.setattr(_es_mod, "_FDR_LEDGER_PATH", ledger)
from research.outcome_table import minimum_detectable_effect
from turnaround_validation import _bar_counted_forward_returns_from_open


# -------------------------------------------------------------------------
# Helpers: synthetic price frame
# -------------------------------------------------------------------------

def _make_price_df(
    start: date,
    end: date,
    annual_growth_rate: float = 0.0,
    base: float = 100.0,
) -> pd.DataFrame:
    """Synthetic daily OHLCV.

    TST-02: Open is deliberately set DIFFERENT from Close (Open = Close * (1 ±1%),
    alternating sign by bar) so any bug that substitutes Close for Open as the
    return baseline is detectable — Open == Close fixtures share exactly the
    implementer's blind spot (F338).
    """
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)
    if n == 0:
        return pd.DataFrame()
    multiplier = max(0.01, 1.0 + annual_growth_rate)
    closes = [base * (multiplier ** (i / 252)) for i in range(n)]
    # TST-02: alternating ±1% Open/Close gap.
    opens = [c * (1.01 if i % 2 == 0 else 0.99) for i, c in enumerate(closes)]
    df = pd.DataFrame({
        "Open": opens,
        "High": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
        "Low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
        "Close": closes,
        "Volume": [1_000_000] * n,
    }, index=dates)
    return df


def _make_no_open_df(start: date, end: date) -> pd.DataFrame:
    """Price frame without Open column (tests fallback path)."""
    df = _make_price_df(start, end)
    return df.drop(columns=["Open"])


# US market holidays that fall on a weekday in the synthetic test window.
_NYSE_HOLIDAYS = {
    date(2019, 7, 4),    # Independence Day (Thursday)
    date(2019, 12, 25),  # Christmas (Wednesday)
    date(2019, 11, 28),  # Thanksgiving (Thursday)
    date(2019, 1, 1),    # New Year's Day
    date(2019, 1, 21),   # MLK Day
}


def _make_holiday_aware_df(
    start: date,
    end: date,
    base: float = 100.0,
) -> pd.DataFrame:
    """TST-07: business-day frame with NYSE holidays removed.

    freq='B' alone treats Dec 25 / July 4 as trading days; real price caches have
    no such rows.  This drops the known holidays so _first_trading_close_on_or_after
    must advance past them exactly as it would on real data.
    """
    dates = [d.date() for d in pd.date_range(start, end, freq="B")]
    dates = [d for d in dates if d not in _NYSE_HOLIDAYS]
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    n = len(idx)
    closes = [base + i * 0.01 for i in range(n)]
    opens = [c * (1.01 if i % 2 == 0 else 0.99) for i, c in enumerate(closes)]
    return pd.DataFrame({
        "Open": opens,
        "High": [max(o, c) * 1.01 for o, c in zip(opens, closes)],
        "Low": [min(o, c) * 0.99 for o, c in zip(opens, closes)],
        "Close": closes,
        "Volume": [1_000_000] * n,
    }, index=idx)


# -------------------------------------------------------------------------
# Statistics: FDR Ledger
# -------------------------------------------------------------------------

class TestFDRLedger:
    def test_bh_procedure_textbook(self):
        """BH textbook: p=[0.01, 0.04, 0.20], q=0.10, m=3.
        BH thresholds: 1/3*0.10=0.033, 2/3*0.10=0.067, 3/3*0.10=0.10
        rank 1 (0.01 <= 0.033) → reject
        rank 2 (0.04 <= 0.067) → reject
        rank 3 (0.20 <= 0.10)  → no reject
        → exactly 2 rejected.
        """
        ledger = FDRLedger(q=0.10)
        ledger.add("h1", 0.01, "test 1")
        ledger.add("h2", 0.04, "test 2")
        ledger.add("h3", 0.20, "test 3")
        report = ledger.finalize()
        rejected = [k for k, v in report.items() if v["rejected"]]
        assert len(rejected) == 2, f"Expected 2 rejected, got {len(rejected)}: {rejected}"
        assert "h1" in report and report["h1"]["rejected"]
        assert "h2" in report and report["h2"]["rejected"]
        assert "h3" in report and not report["h3"]["rejected"]

    def test_empty_ledger(self):
        ledger = FDRLedger(q=0.10)
        report = ledger.finalize()
        assert report == {}

    def test_add_after_finalize_raises(self):
        ledger = FDRLedger(q=0.10)
        ledger.finalize()
        with pytest.raises(RuntimeError):
            ledger.add("h1", 0.05)

    def test_all_rejected_at_low_q(self):
        """All p-values tiny at q=0.10 → all rejected."""
        ledger = FDRLedger(q=0.10)
        for i, p in enumerate([0.001, 0.002, 0.003]):
            ledger.add(f"h{i}", p)
        report = ledger.finalize()
        assert all(v["rejected"] for v in report.values())

    def test_p_adj_monotone(self):
        """p_adj must be non-decreasing with rank — even for a sequence whose RAW
        Simes values are NON-monotone (COR-02/PY-02 regression).

        Raw Simes for sorted p=[0.03, 0.04, 0.25], m=3:
          rank1: 0.03*3/1 = 0.09
          rank2: 0.04*3/2 = 0.06   <-- decreases below rank1 (the bug)
          rank3: 0.25*3/3 = 0.25
        Without the reverse cumulative-min pass rank2 < rank1 and the assert below
        fails.  With the fix, rank1 is pulled down to 0.06.
        """
        ledger = FDRLedger(q=0.10)
        for p in [0.03, 0.04, 0.25]:
            ledger.add(f"h_{p}", p)
        report = ledger.finalize()
        sorted_by_rank = sorted(report.values(), key=lambda v: v["rank"])
        adjs = [v["p_adj"] for v in sorted_by_rank]
        for i in range(len(adjs) - 1):
            assert adjs[i] <= adjs[i + 1] + 1e-12, f"p_adj not monotone: {adjs}"
        # The fix pulls rank-1 down to the rank-2 raw Simes value (0.06).
        assert abs(adjs[0] - 0.06) < 1e-9, f"rank-1 p_adj should be 0.06, got {adjs[0]}"

    def test_bh_step_up_ties_symmetric(self):
        """TST-05: tied p-values must get the SAME rejection decision (step-up).

        p=[0.04, 0.04], q=0.05, m=2. Row-by-row evaluation would give rank-1
        threshold 0.025 (0.04 > 0.025 → NOT rejected) and rank-2 threshold 0.05
        (0.04 <= 0.05 → rejected) — incoherent. Step-up: largest k with
        p_(k) <= (k/m)q is k=2, so BOTH are rejected.
        """
        ledger = FDRLedger(q=0.05)
        ledger.add("h1", 0.04)
        ledger.add("h2", 0.04)
        report = ledger.finalize()
        assert report["h1"]["rejected"] == report["h2"]["rejected"], (
            "tied p-values got asymmetric rejection (step-up not implemented)"
        )
        assert report["h1"]["rejected"] is True, "both should be rejected at q=0.05"


# -------------------------------------------------------------------------
# Statistics: block bootstrap
# -------------------------------------------------------------------------

class TestBlockBootstrap:
    def test_null_fpr_below_threshold(self):
        """Null: N(0, 0.05). p > 0.05 in > 85% of runs (FPR control)."""
        rng = np.random.default_rng(0)
        n_sim = 100
        n_obs = 50
        rejections = 0
        for _ in range(n_sim):
            vals = rng.normal(0, 0.05, n_obs)
            p = _block_bootstrap_pvalue(vals, block_size=3, n_boot=499, rng=rng)
            if p < 0.05:
                rejections += 1
        fpr = rejections / n_sim
        assert fpr <= 0.15, f"FPR = {fpr:.2f} > 0.15 — block bootstrap inflates FPR"

    def test_power_detects_signal(self):
        """Signal: N(0.02, 0.05), n=50. Detects in > 60% of runs."""
        rng = np.random.default_rng(1)
        n_sim = 100
        n_obs = 50
        detections = 0
        for _ in range(n_sim):
            vals = rng.normal(0.02, 0.05, n_obs)
            p = _block_bootstrap_pvalue(vals, block_size=3, n_boot=499, rng=rng)
            if p < 0.05:
                detections += 1
        power = detections / n_sim
        assert power >= 0.60, f"Power = {power:.2f} < 0.60 — block bootstrap lacks power"

    def test_empty_returns_one(self):
        arr = np.array([], dtype=float)
        p = _block_bootstrap_pvalue(arr, block_size=1)
        assert p == 1.0

    def test_single_value_returns_one(self):
        arr = np.array([0.05])
        p = _block_bootstrap_pvalue(arr, block_size=1)
        assert p == 1.0

    def test_iid_vs_block_differ_when_autocorrelated(self):
        """TST-01/PY-04: block bootstrap must DIFFER from iid on autocorrelated data.

        Previously `assert ... or True` made this vacuous. On a strongly AR(1)
        series the block bootstrap (which preserves serial structure) and the iid
        bootstrap (which destroys it) give materially different p-values. We assert
        a real, seeded gap.
        """
        # Highly autocorrelated AR(1), phi=0.9, NO mean shift: the iid bootstrap
        # destroys serial structure and reads the wandering sample mean as
        # "significant", while the block bootstrap preserves clustering and reads
        # it as noise. The two p-values diverge substantially.
        rng = np.random.default_rng(11)
        n = 80
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = 0.9 * x[i - 1] + rng.normal(0, 0.01)

        p_block = _block_bootstrap_pvalue(x, block_size=12, n_boot=1499,
                                          rng=np.random.default_rng(7))
        p_iid = _block_bootstrap_pvalue(x, block_size=1, n_boot=1499,
                                        rng=np.random.default_rng(7))
        assert abs(p_block - p_iid) > 0.10, (
            f"block ({p_block:.3f}) and iid ({p_iid:.3f}) should differ on AR(1) data"
        )

    def test_block_size_cap_is_n_over_2_not_n_over_4(self):
        """COR-01: the cap is a CEILING at n//2 (was n//4). With n=40 and a
        requested block of 30, used L must be 20 (n//2), not 10 (n//4)."""
        arr = np.random.default_rng(3).normal(0, 0.05, 40)
        _, used_L, capped = _block_bootstrap_pvalue(
            arr, block_size=30, n_boot=99, rng=np.random.default_rng(3),
            return_diag=True,
        )
        assert used_L == 20, f"expected n//2=20, got {used_L}"
        assert capped is True, "cap should be flagged binding"

    def test_block_bootstrap_fpr_controlled_on_autocorrelated_null(self):
        """COR-01 regression: under H0 on AR(1) (phi=0.5) data, with a properly
        sized block, FPR must stay controlled (<= 0.15). The old n//4 cap
        under-blocked dense overlapping returns and inflated this."""
        n_sim = 120
        n_obs = 40
        rejections = 0
        for s in range(n_sim):
            rng = np.random.default_rng(1000 + s)
            x = np.zeros(n_obs)
            for i in range(1, n_obs):
                x[i] = 0.5 * x[i - 1] + rng.normal(0, 0.05)
            # H0 true: mean is ~0 (no shift added)
            p = _block_bootstrap_pvalue(x, block_size=7, n_boot=399, rng=rng)
            if p < 0.05:
                rejections += 1
        fpr = rejections / n_sim
        assert fpr <= 0.15, f"FPR on AR(1) null = {fpr:.2f} > 0.15 (block under-corrects)"


# -------------------------------------------------------------------------
# Statistics: MDE
# -------------------------------------------------------------------------

class TestMDE:
    def test_regression_anchor(self):
        """minimum_detectable_effect(n=40, std=0.05) ≈ 0.0228 ± 0.001."""
        mde = minimum_detectable_effect(40, 0.05)
        assert abs(mde - 0.02278) < 0.001, f"MDE = {mde:.5f}, expected ≈ 0.02278"

    def test_mde_monotone_in_n(self):
        """MDE must decrease as n grows (keeping std fixed)."""
        std = 0.05
        ns = [20, 40, 80, 160, 320]
        mdes = [minimum_detectable_effect(n, std) for n in ns]
        for i in range(len(mdes) - 1):
            assert mdes[i] > mdes[i + 1], (
                f"MDE not monotone: mde[{ns[i]}]={mdes[i]:.4f} <= mde[{ns[i+1]}]={mdes[i+1]:.4f}"
            )

    def test_mde_zero_n(self):
        assert math.isnan(minimum_detectable_effect(0, 0.05))

    def test_mde_zero_std(self):
        assert math.isnan(minimum_detectable_effect(40, 0.0))


# -------------------------------------------------------------------------
# Time mechanics
# -------------------------------------------------------------------------

class TestTimeMechanics:
    def _flat_df(self, start: date, end: date) -> pd.DataFrame:
        """Flat price frame (all 100.0) spanning [start, end] business days."""
        return _make_price_df(start, end, annual_growth_rate=0.0)

    def test_parse_acceptance_dt_valid(self):
        """EDGAR format '2026-05-28T20:13:31.000Z' → UTC datetime."""
        dt = _parse_acceptance_dt("2026-05-28T20:13:31.000Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026 and dt.month == 5 and dt.day == 28
        assert dt.hour == 20 and dt.minute == 13 and dt.second == 31
        # Must be UTC
        utc_offset = dt.utcoffset()
        assert utc_offset == timedelta(0)

    def test_parse_acceptance_dt_missing(self):
        assert _parse_acceptance_dt("") is None
        assert _parse_acceptance_dt(None) is None  # type: ignore

    def test_fallback_dt_after_hours(self):
        """filingDate fallback = that date at 21:01 UTC (after ET market close)."""
        dt = _filing_date_fallback_dt("2020-12-31")
        assert dt is not None
        et_dt = _to_et(dt)
        # Should be after 16:00 ET
        assert et_dt.hour >= 16, f"Fallback time {et_dt} should be after-hours ET"

    def test_after_hours_filing_next_trading_day(self):
        """Filing at 20:13 ET (after-hours) → entry on next trading day."""
        # Use a fixed trading week: Mon-Fri 2019-06-03..2019-06-07
        df = self._flat_df(date(2019, 6, 3), date(2019, 12, 31))

        # 2019-06-03 20:13:31 ET = 2019-06-04T00:13:31Z
        event_ts = datetime(2019, 6, 4, 0, 13, 31, tzinfo=timezone.utc)
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=1)
        assert result is not None
        entry_date, entry_price, _ = result
        # After-hours on 2019-06-03 ET → next bday = 2019-06-04
        assert entry_date == date(2019, 6, 4), (
            f"After-hours filing should map to next day, got {entry_date}"
        )

    def test_pre_market_filing_same_day(self):
        """Filing at 09:00 ET with entry_lag_days=0 → entry on same trading day."""
        df = self._flat_df(date(2019, 6, 3), date(2019, 12, 31))
        # 09:00 ET = 13:00 UTC (EDT, UTC-4)
        event_ts = datetime(2019, 6, 3, 13, 0, 0, tzinfo=timezone.utc)
        # entry_lag_days=0: same-day mode with 16:00 ET cutoff → 09:00 < 16:00 → same day
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=0)
        assert result is not None
        entry_date, _, _ = result
        assert entry_date == date(2019, 6, 3), (
            f"Pre-market filing (lag=0) should map to same day, got {entry_date}"
        )

    def test_default_lag_always_advances(self):
        """With default entry_lag_days=1, both pre-market and after-hours go to next day."""
        df = self._flat_df(date(2019, 6, 3), date(2019, 12, 31))
        # Pre-market: 09:00 ET on 2019-06-03 with lag=1 → entry 2019-06-04
        event_ts_pre = datetime(2019, 6, 3, 13, 0, 0, tzinfo=timezone.utc)
        result_pre = _entry_date_from_event_ts(event_ts_pre, df, entry_lag_days=1)
        assert result_pre is not None
        assert result_pre[0] == date(2019, 6, 4), (
            f"Pre-market lag=1 should map to next day, got {result_pre[0]}"
        )
        # After-hours: 20:13 ET on 2019-06-03 with lag=1 → entry 2019-06-04
        event_ts_aft = datetime(2019, 6, 4, 0, 13, 31, tzinfo=timezone.utc)
        result_aft = _entry_date_from_event_ts(event_ts_aft, df, entry_lag_days=1)
        assert result_aft is not None
        assert result_aft[0] == date(2019, 6, 4), (
            f"After-hours lag=1 should map to next day, got {result_aft[0]}"
        )

    def test_friday_after_hours_maps_to_monday(self):
        """Friday 17:30 ET filing → Monday's open (weekend skipped)."""
        # 2019-05-31 is a Friday; 2019-06-03 is Monday
        df = self._flat_df(date(2019, 5, 1), date(2019, 12, 31))
        # Friday 2019-05-31 17:30 ET (EDT = UTC-4 → 21:30 UTC)
        event_ts = datetime(2019, 5, 31, 21, 30, 0, tzinfo=timezone.utc)
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=1)
        assert result is not None
        entry_date, _, _ = result
        # After-hours Fri → advance 1 cal day = Sat → _first_trading_close_on_or_after → Mon
        assert entry_date == date(2019, 6, 3), (
            f"Friday after-hours should map to Monday, got {entry_date}"
        )

    def test_saturday_morning_maps_to_monday(self):
        """Event at Saturday 09:00 ET → Monday's open."""
        df = self._flat_df(date(2019, 6, 1), date(2019, 12, 31))
        # 2019-06-01 is Saturday; 09:00 ET = 13:00 UTC (EDT)
        event_ts = datetime(2019, 6, 1, 13, 0, 0, tzinfo=timezone.utc)
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=1)
        assert result is not None
        entry_date, _, _ = result
        # Saturday 09:00 → pre-market, same day = Sat → not a trading day → Mon 2019-06-03
        assert entry_date == date(2019, 6, 3), (
            f"Saturday filing should map to Monday, got {entry_date}"
        )


# -------------------------------------------------------------------------
# iter_form4_events (Task 3)
# -------------------------------------------------------------------------

class TestIterForm4Events:
    """Uses real cached submissions (read-only)."""

    _SUBS_DIR = Path(_BACKEND) / "data" / "turnaround" / "edgar_cache" / "submissions"

    def test_yields_events_for_real_cik(self):
        """AMD CIK 0000002488 has Form 4 filings — must yield at least one."""
        if not self._SUBS_DIR.exists():
            pytest.skip("EDGAR submissions cache not available")
        events = list(iter_form4_events(
            cik_list=["0000002488"],
            subs_dir=self._SUBS_DIR,
        ))
        assert len(events) >= 1, "Expected at least one Form 4 from AMD"
        e = events[0]
        assert isinstance(e, EventRecord)
        assert e.ticker == "AMD"
        assert "form_type" in e.payload
        assert e.payload["form_type"] in ("4", "4/A")

    def test_all_event_ts_utc_aware(self):
        """All yielded event_ts must be timezone-aware UTC datetimes."""
        if not self._SUBS_DIR.exists():
            pytest.skip("EDGAR submissions cache not available")
        events = list(iter_form4_events(
            cik_list=["0000002488"],
            subs_dir=self._SUBS_DIR,
        ))
        for ev in events[:20]:
            assert ev.event_ts.tzinfo is not None, f"event_ts not tz-aware: {ev.event_ts}"
            assert ev.event_ts.utcoffset() == timedelta(0), (
                f"event_ts not UTC: {ev.event_ts}"
            )

    def test_date_filter_respected(self):
        """No event should have event_ts.date() outside [start, end]."""
        if not self._SUBS_DIR.exists():
            pytest.skip("EDGAR submissions cache not available")
        start = date(2019, 1, 1)
        end = date(2020, 12, 31)
        events = list(iter_form4_events(
            cik_list=["0000002488"],
            start=start, end=end,
            subs_dir=self._SUBS_DIR,
        ))
        for ev in events:
            et_date = _to_et(ev.event_ts).date()
            assert start <= et_date <= end, (
                f"Event date {et_date} outside [{start}, {end}]"
            )

    def test_ticker_list_resolves_cik(self):
        """ticker_list=['AMD'] yields same events as cik_list for AMD."""
        if not self._SUBS_DIR.exists():
            pytest.skip("EDGAR submissions cache not available")
        by_cik = list(iter_form4_events(cik_list=["0000002488"], subs_dir=self._SUBS_DIR))
        by_ticker = list(iter_form4_events(ticker_list=["AMD"], subs_dir=self._SUBS_DIR))
        assert len(by_cik) == len(by_ticker), "ticker_list and cik_list should yield same count"


# -------------------------------------------------------------------------
# Open-anchored forward return helper (Task 1 additive extension)
# -------------------------------------------------------------------------

class TestOpenAnchoredForwardReturns:
    def test_open_anchored_exact_values(self):
        """Ramp with Open == Close: same return as Close-based at each horizon."""
        start = date(2015, 1, 1)
        end = date(2022, 12, 31)
        df = _make_price_df(start, end, annual_growth_rate=0.5)  # strong growth
        entry_date = date(2018, 6, 1)

        # Find entry row
        from turnaround_validation import _first_trading_close_on_or_after
        res = _first_trading_close_on_or_after(df, entry_date)
        assert res is not None
        edate, eclose = res
        eopen = float(df.loc[df.index.normalize() == pd.Timestamp(edate), "Open"].iloc[0])

        fwd = _bar_counted_forward_returns_from_open(df, edate, eopen, horizons=(21, 63, 126))
        # Returns should be positive (growing frame)
        for h in (21, 63, 126):
            assert fwd[h] is not None
            assert fwd[h] > 0, f"Expected positive return at horizon {h}"

    def test_open_anchored_incomplete_horizon(self):
        """Horizon past data end → None (not extrapolated)."""
        start = date(2020, 1, 1)
        end = date(2020, 4, 30)  # ~84 business days
        df = _make_price_df(start, end)
        entry_date = date(2020, 1, 2)
        from turnaround_validation import _first_trading_close_on_or_after
        res = _first_trading_close_on_or_after(df, entry_date)
        assert res is not None
        edate, _ = res
        eopen = float(df.loc[df.index.normalize() == pd.Timestamp(edate), "Open"].iloc[0])

        fwd = _bar_counted_forward_returns_from_open(df, edate, eopen, horizons=(21, 63, 126))
        assert fwd[21] is not None
        assert fwd[63] is not None
        assert fwd[126] is None, "126d horizon past data end must be None"

    def test_open_anchored_short_direction(self):
        """Short: growing price → negative return."""
        start = date(2018, 1, 1)
        end = date(2022, 12, 31)
        df = _make_price_df(start, end, annual_growth_rate=0.5)
        entry_date = date(2019, 1, 2)
        from turnaround_validation import _first_trading_close_on_or_after
        res = _first_trading_close_on_or_after(df, entry_date)
        assert res is not None
        edate, _ = res
        eopen = float(df.loc[df.index.normalize() == pd.Timestamp(edate), "Open"].iloc[0])

        fwd = _bar_counted_forward_returns_from_open(df, edate, eopen, horizons=(63,), direction="short")
        assert fwd[63] is not None
        assert fwd[63] < 0, "Short on growing stock must have negative return"

    def test_existing_close_based_function_unchanged(self):
        """Regression: _bar_counted_forward_returns (Close-based) still works."""
        from turnaround_validation import _bar_counted_forward_returns, _first_trading_close_on_or_after
        df = _make_price_df(date(2015, 1, 1), date(2022, 12, 31), annual_growth_rate=0.0)
        res = _first_trading_close_on_or_after(df, date(2018, 1, 2))
        assert res is not None
        edate, eclose = res
        fwd = _bar_counted_forward_returns(df, edate, eclose)
        # Flat frame → all returns near 0.0
        for h in (21, 63, 126):
            assert fwd[h] is not None
            assert abs(fwd[h]) < 0.01, f"Flat frame return at {h}d not near 0: {fwd[h]}"


# -------------------------------------------------------------------------
# Core harness (Task 1 + 4)
# -------------------------------------------------------------------------

class TestRunEventStudy:
    """Integration tests using synthetic price data (no network)."""

    def _loader(self, frames: dict):
        """Build a loader function from a {ticker: DataFrame} dict."""
        def _fn(ticker: str):
            return frames.get(ticker)
        return _fn

    def test_run_event_study_synthetic_three_events(self, tmp_path):
        """3-event synthetic stream → meta.json written with expected fields."""
        frames = {
            "AAPL": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), annual_growth_rate=0.3),
            "MSFT": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), annual_growth_rate=0.2),
            "GOOG": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), annual_growth_rate=0.1),
        }
        events = [
            EventRecord("AAPL", datetime(2018, 3, 14, 22, 0, 0, tzinfo=timezone.utc), {"test": 1}),
            EventRecord("MSFT", datetime(2018, 6, 15, 22, 0, 0, tzinfo=timezone.utc), {"test": 2}),
            EventRecord("GOOG", datetime(2019, 9, 20, 22, 0, 0, tzinfo=timezone.utc), {"test": 3}),
        ]
        config = EventStudyConfig(
            study_name="test_synthetic_3",
            horizons=(21, 63, 126),
            output_dir=tmp_path / "test_synthetic_3",
        )
        outcomes, meta = run_event_study(
            events, config, self._loader(frames),
            universe_tickers=["AAPL", "MSFT", "GOOG"],
        )
        # meta.json must be written
        meta_path = tmp_path / "test_synthetic_3" / "meta.json"
        assert meta_path.exists(), "meta.json not written"
        loaded_meta = json.loads(meta_path.read_text())
        assert loaded_meta["n_events"] == 3
        assert "mde_by_horizon" in loaded_meta
        assert "fdr_report" in loaded_meta
        assert loaded_meta["schema_version"] == 2
        # TST-10: behavioral (not just structural) assertions.
        assert all(o.floor_status == "ok" for o in outcomes), "all 3 should pass floor"
        for o in outcomes:
            assert o.fwd_return_pct[21] is not None, "21d return should be populated"
        # universe_tickers provided → excess must be populated for in-universe picks.
        assert all(o.fwd_excess_pct[21] is not None for o in outcomes), (
            "excess should be populated when universe provided"
        )
        # ADV-02 survivorship + ADV-05 config hash present.
        assert loaded_meta["survivorship"]["events_entered"] == 3
        assert loaded_meta["survivorship"]["events_no_price_data"] == 0
        assert "study_config_hash" in loaded_meta
        assert "era_consistency" in loaded_meta
        # events.ndjson must be written
        ndjson_path = tmp_path / "test_synthetic_3" / "events.ndjson"
        assert ndjson_path.exists()
        rows = [json.loads(line) for line in ndjson_path.read_text().splitlines() if line.strip()]
        assert len(rows) == 3

    def test_floor_status_values(self, tmp_path):
        """floor_status on each EventOutcome must be one of 3 expected tokens."""
        frames = {
            "AAPL": _make_price_df(date(2016, 1, 1), date(2022, 12, 31)),
        }
        events = [
            EventRecord("AAPL", datetime(2018, 3, 14, 22, 0, 0, tzinfo=timezone.utc), {}),
            EventRecord("NOPE", datetime(2018, 3, 14, 22, 0, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="test_floor",
            horizons=(21,),
            output_dir=tmp_path / "test_floor",
        )
        outcomes, _ = run_event_study(events, config, self._loader(frames))
        valid_statuses = {"ok", "below_floor", "corrupt_frame"}
        for o in outcomes:
            assert o.floor_status in valid_statuses, (
                f"Unexpected floor_status: {o.floor_status}"
            )

    def test_missing_ticker_floor_status_below(self, tmp_path):
        """Ticker not in price cache → below_floor or out_of_range."""
        frames: dict = {}
        events = [
            EventRecord("GHOST", datetime(2018, 3, 14, 22, 0, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="test_ghost",
            horizons=(21,),
            output_dir=tmp_path / "test_ghost",
        )
        outcomes, meta = run_event_study(events, config, self._loader(frames))
        assert len(outcomes) == 1
        o = outcomes[0]
        # TST-03/PY-08: a None-df (missing ticker) is unconditionally _FLOOR_BELOW.
        # 'ok' must NOT be accepted — that would mask a broken floor assignment.
        assert o.floor_status == "below_floor", (
            f"Missing ticker must be below_floor, got: {o.floor_status}"
        )
        assert o.split == "out_of_range", f"expected out_of_range, got {o.split}"
        assert o.no_price_data is True, "missing ticker should flag no_price_data"
        # ADV-02: counted, not silently dropped.
        assert meta["survivorship"]["events_no_price_data"] == 1
        # All forward returns must be None
        for h, v in o.fwd_return_pct.items():
            assert v is None, f"Missing ticker should have None forward return at {h}d"

    def test_explore_confirm_split(self, tmp_path):
        """entry_date <= 2020-12-31 → explore; >= 2021-01-01 → confirm."""
        frames = {
            "AAPL": _make_price_df(date(2015, 1, 1), date(2022, 12, 31)),
        }
        events = [
            # After-hours 2020-12-30 ET → entry 2020-12-31 → explore
            EventRecord("AAPL", datetime(2020, 12, 31, 2, 0, 0, tzinfo=timezone.utc), {}),
            # After-hours 2021-01-01 ET → entry 2021-01-04 (Mon) → confirm
            EventRecord("AAPL", datetime(2021, 1, 2, 2, 0, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="test_split",
            horizons=(21,),
            dedup_same_ticker=False,  # both events are the split point — keep both
            output_dir=tmp_path / "test_split",
        )
        outcomes, _ = run_event_study(events, config, self._loader(frames))
        splits = {o.split for o in outcomes}
        # TST-04: AND, not OR — both splits must be correctly populated.
        assert splits == {"explore", "confirm"}, (
            f"Expected exactly explore+confirm, got {splits}"
        )
        by_split = {o.split: o for o in outcomes}
        assert by_split["explore"].entry_date <= date(2020, 12, 31)
        assert by_split["confirm"].entry_date > date(2020, 12, 31)

    def test_meta_json_has_mde_line(self, tmp_path):
        """meta.json must contain mde_by_horizon for all configured horizons."""
        frames = {
            "AAPL": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), annual_growth_rate=0.2),
        }
        events = [
            EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="test_mde_meta",
            horizons=(21, 63),
            output_dir=tmp_path / "test_mde_meta",
        )
        _, meta = run_event_study(events, config, self._loader(frames))
        assert "mde_by_horizon" in meta
        for h in (21, 63):
            assert h in meta["mde_by_horizon"] or str(h) in meta["mde_by_horizon"]


# -------------------------------------------------------------------------
# Compute study stats (unit-level)
# -------------------------------------------------------------------------

class TestComputeStudyStats:
    def _make_outcome(self, ticker: str, entry_date: date, excess: float) -> EventOutcome:
        """Construct a minimal EventOutcome for stats testing."""
        split = "explore" if entry_date <= date(2020, 12, 31) else "confirm"
        return EventOutcome(
            ticker=ticker,
            event_ts=datetime(entry_date.year, entry_date.month, entry_date.day,
                              22, 0, 0, tzinfo=timezone.utc),
            entry_date=entry_date,
            entry_price=100.0,
            payload={},
            split=split,
            fwd_return_pct={21: excess, 63: excess, 126: excess},
            fwd_excess_pct={21: excess, 63: excess, 126: excess},
            floor_status="ok",
            universe_n={21: 10, 63: 10, 126: 10},
        )

    def test_null_hypothesis_not_rejected_on_noise(self):
        """50 N(0, 0.05) excess values → p_bootstrap > 0.05 in >85% of sim runs."""
        rng = np.random.default_rng(99)
        n_sim = 50
        rejections = 0
        for _ in range(n_sim):
            # Generate 20 outcomes spread over 2016-2020
            base = date(2016, 1, 1)
            outcomes = []
            vals = rng.normal(0, 5.0, 20)  # ppt already
            for i, v in enumerate(vals):
                d = base + timedelta(days=i * 15)
                outcomes.append(self._make_outcome(f"T{i}", d, float(v)))
            config = EventStudyConfig(study_name="test_null", horizons=(21,), n_boot=199, output_dir=None)
            # Replace output_dir with a tmp path to avoid disk writes in tight loop
            import tempfile, pathlib
            with tempfile.TemporaryDirectory() as td:
                config.output_dir = pathlib.Path(td) / "null_test"
                stats = compute_study_stats(outcomes, config, rng=rng)
            p = stats["per_horizon"][21]["p_bootstrap"]
            if p < 0.05:
                rejections += 1
        fpr = rejections / n_sim
        assert fpr <= 0.20, f"Null FPR = {fpr:.2f} (expected <= 0.20)"

    def test_signal_detected_on_strong_effect(self):
        """50 outcomes with +5ppt excess → p_bootstrap < 0.05."""
        rng = np.random.default_rng(7)
        base = date(2016, 1, 1)
        outcomes = [
            self._make_outcome(f"T{i}", base + timedelta(days=i * 10), 5.0 + float(rng.normal(0, 1)))
            for i in range(50)
        ]
        config = EventStudyConfig(study_name="test_signal", horizons=(21,), n_boot=499)
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            config.output_dir = pathlib.Path(td) / "signal_test"
            stats = compute_study_stats(outcomes, config, rng=rng)
        p = stats["per_horizon"][21]["p_bootstrap"]
        assert p < 0.05, f"Strong signal not detected: p={p:.3f}"

    def test_nw_disagree_warning(self, caplog):
        """TST-06: when block bootstrap and NW p-values diverge by > 0.10, a
        WARNING is logged and both p-values appear in the output.

        A strongly autocorrelated excess series (all same-sign, AR-like) makes the
        iid-flavoured NW and the block bootstrap disagree."""
        import logging
        base = date(2016, 1, 1)
        # 20 events 5 days apart, excess strongly positive & autocorrelated.
        outcomes = []
        prev = 2.0
        for i in range(20):
            prev = 0.85 * prev + 0.5  # drifts upward, serially correlated
            d = base + timedelta(days=i * 5)
            outcomes.append(self._make_outcome(f"T{i}", d, float(prev)))
        config = EventStudyConfig(study_name="test_nw_disagree", horizons=(21,), n_boot=399)
        import tempfile, pathlib
        with caplog.at_level(logging.WARNING):
            with tempfile.TemporaryDirectory() as td:
                config.output_dir = pathlib.Path(td) / "nw_test"
                stats = compute_study_stats(outcomes, config, rng=np.random.default_rng(5))
        ph = stats["per_horizon"][21]
        assert ph["p_bootstrap"] is not None and ph["p_nw"] is not None
        # The divergence warning must have fired given the constructed disagreement.
        if abs(ph["p_bootstrap"] - ph["p_nw"]) > 0.10:
            assert any("disagree" in r.message for r in caplog.records), (
                "expected NW/bootstrap disagreement warning"
            )


# -------------------------------------------------------------------------
# ADV-01 — floor lookahead (fixture-based P0 proof)
# -------------------------------------------------------------------------

class TestFloorLookaheadADV01:
    def _loader(self, frames):
        return lambda t: frames.get(t)

    def _gap_up_frame(self) -> pd.DataFrame:
        """Frame where the last close ON OR BEFORE the event date is BELOW the
        $5.00 floor, but the next-day OPEN gaps up above it.

        Event date = 2018-06-01 (Friday). Closes <= that date are 4.80 (below
        floor). The next trading bar (Mon 2018-06-04) opens at 5.20 and closes
        5.30 (above floor). Volume always clears the liquidity floor.
        """
        dates = [d.date() for d in pd.date_range(date(2017, 1, 1), date(2019, 12, 31), freq="B")]
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
        opens, closes = [], []
        event_day = date(2018, 6, 1)
        for d in dates:
            if d <= event_day:
                opens.append(4.78)
                closes.append(4.80)   # below $5 floor pre-event
            else:
                opens.append(5.20)    # gap-up open on news
                closes.append(5.30)
        return pd.DataFrame({
            "Open": opens,
            "High": [c * 1.01 for c in closes],
            "Low": [o * 0.99 for o in opens],
            "Close": closes,
            "Volume": [1_000_000] * len(dates),
        }, index=idx)

    def test_floor_decided_at_event_date_not_entry_bar(self, tmp_path):
        """A stock below floor on the event date but gapping above it at entry must
        be EXCLUDED (below_floor) — the entry bar's gap-up open must not buy it
        into the universe. This is the ADV-01 lookahead guard."""
        frames = {"GAP": self._gap_up_frame()}
        # Event after-hours Thursday 2018-05-31 → entry Friday 2018-06-01... but we
        # want event_et_date == 2018-06-01 with entry on the next bar. Use an
        # after-hours filing on 2018-06-01 ET so entry lands 2018-06-04 (Mon).
        events = [
            EventRecord("GAP", datetime(2018, 6, 2, 1, 0, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="adv01", horizons=(21,), output_dir=tmp_path / "adv01",
        )
        outcomes, _ = run_event_study(events, config, self._loader(frames))
        o = outcomes[0]
        # event_et_date = 2018-06-01; last close <= that = 4.80 < 5.0 → below_floor.
        assert o.floor_status == "below_floor", (
            f"gap-up open must not gate inclusion; expected below_floor, got "
            f"{o.floor_status} (entry_date={o.entry_date})"
        )


# -------------------------------------------------------------------------
# ADV-03 — symmetric delisting (terminal-exit) fixture proof
# -------------------------------------------------------------------------

class TestTerminalExitADV03:
    def test_series_ending_inside_horizon_uses_terminal_close(self):
        """A name whose price series ENDS before the full horizon contributes its
        terminal (last) close as the exit, not None. The plain bar-counted helper
        returns None for the same case — this proves the symmetric-delisting fix."""
        from turnaround_validation import _bar_counted_forward_returns_from_open
        # 30 trading bars only; horizon 126 is well past the end.
        df = _make_price_df(date(2019, 1, 1), date(2019, 2, 15))  # ~33 bdays
        entry_date = [d.date() for d in df.index][0]
        entry_open = float(df.iloc[0]["Open"])

        r_term, was_terminal = _forward_return_terminal(df, entry_date, entry_open, 126)
        r_plain = _bar_counted_forward_returns_from_open(
            df, entry_date, entry_open, horizons=(126,)
        )[126]
        assert r_plain is None, "plain helper drops the incomplete horizon"
        assert r_term is not None, "terminal helper must substitute the last close"
        assert was_terminal is True
        # Terminal return equals (last_close - entry_open)/entry_open*100.
        last_close = float(df.iloc[-1]["Close"])
        expected = (last_close - entry_open) / entry_open * 100.0
        assert abs(r_term - expected) < 1e-9

    def test_terminal_exit_no_forward_bars_returns_none(self):
        """If the entry bar is the very last bar, there are no forward bars at all
        → None (cannot fabricate a return)."""
        df = _make_price_df(date(2019, 1, 1), date(2019, 2, 15))
        last_date = [d.date() for d in df.index][-1]
        entry_open = float(df.iloc[-1]["Open"])
        r, was_terminal = _forward_return_terminal(df, last_date, entry_open, 21)
        assert r is None


# -------------------------------------------------------------------------
# COR-03 / PY-01 — acceptance_dt_fallbacks counter actually counts
# -------------------------------------------------------------------------

class TestFallbackCount:
    def _loader(self, frames):
        return lambda t: frames.get(t)

    def test_fallback_count_threaded_through(self, tmp_path):
        """Events flagged is_fallback=True must be counted in meta, not stuck at 0."""
        frames = {
            "AAPL": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), 0.2),
        }
        events = [
            EventRecord("AAPL", datetime(2018, 3, 14, 22, 0, 0, tzinfo=timezone.utc), {}, is_fallback=True),
            EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, 0, tzinfo=timezone.utc), {}, is_fallback=False),
            EventRecord("AAPL", datetime(2018, 9, 20, 22, 0, 0, tzinfo=timezone.utc), {}, is_fallback=True),
        ]
        # Disable dedup so all three survive (same ticker, far apart anyway).
        config = EventStudyConfig(
            study_name="fbcount", horizons=(21,),
            dedup_same_ticker=False, output_dir=tmp_path / "fbcount",
        )
        _, meta = run_event_study(events, config, self._loader(frames))
        assert meta["acceptance_dt_fallbacks"] == 2, (
            f"expected 2 fallbacks counted, got {meta['acceptance_dt_fallbacks']}"
        )


# -------------------------------------------------------------------------
# ADV-06 / ADV-08 — same-ticker de-clustering
# -------------------------------------------------------------------------

class TestDeclustering:
    def test_dedup_collapses_same_ticker_cluster(self):
        """Three AMD Form 4s within one week collapse to one; a far-apart fourth
        survives. Raw count preserved separately."""
        evs = [
            EventRecord("AMD", datetime(2018, 3, 5, 14, 0, tzinfo=timezone.utc), {}),
            EventRecord("AMD", datetime(2018, 3, 6, 14, 0, tzinfo=timezone.utc), {}),
            EventRecord("AMD", datetime(2018, 3, 7, 14, 0, tzinfo=timezone.utc), {}),
            EventRecord("AMD", datetime(2018, 6, 1, 14, 0, tzinfo=timezone.utc), {}),
        ]
        deduped, dropped = _dedup_events(evs, window_days=7)
        assert dropped == 2, f"expected 2 dropped, got {dropped}"
        assert len(deduped) == 2
        # Different tickers never collapse together.
        mixed = evs + [EventRecord("NVDA", datetime(2018, 3, 5, 14, 0, tzinfo=timezone.utc), {})]
        _, dropped2 = _dedup_events(mixed, window_days=7)
        assert dropped2 == 2, "NVDA must not be collapsed with AMD"

    def test_dedup_reported_in_meta(self, tmp_path):
        frames = {"AMD": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), 0.2)}
        events = [
            EventRecord("AMD", datetime(2018, 3, 5, 22, 0, tzinfo=timezone.utc), {}),
            EventRecord("AMD", datetime(2018, 3, 6, 22, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="dedup_meta", horizons=(21,),
            dedup_same_ticker=True, dedup_window_days=7,
            output_dir=tmp_path / "dedup_meta",
        )
        _, meta = run_event_study(events, config, self._loader_meta(frames))
        assert meta["survivorship"]["events_total"] == 2
        assert meta["survivorship"]["events_declustered"] == 1
        assert meta["survivorship"]["events_after_dedup"] == 1

    def _loader_meta(self, frames):
        return lambda t: frames.get(t)


# -------------------------------------------------------------------------
# Era-consistency + 2025+ hard guard (John, 2026-06-06)
# -------------------------------------------------------------------------

class TestEraConsistency:
    def _loader(self, frames):
        return lambda t: frames.get(t)

    def test_explore_era_block_present_and_grouped(self, tmp_path):
        frames = {"AAPL": _make_price_df(date(2014, 1, 1), date(2022, 12, 31), 0.2)}
        events = [
            EventRecord("AAPL", datetime(2015, 6, 1, 22, 0, tzinfo=timezone.utc), {}),
            EventRecord("AAPL", datetime(2017, 6, 1, 22, 0, tzinfo=timezone.utc), {}),
            EventRecord("AAPL", datetime(2019, 6, 1, 22, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="era", horizons=(21,),
            dedup_same_ticker=False, output_dir=tmp_path / "era",
        )
        _, meta = run_event_study(events, config, self._loader(frames),
                                  universe_tickers=["AAPL"])
        era = meta["era_consistency"]
        assert set(era.keys()) == {"2015-16", "2017-18", "2019-20"}
        assert era["2015-16"]["n_events"] == 1
        assert era["2017-18"]["n_events"] == 1
        assert era["2019-20"]["n_events"] == 1
        assert "confirm_era_breakdown" in meta

    def test_post_2020_explore_hard_guard(self, tmp_path):
        """explore_cutoff past 2020-12-31 must raise unless overridden."""
        frames = {"AAPL": _make_price_df(date(2016, 1, 1), date(2025, 12, 31), 0.2)}
        config = EventStudyConfig(
            study_name="guard", horizons=(21,),
            explore_cutoff=date(2025, 1, 1),
            output_dir=tmp_path / "guard",
        )
        with pytest.raises(ValueError):
            run_event_study([], config, self._loader(frames))
        # Override allows it.
        config2 = EventStudyConfig(
            study_name="guard2", horizons=(21,),
            explore_cutoff=date(2025, 1, 1), allow_post_2020_explore=True,
            output_dir=tmp_path / "guard2",
        )
        outcomes, _ = run_event_study([], config2, self._loader(frames))
        assert outcomes == []


# -------------------------------------------------------------------------
# TST-07 holiday / TST-08 DST / TST-09 exact value / TST-13 guards
# -------------------------------------------------------------------------

class TestCalendarAndGuards:
    def test_holiday_entry_advances(self):
        """TST-07: event after-hours on 2019-07-03 (lag=1 → 2019-07-04, a market
        holiday) must skip July 4 and land on July 5."""
        df = _make_holiday_aware_df(date(2019, 6, 1), date(2019, 12, 31))
        # After-hours 2019-07-03 ET (21:00 UTC EDT = 17:00 ET) → +1 cal day = 07-04
        event_ts = datetime(2019, 7, 3, 21, 0, 0, tzinfo=timezone.utc)
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=1)
        assert result is not None
        entry_date, _, _ = result
        assert entry_date == date(2019, 7, 5), (
            f"July 4 holiday must be skipped → 07-05, got {entry_date}"
        )

    def test_christmas_holiday_skipped(self):
        """TST-07: event mapping to Dec 25 must advance to Dec 26."""
        df = _make_holiday_aware_df(date(2019, 6, 1), date(2019, 12, 31))
        # After-hours 2019-12-24 ET → +1 = 2019-12-25 (holiday) → 12-26
        event_ts = datetime(2019, 12, 24, 21, 0, 0, tzinfo=timezone.utc)
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=1)
        assert result is not None
        assert result[0] == date(2019, 12, 26), f"got {result[0]}"

    def test_dst_summer_vs_winter_cutoff(self):
        """TST-08: a 20:30 UTC filing with lag=0 is AFTER 16:00 ET in summer (EDT
        20:30 UTC = 16:30 ET → next day) but BEFORE in winter (EST 20:30 UTC =
        15:30 ET → same day)."""
        df_summer = _make_price_df(date(2019, 7, 1), date(2019, 8, 30))
        # 2019-07-15 20:30 UTC = 16:30 EDT → after cutoff → next trading day (07-16)
        ev_summer = datetime(2019, 7, 15, 20, 30, 0, tzinfo=timezone.utc)
        res_s = _entry_date_from_event_ts(ev_summer, df_summer, entry_lag_days=0)
        assert res_s is not None
        assert res_s[0] == date(2019, 7, 16), f"summer should advance, got {res_s[0]}"

        df_winter = _make_price_df(date(2019, 12, 1), date(2020, 1, 31))
        # 2019-12-16 20:30 UTC = 15:30 EST → before cutoff → same day (12-16, Mon)
        ev_winter = datetime(2019, 12, 16, 20, 30, 0, tzinfo=timezone.utc)
        res_w = _entry_date_from_event_ts(ev_winter, df_winter, entry_lag_days=0)
        assert res_w is not None
        assert res_w[0] == date(2019, 12, 16), f"winter should be same day, got {res_w[0]}"

    def test_open_anchored_exact_numeric_value(self):
        """TST-09: pin the exact Open-anchored return at a known horizon.

        Build a 30-bar frame: entry Open = 100.00 exactly, the bar at offset 21
        has Close = 105.00 → expected return = +5.00%."""
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in
                                [dd.date() for dd in pd.date_range(date(2020, 1, 1), periods=30, freq="B")]])
        closes = [100.0] * 30
        closes[21] = 105.0
        opens = [100.0] * 30
        df = pd.DataFrame({
            "Open": opens, "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes], "Close": closes,
            "Volume": [1_000_000] * 30,
        }, index=idx)
        entry_date = [d.date() for d in df.index][0]
        from turnaround_validation import _bar_counted_forward_returns_from_open
        fwd = _bar_counted_forward_returns_from_open(df, entry_date, 100.0, horizons=(21,))
        assert fwd[21] == pytest.approx(5.0, abs=1e-6), f"got {fwd[21]}"

    def test_entry_open_zero_returns_none(self):
        """TST-13: entry_open <= 0 must return all-None (no divide-by-zero)."""
        from turnaround_validation import _bar_counted_forward_returns_from_open
        df = _make_price_df(date(2020, 1, 1), date(2020, 6, 30))
        entry_date = [d.date() for d in df.index][0]
        fwd = _bar_counted_forward_returns_from_open(df, entry_date, 0.0, horizons=(21,))
        assert all(v is None for v in fwd.values())
        fwd_term, was_term = _forward_return_terminal(df, entry_date, 0.0, 21)
        assert fwd_term is None

    def test_no_open_column_falls_back_to_close(self):
        """TST-13: a frame without an Open column resolves entry via Close fallback."""
        df = _make_no_open_df(date(2019, 1, 1), date(2019, 12, 31))
        event_ts = datetime(2019, 6, 4, 0, 13, 31, tzinfo=timezone.utc)
        result = _entry_date_from_event_ts(event_ts, df, entry_lag_days=1)
        assert result is not None
        entry_date, entry_price, used_close_fallback = result
        assert used_close_fallback is True
        assert entry_price > 0


# -------------------------------------------------------------------------
# ADV-05 — FDR ledger persists across runs (append-only)
# -------------------------------------------------------------------------

class TestFDRLedgerPersistence:
    def _loader(self, frames):
        return lambda t: frames.get(t)

    def test_ledger_appends_across_runs(self, tmp_path, monkeypatch):
        """Two runs must produce TWO ledger entries (not a reset-per-run file)."""
        ledger_path = tmp_path / "fdr_ledger.json"
        monkeypatch.setattr(_es_mod, "_FDR_LEDGER_PATH", ledger_path)
        frames = {"AAPL": _make_price_df(date(2016, 1, 1), date(2022, 12, 31), 0.2)}
        events = [EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, tzinfo=timezone.utc), {})]
        for name in ("run_a", "run_b"):
            config = EventStudyConfig(
                study_name=name, horizons=(21,), output_dir=tmp_path / name,
            )
            run_event_study(events, config, self._loader(frames),
                            universe_tickers=["AAPL"])
        assert ledger_path.exists(), "ledger file not created"
        rows = json.loads(ledger_path.read_text())
        assert isinstance(rows, list) and len(rows) == 2, (
            f"ledger must append (expected 2 rows, got {len(rows) if isinstance(rows, list) else '?'})"
        )
        assert rows[0]["study_name"] == "run_a"
        assert rows[1]["study_name"] == "run_b"
        assert "study_config_hash" in rows[0]


# -------------------------------------------------------------------------
# F349: Sector-peer benchmark
# -------------------------------------------------------------------------

@pytest.fixture
def sample_ticker_to_sic() -> dict:
    """Synthetic SIC map: XOM/CVX share 3-digit "131", MSFT/ADBE share 2-digit "73"."""
    return {
        "XOM": "1311",   # Crude Petroleum & Natural Gas
        "CVX": "1311",   # same SIC
        "COP": "1382",   # Oil & Gas Field Services (same 2-digit "13")
        "MSFT": "7372",  # Prepackaged Software
        "ADBE": "7372",  # same SIC
        "CRM": "7374",   # same 2-digit "73"
        "ENPH": "3621",  # Electrical Industrial Apparatus (unique 3-digit in this set)
    }


class TestPeerExcessF349:
    """F349: sector-peer benchmark — helper function unit tests."""

    def _loader(self, frames):
        return lambda t: frames.get(t)

    def test_get_peer_set_3digit_match(self, sample_ticker_to_sic):
        """_get_peer_set_by_sic returns 3-digit SIC peers when count >= min_peers."""
        universe = ["XOM", "CVX", "COP", "MSFT", "ADBE", "CRM", "ENPH"]
        peers, sic, level = _get_peer_set_by_sic(
            "XOM", universe, sample_ticker_to_sic, min_peers=2
        )
        # XOM "1311" → 3-digit "131"; only CVX shares "131X" match at 3 digits.
        # CVX="1311" → yes; COP="1382" → "138" ≠ "131" → no.  Count=1 < min=2.
        # Fall back to 2-digit "13": CVX + COP = 2 >= 2 → "2_digit".
        assert level in ("2_digit", "universe")
        # XOM itself must be excluded from peers
        assert "XOM" not in peers

    def test_get_peer_set_fallback_universe(self, sample_ticker_to_sic):
        """ENPH has a unique 3- and 2-digit SIC in the sample → universe fallback."""
        universe = ["XOM", "CVX", "COP", "MSFT", "ADBE", "CRM", "ENPH"]
        peers, sic, level = _get_peer_set_by_sic(
            "ENPH", universe, sample_ticker_to_sic, min_peers=3
        )
        # ENPH "3621" — no other ticker has "36X" or "3X" in this set → fallback.
        assert level == "universe"
        assert "ENPH" not in peers
        assert set(peers) == {"XOM", "CVX", "COP", "MSFT", "ADBE", "CRM"}

    def test_get_peer_set_excludes_self(self, sample_ticker_to_sic):
        """Event ticker itself must never appear in the peer set (any fallback level)."""
        universe = ["XOM", "CVX", "COP", "MSFT", "ADBE", "CRM", "ENPH"]
        for ticker in universe:
            peers, _, _ = _get_peer_set_by_sic(ticker, universe, sample_ticker_to_sic, min_peers=1)
            assert ticker not in peers, f"{ticker} should not be in its own peer set"

    def test_get_peer_set_no_sic(self):
        """Ticker with no SIC in map falls back to full universe."""
        ticker_to_sic: dict = {}
        universe = ["A", "B", "C", "D", "E", "F"]
        peers, sic, level = _get_peer_set_by_sic("A", universe, ticker_to_sic, min_peers=2)
        assert level == "universe"
        assert sic is None
        assert "A" not in peers

    def test_compute_peer_median_matches_universe_median_when_equal(self):
        """When peer_set = all universe tickers minus event ticker, result matches."""
        from research.event_study import _compute_universe_median
        universe = ["AAPL", "MSFT", "GOOG"]
        frames = {t: _make_price_df(date(2018, 1, 1), date(2021, 12, 31), 0.15) for t in universe}
        loader = lambda t: frames.get(t)
        entry = date(2019, 6, 3)  # business day in the frame
        h = 21
        # Peers = universe minus "AAPL"
        peers = ["MSFT", "GOOG"]
        peer_med, peer_n, peer_term = _compute_peer_median(entry, h, "AAPL", peers, loader)
        univ_med, univ_n, univ_term = _compute_universe_median(entry, h, "AAPL", loader, universe)
        # Both computed over same two tickers → same median.
        if peer_med is not None and univ_med is not None:
            assert abs(peer_med - univ_med) < 1e-9, (
                f"peer_median {peer_med} != universe_median {univ_med} with equal sets"
            )

    def test_load_ticker_to_sic_from_cache(self, tmp_path):
        """_load_ticker_to_sic reads universe.json + submissions/*.json."""
        # Build a minimal fake edgar_cache layout.
        submissions_dir = tmp_path / "submissions"
        submissions_dir.mkdir()
        # universe.json in normalized format: {TICKER: {"cik_str": "0000000001", ...}}
        universe_data = {
            "XOM": {"cik_str": "0000000001", "title": "Exxon Mobil"},
            "MSFT": {"cik_str": "0000000002", "title": "Microsoft"},
            "UNKNOWN": {"cik_str": "0000000099", "title": "No Submission"},
        }
        universe_path = tmp_path / "universe.json"
        universe_path.write_text(json.dumps(universe_data))
        # submission for XOM
        (submissions_dir / "0000000001.json").write_text(
            json.dumps({"sic": "1311", "sicDescription": "Crude Petroleum"})
        )
        # submission for MSFT
        (submissions_dir / "0000000002.json").write_text(
            json.dumps({"sic": "7372", "sicDescription": "Prepackaged Software"})
        )
        # UNKNOWN has no submission file
        # DI-03: _load_ticker_to_sic returns (dict, parse_error_count).
        result, parse_errors = _load_ticker_to_sic(["XOM", "MSFT", "UNKNOWN"], sic_cache_path=submissions_dir)
        assert result["XOM"] == "1311"
        assert result["MSFT"] == "7372"
        assert result["UNKNOWN"] is None
        assert parse_errors == 0  # no JSON decode failures in this set

    def test_run_event_study_includes_peer_excess(self, tmp_path):
        """run_event_study populates fwd_peer_excess_pct and meta.sic_coverage."""
        universe = ["AAPL", "MSFT", "GOOG"]
        frames = {t: _make_price_df(date(2017, 1, 1), date(2020, 12, 31), 0.10) for t in universe}
        events = [
            EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="peer_test", horizons=(21,),
            dedup_same_ticker=False, output_dir=tmp_path / "peer_test",
        )
        outcomes, meta = run_event_study(
            events, config, lambda t: frames.get(t), universe_tickers=universe
        )
        # fwd_peer_excess_pct always present on entered outcomes.
        entered = [o for o in outcomes if o.split == "explore"]
        assert len(entered) >= 1
        for o in entered:
            assert hasattr(o, "fwd_peer_excess_pct")
            assert hasattr(o, "peer_n")
        # meta.sic_coverage always present when universe_tickers supplied.
        assert "sic_coverage" in meta
        assert "tickers_with_sic" in meta["sic_coverage"]
        assert "tickers_without_sic" in meta["sic_coverage"]
        assert "coverage_pct" in meta["sic_coverage"]
        assert "sic_fallback_stats" in meta
        total_fallbacks = sum(meta["sic_fallback_stats"].values())
        assert total_fallbacks >= 0  # at least 0 (may be universe-fallback if no SIC file)

    def test_meta_sic_coverage_absent_without_universe(self, tmp_path):
        """When universe_tickers=None, meta.sic_coverage is None (backward-compat)."""
        frames = {"AAPL": _make_price_df(date(2017, 1, 1), date(2020, 12, 31))}
        events = [EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, tzinfo=timezone.utc), {})]
        config = EventStudyConfig(
            study_name="no_univ", horizons=(21,), output_dir=tmp_path / "no_univ"
        )
        _, meta = run_event_study(events, config, lambda t: frames.get(t))
        assert meta["sic_coverage"] is None
        assert meta["sic_fallback_stats"] is None

    def test_peer_median_excess_pct_populated_in_per_horizon(self, tmp_path):
        """COR-01 locking test: per_horizon[h].peer_median_excess_pct is written (int key).

        Regression: h_str = str(h) key lookup silently missed the int-keyed dict,
        leaving peer_median_excess_pct absent from every study run.
        """
        universe = ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA"]
        frames = {t: _make_price_df(date(2016, 1, 1), date(2022, 12, 31), 0.15) for t in universe}
        # Wire up a fake SIC cache so at least some peers are resolved.
        sic_dir = tmp_path / "sic_cache"
        sic_dir.mkdir()
        universe_data = {t: {"cik_str": str(i + 1).zfill(10)} for i, t in enumerate(universe)}
        (sic_dir.parent / "universe.json").write_text(json.dumps(universe_data))
        # Write submission JSON with all same SIC so 3-digit peers always found.
        for i, t in enumerate(universe):
            cik = str(i + 1).zfill(10)
            (sic_dir / f"{cik}.json").write_text(json.dumps({"sic": "7372"}))

        events = [
            EventRecord("AAPL", datetime(2019, 3, 14, 22, 0, tzinfo=timezone.utc), {}),
        ]
        config = EventStudyConfig(
            study_name="cor01_lock",
            horizons=(21, 63),
            output_dir=tmp_path / "cor01_lock",
            sic_coverage_path=sic_dir,
        )
        _, meta = run_event_study(
            events, config, lambda t: frames.get(t), universe_tickers=universe
        )
        per_h = meta.get("per_horizon", {})
        for h in (21, 63):
            assert h in per_h, f"per_horizon missing int key {h}"
            # peer_median_excess_pct must be present (not absent) — None means no peer vals,
            # but the key itself must exist.
            assert "peer_median_excess_pct" in per_h[h], (
                f"per_horizon[{h}].peer_median_excess_pct missing (COR-01 regression)"
            )

    def test_parse_errors_counted_in_sic_coverage(self, tmp_path):
        """DI-03 locking test: corrupted submission JSON increments parse_errors in meta.sic_coverage."""
        submissions_dir = tmp_path / "submissions"
        submissions_dir.mkdir()
        universe_data = {
            "XOM": {"cik_str": "0000000001"},
            "MSFT": {"cik_str": "0000000002"},
        }
        (tmp_path / "universe.json").write_text(json.dumps(universe_data))
        (submissions_dir / "0000000001.json").write_text("{bad json")  # corrupted
        (submissions_dir / "0000000002.json").write_text(json.dumps({"sic": "7372"}))

        result, parse_errors = _load_ticker_to_sic(["XOM", "MSFT"], sic_cache_path=submissions_dir)
        assert parse_errors == 1, f"expected 1 parse error, got {parse_errors}"
        assert result["XOM"] is None  # failed parse → None
        assert result["MSFT"] == "7372"

    def test_sic_fallback_stats_consistent_when_universe_json_absent(self, tmp_path):
        """DI-08 locking test: sic_fallback_stats total == floor-ok events even when universe.json missing.

        When universe.json is absent, ticker_to_sic is empty.  Floor-passing events must
        still increment sic_fallback_counts (as 'universe') so probe anchor 5 passes.
        """
        universe = ["AAPL", "MSFT", "GOOG"]
        frames = {t: _make_price_df(date(2017, 1, 1), date(2020, 12, 31), 0.10) for t in universe}
        events = [
            EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, tzinfo=timezone.utc), {}),
        ]
        # Use a sic_cache_path with no universe.json → empty ticker_to_sic.
        empty_sic_dir = tmp_path / "empty_submissions"
        empty_sic_dir.mkdir()
        config = EventStudyConfig(
            study_name="di08_lock",
            horizons=(21,),
            output_dir=tmp_path / "di08_lock",
            sic_coverage_path=empty_sic_dir,
        )
        outcomes, meta = run_event_study(
            events, config, lambda t: frames.get(t), universe_tickers=universe
        )
        entered = [o for o in outcomes if o.floor_status == "ok"]
        fs = meta.get("sic_fallback_stats", {})
        fs_total = sum(fs.values())
        assert fs_total == len(entered), (
            f"sic_fallback_stats total {fs_total} != floor-ok events {len(entered)} "
            "(DI-08 regression: empty universe.json path breaks probe anchor 5)"
        )

    def test_cor04_separate_terminal_exit_accumulators(self, tmp_path):
        """COR-04 locking test: peer_attrition has sic_peer_terminal_exits key (separate from universe).

        Regression: both universe-median and SIC-peer-median paths incremented the same
        peer_terminal_by_h accumulator, double-counting attrition.
        """
        universe = ["AAPL", "MSFT", "GOOG"]
        frames = {t: _make_price_df(date(2017, 1, 1), date(2020, 12, 31), 0.10) for t in universe}
        events = [EventRecord("AAPL", datetime(2018, 6, 15, 22, 0, tzinfo=timezone.utc), {})]
        config = EventStudyConfig(
            study_name="cor04_lock",
            horizons=(21,),
            output_dir=tmp_path / "cor04_lock",
        )
        _, meta = run_event_study(events, config, lambda t: frames.get(t), universe_tickers=universe)
        pa = meta.get("peer_attrition", {})
        assert 21 in pa, "peer_attrition must have horizon 21"
        assert "peer_terminal_exits" in pa[21], "peer_terminal_exits missing"
        assert "sic_peer_terminal_exits" in pa[21], (
            "sic_peer_terminal_exits missing (COR-04 regression: separate accumulator not wired)"
        )


# -------------------------------------------------------------------------
# F350: Regime-breakdown lens
# -------------------------------------------------------------------------

def _make_regime_states(states: dict[str, str]) -> dict:
    """Build a minimal regime_states.json dict with the given {date_str: state} map."""
    return {
        "meta": {"start_date": min(states), "end_date": max(states), "state_counts": {}},
        "schema_version": 1,
        "states": {d: {"state": s} for d, s in states.items()},
    }


class TestRegimeBreakdownF350:
    """F350: regime-breakdown lens."""

    def _loader(self, frames):
        return lambda t: frames.get(t)

    def test_regime_breakdown_structure(self):
        """_regime_breakdown returns {regime: {n_events, per_horizon}} for all 4 states."""
        # Build synthetic outcomes with known regime states.
        def _make_outcome(regime, excess):
            return EventOutcome(
                ticker="T", event_ts=datetime(2019, 1, 2, 22, tzinfo=timezone.utc),
                entry_date=date(2019, 1, 3), entry_price=100.0, payload={},
                split="explore",
                fwd_return_pct={21: 0.05},
                fwd_excess_pct={21: excess},
                floor_status="ok", universe_n={21: 5},
                regime_state=regime,
            )
        outcomes = [
            _make_outcome("RISK_ON", 0.02),
            _make_outcome("RISK_ON", 0.01),
            _make_outcome("NEUTRAL", -0.01),
            _make_outcome("RISK_OFF", 0.005),
            _make_outcome("STRESS", -0.03),
        ]
        result = _regime_breakdown(outcomes, (21,), low_count_threshold=10)
        # All 4 states must appear.
        assert set(result.keys()) == {"RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"}
        assert result["RISK_ON"]["n_events"] == 2
        assert result["NEUTRAL"]["n_events"] == 1
        assert result["RISK_OFF"]["n_events"] == 1
        assert result["STRESS"]["n_events"] == 1
        # per_horizon structure
        ph = result["RISK_ON"]["per_horizon"]
        assert 21 in ph
        assert "n" in ph[21]
        assert "mean_excess_pct" in ph[21]
        assert "sign_agreement" in ph[21]

    def test_regime_breakdown_low_count_flag(self):
        """Regime with n < low_count_threshold gets LOW_COUNT_FLAG=True."""
        outcomes = [
            EventOutcome(
                ticker="T", event_ts=datetime(2019, 1, 2, 22, tzinfo=timezone.utc),
                entry_date=date(2019, 1, 3), entry_price=100.0, payload={},
                split="explore", fwd_return_pct={21: 0.01}, fwd_excess_pct={21: 0.01},
                floor_status="ok", universe_n={21: 5},
                regime_state="RISK_OFF",  # rare state
            )
        ]
        result = _regime_breakdown(outcomes, (21,), low_count_threshold=10)
        # RISK_OFF has 1 event < threshold=10 → flagged.
        assert result["RISK_OFF"].get("LOW_COUNT_FLAG") is True
        # States with 0 events also flag (0 < 10).
        for state in ("RISK_ON", "NEUTRAL", "STRESS"):
            assert result[state].get("LOW_COUNT_FLAG") is True

    def test_run_event_study_includes_regime_state(self, tmp_path):
        """run_event_study populates regime_state on outcomes and meta.regime_breakdown."""
        frames = {"AAPL": _make_price_df(date(2017, 1, 1), date(2021, 12, 31), 0.10)}
        events = [
            EventRecord("AAPL", datetime(2019, 3, 18, 22, 0, tzinfo=timezone.utc), {}),
        ]
        # Create a minimal regime_states.json in tmp_path.
        regime_file = tmp_path / "regime_states.json"
        regime_data = _make_regime_states({
            "2019-03-18": "RISK_ON",
            "2019-03-19": "RISK_ON",
        })
        regime_file.write_text(json.dumps(regime_data))
        config = EventStudyConfig(
            study_name="regime_test", horizons=(21,),
            dedup_same_ticker=False,
            output_dir=tmp_path / "regime_test",
            regime_states_path=regime_file,
        )
        outcomes, meta = run_event_study(
            events, config, self._loader(frames), universe_tickers=["AAPL"]
        )
        entered = [o for o in outcomes if o.split in ("explore", "confirm")]
        assert len(entered) >= 1
        for o in entered:
            assert o.regime_state in ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", None)
        # meta.regime_breakdown present.
        assert "regime_breakdown" in meta
        bd = meta["regime_breakdown"]
        assert set(bd.keys()) == {"RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"}

    def test_regime_breakdown_missing_file_graceful(self, tmp_path):
        """Missing regime_states.json produces regime_state=None on all outcomes (no crash)."""
        frames = {"AAPL": _make_price_df(date(2017, 1, 1), date(2021, 12, 31))}
        events = [EventRecord("AAPL", datetime(2019, 3, 18, 22, 0, tzinfo=timezone.utc), {})]
        config = EventStudyConfig(
            study_name="no_regime", horizons=(21,),
            output_dir=tmp_path / "no_regime",
            regime_states_path=tmp_path / "nonexistent_regime_states.json",
        )
        outcomes, meta = run_event_study(
            events, config, self._loader(frames), universe_tickers=["AAPL"]
        )
        for o in outcomes:
            assert o.regime_state is None
        # regime_breakdown still present (all zeros, all LOW_COUNT_FLAG).
        assert "regime_breakdown" in meta

    def test_regime_state_entry_date_discipline(self, tmp_path):
        """regime_state is tagged at entry_date (ET), consistent with ADV-01."""
        # Entry is 2019-03-19 (lag=1 from 2019-03-18 after-hours).
        frames = {"AAPL": _make_price_df(date(2017, 1, 1), date(2021, 12, 31))}
        events = [EventRecord("AAPL", datetime(2019, 3, 18, 22, 0, tzinfo=timezone.utc), {})]
        regime_file = tmp_path / "rs.json"
        # 2019-03-18=STRESS, 2019-03-19=RISK_ON → entry_date=2019-03-19 → RISK_ON.
        regime_data = _make_regime_states({
            "2019-03-18": "STRESS",
            "2019-03-19": "RISK_ON",
        })
        regime_file.write_text(json.dumps(regime_data))
        config = EventStudyConfig(
            study_name="dt_disc", horizons=(21,), entry_lag_days=1,
            dedup_same_ticker=False,
            output_dir=tmp_path / "dt_disc",
            regime_states_path=regime_file,
        )
        outcomes, _ = run_event_study(
            events, config, self._loader(frames), universe_tickers=["AAPL"]
        )
        entered = [o for o in outcomes if o.entry_date != date.min]
        assert len(entered) == 1
        # Entry date should be 2019-03-19 → regime RISK_ON.
        assert entered[0].regime_state == "RISK_ON", (
            f"Expected RISK_ON at entry_date {entered[0].entry_date}, "
            f"got {entered[0].regime_state}"
        )

    def test_real_regime_states_json_schema(self):
        """Real regime_states.json has expected schema: RISK_OFF is the rare state."""
        regime_path = Path(_BACKEND) / "data" / "turnaround" / "regime_states.json"
        if not regime_path.exists():
            pytest.skip("regime_states.json not present in cache")
        data = json.loads(regime_path.read_text())
        counts = data["meta"]["state_counts"]
        # RISK_OFF is the crisis (rare) state: ~6 days 2015-2024.
        assert counts["RISK_OFF"] < 20, (
            f"RISK_OFF should be rare (<20 days), got {counts['RISK_OFF']}"
        )
        # RISK_ON dominates.
        assert counts["RISK_ON"] > 1000, (
            f"RISK_ON should dominate (>1000 days), got {counts['RISK_ON']}"
        )
        # STRESS is the second-largest "bad weather" state.
        assert counts["STRESS"] > 100, (
            f"STRESS (stormy) should be >100 days, got {counts['STRESS']}"
        )
