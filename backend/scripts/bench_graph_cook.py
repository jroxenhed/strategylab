"""Benchmark how fast the node builder cooks a graph, and how much memory it takes.

F435 Wave 2 item 2.0 (plan docs/plans/2026-09-29-node-builder-finish-plan.md).
"Cook" means: parse the graph, compile it, compute its indicators, and
evaluate its signals over every bar of a frame.  The trade simulator is NOT
part of the cook; it is the same for every engine and lives in routes/.

Two synthetic frames, no network:
  big   500,000 one-minute bars (the Wave 2 gate frame)
  msft  5 years of 1h bars shaped like MSFT (about 8,800 bars)

Both run the 30-node graph in backend/tests/nodebuilder/fixtures/bench_graph_30.json.

Each case runs in its own child process so peak memory is measured cleanly.
Peak RSS is reported as the growth over the RSS just before the cook (the
frame is already built by then), next to the frame's own size in bytes.

Usage (from the repo root):
    backend/venv/bin/python backend/scripts/bench_graph_cook.py --out .run/F435/bench/baseline.json
    backend/venv/bin/python backend/scripts/bench_graph_cook.py --check

--check exits 1 unless the big case cooks in under 3 s (median) and its peak
RSS growth stays under 3x the input frame size.  It is the Wave 2 gate.  The
cook runs the W2 column engine; the per-bar engine it replaced (baseline.json,
86 s on mfcore01) failed it.

This script must not import routes, brokers or FastAPI, so it runs on a
research worker that has only numpy, pandas and pydantic.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
FIXTURE = BACKEND_DIR / "tests" / "nodebuilder" / "fixtures" / "bench_graph_30.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Gate limits (plan section 6, Wave 2 acceptance).
MAX_COOK_SECONDS = 3.0
MAX_RSS_RATIO = 3.0

# Modules that must never be loaded by the bench.
FORBIDDEN_MODULES = ("fastapi", "routes", "broker", "bot_runner", "bot_manager")

CASES = {
    "big": {"desc": "500,000 one-minute synthetic bars", "default_runs": 3},
    "msft": {"desc": "5 years of 1h MSFT-shaped synthetic bars", "default_runs": 5},
}


# ---------------------------------------------------------------------------
# Synthetic frames
# ---------------------------------------------------------------------------

def _ohlcv_from_close(close, rng, index, base_volume: float):
    """Turn a close path into a plausible OHLCV frame."""
    import numpy as np
    import pandas as pd

    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    # Wicks: a small random reach past the body on each side.
    reach = np.abs(rng.normal(0.0, 0.0015, size=(2, n))) * close
    high = np.maximum(open_, close) + reach[0]
    low = np.minimum(open_, close) - reach[1]
    volume = np.maximum(1.0, rng.lognormal(np.log(base_volume), 0.5, size=n)).round()
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


def build_big_frame(rows: int = 500_000, seed: int = 7):
    """A random-walk minute frame with `rows` bars."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    # About 20% a year of volatility spread over minutes.
    rets = rng.normal(0.0, 0.0006, size=rows)
    close = 100.0 * np.exp(np.cumsum(rets))
    index = pd.date_range("2020-01-02 14:30", periods=rows, freq="min", tz="UTC")
    return _ohlcv_from_close(close, rng, index, base_volume=20_000)


