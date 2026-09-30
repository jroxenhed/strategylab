"""Graph routes: named graphs stored on the server (F435 W1 item 1.B, decision D2).

    GET    /api/graphs              -> 200 {"graphs": [GraphListItem]}
    GET    /api/graphs/{id}         -> 200 GraphEnvelope | 404
    POST   /api/graphs              -> 201 GraphEnvelope (rev 1)
    PUT    /api/graphs/{id}         -> 200 GraphEnvelope (rev+1) | 409
    DELETE /api/graphs/{id}?rev=N   -> 204 | 409
    POST   /api/graphs/seed         -> 200 {"imported": [id], "skipped": [{name, reason}]}

Errors:
  - 409 {"detail": {"code": "rev_conflict", "current_rev": N}} on a stale rev.
  - 409 {"detail": {"code": "name_taken"}} when another graph has the name.
  - 409 {"detail": {"code": "graph_corrupt", "detail": <sentence>}} when the
    stored file cannot be read. That is the file's fault, never the body's.
    DELETE of such a file moves it aside (``<id>.json.corrupt``) and is a 204.
  - 400 {"detail", "node_id", "code", "diagnostics"} (plan section 4.4) when
    the graph does not parse. Item 1.C fills in the full diagnostics list.
  - 413 when the body is over 2 MB. 422 for a badly shaped body.

Bodies are read by hand so the 2 MB limit is checked before JSON parsing, and
all file work and graph parsing run in the thread pool, never on the event loop.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool

from nodebuilder.diagnostics import error_body, validate_graph_data
from nodebuilder.models import GraphValidationError
from nodebuilder.storage import (
    GraphCorruptError,
    GraphNotFoundError,
    InvalidNameError,
    NameTakenError,
    RevConflictError,
    get_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graphs", tags=["graphs"])

MAX_BODY_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


class CreateGraphBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    graph: Optional[dict[str, Any]] = None
    duplicate_of: Optional[str] = None


class SaveGraphBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rev: int
    name: Optional[str] = None
    description: Optional[str] = None
    graph: dict[str, Any]


class SeedBody(BaseModel):
    legacy: Any = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_body(request: Request, model: type[BaseModel]) -> BaseModel:
    """Read the raw body, enforce the 2 MB limit, then parse it into ``model``."""
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413, detail=f"Request body too large (max {MAX_BODY_BYTES} bytes)"
        )
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body",), "msg": "Body is not valid JSON", "input": None}]
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        # Same 422 shape FastAPI gives for a badly shaped body.
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        )


def _graph_error_sync(exc: Exception, graph_data: Any) -> JSONResponse:
    """400 in the plan section 4.4 shape for a graph that does not parse.

    ``detail``, ``node_id`` and ``code`` describe the error that stopped the
    save; ``diagnostics`` lists every problem the graph has (the same check
    /api/nodebuilder/validate runs, so no market data is fetched).
    """
    diagnostics = validate_graph_data(graph_data) if isinstance(graph_data, dict) else []
    return JSONResponse(status_code=400, content=error_body(exc, diagnostics))


async def _graph_error(exc: Exception, graph_data: Any) -> JSONResponse:
    # Checking a big graph is CPU work: keep it off the event loop.
    return await run_in_threadpool(_graph_error_sync, exc, graph_data)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Graph not found")


def _rev_conflict(exc: RevConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"detail": {"code": "rev_conflict", "current_rev": exc.current_rev}}
    )


def _name_taken() -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": {"code": "name_taken"}})


def _graph_corrupt(exc: GraphCorruptError) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"detail": {"code": "graph_corrupt", "detail": str(exc)}}
    )


def _bad_name(exc: InvalidNameError) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", "name"), "msg": str(exc), "input": None}]
    )


# Errors a graph that does not parse can raise from Graph.model_validate.
# GraphCorruptError is not one of them (it is not a ValueError), and every
# route catches it first anyway.
_PARSE_ERRORS = (GraphValidationError, ValidationError, ValueError, TypeError)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_graphs():
    items = await run_in_threadpool(get_store().list)
    return {"graphs": items}


# /seed is declared before /{graph_id} routes; it only has a POST, and the
# /{graph_id} routes are GET, PUT and DELETE, so they never clash anyway.
@router.post("/seed")
async def seed_graphs(request: Request):
    body = await _read_body(request, SeedBody)
    return await run_in_threadpool(get_store().seed, body.legacy)


@router.get("/{graph_id}")
async def get_graph(graph_id: str):
    try:
        return await run_in_threadpool(get_store().get, graph_id)
    except GraphNotFoundError:
        raise _not_found()
    except GraphCorruptError as exc:
        return _graph_corrupt(exc)


@router.post("", status_code=201)
async def create_graph(request: Request):
    body = await _read_body(request, CreateGraphBody)
    if body.graph is not None and body.duplicate_of is not None:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body",), "msg": "Give graph or duplicate_of, not both", "input": None}]
        )
    try:
        env = await run_in_threadpool(
            get_store().create,
            body.name,
            body.graph,
            body.description,
            body.duplicate_of,
        )
    except GraphNotFoundError:
        raise _not_found()
    except GraphCorruptError as exc:
        return _graph_corrupt(exc)
    except InvalidNameError as exc:
        raise _bad_name(exc)
    except NameTakenError:
        return _name_taken()
    except _PARSE_ERRORS as exc:
        return await _graph_error(exc, body.graph)
    return JSONResponse(status_code=201, content=env)


@router.put("/{graph_id}")
async def save_graph(graph_id: str, request: Request):
    body = await _read_body(request, SaveGraphBody)
    try:
        return await run_in_threadpool(
            get_store().update,
            graph_id,
            body.rev,
            body.graph,
            body.name,
            body.description,
        )
    except GraphNotFoundError:
        raise _not_found()
    except GraphCorruptError as exc:
        return _graph_corrupt(exc)
    except RevConflictError as exc:
        return _rev_conflict(exc)
    except InvalidNameError as exc:
        raise _bad_name(exc)
    except NameTakenError:
        return _name_taken()
    except _PARSE_ERRORS as exc:
        return await _graph_error(exc, body.graph)


@router.delete("/{graph_id}", status_code=204)
async def delete_graph(graph_id: str, rev: int):
    try:
        await run_in_threadpool(get_store().delete, graph_id, rev)
    except GraphNotFoundError:
        raise _not_found()
    except GraphCorruptError as exc:
        return _graph_corrupt(exc)
    except RevConflictError as exc:
        return _rev_conflict(exc)
    return Response(status_code=204)
