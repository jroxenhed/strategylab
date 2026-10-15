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

    # Flat: closed while the backend was down.  The account holds another
    # symbol, so the replies lacking AAPL are not empty ones: F449 books the
    # close only after two such checks in a row (one reply is not proof),
    # and the first check counts as a failure with the position kept.
    broker = FakeBroker(price=CLOSE)
    broker.position = {"symbol": "MSFT", "side": "long", "qty": 1, "avg_entry": 1.0}
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


# ---------------------------------------------------------------------------
# Merge of W7 with F449: a code failure while the broker position check
# fails, or while the broker lists no position that nothing confirms gone.
# The stricter rule wins: no entry, the price exits of a tracked position
# still run, "gone" only on confirmation, and every such tick counts.
# ---------------------------------------------------------------------------


class _DownBroker(FakeBroker):
    """FakeBroker whose position list can fail (a broker outage)."""

    def __init__(self, price, *, down: bool):
        super().__init__(price=price)
        self.down = down

    def get_positions(self):
        if self.down:
            raise ConnectionError("broker unreachable")
        return super().get_positions()


def test_a_code_failure_while_the_position_check_fails_keeps_the_position_and_counts():
    """The code fails on a bar where the broker cannot be asked: no entry,
    the bot does not pause (it may still hold the position), the tick
    raises so it counts, and only the code failure alerts (no exit was
    due).  Once the broker answers, an empty account is not proof the
    position is gone; two non-empty replies that lack it are."""
    entry = CLOSE * 0.999  # the 2% stop is far
    cfg = code_bot(wrangle_bot_data(FAILS))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long")
    state.last_bar_time = str(OWN.index[-2])
    runner = BotRunner(cfg, state, _Manager())

    broker = _DownBroker(CLOSE, down=True)
    alerts = AsyncMock()
    with pytest.raises(bot_runner.PositionCheckError, match="Position check failed"):
        _tick(runner, broker, notify_error=alerts)
    assert state.code_exits_only is True and state.status == "running"
    assert state.pause_reason is None and state.entry_price == entry
    assert broker.submitted == [] and broker.closed == []
    assert alerts.call_count == 1  # the code failure's one alert, no outage alert
    assert "code_runtime" in alerts.call_args.kwargs["error_msg"]

    # Broker back, but the account reply is empty: not proof.
    broker.down = False
    with pytest.raises(bot_runner.PositionCheckError, match="not confirmed yet"):
        _tick(runner, broker)
    assert state.entry_price == entry and state.pause_reason is None

    # Two non-empty replies that lack the position confirm the close.
    broker.position = {"symbol": "MSFT", "side": "long", "qty": 1, "avg_entry": 1.0}
    with pytest.raises(bot_runner.PositionCheckError, match="not confirmed yet"):
        _tick(runner, broker)
    assert state.entry_price == entry
    _tick(runner, broker)
    assert state.entry_price is None
    assert state.pause_reason.startswith("code_runtime") and state.code_exits_only is False
    assert broker.submitted == []


def test_an_exits_only_stop_that_cannot_be_sent_alerts_once_then_closes():
    """In the exits-only state the stop is due while the broker is down: no
    close goes out (the broker did not confirm the position), John is
    alerted at once (one alert for the outage), the tick counts, and the
    stop goes out once the broker answers; then the bot pauses."""
    entry = CLOSE * THROUGH_STOP["long"]
    cfg = code_bot(wrangle_bot_data(BAD))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax: signal_code line 3",
                     graph_exit_fields={"stop_loss_pct": 2.0})
    runner = BotRunner(cfg, state, _Manager())

    broker = _DownBroker(CLOSE, down=True)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": entry}
    alerts = AsyncMock()
    for _ in range(2):
        with pytest.raises(bot_runner.PositionCheckError, match="NOT SENT"):
            _tick(runner, broker, notify_error=alerts)
    assert broker.closed == [] and broker.submitted == []
    assert state.entry_price == entry and state.pause_reason is None
    outage = [c for c in alerts.call_args_list if "NOT SENT" in c.kwargs["error_msg"]]
    assert len(outage) == 1  # one alert per outage

    broker.down = False
    _tick(runner, broker)
    assert broker.closed == [("AAPL", "long")] and broker.submitted == []
    assert state.entry_price is None
    assert state.pause_reason == "code_syntax: signal_code line 3"


# ---------------------------------------------------------------------------
# Review of the merged tick (F449 meets W7): MT-1 .. MT-5
# ---------------------------------------------------------------------------

