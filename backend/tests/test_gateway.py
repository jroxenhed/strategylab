"""Tests for gateway.py — IBC log parsing, command port protocol, alert transitions (F428).

Log line fixtures below are lifted verbatim (marker text) from real IBC logs
captured on the dev Mac (~/ibc/logs/ibc-3.23.0_GATEWAY-10.45_*.txt), except
"Second Factor" which the plan flags as an assumed string.
"""

from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import asyncio
from datetime import datetime

import pytest

import gateway


# ---------------------------------------------------------------------------
# parse_log_text — one marker each
# ---------------------------------------------------------------------------

def _line(ts: str, rest: str) -> str:
    return f"{ts}:000 {rest}"


def _expected_iso(ts: str) -> str:
    """Same conversion gateway.py applies to `since` (CORR-03): naive
    'YYYY-MM-DD HH:MM:SS' host-local wall clock -> tz-aware ISO 8601.
    Computed the same way here so assertions are host-timezone-independent."""
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").astimezone().isoformat()


@pytest.mark.parametrize(
    "rest,expected_state",
    [
        ("IBC: Login has completed", "logged_in"),
        ("IBC: detected dialog entitled: Re-login is required; event=Opened", "relogin_required"),
        ("IBC: Too many failed login attempts", "locked_out"),
        ("IBC: detected dialog entitled: Unrecognized Username or Password; event=Opened", "bad_credentials"),
        ("IBC: Second Factor Authentication requested", "awaiting_2fa"),
        ("IBC: SECOND FACTOR requested", "awaiting_2fa"),  # case-insensitive
        ("IBC: detected dialog entitled: Restart in progress; event=Opened", "restarting"),
        ("IBC: detected dialog entitled: Shutdown progress; event=Opened", "restarting"),
        ("IBC: Login dialog WINDOW_OPENED: LoginState is LOGGED_OUT", "logging_in"),
        ("IBC: Login attempt: 1", "logging_in"),
    ],
)
def test_single_marker(rest, expected_state):
    text = _line("2026-06-02 04:00:05", rest)
    state, since, last_lines = gateway.parse_log_text(text)
    assert state == expected_state
    assert since == _expected_iso("2026-06-02 04:00:05")
    assert last_lines == [text]


def test_no_marker_is_unknown():
    text = "\n".join([
        _line("2026-06-02 04:00:00", "IBC: some unrelated line"),
        _line("2026-06-02 04:00:01", "IBC: another unrelated line"),
    ])
    state, since, _ = gateway.parse_log_text(text)
    assert state == "unknown"
    assert since is None


def test_last_marker_wins():
    text = "\n".join([
        _line("2026-06-02 04:00:05", "IBC: Login dialog WINDOW_OPENED: LoginState is LOGGED_OUT"),
        _line("2026-06-02 04:00:06", "IBC: Login attempt: 1"),
        _line("2026-06-02 04:00:10", "IBC: Login has completed"),
        _line("2026-06-02 05:00:00", "IBC: detected dialog entitled: Restart in progress; event=Opened"),
        _line("2026-06-02 05:00:07", "IBC: Login dialog WINDOW_OPENED: LoginState is LOGGED_OUT"),
        _line("2026-06-02 05:00:11", "IBC: Login has completed"),
    ])
    state, since, _ = gateway.parse_log_text(text)
    assert state == "logged_in"
    assert since == _expected_iso("2026-06-02 05:00:11")


def test_last_marker_wins_relogin_then_bad_credentials():
    text = "\n".join([
        _line("2026-06-03 06:31:57", "IBC: detected dialog entitled: Re-login is required; event=Opened"),
        _line("2026-06-03 06:31:58", "IBC: detected dialog entitled: Unrecognized Username or Password; event=Opened"),
    ])
    state, since, _ = gateway.parse_log_text(text)
    assert state == "bad_credentials"
    assert since == _expected_iso("2026-06-03 06:31:58")


def test_last_lines_capped_at_12():
    lines = [_line(f"2026-06-02 04:00:{i:02d}", f"IBC: line {i}") for i in range(20)]
    text = "\n".join(lines)
    _, _, last_lines = gateway.parse_log_text(text)
    assert len(last_lines) == 12
    assert last_lines == lines[-12:]


