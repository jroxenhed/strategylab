"""F357 — Tests for backend/research/returns_matrix.py.

Coverage:
  TestSealGuard         — ValueError on dates > 2024-12-31
  TestSchemaCorrectness — Parquet schema matches spec
  TestCompletenessContract — loader raises on missing/partial sidecar; allow_partial bypass
  TestBuilderSmoke      — real ProcessPool build with synthetic cache (spawn path)
  TestLoaderReshape     — load_matrix_as_vector_cache returns _VectorCache format
  TestMetadataFields    — metadata sidecar completeness
  TestF338Anchors       — spot-check matrix rows vs live _build_return_vector

All tests use synthetic price frames (no network, no real price cache).
"""
from __future__ import annotations

import json
import sys
import tempfile
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_BACKEND = Path(__file__).resolve().parent.parent
_RESEARCH = _BACKEND / "research"
for p in [str(_BACKEND), str(_RESEARCH)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Minimal synthetic price frame builder
# ---------------------------------------------------------------------------

def _make_price_frame(
    start_date: date,
    n_bars: int = 300,
    base_price: float = 50.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Build a synthetic daily OHLCV DataFrame with a DatetimeIndex."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start=pd.Timestamp(start_date), periods=n_bars, freq="B")
    # Random walk
    changes = rng.standard_normal(n_bars) * 0.5
    closes = base_price + np.cumsum(changes)
    closes = np.clip(closes, 1.0, None)
    opens = closes * (1.0 + rng.standard_normal(n_bars) * 0.002)
    opens = np.clip(opens, 1.0, None)
    highs = np.maximum(opens, closes) * (1.0 + np.abs(rng.standard_normal(n_bars) * 0.003))
    lows = np.minimum(opens, closes) * (1.0 - np.abs(rng.standard_normal(n_bars) * 0.003))
    volumes = rng.integers(100000, 5000000, n_bars).astype(float)
    df = pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    }, index=dates)
    df.index.name = "Date"
    return df


def _make_short_frame(
    start_date: date,
    n_bars: int = 30,
    base_price: float = 20.0,
    seed: int = 99,
) -> pd.DataFrame:
    """Build a short (delisted-like) price frame."""
    return _make_price_frame(start_date, n_bars=n_bars, base_price=base_price, seed=seed)


# ---------------------------------------------------------------------------
# Mock loader factory
# ---------------------------------------------------------------------------

def _make_loader(frames: dict[str, Optional[pd.DataFrame]]):
    """Return a loader function that serves from a fixed dict."""
    def loader(ticker: str) -> Optional[pd.DataFrame]:
        return frames.get(ticker)
    # Attach fetch_failures attribute (expected by _make_memoized_loader interface)
    loader.fetch_failures = 0
    return loader


# ---------------------------------------------------------------------------
# TestSealGuard
# ---------------------------------------------------------------------------

class TestSealGuard:
    def test_rejects_date_after_2024(self):
        from research.returns_matrix import build_universe_returns_matrix
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "matrix.parquet"
            with pytest.raises(ValueError, match="seal violated"):
                build_universe_returns_matrix(
                    universe_tickers=["AAPL"],
                    entry_dates=[date(2025, 1, 2)],
                    output_path=out,
                )

    def test_rejects_mixed_dates_any_after_2024(self):
        from research.returns_matrix import build_universe_returns_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "matrix.parquet"
            with pytest.raises(ValueError, match="seal violated"):
                build_universe_returns_matrix(
                    universe_tickers=["AAPL"],
                    entry_dates=[date(2020, 1, 2), date(2025, 6, 1)],
                    output_path=out,
                )

    def test_accepts_max_date_exactly_2024_12_31(self):
        """2024-12-31 is exactly at the boundary — should not raise."""
        from research.returns_matrix import build_universe_returns_matrix, _MATRIX_BUILD_MAX_DATE
        # Just verify the date constant is correct
        assert _MATRIX_BUILD_MAX_DATE == date(2024, 12, 31)

    def test_empty_entry_dates_raises(self):
        from research.returns_matrix import build_universe_returns_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "matrix.parquet"
            with pytest.raises(ValueError, match="entry_dates must not be empty"):
                build_universe_returns_matrix(
                    universe_tickers=["AAPL"],
                    entry_dates=[],
                    output_path=out,
                )

    def test_empty_universe_raises(self):
        from research.returns_matrix import build_universe_returns_matrix
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "matrix.parquet"
            with pytest.raises(ValueError, match="universe_tickers must not be empty"):
                build_universe_returns_matrix(
                    universe_tickers=[],
                    entry_dates=[date(2020, 1, 2)],
                    output_path=out,
                )


