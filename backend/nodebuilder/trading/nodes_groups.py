"""Output Groups (plan D7, W5 item 5.B).

An Output Group is a network node (``output_group``).  Its children are one
strategy leg: an Entry and an Exit (one of each per side in a
``regime_switch`` group), plus at most one size, stop, trailing stop, time
stop and regime terminal.  Its params say how the leg trades:

- ``direction``: ``long``, ``short`` or ``regime_switch``;
- ``ticker``: a path to the leg's primary Ticker node (tried relative to the
  group, then the group's parent, then the root, as the frontend does);
- ``capital_weight``: the leg's share of the capital (default 1).

A graph with no Output Group is one implicit group called ``main``.  Its
direction, symbol and interval come from the request, so graphs saved
before W5 and auto-rendered rule strategies keep working unchanged.  Its
Tickers with no prefix all read the request's frame (plan D11); a Ticker
with a prefix is a reference Ticker on its own symbol and interval (plan
D8).  Its primary_ticker_id names the first root Ticker with no prefix (the
one the editor and the spawn dialog show), for display only.  In a
graph that has groups, a terminal outside every group is an error
(``group_terminal_outside``).

Compile uses this module twice:

1. ``layout_groups`` (step 4 of compile, before the program exists): finds
   the groups, checks their params, sorts the terminals into them and
   checks each group's Entry and Exit signals.
2. ``build_group_programs`` (after the program is built): one
   ``GroupProgram`` per group, with the simulator plan from
   ``sim_bridge.plan_group``.

run.py then runs one simulation per group, and ``combine`` adds the legs
into one combined result.  The kernel never imports this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from nodebuilder.kernel.flatten import NETWORK_META
from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.kernel.schema import BROKEN, DISABLED, LIVE, SKIPPED, coded
from nodebuilder.evaluator import MissingTerminalError
from nodebuilder.models import GraphValidationError
from nodebuilder.trading import sim_bridge as sb
from nodebuilder.trading.nodes_terminals import TERMINAL_TYPES

# The node type name, and the name of the implicit group (plan D7).
GROUP_TYPE = "output_group"
IMPLICIT_GROUP = "main"

# Group directions, in the order the header's select shows them.
DIRECTIONS: tuple[str, ...] = sb.DIRECTIONS

# The meta key that marks the Output Group type (on top of the network mark
# that kernel/flatten.py reads).
GROUP_META = "output_group"

# Diagnostic codes this module emits besides the shared ones (plan 4.2;
# registered in nodebuilder.diagnostics).
GROUP_DUPLICATE_CODE = "group_duplicate_terminal"
WEIGHT_ZERO_CODE = "group_weight_zero"
SETTING_UNSCOPED_CODE = "setting_unscoped"

# The simulator fields that are costs (D7 "scoped cost constants").
COST_FIELDS: tuple[str, ...] = ("slippage_bps", "per_share_rate", "min_per_order", "borrow_rate_annual")

# Entry types an exit closes, as the simulator writes them in trades.
_ENTRY_TRADES = ("buy", "short")
_EXIT_TRADES = ("sell", "cover")

# A group's input ports: streams from outside come in here and show up
# inside on Subnet inputs (as for a Subnet).  Defined here, not imported from
# nodes_network, so importing this module never changes the order in which
# node modules register (the catalog order, F435 W2 KC-6).
_GROUP_INPUTS = PortsSpec(ports=(PortSpec("in0", optional=True),), dynamic=True, min=0, max=32)


def _pass_through(inputs, params):
    # Never runs: flatten takes network nodes out before compile.
    return inputs


def _not_flattened(ctx) -> None:
    ctx.fail(
        "boundary_invalid",
        f"{ctx.node.type} {ctx.node_id!r} reached compile without being flattened.  "
        "This is a bug: the graph must go through flatten() first.",
    )


register_node(
    name=GROUP_TYPE, cat="network",
    desc="Output group. One strategy leg: its terminals, primary Ticker, direction and capital share.",
    params=(
        ParamSpec("direction", "select", "direction", "long", options=DIRECTIONS, code_able=False),
        ParamSpec("ticker", "path", "ticker", "", code_able=False),
        ParamSpec("capital_weight", "number", "capital weight", 1.0, min=0.0, step=0.1,
                  code_able=False),
    ),
    inputs=_GROUP_INPUTS, impl=_pass_through, check=_not_flattened,
    has_output=False, bypassable=False,
    reads=(), writes=(), subtitle="Output group", ins=0, outs=0,
    meta={NETWORK_META: True, GROUP_META: True}, module=__name__,
)


# ---------------------------------------------------------------------------
# Compile step 4: find the groups and check their terminals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupSpec:
    """One group as compile sees it before the program is built.

    node_id   : the output_group node id; None for the implicit group.
    direction : the group's direction; None for the implicit group (the
                request's direction applies).
    symbol, interval : from the primary Ticker; None for the implicit group
                (the request's ticker and interval apply).
    terminals : the group's terminal ids (flat ids), in walk order.
    settings  : the settings node ids in scope, farthest first; None for the
                implicit group (every settings node, in step order).
    exit_connected : True when an Exit of the group gets a signal.
    """
    name: str
    node_id: Optional[str]
    path: str
    direction: Optional[str]
    weight: float
    primary_ticker_id: Optional[str]
    symbol: Optional[str]
    interval: Optional[str]
    terminals: tuple[str, ...]
    settings: Optional[tuple[str, ...]]
    exit_connected: bool
    implicit: bool


@dataclass
class GroupLayout:
    """What layout_groups found.  entry_* / exit_* are the first group's
    (long side) signals, for the CompiledProgram's legacy fields."""
    specs: list[GroupSpec] = field(default_factory=list)
    entry_attr: Optional[str] = None
    entry_node: Optional[str] = None
    exit_attr: Optional[str] = None
    exit_node: Optional[str] = None


def _node_path(graph, node_id: str) -> str:
    from nodebuilder.migrate import node_path
    try:
        return node_path(graph, node_id)
    except KeyError:
        return "/" + node_id


def _find_ticker(graph, group) -> Optional[str]:
    """The id of the Ticker node the group's ``ticker`` path points at:
    tried relative to the group, then its parent, then the root."""
    from nodebuilder.migrate import find_by_path

    path = (group.params or {}).get("ticker")
    if not isinstance(path, str) or not path.strip():
        return None
    for base in (group.id, group.parent, None):
        nid = find_by_path(graph, path.strip(), base)
        if nid is not None and graph.nodes[nid].type == "ticker":
            return nid
    return None


def _weight(value: Any) -> Optional[float]:
    """A capital weight: a finite number, 0 or above (0: the group gets no
    capital and does not trade, S32a), else None."""
    if isinstance(value, bool):
        return None
    try:
        w = float(value)
    except (TypeError, ValueError):
        return None
    return w if math.isfinite(w) and w >= 0 else None


def layout_groups(graph, flat, analysis, record: Callable, warn: Callable) -> GroupLayout:
    """Find the groups of *graph* and check their terminals (compile step 4).

    graph    : the source graph (what the user sees).
    flat     : kernel.flatten.FlatGraph of it.
    analysis : the kernel check of the flat graph.
    record(exc) / warn(code, message, node_id) add a problem.

    The implicit group gives exactly the messages compile gave before W5.
    """
    nodes = analysis.nodes
    flat_nodes = analysis.graph.nodes
    terminal_ids = [nid for nid in analysis.order if flat_nodes[nid].type in TERMINAL_TYPES]
    group_nodes = [n for n in graph.nodes.values() if n.type == GROUP_TYPE]
    layout = GroupLayout()

    if not group_nodes:
        entries = [nid for nid in terminal_ids if flat_nodes[nid].type == "entry"]
        exits = [nid for nid in terminal_ids if flat_nodes[nid].type == "exit"]
        sig = _check_signals(nodes, entries, exits, record, warn, owner=None, side=None)
        layout.entry_attr, layout.entry_node, layout.exit_attr, layout.exit_node = sig
        layout.specs.append(GroupSpec(
            name=IMPLICIT_GROUP, node_id=None, path="/", direction=None, weight=1.0,
            primary_ticker_id=_implicit_primary(graph), symbol=None, interval=None,
            terminals=tuple(terminal_ids), settings=None,
            exit_connected=sig[2] is not None, implicit=True,
        ))
        return layout

    # Sort every terminal into its nearest group.
    group_ids = {n.id for n in group_nodes}
    members: dict[str, list[str]] = {gid: [] for gid in group_ids}
    for nid in terminal_ids:
        owner = next((a for a in flat.ancestors(nid) if a in group_ids), None)
        if owner is None:
            record(coded(GraphValidationError(
                f"{flat_nodes[nid].type.replace('_', ' ').capitalize()} {flat.to_source(nid)!r} "
                "is outside every output group.  In a graph with output groups, every "
                "terminal belongs to a group: move it into one.",
                node_id=flat.to_source(nid),
            ), "group_terminal_outside"))
            continue
        members[owner].append(nid)

    settings_ids = [nid for nid in analysis.order if flat_nodes[nid].type in sb.SETTING_TYPES]
    seen_names: dict[str, str] = {}
    first = True
    for g in group_nodes:
        params = g.params or {}
        name = g.name or g.id
        ok = True
        outer = [a for a in _ancestors(graph, g.id) if a in group_ids]
        if outer:
            record(coded(GraphValidationError(
                f"Output group {name!r} is inside output group {graph.nodes[outer[0]].name!r}.  "
                "Groups cannot be nested.", node_id=g.id), "group_invalid"))
            ok = False
        if name in seen_names:
            record(coded(GraphValidationError(
                f"Two output groups are called {name!r}.  Rename one: bots and results "
                "name a group by its name.", node_id=g.id), "group_invalid"))
            ok = False
        seen_names.setdefault(name, g.id)
        direction = params.get("direction", "long")
        if direction not in DIRECTIONS:
            record(coded(GraphValidationError(
                f"Output group {name!r}: direction must be one of {', '.join(DIRECTIONS)} "
                f"(got {direction!r}).", node_id=g.id), "param_invalid", param="direction"))
            ok = False
        weight = _weight(params.get("capital_weight", 1.0))
        if weight is None:
            record(coded(GraphValidationError(
                f"Output group {name!r}: capital weight must be a number, 0 or above "
                f"(got {params.get('capital_weight')!r}).", node_id=g.id),
                "param_invalid", param="capital_weight"))
            ok = False
        elif weight == 0:
            warn(WEIGHT_ZERO_CODE, f"Output group {name!r} has capital weight 0: it gets no "
                 "capital and will not trade.", g.id)
        ticker_id = _find_ticker(graph, g)
        if ticker_id is None:
            path = params.get("ticker")
            why = ("has no primary Ticker" if not (isinstance(path, str) and path.strip())
                   else f"points at {path!r}, which is not a Ticker node")
            record(coded(GraphValidationError(
                f"Output group {name!r} {why}.  Pick the Ticker this leg trades in the "
                "group header.", node_id=g.id), "ticker_missing", param="ticker"))
            ok = False

        mine = members[g.id]
        if not mine:
            record(MissingTerminalError(f"Output group {name!r} has no Entry terminal.",
                                        node_id=g.id))
            continue
        entries = [nid for nid in mine if flat_nodes[nid].type == "entry"]
        exits = [nid for nid in mine if flat_nodes[nid].type == "exit"]
        exit_connected = False
        if direction == "regime_switch":
            sides = []
            for side in ("long", "short"):
                e_side = [n for n in entries if _side(flat_nodes[n]) == side]
                x_side = [n for n in exits if _side(flat_nodes[n]) == side]
                sides.append(_check_signals(nodes, e_side, x_side, record, warn,
                                            owner=(name, g.id), side=side))
            sig = sides[0]
            exit_connected = any(s[2] is not None for s in sides)
        else:
            sig = _check_signals(nodes, entries, exits, record, warn, owner=(name, g.id), side=None)
            exit_connected = sig[2] is not None
        if first:
            layout.entry_attr, layout.entry_node, layout.exit_attr, layout.exit_node = sig
            first = False
        if not ok:
            continue
        ticker = graph.nodes[ticker_id]
        chain = (g.id,) + _ancestors(graph, g.id)
        layout.specs.append(GroupSpec(
            name=name, node_id=g.id, path=_node_path(graph, g.id), direction=direction,
            weight=float(weight), primary_ticker_id=ticker_id,
            symbol=str((ticker.params or {}).get("symbol") or "").strip().upper() or None,
            interval=str((ticker.params or {}).get("interval") or "").strip() or None,
            terminals=tuple(mine),
            settings=_scoped_settings(flat, settings_ids, chain),
            exit_connected=exit_connected, implicit=False,
        ))
    _check_scopes(graph, flat, group_nodes, settings_ids, layout, warn)
    if layout.specs and all(spec.weight == 0 for spec in layout.specs) \
            and len(layout.specs) == len(group_nodes):
        first_spec = layout.specs[0]
        record(coded(GraphValidationError(
            "Every output group has capital weight 0, so nothing would trade.  Give at "
            "least one group a weight above 0.", node_id=first_spec.node_id),
            "param_invalid", param="capital_weight"))
    return layout


def _check_scopes(graph, flat, group_nodes, settings_ids, layout: GroupLayout,
                  warn: Callable) -> None:
    """Warn (setting_unscoped) for a settings node that applies to no
    group: one inside a network that no group's scope chain reaches, for
    example a Subnet inside an Output Group (KA-7; plan D7: a settings node
    applies to every group in its network and below)."""
    group_ids = {n.id for n in group_nodes}
    chains = [(n.id,) + _ancestors(graph, n.id) for n in group_nodes]
    scoped: set[str] = set()
    for chain in chains:
        scoped.update(_scoped_settings(flat, settings_ids, chain))
    for nid in settings_ids:
        if nid in scoped:
            continue
        net = flat.network_of(nid)
        inside = next((a for a in flat.ancestors(nid) if a in group_ids), None)
        where = (f"inside network {net!r} of output group "
                 f"{(graph.nodes[inside].name or inside)!r}" if inside is not None
                 else f"inside network {net!r}")
        warn(SETTING_UNSCOPED_CODE,
             f"Setting {flat.to_source(nid)!r} sits {where}, so it applies to no output "
             "group.  Move it to the group itself, a network around it, or the root.",
             flat.to_source(nid))


def _implicit_primary(graph) -> Optional[str]:
    """The implicit group's primary Ticker: the first root Ticker with no
    prefix (graphGroups.ts and routes.graphs.implicit_ticker pick the same)."""
    from nodebuilder.trading.nodes_data import ticker_prefix

    for node in graph.nodes.values():
        if node.type == "ticker" and node.parent is None and not ticker_prefix(node.params or {}):
            return node.id
    return None


def _side(node) -> str:
    side = (node.params or {}).get("side")
    return side if side in ("long", "short") else "long"


def _ancestors(graph, node_id: str) -> tuple[str, ...]:
    out: list[str] = []
    current = graph.nodes[node_id].parent if node_id in graph.nodes else None
    while current is not None and current not in out and current in graph.nodes:
        out.append(current)
        current = graph.nodes[current].parent
    return tuple(out)


def _scoped_settings(flat, settings_ids: Sequence[str], chain: tuple[str, ...]) -> tuple[str, ...]:
    """The settings nodes that apply to a group whose network chain (the
    group, then the networks around it, nearest first) is *chain*: those at
    the root and those in a network of the chain.  Farthest scope first, so
    the nearest one wins (plan D7); step order inside one scope."""
    depth_of = {net: len(chain) - i for i, net in enumerate(chain)}  # outermost = 1
    picked: list[tuple[int, int, str]] = []
    for order, nid in enumerate(settings_ids):
        net = flat.network_of(nid)
        if net is None:
            picked.append((0, order, nid))
        elif net in depth_of:
            picked.append((depth_of[net], order, nid))
    return tuple(nid for _d, _o, nid in sorted(picked))


def _check_signals(nodes, entries, exits, record, warn, *, owner, side):
    """One group's (or one side's) Entry and Exit checks.

    Returns (entry_attr, entry_node, exit_attr, exit_node).  owner is None
    for the implicit group, which keeps the exact pre-W5 messages.
    """
    if owner is None:
        where, node_for_missing = "The graph", None
    else:
        where, node_for_missing = f"Output group {owner[0]!r}", owner[1]
    label_entry = "Entry" if side is None else f"{side} Entry"
    label_exit = "Exit" if side is None else f"{side} Exit"

    for label, terminals in ((label_entry, entries), (label_exit, exits)):
        if owner is None:
            for nid in terminals[1:]:
                record(coded(GraphValidationError(
                    f"{where} has more than one {label} ({terminals[0]!r} and {nid!r}).  "
                    f"Join the signals with OR into one {label}.",
                    node_id=nid,
                ), "duplicate_terminal"))
            continue
        # An Output Group (plan 4.2 group_duplicate_terminal): every one of
        # the terminals gets the error, naming the other (S32b).
        if len(terminals) > 1:
            for nid in terminals:
                other = next(t for t in terminals if t != nid)
                record(coded(GraphValidationError(
                    f"{where} has more than one {label}: {nid!r} and {other!r}.  "
                    f"Join the signals with OR into one {label}.",
                    node_id=nid,
                ), GROUP_DUPLICATE_CODE))

    entry_attr = entry_node = None
    if not entries:
        if owner is None:
            record(MissingTerminalError("Graph has no Entry terminal (no 'entry' node found)."))
        else:
            record(MissingTerminalError(f"{where} has no {label_entry} terminal.",
                                        node_id=node_for_missing))
    else:
        nid = entries[0]
        res = nodes.get(nid)
        if res is not None and res.status not in (BROKEN, SKIPPED):
            info = res.reads.get("signal")
            if info is not None and info.status == LIVE:
                entry_attr, entry_node = info.name, nid
            elif info is not None and info.status == DISABLED:
                record(coded(MissingTerminalError(
                    f"Entry terminal {nid!r} gets no signal: its input is bypassed.",
                    node_id=nid,
                ), "missing_input", port="in0"))
            else:
                record(coded(MissingTerminalError(
                    f"Entry terminal {nid!r} is not wired to a signal.",
                    node_id=nid,
                ), "missing_input", port="in0"))

    exit_attr = exit_node = None
    if not exits:
        # Not an error: stops, trailing stops or the end of the data close
        # the trade.  But a strategy with no exit signal is often a mistake.
        if owner is None:
            warn("exit_unconnected", "The graph has no Exit, so only a stop or "
                 "the end of the data closes a trade.", None)
        else:
            warn("exit_unconnected", f"{where} has no {label_exit}, so only a stop or "
                 "the end of the data closes its trades.", node_for_missing)
    else:
        nid = exits[0]
        res = nodes.get(nid)
        if res is not None and res.status not in (BROKEN, SKIPPED):
            info = res.reads.get("signal")
            if info is not None and info.status == LIVE:
                exit_attr, exit_node = info.name, nid
            else:
                warn("exit_unconnected", f"Exit {nid!r} gets no signal, so only "
                     "a stop or the end of the data closes a trade.", nid)
    return entry_attr, entry_node, exit_attr, exit_node


# ---------------------------------------------------------------------------
# After the program is built: one GroupProgram per group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupProgram:
    """One group of a compiled graph (plan D7 ``CompiledProgram.groups``).

    name, node_id, path : the group (node_id None and path "/" for the
        implicit ``main`` group).
    direction, symbol, interval : None for the implicit group, where the
        request's direction, ticker and interval apply (use ``resolve``).
    weight    : capital_weight; the group's capital is
        ``initial_capital * weight / sum(weights)``.
    terminals : the group's terminals (sim_bridge.GroupTerminals).
    settings  : the settings nodes in scope, farthest first (None: every
        settings node, the implicit group).
    plan      : the simulator plan (sim_bridge.GroupPlan).  For the implicit
        group it is built as "long"; ``plan_for(direction)`` gives the plan
        for the request's direction.
    step_ids  : every node the group's terminals depend on (what a cook of
        this group alone must run).
    references: the reference Tickers the group reads (align.ReferenceTicker,
        plan D8), in step order: every Ticker in step_ids but the primary
        one; for the implicit group, every Ticker with a prefix (one with
        no prefix reads the request's frame, plan D11).  Each is fetched on
        its own symbol and interval and aligned onto the group's bars.
    """
    name: str
    node_id: Optional[str]
    path: str
    direction: Optional[str]
    weight: float
    primary_ticker_id: Optional[str]
    symbol: Optional[str]
    interval: Optional[str]
    terminals: Any
    settings: Optional[tuple[str, ...]]
    plan: Any
    step_ids: frozenset
    exit_connected: bool
    implicit: bool
    references: tuple = ()

    # D7's field names, read from the plan.
    @property
    def entry_attrs(self) -> tuple[str, ...]:
        p = self.plan
        return tuple(c.attr for c in (p.entry, p.entry_long, p.entry_short) if c is not None)

    @property
    def exit_attrs(self) -> tuple[str, ...]:
        p = self.plan
        return tuple(c.attr for c in (p.exit, p.exit_long, p.exit_short) if c is not None)

    @property
    def regime_attr(self) -> Optional[str]:
        return self.plan.regime.attr if self.plan.regime is not None else None

    @property
    def on_flip(self) -> str:
        return self.plan.on_flip

    @property
    def costs(self) -> dict[str, Any]:
        return {k: v for k, v in self.plan.fields.items() if k in COST_FIELDS}

    def resolve(self, ticker: str, interval: str, direction: str) -> tuple[str, str, str]:
        """(symbol, interval, direction) this group trades, given the
        request's (or the bot config's) values for the implicit group."""
        return (self.symbol or ticker, self.interval or interval, self.direction or direction)

    def plan_for(self, direction: Optional[str] = None):
        """The simulator plan in *direction* (the implicit group takes the
        request's direction; an explicit group ignores it).  Raises
        GraphValidationError when the group cannot run that way."""
        want = self.direction or direction or "long"
        if want == self.plan.direction:
            return self.plan
        if want in ("long", "short") and self.plan.direction in ("long", "short"):
            return replace(self.plan, direction=want)
        return sb.plan_group(self._program, self.terminals, settings_nodes=self.settings,
                             direction=want)

    # Set by build_group_programs (needed only to re-plan a direction).
    _program: Any = field(default=None, repr=False, compare=False)


def _upstream(program, roots: Iterable[str]) -> frozenset:
    by_id = {s.node_id: s for s in program.steps}
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in by_id:
            continue
        seen.add(nid)
        stack.extend(by_id[nid].depends_on())
    return frozenset(seen)


def build_group_programs(program, layout: GroupLayout, record: Callable, warn: Callable) -> tuple:
    """One GroupProgram per group of *layout*.  Problems the simulator plan
    finds (a second size terminal, a regime_switch group with no regime
    terminal...) go to *record*; a size or stop terminal that overrides a
    settings node gives the ``setting_shadowed`` warning."""
    out = []
    for spec in layout.specs:
        try:
            if spec.implicit:
                terminals = sb.GroupTerminals.from_program(program)
            else:
                terminals = sb.GroupTerminals.from_program(
                    program, node_ids=spec.terminals, direction=spec.direction,
                    code=GROUP_DUPLICATE_CODE)
            plan = sb.plan_group(program, terminals, settings_nodes=spec.settings,
                                 direction=spec.direction or "long")
        except GraphValidationError as exc:
            if exc.node_id is None and spec.node_id is not None:
                exc.node_id = spec.node_id
            record(exc)
            continue
        step_ids = _upstream(program, terminals.node_ids())
        if _ticker_clash(program, step_ids, spec, record):
            continue
        for setting_id, terminal_id in plan.shadowed:
            warn("setting_shadowed",
                 f"Setting {setting_id!r} is ignored for group {spec.name!r}: "
                 f"terminal {terminal_id!r} sets the same value.", setting_id)
        out.append(GroupProgram(
            name=spec.name, node_id=spec.node_id, path=spec.path, direction=spec.direction,
            weight=spec.weight, primary_ticker_id=spec.primary_ticker_id,
            symbol=spec.symbol, interval=spec.interval, terminals=terminals,
            settings=spec.settings, plan=plan, step_ids=step_ids,
            exit_connected=spec.exit_connected, implicit=spec.implicit,
            references=_group_references(program, step_ids, spec), _program=program,
        ))
    return tuple(out)


def _group_tickers(program, step_ids) -> list:
    return [s for s in program.steps
            if s.node_id in step_ids and s.type == "ticker" and s.mode == "run"]


def _group_references(program, step_ids, spec) -> tuple:
    """The reference Tickers a group reads (GroupProgram.references)."""
    from nodebuilder.trading.align import reference_of
    from nodebuilder.trading.nodes_data import ticker_prefix

    out = []
    for s in _group_tickers(program, step_ids):
        if spec.implicit:
            if ticker_prefix(s.params or {}):
                out.append(reference_of(s))
        elif s.node_id != spec.primary_ticker_id:
            out.append(reference_of(s))
    return tuple(out)


def _ticker_clash(program, step_ids, spec, record: Callable) -> bool:
    """Refuse a group that reads two Tickers writing the same names (two
    Tickers with no prefix, or with the same prefix): which bars @close
    means would depend on the wiring.  The error is on the second Ticker in
    step order (attr_clash, param prefix).  In the implicit group two
    Tickers with no prefix both read the request's frame, as they always
    have, so only a pair with a prefixed Ticker is checked.  True when
    refused."""
    from nodebuilder.trading.nodes_data import ticker_prefix

    seen: list = []
    for s in _group_tickers(program, step_ids):
        names = set(s.writes)
        prefixed = bool(ticker_prefix(s.params or {}))
        for other, other_names, other_prefixed in seen:
            if spec.implicit and not (prefixed or other_prefixed):
                continue
            both = sorted(names & other_names)
            if not both:
                continue
            where = (f"Output group {spec.name!r}" if not spec.implicit else "The graph")
            record(coded(GraphValidationError(
                f"{where} reads Ticker {other.node_id!r} and Ticker {s.node_id!r}, which "
                f"both write {both[0]}, so {both[0]} would mean either one.  Give "
                f"{s.node_id!r} its own prefix (for example "
                f"{_prefix_hint(s)}) so its bars are named @{_prefix_hint(s)}_close and so on.",
                node_id=s.node_id), "attr_clash", param="prefix"))
            return True
        seen.append((s, names, prefixed))
    return False


def _prefix_hint(step) -> str:
    import re

    sym = str((step.params or {}).get("symbol") or "").strip().lower()
    hint = re.sub(r"[^a-z0-9_]", "_", sym)[:16]
    if not hint or hint[0].isdigit():
        hint = ("t_" + hint)[:16] if hint else "ref"
    return hint


def group_named(program, name: Optional[str]) -> GroupProgram:
    """The group called *name* (None or "main": the only group of a graph
    with one group).  Raises KeyError for an unknown name."""
    groups = getattr(program, "groups", ()) or ()
    if name is None or (name == IMPLICIT_GROUP and len(groups) == 1):
        if len(groups) == 1:
            return groups[0]
        raise KeyError("the graph has more than one group: name one")
    for g in groups:
        if g.name == name:
            return g
    raise KeyError(name)


def program_for_steps(program, step_ids: Optional[Iterable[str]]):
    """*program* cut down to the steps in *step_ids* (None: all of them)."""
    if step_ids is None:
        return program
    keep = set(step_ids)
    return replace(program, steps=tuple(s for s in program.steps if s.node_id in keep))


# ---------------------------------------------------------------------------
# Combined result (plan D7)
# ---------------------------------------------------------------------------


@dataclass
class LegTrack:
    """One leg's bars for the combined result.

    times    : the leg's bar times (UTC, no time zone).
    keys     : the leg's time keys as its equity curve writes them.
    equity, baseline : the leg's equity and buy-and-hold value per bar.
    in_pos   : True where the leg holds a position at the bar's close.
    notional : shares times close where it holds one (0 elsewhere).
    capital  : the leg's starting capital.
    final_value, num_trades : from the leg's summary.
    trades   : the leg's trades (for the combined win rate and gain/loss
               stats).
    equity_curve, baseline_curve : optional, the leg's own curve points
               ({"time", "value"}).  Contract: given only when there is one
               point per key, each point's time IS that key (the same
               object) and the values are *equity* / *baseline* bit for bit
               (run._leg_track checks this).  A one-leg combine then returns
               a copy instead of building one dict per bar (see _curve).
    """
    times: pd.DatetimeIndex
    keys: list
    equity: np.ndarray
    baseline: np.ndarray
    in_pos: np.ndarray
    notional: np.ndarray
    capital: float
    final_value: float
    num_trades: int
    trades: list = field(default_factory=list)
    equity_curve: Optional[list] = None
    baseline_curve: Optional[list] = None
    interval: Optional[str] = None
    ends: Optional[np.ndarray] = None   # when each bar closed (int64 ns UTC)


def utc_naive(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx


def position_track(trades: Sequence[Mapping], keys: Sequence, close: np.ndarray):
    """(in_pos, notional) per bar from a leg's trades.

    A trade's ``date`` is the key of its bar.  A position opened on bar e and
    closed on bar x is held at the close of bars e .. x-1 (an exit on bar x
    has closed it by that bar's close); one still open at the end is held to
    the last bar.  Notional is shares times close, for longs and shorts.
    """
    n = len(keys)
    pos = {k: i for i, k in enumerate(keys)}
    in_pos = np.zeros(n, dtype=bool)
    notional = np.zeros(n, dtype=float)
    open_at: Optional[int] = None
    shares = 0.0
    for t in trades:
        i = pos.get(t.get("date"))
        if i is None:
            continue
        if t.get("type") in _ENTRY_TRADES:
            open_at, shares = i, float(t.get("shares") or 0.0)
        elif t.get("type") in _EXIT_TRADES and open_at is not None:
            in_pos[open_at:i] = True
            notional[open_at:i] = shares * close[open_at:i]
            open_at = None
    if open_at is not None:
        in_pos[open_at:] = True
        notional[open_at:] = shares * close[open_at:]
    return in_pos, notional


def _key_to_seconds(key) -> int:
    if isinstance(key, str):
        return int(pd.Timestamp(key, tz="UTC").timestamp())
    return int(key)


def _trade_stats(trades: Sequence[Mapping]) -> dict:
    """Win rate and gain/loss stats over closed trades, with the simulator's
    own rules (routes.backtest), so a one-group combined summary matches
    that group's."""
    from routes.backtest import _edge_stats, _side_stats

    closed = [t for t in trades if t.get("type") in _EXIT_TRADES]
    gains = [float(t["pnl"]) for t in closed if t.get("pnl", 0) > 0]
    losses = [float(t["pnl"]) for t in closed if t.get("pnl", 0) < 0]
    return {
        "win_rate_pct": round(len(gains) / len(closed) * 100, 2) if closed else 0.0,
        "gain_stats": _side_stats(gains),
        "loss_stats": _side_stats(losses),
        **_edge_stats(gains, losses, len(closed)),
    }


def _mixed_intervals(legs: Sequence[LegTrack]) -> bool:
    """True when the legs trade different intervals (a daily leg next to an
    hourly one) and every leg knows when its bars closed."""
    intervals = {leg.interval for leg in legs}
    return len(intervals) > 1 and all(leg.ends is not None for leg in legs)


def _at_close(leg: LegTrack) -> LegTrack:
    """*leg* with each bar stamped at its close (LegTrack.ends) instead of
    its label (KA-3): a daily bar's equity and position exist only once the
    day has ended, never on that day's intraday bars."""
    times = pd.DatetimeIndex(np.asarray(leg.ends, dtype="int64").view("datetime64[ns]"))
    return replace(leg, times=times, equity_curve=None, baseline_curve=None)


def combine(legs: Sequence[LegTrack], initial_capital: float) -> dict:
    """The combined result of the legs (plan D7).

    The legs' equity is summed on the union of their bar times, each leg
    forward-filled (and at its starting capital before its first bar).
    Legs of one interval are placed by bar label, as each leg's own curve
    is.  Legs of different intervals are placed by bar CLOSE (KA-3), so a
    daily leg's end-of-day equity and position never show on that day's
    intraday bars; the combined curves are then keyed by the close time in
    unix seconds.
    Returns {"summary", "equity_curve", "baseline_curve"}.

    The summary also has buy_hold_return_pct (of the summed buy-and-hold
    curves), win_rate_pct and the gain/loss and edge stats over every leg's
    closed trades.

    exposure_pct       : share of the union bars where any leg holds a position.
    gross_deployed_pct : average over the union bars of the legs' summed
                         notional (shares times close, longs and shorts
                         alike) as a share of the combined equity.
    """
    if _mixed_intervals(legs):
        legs = [_at_close(leg) for leg in legs]
        mixed = True
    else:
        mixed = False
    one = legs[0] if len(legs) == 1 else None
    if one is not None and one.times.is_unique and one.times.is_monotonic_increasing:
        # One leg with ordered, distinct bar times: the union is its own
        # index and its keys in order (what the general path below gives,
        # without building a Timestamp per bar).
        union = one.times
        keys = list(one.keys)
    else:
        one = None          # the union reorders or merges bars: no own curves
        key_of: dict[pd.Timestamp, Any] = {}
        for leg in legs:
            for t, k in zip(leg.times, leg.keys):
                key_of.setdefault(t, k)
        union = pd.DatetimeIndex(sorted(key_of))
        keys = [key_of[t] for t in union]
    # Unix seconds for intraday keys.  Keys that are already plain ints are
    # their own seconds (_key_to_seconds returns them as they are).
    if mixed:
        keys = [int(t.timestamp()) for t in union]
    elif not (all(isinstance(k, str) for k in keys) or all(type(k) is int for k in keys)):
        keys = [_key_to_seconds(k) for k in keys]
        one = None          # the keys changed: the own curves carry the old ones

    def _on_union(values: np.ndarray, leg: LegTrack, before: float) -> np.ndarray:
        s = pd.Series(values, index=leg.times)
        s = s[~s.index.duplicated(keep="last")]
        return s.reindex(union).ffill().fillna(before).to_numpy(dtype=float)

    equity = np.zeros(len(union))
    baseline = np.zeros(len(union))
    notional = np.zeros(len(union))
    any_pos = np.zeros(len(union), dtype=bool)
    for leg in legs:
        equity += _on_union(leg.equity, leg, leg.capital)
        baseline += _on_union(leg.baseline, leg, leg.capital)
        notional += _on_union(leg.notional, leg, 0.0)
        any_pos |= _on_union(leg.in_pos.astype(float), leg, 0.0) > 0.5

    eq_series = pd.Series(equity)
    returns = eq_series.pct_change().dropna()
    std = returns.std()
    sharpe = float((returns.mean() / std) * np.sqrt(252)) if std > 0 else 0.0
    peak = eq_series.cummax()
    max_dd = float(((eq_series - peak) / peak).min() * 100) if len(eq_series) else 0.0
    final_value = float(sum(leg.final_value for leg in legs))
    with np.errstate(divide="ignore", invalid="ignore"):
        deployed = np.where(equity > 0, notional / equity, 0.0)
    buy_hold = ((baseline[-1] - baseline[0]) / baseline[0] * 100
                if len(baseline) and baseline[0] else 0.0)
    summary = {
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "total_return_pct": round((final_value - initial_capital) / initial_capital * 100, 2)
        if initial_capital else 0.0,
        "buy_hold_return_pct": round(float(buy_hold), 2),
        "num_trades": int(sum(leg.num_trades for leg in legs)),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 3),
        **_trade_stats([t for leg in legs for t in leg.trades]),
        "exposure_pct": round(float(any_pos.mean() * 100) if len(union) else 0.0, 2),
        "gross_deployed_pct": round(float(deployed.mean() * 100) if len(union) else 0.0, 2),
    }
    return {
        "summary": summary,
        "equity_curve": _curve(keys, equity, *((one.equity_curve, one.equity) if one else ())),
        "baseline_curve": _curve(keys, baseline,
                                 *((one.baseline_curve, one.baseline) if one else ())),
    }


# Below this a float's spacing stays far under a cent, which the rounding
# argument in _curve needs.
_ROUND_SAFE_ABS = 1e12


def _curve(keys: Sequence, values: np.ndarray, own: Optional[list] = None,
           raw: Optional[np.ndarray] = None) -> list:
    """``[{"time": k, "value": round(float(v), 2)}]`` over *keys* and *values*.

    When every value is finite, under _ROUND_SAFE_ABS and already a whole
    number of cents (np.round(v, 2) == v, which makes v the float nearest
    some k/100), Python's round(v, 2) returns v itself, so the per-bar
    rounding is skipped with the same floats out.  This is the usual case
    for one group: the simulator already rounded its equity and buy-and-hold
    values to cents.

    *own* / *raw* (one leg, keys not converted): a LegTrack curve and the
    array it holds (equity_curve / equity).  By the LegTrack contract its
    times are the keys themselves and its values are *raw* bit for bit, so
    when *values* are *raw* bit for bit (the fill changed nothing, -0.0
    stays -0.0) a copy of *own* is this very curve.
    """
    values = np.asarray(values, dtype=float)
    if not (len(values) and np.isfinite(values).all()
            and float(np.abs(values).max()) < _ROUND_SAFE_ABS
            and np.array_equal(np.round(values, 2), values)):
        return [{"time": k, "value": round(float(v), 2)} for k, v in zip(keys, values)]
    if own is not None and raw is not None and len(own) == len(keys) == len(raw) \
            and np.asarray(raw, dtype=float).tobytes() == values.tobytes():
        return list(own)
    return [{"time": k, "value": v} for k, v in zip(keys, values.tolist())]
