"""The Houdini ``@name`` sugar (design note 4.2).

``rewrite(source)`` turns every attribute ``@name`` into ``stream["name"]``
and leaves everything else alone: the matrix-multiply operator ``a @ b``,
decorators, and any ``@`` inside a string or a comment.  It works on the
``tokenize`` token stream, so strings and comments are single tokens and
are never touched.

The rewrite never adds or removes a line, so line numbers stay exact.  A
``ColumnMap`` maps a column in the rewritten text back to the user's text
and the other way.  A column inside a rewritten span maps to its ``@``.

Rules for one ``@`` operator token:

1. Find the previous significant token (skip NL, COMMENT, INDENT, DEDENT).
2. The ``@`` is unary when that token is nothing or NEWLINE (line start),
   an operator other than ``)`` ``]`` ``}`` ``...`` ``.``, or a keyword
   other than True, False and None.  Otherwise it is matrix multiply.
3. A line-start ``@`` is a decorator, and is left alone, when its logical
   line has no assignment operator and no annotation colon outside
   brackets (a ``lambda``'s colon does not count), and the next logical
   line starts with ``def``, ``class``, ``async`` or another decorator.
3b. ``match @name:`` at the start of a statement is the match statement
   on an attribute (``match`` is a soft keyword, so rule 2 alone would
   read the ``@`` as matrix multiply).  ``case @name`` is ``code_syntax``
   with a hint: a case pattern cannot read an attribute.
4. Every other unary ``@`` must be followed, with no space, by a name that
   matches ``^[a-z_][a-z0-9_]{0,63}$``.  Anything else is ``code_syntax``
   at the ``@``.
"""
from __future__ import annotations

import io
import keyword
import re
import tokenize
from dataclasses import dataclass
from typing import Optional

from nodebuilder.code.errors import CODE_SYNTAX, CodeDiagnostic

ATTR_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
"""A valid attribute name, without the sigil."""

_VALUE_KEYWORDS = frozenset({"True", "False", "None"})
_CLOSERS = frozenset({")", "]", "}", "...", "."})
_SKIP = frozenset({tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT})
_ASSIGN_OPS = frozenset({
    "=", ":=", "+=", "-=", "*=", "/=", "//=", "%=", "**=", "@=",
    "&=", "|=", "^=", ">>=", "<<=",
})
_DECORATED = frozenset({"def", "class", "async"})


def normalize_newlines(source: str) -> str:
    """\\r\\n and a lone \\r become \\n, as Python's own parser reads them.
    No column on any line moves."""
    return source.replace("\r\n", "\n").replace("\r", "\n")


def split_lines(text: str) -> list[str]:
    """*text* split on \\n only (str.splitlines also splits on form feeds and
    other characters Python's tokenizer does not treat as line ends)."""
    return text.split("\n")


def attr_access(name: str) -> str:
    """What ``@name`` becomes."""
    return f'stream["{name}"]'


# ---------------------------------------------------------------------------
# Column map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """One rewritten ``@name`` on a line."""
    user_col: int   # column of the @ in the user's line
    user_len: int   # len("@name")
    new_len: int    # len('stream["name"]')


@dataclass(frozen=True)
class ColumnMap:
    """Columns between the user's text and the rewritten text, per line.

    Columns count characters and are 0-based; lines are 1-based.  Lines
    with no rewrite map one to one.
    """
    spans: dict[int, tuple[Span, ...]]

    def to_user(self, line: int, col: int) -> int:
        """A rewritten column as a column in the user's text."""
        shift = 0
        for sp in self.spans.get(line, ()):
            start = sp.user_col + shift
            if col < start:
                break
            if col < start + sp.new_len:
                return sp.user_col
            shift += sp.new_len - sp.user_len
        return col - shift

    def to_rewritten(self, line: int, col: int) -> int:
        """A column in the user's text as a rewritten column."""
        shift = 0
        for sp in self.spans.get(line, ()):
            if col < sp.user_col:
                break
            if col < sp.user_col + sp.user_len:
                return sp.user_col + shift
            shift += sp.new_len - sp.user_len
        return col + shift


