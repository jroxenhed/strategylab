"""R-1b Insider Cluster Explore Driver — run_r1b_explore.py.

Runs the full R-1b explore study (2015-01-01..2020-12-31) or a fast
--calibrate mini-run (first ~25 events by event_ts) and calls
r1_analysis.run_r1_analysis on the result.

Charter:  docs/plans/2026-06-07-R1b-insider-cluster-charter-DRAFT.md
  SHA256: ff1d329cdfa68a31d23357b6ab9883b41f519862fc7c5d4c01699bede2b2220e

Pins (binding, do NOT change without minting a new charter):
  - SEED = 20260606  (§1 / bootstrap seed — carried verbatim from R-1)
  - dedup_window_days = 30  (§6 — 30 calendar days, same as R-1 final value)
  - matrix pin phrase = "universe medians from matrix build 2026-06-07"
  - matrix_strict = True  (charter §2c: silent fallback violates the pin)
  - Event source = form4_ingest.build_form4_dataset_events (charter §2 delta)
  - dedup_amendments = True  (pinned per charter §2 binding property (b))

Usage
-----
    # Full explore study (orchestrator decision; runtime ~1.5-3h on 14900k):
    backend/venv/bin/python backend/research/run_r1b_explore.py

    # Fast calibration (~25 events × full universe, expected <20min):
    backend/venv/bin/python backend/research/run_r1b_explore.py --calibrate

DO NOT touch the real fdr_ledger.json with --calibrate; it redirects to
backend/data/turnaround/fdr_ledger_smoke.json automatically.

DO NOT run this script without the orchestrator's explicit decision — the
matrix strict mode will fail fast on a missing/incomplete artifact.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _charter_cost_fn(ev, entry_price) -> float:
    """§7 frozen cost model: 2bps/leg × 2 legs = 4bps = 0.04 pct.

    Module-level (not a lambda) so it pickles into ProcessPool workers —
    a run.<locals>.<lambda> here crashed the first parallel calibrate
    (the F365 probe called run_event_study directly with its own picklable
    cost fn, so this driver path was never exercised under workers>1).
    """
    return 0.04


def _attach_run_log(output_dir: Path) -> None:
    """Tee all driver/harness logging to <study_dir>/run.log.

    Long-running drivers must be followable live regardless of how they were
    launched (John, 2026-06-06: the first explore run was piped through `tail`,
    which buffers until exit — 76 minutes with zero progress visibility).
    `tail -f <study_dir>/run.log` is the supported progress channel.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(fh)


# ---------------------------------------------------------------------------
# Frozen paths (EDGAR cache layout)
# ---------------------------------------------------------------------------
_EDGAR_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache"
_SUBS_DIR = _EDGAR_CACHE_DIR / "submissions"
_PRICE_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"
# Frozen returns matrix path (charter §2c pin)
_MATRIX_PATH = _BACKEND_DIR / "data" / "universe_matrix.parquet"
# Real FDR ledger — R-1b takes its own fresh draw (clean re-charter, §5).
_REAL_FDR_LEDGER = _BACKEND_DIR / "data" / "turnaround" / "fdr_ledger.json"
# Calibration/smoke FDR ledger — never touches the real ledger.
_SMOKE_FDR_LEDGER = _BACKEND_DIR / "data" / "turnaround" / "fdr_ledger_smoke.json"

# ---------------------------------------------------------------------------
# Frozen study constants (§1 / §3 / §6 / §8 — do NOT change post-outcome)
# ---------------------------------------------------------------------------
STUDY_NAME = "r1b_insider_clusters_explore_2015_2020"
CALIBRATE_STUDY_NAME = "r1b_calibration_DELETEME"

# Charter SHA256 — pinned here; verified at startup
_CHARTER_PATH = _REPO_ROOT / "docs" / "plans" / "2026-06-07-R1b-insider-cluster-charter-DRAFT.md"
_CHARTER_SHA256 = "ff1d329cdfa68a31d23357b6ab9883b41f519862fc7c5d4c01699bede2b2220e"

