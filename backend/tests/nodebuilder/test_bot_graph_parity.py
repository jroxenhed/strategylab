"""F435 W0 item 0.B: a live graph bot trades with the settings its backtest used.

Covers:
  - the graph's Stop Loss node is the bot's effective stop, on entry (the
    broker stop order) and on exit (the polled stop), and a per-direction stop
    in the bot config cannot beat it;
  - Position Size 100 and 1 size the same way live as in the graph backtest;
  - backtest_bot on a graph bot runs the graph backtest and gets trades;
  - add_bot / update_bot refuse a bad graph with 400 {detail, node_id};
  - graph compile, indicators and evaluation run in the executor, not on the
    event loop.

No test starts a bot or places a real order: brokers are stubs, fetches are
patched.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from bot_manager import BotConfig, BotManager, BotState
from broker import OrderResult
from models import StrategyRequest, TrailingStopConfig
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import compile as nb_compile
from nodebuilder.models import Graph
from nodebuilder.run import _apply_settings_overrides, run_graph_backtest
from nodebuilder.sim_settings import BOT_DIRECTION_FIELDS, apply_to_bot_config


# ---------------------------------------------------------------------------
# Graph and data helpers
# ---------------------------------------------------------------------------

def _graph(settings: dict | None = None, entry_threshold: float = 0.0,
           exit_threshold: float = 0.0, with_entry: bool = True,
           wire_entry: bool = True) -> Graph:
    """Close above entry_threshold -> Entry, close below exit_threshold -> Exit.

    With the default thresholds, Entry is always true and Exit never fires, so
    a tick with no position always enters.  `settings` maps a settings node
    type to its params, e.g. {"stop_loss": {"pct": 3.0}}.
    """
    nodes = {
        "/ticker": {"id": "/ticker", "type": "ticker", "params": {}},
        "/above": {"id": "/above", "type": "above", "params": {"threshold": entry_threshold}},
        "/below": {"id": "/below", "type": "below", "params": {"threshold": exit_threshold}},
        "/exit": {"id": "/exit", "type": "exit", "params": {}},
    }
    wires = [
        {"id": "w1", "from": "/ticker", "to": "/above"},
        {"id": "w2", "from": "/ticker", "to": "/below"},
        {"id": "w4", "from": "/below", "to": "/exit"},
    ]
    if with_entry:
        nodes["/entry"] = {"id": "/entry", "type": "entry", "params": {}}
        if wire_entry:
            wires.append({"id": "w3", "from": "/above", "to": "/entry"})
    for node_type, params in (settings or {}).items():
        path = f"/{node_type}"
        nodes[path] = {"id": path, "type": node_type, "params": params}
    return Graph.model_validate({"_version": 1, "nodes": nodes, "wires": wires})


def _rsi_graph(settings: dict | None = None) -> Graph:
    """RSI(14) below 30 -> Entry, above 70 -> Exit.  Trades on a random walk."""
    nodes = {
        "/ticker": {"id": "/ticker", "type": "ticker", "params": {}},
        "/rsi": {"id": "/rsi", "type": "rsi", "params": {"period": 14, "type": "wilder"}},
        "/buy": {"id": "/buy", "type": "below", "params": {"threshold": 30}},
        "/sell": {"id": "/sell", "type": "above", "params": {"threshold": 70}},
        "/entry": {"id": "/entry", "type": "entry", "params": {}},
        "/exit": {"id": "/exit", "type": "exit", "params": {}},
    }
    wires = [
        {"id": "w1", "from": "/ticker", "to": "/rsi"},
        {"id": "w2", "from": "/rsi", "to": "/buy"},
        {"id": "w3", "from": "/rsi", "to": "/sell"},
        {"id": "w4", "from": "/buy", "to": "/entry"},
        {"id": "w5", "from": "/sell", "to": "/exit"},
    ]
    for node_type, params in (settings or {}).items():
        path = f"/{node_type}"
        nodes[path] = {"id": path, "type": node_type, "params": params}
    return Graph.model_validate({"_version": 1, "nodes": nodes, "wires": wires})


def _walk_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Seeded synthetic daily bars with every OHLCV column."""
    rng = np.random.default_rng(seed)
    close = np.clip(100 + rng.standard_normal(n).cumsum() * 1.5, 5, None)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "Open": close + rng.uniform(-0.5, 0.5, n),
        "High": close + rng.uniform(0.5, 2.0, n),
        "Low": close - rng.uniform(0.5, 2.0, n),
        "Close": close,
        "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


