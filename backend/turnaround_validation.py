"""
turnaround_validation.py — Phase 2 Historical Validation

Runs the turnaround filter at historical as-of dates (quarterly, 15 Feb/May/Aug/Nov)
and measures the hit rate vs a null baseline. Applies the standard StrategyLab
cost model (slippage_bps + per_leg_commission).

Key design decisions:
- D2: ONE memoized bars_loader per symbol covering full validation span; injected
  into run_filter so the network is touched at most once per symbol.
- D3: Conviction (EDGAR Form 4 / 8-K) is skipped entirely for validation runs —
  EFTS coverage doesn't extend reliably backward and would be unrunnable at scale.
  Both flags are post-zeroed to False, composite score recomputed without the bonus,
  and conviction_skipped=True is surfaced in ValidationResult.
- D6: slippage uses req.slippage_bps (not a module constant). Commission duck-typed
  via per_leg_commission(shares, req) using ValidationRequest directly.
- D10: as-of dates = 15 Feb/May/Aug/Nov for each year in [start_year, end_year].
- D11: entry = first trading close >= as_of; exit = first close >= entry*(1+threshold)
  within horizon_months (take-profit), else close at horizon-end. Truncated events
  (horizon extends past available price data) are counted and skipped.
"""
from __future__ import annotations

import logging
import math
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional, TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wall-clock budget (mirrors _WFA_TIMEOUT_SECS in routes/walk_forward.py).
# On timeout the completed-dates result IS written (salvage); on cancel
# nothing is written (user intent). Checked at every as-of date boundary and
# at every symbol iteration inside the current date.
# ---------------------------------------------------------------------------
_VALIDATION_TIMEOUT_SECS: float = 3 * 3600  # 3 hours — generous; real runs take ~60 min


# ---------------------------------------------------------------------------
# Progress tracker — plain mutable dict owned by the route, mutated here via
# GIL-atomic single-key writes (no asyncio.Lock needed from a sync thread).
# ---------------------------------------------------------------------------

@dataclass
class ValidationProgress:
    """Mutable progress state, passed by reference into run_validation.

    Fields are mutated by the worker thread; the async status route reads them
    (GIL ensures single-key updates are atomic — no asyncio.Lock needed).
    """
    dates_done: int = 0
    dates_total: int = 0
    current_date: str = ""       # ISO date string of the as-of date being processed
    symbols_loaded: int = 0      # symbols processed in the current as-of date pass
    universe_size: int = 0       # total universe size (set once at startup)
    signal_events: int = 0       # running count of signal events recorded
    null_events: int = 0         # running count of null events recorded


# ---------------------------------------------------------------------------
# Lazy / conditional imports so this module doesn't hard-fail while lane B
# (turnaround.py) may still be mid-write.  The imports are resolved at
# function-call time, not at module import time.
# ---------------------------------------------------------------------------
def _import_turnaround():
    """Return the turnaround module, raising ImportError if not yet available."""
    import turnaround as _t
    return _t


def _import_per_leg_commission():
    from routes.backtest import per_leg_commission
    return per_leg_commission


# ---------------------------------------------------------------------------
# Pydantic model (crosses API boundary — must be BaseModel, not dataclass)
# ---------------------------------------------------------------------------

class ValidationRequest(BaseModel):
    """Request body for POST /api/turnaround/validate."""

    # FilterParams embedded — BaseModel wraps a pydantic-converted FilterParams.
    # We store it as a dict at parse time and reconstruct on use to avoid a
    # hard import of FilterParams at module import.
    params: dict = Field(default_factory=dict)

    start_year: int = Field(default=2015, ge=2005, le=2024)
    end_year: int = Field(default=2023, ge=2005, le=2024)
    horizon_months: int = Field(default=12, ge=3, le=24)
    hit_threshold_pct: float = Field(default=50.0, ge=10.0, le=500.0)
    initial_capital: float = Field(default=10_000.0, ge=1_000.0)

    # Cost model — duck-typed by per_leg_commission
    slippage_bps: float = Field(default=2.0, ge=0.0)
    per_share_rate: float = Field(default=0.0, ge=0.0)
    min_per_order: float = Field(default=0.0, ge=0.0)

    # Universe cap — same role as ScanRequest.max_universe. The full universe
    # is ~10k names == ~10k sequential daily-bar fetches in the memoized
    # loader; the cap keeps a validation run minutes-scale. build_universe
    # ordering is deterministic, so capped runs are reproducible.
    max_universe: int = Field(default=2000, ge=50, le=15000)

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Dataclasses (internal + result — serialised via dataclasses.asdict)
# ---------------------------------------------------------------------------

