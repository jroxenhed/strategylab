"""
routes/gateway.py — REST API for the IB Gateway panel (F428).

Endpoints:
  GET  /api/gateway/status         — parsed IBC log state + command port + api_connected
  POST /api/gateway/command/{cmd}  — RESTART | RECONNECTACCOUNT | RECONNECTDATA | STOP
"""

import logging

from fastapi import APIRouter, HTTPException

from gateway import VALID_COMMANDS, read_state, send_command

router = APIRouter(prefix="/api/gateway")
logger = logging.getLogger(__name__)


@router.get("/status")
async def get_status():
    return await read_state()


@router.post("/command/{cmd}")
async def post_command(cmd: str):
    cmd_upper = cmd.upper()
    if cmd_upper not in VALID_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown command: {cmd}. Valid: {sorted(VALID_COMMANDS)}",
        )
    try:
        reply = await send_command(cmd_upper)
    except ConnectionError:
        raise HTTPException(
            status_code=503,
            detail="IBC command server disabled — set CommandServerPort=7462 in config.ini",
        )
    return {"ok": True, "reply": reply}
