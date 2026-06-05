"""backend/research/config_deterioration.py — Deterioration-short candidate source (Unit 7)

Pre-registered deterioration-short config per DETERIORATION-TEST charter
(.run/DETERIORATION-TEST/charter.md,
sha256=716c16b2b9b3c0ef082c2b51a7106551597117e858e345cbb846b49099e5d00c).

Gates (charter §1 — FIXED, not tuned):
  Gate A  — pct_off_high ≥ 50.0  (crashed ≥ 50% from trailing 252-day high)
  Gate B  — pct_above_low ≤ 25.0 (price within 25% of trailing 252-day low)
  Gate C  — revenue YoY ≥ 0 (still-positive trailing fundamentals; D1 only)
             Veto: excludes if revenue_yoy_pct < 0 (bad news already printed)
             Excludes with counted reason if no parseable fundamentals (no_fundamentals)
  Gate D  — ≥ 252 trading days of price history at as_of (same as Gate C for momentum)

Variant grid (charter §1 ledger):
  D1 (PRIMARY) : Gates A+B+C+D — crash + near-low + revenue veto
  D2           : Gates A+B+D only (veto leg OFF — price-only fallback per §2)

Out-of-grid variant names are REFUSED (ledger enforcement, charter §1 error-path).
Only D1/D2 variant names are accepted; any other name raises ValueError.

pct_off_high formula (charter §1, Gate A — mirrored from turnaround.py):
  pct_off_high = (high_252 - price) / high_252 * 100
  where high_252 = max close over trailing 252 trading rows (row-count based).

pct_above_low formula (charter §1, Gate B — mirrored from turnaround.py):
  pct_above_low = (price - low_252) / low_252 * 100
  where low_252 = min close over trailing 252 trading rows (row-count based).

Revenue YoY (charter §1, Gate C):
  Computed via edgar.get_quarterly_revenue(cik) (XBRL tag fallback chain).
  Point-in-time: only filings with filed < as_of.isoformat() are considered.
  YoY = (latest_quarter_value - same_quarter_prior_year_value) / prior * 100.
  ≥ 0 = still-positive (bad news not finished) → short candidate admitted.
  < 0 = deterioration already printed → excluded (veto).
  Unparseable / no-data → excluded with counted reason 'no_fundamentals'.

Direction: short.
expected_events_per_year: 105 (charter §3 R1 declaration, conservative post-veto floor).
borrow_rate_annual: 10.0 (charter §4, hard-to-borrow small-cap stress rate).
Horizons: [21, 63, 126] trading days (charter §4, V2_HORIZONS_TRADING_DAYS).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

import pandas as pd

from research.universe_floors import (
    floor_status,
    BELOW_FLOOR as _FLOOR_BELOW,
    CORRUPT_FRAME as _FLOOR_CORRUPT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Charter-fixed constants (FROZEN — not tunable per charter §8 amendment rule)
# ---------------------------------------------------------------------------

# Gate A — crash depth lower bound (pct_off_high)
_CRASH_THRESHOLD = 50.0   # PRIMARY: pct_off_high ≥ 50.0

# Gate B — near-low upper bound (pct_above_low)
_NEAR_LOW_THRESHOLD = 25.0  # price within 25% of trailing-1-year low

# Gate D — minimum trading-day history
_MIN_HISTORY_BARS = 252

# R1 declaration — charter §3
_EXPECTED_EVENTS_PER_YEAR = 105.0

# Borrow rate — charter §4 (10%/yr, hard-to-borrow crashed small-cap stress rate)
_BORROW_RATE_ANNUAL = 10.0

# Valid variant names — refuses anything outside the charter grid
_VALID_VARIANTS = frozenset({"D1", "D2"})

# Trailing window for revenue YoY computation (8 quarters per charter §1 Gate C)
_REVENUE_TRAILING_QUARTERS = 8


# ---------------------------------------------------------------------------
# Exclusion reason counter (returned alongside candidates so harness can log)
# ---------------------------------------------------------------------------

@dataclass
class _ExclusionCounts:
    no_bars: int = 0            # bars_loader returned None or empty
    below_floor: int = 0        # UNIVERSE_V2: sub-$5 price or thin avg volume
    corrupt_frame: int = 0      # UNIVERSE_V2: >10x split-corruption in trailing 252td
    short_history: int = 0      # Gate D: < 252 td (recent IPOs)
    gate_a_fail: int = 0        # pct_off_high < 50 (not crashed enough)
    gate_b_fail: int = 0        # pct_above_low > 25 (recovered too much from low)
    no_fundamentals: int = 0    # Gate C: no parseable revenue series (D1 only)
    veto_exclude_negative: int = 0  # Gate C: revenue YoY < 0 (bad news already printed)
    veto_admit: int = 0         # Gate C: revenue YoY ≥ 0 (admitted as short candidate)


# ---------------------------------------------------------------------------
# Helpers mirrored from config_momentum.py and turnaround.py
# ---------------------------------------------------------------------------

def _df_up_to(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Slice df to rows with index date <= as_of (point-in-time safe).

    Mirrors turnaround._df_up_to exactly (tz-aware index stripping).
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
# Per-symbol price gate evaluation (pure — operates on a pre-fetched DataFrame)
# ---------------------------------------------------------------------------

def _compute_price_gates(
    df: pd.DataFrame,
    as_of: date,
) -> tuple[bool, dict]:
    """Evaluate deterioration price gates A/B/D for one symbol at one as_of date.

    Returns (passes_price_gates, metrics_dict).
    metrics_dict keys: price, high_252, low_252, pct_off_high, pct_above_low,
                       gate_a, gate_b, gate_d, bars_available.

    Gate A: pct_off_high = (high_252 - price) / high_252 * 100 ≥ 50.0
    Gate B: pct_above_low = (price - low_252) / low_252 * 100 ≤ 25.0
    Gate D: bars_available ≥ 252

    Formula mirrored from turnaround.evaluate_washed_out (lines ~271-272):
      pct_above_low = (price - low_N) / low_N * 100.0
      pct_off_high_val = (high_N - price) / high_N * 100.0
    Using 252-row window (charter §1, row-count based, matching momentum's Gate A).
    """
    metrics: dict = {
        "price": None,
        "high_252": None,
        "low_252": None,
        "pct_off_high": None,
        "pct_above_low": None,
        "gate_a": False,
        "gate_b": False,
        "gate_d": False,
        "bars_available": 0,
    }

    if df is None or df.empty:
        return False, metrics

    sliced = _df_up_to(df, as_of)
    if sliced.empty:
        return False, metrics

    n = len(sliced)
    metrics["bars_available"] = n

    # Gate D — minimum history check (charter §1 Gate D)
    gate_d = n >= _MIN_HISTORY_BARS
    metrics["gate_d"] = gate_d
    if not gate_d:
        return False, metrics

    try:
        close = _get_close(sliced)
    except KeyError:
        return False, metrics

    price = float(close.iloc[-1])
    metrics["price"] = price

    if price <= 0:
        return False, metrics

    # Gate A — crash depth gate (charter §1 Gate A)
    # high_252 = max close over trailing 252 trading rows (row-count based).
    high_window = close.iloc[-_MIN_HISTORY_BARS:]
    high_252 = float(high_window.max())
    metrics["high_252"] = high_252

    if high_252 <= 0:
        return False, metrics

    pct_off_high = (high_252 - price) / high_252 * 100.0
    metrics["pct_off_high"] = pct_off_high
    gate_a = pct_off_high >= _CRASH_THRESHOLD
    metrics["gate_a"] = gate_a

    # Gate B — near-low gate (charter §1 Gate B)
    # low_252 = min close over trailing 252 trading rows (row-count based).
    low_window = close.iloc[-_MIN_HISTORY_BARS:]
    low_252 = float(low_window.min())
    metrics["low_252"] = low_252

    if low_252 <= 0:
        return False, metrics

    pct_above_low = (price - low_252) / low_252 * 100.0
    metrics["pct_above_low"] = pct_above_low
    gate_b = pct_above_low <= _NEAR_LOW_THRESHOLD
    metrics["gate_b"] = gate_b

    passes = gate_a and gate_b  # Gate D already cleared
    return passes, metrics


# ---------------------------------------------------------------------------
# Point-in-time revenue YoY computation (charter §1 Gate C)
# ---------------------------------------------------------------------------

def _compute_revenue_yoy_pit(cik: str, as_of: date) -> Optional[float]:
    """Compute latest trailing revenue YoY% with strict point-in-time filter.

    Returns:
      float (positive or negative) if computable.
      None if no parseable series or no prior-year comparison is available.

    Point-in-time rule (FROZEN, charter §1 Gate C):
      Only filings with filed STRICTLY BEFORE as_of are considered.
      filed < as_of.isoformat() (string comparison, ISO format).

    Uses edgar.get_quarterly_revenue(cik) → tag fallback chain:
      Revenues → RevenueFromContractWithCustomerExcludingAssessedTax
               → RevenueFromContractWithCustomerIncludingAssessedTax
               → SalesRevenueNet
    plus Q4-from-annual derivation, merged in.

    YoY = (latest_end_val - same_fiscal_quarter_prior_year_val) / prior_val * 100.
    The "same fiscal quarter prior year" is identified by matching the end-date
    month and day (±15 days window) approximately 4 quarters back in the series.
    """
    import edgar

    try:
        series = edgar.get_quarterly_revenue(cik)
    except Exception as exc:
        logger.warning(
            "deterioration: edgar.get_quarterly_revenue failed for cik=%s: %s", cik, exc
        )
        return None

    if not series:
        return None

    as_of_str = as_of.isoformat()

    # Apply strict point-in-time filter: filed < as_of (STRICTLY before)
    pit_series = [
        e for e in series
        if e.get("filed", "") < as_of_str
    ]

    if not pit_series:
        return None

    # Find the most-recent quarter by end date among PIT-filtered entries
    latest = max(pit_series, key=lambda e: e["end"])
    latest_end = latest["end"]  # e.g. "2015-09-30"
    latest_val = latest["val"]

    # Find the same-fiscal-quarter prior year: look for entry with end ≈ 1 year prior
    # "1 year prior" = same month/day but year - 1, ±15 calendar-day tolerance.
    try:
        latest_date = date.fromisoformat(latest_end)
    except (ValueError, TypeError):
        return None

    # Compute target prior-year end (same calendar date, year-1)
    try:
        prior_year_target = latest_date.replace(year=latest_date.year - 1)
    except ValueError:
        # Feb 29 edge case — fall back to Feb 28
        prior_year_target = latest_date.replace(year=latest_date.year - 1, day=28)

    # Find the closest matching entry in the PIT series within ±15 days
    best_prior: Optional[dict] = None
    best_delta: int = 9999
    for e in pit_series:
        if e["end"] == latest_end:
            continue  # skip the latest quarter itself
        try:
            e_date = date.fromisoformat(e["end"])
        except (ValueError, TypeError):
            continue
        delta = abs((e_date - prior_year_target).days)
        if delta <= 15 and delta < best_delta:
            best_delta = delta
            best_prior = e

    if best_prior is None:
        return None

    prior_val = best_prior["val"]
    if prior_val == 0:
        return None  # avoid division by zero

    return (latest_val - prior_val) / abs(prior_val) * 100.0


# ---------------------------------------------------------------------------
# CandidateResult factory (duck-typed; uses the real dataclass from turnaround)
# ---------------------------------------------------------------------------

def _make_deterioration_candidate(
    ticker: str,
    cik: str,
    pct_off_high: float,
    pct_above_low: float,
    revenue_yoy_pct: Optional[float],
) -> object:
    """Return a CandidateResult-like object for a deterioration signal candidate.

    is_null_candidate=False → signal event (not a null placeholder).
    direction='short' flows into _apply_costs() via the config.
    """
    from turnaround import CandidateResult
    return CandidateResult(
        ticker=ticker,
        cik=cik,
        price_near_low=True,         # crashed + near low
        pct_off_high=pct_off_high,   # carry the metric for diagnostics
        pct_above_low=pct_above_low, # carry the metric for diagnostics
        below_ma=True,               # crashed names are below MA by construction
        revenue_yoy_pct=revenue_yoy_pct,
        revenue_consec_positive=0,   # not evaluated for deterioration screen
        gross_margin_delta_pct=None,
        net_income_consec_improving=0,
        ocf_positive_quarters=0,
        ps_ratio=None,
        has_insider_buying=False,
        has_buyback=False,
        # composite_score: deeper crash + closer to low = more deteriorated
        # pct_off_high ∈ [50, 100], pct_above_low ∈ [0, 25] for admissions
        composite_score=pct_off_high - pct_above_low,
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
      source_fn(as_of: date, universe: list[tuple[str, str, str]], bars_loader: Callable)
        -> list[CandidateResult]

    Universe entries may be (ticker, name) or (ticker, name, cik) tuples.
    CIK is needed for Gate C (revenue YoY via EDGAR).

    variant must be one of {"D1", "D2"} (charter grid enforcement).

    D1 (PRIMARY): All four gates A+B+C+D.
    D2 (price-only): Gates A+B+D only (veto leg OFF per charter §1 variant grid).
    """
    if variant not in _VALID_VARIANTS:
        raise ValueError(
            f"Out-of-charter variant {variant!r}. "
            f"Allowed variants: {sorted(_VALID_VARIANTS)}. "
            f"Charter §1: only D1/D2 are registered; out-of-grid parameter values "
            f"are refused by the config (ledger enforcement)."
        )

    apply_veto = (variant == "D1")  # D2 has Gate C OFF

    def source_fn(
        as_of: date,
        universe: list,
        bars_loader: Callable[[str], Optional[pd.DataFrame]],
    ) -> list:
        candidates = []
        excl = _ExclusionCounts()

        for entry in universe:
            if isinstance(entry, (tuple, list)):
                ticker = entry[0]
                # universe is list[(ticker, cik)] — build_universe returns (ticker, cik_padded)
                # entry[1] is the CIK (10-digit zero-padded); entry[2] if present is a name
                cik = entry[1] if len(entry) >= 2 else ""
            else:
                ticker = entry
                cik = ""

            df = bars_loader(ticker)
            if df is None or (hasattr(df, 'empty') and df.empty):
                excl.no_bars += 1
                continue

            # UNIVERSE_V2 floor conformance (charter pre-registered universe-v2):
            # point-in-time min_price / min_avg_volume floors + split-corruption
            # guard, evaluated strictly from bars <= as_of (no look-ahead). The
            # SAME enforcement runs in the harness's null-aggregates path so signal
            # and null see the same universe. Rejects the live $0.0112 / sub-$5
            # leaks and the GXXM $51M split-corrupted entry.
            fstatus = floor_status(df, as_of)
            if fstatus == _FLOOR_BELOW:
                excl.below_floor += 1
                continue
            if fstatus == _FLOOR_CORRUPT:
                excl.corrupt_frame += 1
                logger.debug(
                    "deterioration/%s: %s excluded — corrupt_frame at %s",
                    variant, ticker, as_of,
                )
                continue

            passes_price, metrics = _compute_price_gates(df, as_of)

            # Gate D — short history
            if not metrics["gate_d"]:
                excl.short_history += 1
                logger.debug(
                    "deterioration/%s: %s excluded — Gate D: %d bars < %d at %s",
                    variant, ticker, metrics["bars_available"], _MIN_HISTORY_BARS, as_of,
                )
                continue

            # Gate A — not crashed enough
            if not metrics["gate_a"]:
                excl.gate_a_fail += 1
                continue

            # Gate B — recovered too much from low
            if not metrics["gate_b"]:
                excl.gate_b_fail += 1
                continue

            # Price gates A+B+D passed — apply Gate C (revenue YoY veto) if D1
            if apply_veto:
                if not cik:
                    # No CIK → cannot fetch fundamentals → exclude with counted reason
                    excl.no_fundamentals += 1
                    logger.debug(
                        "deterioration/D1: %s excluded — no_fundamentals (no CIK) at %s",
                        ticker, as_of,
                    )
                    continue

                revenue_yoy = _compute_revenue_yoy_pit(cik, as_of)

                if revenue_yoy is None:
                    # Unparseable series — excluded with counted reason (never imputed)
                    excl.no_fundamentals += 1
                    logger.debug(
                        "deterioration/D1: %s excluded — no_fundamentals "
                        "(no parseable revenue series) at %s cik=%s",
                        ticker, as_of, cik,
                    )
                    continue
                elif revenue_yoy < 0.0:
                    # Deterioration already printed → veto (short edge spent)
                    excl.veto_exclude_negative += 1
                    logger.debug(
                        "deterioration/D1: %s excluded — veto_exclude_negative "
                        "(revenue_yoy=%.1f%%) at %s",
                        ticker, revenue_yoy, as_of,
                    )
                    continue
                else:
                    # revenue_yoy ≥ 0 → still-positive trailing fundamentals → admit
                    excl.veto_admit += 1
                    candidates.append(
                        _make_deterioration_candidate(
                            ticker, cik,
                            pct_off_high=metrics["pct_off_high"] or 0.0,
                            pct_above_low=metrics["pct_above_low"] or 0.0,
                            revenue_yoy_pct=revenue_yoy,
                        )
                    )
            else:
                # D2: no veto leg — emit directly after price gates
                candidates.append(
                    _make_deterioration_candidate(
                        ticker, cik,
                        pct_off_high=metrics["pct_off_high"] or 0.0,
                        pct_above_low=metrics["pct_above_low"] or 0.0,
                        revenue_yoy_pct=None,
                    )
                )

        # Coverage logging (charter §2: frac_no_fundamentals must be logged)
        if apply_veto:
            total_price_gated = (
                excl.no_fundamentals + excl.veto_admit + excl.veto_exclude_negative
            )
            frac_no_fundamentals = (
                excl.no_fundamentals / total_price_gated
                if total_price_gated > 0 else 0.0
            )
            logger.info(
                "deterioration/%s at %s: %d candidates from %d universe names "
                "(no_bars=%d, below_floor=%d, corrupt_frame=%d, short_history=%d, "
                "gate_a_fail=%d, gate_b_fail=%d, "
                "no_fundamentals=%d [%.1f%%], veto_negative=%d, veto_admit=%d)",
                variant, as_of, len(candidates), len(universe),
                excl.no_bars, excl.below_floor, excl.corrupt_frame, excl.short_history,
                excl.gate_a_fail, excl.gate_b_fail,
                excl.no_fundamentals, frac_no_fundamentals * 100,
                excl.veto_exclude_negative, excl.veto_admit,
            )
        else:
            logger.info(
                "deterioration/%s at %s: %d candidates from %d universe names "
                "(no_bars=%d, below_floor=%d, corrupt_frame=%d, short_history=%d, "
                "gate_a_fail=%d, gate_b_fail=%d)",
                variant, as_of, len(candidates), len(universe),
                excl.no_bars, excl.below_floor, excl.corrupt_frame, excl.short_history,
                excl.gate_a_fail, excl.gate_b_fail,
            )

        return candidates

    return source_fn


# ---------------------------------------------------------------------------
# Registered config objects (one per variant)
# ---------------------------------------------------------------------------

def _build_config(variant: str) -> object:
    """Build a CandidateSourceConfig for the given deterioration variant.

    Raises ValueError if variant is not in the charter grid.
    """
    from turnaround_validation import CandidateSourceConfig

    if variant not in _VALID_VARIANTS:
        raise ValueError(
            f"Out-of-charter variant {variant!r}. Allowed: {sorted(_VALID_VARIANTS)}."
        )

    return CandidateSourceConfig(
        name=f"deterioration_{variant}",
        direction="short",
        expected_events_per_year=_EXPECTED_EVENTS_PER_YEAR,
        source_fn=_make_source_fn(variant),
        horizons=[21, 63, 126],  # charter §4 — V2_HORIZONS_TRADING_DAYS
    )


# Primary config (D1) — the one registered in the route registry for H1/H2 judging
CONFIG_D1 = _build_config("D1")

# Price-only fallback variant (D2) — per charter §1/§2 variant grid
CONFIG_D2 = _build_config("D2")

# Convenience alias: the primary config
CONFIG = CONFIG_D1
