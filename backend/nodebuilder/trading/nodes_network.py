"""Network nodes: Subnet and its boundary nodes (plan D7, W5 item 5.0).

A Subnet is a folder of nodes.  Its children point to it through
``parent``, and wires connect only siblings.  A stream comes in through the
Subnet's input port ``in<k>`` and shows up inside on the Subnet input whose
``port`` param is ``k``.  It goes out through the one Subnet output, whose
input becomes the Subnet's output.

These nodes compute nothing themselves.  kernel/flatten.py takes them out
and splices the wires through them before compile, so compile never sees
one.  If one ever reaches the compile check, flatten was skipped, and the
check says so instead of computing something wrong.

``meta["network"]`` marks a network type and ``meta["boundary"]`` a
boundary type; flatten reads only those.  The Output Group (W5 item 5.B,
nodes_groups.py) registers its own type with the same meta.

``regime_net`` (display name "Regime", W5 item 5.C, plan D8) is a Subnet
with a fixed role: the regime filter of a group.  It holds the regime's
reference Ticker (the regime timeframe), its indicator or rules, the
comparison, the held-for rule (min_bars) and a Subnet output, and its output
feeds the group's ``regime`` terminal.  It computes nothing itself either;
the type name only tells the editor to draw it as a regime frame.  It is
not the ``regime`` terminal, which is a separate type (nodes_terminals.py).
"""
from __future__ import annotations

from nodebuilder.kernel.flatten import (
    BOUNDARY_INPUT,
    BOUNDARY_META,
    BOUNDARY_OUTPUT,
    NETWORK_META,
    PORT_PARAM,
)
from nodebuilder.kernel.registry import NO_INPUTS, ParamSpec, PortSpec, PortsSpec, register_node

# How many input ports a network may have.  Plenty; it only bounds the
# catalog's port list.
MAX_NETWORK_INPUTS = 32

NETWORK_INPUTS = PortsSpec(
    ports=(PortSpec("in0", optional=True),), dynamic=True, min=0, max=MAX_NETWORK_INPUTS,
)
BOUNDARY_OUTPUT_INPUTS = PortsSpec(
    ports=(PortSpec("in", optional=True),), dynamic=False, min=0, max=1,
)


def _pass_through(inputs, params):
    # Never runs: flatten takes these nodes out and the check below refuses
    # one that slips through.  A network's output is a stream it passes on,
    # so the impl says that.
    return inputs


def _not_flattened(ctx) -> None:
    ctx.fail(
        "boundary_invalid",
        f"{ctx.node.type} {ctx.node_id!r} reached compile without being flattened.  "
        "This is a bug: the graph must go through flatten() first.",
    )


register_node(
    name="subnet", cat="network",
    desc="Subnet. A folder of nodes with input ports and one output. Dive in to edit it.",
    inputs=NETWORK_INPUTS, impl=_pass_through, check=_not_flattened,
    has_output=True, bypassable=True,
    reads=(), writes=(), subtitle="Subnet", ins=0, outs=0,
    meta={NETWORK_META: True}, module=__name__,
)

REGIME_NETWORK_TYPE = "regime_net"

register_node(
    name=REGIME_NETWORK_TYPE, cat="network",
    desc="Regime. A network that works out when the group may trade (its output feeds "
         "the group's Regime terminal). Dive in to edit it.",
    inputs=NETWORK_INPUTS, impl=_pass_through, check=_not_flattened,
    has_output=True, bypassable=False,
    reads=(), writes=(), subtitle="Regime", ins=0, outs=0,
    meta={NETWORK_META: True, "regime": True}, module=__name__,
)

register_node(
    name="subnet_input", cat="network",
    desc="Subnet input. Adds an input port to this network.",
    params=(ParamSpec(PORT_PARAM, "int", "port", 0, min=0, max=MAX_NETWORK_INPUTS - 1),),
    inputs=NO_INPUTS, impl=_pass_through, check=_not_flattened,
    has_output=True, bypassable=False,
    reads=(), writes=(), subtitle="Input", ins=0, outs=0,
    meta={BOUNDARY_META: BOUNDARY_INPUT}, module=__name__,
)

register_node(
    name="subnet_output", cat="network",
    desc="Subnet output. The stream wired here is what the network puts out (one per network).",
    inputs=BOUNDARY_OUTPUT_INPUTS, impl=None, check=_not_flattened,
    has_output=False, bypassable=False,
    reads=(), writes=(), subtitle="Output", ins=1, outs=0,
    meta={BOUNDARY_META: BOUNDARY_OUTPUT}, module=__name__,
)
