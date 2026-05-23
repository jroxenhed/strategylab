"""Snapshot helpers — numpy/NaN/Timestamp-aware JSON encoder + tolerance comparison."""
import json
import math
import numpy as np
import pandas as pd
import pytest
from typing import Any


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            f = float(obj)
            return "__NaN__" if math.isnan(f) else f
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super().default(obj)


def _wrap_nan(o):
    """Pre-process Python dicts/lists/scalars to replace NaN with sentinel."""
    if isinstance(o, dict):
        return {k: _wrap_nan(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_wrap_nan(v) for v in o]
    if isinstance(o, float) and math.isnan(o):
        return "__NaN__"
    return o


def dump_snapshot(data: dict, path: str) -> None:
    """Write a backtest output dict to a deterministic JSON file."""
    cleaned = _wrap_nan(data)
    with open(path, "w") as f:
        json.dump(cleaned, f, cls=_Encoder, indent=2, sort_keys=True)


def load_snapshot(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _is_numeric(v: Any) -> bool:
    """Return True for int, float, and numpy numeric scalars."""
    if isinstance(v, bool):
        return False  # bool is subclass of int; treat as categorical
    return isinstance(v, (int, float, np.integer, np.floating))


def assert_equal_within_tolerance(actual: Any, expected: Any, rel: float = 1e-9,
                                   path: str = "") -> None:
    """Walk two structures; categorical/list-length/key-set fields exact;
    numeric fields within `rel` relative tolerance (same code path → ~1e-9).

    Handles np.float64/np.int64 in `actual` (live run_backtest output) vs
    plain Python int/float in `expected` (JSON-loaded fixtures).
    """
    # Both numeric → compare with tolerance (handles np.float64 vs float)
    if _is_numeric(actual) and _is_numeric(expected):
        pass  # fall through to numeric comparison below
    elif type(actual) != type(expected):
        # Allow JSON int↔float promotion for plain Python types
        if not (isinstance(actual, (int, float)) and isinstance(expected, (int, float))):
            raise AssertionError(
                f"type mismatch at {path}: {type(actual).__name__} vs {type(expected).__name__}"
            )

    if isinstance(expected, dict):
        if set(actual.keys()) != set(expected.keys()):
            raise AssertionError(
                f"key set mismatch at {path}: extra={set(actual)-set(expected)} "
                f"missing={set(expected)-set(actual)}"
            )
        for k in expected:
            assert_equal_within_tolerance(actual[k], expected[k], rel, f"{path}.{k}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise AssertionError(f"length mismatch at {path}: {len(actual)} vs {len(expected)}")
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_equal_within_tolerance(a, e, rel, f"{path}[{i}]")
    elif isinstance(expected, str) and expected == "__NaN__":
        # actual may be np.float64 NaN or the sentinel string
        if isinstance(actual, str):
            if actual != "__NaN__":
                raise AssertionError(f"NaN expected at {path}, got {actual!r}")
        elif _is_numeric(actual):
            import math
            if not math.isnan(float(actual)):
                raise AssertionError(f"NaN expected at {path}, got {actual!r}")
        else:
            raise AssertionError(f"NaN expected at {path}, got {actual!r}")
    elif _is_numeric(expected) and not isinstance(expected, bool):
        actual_f = float(actual)
        expected_f = float(expected)
        ea = pytest.approx(expected_f, rel=rel, abs=1e-12)
        if actual_f != ea:
            raise AssertionError(f"numeric mismatch at {path}: {actual_f} != approx({expected_f}, rel={rel})")
    else:
        if actual != expected:
            raise AssertionError(f"value mismatch at {path}: {actual!r} != {expected!r}")
