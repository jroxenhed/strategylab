"""Node-builder API routes.

POST /api/nodebuilder/auto_render  — Unit 3
POST /api/nodebuilder/backtest     — Unit 8b
POST /api/nodebuilder/validate     — W1 item 1.C
POST /api/nodebuilder/inspect      — W4 item 4.A (the wire inspector, plan D6)
POST /api/nodebuilder/preview      — W4 item 4.A (node sparklines, plan D6)
POST /api/nodebuilder/parse_code   — W7 item 7.C (checks code while the user types)
GET  /api/nodebuilder/code_capabilities — W7 item 7.C (the kill switch, limits, sl helpers)

Graph errors return HTTP 400 in the plan 4.4 shape:
{"detail": <message>, "node_id": <id or null>, "code": <diagnostic code>,
 "diagnostics": [Diagnostic]}, so the editor can show the message, badge the
node at fault and every other problem the graph has.  A code failure while
cooking (W7: code_runtime, code_type, attr_missing, code_timeout after the
60 s guard...) is such a 400 too, with the line and column in the code.
These routes are plain defs: FastAPI runs them in its thread pool, so a
cook (code included) never runs on the event loop.  /validate and
/parse_code never run code (prepare only).
"""
from __future__ import annotations

import logging
from typing import Any, Literal, NamedTuple, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from models import StrategyRequest
from nodebuilder import cook_cache
from nodebuilder.code import CodeError
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
from nodebuilder.compile import compile as compile_graph
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
    fetch_reference_frames,
    window_program,
    run_graph_backtest,
    run_graph_backtest_cooked,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nodebuilder", tags=["nodebuilder"])

