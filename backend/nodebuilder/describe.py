"""A strategy graph as plain English text (F454).

describe_graph(graph) reads a graph and says what it does, one short
sentence per line, for a reader who is not an expert:

    Buy SPY (5m) when RSI(14) is below 40.
    Sell when RSI(14) is above 60.
    Stop: 0.5 % below the entry price.

It only reads the graph.  It never fetches data, never cooks and never runs
user code: the graph goes through the same first steps as compile (library
assets expanded, networks flattened, the kernel's read check), and the text
is built by walking up the wires from each terminal.  The kernel check tells
which node writes each attribute a node reads, so a bypassed node is skipped
the way compile skips it and an empty ``a`` or ``signal`` param reads the
wired node, as compile does.

Rule bots get text too: describe_rule_bot draws the bot's rules as a graph
(from_rules.auto_render) and describes that graph; if the rules cannot be
drawn, describe_rules phrases the rules directly.

Used by POST /api/nodebuilder/describe and GET /api/bots/{id}/describe.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from nodebuilder import trading as _trading  # noqa: F401  (registers every node type)
from nodebuilder.kernel import assets as _assets
from nodebuilder.kernel import registry as _registry
from nodebuilder.kernel.flatten import is_locked_instance
from nodebuilder.kernel.schema import DISABLED, LIVE, NONE, PASS
from nodebuilder.models import Graph, expr_text, is_expr_value
from nodebuilder.trading import nodes_groups as _groups
from nodebuilder.trading.nodes_data import ticker_prefix

# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

# A Ticker's fields, as a reader says them.
_FIELD_WORDS = {
    "@open": "open", "@high": "high", "@low": "low", "@close": "close",
    "@volume": "volume", "@time": "time", "@index": "bar number",
}

_COMPARE_WORDS = {
    "above": "is above", "below": "is below",
    "crosses_above": "crosses above", "crosses_below": "crosses below",
}

_MATH_SIGNS = {"add": "+", "sub": "-", "mul": "x", "div": "/"}

_ROLLING_WORDS = {
    "mean": "average", "min": "low", "max": "high", "std": "standard deviation", "sum": "sum",
}

_DAY_WORDS = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri",
              "sat": "Sat", "sun": "Sun"}

_SETTING_TYPES = ("position_size", "stop_loss", "slippage", "commission", "borrow_rate")
_TERMINAL_TYPES = ("entry", "exit", "size", "stop", "trailing_stop", "time_stop", "regime")
_NETWORK_TYPES = ("subnet", "regime_net", "subnet_input", "subnet_output", "output_group")

# The cost settings' values when nothing is set (the catalog defaults).  A
# rule bot's drawn graph always carries them; at these values they say
# nothing, so describe_rule_bot leaves them out.
_DEFAULT_COSTS = {"slippage": {"bps": 2.0},
                  "commission": {"per_share_rate": 0.0, "min_per_order": 0.0},
                  "borrow_rate": {"rate": 0.5}}

# The interval of a Ticker that has a symbol but no interval: what the
# editor run (graphTicker.ts DEFAULT_INTERVAL) and spawn (group_bot_fields) use.
_DEFAULT_INTERVAL = "1d"

_EXPR_MAX = 60       # longest expression text shown in a phrase
_DEPTH_MAX = 40      # deepest phrase walk (a guard; graphs have no cycles)

# The kinds of phrase, for brackets: "A and (B or C)", "(A + B) / 2".
ATOM, COND, AND, OR, XOR, NOT, MATH = "atom", "cond", "and", "or", "xor", "not", "math"


@dataclass(frozen=True)
class _P:
    """One phrase and its kind.  *boolish* marks a true/false signal
    carried as a 0/1 number (a rolling low or high of a signal): "above 0.5"
    of it reads as the signal itself.  *ref* is the label of a reference
    Ticker ("QQQ", "QQQ (1d)") when the phrase is one of its fields."""
    text: str
    kind: str = ATOM
    boolish: bool = False
    ref: Optional[str] = None


def _num(value: Any) -> str:
    """A param value as text: 40, 0.5, "= 10 + 5" for an expression."""
    if is_expr_value(value):
        return _expr(value)
    if value is None:
        return "?"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


def _expr(value: Any) -> str:
    text = " ".join((expr_text(value) or "").split())
    if len(text) > _EXPR_MAX:
        text = text[:_EXPR_MAX - 1] + "…"
    return f"= {text}"


def _pct_of_fraction(value: Any) -> str:
    """A size fraction (1 = all of the capital) as "100 %"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{_num(round(float(value) * 100, 6))} %"
    return f"{_num(value)} (as a share)"


def _bars(n: Any) -> str:
    return f"{_num(n)} bar" if n == 1 else f"{_num(n)} bars"


def _wrap(p: _P, *kinds: str) -> str:
    """The phrase in brackets when its kind is one of *kinds*."""
    return f"({p.text})" if p.kind in kinds else p.text


def _human(type_name: str) -> str:
    return type_name.replace("_", " ")


