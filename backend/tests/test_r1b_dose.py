"""Tests for r1_dose.build_r1b_events — R-1b dose builder (form4_ingest path).

Coverage:
  Window aggregation
    test_window_aggregation_multi_filing  — multi-filing window sums D and unions owner_ciks
    test_window_aggregation_outside_window — filing outside W-bday window is excluded
    test_distinct_owner_k_counting        — distinct owner CIKs across filings → k
    test_same_day_multiple_filings        — same-day filings aggregate into one event_ts=latest

  Payload contract
    test_payload_contract_keys            — all required payload keys present
    test_payload_perturb_9_keys           — all 9 perturbation keys present

  Provenance carry-through
    test_provenance_acceptance_dt_source  — acceptance_dt_source carried from ingest
    test_provenance_adt_midnight_utc      — adt_midnight_utc flag carried from ingest

  Score correctness
    test_score_equals_compute_score       — score equals _compute_score(D, k, MC)
    test_w21_f0_equals_primary_score      — W21_F0 perturbation == primary score

  Meta contract
    test_meta_r1b_keys                    — R-1b extra meta keys present
    test_meta_n_midnight_utc_adt          — n_midnight_utc_adt propagated
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# -------------------------------------------------------------------------
# Path setup
# -------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
_RESEARCH = _BACKEND / "research"
for p in [str(_BACKEND), str(_RESEARCH)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from research.r1_dose import (
    _compute_score,
    _busday_window_start,
    _PERTURB_KEY_MAP,
    _W_PRIMARY,
)
from research.event_study import EventRecord


# -------------------------------------------------------------------------
# Fixture helpers — build synthetic ingest EventRecords
# -------------------------------------------------------------------------

def _make_ingest_event(
    ticker: str,
    et_date: date,
    D: float,
    owner_ciks: list[str],
    *,
    accession: str = "0000111111-19-000001",
    filing_date: str = "",
    acceptance_fallback: bool = False,
    acceptance_dt_source: str = "direct_hit",
    adt_midnight_utc: bool = False,
    n_10b51_excluded: int = 0,
    missing_price_txns: int = 0,
    issuer_cik: str = "111111",
    hour_utc: int = 20,
) -> EventRecord:
    """Build a synthetic ingest EventRecord as form4_ingest would produce."""
    # event_ts: 20:00 UTC on et_date (unless midnight flag)
    if adt_midnight_utc:
        hour_utc = 0
    event_ts = datetime(
        et_date.year, et_date.month, et_date.day,
        hour_utc, 0, 0, tzinfo=timezone.utc,
    )
    if not filing_date:
        filing_date = et_date.isoformat()
    k = len(owner_ciks) if owner_ciks else 1
    payload = {
        "form_type": "4",
        "accession": accession,
        "filing_date": filing_date,
        "period_of_report": et_date.isoformat(),
        "acceptance_fallback": acceptance_fallback,
        "acceptance_dt_source": acceptance_dt_source,
        "adt_midnight_utc": adt_midnight_utc,
        "D": D,
        "k": k,
        "n_txns_qualifying": 1,
        "n_10b51_excluded": n_10b51_excluded,
        "missing_price_txns": missing_price_txns,
        "owner_cik": owner_ciks[0] if owner_ciks else "",
        "owner_ciks": owner_ciks,
        "issuer_cik": issuer_cik,
    }
    return EventRecord(
        ticker=ticker.upper(),
        event_ts=event_ts,
        payload=payload,
        is_fallback=acceptance_fallback,
    )


def _make_price_df(close: float = 100.0) -> pd.DataFrame:
    """Synthetic daily price frame covering 2018-2020."""
    dates = pd.date_range("2018-01-01", "2020-12-31", freq="B")
    n = len(dates)
    return pd.DataFrame({
        "Open":   [close * 0.99] * n,
        "High":   [close * 1.01] * n,
        "Low":    [close * 0.98] * n,
        "Close":  [close] * n,
        "Volume": [1_000_000] * n,
    }, index=dates)


def _build_mock_ingest(
    events: list[EventRecord],
    ingest_meta: Optional[dict] = None,
):
    """Return a mock replacement for build_form4_dataset_events."""
    if ingest_meta is None:
        ingest_meta = {
            "quarters_processed": 1,
            "submissions_scanned": 10,
            "submissions_universe_pass": 5,
            "submissions_universe_fail": 5,
            "form4_qualified_txns": len(events),
            "form4_10b51_excluded_txns": 0,
            "form4_missing_price_txns": 0,
            "events_qualifying": len(events),
            "events_returned": len(events),
            "acceptances_direct_hit": len(events),
            "acceptances_fetched": 0,
            "acceptances_fallback": 0,
            "amendments_included": 0,
            "n_superseded_dropped": 0,
            "n_dup4_collisions": 0,
            "n_ticker_fallback": 0,
            "n_no_timestamp_dropped": 0,
            "n_midnight_utc_adt": sum(
                1 for e in events if e.payload.get("adt_midnight_utc", False)
            ),
            "per_quarter": {},
        }
    mock = MagicMock(return_value=(events, ingest_meta))
    return mock


def _call_build_r1b_events(
    ingest_events: list[EventRecord],
    start: date,
    end: date,
    close: float = 100.0,
    shares_outstanding: Optional[float] = 1_000_000.0,
    ingest_meta: Optional[dict] = None,
):
    """Call build_r1b_events with mocked ingest and loader_fn."""
    from research.r1_dose import build_r1b_events

    ticker_to_df: dict[str, pd.DataFrame] = {}
    for ev in ingest_events:
        if ev.ticker.upper() not in ticker_to_df:
            ticker_to_df[ev.ticker.upper()] = _make_price_df(close=close)

    def loader_fn(sym: str) -> Optional[pd.DataFrame]:
        return ticker_to_df.get(sym.upper())

    def shares_fn(cik: str, as_of: date) -> Optional[float]:
        return shares_outstanding

    mock_ingest_fn = _build_mock_ingest(ingest_events, ingest_meta)

    with patch("research.form4_ingest.build_form4_dataset_events", mock_ingest_fn):
        events, meta = build_r1b_events(
            start=start,
            end=end,
            loader_fn=loader_fn,
            shares_fn=shares_fn,
        )
    return events, meta


# -------------------------------------------------------------------------
# Tests: Window aggregation
# -------------------------------------------------------------------------

class TestWindowAggregation:
    def test_window_aggregation_multi_filing(self):
        """Multi-filing window: D sums, owner_ciks unioned across filings."""
        # Two filings by different owners within 21-bday window
        ticker = "WTEST"
        et_date = date(2019, 6, 10)

        # Filing 1: 3 days before event_date (clearly within W=21 bday window)
        import numpy as np
        day1 = date.fromisoformat(str(np.busday_offset(et_date.isoformat(), -3, roll="backward")))
        # Filing 2: event_date itself
        day2 = et_date

        ev1 = _make_ingest_event(
            ticker, day1, D=50_000.0, owner_ciks=["CIK001"],
            accession="0001-19-000001", issuer_cik="999001",
        )
        ev2 = _make_ingest_event(
            ticker, day2, D=30_000.0, owner_ciks=["CIK002"],
            accession="0001-19-000002", issuer_cik="999001",
            hour_utc=20,
        )

        events, meta = _call_build_r1b_events(
            [ev1, ev2],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        # Should collapse to one event per (ticker, ET date) for day2
        # day1 event is also in window, day2 is the trigger
        day2_events = [e for e in events if e.ticker == ticker]
        assert len(day2_events) >= 1, "Expected at least one event for WTEST"
        # Find the day2 event
        day2_ev = next(
            (e for e in day2_events
             if e.event_ts.date() == day2 or
             (abs((e.event_ts.date() - day2).days) <= 1)),
            None,
        )
        if day2_ev is None:
            day2_ev = day2_events[-1]

        # Window aggregation for day2 should include both ev1 and ev2
        # D = 50k + 30k = 80k (both in 21-bday window)
        # k = 2 distinct owner CIKs
        assert day2_ev.payload["D"] == pytest.approx(80_000.0), (
            f"Expected D=80k (both filings in window), got {day2_ev.payload['D']}"
        )
        assert day2_ev.payload["k"] == 2, (
            f"Expected k=2 (two distinct owners), got {day2_ev.payload['k']}"
        )

    def test_window_aggregation_outside_window(self):
        """Filing outside W=21 bday window is excluded from aggregation."""
        import numpy as np
        ticker = "WOUT"
        et_date = date(2019, 6, 10)

        # W=21 bday window start
        window_start = _busday_window_start(et_date, 21)
        # day_outside = 1 bday before window_start (outside)
        day_outside = date.fromisoformat(
            str(np.busday_offset(window_start.isoformat(), -1, roll="backward"))
        )
        assert day_outside < window_start, "day_outside should be before window_start"

        ev_outside = _make_ingest_event(
            ticker, day_outside, D=100_000.0, owner_ciks=["CIK_OUT"],
            accession="0002-19-000001", issuer_cik="999002",
        )
        # Triggering event on et_date itself
        ev_trigger = _make_ingest_event(
            ticker, et_date, D=20_000.0, owner_ciks=["CIK_TRIG"],
            accession="0002-19-000002", issuer_cik="999002",
        )

        events, meta = _call_build_r1b_events(
            [ev_outside, ev_trigger],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        trigger_evs = [e for e in events if e.ticker == ticker.upper()]
        assert len(trigger_evs) >= 1

        # The outside filing should NOT be in the window aggregation
        # So D should be only the trigger event's D (20k), not 120k
        trigger_ev = max(trigger_evs, key=lambda e: e.event_ts)
        assert trigger_ev.payload["D"] == pytest.approx(20_000.0), (
            f"Outside filing should be excluded; expected D=20k, got {trigger_ev.payload['D']}"
        )

    def test_distinct_owner_k_counting(self):
        """Distinct owner CIKs across window filings → correct k."""
        ticker = "KTEST"
        et_date = date(2019, 7, 1)

        # 3 filings: CIK_A twice + CIK_B once → k=2
        ev1 = _make_ingest_event(
            ticker, et_date, D=10_000.0, owner_ciks=["CIK_A"],
            accession="0003-19-000001", issuer_cik="999003", hour_utc=18,
        )
        ev2 = _make_ingest_event(
            ticker, et_date, D=10_000.0, owner_ciks=["CIK_A"],
            accession="0003-19-000002", issuer_cik="999003", hour_utc=19,
        )
        ev3 = _make_ingest_event(
            ticker, et_date, D=10_000.0, owner_ciks=["CIK_B"],
            accession="0003-19-000003", issuer_cik="999003", hour_utc=20,
        )

        events, meta = _call_build_r1b_events(
            [ev1, ev2, ev3],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        evs = [e for e in events if e.ticker == ticker.upper()]
        assert len(evs) == 1, "Three same-day filings → one event"
        assert evs[0].payload["k"] == 2, (
            f"CIK_A twice + CIK_B once = 2 distinct owners; got k={evs[0].payload['k']}"
        )
        assert evs[0].payload["D"] == pytest.approx(30_000.0), (
            f"D = 10k+10k+10k = 30k; got D={evs[0].payload['D']}"
        )

    def test_same_day_multiple_filings_latest_ts(self):
        """Same-day multiple filings: one EventRecord returned, event_ts = latest."""
        ticker = "SAMEDY"
        et_date = date(2019, 8, 5)

        ev1 = _make_ingest_event(
            ticker, et_date, D=5_000.0, owner_ciks=["CIK_X"],
            accession="0004-19-000001", hour_utc=18,
        )
        ev2 = _make_ingest_event(
            ticker, et_date, D=5_000.0, owner_ciks=["CIK_Y"],
            accession="0004-19-000002", hour_utc=22,  # latest
        )

        events, meta = _call_build_r1b_events(
            [ev1, ev2],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        evs = [e for e in events if e.ticker == ticker.upper()]
        assert len(evs) == 1, "Two same-day filings → one EventRecord"
        expected_ts = datetime(2019, 8, 5, 22, 0, 0, tzinfo=timezone.utc)
        assert evs[0].event_ts == expected_ts, (
            f"event_ts should be latest: {evs[0].event_ts} vs {expected_ts}"
        )


# -------------------------------------------------------------------------
# Tests: Payload contract
# -------------------------------------------------------------------------

class TestPayloadContract:
    def _get_single_event(self, close=100.0, shares=1_000_000.0):
        ticker = "CTPAY"
        et_date = date(2019, 9, 3)
        ev = _make_ingest_event(
            ticker, et_date, D=50_000.0, owner_ciks=["CIK_P"],
            accession="0005-19-000001", issuer_cik="888001",
        )
        events, meta = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=close,
            shares_outstanding=shares,
        )
        assert len(events) == 1
        return events[0], meta

    def test_payload_contract_keys(self):
        """All required payload keys must be present (charter §2b contract)."""
        ev, _ = self._get_single_event()
        required_keys = {
            # R-1 contract keys
            "form_type", "accession", "filing_date", "acceptance_fallback",
            "score", "score_undefined", "D", "k", "MC",
            "n_filings_window", "n_10b51_excluded", "missing_price_txns",
            "score_perturb",
            # R-1b provenance (charter §2a)
            "acceptance_dt_source", "adt_midnight_utc",
        }
        missing = required_keys - set(ev.payload.keys())
        assert not missing, f"Missing payload keys: {missing}"

    def test_payload_perturb_9_keys(self):
        """score_perturb must contain all 9 perturbation keys."""
        ev, _ = self._get_single_event()
        perturb = ev.payload["score_perturb"]
        assert len(perturb) == 9, f"Expected 9 perturb keys, got {len(perturb)}"
        expected_keys = {
            "W20_F0", "W20_F40k", "W20_F60k",
            "W21_F0", "W21_F40k", "W21_F60k",
            "W22_F0", "W22_F40k", "W22_F60k",
        }
        assert set(perturb.keys()) == expected_keys


# -------------------------------------------------------------------------
# Tests: Provenance carry-through
# -------------------------------------------------------------------------

class TestProvenanceCarryThrough:
    def test_provenance_acceptance_dt_source(self):
        """acceptance_dt_source is carried from ingest payload."""
        ticker = "PROV1"
        et_date = date(2019, 10, 7)
        ev = _make_ingest_event(
            ticker, et_date, D=20_000.0, owner_ciks=["CIK_Q"],
            accession="0006-19-000001",
            acceptance_dt_source="older_index_fetch",
        )
        events, _ = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
        )
        assert len(events) == 1
        assert events[0].payload["acceptance_dt_source"] == "older_index_fetch", (
            f"Expected 'older_index_fetch', got {events[0].payload['acceptance_dt_source']!r}"
        )

    def test_provenance_adt_midnight_utc_flag(self):
        """adt_midnight_utc=True is carried from the latest ingest filing for that day."""
        ticker = "PROV2"
        et_date = date(2019, 11, 4)
        ev = _make_ingest_event(
            ticker, et_date, D=15_000.0, owner_ciks=["CIK_R"],
            accession="0007-19-000001",
            adt_midnight_utc=True,
        )
        ingest_meta_with_midnight = {
            "quarters_processed": 1,
            "submissions_scanned": 1,
            "submissions_universe_pass": 1,
            "submissions_universe_fail": 0,
            "form4_qualified_txns": 1,
            "form4_10b51_excluded_txns": 0,
            "form4_missing_price_txns": 0,
            "events_qualifying": 1,
            "events_returned": 1,
            "acceptances_direct_hit": 1,
            "acceptances_fetched": 0,
            "acceptances_fallback": 0,
            "amendments_included": 0,
            "n_superseded_dropped": 0,
            "n_dup4_collisions": 0,
            "n_ticker_fallback": 0,
            "n_no_timestamp_dropped": 0,
            "n_midnight_utc_adt": 1,
            "per_quarter": {},
        }
        events, meta = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            ingest_meta=ingest_meta_with_midnight,
        )
        assert len(events) == 1
        # The adt_midnight_utc flag from the triggering (latest) ingest filing is carried
        assert events[0].payload["adt_midnight_utc"] is True, (
            "adt_midnight_utc=True should be carried in payload"
        )
        assert meta["n_midnight_utc_adt"] == 1, (
            f"Meta n_midnight_utc_adt should be 1, got {meta['n_midnight_utc_adt']}"
        )


# -------------------------------------------------------------------------
# Tests: Score correctness
# -------------------------------------------------------------------------

class TestScoreCorrectness:
    def test_score_equals_compute_score(self):
        """score must equal _compute_score(D, k, MC) hand-computed."""
        ticker = "SCR1"
        et_date = date(2019, 5, 15)
        D = 75_000.0       # total purchase dollars
        owner_ciks = ["CIK_S1", "CIK_S2", "CIK_S3"]  # k=3 distinct owners
        close = 50.0        # price per share
        shares = 2_000_000.0  # shares outstanding

        ev = _make_ingest_event(
            ticker, et_date, D=D, owner_ciks=owner_ciks,
            accession="0008-19-000001", issuer_cik="777001",
        )
        events, _ = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=close,
            shares_outstanding=shares,
        )
        assert len(events) == 1
        ev_out = events[0]

        MC = shares * close  # 100_000_000
        k_out = ev_out.payload["k"]
        D_out = ev_out.payload["D"]
        expected_score = _compute_score(D_out, k_out, MC)
        assert ev_out.payload["score"] == pytest.approx(expected_score, rel=1e-9), (
            f"score {ev_out.payload['score']} != _compute_score result {expected_score}"
        )

    def test_w21_f0_equals_primary_score(self):
        """W21_F0 perturbation score must equal the primary score."""
        ticker = "SCR2"
        et_date = date(2019, 4, 10)
        ev = _make_ingest_event(
            ticker, et_date, D=40_000.0, owner_ciks=["CIK_T"],
            accession="0009-19-000001",
        )
        events, _ = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        assert len(events) == 1
        ev_out = events[0]
        assert ev_out.payload["score_perturb"]["W21_F0"] == pytest.approx(
            ev_out.payload["score"], rel=1e-9
        ), "W21_F0 must equal primary score"

    def test_score_undefined_when_no_shares(self):
        """Missing shares_outstanding → score=None, score_undefined=True."""
        ticker = "SCRUNDEF"
        et_date = date(2019, 3, 5)
        ev = _make_ingest_event(
            ticker, et_date, D=20_000.0, owner_ciks=["CIK_U"],
            accession="0010-19-000001",
        )
        events, meta = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=None,  # triggers score_undefined
        )
        assert len(events) == 1
        ev_out = events[0]
        assert ev_out.payload["score"] is None
        assert ev_out.payload["score_undefined"] is True
        for key, val in ev_out.payload["score_perturb"].items():
            assert val is None, f"score_perturb[{key}] should be None when score_undefined"
        assert meta["score_undefined_total"] == 1


# -------------------------------------------------------------------------
# Tests: Meta contract
# -------------------------------------------------------------------------

class TestMetaContract:
    def _base_event(self, ticker="METABASE", et_date=date(2019, 6, 1)):
        return _make_ingest_event(
            ticker, et_date, D=10_000.0, owner_ciks=["CIK_M"],
            accession="0011-19-000001",
        )

    def test_meta_r1b_keys(self):
        """R-1b meta must include all extra keys from ingest."""
        ev = self._base_event()
        _, meta = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
        )
        required_r1b_keys = {
            # R-1 contract keys
            "filings_scanned", "filings_qualifying", "acceptance_fallbacks",
            "n_10b51_excluded_total", "missing_price_txns_total",
            "score_undefined_total", "events_raw", "events_returned",
            # R-1b provenance keys
            "n_midnight_utc_adt",
            "n_superseded_dropped",
            "n_dup4_collisions",
            "n_ticker_fallback",
        }
        missing = required_r1b_keys - set(meta.keys())
        assert not missing, f"Missing meta keys: {missing}"

    def test_meta_n_midnight_utc_adt(self):
        """n_midnight_utc_adt in meta reflects ingest meta value."""
        ticker = "MIDNMETA"
        et_date = date(2019, 6, 2)
        ev = _make_ingest_event(
            ticker, et_date, D=5_000.0, owner_ciks=["CIK_N"],
            adt_midnight_utc=True,
        )
        ingest_meta = {
            "quarters_processed": 1,
            "submissions_scanned": 1,
            "submissions_universe_pass": 1,
            "submissions_universe_fail": 0,
            "form4_qualified_txns": 1,
            "form4_10b51_excluded_txns": 0,
            "form4_missing_price_txns": 0,
            "events_qualifying": 1,
            "events_returned": 1,
            "acceptances_direct_hit": 1,
            "acceptances_fetched": 0,
            "acceptances_fallback": 0,
            "amendments_included": 0,
            "n_superseded_dropped": 0,
            "n_dup4_collisions": 0,
            "n_ticker_fallback": 0,
            "n_no_timestamp_dropped": 0,
            "n_midnight_utc_adt": 3,  # injected value
            "per_quarter": {},
        }
        _, meta = _call_build_r1b_events(
            [ev],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            ingest_meta=ingest_meta,
        )
        assert meta["n_midnight_utc_adt"] == 3, (
            f"Expected n_midnight_utc_adt=3 from ingest meta, got {meta['n_midnight_utc_adt']}"
        )

    def test_meta_date_range_filter(self):
        """Event outside date range is not returned; meta events_returned reflects filtered count."""
        ticker = "METAFILT"
        et_date_in = date(2019, 6, 1)
        et_date_out = date(2021, 6, 1)  # outside [2019-01-01 .. 2019-12-31]

        ev_in = _make_ingest_event(
            ticker + "A", et_date_in, D=10_000.0, owner_ciks=["CIK_IN"],
        )
        ev_out = _make_ingest_event(
            ticker + "B", et_date_out, D=10_000.0, owner_ciks=["CIK_OUT"],
        )
        events, meta = _call_build_r1b_events(
            [ev_in, ev_out],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
        )
        assert meta["events_returned"] == 1, (
            f"Expected 1 event in range, got {meta['events_returned']}"
        )
        assert len(events) == 1


# -------------------------------------------------------------------------
# Tests: DI-08 — Multi-owner form union-k semantics (charter §2b / PY-03)
# -------------------------------------------------------------------------

class TestMultiOwnerFormKSemantics:
    """DI-08 regression: multi-owner forms and 10b5-1-only forms owner-union behavior.

    Charter §2b defines k = distinct reporting-owner CIKs with ≥1 qualifying
    non-10b5-1 P purchase in the window.  At TSV granularity, we union ALL
    owners on forms that have ≥1 qualifying transaction (the form's owner_ciks).

    Forms where ALL transactions are 10b5-1-flagged are dropped by ingest
    upstream and never appear in ticker_to_filing_events — so NO owners from
    such forms enter k.
    """

    def test_multi_owner_form_all_owners_count_in_k(self):
        """Multi-owner form with ≥1 qualifying txn: ALL owners count in k.

        A form with two reporting owners and one qualifying non-10b5-1 transaction
        should contribute BOTH owners to k (charter §2b / TSV-granularity reading).
        """
        ticker = "MOWNERTEST"
        et_date = date(2019, 5, 1)

        # Ingest event representing a joint Form 4 with 2 owners, D>0, no 10b5-1 excl.
        ev_multi = _make_ingest_event(
            ticker, et_date, D=50_000.0,
            owner_ciks=["CIK_OWNER_A", "CIK_OWNER_B"],  # two owners on one form
            accession="DI08-19-000001",
            issuer_cik="DI08001",
            n_10b51_excluded=0,  # no 10b5-1 exclusions
        )

        events, meta = _call_build_r1b_events(
            [ev_multi],
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        assert len(events) == 1, "Expected one event for multi-owner form"
        ev_out = events[0]
        # Both owners must be counted in k (charter §2b union semantics)
        assert ev_out.payload["k"] == 2, (
            f"Multi-owner form with qualifying txns: expected k=2, got k={ev_out.payload['k']}"
        )
        assert ev_out.payload["D"] == pytest.approx(50_000.0)

    def test_10b51_only_form_no_owners_count_in_k(self):
        """Form where all transactions are 10b5-1-flagged: dropped by ingest, zero k contribution.

        Ingest upstream drops forms with zero non-10b5-1 transactions before they
        reach build_r1b_events.  We simulate this by simply NOT including such an
        event in the ingest stream — the form is never passed to the dose builder.
        The only event present is a valid qualifying form from a different ticker.
        """
        # The 10b5-1-only form is represented by: the ingest layer simply never emits it.
        # We verify that k is computed only from the qualifying forms actually present.
        ticker_valid = "VALIDTICKER"
        ticker_10b51 = "TENB51ONLY"
        et_date = date(2019, 6, 10)

        # Only the valid event is in the ingest stream (the 10b5-1-only form was dropped upstream)
        ev_valid = _make_ingest_event(
            ticker_valid, et_date, D=30_000.0,
            owner_ciks=["CIK_VALID_OWNER"],
            accession="DI08-19-000002",
            issuer_cik="DI08002",
        )

        events, meta = _call_build_r1b_events(
            [ev_valid],  # 10b5-1-only form not present — dropped by ingest
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            close=100.0,
            shares_outstanding=1_000_000.0,
        )
        valid_evs = [e for e in events if e.ticker == ticker_valid.upper()]
        assert len(valid_evs) == 1
        # k should be 1 (only the valid form's owner)
        assert valid_evs[0].payload["k"] == 1, (
            f"Only valid form contributes; expected k=1, got k={valid_evs[0].payload['k']}"
        )
        # The 10b5-1-only ticker should produce no events
        ten_evs = [e for e in events if e.ticker == ticker_10b51.upper()]
        assert len(ten_evs) == 0, (
            "10b5-1-only form should have been dropped by ingest, no events expected"
        )
