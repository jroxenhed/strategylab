"""Tests for research/r1_dose.py — R-1 dose builder.

Coverage:
  Window mechanics
    test_window_edge_day21_in         — filing on day-21 boundary IS in window
    test_window_edge_day22_out        — filing on day-22 boundary is OUT of window
    test_w20_variant_start_differs    — W=20 window start is later than W=21
    test_w22_variant_start_differs    — W=22 window start is earlier than W=21

  10b5-1 exclusion
    test_10b51_excluded_from_D_and_k  — transaction-level 10b5-1 excluded from D and k
    test_10b51_all_excluded_no_event  — filing whose ONLY qualifying txns are 10b5-1 does not open event

  Distinct-CIK counting
    test_distinct_cik_same_twice      — same owner CIK appears in two filings → k=1

  Missing price / MC
    test_missing_price_contributes_zero — transaction with no price → $0 in D, counted
    test_missing_mc_score_undefined   — missing shares-outstanding → score=None, score_undefined=True

  Floor clamp
    test_floor_clamp_D_lt_F           — D < 40k → D clamped to 0 for F=40k variant

  Score formula
    test_score_formula_hand_computed  — verify score = log1p(D/MC) * (1 + 0.5*k)
    test_w21_f0_equals_primary_score  — W21_F0 perturbation == primary score

  Acceptance fallback
    test_acceptance_fallback_path     — missing acceptanceDateTime → filingDate+16:01 fallback

  One-event-per-day collapse
    test_one_event_per_day_two_filings — two filings on same ET date → one EventRecord,
                                         event_ts = latest
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
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
    build_r1_events,
    _busday_window_start,
    _compute_score,
    _is_10b51_text,
    _parse_qualifying_transactions,
    _aggregate_dose_window,
)

# -------------------------------------------------------------------------
# XML fixture factory
# -------------------------------------------------------------------------

def _make_form4_xml(
    owner_cik: str = "0000111111",
    shares: float = 1000.0,
    price: float = 50.0,
    code: str = "P",
    adc: str = "A",
    footnotes: str = "",
    remarks: str = "",
) -> str:
    """Build a minimal synthetic Form 4 XML.

    Parameters
    ----------
    owner_cik   : reporting owner CIK
    shares      : transactionShares
    price       : transactionPricePerShare (empty string → omit element)
    code        : transactionCode
    adc         : acquiredDisposedCode value
    footnotes   : text to inject into a <footnote> element on the form
    remarks     : text to inject into a <remarks> element on the form
    """
    price_block = (
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        if price != ""
        else ""
    )
    footnote_block = f"<footnotes><footnote id='F1'>{footnotes}</footnote></footnotes>" if footnotes else ""
    remarks_block = f"<remarks>{remarks}</remarks>" if remarks else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ownershipDocument>
  <issuer>
    <issuerCik>0000999999</issuerCik>
    <issuerName>Test Corp</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>{owner_cik}</rptOwnerCik>
      <rptOwnerName>Test Owner</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2019-06-01</value></transactionDate>
      <transactionCode>{code}</transactionCode>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        {price_block}
        <transactionAcquiredDisposedCode><value>{adc}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  {footnote_block}
  {remarks_block}
</ownershipDocument>"""


def _make_price_df(start: date, end: date, close: float = 100.0) -> pd.DataFrame:
    """Synthetic daily OHLCV DataFrame with naive DatetimeIndex."""
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)
    closes = [close] * n
    opens = [close * 0.99] * n
    return pd.DataFrame({
        "Open": opens,
        "High": [close * 1.01] * n,
        "Low": [close * 0.99] * n,
        "Close": closes,
        "Volume": [1_000_000] * n,
    }, index=dates)


def _write_xml(xml_dir: Path, padded_cik: str, accession_nodash: str, xml: str) -> None:
    fname = f"{padded_cik}_{accession_nodash}.xml"
    (xml_dir / fname).write_text(xml, encoding="utf-8")


