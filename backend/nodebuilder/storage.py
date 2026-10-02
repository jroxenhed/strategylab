"""Graph storage: one JSON file per graph on the server (F435 W1 item 1.B, decision D2).

Each graph lives in ``<data dir>/graphs/<graph_id>.json`` as an envelope:

    {id, rev, name, description, created_at, updated_at, graph}

Library assets (W6) will live in ``<data dir>/graph_library/``. This module
only creates that folder's path so startup can clean temp files there.

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

import hashlib
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError

from fileutil import atomic_write_text
from nodebuilder.models import Graph, GraphValidationError

logger = logging.getLogger(__name__)

GRAPHS_DIRNAME = "graphs"
LIBRARY_DIRNAME = "graph_library"

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
    because that function does not look inside sub-folders."""
    base = data_dir() if base is None else Path(base)
    return [base / GRAPHS_DIRNAME, base / LIBRARY_DIRNAME]


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
        heads = {env["id"]: (env["rev"], env["name"]) for env in self._all()}
        with self._heads_lock:
            self._heads = heads

    def _set_head(self, env: dict) -> None:
        """Record a graph's new rev and name.  Called after its file is
        written, under that graph's file lock, so the index never runs ahead
        of the disk and two saves of one graph update it in order."""
        with self._heads_lock:
            self._heads[env["id"]] = (env["rev"], env["name"])

    def _drop_head(self, graph_id: str) -> None:
        with self._heads_lock:
            self._heads.pop(graph_id, None)

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
