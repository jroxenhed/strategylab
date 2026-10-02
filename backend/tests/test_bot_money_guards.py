"""Bot money-path guards (F435 W5 review: LM-2, LM-3, LM-6, LM-10, LM-11,
DI-06, DI-08).

- LM-2: a bot that can hold either side (a regime_switch graph bot or a
  rule regime bot) adopts whichever side the broker holds when it tracks
  none (after a Stop that kept the position), and refuses to enter while
  any position on its symbol exists.  It used to skip a short (it matched
  only "long"), leaving it with no stop, and could stack a second short.
- LM-3: entry_in_flight is set from the entry order until its fill is
  booked; graph_update refuses while it is set.
- LM-6: a resumed position under a wired Stop keeps the stop it was entered
  with; with no value on the bar it alerts and takes the next bar's value;
  stop_bot clears the stop only when it closes the position.
- LM-10: the start routes compile in the thread pool; start_bot then does
  not compile the same config again on the event loop.
- LM-11: an add's insert, save and rollback hold _save_lock, so a runner's
  concurrent save never writes some of the new bots.
- DI-06: graph and graph_rev change under _save_lock.
- DI-08: a bots.json written before Wave 5 is copied once to
  bots.json.pre-w5.

Money safety (plan 8.4): no real bot runs (BotRunner is replaced where a
start happens; ticks are awaited directly); the broker is an in-memory
fake; journal and notifications are mocks; bots.json is a temporary file.
"""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot_manager as bot_manager_mod
from bot_manager import BotConfig, BotManager, BotState, InPositionError
from bot_runner import BotRunner
from models import RegimeConfig
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.models import Graph
from nodebuilder.trading.sim_bridge import EntrySample
from tests.test_bot_bidirectional_graph import (  # noqa: F401  (switch_frame is a fixture)
    FakeBroker, _Manager, run_tick, switch_bot, switch_frame)
from tests.test_graph_spawn import frame, graph_data, group, leg, node, rule_config


class SequenceBroker(FakeBroker):
    """A FakeBroker whose get_positions answers from a list, one call at a
    time (the last answer repeats)."""

    def __init__(self, answers, price=100.0):
        super().__init__(price=price)
        self.answers = list(answers)

    def get_positions(self):
        if len(self.answers) > 1:
            return [dict(p) for p in self.answers.pop(0)]
        return [dict(p) for p in self.answers[0]]


SHORT = {"symbol": "AAPL", "side": "short", "qty": 7, "avg_entry": 100.0}


# ---------------------------------------------------------------------------
# LM-2: a regime_switch graph bot adopts the broker's side
# ---------------------------------------------------------------------------


def test_regime_switch_bot_adopts_a_short_it_does_not_track(switch_frame):
    """After Stop (keep position) + Start the bot tracks no side.  On a bar
    where the regime is false and the short Entry fires, the old code
    matched only "long", missed the short and stacked a second one."""
    df, _cols, bars = switch_frame
    t = bars["short_entry"]
    runner = BotRunner(switch_bot(), BotState(), _Manager())
    broker = FakeBroker(price=float(df["Close"].iloc[t]))
    broker.position = dict(SHORT)
    run_tick(runner, df.iloc[:t + 1], broker)
    assert broker.submitted == []  # no second short
    assert runner.state.position_direction == "short"
    assert runner.state.entry_price == 100.0
    assert any("Adopted the broker's short position" in e["msg"] for e in runner.state.activity_log)


def test_regime_switch_bot_manages_the_adopted_short(switch_frame):
    """The adopted short gets its exits: here its stop (no broker stop leg
    exists for a short)."""
    df, cols, _bars = switch_frame
    t = next(i for i in range(40, len(df)) if not cols["regime"][i])  # no flip to a long
    price = float(df["Close"].iloc[t])
    runner = BotRunner(switch_bot(stop_loss_pct=1.0), BotState(), _Manager())
    broker = FakeBroker(price=price)
    broker.position = dict(SHORT, avg_entry=price / 1.05)  # the bar is 5% above entry
    log_trade = run_tick(runner, df.iloc[:t + 1], broker)
    assert broker.closed == [("AAPL", "short")]
    assert log_trade.call_args.args[1] == "cover"
    assert log_trade.call_args.kwargs["reason"] == "stop_loss"


