"""is_bidirectional on graph bots (F435 W5 5.D, plan D7).

BotConfig.is_bidirectional is True for a rule bot with its regime on, or a
graph bot whose group is regime_switch.  It replaces every
``regime and regime.enabled`` check in bot_manager.py and bot_runner.py, so
a regime_switch graph bot:
- needs its symbol to itself (the exclusive-symbol guard on start);
- sizes and reports P&L over both sides (compute_bidirectional_pnl);
- enters long while its Regime terminal is true and short while it is
  false, and exits on its own side's Exit;
- on a regime flip with close_and_reverse, closes and opens the other side,
  and the next tick finds the reversed position (nothing is booked as
  closed outside the bot).

Also: graph_bot_live refuses a rule regime on a graph bot, a regime_switch
group on a bot not made for it, and a long group whose Regime terminal flips
with close_and_reverse.

Money safety (plan 8.4): no bot is started (start_bot is only called where
the guard refuses before any task is made); the broker is an in-memory fake;
the journal and notifications are mocks; bots.json is never written.
"""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import bot_runner
from bot_manager import BotConfig, BotManager, BotState
from bot_runner import BotRunner, graph_bot_live
from models import RegimeConfig
from nodebuilder.compile import compile as nb_compile
from nodebuilder.models import Graph, GraphValidationError
from nodebuilder.prepare import build_graph_attrs
from nodebuilder.trading import sim_bridge as sb
from tests.test_graph_spawn import frame, graph_data, group, leg, rule_config, switch_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def switch_bot(on_flip="hold", alloc=1000.0, **extra) -> BotConfig:
    fields = dict(
        strategy_name="sw", symbol="AAPL", interval="1d", buy_rules=[], sell_rules=[],
        long_buy_rules=None, long_sell_rules=None, short_buy_rules=None, short_sell_rules=None,
        allocated_capital=alloc, kind="graph",
        graph=Graph.model_validate(switch_data("AAPL", on_flip=on_flip)),
        graph_id="g_000000000001", graph_rev=1, graph_group="sw",
        graph_direction_mode="regime_switch", direction="long", bot_id="bot-sw",
    )
    fields.update(extra)
    return BotConfig(**fields)


class FakeBroker:
    """An in-memory broker: orders fill at once at ``price``."""

    def __init__(self, price=100.0):
        self.price = price
        self.position = None
        self.submitted: list = []
        self.closed: list = []

    def get_positions(self):
        return [dict(self.position)] if self.position else []

    def submit_order(self, req):
        side = "short" if req.side == "sell" else "long"
        self.submitted.append((req.symbol, req.side, req.qty))
        self.position = {"symbol": req.symbol, "side": side, "qty": req.qty, "avg_entry": self.price}
        return SimpleNamespace(order_id=f"o{len(self.submitted)}")

    def get_order(self, order_id):
        return SimpleNamespace(filled_avg_price=self.price)

    def get_orders(self, *a, **k):
        return []

    def cancel_order(self, order_id):
        return None

    def close_position(self, symbol):
        self.closed.append((symbol, self.position["side"] if self.position else None))
        self.position = None
        return SimpleNamespace(order_id=f"c{len(self.closed)}")


class _Manager:
    def save(self):
        return None


def run_tick(runner, df, broker, *, bidir_pnl=0.0):
    """One _tick with a mocked fetch, broker, journal and notifications.
    Returns the _log_trade mock."""
    log_trade = MagicMock()

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", AsyncMock(return_value=df)),
            patch("bot_runner.get_trading_provider", return_value=broker),
            patch("bot_runner.notify_entry", AsyncMock()),
            patch("bot_runner.notify_exit", AsyncMock()),
            patch("bot_runner.notify_error", AsyncMock()),
            patch("bot_runner._log_trade", log_trade),
            patch("bot_runner.compute_bidirectional_pnl", return_value=bidir_pnl),
            patch("bot_runner.compute_realized_pnl",
                  side_effect=AssertionError("a regime_switch bot uses both sides' P&L")),
            patch("asyncio.sleep", AsyncMock()),
        ]
        for p in patches:
            p.start()
        try:
            await runner._tick()
        finally:
            for p in reversed(patches):
                p.stop()

    asyncio.run(go())
    return log_trade


def switch_columns(cfg, df):
    """The regime_switch group's columns over a whole frame (the backtest's
    cook), to pick bars for the tick tests."""
    live = graph_bot_live(nb_compile(cfg.graph), cfg)
    attrs = build_graph_attrs(live.program, df)
    result = sb.cook(live.program, attrs, live.plan.keep_ids())
    col = lambda read: np.asarray(result.column(read.node_id, read.attr), dtype=bool)  # noqa: E731
    p = live.plan
    return {"regime": col(p.regime), "entry_long": col(p.entry_long),
            "entry_short": col(p.entry_short), "exit_long": col(p.exit_long),
            "exit_short": col(p.exit_short)}


