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
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

# F352 pattern (mirrored from event_study.py): the lock must NEVER silently
# degrade — a broken fileutil import surfaces immediately as ImportError rather
# than disabling the FDR ledger concurrency guard.
from fileutil import file_lock as _fileutil_file_lock  # noqa: E402

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
# F394: asyncio.Lock guarding the cache read-check-write to prevent TOCTOU.
# Must be held in the async wrapper (_check_power_audit), not inside the sync
# function.
# SEC-06/DI-06: Lock deferred to first use via getter to avoid Python <3.10
# event-loop binding at module import time (RuntimeError in per-test event loops).
_power_audit_cache_lock: Optional[asyncio.Lock] = None
# Module-level threading.Lock guards the double-checked creation of
# _power_audit_cache_lock so concurrent calls from to_thread() or tests that
# share the module cannot race on the check-then-set window (R-03).
_power_audit_cache_lock_guard: threading.Lock = threading.Lock()


def _get_power_audit_lock() -> asyncio.Lock:
    """Lazy getter for the power-audit cache lock.

    Creates the Lock on first call (inside a running event loop) rather than
    at module import time, which avoids 'Future attached to a different loop'
    errors in test environments that spin up a fresh event loop per test.

    Thread-safe: double-checked locking via _power_audit_cache_lock_guard so
    concurrent to_thread() callers cannot each create a separate Lock instance.

    Loop-aware: if the cached lock was created in a now-closed event loop (a
    common pattern in pytest sessions that tear down and recreate the loop),
    the lock is re-created.  The stale lock is discarded — no waiters can exist
    on a closed loop's lock.
    """
    global _power_audit_cache_lock
    with _power_audit_cache_lock_guard:
        if _power_audit_cache_lock is None:
            _power_audit_cache_lock = asyncio.Lock()
        else:
            # Re-create if the lock's loop is closed or mismatched (per-test isolation).
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            lock_loop = getattr(_power_audit_cache_lock, "_loop", None)
            if lock_loop is not None and (lock_loop.is_closed() or lock_loop is not loop):
                _power_audit_cache_lock = asyncio.Lock()
    return _power_audit_cache_lock

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


async def _cleanup_job(premise_id: str) -> None:
    """Remove terminal job entries from _jobs/_job_locks after a short delay.

    F394: prevents unbounded memory growth.  Called from _bg() finally blocks
    after the terminal status update has been written so one polling cycle can
    still read the final state.

    R-1: 60s delay (up from 1s) covers realistic polling intervals for both
    preview and explore terminal jobs.  A poller that arrives after the job is
    gone falls back to store-derived status via the run-status endpoint.
    """
    await asyncio.sleep(60)
    _jobs.pop(premise_id, None)
    _job_locks.pop(premise_id, None)
    log.debug("F394: cleaned up job state for %s", premise_id)


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# F418 census gate helpers
# ---------------------------------------------------------------------------

def _find_latest_preview_outdir(premise_id: str, store: Any) -> Optional[Path]:
    """Return Path to the latest preview outdir for the given premise, or None."""
    try:
        p = store._get(premise_id)
    except KeyError:
        return None
    for entry in reversed(p.get("run_history", [])):
        if entry.get("run_type") == "preview" and entry.get("output_dir"):
            return Path(entry["output_dir"])
    return None


def _census_testable(census: Any, analysis_form: str) -> bool:
    """Return True if the census indicates the premise is testable.

    Conservative: None → False.
    """
    if analysis_form == "one_sample":
        return bool(census.testable_1samp)
    # dose_response
    return bool(census.testable_gap)


# ---------------------------------------------------------------------------
# F394 startup reconciliation: scan study dirs for ledger sidecars not yet in ledger
# ---------------------------------------------------------------------------

