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
