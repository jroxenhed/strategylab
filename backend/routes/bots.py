"""
routes/bots.py — REST API for the live trading bot system.

Endpoints:
  GET  /api/bots/fund          — get fund status
  PUT  /api/bots/fund          — set bot fund amount
  POST /api/bots               — add a new bot (stopped)
  GET  /api/bots               — list all bots + fund status
  GET  /api/bots/{id}          — full bot detail
  POST /api/bots/{id}/start    — start live polling
  POST /api/bots/{id}/stop     — stop polling (?close=true to also close position)
  POST /api/bots/{id}/backtest — run backtest with bot's config
  POST /api/bots/{id}/graph_update — move a graph bot to a newer saved rev
  DELETE /api/bots/{id}        — delete a stopped bot

A graph bot's graph is compiled when it is added or replaced.  A bad graph
returns HTTP 400 {"detail": <message>, "node_id": <id or null>}, the same
shape as /api/nodebuilder/backtest, so the editor can highlight the node.

A graph bot can be added from a saved graph: POST with kind "graph",
graph_id, graph_rev and graph_group, and no graph.  The server loads that
revision (the same loader as spawn) and takes the symbol and direction from
the group (F435 W5 5.D).

NOTE: /api/bots/fund is registered before /{id} routes to prevent
FastAPI treating "fund" as a bot_id.
"""

import logging
from typing import Any, Optional
from fastapi import APIRouter, Body, HTTPException, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from bot_manager import (BotConfig, BotManager, InPositionError, ReferenceUnavailableError,
                         probe_references)
from models import RegimeConfig, LogicField, DirectionField, OptionalBoundedRuleList
from nodebuilder.models import Graph, GraphValidationError

router = APIRouter(prefix="/api/bots")
logger = logging.getLogger(__name__)

# Module-level reference set by main.py lifespan handler
bot_manager: Optional[BotManager] = None


def _get_manager() -> BotManager:
    if bot_manager is None:
        raise HTTPException(status_code=503, detail="Bot manager not initialized")
    return bot_manager


def _graph_error(exc: GraphValidationError) -> JSONResponse:
    """The 400 body for a bad graph: {detail, node_id}."""
    return JSONResponse(status_code=400, content={"detail": str(exc), "node_id": exc.node_id})


def _parse_body(model, payload: dict):
    """Validate a request body against model.

    The body is parsed here rather than by FastAPI, because a cycle or a
    dangling wire in a graph is raised while the Graph model is built, and
    under FastAPI's own parsing that became a bare 500.  Returns the model,
    or a 400 JSONResponse for a bad graph.  Any other bad field keeps
    FastAPI's usual 422 shape.
    """
    try:
        return model.model_validate(payload)
    except GraphValidationError as exc:
        return _graph_error(exc)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SetFundRequest(BaseModel):
    amount: float = Field(..., ge=0)


class UpdateBotRequest(BaseModel):
    # Unknown fields are refused (422), not dropped: a PATCH of, say,
    # stop_loss_pct used to answer ok and change nothing (Key Bugs Fixed:
    # silent drop of bot config fields).
    model_config = ConfigDict(extra="forbid")

    allocated_capital: Optional[float] = Field(default=None, gt=0)
    strategy_name: Optional[str] = None
    buy_rules: OptionalBoundedRuleList
    sell_rules: OptionalBoundedRuleList
    buy_logic: Optional[LogicField] = None
    sell_logic: Optional[LogicField] = None
    long_buy_rules: OptionalBoundedRuleList
    long_sell_rules: OptionalBoundedRuleList
    long_buy_logic: Optional[LogicField] = None
    long_sell_logic: Optional[LogicField] = None
    short_buy_rules: OptionalBoundedRuleList
    short_sell_rules: OptionalBoundedRuleList
    short_buy_logic: Optional[LogicField] = None
    short_sell_logic: Optional[LogicField] = None
    max_spread_bps: Optional[float] = Field(default=None, ge=0)
    drawdown_threshold_pct: Optional[float] = Field(default=None, ge=0)
    borrow_rate_annual: Optional[float] = Field(default=None, ge=0)
    data_source: Optional[str] = None
    direction: Optional[DirectionField] = None
    broker: Optional[str] = None
    regime: Optional[RegimeConfig] = None
    graph: Optional[Graph] = None