PARSE_CODE_MAX_CHARS = 262_144
"""The most characters /parse_code takes (32 times the 8 KB snippet limit,
which is reported as code_limit): a robustness cap, not a code rule."""


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

    W7: param_deps lists one edge per literal ch() reference that points
    somewhere: {"reader_id", "reader_param", "target_id", "target"}
    (reader_param is null for a call in a code block or a Wrangle body;
    target is a param name or an @attr).  [] when there are none.  A code
    write with no annotation has "dtype": "any" in streams.  Code is
    prepared (parsed and compiled), never run.
    """
    if "graph" not in payload:
        raise RequestValidationError(
            [{"type": "missing", "loc": ("body", "graph"), "msg": "Field required", "input": payload}]
        )
    from nodebuilder.compile import capture_checks

    with capture_checks() as checks:
        result = validate_graph_full(payload["graph"])
    return {
        "ok": not has_errors(result.diagnostics),
        "diagnostics": [d.model_dump() for d in result.diagnostics],
        "streams": result.streams,
        "stream_schema": STREAM_SCHEMA_VERSION,
        "param_deps": list(checks[-1].param_deps) if checks else [],
    }


# ---------------------------------------------------------------------------
# W7: parse_code and code_capabilities
# ---------------------------------------------------------------------------


class ParseCodeRequest(BaseModel):
    """The parse_code body (plan W7 contract).  ``param`` is optional: the
    param an expression sits on (its diagnostics then carry it).
    ``expected`` ({"type", "options"?}) is the type of that param: an
    expression that is one literal value is checked against it.  ``code``
    is capped far above the 8 KB snippet limit (that limit is a code_limit
    diagnostic), so one request cannot hold an unbounded string."""
    model_config = ConfigDict(extra="ignore")

    code: str = Field(max_length=PARSE_CODE_MAX_CHARS)
    context: Literal["expr", "node_code", "wrangle"]
    expected: Optional[dict[str, Any]] = None
    graph: Optional[dict[str, Any]] = None
    node_id: Optional[str] = None
    param: Optional[str] = None


def _code_graph(graph_data: Optional[dict], node_id: Optional[str]):
    """(node, check) for a parse_code request: the node the code belongs to
    (a models.Node, the raw node dict, or None) and a compile pass over the
    graph (None when the graph does not parse).  Never runs code."""
    if not isinstance(graph_data, dict):
        return None, None
    from nodebuilder.compile import check_graph
    from nodebuilder.migrate import migrate_graph_data

    try:
        graph = Graph.model_validate(migrate_graph_data(graph_data))
    except Exception:  # a graph mid-edit may not parse; the code is still checked
        raw = graph_data.get("nodes")
        node = raw.get(node_id) if isinstance(raw, dict) and node_id else None
        return (node if isinstance(node, dict) else None), None
    node = graph.nodes.get(node_id) if node_id else None
    try:
        check = check_graph(graph, code_switch=False)
    except Exception:  # never a 500 for a graph the editor is still building
        logger.exception("parse_code: the graph check failed")
        check = None
    return node, check


def _attr_lookup(check, node_id: Optional[str], context: str):
    """lookup(name) -> (class, dtype) of an attribute the code reads, from
    the stream it sees: the node's merged input stream for an expression or
    a Wrangle, its output stream (input plus its own outputs) for a code
    block.  None when the stream is not known."""
    analysis = getattr(check, "analysis", None)
    res = analysis.nodes.get(node_id) if (analysis is not None and node_id) else None
    if res is None:
        return None
    schema = res.out_schema if context == "node_code" else (res.in_schema or res.out_schema)
    if schema is None:
        return None

    def lookup(name: str):
        info = schema.lookup(f"@{name}")
        return (info.kind, info.dtype) if info is not None else None

    return lookup


def _literal_problem(prepared, expected: Optional[dict], node_id: Optional[str],
                     param: Optional[str]) -> list:
    """code_type (or param_invalid for an unknown option) when an expression
    is one literal value that does not fit the param's *expected* type, on
    the whole expression.  Nothing runs: only a literal is read
    (ast.literal_eval)."""
    import ast

    from nodebuilder.code import CodeError, check_expr_result
    from nodebuilder.code.sugar import normalize_newlines, split_lines
    from nodebuilder.diagnostics import make as make_diagnostic

    if prepared.context != "expr" or not prepared.ok or not isinstance(expected, dict):
        return []
    kind = expected.get("type")
    if kind not in ("int", "number", "float", "bool", "string", "str", "select"):
        return []
    options = expected.get("options")
    options = [o for o in options if isinstance(o, str)] if isinstance(options, list) else None
    try:
        value = ast.literal_eval(prepared.source.strip())
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return []   # not one literal: its type is known only when it runs
    try:
        check_expr_result(value, kind, options)
    except CodeError as exc:
        lines = split_lines(normalize_newlines(prepared.source))
        return [make_diagnostic(exc.code, exc.message, node_id=node_id, param=param, line=1,
                                col=0, end_line=len(lines), end_col=len(lines[-1]))]
    except ValueError:
        return []
    return []


def _path_problems(prepared, check, node_id: Optional[str], param: Optional[str]) -> list:
    """ref_broken for each ch() path of the code that points at nothing in
    the graph (a missing node or param, an attribute nobody writes), at the
    call's line and column.  The same rules as compile (kernel/params.py)."""
    from nodebuilder.diagnostics import make as make_diagnostic
    from nodebuilder.kernel import params as kparams

    plan = getattr(check, "plan", None)
    paths = prepared.paths
    if plan is None or not node_id or not paths or node_id not in plan.flat.graph.nodes:
        return []
    children = kparams.child_index(plan._source.nodes)
    out = []
    for cr in paths:
        ref = kparams._build_ref(plan, node_id, param, cr, children)
        if ref.problem is None:
            continue
        out.append(make_diagnostic(ref.problem_code or "ref_broken", ref.problem,
                                   node_id=node_id, param=param,
                                   line=cr.line, col=cr.col, end_line=cr.end_line,
                                   end_col=cr.end_col))
    return out


