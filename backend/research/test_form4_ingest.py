"""Unit tests for form4_ingest.py.

These tests use synthetic fixtures and test the pure-logic helpers.
They CANNOT prove correctness on real data — that is the probe's job.

Run:
    backend/venv/bin/python3 -m pytest backend/research/test_form4_ingest.py -x -q
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Path setup — run from repo root or from backend/
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from research.form4_ingest import (  # noqa: E402
    _parse_filing_date,
    _form_is_10b51_tsv,
    _txn_row_is_10b51,
    _resolve_event_ts,
    _fetch_older_page,
    _load_quarter_tables,
    _process_quarter,
    _dedup_amendments,
    build_form4_dataset_events,
)
from research.r1_dose import _is_10b51_text  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers to build minimal synthetic ZIPs
# ---------------------------------------------------------------------------

def _make_tsv(rows: list[dict], columns: list[str]) -> bytes:
    """Encode rows as TSV bytes."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, delimiter="\t",
                            quoting=csv.QUOTE_NONE, quotechar="\x00",
                            extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in columns})
    return buf.getvalue().encode("utf-8")


def _make_zip(tables: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP containing named TSV files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in tables.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Test: _parse_filing_date
# ---------------------------------------------------------------------------

class TestParseFilingDate:
    def test_basic(self):
        d = _parse_filing_date("31-JAN-2018")
        assert d == date(2018, 1, 31)

    def test_march(self):
        d = _parse_filing_date("15-MAR-2020")
        assert d == date(2020, 3, 15)

    def test_none_on_empty(self):
        assert _parse_filing_date("") is None
        assert _parse_filing_date(None) is None

    def test_iso_format_fails(self):
        # ISO format is NOT supported (DD-MMM-YYYY expected)
        assert _parse_filing_date("2018-01-31") is None


# ---------------------------------------------------------------------------
# Test: _is_10b51_text (imported from r1_dose — verify we're using the real function)
# ---------------------------------------------------------------------------

class TestIs10b51Text:
    def test_positive_hyphen(self):
        assert _is_10b51_text("Pursuant to 10b5-1 plan") is True

    def test_positive_nohyphen(self):
        assert _is_10b51_text("rule 10b5 trading plan") is True

    def test_positive_underscore(self):
        assert _is_10b51_text("10b5_1 arrangement") is True

    def test_negative_empty(self):
        assert _is_10b51_text("") is False
        assert _is_10b51_text(None) is False

    def test_negative_irrelevant(self):
        assert _is_10b51_text("Open-market purchase of common stock") is False

    def test_case_insensitive(self):
        assert _is_10b51_text("RULE 10B5-1 PLAN") is True


# ---------------------------------------------------------------------------
# Test: _form_is_10b51_tsv
# ---------------------------------------------------------------------------

class TestFormIs10b51Tsv:
    def test_positive_remarks(self):
        assert _form_is_10b51_tsv("ACC1", "Executed under 10b5-1 plan", {}) is True

    def test_positive_footnote(self):
        footnotes = {"ACC1": ["This sale is pursuant to a Rule 10b5-1 plan."]}
        assert _form_is_10b51_tsv("ACC1", "", footnotes) is True

    def test_negative(self):
        footnotes = {"ACC1": ["No plan referenced here."]}
        assert _form_is_10b51_tsv("ACC1", "Normal purchase", footnotes) is False

    def test_no_footnotes(self):
        assert _form_is_10b51_tsv("ACC1", "Normal purchase", {}) is False

    def test_different_accession(self):
        # Footnote exists for ACC2, not ACC1
        footnotes = {"ACC2": ["10b5-1 plan"]}
        assert _form_is_10b51_tsv("ACC1", "", footnotes) is False


# ---------------------------------------------------------------------------
# Test: _resolve_event_ts with fallback (no submissions cache file)
# ---------------------------------------------------------------------------

class TestResolveEventTs:
    def test_fallback_when_no_subs_file(self, tmp_path):
        """When no submissions file exists, fall back to filing_date + 16:01 ET."""
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()
        event_ts, is_fallback, source = _resolve_event_ts(
            "0000036270",
            "0001209191-18-006272",
            "2018-01-31",
            subs_dir,
            fetch_missing=False,
        )
        assert is_fallback is True
        assert source == "filing_date_fallback"
        # 2018-01-31 + 16:01 ET → 21:01 UTC (EST offset)
        assert event_ts.year == 2018
        assert event_ts.month == 1
        assert event_ts.day == 31

    def test_direct_hit(self, tmp_path):
        """When accession is in submissions recent, return direct_hit."""
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()
        # Write a fake submissions JSON
        padded_cik = "0000036270"
        acc = "0001209191-18-006272"
        adt = "2018-01-31T20:13:31.000Z"
        subs_data = {
            "filings": {
                "recent": {
                    "accessionNumber": [acc],
                    "acceptanceDateTime": [adt],
                },
                "files": [],
            }
        }
        (subs_dir / f"{padded_cik}.json").write_text(json.dumps(subs_data))
        event_ts, is_fallback, source = _resolve_event_ts(
            padded_cik, acc, "2018-01-31", subs_dir, fetch_missing=False,
        )
        assert is_fallback is False
        assert source == "direct_hit"
        assert event_ts.tzinfo is not None
        assert event_ts.year == 2018


# ---------------------------------------------------------------------------
# Test: _load_quarter_tables on a synthetic ZIP
# ---------------------------------------------------------------------------

class TestLoadQuarterTables:
    def _make_test_zip(self) -> Path:
        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT",
                    "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED", "NOT_SUBJECT_SEC16",
                    "FORM3_HOLDINGS_REPORTED", "FORM4_TRANS_REPORTED",
                    "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME",
                    "ISSUERTRADINGSYMBOL", "REMARKS"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]

        sub_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000001",
            "FILING_DATE": "15-JAN-2018",
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "0000036270",
            "ISSUERTRADINGSYMBOL": "MTB",
            "REMARKS": "",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000001",
            "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "TRANS_SHARES": "500",
            "TRANS_PRICEPERSHARE": "150.25",
        }]
        owner_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000001",
            "RPTOWNERCIK": "0001234567",
        }]
        fn_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000001",
            "FOOTNOTE_ID": "F1",
            "FOOTNOTE_TXT": "Open-market purchase.",
        }]

        tables = {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv(owner_rows, owner_cols),
            "FOOTNOTES.tsv": _make_tsv(fn_rows, fn_cols),
        }
        return tables

    def test_tables_load(self, tmp_path):
        tables = self._make_test_zip()
        zip_path = tmp_path / "2018q1_form345.zip"
        zip_path.write_bytes(_make_zip(tables))

        sub_df, owner_df, nd_df, fn_df = _load_quarter_tables(zip_path)
        assert len(sub_df) == 1
        assert len(nd_df) == 1
        assert len(owner_df) == 1
        assert len(fn_df) == 1
        assert sub_df.iloc[0]["DOCUMENT_TYPE"] == "4"
        assert nd_df.iloc[0]["TRANS_CODE"] == "P"


