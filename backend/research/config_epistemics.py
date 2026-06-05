"""backend/research/config_epistemics.py — Epistemics-ablation candidate sources (Unit 8)

Pre-registered epistemics-ablation configs per EPISTEMICS-TEST charter
(.run/EPISTEMICS-TEST/charter.md,
sha256=015b04646789036a7894144a058300bec3a0c8813540e5e69bdf31da2060149d).

TWO arms, both long, head-to-head on the same universe, same horizons, same cohorts:

  EP_PRICE  — top-50 by trailing 126-trading-day total return rank (price info, ONE criterion)
  EP_FILING — top-50 by trailing revenue YoY% rank (XBRL point-in-time, ONE criterion)

Charter §2 (FROZEN, not tunable):
  - Both arms rank within UNIVERSE_V2 floor-eligible names (floor_status == "ok",
    ≥ 252 td history — BOTH arms; additionally §3 coverage gate for FILING arm).
  - N = 50 per cohort per arm (rank-top-N, fixed from event-rate arithmetic).
  - Ties broken by ticker ascending (deterministic).
  - expected_events_per_year = 200 each (4 cohorts/yr × 50 names, charter §2 R1).

Charter §3 — coverage measurement (binding):
  Per window (explore and confirm), record:
    facts_coverage = eligible_with_pit_revenue / all_floor_eligible
  Tiers:
    ≥ 0.60          → full read proceeds
    0.40 ≤ c < 0.60 → reweighted covered-subset read fires
    < 0.40          → FILING arm UNVIABLE; H1/H2 UNTESTABLE

PRICE arm:
  criterion: ret_126 = (close[-1] / close[-127] - 1) * 100
  sort:       descending (higher trailing return = higher rank)
  composite_score = ret_126

FILING arm:
  criterion: revenue_yoy_pct via edgar.get_quarterly_revenue(cik)
             XBRL tag fallback chain: Revenues → RevenueFromContractWith...Excluding...
             → RevenueFromContractWith...Including... → SalesRevenueNet
             Q4-from-annual derivation merged in.
  point-in-time rule (FROZEN, identical to DETERIORATION-TEST §1 Gate C):
             only filings with filed STRICTLY BEFORE as_of count.
  sort:       descending (higher trailing revenue YoY = higher rank)
  composite_score = revenue_yoy_pct

Direction: long (both arms).
Horizons: [21, 63, 126] trading days (charter §4, V2_HORIZONS_TRADING_DAYS).
expected_events_per_year: 200 (both arms, charter §2 R1 declaration).
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
# Charter-fixed constants (FROZEN — not tunable per charter §7 amendment rule)
# ---------------------------------------------------------------------------

# Rank-N per cohort per arm — fixed from event-rate arithmetic, charter §2
_RANK_N = 50

# Minimum trading-day history (needed for 126td return + floor windows)
_MIN_HISTORY_BARS = 252

# Minimum bars needed so ret_126 can be computed: need rows[-127] to rows[-1]
_RET_126_MIN_BARS = 127

# R1 declaration — charter §2
_EXPECTED_EVENTS_PER_YEAR = 200.0

# Coverage tier thresholds (charter §3, FROZEN)
_COVERAGE_VIABLE_THRESHOLD = 0.60
_COVERAGE_REWEIGHT_THRESHOLD = 0.40


# ---------------------------------------------------------------------------
# Exclusion reason counters
# ---------------------------------------------------------------------------

@dataclass
class _PriceExclusionCounts:
    no_bars: int = 0            # bars_loader returned None or empty
    below_floor: int = 0        # UNIVERSE_V2: sub-$5 price or thin avg volume
    corrupt_frame: int = 0      # UNIVERSE_V2: >10x split-corruption
    short_history: int = 0      # < 252 td (§2 condition b)


@dataclass
class _FilingExclusionCounts:
    no_bars: int = 0
    below_floor: int = 0
    corrupt_frame: int = 0
    short_history: int = 0      # < 252 td (§2 condition b)
    no_cik: int = 0             # no CIK → cannot fetch fundamentals
    no_fundamentals: int = 0    # no parseable PIT revenue YoY → §3 coverage


# ---------------------------------------------------------------------------
# Shared price-frame helpers (mirrored from config_momentum / config_deterioration)
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
# Point-in-time revenue YoY computation (identical to deterioration §1 Gate C)
# ---------------------------------------------------------------------------

def _compute_revenue_yoy_pit(cik: str, as_of: date) -> Optional[float]:
    """Compute latest trailing revenue YoY% with strict point-in-time filter.

    Returns float if computable, None if no parseable series or no prior-year pair.

    Point-in-time rule (FROZEN, charter §2 / identical to DETERIORATION-TEST §1 Gate C):
      Only filings with filed STRICTLY BEFORE as_of count.
      filed < as_of.isoformat() (string comparison, ISO format).

    XBRL tag fallback chain (charter §2):
      Revenues → RevenueFromContractWithCustomerExcludingAssessedTax
               → RevenueFromContractWithCustomerIncludingAssessedTax
               → SalesRevenueNet
    plus Q4-from-annual derivation, merged in.

    YoY = (latest_end_val - same_fiscal_quarter_prior_year_val) / abs(prior_val) * 100.
    Prior-year quarter identified by end-date month/day ±15 calendar days.
    """
    import edgar

    try:
        series = edgar.get_quarterly_revenue(cik)
    except Exception as exc:
        logger.warning(
            "epistemics/filing: edgar.get_quarterly_revenue failed for cik=%s: %s", cik, exc
        )
        return None

    if not series:
        return None

    as_of_str = as_of.isoformat()

    # Apply strict PIT filter: filed < as_of (STRICTLY before)
    pit_series = [
        e for e in series
        if e.get("filed", "") < as_of_str
    ]

    if not pit_series:
        return None

    # Find the most-recent quarter by end date among PIT-filtered entries
    latest = max(pit_series, key=lambda e: e["end"])
    latest_end = latest["end"]
    latest_val = latest["val"]

    # Find the same-fiscal-quarter prior year: end ≈ 1 year prior (±15 calendar days)
    try:
        latest_date = date.fromisoformat(latest_end)
    except (ValueError, TypeError):
        return None

    try:
        prior_year_target = latest_date.replace(year=latest_date.year - 1)
    except ValueError:
        # Feb 29 edge case
        prior_year_target = latest_date.replace(year=latest_date.year - 1, day=28)

    best_prior: Optional[dict] = None
    best_delta: int = 9999
    for e in pit_series:
        if e["end"] == latest_end:
            continue
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
        return None

    return (latest_val - prior_val) / abs(prior_val) * 100.0


# ---------------------------------------------------------------------------
# CandidateResult factory
# ---------------------------------------------------------------------------

def _make_price_candidate(ticker: str, ret_126: float) -> object:
    """Return a CandidateResult for the PRICE arm (EP_PRICE).

    composite_score = ret_126 (raw trailing return for rank/diagnostic).
    All fundamental fields zeroed — price-only by construction (charter §2).
    """
    from turnaround import CandidateResult
    return CandidateResult(
        ticker=ticker,
        cik="",
        price_near_low=False,
        pct_off_high=0.0,
        pct_above_low=0.0,
        below_ma=False,
        revenue_yoy_pct=None,
        revenue_consec_positive=0,
        gross_margin_delta_pct=None,
        net_income_consec_improving=0,
        ocf_positive_quarters=0,
        ps_ratio=None,
        has_insider_buying=False,
        has_buyback=False,
        composite_score=ret_126,
        is_null_candidate=False,
    )


def _make_filing_candidate(ticker: str, cik: str, revenue_yoy_pct: float) -> object:
    """Return a CandidateResult for the FILING arm (EP_FILING).

    composite_score = revenue_yoy_pct (raw YoY% for rank/diagnostic).
    Price conviction fields carry diagnostics only (charter §2).
    """
    from turnaround import CandidateResult
    return CandidateResult(
        ticker=ticker,
        cik=cik,
        price_near_low=False,
        pct_off_high=0.0,
        pct_above_low=0.0,
        below_ma=False,
        revenue_yoy_pct=revenue_yoy_pct,
        revenue_consec_positive=0,
        gross_margin_delta_pct=None,
        net_income_consec_improving=0,
        ocf_positive_quarters=0,
        ps_ratio=None,
        has_insider_buying=False,
        has_buyback=False,
        composite_score=revenue_yoy_pct,
        is_null_candidate=False,
    )


# ---------------------------------------------------------------------------
# Coverage measurement helper (charter §3)
# ---------------------------------------------------------------------------

def compute_coverage_tier(facts_coverage: float) -> str:
    """Return the charter §3 coverage tier label for a given coverage fraction.

    Tiers:
      'viable'     — coverage >= 0.60 (full read proceeds)
      'reweight'   — 0.40 <= coverage < 0.60 (covered-subset-matched read)
      'unviable'   — coverage < 0.40 (FILING arm UNVIABLE; H1/H2 UNTESTABLE)
    """
    if facts_coverage >= _COVERAGE_VIABLE_THRESHOLD:
        return "viable"
    elif facts_coverage >= _COVERAGE_REWEIGHT_THRESHOLD:
        return "reweight"
    else:
        return "unviable"


# ---------------------------------------------------------------------------
# PRICE arm source function
# ---------------------------------------------------------------------------

def _make_price_source_fn() -> Callable[[date, list, Callable], list]:
    """Return the EP_PRICE source_fn.

    For each floor-eligible (floor_status==ok, ≥252 td history) name at as_of:
    - Compute ret_126 = (close[-1] / close[-127] - 1) * 100
    - Rank all eligible names by ret_126 descending
    - Return top N=50 (ties broken by ticker ascending)
    """

    def source_fn(
        as_of: date,
        universe: list,
        bars_loader: Callable[[str], Optional[pd.DataFrame]],
    ) -> list:
        scored: list[tuple[float, str, object]] = []  # (ret_126, ticker, candidate)
        excl = _PriceExclusionCounts()

        for entry in universe:
            if isinstance(entry, (tuple, list)):
                ticker = entry[0]
            else:
                ticker = entry

            df = bars_loader(ticker)
            if df is None or (hasattr(df, 'empty') and df.empty):
                excl.no_bars += 1
                continue

            # UNIVERSE_V2 floor conformance (charter §2)
            fstatus = floor_status(df, as_of)
            if fstatus == _FLOOR_BELOW:
                excl.below_floor += 1
                continue
            if fstatus == _FLOOR_CORRUPT:
                excl.corrupt_frame += 1
                logger.debug(
                    "epistemics/price: %s excluded — corrupt_frame at %s", ticker, as_of
                )
                continue

            # Slice to as_of point-in-time
            sliced = _df_up_to(df, as_of)
            if sliced.empty:
                excl.no_bars += 1
                continue

            n = len(sliced)

            # §2 condition (b): ≥252 td history
            if n < _MIN_HISTORY_BARS:
                excl.short_history += 1
                logger.debug(
                    "epistemics/price: %s excluded — short_history: %d bars < %d at %s",
                    ticker, n, _MIN_HISTORY_BARS, as_of,
                )
                continue

            # Need ≥127 rows for ret_126 (same condition; already covered by ≥252)
            try:
                close = _get_close(sliced)
            except KeyError:
                excl.no_bars += 1
                continue

            # ret_126 = (close[-1] / close[-127] - 1) * 100
            # close[-127] is index position -(127) = the price 126 trading days prior
            c_now = float(close.iloc[-1])
            c_127 = float(close.iloc[-127])

            if c_127 <= 0 or c_now <= 0:
                excl.no_bars += 1
                continue

            ret_126 = (c_now / c_127 - 1.0) * 100.0
            scored.append((ret_126, ticker, _make_price_candidate(ticker, ret_126)))

        # Sort: descending ret_126, ties broken by ticker ascending
        scored.sort(key=lambda x: (-x[0], x[1]))

        # Take top N
        top_n = scored[:_RANK_N]
        candidates = [c for _, _, c in top_n]

        logger.info(
            "epistemics/price at %s: %d scored from %d universe names "
            "(no_bars=%d, below_floor=%d, corrupt_frame=%d, short_history=%d) "
            "→ top-%d emitted",
            as_of, len(scored), len(universe),
            excl.no_bars, excl.below_floor, excl.corrupt_frame, excl.short_history,
            len(candidates),
        )

        return candidates

    return source_fn


# ---------------------------------------------------------------------------
# FILING arm source function
# ---------------------------------------------------------------------------

def _make_filing_source_fn() -> Callable[[date, list, Callable], list]:
    """Return the EP_FILING source_fn.

    For each floor-eligible (floor_status==ok, ≥252 td history) name with CIK:
    - Compute revenue_yoy_pct via _compute_revenue_yoy_pit(cik, as_of)
    - Names with no parseable PIT revenue YoY → EXCLUDED with counted reason
      (no_fundamentals); contributes to coverage fraction denominator
    - Rank all eligible names with PIT YoY by revenue_yoy_pct descending
    - Return top N=50 (ties broken by ticker ascending)

    Coverage measurement (charter §3):
    - facts_coverage = (floor-eligible names with parseable PIT YoY) /
                       (all floor-eligible names)
    - Logged + persisted in the candidate list as a coverage annotation.
    """

    def source_fn(
        as_of: date,
        universe: list,
        bars_loader: Callable[[str], Optional[pd.DataFrame]],
    ) -> list:
        scored: list[tuple[float, str, object]] = []  # (revenue_yoy_pct, ticker, candidate)
        excl = _FilingExclusionCounts()
        floor_eligible_count: int = 0  # denominator for coverage fraction

        for entry in universe:
            if isinstance(entry, (tuple, list)):
                ticker = entry[0]
                cik = entry[1] if len(entry) >= 2 else ""
            else:
                ticker = entry
                cik = ""

            df = bars_loader(ticker)
            if df is None or (hasattr(df, 'empty') and df.empty):
                excl.no_bars += 1
                continue

            # UNIVERSE_V2 floor conformance (charter §2 — identical to PRICE arm)
            fstatus = floor_status(df, as_of)
            if fstatus == _FLOOR_BELOW:
                excl.below_floor += 1
                continue
            if fstatus == _FLOOR_CORRUPT:
                excl.corrupt_frame += 1
                logger.debug(
                    "epistemics/filing: %s excluded — corrupt_frame at %s", ticker, as_of
                )
                continue

            # Slice to as_of point-in-time
            sliced = _df_up_to(df, as_of)
            if sliced.empty:
                excl.no_bars += 1
                continue

            n = len(sliced)

            # §2 condition (b): ≥252 td history
            if n < _MIN_HISTORY_BARS:
                excl.short_history += 1
                logger.debug(
                    "epistemics/filing: %s excluded — short_history: %d bars < %d at %s",
                    ticker, n, _MIN_HISTORY_BARS, as_of,
                )
                continue

            # This name is floor-eligible (passed floor + history) — counts toward
            # coverage fraction denominator regardless of fundamentals outcome.
            floor_eligible_count += 1

            # §2 condition (c) — FILING arm only: parseable PIT revenue YoY required
            if not cik:
                # No CIK → cannot look up fundamentals → no_fundamentals exclusion
                excl.no_cik += 1
                excl.no_fundamentals += 1
                logger.debug(
                    "epistemics/filing: %s excluded — no_fundamentals (no CIK) at %s",
                    ticker, as_of,
                )
                continue

            revenue_yoy = _compute_revenue_yoy_pit(cik, as_of)

            if revenue_yoy is None:
                # No parseable PIT revenue YoY → no_fundamentals (never imputed, charter §3)
                excl.no_fundamentals += 1
                logger.debug(
                    "epistemics/filing: %s excluded — no_fundamentals "
                    "(no parseable PIT revenue series) at %s cik=%s",
                    ticker, as_of, cik,
                )
                continue

            # PIT YoY available — eligible for rank
            scored.append((revenue_yoy, ticker, _make_filing_candidate(ticker, cik, revenue_yoy)))

        # Coverage measurement (charter §3)
        covered_count = len(scored)
        facts_coverage = (
            covered_count / floor_eligible_count
            if floor_eligible_count > 0 else 0.0
        )
        coverage_tier = compute_coverage_tier(facts_coverage)

        logger.info(
            "epistemics/filing at %s: floor_eligible=%d, covered=%d, no_fundamentals=%d "
            "facts_coverage=%.3f (tier=%s) "
            "(no_bars=%d, below_floor=%d, corrupt_frame=%d, short_history=%d, no_cik=%d)",
            as_of, floor_eligible_count, covered_count, excl.no_fundamentals,
            facts_coverage, coverage_tier,
            excl.no_bars, excl.below_floor, excl.corrupt_frame, excl.short_history,
            excl.no_cik,
        )

        if coverage_tier == "unviable":
            logger.warning(
                "epistemics/filing at %s: UNVIABLE — facts_coverage=%.3f < %.2f threshold. "
                "H1/H2 UNTESTABLE for this window if this pattern holds. "
                "Emitting what is available (%d names).",
                as_of, facts_coverage, _COVERAGE_REWEIGHT_THRESHOLD, len(scored),
            )
        elif coverage_tier == "reweight":
            logger.warning(
                "epistemics/filing at %s: REWEIGHT branch — facts_coverage=%.3f "
                "(0.40 <= c < 0.60). Covered-subset-matched H1 read will fire for this window.",
                as_of, facts_coverage,
            )

        # Sort: descending revenue_yoy_pct, ties broken by ticker ascending
        scored.sort(key=lambda x: (-x[0], x[1]))

        # Take top N
        top_n = scored[:_RANK_N]
        candidates = [c for _, _, c in top_n]

        logger.info(
            "epistemics/filing at %s: %d ranked from %d covered → top-%d emitted",
            as_of, len(scored), covered_count, len(candidates),
        )

        return candidates

    return source_fn


# ---------------------------------------------------------------------------
# Registered config objects
# ---------------------------------------------------------------------------

def _build_config(arm: str) -> object:
    """Build a CandidateSourceConfig for the given epistemics arm.

    arm must be 'price' or 'filing'.
    """
    from turnaround_validation import CandidateSourceConfig

    if arm == "price":
        return CandidateSourceConfig(
            name="epistemics_price",
            direction="long",
            expected_events_per_year=_EXPECTED_EVENTS_PER_YEAR,
            source_fn=_make_price_source_fn(),
            horizons=[21, 63, 126],  # charter §4 — V2_HORIZONS_TRADING_DAYS
        )
    elif arm == "filing":
        return CandidateSourceConfig(
            name="epistemics_filing",
            direction="long",
            expected_events_per_year=_EXPECTED_EVENTS_PER_YEAR,
            source_fn=_make_filing_source_fn(),
            horizons=[21, 63, 126],  # charter §4 — V2_HORIZONS_TRADING_DAYS
        )
    else:
        raise ValueError(
            f"Unknown epistemics arm {arm!r}. "
            f"Allowed arms: 'price', 'filing'. "
            f"Charter §2: only EP_PRICE and EP_FILING are registered."
        )


# PRICE arm config (EP_PRICE) — top-50 by trailing 126td return rank
CONFIG_PRICE = _build_config("price")

# FILING arm config (EP_FILING) — top-50 by PIT revenue YoY rank
CONFIG_FILING = _build_config("filing")