# Every BotConfig field is either editable through UpdateBotRequest (the
# PATCH body) or listed here with the reason it is not.  A test
# (tests/test_update_bot_request_coverage.py) fails when a new BotConfig
# field is in neither, so no field can be dropped without a decision (Key
# Bugs Fixed: silent drop of bot config fields).  UpdateBotRequest forbids
# unknown fields, so a PATCH of a listed field is a 422, never a no-op.
PATCH_DENYLIST: dict[str, str] = {
    # Identity: a different value is a different bot.
    "bot_id": "assigned by the server",
    "symbol": "a new symbol is a new bot",
    "interval": "a new interval is a new bot",
    "kind": "a rule bot and a graph bot are different bots",
    # Set when the bot is created; not edited after.
    "position_size": "set at create (a graph bot takes it from its graph)",
    "stop_loss_pct": "set at create (a graph bot takes it from its graph)",
    "trailing_stop": "set at create (a graph bot takes it from its graph)",
    "max_bars_held": "set at create (a graph bot takes it from its graph)",
    "dynamic_sizing": "set at create",
    "skip_after_stop": "set at create",
    "trading_hours": "set at create",
    "slippage_bps": "set at create (a graph bot takes it from its graph)",
    "long_stop_loss_pct": "set at create (rule regime bots)",
    "short_stop_loss_pct": "set at create (rule regime bots)",
    "long_trailing_stop": "set at create (rule regime bots)",
    "short_trailing_stop": "set at create (rule regime bots)",
    "long_max_bars_held": "set at create (rule regime bots)",
    "short_max_bars_held": "set at create (rule regime bots)",
    "long_position_size": "set at create (rule regime bots)",
    "short_position_size": "set at create (rule regime bots)",
    # Has its own route.
    "pnl_epoch": "POST /api/bots/{id}/reset-pnl",
    # A saved graph's revision: graph and graph_rev change together, only
    # through POST /api/bots/{id}/graph_update (plan D7).
    "graph_id": "set at spawn or create; a bot never changes graph",
    "graph_rev": "POST /api/bots/{id}/graph_update",
    "graph_group": "set at spawn or create; a bot never changes group",
    "graph_direction_mode": "comes from the group; graph_update refuses a change",
}


class GraphUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_id: str
    rev: int


class ReorderBotsRequest(BaseModel):
    order: list[str]


# ---------------------------------------------------------------------------
# Fund endpoints (must be before /{bot_id} routes)
# ---------------------------------------------------------------------------

@router.get("/fund")
def get_fund():
    return _get_manager().get_fund_status()


@router.put("/fund")
def set_fund(req: SetFundRequest):
    mgr = _get_manager()
    try:
        mgr.set_bot_fund(req.amount)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return mgr.get_fund_status()


# ---------------------------------------------------------------------------
# Bulk actions (must be before /{bot_id} routes)
# ---------------------------------------------------------------------------

@router.post("/start-all")
async def start_all_bots():
    """Start every stopped bot. Silently skips bots in error state.

    Each graph bot is compiled and its reference frames fetched in the
    thread pool (BotManager.prepare_start, F435 W5 LM-10), one bot at a
    time, so starting many graph bots never blocks the running bots'
    polling loop."""
    mgr = _get_manager()
    started: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "stopped":
            skipped.append(bot_id)
            continue
        try:
            await run_in_threadpool(mgr.prepare_start, bot_id)
        except GraphValidationError as e:
            mgr.refuse_start(bot_id, e)
            failed.append({"bot_id": bot_id, "error": str(e)})
            continue
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
            continue
        try:
            mgr.start_bot(bot_id)
            started.append(bot_id)
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
    return {"started": started, "skipped": skipped, "failed": failed}


@router.post("/stop-all")
def stop_all_bots():
    """Stop every running bot. Leaves positions open."""
    mgr = _get_manager()
    stopped: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "running":
            continue
        try:
            mgr.stop_bot(bot_id, close_position=False)
            stopped.append(bot_id)
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
    return {"stopped": stopped, "failed": failed}


