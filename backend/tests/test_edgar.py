"""Tests for backend/edgar.py.

All network is mocked by monkeypatching edgar._get — never httpx directly (D5).
Caches are redirected to tmp_path.
"""

from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_response(data: dict | list) -> MagicMock:
    """Create a fake httpx.Response-like mock."""
    mock = MagicMock()
    mock.json.return_value = data
    mock.text = json.dumps(data)
    mock.raise_for_status.return_value = None
    return mock


def _seed_cache(path: Path, data: dict | list) -> None:
    """Write data to a cache file with a fresh mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _expire_cache(path: Path) -> None:
    """Set mtime of cache file to 10 days ago so it's treated as expired."""
    import os
    old_time = time.time() - 10 * 86400
    os.utime(path, (old_time, old_time))


# ---------------------------------------------------------------------------
# test_fetch_companyfacts_uses_cache
# ---------------------------------------------------------------------------


def test_fetch_companyfacts_uses_cache(monkeypatch, tmp_path):
    """Second call returns cached file, no HTTP."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    fake_data = {"facts": {"us-gaap": {}}}
    _seed_cache(tmp_path / "facts" / "0000123456.json", fake_data)

    call_count = {"n": 0}

    def fake_get(url, params=None):
        call_count["n"] += 1
        raise AssertionError("should not call HTTP — cache should be valid")

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.fetch_companyfacts("123456")
    assert result == fake_data
    assert call_count["n"] == 0


# ---------------------------------------------------------------------------
# test_fetch_companyfacts_cache_expired
# ---------------------------------------------------------------------------


def test_fetch_companyfacts_cache_expired(monkeypatch, tmp_path):
    """Expired cache triggers fresh HTTP call."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    stale_data = {"facts": {"us-gaap": {"stale": True}}}
    cache_path = tmp_path / "facts" / "0000123456.json"
    _seed_cache(cache_path, stale_data)
    _expire_cache(cache_path)

    fresh_data = {"facts": {"us-gaap": {"fresh": True}}}

    call_count = {"n": 0}

    def fake_get(url, params=None):
        call_count["n"] += 1
        return _make_fake_response(fresh_data)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.fetch_companyfacts("123456")
    assert result == fresh_data
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# test_get_quarterly_revenue_tries_tag_fallback
# ---------------------------------------------------------------------------


def test_get_quarterly_revenue_tries_tag_fallback(monkeypatch, tmp_path):
    """When Revenues is absent, falls back to RevenueFromContractWithCustomerExcludingAssessedTax."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    # Build facts without primary 'Revenues' tag, only the fallback tag
    fallback_entries = [
        {"end": "2023-03-31", "val": 1_000_000, "accn": "a1", "fy": 2023,
         "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},
        {"end": "2023-06-30", "val": 1_100_000, "accn": "a2", "fy": 2023,
         "fp": "Q2", "form": "10-Q", "filed": "2023-08-01"},
    ]
    fake_facts = {
        "facts": {
            "us-gaap": {
                # Revenues is absent
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {"USD": fallback_entries}
                }
            }
        }
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    assert len(result) == 2
    assert result[0]["end"] == "2023-03-31"
    assert result[0]["val"] == 1_000_000.0
    assert result[1]["end"] == "2023-06-30"


# ---------------------------------------------------------------------------
# test_get_quarterly_revenue_deduplicates_amendments
# ---------------------------------------------------------------------------


def test_get_quarterly_revenue_deduplicates_amendments(monkeypatch, tmp_path):
    """Same (end, filed) pair yields one entry even if multiple filings."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    # Two entries with same end+filed (amendment)
    duplicate_entries = [
        {"end": "2023-03-31", "val": 1_000_000, "accn": "a1", "fy": 2023,
         "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},
        {"end": "2023-03-31", "val": 1_050_000, "accn": "a2", "fy": 2023,
         "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},  # same end+filed
        {"end": "2023-06-30", "val": 1_100_000, "accn": "a3", "fy": 2023,
         "fp": "Q2", "form": "10-Q", "filed": "2023-08-01"},
    ]
    fake_facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": duplicate_entries}}
            }
        }
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    # Should be deduplicated: only one Q1 entry
    ends = [e["end"] for e in result]
    assert ends.count("2023-03-31") == 1
    assert len(result) == 2


# ---------------------------------------------------------------------------
# test_get_quarterly_revenue_excludes_fy
# ---------------------------------------------------------------------------


