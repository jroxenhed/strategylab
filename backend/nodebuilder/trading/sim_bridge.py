"""The simulator bridge: from a compiled graph and its cook to one simulation
(plan W5 item 5.A, D7, D8, D11).

One group of a graph runs as one call of routes.backtest._run_simulation.
This module turns the group's terminals and settings nodes into that
call's inputs:

1. ``plan_group(program, terminals, ...)`` (compile time, no data): which
   columns the simulator reads (entry, exit, a wired size or stop, the
   regime signal) and which simulator fields the graph sets (size, stop,
   trailing stop, time stop, costs).
2. ``strategy_fields(request_fields(req), plan)``: the request's fields with
   the graph's values on top (plan D11: graph node, then request field,
   then engine default).
3. ``cook(program, attrs, plan.keep_ids())``: one cook that keeps every
   terminal stream the simulation reads.
4. ``simulate(plan, result, df, attrs, sim_req)``: the signal callables and
   the size, stop and regime series, passed to ``_run_simulation``.

``run_group`` does all four for one group.  A graph with no Output Groups is
one implicit group (``GroupTerminals.from_program``), and run_group then
gives the same result the Wave 4 run.py gave.

The live bot uses the same pieces: ``apply_to_bot_config`` for the fields
and ``entry_sample`` for a wired size or stop at the entry bar, so a live
graph bot sizes and stops exactly as its backtest does.

Pure functions; cooking and simulating are CPU work, never run them on an
event loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from nodebuilder.kernel.schema import coded as _coded
from nodebuilder.models import GraphValidationError
from nodebuilder.trading.nodes_settings import trailing_stop_config
from nodebuilder.trading.nodes_terminals import TERMINAL_TYPES

# Step modes (nodebuilder.kernel.schema).  Only a running step applies: a
# bypassed settings node or trailing stop does not.
_RUN = "run"

# Group directions (plan D7).
DIRECTIONS: tuple[str, ...] = ("long", "short", "regime_switch")

# The simulator fields a graph can set, by StrategyRequest field name.
# BotConfig uses the same names, except that it has no commission fields.
GRAPH_FIELDS: tuple[str, ...] = (
    "position_size", "stop_loss_pct", "trailing_stop", "max_bars_held",
    "slippage_bps", "per_share_rate", "min_per_order", "borrow_rate_annual",
)

# Every request field the simulator reads (StrategyRequest and
# GraphBacktestRequest share the names).
SIM_FIELDS: tuple[str, ...] = (
    "initial_capital", "position_size", "stop_loss_pct", "trailing_stop",
    "max_bars_held", "slippage_bps", "commission_pct", "per_share_rate",
    "min_per_order", "borrow_rate_annual", "dynamic_sizing", "skip_after_stop",
    "trading_hours", "direction",
)

# Settings node type -> (param, field) pairs.  Settings nodes are scoped
# constants: the nearest one wins (plan D7).
SETTING_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "position_size": (("size", "position_size"),),
    "stop_loss": (("pct", "stop_loss_pct"),),
    "slippage": (("bps", "slippage_bps"),),
    "commission": (("per_share_rate", "per_share_rate"), ("min_per_order", "min_per_order")),
    "borrow_rate": (("rate", "borrow_rate_annual"),),
}
SETTING_TYPES: tuple[str, ...] = tuple(SETTING_FIELDS)

# Terminals a group has at most one of (entry and exit are counted per side).
_SINGLE_TERMINALS: tuple[str, ...] = ("size", "stop", "trailing_stop", "time_stop", "regime")

# Terminals that take a ``side`` (long | short) in a regime_switch group: one
# with no side applies to both sides, one with a side to that side only
# (the rule backtest's per-direction fields, B25).  A group has at most one
# of each kind per side.  Outside a regime_switch group side is not read.
SIDED_TERMINALS: tuple[str, ...] = ("size", "stop", "trailing_stop", "time_stop")
SIDES: tuple[str, ...] = ("long", "short")

# Terminal kind -> the request field a per-side terminal sets (with the
# side's prefix: long_position_size, short_stop_loss_pct...).
SIDE_FIELD_OF: dict[str, str] = {
    "size": "position_size", "stop": "stop_loss_pct",
    "trailing_stop": "trailing_stop", "time_stop": "max_bars_held",
}

# The per-direction request fields (StrategyRequest and BotConfig).  The
# simulator reads them only for a regime_switch group (b23_mode).
DIRECTION_FIELDS: tuple[str, ...] = tuple(
    f"{side}_{name}" for name in SIDE_FIELD_OF.values() for side in SIDES)


def terminal_side(step) -> Optional[str]:
    """A sided terminal's side ("long" / "short"), or None (both sides)."""
    side = (step.params or {}).get("side")
    return side if side in SIDES else None


