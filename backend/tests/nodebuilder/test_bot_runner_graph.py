"""Unit 9 tests — BotConfig.kind + BotState.graph_hash + _tick() graph branch.

Tests:
  1. test_graph_signal_matches_rule_signal_on_fixture
  2. test_existing_rule_bot_loads_unchanged
  3. test_kind_graph_missing_graph_logs_warning
  4. test_graph_hash_recompile_on_change
  5. test_mid_position_graph_swap_rejected
  6. test_regime_graph_raises_at_compile
  7. test_htf_graph_raises
"""
from __future__ import annotations

import sys
import os

# Ensure backend/ is on sys.path
_BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from bot_manager import BotConfig, BotState
from nodebuilder.models import Graph, Node, Wire
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import (
    compute_indicators_from_specs,
    evaluate_graph,
)
from nodebuilder.from_rules import auto_render
from signal_engine import Rule, compute_indicators, eval_rules


# ---------------------------------------------------------------------------
# Helpers — minimal graph construction
# ---------------------------------------------------------------------------

def _node(path: str, node_type: str, params: dict | None = None) -> Node:
    return Node(id=path, type=node_type, params=params or {})


def _wire(wid: str, from_path: str, to_path: str) -> Wire:
    return Wire.model_validate({"id": wid, "from": from_path, "to": to_path})


def _rsi_below_above_graph(rsi_buy: float = 30, rsi_sell: float = 70) -> Graph:
    """Build a minimal RSI below/above graph: RSI < rsi_buy → Entry, RSI > rsi_sell → Exit."""
    nodes = {
        "/ticker": _node("/ticker", "ticker"),
        "/rsi_period_14_type_sma": _node("/rsi_period_14_type_sma", "rsi", {"period": 14, "type": "sma"}),
        "/cmp_buy": _node("/cmp_buy", "below", {"threshold": rsi_buy}),
        "/cmp_sell": _node("/cmp_sell", "above", {"threshold": rsi_sell}),
        "/logic_buy": _node("/logic_buy", "and"),
        "/logic_sell": _node("/logic_sell", "and"),
        "/entry": _node("/entry", "entry"),
        "/exit": _node("/exit", "exit"),
        "/position_size": _node("/position_size", "position_size", {"size": 1.0}),
        "/slippage": _node("/slippage", "slippage", {"bps": 2.0}),
        "/commission": _node("/commission", "commission", {"per_share_rate": 0.0, "min_per_order": 0.0}),
    }
    wires = [
        _wire("w1", "/ticker", "/rsi_period_14_type_sma"),
        _wire("w2", "/rsi_period_14_type_sma", "/cmp_buy"),
        _wire("w3", "/rsi_period_14_type_sma", "/cmp_sell"),
        _wire("w4", "/cmp_buy", "/logic_buy"),
        _wire("w5", "/cmp_sell", "/logic_sell"),
        _wire("w6", "/logic_buy", "/entry"),
        _wire("w7", "/logic_sell", "/exit"),
    ]
    return Graph.model_validate({"_version": 1, "nodes": {k: v.model_dump() for k, v in nodes.items()}, "wires": [w.model_dump(by_alias=True) for w in wires]})


def _make_ohlcv_df(n: int = 60, seed: int = 42) -> pd.DataFrame:
    """Generate a synthetic daily OHLCV DataFrame with n bars."""
    rng = np.random.default_rng(seed)
    close = pd.Series(rng.standard_normal(n).cumsum() + 100).clip(lower=5)
    high = close + rng.uniform(0.5, 2.0, size=n)
    low = (close - rng.uniform(0.5, 2.0, size=n)).clip(lower=1)
    open_ = close + rng.uniform(-1.0, 1.0, size=n)
    volume = pd.Series(np.full(n, 1_000_000.0))
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.DataFrame({"Open": open_.values, "High": high.values, "Low": low.values,
                         "Close": close.values, "Volume": volume.values}, index=idx)


