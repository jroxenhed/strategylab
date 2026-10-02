"""Graph storage: one JSON file per graph on the server (F435 W1 item 1.B, decision D2).

Each graph lives in ``<data dir>/graphs/<graph_id>.json`` as an envelope:

    {id, rev, name, description, created_at, updated_at, graph}

Library assets (F435 W6 item 6.B) live in ``<data dir>/library/<name>/<version>.json``,
one immutable file per version (see AssetLibrary below).  The graph store
keeps an in-memory index of which saved graphs use which asset (``used_by``).

Rules:
  - Writes go through ``fileutil.atomic_write_text``. No new temp-file code.
  - Each graph file has its own ``threading.Lock``. A save checks ``rev`` and
    writes while holding that lock, so two saves of the same graph take turns
    and the second one sees the new rev (F444 lesson: an old snapshot must
    never land last).
  - Names are unique without regard to case. A second, store-wide lock guards
    every step that picks or changes a name. Lock order is always file lock
    first, then the name lock, so the two can never deadlock.
  - The graph is parsed with ``Graph.model_validate`` (which runs the version
    migration) and stored in its parsed, current-version form. So a stored
    graph always loads the same way on every path. Each node's input wires
    are written in port order (see ``Graph._wires_in_port_order``).
  - A file that cannot be read (bad JSON, an envelope without a valid id,
    rev, name or graph) raises GraphCorruptError, never a graph parse error:
    the stored file is at fault, not the caller's graph. The list skips it.

The data dir is read on every call (``STRATEGYLAB_DATA_DIR``, else the same
default ``journal.DATA_DIR`` that bots.json uses), so tests can point it at a
temporary folder.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import secrets
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from fileutil import atomic_write_text
from nodebuilder.models import Graph, GraphValidationError

logger = logging.getLogger(__name__)

GRAPHS_DIRNAME = "graphs"
LIBRARY_DIRNAME = "library"

NAME_MAX_LEN = 80
IMPORTED_SUFFIX = " (imported)"

_ID_CHARS = set("0123456789abcdef")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GraphNotFoundError(Exception):
    """No graph with this id."""


class RevConflictError(Exception):
    """The caller's rev is not the stored rev."""

    def __init__(self, current_rev: int) -> None:
        super().__init__(f"rev conflict (current rev is {current_rev})")
        self.current_rev = current_rev


class NameTakenError(Exception):
    """Another graph already uses this name (case does not matter)."""


class InvalidNameError(ValueError):
    """The name is empty or longer than NAME_MAX_LEN characters."""


class GraphCorruptError(Exception):
    """A stored graph file cannot be read: bad JSON, or an envelope without a
    valid id, rev, name or graph.  It is the stored file's fault, never the
    caller's, so routes answer 409 ``graph_corrupt`` rather than a graph
    error about the request body.  Not a ValueError on purpose: routes treat
    ValueError as "the body's graph does not parse".
    """

    def __init__(self, graph_id: str, reason: str) -> None:
        super().__init__(
            f"The stored file for graph {graph_id} is damaged and cannot be read ({reason})."
        )
        self.graph_id = graph_id
        self.reason = reason


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """The data dir that bots.json uses. Read on every call (tests change it)."""
    env = os.environ.get("STRATEGYLAB_DATA_DIR")
    if env:
        return Path(env)
    from journal import DATA_DIR  # same default as bots.json and the journal

    return DATA_DIR


def graph_dirs(base: Optional[Path] = None) -> list[Path]:
    """The folders this module writes to. main.py passes them to cleanup_orphan_tmps,
    because that function does not look inside sub-folders.  Asset versions
    are written in ``library/<name>/``, so each asset folder on disk is listed
    after the two fixed ones."""
    base = data_dir() if base is None else Path(base)
    library = base / LIBRARY_DIRNAME
    dirs = [base / GRAPHS_DIRNAME, library]
    try:
        dirs += sorted(p for p in library.iterdir() if p.is_dir() and is_asset_name(p.name))
    except OSError:  # no library folder yet
        pass
    return dirs


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def new_graph_id() -> str:
    """``g_`` plus 12 lowercase hex characters."""
    return "g_" + secrets.token_hex(6)


def is_graph_id(value: str) -> bool:
    """True for a well-formed graph id. Also keeps odd strings out of file paths."""
    return (
        isinstance(value, str)
        and len(value) == 14
        and value.startswith("g_")
        and all(c in _ID_CHARS for c in value[2:])
    )


# Asset names (plan W6): lower case, digits and underscores, at most 64.
ASSET_NAME_RE = re.compile(r"[a-z_][a-z0-9_]{0,63}")


def is_asset_name(value: Any) -> bool:
    """True for a valid library asset name. Also keeps odd strings out of file paths."""
    return isinstance(value, str) and ASSET_NAME_RE.fullmatch(value) is not None


def clean_name(name: Any) -> str:
    """Trim the name and check its length. Raises InvalidNameError."""
    if not isinstance(name, str):
        raise InvalidNameError("Graph name must be a string.")
    name = name.strip()
    if not name:
        raise InvalidNameError("Graph name must not be empty.")
    if len(name) > NAME_MAX_LEN:
        raise InvalidNameError(f"Graph name must be at most {NAME_MAX_LEN} characters.")
    return name


