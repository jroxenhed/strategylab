"""F435 W5 review fixes, kernel and rendering (fixer B).

- KA-1 / KA-6: reference alignment by bar END time.  A same-interval daily
  or weekly reference on another calendar (BTC-like, stamped 00:00 UTC under
  an ET-stamped primary) is shifted; a reference missing the current
  period's bar still gives the last bar that closed.  Moving a later
  reference bar never changes an earlier primary value (finer, same
  calendar, other calendar, weekly).
- KA-9: 1h primary with a 1d regime backtests like the rule builder; a live
  window cook gives the full-history value for a cross-symbol reference.
- KA-3: legs of different intervals combine at bar close.
- KA-2 / KA-4: the inspector cooks only the window's groups; a stray Ticker
  is never fetched and never fails a run.
- KA-7 / KA-8: unscoped settings warn; bypassing a Subnet switches off what
  it holds.
- DI-05: ticker_missing and group_duplicate_terminal.
- FE-04 (backend): capital weight 0.
- DI-01 / DI-02 / DI-03 / DI-09: a rule strategy rendered as a graph runs
  like the rule backtest when the request carries only the sidebar fields
  (what graph view sends), per-side terminals included.

No test fetches data: frames are passed in or the fetch is patched.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import shared
from bot_manager import BotConfig
from models import RegimeConfig, Rule, StrategyRequest, TrailingStopConfig
from nodebuilder import trading  # noqa: F401  (registers every node type)
from nodebuilder.api_models import GraphBacktestRequest
from nodebuilder.compile import check_graph
from nodebuilder.compile import compile as nb_compile
from nodebuilder.from_rules import _GRAPH_BORROW_DEFAULT, auto_render
from nodebuilder.kernel.flatten import flatten
from nodebuilder.models import Graph, GraphValidationError
from nodebuilder.prepare import build_graph_attrs
from nodebuilder.run import (
    cook_attrs,
    cook_graph_window,
    run_graph_backtest,
    run_graph_backtest_cooked,
)
from nodebuilder.trading import align
from nodebuilder.trading import nodes_groups as ng
from nodebuilder.trading import sim_bridge as sb
from shared import align_htf_to_ltf
from tests.nodebuilder import test_backtest_parity as parity
from tests.nodebuilder import test_output_groups as og
from tests.nodebuilder import test_reference_tickers as rt

TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------


def _ohlcv(index, seed: int, amp: float = 0.0, period: float = 40.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = np.arange(len(index))
    close = 100 + amp * np.sin(t / period) + np.cumsum(rng.normal(0, 1.0, len(index)))
    return pd.DataFrame({"Open": close, "High": close + 0.5, "Low": close - 0.5,
                         "Close": close, "Volume": rng.integers(1e5, 1e6, len(index))},
                        index=index)


def _et_daily(start="2024-01-02", periods=120, seed=2):
    return _ohlcv(pd.bdate_range(start, periods=periods, tz=TZ), seed)


def _utc_daily(start="2023-12-01", periods=200, seed=3):
    """A crypto-like daily frame: every calendar day, stamped 00:00 UTC."""
    return _ohlcv(pd.date_range(start, periods=periods, freq="D", tz="UTC"), seed)


def _et_weekly(start="2023-06-05", periods=60, seed=4):
    return _ohlcv(pd.date_range(start, periods=periods, freq="W-MON", tz=TZ), seed)


def _utc_weekly(start="2023-06-05", periods=60, seed=5):
    return _ohlcv(pd.date_range(start, periods=periods, freq="W-MON", tz="UTC"), seed)


def _hourly(start="2024-03-04", days=30, seed=1, amp=0.0):
    """Yahoo-style 1h bars: 9:30 to 15:30 ET on business days."""
    stamps = []
    for day in pd.bdate_range(start, periods=days):
        for h in range(7):
            stamps.append(pd.Timestamp(day.date()).tz_localize(TZ) + pd.Timedelta(hours=9.5 + h))
    return _ohlcv(pd.DatetimeIndex(stamps), seed, amp=amp, period=7 * 25)


def _ref_graph(primary_interval: str, ref_symbol: str, ref_interval: str, sma: int = 3) -> Graph:
    """Implicit group: AAPL on *primary_interval* and a prefixed reference
    Ticker; the primary reads the reference's close and its SMA."""
    n = rt._node
    nodes = [
        n("t", "ticker", {"symbol": "AAPL", "interval": primary_interval}),
        n("r", "ticker", {"symbol": ref_symbol, "interval": ref_interval, "prefix": "ref"}),
        n("s", "sma", {"period": sma, "source": "@ref_close", "out": "@ref_sma"}),
        n("diff", "math", {"op": "sub", "a": "@close", "b": "@ref_sma", "out": "@diff"}),
        n("up", "above", {"a": "@diff", "threshold": 0.0, "out": "@up"}),
        n("entry", "entry", {"signal": "@up"}),
    ]
    wires = [("t", "diff"), ("r", "s"), ("s", "diff"), ("diff", "up"), ("up", "entry")]
    return rt._graph(nodes, wires)


def _cook(graph, df, frames, interval):
    program = nb_compile(graph)
    attrs = build_graph_attrs(program, df, frames=frames, interval=interval)
    return cook_attrs(program, attrs, keep_all=True)


