"""The exits-only state of a graph bot whose code failed or is refused
(F435 W7 fixes BS-01, CR-3, BS-05, BS-06, BS-02; John's OPEN POSITION RULE,
decisions-pre 2026-10-03).

A bot that holds a position and whose code is refused at load (the kill
switch SL_CODE_NODES=0, or a snippet that no longer prepares) is not
paused: it loads in the exits-only state, auto-resume starts it, a manual
Start is accepted, and it runs only the price exits (stop, trailing stop,
time stop) of that position, long or short, then pauses once flat.  The
state is saved on the bot (code_exits_only), so a restart in the middle of
it keeps it even when the code would now run, and the bot summary shows
it.  With no plan to read, the exits come from the group's settings as the
last good tick applied them (graph_exit_fields).  A flat bot keeps the
pause it always had.

Money safety (plan 8.4): STRATEGYLAB_DATA_DIR and bots.json are in a
temporary folder; the broker is an in-memory fake; journal, fetch and
notifications are mocks; nothing trades.
"""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot_manager as bot_manager_mod
import bot_runner
import notifications
from bot_manager import BotManager, BotState
from bot_runner import BotRunner
from nodebuilder.code import CodeError
from tests.nodebuilder.code_graphs import code_bot, wrangle_bot_data
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_graph_spawn import frame

OWN = frame(3)
CLOSE = float(OWN["Close"].iloc[-1])
SIGNAL = "@sig: bool = @close > 0\n"                     # enters on every bar
FAILS = "@sig: bool = @close > 0\nx = 1 / 0\n"             # fails at line 2
BAD = "x = 1\ny = 2\nz = (y +\n@sig: bool = @close > 0\n"  # does not prepare: line 3

# entry price factor that puts the bar ~5% through the 2% stop
THROUGH_STOP = {"long": 1.05, "short": 0.95}


@pytest.fixture
def disk(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGYLAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(tmp_path / "bots.json"))
    monkeypatch.setattr(bot_manager_mod, "_load_trades", lambda: [])
    monkeypatch.delenv("SL_CODE_NODES", raising=False)
    monkeypatch.delenv("BOTS_AUTORESUME", raising=False)
    alerts = AsyncMock()
    monkeypatch.setattr(notifications, "notify_error", alerts)
    return SimpleNamespace(path=tmp_path, alerts=alerts)


def _write_bots(disk, rows) -> None:
    (disk.path / "bots.json").write_text(json.dumps({"bot_fund": 10_000, "bots": rows}))


def _position_row(config, direction: str, entry: float, **state) -> dict:
    """A bots.json row of a bot that was running and holds a position."""
    return {"config": config.model_dump(mode="json"),
            "state": {"status": "running", "user_stopped": False, "entry_price": entry,
                      "trail_peak": entry, "position_direction": direction, **state}}


def _broker(direction: str, entry: float) -> FakeBroker:
    broker = FakeBroker(price=CLOSE)
    broker.position = {"symbol": "AAPL", "side": direction, "qty": 10, "avg_entry": entry}
    return broker


class Live:
    """Runs bots for real (BotRunner.run) with a fake fetch and broker and
    mocked journal, alerts and sleep; counts every cook."""

    def __init__(self, broker, df=OWN):
        self.broker = broker
        self.df = df
        self.log_trade = MagicMock()
        self.notify_error = AsyncMock()
        self.cooks = 0

    def run(self, mgr: BotManager, start) -> object:
        real_cook = bot_runner.cook_graph_bar
        real_tagged = bot_runner.cook_graph_bar_tagged

        def counting_cook(*args):
            self.cooks += 1
            return real_cook(*args)

        def counting_tagged(*args):
            self.cooks += 1
            return real_tagged(*args)

        async def go():
            patches = [
                patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=self.df)),
                patch("bot_runner.get_trading_provider", return_value=self.broker),
                patch("bot_runner.notify_entry", AsyncMock()),
                patch("bot_runner.notify_exit", AsyncMock()),
                patch("bot_runner.notify_error", self.notify_error),
                patch("bot_runner._log_trade", self.log_trade),
                patch("bot_runner.compute_realized_pnl", return_value=0.0),
                patch("bot_runner.cook_graph_bar", counting_cook),
                patch("bot_runner.cook_graph_bar_tagged", counting_tagged),
                patch("asyncio.sleep", AsyncMock()),
            ]
            for p in patches:
                p.start()
            try:
                out = start()
                tasks = list(mgr.tasks.values())
                if tasks:
                    await asyncio.wait_for(asyncio.gather(*tasks), 5)
                return out
            finally:
                for p in reversed(patches):
                    p.stop()

        return asyncio.run(go())


