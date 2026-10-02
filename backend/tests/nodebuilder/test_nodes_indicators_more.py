"""More indicator nodes against the rule engine (F435 W2 item 2.C).

stochastic, adx, atr_pct, volume, ma and price must write exactly the
columns signal_engine.compute_indicators builds, and a condition on them
must give what eval_rules gives, plain and negated.  Synthetic data only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nodebuilder.compile import compile as nb_compile, compile_with_diagnostics
from nodebuilder.evaluator import cook_program, cook_signals
from nodebuilder.models import Graph
from signal_engine import Rule, compute_indicators, eval_rules

N = 400


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(21)
    t = np.arange(N)
    close = 100 + 8 * np.sin(t / 20) + np.cumsum(rng.normal(0, 0.8, N))
    high = close + np.abs(rng.normal(0, 0.7, N))
    low = close - np.abs(rng.normal(0, 0.7, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    # Volume as int64, as the providers give it.
    volume = rng.integers(100_000, 1_000_000, N)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": volume}, index=idx)


def _graph(nodes: dict, wires: list) -> Graph:
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p} for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _indicator_graph(node_type: str, params: dict, cond: str, cond_params: dict,
                     negated: bool = False) -> Graph:
    """Ticker -> indicator -> condition [-> NOT] -> Entry."""
    nodes = {"/t": ("ticker", {}), "/i": (node_type, params), "/c": (cond, cond_params),
             "/e": ("entry", {})}
    wires = [("/t", "/i", "in0"), ("/i", "/c", "in0")]
    last = "/c"
    if negated:
        nodes["/not"] = ("not", {})
        wires.append(("/c", "/not", "in0"))
        last = "/not"
    wires.append((last, "/e", "in0"))
    return _graph(nodes, wires)


def _columns(df, node_type: str, params: dict, names: tuple[str, ...]) -> dict:
    g = _indicator_graph(node_type, params, "above", {"a": names[0], "threshold": 0})
    result = cook_program(nb_compile(g), df, keep={"/i"})
    return {name: result.column("/i", name) for name in names}


def _rule_ind(df, rule: Rule) -> dict:
    return compute_indicators(df["Close"], df["High"], df["Low"], df["Volume"], rules=[rule])


def _rule_signal(df, rule: Rule) -> np.ndarray:
    ind = _rule_ind(df, rule)
    return np.array([eval_rules([rule], "AND", ind, i) for i in range(len(df))], dtype=bool)


def _node_signal(df, graph: Graph) -> np.ndarray:
    entry, _ = cook_signals(nb_compile(graph), df)
    return np.asarray(entry, dtype=bool)


def _same(got, want) -> None:
    np.testing.assert_array_equal(np.asarray(got, dtype=float), np.asarray(want, dtype=float))


# ---------------------------------------------------------------------------
# Columns equal the rule engine's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("k,d,s", [(14, 3, 3), (5, 2, 1), (21, 5, 4)])
def test_stochastic_columns(df, k, d, s):
    got = _columns(df, "stochastic", {"k_period": k, "d_period": d, "smooth_k": s},
                   ("@stoch_k", "@stoch_d"))
    ind = _rule_ind(df, Rule(indicator="stochastic", condition="above", value=50,
                             params={"k_period": k, "d_period": d, "smooth_k": s}))
    _same(got["@stoch_k"], ind[f"stoch_{k}_{d}_{s}_k"])
    _same(got["@stoch_d"], ind[f"stoch_{k}_{d}_{s}_d"])


@pytest.mark.parametrize("period", [14, 7])
def test_adx_columns(df, period):
    got = _columns(df, "adx", {"period": period}, ("@adx", "@plus_di", "@minus_di"))
    ind = _rule_ind(df, Rule(indicator="adx", condition="above", value=20,
                             params={"period": period}))
    _same(got["@adx"], ind[f"adx_{period}"])
    _same(got["@plus_di"], ind[f"adx_{period}_plus_di"])
    _same(got["@minus_di"], ind[f"adx_{period}_minus_di"])


@pytest.mark.parametrize("period", [14, 10])
def test_atr_pct_column(df, period):
    got = _columns(df, "atr_pct", {"period": period}, ("@atr_pct",))
    ind = _rule_ind(df, Rule(indicator="atr_pct", condition="above", value=1,
                             params={"period": period}))
    _same(got["@atr_pct"], ind[f"atr_pct_{period}"])


def test_volume_columns(df):
    raw = _columns(df, "volume", {"type": "raw"}, ("@vol",))["@vol"]
    sma = _columns(df, "volume", {"type": "sma", "period": 20}, ("@vol",))["@vol"]
    ind = _rule_ind(df, Rule(indicator="volume", condition="rising", param="sma",
                             params={"period": 20}))
    _same(raw, ind["volume_raw"])
    _same(sma, ind["volume_sma_20"])


@pytest.mark.parametrize("ma_type", ["sma", "ema", "rma"])
def test_ma_columns(df, ma_type):
    got = _columns(df, "ma", {"period": 20, "type": ma_type}, ("@ma",))["@ma"]
    ind = _rule_ind(df, Rule(indicator="ma", condition="above", value=100,
                             params={"period": 20, "type": ma_type}))
    _same(got, ind[f"ma_20_{ma_type}"])


def test_ma_shares_the_sma_node_column(df):
    """An ma(sma, 20) and an sma(20) on the same input are one computation."""
    g = _graph({"/t": ("ticker", {}),
                "/a": ("ma", {"period": 20, "type": "sma"}),
                "/b": ("sma", {"period": 20}),
                "/c": ("above", {"a": "@ma", "b": "@sma"}),
                "/e": ("entry", {})},
               [("/t", "/a", "in0"), ("/t", "/b", "in0"), ("/a", "/c", "in0"),
                ("/b", "/c", "in1"), ("/c", "/e", "in0")])
    result = cook_program(nb_compile(g), df, keep={"/c"})
    stream = result.stream("/c")
    assert stream.column_key("@ma") == stream.column_key("@sma")


@pytest.mark.parametrize("field,col", [("@close", "Close"), ("@high", "High"),
                                       ("@low", "Low"), ("@volume", "Volume")])
def test_price_column(df, field, col):
    got = _columns(df, "price", {"field": field}, ("@price",))["@price"]
    _same(got, df[col].to_numpy(dtype=float))


# ---------------------------------------------------------------------------
# Conditions equal eval_rules, plain and negated
# ---------------------------------------------------------------------------

# (label, node type, node params, slot read, rule kwargs)
INDICATORS = [
    ("stoch_k", "stochastic", {"k_period": 14, "d_period": 3, "smooth_k": 3}, "@stoch_k",
     {"indicator": "stochastic", "params": {"k_period": 14, "d_period": 3, "smooth_k": 3}}),
    ("adx", "adx", {"period": 14}, "@adx",
     {"indicator": "adx", "params": {"period": 14}, "param": "adx"}),
    ("plus_di", "adx", {"period": 14}, "@plus_di",
     {"indicator": "adx", "params": {"period": 14}, "param": "plus_di"}),
    ("minus_di", "adx", {"period": 14}, "@minus_di",
     {"indicator": "adx", "params": {"period": 14}, "param": "minus_di"}),
    ("atr_pct", "atr_pct", {"period": 14}, "@atr_pct",
     {"indicator": "atr_pct", "params": {"period": 14}}),
    ("volume_raw", "volume", {"type": "raw"}, "@vol",
     {"indicator": "volume", "param": "raw"}),
    ("volume_sma", "volume", {"type": "sma", "period": 20}, "@vol",
     {"indicator": "volume", "param": "sma", "params": {"period": 20}}),
    ("ma_ema", "ma", {"period": 20, "type": "ema"}, "@ma",
     {"indicator": "ma", "params": {"period": 20, "type": "ema"}}),
    ("ma_sma", "ma", {"period": 10, "type": "sma"}, "@ma",
     {"indicator": "ma", "params": {"period": 10, "type": "sma"}}),
    ("ma_rma", "ma", {"period": 14, "type": "rma"}, "@ma",
     {"indicator": "ma", "params": {"period": 14, "type": "rma"}}),
    ("price", "price", {}, "@price", {"indicator": "price"}),
]


def _middle(df, node_type, params, slot) -> float:
    """A threshold near the middle of the slot's values, so it is crossed."""
    col = _columns(df, node_type, params, (slot,))[slot]
    return float(np.round(np.nanmedian(col), 2))


