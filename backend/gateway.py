"""
gateway.py — IB Gateway state via IBC log tailing + IBC command port (F428).

read_state() parses the newest IBC log file for the last-seen marker string
(real markers, listed below, were grepped from ~/ibc/logs/*.txt on the dev
Mac). send_command() talks to the IBC CommandServer (a plain-text line
protocol on 127.0.0.1:7462 by default) to trigger RESTART / RECONNECTACCOUNT
/ RECONNECTDATA / STOP.

alert_loop() polls read_state() every 60s and fires notify()/slack() on
transitions into a "needs a human" state, re-alerting every 30 min while
stuck there. Per CLAUDE.md ("Key Bugs Fixed" — fire-and-forget notifications
must use asyncio.create_task, never await, inside a polling loop), the
notify/slack calls are scheduled via asyncio.create_task so a slow or down
ntfy.sh/Slack webhook never stalls the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_COMMAND_HOST = "127.0.0.1"
DEFAULT_COMMAND_PORT = 7462

VALID_COMMANDS = {"RESTART", "RECONNECTACCOUNT", "RECONNECTDATA", "STOP"}

# Markers are matched as substrings against each log line, in this order;
# the LAST marker seen while scanning the file top-to-bottom wins (log lines
# are chronological). "Second Factor" is handled separately, case-insensitive,
# per the plan (assumed string — not confirmed verbatim in captured logs).
_MARKERS: list[tuple[str, str]] = [
    ("logged_in", "Login has completed"),
    ("relogin_required", "detected dialog entitled: Re-login is required"),
    ("locked_out", "Too many failed login attempts"),
    ("bad_credentials", "Unrecognized Username or Password"),
    ("restarting", "Restart in progress"),
    ("restarting", "Shutdown progress"),
    ("logging_in", "Login dialog WINDOW_OPENED: LoginState is LOGGED_OUT"),
    ("logging_in", "Login attempt"),
]

# "YYYY-MM-DD HH:MM:SS:mmm IBC: rest..." — IBC's log line timestamp format.
_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):\d{3}\s+(.*)$")

# Down alerts + staleness check: no fresh log activity within this window.
_STALE_SECS = 15 * 60


def _get_log_dir() -> Path:
    env = os.environ.get("IBC_LOG_DIR")
    if env:
        return Path(env)
    default = Path("/var/log/ibc")
    if default.exists():
        return default
    return Path.home() / "ibc" / "logs"


def _newest_log_file(log_dir: Path) -> Optional[Path]:
    if not log_dir.exists():
        return None
    files = list(log_dir.glob("ibc-*_GATEWAY-*_*.txt"))
    if not files:
        return None
    return max(files, key=lambda p: (p.stat().st_mtime, p.name))


def _match_marker(text: str) -> Optional[str]:
    if "second factor" in text.lower():
        return "awaiting_2fa"
    for state, marker in _MARKERS:
        if marker in text:
            return state
    return None


def _split_line(line: str) -> tuple[Optional[str], str]:
    m = _LINE_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    return None, line


def _parse_ts_to_iso(ts: str) -> Optional[str]:
    """Parse an IBC log timestamp ('YYYY-MM-DD HH:MM:SS', the Gateway host's
    local wall clock, no timezone) into a timezone-aware ISO 8601 string
    (CORR-03). astimezone() with no argument attaches the *host's* local
    offset to the naive datetime, so the frontend's `new Date(iso)` parses
    it unambiguously regardless of the viewing browser's timezone."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return None
    return dt.isoformat()


def _epoch_to_iso(epoch: float) -> str:
    """Unix epoch seconds -> timezone-aware ISO 8601 string, same convention
    as _parse_ts_to_iso()."""
    return datetime.fromtimestamp(epoch).astimezone().isoformat()


def parse_log_text(text: str) -> tuple[str, Optional[str], list[str]]:
    """Parse raw IBC log text. Returns (state, since, last_lines[<=12]).
    `since` is a timezone-aware ISO 8601 string (see _parse_ts_to_iso), or
    None if no marker matched."""
    lines = text.splitlines()
    last_lines = lines[-12:]
    state = "unknown"
    since: Optional[str] = None
    for line in lines:
        ts, rest = _split_line(line)
        marker_state = _match_marker(rest)
        if marker_state is not None:
            state = marker_state
            since = _parse_ts_to_iso(ts) if ts is not None else None
    return state, since, last_lines


