"""Rule-builder coverage sweep (F435 item 0.H).

For every RuleIndicator x RuleCondition (and negated), a rule strategy opened
as a graph must either:
  - backtest to the same trades and final value as the rule backtest, or
  - be refused by compile() with UnsupportedNodeError, which the route
    turns into a 400 naming the node.
It must never run and silently give a different result.  There are no
expected failures: a new divergence fails this test.

Data is a deterministic synthetic OHLCV frame (seeded random walk), shaped
like the parity fixtures (tz-aware daily index, same columns).  No network.
Mirrors how test_backtest_parity.py builds its requests and runs both paths.
"""
from __future__ import annotations

import os
import sys
from typing import get_args

import numpy as np
import pandas as pd
import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from models import StrategyRequest
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.evaluator import UnsupportedNodeError
from nodebuilder.nodes import NODE_CATALOG
from nodebuilder.from_rules import auto_render
from nodebuilder.run import run_graph_backtest
from routes.backtest import run_backtest
from signal_engine import Rule, RuleCondition, RuleIndicator

_REL = 1e-6


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

def _synthetic_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Seeded random walk with swings, so every condition fires sometimes."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    # A slow sine plus noise gives repeated up and down legs.
    drift = 8.0 * np.sin(t / 18.0) + 3.0 * np.sin(t / 5.0)
    # Priced near 250, not 100, so ATR and ATR% are different numbers.
    close = 250.0 + drift + np.cumsum(rng.normal(0.0, 0.8, n))
    open_ = close + rng.normal(0.0, 0.4, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.0, 0.6, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.0, 0.6, n))
    volume = (1_000_000 + 400_000 * np.sin(t / 7.0) + rng.normal(0, 50_000, n)).astype("int64")
    idx = pd.date_range("2022-01-03", periods=n, freq="B", tz="America/New_York", name="Date")
    return pd.DataFrame(
        {
            "Open": open_, "High": high, "Low": low, "Close": close,
            "Volume": volume, "Dividends": 0.0, "Stock Splits": 0.0,
        },
        index=idx,
    )


_DF = _synthetic_df()
_MID_CLOSE = float(_DF["Close"].median())


# ---------------------------------------------------------------------------
# Rule construction
# ---------------------------------------------------------------------------

# A threshold near the middle of each indicator's range, so the scalar
# comparisons fire on this data.
_THRESHOLD = {
    "rsi": 50.0,
    "macd": 0.0,
    "stochastic": 50.0,
    "adx": 25.0,
    "atr": 1.5,       # ATR in price units (median about 1.6 on this data)
    "atr_pct": 0.65,  # ATR as % of price (median about 0.68)
    "volume": 1_000_000.0,
}

_SLOPE_CONDITIONS = {
    "rising", "falling", "rising_over", "falling_over",
    "turns_up", "turns_down", "turns_up_below", "turns_down_above",
    "decelerating", "accelerating",
}


# The params and param the rule builder sets when you pick each indicator
# (frontend/src/features/strategy/RuleRow.tsx, indicator onChange).
_UI_DEFAULTS: dict[str, dict] = {
    "ma": {"params": {"period": 20, "type": "ema"}},
    "rsi": {"params": {"period": 14, "type": "wilder"}},
    "bb": {"params": {"period": 20, "std": 2}, "param": "upper"},
    "atr": {"params": {"period": 14}},
    "atr_pct": {"params": {"period": 14}},
    "volume": {"params": {"period": 20}, "param": "raw"},
    "stochastic": {"params": {"k_period": 14, "d_period": 3, "smooth_k": 3}},
    "adx": {"params": {"period": 14}, "param": "adx"},
}


def _make_rule(indicator: str, condition: str, negated: bool) -> Rule:
    """Build one rule the rule builder could produce for this pair."""
    kw: dict = {"indicator": indicator, "condition": condition, "negated": negated}
    kw.update(_UI_DEFAULTS.get(indicator, {}))
    if condition in ("is_above_signal", "is_below_signal"):
        pass
    elif condition in _SLOPE_CONDITIONS:
        if condition in ("rising_over", "falling_over"):
            kw["value"] = 3
        elif condition in ("turns_up_below", "turns_down_above"):
            kw["value"] = _THRESHOLD.get(indicator, _MID_CLOSE)
    else:
        kw["value"] = _THRESHOLD.get(indicator, _MID_CLOSE)
    return Rule(**kw)