def build_msft_frame(seed: int = 11):
    """Five years of regular-hours 1h bars shaped like MSFT (about 280 to 420)."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2021-10-01", "2026-09-30")
    # Seven hourly bars a day, stamped like yfinance: 09:30, 10:30 ... 15:30 ET.
    hours = [pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(hours=h) for h in range(7)]
    stamps = [d + h for d in days for h in hours]
    index = pd.DatetimeIndex(stamps).tz_localize("America/New_York").tz_convert("UTC")
    n = len(index)
    bars_per_year = 252 * 7
    drift = np.log(420.0 / 280.0) / (n)            # ends near 420
    sigma = 0.25 / np.sqrt(bars_per_year)          # 25% a year
    rets = rng.normal(drift, sigma, size=n)
    close = 280.0 * np.exp(np.cumsum(rets))
    return _ohlcv_from_close(close, rng, index, base_volume=3_000_000)


# ---------------------------------------------------------------------------
# The cook: the Wave 2 column engine.  The timing keys are the ones the
# per-bar baseline (.run/F435/bench/baseline.json) recorded, so the results
# stay comparable: indicators_s is now the bar prep (indicators are graph
# nodes and cook inside evaluate_s).
# ---------------------------------------------------------------------------

def load_graph_data() -> dict:
    data = json.loads(FIXTURE.read_text())
    data.pop("_about", None)
    return data


def cook(graph_data: dict, df) -> dict:
    """Parse, compile and cook the graph over every bar of df.

    The column engine: one cook of the whole frame (evaluator.cook_program),
    then the Entry and Exit columns, without the simulator.  Returns timings
    in seconds plus signal counts.
    """
    from nodebuilder.compile import compile as compile_graph
    from nodebuilder.evaluator import bars_from_frame, cook_program, signal_columns
    from nodebuilder.models import Graph

    t0 = time.perf_counter()
    graph = Graph.model_validate(graph_data)
    t1 = time.perf_counter()
    program = compile_graph(graph)
    t2 = time.perf_counter()

    bars = bars_from_frame(df)
    t3 = time.perf_counter()

    result = cook_program(program, index=df.index, bars=bars)
    entry, exit_ = signal_columns(program, result)
    entries = int(entry.sum())
    exits = int(exit_.sum())
    t4 = time.perf_counter()

    return {
        "parse_s": t1 - t0,
        "compile_s": t2 - t1,
        "indicators_s": t3 - t2,
        "evaluate_s": t4 - t3,
        "cook_s": t4 - t0,
        "entry_bars": entries,
        "exit_bars": exits,
        # Kept for comparison with the baseline; both are 0 since W2 (no
        # per-bar program, indicators are steps).
        "per_bar_ops": len(program.per_bar_program),
        "indicator_specs": len(program.indicator_specs),
        "steps": len(program.steps),
        "required_lookback_bars": int(program.required_lookback_bars),
    }


# ---------------------------------------------------------------------------
# Memory helpers (no psutil: /proc on Linux, getrusage elsewhere)
# ---------------------------------------------------------------------------

def _proc_status_kb(field: str) -> int | None:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith(field + ":"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _maxrss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return int(raw) if sys.platform == "darwin" else int(raw) * 1024


def current_rss_bytes() -> tuple[int, str]:
    kb = _proc_status_kb("VmRSS")
    if kb is not None:
        return kb * 1024, "proc_vmrss"
    return _maxrss_bytes(), "ru_maxrss"


def reset_peak() -> bool:
    """Reset the kernel's peak-RSS mark to the current RSS (Linux only)."""
    try:
        with open("/proc/self/clear_refs", "w") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


def peak_rss_bytes() -> tuple[int, str]:
    kb = _proc_status_kb("VmHWM")
    if kb is not None:
        return kb * 1024, "proc_vmhwm"
    return _maxrss_bytes(), "ru_maxrss"


# ---------------------------------------------------------------------------
# Child: one case, N runs
# ---------------------------------------------------------------------------

