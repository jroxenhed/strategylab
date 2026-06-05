"""REGIME-TEST Unit 5 — pre-registered regime → forward-base-rate test.

Charter: .run/REGIME-TEST/charter.md
  (FROZEN, sha256 d5da66aa48f457ab6d7a721d46070afc01d820fd1a3198e36c37f9852c9319e1)

This module is the test harness for the single falsifiable hypothesis of the
charter (§3): does a point-in-time, price-only regime label at a cohort's
``as_of`` date carry differential information about that cohort's FORWARD BASE
RATE on universe v2, out of time?

It does NOT claim an edge.  A CONFIRMED verdict means only "the pre-registered
rank ordering of forward base rates by regime survived an out-of-time confirm
window."

------------------------------------------------------------------------------
DESIGN DECISIONS (resolved + documented per dispatch contract)
------------------------------------------------------------------------------

1. DIRECT computation, not a full run_validation() re-execution.
   The charter §3/§4 outcome variable is the *universe-v2 cohort forward-return
   base rate* = the fraction of universe-v2 names whose forward return beats the
   cohort-matched null median, at 21/63/126 td.  We compute this DIRECTLY per
   cohort: a seeded stratified random sample of universe-v2 names, forward
   returns via the v2 bar-counting outcome math.  Rationale: run_validation is a
   filter+null pipeline keyed to a candidate SOURCE; the regime test has no
   candidate selection — the "cohort" IS universe-v2 itself.  Re-using the whole
   pipeline would force a synthetic always-pass source and discard most of its
   output.  Direct computation is faithful to the charter's exact definition and
   far cheaper.

2. IMPORT, not reimplement, the outcome math.  The bar-counting forward-return
   engine (``_bar_counted_forward_returns``), the entry-bar resolver
   (``_first_trading_close_on_or_after``), the F332 memoized loader factory
   (``_make_memoized_loader``), the quarterly schedule (``_quarterly_as_of_dates``),
   the Wilson interval (``wilson_ci``) and the horizon constant
   (``V2_HORIZONS_TRADING_DAYS``) are all imported verbatim from
   ``turnaround_validation``.  They were verified cleanly importable, so NO
   extract-and-refactor was needed (the dispatch allowed it only as a fallback).

3. COHORT-MATCHED NULL = a SEPARATE seeded universe-v2 draw at the same as_of.
   Charter §3 NOTE: "the null is drawn from the same cohort, a uniform tape-wide
   lift is partially differenced out by construction ... H1 therefore predicts
   that regime shifts the *share of names beating their own-cohort median*."
   To make the base rate a non-trivial (not identically 0.5) shape statistic, we
   draw TWO disjoint seeded samples per cohort from universe-v2: an EVAL set and
   a NULL set.  Each eval name's forward return is compared to the NULL set's
   median forward return at that horizon; hit = excess > 0.  This mirrors the v2
   engine exactly, where signal events are scored against a *separately drawn*
   random-universe null cohort's median (``_cohort_null_median``) — not against
   their own median.  Both draws come from universe-v2, so the base rate is ~0.5
   in expectation but its level/shape per cohort carries the regime signal H1
   predicts.  Disjoint + seeded ⇒ deterministic and free of the self-comparison
   artifact the charter calls out.

4. SAMPLING: seeded (per-cohort seed derived from a fixed run seed + the as_of
   ordinal), stratified by first-letter bucket of the qualifying ticker list to
   spread the draw across the alphabetically-sorted universe (avoids an all-"A"
   sample), sample size = ``_SAMPLE_PER_SIDE`` (default 200) per side or the full
   qualifying set if smaller.  200/side keeps per-cohort Wilson noise honest at
   universe scale while bounding the breadth-style fetch cost; it is documented,
   not tuned to any outcome.

5. EXPLORE / CONFIRM ISOLATION.  Each window is computed by an INDEPENDENT
   invocation (``--window explore`` | ``--window confirm``).  An explore
   invocation NEVER reads, computes, or prints any confirm cohort: the only
   as_of dates the loader is ever asked about are this window's cohorts (tests
   assert this by spying the loader's requested dates).  The orchestrator runs
   confirm via a sealed agent after the charter is frozen.

6. VERDICTS (charter §4) and the LEDGER (charter §5, exactly 6 comparisons) are
   defined below; no extra contrast is emitted.

Output artifact: .run/REGIME-TEST/<window>-result.json (gitignored staging,
deterministic) + explore writes a human-readable section to
.run/REGIME-TEST/explore-result.json's ``human_readable`` field.  The verdict
DOC comes later (separate dispatch).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import statistics
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Paths + provenance
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
_RUN_DIR = _REPO_ROOT / ".run" / "REGIME-TEST"
_CHARTER_PATH = _RUN_DIR / "charter.md"
_REGIME_STATES_PATH = _BACKEND_DIR / "data" / "turnaround" / "regime_states.json"

# Frozen charter sha256 (charter §0; matches regime_state.py).
_CHARTER_SHA256 = "d5da66aa48f457ab6d7a721d46070afc01d820fd1a3198e36c37f9852c9319e1"

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# IMPORT (design decision #2) — outcome math reused verbatim, never reimplemented.
from turnaround_validation import (  # noqa: E402
    V2_HORIZONS_TRADING_DAYS,
    PriceFrameCache,
    _bar_counted_forward_returns,
    _first_trading_close_on_or_after,
    _make_memoized_loader,
    _quarterly_as_of_dates,
    wilson_ci,
)

logger = logging.getLogger("regime_validation")

# ---------------------------------------------------------------------------
# Charter constants (FROZEN)
# ---------------------------------------------------------------------------

# §4 windows (frozen) — inclusive year ranges.
WINDOWS: dict[str, tuple[int, int]] = {
    "explore": (2015, 2020),  # 24 quarterly cohorts
    "confirm": (2021, 2024),  # 16 quarterly cohorts
}

# §2 states (WARMUP excluded from joins).
_REGIME_STATES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS")

# §3 primary horizon + all pre-registered horizons.
_PRIMARY_HORIZON = 63
assert set(V2_HORIZONS_TRADING_DAYS) == {21, 63, 126}

# §4 UNTESTABLE rule: a state with < this many cohort observations is UNTESTABLE.
_MIN_COHORTS = 3

# §4 (c): per-cohort direction-agreement threshold for CONFIRMED.
_DIRECTION_AGREEMENT_THRESHOLD = 0.60

# §5 program alpha budget — this test's share for the primary contrast.
_ALPHA_H1 = 0.0125
# z for the §4(b) cohort-clustered CI evaluated at the primary alpha.  Two-sided
# 0.0125 → z ≈ 2.498 (kept here so the distinguishability test uses the charter's
# budgeted alpha, not a nominal 0.05).
_Z_ALPHA_H1 = 2.4977

# Sampling (design decision #4) — documented, not tuned to outcome.
_SAMPLE_PER_SIDE = 200
_RUN_SEED = 20260605  # fixed run seed (charter authoring date); determinism anchor.

# Outcome span padding (charter §6): warmup pad before first cohort, 126 td after
# the last.  We reuse the validation loader's own span math via _make_memoized_loader,
# so we only need to supply the loader's year/lookback knobs.
_LOW_LOOKBACK_YEARS = 2
_HORIZON_MONTHS = 7  # ≥126 td ≈ 6 months; 7 gives the loader a safe forward pad.


# ---------------------------------------------------------------------------
# §5 LEDGER — exactly the 6 pre-registered comparisons (no extras).
# ---------------------------------------------------------------------------
# Each entry: (id, hypothesis, kind, payload, horizon).
#   kind "pair"     : payload (hi_state, lo_state); predicts base_rate[hi] > base_rate[lo]
#                     (or >= for the RISK_OFF/STRESS link — encoded via ``ge``).
#   kind "lowest"   : payload state; predicts that state is the lowest of the four.
#   kind "ordering" : payload the full H1 chain; predicts the H1 rank holds at horizon.
LEDGER: tuple[dict, ...] = (
    {"id": 1, "hypothesis": "H1", "kind": "pair",
     "hi": "RISK_ON", "lo": "NEUTRAL", "ge": False, "horizon": 63},
    {"id": 2, "hypothesis": "H1", "kind": "pair",
     "hi": "NEUTRAL", "lo": "RISK_OFF", "ge": False, "horizon": 63},
    {"id": 3, "hypothesis": "H1", "kind": "pair",
     "hi": "RISK_OFF", "lo": "STRESS", "ge": True, "horizon": 63},
    {"id": 4, "hypothesis": "H2", "kind": "lowest",
     "state": "STRESS", "horizon": 63},
    {"id": 5, "hypothesis": "H3", "kind": "ordering",
     "chain": ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"), "horizon": 21},
    {"id": 6, "hypothesis": "H3", "kind": "ordering",
     "chain": ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"), "horizon": 126},
)
assert len(LEDGER) == 6, "charter §5 locks the test count at 6"


# ---------------------------------------------------------------------------
# Charter / artifact provenance
# ---------------------------------------------------------------------------

def charter_sha256(path: Optional[Path] = None) -> Optional[str]:
    """Return the sha256 of the charter file, or None if absent."""
    p = path or _CHARTER_PATH
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _atomic_write_json(path: Path, obj: object) -> None:
    """Write JSON deterministically (sorted keys) via tmp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Universe-v2 constituent list (same build_universe + UNIVERSE_V2 as the harness)