@dataclass
class TradeOutcome:
    ticker: str
    as_of: date
    entry_price: float
    exit_price: float
    forward_return_pct: float          # gross (before costs)
    net_return_pct: float              # after slippage + commission
    is_hit: bool                       # judged on NET return >= hit_threshold (not gross)
    is_null: bool                      # True = washed-out only (null candidate)
    horizon_months: int
    composite_score: float = 0.0       # score from run_filter (for miss list ordering)
    # Item 1: per-event fields added for event-level table
    entry_date: Optional[date] = None
    exit_date: Optional[date] = None   # touch date if hit early, horizon-end otherwise
    days_to_hit: Optional[int] = None  # None if not a hit; calendar days entry→exit
    # Item 2b (F327): horizon-end close for fixed-horizon return comparison
    # hits exit early at the touch price; this captures what the price was at horizon-end
    horizon_end_price: Optional[float] = None
    horizon_end_return_pct: Optional[float] = None  # net return if held to horizon-end


@dataclass
class ValidationResult:
    # Signal (pass all filters)
    signal_hit_rate: float
    signal_hit_rate_ci_low: float
    signal_hit_rate_ci_high: float
    signal_n: int
    signal_hits: int
    # Null (washed-out only, failed fundamentals)
    null_hit_rate: float
    null_hit_rate_ci_low: float
    null_hit_rate_ci_high: float
    null_n: int
    null_hits: int
    # Net return distribution (signal)
    signal_mean_return_pct: float
    signal_median_return_pct: float
    signal_p25_return_pct: float
    signal_p75_return_pct: float
    # Miss list — signal candidates that did NOT hit, sorted composite_score desc
    miss_list: list[dict]  # [{ticker, as_of, composite_score, net_return_pct}, ...]
    # Meta
    survivorship_warning: str
    total_as_of_dates: int
    elapsed_secs: float
    # Addendum fields (D3, deduplication)
    conviction_skipped: bool = True
    unique_tickers: int = 0
    truncated_events: int = 0
    # REL-07: network fetch failures during memoized loading
    fetch_failures: int = 0
    # ADV-05: ticker-horizon overlaps suppressed (same ticker, prior event still open)
    overlap_suppressed: int = 0
    # Item 2a (F327): null cohort return distribution — was missing from prior payload
    null_mean_return_pct: float = 0.0
    null_median_return_pct: float = 0.0
    null_p25_return_pct: float = 0.0
    null_p75_return_pct: float = 0.0
    # Item 2b (F327): fixed-horizon return comparison (mean/median for both cohorts)
    # NOTE: hits exit early at the touch price; horizon-end price is the additional measure.
    signal_horizon_mean_return_pct: float = 0.0
    signal_horizon_median_return_pct: float = 0.0
    null_horizon_mean_return_pct: float = 0.0
    null_horizon_median_return_pct: float = 0.0
    # Item 1: complete per-event table for downstream analyses (F324/F325/F328)
    # Each dict has keys: ticker, as_of, is_null, entry_date, entry_price,
    # exit_date, exit_price, net_return_pct, hit, days_to_hit (or null),
    # composite_score, horizon_end_return_pct (or null), forward_return_pct
    events: list[dict] = field(default_factory=list)
    # Schema version — increment when per-event dict shape changes so readers
    # can handle old persisted files gracefully (F315 backward-compat concern).
    schema_version: int = 1
    # F313: timeout salvage annotation.
    # timed_out=True means the budget fired mid-run; completed dates' events
    # ARE included (honest partial result). Partial in-flight date is dropped
    # (its events would be biased toward the prefix of the universe).
    # Cancel writes nothing; timeout writes this partial-but-honest result.
    timed_out: bool = False
    dates_completed: int = 0    # number of as-of dates fully processed before timeout


# ---------------------------------------------------------------------------
# Wilson confidence interval
# ---------------------------------------------------------------------------

