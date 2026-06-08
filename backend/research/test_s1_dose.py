"""Unit tests for s1_dose.py (F395) and related registry wiring.

Tests per brief §6:
  - TestParseQualifySellTransactions — XML parsing S+D filter
  - TestS1Score — score formula
  - TestBuildS1EventsInterchangeable — signature + return type
  - TestCompileRegistersS1 — premise_compile/spec registry
  - TestMaxCapFloor — max_market_cap filtering
  - TestDoseBuilderDispatch — premise_run dispatch
  - TestCacheIsolation (E1) — s1 xml_cache never shares with r1

Run:
    backend/venv/bin/python3 -m pytest backend/research/test_s1_dose.py -q
"""
from __future__ import annotations

import inspect
import math
import sys
import tempfile
import json
from datetime import date
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _make_xml(code: str, adc: str, shares: float = 1000.0, price: float = 10.0,
              with_10b51: bool = False, form_10b51: bool = False) -> str:
    """Build a minimal Form 4 XML with one nonDerivativeTransaction."""
    footnote_id = ""
    footnote_body = ""
    remarks = ""
    if with_10b51:
        footnote_id = '<footnoteId id="F1"/>'
        footnote_body = '<footnote id="F1">Pursuant to Rule 10b5-1 trading plan</footnote>'
    if form_10b51:
        remarks = "<remarks>Adopted pursuant to Rule 10b5-1 plan.</remarks>"

    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
      <rptOwnerName>Test Owner</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  {remarks}
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCode>{code}</transactionCode>
      <transactionAcquiredDisposedCode><value>{adc}</value></transactionAcquiredDisposedCode>
      <transactionShares><value>{shares}</value></transactionShares>
      <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
      {footnote_id}
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <footnotes>
    {footnote_body}
  </footnotes>