# ---------------------------------------------------------------------------

def get_universe_v2_tickers(limit: Optional[int] = None) -> list[tuple[str, str]]:
    """Return universe-v2 (ticker, cik) pairs via build_universe + F319 hygiene.

    Mirrors regime_state._get_universe_v2_tickers (same universe the breadth
    feature is computed over).  Price/volume floors are applied per-bar at
    sampling time, not here (build_universe applies only hygiene).
    """
    from turnaround import build_universe  # noqa: E402
    import edgar  # noqa: E402

    ticker_cik_map = edgar.fetch_universe()
    pairs = build_universe(ticker_cik_map, params=None)
    if limit is not None:
        pairs = pairs[:limit]
    return pairs


# ---------------------------------------------------------------------------
# Regime join (charter §3): cohort as_of → regime state at that date
# ---------------------------------------------------------------------------

def load_regime_states(path: Optional[Path] = None) -> dict:
    """Load regime_states.json (runtime read).  Tests pass a synthetic dict via
    ``regime_states`` to the cohort routines instead of this."""
    p = path or _REGIME_STATES_PATH
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def regime_state_for_as_of(as_of: date, regime_states: dict) -> Optional[str]:
    """Return the regime state at ``as_of`` (charter §3: state at the cohort date).

    The states map is keyed by trading-date ISO strings.  ``as_of`` (the 15th)
    may not be a trading day, so we take the most-recent state at a date <= as_of
    (charter §1: "A feature value at date t is the most-recent available bar <= t").
    WARMUP / absent dates return None (excluded from joins).
    """
    states = regime_states.get("states", regime_states)
    as_of_iso = as_of.isoformat()
    # Most-recent keyed date <= as_of.
    candidates = [k for k in states if k <= as_of_iso]
    if not candidates:
        return None
    key = max(candidates)
    entry = states[key]
    state = entry.get("state") if isinstance(entry, dict) else entry
    if state in _REGIME_STATES:
        return state
    return None  # WARMUP / absent