def _reconcile_ledger_sidecars() -> None:
    """Scan event_studies dirs for ledger_entry.json sidecars not yet in the FDR ledger.

    Idempotency: keyed by study_name — safe to run on every startup.
    Corrupt sidecars are backed up and skipped rather than crashing startup.

    Called from main.py lifespan after BotManager.load(), inside a try/except
    so a corrupt sidecar never blocks startup.
    """
    import json as _json
    import shutil as _shutil
    import time as _time
    from research.event_study import _atomic_write as _aw

    if not _STUDIES_DIR.exists():
        log.info("F394 reconcile: studies dir does not exist yet — nothing to reconcile")
        return

    raw_sidecars = list(_STUDIES_DIR.glob("premise_*_explore_*/ledger_entry.json"))
    # DI-04/SEC-04: filter sidecar list before any disk reads:
    #   1. Skip symlinked parent dirs (prevents attacker-symlink injection into FDR ledger).
    #   2. Require exact filename match "ledger_entry.json" (no .bak, no corrupt_* variants).
    #   3. Require a verdict file present in the same dir (only accept completed runs).
    sidecars = []
    for _sp in raw_sidecars:
        # Exact name check — glob should already guarantee this, but be explicit
        if _sp.name != "ledger_entry.json":
            continue
        # Skip symlinked parent dirs
        if _sp.parent.is_symlink():
            log.warning(
                "F394 reconcile: skipping sidecar in symlinked dir %s", _sp.parent
            )
            continue
        # Require at least one verdict file in the dir
        _has_verdict = (
            (_sp.parent / "r1_explore_verdict.json").exists()
            or (_sp.parent / "s1_onesample_verdict.json").exists()
        )
        if not _has_verdict:
            log.debug(
                "F394 reconcile: no verdict file in %s — skipping (incomplete run)",
                _sp.parent,
            )
            continue
        sidecars.append(_sp)

    if not sidecars:
        log.info("F394 reconcile: no eligible ledger sidecars found in %s", _STUDIES_DIR)
        return

    log.info("F394 reconcile: found %d eligible ledger sidecar(s) to check", len(sidecars))

    with _fileutil_file_lock(_REAL_FDR_LEDGER):
        # Read current ledger
        ledger_rows: list = []
        if _REAL_FDR_LEDGER.exists():
            try:
                ledger_rows = _json.loads(_REAL_FDR_LEDGER.read_text(encoding="utf-8"))
                if not isinstance(ledger_rows, list):
                    ledger_rows = []
            except (ValueError, _json.JSONDecodeError):
                ledger_rows = []

        # Build set of already-present study_names for O(1) lookup
        existing_study_names = {
            entry.get("study_name") for entry in ledger_rows if isinstance(entry, dict)
        }

        appended = 0
        for sidecar_path in sidecars:
            try:
                entry = _json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (ValueError, _json.JSONDecodeError) as parse_exc:
                # DI-3 pattern: back up corrupt sidecar, skip it
                _backup = sidecar_path.with_name(
                    f"ledger_entry.corrupt_{int(_time.time())}.json"
                )
                try:
                    _shutil.copy2(sidecar_path, _backup)
                except OSError:
                    pass
                log.error(
                    "F394 reconcile: corrupt sidecar at %s — backed up to %s, skipping. Error: %s",
                    sidecar_path, _backup, parse_exc,
                )
                continue
            except OSError as read_exc:
                log.warning("F394 reconcile: cannot read %s: %s — skipping", sidecar_path, read_exc)
                continue

            study_name = entry.get("study_name")
            if not study_name:
                log.warning(
                    "F394 reconcile: sidecar %s has no study_name — skipping", sidecar_path
                )
                continue

            # SEC-07/SEC-C: sanitize study_name before logging to prevent log injection
            # (crafted sidecar with embedded newlines, ANSI escapes, or tabs would
            # corrupt the log stream or spoof log entries).
            safe_name = re.sub(r'[\x00-\x1f\x7f]', ' ', str(study_name)[:128])

            if study_name in existing_study_names:
                log.debug("F394 reconcile: %s already in ledger — skip", safe_name)
                continue

            ledger_rows.append(entry)
            existing_study_names.add(study_name)
            appended += 1
            log.info("F394 reconcile: appended %s from sidecar %s", safe_name, sidecar_path)

        if appended > 0:
            try:
                _aw(_REAL_FDR_LEDGER, _json.dumps(ledger_rows, indent=2, default=str))
                log.info(
                    "F394 reconcile: wrote %d new entry/entries to FDR ledger (%d total)",
                    appended, len(ledger_rows),
                )
            except OSError as write_exc:
                log.error(
                    "F394 reconcile: FDR ledger write failed: %s", write_exc, exc_info=True
                )
        else:
            log.info("F394 reconcile: all sidecars already in ledger — no changes")


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
    from research.s1_dose import build_s1_events
    from research.r1_analysis import run_r1_analysis
    from research.event_study import run_event_study
    from turnaround_validation import _make_memoized_loader
    import numpy as np

    _DOSE_BUILDERS = {
        "r1_score": build_r1_events,
        "s1_score": build_s1_events,
    }

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

    # Build events (preview window only) — dispatch by dose_builder
    dose_builder_fn = _DOSE_BUILDERS.get(cr.dose_builder)
    if dose_builder_fn is None:
        raise ValueError(f"Unknown dose_builder: {cr.dose_builder!r}")
    _builder_kwargs: dict = dict(
        start=_PREVIEW_START,
        end=_PREVIEW_END,
        index_path=_INDEX_PATH,
        xml_dir=_XML_DIR,
        subs_dir=_SUBS_DIR,
        loader_fn=loader,
    )
    # Pass max_market_cap for s1 (and future builders that support it)
    if cr.floors.max_market_cap is not None:
        _builder_kwargs["max_market_cap"] = cr.floors.max_market_cap
    events_raw, dose_meta = dose_builder_fn(**_builder_kwargs)
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
    # F414: dispatch by analysis_form — a one_sample spec must not get the
    # dose-response quintile analysis (preview parity with the worker branch).
    if getattr(spec, "analysis_form", "dose_response") == "one_sample":
        from research.s1_onesample_analysis import run_s1_onesample_analysis
        analysis_result = run_s1_onesample_analysis(
            study_dir=output_dir,
            seed=_SEED,
            ledger_path=None,
            primary_horizon=max(spec.horizons),
            horizons=tuple(sorted(spec.horizons)),
            direction=spec.direction,
            design_mde_pp=spec.design_mde_pp,
            fdr_q=spec.fdr_q,
        )
    else:
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
        finally:
            # F394: clean up terminal job after a 1s delay so polling can read final state
            asyncio.create_task(_cleanup_job(premise_id))

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

    # R-3: compute census BEFORE acquiring the lock so blocking file I/O does not
    # stall the event loop while holding the per-premise asyncio lock.
    # We need to read the preview_outdir + spec from the store first (short, non-blocking).
    _pre_store = PremiseStore()
    try:
        _pre_p = _pre_store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")
    preview_outdir_pre = _find_latest_preview_outdir(premise_id, _pre_store)

    census_result = None
    census_warn = None
    import dataclasses as _dc
    if preview_outdir_pre:

        def _compute_census_pre():
            try:
                from research.premise_census import compute_census as _cc
                from research.premise_spec import PremiseSpec as _PremiseSpec
                _spec_obj = _PremiseSpec(**_pre_p.get("spec", {}))
                _events_path = preview_outdir_pre / "events.ndjson"
                return _cc(
                    events_path=_events_path,
                    analysis_form=_spec_obj.analysis_form,
                    horizons=tuple(_spec_obj.horizons),
                    primary_horizon=max(_spec_obj.horizons),
                    design_mde_pp=_spec_obj.design_mde_pp,
                ), _spec_obj.analysis_form
            except FileNotFoundError:
                log.info(
                    "F418 census gate: no events.ndjson in preview dir for %s — skipping",
                    premise_id,
                )
                return None, None
            except Exception as _cg_exc:
                log.warning(
                    "F418 census gate: census computation failed for %s: %s — skipping",
                    premise_id, _cg_exc,
                )
                return None, None

        _census_pre, _analysis_form_pre = await asyncio.to_thread(_compute_census_pre)
        if _census_pre is not None:
            if not _census_testable(_census_pre, _analysis_form_pre):
                census_result = _census_pre
                census_warn = _census_pre.note
                log.warning(
                    "F418 census gate: %s for %s — proceeding anyway (warn mode)",
                    census_warn, premise_id,
                )
            else:
                census_result = _census_pre
                log.info(
                    "F418 census gate: %s for %s — OK", _census_pre.note, premise_id
                )

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
            # F418: census computed from preview events before the full explore starts
            "census": _dc.asdict(census_result) if census_result is not None else None,
            "census_warn": census_warn,
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
            finally:
                # F394: clean up terminal dispatch-failure job after polling window
                # NOTE: only runs on dispatch failure. Poll-driven terminal states
                # (DONE/FAILED from worker) are cleaned up by poll_explore_status
                # path — that path does not call _cleanup_job because the job must
                # remain readable until the next poll cycle after the verdict write.
                asyncio.create_task(_cleanup_job(premise_id))

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
        # R-5: TOCTOU guard — re-read the job from _jobs inside this thread after the
        # subprocess call.  A concurrent poll may have already processed STATUS=DONE
        # and mutated status to 'done' or 'failed'.  Bail if status is no longer 'running'
        # to prevent duplicate run_history entries and duplicate FDR ledger appends.
        # (Ledger dedup in _existing_names below is an additional safety net for the
        # case where the guard is bypassed, e.g. an older server version.)
        current_job = _jobs.get(premise_id)
        if current_job is None or current_job.get("status") != "running":
            log.info(
                "poll_explore_status TOCTOU guard: status for %s is no longer 'running' "
                "(concurrent poll already processed this DONE transition) — skipping.",
                premise_id,
            )
            # COR-07: bail without appending run_history — intentional duplicate suppression.
            # The concurrent poll that did NOT bail already appended the run_history entry
            # and transitioned the status.  run_history is NOT appended here to prevent
            # duplicate explore-run entries in the store.  Low-probability edge: if the
            # concurrent caller fails between status update and run_history.append(), this
            # premise will have 'explored' status with no corresponding run_history entry;
            # that inconsistency is recoverable via re-run (deferred per COR-02 decision).
            return dict(current_job) if current_job is not None else {"status": "not_found"}
        # Read verdict from outdir
        verdict = _read_worker_verdict(outdir)
        if verdict:
            # Normal success path
            job.update({
                "status": "done",
                "finished_at": _utcnow_str(),
                "verdict": verdict,
            })
            # F410: Deliberate local ledger append (John's rule: must be explicit, never default)
            ledger_entry_path = Path(outdir) / "ledger_entry.json"
            if ledger_entry_path.exists():
                try:
                    import json as _json
                    import shutil as _shutil
                    import time as _time
                    from research.event_study import _atomic_write as _aw
                    # DI-3: parse the sidecar first; separate error handling for
                    # corrupt sidecar vs ledger write failure.
                    try:
                        with open(ledger_entry_path, encoding="utf-8") as _f:
                            _entry = _json.load(_f)
                    except (ValueError, _json.JSONDecodeError) as parse_exc:
                        # DI-3: corrupt sidecar — back up and log at ERROR (mirrors
                        # event_study.py DI-04 pattern at ~line 2476).
                        _backup = ledger_entry_path.with_name(
                            f"ledger_entry.corrupt_{int(_time.time())}.json"
                        )
                        try:
                            _shutil.copy2(ledger_entry_path, _backup)
                        except OSError:
                            pass
                        log.error(
                            "Corrupt ledger_entry.json for %s — backed up to %s, "
                            "FDR ledger NOT updated.  Parse error: %s",
                            premise_id, _backup, parse_exc,
                        )
                        _entry = None
                    if _entry is not None:
                        # DI-2: acquire an exclusive inter-process lock spanning the
                        # entire read-modify-write (F352 pattern from event_study.py).
                        # Prevents concurrent DONE transitions from silently dropping
                        # each other's entries from the alpha-accounting ledger.
                        with _fileutil_file_lock(_REAL_FDR_LEDGER):
                            _ledger_rows: list = []
                            if _REAL_FDR_LEDGER.exists():
                                try:
                                    _ledger_rows = _json.loads(_REAL_FDR_LEDGER.read_text(encoding="utf-8"))
                                    if not isinstance(_ledger_rows, list):
                                        _ledger_rows = []
                                except (ValueError, _json.JSONDecodeError):
                                    _ledger_rows = []
                            # DI-01/R-2: dedup by study_name before append — mirrors
                            # _reconcile_ledger_sidecars() to prevent double-append
                            # under concurrent GET /run-status polls.
                            _existing_names = {
                                r.get("study_name") for r in _ledger_rows
                                if isinstance(r, dict)
                            }
                            if _entry.get("study_name") in _existing_names:
                                log.warning(
                                    "FDR ledger: study_name %r already present, "
                                    "skipping duplicate append for %s",
                                    _entry.get("study_name"), premise_id,
                                )
                            else:
                                _ledger_rows.append(_entry)
                                try:
                                    _aw(_REAL_FDR_LEDGER, _json.dumps(_ledger_rows, indent=2, default=str))
                                except OSError as write_exc:
                                    # DI-3: ledger write failure — log at ERROR with traceback.
                                    log.error(
                                        "FDR ledger write failed for %s: %s",
                                        premise_id, write_exc, exc_info=True,
                                    )
                                    raise
                                log.info(
                                    "FDR ledger entry appended from %s (total entries: %d)",
                                    ledger_entry_path, len(_ledger_rows),
                                )
                except Exception as exc:
                    log.warning("Failed to append ledger entry for %s: %s", premise_id, exc)
            else:
                log.warning(
                    "No ledger_entry.json found in %s — FDR ledger NOT updated", outdir,
                )
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
                    # F418: persist the pre-explore census so autopsy can reuse it
                    "census": job.get("census"),
                    "census_warn": job.get("census_warn"),
                })
                s.transition(premise_id, "explored")
            except Exception as exc:
                log.error("Failed to persist explore result for %s: %s", premise_id, exc)
        else:
            # G3: DONE but verdict file missing/corrupt → treat as failure
            error_msg = (
                f"Worker reported STATUS=DONE but verdict file is missing or unreadable "
                f"at {outdir}/ (checked: r1_explore_verdict.json, s1_onesample_verdict.json). "
                f"Reverting to spec_ready."
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
    """Read the verdict JSON written by premise_run_worker.py.

    C-04: Try r1_explore_verdict.json first (dose_response path + s1 mirror write),
    then s1_onesample_verdict.json (direct path, if mirror write failed mid-flight).
    This handles a disk-full or crash between the s1 write and the mirror write.
    """
    import json
    base = Path(outdir)
    # Preferred name: the mirror contract — premise_run_worker.py writes r1_explore_verdict.json
    # for both dose_response and one_sample specs (lines 275–276 and 316–317 of worker).
    candidates = [
        base / "r1_explore_verdict.json",
        base / "s1_onesample_verdict.json",  # fallback: mirror write may have failed
    ]
    for verdict_path in candidates:
        if verdict_path.exists():
            try:
                with open(verdict_path, encoding="utf-8") as f:
                    data = json.load(f)
                if verdict_path.name != "r1_explore_verdict.json":
                    log.info(
                        "Worker verdict found at fallback path %s "
                        "(r1_explore_verdict.json missing — mirror write may have failed)",
                        verdict_path,
                    )
                return data
            except Exception as exc:
                log.error("Failed to read worker verdict from %s: %s", verdict_path, exc)
                # Try the next candidate rather than bailing immediately
                continue
    log.warning(
        "Worker verdict not found at %s — checked: %s",
        outdir,
        ", ".join(p.name for p in candidates),
    )
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

    F394: _get_power_audit_lock() prevents TOCTOU — two concurrent callers both
    seeing a stale cache would both invoke run_audit() (wasteful but harmless since
    it's idempotent).  The lock is held around the entire to_thread call so only
    one thread runs the audit at a time.
    """
    from fastapi import HTTPException

    async with _get_power_audit_lock():
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