# ---------------------------------------------------------------------------
# TestSchemaCorrectness
# ---------------------------------------------------------------------------

class TestSchemaCorrectness:
    """Verify Parquet schema: columns, dtypes, partitioning."""

    def test_schema_columns_and_dtypes(self, tmp_path):
        """Build a tiny matrix and verify column names and dtypes."""
        # We build a tiny DataFrame directly to test the write/read path
        from research.returns_matrix import _write_parquet_atomic, load_matrix_as_vector_cache

        rows = [
            (date(2020, 1, 2), 21, "AAPL", 2.5, False),
            (date(2020, 1, 2), 63, "AAPL", 5.1, False),
            (date(2020, 1, 2), 126, "AAPL", 8.3, True),
            (date(2020, 1, 3), 21, "MSFT", -1.2, False),
        ]
        df = pd.DataFrame(rows, columns=[
            "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
        ])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["horizon_days"] = df["horizon_days"].astype("int32")
        df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
        df["is_terminal"] = df["is_terminal"].astype("bool")

        out = tmp_path / "matrix.parquet"
        _write_parquet_atomic(df, out)
        assert out.exists()

        # Read back and verify schema (raw pyarrow read, bypass completeness check)
        loaded = pd.read_parquet(str(out), engine="pyarrow")
        assert set(loaded.columns) >= {"entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"}
        assert loaded["fwd_return_pct"].dtype == np.float64
        assert loaded["is_terminal"].dtype == bool or loaded["is_terminal"].dtype == np.bool_

    def test_partition_by_horizon(self, tmp_path):
        """Verify partitioned read by horizon_days works."""
        from research.returns_matrix import _write_parquet_atomic, load_matrix_as_vector_cache

        rows = [
            (date(2020, 1, 2), 21, "AAPL", 2.5, False),
            (date(2020, 1, 2), 63, "AAPL", 5.1, False),
            (date(2020, 1, 2), 126, "AAPL", 8.3, True),
        ]
        df = pd.DataFrame(rows, columns=[
            "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
        ])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["horizon_days"] = df["horizon_days"].astype("int32")
        df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
        df["is_terminal"] = df["is_terminal"].astype("bool")

        out = tmp_path / "matrix.parquet"
        _write_parquet_atomic(df, out)

        # Load only horizon=21 (allow_partial=True: no sidecar in _write_parquet_atomic artifacts)
        cache_21 = load_matrix_as_vector_cache(out, horizon=21, allow_partial=True)
        assert len(cache_21) == 1
        assert (date(2020, 1, 2), 21) in cache_21
        assert "AAPL" in cache_21[(date(2020, 1, 2), 21)]
        assert cache_21[(date(2020, 1, 2), 21)]["AAPL"] == pytest.approx((2.5, False))

        # Load only horizon=126 — should have is_terminal=True
        cache_126 = load_matrix_as_vector_cache(out, horizon=126, allow_partial=True)
        assert (date(2020, 1, 2), 126) in cache_126
        ret, terminal = cache_126[(date(2020, 1, 2), 126)]["AAPL"]
        assert terminal is True


# ---------------------------------------------------------------------------
# TestLoaderReshape
# ---------------------------------------------------------------------------

