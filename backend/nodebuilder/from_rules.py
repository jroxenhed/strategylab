"""StrategyRequest -> Graph auto-render (canonical Python impl).

Unit 3 — public surface is `auto_render(req: StrategyRequest) -> Graph`.

The returned graph always has readOnly=True, _version=3 and the current
stream_schema.  Each consumer's input wires get in0, in1... in the order
they are wired.

Since W2 (plan D4) a node names what it reads in its params (``a``, ``b``,
``source``, ``terms``, ``signal``) and what it writes (``out``...).  Wires
only carry streams.  Write names are the catalog defaults made unique in the
graph (``@rsi``, ``@rsi_2``), exactly as the v2 -> v3 migration stores them.

Every rule the rule builder can make is drawn so that the graph backtest
gives the same trades as the rule backtest (``signal_engine.eval_rule``),
including ``negated`` (a NOT node; bar 0 stays False) and the bar-0 guard.
A rule that the rule engine can never fire (no value and no reference, a
series it cannot find) is drawn as a "never" node, so a NOT on it is True
from bar 1, as in eval_rules.  The one thing still refused out loud is a
rule on another timeframe (``rule.timeframe``): its comparison carries a
``condition_extra`` marker and compile raises UnsupportedNodeError.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Optional, Union

from models import StrategyRequest, RegimeConfig
from signal_engine import Rule, _clamp_lookback, migrate_rule
from nodebuilder.migrate import default_name, unique_name
from nodebuilder.models import STREAM_SCHEMA_VERSION, Graph, Node, Wire


# ---------------------------------------------------------------------------
# Layout constants — top-to-bottom flow (Houdini-style).
# Pipeline levels become rows (y); per-level siblings spread horizontally (x).
# Wires render from one node's body-bottom to the next node's body-top.
# ---------------------------------------------------------------------------

# Vertical positions (one row per pipeline stage)
_COL_TICKER = 0.0           # row y for Ticker
_COL_INDICATOR = 160.0      # row y for Indicators
_COL_COMPARISON = 320.0     # row y for Comparisons (+ NOT wrappers offset 60px down)
_COL_LOGIC = 540.0          # row y for per-side Logic AND/OR
_COL_REGIME_GATE = 700.0    # row y for regime AND gates
_COL_TERMINAL = 860.0       # row y for Entry / Exit terminals

# Horizontal stacking within a row
_ROW_PITCH = 200.0          # x-gap between siblings on the same row

# Settings cluster lives to the side of the main pipeline
_SETTINGS_X = -260.0        # x position (left of ticker)
_SETTINGS_Y_START = 0.0     # y for first settings node
_SETTINGS_Y_PITCH = 110.0   # vertical gap between settings nodes

# Regime sub-tree pushed below the main pipeline (when present)
_REGIME_Y_OFFSET = 1000.0   # added to main-pipeline y for regime-internal rows


# ---------------------------------------------------------------------------
# What a node reads
# ---------------------------------------------------------------------------


class _Ref(NamedTuple):
    """One attribute a node reads, before write names are known.

    ``slot`` is a write param of the node at ``node`` (``out_signal``), None
    for that node's first write param, or a fixed attribute name such as a
    Ticker's ``@close``.
    """

    node: str
    slot: Optional[str] = None


_Read = Union[_Ref, list[_Ref]]

# MACD params the rule engine always uses (signal_engine.compute_indicators).
# Its "signal" series, which is_above_signal and param="signal" compare
# against, is this MACD's signal line whatever the rule's indicator is.
_RULE_MACD_PARAMS: dict[str, int] = {"fast": 12, "slow": 26, "signal": 9}

# Bollinger bands the rule engine keeps (bb_<p>_<std>_<band>) and the node
# output for the three plain ones.  bandwidth and pctb are drawn with math
# nodes.
_BB_SLOTS: dict[str, str] = {"upper": "out_upper", "middle": "out_middle", "lower": "out_lower"}
_BB_BANDS = frozenset(_BB_SLOTS) | {"bandwidth", "pctb"}

_ADX_SLOTS: dict[str, str] = {"adx": "out_adx", "plus_di": "out_plus_di", "minus_di": "out_minus_di"}
_STOCH_SLOTS: dict[str, str] = {"k": "out_k", "d": "out_d"}

# Conditions that compare a series with a second series or a value.
_TWO_SIDED: dict[str, str] = {
    "above": "above",
    "below": "below",
    "crossover_up": "crosses_above",
    "crosses_above": "crosses_above",
    "crossover_down": "crosses_below",
    "crosses_below": "crosses_below",
}
# Conditions that compare a series with the default MACD's signal line.
_SIGNAL_CONDITIONS: dict[str, str] = {"is_above_signal": "above", "is_below_signal": "below"}
# Conditions that look at one series over time; the node type has the same name.
_SLOPE_CONDITIONS = frozenset({
    "rising", "falling", "rising_over", "falling_over", "turns_up", "turns_down",
    "turns_up_below", "turns_down_above", "accelerating", "decelerating",
})


class _Unsupported(Exception):
    """A rule this renderer cannot draw so that it runs the same."""


def _same_key(value: Any) -> int:
    """*value* as a whole number, checking the rule engine would find it.

    compute_indicators stores most series under ``int(period)`` but
    resolve_series looks them up under the period as written, so a period
    such as ``20.0`` is computed and then never found: the rule never fires.
    Raises KeyError for such a value and _Unsupported when int() fails (the
    rule backtest itself fails on that value).
    """
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _Unsupported(f"period {value!r}") from exc
    if f"{value}" != f"{number}":
        raise KeyError(value)
    return number


def _ma_node(period: Any, ma_type: Any) -> tuple[str, dict[str, Any]]:
    """The node for an MA the rule engine computes with compute_ma.

    compute_ma lower-cases the type and runs anything but sma and rma as an
    EMA.  SMA and EMA keep their own node types (the viewer's familiar
    boxes); rma uses the general ``ma`` node.
    """
    kind = str(ma_type).lower()
    if kind == "sma":
        return "sma", {"period": int(period)}
    if kind == "rma":
        return "ma", {"period": int(period), "type": "rma"}
    return "ema", {"period": int(period)}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _param_hash(params: dict[str, Any]) -> str:
    """Stable param hash for deduplication, e.g. 'period_14_type_sma'."""
    return "_".join(f"{k}_{v}" for k, v in sorted(params.items()))


def _indicator_path(catalog_name: str, params: dict[str, Any]) -> str:
    h = _param_hash(params)
    return f"/{catalog_name}_{h}" if h else f"/{catalog_name}"


def _wire_id(from_path: str, to_path: str) -> str:
    """Stable wire id: replace / with _."""
    f_slug = from_path.replace("/", "_").lstrip("_")
    t_slug = to_path.replace("/", "_").lstrip("_")
    return f"wire_{f_slug}_{t_slug}"


# ---------------------------------------------------------------------------
# Core builder state
# ---------------------------------------------------------------------------

class _GraphBuilder:
    """Accumulates nodes, wires and reads during the auto_render traversal."""

    def __init__(self, ticker: str, interval: str, source: str) -> None:
        self.ticker = ticker
        self.interval = interval
        self.source = source

        self.nodes: dict[str, Node] = {}
        self.wires: list[Wire] = []
        # node id -> read param -> what it reads (names filled in at the end)
        self.reads: dict[str, dict[str, _Read]] = {}

        # Layout trackers per column
        self._col_y: dict[float, float] = {}

    def _next_y(self, col_x: float) -> float:
        y = self._col_y.get(col_x, 0.0)
        self._col_y[col_x] = y + _ROW_PITCH
        return y

    def _add_node(self, path: str, node_type: str, params: dict[str, Any],
                  position: tuple[float, float]) -> None:
        """Add a node; silently skip if path already registered (idempotent).

        The name follows the same rule as the v1 -> v2 migration, so an old
        auto-rendered graph and a new one come out identical.
        """
        if path in self.nodes:
            return
        name = unique_name(default_name(path, node_type), (n.name for n in self.nodes.values()))
        self.nodes[path] = Node(id=path, type=node_type, name=name, params=params, position=position)

    def _connect(self, from_path: str, to_path: str) -> None:
        """Wire *from_path* into the next free input of *to_path* (once)."""
        for w in self.wires:
            if (w.from_path, w.to_path) == (from_path, to_path):
                return
        k = sum(1 for w in self.wires if w.to_path == to_path)
        self.wires.append(Wire(**{
            "id": _wire_id(from_path, to_path),
            "from": from_path,
            "to": to_path,
            "to_port": f"in{k}",
        }))

    def _read(self, node: str, param: str, what: _Read) -> None:
        """*node* reads *what* through *param*; wire each source in, in order."""
        self.reads.setdefault(node, {})[param] = what
        for ref in (what if isinstance(what, list) else [what]):
            self._connect(ref.node, node)

    # ------------------------------------------------------------------
    # Ticker node
    # ------------------------------------------------------------------

    def add_ticker(self, symbol: str, interval: str, source: str,
                   prefix: str = "", y_offset: float = 0.0) -> str:
        sym_lower = symbol.lower().replace(".", "_")
        # The source stays in the id so ids match older graphs; the Ticker no
        # longer has a source param (the sidebar owns it, plan D11).
        path = f"{prefix}/ticker_{sym_lower}_{interval}_{source}"
        self._add_node(path, "ticker", {"symbol": symbol, "interval": interval},
                       (y_offset, _COL_TICKER))
        return path

    # ------------------------------------------------------------------
    # Indicator nodes (with dedup by path)
    # ------------------------------------------------------------------

    def add_indicator(self, catalog_name: str, params: dict[str, Any],
                      ticker_path: str, prefix: str = "", source: bool = True) -> str:
        """An indicator node on the Ticker, shared by every rule that uses the
        same type and params.  *source* False leaves the source param to the
        node's own default (volume reads @volume, price reads @close)."""
        path = prefix + _indicator_path(catalog_name, params)
        if path not in self.nodes:
            y = self._next_y(_COL_INDICATOR)
            self._add_node(path, catalog_name, params, (y, _COL_INDICATOR))
            if source:
                self._read(path, "source", _Ref(ticker_path, "@close"))
            else:
                self._connect(ticker_path, path)
        return path

    def _math(self, path: str, op: str, a: _Ref, b: _Ref) -> _Ref:
        """A math node a <op> b (shared by path)."""
        if path not in self.nodes:
            y = self._next_y(_COL_INDICATOR)
            self._add_node(path, "math", {"op": op}, (y, _COL_INDICATOR + 60.0))
            self._read(path, "a", a)
            self._read(path, "b", b)
        return _Ref(path)

    # ------------------------------------------------------------------
    # The series a rule reads (signal_engine.resolve_series)
    # ------------------------------------------------------------------

    def _series(self, rule: Rule, tp: str, prefix: str) -> Optional[_Ref]:
        """The rule's own series, or None when the rule engine finds none
        (the rule is then never true).  Raises _Unsupported."""
        ind = rule.indicator
        rp = rule.params or {}
        try:
            if ind == "rsi":
                # compute_rsi runs Wilder for "wilder" (any case) and a plain
                # rolling mean for anything else.
                rsi_type = "wilder" if str(rp.get("type", "sma")).lower() == "wilder" else "sma"
                return _Ref(self.add_indicator(
                    "rsi", {"period": rp.get("period", 14), "type": rsi_type}, tp, prefix))
            if ind == "macd":
                # The rule engine always computes MACD(12,26,9) and ignores
                # rule params, so the node does too.
                return _Ref(self.add_indicator("macd", dict(_RULE_MACD_PARAMS), tp, prefix),
                            "out_line")
            if ind == "ma":
                if not rule.params:
                    return None
                if "period" not in rp:
                    raise _Unsupported("ma without a period")
                node_type, params = _ma_node(rp["period"], rp.get("type", "ema"))
                return _Ref(self.add_indicator(node_type, params, tp, prefix))
            if ind == "bb":
                band = rule.param or "upper"
                if band not in _BB_BANDS:
                    return None
                bb = self._bollinger(_same_key(rp.get("period", 20)), rp.get("std", 2), tp, prefix)
                return self._bb_band(bb, band, tp)
            if ind in ("atr", "atr_pct"):
                period = _same_key(rp.get("period", 14))
                return _Ref(self.add_indicator(ind, {"period": period}, tp, prefix))
            if ind == "volume":
                if (rule.param or "raw") == "sma":
                    return self._volume_sma(_same_key(rp.get("period", 20)), tp, prefix)
                return _Ref(self.add_indicator("volume", {"type": "raw"}, tp, prefix, source=False))
            if ind == "stochastic":
                return _Ref(self._stochastic(rp, tp, prefix), "out_k")
            if ind == "adx":
                component = rule.param if rule.param in _ADX_SLOTS else "adx"
                node = self.add_indicator("adx", {"period": _same_key(rp.get("period", 14))},
                                          tp, prefix)
                return _Ref(node, _ADX_SLOTS[component])
            if ind == "price":
                return _Ref(self.add_indicator("price", {}, tp, prefix, source=False))
        except KeyError:
            return None
        except (TypeError, ValueError) as exc:
            # A param the rule backtest cannot read either (int("abc")).
            raise _Unsupported(f"{ind} params {rp!r}") from exc
        return None

    def _bollinger(self, period: int, std: Any, tp: str, prefix: str) -> str:
        return self.add_indicator("bollinger", {"period": period, "stddev": float(std)}, tp, prefix)

    def _bb_band(self, bb: str, band: str, tp: str) -> _Ref:
        """One Bollinger band, with bandwidth and %B built from math nodes:
        bandwidth = (upper - lower) / middle,
        pctb = (close - lower) / (upper - lower), NaN where upper == lower.
        The math node's division gives NaN on a zero divisor, as the rule
        engine's replace(0, nan) does for %B."""
        if band in _BB_SLOTS:
            return _Ref(bb, _BB_SLOTS[band])
        width = self._math(f"{bb}_width", "sub", _Ref(bb, "out_upper"), _Ref(bb, "out_lower"))
        if band == "bandwidth":
            return self._math(f"{bb}_bandwidth", "div", width, _Ref(bb, "out_middle"))
        above_lower = self._math(f"{bb}_above_lower", "sub", _Ref(tp, "@close"), _Ref(bb, "out_lower"))
        return self._math(f"{bb}_pctb", "div", above_lower, width)

    def _volume_sma(self, period: int, tp: str, prefix: str) -> _Ref:
        if period == 1:
            # rolling(1).mean() is the volume itself; the node's period starts at 2.
            return _Ref(self.add_indicator("volume", {"type": "raw"}, tp, prefix, source=False))
        return _Ref(self.add_indicator("volume", {"type": "sma", "period": period},
                                       tp, prefix, source=False))

    def _stochastic(self, rp: dict, tp: str, prefix: str) -> str:
        params = {
            "k_period": _same_key(rp.get("k_period", 14)),
            "d_period": _same_key(rp.get("d_period", 3)),
            "smooth_k": _same_key(rp.get("smooth_k", 3)),
        }
        return self.add_indicator("stochastic", params, tp, prefix)

    # ------------------------------------------------------------------
    # The series a rule compares with (signal_engine.resolve_ref)
    # ------------------------------------------------------------------

    def _reference(self, rule: Rule, tp: str, prefix: str) -> Optional[_Ref]:
        """The series rule.param names, or None (the rule then compares with
        its value).  Mirrors resolve_ref case by case."""
        param = rule.param
        if not param:
            return None
        if param == "signal":
            return self._macd_signal(tp, prefix)
        if param == "close":
            return _Ref(tp, "@close")
        if param == "d" and rule.indicator == "stochastic":
            try:
                return _Ref(self._stochastic(rule.params or {}, tp, prefix), "out_d")
            except KeyError:
                return None
        parts = param.split(":")
        try:
            if param.startswith("ma:"):
                head = param.split(":", 2)
                if len(head) != 3:
                    return None
                node_type, params = _ma_node(int(head[1]), head[2])
                return _Ref(self.add_indicator(node_type, params, tp, prefix))
            if param.startswith("bb:"):
                if len(parts) < 4 or parts[3] not in _BB_BANDS:
                    return None
                bb = self._bollinger(int(parts[1]), float(parts[2]), tp, prefix)
                return self._bb_band(bb, parts[3], tp)
            if param.startswith("atr:"):
                if len(parts) < 2:
                    return None
                return _Ref(self.add_indicator("atr", {"period": int(parts[1])}, tp, prefix))
            if param.startswith("volume_sma:"):
                if len(parts) < 2:
                    return None
                return self._volume_sma(int(parts[1]), tp, prefix)
            if param.startswith("stoch:"):
                if len(parts) < 5 or parts[4] not in _STOCH_SLOTS:
                    return None
                node = self.add_indicator("stochastic", {
                    "k_period": int(parts[1]), "d_period": int(parts[2]), "smooth_k": int(parts[3]),
                }, tp, prefix)
                return _Ref(node, _STOCH_SLOTS[parts[4]])
            if param.startswith("adx:"):
                if len(parts) < 3:
                    return None
                component = parts[2] if parts[2] in ("plus_di", "minus_di") else "adx"
                node = self.add_indicator("adx", {"period": int(parts[1])}, tp, prefix)
                return _Ref(node, _ADX_SLOTS[component])
        except ValueError:
            return None
        return None

    def _macd_signal(self, tp: str, prefix: str) -> _Ref:
        return _Ref(self.add_indicator("macd", dict(_RULE_MACD_PARAMS), tp, prefix), "out_signal")

    # ------------------------------------------------------------------
    # Rule set → comparison + logic
    # ------------------------------------------------------------------

    def _rule_node(self, rule: Rule, path: str, pos: tuple[float, float],
                   tp: str, prefix: str) -> None:
        """The node that is True on the bars where *rule* fires (before NOT)."""
        cond = rule.condition
        try:
            if rule.timeframe is not None:
                raise _Unsupported(f"on {rule.timeframe} bars")
            series = self._series(rule, tp, prefix)
        except _Unsupported as exc:
            # Refused by compile with the node named, never run part-way.
            node_type = _TWO_SIDED.get(cond) or _SIGNAL_CONDITIONS.get(cond) or "above"
            self._add_node(path, node_type, {"condition_extra": f"{cond} {exc}"}, pos)
            self._read(path, "a", _Ref(tp, "@close"))
            return

        if series is None:
            self._never(path, pos, tp)
            return

        if cond in _TWO_SIDED:
            ref = self._reference(rule, tp, prefix)
            if ref is not None:
                self._add_node(path, _TWO_SIDED[cond], {}, pos)
                self._read(path, "a", series)
                self._read(path, "b", ref)
            elif rule.value is not None:
                self._add_node(path, _TWO_SIDED[cond], {"threshold": rule.value}, pos)
                self._read(path, "a", series)
            else:
                # No reference and no value: eval_rule falls through to False.
                self._never(path, pos, tp)
            return

        if cond in _SIGNAL_CONDITIONS:
            self._add_node(path, _SIGNAL_CONDITIONS[cond], {}, pos)
            self._read(path, "a", series)
            self._read(path, "b", self._macd_signal(tp, prefix))
            return

        params: dict[str, Any] = {}
        if cond in ("rising_over", "falling_over"):
            params["bars"] = _clamp_lookback(rule.value, 10)
        elif cond in ("turns_up_below", "turns_down_above"):
            if rule.value is None:
                self._never(path, pos, tp)
                return
            params["threshold"] = rule.value
        elif cond in ("turns_up", "turns_down"):
            params["bars"] = max(1, _clamp_lookback(rule.value, 1))
            if rule.threshold is not None and rule.threshold > 0:
                params["min_pct"] = rule.threshold
        self._add_node(path, cond, params, pos)
        self._read(path, "a", series)

    def _never(self, path: str, pos: tuple[float, float], tp: str) -> None:
        """A rule the rule engine never fires: "bar number below 0", False on
        every bar.  A NOT after it is True from bar 1, as in eval_rules."""
        self._add_node(path, "below", {"threshold": 0}, pos)
        self._read(path, "a", _Ref(tp, "@index"))

    def _emit_rule_set(
        self,
        side: str,
        rules: list[Rule],
        logic_op: str,
        ticker_path: str,
        prefix: str = "",
        y_base: float = 0.0,
    ) -> Optional[str]:
        """Emit indicator, comparison, NOT, and logic nodes for *rules*.

        Returns the logic node path (or None if rules is empty).
        """
        if not rules:
            return None

        logic_path = f"{prefix}/logic_{side}"
        logic_y = y_base + _ROW_PITCH * (len(rules) / 2.0)
        self._add_node(logic_path, logic_op.lower(), {}, (logic_y, _COL_LOGIC))

        terms: list[_Ref] = []
        for idx, raw_rule in enumerate(rules):
            rule = migrate_rule(raw_rule)
            row_y = y_base + idx * _ROW_PITCH

            cmp_path = f"{prefix}/cmp_{side}_{idx}"
            self._rule_node(rule, cmp_path, (row_y, _COL_COMPARISON), ticker_path, prefix)
            # A muted rule is skipped by the rule engine.  Draw it bypassed so
            # the viewer still shows it and compile leaves it out the same way.
            if rule.muted:
                self.nodes[cmp_path].bypass = True

            # --- NOT wrapper (if negated) --------------------------------
            if rule.negated:
                not_path = f"{prefix}/not_{side}_{idx}"
                self._add_node(not_path, "not", {}, (row_y, _COL_COMPARISON + 90.0))
                if rule.muted:
                    self.nodes[not_path].bypass = True
                self._read(not_path, "signal", _Ref(cmp_path))
                terms.append(_Ref(not_path))
            else:
                terms.append(_Ref(cmp_path))

        if all(r.muted for r in rules):
            # eval_rules gives False when every rule is muted.  The bypassed
            # rules leave the AND/OR with nothing (it is off, and compile
            # refuses an Entry fed by it), so a "never" term carries that False.
            never = f"{prefix}/never_{side}"
            self._never(never, (y_base + len(rules) * _ROW_PITCH, _COL_COMPARISON), ticker_path)
            terms.append(_Ref(never))
        self._combine(logic_path, logic_op.lower(), terms, (logic_y, _COL_LOGIC))
        return logic_path

    def _combine(self, path: str, op: str, terms: list[_Ref],
                 pos: tuple[float, float]) -> None:
        """*path* (already added) is the AND/OR of *terms*.

        An AND/OR node takes at most ``inputs.max`` (16) signals, but a rule
        list can hold up to 100 rules.  Longer lists are split into parts of
        at most that size, each its own AND/OR, under *path*.  AND and OR do
        not care how terms are grouped, and a part whose rules are all muted
        is off and left out like a muted rule, so the result is the same as
        one big AND/OR (eval_rules).
        """
        from nodebuilder.kernel import registry
        import nodebuilder.trading  # noqa: F401  (registers every node type)

        limit = registry.get(op).inputs.max
        if len(terms) <= limit:
            self._read(path, "terms", terms)
            return
        size = -(-len(terms) // -(-len(terms) // limit))  # even parts, none over the limit
        parts: list[_Ref] = []
        for k, start in enumerate(range(0, len(terms), size)):
            part = f"{path}_part{k}"
            self._add_node(part, op, {}, (pos[0] + k * _ROW_PITCH, pos[1] - 60.0))
            self._combine(part, op, terms[start:start + size], pos)
            parts.append(_Ref(part))
        self._combine(path, op, parts, pos)

    # ------------------------------------------------------------------
    # Regime sub-tree
    # ------------------------------------------------------------------

    def add_regime(
        self,
        regime: RegimeConfig,
        ticker: str,
        source: str,
        ticker_path: str,
        buy_logic_path: Optional[str],
        sell_logic_path: Optional[str],
        entry_path: str,
        exit_path: str,
    ) -> None:
        """Emit regime sub-tree and gate buy/sell into entry/exit via AND nodes.

        Compile refuses a graph with a regime (regime_unsupported) until W5;
        the sub-tree is drawn so the viewer shows it.
        """
        prefix = "/regime"
        y_off = _REGIME_Y_OFFSET

        # Regime ticker (same symbol, different timeframe)
        regime_ticker = self.add_ticker(ticker, regime.timeframe, source, prefix=prefix, y_offset=y_off)

        if regime.rules:
            regime_logic_path = self._emit_rule_set(
                "regime", list(regime.rules), regime.logic, regime_ticker,
                prefix=prefix, y_base=y_off,
            )
        else:
            # Legacy single-indicator regime: price above / below the
            # indicator, or the indicator rising / falling.
            ind_name, ind_params = _resolve_indicator_from_regime(regime)
            regime_ind = self.add_indicator(ind_name, ind_params, regime_ticker, prefix=prefix)
            regime_cmp_path = f"{prefix}/cmp_regime_0"
            pos = (y_off, _COL_COMPARISON)
            if regime.condition in ("rising", "falling"):
                self._add_node(regime_cmp_path, regime.condition, {}, pos)
                self._read(regime_cmp_path, "a", _Ref(regime_ind))
            else:
                cmp_type = "below" if regime.condition == "below" else "above"
                self._add_node(regime_cmp_path, cmp_type, {}, pos)
                self._read(regime_cmp_path, "a", _Ref(regime_ticker, "@close"))
                self._read(regime_cmp_path, "b", _Ref(regime_ind))

            regime_logic_path = f"{prefix}/logic_regime"
            self._add_node(regime_logic_path, "and", {}, (y_off, _COL_LOGIC))
            self._read(regime_logic_path, "terms", [_Ref(regime_cmp_path)])

        if regime_logic_path is None:
            return

        # Gate buy side
        if buy_logic_path is not None:
            gate_buy = "/and_regime_buy_gate"
            self._add_node(gate_buy, "and", {}, (0.0, _COL_REGIME_GATE))
            self._read(gate_buy, "terms", [_Ref(regime_logic_path), _Ref(buy_logic_path)])
            self._read(entry_path, "signal", _Ref(gate_buy))
        else:
            # No buy logic — wire regime directly to entry
            self._read(entry_path, "signal", _Ref(regime_logic_path))

        # Gate sell side
        if sell_logic_path is not None:
            gate_sell = "/and_regime_sell_gate"
            self._add_node(gate_sell, "and", {}, (_ROW_PITCH, _COL_REGIME_GATE))
            self._read(gate_sell, "terms", [_Ref(regime_logic_path), _Ref(sell_logic_path)])
            self._read(exit_path, "signal", _Ref(gate_sell))
        else:
            self._read(exit_path, "signal", _Ref(regime_logic_path))

    # ------------------------------------------------------------------
    # Settings nodes
    # ------------------------------------------------------------------

    def add_settings(self, req: StrategyRequest) -> None:
        y = _SETTINGS_Y_START
        b23_mode = _is_b23_mode(req)

        self._add_node(
            "/setting_position_size",
            "position_size",
            {"size": req.position_size},
            (_SETTINGS_X, y),
        )
        y += _SETTINGS_Y_PITCH

        # Stop loss — simple mode
        if not b23_mode:
            if req.stop_loss_pct is not None:
                self._add_node(
                    "/setting_stop_loss",
                    "stop_loss",
                    {"pct": req.stop_loss_pct},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH
        else:
            # Per-direction stop loss nodes
            if req.long_stop_loss_pct is not None:
                self._add_node(
                    "/setting_long_stop_loss",
                    "stop_loss",
                    {"pct": req.long_stop_loss_pct, "direction": "long"},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH
            if req.short_stop_loss_pct is not None:
                self._add_node(
                    "/setting_short_stop_loss",
                    "stop_loss",
                    {"pct": req.short_stop_loss_pct, "direction": "short"},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH

        self._add_node(
            "/setting_slippage",
            "slippage",
            {"bps": req.slippage_bps},
            (_SETTINGS_X, y),
        )
        y += _SETTINGS_Y_PITCH

        self._add_node(
            "/setting_commission",
            "commission",
            {"per_share_rate": req.per_share_rate, "min_per_order": req.min_per_order},
            (_SETTINGS_X, y),
        )
        y += _SETTINGS_Y_PITCH

        # Trailing stop (generic node, not in Core 14 — viewer falls back)
        if not b23_mode and req.trailing_stop is not None:
            self._add_node(
                "/setting_trailing_stop",
                "trailing_stop",
                req.trailing_stop.model_dump(),
                (_SETTINGS_X, y),
            )
        elif b23_mode:
            if req.long_trailing_stop is not None:
                self._add_node(
                    "/setting_long_trailing_stop",
                    "trailing_stop",
                    {**req.long_trailing_stop.model_dump(), "direction": "long"},
                    (_SETTINGS_X, y),
                )
                y += _SETTINGS_Y_PITCH
            if req.short_trailing_stop is not None:
                self._add_node(
                    "/setting_short_trailing_stop",
                    "trailing_stop",
                    {**req.short_trailing_stop.model_dump(), "direction": "short"},
                    (_SETTINGS_X, y),
                )

    # ------------------------------------------------------------------
    # Finish: store write names, then turn every read into names
    # ------------------------------------------------------------------

    def build(self) -> Graph:
        """The finished v3 graph.

        Write names come from ``assign_write_names`` (catalog defaults made
        unique in topological order, as the v2 -> v3 migration stores them).
        Each node stores its own write names, and every read is the name its
        source writes.
        """
        from nodebuilder.kernel import registry
        from nodebuilder.kernel.schema import assign_write_names
        import nodebuilder.trading  # noqa: F401  (registers every node type)

        draft = Graph.model_validate(self._dump())
        names = assign_write_names(draft)
        for node_id, slots in names.items():
            self.nodes[node_id].params.update(slots)

        def _name(ref: _Ref) -> str:
            if ref.slot is not None and ref.slot.startswith("@"):
                return ref.slot
            node = self.nodes[ref.node]
            slot = ref.slot
            if slot is None:
                nt = registry.get(node.type)
                writes = nt.write_params() if nt is not None else []
                slot = writes[0].name if writes else "out"
            return names.get(ref.node, {}).get(slot) or f"@{node.name}_{slot}"

        for node_id, reads in self.reads.items():
            params = self.nodes[node_id].params
            for param, what in reads.items():
                params[param] = [_name(r) for r in what] if isinstance(what, list) else _name(what)
        return Graph.model_validate(self._dump())

    def _dump(self) -> dict[str, Any]:
        return {
            "_version": 3,
            "stream_schema": STREAM_SCHEMA_VERSION,
            "readOnly": True,
            "nodes": {path: node.model_dump(by_alias=False) for path, node in self.nodes.items()},
            "wires": [w.model_dump(by_alias=True, exclude={"attr"}) for w in self.wires],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_b23_mode(req: StrategyRequest) -> bool:
    """True if any per-direction rule list is populated (b23 mode)."""
    return any([
        req.long_buy_rules,
        req.long_sell_rules,
        req.short_buy_rules,
        req.short_sell_rules,
    ])


def _resolve_indicator_from_regime(regime: RegimeConfig) -> tuple[str, dict[str, Any]]:
    """Resolve a single-indicator regime to (catalog_name, params)."""
    ind = regime.indicator
    ip = regime.indicator_params or {}
    if ind == "ma":
        ma_type = ip.get("type", "sma")
        node_name = "ema" if ma_type == "ema" else "sma"
        return node_name, {"period": ip.get("period", 200)}
    if ind == "rsi":
        return "rsi", {"period": ip.get("period", 14), "type": ip.get("type", "sma")}
    if ind == "macd":
        return "macd", {
            "fast": ip.get("fast", 12),
            "slow": ip.get("slow", 26),
            "signal": ip.get("signal", 9),
        }
    if ind == "bb":
        return "bollinger", {"period": ip.get("period", 20), "stddev": ip.get("stddev", 2.0)}
    if ind == "atr":
        return "atr", {"period": ip.get("period", 14)}
    return ind, dict(ip)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def auto_render(req: StrategyRequest) -> Graph:
    """Translate a StrategyRequest into a readOnly Graph for the T1 viewer.

    The returned graph has:
      - readOnly=True
      - _version=3 (stream_schema, node names, wire ports, named reads and
        writes, no wire.attr)
      - Deterministic node paths for stable test snapshots
      - No dangling wires (Pydantic model_validator enforces this)
      - No cycles (Pydantic model_validator enforces this)
    """
    ticker = req.ticker
    interval = req.interval
    source = req.source
    b23_mode = _is_b23_mode(req)
    has_regime = req.regime is not None and req.regime.enabled

    builder = _GraphBuilder(ticker, interval, source)

    # Primary ticker node
    ticker_path = builder.add_ticker(ticker, interval, source)

    # Entry / Exit terminal nodes
    entry_path = "/entry"
    exit_path = "/exit"
    # Entry at col 5 if regime (gate nodes in col 4), else col 4
    terminal_x = _COL_TERMINAL if has_regime else _COL_REGIME_GATE
    builder._add_node(entry_path, "entry", {}, (0.0, terminal_x))
    builder._add_node(exit_path, "exit", {}, (_ROW_PITCH, terminal_x))

    # Settings nodes (always)
    builder.add_settings(req)

    # --- Rule set emission -----------------------------------------------

    if not b23_mode:
        # Simple mode: buy_rules + sell_rules
        buy_logic_path = builder._emit_rule_set(
            "buy", list(req.buy_rules), req.buy_logic, ticker_path, y_base=0.0
        )
        sell_logic_path = builder._emit_rule_set(
            "sell", list(req.sell_rules), req.sell_logic, ticker_path,
            y_base=len(req.buy_rules) * _ROW_PITCH
        )

    else:
        # B23 mode: per-direction rule sets
        long_buy_logic = builder._emit_rule_set(
            "long_buy",
            list(req.long_buy_rules or []),
            req.long_buy_logic,
            ticker_path,
            y_base=0.0,
        )
        long_sell_logic = builder._emit_rule_set(
            "long_sell",
            list(req.long_sell_rules or []),
            req.long_sell_logic,
            ticker_path,
            y_base=len(req.long_buy_rules or []) * _ROW_PITCH,
        )
        short_buy_logic = builder._emit_rule_set(
            "short_buy",
            list(req.short_buy_rules or []),
            req.short_buy_logic,
            ticker_path,
            y_base=(len(req.long_buy_rules or []) + len(req.long_sell_rules or [])) * _ROW_PITCH,
        )
        short_sell_logic = builder._emit_rule_set(
            "short_sell",
            list(req.short_sell_rules or []),
            req.short_sell_logic,
            ticker_path,
            y_base=(
                len(req.long_buy_rules or [])
                + len(req.long_sell_rules or [])
                + len(req.short_buy_rules or [])
            ) * _ROW_PITCH,
        )

        # Combine per-direction logic into a single OR for entry/exit
        # Long and short buy → OR → entry
        buy_logic_path = _either(builder, "/or_b23_buy", long_buy_logic, short_buy_logic, 0.0)
        sell_logic_path = _either(builder, "/or_b23_sell", long_sell_logic, short_sell_logic,
                                  _ROW_PITCH)

    if not has_regime:
        # Direct wire: logic → terminals
        if buy_logic_path:
            builder._read(entry_path, "signal", _Ref(buy_logic_path))
        if sell_logic_path:
            builder._read(exit_path, "signal", _Ref(sell_logic_path))

    # --- Regime sub-tree -------------------------------------------------

    if has_regime:
        assert req.regime is not None
        builder.add_regime(
            req.regime,
            ticker,
            source,
            ticker_path,
            buy_logic_path,
            sell_logic_path,
            entry_path,
            exit_path,
        )

    return builder.build()


def _either(builder: _GraphBuilder, path: str, long_path: Optional[str],
            short_path: Optional[str], x: float) -> Optional[str]:
    """An OR over the long and short logic when both exist, else the one there is."""
    if long_path and short_path:
        builder._add_node(path, "or", {}, (x, _COL_LOGIC + 100))
        builder._read(path, "terms", [_Ref(long_path), _Ref(short_path)])
        return path
    return long_path or short_path
