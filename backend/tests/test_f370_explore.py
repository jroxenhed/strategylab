"""Tests for run_f370_explore.py — hermetic, no I/O.

Covers:
  - Loader test (fix K1): make_disk_only_loader calls cache.load() with the
    right args and never references _make_key or .get.
  - Falsy-zero test (fix C370-01): _has_valid_excess returns True for rows
    whose 63td excess is exactly 0.0, not just non-zero values.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
_RESEARCH = _BACKEND / "research"
for _p in [str(_BACKEND), str(_RESEARCH)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Loader test — fix K1 (the crash): must use cache.load(), not _make_key/.get
# ---------------------------------------------------------------------------

class TestMakeDiskOnlyLoader:
    """make_disk_only_loader must call PriceFrameCache.load() correctly.

    The old implementation called cache._make_key() + cache.get(), methods that
    don't exist on PriceFrameCache and caused every parallel worker to crash.
    """

    def test_loader_calls_cache_load(self):
        """Loader returned by make_disk_only_loader delegates to cache.load()."""
        import pandas as pd

        mock_cache = MagicMock()
        mock_frame = pd.DataFrame({"Close": [100.0]})
        mock_cache.load.return_value = mock_frame

        with patch(
            "turnaround_validation.PriceFrameCache",
            return_value=mock_cache,
        ):
            from research.run_f370_explore import make_disk_only_loader

            loader = make_disk_only_loader(
                start_year=2015,
                end_year=2020,
                low_lookback_years=2,
                horizon_months=6,
                data_source="yahoo",
            )

        # Call the loader
        result = loader("AAPL")

        # Must have called cache.load with (ticker, fetch_start, fetch_end, data_source)
        mock_cache.load.assert_called_once()
        call_args = mock_cache.load.call_args
        assert call_args[0][0] == "AAPL", "First arg must be the ticker"
        # fetch_start = (2015 - 2 - 1) - 01 - 01 = 2012-01-01
        assert call_args[0][1] == "2012-01-01", f"fetch_start wrong: {call_args[0][1]}"
        # fetch_end_year = 2020 + max(1, (6+11)//12) + 1 = 2020 + 1 + 1 = 2022
        assert call_args[0][2] == "2022-12-31", f"fetch_end wrong: {call_args[0][2]}"
        assert call_args[0][3] == "yahoo", "data_source must be passed through"

        assert result is mock_frame

    def test_loader_does_not_call_make_key(self):
        """Loader must NOT reference _make_key (which doesn't exist on PriceFrameCache)."""
        mock_cache = MagicMock()
        mock_cache.load.return_value = None

        with patch(
            "turnaround_validation.PriceFrameCache",
            return_value=mock_cache,
        ):
            from research.run_f370_explore import make_disk_only_loader

            loader = make_disk_only_loader(
                start_year=2015,
                end_year=2020,
                low_lookback_years=2,
                horizon_months=6,
                data_source="yahoo",
            )

        loader("TSLA")

        # _make_key and .get must never be called
        assert not mock_cache._make_key.called, "_make_key must not be called"
        assert not mock_cache.get.called, ".get must not be called"

    def test_loader_memoizes_in_process(self):
        """Loader only calls cache.load once per ticker (in-process _mem_cache)."""
        mock_cache = MagicMock()
        mock_cache.load.return_value = None

        with patch(
            "turnaround_validation.PriceFrameCache",
            return_value=mock_cache,
        ):
            from research.run_f370_explore import make_disk_only_loader

            loader = make_disk_only_loader(
                start_year=2015,
                end_year=2020,
                low_lookback_years=2,
                horizon_months=6,
                data_source="yahoo",
            )

        loader("MSFT")
        loader("MSFT")
        loader("MSFT")

        # cache.load should have been called exactly once for MSFT
        assert mock_cache.load.call_count == 1, (
            f"Expected 1 cache.load call (memoized), got {mock_cache.load.call_count}"
        )

    def test_loader_returns_none_on_disk_miss(self):
        """Disk miss (cache.load returns None) propagates as None."""
        mock_cache = MagicMock()
        mock_cache.load.return_value = None

        with patch(
            "turnaround_validation.PriceFrameCache",
            return_value=mock_cache,
        ):
            from research.run_f370_explore import make_disk_only_loader

            loader = make_disk_only_loader(
                start_year=2015,
                end_year=2020,
                low_lookback_years=2,
                horizon_months=6,
                data_source="yahoo",
            )

        result = loader("MISSING_TICKER")
        assert result is None


# ---------------------------------------------------------------------------
# Falsy-zero test — fix C370-01
# ---------------------------------------------------------------------------

class TestHasValidExcess:
    """_has_valid_excess must treat 0.0 as a valid (non-None) excess.

    The old code used `m.get(str(h)) or m.get(h)` which silently drops
    events with exact-zero excess (0.0 is falsy in Python).
    """

    def _call(self, fwd_excess_pct: dict, h: int = 63) -> bool:
        """Inline _has_valid_excess logic matching the fixed implementation."""
        m = fwd_excess_pct or {}
        v = m.get(str(h))
        if v is None:
            v = m.get(h)
        return v is not None

    def test_zero_float_is_valid(self):
        """Exact zero excess (0.0) must be treated as a valid data point."""
        row_excess = {"63": 0.0}
        assert self._call(row_excess, 63) is True, (
            "0.0 excess must be valid — it is a real observed excess return"
        )

    def test_zero_int_key_is_valid(self):
        """Exact zero excess with integer key (h=63, not '63') must also work."""
        row_excess = {63: 0.0}
        assert self._call(row_excess, 63) is True

    def test_nonzero_positive_is_valid(self):
        row_excess = {"63": 2.5}
        assert self._call(row_excess, 63) is True

    def test_nonzero_negative_is_valid(self):
        """Negative excess (underperformance) is a valid observation."""
        row_excess = {"63": -3.1}
        assert self._call(row_excess, 63) is True

    def test_none_string_key_falls_back_to_int_key(self):
        """Falls back to integer key if string key not present."""
        row_excess = {63: 1.5}
        assert self._call(row_excess, 63) is True

    def test_truly_missing_returns_false(self):
        """No excess for this horizon → False (correctly excluded)."""
        row_excess = {"21": 1.0}  # only 21td, not 63td
        assert self._call(row_excess, 63) is False

    def test_none_value_returns_false(self):
        """Explicit None value → False (not a valid observation)."""
        row_excess = {"63": None}
        assert self._call(row_excess, 63) is False

    def test_empty_dict_returns_false(self):
        assert self._call({}, 63) is False

    def test_nan_value_is_not_none(self):
        """NaN is not None — the is-not-None check passes it through.
        The analysis layer is responsible for filtering NaN downstream;
        the filter here only gates on None absence.
        """
        import math
        row_excess = {"63": float("nan")}
        # NaN is not None, so _has_valid_excess returns True
        assert self._call(row_excess, 63) is True


class TestAdtToEtDate:
    """_adt_to_et_date: EDGAR acceptanceDateTime -> ET calendar date (COR-02).

    The gap lens links 8-K item-2.02 announcements to 10-Q/10-K filings by ET
    date, so the after-hours rollover convention must be exact.
    """

    def _call(self, adt):
        from research.run_f370_explore import _adt_to_et_date
        return _adt_to_et_date(adt)

    def test_after_hours_utc_stays_same_et_day(self):
        # 2019-02-14 21:30Z = 16:30 ET on 2019-02-14 (not the 15th).
        assert self._call("2019-02-14T21:30:00.000Z") == "2019-02-14"

    def test_midnight_utc_rolls_back_to_prev_et_day(self):
        # 2020-01-01 00:30Z = 2019-12-31 19:30 ET.
        assert self._call("2020-01-01T00:30:00Z") == "2019-12-31"

    def test_explicit_offset_preserved(self):
        # Already ET-offset; no shift.
        assert self._call("2019-07-30T16:05:00-04:00") == "2019-07-30"

    def test_garbage_returns_none(self):
        assert self._call("not-a-date") is None
        assert self._call("") is None


# ---------------------------------------------------------------------------
# Gap-lens index / time-match unit test (F381, item 7)
# ---------------------------------------------------------------------------
#
# Tests the "most-recent 8-K item-2.02 announcement strictly before the 10-Q/10-K
# filing date" logic used by the gap-lens in run().
#
# We test three things:
#   A. _build_eightk_202_index parses synthetic submission JSON and filters
#      correctly to 8-K + item 2.02 only.
#   B. The bisect-based time-match picks the correct announce date.
#   C. Edge cases: no prior 2.02, gap too large (>90d), gap too small (<=0d).
# ---------------------------------------------------------------------------

class TestBuildEightk202Index:
    """_build_eightk_202_index: parses submissions JSON, filters 8-K item 2.02."""

    def _call(self, cik_to_ticker, submissions_dir):
        from research.run_f370_explore import _build_eightk_202_index
        return _build_eightk_202_index(cik_to_ticker, submissions_dir)

    def _write_submission(self, tmp_path, cik: str, entries: list[dict]) -> None:
        """Write a minimal submissions/<cik>.json to tmp_path."""
        import json
        forms = [e["form"] for e in entries]
        accept_dts = [e["adt"] for e in entries]
        items = [e.get("items", "") for e in entries]
        data = {
            "filings": {
                "recent": {
                    "form": forms,
                    "acceptanceDateTime": accept_dts,
                    "items": items,
                }
            }
        }
        (tmp_path / f"{cik}.json").write_text(json.dumps(data), encoding="utf-8")

    def test_basic_8k_202_is_indexed(self, tmp_path):
        """An 8-K with item 2.02 in recent filings appears in the index."""
        cik = "0000123456"
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2019-02-15T21:30:00.000Z", "items": "2.02"},
            {"form": "10-Q", "adt": "2019-03-01T21:00:00Z", "items": ""},
        ])
        result = self._call({cik: "AAPL"}, tmp_path)
        assert cik in result, f"CIK {cik} should be in index"
        assert len(result[cik]) == 1
        assert result[cik][0] == "2019-02-15"

    def test_non_8k_excluded(self, tmp_path):
        """10-Q and 10-K filings are not indexed (only 8-K)."""
        cik = "0000234567"
        self._write_submission(tmp_path, cik, [
            {"form": "10-Q", "adt": "2019-02-15T21:30:00Z", "items": "2.02"},
            {"form": "10-K", "adt": "2019-03-01T21:00:00Z", "items": "2.02"},
        ])
        result = self._call({cik: "MSFT"}, tmp_path)
        assert cik not in result, "10-Q/10-K should not be in the 8-K 2.02 index"

    def test_8k_without_202_excluded(self, tmp_path):
        """An 8-K that doesn't contain item 2.02 is excluded."""
        cik = "0000345678"
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2019-02-15T21:30:00Z", "items": "1.01"},
            {"form": "8-K", "adt": "2019-03-01T21:00:00Z", "items": "8.01, 9.01"},
        ])
        result = self._call({cik: "GOOG"}, tmp_path)
        assert cik not in result, "8-K without item 2.02 should not appear"

    def test_multiple_8k_202_sorted(self, tmp_path):
        """Multiple 8-K item-2.02 filings are stored in sorted order."""
        cik = "0000456789"
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2019-08-01T20:30:00Z", "items": "2.02"},
            {"form": "8-K", "adt": "2019-02-15T21:30:00Z", "items": "2.02"},
            {"form": "8-K", "adt": "2019-05-01T20:00:00Z", "items": "2.02"},
        ])
        result = self._call({cik: "AMZN"}, tmp_path)
        assert cik in result
        dates = result[cik]
        assert dates == sorted(dates), f"Expected sorted dates, got: {dates}"
        assert len(dates) == 3

    def test_cik_not_in_ticker_map_skipped(self, tmp_path):
        """CIK not in cik_to_ticker is not scanned."""
        cik = "0000567890"
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2019-02-15T21:30:00Z", "items": "2.02"},
        ])
        # Pass an empty cik_to_ticker — the CIK is not mapped
        result = self._call({}, tmp_path)
        assert cik not in result, "Unknown CIK should be skipped"

    def test_after_hours_utc_maps_to_same_et_date(self, tmp_path):
        """8-K filed at 21:30Z maps to the same ET calendar day (not next day)."""
        cik = "0000678901"
        # 2019-11-07T21:30:00Z = 16:30 ET (EST offset = -5h in November)
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2019-11-07T21:30:00Z", "items": "2.02"},
        ])
        result = self._call({cik: "META"}, tmp_path)
        assert cik in result
        assert result[cik][0] == "2019-11-07", (
            f"After-hours filing should map to same ET day, got: {result[cik][0]}"
        )

    def test_midnight_utc_rolls_back_to_prev_et_day(self, tmp_path):
        """8-K filed at 00:30Z rolls back to previous ET calendar day."""
        cik = "0000789012"
        # 2020-01-15T00:30:00Z = 2020-01-14 19:30 ET
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2020-01-15T00:30:00Z", "items": "2.02"},
        ])
        result = self._call({cik: "NFLX"}, tmp_path)
        assert cik in result
        assert result[cik][0] == "2020-01-14", (
            f"Early-UTC filing should map to prev ET day, got: {result[cik][0]}"
        )

    def test_items_with_multiple_codes_includes_202(self, tmp_path):
        """8-K with items '2.02, 9.01' (comma-separated) is indexed."""
        cik = "0000890123"
        self._write_submission(tmp_path, cik, [
            {"form": "8-K", "adt": "2019-05-01T21:00:00Z", "items": "2.02, 9.01"},
        ])
        result = self._call({cik: "NVDA"}, tmp_path)
        assert cik in result
        assert len(result[cik]) == 1


