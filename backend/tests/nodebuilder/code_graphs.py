"""Graphs, frames and bot configs shared by the W7 code tests (F435 item 7.C).

Not a test module (pytest collects test_*.py only).  Everything here is
plain data: no bot is started, no order is placed, nothing is written.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

# John's vision example (design note 3.3): a Wrangle that measures ATR as a
# percent of the close and flags a high-volatility regime.  Its spare param
# "threshold" is what the RSI's period expression reads.
VOL_CODE = (
    'period = chi("atr_period", default=14, min=2, max=50)\n'
    'th = chf("threshold", default=2.0)\n'
    '@atr_pct: float = sl.atr(@high, @low, @close, period) / @close * 100\n'
    '@vol_regime: bool = @atr_pct > th\n'
)

# The RSI period expression of the vision example.
VISION_PERIOD = '7 if chf("../vol/threshold") > 2 else 21'

# The adaptive case of the plan acceptance, as one Wrangle line (verbatim),
# plus two annotated signal lines for the Entry and the Exit.  (An Entry or
# a comparison reading the unannotated @rsi_adaptive itself needs the
# kernel to accept dtype "any" on a read: see the 7.C report.)
ADAPTIVE_LINE = ('@rsi_adaptive = np.where(@atr_pct > chf("th", default=2.0), '
                 'sl.rsi(@close, 7), sl.rsi(@close, 21))')
ADAPTIVE_CODE = (
    ADAPTIVE_LINE + '\n'
    '@adaptive_low: bool = @rsi_adaptive < 40\n'
    '@adaptive_high: bool = @rsi_adaptive > 60\n'
)


def node(nid: str, typ: str, params: Optional[dict] = None, parent: Optional[str] = None,
         name: Optional[str] = None, **extra: Any) -> dict:
    return {"id": nid, "type": typ, "name": name or nid, "parent": parent,
            "params": params or {}, **extra}


def wire(wid: str, a: str, b: str, port: str = "in0") -> dict:
    return {"id": wid, "from": a, "to": b, "to_port": port}


def graph_data(nodes: list, wires: list) -> dict:
    return {"_version": 3, "nodes": {n["id"]: n for n in nodes}, "wires": list(wires)}


def vision_data(threshold: Optional[float] = None, lookback_bars: Optional[int] = None) -> dict:
    """Ticker -> vol (Wrangle: @atr_pct, @vol_regime); Ticker -> RSI whose
    period is the vision expression; RSI below 45 AND @vol_regime -> Entry;
    RSI above 60 -> Exit."""
    vol_params: dict = {}
    if threshold is not None:
        vol_params["threshold"] = threshold
    if lookback_bars is not None:
        vol_params["lookback_bars"] = lookback_bars
    nodes = [
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        node("vol", "wrangle", vol_params, code=VOL_CODE),
        node("rsi", "rsi", {"period": {"expr": VISION_PERIOD}}),
        node("lo", "below", {"a": "@rsi", "threshold": 45, "out": "@rsi_low"}),
        node("hi", "above", {"a": "@rsi", "threshold": 60, "out": "@rsi_high"}),
        node("both", "and", {"terms": ["@rsi_low", "@vol_regime"], "out": "@long"}),
        node("entry", "entry", {"signal": "@long"}),
        node("exit", "exit", {}),
    ]
    wires = [
        wire("w1", "t", "vol"), wire("w2", "t", "rsi"), wire("w3", "rsi", "lo"),
        wire("w4", "rsi", "hi"), wire("w5", "lo", "both"), wire("w6", "vol", "both", "in1"),
        wire("w7", "both", "entry"), wire("w8", "hi", "exit"),
    ]
    return graph_data(nodes, wires)


def adaptive_data(lookback_bars: Optional[int] = None, *, exit_node: bool = False) -> dict:
    """The vision Wrangle feeding the adaptive Wrangle; Entry on
    @adaptive_low, Exit on @adaptive_high.  exit_node: the Exit reads an
    ``above`` node over the unannotated @rsi_adaptive instead (needs the
    kernel to accept a read of dtype "any")."""
    params = {"lookback_bars": lookback_bars} if lookback_bars is not None else {}
    nodes = [
        node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        node("vol", "wrangle", dict(params), code=VOL_CODE),
        node("adapt", "wrangle", dict(params), code=ADAPTIVE_CODE),
        node("entry", "entry", {"signal": "@adaptive_low"}),
    ]
    wires = [wire("w1", "t", "vol"), wire("w2", "vol", "adapt"), wire("w4", "adapt", "entry")]
    if exit_node:
        nodes += [node("hi", "above", {"a": "@rsi_adaptive", "threshold": 60, "out": "@hi"}),
                  node("exit", "exit", {"signal": "@hi"})]
        wires += [wire("w3", "adapt", "hi"), wire("w5", "hi", "exit")]
    else:
        nodes.append(node("exit", "exit", {"signal": "@adaptive_high"}))
        wires.append(wire("w5", "adapt", "exit"))
    return graph_data(nodes, wires)


def wrangle_bot_data(code: str, *, stop: Optional[float] = 2.0, max_bars: Optional[int] = None,
                     symbol: str = "AAPL", entry_signal: str = "@sig") -> dict:
    """The implicit group: Ticker -> Wrangle *code* -> Entry (reads
    *entry_signal*), with a constant Stop (percent) and an optional Time
    Stop.  The bot's own direction decides the side."""
    nodes = [
        node("t", "ticker", {"symbol": symbol, "interval": "1d"}),
        node("w", "wrangle", {"lookback_bars": 20}, name="signal_code", code=code),
        node("entry", "entry", {"signal": entry_signal}),
    ]
    wires = [wire("w1", "t", "w"), wire("w2", "w", "entry")]
    if stop is not None:
        nodes.append(node("stop", "stop", {"constant": stop}))
    if max_bars is not None:
        nodes.append(node("tstop", "time_stop", {"max_bars": max_bars}))
    return graph_data(nodes, wires)


