"""F357 — Universe Returns Matrix.

One-pass precompute of forward returns for all ~4,700 liquid-universe tickers ×
all trading days (2015-01-02 through 2024-12-31) × horizons (21, 63, 126 td)
into a partitioned Parquet artifact.

Builder, loader, and CLI in one module.

Design:
  - Worker-owns-chunk parallelization via ProcessPoolExecutor (wfa_pool pattern).
    Each worker receives a ticker chunk (~30-50 tickers) and iterates over all
    entry dates × horizons, replicating the exact semantics of
    event_study._build_return_vector().
  - Artifact: long-format Parquet partitioned by horizon_days.
    Columns: entry_date (date), horizon_days (int32), symbol (str),
             fwd_return_pct (float64), is_terminal (bool).
  - Loader: reads one horizon partition, builds per-date dicts via zip+groupby
    over numpy arrays (never iterrows), memoized into _VectorCache shape.
  - Hard seal guard: ValueError on any entry_date > 2024-12-31.

Usage (CLI):
  backend/venv/bin/python -m research.returns_matrix \\
      --start 2015-01-02 --end 2024-12-31 \\
      --output /path/to/matrix.parquet \\
      --log-file /path/to/run.log

Reference: F357 plan.md, event_study.py _build_return_vector semantics.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — allow running from any cwd
# ---------------------------------------------------------------------------
_MODULE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _MODULE_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MATRIX_BUILD_MAX_DATE = date(2024, 12, 31)
_DEFAULT_HORIZONS: tuple[int, ...] = (21, 63, 126)
_DEFAULT_OUTPUT_PATH = _BACKEND_DIR / "data" / "universe_matrix.parquet"
_DEFAULT_META_PATH = _BACKEND_DIR / "data" / "universe_matrix_meta.json"
# Chunk size: ~30-50 tickers per worker (seconds-of-work granularity per F166).
_DEFAULT_CHUNK_SIZE = 40

# ---------------------------------------------------------------------------
# Shared return-vector types (mirrors event_study.py F351 types)
# ---------------------------------------------------------------------------
_ReturnVector = dict[str, tuple[float, bool]]
_VectorCache = dict[tuple, _ReturnVector]


# ---------------------------------------------------------------------------
# Worker function (must be module-level + pickle-safe for spawn context)
# ---------------------------------------------------------------------------

def _worker_build_chunk(
    ticker_chunk: list[str],
    entry_dates: list[str],       # ISO strings (date objects aren't always pickle-safe cross-version)
    horizons: tuple[int, ...],
    start_year: int,
    end_year: int,
    low_lookback_years: int,
    horizon_months: int,
    data_source: str,
    price_cache_dir: str,         # str path for pickle-safety
    progress_path: Optional[str] = None,  # per-worker progress file (str for pickle-safety)
) -> tuple[list[tuple], dict[str, list[str]]]:
    """Worker: compute forward returns for a ticker chunk.

    Returns (rows, coverage) where rows is a list of
    (entry_date_str, horizon_days, symbol, fwd_return_pct, is_terminal) and
    coverage = {"no_frame": [syms], "no_rows": [syms]} — the accounting for
    tickers that produced nothing.  "no_frame" = loader returned None/empty
    (includes TRANSIENT network-fetch failures — observed live 2026-06-07:
    7/100 probe tickers fetch-failed in one run and loaded fine in the next,
    so an uncounted no-frame ticker is an invisible, nondeterministic coverage
    hole).  "no_rows" = frame loaded but zero (date, horizon) cells passed the
    floor/entry gates (deterministic).  Both surface in the artifact sidecar.

    Spawn-safe: receives only primitives/plain containers; imports are local;
    no closures or module-level state passed in.

    `progress_path`: when set, the worker overwrites this file with
    "<done>/<total> <last_symbol>" after each ticker — the followable progress
    channel for within-chunk visibility (John: long-running tasks must always
    have a way to follow progress; chunk-level parent logging alone goes silent
    for the entire chunk duration).  Best-effort: progress-write failures never
    fail the chunk.
    """
    import sys
    from pathlib import Path as _Path
    _backend = _Path(__file__).resolve().parent.parent
    if str(_backend) not in sys.path:
        sys.path.insert(0, str(_backend))

    from datetime import date as _date
    from turnaround_validation import (
        PriceFrameCache,
        _make_memoized_loader,
        _frame_dates,
        _first_trading_close_on_or_after,
    )
    from research.event_study import (
        _floor_status,
        _FLOOR_OK,
        _resolve_entry_open,
        _forward_return_terminal,
    )
    from research.universe_floors import precompute_df_up_to as _precompute_floor_pre

    # Build loader for this worker
    cache = PriceFrameCache(cache_dir=_Path(price_cache_dir))
    loader = _make_memoized_loader(
        start_year=start_year,
        end_year=end_year,
        low_lookback_years=low_lookback_years,
        horizon_months=horizon_months,
        data_source=data_source,
        price_cache=cache,
    )

    entry_dates_parsed: list[_date] = [_date.fromisoformat(d) for d in entry_dates]

    import logging as _logging
    _wlog = _logging.getLogger(__name__)

    rows: list[tuple] = []
    no_frame_syms: list[str] = []
    no_row_syms: list[str] = []
    for sym_i, sym in enumerate(ticker_chunk):
        if progress_path is not None:
            try:
                _Path(progress_path).write_text(
                    f"{sym_i}/{len(ticker_chunk)} {sym}\n"
                )
            except OSError:
                pass  # progress is best-effort, never fails the chunk
        df = loader(sym)
        if df is None or df.empty:
            no_frame_syms.append(sym)
            continue

        # Build a set of dates this symbol actually trades on (fast check)
        try:
            dates_list = _frame_dates(df)
        except Exception as _exc:
            _wlog.warning("skip %s: _frame_dates failed: %s", sym, _exc)
            no_frame_syms.append(sym)
            continue
        trading_date_set: set[_date] = set(dates_list)

        # F357 perf: precompute the per-frame invariants ONCE per symbol and
        # pass them into the per-date helpers below.  Without this, each helper
        # re-derived the full index per (symbol, date) call — per-element tz
        # conversion + a frame copy that measured ~40 CPU-seconds per ticker
        # (~52 CPU-hours for the full universe).  Helpers produce identical
        # results with or without the precomputed args (probe-diffed).
        try:
            floor_pre = _precompute_floor_pre(df)
        except Exception as _exc:
            _wlog.warning("skip %s: precompute_df_up_to failed: %s", sym, _exc)
            no_frame_syms.append(sym)
            continue
        rows_before_sym = len(rows)

        sym_errors = 0
        for entry_date in entry_dates_parsed:
            # Skip if symbol doesn't trade on this date
            if entry_date not in trading_date_set:
                continue

            # ADV-01: floor decided from info available BEFORE entry
            try:
                fs = _floor_status(df, entry_date, pre=floor_pre)
            except Exception as _exc:
                sym_errors += 1
                _wlog.debug("skip %s %s floor: %s", sym, entry_date, _exc)
                continue
            if fs != _FLOOR_OK:
                continue

            # Verify first trading close on or after entry_date IS entry_date
            try:
                res = _first_trading_close_on_or_after(df, entry_date, dates=dates_list)
            except Exception as _exc:
                sym_errors += 1
                _wlog.debug("skip %s %s ftco: %s", sym, entry_date, _exc)
                continue
            if res is None:
                continue
            e_date, _ = res
            if e_date != entry_date:
                continue

            # Resolve entry open (prefer Open, fallback to Close)
            try:
                entry_open = _resolve_entry_open(df, entry_date, sym, dates=dates_list)
            except Exception as _exc:
                sym_errors += 1
                _wlog.debug("skip %s %s entry_open: %s", sym, entry_date, _exc)
                continue
            if entry_open is None or entry_open <= 0:
                continue

            # Compute forward returns for all horizons
            for h in horizons:
                try:
                    r, was_terminal = _forward_return_terminal(
                        df, entry_date, entry_open, h, direction="long",
                        dates=dates_list,
                    )
                except Exception as _exc:
                    sym_errors += 1
                    _wlog.debug("skip %s %s h=%s fwd_ret: %s", sym, entry_date, h, _exc)
                    continue
                if r is not None:
                    rows.append((
                        entry_date.isoformat(),   # str for pickle safety
                        h,
                        sym,
                        float(r),
                        bool(was_terminal),
                    ))

        if sym_errors > 0:
            _wlog.warning("sym %s: %d unexpected errors (dates/horizons skipped)", sym, sym_errors)

        if len(rows) == rows_before_sym:
            no_row_syms.append(sym)

    if progress_path is not None:
        try:
            _Path(progress_path).write_text(
                f"{len(ticker_chunk)}/{len(ticker_chunk)} done\n"
            )
        except OSError:
            pass

    return rows, {"no_frame": no_frame_syms, "no_rows": no_row_syms}


# ---------------------------------------------------------------------------
# Matrix builder
# ---------------------------------------------------------------------------

def build_universe_returns_matrix(
    universe_tickers: list[str],
    entry_dates: list[date],
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    start_year: int = 2015,
    end_year: int = 2024,
    low_lookback_years: int = 3,
    horizon_months: int = 7,
    data_source: str = "yahoo",
    price_cache_dir: Optional[Path] = None,
    max_workers: Optional[int] = None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    output_path: Optional[Path] = None,
    log_file: Optional[Path] = None,
) -> Path:
    """Precompute forward returns for (ticker, date, horizon) tuples.

    Args:
        universe_tickers: ~4,700 liquid-universe symbols.
        entry_dates: trading dates (must all be <= 2024-12-31).
        horizons: forward lookout horizons in trading days (default: 21, 63, 126).
        start_year: year span for loader (controls price fetch window start).
        end_year: year span for loader (controls price fetch window end).
        low_lookback_years: lookback extension for floor checks.
        horizon_months: months forward past end_year for exit prices.
        data_source: price data provider (default: "yahoo").
        price_cache_dir: price cache directory (default: standard path).
        max_workers: ProcessPool size (default: cpu_count() - 1, min 1).
        chunk_size: tickers per worker chunk (~30-50 recommended).
        output_path: Parquet output path (default: backend/data/universe_matrix.parquet).
        log_file: worker progress log path (long runs should always set this).

    Returns:
        Path to the output Parquet artifact.

    Raises:
        ValueError: if any entry_date > 2024-12-31 (hard seal guard).
    """
    # Hard seal guard
    if entry_dates:
        max_entry = max(entry_dates)
        if max_entry > _MATRIX_BUILD_MAX_DATE:
            raise ValueError(
                f"Matrix build seal violated: entry_dates include {max_entry}, "
                f"but 2025+ is sealed for future confirm window. "
                f"Maximum allowed: {_MATRIX_BUILD_MAX_DATE}"
            )

    if not entry_dates:
        raise ValueError("entry_dates must not be empty")
    if not universe_tickers:
        raise ValueError("universe_tickers must not be empty")

    # Attach log file handler — removed in finally to avoid handle leaks across calls
    fh: Optional[logging.FileHandler] = None
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
        ))
        logging.getLogger().addHandler(fh)
        log.info("Log file attached: %s", log_file)

    try:
        return _build_universe_returns_matrix_inner(
            universe_tickers=universe_tickers,
            entry_dates=entry_dates,
            horizons=horizons,
            start_year=start_year,
            end_year=end_year,
            low_lookback_years=low_lookback_years,
            horizon_months=horizon_months,
            data_source=data_source,
            price_cache_dir=price_cache_dir,
            max_workers=max_workers,
            chunk_size=chunk_size,
            output_path=output_path,
        )
    finally:
        if fh is not None:
            logging.getLogger().removeHandler(fh)
            fh.close()


def _build_universe_returns_matrix_inner(
    universe_tickers: list[str],
    entry_dates: list[date],
    horizons: tuple[int, ...],
    start_year: int,
    end_year: int,
    low_lookback_years: int,
    horizon_months: int,
    data_source: str,
    price_cache_dir: Optional[Path],
    max_workers: Optional[int],
    chunk_size: int,
    output_path: Optional[Path],
) -> Path:
    """Inner builder — called by build_universe_returns_matrix after log setup."""
    import multiprocessing as mp

    out_path = Path(output_path) if output_path else _DEFAULT_OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve price cache dir
    if price_cache_dir is None:
        price_cache_dir = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
    price_cache_dir = Path(price_cache_dir)

    # Worker pool sizing
    if max_workers is None:
        cpu = mp.cpu_count() or 2
        max_workers = max(1, cpu - 1)

    # Split into chunks
    chunks = [
        universe_tickers[i:i + chunk_size]
        for i in range(0, len(universe_tickers), chunk_size)
    ]
    n_chunks = len(chunks)
    n_tickers = len(universe_tickers)
    n_dates = len(entry_dates)

    log.info(
        "build_universe_returns_matrix: %d tickers, %d dates, horizons=%s, "
        "%d chunks (size=%d), max_workers=%d",
        n_tickers, n_dates, horizons, n_chunks, chunk_size, max_workers,
    )

    # Entry dates as ISO strings for pickle-safe worker args
    entry_dates_strs = [d.isoformat() for d in entry_dates]
    price_cache_dir_str = str(price_cache_dir)

    # Within-chunk progress files: workers overwrite chunk_<i>.txt per ticker.
    # Followable via `cat <progress_dir>/chunk_*.txt` while a chunk is mid-flight
    # (parent chunk-completion logs alone go silent for a whole chunk's duration).
    progress_dir = out_path.parent / f"{out_path.name}.progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    log.info("Within-chunk progress files: %s/chunk_<i>.txt", progress_dir)

    # Worker args (all primitives + plain containers — no closures)
    worker_kwargs = dict(
        entry_dates=entry_dates_strs,
        horizons=horizons,
        start_year=start_year,
        end_year=end_year,
        low_lookback_years=low_lookback_years,
        horizon_months=horizon_months,
        data_source=data_source,
        price_cache_dir=price_cache_dir_str,
    )

    # Run in process pool
    from concurrent.futures import ProcessPoolExecutor, as_completed

    all_rows: list[tuple] = []
    no_frame_tickers: list[str] = []
    no_row_tickers: list[str] = []
    failed_chunk_tickers: list[str] = []  # COV-1: failed chunks' tickers must not count as covered
    t0 = time.monotonic()
    completed = 0
    errors = 0

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as ex:
        future_to_chunk_idx = {
            ex.submit(
                _worker_build_chunk, chunk,
                progress_path=str(progress_dir / f"chunk_{i}.txt"),
                **worker_kwargs,
            ): i
            for i, chunk in enumerate(chunks)
        }
        for fut in as_completed(future_to_chunk_idx):
            idx = future_to_chunk_idx[fut]
            try:
                rows, chunk_coverage = fut.result()
                all_rows.extend(rows)
                no_frame_tickers.extend(chunk_coverage["no_frame"])
                no_row_tickers.extend(chunk_coverage["no_rows"])
                completed += 1
                if completed % 10 == 0 or completed == n_chunks:
                    elapsed = time.monotonic() - t0
                    log.info(
                        "Chunk %d/%d done (%.0fs elapsed, %d rows so far)",
                        completed, n_chunks, elapsed, len(all_rows),
                    )
            except Exception as exc:
                errors += 1
                chunk_tickers = chunks[idx]
                # COV-1 (review-wave 2026-06-07, confirmed P1): attribute the
                # failed chunk's tickers to their own bucket — without this the
                # coverage formula counts them as "produced rows" and a partial
                # build reports e.g. 4678/4678 while whole chunks never ran.
                failed_chunk_tickers.extend(chunk_tickers)
                log.error(
                    "Worker chunk %d failed (tickers %s..%s): %s",
                    idx,
                    chunk_tickers[0] if chunk_tickers else "?",
                    chunk_tickers[-1] if chunk_tickers else "?",
                    exc,
                )

    elapsed_total = time.monotonic() - t0
    log.info(
        "All chunks done: %d/%d completed, %d errors, %d total rows in %.1fs",
        completed, n_chunks, errors, len(all_rows), elapsed_total,
    )
    # Coverage accounting: no-frame tickers may be TRANSIENT fetch failures
    # (nondeterministic across runs — observed live 2026-06-07), so they must
    # be visible, never silently absent.
    log.info(
        "Ticker coverage: %d/%d produced rows, %d no-frame (loader None/empty), "
        "%d loaded-but-no-rows, %d in failed chunks (coverage unknown)",
        n_tickers - len(no_frame_tickers) - len(no_row_tickers) - len(failed_chunk_tickers),
        n_tickers,
        len(no_frame_tickers), len(no_row_tickers), len(failed_chunk_tickers),
    )
    if no_frame_tickers:
        log.warning(
            "no-frame tickers (possible transient fetch failures — rerun to "
            "check determinism): %s%s",
            ", ".join(sorted(no_frame_tickers)[:20]),
            f" … +{len(no_frame_tickers) - 20} more" if len(no_frame_tickers) > 20 else "",
        )

    if not all_rows:
        raise RuntimeError(
            f"Matrix build produced no rows (all {n_chunks} chunks may have failed)"
        )

    # Determine completeness status for metadata
    status = "complete" if errors == 0 else "partial"
    if errors > 0:
        log.warning(
            "Build has partial failures: %d/%d chunks failed — artifact status='partial'",
            errors, n_chunks,
        )

    # Assemble DataFrame
    log.info("Assembling DataFrame from %d rows...", len(all_rows))
    df = pd.DataFrame(all_rows, columns=[
        "entry_date", "horizon_days", "symbol", "fwd_return_pct", "is_terminal"
    ])
    # Release the raw list immediately to halve peak-memory usage (PY-01).
    # The list + DataFrame both in memory at ~35M rows would be 4–8 GB.
    del all_rows

    # Parse entry_date strings back to Python date objects
    df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date
    df["horizon_days"] = df["horizon_days"].astype("int32")
    df["fwd_return_pct"] = df["fwd_return_pct"].astype("float64")
    df["is_terminal"] = df["is_terminal"].astype("bool")

    # Sort for cache locality
    df = df.sort_values(["horizon_days", "entry_date", "symbol"]).reset_index(drop=True)

    # Compute last_full_coverage_date per horizon BEFORE writing.
    # (COR-01 + COR-04) Anchor to actual last_entry and use trading-day
    # arithmetic from the build's own entry_dates calendar, not calendar
    # approximation relative to the hardcoded 2024-12-31.
    sorted_entry_dates = sorted(entry_dates)
    last_full_coverage = _compute_last_full_coverage(sorted_entry_dates, horizons)

    # Write parquet + metadata sidecar jointly atomic:
    # Both go into a tmp dir that is renamed to final in ONE os.rename call
    # so a crash between the two writes can never leave a stale sidecar
    # paired with a newer parquet (COR-03).
    log.info("Writing Parquet artifact (atomic) to %s ...", out_path)
    meta = _build_metadata(
        parquet_path=out_path,
        entry_dates=entry_dates,
        horizons=horizons,
        universe_tickers=universe_tickers,
        n_rows=len(df),
        n_chunks_expected=n_chunks,
        n_chunks_failed=errors,
        price_cache_dir=price_cache_dir,
        data_source=data_source,
        elapsed_total=elapsed_total,
        status=status,
        last_full_coverage=last_full_coverage,
        no_frame_tickers=no_frame_tickers,
        no_row_tickers=no_row_tickers,
        failed_chunk_tickers=failed_chunk_tickers,
    )
    _write_parquet_and_meta_atomic(df, out_path, meta)

    log.info(
        "Matrix complete: %s (%d rows, %d tickers, %d dates, status=%s)",
        out_path,
        len(df),
        df["symbol"].nunique(),
        df["entry_date"].nunique(),
        status,
    )
    return out_path


def _compute_last_full_coverage(
    sorted_entry_dates: list[date],
    horizons: tuple[int, ...],
) -> dict[str, str]:
    """Compute last entry_date with full coverage per horizon using trading-day arithmetic.

    (COR-01 + COR-04) Instead of calendar-day approximation relative to a
    hardcoded 2024-12-31 anchor, count backwards in the build's own entry_dates
    calendar: the last entry_date d such that there are at least h entries
    AFTER d in the sorted list (i.e. d_idx + h < len(sorted_entry_dates)).

    This is exact for the actual entry_dates set the artifact was built from.
    """
    n = len(sorted_entry_dates)
    result: dict[str, str] = {}
    for h in horizons:
        # Last index where index + h < n  →  index < n - h
        last_idx = n - h - 1
        if last_idx >= 0:
            result[str(h)] = sorted_entry_dates[last_idx].isoformat()
        else:
            # Not enough dates to have any full coverage for this horizon
            result[str(h)] = sorted_entry_dates[0].isoformat() if sorted_entry_dates else ""
    return result


def _build_metadata(
    parquet_path: Path,
    entry_dates: list[date],
    horizons: tuple[int, ...],
    universe_tickers: list[str],
    n_rows: int,
    n_chunks_expected: int,
    n_chunks_failed: int,
    price_cache_dir: Path,
    data_source: str,
    elapsed_total: float,
    status: str,
    last_full_coverage: dict[str, str],
    no_frame_tickers: Optional[list[str]] = None,
    no_row_tickers: Optional[list[str]] = None,
    failed_chunk_tickers: Optional[list[str]] = None,
) -> dict:
    """Build metadata dict (no I/O — caller writes it)."""
    now_utc = datetime.now(tz=timezone.utc)

    # Cache fingerprint (fast: just count files rather than hashing all bytes)
    cache_v1_dir = price_cache_dir / "v1"
    pkl_count = sum(1 for _ in cache_v1_dir.glob("*.pkl")) if cache_v1_dir.exists() else 0
    cache_fingerprint_note = f"{pkl_count}_pkl_files_in_v1"

    return {
        "build_date": now_utc.date().isoformat(),
        "build_timestamp_utc": now_utc.isoformat(),
        "build_elapsed_seconds": round(elapsed_total, 1),
        "status": status,
        "n_chunks_expected": n_chunks_expected,
        "n_chunks_failed": n_chunks_failed,
        "worker_errors": n_chunks_failed,
        "parquet_schema_version": 1,
        "float_precision": "float64",
        "data_range": {
            "entry_date_first": min(entry_dates).isoformat(),
            "entry_date_last": max(entry_dates).isoformat(),
            "entry_date_count": len(entry_dates),
        },
        "universe": {
            "ticker_count": len(universe_tickers),
            "source": str(price_cache_dir),
            "gating": "SIC-bearing + 2012+ coverage + _FLOOR_OK on every date",
        },
        # Ticker-level coverage accounting. no_frame entries may be TRANSIENT
        # network-fetch failures (observed 2026-06-07: 7/100 probe tickers
        # fetch-failed in one run, loaded in the next) — a nonzero count means
        # a rerun may produce MORE coverage; compare before trusting "complete".
        "ticker_coverage": {
            "tickers_with_rows": (
                len(universe_tickers)
                - len(no_frame_tickers or [])
                - len(no_row_tickers or [])
                - len(failed_chunk_tickers or [])
            ),
            "no_frame_count": len(no_frame_tickers or []),
            "no_frame_symbols": sorted(no_frame_tickers or []),
            "no_rows_count": len(no_row_tickers or []),
            "no_rows_symbols": sorted(no_row_tickers or []),
            # COV-1: tickers in chunks that FAILED — coverage unknown, not "with rows"
            "failed_chunk_count": len(failed_chunk_tickers or []),
            "failed_chunk_symbols": sorted(failed_chunk_tickers or []),
        },
        "horizons_trading_days": list(horizons),
        "last_full_coverage_date": last_full_coverage,
        "price_cache_fingerprint": cache_fingerprint_note,
        "seal_status": "explore-era-sealed (2025+ price cache untouched)",
        "seal_attestation": "matrix built through 2024-12-31 per charter",
        "delisting_handling": "ADV-03: terminal values included, is_terminal marked",
        "entry_convention": "next-trading-open on entry_date",
        "row_count": n_rows,
        "parquet_path": str(parquet_path),
        "data_source": data_source,
    }


def _write_parquet_and_meta_atomic(
    df: pd.DataFrame,
    out_path: Path,
    meta: dict,
) -> None:
    """Write Parquet partitions + metadata sidecar in a single atomic rename.

    (COR-03) Both files go into a tmp directory first; the entire tmp dir is
    renamed to final in ONE os.rename call. A crash between the parquet write
    and the metadata write leaves only the tmp dir (no final artifact), so the
    loader never sees a parquet without its sidecar.

    The metadata sidecar is written as _meta.json INSIDE the parquet directory
    (not as a sibling). The loader reads it from there.
    """
    import shutil
    out_path = Path(out_path)
    # Use a .tmp suffix on the parent dir name to avoid colliding with the
    # actual output dir if it already exists.
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    if tmp_path.exists():
        shutil.rmtree(str(tmp_path), ignore_errors=True)
    try:
        # Write partitioned Parquet into tmp dir
        df.to_parquet(
            str(tmp_path),
            engine="pyarrow",
            partition_cols=["horizon_days"],
            compression="snappy",
            index=False,
        )
        # Write metadata sidecar INSIDE the tmp dir (so it travels with the rename)
        meta_inside = tmp_path / "_meta.json"
        meta_inside.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Atomic rename: remove any existing final dir first, then rename tmp→final
        if out_path.exists():
            shutil.rmtree(str(out_path))
        os.rename(str(tmp_path), str(out_path))
        log.info("Artifact written atomically: %s", out_path)
    except Exception:
        shutil.rmtree(str(tmp_path), ignore_errors=True)
        raise


def _write_parquet_atomic(df: pd.DataFrame, out_path: Path) -> None:
    """Write DataFrame as partitioned Parquet atomically (tmp + os.rename).

    Kept for backward-compat with TestSchemaCorrectness / TestLoaderReshape /
    TestF338Anchors which call _write_parquet_atomic directly (no metadata
    sidecar needed in those unit tests — they test the Parquet read path only).
    """
    import shutil
    out_path = Path(out_path)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    if tmp_path.exists():
        shutil.rmtree(str(tmp_path), ignore_errors=True)
    try:
        df.to_parquet(
            str(tmp_path),
            engine="pyarrow",
            partition_cols=["horizon_days"],
            compression="snappy",
            index=False,
        )
        if out_path.exists():
            shutil.rmtree(str(out_path))
        os.rename(str(tmp_path), str(out_path))
    except Exception:
        shutil.rmtree(str(tmp_path), ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Loader — matrix-backed _VectorCache
# ---------------------------------------------------------------------------

def _read_matrix_sidecar(matrix_path: Path) -> dict:
    """Read the _meta.json sidecar from inside the matrix directory.

    Raises:
        FileNotFoundError: if the sidecar is missing (indicates incomplete or
            truncated artifact — never produced by the current builder).
    """
    meta_path = Path(matrix_path) / "_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"Matrix sidecar missing: {meta_path}. "
            f"The artifact may be incomplete or was built by an older version. "
            f"Rebuild with build_universe_returns_matrix()."
        )
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_matrix_as_vector_cache(
    matrix_path: Path,
    horizon: int,
    entry_dates: Optional[list[date]] = None,
    allow_partial: bool = False,
) -> _VectorCache:
    """Load one horizon partition from the matrix and return a _VectorCache.

    Decision F357 §5: never iterrows over the full artifact.
    Strategy:
    - Read sidecar first; raise on status != "complete" (completeness contract).
    - Read only the requested horizon partition via partition filter.
    - Group by entry_date using np.searchsorted on the already-sorted partition
      (O(log n) per date, not O(n) boolean mask scan).
    - Build per-date dicts lazily via zip over numpy array slices.

    Args:
        matrix_path: path to the partitioned Parquet directory.
        horizon: one of (21, 63, 126) — reads only that partition.
        entry_dates: optional filter; if given, only load these dates.
        allow_partial: if True, skip the completeness check and load a partial
            artifact. Only for explicit recovery/analysis workflows.

    Returns:
        {(entry_date, horizon): {symbol: (fwd_return_pct, is_terminal)}}

    Raises:
        FileNotFoundError: if the matrix directory or sidecar does not exist.
        RuntimeError: if status != "complete" and allow_partial is False.
    """
    matrix_path = Path(matrix_path)
    if not matrix_path.exists():
        raise FileNotFoundError(f"Universe matrix not found: {matrix_path}")

    # Completeness contract: read sidecar and validate before loading data.
    # allow_partial=True skips all checks (for unit tests and recovery workflows).
    if not allow_partial:
        sidecar = _read_matrix_sidecar(matrix_path)
        status = sidecar.get("status", "unknown")
        if status != "complete":
            n_failed = sidecar.get("n_chunks_failed", sidecar.get("worker_errors", "?"))
            n_expected = sidecar.get("n_chunks_expected", "?")
            raise RuntimeError(
                f"Matrix artifact is not complete (status={status!r}, "
                f"n_chunks_failed={n_failed}/{n_expected}). "
                f"Pass allow_partial=True to load anyway, or rebuild with "
                f"build_universe_returns_matrix()."
            )

    filters = [("horizon_days", "=", horizon)]
    if entry_dates is not None and len(entry_dates) > 0:
        filters.append(("entry_date", "in", entry_dates))

    df = pd.read_parquet(
        str(matrix_path),
        engine="pyarrow",
        filters=filters,
        columns=["entry_date", "symbol", "fwd_return_pct", "is_terminal"],
    )

    if df.empty:
        return {}

    # Ensure entry_date is Python date objects
    if hasattr(df["entry_date"].dtype, "tz"):
        df["entry_date"] = df["entry_date"].dt.date
    elif df["entry_date"].dtype == object:
        pass  # already date objects
    else:
        df["entry_date"] = pd.to_datetime(df["entry_date"]).dt.date

    # Build _VectorCache without iterrows.
    # Sort by entry_date so np.searchsorted gives correct O(log n) per-date
    # slicing (PY-02).  The production builder writes data pre-sorted by
    # (horizon_days, entry_date, symbol), so the sort is a no-op at runtime;
    # it also makes the loader robust for test artifacts that skip the pre-sort.
    df = df.sort_values("entry_date").reset_index(drop=True)

    dates_arr = df["entry_date"].to_numpy()
    syms_arr = df["symbol"].to_numpy()
    rets_arr = df["fwd_return_pct"].to_numpy(dtype=np.float64)
    term_arr = df["is_terminal"].to_numpy(dtype=bool)

    cache: _VectorCache = {}

    unique_dates = np.unique(dates_arr)
    for d in unique_dates:
        lo = int(np.searchsorted(dates_arr, d, side="left"))
        hi = int(np.searchsorted(dates_arr, d, side="right"))
        syms = syms_arr[lo:hi]
        rets = rets_arr[lo:hi]
        terms = term_arr[lo:hi]
        vec: _ReturnVector = {
            sym: (float(r), bool(t))
            for sym, r, t in zip(syms, rets, terms)
        }
        cache[(d, horizon)] = vec

    return cache


def load_matrix_as_vector_cache_multi(
    matrix_path: Path,
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
    entry_dates: Optional[list[date]] = None,
    allow_partial: bool = False,
) -> _VectorCache:
    """Load multiple horizon partitions, merging into a single _VectorCache.

    Convenience wrapper around load_matrix_as_vector_cache for multi-horizon use.
    The completeness check is performed once per horizon call; pass allow_partial=True
    to suppress it (only for explicit recovery/analysis workflows).
    """
    merged: _VectorCache = {}
    for h in horizons:
        partial = load_matrix_as_vector_cache(matrix_path, h, entry_dates, allow_partial=allow_partial)
        merged.update(partial)
    return merged


# ---------------------------------------------------------------------------
# Cache fingerprint (for metadata integrity checks)
# ---------------------------------------------------------------------------

def cache_fingerprint(cache_dir: Path) -> str:
    """Compute SHA256 hash of all .pkl files under cache_dir.

    Note: on a 15-20 GB price cache this is expensive (~minutes). Used only
    in metadata generation when requested explicitly.
    """
    hasher = hashlib.sha256()
    v1_dir = Path(cache_dir) / "v1"
    for pkl in sorted(v1_dir.glob("*.pkl")):
        hasher.update(pkl.read_bytes())
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args():
    import argparse
    parser = argparse.ArgumentParser(
        description="F357 — Build universe returns matrix.",
    )
    parser.add_argument(
        "--start",
        default="2015-01-02",
        help="First entry date (YYYY-MM-DD, default: 2015-01-02)",
    )
    parser.add_argument(
        "--end",
        default="2024-12-31",
        help="Last entry date (YYYY-MM-DD, default: 2024-12-31)",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT_PATH),
        help="Output Parquet path (default: backend/data/universe_matrix.parquet)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="File to tee log output to (required for long runs)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="ProcessPool worker count (default: cpu_count-1)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=_DEFAULT_CHUNK_SIZE,
        help=f"Tickers per worker chunk (default: {_DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--data-source",
        default="yahoo",
        help="Price data source (default: yahoo)",
    )
    parser.add_argument(
        "--price-cache-dir",
        default=None,
        help="Override price cache directory",
    )
    parser.add_argument(
        "--universe-file",
        default=None,
        help="Path to a plain-text file with one ticker per line (default: auto-discover)",
    )
    return parser.parse_args()


def _get_universe_tickers(universe_file: Optional[str] = None) -> list[str]:
    """Load universe tickers: from file if given, else auto-discover from price cache."""
    if universe_file is not None:
        tickers = Path(universe_file).read_text().splitlines()
        return [t.strip() for t in tickers if t.strip()]

    # Auto-discover: replicate _build_universe_tickers logic from run_r1_explore
    price_cache_dir = _BACKEND_DIR / "data" / "turnaround" / "price_cache" / "v1"
    subs_dir = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "submissions"

    if not price_cache_dir.exists():
        raise FileNotFoundError(f"Price cache not found: {price_cache_dir}")
    if not subs_dir.exists():
        raise FileNotFoundError(f"Submissions dir not found: {subs_dir}")

    _PRICE_SPAN_START = "20120101"
    _PRICE_SPAN_END = "20211231"

    covering: set[str] = set()
    for f in price_cache_dir.iterdir():
        if not f.name.endswith(".pkl"):
            continue
        parts = f.stem.split("_")
        if len(parts) < 5:
            continue
        ticker = parts[0]
        start = parts[3]
        end = parts[4]
        if start <= _PRICE_SPAN_START and end >= _PRICE_SPAN_END:
            covering.add(ticker)

    log.info("Price-cache covering set (2012-2021 span): %d tickers", len(covering))

    universe: list[str] = []
    for subs_file in sorted(subs_dir.iterdir()):
        if not subs_file.name.endswith(".json"):
            continue
        try:
            d = json.loads(subs_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        tickers = d.get("tickers", [])
        if not tickers:
            continue
        ticker = tickers[0]
        if ticker not in covering:
            continue
        sic = d.get("sic")
        if sic and int(sic) != 0:
            universe.append(ticker)

    log.info("Universe tickers (SIC-bearing, 2012-2021 cache): %d", len(universe))
    return universe


def _generate_trading_dates(start: date, end: date) -> list[date]:
    """Generate weekday dates between start and end (inclusive).

    Note: does not exclude US market holidays. The price cache is the truth —
    if a date has no price data, it will simply produce no rows in the matrix.
    Workers will naturally skip dates where no symbols trade.
    """
    current = start
    dates: list[date] = []
    while current <= end:
        if current.weekday() < 5:  # Monday=0 ... Friday=4
            dates.append(current)
        current = current + timedelta(days=1)
    return dates


def _main() -> None:
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    args = _parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    # Load universe
    log.info("Loading universe tickers...")
    universe_tickers = _get_universe_tickers(args.universe_file)
    log.info("Universe: %d tickers", len(universe_tickers))

    # Generate trading dates
    log.info("Generating trading dates %s -> %s...", start_date, end_date)
    entry_dates = _generate_trading_dates(start_date, end_date)
    log.info("Entry dates: %d", len(entry_dates))

    # Build
    out = build_universe_returns_matrix(
        universe_tickers=universe_tickers,
        entry_dates=entry_dates,
        horizons=_DEFAULT_HORIZONS,
        start_year=start_date.year,
        end_year=end_date.year,
        low_lookback_years=3,
        horizon_months=7,
        data_source=args.data_source,
        price_cache_dir=Path(args.price_cache_dir) if args.price_cache_dir else None,
        max_workers=args.max_workers,
        chunk_size=args.chunk_size,
        output_path=Path(args.output),
        log_file=Path(args.log_file) if args.log_file else None,
    )

    log.info("Done. Artifact: %s (%.1f MB)", out, out.stat().st_size / 1e6)


if __name__ == "__main__":
    _main()