def test_get_quarterly_revenue_excludes_fy(monkeypatch, tmp_path):
    """fp == 'FY' entries are excluded from quarterly series."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    entries = [
        {"end": "2022-12-31", "val": 4_000_000, "accn": "a0", "fy": 2022,
         "fp": "FY", "form": "10-K", "filed": "2023-02-15"},  # annual — excluded
        {"end": "2023-03-31", "val": 1_000_000, "accn": "a1", "fy": 2023,
         "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},
    ]
    fake_facts = {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    assert len(result) == 1
    assert result[0]["end"] == "2023-03-31"


# ---------------------------------------------------------------------------
# test_get_shares_outstanding_point_in_time
# ---------------------------------------------------------------------------


def test_get_shares_outstanding_point_in_time(monkeypatch, tmp_path):
    """Returns most-recent filed on or before as_of, NOT entries after as_of."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    entries = [
        {"end": "2023-03-31", "val": 100_000_000, "filed": "2023-05-01", "form": "10-Q"},
        {"end": "2023-06-30", "val": 105_000_000, "filed": "2023-08-01", "form": "10-Q"},
        {"end": "2023-09-30", "val": 110_000_000, "filed": "2023-11-01", "form": "10-Q"},
    ]
    fake_facts = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {"units": {"shares": entries}}
            }
        }
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    # as_of = 2023-09-01 — only the first two entries qualify (filed <= 2023-09-01)
    as_of = date(2023, 9, 1)
    result = edgar.get_shares_outstanding("0000123456", as_of)
    assert result == 105_000_000.0  # most recent filed before as_of

    # as_of = 2023-12-31 — all three qualify, most recent is 110M
    as_of_later = date(2023, 12, 31)
    result_later = edgar.get_shares_outstanding("0000123456", as_of_later)
    assert result_later == 110_000_000.0

    # as_of before any entries → None
    as_of_early = date(2023, 4, 1)
    result_early = edgar.get_shares_outstanding("0000123456", as_of_early)
    assert result_early is None


# ---------------------------------------------------------------------------
# test_get_shares_outstanding_missing_data
# ---------------------------------------------------------------------------


def test_get_shares_outstanding_missing_data(monkeypatch, tmp_path):
    """Returns None when CommonStockSharesOutstanding tag is absent."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    fake_facts = {"facts": {"us-gaap": {}}}  # tag absent

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_shares_outstanding("0000123456", date.today())
    assert result is None


# ---------------------------------------------------------------------------
# test_get_form4_net_buys_counts_correctly
# ---------------------------------------------------------------------------

_FORM4_XML_BUYS = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTransaction>
    <transactionCode>P</transactionCode>
    <transactionAmounts>
      <transactionShares><value>1000</value></transactionShares>
      <transactionPricePerShare><value>10.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
    <transactionCode>P</transactionCode>
    <transactionAmounts>
      <transactionShares><value>500</value></transactionShares>
      <transactionPricePerShare><value>10.50</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

_FORM4_XML_SELLS = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTransaction>
    <transactionCode>S</transactionCode>
    <transactionAmounts>
      <transactionShares><value>200</value></transactionShares>
      <transactionPricePerShare><value>12.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

_FORM4_XML_MIXED = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTransaction>
    <transactionCode>P</transactionCode>
    <transactionAmounts>
      <transactionShares><value>300</value></transactionShares>
      <transactionPricePerShare><value>15.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
    <transactionCode>S</transactionCode>
    <transactionAmounts>
      <transactionShares><value>100</value></transactionShares>
      <transactionPricePerShare><value>15.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>
"""


def test_get_form4_net_buys_counts_correctly(monkeypatch, tmp_path):
    """Net dollar buy/sell correctly summed from mock Form 4 XML."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "form4").mkdir(parents=True, exist_ok=True)
    (tmp_path / "submissions").mkdir(parents=True, exist_ok=True)

    cik = "0000123456"
    today = date.today()
    recent_date = (today - timedelta(days=30)).isoformat()

    # Mock submissions with 2 Form 4 filings within the window
    fake_subs = {
        "filings": {
            "recent": {
                "form": ["4", "4"],
                "accessionNumber": ["0001234567-23-000001", "0001234567-23-000002"],
                "filingDate": [recent_date, recent_date],
            }
        }
    }

    xml_map = {
        "0001234567-23-000001": _FORM4_XML_BUYS,   # 1000×10 + 500×10.5 = 15250 net buy
        "0001234567-23-000002": _FORM4_XML_SELLS,  # 200×12 = 2400 net sell
    }

    def fake_get(url, params=None):
        # Return submissions JSON
        if "submissions" in url:
            return _make_fake_response(fake_subs)
        # Return filing index JSON for each accession
        for accession_nodash, xml in [
            ("000123456723000001", _FORM4_XML_BUYS),
            ("000123456723000002", _FORM4_XML_SELLS),
        ]:
            if accession_nodash in url and "index.json" in url:
                return _make_fake_response({
                    "documents": [{"document": "form4.xml", "type": "4"}]
                })
            if accession_nodash in url and url.endswith(".xml"):
                mock = MagicMock()
                mock.text = xml
                mock.raise_for_status.return_value = None
                return mock
        return _make_fake_response({})

    monkeypatch.setattr(edgar, "_get", fake_get)

    # Inject XML directly into cache to bypass filing index dance
    def fake_fetch_form4_xml(cik_arg, accession):
        return xml_map.get(accession, "")

    monkeypatch.setattr(edgar, "fetch_form4_xml", fake_fetch_form4_xml)

    # 1000×10 + 500×10.5 - 200×12 = 10000 + 5250 - 2400 = 12850
    result = edgar.get_form4_net_buys(cik, months_back=6)
    assert result == 12850


def test_get_form4_net_buys_net_sell(monkeypatch, tmp_path):
    """Net sell: returns negative value when sells outweigh buys."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    today = date.today()
    recent_date = (today - timedelta(days=10)).isoformat()

    fake_subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0001234567-23-000001"],
                "filingDate": [recent_date],
            }
        }
    }

    _SELL_HEAVY = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTransaction>
    <transactionCode>S</transactionCode>
    <transactionAmounts>
      <transactionShares><value>5000</value></transactionShares>
      <transactionPricePerShare><value>20.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

    def fake_get(url, params=None):
        if "submissions" in url:
            return _make_fake_response(fake_subs)
        return _make_fake_response({})

    monkeypatch.setattr(edgar, "_get", fake_get)
    monkeypatch.setattr(edgar, "fetch_form4_xml", lambda cik_arg, acc: _SELL_HEAVY)

    result = edgar.get_form4_net_buys("0000123456", months_back=6)
    assert result == -100_000  # 5000 × 20 = 100000, negative because sell


