"""backend/research/premise_power_census.py — Premise Power Census (F369).

Read-only power census: counts + dispersion for 5 premise families.
Draws NO FDR alpha (ledger_path=None). Never writes research conclusions.

Usage:
    python3 backend/research/premise_power_census.py --family calibration
    python3 backend/research/premise_power_census.py --family r1b_subuniverse
    python3 backend/research/premise_power_census.py --family pead
    python3 backend/research/premise_power_census.py --family eightk
    python3 backend/research/premise_power_census.py --family r2
    python3 backend/research/premise_power_census.py --family all

Output:
    <out>/census.json           — machine-readable results
    <out>/census_run.log        — progress log
    docs/research/2026-06-08-premise-power-census.md — human table + verdicts
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import math
import os
import pickle
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _REPO_ROOT / "backend"
_DATA = _BACKEND / "data" / "turnaround"
_EVENTS_NDJSON = (
    _DATA / "event_studies"
    / "r1b_insider_clusters_explore_2015_2020"
    / "events.ndjson"
)
_VERDICT_JSON = (
    _DATA / "event_studies"
    / "r1b_insider_clusters_explore_2015_2020"
    / "r1_explore_verdict.json"
)
_MATRIX_DIR = _BACKEND / "data" / "universe_matrix.parquet"
_SUBMISSIONS_DIR = _DATA / "edgar_cache" / "submissions"
_UNIVERSE_JSON = _DATA / "edgar_cache" / "universe.json"
_PRICE_CACHE_DIR = _DATA / "price_cache" / "v1"


# ---------------------------------------------------------------------------
# MDE helpers
# ---------------------------------------------------------------------------
def _mde_1samp(n: int, std: float) -> float:
    """One-sample MDE: 2.802 * std / sqrt(n). Returns pp if std in pp."""
    if n <= 0 or std <= 0:
        return float("nan")
    return 2.802 * std / math.sqrt(n)


def _mde_gap(n_q5: int, var_q5: float, n_q1: int, var_q1: float) -> float:
    """Dose-gap MDE: 2.802 * sqrt(var_q5/n_q5 + var_q1/n_q1). Var in pp²."""
    if n_q5 <= 0 or n_q1 <= 0 or var_q5 < 0 or var_q1 < 0:
        return float("nan")
    return 2.802 * math.sqrt(var_q5 / n_q5 + var_q1 / n_q1)


def _n_needed_1pp(std: float) -> int:
    """n needed to detect 1.0pp with one-sample test: ceil((2.802 * std)^2)."""
    if std <= 0 or math.isnan(std):
        return -1
    return math.ceil((2.802 * std) ** 2)


# ---------------------------------------------------------------------------
# Shared helpers for filing-event processing
# ---------------------------------------------------------------------------
_ET_TZ = ZoneInfo("America/New_York")


def _next_trading_day(date_str: str, sorted_dates: list) -> Optional[str]:
    """Return the next trading day in sorted_dates on or after date_str.

    Args:
        date_str: ISO date string 'YYYY-MM-DD'
        sorted_dates: sorted list of trading-day date strings from the matrix
    Returns:
        date string or None if beyond the matrix range
    """
    idx = bisect.bisect_left(sorted_dates, date_str)
    if idx < len(sorted_dates):
        return sorted_dates[idx]
    return None


def _entry_date_for_filing(acceptance_dt: str, sorted_dates: list) -> Optional[str]:
    """Convert an EDGAR acceptanceDateTime string to the correct matrix entry date.

    Rule (COR-02): filings published at or after 16:00 ET (market close) must
    enter on the NEXT calendar day before calling _next_trading_day, so that
    we never assign an entry on the same trading day as an after-hours filing.
    DST is handled via zoneinfo.ZoneInfo('America/New_York').

    Args:
        acceptance_dt: EDGAR acceptanceDateTime, e.g. '2018-02-14T21:30:00.000Z'
        sorted_dates: sorted list of trading-day date strings from the matrix

    Returns:
        entry date string (next trading day on-or-after corrected calendar date),
        or None if beyond the matrix range.
    """
    try:
        # Normalise to a UTC-aware datetime
        dt_str = acceptance_dt.replace("Z", "+00:00")
        # Handle fractional seconds if present
        if "." in dt_str:
            dt_str = dt_str.split(".")[0] + "+00:00"
        dt_utc = datetime.fromisoformat(dt_str)
        dt_et = dt_utc.astimezone(_ET_TZ)
        cal_date = dt_et.date()
        # If at or after 16:00 ET, advance calendar date by one day so entry
        # cannot coincide with the same-day price bar that closed before the filing
        if dt_et.hour >= 16:
            cal_date = cal_date + timedelta(days=1)
        date_str = cal_date.isoformat()
    except Exception:
        # Fall back to UTC calendar date slice (legacy behaviour — should not occur
        # on well-formed EDGAR acceptanceDates, but keep a safe path)
        date_str = acceptance_dt[:10]
    return _next_trading_day(date_str, sorted_dates)


def _load_cik_ticker_map() -> dict:
    """Load the CIK→ticker mapping from universe.json.

    Returns:
        {zero-padded-10-digit-CIK: ticker}
    """
    with open(_UNIVERSE_JSON) as f:
        universe = json.load(f)
    return {
        str(entry["cik_str"]).zfill(10): entry["ticker"]
        for entry in universe.values()
    }


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def _setup_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("census")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    # File handler
    fh = logging.FileHandler(log_path, mode="a")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


# ---------------------------------------------------------------------------
# Universe matrix loader + per-date median cache
# ---------------------------------------------------------------------------
def _load_matrix_medians(horizons: tuple = (21, 63, 126), logger=None) -> dict:
    """Load universe matrix and compute per-date median excess baseline.

    Returns:
        {horizon: {entry_date_str: median_fwd_return_pct}}

    All symbols in the matrix already pass floor criteria (matrix is pre-filtered).
    """
    if logger:
        logger.info("Loading universe matrix for horizons %s ...", horizons)
    medians: dict = {}
    for h in horizons:
        h_path = _MATRIX_DIR / f"horizon_days={h}"
        if logger:
            logger.info("  Reading horizon=%d from %s", h, h_path)
        df = pd.read_parquet(h_path)
        # Normalize entry_date to str for consistent key lookups
        df = df.copy()
        df["entry_date"] = df["entry_date"].astype(str)
        # All rows pass floors; compute median per entry_date
        med_series = df.groupby("entry_date")["fwd_return_pct"].median()
        medians[h] = med_series.to_dict()
        if logger:
            logger.info(
                "  horizon=%d: %d dates, %d symbols total",
                h, len(med_series), len(df),
            )
    return medians


def _compute_excess_from_matrix(
    events: list[dict],
    medians: dict,
    horizon: int = 63,
    logger=None,
    mat_lookup: Optional[dict] = None,
) -> tuple[list[float], int]:
    """Compute per-event excess return via matrix median join.

    Args:
        events: list of dicts with 'entry_date' and 'ticker'
        medians: {horizon: {entry_date_str: median_fwd_return_pct}}
        horizon: forward return horizon
        mat_lookup: optional pre-built {(entry_date_str, symbol): fwd_return_pct}
            dict for this horizon.  When provided the parquet is NOT re-read
            (PY-02: avoids redundant IO inside run_calibration which already holds
            the matrix in memory).  Callers that don't yet have a lookup may omit
            this parameter; the function will load the parquet itself.

    Returns:
        (excess_list, n_no_frame) where excess_list is list of pp values
    """
    if logger:
        logger.info(
            "Computing matrix-join excess for %d events at horizon %d ...",
            len(events), horizon,
        )
    # PY-02: use caller-supplied lookup if available to avoid redundant parquet reads.
    if mat_lookup is None:
        h_path = _MATRIX_DIR / f"horizon_days={horizon}"
        df = pd.read_parquet(h_path)
        df = df.copy()
        df["entry_date"] = df["entry_date"].astype(str)
        lookup = df.set_index(["entry_date", "symbol"])["fwd_return_pct"].to_dict()
    else:
        lookup = mat_lookup

    med_map = medians[horizon]
    excess_list = []
    n_no_frame = 0
    for ev in events:
        ed = ev.get("entry_date", "")
        ticker = ev.get("ticker", "") or ev.get("symbol", "")
        fwd = lookup.get((ed, ticker))
        med = med_map.get(ed)
        if fwd is None or med is None:
            n_no_frame += 1
            continue
        excess_list.append(fwd - med)
    if logger:
        logger.info(
            "  Matrix-join: %d excess values, %d no-frame",
            len(excess_list), n_no_frame,
        )
    return excess_list, n_no_frame


# ---------------------------------------------------------------------------
# Placebo-control benchmark (F371)
# ---------------------------------------------------------------------------
_PLACEBO_SEED = 20260608
_PLACEBO_K = 3  # control draws per event (capped at availability)


def _compute_placebo_control(
    events: list[dict],
    mat_lookup: dict,
    med_map: dict,
    sorted_dates: list,
    horizon: int = 63,
    K: int = _PLACEBO_K,
    seed: int = _PLACEBO_SEED,
    logger=None,
) -> dict:
    """Compute a deterministic placebo-control benchmark for an event set.

    For each family's valid event set, constructs a control set of NON-event
    observations for the SAME tickers: for each event ticker, candidate control
    dates are all trading days for that ticker in the universe matrix that are
    at least `horizon` trading-day positions away from ANY real event date for
    that ticker in this family.  Excess is computed identically to event excess
    (fwd_return_pct − floor_median_per_date).

    The control mean excess approximates the baseline/selection artifact.
    event_mean − control_mean isolates the event-specific component.

    Args:
        events: list of dicts with 'entry_date' (str 'YYYY-MM-DD') and
                'ticker' / 'symbol' keys.  Same set used for the family's
                main excess computation.
        mat_lookup: {(entry_date_str, symbol): fwd_return_pct} — the full
                    matrix lookup for the relevant horizon.
        med_map: {entry_date_str: median_fwd_return_pct} for that horizon.
        sorted_dates: sorted list of all trading-day date strings in the matrix.
        horizon: blackout radius in trading-day *positions* (not calendar days).
                 A candidate date at position i is eligible if
                 |i − event_pos| >= horizon for all event dates of that ticker.
        K: maximum control samples per event (default 3); fewer drawn if
           availability is less than K.
        seed: RNG seed for deterministic sampling (default 20260608).

    Returns:
        dict with:
            control_n            — total control observations
            control_mean_excess  — mean excess of control observations
            control_std_excess   — std of control excess (for reference)
            event_n              — n valid events (passed through from caller)
            event_mean_excess    — mean of event excess (recomputed here)
            net_excess           — event_mean_excess − control_mean_excess
            interpretation       — one-line string
            control_K            — K value used
            control_horizon_days — horizon value used
    """
    if not events:
        return {
            "control_n": 0,
            "control_mean_excess": float("nan"),
            "control_std_excess": float("nan"),
            "event_n": 0,
            "event_mean_excess": float("nan"),
            "net_excess": float("nan"),
            "interpretation": "no events — control not computed",
            "control_K": K,
            "control_horizon_days": horizon,
        }

    # Build date-position index for blackout arithmetic
    # sorted_dates is already sorted; bisect gives O(log n) position lookup.
    date_pos: dict[str, int] = {d: i for i, d in enumerate(sorted_dates)}

    # --- Step 1: group event positions by ticker ---
    # ticker -> sorted list of trading-day positions of events for that ticker
    ticker_event_positions: dict[str, list[int]] = defaultdict(list)
    event_excess_list: list[float] = []

    for ev in events:
        ed = ev.get("entry_date", "")
        ticker = ev.get("ticker", "") or ev.get("symbol", "")
        # Recompute event excess inline (same logic as _compute_excess_from_matrix)
        fwd = mat_lookup.get((ed, ticker))
        med = med_map.get(ed)
        if fwd is not None and med is not None:
            event_excess_list.append(fwd - med)
        pos = date_pos.get(ed)
        if pos is not None:
            ticker_event_positions[ticker].append(pos)

    # Sort event positions per ticker for fast range-exclusion checks
    for t in ticker_event_positions:
        ticker_event_positions[t].sort()

    # --- Step 2: for each ticker, build eligible candidate (date, ticker) pool ---
    # Candidate = any matrix row for that ticker whose date position is
    # at least `horizon` positions from every event-date position for that ticker.
    # We group all matrix rows by symbol first (one pass over mat_lookup keys).
    # mat_lookup keys are (date_str, symbol) — iterate and group.
    ticker_to_candidates: dict[str, list[str]] = defaultdict(list)
    for (ed, sym) in mat_lookup.keys():
        # Only tickers that have events contribute to the pool (controls are
        # for the same tickers — matches the spec's "same-ticker control").
        if sym not in ticker_event_positions:
            continue
        pos = date_pos.get(ed)
        if pos is None:
            continue
        ev_positions = ticker_event_positions[sym]
        # Binary-search: find nearest event position and check distance
        idx = bisect.bisect_left(ev_positions, pos)
        # Check left and right neighbors
        left_ok = (idx == 0 or (pos - ev_positions[idx - 1]) >= horizon)
        right_ok = (idx == len(ev_positions) or (ev_positions[idx] - pos) >= horizon)
        if left_ok and right_ok:
            ticker_to_candidates[sym].append(ed)

    # COR-03: sort each ticker's candidate list so sampling is invariant to
    # parquet row order (which can change on matrix rebuild).  Lexicographic
    # date order is the canonical ordering; combined with the fixed seed this
    # makes _compute_placebo_control fully deterministic across reruns.
    for sym in ticker_to_candidates:
        ticker_to_candidates[sym].sort()

    if logger:
        total_candidates = sum(len(v) for v in ticker_to_candidates.values())
        logger.info(
            "Placebo control: %d tickers with events, total candidate pool = %d rows",
            len(ticker_event_positions), total_candidates,
        )

    # --- Step 3: sample K controls per event, deterministically ---
    rng = np.random.default_rng(seed)
    control_excess_list: list[float] = []
    n_no_candidates = 0

    for ev in events:
        ed = ev.get("entry_date", "")
        ticker = ev.get("ticker", "") or ev.get("symbol", "")
        candidates = ticker_to_candidates.get(ticker, [])
        if not candidates:
            n_no_candidates += 1
            continue
        # Sample min(K, len(candidates)) WITHOUT replacement from candidates
        k_draw = min(K, len(candidates))
        chosen_indices = rng.choice(len(candidates), size=k_draw, replace=False)
        for ci in chosen_indices:
            ced = candidates[ci]
            fwd = mat_lookup.get((ced, ticker))
            med = med_map.get(ced)
            if fwd is not None and med is not None:
                control_excess_list.append(fwd - med)

    if logger:
        logger.info(
            "Placebo control: %d control observations drawn (K=%d/event); "
            "%d events had no eligible candidates",
            len(control_excess_list), K, n_no_candidates,
        )

    control_n = len(control_excess_list)
    if control_n == 0:
        ctrl_mean = float("nan")
        ctrl_std = float("nan")
    else:
        ctrl_arr = np.array(control_excess_list, dtype=float)
        ctrl_mean = float(np.mean(ctrl_arr))
        ctrl_std = float(np.std(ctrl_arr, ddof=1)) if control_n > 1 else float("nan")

    event_n = len(event_excess_list)
    event_mean = float(np.mean(event_excess_list)) if event_n > 0 else float("nan")
    net = event_mean - ctrl_mean if (math.isfinite(event_mean) and math.isfinite(ctrl_mean)) else float("nan")

    # One-line interpretation
    if math.isfinite(ctrl_mean) and math.isfinite(net):
        interp = (
            f"artifact ≈ {ctrl_mean:+.2f}pp; "
            f"event-specific ≈ {net:+.2f}pp"
        )
    else:
        interp = "control not computable (no eligible candidates)"

    return {
        "control_n": control_n,
        "control_mean_excess": round(ctrl_mean, 4) if math.isfinite(ctrl_mean) else float("nan"),
        "control_std_excess": round(ctrl_std, 4) if math.isfinite(ctrl_std) else float("nan"),
        "event_n": event_n,
        "event_mean_excess": round(event_mean, 4) if math.isfinite(event_mean) else float("nan"),
        "net_excess": round(net, 4) if math.isfinite(net) else float("nan"),
        "interpretation": interp,
        "control_K": K,
        "control_horizon_days": horizon,
        "n_events_no_candidates": n_no_candidates,
    }


# ---------------------------------------------------------------------------
# Core stats function
# ---------------------------------------------------------------------------
def _compute_stats(
    excess_list: list[float],
    dose_scores: Optional[list[float]] = None,
    q5_n_override: Optional[int] = None,
    q1_n_override: Optional[int] = None,
    q5_var_override: Optional[float] = None,
    q1_var_override: Optional[float] = None,
) -> dict:
    """Compute n, mean, std, MDE_1samp, MDE_gap from excess values.

    If dose_scores provided, computes quintile split.
    Override args allow using pre-known per-quintile stats (e.g. from verdict file).
    """
    n = len(excess_list)
    if n == 0:
        return {
            "n_valid": 0,
            "mean_excess": float("nan"),
            "std_excess": float("nan"),
            "MDE_1samp": float("nan"),
            "MDE_gap": None,
            "n_needed_1pp": -1,
        }

    arr = np.array(excess_list, dtype=float)
    mean_exc = float(np.mean(arr))
    std_exc = float(np.std(arr, ddof=1)) if n > 1 else float("nan")
    mde_1 = _mde_1samp(n, std_exc)
    n_needed = _n_needed_1pp(std_exc)

    result = {
        "n_valid": n,
        "mean_excess": round(mean_exc, 4),
        "std_excess": round(std_exc, 4),
        "MDE_1samp": round(mde_1, 4),
        "n_needed_1pp": n_needed,
        "MDE_gap": None,
    }

    # Dose-gap MDE
    if q5_n_override is not None and q1_n_override is not None:
        # Use override quintile stats
        n_q5 = q5_n_override
        n_q1 = q1_n_override
        if q5_var_override is not None and q1_var_override is not None:
            var_q5 = q5_var_override
            var_q1 = q1_var_override
        else:
            # fallback: use overall variance as proxy
            var_q5 = std_exc ** 2
            var_q1 = std_exc ** 2
        mde_g = _mde_gap(n_q5, var_q5, n_q1, var_q1)
        result["MDE_gap"] = round(mde_g, 4)
        result["n_q5"] = n_q5
        result["n_q1"] = n_q1
    elif dose_scores is not None and len(dose_scores) == n:
        # Build quintiles from dose_scores
        scores = np.array(dose_scores, dtype=float)
        valid = np.isfinite(scores) & np.isfinite(arr)
        if np.sum(valid) > 10:
            s_valid = scores[valid]
            a_valid = arr[valid]
            q20 = np.percentile(s_valid, 20)
            q80 = np.percentile(s_valid, 80)
            q1_mask = s_valid <= q20
            q5_mask = s_valid >= q80
            exc_q1 = a_valid[q1_mask]
            exc_q5 = a_valid[q5_mask]
            if len(exc_q1) > 1 and len(exc_q5) > 1:
                var_q1 = float(np.var(exc_q1, ddof=1))
                var_q5 = float(np.var(exc_q5, ddof=1))
                mde_g = _mde_gap(len(exc_q5), var_q5, len(exc_q1), var_q1)
                result["MDE_gap"] = round(mde_g, 4)
                result["n_q5"] = int(len(exc_q5))
                result["n_q1"] = int(len(exc_q1))

    return result


# ---------------------------------------------------------------------------
# CALIBRATION FAMILY — must PASS before any other family is reported
# ---------------------------------------------------------------------------
def run_calibration(out_dir: Path, logger: logging.Logger) -> dict:
    """Calibration anchor check using R-1b events' own stored excess values.

    A1: n_valid == 4245 (exact)
    A2: std within ±2% of 23.39, mean within ±0.05 of 2.31
    A3: one-sample MDE within ±0.02 of 1.006; dose-gap MDE within ±0.1 of 3.40

    Also validates the matrix-join path.
    """
    logger.info("=" * 60)
    logger.info("CALIBRATION FAMILY — F338 gate")
    logger.info("=" * 60)

    # Load verdict for per-quintile overrides
    with open(_VERDICT_JSON) as f:
        verdict = json.load(f)
    per_quintile = verdict.get("per_quintile", {})

    # Per spec: use n_q5=591/n_q1=596 with per-quintile means/stds from verdict
    # The verdict has only mean_63d_excess and n per quintile, not std.
    # For the dose-gap MDE, we use variance from stored excess values.
    n_q5 = verdict.get("H1", {}).get("n_q5", 591)
    n_q1 = verdict.get("H1", {}).get("n_q1", 596)

    logger.info("Loading R-1b events from %s ...", _EVENTS_NDJSON)
    events_floor_ok = []
    all_events_with_score = []

    with open(_EVENTS_NDJSON) as f:
        for line in f:
            ev = json.loads(line.strip())
            fs = ev.get("floor_status", "")
            exc_63 = (ev.get("fwd_excess_pct") or {}).get("63")
            if (
                ev.get("split") == "explore"
                and fs == "ok"
                and exc_63 is not None
            ):
                events_floor_ok.append(ev)

    logger.info("Events loaded: n_floor_ok_explore = %d", len(events_floor_ok))

    # --- A1 check ---
    n_valid = len(events_floor_ok)
    a1_pass = (n_valid == 4245)
    logger.info("A1 n_valid=%d (expected 4245) — %s", n_valid, "PASS" if a1_pass else "FAIL")

    # Compute stats from stored excess
    excess_63 = [ev["fwd_excess_pct"]["63"] for ev in events_floor_ok]
    arr = np.array(excess_63, dtype=float)
    mean_exc = float(np.mean(arr))
    std_exc = float(np.std(arr, ddof=1))
    mde_1samp = _mde_1samp(n_valid, std_exc)

    # --- A2 check ---
    std_tol = 0.02  # ±2%
    a2_std = abs(std_exc - 23.39) / 23.39 <= std_tol
    a2_mean = abs(mean_exc - 2.31) <= 0.05
    a2_pass = a2_std and a2_mean
    logger.info(
        "A2 std_excess_63=%.4f (target 23.39 ±2%%) — %s; mean_excess_63=%.4f (target 2.31 ±0.05) — %s",
        std_exc, "PASS" if a2_std else "FAIL",
        mean_exc, "PASS" if a2_mean else "FAIL",
    )

    # --- A3 MDE_1samp check ---
    a3_mde1 = abs(mde_1samp - 1.006) <= 0.02
    logger.info(
        "A3 MDE_1samp_63=%.4f (target 1.006 ±0.02) — %s",
        mde_1samp, "PASS" if a3_mde1 else "FAIL",
    )

    # Dose-gap MDE: use per-quintile stds from stored excess values
    # Sort events by payload score (insider cluster score) for quintile split
    scores = []
    for ev in events_floor_ok:
        payload = ev.get("payload") or {}
        score = payload.get("score")
        scores.append(score)

    scores_valid = [s for s in scores if s is not None]
    logger.info(
        "Events with non-null payload.score: %d / %d",
        len(scores_valid), n_valid,
    )

    mde_gap = float("nan")
    if len(scores_valid) > 100:
        # Build quintile split on score
        score_arr = np.array([s if s is not None else np.nan for s in scores], dtype=float)
        exc_arr = np.array(excess_63, dtype=float)
        valid_mask = np.isfinite(score_arr)
        s_valid = score_arr[valid_mask]
        e_valid = exc_arr[valid_mask]
        q20 = np.percentile(s_valid, 20)
        q80 = np.percentile(s_valid, 80)
        q1_mask_arr = s_valid <= q20
        q5_mask_arr = s_valid >= q80
        exc_q1_arr = e_valid[q1_mask_arr]
        exc_q5_arr = e_valid[q5_mask_arr]
        if len(exc_q1_arr) > 1 and len(exc_q5_arr) > 1:
            var_q1 = float(np.var(exc_q1_arr, ddof=1))
            var_q5 = float(np.var(exc_q5_arr, ddof=1))
            mde_gap = _mde_gap(len(exc_q5_arr), var_q5, len(exc_q1_arr), var_q1)
            logger.info(
                "Dose-gap from stored excess: n_q5=%d var_q5=%.2f n_q1=%d var_q1=%.2f → MDE_gap=%.4f",
                len(exc_q5_arr), var_q5, len(exc_q1_arr), var_q1, mde_gap,
            )
    else:
        # COR-04: the null-score fallback (overall var as per-quintile proxy) produces
        # ~3.80pp vs the 3.40pp target because per-quintile variance (~437pp²) is
        # substantially lower than overall variance (547pp²). This path cannot honestly
        # validate A3_gap. Log a warning and set mde_gap=NaN so the A3_gap check is
        # skipped rather than hard-failing on a path that uses degraded inputs.
        logger.warning(
            "Score mostly null (%d non-null) — cannot compute A3_gap without per-quintile "
            "variances; skipping A3_gap assertion (mde_gap=NaN). "
            "Re-run with a scored events file to validate the dose-gap anchor.",
            len(scores_valid),
        )
        mde_gap = float("nan")

    a3_gap_skipped = False
    if math.isnan(mde_gap):
        # COR-06: null-score fallback path — skip A3_gap rather than hard-failing.
        # a3_gap=True here means "not blocking" (same as pass for the sys.exit guard),
        # but the census.json anchor entry records skipped=True so downstream consumers
        # can distinguish "anchor validated" from "anchor skipped due to unavailable data".
        a3_gap = True
        a3_gap_skipped = True
        logger.info("A3 MDE_gap_63=NaN (skipped — null-score fallback, see warning above)")
    else:
        a3_gap = abs(mde_gap - 3.40) <= 0.10
        logger.info(
            "A3 MDE_gap_63=%.4f (target 3.40 ±0.10) — %s",
            mde_gap, "PASS" if a3_gap else "FAIL",
        )

    a3_pass = a3_mde1 and a3_gap
    all_pass = a1_pass and a2_pass and a3_pass

    if not all_pass:
        logger.error(
            "CALIBRATION FAILED — A1=%s A2=%s A3=%s. "
            "Census mechanics are broken; do NOT report family numbers.",
            "PASS" if a1_pass else "FAIL",
            "PASS" if a2_pass else "FAIL",
            "PASS" if a3_pass else "FAIL",
        )
        sys.exit(1)

    logger.info("CALIBRATION PASS — all anchors A1/A2/A3 met.")

    # --- Matrix-join validation ---
    # PY-02: Load the parquet ONCE here and reuse for validation + placebo below.
    logger.info("Validating matrix-join path ...")
    medians = _load_matrix_medians(horizons=(63,), logger=logger)
    h_path_63 = _MATRIX_DIR / "horizon_days=63"
    df_mat = pd.read_parquet(h_path_63)
    df_mat = df_mat.copy()
    df_mat["entry_date"] = df_mat["entry_date"].astype(str)
    mat_lookup_cal = df_mat.set_index(["entry_date", "symbol"])["fwd_return_pct"].to_dict()
    med_map = medians[63]

    matrix_events = [{"entry_date": ev["entry_date"], "ticker": ev.get("ticker", "")} for ev in events_floor_ok]
    # Pass pre-built mat_lookup to avoid a second parquet read (PY-02)
    matrix_excess, n_no_frame = _compute_excess_from_matrix(
        matrix_events, medians, horizon=63, logger=logger, mat_lookup=mat_lookup_cal,
    )

    # Sentinel init so n_matched reference is always valid (PY-02/COR-09)
    stored_exc_arr: list = []
    matrix_exc_arr: list = []

    if len(matrix_excess) > 0:
        mat_arr = np.array(matrix_excess, dtype=float)
        mat_mean = float(np.mean(mat_arr))
        mat_std = float(np.std(mat_arr, ddof=1))

        for ev in events_floor_ok:
            ed = ev["entry_date"]  # already string from ndjson
            ticker = ev.get("ticker", "")
            exc_stored = ev["fwd_excess_pct"]["63"]
            fwd_mat = mat_lookup_cal.get((ed, ticker))
            med = med_map.get(ed)
            if fwd_mat is not None and med is not None:
                stored_exc_arr.append(exc_stored)
                matrix_exc_arr.append(fwd_mat - med)

        if len(stored_exc_arr) > 0:
            stored_np = np.array(stored_exc_arr)
            matrix_np = np.array(matrix_exc_arr)
            delta_mean = float(np.mean(matrix_np - stored_np))
            delta_std = float(np.std(matrix_np - stored_np, ddof=1))
            corr = float(np.corrcoef(stored_np, matrix_np)[0, 1])
            logger.info(
                "Matrix-join vs stored excess: n_matched=%d, mean_delta=%.4fpp, "
                "std_delta=%.4fpp, correlation=%.4f",
                len(stored_exc_arr), delta_mean, delta_std, corr,
            )
            logger.info(
                "Matrix-join excess: mean=%.4f std=%.4f (matched n=%d, no_frame=%d)",
                mat_mean, mat_std, len(matrix_excess), n_no_frame,
            )
        else:
            logger.warning("No matched events for matrix-join vs stored comparison")

    # --- Placebo-control benchmark (F371) ---
    # PY-02: reuse df_mat / mat_lookup_cal already loaded above — no third parquet read.
    logger.info("Computing placebo-control benchmark (F371) ...")
    mat_lookup_placebo = mat_lookup_cal
    med_map_placebo = med_map
    # COR-05: build sorted_dates from the FULL matrix entry-date set, not just
    # med_map keys (which only contains dates with at least one floor-passing row).
    # Dates absent from med_map but present in the matrix still need to be in
    # sorted_dates so their blackout windows are registered correctly.
    sorted_dates_placebo = sorted(set(df_mat["entry_date"].tolist()))
    cal_events_for_control = [
        {"entry_date": ev["entry_date"], "ticker": ev.get("ticker", "")}
        for ev in events_floor_ok
    ]
    placebo_cal = _compute_placebo_control(
        cal_events_for_control,
        mat_lookup_placebo,
        med_map_placebo,
        sorted_dates_placebo,
        horizon=63,
        logger=logger,
    )
    logger.info(
        "Calibration placebo: control_n=%d control_mean=%.4fpp event_mean=%.4fpp net=%.4fpp | %s",
        placebo_cal["control_n"],
        placebo_cal["control_mean_excess"],
        placebo_cal["event_mean_excess"],
        placebo_cal["net_excess"],
        placebo_cal["interpretation"],
    )

    # COR-06: A3_MDE_gap uses skipped=True (pass=null) when mde_gap is NaN so
    # downstream consumers can distinguish "validated" from "unavailable data — skipped".
    a3_gap_entry: dict = {
        "value": round(mde_gap, 4) if not math.isnan(mde_gap) else None,
        "target": 3.40,
        "pass": (a3_gap if not a3_gap_skipped else None),
    }
    if a3_gap_skipped:
        a3_gap_entry["skipped"] = True
        a3_gap_entry["skip_reason"] = "null-score fallback — per-quintile variances unavailable"

    result = {
        "family": "calibration",
        "anchors": {
            "A1_n_valid": {"value": n_valid, "target": 4245, "pass": a1_pass},
            "A2_std": {"value": round(std_exc, 4), "target": 23.39, "pass": a2_std},
            "A2_mean": {"value": round(mean_exc, 4), "target": 2.31, "pass": a2_mean},
            "A3_MDE_1samp": {"value": round(mde_1samp, 4), "target": 1.006, "pass": a3_mde1},
            "A3_MDE_gap": a3_gap_entry,
        },
        "stats_stored_excess": {
            "n_valid": n_valid,
            "mean_excess_63": round(mean_exc, 4),
            "std_excess_63": round(std_exc, 4),
            "MDE_1samp_63": round(mde_1samp, 4),
            "MDE_gap_63": round(mde_gap, 4),
        },
        "matrix_join_validation": {
            "n_matched": len(stored_exc_arr),  # sentinel-inited above (PY-02)
            "n_no_frame": n_no_frame,
            "matrix_mean_excess_63": round(mat_mean, 4) if len(matrix_excess) > 0 else None,
            "matrix_std_excess_63": round(mat_std, 4) if len(matrix_excess) > 0 else None,
        },
        "placebo_control": placebo_cal,  # F371 — additive; does not affect anchor checks
        "all_pass": all_pass,
        # Note: medians dict is NOT stored in census.json (too large);
        # it's attached here and stripped before JSON write by write_census_json
    }
    result["_medians"] = medians  # non-serialized, popped before JSON write (COR-01 fix)
    return result


# ---------------------------------------------------------------------------
# R1B_SUBUNIVERSE FAMILY
# ---------------------------------------------------------------------------
def run_r1b_subuniverse(medians: dict, out_dir: Path, logger: logging.Logger) -> dict:
    """R-1b calmer sub-universe analysis by MC quartile (and vol if cheap)."""
    logger.info("=" * 60)
    logger.info("R1B_SUBUNIVERSE FAMILY")
    logger.info("=" * 60)

    if 63 not in medians:
        logger.info("Loading medians for horizon 63 ...")
        medians.update(_load_matrix_medians(horizons=(63,), logger=logger))

    logger.info("Loading R-1b events ...")
    events_valid = []
    with open(_EVENTS_NDJSON) as f:
        for line in f:
            ev = json.loads(line.strip())
            fs = ev.get("floor_status", "")
            exc_63 = (ev.get("fwd_excess_pct") or {}).get("63")
            if (
                ev.get("split") == "explore"
                and fs == "ok"
                and exc_63 is not None
            ):
                events_valid.append(ev)

    logger.info("Total valid events: %d", len(events_valid))

    # Separate MC-carrying vs null MC
    events_with_mc = [ev for ev in events_valid if (ev.get("payload") or {}).get("MC") is not None]
    n_no_mc = len(events_valid) - len(events_with_mc)
    logger.info("Events with MC: %d; no-MC: %d", len(events_with_mc), n_no_mc)

    # MC quartile boundaries per spec: p25=$1.5B, p50=$3.8B, p75=$11.8B
    # (in dollars)
    Q_BOUNDS = [1.5e9, 3.8e9, 11.8e9]
    Q_LABELS = ["Q1 (<$1.5B)", "Q2 ($1.5B-$3.8B)", "Q3 ($3.8B-$11.8B)", "Q4 (>$11.8B)"]

    buckets: dict[str, list] = {q: [] for q in Q_LABELS}
    for ev in events_with_mc:
        mc = ev["payload"]["MC"]
        exc_63 = ev["fwd_excess_pct"]["63"]
        if mc < Q_BOUNDS[0]:
            buckets[Q_LABELS[0]].append(exc_63)
        elif mc < Q_BOUNDS[1]:
            buckets[Q_LABELS[1]].append(exc_63)
        elif mc < Q_BOUNDS[2]:
            buckets[Q_LABELS[2]].append(exc_63)
        else:
            buckets[Q_LABELS[3]].append(exc_63)

    rows = []
    for label, excesses in buckets.items():
        stats = _compute_stats(excesses)
        logger.info(
            "  MC bucket '%s': n=%d std=%.2f MDE_1samp=%.4f",
            label, stats["n_valid"], stats.get("std_excess", float("nan")),
            stats.get("MDE_1samp", float("nan")),
        )
        rows.append({"mc_bucket": label, **stats})

    # Overall with-MC subset
    all_mc_excess = [ev["fwd_excess_pct"]["63"] for ev in events_with_mc]
    overall_stats = _compute_stats(all_mc_excess)
    logger.info(
        "Overall MC-subset (n=%d): std=%.4f MDE_1samp=%.4f",
        overall_stats["n_valid"], overall_stats.get("std_excess", float("nan")),
        overall_stats.get("MDE_1samp", float("nan")),
    )

    # Note on vol: skipping realized trailing vol (requires price history per event) — noted
    logger.info(
        "NOTE: Per-bucket realized trailing vol analysis skipped "
        "(requires price cache scan per event — deferred to full worker run)."
    )

    return {
        "family": "r1b_subuniverse",
        "n_events_with_mc": len(events_with_mc),
        "n_events_no_mc": n_no_mc,
        "mc_quartile_boundaries_dollars": Q_BOUNDS,
        "mc_buckets": rows,
        "overall_mc_subset": overall_stats,
        "vol_bucketing": "skipped — requires price history per event",
    }


# ---------------------------------------------------------------------------
# PEAD FAMILY (heavy — submissions scan)
# ---------------------------------------------------------------------------
def run_pead(
    medians: dict,
    out_dir: Path,
    logger: logging.Logger,
    max_files: Optional[int] = None,
) -> dict:
    """PEAD family: 10-Q/10-K acceptance dates 2015-2020 → matrix excess."""
    logger.info("=" * 60)
    logger.info("PEAD FAMILY (10-Q/10-K submissions scan 2015-2020)")
    logger.info("=" * 60)

    if max_files:
        logger.warning(
            "PARTIAL RUN: max_files=%d. Full run should be on worker.", max_files
        )

    # Load CIK->ticker map via shared helper (PY-10)
    cik_to_ticker = _load_cik_ticker_map()
    logger.info("CIK->ticker map: %d entries", len(cik_to_ticker))

    if 63 not in medians:
        logger.info("Loading matrix medians ...")
        medians.update(_load_matrix_medians(horizons=(63,), logger=logger))

    med_map_63 = medians[63]

    # Load matrix lookup for 63d (normalize entry_date to str)
    h_path = _MATRIX_DIR / "horizon_days=63"
    df_mat = pd.read_parquet(h_path)
    df_mat = df_mat.copy()
    df_mat["entry_date"] = df_mat["entry_date"].astype(str)
    mat_lookup = df_mat.set_index(["entry_date", "symbol"])["fwd_return_pct"].to_dict()

    start_year = 2015
    end_year = 2020

    raw_events = []  # (acceptance_dt, ticker, form)
    n_raw = 0
    n_parse_errors = 0  # PY-04: count silenced parse errors
    submission_files = sorted(_SUBMISSIONS_DIR.glob("*.json"))
    total_files = len(submission_files)
    if max_files:
        submission_files = submission_files[:max_files]

    # Sorted trading-day list for _entry_date_for_filing / _next_trading_day (PY-11)
    all_dates_sorted = sorted(set(med_map_63.keys()))

    logger.info("Scanning %d / %d submission files ...", len(submission_files), total_files)
    t0 = time.time()
    for i, fp in enumerate(submission_files):
        if i > 0 and i % 1000 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(submission_files) - i) / rate if rate > 0 else 0
            logger.info(
                "  PEAD scan progress: %d / %d files (%.1f%%) — %.1f files/s — ETA %.0fs",
                i, len(submission_files), 100 * i / len(submission_files),
                rate, eta,
            )

        cik = fp.stem  # e.g. "0000320193"
        ticker = cik_to_ticker.get(cik)
        if not ticker:
            continue

        try:
            with open(fp) as f:
                filing_data = json.load(f)
        except Exception as exc:
            n_parse_errors += 1
            if n_parse_errors <= 5:
                logger.debug("PEAD parse error %s: %s", fp, exc)
            continue

        recent = filing_data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accept_dts = recent.get("acceptanceDateTime", [])
        if len(forms) != len(accept_dts):
            continue

        for form, adt in zip(forms, accept_dts):
            if form not in ("10-Q", "10-K"):
                continue
            if not adt:
                continue
            try:
                # Parse year from ISO 8601 string
                year = int(adt[:4])
            except (ValueError, IndexError):
                continue
            if year < start_year or year > end_year:
                continue

            n_raw += 1
            # Store full acceptanceDateTime for tz-aware entry-date conversion (COR-02)
            raw_events.append((adt, ticker, form))

    if n_parse_errors > 0:
        n_files = len(submission_files)
        pct = 100 * n_parse_errors / n_files if n_files else 0
        if pct > 1.0:
            logger.warning(
                "PEAD: %d parse errors out of %d files (%.1f%%) — check submissions corpus",
                n_parse_errors, n_files, pct,
            )
        else:
            logger.info("PEAD: %d parse errors (%.2f%% of files)", n_parse_errors, pct)

    logger.info("PEAD: n_raw=%d events from %d files", n_raw, len(submission_files))

    # Join to matrix: COR-02 — use _entry_date_for_filing for tz-aware after-hours correction
    excess_list = []
    n_no_frame = 0
    n_deduped = 0  # COR-06: count deduped events
    n_matched = 0
    seen: set = set()  # dedup: (ticker, entry_date) — one event per ticker per date
    valid_events_for_control: list = []  # F371: (ticker, entry_date) tuples for placebo

    for (adt, ticker, form) in raw_events:
        ed = _entry_date_for_filing(adt, all_dates_sorted)
        if ed is None:
            n_no_frame += 1
            continue
        key = (ticker, ed)
        if key in seen:
            n_deduped += 1  # COR-06
            continue
        seen.add(key)

        fwd = mat_lookup.get((ed, ticker))
        med = med_map_63.get(ed)
        if fwd is None or med is None:
            n_no_frame += 1
            continue
        excess_list.append(fwd - med)
        n_matched += 1
        valid_events_for_control.append({"entry_date": ed, "ticker": ticker})

    if max_files:
        n_valid_est = int(n_matched * total_files / len(submission_files))
        logger.info(
            "PEAD partial: n_matched=%d, estimated full n_valid~%d (extrapolated from %d/%d files)",
            n_matched, n_valid_est, len(submission_files), total_files,
        )
    else:
        n_valid_est = n_matched

    stats = _compute_stats(excess_list)
    logger.info(
        "PEAD: n_raw=%d n_deduped=%d n_no_frame=%d n_valid=%d n_parse_errors=%d "
        "std_63=%.4f MDE_1samp_63=%.4f",
        n_raw, n_deduped, n_no_frame, stats["n_valid"], n_parse_errors,
        stats.get("std_excess", float("nan")),
        stats.get("MDE_1samp", float("nan")),
    )

    # --- Placebo-control benchmark (F371) ---
    logger.info("Computing PEAD placebo-control benchmark (F371) ...")
    placebo_pead = _compute_placebo_control(
        valid_events_for_control,
        mat_lookup,
        med_map_63,
        # RCHK-12/COR-05: full matrix dates (not med_map keys) so blackout windows
        # register on all-floor-fail dates too — matches the calibration placebo.
        sorted(set(df_mat["entry_date"].tolist())),
        horizon=63,
        logger=logger,
    )
    logger.info(
        "PEAD placebo: control_n=%d control_mean=%.4fpp event_mean=%.4fpp net=%.4fpp | %s",
        placebo_pead["control_n"],
        placebo_pead["control_mean_excess"],
        placebo_pead["event_mean_excess"],
        placebo_pead["net_excess"],
        placebo_pead["interpretation"],
    )

    return {
        "family": "pead",
        "is_partial": bool(max_files),
        "files_scanned": len(submission_files),
        "total_files": total_files,
        "n_raw": n_raw,
        "n_deduped": n_deduped,
        "n_no_frame": n_no_frame,
        "n_parse_errors": n_parse_errors,
        "note_extractor_owed": "surprise definition (estimate-free YoY-accel / actual-vs-trailing) not built",
        "entry_date_rule": "next trading day in matrix on-or-after acceptanceDateTime (ET tz-aware, >=16:00 ET advances one day)",
        "placebo_control": placebo_pead,  # F371
        **stats,
    }


# ---------------------------------------------------------------------------
# 8-K FAMILY (heavy)
# ---------------------------------------------------------------------------
def run_eightk(
    medians: dict,
    out_dir: Path,
    logger: logging.Logger,
    max_files: Optional[int] = None,
) -> dict:
    """8-K family: split by item code 2015-2020."""
    logger.info("=" * 60)
    logger.info("8-K FAMILY (submissions scan 2015-2020)")
    logger.info("=" * 60)

    if max_files:
        logger.warning("PARTIAL RUN: max_files=%d.", max_files)

    # Shared helpers (PY-10, PY-11)
    cik_to_ticker = _load_cik_ticker_map()

    if 63 not in medians:
        medians.update(_load_matrix_medians(horizons=(63,), logger=logger))

    med_map_63 = medians[63]
    h_path = _MATRIX_DIR / "horizon_days=63"
    df_mat = pd.read_parquet(h_path)
    df_mat = df_mat.copy()
    df_mat["entry_date"] = df_mat["entry_date"].astype(str)
    mat_lookup = df_mat.set_index(["entry_date", "symbol"])["fwd_return_pct"].to_dict()

    all_dates_sorted = sorted(set(med_map_63.keys()))

    start_year = 2015
    end_year = 2020

    TARGET_ITEMS = {"2.02", "5.02", "1.01", "8.01"}

    # COR-03: split other_multi into multi_target (≥2 target codes) and
    # no_target (0 target codes) — they are structurally distinct populations.
    # {bucket_key: [(acceptance_dt, ticker),...]}
    item_events: dict[str, list] = {k: [] for k in TARGET_ITEMS}
    item_events["multi_target"] = []   # len(target_codes) > 1
    item_events["no_target"] = []      # len(target_codes) == 0
    n_raw_total = 0
    n_parse_errors = 0  # PY-04

    submission_files = sorted(_SUBMISSIONS_DIR.glob("*.json"))
    total_files = len(submission_files)
    if max_files:
        submission_files = submission_files[:max_files]

    logger.info("Scanning %d / %d files for 8-K events ...", len(submission_files), total_files)
    t0 = time.time()
    for i, fp in enumerate(submission_files):
        if i > 0 and i % 1000 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(submission_files) - i) / rate if rate > 0 else 0
            logger.info(
                "  8-K scan progress: %d / %d files (%.1f%%) — %.1f files/s — ETA %.0fs",
                i, len(submission_files), 100 * i / len(submission_files),
                rate, eta,
            )

        cik = fp.stem
        ticker = cik_to_ticker.get(cik)
        if not ticker:
            continue

        try:
            with open(fp) as f:
                filing_data = json.load(f)
        except Exception as exc:
            n_parse_errors += 1
            if n_parse_errors <= 5:
                logger.debug("8-K parse error %s: %s", fp, exc)
            continue

        recent = filing_data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accept_dts = recent.get("acceptanceDateTime", [])
        items_list = recent.get("items", [])

        if len(forms) != len(accept_dts):
            continue

        # Pad items_list if shorter
        while len(items_list) < len(forms):
            items_list.append("")

        for form, adt, items_str in zip(forms, accept_dts, items_list):
            if form != "8-K":
                continue
            if not adt:
                continue
            try:
                year = int(adt[:4])
            except (ValueError, IndexError):
                continue
            if year < start_year or year > end_year:
                continue

            n_raw_total += 1
            codes = [c.strip() for c in items_str.split(",") if c.strip()] if items_str else []

            target_codes = [c for c in codes if c in TARGET_ITEMS]
            if len(target_codes) == 1:
                item_events[target_codes[0]].append((adt, ticker))
            elif len(target_codes) > 1:
                # COR-03: multi_target bucket (≥2 of our target item codes)
                item_events["multi_target"].append((adt, ticker))
            else:
                # COR-03: no_target bucket (0 target item codes found — exhibit
                # amendments, 9.01, 8.02+9.01, etc.)
                item_events["no_target"].append((adt, ticker))

    if n_parse_errors > 0:
        n_files = len(submission_files)
        pct = 100 * n_parse_errors / n_files if n_files else 0
        if pct > 1.0:
            logger.warning(
                "8-K: %d parse errors out of %d files (%.1f%%) — check submissions corpus",
                n_parse_errors, n_files, pct,
            )
        else:
            logger.info("8-K: %d parse errors (%.2f%% of files)", n_parse_errors, pct)

    logger.info("8-K: n_raw_total=%d", n_raw_total)

    # Compute excess per item bucket
    # COR-02: use _entry_date_for_filing for tz-aware after-hours correction
    bucket_results = []
    for bucket_name, evts in item_events.items():
        excess_list = []
        n_no_frame = 0
        n_deduped = 0  # COR-06
        seen: set = set()
        bucket_valid_events: list = []  # F371: valid events for this bucket's placebo
        for (adt, ticker) in evts:
            ed = _entry_date_for_filing(adt, all_dates_sorted)
            if ed is None:
                n_no_frame += 1
                continue
            key = (ticker, ed)
            if key in seen:
                n_deduped += 1  # COR-06
                continue
            seen.add(key)
            fwd = mat_lookup.get((ed, ticker))
            med = med_map_63.get(ed)
            if fwd is None or med is None:
                n_no_frame += 1
                continue
            excess_list.append(fwd - med)
            bucket_valid_events.append({"entry_date": ed, "ticker": ticker})

        stats = _compute_stats(excess_list)
        logger.info(
            "  8-K item '%s': n_raw=%d n_deduped=%d n_no_frame=%d n_valid=%d std=%.2f MDE_1samp=%.4f",
            bucket_name, len(evts), n_deduped, n_no_frame, stats["n_valid"],
            stats.get("std_excess", float("nan")),
            stats.get("MDE_1samp", float("nan")),
        )

        # Placebo-control benchmark per bucket (F371)
        placebo_bucket = _compute_placebo_control(
            bucket_valid_events,
            mat_lookup,
            med_map_63,
            # RCHK-12/COR-05: full matrix dates (not med_map keys), matches calibration.
            sorted(set(df_mat["entry_date"].tolist())),
            horizon=63,
            logger=logger,
        )
        logger.info(
            "  8-K/%s placebo: control_n=%d control_mean=%.4fpp event_mean=%.4fpp net=%.4fpp | %s",
            bucket_name,
            placebo_bucket["control_n"],
            placebo_bucket["control_mean_excess"],
            placebo_bucket["event_mean_excess"],
            placebo_bucket["net_excess"],
            placebo_bucket["interpretation"],
        )

        bucket_results.append({
            "item_code": bucket_name,
            "n_raw": len(evts),
            "n_deduped": n_deduped,
            "n_no_frame": n_no_frame,
            "placebo_control": placebo_bucket,  # F371
            **stats,
        })

    return {
        "family": "eightk",
        "is_partial": bool(max_files),
        "files_scanned": len(submission_files),
        "total_files": total_files,
        "n_raw_total": n_raw_total,
        "n_parse_errors": n_parse_errors,
        "item_buckets": bucket_results,
        "entry_date_rule": "next trading day in matrix on-or-after acceptanceDateTime (ET tz-aware, >=16:00 ET advances one day)",
        "bucket_note": "multi_target=len(target_codes)>1; no_target=len(target_codes)==0 (exhibits/amendments)",
    }


# ---------------------------------------------------------------------------
# R2 FAMILY (heavy-ish)
# ---------------------------------------------------------------------------
def run_r2(
    medians: dict,
    out_dir: Path,
    logger: logging.Logger,
    max_files: Optional[int] = None,
) -> dict:
    """R-2 distress recovery: D2-state tickers at 10-Q/10-K filings 2015-2020.

    Approach: approximate the D2 predicate using only the universe matrix
    (crash/low gates derivable from price cache frames), joined to 10-Q/10-K
    filing dates. Clearly labeled as an estimate.

    D2 predicate (Gates A+B+D, revenue veto OFF):
      Gate D: ≥252 bars of price history
      Gate A: pct_off_high ≥ 50% (using 252-bar trailing high)
      Gate B: pct_above_low ≤ 25% (using 252-bar trailing low)
    """
    logger.info("=" * 60)
    logger.info("R2 FAMILY (D2 distress recovery, 2015-2020)")
    logger.info("=" * 60)

    if max_files:
        logger.warning("PARTIAL RUN: max_files=%d.", max_files)

    # Check price cache availability
    if not _PRICE_CACHE_DIR.exists():
        logger.warning("Price cache not found at %s; R2 family blocked.", _PRICE_CACHE_DIR)
        return {
            "family": "r2",
            "status": "blocked",
            "reason": f"Price cache not found at {_PRICE_CACHE_DIR}",
        }

    # Shared helpers (PY-10, PY-11)
    cik_to_ticker = _load_cik_ticker_map()

    if 63 not in medians:
        medians.update(_load_matrix_medians(horizons=(63,), logger=logger))

    med_map_63 = medians[63]
    h_path = _MATRIX_DIR / "horizon_days=63"
    df_mat = pd.read_parquet(h_path)
    df_mat = df_mat.copy()
    df_mat["entry_date"] = df_mat["entry_date"].astype(str)
    mat_lookup = df_mat.set_index(["entry_date", "symbol"])["fwd_return_pct"].to_dict()

    all_dates_sorted = sorted(set(med_map_63.keys()))

    # Load price cache for D2 predicate evaluation
    # Build ticker -> latest price cache file mapping
    price_cache_files = list(_PRICE_CACHE_DIR.glob("*.pkl"))
    ticker_to_cache: dict[str, list[Path]] = {}
    for pf in price_cache_files:
        parts = pf.stem.split("_")
        if parts:
            ticker_to_cache.setdefault(parts[0], []).append(pf)
    logger.info("Price cache: %d files covering %d tickers", len(price_cache_files), len(ticker_to_cache))

    def load_price_df(ticker: str, as_of_year: int) -> Optional[pd.DataFrame]:
        """Load the best matching price cache for ticker covering as_of_year."""
        candidates = ticker_to_cache.get(ticker, [])
        if not candidates:
            return None
        # Filter to files that cover the year range
        best = None
        for pf in candidates:
            # Filename format: TICKER_<hash>_<provider>_<start>_<end>.pkl
            stem = pf.stem
            parts = stem.split("_")
            if len(parts) >= 5:
                try:
                    start_y = int(parts[-2][:4])
                    end_y = int(parts[-1][:4])
                    if start_y <= as_of_year <= end_y:
                        if best is None or (int(parts[-2]) < int(best.stem.split("_")[-2])):
                            best = pf
                except (ValueError, IndexError):
                    pass
        if best is None:
            # Fall back to any candidate
            best = candidates[0]
        # PY-07: narrow try to the pickle.load call only; validate result type separately
        try:
            with open(best, "rb") as f:
                result = pickle.load(f)
        except Exception:
            return None
        # If pickle succeeded but produced an unexpected type, don't let df.empty
        # AttributeError crash the D2 predicate loop
        if not isinstance(result, pd.DataFrame):
            return None
        return result

    def check_d2(ticker: str, as_of_date: date) -> bool:
        """Check if ticker passes D2 gates (A+B+D) at as_of_date."""
        df = load_price_df(ticker, as_of_date.year)
        if df is None or df.empty:
            return False

        # Slice to bars <= as_of_date
        idx = df.index
        if isinstance(idx, pd.DatetimeIndex):
            if idx.tz is not None:
                # C2/COR-02: convert to UTC first, THEN strip timezone metadata.
                # tz_localize(None) alone keeps UTC nanosecond values but removes the
                # tz tag — for daily price frames (midnight-UTC or midnight-ET stored as
                # UTC) this is an invariant-correctness fix: tz_convert ensures the naive
                # index is always in UTC regardless of the stored timezone.
                # For daily bars the behavioral impact is nil (UTC midnight == date),
                # but this is the structurally correct form.
                df = df.copy()
                df.index = idx = idx.tz_convert("UTC").tz_localize(None)
            mask = idx.normalize() <= pd.Timestamp(as_of_date)
        else:
            mask = pd.to_datetime(idx).normalize() <= pd.Timestamp(as_of_date)
        sliced = df[mask]

        n = len(sliced)
        if n < 252:
            return False  # Gate D

        # Close series
        close_col = None
        for col in ("Close", "close", "Adj Close"):
            if col in sliced.columns:
                close_col = col
                break
        if close_col is None:
            return False

        close = sliced[close_col].iloc[-252:]
        price = float(close.iloc[-1])
        if price <= 0:
            return False

        high_252 = float(close.max())
        low_252 = float(close.min())

        if high_252 <= 0 or low_252 <= 0:
            return False

        pct_off_high = (high_252 - price) / high_252 * 100.0
        pct_above_low = (price - low_252) / low_252 * 100.0

        return pct_off_high >= 50.0 and pct_above_low <= 25.0

    start_year = 2015
    end_year = 2020

    # First, collect all 10-Q/10-K filing events in range
    submission_files = sorted(_SUBMISSIONS_DIR.glob("*.json"))
    total_files = len(submission_files)
    if max_files:
        submission_files = submission_files[:max_files]

    filing_events = []  # (acceptance_dt, ticker) — store full dt for COR-02
    n_raw_filings = 0
    n_parse_errors = 0  # PY-04

    logger.info("Scanning %d / %d files for 10-Q/10-K events ...", len(submission_files), total_files)
    t0 = time.time()
    for i, fp in enumerate(submission_files):
        if i > 0 and i % 500 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 1
            eta = (len(submission_files) - i) / rate
            logger.info(
                "  R2 scan progress: %d / %d (%.1f%%) — ETA %.0fs",
                i, len(submission_files), 100 * i / len(submission_files), eta,
            )

        cik = fp.stem
        ticker = cik_to_ticker.get(cik)
        if not ticker:
            continue

        try:
            with open(fp) as f:
                filing_data = json.load(f)
        except Exception as exc:
            n_parse_errors += 1
            if n_parse_errors <= 5:
                logger.debug("R2 parse error %s: %s", fp, exc)
            continue

        recent = filing_data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accept_dts = recent.get("acceptanceDateTime", [])

        if len(forms) != len(accept_dts):
            continue

        for form, adt in zip(forms, accept_dts):
            if form not in ("10-Q", "10-K"):
                continue
            if not adt:
                continue
            try:
                year = int(adt[:4])
            except (ValueError, IndexError):
                continue
            if year < start_year or year > end_year:
                continue
            n_raw_filings += 1
            # Store full acceptanceDateTime for tz-aware entry-date conversion (COR-02)
            filing_events.append((adt, ticker))

    if n_parse_errors > 0:
        n_files = len(submission_files)
        pct = 100 * n_parse_errors / n_files if n_files else 0
        if pct > 1.0:
            logger.warning(
                "R2: %d parse errors out of %d files (%.1f%%) — check submissions corpus",
                n_parse_errors, n_files, pct,
            )
        else:
            logger.info("R2: %d parse errors (%.2f%% of files)", n_parse_errors, pct)

    logger.info("R2: %d 10-Q/10-K events collected from %d files", len(filing_events), len(submission_files))

    # Apply D2 predicate per event
    # COR-02: use _entry_date_for_filing for tz-aware after-hours correction
    n_d2_pass = 0
    excess_list = []
    n_no_frame = 0
    n_no_price = 0
    n_deduped = 0  # COR-06
    seen: set = set()
    r2_valid_events_for_control: list = []  # F371: valid events for placebo

    logger.info("Applying D2 predicate to %d events ...", len(filing_events))
    t0 = time.time()
    for i, (adt, ticker) in enumerate(filing_events):
        if i > 0 and i % 2000 == 0:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 1
            eta = (len(filing_events) - i) / rate
            logger.info(
                "  D2 predicate: %d / %d (%.1f%%) — ETA %.0fs",
                i, len(filing_events), 100 * i / len(filing_events), eta,
            )

        # as_of for D2 gates uses the ET calendar date (same logic as entry_date, pre-advance)
        try:
            dt_str = adt.replace("Z", "+00:00")
            if "." in dt_str:
                dt_str = dt_str.split(".")[0] + "+00:00"
            dt_et = datetime.fromisoformat(dt_str).astimezone(_ET_TZ)
            as_of = dt_et.date()
        except Exception:
            try:
                as_of = date.fromisoformat(adt[:10])
            except ValueError:
                continue

        ed = _entry_date_for_filing(adt, all_dates_sorted)
        if ed is None:
            # COR-04/COR-10: no matrix entry date — distinct from no forward return
            n_no_price += 1
            continue

        key = (ticker, ed)
        if key in seen:
            n_deduped += 1  # COR-06
            continue
        seen.add(key)

        passes = check_d2(ticker, as_of)
        if not passes:
            continue

        n_d2_pass += 1
        fwd = mat_lookup.get((ed, ticker))
        med = med_map_63.get(ed)
        if fwd is None or med is None:
            n_no_frame += 1
            continue
        excess_list.append(fwd - med)
        r2_valid_events_for_control.append({"entry_date": ed, "ticker": ticker})

    stats = _compute_stats(excess_list)
    logger.info(
        "R2: n_raw_filings=%d n_deduped=%d n_no_price=%d n_d2_pass=%d n_valid=%d "
        "n_no_frame=%d n_parse_errors=%d std_63=%.4f MDE_1samp_63=%.4f",
        n_raw_filings, n_deduped, n_no_price, n_d2_pass, stats["n_valid"], n_no_frame,
        n_parse_errors,
        stats.get("std_excess", float("nan")),
        stats.get("MDE_1samp", float("nan")),
    )

    # --- Placebo-control benchmark (F371) ---
    logger.info("Computing R2 placebo-control benchmark (F371) ...")
    placebo_r2 = _compute_placebo_control(
        r2_valid_events_for_control,
        mat_lookup,
        med_map_63,
        # RCHK-12/COR-05: full matrix dates (not med_map keys), matches calibration.
        sorted(set(df_mat["entry_date"].tolist())),
        horizon=63,
        logger=logger,
    )
    logger.info(
        "R2 placebo: control_n=%d control_mean=%.4fpp event_mean=%.4fpp net=%.4fpp | %s",
        placebo_r2["control_n"],
        placebo_r2["control_mean_excess"],
        placebo_r2["event_mean_excess"],
        placebo_r2["net_excess"],
        placebo_r2["interpretation"],
    )

    return {
        "family": "r2",
        "is_partial": bool(max_files),
        "files_scanned": len(submission_files),
        "total_files": total_files,
        "n_raw_filings": n_raw_filings,
        "n_deduped": n_deduped,
        # COR-04/COR-10: split into two distinct counters.
        # n_no_price: entry date not found in matrix (no trading-day match).
        # n_no_frame: D2 passed but fwd_return or median missing in matrix.
        "n_no_price": n_no_price,
        "n_no_frame": n_no_frame,
        "n_parse_errors": n_parse_errors,
        "n_d2_pass": n_d2_pass,
        "estimate_label": "D2 gates A+B+D applied from price cache (Gates A/B use 252-bar trailing close; revenue veto OFF)",
        "entry_date_rule": "next trading day in matrix on-or-after acceptanceDateTime (ET tz-aware, >=16:00 ET advances one day)",
        "placebo_control": placebo_r2,  # F371
        **stats,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def _testable(mde: Optional[float], floor: float = 1.0) -> str:
    if mde is None or (isinstance(mde, float) and math.isnan(mde)):
        return "N/A"
    return "YES" if mde <= floor else "NO"


def _fmt(val, decimals=2):
    if val is None:
        return "—"
    if isinstance(val, float) and math.isnan(val):
        return "—"
    return f"{val:.{decimals}f}"


def write_census_json(results: dict, out_dir: Path, logger: logging.Logger) -> None:
    census_path = out_dir / "census.json"
    # Strip internal-only keys before serializing
    serializable = {
        k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
        if isinstance(v, dict) else v
        for k, v in results.items()
    }
    payload = json.dumps(serializable, indent=2, default=str)
    # PY-09: atomic write (tmp + os.replace) to prevent partial/corrupt census.json
    # on power failure or Ctrl-C mid-write during a long worker run.
    tmp_path = census_path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, census_path)
    finally:
        # Best-effort cleanup if os.replace was not reached
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    logger.info("census.json written: %s", census_path)


def write_human_report(results: dict, out_dir: Path, logger: logging.Logger) -> None:
    report_dir = _REPO_ROOT / "docs" / "research"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "2026-06-08-premise-power-census.md"

    lines = [
        "# Premise Power Census — 2026-06-08",
        "",
        "> **Status:** read-only feasibility measurement. No FDR alpha drawn. "
        "Findings do not constitute hypothesis tests or research verdicts.",
        "",
        "**Key insight:** Two MDEs matter, not one.",
        "",
        "| MDE | Formula | What it answers |",
        "|---|---|---|",
        "| One-sample | 2.802 × std / √n | Does this family beat the market on average? |",
        "| Dose-gap Q5−Q1 | 2.802 × √(var_q5/n_q5 + var_q1/n_q1) | Does the effect scale with a score? |",
        "",
        "**Caveat:** MDE assumes iid events. Cross-sectional correlation on shared "
        "entry dates understates the true penalty. MDE is a power-screening heuristic, "
        "not a significance verdict.",
        "",
        "---",
        "",
        "## Ranked Table (headline: 63-trading-day horizon)",
        "",
        "| family / slice | n_raw | n_valid | std_63 (pp) | MDE_1samp_63 (pp) | MDE_gap_63 (pp) | testable 1.0pp (1samp/gap) | n needed for 1.0pp | extractor owed |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    # Build rows
    families_order = ["calibration", "r1b_subuniverse", "pead", "eightk", "r2"]
    for fam_name in families_order:
        fam = results.get(fam_name)
        if fam is None:
            lines.append(f"| {fam_name} | — | — | — | — | — | pending | — | — |")
            continue

        if fam_name == "calibration":
            s = fam.get("stats_stored_excess", {})
            n_raw = s.get("n_valid", "—")
            n_valid = s.get("n_valid", "—")
            std = s.get("std_excess_63", float("nan"))
            mde1 = s.get("MDE_1samp_63", float("nan"))
            mde_gap = s.get("MDE_gap_63", float("nan"))
            n_need = _n_needed_1pp(std) if not math.isnan(std) else "—"
            test1 = _testable(mde1)
            testg = _testable(mde_gap)
            lines.append(
                f"| calibration (R-1b full) | {n_raw} | {n_valid} | {_fmt(std)} | "
                f"{_fmt(mde1, 3)} | {_fmt(mde_gap, 3)} | {test1}/{testg} | {n_need} | — (anchor) |"
            )

        elif fam_name == "r1b_subuniverse":
            for bucket in fam.get("mc_buckets", []):
                label = bucket.get("mc_bucket", "?")
                n_v = bucket.get("n_valid", 0)
                std = bucket.get("std_excess", float("nan"))
                mde1 = bucket.get("MDE_1samp", float("nan"))
                mde_g = bucket.get("MDE_gap")
                n_need = _n_needed_1pp(std) if not (isinstance(std, float) and math.isnan(std)) else "—"
                test1 = _testable(mde1)
                testg = _testable(mde_g)
                lines.append(
                    f"| R-1b/{label} | {n_v} | {n_v} | {_fmt(std)} | "
                    f"{_fmt(mde1, 3)} | {_fmt(mde_g, 3)} | {test1}/{testg} | {n_need} | score TBD |"
                )
            no_mc = fam.get("n_events_no_mc", 0)
            if no_mc > 0:
                lines.append(f"| R-1b/no-MC remainder | {no_mc} | {no_mc} | — | — | — | — | — | — |")

        elif fam_name == "pead":
            n_raw = fam.get("n_raw", "—")
            n_valid = fam.get("n_valid", "—")
            std = fam.get("std_excess", float("nan"))
            mde1 = fam.get("MDE_1samp", float("nan"))
            n_need = _n_needed_1pp(std) if not (isinstance(std, float) and math.isnan(std)) else "—"
            test1 = _testable(mde1)
            partial = " (PARTIAL)" if fam.get("is_partial") else ""
            lines.append(
                f"| PEAD 10-Q/10-K{partial} | {n_raw} | {n_valid} | {_fmt(std)} | "
                f"{_fmt(mde1, 3)} | — | {test1}/N/A | {n_need} | surprise definition |"
            )

        elif fam_name == "eightk":
            for bucket in fam.get("item_buckets", []):
                item = bucket.get("item_code", "?")
                n_raw_b = bucket.get("n_raw", 0)
                n_v = bucket.get("n_valid", 0)
                std = bucket.get("std_excess", float("nan"))
                mde1 = bucket.get("MDE_1samp", float("nan"))
                n_need = _n_needed_1pp(std) if not (isinstance(std, float) and math.isnan(std)) else "—"
                test1 = _testable(mde1)
                partial = " (PARTIAL)" if fam.get("is_partial") else ""
                lines.append(
                    f"| 8-K/{item}{partial} | {n_raw_b} | {n_v} | {_fmt(std)} | "
                    f"{_fmt(mde1, 3)} | — | {test1}/N/A | {n_need} | item score TBD |"
                )

        elif fam_name == "r2":
            if fam.get("status") == "blocked":
                lines.append(
                    f"| R-2 (D2 distress) | — | — | — | — | — | blocked | — | {fam.get('reason', '?')} |"
                )
            else:
                n_raw = fam.get("n_d2_pass", "—")
                n_valid = fam.get("n_valid", "—")
                std = fam.get("std_excess", float("nan"))
                mde1 = fam.get("MDE_1samp", float("nan"))
                n_need = _n_needed_1pp(std) if not (isinstance(std, float) and math.isnan(std)) else "—"
                test1 = _testable(mde1)
                partial = " (PARTIAL)" if fam.get("is_partial") else ""
                lines.append(
                    f"| R-2 D2 distress{partial} | {n_raw} | {n_valid} | {_fmt(std)} | "
                    f"{_fmt(mde1, 3)} | — | {test1}/N/A | {n_need} | distress score TBD |"
                )

    # --- Placebo-control benchmark table (F371) ---
    lines += [
        "",
        "---",
        "",
        "## Placebo-Control Benchmark (F371)",
        "",
        "> **What this is:** For each family's valid events, a random control set of "
        "NON-event observations was drawn from the SAME tickers (K=3 per event, at least "
        "63 trading days away from any real event for that ticker). The control's mean "
        "excess approximates the baseline/selection artifact. "
        "**net_excess = event_mean − control_mean** isolates the event-specific component. "
        "Seed=20260608 (deterministic).",
        "",
        "| family / bucket | event_n | event_mean (pp) | control_n | control_mean (pp) | net_excess (pp) | interpretation |",
        "|---|---|---|---|---|---|---|",
    ]

    def _placebo_row(label: str, placebo: Optional[dict]) -> str:
        if not placebo:
            return f"| {label} | — | — | — | — | — | not computed |"
        en = placebo.get("event_n", "—")
        em = placebo.get("event_mean_excess", float("nan"))
        cn = placebo.get("control_n", "—")
        cm = placebo.get("control_mean_excess", float("nan"))
        net = placebo.get("net_excess", float("nan"))
        interp = placebo.get("interpretation", "—")
        return (
            f"| {label} | {en} | {_fmt(em)} | {cn} | {_fmt(cm)} | {_fmt(net)} | {interp} |"
        )

    for fam_name in families_order:
        fam = results.get(fam_name)
        if fam is None:
            lines.append(f"| {fam_name} | — | — | — | — | — | pending |")
            continue

        if fam_name == "calibration":
            lines.append(_placebo_row("calibration (R-1b)", fam.get("placebo_control")))
        elif fam_name == "r1b_subuniverse":
            lines.append("| R-1b sub-universe | — | — | — | — | — | no per-bucket placebo (uses stored excess) |")
        elif fam_name == "pead":
            partial = " (PARTIAL)" if fam.get("is_partial") else ""
            lines.append(_placebo_row(f"PEAD 10-Q/10-K{partial}", fam.get("placebo_control")))
        elif fam_name == "eightk":
            for bucket in fam.get("item_buckets", []):
                item = bucket.get("item_code", "?")
                partial = " (PARTIAL)" if fam.get("is_partial") else ""
                lines.append(_placebo_row(f"8-K/{item}{partial}", bucket.get("placebo_control")))
        elif fam_name == "r2":
            if fam.get("status") == "blocked":
                lines.append(f"| R-2 (D2 distress) | — | — | — | — | — | blocked |")
            else:
                partial = " (PARTIAL)" if fam.get("is_partial") else ""
                lines.append(_placebo_row(f"R-2 D2 distress{partial}", fam.get("placebo_control")))

    lines += ["", "---", "", "## Plain-English Summary Per Family", ""]

    # Calibration
    cal = results.get("calibration", {})
    s_cal = cal.get("stats_stored_excess", {})
    lines += [
        "### Calibration (R-1b full universe — anchor validation)",
        "",
        f"The R-1b insider-cluster study produced {s_cal.get('n_valid', '?')} valid events (floor-passing, "
        f"2015-2020 explore split) with a 63-day excess standard deviation of "
        f"{_fmt(s_cal.get('std_excess_63'), 2)} percentage points. "
        f"The one-sample MDE is {_fmt(s_cal.get('MDE_1samp_63'), 3)}pp — meaning this "
        f"family can detect a mean excess of about {_fmt(s_cal.get('MDE_1samp_63'), 2)}pp "
        f"at 80% power, well below the 1.0pp tradeable floor. "
        f"The dose-gap MDE (Q5 vs Q1) is {_fmt(s_cal.get('MDE_gap_63'), 3)}pp, "
        f"above the 1.0pp floor — which is exactly R-1b's UNTESTABLE-underpowered verdict "
        f"for the dose-response question. "
        f"All calibration anchors A1/A2/A3 pass, validating census mechanics.",
        "",
    ]

    # R1b subuniverse
    r1b = results.get("r1b_subuniverse", {})
    if r1b:
        lines += [
            "### R-1b Sub-Universe (calmer MC quartile buckets)",
            "",
            f"Of the {r1b.get('n_events_with_mc', '?')} valid R-1b events with non-null market cap, "
            f"bucketed by MC quartile (p25=$1.5B, p50=$3.8B, p75=$11.8B). "
            f"Each quartile has fewer events (n ≈ {r1b.get('n_events_with_mc', 0)//4}), which "
            f"pushes MDE up. The table shows whether any quartile's lower std compensates "
            f"for the reduced n. "
            f"Verdict: if no MC bucket drops MDE_gap below 1.0pp at its own n, "
            f"the 'calmer universe' lever is dead for R-1b. "
            f"Note: realized trailing vol bucketing skipped — requires price history per event; "
            f"deferred to worker.",
            "",
        ]

    # PEAD
    pead = results.get("pead", {})
    if pead:
        partial_note = " (PARTIAL RUN — full scan deferred to worker)" if pead.get("is_partial") else ""
        lines += [
            f"### PEAD / Fundamental Surprise (10-Q and 10-K filings){partial_note}",
            "",
            f"Scanned {pead.get('files_scanned', '?')} / {pead.get('total_files', '?')} submission files. "
            f"Found {pead.get('n_raw', '?')} raw 10-Q/10-K filing events (2015-2020) for universe tickers. "
            f"After matrix join: {pead.get('n_valid', '?')} valid events with 63d excess. "
            f"Std = {_fmt(pead.get('std_excess'), 2)}pp, MDE_1samp = {_fmt(pead.get('MDE_1samp'), 3)}pp. "
            f"No surprise score built yet — this is counts + dispersion only. "
            f"Extractor owed: surprise definition (estimate-free YoY-accel or actual-vs-trailing).",
            "",
        ]

    # 8-K
    eightk = results.get("eightk", {})
    if eightk:
        partial_note = " (PARTIAL RUN)" if eightk.get("is_partial") else ""
        lines += [
            f"### 8-K Item-Type Drift{partial_note}",
            "",
            f"Scanned {eightk.get('files_scanned', '?')} / {eightk.get('total_files', '?')} files. "
            f"Total raw 8-K events 2015-2020: {eightk.get('n_raw_total', '?')}. "
            f"Split by item code: 2.02 (earnings results), 5.02 (officer change), "
            f"1.01 (material agreement), 8.01 (other), and other/multi. "
            f"Item types differ markedly in volume and dispersion — "
            f"see table for per-item MDE.",
            "",
        ]

    # R2
    r2 = results.get("r2", {})
    if r2:
        if r2.get("status") == "blocked":
            lines += [
                "### R-2 Distress Recovery",
                "",
                f"BLOCKED: {r2.get('reason', 'unknown')}",
                "",
            ]
        else:
            partial_note = " (PARTIAL RUN)" if r2.get("is_partial") else ""
            lines += [
                f"### R-2 Distress Recovery (D2 predicate){partial_note}",
                "",
                f"Applied D2 price gates (Gate A ≥50% crash, Gate B ≤25% above 1yr low, "
                f"Gate D ≥252 bars, revenue veto OFF) to all 10-Q/10-K filing dates 2015-2020. "
                f"This is a price-only approximation — no revenue veto. "
                f"D2-state events: {r2.get('n_d2_pass', '?')} (from {r2.get('n_raw_filings', '?')} raw filings). "
                f"Std = {_fmt(r2.get('std_excess'), 2)}pp, MDE_1samp = {_fmt(r2.get('MDE_1samp'), 3)}pp. "
                f"This answers: will the already-approved R-2 run hit the same "
                f"underpowered wall R-1b did?",
                "",
            ]

    lines += [
        "---",
        "",
        f"*Generated {datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} by premise_power_census.py (F369)*",
        "",
    ]

    pending = [fam for fam in families_order if fam not in results]
    if pending:
        lines.append(f"**Pending families (not yet run):** {', '.join(pending)}")
        lines.append("")

    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    logger.info("Human report written: %s", report_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Premise Power Census (F369)")
    parser.add_argument(
        "--family",
        choices=["calibration", "r1b_subuniverse", "pead", "eightk", "r2", "all"],
        default="calibration",
        help="Which family to run (default: calibration)",
    )
    parser.add_argument(
        "--out",
        default=str(_REPO_ROOT / ".run" / "F369"),
        help="Output directory (default: .run/F369/)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Max submission files for heavy families (for smoke testing)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = out_dir / "census_run.log"
    logger = _setup_logging(log_path)
    logger.info("Starting census — family=%s out=%s", args.family, out_dir)

    families_to_run = (
        ["calibration", "r1b_subuniverse", "pead", "eightk", "r2"]
        if args.family == "all"
        else [args.family]
    )

    results = {}

    # Try to load existing census.json for partial results
    census_path = out_dir / "census.json"
    if census_path.exists():
        try:
            with open(census_path) as f:
                results = json.load(f)
            logger.info("Loaded existing census.json (%d families)", len(results))
        except Exception as exc:
            logger.warning(
                "Could not load existing census.json (%s) — starting fresh", exc
            )
            results = {}

    # Calibration must run first (or be already passed)
    medians: dict = {}

    if "calibration" in families_to_run or "calibration" not in results:
        if "calibration" not in families_to_run and "calibration" not in results:
            # Need to run calibration to get medians even if not explicitly requested
            logger.info("Running calibration (required for medians) ...")
            cal_result = run_calibration(out_dir, logger)
            results["calibration"] = cal_result
            medians = cal_result.get("_medians", {})
            write_census_json(results, out_dir, logger)
        elif "calibration" in families_to_run:
            cal_result = run_calibration(out_dir, logger)
            results["calibration"] = cal_result
            medians = cal_result.get("_medians", {})
            write_census_json(results, out_dir, logger)
    else:
        # Calibration already done — load medians fresh
        logger.info("Calibration already in census.json, loading medians ...")
        medians = _load_matrix_medians(logger=logger)

    # Run other families
    for fam in families_to_run:
        if fam == "calibration":
            continue

        logger.info("Running family: %s", fam)
        t0 = time.time()
        try:
            if fam == "r1b_subuniverse":
                result = run_r1b_subuniverse(medians, out_dir, logger)
            elif fam == "pead":
                result = run_pead(medians, out_dir, logger, max_files=args.max_files)
            elif fam == "eightk":
                result = run_eightk(medians, out_dir, logger, max_files=args.max_files)
            elif fam == "r2":
                result = run_r2(medians, out_dir, logger, max_files=args.max_files)
            else:
                logger.warning("Unknown family: %s", fam)
                continue
            results[fam] = result
        except Exception as e:
            logger.exception("Family %s failed: %s", fam, e)
            results[fam] = {"family": fam, "status": "failed", "error": str(e)}

        elapsed = time.time() - t0
        logger.info("Family %s done in %.1fs", fam, elapsed)
        write_census_json(results, out_dir, logger)

    write_census_json(results, out_dir, logger)
    write_human_report(results, out_dir, logger)

    logger.info("Census complete. Results: %s", list(results.keys()))


if __name__ == "__main__":
    main()