def _write_subs(subs_dir: Path, padded_cik: str, ticker: str, filings: list[dict]) -> None:
    """Write a minimal submissions JSON file."""
    # filings is list of {accessionNumber, filingDate, form, acceptanceDateTime (optional)}
    data = {
        "cik": padded_cik.lstrip("0"),
        "tickers": [ticker],
        "filings": {
            "recent": {
                "form": [f["form"] for f in filings],
                "accessionNumber": [f["accessionNumber"] for f in filings],
                "filingDate": [f["filingDate"] for f in filings],
                "acceptanceDateTime": [f.get("acceptanceDateTime", "") for f in filings],
            }
        },
    }
    (subs_dir / f"{padded_cik}.json").write_text(json.dumps(data), encoding="utf-8")


def _build_index(
    ticker: str,
    cik: str,
    as_of: str,
    filings: list[dict],
) -> dict:
    """Build a minimal index dict for one event_key."""
    padded = cik.zfill(10)
    return {
        f"{ticker.upper()}_{as_of}": {
            "ticker": ticker.upper(),
            "cik": cik,
            "status": "done",
            "filings": filings,
        }
    }


def _make_test_env(
    tmp_path: Path,
    ticker: str = "TSTCO",
    cik: str = "111111",
    as_of_str: str = "2019-06-01",
    accession: str = "0001234567-19-000001",
    owner_cik: str = "0000111111",
    shares: float = 1000.0,
    price: float = 50.0,
    close: float = 100.0,
    footnotes: str = "",
    remarks: str = "",
    acceptance_dt: str = "2019-06-01T20:00:00.000Z",
    filed: str = "2019-06-01",
    shares_outstanding: Optional[float] = 1_000_000.0,
) -> tuple[Path, Path, Path, dict, callable, callable]:
    """
    Create a full test environment in tmp_path.

    Returns: (index_path, xml_dir, subs_dir, index_dict, loader_fn, shares_fn)
    """
    xml_dir = tmp_path / "form4_stratified"
    xml_dir.mkdir()
    subs_dir = tmp_path / "submissions"
    subs_dir.mkdir()

    padded = cik.zfill(10)
    accession_nodash = accession.replace("-", "")

    # Write XML
    xml = _make_form4_xml(
        owner_cik=owner_cik, shares=shares, price=price,
        footnotes=footnotes, remarks=remarks,
    )
    _write_xml(xml_dir, padded, accession_nodash, xml)

    # Write submissions
    _write_subs(
        subs_dir, padded, ticker,
        [{"form": "4", "accessionNumber": accession, "filingDate": filed,
          "acceptanceDateTime": acceptance_dt}],
    )

    # Write index
    index = _build_index(ticker, cik, as_of_str, [
        {"accession": accession, "filed": filed, "xml_status": "ok"}
    ])
    index_path = xml_dir / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    # Price frame covering a wide window
    frame_start = date(2019, 1, 1)
    frame_end = date(2019, 12, 31)
    df = _make_price_df(frame_start, frame_end, close=close)
    frames = {ticker.upper(): df}
    loader_fn = lambda sym: frames.get(sym.upper())

    # Shares function
    if shares_outstanding is not None:
        shares_fn = lambda cik_, as_of_: shares_outstanding
    else:
        shares_fn = lambda cik_, as_of_: None

    return index_path, xml_dir, subs_dir, index, loader_fn, shares_fn


# -------------------------------------------------------------------------
# Tests: window mechanics
# -------------------------------------------------------------------------

