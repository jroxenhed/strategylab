"""backend/routes/turnaround.py — Turnaround screen endpoints.

Endpoints:
  POST /api/turnaround/scan              — start async full-universe scan
  GET  /api/turnaround/scan/status       — poll scan state
  GET  /api/turnaround/watchlist         — read last persisted watchlist
  POST /api/turnaround/validate          — start async validation run
  GET  /api/turnaround/validate/status   — poll validation state
  GET  /api/turnaround/validate/result   — read last persisted validation result

Async pattern: module-level state dicts + BackgroundTasks.
_run_scan_background / _run_validate_background are async fns that use
asyncio.to_thread() to run sync workers off the event loop.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from fileutil import atomic_write_text  # REL-09: top-level import to surface ImportError at startup

logger = logging.getLogger(__name__)

router = APIRouter()


# DI-01: custom encoder for date/datetime objects in dataclasses.asdict output
class _DateEncoder(json.JSONEncoder):
    def default(self, o: object) -> object:
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)

# ---------------------------------------------------------------------------
# Persistence paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "turnaround"
_WATCHLIST_PATH = _DATA_DIR / "watchlist.json"
_VALIDATION_PATH = _DATA_DIR / "validation_result.json"


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Module-level state dicts (one job at a time each, stateless across restarts)
# REL-01/REL-02/PY-08/DI-09: asyncio.Lock per operation type — check+set atomic
# ---------------------------------------------------------------------------

_scan_state: dict = {
    "status": "idle",
    "started_at": None,
    "duration_secs": None,
    "error": None,
    "candidate_count": None,
}

_validate_state: dict = {
    "status": "idle",
    "started_at": None,
    "duration_secs": None,
    "error": None,
}

# F313: per-run cancel event + started_at_monotonic for live duration_secs
# These are replaced at the start of each new run.
_validate_cancel_event: Optional[threading.Event] = None
_validate_started_at_monotonic: Optional[float] = None

# F313: mutable progress object — mutated by the worker thread via GIL-atomic
# single-key writes; read by the async status route (no asyncio.Lock needed).
# Type is ValidationProgress (from turnaround_validation), imported lazily.
_validate_progress: Optional[object] = None  # ValidationProgress instance or None

_scan_lock: asyncio.Lock = asyncio.Lock()
_validate_lock: asyncio.Lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

from turnaround import FilterParams  # noqa: E402


class ScanRequest(BaseModel):
    params: FilterParams = Field(default_factory=FilterParams)
    max_universe: int = Field(default=5000, ge=100, le=15000)


class ScanStatusResponse(BaseModel):
    status: str
    started_at: Optional[str]
    duration_secs: Optional[float]
    error: Optional[str]
    candidate_count: Optional[int]


# ValidationRequest imported from turnaround_validation at runtime (lazy)


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Scan background worker
# ---------------------------------------------------------------------------

def _run_scan_sync(params: FilterParams, max_universe: int) -> list[dict]:
    """Synchronous scan worker — runs in asyncio.to_thread()."""
    import edgar
    from turnaround import build_universe, run_filter
    import dataclasses as dc

    raw_universe = edgar.fetch_universe()
    universe = build_universe(raw_universe, params)
    # Apply max_universe cap in deterministic alphabetical order (already sorted by build_universe)
    if max_universe < len(universe):
        universe = universe[:max_universe]

    as_of = datetime.now(timezone.utc).date()
    candidates = run_filter(universe, as_of=as_of, params=params)
    return [dc.asdict(c) for c in candidates]


async def _run_scan_background(params: FilterParams, max_universe: int) -> None:
    """Async background function: calls _run_scan_sync in a thread.

    REL-02: try/finally guarantees ANY exit path (including CancelledError) sets
    a terminal status — status never stays 'running' after the worker exits.
    """
    started = datetime.now(timezone.utc)
    try:
        results = await asyncio.to_thread(_run_scan_sync, params, max_universe)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _ensure_data_dir()
        atomic_write_text(_WATCHLIST_PATH, json.dumps(results, cls=_DateEncoder))
        _scan_state.update({
            "status": "done",
            "duration_secs": elapsed,
            "candidate_count": len(results),
            "error": None,
        })
        logger.info("Scan done: %d candidates in %.1fs", len(results), elapsed)
    except asyncio.CancelledError:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _scan_state.update({"status": "cancelled", "duration_secs": elapsed, "error": "cancelled"})
        raise
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        logger.error("Scan failed: %s", exc, exc_info=True)
        _scan_state.update({
            "status": "error",
            "duration_secs": elapsed,
            "error": str(exc),
        })


# ---------------------------------------------------------------------------
# Validation background worker
# ---------------------------------------------------------------------------

def _run_validate_sync(
    req_dict: dict,
    cancel_event: threading.Event,
    progress: object,  # ValidationProgress instance
    config_name: Optional[str] = None,
) -> dict:
    """Synchronous validation worker — runs in asyncio.to_thread().

    F313: cancel_event and progress are injected by the route.
    Raises RuntimeError('_cancelled_') when cancel_event fires mid-run.

    Unit 1 (D12): config_name resolves to a CandidateSourceConfig (or None for legacy).
    Resolution errors surface as RuntimeError → status="error" at GET /validate/status.
    """
    import dataclasses as dc
    # Late import — lane C may not exist during parallel build
    from turnaround_validation import ValidationRequest, run_validation

    # Resolve config before constructing req so refusal errors surface cleanly
    try:
        candidate_source = _resolve_candidate_source(config_name)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    req = ValidationRequest(**req_dict)
    result = run_validation(
        req,
        cancel_event=cancel_event,
        progress=progress,
        candidate_source=candidate_source,
    )
    return dc.asdict(result)


async def _run_validate_background(req_dict: dict, config_name: Optional[str] = None) -> None:
    """Async background function: calls _run_validate_sync in a thread.

    REL-02: try/finally guarantees ANY exit path (including CancelledError) sets
    a terminal status — status never stays 'running' after the worker exits.

    F313: pass cancel_event + progress to the sync worker.
    Cancel (user intent) → status='cancelled', no result file written.
    Timeout (budget) → status='timeout', partial-but-honest result IS written
    (timed_out=True + dates_completed annotation in the result payload).

    Unit 1 (D12): config_name forwarded to _run_validate_sync for source resolution.
    """
    global _validate_cancel_event, _validate_started_at_monotonic, _validate_progress

    cancel_event = _validate_cancel_event
    progress = _validate_progress
    started = datetime.now(timezone.utc)
    try:
        result_dict = await asyncio.to_thread(
            _run_validate_sync, req_dict, cancel_event, progress, config_name
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _ensure_data_dir()
        atomic_write_text(_VALIDATION_PATH, json.dumps(result_dict, cls=_DateEncoder), backup_depth=3)
        final_status = "timeout" if result_dict.get("timed_out") else "done"
        _validate_state.update({
            "status": final_status,
            "duration_secs": elapsed,
            "error": None,
        })
        logger.info("Validation %s in %.1fs", final_status, elapsed)
    except asyncio.CancelledError:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _validate_state.update({"status": "cancelled", "duration_secs": elapsed, "error": "cancelled"})
        raise
    except RuntimeError as exc:
        if "_cancelled_" in str(exc):
            # F313: user-initiated cancel via POST /cancel — no result file written
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            _validate_state.update({
                "status": "cancelled",
                "duration_secs": elapsed,
                "error": "cancelled by user",
            })
            logger.info("Validation cancelled after %.1fs", elapsed)
        else:
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            logger.error("Validation failed: %s", exc, exc_info=True)
            _validate_state.update({
                "status": "error",
                "duration_secs": elapsed,
                "error": str(exc),
            })
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        logger.error("Validation failed: %s", exc, exc_info=True)
        _validate_state.update({
            "status": "error",
            "duration_secs": elapsed,
            "error": str(exc),
        })


# ValidationRequest imported here (parallel-build concern no longer applies)
from turnaround_validation import ValidationRequest  # noqa: E402

# ---------------------------------------------------------------------------
# Config registry (Unit 1 / D12): maps config_name string to CandidateSourceConfig
# or None (legacy path).  Populated here so the route can resolve config_name
# to an object without the API surface carrying Python objects.
# New configs are registered here as they land (Units 6–8); default is legacy (None).
# ---------------------------------------------------------------------------

def _resolve_candidate_source(config_name: Optional[str]):
    """Return a CandidateSourceConfig for the given name, or None for legacy.

    Raises ValueError with a clear message if the name is not registered —
    surfaces through the F313 error channel at GET /validate/status.
    """
    from turnaround_validation import CandidateSourceConfig  # noqa: local import

    _REGISTERED: dict[str, object] = {
        # "legacy" resolves to None (the default run_filter path)
        "legacy": None,
        # Future configs registered here by their units:
        # "momentum": config_momentum.CONFIG,
        # "deterioration_short": config_deterioration.CONFIG,
    }

    if config_name is None or config_name == "legacy":
        return None

    if config_name not in _REGISTERED:
        registered_names = ", ".join(sorted(_REGISTERED.keys()))
        raise ValueError(
            f"Unknown config_name {config_name!r}. "
            f"Registered configs: {registered_names}."
        )
    return _REGISTERED[config_name]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/turnaround/scan")
async def start_scan(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """Start a full-universe turnaround scan in the background."""
    # REL-01/DI-09: asyncio.Lock makes the check+update atomic
    async with _scan_lock:
        if _scan_state["status"] == "running":
            raise HTTPException(status_code=409, detail="Scan already running")
        _scan_state.update({
            "status": "running",
            "started_at": _utcnow_str(),
            "duration_secs": None,
            "error": None,
            "candidate_count": None,
        })
    background_tasks.add_task(_run_scan_background, request.params, request.max_universe)
    return {"status": "running"}


@router.get("/api/turnaround/scan/status", response_model=ScanStatusResponse)
def scan_status() -> ScanStatusResponse:
    """Poll the current scan state."""
    return ScanStatusResponse(
        status=_scan_state["status"],
        started_at=_scan_state.get("started_at"),
        duration_secs=_scan_state.get("duration_secs"),
        error=_scan_state.get("error"),
        candidate_count=_scan_state.get("candidate_count"),
    )


@router.get("/api/turnaround/watchlist")
def get_watchlist(include_null: bool = Query(default=False, alias="include_null")) -> list[dict]:
    """Read the last persisted watchlist.

    D8: Returns non-null candidates by default.
    ?include_null=true returns everything (signal + null candidates).
    Raises 404 if no scan has been run yet.
    """
    if not _WATCHLIST_PATH.exists():
        raise HTTPException(status_code=404, detail="No watchlist yet — run a scan first")
    try:
        data: list[dict] = json.loads(_WATCHLIST_PATH.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read watchlist: {exc}")
    if not include_null:
        data = [c for c in data if not c.get("is_null_candidate", False)]
    return data


@router.post("/api/turnaround/validate")
async def start_validation(
    request: ValidationRequest,  # PY-07: typed body → FastAPI emits 422 on bad input
    background_tasks: BackgroundTasks,
    config_name: Optional[str] = Query(
        default=None,
        description=(
            "Unit 1 (D12): optional config name. "
            "Default (None or 'legacy') → legacy run_filter path (config #0). "
            "Future: 'momentum', 'deterioration_short', etc. "
            "Unknown names are refused with status='error' at GET /validate/status."
        ),
    ),
) -> dict:
    """Start a historical validation run in the background.

    PY-06/ORCH-01: ValidationRequest is fully typed — invalid body returns 422.
    REL-01/DI-09: asyncio.Lock makes the check+update atomic.
    F313: creates a fresh cancel_event + ValidationProgress for this run.

    Unit 1 (D12): optional config_name selects the candidate source.
    Default (None) → legacy run_filter path unchanged (regression anchor).
    Response shapes are unchanged regardless of config_name.
    """
    import time as _time
    from turnaround_validation import ValidationProgress
    global _validate_cancel_event, _validate_started_at_monotonic, _validate_progress

    # REL-01/DI-09: asyncio.Lock makes the check+update atomic
    async with _validate_lock:
        if _validate_state["status"] == "running":
            raise HTTPException(status_code=409, detail="Validation already running")
        # F313: fresh cancel event + progress tracker for this run
        _validate_cancel_event = threading.Event()
        _validate_progress = ValidationProgress()
        _validate_started_at_monotonic = _time.monotonic()
        _validate_state.update({
            "status": "running",
            "started_at": _utcnow_str(),
            "duration_secs": None,
            "error": None,
        })
    background_tasks.add_task(_run_validate_background, request.model_dump(), config_name)
    return {"status": "running"}


@router.get("/api/turnaround/validate/status")
def validate_status() -> dict:
    """Poll the current validation state.

    F313: adds live duration_secs while running + progress object.
    duration_secs is computed from the monotonic clock while status='running'
    so callers see a live wall-clock counter; at terminal states it reflects
    the total elapsed time recorded by the background task.
    """
    import time as _time

    status = _validate_state["status"]

    # F313: live duration while running
    if status == "running" and _validate_started_at_monotonic is not None:
        live_duration = _time.monotonic() - _validate_started_at_monotonic
    else:
        live_duration = _validate_state.get("duration_secs")

    # F313: snapshot progress (GIL-safe read of individual fields)
    progress_snapshot: Optional[dict] = None
    if _validate_progress is not None:
        p = _validate_progress
        progress_snapshot = {
            "dates_done": p.dates_done,
            "dates_total": p.dates_total,
            "current_date": p.current_date,
            "symbols_loaded": p.symbols_loaded,
            "universe_size": p.universe_size,
            "events_so_far": {
                "signal": p.signal_events,
                "null": p.null_events,
            },
        }

    return {
        "status": status,
        "started_at": _validate_state.get("started_at"),
        "duration_secs": live_duration,
        "error": _validate_state.get("error"),
        "progress": progress_snapshot,
    }


@router.post("/api/turnaround/validate/cancel")
async def cancel_validation() -> dict:
    """Cancel an in-flight validation run.

    F313: sets the cancel_event so the worker thread exits cleanly at the next
    symbol or date boundary. Status transitions to 'cancelled', no result file
    written (cancellation = user intent, not a salvage scenario).
    Returns 409 if no run is in flight.
    """
    async with _validate_lock:
        if _validate_state["status"] != "running":
            raise HTTPException(
                status_code=409,
                detail=f"No validation in flight (status={_validate_state['status']!r})",
            )
        if _validate_cancel_event is not None:
            _validate_cancel_event.set()
    return {"status": "cancelling"}


@router.get("/api/turnaround/validate/result")
def get_validation_result() -> dict:
    """Read the last persisted validation result.

    Raises 404 if no validation has been run yet.

    DI-01: applies a read-time normalization shim so pre-schema_version payloads
    (run-1 artifacts lacking the events/distribution fields) return a complete schema.
    schema_version=0 is the sentinel for "pre-events run" — consumers can gate on it.
    The on-disk artifact is NOT modified.
    """
    if not _VALIDATION_PATH.exists():
        raise HTTPException(status_code=404, detail="No validation result yet — run a validation first")
    try:
        data: dict = json.loads(_VALIDATION_PATH.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read validation result: {exc}")
    # DI-01: backfill missing fields introduced in schema_version=1 so old artifacts
    # serve a complete schema.  Defaults mirror the ValidationResult field defaults.
    _schema_v1_defaults: dict = {
        "schema_version": 0,
        "events": [],
        "null_mean_return_pct": 0.0,
        "null_median_return_pct": 0.0,
        "null_p25_return_pct": 0.0,
        "null_p75_return_pct": 0.0,
        "signal_horizon_mean_return_pct": 0.0,
        "signal_horizon_median_return_pct": 0.0,
        "null_horizon_mean_return_pct": 0.0,
        "null_horizon_median_return_pct": 0.0,
    }
    data = {**_schema_v1_defaults, **data}
    return data