# Matrix pin phrase (§2c)
_MATRIX_PIN_PHRASE = "universe medians from matrix build 2026-06-07"
# ADV-01: pinned build date for vintage check — must match sidecar _meta.json "build_date" field
# exactly.  If the sidecar ever reports a different date the explore refuses to run
# (a wrong-vintage matrix produces silently wrong medians).
_MATRIX_EXPECTED_BUILD_DATE = "2026-06-07"

# §8: explore window (hard ceiling, never 2021+)
_EXPLORE_START = date(2015, 1, 1)
_EXPLORE_END = date(2020, 12, 31)

# §3a: primary seed (frozen, §1 — carried verbatim from R-1)
_SEED = 20260606

# §3b: loader parameters — span needed: 2012-01-01 (pre-event floor checks)
# to 2021-12-31 (126td forward from 2020-12-31 events)
_START_YEAR = 2015
_END_YEAR = 2020
_LOW_LOOKBACK_YEARS = 2     # gives fetch_start = 2012-01-01
_HORIZON_MONTHS = 6         # covers 126 td (~6 calendar months past 2020-12-31)
_DATA_SOURCE = "yahoo"

# §3b: price cache span gate (same as R-1: 4,666+ qualifying tickers)
_PRICE_SPAN_START = "20120101"
_PRICE_SPAN_END = "20211231"

# §3: universe-median phase progress log interval
_PROGRESS_LOG_INTERVAL = 10

# --calibrate: event truncation
_CALIBRATE_N_EVENTS = 25


# ---------------------------------------------------------------------------
# F365 Part 2: module-level loader factory (must be at module level for pickle)
# ---------------------------------------------------------------------------

def make_disk_only_loader(
    start_year: int,
    end_year: int,
    low_lookback_years: int,
    horizon_months: int,
    data_source: str,
    price_cache_dir: Optional[str] = None,
) -> Callable[[str], Optional[pd.DataFrame]]:
    """Build a disk-only price loader for F365 parallel workers.

    Workers use this instead of _make_memoized_loader because:
    1. Closures from _make_memoized_loader are not picklable across fork.
    2. Workers must NOT re-fetch from the network — the parent's prefetch
       already wrote all required frames to the on-disk PriceFrameCache.
       A disk miss returns None (treated as "no price data" — survivorship
       consistent with a fresh cache miss).

    The factory signature matches the loader_factory protocol:
    ("research.run_r1b_explore:make_disk_only_loader", {kwargs}).
    Must be module-level for macOS spawn safety.
    """
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
# Charter sha256 verification
# ---------------------------------------------------------------------------

def _verify_charter_sha256() -> None:
    """Verify the charter file matches the pinned SHA256.

    Raises RuntimeError if mismatched or file absent.
    This is a structural guard: if the charter file changed, the driver must
    be re-pinned by a human decision (it could mean the charter was amended
    post-outcome, which would violate the blindness contract).
    """
    import hashlib
    if not _CHARTER_PATH.exists():
        raise RuntimeError(
            f"Charter file not found: {_CHARTER_PATH}\n"
            f"Expected SHA256: {_CHARTER_SHA256}"
        )
    digest = hashlib.sha256(_CHARTER_PATH.read_bytes()).hexdigest()
    if digest != _CHARTER_SHA256:
        raise RuntimeError(
            f"Charter SHA256 mismatch!\n"
            f"  Expected: {_CHARTER_SHA256}\n"
            f"  Got:      {digest}\n"
            f"  File:     {_CHARTER_PATH}\n"
            f"The charter may have been modified post-pin; re-pin explicitly if intentional."
        )
    log.info("Charter SHA256 verified: %s", digest)


# ---------------------------------------------------------------------------
# Matrix coverage-sidecar check (charter §2c)
# ---------------------------------------------------------------------------