async def _check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def read_state() -> dict:
    """Read Gateway state from the newest IBC log file + command port + broker registry."""
    log_dir = _get_log_dir()
    log_file = _newest_log_file(log_dir)

    state = "unknown"
    since: Optional[str] = None
    last_lines: list[str] = []
    if log_file is not None:
        try:
            text = log_file.read_text(errors="replace")
            state, since, last_lines = parse_log_text(text)
        except OSError as exc:
            logger.warning("gateway: failed to read log file %s: %s", log_file, exc)

    from broker import get_available_brokers

    api_connected = "ibkr" in get_available_brokers()

    host = os.environ.get("IBC_COMMAND_HOST", DEFAULT_COMMAND_HOST)
    port = int(os.environ.get("IBC_COMMAND_PORT", str(DEFAULT_COMMAND_PORT)))
    reachable = await _check_port(host, port)

    now = time.time()
    if log_file is None:
        stale_or_missing = True
    else:
        stale_or_missing = (now - log_file.stat().st_mtime) > _STALE_SECS
    if stale_or_missing and not api_connected:
        state = "down"
        # `since` from parse_log_text() means "since the last marker fired"
        # (e.g. last login) — not "since it went down". Re-point it at the
        # log file's mtime (when it went quiet), or None if there's no file,
        # so the panel never pairs "Down" with an unrelated login timestamp.
        since = _epoch_to_iso(log_file.stat().st_mtime) if log_file is not None else None

    return {
        "state": state,
        "since": since,
        "log_file": str(log_file) if log_file is not None else None,
        "last_lines": last_lines,
        "api_connected": api_connected,
        "command_port": {"host": host, "port": port, "reachable": reachable},
    }


async def send_command(cmd: str) -> str:
    """Send a command to the IBC CommandServer. Raises ValueError (bad cmd) or
    ConnectionError (port unreachable) — routes/gateway.py maps these to 400/503."""
    if cmd not in VALID_COMMANDS:
        raise ValueError(f"Unknown command: {cmd}")

    host = os.environ.get("IBC_COMMAND_HOST", DEFAULT_COMMAND_HOST)
    port = int(os.environ.get("IBC_COMMAND_PORT", str(DEFAULT_COMMAND_PORT)))

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3.0
        )
    except Exception as exc:
        raise ConnectionError(f"IBC command server unreachable at {host}:{port}") from exc

    try:
        writer.write(f"{cmd}\n".encode())
        await writer.drain()
        try:
            reply_bytes = await asyncio.wait_for(reader.readline(), timeout=3.0)
        except asyncio.TimeoutError:
            reply_bytes = b""
        reply = reply_bytes.decode(errors="replace").strip()
        try:
            writer.write(b"EXIT\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    return reply


# ---------------------------------------------------------------------------
# Alert loop
# ---------------------------------------------------------------------------

NEEDS_HUMAN = {"awaiting_2fa", "relogin_required", "locked_out", "bad_credentials", "down"}
REALERT_SECS = 30 * 60


class AlertState:
    """Mutable transition-tracking state for the alert loop. A fresh instance
    per test gives deterministic, network-free unit tests; production uses
    the module-level singleton below.

    Known limitation (REL-02, deferred): this state is in-memory only and does
    not survive a backend restart (systemd auto-restart, or a redeploy via
    install.sh). If the Gateway is stuck in a needs-human state across a
    restart, the next poll sees last_state=None and can re-alert sooner than
    the documented 30-min REALERT_SECS cadence — at most one extra alert per
    restart. Not persisted for now; a small JSON sidecar (mirroring the
    bots.json pattern) would fix it if this becomes a real nuisance."""

    def __init__(self) -> None:
        self.last_state: Optional[str] = None
        self.last_alert_ts: float = 0.0
        self.ever_logged_in: bool = False


_alert_state = AlertState()


async def check_and_alert(
    info: dict, alert_state: AlertState, now: Optional[float] = None
) -> None:
    """Inspect one read_state() result and schedule notify()/slack() alerts
    on transition. Never awaits the notification itself (create_task only) —
    see module docstring / CLAUDE.md Key Bugs Fixed."""
    from notifications import notify, slack

    state = info.get("state")
    ts = now if now is not None else time.time()
    prev = alert_state.last_state

    if state == "logged_in":
        if prev is not None and prev != "logged_in":
            asyncio.create_task(
                notify(title="Gateway logged in", message="IB Gateway is logged in.", priority="default")
            )
        alert_state.ever_logged_in = True
    elif state in NEEDS_HUMAN:
        if state == "down" and not alert_state.ever_logged_in:
            # Avoid alert storms on cold start (never seen logged_in yet).
            pass
        else:
            transitioned = prev != state
            due = (ts - alert_state.last_alert_ts) >= REALERT_SECS
            if transitioned or due:
                msg = f"IB Gateway state: {state}"
                asyncio.create_task(
                    notify(title="IB Gateway needs you", message=msg, priority="high", tags="warning")
                )
                asyncio.create_task(slack(f"IB Gateway needs you: {state}"))
                alert_state.last_alert_ts = ts

    alert_state.last_state = state


def _alerts_enabled() -> bool:
    return os.environ.get("GATEWAY_ALERTS", "1") not in ("0", "false", "False", "")


async def alert_loop() -> None:
    """Background task: poll read_state() every 60s and alert on transitions.
    Started via asyncio.create_task() in main.py's lifespan; cancelled on shutdown."""
    if not _alerts_enabled():
        return
    while True:
        try:
            info = await read_state()
            await check_and_alert(info, _alert_state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("gateway.alert_loop: iteration failed")
        await asyncio.sleep(60)