def _cases():
    out = []
    for direction in ("long", "short"):
        for ind in get_args(RuleIndicator):
            for cond in get_args(RuleCondition):
                for negated in (False, True):
                    out.append((ind, cond, negated, "buy", direction))
                    # The same rule on the exit side: an unsupported exit must
                    # not quietly become "never exit".
                    out.append((ind, cond, negated, "sell", direction))
    return out


_CASES = _cases()
_IDS = [f"{i}-{c}-{'not' if n else 'plain'}-{side}-{d}" for i, c, n, side, d in _CASES]

# Rules that never fire on the synthetic data, so the rule side trades
# nothing on that side and "graph matches rule" proves little.  They still
# run and compare (a refusal or a crash would fail), but they are exempt from
# the "the tested rule fired" check below.  Keyed by indicator-condition-
# (plain|not)-side; the same set holds for long and short.
#   *-is_below_signal / NOT *-is_above_signal: these series have no signal
#     line, so is_below_signal is never true.
#   ema50/ema200 crossover_up / crosses_above: the 200-bar warmup leaves too
#     few bars on 400 synthetic days for a cross.
_NEVER_FIRES_BUY = {
    "rsi-is_below_signal-plain", "ma-is_below_signal-plain", "bb-is_below_signal-plain",
    "volume-is_below_signal-plain", "price-is_below_signal-plain",
    "ema20-is_below_signal-plain", "ema50-is_below_signal-plain",
    "ema200-is_below_signal-plain", "ma8-is_below_signal-plain", "ma21-is_below_signal-plain",
    "ma-is_above_signal-not", "volume-is_above_signal-not", "price-is_above_signal-not",
    "ema20-is_above_signal-not", "ema50-is_above_signal-not", "ema200-is_above_signal-not",
    "ema50-crossover_up-plain", "ema50-crosses_above-plain",
    "ema200-crossover_up-plain", "ema200-crosses_above-plain",
}
_NEVER_FIRES_SELL = {
    "rsi-is_below_signal-plain", "ma-is_below_signal-plain", "bb-is_below_signal-plain",
    "volume-is_below_signal-plain", "price-is_below_signal-plain",
    "ema20-is_below_signal-plain", "ema50-is_below_signal-plain",
    "ema200-is_below_signal-plain", "ma8-is_below_signal-plain", "ma21-is_below_signal-plain",
    "rsi-is_above_signal-not", "ma-is_above_signal-not", "bb-is_above_signal-not",
    "volume-is_above_signal-not", "price-is_above_signal-not", "ema20-is_above_signal-not",
    "ema50-is_above_signal-not", "ema200-is_above_signal-not",
    "ma8-is_above_signal-not", "ma21-is_above_signal-not",
    "ema50-crossover_up-plain", "ema50-crosses_above-plain",
    "ema200-crossover_up-plain", "ema200-crosses_above-plain",
}

# A plain, supported partner rule for the other side.
_PARTNER_BUY = Rule(indicator="rsi", condition="below", value=40)
_PARTNER_SELL = Rule(indicator="rsi", condition="above", value=60)


def _request(rule: Rule, side: str, direction: str = "long") -> StrategyRequest:
    buy, sell = ([rule], [_PARTNER_SELL]) if side == "buy" else ([_PARTNER_BUY], [rule])
    return StrategyRequest(
        ticker="SYN", start="2022-01-01", end="2024-01-01", interval="1d",
        source="yahoo", buy_rules=buy, sell_rules=sell, direction=direction,
    )


def _graph_request(req: StrategyRequest) -> GraphBacktestRequest:
    return GraphBacktestRequest(
        graph=auto_render(req),
        ticker=req.ticker, start=req.start, end=req.end,
        interval=req.interval, source=req.source,
        initial_capital=req.initial_capital,
        position_size=req.position_size,
        stop_loss_pct=req.stop_loss_pct,
        trailing_stop=req.trailing_stop,
        max_bars_held=req.max_bars_held,
        slippage_bps=req.slippage_bps,
        commission_pct=req.commission_pct,
        per_share_rate=req.per_share_rate,
        min_per_order=req.min_per_order,
        borrow_rate_annual=req.borrow_rate_annual,
        direction=req.direction,
    )


# ---------------------------------------------------------------------------
# Which graphs compile must refuse
# ---------------------------------------------------------------------------

_COMPILE_ACTIVE = {e.name for e in NODE_CATALOG if e.compile_active}


