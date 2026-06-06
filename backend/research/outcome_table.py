"""F339 — Outcome Table ETL + Analysis.

Reads all completed research experiment artifacts, builds one flat CSV table
of per-pick forward returns and excess returns, then runs four re-analyses.

Usage:
    python3 backend/research/outcome_table.py build       # ETL → CSV
    python3 backend/research/outcome_table.py summary     # print summary stats

Output:
    backend/data/turnaround/outcome_table.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import random
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _REPO_ROOT / "backend" / "data" / "turnaround"
_OUTPUT_CSV = _DATA_DIR / "outcome_table.csv"
_PRICE_CACHE_DIR = _DATA_DIR / "price_cache"
_PRICE_CACHE_VERSION = "v1"

# ---------------------------------------------------------------------------
# Artifact registry
# ---------------------------------------------------------------------------
# (experiment, arm, filename)
_ARTIFACTS: list[tuple[str, str, str]] = [
    ("momentum_M1",         "explore", "momentum_M1_explore_result.json"),
    ("momentum_M1",         "confirm", "momentum_M1_confirm_result.json"),
    ("deterioration_D1",    "explore", "deterioration_D1_explore_result.json"),
    ("deterioration_D2",    "explore", "deterioration_D2_explore_result.json"),
    ("deterioration_D2",    "confirm", "deterioration_D2_confirm_result.json"),
    ("epistemics_price",    "explore", "epistemics_price_explore_result.json"),
    ("epistemics_price",    "confirm", "epistemics_price_confirm_result.json"),
    ("epistemics_filing",   "explore", "epistemics_filing_explore_result.json"),
    ("epistemics_filing",   "confirm", "epistemics_filing_confirm_result.json"),
]

# Columns in the flat table
_COLUMNS = [
    "experiment",
    "arm",                  # explore | confirm
    "cohort_date",          # as_of date string
    "entry_date",           # first trading day after as_of (for intermediate horizon anchoring)
    "ticker",
    "direction",            # long | short
    "composite_score",
    "fwd_ret_21",           # forward return at 21 trading days (pct)
    "fwd_ret_63",           # forward return at 63 trading days (pct)
    "fwd_ret_126",          # forward return at 126 trading days (pct)
    "excess_21",            # pick return minus cohort universe median at 21d
    "excess_63",            # pick return minus cohort universe median at 63d
    "excess_126",           # pick return minus cohort universe median at 126d
]


# ---------------------------------------------------------------------------
# ETL
# ---------------------------------------------------------------------------

def _load_artifact(path: Path) -> list[dict]:
    """Load events list from a result JSON artifact."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("events", [])


def build_outcome_table(output_path: Path = _OUTPUT_CSV) -> list[dict]:
    """ETL all artifacts into a flat list of row-dicts. Write to CSV. Return rows."""
    rows: list[dict] = []

    for experiment, arm, filename in _ARTIFACTS:
        artifact_path = _DATA_DIR / filename
        if not artifact_path.exists():
            print(f"  WARNING: missing {filename}, skipping", file=sys.stderr)
            continue

        events = _load_artifact(artifact_path)
        for ev in events:
            rows.append(
                {
                    "experiment": experiment,
                    "arm": arm,
                    "cohort_date": ev.get("as_of", ""),
                    "entry_date": ev.get("entry_date", ev.get("as_of", "")),
                    "ticker": ev.get("ticker", ""),
                    "direction": ev.get("direction", ""),
                    "composite_score": ev.get("composite_score"),
                    "fwd_ret_21": ev.get("fwd_return_21d"),
                    "fwd_ret_63": ev.get("fwd_return_63d"),
                    "fwd_ret_126": ev.get("fwd_return_126d"),
                    "excess_21": ev.get("excess_21d"),
                    "excess_63": ev.get("excess_63d"),
                    "excess_126": ev.get("excess_126d"),
                }
            )

    # Write CSV
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return rows


# ---------------------------------------------------------------------------
# Bootstrap CI helper
# ---------------------------------------------------------------------------

