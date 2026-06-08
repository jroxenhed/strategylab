"""R-1 Insider Cluster Explore Driver — run_r1_explore.py.

Runs the full R-1 explore study (2015-01-01..2020-12-31) or a fast
--calibrate mini-run (first ~25 events by event_ts) and calls
r1_analysis.run_r1_analysis on the result.

Charter: docs/plans/2026-06-06-R1-insider-cluster-charter-DRAFT.md
All frozen constants are documented with their charter section.

SEED = 20260606 (§1 / SEED frozen).
Harness: backend/research/event_study.py (F342).
Dose: backend/research/r1_dose.py (Agent A).
Analysis: backend/research/r1_analysis.py (Agent B).

Usage
-----
    # Full explore study (orchestrator decision; runtime ~1.5-3h on 14900k):
    backend/venv/bin/python backend/research/run_r1_explore.py

    # Fast calibration (~25 events × full 4666-universe, expected <20min):
    backend/venv/bin/python backend/research/run_r1_explore.py --calibrate

DO NOT touch the real fdr_ledger.json with --calibrate; it redirects to
backend/data/turnaround/fdr_ledger_smoke.json automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

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
_STRATIFIED_DIR = _EDGAR_CACHE_DIR / "form4_stratified"
_INDEX_PATH = _STRATIFIED_DIR / "index.json"
_XML_DIR = _STRATIFIED_DIR
_SUBS_DIR = _EDGAR_CACHE_DIR / "submissions"
_PRICE_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"
# Real FDR ledger — first real entry (F352 open: single-writer discipline).
_REAL_FDR_LEDGER = _BACKEND_DIR / "data" / "turnaround" / "fdr_ledger.json"
# Calibration/smoke FDR ledger — never touches the real ledger.
_SMOKE_FDR_LEDGER = _BACKEND_DIR / "data" / "turnaround" / "fdr_ledger_smoke.json"

# ---------------------------------------------------------------------------
# Frozen study constants (§1 / §3 / §6 / §8 — do NOT change post-outcome)
# ---------------------------------------------------------------------------
STUDY_NAME = "r1_insider_clusters_explore_2015_2020"
CALIBRATE_STUDY_NAME = "r1_calibration_DELETEME"

# §8: explore window (hard ceiling, never 2021+)
_EXPLORE_START = date(2015, 1, 1)
_EXPLORE_END = date(2020, 12, 31)

# §3a: primary seed (frozen, §1)
_SEED = 20260606

# §3b: loader parameters — span needed: 2012-01-01 (pre-event floor checks)
# to 2021-12-31 (126td forward from 2020-12-31 events)
_START_YEAR = 2015
_END_YEAR = 2020
_LOW_LOOKBACK_YEARS = 2     # gives fetch_start = 2012-01-01
_HORIZON_MONTHS = 6         # covers 126 td (~6 calendar months past 2020-12-31)
_DATA_SOURCE = "yahoo"

# §3b: price cache span gate (scouts measured 4,666 qualifying tickers at 2021 end)
_PRICE_SPAN_START = "20120101"
_PRICE_SPAN_END = "20211231"

# §3: universe-median phase progress log interval
_PROGRESS_LOG_INTERVAL = 10

# --calibrate: event truncation
_CALIBRATE_N_EVENTS = 25


# ---------------------------------------------------------------------------
# Universe construction
# ---------------------------------------------------------------------------

def _build_universe_tickers(event_tickers: list[str] | None = None) -> list[str]:
    """Return ALL tickers with price-cache span 2012-2021 + non-zero SIC.

    Delegates to universe_loader.build_liquid_universe (F358 consolidation).
    See that module for the full definition and F349 CRITICAL guard.

    Price-cache span gate: START <= 20120101 AND END >= 20211231.
    (Smoke driver uses 20221231; R-1 uses 20211231 — 126td past 2020-12-31 explores.)
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
# Driver
# ---------------------------------------------------------------------------

