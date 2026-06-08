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
# F322 — Dual-class filer shares fallback (dei + weighted-average)
# ---------------------------------------------------------------------------


def test_f322_dei_shares_fallback(monkeypatch, tmp_path):
    """F322: get_shares_outstanding returns value from dei:EntityCommonStockSharesOutstanding
    when us-gaap:CommonStockSharesOutstanding is absent.

    Models NKE pattern: dei namespace has total aggregate shares (no per-class split).
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    dei_entries = [
        {"end": "2023-05-31", "val": 1_220_000_000, "filed": "2023-06-15", "form": "10-K"},
        {"end": "2023-08-31", "val": 1_210_000_000, "filed": "2023-10-01", "form": "10-Q"},
    ]
    fake_facts = {
        "facts": {
            "us-gaap": {},   # no CommonStockSharesOutstanding
            "dei": {
                "EntityCommonStockSharesOutstanding": {"units": {"shares": dei_entries}}
            },
        }
    }

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: _make_fake_response(fake_facts))

    # as_of = 2023-12-31 → both filed; most recent filed=2023-10-01 → 1210M
    result = edgar.get_shares_outstanding("0000123456", date(2023, 12, 31))
    assert result == 1_210_000_000.0, f"Expected dei fallback 1210M, got {result}"

    # as_of = 2023-07-01 → only first entry qualifies (filed 2023-06-15)
    result_early = edgar.get_shares_outstanding("0000123456", date(2023, 7, 1))
    assert result_early == 1_220_000_000.0, f"Expected 1220M at 2023-07-01, got {result_early}"


def test_f322_wa_shares_fallback(monkeypatch, tmp_path):
    """F322: get_shares_outstanding returns value from WeightedAverageNumberOfSharesOutstandingBasic
    when both primary and dei tags are absent (PTON/EL pattern).

    PIT guard: wa entries with end > as_of are excluded even if filed <= as_of.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    wa_entries = [
        # Q3: filed 2023-11-01, period Jul-Sep 2023
        {"start": "2023-07-01", "end": "2023-09-30", "val": 413_000_000, "filed": "2023-11-01", "form": "10-Q"},
        # Q2 YTD: filed 2024-02-01, period Oct-Dec 2023
        {"start": "2023-10-01", "end": "2023-12-31", "val": 421_000_000, "filed": "2024-02-01", "form": "10-Q"},
    ]
    fake_facts = {
        "facts": {
            "us-gaap": {
                "WeightedAverageNumberOfSharesOutstandingBasic": {"units": {"shares": wa_entries}}
            },
            "dei": {},
        }
    }

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: _make_fake_response(fake_facts))

    # as_of = 2023-12-31 → Q4 filing (2024-02-01) not yet filed; only Q3 qualifies → 413M
    result = edgar.get_shares_outstanding("0000123456", date(2023, 12, 31))
    assert result == 413_000_000.0, f"Expected 413M (PIT: Q4 filing not yet filed), got {result}"

    # as_of = 2024-03-01 → Q4 filing available; end=2023-12-31 <= 2024-03-01 → 421M
    result_after = edgar.get_shares_outstanding("0000123456", date(2024, 3, 1))
    assert result_after == 421_000_000.0, f"Expected 421M after Q4 filing, got {result_after}"


