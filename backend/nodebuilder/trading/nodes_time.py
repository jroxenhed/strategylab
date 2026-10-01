"""Time nodes: time_of_day, day_of_week, session_bar.

They read the bar times of the cook's index, as New York wall-clock time
(America/New_York, the clock of the chart's toET() and of the time_range
contract, plan 4.3):

- A time-zone-aware index is converted to New York time.
- A naive index is read as UTC, as backend/shared.py does, except an index
  whose times are all midnight: that is a list of dates (daily bars), and
  dates are read as they stand.

A bar is stamped with its start time.

- time_of_day: True when the bar starts inside ``range``, ``"HH:MM-HH:MM"``,
  from included, to excluded.  Empty means the regular session,
  09:30-16:00, the same test as the regular-hours filter in shared.py.
  Daily bars start at 00:00, so they are outside any range that does not
  include midnight.
- day_of_week: True on the picked weekdays.  No days picked: never true.
- session_bar: the bar's number in the regular session of its day (0 for
  the first bar at or after 09:30).  NaN outside 09:30-16:00, so daily bars
  are NaN.

time_of_day and day_of_week are False on bar 0, like every other signal
node (the rule engine's ``i < 1`` guard).

Each node's one input is optional: on its own it is a source; wired, the
stream it gets flows on with the signal added.
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.trading.nodes_compare import first_bar_false

NY = "America/New_York"

# The regular session, in minutes after midnight.
SESSION_OPEN = 9 * 60 + 30
SESSION_CLOSE = 16 * 60

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")
# pandas numbers the days Monday = 0.
_DAY_NUMBER = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4}

OPTIONAL_INPUT = PortsSpec(ports=(PortSpec("in", optional=True),), dynamic=False, min=0, max=1)

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ---------------------------------------------------------------------------
# The bar clock
# ---------------------------------------------------------------------------


def ny_times(index: pd.Index) -> pd.DatetimeIndex:
    """The bar times as New York wall-clock times (see the module notes)."""
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("time nodes need bar times: the data has no dates on its rows")
    if index.tz is not None:
        return index.tz_convert(NY)
    if len(index) == 0 or (index == index.normalize()).all():
        return index
    return index.tz_localize("UTC").tz_convert(NY)


def _clock(inputs, params) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(minutes after midnight, weekday, day number) for every bar, once per cook."""
    memo = params.env.get("memo") if params.env else None
    key = ("nodes_time.clock",)
    if memo is not None and key in memo:
        return memo[key]
    times = ny_times(inputs.store.index)
    minutes = np.asarray(times.hour * 60 + times.minute, dtype=np.int64)
    weekday = np.asarray(times.dayofweek, dtype=np.int64)
    days = pd.factorize(times.normalize())[0]
    clock = (minutes, weekday, np.asarray(days, dtype=np.int64))
    if memo is not None:
        memo[key] = clock
    return clock


# ---------------------------------------------------------------------------
# time_of_day
# ---------------------------------------------------------------------------