# ---------------------------------------------------------------------------
# Which terminals belong to a group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupTerminals:
    """The terminal node ids of one group.

    entries / exits hold every entry and exit terminal; ``plan_group`` sorts
    them by ``side`` for a regime_switch group.  The others hold one node id,
    or None when the group has none (in a regime_switch group: the one with
    no side, which applies to both sides).  sided holds a regime_switch
    group's per-side terminals as (kind, side, node id).  Output Groups (W5
    5.B) build one of these per group from the nodes whose parent is the
    group.
    """
    entries: tuple[str, ...] = ()
    exits: tuple[str, ...] = ()
    size: Optional[str] = None
    stop: Optional[str] = None
    trailing_stop: Optional[str] = None
    time_stop: Optional[str] = None
    regime: Optional[str] = None
    sided: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def from_program(cls, program, node_ids: Optional[Iterable[str]] = None, *,
                     direction: Optional[str] = None,
                     code: str = "duplicate_terminal") -> "GroupTerminals":
        """Collect the running terminal steps of *program* (only those in
        *node_ids*, when given), in step order.

        A second size, stop, trailing_stop, time_stop or regime is refused
        (*code*: duplicate_terminal, or group_duplicate_terminal for an
        Output Group), naming the second one: which one should win is not
        something to guess.  In a regime_switch *direction* the sided kinds
        are counted per side (one with no side, one long, one short).  A
        bypassed trailing stop is not collected.
        """
        wanted = set(node_ids) if node_ids is not None else None
        switch = direction == "regime_switch"
        found: dict[str, list[str]] = {t: [] for t in TERMINAL_TYPES}
        sided: dict[tuple[str, str], list[str]] = {}
        for step in program.steps:
            if step.type not in found or step.mode != _RUN:
                continue
            if wanted is not None and step.node_id not in wanted:
                continue
            side = terminal_side(step) if switch and step.type in SIDED_TERMINALS else None
            if side is not None:
                sided.setdefault((step.type, side), []).append(step.node_id)
            else:
                found[step.type].append(step.node_id)

        def _dup(ids: list[str], label: str) -> None:
            if len(ids) > 1:
                raise _coded(GraphValidationError(
                    f"The group has more than one {label} terminal ({ids[0]!r} and {ids[1]!r}).  "
                    f"Delete one.",
                    node_id=ids[1],
                ), code)

        for kind in _SINGLE_TERMINALS:
            _dup(found[kind], kind if not switch or kind not in SIDED_TERMINALS
                 else f"{kind} (both sides)")
        for (kind, side), ids in sided.items():
            _dup(ids, f"{side} {kind}")
        return cls(
            entries=tuple(found["entry"]), exits=tuple(found["exit"]),
            sided=tuple((kind, side, ids[0]) for (kind, side), ids in sided.items()),
            **{kind: (found[kind][0] if found[kind] else None) for kind in _SINGLE_TERMINALS},
        )

    def node_ids(self) -> tuple[str, ...]:
        singles = tuple(getattr(self, k) for k in _SINGLE_TERMINALS)
        return (self.entries + self.exits + tuple(n for n in singles if n is not None)
                + tuple(nid for _k, _s, nid in self.sided))


# ---------------------------------------------------------------------------
# The plan for one group (compile time)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnRead:
    """A column the simulation reads: the terminal whose kept input stream
    holds it, and the attribute name."""
    node_id: str
    attr: str