# ---------------------------------------------------------------------------
# Per-cohort base rate (charter §3/§4) — DIRECT computation
# ---------------------------------------------------------------------------

def _stratified_seeded_sample(
    pairs: list[tuple[str, str]],
    n: int,
    seed: int,
) -> list[tuple[str, str]]:
    """Deterministic stratified sample of ``n`` (ticker, cik) pairs.

    Stratifies by ticker first-letter bucket so the draw spans the alphabetically
    sorted universe rather than clustering at the front.  Seeded ⇒ reproducible.
    Returns the full list (sorted) if len(pairs) <= n.
    """
    import random

    if n <= 0 or not pairs:
        return []
    if len(pairs) <= n:
        return sorted(pairs)

    rng = random.Random(seed)
    # Bucket by first character.
    buckets: dict[str, list[tuple[str, str]]] = {}
    for p in pairs:
        buckets.setdefault(p[0][:1], []).append(p)
    bucket_keys = sorted(buckets)
    # Proportional allocation across buckets.
    total = len(pairs)
    chosen: list[tuple[str, str]] = []
    for bk in bucket_keys:
        b = sorted(buckets[bk])
        take = max(1, round(n * len(b) / total))
        take = min(take, len(b))
        chosen.extend(rng.sample(b, take))
    # Trim / pad to exactly n deterministically.
    chosen = sorted(set(chosen))
    if len(chosen) > n:
        chosen = rng.sample(chosen, n)
    return sorted(chosen)