def _ns(index) -> np.ndarray:
    return align._utc_ns(index, "x")


# ---------------------------------------------------------------------------
# KA-1: a same-interval daily+ reference on another calendar is shifted
# ---------------------------------------------------------------------------


def test_utc_daily_reference_under_an_et_daily_primary_gets_the_last_closed_day():
    primary, ref = _et_daily(), _utc_daily()
    assert align.shifts("1d", ref.index, "1d", primary.index)
    pos = align.align_positions(ref.index, primary.index, True, ref_interval="1d")
    ends = _ns(ref.index) + 86_400 * 10**9
    labels = _ns(primary.index)
    for i in range(len(primary)):
        k = pos[i]
        # The reference bar has closed by the time the primary bar opens...
        assert ends[k] <= labels[i]
        # ...and it is the freshest one that has.
        assert k + 1 == len(ref) or ends[k + 1] > labels[i]
    # The old exact join gave the same calendar day, whose bar closes at
    # 00:00 UTC D+1, after AAPL's close (the KA-1 probe).
    exact = align.align_positions(ref.index, primary.index, False)
    assert (exact == pos + 1).all()


def test_utc_weekly_reference_under_an_et_weekly_primary_gets_the_previous_week():
    primary, ref = _et_weekly(), _utc_weekly()
    assert align.shifts("1wk", ref.index, "1wk", primary.index)
    pos = align.align_positions(ref.index, primary.index, True, ref_interval="1wk")
    # The same Monday label: the UTC week (with its weekend) is not closed
    # when the ET week opens; the week before it is.
    np.testing.assert_array_equal(pos, np.arange(len(primary)) - 1)


def test_one_calendar_same_interval_joins_exactly():
    a, b = _et_daily(seed=6), _et_daily(seed=7)
    assert align.one_calendar(a.index, b.index)
    assert not align.shifts("1d", b.index, "1d", a.index)
    assert not align.one_calendar(a.index, _utc_daily().index)
    naive = a.index.tz_localize(None)
    assert not align.one_calendar(a.index, naive)
    assert align.one_calendar(naive, naive)
    # Intraday same interval always joins exactly, whatever the zone.
    h = _hourly()
    assert not align.shifts("1h", h.index.tz_convert("UTC"), "1h", h.index)


def test_shifted_alignment_is_align_htf_to_ltf_on_complete_data():
    hourly, daily = _hourly(), _et_daily()
    pos = align.align_positions(daily.index, hourly.index, True, ref_interval="1d")
    got = align.align_values(daily["Close"].to_numpy(), pos, "float")
    want = align_htf_to_ltf(daily["Close"], hourly.index).to_numpy()
    np.testing.assert_array_equal(got, want)


def test_a_daily_bar_ends_at_its_own_midnight_across_a_dst_change():
    """2024-03-10 is the spring DST change in New York: day 03-08's bar
    ends at 03-09 00:00 ET, not 24 hours later in UTC terms of a 23 h day."""
    idx = pd.DatetimeIndex([pd.Timestamp("2024-03-09", tz=TZ), pd.Timestamp("2024-03-10", tz=TZ)])
    ends = align.bar_ends(idx, "1d")
    assert ends[1] == pd.Timestamp("2024-03-11", tz=TZ).value


# ---------------------------------------------------------------------------
# KA-6: by time, not by position
# ---------------------------------------------------------------------------


def test_a_reference_without_todays_bar_gives_the_last_closed_day_not_two_back():
    hourly, daily = _hourly(), _et_daily(start="2024-01-02", periods=60)
    full = align.align_positions(daily.index, hourly.index, True, ref_interval="1d")
    last_day = hourly.index[-1].normalize()
    partial = daily[daily.index < last_day]          # no bar for the last day yet
    got = align.align_positions(partial.index, hourly.index, True, ref_interval="1d")
    np.testing.assert_array_equal(got, full)
    # The old position rule went one bar further back on the last day.
    by_position = np.searchsorted(_ns(partial.index), _ns(hourly.index), side="right") - 2
    on_last = np.asarray(hourly.index.normalize() == last_day)
    assert (by_position[on_last] == full[on_last] - 1).all()


def test_a_holiday_on_the_reference_calendar_keeps_the_last_closed_bar():
    hourly = _hourly()
    daily = _et_daily(start="2024-01-02", periods=60)
    holiday = pd.Timestamp("2024-03-12", tz=TZ)
    gap = daily.drop(index=[holiday])
    pos = align.align_positions(gap.index, hourly.index, True, ref_interval="1d")
    for i, t in enumerate(hourly.index):
        closed = gap.index[gap.index + pd.DateOffset(days=1) <= t]
        assert gap.index[pos[i]] == closed[-1], t


# ---------------------------------------------------------------------------
# Lookahead: moving a later reference bar never changes an earlier value
# ---------------------------------------------------------------------------


def _stock_daily_close(idx):
    return _ns(idx.normalize() + pd.Timedelta(hours=16))


def _stock_weekly_close(idx):
    return _ns(idx.normalize() + pd.Timedelta(days=4, hours=16))