@dataclass(frozen=True)
class GroupPlan:
    """What one group's simulation needs, worked out before any data is read.

    direction : "long", "short" or "regime_switch".
    entry, exit : the signals of a long or short group (exit None: never).
    entry_long, entry_short, exit_long, exit_short : a regime_switch group's
        signals, by side (an exit None: that side never exits on a signal).
    size, stop : a wired size or stop terminal, read at the entry bar; None
        when the size or stop is a constant (in ``fields``) or not set.
    regime, on_flip : the regime terminal's signal and flip mode.  on_flip
        is "hold" when there is no regime terminal.
    fields : the simulator fields the graph sets (GRAPH_FIELDS names); a
        field the graph does not set is absent, so the request's value
        applies.
    sources : field name -> id of the node that set it.
    shadowed : (settings node id, terminal id) pairs where a size or stop
        terminal wins over a position_size or stop_loss settings node (the
        ``setting_shadowed`` warning of plan D7).
    """
    direction: str
    entry: Optional[ColumnRead] = None
    exit: Optional[ColumnRead] = None
    entry_long: Optional[ColumnRead] = None
    entry_short: Optional[ColumnRead] = None
    exit_long: Optional[ColumnRead] = None
    exit_short: Optional[ColumnRead] = None
    size: Optional[ColumnRead] = None
    stop: Optional[ColumnRead] = None
    regime: Optional[ColumnRead] = None
    on_flip: str = "hold"
    fields: Mapping[str, Any] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    shadowed: tuple[tuple[str, str], ...] = ()

    @property
    def b23_mode(self) -> bool:
        """True for a regime_switch group: the simulator then keeps its
        skip-after-stop and dynamic-sizing counters per side, as the rule
        backtest does for a regime strategy."""
        return self.direction == "regime_switch"

    def reads(self) -> tuple[ColumnRead, ...]:
        cols = (self.entry, self.exit, self.entry_long, self.entry_short, self.exit_long,
                self.exit_short, self.size, self.stop, self.regime)
        return tuple(c for c in cols if c is not None)

    def keep_ids(self) -> set[str]:
        """The terminals whose input streams the cook must keep."""
        return {c.node_id for c in self.reads()}


def _signal_read(step) -> Optional[ColumnRead]:
    attr = (step.params or {}).get("signal")
    return ColumnRead(step.node_id, attr) if attr else None


def _value_read(step) -> Optional[ColumnRead]:
    attr = (step.params or {}).get("value")
    return ColumnRead(step.node_id, attr) if attr else None


def _one(ids: list[str], label: str, *, required: bool):
    if len(ids) > 1:
        raise _coded(GraphValidationError(
            f"The group has more than one {label} ({ids[0]!r} and {ids[1]!r}).  "
            f"Join the signals with OR into one {label}.",
            node_id=ids[1],
        ), "duplicate_terminal")
    if not ids:
        if required:
            raise _coded(GraphValidationError(f"The group has no {label} terminal."),
                         "missing_terminal")
        return None
    return ids[0]


