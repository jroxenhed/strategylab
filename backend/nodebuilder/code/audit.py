"""The code audit trail (design note 4.10).

One INFO line per code snippet on the logger ``strategylab.code_audit``,
on every save of a code-bearing graph and every bot start:

    code_audit event=graph_save graph_id=g_3f9a1c7e2b40 rev=5 node_id=n_spread
    name=spread_z level=wrangle sha256=<64 hex> bytes=412 email=<email or ->

(one line; wrapped here).  ``level`` is ``expr:<param>``, ``node_code`` or
``wrangle``.  The hash covers the user's original source as UTF-8, before
the sugar rewrite.  Nothing is ever refused because of this log; it
answers one question after the fact: which code ran, and when did it
first appear?
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Union

from nodebuilder.code.runtime import source_sha256

AUDIT_LOGGER_NAME = "strategylab.code_audit"
audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)

WRANGLE_TYPE = "wrangle"

_UNSAFE = re.compile(r"[\s\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class CodeSnippet:
    """One piece of code in a graph."""
    node_id: str
    name: str
    level: str          # expr:<param> | node_code | wrangle
    source: str

    @property
    def context(self) -> str:
        """The prepare() context for this snippet."""
        return "expr" if self.level.startswith("expr:") else self.level

    @property
    def param(self) -> Optional[str]:
        return self.level[5:] if self.level.startswith("expr:") else None

    @property
    def sha256(self) -> str:
        return source_sha256(self.source)

    @property
    def nbytes(self) -> int:
        return len(self.source.encode("utf-8", errors="surrogatepass"))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def expr_source(value: Any) -> Optional[str]:
    """The source of a param value that is an expression (``{"expr": ...}``),
    else None."""
    if isinstance(value, Mapping):
        src = value.get("expr")
    else:
        src = getattr(value, "expr", None) if not isinstance(value, (str, bytes)) else None
    return src if isinstance(src, str) else None


def _nodes(graph: Any) -> Iterable[Any]:
    """The nodes of a Graph model, a graph dict, or a list or dict of nodes."""
    if isinstance(graph, Mapping) and "nodes" not in graph and "id" not in graph:
        return graph.values()
    nodes = _get(graph, "nodes", graph)
    if isinstance(nodes, Mapping):
        return nodes.values()
    return nodes or ()


def iter_code_snippets(graph: Any) -> list[CodeSnippet]:
    """Every code snippet in *graph*, in node order: a node's code block
    (or Wrangle body) first, then its parameter expressions in param order.
    Empty or blank code is not a snippet."""
    out: list[CodeSnippet] = []
    for node in _nodes(graph):
        node_id = _get(node, "id") or ""
        name = _get(node, "name") or node_id
        code = _get(node, "code")
        if isinstance(code, str) and code.strip():
            level = WRANGLE_TYPE if _get(node, "type") == WRANGLE_TYPE else "node_code"
            out.append(CodeSnippet(node_id, name, level, code))
        params = _get(node, "params") or {}
        if isinstance(params, Mapping):
            for pname, value in params.items():
                src = expr_source(value)
                if src is not None and src.strip():
                    out.append(CodeSnippet(node_id, name, f"expr:{pname}", src))
    return out


def has_code(graph: Any) -> bool:
    """True when *graph* holds any code (for the kill switch)."""
    return bool(iter_code_snippets(graph))


def _field(value: Any) -> str:
    """One value for a key=value field: never empty, never two lines."""
    if value is None:
        return "-"
    text = _UNSAFE.sub("_", str(value))
    return text or "-"


def audit_lines(event: str, snippets: Union[Any, Iterable[CodeSnippet]], *,
                email: Optional[str] = None, **fields: Any) -> list[str]:
    """The audit lines for *snippets* (a list of CodeSnippet, or a graph).
    *fields* (graph_id, rev, bot_id...) go after ``event``, in order."""
    if not (isinstance(snippets, (list, tuple)) and all(isinstance(s, CodeSnippet) for s in snippets)):
        snippets = iter_code_snippets(snippets)
    head = " ".join([f"event={_field(event)}"] + [f"{k}={_field(v)}" for k, v in fields.items()])
    return [
        f"code_audit {head} node_id={_field(s.node_id)} name={_field(s.name)} "
        f"level={_field(s.level)} sha256={s.sha256} bytes={s.nbytes} email={_field(email)}"
        for s in snippets
    ]


def audit_log(event: str, snippets: Union[Any, Iterable[CodeSnippet]], *,
              email: Optional[str] = None, logger: Optional[logging.Logger] = None,
              **fields: Any) -> list[str]:
    """Log one ``code_audit`` line per snippet at INFO and return the lines.

    audit_log("graph_save", graph, graph_id=g.id, rev=5, email=forwarded_email)
    audit_log("bot_start", graph, bot_id=cfg.bot_id)
    """
    lines = audit_lines(event, snippets, email=email, **fields)
    log = logger or audit_logger
    for line in lines:
        log.info(line)
    return lines
