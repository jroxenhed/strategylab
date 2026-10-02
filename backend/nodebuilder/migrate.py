"""Graph format migration and node path helpers (plan D2, D3).

Migration
---------
``migrate_graph_data`` is the one entry point.  ``Graph`` calls it from a
``model_validator(mode="before")``, so every load path (API body, bots.json,
the graphs folder, library assets, seeds) upgrades a stored graph the same way.

- v1 -> v2 adds ``Node.name``, ``Node.parent`` (replaces the unused
  ``subgraph``), ``Wire.from_port`` / ``Wire.to_port``, ``stream_schema``,
  ``meta`` and ``annotations``.
- A v2 graph that is missing a node name or a wire port gets it filled in by
  the same rules.  This keeps a half-built graph from the editor loadable.
- v2 -> v3 (plan D4) moves attribute choice off the wires and onto the
  nodes.  Every write param gets its final name stored (``@rsi``,
  ``@rsi_2``...).  Every empty read param that a wire feeds gets the name the
  v2 engine would have read through that wire's ``attr`` label, so a graph
  computes exactly what it did.  Then ``wire.attr`` is dropped, as is the
  Ticker's ``source`` param (the sidebar owns the data source since W2, plan
  D11).  See ``migrate_v2_to_v3``.

Paths
-----
A node's path is its parent's path plus ``/`` plus its name.  Root is ``/``.
Wires point at ids, not paths, so a rename never breaks a wire.

Relative paths follow Houdini: they start at the node given as
``relative_to``.  ``..`` goes up one level (so ``../rsi`` is a sibling
called ``rsi``), ``.`` stays put, and a bare name goes down into a child.

The TypeScript copy of these helpers is
``frontend/src/features/nodebuilder/paths.ts``.  Both run the same vectors in
``backend/tests/nodebuilder/vectors/paths.json``; change them together.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable, Optional

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover
    from nodebuilder.models import Graph

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_GRAPH_VERSION: int = 3
"""The ``_version`` every loaded graph ends up at."""

NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
"""A valid node name (the leaf of its path)."""

_NAME_MAX = 64

_UUID_RE = re.compile(
    r"^/?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NEW_ID_RE = re.compile(r"^n_[a-z0-9]{8}$")
_PORT_RE = re.compile(r"^in(\d+)$")
_TRAILING_DIGITS_RE = re.compile(r"^(.*?)(\d+)$")


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def is_valid_name(name: Any) -> bool:
    return isinstance(name, str) and NAME_RE.match(name) is not None


def sanitize_name(raw: str) -> str:
    """Turn any string into a valid node name.

    Lower-case, every character outside ``[a-z0-9_]`` becomes ``_``, a leading
    digit gets an ``n_`` in front, and the result is cut to 64 characters.
    """
    s = re.sub(r"[^a-z0-9_]", "_", str(raw).lower())
    if not s:
        s = "node"
    if s[0].isdigit():
        s = "n_" + s
    return s[:_NAME_MAX]


def is_opaque_id(node_id: str) -> bool:
    """True for ids that say nothing about the node (UUIDs, ``n_xxxxxxxx``).

    Such nodes are named after their type instead (``rsi1``, ``rsi2``...).
    """
    return bool(_UUID_RE.match(node_id) or _NEW_ID_RE.match(node_id))


def unique_name(base: str, taken: Iterable[str]) -> str:
    """Return *base*, or the next free numbered form of it.

    ``rsi`` taken gives ``rsi1``; ``rsi1`` taken gives ``rsi2``; ``cmp_buy_0``
    taken gives ``cmp_buy_1``.  *base* must already be a valid name.  The
    result is never longer than 64 characters.
    """
    # A set is used as is (it is only read); copying it on every call made
    # naming a big graph quadratic.
    taken_set = taken if isinstance(taken, (set, frozenset)) else set(taken)
    if base not in taken_set:
        return base
    m = _TRAILING_DIGITS_RE.match(base)
    stem, n = (m.group(1), int(m.group(2)) + 1) if m and m.group(1) else (base, 1)
    while True:
        suffix = str(n)
        candidate = stem[: _NAME_MAX - len(suffix)] + suffix
        if candidate not in taken_set:
            return candidate
        n += 1


def default_name(node_id: str, node_type: str) -> str:
    """The name a node gets when it has none (plan D3)."""
    if is_opaque_id(node_id):
        return sanitize_name(node_type or "node")[: _NAME_MAX - 1] + "1"
    return sanitize_name(node_id.lstrip("/"))


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _as_dict(value: Any, *, by_alias: bool) -> Any:
    """Model instances become plain dicts so migration can treat both alike."""
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=by_alias)
    if isinstance(value, dict):
        return dict(value)
    return value


def _wire_target(wire: dict) -> Any:
    return wire.get("to", wire.get("to_path"))


def _hkey(value: Any) -> Any:
    """*value* as a dict key.  A malformed (unhashable) value gets a stand-in
    so migration never crashes; the model then reports the bad field."""
    try:
        hash(value)
    except TypeError:
        return ("<unhashable>", repr(value))
    return value


def _fill_names(nodes: dict) -> None:
    """Give every node without a name a free name among its siblings.

    Existing names are kept.  Blank ones are filled in node order, so the
    result does not depend on anything but the graph itself.
    """
    taken: dict[Any, set[str]] = {}
    for node in nodes.values():
        if isinstance(node, dict) and isinstance(node.get("name"), str) and node["name"]:
            taken.setdefault(_hkey(node.get("parent")), set()).add(node["name"])
    for key, node in nodes.items():
        if not isinstance(node, dict) or node.get("name"):
            continue
        siblings = taken.setdefault(_hkey(node.get("parent")), set())
        name = unique_name(
            default_name(str(node.get("id", key)), str(node.get("type", ""))), siblings
        )
        node["name"] = name
        siblings.add(name)


def _fill_ports(wires: list) -> None:
    """Give every wire without a ``to_port`` the next free ``in<k>`` port.

    Ports are handed out per consumer in wire order, which is the order
    compile reads a node's inputs in today.  Existing ports are kept.
    """
    used: dict[Any, set[Any]] = {}
    for wire in wires:
        if isinstance(wire, dict) and wire.get("to_port"):
            used.setdefault(_hkey(_wire_target(wire)), set()).add(_hkey(wire["to_port"]))
    for wire in wires:
        if not isinstance(wire, dict):
            continue
        wire.setdefault("from_port", "out")
        if wire.get("to_port"):
            continue
        ports = used.setdefault(_hkey(_wire_target(wire)), set())
        k = 0
        while f"in{k}" in ports:
            k += 1
        wire["to_port"] = f"in{k}"
        ports.add(wire["to_port"])


def migrate_v1_to_v2(data: dict) -> dict:
    """Upgrade a v1 graph dict to v2.  Returns a new dict.

    ``subgraph`` was never used, so it is dropped and ``parent`` starts empty
    (root).  Names come from the id (``/rsi_ab12`` -> ``rsi_ab12``), or from
    the type for opaque ids (``rsi1``).  Each consumer's input wires get
    ``in0``, ``in1``... in their current order.
    """
    out = dict(data)
    nodes = {}
    for key, node in (out.get("nodes") or {}).items():
        node = _as_dict(node, by_alias=False)
        if isinstance(node, dict):
            node.pop("subgraph", None)
            node.setdefault("parent", None)
        nodes[key] = node
    out["nodes"] = nodes
    out["wires"] = [_as_dict(w, by_alias=True) for w in (out.get("wires") or [])]
    _fill_names(nodes)
    _fill_ports(out["wires"])
    out.pop("version", None)
    out["_version"] = 2
    return out


def migrate_graph_data(data: Any) -> Any:
    """Run the migration chain on raw graph input.  Anything not a dict passes through.

    Raises IncompatibleGraphVersionError for a ``_version`` below the floor,
    and UnsupportedGraphVersionError (code graph_invalid) for a ``_version``
    that is not a whole number or is newer than CURRENT_GRAPH_VERSION.  A
    newer graph is refused rather than loaded, because loading it would drop
    the fields this code does not know.

    ``nodes`` that is not an object, or ``wires`` that is not a list, is
    passed through untouched so the model reports it as a field error.
    """
    if not isinstance(data, dict):
        return data
    from nodebuilder.models import (
        MIN_SUPPORTED_VERSION,
        IncompatibleGraphVersionError,
        UnsupportedGraphVersionError,
    )

    # bots.json stores model_dump() output, where the key is "version".
    version = data.get("_version", data.get("version", 1))
    if isinstance(version, bool) or not isinstance(version, int):
        raise UnsupportedGraphVersionError(
            f"Graph _version must be a whole number, got {version!r}."
        )
    if version < MIN_SUPPORTED_VERSION:
        raise IncompatibleGraphVersionError(version, MIN_SUPPORTED_VERSION)
    if version > CURRENT_GRAPH_VERSION:
        raise UnsupportedGraphVersionError(
            f"This graph was saved by a newer StrategyLab (graph version {version}; "
            f"this server reads up to {CURRENT_GRAPH_VERSION}).  Update StrategyLab "
            "to open it."
        )

    nodes = data.get("nodes")
    wires = data.get("wires")
    if not isinstance(nodes, (dict, type(None))) or not isinstance(wires, (list, type(None))):
        return data  # let pydantic report the bad field

    if version < 2:
        out = migrate_v1_to_v2(data)
    else:
        # v2 or v3: fill in any names or ports the sender left out.
        out = dict(data)
        out["nodes"] = {k: _as_dict(n, by_alias=False) for k, n in (out.get("nodes") or {}).items()}
        out["wires"] = [_as_dict(w, by_alias=True) for w in (out.get("wires") or [])]
        _fill_names(out["nodes"])
        _fill_ports(out["wires"])
        if "_version" not in out:
            out["_version"] = out.pop("version")
        else:
            out.pop("version", None)
    if version < 3:
        out = migrate_v2_to_v3(out)
    return out


# ---------------------------------------------------------------------------
# v2 -> v3: attribute choice moves from the wires to the nodes (plan D4)
# ---------------------------------------------------------------------------


class _NodeView:
    """The three fields the kernel's naming helpers read from a node."""

    __slots__ = ("id", "type", "params")

    def __init__(self, node_id: Any, node_type: str, params: dict) -> None:
        self.id = node_id
        self.type = node_type
        self.params = params