def test_regime_switch_bot_refuses_an_entry_next_to_any_position(switch_frame):
    """A position that appears between the position check and the entry
    (or one the bot did not match) blocks the entry, on either side."""
    df, _cols, bars = switch_frame
    t = bars["short_entry"]
    runner = BotRunner(switch_bot(), BotState(), _Manager())
    broker = SequenceBroker([[], [SHORT]], price=float(df["Close"].iloc[t]))
    run_tick(runner, df.iloc[:t + 1], broker)
    assert broker.submitted == []
    assert any("a short position on AAPL exists" in e["msg"] for e in runner.state.activity_log)


def _rule_regime_bot() -> BotConfig:
    # Its rules are not evaluated here: eval_rules is patched to fire.
    return rule_config(bot_id="bot-rr", regime=RegimeConfig(enabled=True, on_flip="hold"))


def _rule_tick(runner, broker, df, fire=True):
    log_trade = MagicMock()

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)), \
             patch("bot_runner.get_trading_provider", return_value=broker), \
             patch("bot_runner.compute_indicators", return_value={}), \
             patch("bot_runner.eval_rules", return_value=fire), \
             patch.object(BotRunner, "_eval_regime_direction", AsyncMock(return_value="short")), \
             patch("bot_runner.notify_entry", AsyncMock()), \
             patch("bot_runner.notify_exit", AsyncMock()), \
             patch("bot_runner.notify_error", AsyncMock()), \
             patch("bot_runner._log_trade", log_trade), \
             patch("bot_runner.compute_bidirectional_pnl", return_value=0.0), \
             patch("asyncio.sleep", AsyncMock()):
            await runner._tick()

    asyncio.run(go())
    return log_trade


def test_rule_regime_bot_adopts_a_short_it_does_not_track():
    runner = BotRunner(_rule_regime_bot(), BotState(), _Manager())
    broker = FakeBroker(price=100.0)
    broker.position = dict(SHORT)
    _rule_tick(runner, broker, frame(5), fire=False)  # no rule fires: a signal exit would close it
    assert broker.submitted == [] and broker.closed == []
    assert runner.state.position_direction == "short"
    assert runner.state.entry_price == 100.0


def test_rule_regime_bot_refuses_an_entry_next_to_a_same_side_position():
    runner = BotRunner(_rule_regime_bot(), BotState(), _Manager())
    broker = SequenceBroker([[], [SHORT]])
    _rule_tick(runner, broker, frame(5))
    assert broker.submitted == []


def test_a_one_sided_bot_still_ignores_the_other_side():
    """A long bot next to a short bot on the same symbol: the short is not
    the long bot's, so it neither adopts it nor counts it as its own."""
    cfg = rule_config(bot_id="bot-long")
    runner = BotRunner(cfg, BotState(), _Manager())
    broker = FakeBroker()
    broker.position = dict(SHORT)
    log_trade = MagicMock()

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=frame(5))), \
             patch("bot_runner.get_trading_provider", return_value=broker), \
             patch("bot_runner.compute_indicators", return_value={}), \
             patch("bot_runner.eval_rules", return_value=False), \
             patch("bot_runner._log_trade", log_trade):
            await runner._tick()

    asyncio.run(go())
    assert runner.state.entry_price is None and runner.state.position_direction is None


# ---------------------------------------------------------------------------
# LM-3: entry in flight
# ---------------------------------------------------------------------------


