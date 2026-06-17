"""run_f370_explore.py — F370 PEAD / earnings-surprise explore-0 driver.

Explore-0 feasibility screen: doses 1-3 (earnings_yoy, revenue_yoy, composite),
no std_sue (gated on F348 add), no confirm (explore-0 only).

NON-LEDGERED: explore-0 passes fdr_ledger_path=None everywhere. This is a
feasibility screen, not a registered study. Never writes the real FDR ledger.
The confirm window (2021-2024) is never touched.

Design spec: docs/plans/2026-06-08-F370-pead-surprise-charter-spec.md
Seed: 20260608 (frozen). Matrix pin: "universe medians from matrix build 2026-06-07".
Reuse boundary: event_study.run_event_study, fundamental_surprise.build_pead_surprise_events,
r1_analysis low-level helpers, power_audit.run_audit, universe_loader.build_liquid_universe.

Usage:
    backend/venv/bin/python3 backend/research/run_f370_explore.py [--workers N]

Artifacts written to:
    backend/data/turnaround/event_studies/f370_pead_explore_2015_2020/
        run.log          (live-tailable; tail -f <dir>/run.log for progress)
        events.ndjson    (outcome rows)
        meta.json        (harness meta)
        f370_explore_summary.json   (per-dose × per-horizon stats + power_audit MDE
                                     + gap-lens summary)
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import json
import logging
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

_ET_TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Path setup (mirrors run_r1b_explore.py)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in [str(_BACKEND_DIR), str(_SCRIPT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen paths
# ---------------------------------------------------------------------------
_EDGAR_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache"
_SUBS_DIR = _EDGAR_CACHE_DIR / "submissions"
_PRICE_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"
_MATRIX_PATH = _BACKEND_DIR / "data" / "universe_matrix.parquet"

# ---------------------------------------------------------------------------
# Frozen study constants
# ---------------------------------------------------------------------------
STUDY_NAME = "f370_pead_explore_2015_2020"

# §3a: primary seed (frozen)
_SEED = 20260608

# Explore window (§ explore-0 spec)
_EXPLORE_START = date(2015, 1, 1)
_EXPLORE_END = date(2020, 12, 31)

# Loader span: 2012-01-01 pre-event floor checks, through 2021-12-31 (126td fwd)
_START_YEAR = 2015
_END_YEAR = 2020
_LOW_LOOKBACK_YEARS = 2
_HORIZON_MONTHS = 6
_DATA_SOURCE = "yahoo"

_PRICE_SPAN_START = "20120101"
_PRICE_SPAN_END = "20211231"

# Matrix pin phrase (charter discipline)
_MATRIX_PIN_PHRASE = "universe medians from matrix build 2026-06-07"

# Primary horizon
_PRIMARY_HORIZON = 63
_HORIZONS = (21, 63, 126)

_PROGRESS_LOG_INTERVAL = 50


# ---------------------------------------------------------------------------
# _attach_run_log — mirrors run_r1b_explore.py exactly
# ---------------------------------------------------------------------------

def _attach_run_log(output_dir: Path) -> None:
    """Tee all driver/harness logging to <study_dir>/run.log.

    tail -f <study_dir>/run.log is the supported live-progress channel.
    (John, 2026-06-06: the first explore run was piped through tail which
    buffers until exit — 76 minutes zero visibility.)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(fh)


# ---------------------------------------------------------------------------
# F365-compatible disk-only loader factory (module-level for pickle safety)
# ---------------------------------------------------------------------------

def make_disk_only_loader(
    start_year: int,
    end_year: int,
    low_lookback_years: int,
    horizon_months: int,
    data_source: str,
    price_cache_dir: Optional[str] = None,
) -> Callable[[str], Optional[object]]:
    """Build a disk-only price loader for F365 parallel workers.

    Workers use this instead of _make_memoized_loader because:
    1. Closures from _make_memoized_loader are not picklable across fork.
    2. Workers must NOT re-fetch from the network — the parent's prefetch
       already wrote all required frames to the on-disk PriceFrameCache.
       A disk miss returns None (treated as "no price data" — survivorship
       consistent with a fresh cache miss).

    The factory signature matches the loader_factory protocol:
    ("research.run_f370_explore:make_disk_only_loader", {kwargs}).
    Must be module-level for macOS spawn safety.

    Copied VERBATIM from run_r1b_explore.make_disk_only_loader (the proven
    sibling pattern). Uses cache.load(ticker, fetch_start, fetch_end,
    data_source) — the real PriceFrameCache API. The old version called
    cache._make_key() + cache.get() which do not exist (crash fix K1).
    """
    import pandas as pd
    from turnaround_validation import PriceFrameCache

    fetch_start_year = start_year - low_lookback_years - 1
    fetch_end_year = end_year + max(1, (horizon_months + 11) // 12) + 1
    fetch_start = f"{fetch_start_year}-01-01"
    fetch_end = f"{fetch_end_year}-12-31"

    if price_cache_dir is not None:
        cache = PriceFrameCache(cache_dir=Path(price_cache_dir))
    else:
        # Default: same path as the main loader (PriceFrameCache default).
        cache = PriceFrameCache()

    _mem_cache: dict[str, Optional[pd.DataFrame]] = {}

    def _loader(ticker: str) -> Optional[pd.DataFrame]:
        """Disk-only loader: in-process memo → disk cache → None (no network)."""
        if ticker in _mem_cache:
            return _mem_cache[ticker]
        result = cache.load(ticker, fetch_start, fetch_end, data_source)
        _mem_cache[ticker] = result
        return result

    return _loader


# ---------------------------------------------------------------------------
# Universe builder
# ---------------------------------------------------------------------------

def _build_universe_tickers(event_tickers: Optional[list[str]] = None) -> list[str]:
    """Return all tickers with price-cache span 2012-2021 + SIC.

    Mirrors run_r1b_explore._build_universe_tickers.
    """
    from research.universe_loader import build_liquid_universe
    return build_liquid_universe(
        price_cache_dir=_PRICE_CACHE_DIR / "v1",
        subs_dir=_SUBS_DIR,
        span_start=_PRICE_SPAN_START,
        span_end=_PRICE_SPAN_END,
        extra_tickers=event_tickers,
    )


# ---------------------------------------------------------------------------
# Gap-lens: link 10-Q/10-K filing to its matching 8-K item 2.02
# ---------------------------------------------------------------------------

def _adt_to_et_date(adt: str) -> Optional[str]:
    """Parse an EDGAR acceptanceDateTime → ET calendar date ISO string (COR-02).

    Returns None on parse failure. After-hours filings are dated by ET wall-clock
    (mirrors fundamental_surprise.py), so a 21:30Z filing reads as the same ET day.
    """
    try:
        s = str(adt)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if "." in s:  # strip fractional seconds, preserve any offset
            dot = s.index(".")
            after = s[dot + 1:]
            for sep in ("+", "-"):
                if sep in after:
                    s = s[:dot] + sep + after.split(sep, 1)[1]
                    break
            else:
                s = s[:dot] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_ET_TZ).date().isoformat()
    except Exception:
        return None