class _GraphView:
    """Just enough of a Graph for ``kernel.schema.assign_write_names``."""

    def __init__(self, nodes: dict) -> None:
        self.nodes = nodes


def _topological_ids(nodes: dict, wires: list) -> list:
    """Node keys in the order ``models.topological_sort`` gives (Kahn's
    algorithm, ties by id), so write names come out as compile would pick
    them.  Unlike that function it never fails: wires to unknown nodes are
    skipped and nodes on a cycle go last, in node order (the model refuses
    such a graph right after migration anyway).
    """
    in_degree = {k: 0 for k in nodes}
    adj: dict[Any, list] = {k: [] for k in nodes}
    for wire in wires:
        if not isinstance(wire, dict):
            continue
        src, dst = _hkey(wire.get("from", wire.get("from_path"))), _hkey(_wire_target(wire))
        if src in adj and dst in in_degree:
            adj[src].append(dst)
            in_degree[dst] += 1
    queue = sorted((k for k, d in in_degree.items() if d == 0), key=str)
    order: list = []
    while queue:
        key = queue.pop(0)
        order.append(key)
        for nxt in sorted(adj[key], key=str):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    placed = set(order)
    return order + [k for k in nodes if k not in placed]


def _wire_port_key(item: tuple) -> tuple:
    """Sort key for (list index, wire): numbered ports first, in port order,
    then the rest in list order.  The same order compile reads inputs in."""
    i, wire = item
    port = wire.get("to_port")
    k = int(port[2:]) if isinstance(port, str) and _PORT_RE.match(port) else None
    return (k is None, k if k is not None else 0, i)