def parse_range(value: Any) -> tuple[int, int] | None:
    """``"09:35-15:55"`` as (575, 955); None when empty; ValueError when it
    is not a valid range (from must come before to)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{value!r} is not text")
    parts = value.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"{value!r} is not HH:MM-HH:MM")
    ends = []
    for part in parts:
        m = _HHMM.match(part.strip())
        if m is None:
            raise ValueError(f"{part.strip()!r} is not HH:MM")
        ends.append(int(m.group(1)) * 60 + int(m.group(2)))
    if ends[0] >= ends[1]:
        raise ValueError(f"{value!r} ends before it starts")
    return ends[0], ends[1]


def _check_time(ctx) -> None:
    try:
        span = parse_range(ctx.params.get("range"))
    except ValueError as exc:
        ctx.fail("param_invalid",
                 f"time_of_day {ctx.node_id!r} range: {exc}.  Use HH:MM-HH:MM in New York "
                 f"time, from before to, for example 09:35-15:55.", param="range")
    ctx.params["range"] = span if span is not None else (SESSION_OPEN, SESSION_CLOSE)


def _time_of_day(inputs, p):
    start, end = p["range"]
    minutes, _weekday, _days = _clock(inputs, p)
    result = (minutes >= start) & (minutes < end)
    # Bar 0 is False, as for every other signal node (the rule engine's
    # i < 1 guard), so a time node wired straight into Entry cannot fire there.
    return inputs.with_point(p["out"], first_bar_false(result), p.node_id, "bool")


register_node(
    name="time_of_day", cat="data",
    desc="True when the bar starts inside the time range (New York time, from included, "
         "to excluded). Empty: the regular session 09:30-16:00.",
    params=(
        ParamSpec("range", "time_range", "range", None, optional=True),
        ParamSpec("out", "write", "out", "@in_time", dtype="bool"),
    ),
    inputs=OPTIONAL_INPUT, impl=_time_of_day, check=_check_time,
    subtitle="session", ins=1, outs=1, module=__name__,
)


# ---------------------------------------------------------------------------
# day_of_week
# ---------------------------------------------------------------------------


def parse_days(value: Any) -> list[str]:
    """The picked days as a list (``["mon", "fri"]`` or ``"mon, fri"``).
    ValueError on a day that is not one of WEEKDAYS."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [v for v in re.split(r"[\s,]+", value) if v]
    elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        items = list(value)
    else:
        raise ValueError(f"{value!r} is not a list of days")
    bad = [v for v in items if v not in _DAY_NUMBER]
    if bad:
        raise ValueError(f"unknown day {bad[0]!r}; use {list(WEEKDAYS)}")
    return [d for d in WEEKDAYS if d in items]


def _check_days(ctx) -> None:
    try:
        ctx.params["days"] = parse_days(ctx.params.get("days"))
    except ValueError as exc:
        ctx.fail("param_invalid", f"day_of_week {ctx.node_id!r} days: {exc}.", param="days")


def _day_of_week(inputs, p):
    _minutes, weekday, _days = _clock(inputs, p)
    picked = [_DAY_NUMBER[d] for d in p["days"]]
    result = np.isin(weekday, picked)
    return inputs.with_point(p["out"], first_bar_false(result), p.node_id, "bool")


register_node(
    name="day_of_week", cat="data",
    desc="True on the picked weekdays (New York date). No days picked: never true.",
    params=(
        ParamSpec("days", "select", "days", list(WEEKDAYS), options=WEEKDAYS),
        ParamSpec("out", "write", "out", "@on_day", dtype="bool"),
    ),
    inputs=OPTIONAL_INPUT, impl=_day_of_week, check=_check_days,
    subtitle="M T W T F", ins=1, outs=1, module=__name__,
)


# ---------------------------------------------------------------------------
# session_bar
# ---------------------------------------------------------------------------


def _session_bar(inputs, p):
    minutes, _weekday, days = _clock(inputs, p)
    in_session = (minutes >= SESSION_OPEN) & (minutes < SESSION_CLOSE)
    count = pd.Series(in_session.astype(np.int64)).groupby(days).cumsum().to_numpy()
    result = np.where(in_session, count - 1, np.nan).astype(np.float64)
    return inputs.with_point(p["out"], result, p.node_id, "float")


register_node(
    name="session_bar", cat="data",
    desc="The bar's number in its day's regular session (0 = the first bar at or after "
         "09:30 New York time). NaN outside 09:30-16:00 and on daily bars.",
    params=(ParamSpec("out", "write", "out", "@session_bar", dtype="float"),),
    inputs=OPTIONAL_INPUT, impl=_session_bar,
    subtitle="bar #", ins=1, outs=1,
    # The count restarts each day, so a cook must start at or before the
    # latest session open to give the right number (see the 2.C report).
    meta={"needs_session_start": True},
    module=__name__,
)