CONDITIONS = ["above", "below", "crosses_above", "crosses_below", "rising", "falling",
              "rising_over", "turns_up", "turns_down", "turns_up_below",
              "turns_down_above", "accelerating", "decelerating"]


@pytest.mark.parametrize("negated", [False, True], ids=["plain", "not"])
@pytest.mark.parametrize("cond", CONDITIONS)
@pytest.mark.parametrize("ind", INDICATORS, ids=[i[0] for i in INDICATORS])
def test_condition_on_indicator_matches_rule_engine(df, ind, cond, negated):
    _label, node_type, params, slot, rule_kw = ind
    t = _middle(df, node_type, params, slot)
    if cond in ("above", "below", "crosses_above", "crosses_below",
                "turns_up_below", "turns_down_above"):
        rule = Rule(condition=cond, value=t, negated=negated, **rule_kw)
        cond_params = {"a": slot, "threshold": t}
    elif cond == "rising_over":
        rule = Rule(condition=cond, value=4, negated=negated, **rule_kw)
        cond_params = {"a": slot, "bars": 4}
    else:
        rule = Rule(condition=cond, negated=negated, **rule_kw)
        cond_params = {"a": slot}
    want = _rule_signal(df, rule)
    got = _node_signal(df, _indicator_graph(node_type, params, cond, cond_params, negated))
    np.testing.assert_array_equal(got, want)
    if not negated:
        assert want.any() or cond in ("turns_up_below", "turns_down_above")