MSFT = {"symbol": "MSFT", "side": "long", "qty": 1, "avg_entry": 1.0}


class _ReplyBroker(FakeBroker):
    """FakeBroker whose next position lists are given in order (each a list
    of positions); once they run out it lists its own position."""

    def __init__(self, price, replies):
        super().__init__(price=price)
        self.replies = list(replies)
        self.position_calls = 0

    def get_positions(self):
        self.position_calls += 1
        if self.replies:
            return [dict(p) for p in self.replies.pop(0)]
        return super().get_positions()


def _untracked_state() -> BotState:
    """A bot in the exits-only state after a Stop that kept the position:
    entry_price and position_direction are cleared, code_exits_only and the
    trade's stop stay (bot_manager.stop_bot)."""
    return BotState(status="running", entry_price=None, position_direction=None,
                    entry_stop_pct=2.0, code_exits_only=True,
                    code_exits_reason="code_syntax: signal_code line 3",
                    graph_exit_fields={"stop_loss_pct": 2.0})


@pytest.mark.parametrize("direction", ["long", "short"])
def test_mt1_an_untracked_exits_only_bot_does_not_pause_on_an_empty_reply(direction):
    """MT-1: one empty account reply (a Gateway reconnect) is not proof the
    bot is flat.  It keeps the state, the tick counts, and the next reply
    that lists the position resumes it with its stop."""
    entry = CLOSE * (0.999 if direction == "long" else 1.001)  # the 2% stop is far
    cfg = code_bot(wrangle_bot_data(BAD), direction=direction)
    state = _untracked_state()
    runner = BotRunner(cfg, state, _Manager())
    pos = {"symbol": "AAPL", "side": direction, "qty": 10, "avg_entry": entry}
    broker = _ReplyBroker(CLOSE, replies=[[]])
    broker.position = pos

    with pytest.raises(bot_runner.PositionCheckError, match="no positions at all"):
        _tick(runner, broker)
    assert state.status == "running" and state.pause_reason is None
    assert state.code_exits_only is True

    _tick(runner, broker)  # the position is listed again: resumed, not paused
    assert state.entry_price == entry and state.code_exits_only is True
    assert state.pause_reason is None
    assert broker.closed == [] and broker.submitted == []


def test_mt1_an_untracked_exits_only_bot_pauses_once_two_replies_confirm_flat():
    """MT-1: flat is confirmed the F449 way, by two replies in a row that
    list other positions but none of this bot's; an empty reply in between
    counts neither way."""
    cfg = code_bot(wrangle_bot_data(BAD))
    state = _untracked_state()
    runner = BotRunner(cfg, state, _Manager())
    broker = _ReplyBroker(CLOSE, replies=[[MSFT], [], [MSFT]])

    with pytest.raises(bot_runner.PositionCheckError, match="next check confirms"):
        _tick(runner, broker)
    assert state.code_exits_only is True and state.pause_reason is None
    with pytest.raises(bot_runner.PositionCheckError, match="no positions at all"):
        _tick(runner, broker)
    assert state.code_exits_only is True and state.pause_reason is None
    _tick(runner, broker)
    assert state.code_exits_only is False
    assert state.pause_reason == "code_syntax: signal_code line 3"
    assert broker.closed == [] and broker.submitted == []


def test_mt1_a_bot_that_was_flat_when_its_code_failed_still_pauses_at_once():
    """A bot with no position when its code fails pauses on the first good
    reply, even an empty account: it never held anything to protect."""
    cfg = code_bot(wrangle_bot_data(FAILS))
    state = BotState(status="running")
    runner = BotRunner(cfg, state, _Manager())
    broker = _ReplyBroker(CLOSE, replies=[[]])
    _tick(runner, broker)
    assert state.status == "error" and state.pause_reason.startswith("code_runtime")
    assert state.code_exits_only is False and broker.submitted == []


