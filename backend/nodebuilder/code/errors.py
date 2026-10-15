"""Code diagnostics and the errors the code runtime raises.

Every code problem becomes one CodeDiagnostic (design note 4.8):

- ``line`` is 1-based and ``col`` is 0-based.  Both count characters in the
  user's original text (before the ``@name`` rewrite), as Python's ``ast``
  reports them.  ``end_line`` and ``end_col`` follow the same rule and may
  be None.
- Every code in this package is an error.

``CodeDiagnostic.to_diagnostic()`` turns one into the graph-wide
``nodebuilder.diagnostics.Diagnostic`` (plan section 4.2).

This module imports nothing from the rest of nodebuilder at load time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Optional

# The codes the runtime itself produces.  Wave 7 registers them (and their
# severity, always "error") in nodebuilder/diagnostics.py.
CODE_SYNTAX = "code_syntax"
CODE_LIMIT = "code_limit"
CODE_RUNTIME = "code_runtime"
CODE_TIMEOUT = "code_timeout"
CODE_TYPE = "code_type"
CODE_DISABLED = "code_disabled"
CH_DYNAMIC = "ch_dynamic"
ATTR_DYNAMIC = "attr_dynamic"
ATTR_MISSING = "attr_missing"
ATTR_CLASH = "attr_clash"
REF_BROKEN = "ref_broken"
PARAM_INVALID = "param_invalid"

CODES: tuple[str, ...] = (
    CODE_SYNTAX, CODE_LIMIT, CODE_RUNTIME, CODE_TIMEOUT, CODE_TYPE, CODE_DISABLED,
    CH_DYNAMIC, ATTR_DYNAMIC, ATTR_MISSING, ATTR_CLASH, REF_BROKEN, PARAM_INVALID,
)


@dataclass(frozen=True)
class CodeDiagnostic:
    """One code problem, with its place in the user's text."""

    code: str
    message: str
    line: Optional[int] = None
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    node_id: Optional[str] = None
    param: Optional[str] = None
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_node(self, node_id: Optional[str], param: Optional[str] = None) -> "CodeDiagnostic":
        """The same diagnostic tied to *node_id* (and *param*, when given)."""
        return replace(self, node_id=node_id, param=param if param is not None else self.param)

    def to_diagnostic(self):
        """This diagnostic as a ``nodebuilder.diagnostics.Diagnostic``."""
        from nodebuilder.diagnostics import Diagnostic

        return Diagnostic(**self.to_dict())


class CodeError(Exception):
    """A code failure with a diagnostic code.

    The runtime raises it on purpose (a missing attribute, a wrong result,
    an unknown ``ch()`` name) and ``run()`` raises it for every failure, with
    the place in the user's text filled in.  ``code``, ``node_id`` and
    ``param`` are plain attributes, so ``diagnostics.from_error`` reads them.
    """

    def __init__(self, code: str, message: str, *, line: Optional[int] = None,
                 col: Optional[int] = None, end_line: Optional[int] = None,
                 end_col: Optional[int] = None, node_id: Optional[str] = None,
                 param: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.end_line = end_line
        self.end_col = end_col
        self.node_id = node_id
        self.param = param

    def __str__(self) -> str:
        return self.message

    @property
    def diagnostic(self) -> CodeDiagnostic:
        return CodeDiagnostic(
            code=self.code, message=self.message, line=self.line, col=self.col,
            end_line=self.end_line, end_col=self.end_col, node_id=self.node_id,
            param=self.param,
        )

    def to_diagnostic(self):
        return self.diagnostic.to_diagnostic()

    @classmethod
    def from_diagnostic(cls, diag: CodeDiagnostic) -> "CodeError":
        return cls(diag.code, diag.message, line=diag.line, col=diag.col,
                   end_line=diag.end_line, end_col=diag.end_col,
                   node_id=diag.node_id, param=diag.param)


class AttrMissingError(CodeError, KeyError):
    """A read of an attribute the stream does not hold (``attr_missing``).

    It is also a KeyError, so ``try: ... except KeyError:`` in user code
    works the way a Python user expects.
    """

    def __init__(self, message: str, **kwargs: Any) -> None:
        CodeError.__init__(self, ATTR_MISSING, message, **kwargs)


class CodeTimeout(CodeError):
    """The wall-clock guard fired (``code_timeout``).  ``pool_full`` is True
    when the cook never started because every code thread is held by a
    leaked cook (its message says so)."""

    pool_full = False

    def __init__(self, message: str, *, timeout_s: float, node_id: Optional[str] = None,
                 node_name: Optional[str] = None) -> None:
        super().__init__(CODE_TIMEOUT, message, node_id=node_id)
        self.timeout_s = timeout_s
        self.node_name = node_name