def _check_matrix_sidecar() -> dict:
    """Read the matrix _meta.json sidecar and enforce coverage requirements.

    Charter §2c binding rule: if no_frame_count != 0 OR status != 'complete',
    the matrix is not a clean full snapshot — FAIL FAST rather than proceeding
    with a partial benchmark snapshot.

    Returns the parsed _meta dict on success.
    Raises RuntimeError on missing/incomplete/corrupt sidecar.
    """
    meta_path = _MATRIX_PATH / "_meta.json"
    if not meta_path.exists():
        raise RuntimeError(
            f"Matrix coverage sidecar not found: {meta_path}\n"
            f"Matrix path: {_MATRIX_PATH}\n"
            f"Run the matrix builder before launching the explore."
        )
    try:
        matrix_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to parse matrix sidecar {meta_path}: {exc}"
        ) from exc

    status = matrix_meta.get("status", "")
    no_frame_count = matrix_meta.get("ticker_coverage", {}).get("no_frame_count", None)
    build_date = matrix_meta.get("build_date", "")

    if status != "complete":
        raise RuntimeError(
            f"Matrix sidecar status != 'complete': {status!r}\n"
            f"Charter §2c requires a complete snapshot before using any median.\n"
            f"Matrix path: {_MATRIX_PATH}"
        )
    if no_frame_count is None:
        raise RuntimeError(
            f"Matrix sidecar missing 'ticker_coverage.no_frame_count' field.\n"
            f"Cannot verify coverage completeness. Matrix path: {_MATRIX_PATH}"
        )
    if no_frame_count != 0:
        raise RuntimeError(
            f"Matrix sidecar no_frame_count = {no_frame_count} (nonzero).\n"
            f"Charter §2c: a nonzero no_frame_count signals the matrix is not a clean "
            f"full snapshot — rebuild-and-compare before use.\n"
            f"Matrix path: {_MATRIX_PATH}"
        )

    # ADV-01 vintage check: charter pins "universe medians from matrix build 2026-06-07".
    # A wrong-vintage matrix would silently produce wrong medians — fail closed.
    if build_date != _MATRIX_EXPECTED_BUILD_DATE:
        raise RuntimeError(
            f"Matrix sidecar build_date mismatch!\n"
            f"  Expected (charter pin): '{_MATRIX_EXPECTED_BUILD_DATE}'\n"
            f"  Got from sidecar:       '{build_date}'\n"
            f"  The charter pins: \"{_MATRIX_PIN_PHRASE}\".\n"
            f"  A wrong-vintage matrix produces wrong medians — refusing to proceed.\n"
            f"  Matrix path: {_MATRIX_PATH}"
        )

    log.info(
        "Matrix sidecar check PASSED: status=%s, no_frame_count=%d, build_date=%s (vintage verified)",
        status, no_frame_count, build_date,
    )
    log.info("Matrix pin phrase: %s", _MATRIX_PIN_PHRASE)
    return matrix_meta


# ---------------------------------------------------------------------------
# Universe construction (same logic as run_r1_explore.py)
# ---------------------------------------------------------------------------

