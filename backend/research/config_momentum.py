"""backend/research/config_momentum.py — Momentum candidate source (Unit 6)

Pre-registered momentum/52-week-high config per MOMENTUM-TEST charter
(.run/MOMENTUM-TEST/charter.md, sha256=ffef4c05987778b03ffeb7a0d064a701cdefcc302ea596a9aaf65b8b55c6189f).

Gates (charter §1 — FIXED, not tuned):
  Gate A  — pct_off_high ≤ threshold  (near trailing 252-day high)
  Gate B  — price > ma_200 AND ma_200 rising over last 21 trading days
  Gate C  — ≥ 252 trading days of price history at as_of

Variant grid (charter §1 ledger):
  M1 (PRIMARY) : pct_off_high ≤ 5.0,  Gate B with slope,  ≥252td
  M2            : pct_off_high ≤ 10.0, Gate B with slope,  ≥252td
  M3            : pct_off_high ≤ 5.0,  price > ma_200 (drop slope), ≥252td

Out-of-grid parameter values are REFUSED (ledger enforcement, charter §1 error-path).
Only M1/M2/M3 variant names are accepted; any other name raises ValueError.

pct_off_high formula (charter §1, Gate A):
  pct_off_high = (high_252 - price) / high_252 * 100
  where high_252 = max close over trailing 252 trading rows (row-count based,
  matching the "explicit 252-row max" wording in charter §1 Gate A).
  This is the INVERSION of turnaround.evaluate_washed_out's washed-out gate
  (turnaround requires pct_off_high >= 50; momentum requires pct_off_high <= 5).
  Math mirrored from turnaround.evaluate_washed_out (pct_off_high_val =
  (high_N - price) / high_N * 100.0 in turnaround.py ~line 274).

ma_200 formula (charter §1, Gate B):
  ma_200 = mean of last 200 Close rows (trading-day rolling mean) — reuses
  turnaround.evaluate_washed_out's ma_window convention (sliced.iloc[-200:]).
  Slope check: ma_200_today > ma_200_{21 rows back} (same rolling-200 logic
  applied to the frame 21 rows back).

Direction: long.
expected_events_per_year: 350 (charter §2 R1 declaration, conservative single-cohort floor).
Horizons: [21, 63, 126] trading days (charter §3, V2_HORIZONS_TRADING_DAYS).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Charter-fixed constants (FROZEN — not tunable per charter §7 amendment rule)
# ---------------------------------------------------------------------------

# Variant gate-A thresholds (pct_off_high upper bound)
_VARIANT_THRESHOLDS: dict[str, float] = {
    "M1": 5.0,   # PRIMARY
    "M2": 10.0,  # robustness (looser nearness band)
    "M3": 5.0,   # robustness (trend-filter ablation — Gate B without slope req.)
}

# Gate C — minimum trading-day history
_MIN_HISTORY_BARS = 252

# Gate B — 200-day SMA period (trading-day rolling)
_MA_PERIOD = 200

# Gate B slope check — how many rows back to compare SMA to for "rising" test
_MA_SLOPE_LOOKBACK = 21

# R1 declaration — charter §2
_EXPECTED_EVENTS_PER_YEAR = 350.0

# Valid variant names — refuses anything outside the charter grid
_VALID_VARIANTS = frozenset({"M1", "M2", "M3"})


# ---------------------------------------------------------------------------
# Exclusion reason counter (returned alongside candidates so harness can log)
# ---------------------------------------------------------------------------

@dataclass
class _ExclusionCounts:
    short_history: int = 0    # Gate C: < 252 td (e.g. recent IPOs)
    no_bars: int = 0          # bars_loader returned None or empty
    gate_a_fail: int = 0      # pct_off_high > threshold (not near high)
    gate_b_fail: int = 0      # price <= ma_200 OR ma_200 not rising


# ---------------------------------------------------------------------------
# Per-symbol gate evaluation (pure — operates on an already-fetched DataFrame)
# ---------------------------------------------------------------------------

def _compute_gates(
    df: pd.DataFrame,
    as_of: date,
    threshold_pct: float,
    require_ma_slope: bool,
) -> tuple[bool, dict]:
    """Evaluate momentum gates A/B/C for one symbol at one as_of date.

    Returns (passes_all_gates, metrics_dict).
    metrics_dict keys: price, ma_200, high_252, pct_off_high, ma_200_21ago,
                       gate_a, gate_b, gate_c, bars_available.

    Metrics are always populated where computable; gates are short-circuit-safe.
    Operates on the full bars frame — caller slices to as_of via _df_up_to
    before passing here (or passes the full frame and this function slices).

    Gate formulas mirror turnaround.evaluate_washed_out math exactly:
      - pct_off_high = (high_252 - price) / high_252 * 100
        where high_252 = max(close[-252:]) [trailing 252 rows]
      - ma_200 = mean(close[-200:])  [trailing 200 rows]
    """
    metrics: dict = {
        "price": None,
        "ma_200": None,
        "high_252": None,
        "pct_off_high": None,
        "ma_200_21ago": None,
        "gate_a": False,
        "gate_b": False,
        "gate_c": False,
        "bars_available": 0,
    }

    if df is None or df.empty:
        return False, metrics

    # Slice to rows <= as_of (point-in-time, no look-ahead)
    sliced = _df_up_to(df, as_of)
    if sliced.empty:
        return False, metrics

    n = len(sliced)
    metrics["bars_available"] = n

    # Gate C — minimum history check (charter §1 Gate C)
    gate_c = n >= _MIN_HISTORY_BARS
    metrics["gate_c"] = gate_c
    if not gate_c:
        return False, metrics

    # Extract Close series
    try:
        close = _get_close(sliced)
    except KeyError:
        return False, metrics

    price = float(close.iloc[-1])
    metrics["price"] = price

    if price <= 0:
        return False, metrics

    # Gate A — near trailing 252-day high (charter §1 Gate A)
    # high_252 = max close over trailing 252 trading rows (row-count based).
    high_window = close.iloc[-_MIN_HISTORY_BARS:]   # last 252 rows
    high_252 = float(high_window.max())
    metrics["high_252"] = high_252

    if high_252 <= 0:
        return False, metrics

    pct_off_high = (high_252 - price) / high_252 * 100.0
    metrics["pct_off_high"] = pct_off_high

    gate_a = pct_off_high <= threshold_pct
    metrics["gate_a"] = gate_a

    # Gate B — trend persistence (charter §1 Gate B)
    # ma_200 = mean(close[-200:])  — trading-day rolling 200-period mean.
    ma_window = close.iloc[-_MA_PERIOD:]
    ma_200 = float(ma_window.mean())
    metrics["ma_200"] = ma_200

    # Slope check (M1/M2 only — M3 drops the slope requirement per charter §1):
    # ma_200_today > ma_200_{21 trading rows ago}
    # The 21-rows-back SMA is computed on the frame sliced to 21 rows earlier,
    # i.e. we use close.iloc[: n - _MA_SLOPE_LOOKBACK] to compute the SMA
    # at that point-in-time position.
    if require_ma_slope and n >= _MA_PERIOD + _MA_SLOPE_LOOKBACK:
        lookback_end = n - _MA_SLOPE_LOOKBACK
        ma_window_21ago = close.iloc[lookback_end - _MA_PERIOD: lookback_end]
        if len(ma_window_21ago) == _MA_PERIOD:
            ma_200_21ago = float(ma_window_21ago.mean())
            metrics["ma_200_21ago"] = ma_200_21ago
            slope_ok = ma_200 > ma_200_21ago
        else:
            # Not enough bars for the slope check — fail conservatively
            metrics["ma_200_21ago"] = None
            slope_ok = False
    elif require_ma_slope:
        # Need at least _MA_PERIOD + _MA_SLOPE_LOOKBACK rows for slope
        metrics["ma_200_21ago"] = None
        slope_ok = False
    else:
        # M3 variant: no slope requirement
        metrics["ma_200_21ago"] = None
        slope_ok = True  # slope not required

    gate_b = (price > ma_200) and slope_ok
    metrics["gate_b"] = gate_b

    passes = gate_a and gate_b  # Gate C already cleared above
    return passes, metrics


def _df_up_to(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Slice df to rows with index date <= as_of.

    Mirrors turnaround._df_up_to exactly (tz-aware index stripping, same
    normalise-to-Timestamp approach) so gate evaluation is point-in-time safe.
    """
    if df.empty:
        return df
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        if idx.tz is not None:
            df = df.copy()
            df.index = idx = idx.tz_localize(None)
        mask = idx.normalize() <= pd.Timestamp(as_of)
    elif hasattr(idx[0], 'date'):
        mask = pd.to_datetime(idx).normalize() <= pd.Timestamp(as_of)
    else:
        mask = idx <= pd.Timestamp(as_of)
    return df[mask]