def _upper(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    return text or None


def _first_sentence_upper(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _is_bypassed(node: Any) -> bool:
    """True when compile skips *node* for its bypass flag.  The kernel only
    honours the flag on a type that can be bypassed (kernel/schema.py):
    on a terminal or a Ticker the flag is ignored and the node still runs."""
    if node is None or not getattr(node, "bypass", False):
        return False
    nt = _registry.get(node.type)
    return nt is None or bool(nt.bypassable)


def _number(value: Any) -> Optional[float]:
    """*value* as a float, or None when it is not a plain number (an
    expression, a missing value)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _plural(n: Any, one: str, many: str) -> str:
    return one if n == 1 else many


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


@dataclass
class GroupText:
    """One group's text (the contract's group object)."""
    name: str
    symbol: Optional[str]
    interval: Optional[str]
    direction: Optional[str]
    entry: Optional[str] = None
    exit: Optional[str] = None
    stops: list[str] = field(default_factory=list)
    sizing: Optional[str] = None
    other: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    weight: float = 1.0

    def to_json(self) -> dict[str, Any]:
        # The contract names a regime_switch group's direction "long_short".
        direction = "long_short" if self.direction == "regime_switch" else self.direction
        if direction not in ("long", "short", "long_short"):
            direction = None
        return {"name": self.name, "symbol": self.symbol, "interval": self.interval,
                "direction": direction, "entry": self.entry, "exit": self.exit,
                "stops": list(self.stops), "sizing": self.sizing, "other": list(self.other)}


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


class _Describer:
    """Phrases for one analysed graph.  One instance per describe call."""

    def __init__(self, analysis: Any, flat: Any, graph: Graph,
                 timeframes: Optional[Mapping[str, str]] = None) -> None:
        self.a = analysis
        self.flat = flat
        self.graph = graph                    # the expanded source graph
        self.nodes = analysis.graph.nodes     # the flat nodes
        # Rule bots only: source node id -> the higher timeframe its rule
        # runs on ("1d").  The phrase of that node says "(on 1d bars)".
        self.timeframes = dict(timeframes or {})
        self.warnings: list[str] = []
        self._warned: set[str] = set()
        self._memo: dict[tuple, _P] = {}
        self._stack: list[tuple] = []
        self.primary: Optional[str] = None    # the current group's primary Ticker
        self.interval: Optional[str] = None   # the current group's interval
        # Wires into each flat node, by port number.
        self.wires_in: dict[str, dict[int, str]] = {}
        for wire in analysis.graph.wires:
            port = wire.to_port or "in0"
            try:
                k = int(port[2:]) if port.startswith("in") else 0
            except ValueError:
                k = 0
            self.wires_in.setdefault(wire.to_path, {})[k] = wire.from_path

    # -- helpers ------------------------------------------------------------

    def warn(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.warnings.append(message)

    def name_of(self, fid: str) -> str:
        node = self.nodes.get(fid)
        if node is not None and node.name:
            return node.name
        return self.flat.to_source(fid) or fid

    def params(self, fid: str) -> dict[str, Any]:
        """The node's params over its catalog defaults (raw: an expression
        stays an expression)."""
        node = self.nodes.get(fid)
        if node is None:
            return {}
        nt = _registry.get(node.type)
        out = dict(nt.entry.defaults.get("params", {})) if nt is not None else {}
        out.update(node.params or {})
        return out

    def set_group(self, primary: Optional[str], interval: Optional[str] = None) -> None:
        self.primary = primary
        self.interval = interval
        self._memo.clear()

    # -- reads ----------------------------------------------------------------

    def read(self, fid: str, pname: str) -> Optional[_P]:
        """What param *pname* of node *fid* reads, as a phrase; None when it
        reads nothing (empty and not wired)."""
        raw = (self.nodes[fid].params or {}).get(pname) if fid in self.nodes else None
        if is_expr_value(raw):
            return _P(_expr(raw))
        res = self.a.nodes.get(fid)
        info = res.reads.get(pname) if res is not None else None
        if info is not None and not isinstance(info, list):
            if info.name and info.status in (LIVE, DISABLED):
                p = self.attr(info.writer, info.name)
                return p if info.status == LIVE else _P(f"{p.text} (bypassed)", p.kind)
            if info.status == NONE:
                return None
        return self._fallback_read(fid, pname, raw)

    def read_list(self, fid: str, pname: str) -> list[_P]:
        """The phrases of a list read (AND, OR terms).  A term only a
        bypassed node writes is left out, as compile leaves it out."""
        res = self.a.nodes.get(fid)
        info = res.reads.get(pname) if res is not None else None
        if isinstance(info, list):
            return [self.attr(r.writer, r.name) for r in info
                    if r.status == LIVE and r.name is not None]
        raw = (self.nodes[fid].params or {}).get(pname) if fid in self.nodes else None
        if isinstance(raw, (list, tuple)) and raw:
            return [self.attr(self._find_writer(fid, n), n) for n in raw if isinstance(n, str)]
        node = self.nodes.get(fid)
        nt = _registry.get(node.type) if node is not None else None
        wired = self.wires_in.get(fid, {})
        if nt is None:
            return []
        out = []
        for k in nt.ports_for_param(pname, sorted(wired)):
            src = wired[k]
            out.append(self.attr(src, self._primary_write(src)))
        return out

    def _fallback_read(self, fid: str, pname: str, raw: Any) -> Optional[_P]:
        """A read the kernel did not resolve (the node has an error, or one
        of its inputs has): the param's name, else the wired node's primary
        write, the same default compile uses."""
        if isinstance(raw, str) and raw.startswith("@"):
            return self.attr(self._find_writer(fid, raw), raw)
        node = self.nodes.get(fid)
        nt = _registry.get(node.type) if node is not None else None
        if nt is None:
            return None
        spec = nt.param(pname)
        if spec is not None and isinstance(spec.default, str) and spec.default.startswith("@"):
            return self.attr(self._find_writer(fid, spec.default), spec.default)
        wired = self.wires_in.get(fid, {})
        ports = nt.ports_for_param(pname, sorted(wired))
        if not ports:
            return None
        src = wired[ports[0]]
        return self.attr(src, self._primary_write(src))

    def _writes(self, fid: str) -> tuple[str, ...]:
        res = self.a.nodes.get(fid)
        names = tuple(res.writes) if res is not None and res.node_type is not None else ()
        names += tuple((self.a.write_names.get(fid) or {}).values())
        node = self.nodes.get(fid)
        nt = _registry.get(node.type) if node is not None else None
        if nt is not None:
            names += tuple(n for n, _d in nt.fixed_writes_for(self.params(fid)))
        return names

    def _primary_write(self, fid: str) -> Optional[str]:
        node = self.nodes.get(fid)
        nt = _registry.get(node.type) if node is not None else None
        if nt is None:
            return None
        fixed = nt.primary_for(self.params(fid))
        if fixed is not None:
            return fixed
        names = self.a.write_names.get(fid) or {}
        for spec in nt.write_params():
            if spec.name in names:
                return names[spec.name]
        return None

    def _find_writer(self, fid: str, name: str) -> Optional[str]:
        """The nearest node upstream of *fid* that writes *name*."""
        seen: set[str] = set()
        queue = [src for _k, src in sorted(self.wires_in.get(fid, {}).items())]
        while queue:
            nid = queue.pop(0)
            if nid in seen:
                continue
            seen.add(nid)
            node = self.nodes.get(nid)
            if node is not None and not _is_bypassed(node) and name in self._writes(nid):
                return nid
            queue.extend(src for _k, src in sorted(self.wires_in.get(nid, {}).items()))
        return None

    # -- attributes -------------------------------------------------------------

    def attr(self, writer: Optional[str], name: Optional[str]) -> _P:
        """The phrase for attribute *name* as node *writer* writes it."""
        if name is None:
            return _P("?")
        if writer is None or writer not in self.nodes:
            word = _FIELD_WORDS.get(name, name.lstrip("@").replace("_", " "))
            return _P(word)
        key = (writer, name)
        if key in self._memo:
            return self._memo[key]
        if key in self._stack or len(self._stack) > _DEPTH_MAX:
            return _P(self.name_of(writer))
        self._stack.append(key)
        try:
            p = self._attr(writer, name)
        except Exception:  # noqa: BLE001  (a phrase must never fail the describe)
            p = _P(self.generic(writer))
        finally:
            self._stack.pop()
        tf = self.timeframes.get(self.flat.to_source(writer) or writer)
        if tf and p.text not in ("never", "always"):
            # A rule bot's rule set to another timeframe (from_rules draws it
            # on the bot's own bars here).  describe_rule_bot adds a warning
            # that says how backtests and the live bot treat it.
            p = _P(f"{p.text} (set to {tf} bars)", p.kind, p.boolish, p.ref)
        self._memo[key] = p
        return p

    def _asset_of(self, fid: str) -> Optional[str]:
        """The library asset name when *fid* sits inside a locked asset
        instance (its children come from the library, so the text shows the
        asset, not its insides)."""
        candidates = [self.flat.to_source(fid)]
        seen: set[str] = set()
        current = self.graph.nodes[fid].parent if fid in self.graph.nodes else None
        while current is not None and current not in seen:
            seen.add(current)
            candidates.append(current)
            parent = self.graph.nodes.get(current)
            current = parent.parent if parent is not None else None
        for nid in candidates:
            node = self.graph.nodes.get(nid) if nid is not None else None
            if node is not None and nid != fid and is_locked_instance(node):
                return node.asset_ref.name
        return None

    def code_phrase(self, fid: str) -> _P:
        name = self.name_of(fid)
        self.warn(f"code:{fid}", f"Node '{name}' runs Python code; the text shows only its name.")
        return _P(f"custom Python (node '{name}')")

    def _attr(self, writer: str, name: str) -> _P:
        asset = self._asset_of(writer)
        if asset is not None:
            return _P(asset)
        node = self.nodes[writer]
        res = self.a.nodes.get(writer)
        if node.type == "wrangle":
            return self.code_phrase(writer)
        if res is not None and res.extra is not None and name in {n for n, _d in res.extra.writes}:
            return self.code_phrase(writer)
        if isinstance(getattr(node, "code", None), str) and node.code.strip():
            self.code_phrase(writer)  # the warning; the node's own phrase follows
        names = (res.write_names if res is not None and res.write_names
                 else self.a.write_names.get(writer) or {})
        slot = next((p for p, n in names.items() if n == name), None)
        fn = getattr(self, f"_t_{node.type}", None)
        if fn is None:
            if _registry.get(node.type) is None:
                self.warn(f"unknown:{writer}",
                          f"Node '{self.name_of(writer)}' has an unknown type '{node.type}'.")
            return _P(self.generic(writer))
        return fn(writer, self.params(writer), slot, name)

    def generic(self, fid: str) -> str:
        """The fallback phrase: the node's type and its plain params."""
        node = self.nodes.get(fid)
        if node is None:
            return fid
        nt = _registry.get(node.type)
        parts = []
        params = self.params(fid)
        specs = nt.params if nt is not None else ()
        for spec in specs:
            if spec.type in ("attr", "attr_list", "write"):
                continue
            value = params.get(spec.name)
            if value is None or value == "" or value == []:
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            parts.append(_num(value))
        label = _human(node.type)
        return f"{label}({', '.join(parts)})" if parts else label

    # -- the source of an indicator ----------------------------------------------

    def _on(self, fid: str, label: str, *, always_of: bool = False,
            default: str = "close") -> str:
        """*label* with what it is computed on: "RSI(14)" on the close,
        "QQQ RSI(14)" on a reference Ticker's close, "RSI(14) of volume"
        on anything else.  *always_of* always names it: "SMA(50) of close"."""
        src = self.read(fid, "source")
        if src is None:
            return f"{label} of {default}" if always_of else label
        text = src.text
        if text == default:
            return f"{label} of {text}" if always_of else label
        if src.ref is not None and text == f"{src.ref} {default}" and not always_of:
            return f"{src.ref} {label}"
        return f"{label} of {_wrap(src, MATH, AND, OR, XOR, NOT, COND)}"

    # -- one phrase per node type --------------------------------------------------

    def _t_ticker(self, fid, params, slot, name):
        prefix = ticker_prefix(params)
        base = name
        if prefix and name.startswith(f"@{prefix}_"):
            base = "@" + name[len(prefix) + 2:]
        word = _FIELD_WORDS.get(base, base.lstrip("@").replace("_", " "))
        if fid != self.primary:
            # A reference Ticker: its symbol, and its interval when it is not
            # the group's ("SPY (1d) close" in a 5m strategy).
            sym = _upper(params.get("symbol")) or prefix or self.name_of(fid)
            iv = str(params.get("interval") or "").strip()
            if iv and self.interval and iv != self.interval:
                sym = f"{sym} ({iv})"
            return _P(f"{sym} {word}", ref=sym)
        return _P(word)

    def _t_price(self, fid, params, slot, name):
        field_p = self.read(fid, "field")
        return field_p if field_p is not None else _P("close")

    def _t_time_of_day(self, fid, params, slot, name):
        rng = params.get("range")
        if isinstance(rng, str) and "-" in rng:
            start, end = (s.strip() for s in rng.split("-", 1))
            return _P(f"the time is {start} to {end} ET", COND)
        return _P("the time is in the regular session (09:30 to 16:00 ET)", COND)

    def _t_day_of_week(self, fid, params, slot, name):
        days = params.get("days") or []
        if not isinstance(days, (list, tuple)):
            days = [days]
        if not days:
            return _P("never (no day is picked)", COND)
        if sorted(days) == sorted(["mon", "tue", "wed", "thu", "fri"]):
            return _P("it is a weekday", COND)
        words = [_DAY_WORDS.get(str(d), str(d)) for d in days]
        joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]
        return _P(f"the day is {joined}", COND)

    def _t_session_bar(self, fid, params, slot, name):
        return _P("the bar number in the session")

    def _t_rsi(self, fid, params, slot, name):
        return _P(self._on(fid, f"RSI({_num(params.get('period'))})"))

    def _t_macd(self, fid, params, slot, name):
        label = (f"MACD({_num(params.get('fast'))}, {_num(params.get('slow'))}, "
                 f"{_num(params.get('signal'))})")
        label = self._on(fid, label)
        suffix = {"out_signal": " signal", "out_hist": " histogram"}.get(slot or "", "")
        return _P(label + suffix)

    def _t_sma(self, fid, params, slot, name):
        return _P(self._on(fid, f"SMA({_num(params.get('period'))})", always_of=True))

    def _t_ema(self, fid, params, slot, name):
        return _P(self._on(fid, f"EMA({_num(params.get('period'))})", always_of=True))

    def _t_ma(self, fid, params, slot, name):
        kind = str(params.get("type") or "ema").upper()
        return _P(self._on(fid, f"{kind}({_num(params.get('period'))})", always_of=True))

    def _t_bollinger(self, fid, params, slot, name):
        band = {"out_upper": "upper", "out_middle": "middle", "out_lower": "lower"}.get(
            slot or "", "upper")
        label = f"Bollinger({_num(params.get('period'))}, {_num(params.get('stddev'))})"
        return _P(f"{self._on(fid, label)} {band} band")

    def _t_atr(self, fid, params, slot, name):
        return _P(self._on(fid, f"ATR({_num(params.get('period'))})"))

    def _t_atr_pct(self, fid, params, slot, name):
        return _P(self._on(fid, f"ATR%({_num(params.get('period'))})"))

    def _t_stochastic(self, fid, params, slot, name):
        label = (f"Stochastic({_num(params.get('k_period'))}, {_num(params.get('d_period'))}, "
                 f"{_num(params.get('smooth_k'))})")
        line = "%D" if slot == "out_d" else "%K"
        return _P(f"{self._on(fid, label)} {line}")

    def _t_adx(self, fid, params, slot, name):
        period = _num(params.get("period"))
        label = {"out_plus_di": f"+DI({period})", "out_minus_di": f"-DI({period})"}.get(
            slot or "", f"ADX({period})")
        return _P(self._on(fid, label))

    def _t_volume(self, fid, params, slot, name):
        src = self.read(fid, "source")
        vol = src.text if src is not None else "volume"
        if params.get("type") == "sma":
            return _P(f"the {_num(params.get('period'))}-bar average {vol}")
        return _P(vol)

    def _t_constant(self, fid, params, slot, name):
        return _P(_num(params.get("value")))

    def _t_math(self, fid, params, slot, name):
        op = params.get("op") or "add"
        a = self.read(fid, "a") or _P("?")
        if op == "abs":
            return _P(f"abs({a.text})")
        if op == "neg":
            return _P(f"-{_wrap(a, MATH, COND, AND, OR, XOR, NOT)}", MATH)
        b = self.read(fid, "b") or _P("?")
        if op in ("min", "max"):
            word = "smaller" if op == "min" else "larger"
            return _P(f"the {word} of {a.text} and {b.text}")
        sign = _MATH_SIGNS.get(str(op), str(op))
        return _P(f"{_wrap(a, MATH, COND, AND, OR, XOR, NOT)} {sign} "
                  f"{_wrap(b, MATH, COND, AND, OR, XOR, NOT)}", MATH)

    def _t_shift(self, fid, params, slot, name):
        a = self.read(fid, "a") or _P("?")
        ago = _bars(params.get("bars"))
        if a.boolish or a.kind in (COND, AND, OR, XOR, NOT):
            return _P(f"{a.text}, as of {ago} ago", COND, a.boolish)
        return _P(f"{_wrap(a, MATH)} {ago} ago")

    def _t_rolling(self, fid, params, slot, name):
        a = self.read(fid, "a") or _P("?")
        op = str(params.get("op"))
        if op in ("min", "max") and (a.boolish or a.kind in (COND, AND, OR, XOR, NOT)):
            # The low (high) of a true/false signal: true on every (any) bar.
            which = "each" if op == "min" else "any"
            return _P(f"{a.text} on {which} of the last {_bars(params.get('window'))}",
                      COND, True)
        word = _ROLLING_WORDS.get(op, op)
        return _P(f"the {_num(params.get('window'))}-bar {word} of "
                  f"{_wrap(a, MATH, COND, AND, OR, XOR, NOT)}")

    def _slope(self, fid, text: str) -> _P:
        a = self.read(fid, "a") or _P("?")
        return _P(f"{_wrap(a, COND, AND, OR, XOR, NOT)} {text}", COND)

    def _t_rising(self, fid, params, slot, name):
        return self._slope(fid, "is rising")

    def _t_falling(self, fid, params, slot, name):
        return self._slope(fid, "is falling")

    def _t_rising_over(self, fid, params, slot, name):
        return self._slope(fid, f"is higher than {_bars(params.get('bars'))} ago")

    def _t_falling_over(self, fid, params, slot, name):
        return self._slope(fid, f"is lower than {_bars(params.get('bars'))} ago")

    def _turn(self, fid, params, word: str) -> _P:
        text = f"turns {word}"
        bars = params.get("bars")
        if bars not in (None, 1):
            text += f" (over {_bars(bars)})"
        min_pct = params.get("min_pct")
        if min_pct not in (None, 0, 0.0):
            text += f" by at least {_num(min_pct)} %"
        return self._slope(fid, text)

    def _t_turns_up(self, fid, params, slot, name):
        return self._turn(fid, params, "up")

    def _t_turns_down(self, fid, params, slot, name):
        return self._turn(fid, params, "down")

    def _t_turns_up_below(self, fid, params, slot, name):
        return self._slope(fid, f"turns up below {_num(params.get('threshold'))}")

    def _t_turns_down_above(self, fid, params, slot, name):
        return self._slope(fid, f"turns down above {_num(params.get('threshold'))}")

    def _t_accelerating(self, fid, params, slot, name):
        return self._slope(fid, "is accelerating")

    def _t_decelerating(self, fid, params, slot, name):
        return self._slope(fid, "is slowing down")

    def _compare(self, fid, params, word_key: str) -> _P:
        extra = (self.nodes[fid].params or {}).get("condition_extra")
        if extra:
            self.warn(f"extra:{fid}",
                      f"Node '{self.name_of(fid)}' holds a rule the graph cannot run ({extra}), "
                      "so the graph does not run as written.")
            return _P(f"a rule the graph cannot run ({extra})", COND)
        a = self.read(fid, "a") or _P("?")
        b = self.read(fid, "b")
        threshold = params.get("threshold")
        if (word_key == "below" and b is None and threshold in (0, 0.0)
                and (a.text in ("bar number", "close")
                     or (a.ref is not None and a.text == f"{a.ref} close"))):
            # from_rules draws a rule that can never fire as "bar number
            # below 0", or "close below 0" on a reference Ticker (a price is
            # never below 0): False on every bar.
            return _P("never", COND)
        if a.boolish and b is None and threshold == 0.5 and word_key in ("above", "below"):
            # A 0/1 signal against 0.5 is the signal itself (or its opposite).
            return _P(a.text, COND) if word_key == "above" else _P(f"not ({a.text})", NOT)
        rhs = (_wrap(b, COND, AND, OR, XOR, NOT) if b is not None
               else _num(threshold) if threshold is not None else "?")
        return _P(f"{_wrap(a, COND, AND, OR, XOR, NOT)} {_COMPARE_WORDS[word_key]} {rhs}", COND)

    def _t_above(self, fid, params, slot, name):
        return self._compare(fid, params, "above")

    def _t_below(self, fid, params, slot, name):
        return self._compare(fid, params, "below")

    def _t_crosses_above(self, fid, params, slot, name):
        return self._compare(fid, params, "crosses_above")

    def _t_crosses_below(self, fid, params, slot, name):
        return self._compare(fid, params, "crosses_below")

    def _logic(self, fid, kind: str) -> _P:
        terms = self.read_list(fid, "terms")
        if not terms:
            return _P("nothing")
        # A rule that is never (or always) true decides an AND or an OR on
        # its own, or drops out of it: "A or never" is just A.
        if kind in (AND, OR):
            texts = {t.text for t in terms}
            decides, drops = ("never", "always") if kind == AND else ("always", "never")
            if decides in texts:
                return _P(decides, COND)
            terms = [t for t in terms if t.text != drops]
            if not terms:
                return _P(drops, COND)
        if len(terms) == 1:
            return terms[0]
        others = tuple(k for k in (AND, OR, XOR) if k != kind)
        texts = [t.text if t.kind == kind else _wrap(t, *others) for t in terms]
        if kind == XOR:
            if len(texts) == 2:
                return _P(f"exactly one of {texts[0]} and {texts[1]}", XOR)
            return _P("an odd number of " + ", ".join(texts[:-1]) + " and " + texts[-1], XOR)
        return _P(f" {kind} ".join(texts), kind)

    def _t_and(self, fid, params, slot, name):
        return self._logic(fid, AND)

    def _t_or(self, fid, params, slot, name):
        return self._logic(fid, OR)

    def _t_xor(self, fid, params, slot, name):
        return self._logic(fid, XOR)

    def _t_not(self, fid, params, slot, name):
        child = self.read(fid, "signal") or _P("nothing")
        if child.text in ("never", "always"):
            return _P("always" if child.text == "never" else "never", COND)
        return _P(f"not {_wrap(child, COND, AND, OR, XOR, NOT, MATH)}", NOT)

    def _t_wrangle(self, fid, params, slot, name):
        return self.code_phrase(fid)