def test_f322_primary_unchanged_when_present(monkeypatch, tmp_path):
    """F322: primary us-gaap tag still wins when present; fallbacks are not consulted."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    primary_entries = [
        {"end": "2023-12-31", "val": 15_000_000_000, "filed": "2024-02-01", "form": "10-K"},
    ]
    # Both fallbacks also present — they must NOT influence the result
    fake_facts = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {"units": {"shares": primary_entries}},
                "WeightedAverageNumberOfSharesOutstandingBasic": {
                    "units": {"shares": [
                        {"start": "2023-10-01", "end": "2023-12-31", "val": 99_000_000,
                         "filed": "2024-02-01", "form": "10-K"}
                    ]}
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [
                        {"end": "2023-12-31", "val": 88_000_000, "filed": "2024-02-01", "form": "10-K"}
                    ]}
                }
            },
        }
    }

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: _make_fake_response(fake_facts))

    result = edgar.get_shares_outstanding("0000123456", date(2024, 6, 1))
    assert result == 15_000_000_000.0, (
        f"Primary tag must win when present; fallbacks must not override. Got {result}"
    )


def test_f322_fail_closed_when_all_tags_absent(monkeypatch, tmp_path):
    """F322: returns None when no share tags are present (fail-closed preserved)."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    fake_facts = {"facts": {"us-gaap": {}, "dei": {}}}

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: _make_fake_response(fake_facts))

    result = edgar.get_shares_outstanding("0000123456", date(2024, 6, 1))
    assert result is None, f"Expected None (no share tags), got {result}"


# ---------------------------------------------------------------------------
# F314 — Raw-facts cache pruning
# ---------------------------------------------------------------------------


def test_f314_prune_age_based(tmp_path):
    """F314: prune_edgar_raw_cache deletes files older than max_age_days."""
    import edgar
    import time

    monkeypatch_dir = tmp_path / "facts"
    monkeypatch_dir.mkdir(parents=True)

    old_file = monkeypatch_dir / "0000111111.json"
    old_file.write_text("{}", encoding="utf-8")
    # Set mtime to 35 days ago
    old_mtime = time.time() - 35 * 86400
    import os
    os.utime(old_file, (old_mtime, old_mtime))

    new_file = monkeypatch_dir / "0000222222.json"
    new_file.write_text("{}", encoding="utf-8")
    # recent file: default mtime = now

    orig_cache_dir = edgar.CACHE_DIR
    edgar.CACHE_DIR = tmp_path
    try:
        result = edgar.prune_edgar_raw_cache(max_age_days=30, size_cap_bytes=10 * 1024 ** 3)
    finally:
        edgar.CACHE_DIR = orig_cache_dir

    assert result["deleted_age"] == 1, f"Expected 1 age-eviction, got {result}"
    assert not old_file.exists(), "Old file should be deleted"
    assert new_file.exists(), "New file must be preserved"


def test_f314_prune_size_cap(tmp_path):
    """F314: prune_edgar_raw_cache evicts oldest files when total size exceeds cap."""
    import edgar
    import time
    import os

    monkeypatch_dir = tmp_path / "facts"
    monkeypatch_dir.mkdir(parents=True)

    now = time.time()
    files = []
    # Create 5 files, 100 bytes each; oldest first
    for i in range(5):
        p = monkeypatch_dir / f"000000000{i}.json"
        p.write_text("x" * 100, encoding="utf-8")
        mtime = now - (5 - i) * 3600  # oldest = 5h ago, newest = 1h ago
        os.utime(p, (mtime, mtime))
        files.append(p)

    orig_cache_dir = edgar.CACHE_DIR
    edgar.CACHE_DIR = tmp_path
    try:
        # Cap = 300 bytes; 5×100=500 bytes total → must evict 2 oldest to get ≤300
        result = edgar.prune_edgar_raw_cache(max_age_days=9999, size_cap_bytes=300, min_keep=1)
    finally:
        edgar.CACHE_DIR = orig_cache_dir

    assert result["deleted_size"] == 2, f"Expected 2 size-evictions, got {result}"
    assert not files[0].exists(), "Oldest file should be evicted"
    assert not files[1].exists(), "Second oldest should be evicted"
    assert files[2].exists(), "Third file should survive"


