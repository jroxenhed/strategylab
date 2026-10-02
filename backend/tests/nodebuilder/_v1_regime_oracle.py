"""What a stored Wave 0 regime graph spells out, worked out by the rule engine.

Before W5 the graph refused any /regime/ node, so the regime strategies in
vectors/v1_autorender.json (regime_mode, per_direction_b23) and their w1
goldens recorded an error name (RegimeUnsupportedError).  Since W5 (plan
D8) those stored graphs compile, so the tests check their signals instead
(plan section 10).

A v1 regime graph is drawn as (from_rules before W2):

- entry = AND(regime, OR(long buy rules, short buy rules))
- exit  = AND(regime, OR(long sell rules, short sell rules))
- regime = EMA 20 above SMA 200 on the regime Ticker.  v1 drew an EMA 20 for
  the rule ``ma above ma:200:sma`` with no params, which the rule engine
  itself never fires; the stored graph keeps what was drawn.

Its regime Ticker has no prefix, so it reads the request's frame, as every
Ticker of a graph with no Output Group does (plan D11).  This module
evaluates that expression with the rule engine (signal_engine.eval_rules),
an oracle that shares no code with the graph kernel.
"""
from __future__ import annotations

import pandas as pd

from signal_engine import Rule, compute_indicators, eval_rules

# The regime comparison v1 drew: EMA 20 above SMA 200.
V1_REGIME_RULE = Rule(indicator="ma", condition="above", param="ma:200:sma",
                      params={"period": 20, "type": "ema"})


def _side_rules(req) -> dict[str, list]:
    return {
        "long_buy": list(req.long_buy_rules or []),
        "long_sell": list(req.long_sell_rules or []),
        "short_buy": list(req.short_buy_rules or []),
        "short_sell": list(req.short_sell_rules or []),
    }


def v1_regime_signals(req, df: pd.DataFrame) -> tuple[list[int], list[int]]:
    """(entry bars, exit bars) of the v1 regime graph rendered from *req*,
    over *df*."""
    sides = _side_rules(req)
    all_rules = [V1_REGIME_RULE] + [r for rules in sides.values() for r in rules]
    vol = df["Volume"] if "Volume" in df.columns else None
    ind = compute_indicators(df["Close"], high=df["High"], low=df["Low"], volume=vol,
                             rules=all_rules)

    def fired(rules: list, i: int) -> bool:
        return bool(rules) and bool(eval_rules(rules, "AND", ind, i))

    entries, exits = [], []
    for i in range(len(df)):
        regime = bool(eval_rules([V1_REGIME_RULE], "AND", ind, i))
        if regime and (fired(sides["long_buy"], i) or fired(sides["short_buy"], i)):
            entries.append(i)
        if regime and (fired(sides["long_sell"], i) or fired(sides["short_sell"], i)):
            exits.append(i)
    return entries, exits