def _tick(runner, broker, df=OWN, *, notify_error=None):
    notify_error = notify_error or AsyncMock()

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)), \
                patch("bot_runner.get_trading_provider", return_value=broker), \
                patch("bot_runner.notify_entry", AsyncMock()), \
                patch("bot_runner.notify_exit", AsyncMock()), \
                patch("bot_runner.notify_error", notify_error), \
                patch("bot_runner._log_trade", MagicMock()), \
                patch("bot_runner.compute_realized_pnl", return_value=0.0), \
                patch("asyncio.sleep", AsyncMock()):
            await runner._tick()

    asyncio.run(go())
    return notify_error


# ---------------------------------------------------------------------------
# BS-01 / CR-3: refused at load while holding a position
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["long", "short"])
def test_kill_switch_on_restart_resumes_a_bot_in_a_position_and_runs_its_stop(
        disk, monkeypatch, direction):
    """SL_CODE_NODES=0 and a restart: the bot that holds a position loads
    in the exits-only state (not paused), auto-resume starts it, its 2%
    stop closes the position (a short too: no broker stop) without any
    code running, and it pauses with code_disabled once flat."""
    monkeypatch.setenv("SL_CODE_NODES", "0")
    entry = CLOSE * THROUGH_STOP[direction]
    cfg = code_bot(wrangle_bot_data(SIGNAL), bot_id="bot-pos", direction=direction)
    flat = code_bot(wrangle_bot_data(SIGNAL, symbol="MSFT"), bot_id="bot-flat", symbol="MSFT")
    _write_bots(disk, [_position_row(cfg, direction, entry),
                       {"config": flat.model_dump(mode="json"),
                        "state": {"status": "running", "user_stopped": False}}])
    mgr = BotManager()
    mgr.load()

    state = mgr.bots["bot-pos"][1]
    assert state.pause_reason is None and state.error_message is None
    assert state.code_exits_only is True and state.code_exits_reason == "code_disabled"
    flat_state = mgr.bots["bot-flat"][1]
    assert flat_state.pause_reason == "code_disabled"  # a flat bot keeps its pause
    saved = {r["config"]["bot_id"]: r["state"]
             for r in json.loads((disk.path / "bots.json").read_text())["bots"]}
    assert saved["bot-pos"]["code_exits_only"] is True

    live = Live(_broker(direction, entry))
    result = live.run(mgr, mgr.resume_was_running)

    assert result["resumed"] == ["bot-pos"] and "bot-flat" in result["skipped"]
    assert live.broker.closed == [("AAPL", direction)]
    assert live.broker.submitted == []           # no entry, ever
    assert live.cooks == 0                       # no code ran
    assert live.log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.pause_reason == "code_disabled" and state.code_exits_only is False
    assert state.entry_price is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_a_syntax_error_on_load_runs_the_saved_exits_of_a_position(disk, direction):
    """A snippet that no longer prepares: no plan can be compiled, so the
    stop comes from the group settings the last good tick saved."""
    entry = CLOSE * THROUGH_STOP[direction]
    cfg = code_bot(wrangle_bot_data(BAD), bot_id="bot-syn", direction=direction)
    cfg.graph.nodes["w"].name = "spread_z"
    _write_bots(disk, [_position_row(cfg, direction, entry,
                                     graph_exit_fields={"stop_loss_pct": 2.0})])
    mgr = BotManager()
    mgr.load()
    state = mgr.bots["bot-syn"][1]
    assert state.pause_reason is None
    assert state.code_exits_reason == "code_syntax: spread_z line 3"

    live = Live(_broker(direction, entry))
    live.run(mgr, mgr.resume_was_running)

    assert live.broker.closed == [("AAPL", direction)] and live.broker.submitted == []
    assert live.log_trade.call_args.kwargs["reason"] == "stop_loss"
    assert state.pause_reason == "code_syntax: spread_z line 3"
    assert state.code_exits_only is False