# ---------------------------------------------------------------------------
# Terminals and settings: stops, size, regime, costs
# ---------------------------------------------------------------------------


def _away(side: str) -> str:
    """Where a stop sits from the entry price for a trade on *side*."""
    if side == "short":
        return "above the entry price"
    if side == "both":
        return "below the entry price on long trades and above it on short trades"
    return "below the entry price"


def _side_label(kind: str, side: Optional[str]) -> str:
    return f"{kind} for {side} trades" if side in ("long", "short") else kind


def stop_text(amount: str, side: str, label_side: Optional[str] = None) -> str:
    return f"{_side_label('Stop', label_side)}: {amount} {_away(side)}"


def trailing_text(params: Mapping[str, Any], side: str, label_side: Optional[str] = None) -> str:
    """A trailing stop (terminal params or a TrailingStopConfig dump)."""
    value = _num(params.get("value"))
    amount = f"{value} x ATR" if params.get("type") == "atr" else f"{value} %"
    px = "close" if params.get("source") == "close" else "price"
    if side == "short":
        where = f"above the lowest {px} since entry"
    elif side == "both":
        where = f"behind the best {px} since entry"
    else:
        where = f"below the highest {px} since entry"
    text = f"{_side_label('Trailing stop', label_side)}: {amount} {where}"
    if params.get("activate_on_profit"):
        pct = params.get("activate_pct")
        if pct not in (None, 0, 0.0):
            text += f", once the trade is up {_num(pct)} %"
        else:
            text += ", once the trade is in profit"
    return text