def test_f314_prune_dry_run(tmp_path):
    """F314: dry_run=True reports deletions without modifying disk."""
    import edgar
    import time
    import os

    monkeypatch_dir = tmp_path / "facts"
    monkeypatch_dir.mkdir(parents=True)

    old_file = monkeypatch_dir / "0000333333.json"
    old_file.write_text("{}", encoding="utf-8")
    old_mtime = time.time() - 40 * 86400
    os.utime(old_file, (old_mtime, old_mtime))

    orig_cache_dir = edgar.CACHE_DIR
    edgar.CACHE_DIR = tmp_path
    try:
        result = edgar.prune_edgar_raw_cache(max_age_days=30, dry_run=True)
    finally:
        edgar.CACHE_DIR = orig_cache_dir

    assert result["dry_run"] is True
    assert result["deleted_age"] == 1, f"dry_run should report 1 deletion, got {result}"
    assert old_file.exists(), "dry_run must not delete files"


def test_f314_prune_never_evicts_derived(tmp_path):
    """F314: derived cache files are never touched by prune_edgar_raw_cache."""
    import edgar
    import time
    import os

    (tmp_path / "facts").mkdir(parents=True)
    derived_dir = tmp_path / "derived" / "v1"
    derived_dir.mkdir(parents=True)

    derived_file = derived_dir / "0000444444.json"
    derived_file.write_text("{}", encoding="utf-8")
    # Make it very old
    old_mtime = time.time() - 365 * 86400
    os.utime(derived_file, (old_mtime, old_mtime))

    orig_cache_dir = edgar.CACHE_DIR
    edgar.CACHE_DIR = tmp_path
    try:
        edgar.prune_edgar_raw_cache(max_age_days=1, size_cap_bytes=0)
    finally:
        edgar.CACHE_DIR = orig_cache_dir

    assert derived_file.exists(), "Derived cache must never be evicted by prune_edgar_raw_cache"


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


# ---------------------------------------------------------------------------
# F329 — Real Form 4 P-code (purchase) fixture
# ---------------------------------------------------------------------------


def test_f329_form4_parse_buy_p_code_real_xml():
    """F329: _parse_form4_transactions correctly parses a real P-code (purchase) Form 4.

    Fixture: MCY (Mercury General) insider buy by director Joshua Little, 2023-03-13.
    Accession 0001209191-23-018396, filed 2023-03-14.
    Source: recorded from EDGAR cache 2026-06-08.
    Expected: 1 transaction, code=P, 250 shares at $29.8541.
    """
    import edgar

    xml_content = (_FIXTURES_DIR / "mcy_form4_buy_p_code.xml").read_text(encoding="utf-8")
    txns = edgar._parse_form4_transactions(xml_content)

    # Must parse exactly 1 transaction
    assert len(txns) == 1, f"Expected 1 transaction, got {len(txns)}: {txns}"
    t = txns[0]
    assert t["transactionCode"] == "P", f"Expected transactionCode 'P', got {t['transactionCode']!r}"
    assert t["shares"] == 250.0, f"Expected 250 shares, got {t['shares']}"
    assert t["price"] == pytest.approx(29.8541, rel=1e-4), f"Expected price ~29.8541, got {t['price']}"


