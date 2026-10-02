"""Logic nodes: AND, OR (any number of signals) and NOT.

AND and OR read the signals named in ``terms`` (empty: what each wired node
writes first, in port order).  A term whose writer is bypassed is left out,
the way the rule engine skips a muted rule; with every term left out the
node is off.  NOT reads one signal.

Bar 0 is False for all three, as in the rule engine.  Inputs must be
true/false signals: an indicator or a price fed in would count as true
whenever it is non-zero, so the static check refuses it (attr_type).
"""
from __future__ import annotations

import numpy as np

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node
from nodebuilder.trading.nodes_compare import as_bool, first_bar_false

# AND/OR take one or more signals.  max is a generous cap for the canvas.
LOGIC_INPUTS = PortsSpec(ports=(PortSpec("in0"), PortSpec("in1", optional=True)),
                         dynamic=True, min=1, max=16)
ONE_SIGNAL = PortsSpec(ports=(PortSpec("signal"),), dynamic=False, min=1, max=1)


def _terms() -> ParamSpec:
    return ParamSpec("terms", "attr_list", "terms", None, dtype="bool")


def _combine(reduce):
    def impl(inputs, p):
        columns = [as_bool(inputs.column(name)) for name in p["terms"]]
        result = reduce(columns) if len(columns) > 1 else columns[0]
        return inputs.with_point(p["out"], first_bar_false(result), p.node_id, "bool")
    return impl


def _not(inputs, p):
    result = ~as_bool(inputs.column(p["signal"]))
    return inputs.with_point(p["out"], first_bar_false(result), p.node_id, "bool")


register_node(
    name="and", cat="logic", desc="True when ALL incoming boolean signals are true.",
    params=(_terms(), ParamSpec("out", "write", "out", "@and", dtype="bool")),
    inputs=LOGIC_INPUTS, impl=_combine(np.logical_and.reduce),
    reads=("@bool",), subtitle="AND", ins=2, outs=1, module=__name__,
)

register_node(
    name="or", cat="logic", desc="True when ANY incoming boolean signal is true.",
    params=(_terms(), ParamSpec("out", "write", "out", "@or", dtype="bool")),
    inputs=LOGIC_INPUTS, impl=_combine(np.logical_or.reduce),
    reads=("@bool",), subtitle="OR", ins=2, outs=1, module=__name__,
)

# NOT renders rule.negated (auto_render).  Bar 0 stays False: the rule
# engine never inverts at i < 1.
register_node(
    name="not", cat="logic", desc="Inverts the incoming boolean signal.",
    params=(ParamSpec("signal", "attr", "signal", None, dtype="bool"),
            ParamSpec("out", "write", "out", "@not", dtype="bool")),
    inputs=ONE_SIGNAL, impl=_not,
    reads=("@bool",), subtitle="NOT", ins=1, outs=1, module=__name__,
)