def time_stop_text(max_bars: Any, label_side: Optional[str] = None) -> str:
    label = _side_label("Time stop", label_side)
    if max_bars is None:
        return f"{label}: no bar limit is set"
    return f"{label}: the trade closes after {_bars(max_bars)}"


def size_text(amount: str, of: str = "the bot's capital") -> str:
    """*of*: "the bot's capital" for a bot, "the capital" in the editor,
    "this group's capital" in a graph with several groups."""
    return f"{amount} of {of}"


def _costs_text(kind: str, params: Mapping[str, Any]) -> Optional[str]:
    if kind == "slippage":
        return f"Backtests count {_num(params.get('bps'))} bps slippage per trade"
    if kind == "commission":
        rate, low = params.get("per_share_rate"), params.get("min_per_order")
        if rate in (0, 0.0) and low in (0, 0.0):
            return "Backtests count no commission"
        return (f"Backtests count a commission of ${_num(rate)} per share, "
                f"at least ${_num(low)} per order")
    if kind == "borrow_rate":
        return f"Short borrow cost: {_num(params.get('rate'))} % a year"
    return None


def _is_default_cost(kind: str, params: Mapping[str, Any]) -> bool:
    want = _DEFAULT_COSTS.get(kind)
    if want is None:
        return False
    for key, value in want.items():
        got = params.get(key)
        if isinstance(got, bool) or not isinstance(got, (int, float)) or float(got) != value:
            return False
    return True


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    name: str
    node_id: Optional[str]
    direction: Optional[str]
    weight: float
    primary: Optional[str]
    symbol: Optional[str]
    interval: Optional[str]
    terminals: list[str]
    settings: list[str]


def _find_groups(d: _Describer, graph: Graph, *, ticker: Optional[str],
                 interval: Optional[str], direction: Optional[str],
                 window_wins: bool) -> list[_Group]:
    """The groups of the graph, as compile finds them (nodes_groups.layout_groups),
    but keeping every group, even one compile would refuse."""
    flat_nodes = d.nodes
    order = list(d.a.order)
    terminals = [nid for nid in order if flat_nodes[nid].type in _TERMINAL_TYPES]
    settings = [nid for nid in order if flat_nodes[nid].type in _SETTING_TYPES]
    group_nodes = [n for n in graph.nodes.values() if n.type == "output_group"]

    def pick(own: Optional[str], given: Optional[str], wins: bool) -> Optional[str]:
        if wins and given:
            return given
        return own or given

    if not group_nodes:
        primary = _groups._implicit_primary(graph)
        params = d.params(primary) if primary is not None else {}
        raw = (graph.nodes[primary].params or {}) if primary is not None else {}
        own_symbol = _upper(raw.get("symbol"))
        own_interval = str(raw.get("interval") or "").strip() or None
        given_interval = (interval or "").strip() or None
        if window_wins:
            # A bot trades its own symbol and interval.
            symbol = pick(own_symbol, _upper(ticker), True)
            iv = pick(own_interval, given_interval, True)
            if iv is None and primary is not None:
                iv = str(params.get("interval") or "") or None
        elif own_symbol:
            # The editor run (graphTicker.graphOwnTicker) and spawn read the
            # Ticker's symbol and interval as a pair: its own symbol runs on
            # its own interval, or "1d" when that is empty.
            symbol, iv = own_symbol, own_interval or _DEFAULT_INTERVAL
        else:
            # No symbol of its own: the run uses the sidebar's symbol AND
            # interval; the Ticker's own interval is not used.
            symbol, iv = _upper(ticker), given_interval
        return [_Group(name=_groups.IMPLICIT_GROUP, node_id=None,
                       direction=direction if direction in ("long", "short") else None,
                       weight=1.0, primary=primary, symbol=symbol, interval=iv,
                       terminals=terminals, settings=settings)]

    group_ids = {g.id for g in group_nodes}
    members: dict[str, list[str]] = {g.id: [] for g in group_nodes}
    for nid in terminals:
        owner = next((a for a in d.flat.ancestors(nid) if a in group_ids), None)
        if owner is None:
            d.warn(f"outside:{nid}",
                   f"{_human(flat_nodes[nid].type).capitalize()} '{d.name_of(nid)}' is outside "
                   "every output group, so it does nothing.")
            continue
        members[owner].append(nid)
    out = []
    for g in group_nodes:
        params = g.params or {}
        primary = _groups._find_ticker(graph, g)
        traw = (graph.nodes[primary].params or {}) if primary is not None else {}
        weight = _groups._weight(params.get("capital_weight", 1.0))
        chain = (g.id,) + _groups._ancestors(graph, g.id)
        out.append(_Group(
            name=g.name or g.id, node_id=g.id,
            direction=params.get("direction", "long"),
            weight=weight if weight is not None else 1.0,
            primary=primary,
            symbol=_upper(traw.get("symbol")) or _upper(ticker),
            interval=(str(traw.get("interval") or "").strip() or (interval or None)
                      or (str(d.params(primary).get("interval") or "") or None
                          if primary is not None else None)),
            terminals=members[g.id],
            settings=list(_groups._scoped_settings(d.flat, settings, chain)),
        ))
    return out


def _side_of(d: _Describer, fid: str) -> str:
    """A terminal's side, with the catalog default (Entry and Exit: long;
    Size, Stop, trailing stop, time stop: both)."""
    side = d.params(fid).get("side")
    return side if side in ("long", "short", "both") else "both"


def _active(d: _Describer, fid: str) -> bool:
    """False for a node compile skips: a bypassed trailing stop or settings
    node.  The other terminals cannot be bypassed: compile ignores their
    bypass flag and runs them, so the text does too."""
    node = d.nodes.get(fid)
    if node is None or _is_bypassed(node):
        return False
    res = d.a.nodes.get(fid)
    return res is None or res.status != PASS


def _join_or(phrases: Sequence[_P]) -> Optional[_P]:
    if not phrases:
        return None
    if len(phrases) == 1:
        return phrases[0]
    return _P(" or ".join(_wrap(p, AND, XOR) for p in phrases), OR)


