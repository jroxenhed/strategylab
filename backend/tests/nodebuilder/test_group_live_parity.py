"""Group live parity (F435 W5 5.D, plan W5 "Tests to add").

For a two-group graph (AAPL long, MSFT regime_switch), a live bot on each
group gives the same signals as the graph backtest, bar for bar:

1. Signals: the runner's cook of the bars up to t (bot_runner.cook_graph_bar
   on the bot's own group program) gives, at bar t, the entry and exit the
   backtest's simulator callables give at t (sim_bridge.signal_fns over the
   backtest's cook of the whole frame), for every bar.
2. Trades: BotRunner._tick, run once per bar over a growing frame with an
   in-memory broker, opens and closes positions on exactly the bars and
   sides of the backtest's trades for that group.

The regime_switch group flips with on_flip "hold" here.  With close_only
or close_and_reverse the live bot and the simulator differ on the bar of a
flip (the simulator may also enter or exit on that bar; the live tick stops
after the flip), as they always have for rule regime bots.

Money safety (plan 8.4): no bot is started (each tick is awaited directly),
the broker is an in-memory fake, the journal and notifications are mocks,
and nothing is written to disk.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot_runner
from bot_manager import BotConfig, BotState
from bot_runner import BotRunner, cook_graph_bar, graph_bot_live
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import compile as nb_compile
from nodebuilder.models import Graph
from nodebuilder.prepare import build_graph_attrs
from nodebuilder.run import run_graph_backtest_cooked
from nodebuilder.trading import nodes_groups as ng
from nodebuilder.trading import sim_bridge as sb
from tests.test_bot_bidirectional_graph import FakeBroker, _Manager
from tests.test_graph_spawn import frame, graph_data, group, leg, switch_leg

FRAMES = {("AAPL", "1d"): frame(3), ("MSFT", "1d"): frame(4)}
GROUPS = (("long_leg", "AAPL", "long"), ("switch_leg", "MSFT", "regime_switch"))


def _graph() -> Graph:
    a_nodes, a_wires = leg("a_", "AAPL", "long_leg")
    b_nodes, b_wires = switch_leg("b_", "MSFT", "switch_leg", on_flip="hold")
    return Graph.model_validate(graph_data(
        [group("long_leg", "long"), group("switch_leg", "regime_switch"), *a_nodes, *b_nodes],
        a_wires + b_wires))


GRAPH = _graph()


def _bot(group_name: str, symbol: str, mode: str) -> BotConfig:
    return BotConfig(
        strategy_name=f"pair ▸ {group_name}", symbol=symbol, interval="1d",
        buy_rules=[], sell_rules=[], long_buy_rules=None, long_sell_rules=None,
        short_buy_rules=None, short_sell_rules=None, allocated_capital=100_000.0,
        kind="graph", graph=GRAPH, graph_id="g_000000000001", graph_rev=1,
        graph_group=group_name, graph_direction_mode=mode,
        direction="long" if mode == "regime_switch" else mode, bot_id=f"bot-{group_name}",
    )


@pytest.fixture(scope="module")
def backtest():
    req = GraphBacktestRequest(graph=GRAPH, ticker="AAPL", start="2023-01-02", end="2023-12-29",
                               interval="1d", source="yahoo", initial_capital=10_000.0)
    response, _cook = run_graph_backtest_cooked(req, frames=FRAMES, keep_all=False)
    return {g.name: g for g in response.groups}


def _backtest_callables(group_name: str, df):
    """The simulator's (buy_fn, sell_fn, regime column) for one group, from a
    cook of the whole frame the way run.py cooks a group on its own frame."""
    program = nb_compile(GRAPH)
    g = ng.group_named(program, group_name)
    plan = g.plan_for(None)
    frame_program = ng.program_for_steps(program, g.step_ids)
    attrs = build_graph_attrs(frame_program, df, None)
    result = sb.cook(frame_program, attrs, plan.keep_ids())
    buy_fn, sell_fn = sb.signal_fns(plan, result, len(df))
    regime = (result.column(plan.regime.node_id, plan.regime.attr).astype(bool)
              if plan.regime is not None else None)
    return plan, buy_fn, sell_fn, regime


@pytest.mark.parametrize("group_name,symbol,mode", GROUPS)
def test_runner_signals_match_backtest_signals_bar_for_bar(group_name, symbol, mode):
    df = FRAMES[(symbol, "1d")]
    plan, buy_fn, sell_fn, regime = _backtest_callables(group_name, df)
    cfg = _bot(group_name, symbol, mode)
    live = graph_bot_live(nb_compile(cfg.graph), cfg)
    assert live.plan == plan
    assert live.references == ()
    fired = {"entry": 0, "exit": 0}
    for t in range(1, len(df)):
        _attrs, sigs = cook_graph_bar(live.program, df.iloc[:t + 1], None, live.plan)
        active = bool(regime[t]) if regime is not None else True
        want_buy, _rules, want_side = buy_fn(t, active)
        side = bot_runner.graph_entry_direction(live.plan, sigs, cfg.direction)
        got_buy = bot_runner.graph_buy_signal(live.plan, sigs, side)
        assert got_buy == bool(want_buy), f"entry differs at bar {t}"
        if want_buy:
            assert side == want_side, f"entry side differs at bar {t}"
        for pos_side in (("long", "short") if mode == "regime_switch" else (mode,)):
            want_exit, _rules = sell_fn(t, pos_side, active)
            got_exit = bot_runner.graph_sell_signal(live.plan, sigs, pos_side)
            assert got_exit == bool(want_exit), f"{pos_side} exit differs at bar {t}"
            fired["exit"] += bool(want_exit)
        fired["entry"] += bool(want_buy)
    # The fixture is not vacuous: both signals fire many times.
    assert fired["entry"] > 10 and fired["exit"] > 10


def _walk(cfg: BotConfig, df):
    """Run one tick per bar over a growing frame; return the broker's fills
    as (date, type) pairs, type in buy / short / sell / cover."""
    runner = BotRunner(cfg, BotState(), _Manager())
    broker = FakeBroker()
    fills: list[tuple[str, str]] = []
    bar = {"t": 0}

    real_submit, real_close = broker.submit_order, broker.close_position

    def submit(req):
        fills.append((df.index[bar["t"]].strftime("%Y-%m-%d"),
                      "short" if req.side == "sell" else "buy"))
        return real_submit(req)

    def close(symbol):
        side = broker.position["side"] if broker.position else None
        fills.append((df.index[bar["t"]].strftime("%Y-%m-%d"),
                      "cover" if side == "short" else "sell"))
        return real_close(symbol)

    broker.submit_order, broker.close_position = submit, close
    fetch = AsyncMock()

    async def go():
        patches = [
            patch("bot_runner.fetch_ohlcv_async", fetch),
            patch("bot_runner.get_trading_provider", return_value=broker),
            patch("bot_runner.notify_entry", AsyncMock()),
            patch("bot_runner.notify_exit", AsyncMock()),
            patch("bot_runner.notify_error", AsyncMock()),
            patch("bot_runner._log_trade", MagicMock()),
            patch("bot_runner.compute_bidirectional_pnl", return_value=0.0),
            patch("bot_runner.compute_realized_pnl", return_value=0.0),
            patch("asyncio.sleep", AsyncMock()),
        ]
        for p in patches:
            p.start()
        try:
            for t in range(1, len(df)):
                bar["t"] = t
                broker.price = float(df["Close"].iloc[t])
                fetch.return_value = df.iloc[:t + 1]
                await runner._tick()
                assert runner.state.status != "error", runner.state.pause_reason
        finally:
            for p in reversed(patches):
                p.stop()

    asyncio.run(go())
    return fills


@pytest.mark.parametrize("group_name,symbol,mode", GROUPS)
def test_live_bot_trades_on_the_backtest_bars(backtest, group_name, symbol, mode):
    df = FRAMES[(symbol, "1d")]
    want = [(t["date"], t["type"]) for t in backtest[group_name].trades]
    got = _walk(_bot(group_name, symbol, mode), df)
    assert len(want) >= 6, "the fixture should trade several times"
    if mode == "regime_switch":
        assert {kind for _d, kind in want} >= {"buy", "short"}, "both sides should trade"
    assert got == want


# ---------------------------------------------------------------------------
# Reference tickers (plan D8): fetched next to the bot's bars, all at once
# ---------------------------------------------------------------------------


def test_reference_frames_are_fetched_together_and_passed_to_the_cook(monkeypatch):
    """A group that reads a second Ticker (SPY) fetches SPY's bars in the
    same asyncio.gather as its own, and the cook gets them keyed by
    (SYMBOL, interval), and cooks the RSI over SPY's own bars (W5 5.C)."""
    a_nodes, a_wires = leg("a_", "AAPL", "long_leg")
    spy_node = {"id": "a_spy", "type": "ticker", "name": "spy", "parent": "long_leg",
                "params": {"symbol": "SPY", "interval": "1d"}}
    a_wires = [w if w[0] != "a_w1" else ("a_w1", "a_spy", "a_rsi") for w in a_wires]
    graph = Graph.model_validate(graph_data([group("long_leg", "long"), *a_nodes, spy_node],
                                            a_wires))
    cfg = _bot("long_leg", "AAPL", "long").model_copy(update={"graph": graph})
    live = graph_bot_live(nb_compile(graph), cfg)
    assert live.references == (("SPY", "1d"),)

    own, ref = FRAMES[("AAPL", "1d")], FRAMES[("MSFT", "1d")]
    in_flight = {"now": 0, "max": 0}
    calls: list[tuple] = []

    async def fetch(symbol, start, end, interval, source):
        calls.append((symbol, interval, source))
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        for _ in range(3):
            await asyncio.sleep(0)
        in_flight["now"] -= 1
        return own if symbol == "AAPL" else ref

    seen: dict = {}
    real_build = bot_runner.build_graph_attrs

    def build_spy(program, df, trailing_stop, frames=None, interval=None):
        seen["frames"] = frames
        seen["interval"] = interval
        attrs = real_build(program, df, trailing_stop, frames=frames, interval=interval)
        seen["attrs"] = attrs
        return attrs

    runner = BotRunner(cfg, BotState(), _Manager())

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", side_effect=fetch), \
             patch("bot_runner.build_graph_attrs", side_effect=build_spy), \
             patch("bot_runner.get_trading_provider", return_value=FakeBroker()), \
             patch("bot_runner.notify_entry", AsyncMock()), \
             patch("bot_runner.notify_error", AsyncMock()), \
             patch("bot_runner._log_trade", MagicMock()), \
             patch("bot_runner.compute_realized_pnl", return_value=0.0):
            await runner._tick()

    asyncio.run(go())
    assert sorted(calls) == [("AAPL", "1d", "alpaca-iex"), ("SPY", "1d", "alpaca-iex")]
    assert in_flight["max"] == 2, "the two fetches did not run together"
    assert set(seen["frames"]) == {("SPY", "1d")}
    assert seen["frames"][("SPY", "1d")] is ref
    # The bot's interval reaches the alignment, as in the backtest (LM-8).
    assert seen["interval"] == "1d"
    assert runner.state.status != "error"
    # The cook read SPY's bars (here MSFT's synthetic frame), not AAPL's.
    from nodebuilder.trading.align import REFS_KEY
    refs = seen["attrs"][REFS_KEY]
    assert list(refs.frames) == [("SPY", "1d")]
    import numpy as np
    spy_close = refs.frames[("SPY", "1d")].bars["@close"]
    assert np.array_equal(spy_close, ref["Close"].to_numpy())
    assert not np.array_equal(spy_close, own["Close"].to_numpy())