def plan_group(
    program,
    terminals: Optional[GroupTerminals] = None,
    *,
    settings_nodes: Optional[Iterable[str]] = None,
    direction: str = "long",
) -> GroupPlan:
    """Work out one group's simulation inputs from the compiled steps.

    program        : the CompiledProgram.
    terminals      : the group's terminals; None collects every terminal of
                     the program (a graph with no Output Groups).
    settings_nodes : the settings node ids that apply to the group, from
                     the farthest scope to the nearest (the later one wins).
                     None takes every running settings node in step order,
                     which is what compile's settings list did before W5.
    direction      : the group's direction ("long", "short" or
                     "regime_switch").  A graph with no Output Groups takes
                     the request's direction.

    Precedence (plan D7, D11): a size or stop terminal wins over a
    position_size or stop_loss settings node; an empty constant on a
    terminal sets nothing.  Raises GraphValidationError (with .code) for a
    group the simulator cannot run.
    """
    if direction not in DIRECTIONS:
        raise _coded(GraphValidationError(
            f"Group direction must be one of {DIRECTIONS}, got {direction!r}."), "group_invalid")
    if terminals is None:
        terminals = GroupTerminals.from_program(program, direction=direction)
    steps = {s.node_id: s for s in program.steps}

    def _step(node_id: Optional[str]):
        if node_id is None:
            return None
        step = steps.get(node_id)
        return step if step is not None and step.mode == _RUN else None

    fields: dict[str, Any] = {}
    sources: dict[str, str] = {}

    # 1. Settings nodes (scoped constants), farthest first.
    if settings_nodes is None:
        setting_ids = [s.node_id for s in program.steps if s.type in SETTING_FIELDS and s.mode == _RUN]
    else:
        setting_ids = list(settings_nodes)
    for node_id in setting_ids:
        step = _step(node_id)
        if step is None or step.type not in SETTING_FIELDS:
            continue
        for param, name in SETTING_FIELDS[step.type]:
            value = (step.params or {}).get(param)
            if value is not None:
                fields[name] = float(value)
                sources[name] = node_id

    # 2. Terminals win over settings nodes.
    shadowed: list[tuple[str, str]] = []
    reads: dict[str, Optional[ColumnRead]] = {"size": None, "stop": None}
    for kind, name in (("size", "position_size"), ("stop", "stop_loss_pct")):
        step = _step(getattr(terminals, kind))
        if step is None:
            continue
        read = _value_read(step)
        constant = (step.params or {}).get("constant")
        if read is None and constant is None:
            continue  # an empty terminal sets nothing
        if name in sources:
            shadowed.append((sources[name], step.node_id))
        if read is not None:
            reads[kind] = read
            fields.pop(name, None)
            sources.pop(name, None)
        else:
            fields[name] = float(constant)
            sources[name] = step.node_id

    step = _step(terminals.trailing_stop)
    if step is not None:
        fields["trailing_stop"] = trailing_stop_config(dict(step.params or {}))
        sources["trailing_stop"] = step.node_id

    step = _step(terminals.time_stop)
    if step is not None and (step.params or {}).get("max_bars") is not None:
        fields["max_bars_held"] = int(step.params["max_bars"])
        sources["max_bars_held"] = step.node_id

    # 2b. Per-side terminals (regime_switch only): the per-direction request
    # fields, which the simulator reads before the shared value, as the rule
    # backtest does (B25).  Only a constant can be per side: the simulator
    # takes one wired size and one wired stop series for both sides.
    if direction == "regime_switch":
        for kind, side, node_id in terminals.sided:
            step = _step(node_id)
            if step is None:
                continue
            _side_fields(kind, side, step, reads, fields, sources)

    regime: Optional[ColumnRead] = None
    on_flip = "hold"
    step = _step(terminals.regime)
    if step is not None:
        regime = _signal_read(step)
        on_flip = (step.params or {}).get("on_flip") or "hold"

    # 3. Entry and exit signals.
    entry_steps = [s for s in (_step(n) for n in terminals.entries) if s is not None]
    exit_steps = [s for s in (_step(n) for n in terminals.exits) if s is not None]
    sig: dict[str, Optional[ColumnRead]] = {}

    def _entry(e_id: str) -> ColumnRead:
        read = _signal_read(steps[e_id])
        if read is None:
            raise _coded(GraphValidationError(
                f"Entry terminal {e_id!r} is not wired to a signal.", node_id=e_id,
            ), "missing_input", port="in0")
        return read

    if direction == "regime_switch":
        if regime is None:
            raise _coded(GraphValidationError(
                "A regime_switch group needs a regime terminal: it decides which side trades.",
                node_id=terminals.entries[0] if terminals.entries else None,
            ), "group_invalid")
        for side in ("long", "short"):
            e_ids = [s.node_id for s in entry_steps if (s.params or {}).get("side", "long") == side]
            x_ids = [s.node_id for s in exit_steps if (s.params or {}).get("side", "long") == side]
            e_id = _one(e_ids, f"{side} entry", required=True)
            x_id = _one(x_ids, f"{side} exit", required=False)
            sig[f"entry_{side}"] = _entry(e_id)
            sig[f"exit_{side}"] = _signal_read(steps[x_id]) if x_id is not None else None
    else:
        # A long or short group does not read side (S32b).
        e_id = _one([s.node_id for s in entry_steps], "entry", required=True)
        x_id = _one([s.node_id for s in exit_steps], "exit", required=False)
        sig["entry"] = _entry(e_id)
        sig["exit"] = _signal_read(steps[x_id]) if x_id is not None else None

    return GroupPlan(
        direction=direction, size=reads["size"], stop=reads["stop"],
        regime=regime, on_flip=on_flip,
        fields=fields, sources=sources, shadowed=tuple(shadowed), **sig,
    )


