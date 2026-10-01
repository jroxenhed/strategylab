"""Data nodes: the Ticker (a source of bars).

The cook environment carries the bars: ``params.env["bars"]`` maps
``@open @high @low @close @volume`` to arrays on the cook's index.  Before
W5 every Ticker node reads the request's one frame (the sidebar's symbol and
interval, plan D11); reference tickers arrive in W5.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nodebuilder.kernel.registry import NO_INPUTS, ParamSpec, register_node

# Ticker choices.  The sources are the data providers backend/shared.py can
# register (no polygon: there is no such provider).  Since W2 the data
# source is set by the sidebar or the spawn dialog, not by the Ticker node
# (plan D11); the list stays for the frontend.
INTERVAL_OPTIONS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo")
SOURCE_OPTIONS: tuple[str, ...] = ("yahoo", "alpaca", "alpaca-iex", "ibkr")

# The price fields a Ticker writes, in order.
BAR_FIELDS: tuple[str, ...] = ("@open", "@high", "@low", "@close", "@volume")


def _epoch_seconds(index: pd.Index) -> np.ndarray:
    """Bar times as seconds since 1970-01-01 UTC (the bar number when the
    index holds no times)."""
    if isinstance(index, pd.DatetimeIndex):
        epoch = pd.Timestamp("1970-01-01", tz="UTC") if index.tz is not None else pd.Timestamp("1970-01-01")
        return np.asarray((index - epoch).total_seconds(), dtype=float)
    return np.arange(len(index), dtype=float)


def _ticker(inputs, params):
    """Put the bars on the stream: @open @high @low @close @volume, plus
    @time and @index, which are built only if something reads them."""
    bars = params.env.get("bars")
    if bars is None:
        raise ValueError("the cook has no bars for the Ticker")
    out = inputs
    for name in BAR_FIELDS:
        out = out.with_point(name, bars[name], params.node_id, "float")
    index = inputs.store.index
    out = out.with_lazy_point("@time", lambda: _epoch_seconds(index), "float", params.node_id)
    out = out.with_lazy_point(
        "@index", lambda: np.arange(len(index), dtype=float), "float", params.node_id
    )
    return out


register_node(
    name="ticker",
    cat="ticker",
    desc="Market data source: OHLCV price series for a symbol.",
    params=(
        ParamSpec("symbol", "string", "symbol", "AAPL", code_able=False),
        ParamSpec("interval", "select", "interval", "1d", options=INTERVAL_OPTIONS, code_able=False),
    ),
    inputs=NO_INPUTS,
    impl=_ticker,
    fixed_writes=tuple((name, "float") for name in BAR_FIELDS + ("@time", "@index")),
    primary="@close",
    # v1/v2 wires out of a Ticker named the field they carried.
    legacy_reads={name: name for name in BAR_FIELDS},
    # Bypassing a source means nothing; the Ticker always gives its bars.
    bypassable=False,
    reads=(),
    ins=0,
    outs=5,
    module=__name__,
)