def canonical_graph(raw: Any) -> dict:
    """Parse a graph (migrating old versions) and return its stored JSON form.

    Raises whatever ``Graph.model_validate`` raises for a graph that does not
    parse (pydantic ValidationError, GraphValidationError, ValueError), or
    TypeError when the value is not a JSON object.
    """
    if not isinstance(raw, dict):
        raise TypeError("graph must be a JSON object")
    graph = Graph.model_validate(raw)
    return graph.model_dump(mode="json", by_alias=True)


def content_hash(graph: dict) -> str:
    """Stable hash of a stored graph, used to spot seed duplicates."""
    text = json.dumps(graph, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_item(env: dict) -> dict:
    """The GraphListItem for one envelope."""
    graph = env.get("graph") or {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    description = env.get("description", "")
    updated_at = env.get("updated_at", "")
    return {
        "id": env["id"],
        "rev": env["rev"],
        "name": env["name"],
        "description": description if isinstance(description, str) else "",
        # The list is sorted by this, so it must always be a string.
        "updated_at": updated_at if isinstance(updated_at, str) else "",
        "node_count": len(nodes) if isinstance(nodes, dict) else 0,
        "groups": group_names(nodes),
    }


def group_names(nodes: Any) -> list[str]:
    """The Output Group names of a stored graph's nodes, in node order
    (F435 W5 DI-04): each output_group node's name (its id when the name is
    empty, as nodes_groups names it), or ["main"] for a graph with none
    (the implicit group).  Read from the JSON, no compile."""
    names: list[str] = []
    for n in (nodes.values() if isinstance(nodes, dict) else ()):
        if isinstance(n, dict) and n.get("type") == "output_group":
            name = n.get("name") or n.get("id")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names or ["main"]


def assets_used(graph: Any) -> frozenset[str]:
    """Names of the library assets a stored graph's nodes point at
    (``asset_ref.name``), locked or not: an unlocked copy keeps its
    asset_ref for provenance, and "where used" lists it too.  Read from the
    JSON, no parse."""
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    names = set()
    for n in (nodes.values() if isinstance(nodes, dict) else ()):
        ref = n.get("asset_ref") if isinstance(n, dict) else None
        if isinstance(ref, dict) and is_asset_name(ref.get("name")):
            names.add(ref["name"])
    return frozenset(names)


def envelope_problem(env: Any, graph_id: str) -> Optional[str]:
    """Why a stored envelope cannot be used, or None when it is fine."""
    if not isinstance(env, dict):
        return "not a JSON object"
    if env.get("id") != graph_id:
        return "its id does not match the file name"
    rev = env.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int):
        return "rev is missing or not a whole number"
    if not isinstance(env.get("name"), str):
        return "name is missing"
    if not isinstance(env.get("graph"), dict):
        return "graph is missing"
    return None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class GraphStore:
    """All graph files under one data dir."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base)
        self.graphs_dir = self.base / GRAPHS_DIRNAME
        self.library_dir = self.base / LIBRARY_DIRNAME
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Held while a name is checked and then written, so two creates or
        # renames cannot both take the same name.
        self._names_lock = threading.Lock()
        # The head index: graph_id -> (rev, name) of every readable graph
        # file (plan W5 5.D).  The bot list reads it, so building the bot
        # summary never opens a graph file.  Filled here, at start, from the
        # files on disk; every write and delete below keeps it current.
        # _heads_lock is always the innermost lock: nothing else is taken
        # while it is held.
        self._heads_lock = threading.Lock()
        self._heads: dict[str, tuple[int, str]] = {}
        # The used_by index (plan W6 6.B): graph_id -> names of the library
        # assets its nodes point at.  Kept with the head index, under the
        # same lock, so the asset list never opens a graph file either.
        self._uses: dict[str, frozenset[str]] = {}
        self._load_heads()

    # -- locks and files -----------------------------------------------------

    def _lock_for(self, graph_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(graph_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[graph_id] = lock
            return lock

    def _drop_lock(self, graph_id: str) -> None:
        """Forget a deleted graph's lock, so the map does not grow forever.
        A caller still waiting on the old lock then finds no file (404)."""
        with self._locks_guard:
            self._locks.pop(graph_id, None)

    def _path(self, graph_id: str) -> Path:
        if not is_graph_id(graph_id):
            raise GraphNotFoundError(graph_id)
        return self.graphs_dir / f"{graph_id}.json"

    def _read(self, graph_id: str) -> dict:
        """The stored envelope.  Raises GraphNotFoundError or GraphCorruptError."""
        path = self._path(graph_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise GraphNotFoundError(graph_id) from None
        except (OSError, ValueError) as exc:  # ValueError: bad UTF-8
            raise GraphCorruptError(graph_id, f"unreadable: {exc}") from exc
        try:
            env = json.loads(text)
        except ValueError as exc:
            raise GraphCorruptError(graph_id, f"not valid JSON: {exc}") from exc
        problem = envelope_problem(env, graph_id)
        if problem is not None:
            raise GraphCorruptError(graph_id, problem)
        return env

    def _write(self, env: dict) -> None:
        self.graphs_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path(env["id"]), json.dumps(env, indent=2))
        self._set_head(env)

    # -- head index (rev and name per graph id) -------------------------------

    def _load_heads(self) -> None:
        """Fill the head index from the files on disk (once, at start)."""
        envs = self._all()
        heads = {env["id"]: (env["rev"], env["name"]) for env in envs}
        uses = {env["id"]: assets_used(env["graph"]) for env in envs}
        with self._heads_lock:
            self._heads = heads
            self._uses = uses

    def _set_head(self, env: dict) -> None:
        """Record a graph's new rev and name.  Called after its file is
        written, under that graph's file lock, so the index never runs ahead
        of the disk and two saves of one graph update it in order."""
        uses = assets_used(env["graph"])
        with self._heads_lock:
            self._heads[env["id"]] = (env["rev"], env["name"])
            self._uses[env["id"]] = uses

    def _drop_head(self, graph_id: str) -> None:
        with self._heads_lock:
            self._heads.pop(graph_id, None)
            self._uses.pop(graph_id, None)

    def asset_users(self) -> dict[str, list[dict]]:
        """asset name -> [{graph_id, name}] of the saved graphs that use it,
        sorted by graph name.  Reads the in-memory index only, never a file."""
        out: dict[str, list[dict]] = {}
        with self._heads_lock:
            rows = [(gid, self._heads[gid][1], names)
                    for gid, names in self._uses.items() if gid in self._heads]
        for gid, gname, names in rows:
            for asset in names:
                out.setdefault(asset, []).append({"graph_id": gid, "name": gname})
        for users in out.values():
            users.sort(key=lambda u: (u["name"].casefold(), u["graph_id"]))
        return out

    def head(self, graph_id: str) -> Optional[tuple[int, str]]:
        """(rev, name) of a stored graph, or None when there is no readable
        graph with this id (deleted, never saved, or a damaged file).
        Reads the in-memory index only, never a file."""
        with self._heads_lock:
            return self._heads.get(graph_id)

    def _all(self) -> list[dict]:
        """Every readable envelope. A broken file is logged and skipped."""
        if not self.graphs_dir.exists():
            return []
        out: list[dict] = []
        for path in sorted(self.graphs_dir.glob("g_*.json")):
            if not is_graph_id(path.stem):
                continue
            try:
                env = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("skipping unreadable graph file %s: %s", path, exc)
                continue
            problem = envelope_problem(env, path.stem)
            if problem is not None:
                logger.warning("skipping malformed graph file %s: %s", path, problem)
                continue
            out.append(env)
        return out

    def _name_owner(self, name: str) -> Optional[dict]:
        """The envelope that already uses this name (any case), if any.
        Call with _names_lock held."""
        folded = name.casefold()
        for env in self._all():
            if str(env.get("name", "")).casefold() == folded:
                return env
        return None

    # -- public API ----------------------------------------------------------

    def list(self) -> list[dict]:
        items = [list_item(env) for env in self._all()]
        items.sort(key=lambda it: it["updated_at"], reverse=True)
        return items

    def get(self, graph_id: str) -> dict:
        """The stored envelope, with its graph in the current version.

        A graph saved by older code is migrated on the way out (F435 W2,
        MD-02), so the editor never edits an un-migrated graph (v2 left write
        names to be re-derived on every save).  The file itself is rewritten
        only by the next save.  A graph that does not load is returned as
        stored; /validate then reports why.
        """
        env = self._read(graph_id)
        try:
            graph = canonical_graph(env["graph"])
        except (GraphValidationError, ValidationError, ValueError, TypeError) as exc:
            logger.warning("graph %s does not load, returned as stored: %s", graph_id, exc)
            return env
        return {**env, "graph": graph}

    def create(
        self,
        name: str,
        graph: Any = None,
        description: Optional[str] = None,
        duplicate_of: Optional[str] = None,
    ) -> dict:
        """Make a new graph at rev 1.

        ``graph`` is parsed and migrated. With ``duplicate_of`` the graph (and,
        when none is given, the description) comes from that stored graph.
        """
        name = clean_name(name)
        if duplicate_of is not None:
            source = self._read(duplicate_of)
            try:
                stored = canonical_graph(source["graph"])
            except (GraphValidationError, ValidationError, ValueError, TypeError) as exc:
                # The stored graph is at fault, not the request: say so.
                raise GraphCorruptError(duplicate_of, f"its graph does not load: {exc}") from exc
            if description is None:
                description = source.get("description", "")
        else:
            stored = canonical_graph({} if graph is None else graph)
        with self._names_lock:
            if self._name_owner(name) is not None:
                raise NameTakenError(name)
            return self._create_locked(name, stored, description or "")

    def _create_locked(self, name: str, stored: dict, description: str) -> dict:
        graph_id = new_graph_id()
        while self._path(graph_id).exists():  # a clash is very unlikely; just retry
            graph_id = new_graph_id()
        now = _now()
        env = {
            "id": graph_id,
            "rev": 1,
            "name": name,
            "description": description,
            "created_at": now,
            "updated_at": now,
            "graph": stored,
        }
        self._write(env)
        return env

    def update(
        self,
        graph_id: str,
        rev: int,
        graph: Any,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        """Save a new version. ``rev`` must equal the stored rev; the result has rev+1."""
        stored = canonical_graph(graph)
        new_name = clean_name(name) if name is not None else None
        self._path(graph_id)  # a malformed id is a 404 before it gets a lock
        with self._lock_for(graph_id):
            try:
                env = self._read(graph_id)
            except GraphNotFoundError:
                self._drop_lock(graph_id)
                raise
            if env["rev"] != rev:
                raise RevConflictError(env["rev"])
            env = dict(env)
            env["graph"] = stored
            if description is not None:
                env["description"] = description
            env["rev"] = env["rev"] + 1
            env["updated_at"] = _now()
            if new_name is not None and new_name != env["name"]:
                with self._names_lock:
                    owner = self._name_owner(new_name)
                    if owner is not None and owner["id"] != graph_id:
                        raise NameTakenError(new_name)
                    env["name"] = new_name
                    self._write(env)
            else:
                self._write(env)
            return env

    def delete(self, graph_id: str, rev: int) -> None:
        """Delete a graph at ``rev``.

        A damaged file has no rev to check.  It is moved aside to
        ``<id>.json.corrupt`` (its .bak stays) rather than deleted, so the
        user can remove it from the list and the data can still be rescued.
        """
        path = self._path(graph_id)  # a malformed id is a 404 before it gets a lock
        with self._lock_for(graph_id):
            try:
                env = self._read(graph_id)
            except GraphNotFoundError:
                self._drop_lock(graph_id)
                raise
            except GraphCorruptError as exc:
                path.replace(path.with_name(path.name + ".corrupt"))
                logger.warning("graph %s: moved damaged file aside on delete (%s)",
                               graph_id, exc.reason)
                self._drop_head(graph_id)
                self._drop_lock(graph_id)
                return
            if env["rev"] != rev:
                raise RevConflictError(env["rev"])
            path.unlink()
            self._drop_head(graph_id)
            # atomic_write_text keeps one .bak copy; the user asked for a delete.
            try:
                Path(str(path) + ".bak").unlink()
            except FileNotFoundError:
                pass
            self._drop_lock(graph_id)

    def seed(self, legacy: Any) -> dict:
        """Import graphs from the old localStorage key. Safe to run many times.

        Accepts the object shape ``{name: graph}``, the array shape
        ``[{name, graph}]``, or that JSON as a string. Anything else imports
        nothing. An entry whose name and content match a stored graph is
        skipped as "duplicate". A name clash with different content is stored
        as "<name> (imported)".
        """
        imported: list[str] = []
        skipped: list[dict] = []
        entries = _legacy_entries(legacy)
        with self._names_lock:
            existing = self._all()
            for raw_name, raw_graph in entries:
                label = raw_name if isinstance(raw_name, str) else ""
                try:
                    stored = canonical_graph(raw_graph)
                except Exception as exc:  # any parse failure skips this entry only
                    logger.info("seed: skipping %r, graph does not parse: %s", label, exc)
                    skipped.append({"name": label, "reason": "invalid"})
                    continue
                base = _seed_name(label)
                digest = content_hash(stored)
                if _is_seed_duplicate(existing, base, digest):
                    skipped.append({"name": label, "reason": "duplicate"})
                    continue
                taken = {str(e.get("name", "")).casefold() for e in existing}
                env = self._create_locked(_free_name(base, taken), stored, "")
                existing.append(env)
                imported.append(env["id"])
        return {"imported": imported, "skipped": skipped}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _legacy_entries(legacy: Any) -> list[tuple[Any, Any]]:
    """(name, graph) pairs from either legacy shape. Garbage gives []."""
    if isinstance(legacy, str):
        try:
            legacy = json.loads(legacy)
        except ValueError:
            return []
    if isinstance(legacy, list):
        return [
            (item.get("name"), item.get("graph"))
            for item in legacy
            if isinstance(item, dict) and isinstance(item.get("graph"), dict)
        ]
    if isinstance(legacy, dict):
        return [(k, v) for k, v in legacy.items() if isinstance(v, dict)]
    return []


def _seed_name(label: str) -> str:
    """A valid name for a legacy entry. Room is kept for the " (imported N)" suffix."""
    name = label.strip() or "Imported graph"
    return name[: NAME_MAX_LEN - len(IMPORTED_SUFFIX) - 3]


def _seed_names(base: str) -> list[str]:
    """The names a seed entry may end up with, in the order they are tried."""
    names = [base, base + IMPORTED_SUFFIX]
    names += [f"{base} (imported {n})" for n in range(2, 100)]
    return names


def _is_seed_duplicate(existing: list[dict], base: str, digest: str) -> bool:
    """True when a stored graph has one of this entry's names and the same content."""
    names = {n.casefold() for n in _seed_names(base)}
    for env in existing:
        if str(env.get("name", "")).casefold() not in names:
            continue
        # Parse the stored graph again, so one saved before a schema change
        # hashes the same as the freshly migrated legacy entry.
        try:
            stored = canonical_graph(env.get("graph") or {})
        except Exception:
            continue
        if content_hash(stored) == digest:
            return True
    return False


def _free_name(base: str, taken: set[str]) -> str:
    for name in _seed_names(base):
        if name.casefold() not in taken:
            return name
    # 100 clashes on one name: fall back to a random tail so the import still lands.
    return f"{base} ({secrets.token_hex(3)})"


# ---------------------------------------------------------------------------
# Asset library (F435 W6 item 6.B)
# ---------------------------------------------------------------------------

# Param types a promoted param may have (plan 4.3 ParamType).
PROMOTED_TYPES = frozenset({
    "number", "int", "string", "select", "bool",
    "attr", "attr_list", "write", "path", "time_range",
})
DESCRIPTION_MAX_LEN = 500


class AssetNotFoundError(Exception):
    """No live asset file with this name and version."""


class AssetCorruptError(Exception):
    """A stored asset file cannot be read (bad JSON or a wrong shape)."""

    def __init__(self, name: str, version: int, reason: str) -> None:
        super().__init__(
            f"The stored file for asset {name} version {version} is damaged and cannot be read ({reason})."
        )
        self.name = name
        self.version = version
        self.reason = reason


class InvalidAssetError(ValueError):
    """The asset in a save request is not usable.  ``code`` is a diagnostic
    code (``name_invalid``, ``promoted_invalid`` or ``request_invalid``)."""

    def __init__(self, code: str, message: str, node_id: Optional[str] = None,
                 param: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.node_id = node_id
        self.param = param


def canonical_network(network: Any) -> dict:
    """Parse an asset's network as a graph of the current version and return
    its stored ``{nodes, wires}`` form (names, ports and wire order filled
    in, the same as a saved graph's).  A network taken from an unlocked
    copy (ids like ``inst::sma``) is stored with plain ids (KA-2,
    kernel.assets.plain_network_ids).  Raises what Graph.model_validate
    raises for a network that does not parse, or TypeError."""
    if not isinstance(network, dict):
        raise TypeError("network must be a JSON object")
    from nodebuilder.kernel.assets import plain_network_ids
    from nodebuilder.migrate import CURRENT_GRAPH_VERSION

    network = plain_network_ids(network)
    graph = Graph.model_validate({
        "_version": CURRENT_GRAPH_VERSION,
        "nodes": network.get("nodes") or {},
        "wires": network.get("wires") or [],
    })
    dumped = graph.model_dump(mode="json", by_alias=True)
    return {"nodes": dumped["nodes"], "wires": dumped["wires"]}


def promoted_target_node(nodes: dict, target: Any) -> Optional[str]:
    """The id of the node a promoted target names, or None.

    A target is a path relative to the network, ending in a param name:
    ``sma/period`` is the param ``period`` of the root node named ``sma``;
    ``inner/sma/period`` goes one network down.  Node names are unique among
    siblings, so each step matches at most one node.  The path is read the
    way compile reads it (kernel/flatten.py ``_check_promoted``): empty and
    ``.`` steps are skipped; a leading ``/`` or a ``..`` step names nothing.
    """
    if not isinstance(target, str) or target.startswith("/"):
        return None
    node_part, _, param = target.rpartition("/")
    if not node_part or not param:
        return None
    parent: Optional[str] = None
    for segment in node_part.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return None
        found = [nid for nid, n in nodes.items()
                 if isinstance(n, dict) and n.get("parent") == parent and n.get("name") == segment]
        if len(found) != 1:
            return None
        parent = found[0]
    return parent


_NUMBER_TYPES = ("int", "number")


def _default_fits(ptype: str, value: Any) -> bool:
    """True when a promoted param's default has the right kind of value.

    The compile rule (kernel/flatten.py ``value_fits``, KA-9), so a default
    the library stores is one an instance can use: a list for a multi
    select, 5.0 for an int, any value for a time_range.  One rule more: a
    number type takes a JSON number, never a numeric string, so a stored
    asset keeps clean values.  None is allowed for every type (the target's
    own value then stands)."""
    from nodebuilder.kernel.flatten import value_fits

    if ptype in _NUMBER_TYPES and isinstance(value, str):
        return False
    return value_fits(ptype, value)


def _instance_check_graph(name: str, network: dict, promoted: list[dict]) -> Graph:
    """A throwaway graph with one plain network node named *name* that holds
    *network* and promotes *promoted* with no values of its own: the shape
    a new instance of the asset has once compile expanded it."""
    from nodebuilder.migrate import CURRENT_GRAPH_VERSION
    from nodebuilder.models import SUBNET_TYPE

    nodes = network.get("nodes") or {}
    holder = "asset"
    n = 1
    while holder in nodes:
        holder = f"asset_{n}"
        n += 1
    out = {holder: {"id": holder, "type": SUBNET_TYPE, "name": name, "parent": None,
                    "params": {}, "promoted": promoted}}
    for nid, node in nodes.items():
        parent = node.get("parent") if isinstance(node, dict) else None
        out[nid] = {**node, "parent": holder if parent is None else parent}
    return Graph.model_validate({"_version": CURRENT_GRAPH_VERSION, "nodes": out,
                                 "wires": network.get("wires") or []})


def kernel_promoted_problem(name: str, network: dict, promoted: list[dict],
                            resolve: Any = None) -> Optional[tuple[str, Optional[str]]]:
    """(message, param) of the first ``promoted_invalid`` problem compile
    would report on every instance of this asset, or None (LD-02).

    Runs the compile rules themselves (kernel/flatten.py): the target is a
    param of that node, the type matches it, the name is not already a
    param of a network node, two promoted params do not set one target, the
    default fits.  Nested library instances in the network are expanded
    with *resolve* first, as compile does.  When the network cannot be
    checked this way (a nested asset is missing, the check itself fails),
    returns None: the store's own checks still ran.
    """
    from nodebuilder import trading  # noqa: F401  (registers every node type)
    from nodebuilder.kernel.assets import expand_assets
    from nodebuilder.kernel.flatten import PROMOTED_INVALID, flatten

    try:
        graph = _instance_check_graph(name, network, promoted)
        expanded, problems = expand_assets(graph, resolve)
        if any(getattr(p, "severity", "error") == "error" for p in problems):
            return None
        found = flatten(expanded).found
    except Exception:  # noqa: BLE001 (the check must never break a save)
        logger.exception("asset %s: promoted params could not be checked by compile", name)
        return None
    for diag, _exc in found:
        if getattr(diag, "code", None) == PROMOTED_INVALID:
            return diag.message, getattr(diag, "param", None)
    return None


def check_promoted(promoted: Any, network: dict, *, asset_name: Optional[str] = None,
                   resolve: Any = None) -> list[dict]:
    """Check the promoted params against the (canonical) network and return
    them as plain dicts.  Raises InvalidAssetError ``promoted_invalid``.

    With *asset_name*, the params are also checked by the
    compile rules (kernel_promoted_problem), so the library never stores a
    version whose every instance fails to compile; *resolve* looks up
    nested library assets for that check."""
    if not isinstance(promoted, list):
        raise InvalidAssetError("promoted_invalid", "promoted must be a list.")
    nodes = network.get("nodes") or {}
    out: list[dict] = []
    seen: set[str] = set()
    for p in promoted:
        if not isinstance(p, dict):
            raise InvalidAssetError("promoted_invalid", "Each promoted param must be an object.")
        name, ptype, target = p.get("name"), p.get("type"), p.get("target")
        if not is_asset_name(name):
            raise InvalidAssetError(
                "promoted_invalid",
                f"Promoted param name {name!r} must use lower case letters, digits and "
                f"underscores (at most 64).", param=name if isinstance(name, str) else None)
        if name in seen:
            raise InvalidAssetError("promoted_invalid", f"Two promoted params are named {name!r}.",
                                    param=name)
        seen.add(name)
        if ptype not in PROMOTED_TYPES:
            raise InvalidAssetError("promoted_invalid",
                                    f"Promoted param {name!r} has an unknown type {ptype!r}.",
                                    param=name)
        node_id = promoted_target_node(nodes, target)
        if node_id is None:
            raise InvalidAssetError(
                "promoted_invalid",
                f"Promoted param {name!r} points at {target!r}, which is not a node param "
                f"inside the asset.", param=name)
        if not _default_fits(ptype, p.get("default")):
            raise InvalidAssetError(
                "promoted_invalid",
                f"Promoted param {name!r} has a default of the wrong kind for type {ptype!r}.",
                node_id=node_id, param=name)
        label = p.get("label")
        out.append({"name": name, "label": label if isinstance(label, str) and label else name,
                    "target": target, "type": ptype, "default": p.get("default")})
    if asset_name is not None and out:
        problem = kernel_promoted_problem(asset_name, network, out, resolve)
        if problem is not None:
            message, param = problem
            raise InvalidAssetError("promoted_invalid", message, param=param)
    return out


def asset_file_problem(data: Any, name: str, version: int) -> Optional[str]:
    """Why a stored asset file cannot be used, or None when it is fine."""
    if not isinstance(data, dict):
        return "not a JSON object"
    if data.get("name") != name:
        return "its name does not match the folder"
    if data.get("version") != version or isinstance(data.get("version"), bool):
        return "its version does not match the file name"
    network = data.get("network")
    if not isinstance(network, dict) or not isinstance(network.get("nodes"), dict) \
            or not isinstance(network.get("wires"), list):
        return "network is missing"
    if not isinstance(data.get("promoted"), list):
        return "promoted is missing"
    return None


def _version_of(stem: str) -> Optional[int]:
    """The version a file stem names ("3" -> 3), or None for anything else."""
    if not stem.isdigit() or (len(stem) > 1 and stem[0] == "0"):
        return None
    return int(stem)


class AssetVersionTakenError(RuntimeError):
    """A version file the store was about to write already exists: something
    outside this process's lock wrote it (LD-07).  Nothing was replaced."""

    def __init__(self, name: str, version: int) -> None:
        super().__init__(
            f"Version {version} of asset {name} was written by something else at the same "
            f"moment, so this save did not replace it.  Save again."
        )
        self.name = name
        self.version = version


def _write_new_file(path: Path, text: str) -> None:
    """Write *path* only if it does not exist yet, atomically (LD-07).

    The text goes to a temp file in the same folder (fsync'd), which is then
    hard-linked to *path*: the link fails when *path* exists, so a version
    file is never replaced, even by a writer outside this process.  On a
    filesystem without hard links it falls back to check-then-rename.
    Raises AssetVersionTakenError when the file exists; OSError otherwise.
    """
    import tempfile

    fd = tempfile.NamedTemporaryFile(mode="w", delete=False, dir=str(path.parent),
                                     suffix=".tmp", encoding="utf-8")
    tmp = fd.name
    try:
        try:
            fd.write(text)
            fd.flush()
            os.fsync(fd.fileno())
        finally:
            fd.close()
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise AssetVersionTakenError(path.parent.name, int(path.stem)) from None
        except OSError as exc:  # no hard links here: check, then rename
            logger.warning("asset file %s: hard link failed (%s); using rename", path, exc)
            if path.exists():
                raise AssetVersionTakenError(path.parent.name, int(path.stem)) from None
            os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class AssetLibrary:
    """All asset files under one data dir: ``library/<name>/<version>.json``.

    Rules:
      - A version file is written once and never changed (immutable).  A new
        save of a name is version latest + 1, and the write itself refuses
        to replace a file that exists (``_write_new_file``).
      - DELETE removes one version's file and leaves an empty
        ``<version>.deleted`` marker, so the number is never given out again:
        a graph pinned to a deleted version shows ``asset_missing`` rather
        than silently getting a different asset under the same number.
      - Files are immutable, so parsed files are cached in memory (at most
        CACHE_MAX, least recently used out first); a delete drops its entry,
        and a read that was in flight when a delete ran never puts it back.
        Callers always get their own deep copy.
      - One lock guards picking a version, writing and deleting.
    """

    CACHE_MAX = 256
    """Most parsed asset files kept in memory (LD-06).  An asset is one
    network, at most 1 MB as posted and usually a few KB."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base)
        self.dir = self.base / LIBRARY_DIRNAME
        self._lock = threading.Lock()
        self._cache: "OrderedDict[tuple[str, int], dict]" = OrderedDict()
        self._cache_lock = threading.Lock()  # innermost; nothing is taken while held
        # Bumped by every delete.  A read only caches what it parsed when no
        # delete ran since it missed the cache (LD-01): a DELETE wins over a
        # read in flight.
        self._generation = 0

    def _remember(self, key: tuple[str, int], data: dict) -> None:
        """Cache *data* as the newest entry.  Call with _cache_lock held."""
        self._cache[key] = data
        self._cache.move_to_end(key)
        while len(self._cache) > self.CACHE_MAX:
            self._cache.popitem(last=False)

    # -- files ---------------------------------------------------------------

    def _asset_dir(self, name: str) -> Path:
        if not is_asset_name(name):
            raise AssetNotFoundError(name)
        return self.dir / name

    def _path(self, name: str, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise AssetNotFoundError(f"{name}/{version}")
        return self._asset_dir(name) / f"{version}.json"

    def _numbers(self, name: str, suffix: str) -> list[int]:
        folder = self._asset_dir(name)
        try:
            entries = list(folder.iterdir())
        except OSError:  # no folder: no versions
            return []
        out = []
        for entry in entries:
            if entry.suffix == suffix:
                version = _version_of(entry.stem)
                if version is not None:
                    out.append(version)
        return sorted(out)

    def versions(self, name: str) -> list[int]:
        """Live versions of an asset, oldest first."""
        return self._numbers(name, ".json")

    def _high_water(self, name: str) -> int:
        """The highest version ever given out, deleted ones included."""
        return max(self._numbers(name, ".json") + self._numbers(name, ".deleted"), default=0)

    def _read(self, name: str, version: int) -> dict:
        """The stored file (the cached copy itself: never hand it out)."""
        key = (name, version)
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            generation = self._generation
        path = self._path(name, version)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise AssetNotFoundError(f"{name}/{version}") from None
        except (OSError, ValueError) as exc:
            raise AssetCorruptError(name, version, f"unreadable: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AssetCorruptError(name, version, f"not valid JSON: {exc}") from exc
        problem = asset_file_problem(data, name, version)
        if problem is not None:
            raise AssetCorruptError(name, version, problem)
        with self._cache_lock:
            # A delete that ran while the file was read wins: its version is
            # not put back in the cache (this caller still gets what it read).
            if self._generation == generation:
                self._remember(key, data)
        return data

    # -- public API ----------------------------------------------------------

    def get(self, name: str, version: int) -> dict:
        """One AssetFile.  Raises AssetNotFoundError or AssetCorruptError."""
        return copy.deepcopy(self._read(name, version))

    def resolve(self, name: str, version: int) -> Optional[dict]:
        """The AssetFile for (name, version), or None when there is none or
        it cannot be read.  The kernel's resolver: it never raises."""
        try:
            return self.get(name, version)
        except (AssetNotFoundError, AssetCorruptError) as exc:
            logger.info("asset %s version %s cannot be resolved: %s", name, version, exc)
            return None
        except Exception:  # a resolver must never crash a compile
            logger.exception("asset %s version %s: lookup failed", name, version)
            return None

    def list(self, users: Optional[dict[str, list[dict]]] = None) -> list[dict]:
        """One AssetListItem per asset with at least one live version, by
        name.  ``users`` is the graph store's used_by map (asset_users())."""
        users = users or {}
        try:
            names = sorted(p.name for p in self.dir.iterdir()
                           if p.is_dir() and is_asset_name(p.name))
        except OSError:
            return []
        items = []
        for name in names:
            versions = self.versions(name)
            latest_file = None
            latest = None
            # The list shows the newest readable version: its number is
            # "latest" (what the Tab menu places), with its palette and
            # interface (LD-04).  "versions" still lists a damaged file, so
            # the Asset Manager can offer to delete it.
            for version in reversed(versions):
                try:
                    latest_file = self._read(name, version)
                    latest = version
                    break
                except (AssetNotFoundError, AssetCorruptError) as exc:
                    logger.warning("asset list: skipping %s version %s: %s", name, version, exc)
            if latest_file is None:
                continue
            items.append({
                "name": name,
                "versions": versions,
                "latest": latest,
                "palette": copy.deepcopy(latest_file.get("palette")),
                "interface": copy.deepcopy(latest_file.get("interface")
                                           or {"reads": [], "writes": []}),
                "used_by": [dict(u) for u in users.get(name, [])],
            })
        return items

    def create(
        self,
        name: Any,
        network: Any,
        promoted: Any = None,
        description: Any = "",
        interface: Any = None,
        palette: Any = None,
    ) -> dict:
        """Save a new version of an asset (latest + 1) and return its AssetFile.

        Raises InvalidAssetError (name_invalid, promoted_invalid,
        request_invalid), or what Graph.model_validate raises for a network
        that does not parse.  ``interface`` and ``palette`` are stored as
        given (the route checks their shape); interface defaults to empty.
        Raises AssetVersionTakenError when the new version's file appeared
        under it (another writer), and OSError when the file cannot be
        written.
        """
        if not is_asset_name(name):
            raise InvalidAssetError(
                "name_invalid",
                "Asset name must start with a lower case letter or underscore and use "
                "only lower case letters, digits and underscores (at most 64).")
        if description is None:
            description = ""
        if not isinstance(description, str) or len(description) > DESCRIPTION_MAX_LEN:
            raise InvalidAssetError(
                "request_invalid", f"Description must be text of at most {DESCRIPTION_MAX_LEN} characters.")
        stored_network = canonical_network(network)
        if not stored_network["nodes"]:
            raise InvalidAssetError("request_invalid", "An asset needs at least one node.")
        stored_promoted = check_promoted(promoted if promoted is not None else [], stored_network,
                                         asset_name=name, resolve=self.resolve)
        from nodebuilder.kernel.stream import STREAM_SCHEMA_VERSION

        with self._lock:
            version = self._high_water(name) + 1
            data = {
                "name": name,
                "version": version,
                "description": description,
                "stream_schema": STREAM_SCHEMA_VERSION,
                "interface": copy.deepcopy(interface) if interface is not None
                else {"reads": [], "writes": []},
                "promoted": stored_promoted,
                "palette": copy.deepcopy(palette),
                "network": stored_network,
                "created_at": _now(),
            }
            folder = self._asset_dir(name)
            folder.mkdir(parents=True, exist_ok=True)
            path = self._path(name, version)
            # Never replace a version: the number is new, so a file there
            # means something else wrote it outside this lock.  The write
            # itself refuses an existing file (no check-then-replace).
            _write_new_file(path, json.dumps(data, indent=2))
            with self._cache_lock:
                self._remember((name, version), copy.deepcopy(data))
        return data

    def delete(self, name: str, version: int) -> None:
        """Remove one version.  Raises AssetNotFoundError when it is not there.
        A damaged file is removed the same way (it has nothing to check).
        Raises OSError when the file cannot be removed."""
        path = self._path(name, version)
        with self._lock:
            if not path.exists():
                raise AssetNotFoundError(f"{name}/{version}")
            # The marker goes first, so the number stays taken even if the
            # process stops between the two steps.
            path.with_suffix(".deleted").touch()
            path.unlink()
            with self._cache_lock:
                self._generation += 1  # a read in flight must not cache it again
                self._cache.pop((name, version), None)


# ---------------------------------------------------------------------------
# One store per data dir
# ---------------------------------------------------------------------------

_stores: dict[Path, GraphStore] = {}
_stores_guard = threading.Lock()


def graph_head(graph_id: Optional[str]) -> Optional[tuple[int, str]]:
    """(rev, name) of a saved graph from the store's in-memory head index, or
    None when the id is empty or no readable graph has it (plan W5 5.D: the
    bot summary's graph_latest_rev and graph_name).  Never reads a file."""
    if not graph_id:
        return None
    return get_store().head(graph_id)


def get_store() -> GraphStore:
    """The store for the current data dir. Stores are shared, so their locks are too."""
    base = data_dir().resolve()
    with _stores_guard:
        store = _stores.get(base)
        if store is None:
            store = GraphStore(base)
            _stores[base] = store
        return store


_libraries: dict[Path, AssetLibrary] = {}


def get_library() -> AssetLibrary:
    """The asset library for the current data dir (shared, like the stores)."""
    base = data_dir().resolve()
    with _stores_guard:
        library = _libraries.get(base)
        if library is None:
            library = AssetLibrary(base)
            _libraries[base] = library
        return library


def resolve_asset(name: str, version: int) -> Optional[dict]:
    """The kernel resolver (``kernel.assets.Resolver``): the AssetFile for
    (name, version) in the current data dir, or None.  Never raises."""
    try:
        return get_library().resolve(name, version)
    except Exception:
        logger.exception("asset %s version %s: lookup failed", name, version)
        return None


def asset_list() -> list[dict]:
    """GET /api/graph_library: every asset, with used_by from the graph
    store's in-memory index (no graph file is read)."""
    return get_library().list(get_store().asset_users())


# Compile looks assets up through the kernel's default resolver; this module
# is the one place that knows where they are stored (decisions-pre W6).
from nodebuilder.kernel import assets as _kernel_assets  # noqa: E402  (no storage imports there)

_kernel_assets.set_default_resolver(resolve_asset)