def test_f329_get_form4_net_buys_p_code_fixture(monkeypatch, tmp_path):
    """F329 positive control: get_form4_net_buys returns net_buys > 0 for a P-only Form 4.

    Uses a real MCY Form 4 (director purchase) fixture and a minimal inline submissions stub.
    Fixture: 250 shares × $29.8541 = $7463.52 net buy.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)

    xml_content = (_FIXTURES_DIR / "mcy_form4_buy_p_code.xml").read_text(encoding="utf-8")

    # Minimal submissions stub: one Form 4 within the 6-month window
    subs_stub = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0001209191-23-018396"],
                "filingDate": ["2023-03-14"],
            }
        }
    }

    def fake_get(url, params=None):
        if "submissions" in url:
            return _make_fake_response(subs_stub)
        raise AssertionError(f"Unexpected _get call: {url}")

    monkeypatch.setattr(edgar, "_get", fake_get)
    monkeypatch.setattr(edgar, "fetch_form4_xml", lambda cik_arg, acc: xml_content)

    result = edgar.get_form4_net_buys("0000064996", months_back=6, as_of=date(2023, 6, 14))

    expected_net = 250.0 * 29.8541  # = 7463.525
    assert result > 0, f"Expected net_buys > 0 (pure P-code filing), got {result}"
    assert result == pytest.approx(expected_net, rel=1e-4), (
        f"Expected net_buys ≈ {expected_net:.2f} (250×29.8541), got {result}"
    )


# ===========================================================================
# F320 — Derived compact fundamentals cache tests
# ===========================================================================


def _make_full_facts(revenue_val=1_000_000, ni_val=200_000, gp_val=400_000,
                     ocf_val=300_000, shares_val=5_000_000):
    """Build a minimal but complete synthetic companyfacts dict covering all 5 series."""
    def _q(val):
        return [
            {"end": "2023-03-31", "val": val, "accn": "a1", "fy": 2023, "fp": "Q1",
             "form": "10-Q", "filed": "2023-05-01", "start": "2023-01-01"},
            {"end": "2023-06-30", "val": val + 10, "accn": "a2", "fy": 2023, "fp": "Q2",
             "form": "10-Q", "filed": "2023-08-01", "start": "2023-04-01"},
            {"end": "2023-09-30", "val": val + 20, "accn": "a3", "fy": 2023, "fp": "Q3",
             "form": "10-Q", "filed": "2023-11-01", "start": "2023-07-01"},
        ]

    return {
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": _q(revenue_val)}},
                "NetIncomeLoss": {"units": {"USD": _q(ni_val)}},
                "GrossProfit": {"units": {"USD": _q(gp_val)}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": _q(ocf_val)}},
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2023-03-31", "val": shares_val, "filed": "2023-05-01",
                             "form": "10-Q", "accn": "a1"},
                            {"end": "2023-09-30", "val": shares_val + 100, "filed": "2023-11-01",
                             "form": "10-Q", "accn": "a3"},
                        ]
                    }
                },
            }
        }
    }


# ---------------------------------------------------------------------------
# test_f320_parse_companyfacts_to_derived_identity
# ---------------------------------------------------------------------------


def test_f320_parse_companyfacts_to_derived_identity(monkeypatch, tmp_path):
    """parse_companyfacts_to_derived output is bit-identical to the raw-path accessors.

    For each of the 5 series, the derived path must return the exact same list
    as the original _get_quarterly_series_for_tag_list / raw get_shares_outstanding.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    # DERIVED_CACHE_DIR not needed — _derived_path() reads CACHE_DIR at call time.

    facts = _make_full_facts()
    _seed_cache(tmp_path / "facts" / "0000123456.json", facts)

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: (_ for _ in ()).throw(
        AssertionError("_get must not be called — facts are cached")))

    # --- raw path (old) ---
    raw_revenue = edgar._get_quarterly_series_for_tag_list("0000123456", edgar._REVENUE_TAGS)
    raw_ni = edgar._get_quarterly_series_for_tag_list("0000123456", edgar._NET_INCOME_TAGS)
    raw_gp = edgar._get_quarterly_series_for_tag_list("0000123456", edgar._GROSS_PROFIT_TAGS)
    raw_ocf = edgar._get_quarterly_series_for_tag_list("0000123456", edgar._OCF_TAGS)
    # shares raw path
    raw_facts = edgar.fetch_companyfacts("0000123456")
    raw_shares_entries = (
        raw_facts.get("facts", {}).get("us-gaap", {})
        .get("CommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    )
    as_of = date(2023, 12, 1)
    raw_shares_val = edgar.get_shares_outstanding.__wrapped__("0000123456", as_of) \
        if hasattr(edgar.get_shares_outstanding, "__wrapped__") else None

    # --- derived path ---
    # Clear LRU so the test starts from a fresh slate.
    edgar._load_derived_cache_clear()
    derived = edgar.parse_companyfacts_to_derived("0000123456")

    assert derived["revenue"] == raw_revenue, "revenue mismatch"
    assert derived["net_income"] == raw_ni, "net_income mismatch"
    assert derived["gross_profit"] == raw_gp, "gross_profit mismatch"
    assert derived["ocf"] == raw_ocf, "ocf mismatch"

    # shares: derived must contain all raw share entries (as float val + filed + form)
    derived_share_filds = {(e["filed"], float(e["val"])) for e in derived["shares"]}
    for se in raw_shares_entries:
        if se.get("val") is not None:
            assert (se["filed"], float(se["val"])) in derived_share_filds, \
                f"share entry missing from derived: {se}"

    # schema fields
    assert derived["schema_version"] == 1
    assert derived["cik"] == "0000123456"


# ---------------------------------------------------------------------------
# test_f320_public_accessors_use_derived_cache
# ---------------------------------------------------------------------------


def test_f320_public_accessors_use_derived_cache(monkeypatch, tmp_path):
    """The five public accessors route through _load_derived (not fetch_companyfacts directly).

    After the derived cache is warm, fetch_companyfacts must NOT be called again.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    facts = _make_full_facts()
    _seed_cache(tmp_path / "facts" / "0000123456.json", facts)

    fetch_calls = {"n": 0}
    original_fetch = edgar.fetch_companyfacts

    def counting_fetch(cik):
        fetch_calls["n"] += 1
        return original_fetch(cik)

    monkeypatch.setattr(edgar, "fetch_companyfacts", counting_fetch)
    monkeypatch.setattr(edgar, "_get", lambda url, params=None: (_ for _ in ()).throw(
        AssertionError("_get must not be called")))

    # First call — builds derived (1 parse)
    edgar.get_quarterly_revenue("0000123456")
    first_count = fetch_calls["n"]
    assert first_count == 1, f"Expected 1 fetch_companyfacts call to build derived, got {first_count}"

    # Subsequent calls — LRU hit, no additional parses
    edgar.get_quarterly_net_income("0000123456")
    edgar.get_quarterly_gross_profit("0000123456")
    edgar.get_quarterly_ocf("0000123456")
    edgar.get_shares_outstanding("0000123456", date(2023, 12, 1))

    assert fetch_calls["n"] == 1, (
        f"Expected exactly 1 total fetch_companyfacts call (LRU hit for subsequent); "
        f"got {fetch_calls['n']}"
    )


# ---------------------------------------------------------------------------
# test_f320_derived_identity_end_to_end
# ---------------------------------------------------------------------------


def test_f320_derived_identity_end_to_end(monkeypatch, tmp_path):
    """Public accessors return identical values via old raw path vs new derived path."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    facts = _make_full_facts(revenue_val=9_000_000, ni_val=1_500_000,
                             gp_val=3_000_000, ocf_val=2_100_000, shares_val=12_000_000)
    _seed_cache(tmp_path / "facts" / "0000777777.json", facts)

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: (_ for _ in ()).throw(
        AssertionError("_get must not be called")))

    # Collect expected values via raw helpers directly
    raw_facts = edgar.fetch_companyfacts("0000777777")
    expected_rev = edgar._get_quarterly_series_for_tag_list("0000777777", edgar._REVENUE_TAGS)
    expected_ni = edgar._get_quarterly_series_for_tag_list("0000777777", edgar._NET_INCOME_TAGS)
    expected_gp = edgar._get_quarterly_series_for_tag_list("0000777777", edgar._GROSS_PROFIT_TAGS)
    expected_ocf = edgar._get_quarterly_series_for_tag_list("0000777777", edgar._OCF_TAGS)

    # Clear LRU so derived path runs fresh
    edgar._load_derived_cache_clear()

    assert edgar.get_quarterly_revenue("0000777777") == expected_rev
    assert edgar.get_quarterly_net_income("0000777777") == expected_ni
    assert edgar.get_quarterly_gross_profit("0000777777") == expected_gp
    assert edgar.get_quarterly_ocf("0000777777") == expected_ocf

    # Shares point-in-time: earliest filed → must be the Q1 entry
    as_of_early = date(2023, 6, 1)
    shares_early = edgar.get_shares_outstanding("0000777777", as_of_early)
    assert shares_early == 12_000_000.0, f"Expected 12_000_000 shares, got {shares_early}"

    # Later as_of → Q3 entry (higher)
    as_of_late = date(2023, 12, 1)
    shares_late = edgar.get_shares_outstanding("0000777777", as_of_late)
    assert shares_late == 12_000_100.0, f"Expected 12_000_100 shares, got {shares_late}"