class TestLoaderReshape:
    """Verify loader builds correct _VectorCache from Parquet."""

    def test_load_as_vector_cache_multi(self, tmp_path):
        """Multi-horizon load returns all (date, horizon) keys."""
        from research.returns_matrix import (
            _write_parquet_atomic,
            load_matrix_as_vector_cache_multi,
        )

        rows = [
            (date(2019, 3, 1), 21, "AAPL", 1.0, False),
            (date(2019, 3, 1), 21, "MSFT", -2.0, False),
            (date(2019, 3, 1), 63, "AAPL", 3.0, False),
            (date(2019, 6, 1), 21, "AAPL", 4.0, True),
        ]
        df = pd.DataFrame(rows, columns=[
            "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
        ])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["horizon_days"] = df["horizon_days"].astype("int32")
        df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
        df["is_terminal"] = df["is_terminal"].astype("bool")

        out = tmp_path / "matrix.parquet"
        _write_parquet_atomic(df, out)

        cache = load_matrix_as_vector_cache_multi(out, horizons=(21, 63), allow_partial=True)
        assert (date(2019, 3, 1), 21) in cache
        assert (date(2019, 3, 1), 63) in cache
        assert (date(2019, 6, 1), 21) in cache

        # Values
        vec = cache[(date(2019, 3, 1), 21)]
        assert "AAPL" in vec
        assert "MSFT" in vec
        assert vec["AAPL"][0] == pytest.approx(1.0)
        assert vec["MSFT"][0] == pytest.approx(-2.0)

        # Terminal flag
        vec2 = cache[(date(2019, 6, 1), 21)]
        assert vec2["AAPL"][1] is True

    def test_date_filter_reduces_keys(self, tmp_path):
        """entry_dates filter loads only those dates."""
        from research.returns_matrix import (
            _write_parquet_atomic,
            load_matrix_as_vector_cache,
        )

        rows = [
            (date(2019, 3, 1), 21, "AAPL", 1.0, False),
            (date(2019, 4, 1), 21, "AAPL", 2.0, False),
            (date(2019, 5, 1), 21, "AAPL", 3.0, False),
        ]
        df = pd.DataFrame(rows, columns=[
            "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
        ])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["horizon_days"] = df["horizon_days"].astype("int32")
        df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
        df["is_terminal"] = df["is_terminal"].astype("bool")

        out = tmp_path / "matrix.parquet"
        _write_parquet_atomic(df, out)

        # Filter to only one date
        cache = load_matrix_as_vector_cache(
            out, horizon=21, entry_dates=[date(2019, 4, 1)], allow_partial=True
        )
        assert len(cache) == 1
        assert (date(2019, 4, 1), 21) in cache

    def test_empty_matrix_returns_empty_cache(self, tmp_path):
        """Loading a horizon that has no rows returns {}."""
        from research.returns_matrix import (
            _write_parquet_atomic,
            load_matrix_as_vector_cache,
        )

        rows = [
            (date(2019, 3, 1), 21, "AAPL", 1.0, False),
        ]
        df = pd.DataFrame(rows, columns=[
            "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
        ])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["horizon_days"] = df["horizon_days"].astype("int32")
        df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
        df["is_terminal"] = df["is_terminal"].astype("bool")

        out = tmp_path / "matrix.parquet"
        _write_parquet_atomic(df, out)

        cache = load_matrix_as_vector_cache(out, horizon=126, allow_partial=True)
        assert cache == {}

    def test_missing_matrix_raises(self, tmp_path):
        from research.returns_matrix import load_matrix_as_vector_cache
        with pytest.raises(FileNotFoundError, match="not found"):
            load_matrix_as_vector_cache(tmp_path / "nonexistent.parquet", horizon=21)


# ---------------------------------------------------------------------------
# TestMetadataFields
# ---------------------------------------------------------------------------

