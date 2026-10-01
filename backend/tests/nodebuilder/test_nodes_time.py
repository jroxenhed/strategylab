"""Time nodes (F435 W2 item 2.C): time_of_day, day_of_week, session_bar.

Bar times are read as New York wall clock.  The tests cover a daylight
saving change (2024-03-10), pre-market and after-hours bars, the same bars
given in New York time, in UTC and as naive UTC, daily bars stamped as
dates, the range edges (from included, to excluded), an empty day list and
bad params.  Synthetic data only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nodebuilder.compile import compile as nb_compile, compile_with_diagnostics
from nodebuilder.evaluator import cook_program, cook_signals
from nodebuilder.models import Graph
from nodebuilder.trading.nodes_time import parse_days, parse_range

NY = "America/New_York"
# Friday before the clocks change, then Monday and Tuesday after.
DAYS = ("2024-03-08", "2024-03-11", "2024-03-12")


def _ny_index() -> pd.DatetimeIndex:
    """5-minute bars from 04:00 to 19:55 New York time on each day."""
    parts = [pd.date_range(f"{d} 04:00", f"{d} 19:55", freq="5min", tz=NY) for d in DAYS]
    return parts[0].append(parts[1:])


def _frame(index: pd.Index) -> pd.DataFrame:
    n = len(index)
    close = 100 + np.sin(np.arange(n) / 7.0)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close,
                         "Volume": np.full(n, 10.0)}, index=index)


def _graph(nodes: dict, wires: list) -> Graph:
    return Graph.model_validate({
        "_version": 2,
        "nodes": {nid: {"id": nid, "type": t, "params": p} for nid, (t, p) in nodes.items()},
        "wires": [{"id": f"w{i}", "from": a, "to": b, "to_port": port}
                  for i, (a, b, port) in enumerate(wires)],
    })


def _time_graph(node_type: str, params: dict) -> Graph:
    """The time node alone (a source) feeding Entry through an AND with a
    comparison, so its column can be read back."""
    out = {"time_of_day": "@in_time", "day_of_week": "@on_day"}.get(node_type)
    nodes = {"/t": ("ticker", {}), "/n": (node_type, params),
             "/c": ("above", {"a": "@close", "threshold": 0, "out": "@pos"}),
             "/e": ("entry", {})}
    wires = [("/t", "/c", "in0")]
    if out is None:   # session_bar writes a number
        nodes["/k"] = ("above", {"a": "@session_bar", "threshold": -1, "out": "@sb"})
        wires += [("/n", "/k", "in0")]
        nodes["/and"] = ("and", {"terms": ["@pos", "@sb"]})
        wires += [("/c", "/and", "in0"), ("/k", "/and", "in1")]
    else:
        nodes["/and"] = ("and", {"terms": ["@pos", out]})
        wires += [("/c", "/and", "in0"), ("/n", "/and", "in1")]
    wires.append(("/and", "/e", "in0"))
    return _graph(nodes, wires)


def _column(index: pd.Index, node_type: str, params: dict, name: str) -> np.ndarray:
    program = nb_compile(_time_graph(node_type, params))
    return cook_program(program, _frame(index), keep={"/n"}).column("/n", name)


def _guard(x) -> np.ndarray:
    """*x* with bar 0 False: time signals carry the bar-0 guard (KC-5)."""
    x = np.array(x, dtype=bool)
    x[0] = False
    return x


def _codes(g: Graph) -> set[str]:
    _program, diags = compile_with_diagnostics(g)
    return {d.code for d in diags}


def _minutes(index: pd.DatetimeIndex) -> np.ndarray:
    ny = index.tz_convert(NY)
    return np.asarray(ny.hour * 60 + ny.minute)


# ---------------------------------------------------------------------------
# time_of_day
# ---------------------------------------------------------------------------


def test_time_of_day_default_is_the_regular_session():
    idx = _ny_index()
    got = _column(idx, "time_of_day", {}, "@in_time")
    m = _minutes(idx)
    np.testing.assert_array_equal(got, (m >= 570) & (m < 960))
    assert got.dtype == bool
    # 78 five-minute bars a day, every day, on both sides of the clock change.
    assert got.sum() == 78 * len(DAYS)


def test_time_of_day_edges():
    idx = _ny_index()
    got = pd.Series(_column(idx, "time_of_day", {"range": "09:35-15:55"}, "@in_time"),
                    index=idx)
    day = got.loc["2024-03-11"]
    assert not day.at[pd.Timestamp("2024-03-11 09:30", tz=NY)]
    assert day.at[pd.Timestamp("2024-03-11 09:35", tz=NY)]      # from is included
    assert day.at[pd.Timestamp("2024-03-11 15:50", tz=NY)]
    assert not day.at[pd.Timestamp("2024-03-11 15:55", tz=NY)]  # to is excluded
    assert day.sum() == 76


@pytest.mark.parametrize("convert", ["utc", "naive_utc"])
def test_time_of_day_same_in_any_time_zone(convert):
    """The same bars in UTC, or naive UTC, give the same New York signal,
    across the clock change (09:30 New York is 14:30 UTC on Friday and 13:30
    UTC on Monday)."""
    idx = _ny_index()
    want = _column(idx, "time_of_day", {}, "@in_time")
    other = idx.tz_convert("UTC")
    if convert == "naive_utc":
        other = other.tz_localize(None)
    got = _column(other, "time_of_day", {}, "@in_time")
    np.testing.assert_array_equal(got, want)


def test_time_of_day_pre_and_after_hours():
    idx = _ny_index()
    got = _column(idx, "time_of_day", {"range": "04:00-09:30"}, "@in_time")
    m = _minutes(idx)
    np.testing.assert_array_equal(got, _guard((m >= 240) & (m < 570)))


def test_time_of_day_on_daily_bars_is_false():
    """Daily bars start at 00:00, outside the session."""
    idx = pd.date_range("2024-01-02", periods=10, freq="B", tz=NY)
    assert not _column(idx, "time_of_day", {}, "@in_time").any()


@pytest.mark.parametrize("value", ["16:00-09:30", "09:30-09:30", "9:5-10:00", "abc",
                                   "09:30-10:00-11:00", "24:00-24:30"])
def test_time_of_day_bad_range_is_refused(value):
    assert "param_invalid" in _codes(_time_graph("time_of_day", {"range": value}))


def test_parse_range():
    assert parse_range("09:35-15:55") == (575, 955)
    assert parse_range(" 09:35 - 15:55 ") == (575, 955)
    assert parse_range(None) is None
    assert parse_range("") is None
    with pytest.raises(ValueError):
        parse_range(930)


def test_time_of_day_in_a_chain_passes_the_stream_on():
    """Wired, the node adds its signal to the stream it gets."""
    idx = _ny_index()
    g = _graph({"/t": ("ticker", {}), "/n": ("time_of_day", {}),
                "/c": ("above", {"a": "@close", "threshold": 100, "out": "@up"}),
                "/and": ("and", {"terms": ["@up", "@in_time"]}), "/e": ("entry", {})},
               [("/t", "/n", "in0"), ("/n", "/c", "in0"), ("/c", "/and", "in0"),
                ("/and", "/e", "in0")])
    result = cook_program(nb_compile(g), _frame(idx), keep={"/and"})
    close = _frame(idx)["Close"].to_numpy()
    m = _minutes(idx)
    want = (close > 100) & (m >= 570) & (m < 960)
    want[0] = False
    np.testing.assert_array_equal(result.column("/and", "@and"), want)


def test_time_nodes_need_bar_times():
    df = _frame(_ny_index()).reset_index(drop=True)
    program = nb_compile(_time_graph("time_of_day", {}))
    with pytest.raises(ValueError, match="bar times"):
        cook_program(program, df)


# ---------------------------------------------------------------------------
# day_of_week
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days,want_days", [
    (["mon", "wed", "fri"], {0, 4}),           # the frame has Fri, Mon, Tue
    (["tue"], {1}),
    ("mon, tue", {0, 1}),
    (None, {0, 1, 4}),                          # default: every weekday
])
def test_day_of_week(days, want_days):
    idx = _ny_index()
    params = {} if days is None else {"days": days}
    got = _column(idx, "day_of_week", params, "@on_day")
    np.testing.assert_array_equal(got, _guard(np.isin(idx.dayofweek, list(want_days))))


def test_day_of_week_empty_is_never_true():
    assert not _column(_ny_index(), "day_of_week", {"days": []}, "@on_day").any()


def test_day_of_week_uses_the_new_york_date():
    """Late-evening New York bars are already the next day in UTC."""
    idx = pd.DatetimeIndex([pd.Timestamp("2024-03-07 10:00", tz=NY),     # bar 0 (guarded)
                            pd.Timestamp("2024-03-08 19:00", tz=NY),     # Fri NY, Sat UTC
                            pd.Timestamp("2024-03-11 09:30", tz=NY)])
    got = _column(idx.tz_convert("UTC"), "day_of_week", {"days": ["fri"]}, "@on_day")
    np.testing.assert_array_equal(got, [False, True, False])


def test_day_of_week_on_naive_dates():
    """Naive daily dates are dates, not UTC midnights (which would be the
    evening before in New York)."""
    idx = pd.date_range("2023-12-29", periods=10, freq="B")   # naive, Fri then Monday
    got = _column(idx, "day_of_week", {"days": ["mon"]}, "@on_day")
    np.testing.assert_array_equal(got, _guard(idx.dayofweek == 0))
    assert got[1]


@pytest.mark.parametrize("days", [["sat"], ["monday"], [1, 2], "mon fri xyz"])
def test_day_of_week_bad_days_are_refused(days):
    assert "param_invalid" in _codes(_time_graph("day_of_week", {"days": days}))


def test_parse_days_keeps_weekday_order():
    assert parse_days(["fri", "mon"]) == ["mon", "fri"]
    assert parse_days("") == []


# ---------------------------------------------------------------------------
# session_bar
# ---------------------------------------------------------------------------


def test_session_bar_counts_from_the_open_each_day():
    idx = _ny_index()
    got = pd.Series(_column(idx, "session_bar", {}, "@session_bar"), index=idx)
    for d in DAYS:
        day = got.loc[d]
        session = day.between_time("09:30", "15:55")
        np.testing.assert_array_equal(session.to_numpy(), np.arange(78, dtype=float))
        outside = day.drop(session.index)
        assert outside.isna().all() and len(outside) > 0


def test_session_bar_with_a_gap_counts_bars_not_minutes():
    idx = pd.DatetimeIndex([pd.Timestamp(f"2024-03-11 {t}", tz=NY)
                            for t in ("09:00", "09:30", "09:35", "10:30", "16:00")])
    got = _column(idx, "session_bar", {}, "@session_bar")
    np.testing.assert_array_equal(got, [np.nan, 0, 1, 2, np.nan])


def test_session_bar_on_daily_bars_is_nan():
    idx = pd.date_range("2024-01-02", periods=5, freq="B", tz=NY)
    assert np.isnan(_column(idx, "session_bar", {}, "@session_bar")).all()


def test_time_node_schema():
    program = nb_compile(_time_graph("session_bar", {}))
    schema = program.stream_schemas_json()["/n"]
    assert [a["name"] for a in schema["points"]] == ["@session_bar"]
    assert schema["points"][0]["dtype"] == "float"


# ---------------------------------------------------------------------------
# Bar-0 guard (F435 W2 KC-5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("node_type,params,name", [
    ("time_of_day", {"range": "09:30-16:00"}, "@in_time"),
    ("day_of_week", {}, "@on_day"),
])
def test_time_signal_straight_into_entry_never_fires_on_bar_0(node_type, params, name):
    """Every other signal node forces bar 0 False (the rule engine's i < 1
    guard); a time node wired straight into Entry must too."""
    idx = pd.date_range("2024-01-02 09:30", periods=20, freq="30min", tz=NY)
    g = _graph({"/n": (node_type, params), "/e": ("entry", {})}, [("/n", "/e", "in0")])
    entry, _exit = cook_signals(nb_compile(g), _frame(idx))
    assert not entry[0]
    assert entry[1:].any()
