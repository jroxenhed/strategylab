"""Output terminals: Entry, Exit, and the not-yet-run Size and Stop.

Entry and Exit read one true/false ``signal`` (empty: what the wired node
writes first) and write nothing; the graph's buy and sell columns are read
off their input streams.  The terminal rules (one Entry, an Entry that must
get a signal, an unwired Exit as a warning) live in nodebuilder.compile,
because they are about the whole graph.

Nothing may be wired out of a terminal.  The bypass flag is ignored on a
terminal, as the Wave 0 engine did.

Size and Stop are catalog-only (compile_active False) until W4/W5: an
unwired one is ignored, a wired one is refused.
"""
from __future__ import annotations

from nodebuilder.kernel.registry import ParamSpec, PortSpec, PortsSpec, register_node

ONE_SIGNAL = PortsSpec(ports=(PortSpec("signal"),), dynamic=False, min=1, max=1)
# Exit may stay unwired (the strategy then only leaves by stops), and the
# Size/Stop terminals are ignored while unwired.
OPTIONAL_SIGNAL = PortsSpec(ports=(PortSpec("signal", optional=True),), dynamic=False, min=0, max=1)


def _check_terminal(ctx) -> None:
    # compile.py turns an empty or bypassed signal into the terminal errors.
    ctx.handled.add("signal")


register_node(
    name="entry", cat="output",
    desc="Entry terminal. Wire the buy-signal boolean here to trigger long entries.",
    params=(ParamSpec("signal", "attr", "signal", None, dtype="bool"),),
    inputs=ONE_SIGNAL, impl=None, check=_check_terminal,
    has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Entry", ins=1, outs=0, module=__name__,
)

register_node(
    name="exit", cat="output",
    desc="Exit terminal. Wire the sell-signal boolean here to trigger exits.",
    params=(ParamSpec("signal", "attr", "signal", None, dtype="bool", optional=True),),
    inputs=OPTIONAL_SIGNAL, impl=None, check=_check_terminal,
    has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Exit", ins=1, outs=0, module=__name__,
)

register_node(
    name="size", cat="output",
    desc="(T4) Size terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
    inputs=OPTIONAL_SIGNAL, compile_active=False, has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Size (T4)", ins=1, outs=0, module=__name__,
)

register_node(
    name="stop", cat="output",
    desc="(T4) Stop terminal. Not run yet: an unwired one is ignored, a wired one is refused by the backtest.",
    inputs=OPTIONAL_SIGNAL, compile_active=False, has_output=False, bypassable=False,
    reads=("@bool",), writes=(), subtitle="Stop (T4)", ins=1, outs=0, module=__name__,
)