def _forward_return(
    frame,
    as_of: date,
    horizon: int,
) -> Optional[float]:
    """Bar-counted forward return at ``horizon`` for a name, entered on the first
    trading bar >= as_of.  Uses the IMPORTED v2 outcome math."""
    if frame is None:
        return None
    resolved = _first_trading_close_on_or_after(frame, as_of)
    if resolved is None:
        return None
    entry_date, entry_close = resolved
    fwd = _bar_counted_forward_returns(
        frame, entry_date, entry_close, horizons=V2_HORIZONS_TRADING_DAYS,
        direction="long",
    )
    return fwd.get(horizon)


def cohort_base_rates(
    as_of: date,
    pairs: list[tuple[str, str]],
    bars_loader: Callable[[str], object],
    *,
    sample_per_side: int = _SAMPLE_PER_SIDE,
    run_seed: int = _RUN_SEED,
) -> dict[int, Optional[dict]]:
    """Compute the universe-v2 cohort forward base rate at each horizon.

    DIRECT computation (design decision #1/#3):
      - draw two DISJOINT seeded stratified samples from universe-v2 at this as_of:
        an EVAL set and a NULL set;
      - for each horizon, the cohort-matched null median = median forward return of
        the NULL set; hit = (eval name forward return - null median) > 0;
      - base rate = hits / evaluable eval names.

    Returns {horizon: {"base_rate", "n", "hits", "null_median",
                       "ci_low", "ci_high"} or None}.  None ⇒ no evaluable names.
    """
    cohort_seed = (run_seed * 1_000_003 + as_of.toordinal()) & 0x7FFFFFFF
    # Disjoint eval / null draws: sample 2*per_side then split.
    big = _stratified_seeded_sample(pairs, sample_per_side * 2, cohort_seed)
    if len(big) >= 2:
        mid = len(big) // 2
        eval_set = big[:mid]
        null_set = big[mid:]
    else:
        eval_set, null_set = big, big

    eval_frames = {t: bars_loader(t) for t, _ in eval_set}
    null_frames = {t: bars_loader(t) for t, _ in null_set}

    out: dict[int, Optional[dict]] = {}
    for h in V2_HORIZONS_TRADING_DAYS:
        null_returns = [
            r for r in (_forward_return(f, as_of, h) for f in null_frames.values())
            if r is not None
        ]
        if not null_returns:
            out[h] = None
            continue
        null_median = statistics.median(null_returns)
        eval_returns = [
            r for r in (_forward_return(f, as_of, h) for f in eval_frames.values())
            if r is not None
        ]
        n = len(eval_returns)
        if n == 0:
            out[h] = None
            continue
        hits = sum(1 for r in eval_returns if (r - null_median) > 0)
        ci_low, ci_high = wilson_ci(hits, n)
        out[h] = {
            "base_rate": hits / n,
            "n": n,
            "hits": hits,
            "null_median": null_median,
            "ci_low": ci_low,
            "ci_high": ci_high,
        }
    return out


# ---------------------------------------------------------------------------
# Window computation: cohorts → per-state base-rate distributions
# ---------------------------------------------------------------------------