class TestWindowMechanics:
    def test_window_edge_day21_in(self, tmp_path):
        """A filing whose filed date is exactly 21 business days before event_date
        falls within the W=21 window (inclusive)."""
        # event_date: 2019-07-01 (Monday)
        # 21 business days before 2019-07-01 = compute with numpy
        event_date = date(2019, 7, 1)
        window_start = _busday_window_start(event_date, 21)
        # Verify the window contains exactly 21 business days [start..event_date]
        bdays_in_window = np.busday_count(
            window_start.isoformat(), event_date.isoformat()
        ) + 1  # inclusive
        assert bdays_in_window == 21, f"Expected 21, got {bdays_in_window}"
        # Filed exactly on window_start → in window
        assert window_start <= event_date

        # Build env with a filing on window_start
        filed_str = window_start.isoformat()
        _, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="EDGE21",
            cik="200001",
            as_of_str=event_date.isoformat(),
            accession="0000200001-19-000100",
            filed=filed_str,
            acceptance_dt=filed_str + "T20:00:00.000Z",
        )
        index_path = xml_dir / "index.json"
        cik = "200001"
        padded = cik.zfill(10)
        index = json.loads(index_path.read_text())

        D, k, n_filings, _, _ = _aggregate_dose_window(
            "EDGE21", event_date, 21, xml_dir, index, cik
        )
        assert n_filings >= 1, "Filing on day-21 boundary must be counted"
        assert k >= 1
        assert D > 0

    def test_window_edge_day22_out(self, tmp_path):
        """A filing whose filed date is 22 business days before event_date
        is outside the W=21 window."""
        event_date = date(2019, 7, 1)
        window_start = _busday_window_start(event_date, 21)
        # Day 22 = one business day before window_start
        day22 = np.busday_offset(window_start.isoformat(), -1, roll="backward")
        day22_date = date.fromisoformat(str(day22))
        assert day22_date < window_start

        filed_str = day22_date.isoformat()
        _, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="EDGE22",
            cik="200002",
            as_of_str=event_date.isoformat(),
            accession="0000200002-19-000100",
            filed=filed_str,
            acceptance_dt=filed_str + "T20:00:00.000Z",
        )
        index_path = xml_dir / "index.json"
        cik = "200002"
        index = json.loads(index_path.read_text())

        D, k, n_filings, _, _ = _aggregate_dose_window(
            "EDGE22", event_date, 21, xml_dir, index, cik
        )
        assert n_filings == 0, "Filing on day-22 boundary must NOT be counted"
        assert D == 0.0
        assert k == 0

    def test_w20_variant_start_differs(self):
        """W=20 window starts 1 business day later than W=21."""
        event_date = date(2019, 6, 3)
        start21 = _busday_window_start(event_date, 21)
        start20 = _busday_window_start(event_date, 20)
        # W=20 window is shorter → starts later
        assert start20 > start21

    def test_w22_variant_start_differs(self):
        """W=22 window starts 1 business day earlier than W=21."""
        event_date = date(2019, 6, 3)
        start21 = _busday_window_start(event_date, 21)
        start22 = _busday_window_start(event_date, 22)
        assert start22 < start21


# -------------------------------------------------------------------------
# Tests: 10b5-1 exclusion
# -------------------------------------------------------------------------

