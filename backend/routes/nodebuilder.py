"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b
POST /api/nodebuilder/validate     — W1 item 1.C
POST /api/nodebuilder/inspect      — W4 item 4.A (the wire inspector, plan D6)
POST /api/nodebuilder/preview      — W4 item 4.A (node sparklines, plan D6)

Graph errors return HTTP 400 in the plan 4.4 shape:
{"detail": <message>, "node_id": <id or null>, "code": <diagnostic code>,
 "diagnostics": [Diagnostic]}, so the editor can show the message, badge the
node at fault and every other problem the graph has.
"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple

from fastapi import APIRouter, Body, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from models import StrategyRequest
from nodebuilder import cook_cache
from nodebuilder.api_models import (
    AutoRenderResponse,
    GraphBacktestRequest,
    GraphBacktestResponse,
    GraphBacktestRouteResponse,
    InspectRequest,
    InspectResponse,
    PreviewRequest,
    PreviewResponse,
)
from nodebuilder.diagnostics import error_body, has_errors, validate_graph_data, validate_graph_full
from nodebuilder.from_rules import auto_render
from nodebuilder.models import STREAM_SCHEMA_VERSION, Graph, GraphValidationError

# The backtest core moved to nodebuilder/run.py so bot code can call it
# without importing a routes module.  These names are re-exported for the
# existing tests and callers that import them from here.
from nodebuilder.run import (  # noqa: F401
    _apply_settings_overrides,
    _build_baseline_curve,
    _make_cached_eval,
    _settings_to_strategy_request,
    cook_graph_window,
    run_graph_backtest,
    run_graph_backtest_cooked,
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
    response_model=GraphBacktestRouteResponse,
    responses={400: {"description": "Graph error: {detail, node_id, code, diagnostics}"}},
)
def post_graph_backtest(payload: dict[str, Any] = Body(...)):
    """Run a backtest using a compiled node graph.

    Returns {summary, trades, equity_curve, baseline_curve, cook_id}.
    cook_id names this run's cook in the cook cache (/inspect, /preview),
    or is null when the cook was too big to keep.  The summary also
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
        response, cook = run_graph_backtest_cooked(req)
        return _with_cook_id(response, _cache_backtest_cook(req, cook))
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


# ---------------------------------------------------------------------------
# The cook cache: the backtest's cook, /inspect and /preview (plan D6)
# ---------------------------------------------------------------------------

def _cache_backtest_cook(req: GraphBacktestRequest, cook) -> str | None:
    """Keep the backtest's cook in the cook cache and return its id.

    Only references are stored (O(1)).  A failure here never fails the
    backtest: the response then has no cook_id and the inspector cooks
    again from the graph.
    """
    try:
        window = {"ticker": req.ticker, "start": req.start, "end": req.end,
                  "interval": req.interval, "source": req.source}
        key = cook_cache.key_for(req.graph, cook.frames, window)
        entry = cook_cache.COOK_CACHE.put(
            key=key, graph=req.graph, program=cook.program, result=cook.result, window=window,
        )
        return entry.cook_id if entry.cook_id in cook_cache.COOK_CACHE else None
    except Exception:
        logger.exception("could not cache the backtest cook")
        return None


def _with_cook_id(response: GraphBacktestResponse, cook_id: str | None) -> GraphBacktestRouteResponse:
    """The route response: the backtest's fields (shared, not copied) plus
    cook_id."""
    return GraphBacktestRouteResponse.model_construct(**dict(response), cook_id=cook_id)


class _GraphProblem(Exception):
    """A graph or data error on a cache miss: answered with the plan 4.4
    400 body (_graph_error)."""

    def __init__(self, exc: BaseException) -> None:
        super().__init__(str(exc))
        self.exc = exc


class _Resolved(NamedTuple):
    entry: Any
    state: str          # "hit" | "miss"
    graph: Any
    stale_data: bool    # served from the last good cook: the fetch failed


def _resolve_cook(cook_id: str | None, graph_data: dict | None, window) -> _Resolved:
    """The cook for an /inspect or /preview request.

    - Neither a cook id nor a graph: 422 cook_or_graph_required (a client
      bug, not an expiry).
    - No graph: the cook id must name a live entry, else 410 cook_expired.
    - A graph: its frame is fetched (the TTL-cached _fetch, so this is cheap
      while the data is fresh) and the cache is looked up by key, so a graph
      edit or a new bar gives a fresh cook.  The window defaults to the cook
      id's window.  A miss cooks the graph and stores it; two requests that
      miss the same key at once cook it once (single flight).
    - The fetch fails (the provider is down, or returns no bars): the last
      good live cook of the same graph and window is served, marked
      stale_data.  With none, a provider error is 502 data_unavailable and
      no data stays the 400 graph-error body.

    Runs on FastAPI's thread pool (the routes are sync), never on the event
    loop.
    """
    from shared import _fetch, require_valid_source

    if not cook_id and graph_data is None:
        raise HTTPException(status_code=422, detail={
            "code": "cook_or_graph_required",
            "message": "Send the cook_id of a live cook, or a graph and window.",
        })
    entry = cook_cache.COOK_CACHE.get(cook_id) if cook_id else None
    if graph_data is None:
        if entry is None:
            raise HTTPException(status_code=410, detail={"code": "cook_expired"})
        return _Resolved(entry, "hit", entry.graph, False)

    try:
        graph = Graph.model_validate(graph_data)
    except (GraphValidationError, ValidationError) as exc:
        raise _GraphProblem(exc) from exc

    if window is not None:
        win = window.model_dump()
    elif entry is not None:
        win = dict(entry.window)
    else:
        raise HTTPException(status_code=422, detail={
            "code": "window_required",
            "message": "Send a window with the graph, or the cook_id of a live cook.",
        })

    def last_good_cook():
        graph_hash = cook_cache.eval_hash(graph)
        if entry is not None and entry.key[0] == graph_hash and \
                cook_cache.window_id(entry.window) == cook_cache.window_id(win):
            return cook_cache.COOK_CACHE.get(entry.cook_id)
        return cook_cache.COOK_CACHE.find_live(graph_hash, win)

    source = None
    try:
        source = require_valid_source(win["source"])
        df = _fetch(win["ticker"], win["start"], win["end"], win["interval"], source=source)
        if df is None or len(df) == 0:
            raise ValueError(f"No data for {win['ticker']} in {win['start']}..{win['end']}.")
    except HTTPException as exc:
        if exc.status_code == 400:
            raise _GraphProblem(ValueError(str(exc.detail))) from exc
        fallback = last_good_cook()
        if fallback is None:
            raise
        logger.warning("nodebuilder cook: fetch failed (%s), serving the last good cook", exc.detail)
        return _Resolved(fallback, "hit", graph, True)
    except Exception as exc:
        if source is None:      # require_valid_source: not a fetch problem
            raise _GraphProblem(exc) from exc
        fallback = last_good_cook()
        if fallback is not None:
            logger.warning("nodebuilder cook: fetch failed (%r), serving the last good cook", exc)
            return _Resolved(fallback, "hit", graph, True)
        if isinstance(exc, ValueError):
            raise _GraphProblem(exc) from exc
        logger.exception("nodebuilder cook: fetch failed")
        raise HTTPException(status_code=502, detail={
            "code": "data_unavailable",
            "message": f"Could not fetch {win['ticker']} ({win['interval']}) from {win['source']}.",
        }) from exc

    lookup_frames = ((win["ticker"], win["interval"], df),)
    key = cook_cache.key_for(graph, lookup_frames, win)

    def cook_and_put():
        try:
            cook = cook_graph_window(graph, df=df, **win)
        except (GraphValidationError, ValueError) as exc:
            raise _GraphProblem(exc) from exc
        except HTTPException as exc:
            if exc.status_code == 400:
                raise _GraphProblem(ValueError(str(exc.detail))) from exc
            raise
        # Key the entry by every frame the cook read.  Should a cook read a
        # frame the lookup above did not fetch, the entry just misses next
        # time (never a wrong hit) until that frame joins the lookup.
        return cook_cache.COOK_CACHE.put(
            key=cook_cache.key_for(graph, cook.frames, win), graph=graph,
            program=cook.program, result=cook.result, window=win,
        )

    entry, state = cook_cache.COOK_CACHE.get_or_cook(key, cook_and_put)
    return _Resolved(entry, state, graph, False)


def _cook_flags(resolved: _Resolved) -> dict:
    """The fields /inspect and /preview add about the cook itself.

    kept       : the cook_id names a live cache entry.  False when the cook
                 was too big to keep: the client should send the graph and
                 window with every request rather than the cook_id alone
                 (which would only get a 410).
    stale_data : the fetch failed and this is the last good cook of the
                 same graph and window, not today's data.
    """
    return {
        "kept": resolved.entry.cook_id in cook_cache.COOK_CACHE,
        "stale_data": resolved.stale_data,
    }


def _parse_body(model, payload: dict[str, Any]):
    """Validate *payload* with FastAPI's usual 422 on a bad field."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        )