def _find_bar(cols, want: dict, start=40):
    n = len(cols["regime"])
    for t in range(start, n):
        if all(bool(cols[k][t]) == v for k, v in want.items()):
            return t
    return None


@pytest.fixture(scope="module")
def switch_frame():
    """A frame with a bar of each kind the tests need."""
    cfg = switch_bot()
    for seed in range(1, 40):
        df = frame(seed)
        cols = switch_columns(cfg, df)
        bars = {
            "short_entry": _find_bar(cols, {"regime": False, "entry_short": True}),
            "long_entry": _find_bar(cols, {"regime": True, "entry_long": True}),
            "regime_false": _find_bar(cols, {"regime": False}),
        }
        if all(v is not None for v in bars.values()):
            return df, cols, bars
    pytest.fail("no synthetic frame has the bars the tests need")


# ---------------------------------------------------------------------------
# is_bidirectional
# ---------------------------------------------------------------------------


def test_is_bidirectional_truth_table():
    assert not rule_config().is_bidirectional
    assert rule_config(regime=RegimeConfig(enabled=True)).is_bidirectional
    assert not rule_config(regime=RegimeConfig(enabled=False)).is_bidirectional
    assert switch_bot().is_bidirectional
    assert not switch_bot(graph_direction_mode="long").is_bidirectional
    # graph_direction_mode means nothing on a rule bot.
    assert not rule_config(graph_direction_mode="regime_switch").is_bidirectional


def test_legacy_rule_bot_row_loads_unchanged():
    row = {"config": rule_config().model_dump(), "state": {"status": "running"}}
    for key in ("graph_id", "graph_rev", "graph_group", "graph_direction_mode"):
        row["config"].pop(key)
    cfg, state = BotManager._load_entry(row)
    assert (cfg.graph_id, cfg.graph_rev, cfg.graph_group, cfg.graph_direction_mode) == (None,) * 4
    assert cfg.kind == "rule" and not cfg.is_bidirectional
    assert state.status == "stopped" and state.was_running


# ---------------------------------------------------------------------------
# The exclusive-symbol guard on start (refusals only: nothing is started)
# ---------------------------------------------------------------------------


def _manager_running(running_cfg: BotConfig, other_cfg: BotConfig) -> BotManager:
    mgr = BotManager()
    mgr.bot_fund = 100_000.0
    mgr.save = lambda: None
    mgr.bots[running_cfg.bot_id] = (running_cfg, BotState(status="running"))
    mgr.bots[other_cfg.bot_id] = (other_cfg, BotState())
    task = MagicMock()
    task.done.return_value = False
    mgr.tasks[running_cfg.bot_id] = task
    return mgr


def test_a_running_regime_switch_bot_blocks_any_bot_on_its_symbol():
    other = rule_config("AAPL", "short", bot_id="bot-rule")
    mgr = _manager_running(switch_bot(), other)
    with patch("bot_manager.BotRunner", side_effect=AssertionError("must not start")):
        with pytest.raises(ValueError, match="exclusive symbol access"):
            mgr.start_bot("bot-rule")
    assert set(mgr.tasks) == {"bot-sw"}


def test_a_regime_switch_bot_cannot_start_beside_another_bot_on_its_symbol():
    running = rule_config("AAPL", "short", bot_id="bot-rule")
    mgr = _manager_running(running, switch_bot())
    with patch("bot_manager.BotRunner", side_effect=AssertionError("must not start")):
        with pytest.raises(ValueError, match="exclusive symbol access"):
            mgr.start_bot("bot-sw")
    assert set(mgr.tasks) == {"bot-rule"}


# ---------------------------------------------------------------------------
# P&L and sizing over both sides
# ---------------------------------------------------------------------------


def test_bot_pnl_uses_both_sides():
    runner = BotRunner(switch_bot(), BotState(), _Manager())
    with patch("bot_runner.compute_bidirectional_pnl", return_value=250.0) as both, \
         patch("bot_runner.compute_realized_pnl", side_effect=AssertionError("one side only")):
        assert runner._bot_pnl(runner.config, runner.state) == 250.0
    both.assert_called_once_with("AAPL", "bot-sw", since=None)


def test_list_bots_reports_both_sides_pnl():
    mgr = BotManager()
    mgr.bots["bot-sw"] = (switch_bot(), BotState())
    with patch("bot_manager._load_trades", return_value=[]), \
         patch("bot_manager.compute_bidirectional_pnl", return_value=123.456) as both, \
         patch("bot_manager.compute_realized_pnl", side_effect=AssertionError("one side only")):
        [bot] = mgr.list_bots()
    assert bot["total_pnl"] == 123.46
    assert bot["graph_direction_mode"] == "regime_switch"
    both.assert_called_once()


