"""backend/routes/premises.py — FastAPI router for premise workbench (F389).

Endpoints:
  GET    /api/premises                              — list all premises
  POST   /api/premises                              — create premise
  GET    /api/premises/{premise_id}                 — get full premise dict
  PUT    /api/premises/{premise_id}/spec            — save validated spec
  POST   /api/premises/{premise_id}/run             — trigger run (preview|explore)
  GET    /api/premises/{premise_id}/run-status      — poll job status
  GET    /api/premises/{premise_id}/verdict         — read latest verdict
  POST   /api/premises/{premise_id}/graduate-to-confirm — gate: freeze + awaiting_confirm
  DELETE /api/premises/{premise_id}                 — soft-delete (non-confirmed only)

Async pattern: module-level job state + BackgroundTasks, matching turnaround.py.
Research imports happen lazily inside sync workers (turnaround.py pattern).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# G5: premise_id format pattern (mirrors premise_run._PREMISE_ID_RE)
_PREMISE_ID_PATTERN = r"^p-[0-9a-f]{8}$"


# ---------------------------------------------------------------------------
# Request models (G11: explicit Pydantic models for OpenAPI + input validation)
# ---------------------------------------------------------------------------

class CreatePremiseRequest(BaseModel):
    premise_text: str


class RunRequest(BaseModel):
    # G11: Literal enforces valid values at deserialization time (not just a manual check)
    mode: Literal["preview", "explore"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/premises")
async def list_premises():
    """List all premises (id, status, premise_text excerpt, last_updated)."""
    from research.premise_store import PremiseStore

    store = PremiseStore()
    result = []
    for pid, p in store.premises.items():
        text = p.get("premise_text", "")
        excerpt = (text[:120] + "…") if len(text) > 120 else text
        result.append({
            "premise_id": pid,
            "status": p.get("status"),
            "premise_text_excerpt": excerpt,
            "last_updated": p.get("updated_at"),
            "created_at": p.get("created_at"),
        })
    return result


@router.post("/api/premises", status_code=201)
async def create_premise(req: CreatePremiseRequest):
    """Create a new premise in 'draft' state."""
    from research.premise_store import PremiseStore

    if not req.premise_text.strip():
        raise HTTPException(400, "premise_text must be non-empty")

    store = PremiseStore()
    pid = store.add_premise(req.premise_text.strip())
    return {"premise_id": pid, "status": "draft"}


@router.get("/api/premises/{premise_id}")
async def get_premise(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Return the full premise dict (spec, run_history, error_note, etc.)."""
    from research.premise_store import PremiseStore

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")
    return dict(p)


@router.put("/api/premises/{premise_id}/spec")
async def save_spec(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
    spec_dict: dict = ...,
):
    """Validate and attach a PremiseSpec to a premise; transition to spec_ready.

    Body: full PremiseSpec dict (must be a JSON object). Raises 422 if invalid.

    G2: Refused on statuses beyond explored (exploring/awaiting_confirm/confirmed
    → 409). Only draft/awaiting_formalization/spec_ready/explored may accept a
    new spec. Returns the ACTUAL resulting status (not a hardcoded 'spec_ready').
    """
    from research.premise_store import PremiseStore
    from pydantic import ValidationError

    # G11: reject non-dict bodies (bare string, array, etc.)
    if not isinstance(spec_dict, dict):
        raise HTTPException(422, "Spec body must be a JSON object (dict).")

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    # G2: block mutation on statuses that must not accept a new spec
    current_status = p.get("status")
    _IMMUTABLE_STATUSES = {"exploring", "awaiting_confirm", "confirmed"}
    if current_status in _IMMUTABLE_STATUSES:
        raise HTTPException(
            409,
            f"Cannot replace spec on premise {premise_id!r} with status "
            f"{current_status!r}. Spec is frozen at this stage."
        )

    try:
        store.add_spec(premise_id, spec_dict)
    except ValidationError as exc:
        raise HTTPException(422, f"Spec validation failed: {exc}")
    except ValueError as exc:
        # G2: do NOT swallow — propagate store errors as 409
        raise HTTPException(409, str(exc))

    # G2: transition to spec_ready only from states that allow it; return actual status
    p2 = store._get(premise_id)
    current2 = p2.get("status")
    _SPEC_READY_SOURCES = {"draft", "awaiting_formalization", "spec_ready", "explored"}
    if current2 in _SPEC_READY_SOURCES and current2 != "spec_ready":
        try:
            store.transition(premise_id, "spec_ready")
        except ValueError as exc:
            # Log the failure but do NOT silently swallow — G2: return actual status
            logger.warning(
                "save_spec: transition %r → spec_ready refused for %r: %s",
                current2, premise_id, exc,
            )

    # G2: reload to get the ACTUAL status after the transition attempt
    p3 = store._get(premise_id)
    actual_status = p3.get("status")
    return {"premise_id": premise_id, "status": actual_status}


