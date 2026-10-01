"""Display and bypass flags, end to end (F435 W3 item 3.B, spec S16).

The frontend test flags.test.ts presses B on the EMA of the graph in
vectors/flags.json and checks the graph JSON it sends equals `after_bypass`.
This test takes that same JSON and checks the backend treats it as
pass-through: the bypassed EMA's output stream is its input stream, and the
entry signals match the graph with the EMA removed. Moving the display flag
never changes the signals.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nodebuilder.compile import check_graph, compile as nb_compile
from nodebuilder.evaluator import cook_signals
from nodebuilder.models import Graph

VECTORS = json.loads((Path(__file__).parent / "vectors" / "flags.json").read_text())
N = 300


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    t = np.arange(N)
    close = 100 + 5 * np.sin(t / 12) + np.cumsum(rng.normal(0, 0.6, N))
    high = close + np.abs(rng.normal(0, 0.5, N))
    low = close - np.abs(rng.normal(0, 0.5, N))
    idx = pd.date_range("2023-01-02", periods=N, freq="B", tz="America/New_York")
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1e5, 1e6, N)}, index=idx)


def _without(data: dict, node_id: str) -> dict:
    """The graph with `node_id` cut out and its input wired straight to its consumers."""
    g = copy.deepcopy(data)
    del g["nodes"][node_id]
    [w_in] = [w for w in g["wires"] if w["to"] == node_id]
    wires = []
    for w in g["wires"]:
        if w["to"] == node_id:
            continue
        if w["from"] == node_id:
            w = {**w, "from": w_in["from"]}
        wires.append(w)
    g["wires"] = wires
    return g


def test_bypassed_node_passes_its_input_through(df):
    bypassed = VECTORS["bypass_node"]
    after = Graph.model_validate(VECTORS["after_bypass"])
    assert after.nodes[bypassed].bypass is True

    prog = nb_compile(after)
    assert prog.step(bypassed).mode == "pass"
    streams = check_graph(after).streams_json()
    assert streams[bypassed] == streams["n_tick"]

    entry, exit_ = cook_signals(prog, df)
    ref_entry, ref_exit = cook_signals(nb_compile(Graph.model_validate(_without(VECTORS["after_bypass"], bypassed))), df)
    np.testing.assert_array_equal(entry, ref_entry)
    np.testing.assert_array_equal(exit_, ref_exit)
    assert entry.any(), "the vector should fire at least once, or the check proves nothing"


def test_unbypassed_node_runs(df):
    before = Graph.model_validate(VECTORS["before"])
    bypassed = VECTORS["bypass_node"]
    assert before.nodes[bypassed].bypass is False
    assert nb_compile(before).step(bypassed).mode != "pass"
    streams = check_graph(before).streams_json()
    assert streams[bypassed] != streams["n_tick"]


def test_display_flag_never_changes_signals(df):
    data = copy.deepcopy(VECTORS["after_bypass"])
    base = cook_signals(nb_compile(Graph.model_validate(data)), df)
    for nid, on in VECTORS["after_display"].items():
        data["nodes"][nid]["display"] = on
    moved = cook_signals(nb_compile(Graph.model_validate(data)), df)
    np.testing.assert_array_equal(base[0], moved[0])
    np.testing.assert_array_equal(base[1], moved[1])