def _get_close(df: pd.DataFrame) -> pd.Series:
    """Return Close series (mirrors turnaround._get_close)."""
    for col in ("Close", "close", "Adj Close", "adj close"):
        if col in df.columns:
            return df[col]
    raise KeyError("No Close column in DataFrame")


# ---------------------------------------------------------------------------
# CandidateResult factory (duck-typed; uses the real dataclass from turnaround)
# ---------------------------------------------------------------------------

def _make_momentum_candidate(ticker: str, pct_off_high: float) -> object:
    """Return a CandidateResult-like object for a momentum signal candidate.

    is_null_candidate=False → signal event (not a null placeholder).
    composite_score uses pct_off_high inverted: nearer to high = higher score.
    All fundamental/conviction fields are zeroed out — momentum is price-only (R8).

    Uses the real CandidateResult dataclass at import time (lazy import avoids
    circular dependency at module load; same pattern as turnaround_validation.py).
    """
    from turnaround import CandidateResult
    return CandidateResult(
        ticker=ticker,
        cik="",
        price_near_low=False,        # inverse of washed-out
        pct_off_high=pct_off_high,   # carry the metric for diagnostics
        pct_above_low=0.0,           # not meaningful for momentum universe
        below_ma=False,              # inverse of washed-out (price > ma_200)
        revenue_yoy_pct=None,
        revenue_consec_positive=0,
        gross_margin_delta_pct=None,
        net_income_consec_improving=0,
        ocf_positive_quarters=0,
        ps_ratio=None,
        has_insider_buying=False,
        has_buyback=False,
        # composite_score: nearer to high = better (5 - pct_off_high, floored at 0)
        # range 0..5 for M1 (threshold 5.0) — kept as a relative rank signal
        composite_score=max(0.0, 5.0 - pct_off_high),
        is_null_candidate=False,
    )