def _tick_df(last_close: float = 100.0, n: int = 30) -> pd.DataFrame:
    """Flat bars ending at last_close, for one live tick."""
    closes = [100.0] * (n - 1) + [last_close]
    idx = pd.date_range(end="2026-01-10", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "Open": closes,
        "High": [c * 1.001 for c in closes],
        "Low": [c * 0.999 for c in closes],
        "Close": closes,
        "Volume": [1000.0] * n,
    }, index=idx)


def _bot_config(graph: Graph | None, **overrides) -> BotConfig:
    base = dict(
        bot_id="bot-graph-1",
        strategy_name="graph test",
        symbol="AAPL",
        interval="1d",
        buy_rules=[],
        sell_rules=[],
        allocated_capital=10_000.0,
        broker="alpaca",
        data_source="yahoo",
        direction="long",
        kind="graph",
        graph=graph,
    )
    base.update(overrides)
    return BotConfig(**base)


def _backtest_settings(graph: Graph, **req_fields) -> dict:
    """The simulator fields the graph backtest runs with for this graph."""
    req = GraphBacktestRequest(graph=graph, ticker="AAPL", start="2024-01-01",
                               end="2025-01-01", **req_fields)
    return _apply_settings_overrides(req, nb_compile(graph).simulator_settings)


# ---------------------------------------------------------------------------
# Live tick harness (stub broker, patched fetch and journal)
# ---------------------------------------------------------------------------

def _order(price: float, qty: int, side: str, order_id: str = "o-1") -> OrderResult:
    return OrderResult(order_id=order_id, symbol="AAPL", qty=qty, side=side,
                       status="filled", filled_avg_price=price, filled_qty=qty)


class _StubProvider:
    """Broker stub.  Records orders; never talks to a broker."""

    def __init__(self, positions=None, price: float = 100.0):
        self._positions = positions or []
        self.submit_order = MagicMock(side_effect=lambda req: _order(price, req.qty, req.side))
        self.get_order = MagicMock(return_value=_order(price, 1, "buy"))
        self.get_latest_quote = MagicMock(return_value=(99.9, 100.1))
        self.get_orders = MagicMock(return_value=[])
        self.cancel_order = MagicMock(return_value=None)
        self.close_position = MagicMock(return_value=_order(price, 1, "sell", "c-1"))

    def get_positions(self):
        return list(self._positions)


class _Manager:
    def save(self):
        pass


def _run_tick(cfg: BotConfig, state: BotState, df: pd.DataFrame, provider: _StubProvider,
              extra_patches=()):
    from bot_runner import BotRunner

    runner = BotRunner(cfg, state, _Manager())
    patches = [
        patch("bot_runner.fetch_ohlcv_async", new_callable=AsyncMock, return_value=df),
        patch("bot_runner.get_trading_provider", return_value=provider),
        patch("bot_runner._log_trade"),
        patch("bot_runner.compute_realized_pnl", return_value=0.0),
        patch("bot_runner.compute_bidirectional_pnl", return_value=0.0),
        patch("bot_runner.notify_entry", new_callable=AsyncMock),
        patch("bot_runner.notify_exit", new_callable=AsyncMock),
        patch("bot_runner.notify_error", new_callable=AsyncMock),
        patch("asyncio.sleep", new_callable=AsyncMock),
        *extra_patches,
    ]

    async def go():
        for p in patches:
            p.start()
        try:
            await runner._tick()
        finally:
            for p in reversed(patches):
                p.stop()

    asyncio.run(go())
    return runner


def _entry_order(provider: _StubProvider):
    assert provider.submit_order.call_count == 1, "expected exactly one entry order"
    return provider.submit_order.call_args.args[0]


# ---------------------------------------------------------------------------
# 1. Stop Loss: the graph's stop is the bot's stop
# ---------------------------------------------------------------------------

def test_overlay_stop_equals_backtest_stop():
    graph = _graph({"stop_loss": {"pct": 3.0}})
    cfg = _bot_config(graph, stop_loss_pct=10.0)
    eff = apply_to_bot_config(cfg, nb_compile(graph).simulator_settings)
    assert eff.stop_loss_pct == 3.0
    assert eff.stop_loss_pct == _backtest_settings(graph, stop_loss_pct=10.0)["stop_loss_pct"]
    # The overlay is a copy: the stored config is unchanged.
    assert cfg.stop_loss_pct == 10.0


