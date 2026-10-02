"""Reference tickers, HTF and regime in the graph (F435 W5 item 5.C, plan D8).

- The prefixed-writes contract of the Ticker node.
- No lookahead: a coarser reference (1d under 1h) gives each primary bar the
  last reference bar that closed before it (shared.align_htf_to_ltf); the
  same interval gives bar t the reference bar t, never t+1; moving a later
  reference bar never changes an earlier primary value.
- A reference cooks over its own (padded) bars, then aligns.
- Missing frames fail loudly; backtests fetch references with padding.
- Firing-regime parity: a regime rule strategy, auto-rendered, backtests
  exactly like the rule builder, with the regime fetch patched to a pickled
  frame (no network).

No test here fetches data: every fetch is patched or frames are passed in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import shared
from models import RegimeConfig, Rule, StrategyRequest
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import check_graph
from nodebuilder.compile import compile as nb_compile
from nodebuilder.evaluator import cook_program
from nodebuilder.from_rules import auto_render
from nodebuilder.models import Graph
from nodebuilder.prepare import (
    build_graph_attrs,
    reference_fetches,
    reference_padding_days,
)
from nodebuilder.run import cook_attrs, cook_graph_window, run_graph_backtest_cooked
from nodebuilder.trading import align
from nodebuilder.trading import sim_bridge as sb
from shared import align_htf_to_ltf, htf_lookback_days
from tests.nodebuilder import test_backtest_parity as parity

TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Frames and graphs
# ---------------------------------------------------------------------------


def _ohlcv(index: pd.DatetimeIndex, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1.0, len(index)))
    return pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, len(index))},
                        index=index)


def _hourly(days: int = 30, seed: int = 1) -> pd.DataFrame:
    """Yahoo-style 1h bars: 9:30 to 15:30 ET on business days."""
    stamps = []
    for day in pd.bdate_range("2024-03-04", periods=days):
        for h in range(7):
            stamps.append(pd.Timestamp(day.date()).tz_localize(TZ) + pd.Timedelta(hours=9.5 + h))
    return _ohlcv(pd.DatetimeIndex(stamps), seed)


def _daily(start: str = "2024-01-02", periods: int = 120, seed: int = 2) -> pd.DataFrame:
    """Yahoo-style 1d bars, stamped at midnight ET."""
    return _ohlcv(pd.bdate_range(start, periods=periods, tz=TZ), seed)


def _node(nid, typ, params=None, parent=None, name=None):
    return {"id": nid, "type": typ, "name": name or nid, "parent": parent, "params": params or {}}


def _graph(nodes, wires) -> Graph:
    """A v3 graph; each consumer's wires take in0, in1... in list order."""
    count: dict[str, int] = {}
    out = []
    for i, (a, b) in enumerate(wires):
        k = count.get(b, 0)
        count[b] = k + 1
        out.append({"id": f"w{i}", "from": a, "to": b, "to_port": f"in{k}"})
    return Graph.model_validate({"_version": 3, "nodes": {n["id"]: n for n in nodes},
                                 "wires": out})


def _ref_graph(ref_symbol="SPY", ref_interval="1d", prefix="spy", sma=None) -> Graph:
    """Implicit group: AAPL (no prefix) and a prefixed reference Ticker.
    Entry: the reference's close (or its SMA) above 0, read through a math
    node on the primary side, so the primary reads the aligned column."""
    ref_close = f"@{prefix}_close"
    nodes = [
        _node("t", "ticker", {"symbol": "AAPL", "interval": "1h"}),
        _node("r", "ticker", {"symbol": ref_symbol, "interval": ref_interval, "prefix": prefix}),
        _node("diff", "math", {"op": "sub", "a": "@close",
                               "b": "@ref_sma" if sma else ref_close, "out": "@diff"}),
        _node("up", "above", {"a": "@diff", "threshold": 0.0, "out": "@up"}),
        _node("entry", "entry", {"signal": "@up"}),
    ]
    wires = [("t", "diff"), ("diff", "up"), ("up", "entry")]
    if sma:
        nodes.append(_node("s", "sma", {"period": sma, "source": ref_close, "out": "@ref_sma"}))
        wires += [("r", "s"), ("s", "diff")]
    else:
        wires.append(("r", "diff"))
    return _graph(nodes, wires)