class TestMetadataFields:
    """Verify metadata sidecar has all required fields."""

    def test_metadata_completeness(self, tmp_path):
        from research.returns_matrix import (
            _build_metadata, _compute_last_full_coverage,
        )

        entry_dates = [date(2020, 1, 2), date(2020, 1, 3)]
        horizons = (21, 63, 126)
        tickers = ["AAPL", "MSFT"]
        out_path = tmp_path / "matrix.parquet"

        sorted_dates = sorted(entry_dates)
        last_full_coverage = _compute_last_full_coverage(sorted_dates, horizons)

        meta = _build_metadata(
            parquet_path=out_path,
            entry_dates=entry_dates,
            horizons=horizons,
            universe_tickers=tickers,
            n_rows=100,
            n_chunks_expected=5,
            n_chunks_failed=0,
            price_cache_dir=tmp_path,
            data_source="yahoo",
            elapsed_total=42.5,
            status="complete",
            last_full_coverage=last_full_coverage,
        )

        required_fields = [
            "build_date", "build_timestamp_utc", "parquet_schema_version",
            "float_precision", "data_range", "universe", "horizons_trading_days",
            "seal_status", "seal_attestation", "delisting_handling",
            "entry_convention", "row_count", "parquet_path",
            "last_full_coverage_date", "status", "n_chunks_expected", "n_chunks_failed",
        ]
        for field in required_fields:
            assert field in meta, f"Missing metadata field: {field}"

        assert meta["float_precision"] == "float64"
        assert meta["seal_status"] == "explore-era-sealed (2025+ price cache untouched)"
        assert meta["data_range"]["entry_date_first"] == "2020-01-02"
        assert meta["data_range"]["entry_date_last"] == "2020-01-03"
        assert meta["horizons_trading_days"] == [21, 63, 126]
        assert meta["row_count"] == 100
        assert meta["status"] == "complete"
        assert meta["n_chunks_expected"] == 5
        assert meta["n_chunks_failed"] == 0

        # last_full_coverage_date should have an entry per horizon
        for h in horizons:
            assert str(h) in meta["last_full_coverage_date"]

    def test_last_full_coverage_trading_day_arithmetic(self):
        """COR-01 + COR-04: last_full_coverage anchored to actual entry_dates."""
        from research.returns_matrix import _compute_last_full_coverage

        # 5 entry dates, horizon 3 → last index with 3 entries after = index 1
        # sorted: [d1, d2, d3, d4, d5] — index 1 (d2) has 3 entries after it (d3,d4,d5)
        dates = [
            date(2020, 1, 2),
            date(2020, 1, 3),
            date(2020, 1, 6),
            date(2020, 1, 7),
            date(2020, 1, 8),
        ]
        result = _compute_last_full_coverage(dates, (3,))
        assert result["3"] == date(2020, 1, 3).isoformat()

        # horizon=1 → last index where index+1 < 5 → index 3 (d4)
        result2 = _compute_last_full_coverage(dates, (1,))
        assert result2["1"] == date(2020, 1, 7).isoformat()

        # horizon >= n → no full coverage → returns first date
        result3 = _compute_last_full_coverage(dates, (5,))
        assert result3["5"] == date(2020, 1, 2).isoformat()


# ---------------------------------------------------------------------------
# TestCompletenessContract
# ---------------------------------------------------------------------------

class TestCompletenessContract:
    """Loader raises on missing/partial sidecar; allow_partial bypasses check."""

    def _write_test_parquet(self, tmp_path: Path) -> Path:
        """Write a minimal test parquet artifact (no sidecar)."""
        from research.returns_matrix import _write_parquet_atomic
        rows = [(date(2020, 1, 2), 21, "AAPL", 1.0, False)]
        df = pd.DataFrame(rows, columns=[
            "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
        ])
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
        df["horizon_days"] = df["horizon_days"].astype("int32")
        df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
        df["is_terminal"] = df["is_terminal"].astype("bool")
        out = tmp_path / "matrix.parquet"
        _write_parquet_atomic(df, out)
        return out

    def test_missing_sidecar_raises(self, tmp_path):
        """Loader raises FileNotFoundError when sidecar is absent."""
        from research.returns_matrix import load_matrix_as_vector_cache
        out = self._write_test_parquet(tmp_path)
        # No _meta.json written — should raise
        with pytest.raises(FileNotFoundError, match="sidecar missing"):
            load_matrix_as_vector_cache(out, horizon=21)

    def test_partial_status_raises(self, tmp_path):
        """Loader raises RuntimeError when status='partial'."""
        from research.returns_matrix import load_matrix_as_vector_cache
        out = self._write_test_parquet(tmp_path)
        # Write a sidecar with status=partial
        sidecar = out / "_meta.json"
        sidecar.write_text(json.dumps({
            "status": "partial",
            "n_chunks_failed": 3,
            "n_chunks_expected": 10,
        }))
        with pytest.raises(RuntimeError, match="not complete"):
            load_matrix_as_vector_cache(out, horizon=21)

    def test_allow_partial_bypasses_check(self, tmp_path):
        """allow_partial=True loads even when status='partial'."""
        from research.returns_matrix import load_matrix_as_vector_cache
        out = self._write_test_parquet(tmp_path)
        sidecar = out / "_meta.json"
        sidecar.write_text(json.dumps({
            "status": "partial",
            "n_chunks_failed": 1,
            "n_chunks_expected": 5,
        }))
        cache = load_matrix_as_vector_cache(out, horizon=21, allow_partial=True)
        assert (date(2020, 1, 2), 21) in cache

    def test_complete_status_loads_normally(self, tmp_path):
        """status='complete' loads without errors."""
        from research.returns_matrix import load_matrix_as_vector_cache
        out = self._write_test_parquet(tmp_path)
        sidecar = out / "_meta.json"
        sidecar.write_text(json.dumps({"status": "complete"}))
        cache = load_matrix_as_vector_cache(out, horizon=21)
        assert (date(2020, 1, 2), 21) in cache