# ---------------------------------------------------------------------------
# test_f320_derived_invalidated_when_raw_newer
# ---------------------------------------------------------------------------


def test_f320_derived_invalidated_when_raw_newer(monkeypatch, tmp_path):
    """Derived cache is rebuilt when raw facts file is newer — detected via mtime key.

    This test proves the PRODUCTION failure mode: the in-process cache is warm
    after the first accessor call, then raw is refreshed WITHOUT calling any
    cache_clear(), and the second accessor call must still return the new data.
    A naive lru_cache implementation would silently return v1 here (COR-01).
    """
    import edgar
    import os

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    facts_v1 = _make_full_facts(revenue_val=1_000_000)
    _seed_cache(tmp_path / "facts" / "0000999999.json", facts_v1)

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: (_ for _ in ()).throw(
        AssertionError("_get must not be called")))

    # Step 1: warm the in-process cache.
    rev_v1 = edgar.get_quarterly_revenue("0000999999")
    assert rev_v1[0]["val"] == 1_000_000.0

    # Step 2: simulate a TTL-driven raw refresh — update raw file content and advance
    # its mtime to be NEWER than derived, WITHOUT touching the in-process cache.
    facts_v2 = _make_full_facts(revenue_val=2_000_000)
    raw_path = tmp_path / "facts" / "0000999999.json"
    raw_path.write_text(json.dumps(facts_v2), encoding="utf-8")
    # Push raw mtime forward so derived (unchanged) appears older.
    derived_path = tmp_path / "derived" / "v1" / "0000999999.json"
    old_derived_mtime = derived_path.stat().st_mtime
    os.utime(derived_path, (old_derived_mtime - 10, old_derived_mtime - 10))

    # Step 3: call accessor again — NO cache_clear() called.
    # The mtime-keyed cache must detect the changed raw mtime and rebuild.
    rev_v2 = edgar.get_quarterly_revenue("0000999999")
    assert rev_v2[0]["val"] == 2_000_000.0, (
        f"Expected revenue 2_000_000 after raw refresh (warm cache), got {rev_v2[0]['val']}. "
        "lru_cache-style caching would silently return stale v1 here (COR-01 regression)."
    )


