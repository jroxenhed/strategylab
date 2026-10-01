"""Wave 2 reproduces the frozen Wave 1 engine (F435 W2, MD-05/MD-06).

fixtures/w1_goldens.json holds the Entry and Exit bars the Wave 1 engine
(commit 83d5ba1, exported with git archive; see make_w1_goldens.py)
computed on test_rule_coverage._DF for every stored-graph shape we know:
the auto_render corpus, the v1 vectors, the 30-step bench graph, and
hand-built Wave 1 graphs (unwired and orphan indicators, no Ticker, logic
over 16 inputs, non-numbered ports, fan-out, NOT, nested logic, bypass,
settings, Ticker field labels).

The rule (decisions.md): a graph that compiled in Wave 1 compiles in Wave 2
and gives the same signals.  This replaces the deleted legacy-equivalence
suite: the goldens come from code no longer in the tree, so a mistake in a
shared helper cannot sit on both sides.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from nodebuilder.compile import compile as compile_graph
from nodebuilder.evaluator import cook_signals
from nodebuilder.migrate import CURRENT_GRAPH_VERSION, migrate_graph_data
from nodebuilder.models import Graph, GraphValidationError
from tests.nodebuilder.test_rule_coverage import _DF

_HERE = Path(os.path.dirname(__file__))
_GOLDEN = json.loads((_HERE / "fixtures" / "w1_goldens.json").read_text())
_CORPUS = {c["id"]: c["graph"] for c in
           json.loads((_HERE / "fixtures" / "v2_autorender_corpus.json").read_text())["cases"]}
_V1 = json.loads((_HERE / "vectors" / "v1_autorender.json").read_text())
_BENCH = json.loads((_HERE / "fixtures" / "bench_graph_30.json").read_text())


def _graph_data(case: dict) -> dict:
    src = case["source"]
    if src == "inline":
        return case["graph"]
    if src == "bench_graph_30":
        return _BENCH
    kind, _, key = src.partition(":")
    if kind == "v2_autorender_corpus":
        return _CORPUS[key]
    if kind == "v1_autorender":
        grp, _, name = key.partition(":")
        return _V1[grp][name]
    raise KeyError(src)


_COMPUTED = [c for c in _GOLDEN["cases"] if c["w1"]["ok"]]
_REFUSED = [c for c in _GOLDEN["cases"] if not c["w1"]["ok"]]


def _bars(col: np.ndarray) -> list[int]:
    return [int(i) for i in np.flatnonzero(col)]


def test_frame_is_the_one_the_goldens_were_made_on():
    assert _GOLDEN["bars"] == len(_DF) == 400
    assert len(_COMPUTED) >= 130
    # make_w1_goldens.py keeps a copy of the frame builder; they must agree.
    src = (_HERE / "fixtures" / "make_w1_goldens.py").read_text()
    body = src[src.index("def _synthetic_df"):src.index("DF = _synthetic_df()")]
    ns: dict = {}
    exec("import numpy as np\nimport pandas as pd\n" + body, ns)
    import pandas as pd
    pd.testing.assert_frame_equal(ns["_synthetic_df"](), _DF)


@pytest.mark.parametrize("case", _COMPUTED, ids=[c["id"] for c in _COMPUTED])
def test_wave2_matches_wave1(case):
    graph = Graph.model_validate(copy.deepcopy(_graph_data(case)))
    assert graph.version == CURRENT_GRAPH_VERSION
    entry, exit_ = cook_signals(compile_graph(graph), _DF)
    assert _bars(entry) == case["w1"]["entry"]
    assert _bars(exit_) == case["w1"]["exit"]


@pytest.mark.parametrize("case", _COMPUTED, ids=[c["id"] for c in _COMPUTED])
def test_wave2_matches_wave1_through_the_botsjson_dump(case):
    """bots.json stores model_dump() (key "version", from_path/to_path):
    the other branch of migrate_graph_data."""
    stored = Graph.model_validate(copy.deepcopy(_graph_data(case))).model_dump()
    graph = Graph.model_validate(json.loads(json.dumps(stored)))
    entry, exit_ = cook_signals(compile_graph(graph), _DF)
    assert _bars(entry) == case["w1"]["entry"]
    assert _bars(exit_) == case["w1"]["exit"]


@pytest.mark.parametrize("case", _REFUSED, ids=[c["id"] for c in _REFUSED])
def test_wave1_refusals_are_still_refused(case):
    """The Wave 1 refusals in the corpus (regime, atr_pct rules) stay refused
    (regime until W5); nothing here may start trading silently."""
    with pytest.raises(GraphValidationError):
        compile_graph(Graph.model_validate(copy.deepcopy(_graph_data(case))))


@pytest.mark.parametrize("case", _GOLDEN["cases"], ids=[c["id"] for c in _GOLDEN["cases"]])
def test_migration_is_idempotent(case):
    """MD-10: migrating twice is migrating once, for the raw data and both
    dump forms (storage: by_alias; bots.json: plain model_dump)."""
    data = copy.deepcopy(_graph_data(case))
    once = migrate_graph_data(copy.deepcopy(data))
    assert migrate_graph_data(copy.deepcopy(once)) == once
    graph = Graph.model_validate(data)
    # Compared as dumps: the wire serializer writes each node's inputs in
    # port order, while a loaded graph keeps its list order in memory.
    want = graph.model_dump(by_alias=True)
    for dumped in (graph.model_dump(by_alias=True), graph.model_dump()):
        again = Graph.model_validate(json.loads(json.dumps(dumped)))
        assert again.model_dump(by_alias=True) == want