def test_entry_size_compounds_both_sides_pnl():
    cfg = switch_bot(alloc=1000.0)
    runner = BotRunner(cfg, BotState(), _Manager())
    broker = FakeBroker(price=50.0)

    async def go():
        with patch("bot_runner.get_trading_provider", return_value=broker), \
             patch("bot_runner.notify_entry", AsyncMock()), \
             patch("bot_runner._log_trade"), \
             patch("bot_runner.compute_bidirectional_pnl", return_value=500.0), \
             patch("bot_runner.compute_realized_pnl", side_effect=AssertionError("one side only")), \
             patch("asyncio.sleep", AsyncMock()):
            await runner._enter_position(cfg, runner.state, "short", 50.0, {}, 0)

    asyncio.run(go())
    assert broker.submitted == [("AAPL", "sell", math.floor((1000.0 + 500.0) / 50.0))]
    assert runner.state.position_direction == "short"


# ---------------------------------------------------------------------------
# A regime_switch tick: side from the Regime terminal, exits by side
# ---------------------------------------------------------------------------


def test_regime_false_enters_short_on_the_short_entry(switch_frame):
    df, _cols, bars = switch_frame
    t = bars["short_entry"]
    runner = BotRunner(switch_bot(), BotState(), _Manager())
    broker = FakeBroker(price=float(df["Close"].iloc[t]))
    log_trade = run_tick(runner, df.iloc[:t + 1], broker, bidir_pnl=200.0)
    qty = math.floor((1000.0 + 200.0) / broker.price)
    assert broker.submitted == [("AAPL", "sell", qty)]
    assert runner.state.position_direction == "short"
    assert runner.state.regime_direction == "short"
    assert log_trade.call_args.args[1] == "short"
    assert log_trade.call_args.kwargs["direction"] == "short"


def test_regime_true_enters_long_on_the_long_entry(switch_frame):
    df, _cols, bars = switch_frame
    t = bars["long_entry"]
    runner = BotRunner(switch_bot(), BotState(), _Manager())
    broker = FakeBroker(price=float(df["Close"].iloc[t]))
    run_tick(runner, df.iloc[:t + 1], broker)
    assert [s[1] for s in broker.submitted] == ["buy"]
    assert runner.state.position_direction == "long"
    assert runner.state.regime_direction == "long"


def test_a_short_position_exits_on_the_short_exit_only(switch_frame):
    df, cols, _bars = switch_frame
    t = _find_bar(cols, {"regime": False, "exit_short": True, "exit_long": False})
    assert t is not None
    runner = BotRunner(switch_bot(), BotState(), _Manager())
    price = float(df["Close"].iloc[t])
    broker = FakeBroker(price=price)
    broker.position = {"symbol": "AAPL", "side": "short", "qty": 10, "avg_entry": price}
    runner.state.entry_price = price
    runner.state.position_direction = "short"
    runner._last_broker_qty = 10
    log_trade = run_tick(runner, df.iloc[:t + 1], broker)
    assert broker.closed == [("AAPL", "short")]
    assert log_trade.call_args.args[1] == "cover"
    assert log_trade.call_args.kwargs["reason"] == "signal"


def test_close_and_reverse_keeps_the_reversed_side(switch_frame):
    """In a long while the regime turns false: close_and_reverse closes the
    long and opens a short.  The bot must then track the short: the next
    tick finds it, and nothing is booked as closed outside the bot."""
    df, cols, bars = switch_frame
    t = bars["regime_false"]
    runner = BotRunner(switch_bot(on_flip="close_and_reverse"), BotState(), _Manager())
    price = float(df["Close"].iloc[t])
    broker = FakeBroker(price=price)
    broker.position = {"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": price}
    runner.state.entry_price = price
    runner.state.position_direction = "long"
    runner._last_broker_qty = 10
    log_trade = run_tick(runner, df.iloc[:t + 1], broker)
    assert broker.closed == [("AAPL", "long")]
    assert broker.position is not None and broker.position["side"] == "short"
    assert runner.state.position_direction == "short"
    assert [c.kwargs["reason"] for c in log_trade.call_args_list] == ["regime_flip", "entry"]

    # The next bar: the short is found; no "external" close is journaled.
    log_trade = run_tick(runner, df.iloc[:t + 2], broker)
    assert all(c.kwargs.get("reason") != "external" for c in log_trade.call_args_list)
    assert runner.state.entry_price is not None or broker.position is None


# ---------------------------------------------------------------------------
# graph_bot_live refusals
# ---------------------------------------------------------------------------


def _code(exc_info) -> str:
    return getattr(exc_info.value, "code", None)