def _build_universe_tickers(event_tickers: list[str] | None = None) -> list[str]:
    """Return ALL tickers with price-cache span 2012-2021 + non-zero SIC.

    Identical logic to run_r1_explore.py._build_universe_tickers.
    event_tickers must be included (F349 CRITICAL: absent event tickers force
    100% peer fallback because _load_ticker_to_sic only processes universe_tickers).
    """
    price_cache_dir = _PRICE_CACHE_DIR / "v1"
    if not price_cache_dir.exists():
        raise FileNotFoundError(f"Price cache not found: {price_cache_dir}")
    if not _SUBS_DIR.exists():
        raise FileNotFoundError(f"Submissions dir not found: {_SUBS_DIR}")

    # Step 1: tickers with full price coverage 2012-2021
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

    # Step 2: keep those with non-zero SIC in submissions
    universe: list[str] = []
    for subs_file in sorted(_SUBS_DIR.iterdir()):
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

    # F349 CRITICAL: event tickers MUST be in universe_tickers
    universe_set = set(universe)
    n_added = 0
    if event_tickers:
        for et in event_tickers:
            if et in covering and et not in universe_set:
                universe.append(et)
                universe_set.add(et)
                n_added += 1

    if n_added > 0:
        log.info(
            "Event tickers added to universe (not in SIC scan but price-covered): %d",
            n_added,
        )

    log.info("Universe tickers (SIC-bearing, 2012-2021 cache): %d", len(universe))
    return universe


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run(calibrate: bool = False, workers: int = 1) -> None:
    """Run the R-1b explore study (or calibration mini-run if calibrate=True).

    workers=1 forces the legacy serial path; workers>1 uses ProcessPoolExecutor
    over event chunks.  Bit-identical output to serial is the binding contract.
    """
    from research.event_study import EventStudyConfig, run_event_study
    from research.r1_dose import build_r1b_events
    from research.r1_analysis import run_r1_analysis
    from turnaround_validation import _make_memoized_loader

    t0 = time.monotonic()

    # ------------------------------------------------------------------
    # 0. Pre-flight: verify charter SHA256 + matrix sidecar
    # ------------------------------------------------------------------
    log.info("R-1b pre-flight checks ...")
    _verify_charter_sha256()
    matrix_meta = _check_matrix_sidecar()

    # ------------------------------------------------------------------
    # 1. Build events via R-1b dose builder (form4_ingest path)
    # ------------------------------------------------------------------
    log.info(
        "Building R-1b events via r1_dose.build_r1b_events "
        "(form4_ingest source, dedup_amendments=True) ..."
    )
    loader = _make_memoized_loader(
        start_year=_START_YEAR,
        end_year=_END_YEAR,
        low_lookback_years=_LOW_LOOKBACK_YEARS,
        horizon_months=_HORIZON_MONTHS,
        data_source=_DATA_SOURCE,
    )
    log.info("Memoized loader built (2015-2020 span, yahoo, warm cache).")

    # Driver perf fix: derive quarter list from explore start/end dates.
    # The trailing dose window only looks BACK (no later quarter needed), so
    # we include exactly the quarters covering [_EXPLORE_START, _EXPLORE_END].
    # Ingesting all 45 quarters (through 2026q1) for a 2015-2020 explore is
    # wasteful; this cuts ingest scope to the 24 quarters in the window.
    def _quarters_for_range(start_date: date, end_date: date) -> list[str]:
        """Return sorted list of 'YYYYqN' strings covering [start_date, end_date]."""
        qs: list[str] = []
        y, q = start_date.year, (start_date.month - 1) // 3 + 1
        end_y, end_q = end_date.year, (end_date.month - 1) // 3 + 1
        while (y, q) <= (end_y, end_q):
            qs.append(f"{y}q{q}")
            q += 1
            if q > 4:
                q = 1
                y += 1
        return qs

    _explore_quarters = _quarters_for_range(_EXPLORE_START, _EXPLORE_END)
    log.info(
        "Derived quarter list from explore dates %s..%s: %d quarters (%s .. %s)",
        _EXPLORE_START, _EXPLORE_END,
        len(_explore_quarters),
        _explore_quarters[0],
        _explore_quarters[-1],
    )

    events_raw, dose_meta = build_r1b_events(
        start=_EXPLORE_START,
        end=_EXPLORE_END,
        loader_fn=loader,
        quarters=_explore_quarters,
        # shares_fn=None uses the default _shares_outstanding_disk_only (offline-safe)
    )

    ingest_meta = dose_meta.get("_ingest_meta", {})
    log.info(
        "R-1b dose builder: events=%d "
        "(ingest: superseded_dropped=%d, dup4_collisions=%d, midnight_utc=%d, ticker_fallback=%d)",
        len(events_raw),
        dose_meta.get("n_superseded_dropped", 0),
        dose_meta.get("n_dup4_collisions", 0),
        dose_meta.get("n_midnight_utc_adt", 0),
        dose_meta.get("n_ticker_fallback", 0),
    )
    log.info(
        "  fallbacks=%d 10b51_excl=%d missing_price=%d score_undefined=%d",
        dose_meta.get("acceptance_fallbacks", 0),
        dose_meta.get("n_10b51_excluded_total", 0),
        dose_meta.get("missing_price_txns_total", 0),
        dose_meta.get("score_undefined_total", 0),
    )
    log.info(
        "  filings_scanned=%d filings_qualifying=%d events_pre_date_filter=%d "
        "n_multi_owner_forms=%d "
        "(multi-owner forms: population where R-1b union-k differs from R-1 first-owner-k)",
        dose_meta.get("filings_scanned", 0),
        dose_meta.get("filings_qualifying", 0),
        dose_meta.get("events_pre_date_filter", 0),
        dose_meta.get("n_multi_owner_forms", 0),
    )

    # ------------------------------------------------------------------
    # 2. --calibrate: truncate to first ~25 events by event_ts
    # ------------------------------------------------------------------
    if calibrate:
        log.info(
            "CALIBRATE MODE: truncating deduped event list to first %d events "
            "(sorted by event_ts) before harness.",
            _CALIBRATE_N_EVENTS,
        )
        events_sorted = sorted(events_raw, key=lambda e: e.event_ts)
        events_to_run = events_sorted[:_CALIBRATE_N_EVENTS]
        study_name = CALIBRATE_STUDY_NAME
        output_dir = _STUDIES_DIR / CALIBRATE_STUDY_NAME
        fdr_ledger_path = _SMOKE_FDR_LEDGER  # NEVER touches real ledger
        _attach_run_log(output_dir)
        log.info(
            "Calibrate: running %d events (out of %d total). "
            "Output → %s, ledger → %s",
            len(events_to_run),
            len(events_raw),
            output_dir,
            fdr_ledger_path,
        )
    else:
        events_to_run = events_raw
        study_name = STUDY_NAME
        output_dir = _STUDIES_DIR / STUDY_NAME
        fdr_ledger_path = _REAL_FDR_LEDGER
        _attach_run_log(output_dir)
        log.info(
            "FULL EXPLORE: running %d raw events. Output → %s, ledger → %s",
            len(events_to_run),
            output_dir,
            fdr_ledger_path,
        )

    # ------------------------------------------------------------------
    # 3. Build universe_tickers (required for peer median + universe median)
    # ------------------------------------------------------------------
    event_tickers = list({e.ticker for e in events_to_run})
    log.info(
        "Building universe tickers (4,666+ expected; event tickers guaranteed included)..."
    )
    universe_tickers = _build_universe_tickers(event_tickers=event_tickers)

    # ------------------------------------------------------------------
    # 4. Configure EventStudy
    # ------------------------------------------------------------------
    # §3b / §6: frozen config — identical to R-1 except study_name
    cfg = EventStudyConfig(
        study_name=study_name,
        horizons=(21, 63, 126),           # §4 / brief
        explore_cutoff=_EXPLORE_END,      # §8: 2020-12-31 hard ceiling
        entry_lag_days=1,                 # §2b / brief
        dedup_same_ticker=True,           # §6
        dedup_window_days=30,             # §6: 30 calendar days (stated directly in R-1b)
        n_boot=999,                       # §5 / brief
        fdr_q=0.10,                       # §5
        min_peer_count=8,                 # §2c: fallback cascade minimum
        cost_fn=_charter_cost_fn,  # §7: 2bps/leg × 2 legs = 4bps = 0.04 pct (module-level: pickles to workers)
        output_dir=output_dir,
        fdr_ledger_path=fdr_ledger_path,
        allow_post_2020_explore=False,    # §8: hard guard
    )

    log.info(
        "EventStudyConfig: study=%s, horizons=%s, dedup_window=%d, "
        "min_peer_count=%d, cost=0.04, fdr_q=%.2f",
        cfg.study_name,
        cfg.horizons,
        cfg.dedup_window_days,
        cfg.min_peer_count,
        cfg.fdr_q,
    )

    # ------------------------------------------------------------------
    # 5. Run event study with frozen matrix (strict mode)
    # ------------------------------------------------------------------
    # §1: SEED = 20260606, passed as rng
    rng = np.random.default_rng(_SEED)
    log.info(
        "Running run_event_study (rng seed=%d, use_matrix=True, matrix_strict=True, "
        "matrix_path=%s, workers=%d) ...",
        _SEED, _MATRIX_PATH, workers,
    )
    log.info("Matrix pin: %s", _MATRIX_PIN_PHRASE)
    if workers > 1:
        log.info("Parallel path active (workers=%d); serial path is workers=1.", workers)
    else:
        log.info("Serial path active (workers=1).")

    events_with_progress = _progress_wrap(events_to_run, interval=_PROGRESS_LOG_INTERVAL)

    # F365 Part 2: build loader_factory for parallel workers.
    # Workers cannot use the parent's memoized loader (closures not picklable).
    # make_disk_only_loader reads from the on-disk PriceFrameCache only — the
    # parent's prefetch already wrote all required frames; a disk miss returns
    # None (no network fetch, no block on 429 budget).
    _loader_factory = (
        "research.run_r1b_explore:make_disk_only_loader",
        {
            "start_year": _START_YEAR,
            "end_year": _END_YEAR,
            "low_lookback_years": _LOW_LOOKBACK_YEARS,
            "horizon_months": _HORIZON_MONTHS,
            "data_source": _DATA_SOURCE,
            # price_cache_dir=None uses the PriceFrameCache default path.
        },
    )

    t_harness_start = time.monotonic()
    outcomes, harness_meta = run_event_study(
        events=events_with_progress,
        config=cfg,
        loader_fn=loader,
        universe_tickers=universe_tickers,
        rng=rng,
        use_matrix=True,
        matrix_path=_MATRIX_PATH,
        matrix_strict=True,  # charter §2c: silent fallback violates the benchmark pin
        workers=workers,
        loader_factory=_loader_factory if workers > 1 else None,
    )
    t_harness_elapsed = time.monotonic() - t_harness_start

    log.info(
        "Harness done in %.1fs. n_events=%d, n_explore=%d, n_confirm=%d",
        t_harness_elapsed,
        harness_meta.get("n_events"),
        harness_meta.get("n_explore"),
        harness_meta.get("n_confirm"),
    )

    # ------------------------------------------------------------------
    # 6. Log dose-builder + ingest + matrix meta
    # ------------------------------------------------------------------
    log.info(
        "Dose builder meta — "
        "acceptance_fallbacks: %d; "
        "10b5-1 excluded txns: %d; "
        "score_undefined: %d events (missing MC or price); "
        "midnight_utc_adt: %d; "
        "superseded_dropped: %d; "
        "dup4_collisions_kept: %d",
        dose_meta.get("acceptance_fallbacks", 0),
        dose_meta.get("n_10b51_excluded_total", 0),
        dose_meta.get("score_undefined_total", 0),
        dose_meta.get("n_midnight_utc_adt", 0),
        dose_meta.get("n_superseded_dropped", 0),
        dose_meta.get("n_dup4_collisions", 0),
    )
    log.info(
        "Matrix meta: build_date=%s, status=%s, no_frame_count=%d, "
        "n_universe_tickers=%d, row_count=%s",
        matrix_meta.get("build_date", ""),
        matrix_meta.get("status", ""),
        matrix_meta.get("ticker_coverage", {}).get("no_frame_count", -1),
        matrix_meta.get("universe", {}).get("ticker_count", -1),
        matrix_meta.get("row_count", "?"),
    )
    sic_cov = harness_meta.get("sic_coverage") or {}
    log.info(
        "SIC coverage: %.1f%% (%d with SIC / %d without)",
        sic_cov.get("coverage_pct", 0.0),
        sic_cov.get("tickers_with_sic", 0),
        sic_cov.get("tickers_without_sic", 0),
    )
    fb = harness_meta.get("sic_fallback_stats") or {}
    log.info("SIC fallback stats: %s", fb)
    rb = harness_meta.get("regime_breakdown") or {}
    regime_counts = {
        s: rb.get(s, {}).get("n_events", 0)
        for s in ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS")
    }
    log.info("Regime distribution (all harness events): %s", regime_counts)

    # ------------------------------------------------------------------
    # 7. Run r1_analysis.run_r1_analysis on the study artifact dir
    # ------------------------------------------------------------------
    log.info("Running r1_analysis.run_r1_analysis on %s ...", output_dir)
    t_analysis_start = time.monotonic()
    analysis_result = run_r1_analysis(
        study_dir=output_dir,
        seed=_SEED,
        ledger_path=fdr_ledger_path,
    )
    t_analysis_elapsed = time.monotonic() - t_analysis_start
    log.info("Analysis done in %.1fs.", t_analysis_elapsed)

    # ------------------------------------------------------------------
    # 8. Write R-1b meta fields to study_dir/r1b_meta.json
    # ------------------------------------------------------------------
    r1b_meta = {
        "charter_path": str(_CHARTER_PATH),
        "charter_sha256": _CHARTER_SHA256,
        "matrix_pin_phrase": _MATRIX_PIN_PHRASE,
        "matrix_path": str(_MATRIX_PATH),
        "matrix_build_date": matrix_meta.get("build_date", ""),
        "matrix_status": matrix_meta.get("status", ""),
        "matrix_no_frame_count": matrix_meta.get("ticker_coverage", {}).get("no_frame_count", -1),
        "matrix_row_count": matrix_meta.get("row_count"),
        "ingest_meta": {
            "n_midnight_utc_adt": dose_meta.get("n_midnight_utc_adt", 0),
            "n_superseded_dropped": dose_meta.get("n_superseded_dropped", 0),
            "n_dup4_collisions": dose_meta.get("n_dup4_collisions", 0),
            "n_ticker_fallback": dose_meta.get("n_ticker_fallback", 0),
            "quarters_processed": ingest_meta.get("quarters_processed", 0),
            "submissions_scanned": ingest_meta.get("submissions_scanned", 0),
            "form4_qualified_txns": ingest_meta.get("form4_qualified_txns", 0),
            "form4_10b51_excluded_txns": ingest_meta.get("form4_10b51_excluded_txns", 0),
        },
        "seed": _SEED,
        "dedup_window_days": 30,
        "dedup_amendments": True,
        "study_name": study_name,
        "explore_start": str(_EXPLORE_START),
        "explore_end": str(_EXPLORE_END),
    }
    r1b_meta_path = output_dir / "r1b_meta.json"
    r1b_meta_path.write_text(json.dumps(r1b_meta, indent=2), encoding="utf-8")
    log.info("R-1b meta written to: %s", r1b_meta_path)

    # ------------------------------------------------------------------
    # 9. Calibration timing report + extrapolation
    # ------------------------------------------------------------------
    t_total = time.monotonic() - t0
    if calibrate:
        n_events_calibrate = len(events_to_run)
        n_events_full = len(events_raw)
        elapsed_s = t_harness_elapsed
        n_explore_calibrate = harness_meta.get("n_explore", n_events_calibrate)
        unique_dates_calibrate = max(1, harness_meta.get("n_explore", n_events_calibrate))
        unique_dates_full_estimate = 150  # scout Q8 estimate (same as R-1)
        scale = unique_dates_full_estimate / max(1, unique_dates_calibrate)
        extrapolated_s = elapsed_s * scale
        extrapolated_min = extrapolated_s / 60.0
        log.info(
            "CALIBRATION TIMING REPORT:\n"
            "  Calibrate wall-clock (harness only): %.1fs (%.1f min)\n"
            "  Total calibrate wall-clock (incl. dose-build + analysis): %.1fs\n"
            "  Unique explore events in calibrate: %d\n"
            "  Full unique entry dates estimate (scout): %d\n"
            "  Scale factor: %.1fx\n"
            "  Extrapolated full-run harness time: %.0fs (~%.1f min)\n"
            "  Note: full universe = %d tickers vs calibrate = %d",
            elapsed_s,
            elapsed_s / 60.0,
            t_total,
            unique_dates_calibrate,
            unique_dates_full_estimate,
            scale,
            extrapolated_s,
            extrapolated_min,
            len(universe_tickers),
            len(universe_tickers),
        )
        print(
            f"\n{'=' * 70}\n"
            f"R-1b CALIBRATE TIMING SUMMARY\n"
            f"  Harness elapsed: {elapsed_s:.1f}s ({elapsed_s/60:.1f} min)\n"
            f"  Total elapsed (dose+harness+analysis): {t_total:.1f}s\n"
            f"  Calibrate unique explore events: {unique_dates_calibrate}\n"
            f"  Extrapolated full-run harness time:\n"
            f"    Linear (unique_dates scale={scale:.1f}x): {extrapolated_s:.0f}s "
            f"(~{extrapolated_min:.1f} min)\n"
            f"    Full universe = {len(universe_tickers)} tickers\n"
            f"  Study written to: {output_dir}\n"
            f"  FDR ledger: {fdr_ledger_path} (SMOKE — real ledger not touched)\n"
            f"{'=' * 70}\n"
        )
    else:
        log.info(
            "FULL EXPLORE done. Total wall-clock: %.1fs (%.1f min). "
            "Study written to: %s",
            t_total,
            t_total / 60.0,
            output_dir,
        )

    log.info(
        "R-1b explore complete. verdict: %s | n_valid=%d | MDE=%.4fpp | gap=%s",
        analysis_result.get("explore_decision"),
        analysis_result.get("n_valid_events"),
        analysis_result.get("mde_q5q1_pp") or float("nan"),
        analysis_result.get("H1", {}).get("obs_gap_q5q1_pp"),
    )