@dataclass(frozen=True)
class _Val:
    """A stop, size or time stop value and where it comes from.  *num* is
    the plain number (None for a wired value or an expression); *raw* is
    the value itself (a trailing stop's params); *note* goes at the end of
    the sentence (" (bot setting)")."""
    text: str
    num: Optional[float] = None
    raw: Any = None
    note: str = ""


def _describe_group(d: _Describer, g: _Group, *, bot_fields: Optional[Mapping[str, Any]],
                    skip_default_costs: bool, extra_other: Sequence[str],
                    header: Optional[str], capital: str = "the capital") -> GroupText:
    d.set_group(g.primary, g.interval)
    switch = g.direction == "regime_switch"
    side_dir = g.direction if g.direction in ("long", "short") else "long"
    terms = [t for t in g.terminals if _active(d, t)]
    by_type: dict[str, list[str]] = {}
    for t in terms:
        by_type.setdefault(d.nodes[t].type, []).append(t)

    text = GroupText(name=g.name, symbol=g.symbol, interval=g.interval,
                     direction=g.direction, weight=g.weight)
    what = g.symbol or ""
    if g.symbol and g.interval:
        what = f"{g.symbol} ({g.interval})"

    def signal(fids: Iterable[str]) -> Optional[_P]:
        return _join_or([p for p in (d.read(f, "signal") for f in fids) if p is not None])

    # The regime terminal.  With on_flip close_and_reverse in a long or short
    # group the entry is not gated: while the regime is off the same entry
    # opens a trade on the other side (sim_bridge.signal_fns, buy_reverse).
    regimes = by_type.get("regime", [])
    flips = [d.params(t).get("on_flip") or "hold" for t in regimes]
    reverse = not switch and "close_and_reverse" in flips
    other_side = "short" if side_dir == "long" else "long"
    # The side a shared stop sits on: both sides when the group trades both.
    stop_side = "both" if switch or reverse else side_dir

    # -- values: a terminal, else a settings node, else the bot's own field --
    #
    # The order sim_bridge.plan_group uses: an empty terminal (no wire, no
    # constant) sets nothing, so the settings node, then the bot's own
    # value, still applies.  In a regime_switch group a terminal with a side
    # sets that side only; a side with none uses the shared value.

    def terminal_value(t: str, kind: str) -> Optional[_Val]:
        params = d.params(t)
        if kind == "trailing_stop":
            return _Val("", raw=params)
        if kind == "time_stop":
            n = params.get("max_bars")
            return None if n is None else _Val(_num(n), _number(n), n)
        value = d.read(t, "value")
        if value is not None:
            unit = "(in %)" if kind == "stop" else "(as a share)"
            return _Val(f"{_wrap(value, MATH, COND, AND, OR, XOR, NOT)} {unit}")
        const = params.get("constant")
        if const is None:
            return None
        amount = f"{_num(const)} %" if kind == "stop" else _pct_of_fraction(const)
        return _Val(amount, _number(const), const)

    def from_terminals(kind: str, side: Optional[str]) -> Optional[_Val]:
        """The first non-empty terminal of *kind*: in a regime_switch group
        the ones on *side* (None: the ones for both sides)."""
        for t in by_type.get(kind, []):
            if switch:
                t_side = _side_of(d, t)
                if (side or "both") != t_side:
                    continue
            v = terminal_value(t, kind)
            if v is not None:
                return v
        return None

    setting_of = {"stop": ("stop_loss", "pct"), "size": ("position_size", "size")}
    bot_of = {"stop": "stop_loss_pct", "size": "position_size",
              "trailing_stop": "trailing_stop", "time_stop": "max_bars_held"}

    def shared_value(kind: str) -> Optional[_Val]:
        v = from_terminals(kind, None)
        if v is not None:
            return v
        if kind in setting_of:
            value = _last_setting(d, g, *setting_of[kind])
            if value is not None:
                amount = f"{_num(value)} %" if kind == "stop" else _pct_of_fraction(value)
                return _Val(amount, _number(value), value)
        value = (bot_fields or {}).get(bot_of[kind])
        if value is None or (kind == "trailing_stop" and not value):
            return None
        # The bot's own size is how big every trade is; it needs no note.
        note = "" if kind == "size" else " (bot setting)"
        if kind == "trailing_stop":
            return _Val("", raw=dict(value), note=note)
        amount = (f"{_num(value)} %" if kind == "stop" else
                  _pct_of_fraction(value) if kind == "size" else _num(value))
        return _Val(amount, _number(value), value, note)

    def per_side(kind: str, render) -> list[str]:
        """One sentence for the group, or one per side in a regime_switch
        group whose terminals set a side.  *render(value, side, label_side)*
        returns None when the value means "none" (a 0 % stop)."""
        shared = shared_value(kind)
        out: list[Optional[str]] = []
        if not switch:
            if shared is not None:
                out.append(render(shared, stop_side, None))
        else:
            sided = {s: from_terminals(kind, s) for s in ("long", "short")}
            if all(v is None for v in sided.values()):
                if shared is not None:
                    out.append(render(shared, "both", None))
            else:
                for s in ("long", "short"):
                    v = sided[s] if sided[s] is not None else shared
                    if v is not None:
                        out.append(render(v, s, s))
        return [x for x in out if x]

    def render_stop(v: _Val, side: str, label_side: Optional[str]) -> Optional[str]:
        if v.num is not None and v.num <= 0:
            return None  # 0 means no stop (routes/backtest sets none at 0)
        return stop_text(v.text, side, label_side) + v.note

    def render_trailing(v: _Val, side: str, label_side: Optional[str]) -> Optional[str]:
        return trailing_text(v.raw, side, label_side) + v.note

    def render_time(v: _Val, side: str, label_side: Optional[str]) -> Optional[str]:
        return time_stop_text(v.raw, label_side) + v.note

    def render_size(v: _Val, side: str, label_side: Optional[str]) -> Optional[str]:
        amount = size_text(v.text, capital) + v.note
        return f"for {label_side} trades: {amount}" if label_side else amount

    # Stops: stop, trailing stop, time stop, in that order.
    stops = (per_side("stop", render_stop) + per_side("trailing_stop", render_trailing)
             + per_side("time_stop", render_time))
    text.stops = stops

    # Size.
    sizes = per_side("size", render_size)
    text.sizing = "; ".join(sizes) if sizes else None

    # Regime and settings.
    other: list[str] = []
    for t, flip in zip(regimes, flips):
        reg = d.read(t, "signal")
        reg_text = reg.text if reg is not None else "nothing (not wired)"
        if switch:
            other.append(f"The regime is on when {reg_text}")
        elif flip == "close_and_reverse":
            other.append(f"The regime is on when {reg_text}")
            other.append(f"While the regime is off, the same entry opens a {other_side} "
                         "trade instead")
        else:
            other.append(f"Only opens trades while {reg_text}")
        if flip == "close_only":
            other.append("When the regime flips, an open trade is closed")
        elif flip == "close_and_reverse":
            other.append("When the regime flips, an open trade is closed and the other side opens")
    for s in g.settings:
        if not _active(d, s):
            continue
        kind = d.nodes[s].type
        params = d.params(s)
        if skip_default_costs and _is_default_cost(kind, params):
            continue
        phrase = _costs_text(kind, params)
        if phrase is not None and phrase not in other:
            other.append(phrase)
    other.extend(x for x in extra_other if x not in other)
    text.other = other

    # Entry and exit.
    lines: list[str] = []
    if header:
        lines.append(header)
    if switch:
        sides = []
        for side, on_off, verb, back in (("long", "on", "buy", "Sell"),
                                         ("short", "off", "sell short", "Buy back")):
            e = signal(t for t in by_type.get("entry", []) if _side_of(d, t) in (side, "both"))
            x = signal(t for t in by_type.get("exit", []) if _side_of(d, t) in (side, "both"))
            sides.append((side, e, x))
            target = f" {what}" if what else ""
            if e is None:
                lines.append(f"While the regime is {on_off}, no {side} entry is wired.")
            else:
                lines.append(_entry_line(f"While the regime is {on_off}, {verb}{target}", e,
                                         never=f"While the regime is {on_off}, it never "
                                               f"opens a {side} trade."))
            if x is not None:
                lines.append(_exit_line(back, x, side))
        text.entry = _sided([(s, e) for s, e, _x in sides])
        text.exit = _sided([(s, x) for s, _e, x in sides])
    else:
        e = signal(by_type.get("entry", []))
        x = signal(by_type.get("exit", []))
        text.entry = e.text if e is not None else None
        text.exit = x.text if x is not None else None
        verb = "Sell short" if side_dir == "short" else "Buy"
        target = f" {what}" if what else ""
        if e is None:
            lines.append("No entry rule is wired, so no trade opens.")
        else:
            lines.append(_entry_line(f"{verb}{target}", e,
                                     never="Never opens a trade: the entry rule is never true.",
                                     suffix=", while the regime is on" if reverse else ""))
        if x is not None:
            if reverse:
                # Trades on both sides close on the same exit rule.
                lines.append(_exit_line("Close the trade", x, None))
            else:
                lines.append(_exit_line("Buy back" if side_dir == "short" else "Sell", x,
                                        side_dir))
        elif stops:
            lines.append("No exit rule: a trade closes only through its stops.")
        else:
            lines.append("No exit rule and no stop: a trade stays open.")
    lines.extend(f"{s}." for s in stops)
    if text.sizing:
        lines.append(f"Size: {text.sizing}.")
    lines.extend(f"{_first_sentence_upper(o)}." for o in other)
    text.lines = lines
    return text


