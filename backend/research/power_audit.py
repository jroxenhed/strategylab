"""Statistical power audit of StrategyLab experiment designs via synthetic edge injection.

F340 — answers: could the closed signal program (quarterly, ~40 decision points, 25-50
picks each) have detected a modest real edge?  How much better are denser designs?

METHOD
------
1.  Load explore-era (2015-01-01–2020-12-31) daily closes from the on-disk
    PriceFrameCache.  Tickers must pass a rough liquidity floor (price > $5 on
    at least half of explore-era trading days); up to MAX_TICKERS sampled.
2.  Build a (ticker × decision_date) panel of forward-63-trading-day returns
    once up front; all Monte Carlo reps index into it (the key runtime trick).
3.  Four experiment designs compared:
      (a) QUARTERLY-4:  Feb/May/Aug/Nov 15 each year   → 24 decision pts in 6 yrs
      (b) MONTHLY:      15th of each month              → 72 pts
      (c) EVENT-TIME-100: 100 random biz-days/yr       → 600 pts
      (d) EVENT-TIME-400: 400 random biz-days/yr       → 2400 pts
    Two pick-count conditions:
      FIXED-40: all designs use 40 picks/point (total picks ~960–96000)
      MATCHED:  (a)+(b) use 40; (c) uses 4; (d) uses 1  → comparable total
4.  Per rep: pick random tickers, add synthetic uplift E ppt, measure excess
    (mean pick return – date universe median), bootstrap p<0.05 test.
5.  Grid: E ∈ {0,1,2,3,5,10} × designs × ≥200 reps (500 if fast enough).

F338 smoke anchors (pre-stated, checked in main):
  (1) E=0 placebo rate ≈5% (2–10%) for every design
  (2) E=10 detection ≥95% for every design
  (3) power non-decreasing in E within each design
  (4) denser designs have equal-or-higher power at every E (±3 ppt tolerance)

Output: backend/data/turnaround/power_audit_result.json

Usage:
    python3 backend/research/power_audit.py [--reps 500] [--max-tickers 1500]
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import tempfile
import time
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Path setup — mirrors run_f370_explore.py:49-54 (TIMING-01)
# Without this, `python power_audit.py --workers N` fails with
# ModuleNotFoundError: No module named 'research' because the ProcessPool
# workers inherit sys.path from the spawned process, which does not
# automatically include backend/ or backend/research/.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
for _p in [str(_BACKEND_DIR), str(_SCRIPT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class PowerAuditResult(TypedDict, total=False):
    """Shape of the dict returned by run_audit() and consumed by check_f338_anchors()."""
    meta: dict[str, object]
    e_grid: list[float]
    designs: dict[str, str]
    design_dates_count: dict[str, int]
    design_picks: dict[str, int]
    power_table: dict[str, list[float]]
    mde_80pct: dict[str, float | None]
    f338_anchors: list[str]

# ---------------------------------------------------------------------------
# Paths (repo-relative; script can be run from anywhere)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_DIR = _REPO_ROOT / "backend" / "data" / "turnaround" / "price_cache" / "v1"
_OUTPUT_PATH = _REPO_ROOT / "backend" / "data" / "turnaround" / "power_audit_result.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EXPLORE_START = "2015-01-01"
EXPLORE_END = "2020-12-31"
CONFIRM_CUTOFF = pd.Timestamp("2021-01-01")  # never touch 2021+

FORWARD_DAYS = 63  # trading days

# Liquidity floor: ticker must have median close > $5 over explore era
PRICE_FLOOR = 5.0

MAX_TICKERS = 1500  # sample cap (full universe ~6663; doc if triggered)

# Decision date schedules (all within 2015–2020)
# Latest date where 63 forward trading days exist before explore_end
# ~2020-09-28 gives exactly 63 trading days by 2020-12-31 in normal markets
_LATEST_DECISION = pd.Timestamp("2020-09-30")


def _quarterly_dates() -> list[pd.Timestamp]:
    """Feb/May/Aug/Nov 15 each year, 2015–2020, capped at _LATEST_DECISION."""
    dates = []
    for y in range(2015, 2021):
        for m, d in [(2, 15), (5, 15), (8, 15), (11, 15)]:
            dt = pd.Timestamp(y, m, d)
            if dt <= _LATEST_DECISION:
                dates.append(dt)
    return dates


def _monthly_dates() -> list[pd.Timestamp]:
    """15th of each month, 2015–2020, capped at _LATEST_DECISION."""
    dates = []
    for y in range(2015, 2021):
        for m in range(1, 13):
            dt = pd.Timestamp(y, m, 15)
            if dt <= _LATEST_DECISION:
                dates.append(dt)
    return dates


def _event_time_dates(n_per_year: int, rng: np.random.Generator,
                      trading_days: list[pd.Timestamp]) -> list[pd.Timestamp]:
    """n_per_year random business days per year, 2015–2020, capped at _LATEST_DECISION."""
    eligible = [d for d in trading_days
                if pd.Timestamp("2015-01-01") <= d <= _LATEST_DECISION]
    # Group by year and sample n_per_year from each
    by_year: dict[int, list[pd.Timestamp]] = {}
    for d in eligible:
        by_year.setdefault(d.year, []).append(d)
    dates = []
    for y in sorted(by_year):
        pool = by_year[y]
        n = min(n_per_year, len(pool))
        chosen = rng.choice(len(pool), size=n, replace=False)
        dates.extend(sorted(pool[i] for i in chosen))
    return sorted(dates)


# ---------------------------------------------------------------------------
# Price cache loading
# ---------------------------------------------------------------------------
def _ticker_from_path(p: Path) -> str:
    """Extract ticker from cache filename {TICKER}_{crc}_{source}_{span}.pkl."""
    # Split from right to avoid tickers with underscores (rare but possible).
    # Format: parts = [*ticker_parts, crc8hex, source, startdate, enddate]
    stem = p.stem  # drop .pkl
    parts = stem.split("_")
    # crc is 8 hex chars; source is alpha; dates are 8 digits
    # Scan from right: enddate, startdate, source, crc, then ticker
    # At minimum 5 parts: TICKER, crc, source, start, end
    if len(parts) < 5:
        return parts[0]
    return "_".join(parts[:-4])


def load_panel(
    cache_dir: Path = _CACHE_DIR,
    max_tickers: int = MAX_TICKERS,
    price_floor: float = PRICE_FLOOR,
    verbose: bool = True,
) -> tuple[np.ndarray, list[str], list[pd.Timestamp]]:
    """Load price data and return (panel, tickers, decision_dates).

    panel shape: (n_tickers, n_decision_dates)
    Each cell: forward-63-trading-day return for that ticker on that date.
    NaN if data unavailable.

    Returns all decision dates across all designs merged (unique, sorted).
    """
    if verbose:
        print(f"Scanning cache dir: {cache_dir}")

    # Only use files covering explore era (20130601-20251231 span)
    all_files = sorted(cache_dir.glob("*yahoo_20130601_20251231.pkl"))
    if verbose:
        print(f"  Found {len(all_files)} candidate files")

    # Generate the superset of all decision dates we'll need.
    # Load a pilot file to get the trading-day grid — must cover explore era.
    pilot_trading_days: list[pd.Timestamp] = []
    for f in all_files:
        try:
            with open(f, "rb") as fh:
                df = pickle.load(fh)
            if df is None or df.empty:
                continue
            # Normalize index
            idx = df.index
            if hasattr(idx, "tz") and idx.tz is not None:
                idx = idx.tz_localize(None)
            pilot_dates_all = idx.normalize().unique()
            candidate_days = sorted(
                d for d in pilot_dates_all
                if pd.Timestamp("2015-01-01") <= d <= pd.Timestamp("2021-06-30")
            )
            if len(candidate_days) > 500:  # covers most of explore era
                pilot_trading_days = candidate_days
                break
        except (OSError, pickle.UnpicklingError, EOFError):
            pass

    if not pilot_trading_days:
        raise RuntimeError("Could not find a pilot file covering explore era — check cache_dir")

    trading_days_all = pilot_trading_days

    # Collect fixed decision dates (quarterly + monthly)
    fixed_dates = sorted(set(_quarterly_dates() + _monthly_dates()))

    # For event-time designs we'll need the trading_days list at MC time.
    # We include them in the panel but mark them separately.
    # Pre-compute panel for all trading days 2015-2020 (to support event-time)
    panel_dates = sorted(
        d for d in trading_days_all
        if pd.Timestamp("2015-01-01") <= d <= _LATEST_DECISION
    )

    if verbose:
        print(f"  Panel decision dates: {len(panel_dates)} "
              f"({panel_dates[0].date()} → {panel_dates[-1].date()})")

    # Load tickers (with liquidity filter)
    tickers: list[str] = []
    close_series: list[np.ndarray] = []  # shape (n_dates_panel,) per ticker

    # Build a date→index mapping
    date_to_idx: dict[pd.Timestamp, int] = {d: i for i, d in enumerate(panel_dates)}

    t0 = time.time()
    n_loaded = 0
    n_filtered = 0

    # Shuffle with a fixed seed so sampling is reproducible but not alphabetical.
    # Using Python random here (not numpy rng) — only affects which tickers are
    # included in the sample cap, not the MC draws.
    import random as _rnd
    _rnd.seed(1337)
    shuffled_files = list(all_files)
    _rnd.shuffle(shuffled_files)

    for f in shuffled_files:
        if len(tickers) >= max_tickers:
            break
        ticker = _ticker_from_path(f)
        try:
            with open(f, "rb") as fh:
                df = pickle.load(fh)
        except (OSError, pickle.UnpicklingError, EOFError):
            continue

        if df is None or df.empty or "Close" not in df.columns:
            continue

        # Normalize index
        didx = df.index
        if hasattr(didx, "tz") and didx.tz is not None:
            didx = didx.tz_localize(None)
        df = df.copy()
        df.index = didx.normalize()

        # Slice to explore era + forward window
        df_slice = df.loc["2015-01-01":"2021-06-30"]["Close"]
        if df_slice.empty:
            continue

        # Liquidity filter: median close > price_floor in explore era
        explore_slice = df_slice.loc["2015-01-01":"2020-12-31"]
        if explore_slice.empty or explore_slice.median() < price_floor:
            n_filtered += 1
            continue

        # Build close array indexed to panel_dates
        # We need: for each panel_date d, the close on d and 63 trading days later
        close_arr = np.full(len(panel_dates), np.nan)
        for i, d in enumerate(panel_dates):
            if d in df_slice.index:
                close_arr[i] = df_slice.loc[d]

        # Compute forward returns: fwd_ret[i] = close at (d + 63 biz days) / close[d] - 1
        # We'll build this from the close array using trading day offsets
        fwd_ret = np.full(len(panel_dates), np.nan)
        for i in range(len(panel_dates) - 1):
            c0 = close_arr[i]
            if np.isnan(c0) or c0 <= 0:
                continue
            # Find the close 63 trading days later
            j = i + FORWARD_DAYS
            if j < len(panel_dates):
                cj = close_arr[j]
                if not np.isnan(cj) and cj > 0:
                    fwd_ret[i] = (cj / c0) - 1.0

        tickers.append(ticker)
        close_series.append(fwd_ret)
        n_loaded += 1

    elapsed = time.time() - t0
    if verbose:
        print(f"  Loaded {n_loaded} tickers, filtered {n_filtered} (price<${price_floor})")
        print(f"  Loading took {elapsed:.1f}s")

    panel = np.vstack(close_series)  # (n_tickers, n_panel_dates)
    return panel, tickers, panel_dates


# ---------------------------------------------------------------------------
# Monte Carlo power estimation
# ---------------------------------------------------------------------------

def _bootstrap_pvalue(
    values: np.ndarray,
    n_boot: int = 499,
    rng: np.random.Generator | None = None,
) -> float:
    """One-sample bootstrap p-value for H0: mean=0 (two-sided), using
    the shifted bootstrap distribution.

    Parameters
    ----------
    values : array of per-date excess values.
    n_boot : number of bootstrap resamples.
    rng    : seeded Generator for reproducibility; if None a fresh entropy-sourced
             one is created (use only when reproducibility is not required).

    Returns p-value in [0,1].
    """
    if len(values) == 0:
        return 1.0
    if rng is None:
        rng = np.random.default_rng()
    obs_mean = np.mean(values)
    # Shift values to satisfy H0
    shifted = values - obs_mean
    n = len(values)
    boot_means = np.array([
        np.mean(rng.choice(shifted, size=n, replace=True))
        for _ in range(n_boot)
    ])
    p = np.mean(np.abs(boot_means) >= np.abs(obs_mean))
    return float(p)


def _nw_ttest_pvalue(x: np.ndarray, nw_lag: int) -> float:
    """Two-sided one-sample t-test on H0: mean(x)=0 with Newey-West HAC SE.

    Uses the Bartlett kernel: w_j = 1 - j/(L+1) for j=1..L.
    Returns p-value in [0,1].

    When nw_lag == 0 this reduces exactly to the standard t-test SE.

    Parameters
    ----------
    x      : 1-D array of observations (already sorted by date before calling).
    nw_lag : number of autocovariance lags to include (L in the Bartlett formula).
             Should be max(0, ceil(FORWARD_DAYS / median_gap_trading_days) - 1).
    """
    from scipy import stats as scipy_stats  # local import; available in venv

    n = len(x)
    if n < 2:
        return 1.0

    x_bar = np.mean(x)
    demeaned = x - x_bar

    # gamma_0: sample variance (unadjusted denominator n for consistency with HAC)
    gamma0 = float(np.dot(demeaned, demeaned)) / n

    # Accumulate Bartlett-weighted autocovariances
    omega = gamma0
    for j in range(1, nw_lag + 1):
        w_j = 1.0 - j / (nw_lag + 1)
        gamma_j = float(np.dot(demeaned[j:], demeaned[:-j])) / n
        omega += 2.0 * w_j * gamma_j

    # HAC variance of the sample mean: Var(x_bar) = omega / n
    # Clamp to avoid numeric issues when all excess values are identical
    var_mean = max(omega / n, 1e-30)
    se = float(np.sqrt(var_mean))

    t_stat = x_bar / se
    # Use t(n-1) as in the standard test (conservative but consistent)
    p = float(2.0 * scipy_stats.t.sf(abs(t_stat), df=n - 1))
    return p


def _compute_nw_lag(decision_dates: list[pd.Timestamp], forward_days: int = FORWARD_DAYS) -> int:
    """Compute the Newey-West lag for a given decision-date schedule.

    For a calendar with median gap G trading days between adjacent decision dates
    and a forward window of F trading days, consecutive excess values share
    (F - G) overlapping days.  The NW lag needed to cover one full window of
    overlap is:  L = max(0, ceil(F / G) - 1).

    Quarterly schedules have G ≈ 63 ≈ F, so L = 0 (no adjustment needed —
    quarterly windows don't overlap).  Dense event-time schedules (G < 1) get
    large L values.

    Decision dates are sorted chronologically before gap calculation.
    """
    if len(decision_dates) < 2:
        return 0
    sorted_dates = sorted(decision_dates)
    # Compute trading-day gaps between adjacent dates using calendar days as proxy.
    # We use calendar day gap / 7 * 5 to approximate trading-day gap, but since
    # both decision dates and forward windows are defined in trading days the
    # ratio F/G is what matters; calendar day approximation cancels out.
    gaps_td: list[float] = []
    for i in range(1, len(sorted_dates)):
        cal_gap = (sorted_dates[i] - sorted_dates[i - 1]).days
        # Convert calendar days → approximate trading days (252/365 ≈ 0.6897)
        td_gap = cal_gap * (252.0 / 365.0)
        gaps_td.append(td_gap)

    median_gap = float(np.median(gaps_td))
    if median_gap <= 0:
        return 0
    import math
    lag = max(0, math.ceil(forward_days / median_gap) - 1)
    return lag


def run_power_experiment(
    panel: np.ndarray,
    tickers: list[str],
    panel_dates: list[pd.Timestamp],
    decision_dates: list[pd.Timestamp],
    n_picks: int,
    uplift: float,
    n_reps: int,
    rng: np.random.Generator,
    use_ttest: bool = True,
    clip_lo: float = -0.99,
    clip_hi: float = 5.0,
) -> float:
    """Estimate detection rate for one (design, uplift, n_picks) cell.

    Returns fraction of reps where H0: mean excess = 0 is rejected at p<0.05.

    Design notes:
    - Returns are clipped at clip_lo/clip_hi before any statistics to prevent
      extreme outliers (rare penny-stock blowups and turnaround 100-baggers) from
      dominating variance.  Default clip [-99%, +500%] removes <0.1% of cells.
    - Reference is the universe MEAN computed over NON-PICK tickers (COR-02).
      Excluding picks from the baseline removes a small downward bias (~4% for
      n_picks=40, n_valid≈1000) and matches the real program's "every OTHER
      qualifying stock" definition.
    - Significance test uses a Newey-West HAC standard error (COR-01) with lag
      L = max(0, ceil(FORWARD_DAYS / median_gap_trading_days) - 1).  For
      quarterly designs L=0 so the test degenerates to the standard t-test.
      For dense designs (e.g. EVENT-TIME-400 with daily-or-sub-daily gaps) L is
      large and accounts for the heavy window overlap.
    """
    # Decision dates must be sorted chronologically for NW lag computation and
    # for the NW autocovariance accumulation to be meaningful.
    sorted_decision_dates = sorted(decision_dates)

    nw_lag = _compute_nw_lag(sorted_decision_dates, forward_days=FORWARD_DAYS)

    date_to_panel_idx = {d: i for i, d in enumerate(panel_dates)}

    # Map decision dates to panel indices (use closest available trading day)
    dec_indices: list[int] = []
    for dt in sorted_decision_dates:
        if dt in date_to_panel_idx:
            dec_indices.append(date_to_panel_idx[dt])
        else:
            # Find nearest trading day
            diffs = np.array([abs((dt - d).days) for d in panel_dates])
            nearest = int(np.argmin(diffs))
            dec_indices.append(nearest)

    n_dates = len(dec_indices)
    n_tickers = panel.shape[0]
    if n_dates == 0 or n_tickers == 0:
        return 0.0

    # Pre-clip the panel (vectorized, amortized over all reps and designs).
    panel_clipped = np.clip(panel, clip_lo, clip_hi)
    clip_hi_up = clip_hi + max(0.0, uplift / 100.0)  # allow room above clip_hi for uplift

    # Pre-compute per-date valid indices (universe means computed per rep to
    # exclude picks — COR-02).
    active: list[tuple[int, np.ndarray]] = []  # (panel_idx, valid_indices)
    for pidx in dec_indices:
        col = panel_clipped[:, pidx]
        valid_mask = ~np.isnan(col)
        n_valid = int(valid_mask.sum())
        if n_valid < n_picks + 5:
            continue
        valid_idx = np.where(valid_mask)[0]
        active.append((pidx, valid_idx))

    if len(active) < 3:
        return 0.0

    n_active = len(active)
    detections = 0

    for _ in range(n_reps):
        # For each active decision date: pick n_picks random tickers,
        # compute their forward returns + uplift, compute excess vs universe mean
        # of the NON-PICK complement (COR-02).
        excess = np.empty(n_active)
        for k, (pidx, valid_idx) in enumerate(active):
            col = panel_clipped[:, pidx]
            n_draw = min(n_picks, len(valid_idx))
            # relative indices into valid_idx (not absolute ticker indices)
            chosen_rel = rng.choice(len(valid_idx), size=n_draw, replace=False)
            chosen_abs = valid_idx[chosen_rel]

            # Universe mean: exclude the picks (COR-02 fix)
            non_pick_mask = np.ones(len(valid_idx), dtype=bool)
            non_pick_mask[chosen_rel] = False
            if non_pick_mask.any():
                universe_mean = float(np.mean(col[valid_idx[non_pick_mask]]))
            else:
                universe_mean = float(np.mean(col[valid_idx]))  # degenerate: all tickers picked

            # Add uplift; re-clip to keep values bounded
            pick_returns = np.clip(col[chosen_abs] + uplift / 100.0, clip_lo, clip_hi_up)
            excess[k] = float(np.mean(pick_returns)) - universe_mean

        # Test: mean excess != 0 at p < 0.05
        # use_ttest=True → Newey-West HAC t-test (accounts for window overlap)
        # use_ttest=False → iid shifted bootstrap (for robustness checks)
        if use_ttest:
            p = _nw_ttest_pvalue(excess, nw_lag)
            detected = p < 0.05
        else:
            detected = _bootstrap_pvalue(excess, rng=rng) < 0.05

        if detected:
            detections += 1

    return detections / n_reps


# ---------------------------------------------------------------------------
# Module-level picklable worker for parallel power-audit grid (F380c)
# ---------------------------------------------------------------------------

def _power_cell_worker(task: tuple, seed: int) -> tuple[str, float, float]:
    """Picklable worker: run one (design, E) cell of the power-audit grid.

    ``task`` is ``(design_name, decision_dates, n_picks, e_val, n_reps,
                   panel_path, panel_shape, panel_dtype, tickers, panel_dates)``.

    CONC-01: the panel numpy array (~16.8 MB) is stored on disk as a
    numpy memmap file (written once in the parent before task dispatch).
    Each worker loads it with np.load(mmap_mode='r') — disk I/O happens
    once per worker process, not once per task tuple.  Only small args
    (design name, dates, picks, E value, n_reps, file path, tickers,
    dates) cross the IPC pipe, cutting per-task pickle cost from ~16.8 MB
    to <100 KB and total IPC overhead from ~806 MB to ~5 MB for 48 cells.

    ``seed`` is a deterministic per-task seed from parallel_map.

    Returns ``(design_name, e_val, rate)`` for reassembly.

    Module-level (pickle-safe on macOS spawn).
    """
    (design_name, decision_dates, n_picks, e_val, n_reps,
     panel_path, tickers, panel_dates) = task
    # Load panel from disk (read-only mmap; no copy unless written to)
    panel = np.load(panel_path, mmap_mode="r")
    rng = np.random.default_rng(seed)
    rate = run_power_experiment(
        panel=panel,
        tickers=tickers,
        panel_dates=panel_dates,
        decision_dates=decision_dates,
        n_picks=n_picks,
        uplift=e_val,
        n_reps=n_reps,
        rng=rng,
        use_ttest=True,
    )
    return design_name, e_val, round(rate, 4)


# ---------------------------------------------------------------------------
# Design definitions
# ---------------------------------------------------------------------------

def get_designs(
    panel_dates: list[pd.Timestamp],
    rng: np.random.Generator,
) -> dict[str, tuple[list[pd.Timestamp], int, str]]:
    """Return dict: design_name → (decision_dates, n_picks, description).

    Two pick-count conditions:
    - MATCHED:  (a)+(b) 40 picks; (c) 4 picks; (d) 1 pick  → comparable totals
    - FIXED-40: all designs 40 picks
    """
    q_dates = _quarterly_dates()
    m_dates = _monthly_dates()
    et100_dates = _event_time_dates(100, rng, panel_dates)
    et400_dates = _event_time_dates(400, rng, panel_dates)

    designs: dict[str, tuple[list[pd.Timestamp], int, str]] = {
        # MATCHED condition
        "QUARTERLY-4_matched": (
            q_dates, 40,
            f"Quarterly (Feb/May/Aug/Nov 15) | {len(q_dates)} dates | 40 picks | matched"
        ),
        "MONTHLY_matched": (
            m_dates, 40,
            f"Monthly (15th) | {len(m_dates)} dates | 40 picks | matched"
        ),
        "EVENT-TIME-100_matched": (
            et100_dates, 4,
            f"Event-time 100/yr | {len(et100_dates)} dates | 4 picks | matched"
        ),
        "EVENT-TIME-400_matched": (
            et400_dates, 1,
            f"Event-time 400/yr | {len(et400_dates)} dates | 1 pick | matched"
        ),
        # FIXED-40 condition (isolates clock vs. sample size)
        "QUARTERLY-4_fixed40": (
            q_dates, 40,
            f"Quarterly | {len(q_dates)} dates | 40 picks | fixed-40"
        ),
        "MONTHLY_fixed40": (
            m_dates, 40,
            f"Monthly | {len(m_dates)} dates | 40 picks | fixed-40"
        ),
        "EVENT-TIME-100_fixed40": (
            et100_dates, 40,
            f"Event-time 100/yr | {len(et100_dates)} dates | 40 picks | fixed-40"
        ),
        "EVENT-TIME-400_fixed40": (
            et400_dates, 40,
            f"Event-time 400/yr | {len(et400_dates)} dates | 40 picks | fixed-40"
        ),
    }
    return designs


# ---------------------------------------------------------------------------
# F338 smoke gate anchors
# ---------------------------------------------------------------------------

def check_f338_anchors(results: PowerAuditResult) -> list[str]:
    """Check pre-stated smoke gate anchors. Returns list of PASS/FAIL strings."""
    from scipy import stats as scipy_stats  # local import; available in venv

    findings = []

    e_grid = results["e_grid"]
    design_names = list(results["power_table"].keys())
    n_reps: int = results.get("meta", {}).get("n_reps", 500)  # type: ignore[union-attr]

    e0_idx = e_grid.index(0)
    e10_idx = e_grid.index(10) if 10 in e_grid else None

    # Anchor 1: E=0 placebo rate within the exact binomial 99% CI around 0.05
    # (COR-04: replaces the hand-widened tol_e0 = 0.06 with a principled bound).
    # With n_reps draws at true FPR=0.05, the 99% Clopper-Pearson interval gives
    # the range of observed rates we should accept.
    binom_lo = float(scipy_stats.binom.ppf(0.005, n_reps, 0.05)) / n_reps
    binom_hi = float(scipy_stats.binom.ppf(0.995, n_reps, 0.05)) / n_reps
    all_a1_pass = True
    for dname in design_names:
        rate = results["power_table"][dname][e0_idx]
        ok = binom_lo <= rate <= binom_hi
        if not ok:
            all_a1_pass = False
            findings.append(
                f"[FAIL] Anchor 1 – E=0 placebo | {dname}: {rate:.3f} "
                f"(expected {binom_lo:.3f}–{binom_hi:.3f} per binomial 99% CI, n={n_reps})"
            )
    if all_a1_pass:
        findings.append(
            f"[PASS] Anchor 1 – E=0 placebo within binomial 99% CI "
            f"[{binom_lo:.3f},{binom_hi:.3f}] for all designs (n_reps={n_reps})"
        )

    # Anchor 2: E=10 detection ≥95% for every design
    if e10_idx is not None:
        all_a2_pass = True
        for dname in design_names:
            rate = results["power_table"][dname][e10_idx]
            ok = rate >= 0.95
            if not ok:
                all_a2_pass = False
                findings.append(
                    f"[FAIL] Anchor 2 – E=10 detection | {dname}: {rate:.3f} (expected ≥0.95)"
                )
        if all_a2_pass:
            findings.append("[PASS] Anchor 2 – E=10 detection ≥95% for all designs")
    else:
        findings.append("[SKIP] Anchor 2 – E=10 not in grid")

    # Anchor 3: power non-decreasing in E within each design
    all_a3_pass = True
    for dname in design_names:
        rates = results["power_table"][dname]
        for i in range(1, len(rates)):
            if rates[i] < rates[i - 1] - 0.03:  # allow 3ppt noise
                all_a3_pass = False
                findings.append(
                    f"[FAIL] Anchor 3 – non-monotone | {dname}: "
                    f"E={e_grid[i-1]}→E={e_grid[i]}: {rates[i-1]:.3f}→{rates[i]:.3f}"
                )
    if all_a3_pass:
        findings.append("[PASS] Anchor 3 – power non-decreasing in E for all designs")

    # Anchor 4: denser designs have equal-or-higher power at every E (±3 ppt).
    # Applies to the FIXED-40 condition ONLY — in this condition picks are held
    # constant so more decision points unambiguously add information.
    #
    # The MATCHED condition is intentionally excluded: halving picks from 40→4
    # to compensate for more dates trades per-date precision for date count; the
    # t-statistic scales as sqrt(n_dates × picks), so reducing picks from 40→4
    # requires 100× more dates to compensate for 10× fewer picks — the matched
    # design does not achieve this and MONTHLY can outperform ET100-matched.
    # This is a finding, not a bug; see power_report.md for analysis.
    density_order_fixed = [
        "QUARTERLY-4_fixed40",
        "MONTHLY_fixed40",
        "EVENT-TIME-100_fixed40",
        "EVENT-TIME-400_fixed40",
    ]
    tol = 0.03
    # At E=0, use the same principled binomial band as Anchor 1 to set tolerance:
    # two designs are "equal" at E=0 if the difference is within sampling noise
    # at nominal alpha=5%, i.e. within (binom_hi - binom_lo) / 2 of each other.
    # We cap at tol to avoid being looser than the non-E0 tolerance in thin designs.
    tol_e0 = min(tol, (binom_hi - binom_lo) / 2.0)
    all_a4_pass = True
    for i in range(len(density_order_fixed) - 1):
        d_less = density_order_fixed[i]
        d_more = density_order_fixed[i + 1]
        if d_less not in results["power_table"] or d_more not in results["power_table"]:
            continue
        for e_i, e_val in enumerate(e_grid):
            r_less = results["power_table"][d_less][e_i]
            r_more = results["power_table"][d_more][e_i]
            this_tol = tol_e0 if e_val == 0 else tol
            if r_more < r_less - this_tol:
                all_a4_pass = False
                findings.append(
                    f"[FAIL] Anchor 4 – density ordering (fixed-40) | E={e_val}: "
                    f"{d_less}({r_less:.3f}) > {d_more}({r_more:.3f}) by >{this_tol}"
                )
    if all_a4_pass:
        findings.append("[PASS] Anchor 4 – denser designs have equal-or-higher power (±3ppt) "
                        "in fixed-40 condition")
    # Note matched-condition behavior (informational, not a PASS/FAIL anchor)
    findings.append(
        "[NOTE] Anchor 4 – matched condition excluded: MONTHLY_matched can outperform "
        "EVENT-TIME-100_matched because sqrt(n_dates × picks) is larger for MONTHLY "
        "(52.5 vs 49.0); this is the expected result, not a bug — the comparison isolates "
        "the clock only when picks are fixed."
    )

    return findings


# ---------------------------------------------------------------------------
# Minimum detectable edge interpolation
# ---------------------------------------------------------------------------

def min_detectable_edge(e_grid: list[float], power_values: list[float],
                         threshold: float = 0.80) -> float | None:
    """Interpolate to find minimum E at which power first reaches `threshold`.

    Returns None if power never reaches threshold in the grid.
    """
    for i in range(len(e_grid) - 1):
        p0, p1 = power_values[i], power_values[i + 1]
        e0, e1 = e_grid[i], e_grid[i + 1]
        if p0 <= threshold <= p1:
            # Linear interpolation
            if p1 == p0:
                return float(e0)
            frac = (threshold - p0) / (p1 - p0)
            return round(float(e0 + frac * (e1 - e0)), 2)
        if p0 >= threshold:
            return float(e0)
    if power_values[-1] >= threshold:
        return float(e_grid[-1])
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_audit(
    n_reps: int = 500,
    max_tickers: int = MAX_TICKERS,
    e_grid: list[float] | None = None,
    verbose: bool = True,
    seed: int = 42,
    workers: int = 1,
) -> PowerAuditResult:
    """Run the full power audit and return results dict.

    Parameters
    ----------
    workers : int
        Number of parallel ProcessPool workers for the design×E grid.
        Use 1 (default) for the deterministic serial path.
        F380(c): set to os.cpu_count() for maximum throughput.
        Each (design, E) cell receives a deterministic per-task seed
        derived from ``seed ^ task_index``, preserving byte-identical
        results relative to the serial path (same seed, just reordered).
    """
    if e_grid is None:
        e_grid = [0, 1, 2, 3, 5, 10]

    rng = np.random.default_rng(seed)

    if verbose:
        print("=" * 60)
        print("F340 — Statistical Power Audit")
        print("=" * 60)
        print(f"n_reps={n_reps}, max_tickers={max_tickers}, E_grid={e_grid}")
        print()

    # Step 1: Load panel
    if verbose:
        print("Step 1: Loading price panel …")
    t0 = time.time()
    panel, tickers, panel_dates = load_panel(
        cache_dir=_CACHE_DIR,
        max_tickers=max_tickers,
        price_floor=PRICE_FLOOR,
        verbose=verbose,
    )
    if verbose:
        print(f"  Panel shape: {panel.shape}  ({len(tickers)} tickers × {len(panel_dates)} dates)")
        print(f"  Non-NaN cells: {(~np.isnan(panel)).sum():,} / {panel.size:,} "
              f"({100*(~np.isnan(panel)).mean():.1f}%)")
        print(f"  Load time: {time.time()-t0:.1f}s")
        print()

    # Step 2: Build designs
    if verbose:
        print("Step 2: Building designs …")
    designs = get_designs(panel_dates, rng)
    for name, (dates, picks, desc) in designs.items():
        if verbose:
            total_picks = len(dates) * picks
            print(f"  {name}: {len(dates)} dates × {picks} picks = {total_picks} total picks")

    if verbose:
        print()
        print("Step 3: Monte Carlo grid …")
        print(f"  {len(designs)} designs × {len(e_grid)} E values × {n_reps} reps "
              f"= {len(designs)*len(e_grid)*n_reps:,} total experiments")
        print()

    # Step 3: Run grid
    # F380(c): Optional parallel dispatch of the (design × E) grid.
    # DETERMINISM NOTE: the serial path uses a single shared `rng` that is
    # consumed sequentially across all cells.  The parallel path uses
    # independent per-cell seeds (seed ^ task_index via parallel_map).
    # Parallel results are reproducible (same seed → same output every run)
    # but not byte-identical to the serial path because each cell's RNG
    # stream starts from a different state.  workers=1 always gives the
    # original serial byte-identical output.
    power_table: dict[str, list[float]] = {name: [] for name in designs}

    _design_names_ordered = list(designs.keys())

    t_grid = time.time()
    if workers > 1:
        # CONC-01: write the panel to a temp .npy file once; workers load it
        # from disk via np.load(mmap_mode='r').  This replaces embedding the
        # full ~16.8 MB array in every task tuple (~806 MB total IPC → ~5 MB).
        import atexit
        _panel_tmp = tempfile.NamedTemporaryFile(
            suffix=".npy", delete=False, dir=str(_REPO_ROOT / "backend" / "data"),
        )
        _panel_tmp_path = _panel_tmp.name
        _panel_tmp.close()
        np.save(_panel_tmp_path, panel)
        # Register cleanup even if an exception aborts the run
        atexit.register(lambda p=_panel_tmp_path: os.unlink(p) if os.path.exists(p) else None)

        # Build flat task list: (design_name, dates, picks, e_val, n_reps,
        #                        panel_path, tickers, panel_dates)
        # Order: e_val outer, design inner (matches serial print ordering)
        from research.parallel_map import parallel_map as _parallel_map
        _grid_tasks = []
        _task_key: list[tuple[str, float]] = []  # (design_name, e_val) per task
        for e_val in e_grid:
            for name, (dates, picks, _desc) in designs.items():
                _grid_tasks.append((name, dates, picks, e_val, n_reps,
                                    _panel_tmp_path, tickers, panel_dates))
                _task_key.append((name, e_val))

        n_cells = len(_grid_tasks)
        if verbose:
            print(f"  F380(c): {n_cells} cells, {min(workers, n_cells)} workers ...")

        _results_flat = _parallel_map(
            _power_cell_worker, _grid_tasks,
            workers=min(workers, n_cells),
            seed_base=seed,
        )

        # Clean up temp panel file immediately after workers are done
        try:
            os.unlink(_panel_tmp_path)
        except OSError:
            pass
        # Reassemble into power_table[design][e_idx]
        # Pre-fill with zeros
        for name in _design_names_ordered:
            power_table[name] = [0.0] * len(e_grid)
        e_val_to_idx = {e: i for i, e in enumerate(e_grid)}
        for (d_name, e_val_raw, rate) in _results_flat:
            power_table[d_name][e_val_to_idx[e_val_raw]] = rate

        if verbose:
            print(f"  Grid done in {time.time()-t_grid:.1f}s (parallel, {workers} workers)")
            print()
            # Print grid summary
            for e_i, e_val in enumerate(e_grid):
                row_str = "  ".join(f"{power_table[n][e_i]:.3f}" for n in _design_names_ordered)
                print(f"  E={e_val:4.1f}%  {row_str}")
    else:
        for e_val in e_grid:
            t_e = time.time()
            if verbose:
                print(f"  E={e_val:4.1f}%  ", end="", flush=True)
            for name, (dates, picks, desc) in designs.items():
                rate = run_power_experiment(
                    panel=panel,
                    tickers=tickers,
                    panel_dates=panel_dates,
                    decision_dates=dates,
                    n_picks=picks,
                    uplift=e_val,
                    n_reps=n_reps,
                    rng=rng,
                    use_ttest=True,
                )
                power_table[name].append(round(rate, 4))
            if verbose:
                row = "  ".join(f"{power_table[n][-1]:.3f}" for n in designs)
                print(f"[{time.time()-t_e:.1f}s]  {row}")

    # Step 4: Compute minimum detectable edges at 80% power
    mde80: dict[str, float | None] = {}
    for name in designs:
        mde80[name] = min_detectable_edge(list(e_grid), power_table[name], 0.80)

    # Step 5: Panel statistics for reporting
    n_valid_per_date = (~np.isnan(panel)).sum(axis=0)

    results = {
        "meta": {
            "generated_at": pd.Timestamp.now().isoformat(),
            "n_reps": n_reps,
            "n_tickers": len(tickers),
            "panel_dates": len(panel_dates),
            "explore_era": f"{EXPLORE_START} to {EXPLORE_END}",
            "forward_days": FORWARD_DAYS,
            "price_floor": PRICE_FLOOR,
            "max_tickers": max_tickers,
            "seed": seed,
            "panel_fill_pct": round(float(100 * (~np.isnan(panel)).mean()), 2),
            "median_valid_tickers_per_date": int(np.median(n_valid_per_date)),
        },
        "e_grid": list(e_grid),
        "designs": {name: desc for name, (_, _, desc) in designs.items()},
        "design_dates_count": {name: len(dates) for name, (dates, _, _) in designs.items()},
        "design_picks": {name: picks for name, (_, picks, _) in designs.items()},
        "power_table": power_table,
        "mde_80pct": mde80,
    }

    # F338 anchors
    if verbose:
        print()
        print("Step 4: F338 smoke gate anchors …")
    anchor_results = check_f338_anchors(results)
    results["f338_anchors"] = anchor_results
    if verbose:
        for line in anchor_results:
            print(f"  {line}")

    return results


def _atomic_write_json(path: Path, obj: object) -> None:
    content = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)
    dir_ = str(path.parent)
    fd = tempfile.NamedTemporaryFile(
        mode="w", delete=False, dir=dir_, suffix=".tmp", encoding="utf-8"
    )
    try:
        fd.write(content)
        fd.flush()
        os.fsync(fd.fileno())
        try:
            fd.close()
        except Exception:
            pass
        os.replace(fd.name, str(path))
    except Exception:
        try:
            fd.close()
        except Exception:
            pass
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="F340 Statistical Power Audit")
    parser.add_argument("--reps", type=int, default=500, help="Monte Carlo reps per cell")
    parser.add_argument("--max-tickers", type=int, default=MAX_TICKERS,
                        help="Max tickers to load (default 1500)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    parser.add_argument("--output", type=str, default=str(_OUTPUT_PATH),
                        help="Output JSON path")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    parser.add_argument(
        "--workers", type=int, default=1,
        help=(
            "Number of parallel workers for the design×E grid (default 1 = serial). "
            "F380(c): set to os.cpu_count() for maximum throughput. "
            "NOTE: parallel path uses per-cell seeds; results differ from serial."
        ),
    )
    args = parser.parse_args()

    results = run_audit(
        n_reps=args.reps,
        max_tickers=args.max_tickers,
        verbose=not args.quiet,
        seed=args.seed,
        workers=args.workers,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(output_path, results)

    print(f"\nResults written to {output_path}")
    print("\nMinimum detectable edge at 80% power (ppt):")
    for name, mde in results["mde_80pct"].items():
        val = f"{mde:.1f}%" if mde is not None else "never reached"
        print(f"  {name}: {val}")

    anchor_pass = all("[PASS]" in s or "[SKIP]" in s or "[NOTE]" in s for s in results["f338_anchors"])
    print(f"\nF338 anchors: {'ALL PASS' if anchor_pass else 'FAILURES FOUND — see output'}")
    if not anchor_pass:
        for s in results["f338_anchors"]:
            if "[FAIL]" in s:
                print(f"  {s}")
        sys.exit(1)


if __name__ == "__main__":
    main()
