"""Tests for the three TODO/NOW shape checkers in bin/.

Each checker is a standalone script with a dashed filename, so they are loaded
by path rather than imported as packages. stdlib + pytest only.

Run:
    backend/venv/bin/python -m pytest bin/tests/test_todo_checkers.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parent.parent
REPO = BIN.parent


def _load(name: str):
    path = BIN / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


plain = _load("check-todo-plain")
codes = _load("check-todo-codes")
now = _load("check-now-focused")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

HEADER = "# StrategyLab TODO\n\nOpen items only. Completed work lives in TODO-archive.md.\n\n---\n\n"


def todo(*items: str, section: str = "## Features") -> str:
    return HEADER + section + "\n\n" + "\n".join(items) + "\n"


def item(id_: str, body: str, anchor: bool = True, box: str = " ") -> str:
    a = f'<a id="{id_.lower()}"></a> ' if anchor else ""
    return f"- [{box}] {a}**{id_}** {body}"


def only(problems, needle):
    """Assert exactly one problem and that it mentions `needle`; return it."""
    assert len(problems) == 1, problems
    assert needle in problems[0], problems[0]
    return problems[0]


CLEAN = todo(
    item("F431", "Gateway alert cooldown does not survive a backend restart. Persist the last alert timestamp in the data dir. [easy] [hardening]"),
    item("F432", "Backend does not re-register IBKR when the Gateway comes up later. Let the alert loop retry. [easy] [hardening]"),
)


# ---------------------------------------------------------------------------
# check-todo-plain
# ---------------------------------------------------------------------------

def test_clean_file_passes():
    assert plain.check(CLEAN) == []


def test_ceiling_file_says_40():
    assert plain.ceiling() == 40
    assert plain.CEILING_FILE == REPO / "bin" / "check-todo-plain.ceiling"


def test_ticked_item_fails():
    text = todo(item("F431", "Something that already shipped. [easy] [polish]", box="x"))
    p = only(plain.check(text), "ticked item")
    assert "JOURNAL" in p


def test_nested_item_fails():
    text = todo(
        item("A8", "Chart performance work for very large datasets. [arch]"),
        "  - [ ] Viewport-only rendering for the indicator series. [hard]",
    )
    p = only(plain.check(text), "nested item")
    assert "One piece of work per ID" in p


def test_item_over_limit_fails_measured_without_anchor():
    # 15 chars of prefix ("- [ ] " + "**F900** ") after the anchor is stripped.
    prefix = len("- [ ] ") + len("**F900** ")
    body = "a" * (plain.MAX_ITEM_CHARS + 1 - prefix - 1) + "."
    text = todo(item("F900", body))
    p = only(plain.check(text), f"is {plain.MAX_ITEM_CHARS + 1} chars")
    # The raw line is longer than that: the anchor markup was excluded.
    raw = [l for l in text.split("\n") if "F900" in l][0]
    assert len(raw) > plain.MAX_ITEM_CHARS + 1


def test_item_at_limit_passes_with_anchor():
    prefix = len("- [ ] ") + len("**F900** ")
    body = "a" * (plain.MAX_ITEM_CHARS - prefix - 1) + "."
    text = todo(item("F900", body))
    assert plain.check(text) == []


def test_em_dash_fails():
    text = todo(item("F431", "Gateway alert cooldown — persist it across restarts. [easy] [hardening]"))
    p = only(plain.check(text), "em dash")
    assert "full stop or a comma" in p


def test_done_word_fails():
    text = todo(item("F431", "Gateway alert cooldown persistence. SHIPPED on the office VM. [easy] [hardening]"))
    only(plain.check(text), "narrates a closure")


def test_too_many_sentences_fails():
    body = "One. Two. Three. Four. Five. Six. [arch]"
    text = todo(item("F900", body))
    only(plain.check(text), "sentences (max 5)")


def test_ceiling_exceeded_fails():
    text = todo(
        item("F901", "First open item. [arch]"),
        item("F902", "Second open item. [arch]"),
        item("F903", "Third open item. [arch]"),
    )
    p = only(plain.check(text, max_items=2), "3 open items, ceiling is 2")
    assert "bin/check-todo-plain.ceiling" in p


def test_deferred_gated_items_count_toward_ceiling():
    text = (
        todo(item("F901", "First open item. [arch]"))
        + "\n## Deferred (gated)\n\n"
        + item("F902", "Parked item. [arch] [gated: John approves]")
        + "\n"
    )
    only(plain.check(text, max_items=1), "2 open items, ceiling is 1")


def test_intro_pointer_under_h1_is_not_a_section_prose_violation():
    long_intro = "# StrategyLab TODO\n\n" + ("word " * 120).strip() + "\n\n---\n\n## Features\n\n" \
        + item("F901", "An item. [arch]") + "\n"
    assert len(long_intro.split("---")[0]) > plain.MAX_SECTION_PROSE
    assert plain.check(long_intro) == []


def test_section_prose_over_limit_fails():
    text = HEADER + "## Features\n\n" + ("word " * 120).strip() + "\n\n" \
        + item("F901", "An item. [arch]") + "\n"
    only(plain.check(text), "chars of prose outside its items")


# ---------------------------------------------------------------------------
# check-todo-codes
# ---------------------------------------------------------------------------

def test_codes_clean_file_has_no_duplicates():
    defs = codes.find_definitions(CLEAN)
    assert [c for _, c in defs] == ["F431", "F432"]
    assert codes.find_duplicates(defs) == {}


def test_duplicate_code_both_anchored():
    text = todo(
        item("F431", "One thing. [arch]"),
        item("F431", "A different thing that reused the ID. [arch]"),
    )
    dupes = codes.find_duplicates(codes.find_definitions(text))
    assert list(dupes) == ["F431"]
    assert len(dupes["F431"]) == 2


def test_duplicate_code_one_anchored_one_not():
    text = todo(
        item("F431", "One thing. [arch]", anchor=True),
        item("F431", "A different thing filed by another session. [arch]", anchor=False),
    )
    dupes = codes.find_duplicates(codes.find_definitions(text))
    assert list(dupes) == ["F431"]


@pytest.mark.parametrize("code", ["A8", "B9", "D24b", "F249c", "F249-alt"])
def test_code_shapes_are_recognised(code):
    text = todo(item(code, "An item. [arch]"))
    assert [c for _, c in codes.find_definitions(text)] == [code]


def test_mid_sentence_reference_is_not_a_definition():
    text = todo(
        item("B9", "Cost model v2, deferred from **B6**. [features]"),
        item("F431", "Ties **B9** to the alert loop. [arch]"),
    )
    assert codes.find_duplicates(codes.find_definitions(text)) == {}


def test_index_table_row_is_not_a_definition():
    text = CLEAN + "\n| [Features](#features) | 2 | **F431**, **F432** |\n"
    assert codes.find_duplicates(codes.find_definitions(text)) == {}


# ---------------------------------------------------------------------------
# check-now-focused
# ---------------------------------------------------------------------------

NOW_CLEAN = "# NOW\n\n## This week\n\n- [ ] Ship the TODO rewrite.\n- [ ] Re-register IBKR automatically.\n"


def test_now_clean_passes():
    assert now.check(NOW_CLEAN) == []


def test_now_section_of_five_items_fails():
    text = "# NOW\n\n## This week\n\n" + "".join(
        f"- [ ] Item number {n}.\n" for n in range(1, 6)
    )
    p = only(now.check(text), "has 5 open items (max 4)")
    assert "DOWN a tier" in p


def test_now_uncapped_section_allows_five():
    text = "# NOW\n\n## Waiting on someone else\n\n" + "".join(
        f"- [ ] Item number {n}.\n" for n in range(1, 6)
    )
    assert now.check(text) == []


def test_now_done_word_fails():
    text = "# NOW\n\n## This week\n\n- [ ] Gateway auto-reconnect SHIPPED on strategylab01.\n"
    p = only(now.check(text), "records completed work")
    assert "SHIPPED" in p


def test_now_long_item_fails():
    text = "# NOW\n\n## This week\n\n- [ ] " + "a" * 500 + "\n"
    only(now.check(text), f"max {now.MAX_ITEM_CHARS}")