def _build_eightk_202_index(
    cik_to_ticker: dict,
    submissions_dir: Path,
) -> dict:
    """Scan submissions/*.json and return a map:

        padded_cik -> sorted list of 8-K item-2.02 ET announcement dates (ISO str)

    The 8-K item-2.02 "reportDate" is the EARNINGS-ANNOUNCEMENT date, NOT the
    fiscal period end (verified 2026-06-08: 0/N match against derived period-ends
    across all sampled CIKs). So the gap lens links by TIME, not by period key:
    for each 10-Q/10-K we later pick the most recent 2.02 announcement filed just
    before the filing date. Identification mirrors premise_power_census.run_eightk:
    form=="8-K" with "2.02" in the recent "items" field.
    """
    log.info("Building 8-K item-2.02 index from submissions ...")
    t0 = time.monotonic()

    index: dict[str, list[str]] = {}  # padded_cik -> [ET announce date, ...]

    for fp in sorted(submissions_dir.glob("*.json")):
        cik = fp.stem  # zero-padded; matches cik_to_ticker keys (both zfill(10))
        if cik not in cik_to_ticker:
            continue
        try:
            with open(fp) as f:
                data = json.load(f)
        except Exception:
            continue

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accept_dts = recent.get("acceptanceDateTime", [])
        items_list = list(recent.get("items", []))
        while len(items_list) < len(forms):
            items_list.append("")

        dates: list[str] = []
        for form, adt, items_str in zip(forms, accept_dts, items_list):
            if form != "8-K" or not adt:
                continue
            codes = [c.strip() for c in items_str.split(",") if c.strip()] if items_str else []
            if "2.02" not in codes:
                continue
            et = _adt_to_et_date(adt)
            if et:
                dates.append(et)
        if dates:
            index[cik] = sorted(dates)

    elapsed = time.monotonic() - t0
    log.info(
        "8-K item-2.02 index built: %d CIKs with >=1 item-2.02 (%d total announcements) in %.1fs",
        len(index), sum(len(v) for v in index.values()), elapsed,
    )
    return index


def return_between_dates(
    ticker: str,
    start_date: str,
    end_date: str,
    loader_fn: Callable,
) -> Optional[float]:
    """Compute the return from start_date to end_date for ticker (close-to-close).

    Uses bisect over the frame's sorted date index (avoids iterating all rows).
    Returns None if the ticker has no price data or the dates are out of range.
    start_date and end_date are ISO strings "YYYY-MM-DD".

    Return = (close[end_date_or_next] / close[start_date_or_next]) - 1, in pct.
    """
    import pandas as pd
    frame = loader_fn(ticker)
    if frame is None or frame.empty:
        return None

    # Normalize index to date strings (MAINT-06: both DatetimeIndex and plain
    # string indices use the same [:10] slice — the if/else was dead code).
    idx = frame.index
    date_strs = [str(d)[:10] for d in idx]

    if not date_strs:
        return None

    # Find the closest entry on-or-after start_date and end_date
    i_start = bisect.bisect_left(date_strs, start_date)
    i_end = bisect.bisect_left(date_strs, end_date)

    if i_start >= len(date_strs) or i_end >= len(date_strs):
        return None
    if i_start == i_end:
        return 0.0

    try:
        close_col = None
        for col in ("Close", "close", "Adj Close", "adj_close"):
            if col in frame.columns:
                close_col = col
                break
        if close_col is None:
            return None

        p_start = float(frame.iloc[i_start][close_col])
        p_end = float(frame.iloc[i_end][close_col])
        if p_start <= 0 or not math.isfinite(p_start) or not math.isfinite(p_end):
            return None
        return 100.0 * (p_end / p_start - 1.0)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-dose analysis loop (reuses r1_analysis low-level helpers)
# ---------------------------------------------------------------------------