@router.put("/reorder")
def reorder_bots(req: ReorderBotsRequest):
    """Persist a new display order for the bot list."""
    try:
        mgr = _get_manager()
        mgr.reorder(req.order)
        return {"ok": True}
    except Exception:
        logger.exception("/api/bots/reorder failed")
        raise HTTPException(status_code=500, detail="reorder failed")


@router.post("/stop-and-close-all")
def stop_and_close_all_bots():
    """Stop every running bot AND flatten open positions at market."""
    mgr = _get_manager()
    closed: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "running":
            continue
        try:
            mgr.stop_bot(bot_id, close_position=True)
            closed.append(bot_id)
        except Exception as e:
            failed.append({"bot_id": bot_id, "error": str(e)})
    return {"closed": closed, "failed": failed}


# ---------------------------------------------------------------------------
# Bot CRUD
# ---------------------------------------------------------------------------

def _from_saved_graph(config: BotConfig):
    """A graph bot added by graph_id (no graph in the body): the config with
    the saved revision as its graph, and the symbol, direction and
    graph_direction_mode its Output Group decides.  The interval stays the
    body's (the bot may run another interval than the group).  Returns the
    config, or a JSONResponse refusal.  Reads a file and compiles: runs in
    the thread pool (this route is a plain def)."""
    from bot_runner import compile_bot_graph
    from nodebuilder.trading import nodes_groups
    from routes.graphs import (SnapshotRefused, graph_invalid_detail, group_bot_fields,
                               load_graph_snapshot, snapshot_detail, weight_zero_detail)

    if config.graph is not None:
        return JSONResponse(status_code=400, content={"detail": snapshot_detail(
            "leg_invalid", "Send graph_id, graph_rev and graph_group, or graph; not both.")})
    if config.graph_rev is None:
        return JSONResponse(status_code=400, content={"detail": snapshot_detail(
            "leg_invalid", "graph_rev is required with graph_id.")})
    try:
        env, graph = load_graph_snapshot(config.graph_id, config.graph_rev)
        try:
            program = compile_bot_graph(graph)
        except GraphValidationError as exc:
            raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"]))
        try:
            group = nodes_groups.group_named(program, config.graph_group)
        except KeyError:
            names = ", ".join(g.name for g in program.groups)
            message = (f"Graph {env['name']!r} has no Output Group {config.graph_group!r} "
                       f"(groups: {names})." if config.graph_group else
                       f"Graph {env['name']!r} has several Output Groups ({names}); "
                       f"name one in graph_group.")
            raise SnapshotRefused(400, snapshot_detail(
                "group_unknown", message,
                groups=[config.graph_group] if config.graph_group else []))
        refusal = weight_zero_detail(group)
        if refusal is not None:
            raise SnapshotRefused(400, refusal)
        try:
            fields = group_bot_fields(graph, group, interval_override=config.interval,
                                      direction=config.direction, fallback_symbol=config.symbol)
            return BotConfig.model_validate({
                **config.model_dump(exclude={"graph"}), **fields,
                "graph": graph, "graph_rev": env["rev"], "graph_group": group.name,
            })
        except (ValueError, ValidationError) as exc:
            raise SnapshotRefused(400, snapshot_detail(
                "graph_invalid", f"Output Group {group.name!r}: {exc}", diagnostics=[]))
    except SnapshotRefused as exc:
        return exc.response()


@router.post("", responses={400: {"description": "Bad config, or bad graph: {detail, node_id}"}})
def add_bot(payload: dict[str, Any] = Body(...)):
    """Create a bot. Validates the body with BotConfig directly to avoid
    field drift — any new BotConfig field is accepted automatically.
    A graph bot's graph is compiled first; a bad graph returns 400 with node_id.
    A graph bot with graph_id is built from that saved revision (see
    _from_saved_graph)."""
    mgr = _get_manager()
    config = _parse_body(BotConfig, payload)
    if isinstance(config, JSONResponse):
        return config
    # A rule bot runs no saved graph, so it cannot carry a link to one
    # (F435 W5 DI-07): the bot list would name a graph it does not run, and
    # PATCH would refuse its direction "because it runs a saved graph".
    linked = [k for k in BotManager.W5_CONFIG_FIELDS if getattr(config, k) is not None]
    if config.kind != "graph" and linked:
        from routes.graphs import snapshot_detail
        return JSONResponse(status_code=400, content={"detail": snapshot_detail(
            "leg_invalid", f"A rule bot runs no saved graph; leave out {', '.join(linked)}.")})
    if config.kind == "graph" and config.graph_id:
        config = _from_saved_graph(config)
        if isinstance(config, JSONResponse):
            return config
    # Every reference Ticker the bot's group reads must load on the bot's
    # data source, or the bot could never work out a signal (LM-1).  A graph
    # that does not compile is left to add_bot, which answers with node_id.
    try:
        probe_references(config)
    except ReferenceUnavailableError as exc:
        return JSONResponse(status_code=400, content={"detail": exc.detail})
    except (GraphValidationError, ValidationError, ValueError, TypeError):
        pass  # add_bot's own check refuses it with the usual body
    try:
        config.bot_id = ""  # server assigns the id
        bot_id = mgr.add_bot(config)
    except GraphValidationError as e:
        return _graph_error(e)
    except (ValueError, Exception) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"bot_id": bot_id}