def _refusal_reason(graph) -> str | None:
    """Why compile must refuse this auto-rendered graph, or None if it must run.

    auto_render draws every rule, but a rule the graph cannot run the same
    way shows up as a node type that is not compile-active (slope
    conditions, stochastic, adx) or as a comparison carrying a
    condition_extra marker (ATR%, a bb band or reference it does not draw).
    """
    for node in graph.nodes.values():
        if node.type not in _COMPILE_ACTIVE:
            return f"node type {node.type!r}"
        extra = (node.params or {}).get("condition_extra")
        if extra is not None:
            return f"condition_extra {extra!r}"
    return None


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("indicator,condition,negated,side,direction", _CASES, ids=_IDS)
def test_graph_matches_rule_or_refuses(indicator, condition, negated, side, direction):
    rule = _make_rule(indicator, condition, negated)
    req = _request(rule, side, direction)

    # The rule backtest accepts every pair today.  An exception here is a
    # regression in the rule engine and fails the test (never a skip).
    rule_result = run_backtest(req, include_spy_correlation=False, df=_DF.copy())

    graph_req = _graph_request(req)

    reason = _refusal_reason(graph_req.graph)
    if reason is not None:
        # Refused out loud with the node named; the route turns this into a
        # 400 with node_id.  Never a silent run with part of the rule missing.
        with pytest.raises(UnsupportedNodeError) as info:
            run_graph_backtest(graph_req, df=_DF.copy())
        assert info.value.node_id in graph_req.graph.nodes, reason
        return

    # Everything else must run and match the rule backtest exactly.
    graph_result = run_graph_backtest(graph_req, df=_DF.copy())
    rt, gt = rule_result["trades"], graph_result.trades
    assert [(t["type"], t["date"]) for t in gt] == [(t["type"], t["date"]) for t in rt], (
        "graph backtest trades differ from the rule backtest "
        f"(rule {len(rt)} trades, graph {len(gt)})"
    )
    assert graph_result.summary["final_value"] == pytest.approx(
        rule_result["summary"]["final_value"], rel=_REL
    )

    # The match must not be vacuous: the tested rule has to fire, or two empty
    # trade lists would pass.  Exits are "sell" (long) or "cover" (short).
    key = f"{indicator}-{condition}-{'not' if negated else 'plain'}"
    if side == "buy" and key not in _NEVER_FIRES_BUY:
        assert rt, f"{key} never entered on the synthetic data"
    if side == "sell" and key not in _NEVER_FIRES_SELL:
        assert any(t["type"] in ("sell", "cover") for t in rt), (
            f"{key} never exited on the synthetic data"
        )


# ---------------------------------------------------------------------------
# Variants the default-param sweep does not reach: missing params, other
# bands, references to a second series.  Each one says whether the graph
# must run (and match) or be refused, so a wrong refusal also fails.
# ---------------------------------------------------------------------------