def _side_fields(kind: str, side: str, step, reads: Mapping[str, Optional[ColumnRead]],
                 fields: dict, sources: dict) -> None:
    """Put one per-side terminal's value into *fields* (long_stop_loss_pct
    and so on).  Refuses a wired per-side size or stop, and a per-side
    constant next to a wired shared one (the simulator reads the wired
    series at every entry, so the per-side value would be ignored)."""
    name = f"{side}_{SIDE_FIELD_OF[kind]}"
    params = step.params or {}
    if kind in ("size", "stop"):
        label = "Size" if kind == "size" else "Stop"
        if _value_read(step) is not None:
            raise _coded(GraphValidationError(
                f"{label} {step.node_id!r} is for the {side} side and reads a wired value.  "
                f"A per-side {kind} can only be a constant: clear side to use the wired "
                f"value for both sides, or delete the wire and set the constant.",
                node_id=step.node_id,
            ), "param_invalid", param="side")
        constant = params.get("constant")
        if constant is None:
            return
        if reads.get(kind) is not None:
            shared = reads[kind].node_id
            raise _coded(GraphValidationError(
                f"{label} {step.node_id!r} sets the {side} side, but {label.lower()} "
                f"{shared!r} reads a wired value for both sides, which wins at every "
                f"entry.  Give the wired {kind} a side too, or make it a constant.",
                node_id=step.node_id,
            ), "param_invalid", param="side")
        fields[name] = float(constant)
    elif kind == "trailing_stop":
        fields[name] = trailing_stop_config(dict(params))
    else:  # time_stop
        if params.get("max_bars") is None:
            return
        fields[name] = int(params["max_bars"])
    sources[name] = step.node_id


def atr_trailing(fields: Mapping[str, Any]):
    """The trailing stop a frame's attrs are built for: an ATR one when the
    shared or a per-side trailing stop is ATR based (the frame then carries
    the ATR column the simulator reads), else the shared one."""
    for name in ("trailing_stop", "long_trailing_stop", "short_trailing_stop"):
        ts = fields.get(name)
        if getattr(ts, "type", None) == "atr":
            return ts
    return fields.get("trailing_stop")


# ---------------------------------------------------------------------------
# Fields: request values with the graph's on top
# ---------------------------------------------------------------------------


def request_fields(req) -> dict[str, Any]:
    """The simulator fields of a GraphBacktestRequest (or any object or dict
    with the same field names)."""
    get = req.get if isinstance(req, Mapping) else (lambda k: getattr(req, k))
    return {name: get(name) for name in SIM_FIELDS}


def strategy_fields(base: Mapping[str, Any], plan: GroupPlan) -> dict[str, Any]:
    """*base* (request fields) with the fields the graph sets on top.

    The direction is the group's; a regime_switch group runs as "long" (the
    simulator takes each trade's side from the entry signal, as the rule
    backtest does for a regime strategy).
    """
    out = dict(base)
    out.update(plan.fields)
    out["direction"] = "long" if plan.direction == "regime_switch" else plan.direction
    return out


def to_strategy_request(fields: Mapping[str, Any], req, *, ticker: Optional[str] = None,
                        interval: Optional[str] = None):
    """The StrategyRequest _run_simulation reads, from *fields* and the
    request's window (ticker, start, end, interval, source).

    A group whose primary Ticker differs from the request passes its own
    *ticker* and *interval*.  The rule lists are empty: the simulator reads
    only the simulator fields.  StrategyRequest's validator clamps
    position_size into [0.01, 1.0].
    """
    from models import StrategyRequest

    get = req.get if isinstance(req, Mapping) else (lambda k: getattr(req, k))
    return StrategyRequest(
        ticker=ticker or get("ticker"), start=get("start"), end=get("end"),
        interval=interval or get("interval"), source=get("source"),
        buy_rules=[], sell_rules=[],
        **{name: fields[name] for name in SIM_FIELDS},
        # Per-side terminals of a regime_switch group (read only in b23_mode).
        **{name: fields[name] for name in DIRECTION_FIELDS if fields.get(name) is not None},
    )


# ---------------------------------------------------------------------------
# Cook and simulate
# ---------------------------------------------------------------------------


def cook(program, attrs: Mapping[str, Any], keep: Iterable[str] = (), *, keep_all: bool = False):
    """Cook *program* over the bars in *attrs* (as build_graph_attrs makes
    them, with the reference frames it carries), keeping the streams of the
    nodes in *keep* (pass ``plan.keep_ids()``, or the union over every
    group).  The same cook run.cook_attrs makes, plus the kept terminals."""
    from nodebuilder import evaluator as _evaluator

    return _evaluator.cook_attrs(program, attrs, keep=set(keep), keep_all=keep_all)


