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
  DELETE /api/bots/{id}        — delete a stopped bot

NOTE: /api/bots/fund is registered before /{id} routes to prevent
FastAPI treating "fund" as a bot_id.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from bot_manager import BotConfig, BotManager
from models import RegimeConfig, LogicField, DirectionField

router = APIRouter(prefix="/api/bots")

# Module-level reference set by main.py lifespan handler
bot_manager: Optional[BotManager] = None


def _get_manager() -> BotManager:
    if bot_manager is None:
        raise HTTPException(status_code=503, detail="Bot manager not initialized")
    return bot_manager


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SetFundRequest(BaseModel):
    amount: float = Field(..., ge=0)


class UpdateBotRequest(BaseModel):
    allocated_capital: Optional[float] = Field(default=None, gt=0)
    strategy_name: Optional[str] = None
    buy_rules: Optional[list] = None
    sell_rules: Optional[list] = None
    buy_logic: Optional[LogicField] = None
    sell_logic: Optional[LogicField] = None
    long_buy_rules: Optional[list] = None
    long_sell_rules: Optional[list] = None
    long_buy_logic: Optional[LogicField] = None
    long_sell_logic: Optional[LogicField] = None
    short_buy_rules: Optional[list] = None
    short_sell_rules: Optional[list] = None
    short_buy_logic: Optional[LogicField] = None
    short_sell_logic: Optional[LogicField] = None
    max_spread_bps: Optional[float] = Field(default=None, ge=0)
    drawdown_threshold_pct: Optional[float] = Field(default=None, ge=0)
    borrow_rate_annual: Optional[float] = Field(default=None, ge=0)
    data_source: Optional[str] = None
    direction: Optional[DirectionField] = None
    broker: Optional[str] = None
    regime: Optional[RegimeConfig] = None



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
    """Start every stopped bot. Silently skips bots in error state."""
    mgr = _get_manager()
    started: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for bot_id, (_, state) in list(mgr.bots.items()):
        if state.status != "stopped":
            skipped.append(bot_id)
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
    mgr = _get_manager()
    mgr.reorder(req.order)
    return {"ok": True}


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

@router.post("")
def add_bot(config: BotConfig):
    """Create a bot. Uses BotConfig directly as the request schema to avoid
    field drift — any new BotConfig field is accepted automatically."""
    mgr = _get_manager()
    try:
        config.bot_id = ""  # server assigns the id
        bot_id = mgr.add_bot(config)
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


@router.patch("/{bot_id}")
def update_bot(bot_id: str, req: UpdateBotRequest):
    mgr = _get_manager()
    try:
        mgr.update_bot(bot_id, req.model_dump(exclude_none=True))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
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

@router.post("/{bot_id}/start")
async def start_bot(bot_id: str):
    mgr = _get_manager()
    try:
        mgr.start_bot(bot_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
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