# ---------------------------------------------------------------------------
# test_f320_orphan_derived_refetches_when_raw_deleted
# ---------------------------------------------------------------------------


def test_f320_orphan_derived_refetches_when_raw_deleted(monkeypatch, tmp_path):
    """Orphan derived (raw deleted) is detected via mtime key — re-fetch triggered.

    Proves COR-02: if raw file disappears after the in-process cache is warm,
    the next accessor call detects raw_mtime=None (key mismatch) and triggers
    a fresh network fetch rather than serving the orphaned LRU entry.
    """
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    facts_v1 = _make_full_facts(revenue_val=5_000_000)
    _seed_cache(tmp_path / "facts" / "0000444444.json", facts_v1)

    # Track network fetch calls.
    fetch_calls = {"n": 0}
    facts_v2 = _make_full_facts(revenue_val=6_000_000)

    def fake_get(url, params=None):
        fetch_calls["n"] += 1
        return _make_fake_response(facts_v2)

    monkeypatch.setattr(edgar, "_get", fake_get)

    # Step 1: warm the in-process cache (no network fetch — raw file is present).
    rev_v1 = edgar.get_quarterly_revenue("0000444444")
    assert rev_v1[0]["val"] == 5_000_000.0
    assert fetch_calls["n"] == 0, "First load must not hit network (raw file is present)"

    # Step 2: delete the raw file, simulating cache pruning or disk cleanup.
    raw_path = tmp_path / "facts" / "0000444444.json"
    raw_path.unlink()

    # Step 3: call accessor again — NO cache_clear() called.
    # raw_mtime is now None → key mismatch → orphan policy triggers re-fetch.
    rev_v2 = edgar.get_quarterly_revenue("0000444444")
    assert fetch_calls["n"] > 0, (
        "Expected a network fetch after raw deletion (COR-02 regression: "
        "mtime-keyed cache must detect absent raw file and re-fetch)."
    )
    assert rev_v2[0]["val"] == 6_000_000.0, (
        f"Expected revenue 6_000_000 from re-fetch, got {rev_v2[0]['val']}"
    )