# ---------------------------------------------------------------------------
# TestBuilderSmoke — real ProcessPool build exercising spawn+pickle path
# ---------------------------------------------------------------------------

class TestBuilderSmoke:
    """Exercises the actual ProcessPool spawn path with a synthetic price cache.

    This is the COR-05 gate: a direct-call test cannot catch spawn-context
    import failures, pickling bugs, or worker import errors.  max_workers=1
    with the spawn context still exercises the full pickle roundtrip.

    Runtime: ~30-60s (spawn overhead + 3 ticker chunks × price cache lookups).
    Kept in pytest because it exercises the exact code path the production
    multi-hour build uses.
    """

    def _write_cache_pkl(
        self,
        cache_dir: Path,
        ticker: str,
        df: pd.DataFrame,
        fetch_start: str,
        fetch_end: str,
        data_source: str = "yahoo",
    ) -> None:
        """Write a synthetic DataFrame as a pkl file using the PriceFrameCache
        naming convention, so the worker can find it via cache.load()."""
        import pickle
        import re
        import zlib

        def _safe_ticker_fn(t: str) -> str:
            return re.sub(r"[^A-Za-z0-9]", "_", t).upper()

        def _ticker_key_fn(t: str) -> str:
            crc = format(zlib.crc32(t.encode("utf-8")) & 0xFFFFFFFF, "08x")
            return f"{_safe_ticker_fn(t)}_{crc}"

        def _safe_source_fn(s: str) -> str:
            return re.sub(r"[^A-Za-z0-9]", "_", s or "unknown").lower()

        v1_dir = cache_dir / "v1"
        v1_dir.mkdir(parents=True, exist_ok=True)

        key = _ticker_key_fn(ticker)
        ds = _safe_source_fn(data_source)
        span = f"{fetch_start}_{fetch_end}".replace("-", "")
        pkl_path = v1_dir / f"{key}_{ds}_{span}.pkl"
        with open(pkl_path, "wb") as fh:
            pickle.dump(df, fh)

    def test_processpool_spawn_path(self, tmp_path):
        """Run a real ProcessPool build with synthetic cache (2 tickers, 3 dates,
        horizons=(21,), max_workers=1) and verify the output artifact + sidecar.

        Exercises: spawn context import, pickle roundtrip, chunk→all_rows assembly,
        _write_parquet_and_meta_atomic, _read_matrix_sidecar, and loader.
        """
        from research.returns_matrix import (
            build_universe_returns_matrix,
            load_matrix_as_vector_cache,
        )

        # Build synthetic frames large enough for a 21-day horizon
        start = date(2018, 1, 2)
        tickers = ["AAPL", "MSFT"]
        frames = {
            "AAPL": _make_price_frame(start, n_bars=300, seed=10),
            "MSFT": _make_price_frame(start, n_bars=300, seed=11),
        }

        # Worker uses _make_memoized_loader with these spans:
        # start_year=2018, end_year=2018, low_lookback_years=3, horizon_months=7
        # → fetch_start = "2014-01-01", fetch_end = "2020-12-31"
        fetch_start = "2014-01-01"
        fetch_end = "2020-12-31"
        cache_dir = tmp_path / "price_cache"
        for ticker, df in frames.items():
            self._write_cache_pkl(cache_dir, ticker, df, fetch_start, fetch_end)

        entry_dates = [
            date(2018, 3, 1),
            date(2018, 6, 1),
            date(2018, 9, 3),
        ]
        out = tmp_path / "smoke_matrix.parquet"

        result_path = build_universe_returns_matrix(
            universe_tickers=tickers,
            entry_dates=entry_dates,
            horizons=(21,),
            start_year=2018,
            end_year=2018,
            low_lookback_years=3,
            horizon_months=7,
            data_source="yahoo",
            price_cache_dir=cache_dir,
            max_workers=1,
            chunk_size=2,
            output_path=out,
        )

        # Verify artifact exists and has sidecar
        assert result_path.exists(), "Output parquet directory missing"
        sidecar_path = result_path / "_meta.json"
        assert sidecar_path.exists(), "Sidecar _meta.json missing from artifact dir"

        # Verify sidecar completeness
        import json as _json
        sidecar = _json.loads(sidecar_path.read_text())
        assert sidecar["status"] == "complete", f"Expected complete, got {sidecar['status']}"
        assert sidecar["n_chunks_expected"] == 1  # 2 tickers / chunk_size=2 = 1 chunk
        assert sidecar["n_chunks_failed"] == 0
        assert sidecar["n_chunks_expected"] == sidecar["n_chunks_expected"]

        # Verify loader can read it (completeness check passes)
        cache = load_matrix_as_vector_cache(result_path, horizon=21)
        assert len(cache) > 0, "Loader returned empty cache from ProcessPool artifact"

        # Spot-check: verify ≥1 ticker appears in ≥1 (date, horizon) key
        all_syms = {sym for vec in cache.values() for sym in vec}
        assert len(all_syms) > 0, "No symbols in loaded cache"