def compute_window(
    window: str,
    regime_states: dict,
    pairs: list[tuple[str, str]],
    bars_loader: Callable[[str], object],
    *,
    sample_per_side: int = _SAMPLE_PER_SIDE,
    run_seed: int = _RUN_SEED,
) -> dict:
    """Compute one window (explore | confirm) IN ISOLATION.

    Only this window's quarterly cohorts are ever produced; the loader is asked
    only about this window's as_of dates (explore/confirm isolation, design #5).

    Returns a deterministic dict: per-cohort records (as_of, state, per-horizon
    base rates) plus per-state aggregates.
    """
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}; expected one of {sorted(WINDOWS)}")
    start_year, end_year = WINDOWS[window]
    as_of_dates = _quarterly_as_of_dates(start_year, end_year)

    cohorts: list[dict] = []
    for as_of in as_of_dates:
        state = regime_state_for_as_of(as_of, regime_states)
        rates = cohort_base_rates(
            as_of, pairs, bars_loader,
            sample_per_side=sample_per_side, run_seed=run_seed,
        )
        cohorts.append({
            "as_of": as_of.isoformat(),
            "state": state,  # None ⇒ WARMUP/absent (excluded from joins)
            "base_rates": {
                str(h): rates[h] for h in V2_HORIZONS_TRADING_DAYS
            },
        })

    # Per-state, per-horizon distribution of per-cohort base rates (cohort is the
    # unit of inference — charter §4).  Only joined (non-None) states contribute.
    per_state: dict[str, dict[int, list[float]]] = {
        s: {h: [] for h in V2_HORIZONS_TRADING_DAYS} for s in _REGIME_STATES
    }
    per_state_cohorts: dict[str, int] = {s: 0 for s in _REGIME_STATES}
    for c in cohorts:
        s = c["state"]
        if s not in _REGIME_STATES:
            continue
        per_state_cohorts[s] += 1
        for h in V2_HORIZONS_TRADING_DAYS:
            cell = c["base_rates"][str(h)]
            if cell is not None:
                per_state[s][h].append(cell["base_rate"])

    aggregates = _aggregate_states(per_state)

    return {
        "window": window,
        "year_range": [start_year, end_year],
        "charter_sha256": _CHARTER_SHA256,
        "primary_horizon": _PRIMARY_HORIZON,
        "alpha_h1": _ALPHA_H1,
        "sample_per_side": sample_per_side,
        "run_seed": run_seed,
        "cohorts": cohorts,
        "per_state_cohort_counts": per_state_cohorts,
        "state_aggregates": aggregates,
    }


def _aggregate_states(
    per_state: dict[str, dict[int, list[float]]],
) -> dict[str, dict[str, dict]]:
    """Per-state, per-horizon aggregate: mean base rate + cohort-clustered CI.

    The CI is on the MEAN of per-cohort base rates (cohorts are the cluster —
    charter §4), evaluated at the primary alpha (z = _Z_ALPHA_H1).  When < 2
    cohorts, the interval is degenerate (mean, mean) and flagged.
    """
    out: dict[str, dict[str, dict]] = {}
    for s in _REGIME_STATES:
        out[s] = {}
        for h in V2_HORIZONS_TRADING_DAYS:
            vals = per_state[s][h]
            n = len(vals)
            if n == 0:
                out[s][str(h)] = {"n_cohorts": 0, "mean": None,
                                  "ci_low": None, "ci_high": None}
                continue
            mean = statistics.mean(vals)
            if n >= 2:
                sd = statistics.stdev(vals)
                se = sd / (n ** 0.5)
                half = _Z_ALPHA_H1 * se
            else:
                half = 0.0
            out[s][str(h)] = {
                "n_cohorts": n,
                "mean": mean,
                "ci_low": max(0.0, mean - half),
                "ci_high": min(1.0, mean + half),
            }
    return out


# ---------------------------------------------------------------------------
# §4 VERDICTS — exactly the 6 ledgered comparisons
# ---------------------------------------------------------------------------

def _state_mean(aggregates: dict, state: str, horizon: int) -> Optional[float]:
    cell = aggregates.get(state, {}).get(str(horizon))
    return cell["mean"] if cell else None


def _state_n(counts: dict, state: str) -> int:
    return int(counts.get(state, 0))


def _cohort_direction_agreement(
    cohorts: list[dict],
    hi: str,
    lo: str,
    horizon: int,
) -> Optional[float]:
    """Fraction of cohort PAIRS supporting hi > lo.

    Per charter §4(c): "≥60% of confirm cohorts individually rank in the
    predicted direction for the contrast".  With cohorts each carrying a single
    state, "per-cohort direction" is evaluated by comparing each hi-state cohort's
    base rate against the lo-state mean (the contrast's reference level): a hi
    cohort agrees if it exceeds the lo-state mean; a lo cohort agrees if it falls
    below the hi-state mean.  Returns agreement fraction over the union of hi/lo
    cohorts, or None if either side is empty.
    """
    hi_rates = [
        c["base_rates"][str(horizon)]["base_rate"]
        for c in cohorts
        if c["state"] == hi and c["base_rates"][str(horizon)] is not None
    ]
    lo_rates = [
        c["base_rates"][str(horizon)]["base_rate"]
        for c in cohorts
        if c["state"] == lo and c["base_rates"][str(horizon)] is not None
    ]
    if not hi_rates or not lo_rates:
        return None
    hi_mean = statistics.mean(hi_rates)
    lo_mean = statistics.mean(lo_rates)
    agree = sum(1 for r in hi_rates if r > lo_mean)
    agree += sum(1 for r in lo_rates if r < hi_mean)
    total = len(hi_rates) + len(lo_rates)
    return agree / total if total else None


