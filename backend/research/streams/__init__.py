"""Stream protocol + registry for the StrategyLab research workbench.

A Stream is a named event source that emits EventRecord instances.
New streams register themselves by calling register() at module load.

Auto-registration: importing this package triggers `from . import form4`
(and any future stream modules added at the bottom of this file), which
calls register(<StreamClass>()) at module load — so any code that does
`from research.streams import _REGISTRY` is guaranteed to see the registry
populated without needing an explicit setup call.

The F389 run service dispatches to registered streams by looking up
spec.stream in _REGISTRY and calling stream.iter_events(...,
event_filter=spec.event_filter) — see Stream.iter_events Protocol signature.
"""
from __future__ import annotations

import sys
import os
from typing import Iterator, Optional, Protocol, runtime_checkable
from datetime import date

# Locate backend dir for path resolution when running as standalone script
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(os.path.dirname(_THIS_DIR))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from research.event_study import EventRecord  # noqa: E402


@runtime_checkable
class Stream(Protocol):
    """A named event source that emits EventRecord instances."""

    @property
    def stream_id(self) -> str:
        """Unique registry key (e.g. 'form4')."""
        ...

    def iter_events(
        self,
        start: date,
        end: date,
        universe: Optional[list[str]] = None,
        *,
        event_filter: Optional[dict] = None,
    ) -> Iterator[EventRecord]:
        """Yield EventRecord objects in chronological order.

        Parameters
        ----------
        start, end     : inclusive date range.
        universe       : restrict to these ticker symbols; None = all.
        event_filter   : validated predicates from PremiseSpec.event_filter.
            The F389 run service retrieves the stream from _REGISTRY and passes
            spec.event_filter here so stream-level filters (e.g. form_types)
            are applied.  Implementation is stream-specific; keys must be in
            filter_vocabulary().
        """
        ...

    def filter_vocabulary(self) -> frozenset[str]:
        """Declared keys that event_filter may reference."""
        ...

    def dose_vocabulary(self) -> frozenset[str]:
        """Declared keys that dose_params may reference."""
        ...


# Registry: stream_id → Stream instance
_REGISTRY: dict[str, "Stream"] = {}


def register(stream: "Stream") -> None:
    """Register a stream instance by its stream_id."""
    _REGISTRY[stream.stream_id] = stream


def get(stream_id: str) -> "Stream":
    """Retrieve a registered stream by id; raises KeyError if absent."""
    if stream_id not in _REGISTRY:
        raise KeyError(f"No stream registered for id {stream_id!r}")
    return _REGISTRY[stream_id]


# Auto-register all built-in streams at import time so that any code doing
# `from research.streams import _REGISTRY` (or `get(...)`) is guaranteed to
# see the full registry without needing an explicit setup call.  Each stream
# module calls register() on its own instance at the bottom of the file, so
# importing the module is sufficient — no central registration table to maintain.
from . import form4  # noqa: E402, F401