def _pick_group(groups: Sequence[_Group], name: Optional[str]) -> Optional[_Group]:
    """The group a graph bot trades, as nodes_groups.group_named picks it:
    no name (or "main") takes the only group; a graph with several groups
    needs a name.  None when the bot's group cannot be found."""
    if name is None or (name == _groups.IMPLICIT_GROUP and len(groups) == 1):
        return groups[0] if len(groups) == 1 else None
    return next((g for g in groups if g.name == name), None)


def _entry_line(start: str, cond: _P, *, never: str, suffix: str = "") -> str:
    if cond.text == "never":
        return never
    if cond.text == "always":
        return f"{start} on every bar{suffix}: the entry rule is always true."
    return f"{start} when {cond.text}{suffix}."


def _exit_line(verb: str, cond: _P, side: Optional[str]) -> str:
    rule = f"The {side} exit rule" if side else "The exit rule"
    if cond.text == "never":
        return f"{rule} is never true, so a trade closes only through its stops."
    if cond.text == "always":
        return f"{rule} is always true, so a trade closes on the next bar."
    return f"{verb} when {cond.text}."


def _sided(pairs: Sequence[tuple[str, Optional[_P]]]) -> Optional[str]:
    parts = [f"{side}: {p.text}" for side, p in pairs if p is not None]
    return "; ".join(parts) if parts else None


