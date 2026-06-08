"""parallel_map.py — Shared parallel-map utilities for StrategyLab research.

F380: a thin, picklable ProcessPool helper reused across analysis phases
(bootstrap grids, power_audit Monte Carlo, gap-lens I/O).

Design principles
-----------------
* **Determinism gate (F357/F369 axiom):** every worker receives a
  deterministic per-task seed derived from ``seed ^ task_index``
  (XOR is injective across the integer range used here, making each
  task seed unique while remaining computable from the base seed alone).
  Workers MUST NOT call ``np.random.default_rng()`` without a seed.

* **Picklable workers only:** function arguments must be pickle-safe
  (no in-process closures captured over non-serialisable objects).
  Module-level callables or ``functools.partial`` are fine.

* **Graceful serial fallback:** ``parallel_map`` falls back to the
  serial path when ``workers=1`` or ``n_tasks <= 1``.  This keeps the
  serial path as the reference path for diffing.

* **Ordering preserved:** results are returned in input order regardless
  of completion order.

Public API
----------
* ``parallel_map(fn, tasks, *, workers, seed_base)``
  Map ``fn(task, seed)`` over ``tasks`` using a ProcessPool, returning
  results in input order.  ``seed`` is ``seed_base ^ i`` for task index
  ``i``.

* ``task_seed(seed_base, task_index)``
  Deterministic per-task seed: ``seed_base ^ task_index``.  XOR is safe
  here because ``task_index`` is a small non-negative integer and
  ``seed_base`` is a large fixed constant (e.g. 20260606), so
  ``seed_base ^ 0 == seed_base`` (task 0 gets the base seed unchanged
  — matches the serial path).

* ``cpu_count()``
  Return a safe default worker count: min(os.cpu_count(), n_tasks).
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")

__all__ = [
    "parallel_map",
    "task_seed",
    "cpu_count",
]


def task_seed(seed_base: int, task_index: int) -> int:
    """Deterministic per-task seed.

    Returns ``seed_base ^ task_index``.  XOR with small indices keeps
    seeds distinct and fully deterministic from (seed_base, task_index).
    Task 0 intentionally gets ``seed_base`` unchanged, so a single-task
    run is byte-identical to the serial path.

    Parameters
    ----------
    seed_base  : fixed base seed for the whole run (e.g. 20260606).
    task_index : zero-based index in the task list.

    DI-06 / RM-04 — XOR ALIASING NOTE:
    XOR is injective only when (seed_base, task_index) pairs are constrained.
    In general, seed_base=4 XOR index=5 == seed_base=5 XOR index=4 == 1 —
    two different (base, index) pairs produce the same seed.  For our fixed
    usage (seed_base=20260608 = 0x135200, task_index ≤ 47) the derived
    seeds are 20260608–20260655: all distinct, no aliasing.

    Hardening to a hash-based mix (e.g. hashlib.shake_128) would eliminate
    the aliasing edge case but CHANGES every derived seed and therefore
    BREAKS byte-identical reproducibility for any caller that uses the
    passed seed (e.g. _power_cell_worker in power_audit.py).  That is a
    methodology decision, not a purely mechanical fix.  Deferred to a
    separate TODO with John's sign-off; do not change here without running
    test_determinism.py AND re-validating all stored results.
    """
    return int(seed_base) ^ int(task_index)


def cpu_count(n_tasks: int | None = None) -> int:
    """Return a safe default worker count.

    Returns ``os.cpu_count()`` capped at ``n_tasks`` if provided.
    Falls back to 1 if the OS count cannot be determined.
    """
    n_cpu = os.cpu_count() or 1
    if n_tasks is not None:
        return max(1, min(n_cpu, n_tasks))
    return max(1, n_cpu)


def parallel_map(
    fn: Callable[[Any, int], T],
    tasks: list[Any],
    *,
    workers: int = 1,
    seed_base: int = 0,
) -> list[T]:
    """Map ``fn(task, seed)`` over ``tasks`` in parallel.

    Preserves input order in the returned list.

    Parameters
    ----------
    fn         : callable(task, seed) → result.  Must be top-level or
                 ``functools.partial`` (pickle-safe).
    tasks      : list of task descriptors passed to ``fn``.
    workers    : number of ProcessPool workers.  Use 1 for serial.
    seed_base  : base seed; task ``i`` receives ``task_seed(seed_base, i)``.

    Returns
    -------
    List of results in the same order as ``tasks``.

    Notes
    -----
    Falls back to the serial path when ``workers <= 1`` or ``len(tasks) <= 1``
    to avoid fork overhead and keep the serial reference path intact.
    """
    n = len(tasks)
    if n == 0:
        return []

    seeds = [task_seed(seed_base, i) for i in range(n)]

    # Serial path — reference implementation, no fork overhead
    if workers <= 1 or n <= 1:
        return [fn(task, seed) for task, seed in zip(tasks, seeds)]

    # Parallel path
    results: list[Any] = [None] * n
    effective_workers = min(workers, n)
    with ProcessPoolExecutor(max_workers=effective_workers) as pool:
        futures = {
            pool.submit(fn, task, seed): idx
            for idx, (task, seed) in enumerate(zip(tasks, seeds))
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            results[idx] = fut.result()  # re-raises exceptions from workers
    return results