def test_graph_stop_sets_the_entry_stop_order():
    """The broker stop order placed on entry uses the graph's 3%, not the config's 10%."""
    graph = _graph({"stop_loss": {"pct": 3.0}})
    cfg = _bot_config(graph, stop_loss_pct=10.0)
    provider = _StubProvider(price=100.0)
    _run_tick(cfg, BotState(), _tick_df(100.0), provider)
    order = _entry_order(provider)
    assert order.order_type == "stop"
    assert order.stop_price == round(100.0 * (1 - 3.0 / 100), 2)


def test_per_direction_stop_does_not_beat_graph_stop_on_entry():
    graph = _graph({"stop_loss": {"pct": 3.0}})
    cfg = _bot_config(graph, stop_loss_pct=10.0, long_stop_loss_pct=20.0, short_stop_loss_pct=20.0)
    provider = _StubProvider(price=100.0)
    _run_tick(cfg, BotState(), _tick_df(100.0), provider)
    assert _entry_order(provider).stop_price == 97.0


def test_per_direction_stop_does_not_beat_graph_stop_on_exit():
    """In position at 100, price 96: the graph's 3% stop fires; the 20% per-direction stop would not."""
    graph = _graph({"stop_loss": {"pct": 3.0}})
    cfg = _bot_config(graph, long_stop_loss_pct=20.0)
    state = BotState(entry_price=100.0, position_direction="long")
    provider = _StubProvider(
        positions=[{"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": 100.0}], price=96.0
    )
    exit_mock = AsyncMock(return_value=True)
    _run_tick(cfg, state, _tick_df(96.0), provider,
              extra_patches=[patch("bot_runner.BotRunner._execute_exit", exit_mock)])
    exit_mock.assert_awaited_once()
    assert exit_mock.await_args.args[2] == "stop_loss"
    # The exit helpers get the effective config too.
    assert exit_mock.await_args.args[0].stop_loss_pct == 3.0
    provider.submit_order.assert_not_called()


def test_without_graph_stop_the_config_stop_still_applies():
    """No Stop Loss node: the bot config's plain stop is used, as in the backtest request."""
    graph = _graph()
    cfg = _bot_config(graph, stop_loss_pct=10.0, long_stop_loss_pct=20.0)
    eff = apply_to_bot_config(cfg, nb_compile(graph).simulator_settings)
    assert eff.stop_loss_pct == 10.0 == _backtest_settings(graph, stop_loss_pct=10.0)["stop_loss_pct"]
    # The graph backtest never reads per-direction fields, so live clears them.
    for name in BOT_DIRECTION_FIELDS:
        assert getattr(eff, name) is None


def test_overlay_clears_every_per_direction_field():
    ts = TrailingStopConfig(type="pct", value=9.0)
    graph = _graph({"stop_loss": {"pct": 3.0}, "position_size": {"size": 0.5}})
    cfg = _bot_config(
        graph,
        long_stop_loss_pct=20.0, short_stop_loss_pct=20.0,
        long_position_size=0.1, short_position_size=0.1,
        long_trailing_stop=ts, short_trailing_stop=ts,
        long_max_bars_held=3, short_max_bars_held=3,
    )
    eff = apply_to_bot_config(cfg, nb_compile(graph).simulator_settings)
    for name in BOT_DIRECTION_FIELDS:
        assert getattr(eff, name) is None, name
    assert eff.position_size == 0.5


def test_graph_trailing_stop_and_slippage_win():
    graph = _graph({"trailing_stop": {"type": "pct", "value": 4.0}, "slippage": {"bps": 7.0}})
    cfg = _bot_config(graph, trailing_stop=TrailingStopConfig(type="pct", value=12.0), slippage_bps=1.0)
    eff = apply_to_bot_config(cfg, nb_compile(graph).simulator_settings)
    bt = _backtest_settings(graph, trailing_stop=TrailingStopConfig(type="pct", value=12.0), slippage_bps=1.0)
    assert eff.trailing_stop.value == 4.0 == bt["trailing_stop"].value
    assert eff.slippage_bps == 7.0 == bt["slippage_bps"]