@router.post("/api/premises/{premise_id}/run")
async def trigger_run(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
    req: RunRequest = ...,
):
    """Trigger a run (preview or explore).

    Returns {job_id, status}. Transitions premise to 'exploring'.
    409 if a job is already running or premise status is not runnable.
    400 if premise has no spec.
    """
    from research.premise_store import PremiseStore
    from research.premise_run import run_preview, run_full_explore

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    if p.get("spec") is None:
        raise HTTPException(400, f"Premise {premise_id!r} has no spec. Save a spec first.")

    # G8: status guard is enforced in run_preview/run_full_explore (inside lock),
    # so 409 from there will propagate correctly. Mode is now Literal-validated.
    if req.mode == "preview":
        await run_preview(premise_id)
    else:
        await run_full_explore(premise_id)

    return {"premise_id": premise_id, "status": "running", "run_type": req.mode}


@router.get("/api/premises/{premise_id}/run-status")
async def get_run_status(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Poll job status for the given premise.

    Returns {status, run_type, started_at, finished_at, error, verdict}.
    If no job in memory but premise is 'exploring': server restart detected.
    """
    from research.premise_run import _jobs, poll_explore_status
    from research.premise_store import PremiseStore

    job = _jobs.get(premise_id)

    if job is None:
        # Check if premise is in 'exploring' state (server restart scenario)
        store = PremiseStore()
        try:
            p = store._get(premise_id)
            if p.get("status") == "exploring":
                return {
                    "status": "unknown",
                    "note": "server restarted — check premise state and outdir for the running job",
                }
        except KeyError:
            raise HTTPException(404, f"Premise not found: {premise_id!r}")
        return {"status": "not_found", "note": "No active job for this premise."}

    # For running explore jobs, poll worker status
    if job.get("run_type") == "explore" and job.get("status") == "running":
        job = poll_explore_status(premise_id)

    return {
        "status": job.get("status"),
        "run_type": job.get("run_type"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "verdict": job.get("verdict"),
    }


@router.get("/api/premises/{premise_id}/verdict")
async def get_verdict(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Return the latest verdict from run_history.

    Raises 404 if premise not found. Returns 200 with null verdict if no runs.
    """
    from research.premise_store import PremiseStore

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    run_history = p.get("run_history", [])
    # Find the latest run with a verdict
    for run in reversed(run_history):
        if run.get("verdict") is not None:
            return {
                "premise_id": premise_id,
                "run_type": run.get("run_type"),
                "verdict_valid": run.get("verdict_valid", False),
                "verdict": run.get("verdict"),
                "study_name": run.get("study_name"),
            }

    return {"premise_id": premise_id, "verdict": None, "note": "No completed runs yet."}


@router.post("/api/premises/{premise_id}/graduate-to-confirm")
async def graduate_to_confirm_endpoint(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Gate: power_audit → freeze spec_hash → awaiting_confirm.

    OVERRIDE (decisions.md): Does NOT run any backtest. Does NOT append to
    fdr_ledger.json. Terminal reachable state in v1 is awaiting_confirm.
    confirmed is only reachable by F393.

    Raises:
      404 — premise not found
      409 — status != explored, duplicate hash, already has confirm_request
      400 — power audit underpowered
    """
    from research.premise_run import graduate_to_confirm

    return await graduate_to_confirm(premise_id)


@router.delete("/api/premises/{premise_id}")
async def delete_premise(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Soft-delete a premise (only non-confirmed, non-active).

    Sets status to 'draft', clears spec. Refused on confirmed premises or
    if an active job is in flight (G10).
    """
    from research.premise_store import PremiseStore
    from research.premise_run import _jobs

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    if p.get("status") == "confirmed":
        raise HTTPException(
            409,
            f"Cannot delete confirmed premise {premise_id!r}. "
            "Confirmed is terminal (FDR ledger appended)."
        )

    # G10: refuse delete if an active job is in flight
    active_job = _jobs.get(premise_id, {})
    if active_job.get("status") == "running":
        raise HTTPException(
            409,
            f"Cannot delete premise {premise_id!r}: a run is in progress. "
            "Wait for the run to complete or fail first."
        )

    # Soft-delete: clear spec and revert to draft
    import copy
    prior = copy.deepcopy(store.premises)
    p["spec"] = None
    p["error_note"] = "soft-deleted"
    try:
        # Force-write the draft status (bypass transition validation)
        p["status"] = "draft"
        from datetime import datetime, timezone
        p["updated_at"] = datetime.now(timezone.utc).isoformat()
        store.save()
    except Exception as exc:
        store.premises = prior
        raise HTTPException(500, f"Failed to delete premise: {exc}")

    return {"premise_id": premise_id, "status": "draft", "note": "Soft-deleted."}