# ---------------------------------------------------------------------------
# Test: _process_quarter integration test (synthetic, offline)
# ---------------------------------------------------------------------------

class TestProcessQuarter:
    def _build_subs_file(self, subs_dir: Path, padded_cik: str, accession: str):
        """Write a fake submissions JSON with direct acceptanceDateTime."""
        data = {
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "acceptanceDateTime": ["2018-01-15T20:00:00.000Z"],
                },
                "files": [],
            }
        }
        (subs_dir / f"{padded_cik}.json").write_text(json.dumps(data))

    def test_single_qualifying_event(self, tmp_path):
        """End-to-end: one Form 4 with one P+A txn → one EventRecord."""
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()
        padded_cik = "0000036270"
        self._build_subs_file(subs_dir, padded_cik, "0000001234-18-000001")

        # Build a minimal ZIP
        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                    "ISSUERCIK", "ISSUERTRADINGSYMBOL", "REMARKS",
                    "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
                    "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
                    "FORM4_TRANS_REPORTED", "ISSUERNAME"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]

        sub_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000001",
            "FILING_DATE": "15-JAN-2018",
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "0000036270",   # MTB CIK
            "ISSUERTRADINGSYMBOL": "MTB",
            "REMARKS": "",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000001",
            "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "TRANS_SHARES": "200",
            "TRANS_PRICEPERSHARE": "50.00",
        }]
        owner_rows = [{"ACCESSION_NUMBER": "0000001234-18-000001", "RPTOWNERCIK": "0001111111"}]

        tables = {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv(owner_rows, owner_cols),
            "FOOTNOTES.tsv": _make_tsv([], fn_cols),
        }
        zip_path = tmp_path / "2018q1_form345.zip"
        zip_path.write_bytes(_make_zip(tables))

        # Build a tiny universe with MTB
        cik_to_ticker = {36270: "MTB"}

        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir,
            fetch_missing=False,
        )

        assert len(events) == 1
        ev = events[0]
        assert ev.ticker == "MTB"
        assert ev.is_fallback is False
        assert ev.payload["D"] == pytest.approx(10000.0)  # 200 * 50.00
        assert ev.payload["n_txns_qualifying"] == 1
        assert ev.payload["n_10b51_excluded"] == 0
        assert qstats["qualifying_txns_raw"] == 1
        assert qstats["form4_10b51_excluded_txns"] == 0
        assert qstats["submissions_universe_pass"] == 1

    def test_10b51_form_excluded(self, tmp_path):
        """Form with 10b5-1 in REMARKS → all transactions excluded."""
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                    "ISSUERCIK", "ISSUERTRADINGSYMBOL", "REMARKS",
                    "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
                    "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
                    "FORM4_TRANS_REPORTED", "ISSUERNAME"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]

        sub_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000002",
            "FILING_DATE": "15-JAN-2018",
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "0000036270",
            "ISSUERTRADINGSYMBOL": "MTB",
            "REMARKS": "Sale pursuant to 10b5-1 plan",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000002",
            "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "TRANS_SHARES": "200",
            "TRANS_PRICEPERSHARE": "50.00",
        }]
        owner_rows = [{"ACCESSION_NUMBER": "0000001234-18-000002", "RPTOWNERCIK": "0001111111"}]

        tables = {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv(owner_rows, owner_cols),
            "FOOTNOTES.tsv": _make_tsv([], fn_cols),
        }
        zip_path = tmp_path / "2018q1_form345.zip"
        zip_path.write_bytes(_make_zip(tables))

        cik_to_ticker = {36270: "MTB"}
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir,
            fetch_missing=False,
        )

        # All transactions excluded by 10b5-1 → no events
        assert len(events) == 0
        assert qstats["form4_10b51_excluded_txns"] == 1
        assert qstats["qualifying_txns_raw"] == 1

    def test_universe_filter_drops_unknown_cik(self, tmp_path):
        """Filing for CIK not in universe → dropped."""
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                    "ISSUERCIK", "ISSUERTRADINGSYMBOL", "REMARKS",
                    "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
                    "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
                    "FORM4_TRANS_REPORTED", "ISSUERNAME"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]

        sub_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000003",
            "FILING_DATE": "15-JAN-2018",
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "9999999999",  # Not in universe
            "ISSUERTRADINGSYMBOL": "FAKE",
            "REMARKS": "",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000003",
            "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "TRANS_SHARES": "100",
            "TRANS_PRICEPERSHARE": "10.00",
        }]

        tables = {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv([], owner_cols),
            "FOOTNOTES.tsv": _make_tsv([], fn_cols),
        }
        zip_path = tmp_path / "2018q1_form345.zip"
        zip_path.write_bytes(_make_zip(tables))

        cik_to_ticker = {36270: "MTB"}  # 9999999999 not in here
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir,
            fetch_missing=False,
        )
        assert len(events) == 0
        assert qstats["submissions_universe_fail"] == 1
        assert qstats["submissions_universe_pass"] == 0

    def test_missing_price_counts(self, tmp_path):
        """Transaction with missing price → D=0, counted in missing_price_txns."""
        subs_dir = tmp_path / "submissions"
        subs_dir.mkdir()

        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                    "ISSUERCIK", "ISSUERTRADINGSYMBOL", "REMARKS",
                    "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
                    "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
                    "FORM4_TRANS_REPORTED", "ISSUERNAME"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]

        sub_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000004",
            "FILING_DATE": "15-JAN-2018",
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "0000036270",
            "ISSUERTRADINGSYMBOL": "MTB",
            "REMARKS": "",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-000004",
            "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "TRANS_SHARES": "100",
            "TRANS_PRICEPERSHARE": "",  # Missing price
        }]
        owner_rows = [{"ACCESSION_NUMBER": "0000001234-18-000004", "RPTOWNERCIK": "0001111111"}]

        tables = {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv(owner_rows, owner_cols),
            "FOOTNOTES.tsv": _make_tsv([], fn_cols),
        }
        zip_path = tmp_path / "2018q1_form345.zip"
        zip_path.write_bytes(_make_zip(tables))

        cik_to_ticker = {36270: "MTB"}
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir,
            fetch_missing=False,
        )
        assert len(events) == 1
        assert events[0].payload["D"] == 0.0
        assert events[0].payload["missing_price_txns"] == 1
        assert qstats["missing_price_txns"] == 1