def _minimal_bot_config(kind: str = "rule", graph: Optional[Graph] = None) -> BotConfig:
    """Construct a minimal BotConfig that passes validation."""
    extra = {}
    if graph is not None:
        extra["graph"] = graph
    return BotConfig(
        strategy_name="test",
        symbol="AAPL",
        interval="1d",
        buy_rules=[],
        sell_rules=[],
        long_buy_rules=None,
        long_sell_rules=None,
        short_buy_rules=None,
        short_sell_rules=None,
        allocated_capital=1000.0,
        kind=kind,
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. test_graph_signal_matches_rule_signal_on_fixture
# ---------------------------------------------------------------------------

def test_graph_signal_matches_rule_signal_on_fixture():
    """Graph evaluator and rule engine must agree on buy/sell signals for an RSI strategy."""
    df = _make_ohlcv_df(n=60)
    i = len(df) - 1

    # Rule-mode: RSI below 30 → buy, above 70 → sell
    buy_rule = Rule(indicator="rsi", condition="below", value=30)
    sell_rule = Rule(indicator="rsi", condition="above", value=70)
    vol = df["Volume"]
    indicators = compute_indicators(df["Close"], high=df["High"], low=df["Low"], volume=vol, rules=[buy_rule, sell_rule])
    rule_buy = eval_rules([buy_rule], "AND", indicators, i)
    rule_sell = eval_rules([sell_rule], "AND", indicators, i)

    # Graph-mode: same RSI strategy via auto_render path
    from models import StrategyRequest
    req = StrategyRequest(
        ticker="AAPL", start="2023-01-01", end="2024-01-01", interval="1d",
        buy_rules=[buy_rule], sell_rules=[sell_rule],
    )
    graph = auto_render(req)
    program = nb_compile(graph)

    from indicators import OHLCVSeries
    ohlcv = OHLCVSeries(close=df["Close"], high=df["High"], low=df["Low"], volume=df["Volume"])
    attrs = compute_indicators_from_specs(program.indicator_specs, ohlcv)
    attrs["@close"] = df["Close"]
    attrs["@open"] = df["Open"]
    attrs["@high"] = df["High"]
    attrs["@low"] = df["Low"]
    attrs["@volume"] = df["Volume"]
    for op in program.per_bar_program:
        if op.writes not in attrs:
            attrs[op.writes] = pd.Series(np.nan, index=df.index, dtype="float64")

    sigs = evaluate_graph(program, attrs, i)
    graph_buy = sigs["entry"]
    graph_sell = sigs["exit"]

    assert graph_buy == rule_buy, f"buy_signal mismatch: graph={graph_buy}, rule={rule_buy}"
    assert graph_sell == rule_sell, f"sell_signal mismatch: graph={graph_sell}, rule={rule_sell}"


# ---------------------------------------------------------------------------
# 2. test_existing_rule_bot_loads_unchanged
# ---------------------------------------------------------------------------

def test_existing_rule_bot_loads_unchanged():
    """A bots.json row without kind/graph fields loads cleanly with defaults."""
    cfg_dict = {
        "strategy_name": "legacy_bot",
        "symbol": "AAPL",
        "interval": "15m",
        "buy_rules": [{"indicator": "rsi", "condition": "below", "value": 30,
                       "param": None, "threshold": None, "muted": False, "negated": False}],
        "sell_rules": [{"indicator": "rsi", "condition": "above", "value": 70,
                        "param": None, "threshold": None, "muted": False, "negated": False}],
        "long_buy_rules": None,
        "long_sell_rules": None,
        "short_buy_rules": None,
        "short_sell_rules": None,
        "allocated_capital": 1000.0,
        # No kind, no graph
    }
    cfg = BotConfig(**cfg_dict)
    assert cfg.kind == "rule"
    assert cfg.graph is None


# ---------------------------------------------------------------------------
# 3. test_kind_graph_missing_graph_logs_warning
# ---------------------------------------------------------------------------

def test_kind_graph_missing_graph_logs_warning(caplog):
    """BotConfig(kind='graph', graph=None) must log a warning but not raise."""
    with caplog.at_level(logging.WARNING, logger="bot_manager"):
        cfg = BotConfig(
            strategy_name="test",
            symbol="AAPL",
            interval="1d",
            buy_rules=[],
            sell_rules=[],
            long_buy_rules=None,
            long_sell_rules=None,
            short_buy_rules=None,
            short_sell_rules=None,
            allocated_capital=1000.0,
            kind="graph",
            graph=None,
        )
    assert cfg.kind == "graph"
    assert cfg.graph is None
    assert any("kind=graph" in r.message for r in caplog.records), \
        f"Expected warning about kind=graph with no graph; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# 4. test_graph_hash_recompile_on_change
# ---------------------------------------------------------------------------

def test_graph_hash_recompile_on_change():
    """Changing the graph on a BotConfig should trigger recompile (different hash → different compiled_program)."""
    graph_a = _rsi_below_above_graph(rsi_buy=30, rsi_sell=70)
    graph_b = _rsi_below_above_graph(rsi_buy=25, rsi_sell=75)

    state = BotState()
    assert state.graph_hash is None
    assert state.compiled_program is None

    # Simulate first compile
    dump_a = json.dumps(graph_a.model_dump(mode='json'), sort_keys=True)
    hash_a = hashlib.sha256(dump_a.encode()).hexdigest()
    program_a = nb_compile(graph_a)
    state.compiled_program = program_a
    state.graph_hash = hash_a

    # Now "swap" to graph_b by computing new hash
    dump_b = json.dumps(graph_b.model_dump(mode='json'), sort_keys=True)
    hash_b = hashlib.sha256(dump_b.encode()).hexdigest()

    # Hash should differ
    assert hash_a != hash_b, "Graphs with different params must produce different hashes"

    # Recompile because hash changed
    if hash_b != state.graph_hash or state.compiled_program is None:
        program_b = nb_compile(graph_b)
        state.compiled_program = program_b
        state.graph_hash = hash_b

    assert state.graph_hash == hash_b
    # program identity changed (different object)
    assert state.compiled_program is not program_a


# ---------------------------------------------------------------------------
# 5. test_mid_position_graph_swap_rejected
# ---------------------------------------------------------------------------

def test_mid_position_graph_swap_rejected():
    """PATCH /api/bots/{id} with a new graph while bot is in-position must return 409."""
    # Import here to avoid loading the full app unless needed
    import routes.bots as bots_route
    from main import app

    graph_a = _rsi_below_above_graph(rsi_buy=30, rsi_sell=70)
    graph_b = _rsi_below_above_graph(rsi_buy=25, rsi_sell=75)

    # Set up a mock bot manager with one in-position bot
    cfg = _minimal_bot_config(kind="graph", graph=graph_a)
    state = BotState()
    state.entry_price = 150.0  # simulate open position

    mock_mgr = MagicMock()
    mock_mgr.get_bot.return_value = (cfg, state)

    original_manager = bots_route.bot_manager
    bots_route.bot_manager = mock_mgr
    try:
        client = TestClient(app)
        patch_body = {
            "graph": graph_b.model_dump(by_alias=True),
        }
        resp = client.patch(f"/api/bots/test-bot-id", json=patch_body)
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        detail = resp.json()["detail"]
        assert detail["code"] == "in_position"
        assert "in position" in detail["message"].lower()
    finally:
        bots_route.bot_manager = original_manager


# ---------------------------------------------------------------------------
# 6. A regime strategy compiles for a bot (W5, plan D8)
# ---------------------------------------------------------------------------

def _bot_config(graph, **extra) -> BotConfig:
    return BotConfig(
        strategy_name="graph_test", symbol="AAPL", interval="1d",
        buy_rules=[], sell_rules=[], long_buy_rules=None, long_sell_rules=None,
        short_buy_rules=None, short_sell_rules=None, allocated_capital=1000.0,
        kind="graph", graph=graph, **extra,
    )


def test_regime_graph_compiles_for_a_bot():
    """A regime rule strategy, auto-rendered, compiles for a bot: one
    regime_switch group whose regime Ticker is a reference frame the tick
    fetches next to its own bars (it used to be RegimeUnsupportedError)."""
    from bot_runner import compile_bot_graph, graph_bot_live
    from models import RegimeConfig, StrategyRequest

    req = StrategyRequest(
        ticker="AAPL", start="2023-01-01", end="2024-01-01", interval="1d",
        buy_rules=[], sell_rules=[],
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=35)],
        short_buy_rules=[Rule(indicator="rsi", condition="above", value=65)],
        regime=RegimeConfig(enabled=True, timeframe="1wk", indicator="ma",
                            indicator_params={"period": 10, "type": "sma"}, min_bars=2),
    )
    graph = auto_render(req)
    program = compile_bot_graph(graph, "bot-1")
    live = graph_bot_live(program, _bot_config(graph, graph_direction_mode="regime_switch"))
    assert live.group.direction == "regime_switch"
    assert live.references == (("AAPL", "1wk"),)
    assert live.plan.regime is not None


