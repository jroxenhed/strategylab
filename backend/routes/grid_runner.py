"""
routes/grid_runner.py — shared serial grid runner for the optimizer + WFA IS-loop.

Runs `run_backtest` over a Cartesian product of param values serially with a
single shared `indicator_cache`. The cache amortizes MACD/RSI/ATR/MA/BB
computation across every combo in the grid (same underlying data slice → same
indicator series), which is the dominant speedup over the prior copy-pasted
loops: profiled at ~6× on a 24-combo SPY daily grid.

ProcessPool parallelism was prototyped and dropped (see F166): with the cache
in place, per-backtest cost falls to ~10ms, and spawn-context worker startup
(~100ms each, plus cold _fetch + cache rebuild per worker) dominates wall-clock
on the grid sizes the UI currently allows (≤200 combos for optimizer, ≤50/window
for WFA). The serial path with shared cache beats parallel-without-cache on
every grid size we benchmarked. A persistent pool with df-ship-via-initializer
plus window-level parallelism for WFA is the design that would actually win;
deferred to F166 if a real large-grid workload appears.

Public contract:
  run_grid(base, params, timeout_secs)
    -> tuple[list[tuple[dict, dict]], bool, int]
  Returns (results, timed_out, skipped):
    - results: list of (combo_dict, summary_dict) for successful runs only.
      Order matches `itertools.product(*[p.values for p in params])`.
    - timed_out: True iff the loop broke out before completing all combos.
    - skipped: count of combos that ran and were rejected (4xx HTTPException
      or generic exception). DOES NOT include combos that never ran due to
      timeout — `total_combos - len(results) - skipped` gives the unreached
      count when timed_out=True.
  Combos that 4xx'd inside run_backtest are silently dropped + counted as skipped.
  Combos that raise an unexpected exception are dropped, logged, counted as skipped.
  Combos that raise a 5xx HTTPException re-raise to the caller.
  HTTPException(400) from _apply_param (unsupported param_path) is raised
  PRIOR to entering the loop via an upfront validation step.
"""
import logging
from itertools import product
from time import monotonic
from typing import Protocol

from fastapi import HTTPException

from models import StrategyRequest
from routes.backtest import run_backtest  # module-level so tests can patch
from routes.backtest_sweep import _apply_param

logger = logging.getLogger(__name__)


class _GridParam(Protocol):
    """Duck-typed: both OptimizeParam and WalkForwardParam satisfy this."""

    path: str
    values: list[float]


def run_grid(
    base: StrategyRequest,
    params: list[_GridParam],
    timeout_secs: float,
) -> tuple[list[tuple[dict, dict]], bool, int]:
    """Run run_backtest over the Cartesian product of params.values.

    See module docstring for the full contract.

    Args:
        base: Base StrategyRequest. `include_spy_correlation=False` is forced
            for every combo (callers that need SPY-corr should run a separate
            backtest on the winner).
        params: list of objects with .path and .values attributes.
        timeout_secs: Wall-clock budget. If exceeded, the loop breaks and
            timed_out=True; combos that finished before the break are kept.

    Returns:
        (results, timed_out, skipped). See module docstring.

    Raises:
        HTTPException(400) if any param_path is invalid (upfront validation).
        HTTPException(5xx) propagated unchanged from run_backtest.
    """
    param_paths = [p.path for p in params]
    combos = list(product(*[p.values for p in params]))

    if not combos:
        return [], False, 0

    # Upfront param-path validation: _apply_param raises HTTPException(400)
    # on unsupported paths. Surface that immediately rather than swallowing
    # it once per combo inside the loop.
    sample = base
    for path, value in zip(param_paths, combos[0]):
        sample = _apply_param(sample, path, value)

    results: list[tuple[dict, dict]] = []
    timed_out = False
    skipped = 0
    start = monotonic()
    # One cache for the whole grid — same df slice means the indicator
    # series (MACD, RSI(period,type), ATR(period), MA(period,type),
    # BB(period,std)) are identical across combos.
    indicator_cache: dict[tuple, object] = {}

    for combo_values in combos:
        if monotonic() - start > timeout_secs:
            timed_out = True
            break
        combo = dict(zip(param_paths, combo_values))
        req = base
        for path, value in combo.items():
            req = _apply_param(req, path, value)
        try:
            r = run_backtest(
                req,
                include_spy_correlation=False,
                indicator_cache=indicator_cache,
            )
            results.append((combo, r["summary"]))
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise
            # 4xx: invalid param for this data slice (e.g. period > bar count) — skip
            skipped += 1
        except Exception as exc:
            logger.warning("grid combo %s raised: %s", combo, exc)
            skipped += 1

    return results, timed_out, skipped
