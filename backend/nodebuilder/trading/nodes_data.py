"""Data nodes: the Ticker (a source of bars).

The cook environment carries the bars.  ``params.env["bars"]`` maps
``@open @high @low @close @volume`` to arrays on the cook's index (the
request's frame, plan D11), and ``params.env["frames"]``, when present, maps
a Ticker's node id to the bars of its own frame (plan D8).

A Ticker with an empty ``prefix`` writes the plain names (``@close``...) plus
``@time`` and ``@index``.  A Ticker with a prefix writes ``@<prefix>_open``,
``@<prefix>_high``, ``@<prefix>_low``, ``@<prefix>_close`` and
``@<prefix>_volume`` (the same order and types, no time or index), so
``@close`` always means the group's primary Ticker.

Which frame a Ticker reads is decided per cook (nodebuilder.trading.align):
a group's primary Ticker reads the cook's own frame; any other Ticker the
group reads is a reference Ticker, fetched on its own symbol and interval,
cooked on its own bars and aligned onto the primary's index.  A prefixed
Ticker never falls back to the cook's frame: without its own bars it fails
the cook, so it can never silently read another symbol's prices.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

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

# A reference Ticker's prefix (S32c): lower case, digits and underscore,
# at most 16 characters, not starting with a digit.
PREFIX_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,15}$")

TICKER_TYPE = "ticker"


def ticker_prefix(params: Mapping[str, Any]) -> str:
    """The Ticker's prefix as stored ("" when unset or not a string)."""
    value = (params or {}).get("prefix")
    return value.strip() if isinstance(value, str) else ""


def bar_names(prefix: str) -> tuple[str, ...]:
    """The five price names a Ticker with *prefix* writes, in BAR_FIELDS order."""
    if not prefix:
        return BAR_FIELDS
    return tuple(f"@{prefix}_{name[1:]}" for name in BAR_FIELDS)


def prefixed_name(prefix: str, name: str) -> str:
    """*name* (one of BAR_FIELDS) as a Ticker with *prefix* writes it."""
    return bar_names(prefix)[BAR_FIELDS.index(name)]


def _ticker_writes(params: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    prefix = ticker_prefix(params)
    if prefix:
        return tuple((name, "float") for name in bar_names(prefix))
    return tuple((name, "float") for name in BAR_FIELDS + ("@time", "@index"))


def _epoch_seconds(index: pd.Index) -> np.ndarray:
    """Bar times as seconds since 1970-01-01 UTC (the bar number when the
    index holds no times)."""
    if isinstance(index, pd.DatetimeIndex):
        epoch = pd.Timestamp("1970-01-01", tz="UTC") if index.tz is not None else pd.Timestamp("1970-01-01")
        return np.asarray((index - epoch).total_seconds(), dtype=float)
    return np.arange(len(index), dtype=float)


def _check_ticker(ctx) -> None:
    raw = ctx.raw.get("prefix", "")
    if raw in (None, ""):
        return
    if not isinstance(raw, str) or not PREFIX_PATTERN.match(raw):
        ctx.fail(
            "param_invalid",
            f"Ticker {ctx.node_id!r} has prefix {raw!r}.  A prefix is lower-case letters, "
            "digits and underscore (up to 16, not starting with a digit), for example spy.",
            param="prefix",
        )


def _ticker(inputs, params):
    """Put the bars on the stream: @open @high @low @close @volume, plus
    @time and @index (built only if something reads them).  A prefixed
    Ticker writes the five prefixed names only."""
    prefix = ticker_prefix(params)
    frames = params.env.get("frames") or {}
    bars = frames.get(params.node_id)
    if bars is None:
        if prefix:
            raise ValueError(
                f"Ticker {params.node_id!r} ({params.get('symbol')} {params.get('interval')}, "
                f"prefix {prefix}) has no bars of its own in this cook.  A reference "
                "Ticker reads its own symbol's bars, which must be fetched for it."
            )
        bars = params.env.get("bars")
    if bars is None:
        raise ValueError("the cook has no bars for the Ticker")
    out = inputs
    for field, name in zip(BAR_FIELDS, bar_names(prefix)):
        out = out.with_point(name, bars[field], params.node_id, "float")
    if prefix:
        return out
    index = inputs.store.index
    out = out.with_lazy_point("@time", lambda: _epoch_seconds(index), "float", params.node_id)
    out = out.with_lazy_point(
        "@index", lambda: np.arange(len(index), dtype=float), "float", params.node_id
    )
    return out


register_node(
    name=TICKER_TYPE,
    cat="ticker",
    desc="Market data source: OHLCV price series for a symbol.",
    params=(
        ParamSpec("symbol", "string", "symbol", "AAPL", code_able=False),
        ParamSpec("interval", "select", "interval", "1d", options=INTERVAL_OPTIONS, code_able=False),
        ParamSpec("prefix", "string", "prefix", "", code_able=False),
    ),
    inputs=NO_INPUTS,
    impl=_ticker,
    check=_check_ticker,
    fixed_writes=tuple((name, "float") for name in BAR_FIELDS + ("@time", "@index")),
    writes_for=_ticker_writes,
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