def _distinguishable(aggregates: dict, hi: str, lo: str, horizon: int, ge: bool) -> bool:
    """Charter §4(b): hi/lo CIs do not overlap in the predicted direction at the
    primary alpha.  For a ``>=`` link, distinguishability is satisfied if hi is
    not below lo beyond CI (i.e. hi_ci_low >= lo_ci_high OR they overlap with hi
    mean >= lo mean — the >= link only needs non-reversal)."""
    hi_cell = aggregates.get(hi, {}).get(str(horizon))
    lo_cell = aggregates.get(lo, {}).get(str(horizon))
    if not hi_cell or not lo_cell:
        return False
    if hi_cell["mean"] is None or lo_cell["mean"] is None:
        return False
    if ge:
        # >= link: distinguishable if hi is not significantly BELOW lo.
        return hi_cell["ci_high"] >= lo_cell["ci_low"]
    # strict >: hi CI low above lo CI high.
    return hi_cell["ci_low"] > lo_cell["ci_high"]


def evaluate_pair(comparison: dict, window_result: dict) -> dict:
    """Evaluate an H1 pair contrast (hi > lo, or hi >= lo) → verdict dict."""
    hi, lo = comparison["hi"], comparison["lo"]
    ge = comparison.get("ge", False)
    horizon = comparison["horizon"]
    counts = window_result["per_state_cohort_counts"]
    aggregates = window_result["state_aggregates"]

    # UNTESTABLE if either required state < 3 cohorts.
    if _state_n(counts, hi) < _MIN_COHORTS or _state_n(counts, lo) < _MIN_COHORTS:
        return _verdict(comparison, "UNTESTABLE",
                        reason=f"{hi}={_state_n(counts, hi)} or "
                               f"{lo}={_state_n(counts, lo)} cohorts < {_MIN_COHORTS}")

    hi_mean = _state_mean(aggregates, hi, horizon)
    lo_mean = _state_mean(aggregates, lo, horizon)
    if hi_mean is None or lo_mean is None:
        return _verdict(comparison, "UNTESTABLE",
                        reason="missing aggregate mean (no evaluable cohorts)")

    direction_ok = hi_mean >= lo_mean if ge else hi_mean > lo_mean
    if not direction_ok:
        return _verdict(comparison, "REVERSED",
                        reason=f"point estimate {hi}={hi_mean:.4f} "
                               f"{'<' if not ge else '<'} {lo}={lo_mean:.4f}",
                        hi_mean=hi_mean, lo_mean=lo_mean)

    distinguish = _distinguishable(aggregates, hi, lo, horizon, ge)
    agreement = _cohort_direction_agreement(window_result["cohorts"], hi, lo, horizon)
    agreement_ok = agreement is not None and agreement >= _DIRECTION_AGREEMENT_THRESHOLD

    if distinguish and agreement_ok:
        verdict = "CONFIRMED"
    else:
        verdict = "WEAKENED"
    return _verdict(comparison, verdict,
                    reason=f"direction held; distinguishable={distinguish}; "
                           f"agreement={agreement}",
                    hi_mean=hi_mean, lo_mean=lo_mean,
                    distinguishable=distinguish, direction_agreement=agreement)