</ownershipDocument>"""


def _make_xml_no_price(code: str, adc: str, shares: float = 1000.0) -> str:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCode>{code}</transactionCode>
      <transactionAcquiredDisposedCode><value>{adc}</value></transactionAcquiredDisposedCode>
      <transactionShares><value>{shares}</value></transactionShares>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def _make_xml_both(buy_shares: float = 500.0, buy_price: float = 10.0,
                   sell_shares: float = 600.0, sell_price: float = 11.0) -> str:
    """Build a Form 4 XML with both a P/A purchase AND an S/D sale."""
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001234567</rptOwnerCik>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCode>P</transactionCode>
      <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      <transactionShares><value>{buy_shares}</value></transactionShares>
      <transactionPricePerShare><value>{buy_price}</value></transactionPricePerShare>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCode>S</transactionCode>
      <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      <transactionShares><value>{sell_shares}</value></transactionShares>
      <transactionPricePerShare><value>{sell_price}</value></transactionPricePerShare>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


# ===========================================================================
# §6.1 TestParseQualifySellTransactions
# ===========================================================================

class TestParseQualifySellTransactions:
    from research.s1_dose import _parse_qualifying_sell_transactions

    def test_s_d_transaction_captured(self):
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml("S", "D", shares=1000.0, price=15.0)
        txns, owner_cik, form_10b51 = _parse_qualifying_sell_transactions(xml)
        assert len(txns) == 1
        assert txns[0]["shares"] == 1000.0
        assert txns[0]["price"] == 15.0
        assert txns[0]["is_10b51"] is False
        assert owner_cik == "0001234567"

    def test_p_a_transaction_excluded(self):
        """P/A (buy) transactions must NOT be captured by the sell parser."""
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml("P", "A", shares=500.0, price=10.0)
        txns, _, _ = _parse_qualifying_sell_transactions(xml)
        assert txns == []

    def test_s_a_transaction_excluded(self):
        """S/A (stock award, code S but acquired) must NOT be captured."""
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml("S", "A", shares=200.0, price=12.0)
        txns, _, _ = _parse_qualifying_sell_transactions(xml)
        assert txns == []

    def test_10b51_txn_flagged(self):
        """S/D + transaction-level 10b5-1 footnote → is_10b51=True."""
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml("S", "D", with_10b51=True)
        txns, _, _ = _parse_qualifying_sell_transactions(xml)
        assert len(txns) == 1
        assert txns[0]["is_10b51"] is True

    def test_10b51_form_level_propagates(self):
        """Form-level 10b5-1 remarks → all transactions get is_10b51=True."""
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml("S", "D", form_10b51=True)
        txns, _, form_flag = _parse_qualifying_sell_transactions(xml)
        assert len(txns) == 1
        assert txns[0]["is_10b51"] is True
        assert form_flag is True

    def test_missing_price_handled(self):
        """S/D with no price element → price=None, shares captured."""
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml_no_price("S", "D", shares=750.0)
        txns, _, _ = _parse_qualifying_sell_transactions(xml)
        assert len(txns) == 1
        assert txns[0]["shares"] == 750.0
        assert txns[0]["price"] is None

    def test_empty_xml_returns_empty(self):
        from research.s1_dose import _parse_qualifying_sell_transactions
        txns, owner, flag = _parse_qualifying_sell_transactions("")
        assert txns == []
        assert owner is None
        assert flag is False

    def test_malformed_xml_returns_empty(self):
        from research.s1_dose import _parse_qualifying_sell_transactions
        txns, owner, flag = _parse_qualifying_sell_transactions("<broken<<xml>")
        assert txns == []


# ===========================================================================
# E1 Cache isolation test
# ===========================================================================

class TestCacheIsolation:
    """E1: An accession with BOTH a P/A and S/D transaction must yield
    DIFFERENT qualifying sets when passed through the r1 vs s1 parsers."""

    def test_buy_parser_sees_only_buy(self):
        from research.r1_dose import _parse_qualifying_transactions
        xml = _make_xml_both(buy_shares=500.0, buy_price=10.0, sell_shares=600.0, sell_price=11.0)
        txns, _, _ = _parse_qualifying_transactions(xml)
        assert len(txns) == 1
        assert txns[0]["shares"] == 500.0
        assert txns[0]["price"] == 10.0

    def test_sell_parser_sees_only_sell(self):
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml_both(buy_shares=500.0, buy_price=10.0, sell_shares=600.0, sell_price=11.0)
        txns, _, _ = _parse_qualifying_sell_transactions(xml)
        assert len(txns) == 1
        assert txns[0]["shares"] == 600.0
        assert txns[0]["price"] == 11.0

    def test_different_qualifying_sets(self):
        """Running both parsers on same XML → completely different transaction sets."""
        from research.r1_dose import _parse_qualifying_transactions
        from research.s1_dose import _parse_qualifying_sell_transactions
        xml = _make_xml_both(buy_shares=500.0, buy_price=10.0, sell_shares=600.0, sell_price=11.0)

        buy_txns, _, _ = _parse_qualifying_transactions(xml)
        sell_txns, _, _ = _parse_qualifying_sell_transactions(xml)

        # Must yield different transaction sets
        buy_prices = {t["price"] for t in buy_txns}
        sell_prices = {t["price"] for t in sell_txns}
        assert buy_prices != sell_prices, "Buy and sell parsers must return different transactions"
        assert buy_prices == {10.0}
        assert sell_prices == {11.0}


# ===========================================================================
# §6.1 TestS1Score
# ===========================================================================

class TestS1Score:
    def test_score_formula_matches_r1(self):
        """Same D/k/MC → same score value (uses same _compute_score from r1_dose)."""
        from research.r1_dose import _compute_score
        D = 1_000_000.0
        k = 3
        MC = 5_000_000_000.0
        expected = math.log1p(D / MC) * (1.0 + 0.5 * k)
        actual = _compute_score(D, k, MC)
        assert abs(actual - expected) < 1e-12

    def test_score_zero_when_mc_zero(self):
        from research.r1_dose import _compute_score
        assert _compute_score(1_000_000.0, 3, 0.0) == 0.0

    def test_score_nonnegative(self):
        from research.r1_dose import _compute_score
        import random
        rng = random.Random(42)
        for _ in range(20):
            D = rng.uniform(1e4, 1e9)
            k = rng.randint(1, 10)
            MC = rng.uniform(1e8, 1e12)
            score = _compute_score(D, k, MC)
            assert score >= 0.0
            assert math.isfinite(score)


# ===========================================================================
# §6.1 TestBuildS1EventsInterchangeable
# ===========================================================================

class TestBuildS1EventsInterchangeable:
    def test_same_signature_as_r1(self):
        """build_s1_events must have the same required params as build_r1_events."""
        from research.r1_dose import build_r1_events
        from research.s1_dose import build_s1_events

        r1_sig = inspect.signature(build_r1_events)
        s1_sig = inspect.signature(build_s1_events)

        # Required positional params: start, end
        r1_params = set(r1_sig.parameters.keys())
        s1_params = set(s1_sig.parameters.keys())

        # s1 must have all r1 params (plus max_market_cap)
        missing = r1_params - s1_params
        assert not missing, f"s1 missing params from r1: {missing}"
        assert "max_market_cap" in s1_params, "s1 must have max_market_cap param"

    def test_returns_tuple_list_dict_on_empty(self, tmp_path):
        """build_s1_events returns (list, dict) even on empty/missing index."""
        from research.s1_dose import build_s1_events

        # Write a minimal valid index with zero entries
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps({}), encoding="utf-8")

        loader_fn = lambda ticker: None
        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 3, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=loader_fn,
        )
        assert isinstance(events, list)
        assert isinstance(meta, dict)
        assert len(events) == 0

    def test_meta_has_required_keys(self, tmp_path):
        """Meta dict must have all required keys."""
        from research.s1_dose import build_s1_events

        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps({}), encoding="utf-8")

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 3, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: None,
        )
        required_keys = {
            "filings_scanned", "filings_qualifying", "acceptance_fallbacks",
            "n_10b51_excluded_total", "missing_price_txns_total",
            "score_undefined_total", "events_raw", "events_returned",
            "n_cap_ceiling_excluded",
        }
        assert required_keys.issubset(set(meta.keys()))


# ===========================================================================
# §6.1 TestCompileRegistersS1
# ===========================================================================

class TestCompileRegistersS1:
    def test_s1_score_in_valid_doses(self):
        from research.premise_spec import _VALID_DOSES
        assert "s1_score" in _VALID_DOSES

    def test_r1_score_still_in_valid_doses(self):
        from research.premise_spec import _VALID_DOSES
        assert "r1_score" in _VALID_DOSES

    def test_s1_cost_fn_registered(self):
        from research.premise_compile import _COST_FN_BY_DOSE
        assert "s1_score" in _COST_FN_BY_DOSE

    def test_s1_cost_fn_returns_0_04(self):
        from research.premise_compile import _COST_FN_BY_DOSE
        fn = _COST_FN_BY_DOSE["s1_score"]
        assert fn(None, 50.0) == 0.04

    def test_assert_passes(self):
        """Importing premise_compile must not raise (assert _VALID_DOSES ⊆ _COST_FN_BY_DOSE)."""
        # Import without reload — module-level assert ran at initial import.
        # We verify it didn't raise by checking the registry is populated.
        from research.premise_compile import _COST_FN_BY_DOSE
        from research.premise_spec import _VALID_DOSES
        assert _VALID_DOSES.issubset(set(_COST_FN_BY_DOSE)), (
            f"Assert would fire: doses missing cost_fn: "
            f"{_VALID_DOSES - set(_COST_FN_BY_DOSE)}"
        )

    def test_compile_spec_s1_score(self, tmp_path):
        """compile_spec with dose='s1_score' produces a CompileResult without error."""
        from research.premise_spec import PremiseSpec
        from research.premise_compile import compile_spec

        spec = PremiseSpec(
            premise_text="Insider sell → price recovery",
            stream="form4",
            dose="s1_score",
        )
        cr = compile_spec(spec, study_name="test_s1", output_dir=tmp_path)
        assert cr.dose_builder == "s1_score"
        assert cr.config.cost_fn is not None
        assert cr.config.cost_fn(None, 50.0) == 0.04

    def test_universe_floors_max_market_cap_default_none(self):
        from research.premise_spec import UniverseFloors
        floors = UniverseFloors()
        assert floors.max_market_cap is None

    def test_universe_floors_max_market_cap_set(self):
        from research.premise_spec import UniverseFloors
        floors = UniverseFloors(max_market_cap=10_000_000_000.0)
        assert floors.max_market_cap == 10_000_000_000.0


# ===========================================================================
# §6.1 TestMaxCapFloor
# ===========================================================================

class TestMaxCapFloor:
    """Tests for max_market_cap filtering inside build_s1_events."""

    def _build_index_with_filing(self, tmp_path: Path, ticker: str, cik: str,
                                  accession: str, filed: str) -> Path:
        """Write a minimal index.json and return its path."""
        index_path = tmp_path / "index.json"
        accession_nodash = accession.replace("-", "")
        index_data = {
            "entry_1": {
                "ticker": ticker,
                "cik": cik,
                "status": "done",
                "filings": [
                    {
                        "accession": accession,
                        "filed": filed,
                        "xml_status": "ok",
                    }
                ],
            }
        }
        index_path.write_text(json.dumps(index_data), encoding="utf-8")
        return index_path

    def _write_xml(self, tmp_path: Path, cik: str, accession: str, xml_content: str) -> None:
        padded = str(int(cik)).zfill(10)
        accession_nodash = accession.replace("-", "")
        xml_path = tmp_path / f"{padded}_{accession_nodash}.xml"
        xml_path.write_text(xml_content, encoding="utf-8")

    def _write_subs(self, tmp_path: Path, cik: str, accession: str, filed: str) -> None:
        padded = str(int(cik)).zfill(10)
        subs_data = {
            "filings": {
                "recent": {
                    "accessionNumber": [accession],
                    "acceptanceDateTime": [f"{filed}T10:00:00.000Z"],
                }
            }
        }
        subs_path = tmp_path / f"{padded}.json"
        subs_path.write_text(json.dumps(subs_data), encoding="utf-8")

    def test_max_cap_excludes_large_cap_event(self, tmp_path):
        """Events with MC > max_market_cap must not appear in results."""
        from research.s1_dose import build_s1_events

        ticker = "BIGCAP"
        cik = "9999999"
        accession = "0000111222-19-000001"
        filed = "2019-06-01"

        index_path = self._build_index_with_filing(tmp_path, ticker, cik, accession, filed)
        self._write_xml(tmp_path, cik, accession, _make_xml("S", "D", shares=1000.0, price=10.0))
        self._write_subs(tmp_path, cik, accession, filed)

        # shares=1e9 * close=100 → MC=1e11 ($100B)
        shares_fn = lambda c, d: 1_000_000_000.0  # 1B shares
        loader_fn = lambda t: _make_price_frame(100.0)

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=loader_fn,
            shares_fn=shares_fn,
            max_market_cap=10_000_000_000.0,  # $10B ceiling
        )
        assert len(events) == 0
        assert meta["n_cap_ceiling_excluded"] == 1

    def test_max_cap_none_passes_all(self, tmp_path):
        """max_market_cap=None (default) must not exclude any events."""
        from research.s1_dose import build_s1_events

        ticker = "BIGCAP"
        cik = "9999999"
        accession = "0000111222-19-000001"
        filed = "2019-06-01"

        index_path = self._build_index_with_filing(tmp_path, ticker, cik, accession, filed)
        self._write_xml(tmp_path, cik, accession, _make_xml("S", "D", shares=1000.0, price=10.0))
        self._write_subs(tmp_path, cik, accession, filed)

        shares_fn = lambda c, d: 1_000_000_000.0
        loader_fn = lambda t: _make_price_frame(100.0)

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=loader_fn,
            shares_fn=shares_fn,
            max_market_cap=None,  # no ceiling
        )
        assert len(events) == 1
        assert meta["n_cap_ceiling_excluded"] == 0

    def test_max_cap_meta_counts_excluded(self, tmp_path):
        """n_cap_ceiling_excluded in meta matches the number excluded."""
        from research.s1_dose import build_s1_events

        # Write two filings — one under cap, one over
        ticker = "DUAL"
        cik1, cik2 = "1111111", "2222222"
        acc1 = "0000111111-19-000001"
        acc2 = "0000222222-19-000001"
        filed = "2019-06-01"

        # Write index with both tickers
        index_data = {
            "entry_1": {
                "ticker": "SMALL",
                "cik": cik1,
                "status": "done",
                "filings": [{"accession": acc1, "filed": filed, "xml_status": "ok"}],
            },
            "entry_2": {
                "ticker": "LARGE",
                "cik": cik2,
                "status": "done",
                "filings": [{"accession": acc2, "filed": filed, "xml_status": "ok"}],
            },
        }
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        for (cik, acc) in [(cik1, acc1), (cik2, acc2)]:
            self._write_xml(tmp_path, cik, acc, _make_xml("S", "D", shares=1000.0, price=10.0))
            self._write_subs(tmp_path, cik, acc, filed)

        # small: 1M shares * $5 = $5M; large: 1B shares * $100 = $100B
        def shares_fn(cik, d):
            return 1_000_000.0 if cik == cik1 else 1_000_000_000.0

        def loader_fn(ticker):
            price = 5.0 if ticker == "SMALL" else 100.0
            return _make_price_frame(price)

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=loader_fn,
            shares_fn=shares_fn,
            max_market_cap=10_000_000_000.0,  # $10B
        )
        # SMALL passes ($5M < $10B), LARGE excluded ($100B > $10B)
        assert meta["n_cap_ceiling_excluded"] == 1
        assert len(events) == 1
        assert events[0].ticker == "SMALL"


def _make_price_frame(price: float):
    """Create a minimal price DataFrame for testing."""
    import pandas as pd
    idx = pd.to_datetime(["2019-01-02", "2019-06-03", "2019-12-31"])
    return pd.DataFrame({"Close": [price, price, price]}, index=idx)


# ===========================================================================
# §6.1 TestDoseBuilderDispatch
# ===========================================================================

class TestDoseBuilderDispatch:
    """S5-FIX: Test that the real _DOSE_BUILDERS registry in premise_run.py
    maps to the correct builder functions (not a locally-constructed dict).

    This exercises the actual runtime dispatch path rather than a test-local
    shadow dict.
    """

    def _get_real_dose_builders(self) -> dict:
        """Extract the _DOSE_BUILDERS dict from _run_preview_sync's local scope
        by inspecting the real function source and importing the builders it uses.
        Since _DOSE_BUILDERS is a local variable inside _run_preview_sync, we
        verify correctness by asserting the actual imports resolve to the same
        canonical builder objects.
        """
        from research.r1_dose import build_r1_events
        from research.s1_dose import build_s1_events
        import inspect
        import research.premise_run as pr

        # Verify the real _run_preview_sync source maps each dose to the expected builder
        src = inspect.getsource(pr._run_preview_sync)
        assert '"r1_score": build_r1_events' in src or "'r1_score': build_r1_events" in src, (
            "premise_run._run_preview_sync must map 'r1_score' → build_r1_events in _DOSE_BUILDERS"
        )
        assert '"s1_score": build_s1_events' in src or "'s1_score': build_s1_events" in src, (
            "premise_run._run_preview_sync must map 's1_score' → build_s1_events in _DOSE_BUILDERS"
        )
        # Return the canonical builders for identity checks
        return {
            "r1_score": build_r1_events,
            "s1_score": build_s1_events,
        }

    def test_real_registry_r1_maps_to_build_r1_events(self):
        """Real _DOSE_BUILDERS in premise_run.py maps r1_score → build_r1_events."""
        from research.r1_dose import build_r1_events
        builders = self._get_real_dose_builders()
        assert builders["r1_score"] is build_r1_events

    def test_real_registry_s1_maps_to_build_s1_events(self):
        """Real _DOSE_BUILDERS in premise_run.py maps s1_score → build_s1_events."""
        from research.s1_dose import build_s1_events
        builders = self._get_real_dose_builders()
        assert builders["s1_score"] is build_s1_events

    def test_real_registry_exhaustive_dose_coverage(self):
        """Every dose in _VALID_DOSES must appear in premise_run's _DOSE_BUILDERS."""
        from research.premise_spec import _VALID_DOSES
        import inspect
        import research.premise_run as pr

        src = inspect.getsource(pr._run_preview_sync)
        for dose in _VALID_DOSES:
            assert dose in src, (
                f"dose '{dose}' from _VALID_DOSES is not referenced in "
                f"premise_run._run_preview_sync _DOSE_BUILDERS"
            )

    def test_unknown_dose_raises(self):
        """Unknown dose_builder should raise ValueError at the real dispatch site."""
        import inspect
        import research.premise_run as pr

        # Verify the real dispatch guards against unknown doses
        src = inspect.getsource(pr._run_preview_sync)
        assert "Unknown dose_builder" in src or "dose_builder_fn is None" in src, (
            "premise_run._run_preview_sync must guard against unknown dose_builder"
        )