def _column(result, read: Optional[ColumnRead], n: int, dtype) -> Optional[np.ndarray]:
    if read is None:
        return None
    values = np.asarray(result.column(read.node_id, read.attr), dtype=dtype)
    if values.shape != (n,):
        raise ValueError(f"{read.attr} from {read.node_id!r} has {values.shape} values, "
                         f"but the frame has {n} bars")
    return values


def signal_fns(plan: GroupPlan, result, n: int):
    """The (buy_signal_fn, sell_signal_fn) pair _run_simulation calls.

    Long or short group:
      - no regime: the entry and exit columns, in the group's direction;
      - with a regime: an entry needs the regime signal true, except with
        on_flip close_and_reverse, where the entry side follows the regime
        (the group's direction while it is true, the other side while it is
        false).  These are the rule backtest's single-rule-set regime rules.
    regime_switch group: while the regime is true the long entry opens a
    long; while it is false the short entry opens a short.  An open trade
    exits on its own side's exit.
    """
    false = np.zeros(n, dtype=bool)

    def col(read):
        values = _column(result, read, n, bool)
        return false if values is None else values

    if plan.direction == "regime_switch":
        entry_long, entry_short = col(plan.entry_long), col(plan.entry_short)
        exit_long, exit_short = col(plan.exit_long), col(plan.exit_short)

        def buy_switch(i: int, curr_regime_active: bool):
            if curr_regime_active:
                return bool(entry_long[i]), [], "long"
            return bool(entry_short[i]), [], "short"

        def sell_switch(i: int, position_direction, curr_regime_active: bool):
            if position_direction == "long":
                return bool(exit_long[i]), []
            if position_direction == "short":
                return bool(exit_short[i]), []
            return False, []

        return buy_switch, sell_switch

    entry_col, exit_col = col(plan.entry), col(plan.exit)
    direction = plan.direction

    def sell_signal_fn(i: int, position_direction, curr_regime_active: bool):
        return bool(exit_col[i]), []

    if plan.regime is None:
        # The Wave 4 callables, unchanged: no regime, one direction.
        def buy_signal_fn(i: int, curr_regime_active: bool):
            return bool(entry_col[i]), [], direction

        return buy_signal_fn, sell_signal_fn

    if plan.on_flip == "close_and_reverse":
        other = "short" if direction == "long" else "long"

        def buy_reverse(i: int, curr_regime_active: bool):
            fired = bool(entry_col[i])
            side = direction if curr_regime_active else other
            return fired, [], side if fired else direction

        return buy_reverse, sell_signal_fn

    def buy_gated(i: int, curr_regime_active: bool):
        return bool(curr_regime_active and entry_col[i]), [], direction

    return buy_gated, sell_signal_fn


def simulate(plan: GroupPlan, result, df: pd.DataFrame, indicators: Mapping[str, Any], sim_req,
             date_strs: Optional[list] = None) -> dict:
    """Run one group's simulation over *df* with the cook *result*.

    *indicators* is the attrs dict the cook used (it carries ``atr`` for an
    ATR trailing stop); *sim_req* comes from to_strategy_request.  Returns
    _run_simulation's dict (summary, trades, equity_curve).
    """
    from routes.backtest import _run_simulation

    n = len(df)
    buy_signal_fn, sell_signal_fn = signal_fns(plan, result, n)
    regime = _column(result, plan.regime, n, bool)
    return _run_simulation(
        df=df,
        indicators=indicators,
        buy_signal_fn=buy_signal_fn,
        sell_signal_fn=sell_signal_fn,
        req=sim_req,
        b23_mode=plan.b23_mode,
        regime_active_series=pd.Series(regime, index=df.index) if regime is not None else None,
        on_flip=plan.on_flip if regime is not None else "hold",
        date_strs=date_strs,
        size_series=_column(result, plan.size, n, float),
        stop_series=_column(result, plan.stop, n, float),
    )


@dataclass
class GroupRun:
    """One group's run: its plan, the fields it ran with, the request the
    simulator read, the attrs and cook it used, and the simulation dict."""
    plan: GroupPlan
    fields: dict
    sim_req: Any
    attrs: dict
    result: Any
    sim: dict


