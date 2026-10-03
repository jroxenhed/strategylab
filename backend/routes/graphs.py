"""Graph routes: named graphs stored on the server (F435 W1 item 1.B, decision D2).

    GET    /api/graphs              -> 200 {"graphs": [GraphListItem]}
    GET    /api/graphs/{id}         -> 200 GraphEnvelope | 404
    POST   /api/graphs              -> 201 GraphEnvelope (rev 1)
    PUT    /api/graphs/{id}         -> 200 GraphEnvelope (rev+1) | 409
    DELETE /api/graphs/{id}?rev=N   -> 204 | 409
    POST   /api/graphs/seed         -> 200 {"imported": [id], "skipped": [{name, reason}]}
    POST   /api/graphs/{id}/spawn   -> 201 {"bots": [...]} (F435 W5 5.D, plan D7)

Errors:
  - 409 {"detail": {"code": "rev_conflict", "current_rev": N}} on a stale rev.
  - 409 {"detail": {"code": "name_taken"}} when another graph has the name.
  - 409 {"detail": {"code": "graph_corrupt", "detail": <sentence>}} when the
    stored file cannot be read. That is the file's fault, never the body's.
    DELETE of such a file moves it aside (``<id>.json.corrupt``) and is a 204.
  - 400 {"detail", "node_id", "code", "diagnostics"} (plan section 4.4) when
    the graph does not parse. Item 1.C fills in the full diagnostics list.
  - 413 when the body is over 2 MB. 422 for a badly shaped body.

Spawn (plan W5 "Spawn" contract): one stopped bot per leg, from saved
revision ``rev``, all or nothing (one BotManager.add_bots call, one save).
  - 409 {"detail": {"code": "rev_conflict", "current_rev": N}} on a stale rev.
  - 400 {"detail": {"code", "message", "diagnostics", ...}} with code
    group_unknown, same_symbol_same_direction, graph_invalid, leg_invalid
    (a field the leg may not set) or fund (not enough unallocated fund).
  - 400 {"detail": {"code": "reference_unavailable", "message", "symbol",
    "interval", "data_source", "group", "groups"}} when a reference Ticker a
    leg's group reads cannot be fetched on that leg's data source (LM-1).
  - The body's optional trading_hours, skip_after_stop and dynamic_sizing
    (the BotConfig shapes) go to every leg (LM-5).
  - 400 {"detail": {"code": "asset_missing", "message", "node_id", "name",
    "asset", "version"}} when a locked library asset instance (``name`` is
    the instance node's name) points at an asset version the library does
    not have (F435 W6 6.B).  409 with the same fields and code
    ``asset_corrupt`` when that version's stored file is damaged.  Any
    other asset problem (a cycle), and any error the editor's compile of a
    graph with a locked instance reports (an interface_mismatch), is 400
    graph_invalid with the problems as diagnostics.
  - 400 {"detail": {"code": "code_disabled", "message", "node_ids",
    "diagnostics"}} while SL_CODE_NODES=0 and the graph holds code (F435
    W7, design note 4.9).  Saving such a graph still works.

Code audit (W7, design note 4.10): every save (POST, PUT, seed) of a graph
that holds code, and every spawn, logs one ``code_audit`` line per snippet
on the ``strategylab.code_audit`` logger, with the X-Forwarded-Email user.

Bodies are read by hand so the 2 MB limit is checked before JSON parsing, and
all file work and graph parsing run in the thread pool, never on the event loop.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from models import DynamicSizingConfig, IntervalField, SkipAfterStopConfig, TradingHoursConfig
from nodebuilder.diagnostics import error_body, from_error, validate_graph_data
from nodebuilder.models import Graph, GraphValidationError
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


class SpawnLegBody(BaseModel):
    """One leg of a spawn: one Output Group becomes one stopped bot."""
    model_config = ConfigDict(extra="forbid")

    group: str
    allocated_capital: float = Field(gt=0)
    broker: Literal["alpaca", "ibkr"]
    data_source: Literal["yahoo", "alpaca", "alpaca-iex", "ibkr"]
    interval_override: Optional[IntervalField] = None  # None: the group's interval
    strategy_name: Optional[str] = None                # None: "<graph name> ▸ <group>"
    # Only for a graph with no Output Group (the implicit "main" group, which
    # has no direction of its own).  Default "long".  An explicit group's
    # direction always comes from the group.
    direction: Optional[Literal["long", "short"]] = None


class SpawnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rev: int
    legs: list[SpawnLegBody] = Field(min_length=1, max_length=32)
    # The sidebar-owned gates the graph backtest ran with (plan D11, F435 W5
    # LM-5), shared by every leg; each has the BotConfig field's shape.
    # None: the bot runs without that gate.  They cannot be set later
    # (PATCH_DENYLIST: "set at create"), so a spawned bot gets them here.
    trading_hours: Optional[TradingHoursConfig] = None
    skip_after_stop: Optional[SkipAfterStopConfig] = None
    dynamic_sizing: Optional[DynamicSizingConfig] = None


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


# ---------------------------------------------------------------------------
# Code (F435 W7): the audit trail and the kill switch
# ---------------------------------------------------------------------------


def forwarded_email(request: Optional[Request]) -> Optional[str]:
    """The signed-in user's email as oauth2-proxy forwards it, for the code
    audit lines (None on the Mac, where nothing sets it)."""
    if request is None:
        return None
    return request.headers.get("x-forwarded-email") or None


def audit_graph(event: str, graph: Any, email: Optional[str] = None, **fields: Any) -> None:
    """One ``code_audit`` line per code snippet of *graph* (W7, design note
    4.10).  Nothing is ever refused because of it: a failure is logged and
    ignored.  Cheap (a hash of each snippet), but call it from the thread
    pool with the save it belongs to."""
    try:
        from nodebuilder.code import audit_log, iter_code_snippets

        snippets = iter_code_snippets(graph)
        if snippets:
            audit_log(event, snippets, email=email, **fields)
    except Exception:
        logger.exception("code audit (%s) failed", event)


def _holds_code(env: dict) -> bool:
    """True when a saved graph holds a code snippet (a plain scan, no
    hashing), so a save without code makes no extra thread-pool call."""
    from nodebuilder.code import has_code

    try:
        return has_code(env.get("graph") or {})
    except Exception:  # an odd stored shape: let the audit itself decide
        return True


def _audit_saved(env: dict, email: Optional[str]) -> dict:
    audit_graph("graph_save", env.get("graph"), email, graph_id=env.get("id"), rev=env.get("rev"))
    return env


def _seed_and_audit(legacy: Any, email: Optional[str]) -> dict:
    """Seed import, then one audit line per code snippet of each imported
    graph (they are saves too)."""
    store = get_store()
    out = store.seed(legacy)
    for graph_id in out.get("imported", []):
        try:
            _audit_saved(store.get(graph_id), email)
        except Exception:
            logger.exception("code audit of seeded graph %s failed", graph_id)
    return out


def code_disabled_detail(graph: Graph, **extra) -> Optional[dict]:
    """``detail`` code ``code_disabled`` when SL_CODE_NODES=0 and *graph*
    holds code (W7, design note 4.9), else None.  Spawn and graph_update
    refuse such a graph with it; a save still works (it is data)."""
    from nodebuilder.code import ENV_SWITCH, code_enabled
    from nodebuilder.trading.nodes_code import code_node_ids, disabled_found

    if code_enabled():
        return None
    ids = code_node_ids(graph)
    if not ids:
        return None
    diagnostics = [diag.model_dump() for diag, _err in disabled_found(graph)]
    return snapshot_detail(
        "code_disabled",
        f"Code nodes are turned off on this server ({ENV_SWITCH}=0), and this graph holds code "
        f"({len(ids)} node(s)), so no bot can run it.", node_ids=ids, diagnostics=diagnostics,
        **extra)


# Errors a graph that does not parse can raise from Graph.model_validate.
# GraphCorruptError is not one of them (it is not a ValueError), and every
# route catches it first anyway.
_PARSE_ERRORS = (GraphValidationError, ValidationError, ValueError, TypeError)


# ---------------------------------------------------------------------------
# Bot snapshots (F435 W5 5.D): spawn here, and graph_update / add with a
# graph_id in routes/bots.py, load a saved revision the same way.
# ---------------------------------------------------------------------------


class AssetBakeError(Exception):
    """A graph could not become a bot's snapshot (F435 W6 6.B).

    ``code`` is ``asset_missing`` (no such asset version), ``asset_corrupt``
    (the version's stored file cannot be read, status 409, LD-03) or
    ``graph_invalid`` (an asset cycle, an expansion that is too big, or a
    compile error the editor reports, BS-02); ``problems`` are the
    diagnostics.  ``status`` is the HTTP status of the refusal."""

    def __init__(self, code: str, message: str, node_id: Optional[str] = None,
                 name: Optional[str] = None, asset: Optional[str] = None,
                 version: Optional[int] = None, problems: Optional[list] = None,
                 status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.node_id = node_id
        self.name = name
        self.asset = asset
        self.version = version
        self.problems = problems or []
        self.status = status

    def detail(self) -> dict:
        """The ``detail`` object of the refusal."""
        if self.code in ("asset_missing", "asset_corrupt"):
            return snapshot_detail(self.code, self.message, node_id=self.node_id,
                                   name=self.name, asset=self.asset, version=self.version)
        return snapshot_detail("graph_invalid", self.message, node_id=self.node_id,
                               diagnostics=self.problems)


def _library_lookup():
    """(resolver, corrupt) for one bake: the resolver reads each asset
    version from the library once (so the bake and the editor check below
    see the same files even if the library changes meanwhile), and
    ``corrupt`` collects the versions whose file is damaged, so the refusal
    can say so instead of "not in the library" (LD-03)."""
    from nodebuilder.storage import AssetCorruptError, AssetNotFoundError, get_library

    library = get_library()
    cache: dict = {}
    corrupt: dict = {}

    def lookup(name: str, version: int):
        key = (name, version)
        if key not in cache:
            try:
                cache[key] = library.get(name, version)
            except AssetCorruptError as exc:
                corrupt[key] = exc
                cache[key] = None
            except AssetNotFoundError:
                cache[key] = None
            except Exception:  # a resolver never raises (kernel.assets.Resolver)
                logger.exception("asset %s version %s: lookup failed", name, version)
                cache[key] = None
        return cache[key]

    return lookup, corrupt


def _refuse_editor_errors(graph: Graph, lookup) -> None:
    """Raise AssetBakeError graph_invalid when the graph, as the editor
    compiles it (locked instances read from the library), has an error
    (BS-02).  The bake unlocks every instance, and an unlocked copy skips
    the asset interface check (declared reads and writes, stream schema),
    so without this a graph the editor refuses to backtest could still
    become a bot.  Diagnostics carry the editor's node ids (BS-04)."""
    from nodebuilder.compile import check_graph

    check = check_graph(graph, resolve=lookup)
    errors = [d for d in check.diagnostics if d.severity == "error"]
    if not errors and not check.errors:
        return
    listed = list(check.diagnostics)
    first = errors[0] if errors else from_error(check.errors[0])
    if not errors:
        listed.insert(0, first)
    raise AssetBakeError("graph_invalid", first.message, node_id=first.node_id,
                         problems=[d.model_dump() for d in listed])


def bake_in_library(graph: Graph) -> Graph:
    """The library bake-in hook (plan D7, F435 W6 6.B).

    A bot's graph is a snapshot with every library asset's definition copied
    in, so a later library edit or delete can never change a live bot.
    Every path that gives a bot a graph calls this: spawn, add by graph_id
    and graph_update (load_graph_snapshot), and add or PATCH with an inline
    graph (routes.bots._bake_inline_graph).  ``kernel.assets.bake_assets``
    gives each locked asset instance the asset's nodes as stored children
    (locked becomes False, asset_ref stays for provenance) and writes every
    promoted param's value into its target (BS-01), so a reader that
    ignores ``promoted`` computes the same strategy.  A graph with nothing
    to bake comes back unchanged (the same object).

    A graph with a locked instance is also compiled as the editor compiles
    it, and refused when that has an error (BS-02).

    Raises AssetBakeError: asset_missing when an instance's asset version is
    not in the library; asset_corrupt (409) when its file is damaged;
    graph_invalid when the assets cannot be expanded (a cycle) or the
    editor's compile has an error.  Reads asset files: thread pool only.
    """
    from nodebuilder.kernel import assets as kernel_assets
    from nodebuilder.kernel.flatten import is_locked_instance

    lookup, corrupt = _library_lookup()
    try:
        baked = kernel_assets.bake_assets(graph, lookup)
    except kernel_assets.AssetError as exc:
        node = graph.nodes.get(exc.node_id) if exc.node_id else None
        name = node.name if node is not None else exc.node_id
        what = (f"{exc.asset_name} version {exc.asset_version}" if exc.asset_name
                else "its library asset")
        if exc.code == "asset_missing" and (exc.asset_name, exc.asset_version) in corrupt:
            raise AssetBakeError(
                "asset_corrupt",
                f"Node {name!r} uses {what}, whose stored file is damaged and cannot be "
                f"read. Pick another version, or delete that version and save the asset "
                f"again.",
                node_id=exc.node_id, name=name, asset=exc.asset_name,
                version=exc.asset_version, status=409) from None
        if exc.code == "asset_missing":
            raise AssetBakeError(
                "asset_missing",
                f"Node {name!r} uses {what}, which is not in the library. "
                f"Pick another version or unlock the node first.",
                node_id=exc.node_id, name=name, asset=exc.asset_name,
                version=exc.asset_version) from None
        raise AssetBakeError(exc.code, str(exc), node_id=exc.node_id, name=name,
                             problems=[from_error(exc).model_dump()]) from None
    if any(is_locked_instance(n) for n in graph.nodes.values()):
        _refuse_editor_errors(graph, lookup)
    return baked


class SnapshotRefused(Exception):
    """A spawn, graph_update or bot add answered with an error status and a
    ``detail`` (a string for 404, else an object with a ``code``)."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status, content={"detail": self.detail})


def load_graph_snapshot(graph_id: str, rev: int) -> tuple[dict, Graph]:
    """(envelope, graph) of saved revision *rev*, ready to become a bot's
    graph (parsed, migrated, library baked in).

    Raises SnapshotRefused: 404 no such graph; 409 graph_corrupt; 409
    rev_conflict with current_rev (the store keeps only the latest rev);
    400 graph_invalid with diagnostics when the saved graph does not parse,
    or (with a locked asset instance) when the editor's compile of it has
    an error; 400 asset_missing when a locked asset instance's version is
    not in the library (W6 6.B); 409 asset_corrupt when its file is damaged.
    Reads a file: call it from the thread pool, never the event loop.
    """
    try:
        env = get_store().get(graph_id)
    except GraphNotFoundError:
        raise SnapshotRefused(404, "Graph not found") from None
    except GraphCorruptError as exc:
        raise SnapshotRefused(409, {"code": "graph_corrupt", "detail": str(exc)}) from None
    if env["rev"] != rev:
        raise SnapshotRefused(409, {"code": "rev_conflict", "current_rev": env["rev"]})
    try:
        graph = Graph.model_validate(env["graph"])
    except _PARSE_ERRORS as exc:
        raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"])) from None
    try:
        return env, bake_in_library(graph)
    except AssetBakeError as exc:
        raise SnapshotRefused(exc.status, exc.detail()) from None
    except _PARSE_ERRORS as exc:  # the baked graph does not load
        raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"])) from None


def snapshot_detail(code: str, message: str, **extra) -> dict:
    """The ``detail`` object of a spawn or graph_update refusal."""
    return {"code": code, "message": message, **extra}


def weight_zero_detail(group) -> Optional[dict]:
    """``leg_invalid`` detail for a leg whose Output Group has capital weight
    0, else None.  Such a group gets no capital and is never simulated (the
    graph backtest leaves it out), so a bot of it would trade something no
    backtest checked.  Spawn and add refuse it; the spawn dialog leaves such
    a leg unchecked."""
    if getattr(group, "weight", 1.0) != 0:
        return None
    return snapshot_detail(
        "leg_invalid",
        f"Output Group {group.name!r} has capital weight 0, so it never trades in the "
        f"backtest. Give it a weight above 0 first.",
        groups=[group.name])


def graph_invalid_detail(exc: BaseException, graph_data: Any, **extra) -> dict:
    """``detail`` for a saved graph that does not parse or compile: code
    graph_invalid, the error's message and node, and every diagnostic (the
    /validate check, no market data).  CPU work: thread pool only."""
    diagnostics = validate_graph_data(graph_data) if isinstance(graph_data, dict) else []
    body = error_body(exc, diagnostics)
    return snapshot_detail("graph_invalid", body["detail"], node_id=body["node_id"],
                           diagnostics=body["diagnostics"], **extra)


def implicit_ticker(graph: Graph):
    """The Ticker of a graph with no Output Group (the implicit "main"
    group): the first root Ticker with no reference prefix, else the first
    root Ticker, else None.  The same choice the spawn dialog shows."""
    roots = [n for n in graph.nodes.values() if n.type == "ticker" and n.parent is None]
    for node in roots:
        if not str((node.params or {}).get("prefix") or "").strip():
            return node
    return roots[0] if roots else None


def group_bot_fields(graph: Graph, group, *, interval_override: Optional[str] = None,
                     direction: Optional[str] = None,
                     fallback_symbol: Optional[str] = None) -> dict:
    """The BotConfig fields an Output Group decides (plan D7): symbol,
    interval, direction and graph_direction_mode.

    An explicit group gives its own; a regime_switch group runs as "long"
    with graph_direction_mode "regime_switch" (is_bidirectional).  The
    implicit group takes its Ticker's symbol (else *fallback_symbol*) and
    interval, and *direction* (default "long").  Raises ValueError when no
    symbol can be found.
    """
    if group.implicit:
        ticker = implicit_ticker(graph)
        params = (ticker.params or {}) if ticker is not None else {}
        symbol = str(params.get("symbol") or fallback_symbol or "").strip().upper()
        interval = str(params.get("interval") or "").strip() or "1d"
        mode = direction or "long"
    else:
        symbol, interval, mode = group.symbol or "", group.interval or "1d", group.direction
    if not symbol:
        raise ValueError(f"Output Group {group.name!r} has no Ticker symbol.")
    return {
        "symbol": symbol,
        "interval": interval_override or interval,
        "direction": "long" if mode == "regime_switch" else mode,
        "graph_direction_mode": mode,
    }


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
    return await run_in_threadpool(_seed_and_audit, body.legacy, forwarded_email(request))


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
    if _holds_code(env):
        await run_in_threadpool(_audit_saved, env, forwarded_email(request))
    return JSONResponse(status_code=201, content=env)


@router.put("/{graph_id}")
async def save_graph(graph_id: str, request: Request):
    body = await _read_body(request, SaveGraphBody)
    try:
        env = await run_in_threadpool(
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
    if _holds_code(env):
        await run_in_threadpool(_audit_saved, env, forwarded_email(request))
    return env


def _gate_copy(gate):
    """Each leg gets its own copy of a shared gate config."""
    return gate.model_copy(deep=True) if gate is not None else None


def _spawn_sync(mgr, graph_id: str, body: SpawnBody, email: Optional[str] = None) -> dict:
    """Validate every leg, then add every bot in one add_bots call."""
    from bot_manager import (BotConfig, BotManager, ReferenceUnavailableError, SymbolConflictError,
                             probe_references)
    from bot_runner import compile_bot_graph
    from nodebuilder.trading import nodes_groups

    # 1. The saved revision, compiled once.  With SL_CODE_NODES=0 a graph
    # that holds code is refused first, with its own code (W7).
    env, graph = load_graph_snapshot(graph_id, body.rev)
    refusal = code_disabled_detail(graph)
    if refusal is not None:
        raise SnapshotRefused(400, refusal)
    try:
        program = compile_bot_graph(graph)
    except GraphValidationError as exc:
        raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"]))

    # 2. One BotConfig per leg.
    names = [g.name for g in program.groups]
    configs = []
    for leg in body.legs:
        try:
            group = nodes_groups.group_named(program, leg.group)
        except KeyError:
            raise SnapshotRefused(400, snapshot_detail(
                "group_unknown",
                f"Graph {env['name']!r} has no Output Group {leg.group!r} "
                f"(groups: {', '.join(names)}).", groups=[leg.group]))
        refusal = weight_zero_detail(group)
        if refusal is not None:
            raise SnapshotRefused(400, refusal)
        if leg.direction is not None and not group.implicit:
            raise SnapshotRefused(400, snapshot_detail(
                "leg_invalid",
                f"Output Group {group.name!r} sets its own direction; leave the leg's "
                f"direction out.", groups=[group.name]))
        try:
            fields = group_bot_fields(graph, group, interval_override=leg.interval_override,
                                      direction=leg.direction)
            configs.append(BotConfig(
                strategy_name=leg.strategy_name or f"{env['name']} ▸ {group.name}",
                buy_rules=[], sell_rules=[],
                long_buy_rules=None, long_sell_rules=None,
                short_buy_rules=None, short_sell_rules=None,
                allocated_capital=leg.allocated_capital,
                data_source=leg.data_source,
                broker=leg.broker,
                kind="graph",
                graph=graph.model_copy(deep=True),
                graph_id=graph_id,
                graph_rev=env["rev"],
                graph_group=group.name,
                trading_hours=_gate_copy(body.trading_hours),
                skip_after_stop=_gate_copy(body.skip_after_stop),
                dynamic_sizing=_gate_copy(body.dynamic_sizing),
                **fields,
            ))
        except (ValueError, ValidationError) as exc:  # a bad symbol in the graph
            raise SnapshotRefused(400, snapshot_detail(
                "graph_invalid", f"Output Group {group.name!r}: {exc}",
                groups=[group.name], diagnostics=[]))

    # 3. Every leg checked (group, symbol, direction, settings), then added
    # in one call: all of them or none.
    for config in configs:
        try:
            BotManager._check_graph(config, program)
        except (GraphValidationError, ValidationError) as exc:
            raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"], groups=[config.graph_group]))

    # 4. Every reference Ticker each leg reads loads on that leg's data
    # source (F435 W5 LM-1), each (symbol, interval, source) fetched once.
    seen: dict = {}
    for config in configs:
        try:
            probe_references(config, program, seen=seen)
        except ReferenceUnavailableError as exc:
            raise SnapshotRefused(400, exc.detail)

    try:
        ids = mgr.add_bots(configs, programs=[program] * len(configs))
    except SymbolConflictError as exc:
        raise SnapshotRefused(400, snapshot_detail(
            "same_symbol_same_direction", str(exc), groups=list(exc.names)))
    except GraphValidationError as exc:
        raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"]))
    except ValueError as exc:  # the bot fund
        raise SnapshotRefused(400, snapshot_detail("fund", str(exc)))
    logger.info("spawned %d stopped bot(s) from graph %s rev %s: %s",
                len(ids), graph_id, env["rev"], ", ".join(ids))
    for bid in ids:  # the code audit trail (W7): one line per snippet per bot
        audit_graph("spawn", graph, email, bot_id=bid, graph_id=graph_id, rev=env["rev"])
    return {"bots": [
        {"bot_id": bid, "group": cfg.graph_group, "symbol": cfg.symbol,
         "direction": cfg.graph_direction_mode, "running": False}
        for bid, cfg in zip(ids, configs)
    ]}


@router.post("/{graph_id}/spawn", status_code=201)
async def spawn_bots(graph_id: str, request: Request):
    """One STOPPED bot per leg from saved revision ``rev`` (plan D7).

    Every leg is validated first; then one BotManager.add_bots call adds
    them all (one lock, one save) or none.  Nothing is started.
    """
    body = await _read_body(request, SpawnBody)
    from routes import bots as bots_route  # here, not at the top: routes.bots imports this module

    mgr = bots_route._get_manager()
    try:
        out = await run_in_threadpool(_spawn_sync, mgr, graph_id, body, forwarded_email(request))
    except SnapshotRefused as exc:
        return exc.response()
    return JSONResponse(status_code=201, content=out)


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
