"""Unit 3 tests — auto_render(StrategyRequest) -> Graph."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from models import StrategyRequest, RegimeConfig
from signal_engine import Rule
from nodebuilder.from_rules import auto_render
from nodebuilder.api_models import AutoRenderResponse
from nodebuilder.models import Graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rsi_rule(value: float = 30, condition: str = "below", negated: bool = False) -> Rule:
    return Rule(indicator="rsi", condition=condition, value=value, negated=negated)


def _make_req(
    buy_rules=None,
    sell_rules=None,
    ticker="AAPL",
    stop_loss_pct=None,
    **kwargs,
) -> StrategyRequest:
    return StrategyRequest(
        ticker=ticker,
        start="2023-01-01",
        end="2024-01-01",
        interval="1d",
        buy_rules=buy_rules or [],
        sell_rules=sell_rules or [],
        stop_loss_pct=stop_loss_pct,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Simple RSI below 30 long
# ---------------------------------------------------------------------------

def test_simple_rsi_below_30_long():
    req = _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
    )
    g = auto_render(req)

    node_types = {n.type for n in g.nodes.values()}
    # Expected node types: ticker, rsi, above, below, and (logic x2), entry, exit
    # + 4 settings
    assert "ticker" in node_types
    assert "rsi" in node_types
    assert "below" in node_types
    assert "above" in node_types
    assert "entry" in node_types
    assert "exit" in node_types
    assert "position_size" in node_types
    assert "slippage" in node_types
    assert "commission" in node_types

    # Count RSI nodes — must be exactly 1 (deduplication)
    rsi_nodes = [n for n in g.nodes.values() if n.type == "rsi"]
    assert len(rsi_nodes) == 1

    # Specific paths
    assert "/rsi_period_14_type_sma" in g.nodes
    assert "/cmp_buy_0" in g.nodes
    assert "/cmp_sell_0" in g.nodes
    assert "/logic_buy" in g.nodes
    assert "/logic_sell" in g.nodes

    # Wires exist from logic nodes to terminals
    wire_targets = {w.to_path for w in g.wires}
    assert "/entry" in wire_targets
    assert "/exit" in wire_targets


# ---------------------------------------------------------------------------
# 2. Indicator memoization across sides
# ---------------------------------------------------------------------------

def test_indicator_memoization_across_sides():
    req = _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
    )
    g = auto_render(req)

    rsi_nodes = [n for n in g.nodes.values() if n.type == "rsi"]
    assert len(rsi_nodes) == 1, "RSI node must be emitted only once despite appearing in both sides"

    # RSI node should have two outgoing wires (to buy comparison and sell comparison)
    rsi_path = rsi_nodes[0].id
    outgoing = [w for w in g.wires if w.from_path == rsi_path]
    assert len(outgoing) >= 2, f"Expected ≥2 wires from RSI node, got {len(outgoing)}"


# ---------------------------------------------------------------------------
# 3. Negated rule inserts NOT node
# ---------------------------------------------------------------------------

def test_negated_rule_inserts_not_node():
    req = _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30, negated=True)],
        sell_rules=[],
    )
    g = auto_render(req)

    assert "/not_buy_0" in g.nodes, "NOT node must be present for negated rule"
    not_node = g.nodes["/not_buy_0"]
    assert not_node.type == "not"

    # Wire: comparison → NOT
    cmp_to_not = [w for w in g.wires if w.from_path == "/cmp_buy_0" and w.to_path == "/not_buy_0"]
    assert cmp_to_not, "Wire from comparison to NOT node must exist"

    # Wire: NOT → logic
    not_to_logic = [w for w in g.wires if w.from_path == "/not_buy_0" and w.to_path == "/logic_buy"]
    assert not_to_logic, "Wire from NOT node to logic must exist"


# ---------------------------------------------------------------------------
# 4. Empty strategy
# ---------------------------------------------------------------------------

def test_empty_strategy():
    req = _make_req(buy_rules=[], sell_rules=[])
    g = auto_render(req)

    node_types = {n.type for n in g.nodes.values()}
    assert "ticker" in node_types
    assert "entry" in node_types
    assert "exit" in node_types
    assert "position_size" in node_types

    # No comparison or logic nodes
    assert all(n.type not in ("above", "below", "crosses_above", "crosses_below", "and", "or")
               for n in g.nodes.values()), "Empty strategy must have no comparison/logic nodes"

    # No wires to entry/exit (nothing to wire from)
    entry_wires = [w for w in g.wires if w.to_path == "/entry"]
    exit_wires = [w for w in g.wires if w.to_path == "/exit"]
    assert not entry_wires, "No wires should target entry in empty strategy"
    assert not exit_wires, "No wires should target exit in empty strategy"


# ---------------------------------------------------------------------------
# 5. Regime mode emits sub-tree
# ---------------------------------------------------------------------------

def test_regime_mode_emits_subtree():
    regime = RegimeConfig(
        enabled=True,
        timeframe="1d",
        rules=[Rule(indicator="ma", condition="above", value=0,
                    params={"period": 200, "type": "sma"})],
        logic="AND",
    )
    req = _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        regime=regime,
    )
    g = auto_render(req)

    regime_paths = [p for p in g.nodes if p.startswith("/regime/")]
    assert regime_paths, "Regime sub-tree nodes must be emitted"

    # Regime ticker must exist
    assert any("ticker" in g.nodes[p].type for p in regime_paths), "Regime ticker must exist"

    # Regime gate AND nodes must exist
    assert "/and_regime_buy_gate" in g.nodes
    assert "/and_regime_sell_gate" in g.nodes

    # Gates must wire to entry/exit
    buy_gate_wires = [w for w in g.wires if w.from_path == "/and_regime_buy_gate" and w.to_path == "/entry"]
    sell_gate_wires = [w for w in g.wires if w.from_path == "/and_regime_sell_gate" and w.to_path == "/exit"]
    assert buy_gate_wires, "Regime buy gate must wire to /entry"
    assert sell_gate_wires, "Regime sell gate must wire to /exit"


# ---------------------------------------------------------------------------
# 6. Per-direction B23 mode
# ---------------------------------------------------------------------------

def test_per_direction_b23_mode():
    req = _make_req(
        buy_rules=[],
        sell_rules=[],
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        short_buy_rules=[Rule(indicator="rsi", condition="above", value=70)],
    )
    g = auto_render(req)

    # Per-direction logic nodes must exist
    assert "/logic_long_buy" in g.nodes
    assert "/logic_short_buy" in g.nodes

    # OR combiner must exist (two buy-side logic paths)
    assert "/or_b23_buy" in g.nodes, "OR combiner for b23 buy must exist"

    # Entry must be reachable
    entry_reachable = any(w.to_path == "/entry" for w in g.wires)
    assert entry_reachable, "Entry must have at least one incoming wire in b23 mode"


# ---------------------------------------------------------------------------
# 7. stop_loss_pct=None omits setting node
# ---------------------------------------------------------------------------

def test_stop_loss_none_omits_setting_node():
    req = _make_req(buy_rules=[], sell_rules=[], stop_loss_pct=None)
    g = auto_render(req)
    assert "/setting_stop_loss" not in g.nodes, "stop_loss node must be absent when stop_loss_pct is None"


def test_stop_loss_set_emits_setting_node():
    req = _make_req(buy_rules=[], sell_rules=[], stop_loss_pct=5.0)
    g = auto_render(req)
    assert "/setting_stop_loss" in g.nodes
    assert g.nodes["/setting_stop_loss"].params["pct"] == 5.0


# ---------------------------------------------------------------------------
# 8. MACD signal two-wire comparison
# ---------------------------------------------------------------------------

def test_macd_signal_two_wire_comparison():
    req = _make_req(
        buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
        sell_rules=[],
    )
    g = auto_render(req)

    cmp_path = "/cmp_buy_0"
    assert cmp_path in g.nodes, "Comparison node must exist"

    # v3 (W2): the operands are the comparison's a / b params; one wire from
    # the MACD node carries both, and no wire holds an attr.
    cmp = g.nodes[cmp_path]
    assert cmp.params["a"] == "@macd_line", "a must read the MACD line"
    assert cmp.params["b"] == "@macd_signal", "b must read the MACD signal line"
    incoming = [w for w in g.wires if w.to_path == cmp_path]
    macd_ids = {n.id for n in g.nodes.values() if n.type == "macd"}
    assert {w.from_path for w in incoming} == macd_ids and len(macd_ids) == 1
    assert all(w.attr is None for w in incoming)


# ---------------------------------------------------------------------------
# 9. MA with param as other indicator
# ---------------------------------------------------------------------------

def test_ma_with_param_other_indicator():
    """buy=[price > ema200] — two wires into comparison: @close from ticker + @ema from indicator."""
    req = _make_req(
        buy_rules=[Rule(indicator="price", condition="above", param="ma:200:ema")],
        sell_rules=[],
    )
    g = auto_render(req)

    cmp_path = "/cmp_buy_0"
    assert cmp_path in g.nodes

    # EMA node must exist
    ema_nodes = [n for n in g.nodes.values() if n.type == "ema"]
    assert ema_nodes, "EMA indicator node must be emitted for ma:200:ema param"
    ema_node = ema_nodes[0]
    assert ema_node.params.get("period") == 200

    # v3 (W2): price (a Price node reading @close) is a, the EMA is b.
    cmp = g.nodes[cmp_path]
    price_nodes = [n for n in g.nodes.values() if n.type == "price"]
    assert len(price_nodes) == 1
    assert (cmp.params["a"], cmp.params["b"]) == (price_nodes[0].params["out"], ema_node.params["out"])
    assert price_nodes[0].params.get("field", "@close") == "@close"
    # Specifically the EMA wire must exist
    ema_wires = [w for w in g.wires if w.to_path == cmp_path and w.from_path == ema_node.id]
    assert ema_wires, "Wire from EMA node to comparison must exist"


# ---------------------------------------------------------------------------
# 10. Legacy ema20 canonicalizes via migrate_rule
# ---------------------------------------------------------------------------

def test_legacy_ema20_canonicalizes():
    req = _make_req(
        buy_rules=[Rule(indicator="ema20", condition="above", value=0)],  # type: ignore[arg-type]
        sell_rules=[],
    )
    g = auto_render(req)

    # Must NOT have a node named "ema20"
    assert all(n.type != "ema20" for n in g.nodes.values()), "No node should have type 'ema20'"

    # Must have an 'ema' node with period=20
    ema_nodes = [n for n in g.nodes.values() if n.type == "ema"]
    assert ema_nodes, "migrate_rule must produce an 'ema' node for legacy 'ema20'"
    assert ema_nodes[0].params.get("period") == 20


# ---------------------------------------------------------------------------
# 11. readOnly flag always set
# ---------------------------------------------------------------------------

def test_readonly_flag_set():
    for req in [
        _make_req(),
        _make_req(buy_rules=[Rule(indicator="rsi", condition="below", value=30)]),
    ]:
        g = auto_render(req)
        assert g.readOnly is True, "auto_render must always return readOnly=True"


# ---------------------------------------------------------------------------
# 12. All wire endpoints exist in nodes
# ---------------------------------------------------------------------------

def test_all_wires_endpoints_exist():
    req = _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
        stop_loss_pct=5.0,
    )
    g = auto_render(req)

    node_paths = set(g.nodes.keys())
    for w in g.wires:
        assert w.from_path in node_paths, f"Wire from_path {w.from_path!r} not in nodes"
        assert w.to_path in node_paths, f"Wire to_path {w.to_path!r} not in nodes"


# ---------------------------------------------------------------------------
# 13. No cycles on 5 fixture strategies
# ---------------------------------------------------------------------------

def _fixture_strategies():
    # 1. Simple long
    yield _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        stop_loss_pct=5.0,
    )
    # 2. Simple short (direction doesn't affect graph structure)
    yield _make_req(
        buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
        sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")],
        direction="short",
    )
    # 3. MACD crossover (already in #2 above, use BB here instead)
    yield _make_req(
        buy_rules=[Rule(indicator="bb", condition="below", value=0)],
        sell_rules=[Rule(indicator="bb", condition="above", value=0)],
    )
    # 4. Regime-gated
    yield _make_req(
        buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        regime=RegimeConfig(
            enabled=True,
            timeframe="1d",
            rules=[Rule(indicator="ma", condition="above", value=0,
                        params={"period": 200, "type": "sma"})],
        ),
    )
    # 5. B23 per-direction
    yield _make_req(
        buy_rules=[],
        sell_rules=[],
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=30)],
        long_sell_rules=[Rule(indicator="rsi", condition="above", value=70)],
        short_buy_rules=[Rule(indicator="macd", condition="crosses_above", param="signal")],
        short_sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")],
    )


def test_no_cycles_on_5_fixture_strategies():
    for i, req in enumerate(_fixture_strategies()):
        # Graph.__init__ calls _assert_acyclic via model_validator — if no exception, no cycle.
        try:
            g = auto_render(req)
        except Exception as exc:
            pytest.fail(f"Fixture strategy {i} raised {type(exc).__name__}: {exc}")
        assert isinstance(g, Graph)


# ---------------------------------------------------------------------------
# 14. Endpoint test via TestClient
# ---------------------------------------------------------------------------

def test_post_auto_render_endpoint():
    from main import app

    client = TestClient(app)
    payload = {
        "ticker": "AAPL",
        "start": "2023-01-01",
        "end": "2024-01-01",
        "interval": "1d",
        "buy_rules": [{"indicator": "rsi", "condition": "below", "value": 30}],
        "sell_rules": [{"indicator": "rsi", "condition": "above", "value": 70}],
    }
    resp = client.post("/api/nodebuilder/auto_render", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body = resp.json()
    parsed = AutoRenderResponse.model_validate(body)
    assert parsed.graph.readOnly is True
    assert len(parsed.graph.nodes) > 0


# ---------------------------------------------------------------------------
# Schema v2 (W1 item 1.A): names, ports, version
# ---------------------------------------------------------------------------


def _v2_req() -> StrategyRequest:
    return StrategyRequest(
        ticker="AAPL", start="2022-01-01", end="2024-01-01", interval="1d", source="yahoo",
        buy_rules=[
            Rule(indicator="rsi", condition="below", value=30),
            Rule(indicator="rsi", condition="above", value=10, negated=True),
        ],
        sell_rules=[Rule(indicator="macd", condition="crosses_below", param="signal")],
        regime=RegimeConfig(
            enabled=True,
            rules=[Rule(indicator="ma", condition="above", param="ma:200:sma")],
            on_flip="close_only",
        ),
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=35)],
        long_sell_rules=[Rule(indicator="rsi", condition="above", value=65)],
    )


def test_auto_render_emits_current_version():
    from nodebuilder.migrate import CURRENT_GRAPH_VERSION

    g = auto_render(_v2_req())
    assert g.version == CURRENT_GRAPH_VERSION == 3
    assert g.stream_schema == 1
    dumped = g.model_dump(by_alias=True)
    assert dumped["_version"] == CURRENT_GRAPH_VERSION
    assert dumped["meta"] == {} and dumped["annotations"] == {"boxes": [], "notes": []}


def test_auto_render_names_every_node_uniquely():
    from nodebuilder.migrate import is_valid_name, sanitize_name

    g = auto_render(_v2_req())
    names = [n.name for n in g.nodes.values()]
    assert all(is_valid_name(n) for n in names)
    assert len(names) == len(set(names))
    for node_id, node in g.nodes.items():
        assert node.parent is None
        assert node.name == sanitize_name(node_id.lstrip("/"))


def test_auto_render_ports_follow_wire_order():
    g = auto_render(_v2_req())
    count: dict[str, int] = {}
    for w in g.wires:
        k = count.get(w.to_path, 0)
        assert (w.from_port, w.to_port) == ("out", f"in{k}")
        count[w.to_path] = k + 1
    # at least one node takes more than one input, so in1 is exercised
    assert max(count.values()) >= 2


def test_auto_render_route_returns_current_version_fields():
    from main import app

    client = TestClient(app)
    resp = client.post("/api/nodebuilder/auto_render", json=_v2_req().model_dump())
    assert resp.status_code == 200, resp.text
    graph = resp.json()["graph"]
    assert graph["_version"] == 3
    node = next(iter(graph["nodes"].values()))
    assert node["name"] and "parent" in node and "subgraph" not in node
    assert all(w["to_port"].startswith("in") and w["from_port"] == "out" for w in graph["wires"])