# Wave 1's indicator types.  Their one input was optional: an indicator with
# no wire read the run's bars (W1 nodes.py ``_SOURCE_INPUT``).
_W1_INDICATORS = ("rsi", "macd", "sma", "ema", "bollinger", "atr")


def _wire_from(wire: dict) -> Any:
    return wire.get("from", wire.get("from_path"))


def _registered(node: Any) -> Any:
    """The NodeType of a node dict, or None."""
    from nodebuilder.kernel import registry

    if not isinstance(node, dict) or not isinstance(node.get("type"), str):
        return None
    return registry.get(node["type"])


def _inbound(wires: list) -> dict:
    out: dict = {}
    for wire in wires:
        if isinstance(wire, dict):
            out.setdefault(_hkey(_wire_target(wire)), []).append(wire)
    return out


def _new_wire_id(wires: list, base: str) -> str:
    taken = {w.get("id") for w in wires if isinstance(w, dict)}
    wid, n = base, 1
    while wid in taken:
        n += 1
        wid = f"{base}_{n}"
    return wid


def _number_odd_ports(nodes: dict, wires: list) -> None:
    """Wave 1 ignored the port id on a node that is not a comparison: a
    single-input node read its one wire, and AND/OR read every wire, numbered
    ports first, then the rest in list order.  Give such wires the ``in<k>``
    port Wave 1 read them on (MD-09).  Comparisons refused a non-numbered
    port in Wave 1, so they keep it and stay refused.
    """
    for target, items in _inbound(wires).items():
        nt = _registered(nodes.get(target))
        odd = [w for w in items if not (isinstance(w.get("to_port"), str) and _PORT_RE.match(w["to_port"]))]
        if nt is None or not odd:
            continue
        if nt.inputs.max == 1:
            if len(items) == 1:
                items[0]["to_port"] = "in0"
        elif nt.inputs.dynamic:
            used = {int(w["to_port"][2:]) for w in items if w not in odd}
            k = 0
            for wire in odd:  # list order, after every numbered port
                while k in used or k <= max(used, default=-1):
                    k += 1
                wire["to_port"] = f"in{k}"
                used.add(k)