def test_with_no_exit_settings_at_all_the_bot_alerts_once_and_keeps_watching(disk):
    """No plan and nothing saved: there is no price exit to run.  One alert
    says so (close it by hand); the bot places nothing and stays up, so an
    external close still pauses it."""
    entry = CLOSE * 1.05
    cfg = code_bot(wrangle_bot_data(BAD), bot_id="bot-none")
    state = BotState(entry_price=entry, trail_peak=entry, position_direction="long",
                     code_exits_only=True, code_exits_reason="code_syntax: signal_code line 3")
    runner = BotRunner(cfg, state, _Manager())
    broker = _broker("long", entry)
    alerts = AsyncMock()
    _tick(runner, broker, notify_error=alerts)
    _tick(runner, broker, notify_error=alerts)
    msgs = [c.kwargs["error_msg"] for c in alerts.call_args_list]
    assert len(msgs) == 1 and "close it by hand" in msgs[0]
    assert broker.closed == [] and broker.submitted == []
    assert state.status != "error" and state.code_exits_only is True


def test_a_manual_start_of_a_bot_in_a_position_is_accepted_exits_only(tmp_path, monkeypatch):
    """Start with the switch off: a bot that holds a position starts in the
    exits-only state (the flat bot's refusal is test_code_kill_switch)."""
    from tests.test_graph_spawn import leg_body, make_world

    monkeypatch.setattr(notifications, "notify_error", AsyncMock())
    monkeypatch.delenv("SL_CODE_NODES", raising=False)
    world = make_world(tmp_path, monkeypatch)
    started: list = []

    async def no_run(self):  # the runner is built, never run
        started.append(self)

    monkeypatch.setattr(bot_runner.BotRunner, "run", no_run)
    env = world.client.post("/api/graphs", json={"name": "g", "graph": wrangle_bot_data(SIGNAL)})
    r = world.client.post(f"/api/graphs/{env.json()['id']}/spawn",
                          json={"rev": 1, "legs": [leg_body("main", direction="short")]})
    assert r.status_code == 201, r.text
    (bot_id,) = world.mgr.bots
    state = world.mgr.bots[bot_id][1]
    state.entry_price, state.position_direction = 101.0, "short"
    monkeypatch.setenv("SL_CODE_NODES", "0")

    def card_fields() -> tuple:
        """What the bot card reads: the summary from GET /api/bots and the
        detail state from GET /api/bots/{id} (FE-7: either may be shown)."""
        rows = world.client.get("/api/bots").json()["bots"]
        summary = {b["bot_id"]: b for b in rows}[bot_id]
        detail = world.client.get(f"/api/bots/{bot_id}").json()["state"]
        return summary["code_exits_only"], detail["code_exits_only"]

    assert card_fields() == (False, False)   # every summary carries the field
    r = world.client.post(f"/api/bots/{bot_id}/start")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "running", "code_exits_only": True}
    assert state.code_exits_only is True and state.code_exits_reason == "code_disabled"
    assert state.pause_reason is None
    assert len(started) == 1 and started[0]._code_failure is not None
    assert started[0]._code_failure.reason == "code_disabled"
    summary = {b["bot_id"]: b for b in world.mgr.list_bots()}[bot_id]
    assert summary["code_exits_only"] is True
    assert card_fields() == (True, True)


# ---------------------------------------------------------------------------
# BS-05 / BS-06: the state is saved, survives a restart, shows on the card
# ---------------------------------------------------------------------------