def run(calibrate: bool = False) -> None:
    """Run the R-1 explore study (or calibration mini-run if calibrate=True)."""
    from research.event_study import EventStudyConfig, run_event_study
    from research.r1_dose import build_r1_events
    from research.r1_analysis import run_r1_analysis
    from turnaround_validation import _make_memoized_loader

    # ------------------------------------------------------------------
    # 1. Build events via dose builder
    # ------------------------------------------------------------------
    log.info(
        "Building R-1 events via r1_dose.build_r1_events "
        "(index: %s, XMLs: %s, subs: %s)...",
        _INDEX_PATH,
        _XML_DIR,
        _SUBS_DIR,
    )
    t0 = time.monotonic()
    loader = _make_memoized_loader(
        start_year=_START_YEAR,
        end_year=_END_YEAR,
        low_lookback_years=_LOW_LOOKBACK_YEARS,
        horizon_months=_HORIZON_MONTHS,
        data_source=_DATA_SOURCE,
    )
    log.info("Memoized loader built (2015-2020 span, yahoo, warm cache).")

    events_raw, dose_meta = build_r1_events(
        start=_EXPLORE_START,
        end=_EXPLORE_END,
        index_path=_INDEX_PATH,
        xml_dir=_XML_DIR,
        subs_dir=_SUBS_DIR,
        loader_fn=loader,
        # shares_fn=None uses the default _shares_outstanding_disk_only (offline-safe)
    )

    log.info(
        "Dose builder: scanned=%d qualifying=%d fallbacks=%d "
        "10b51_excl=%d missing_price=%d score_undefined=%d events_raw=%d",
        dose_meta.get("filings_scanned", 0),
        dose_meta.get("filings_qualifying", 0),
        dose_meta.get("acceptance_fallbacks", 0),
        dose_meta.get("n_10b51_excluded_total", 0),
        dose_meta.get("missing_price_txns_total", 0),
        dose_meta.get("score_undefined_total", 0),
        len(events_raw),
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
        # Sort all raw events by event_ts ascending, take the first N
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
        "Building universe tickers (4,666 expected; event tickers guaranteed included)..."
    )
    universe_tickers = _build_universe_tickers(event_tickers=event_tickers)

    # ------------------------------------------------------------------
    # 4. Configure EventStudy
    # ------------------------------------------------------------------
    # §3b / §6: frozen config
    cfg = EventStudyConfig(
        study_name=study_name,
        horizons=(21, 63, 126),          # §4 / brief
        explore_cutoff=_EXPLORE_END,     # §8: 2020-12-31 hard ceiling
        entry_lag_days=1,                # §2b / brief
        dedup_same_ticker=True,          # §6
        # §6 AMENDED 2026-06-06 (pre-outcome, §10; John approved): charter froze
        # 21 *calendar* days claiming alignment with the 21-*business*-day dose
        # window, but 21 bdays ≈ 29 calendar days — events 22-28 days apart
        # double-counted filings (ADV-02, bias toward hypothesis). 30 calendar
        # days fulfills the charter's stated intent: one buying wave = one
        # degree of freedom. No R-1 outcome was computed before this change.
        dedup_window_days=30,
        n_boot=999,                      # §5 / brief
        fdr_q=0.10,                      # §5
        min_peer_count=8,                # §2c: fallback cascade minimum
        cost_fn=lambda ev, entry_price: 0.04,  # §7: 2bps/leg × 2 legs = 4bps = 0.04 pct
        output_dir=output_dir,
        fdr_ledger_path=fdr_ledger_path,
        allow_post_2020_explore=False,   # §8: hard guard
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
    # 5. Run event study
    # ------------------------------------------------------------------
    # §1: SEED = 20260606, passed as rng
    rng = np.random.default_rng(_SEED)
    log.info("Running run_event_study (rng seed=%d)...", _SEED)

    # Progress logging wired into the harness via a progress callback (if supported)
    # The long pole is the universe-median phase. We log every ~10 events inside
    # the harness via the fact that it processes events in sequence.
    # For external progress logging: the harness logs internally; additionally we
    # provide a context to log every ~10 events by wrapping events_to_run.
    events_with_progress = _progress_wrap(events_to_run, interval=_PROGRESS_LOG_INTERVAL)

    t_harness_start = time.monotonic()
    outcomes, harness_meta = run_event_study(
        events=events_with_progress,
        config=cfg,
        loader_fn=loader,
        universe_tickers=universe_tickers,
        rng=rng,
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
    # 6. Log dose-builder meta
    # ------------------------------------------------------------------
    log.info(
        "Dose builder meta — "
        "fallback acceptance_dt: %d / %d filings; "
        "10b5-1 excluded: %d txns; "
        "score_undefined: %d events (missing MC or price)",
        dose_meta.get("acceptance_fallbacks", 0),
        dose_meta.get("filings_qualifying", 0),
        dose_meta.get("n_10b51_excluded_total", 0),
        dose_meta.get("score_undefined_total", 0),
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
    log.info(
        "Running r1_analysis.run_r1_analysis on %s ...", output_dir
    )
    t_analysis_start = time.monotonic()
    analysis_result = run_r1_analysis(
        study_dir=output_dir,
        seed=_SEED,
        ledger_path=fdr_ledger_path,
    )
    t_analysis_elapsed = time.monotonic() - t_analysis_start
    log.info("Analysis done in %.1fs.", t_analysis_elapsed)

    # ------------------------------------------------------------------
    # 8. Calibration timing report + extrapolation
    # ------------------------------------------------------------------
    t_total = time.monotonic() - t0
    if calibrate:
        # Unique entry dates in calibration run
        n_events_calibrate = len(events_to_run)
        n_events_full = len(events_raw)
        elapsed_s = t_harness_elapsed
        # Count unique entry dates in calibration (deduped stream is in harness_meta)
        n_explore_calibrate = harness_meta.get("n_explore", n_events_calibrate)
        # For extrapolation: key scaling factor is unique_entry_dates × universe_size
        # Calibrate: ~25 events → ≤25 unique dates
        # Full: ~150 unique entry dates (scout estimate) × 4,666 universe
        unique_dates_calibrate = max(1, harness_meta.get("n_explore", n_events_calibrate))
        unique_dates_full_estimate = 150  # scout Q8 estimate
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
            f"CALIBRATE TIMING SUMMARY\n"
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
        "R-1 explore complete. verdict: %s | n_valid=%d | MDE=%.4fpp | gap=%s",
        analysis_result.get("explore_decision"),
        analysis_result.get("n_valid_events"),
        analysis_result.get("mde_q5q1_pp") or float("nan"),
        analysis_result.get("H1", {}).get("obs_gap_q5q1_pp"),
    )


# ---------------------------------------------------------------------------
# Progress wrap helper
# ---------------------------------------------------------------------------

class _progress_wrap:
    """Iterable wrapper that logs progress every N events.

    The universe-median phase is the long pole (~10ms/op × 4,666 tickers × horizons).
    Log every PROGRESS_LOG_INTERVAL events so the operator can track throughput.
    """
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
        description="R-1 Insider Cluster Explore Driver.",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        default=False,
        help=(
            "Run a mini-calibration with the first ~25 events (sorted by event_ts) "
            "on the FULL 4,666-ticker universe.  Writes to a THROWAWAY output dir "
            f"(event_studies/{CALIBRATE_STUDY_NAME}) and redirects FDR ledger to "
            "fdr_ledger_smoke.json.  NEVER touches the real fdr_ledger.json."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(calibrate=args.calibrate)