def _split_wide_logic(nodes: dict, wires: list) -> None:
    """Wave 1 had no input cap on AND/OR (a 20-rule strategy drew one AND
    with 20 inputs); the v3 node takes at most ``inputs.max`` (16).  Split a
    wider node into parts of the same type, each at most that wide, feeding
    the original, as from_rules does (MD-03).  AND of ANDs and OR of ORs is
    the same signal; the original keeps its id, so wires out of it stay.
    A node whose ``terms`` are already named is left alone.
    """
    for target, items in list(_inbound(wires).items()):
        node = nodes.get(target)
        nt = _registered(node)
        if nt is None or node.get("type") not in ("and", "or"):
            continue
        limit = nt.inputs.max
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        if params.get("terms") or len(items) <= limit:
            continue
        ordered = [w for _i, w in sorted(enumerate(items), key=_wire_port_key)]
        n_parts = -(-len(ordered) // limit)
        if n_parts > limit:
            # Over limit * limit (256) inputs: not reachable from the rule
            # builder (100 rules); left for compile to refuse.
            continue
        size = -(-len(ordered) // n_parts)  # even parts, none over the limit
        groups = [ordered[i:i + size] for i in range(0, len(ordered), size)]
        node_id = node.get("id", target)
        siblings = {n.get("name") for n in nodes.values()
                    if isinstance(n, dict) and n.get("parent") == node.get("parent")
                    and isinstance(n.get("name"), str)}
        for k, group in enumerate(groups):
            part_id = f"{node_id}_part{k}"
            while part_id in nodes:
                part_id += "_"
            name = unique_name(sanitize_name(f"{node.get('name') or 'logic'}_part{k}"), siblings)
            siblings.add(name)
            nodes[part_id] = {
                "id": part_id, "type": node["type"], "name": name, "parent": node.get("parent"),
                "params": {}, "position": node.get("position", [0.0, 0.0]),
                "display": False, "bypass": False,
            }
            for j, wire in enumerate(group):
                if "to" in wire or "to_path" not in wire:
                    wire["to"] = part_id
                    wire.pop("to_path", None)
                else:
                    wire["to_path"] = part_id
                wire["to_port"] = f"in{j}"
            wires.append({"id": _new_wire_id(wires, f"{part_id}_out"), "from": part_id,
                          "to": node_id, "from_port": "out", "to_port": f"in{k}"})


def _wire_unwired_indicators(nodes: dict, wires: list) -> None:
    """Wave 1 computed every indicator from the run's bars, wired or not
    (an orphan or unwired RSI was RSI of the run's close).  The v3 engine
    refuses an indicator with no input, so wire each Wave 1 indicator type
    with no inbound wire from a root Ticker on in0 (LT-2, MD-04).  Before W5
    every Ticker reads the run's bars, so the values are what Wave 1 used;
    the read params get their defaults (@close; ATR's @high/@low), which is
    what Wave 1 read.  With no root Ticker, one is added; with several, the
    first by id is used.
    """
    if not any(isinstance(n, dict) and n.get("type") == "entry" for n in nodes.values()):
        return  # Wave 1 compiled nothing without an Entry; leave the graph alone
    has_input = {_hkey(_wire_target(w)) for w in wires if isinstance(w, dict)}
    todo = [k for k, n in nodes.items()
            if isinstance(n, dict) and n.get("type") in _W1_INDICATORS
            and n.get("parent") is None and _hkey(k) not in has_input
            and _registered(n) is not None]
    if not todo:
        return
    tickers = sorted((k for k, n in nodes.items() if isinstance(n, dict)
                      and n.get("type") == "ticker" and n.get("parent") is None), key=str)
    if tickers:
        ticker = tickers[0]
    else:
        ticker = "/ticker" if "/ticker" not in nodes else "/ticker_bars"
        while ticker in nodes:
            ticker += "_"
        siblings = {n.get("name") for n in nodes.values() if isinstance(n, dict)
                    and n.get("parent") is None and isinstance(n.get("name"), str)}
        nodes[ticker] = {"id": ticker, "type": "ticker", "name": unique_name("ticker", siblings),
                         "parent": None, "params": {}, "position": [0.0, 0.0],
                         "display": False, "bypass": False}
    ticker_id = nodes[ticker].get("id", ticker)
    for key in todo:
        node_id = nodes[key].get("id", key)
        wires.append({"id": _new_wire_id(wires, f"{ticker_id}-{node_id}-in0"), "from": ticker_id,
                      "to": node_id, "from_port": "out", "to_port": "in0"})


def _note_ticker_source(out: dict, nodes: dict) -> None:
    """The Ticker ``source`` param is dropped in v3 (plan D11: the sidebar
    owns the data source).  Keep the old choice as ``meta.legacy_source`` so
    it is not lost without a trace (MD-07).  Never pushes meta past its key
    cap."""
    found = sorted({n["params"]["source"] for n in nodes.values()
                    if isinstance(n, dict) and n.get("type") == "ticker"
                    and isinstance(n.get("params"), dict)
                    and isinstance(n["params"].get("source"), str) and n["params"]["source"]})
    if not found:
        return
    meta = out.get("meta")
    if meta is None:
        meta = {}
    if not isinstance(meta, dict) or "legacy_source" in meta:
        return
    from nodebuilder.models import _META_MAX_KEYS

    if len(meta) >= _META_MAX_KEYS:
        return
    out["meta"] = {**meta, "legacy_source": ",".join(found)}


def migrate_v2_to_v3(data: dict) -> dict:
    """Upgrade a v2 graph dict (names and ports already filled) to v3.

    v2 picked what a node read through each input wire's ``attr`` label.  v3
    keeps that choice on the node, in its read params (plan D4).  For each
    node type the backend knows, in this order:

    0. Wave 1 shapes the v3 engine refuses are rewritten to what Wave 1
       computed (F435 W2 fix pass; signals pinned by
       tests/nodebuilder/test_w1_goldens.py against the frozen Wave 1
       engine): a non-numbered port on a single-input node or AND/OR gets
       its in<k> (``_number_odd_ports``); an AND/OR over 16 inputs is split
       into parts (``_split_wide_logic``); a Wave 1 indicator with no input
       is wired from a root Ticker, added if there is none
       (``_wire_unwired_indicators``); the Ticker source is kept as
       ``meta.legacy_source`` (``_note_ticker_source``).
    1. Every write param gets its final name stored.  A valid name is kept;
       an empty one gets the catalog default made unique in the graph
       (``kernel.schema.assign_write_names``, the names compile would use).
    2. Every empty read param that a wire feeds gets the name the v2 engine
       read through that wire (``kernel.schema._resolve_reads``):
       - a label the source type knows (a Ticker field such as ``@high``, a
         MACD or Bollinger output such as ``@macd_signal``) names that output;
       - any other label, or no label, reads the source's primary write;
       - a param of the ``legacy_ignore`` kind (an indicator's ``source``)
         ignores the label and reads the source's primary write (a Ticker's
         ``@close``).
       ``attr_list`` params (logic ``terms``) get one name per wire, in port
       order.  Params that already name an attribute are left alone.
    3. Old auto-rendered graphs wired one Ticker into ATR three times (in0,
       in1, in2).  ATR has one input; the extra wires from the same node
       added nothing and are dropped.
    4. ``wire.attr`` is dropped.  It stays only where it could not be turned
       into a param, so compile still reports what it reported before: a
       wire into a node type the backend does not know, or a label that
       names another type's output (``@macd_signal`` out of an RSI).
    5. The Ticker ``source`` param is dropped (plan D11).

    Never raises on a malformed graph: anything it cannot read is left as it
    is for the model to report.
    """
    from nodebuilder.kernel import registry
    from nodebuilder.kernel.schema import (
        AttrInfo, _list_value, _type_ok, assign_write_names, primary_write,
    )
    from nodebuilder.kernel.stream import is_attr_name
    import nodebuilder.trading  # noqa: F401  (registers every node type)

    out = dict(data)
    # Copies, so the caller's dicts are never changed.
    nodes: dict = {}
    for key, node in (out.get("nodes") or {}).items():
        if isinstance(node, dict):
            node = dict(node)
            if isinstance(node.get("params"), dict):
                node["params"] = dict(node["params"])
        nodes[key] = node
    out["nodes"] = nodes
    wires: list = [dict(w) if isinstance(w, dict) else w for w in (out.get("wires") or [])]

    # 0. Wave 1 shapes the v3 engine would refuse, rewritten to what Wave 1
    #    computed (F435 W2: a graph that compiled in Wave 1 compiles in Wave 2
    #    with the same signals).
    _number_odd_ports(nodes, wires)
    _split_wide_logic(nodes, wires)
    _wire_unwired_indicators(nodes, wires)
    _note_ticker_source(out, nodes)

    # Node views for every node whose type is registered.
    views: dict = {}
    for key, node in nodes.items():
        if not isinstance(node, dict):
            continue
        params = node.get("params")
        if params is None:
            params = node["params"] = {}
        if not isinstance(params, dict):
            continue
        node_type = node.get("type")
        if node_type == "ticker":
            params.pop("source", None)
        node_id = node.get("id", key)
        if (isinstance(node_type, str) and isinstance(node_id, str)
                and registry.get(node_type) is not None):
            views[_hkey(key)] = _NodeView(node_id, node_type, params)

    # 1. Final write names, stored on the node.
    order = [views[k] for k in _topological_ids(nodes, wires) if _hkey(k) in views]
    write_names = assign_write_names(_GraphView(views), order)
    by_id = {v.id: v for v in views.values()}
    for view in views.values():
        for slot, name in write_names.get(view.id, {}).items():
            view.params[slot] = name

    def _write_dtypes(view: _NodeView, st) -> dict[str, str]:
        """Name -> dtype of everything *view* writes."""
        out = {name: dtype for name, dtype in st.fixed_writes}
        for spec in st.write_params():
            name = write_names.get(view.id, {}).get(spec.name)
            if name is not None:
                out[name] = spec.dtype or "float"
        return out

    strict_labels = registry.strict_legacy_labels()
    keep_label: set[int] = set()   # ids of wires whose attr must stay
    drop: set[int] = set()         # ids of redundant wires

    inbound: dict[Any, list] = {}
    for i, wire in enumerate(wires):
        if isinstance(wire, dict):
            inbound.setdefault(_hkey(_wire_target(wire)), []).append((i, wire))

    for target, items in inbound.items():
        view = views.get(target)
        if view is None:
            # Unknown consumer type: keep its labels for whatever reads them.
            keep_label.update(id(w) for _i, w in items if w.get("attr"))
            continue
        nt = registry.get(view.type)
        limit = nt.inputs.max
        wired: dict[int, dict] = {}
        extra: list[dict] = []
        for _i, wire in sorted(items, key=_wire_port_key):
            port = wire.get("to_port")
            k = int(port[2:]) if isinstance(port, str) and _PORT_RE.match(port) else None
            if k is None or k >= limit:
                extra.append(wire)
            else:
                wired.setdefault(k, wire)
        # 3. A wire past the node's last port, from a node already wired in.
        sources = {_hkey(w.get("from", w.get("from_path"))) for w in wired.values()}
        for wire in extra:
            if _hkey(wire.get("from", wire.get("from_path"))) in sources:
                drop.add(id(wire))

        def _name_through(wire: dict, spec) -> tuple[Optional[str], bool]:
            """(name the v2 engine read through *wire* for *spec*, ok).

            ok is False when the read must stay implicit (param empty, label
            kept): the label names another type's output, or the name it
            reads has the wrong type for *spec*.  Compile then reports the
            error exactly as before, against the same node (a wrong-type
            implicit read is blamed on the node upstream, an explicit one on
            the reader).
            """
            src = by_id.get(_hkey(wire.get("from", wire.get("from_path"))))
            st = registry.get(src.type) if src is not None else None
            if src is None:
                # A source type the backend does not know (yet): keep the
                # label, as for an unknown consumer, so the choice survives
                # until the type is registered (MD-08).
                return None, False
            label = wire.get("attr") if spec.name not in nt.legacy_ignore else None
            name = None
            if label:
                target_slot = st.legacy_reads.get(label)
                if target_slot is not None:
                    name = write_names.get(src.id, {}).get(target_slot, target_slot)
                elif label in strict_labels:
                    return None, False
            if name is None:
                name = primary_write(src, st, write_names)
            dtype = _write_dtypes(src, st).get(name)
            if dtype is not None and not _type_ok(spec.dtype, AttrInfo(name, dtype, src.id)):
                return None, False
            return name, True

        ports = sorted(wired)
        for spec in nt.read_params():
            raw = view.params.get(spec.name)
            fed = nt.ports_for_param(spec.name, ports)
            if not fed:
                continue
            if spec.type == "attr":
                if raw not in (None, "") or (isinstance(spec.default, str) and spec.default):
                    continue
                name, ok = _name_through(wired[fed[0]], spec)
                if not ok:
                    keep_label.add(id(wired[fed[0]]))
                elif name is not None and is_attr_name(name):
                    view.params[spec.name] = name
            else:
                if _list_value(raw) != []:
                    continue  # already named (or malformed: the model says so)
                names: list[str] = []
                for k in fed:
                    name, ok = _name_through(wired[k], spec)
                    if not ok:
                        names = []
                        keep_label.update(id(wired[j]) for j in fed)
                        break
                    if name is not None and is_attr_name(name):
                        names.append(name)
                if names:
                    view.params[spec.name] = names

    new_wires = []
    for wire in wires:
        if isinstance(wire, dict):
            if id(wire) in drop:
                continue
            if id(wire) not in keep_label:
                wire.pop("attr", None)
        new_wires.append(wire)
    out["wires"] = new_wires
    out.pop("version", None)
    out["_version"] = 3
    return out


# ---------------------------------------------------------------------------
# Name and parent checks (used by Graph's validator and by /validate)
# ---------------------------------------------------------------------------


def name_issues(nodes: dict) -> list[dict]:
    """Every problem with node names and parents, in node order.

    Each item is ``{"code", "node_id", "message"}``.  Codes: ``name_invalid``,
    ``name_duplicate``, ``parent_missing``, ``parent_cycle``.  *nodes* maps id
    to anything with ``name`` and ``parent`` attributes.
    """
    issues: list[dict] = []
    seen: dict[tuple[Optional[str], str], str] = {}
    for node_id, node in nodes.items():
        if not is_valid_name(node.name):
            issues.append({
                "code": "name_invalid", "node_id": node_id,
                "message": f"Name {node.name!r} is not valid. Use lower-case letters, "
                           "digits and _, starting with a letter or _ (at most 64).",
            })
        else:
            key = (node.parent, node.name)
            if key in seen:
                issues.append({
                    "code": "name_duplicate", "node_id": node_id,
                    "message": f"Another node here is already called {node.name!r}.",
                })
            else:
                seen[key] = node_id
        if node.parent is not None and node.parent not in nodes:
            issues.append({
                "code": "parent_missing", "node_id": node_id,
                "message": f"Parent {node.parent!r} does not exist.",
            })
    for node_id in nodes:
        if _parent_cycle(nodes, node_id):
            issues.append({
                "code": "parent_cycle", "node_id": node_id,
                "message": "This node is inside itself (its parents loop back to it).",
            })
    return issues


def _parent_cycle(nodes: dict, node_id: str) -> bool:
    seen = {node_id}
    parent = nodes[node_id].parent
    while parent is not None and parent in nodes:
        if parent in seen:
            return True
        seen.add(parent)
        parent = nodes[parent].parent
    return False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def node_path(graph: "Graph", node_id: str) -> str:
    """The absolute path of a node, e.g. ``/regime/spy_sma``.

    Raises KeyError when the node does not exist.  Assumes a validated graph
    (no parent loops).
    """
    names: list[str] = []
    current: Optional[str] = node_id
    while current is not None:
        node = graph.nodes[current]
        names.append(node.name)
        current = node.parent
    return "/" + "/".join(reversed(names))


def _child_by_name(graph: "Graph", parent: Optional[str], name: str) -> Optional[str]:
    for node_id, node in graph.nodes.items():
        if node.parent == parent and node.name == name:
            return node_id
    return None


def find_by_path(graph: "Graph", path: str, relative_to: Optional[str] = None) -> Optional[str]:
    """Return the id of the node at *path*, or None when nothing is there.

    An absolute path starts with ``/``.  Anything else is relative to the node
    whose id is *relative_to* (or to the root when that is None).  ``..`` goes
    up one level, ``.`` stays, a name goes down into that child.  Going above
    the root, or a path that ends at the root itself, finds nothing.
    """
    if not isinstance(path, str) or path == "":
        return None
    if path.startswith("/") or relative_to is None:
        current: Optional[str] = None  # the root
    else:
        if relative_to not in graph.nodes:
            return None
        current = relative_to
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if current is None:
                return None
            current = graph.nodes[current].parent
            continue
        current = _child_by_name(graph, current, part)
        if current is None:
            return None
    return current


class NodeNotFoundError(Exception):
    """rename_node was given an id that is not in the graph."""

    code = "node_not_found"

    def __init__(self, node_id: str) -> None:
        super().__init__(f"No node with id {node_id!r}.")
        self.node_id = node_id


def rename_node(graph: "Graph", node_id: str, new_name: str) -> "Graph":
    """Return a copy of *graph* with the node renamed.  The input is not changed.

    Wires use ids, so they need nothing.  Stored path strings that pointed at
    the old path are rewritten (see ``_rewrite_path_refs``).

    Raises NodeNotFoundError, InvalidNodeNameError (``name_invalid``) or
    DuplicateNodeNameError (``name_duplicate``).  It does not check
    ``readOnly``; the caller decides whether the graph may change.
    """
    from nodebuilder.models import DuplicateNodeNameError, InvalidNodeNameError

    if node_id not in graph.nodes:
        raise NodeNotFoundError(node_id)
    node = graph.nodes[node_id]
    if not is_valid_name(new_name):
        raise InvalidNodeNameError(
            f"Name {new_name!r} is not valid. Use lower-case letters, digits and _, "
            "starting with a letter or _ (at most 64).",
            node_id=node_id,
        )
    if new_name == node.name:
        return graph.model_copy(deep=True)
    if _child_by_name(graph, node.parent, new_name) is not None:
        raise DuplicateNodeNameError(
            f"Another node here is already called {new_name!r}.", node_id=node_id
        )
    old_path = node_path(graph, node_id)
    new_graph = graph.model_copy(deep=True)
    new_graph.nodes[node_id] = new_graph.nodes[node_id].model_copy(update={"name": new_name})
    _rewrite_path_refs(new_graph, old_path, node_path(new_graph, node_id))
    return new_graph


def _rewrite_path_refs(
    graph: "Graph",
    old_path: str,
    new_path: str,
    only_nodes: Optional[set[str]] = None,
) -> None:
    """Point stored path strings at a renamed (or moved) node's new path, in place.

    The twin of frontend ``paths.rewritePathRefs``; the shared vectors in
    tests/nodebuilder/vectors/paths.json hold both to the same answers.
    Two kinds of ref are stored today: an Output Group's ``ticker`` param
    (W5) and the ``target`` of each promoted param on a network (W6, see
    ``_rewrite_promoted``).  W7 adds ``ch()`` strings.  *graph* is the
    graph after the rename.  *only_nodes* limits the rewrite to refs stored
    on those nodes.

    An absolute ref is rewritten when it is *old_path* or lies under
    ``old_path + "/"``.  A relative ref is resolved the way the server
    resolves a group's ticker (against the group, then its parent, then the
    root): at the first base where the old resolution lands under
    *old_path*, it is rewritten to the absolute new path only when the same
    relative string no longer lands there; otherwise it is kept.
    """
    if old_path == new_path or old_path in ("/", ""):
        return

    def under(p: str) -> bool:
        return p == old_path or p.startswith(old_path + "/")

    def to_new(p: str) -> str:
        return new_path + p[len(old_path):] if under(p) else p

    def to_old(p: str) -> str:
        if p == new_path or p.startswith(new_path + "/"):
            return old_path + p[len(new_path):]
        return p

    for node_id, node in list(graph.nodes.items()):
        if only_nodes is not None and node_id not in only_nodes:
            continue
        if getattr(node, "promoted", None):
            _rewrite_promoted(graph, node_id, under, to_new, to_old)
            node = graph.nodes[node_id]
        if node.type != "output_group":
            continue
        ref = (node.params or {}).get("ticker")
        if not isinstance(ref, str) or ref == "":
            continue
        nxt = ref
        if ref.startswith("/"):
            nxt = to_new(ref)
        else:
            for base in (node_id, node.parent, None):
                try:
                    new_base = "/" if base is None else node_path(graph, base)
                except KeyError:
                    continue
                old_abs = _join_path(to_old(new_base), ref)
                if old_abs is None or not under(old_abs):
                    continue
                want = to_new(old_abs)
                nxt = ref if _join_path(new_base, ref) == want else want
                break
        if nxt == ref:
            continue
        graph.nodes[node_id] = node.model_copy(update={"params": {**node.params, "ticker": nxt}})


def _rewrite_promoted(graph: "Graph", node_id: str, under, to_new, to_old) -> None:
    """Rewrite the promoted-param targets of network *node_id* after a rename.

    A target is ``<node path>/<param>``, relative to the network (W6).  It
    is rewritten when the node it named lies on the renamed path and the
    same string no longer reaches it.  The new target stays relative to the
    network.  Renaming the network itself (or anything above it) moves the
    base and the target together, so nothing changes.
    """
    node = graph.nodes[node_id]
    try:
        new_base = node_path(graph, node_id)
    except KeyError:
        return
    old_base = to_old(new_base)
    changed = False
    promoted = []
    for p in node.promoted:
        target = getattr(p, "target", None)
        node_part, sep, param = target.rpartition("/") if isinstance(target, str) else ("", "", "")
        if not sep or not node_part or target.startswith("/"):
            promoted.append(p)
            continue
        old_abs = _join_path(old_base, node_part)
        if old_abs is None or not under(old_abs):
            promoted.append(p)
            continue
        want = to_new(old_abs)
        if _join_path(new_base, node_part) == want:
            promoted.append(p)
            continue
        promoted.append(p.model_copy(update={"target": f"{_relative_path(new_base, want)}/{param}"}))
        changed = True
    if changed:
        graph.nodes[node_id] = node.model_copy(update={"promoted": promoted})


def _relative_path(base: str, target: str) -> str:
    """The path from the node at absolute *base* to the node at *target*,
    using ``..`` only when *target* is not inside *base*."""
    b = [p for p in base.split("/") if p]
    t = [p for p in target.split("/") if p]
    common = 0
    while common < min(len(b), len(t)) and b[common] == t[common]:
        common += 1
    parts = [".."] * (len(b) - common) + t[common:]
    return "/".join(parts) if parts else "."


def _join_path(base: str, rel: str) -> Optional[str]:
    """The absolute path *rel* names from the node at absolute path *base*.

    Same rules as find_by_path (and frontend ``paths.joinPath``).  None when
    it climbs above the root or ends at the root itself.
    """
    parts = [] if rel.startswith("/") else [p for p in base.split("/") if p != ""]
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts) if parts else None
