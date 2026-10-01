"""The node graph kernel (plan decision D5).

Generic graph machinery with no trading words in it:

- ``stream``   the runtime Stream (points, detail, reserved prims, written_by)
               and the per-cook column store.
- ``schema``   StreamSchema and the static pass that checks a graph: ports,
               attribute reads and writes, merges, clashes, types, bypass.
- ``registry`` node types: param and port specs, catalog entries, and the
               registry every node module registers into.
- ``evaluate`` the column evaluator (one cook over the full index).

The trading node types live in ``nodebuilder.trading`` and register
themselves on import.  This package imports nothing from there.

Keep this file import-light: ``nodebuilder.models`` imports
``kernel.stream`` for STREAM_SCHEMA_VERSION.
"""