# ---------------------------------------------------------------------------
# 7. Another timeframe is a reference Ticker, not a refusal (HTF retired)
# ---------------------------------------------------------------------------

def test_other_timeframe_ticker_is_a_reference_for_a_bot():
    """HTFGraphNotSupportedError is retired (plan D8): a prefixed Ticker on
    another symbol or interval compiles, and the bot lists its frame."""
    from bot_runner import compile_bot_graph, graph_bot_live

    graph = _rsi_below_above_graph()
    nodes_dict = {k: v.model_dump() for k, v in graph.nodes.items()}
    nodes_dict["/spy"] = _node("/spy", "ticker",
                               {"symbol": "SPY", "interval": "1wk", "prefix": "spy"}).model_dump()
    nodes_dict["/spy_rsi"] = _node("/spy_rsi", "rsi", {"period": 14, "out": "@spy_rsi"}).model_dump()
    nodes_dict["/spy_up"] = _node("/spy_up", "above", {"threshold": 50.0, "out": "@spy_up"}).model_dump()
    nodes_dict["/both"] = _node("/both", "and", {"terms": ["@spy_up", "@below"]}).model_dump()
    wires = [w.model_dump(by_alias=True) for w in graph.wires if w.to_path != "/entry"]
    wires += [
        {"id": "w_s1", "from": "/spy", "to": "/spy_rsi"},
        {"id": "w_s2", "from": "/spy_rsi", "to": "/spy_up"},
        {"id": "w_s3", "from": "/spy_up", "to": "/both"},
        {"id": "w_s4", "from": next(w.from_path for w in graph.wires if w.to_path == "/entry"),
         "to": "/both"},
        {"id": "w_s5", "from": "/both", "to": "/entry"},
    ]
    ref_graph = Graph.model_validate({"_version": 3, "nodes": nodes_dict, "wires": wires})
    program = compile_bot_graph(ref_graph, "bot-1")
    live = graph_bot_live(program, _bot_config(ref_graph))
    assert live.references == (("SPY", "1wk"),)