def test_commission_keys_are_skipped_for_bots():
    """BotConfig has no commission fields; the overlay must not fail on them."""
    graph = _graph({"commission": {"per_share_rate": 0.0035, "min_per_order": 0.35}})
    eff = apply_to_bot_config(_bot_config(graph), nb_compile(graph).simulator_settings)
    assert not hasattr(eff, "per_share_rate")


# ---------------------------------------------------------------------------
# 2. Position Size: 100 and 1 behave as in the backtest
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size, expected", [(100, 1.0), (1, 1.0), (0.5, 0.5), (0.001, 0.01)])
def test_size_overlay_matches_backtest_clamp(size, expected):
    graph = _graph({"position_size": {"size": size}})
    eff = apply_to_bot_config(_bot_config(graph), nb_compile(graph).simulator_settings)
    settings = _backtest_settings(graph)
    backtest_size = StrategyRequest(ticker="AAPL", start="2024-01-01", end="2025-01-01",
                                    buy_rules=[], sell_rules=[],
                                    position_size=settings["position_size"]).position_size
    assert eff.position_size == expected == backtest_size


def test_size_100_and_1_give_the_same_live_quantity():
    quantities = {}
    for size in (100, 1):
        graph = _graph({"position_size": {"size": size}})
        provider = _StubProvider(price=100.0)
        _run_tick(_bot_config(graph), BotState(), _tick_df(100.0), provider)
        quantities[size] = _entry_order(provider).qty
    # 10,000 capital at 100 per share, full size: 100 shares, never 100x that.
    assert quantities[100] == quantities[1] == 100


def test_size_half_live_quantity():
    graph = _graph({"position_size": {"size": 0.5}})
    provider = _StubProvider(price=100.0)
    _run_tick(_bot_config(graph, position_size=1.0), BotState(), _tick_df(100.0), provider)
    assert _entry_order(provider).qty == 50


def test_size_100_and_1_give_the_same_backtest():
    df = _walk_df()
    results = []
    for size in (100, 1):
        req = GraphBacktestRequest(graph=_rsi_graph({"position_size": {"size": size}}),
                                   ticker="AAPL", start="2024-01-01", end="2025-02-01")
        results.append(run_graph_backtest(req, df=df))
    assert results[0].trades == results[1].trades
    assert results[0].summary["final_value"] == results[1].summary["final_value"]


# ---------------------------------------------------------------------------
# 3. backtest_bot runs the graph backtest
# ---------------------------------------------------------------------------

def _manager_with(cfg: BotConfig) -> BotManager:
    mgr = BotManager()
    mgr.bot_fund = 100_000.0
    mgr.save = lambda: None  # never write bots.json
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    return mgr


def test_backtest_bot_runs_graph_backtest_with_trades():
    cfg = _bot_config(_rsi_graph({"stop_loss": {"pct": 4.0}}), stop_loss_pct=25.0)
    mgr = _manager_with(cfg)
    captured = {}

    def spy(req, df=None):
        captured["req"] = req
        captured["result"] = run_graph_backtest(req, df=df)
        return captured["result"]

    with patch("shared._fetch", return_value=_walk_df()), \
         patch("bot_manager.run_graph_backtest", side_effect=spy), \
         patch("bot_manager.run_backtest") as rule_backtest:
        mgr.backtest_bot(cfg.bot_id)

    rule_backtest.assert_not_called()
    _, state = mgr.bots[cfg.bot_id]
    assert state.status == "stopped"
    assert "error" not in state.backtest_summary, state.backtest_summary
    assert len(captured["result"].trades) > 0
    assert state.backtest_summary["num_trades"] > 0
    # The bot config fills the request; the graph's stop wins inside the run.
    assert captured["req"].stop_loss_pct == 25.0
    assert captured["req"].initial_capital == cfg.allocated_capital
    assert "exit_connected" in state.backtest_summary


def test_backtest_bot_graph_error_is_recorded():
    cfg = _bot_config(_graph(with_entry=False))
    mgr = _manager_with(cfg)
    with patch("shared._fetch", return_value=_walk_df()):
        mgr.backtest_bot(cfg.bot_id)
    _, state = mgr.bots[cfg.bot_id]
    assert state.status == "stopped"
    assert "error" in state.backtest_summary


# ---------------------------------------------------------------------------
# 4. add_bot / update_bot refuse a bad graph with 400 {detail, node_id}
# ---------------------------------------------------------------------------

