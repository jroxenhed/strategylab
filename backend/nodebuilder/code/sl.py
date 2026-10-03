"""``sl``: the StrategyLab helpers user code can call (design note 4.5).

Rule: every helper runs the same backend function the matching node runs,
and never writes its own formula.  Each one builds a one-off stream holding
its inputs and calls the registered node type's own ``impl`` (the code the
cook runs), so ``sl.rsi(@close, 14)`` equals the RSI node's ``@rsi`` bar
for bar, NaN warmup included.  The indicator nodes reach
``indicators.compute_instance`` through their adapter in
``trading/nodes_indicators.py``; so do these helpers.

Defaults equal each node's catalog defaults.  ``sl.rsi``'s ``type`` default
is read from the RSI node's catalog at call time, not guessed.

Inputs are pandas Series on the cook's bar index (a numpy array or a list
works too), or scalars where noted.  Outputs are Series on the same index.

The node modules are imported on first use, never when this module loads:
``nodebuilder.trading`` will import this package (the Wrangle node), so a
load-time import would be circular.
"""
from __future__ import annotations

import inspect
from collections import namedtuple
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

__all__ = [
    "sma", "ema", "rsi", "atr", "macd", "bb", "zscore",
    "crosses_above", "crosses_below", "rising", "falling",
    "bars_since", "shift", "rolling",
]

MACD = namedtuple("MACD", ["line", "signal", "hist"])
BB = namedtuple("BB", ["upper", "middle", "lower"])

_NODE_ID = "sl"


# ---------------------------------------------------------------------------
# Plumbing: run one registered node over the given columns
# ---------------------------------------------------------------------------


def _node_type(name: str):
    from nodebuilder.kernel import registry

    nt = registry.get(name)
    if nt is None:
        import nodebuilder.trading  # noqa: F401  (registers the node types)

        nt = registry.get(name)
    if nt is None:  # pragma: no cover (a broken install)
        raise RuntimeError(f"node type {name!r} is not registered")
    return nt


def _catalog_default(node: str, param: str) -> Any:
    spec = _node_type(node).param(param)
    return spec.default if spec is not None else None


def _as_column(fn: str, arg: str, value: Any) -> tuple[Optional[pd.Index], np.ndarray]:
    """(index or None, 1-D numpy column) for one input."""
    index = None
    if isinstance(value, pd.DataFrame):
        raise TypeError(f"sl.{fn}: {arg} must be one column (a Series), not a DataFrame")
    if isinstance(value, pd.Series):
        index = value.index
        if value.dtype == bool:
            return index, value.to_numpy(dtype=bool)
        try:
            return index, value.to_numpy(dtype=np.float64, na_value=np.nan)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"sl.{fn}: {arg} must hold numbers or true/false values") from exc
    arr = np.asarray(value)
    if arr.ndim != 1:
        raise TypeError(f"sl.{fn}: {arg} must be a Series or a 1-D array, got shape {arr.shape}")
    if arr.dtype == np.bool_:
        return None, arr
    try:
        return None, np.asarray(arr, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"sl.{fn}: {arg} must hold numbers or true/false values") from exc


def _columns(fn: str, **named: Any) -> tuple[pd.Index, dict[str, np.ndarray]]:
    """The shared bar index and one column per named input."""
    index: Optional[pd.Index] = None
    length: Optional[int] = None
    cols: dict[str, np.ndarray] = {}
    for arg, value in named.items():
        idx, col = _as_column(fn, arg, value)
        if idx is not None:
            if index is None:
                index = idx
            elif not (idx is index or idx.equals(index)):
                raise ValueError(f"sl.{fn}: the inputs must share one bar index")
        if length is not None and len(col) != length:
            raise ValueError(f"sl.{fn}: the inputs must have the same length")
        length = len(col)
        cols[arg] = col
    if index is None:
        index = pd.RangeIndex(length or 0)
    elif len(index) != length:
        raise ValueError(f"sl.{fn}: the inputs must have the same length")
    return index, cols


def _run_node(node: str, index: pd.Index, columns: Mapping[str, np.ndarray],
              params: Mapping[str, Any], outs: Sequence[str]) -> tuple[pd.Series, ...]:
    """Run the registered *node* impl on a stream holding *columns* and
    return the write params *outs* as Series on *index*.

    *columns* maps an attribute name (``@x``) to its values; *params* holds
    the node's params, with read params naming those attributes and write
    params naming where the results go.
    """
    from nodebuilder.kernel.schema import Params
    from nodebuilder.kernel.stream import ColumnStore, Stream

    nt = _node_type(node)
    store = ColumnStore(index)
    stream = Stream.empty(store)
    for name, col in columns.items():
        stream = stream.with_point(name, col, None)
    full = {p.name: p.default for p in nt.params}
    full.update(params)
    out = nt.impl(stream, Params(full, _NODE_ID, {}))
    # The impl built fresh arrays in a throwaway store: no copy needed.
    return tuple(pd.Series(out.column(full[o]), index=index, copy=False) for o in outs)