# ---------------------------------------------------------------------------
# Test: _dedup_amendments (ADV-01 / fix #1)
# ---------------------------------------------------------------------------

class TestDedupAmendments:
    """Test global amendment dedup (cross-quarter pairs supported)."""

    def _make_event(self, ticker: str, owner_cik: str, period: str,
                    form_type: str, accession: str, event_ts: datetime,
                    is_fallback: bool = False,
                    issuer_cik: str = "36270") -> "EventRecord":
        from research.event_study import EventRecord
        return EventRecord(
            ticker=ticker,
            event_ts=event_ts,
            payload={
                "form_type": form_type,
                "accession": accession,
                "period_of_report": period,
                "owner_cik": owner_cik,
                "issuer_cik": issuer_cik,
                "D": 100.0,
                "k": 1,
                "n_txns_qualifying": 1,
                "n_10b51_excluded": 0,
                "missing_price_txns": 0,
                "owner_ciks": [owner_cik],
                "filing_date": "2018-01-15",
                "acceptance_fallback": is_fallback,
                "acceptance_dt_source": "direct_hit",
                "adt_midnight_utc": False,
            },
            is_fallback=is_fallback,
        )

    def test_dedup_keeps_amendment_over_original(self):
        """4/A with later timestamp should supersede original 4."""
        t0 = datetime(2018, 1, 10, 20, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2018, 1, 15, 20, 0, 0, tzinfo=timezone.utc)
        orig = self._make_event("MTB", "111", "2018-01-05", "4", "ACC-001", t0)
        amend = self._make_event("MTB", "111", "2018-01-05", "4/A", "ACC-002", t1)

        deduped, n_dropped, n_dup4 = _dedup_amendments([orig, amend])
        assert n_dropped == 1
        assert len(deduped) == 1
        assert deduped[0].payload["accession"] == "ACC-002"
        assert deduped[0].payload["form_type"] == "4/A"

    def test_dedup_cross_quarter(self):
        """Amendment in q2 supersedes original in q1 (cross-quarter dedup)."""
        t0 = datetime(2018, 1, 10, 20, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2018, 4, 5, 20, 0, 0, tzinfo=timezone.utc)
        orig = self._make_event("MTB", "111", "2018-01-05", "4", "ACC-Q1", t0)
        amend = self._make_event("MTB", "111", "2018-01-05", "4/A", "ACC-Q2", t1)

        deduped, n_dropped, n_dup4 = _dedup_amendments([orig, amend])
        assert n_dropped == 1
        assert len(deduped) == 1
        assert deduped[0].payload["accession"] == "ACC-Q2"

    def test_dedup_equal_ts_higher_accession_wins(self):
        """Tie on timestamp: higher accession string wins."""
        ts = datetime(2018, 1, 15, 20, 0, 0, tzinfo=timezone.utc)
        e1 = self._make_event("MTB", "111", "2018-01-05", "4", "0001000000-18-000001", ts)
        e2 = self._make_event("MTB", "111", "2018-01-05", "4/A", "0001000000-18-000099", ts)

        deduped, n_dropped, n_dup4 = _dedup_amendments([e1, e2])
        assert n_dropped == 1
        assert deduped[0].payload["accession"] == "0001000000-18-000099"

    def test_dedup_no_amendments_unchanged(self):
        """Two events with different periods: no dedup."""
        t0 = datetime(2018, 1, 10, 20, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2018, 2, 10, 20, 0, 0, tzinfo=timezone.utc)
        e1 = self._make_event("MTB", "111", "2018-01-05", "4", "ACC-001", t0)
        e2 = self._make_event("MTB", "111", "2018-02-05", "4", "ACC-002", t1)

        deduped, n_dropped, n_dup4 = _dedup_amendments([e1, e2])
        assert n_dropped == 0
        assert len(deduped) == 2
        assert n_dup4 == 0

    def test_dedup_plain4_collision_kept_and_counted(self):
        """Same-key all-original '4's: ambiguous class — KEPT, counted, never merged.

        Merging would silently drop real dollars from D (beyond ADV-01's
        reviewed supersession scope); the count surfaces at the F354 gate.
        """
        t0 = datetime(2018, 1, 10, 20, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2018, 1, 11, 20, 0, 0, tzinfo=timezone.utc)
        e1 = self._make_event("MTB", "111", "2018-01-05", "4", "ACC-001", t0)
        e2 = self._make_event("MTB", "111", "2018-01-05", "4", "ACC-002", t1)

        deduped, n_dropped, n_dup4 = _dedup_amendments([e1, e2])
        assert n_dropped == 0
        assert len(deduped) == 2
        assert n_dup4 == 1

    def test_dedup_keys_on_issuer_cik_not_ticker(self):
        """Dedup key is issuer CIK, not ticker (ticker is resolution-dependent).

        (a) Same issuer whose original and amendment resolved to DIFFERENT
        tickers (ADV-02 era-drift) must still dedup. (b) Two DIFFERENT issuers
        sharing an owner and period must NOT dedup even with the same ticker
        string.
        """
        t0 = datetime(2018, 1, 10, 20, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2018, 1, 15, 20, 0, 0, tzinfo=timezone.utc)
        # (a) same issuer, divergent ticker resolution → still dedups
        orig = self._make_event("JSM", "111", "2018-01-05", "4", "ACC-001", t0,
                                issuer_cik="1593538")
        amend = self._make_event("NAVI", "111", "2018-01-05", "4/A", "ACC-002", t1,
                                 issuer_cik="1593538")
        deduped, n_dropped, n_dup4 = _dedup_amendments([orig, amend])
        assert n_dropped == 1
        assert deduped[0].payload["accession"] == "ACC-002"

        # (b) different issuers, same owner+period+ticker-string → no dedup
        e1 = self._make_event("MTB", "111", "2018-01-05", "4", "ACC-003", t0,
                              issuer_cik="36270")
        e2 = self._make_event("MTB", "111", "2018-01-05", "4/A", "ACC-004", t1,
                              issuer_cik="99999")
        deduped, n_dropped, n_dup4 = _dedup_amendments([e1, e2])
        assert n_dropped == 0
        assert len(deduped) == 2