def plain_bot_data(symbol: str = "MSFT") -> dict:
    """A graph with no code: RSI below 0 -> Entry (never fires)."""
    nodes = [
        node("t", "ticker", {"symbol": symbol, "interval": "1d"}),
        node("rsi", "rsi", {"period": 14}),
        node("lo", "below", {"a": "@rsi", "threshold": 0, "out": "@lo"}),
        node("entry", "entry", {}),
    ]
    wires = [wire("w1", "t", "rsi"), wire("w2", "rsi", "lo"), wire("w3", "lo", "entry")]
    return graph_data(nodes, wires)


def daily_frame(n: int = 900, seed: int = 9, start: str = "2018-01-02") -> pd.DataFrame:
    """Synthetic daily bars with calm and wild stretches, so ATR as a
    percent of the close spends long runs below and above 2."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100 + 8 * np.sin(t / 30) + np.cumsum(rng.normal(0, 1.2, n))
    close = np.maximum(close, 20.0)
    spread = np.where(np.sin(t / 70) > 0, 3.0, 0.5)
    idx = pd.bdate_range(start, periods=n, tz="America/New_York")
    return pd.DataFrame({
        "Open": close + rng.normal(0, 0.2, n),
        "High": close + np.abs(rng.normal(0, 1.0, n)) * spread,
        "Low": close - np.abs(rng.normal(0, 1.0, n)) * spread,
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    }, index=idx)


def code_bot(graph, *, bot_id: str = "bot-code", symbol: str = "AAPL",
             direction: str = "long", **extra: Any):
    """A stopped graph BotConfig for *graph* (a Graph or graph data)."""
    from bot_manager import BotConfig
    from nodebuilder.models import Graph

    if not isinstance(graph, Graph):
        graph = Graph.model_validate(graph)
    fields: dict = dict(
        strategy_name=bot_id, symbol=symbol, interval="1d", buy_rules=[], sell_rules=[],
        long_buy_rules=None, long_sell_rules=None, short_buy_rules=None, short_sell_rules=None,
        allocated_capital=1000.0, kind="graph", graph=graph, direction=direction,
        bot_id=bot_id,
    )
    fields.update(extra)
    return BotConfig(**fields)
