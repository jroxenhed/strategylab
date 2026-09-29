"""F445: alert when bots that ran recently stand still in market hours.

After the 2026-09-26 office move every bot sat stopped for two sessions and
nobody noticed: the VM was up and the Gateway was fine, only the bots were off.
Every minute between 09:35 and 16:00 ET on weekdays, list the bots that ticked
in the last seven days but are not working now, and send one ntfy push and one
Slack line for bots not yet reported that ET day. "Not working" means:

- the bot's task is not alive (stopped, or the runner died and left status
  "running" behind), or
- the task is alive but has not ticked for HUNG_AFTER.

Skipped: bots the user stopped (BotState.user_stopped) and paused bots (a
pause_reason sends its own notify_error when it is set). notify/slack go
through asyncio.create_task so a slow webhook never stalls the loop, the same
as gateway.alert_loop.

Limits: this runs inside the backend, so it cannot report a backend or VM that
is down. The once-a-day memory is in-process, so a restart during the session
reports a still-stalled bot again. Opt out with BOT_WATCH_ALERTS=0.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CHECK_FROM = time(9, 35)          # five minutes into the session, after F430 auto-resume
CHECK_UNTIL = time(16, 0)
LOOKBACK = timedelta(days=7)      # about five sessions
HUNG_AFTER = timedelta(minutes=30)  # 30x the longest POLL_INTERVALS value (60 s)
POLL_SECS = 60

_pending: set[asyncio.Task] = set()  # keep fire-and-forget alert tasks referenced


def _enabled() -> bool:
    return os.environ.get("BOT_WATCH_ALERTS", "1") not in ("0", "false", "False", "")


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def stalled_bots(manager, now: datetime) -> dict[str, str]:
    """bot_id -> label for each bot that ticked within LOOKBACK but is not working
    now. `manager` needs .bots {id: (BotConfig, BotState)} and .tasks {id: Task}."""
    alive = {bid for bid, task in manager.tasks.items() if not task.done()}
    out: dict[str, str] = {}
    for bid, (config, state) in list(manager.bots.items()):
        if state.user_stopped or state.pause_reason:
            continue
        last = _parse(state.last_tick)
        if last is None or now - last > LOOKBACK:
            continue
        if bid in alive and state.status == "running":
            if now - last <= HUNG_AFTER:
                continue
            why = f"no tick for {int((now - last).total_seconds() // 60)} min"
        elif state.status == "running":
            why = "runner died"
        else:
            why = state.error_message or state.status
        out[bid] = f"{config.strategy_name} ({config.symbol} {config.direction} {config.interval}): {why}"
    return out


def in_window(now: datetime) -> bool:
    et = now.astimezone(ET)
    return et.weekday() < 5 and CHECK_FROM <= et.time() < CHECK_UNTIL


def _fire(coro) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def check_once(manager, now: datetime, reported: dict[str, date]) -> list[str]:
    """Alert on stalled bots not yet reported this ET day. `reported` maps
    bot_id -> ET date of its last alert and is updated in place."""
    from notifications import notify, slack
    today = now.astimezone(ET).date()
    stalled = stalled_bots(manager, now)
    new = [label for bid, label in stalled.items() if reported.get(bid) != today]
    if new:
        text = f"{len(new)} bot(s) ran in the last 7 days but are not working: " + "; ".join(new)
        logger.warning("F445 bot watch: %s", text)
        _fire(notify(title="StrategyLab bots not running", message=text,
                     priority="high", tags="warning"))
        _fire(slack(f"StrategyLab bots not running: {text}"))
    for bid in stalled:
        reported[bid] = today
    return new


async def watch_loop(manager) -> None:
    """Background task started in main.py's lifespan; cancelled on shutdown.
    Sleeps first so F430 auto-resumed runners have set their status."""
    if not _enabled():
        return
    reported: dict[str, date] = {}
    while True:
        await asyncio.sleep(POLL_SECS)
        try:
            now = datetime.now(timezone.utc)
            if in_window(now):
                check_once(manager, now, reported)
        except Exception:
            logger.exception("bot_watch: iteration failed")