@router.get("")
def list_bots():
    mgr = _get_manager()
    return {
        "fund": mgr.get_fund_status(),
        "bots": mgr.list_bots(),
    }


@router.get("/{bot_id}")
def get_bot(bot_id: str):
    mgr = _get_manager()
    try:
        config, state = mgr.get_bot(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "config": config.model_dump(),
        "state": state.to_dict(),
    }


@router.patch("/{bot_id}", responses={400: {"description": "Bad update, or bad graph: {detail, node_id}"}})
def update_bot(bot_id: str, payload: dict[str, Any] = Body(...)):
    mgr = _get_manager()
    req = _parse_body(UpdateBotRequest, payload)
    if isinstance(req, JSONResponse):
        return req

    # Mid-position graph-swap guard: only check when a graph is actually
    # being supplied and differs.  The same guard as graph_update (F435 W5
    # LM-3): entry_price, an entry in flight, and the broker's own position
    # (entry_price misses one after a Stop that kept it; the next Start
    # would resume it under the new graph).  A saved-graph bot (graph_id)
    # never takes a graph here (BotManager.update_bot refuses it), and a
    # running bot is refused there too, so only a stopped legacy bot asks
    # the broker.  This route is a plain def: the broker call runs in the
    # thread pool.
    if req.graph is not None:
        from routes.graphs import SnapshotRefused, snapshot_detail
        try:
            config, state = mgr.get_bot(bot_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        current_graph_dump = config.graph.model_dump(mode='json') if config.graph is not None else None
        if current_graph_dump != req.graph.model_dump(mode='json'):
            if state.entry_price is not None or state.entry_in_flight:
                return JSONResponse(status_code=409, content={"detail": snapshot_detail(
                    "in_position", "Bot is in position; close before swapping graph.")})
            if not config.graph_id and state.status != "running":
                # A bad graph is a 400 whatever the broker says: check it
                # first, as BotManager.update_bot will (same merged config).
                try:
                    BotManager._check_graph(BotConfig(**{**config.model_dump(),
                                                          **req.model_dump(exclude_none=True)}))
                except GraphValidationError as e:
                    return _graph_error(e)
                except (ValidationError, ValueError):
                    pass  # update_bot answers these with its usual 400
                try:
                    _refuse_broker_position(config)
                except SnapshotRefused as exc:
                    return exc.response()

    try:
        mgr.update_bot(bot_id, req.model_dump(exclude_none=True))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GraphValidationError as e:
        return _graph_error(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/{bot_id}")
def delete_bot(bot_id: str):
    mgr = _get_manager()
    try:
        mgr.delete_bot(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Bot actions
# ---------------------------------------------------------------------------

def _graph_update_candidate(mgr: BotManager, bot_id: str, graph_id: str, rev: int):
    """Load revision *rev* for a graph_update and check it against the bot
    (thread pool: reads a file and compiles).  Returns (graph, program), or
    raises SnapshotRefused with the contract's codes."""
    from bot_runner import compile_bot_graph
    from nodebuilder.trading import nodes_groups
    from routes.graphs import (SnapshotRefused, graph_invalid_detail, group_bot_fields,
                               load_graph_snapshot, snapshot_detail)

    config, _state = mgr.get_bot(bot_id)
    env, graph = load_graph_snapshot(graph_id, rev)
    try:
        program = compile_bot_graph(graph)
    except GraphValidationError as exc:
        raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"]))
    try:
        group = nodes_groups.group_named(program, config.graph_group)
    except KeyError:
        raise SnapshotRefused(400, snapshot_detail(
            "group_missing", f"Group {config.graph_group} no longer exists in rev {rev}."))
    try:
        fields = group_bot_fields(graph, group, direction=config.direction,
                                  fallback_symbol=config.symbol)
    except ValueError as exc:
        raise SnapshotRefused(400, snapshot_detail("graph_invalid", str(exc), diagnostics=[]))
    if fields["symbol"] != str(config.symbol).strip().upper():
        raise SnapshotRefused(400, snapshot_detail(
            "symbol_changed", f"In rev {rev} group {group.name} trades {fields['symbol']}, "
            f"not {config.symbol}.  Spawn a new bot instead."))
    if fields["graph_direction_mode"] != (config.graph_direction_mode or config.direction):
        raise SnapshotRefused(400, snapshot_detail(
            "direction_changed", f"In rev {rev} group {group.name} is "
            f"{fields['graph_direction_mode']}.  Spawn a new bot instead."))
    # The bot keeps its own interval, so a rev that moves the group to
    # another interval would run the new logic on the old bars, unlike the
    # rev's own backtest (F435 W5 LM-7): refuse it like a symbol change.
    old_interval = _group_interval(config)
    if fields["interval"] != (old_interval or config.interval):
        raise SnapshotRefused(400, snapshot_detail(
            "interval_changed", f"In rev {rev} group {group.name} runs on {fields['interval']}, "
            f"not {old_interval or config.interval}.  Spawn a new bot instead."))
    candidate = BotConfig.model_validate(
        {**config.model_dump(exclude={"graph"}), "graph": graph, "graph_rev": rev})
    try:
        BotManager._check_graph(candidate, program)
    except GraphValidationError as exc:
        code = getattr(exc, "code", None)
        if code in ("group_missing", "symbol_changed", "direction_changed"):
            raise SnapshotRefused(400, snapshot_detail(code, str(exc)))
        raise SnapshotRefused(400, graph_invalid_detail(exc, env["graph"]))
    _refuse_broker_position(config)
    return graph, program


def _group_interval(config: BotConfig) -> Optional[str]:
    """The interval the bot's group has in the bot's current graph (not the
    bot's own interval, which a spawn may override), or None when that
    graph no longer compiles or has no such group.  Thread pool only."""
    from bot_runner import compile_bot_graph
    from nodebuilder.trading import nodes_groups
    from routes.graphs import group_bot_fields

    if config.graph is None:
        return None
    try:
        program = compile_bot_graph(config.graph)
        group = nodes_groups.group_named(program, config.graph_group)
        return group_bot_fields(config.graph, group, direction=config.direction,
                                fallback_symbol=config.symbol)["interval"]
    except (GraphValidationError, KeyError, ValueError, ValidationError):
        return None


def _refuse_broker_position(config: BotConfig) -> None:
    """Refuse a graph_update when the broker holds the bot's position (F435
    W5 LM-3).  entry_price misses one after a Stop that kept the position
    (the next Start resumes it under the new graph) and during an entry's
    fill poll.  A one-sided bot owns the position on its side of the
    symbol; a bot that trades both sides owns either side.  When the
    broker cannot be asked, refuse too: 503 broker_unavailable.  Thread
    pool only (a broker call)."""
    import bot_manager
    from routes.graphs import SnapshotRefused, snapshot_detail

    symbol = str(config.symbol).strip().upper()
    try:
        provider = bot_manager.get_trading_provider(config.broker)
        positions = provider.get_positions()
    except Exception as exc:
        raise SnapshotRefused(503, snapshot_detail(
            "broker_unavailable",
            f"Could not ask {config.broker} whether this bot holds a position ({exc}).  "
            f"Try again."))
    for pos in positions or []:
        if str(pos.get("symbol", "")).upper() != symbol:
            continue
        if config.is_bidirectional or pos.get("side") == config.direction:
            raise SnapshotRefused(409, snapshot_detail(
                "in_position", f"{config.broker} holds a {pos.get('side')} position on {symbol} "
                f"for this bot.  Close it before updating the graph."))


@router.post("/{bot_id}/graph_update", responses={
    400: {"description": "group_missing | symbol_changed | direction_changed | interval_changed "
                         "| graph_invalid | graph_mismatch"},
    409: {"description": "in_position | rev_conflict"},
    503: {"description": "broker_unavailable (the broker could not be asked for a position)"},
})
async def graph_update(bot_id: str, payload: dict[str, Any] = Body(...)):
    """Move a graph bot to saved revision ``rev`` of its graph (plan D7).

    Sets graph and graph_rev together, never through the generic PATCH
    (UpdateBotRequest would drop the graph fields).  Refused while the bot
    holds a position, and when the bot's group is gone or now trades another
    symbol or direction (spawn a new bot for that).  A running bot without
    a position takes the new graph on its next tick.
    """
    from routes.graphs import SnapshotRefused, snapshot_detail

    body = _parse_body(GraphUpdateRequest, payload)
    if isinstance(body, JSONResponse):
        return body
    mgr = _get_manager()
    try:
        config, state = mgr.get_bot(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if config.kind != "graph" or not config.graph_id:
        return JSONResponse(status_code=400, content={"detail": snapshot_detail(
            "graph_invalid", "This bot does not run a saved graph.", diagnostics=[])})
    if body.graph_id != config.graph_id:
        return JSONResponse(status_code=400, content={"detail": snapshot_detail(
            "graph_mismatch", f"This bot runs graph {config.graph_id}, not {body.graph_id}.")})
    in_position = JSONResponse(status_code=409, content={"detail": {"code": "in_position"}})
    # entry_in_flight: an entry order is out and its fill not yet booked
    # (LM-3).  The broker's own position is checked in the thread pool.
    if state.entry_price is not None or state.entry_in_flight:
        return in_position
    try:
        graph, program = await run_in_threadpool(
            _graph_update_candidate, mgr, bot_id, body.graph_id, body.rev)
    except SnapshotRefused as exc:
        return exc.response()
    except KeyError as e:  # deleted while loading
        raise HTTPException(status_code=404, detail=str(e))
    # Back on the event loop: nothing is awaited between this in-position
    # check and setting graph and graph_rev, so the bot's own tick cannot
    # open a position in between.
    try:
        mgr.apply_graph_update(bot_id, graph, body.rev)
    except InPositionError:
        return in_position
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await run_in_threadpool(mgr.save)
    return {"bot_id": bot_id, "graph_rev": body.rev}


@router.post("/{bot_id}/start", responses={
    400: {"description": "Bad graph {detail, node_id}, reference_unavailable, or already running"},
})
async def start_bot(bot_id: str):
    """Start a stopped bot.  The graph compile, group check and reference
    probe run in the thread pool (BotManager.prepare_start, F435 W5 LM-10,
    LM-1); only the task is created on the event loop."""
    mgr = _get_manager()
    try:
        await run_in_threadpool(mgr.prepare_start, bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GraphValidationError as e:
        mgr.refuse_start(bot_id, e)
        return _graph_error(e)
    except ReferenceUnavailableError as e:
        return JSONResponse(status_code=400, content={"detail": e.detail})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        mgr.start_bot(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GraphValidationError as e:
        return _graph_error(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "status": "running"}


@router.post("/{bot_id}/stop")
def stop_bot(bot_id: str, close: bool = False):
    mgr = _get_manager()
    try:
        mgr.stop_bot(bot_id, close_position=close)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "status": "stopped"}


@router.post("/{bot_id}/buy")
def manual_buy(bot_id: str):
    mgr = _get_manager()
    try:
        result = mgr.manual_buy(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except GraphValidationError as e:
        return _graph_error(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/{bot_id}/reset-pnl")
def reset_bot_pnl(bot_id: str):
    mgr = _get_manager()
    try:
        epoch = mgr.reset_pnl(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, "pnl_epoch": epoch}


@router.post("/{bot_id}/backtest")
def backtest_bot(bot_id: str, background_tasks: BackgroundTasks):
    mgr = _get_manager()
    try:
        mgr.get_bot(bot_id)  # validates existence
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Run in background so the request returns immediately
    background_tasks.add_task(mgr.backtest_bot, bot_id)
    return {"ok": True, "status": "backtesting"}