def test_a_restart_in_the_exits_only_state_keeps_it_even_when_the_code_now_runs(disk):
    """The code fails while the bot holds a position whose stop is far.  The
    backend restarts; meanwhile the position was closed and the code would
    now run (a failure that depended on the data).  The resumed bot still
    cooks nothing and enters nothing: it books the close and pauses."""
    entry = CLOSE * 0.999
    cfg = code_bot(wrangle_bot_data(FAILS), bot_id="bot-1")
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long")
    state.last_bar_time = str(OWN.index[-2])
    mgr = BotManager()
    mgr.bot_fund = 10_000
    mgr.bots = {"bot-1": (cfg, state)}
    _tick(BotRunner(cfg, state, mgr), _broker("long", entry))
    assert state.code_exits_only is True and state.status == "running"
    assert state.code_exits_reason.startswith("code_runtime: signal_code line 2")
    assert state.graph_exit_fields == {"stop_loss_pct": 2.0}  # saved before the cook ran
    summary = mgr.list_bots()[0]
    assert summary["code_exits_only"] is True
    assert summary["code_exits_reason"].startswith("code_runtime")

    # Restart: the saved row now holds code that runs.
    rows = json.loads((disk.path / "bots.json").read_text())["bots"]
    assert rows[0]["state"]["code_exits_only"] is True
    rows[0]["config"]["graph"]["nodes"]["w"]["code"] = SIGNAL
    _write_bots(disk, rows)
    mgr2 = BotManager()
    mgr2.load()
    state2 = mgr2.bots["bot-1"][1]
    assert state2.code_exits_only is True and state2.pause_reason is None

    broker = FakeBroker(price=CLOSE)  # flat: closed while the backend was down
    live = Live(broker)
    live.run(mgr2, mgr2.resume_was_running)
    assert live.cooks == 0 and broker.submitted == []
    assert state2.pause_reason.startswith("code_runtime: signal_code line 2")
    assert state2.code_exits_only is False and state2.entry_price is None


def test_a_good_tick_saves_the_group_exit_settings():
    cfg = code_bot(wrangle_bot_data("@sig: bool = @close < 0\n", max_bars=7))
    state = BotState()
    _tick(BotRunner(cfg, state, _Manager()), FakeBroker(price=CLOSE))
    assert state.status != "error", (state.pause_reason, state.error_message)
    assert state.graph_exit_fields["stop_loss_pct"] == 2.0
    assert state.graph_exit_fields["max_bars_held"] == 7
    assert json.dumps(state.to_dict())  # plain data, saved with the bot
    restored = BotState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored.graph_exit_fields == state.graph_exit_fields


def test_the_saved_trailing_stop_and_time_stop_come_back_as_a_working_config():
    """No plan: a trailing stop and a time stop saved by the last good tick
    still close the position (here the 3% trail from a 10% peak)."""
    entry = CLOSE * 0.999
    cfg = code_bot(wrangle_bot_data(BAD, stop=None))
    state = BotState(entry_price=entry, trail_peak=entry * 1.10, position_direction="long",
                     code_exits_only=True, code_exits_reason="code_syntax: signal_code line 3",
                     graph_exit_fields={"trailing_stop": {"type": "pct", "value": 3.0},
                                        "max_bars_held": 50})
    runner = BotRunner(cfg, state, _Manager())
    eff = runner._saved_exits_config(state)
    assert eff.trailing_stop.value == 3.0 and eff.max_bars_held == 50
    assert eff.graph is cfg.graph or eff.graph == cfg.graph
    broker = _broker("long", entry)
    _tick(runner, broker)
    assert broker.closed == [("AAPL", "long")] and broker.submitted == []
    assert state.pause_reason == "code_syntax: signal_code line 3"


def test_stop_with_close_ends_the_state_and_a_stop_that_keeps_the_position_does_not(disk):
    mgr = BotManager()
    mgr.bot_fund = 10_000
    for bot_id in ("keep", "close"):
        cfg = code_bot(wrangle_bot_data(SIGNAL), bot_id=bot_id)
        mgr.bots[bot_id] = (cfg, BotState(entry_price=100.0, position_direction="long",
                                          code_exits_only=True, code_exits_reason="code_disabled"))
    broker = FakeBroker()
    with patch.object(bot_manager_mod, "get_trading_provider", return_value=broker):
        mgr.stop_bot("keep", close_position=False)
        mgr.stop_bot("close", close_position=True)
    assert mgr.bots["keep"][1].code_exits_only is True
    assert bot_manager_mod.holds_position(mgr.bots["keep"][1])  # Start runs exits only
    assert mgr.bots["close"][1].code_exits_only is False


def test_a_manual_buy_is_refused_in_the_exits_only_state(disk):
    mgr = BotManager()
    cfg = code_bot(wrangle_bot_data(SIGNAL), bot_id="b")
    mgr.bots["b"] = (cfg, BotState(status="running", code_exits_only=True,
                                   code_exits_reason="code_disabled"))
    with pytest.raises(ValueError, match="manages only the exits"):
        mgr.manual_buy("b")