def _inspect_error(exc: cook_cache.InspectError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


@router.post(
    "/inspect",
    responses={
        200: {"model": InspectResponse},
        400: {"description": "Graph error: {detail, node_id, code, diagnostics}"},
        410: {"description": 'The cook is gone and no graph was sent: {"detail": {"code": "cook_expired"}}'},
        422: {"description": "A bad field, or {detail: {code: cook_or_graph_required | window_required | ...}}"},
        502: {"description": 'The data could not be fetched and no earlier cook of this graph is live: {"detail": {"code": "data_unavailable"}}'},
    },
)
def post_inspect(payload: dict[str, Any] = Body(...)):
    """One page of a node's (or a wire's) stream from a cook (plan D6).

    Reads the cook cache; a miss cooks the graph over the window (the same
    cook a backtest makes, without the simulation).  A wire target shows
    its source node's whole output stream and the names the consumer reads
    (read_by_consumer).  A bypassed node shows its input stream.
    """
    req = _parse_body(InspectRequest, payload)
    try:
        resolved = _resolve_cook(req.cook_id, req.graph, req.window)
        body = cook_cache.inspect_body(
            resolved.entry, graph=resolved.graph, target=req.target, attrs=req.attrs,
            offset=req.offset, limit=req.limit, around_time=req.around_time,
            flt=req.filter, cache_state=resolved.state,
        )
        body.update(_cook_flags(resolved))
    except _GraphProblem as exc:
        return _graph_error(exc.exc, req.graph)
    except cook_cache.InspectError as exc:
        raise _inspect_error(exc)
    except HTTPException:
        raise
    except Exception:
        logger.exception("/api/nodebuilder/inspect failed")
        raise HTTPException(status_code=500, detail="inspect failed")
    # A plain JSON response: the rows can be large, and the body is already
    # plain Python values (no NaN), so FastAPI's encoder pass is skipped.
    return JSONResponse(content=body)


@router.post(
    "/preview",
    responses={
        200: {"model": PreviewResponse},
        400: {"description": "Graph error: {detail, node_id, code, diagnostics}"},
        410: {"description": 'The cook is gone and no graph was sent: {"detail": {"code": "cook_expired"}}'},
        422: {"description": "A bad field, or {detail: {code: cook_or_graph_required | window_required | ...}}"},
        502: {"description": 'The data could not be fetched and no earlier cook of this graph is live: {"detail": {"code": "data_unavailable"}}'},
    },
)
def post_preview(payload: dict[str, Any] = Body(...)):
    """Sparkline data for every node (or node_ids): each node's primary
    write decimated to `points` values (plan D6, S26/S27).  Reads the cook
    cache; a miss cooks the graph over the window."""
    req = _parse_body(PreviewRequest, payload)
    try:
        resolved = _resolve_cook(req.cook_id, req.graph, req.window)
        body = cook_cache.preview_body(
            resolved.entry, graph=resolved.graph, node_ids=req.node_ids, points=req.points,
        )
        body.update(_cook_flags(resolved))
    except _GraphProblem as exc:
        return _graph_error(exc.exc, req.graph)
    except cook_cache.InspectError as exc:
        raise _inspect_error(exc)
    except HTTPException:
        raise
    except Exception:
        logger.exception("/api/nodebuilder/preview failed")
        raise HTTPException(status_code=500, detail="preview failed")
    return JSONResponse(content=body)