# ---------------------------------------------------------------------------
# Source function factory
# ---------------------------------------------------------------------------

def _make_source_fn(
    variant: str,
) -> Callable[[date, list, Callable], list]:
    """Return a source_fn callable for the given variant.

    The source_fn signature is:
      source_fn(as_of: date, universe: list[tuple[str, str]], bars_loader: Callable)
        -> list[CandidateResult]

    Evaluates all three gates per charter §1 at each cohort as_of from the
    already-fetched daily frame via bars_loader.  Names failing Gate C are
    excluded with a counted reason (logged at DEBUG level).

    variant must be one of {"M1", "M2", "M3"} (charter grid enforcement).
    """
    if variant not in _VALID_VARIANTS:
        raise ValueError(
            f"Out-of-charter variant {variant!r}. "
            f"Allowed variants: {sorted(_VALID_VARIANTS)}. "
            f"Charter §1: only M1/M2/M3 are registered; out-of-grid parameter values "
            f"are refused by the config (ledger enforcement)."
        )

    threshold_pct = _VARIANT_THRESHOLDS[variant]
    require_ma_slope = (variant != "M3")   # M3 drops the slope requirement

    def source_fn(
        as_of: date,
        universe: list,
        bars_loader: Callable[[str], Optional[pd.DataFrame]],
    ) -> list:
        candidates = []
        excl = _ExclusionCounts()

        for entry in universe:
            # universe is list[tuple[str, str]] — (ticker, name)
            ticker = entry[0] if isinstance(entry, (tuple, list)) else entry

            df = bars_loader(ticker)
            if df is None or (hasattr(df, 'empty') and df.empty):
                excl.no_bars += 1
                continue

            passes, metrics = _compute_gates(
                df, as_of, threshold_pct, require_ma_slope
            )

            if not metrics["gate_c"]:
                excl.short_history += 1
                logger.debug(
                    "momentum/%s: %s excluded — Gate C: %d bars < %d required at %s",
                    variant, ticker, metrics["bars_available"], _MIN_HISTORY_BARS, as_of,
                )
                continue

            if not metrics["gate_a"]:
                excl.gate_a_fail += 1
                continue

            if not metrics["gate_b"]:
                excl.gate_b_fail += 1
                continue

            # All gates pass — emit as signal candidate
            pct_off = metrics["pct_off_high"] or 0.0
            candidates.append(_make_momentum_candidate(ticker, pct_off))

        logger.debug(
            "momentum/%s at %s: %d candidates from %d universe names "
            "(no_bars=%d, short_history=%d, gate_a_fail=%d, gate_b_fail=%d)",
            variant, as_of, len(candidates), len(universe),
            excl.no_bars, excl.short_history, excl.gate_a_fail, excl.gate_b_fail,
        )

        return candidates

    return source_fn


# ---------------------------------------------------------------------------
# Registered config objects (one per variant)
# ---------------------------------------------------------------------------

def _build_config(variant: str) -> object:
    """Build a CandidateSourceConfig for the given momentum variant.

    Raises ValueError if variant is not in the charter grid.
    """
    from turnaround_validation import CandidateSourceConfig

    if variant not in _VALID_VARIANTS:
        raise ValueError(
            f"Out-of-charter variant {variant!r}. Allowed: {sorted(_VALID_VARIANTS)}."
        )

    return CandidateSourceConfig(
        name=f"momentum_{variant}",
        direction="long",
        expected_events_per_year=_EXPECTED_EVENTS_PER_YEAR,
        source_fn=_make_source_fn(variant),
        horizons=[21, 63, 126],  # charter §3 — will be used by harness for v2 metrics
    )


# Primary config (M1) — the one registered in the route registry
CONFIG_M1 = _build_config("M1")

# Robustness variants (M2/M3) — registered as separate configs per charter §1 ledger
CONFIG_M2 = _build_config("M2")
CONFIG_M3 = _build_config("M3")

# Convenience alias: the primary config
CONFIG = CONFIG_M1