def test_a_graph_update_on_a_flat_bot_ends_the_state(disk):
    from nodebuilder.models import Graph

    mgr = BotManager()
    cfg = code_bot(wrangle_bot_data(SIGNAL), bot_id="b")
    state = BotState(code_exits_only=True, code_exits_reason="code_disabled")
    mgr.bots["b"] = (cfg, state)
    mgr.apply_graph_update("b", Graph.model_validate(wrangle_bot_data(SIGNAL)), 2)
    assert state.code_exits_only is False and state.code_exits_reason is None


# ---------------------------------------------------------------------------
# BS-02 / CR-5 / BS-03 / BS-07: the bot's cook runs on the code pool
# ---------------------------------------------------------------------------


def test_the_guarded_cook_runs_on_the_code_pool_not_the_default_executor():
    """Contract with fixer B1: await_guarded's default executor is the code
    pool (thread names "sl-code"); the bot passes no executor of its own, so
    a hung Wrangle never holds a thread its broker calls and fetches need."""
    threads: list[str] = []
    real = bot_runner.cook_graph_bar_tagged

    def spy(*args):
        threads.append(threading.current_thread().name)
        return real(*args)

    state = BotState()
    runner = BotRunner(code_bot(wrangle_bot_data("@sig: bool = @close < 0\n")), state,
                       _Manager())
    with patch("bot_runner.cook_graph_bar_tagged", spy):
        _tick(runner, FakeBroker(price=CLOSE))
    assert state.status != "error" and runner._code_failure is None
    assert len(threads) == 1 and threads[0].startswith("sl-code"), threads


def test_a_full_code_pool_pauses_a_flat_bot_with_code_timeout():
    """await_guarded raises a code_timeout saying the code pool is full: the
    bot treats it as a code failure (no order; a flat bot pauses)."""
    state = BotState()
    broker = FakeBroker(price=CLOSE)
    runner = BotRunner(code_bot(wrangle_bot_data(SIGNAL)), state, _Manager())
    full = CodeError("code_timeout", "the code pool is full: 4 code cooks still run past "
                                     "their limit; a backend restart frees them")
    with patch.object(bot_runner._code_runtime, "await_guarded", AsyncMock(side_effect=full)):
        _tick(runner, broker)
    assert broker.submitted == [] and broker.closed == []
    assert state.status == "error"
    assert state.pause_reason.startswith("code_timeout") and "pool is full" in state.pause_reason


# ---------------------------------------------------------------------------
# A param-only window expression fails at compile (kernel.params.static_windows)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["long", "short"])
def test_a_window_expression_that_fails_at_compile_is_a_code_failure(disk, direction):
    """An SMA period of ``1/0`` reads only params, so compile evaluates it
    to size the window and gets the cook's code_runtime.  That is a code
    failure (``code_failure``), never "Graph does not compile": the bot in a
    position runs its stop exits-only (OPEN POSITION RULE), enters nothing,
    and pauses with the code reason once flat."""
    from tests.nodebuilder.code_graphs import node, wire

    data = wrangle_bot_data(SIGNAL)
    data["nodes"]["ma"] = node("ma", "sma", {"period": {"expr": "1/0"}, "out": "@ma"})
    data["wires"].append(wire("w9", "t", "ma"))
    entry = CLOSE * THROUGH_STOP[direction]
    cfg = code_bot(data, bot_id="bot-1", direction=direction)
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction=direction)
    state.last_bar_time = str(OWN.index[-2])
    mgr = BotManager()
    mgr.bot_fund = 10_000
    mgr.bots = {"bot-1": (cfg, state)}
    broker = _broker(direction, entry)
    runner = BotRunner(cfg, state, mgr)
    _tick(runner, broker)
    if broker.closed == []:
        _tick(runner, broker)
    assert broker.closed == [("AAPL", direction)] and broker.submitted == []
    assert "does not compile" not in (state.pause_reason or "")
    # Worded as the cook's failure would be: the node, the line, the error.
    assert state.pause_reason == "code_runtime: ma line 1: ZeroDivisionError: division by zero"
    assert state.code_exits_only is False and state.entry_price is None