def _run_until(runner, broker, *, sleeps: int, on_sleep=None, df=OWN, notify_error=None):
    """Run BotRunner.run() with a fake fetch and broker.  Every
    asyncio.sleep call is passed to on_sleep(n, secs); the run is cancelled
    at sleep number *sleeps*."""
    calls = {"n": 0}

    async def fake_sleep(secs, *a, **k):
        calls["n"] += 1
        if on_sleep is not None:
            on_sleep(calls["n"], secs)
        if calls["n"] >= sleeps:
            raise asyncio.CancelledError

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)), \
                patch("bot_runner.get_trading_provider", return_value=broker), \
                patch("bot_runner.notify_entry", AsyncMock()), \
                patch("bot_runner.notify_exit", AsyncMock()), \
                patch("bot_runner.notify_error", notify_error or AsyncMock()), \
                patch("bot_runner._log_trade", MagicMock()), \
                patch("bot_runner.compute_realized_pnl", return_value=0.0), \
                patch("asyncio.sleep", fake_sleep):
            try:
                await runner.run()
            except asyncio.CancelledError:
                pass

    asyncio.run(go())
    return calls["n"]


class _SavingManager:
    """Records error_message at every save."""

    def __init__(self, state):
        self.state = state
        self.saved: list = []

    def save(self):
        self.saved.append(self.state.error_message)


def test_mt2_the_recovery_backoff_writes_no_error_message_for_a_bot_in_a_position():
    """MT-2: five failed broker checks put an exits-only bot that holds a
    position into the backoff.  error_message stays empty there (and in
    every save), so a restart during the outage still auto-resumes it."""
    entry = CLOSE * 0.999  # the 2% stop is far
    cfg = code_bot(wrangle_bot_data(BAD))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax: signal_code line 3",
                     graph_exit_fields={"stop_loss_pct": 2.0})
    mgr = _SavingManager(state)
    runner = BotRunner(cfg, state, mgr)
    broker = _DownBroker(CLOSE, down=True)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": entry}
    seen: list = []
    alerts = AsyncMock()
    _run_until(runner, broker, sleeps=5, notify_error=alerts,
               on_sleep=lambda n, secs: seen.append(state.error_message))
    assert any("Backing off" in e["msg"] for e in state.activity_log)
    assert seen == [None] * 5 and all(m is None for m in mgr.saved)
    assert any("consecutive tick failures" in c.kwargs["error_msg"]
               for c in alerts.call_args_list)


def test_mt2_a_pause_set_during_the_backoff_stops_the_loop():
    """A pause set while the backoff waits (an IBKR reject from its own
    thread) is kept: the backoff does not set the bot running again."""
    cfg = code_bot(wrangle_bot_data(BAD))
    state = BotState(status="running", entry_price=CLOSE * 0.999, trail_peak=CLOSE,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax", graph_exit_fields={"stop_loss_pct": 2.0})
    runner = BotRunner(cfg, state, _Manager())
    broker = _DownBroker(CLOSE, down=True)

    def reject(n, secs):
        if n == 5:  # the backoff wait
            state.pause_reason = "IBKR reject: test (code 201)"
            state.status = "error"

    n = _run_until(runner, broker, sleeps=50, on_sleep=reject)
    assert n == 5  # the loop stopped on the pause, no further tick
    assert state.pause_reason == "IBKR reject: test (code 201)"


def test_mt2_auto_resume_is_not_blocked_by_the_recovery_note(disk):
    """A bots.json saved during a backoff (before the MT-2 change) carries
    "Recovering: ..."; that is not a stop reason, so the bot resumes."""
    from bot_runner import RECOVERY_NOTE

    mgr = BotManager()
    for bot_id, extra in (("rec", {"error_message": f"{RECOVERY_NOTE}broker down"}),
                          ("err", {"error_message": "boom"})):
        mgr.bots[bot_id] = (code_bot(wrangle_bot_data(SIGNAL), bot_id=bot_id),
                            BotState(was_running=True, **extra))
    started: list = []
    with patch.object(mgr, "start_bot", lambda bot_id: started.append(bot_id)):
        r = mgr.resume_was_running()
    assert started == ["rec"] and r["skipped"] == ["err"]
    assert mgr.bots["rec"][1].error_message is None


def test_mt3_a_restart_on_the_same_bar_does_not_count_it_again():
    """MT-3: a bar whose exits already ran (main path or an earlier
    exits-only run) is not counted again toward the time stop by a runner
    that starts in the exits-only state on that same bar."""
    entry = CLOSE * 0.999
    cfg = code_bot(wrangle_bot_data(BAD, max_bars=3))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax", entry_bar_count=2,
                     graph_exit_fields={"stop_loss_pct": 2.0, "max_bars_held": 3})
    state.last_bar_time = str(OWN.index[-1])
    broker = _ReplyBroker(CLOSE, replies=[])
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": entry}
    _tick(BotRunner(cfg, state, _Manager()), broker)
    assert state.entry_bar_count == 2 and broker.closed == []
    assert broker.position_calls == 1  # the broker is still asked on that bar