# ---------------------------------------------------------------------------
# Test: ADV-02 ticker resolution fallback
# ---------------------------------------------------------------------------

class TestTickerResolution:
    """ADV-02 fix: ISSUERTRADINGSYMBOL used when it maps back to the same CIK."""

    def _build_subs(self, subs_dir: Path, padded_cik: str, accession: str) -> None:
        data = {
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "acceptanceDateTime": ["2018-01-15T20:00:00.000Z"],
                },
                "files": [],
            }
        }
        (subs_dir / f"{padded_cik}.json").write_text(json.dumps(data))

    def _base_tables(self, accession: str, cik_str: str, tsv_symbol: str,
                     doc_type: str = "4") -> dict:
        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                    "ISSUERCIK", "ISSUERTRADINGSYMBOL", "REMARKS",
                    "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
                    "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
                    "FORM4_TRANS_REPORTED", "ISSUERNAME"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]
        sub_rows = [{
            "ACCESSION_NUMBER": accession, "FILING_DATE": "15-JAN-2018",
            "DOCUMENT_TYPE": doc_type, "ISSUERCIK": cik_str,
            "ISSUERTRADINGSYMBOL": tsv_symbol, "REMARKS": "",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": accession, "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A", "TRANS_SHARES": "100",
            "TRANS_PRICEPERSHARE": "10.00",
        }]
        owner_rows = [{"ACCESSION_NUMBER": accession, "RPTOWNERCIK": "0001111111"}]
        return {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv(owner_rows, owner_cols),
            "FOOTNOTES.tsv": _make_tsv([], fn_cols),
        }

    def test_tsv_symbol_matches_same_cik(self, tmp_path):
        """When TSV symbol resolves to the same CIK, use TSV symbol as ticker."""
        subs_dir = tmp_path / "subs"
        subs_dir.mkdir()
        acc = "0000001234-18-000001"
        cik_str = "0000036270"
        self._build_subs(subs_dir, cik_str, acc)

        tables = self._base_tables(acc, cik_str, "MTB")
        zip_path = tmp_path / "q.zip"
        zip_path.write_bytes(_make_zip(tables))

        # Universe maps cik=36270 → "MTB", same as TSV symbol
        cik_to_ticker = {36270: "MTB"}
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir, fetch_missing=False,
        )
        assert len(events) == 1
        assert events[0].ticker == "MTB"
        assert qstats["n_ticker_fallback"] == 0

    def test_tsv_symbol_wrong_cik_falls_back(self, tmp_path):
        """When TSV symbol maps to a different CIK, fall back to universe ticker."""
        subs_dir = tmp_path / "subs"
        subs_dir.mkdir()
        acc = "0000001234-18-000002"
        cik_str = "0000036270"  # MTB's CIK
        self._build_subs(subs_dir, cik_str, acc)

        # TSV symbol is "JSM" which in universe maps to CIK 99999 (not 36270)
        tables = self._base_tables(acc, cik_str, "JSM")
        zip_path = tmp_path / "q.zip"
        zip_path.write_bytes(_make_zip(tables))

        # Universe: MTB→36270, JSM→99999 (different CIK)
        cik_to_ticker = {36270: "MTB", 99999: "JSM"}
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir, fetch_missing=False,
        )
        assert len(events) == 1
        # Should use universe ticker (MTB) not TSV symbol (JSM)
        assert events[0].ticker == "MTB"
        assert qstats["n_ticker_fallback"] == 1
        assert events[0].payload.get("tsv_symbol") == "JSM"
        assert events[0].payload.get("universe_symbol") == "MTB"

    def test_tsv_symbol_unknown_trusts_filed_symbol(self, tmp_path):
        """ADV-02 Navient case: TSV symbol absent from the universe map entirely.

        The CIK's primary can be a different instrument (Navient's map primary
        JSM is a $25-par note; it filed under NAVI). Trust the issuer's own
        filed symbol — an era-correct symbol with no price frame is excluded
        and counted downstream, which beats silently pricing the wrong
        instrument (returns AND the D/MC market-cap denominator).
        """
        subs_dir = tmp_path / "subs"
        subs_dir.mkdir()
        acc = "0000001234-18-000003"
        cik_str = "0001593538"  # Navient
        self._build_subs(subs_dir, cik_str, acc)

        # TSV symbol "NAVI" is NOT in the universe map; CIK's primary is "JSM"
        tables = self._base_tables(acc, cik_str, "NAVI")
        zip_path = tmp_path / "q.zip"
        zip_path.write_bytes(_make_zip(tables))

        cik_to_ticker = {1593538: "JSM"}
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir, fetch_missing=False,
        )
        assert len(events) == 1
        # Filed symbol wins over the map primary
        assert events[0].ticker == "NAVI"
        assert qstats["n_ticker_fallback"] == 1
        assert events[0].payload.get("tsv_symbol") == "NAVI"
        assert events[0].payload.get("universe_symbol") == "JSM"


