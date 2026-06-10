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

import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# G5: premise_id format pattern (mirrors premise_run._PREMISE_ID_RE)
_PREMISE_ID_PATTERN = r"^p-[0-9a-f]{8}$"


# ---------------------------------------------------------------------------
# Request models (G11: explicit Pydantic models for OpenAPI + input validation)
# ---------------------------------------------------------------------------

class CreatePremiseRequest(BaseModel):
    # SEC-05: max_length bounds input and prevents DoS via MB-sized strings in the
    # store (store.load() re-reads premises.json on every endpoint call).
    # 4000 chars matches PremiseSpec.premise_text; generous for a research premise.
    premise_text: str = Field(..., max_length=4000)


class RunRequest(BaseModel):
    # G11: Literal enforces valid values at deserialization time (not just a manual check)
    mode: Literal["preview", "explore"]


class DispositionRequest(BaseModel):
    """F397: set user disposition on a premise."""
    # F398: static Literal type alias — enforces valid values at deserialization
    disposition: Literal["active", "parked_needs_data", "parked_sharpen", "rejected", "promising"]
    note: str = Field("", max_length=2000)


class DeriveRequest(BaseModel):
    """F417: derive a descendant premise with optional spec overrides."""
    # SEC-D: cap key count to prevent large error messages from interpolating
    # thousands of keys into the response body (BodySizeLimitMiddleware caps
    # the request to 1 MB, but bounding key count here is cheaper and clearer).
    spec_overrides: dict = Field(default_factory=dict, max_length=50)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_VALID_STATUSES = {
    "draft", "awaiting_formalization", "spec_ready",
    "exploring", "explored", "awaiting_confirm", "confirmed",
}

# F397: import at module level so it's available in endpoint bodies
from research.premise_store import _VALID_DISPOSITIONS  # noqa: E402


@router.get("/api/premises")
async def list_premises(
    status: Optional[str] = Query(
        None,
        description=(
            "H6: Optional status filter — e.g. 'awaiting_formalization' lets the "
            "agent-operator poll its own queue. 422 on unknown status."
        ),
    ),
    disposition: Optional[str] = Query(
        None,
        description=(
            "F397: Optional disposition filter. 422 on unknown disposition."
        ),
    ),
):
    """List all premises (id, status, premise_text excerpt, last_updated).

    Optional ?status= query param: filters returned list to premises with that
    exact status. Raises 422 if the status value is not a known PremiseStatus.

    Optional ?disposition= query param (F397): filters by user disposition.
    """
    from research.premise_store import PremiseStore, derive_machine_outcome

    # H6: validate status param before touching store
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            422,
            f"Unknown status {status!r}. Valid values: {sorted(_VALID_STATUSES)}",
        )
    # F397: validate disposition param
    if disposition is not None and disposition not in _VALID_DISPOSITIONS:
        raise HTTPException(
            422,
            f"Unknown disposition {disposition!r}. Valid values: {sorted(_VALID_DISPOSITIONS)}",
        )

    store = PremiseStore()
    result = []
    for pid, p in store.premises.items():
        p_status = p.get("status")
        p_disposition = p.get("disposition", "active")
        # H6: apply status filter when provided
        if status is not None and p_status != status:
            continue
        # F397: apply disposition filter when provided
        if disposition is not None and p_disposition != disposition:
            continue
        text = p.get("premise_text", "")
        excerpt = (text[:120] + "…") if len(text) > 120 else text
        result.append({
            "premise_id": pid,
            "status": p_status,
            "premise_text_excerpt": excerpt,
            "last_updated": p.get("updated_at"),
            "created_at": p.get("created_at"),
            # F397: new fields
            "disposition": p_disposition,
            "derived_from": p.get("derived_from"),
            "machine_outcome": derive_machine_outcome(p),
        })
    # F397: sort most-recently-updated first
    result.sort(key=lambda x: x.get("last_updated") or "", reverse=True)
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


