"""premise_run.py — Run service for the premise workbench (F389).

Adapts a PremiseSpec (abstract intent) to run_event_study (concrete harness).

Two run modes:
  preview    — in-process, reduced universe/window, fast (seconds-to-minutes).
  explore    — subprocess via bin/worker-dispatch.sh, full harness, ~76 min on worker.

Job state:
  _jobs: dict[premise_id, dict] — one slot per premise, in-memory only.
  Server restart clears jobs; clients detect stale-exploring via premise status.

graduate_to_confirm (OVERRIDE per decisions.md):
  Does NOT run any backtest or pass _REAL_FDR_LEDGER to anything.
  Validates state, power_audit pre-check, freezes spec_hash, transitions
  to awaiting_confirm, records confirm_request. Terminal reachable state in v1
  is awaiting_confirm. confirmed is only reachable by F393.

All v1 runs (preview AND full-explore) pass fdr_ledger_path=None.
_REAL_FDR_LEDGER is defined as a module constant for building future-command
strings — never actually passed to compile_spec/run_event_study in v1.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Premise ID format (G5: used in service-level guard + FastAPI Path constraint)
# ---------------------------------------------------------------------------
_PREMISE_ID_RE = re.compile(r"^p-[0-9a-f]{8}$")


def _assert_premise_id_format(premise_id: str) -> None:
    """Raise ValueError if premise_id does not match ^p-[0-9a-f]{8}$.

    Called before any path interpolation to prevent path traversal.
    """
    if not _PREMISE_ID_RE.match(premise_id):
        raise ValueError(
            f"Invalid premise_id format: {premise_id!r}. "
            f"Expected format: p-XXXXXXXX (8 lowercase hex chars)."
        )


# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen paths (mirrors run_r1_explore.py exactly)
# ---------------------------------------------------------------------------
_EDGAR_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache"
_STRATIFIED_DIR = _EDGAR_CACHE_DIR / "form4_stratified"
_INDEX_PATH = _STRATIFIED_DIR / "index.json"
_XML_DIR = _STRATIFIED_DIR
_SUBS_DIR = _EDGAR_CACHE_DIR / "submissions"
_PRICE_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"

# Real FDR ledger — referenced ONLY to build the future-command string in
# confirm_request records. NEVER passed to compile_spec or run_event_study in v1.
_REAL_FDR_LEDGER = _BACKEND_DIR / "data" / "turnaround" / "fdr_ledger.json"

# Worker scripts
_BIN_DIR = _REPO_ROOT / "bin"

# ---------------------------------------------------------------------------
# Study constants (mirrors run_r1_explore.py)
# ---------------------------------------------------------------------------
_EXPLORE_START = date(2015, 1, 1)
_EXPLORE_END = date(2020, 12, 31)
_SEED = 20260606

# Preview: smaller window for speed
_PREVIEW_START = date(2019, 1, 1)
_PREVIEW_END = date(2020, 12, 31)

# Loader params (full explore)
_START_YEAR = 2015
_END_YEAR = 2020
_LOW_LOOKBACK_YEARS = 2
_HORIZON_MONTHS = 6
_DATA_SOURCE = "yahoo"

# Preview loader params (smaller, faster)
_PREVIEW_START_YEAR = 2018
_PREVIEW_END_YEAR = 2020

# Universe span gate
_PRICE_SPAN_START = "20120101"
_PRICE_SPAN_END = "20211231"

# ---------------------------------------------------------------------------
# Confirm gate constants
# ---------------------------------------------------------------------------
_CONFIRM_MDE_THRESHOLD_PP = 10.0  # programme-level sanity gate

# ---------------------------------------------------------------------------
# Power audit cache (programme-level, shared across premises, 1h TTL)
# ---------------------------------------------------------------------------
_power_audit_cache: dict = {}  # keys: "result", "cached_at"
_POWER_AUDIT_TTL_SECS = 3600

# ---------------------------------------------------------------------------
# Job state (in-process memory only; cleared on server restart)
# ---------------------------------------------------------------------------
# Key: premise_id → job dict
_jobs: dict[str, dict] = {}
# Per-premise asyncio lock (created on demand)
_job_locks: dict[str, asyncio.Lock] = {}


def _get_job_lock(premise_id: str) -> asyncio.Lock:
    # G9: use setdefault — single atomic dict op, eliminates check-then-act race
    return _job_locks.setdefault(premise_id, asyncio.Lock())


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Cache absence check (FAIL LOUD)
# ---------------------------------------------------------------------------

def _check_required_caches() -> None:
    """Raise RuntimeError if any required cache is absent. Must be called before compute."""
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


# ---------------------------------------------------------------------------
# Verdict extraction helper
# ---------------------------------------------------------------------------

def _extract_verdict(analysis_result: dict, harness_meta: dict) -> dict:
    """Extract the plain-English-able verdict dict from run_r1_analysis output."""
    return {
        "explore_decision": analysis_result.get("explore_decision"),
        "n_valid_events": analysis_result.get("n_valid_events"),
        "mde_q5q1_pp": analysis_result.get("mde_q5q1_pp"),
        "mde_gate_passed": analysis_result.get("mde_gate_passed"),
        "H1": analysis_result.get("H1"),
        "H1b": analysis_result.get("H1b"),
        "H2": analysis_result.get("H2"),
        "fdr_report": analysis_result.get("fdr_report"),
        "era_lens": analysis_result.get("era_lens"),
        "peer_lens": analysis_result.get("peer_lens"),
        "regime_lens": analysis_result.get("regime_lens"),
        "perturbation_band": analysis_result.get("perturbation_band"),
        # Harness meta
        "n_events_harness": harness_meta.get("n_events"),
        "n_explore_harness": harness_meta.get("n_explore"),
        "n_confirm_harness": harness_meta.get("n_confirm"),
        "sic_coverage": harness_meta.get("sic_coverage"),
        "regime_breakdown": harness_meta.get("regime_breakdown"),
    }


# ---------------------------------------------------------------------------
# Preview run (in-process, sync)
# ---------------------------------------------------------------------------

def _run_preview_sync(premise_id: str) -> dict:
    """Synchronous preview worker — called via asyncio.to_thread().

    Reduced scope:
    - Event window: 2019-01-01 .. 2020-12-31
    - Universe: event tickers only (no background universe expansion)
    - n_boot: 99 (override via spec copy)
    - fdr_ledger_path: None (NEVER touches real ledger)
    """
    # G5: assert format before any path interpolation
    _assert_premise_id_format(premise_id)

    # Lazy imports inside sync worker (turnaround.py pattern)
    from research.premise_compile import compile_spec
    from research.premise_spec import PremiseSpec
    from research.premise_store import PremiseStore
    from research.r1_dose import build_r1_events
    from research.r1_analysis import run_r1_analysis
    from research.event_study import run_event_study
    from turnaround_validation import _make_memoized_loader
    import numpy as np

    _check_required_caches()

    store = PremiseStore()
    p = store.premises.get(premise_id)
    if p is None:
        raise KeyError(f"Premise not found: {premise_id!r}")

    spec_dict = p.get("spec")
    if spec_dict is None:
        raise ValueError(f"Premise {premise_id!r} has no spec. Save a spec first.")

    spec = PremiseSpec(**spec_dict)

    # Preview spec copy: override n_boot=99 (PremiseSpec is frozen=True)
    preview_spec_dict = spec.model_dump()
    preview_spec_dict["n_boot"] = 99
    preview_spec = PremiseSpec(**preview_spec_dict)

    ts = int(time.time())
    study_name = f"premise_{premise_id}_preview_{ts}"
    output_dir = _STUDIES_DIR / study_name

    # Compile — fdr_ledger_path=None (NEVER touches real ledger in v1)
    cr = compile_spec(preview_spec, study_name=study_name, output_dir=output_dir, fdr_ledger_path=None)

    # Build loader (smaller year range for speed)
    loader = _make_memoized_loader(
        start_year=_PREVIEW_START_YEAR,
        end_year=_PREVIEW_END_YEAR,
        low_lookback_years=_LOW_LOOKBACK_YEARS,
        horizon_months=_HORIZON_MONTHS,
        data_source=_DATA_SOURCE,
    )

    # Build events (preview window only)
    events_raw, dose_meta = build_r1_events(
        start=_PREVIEW_START,
        end=_PREVIEW_END,
        index_path=_INDEX_PATH,
        xml_dir=_XML_DIR,
        subs_dir=_SUBS_DIR,
        loader_fn=loader,
    )
    log.info(
        "Preview dose builder: events_raw=%d (2019-2020 window)", len(events_raw)
    )

    # Universe: event tickers only (no background universe expansion)
    event_tickers = list({e.ticker for e in events_raw})

    # Run harness
    rng = np.random.default_rng(_SEED)
    outcomes, harness_meta = run_event_study(
        events=events_raw,
        config=cr.config,
        loader_fn=loader,
        universe_tickers=event_tickers,  # event-tickers-only for speed
        rng=rng,
    )
    log.info(
        "Preview harness done: n_events=%d, n_explore=%d",
        harness_meta.get("n_events", 0),
        harness_meta.get("n_explore", 0),
    )

    # Analysis (ledger_path=None → skip FDR append)
    analysis_result = run_r1_analysis(
        study_dir=output_dir,
        seed=_SEED,
        ledger_path=None,
    )

    verdict = _extract_verdict(analysis_result, harness_meta)
    verdict["run_type"] = "preview"
    verdict["verdict_valid"] = False
    verdict["note"] = (
        "Preview run — universe excess not computed (event tickers only). "
        "Run full explore for a valid verdict."
    )

    # Persist run record
    store2 = PremiseStore()
    store2.append_run(premise_id, {
        "run_type": "preview",
        "started_at": _utcnow_str(),
        "study_name": study_name,
        "output_dir": str(output_dir),
        "verdict_valid": False,
        "verdict": verdict,
    })

    return verdict


# ---------------------------------------------------------------------------
# Full explore run (subprocess via worker-dispatch.sh)
# ---------------------------------------------------------------------------

def _probe_worker() -> tuple[str, str]:
    """Run bin/worker-probe.sh --quiet and parse the RECOMMEND line.

    Returns (worker_host, worker_shell) or ("LOCAL", "LOCAL").
    """
    probe_script = str(_BIN_DIR / "worker-probe.sh")
    try:
        result = subprocess.run(
            [probe_script, "--quiet"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("worker-probe.sh failed: %s — falling back to LOCAL", exc)
        return "LOCAL", "LOCAL"

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("RECOMMEND:"):
            rest = line[len("RECOMMEND:"):].strip()
            if rest == "LOCAL":
                return "LOCAL", "LOCAL"
            # Parse "WORKER_HOST=mfcore01 WORKER_SHELL=native"
            host = ""
            shell = ""
            for token in rest.split():
                if token.startswith("WORKER_HOST="):
                    host = token[len("WORKER_HOST="):]
                elif token.startswith("WORKER_SHELL="):
                    shell = token[len("WORKER_SHELL="):]
            if host:
                return host, shell or "wsl"

    log.warning("worker-probe.sh: no RECOMMEND line found — falling back to LOCAL")
    return "LOCAL", "LOCAL"


def _run_full_explore_sync(premise_id: str, outdir: str, logname: str) -> dict:
    """Dispatch full explore via worker-dispatch.sh (sync, runs in asyncio.to_thread).

    Returns the dispatch metadata dict. Actual results are polled via
    bin/worker-status.sh.
    """
    # G5: assert format before any path interpolation
    _assert_premise_id_format(premise_id)

    # Probe worker
    worker_host, worker_shell = _probe_worker()

    # WORKER_REQUIRE: paths that MUST exist on the worker
    require_paths = ",".join([
        "backend/data/turnaround/edgar_cache/form4_stratified/index.json",
        "backend/data/turnaround/edgar_cache/submissions",
        "backend/data/turnaround/price_cache/v1",
    ])

    dispatch_script = str(_BIN_DIR / "worker-dispatch.sh")
    worker_script = str(_THIS_DIR / "premise_run_worker.py")

    env = dict(os.environ)
    env["WORKER_REQUIRE"] = require_paths
    if worker_host != "LOCAL":
        env["WORKER_HOST"] = worker_host
        env["WORKER_SHELL"] = worker_shell

    cmd = [
        dispatch_script,
        outdir,
        logname,
        worker_script,
        "--premise-id", premise_id,
        "--outdir", outdir,
    ]

    log.info(
        "Dispatching full explore: worker_host=%s cmd=%s",
        worker_host,
        " ".join(cmd),
    )
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    output = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    log.info("worker-dispatch stdout: %s", output)
    if stderr:
        log.warning("worker-dispatch stderr: %s", stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"worker-dispatch.sh exited {result.returncode}. "
            f"stdout: {output!r}  stderr: {stderr!r}"
        )

    return {
        "dispatched_output": output,
        "worker_host": worker_host,
        "worker_shell": worker_shell,
        "outdir": outdir,
        "logname": logname,
    }


# ---------------------------------------------------------------------------
# Async run triggers
# ---------------------------------------------------------------------------

async def run_preview(premise_id: str) -> None:
    """Trigger an async preview run for the given premise.

    Updates job state; returns immediately. Results in job state when done.
    Raises HTTPException(409) if a job is already active or premise status is wrong.
    """
    from fastapi import HTTPException
    from research.premise_store import PremiseStore

    # G8: validate source status BEFORE scheduling (409 if not runnable)
    # G9: store transition is done INSIDE the lock to avoid state-guard race
    _RUNNABLE_STATES = {"spec_ready", "explored"}

    lock = _get_job_lock(premise_id)
    async with lock:
        existing = _jobs.get(premise_id, {})
        if existing.get("status") == "running":
            raise HTTPException(409, f"Run already in progress for premise {premise_id!r}")

        # G8: check current premise status before accepting the run
        store = PremiseStore()
        try:
            p = store._get(premise_id)
        except KeyError:
            raise HTTPException(404, f"Premise not found: {premise_id!r}")

        current_status = p.get("status")
        if current_status not in _RUNNABLE_STATES:
            raise HTTPException(
                409,
                f"Cannot trigger run for premise {premise_id!r}: status is "
                f"{current_status!r} (must be one of {sorted(_RUNNABLE_STATES)})."
            )

        # G9: transition to exploring INSIDE the lock
        try:
            store.transition(premise_id, "exploring")
        except Exception as exc:
            raise HTTPException(409, f"Cannot transition {premise_id!r} to 'exploring': {exc}")

        _jobs[premise_id] = {
            "status": "running",
            "run_type": "preview",
            "started_at": _utcnow_str(),
            "finished_at": None,
            "error": None,
            "outdir": None,
            "logname": None,
            "verdict": None,
        }

    # Run in background thread
    async def _bg():
        try:
            verdict = await asyncio.to_thread(_run_preview_sync, premise_id)
            _jobs[premise_id].update({
                "status": "done",
                "finished_at": _utcnow_str(),
                "verdict": verdict,
            })
            # Transition to explored
            s = PremiseStore()
            s.transition(premise_id, "explored")
        except Exception as exc:
            log.error("Preview run failed for %s: %s", premise_id, exc, exc_info=True)
            _jobs[premise_id].update({
                "status": "failed",
                "finished_at": _utcnow_str(),
                "error": str(exc),
            })
            # Revert to spec_ready on failure
            try:
                s = PremiseStore()
                s.set_error_note(premise_id, str(exc))
                s.transition(premise_id, "spec_ready")
            except Exception as te:
                log.warning("Failed to transition %s back to spec_ready: %s", premise_id, te)

    asyncio.create_task(_bg())


async def run_full_explore(premise_id: str) -> None:
    """Trigger an async full-explore dispatch for the given premise.

    Dispatches to worker via bin/worker-dispatch.sh. Returns immediately.
    Status polled via poll_explore_status(). Raises HTTPException(409) if active
    or premise status is not runnable.
    """
    from fastapi import HTTPException
    from research.premise_store import PremiseStore

    # G8: validate source status BEFORE scheduling (409 if not runnable)
    _RUNNABLE_STATES = {"spec_ready", "explored"}

    lock = _get_job_lock(premise_id)
    async with lock:
        existing = _jobs.get(premise_id, {})
        if existing.get("status") == "running":
            raise HTTPException(409, f"Run already in progress for premise {premise_id!r}")

        # G8: check current premise status before accepting the run
        store = PremiseStore()
        try:
            p = store._get(premise_id)
        except KeyError:
            raise HTTPException(404, f"Premise not found: {premise_id!r}")

        current_status = p.get("status")
        if current_status not in _RUNNABLE_STATES:
            raise HTTPException(
                409,
                f"Cannot trigger run for premise {premise_id!r}: status is "
                f"{current_status!r} (must be one of {sorted(_RUNNABLE_STATES)})."
            )

        ts = int(time.time())
        logname = f"premise_{premise_id}_explore_{ts}.log"
        outdir = str(_STUDIES_DIR / f"premise_{premise_id}_explore_{ts}")

        # G9: transition to exploring INSIDE the lock
        try:
            store.transition(premise_id, "exploring")
        except Exception as exc:
            raise HTTPException(409, f"Cannot transition {premise_id!r} to 'exploring': {exc}")

        _jobs[premise_id] = {
            "status": "running",
            "run_type": "explore",
            "started_at": _utcnow_str(),
            "finished_at": None,
            "error": None,
            "outdir": outdir,
            "logname": logname,
            "verdict": None,
        }

    # Capture outdir/logname at lock-close time (G from review-reliability R8)
    outdir_captured = outdir
    logname_captured = logname

    async def _bg():
        try:
            await asyncio.to_thread(
                _run_full_explore_sync, premise_id, outdir_captured, logname_captured
            )
            # Dispatch succeeded — job is now in flight on the worker.
            # Status transitions (done/failed) happen via poll_explore_status().
            log.info(
                "Full explore dispatched for %s (outdir=%s logname=%s)",
                premise_id, outdir_captured, logname_captured,
            )
        except Exception as exc:
            log.error("Full explore dispatch failed for %s: %s", premise_id, exc, exc_info=True)
            _jobs[premise_id].update({
                "status": "failed",
                "finished_at": _utcnow_str(),
                "error": str(exc),
            })
            try:
                s = PremiseStore()
                s.set_error_note(premise_id, str(exc))
                s.transition(premise_id, "spec_ready")
            except Exception as te:
                log.warning("Failed to transition %s back to spec_ready: %s", premise_id, te)

    asyncio.create_task(_bg())


def poll_explore_status(premise_id: str) -> dict:
    """Poll bin/worker-status.sh for a running full-explore job.

    Called synchronously. Updates job state on DONE/FAILED.
    Returns the current job state dict.
    """
    job = _jobs.get(premise_id)
    if job is None:
        return {"status": "not_found"}

    if job["run_type"] != "explore" or job["status"] not in ("running",):
        return dict(job)

    outdir = job.get("outdir", "")
    logname = job.get("logname", "")
    if not outdir or not logname:
        return dict(job)

    status_script = str(_BIN_DIR / "worker-status.sh")
    try:
        result = subprocess.run(
            [status_script, outdir, logname],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "").strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("worker-status.sh error for %s: %s", premise_id, exc)
        return dict(job)

    if "STATUS=DONE" in output:
        # Read verdict from outdir
        verdict = _read_worker_verdict(outdir)
        if verdict:
            # Normal success path
            job.update({
                "status": "done",
                "finished_at": _utcnow_str(),
                "verdict": verdict,
            })
            try:
                from research.premise_store import PremiseStore
                s = PremiseStore()
                s.append_run(premise_id, {
                    "run_type": "explore",
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                    "study_name": logname.replace(".log", ""),
                    "output_dir": outdir,
                    "verdict_valid": True,
                    "verdict": verdict,
                })
                s.transition(premise_id, "explored")
            except Exception as exc:
                log.error("Failed to persist explore result for %s: %s", premise_id, exc)
        else:
            # G3: DONE but verdict file missing/corrupt → treat as failure
            error_msg = (
                f"Worker reported STATUS=DONE but verdict file is missing or unreadable "
                f"at {outdir}/r1_explore_verdict.json. Reverting to spec_ready."
            )
            log.error("DONE-without-verdict for %s: %s", premise_id, error_msg)
            job.update({
                "status": "failed",
                "finished_at": _utcnow_str(),
                "error": error_msg,
                "verdict": None,
            })
            try:
                from research.premise_store import PremiseStore
                s = PremiseStore()
                s.set_error_note(premise_id, error_msg)
                s.transition(premise_id, "spec_ready")
            except Exception as exc:
                log.warning("Failed to revert %s after missing verdict: %s", premise_id, exc)
    elif "STATUS=FAILED" in output:
        # G4: worker reported FAILED
        error_msg = f"Worker reported FAILED. See {outdir}/{logname}"
        job.update({
            "status": "failed",
            "finished_at": _utcnow_str(),
            "error": error_msg,
        })
        try:
            from research.premise_store import PremiseStore
            s = PremiseStore()
            s.set_error_note(premise_id, error_msg)
            s.transition(premise_id, "spec_ready")
        except Exception as exc:
            log.warning("Failed to revert %s: %s", premise_id, exc)
    elif "STATUS=TIMEOUT" in output:
        # G4: worker timed out — treat as failure; no escape path otherwise
        error_msg = f"Worker timed out. See {outdir}/{logname}"
        log.warning("Worker TIMEOUT for %s: %s", premise_id, error_msg)
        job.update({
            "status": "failed",
            "finished_at": _utcnow_str(),
            "error": error_msg,
        })
        try:
            from research.premise_store import PremiseStore
            s = PremiseStore()
            s.set_error_note(premise_id, error_msg)
            s.transition(premise_id, "spec_ready")
        except Exception as exc:
            log.warning("Failed to revert %s after timeout: %s", premise_id, exc)

    return dict(job)


def _read_worker_verdict(outdir: str) -> Optional[dict]:
    """Read the verdict JSON written by premise_run_worker.py."""
    import json
    verdict_path = Path(outdir) / "r1_explore_verdict.json"
    if not verdict_path.exists():
        log.warning("Worker verdict not found at %s", verdict_path)
        return None
    try:
        with open(verdict_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.error("Failed to read worker verdict from %s: %s", verdict_path, exc)
        return None


# ---------------------------------------------------------------------------
# Power audit (cached, programme-level)
# ---------------------------------------------------------------------------

def _run_power_audit_sync() -> dict:
    """Run power_audit.run_audit() — slow, cached for 1h.

    Returns PowerAuditResult dict.
    Raises RuntimeError if price_cache absent.
    """
    from research.power_audit import run_audit

    now = time.monotonic()
    cached_at = _power_audit_cache.get("cached_at", 0.0)
    if _power_audit_cache.get("result") and (now - cached_at) < _POWER_AUDIT_TTL_SECS:
        log.info("Power audit: returning cached result (age=%.0fs)", now - cached_at)
        return _power_audit_cache["result"]

    log.info("Running power audit (n_reps=50, programme-level sanity gate)...")
    result = run_audit(n_reps=50, verbose=False)
    _power_audit_cache["result"] = result
    _power_audit_cache["cached_at"] = time.monotonic()
    return result


async def _check_power_audit() -> None:
    """Async power audit pre-check for graduate_to_confirm.

    Raises HTTPException(400) if underpowered.
    """
    from fastapi import HTTPException

    result = await asyncio.to_thread(_run_power_audit_sync)
    mde_vals = [v for v in result.get("mde_80pct", {}).values() if v is not None]
    if not mde_vals:
        raise HTTPException(
            400,
            f"Power pre-check failed: no MDE values computed. "
            f"Check price_cache availability. "
            f"(threshold: {_CONFIRM_MDE_THRESHOLD_PP}pp)"
        )
    best_mde = min(mde_vals)
    if best_mde > _CONFIRM_MDE_THRESHOLD_PP:
        raise HTTPException(
            400,
            f"Power pre-check failed: best MDE at 80% power is {best_mde:.1f}pp "
            f"(threshold: {_CONFIRM_MDE_THRESHOLD_PP}pp). "
            f"Fix the design before confirming."
        )
    log.info("Power audit passed: best_mde=%.2fpp (threshold=%.1fpp)", best_mde, _CONFIRM_MDE_THRESHOLD_PP)


# ---------------------------------------------------------------------------
# graduate_to_confirm (OVERRIDE — no run, no real ledger, terminal=awaiting_confirm)
# ---------------------------------------------------------------------------

async def graduate_to_confirm(premise_id: str) -> dict:
    """Gate: power_audit → freeze spec_hash → idempotency → awaiting_confirm.

    OVERRIDE (decisions.md):
    - Does NOT run any backtest.
    - Does NOT pass _REAL_FDR_LEDGER to anything.
    - Does NOT reach state 'confirmed'.
    - Terminal reachable state in v1: awaiting_confirm.
    - Records a confirm_request with the frozen spec + the exact worker command
      a future real OOS run (F393) would use.

    Returns a response dict with a clear message stating the deferred nature.
    """
    import copy as _copy
    from fastapi import HTTPException
    from research.premise_store import PremiseStore
    from research.premise_spec import PremiseSpec, spec_hash as compute_spec_hash

    # G7: single store instance throughout; snapshot/rollback on failure
    store = PremiseStore()
    snapshot = _copy.deepcopy(store.premises)

    # 1. Load premise
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    # 2. Assert status == "explored"
    current_status = p.get("status")
    if current_status != "explored":
        raise HTTPException(
            409,
            f"Cannot graduate premise {premise_id!r}: status is {current_status!r} "
            f"(must be 'explored')."
        )

    # 3. Assert no prior confirm_request for this premise (G1: correct field name)
    run_history = p.get("run_history", [])
    if any(r.get("type") == "confirm_request" for r in run_history):
        raise HTTPException(409, f"Premise {premise_id!r} already has a confirm_request.")

    # 4. Compute spec_hash + store-wide idempotency check
    spec_dict = p.get("spec")
    if spec_dict is None:
        raise HTTPException(400, f"Premise {premise_id!r} has no spec.")

    spec = PremiseSpec(**spec_dict)
    proposed_hash = compute_spec_hash(spec)

    # G1: check store-wide using stored spec_hash (not the spec sub-dict)
    for other_id, other_p in store.premises.items():
        if other_id == premise_id:
            continue
        other_status = other_p.get("status", "")
        other_spec = other_p.get("spec") or {}
        other_hash = other_spec.get("spec_hash")
        if other_status in ("confirmed", "awaiting_confirm") and other_hash == proposed_hash:
            raise HTTPException(
                409,
                f"Spec hash {proposed_hash!r} already exists in premise {other_id!r} "
                f"(status: {other_status!r}). Duplicate structural spec refused."
            )

    # 5. Power audit pre-check (async, cached)
    await _check_power_audit()

    # 6-8. Freeze spec + transition + record confirm_request — all on one store instance
    #       with snapshot/rollback (G7: mirrors F388 store rollback pattern).
    try:
        # 6. Freeze spec: write spec_hash onto stored spec
        frozen_spec_dict = spec.model_dump()
        frozen_spec_dict["spec_hash"] = proposed_hash
        store.add_spec(premise_id, frozen_spec_dict)

        # 7. Transition to awaiting_confirm
        store.transition(premise_id, "awaiting_confirm")

        # 8. Build the future-command string (F393 OOS run — _REAL_FDR_LEDGER referenced
        #    here ONLY to construct the string, never passed to any compute function in v1)
        ts_label = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        future_outdir = str(_STUDIES_DIR / f"premise_{premise_id}_confirm_{proposed_hash[:8]}_{ts_label}")
        future_logname = f"premise_{premise_id}_confirm_{ts_label}.log"
        future_worker_cmd = (
            f"WORKER_HOST=<host> WORKER_SHELL=<shell> "
            f"WORKER_REQUIRE=backend/data/turnaround/edgar_cache/form4_stratified/index.json,"
            f"backend/data/turnaround/edgar_cache/submissions,"
            f"backend/data/turnaround/price_cache/v1 "
            f"bin/worker-dispatch.sh {future_outdir} {future_logname} "
            f"backend/research/premise_run_worker.py "
            f"--premise-id {premise_id} "
            f"--outdir {future_outdir} "
            f"--fdr-ledger-path {_REAL_FDR_LEDGER}  # F393: real OOS confirm run"
        )

        # Record confirm_request entry
        confirm_request = {
            "type": "confirm_request",
            "recorded_at": _utcnow_str(),
            "spec_hash": proposed_hash,
            "frozen_spec": frozen_spec_dict,
            "future_worker_cmd": future_worker_cmd,
            "note": (
                "Frozen + power-checked + queued for OOS confirm. "
                "The real out-of-sample confirm run that writes the FDR ledger "
                "is a separate gated step (F393)."
            ),
        }
        store.append_run(premise_id, confirm_request)

    except Exception:
        # G7: rollback to snapshot on any failure in steps 6-8
        store.premises = snapshot
        try:
            store.save()
        except Exception as save_exc:
            log.error(
                "graduate_to_confirm: snapshot rollback save failed for %s: %s",
                premise_id, save_exc,
            )
        raise

    log.info(
        "graduate_to_confirm: premise=%s hash=%s → awaiting_confirm (F393 deferred)",
        premise_id,
        proposed_hash,
    )

    return {
        "premise_id": premise_id,
        "spec_hash": proposed_hash,
        "status": "awaiting_confirm",
        "message": (
            "Frozen + power-checked + queued for OOS confirm. "
            "The real out-of-sample confirm run that writes the FDR ledger "
            "is a separate gated step (F393)."
        ),
        "confirm_request": confirm_request,
    }