def _whole(fn: str, arg: str, value: Any, minimum: Optional[int] = None) -> int:
    """*value* as an int; a fraction or a bool is refused (int() would cut it)."""
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"sl.{fn}: {arg} must be a whole number, got {value!r}")
    if isinstance(value, (int, np.integer)):
        n = int(value)
    elif isinstance(value, (float, np.floating)) and float(value).is_integer():
        n = int(value)
    else:
        raise TypeError(f"sl.{fn}: {arg} must be a whole number, got {value!r}")
    if minimum is not None and n < minimum:
        raise ValueError(f"sl.{fn}: {arg} must be {minimum} or more, got {n}")
    return n


def _number(fn: str, arg: str, value: Any) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise TypeError(f"sl.{fn}: {arg} must be a number, got {value!r}")
    return float(value)


def _choice(fn: str, arg: str, value: Any, options: Sequence[str]) -> str:
    if value not in options:
        raise ValueError(f"sl.{fn}: {arg} must be one of {list(options)}, got {value!r}")
    return value


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def sma(x, period):
    """Simple moving average of x over period bars (the MA node, type sma)."""
    index, cols = _columns("sma", x=x)
    p = _whole("sma", "period", period)
    (out,) = _run_node("ma", index, {"@x": cols["x"]},
                       {"period": p, "type": "sma", "source": "@x", "out": "@out"}, ("out",))
    return out


def ema(x, period):
    """Exponential moving average of x over period bars (the MA node, type ema)."""
    index, cols = _columns("ema", x=x)
    p = _whole("ema", "period", period)
    (out,) = _run_node("ma", index, {"@x": cols["x"]},
                       {"period": p, "type": "ema", "source": "@x", "out": "@out"}, ("out",))
    return out


def rsi(x, period=14, type=None):  # noqa: A002 (the RSI node's param is called type)
    """Relative Strength Index of x (the RSI node).  type is wilder or sma;
    the default is the RSI node's default."""
    from nodebuilder.trading.nodes_indicators import RSI_TYPE_OPTIONS

    index, cols = _columns("rsi", x=x)
    p = _whole("rsi", "period", period)
    kind = _catalog_default("rsi", "type") if type is None else type
    _choice("rsi", "type", kind, RSI_TYPE_OPTIONS)
    (out,) = _run_node("rsi", index, {"@x": cols["x"]},
                       {"period": p, "type": kind, "source": "@x", "out": "@out"}, ("out",))
    return out


def atr(high, low, close, period=14):
    """Average True Range (the ATR node)."""
    index, cols = _columns("atr", high=high, low=low, close=close)
    p = _whole("atr", "period", period)
    (out,) = _run_node("atr", index,
                       {"@h": cols["high"], "@l": cols["low"], "@c": cols["close"]},
                       {"period": p, "source": "@c", "high": "@h", "low": "@l", "out": "@out"},
                       ("out",))
    return out


def macd(x, fast=12, slow=26, signal=9):
    """MACD of x (the MACD node).  Returns (line, signal, hist)."""
    index, cols = _columns("macd", x=x)
    args = {k: _whole("macd", k, v) for k, v in (("fast", fast), ("slow", slow), ("signal", signal))}
    line, sig, hist = _run_node(
        "macd", index, {"@x": cols["x"]},
        {**args, "source": "@x", "out_line": "@l", "out_signal": "@s", "out_hist": "@h"},
        ("out_line", "out_signal", "out_hist"))
    return MACD(line, sig, hist)


def bb(x, period=20, std=2.0):
    """Bollinger Bands of x (the Bollinger node).  Returns (upper, middle, lower)."""
    index, cols = _columns("bb", x=x)
    p = _whole("bb", "period", period)
    upper, middle, lower = _run_node(
        "bollinger", index, {"@x": cols["x"]},
        {"period": p, "stddev": _number("bb", "std", std), "source": "@x",
         "out_upper": "@u", "out_middle": "@m", "out_lower": "@lo"},
        ("out_upper", "out_middle", "out_lower"))
    return BB(upper, middle, lower)


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------


def rolling(x, window, op="mean"):
    """mean, min, max, std or sum of x over the last window bars (the rolling node)."""
    from nodebuilder.trading.nodes_math import ROLLING_OPS

    index, cols = _columns("rolling", x=x)
    w = _whole("rolling", "window", window, 1)
    _choice("rolling", "op", op, ROLLING_OPS)
    (out,) = _run_node("rolling", index, {"@x": cols["x"]},
                       {"a": "@x", "op": op, "window": w, "out": "@out"}, ("out",))
    return out


def zscore(x, window):
    """(x - mean) / std over the last window bars, with the rolling node's
    mean and std, and the math node's sub and div (NaN where std is 0)."""
    index, cols = _columns("zscore", x=x)
    w = _whole("zscore", "window", window, 1)
    col = cols["x"]
    params = {"a": "@x", "window": w}
    (mean,) = _run_node("rolling", index, {"@x": col}, {**params, "op": "mean", "out": "@o"}, ("out",))
    (std,) = _run_node("rolling", index, {"@x": col}, {**params, "op": "std", "out": "@o"}, ("out",))
    (dev,) = _run_node("math", index, {"@x": col, "@m": mean.to_numpy()},
                       {"op": "sub", "a": "@x", "b": "@m", "out": "@o"}, ("out",))
    (z,) = _run_node("math", index, {"@d": dev.to_numpy(), "@s": std.to_numpy()},
                     {"op": "div", "a": "@d", "b": "@s", "out": "@o"}, ("out",))
    return z