@pytest.fixture
def client_real_mgr():
    import routes.bots as bots_route
    from main import app

    mgr = BotManager()
    mgr.bot_fund = 100_000.0
    mgr.save = lambda: None
    original = bots_route.bot_manager
    bots_route.bot_manager = mgr
    try:
        yield TestClient(app), mgr
    finally:
        bots_route.bot_manager = original


def _add_body(graph: Graph | dict) -> dict:
    graph_json = graph if isinstance(graph, dict) else graph.model_dump(by_alias=True)
    return {
        "strategy_name": "graph bot",
        "symbol": "AAPL",
        "interval": "1d",
        "buy_rules": [],
        "sell_rules": [],
        "allocated_capital": 1000.0,
        "data_source": "yahoo",
        "kind": "graph",
        "graph": graph_json,
    }


def test_add_bot_graph_without_entry_is_400(client_real_mgr):
    client, mgr = client_real_mgr
    r = client.post("/api/bots", json=_add_body(_graph(with_entry=False)))
    assert r.status_code == 400, r.text
    body = r.json()
    assert "node_id" in body and body["node_id"] is None
    assert "entry" in body["detail"].lower()
    assert mgr.bots == {}


def test_add_bot_unwired_entry_names_the_node(client_real_mgr):
    client, mgr = client_real_mgr
    r = client.post("/api/bots", json=_add_body(_graph(wire_entry=False)))
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "/entry"
    assert mgr.bots == {}


def test_add_bot_unsupported_node_names_the_node(client_real_mgr):
    client, _ = client_real_mgr
    graph = _graph().model_dump(by_alias=True)
    graph["nodes"]["/stoch"] = {"id": "/stoch", "type": "stochastic", "params": {}}
    r = client.post("/api/bots", json=_add_body(graph))
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "/stoch"


def test_add_bot_cyclic_graph_is_400_not_500(client_real_mgr):
    client, _ = client_real_mgr
    graph = _graph().model_dump(by_alias=True)
    graph["wires"].append({"id": "loop", "from": "/above", "to": "/ticker"})
    r = client.post("/api/bots", json=_add_body(graph))
    assert r.status_code == 400, r.text
    assert "node_id" in r.json()


def test_add_bot_htf_graph_is_400(client_real_mgr):
    client, _ = client_real_mgr
    graph = _graph().model_dump(by_alias=True)
    graph["nodes"]["/above"]["params"]["timeframe"] = "1h"
    r = client.post("/api/bots", json=_add_body(graph))
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "/above"


def test_add_bot_good_graph_is_added(client_real_mgr):
    client, mgr = client_real_mgr
    r = client.post("/api/bots", json=_add_body(_graph({"stop_loss": {"pct": 3.0}})))
    assert r.status_code == 200, r.text
    assert r.json()["bot_id"] in mgr.bots


def test_add_bot_other_bad_field_is_still_422(client_real_mgr):
    client, _ = client_real_mgr
    body = _add_body(_graph())
    del body["strategy_name"]
    r = client.post("/api/bots", json=body)
    assert r.status_code == 422


def test_update_bot_bad_graph_is_400_and_keeps_old_graph(client_real_mgr):
    client, mgr = client_real_mgr
    good = _graph()
    cfg = _bot_config(good)
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    r = client.patch(f"/api/bots/{cfg.bot_id}",
                     json={"graph": _graph(wire_entry=False).model_dump(by_alias=True)})
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "/entry"
    assert mgr.bots[cfg.bot_id][0].graph == good


def test_update_bot_cyclic_graph_is_400(client_real_mgr):
    client, mgr = client_real_mgr
    cfg = _bot_config(_graph())
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    graph = _graph().model_dump(by_alias=True)
    graph["wires"].append({"id": "loop", "from": "/above", "to": "/ticker"})
    r = client.patch(f"/api/bots/{cfg.bot_id}", json={"graph": graph})
    assert r.status_code == 400, r.text


def test_update_bot_good_graph_is_saved(client_real_mgr):
    client, mgr = client_real_mgr
    cfg = _bot_config(_graph())
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    new = _graph({"stop_loss": {"pct": 2.0}})
    r = client.patch(f"/api/bots/{cfg.bot_id}", json={"graph": new.model_dump(by_alias=True)})
    assert r.status_code == 200, r.text
    assert "/stop_loss" in mgr.bots[cfg.bot_id][0].graph.nodes


