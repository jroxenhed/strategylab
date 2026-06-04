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

def _run_validate_sync(req_dict: dict) -> dict:
    """Synchronous validation worker — runs in asyncio.to_thread()."""
    import dataclasses as dc
    # Late import — lane C may not exist during parallel build
    from turnaround_validation import ValidationRequest, run_validation

    req = ValidationRequest(**req_dict)
    result = run_validation(req)
    return dc.asdict(result)


async def _run_validate_background(req_dict: dict) -> None:
    """Async background function: calls _run_validate_sync in a thread.

    REL-02: try/finally guarantees ANY exit path (including CancelledError) sets
    a terminal status — status never stays 'running' after the worker exits.
    """
    started = datetime.now(timezone.utc)
    try:
        result_dict = await asyncio.to_thread(_run_validate_sync, req_dict)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _ensure_data_dir()
        atomic_write_text(_VALIDATION_PATH, json.dumps(result_dict, cls=_DateEncoder))
        _validate_state.update({
            "status": "done",
            "duration_secs": elapsed,
            "error": None,
        })
        logger.info("Validation done in %.1fs", elapsed)
    except asyncio.CancelledError:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _validate_state.update({"status": "cancelled", "duration_secs": elapsed, "error": "cancelled"})
        raise
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
) -> dict:
    """Start a historical validation run in the background.

    PY-06/ORCH-01: ValidationRequest is fully typed — invalid body returns 422.
    REL-01/DI-09: asyncio.Lock makes the check+update atomic.
    """
    # REL-01/DI-09: asyncio.Lock makes the check+update atomic
    async with _validate_lock:
        if _validate_state["status"] == "running":
            raise HTTPException(status_code=409, detail="Validation already running")
        _validate_state.update({
            "status": "running",
            "started_at": _utcnow_str(),
            "duration_secs": None,
            "error": None,
        })
    background_tasks.add_task(_run_validate_background, request.model_dump())
    return {"status": "running"}


@router.get("/api/turnaround/validate/status")
def validate_status() -> dict:
    """Poll the current validation state."""
    return {
        "status": _validate_state["status"],
        "started_at": _validate_state.get("started_at"),
        "duration_secs": _validate_state.get("duration_secs"),
        "error": _validate_state.get("error"),
    }


@router.get("/api/turnaround/validate/result")
def get_validation_result() -> dict:
    """Read the last persisted validation result.

    Raises 404 if no validation has been run yet.
    """
    if not _VALIDATION_PATH.exists():
        raise HTTPException(status_code=404, detail="No validation result yet — run a validation first")
    try:
        data: dict = json.loads(_VALIDATION_PATH.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read validation result: {exc}")
    return data