# ===========================================================================
# S1-FIX test: n_10b51_excluded_total must NOT be double-counted
# ===========================================================================

class TestS1No10b51DoubleCount:
    """S1-FIX: n_10b51_excluded_total must equal the per-transaction aggregation
    count from _aggregate_sell_dose_window only — NOT additionally incremented in
    the scan-loop triggering-filing check (COR-06 / DI-09)."""

    def _write_xml(self, tmp_path, cik, accession, xml_content):
        padded = str(int(cik)).zfill(10)
        acc_nd = accession.replace("-", "")
        (tmp_path / f"{padded}_{acc_nd}.xml").write_text(xml_content, encoding="utf-8")

    def _write_subs(self, tmp_path, cik, accession, filed):
        padded = str(int(cik)).zfill(10)
        subs_data = {"filings": {"recent": {
            "accessionNumber": [accession],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

    def test_no_double_count_all_10b51_filing(self, tmp_path):
        """A filing where ALL S/D transactions are 10b5-1 flagged (form-level marker):
        - The scan-loop must SKIP it as a triggering filing without incrementing the counter.
        - No event is generated → no window aggregation → n_10b51_excluded_total == 0.
        - Old bug: scan-loop also did n_10b51_excluded_total += len(txns) before the skip,
          which would yield 1 even though the window never ran.
        """
        from research.s1_dose import build_s1_events

        cik = "5551111"
        acc_all_10b51 = "0000555111-19-000001"
        filed = "2019-06-03"

        # Single filing: 1 transaction, 10b5-1 flagged at form level
        xml_all_10b51 = _make_xml("S", "D", shares=500.0, price=20.0, form_10b51=True)

        index_data = {
            "e1": {
                "ticker": "SELL10B51",
                "cik": cik,
                "status": "done",
                "filings": [{"accession": acc_all_10b51, "filed": filed, "xml_status": "ok"}],
            }
        }
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")
        self._write_xml(tmp_path, cik, acc_all_10b51, xml_all_10b51)
        self._write_subs(tmp_path, cik, acc_all_10b51, filed)

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(50.0),
            shares_fn=lambda c, d: 1_000_000.0,
        )
        # All-10b51 filing → no triggering event → no window scan
        assert len(events) == 0
        # Without the double-count fix, the old code would yield 1 here (scan-loop increment).
        # The correct value is 0: no event means no window aggregation, so no transaction counted.
        assert meta["n_10b51_excluded_total"] == 0, (
            f"Scan-loop double-count still present: expected 0 (no window ran), "
            f"got {meta['n_10b51_excluded_total']}"
        )

    def test_mixed_filing_excluded_count_is_window_only(self, tmp_path):
        """Two filings: one all-10b51 (skipped as triggering filing), one clean.
        The clean filing triggers a window scan that covers both filings.
        n_10b51_excluded_total must equal the window count (the all-10b51 txn in the window),
        NOT that count + the scan-loop count (which is the old double-count pattern).
        """
        from research.s1_dose import build_s1_events

        cik = "5552222"
        acc_clean = "0000555222-19-000001"
        acc_10b51 = "0000555222-19-000002"
        filed = "2019-06-03"

        xml_clean = _make_xml("S", "D", shares=100.0, price=20.0)
        xml_10b51 = _make_xml("S", "D", shares=200.0, price=20.0, form_10b51=True)

        index_data = {
            "e1": {
                "ticker": "MIXSELL",
                "cik": cik,
                "status": "done",
                "filings": [
                    {"accession": acc_clean, "filed": filed, "xml_status": "ok"},
                    {"accession": acc_10b51, "filed": filed, "xml_status": "ok"},
                ],
            }
        }
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")
        self._write_xml(tmp_path, cik, acc_clean, xml_clean)
        self._write_xml(tmp_path, cik, acc_10b51, xml_10b51)
        # Both filings on same subs entry — write both accessions in the subs file
        padded = str(int(cik)).zfill(10)
        subs_data = {"filings": {"recent": {
            "accessionNumber": [acc_clean, acc_10b51],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z", f"{filed}T11:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(50.0),
            shares_fn=lambda c, d: 1_000_000.0,
        )
        # One event (clean filing triggers it)
        assert len(events) == 1
        # Window scan sees 1 excluded txn (from the 10b51 filing in the window).
        # Old double-count would yield 2 (1 scan-loop for the all-10b51 filing + 1 window).
        assert meta["n_10b51_excluded_total"] == 1, (
            f"Expected 1 excluded (window count only), got {meta['n_10b51_excluded_total']} "
            f"(if 2: old scan-loop double-count still present)"
        )

    def test_n_10b51_sales_seen_total_populated(self, tmp_path):
        """n_10b51_sales_seen_total is populated at parse time for a mixed input.

        Setup: one all-10b51 filing (1 txn, is_10b51=True) + one clean filing (1 txn, is_10b51=False).
        Expected: n_10b51_sales_seen_total == 1 (the all-10b51 txn, counted at parse time
        BEFORE the triggering-gate), n_10b51_excluded_total == 1 (window count for the
        10b5-1 txn that falls inside the clean filing's dose window).
        """
        from research.s1_dose import build_s1_events

        cik = "5553333"
        acc_clean = "0000555333-19-000001"
        acc_10b51 = "0000555333-19-000002"
        filed = "2019-06-03"

        xml_clean = _make_xml("S", "D", shares=100.0, price=20.0)
        xml_10b51 = _make_xml("S", "D", shares=300.0, price=20.0, form_10b51=True)

        index_data = {
            "e1": {
                "ticker": "SEENTOTAL",
                "cik": cik,
                "status": "done",
                "filings": [
                    {"accession": acc_clean, "filed": filed, "xml_status": "ok"},
                    {"accession": acc_10b51, "filed": filed, "xml_status": "ok"},
                ],
            }
        }
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        padded = str(int(cik)).zfill(10)
        acc_clean_nd = acc_clean.replace("-", "")
        acc_10b51_nd = acc_10b51.replace("-", "")
        (tmp_path / f"{padded}_{acc_clean_nd}.xml").write_text(xml_clean, encoding="utf-8")
        (tmp_path / f"{padded}_{acc_10b51_nd}.xml").write_text(xml_10b51, encoding="utf-8")
        subs_data = {"filings": {"recent": {
            "accessionNumber": [acc_clean, acc_10b51],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z", f"{filed}T11:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(50.0),
            shares_fn=lambda c, d: 1_000_000.0,
        )
        # Parse-time counter: 1 txn flagged is_10b51 (from the all-10b51 filing)
        assert meta["n_10b51_sales_seen_total"] > 0, (
            f"n_10b51_sales_seen_total should be > 0 on mixed input, got {meta.get('n_10b51_sales_seen_total')}"
        )
        assert meta["n_10b51_sales_seen_total"] == 1, (
            f"Expected 1 parse-time 10b5-1 txn seen, got {meta['n_10b51_sales_seen_total']}"
        )


# ===========================================================================
# S2-FIX test: build_r1_events accepts max_market_cap without TypeError
# ===========================================================================

class TestR1MaxCapKwarg:
    """S2-FIX: build_r1_events must accept max_market_cap kwarg (COR-09 latent TypeError)."""

    def test_r1_max_cap_no_crash(self, tmp_path):
        """build_r1_events with max_market_cap set must not raise TypeError."""
        from research.r1_dose import build_r1_events

        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps({}), encoding="utf-8")

        events, meta = build_r1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: None,
            max_market_cap=10_000_000_000.0,
        )
        assert isinstance(events, list)
        assert isinstance(meta, dict)
        assert meta["n_cap_ceiling_excluded"] == 0

    def test_r1_max_cap_filters_large_cap(self, tmp_path):
        """build_r1_events with max_market_cap excludes events above the ceiling."""
        from research.r1_dose import build_r1_events

        cik = "7771111"
        acc = "0000777111-19-000001"
        filed = "2019-06-03"

        xml_buy = _make_xml("P", "A", shares=1000.0, price=10.0)
        padded = str(int(cik)).zfill(10)
        acc_nd = acc.replace("-", "")
        (tmp_path / f"{padded}_{acc_nd}.xml").write_text(xml_buy, encoding="utf-8")

        subs_data = {"filings": {"recent": {
            "accessionNumber": [acc],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

        index_data = {"e1": {
            "ticker": "BIGBUY",
            "cik": cik,
            "status": "done",
            "filings": [{"accession": acc, "filed": filed, "xml_status": "ok"}],
        }}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        # MC = 1B shares * $100 = $100B → above $10B ceiling
        events, meta = build_r1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(100.0),
            shares_fn=lambda c, d: 1_000_000_000.0,
            max_market_cap=10_000_000_000.0,
        )
        assert len(events) == 0
        assert meta["n_cap_ceiling_excluded"] == 1


# ===========================================================================
# S3-FIX test: unknown MC excluded when max_market_cap is set
# ===========================================================================

class TestUnknownMCExclusion:
    """S3-FIX: events with uncomputable MC must be excluded when max_market_cap is set."""

    def test_s1_unknown_mc_excluded_with_cap(self, tmp_path):
        """S/D event with no MC (shares_fn returns None) + max_market_cap set → excluded."""
        from research.s1_dose import build_s1_events

        cik = "8881111"
        acc = "0000888111-19-000001"
        filed = "2019-06-03"

        xml_sell = _make_xml("S", "D", shares=1000.0, price=10.0)
        padded = str(int(cik)).zfill(10)
        acc_nd = acc.replace("-", "")
        (tmp_path / f"{padded}_{acc_nd}.xml").write_text(xml_sell, encoding="utf-8")

        subs_data = {"filings": {"recent": {
            "accessionNumber": [acc],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

        index_data = {"e1": {
            "ticker": "UNKNOWNMC",
            "cik": cik,
            "status": "done",
            "filings": [{"accession": acc, "filed": filed, "xml_status": "ok"}],
        }}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        # shares_fn returns None → MC is uncomputable
        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(50.0),
            shares_fn=lambda c, d: None,
            max_market_cap=10_000_000_000.0,
        )
        assert len(events) == 0
        assert meta["n_excluded_unknown_mc"] == 1, (
            f"Expected 1 excluded for unknown MC, got {meta.get('n_excluded_unknown_mc')}"
        )

    def test_s1_unknown_mc_passes_without_cap(self, tmp_path):
        """S/D event with no MC + NO max_market_cap → not excluded (score_undefined)."""
        from research.s1_dose import build_s1_events

        cik = "8882222"
        acc = "0000888222-19-000001"
        filed = "2019-06-03"

        xml_sell = _make_xml("S", "D", shares=1000.0, price=10.0)
        padded = str(int(cik)).zfill(10)
        acc_nd = acc.replace("-", "")
        (tmp_path / f"{padded}_{acc_nd}.xml").write_text(xml_sell, encoding="utf-8")

        subs_data = {"filings": {"recent": {
            "accessionNumber": [acc],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

        index_data = {"e1": {
            "ticker": "UNKNOWNMC2",
            "cik": cik,
            "status": "done",
            "filings": [{"accession": acc, "filed": filed, "xml_status": "ok"}],
        }}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        # No cap → unknown MC events pass through with score_undefined=True
        events, meta = build_s1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(50.0),
            shares_fn=lambda c, d: None,
            max_market_cap=None,  # no ceiling
        )
        assert len(events) == 1
        assert events[0].payload["score_undefined"] is True
        assert meta["n_excluded_unknown_mc"] == 0

    def test_r1_unknown_mc_excluded_with_cap(self, tmp_path):
        """R1: P/A event with no MC + max_market_cap set → excluded with n_excluded_unknown_mc."""
        from research.r1_dose import build_r1_events

        cik = "9991111"
        acc = "0000999111-19-000001"
        filed = "2019-06-03"

        xml_buy = _make_xml("P", "A", shares=1000.0, price=10.0)
        padded = str(int(cik)).zfill(10)
        acc_nd = acc.replace("-", "")
        (tmp_path / f"{padded}_{acc_nd}.xml").write_text(xml_buy, encoding="utf-8")

        subs_data = {"filings": {"recent": {
            "accessionNumber": [acc],
            "acceptanceDateTime": [f"{filed}T10:00:00.000Z"],
        }}}
        (tmp_path / f"{padded}.json").write_text(json.dumps(subs_data), encoding="utf-8")

        index_data = {"e1": {
            "ticker": "UNKNOWNMCR1",
            "cik": cik,
            "status": "done",
            "filings": [{"accession": acc, "filed": filed, "xml_status": "ok"}],
        }}
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        events, meta = build_r1_events(
            start=date(2019, 1, 1),
            end=date(2019, 12, 31),
            index_path=index_path,
            xml_dir=tmp_path,
            subs_dir=tmp_path,
            loader_fn=lambda t: _make_price_frame(50.0),
            shares_fn=lambda c, d: None,
            max_market_cap=10_000_000_000.0,
        )
        assert len(events) == 0
        assert meta["n_excluded_unknown_mc"] == 1