# ---------------------------------------------------------------------------
# test_has_buyback_authorization_true
# ---------------------------------------------------------------------------


def test_has_buyback_authorization_true(monkeypatch, tmp_path):
    """True when EFTS returns matching 8-K filings.

    COR-04 / F321 Fix 3: fixture uses real EFTS field names (adsh, root_forms)
    so the test validates F321's field-name fix, not just the hit count.
    Old field names (accession_no, form_type) would silently produce empty strings.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    fake_efts = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "adsh": "0001234567-23-000099",
                        "file_date": "2023-06-15",
                        "root_forms": ["8-K"],
                    }
                }
            ]
        }
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_efts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    filings = edgar.search_buyback_8k("0000123456", months_back=12)
    result = edgar.has_buyback_authorization("0000123456", months_back=12)
    assert result is True
    # COR-04: verify the field-name fix actually worked — accessionNo must be non-empty
    assert len(filings) == 1
    assert filings[0]["accessionNo"] != "", "accessionNo is empty — adsh field not parsed (F321 regression)"


# ---------------------------------------------------------------------------
# test_has_buyback_authorization_false
# ---------------------------------------------------------------------------


def test_has_buyback_authorization_false(monkeypatch, tmp_path):
    """False when EFTS response has no hits."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    fake_efts = {"hits": {"hits": []}}

    def fake_get(url, params=None):
        return _make_fake_response(fake_efts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.has_buyback_authorization("0000123456", months_back=12)
    assert result is False


# ---------------------------------------------------------------------------
# test_rate_limiter_throttles_to_10_per_sec
# ---------------------------------------------------------------------------


def test_rate_limiter_throttles_to_10_per_sec(monkeypatch, tmp_path):
    """Rate limiter: rapid call sleeps at least (0.1 - elapsed) seconds."""
    import edgar

    sleep_calls = []

    def fake_sleep(secs):
        sleep_calls.append(secs)
        # Don't actually sleep in tests

    monkeypatch.setattr(time, "sleep", fake_sleep)

    # Simulate the rate limit being saturated: set _last_req_time to now
    # so the next call sees <0.1s elapsed and must sleep.
    def fake_httpx_get(url, **kwargs):
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        return mock

    monkeypatch.setattr(edgar._http_client, "get", fake_httpx_get)

    # Set _last_req_time to just now so the next call must sleep.
    edgar._last_req_time = time.monotonic()
    edgar._get("https://example.com/test1")

    # sleep should have been called since _last_req_time was "now"
    assert any(s > 0 for s in sleep_calls), (
        f"Expected at least one positive sleep call, got: {sleep_calls}"
    )


# ---------------------------------------------------------------------------
# test_form4_xml_parse_buy
# ---------------------------------------------------------------------------


def test_form4_xml_parse_buy():
    """_parse_form4_transactions correctly parses Purchase (P) transactions."""
    import edgar

    txns = edgar._parse_form4_transactions(_FORM4_XML_BUYS)
    assert len(txns) == 2
    assert txns[0]["transactionCode"] == "P"
    assert txns[0]["shares"] == 1000.0
    assert txns[0]["price"] == 10.0
    assert txns[1]["transactionCode"] == "P"
    assert txns[1]["shares"] == 500.0
    assert txns[1]["price"] == 10.5


# ---------------------------------------------------------------------------
# test_form4_xml_parse_sell
# ---------------------------------------------------------------------------


def test_form4_xml_parse_sell():
    """_parse_form4_transactions correctly parses Sale (S) transactions."""
    import edgar

    txns = edgar._parse_form4_transactions(_FORM4_XML_SELLS)
    assert len(txns) == 1
    assert txns[0]["transactionCode"] == "S"
    assert txns[0]["shares"] == 200.0
    assert txns[0]["price"] == 12.0


# ---------------------------------------------------------------------------
# test_form4_xml_parse_mixed
# ---------------------------------------------------------------------------


def test_form4_xml_parse_mixed():
    """_parse_form4_transactions handles mixed P and S in the same document."""
    import edgar

    txns = edgar._parse_form4_transactions(_FORM4_XML_MIXED)
    codes = [t["transactionCode"] for t in txns]
    assert "P" in codes
    assert "S" in codes
    # Net dollar: 300×15 - 100×15 = 4500 - 1500 = 3000
    net = sum(
        t["shares"] * t["price"] if t["transactionCode"] == "P"
        else -(t["shares"] * t["price"])
        for t in txns
    )
    assert net == pytest.approx(3000.0)


# ---------------------------------------------------------------------------
# test_form4_xml_parse_empty
# ---------------------------------------------------------------------------


def test_form4_xml_parse_empty():
    """_parse_form4_transactions returns [] on empty string."""
    import edgar

    result = edgar._parse_form4_transactions("")
    assert result == []


# ---------------------------------------------------------------------------
# test_form4_xml_parse_invalid
# ---------------------------------------------------------------------------


def test_form4_xml_parse_invalid():
    """_parse_form4_transactions returns [] on malformed XML."""
    import edgar

    result = edgar._parse_form4_transactions("<bad xml>>>not valid<<")
    assert result == []


# ---------------------------------------------------------------------------
# test_cik_padding
# ---------------------------------------------------------------------------


def test_cik_padding():
    """_pad_cik zero-pads to 10 digits (D9)."""
    import edgar

    assert edgar._pad_cik("123456") == "0000123456"
    assert edgar._pad_cik(320193) == "0000320193"
    assert edgar._pad_cik("0000320193") == "0000320193"
    assert len(edgar._pad_cik("1")) == 10


# ---------------------------------------------------------------------------
# test_fetch_universe_normalizes_ciks
# ---------------------------------------------------------------------------


def test_fetch_universe_normalizes_ciks(monkeypatch, tmp_path):
    """fetch_universe() returns CIKs zero-padded to 10 digits."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    fake_raw = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_raw)

    monkeypatch.setattr(edgar, "_get", fake_get)

    universe = edgar.fetch_universe()
    assert universe["AAPL"]["cik_str"] == "0000320193"
    assert universe["MSFT"]["cik_str"] == "0000789019"
    assert len(universe["AAPL"]["cik_str"]) == 10


# ---------------------------------------------------------------------------
# test_get_quarterly_revenue_returns_empty_on_missing
# ---------------------------------------------------------------------------


def test_get_quarterly_revenue_returns_empty_on_missing(monkeypatch, tmp_path):
    """Returns [] when none of the revenue tags are present."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    fake_facts = {"facts": {"us-gaap": {}}}

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    assert result == []


# ---------------------------------------------------------------------------
# test_get_quarterly_series_oldest_first
# ---------------------------------------------------------------------------


def test_get_quarterly_series_oldest_first(monkeypatch, tmp_path):
    """Quarterly series is sorted oldest-first by end date."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    entries = [
        {"end": "2023-09-30", "val": 1_200_000, "accn": "a3", "fy": 2023,
         "fp": "Q3", "form": "10-Q", "filed": "2023-11-01"},
        {"end": "2023-03-31", "val": 1_000_000, "accn": "a1", "fy": 2023,
         "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},
        {"end": "2023-06-30", "val": 1_100_000, "accn": "a2", "fy": 2023,
         "fp": "Q2", "form": "10-Q", "filed": "2023-08-01"},
    ]
    fake_facts = {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    ends = [e["end"] for e in result]
    assert ends == sorted(ends)


# ---------------------------------------------------------------------------
# test_search_buyback_8k_uses_cache
# ---------------------------------------------------------------------------


def test_search_buyback_8k_uses_cache(monkeypatch, tmp_path):
    """search_buyback_8k returns cached list without HTTP call on second access."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    cached_result = [
        {"accessionNo": "0001234567-23-000099", "filedAt": "2023-06-15", "formType": "8-K"}
    ]
    # Cache key now includes as_of date (today when as_of=None).
    as_of = date(2023, 6, 30)
    cache_path = tmp_path / "efts" / f"0000123456_12_{as_of.isoformat()}.json"
    _seed_cache(cache_path, cached_result)

    call_count = {"n": 0}

    def fake_get(url, params=None):
        call_count["n"] += 1
        raise AssertionError("should not call HTTP — cache should be valid")

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.search_buyback_8k("123456", months_back=12, as_of=as_of)
    assert result == cached_result
    assert call_count["n"] == 0


