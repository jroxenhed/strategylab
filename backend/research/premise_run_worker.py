#!/usr/bin/env python3
"""premise_run_worker.py — Subprocess entry for full-explore dispatch (F389).

Called by bin/worker-dispatch.sh. Runs the sync full-explore and writes results.

Usage:
    backend/venv/bin/python3 backend/research/premise_run_worker.py \\
        --premise-id <pid> \\
        --outdir <outdir_path> \\
        [--fdr-ledger-path <path>]  # F393 only: NEVER passed in v1

v1 contract:
  --fdr-ledger-path is accepted but IGNORED in v1.
  fdr_ledger_path=None is ALWAYS used in v1 runs.
  (Future F393 real OOS confirm will activate this flag.)

Output:
  <outdir>/r1_explore_verdict.json  — verdict dict from run_r1_analysis
  <outdir>/run.log                  — live-followable log (tail -f to watch)

Exit:
  0 on success, non-zero on failure.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

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


def _attach_run_log(output_dir: Path) -> None:
    """Tee all logging to <study_dir>/run.log — live-followable (John's rule)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(fh)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen paths (mirrors run_r1_explore.py)
# ---------------------------------------------------------------------------
_EDGAR_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache"
_STRATIFIED_DIR = _EDGAR_CACHE_DIR / "form4_stratified"
_INDEX_PATH = _STRATIFIED_DIR / "index.json"
_XML_DIR = _STRATIFIED_DIR
_SUBS_DIR = _EDGAR_CACHE_DIR / "submissions"
_PRICE_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"

_EXPLORE_START_YEAR = 2015
_EXPLORE_END_YEAR = 2020
_LOW_LOOKBACK_YEARS = 2
_HORIZON_MONTHS = 6
_DATA_SOURCE = "yahoo"
_PRICE_SPAN_START = "20120101"
_PRICE_SPAN_END = "20211231"
_SEED = 20260606


def _check_required_caches() -> None:
    checks = [
        (_INDEX_PATH, "form4_stratified/index.json"),
        (_XML_DIR, "form4_stratified/ (XMLs)"),
        (_SUBS_DIR, "submissions/"),
        (_PRICE_CACHE_DIR / "v1", "price_cache/v1/"),
    ]
    for path, label in checks:
        if not path.exists():
            raise RuntimeError(
                f"Required cache absent: {label} ({path}). "
                f"Run the EDGAR + price cache population scripts before dispatching "
                f"a premise run. This must be present on the target machine."
            )


def run_full_explore_sync(premise_id: str, outdir: Path) -> dict:
    """Synchronous full-explore run — core logic.

    fdr_ledger_path=None ALWAYS in v1 (see module docstring).
    Returns the verdict dict from run_r1_analysis.
    """
    from datetime import date
    import numpy as np
    from research.premise_compile import compile_spec
    from research.premise_spec import PremiseSpec
    from research.premise_store import PremiseStore
    from research.r1_dose import build_r1_events
    from research.r1_analysis import run_r1_analysis
    from research.universe_loader import build_liquid_universe
    from research.event_study import run_event_study
    from turnaround_validation import _make_memoized_loader

    _check_required_caches()

    store = PremiseStore()
    p = store.premises.get(premise_id)
    if p is None:
        raise KeyError(f"Premise not found: {premise_id!r}")

    spec_dict = p.get("spec")
    if spec_dict is None:
        raise ValueError(f"Premise {premise_id!r} has no spec.")

    spec = PremiseSpec(**spec_dict)

    study_name = outdir.name
    _attach_run_log(outdir)

    # Compile — fdr_ledger_path=None (NEVER touches real ledger in v1)
    cr = compile_spec(spec, study_name=study_name, output_dir=outdir, fdr_ledger_path=None)
    log.info("Compiled spec for premise %s (dose_builder=%s)", premise_id, cr.dose_builder)

    # Build loader
    t0 = time.monotonic()
    loader = _make_memoized_loader(
        start_year=_EXPLORE_START_YEAR,
        end_year=_EXPLORE_END_YEAR,
        low_lookback_years=_LOW_LOOKBACK_YEARS,
        horizon_months=_HORIZON_MONTHS,
        data_source=_DATA_SOURCE,
    )
    log.info("Memoized loader built (%.1fs)", time.monotonic() - t0)

    # Build events
    explore_start = date(2015, 1, 1)
    explore_end = date(2020, 12, 31)
    events_raw, dose_meta = build_r1_events(
        start=explore_start,
        end=explore_end,
        index_path=_INDEX_PATH,
        xml_dir=_XML_DIR,
        subs_dir=_SUBS_DIR,
        loader_fn=loader,
    )
    log.info(
        "Dose builder: events_raw=%d scanned=%d qualifying=%d",
        len(events_raw),
        dose_meta.get("filings_scanned", 0),
        dose_meta.get("filings_qualifying", 0),
    )

    # Build full universe
    event_tickers = list({e.ticker for e in events_raw})
    log.info("Building full liquid universe (expected ~4,666 for 2012-2021 span)...")
    t_univ = time.monotonic()
    universe_tickers = build_liquid_universe(
        price_cache_dir=_PRICE_CACHE_DIR / "v1",
        subs_dir=_SUBS_DIR,
        span_start=_PRICE_SPAN_START,
        span_end=_PRICE_SPAN_END,
        extra_tickers=event_tickers,
    )
    log.info("Universe: %d tickers (%.1fs)", len(universe_tickers), time.monotonic() - t_univ)

    # Run harness
    rng = np.random.default_rng(_SEED)
    log.info("Running event study harness (rng seed=%d)...", _SEED)
    t_harness = time.monotonic()
    outcomes, harness_meta = run_event_study(
        events=events_raw,
        config=cr.config,
        loader_fn=loader,
        universe_tickers=universe_tickers,
        rng=rng,
    )
    log.info(
        "Harness done in %.1fs: n_events=%d n_explore=%d n_confirm=%d",
        time.monotonic() - t_harness,
        harness_meta.get("n_events", 0),
        harness_meta.get("n_explore", 0),
        harness_meta.get("n_confirm", 0),
    )

    # Analysis (ledger_path=None → no FDR append in v1)
    log.info("Running r1_analysis on %s...", outdir)
    analysis_result = run_r1_analysis(
        study_dir=outdir,
        seed=_SEED,
        ledger_path=None,
    )
    log.info("Analysis done. explore_decision=%s", analysis_result.get("explore_decision"))

    # Write verdict JSON to outdir (polling route reads this)
    verdict_path = outdir / "r1_explore_verdict.json"
    with open(verdict_path, "w", encoding="utf-8") as f:
        json.dump(analysis_result, f, indent=2, default=str)
    log.info("Verdict written to %s", verdict_path)

    return analysis_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Premise full-explore worker (F389)")
    parser.add_argument("--premise-id", required=True, help="Premise ID to run")
    parser.add_argument("--outdir", required=True, help="Output directory path")
    parser.add_argument(
        "--fdr-ledger-path",
        default=None,
        help="F393 only: path to real FDR ledger. IGNORED in v1.",
    )
    args = parser.parse_args()

    if args.fdr_ledger_path is not None:
        log.warning(
            "--fdr-ledger-path %r was provided but is IGNORED in v1. "
            "Real OOS confirm (F393) is a separate gated step.",
            args.fdr_ledger_path,
        )

    outdir = Path(args.outdir)

    try:
        analysis_result = run_full_explore_sync(args.premise_id, outdir)
        log.info(
            "Worker done: premise=%s explore_decision=%s",
            args.premise_id,
            analysis_result.get("explore_decision"),
        )
        return 0
    except Exception as exc:
        log.error("Worker FAILED for premise %s: %s", args.premise_id, exc, exc_info=True)
        # Write error sentinel
        try:
            outdir.mkdir(parents=True, exist_ok=True)
            error_path = outdir / "error.txt"
            with open(error_path, "w", encoding="utf-8") as f:
                f.write(f"{type(exc).__name__}: {exc}\n")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
