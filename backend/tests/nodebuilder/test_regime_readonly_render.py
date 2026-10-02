"""The read-only auto-render of a regime rule strategy (F435 W5 item 5.C,
plan D8, critic 33, S31 "read-only graph").

A regime strategy renders as one Output Group ``main`` (regime_switch) that
holds the strategy and a ``regime_net`` network.  The network's children
point at it through ``parent``, the graph is readOnly, every wire joins
siblings, and the graph compiles as is.
"""
from __future__ import annotations

import json

import pytest

from models import RegimeConfig, Rule, StrategyRequest
from nodebuilder.compile import compile_with_diagnostics
from nodebuilder.from_rules import auto_render
from nodebuilder.migrate import find_by_path
from nodebuilder.models import Graph


def _req(**regime) -> StrategyRequest:
    base = dict(enabled=True, indicator="ma", indicator_params={"period": 50, "type": "sma"},
                condition="above", min_bars=3, on_flip="close_only")
    base.update(regime)
    return StrategyRequest(
        ticker="AAPL", start="2023-01-01", end="2024-01-01", interval="1d", source="yahoo",
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=35)],
        long_sell_rules=[Rule(indicator="rsi", condition="above", value=65)],
        short_buy_rules=[Rule(indicator="rsi", condition="above", value=65)],
        regime=RegimeConfig(**base),
    )


def test_regime_children_sit_in_the_regime_network_and_the_graph_is_read_only():
    g = auto_render(_req())
    assert g.readOnly is True

    net = g.nodes["/regime"]
    assert (net.type, net.name, net.parent) == ("regime_net", "regime", "/main")
    children = [n for nid, n in g.nodes.items() if nid.startswith("/regime/")]
    assert children, "the regime network has children"
    assert all(n.parent == "/regime" for n in children)
    types = {n.type for n in children}
    assert {"ticker", "sma", "above", "and", "rolling", "shift", "subnet_output"} <= types

    group = g.nodes["/main"]
    assert (group.type, group.name, group.parent) == ("output_group", "main", None)
    assert group.params["direction"] == "regime_switch"
    primary = find_by_path(g, group.params["ticker"], "/main")
    assert primary == "/ticker_aapl_1d_yahoo" and g.nodes[primary].parent == "/main"
    assert not g.nodes[primary].params.get("prefix")

    # Settings nodes stay at the root; everything else (terminals included,
    # DI-03) is in the group.
    from nodebuilder.trading.sim_bridge import SETTING_TYPES

    for nid, node in g.nodes.items():
        if node.type in SETTING_TYPES or nid == "/main":
            assert node.parent is None, nid
        elif not nid.startswith("/regime/"):
            assert node.parent == "/main", nid


def test_terminals_one_entry_and_exit_per_side_and_the_regime_terminal():
    g = auto_render(_req())
    sides = {(n.type, n.params.get("side")) for n in g.nodes.values() if n.type in ("entry", "exit")}
    assert sides == {("entry", "long"), ("exit", "long"), ("entry", "short"), ("exit", "short")}
    term = g.nodes["/regime_terminal"]
    assert (term.type, term.parent, term.params["on_flip"]) == ("regime", "/main", "close_only")
    # The terminal is wired from the network node, and reads the signal the
    # network's last node writes.
    [w] = [w for w in g.wires if w.to_path == "/regime_terminal"]
    assert w.from_path == "/regime"
    [out_wire] = [w for w in g.wires if w.to_path == "/regime/output"]
    last = g.nodes[out_wire.from_path]
    assert term.params["signal"] == last.params["out"]
    # No short sell rules: the short Exit gets a "never" signal.
    [x] = [w for w in g.wires if w.to_path == "/exit_short"]
    assert x.from_path.startswith("/never_")


def test_every_wire_joins_siblings():
    g = auto_render(_req())
    for w in g.wires:
        assert g.nodes[w.from_path].parent == g.nodes[w.to_path].parent, (w.from_path, w.to_path)


def test_the_regime_ticker_is_a_prefixed_reference_on_the_regime_timeframe():
    g = auto_render(_req(timeframe="1wk"))
    [ticker] = [n for nid, n in g.nodes.items() if nid.startswith("/regime/") and n.type == "ticker"]
    assert ticker.params == {"symbol": "AAPL", "interval": "1wk", "prefix": "regime"}
    sma = next(n for nid, n in g.nodes.items() if nid.startswith("/regime/") and n.type == "sma")
    assert sma.params["source"] == "@regime_close"


@pytest.mark.parametrize("timeframe, min_bars, lag, held", [
    ("1d", 3, True, True),     # same timeframe: the rule regime's one-bar lag is drawn
    ("1wk", 3, False, True),   # coarser: the alignment itself shifts one bar
    ("1wk", 1, False, False),  # min_bars 1: no rolling minimum
    ("1h", 1, True, False),    # finer: the lag is drawn
])
def test_lag_and_held_nodes(timeframe, min_bars, lag, held):
    g = auto_render(_req(timeframe=timeframe, min_bars=min_bars))
    assert ("/regime/lag" in g.nodes) is lag
    assert ("/regime/held" in g.nodes) is held
    if held:
        assert g.nodes["/regime/held"].params["window"] == min_bars


def test_the_render_compiles_and_survives_a_save():
    g = auto_render(_req())
    program, diags = compile_with_diagnostics(g)
    assert [d.code for d in diags if d.severity == "error"] == []
    [group] = program.groups
    assert (group.name, group.direction, group.on_flip) == ("main", "regime_switch", "close_only")
    assert [r.key for r in group.references] == [("AAPL", "1d")]

    again = Graph.model_validate(json.loads(json.dumps(g.model_dump(by_alias=True))))
    assert again.readOnly is True
    assert {nid: n.parent for nid, n in again.nodes.items()} == \
        {nid: n.parent for nid, n in g.nodes.items()}


def test_a_strategy_without_a_regime_is_still_one_implicit_group():
    req = _req().model_copy(update={"regime": None})
    g = auto_render(req)
    assert all(n.parent is None for n in g.nodes.values())
    assert not any(n.type in ("output_group", "regime_net", "regime") for n in g.nodes.values())