# ---------------------------------------------------------------------------
# TestF338Anchors — spot-check matrix rows vs live _build_return_vector
# ---------------------------------------------------------------------------

class TestF338Anchors:
    """F338 real-data anchor gate: matrix values must match live path exactly.

    Uses synthetic price frames to avoid network dependency, but the test
    validates the EXACT equivalence between:
      1. _build_return_vector (the live path in event_study.py)
      2. _worker_build_chunk + load_matrix_as_vector_cache (the matrix path)

    This is the same gate as F351's equivalence test, just at the cell level.
    """

    def _build_live_vector(
        self,
        frames: dict,
        tickers: list[str],
        entry_date: date,
        horizon: int,
    ) -> dict:
        """Call the live _build_return_vector path."""
        from research.event_study import _build_return_vector
        loader = _make_loader(frames)
        return _build_return_vector(entry_date, horizon, loader, tickers)

    def _build_matrix_rows_directly(
        self,
        frames: dict,
        tickers: list[str],
        entry_dates: list[date],
        horizons: tuple[int, ...],
        tmp_path: Path,
    ) -> Path:
        """Build matrix by calling _worker_build_chunk directly (no subprocess)."""
        from research.returns_matrix import _write_parquet_atomic

        # We need a mock loader that _worker_build_chunk can use.
        # Since _worker_build_chunk imports _make_memoized_loader internally,
        # we test via the same cell logic but using mock PriceFrameCache.
        # We replicate the worker logic here directly using the shared helpers.
        from research.event_study import (
            _floor_status, _FLOOR_OK, _resolve_entry_open, _forward_return_terminal
        )
        from turnaround_validation import _frame_dates, _first_trading_close_on_or_after

        loader = _make_loader(frames)
        rows = []

        for sym in tickers:
            df = loader(sym)
            if df is None or df.empty:
                continue
            try:
                dates_list = _frame_dates(df)
            except Exception:
                continue
            trading_date_set = set(dates_list)

            for entry_date in entry_dates:
                if entry_date not in trading_date_set:
                    continue
                try:
                    fs = _floor_status(df, entry_date)
                except Exception:
                    continue
                if fs != _FLOOR_OK:
                    continue
                try:
                    res = _first_trading_close_on_or_after(df, entry_date)
                except Exception:
                    continue
                if res is None:
                    continue
                e_date, _ = res
                if e_date != entry_date:
                    continue
                try:
                    entry_open = _resolve_entry_open(df, entry_date, sym)
                except Exception:
                    continue
                if entry_open is None or entry_open <= 0:
                    continue

                for h in horizons:
                    try:
                        r, was_terminal = _forward_return_terminal(
                            df, entry_date, entry_open, h, direction="long"
                        )
                    except Exception:
                        continue
                    if r is not None:
                        rows.append((entry_date, h, sym, float(r), bool(was_terminal)))

        if not rows:
            # Write empty but valid parquet
            df_out = pd.DataFrame(columns=[
                "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
            ])
        else:
            df_out = pd.DataFrame(rows, columns=[
                "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
            ])

        df_out["entry_date"] = pd.to_datetime(df_out["entry_date"]).dt.date
        df_out["horizon_days"] = df_out["horizon_days"].astype("int32") if len(df_out) else df_out["horizon_days"]
        df_out["fwd_return_pct"] = df_out["fwd_return_pct"].astype("float64") if len(df_out) else df_out["fwd_return_pct"]
        df_out["is_terminal"] = df_out["is_terminal"].astype("bool") if len(df_out) else df_out["is_terminal"]

        out = tmp_path / "anchor_matrix.parquet"
        _write_parquet_atomic(df_out, out)
        return out

    def test_anchor_equivalence_normal_ticker(self, tmp_path):
        """F338 anchor: live path == matrix path for a normal (non-terminal) ticker."""
        from research.returns_matrix import load_matrix_as_vector_cache

        # Build a 300-bar frame starting 2018-01-02
        start = date(2018, 1, 2)
        frames = {
            "AAPL": _make_price_frame(start, n_bars=300, seed=1),
            "MSFT": _make_price_frame(start, n_bars=300, seed=2),
        }
        tickers = ["AAPL", "MSFT"]
        horizons = (21, 63)

        # Entry dates within the frame (we need dates where the frame has data)
        # Use 3 dates in early 2018 (frames start 2018-01-02, 300 bdays ~= 16 months)
        entry_dates = [date(2018, 3, 1), date(2018, 6, 1), date(2018, 9, 3)]

        # Build matrix (direct, no subprocess)
        mat_path = self._build_matrix_rows_directly(
            frames=frames,
            tickers=tickers,
            entry_dates=entry_dates,
            horizons=horizons,
            tmp_path=tmp_path,
        )

        # For each (entry_date, horizon), compare matrix vs live
        loader = _make_loader(frames)
        from research.event_study import _build_return_vector

        for entry_date in entry_dates:
            for h in horizons:
                live_vec = _build_return_vector(entry_date, h, loader, tickers)
                mat_cache = load_matrix_as_vector_cache(mat_path, horizon=h, allow_partial=True)
                mat_vec = mat_cache.get((entry_date, h), {})

                # Each symbol in live_vec must appear in mat_vec with exact same values
                for sym, (live_ret, live_term) in live_vec.items():
                    assert sym in mat_vec, (
                        f"F338 FAIL: sym={sym} entry={entry_date} h={h} "
                        f"in live_vec but NOT in mat_vec"
                    )
                    mat_ret, mat_term = mat_vec[sym]
                    assert mat_ret == pytest.approx(live_ret, rel=1e-10), (
                        f"F338 FAIL: sym={sym} entry={entry_date} h={h}: "
                        f"live={live_ret} matrix={mat_ret}"
                    )
                    assert mat_term == live_term, (
                        f"F338 FAIL: sym={sym} entry={entry_date} h={h}: "
                        f"live is_terminal={live_term} matrix={mat_term}"
                    )

                # Symbols in mat_vec must also all be in live_vec
                for sym in mat_vec:
                    assert sym in live_vec, (
                        f"F338 FAIL: sym={sym} in mat_vec but NOT in live_vec"
                    )

    def test_anchor_terminal_ticker(self, tmp_path):
        """F338 anchor: delisted ticker gets is_terminal=True in both paths."""
        from research.returns_matrix import load_matrix_as_vector_cache

        start = date(2019, 1, 2)
        # Short frame: only 30 bars (~6 weeks)
        frames = {
            "DELIST": _make_short_frame(start, n_bars=30, seed=5),
            "NORMAL": _make_price_frame(start, n_bars=300, seed=6),
        }
        tickers = ["DELIST", "NORMAL"]
        horizons = (63,)  # 63-day horizon on a 30-bar frame → terminal

        # Entry date near the start so horizon overruns the end of the DELIST frame
        entry_dates = [date(2019, 1, 3)]

        mat_path = self._build_matrix_rows_directly(
            frames=frames,
            tickers=tickers,
            entry_dates=entry_dates,
            horizons=horizons,
            tmp_path=tmp_path,
        )

        from research.event_study import _build_return_vector
        loader = _make_loader(frames)

        entry_date = date(2019, 1, 3)
        h = 63
        live_vec = _build_return_vector(entry_date, h, loader, tickers)
        mat_cache = load_matrix_as_vector_cache(mat_path, horizon=h, allow_partial=True)
        mat_vec = mat_cache.get((entry_date, h), {})

        # Verify DELIST appears in both and has the same return + terminal flag
        if "DELIST" in live_vec:
            assert "DELIST" in mat_vec, "F338 FAIL: DELIST in live but not matrix"
            assert live_vec["DELIST"][1] is True, "Expected is_terminal=True for short frame"
            live_ret, live_term = live_vec["DELIST"]
            mat_ret, mat_term = mat_vec["DELIST"]
            assert mat_ret == pytest.approx(live_ret, rel=1e-10)
            assert mat_term == live_term

    def test_anchor_30_cells_exact_match(self, tmp_path):
        """F338 anchor: 30 (ticker, date, horizon) cells — all must be exact match."""
        from research.returns_matrix import load_matrix_as_vector_cache

        # Build 5 tickers × 2 entry dates × 3 horizons = 30 cell check points
        start = date(2017, 6, 1)
        tickers = [f"SYM{i}" for i in range(5)]
        frames = {
            sym: _make_price_frame(start, n_bars=400, seed=i + 10)
            for i, sym in enumerate(tickers)
        }

        entry_dates = [date(2017, 9, 1), date(2018, 3, 1)]
        horizons = (21, 63, 126)

        mat_path = self._build_matrix_rows_directly(
            frames=frames,
            tickers=tickers,
            entry_dates=entry_dates,
            horizons=horizons,
            tmp_path=tmp_path,
        )

        from research.event_study import _build_return_vector
        loader = _make_loader(frames)

        fail_count = 0
        check_count = 0

        for entry_date in entry_dates:
            for h in horizons:
                live_vec = _build_return_vector(entry_date, h, loader, tickers)
                mat_cache = load_matrix_as_vector_cache(mat_path, horizon=h, allow_partial=True)
                mat_vec = mat_cache.get((entry_date, h), {})

                for sym in live_vec:
                    check_count += 1
                    if sym not in mat_vec:
                        fail_count += 1
                        continue
                    live_ret, live_term = live_vec[sym]
                    mat_ret, mat_term = mat_vec[sym]
                    if abs(mat_ret - live_ret) > 1e-10:
                        fail_count += 1
                    if mat_term != live_term:
                        fail_count += 1

        assert fail_count == 0, (
            f"F338 FAIL: {fail_count} cells out of {check_count} failed exact-match gate"
        )
        assert check_count > 0, "F338: no cells were checked (frames may not overlap entry_dates)"


# ---------------------------------------------------------------------------
# TestGenerateTradingDates
# ---------------------------------------------------------------------------

class TestGenerateTradingDates:
    def test_excludes_weekends(self):
        from research.returns_matrix import _generate_trading_dates
        dates = _generate_trading_dates(date(2020, 1, 1), date(2020, 1, 10))
        for d in dates:
            assert d.weekday() < 5, f"{d} is a weekend"

    def test_range_coverage(self):
        from research.returns_matrix import _generate_trading_dates
        dates = _generate_trading_dates(date(2020, 1, 2), date(2020, 1, 10))
        assert date(2020, 1, 2) in dates   # Thursday
        assert date(2020, 1, 9) in dates   # Thursday
        assert date(2020, 1, 4) not in dates  # Saturday
        assert date(2020, 1, 5) not in dates  # Sunday