@dataclass(frozen=True)
class SugarResult:
    """The rewritten text, its column map and any bad sigils."""
    text: str               # the rewritten text (newlines normalized)
    lines: tuple[str, ...]  # its lines, split on \n
    colmap: ColumnMap
    errors: tuple[CodeDiagnostic, ...]

    def user_position(self, line: Optional[int], col: Optional[int],
                      unit: str = "char") -> Optional[int]:
        """Column *col* of rewritten line *line* as a character column in the
        user's text.  *unit* is "char" for a character offset or "byte" for
        a UTF-8 byte offset (what ``ast`` nodes and traceback frames give)."""
        if line is None or col is None:
            return None
        if col < 0:
            return None
        if unit == "byte":
            col = byte_to_char(self.line_text(line), col)
        return self.colmap.to_user(line, col)

    def line_text(self, line: int) -> str:
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1]
        return ""


def byte_to_char(text: str, byte_col: int) -> int:
    """A UTF-8 byte offset into *text* as a character offset."""
    raw = text.encode("utf-8")
    if byte_col >= len(raw):
        return len(text) + (byte_col - len(raw))
    return len(raw[:byte_col].decode("utf-8", errors="ignore"))


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------


def _tokens(text: str) -> tuple[list[tokenize.TokenInfo], bool]:
    """Every token of *text* up to the first tokenizer error.

    Returns (tokens, complete).  When the tokenizer fails, the tokens before
    the failure are still rewritten; Python's parser then reports the error
    itself, with a better message than the tokenizer's.
    """
    out: list[tokenize.TokenInfo] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            out.append(tok)
    except (tokenize.TokenError, SyntaxError, ValueError):
        return out, False
    return out, True


def _is_unary(prev: Optional[tokenize.TokenInfo]) -> tuple[bool, bool]:
    """(unary, line_start) for an @ whose previous significant token is *prev*."""
    if prev is None or prev.type == tokenize.NEWLINE:
        return True, True
    if prev.type == tokenize.OP:
        return prev.string not in _CLOSERS, False
    if prev.type == tokenize.NAME:
        # Soft keywords (match, case, type, _) are not in kwlist: plain names.
        return keyword.iskeyword(prev.string) and prev.string not in _VALUE_KEYWORDS, False
    return False, False


class _Decorators:
    """Decides whether a line-start @ is a decorator (rule 3)."""

    def __init__(self, tokens: list[tokenize.TokenInfo]) -> None:
        self.tokens = tokens
        self._memo: dict[int, bool] = {}

    def __call__(self, i: int) -> bool:
        if i not in self._memo:
            self._memo[i] = False   # a guard: a decorator chain is finite
            self._memo[i] = self._decide(i)
        return self._memo[i]

    def _decide(self, i: int) -> bool:
        toks = self.tokens
        depth = 0
        lambdas = 0   # open lambdas at depth 0: their colon is not an annotation
        j = i + 1
        while j < len(toks) and toks[j].type not in (tokenize.NEWLINE, tokenize.ENDMARKER):
            t = toks[j]
            if t.type == tokenize.NAME and t.string == "lambda" and depth == 0:
                lambdas += 1
            elif t.type == tokenize.OP:
                if t.string in "([{":
                    depth += 1
                elif t.string in ")]}":
                    depth = max(0, depth - 1)
                elif depth == 0 and t.string in _ASSIGN_OPS:
                    return False
                elif depth == 0 and t.string == ":":
                    if lambdas == 0:
                        return False   # @x: bool (a bare annotation), not a decorator
                    lambdas -= 1
            j += 1
        if j >= len(toks) or toks[j].type != tokenize.NEWLINE:
            return False
        k = j + 1
        while k < len(toks) and toks[k].type in _SKIP:
            k += 1
        if k >= len(toks):
            return False
        nxt = toks[k]
        if nxt.type == tokenize.NAME and nxt.string in _DECORATED:
            return True
        if nxt.type == tokenize.OP and nxt.string == "@":
            return self(k)
        return False