class Test10b51Exclusion:
    def test_10b51_excluded_from_D_and_k(self, tmp_path):
        """Transaction with 10b5-1 marker in footnote is excluded from D and k."""
        # Filing with 10b5-1 in footnote
        _, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="B51EX",
            cik="300001",
            as_of_str="2019-06-03",
            accession="0000300001-19-000200",
            owner_cik="0000300001",
            shares=2000.0,
            price=75.0,
            footnotes="This transaction is pursuant to a Rule 10b5-1 trading plan",
        )
        cik = "300001"
        event_date = date(2019, 6, 3)
        index = json.loads((xml_dir / "index.json").read_text())

        D, k, n_filings, n_10b51_excl, _ = _aggregate_dose_window(
            "B51EX", event_date, 21, xml_dir, index, cik
        )
        assert n_10b51_excl >= 1, "At least one 10b5-1 transaction should be excluded"
        # The 10b5-1 transaction is excluded → D and k should be 0
        assert D == 0.0
        assert k == 0

    def test_10b51_all_excluded_no_event(self, tmp_path):
        """Filing whose ONLY qualifying transactions are 10b5-1 → no EventRecord returned."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="B51ALL",
            cik="300002",
            as_of_str="2019-06-03",
            accession="0000300002-19-000300",
            owner_cik="0000300002",
            shares=1000.0,
            price=50.0,
            footnotes="Adopted pursuant to 10b5-1 plan",
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
        )

        events, meta = build_r1_events(
            date(2019, 1, 1),
            date(2019, 12, 31),
            index_path=index_path,
            xml_dir=xml_dir,
            subs_dir=subs_dir,
            loader_fn=loader_fn,
            shares_fn=shares_fn,
        )
        # No EventRecord should be returned because all qualifying txns are 10b5-1
        assert len(events) == 0, (
            f"Expected 0 events (all 10b5-1), got {len(events)}"
        )
        assert meta["n_10b51_excluded_total"] >= 1

    def test_10b51_pattern_case_insensitive(self):
        """10b5-1 markers are detected case-insensitively."""
        assert _is_10b51_text("pursuant to 10B5-1 plan") is True
        assert _is_10b51_text("Rule 10b5-1 trading plan") is True
        assert _is_10b51_text("10b5_1 plan adopted") is True
        assert _is_10b51_text("regular open market purchase") is False


# -------------------------------------------------------------------------
# Tests: Distinct-CIK counting
# -------------------------------------------------------------------------

class TestDistinctCIK:
    def test_distinct_cik_same_twice(self, tmp_path):
        """Same owner CIK appearing in two filings within the window → k=1."""
        cik = "400001"
        padded = cik.zfill(10)
        owner_cik = "0000400001"
        ticker = "SAMEOWN"
        event_date = date(2019, 6, 3)
        filed = "2019-06-01"

        xml_dir = tmp_path / "form4_stratified"
        xml_dir.mkdir()
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        # Two filings, same owner CIK
        acc1 = "0000400001-19-000401"
        acc2 = "0000400001-19-000402"
        acc1_nd = acc1.replace("-", "")
        acc2_nd = acc2.replace("-", "")

        xml1 = _make_form4_xml(owner_cik=owner_cik, shares=500.0, price=50.0)
        xml2 = _make_form4_xml(owner_cik=owner_cik, shares=500.0, price=50.0)
        _write_xml(xml_dir, padded, acc1_nd, xml1)
        _write_xml(xml_dir, padded, acc2_nd, xml2)

        _write_subs(subs_dir, padded, ticker, [
            {"form": "4", "accessionNumber": acc1, "filingDate": filed,
             "acceptanceDateTime": filed + "T18:00:00.000Z"},
            {"form": "4", "accessionNumber": acc2, "filingDate": filed,
             "acceptanceDateTime": filed + "T19:00:00.000Z"},
        ])

        index = {
            f"{ticker}_{event_date.isoformat()}": {
                "ticker": ticker, "cik": cik, "status": "done",
                "filings": [
                    {"accession": acc1, "filed": filed, "xml_status": "ok"},
                    {"accession": acc2, "filed": filed, "xml_status": "ok"},
                ],
            }
        }
        index_path = xml_dir / "index.json"
        index_path.write_text(json.dumps(index))

        D, k, n_filings, _, _ = _aggregate_dose_window(
            ticker, event_date, 21, xml_dir, index, cik
        )
        assert k == 1, f"Same owner CIK twice should yield k=1, got k={k}"
        assert n_filings == 2
        assert D == pytest.approx(1000.0 * 50.0)  # 500+500 shares at $50

    def test_two_distinct_ciks(self, tmp_path):
        """Two different owner CIKs → k=2."""
        cik = "400002"
        padded = cik.zfill(10)
        ticker = "TWOOWN"
        event_date = date(2019, 6, 3)
        filed = "2019-06-01"

        xml_dir = tmp_path / "form4_stratified"
        xml_dir.mkdir()
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        acc1 = "0000400002-19-000501"
        acc2 = "0000400002-19-000502"

        xml1 = _make_form4_xml(owner_cik="0000400002", shares=1000.0, price=50.0)
        xml2 = _make_form4_xml(owner_cik="0000400003", shares=1000.0, price=50.0)
        _write_xml(xml_dir, padded, acc1.replace("-", ""), xml1)
        _write_xml(xml_dir, padded, acc2.replace("-", ""), xml2)

        _write_subs(subs_dir, padded, ticker, [
            {"form": "4", "accessionNumber": acc1, "filingDate": filed,
             "acceptanceDateTime": filed + "T18:00:00.000Z"},
            {"form": "4", "accessionNumber": acc2, "filingDate": filed,
             "acceptanceDateTime": filed + "T19:00:00.000Z"},
        ])

        index = {
            f"{ticker}_{event_date.isoformat()}": {
                "ticker": ticker, "cik": cik, "status": "done",
                "filings": [
                    {"accession": acc1, "filed": filed, "xml_status": "ok"},
                    {"accession": acc2, "filed": filed, "xml_status": "ok"},
                ],
            }
        }
        index_path = xml_dir / "index.json"
        index_path.write_text(json.dumps(index))

        D, k, n_filings, _, _ = _aggregate_dose_window(
            ticker, event_date, 21, xml_dir, index, cik
        )
        assert k == 2, f"Expected k=2, got k={k}"


# -------------------------------------------------------------------------
# Tests: Missing price / MC
# -------------------------------------------------------------------------

class TestMissingPriceAndMC:
    def test_missing_price_contributes_zero(self, tmp_path):
        """Transaction with no price element → contributes $0 to D, counts as missing_price_txn."""
        cik = "500001"
        padded = cik.zfill(10)
        ticker = "NOPRICE"
        event_date = date(2019, 6, 3)
        filed = "2019-06-01"

        xml_dir = tmp_path / "form4_stratified"
        xml_dir.mkdir()
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        acc = "0000500001-19-000601"
        # price="" means the price element is omitted
        xml = _make_form4_xml(owner_cik="0000500001", shares=1000.0, price="")
        _write_xml(xml_dir, padded, acc.replace("-", ""), xml)

        _write_subs(subs_dir, padded, ticker, [
            {"form": "4", "accessionNumber": acc, "filingDate": filed,
             "acceptanceDateTime": filed + "T20:00:00.000Z"},
        ])

        index = {
            f"{ticker}_{event_date.isoformat()}": {
                "ticker": ticker, "cik": cik, "status": "done",
                "filings": [{"accession": acc, "filed": filed, "xml_status": "ok"}],
            }
        }
        index_path = xml_dir / "index.json"
        index_path.write_text(json.dumps(index))

        D, k, n_filings, n_10b51, missing_price = _aggregate_dose_window(
            ticker, event_date, 21, xml_dir, index, cik
        )
        # k=1 (owner still counted — they bought, just no price)
        assert k == 1, f"Expected k=1, got k={k}"
        assert D == 0.0, f"Expected D=0 (no price), got D={D}"
        assert missing_price >= 1, "Missing price count should be >= 1"

    def test_missing_price_event_still_returned(self, tmp_path):
        """An event with missing price (D=0) is still returned as an EventRecord."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="MISPRICE",
            cik="500002",
            as_of_str="2019-06-03",
            price="",  # no price
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
        )
        events, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        assert meta["missing_price_txns_total"] >= 1
        # score should be 0 (D=0 → log1p(0)=0)
        assert events[0].payload["score"] == pytest.approx(0.0)

    def test_missing_mc_score_undefined(self, tmp_path):
        """Missing shares-outstanding → score=None, score_undefined=True, event counted."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="NOMC",
            cik="500003",
            as_of_str="2019-06-03",
            shares=1000.0,
            price=50.0,
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=None,  # triggers score_undefined
        )
        events, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        ev = events[0]
        assert ev.payload["score"] is None
        assert ev.payload["score_undefined"] is True
        assert meta["score_undefined_total"] == 1
        # All perturbation scores should also be None
        for key, val in ev.payload["score_perturb"].items():
            assert val is None, f"score_perturb[{key}] should be None when MC undefined"


# -------------------------------------------------------------------------
# Tests: Floor clamp
# -------------------------------------------------------------------------

class TestFloorClamp:
    def test_floor_clamp_D_lt_F(self, tmp_path):
        """D < 40k → D clamped to 0 for F=40k variant; primary (F=0) unchanged."""
        # D = 1000 shares * $20 = $20,000 < $40,000 floor
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="FLRTEST",
            cik="600001",
            as_of_str="2019-06-03",
            shares=1000.0,
            price=20.0,  # D = 20,000
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
            close=100.0,
        )
        events, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        ev = events[0]
        perturb = ev.payload["score_perturb"]

        # Primary: D=20k, MC=100M → score = log1p(20000/100000000) * 1.5
        # F=0 (W21): same as primary
        assert perturb["W21_F0"] == pytest.approx(ev.payload["score"])

        # F=40k: D=20k < 40k → clamped to 0 → score = 0
        assert perturb["W21_F40k"] == pytest.approx(0.0), (
            f"W21_F40k should be 0 when D < 40k, got {perturb['W21_F40k']}"
        )

        # F=60k: D=20k < 60k → clamped to 0 → score = 0
        assert perturb["W21_F60k"] == pytest.approx(0.0)

    def test_floor_clamp_D_gt_F_unchanged(self, tmp_path):
        """D > 60k → floor clamp does NOT change D for any variant."""
        # D = 2000 shares * $50 = $100,000 > $60,000
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="NOCLAMP",
            cik="600002",
            as_of_str="2019-06-03",
            shares=2000.0,
            price=50.0,  # D = 100,000
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
            close=100.0,
        )
        events, _ = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        ev = events[0]
        perturb = ev.payload["score_perturb"]
        primary = ev.payload["score"]

        # With D=100k > 60k, floor clamp doesn't apply for any F
        # All W21 variants should have same D → same score (only window differs for W20/W22)
        assert perturb["W21_F0"] == pytest.approx(primary)
        # F=40k: D=100k > 40k → not clamped → score same as F=0 for W=21
        assert perturb["W21_F40k"] == pytest.approx(primary)
        assert perturb["W21_F60k"] == pytest.approx(primary)


# -------------------------------------------------------------------------
# Tests: Score formula
# -------------------------------------------------------------------------

class TestScoreFormula:
    def test_score_formula_hand_computed(self):
        """Verify score = log1p(D/MC) * (1 + 0.5*k) by hand."""
        D = 100_000.0  # $100k total purchase
        MC = 50_000_000.0  # $50M market cap
        k = 3  # 3 distinct insiders

        expected = math.log1p(D / MC) * (1.0 + 0.5 * k)
        computed = _compute_score(D, k, MC)
        assert computed == pytest.approx(expected, rel=1e-9), (
            f"Hand-computed {expected} != _compute_score {computed}"
        )

    def test_score_zero_dollars(self):
        """D=0 → score=0 regardless of k."""
        assert _compute_score(0.0, 5, 1_000_000.0) == pytest.approx(0.0)

    def test_score_k_amplification(self):
        """Higher k → higher score for same D and MC."""
        D = 50_000.0
        MC = 10_000_000.0
        s1 = _compute_score(D, 1, MC)
        s3 = _compute_score(D, 3, MC)
        assert s3 > s1, f"k=3 should score higher than k=1; got {s3} vs {s1}"

    def test_w21_f0_equals_primary_score(self, tmp_path):
        """W21_F0 perturbation score must equal the primary score exactly."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="W21CHK",
            cik="700001",
            as_of_str="2019-06-03",
            shares=1000.0,
            price=50.0,
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
            close=100.0,
        )
        events, _ = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        ev = events[0]
        assert ev.payload["score_perturb"]["W21_F0"] == pytest.approx(
            ev.payload["score"], rel=1e-9
        ), "W21_F0 must equal primary score"