def test_a_failed_reference_fetch_waits_for_the_next_tick(monkeypatch):
    """The bar is retried next tick, and since F435 W5 LM-1 the failure
    raises, so run() counts it toward MAX_CONSEC_ERRORS (see
    tests/test_reference_outage.py for the exits that still run)."""
    a_nodes, a_wires = leg("a_", "AAPL", "long_leg")
    spy_node = {"id": "a_spy", "type": "ticker", "name": "spy", "parent": "long_leg",
                "params": {"symbol": "SPY", "interval": "1d"}}
    a_wires = [w if w[0] != "a_w1" else ("a_w1", "a_spy", "a_rsi") for w in a_wires]
    graph = Graph.model_validate(graph_data([group("long_leg", "long"), *a_nodes, spy_node],
                                            a_wires))
    cfg = _bot("long_leg", "AAPL", "long").model_copy(update={"graph": graph})
    runner = BotRunner(cfg, BotState(), _Manager())
    own = FRAMES[("AAPL", "1d")]

    async def fetch(symbol, *a, **k):
        if symbol == "SPY":
            raise RuntimeError("provider down")
        return own

    cook = MagicMock(side_effect=AssertionError("no cook without the reference frame"))

    async def go():
        with patch("bot_runner.fetch_ohlcv_async", side_effect=fetch), \
             patch("bot_runner.cook_graph_bar", cook), \
             patch("bot_runner.get_trading_provider", return_value=FakeBroker()):
            with pytest.raises(bot_runner.ReferenceUnavailableError):
                await runner._tick()

    asyncio.run(go())
    assert runner.state.last_bar_time is None  # the bar is retried next tick
    assert any("Reference fetch failed for SPY 1d" in e["msg"] for e in runner.state.activity_log)