# ---------------------------------------------------------------------------
# test_get_form4_net_buys_no_filings
# ---------------------------------------------------------------------------


def test_get_form4_net_buys_no_filings(monkeypatch, tmp_path):
    """Returns 0 when submissions has no Form 4 filings."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    fake_subs = {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K"],
                "accessionNumber": ["0001234567-23-000001", "0001234567-23-000002"],
                "filingDate": ["2023-05-01", "2023-02-15"],
            }
        }
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_subs)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_form4_net_buys("0000123456", months_back=6)
    assert result == 0


# ---------------------------------------------------------------------------
# test_get_form4_net_buys_outside_window
# ---------------------------------------------------------------------------


def test_get_form4_net_buys_outside_window(monkeypatch, tmp_path):
    """Form 4 filings outside the months_back window are excluded."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    old_date = (date.today() - timedelta(days=200)).isoformat()  # > 6 months ago

    fake_subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0001234567-22-000001"],
                "filingDate": [old_date],
            }
        }
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_subs)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_form4_net_buys("0000123456", months_back=6)
    assert result == 0


# ---------------------------------------------------------------------------
# test_q4_derivation_from_annual
# ---------------------------------------------------------------------------


def test_q4_derivation_from_annual(monkeypatch, tmp_path):
    """Q4 is derived as FY - (Q1+Q2+Q3) when not directly filed as quarterly."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    # FY entry + Q1/Q2/Q3 but no Q4 entry
    entries = [
        {"end": "2022-03-31", "val": 1_000_000, "accn": "a1", "fy": 2022,
         "fp": "Q1", "form": "10-Q", "filed": "2022-05-01"},
        {"end": "2022-06-30", "val": 1_100_000, "accn": "a2", "fy": 2022,
         "fp": "Q2", "form": "10-Q", "filed": "2022-08-01"},
        {"end": "2022-09-30", "val": 1_050_000, "accn": "a3", "fy": 2022,
         "fp": "Q3", "form": "10-Q", "filed": "2022-11-01"},
        # Annual FY: total = 4,500,000 → Q4 = 4,500,000 - 3,150,000 = 1,350,000
        {"end": "2022-12-31", "val": 4_500_000, "accn": "a4", "fy": 2022,
         "fp": "FY", "form": "10-K", "filed": "2023-02-15"},
    ]
    fake_facts = {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    # Should have Q1, Q2, Q3 + derived Q4
    ends = [e["end"] for e in result]
    assert "2022-12-31" in ends  # derived Q4 present
    q4 = next(e for e in result if e["end"] == "2022-12-31")
    assert q4["val"] == pytest.approx(1_350_000.0)


# ---------------------------------------------------------------------------
# test_get_form4_net_buys_as_of_window
# ---------------------------------------------------------------------------


def test_get_form4_net_buys_as_of_window(monkeypatch, tmp_path):
    """get_form4_net_buys respects as_of: filings after as_of are excluded."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    as_of = date(2023, 6, 1)
    recent_before = (as_of - timedelta(days=30)).isoformat()
    recent_after = (as_of + timedelta(days=10)).isoformat()  # after as_of — must be excluded

    fake_subs = {
        "filings": {
            "recent": {
                "form": ["4", "4"],
                "accessionNumber": ["0001234567-23-000001", "0001234567-23-000002"],
                "filingDate": [recent_before, recent_after],
            }
        }
    }

    _BUY_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTransaction>
    <transactionCode>P</transactionCode>
    <transactionAmounts>
      <transactionShares><value>1000</value></transactionShares>
      <transactionPricePerShare><value>10.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

    xml_map = {
        "0001234567-23-000001": _BUY_XML,
        "0001234567-23-000002": _BUY_XML,  # should be excluded
    }

    def fake_get(url, params=None):
        if "submissions" in url:
            return _make_fake_response(fake_subs)
        return _make_fake_response({})

    monkeypatch.setattr(edgar, "_get", fake_get)
    monkeypatch.setattr(edgar, "fetch_form4_xml", lambda cik_arg, acc: xml_map.get(acc, ""))

    result = edgar.get_form4_net_buys("0000123456", months_back=6, as_of=as_of)
    # Only the filing before as_of counts: 1000 × 10 = 10000
    assert result == pytest.approx(10_000.0)


