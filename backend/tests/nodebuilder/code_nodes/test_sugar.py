"""The @name sugar (F435 W7 item 7.A, design note 4.2).

Every row of the design-note table lives in vectors/sugar.json.  The
column map is checked in both directions.
"""
from __future__ import annotations

import ast
import json
import os

import pytest

from nodebuilder.code.errors import CODE_SYNTAX
from nodebuilder.code.sugar import rewrite

_VECTORS = os.path.join(os.path.dirname(__file__), os.pardir, "vectors", "sugar.json")

with open(_VECTORS, encoding="utf-8") as _fh:
    _DOC = json.load(_fh)

VECTORS = _DOC["vectors"]


def test_the_file_holds_all_28_rows():
    assert [v["id"] for v in VECTORS] == list(range(1, 29))


@pytest.mark.parametrize("vec", [v for v in VECTORS if v["error"] is None],
                         ids=lambda v: f"v{v['id']}")
def test_rewrite_vector(vec):
    result = rewrite(vec["source"])
    assert result.errors == ()
    assert result.text == vec["rewritten"]
    # The rewrite never adds or removes a line.
    assert result.text.count("\n") == vec["source"].count("\n")
    # And the result is valid Python.
    ast.parse(result.text)


@pytest.mark.parametrize("vec", [v for v in VECTORS if v["error"] is not None],
                         ids=lambda v: f"v{v['id']}")
def test_error_vector(vec):
    result = rewrite(vec["source"])
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.code == vec["error"]["code"] == CODE_SYNTAX
    assert (err.line, err.col) == (vec["error"]["line"], vec["error"]["col"])


@pytest.mark.parametrize("case", _DOC["column_map"], ids=lambda c: c["source"])
def test_column_map_both_ways(case):
    cmap = rewrite(case["source"]).colmap
    for line, rewritten, user in case["to_user"]:
        assert cmap.to_user(line, rewritten) == user
    for line, user, rewritten in case["to_rewritten"]:
        assert cmap.to_rewritten(line, user) == rewritten


def test_column_inside_a_span_maps_to_its_sigil():
    # @x = @close * 2  ->  stream["x"] = stream["close"] * 2
    cmap = rewrite("@x = @close * 2").colmap
    for col in range(14, 29):          # every column of stream["close"]
        assert cmap.to_user(1, col) == 5
    for col in range(5, 11):           # every column of @close
        assert cmap.to_rewritten(1, col) == 14
    # Columns outside spans round-trip.
    for user in (2, 3, 4, 11, 12, 13, 14):
        assert cmap.to_user(1, cmap.to_rewritten(1, user)) == user


def test_lines_without_a_rewrite_map_one_to_one():
    cmap = rewrite("a = 1\n@b = 2").colmap
    assert cmap.to_user(1, 3) == 3 and cmap.to_rewritten(1, 3) == 3
    assert cmap.to_user(2, 12) == 3      # the = after stream["b"]
    assert cmap.to_user(5, 7) == 7       # a line that does not exist


def test_matmul_assignment_is_never_touched():
    assert rewrite("a @= b").text == "a @= b"
    assert rewrite("a @= @b").text == 'a @= stream["b"]'


def test_stacked_decorators_and_a_fake_one():
    src = "@a\n@b\ndef f(): pass"
    assert rewrite(src).text == src
    # @x followed by a write is not a decorator chain: both are attributes.
    assert rewrite("@x\n@y = 1").text == 'stream["x"]\nstream["y"] = 1'
    # A class body decorator.
    src = "class A:\n    @property\n    def p(self): return 1"
    assert rewrite(src).text == src


def test_soft_keywords_count_as_names():
    # "match" is a soft keyword: a plain name, so @ after it is matmul.
    assert rewrite("y = match @ w").text == "y = match @ w"


def test_attribute_after_a_keyword_and_in_a_comprehension():
    assert rewrite("v = [x for x in @close if x > 0]").text == \
        'v = [x for x in stream["close"] if x > 0]'
    assert rewrite("v = @a if @b else @c").text == \
        'v = stream["a"] if stream["b"] else stream["c"]'


def test_every_bad_sigil_is_reported():
    result = rewrite("a = @ x\nb = @Y\nc = @(1)")
    assert [(e.line, e.col) for e in result.errors] == [(1, 4), (2, 4), (3, 4)]
    assert all(e.code == CODE_SYNTAX for e in result.errors)


def test_crlf_source_keeps_lines_and_columns():
    result = rewrite("a = 1\r\n@b = @c\r\n")
    assert result.errors == ()
    assert result.text == 'a = 1\nstream["b"] = stream["c"]\n'
    assert result.colmap.to_user(2, 14) == 5


def test_tokenizer_failure_still_rewrites_the_part_before_it():
    # An unclosed bracket: the tokenizer stops at the end; the @ before it
    # is still rewritten, and Python's parser reports the real error.
    result = rewrite("x = @close + (1,\n")
    assert result.errors == ()
    assert result.text.startswith('x = stream["close"] + (1,')
    with pytest.raises(SyntaxError):
        ast.parse(result.text)


def test_strings_and_comments_hold_the_at_sign_as_text():
    src = "s = '@a' + \"\"\"@b\n@c\"\"\"  # @d"
    assert rewrite(src).text == src


# ---------------------------------------------------------------------------
# W7 fix CR-11: clearer edges of the sugar
# ---------------------------------------------------------------------------


def test_match_on_an_attribute_is_the_match_statement():
    """match is a soft keyword: at a statement start with a colon, the @ is
    the attribute sugar (it was read as matrix multiply, a bare syntax
    error)."""
    r = rewrite('match @regime:\n    case "bull":\n        x = 1\n')
    assert not r.errors
    ast.parse(r.text)
    assert r.text.startswith('match stream["regime"]:')


def test_match_as_a_plain_name_keeps_matrix_multiply():
    r = rewrite("match = 3\nz = match @ w\nmatch @ w\n")
    assert not r.errors and "stream" not in r.text


def test_case_on_an_attribute_has_a_hint():
    r = rewrite("match x:\n    case @y:\n        pass\n")
    assert [e.code for e in r.errors] == [CODE_SYNTAX]
    assert "case pattern cannot read an attribute" in r.errors[0].message
    assert (r.errors[0].line, r.errors[0].col) == (2, 9)


def test_a_bare_annotation_before_a_def_is_a_write_not_a_decorator():
    r = rewrite("@sig: bool\ndef helper():\n    return 1\n@sig = @close > 0\n")
    assert not r.errors
    assert r.text.startswith('stream["sig"]: bool\n')


def test_a_lambda_decorator_is_still_a_decorator():
    r = rewrite("@lambda f: f\ndef g():\n    pass\n")
    assert not r.errors and r.text.startswith("@lambda f: f")