# ---------------------------------------------------------------------------
# Test: DI-02 no-timestamp drop
# ---------------------------------------------------------------------------

class TestNoTimestampDrop:
    """DI-02 fix: events with no resolvable timestamp are dropped, not epoch-stamped."""

    def test_no_timestamp_dropped(self, tmp_path):
        """Filing with no subs file AND no parseable FILING_DATE → dropped."""
        subs_dir = tmp_path / "subs"
        subs_dir.mkdir()
        # No submissions JSON for this CIK
        sub_cols = ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE",
                    "ISSUERCIK", "ISSUERTRADINGSYMBOL", "REMARKS",
                    "PERIOD_OF_REPORT", "DATE_OF_ORIG_SUB", "NO_SECURITIES_OWNED",
                    "NOT_SUBJECT_SEC16", "FORM3_HOLDINGS_REPORTED",
                    "FORM4_TRANS_REPORTED", "ISSUERNAME"]
        nd_cols = ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE",
                   "SECURITY_TITLE_FN", "TRANS_DATE", "TRANS_DATE_FN",
                   "DEEMED_EXECUTION_DATE", "DEEMED_EXECUTION_DATE_FN",
                   "TRANS_FORM_TYPE", "TRANS_CODE", "EQUITY_SWAP_INVOLVED",
                   "EQUITY_SWAP_TRANS_CD_FN", "TRANS_TIMELINESS",
                   "TRANS_TIMELINESS_FN", "TRANS_SHARES", "TRANS_SHARES_FN",
                   "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                   "TRANS_ACQUIRED_DISP_CD", "TRANS_ACQUIRED_DISP_CD_FN",
                   "SHRS_OWND_FOLWNG_TRANS", "SHRS_OWND_FOLWNG_TRANS_FN",
                   "VALU_OWND_FOLWNG_TRANS", "VALU_OWND_FOLWNG_TRANS_FN",
                   "DIRECT_INDIRECT_OWNERSHIP", "DIRECT_INDIRECT_OWNERSHIP_FN",
                   "NATURE_OF_OWNERSHIP", "NATURE_OF_OWNERSHIP_FN"]
        owner_cols = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                      "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT",
                      "RPTOWNER_STREET1", "RPTOWNER_STREET2", "RPTOWNER_CITY",
                      "RPTOWNER_STATE", "RPTOWNER_ZIPCODE", "RPTOWNER_STATE_DESC",
                      "FILE_NUMBER"]
        fn_cols = ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"]

        sub_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-NODTS",
            "FILING_DATE": "",  # empty — no fallback possible
            "DOCUMENT_TYPE": "4",
            "ISSUERCIK": "0000036270",
            "ISSUERTRADINGSYMBOL": "MTB",
            "REMARKS": "",
        }]
        nd_rows = [{
            "ACCESSION_NUMBER": "0000001234-18-NODTS",
            "TRANS_CODE": "P",
            "TRANS_ACQUIRED_DISP_CD": "A",
            "TRANS_SHARES": "100",
            "TRANS_PRICEPERSHARE": "10.00",
        }]
        owner_rows = [{"ACCESSION_NUMBER": "0000001234-18-NODTS",
                       "RPTOWNERCIK": "0001111111"}]

        tables = {
            "SUBMISSION.tsv": _make_tsv(sub_rows, sub_cols),
            "NONDERIV_TRANS.tsv": _make_tsv(nd_rows, nd_cols),
            "REPORTINGOWNER.tsv": _make_tsv(owner_rows, owner_cols),
            "FOOTNOTES.tsv": _make_tsv([], fn_cols),
        }
        zip_path = tmp_path / "q.zip"
        zip_path.write_bytes(_make_zip(tables))

        cik_to_ticker = {36270: "MTB"}
        events, qstats = _process_quarter(
            zip_path, "2018q1", cik_to_ticker,
            subs_dir=subs_dir, fetch_missing=False,
        )
        # Event should be dropped (no resolvable timestamp)
        assert len(events) == 0
        assert qstats["n_no_timestamp_dropped"] == 1
        # No epoch events should exist
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        for ev in events:
            assert ev.event_ts != epoch