# -------------------------------------------------------------------------
# Tests: Acceptance fallback
# -------------------------------------------------------------------------

class TestAcceptanceFallback:
    def test_acceptance_fallback_path(self, tmp_path):
        """When acceptanceDateTime is absent in submissions, fall back to filingDate+16:01."""
        cik = "800001"
        padded = cik.zfill(10)
        ticker = "FLLBCK"
        filed = "2019-06-03"

        xml_dir = tmp_path / "form4_stratified"
        xml_dir.mkdir()
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        acc = "0000800001-19-000801"
        xml = _make_form4_xml(owner_cik="0000800001", shares=1000.0, price=50.0)
        _write_xml(xml_dir, padded, acc.replace("-", ""), xml)

        # Write submissions WITHOUT acceptanceDateTime (empty string)
        _write_subs(subs_dir, padded, ticker, [
            {"form": "4", "accessionNumber": acc, "filingDate": filed,
             "acceptanceDateTime": ""}  # no acceptance dt
        ])

        index = {
            f"{ticker}_{filed}": {
                "ticker": ticker, "cik": cik, "status": "done",
                "filings": [{"accession": acc, "filed": filed, "xml_status": "ok"}],
            }
        }
        index_path = xml_dir / "index.json"
        index_path.write_text(json.dumps(index))

        df = _make_price_df(date(2019, 1, 1), date(2019, 12, 31), close=100.0)
        loader_fn = lambda sym: df if sym.upper() == ticker else None
        shares_fn = lambda cik_, as_of_: 1_000_000.0

        events, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        ev = events[0]
        assert ev.is_fallback is True
        assert meta["acceptance_fallbacks"] == 1
        assert ev.payload["acceptance_fallback"] is True

        # event_ts should be 2019-06-03 21:01:00 UTC (16:01 ET = 21:01 UTC EST)
        # The fallback uses UTC-5 (conservative EST), so 16:01 ET = 21:01 UTC
        expected_ts = datetime(2019, 6, 3, 21, 1, 0, tzinfo=timezone.utc)
        assert ev.event_ts == expected_ts