def evaluate_lowest(comparison: dict, window_result: dict) -> dict:
    """Evaluate H2: ``state`` is the lowest of the four at the horizon."""
    state = comparison["state"]
    horizon = comparison["horizon"]
    counts = window_result["per_state_cohort_counts"]
    aggregates = window_result["state_aggregates"]

    # UNTESTABLE if any of the four states is < 3 cohorts (the four-way claim
    # needs all four present).
    short = [s for s in _REGIME_STATES if _state_n(counts, s) < _MIN_COHORTS]
    if short:
        return _verdict(comparison, "UNTESTABLE",
                        reason=f"states < {_MIN_COHORTS} cohorts: {short}")

    means = {s: _state_mean(aggregates, s, horizon) for s in _REGIME_STATES}
    if any(m is None for m in means.values()):
        return _verdict(comparison, "UNTESTABLE", reason="missing aggregate mean")

    lowest = min(means, key=lambda s: means[s])
    if lowest != state:
        return _verdict(comparison, "REVERSED",
                        reason=f"lowest is {lowest} ({means[lowest]:.4f}), "
                               f"not {state} ({means[state]:.4f})")
    # Distinguishability: stress CI low not overlapping the second-lowest CI high.
    others = sorted((means[s], s) for s in _REGIME_STATES if s != state)
    second_lowest = others[0][1]
    distinguish = _distinguishable(aggregates, second_lowest, state, horizon, ge=False)
    verdict = "CONFIRMED" if distinguish else "WEAKENED"
    return _verdict(comparison, verdict,
                    reason=f"{state} lowest; distinguishable vs "
                           f"{second_lowest}={distinguish}",
                    distinguishable=distinguish)


def evaluate_ordering(comparison: dict, window_result: dict) -> dict:
    """Evaluate H3: the H1 rank direction holds at ``horizon`` (point estimate)."""
    chain = comparison["chain"]
    horizon = comparison["horizon"]
    counts = window_result["per_state_cohort_counts"]
    aggregates = window_result["state_aggregates"]

    short = [s for s in chain if _state_n(counts, s) < _MIN_COHORTS]
    if short:
        return _verdict(comparison, "UNTESTABLE",
                        reason=f"states < {_MIN_COHORTS} cohorts: {short}")

    means = [_state_mean(aggregates, s, horizon) for s in chain]
    if any(m is None for m in means):
        return _verdict(comparison, "UNTESTABLE", reason="missing aggregate mean")

    # Direction holds if the chain is (weakly, for the last >= link) descending.
    descending = all(means[i] >= means[i + 1] for i in range(len(means) - 1))
    if not descending:
        # Reversed if the OVERALL direction is opposite (first < last).
        if means[0] < means[-1]:
            return _verdict(comparison, "REVERSED",
                            reason=f"{chain[0]}={means[0]:.4f} < "
                                   f"{chain[-1]}={means[-1]:.4f}")
        return _verdict(comparison, "WEAKENED",
                        reason="rank not fully monotone at this horizon")
    return _verdict(comparison, "CONFIRMED",
                    reason="H1 rank direction holds (point estimate)")


def _verdict(comparison: dict, verdict: str, **extra) -> dict:
    rec = {
        "id": comparison["id"],
        "hypothesis": comparison["hypothesis"],
        "kind": comparison["kind"],
        "horizon": comparison["horizon"],
        "verdict": verdict,
    }
    rec.update(extra)
    return rec


def evaluate_ledger(window_result: dict) -> list[dict]:
    """Evaluate exactly the 6 pre-registered comparisons (charter §5).

    Returns a list of 6 verdict dicts, in ledger order.  No extra contrast.
    """
    verdicts: list[dict] = []
    for comp in LEDGER:
        if comp["kind"] == "pair":
            verdicts.append(evaluate_pair(comp, window_result))
        elif comp["kind"] == "lowest":
            verdicts.append(evaluate_lowest(comp, window_result))
        elif comp["kind"] == "ordering":
            verdicts.append(evaluate_ordering(comp, window_result))
        else:  # pragma: no cover - LEDGER is frozen
            raise ValueError(f"unknown comparison kind {comp['kind']!r}")
    assert len(verdicts) == 6, "charter §5 locks exactly 6 comparisons"
    return verdicts


# ---------------------------------------------------------------------------
# Human-readable rendering (explore window only)
# ---------------------------------------------------------------------------

