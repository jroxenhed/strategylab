"""
routes/bot_health.py: GET /internal/bot-health for Zabbix (zabbix-agent2 on
the VM).

bot_watch.py already alerts from inside the backend, but it cannot say that the
backend itself is dead. Zabbix polls this endpoint every minute; a no-data
trigger covers a dead backend or VM, and the flat 0/1 fields below cover
stopped bots, a logged-out IB Gateway and bots that stop ticking.

Why /internal/ and not /api/: production nginx proxies only /api/ to the
backend (deploy/nginx/strategylab.conf), so this path never leaves the VM. On
top of that the route answers 404 unless the TCP peer is loopback AND the
request has no X-Forwarded-For / X-Real-IP header (nginx sets both).

Cheap on purpose: it reads in-memory state only. Bot classification reuses
bot_watch (should_be_working, HUNG_AFTER, the ET window) so the endpoint and
the watcher never disagree. Gateway state comes from gateway.alert_loop's last
result (a fresh read only if that is older than 3 minutes). IBKR health comes
from the HeartbeatMonitor cache; no broker call is made here.

There is no NYSE holiday calendar in the repo, so market_open is weekday
09:30 to 16:00 ET only; on a holiday it reads 1 and bots simply do not tick.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, time, timezone
from fastapi import APIRouter, HTTPException, Request

import bot_watch

router = APIRouter()

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
SYMBOLS_MAX_CHARS = 200

_STARTED = _time.time()


def market_open(now: datetime) -> bool:
    """Weekday 09:30 to 16:00 America/New_York (no holiday calendar)."""
    et = now.astimezone(bot_watch.ET)
    return et.weekday() < 5 and MARKET_OPEN <= et.time() < MARKET_CLOSE


def is_local_request(request: Request) -> bool:
    """Loopback peer and no proxy headers. Uvicorn may rewrite client.host from
    X-Forwarded-For, so the header check matters as much as the address."""
    host = request.client.host if request.client else None
    if host not in LOOPBACK:
        return False
    headers = request.headers
    return "x-forwarded-for" not in headers and "x-real-ip" not in headers


def bot_counts(manager, now: datetime) -> dict:
    """Bot fields of the report. Uses bot_watch's own definitions."""
    alive = bot_watch.alive_ids(manager)
    stalled = bot_watch.stalled_bots(manager, now)
    bots = list(manager.bots.items())

    running = 0
    expected = 0
    paused = 0
    ibkr_bots = 0
    oldest = -1
    for bid, (config, state) in bots:
        if state.status == "running" and bid in alive:
            running += 1
            last = bot_watch._parse(state.last_tick)
            if last is not None:
                oldest = max(oldest, int((now - last).total_seconds()))
        if bot_watch.should_be_working(state, now):
            expected += 1
            if config.broker == "ibkr":
                ibkr_bots += 1
        if state.pause_reason or state.status == "error":
            paused += 1

    symbols = sorted({manager.bots[bid][0].symbol for bid in stalled if bid in manager.bots})
    text = ",".join(symbols)
    if len(text) > SYMBOLS_MAX_CHARS:
        text = text[: SYMBOLS_MAX_CHARS - 3] + "..."

    return {
        "bots_total": len(bots),
        "bots_running": running,
        "bots_expected": expected,
        "bots_stalled": len(stalled),
        "bots_paused": paused,
        "stalled_symbols": text,
        "oldest_tick_age_s": oldest,
        "ibkr_bots": ibkr_bots,
    }


def broker_flags(available: list[str], health: dict[str, dict]) -> dict:
    """ibkr_healthy: IBKR is registered and its last heartbeat passed (no
    heartbeat yet counts as healthy, the same as HeartbeatMonitor.is_healthy).
    alpaca_healthy only when the monitor holds a value for it (Alpaca has no
    ping today, so it is normally left out)."""
    ibkr = health.get("ibkr")
    out = {"ibkr_healthy": int("ibkr" in available and (ibkr is None or bool(ibkr.get("healthy"))))}
    if "alpaca" in health:
        out["alpaca_healthy"] = int(bool(health["alpaca"].get("healthy")))
    return out


def build_report(manager, now: datetime, gateway_info: dict,
                 available: list[str], health: dict[str, dict],
                 started: float = _STARTED) -> dict:
    """The flat report. Pure: callers pass in every input."""
    bots = bot_counts(manager, now)
    window = bot_watch.in_window(now)
    gw_state = str(gateway_info.get("state") or "unknown")
    gw_logged_in = gw_state == "logged_in"
    brokers = broker_flags(available, health)

    # ok is the instant view; Zabbix triggers add durations on the fields.
    bad = (
        (window and (bots["bots_stalled"] > 0 or bots["bots_running"] < bots["bots_expected"]))
        or bots["bots_paused"] > 0
        or (bots["ibkr_bots"] > 0 and (not gw_logged_in or not brokers["ibkr_healthy"]))
    )
    ts = now.timestamp()
    return {
        "ok": int(not bad),
        "generated_at": int(ts),
        "market_open": int(market_open(now)),
        "check_window": int(window),
        **bots,
        "gateway_logged_in": int(gw_logged_in),
        "gateway_state": gw_state,
        **brokers,
        "backend_uptime_s": int(max(0.0, ts - started)),
    }


@router.get("/internal/bot-health", include_in_schema=False)
async def bot_health(request: Request):
    if not is_local_request(request):
        raise HTTPException(status_code=404, detail="Not Found")

    import gateway
    import routes.bots as bots_module
    from broker import get_available_brokers
    from broker_health_singleton import get_monitor

    manager = bots_module.bot_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="bot manager not ready")
    monitor = get_monitor()
    health = monitor.all_health() if monitor else {}
    try:
        info = await gateway.cached_state()
    except Exception:
        info = {"state": "unknown"}
    return build_report(manager, datetime.now(timezone.utc), info,
                        get_available_brokers(), health)