def test_mt3_an_exits_only_bar_is_saved_so_a_later_restart_skips_it():
    """MT-3: the exits-only path saves the bar it ran (last_bar_time), so a
    second restart on the same bar does not count it again either; a new
    bar counts once."""
    entry = CLOSE * 0.999
    cfg = code_bot(wrangle_bot_data(BAD, max_bars=50))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax",
                     graph_exit_fields={"stop_loss_pct": 2.0, "max_bars_held": 50})
    broker = _broker("long", entry)
    earlier = OWN.iloc[:-1]
    _tick(BotRunner(cfg, state, _Manager()), broker, df=earlier)
    assert state.entry_bar_count == 1 and state.last_bar_time == str(earlier.index[-1])
    _tick(BotRunner(cfg, state, _Manager()), broker, df=earlier)  # restart, same bar
    assert state.entry_bar_count == 1
    runner = BotRunner(cfg, state, _Manager())
    _tick(runner, broker)  # restart on a new bar: counts once
    _tick(runner, broker)
    assert state.entry_bar_count == 2 and state.last_bar_time == str(OWN.index[-1])


@pytest.mark.parametrize("fetch", [AsyncMock(side_effect=ConnectionError("down")),
                                   AsyncMock(return_value=OWN.iloc[:1])])
def test_mt4_an_exits_only_fetch_failure_counts(fetch):
    """MT-4: when the bot's own bars cannot be fetched in the exits-only
    state, the stops could not run: the tick raises so it counts.  No stop
    runs on the last known price."""
    entry = CLOSE * THROUGH_STOP["long"]  # the stop would be due
    cfg = code_bot(wrangle_bot_data(BAD))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax", last_price=CLOSE,
                     graph_exit_fields={"stop_loss_pct": 2.0})
    runner = BotRunner(cfg, state, _Manager())
    broker = _broker("long", entry)

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", fetch), \
                patch("bot_runner.get_trading_provider", return_value=broker), \
                patch("bot_runner.notify_error", AsyncMock()), \
                patch("asyncio.sleep", AsyncMock()):
            await runner._tick()

    with pytest.raises(bot_runner.ExitsFetchError, match="could not run"):
        asyncio.run(go())
    assert broker.closed == [] and state.entry_bar_count == 0


def test_mt4_exits_only_fetch_failures_alert_at_the_limit():
    """MT-4: five failed fetches in a row in the exits-only state reach
    MAX_CONSEC_ERRORS and send the one outage alert."""
    entry = CLOSE * 0.999
    cfg = code_bot(wrangle_bot_data(BAD))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax", graph_exit_fields={"stop_loss_pct": 2.0})
    runner = BotRunner(cfg, state, _Manager())
    alerts = AsyncMock()

    async def go():
        calls = {"n": 0}

        async def fake_sleep(secs, *a, **k):
            calls["n"] += 1
            if calls["n"] >= 5:
                raise asyncio.CancelledError

        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(side_effect=ConnectionError("down"))), \
                patch("bot_runner.get_trading_provider", return_value=_broker("long", entry)), \
                patch("bot_runner.notify_error", alerts), \
                patch("asyncio.sleep", fake_sleep):
            try:
                await runner.run()
            except asyncio.CancelledError:
                pass

    asyncio.run(go())
    msgs = [c.kwargs["error_msg"] for c in alerts.call_args_list]
    assert len(msgs) == 1 and "5 consecutive tick failures" in msgs[0]


def test_mt5_the_exits_only_check_runs_with_signal_exits_off():
    """MT-5: the exit check after a code failure asks for price exits only
    (signal_exits=False), so sell rules left in a regime bot's config can
    never place a signal exit."""
    entry = CLOSE * 0.999
    cfg = code_bot(wrangle_bot_data(BAD))
    state = BotState(status="running", entry_price=entry, trail_peak=entry,
                     position_direction="long", code_exits_only=True,
                     code_exits_reason="code_syntax", graph_exit_fields={"stop_loss_pct": 2.0})
    runner = BotRunner(cfg, state, _Manager())
    real = runner._evaluate_exit_reason
    seen: list = []

    async def spy(*args, **kwargs):
        seen.append(kwargs.get("signal_exits", True))
        return await real(*args, **kwargs)

    runner._evaluate_exit_reason = spy
    _tick(runner, _broker("long", entry))
    assert seen == [False]