# ---------------------------------------------------------------------------
# test_get_form4_net_buys_as_of_none_uses_today
# ---------------------------------------------------------------------------


def test_get_form4_net_buys_as_of_none_uses_today(monkeypatch, tmp_path):
    """as_of=None uses today, so no future filings are excluded (live path)."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    today = date.today()
    recent_date = (today - timedelta(days=10)).isoformat()

    fake_subs = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0001234567-23-000001"],
                "filingDate": [recent_date],
            }
        }
    }

    _BUY_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <nonDerivativeTransaction>
    <transactionCode>P</transactionCode>
    <transactionAmounts>
      <transactionShares><value>500</value></transactionShares>
      <transactionPricePerShare><value>20.00</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>
"""

    def fake_get(url, params=None):
        if "submissions" in url:
            return _make_fake_response(fake_subs)
        return _make_fake_response({})

    monkeypatch.setattr(edgar, "_get", fake_get)
    monkeypatch.setattr(edgar, "fetch_form4_xml", lambda cik_arg, acc: _BUY_XML)

    result = edgar.get_form4_net_buys("0000123456", months_back=6, as_of=None)
    assert result == pytest.approx(10_000.0)  # 500 × 20


# ---------------------------------------------------------------------------
# test_search_buyback_8k_as_of_window
# ---------------------------------------------------------------------------


