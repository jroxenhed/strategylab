"""Node types and the registry (plan D5, D9).

The registry is the single source of node types.  A node module registers
each of its types when it is imported, with everything the rest of the app
needs: the impl that runs at cook time, the param and port specs, the
compile-time check, and the catalog metadata the frontend is generated
from.  Compile, /validate, the backend catalog and the catalog export script
all read the registry, so a new module that registers a type needs no edit
anywhere else.

How a module registers a type::

    from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node

    def _double(inputs, params):
        return inputs.with_point(params["out"], inputs.column(params["a"]) * 2,
                                 params.node_id, "float")

    register_node(
        name="double", cat="signal", desc="Twice a.",
        params=(ParamSpec("a", "attr", "a", None, dtype="float"),
                ParamSpec("out", "write", "out", "@double", dtype="float")),
        inputs=PortsSpec(ports=(PortSpec("a"),), dynamic=False, min=1, max=1),
        impl=_double,
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

# Every kind of param a node can have (plan 4.3).  "attr" reads one
# attribute, "attr_list" reads several, "write" names an attribute the node
# writes.
PARAM_TYPES: tuple[str, ...] = (
    "number", "int", "string", "select", "bool",
    "attr", "attr_list", "write", "path", "time_range",
)

READ_PARAM_TYPES: frozenset[str] = frozenset({"attr", "attr_list"})

PARAM_DTYPES: tuple[str, ...] = ("float", "bool", "any")


# ---------------------------------------------------------------------------
# Param and port specs (plan section 4.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """One param of a node type: its kind, label, default and limits.

    min/max are the limits the node enforces (for example 2 to 500).  code_able is False for params that may never hold code.

    For an ``attr`` param, a ``None`` default means "read the primary write
    of the node wired into the matching port" (plan D4, default reads); a
    name default (``@x``) is read as it stands.  dtype is the attribute
    type the param accepts: ``bool``, ``float`` (any number, a bool counts as
    0 or 1) or ``any``.  For a ``write`` param, dtype is the type written.
    """
    name: str
    type: str
    label: str
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: Optional[str] = None
    options: Optional[tuple[str, ...]] = None
    dtype: Optional[str] = None
    optional: bool = False
    code_able: bool = True

    def to_json(self) -> dict[str, Any]:
        """The JSON shape of section 4.3.  Unset optional fields are left out."""
        out: dict[str, Any] = {
            "name": self.name, "type": self.type, "label": self.label, "default": self.default,
        }
        for key in ("min", "max", "step", "unit", "dtype"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.options is not None:
            out["options"] = list(self.options)
        if self.optional:
            out["optional"] = True
        if not self.code_able:
            out["code_able"] = False
        return out


@dataclass(frozen=True)
class PortSpec:
    """One input port.  Its id is in<k> by position; label is what the canvas shows."""
    label: str
    optional: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"label": self.label}
        if self.optional:
            out["optional"] = True
        return out


@dataclass(frozen=True)
class PortsSpec:
    """The input ports of a node type.

    ports   : the ports drawn by default, in order (in0, in1, ...).
    dynamic : True when more ports can be added (AND, OR).  Each extra port
              is labeled by its id.
    min/max : how many wired ports the node needs, and how many it can take.
    """
    ports: tuple[PortSpec, ...]
    dynamic: bool
    min: int
    max: int

    def to_json(self) -> dict[str, Any]:
        return {
            "ports": [p.to_json() for p in self.ports],
            "dynamic": self.dynamic,
            "min": self.min,
            "max": self.max,
        }


# Nodes that take no wire in (sources, constants).
NO_INPUTS = PortsSpec(ports=(), dynamic=False, min=0, max=0)


# ---------------------------------------------------------------------------
# Catalog entry (plan section 4.3, D9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeCatalogEntry:
    """Static description of a node type, as the catalog shows it.

    Fields
    ------
    name          : Unique node-type identifier, e.g. "rsi", "crosses_below".
    cat           : Category key (the domain layer lists the categories).
    desc          : Short human-readable description (Tab-menu search).
    reads         : Attributes this node reads, for display, e.g. ("@a",).
    writes        : Attributes this node writes by default, e.g. ("@out",).
    defaults      : Node-instance defaults dict:
                      "params"   – param defaults.  Filled in from `params`;
                                   never written by hand.
                      "param_options" – the options of each select param,
                                   also filled in from `params`.
                      "ins"      – expected number of inbound wires.
                      "outs"     – how many attributes it writes.
                      "subtitle" – optional subtitle rendered in the node body.
                      "setting_key" – optional; settings nodes name the
                                   simulator field they set.
    compile_active: False for nodes the canvas shows but compile cannot run.
                    An unwired one is ignored; a wired one is refused.
    params        : One ParamSpec per param, in display order.
    inputs        : The input ports.
    """
    name: str
    cat: str
    desc: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    defaults: dict
    compile_active: bool = True
    params: tuple[ParamSpec, ...] = ()
    inputs: PortsSpec = NO_INPUTS

    def __post_init__(self) -> None:
        # The param specs are the only place defaults are written, so the
        # "params" dict that compile reads can never disagree with them.
        if "params" in self.defaults or "param_options" in self.defaults:
            raise ValueError(
                f"catalog entry {self.name!r}: give params as ParamSpec, not in defaults"
            )
        self.defaults["params"] = {p.name: p.default for p in self.params}
        options = {p.name: p.options for p in self.params if p.type == "select"}
        if options:
            self.defaults["param_options"] = options

    def to_json(self) -> dict[str, Any]:
        """The entry as plain JSON, for the frontend codegen."""
        return {
            "name": self.name,
            "cat": self.cat,
            "desc": self.desc,
            "compile_active": self.compile_active,
            "inputs": self.inputs.to_json(),
            "params": [p.to_json() for p in self.params],
            "reads": list(self.reads),
            "writes": list(self.writes),
            "subtitle": self.defaults.get("subtitle"),
            "setting_key": self.defaults.get("setting_key"),
            "ins": self.defaults["ins"],
            "outs": self.defaults["outs"],
        }


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeType:
    """A catalog entry plus what compile and the evaluator need to run it.

    impl         : ``impl(inputs: Stream, params: Params) -> Stream``, run once
                   over the full index at cook time.  ``params`` holds every
                   param with defaults filled in: attr params resolved to the
                   attribute names to read (None when nothing is read),
                   attr_list params to a list of names, write params to the
                   final names to write.  ``params.node_id`` is the node's id
                   (use it as the writer) and ``params.env`` the cook
                   environment.  None means the node writes nothing and
                   passes its input on (terminals).
    check        : ``check(ctx: NodeCheck) -> None``, optional compile-time
                   checks that need the node's meaning (a threshold that must
                   parse, a select that must hold a known option).  See
                   kernel.schema.NodeCheck.
    lookback     : ``lookback(params) -> int``: rows of history this node
                   needs before its first good value, on top of its inputs.
    has_output   : False for nodes nothing may be wired out of (terminals).
    bypassable   : False when the bypass flag has no meaning and is ignored
                   (a data source, a terminal).
    fixed_writes : (name, dtype) points written whatever the params (a
                   data source's columns).
    detail_writes: names of write params that write detail values.
    primary      : the primary write when the node has no write params.
    legacy_reads : v1/v2 wire labels this node honours, label -> write param
                   name (or a fixed write name).  Only read for a wire whose
                   consumer param is empty (graphs not yet migrated to v3).
    legacy_strict: True when a legacy label of this type that a wire carries
                   out of another type is an error (one type's output
                   label on a wire out of another type).
    legacy_ignore: read params for which a v1/v2 wire label is ignored (the
                   default read is then the upstream's primary write).  The
                   Wave 0 engine ignored such labels for these params.
    meta         : free data for the domain layer (a family name for a cap...).
    """
    entry: NodeCatalogEntry
    impl: Optional[Callable[..., Any]] = None
    check: Optional[Callable[..., None]] = None
    lookback: Optional[Callable[..., int]] = None
    has_output: bool = True
    bypassable: bool = True
    fixed_writes: tuple[tuple[str, str], ...] = ()
    detail_writes: frozenset[str] = frozenset()
    primary: Optional[str] = None
    legacy_reads: Mapping[str, str] = field(default_factory=dict)
    legacy_strict: bool = False
    legacy_ignore: frozenset[str] = frozenset()
    meta: Mapping[str, Any] = field(default_factory=dict)
    module: str = ""

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def params(self) -> tuple[ParamSpec, ...]:
        return self.entry.params

    @property
    def inputs(self) -> PortsSpec:
        return self.entry.inputs

    @property
    def compile_active(self) -> bool:
        return self.entry.compile_active

    def param(self, name: str) -> Optional[ParamSpec]:
        for p in self.entry.params:
            if p.name == name:
                return p
        return None

    def read_params(self) -> list[ParamSpec]:
        return [p for p in self.entry.params if p.type in READ_PARAM_TYPES]

    def write_params(self) -> list[ParamSpec]:
        return [p for p in self.entry.params if p.type == "write"]

    def param_for_port(self, k: int) -> Optional[ParamSpec]:
        """The read param a wire on port in<k> feeds (plan D4 default reads).

        The param named like the port's label (``a``, ``b``, ``source``);
        else the list param of a dynamic node; else the k-th single read
        param.  Mirrors readParamForPort in the frontend's streamLabels.ts.
        """
        reads = self.read_params()
        if not reads:
            return None
        ports = self.entry.inputs.ports
        label = ports[k].label if 0 <= k < len(ports) else None
        if label is not None:
            for p in reads:
                if p.name == label:
                    return p
        lists = [p for p in reads if p.type == "attr_list"]
        if self.entry.inputs.dynamic and lists:
            return lists[0]
        singles = [p for p in reads if p.type == "attr"]
        if 0 <= k < len(singles):
            return singles[k]
        return lists[0] if lists else None

    def ports_for_param(self, name: str, wired_ports: Iterable[int]) -> list[int]:
        """The wired ports (indexes) whose default read lands on param *name*."""
        out = []
        for k in wired_ports:
            p = self.param_for_port(k)
            if p is not None and p.name == name:
                out.append(k)
        return out


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_TYPES: dict[str, NodeType] = {}


def register(node_type: NodeType) -> NodeType:
    """Add *node_type* to the registry and return it.

    A second registration of the same name from another module is refused
    (two files claiming one type name).  The same module registering again
    (a reload) replaces the old one.
    """
    name = node_type.name
    old = _TYPES.get(name)
    if old is not None and old.module and node_type.module and old.module != node_type.module:
        raise ValueError(
            f"node type {name!r} is registered by both {old.module} and {node_type.module}"
        )
    for p in node_type.params:
        if p.type not in PARAM_TYPES:
            raise ValueError(f"node type {name!r}: param {p.name!r} has unknown type {p.type!r}")
    _TYPES[name] = node_type
    return node_type


def register_node(
    *,
    name: str,
    cat: str,
    desc: str,
    params: tuple[ParamSpec, ...] = (),
    inputs: PortsSpec = NO_INPUTS,
    impl: Optional[Callable[..., Any]] = None,
    check: Optional[Callable[..., None]] = None,
    lookback: Optional[Callable[..., int]] = None,
    reads: Optional[tuple[str, ...]] = None,
    writes: Optional[tuple[str, ...]] = None,
    subtitle: Optional[str] = None,
    setting_key: Optional[str] = None,
    ins: Optional[int] = None,
    outs: Optional[int] = None,
    compile_active: bool = True,
    has_output: bool = True,
    bypassable: bool = True,
    fixed_writes: tuple[tuple[str, str], ...] = (),
    detail_writes: Iterable[str] = (),
    primary: Optional[str] = None,
    legacy_reads: Optional[Mapping[str, str]] = None,
    legacy_strict: bool = False,
    legacy_ignore: Iterable[str] = (),
    meta: Optional[Mapping[str, Any]] = None,
    module: Optional[str] = None,
) -> NodeType:
    """Build a NodeType (with its catalog entry) and register it.

    reads/writes default to what the params say: the name defaults of the
    read params (or ``@<param>``) and the defaults of the write params plus
    any fixed writes.  ins/outs default to the port count and write count.
    """
    if reads is None:
        reads = tuple(
            p.default if isinstance(p.default, str) and p.default else f"@{p.name}"
            for p in params if p.type in READ_PARAM_TYPES
        )
    if writes is None:
        writes = tuple(p.default for p in params if p.type == "write") + tuple(
            n for n, _dtype in fixed_writes
        )
    defaults: dict[str, Any] = {
        "ins": ins if ins is not None else (len(inputs.ports) if inputs.dynamic else inputs.max),
        "outs": outs if outs is not None else len(writes),
        "subtitle": subtitle,
    }
    if setting_key is not None:
        defaults["setting_key"] = setting_key
    entry = NodeCatalogEntry(
        name=name, cat=cat, desc=desc, reads=tuple(reads), writes=tuple(writes),
        defaults=defaults, compile_active=compile_active, params=tuple(params), inputs=inputs,
    )
    if module is None:
        module = getattr(impl or check, "__module__", "") or ""
    return register(NodeType(
        entry=entry, impl=impl, check=check, lookback=lookback, has_output=has_output,
        bypassable=bypassable, fixed_writes=tuple(fixed_writes),
        detail_writes=frozenset(detail_writes), primary=primary,
        legacy_reads=dict(legacy_reads or {}), legacy_strict=legacy_strict,
        legacy_ignore=frozenset(legacy_ignore),
        meta=dict(meta or {}), module=module,
    ))


def get(name: str) -> Optional[NodeType]:
    """The registered type called *name*, or None."""
    return _TYPES.get(name)


def all_types() -> list[NodeType]:
    """Every registered type, in registration order."""
    return list(_TYPES.values())


def unregister(name: str) -> None:
    """Remove a type (tests only)."""
    _TYPES.pop(name, None)


def strict_legacy_labels() -> frozenset[str]:
    """Legacy wire labels that only their own node type may carry."""
    out: set[str] = set()
    for t in _TYPES.values():
        if t.legacy_strict:
            out.update(t.legacy_reads)
    return frozenset(out)