@router.post("/api/premises/{premise_id}/submit")
async def submit_premise(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Submit a draft premise for formalization (draft → awaiting_formalization).

    Called when the user sends their idea to the agent-operator queue.
    409 if status is not 'draft'.
    Returns {premise_id, status}.
    """
    from research.premise_store import PremiseStore

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    current_status = p.get("status")
    if current_status != "draft":
        raise HTTPException(
            409,
            f"Cannot submit premise {premise_id!r}: status is {current_status!r}, "
            f"expected 'draft'."
        )

    try:
        store.transition(premise_id, "awaiting_formalization")
    except ValueError as exc:
        raise HTTPException(409, str(exc))

    return {"premise_id": premise_id, "status": "awaiting_formalization"}


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

    # B2: auto-advance through LEGAL intermediate edges to reach spec_ready.
    # State machine does NOT have a direct draft→spec_ready edge; must hop via
    # awaiting_formalization. Each hop uses a legal transition() call so the
    # machine invariant is preserved.
    #
    # Allowed start states and their paths:
    #   draft                → awaiting_formalization → spec_ready  (2 hops)
    #   awaiting_formalization → spec_ready                          (1 hop)
    #   spec_ready           → (already there, no-op)
    #   explored             → spec_ready                           (1 hop)
    # G2: block is still in effect above for exploring/awaiting_confirm/confirmed.
    p2 = store._get(premise_id)
    current2 = p2.get("status")
    _SPEC_READY_SOURCES = {"draft", "awaiting_formalization", "spec_ready", "explored"}
    if current2 in _SPEC_READY_SOURCES and current2 != "spec_ready":
        try:
            # If starting from draft, must hop via awaiting_formalization first
            if current2 == "draft":
                store.transition(premise_id, "awaiting_formalization")
            # Now advance to spec_ready (works from awaiting_formalization or explored)
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
        # R-1/R-10: no in-memory job — fall back to store-derived status so pollers
        # never see 'not_found' for a premise that has run history.
        store = PremiseStore()
        try:
            p = store._get(premise_id)
        except KeyError:
            raise HTTPException(404, f"Premise not found: {premise_id!r}")

        p_status = p.get("status")
        if p_status == "exploring":
            return {
                "status": "unknown",
                "note": "server restarted — check premise state and outdir for the running job",
            }

        # Synthesise terminal status from the latest run_history entry (if any)
        run_history = p.get("run_history", [])
        latest_run = next(
            (r for r in reversed(run_history)
             if isinstance(r, dict) and r.get("run_type") in ("preview", "explore")),
            None,
        )
        if latest_run is not None:
            # Derive a terminal status from the stored entry
            verdict_valid = latest_run.get("verdict_valid", False)
            synthesised_status = "done" if verdict_valid else "failed"
            return {
                "status": synthesised_status,
                "run_type": latest_run.get("run_type"),
                "started_at": latest_run.get("started_at"),
                "finished_at": latest_run.get("finished_at"),
                "error": latest_run.get("error"),
                "verdict": latest_run.get("verdict"),
                "note": "synthesised from run_history (job already cleaned up)",
            }
        return {"status": "not_found", "note": "No active job or run history for this premise."}

    # For running explore jobs, poll worker status
    # F394: wrap sync SSH poll in asyncio.to_thread — subprocess.run with 30s timeout
    # blocks the event loop thread if called directly (CLAUDE.md Key Bugs Fixed pattern).
    if job.get("run_type") == "explore" and job.get("status") == "running":
        job = await asyncio.to_thread(poll_explore_status, premise_id)

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


@router.get("/api/premises/{premise_id}/autopsy")
async def get_autopsy(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Return verdict autopsy for the latest valid explore run (F417).

    Raises 404 if premise not found or no valid explore run yet.
    Returns AutopsyResult as dict.
    """
    import dataclasses
    from research.premise_store import PremiseStore
    from research.premise_autopsy import build_autopsy

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    run_history = p.get("run_history", [])
    latest_explore = next(
        (r for r in reversed(run_history)
         if r.get("run_type") == "explore" and r.get("verdict_valid") and r.get("output_dir")),
        None,
    )
    if latest_explore is None:
        raise HTTPException(
            404,
            f"No valid explore run found for premise {premise_id!r}. Run a full explore first."
        )

    from pathlib import Path as _Path
    from research.premise_run import _STUDIES_DIR as _STUDIES_DIR_REF
    study_dir = _Path(latest_explore["output_dir"])
    # SEC-01: containment check — output_dir must be within _STUDIES_DIR
    try:
        if not study_dir.resolve().is_relative_to(_STUDIES_DIR_REF.resolve()):
            logger.warning(
                "SEC-01: output_dir %r for premise %r is outside studies dir — rejecting",
                str(study_dir), premise_id,
            )
            raise HTTPException(404, f"No valid explore run found for premise {premise_id!r}.")
    except (ValueError, OSError) as _cpath_exc:
        logger.warning(
            "SEC-01: cannot resolve output_dir %r for premise %r: %s",
            str(study_dir), premise_id, _cpath_exc,
        )
        raise HTTPException(404, f"No valid explore run found for premise {premise_id!r}.")
    verdict = latest_explore.get("verdict", {})
    spec = p.get("spec") or {}
    cached_census = latest_explore.get("census")

    result = await asyncio.to_thread(
        build_autopsy, premise_id, study_dir, verdict, spec, cached_census
    )
    return dataclasses.asdict(result)


@router.post("/api/premises/{premise_id}/derive", status_code=201)
async def derive_premise(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
    req: DeriveRequest = ...,
):
    """Create a derived premise (descendant) via duplicate_premise + spec overrides (F417).

    - Clones premise_text + spec from source; sets derived_from.
    - Applies spec_overrides via PremiseSpec validation (422 on invalid overrides).
    - Does NOT auto-run anything; descendant starts at spec_ready (or draft if no valid spec).
    - Circularity caveat is AUTO-APPENDED to premise_text server-side — never optional.

    Raises:
      404 — source premise not found
      422 — spec_overrides produce invalid PremiseSpec
    Returns {premise_id, derived_from, status}.
    """
    from research.premise_store import PremiseStore
    from research.premise_spec import PremiseSpec
    from research.premise_autopsy import _CIRCULARITY_CAVEAT

    store = PremiseStore()
    try:
        store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    # 1. Duplicate (clone premise_text + spec, set derived_from)
    new_id = store.duplicate_premise(premise_id)

    # Helper: delete the orphan clone and raise the original exception.
    # DI-03/R-7: any failure after duplicate_premise must clean up the orphan
    # so no phantom draft premise leaks into premises.json.
    def _rollback_and_raise(exc: Exception, *, http_status: int = 422) -> None:
        """Pop the orphan clone from the store and re-raise as HTTPException."""
        store.premises.pop(new_id, None)
        try:
            store.save()
        except Exception as save_exc:
            logger.error(
                "derive_premise: rollback save failed for orphan %s: %s",
                new_id, save_exc,
            )
        raise HTTPException(http_status, str(exc))

    # 2. Apply spec_overrides (if any), validate via PremiseSpec
    if req.spec_overrides:
        from research.premise_spec import _STRUCTURAL_FIELDS as _SPEC_STRUCTURAL_FIELDS
        # SEC-03/C-06: whitelist — only structural fields may be overridden.
        # Non-structural fields (premise_text, plain_summary, guided, spec_hash)
        # must never be injected by callers; they are provenance/prose only.
        _disallowed = {k for k in req.spec_overrides if k not in _SPEC_STRUCTURAL_FIELDS}
        if _disallowed:
            _rollback_and_raise(
                Exception(
                    f"spec_overrides contains non-structural fields that cannot be "
                    f"overridden: {sorted(_disallowed)}. Only structural spec fields "
                    f"are allowed: {sorted(_SPEC_STRUCTURAL_FIELDS)}."
                )
            )
        new_p = store._get(new_id)
        base_spec = dict(new_p.get("spec") or {})
        base_spec.update(req.spec_overrides)
        base_spec.pop("spec_hash", None)
        try:
            validated = PremiseSpec(**base_spec)
        except Exception as exc:
            # DI-03/R-7: rollback orphan on validation failure
            _rollback_and_raise(Exception(f"spec_overrides invalid: {exc}"))

        # R-8: always run add_spec so the clone never carries the parent's
        # stale spec_hash; add_spec strips spec_hash (it's excluded from model_dump
        # unless frozen — but always safe to pop before storing).
        spec_dict_to_store = validated.model_dump()
        spec_dict_to_store.pop("spec_hash", None)
        try:
            store.add_spec(new_id, spec_dict_to_store)
        except Exception as exc:
            _rollback_and_raise(Exception(f"derive_premise add_spec failed: {exc}"), http_status=500)

    # R-8 (no-override path): spec_hash is already cleared in duplicate_premise
    # (premise_store.py) so no extra add_spec call is needed here.

    # 3. Append circularity caveat to premise_text (non-optional, auto-appended server-side)
    derived_p = store._get(new_id)
    prior_text = derived_p.get("premise_text", "")
    circularity_block = (
        f"\n\n[CIRCULARITY CAVEAT — AUTO-APPENDED] This premise is derived from "
        f"{premise_id} and is hypothesis-mined from the same explore data. "
        "Per program charter, a confirm run requires explicit FDR ledger entry and "
        "acknowledgment of the circularity obligation (F393). "
        f"Charter text: {_CIRCULARITY_CAVEAT}"
    )
    derived_p["premise_text"] = prior_text + circularity_block
    from datetime import datetime, timezone
    derived_p["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        store.save()
    except Exception as exc:
        _rollback_and_raise(Exception(f"derive_premise circularity save failed: {exc}"), http_status=500)

    # 4. Transition to spec_ready if spec is present
    new_p2 = store._get(new_id)
    if new_p2.get("spec"):
        try:
            store.transition(new_id, "spec_ready")
        except Exception as te:
            # DI-05: log at ERROR (not silently swallowed); non-fatal — stays draft
            logger.error(
                "derive_premise: transition %s to spec_ready failed: %s", new_id, te
            )

    return {
        "premise_id": new_id,
        "derived_from": premise_id,
        "status": store._get(new_id).get("status"),
    }


@router.post("/api/premises/{premise_id}/reset-stuck-run")
async def reset_stuck_run(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Reset a stuck 'exploring' premise back to 'spec_ready' (F394).

    Only allowed when:
    - premise status == 'exploring'
    - No active in-memory job for the premise

    Returns {premise_id, status, note}.
    """
    from research.premise_store import PremiseStore
    from research.premise_run import _jobs, _get_job_lock

    store = PremiseStore()
    try:
        p = store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    current_status = p.get("status")
    if current_status != "exploring":
        raise HTTPException(
            409,
            f"Cannot reset premise {premise_id!r}: status is {current_status!r}, "
            "expected 'exploring'."
        )

    # SEC-02: hold the per-premise job lock across the check+transition to prevent
    # TOCTOU race where a concurrent run trigger acquires the lock and transitions
    # to 'exploring' between our status check and our store.transition call.
    lock = _get_job_lock(premise_id)
    async with lock:
        # Guard: refuse reset if a live job exists in memory
        active_job = _jobs.get(premise_id, {})
        if active_job.get("status") == "running":
            raise HTTPException(
                409,
                f"Cannot reset premise {premise_id!r}: an active job is in flight. "
                "Wait for the job to complete or fail before resetting."
            )

        try:
            store.set_error_note(premise_id, "manually reset from 'exploring' by operator")
            store.transition(premise_id, "spec_ready")
        except Exception as exc:
            raise HTTPException(500, f"Failed to reset premise {premise_id!r}: {exc}")

    return {
        "premise_id": premise_id,
        "status": "spec_ready",
        "note": "manually reset from exploring",
    }


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


@router.put("/api/premises/{premise_id}/disposition")
async def set_disposition(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
    req: DispositionRequest = ...,
):
    """Set user disposition + optional note on a premise (F397).

    Allowed in any state (disposition is metadata, not a run state).
    422 if disposition not in the valid set.
    Returns {premise_id, disposition}.
    """
    from research.premise_store import PremiseStore

    store = PremiseStore()
    try:
        store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    try:
        store.set_disposition(premise_id, req.disposition, req.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    # H1: echo the saved note so callers can confirm what was stored
    p_after = store._get(premise_id)
    return {"premise_id": premise_id, "disposition": req.disposition, "note": p_after.get("disposition_note", "")}


@router.post("/api/premises/{premise_id}/duplicate", status_code=201)
async def duplicate_premise(
    premise_id: str = Path(..., pattern=_PREMISE_ID_PATTERN),
):
    """Clone premise_text + spec into a new draft, setting derived_from (F397).

    Original premise is untouched.
    Returns {premise_id: <new>, derived_from: <original>}.
    """
    from research.premise_store import PremiseStore

    store = PremiseStore()
    try:
        store._get(premise_id)
    except KeyError:
        raise HTTPException(404, f"Premise not found: {premise_id!r}")

    new_pid = store.duplicate_premise(premise_id)
    return {"premise_id": new_pid, "derived_from": premise_id}