# ---------------------------------------------------------------------------
# Progress wrap helper (mirrors run_r1_explore.py)
# ---------------------------------------------------------------------------

class _progress_wrap:
    """Iterable wrapper that logs progress every N events."""

    def __init__(self, events: list, interval: int = 10) -> None:
        self._events = events
        self._interval = interval

    def __iter__(self):
        n = len(self._events)
        for i, ev in enumerate(self._events):
            if i % self._interval == 0:
                log.info(
                    "Processing event %d / %d (ticker=%s, date=%s) ...",
                    i + 1,
                    n,
                    ev.ticker,
                    ev.event_ts.date() if ev.event_ts else "?",
                )
            yield ev


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R-1b Insider Cluster Explore Driver.",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        default=False,
        help=(
            "Run a mini-calibration with the first ~25 events (sorted by event_ts) "
            "on the FULL universe.  Writes to a THROWAWAY output dir "
            f"(event_studies/{CALIBRATE_STUDY_NAME}) and redirects FDR ledger to "
            "fdr_ledger_smoke.json.  NEVER touches the real fdr_ledger.json."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help=(
            "Number of worker processes for the per-event outcome loop.  "
            "1 forces the legacy serial path (exact byte-identical output).  "
            f"Default: min(8, cpu_count) = {min(8, os.cpu_count() or 1)}."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(calibrate=args.calibrate, workers=args.workers)