# ---------------------------------------------------------------------------
# test_f320_lru_cache_behavior
# ---------------------------------------------------------------------------


def test_f320_lru_cache_behavior(monkeypatch, tmp_path):
    """Mtime-keyed cache: repeated calls for same CIK within a run hit the in-process cache, not disk."""
    import edgar

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    facts = _make_full_facts()
    _seed_cache(tmp_path / "facts" / "0000111111.json", facts)

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: (_ for _ in ()).throw(
        AssertionError("_get must not be called")))

    disk_reads = {"n": 0}
    orig_read_text = None

    # First call — builds + persists derived, then LRU is warm
    edgar.get_quarterly_revenue("0000111111")

    # Spy on Path.read_text to count disk reads on derived file
    from pathlib import Path as _Path
    orig_read_text = _Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if "derived" in str(self):
            disk_reads["n"] += 1
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "read_text", counting_read_text)

    # Call all 5 accessors — all should be LRU hits (0 disk reads for derived)
    edgar.get_quarterly_revenue("0000111111")
    edgar.get_quarterly_net_income("0000111111")
    edgar.get_quarterly_gross_profit("0000111111")
    edgar.get_quarterly_ocf("0000111111")
    edgar.get_shares_outstanding("0000111111", date(2023, 12, 1))

    assert disk_reads["n"] == 0, (
        f"Expected 0 derived disk reads (mtime-keyed cache should be warm), got {disk_reads['n']}"
    )


# ---------------------------------------------------------------------------
# test_f320_corrupt_derived_cache_rebuilds
# ---------------------------------------------------------------------------


def test_f320_corrupt_derived_cache_rebuilds(monkeypatch, tmp_path):
    """Corrupt derived JSON triggers a clean rebuild from raw facts."""
    import edgar
    import os

    monkeypatch.setattr(edgar, "CACHE_DIR", tmp_path)
    edgar._load_derived_cache_clear()

    facts = _make_full_facts(revenue_val=7_777_777)
    _seed_cache(tmp_path / "facts" / "0000888888.json", facts)

    monkeypatch.setattr(edgar, "_get", lambda url, params=None: (_ for _ in ()).throw(
        AssertionError("_get must not be called")))

    # Create a corrupt derived file that is OLDER than raw (so freshness passes,
    # but JSON parse will fail, triggering rebuild).
    derived_dir = tmp_path / "derived" / "v1"
    derived_dir.mkdir(parents=True, exist_ok=True)
    derived_path = derived_dir / "0000888888.json"
    derived_path.write_text("not valid json {{{{", encoding="utf-8")
    # Make derived appear newer than raw so freshness check passes
    raw_mtime = (tmp_path / "facts" / "0000888888.json").stat().st_mtime
    os.utime(derived_path, (raw_mtime + 1, raw_mtime + 1))

    result = edgar.get_quarterly_revenue("0000888888")
    assert len(result) > 0, "Expected non-empty revenue after corrupt-cache rebuild"
    assert result[0]["val"] == 7_777_777.0