def _stock_hourly_close(idx):
    session_end = idx.normalize() + pd.Timedelta(hours=16)
    return np.minimum(_ns(idx + pd.Timedelta(hours=1)), _ns(session_end))


def _utc_daily_close(idx):
    return _ns(idx + pd.Timedelta(days=1))


def _utc_weekly_close(idx):
    return _ns(idx + pd.Timedelta(days=7))


# (primary interval, primary frame, its real bar close, reference interval,
# reference frame, its real bar close).  The closes are worked out here
# from what each market is (a stock session ends 16:00 New York; a crypto
# bar ends one period after its 00:00 UTC label), never from align's own
# decision, so the test cannot agree with a wrong rule.
_PERTURB = {
    "utc_daily_under_et_daily": (
        "1d", lambda: _et_daily(start="2024-01-02", periods=80), _stock_daily_close,
        "1d", lambda: _utc_daily(start="2023-12-01", periods=150), _utc_daily_close),
    "utc_weekly_under_et_weekly": (
        "1wk", lambda: _et_weekly(start="2023-09-04", periods=40), _stock_weekly_close,
        "1wk", lambda: _utc_weekly(start="2023-06-05", periods=60), _utc_weekly_close),
    "finer_1h_under_1d": (
        "1d", lambda: _et_daily(start="2024-03-04", periods=25), _stock_daily_close,
        "1h", lambda: _hourly(start="2024-02-20", days=40, seed=9), _stock_hourly_close),
    "coarser_1d_under_1h": (
        "1h", lambda: _hourly(), _stock_hourly_close,
        "1d", lambda: _et_daily(start="2024-01-02", periods=60), _stock_daily_close),
    "same_calendar_1d": (
        "1d", lambda: _et_daily(start="2024-01-02", periods=80, seed=8), _stock_daily_close,
        "1d", lambda: _et_daily(start="2023-11-01", periods=120), _stock_daily_close),
}