def run_group(program, df: pd.DataFrame, req, *, plan: Optional[GroupPlan] = None,
              keep_all: bool = False, date_strs: Optional[list] = None,
              ticker: Optional[str] = None, interval: Optional[str] = None,
              frames: Optional[Mapping[tuple, pd.DataFrame]] = None) -> GroupRun:
    """Plan (unless given), cook and simulate one group.

    *req* is the GraphBacktestRequest (or a dict with its fields).  With no
    *plan*, the whole graph is one group in the request's direction.
    keep_all keeps every node's stream (the editor's inspector cook).

    A group that reads reference Tickers (plan D8) takes their frames from
    *frames* ((SYMBOL, interval) -> DataFrame); any it lacks is fetched
    with lookback padding over the request's window and source
    (run.fetch_reference_frames), which is what a bot's backtest does.
    """
    from nodebuilder.prepare import build_graph_attrs

    if plan is None:
        plan = plan_group(program, direction=request_fields(req)["direction"])
    fields = strategy_fields(request_fields(req), plan)
    get = req.get if isinstance(req, Mapping) else (lambda k: getattr(req, k))
    run_interval = interval or get("interval")
    from nodebuilder.trading.align import ticker_roles

    if ticker_roles(program).references:
        from nodebuilder.run import fetch_reference_frames
        from shared import require_valid_source

        frames = fetch_reference_frames(program, get("start"), get("end"),
                                        require_valid_source(get("source")), given=frames)
    attrs = build_graph_attrs(program, df, atr_trailing(fields), frames=frames,
                              interval=run_interval)
    result = cook(program, attrs, plan.keep_ids(), keep_all=keep_all)
    sim_req = to_strategy_request(fields, req, ticker=ticker, interval=interval)
    sim = simulate(plan, result, df, attrs, sim_req, date_strs)
    return GroupRun(plan=plan, fields=fields, sim_req=sim_req, attrs=attrs, result=result, sim=sim)


# ---------------------------------------------------------------------------
# The live bot (W5 5.D)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntrySample:
    """A wired size and stop at one bar, as the simulator reads them at an
    entry.  size / stop_pct are None when that terminal is not wired (the
    bot config's value applies).  blocked is "size" or "stop" when the entry
    must not open (see routes.backtest.series_entry_block)."""
    size: Optional[float]
    stop_pct: Optional[float]
    blocked: Optional[str]


def entry_sample(plan: GroupPlan, result, i: int = -1) -> EntrySample:
    """The wired size and stop at bar *i* of a cook (the last bar by
    default), with the simulator's own rules, so a live entry sizes and
    stops as the backtest's entry on that bar did."""
    from routes.backtest import series_entry_block, series_entry_size

    size_v = float(result.column(plan.size.node_id, plan.size.attr)[i]) if plan.size else None
    stop_v = float(result.column(plan.stop.node_id, plan.stop.attr)[i]) if plan.stop else None
    blocked = series_entry_block(size_v, stop_v)
    if blocked is not None:
        return EntrySample(None, None, blocked)
    return EntrySample(
        size=series_entry_size(size_v) if size_v is not None else None,
        stop_pct=stop_v,
        blocked=None,
    )


def apply_to_bot_config(cfg, plan: GroupPlan):
    """A copy of a graph bot's config with the group's fields applied.

    The graph wins.  Fields the bot config does not have (the commission
    fields) are skipped, and every per-direction field is cleared, as
    nodebuilder.sim_settings.apply_to_bot_config does, then set again from
    the group's per-side terminals (a regime_switch group), so the bot's
    per-direction fields are exactly the graph's.  Built with
    model_validate, so BotConfig's position_size clamp runs (never
    model_copy(update=...): a size of 100 would buy 100 times the capital).
    A wired size or stop is not a config field: read it per entry with
    entry_sample.
    """
    from nodebuilder.sim_settings import BOT_DIRECTION_FIELDS

    cls = type(cfg)
    names = cls.model_fields
    overrides: dict[str, Any] = {}
    for name in BOT_DIRECTION_FIELDS:
        if name in names:
            overrides[name] = None
    overrides.update({k: v for k, v in plan.fields.items() if k in names})
    data = cfg.model_dump(exclude={"graph"})
    data.update(overrides)
    if "graph" in names:
        data["graph"] = cfg.graph
    return cls.model_validate(data)