def _cook(graph, df, frames, keep_all=True, interval="1h"):
    program = nb_compile(graph)
    attrs = build_graph_attrs(program, df, frames=frames, interval=interval)
    return program, cook_attrs(program, attrs, keep_all=keep_all)


# ---------------------------------------------------------------------------
# The prefixed-writes contract
# ---------------------------------------------------------------------------


def test_a_prefixed_ticker_writes_the_five_prefixed_names_in_order():
    result = check_graph(_ref_graph())
    assert result.program is not None
    points = list(result.streams["r"].points)
    assert points == ["@spy_open", "@spy_high", "@spy_low", "@spy_close", "@spy_volume"]
    assert all(result.streams["r"].points[n].dtype == "float" for n in points)
    plain = list(result.streams["t"].points)
    assert plain == ["@open", "@high", "@low", "@close", "@volume", "@time", "@index"]


def test_a_reader_wired_to_a_prefixed_ticker_reads_its_close_by_default():
    nodes = [
        _node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        _node("r", "ticker", {"symbol": "SPY", "interval": "1d", "prefix": "spy"}),
        _node("rsi", "rsi", {"period": 14}),
        _node("lo", "below", {"threshold": 30.0}),
        _node("entry", "entry"),
    ]
    graph = _graph(nodes, [("r", "rsi"), ("rsi", "lo"), ("lo", "entry")])
    result = check_graph(graph)
    assert result.program is not None
    step = result.program.step("rsi")
    assert step.reads == ("@spy_close",)


def test_a_prefix_is_not_code_able_and_defaults_to_empty():
    from nodebuilder.kernel import registry

    spec = registry.get("ticker").param("prefix")
    assert (spec.type, spec.default, spec.code_able) == ("string", "", False)


# ---------------------------------------------------------------------------
# No lookahead
# ---------------------------------------------------------------------------


def test_coarser_alignment_is_align_htf_to_ltf():
    """1d under 1h: the positions rule gives exactly the shared function."""
    hourly, daily = _hourly(), _daily()
    values = daily["Close"].to_numpy()
    pos = align.align_positions(daily.index, hourly.index, coarser=True)
    got = align.align_values(values, pos, "float")
    want = align_htf_to_ltf(daily["Close"], hourly.index).to_numpy()
    np.testing.assert_array_equal(got, want)


def test_coarser_reference_gives_the_last_closed_daily_bar():
    """Every 1h bar of day D sees day D-1's close (D's own daily bar has not
    closed yet), through the whole graph cook."""
    hourly, daily = _hourly(), _daily()
    _p, result = _cook(_ref_graph(), hourly, {("SPY", "1d"): daily})
    aligned = result.column("r", "@spy_close")
    for i, t in enumerate(hourly.index):
        before = daily.index[daily.index.normalize() < t.normalize()]
        assert aligned[i] == daily["Close"].loc[before[-1]]


def test_same_interval_gives_bar_t_never_t_plus_1():
    daily_a = _daily(seed=3)
    daily_b = _daily(seed=4)
    nodes = [
        _node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        _node("r", "ticker", {"symbol": "SPY", "interval": "1d", "prefix": "spy"}),
        _node("diff", "math", {"op": "sub", "a": "@close", "b": "@spy_close", "out": "@diff"}),
        _node("up", "above", {"a": "@diff", "threshold": 0.0, "out": "@up"}),
        _node("entry", "entry", {"signal": "@up"}),
    ]
    graph = _graph(nodes, [("t", "diff"), ("r", "diff"), ("diff", "up"), ("up", "entry")])
    _p, result = _cook(graph, daily_a, {("SPY", "1d"): daily_b}, interval="1d")
    np.testing.assert_array_equal(result.column("diff", "@spy_close"), daily_b["Close"].to_numpy())


@pytest.mark.parametrize("prim_unit, ref_unit", [("s", "ns"), ("ns", "us"), ("us", "s")])
def test_frames_stored_in_different_time_units_align_the_same(prim_unit, ref_unit):
    """pandas keeps an index's unit (a pickled yfinance frame is in seconds,
    a fresh one in nanoseconds): alignment and the bar-spacing guess must
    not depend on it."""
    hourly, daily = _hourly(), _daily()
    want = align.align_positions(daily.index, hourly.index, coarser=True)
    p_idx, r_idx = hourly.index.as_unit(prim_unit), daily.index.as_unit(ref_unit)
    np.testing.assert_array_equal(align.align_positions(r_idx, p_idx, coarser=True), want)
    assert align.bar_seconds(r_idx) == 86_400.0
    assert align.is_coarser("1d", r_idx, None, p_idx) is True
    assert align.is_coarser("1d", r_idx, None, daily.index.as_unit(prim_unit)) is False