# ---------------------------------------------------------------------------
# Test: DI-01 non-JSON fetch body rejected without disk write
# ---------------------------------------------------------------------------

class TestCachePoisonRejection:
    """DI-01 fix: non-JSON body must NOT be written to the cache file."""

    def test_non_json_body_not_written(self, tmp_path):
        """If the on-disk cache file contains non-JSON (simulating a poisoned
        cache from a pre-fix write), _fetch_older_page returns None and does
        NOT overwrite (stale file stays, returns None via except path)."""
        cache_dir = tmp_path / "older_pages"
        cache_dir.mkdir()
        name = "CIK0000036270-submissions-001.json"
        cache_path = cache_dir / name

        # Simulate a pre-existing poisoned file
        cache_path.write_text("<html>maintenance</html>", encoding="utf-8")
        mtime_before = cache_path.stat().st_mtime

        result = _fetch_older_page(name, cache_dir)
        # Non-JSON cached content → returns None
        assert result is None
        # Cache file should NOT have been overwritten (mtime unchanged)
        assert cache_path.stat().st_mtime == mtime_before

    def test_valid_json_cache_returned(self, tmp_path):
        """Valid cached JSON is returned without network fetch."""
        cache_dir = tmp_path / "older_pages"
        cache_dir.mkdir()
        name = "CIK0000036270-submissions-001.json"
        cache_path = cache_dir / name
        payload = {"accessionNumber": ["ACC-001"], "acceptanceDateTime": ["2018-01-15T20:00:00.000Z"]}
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _fetch_older_page(name, cache_dir)
        assert result is not None
        assert result["accessionNumber"] == ["ACC-001"]
