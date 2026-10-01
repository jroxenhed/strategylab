"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b
POST /api/nodebuilder/validate     — W1 item 1.C

Graph errors return HTTP 400 in the plan 4.4 shape:
{"detail": <message>, "node_id": <id or null>, "code": <diagnostic code>,
 "diagnostics": [Diagnostic]}, so the editor can show the message, badge the
node at fault and every other problem the graph has.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from models import StrategyRequest
from nodebuilder.api_models import AutoRenderResponse, GraphBacktestRequest, GraphBacktestResponse
from nodebuilder.diagnostics import error_body, has_errors, validate_graph_data, validate_graph_full
from nodebuilder.from_rules import auto_render
from nodebuilder.models import STREAM_SCHEMA_VERSION, GraphValidationError

# The backtest core moved to nodebuilder/run.py so bot code can call it
# without importing a routes module.  These names are re-exported for the
# existing tests and callers that import them from here.
from nodebuilder.run import (  # noqa: F401
    _apply_settings_overrides,
    _build_baseline_curve,
    _make_cached_eval,
    _settings_to_strategy_request,
    run_graph_backtest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nodebuilder", tags=["nodebuilder"])


@router.post("/auto_render", response_model=AutoRenderResponse, response_model_by_alias=True)
def post_auto_render(req: StrategyRequest) -> AutoRenderResponse:
    """Translate a StrategyRequest into a read-only Graph for the T1 viewer."""
    graph = auto_render(req)
    return AutoRenderResponse(graph=graph)


# ---------------------------------------------------------------------------
# POST /api/nodebuilder/backtest  — Unit 8b
# ---------------------------------------------------------------------------

def _graph_error(exc: BaseException, graph_data: Any) -> JSONResponse:
    """The 400 body the editor reads (plan 4.4): the error that stopped the
    request on top, plus every diagnostic the graph has.

    The full list comes from the same check /validate runs, so it never
    fetches data.  Called from sync routes, which FastAPI runs off the event
    loop.
    """
    diagnostics = validate_graph_data(graph_data) if isinstance(graph_data, dict) else []
    return JSONResponse(status_code=400, content=error_body(exc, diagnostics))


@router.post("/validate")
def post_validate(payload: dict[str, Any] = Body(...)):
    """Check a graph without running it.

    Returns {"ok", "diagnostics", "streams", "stream_schema"}.  ok is False
    when any diagnostic is an error.  streams maps each node id to the
    StreamSchema of that node's OUTPUT (plan 3.3 form); the editor derives a
    node's input stream from its upstream outputs and the wires.  A node
    missing from streams could not be checked (it has an error, or reads
    from a node that has one).  stream_schema is the stream format version.

    Never fetches market data and never runs a backtest.  A graph that does
    not parse is still a 200 here, with graph_invalid (or the matching code)
    in the list and no streams; only a body without a "graph" key is a 422.
    """
    if "graph" not in payload:
        raise RequestValidationError(
            [{"type": "missing", "loc": ("body", "graph"), "msg": "Field required", "input": payload}]
        )
    result = validate_graph_full(payload["graph"])
    return {
        "ok": not has_errors(result.diagnostics),
        "diagnostics": [d.model_dump() for d in result.diagnostics],
        "streams": result.streams,
        "stream_schema": STREAM_SCHEMA_VERSION,
    }


@router.post(
    "/backtest",
    response_model=GraphBacktestResponse,
    responses={400: {"description": "Graph error: {detail, node_id, code, diagnostics}"}},
)
def post_graph_backtest(payload: dict[str, Any] = Body(...)):
    """Run a backtest using a compiled node graph.

    Returns {summary, trades, equity_curve, baseline_curve}.  The summary also
    carries open_position and exit_connected (graph backtest only).
    Rule-only debug fields (signal_trace, rule_signals, ema_overlays, regime_series)
    are intentionally absent from the graph backtest response.

    The body is parsed here rather than by FastAPI, because a cycle or a
    dangling wire is raised while the Graph model is being built.  Parsed by
    FastAPI, those surfaced as a bare 500.
    """
    graph_data = payload.get("graph")
    try:
        req = GraphBacktestRequest.model_validate(payload)
    except GraphValidationError as exc:
        return _graph_error(exc, graph_data)
    except ValidationError as exc:
        errors = exc.errors()
        if isinstance(graph_data, dict) and errors and all(
            tuple(err.get("loc", ()))[:1] == ("graph",) for err in errors
        ):
            # A graph that is there but badly typed (a node position "abc",
            # 33 meta keys): the same 400 as /api/graphs gives for it.
            return _graph_error(exc, graph_data)
        # Any other bad field (ticker, dates, a missing graph) keeps
        # FastAPI's usual 422 shape.
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in errors]
        )

    try:
        return run_graph_backtest(req)
    except GraphValidationError as exc:
        return _graph_error(exc, graph_data)
    except ValueError as exc:
        # Not a graph problem (no data, say): code request_invalid.
        return _graph_error(exc, graph_data)
    except HTTPException as exc:
        # e.g. "Invalid source": keep one 400 shape for the editor.
        if exc.status_code == 400:
            return _graph_error(ValueError(str(exc.detail)), graph_data)
        raise
    except Exception:
        logger.exception("/api/nodebuilder/backtest failed")
        raise HTTPException(status_code=500, detail="graph backtest failed")