def test_entry_in_flight_spans_the_order_and_the_fill_poll():
    cfg = switch_bot()
    runner = BotRunner(cfg, BotState(), _Manager())
    seen: list[bool] = []

    class Broker(FakeBroker):
        def submit_order(self, req):
            seen.append(runner.state.entry_in_flight)
            return super().submit_order(req)

        def get_order(self, order_id):
            seen.append(runner.state.entry_in_flight)
            return super().get_order(order_id)

    async def go():
        with patch("bot_runner.get_trading_provider", return_value=Broker(price=50.0)), \
             patch("bot_runner.notify_entry", AsyncMock()), \
             patch("bot_runner._log_trade"), \
             patch("bot_runner.compute_bidirectional_pnl", return_value=0.0), \
             patch("asyncio.sleep", AsyncMock()):
            await runner._enter_position(cfg, runner.state, "long", 50.0, {}, 0)

    asyncio.run(go())
    assert seen and all(seen)
    assert runner.state.entry_in_flight is False and runner.state.entry_price == 50.0


def test_a_failed_entry_order_clears_entry_in_flight():
    cfg = switch_bot()
    runner = BotRunner(cfg, BotState(), _Manager())
    broker = FakeBroker()
    broker.submit_order = MagicMock(side_effect=RuntimeError("rejected"))

    async def go():
        with patch("bot_runner.get_trading_provider", return_value=broker), \
             patch("bot_runner.compute_bidirectional_pnl", return_value=0.0):
            await runner._enter_position(cfg, runner.state, "long", 50.0, {}, 0)

    asyncio.run(go())
    assert runner.state.entry_in_flight is False and runner.state.entry_price is None


def test_graph_update_is_refused_while_an_entry_is_in_flight():
    mgr = BotManager()
    cfg = switch_bot()
    mgr.bots[cfg.bot_id] = (cfg, BotState(entry_in_flight=True))
    with pytest.raises(InPositionError):
        mgr.apply_graph_update(cfg.bot_id, cfg.graph, 2)
    with pytest.raises(InPositionError):
        mgr.update_graph(cfg.bot_id, cfg.graph, 2)
    assert cfg.graph_rev == 1


def test_entry_in_flight_is_never_persisted():
    state = BotState(entry_in_flight=True)
    assert "entry_in_flight" not in state.to_dict()
    assert BotState.from_dict(state.to_dict()).entry_in_flight is False


# ---------------------------------------------------------------------------
# LM-6: the stop of a resumed position under a wired Stop
# ---------------------------------------------------------------------------


def _wired_stop_bot() -> BotConfig:
    nodes, wires = leg("a_", "AAPL", "long_leg")
    nodes += [node("a_three", "constant", {"value": 3.0, "out": "@stop_pct"}, "long_leg",
                   name="three"),
              node("a_stop", "stop", {}, "long_leg", name="stop")]
    wires += [("a_w9", "a_three", "a_stop")]
    graph = Graph.model_validate(graph_data([group("long_leg", "long"), *nodes], wires))
    return BotConfig(strategy_name="ws", symbol="AAPL", interval="1d", buy_rules=[],
                     sell_rules=[], long_buy_rules=None, long_sell_rules=None,
                     short_buy_rules=None, short_sell_rules=None, allocated_capital=1000.0,
                     kind="graph", graph=graph, graph_id="g_000000000001", graph_rev=1,
                     graph_group="long_leg", graph_direction_mode="long", bot_id="bot-ws")


def _sample_tick(runner, broker, df, sample):
    cook = MagicMock(return_value=({}, {"entry": False, "exit": False, "sample": sample}))
    notify = AsyncMock()

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)), \
             patch("bot_runner.get_trading_provider", return_value=broker), \
             patch("bot_runner.cook_graph_bar", cook), \
             patch("bot_runner.notify_error", notify), \
             patch("bot_runner.notify_exit", AsyncMock()), \
             patch("bot_runner._log_trade", MagicMock()), \
             patch("bot_runner.compute_realized_pnl", return_value=0.0), \
             patch("asyncio.sleep", AsyncMock()):
            await runner._tick()
            await asyncio.sleep(0)

    asyncio.run(go())
    return notify


def _held_long(price=100.0):
    broker = FakeBroker(price=price)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 5, "avg_entry": price}
    return broker