@router.post("/parse_code")
def post_parse_code(payload: dict[str, Any] = Body(...)):
    """Check one piece of code while the user types (W7, surfaces S44-S48).

    Body: {"code", "context": "expr" | "node_code" | "wrangle", "expected":
    {"type"} | null, "graph": Graph | null, "node_id": str | null, "param":
    str | null (optional: the param an expression sits on)}.

    Reply: {"ok", "params", "reads", "writes", "result_type": null,
    "diagnostics"}.  params are the spare params the code declares with
    ch*() (lookback_bars first for a code block or a Wrangle, unless the
    code declares it); reads and writes are attributes, every write a
    point of dtype bool, float (from its annotation) or "any"; a read's
    class and dtype come from the stream the node sees in *graph*.
    Diagnostics: prepare()'s (code_syntax, code_limit, ch_dynamic,
    attr_dynamic, code_type, ref_broken for a bare ch() with no
    declaration), ref_broken for a ch() path that points at nothing in
    *graph*, and code_disabled while SL_CODE_NODES=0.

    Positions: ``line`` is 1-based and ``col`` 0-based, both counting
    characters of the user's own text (before the @name rewrite), as
    Python reports them (SyntaxError lineno and offset - 1, ast lineno and
    col_offset).  ``end_line`` and ``end_col`` follow the same rule and may
    be null.

    Runs prepare() only: the code is parsed, scanned and compiled, never
    executed.  result_type is always null in W7 (no code runs here).  An
    expression that is one literal value is checked against *expected*
    (code_type when it does not fit; read with ast.literal_eval, not run).
    """
    from nodebuilder.code import code_enabled, disabled_diagnostic, prepare

    req = _parse_body(ParseCodeRequest, payload)
    param = req.param if req.context == "expr" else None
    node, check = _code_graph(req.graph, req.node_id)
    prepared = prepare(req.code, req.context, node if node is not None else req.node_id,
                       node_id=req.node_id, param=param)
    stream_lookup = _attr_lookup(check, req.node_id, req.context)

    def lookup(name: str):
        # A read of what the code itself wrote earlier has that write's dtype.
        found = stream_lookup(name) if stream_lookup is not None else None
        if found is None and name in prepared.writes:
            return "point", prepared.write_dtypes.get(name, "any")
        return found

    body = prepared.to_parse_result(lookup)
    diagnostics = [d.to_diagnostic() for d in prepared.diagnostics]
    diagnostics += _literal_problem(prepared, req.expected, req.node_id, param)
    diagnostics += _path_problems(prepared, check, req.node_id, param)
    if not code_enabled():
        off = disabled_diagnostic(req.node_id).to_diagnostic()
        off.param = param
        diagnostics.insert(0, off)
    body["diagnostics"] = [d.model_dump() for d in diagnostics]
    body["ok"] = not has_errors(diagnostics)
    return body


@router.get("/code_capabilities")
def get_code_capabilities():
    """Whether code nodes are on (SL_CODE_NODES; unset means on), the
    language, the limits (source size, default lookback_bars, the bot and
    backtest cook timeouts), the modules code sees, the sl helpers with
    signature, return kind and a one-line doc (editor completion, S47), and
    how many timed-out cooks are still running (leaked_cooks)."""
    from nodebuilder.code import capabilities

    return capabilities()


@router.post(
    "/backtest",
    response_model=GraphBacktestRouteResponse,
    responses={400: {"description": "Graph error: {detail, node_id, code, diagnostics}"}},
)
def post_graph_backtest(payload: dict[str, Any] = Body(...)):
    """Run a backtest using a compiled node graph.

    Returns {summary, trades, equity_curve, baseline_curve, groups,
    combined, cook_id} (plan W5 contract).  groups has one result per
    Output Group (the implicit "main" for a graph with none); combined is
    every group together, with exposure_pct and gross_deployed_pct.  The
    four legacy keys are the group's results when there is one group, and
    the combined ones (with no trades) when there are more.  cook_id names
    this run's cook in the cook cache (/inspect, /preview), or is null when
    the cook was too big to keep.  Each summary also carries open_position
    and exit_connected (graph backtest only).
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
    except CodeError as exc:
        # Code failed while cooking (W7): code_runtime, code_type, attr_missing,
        # ref_broken, code_timeout... with the line and column in the code.
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
    """The route response: the backtest's fields, groups and combined result
    (shared, not copied) plus cook_id."""
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
        # Compiled up front: the reference Tickers it reads are part of the
        # cache key (plan D6, D8).
        program = compile_graph(graph)
    except (GraphValidationError, ValidationError, ValueError) as exc:
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
        # Every reference frame the cook reads, padded exactly as the
        # backtest fetches them (TTL-cached _fetch).  A reference fetch that
        # fails is handled like the main frame's.  Only the window's own
        # groups and the unclaimed nodes they can feed are cooked
        # (run.window_program, KA-2/KA-4): a stray Ticker is never fetched.
        refs = fetch_reference_frames(window_program(program, win["ticker"], win["interval"]),
                                      win["start"], win["end"], source)
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

    # The key covers the main frame AND every reference frame, in the order
    # GraphCook.frames lists them: two windows that differ only in reference
    # data never share a cook, and a backtest's cook is found again.
    lookup_frames = ((win["ticker"], win["interval"], df),) + tuple(
        (sym, itv, ref_df) for (sym, itv), ref_df in refs.items())
    key = cook_cache.key_for(graph, lookup_frames, win)

    def cook_and_put():
        try:
            cook = cook_graph_window(graph, df=df, refs=refs, **win)
        except (GraphValidationError, ValueError, CodeError) as exc:
            # CodeError (W7): code failed or ran past the 60 s guard.
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