def bootstrap_mean_ci(
    values: list[float],
    n_resamples: int = 10_000,
    ci_level: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Bootstrap the mean of *values*. Return (mean, ci_low, ci_high).

    Uses the percentile method (not BCa) — adequate for these sample sizes.
    """
    if not values:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = 1.0 - ci_level
    lo_idx = max(0, int(math.floor(alpha / 2 * n_resamples)))
    hi_idx = min(n_resamples - 1, int(math.ceil((1 - alpha / 2) * n_resamples)))
    return (sum(values) / n, means[lo_idx], means[hi_idx])


def minimum_detectable_effect(n: int, std: float) -> float:
    """MDE for a one-sample t-test (mean vs zero).

    Returns the smallest true mean detectable at 80% power with 5% significance
    (two-tailed). Uses normal approximation: MDE = (z_alpha + z_power) * std / sqrt(n).
    Fixed values: z_alpha=1.96 (two-tailed 5%), z_power=0.842 (80% power).
    """
    if n <= 0 or std <= 0:
        return float("nan")
    z_alpha = 1.96   # two-tailed 5%
    z_power = 0.842  # 80% power
    return (z_alpha + z_power) * std / math.sqrt(n)


# ---------------------------------------------------------------------------
# Analysis A — Effect CIs + MDE per experiment arm/window
# ---------------------------------------------------------------------------

def analysis_a_effect_ci(
    rows: list[dict],
    n_resamples: int = 10_000,
) -> list[dict]:
    """For each (experiment, arm) grouping, bootstrap the mean excess_63 across
    cohorts and compute MDE. Returns a list of result dicts.

    Cohort-level aggregation: average excess_63 per cohort date, then bootstrap
    across cohorts (preserves the correlation within a cohort — each cohort is
    one observation, not each pick). This is the right aggregation unit because
    picks within a cohort share macro/market conditions.
    """
    from itertools import groupby

    # Group by (experiment, arm)
    key = lambda r: (r["experiment"], r["arm"])
    sorted_rows = sorted(rows, key=key)

    results = []
    for (exp, arm), group_iter in groupby(sorted_rows, key=key):
        group = list(group_iter)

        # Build cohort-level means for each horizon
        for horizon, col in [(21, "excess_21"), (63, "excess_63"), (126, "excess_126")]:
            # Per cohort mean
            cohort_vals: dict[str, list[float]] = {}
            for r in group:
                v = r[col]
                if v is None:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                cd = r["cohort_date"]
                cohort_vals.setdefault(cd, []).append(v)

            cohort_means = [sum(vs) / len(vs) for vs in cohort_vals.values()]
            n_cohorts = len(cohort_means)
            n_picks = sum(len(vs) for vs in cohort_vals.values())

            if n_cohorts < 2:
                continue

            mean, ci_lo, ci_hi = bootstrap_mean_ci(cohort_means, n_resamples=n_resamples)
            std = math.sqrt(sum((x - mean) ** 2 for x in cohort_means) / (n_cohorts - 1))
            mde = minimum_detectable_effect(n_cohorts, std)

            results.append(
                {
                    "experiment": exp,
                    "arm": arm,
                    "horizon": horizon,
                    "n_cohorts": n_cohorts,
                    "n_picks": n_picks,
                    "mean_excess": mean,
                    "ci_lo": ci_lo,
                    "ci_hi": ci_hi,
                    "mde_80pct": mde,
                    "std_cohort": std,
                }
            )

    return results


# ---------------------------------------------------------------------------
# Analysis B — Momentum decay curve
# ---------------------------------------------------------------------------

def _ticker_key(ticker: str) -> str:
    """CRC32-based filename key (mirrors PriceFrameCache convention)."""
    import binascii

    crc = binascii.crc32(ticker.encode()) & 0xFFFFFFFF
    clean = "".join(c if c.isalnum() or c in "-." else "_" for c in ticker)
    return f"{clean}_{crc:08x}"


def _safe_source(data_source: str) -> str:
    """Sanitise provider string for filename (mirrors PriceFrameCache)."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in data_source)


def _find_cached_price_frame(
    ticker: str,
    cache_dir: Path,
    data_source: str = "yahoo",
) -> Optional[Any]:
    """Find and load any cached price frame for ticker (any span)."""
    version_dir = cache_dir / _PRICE_CACHE_VERSION
    key = _ticker_key(ticker)
    ds = _safe_source(data_source)
    prefix = f"{key}_{ds}_"
    matches = list(version_dir.glob(f"{prefix}*.pkl"))
    if not matches:
        return None
    # Use the file with the widest span (largest by filename length as proxy)
    matches.sort(key=lambda p: len(p.name), reverse=True)
    try:
        with open(matches[0], "rb") as fh:
            df = pickle.load(fh)
        if not hasattr(df, "index"):
            return None
        return df
    except Exception as exc:
        print(f"  WARN: failed to load {matches[0].name}: {exc}", file=sys.stderr)
        return None


def _bar_fwd_return(df: Any, entry_date_str: str, n_bars: int) -> Optional[float]:
    """Compute n-bar forward return from entry_date in df (Close column)."""
    import pandas as pd

    if "Close" not in df.columns:
        raise KeyError(f"No 'Close' column in price frame for {entry_date_str}")
    try:
        # Normalise index to date-only for matching
        idx = df.index
        if hasattr(idx, "normalize"):
            dates = idx.normalize()
        else:
            dates = pd.DatetimeIndex(idx).normalize()
        entry_dt = pd.Timestamp(entry_date_str).normalize()

        # Find the entry bar (or the next available bar)
        positions = [i for i, d in enumerate(dates) if d.date() >= entry_dt.date()]
        if not positions:
            return None
        entry_pos = positions[0]
        exit_pos = entry_pos + n_bars
        if exit_pos >= len(df):
            return None

        entry_close = float(df.iloc[entry_pos]["Close"])
        exit_close = float(df.iloc[exit_pos]["Close"])
        if entry_close == 0:
            return None
        return 100.0 * (exit_close - entry_close) / entry_close
    except (IndexError, ValueError):
        return None


def analysis_b_momentum_decay(
    rows: list[dict],
    horizons: Sequence[int] = (5, 10, 21, 42, 63, 84, 105, 126),
    max_picks: int = 500,
    seed: int = 42,
) -> dict:
    """Momentum decay curve: mean forward return at each horizon.

    Uses picks already in the outcome table at 21/63/126d; for intermediate
    horizons (5, 10, 42, 84, 105) recomputes from the on-disk price cache.
    To avoid >15 min runtime, sample up to *max_picks* picks if needed.

    Returns dict with horizons, per-horizon means (picks-only absolute, no
    universe baseline for intermediate horizons), and a caveat string.
    """
    mom_rows = [r for r in rows if r["experiment"].startswith("momentum_M1")]
    rng = random.Random(seed)

    # Use explore + confirm combined
    if len(mom_rows) > max_picks:
        sample = rng.sample(mom_rows, max_picks)
        sampled = True
    else:
        sample = mom_rows
        sampled = False

    # For horizons already in the table: use directly (faster, no I/O)
    table_horizons = {21: "fwd_ret_21", 63: "fwd_ret_63", 126: "fwd_ret_126"}

    horizon_means: dict[int, Optional[float]] = {}
    horizon_ns: dict[int, int] = {}

    for h in sorted(set(horizons)):
        if h in table_horizons:
            col = table_horizons[h]
            vals = [float(r[col]) for r in sample if r[col] is not None]
        else:
            # Recompute from price cache
            # Use entry_date (first trading day after as_of) to match artifact convention;
            # fall back to cohort_date for rows that pre-date the entry_date column.
            vals = []
            skip_count = 0
            cache_dir = _PRICE_CACHE_DIR
            for r in sample:
                df = _find_cached_price_frame(r["ticker"], cache_dir)
                if df is None:
                    skip_count += 1
                    continue
                anchor = r.get("entry_date") or r["cohort_date"]
                ret = _bar_fwd_return(df, anchor, h)
                if ret is not None:
                    vals.append(ret)
                else:
                    skip_count += 1
            if skip_count > 0:
                print(
                    f"  WARN: analysis_b horizon={h}d skipped {skip_count} picks "
                    f"(no cache or insufficient forward bars)",
                    file=sys.stderr,
                )

        if vals:
            horizon_means[h] = sum(vals) / len(vals)
            horizon_ns[h] = len(vals)
        else:
            horizon_means[h] = None
            horizon_ns[h] = 0

    caveat = (
        f"Downsampled to {max_picks} picks (random seed={seed})." if sampled
        else f"Full sample ({len(sample)} picks)."
    )
    caveat += (
        " Intermediate horizons (not 21/63/126d) use raw absolute returns from "
        "price cache — no universe-median excess available at those horizons."
    )

    return {
        "horizons": sorted(set(horizons)),
        "means": horizon_means,
        "ns": horizon_ns,
        "n_total_picks": len(mom_rows),
        "n_sampled": len(sample),
        "sampled": sampled,
        "caveat": caveat,
    }


# ---------------------------------------------------------------------------
# Analysis C — Deterioration-reversal-as-long (D2 confirm)
# ---------------------------------------------------------------------------

def analysis_c_d2_long(rows: list[dict], n_resamples: int = 10_000) -> dict:
    """D2 confirm picks' excess returns with bootstrap CI (cohort-level).

    D2 confirm picks stocks that showed deterioration AND survived (still filing
    on time). Direction in the artifact is 'short', but the hypothesis is that
    these beaten-down survivors outperform as LONG positions — so excess returns
    are read directly from the artifact (already computed vs universe, sign is
    positive = outperformance for the long).

    Cohort-level aggregation: average excess per cohort date, then bootstrap
    across ~16 cohort means. This is the correct unit for the same reason as
    analysis_a — picks within a cohort share market conditions and are not
    exchangeable independent draws. Pick-level bootstrap would produce a CI
    ~6x too narrow.
    """
    d2c = [r for r in rows if r["experiment"] == "deterioration_D2" and r["arm"] == "confirm"]

    result = {}
    for horizon, col in [(21, "excess_21"), (63, "excess_63"), (126, "excess_126")]:
        # Aggregate to cohort means first
        cohort_vals: dict[str, list[float]] = {}
        for r in d2c:
            v = r[col]
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            cohort_vals.setdefault(r["cohort_date"], []).append(v)

        cohort_means = [sum(vs) / len(vs) for vs in cohort_vals.values()]
        n_cohorts = len(cohort_means)
        n_picks = sum(len(vs) for vs in cohort_vals.values())

        if n_cohorts < 2:
            result[horizon] = None
            continue

        mean, ci_lo, ci_hi = bootstrap_mean_ci(cohort_means, n_resamples=n_resamples)
        result[horizon] = {
            "n_cohorts": n_cohorts,
            "n_picks": n_picks,
            "mean_excess": mean,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
        }

    return result


# ---------------------------------------------------------------------------
# Analysis D — Top-1 concentration re-score
# ---------------------------------------------------------------------------

def analysis_d_top1_concentration(rows: list[dict]) -> dict:
    """Re-score each cohort using only the single highest-scored pick.

    Requires composite_score to be populated. Returns per-experiment mean
    excess_63 when only the top-1 pick per cohort is used, vs all-picks mean.
    """
    from itertools import groupby

    key = lambda r: (r["experiment"], r["arm"])
    sorted_rows = sorted(rows, key=key)

    results = []
    for (exp, arm), group_iter in groupby(sorted_rows, key=key):
        group = list(group_iter)

        # Check score availability
        scored = [r for r in group if r.get("composite_score") is not None]
        if not scored:
            results.append(
                {
                    "experiment": exp,
                    "arm": arm,
                    "status": "skipped: no composite_score",
                }
            )
            continue

        # Build cohort → top-1 pick
        cohort_map: dict[str, list[dict]] = {}
        for r in scored:
            cohort_map.setdefault(r["cohort_date"], []).append(r)

        top1_excess: list[float] = []
        all_excess: list[float] = []
        for cd, picks in cohort_map.items():
            # Top-1 by composite_score (higher = better ranked)
            top = max(picks, key=lambda r: float(r["composite_score"]))
            if top["excess_63"] is not None:
                top1_excess.append(float(top["excess_63"]))
            for r in picks:
                if r["excess_63"] is not None:
                    all_excess.append(float(r["excess_63"]))

        if not top1_excess:
            results.append(
                {"experiment": exp, "arm": arm, "status": "no valid excess_63 for top-1"}
            )
            continue

        results.append(
            {
                "experiment": exp,
                "arm": arm,
                "n_cohorts": len(cohort_map),
                "top1_mean_excess_63": sum(top1_excess) / len(top1_excess),
                "all_picks_mean_excess_63": sum(all_excess) / len(all_excess) if all_excess else None,
                "top1_n_cohorts": len(top1_excess),
                "status": "ok",
            }
        )

    return {"rows": results}


# ---------------------------------------------------------------------------
# Summary command
# ---------------------------------------------------------------------------

def print_summary(rows: list[dict]) -> None:
    """Print a concise summary of the outcome table to stdout."""
    total = len(rows)
    print(f"Outcome table: {total} rows")

    from itertools import groupby

    key = lambda r: (r["experiment"], r["arm"])
    for (exp, arm), group_iter in groupby(sorted(rows, key=key), key=key):
        group = list(group_iter)
        n63 = sum(1 for r in group if r["fwd_ret_63"] is not None)

        # Mean-of-cohort-means (the anchor estimator, matches analysis_a)
        cohort_excess63: dict[str, list[float]] = {}
        for r in group:
            v = r["excess_63"]
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            cohort_excess63.setdefault(r["cohort_date"], []).append(v)
        if cohort_excess63:
            cohort_means = [sum(vs) / len(vs) for vs in cohort_excess63.values()]
            mean_e63 = sum(cohort_means) / len(cohort_means)
        else:
            mean_e63 = float("nan")

        cohorts = len(set(r["cohort_date"] for r in group))
        print(
            f"  {exp:30s} {arm:8s}  n={len(group):5d}  cohorts={cohorts:3d}"
            f"  63d_coverage={n63}/{len(group)}"
            f"  mean_excess_63={mean_e63:+.2f}% (cohort-mean)"
        )


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _fmt_ci(v: float) -> str:
    return f"{v:+.2f}" if not math.isnan(v) else "nan"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="F339 outcome table ETL and analysis."
    )
    parser.add_argument(
        "command",
        choices=["build", "summary"],
        help="'build' runs ETL + all analyses; 'summary' prints table stats.",
    )
    args = parser.parse_args()

    if args.command == "build":
        print("Building outcome table...", flush=True)
        rows = build_outcome_table(_OUTPUT_CSV)
        print(f"  Written {len(rows)} rows to {_OUTPUT_CSV}", flush=True)

        print_summary(rows)

        print("\n--- Analysis A: Effect CIs + MDE ---", flush=True)
        a_results = analysis_a_effect_ci(rows)
        for r in sorted(a_results, key=lambda x: (x["experiment"], x["arm"], x["horizon"])):
            print(
                f"  {r['experiment']:30s} {r['arm']:8s} {r['horizon']:3d}d  "
                f"n_cohorts={r['n_cohorts']:3d}  "
                f"mean_excess={_fmt_ci(r['mean_excess'])}%  "
                f"95%CI=[{_fmt_ci(r['ci_lo'])}, {_fmt_ci(r['ci_hi'])}]  "
                f"MDE={_fmt_ci(r['mde_80pct'])}%"
            )

        print("\n--- Analysis B: Momentum decay curve ---", flush=True)
        b = analysis_b_momentum_decay(rows)
        print(f"  {b['caveat']}")
        for h in b["horizons"]:
            m = b["means"].get(h)
            n = b["ns"].get(h, 0)
            m_str = f"{m:+.2f}%" if m is not None else "N/A"
            print(f"  {h:4d}d  mean_fwd_ret={m_str}  n={n}")

        print("\n--- Analysis C: D2 confirm as-long readout ---", flush=True)
        c = analysis_c_d2_long(rows)
        for h in [21, 63, 126]:
            v = c.get(h)
            if v is None:
                print(f"  {h:3d}d  N/A")
            else:
                print(
                    f"  {h:3d}d  n_cohorts={v['n_cohorts']}  n_picks={v['n_picks']}  "
                    f"mean_excess={_fmt_ci(v['mean_excess'])}%  "
                    f"95%CI=[{_fmt_ci(v['ci_lo'])}, {_fmt_ci(v['ci_hi'])}]"
                )

        print("\n--- Analysis D: Top-1 concentration ---", flush=True)
        d = analysis_d_top1_concentration(rows)
        for r in d["rows"]:
            if r.get("status") != "ok":
                print(f"  {r['experiment']:30s} {r['arm']:8s}  {r['status']}")
            else:
                top1 = r["top1_mean_excess_63"]
                all_ = r["all_picks_mean_excess_63"]
                print(
                    f"  {r['experiment']:30s} {r['arm']:8s}  "
                    f"top1_excess_63={_fmt_ci(top1)}%  "
                    f"all_excess_63={_fmt_ci(all_) if all_ is not None else 'N/A'}%"
                )

    elif args.command == "summary":
        if not _OUTPUT_CSV.exists():
            print("outcome_table.csv not found — run 'build' first.", file=sys.stderr)
            sys.exit(1)

        rows = []
        with open(_OUTPUT_CSV, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Convert numeric columns
                for col in ["fwd_ret_21", "fwd_ret_63", "fwd_ret_126",
                            "excess_21", "excess_63", "excess_126", "composite_score"]:
                    val = row.get(col)
                    row[col] = float(val) if val not in (None, "", "None") else None
                rows.append(row)
        print_summary(rows)


if __name__ == "__main__":
    main()