def _last_setting(d: _Describer, g: _Group, kind: str, param: str) -> Any:
    """The value of the nearest settings node of *kind* in the group's scope
    (settings are listed farthest first, so the last one wins)."""
    value = None
    for s in g.settings:
        if d.nodes[s].type == kind and _active(d, s):
            # An empty value sets nothing (sim_bridge skips it too).
            value = d.params(s).get(param) if d.params(s).get(param) is not None else value
    return value


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def describe_graph(graph: Graph, *, ticker: Optional[str] = None,
                   interval: Optional[str] = None, direction: Optional[str] = None,
                   window_wins: bool = False, only_group: Optional[str] = None,
                   bot_fields: Optional[Mapping[str, Any]] = None,
                   skip_default_costs: bool = False, extra_other: Sequence[str] = (),
                   resolve: Optional[_assets.Resolver] = None, pick_group: bool = False,
                   for_bot: bool = False,
                   timeframes: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """The graph as text: {"groups", "lines", "warnings"} (the F454 contract).

    ticker / interval : the symbol and interval for a graph with no Output
        Group whose primary Ticker has no symbol (the sidebar's, in the
        editor; the Ticker's own interval is then not used either, as in
        graphTicker.ts).  With *window_wins* (a bot) they win over the
        Ticker's own, because the bot trades its own symbol.
    direction : the trade direction of a graph with no Output Group (a bot's
        direction, or the sidebar's in the editor); None reads as long.
    only_group : describe only this group (a bot runs one group).
    pick_group : pick *only_group* the way a graph bot does
        (nodes_groups.group_named).  When the bot's group cannot be picked
        the bot does not trade: no group text, one warning.
    bot_fields : a graph bot's own stop_loss_pct, trailing_stop,
        max_bars_held and position_size; each is shown where the graph sets
        none, as the bot uses them then (sim_bridge.apply_to_bot_config).
    skip_default_costs : leave out cost settings at their default values.
    extra_other : more "other" sentences for every group (trading hours...).
    for_bot : the size is a share of "the bot's capital" (else "the
        capital"; with several groups, "this group's capital").
    timeframes : source node id -> the higher timeframe a rule bot's rule
        runs on (see describe_rule_bot).

    Raises GraphValidationError only for a graph the kernel cannot read at
    all; any other problem in the graph still gives text.
    """
    from nodebuilder.compile import _unknown_error
    from nodebuilder.kernel.evaluate import analyze_graph
    from nodebuilder.trading import nodes_code as _code

    lookup = resolve or _assets.default_resolver()
    expanded, problems = _assets.expand_assets(graph, lookup)
    analysis, flat = analyze_graph(expanded, unknown_error=_unknown_error,
                                   extra_io=_code.extra_io)
    d = _Describer(analysis, flat, expanded, timeframes)
    for problem in problems:
        d.warn(f"asset:{problem}", str(problem))
    for fid, node in analysis.graph.nodes.items():
        if _registry.get(node.type) is None:
            d.warn(f"unknown:{fid}", f"Node '{d.name_of(fid)}' has an unknown type '{node.type}'.")
        code = getattr(node, "code", None)
        if node.type == "wrangle" or (isinstance(code, str) and code.strip()):
            d.code_phrase(fid)  # one warning per code node, reached or not

    groups = _find_groups(d, expanded, ticker=ticker, interval=interval,
                          direction=direction, window_wins=window_wins)
    if pick_group:
        picked = _pick_group(groups, only_group)
        if picked is None:
            names = ", ".join(f"'{g.name}'" for g in groups)
            if only_group in (None, _groups.IMPLICIT_GROUP):
                d.warn("group_missing", f"This bot does not name which group of its graph it "
                                        f"trades (groups: {names}), so it cannot trade.")
            else:
                d.warn("group_missing", f"This bot trades group '{only_group}', which is not in "
                                        f"its graph (groups: {names}), so it cannot trade.")
            groups = []
        else:
            groups = [picked]
    elif only_group is not None and any(g.name == only_group for g in groups):
        groups = [g for g in groups if g.name == only_group]
    capital = ("this group's capital" if len(groups) > 1 else
               "the bot's capital" if for_bot else "the capital")
    total = sum(g.weight for g in groups)
    texts = []
    for g in groups:
        header = None
        if len(groups) > 1:
            if g.weight == 0 or total <= 0:
                share = "no capital, so it does not trade"
            else:
                share = f"{_num(round(g.weight / total * 100, 1))} % of the capital"
            header = f"Group '{g.name}' ({share}):"
        texts.append(_describe_group(d, g, bot_fields=bot_fields,
                                     skip_default_costs=skip_default_costs,
                                     extra_other=extra_other, header=header,
                                     capital=capital))
    lines = [line for t in texts for line in t.lines]
    return {"groups": [t.to_json() for t in texts], "lines": lines,
            "warnings": list(d.warnings)}


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


def bot_extras(config: Any) -> list[str]:
    """Sentences for the bot settings no graph node carries: trading hours,
    smaller size after stops, skipped entries after a stop."""
    out: list[str] = []
    th = getattr(config, "trading_hours", None)
    if th is not None and getattr(th, "enabled", False):
        text = f"Only opens trades from {th.start_time} to {th.end_time} ET"
        skips = [s for s in (th.skip_ranges or []) if s]
        if skips:
            text += ", except " + ", ".join(skips)
        out.append(text)
    # Each trigger as (one, many): "a stop" / "3 stops".
    triggers = {"sl": ("stop", "stops"), "tsl": ("trailing stop", "trailing stops"),
                "both": ("stop or trailing stop", "stops or trailing stops")}
    ds = getattr(config, "dynamic_sizing", None)
    if ds is not None and getattr(ds, "enabled", False):
        one, many = triggers.get(ds.trigger, triggers["sl"])
        after = f"After a {one}" if ds.consec_sls == 1 else f"After {ds.consec_sls} {many} in a row"
        out.append(f"{after}, trades use {_num(ds.reduced_pct)} % of the normal size")
    sk = getattr(config, "skip_after_stop", None)
    if sk is not None and getattr(sk, "enabled", False):
        one, _many = triggers.get(sk.trigger, triggers["sl"])
        skipped = ("the next entry signal" if sk.count == 1
                   else f"the next {sk.count} entry signals")
        out.append(f"After a {one}, skips {skipped}")
    return out


def bot_strategy_request(config: Any):
    """The StrategyRequest a rule bot runs, built the way
    BotManager.backtest_bot builds it (the dates do not matter here)."""
    from models import StrategyRequest

    return StrategyRequest(
        ticker=config.symbol,
        interval=config.interval,
        buy_rules=config.buy_rules,
        sell_rules=config.sell_rules,
        buy_logic=config.buy_logic,
        sell_logic=config.sell_logic,
        initial_capital=config.allocated_capital,
        position_size=config.position_size,
        stop_loss_pct=config.stop_loss_pct,
        max_bars_held=config.max_bars_held,
        trailing_stop=config.trailing_stop,
        dynamic_sizing=config.dynamic_sizing,
        skip_after_stop=config.skip_after_stop,
        trading_hours=config.trading_hours,
        slippage_bps=config.slippage_bps,
        source=config.data_source,
        direction=config.direction,
        regime=config.regime,
        long_buy_rules=config.long_buy_rules,
        long_sell_rules=config.long_sell_rules,
        long_buy_logic=config.long_buy_logic,
        long_sell_logic=config.long_sell_logic,
        short_buy_rules=config.short_buy_rules,
        short_sell_rules=config.short_sell_rules,
        short_buy_logic=config.short_buy_logic,
        short_sell_logic=config.short_sell_logic,
        long_stop_loss_pct=getattr(config, "long_stop_loss_pct", None),
        short_stop_loss_pct=getattr(config, "short_stop_loss_pct", None),
        long_trailing_stop=getattr(config, "long_trailing_stop", None),
        short_trailing_stop=getattr(config, "short_trailing_stop", None),
        long_max_bars_held=getattr(config, "long_max_bars_held", None),
        short_max_bars_held=getattr(config, "short_max_bars_held", None),
        long_position_size=getattr(config, "long_position_size", None),
        short_position_size=getattr(config, "short_position_size", None),
    )


def describe_graph_bot(config: Any) -> dict[str, Any]:
    """A graph bot: its own graph, on its own symbol and interval, only its
    own group.  Raises GraphValidationError when it has no graph."""
    from nodebuilder.models import GraphValidationError

    if config.graph is None:
        raise GraphValidationError("This graph bot has no graph.", node_id=None)
    trailing = config.trailing_stop.model_dump() if config.trailing_stop is not None else None
    return describe_graph(
        config.graph, ticker=config.symbol, interval=config.interval,
        direction=config.direction, window_wins=True, only_group=config.graph_group,
        pick_group=True, for_bot=True,
        bot_fields={"stop_loss_pct": config.stop_loss_pct, "trailing_stop": trailing,
                    "max_bars_held": config.max_bars_held,
                    "position_size": config.position_size},
        extra_other=bot_extras(config),
    )


# The rule lists of a StrategyRequest (a rule bot runs them all).
_RULE_LISTS = ("buy_rules", "sell_rules", "long_buy_rules", "long_sell_rules",
               "short_buy_rules", "short_sell_rules")


def _strip_timeframes(req: Any) -> Any:
    """*req* with every rule's own timeframe removed, so from_rules can draw
    each rule (it refuses a rule on another timeframe)."""
    update = {}
    for name in _RULE_LISTS:
        rules = getattr(req, name, None)
        if rules:
            update[name] = [r.model_copy(update={"timeframe": None}) if r.timeframe else r
                            for r in rules]
    return req.model_copy(update=update)


def describe_rule_bot(config: Any) -> dict[str, Any]:
    """A rule bot: its rules drawn as a graph (from_rules.auto_render), then
    described.  When the rules cannot be drawn, they are phrased directly.

    A rule on a higher timeframe (rule.timeframe) is one the bot runs but
    the graph cannot: from_rules draws it as a placeholder.  The rules are
    then drawn again without their timeframes, and each such rule's phrase
    says "(on 1d bars)", so the text tells what the bot really does."""
    from nodebuilder.from_rules import auto_render

    try:
        req = bot_strategy_request(config)
        graph = auto_render(req)
        timeframes: dict[str, str] = {}
        placeholders = {nid: str((n.params or {}).get("condition_extra"))
                        for nid, n in graph.nodes.items()
                        if (n.params or {}).get("condition_extra")}
        # from_rules marks a higher-timeframe rule "<condition> on <tf> bars".
        htf = {nid: m.group(1) for nid, extra in placeholders.items()
               if (m := re.search(r" on (\S+) bars$", extra))}
        if htf:
            drawn = auto_render(_strip_timeframes(req))
            for nid, tf in htf.items():
                node = drawn.nodes.get(nid)
                if node is None or (node.params or {}).get("condition_extra"):
                    # The drawing did not line up: phrase the rules directly.
                    return describe_rules(config)
                timeframes[nid] = tf
            graph = drawn
        result = describe_graph(graph, ticker=config.symbol, interval=config.interval,
                                direction=config.direction, window_wins=True,
                                skip_default_costs=True, extra_other=bot_extras(config),
                                for_bot=True, timeframes=timeframes)
        if timeframes:
            result["warnings"].append(_timeframe_warning(sorted(set(timeframes.values())),
                                                         config.interval))
        return result
    except Exception:  # noqa: BLE001  (any failure: fall back to the rules themselves)
        return describe_rules(config)


def _timeframe_warning(timeframes: Sequence[str], interval: Any) -> str:
    """Rules set to another timeframe run differently in a backtest
    (routes/backtest._apply_htf_rules: checked on those bars and joined to
    the other rules with AND) and in the live bot (bot_runner calls
    eval_rules on the bot's own bars, which never reads rule.timeframe)."""
    tfs = ", ".join(timeframes)
    return (f"Some rules are set to another timeframe ({tfs}). Backtests check them on "
            f"those bars and join them to the other rules with AND, but the live bot "
            f"checks them on its own {interval} bars like the other rules.")


# -- rules phrased directly (the fallback) -----------------------------------------


def _rule_series(rule: Any) -> str:
    ind = str(rule.indicator)
    rp = rule.params or {}
    if ind == "rsi":
        return f"RSI({_num(rp.get('period', 14))})"
    if ind == "macd":
        return "MACD(12, 26, 9)"
    if ind == "ma":
        return f"{str(rp.get('type', 'ema')).upper()}({_num(rp.get('period', 20))}) of close"
    if ind == "bb":
        return (f"Bollinger({_num(rp.get('period', 20))}, {_num(rp.get('std', 2))}) "
                f"{_BB_WORDS.get(rule.param or 'upper', rule.param)}")
    if ind == "atr":
        return f"ATR({_num(rp.get('period', 14))})"
    if ind == "atr_pct":
        return f"ATR%({_num(rp.get('period', 14))})"
    if ind == "volume":
        return ("the 20-bar average volume" if (rule.param or "raw") == "sma" else "volume")
    if ind == "stochastic":
        return (f"Stochastic({_num(rp.get('k_period', 14))}, {_num(rp.get('d_period', 3))}, "
                f"{_num(rp.get('smooth_k', 3))}) %K")
    if ind == "adx":
        period = _num(rp.get("period", 14))
        return {"plus_di": f"+DI({period})", "minus_di": f"-DI({period})"}.get(
            rule.param or "", f"ADX({period})")
    if ind == "price":
        return "close"
    return _human(ind)


_BB_WORDS = {"upper": "upper band", "middle": "middle band", "lower": "lower band",
             "bandwidth": "bandwidth", "pctb": "%B"}


def _rule_ref(rule: Any) -> Optional[str]:
    """The series rule.param names (signal_engine.resolve_ref), as a reader
    says it: "ma:50:sma" is "SMA(50) of close".  None when rule.param names
    no series (the rule then compares with its value)."""
    param = str(rule.param or "")
    if not param:
        return None
    rp = rule.params or {}
    if param == "signal":
        return "MACD(12, 26, 9) signal"
    if param == "close":
        return "close"
    if param == "d" and rule.indicator == "stochastic":
        return (f"Stochastic({_num(rp.get('k_period', 14))}, {_num(rp.get('d_period', 3))}, "
                f"{_num(rp.get('smooth_k', 3))}) %D")
    parts = param.split(":")
    head = parts[0]
    if head == "ma" and len(parts) >= 3:
        return f"{parts[2].upper()}({parts[1]}) of close"
    if head == "bb" and len(parts) >= 4:
        return f"Bollinger({parts[1]}, {parts[2]}) {_BB_WORDS.get(parts[3], parts[3])}"
    if head == "atr" and len(parts) >= 2:
        return f"ATR({parts[1]})"
    if head == "volume_sma" and len(parts) >= 2:
        return f"the {parts[1]}-bar average volume"
    if head == "stoch" and len(parts) >= 5:
        line = "%D" if parts[4] == "d" else "%K"
        return f"Stochastic({parts[1]}, {parts[2]}, {parts[3]}) {line}"
    if head == "adx" and len(parts) >= 3:
        label = {"plus_di": "+DI", "minus_di": "-DI"}.get(parts[2], "ADX")
        return f"{label}({parts[1]})"
    return None


def _rule_phrase(rule: Any) -> str:
    cond = str(rule.condition)
    series = _rule_series(rule)
    if cond in _COMPARE_WORDS:
        ref = _rule_ref(rule)
        rhs = ref if ref is not None else _num(rule.value) if rule.value is not None else None
        if rhs is None:
            # No series and no value: the rule engine never fires it.
            return "never"
        text = f"{series} {_COMPARE_WORDS[cond]} {rhs}"
    else:
        words = {"rising": "is rising", "falling": "is falling",
                 "turns_up": "turns up", "turns_down": "turns down",
                 "rising_over": f"is higher than {_bars(rule.value or 10)} ago",
                 "falling_over": f"is lower than {_bars(rule.value or 10)} ago",
                 "turns_up_below": f"turns up below {_num(rule.value)}",
                 "turns_down_above": f"turns down above {_num(rule.value)}",
                 "crossover_up": "crosses above its signal",
                 "crossover_down": "crosses below its signal"}
        text = f"{series} {words.get(cond, _human(cond))}"
    if getattr(rule, "timeframe", None):
        text += f" (set to {rule.timeframe} bars)"
    return f"not ({text})" if getattr(rule, "negated", False) else text


def _rules_text(rules: Optional[Sequence[Any]], logic: str) -> Optional[str]:
    live = [r for r in (rules or []) if not getattr(r, "muted", False)]
    if not live:
        return None
    is_or = str(logic).upper() == "OR"
    phrases = [_rule_phrase(r) for r in live]
    # A rule that never fires drops out of an OR and decides an AND.
    if "never" in phrases:
        if not is_or:
            return "never"
        phrases = [p for p in phrases if p != "never"] or ["never"]
    return (" or " if is_or else " and ").join(phrases)


def _rule_timeframes(config: Any) -> list[str]:
    """The timeframes the bot's rules are set to (other than its own)."""
    out = set()
    for name in _RULE_LISTS:
        for r in getattr(config, name, None) or []:
            if getattr(r, "timeframe", None) and not getattr(r, "muted", False):
                out.add(str(r.timeframe))
    return sorted(out)


def _regime_text(regime: Any) -> str:
    """When a rule bot's regime is on (from_rules.add_regime_network): its
    rules, or its single indicator, on its own timeframe, held for min_bars
    bars in a row."""
    tf = regime.timeframe
    if regime.rules:
        when = _rules_text(regime.rules, regime.logic) or "never"
    else:
        ip = regime.indicator_params or {}
        ind = regime.indicator
        if ind == "ma":
            label = f"{str(ip.get('type', 'ema')).upper()}({_num(ip.get('period', 20))}) of close"
        elif ind == "rsi":
            label = f"RSI({_num(ip.get('period', 14))})"
        elif ind == "macd":
            label = (f"MACD({_num(ip.get('fast', 12))}, {_num(ip.get('slow', 26))}, "
                     f"{_num(ip.get('signal', 9))})")
        else:
            return f"The regime uses {_human(str(ind))} on {tf} bars"
        if regime.condition in ("rising", "falling"):
            when = f"{label} is {regime.condition}"
        else:
            word = "below" if regime.condition == "below" else "above"
            when = f"close is {word} {label}"
    text = f"The regime is on when {when}, on {tf} bars"
    n = int(regime.min_bars or 1)
    if n > 1:
        text += f", for {n} bars in a row"
    return text


def _migrated(rules: Optional[Sequence[Any]]) -> list[Any]:
    """The rules as the rule engine reads them (old names like "ema20"
    become "ma:20:ema", signal_engine.migrate_rule)."""
    from signal_engine import migrate_rule

    out = []
    for r in rules or []:
        try:
            out.append(migrate_rule(r))
        except Exception:  # noqa: BLE001  (keep the rule as it is)
            out.append(r)
    return out


def _side_value(config: Any, name: str, side: Optional[str]) -> Any:
    """A bot field for one side of a regime bot: the per-side field
    (long_stop_loss_pct...) when set, else the shared one (routes/backtest
    _dir_stop, _dir_mbh, _dir_size)."""
    if side is not None:
        value = getattr(config, f"{side}_{name}", None)
        if value is not None:
            return value
    return getattr(config, name, None)


def _side_trailing(config: Any, side: Optional[str]) -> Optional[dict]:
    """A regime bot's trailing stop for one side, merged as routes/backtest
    _dir_ts merges it: the side's type and value, the shared source and
    activation."""
    base = config.trailing_stop.model_dump() if config.trailing_stop is not None else None
    over = getattr(config, f"{side}_trailing_stop", None) if side is not None else None
    if over is None:
        return base
    merged = dict(base or {})
    over = over.model_dump()
    if base is None:
        return over
    merged.update(type=over.get("type"), value=over.get("value"))
    return merged


def describe_rules(config: Any, warnings: Optional[Sequence[str]] = None) -> dict[str, Any]:
    """A rule bot's rules phrased directly, without drawing a graph.

    A bot with its regime on trades the long rules while the regime is on
    and the short rules while it is off, with per-side stops and sizes
    (routes/backtest b23_mode); its buy and sell rules and its direction
    are not used then."""
    regime = getattr(config, "regime", None)
    switch = bool(regime is not None and getattr(regime, "enabled", False))
    direction = config.direction if config.direction in ("long", "short") else "long"
    what = f"{config.symbol} ({config.interval})"

    def sentences(side: Optional[str], stop_side: str, label: Optional[str]) -> dict:
        stop = _side_value(config, "stop_loss_pct", side)
        trail = _side_trailing(config, side)
        bars = _side_value(config, "max_bars_held", side)
        size = _side_value(config, "position_size", side)
        return {
            "stop": (stop_text(f"{_num(stop)} %", stop_side, label)
                     if _number(stop) is not None and stop > 0 else None),
            "trail": trailing_text(trail, stop_side, label) if trail else None,
            "time": time_stop_text(bars, label) if bars is not None else None,
            "size": size_text(_pct_of_fraction(size)) if size is not None else None,
        }

    lines: list[str] = []
    stops: list[str] = []
    if not switch:
        entry = _rules_text(_migrated(config.buy_rules), config.buy_logic)
        exit_ = _rules_text(_migrated(config.sell_rules), config.sell_logic)
        s = sentences(None, direction, None)
        stops = [x for x in (s["stop"], s["trail"], s["time"]) if x]
        sizing = s["size"]
        verb = "Sell short" if direction == "short" else "Buy"
        if not entry:
            lines.append("No entry rule is set, so no trade opens.")
        else:
            lines.append(_entry_line(f"{verb} {what}", _P(entry),
                                     never="Never opens a trade: the entry rule is never true."))
        if exit_:
            lines.append(_exit_line("Buy back" if direction == "short" else "Sell",
                                    _P(exit_), direction))
        group_direction = direction
        other = []
    else:
        sides = {}
        for side, on_off, verb, back in (("long", "on", "buy", "Sell"),
                                         ("short", "off", "sell short", "Buy back")):
            e = _rules_text(_migrated(getattr(config, f"{side}_buy_rules", None)),
                            getattr(config, f"{side}_buy_logic", "AND"))
            x = _rules_text(_migrated(getattr(config, f"{side}_sell_rules", None)),
                            getattr(config, f"{side}_sell_logic", "AND"))
            sides[side] = (e, x)
            if e is None:
                lines.append(f"While the regime is {on_off}, no {side} entry is set.")
            else:
                lines.append(_entry_line(f"While the regime is {on_off}, {verb} {what}", _P(e),
                                         never=f"While the regime is {on_off}, it never "
                                               f"opens a {side} trade."))
            if x is not None:
                lines.append(_exit_line(back, _P(x), side))
        entry = _sided([(s, _P(e)) for s, (e, _x) in sides.items() if e])
        exit_ = _sided([(s, _P(x)) for s, (_e, x) in sides.items() if x])
        per = {s: sentences(s, s, s) for s in ("long", "short")}
        # The same value on both sides reads as one sentence for both.
        same = sentences("long", "both", None)
        raw = {"stop": lambda s: _side_value(config, "stop_loss_pct", s),
               "trail": lambda s: _side_trailing(config, s),
               "time": lambda s: _side_value(config, "max_bars_held", s),
               "size": lambda s: _side_value(config, "position_size", s)}
        sizes: list[str] = []
        for key in ("stop", "trail", "time", "size"):
            if raw[key]("long") == raw[key]("short"):
                picked = [(None, same[key])]
            else:
                picked = [("long", per["long"][key]), ("short", per["short"][key])]
            picked = [(s, p) for s, p in picked if p]
            if key == "size":
                sizes = [f"for {s} trades: {p}" if s else p for s, p in picked]
            else:
                stops.extend(p for _s, p in picked)
        sizing = "; ".join(sizes) if sizes else None
        group_direction = "regime_switch"
        other = [_regime_text(regime)]
        if regime.on_flip == "close_only":
            other.append("When the regime flips, an open trade is closed")
        elif regime.on_flip == "close_and_reverse":
            other.append("When the regime flips, an open trade is closed and the other side opens")
    other.extend(bot_extras(config))
    lines.extend(f"{s}." for s in stops)
    if sizing:
        lines.append(f"Size: {sizing}.")
    lines.extend(f"{_first_sentence_upper(o)}." for o in other)
    group = GroupText(name=_groups.IMPLICIT_GROUP, symbol=config.symbol, interval=config.interval,
                      direction=group_direction, entry=entry, exit=exit_, stops=stops,
                      sizing=sizing, other=other)
    if warnings is None:
        warnings = ["These rules could not be drawn as a graph, so the text is read "
                    "from the rules directly."]
    warnings = list(warnings)
    timeframes = _rule_timeframes(config)
    if timeframes:
        warnings.append(_timeframe_warning(timeframes, config.interval))
    return {"groups": [group.to_json()], "lines": lines, "warnings": warnings}
