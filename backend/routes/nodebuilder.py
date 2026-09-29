"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b

Graph errors return HTTP 400 with {"detail": <message>, "node_id": <id or null>}
so the editor can show the message and highlight the node at fault.
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
from nodebuilder.from_rules import auto_render
from nodebuilder.models import GraphValidationError

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

def _graph_error(message: str, node_id: str | None) -> JSONResponse:
    """The 400 body the editor reads: the message plus the node to highlight."""
    return JSONResponse(status_code=400, content={"detail": message, "node_id": node_id})


@router.post(
    "/backtest",
    response_model=GraphBacktestResponse,
    responses={400: {"description": "Graph error: {detail, node_id}"}},
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
    try:
        req = GraphBacktestRequest.model_validate(payload)
    except GraphValidationError as exc:
        return _graph_error(str(exc), exc.node_id)
    except ValidationError as exc:
        # Any other bad field keeps FastAPI's usual 422 shape.
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        )

    try:
        return run_graph_backtest(req)
    except GraphValidationError as exc:
        return _graph_error(str(exc), exc.node_id)
    except ValueError as exc:
        return _graph_error(str(exc), None)
    except HTTPException as exc:
        # e.g. "Invalid source": keep one 400 shape for the editor.
        if exc.status_code == 400:
            return _graph_error(str(exc.detail), None)
        raise
    except Exception:
        logger.exception("/api/nodebuilder/backtest failed")
        raise HTTPException(status_code=500, detail="graph backtest failed")
