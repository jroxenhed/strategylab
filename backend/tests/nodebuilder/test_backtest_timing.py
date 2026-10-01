"""Backtest timing stays within 5 % of Wave 2 (F435 W4 item 4.A, plan 8.2).

Wave 4 changes the editor's backtest in two ways: it keeps every node's
stream from its cook (so the wire inspector can read it) and it stores the
cook in the cook cache.  Neither may slow the backtest down.

The W2 benchmark (.run/F435/bench/w2.json) was measured on another machine
(mfcore01), so absolute times cannot be compared on this Mac.  Instead the
test runs a copy of the Wave 2 backtest (per-bar adapter over the lean,
terminals-only cook) as a baseline in the same process, over the same
30-node bench graph and the same MSFT-shaped frame the benchmark used, and
compares the best of several interleaved runs.  A noisy round is measured
again, up to three rounds.

It also checks the W2 gate itself (bench_graph_cook.MAX_COOK_SECONDS) for
the keep-all cook over the 500,000-bar benchmark frame.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND / "scripts") not in sys.path:
    sys.path.insert(0, str(BACKEND / "scripts"))

from bench_graph_cook import (  # noqa: E402
    MAX_COOK_SECONDS,
    build_big_frame,
    build_msft_frame,
    load_graph_data,
)

import routes.nodebuilder as routes_mod  # noqa: E402
from nodebuilder.api_models import GraphBacktestRequest  # noqa: E402
from nodebuilder.compile import compile as compile_graph  # noqa: E402
from nodebuilder.cook_cache import COOK_CACHE  # noqa: E402
from nodebuilder.models import Graph  # noqa: E402
from nodebuilder.prepare import build_graph_attrs  # noqa: E402
from nodebuilder.run import (  # noqa: E402
    _apply_settings_overrides,
    _build_baseline_curve,
    _make_cached_eval,
    _settings_to_strategy_request,
    cook_attrs,
    run_graph_backtest_cooked,
)

TOLERANCE = 1.05
RUNS = 7
ROUNDS = 3


def _w2_backtest(req: GraphBacktestRequest, df) -> dict:
    """The Wave 2 backtest: the graph cooked lazily through the per-bar
    adapter (evaluate_graph, terminals only), then the simulator."""
    from routes.backtest import _run_simulation
    from shared import _format_time_index

    program = compile_graph(req.graph)
    settings = _apply_settings_overrides(req, program.simulator_settings)
    attrs = build_graph_attrs(program, df, settings.get("trailing_stop"))
    cached_eval = _make_cached_eval(program, attrs)
    direction = settings["direction"]

    def buy(i, _regime):
        return bool(cached_eval(i)["entry"]), [], direction

    def sell(i, _pos, _regime):
        return bool(cached_eval(i)["exit"]), []

    date_strs = _format_time_index(df.index, req.interval)
    sim = _run_simulation(
        df=df, indicators=attrs, buy_signal_fn=buy, sell_signal_fn=sell,
        req=_settings_to_strategy_request(settings, req), b23_mode=False,
        regime_active_series=None, on_flip="hold", date_strs=date_strs,
    )
    _build_baseline_curve(df, settings["initial_capital"], date_strs)
    return sim


def _w4_editor_backtest(req: GraphBacktestRequest, df):
    """What POST /backtest now does: keep-all cook, simulate, cache the cook."""
    response, cook = run_graph_backtest_cooked(req, df)
    return routes_mod._with_cook_id(response, routes_mod._cache_backtest_cook(req, cook))


@pytest.fixture(scope="module")
def msft_case():
    df = build_msft_frame()
    req = GraphBacktestRequest.model_validate({
        "graph": load_graph_data(), "ticker": "MSFT",
        "start": "2021-10-01", "end": "2026-09-30", "interval": "1h",
    })
    return req, df


def _best_times(req, df) -> tuple[float, float]:
    w2, w4 = [], []
    for _ in range(RUNS):
        t = time.perf_counter()
        _w2_backtest(req, df)
        w2.append(time.perf_counter() - t)
        t = time.perf_counter()
        _w4_editor_backtest(req, df)
        w4.append(time.perf_counter() - t)
    return min(w2), min(w4)


def test_editor_backtest_gives_the_w2_trades(msft_case):
    req, df = msft_case
    sim = _w2_backtest(req, df)
    resp = _w4_editor_backtest(req, df)
    assert resp.trades == sim["trades"]
    assert resp.cook_id is not None
    COOK_CACHE.clear()


def test_editor_backtest_within_5_percent_of_w2(msft_case):
    req, df = msft_case
    _best_times(req, df)                      # warm up imports and caches
    ratios = []
    for _ in range(ROUNDS):
        w2, w4 = _best_times(req, df)
        ratios.append(w4 / w2)
        if w4 <= w2 * TOLERANCE:
            break
    COOK_CACHE.clear()
    assert min(ratios) <= TOLERANCE, (
        f"editor backtest is {min(ratios):.3f}x the W2 backtest (rounds: "
        + ", ".join(f"{r:.3f}" for r in ratios) + ")"
    )


def test_keep_all_cook_meets_the_w2_gate_on_500k_bars():
    df = build_big_frame()
    program = compile_graph(Graph.model_validate(load_graph_data()))
    attrs = build_graph_attrs(program, df)
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        result = cook_attrs(program, attrs, keep_all=True)
        best = min(best, time.perf_counter() - t)
        del result
    assert best < MAX_COOK_SECONDS, f"keep-all cook took {best:.3f}s"