# -------------------------------------------------------------------------
# Tests: One-event-per-day collapse
# -------------------------------------------------------------------------

class TestOneEventPerDay:
    def test_two_filings_same_day_one_event(self, tmp_path):
        """Two filings on same ET calendar date → one EventRecord.
        event_ts = latest acceptance ts."""
        cik = "900001"
        padded = cik.zfill(10)
        ticker = "DUPDAY"
        filed = "2019-06-03"

        xml_dir = tmp_path / "form4_stratified"
        xml_dir.mkdir()
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        acc1 = "0000900001-19-000901"
        acc2 = "0000900001-19-000902"
        # Two filings on same day, different acceptance times (same ET date)
        adt1 = "2019-06-03T18:00:00.000Z"  # earlier
        adt2 = "2019-06-03T22:00:00.000Z"  # later

        xml1 = _make_form4_xml(owner_cik="0000900001", shares=1000.0, price=50.0)
        xml2 = _make_form4_xml(owner_cik="0000900002", shares=500.0, price=50.0)
        _write_xml(xml_dir, padded, acc1.replace("-", ""), xml1)
        _write_xml(xml_dir, padded, acc2.replace("-", ""), xml2)

        _write_subs(subs_dir, padded, ticker, [
            {"form": "4", "accessionNumber": acc1, "filingDate": filed, "acceptanceDateTime": adt1},
            {"form": "4", "accessionNumber": acc2, "filingDate": filed, "acceptanceDateTime": adt2},
        ])

        # Two separate index entries (different as_of)
        index = {
            f"{ticker}_{filed}_A": {
                "ticker": ticker, "cik": cik, "status": "done",
                "filings": [{"accession": acc1, "filed": filed, "xml_status": "ok"}],
            },
            f"{ticker}_{filed}_B": {
                "ticker": ticker, "cik": cik, "status": "done",
                "filings": [{"accession": acc2, "filed": filed, "xml_status": "ok"}],
            },
        }
        index_path = xml_dir / "index.json"
        index_path.write_text(json.dumps(index))

        df = _make_price_df(date(2019, 1, 1), date(2019, 12, 31), close=100.0)
        loader_fn = lambda sym: df if sym.upper() == ticker else None
        shares_fn = lambda cik_, as_of_: 1_000_000.0

        events, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        # One event per (ticker, ET date)
        assert len(events) == 1, f"Expected 1 event (same ET date), got {len(events)}"
        ev = events[0]
        # event_ts should be the latest acceptance ts
        expected_latest = datetime(2019, 6, 3, 22, 0, 0, tzinfo=timezone.utc)
        assert ev.event_ts == expected_latest, (
            f"event_ts should be latest ts: {ev.event_ts} vs {expected_latest}"
        )