def shift(x, n=1):
    """x as it was n bars ago (the shift node).  A negative n would read the
    future, so it raises."""
    if isinstance(n, (int, float, np.integer, np.floating)) and not isinstance(n, (bool, np.bool_)) \
            and n < 0:
        raise ValueError(f"sl.shift: n={n} would read the future; n must be 0 or more")
    bars = _whole("shift", "n", n, 0)
    index, cols = _columns("shift", x=x)
    (out,) = _run_node("shift", index, {"@x": cols["x"]},
                       {"a": "@x", "bars": bars, "out": "@out"}, ("out",))
    return out


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def _crosses(node: str, fn: str, a, b):
    if isinstance(b, (pd.Series, np.ndarray, list, tuple)):
        index, cols = _columns(fn, a=a, b=b)
        columns = {"@a": cols["a"], "@b": cols["b"]}
        params = {"a": "@a", "b": "@b", "threshold": None, "out": "@out"}
    else:
        index, cols = _columns(fn, a=a)
        columns = {"@a": cols["a"]}
        params = {"a": "@a", "b": None, "threshold": _number(fn, "b", b), "out": "@out"}
    (out,) = _run_node(node, index, columns, params, ("out",))
    return out


def crosses_above(a, b):
    """True on the bar where a crosses above b (a Series or a number), as the
    crosses_above node: a[i-1] < b[i-1] and a[i] >= b[i]; bar 0 is False."""
    return _crosses("crosses_above", "crosses_above", a, b)


def crosses_below(a, b):
    """True on the bar where a crosses below b (a Series or a number), as the
    crosses_below node: a[i-1] > b[i-1] and a[i] <= b[i]; bar 0 is False."""
    return _crosses("crosses_below", "crosses_below", a, b)


def _slope(up: bool, fn: str, x, n):
    bars = _whole(fn, "n", n, 0)
    index, cols = _columns(fn, x=x)
    if bars == 1:
        node, params = ("rising" if up else "falling"), {}
    else:
        node, params = ("rising_over" if up else "falling_over"), {"bars": bars}
    (out,) = _run_node(node, index, {"@x": cols["x"]}, {"a": "@x", "out": "@out", **params},
                       ("out",))
    return out


def rising(x, n=1):
    """True when x is higher than n bars ago (the rising / rising_over nodes)."""
    return _slope(True, "rising", x, n)


def falling(x, n=1):
    """True when x is lower than n bars ago (the falling / falling_over nodes)."""
    return _slope(False, "falling", x, n)


def bars_since(cond):
    """Bars since cond was last true: 0 on a true bar, NaN until the first
    true bar.  (No node does this.)  A NaN in cond counts as false."""
    index, cols = _columns("bars_since", cond=cond)
    values = cols["cond"]
    if values.dtype == np.bool_:
        mask = values
    else:
        mask = (values != 0) & ~np.isnan(values)
    pos = np.arange(len(mask))
    last = np.where(mask, pos, -1)
    np.maximum.accumulate(last, out=last)
    out = (pos - last).astype(np.float64)
    out[last < 0] = np.nan
    return pd.Series(out, index=index)


# ---------------------------------------------------------------------------
# What the editor lists (code_capabilities.functions)
# ---------------------------------------------------------------------------

_RETURNS: dict[str, str] = {
    "sma": "series_float", "ema": "series_float", "rsi": "series_float",
    "atr": "series_float", "macd": "tuple(line, signal, hist)",
    "bb": "tuple(upper, middle, lower)", "zscore": "series_float",
    "crosses_above": "series_bool", "crosses_below": "series_bool",
    "rising": "series_bool", "falling": "series_bool",
    "bars_since": "series_float", "shift": "series_float", "rolling": "series_float",
}


def helpers() -> dict[str, Callable[..., Any]]:
    """Every helper, by name."""
    return {name: globals()[name] for name in __all__}


def signature(name: str) -> str:
    """``sl.rsi(x, period=14, type='wilder')``: the signature with the
    catalog default in place of a None that means "the node's default"."""
    fn = helpers()[name]
    parts = []
    for p in inspect.signature(fn).parameters.values():
        if p.default is inspect.Parameter.empty:
            parts.append(p.name)
            continue
        default = p.default
        if name == "rsi" and p.name == "type" and default is None:
            default = _catalog_default("rsi", "type")
        parts.append(f"{p.name}={default!r}")
    return f"sl.{name}({', '.join(parts)})"


def describe() -> list[dict[str, str]]:
    """``code_capabilities.functions``: name, signature, returns, one-line doc."""
    out = []
    for name, fn in helpers().items():
        doc = " ".join((inspect.getdoc(fn) or "").split())
        out.append({"name": f"sl.{name}", "signature": signature(name),
                    "returns": _RETURNS[name], "doc": doc})
    return out
