"""audit_log(): one code_audit line per snippet (F435 W7 item 7.A, design
note 4.10).  Item 7.C calls it on graph saves and bot starts; its own
test_code_audit.py checks those call sites.
"""
from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace

from nodebuilder.code import AUDIT_LOGGER_NAME, audit_log, has_code, iter_code_snippets

WRANGLE = "@spread = @close - @spy_close\n@z = sl.zscore(@spread, chi('n', default=20))"
BLOCK = "@rsi_smooth = sl.ema(@rsi, 3)"
EXPR = '7 if chf("../vol/threshold") > 2 else 21'


def _graph() -> dict:
    return {"nodes": {
        "/t": {"id": "/t", "type": "ticker", "name": "aapl", "params": {}},
        "n_spread": {"id": "n_spread", "type": "wrangle", "name": "spread_z", "code": WRANGLE,
                     "params": {"n": 20}},
        "n_rsi": {"id": "n_rsi", "type": "rsi", "name": "rsi", "code": BLOCK,
                  "params": {"period": {"expr": EXPR}, "type": "wilder"}},
        "n_blank": {"id": "n_blank", "type": "sma", "name": "sma", "code": "  \n",
                    "params": {"period": {"expr": "   "}}},
    }, "wires": []}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_one_line_per_snippet_with_the_hash_of_the_original_source(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    lines = audit_log("graph_save", _graph(), graph_id="g_3f9a1c7e2b40", rev=5,
                      email="john@example.com")
    assert lines == [
        f"code_audit event=graph_save graph_id=g_3f9a1c7e2b40 rev=5 node_id=n_spread "
        f"name=spread_z level=wrangle sha256={_sha(WRANGLE)} bytes={len(WRANGLE)} "
        f"email=john@example.com",
        f"code_audit event=graph_save graph_id=g_3f9a1c7e2b40 rev=5 node_id=n_rsi name=rsi "
        f"level=node_code sha256={_sha(BLOCK)} bytes={len(BLOCK)} email=john@example.com",
        f"code_audit event=graph_save graph_id=g_3f9a1c7e2b40 rev=5 node_id=n_rsi name=rsi "
        f"level=expr:period sha256={_sha(EXPR)} bytes={len(EXPR)} email=john@example.com",
    ]
    logged = [r for r in caplog.records if r.name == AUDIT_LOGGER_NAME]
    assert [r.getMessage() for r in logged] == lines
    assert {r.levelno for r in logged} == {logging.INFO}


def test_a_bot_start_line_and_no_email():
    lines = audit_log("bot_start", _graph(), bot_id="b_1")
    assert lines[0].startswith("code_audit event=bot_start bot_id=b_1 node_id=n_spread ")
    assert all(line.endswith(" email=-") for line in lines)


def test_bytes_count_utf8_and_the_hash_is_of_the_text_before_the_rewrite():
    src = '@x = @close  # é'
    (line,) = audit_log("graph_save", {"nodes": {"w": {"id": "w", "type": "wrangle",
                                                        "code": src}}}, graph_id="g")
    assert f"sha256={_sha(src)} bytes={len(src.encode('utf-8'))}" in line
    assert "name=w" in line          # no name: the id stands in


def test_values_never_break_the_line():
    graph = {"nodes": {"w": {"id": "w", "type": "wrangle", "name": "bad\nname x", "code": "x=1"}}}
    (line,) = audit_log("graph_save", graph, email="a b\nc", graph_id=None)
    assert "\n" not in line
    assert "name=bad_name_x" in line and "email=a_b_c" in line and "graph_id=-" in line


def test_snippets_from_models_and_lists_and_has_code():
    nodes = [SimpleNamespace(id="a", name="a", type="wrangle", code="@x = 1", params={}),
             SimpleNamespace(id="b", name="b", type="rsi", code="",
                             params={"period": SimpleNamespace(expr="14")})]
    snippets = iter_code_snippets(SimpleNamespace(nodes={n.id: n for n in nodes}))
    assert [(s.node_id, s.level, s.context, s.param) for s in snippets] == [
        ("a", "wrangle", "wrangle", None), ("b", "expr:period", "expr", "period")]
    assert has_code(nodes) is True
    assert has_code({"nodes": {"t": {"id": "t", "type": "ticker", "params": {"symbol": "AAPL"}}}}) \
        is False
    assert audit_log("graph_save", {"nodes": {}}) == []