# -------------------------------------------------------------------------
# Tests: Full integration / meta counts
# -------------------------------------------------------------------------

class TestIntegration:
    def test_basic_event_returned(self, tmp_path):
        """A valid single filing returns exactly one event with correct payload keys."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="BASIC",
            cik="100001",
            as_of_str="2019-06-03",
            shares=1000.0,
            price=50.0,
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
            close=100.0,
        )
        events, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 1
        ev = events[0]

        required_keys = {
            "form_type", "accession", "filing_date", "acceptance_fallback",
            "score", "score_undefined", "D", "k", "MC",
            "n_filings_window", "n_10b51_excluded", "missing_price_txns",
            "score_perturb",
        }
        assert required_keys.issubset(set(ev.payload.keys())), (
            f"Missing payload keys: {required_keys - set(ev.payload.keys())}"
        )

        # Verify all 9 perturbation keys are present
        assert len(ev.payload["score_perturb"]) == 9
        expected_perturb_keys = {
            "W20_F0", "W20_F40k", "W20_F60k",
            "W21_F0", "W21_F40k", "W21_F60k",
            "W22_F0", "W22_F40k", "W22_F60k",
        }
        assert set(ev.payload["score_perturb"].keys()) == expected_perturb_keys

    def test_date_range_filter(self, tmp_path):
        """Filing outside [start, end] date range is not returned."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="DFILT",
            cik="100002",
            as_of_str="2019-06-03",
            shares=1000.0,
            price=50.0,
            acceptance_dt="2019-06-03T20:00:00.000Z",  # ET date = 2019-06-03
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
        )
        # Filter to future range — event should be excluded
        events, meta = build_r1_events(
            date(2020, 1, 1), date(2020, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        assert len(events) == 0

    def test_meta_counts_populated(self, tmp_path):
        """Meta dict contains all expected keys."""
        index_path, xml_dir, subs_dir, _, loader_fn, shares_fn = _make_test_env(
            tmp_path,
            ticker="META",
            cik="100003",
            as_of_str="2019-06-03",
            shares=1000.0,
            price=50.0,
            acceptance_dt="2019-06-03T20:00:00.000Z",
            filed="2019-06-03",
            shares_outstanding=1_000_000.0,
        )
        _, meta = build_r1_events(
            date(2019, 1, 1), date(2019, 12, 31),
            index_path=index_path, xml_dir=xml_dir, subs_dir=subs_dir,
            loader_fn=loader_fn, shares_fn=shares_fn,
        )
        required_meta_keys = {
            "filings_scanned", "filings_qualifying", "acceptance_fallbacks",
            "n_10b51_excluded_total", "missing_price_txns_total",
            "score_undefined_total", "events_raw", "events_returned",
        }
        assert required_meta_keys.issubset(set(meta.keys())), (
            f"Missing meta keys: {required_meta_keys - set(meta.keys())}"
        )