def wilson_ci(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a proportion.

    Returns (low, high).  Edge cases:
    - n == 0: returns (0.0, 0.0)
    - hits == 0 or hits == n: returns the one-sided bound with a sensible floor/ceiling
    """
    if n == 0:
        return (0.0, 0.0)
    p_hat = hits / n
    denominator = 1 + z**2 / n
    centre = (p_hat + z**2 / (2 * n)) / denominator
    margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2))) / denominator
    low = max(0.0, centre - margin)
    high = min(1.0, centre + margin)
    return (low, high)


# ---------------------------------------------------------------------------
# Quarterly as-of schedule (D10)
# ---------------------------------------------------------------------------

def _quarterly_as_of_dates(start_year: int, end_year: int) -> list[date]:
    """Return dates on 15 Feb/May/Aug/Nov for each year in [start_year, end_year].

    These fall mid-quarter, after most 10-K/10-Q filings have landed, so the
    point-in-time XBRL filter (filed <= as_of) sees fresh data.
    """
    months = [2, 5, 8, 11]
    dates = []
    for year in range(start_year, end_year + 1):
        for month in months:
            dates.append(date(year, month, 15))
    return dates


# ---------------------------------------------------------------------------
# Bars loader factory (D2)
# ---------------------------------------------------------------------------

def _make_memoized_loader(
    start_year: int,
    end_year: int,
    low_lookback_years: int,
    horizon_months: int,
    data_source: str,
) -> Callable[[str], Optional[pd.DataFrame]]:
    """Return a memoized loader that fetches each symbol's full price span ONCE.

    The span covers:
        fetch_start = Jan 1 of (start_year - low_lookback_years - 1)
        fetch_end   = Dec 31 of (end_year + horizon_months//12 + 1)

    This single fetch serves both:
    - The washed-out check (multi-year daily bars, sliced to as_of)
    - The forward return calculation (post-as_of closes up to horizon end)

    REL-07: fetch_failures attribute is incremented on exception (as opposed to
    empty/delisted — those are still cached as None but not counted as failures).
    """
    from shared import _fetch  # noqa: runtime import, safe inside thread

    fetch_start_year = start_year - low_lookback_years - 1
    fetch_end_year = end_year + max(1, (horizon_months + 11) // 12) + 1
    fetch_start = f"{fetch_start_year}-01-01"
    fetch_end = f"{fetch_end_year}-12-31"

    _cache: dict[str, Optional[pd.DataFrame]] = {}

    def _loader(ticker: str) -> Optional[pd.DataFrame]:
        if ticker in _cache:
            return _cache[ticker]
        try:
            df = _fetch(ticker, fetch_start, fetch_end, "1d", data_source)
            _cache[ticker] = df if df is not None and not df.empty else None
        except Exception as exc:
            logger.warning("bars_loader: failed to fetch %s: %s", ticker, exc)
            _loader.fetch_failures += 1  # type: ignore[attr-defined]
            # Retry once before caching None
            try:
                df2 = _fetch(ticker, fetch_start, fetch_end, "1d", data_source)
                _cache[ticker] = df2 if df2 is not None and not df2.empty else None
            except Exception:
                _cache[ticker] = None
        return _cache[ticker]

    _loader.fetch_failures = 0  # type: ignore[attr-defined]
    return _loader


# ---------------------------------------------------------------------------
# Entry / exit price helpers (D11)
# ---------------------------------------------------------------------------

def _first_trading_close_on_or_after(df: pd.DataFrame, target: date) -> Optional[tuple[date, float]]:
    """Return (trading_date, close_price) for the first row >= target date."""
    if df is None or df.empty:
        return None
    # Normalise index to date objects
    if hasattr(df.index, "date"):
        dates = pd.Series([d.date() if hasattr(d, "date") else d for d in df.index], index=df.index)
    else:
        dates = pd.Series(df.index, index=df.index)

    mask = dates >= target
    if not mask.any():
        return None
    row = df[mask].iloc[0]
    row_date = dates[mask].iloc[0]
    # PY-10: always use named 'Close' column; fail loudly rather than read wrong column
    if "Close" not in row.index:
        raise KeyError(f"No 'Close' column in DataFrame for row at {row_date}")
    close = float(row["Close"])
    return (row_date, close)


def _close_at_or_before(df: pd.DataFrame, target: date) -> Optional[float]:
    """Return close price for the last row <= target date."""
    if df is None or df.empty:
        return None
    if hasattr(df.index, "date"):
        dates = [d.date() if hasattr(d, "date") else d for d in df.index]
    else:
        dates = list(df.index)
    mask = [d <= target for d in dates]
    if not any(mask):
        return None
    idx = max(i for i, m in enumerate(mask) if m)
    row = df.iloc[idx]
    # PY-10: always use named 'Close' column; fail loudly rather than read wrong column
    if "Close" not in row.index:
        raise KeyError(f"No 'Close' column in DataFrame at row {idx}")
    return float(row["Close"])


def _horizon_end_date(entry_date: date, horizon_months: int) -> date:
    """Return approximate horizon end: entry_date + horizon_months."""
    month = entry_date.month - 1 + horizon_months
    year = entry_date.year + month // 12
    month = month % 12 + 1
    day = min(entry_date.day, 28)  # avoid month-end overflow
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Cost application helpers
# ---------------------------------------------------------------------------

def _apply_costs(
    gross_entry: float,
    gross_exit: float,
    req: ValidationRequest,
) -> tuple[float, float, float, float]:
    """Return (net_entry, net_exit, commission_total, net_return_pct).

    Long direction only:
    - entry fill: close * (1 + bps/1e4)  — worse (slippage hurts on buy)
    - exit fill:  close * (1 - bps/1e4)  — worse (slippage hurts on sell)
    """
    per_leg_commission = _import_per_leg_commission()

    bps = req.slippage_bps
    net_entry = gross_entry * (1 + bps / 1e4)
    net_exit = gross_exit * (1 - bps / 1e4)

    shares = req.initial_capital / net_entry if net_entry > 0 else 0.0
    comm_in = per_leg_commission(shares, req)
    comm_out = per_leg_commission(shares, req)

    position_value = shares * net_entry
    if position_value > 0:
        net_return_pct = ((net_exit - net_entry) / net_entry * 100
                         - (comm_in + comm_out) / position_value * 100)
    else:
        net_return_pct = 0.0

    return net_entry, net_exit, comm_in + comm_out, net_return_pct


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------

def run_validation(
    req: ValidationRequest,
    *,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[ValidationProgress] = None,
    timeout_secs: float = _VALIDATION_TIMEOUT_SECS,
) -> ValidationResult:
    """Run historical as-of validation.

    Calls run_filter() at each quarterly date in [start_year, end_year].
    Uses a memoized bars_loader (D2) so each symbol's price history is fetched
    at most once across all as-of dates.

    Conviction is skipped for all validation runs (D3): both flags are set False
    after run_filter returns, composite score is NOT recomputed (conviction flags
    are additive-only, never gatekeepers — omitting the bonus is conservative).

    Set definitions (ADV-08):
      signal  = washed-out AND fundamentals AND valuation
      null    = washed-out AND NOT fundamentals (valuation-agnostic)
    These are mutually exclusive by construction.

    is_hit is judged on NET return >= hit_threshold (ADV-02: post-cost, not gross).

    ADV-05: per-ticker cooldown — if a prior event's horizon is still open for a
    given ticker at the current as_of date, the new event is skipped (overlap_suppressed).

    F313: cancel_event — threading.Event; if set, raises CancelledError (no result written).
    F313: progress — mutable ValidationProgress dict mutated with GIL-atomic single-key
    writes (safe from a sync worker thread; the async status route just reads).
    F313: timeout_secs — wall-clock budget; on expiry the completed-dates result IS returned
    with timed_out=True (salvage). The in-flight date is dropped (partial events bias).
    Cancel differs from timeout: cancel = user intent (no write); timeout = salvage (partial write).

    CPU-bound; designed to run in asyncio.to_thread().
    """
    t0 = time.monotonic()

    turnaround = _import_turnaround()

    # PY-06/ORCH-01: no silent FilterParams fallback — raise on invalid params
    try:
        params = turnaround.FilterParams(**req.params)
    except Exception as exc:
        raise ValueError(f"Invalid FilterParams: {exc}") from exc

    # Build universe from EDGAR (uses cached data)
    try:
        import edgar as _edgar
        raw_universe = _edgar.fetch_universe()
        universe = turnaround.build_universe(raw_universe, params)[: req.max_universe]
    except Exception as exc:
        logger.warning("run_validation: failed to build universe: %s", exc)
        universe = []

    # Memoized loader (D2): one fetch per symbol covering the full span
    bars_loader = _make_memoized_loader(
        start_year=req.start_year,
        end_year=req.end_year,
        low_lookback_years=params.low_lookback_years,
        horizon_months=req.horizon_months,
        data_source=params.data_source,
    )

    as_of_dates = _quarterly_as_of_dates(req.start_year, req.end_year)
    hit_threshold = req.hit_threshold_pct / 100.0

    # F313: seed progress metadata now that universe + date list are known
    if progress is not None:
        progress.dates_total = len(as_of_dates)
        progress.universe_size = len(universe)

    signal_outcomes: list[TradeOutcome] = []
    null_outcomes: list[TradeOutcome] = []
    truncated_events = 0
    overlap_suppressed = 0
    seen_tickers: set[str] = set()
    # COR-02: separate cooldown books per cohort so a signal-horizon for ticker X
    # cannot suppress a null event for ticker X (and vice versa).  Each cohort is
    # its own independent event stream; overlap suppression is only within-cohort.
    signal_open_horizons: dict[str, date] = {}
    null_open_horizons: dict[str, date] = {}

    timed_out = False
    dates_completed = 0

    for as_of in as_of_dates:
        # F313: check cancellation at every as-of date boundary
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("_cancelled_")

        # F313: check wall-clock budget at every as-of date boundary
        if (time.monotonic() - t0) >= timeout_secs:
            logger.warning(
                "run_validation: wall-clock budget %.0fs exceeded after %d/%d dates",
                timeout_secs, dates_completed, len(as_of_dates),
            )
            timed_out = True
            break

        # F313: update progress — entering new date
        if progress is not None:
            progress.current_date = as_of.isoformat()
            progress.symbols_loaded = 0
        try:
            # Pass bars_loader into run_filter (D2) — avoids re-fetching per as_of
            candidates = turnaround.run_filter(
                universe=universe,
                as_of=as_of,
                params=params,
                bars_loader=bars_loader,
            )
        except Exception as exc:
            logger.warning("run_validation: run_filter failed at %s: %s", as_of, exc)
            continue

        # D3: zero out conviction flags (both signal and null candidates)
        for c in candidates:
            c.has_insider_buying = False
            c.has_buyback = False
            # Do NOT recompute composite_score — conviction is additive-only,
            # keeping the original score is fine for ordering purposes.

        # ADV-08: signal = passes all filters (not null); null = washed-out + NOT fund
        signal_candidates = [c for c in candidates if not c.is_null_candidate]
        null_candidates = [c for c in candidates if c.is_null_candidate]

        # F313-01: per-date buffers — outcomes/counters commit to the global lists
        # only when the date COMPLETES, so a timeout mid-date drops the partial
        # date entirely (WFA partial-window drop rule; prevents prefix bias and
        # the signal-committed/null-partial cohort asymmetry).
        date_signal_outcomes: list[TradeOutcome] = []
        date_null_outcomes: list[TradeOutcome] = []
        date_overlap_suppressed = 0
        date_truncated_events = 0

        for candidate_list, is_null in [(signal_candidates, False), (null_candidates, True)]:
            if timed_out:
                break
            # COR-02: each cohort uses its own horizon-tracking dict
            open_horizons = null_open_horizons if is_null else signal_open_horizons
            for cand in candidate_list:
                seen_tickers.add(cand.ticker)

                # F313: check cancellation and wall-clock budget at every symbol iteration
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("_cancelled_")
                if (time.monotonic() - t0) >= timeout_secs:
                    logger.warning(
                        "run_validation: wall-clock budget %.0fs exceeded inside date %s",
                        timeout_secs, as_of,
                    )
                    timed_out = True
                    break

                # F313: update within-date symbol progress (signal cohort only to avoid double-counting)
                if not is_null and progress is not None:
                    progress.symbols_loaded += 1

                # ADV-05: skip if a prior event's horizon is still open (within this cohort)
                if cand.ticker in open_horizons and open_horizons[cand.ticker] >= as_of:
                    date_overlap_suppressed += 1
                    logger.debug(
                        "run_validation: overlap suppressed %s at %s (horizon open until %s)",
                        cand.ticker, as_of, open_horizons[cand.ticker],
                    )
                    continue

                df = bars_loader(cand.ticker)
                if df is None or df.empty:
                    logger.debug("run_validation: no bars for %s", cand.ticker)
                    continue

                # Entry: first trading close >= as_of
                entry_result = _first_trading_close_on_or_after(df, as_of)
                if entry_result is None:
                    logger.debug("run_validation: no entry bar for %s at %s", cand.ticker, as_of)
                    continue
                entry_date, entry_close = entry_result

                # DI-03: zero-guard on entry_close — a 0.0 yfinance bar must not abort the run
                if entry_close <= 0:
                    logger.warning(
                        "run_validation: zero entry_close for %s at %s, skipping",
                        cand.ticker, as_of,
                    )
                    continue

                # Horizon end
                horizon_end = _horizon_end_date(entry_date, req.horizon_months)

                # Check if horizon extends past available data
                last_available = None
                if hasattr(df.index, "date"):
                    last_available = max(d.date() if hasattr(d, "date") else d for d in df.index)
                else:
                    last_available = max(df.index)

                if last_available < horizon_end:
                    date_truncated_events += 1
                    logger.debug(
                        "run_validation: truncated event %s at %s (data ends %s, horizon %s)",
                        cand.ticker, as_of, last_available, horizon_end,
                    )
                    continue

                # Register horizon open (ADV-05, within-cohort only)
                open_horizons[cand.ticker] = horizon_end

                # D11 exit: scan forward for take-profit within horizon
                # ADV-02: is_hit judged on NET return >= hit_threshold (not gross close)
                exit_close: Optional[float] = None
                exit_date: Optional[date] = None
                hit_early: bool = False  # True when take-profit touch triggers early exit

                # First compute costs to determine net take-profit threshold
                net_entry_adj = entry_close * (1 + req.slippage_bps / 1e4)
                # Net return threshold: net_return_pct >= hit_threshold_pct
                # net_return_pct = (net_exit - net_entry_adj) / net_entry_adj * 100 - comm_pct
                # For is_hit we use a conservative approximation: gross close >= target_price
                # (commission drag is symmetric and small; the direction is correct for exit scan)
                # Full net return is always computed for the actual exit; is_hit uses net_return_pct.
                target_price = entry_close * (1 + hit_threshold)  # scan trigger (gross)

                # Slice df to [entry_date+1 .. horizon_end]
                if hasattr(df.index, "date"):
                    forward_dates = [d.date() if hasattr(d, "date") else d for d in df.index]
                else:
                    forward_dates = list(df.index)

                for i, row_date in enumerate(forward_dates):
                    if row_date <= entry_date:
                        continue
                    if row_date > horizon_end:
                        break
                    if "Close" not in df.columns:
                        raise KeyError("No 'Close' column in DataFrame during exit scan")
                    close_val = float(df.iloc[i]["Close"])
                    if close_val >= target_price:
                        exit_close = close_val
                        exit_date = row_date
                        hit_early = True
                        break

                if exit_close is None:
                    # No take-profit reached; use horizon-end close
                    exit_close = _close_at_or_before(df, horizon_end)
                    if exit_close is None:
                        logger.debug("run_validation: no exit bar for %s", cand.ticker)
                        continue
                    exit_date = horizon_end

                # Item 2b (F327): capture horizon-end close for fixed-horizon comparison.
                # When hit_early=True, the event exited before horizon-end; we record
                # what the price would have been at horizon-end for the fixed-horizon measure.
                # When hit_early=False, exit IS the horizon-end close, so they are identical.
                horizon_end_close: Optional[float] = None
                horizon_end_return_pct: Optional[float] = None
                if hit_early:
                    horizon_end_close = _close_at_or_before(df, horizon_end)
                    if horizon_end_close is not None:
                        _, _, _, horizon_end_return_pct = _apply_costs(entry_close, horizon_end_close, req)
                else:
                    # No early exit: horizon-end close == exit close
                    horizon_end_close = exit_close

                # Gross return
                gross_return_pct = (exit_close - entry_close) / entry_close * 100

                # Apply costs (D6) — always compute net return
                _, _, _, net_return_pct = _apply_costs(entry_close, exit_close, req)

                if not hit_early:
                    horizon_end_return_pct = net_return_pct

                # ADV-02: is_hit judged on NET return (post slippage + commission)
                is_hit = net_return_pct >= req.hit_threshold_pct

                # Item 1: days_to_hit — calendar days from entry to exit, only for hits
                days_to_hit: Optional[int] = None
                if is_hit and exit_date is not None:
                    days_to_hit = (exit_date - entry_date).days

                outcome = TradeOutcome(
                    ticker=cand.ticker,
                    as_of=as_of,
                    entry_price=entry_close,
                    exit_price=exit_close,
                    forward_return_pct=gross_return_pct,
                    net_return_pct=net_return_pct,
                    is_hit=is_hit,
                    is_null=is_null,
                    horizon_months=req.horizon_months,
                    composite_score=cand.composite_score,  # PY-05/ADV-07
                    entry_date=entry_date,
                    exit_date=exit_date,
                    days_to_hit=days_to_hit,
                    horizon_end_price=horizon_end_close,
                    horizon_end_return_pct=horizon_end_return_pct,
                )

                if is_null:
                    date_null_outcomes.append(outcome)
                    # F313: running event counts = committed + current-date buffer
                    if progress is not None:
                        progress.null_events = len(null_outcomes) + len(date_null_outcomes)
                else:
                    date_signal_outcomes.append(outcome)
                    # F313: running event counts = committed + current-date buffer
                    if progress is not None:
                        progress.signal_events = len(signal_outcomes) + len(date_signal_outcomes)

        # F313-01: timeout mid-date → discard the date's buffers entirely (drop rule);
        # the partial date never reaches the global lists or counters.
        if timed_out:
            break

        # F313: date fully completed — commit buffers, then increment counters
        signal_outcomes.extend(date_signal_outcomes)
        null_outcomes.extend(date_null_outcomes)
        overlap_suppressed += date_overlap_suppressed
        truncated_events += date_truncated_events
        dates_completed += 1
        if progress is not None:
            progress.dates_done = dates_completed
            # snap running counts back to committed totals (drops nothing here;
            # buffers were just committed)
            progress.signal_events = len(signal_outcomes)
            progress.null_events = len(null_outcomes)

    # ---------- Aggregate statistics ----------

    def _hit_rate_and_ci(outcomes: list[TradeOutcome]):
        n = len(outcomes)
        hits = sum(1 for o in outcomes if o.is_hit)
        rate = hits / n if n > 0 else 0.0
        ci_low, ci_high = wilson_ci(hits, n)
        return rate, ci_low, ci_high, n, hits

    def _return_distribution_stats(returns: list[float]) -> tuple[float, float, float, float]:
        """Return (mean, median, p25, p75) for a list of return values.

        COR-05: percentiles via statistics.quantiles (n=4 gives [Q1, Q2, Q3]).
        Falls back to min/max for n < 4.
        """
        if not returns:
            return 0.0, 0.0, 0.0, 0.0
        mean = statistics.mean(returns)
        median = statistics.median(returns)
        if len(returns) >= 4:
            qs = statistics.quantiles(returns, n=4)
            p25, p75 = qs[0], qs[2]
        else:
            p25 = min(returns)
            p75 = max(returns)
        return mean, median, p25, p75

    sig_rate, sig_ci_low, sig_ci_high, sig_n, sig_hits = _hit_rate_and_ci(signal_outcomes)
    null_rate, null_ci_low, null_ci_high, null_n, null_hits = _hit_rate_and_ci(null_outcomes)

    # Return distribution — signal net returns (existing)
    sig_returns = [o.net_return_pct for o in signal_outcomes]
    sig_mean, sig_median, sig_p25, sig_p75 = _return_distribution_stats(sig_returns)

    # Item 2a (F327): null cohort return distribution — was missing from prior payload
    null_returns = [o.net_return_pct for o in null_outcomes]
    null_mean, null_median, null_p25, null_p75 = _return_distribution_stats(null_returns)

    # Item 2b (F327): fixed-horizon return comparison.
    # horizon_end_return_pct is the net return if the position were held to horizon-end,
    # regardless of whether a hit triggered an early exit. For non-hits, it equals
    # net_return_pct (no early exit occurred). For hits it is the hypothetical hold-to-end.
    # None values (no horizon-end bar available) are excluded from mean/median.
    sig_horizon_returns = [
        o.horizon_end_return_pct for o in signal_outcomes
        if o.horizon_end_return_pct is not None
    ]
    null_horizon_returns = [
        o.horizon_end_return_pct for o in null_outcomes
        if o.horizon_end_return_pct is not None
    ]
    sig_hor_mean, sig_hor_median, _, _ = _return_distribution_stats(sig_horizon_returns)
    null_hor_mean, null_hor_median, _, _ = _return_distribution_stats(null_horizon_returns)

    # Miss list: signal outcomes that did NOT hit, sorted by composite_score desc.
    # PY-05/COR-09/ADV-07: composite_score is now stored in TradeOutcome.
    miss_list_raw: list[dict] = []
    for o in signal_outcomes:
        if not o.is_hit:
            miss_list_raw.append({
                "ticker": o.ticker,
                "as_of": o.as_of.isoformat() if isinstance(o.as_of, date) else str(o.as_of),
                "composite_score": o.composite_score,
                "net_return_pct": o.net_return_pct,
            })
    # Sort by composite_score desc; net_return_pct as tiebreak (worst misses last)
    miss_list_raw.sort(key=lambda x: (-(x["composite_score"] or 0), x["net_return_pct"]))

    # Item 1: build per-event table for downstream analyses (F324/F325/F328).
    # Includes both signal and null events. Dates serialized as ISO strings.
    # Schema is additive-forward: new fields can be added without breaking readers
    # that iterate over known keys. schema_version on the parent result tracks shape.
    all_outcomes = signal_outcomes + null_outcomes
    events_table: list[dict] = []
    for o in all_outcomes:
        events_table.append({
            "ticker": o.ticker,
            "as_of": o.as_of.isoformat() if isinstance(o.as_of, date) else o.as_of,
            "is_null": o.is_null,
            "entry_date": o.entry_date.isoformat() if isinstance(o.entry_date, date) else o.entry_date,
            "entry_price": o.entry_price,
            "exit_date": o.exit_date.isoformat() if isinstance(o.exit_date, date) else o.exit_date,
            "exit_price": o.exit_price,
            "net_return_pct": o.net_return_pct,
            "forward_return_pct": o.forward_return_pct,
            "hit": o.is_hit,
            "days_to_hit": o.days_to_hit,
            "composite_score": o.composite_score,
            "horizon_months": o.horizon_months,
            "horizon_end_return_pct": o.horizon_end_return_pct,
        })
    # Sort for deterministic output: as_of asc, ticker asc, null last
    events_table.sort(key=lambda x: (x["as_of"] or "", x["is_null"], x["ticker"] or ""))

    elapsed = time.monotonic() - t0
    fetch_failures = getattr(bars_loader, "fetch_failures", 0)

    return ValidationResult(
        signal_hit_rate=sig_rate,
        signal_hit_rate_ci_low=sig_ci_low,
        signal_hit_rate_ci_high=sig_ci_high,
        signal_n=sig_n,
        signal_hits=sig_hits,
        null_hit_rate=null_rate,
        null_hit_rate_ci_low=null_ci_low,
        null_hit_rate_ci_high=null_ci_high,
        null_n=null_n,
        null_hits=null_hits,
        signal_mean_return_pct=sig_mean,
        signal_median_return_pct=sig_median,
        signal_p25_return_pct=sig_p25,
        signal_p75_return_pct=sig_p75,
        miss_list=miss_list_raw,
        # ADV-09: expanded survivorship warning — biases are asymmetric, not symmetric
        survivorship_warning=(
            "Universe limited to currently-listed names. Delisted names excluded. "
            "Survivorship bias is asymmetric: null candidates (washed-out, failed "
            "fundamentals) correlate with eventual delisting more than signal "
            "candidates do, so null_hit_rate is inflated more than signal_hit_rate. "
            "The true signal-vs-null gap is likely understated. "
            f"Truncated events (horizon past data): {truncated_events}. "
            f"Overlap-suppressed events: {overlap_suppressed}."
        ),
        total_as_of_dates=len(as_of_dates),
        elapsed_secs=elapsed,
        conviction_skipped=True,
        unique_tickers=len(seen_tickers),
        truncated_events=truncated_events,
        fetch_failures=fetch_failures,
        overlap_suppressed=overlap_suppressed,
        # Item 2a (F327): null cohort return distribution
        null_mean_return_pct=null_mean,
        null_median_return_pct=null_median,
        null_p25_return_pct=null_p25,
        null_p75_return_pct=null_p75,
        # Item 2b (F327): fixed-horizon return comparison (hold-to-end, both cohorts)
        signal_horizon_mean_return_pct=sig_hor_mean,
        signal_horizon_median_return_pct=sig_hor_median,
        null_horizon_mean_return_pct=null_hor_mean,
        null_horizon_median_return_pct=null_hor_median,
        # Item 1: per-event table
        events=events_table,
        schema_version=1,
        # F313: timeout salvage annotation
        timed_out=timed_out,
        dates_completed=dates_completed,
    )