def test_a_resumed_position_keeps_the_stop_it_was_entered_with():
    df = frame(6)
    state = BotState(entry_stop_pct=5.0)  # kept by a Stop that left the position open
    runner = BotRunner(_wired_stop_bot(), state, _Manager())
    _sample_tick(runner, _held_long(float(df["Close"].iloc[-1])), df,
                 EntrySample(size=None, stop_pct=3.0, blocked=None))
    assert state.entry_price is not None
    assert state.entry_stop_pct == 5.0  # not this bar's 3.0


def test_a_resumed_position_with_no_stop_value_alerts_then_takes_the_next_one():
    df = frame(6)
    state = BotState()
    runner = BotRunner(_wired_stop_bot(), state, _Manager())
    broker = _held_long(float(df["Close"].iloc[-1]))
    notify = _sample_tick(runner, broker, df.iloc[:-1],
                          EntrySample(size=None, stop_pct=None, blocked="stop"))
    assert state.entry_price is not None and state.entry_stop_pct is None
    notify.assert_called_once()
    assert "no stop" in notify.call_args.kwargs["error_msg"]
    _sample_tick(runner, broker, df, EntrySample(size=None, stop_pct=3.0, blocked=None))
    assert state.entry_stop_pct == 3.0


def test_stop_bot_clears_the_wired_stop_only_when_it_closes():
    mgr = BotManager()
    mgr.save = lambda: None
    cfg = _wired_stop_bot()
    mgr.bots[cfg.bot_id] = (cfg, BotState(entry_price=100.0, entry_stop_pct=4.0))
    with patch("bot_manager.get_trading_provider", return_value=MagicMock()):
        mgr.stop_bot(cfg.bot_id, close_position=False)
        assert mgr.bots[cfg.bot_id][1].entry_stop_pct == 4.0
        mgr.stop_bot(cfg.bot_id, close_position=True)
    assert mgr.bots[cfg.bot_id][1].entry_stop_pct is None


# ---------------------------------------------------------------------------
# LM-10: start compiles in the thread pool, not again on the loop
# ---------------------------------------------------------------------------


def _start_manager(cfg) -> BotManager:
    mgr = BotManager()
    mgr.bot_fund = 100_000.0
    mgr.save = lambda: None
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    return mgr


async def _idle_run(self):
    return None


def test_start_bot_skips_the_compile_prepare_start_did():
    cfg = switch_bot()
    mgr = _start_manager(cfg)
    calls: list[int] = []
    real = bot_manager_mod.compile_bot_graph

    def spy(*a, **k):
        calls.append(threading.get_ident())
        return real(*a, **k)

    async def go():
        with patch("bot_manager.compile_bot_graph", side_effect=spy), \
             patch.object(BotRunner, "run", _idle_run):
            await asyncio.get_running_loop().run_in_executor(None, mgr.prepare_start, cfg.bot_id)
            loop_thread = threading.get_ident()
            mgr.start_bot(cfg.bot_id)
            await asyncio.gather(*mgr.tasks.values())
            return loop_thread

    loop_thread = asyncio.run(go())
    assert len(calls) == 1 and calls[0] != loop_thread


def test_start_bot_compiles_again_when_the_graph_changed_after_prepare():
    cfg = switch_bot()
    mgr = _start_manager(cfg)
    calls: list[int] = []
    real = bot_manager_mod.compile_bot_graph

    async def go():
        with patch("bot_manager.compile_bot_graph",
                   side_effect=lambda *a, **k: calls.append(1) or real(*a, **k)), \
             patch.object(BotRunner, "run", _idle_run):
            mgr.prepare_start(cfg.bot_id)
            mgr.apply_graph_update(cfg.bot_id, cfg.graph.model_copy(deep=True), 2)
            mgr.start_bot(cfg.bot_id)
            await asyncio.gather(*mgr.tasks.values())

    asyncio.run(go())
    assert len(calls) == 2