def test_search_buyback_8k_as_of_window(monkeypatch, tmp_path):
    """search_buyback_8k uses as_of as end_dt and includes it in the cache key."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    as_of = date(2023, 3, 1)
    fake_efts = {"hits": {"hits": []}}

    captured_params: list[dict] = []

    def fake_get(url, params=None):
        if params:
            captured_params.append(dict(params))
        return _make_fake_response(fake_efts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    edgar.search_buyback_8k("0000123456", months_back=6, as_of=as_of)

    assert len(captured_params) == 1
    # enddt should equal as_of, not today
    assert captured_params[0]["enddt"] == as_of.isoformat()
    # Cache key should include as_of date
    cache_files = list((tmp_path / "efts").iterdir())
    assert any(as_of.isoformat() in f.name for f in cache_files)


# ---------------------------------------------------------------------------
# test_corrupt_cache_recovery
# ---------------------------------------------------------------------------


def test_corrupt_cache_recovery(monkeypatch, tmp_path):
    """Corrupt JSON cache is deleted and re-fetched from network (DI-04/REL-08)."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    # Write corrupt JSON to the cache file
    cache_path = tmp_path / "facts" / "0000123456.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("{corrupt json!!!", encoding="utf-8")
    # Make it look fresh (recent mtime)
    import os
    os.utime(cache_path, None)

    fresh_data = {"facts": {"us-gaap": {"fresh": True}}}
    call_count = {"n": 0}

    def fake_get(url, params=None):
        call_count["n"] += 1
        return _make_fake_response(fresh_data)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.fetch_companyfacts("123456")
    assert result == fresh_data
    assert call_count["n"] == 1
    # Corrupt file should have been replaced with fresh data
    assert cache_path.exists()


# ---------------------------------------------------------------------------
# test_retry_on_429
# ---------------------------------------------------------------------------


def test_retry_on_429(monkeypatch, tmp_path):
    """_get() retries on HTTP 429 with backoff and ultimately succeeds."""
    import edgar

    sleep_calls: list[float] = []

    def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    call_count = {"n": 0}

    def fake_httpx_get(url, **kwargs):
        call_count["n"] += 1
        mock = MagicMock()
        if call_count["n"] == 1:
            # First attempt: 429 — retry logic checks status_code before raise_for_status
            mock.status_code = 429
            mock.raise_for_status.return_value = None  # won't be called before retry
        else:
            mock.status_code = 200
            mock.json.return_value = {"ok": True}
            mock.raise_for_status.return_value = None
        return mock

    # Patch the client's get method used by edgar._http_client
    monkeypatch.setattr(edgar._http_client, "get", fake_httpx_get)

    # Reset rate limiter so no rate-limit sleep occurs
    edgar._last_req_time = 0.0

    resp = edgar._get("https://example.com/test")
    assert resp.json() == {"ok": True}
    assert call_count["n"] == 2
    # First retry sleep should be 1s
    assert any(s == 1 for s in sleep_calls)


# ---------------------------------------------------------------------------
# test_q4_dedupe_q1_q3_amendments (COR-01)
# ---------------------------------------------------------------------------


def test_q4_dedupe_q1_q3_amendments(monkeypatch, tmp_path):
    """_derive_q4_from_annual dedupes Q1-Q3 by (fy, fp), keeping latest filed."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    # Q1 filed twice (amendment), Q2 and Q3 once each
    entries = [
        # Q1 original
        {"end": "2022-03-31", "val": 1_000_000, "accn": "a1", "fy": 2022,
         "fp": "Q1", "form": "10-Q", "filed": "2022-05-01"},
        # Q1 amendment (later filed, higher val — should be used)
        {"end": "2022-03-31", "val": 1_200_000, "accn": "a1a", "fy": 2022,
         "fp": "Q1", "form": "10-Q", "filed": "2022-05-15"},
        {"end": "2022-06-30", "val": 1_100_000, "accn": "a2", "fy": 2022,
         "fp": "Q2", "form": "10-Q", "filed": "2022-08-01"},
        {"end": "2022-09-30", "val": 1_050_000, "accn": "a3", "fy": 2022,
         "fp": "Q3", "form": "10-Q", "filed": "2022-11-01"},
        # FY: 4_500_000. With dedupe: Q1=1_200_000, Q2=1_100_000, Q3=1_050_000
        # sum=3_350_000 → Q4 = 1_150_000
        {"end": "2022-12-31", "val": 4_500_000, "accn": "a4", "fy": 2022,
         "fp": "FY", "form": "10-K", "filed": "2023-02-15"},
    ]
    fake_facts = {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}
    }

    def fake_get(url, params=None):
        return _make_fake_response(fake_facts)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.get_quarterly_revenue("0000123456")
    ends = [e["end"] for e in result]
    assert "2022-12-31" in ends
    q4 = next(e for e in result if e["end"] == "2022-12-31")
    # With dedupe: 4_500_000 - (1_200_000 + 1_100_000 + 1_050_000) = 1_150_000
    assert q4["val"] == pytest.approx(1_150_000.0)


# ---------------------------------------------------------------------------
# test_rate_limiter_sleep_outside_lock (REL-04)
# ---------------------------------------------------------------------------


def test_rate_limiter_sleep_outside_lock(monkeypatch):
    """Rate limiter: _last_req_time is updated under the lock; sleep happens outside."""
    import edgar
    import threading

    sleep_calls: list[float] = []
    lock_held_during_sleep: list[bool] = []

    original_sleep = time.sleep

    def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)
        # Check if the rate lock is held during sleep — it should NOT be
        lock_held_during_sleep.append(edgar._rate_lock.locked())

    monkeypatch.setattr(time, "sleep", fake_sleep)

    call_count = {"n": 0}

    def fake_httpx_get(url, **kwargs):
        call_count["n"] += 1
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        return mock

    monkeypatch.setattr(edgar._http_client, "get", fake_httpx_get)

    # Force a rate-limit sleep by setting _last_req_time to just now
    edgar._last_req_time = time.monotonic()
    edgar._get("https://example.com/test")

    if sleep_calls:
        # If sleep was called, the lock must NOT have been held during it
        assert all(not held for held in lock_held_during_sleep), (
            "Rate-limit sleep was called while holding the lock"
        )


# ---------------------------------------------------------------------------
# F321 — Positive-control fixture tests (recorded 2026-06-05 from live EFTS)
# ---------------------------------------------------------------------------

# Path to recorded fixtures
_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_f321_efts_request_params(monkeypatch, tmp_path):
    """F321 Fix 1+2: EFTS request sends zero-padded ciks string + dateRange=custom.

    Before fix: ciks was sent as bare int (silent 0 hits); dateRange was missing (HTTP 500).
    After fix: ciks=0000320193 (string), dateRange=custom present.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    captured_params: list[dict] = []
    captured_urls: list[str] = []

    def fake_get(url, params=None):
        captured_urls.append(url)
        if params:
            captured_params.append(dict(params))
        return _make_fake_response({"hits": {"hits": []}})

    monkeypatch.setattr(edgar, "_get", fake_get)

    edgar.search_buyback_8k("320193", months_back=12)

    assert len(captured_params) == 1
    p = captured_params[0]
    # Fix 1: ciks must be the zero-padded 10-digit string, NOT a bare int
    assert p["ciks"] == "0000320193", f"ciks was {p['ciks']!r}, expected '0000320193'"
    assert isinstance(p["ciks"], str), "ciks must be a string"
    # Fix 2: dateRange=custom is required
    assert p.get("dateRange") == "custom", f"dateRange was {p.get('dateRange')!r}, expected 'custom'"
    # Other required params still present
    assert "startdt" in p
    assert "enddt" in p
    assert p["forms"] == "8-K"
    # TST-04: assert the URL endpoint itself (a wrong base URL would pass all param checks)
    assert len(captured_urls) == 1
    assert captured_urls[0].startswith("https://efts.sec.gov/LATEST/search-index"), (
        f"EFTS URL regression: got {captured_urls[0]!r}"
    )


def test_f321_efts_field_names_from_fixture(monkeypatch, tmp_path):
    """F321 Fix 3: parser reads adsh/root_forms instead of old accession_no/form_type.

    Fixture is a real EFTS response recorded 2026-06-05 for AAPL (CIK 0000320193).
    Before fix: src.get('accession_no') and src.get('form_type') both return '' (silent empty).
    After fix: adsh and root_forms[0] yield non-empty values.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    fixture_path = _FIXTURES_DIR / "aapl_efts_buyback.json"
    with open(fixture_path) as f:
        fixture_data = json.load(f)

    # ADV-07: pin the fixture schema itself — if the fixture goes stale (reverts to old
    # field names), this assertion catches it before the parser assertion can hide it.
    first_src = fixture_data["hits"]["hits"][0]["_source"]
    assert "_source" in fixture_data["hits"]["hits"][0], "fixture structure unexpected"
    assert "adsh" in first_src, (
        "Fixture missing 'adsh' key — fixture may be stale or reverted to old field names"
    )
    assert "root_forms" in first_src, (
        "Fixture missing 'root_forms' key — fixture may be stale or reverted to old field names"
    )

    def fake_get(url, params=None):
        return _make_fake_response(fixture_data)

    monkeypatch.setattr(edgar, "_get", fake_get)

    results = edgar.search_buyback_8k("0000320193", months_back=12)

    assert len(results) == 1
    r = results[0]
    # Fix 3: accessionNo and formType must be non-empty (old field names returned '')
    assert r["accessionNo"] != "", f"accessionNo is empty — adsh field not parsed"
    assert r["accessionNo"] == "0000320193-26-000011"
    assert r["formType"] == "8-K"
    assert r["filedAt"] == "2026-04-30"


def test_f321_has_buyback_authorization_aapl_fixture(monkeypatch, tmp_path):
    """F321 positive control: AAPL has_buyback_authorization → True from real EFTS fixture.

    Fixture: 1 hit (8-K filed 2026-04-30 with buyback authorization, EX-99.1).
    Recorded from live EFTS 2026-06-05 for window 2025-06-05..2026-06-05.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "efts").mkdir(parents=True, exist_ok=True)

    fixture_path = _FIXTURES_DIR / "aapl_efts_buyback.json"
    with open(fixture_path) as f:
        fixture_data = json.load(f)

    def fake_get(url, params=None):
        return _make_fake_response(fixture_data)

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.has_buyback_authorization("0000320193", months_back=12)
    assert result is True, "AAPL should have buyback authorization in 12-month window"