def _run_dose_analysis(
    valid_rows: list[dict],
    score_fn: Callable,
    dose_name: str,
    horizons: tuple[int, ...],
    seed: int,
) -> dict:
    """Run dose-response analysis for one dose using r1_analysis helpers.

    Imports and calls the low-level helpers (NOT run_r1_analysis):
      _assign_quintiles_all_years, _per_quintile_stats,
      _two_sample_mbb_bootstrap, _spearman_exact_onesided,
      _compute_mde_q5q1, _nw_pvalue_for_diff, _per_year_quintile_rho

    Returns a dict with per-horizon results.
    """
    import numpy as np
    from datetime import date as _date
    from research.r1_analysis import (
        _assign_quintiles_all_years,
        _compute_mde_q5q1,
        _nw_pvalue_for_diff,
        _per_quintile_stats,
        _per_year_quintile_rho,
        _spearman_exact_onesided,
        _two_sample_mbb_bootstrap,
    )
    from research.event_study import _block_size_for_horizon

    rng = np.random.default_rng(seed)

    # Count coverage: rows where score_fn returns non-None
    n_total = len(valid_rows)
    n_with_dose = sum(1 for r in valid_rows if score_fn(r) is not None)
    coverage_pct = 100.0 * n_with_dose / n_total if n_total > 0 else 0.0
    log.info(
        "Dose '%s': n_total=%d, n_with_dose=%d, coverage=%.1f%%",
        dose_name, n_total, n_with_dose, coverage_pct,
    )

    # Assign quintiles within-year (uses the r1_analysis helper directly)
    quintiles = _assign_quintiles_all_years(valid_rows, score_fn=score_fn)

    # C370-04 fix: define _get_dates_for_quintile once, outside the horizon loop,
    # with explicit parameters so there is no loop-variable capture.  The old pattern
    # (defining the function inside `for h in horizons`) was correct because the
    # function body does not use `h`, but re-definition on every iteration is
    # recognised as a Python gotcha (if the call pattern ever changes to lazy
    # evaluation the captured binding would silently use the last h value).
    def _get_dates_for_quintile(
        q_label: int,
        _rows: list = valid_rows,
        _qs: list = quintiles,
    ) -> list:
        dates = []
        for row, q in zip(_rows, _qs):
            if q != q_label:
                continue
            ed = row.get("entry_date", "")
            if not ed:
                continue
            try:
                dates.append(_date.fromisoformat(str(ed)[:10]))
            except Exception:
                pass
        return sorted(dates)

    horizon_results: dict[int, dict] = {}
    for h in horizons:
        pqs = _per_quintile_stats(valid_rows, quintiles, h)

        # Extract Q5 and Q1 arrays
        q5_vals = pqs.get(5, {}).get("values", np.array([]))
        q1_vals = pqs.get(1, {}).get("values", np.array([]))
        q5_mean = pqs.get(5, {}).get("mean")
        q1_mean = pqs.get(1, {}).get("mean")
        n5 = pqs.get(5, {}).get("n", 0)
        n1 = pqs.get(1, {}).get("n", 0)

        q5q1_gap = None
        if q5_mean is not None and q1_mean is not None:
            q5q1_gap = q5_mean - q1_mean

        # Block-bootstrap p (one-sided H1: Q5 > Q1)
        # Block size uses event_study._block_size_for_horizon(h, entry_dates)
        p_boot = None
        if n5 >= 2 and n1 >= 2:
            q5_dates = _get_dates_for_quintile(5)
            q1_dates = _get_dates_for_quintile(1)
            L_a = _block_size_for_horizon(h, q5_dates)
            L_b = _block_size_for_horizon(h, q1_dates)

            p_boot, _boot_diffs, _La_used, _cap_a, _Lb_used, _cap_b = (
                _two_sample_mbb_bootstrap(
                    q5_vals, q1_vals,
                    block_size_a=L_a,
                    block_size_b=L_b,
                    n_boot=999,
                    rng=rng,
                )
            )

        # Spearman ρ + exact p (over the 5 quintile means)
        q_means = []
        q_labels = []
        for q in range(1, 6):
            m = pqs.get(q, {}).get("mean")
            if m is not None:
                q_labels.append(float(q))
                q_means.append(m)
        rho_s = None
        p_spearman = None
        if len(q_labels) == 5:
            rho_s, p_spearman = _spearman_exact_onesided(
                np.array(q_labels), np.array(q_means)
            )

        # MDE (Q5-Q1 spread)
        mde = _compute_mde_q5q1(q5_vals, q1_vals)

        # NW cross-check: use pooled Q1+Q5 dates for block size
        p_nw = None
        if n5 >= 2 and n1 >= 2:
            all_dates_nw = []
            for r, q in zip(valid_rows, quintiles):
                if q not in (1, 5):
                    continue
                ed = r.get("entry_date", "")
                if not ed:
                    continue
                try:
                    all_dates_nw.append(_date.fromisoformat(str(ed)[:10]))
                except Exception:
                    pass
            L_nw = _block_size_for_horizon(h, sorted(all_dates_nw)) if all_dates_nw else max(1, h // 10)
            p_nw = _nw_pvalue_for_diff(q5_vals, q1_vals, block_size=L_nw)

        # Per-year / era rho
        per_year_result = _per_year_quintile_rho(valid_rows, quintiles, h)

        # Per-quintile summary
        per_quintile = {}
        for q in range(1, 6):
            per_quintile[q] = {
                "mean": pqs.get(q, {}).get("mean"),
                "n": pqs.get(q, {}).get("n", 0),
            }

        # K4/DI-F370-05: report per-horizon event count (sum of all quintile ns).
        # valid_rows is filtered on 63td non-null excess; for 21td and 126td horizons
        # some rows may lack that horizon's return, so n_events_h can differ from
        # len(valid_rows).  This is the authoritative count for each horizon.
        n_events_h = int(sum(pqs.get(q, {}).get("n", 0) for q in range(1, 6)))

        horizon_results[h] = {
            "n_events": n_events_h,
            "q5q1_gap_pct": q5q1_gap,
            "p_boot": float(p_boot) if p_boot is not None and math.isfinite(p_boot) else p_boot,
            "rho_s": float(rho_s) if rho_s is not None else None,
            "p_spearman": float(p_spearman) if p_spearman is not None else None,
            "mde_pp": float(mde) if math.isfinite(mde) else None,
            "p_nw": float(p_nw) if p_nw is not None and math.isfinite(p_nw) else p_nw,
            "n5": int(n5),
            "n1": int(n1),
            "frac_years_positive_rho": per_year_result.get("frac_years_positive"),
            "per_year_rho": per_year_result.get("per_year"),
            "per_quintile": per_quintile,
        }

    return {
        "dose_name": dose_name,
        "n_total": n_total,
        "n_with_dose": n_with_dose,
        "coverage_pct": coverage_pct,
        "by_horizon": horizon_results,
    }


# ---------------------------------------------------------------------------
# Module-level picklable worker for parallel dose dispatch (F380a)
# ---------------------------------------------------------------------------

# Sentinel key used to carry the pre-computed composite score in row dicts.
# Defined at module level so workers can reference it without capturing a
# closure from the parent process.
_COMPOSITE_KEY = "_f370_composite_score"

# Payload key for each dose (earnings/revenue are directly in the payload;
# composite is injected into the row dict under _COMPOSITE_KEY).
_DOSE_PAYLOAD_KEYS: dict[str, str] = {
    "earnings_yoy": "earnings_yoy",
    "revenue_yoy": "revenue_yoy",
    "composite": _COMPOSITE_KEY,
}


def _score_by_key(row: dict, payload_key: str) -> Optional[float]:
    """Return the dose score for ``row`` given a payload key.

    For 'earnings_yoy' and 'revenue_yoy': reads from ``row['payload']``.
    For _COMPOSITE_KEY: reads from the row directly (injected by the driver).

    Module-level so it is picklable across fork boundaries.
    """
    if payload_key == _COMPOSITE_KEY:
        v = row.get(_COMPOSITE_KEY)
    else:
        v = (row.get("payload") or {}).get(payload_key)
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _dose_worker(task: tuple, _parallel_map_seed: int) -> dict:
    """Picklable worker: run _run_dose_analysis for one dose.

    ``task`` is ``(dose_name, payload_key, valid_rows, horizons, seed)``.

    MAINT-01 / RM-05 — EMBEDDED-SEED SCHEME (intentional, non-fragile):
    The seed is embedded in the task tuple rather than using parallel_map's
    auto-derived XOR seed.  Rationale:

      * All three doses MUST run with the same base seed (_SEED = 20260608)
        so their bootstrap results are comparable and byte-identical to the
        serial path.

      * parallel_map's contract derives ``seed = seed_base ^ task_index``,
        giving doses 0/1/2 seeds [_SEED, _SEED^1, _SEED^2] — three DIFFERENT
        seeds — which would break comparability across doses.

      * The caller embeds ``_SEED`` directly in every task tuple and the
        worker reads it from there.  This is correct and intentional.

    ``_parallel_map_seed`` (the XOR-derived value from parallel_map) is
    therefore explicitly IGNORED here.  If you are tempted to use it, first
    verify that the caller passes seed_base=_SEED and all tasks embed the
    same seed — otherwise results will differ from the serial path.

    Module-level (pickle-safe on macOS spawn).
    """
    dose_name, payload_key, valid_rows, horizons, seed = task

    def _score_fn(row: dict) -> Optional[float]:
        return _score_by_key(row, payload_key)

    return _run_dose_analysis(
        valid_rows=valid_rows,
        score_fn=_score_fn,
        dose_name=dose_name,
        horizons=horizons,
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Progress wrapper (mirrors run_r1b_explore._progress_wrap)
# ---------------------------------------------------------------------------

class _progress_wrap:
    def __init__(self, events, interval: int = 50):
        self._events = list(events)
        self._interval = interval

    def __iter__(self):
        t0 = time.monotonic()
        n = len(self._events)
        for i, ev in enumerate(self._events):
            if i > 0 and i % self._interval == 0:
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (n - i) / rate if rate > 0 else 0
                log.info(
                    "Event progress: %d / %d (%.1f%%) — %.1f/s — ETA %.0fs",
                    i, n, 100 * i / n, rate, eta,
                )
            yield ev

    def __len__(self):
        return len(self._events)


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def run(workers: int = 1) -> None:
    """Run the F370 explore-0 study.

    NON-LEDGERED (explore-0): fdr_ledger_path=None everywhere.
    Never writes the real FDR ledger. Never touches the confirm window.
    """
    from research.event_study import EventStudyConfig, run_event_study
    from research.fundamental_surprise import build_pead_surprise_events
    from research.dose_builders import composite_dose
    from turnaround_validation import _make_memoized_loader
    import numpy as np

    t0 = time.monotonic()

    output_dir = _STUDIES_DIR / STUDY_NAME
    _attach_run_log(output_dir)

    log.info("=" * 60)
    log.info("F370 explore-0 PEAD / earnings-surprise study")
    log.info("Explore window: %s — %s", _EXPLORE_START, _EXPLORE_END)
    log.info("SEED: %d", _SEED)
    log.info("Matrix pin: %s", _MATRIX_PIN_PHRASE)
    log.info(
        "IMPORTANT: explore-0 is NON-LEDGERED (fdr_ledger_path=None). "
        "This is a feasibility screen only; it does not register in the FDR ledger."
    )
    log.info("=" * 60)

    # ------------------------------------------------------------------
    # 1. Build the memoized loader for price data
    # ------------------------------------------------------------------
    log.info("Building memoized price loader (span %d-%d, %s) ...",
             _START_YEAR, _END_YEAR, _DATA_SOURCE)
    loader = _make_memoized_loader(
        start_year=_START_YEAR,
        end_year=_END_YEAR,
        low_lookback_years=_LOW_LOOKBACK_YEARS,
        horizon_months=_HORIZON_MONTHS,
        data_source=_DATA_SOURCE,
    )
    log.info("Memoized loader built.")

    # ------------------------------------------------------------------
    # 2. Build universe
    # ------------------------------------------------------------------
    log.info("Building universe tickers ...")
    universe_tickers = _build_universe_tickers()
    log.info("Universe: %d tickers", len(universe_tickers))

    # ------------------------------------------------------------------
    # 3. Enumerate F348 surprise events
    # ------------------------------------------------------------------
    log.info("Building PEAD surprise events (10-Q + 10-K, 2015-2020) ...")
    t_events_start = time.monotonic()
    events, events_meta = build_pead_surprise_events(
        universe_tickers=universe_tickers,
        span_start=str(_EXPLORE_START),
        span_end=str(_EXPLORE_END),
        forms=("10-Q", "10-K"),
        submissions_dir=_SUBS_DIR,
    )
    log.info(
        "F348 events: n_events=%d, n_filings_seen=%s, n_in_universe=%s (%.1fs)",
        len(events),
        events_meta.get("n_filings_seen", "?"),
        events_meta.get("n_in_universe", "?"),
        time.monotonic() - t_events_start,
    )

    # ------------------------------------------------------------------
    # 4. Configure event study (NON-LEDGERED; matrix_strict=True)
    # ------------------------------------------------------------------
    cfg = EventStudyConfig(
        study_name=STUDY_NAME,
        horizons=_HORIZONS,
        explore_cutoff=_EXPLORE_END,       # §8: hard ceiling 2020-12-31
        entry_lag_days=1,
        dedup_same_ticker=True,
        dedup_window_days=30,
        n_boot=999,
        fdr_q=0.10,
        min_peer_count=8,
        output_dir=output_dir,
        fdr_ledger_path=None,              # explore-0: NON-LEDGERED
        allow_post_2020_explore=False,
    )
    log.info(
        "EventStudyConfig: study=%s, horizons=%s, dedup_window=%d, fdr_ledger_path=None (NON-LEDGERED)",
        cfg.study_name, cfg.horizons, cfg.dedup_window_days,
    )

    # ------------------------------------------------------------------
    # 5. Run event study with frozen matrix (matrix_strict=True)
    # ------------------------------------------------------------------
    rng = np.random.default_rng(_SEED)
    log.info(
        "Running run_event_study (rng seed=%d, use_matrix=True, matrix_strict=True, "
        "matrix_path=%s, workers=%d) ...",
        _SEED, _MATRIX_PATH, workers,
    )

    _loader_factory = (
        "research.run_f370_explore:make_disk_only_loader",
        {
            "start_year": _START_YEAR,
            "end_year": _END_YEAR,
            "low_lookback_years": _LOW_LOOKBACK_YEARS,
            "horizon_months": _HORIZON_MONTHS,
            "data_source": _DATA_SOURCE,
        },
    )

    events_wrapped = _progress_wrap(events, interval=_PROGRESS_LOG_INTERVAL)
    t_harness = time.monotonic()
    outcomes, harness_meta = run_event_study(
        events=events_wrapped,
        config=cfg,
        loader_fn=loader,
        universe_tickers=universe_tickers,
        rng=rng,
        use_matrix=True,
        matrix_path=_MATRIX_PATH,
        matrix_strict=True,
        workers=workers,
        loader_factory=_loader_factory if workers > 1 else None,
    )
    log.info(
        "Harness done in %.1fs. n_events=%d, n_explore=%d, n_confirm=%d",
        time.monotonic() - t_harness,
        harness_meta.get("n_events"),
        harness_meta.get("n_explore"),
        harness_meta.get("n_confirm"),
    )
    # Safety invariant: explore-0 must never ENUMERATE a confirm-era filing.
    # A confirm-SPLIT event is NOT a leak — an event accepted on/before the
    # cutoff whose first tradeable open falls in 2021+ (sparse/late price data,
    # or the year boundary) is tagged "confirm" by the harness and EXCLUDED from
    # the explore analysis below. The real threat is enumerating a filing DATED
    # after the cutoff; that is what we assert against. (Diag 2026-06-08: all
    # 1257 confirm-split events had event_ts <= 2020-12-31; entries spilled to
    # 2021-2022 — see .run/F370/decisions.md.)
    n_confirm = harness_meta.get("n_confirm", 0) or 0
    _max_event_ts = max((o.event_ts for o in outcomes), default=None)
    # 1-day grace absorbs the ET->UTC midnight rollover (a Dec-31-ET filing can
    # carry a Jan-1 UTC event_ts); a real confirm-era leak is months past this.
    assert _max_event_ts is None or _max_event_ts.strftime("%Y-%m-%d") <= "2021-01-01", (
        f"explore-0 enumerated a confirm-era filing: max event_ts {_max_event_ts} "
        f"is past the {_EXPLORE_END} cutoff (+1d grace)."
    )
    log.info(
        "n_confirm=%d confirm-SPLIT events (entry in confirm era; EXCLUDED from "
        "explore analysis, not a leak). No confirm-era enumeration: max event_ts=%s.",
        n_confirm, _max_event_ts,
    )

    # ------------------------------------------------------------------
    # 6. Filter to explore split + floor_status ok
    # ------------------------------------------------------------------
    # EventOutcome is a dataclass; convert to dicts for r1_analysis helpers.
    # dataclasses imported at module top.
    def _outcome_to_dict(o) -> dict:
        if dataclasses.is_dataclass(o):
            d = dataclasses.asdict(o)
        elif hasattr(o, "__dict__"):
            d = dict(o.__dict__)
        else:
            d = dict(o)
        return d

    rows = [_outcome_to_dict(o) for o in outcomes]

    explore_rows = [
        r for r in rows
        if r.get("split") == "explore" and r.get("floor_status") == "ok"
    ]
    log.info(
        "Explore rows (split=explore, floor_status=ok): %d / %d total outcomes",
        len(explore_rows), len(rows),
    )

    # ------------------------------------------------------------------
    # 7. Build dose scores on the explore rows
    # ------------------------------------------------------------------
    # Dose 3: composite (frozen formula — computed cross-sectionally over explore_rows).
    # C370-02/DI-F370-01: z-scores are computed over explore_rows (all floor_ok events)
    # while quintile analysis runs on valid_rows (subset with non-null 63td excess).
    # This is intentionally rank-preserving: the z-scores are computed on the larger
    # population first, then the valid_rows subset retains their relative ranks unchanged.
    # Q5−Q1 is unaffected. No logic change needed; this comment documents the rationale.
    log.info("Computing composite dose (cross-sectional z-scores over explore rows) ...")
    composite_scores = composite_dose(explore_rows)
    # Attach composite score to each row under the module-level _COMPOSITE_KEY
    # so _score_by_key() can read it in workers without a closure.
    # (_COMPOSITE_KEY is defined at module level for pickle safety.)
    for i, row in enumerate(explore_rows):
        row[_COMPOSITE_KEY] = composite_scores[i]

    # ------------------------------------------------------------------
    # 8. Run per-dose analysis (reusing r1_analysis low-level helpers)
    # ------------------------------------------------------------------
    # Filter to rows with at least one valid 63td excess.
    # Use `is not None` throughout — a zero excess (0.0) is a valid data point;
    # truthiness checks would silently drop exact-zero events (C370-01 fix).
    def _has_valid_excess(row, h=63):
        m = row.get("fwd_excess_pct") or {}
        v = m.get(str(h))
        if v is None:
            v = m.get(h)
        return v is not None

    valid_rows = [r for r in explore_rows if _has_valid_excess(r, 63)]
    log.info("Valid rows for analysis (non-null 63td excess): %d", len(valid_rows))

    # F380(a): Parallel dose dispatch.
    # Dose × horizon cells are independent; dispatch each dose as a separate
    # ProcessPool task.  _dose_worker is module-level (pickle-safe on macOS
    # spawn) and receives a deterministic per-task seed via parallel_map.
    # workers=1 falls back to the exact serial path (byte-for-byte identical
    # results — parallel_map serial path calls fn(task, seed) in input order).
    from research.parallel_map import parallel_map as _parallel_map

    _dose_name_to_key = {
        "earnings_yoy": "earnings_yoy",
        "revenue_yoy": "revenue_yoy",
        "composite": _COMPOSITE_KEY,
    }
    _dose_order = ["earnings_yoy", "revenue_yoy", "composite"]
    # Seed is embedded in the task tuple (not from parallel_map's XOR
    # derivation) so each dose uses the same _SEED as the serial path,
    # preserving byte-identical results.
    _dose_tasks = [
        (dn, _dose_name_to_key[dn], valid_rows, _HORIZONS, _SEED)
        for dn in _dose_order
    ]

    # Use min(workers, 3) for dose parallelism — only 3 doses.
    _dose_workers = min(workers, len(_dose_order))
    log.info(
        "F380(a): Running dose analysis (%d doses, %d workers, seed=%d) ...",
        len(_dose_order), _dose_workers, _SEED,
    )
    t_dose = time.monotonic()
    _dose_results_list = _parallel_map(
        _dose_worker, _dose_tasks,
        workers=_dose_workers,
        seed_base=_SEED,
    )
    log.info("Dose analysis done in %.1fs (workers=%d).", time.monotonic() - t_dose, _dose_workers)

    dose_results = {}
    for result in _dose_results_list:
        dose_name = result["dose_name"]
        dose_results[dose_name] = result
        h_primary = result["by_horizon"].get(_PRIMARY_HORIZON, {})
        log.info(
            "  %s @ 63td: Q5-Q1=%.3f%%, p_boot=%s, rho_s=%s, MDE=%.3f%%",
            dose_name,
            h_primary.get("q5q1_gap_pct") or 0.0,
            h_primary.get("p_boot"),
            h_primary.get("rho_s"),
            h_primary.get("mde_pp") or float("nan"),
        )

    # ------------------------------------------------------------------
    # 9. Power audit (one-time; characterises the generic F340-design family)
    # ------------------------------------------------------------------
    # F381-1 (DI-F370-04): run_audit uses a GENERIC simulation population
    # (quarterly/monthly/event-time schedules on a random-ticker panel), NOT the
    # actual PEAD event structure.  Its mde_80pct is therefore NOT the design MDE
    # for this study — it characterises abstract schedule designs, not PEAD quintile
    # Q5-Q1 spreads.  The per-dose empirical _compute_mde_q5q1 (stored in
    # by_horizon[h]["mde_pp"] for each dose) is the authoritative go/no-go MDE.
    # We still run run_audit as a calibration reference (F340 smoke gate) but we
    # deliberately EXCLUDE its mde_80pct from the summary JSON to prevent it
    # being misread as the study's detectable effect size.
    log.info("Running power_audit.run_audit (n_reps=200, seed=%d, workers=%d) ...", _SEED, workers)
    t_pa = time.monotonic()
    from research.power_audit import run_audit
    pa_result = run_audit(
        n_reps=200,
        e_grid=[0, 0.5, 1.0, 1.5, 2.0, 3.0],
        seed=_SEED,
        verbose=True,
        workers=workers,
    )
    log.info(
        "Power audit done in %.1fs. "
        "(mde_80pct omitted from summary — generic-population MDE, not PEAD MDE; "
        "see by_horizon[h]['mde_pp'] per dose for the empirical design MDE.)",
        time.monotonic() - t_pa,
    )

    # ------------------------------------------------------------------
    # 10. Gap lens: 8-K item 2.02 → filing date return
    #     Mirrors the 8-K 2.02 scan pattern from premise_power_census.run_eightk()
    #     (the census function is too entangled to call directly — it runs the full
    #     scan and produces EventOutcome objects; we replicate the identification
    #     logic inline rather than importing the private helper).
    # ------------------------------------------------------------------
    log.info("Building gap lens (8-K item 2.02 to 10-Q/10-K filing date drift) ...")
    ticker_to_cik: dict[str, str] = {}
    gap_lens_summary: dict = {}
    try:
        universe_json = _EDGAR_CACHE_DIR / "universe.json"
        if universe_json.exists():
            with open(universe_json) as f:
                universe_data = json.load(f)
            cik_to_ticker = {
                str(e.get("cik_str", "")).zfill(10): e.get("ticker", "")
                for e in universe_data.values()
                if e.get("ticker")
            }
            ticker_to_cik = {v: k for k, v in cik_to_ticker.items() if v}

            eightk_index = _build_eightk_202_index(cik_to_ticker, _SUBS_DIR)

            # F380(b): Parallel gap-lens I/O.
            # Pre-resolve (ticker, announce_date, filing_date) pairs in a single
            # pass, then dispatch the disk reads concurrently via ThreadPoolExecutor.
            # ThreadPool (not ProcessPool) because:
            #   - loader is an in-process closure (not pickle-safe)
            #   - PriceFrameCache.load() is thread-safe (read-only)
            #   - GIL is released during pickle.load() I/O
            _GAP_WINDOW_DAYS = 90  # 2.02 for this quarter precedes its 10-Q by days-to-weeks
            # Phase 1: resolve matches (CPU-light, serial)
            _gap_candidates: list[tuple] = []  # (ticker, announce_date, filing_date, row)
            n_gap_missing_pre = 0
            for row in explore_rows[:]:
                ticker = row.get("ticker", "")
                cik = ticker_to_cik.get(ticker, "")
                filing_ts = row.get("event_ts")
                if not cik or not filing_ts:
                    n_gap_missing_pre += 1
                    continue
                filing_date = _adt_to_et_date(filing_ts)
                if not filing_date:
                    n_gap_missing_pre += 1
                    continue
                announces = eightk_index.get(cik, [])
                if not announces:
                    n_gap_missing_pre += 1
                    continue
                pos = bisect.bisect_left(announces, filing_date)
                if pos == 0:
                    n_gap_missing_pre += 1
                    continue
                announce_date = announces[pos - 1]
                try:
                    gap_days = (date.fromisoformat(filing_date) - date.fromisoformat(announce_date)).days
                except ValueError:
                    n_gap_missing_pre += 1
                    continue
                if gap_days <= 0 or gap_days > _GAP_WINDOW_DAYS:
                    n_gap_missing_pre += 1
                    continue
                _gap_candidates.append((ticker, announce_date, filing_date, row))

            # Phase 2: parallel disk reads via threads (I/O-bound)
            # CONC-03: respect --workers 1 as a "no extra parallelism" signal.
            # When workers==1, use exactly 1 thread (serial I/O path).
            # For workers>1, scale up to 4× workers (I/O-bound; safe to over-
            # subscribe relative to CPU count), capped at 32 and n_candidates.
            if workers <= 1:
                _gap_thread_workers = 1
            else:
                _gap_thread_workers = min(workers * 4, 32, len(_gap_candidates)) if _gap_candidates else 1
            _gap_thread_workers = max(1, _gap_thread_workers)
            log.info(
                "F380(b): Gap lens I/O — %d candidates, %d thread workers ...",
                len(_gap_candidates), _gap_thread_workers,
            )
            t_gap_io = time.monotonic()

            def _fetch_gap(candidate):
                ticker, announce_date, filing_date, row = candidate
                gap_ret = return_between_dates(ticker, announce_date, filing_date, loader)
                return gap_ret, ticker, announce_date, filing_date, row

            gap_returns = []
            n_gap_found = 0
            n_gap_missing = n_gap_missing_pre
            if _gap_candidates:
                with ThreadPoolExecutor(max_workers=_gap_thread_workers) as _tpool:
                    for gap_ret, ticker, announce_date, filing_date, row in _tpool.map(
                        _fetch_gap, _gap_candidates
                    ):
                        if gap_ret is not None:
                            n_gap_found += 1
                            gap_returns.append({
                                "ticker": ticker,
                                "announce_date": announce_date,
                                "filing_date": filing_date,
                                "gap_return_pct": gap_ret,
                                "earnings_yoy": (row.get("payload") or {}).get("earnings_yoy"),
                                "composite": row.get(_COMPOSITE_KEY),
                            })
                        else:
                            n_gap_missing += 1

            log.info(
                "Gap lens I/O done in %.1fs. n_found=%d, n_missing=%d (no 2.02 match or no price)",
                time.monotonic() - t_gap_io, n_gap_found, n_gap_missing,
            )

            if gap_returns:
                import numpy as np
                gap_ret_arr = [r["gap_return_pct"] for r in gap_returns]
                mean_gap = float(np.mean(gap_ret_arr))
                median_gap = float(np.median(gap_ret_arr))
                p25 = float(np.percentile(gap_ret_arr, 25))
                p75 = float(np.percentile(gap_ret_arr, 75))

                # Spearman rank correlation of gap return with dose (K6 fix).
                # The dose is used rank-wise everywhere else in the analysis;
                # Pearson (the old _corr / np.corrcoef) was the wrong measure.
                # scipy.stats.spearmanr already used in r1_analysis.
                from scipy.stats import spearmanr

                def _spearman_corr(x_list, y_list):
                    """Spearman ρ between two lists; returns None if <3 finite pairs."""
                    pairs = [(x, y) for x, y in zip(x_list, y_list)
                             if x is not None and y is not None
                             and math.isfinite(x) and math.isfinite(y)]
                    if len(pairs) < 3:
                        return None
                    xs = [p[0] for p in pairs]
                    ys = [p[1] for p in pairs]
                    rho, _ = spearmanr(xs, ys)
                    return float(rho) if math.isfinite(rho) else None

                corr_earnings = _spearman_corr(
                    [r.get("earnings_yoy") for r in gap_returns],
                    gap_ret_arr,
                )
                corr_composite = _spearman_corr(
                    [r.get("composite") for r in gap_returns],
                    gap_ret_arr,
                )

                gap_lens_summary = {
                    "n_found": n_gap_found,
                    "n_missing": n_gap_missing,
                    "mean_gap_return_pct": mean_gap,
                    "median_gap_return_pct": median_gap,
                    "p25_gap_return_pct": p25,
                    "p75_gap_return_pct": p75,
                    "corr_gap_vs_earnings_yoy": corr_earnings,
                    "corr_gap_vs_composite": corr_composite,
                }
                log.info(
                    "Gap lens summary: mean_gap=%.2f%%, median=%.2f%%, "
                    "corr_vs_earnings=%.3f, corr_vs_composite=%s",
                    mean_gap, median_gap, corr_earnings or float("nan"),
                    corr_composite,
                )
            else:
                gap_lens_summary = {"n_found": 0, "n_missing": n_gap_missing}
        else:
            log.warning("universe.json not found; gap lens skipped")
            gap_lens_summary = {"skipped": "universe.json not found"}
    except Exception as exc:
        log.warning("Gap lens error (report-only; not fatal): %s", exc, exc_info=True)
        gap_lens_summary = {"error": str(exc)}

    # ------------------------------------------------------------------
    # 11. Write summary artifact
    # ------------------------------------------------------------------
    # Clean up temporary composite key from rows
    for row in explore_rows:
        row.pop(_COMPOSITE_KEY, None)

    # Build JSON-serializable dose_results (convert numpy types)
    def _jsonify(obj):
        if isinstance(obj, dict):
            return {k: _jsonify(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_jsonify(v) for v in obj]
        if isinstance(obj, float) and not math.isfinite(obj):
            return None  # inf/nan -> null in JSON
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return None if not math.isfinite(float(obj)) else float(obj)
            if isinstance(obj, np.ndarray):
                return [_jsonify(x) for x in obj.tolist()]
        except ImportError:
            pass
        return obj

    # RM-03: PROGRAM.md requires "every charter states its design MDE".
    # power_audit's mde_80pct is OMITTED (generic population — not PEAD).
    # Expose per-dose empirical mde_pp at primary horizon (63td) as the
    # labeled design MDE so the charter requirement is formally met.
    # This is the authoritative go/no-go MDE for this study.
    _design_mde_pp: dict[str, object] = {}
    for _dn, _dr in dose_results.items():
        _h_primary = _dr.get("by_horizon", {}).get(_PRIMARY_HORIZON, {})
        _design_mde_pp[_dn] = _h_primary.get("mde_pp")  # float or None

    summary = {
        "study_name": STUDY_NAME,
        "explore_start": str(_EXPLORE_START),
        "explore_end": str(_EXPLORE_END),
        "seed": _SEED,
        "matrix_pin": _MATRIX_PIN_PHRASE,
        "fdr_ledger": "None (explore-0 NON-LEDGERED feasibility screen)",
        "n_events_harness": harness_meta.get("n_events"),
        "n_explore": harness_meta.get("n_explore"),
        "n_valid_for_analysis": len(valid_rows),
        # RM-03: per-dose empirical design MDE at primary horizon (63td).
        # Source: _compute_mde_q5q1 (r1_analysis) applied to Q5 vs Q1 arrays.
        # Units: percentage points (pp).  This is the charter-required design
        # MDE.  power_audit's mde_80pct is intentionally absent — it uses a
        # generic schedule population, not PEAD Q5-Q1 structure (F381-1).
        "design_mde_pp": _jsonify(_design_mde_pp),
        "doses": _jsonify(dose_results),
        "gap_lens": _jsonify(gap_lens_summary),
        "elapsed_sec": round(time.monotonic() - t0, 1),
    }

    summary_path = output_dir / "f370_explore_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    log.info("Summary written: %s", summary_path)

    # Log headline results for easy reading in run.log
    log.info("=" * 60)
    log.info("F370 explore-0 HEADLINE RESULTS")
    log.info("Design MDE (RM-03): design_mde_pp in summary = per-dose empirical mde_pp @ 63td (power_audit MDE omitted — generic population, not PEAD)")
    for dose_name, result in dose_results.items():
        h = result["by_horizon"].get(_PRIMARY_HORIZON, {})
        log.info(
            "  Dose %-20s @ 63td: Q5-Q1=%+.3f%% p_boot=%s rho=%s MDE=%.3f%%"
            " n_events_h=%d n_global=%d coverage=%.1f%%",
            dose_name,
            h.get("q5q1_gap_pct") or 0.0,
            h.get("p_boot"),
            h.get("rho_s"),
            h.get("mde_pp") or float("nan"),
            h.get("n_events", 0),           # K4: per-horizon count
            result.get("n_with_dose", 0),    # global (63td-filtered) coverage count
            result.get("coverage_pct", 0.0),
        )
    log.info("Total elapsed: %.1fs", time.monotonic() - t0)
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="F370 PEAD explore-0 driver (NON-LEDGERED feasibility screen)."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers for run_event_study (default: 1 = serial).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(workers=args.workers)
