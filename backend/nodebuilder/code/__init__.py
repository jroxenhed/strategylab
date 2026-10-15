"""User code in real Python: parameter expressions, node code blocks and
Wrangle nodes (F435 Wave 7, design note
docs/plans/2026-09-29-node-builder-code-nodes-design.md).

Always import this package as ``nodebuilder.code``.  Never put
``backend/nodebuilder`` itself on sys.path: a top-level ``code`` package
would hide Python's standard ``code`` module.

There is no sandbox, by John's decision (2026-09-30): code runs as the
backend user.  What this package adds is cheap and blocks nothing: the
kill switch (``code_enabled``), the audit log (``audit_log``), and guards
so broken or slow code fails one cook instead of crashing the server.

Public API:

- ``prepare(source, context, node)`` checks and compiles a snippet; it
  never runs it.
- ``run(prepared, stream, ...)`` runs it (the only place user code runs).
- ``call_guarded`` / ``await_guarded`` wait for a cook with a time limit
  (from the moment it starts; bot cooks run on ``CODE_EXECUTOR``);
  ``leaked_cooks()`` counts timed-out cooks still running.
- ``code_enabled()``, ``capabilities()``, ``audit_log()``,
  ``iter_code_snippets()``, ``has_code()``, ``pause_reason()``.
"""
from __future__ import annotations

from nodebuilder.code.audit import (
    AUDIT_LOGGER_NAME,
    CodeSnippet,
    audit_lines,
    audit_log,
    expr_source,
    has_code,
    iter_code_snippets,
)
from nodebuilder.code.errors import (
    CODES,
    AttrMissingError,
    CodeDiagnostic,
    CodeError,
    CodeTimeout,
)
from nodebuilder.code.promote import (
    CH_FUNCS,
    CH_TYPES,
    LOOKBACK_DEFAULT,
    LOOKBACK_MAX,
    LOOKBACK_MIN,
    LOOKBACK_PARAM,
    ChRef,
    SpareParam,
    lookback_spec,
)
from nodebuilder.code.runtime import (
    BOT_COOK_TIMEOUT_S,
    CODE_EXECUTOR,
    CODE_POOL_WORKERS,
    CONTEXTS,
    DEFAULT_LOOKBACK_BARS,
    ENV_SWITCH,
    MAX_SOURCE_BYTES,
    ROUTE_COOK_TIMEOUT_S,
    CodeResult,
    CookGuard,
    PreparedCode,
    StreamProxy,
    Written,
    await_guarded,
    builtin_params_of,
    call_guarded,
    capabilities,
    check_expr_result,
    code_enabled,
    code_filename,
    coerce_channel,
    current_guard,
    disabled_diagnostic,
    leaked_cooks,
    modules,
    pause_reason,
    prepare,
    run,
    source_sha256,
)

__all__ = [
    "AUDIT_LOGGER_NAME", "CodeSnippet", "audit_lines", "audit_log", "expr_source", "has_code",
    "iter_code_snippets",
    "CODES", "AttrMissingError", "CodeDiagnostic", "CodeError", "CodeTimeout",
    "CH_FUNCS", "CH_TYPES", "LOOKBACK_DEFAULT", "LOOKBACK_MAX", "LOOKBACK_MIN", "LOOKBACK_PARAM",
    "ChRef", "SpareParam", "lookback_spec",
    "BOT_COOK_TIMEOUT_S", "CODE_EXECUTOR", "CODE_POOL_WORKERS", "CONTEXTS", "DEFAULT_LOOKBACK_BARS", "ENV_SWITCH", "MAX_SOURCE_BYTES",
    "ROUTE_COOK_TIMEOUT_S", "CodeResult", "CookGuard", "PreparedCode", "StreamProxy", "Written",
    "await_guarded", "builtin_params_of", "call_guarded", "capabilities", "check_expr_result",
    "code_enabled", "code_filename", "coerce_channel", "current_guard", "disabled_diagnostic",
    "leaked_cooks", "modules", "pause_reason", "prepare", "run", "source_sha256",
]