def run_child(case: str, runs: int, rows: int) -> dict:
    df = build_big_frame(rows) if case == "big" else build_msft_frame()
    graph_data = load_graph_data()
    frame_bytes = int(df.memory_usage(index=True, deep=True).sum())

    # Load the engine modules first so their import cost is not counted as
    # cook memory.
    import indicators  # noqa: F401
    import nodebuilder.compile  # noqa: F401
    import nodebuilder.evaluator  # noqa: F401

    rss_before, rss_method = current_rss_bytes()
    peak_reset = reset_peak()

    results = [cook(graph_data, df) for _ in range(runs)]

    peak, peak_method = peak_rss_bytes()
    loaded_forbidden = sorted(
        m for m in sys.modules if m.split(".")[0] in FORBIDDEN_MODULES
    )
    keys = ("parse_s", "compile_s", "indicators_s", "evaluate_s", "cook_s")
    return {
        "case": case,
        "desc": CASES[case]["desc"],
        "rows": len(df),
        "first_bar": str(df.index[0]),
        "last_bar": str(df.index[-1]),
        "runs": runs,
        "median": {k: round(statistics.median(r[k] for r in results), 4) for k in keys},
        "all_cook_s": [round(r["cook_s"], 4) for r in results],
        "entry_bars": results[0]["entry_bars"],
        "exit_bars": results[0]["exit_bars"],
        "per_bar_ops": results[0]["per_bar_ops"],
        "indicator_specs": results[0]["indicator_specs"],
        "steps": results[0]["steps"],
        "required_lookback_bars": results[0]["required_lookback_bars"],
        "frame_bytes": frame_bytes,
        "rss_before_cook_bytes": rss_before,
        "peak_rss_bytes": peak,
        "peak_rss_growth_bytes": max(0, peak - rss_before),
        "peak_rss_growth_ratio": round(max(0, peak - rss_before) / frame_bytes, 3),
        "rss_method": rss_method,
        "peak_method": peak_method,
        "peak_reset_before_cook": peak_reset,
        "forbidden_modules_loaded": loaded_forbidden,
    }


# ---------------------------------------------------------------------------
# Parent: spawn one child per case, gather, write JSON
# ---------------------------------------------------------------------------

def environment() -> dict:
    import numpy
    import pandas
    import pydantic

    return {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "pandas": pandas.__version__,
        "numpy": numpy.__version__,
        "pydantic": pydantic.__version__,
        "engine": "column (W2 kernel: evaluator.cook_program)",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def gate(big: dict) -> dict:
    cook_ok = big["median"]["cook_s"] < MAX_COOK_SECONDS
    rss_ok = big["peak_rss_growth_bytes"] < MAX_RSS_RATIO * big["frame_bytes"]
    return {
        "max_cook_s": MAX_COOK_SECONDS,
        "max_rss_ratio": MAX_RSS_RATIO,
        "cook_ok": cook_ok,
        "rss_ok": rss_ok,
        "passed": cook_ok and rss_ok,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="write the result JSON here")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 unless the big case is under 3 s and 3x frame RSS")
    ap.add_argument("--cases", default="big,msft", help="comma list of: big, msft")
    ap.add_argument("--runs", type=int, default=None, help="runs per case (median is reported)")
    ap.add_argument("--rows", type=int, default=500_000, help="rows in the big frame")
    ap.add_argument("--note", default="", help="free text stored in the JSON")
    ap.add_argument("--_child", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args._child:
        runs = args.runs or CASES[args._child]["default_runs"]
        print(json.dumps(run_child(args._child, runs, args.rows)))
        return 0

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    for c in cases:
        if c not in CASES:
            ap.error(f"unknown case {c!r}")
    if args.check and "big" not in cases:
        ap.error("--check needs the big case")

    out: dict = {"environment": environment(), "graph_fixture": str(FIXTURE.relative_to(REPO_ROOT)),
                 "note": args.note, "cases": {}}
    for c in cases:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--_child", c, "--rows", str(args.rows)]
        if args.runs:
            cmd += ["--runs", str(args.runs)]
        print(f"[bench] {c}: {CASES[c]['desc']} ...", file=sys.stderr, flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            print(f"[bench] case {c} failed (exit {proc.returncode})", file=sys.stderr)
            return 2
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        out["cases"][c] = res
        print(f"[bench] {c}: rows={res['rows']} cook median={res['median']['cook_s']}s "
              f"(evaluate {res['median']['evaluate_s']}s) peak RSS growth="
              f"{res['peak_rss_growth_bytes'] / 1e6:.1f} MB = {res['peak_rss_growth_ratio']}x frame",
              file=sys.stderr, flush=True)
        if res["forbidden_modules_loaded"]:
            print(f"[bench] forbidden modules loaded: {res['forbidden_modules_loaded']}", file=sys.stderr)
            return 2

    if "big" in out["cases"]:
        out["gate"] = gate(out["cases"]["big"])

    text = json.dumps(out, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
        print(f"[bench] wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    if args.check:
        g = out["gate"]
        print(f"[bench] gate: cook_ok={g['cook_ok']} rss_ok={g['rss_ok']}", file=sys.stderr)
        return 0 if g["passed"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