def test_same_interval_with_a_gap_forward_fills_and_never_looks_ahead():
    primary = _daily(periods=40, seed=5)
    ref = _daily(periods=40, seed=6).drop(index=_daily(periods=40).index[[10, 11, 25]])
    pos = align.align_positions(ref.index, primary.index, coarser=False)
    got = align.align_values(ref["Close"].to_numpy(), pos, "float")
    for i, t in enumerate(primary.index):
        seen = ref.index[ref.index <= t]
        assert got[i] == ref["Close"].loc[seen[-1]]


@pytest.mark.parametrize("ref_interval, primary, ref", [
    ("1d", _hourly(), _daily()),          # coarser
    ("1d", _daily(seed=7), _daily()),     # same
])
def test_moving_a_later_reference_bar_never_changes_an_earlier_value(ref_interval, primary, ref):
    """Perturb reference bar k (all its prices): a primary bar before the
    time bar k becomes visible keeps its value, through the whole cook
    (an SMA 5 on the reference included)."""
    interval = "1h" if len(primary) > len(ref) else "1d"
    graph = _ref_graph(ref_interval=ref_interval, sma=5)
    _p, base = _cook(graph, primary, {("SPY", ref_interval): ref}, interval=interval)
    coarser = interval == "1h"
    # Reference bars inside the primary's window (first, middle, last).
    inside = np.flatnonzero((ref.index > primary.index[0])
                            & (ref.index < primary.index[-1] - pd.Timedelta(days=3)))
    for k in (inside[0], inside[len(inside) // 2], inside[-1]):
        moved = ref.copy()
        moved.iloc[k, :4] = moved.iloc[k, :4] * 3.0
        _p, after = _cook(graph, primary, {("SPY", ref_interval): moved}, interval=interval)
        # Bar k is visible from its own time (same interval) or once the
        # next reference bar has started (coarser: shifted one bar).
        visible_from = ref.index[k + 1] if coarser else ref.index[k]
        early = np.asarray(primary.index < visible_from)
        assert early.any() and (~early).any()
        for node, name in (("diff", "@spy_close"), ("diff", "@ref_sma"), ("diff", "@diff")):
            b, a = base.column(node, name), after.column(node, name)
            np.testing.assert_array_equal(a[early], b[early])
        assert not np.array_equal(after.column("diff", "@spy_close"),
                                  base.column("diff", "@spy_close"))


# ---------------------------------------------------------------------------
# A reference cooks on its own bars
# ---------------------------------------------------------------------------


def test_a_reference_indicator_runs_over_the_reference_bars_with_their_padding():
    """The SPY SMA 20 is warm on the primary's first bar, because SPY's
    frame starts earlier (its padding), and equals the SMA of SPY's own
    daily bars, not of forward-filled hourly repeats."""
    hourly = _hourly()
    daily = _daily(start="2023-12-01", periods=100)
    _p, result = _cook(_ref_graph(sma=20), hourly, {("SPY", "1d"): daily})
    sma_daily = daily["Close"].rolling(20).mean()
    want = align_htf_to_ltf(sma_daily, hourly.index).to_numpy()
    got = result.column("diff", "@ref_sma")
    assert not np.isnan(got[0])
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-9)
    # The inspector cook holds the reference-domain node too, aligned.
    np.testing.assert_allclose(result.column("s", "@ref_sma"), want, rtol=0, atol=1e-9)


def test_a_constant_read_through_a_reference_stream_has_no_warmup_gap():
    """A constant feeds a reference-side comparison and reaches the primary
    side through it: it is 1.0 on every primary bar, also before the
    reference's first bar (where the reference's own columns are NaN)."""
    hourly = _hourly()
    daily = _daily(start="2024-03-11", periods=40)       # starts after the hourly
    nodes = [
        _node("t", "ticker", {"symbol": "AAPL", "interval": "1h"}),
        _node("r", "ticker", {"symbol": "SPY", "interval": "1d", "prefix": "spy"}),
        _node("c", "constant", {"value": 1.0, "out": "@c"}),
        _node("cmp", "above", {"a": "@spy_close", "b": "@c", "out": "@spy_up"}),
        _node("m", "math", {"op": "add", "a": "@close", "b": "@c", "out": "@m"}),
        _node("up", "above", {"a": "@m", "threshold": 0.0, "out": "@up"}),
        _node("entry", "entry", {"signal": "@up"}),
    ]
    graph = _graph(nodes, [("r", "cmp"), ("c", "cmp"), ("t", "m"), ("cmp", "m"),
                           ("m", "up"), ("up", "entry")])
    _p, result = _cook(graph, hourly, {("SPY", "1d"): daily})
    np.testing.assert_array_equal(result.column("m", "@m"), hourly["Close"].to_numpy() + 1.0)
    spy = result.column("m", "@spy_close")
    assert np.isnan(spy[0]) and not np.isnan(spy[-1])


def test_a_missing_reference_frame_fails_loudly():
    graph = _ref_graph()
    program = nb_compile(graph)
    with pytest.raises(ValueError, match="no bars were fetched"):
        build_graph_attrs(program, _hourly(), frames={})
    with pytest.raises(ValueError, match="no bars were fetched"):
        cook_program(program, _hourly())


def test_reference_fetches_are_padded_for_the_lookback():
    program = nb_compile(_ref_graph(ref_interval="1d", sma=50))
    [f] = reference_fetches(program, "2024-03-04", "2024-04-30")
    assert f.key == ("SPY", "1d") and f.end == "2024-04-30"
    pad = (pd.Timestamp("2024-03-04") - pd.Timestamp(f.start)).days
    assert pad == reference_padding_days(51, "1d")
    assert pad >= htf_lookback_days("ma", {"period": 50})
    # A weekly frame needs weeks of history, not days.
    assert reference_padding_days(51, "1wk") > 51 * 7


def test_backtest_fetches_the_reference_with_padding_and_lists_its_frame(monkeypatch):
    hourly = _hourly()
    daily = _daily(start="2023-10-02", periods=150)
    calls = []

    def fake(ticker, start, end, interval, source="yahoo", extended_hours=False):
        calls.append((ticker, start, interval, source))
        return daily if ticker == "SPY" else hourly

    monkeypatch.setattr(shared, "_fetch", fake)
    req = GraphBacktestRequest(graph=_ref_graph(sma=20), ticker="AAPL", start="2024-03-04",
                               end="2024-04-12", interval="1h", source="yahoo")
    _response, cook = run_graph_backtest_cooked(req, df=hourly)
    [(sym, start, itv, source)] = calls
    assert (sym, itv, source) == ("SPY", "1d", "yahoo")
    assert pd.Timestamp(start) < pd.Timestamp("2024-03-04")
    assert [(f[0], f[1]) for f in cook.frames] == [("AAPL", "1h"), ("SPY", "1d")]

    calls.clear()
    window = cook_graph_window(_ref_graph(sma=20), ticker="AAPL", start="2024-03-04",
                               end="2024-04-12", interval="1h", source="yahoo", df=hourly)
    assert [(f[0], f[1]) for f in window.frames] == [("AAPL", "1h"), ("SPY", "1d")]


def test_run_group_fetches_missing_references_itself(monkeypatch):
    """BotManager's backtest calls sim_bridge.run_group with no frames."""
    hourly, daily = _hourly(), _daily(start="2023-12-01", periods=100)
    seen = []

    def fake(ticker, start, end, interval, source="yahoo", extended_hours=False):
        seen.append((ticker, interval, source))
        return daily

    monkeypatch.setattr(shared, "_fetch", fake)
    program = nb_compile(_ref_graph(sma=5))
    req = {"ticker": "AAPL", "start": "2024-03-04", "end": "2024-04-12", "interval": "1h",
           "source": "yahoo", "initial_capital": 10_000.0, "position_size": 1.0,
           "stop_loss_pct": None, "trailing_stop": None, "max_bars_held": None,
           "slippage_bps": 0.0, "commission_pct": 0.0, "per_share_rate": 0.0,
           "min_per_order": 0.0, "borrow_rate_annual": 0.5, "dynamic_sizing": None,
           "skip_after_stop": None, "trading_hours": None, "direction": "long"}
    run = sb.run_group(program, hourly, req)
    assert seen == [("SPY", "1d", "yahoo")]
    assert run.sim["summary"]["num_trades"] >= 0


# ---------------------------------------------------------------------------
# Which Ticker is a reference
# ---------------------------------------------------------------------------


def test_in_a_graph_with_no_group_only_prefixed_tickers_are_references():
    """Two Tickers with no prefix both read the request's frame (D11, as
    before W5); a prefixed one is a reference."""
    nodes = [
        _node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        _node("t2", "ticker", {"symbol": "MSFT", "interval": "1wk"}),
        _node("r", "ticker", {"symbol": "SPY", "interval": "1d", "prefix": "spy"}),
        _node("diff", "math", {"op": "sub", "a": "@close", "b": "@spy_close", "out": "@diff"}),
        _node("up", "above", {"a": "@diff", "threshold": 0.0, "out": "@up"}),
        _node("entry", "entry", {"signal": "@up"}),
    ]
    graph = _graph(nodes, [("t", "diff"), ("r", "diff"), ("diff", "up"), ("up", "entry")])
    program = nb_compile(graph)
    roles = align.ticker_roles(program)
    assert roles.primary == {"t", "t2"}
    assert [r.node_id for r in roles.references] == ["r"]
    [group] = program.groups
    assert [r.node_id for r in group.references] == ["r"]


def test_two_references_with_the_same_prefix_clash():
    nodes = [
        _node("t", "ticker", {"symbol": "AAPL", "interval": "1d"}),
        _node("r1", "ticker", {"symbol": "SPY", "interval": "1d", "prefix": "ref"}),
        _node("r2", "ticker", {"symbol": "QQQ", "interval": "1d", "prefix": "ref"}),
        _node("a", "above", {"a": "@ref_close", "threshold": 0.0, "out": "@a"}),
        _node("b", "below", {"a": "@ref_close", "threshold": 0.0, "out": "@b"}),
        _node("both", "or", {"terms": ["@a", "@b"]}),
        _node("entry", "entry"),
    ]
    graph = _graph(nodes, [("r1", "a"), ("r2", "b"), ("a", "both"), ("b", "both"),
                           ("both", "entry")])
    codes = [(d.code, d.node_id, d.param) for d in check_graph(graph).diagnostics
             if d.severity == "error"]
    # The two branches never merge into a read of @ref_close, so the kernel
    # alone would only warn; the group check names the second Ticker.
    assert codes == [("attr_clash", "r2", "prefix")]


# ---------------------------------------------------------------------------
# Firing-regime parity: the rule builder's regime, edited as a graph
# ---------------------------------------------------------------------------

_FULL = parity._load_df("regime_mode")       # AAPL 1d, 2022-01-03 .. 2023-12-29
_WEEKLY = _FULL.resample("W-MON", label="left", closed="left").agg(
    {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
_START = "2022-07-01"   # the backtest window starts half a year into the pickle


def _pickled_fetch(calls):
    def fake(ticker, start, end, interval, source="yahoo", extended_hours=False):
        calls.append((ticker, start, interval))
        base = _FULL if interval == "1d" else _WEEKLY
        tz = base.index.tz
        return base.loc[pd.Timestamp(start, tz=tz): pd.Timestamp(end, tz=tz)]
    return fake


_SIDES = dict(
    long_buy_rules=[Rule(indicator="rsi", condition="below", value=45)],
    long_sell_rules=[Rule(indicator="rsi", condition="above", value=60)],
    short_buy_rules=[Rule(indicator="rsi", condition="above", value=55)],
    short_sell_rules=[Rule(indicator="rsi", condition="below", value=40)],
)

_FIRING = {
    # The legacy single-indicator regime: close above its SMA 50, held 3 bars.
    "legacy_sma50": RegimeConfig(enabled=True, indicator="ma",
                                 indicator_params={"period": 50, "type": "sma"},
                                 condition="above", min_bars=3, on_flip="close_only"),
    # A legacy MA with no type is an EMA in the rule engine (compute_ma).
    "legacy_ma_default_ema": RegimeConfig(enabled=True, indicator="ma",
                                          indicator_params={"period": 20},
                                          condition="above", min_bars=2,
                                          on_flip="close_and_reverse"),
    # The rules path, with params: SMA 30 above the close.
    "rules_with_params": RegimeConfig(enabled=True, rules=[Rule(
        indicator="ma", condition="above", param="close", params={"period": 30, "type": "sma"})],
        min_bars=1, on_flip="close_only"),
    # A coarser regime frame: weekly bars under a daily strategy.
    "weekly": RegimeConfig(enabled=True, timeframe="1wk", indicator="ma",
                           indicator_params={"period": 3, "type": "sma"},
                           condition="above", min_bars=1, on_flip="hold"),
}


@pytest.mark.parametrize("name", list(_FIRING))
def test_firing_regime_backtests_like_the_rule_builder(name, monkeypatch):
    """A regime rule strategy whose regime actually turns on and off,
    auto-rendered and backtested as a graph, gives the rule backtest's
    trades, equity and summary (plan W5 acceptance)."""
    calls: list = []
    monkeypatch.setattr(shared, "_fetch", _pickled_fetch(calls))
    df = _FULL.loc[pd.Timestamp(_START, tz=_FULL.index.tz):]
    req = StrategyRequest(ticker="AAPL", start=_START, end="2024-01-01", interval="1d",
                          source="yahoo", buy_rules=[], sell_rules=[], regime=_FIRING[name],
                          **_SIDES)
    rule = parity._run_rule_path(req, df)
    graph = parity._run_graph_path(req, df)
    parity._compare_results(rule, graph, name)

    # It proves something: the regime flips, and both sides trade.
    kinds = {t["type"] for t in graph["trades"]}
    assert {"buy", "short"} <= kinds, kinds
    program = nb_compile(auto_render(req))
    attrs = build_graph_attrs(program, df, frames={
        ("AAPL", _FIRING[name].timeframe): _pickled_fetch([])(
            "AAPL", "2021-01-01", "2024-01-01", _FIRING[name].timeframe)}, interval="1d")
    [group] = program.groups
    result = sb.cook(program, attrs, group.plan.keep_ids())
    regime = np.asarray(result.column(group.plan.regime.node_id, group.plan.regime.attr), bool)
    assert 0.1 < regime.mean() < 0.9, regime.mean()
    # Both paths fetched the regime frame (each with its own padding).
    assert {c[2] for c in calls} == {_FIRING[name].timeframe}


# ---------------------------------------------------------------------------
# Live: the bot's window gives the backtest's regime at its last bar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["legacy_sma50", "weekly"])
def test_live_window_regime_matches_the_backtest(name):
    """The bot cooks its group over its own fetch window, with the regime's
    frame fetched over the window for the regime interval (the same-key
    regime frame is the bot's own window).  At the window's last bar the
    regime and the four side signals equal the full-history backtest's."""
    from datetime import date

    from bot_manager import BotConfig
    from bot_runner import cook_graph_bar, graph_bot_live
    from nodebuilder.prepare import live_fetch_start

    rc = _FIRING[name]
    req = StrategyRequest(ticker="AAPL", start=_START, end="2024-01-01", interval="1d",
                          source="yahoo", buy_rules=[], sell_rules=[], regime=rc, **_SIDES)
    graph = auto_render(req)
    program = nb_compile(graph)
    cfg = BotConfig(strategy_name="r", symbol="AAPL", interval="1d", buy_rules=[],
                    sell_rules=[], long_buy_rules=None, long_sell_rules=None,
                    short_buy_rules=None, short_sell_rules=None, allocated_capital=1000.0,
                    kind="graph", graph=graph, graph_direction_mode="regime_switch")
    live = graph_bot_live(program, cfg)
    ref_base = _FULL if rc.timeframe == "1d" else _WEEKLY

    attrs = build_graph_attrs(program, _FULL, frames={("AAPL", rc.timeframe): ref_base},
                              interval="1d")
    full = sb.cook(program, attrs, live.plan.keep_ids())
    tz = _FULL.index.tz
    for e in range(len(_FULL) - 60, len(_FULL), 7):
        day = _FULL.index[e].date()
        window = _FULL.loc[pd.Timestamp(live_fetch_start(program, "1d", today=day), tz=tz):
                           _FULL.index[e]]
        ref_start = pd.Timestamp(live_fetch_start(program, rc.timeframe, today=day), tz=tz)
        ref = window if rc.timeframe == "1d" else ref_base.loc[ref_start:_FULL.index[e]]
        _attrs, sigs = cook_graph_bar(live.program, window, None, live.plan,
                                      {("AAPL", rc.timeframe): ref})
        for key in ("regime", "entry_long", "entry_short", "exit_long", "exit_short"):
            read = live.plan.regime if key == "regime" else getattr(live.plan, key)
            want = bool(np.asarray(full.column(read.node_id, read.attr), bool)[e])
            assert sigs[key] == want, (name, str(day), key)
        assert isinstance(day, date)
