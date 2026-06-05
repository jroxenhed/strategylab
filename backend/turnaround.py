"""backend/turnaround.py — Pure filter engine for the turnaround screen.

No HTTP calls. No FastAPI imports. Every public function takes `as_of: date`
so Phase 2 (validation) can reuse the same code paths with historical dates.

D1: FilterParams is a pydantic BaseModel (crosses API boundary).
D2: Pure/IO split — evaluate_washed_out(df, as_of, params) is pure;
    is_washed_out() is a thin wrapper that fetches then delegates.
    ONE fetch per symbol (serves both price/volume gate and washed-out check).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameters (D1: pydantic BaseModel — crosses API boundary in ScanRequest)
# ---------------------------------------------------------------------------

class FilterParams(BaseModel):
    price_near_low_pct: float = Field(default=30.0)
    pct_off_high: float = Field(default=50.0)
    price_below_ma_period: int = Field(default=200)
    low_lookback_years: int = Field(default=3)
    high_lookback_years: int = Field(default=3)
    revenue_growth_min_pct: float = Field(default=0.0)
    revenue_consec_quarters: int = Field(default=2)
    gross_margin_min_delta_pct: float = Field(default=-2.0)
    net_income_consec_improving: int = Field(default=2)
    ocf_positive_recent_quarters: int = Field(default=2)
    ps_ratio_max: float = Field(default=3.0)
    insider_buy_months_back: int = Field(default=6)
    buyback_months_back: int = Field(default=12)
    min_price: float = Field(default=1.0)
    max_price: float = Field(default=200.0)
    min_avg_volume: int = Field(default=100_000)
    data_source: str = Field(default="yahoo")


# ---------------------------------------------------------------------------
# Universe v2 preset (Unit 3 / R5)
#
# Liquid, tradeable universe floor params per the Signal-Driven Research Program
# plan (R5: min price $5, meaningful liquidity floor).
#
# F319 junk-suffix hygiene (applied in build_universe) is premise-independent
# and is retained regardless of which preset is in use.
#
# "No washed-out gate" design note:
#   The washed-out gate lives in _process_symbol() and is enforced only when
#   run_filter() is called.  Signal configs that use the pluggable source path
#   (Unit 1 / CandidateSourceConfig) bypass run_filter entirely, so they
#   bypass the washed-out gate naturally.  No FilterParams knob is needed for
#   "gate OFF" — that property is conferred by the pluggable-source path.
#
# USAGE:
#   from turnaround import UNIVERSE_V2
#   params = FilterParams(**UNIVERSE_V2)
#
#   For run_validation with a pluggable source, pass UNIVERSE_V2 as the
#   params dict in the ValidationRequest so Stage 1a (price/volume gate in
#   _process_symbol) uses the v2 floors when bars_loader pre-screens.
#
# EXISTING CONSUMER INVARIANT:
#   The FilterParams() default constructor still gives min_price=1.0 /
#   min_avg_volume=100_000 so existing ScanRequest callers (routes/turnaround.py)
#   are NOT affected. This preset is opt-in — callers must construct FilterParams
#   from UNIVERSE_V2 explicitly.
# ---------------------------------------------------------------------------

UNIVERSE_V2: dict = {
    "min_price": 5.0,
    "min_avg_volume": 500_000,
}


# ---------------------------------------------------------------------------
# Result dataclass (stays dataclass — serialized via dataclasses.asdict)
# ---------------------------------------------------------------------------

@dataclass
class CandidateResult:
    ticker: str
    cik: str
    # Washed-out flags
    price_near_low: bool
    pct_off_high: float
    pct_above_low: float                    # % above N-year low (lower = more washed-out)
    below_ma: bool
    # Fundamental inflection scores
    revenue_yoy_pct: Optional[float]        # most-recent quarter YoY
    revenue_consec_positive: int            # consecutive quarters of positive YoY
    gross_margin_delta_pct: Optional[float] # YoY gross-margin change
    net_income_consec_improving: int
    ocf_positive_quarters: int              # of last 4
    # Valuation
    ps_ratio: Optional[float]
    # Conviction flags
    has_insider_buying: bool
    has_buyback: bool
    # Composite score (0–100 scale, higher = stronger candidate)
    composite_score: float
    # Null flag: passes washed-out check but failed fundamentals
    is_null_candidate: bool
    # Data quality: count of data-gap decisions (valuation/conviction skipped due to missing data)
    data_gap_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df_up_to(df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Slice df to rows with index date <= as_of. Handles DatetimeIndex or date index.

    Live provider frames (_fetch) carry a tz-aware DatetimeIndex
    (America/New_York); comparing that against a naive Timestamp raises
    TypeError. Strip the tz ONCE here (tz_localize(None) keeps the ET
    wall-clock, so the trading date is preserved) — every downstream window
    comparison in evaluate_washed_out then operates naive-vs-naive.
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
    """Return Close series, tolerating lowercase and 'Adj Close' column names."""
    for col in ("Close", "close", "Adj Close", "adj close"):
        if col in df.columns:
            return df[col]
    raise KeyError("No Close column in DataFrame")


def _get_volume(df: pd.DataFrame) -> pd.Series:
    for col in ("Volume", "volume"):
        if col in df.columns:
            return df[col]
    raise KeyError("No Volume column in DataFrame")


def _get_high(df: pd.DataFrame) -> pd.Series:
    for col in ("High", "high"):
        if col in df.columns:
            return df[col]
    raise KeyError("No High column in DataFrame")


def _find_matching_quarter(quarters: list[dict], target_end: str) -> Optional[dict]:
    """Find the quarter in *quarters* whose 'end' is closest to *target_end* (±45d).

    COR-01: used to align GP quarters to the corresponding revenue quarter by period-end
    date rather than by list position (gp_all[-1] may not cover the same fiscal quarter
    as rev_all[-1] when GP has gaps or a different filing cadence).

    Returns None if no quarter is within the 45-day tolerance window.
    """
    target_ts = pd.Timestamp(target_end)
    best: Optional[dict] = None
    best_delta = timedelta(days=46)
    for q in quarters:
        delta = abs(pd.Timestamp(q["end"]) - target_ts)
        if delta.days <= 45 and delta < best_delta:
            best = q
            best_delta = delta
    return best


def _yoy_pair(quarters: list[dict], recent_idx: int) -> Optional[dict]:
    """Find the YoY comparison quarter for quarters[recent_idx] using end±45d tolerance."""
    recent_end = pd.Timestamp(quarters[recent_idx]["end"])
    target = recent_end - pd.Timedelta(days=365)
    best = None
    best_delta = timedelta(days=46)
    for i, q in enumerate(quarters):
        if i == recent_idx:
            continue
        delta = abs(pd.Timestamp(q["end"]) - target)
        if delta.days <= 45 and delta < best_delta:
            best = q
            best_delta = delta
    return best


# ---------------------------------------------------------------------------
# D2: Pure washed-out evaluator (operates on a pre-fetched DataFrame)
# ---------------------------------------------------------------------------

def evaluate_washed_out(
    df: pd.DataFrame,
    as_of: date,
    params: FilterParams,
) -> tuple[bool, dict]:
    """Pure function: check washed-out conditions on daily-bars DataFrame.

    Returns (passes, metrics_dict).
    metrics_dict keys: price, low_N_yr, high_N_yr, ma_200, pct_off_high, pct_above_low.
    df must have DatetimeIndex with Close/High/Volume columns.
    """
    metrics: dict = {
        "price": None,
        "low_N_yr": None,
        "high_N_yr": None,
        "ma_200": None,
        "pct_off_high": None,
        "pct_above_low": None,
    }

    sliced = _df_up_to(df, as_of)
    if sliced.empty:
        return False, metrics

    try:
        close = _get_close(sliced)
    except KeyError:
        return False, metrics

    price = float(close.iloc[-1])
    metrics["price"] = price

    # Lookback windows — low/high use calendar-based slicing (years), not row counts.
    # A year has ~252 trading days but calendar years can vary; iloc-based slicing
    # would over- or under-count by weekends/holidays over 3-year spans.
    # 200-day MA is intentionally trading-day based (rolling(200)), not calendar-based.
    as_of_ts = pd.Timestamp(as_of)
    low_start = as_of_ts - pd.DateOffset(years=params.low_lookback_years)
    high_start = as_of_ts - pd.DateOffset(years=params.high_lookback_years)
    ma_days = params.price_below_ma_period

    low_window = sliced[sliced.index >= low_start]
    high_window = sliced[sliced.index >= high_start]
    # 200-day MA stays trading-day rolling: use the last ma_days rows (trading days)
    ma_window = sliced.iloc[-ma_days:] if len(sliced) >= ma_days else sliced

    try:
        low_close = _get_close(low_window)
        high_close = _get_close(high_window)
        ma_close = _get_close(ma_window)
    except KeyError:
        return False, metrics

    low_N = float(low_close.min())
    high_N = float(high_close.max())
    ma_200 = float(ma_close.mean())

    metrics["low_N_yr"] = low_N
    metrics["high_N_yr"] = high_N
    metrics["ma_200"] = ma_200

    if low_N <= 0 or high_N <= 0:
        return False, metrics

    pct_above_low = (price - low_N) / low_N * 100.0
    pct_off_high_val = (high_N - price) / high_N * 100.0

    metrics["pct_above_low"] = pct_above_low
    metrics["pct_off_high"] = pct_off_high_val

    near_low = pct_above_low <= params.price_near_low_pct
    off_high = pct_off_high_val >= params.pct_off_high
    below_ma = price < ma_200

    passes = near_low and off_high and below_ma
    return passes, metrics


# ---------------------------------------------------------------------------
# D2: Thin wrapper that fetches then delegates
# ---------------------------------------------------------------------------

def is_washed_out(
    ticker: str,
    as_of: date,
    params: FilterParams,
    bars_loader: Optional[Callable[[str], Optional[pd.DataFrame]]] = None,
) -> tuple[bool, dict]:
    """Stage 1a: fetch daily bars then call evaluate_washed_out().

    bars_loader is optional injection point (used by validation to avoid
    per-as_of network calls). Default loader uses _fetch from shared.py.
    """
    metrics: dict = {
        "price": None,
        "low_N_yr": None,
        "high_N_yr": None,
        "ma_200": None,
        "pct_off_high": None,
        "pct_above_low": None,
    }

    if bars_loader is not None:
        df = bars_loader(ticker)
    else:
        df = _default_bars_loader(ticker, as_of, params)

    if df is None or df.empty:
        return False, metrics

    return evaluate_washed_out(df, as_of, params)


def _default_bars_loader(
    ticker: str,
    as_of: date,
    params: FilterParams,
) -> Optional[pd.DataFrame]:
    """Fetch ~(lookback_years + 1y buffer) of daily bars up to as_of.
    Called lazily to avoid circular import at module load time.
    """
    from shared import _fetch

    lookback = max(params.low_lookback_years, params.high_lookback_years)
    start_dt = as_of - timedelta(days=int((lookback + 1) * 365))
    # Use as_of+1 as end so we include the as_of date itself
    end_dt = as_of + timedelta(days=1)
    try:
        df = _fetch(
            ticker,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
            "1d",
            params.data_source,
        )
        return df
    except Exception as exc:
        logger.warning("_fetch failed for %s: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Fundamental inflection (Stage 1b — EDGAR data, point-in-time)
# ---------------------------------------------------------------------------

def _filter_point_in_time(quarters: list[dict], as_of: date) -> list[dict]:
    """Keep only quarters filed on or before as_of."""
    as_of_ts = pd.Timestamp(as_of)
    return [q for q in quarters if pd.Timestamp(q.get("filed", "1900-01-01")) <= as_of_ts]


def _count_consec_positive_yoy(
    quarters: list[dict],
    min_growth_pct: float = 0.0,
) -> tuple[int, Optional[float]]:
    """Count consecutive trailing quarters with YoY growth >= min_growth_pct.

    F326 — SIGN CHANGE REQUIREMENT: requires at least one negative YoY quarter
    BEFORE the consecutive-positive run begins. A name whose entire available
    history is positive YoY returns (0, most_recent_yoy) — "has turned positive"
    cannot be inferred without a prior negative reference. This prevents
    always-positive-but-decelerating former highfliers (GPRO-2015 shape,
    ENPH/CRSR/BOOM 2023) from passing the inflection gate.

    Returns (count, most_recent_yoy_pct).
    Uses end±45d tolerance for YoY pairing.
    """
    if len(quarters) < 2:
        return 0, None

    # ---- Pass 1: collect YoY values for every pairably quarter ----
    yoy_by_idx: dict[int, float] = {}
    for i in range(len(quarters) - 1, -1, -1):
        prior = _yoy_pair(quarters, i)
        if prior is None:
            continue
        current_val = quarters[i].get("val", 0.0) or 0.0
        prior_val = prior.get("val", 0.0) or 0.0
        if prior_val == 0:
            continue
        yoy_by_idx[i] = (current_val - prior_val) / abs(prior_val) * 100.0

    if not yoy_by_idx:
        return 0, None

    sorted_idx = sorted(yoy_by_idx.keys())  # chronological order

    # ---- Pass 2: measure trailing consecutive-positive run ----
    count = 0
    most_recent_yoy: Optional[float] = None
    for i in reversed(sorted_idx):
        yoy = yoy_by_idx[i]
        if most_recent_yoy is None:
            most_recent_yoy = yoy
        if yoy >= min_growth_pct:
            count += 1
        else:
            break

    # ---- Pass 3: sign-change guard — must have ≥1 negative YoY BEFORE the run ----
    # Find the chronological start index of the positive run.
    positive_run_start = None
    for i in reversed(sorted_idx):
        yoy = yoy_by_idx[i]
        if yoy >= min_growth_pct:
            positive_run_start = i
        else:
            break

    if count == 0:
        # No positive run at all — return early, no sign change needed
        return 0, most_recent_yoy

    # There must be at least one paired quarter BEFORE positive_run_start with
    # a negative YoY to confirm the turnaround. If the entire observable
    # history is positive, we cannot infer a turn — fail the gate.
    has_prior_negative = any(
        yoy_by_idx[i] < min_growth_pct
        for i in sorted_idx
        if i < positive_run_start
    )
    if not has_prior_negative:
        # No prior negative quarter observed — cannot confirm sign change
        return 0, most_recent_yoy

    return count, most_recent_yoy


def _count_consec_yoy_improving(quarters: list[dict]) -> int:
    """Count consecutive trailing quarters where value improved vs same quarter a year ago.

    "Improving" = YoY DELTA > 0 (value_t > value_{t-4}), which is correct for
    loss-making turnarounds: −100M → −20M is improving (less negative), and
    current_val > prior_val is True regardless of sign.
    """
    if len(quarters) < 2:
        return 0
    result = 0
    for i in range(len(quarters) - 1, -1, -1):
        prior = _yoy_pair(quarters, i)
        if prior is None:
            break
        current_val = quarters[i].get("val", 0.0) or 0.0
        prior_val = prior.get("val", 0.0) or 0.0
        if current_val > prior_val:
            result += 1
        else:
            break
    return result


def is_fundamental_inflecting(
    cik: str,
    as_of: date,
    params: FilterParams,
) -> tuple[bool, dict]:
    """Stage 1b: check XBRL fundamental inflection (point-in-time).

    Returns (passes, metrics_dict).
    Imports edgar lazily to avoid circular imports / test-collection issues.
    """
    metrics: dict = {
        "revenue_yoy_pct": None,
        "revenue_consec_positive": 0,
        "gross_margin_delta_pct": None,
        "net_income_consec_improving": 0,
        "ocf_positive_quarters": 0,
    }

    try:
        import edgar  # lazy import — may not exist in test env
    except ImportError:
        logger.debug("edgar module not available — skipping fundamentals for %s", cik)
        return False, metrics

    # Fetch all series point-in-time
    try:
        rev_all = _filter_point_in_time(edgar.get_quarterly_revenue(cik), as_of)
        ni_all = _filter_point_in_time(edgar.get_quarterly_net_income(cik), as_of)
        gp_all = _filter_point_in_time(edgar.get_quarterly_gross_profit(cik), as_of)
        ocf_all = _filter_point_in_time(edgar.get_quarterly_ocf(cik), as_of)
        rev_all = sorted(rev_all, key=lambda x: x["end"])
        ni_all = sorted(ni_all, key=lambda x: x["end"])
        gp_all = sorted(gp_all, key=lambda x: x["end"])
        ocf_all = sorted(ocf_all, key=lambda x: x["end"])
    except Exception as exc:
        logger.warning("EDGAR fetch failed for CIK %s: %s", cik, exc)
        return False, metrics

    # ---- Revenue: use last 8 quarters so YoY pairs (need current + prior year) are available ----
    # _count_consec_positive_yoy walks back from the most recent quarter and needs the
    # prior-year counterpart in the same list. 8 quarters = 2 full years of pairings.
    recent_rev = rev_all[-8:] if len(rev_all) >= 8 else rev_all
    rev_consec, rev_yoy = _count_consec_positive_yoy(recent_rev, params.revenue_growth_min_pct)
    metrics["revenue_yoy_pct"] = rev_yoy
    metrics["revenue_consec_positive"] = rev_consec

    # ---- Net income: consecutive YoY improvement ----
    recent_ni = ni_all[-8:] if len(ni_all) >= 8 else ni_all  # need pairs + window
    ni_consec = _count_consec_yoy_improving(recent_ni)
    metrics["net_income_consec_improving"] = ni_consec

    # ---- Gross margin delta (most recent quarter vs YoY) ----
    # COR-01: align GP quarters to the revenue quarter's end date using end±45d
    # tolerance.  gp_all[-1] and rev_all[-1] may cover different fiscal quarters
    # when GP has a different filing cadence or gaps.  If no aligned GP quarter
    # exists for either the recent or prior revenue quarter, treat GM as unavailable
    # (gm_delta = None), consistent with the missing-data convention downstream.
    gm_delta = None
    if len(rev_all) >= 2 and len(gp_all) >= 2:
        try:
            recent_rev_q = rev_all[-1]
            prior_rev_q = _yoy_pair(rev_all, len(rev_all) - 1)
            # Find the GP quarter whose 'end' is closest to the revenue quarter's 'end'
            recent_gp_q = _find_matching_quarter(gp_all, recent_rev_q["end"])
            prior_gp_q = (
                _find_matching_quarter(gp_all, prior_rev_q["end"])
                if prior_rev_q is not None else None
            )
            if (prior_rev_q and recent_gp_q and prior_gp_q
                    and recent_rev_q.get("val", 0) and prior_rev_q.get("val", 0)):
                gm_recent = recent_gp_q.get("val", 0) / recent_rev_q["val"] * 100
                gm_prior = prior_gp_q.get("val", 0) / prior_rev_q["val"] * 100
                gm_delta = gm_recent - gm_prior
        except (ZeroDivisionError, TypeError):
            pass
    metrics["gross_margin_delta_pct"] = gm_delta

    # ---- OCF: positive in >= N of last 4 quarters ----
    recent_ocf = ocf_all[-4:] if len(ocf_all) >= 4 else ocf_all
    ocf_positive = sum(1 for q in recent_ocf if (q.get("val") or 0.0) > 0)
    metrics["ocf_positive_quarters"] = ocf_positive

    # ---- Gate check ----
    passes = (
        rev_consec >= params.revenue_consec_quarters
        and ni_consec >= params.net_income_consec_improving
        and ocf_positive >= params.ocf_positive_recent_quarters
        and (gm_delta is None or gm_delta >= params.gross_margin_min_delta_pct)
    )

    return passes, metrics


# ---------------------------------------------------------------------------
# Valuation (Stage 2)
# ---------------------------------------------------------------------------

def compute_valuation(
    ticker: str,
    cik: str,
    as_of: date,
    params: FilterParams,
    bars_loader: Optional[Callable[[str], Optional[pd.DataFrame]]] = None,
) -> tuple[bool, Optional[float], int]:
    """Return (passes_valuation, ps_ratio, data_gap_count).

    Passes if ps_ratio <= params.ps_ratio_max.
    ps_ratio = market_cap / trailing_12m_revenue.
    market_cap = shares_outstanding * close_price.

    Fail-CLOSED on data errors: missing/error data → does NOT pass valuation.
    Spec principle: never manufacture conviction.
    data_gap_count counts how many data-gap decisions were made (for the
    watchlist payload meta — cheap dict, cheap counter).

    Exception: edgar ImportError → pass through (test/edgar-unavailable env).
    Note: P/S 5-year median fallback is NOT implemented; only the primary
    ps_ratio_max threshold is checked. (Filed as a future F-item.)
    """
    data_gap = 0
    try:
        import edgar
    except ImportError:
        logger.debug("edgar not available — skipping valuation for %s", ticker)
        return True, None, data_gap  # pass through if edgar module unavailable

    try:
        # Get close price as_of
        if bars_loader is not None:
            df = bars_loader(ticker)
        else:
            df = _default_bars_loader(ticker, as_of, params)

        if df is None or df.empty:
            data_gap += 1
            logger.debug("compute_valuation: no price bars for %s — data gap", ticker)
            return False, None, data_gap

        sliced = _df_up_to(df, as_of)
        if sliced.empty:
            data_gap += 1
            logger.debug("compute_valuation: empty sliced bars for %s — data gap", ticker)
            return False, None, data_gap

        price = float(_get_close(sliced).iloc[-1])

        shares = edgar.get_shares_outstanding(cik, as_of)
        if shares is None or shares <= 0:
            data_gap += 1
            logger.debug("compute_valuation: no shares outstanding for CIK %s — data gap", cik)
            return False, None, data_gap

        market_cap = price * shares

        # Trailing 12-month revenue (sum of 4 most-recent quarters filed <= as_of)
        rev_all = _filter_point_in_time(edgar.get_quarterly_revenue(cik), as_of)
        rev_all = sorted(rev_all, key=lambda x: x["end"])
        recent_4 = rev_all[-4:] if len(rev_all) >= 4 else rev_all
        if not recent_4:
            data_gap += 1
            logger.debug("compute_valuation: no revenue quarters for CIK %s — data gap", cik)
            return False, None, data_gap

        ttm_rev = sum(q.get("val", 0.0) or 0.0 for q in recent_4)
        if ttm_rev <= 0:
            data_gap += 1
            logger.debug("compute_valuation: TTM revenue <= 0 for CIK %s — data gap", cik)
            return False, None, data_gap

        ps_ratio = market_cap / ttm_rev

        # Compare against params threshold (primary check only)
        if ps_ratio <= params.ps_ratio_max:
            return True, ps_ratio, data_gap

        return False, ps_ratio, data_gap

    except Exception as exc:
        logger.warning("compute_valuation failed for %s: %s", ticker, exc)
        data_gap += 1
        return False, None, data_gap


# ---------------------------------------------------------------------------
# Conviction flags (Stage 2)
# ---------------------------------------------------------------------------

def check_conviction(
    cik: str,
    as_of: date,
    params: FilterParams,
) -> dict:
    """Return {has_insider_buying: bool, has_buyback: bool}.

    Threads as_of through to the edgar accessors so historical validation
    uses point-in-time conviction data (per pinned cross-fixer interface).
    """
    result = {"has_insider_buying": False, "has_buyback": False}

    try:
        import edgar
    except ImportError:
        logger.debug("edgar not available — skipping conviction for %s", cik)
        return result

    try:
        net_buys = edgar.get_form4_net_buys(cik, params.insider_buy_months_back, as_of=as_of)
        result["has_insider_buying"] = net_buys > 0
    except Exception as exc:
        logger.warning("get_form4_net_buys failed for CIK %s: %s", cik, exc)

    try:
        result["has_buyback"] = edgar.has_buyback_authorization(
            cik, params.buyback_months_back, as_of=as_of
        )
    except Exception as exc:
        logger.warning("has_buyback_authorization failed for CIK %s: %s", cik, exc)

    return result


# ---------------------------------------------------------------------------
# Composite score (0–100, equal pillar weights, additive conviction bonuses)
# ---------------------------------------------------------------------------

def compute_composite_score(candidate: CandidateResult) -> float:
    """Normalized sum of sub-scores (0–100). Equal pillar weights.

    Pillars:
    1. Washed-out depth (pct_off_high, pct_above_low normalized)
    2. Revenue momentum (consec quarters, yoy pct)
    3. Profitability (NI improving, OCF positive rate)
    4. Valuation (ps_ratio inverted)
    5. Gross margin (delta)

    Conviction flags are additive bonuses only — never gatekeepers.
    """
    scores: list[float] = []

    # 1. Washed-out depth — two sub-components averaged:
    #    (a) pct_off_high: higher = more washed-out = better candidate (0–100 norm)
    #    (b) pct_above_low: lower = closer to multi-year low = better (inverted, 0–100 norm)
    #    Combined: cap pct_above_low at 100% (near-low range used in the filter)
    pct_off = min(candidate.pct_off_high, 100.0)
    pct_above = candidate.pct_above_low  # lower = nearer to low = better
    off_high_score = min(pct_off / 100.0, 1.0) * 100.0
    # pct_above_low of 0 → 100 score; pct_above_low >= 100 → 0 score (inverted)
    above_low_score = max(0.0, 100.0 - min(pct_above, 100.0))
    wo_score = (off_high_score + above_low_score) / 2.0
    scores.append(wo_score)

    # 2. Revenue momentum
    rev_yoy = candidate.revenue_yoy_pct or 0.0
    rev_consec = candidate.revenue_consec_positive
    # Consec quarters (normalize 0-4 → 0-60) + yoy pct bonus (0-40)
    rev_score = min(rev_consec / 4.0, 1.0) * 60.0 + min(max(rev_yoy, 0.0) / 50.0, 1.0) * 40.0
    scores.append(rev_score)

    # 3. Profitability
    ni_score = min(candidate.net_income_consec_improving / 4.0, 1.0) * 50.0
    ocf_score = min(candidate.ocf_positive_quarters / 4.0, 1.0) * 50.0
    scores.append((ni_score + ocf_score))

    # 4. Valuation (lower P/S → higher score)
    ps = candidate.ps_ratio
    if ps is None:
        val_score = 50.0  # neutral if unknown
    elif ps <= 0:
        val_score = 100.0
    else:
        # P/S of 0 → 100, P/S of 3 → 50, P/S of 6 → 0
        val_score = max(0.0, 100.0 - (ps / 6.0) * 100.0)
    scores.append(val_score)

    # 5. Gross margin delta
    gm_delta = candidate.gross_margin_delta_pct
    if gm_delta is None:
        gm_score = 50.0  # neutral
    else:
        # Delta of +5pp → 100, Delta of 0 → 50, Delta of -5pp → 0
        gm_score = max(0.0, min(100.0, 50.0 + gm_delta * 10.0))
    scores.append(gm_score)

    base = sum(scores) / len(scores)

    # Conviction bonuses (additive, each +5 pts, capped at 100)
    bonus = 0.0
    if candidate.has_insider_buying:
        bonus += 5.0
    if candidate.has_buyback:
        bonus += 5.0

    return min(100.0, base + bonus)


# ---------------------------------------------------------------------------
# Universe builder (D9: hygiene rules)
# ---------------------------------------------------------------------------

def _is_junk_suffix(ticker: str) -> bool:
    """Return True if ticker matches a known junk-class suffix pattern.

    F319 — Suffix-class exclusion (belt-and-braces, applied in build_universe):
    - 5-char tickers ending W -> SPAC warrant (e.g. MDAIW, KORGW, BDMDW)
    - 5-char tickers ending U -> SPAC unit (e.g. AACBU)
    - 5-char tickers ending R -> SPAC right
    - Any ticker ending Q -> bankruptcy shell (e.g. QVCDQ)
    - 5-char tickers ending F -> foreign OTC pink-sheet (e.g. AAMTF, RTNTF)
    - 5-char tickers ending Y -> foreign OTC ADR/pink-sheet (e.g. KOZAY, YGSHY)

    Legit tickers that must NOT be excluded:
    - GOOGL (5 chars, L suffix) -- passes (L not in the junk suffix set)
    - AAPL (4 chars, L suffix) -- passes (W/U/R/F/Y rules are 5-char only)
    - TSLA (4 chars) -- passes
    """
    t = ticker.upper()
    n = len(t)
    if n == 0:
        return False
    # Any length: ending Q -> bankruptcy shell
    if t.endswith("Q"):
        return True
    # 5-char only: W/U/R -> SPAC warrant/unit/right; F/Y -> foreign OTC
    if n == 5 and t[-1] in ("W", "U", "R", "F", "Y"):
        return True
    return False


def build_universe(
    ticker_cik_map: dict,
    params: Optional[FilterParams] = None,
) -> list[tuple[str, str]]:
    """Filter the raw SEC ticker->CIK map to investable candidates.

    D9:
    - Exclude tickers containing '.' or '-' (warrants/preferred/foreign)
    - Exclude tickers longer than 5 chars
    - D9: cik_str is an int in the JSON -- zero-pad to 10 digits here
    - ORCH-02: exclude companies whose title contains 'ETF', ' Trust',
      or 'Acquisition Corp' (case-insensitive) to cut SPAC/ETF noise.
    - F319: suffix-class exclusion via _is_junk_suffix() -- 5-char W/U/R
      (SPAC warrant/unit/right), any Q (bankruptcy), 5-char F/Y (foreign OTC).

    Returns [(ticker, cik_10digit), ...] in deterministic alphabetical order.
    """
    _NOISE_SUBSTRINGS = ("etf", " trust", "acquisition corp")

    result: list[tuple[str, str]] = []
    for ticker, info in ticker_cik_map.items():
        t = str(ticker).upper().strip()
        if len(t) > 5:
            continue
        if "." in t or "-" in t:
            continue
        # F319: suffix-class exclusion (SPAC warrant/unit/right, bankruptcy, foreign OTC)
        if _is_junk_suffix(t):
            continue
        # Exclude ETF/Trust/SPAC by company title (case-insensitive substring match)
        title = str(info.get("title", "")).lower()
        if any(noise in title for noise in _NOISE_SUBSTRINGS):
            continue
        # Zero-pad CIK to 10 digits at the edgar.py boundary
        raw_cik = info.get("cik_str", "")
        try:
            cik_padded = str(int(raw_cik)).zfill(10)
        except (ValueError, TypeError):
            continue
        result.append((t, cik_padded))

    result.sort(key=lambda x: x[0])
    return result


# ---------------------------------------------------------------------------
# Main filter runner (D2: accepts injectable bars_loader)
# ---------------------------------------------------------------------------

def run_filter(
    universe: list[tuple[str, str]],   # [(ticker, cik), ...]
    as_of: date,
    params: Optional[FilterParams] = None,
    bars_loader: Optional[Callable[[str], Optional[pd.DataFrame]]] = None,
) -> list[CandidateResult]:
    """Run the full cheap-first funnel across universe.

    Funnel order (cheap-first per brief):
      1. Price range + volume gate (no EDGAR, no _fetch MA) — uses bars if available
      2. Washed-out price (calls evaluate_washed_out on fetched bars)
      3. Fundamental inflection (calls edgar.py — only for washed-out survivors)
      4. Valuation + conviction (only for fundamentals survivors)

    D2: bars_loader injected by validation for memoized per-symbol full-span fetch.
    Default loader fetches from _fetch per call.
    Returns candidates sorted by composite_score descending.
    Failed symbols are logged and skipped.
    """
    if params is None:
        params = FilterParams()

    candidates: list[CandidateResult] = []

    for ticker, cik in universe:
        try:
            _process_symbol(ticker, cik, as_of, params, bars_loader, candidates)
        except Exception as exc:
            logger.warning("Unexpected error processing %s: %s", ticker, exc)

    candidates.sort(key=lambda c: c.composite_score, reverse=True)
    return candidates


def _process_symbol(
    ticker: str,
    cik: str,
    as_of: date,
    params: FilterParams,
    bars_loader: Optional[Callable[[str], Optional[pd.DataFrame]]],
    out: list[CandidateResult],
) -> None:
    """Process a single symbol through the funnel. Appends to out on pass."""

    # ---- Stage 1: Fetch bars (one fetch serves both gates) ----
    if bars_loader is not None:
        df = bars_loader(ticker)
    else:
        df = _default_bars_loader(ticker, as_of, params)

    if df is None or df.empty:
        logger.debug("No bars for %s — skipping", ticker)
        return

    sliced = _df_up_to(df, as_of)
    if sliced.empty:
        logger.debug("No bars up to %s for %s — skipping", as_of, ticker)
        return

    # ---- Stage 1a: Price range + volume gate (cheap, no MA) ----
    try:
        close_s = _get_close(sliced)
        vol_s = _get_volume(sliced)
        price = float(close_s.iloc[-1])
    except (KeyError, IndexError):
        logger.debug("Missing Close/Volume for %s — skipping", ticker)
        return

    if price < params.min_price or price > params.max_price:
        logger.debug("Price %s out of range for %s — skipping", price, ticker)
        return

    # 30-day avg volume
    recent_30 = vol_s.iloc[-30:]
    avg_vol = float(recent_30.mean()) if len(recent_30) > 0 else 0.0
    if avg_vol < params.min_avg_volume:
        logger.debug("Avg vol %.0f < min for %s — skipping", avg_vol, ticker)
        return

    # ---- Stage 1b: Washed-out check using the already-fetched df ----
    # Pass bars_loader=None to avoid double-fetch; use a closure that returns df
    _cached_loader: Callable[[str], Optional[pd.DataFrame]] = lambda _t: df
    wo_passes, wo_metrics = is_washed_out(ticker, as_of, params, _cached_loader)

    if not wo_passes:
        logger.debug("Washed-out check failed for %s", ticker)
        return

    # ---- Stage 2: Fundamental inflection ----
    fund_passes, fund_metrics = is_fundamental_inflecting(cik, as_of, params)
    val_passes, ps_ratio, val_data_gap = compute_valuation(ticker, cik, as_of, params, _cached_loader)

    is_null = wo_passes and not fund_passes
    # For fundamentals failures that also fail valuation, still is_null

    # Only compute conviction for full passes (fund + val)
    full_pass = fund_passes and val_passes
    if full_pass:
        conviction = check_conviction(cik, as_of, params)
    else:
        conviction = {"has_insider_buying": False, "has_buyback": False}

    candidate = CandidateResult(
        ticker=ticker,
        cik=cik,
        price_near_low=bool(wo_metrics.get("pct_above_low", 100.0) <= params.price_near_low_pct),
        pct_off_high=float(wo_metrics.get("pct_off_high") or 0.0),
        pct_above_low=float(wo_metrics.get("pct_above_low") or 0.0),
        below_ma=bool(price < (wo_metrics.get("ma_200") or float("inf"))),
        revenue_yoy_pct=fund_metrics.get("revenue_yoy_pct"),
        revenue_consec_positive=int(fund_metrics.get("revenue_consec_positive", 0)),
        gross_margin_delta_pct=fund_metrics.get("gross_margin_delta_pct"),
        net_income_consec_improving=int(fund_metrics.get("net_income_consec_improving", 0)),
        ocf_positive_quarters=int(fund_metrics.get("ocf_positive_quarters", 0)),
        ps_ratio=ps_ratio,
        has_insider_buying=bool(conviction.get("has_insider_buying", False)),
        has_buyback=bool(conviction.get("has_buyback", False)),
        composite_score=0.0,
        is_null_candidate=is_null,
        data_gap_count=val_data_gap,
    )
    candidate.composite_score = compute_composite_score(candidate)

    # Include both signal and null candidates (D8: caller filters nulls)
    out.append(candidate)