# ---------------------------------------------------------------------------
# 5. Graph work runs in the executor, never on the event loop
# ---------------------------------------------------------------------------

def test_graph_compile_and_evaluation_run_off_the_event_loop():
    import nodebuilder.evaluator as evaluator
    import bot_runner

    threads: dict[str, int] = {}
    real_eval = evaluator.evaluate_graph
    real_build = bot_runner.build_graph_attrs
    real_compile = bot_runner.compile_bot_graph

    def eval_spy(*args, **kwargs):
        threads["evaluate"] = threading.get_ident()
        return real_eval(*args, **kwargs)

    def build_spy(*args, **kwargs):
        threads["attrs"] = threading.get_ident()
        return real_build(*args, **kwargs)

    def compile_spy(*args, **kwargs):
        threads["compile"] = threading.get_ident()
        return real_compile(*args, **kwargs)

    loop_thread = threading.get_ident()  # asyncio.run drives the loop on this thread
    provider = _StubProvider(price=100.0)
    _run_tick(_bot_config(_graph()), BotState(), _tick_df(100.0), provider, extra_patches=[
        patch("nodebuilder.evaluator.evaluate_graph", side_effect=eval_spy),
        patch("bot_runner.build_graph_attrs", side_effect=build_spy),
        patch("bot_runner.compile_bot_graph", side_effect=compile_spy),
    ])
    assert set(threads) == {"evaluate", "attrs", "compile"}
    for name, ident in threads.items():
        assert ident != loop_thread, f"{name} ran on the event loop thread"
    # The tick still entered, so the executor results were used.
    assert provider.submit_order.call_count == 1


def test_build_graph_attrs_adds_atr_for_atr_trailing_stop():
    """The backtest computes ATR(14) for an ATR trailing stop; live must too."""
    from bot_runner import build_graph_attrs

    program = nb_compile(_graph())
    df = _walk_df(60)
    with_atr = build_graph_attrs(program, df, TrailingStopConfig(type="atr", value=2.0))
    assert "atr" in with_atr and with_atr["atr"].notna().any()
    without = build_graph_attrs(program, df, TrailingStopConfig(type="pct", value=2.0))
    assert "atr" not in without
    assert "@always_false" in with_atr


# ---------------------------------------------------------------------------
# 6. F435 wave 0 review: live stops and stored graphs that stop compiling
# ---------------------------------------------------------------------------

def _in_position(price: float):
    state = BotState(entry_price=100.0, position_direction="long", trail_peak=100.0)
    provider = _StubProvider(
        positions=[{"symbol": "AAPL", "side": "long", "qty": 10, "avg_entry": 100.0}], price=price
    )
    return state, provider


def test_graph_stop_with_atr_trailing_exits_live_at_minus_10():
    """LT-1: stop 3% + ATR trailing 2 used to have no stop at all live (ATR read
    as 0, fixed stop skipped with a trailing stop, no broker stop leg)."""
    graph = _graph({"stop_loss": {"pct": 3.0},
                    "trailing_stop": {"type": "atr", "value": 2.0, "source": "close"}})
    state, provider = _in_position(90.0)
    exit_mock = AsyncMock(return_value=True)
    _run_tick(_bot_config(graph), state, _tick_df(90.0), provider,
              extra_patches=[patch("bot_runner.BotRunner._execute_exit", exit_mock)])
    exit_mock.assert_awaited_once()
    assert exit_mock.await_args.args[2] == "stop_loss"
    # The ATR trail is set too: peak 100 less 2 x ATR (about 0.9 with the drop).
    assert state.trail_stop_price is not None and 97.0 < state.trail_stop_price < 100.0


def test_graph_atr_trailing_alone_exits_live():
    graph = _graph({"trailing_stop": {"type": "atr", "value": 2.0, "source": "close"}})
    state, provider = _in_position(90.0)
    exit_mock = AsyncMock(return_value=True)
    _run_tick(_bot_config(graph, stop_loss_pct=None), state, _tick_df(90.0), provider,
              extra_patches=[patch("bot_runner.BotRunner._execute_exit", exit_mock)])
    exit_mock.assert_awaited_once()
    assert exit_mock.await_args.args[2] == "trailing_stop"


def _no_longer_compiling_graph() -> Graph:
    """A graph with a node type compile refuses (saved before it was refused)."""
    data = _graph().model_dump(by_alias=True)
    data["nodes"]["/stoch"] = {"id": "/stoch", "type": "stochastic", "params": {}}
    return Graph.model_validate(data)