# ---------------------------------------------------------------------------
# read_state() — log dir resolution + down detection (file system only, no
# real network; command port check will simply fail-closed to unreachable).
# ---------------------------------------------------------------------------

async def test_read_state_no_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IBC_LOG_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("IBC_COMMAND_PORT", "1")  # unlikely to be listening
    monkeypatch.setattr("broker.get_available_brokers", lambda: [])
    info = await gateway.read_state()
    assert info["state"] == "down"
    assert info["log_file"] is None
    assert info["api_connected"] is False
    assert info["command_port"]["reachable"] is False


async def test_read_state_fresh_log_logged_in(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    f = log_dir / "ibc-3.23.0_GATEWAY-10.45_Tuesday.txt"
    f.write_text(_line("2026-06-02 04:00:10", "IBC: Login has completed") + "\n")
    monkeypatch.setenv("IBC_LOG_DIR", str(log_dir))
    monkeypatch.setenv("IBC_COMMAND_PORT", "1")
    monkeypatch.setattr("broker.get_available_brokers", lambda: ["ibkr"])
    info = await gateway.read_state()
    assert info["state"] == "logged_in"
    assert info["api_connected"] is True
    assert info["log_file"] == str(f)


async def test_read_state_stale_log_not_api_connected_is_down(tmp_path, monkeypatch):
    import os
    import time as time_mod

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    f = log_dir / "ibc-3.23.0_GATEWAY-10.45_Tuesday.txt"
    f.write_text(_line("2026-06-02 04:00:10", "IBC: Login has completed") + "\n")
    old = time_mod.time() - 3600  # 1h old, well past the 15-min staleness window
    os.utime(f, (old, old))
    monkeypatch.setenv("IBC_LOG_DIR", str(log_dir))
    monkeypatch.setenv("IBC_COMMAND_PORT", "1")
    monkeypatch.setattr("broker.get_available_brokers", lambda: [])
    info = await gateway.read_state()
    assert info["state"] == "down"
    # CORR-02/REL-03: `since` must not still read the stale login timestamp
    # once the state is overridden to "down" — it should track the log
    # file's mtime (when it went quiet) instead.
    assert info["since"] == gateway._epoch_to_iso(f.stat().st_mtime)


async def test_read_state_no_log_dir_down_since_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("IBC_LOG_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("IBC_COMMAND_PORT", "1")
    monkeypatch.setattr("broker.get_available_brokers", lambda: [])
    info = await gateway.read_state()
    assert info["state"] == "down"
    assert info["since"] is None


async def test_read_state_stale_log_but_api_connected_not_down(tmp_path, monkeypatch):
    import os
    import time as time_mod

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    f = log_dir / "ibc-3.23.0_GATEWAY-10.45_Tuesday.txt"
    f.write_text(_line("2026-06-02 04:00:10", "IBC: Login has completed") + "\n")
    old = time_mod.time() - 3600
    os.utime(f, (old, old))
    monkeypatch.setenv("IBC_LOG_DIR", str(log_dir))
    monkeypatch.setenv("IBC_COMMAND_PORT", "1")
    monkeypatch.setattr("broker.get_available_brokers", lambda: ["ibkr"])
    info = await gateway.read_state()
    assert info["state"] == "logged_in"


# ---------------------------------------------------------------------------
# send_command — real asyncio server on loopback, no live IBC needed.
# ---------------------------------------------------------------------------

async def test_send_command_rejects_unknown_cmd():
    with pytest.raises(ValueError):
        await gateway.send_command("BOGUS")


async def test_send_command_unreachable_port_raises_connection_error(monkeypatch):
    monkeypatch.setenv("IBC_COMMAND_HOST", "127.0.0.1")
    monkeypatch.setenv("IBC_COMMAND_PORT", "1")  # nothing listens on port 1
    with pytest.raises(ConnectionError):
        await gateway.send_command("RESTART")


async def test_send_command_round_trip(monkeypatch):
    received: list[str] = []

    async def handle(reader, writer):
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode().strip()
            received.append(text)
            if text == "EXIT":
                break
            writer.write(b"OK\n")
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("IBC_COMMAND_HOST", "127.0.0.1")
    monkeypatch.setenv("IBC_COMMAND_PORT", str(port))

    try:
        reply = await gateway.send_command("RECONNECTDATA")
    finally:
        server.close()
        await server.wait_closed()

    assert reply == "OK"
    assert received == ["RECONNECTDATA", "EXIT"]


# ---------------------------------------------------------------------------
# check_and_alert — fake clock, mocked notify/slack, no network.
# ---------------------------------------------------------------------------

async def test_alert_fires_on_transition_into_needs_human(monkeypatch):
    calls = {"notify": [], "slack": []}

    async def fake_notify(**kwargs):
        calls["notify"].append(kwargs)

    async def fake_slack(text):
        calls["slack"].append(text)

    monkeypatch.setattr("notifications.notify", fake_notify)
    monkeypatch.setattr("notifications.slack", fake_slack)

    state = gateway.AlertState()
    state.last_state = "logged_in"
    state.ever_logged_in = True

    await gateway.check_and_alert({"state": "awaiting_2fa"}, state, now=1000.0)
    await asyncio.sleep(0)  # flush the create_task calls

    assert len(calls["notify"]) == 1
    assert calls["notify"][0]["priority"] == "high"
    assert len(calls["slack"]) == 1
    assert state.last_alert_ts == 1000.0


async def test_alert_does_not_refire_before_30min(monkeypatch):
    calls = {"notify": 0}

    async def fake_notify(**kwargs):
        calls["notify"] += 1

    async def fake_slack(text):
        pass

    monkeypatch.setattr("notifications.notify", fake_notify)
    monkeypatch.setattr("notifications.slack", fake_slack)

    state = gateway.AlertState()
    state.last_state = "awaiting_2fa"
    state.ever_logged_in = True
    state.last_alert_ts = 1000.0

    # Same state, only 5 minutes later — should not re-alert.
    await gateway.check_and_alert({"state": "awaiting_2fa"}, state, now=1300.0)
    await asyncio.sleep(0)
    assert calls["notify"] == 0

    # 30+ minutes later — should re-alert.
    await gateway.check_and_alert({"state": "awaiting_2fa"}, state, now=1000.0 + 1801)
    await asyncio.sleep(0)
    assert calls["notify"] == 1


async def test_alert_skips_down_on_cold_start(monkeypatch):
    calls = {"notify": 0}

    async def fake_notify(**kwargs):
        calls["notify"] += 1

    async def fake_slack(text):
        pass

    monkeypatch.setattr("notifications.notify", fake_notify)
    monkeypatch.setattr("notifications.slack", fake_slack)

    state = gateway.AlertState()  # fresh: ever_logged_in=False, last_state=None

    await gateway.check_and_alert({"state": "down"}, state, now=1000.0)
    await asyncio.sleep(0)
    assert calls["notify"] == 0
    assert state.last_state == "down"


async def test_alert_fires_on_down_after_previously_logged_in(monkeypatch):
    calls = {"notify": 0}

    async def fake_notify(**kwargs):
        calls["notify"] += 1

    async def fake_slack(text):
        pass

    monkeypatch.setattr("notifications.notify", fake_notify)
    monkeypatch.setattr("notifications.slack", fake_slack)

    state = gateway.AlertState()
    state.ever_logged_in = True
    state.last_state = "logged_in"

    await gateway.check_and_alert({"state": "down"}, state, now=1000.0)
    await asyncio.sleep(0)
    assert calls["notify"] == 1


async def test_alert_fires_once_on_return_to_logged_in(monkeypatch):
    calls = {"notify": []}

    async def fake_notify(**kwargs):
        calls["notify"].append(kwargs)

    async def fake_slack(text):
        pass

    monkeypatch.setattr("notifications.notify", fake_notify)
    monkeypatch.setattr("notifications.slack", fake_slack)

    state = gateway.AlertState()
    state.last_state = "awaiting_2fa"
    state.ever_logged_in = True

    await gateway.check_and_alert({"state": "logged_in"}, state, now=1000.0)
    await asyncio.sleep(0)
    assert len(calls["notify"]) == 1
    assert calls["notify"][0]["priority"] == "default"

    # Staying logged_in should not re-fire.
    await gateway.check_and_alert({"state": "logged_in"}, state, now=2000.0)
    await asyncio.sleep(0)
    assert len(calls["notify"]) == 1