def render_human_readable(window_result: dict, verdicts: list[dict]) -> str:
    lines: list[str] = []
    w = window_result["window"]
    yr = window_result["year_range"]
    lines.append(f"REGIME-TEST {w.upper()} window ({yr[0]}–{yr[1]})")
    lines.append(f"charter sha256: {window_result['charter_sha256']}")
    lines.append("")
    lines.append("Per-state cohort counts:")
    for s in _REGIME_STATES:
        n = window_result["per_state_cohort_counts"].get(s, 0)
        flag = "  (UNTESTABLE <3)" if n < _MIN_COHORTS else ""
        lines.append(f"  {s:9s} {n}{flag}")
    lines.append("")
    lines.append("State mean base rate by horizon:")
    for s in _REGIME_STATES:
        cells = []
        for h in V2_HORIZONS_TRADING_DAYS:
            m = _state_mean(window_result["state_aggregates"], s, h)
            cells.append(f"{h}td={m:.4f}" if m is not None else f"{h}td=NA")
        lines.append(f"  {s:9s} " + "  ".join(cells))
    lines.append("")
    lines.append("Ledger verdicts (6 comparisons):")
    for v in verdicts:
        lines.append(
            f"  #{v['id']} {v['hypothesis']} @{v['horizon']}td → "
            f"{v['verdict']}  ({v.get('reason', '')})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Run + artifact emission
# ---------------------------------------------------------------------------

def run_window(
    window: str,
    *,
    regime_states: Optional[dict] = None,
    pairs: Optional[list[tuple[str, str]]] = None,
    bars_loader: Optional[Callable[[str], object]] = None,
    out_dir: Optional[Path] = None,
    sample_per_side: int = _SAMPLE_PER_SIDE,
    run_seed: int = _RUN_SEED,
    universe_limit: Optional[int] = None,
) -> dict:
    """Compute one window end-to-end and write its artifact.

    EXPLORE / CONFIRM ISOLATION (design #5): a single call only ever touches its
    own window's cohorts.  An explore call never computes or prints confirm.

    Real run wiring (regime_states / pairs / bars_loader = None) reads
    regime_states.json, builds universe-v2, and constructs the F332 loader.
    Tests inject all three as synthetic fixtures.
    """
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}")
    out_dir = out_dir or _RUN_DIR

    if regime_states is None:
        regime_states = load_regime_states()
    if pairs is None:
        pairs = get_universe_v2_tickers(limit=universe_limit)
    if bars_loader is None:
        start_year, end_year = WINDOWS[window]
        bars_loader = _make_memoized_loader(
            start_year=start_year,
            end_year=end_year,
            low_lookback_years=_LOW_LOOKBACK_YEARS,
            horizon_months=_HORIZON_MONTHS,
            data_source="yahoo",
            price_cache=PriceFrameCache(),
        )

    window_result = compute_window(
        window, regime_states, pairs, bars_loader,
        sample_per_side=sample_per_side, run_seed=run_seed,
    )
    verdicts = evaluate_ledger(window_result)
    window_result["ledger_verdicts"] = verdicts
    window_result["generated_at"] = datetime.now(timezone.utc).isoformat()

    if window == "explore":
        window_result["human_readable"] = render_human_readable(window_result, verdicts)

    out_path = Path(out_dir) / f"{window}-result.json"
    _atomic_write_json(out_path, window_result)
    logger.info("Wrote %s window artifact to %s", window, out_path)
    return window_result


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="REGIME-TEST Unit 5 window runner")
    parser.add_argument("--window", required=True, choices=sorted(WINDOWS),
                        help="explore (2015–2020) or confirm (2021–2024); "
                             "computed independently — confirm is NOT touched in "
                             "an explore invocation.")
    parser.add_argument("--out-dir", default=None,
                        help="output dir (default .run/REGIME-TEST)")
    parser.add_argument("--universe-limit", type=int, default=None,
                        help="cap universe-v2 size (smoke/debug only)")
    parser.add_argument("--sample-per-side", type=int, default=_SAMPLE_PER_SIDE)
    args = parser.parse_args(argv)

    # Charter freeze guard: refuse to run if the on-disk charter sha drifted.
    on_disk = charter_sha256()
    if on_disk is not None and on_disk != _CHARTER_SHA256:
        logger.error("Charter sha256 mismatch: on-disk=%s frozen=%s — refusing.",
                     on_disk, _CHARTER_SHA256)
        return 2

    result = run_window(
        args.window,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        sample_per_side=args.sample_per_side,
        universe_limit=args.universe_limit,
    )

    # ISOLATION: only print THIS window's results.  Never print confirm during
    # an explore invocation (and vice-versa) — each invocation is self-contained.
    if args.window == "explore":
        print(result.get("human_readable", ""))
    else:
        print(f"confirm window computed: "
              f"{len(result['cohorts'])} cohorts, "
              f"{sum(1 for v in result['ledger_verdicts'])} ledger verdicts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