def test_tick_pauses_and_alerts_when_the_stored_graph_no_longer_compiles():
    """LT-3/BC-3: the tick raised before any exit check and the bot retried
    forever as 'running' with the position unmanaged."""
    import bot_runner

    state, provider = _in_position(80.0)
    notify = AsyncMock()
    _run_tick(_bot_config(_no_longer_compiling_graph()), state, _tick_df(80.0), provider,
              extra_patches=[patch("bot_runner.notify_error", notify)])
    assert state.status == "error"
    assert "Graph does not compile" in state.pause_reason
    notify.assert_called_once()
    assert "open position" in notify.call_args.kwargs["error_msg"]
    provider.submit_order.assert_not_called()


def test_tick_retries_the_bar_after_a_graph_indicator_error():
    """BC-3: after an indicator error the next tick saw 'same bar' and skipped
    the exit checks for the whole bar."""
    state, provider = _in_position(100.0)
    state.last_bar_time = "earlier"
    _run_tick(_bot_config(_graph()), state, _tick_df(100.0), provider,
              extra_patches=[patch("bot_runner.build_graph_attrs", side_effect=RuntimeError("boom"))])
    assert state.last_bar_time == "earlier"


def test_tick_retries_the_bar_after_an_unexpected_compile_error():
    """BC-3/LT-3: an error that is not a graph error (a compile bug) must not
    leave the bar marked done, or later ticks skip the exit checks silently."""
    state, provider = _in_position(100.0)
    state.last_bar_time = "earlier"
    with pytest.raises(RuntimeError):
        _run_tick(_bot_config(_graph()), state, _tick_df(100.0), provider,
                  extra_patches=[patch("bot_runner.compile_bot_graph",
                                       side_effect=RuntimeError("boom"))])
    assert state.last_bar_time == "earlier"
    assert state.status != "error"


def test_start_bot_refuses_a_graph_that_no_longer_compiles():
    cfg = _bot_config(_no_longer_compiling_graph())
    mgr = _manager_with(cfg)
    from nodebuilder.models import GraphValidationError

    async def go():
        with patch("notifications.notify_error", new_callable=AsyncMock) as notify:
            with pytest.raises(GraphValidationError) as info:
                mgr.start_bot(cfg.bot_id)
            await asyncio.sleep(0)
            return info.value, notify

    exc, notify = asyncio.run(go())
    assert exc.node_id == "/stoch"
    assert cfg.bot_id not in mgr.tasks
    _, state = mgr.bots[cfg.bot_id]
    assert state.status == "error" and "Graph does not compile" in state.pause_reason
    notify.assert_called_once()


def test_start_route_answers_400_with_node_id(client_real_mgr):
    client, mgr = client_real_mgr
    cfg = _bot_config(_no_longer_compiling_graph())
    mgr.bots[cfg.bot_id] = (cfg, BotState())
    with patch("notifications.notify_error", new_callable=AsyncMock):
        r = client.post(f"/api/bots/{cfg.bot_id}/start")
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "/stoch"


def test_add_bot_bad_setting_value_is_400_with_node_id(client_real_mgr):
    """BC-4/LT-4: slippage -1 passed add_bot, then failed on every tick."""
    client, _ = client_real_mgr
    r = client.post("/api/bots", json=_add_body(_graph({"slippage": {"bps": -1}})))
    assert r.status_code == 400, r.text
    assert r.json()["node_id"] == "/slippage"


def test_add_graph_bot_with_regime_is_400(client_real_mgr):
    """BC-12/LT-7: live would gate on the regime; the graph backtest has none."""
    client, _ = client_real_mgr
    body = _add_body(_graph())
    body["regime"] = {"enabled": True}
    r = client.post("/api/bots", json=body)
    assert r.status_code == 400, r.text
    assert "regime" in r.json()["detail"]


def test_check_graph_maps_an_overlay_failure_to_a_graph_error():
    from pydantic import ValidationError
    from nodebuilder.models import GraphValidationError

    cfg = _bot_config(_graph())

    def boom(*_a, **_k):
        BotConfig.model_validate({"allocated_capital": -1})  # raises ValidationError

    with patch("bot_manager.apply_to_bot_config", side_effect=boom):
        with pytest.raises(GraphValidationError):
            BotManager._check_graph(cfg)