_M = _MID_CLOSE
_VARIANTS: list[tuple[str, str, Rule]] = [
    # ma with no type: the rule engine defaults to ema.
    ("ma-no-type", "run", Rule(indicator="ma", condition="above", value=_M, params={"period": 20})),
    # rsi with no type: the rule engine defaults to sma.
    ("rsi-no-type", "run", Rule(indicator="rsi", condition="below", value=45, params={"period": 14})),
    # bb width is "std" in the rule builder.
    ("bb-std-3", "run", Rule(indicator="bb", condition="above", value=_M,
                             params={"period": 20, "std": 3}, param="upper")),
    ("bb-lower", "run", Rule(indicator="bb", condition="crosses_below", value=_M,
                             params={"period": 20, "std": 2}, param="lower")),
    ("bb-middle", "run", Rule(indicator="bb", condition="below", value=_M,
                              params={"period": 20, "std": 2.5}, param="middle")),
    ("bb-no-band", "run", Rule(indicator="bb", condition="above", value=_M, params={"period": 20})),
    # MACD params are ignored by the rule engine (always 12/26/9).
    ("macd-custom-params", "run", Rule(indicator="macd", condition="crosses_above", param="signal",
                                       params={"fast": 5, "slow": 35, "signal": 5})),
    ("rsi-above-signal-param", "run", Rule(indicator="rsi", condition="above", param="signal")),
    ("ma-crosses-close", "run", Rule(indicator="ma", condition="crosses_above", param="close",
                                     params={"period": 20, "type": "sma"})),
    ("price-crosses-ma", "run", Rule(indicator="price", condition="crosses_above", param="ma:20:sma")),
    ("ma-above-ma", "run", Rule(indicator="ma", condition="above", param="ma:50:ema",
                                params={"period": 10, "type": "sma"})),
    ("legacy-ema20-over-ema50", "run", Rule(indicator="ema20", condition="crosses_above", param="ema50")),
    ("volume-raw", "run", Rule(indicator="volume", condition="above", value=1_000_000, param="raw")),
    # Things the graph cannot draw the same way: refused, never guessed.
    ("bb-bandwidth", "refuse", Rule(indicator="bb", condition="above", value=0.05,
                                    params={"period": 20, "std": 2}, param="bandwidth")),
    ("bb-pctb", "refuse", Rule(indicator="bb", condition="below", value=0.2,
                               params={"period": 20, "std": 2}, param="pctb")),
    ("volume-sma", "refuse", Rule(indicator="volume", condition="above", value=1_000_000,
                                  params={"period": 20}, param="sma")),
    ("atr-vs-atr-ref", "refuse", Rule(indicator="atr", condition="above", param="atr:20",
                                      params={"period": 14})),
    ("price-vs-bb-ref", "refuse", Rule(indicator="price", condition="above", param="bb:20:2:upper")),
    ("price-vs-close", "refuse", Rule(indicator="price", condition="above", param="close")),
    ("rsi-no-value", "refuse", Rule(indicator="rsi", condition="above", params={"period": 14, "type": "wilder"})),
    ("atr-pct-custom-period", "refuse", Rule(indicator="atr_pct", condition="above", value=0.7,
                                             params={"period": 10})),
]


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("name,expect,rule", _VARIANTS, ids=[v[0] for v in _VARIANTS])
def test_rule_variants_match_or_refuse(name, expect, rule, side):
    req = _request(rule, side)
    rule_result = run_backtest(req, include_spy_correlation=False, df=_DF.copy())
    graph_req = _graph_request(req)

    reason = _refusal_reason(graph_req.graph)
    assert (reason is not None) == (expect == "refuse"), f"{name}: refusal reason {reason!r}"

    if reason is not None:
        with pytest.raises(UnsupportedNodeError):
            run_graph_backtest(graph_req, df=_DF.copy())
        return

    graph_result = run_graph_backtest(graph_req, df=_DF.copy())
    rt, gt = rule_result["trades"], graph_result.trades
    assert rt, f"{name}: the rule never trades on this data, so the match proves nothing"
    assert [(t["type"], t["date"]) for t in gt] == [(t["type"], t["date"]) for t in rt]
    assert graph_result.summary["final_value"] == pytest.approx(
        rule_result["summary"]["final_value"], rel=_REL
    )


def test_muted_rule_is_left_out_like_the_rule_engine():
    """A muted rule is skipped by the rule engine.  auto_render used to draw it
    as an active comparison, so the graph traded differently.  It is now
    drawn bypassed: the viewer shows it, and compile leaves it out."""
    blocker = Rule(indicator="ma", condition="above", value=_MID_CLOSE * 10,
                   params={"period": 20, "type": "ema"})
    muted = blocker.model_copy(update={"muted": True})
    buy = [Rule(indicator="rsi", condition="below", value=45, params={"period": 14, "type": "wilder"})]

    def run_both(extra):
        req = StrategyRequest(
            ticker="SYN", start="2022-01-01", end="2024-01-01", interval="1d",
            source="yahoo", buy_rules=buy + [extra], sell_rules=[_PARTNER_SELL],
        )
        rule_trades = run_backtest(req, include_spy_correlation=False, df=_DF.copy())["trades"]
        graph_req = _graph_request(req)
        graph_trades = run_graph_backtest(graph_req, df=_DF.copy()).trades
        return graph_req.graph, rule_trades, graph_trades

    graph, rt, gt = run_both(muted)
    assert rt, "the unmuted rule must enter, or this test proves nothing"
    assert [(t["type"], t["date"]) for t in gt] == [(t["type"], t["date"]) for t in rt]
    bypassed = [n for n in graph.nodes.values() if n.bypass]
    assert len(bypassed) == 1 and bypassed[0].type == "above"

    # Unmuted, the blocker stops every entry: the mute really changes the result.
    _graph, rt_active, gt_active = run_both(blocker)
    assert rt_active == [] and gt_active == []