def test_prepare_start_refuses_a_running_bot_without_compiling():
    cfg = switch_bot()
    mgr = _start_manager(cfg)
    task = MagicMock()
    task.done.return_value = False
    mgr.tasks[cfg.bot_id] = task
    with patch("bot_manager.compile_bot_graph", side_effect=AssertionError("no compile")):
        with pytest.raises(ValueError, match="already running"):
            mgr.prepare_start(cfg.bot_id)


# ---------------------------------------------------------------------------
# LM-11: an add holds _save_lock across insert, save and rollback
# ---------------------------------------------------------------------------


def test_a_concurrent_save_never_sees_legs_whose_save_failed():
    mgr = BotManager()
    mgr.bot_fund = 100_000.0
    seen: list[set] = []
    threads: list[threading.Thread] = []

    def runner_save():  # what a runner's asyncio.to_thread(manager.save) does
        with mgr._save_lock:
            seen.append(set(mgr.bots))

    def failing_save():
        t = threading.Thread(target=runner_save)
        threads.append(t)
        t.start()
        t.join(timeout=0.3)  # it cannot take the lock while the add holds it
        raise OSError("disk full")

    mgr.save = failing_save
    legs = [rule_config("AAPL"), rule_config("MSFT")]
    with pytest.raises(OSError):
        mgr.add_bots(legs)
    threads[0].join(timeout=5)
    assert mgr.bots == {}
    assert seen == [set()]  # the concurrent save saw none of the legs


def test_a_failed_single_add_leaves_no_bot():
    mgr = BotManager()
    mgr.bot_fund = 100_000.0
    mgr.save = MagicMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        mgr.add_bot(rule_config())
    assert mgr.bots == {}


# ---------------------------------------------------------------------------
# DI-06: graph and graph_rev change under _save_lock
# ---------------------------------------------------------------------------


def test_graph_and_rev_change_under_the_save_lock():
    mgr = BotManager()
    cfg = switch_bot()
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    seen: list[tuple] = []
    real = mgr._save_lock

    class Spy:
        def __enter__(self):
            real.acquire()
            seen.append(("enter", cfg.graph_rev))

        def __exit__(self, *exc):
            seen.append(("exit", cfg.graph_rev))
            real.release()

    mgr._save_lock = Spy()
    new_graph = cfg.graph.model_copy(deep=True)
    mgr.apply_graph_update(cfg.bot_id, new_graph, 2)
    assert seen == [("enter", 1), ("exit", 2)]
    assert cfg.graph is new_graph and cfg.graph_rev == 2


# ---------------------------------------------------------------------------
# DI-08: a one-time bots.json.pre-w5 copy
# ---------------------------------------------------------------------------


def _w4_row() -> dict:
    row = {"config": rule_config(bot_id="bot-w4").model_dump(), "state": {"status": "stopped"}}
    for key in BotManager.W5_CONFIG_FIELDS:
        row["config"].pop(key)
    return row


def test_a_bots_json_from_before_wave_5_is_copied_once(tmp_path, monkeypatch):
    path = tmp_path / "bots.json"
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(path))
    raw = json.dumps({"bot_fund": 1000.0, "bots": [_w4_row()]}, indent=2)
    path.write_text(raw)
    BotManager().load()
    copy = tmp_path / "bots.json.pre-w5"
    assert copy.read_text() == raw  # the file as Wave 4 wrote it
    # The load rewrote bots.json with the W5 fields; the copy is never
    # overwritten.
    assert "graph_direction_mode" in path.read_text()
    path.write_text(json.dumps({"bot_fund": 5.0, "bots": [_w4_row()]}))
    BotManager().load()
    assert copy.read_text() == raw


def test_a_wave_5_bots_json_gets_no_copy(tmp_path, monkeypatch):
    path = tmp_path / "bots.json"
    monkeypatch.setattr(bot_manager_mod, "DATA_PATH", str(path))
    row = {"config": rule_config(bot_id="bot-w5").model_dump(), "state": {}}
    path.write_text(json.dumps({"bot_fund": 1000.0, "bots": [row]}))
    BotManager().load()
    assert not (tmp_path / "bots.json.pre-w5").exists()