@pytest.mark.parametrize("case", list(_PERTURB))
def test_moving_a_later_reference_bar_never_changes_an_earlier_value(case):
    """Perturb reference bar k (all its prices): every primary bar that
    closes before bar k closes keeps its values, through the whole cook (an
    SMA on the reference included)."""
    p_itv, make_primary, p_close, r_itv, make_ref, r_close = _PERTURB[case]
    primary, ref = make_primary(), make_ref()
    graph = _ref_graph(p_itv, "REF", r_itv)
    base = _cook(graph, primary, {("REF", r_itv): ref}, p_itv)
    closes_p, closes_r = p_close(primary.index), r_close(ref.index)
    inside = np.flatnonzero((closes_r > closes_p[0]) & (ref.index < primary.index[-5]))
    assert len(inside) >= 3
    reached = 0
    for k in (inside[0], inside[len(inside) // 2], inside[-1]):
        moved = ref.copy()
        moved.iloc[k, :4] = moved.iloc[k, :4] * 3.0
        after = _cook(graph, primary, {("REF", r_itv): moved}, p_itv)
        early = closes_p < closes_r[k]
        assert early.any() and (~early).any()
        for name in ("@ref_close", "@ref_sma", "@diff"):
            np.testing.assert_array_equal(after.column("diff", name)[early],
                                          base.column("diff", name)[early])
        reached += int(not np.array_equal(after.column("diff", "@ref_close"),
                                          base.column("diff", "@ref_close")))
    assert reached, "the perturbation never reached the primary: the test proves nothing"


# ---------------------------------------------------------------------------
# KA-9: live window parity for a cross-symbol reference (SPY 1d under AAPL 1h)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("today_bar", [True, False])
def test_a_live_window_cook_gives_the_full_history_value(today_bar):
    """A bot cooks over its fetch window; the reference frame it gets may
    or may not hold today's (forming) daily bar.  Either way the last bar's
    values equal the full-history backtest cook's."""
    hourly = _hourly(start="2024-01-02", days=80, seed=11)
    spy = _et_daily(start="2023-10-02", periods=170, seed=12)
    graph = _ref_graph("1h", "SPY", "1d", sma=5)
    full = _cook(graph, hourly, {("SPY", "1d"): spy}, "1h")
    for e in range(len(hourly) - 140, len(hourly), 9):
        now = hourly.index[e]
        window = hourly.iloc[max(0, e - 200): e + 1]
        upto = now if today_bar else now.normalize() - pd.Timedelta(seconds=1)
        ref = spy.loc[now.normalize() - pd.Timedelta(days=60): upto]
        live = _cook(graph, window, {("SPY", "1d"): ref}, "1h")
        for name in ("@ref_close", "@ref_sma", "@diff"):
            assert live.column("diff", name)[-1] == full.column("diff", name)[e], (str(now), name)
        assert bool(live.column("entry", "@up")[-1]) == bool(full.column("entry", "@up")[e])


# ---------------------------------------------------------------------------
# KA-9: 1h primary, 1d regime, end to end against the rule builder
# ---------------------------------------------------------------------------


_H_ALL = _hourly(start="2022-09-01", days=330, seed=21, amp=12.0)
_D_ALL = _H_ALL.groupby(_H_ALL.index.normalize()).agg(
    {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
_H_START = "2023-01-03"


def _hourly_fetch(calls):
    def fake(ticker, start, end, interval, source="yahoo", extended_hours=False):
        calls.append((ticker, start, interval))
        base = _H_ALL if interval == "1h" else _D_ALL
        return base.loc[pd.Timestamp(start, tz=TZ): pd.Timestamp(end, tz=TZ)]
    return fake


def test_hourly_strategy_with_a_daily_regime_backtests_like_the_rule_builder(monkeypatch):
    calls: list = []
    monkeypatch.setattr(shared, "_fetch", _hourly_fetch(calls))
    df = _H_ALL.loc[pd.Timestamp(_H_START, tz=TZ):]
    regime = RegimeConfig(enabled=True, timeframe="1d", indicator="ma",
                          indicator_params={"period": 10, "type": "sma"},
                          condition="above", min_bars=1, on_flip="close_only")
    req = StrategyRequest(ticker="AAPL", start=_H_START, end="2024-01-01", interval="1h",
                          source="yahoo", buy_rules=[], sell_rules=[], regime=regime,
                          **rt._SIDES)
    rule = parity._run_rule_path(req, df)
    graph = _sidebar_run(req, df)
    parity._compare_results(rule, graph, "hourly_daily_regime")
    kinds = {t["type"] for t in graph["trades"]}
    assert {"buy", "short"} <= kinds, kinds
    program = nb_compile(auto_render(req))
    attrs = build_graph_attrs(program, df, frames={("AAPL", "1d"): _D_ALL}, interval="1h")
    [group] = program.groups
    result = sb.cook(program, attrs, group.plan.keep_ids())
    on = np.asarray(result.column(group.plan.regime.node_id, group.plan.regime.attr), bool)
    assert 0.1 < on.mean() < 0.9, on.mean()


# ---------------------------------------------------------------------------
# KA-3: legs of different intervals combine at bar close
# ---------------------------------------------------------------------------


def _track(index, keys, equity, in_pos, notional, interval, capital=1000.0):
    equity = np.asarray(equity, dtype=float)
    return ng.LegTrack(
        times=ng.utc_naive(index), keys=keys, equity=equity, baseline=equity.copy(),
        in_pos=np.asarray(in_pos, dtype=bool), notional=np.asarray(notional, dtype=float),
        capital=capital, final_value=float(equity[-1]), num_trades=0,
        interval=interval, ends=align.bar_ends(index, interval))


def test_a_daily_legs_close_never_shows_on_that_days_hourly_bars():
    d_idx = pd.DatetimeIndex([pd.Timestamp("2024-03-04", tz=TZ), pd.Timestamp("2024-03-05", tz=TZ)])
    h_idx = pd.DatetimeIndex([pd.Timestamp("2024-03-05", tz=TZ) + pd.Timedelta(hours=9.5 + h)
                              for h in range(7)])
    daily = _track(d_idx, ["2024-03-04", "2024-03-05"], [1000, 500], [False, True], [0, 500], "1d")
    hourly = _track(h_idx, [int(t.timestamp()) for t in h_idx], [1000] * 7, [False] * 7,
                    [0] * 7, "1h")
    out = ng.combine([daily, hourly], 2000.0)
    values = [p["value"] for p in out["equity_curve"]]
    times = [p["time"] for p in out["equity_curve"]]
    # Day 03-04 closes at 03-05 00:00 ET, the hourly bars close 10:30..16:30,
    # day 03-05 closes at 03-06 00:00 ET: its 500 shows only then.
    assert values == [2000.0] * 8 + [1500.0]
    assert all(isinstance(t, int) for t in times) and times == sorted(set(times))
    assert times[-1] == int(pd.Timestamp("2024-03-06", tz=TZ).timestamp())
    assert times[1] == int((h_idx[0] + pd.Timedelta(hours=1)).timestamp())
    # The daily position (entered at 03-05's close) is held only from then on.
    assert out["summary"]["exposure_pct"] == round(100 / 9, 2)


def test_same_interval_legs_still_combine_by_label():
    a = og.AAPL.iloc[:5]
    b = og.MSFT.iloc[:5]
    keys = [t.strftime("%Y-%m-%d") for t in a.index]
    la = _track(a.index, keys, [1000, 1010, 990, 1000, 1020], [False] * 5, [0] * 5, "1d")
    lb = _track(b.index, list(keys), [1000, 1000, 1005, 1000, 1000], [False] * 5, [0] * 5, "1d")
    out = ng.combine([la, lb], 2000.0)
    assert [p["time"] for p in out["equity_curve"]] == keys


def test_a_daily_and_an_hourly_group_backtest_combines_by_close():
    hourly = _hourly(start="2023-03-01", days=120, seed=31)
    g = og._pair()
    data = g.model_dump(by_alias=True)
    data["nodes"]["b_t"]["params"]["interval"] = "1h"
    g = Graph.model_validate(data)
    frames = {("AAPL", "1d"): og.AAPL, ("MSFT", "1h"): hourly}
    resp, _cook = run_graph_backtest_cooked(og._req(g), frames=frames)
    times = [p["time"] for p in resp.combined.equity_curve]
    assert all(isinstance(t, int) for t in times) and times == sorted(set(times))
    finals = sum(gr.summary["final_value"] for gr in resp.groups)
    assert resp.combined.summary["final_value"] == round(finals, 2)
    assert resp.combined.equity_curve[-1]["value"] == pytest.approx(finals, abs=0.02)


# ---------------------------------------------------------------------------
# KA-2: the inspector cooks only the window's groups
# ---------------------------------------------------------------------------


def test_the_inspector_never_cooks_another_groups_ticker_on_the_window_bars():
    g = og._pair()
    cook = cook_graph_window(g, ticker="AAPL", start=og.START, end=og.END, interval="1d",
                             source="yahoo", df=og.AAPL, refs={})
    ids = {s.node_id for s in cook.program.steps}
    assert "a_t" in ids and not any(i.startswith("b_") for i in ids)
    assert "b_t" not in cook.result.streams
    np.testing.assert_array_equal(cook.result.column("a_t", "@close"), og.AAPL["Close"].to_numpy())
    msft = cook_graph_window(g, ticker="MSFT", start=og.START, end=og.END, interval="1d",
                             source="yahoo", df=og.MSFT, refs={})
    np.testing.assert_array_equal(msft.result.column("b_t", "@close"), og.MSFT["Close"].to_numpy())
    assert "a_t" not in msft.result.streams


# ---------------------------------------------------------------------------
# KA-4: a stray Ticker is never fetched and never fails a run
# ---------------------------------------------------------------------------


def _no_fetch(calls):
    def fake(ticker, start, end, interval, source="yahoo", extended_hours=False):
        calls.append((ticker, interval))
        raise ValueError(f"No data for {ticker}")
    return fake


def test_a_stray_ticker_in_a_grouped_graph_is_not_fetched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(shared, "_fetch", _no_fetch(calls))
    stray = [og._node("stray", "ticker", {"symbol": "AAP", "interval": "1d"}),
             og._node("stray_rsi", "rsi", {"period": 14})]
    # An unclaimed node that reads a group's own Ticker is still cooked.
    side = [og._node("a_sma", "sma", {"period": 5}, "long_leg", name="sma_side")]
    nodes, wires = og._leg("a_", "AAPL", "long_leg", extra=side)
    b_nodes, b_wires = og._leg("b_", "MSFT", "short_leg", lo=60, hi=40)
    g = og._graph([og._group("long_leg", "long"), og._group("short_leg", "short"),
                   *nodes, *b_nodes, *stray],
                  wires + b_wires + [("ws", "stray", "stray_rsi", "in0"),
                                     ("wa", "a_t", "a_sma", "in0")])
    resp, cook = run_graph_backtest_cooked(og._req(g), frames=og.FRAMES)
    assert calls == []
    assert [gr.name for gr in resp.groups] == ["long_leg", "short_leg"]
    assert "a_sma" in cook.result.streams
    assert "stray" not in cook.result.streams and "stray_rsi" not in cook.result.streams
    # The same graph without the stray nodes gives the same results.
    clean, _c = run_graph_backtest_cooked(
        og._req(og._graph([og._group("long_leg", "long"), og._group("short_leg", "short"),
                           *nodes, *b_nodes], wires + b_wires + [("wa", "a_t", "a_sma", "in0")])),
        frames=og.FRAMES)
    assert resp.combined.summary == clean.combined.summary
    # The inspector's window cook does not fetch it either.
    cook_graph_window(g, ticker="AAPL", start=og.START, end=og.END, interval="1d",
                      source="yahoo", df=og.AAPL)
    assert calls == []


def test_a_stray_prefixed_ticker_in_a_graph_with_no_group_is_not_fetched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(shared, "_fetch", _no_fetch(calls))
    nodes, wires = og._leg("", "AAPL")
    nodes.append(og._node("stray", "ticker", {"symbol": "SPYX", "interval": "1d",
                                              "prefix": "spyx"}))
    resp = run_graph_backtest(og._req(og._graph(nodes, wires)), df=og.AAPL)
    plain = run_graph_backtest(og._req(og._graph(nodes[:-1], wires)), df=og.AAPL)
    assert calls == []
    assert resp.summary == plain.summary and resp.trades == plain.trades


# ---------------------------------------------------------------------------
# KA-7: a settings node no group's scope reaches warns
# ---------------------------------------------------------------------------


def test_a_setting_in_a_subnet_inside_a_group_warns_setting_unscoped():
    extra = [og._node("a_net", "subnet", {}, "long_leg", name="net"),
             og._node("a_slip", "slippage", {"bps": 9.0}, "a_net", name="slip")]
    result = check_graph(og._pair(a_extra=extra))
    assert result.program is not None
    warns = [(d.code, d.node_id, d.severity) for d in result.diagnostics
             if d.code == "setting_unscoped"]
    assert warns == [("setting_unscoped", "a_slip", "warning")]
    assert "slippage_bps" not in result.program.groups[0].plan.fields
    # A setting in the group itself, or at the root, applies: no warning.
    ok = og._pair(a_extra=[og._node("a_slip", "slippage", {"bps": 9.0}, "long_leg")],
                  root=[og._node("r_slip", "slippage", {"bps": 4.0})])
    assert "setting_unscoped" not in [d.code for d in check_graph(ok).diagnostics]


# ---------------------------------------------------------------------------
# KA-8: bypassing a Subnet switches off everything inside it
# ---------------------------------------------------------------------------


def _subnet_stop(bypass: bool) -> Graph:
    nodes, wires = og._leg("", "AAPL")
    nodes += [og._node("net", "subnet", {}, bypass=bypass),
              og._node("stop_in", "stop", {"constant": 5.0}, "net", name="stop"),
              og._node("slip_in", "slippage", {"bps": 7.0}, "net", name="slip")]
    return og._graph(nodes, wires)


def test_a_bypassed_subnet_drops_the_terminals_and_settings_inside_it():
    live = nb_compile(_subnet_stop(False)).groups[0].plan.fields
    assert live["stop_loss_pct"] == 5.0 and live["slippage_bps"] == 7.0
    off = nb_compile(_subnet_stop(True)).groups[0].plan.fields
    assert "stop_loss_pct" not in off and "slippage_bps" not in off
    flat = flatten(_subnet_stop(True))
    assert "stop_in" not in flat.graph.nodes and "slip_in" not in flat.graph.nodes
    assert "rsi" in flat.graph.nodes


# ---------------------------------------------------------------------------
# DI-05: plan codes ticker_missing and group_duplicate_terminal
# ---------------------------------------------------------------------------


def test_a_group_with_no_ticker_is_ticker_missing():
    g = og._pair().model_copy(deep=True)
    g.nodes["short_leg"].params["ticker"] = ""
    diags = [(d.code, d.node_id, d.param) for d in check_graph(g).diagnostics]
    assert ("ticker_missing", "short_leg", "ticker") in diags


def test_two_entries_in_a_group_flag_both_with_group_duplicate_terminal():
    extra = [og._node("a_entry2", "entry", {}, "long_leg", name="entry2")]
    nodes, wires = og._leg("a_", "AAPL", "long_leg", extra=extra)
    g = og._graph([og._group("long_leg", "long"), *nodes],
                  wires + [("w9", "a_lo", "a_entry2", "in0")])
    dups = [d for d in check_graph(g).diagnostics if d.code == "group_duplicate_terminal"]
    assert {d.node_id for d in dups} == {"a_entry", "a_entry2"}
    assert all("a_entry2" in d.message and "a_entry" in d.message for d in dups)


def test_two_time_stops_in_a_group_are_group_duplicate_terminal():
    extra = [og._node("a_ts1", "time_stop", {"max_bars": 3}, "long_leg"),
             og._node("a_ts2", "time_stop", {"max_bars": 4}, "long_leg")]
    diags = [(d.code, d.node_id) for d in check_graph(og._pair(a_extra=extra)).diagnostics]
    assert ("group_duplicate_terminal", "a_ts2") in diags
    # A graph with no group keeps duplicate_terminal.
    nodes, wires = og._leg("", "AAPL", extra=[og._node("ts1", "time_stop", {"max_bars": 3}),
                                              og._node("ts2", "time_stop", {"max_bars": 4})])
    with pytest.raises(GraphValidationError) as info:
        nb_compile(og._graph(nodes, wires))
    assert info.value.code == "duplicate_terminal"


# ---------------------------------------------------------------------------
# FE-04 (backend): capital weight 0
# ---------------------------------------------------------------------------


def test_a_weight_zero_group_compiles_warns_and_does_not_trade():
    g = og._pair(weight_b=0)
    result = check_graph(g)
    assert result.program is not None
    assert ("group_weight_zero", "short_leg", "warning") in [
        (d.code, d.node_id, d.severity) for d in result.diagnostics]
    resp, _cook = run_graph_backtest_cooked(og._req(g), frames=og.FRAMES)
    assert [gr.name for gr in resp.groups] == ["long_leg"]
    assert resp.groups[0].capital == 10000.0
    alone = og._standalone("s_", "AAPL", "long", 10000.0, 38, 62, og.AAPL)
    assert resp.combined.summary["final_value"] == alone.summary["final_value"]
    assert resp.summary["final_value"] == alone.summary["final_value"]


def test_every_group_at_weight_zero_is_refused_and_a_negative_weight_too():
    codes = [(d.code, d.param) for d in check_graph(og._pair(weight_a=0, weight_b=0)).diagnostics
             if d.severity == "error"]
    assert codes == [("param_invalid", "capital_weight")]
    codes = [(d.code, d.node_id) for d in check_graph(og._pair(weight_a=-1)).diagnostics]
    assert ("param_invalid", "long_leg") in codes


# ---------------------------------------------------------------------------
# DI-01 / DI-02 / DI-03 / DI-09: a rule strategy opened as a graph
# ---------------------------------------------------------------------------


def _sidebar_run(req: StrategyRequest, df: pd.DataFrame) -> dict:
    """Run auto_render(req) as graph view does: the request carries only the
    sidebar fields (ownership.ts GRAPH_OWNED_FIELDS are deleted, and with
    Output Groups the direction too); everything else must come from the
    graph."""
    graph = auto_render(req)
    grouped = any(n.type == "output_group" for n in graph.nodes.values())
    extra = {} if grouped else {"direction": req.direction}
    greq = GraphBacktestRequest(
        graph=graph, ticker=req.ticker, start=req.start, end=req.end, interval=req.interval,
        source=req.source, initial_capital=req.initial_capital,
        dynamic_sizing=req.dynamic_sizing, skip_after_stop=req.skip_after_stop,
        trading_hours=req.trading_hours, **extra)
    result = run_graph_backtest(greq, df=df)
    return {"summary": result.summary, "trades": result.trades,
            "equity_curve": result.equity_curve, "baseline_curve": result.baseline_curve}


def _differs(a: dict, b: dict) -> bool:
    return (a["summary"]["final_value"] != b["summary"]["final_value"]
            or [t["date"] for t in a["trades"]] != [t["date"] for t in b["trades"]])


_SIMPLE = dict(parity._STRATEGIES)["simple_short_rsi"]

_NOREGIME = {
    # DI-02: max_bars_held and borrow_rate_annual (a short pays borrow).
    "time_stop_and_borrow": dict(max_bars_held=4, borrow_rate_annual=6.0),
    "everything": dict(stop_loss_pct=2.0, trailing_stop=TrailingStopConfig(type="pct", value=3.0),
                       max_bars_held=6, borrow_rate_annual=3.0, position_size=0.5,
                       slippage_bps=6.0, per_share_rate=0.005, min_per_order=1.0),
    # DI-09: per-direction lists and values with the regime off are not read.
    "per_direction_lists_ignored": dict(
        long_buy_rules=[Rule(indicator="rsi", condition="below", value=25)],
        short_buy_rules=[Rule(indicator="rsi", condition="above", value=80)],
        long_stop_loss_pct=1.0, short_max_bars_held=2, max_bars_held=5),
}


@pytest.mark.parametrize("case", list(_NOREGIME))
def test_a_rule_strategy_with_no_regime_runs_the_same_as_a_graph(case):
    df = parity._load_df("simple_short_rsi")
    req = _SIMPLE.model_copy(update=_NOREGIME[case])
    rule = parity._run_rule_path(req, df)
    parity._compare_results(rule, _sidebar_run(req, df), case)
    assert rule["trades"], case
    if case != "per_direction_lists_ignored":
        # The values matter: without them the rule backtest trades otherwise.
        assert _differs(rule, parity._run_rule_path(_SIMPLE, df)), case


_REGIME_CASES = {
    # DI-01: the global values under a regime (the fallback for both sides).
    "global_stop_trail_time": dict(stop_loss_pct=4.0, max_bars_held=8,
                                   trailing_stop=TrailingStopConfig(type="pct", value=6.0)),
    # DI-03 / B25: per-side size, stop, trailing stop and time stop, with
    # the shared values as the other side's fallback.
    "per_side_everything": dict(
        stop_loss_pct=5.0, max_bars_held=12, position_size=0.9,
        trailing_stop=TrailingStopConfig(type="pct", value=8.0, activate_on_profit=True,
                                         activate_pct=1.0),
        long_position_size=0.6, short_position_size=0.4,
        long_stop_loss_pct=3.0, short_max_bars_held=5,
        short_trailing_stop=TrailingStopConfig(type="pct", value=2.5),
        borrow_rate_annual=4.0),
    # A per-side ATR trailing stop needs the ATR column on the frame.
    "per_side_atr_trail": dict(long_trailing_stop=TrailingStopConfig(type="atr", value=2.0),
                               long_max_bars_held=7),
}


@pytest.mark.parametrize("case", list(_REGIME_CASES))
def test_a_regime_rule_strategy_runs_the_same_as_a_graph(case, monkeypatch):
    monkeypatch.setattr(shared, "_fetch", rt._pickled_fetch([]))
    df = rt._FULL.loc[pd.Timestamp(rt._START, tz=rt._FULL.index.tz):]
    base = StrategyRequest(ticker="AAPL", start=rt._START, end="2024-01-01", interval="1d",
                           source="yahoo", buy_rules=[], sell_rules=[],
                           regime=rt._FIRING["legacy_sma50"], **rt._SIDES)
    req = base.model_copy(update=_REGIME_CASES[case])
    graph = auto_render(req)
    program = nb_compile(graph)   # DI-03: every terminal is inside the group
    [group] = program.groups
    assert group.direction == "regime_switch"
    rule = parity._run_rule_path(req, df)
    parity._compare_results(rule, _sidebar_run(req, df), case)
    assert {"buy", "short"} <= {t["type"] for t in rule["trades"]}
    assert _differs(rule, parity._run_rule_path(base, df)), case


def test_the_regime_render_puts_every_terminal_in_the_group_and_settings_at_the_root():
    req = StrategyRequest(ticker="AAPL", start=rt._START, end="2024-01-01", interval="1d",
                          buy_rules=[], sell_rules=[], regime=rt._FIRING["legacy_sma50"],
                          **rt._SIDES, **_REGIME_CASES["per_side_everything"])
    g = auto_render(req)
    for node in g.nodes.values():
        if node.type in sb.SETTING_TYPES:
            assert node.parent is None, node.id
        if node.type in ("size", "stop", "trailing_stop", "time_stop"):
            assert node.parent == "/main", node.id
    sides = {(n.type, n.params.get("side")) for n in g.nodes.values()
             if n.type in ("size", "stop", "trailing_stop", "time_stop")}
    assert sides == {("size", "long"), ("size", "short"), ("stop", "long"),
                     ("trailing_stop", None), ("trailing_stop", "short"),
                     ("time_stop", None), ("time_stop", "short")}
    plan = nb_compile(g).groups[0].plan
    assert plan.fields["long_position_size"] == 0.6 and plan.fields["short_max_bars_held"] == 5
    assert plan.fields["stop_loss_pct"] == 5.0 and plan.fields["borrow_rate_annual"] == 4.0
    assert plan.fields["short_trailing_stop"].value == 2.5


def test_borrow_rate_is_drawn_only_when_it_is_not_the_graph_default():
    assert _GRAPH_BORROW_DEFAULT == GraphBacktestRequest.model_fields["borrow_rate_annual"].default
    assert "/setting_borrow_rate" not in auto_render(_SIMPLE).nodes
    g = auto_render(_SIMPLE.model_copy(update={"borrow_rate_annual": 2.0}))
    assert g.nodes["/setting_borrow_rate"].params["rate"] == 2.0


# ---------------------------------------------------------------------------
# Per-side terminals in sim_bridge
# ---------------------------------------------------------------------------


def _switch_graph(**values) -> Graph:
    req = StrategyRequest(ticker="AAPL", start=rt._START, end="2024-01-01", interval="1d",
                          buy_rules=[], sell_rules=[], regime=rt._FIRING["legacy_sma50"],
                          **rt._SIDES, **values)
    return auto_render(req)


def _with_wired_value(g: Graph, terminal: str) -> Graph:
    data = g.model_dump(by_alias=True)
    rsi = next(nid for nid, n in data["nodes"].items()
               if n["type"] == "rsi" and n.get("parent") == "/main")
    data["nodes"][terminal]["params"]["value"] = data["nodes"][rsi]["params"]["out"]
    data["wires"].append({"id": "w_value", "from": rsi, "to": terminal, "to_port": "in0"})
    return Graph.model_validate(data)


def test_a_wired_per_side_stop_is_refused_by_name():
    g = _with_wired_value(_switch_graph(long_stop_loss_pct=3.0), "/stop_long")
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert (info.value.code, info.value.node_id) == ("param_invalid", "/stop_long")
    assert "constant" in str(info.value)


def test_a_per_side_constant_next_to_a_wired_shared_size_is_refused():
    g = _switch_graph(long_position_size=0.5, short_position_size=0.3)
    data = g.model_dump(by_alias=True)
    data["nodes"]["/size_shared"] = {"id": "/size_shared", "type": "size", "name": "size_shared",
                                     "parent": "/main", "params": {}}
    g = _with_wired_value(Graph.model_validate(data), "/size_shared")
    with pytest.raises(GraphValidationError) as info:
        nb_compile(g)
    assert info.value.code == "param_invalid" and info.value.node_id in ("/size_long", "/size_short")


def test_side_is_not_read_outside_a_regime_switch_group():
    extra = [og._node("a_stop", "stop", {"constant": 4.0, "side": "short"}, "long_leg")]
    plan = nb_compile(og._pair(a_extra=extra)).groups[0].plan
    assert plan.fields["stop_loss_pct"] == 4.0
    assert not any(k.startswith(("long_", "short_")) for k in plan.fields)
    extra.append(og._node("a_stop2", "stop", {"constant": 2.0, "side": "long"}, "long_leg"))
    codes = [(d.code, d.node_id) for d in check_graph(og._pair(a_extra=extra)).diagnostics]
    assert ("group_duplicate_terminal", "a_stop2") in codes


def test_two_long_stops_in_a_regime_switch_group_are_refused():
    g = _switch_graph(long_stop_loss_pct=3.0)
    data = g.model_dump(by_alias=True)
    data["nodes"]["/stop_long2"] = {"id": "/stop_long2", "type": "stop", "name": "stop_long2",
                                    "parent": "/main", "params": {"constant": 1.0, "side": "long"}}
    codes = [(d.code, d.node_id) for d in check_graph(Graph.model_validate(data)).diagnostics]
    assert ("group_duplicate_terminal", "/stop_long2") in codes


def test_a_bad_side_is_refused():
    extra = [og._node("a_ts", "time_stop", {"max_bars": 3, "side": "up"}, "long_leg")]
    codes = [(d.code, d.node_id, d.param) for d in check_graph(og._pair(a_extra=extra)).diagnostics]
    assert ("param_invalid", "a_ts", "side") in codes


def test_a_graph_bot_gets_the_per_side_fields():
    g = _switch_graph(long_stop_loss_pct=3.0, short_position_size=0.4,
                      long_trailing_stop=TrailingStopConfig(type="pct", value=2.0))
    plan = nb_compile(g).groups[0].plan
    cfg = BotConfig(strategy_name="r", symbol="AAPL", interval="1d", buy_rules=[],
                    sell_rules=[], allocated_capital=1000.0, short_stop_loss_pct=9.0,
                    kind="graph", graph=g, graph_direction_mode="regime_switch")
    out = sb.apply_to_bot_config(cfg, plan)
    assert out.long_stop_loss_pct == 3.0 and out.short_position_size == 0.4
    assert out.long_trailing_stop.value == 2.0
    # A per-direction value the graph does not set is cleared, never kept.
    assert out.short_stop_loss_pct is None and out.long_position_size is None