def rewrite(source: str) -> SugarResult:
    """Rewrite every attribute ``@name`` in *source* to ``stream["name"]``.

    Never raises for bad user code: a bad sigil is a ``code_syntax`` entry
    in ``errors`` (at the ``@``, in the user's coordinates), and any other
    syntax error is left for Python's parser to report.
    """
    text = normalize_newlines(source)
    lines = split_lines(text)
    tokens, _complete = _tokens(text)
    is_decorator = _Decorators(tokens)

    edits: dict[int, list[tuple[int, int, str]]] = {}   # line -> (start, end, new text)
    errors: list[CodeDiagnostic] = []
    prev: Optional[tokenize.TokenInfo] = None
    before_prev: Optional[tokenize.TokenInfo] = None
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.OP and tok.string == "@":
            unary, line_start = _is_unary(prev)
            soft = _soft_keyword_header(prev, before_prev, tokens, i)
            if soft == "case":
                errors.append(CodeDiagnostic(
                    CODE_SYNTAX,
                    "A case pattern cannot read an attribute.  Match on the attribute "
                    "instead (match @regime: ... case 'bull': ...), or use if/elif.",
                    line=tok.start[0], col=tok.start[1], end_line=tok.end[0],
                    end_col=tok.end[1]))
            elif soft == "match" or (unary and not (line_start and is_decorator(i))):
                err = _attr_at(tokens, i, edits)
                if err is not None:
                    errors.append(err)
        if tok.type not in _SKIP:
            before_prev, prev = prev, tok

    spans: dict[int, tuple[Span, ...]] = {}
    out_lines = list(lines)
    for row, row_edits in edits.items():
        row_edits.sort()
        line = lines[row - 1]
        parts: list[str] = []
        cursor = 0
        row_spans: list[Span] = []
        for start, end, new in row_edits:
            parts.append(line[cursor:start])
            parts.append(new)
            cursor = end
            row_spans.append(Span(start, end - start, len(new)))
        parts.append(line[cursor:])
        out_lines[row - 1] = "".join(parts)
        spans[row] = tuple(row_spans)
    new_text = "\n".join(out_lines)
    return SugarResult(new_text, tuple(out_lines), ColumnMap(spans), tuple(errors))


def _soft_keyword_header(prev: Optional[tokenize.TokenInfo],
                         before_prev: Optional[tokenize.TokenInfo],
                         tokens: list[tokenize.TokenInfo], i: int) -> Optional[str]:
    """"match" or "case" when the @ at *i* follows that soft keyword at the
    start of a statement whose logical line ends with a colon outside
    brackets (a match or case header), else None."""
    if prev is None or prev.type != tokenize.NAME or prev.string not in ("match", "case"):
        return None
    if before_prev is not None and before_prev.type != tokenize.NEWLINE:
        return None
    depth = 0
    last: Optional[tokenize.TokenInfo] = None
    j = i
    while j < len(tokens) and tokens[j].type not in (tokenize.NEWLINE, tokenize.ENDMARKER):
        t = tokens[j]
        if t.type == tokenize.OP:
            if t.string in "([{":
                depth += 1
            elif t.string in ")]}":
                depth = max(0, depth - 1)
        if t.type not in _SKIP:
            last = t if depth == 0 else None
        j += 1
    if last is not None and last.type == tokenize.OP and last.string == ":":
        return prev.string
    return None


def _attr_at(tokens: list[tokenize.TokenInfo], i: int,
             edits: dict[int, list[tuple[int, int, str]]]) -> Optional[CodeDiagnostic]:
    """Record the rewrite of the unary @ at *i*, or return its error."""
    at = tokens[i]
    row, col = at.start
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    if nxt is None or nxt.type != tokenize.NAME:
        return CodeDiagnostic(
            CODE_SYNTAX, "@ must be followed by an attribute name, like @close.",
            line=row, col=col, end_line=row, end_col=col + 1,
        )
    if nxt.start != at.end:
        return CodeDiagnostic(
            CODE_SYNTAX, f"Write @{nxt.string} with no space after the @.",
            line=row, col=col, end_line=nxt.end[0], end_col=nxt.end[1],
        )
    if not ATTR_RE.match(nxt.string):
        return CodeDiagnostic(
            CODE_SYNTAX,
            f"@{nxt.string} is not an attribute name: use lowercase letters, digits and _ "
            "(at most 64), starting with a letter or _.",
            line=row, col=col, end_line=row, end_col=nxt.end[1],
        )
    edits.setdefault(row, []).append((col, nxt.end[1], attr_access(nxt.string)))
    return None