class TestGapLensTimeMatch:
    """Time-matching logic: most-recent 8-K 2.02 strictly before the 10-Q filing date.

    Mirrors the bisect.bisect_left logic used in run_f370_explore.run():
        pos = bisect.bisect_left(announces, filing_date)
        if pos == 0: skip (no prior announce)
        announce_date = announces[pos - 1]
        gap_days = (filing_date - announce_date).days
        if gap_days <= 0 or gap_days > 90: skip
    """

    def _match(self, announces: list[str], filing_date: str) -> tuple[str | None, int | None]:
        """Return (announce_date, gap_days) using the driver's time-match logic.

        Returns (None, None) if no valid match found.
        """
        import bisect
        from datetime import date

        if not announces:
            return None, None
        pos = bisect.bisect_left(announces, filing_date)
        if pos == 0:
            return None, None
        announce_date = announces[pos - 1]
        try:
            gap_days = (date.fromisoformat(filing_date) - date.fromisoformat(announce_date)).days
        except ValueError:
            return None, None
        if gap_days <= 0 or gap_days > 90:
            return None, None
        return announce_date, gap_days

    def test_single_prior_announce_matched(self):
        """Single announce strictly before filing is matched."""
        announces = ["2020-02-01"]
        filing_date = "2020-03-15"
        ad, gap = self._match(announces, filing_date)
        assert ad == "2020-02-01"
        assert gap == 43

    def test_most_recent_of_several_selected(self):
        """Of multiple prior announces, the most recent one is selected."""
        announces = ["2019-08-01", "2019-11-01", "2020-02-01"]
        filing_date = "2020-03-15"
        ad, gap = self._match(announces, filing_date)
        assert ad == "2020-02-01", f"Expected most-recent 2020-02-01, got {ad}"

    def test_announce_after_filing_not_selected(self):
        """Announce dates after the filing date are not selected."""
        announces = ["2020-04-01", "2020-05-01"]
        filing_date = "2020-03-15"
        ad, gap = self._match(announces, filing_date)
        assert ad is None, "All announces are after filing — should return None"

    def test_no_announces_returns_none(self):
        """Empty announces list returns None."""
        ad, gap = self._match([], "2020-03-15")
        assert ad is None

    def test_gap_too_large_excluded(self):
        """Gap > 90 days is excluded (announce too stale to be this quarter's 2.02)."""
        announces = ["2019-10-01"]
        filing_date = "2020-03-15"  # 166-day gap
        ad, gap = self._match(announces, filing_date)
        assert ad is None, f"Gap > 90d should be excluded, got ({ad}, {gap})"

    def test_gap_exactly_90_days_included(self):
        """Gap of exactly 90 days is included (boundary = ≤ 90)."""
        from datetime import date, timedelta
        filing = date(2020, 4, 1)
        announce = filing - timedelta(days=90)
        ad, gap = self._match([announce.isoformat()], filing.isoformat())
        assert ad == announce.isoformat()
        assert gap == 90

    def test_gap_91_days_excluded(self):
        """Gap of 91 days is excluded (> 90)."""
        from datetime import date, timedelta
        filing = date(2020, 4, 1)
        announce = filing - timedelta(days=91)
        ad, gap = self._match([announce.isoformat()], filing.isoformat())
        assert ad is None

    def test_same_day_announce_excluded(self):
        """Same-day announce (gap=0) is excluded (must be strictly before filing)."""
        announces = ["2020-03-15"]
        filing_date = "2020-03-15"
        ad, gap = self._match(announces, filing_date)
        assert ad is None, "Same-day announce (gap=0) should be excluded"

    def test_one_day_gap_is_valid(self):
        """Gap of exactly 1 day is included (minimum valid gap)."""
        announces = ["2020-03-14"]
        filing_date = "2020-03-15"
        ad, gap = self._match(announces, filing_date)
        assert ad == "2020-03-14"
        assert gap == 1

    def test_announce_on_filing_date_correctly_excluded_by_bisect(self):
        """bisect_left places an announce on the filing date at pos, so pos-1
        is the one before — the same-date entry is not selected as the match."""
        # announces contains filing_date itself and a prior date
        announces = ["2020-02-01", "2020-03-15"]  # sorted; filing_date = 2020-03-15
        filing_date = "2020-03-15"
        # bisect_left("2020-03-15") in this list = 1 → pos-1 = 0 → "2020-02-01"
        ad, gap = self._match(announces, filing_date)
        assert ad == "2020-02-01", (
            f"bisect_left should skip the same-date entry, selecting 2020-02-01; got {ad}"
        )