def test_stochastic_k_crosses_d_matches_rule_engine(df):
    """The rule param "d" (k against d) is the comparison's b."""
    sp = {"k_period": 14, "d_period": 3, "smooth_k": 3}
    rule = Rule(indicator="stochastic", condition="crosses_above", param="d", params=sp)
    g = _indicator_graph("stochastic", sp, "crosses_above", {"a": "@stoch_k", "b": "@stoch_d"})
    got = _node_signal(df, g)
    np.testing.assert_array_equal(got, _rule_signal(df, rule))
    assert got.any()


def test_price_crosses_ma_matches_rule_engine(df):
    rule = Rule(indicator="price", condition="crosses_above", param="ma:20:sma")
    g = _graph({"/t": ("ticker", {}), "/p": ("price", {}),
                "/m": ("ma", {"period": 20, "type": "sma"}),
                "/c": ("crosses_above", {"a": "@price", "b": "@ma"}), "/e": ("entry", {})},
               [("/t", "/p", "in0"), ("/t", "/m", "in0"), ("/p", "/c", "in0"),
                ("/m", "/c", "in1"), ("/c", "/e", "in0")])
    got = _node_signal(df, g)
    np.testing.assert_array_equal(got, _rule_signal(df, rule))
    assert got.any()


# ---------------------------------------------------------------------------
# Indicators of indicators, checks, lookback
# ---------------------------------------------------------------------------


def test_ma_of_rsi(df):
    """The source is the wired node's primary write, so MA of RSI works."""
    g = _graph({"/t": ("ticker", {}), "/r": ("rsi", {"period": 14, "type": "wilder"}),
                "/m": ("ma", {"period": 5, "type": "sma"}),
                "/c": ("above", {"a": "@ma", "threshold": 50}), "/e": ("entry", {})},
               [("/t", "/r", "in0"), ("/r", "/m", "in0"), ("/m", "/c", "in0"),
                ("/c", "/e", "in0")])
    result = cook_program(nb_compile(g), df, keep={"/m"})
    rsi = result.column("/m", "@rsi")
    want = pd.Series(rsi).rolling(5).mean().to_numpy()
    _same(result.column("/m", "@ma"), want)


@pytest.mark.parametrize("node_type,params,code", [
    ("ma", {"type": "wma"}, "param_invalid"),
    ("volume", {"type": "avg"}, "param_invalid"),
    ("stochastic", {"smooth_k": 51}, "param_out_of_range"),
    ("adx", {"period": 1}, "param_out_of_range"),
    ("price", {"field": "@nope"}, "attr_missing"),
])
def test_bad_params_are_refused(node_type, params, code):
    g = _indicator_graph(node_type, params, "above", {"threshold": 0})
    _program, diags = compile_with_diagnostics(g)
    assert code in {d.code for d in diags}, diags


def test_lookbacks():
    from nodebuilder.trading.nodes_indicators import RECURSIVE_FACTOR as F

    def need(node_type, params):
        g = _indicator_graph(node_type, params, "above", {"threshold": 0})
        return nb_compile(g).required_lookback_bars
    assert need("ma", {"period": 20, "type": "sma"}) == 20
    assert need("ma", {"period": 20, "type": "ema"}) == F * 20
    assert need("stochastic", {"k_period": 14, "d_period": 3, "smooth_k": 3}) == 18
    assert need("volume", {"type": "raw"}) == 0
    assert need("volume", {"type": "sma", "period": 30}) == 30
    assert need("adx", {"period": 14}) == F * 2 * 14 + 1


def test_schema_lists_every_slot():
    g = _indicator_graph("adx", {}, "above", {"a": "@plus_di", "b": "@minus_di"})
    program = nb_compile(g)
    names = {a["name"] for a in program.stream_schemas_json()["/i"]["points"]}
    assert {"@adx", "@plus_di", "@minus_di", "@close"} <= names