def test_a_rule_regime_on_a_graph_bot_is_refused():
    cfg = switch_bot(regime=RegimeConfig(enabled=False))
    with pytest.raises(GraphValidationError) as info:
        graph_bot_live(nb_compile(cfg.graph), cfg)
    assert _code(info) == "group_invalid"

    long_graph = Graph.model_validate(graph_data(*leg("", "AAPL", None)))
    cfg = rule_config(kind="graph", graph=long_graph, regime=RegimeConfig(enabled=True))
    with pytest.raises(GraphValidationError) as info:
        graph_bot_live(nb_compile(long_graph), cfg)
    assert _code(info) == "group_invalid"


def test_a_regime_switch_group_needs_a_bot_made_for_it():
    cfg = switch_bot(graph_direction_mode=None)
    assert not cfg.is_bidirectional
    with pytest.raises(GraphValidationError) as info:
        graph_bot_live(nb_compile(cfg.graph), cfg)
    assert _code(info) == "direction_changed"
    with pytest.raises(GraphValidationError):
        BotManager._check_graph(cfg)


def test_a_long_group_with_a_close_and_reverse_regime_is_refused_live():
    """That group trades both sides, which a bot does only as regime_switch."""
    nodes, wires = leg("a_", "AAPL", "long_leg")
    nodes += [
        {"id": "a_sma", "type": "sma", "name": "sma", "parent": "long_leg", "params": {"period": 20}},
        {"id": "a_up", "type": "above", "name": "up", "parent": "long_leg",
         "params": {"a": "@close", "b": "@sma", "out": "@up"}},
        {"id": "a_reg", "type": "regime", "name": "regime", "parent": "long_leg",
         "params": {"on_flip": "close_and_reverse"}},
    ]
    wires += [("a_w6", "a_t", "a_sma"), ("a_w7", "a_sma", "a_up"), ("a_w8", "a_up", "a_reg")]
    graph = Graph.model_validate(graph_data([group("long_leg", "long"), *nodes], wires))
    cfg = rule_config(kind="graph", graph=graph, graph_group="long_leg",
                      graph_direction_mode="long")
    with pytest.raises(GraphValidationError) as info:
        graph_bot_live(nb_compile(graph), cfg)
    assert _code(info) == "group_invalid"


def test_a_long_group_with_a_gating_regime_runs_one_side():
    nodes, wires = leg("a_", "AAPL", "long_leg")
    nodes += [
        {"id": "a_sma", "type": "sma", "name": "sma", "parent": "long_leg", "params": {"period": 20}},
        {"id": "a_up", "type": "above", "name": "up", "parent": "long_leg",
         "params": {"a": "@close", "b": "@sma", "out": "@up"}},
        {"id": "a_reg", "type": "regime", "name": "regime", "parent": "long_leg",
         "params": {"on_flip": "close_only"}},
    ]
    wires += [("a_w6", "a_t", "a_sma"), ("a_w7", "a_sma", "a_up"), ("a_w8", "a_up", "a_reg")]
    graph = Graph.model_validate(graph_data([group("long_leg", "long"), *nodes], wires))
    cfg = rule_config(kind="graph", graph=graph, graph_group="long_leg",
                      graph_direction_mode="long")
    live = graph_bot_live(nb_compile(graph), cfg)
    assert not cfg.is_bidirectional
    assert live.plan.regime is not None and live.plan.on_flip == "close_only"
    sigs = {"regime": False, "entry": True}
    assert bot_runner.graph_entry_direction(live.plan, sigs, "long") == "flat"
    sigs["regime"] = True
    assert bot_runner.graph_entry_direction(live.plan, sigs, "long") == "long"
    eff = bot_runner.graph_tick_config(cfg, live.plan)
    assert eff.regime.on_flip == "close_only" and not eff.regime.enabled
    assert not eff.is_bidirectional


def test_the_group_cook_runs_off_the_event_loop(switch_frame):
    """Key Bugs Fixed: never evaluate a graph on the polling loop.  A
    regime_switch group reads more than Entry and Exit, so its tick cooks
    through sim_bridge.cook; that call runs in the executor."""
    import threading

    df, _cols, bars = switch_frame
    t = bars["short_entry"]
    threads: list[int] = []
    real_cook = sb.cook

    def spy(*a, **k):
        threads.append(threading.get_ident())
        return real_cook(*a, **k)

    loop_thread: list[int] = []
    real_tick = BotRunner._tick

    async def tick_on_loop(self):
        loop_thread.append(threading.get_ident())
        return await real_tick(self)

    runner = BotRunner(switch_bot(), BotState(), _Manager())
    with patch.object(sb, "cook", spy), patch.object(BotRunner, "_tick", tick_on_loop):
        run_tick(runner, df.iloc[:t + 1], FakeBroker(price=float(df["Close"].iloc[t])))
    assert threads and loop_thread
    assert all(th != loop_thread[0] for th in threads)