def test_f321_form4_index_parses_directory_item(monkeypatch, tmp_path):
    """F321 Fix 4: fetch_form4_xml uses index.json (not {accession}-index.json) + directory.item[].

    Fixture is a real index.json response for AAPL Form 4 accession 0001140361-26-023363,
    recorded 2026-06-05. Old code requested {accession}-index.json (404) and parsed documents[].
    New code uses index.json and parses directory.item[].name.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    (tmp_path / "form4").mkdir(parents=True, exist_ok=True)

    fixture_path = _FIXTURES_DIR / "aapl_form4_index.json"
    with open(fixture_path) as f:
        index_fixture = json.load(f)

    xml_fixture_path = _FIXTURES_DIR / "aapl_form4_real.xml"
    xml_content = xml_fixture_path.read_text(encoding="utf-8")

    captured_urls: list[str] = []

    def fake_get(url, params=None):
        captured_urls.append(url)
        if url.endswith("index.json"):
            return _make_fake_response(index_fixture)
        if url.endswith("form4.xml"):
            mock = MagicMock()
            mock.text = xml_content
            mock.raise_for_status.return_value = None
            return mock
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(edgar, "_get", fake_get)

    result = edgar.fetch_form4_xml("0000320193", "0001140361-26-023363")

    # Fix 4a: index URL must NOT contain the accession in the filename (old pattern 404d)
    index_url = next((u for u in captured_urls if "index" in u), None)
    assert index_url is not None, "No index URL was requested"
    assert index_url.endswith("/index.json"), (
        f"Expected URL ending in /index.json, got: {index_url}"
    )
    assert "023363-index.json" not in index_url, (
        f"Old broken pattern found in URL: {index_url}"
    )

    # Fix 4b: XML content should be returned (non-empty, valid XML)
    assert result != "", "fetch_form4_xml returned empty string — index parsing failed"
    assert "ownershipDocument" in result, "Returned content is not Form 4 XML"


def test_f321_get_form4_net_buys_aapl_fixture(monkeypatch, tmp_path):
    """F321 positive control: AAPL get_form4_net_buys → non-zero from real submissions + XML fixture.

    Uses recorded submissions fixture (3 Form 4s, all 2026, within 6-month window)
    and real Form 4 XML (has S transaction). Result must be non-zero.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    subs_fixture_path = _FIXTURES_DIR / "aapl_submissions_form4.json"
    with open(subs_fixture_path) as f:
        subs_fixture = json.load(f)

    xml_fixture_path = _FIXTURES_DIR / "aapl_form4_real.xml"
    xml_content = xml_fixture_path.read_text(encoding="utf-8")

    def fake_get(url, params=None):
        if "submissions" in url:
            return _make_fake_response(subs_fixture)
        raise AssertionError(f"Unexpected _get call: {url}")

    monkeypatch.setattr(edgar, "_get", fake_get)
    # Serve the real XML for every accession
    monkeypatch.setattr(edgar, "fetch_form4_xml", lambda cik_arg, acc: xml_content)

    result = edgar.get_form4_net_buys("0000320193", months_back=6,
                                       as_of=date(2026, 6, 5))

    assert result != 0.0, (
        f"get_form4_net_buys returned 0 for AAPL — parser is silently empty. "
        f"Check that transactionCode/shares/price are being extracted."
    )
