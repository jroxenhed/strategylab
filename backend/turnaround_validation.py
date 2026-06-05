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
- D12 (Unit 1): Pluggable candidate source — run_validation accepts an optional
  candidate_source callable (CandidateSourceConfig) so non-legacy configs can emit
  candidates without going through run_filter. Default None → legacy run_filter path
  (config #0, regression anchor). A non-default config MUST declare expected_events_per_year
  (R1 enforcement); the harness refuses to run a non-default config missing this
  declaration. Errors surface via the same RuntimeError channel F313 built (caught
  by _run_validate_background → sets status="error", visible at GET /validate/status).
- D14 (Unit 2): Outcome engine v2 — bar-counted forward returns at 21/63/126
  TRADING DAYS (rows on the already-fetched frame; survivorship-safe, no
  calendar API), cohort-relative excess (event fwd return − same-cohort null
  median at the matched horizon; hit_v2 = excess > 0), and short support
  (direction + borrow_rate_annual on ValidationRequest; _apply_costs() inverts
  slippage sign for shorts and accrues borrow cost over the holding period,
  reusing the borrow_cost pattern from routes/backtest.py). Events table becomes
  schema_version=2 with ADDITIVE fwd_return_/excess_/hit_v2_ fields; the legacy
  calendar-month horizon_end_return_pct stays populated as a diagnostic so
  schema_version=1 consumers keep working. Null sampling is cohort-exhaustive
  (all same-as_of null events), beta control is market/cohort-excess only (NOT
  beta-adjusted — see the second-pass comment).
- D13 (Unit 3 / F332): Price-frame persistence — on-disk cache under
  backend/data/turnaround/price_cache/ keyed by ticker+span so reruns start at
  the date loop without re-fetching.  Format: pickle (protocol 4) — no parquet
  engine (pyarrow/fastparquet) available in the venv; see DEVIATION NOTE in
  PriceFrameCache.  Atomic writes: tmp file + os.replace (fileutil.py pattern).
  The existing in-process TTL-cache (_fetch in shared.py) is NOT bypassed —
  warm in-process hits still short-circuit the loader; D13 adds a durable layer
  beneath it that survives server restarts.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
import re
import statistics
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    # PY-01: CandidateResult lives in turnaround.py (lane B); imported only for type
    # hints so the source_fn signature is statically checkable without a runtime
    # import (preserving the lazy-import design that lets this module load first).
    from turnaround import CandidateResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# F332 / D13: Price-frame on-disk cache (Unit 3)
#
# DEVIATION NOTE — format choice:
#   pyarrow and fastparquet are NOT installed in backend/venv.  pandas.DataFrame
#   pickle (protocol 4, Python 3.8+ portable) is used instead.  Protocol 4 is
#   the highest version that predates Python 3.8's default bump to 5; it gives
#   reliable cross-version portability within the 3.8–3.12 range we target.
#
#   If a parquet engine is later added, replace the _read/_write helpers in
#   PriceFrameCache — the public API (load/store) is format-agnostic.
#
# Cache directory:
#   backend/data/turnaround/price_cache/  (gitignored via the parent rule)
#
# Key encoding:
#   ticker must be a safe filesystem name (A-Z0-9 only after upper-casing; any
#   other character is replaced with "_" for safety on all platforms).
#   span is "YYYY-MM-DD_YYYY-MM-DD".  Combined key: "{safe_ticker}_{span}.pkl"
#
# Atomic write: write to a .tmp sibling, then os.replace — same pattern as
#   fileutil.atomic_write_text but for binary data.
# ---------------------------------------------------------------------------

# Locate the cache directory relative to this file so it works from any cwd.
_THIS_DIR = Path(__file__).resolve().parent
_PRICE_CACHE_DIR: Path = _THIS_DIR / "data" / "turnaround" / "price_cache"
_PICKLE_PROTOCOL = 4  # Python 3.8+ portable; higher protocols not strictly needed

# DI-04: cache-format version. Lives in the on-disk path (price_cache/v1/...).
# Bump this constant to globally evict every cached frame in one move — e.g. when
# the provider schema changes, when the sanitization scheme changes (DI-01), or
# when a split-adjustment staleness sweep is needed.  See PriceFrameCache docstring
# for the split-adjustment staleness risk this guards against.
_PRICE_CACHE_VERSION = "v1"


def _safe_ticker(ticker: str) -> str:
    """Replace non-alphanumeric chars so the ticker is safe as a filename component.

    DI-01: the sanitized name alone is NOT collision-free (BRK.A, BRK_A and BRK/A all
    map to BRK_A).  Callers that need a collision-free key append a hash of the raw
    ticker via _ticker_key().
    """
    return re.sub(r"[^A-Za-z0-9]", "_", ticker).upper()


def _ticker_key(ticker: str) -> str:
    """Collision-free filename component for a ticker (DI-01).

    Combines the human-readable sanitized name with a CRC32 of the *raw* ticker so
    that distinct tickers differing only in punctuation (BRK.A vs BRK_A vs BRK/A) get
    distinct cache files.  The raw bytes drive the hash, so the inverse-collision
    risk is a CRC32 collision on differing inputs (negligible at universe scale).
    """
    crc = format(zlib.crc32(ticker.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return f"{_safe_ticker(ticker)}_{crc}"


def _safe_source(data_source: str) -> str:
    """Sanitize a data_source/provider id for use as a filename component (DI-02)."""
    return re.sub(r"[^A-Za-z0-9]", "_", data_source or "unknown").lower()


class PriceFrameCache:
    """On-disk pickle cache for validation price frames (F332 / D13).

    Each entry is keyed by (data_source, ticker, fetch_start, fetch_end) and stored
    as a single pickle file under *cache_dir*/<version>/.  Reads are best-effort
    (corrupt/missing files return None and the corrupt file is unlinked).  Writes are
    atomic (tmp + os.replace).

    Key design (DI-01/DI-02/DI-04):
    - The filename embeds a CRC32 of the raw ticker (_ticker_key) so punctuation-only
      variants (BRK.A / BRK_A / BRK/A) never alias to the same file.
    - The data_source/provider is part of the key (sanitized) so a frame fetched from
      provider A is never served on a run using provider B.  Providers differ in
      split-adjustment, survivorship, and gaps.
    - The cache-format version (_PRICE_CACHE_VERSION) is a path segment so a version
      bump globally evicts every stale frame in one move.

    STALENESS / SPLIT-ADJUSTMENT RISK (DI-04): there is NO automatic eviction.  Most
    historical bars are immutable, but split-adjusted providers RETROACTIVELY revise
    all historical prices after a post-hoc split.  A frame cached before such a split
    therefore carries stale (pre-adjustment) closes indefinitely — material for the
    high-split small/mid-cap names overrepresented in the turnaround universe.  The
    mitigation today is the manual version-bump lever above; a real time/mtime-based
    eviction policy is deferred to a follow-up F-item (F314/F320 family).

    Thread-safety: multiple threads may call load/store concurrently.
    - load() is read-only (plus a best-effort unlink of a proven-corrupt file) → safe.
    - store() uses os.replace which is atomic on POSIX; on Windows it is NOT
      atomic but is still correct (last writer wins; no partial reads possible
      because the tmp file is fsync'd before rename).
    """

    def __init__(self, cache_dir: Path = _PRICE_CACHE_DIR) -> None:
        self._dir = cache_dir

    def _ensure_dir(self) -> bool:
        """Create the versioned cache dir if needed. True on success, False on OSError."""
        try:
            self._version_dir().mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.warning("PriceFrameCache: cannot create cache dir %s: %s", self._version_dir(), exc)
            return False

    def _version_dir(self) -> Path:
        return self._dir / _PRICE_CACHE_VERSION

    def _path(
        self,
        ticker: str,
        fetch_start: str,
        fetch_end: str,
        data_source: str = "yahoo",
    ) -> Path:
        # DI-01: collision-free ticker key.  DI-02: provider in the key.
        # DI-04: version segment in the path so a bump evicts globally.
        key = _ticker_key(ticker)
        ds = _safe_source(data_source)
        span = f"{fetch_start}_{fetch_end}".replace("-", "")
        return self._version_dir() / f"{key}_{ds}_{span}.pkl"

    def load(
        self,
        ticker: str,
        fetch_start: str,
        fetch_end: str,
        data_source: str = "yahoo",
    ) -> Optional[pd.DataFrame]:
        """Return cached DataFrame for (provider, ticker, span), or None.

        DI-03: a proven-corrupt file (unpickle failure or wrong type) is unlinked
        before returning None, so the next run triggers a clean network fetch instead
        of re-encountering the same corrupt file on every restart.
        """
        p = self._path(ticker, fetch_start, fetch_end, data_source)
        if not p.exists():
            return None
        try:
            with open(p, "rb") as fh:
                obj = pickle.load(fh)
            if not isinstance(obj, pd.DataFrame):
                logger.warning("PriceFrameCache: unexpected type in %s — evicting", p)
                self._unlink_quiet(p)
                return None
            return obj
        except Exception as exc:
            logger.warning("PriceFrameCache: failed to load %s: %s — evicting", p, exc)
            self._unlink_quiet(p)
            return None

    @staticmethod
    def _unlink_quiet(p: Path) -> None:
        """Best-effort unlink of a corrupt cache file (DI-03)."""
        try:
            p.unlink()
        except OSError:
            pass

    def store(
        self,
        ticker: str,
        fetch_start: str,
        fetch_end: str,
        df: pd.DataFrame,
        data_source: str = "yahoo",
    ) -> None:
        """Persist *df* for (provider, ticker, span).  Best-effort — never raises."""
        if not self._ensure_dir():
            return
        p = self._path(ticker, fetch_start, fetch_end, data_source)
        # Atomic write: pickle to tmp sibling, then os.replace.
        try:
            dir_ = str(p.parent)
            fd = tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=dir_, suffix=".tmp"
            )
            try:
                pickle.dump(df, fd, protocol=_PICKLE_PROTOCOL)
                fd.flush()
                os.fsync(fd.fileno())
                try:
                    fd.close()
                except Exception:
                    pass
                os.replace(fd.name, str(p))
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
        except Exception as exc:
            logger.warning("PriceFrameCache: failed to store %s: %s", p, exc)


# Module-level default cache instance (can be replaced in tests via monkeypatch).
_price_frame_cache: PriceFrameCache = PriceFrameCache()


# ---------------------------------------------------------------------------
# Pluggable candidate source (Unit 1 / D12)
#
# A CandidateSourceConfig declares: name, direction (long/short), the
# pre-registered expected event rate (R1 enforcement), and the callable that
# produces candidates for a given (as_of, universe) pair.
#
# The callable signature is:
#   source_fn(as_of: date, universe: list, bars_loader: Callable) -> list[CandidateResult-like]
#
# The returned objects must be duck-compatible with CandidateResult (ticker,
# composite_score, is_null_candidate, has_insider_buying, has_buyback).
# ---------------------------------------------------------------------------

@dataclass
class CandidateSourceConfig:
    """Declares a pluggable candidate source for run_validation (Unit 1 / D12).

    Fields
    ------
    name : str
        Human-readable config identifier (used in events table and error messages).
    direction : str
        'long' or 'short'.  Flows through to TradeOutcome and ValidationResult
        for sign-correct cost handling in Unit 2.  Currently stored as metadata
        only; Unit 2 wires it into _apply_costs().
    expected_events_per_year : float
        Pre-registered expected event rate (R1 enforcement).  The harness refuses
        to run a non-default (non-None candidate_source) config that has this set
        to None or <= 0.  Forces explicit registration before any run.
    source_fn : Callable
        Called once per as-of date: source_fn(as_of, universe, bars_loader)
        -> list of CandidateResult-like objects.
    horizons : list[int], optional
        Horizon set in months.  Defaults to [req.horizon_months] if None.
        Unit 2 will extend this to [1, 3, 6] trading-day horizons.
    """
    name: str
    direction: str  # 'long' | 'short'
    expected_events_per_year: Optional[float]  # None only for legacy config #0
    # PY-01: full signature — (as_of, universe, bars_loader) -> list[CandidateResult].
    # universe is a list of (ticker, name) tuples; bars_loader maps ticker -> frame.
    source_fn: Callable[
        [date, list[tuple[str, str]], Callable[[str], Optional[pd.DataFrame]]],
        list[CandidateResult],
    ]
    horizons: Optional[list[int]] = None  # reserved for Unit 2

# ---------------------------------------------------------------------------
# Wall-clock budget (mirrors _WFA_TIMEOUT_SECS in routes/walk_forward.py).
# On timeout the completed-dates result IS written (salvage); on cancel
# nothing is written (user intent). Checked at every as-of date boundary and
# at every symbol iteration inside the current date.
# ---------------------------------------------------------------------------
_VALIDATION_TIMEOUT_SECS: float = 3 * 3600  # 3 hours — generous; real runs take ~60 min

# ---------------------------------------------------------------------------
# Unit 2 (D14): Outcome engine v2 — bar-counted forward-return horizons.
#
# Horizons are TRADING DAYS (rows on the already-fetched daily frame), NOT
# calendar months.  We count N rows forward from the entry row on the same
# frame the loader already produced — survivorship-safe and no external
# calendar API.  21/63/126 trading days ≈ 1/3/6 calendar months.
#
# The legacy calendar-month horizon (req.horizon_months / _horizon_end_date)
# is RETAINED as a diagnostic field (horizon_end_return_pct) so existing
# schema_version=1 consumers and the run-2 reproduction check keep working.
# ---------------------------------------------------------------------------
V2_HORIZONS_TRADING_DAYS: tuple[int, ...] = (21, 63, 126)


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
    # Unit 2 (D14): horizon_months is now a LEGACY diagnostic horizon only. The
    # v2 outcome metrics use bar-counted trading-day horizons (V2_HORIZONS_TRADING_DAYS)
    # measured on the already-fetched frame. The old ge=3/le=24 calendar-month
    # constraint is RELAXED to ge=1 so a config can run a short diagnostic window;
    # the upper bound is widened so the legacy 12m field still validates while the
    # real horizons are the bar-counted ones. Existing consumers pinning 12 are
    # unaffected (12 is still valid).
    horizon_months: int = Field(default=12, ge=1, le=36)
    hit_threshold_pct: float = Field(default=50.0, ge=10.0, le=500.0)
    initial_capital: float = Field(default=10_000.0, ge=1_000.0)

    # Cost model — duck-typed by per_leg_commission
    slippage_bps: float = Field(default=2.0, ge=0.0)
    per_share_rate: float = Field(default=0.0, ge=0.0)
    min_per_order: float = Field(default=0.0, ge=0.0)

    # Unit 2 (D14): direction + borrow cost for short support (P0 review item).
    # direction flows into _apply_costs() to invert slippage sign and accrue borrow
    # cost over the holding period (mirrors backtester borrow_cost()). When a
    # CandidateSourceConfig with direction='short' is injected, run_validation
    # overrides req.direction from the config so the cost engine is sign-correct.
    direction: str = Field(default="long")  # 'long' | 'short'
    borrow_rate_annual: float = Field(default=0.5, ge=0.0)

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
    # Unit 2 (D14): bar-counted forward returns at V2_HORIZONS_TRADING_DAYS.
    # GROSS direction-aware return at each horizon; None = incomplete (data ended
    # before N bars forward). Keyed by horizon (21/63/126). Cohort-relative excess
    # is computed in a second pass (needs the cohort null medians) and stored in
    # fwd_excess_pct.
    fwd_return_pct: dict[int, Optional[float]] = field(default_factory=dict)   # {21: x, 63: y, 126: z}
    fwd_excess_pct: dict[int, Optional[float]] = field(default_factory=dict)   # {21: ex, 63: ey, 126: ez}
    hit_v2: dict[int, Optional[bool]] = field(default_factory=dict)            # {21: bool, ...} excess>0
    # fix-nulls (U6): trailing-252d daily-return stdev at entry (charter §H2 vol
    # proxy). Lets downstream place this event into its cohort's null vol-tercile
    # for the H2 stratified recompute. None = < 252 trailing bars.
    trailing_vol_252d: Optional[float] = None


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
    # fix-nulls (U6): cohort-exhaustive null aggregates. Populated ONLY for
    # injected candidate_source cohorts that emit ZERO null candidates (the
    # legacy run_filter path emits null candidates, so this stays {} there).
    # Maps as_of ISO string -> {
    #   "n": int,                       # # of unselected universe names with sufficient data
    #   "insufficient": bool,           # True when n==0 (no usable null peer that cohort)
    #   "medians": {"21":..,"63":..,"126":..},   # whole-cohort null median fwd return per horizon
    #   "tercile_breaks": [b1, b2],     # trailing-252d daily-return-stdev tercile breakpoints
    #   "tercile_medians": [            # 3 buckets, low→high vol, each median fwd return per horizon
    #       {"n": int, "medians": {"21":..,"63":..,"126":..}}, ... (3 entries)
    #   ],
    # }
    # Charter §"Outcome spec" + §H2 "Exact stratification computation" (FROZEN sha
    # ffef4c05…189f): the v2 forward-return math (21/63/126td bar-counting) and the
    # volatility-tercile stratification are computed here identically to signal
    # events, so the H2 tercile-stratified recompute is reproducible downstream.
    # Schema stays schema_version=2 (this is an ADDITIVE top-level field; consumers
    # that don't know it simply ignore it).
    cohort_null_aggregates: dict = field(default_factory=dict)
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
    price_cache: Optional[PriceFrameCache] = None,
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

    D13 (F332): Price-frame persistence — the inner fetch first checks the
    on-disk PriceFrameCache (ticker+span key).  On a cache hit, the network is
    NOT touched and the in-process _cache is populated from disk.  On a miss,
    the normal _fetch call runs and, on success, the frame is persisted to disk
    before being returned.

    Layer order (cheapest first):
      1. In-process _cache dict (free; lives only for this run)
      2. On-disk PriceFrameCache (fast; survives server restarts / reruns)
      3. _fetch() network call (slow; only on cold miss)

    The existing TTL-cache inside shared._fetch() is NOT bypassed — a network
    call populates both the TTL-cache and the on-disk frame cache.  However,
    the on-disk cache is checked BEFORE _fetch, so a warm disk hit never even
    calls _fetch (and therefore never hits its TTL-cache lookup overhead).

    price_cache=None falls back to the module-level _price_frame_cache default
    (which is a PriceFrameCache pointing at the standard cache directory).
    Pass a custom PriceFrameCache to redirect writes in tests.
    """
    from shared import _fetch  # noqa: runtime import, safe inside thread

    fetch_start_year = start_year - low_lookback_years - 1
    fetch_end_year = end_year + max(1, (horizon_months + 11) // 12) + 1
    fetch_start = f"{fetch_start_year}-01-01"
    fetch_end = f"{fetch_end_year}-12-31"

    _disk_cache = price_cache if price_cache is not None else _price_frame_cache

    _cache: dict[str, Optional[pd.DataFrame]] = {}

    def _loader(ticker: str) -> Optional[pd.DataFrame]:
        # Layer 1: in-process memo
        if ticker in _cache:
            return _cache[ticker]

        # Layer 2: on-disk cache (F332 / D13).  DI-02: data_source is part of the key.
        disk_hit = _disk_cache.load(ticker, fetch_start, fetch_end, data_source)
        if disk_hit is not None:
            _cache[ticker] = disk_hit
            return _cache[ticker]

        # Layer 3: network fetch
        try:
            df = _fetch(ticker, fetch_start, fetch_end, "1d", data_source)
            result = df if df is not None and not df.empty else None
            _cache[ticker] = result
            # Persist to disk on successful fetch (non-None only)
            if result is not None:
                _disk_cache.store(ticker, fetch_start, fetch_end, result, data_source)
        except Exception as exc:
            logger.warning("bars_loader: failed to fetch %s: %s", ticker, exc)
            _loader.fetch_failures += 1  # type: ignore[attr-defined]
            # Retry once before caching None
            try:
                df2 = _fetch(ticker, fetch_start, fetch_end, "1d", data_source)
                result2 = df2 if df2 is not None and not df2.empty else None
                _cache[ticker] = result2
                if result2 is not None:
                    _disk_cache.store(ticker, fetch_start, fetch_end, result2, data_source)
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


def _frame_dates(df: pd.DataFrame) -> list[date]:
    """Return the frame's index as a list of date objects (normalises Timestamps)."""
    if hasattr(df.index, "date"):
        return [d.date() if hasattr(d, "date") else d for d in df.index]
    return list(df.index)


def _bar_counted_forward_returns(
    df: pd.DataFrame,
    entry_date: date,
    entry_close: float,
    horizons: tuple[int, ...] = V2_HORIZONS_TRADING_DAYS,
    direction: str = "long",
) -> dict[int, Optional[float]]:
    """Unit 2 (D14): bar-counted forward GROSS return at each trading-day horizon.

    Counts N rows forward from the entry row on the *already-fetched* frame
    (survivorship-safe; no calendar API).  For horizon N the exit bar is the
    entry-row index + N.  If that row is past the end of the frame, the horizon
    is INCOMPLETE → None (the long-horizon cell is marked incomplete, never
    extrapolated — D14 / Unit 2 edge case).

    Sign convention (matches CLAUDE.md Short Selling):
      long  : (exit - entry) / entry
      short : (entry - exit) / entry

    Returns {horizon_n: fwd_return_pct or None}.  Gross (pre-cost) — costs are
    applied separately so the diagnostic and v2 paths share one cost model.
    """
    out: dict[int, Optional[float]] = {h: None for h in horizons}
    if df is None or df.empty or entry_close <= 0:
        return out
    dates = _frame_dates(df)
    # Locate the entry row: first row whose date == entry_date (the entry bar the
    # caller already resolved via _first_trading_close_on_or_after).
    entry_idx: Optional[int] = None
    for i, d in enumerate(dates):
        if d == entry_date:
            entry_idx = i
            break
    if entry_idx is None:
        # Fall back to first row >= entry_date (defensive; should not happen).
        for i, d in enumerate(dates):
            if d >= entry_date:
                entry_idx = i
                break
    if entry_idx is None:
        return out
    if "Close" not in df.columns:
        raise KeyError("No 'Close' column in DataFrame during bar-counted forward return")
    n_rows = len(dates)
    for h in horizons:
        exit_idx = entry_idx + h
        if exit_idx >= n_rows:
            out[h] = None  # incomplete horizon — data ends before N bars forward
            continue
        exit_close = float(df.iloc[exit_idx]["Close"])
        if direction == "short":
            out[h] = (entry_close - exit_close) / entry_close * 100.0
        else:
            out[h] = (exit_close - entry_close) / entry_close * 100.0
    return out


# ---------------------------------------------------------------------------
# fix-nulls (U6): cohort-exhaustive null aggregates for injected sources.
#
# Charter §H2 "Exact stratification computation" (FROZEN, sha ffef4c05…189f):
#   volatility proxy = trailing-252-day realized volatility = stdev of the
#   trailing-252-day daily SIMPLE returns at the entry row.  The same already-
#   fetched bars feed both the v2 forward returns and this volatility, so the
#   stratified recompute is reproducible.  Sample stdev (ddof=1) over the 251
#   daily returns spanning the trailing 252 closes; < 252 trailing bars → None
#   (insufficient history, never imputed — matches Gate C / D14 edge handling).
# ---------------------------------------------------------------------------

def _trailing_vol_252(
    df: pd.DataFrame,
    entry_date: date,
    lookback: int = 252,
) -> Optional[float]:
    """Trailing-252d daily-return stdev at the entry row (first row == entry_date).

    Returns None when fewer than `lookback` trailing bars are available (the entry
    row inclusive), so the name lacks a full window and is excluded with a counted
    reason rather than imputed.
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    dates = _frame_dates(df)
    entry_idx: Optional[int] = None
    for i, d in enumerate(dates):
        if d == entry_date:
            entry_idx = i
            break
    if entry_idx is None:
        for i, d in enumerate(dates):
            if d >= entry_date:
                entry_idx = i
                break
    if entry_idx is None:
        return None
    # Need `lookback` closes ending at entry_idx (inclusive) → entry_idx >= lookback-1.
    if entry_idx < lookback - 1:
        return None
    window = df["Close"].iloc[entry_idx - (lookback - 1): entry_idx + 1]
    rets = window.pct_change().dropna()
    if len(rets) < 2:
        return None
    try:
        return float(rets.std(ddof=1))
    except Exception:
        return None


def _compute_cohort_null_aggregates(
    as_of: date,
    selected_tickers: set[str],
    universe: list,
    bars_loader: Callable[[str], Optional[pd.DataFrame]],
    req: ValidationRequest,
    *,
    horizons: tuple[int, ...] = V2_HORIZONS_TRADING_DAYS,
    n_terciles: int = 3,
) -> dict:
    """Cohort-exhaustive null aggregate for a single as_of (fix-nulls / U6).

    For every universe ticker NOT in `selected_tickers`, stream the frame via the
    (warm) cached loader, find the entry bar (first close >= as_of), compute v2
    bar-counted forward returns at `horizons` AND the trailing-252d daily-return
    stdev, then DISCARD the frame.  Names with no entry bar / insufficient history
    / no completed horizon are skipped (counted via the returned n).

    Returns the per-cohort aggregate dict (see ValidationResult.cohort_null_aggregates
    docstring).  When no usable null peer exists, returns an `insufficient=True`
    record with n=0 and empty medians, so signal excess for that cohort stays None
    (counted reason: no exhaustive null median available).

    Memory: per-ticker stream-and-discard — at most one frame is held at a time
    beyond what the loader caches.
    """
    # Per-name records: (vol_or_None, {horizon: fwd_or_None}).
    fwd_by_h: dict[int, list[float]] = {h: [] for h in horizons}
    # For terciles we need names that have BOTH a vol and a fwd value per horizon.
    name_records: list[tuple[float, dict[int, Optional[float]]]] = []
    n_usable = 0

    # UNIVERSE_V2 floor conformance (charter pre-registered universe-v2): exclude
    # sub-$5 / thin-volume / split-corrupt names from the exhaustive null cohort,
    # using the SAME point-in-time helper the configs use at candidate emission.
    # Signal and null MUST see the same universe or the per-cohort excess
    # (signal − null median) is biased. Lazy import: the helper is a leaf module
    # (no project imports), so importing it here cannot create a cycle.
    from research.universe_floors import floor_status as _floor_status, OK as _FLOOR_OK

    for entry in universe:
        ticker = entry[0] if isinstance(entry, (tuple, list)) else entry
        if ticker in selected_tickers:
            continue
        df = bars_loader(ticker)
        if df is None or df.empty:
            continue
        # Point-in-time floor enforcement (counted reason: below_floor / corrupt_frame
        # — names failing the floors are excluded from the null cohort entirely).
        if _floor_status(df, as_of) != _FLOOR_OK:
            continue
        entry_res = _first_trading_close_on_or_after(df, as_of)
        if entry_res is None:
            continue
        entry_date, entry_close = entry_res
        if entry_close <= 0:
            continue
        fwd = _bar_counted_forward_returns(
            df, entry_date, entry_close, horizons=horizons, direction=req.direction,
        )
        vol = _trailing_vol_252(df, entry_date)
        # A name is "usable" if it has at least one completed horizon. The trailing
        # vol may be None (short history) — such a name still contributes to the
        # whole-cohort medians but cannot be placed in a tercile.
        if all(fwd.get(h) is None for h in horizons):
            continue
        n_usable += 1
        for h in horizons:
            v = fwd.get(h)
            if v is not None:
                fwd_by_h[h].append(v)
        if vol is not None:
            name_records.append((vol, fwd))

    if n_usable == 0:
        return {
            "n": 0,
            "insufficient": True,
            "medians": {},
            "tercile_breaks": [],
            "tercile_medians": [],
        }

    medians = {
        str(h): (statistics.median(fwd_by_h[h]) if fwd_by_h[h] else None)
        for h in horizons
    }

    # Tercile stratification by trailing-252d vol (charter §H2). Only names with a
    # defined vol participate. Breakpoints via statistics.quantiles (n=3 → the two
    # interior tercile cut points), then bucket each name by its vol.
    tercile_breaks: list[float] = []
    tercile_medians: list[dict] = []
    vols_only = sorted(r[0] for r in name_records)
    if len(name_records) >= n_terciles:
        qs = statistics.quantiles(vols_only, n=n_terciles)  # n_terciles-1 cut points
        tercile_breaks = [float(q) for q in qs]

        def _bucket(vol: float) -> int:
            b = 0
            for cut in tercile_breaks:
                if vol > cut:
                    b += 1
                else:
                    break
            return min(b, n_terciles - 1)

        buckets: list[list[tuple[float, dict[int, Optional[float]]]]] = [
            [] for _ in range(n_terciles)
        ]
        for vol, fwd in name_records:
            buckets[_bucket(vol)].append((vol, fwd))

        for bucket in buckets:
            b_medians: dict[str, Optional[float]] = {}
            for h in horizons:
                vals = [fwd.get(h) for _, fwd in bucket if fwd.get(h) is not None]
                b_medians[str(h)] = statistics.median(vals) if vals else None
            tercile_medians.append({"n": len(bucket), "medians": b_medians})

    return {
        "n": n_usable,
        "insufficient": False,
        "medians": medians,
        "tercile_breaks": tercile_breaks,
        "tercile_medians": tercile_medians,
    }


# ---------------------------------------------------------------------------
# Cost application helpers
# ---------------------------------------------------------------------------

def _borrow_cost_pct(
    entry_close: float,
    hold_days: float,
    req: ValidationRequest,
) -> float:
    """Unit 2 (D14): short borrow cost as a percent of entry notional.

    Mirrors backend/routes/backtest.py borrow_cost(): the dollar cost is
    shares * entry_price * (rate/100/365) * hold_days.  As a fraction of the
    position notional (shares * entry_price) it collapses to
    (rate/100/365) * hold_days, so it is share-count-independent.  Returned as a
    PERCENT (×100) so it subtracts directly from net_return_pct.  Zero for longs
    or rate=0 (identical guard to the backtester).
    """
    if req.direction != "short" or req.borrow_rate_annual <= 0 or entry_close <= 0:
        return 0.0
    if hold_days <= 0:
        return 0.0
    # (rate/100/365) is the daily borrow fraction; ×hold_days ×100 → percent.
    return (req.borrow_rate_annual / 100.0 / 365.0) * hold_days * 100.0


def _apply_costs(
    gross_entry: float,
    gross_exit: float,
    req: ValidationRequest,
    *,
    hold_days: float = 0.0,
) -> tuple[float, float, float, float]:
    """Return (net_entry, net_exit, commission_total, net_return_pct).

    Unit 2 (D14): direction-aware.  Slippage sign is inverted for shorts and a
    borrow cost accrues over the holding period (reusing the backtester's
    borrow_cost pattern).

    Long (direction='long'):
      - entry fill: close * (1 + bps/1e4)  — worse (slippage hurts on buy)
      - exit fill:  close * (1 - bps/1e4)  — worse (slippage hurts on sell)
      - net_return = (net_exit - net_entry)/net_entry  (price up = profit)

    Short (direction='short'):
      - entry fill: close * (1 - bps/1e4)  — sell to open, slippage hurts (lower)
      - exit fill:  close * (1 + bps/1e4)  — buy to cover, slippage hurts (higher)
      - net_return = (net_entry - net_exit)/net_entry  (price down = profit)
      - borrow cost accrues over hold_days and reduces the short return.

    commission and (for shorts) borrow cost are netted as percent-of-notional.
    """
    per_leg_commission = _import_per_leg_commission()

    bps = req.slippage_bps
    is_short = req.direction == "short"

    if is_short:
        # Sell-to-open then buy-to-cover: slippage is unfavorable on both legs,
        # which for a short means entry fills LOWER and exit (cover) fills HIGHER.
        net_entry = gross_entry * (1 - bps / 1e4)
        net_exit = gross_exit * (1 + bps / 1e4)
    else:
        net_entry = gross_entry * (1 + bps / 1e4)
        net_exit = gross_exit * (1 - bps / 1e4)

    shares = req.initial_capital / net_entry if net_entry > 0 else 0.0
    comm_in = per_leg_commission(shares, req)
    comm_out = per_leg_commission(shares, req)

    position_value = shares * net_entry
    if position_value > 0:
        comm_pct = (comm_in + comm_out) / position_value * 100
        if is_short:
            gross_pct = (net_entry - net_exit) / net_entry * 100
            borrow_pct = _borrow_cost_pct(net_entry, hold_days, req)
            net_return_pct = gross_pct - comm_pct - borrow_pct
        else:
            gross_pct = (net_exit - net_entry) / net_entry * 100
            net_return_pct = gross_pct - comm_pct
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
    candidate_source: Optional[CandidateSourceConfig] = None,
) -> ValidationResult:
    """Run historical as-of validation.

    Default (candidate_source=None): calls run_filter() at each quarterly date
    in [start_year, end_year] — the legacy config #0 regression anchor.

    Pluggable source (Unit 1 / D12): when candidate_source is provided, the harness
    calls source.source_fn(as_of, universe, bars_loader) instead of run_filter().
    The non-default config MUST declare expected_events_per_year > 0 (R1 enforcement);
    the harness refuses to run a config missing this declaration and raises RuntimeError
    so the caller's error-channel (F313 / GET /validate/status) surfaces the refusal.
    No partial artifacts are written on refusal.

    Uses a memoized bars_loader (D2) so each symbol's price history is fetched
    at most once across all as-of dates.

    Conviction is skipped for all validation runs (D3): both flags are set False
    after the candidate source returns, composite score is NOT recomputed (conviction
    flags are additive-only, never gatekeepers — omitting the bonus is conservative).

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
    # D12 (Unit 1): R1 enforcement — non-default configs must declare event rate pre-run.
    # Surfaces through the existing F313 error channel (RuntimeError → status="error" at
    # GET /validate/status), no partial artifacts written.
    if candidate_source is not None:
        if (
            candidate_source.expected_events_per_year is None
            or candidate_source.expected_events_per_year <= 0
        ):
            raise RuntimeError(
                f"Config '{candidate_source.name}' missing required event-rate declaration "
                f"(expected_events_per_year must be > 0). "
                f"R1: every non-default config must pre-register an expected event rate "
                f"before running. Set CandidateSourceConfig.expected_events_per_year."
            )
        # Unit 2 (D14): the config's declared direction governs the cost engine.
        # Override req.direction so _apply_costs() is sign-correct for shorts even
        # when the caller left req at its 'long' default.
        if candidate_source.direction in ("long", "short"):
            req = req.model_copy(update={"direction": candidate_source.direction})
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
    _inner_loader = _make_memoized_loader(
        start_year=req.start_year,
        end_year=req.end_year,
        low_lookback_years=params.low_lookback_years,
        horizon_months=req.horizon_months,
        data_source=params.data_source,
    )

    # F313: instrument the loader itself. The ~60-min date-1 price wall happens
    # INSIDE run_filter (washed-out gate fetches bars per symbol), so both the
    # within-date progress counter and cancellation responsiveness must live at
    # the loader layer — the candidate loop only runs AFTER the wall. On cancel,
    # return None (loader contract: no bars) so run_filter skips remaining
    # symbols in seconds without exception spam; the date-boundary check then
    # raises _cancelled_ cleanly.
    def bars_loader(ticker: str) -> Optional[pd.DataFrame]:
        if cancel_event is not None and cancel_event.is_set():
            return None
        if progress is not None:
            progress.symbols_loaded += 1
        return _inner_loader(ticker)


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
    # fix-nulls (U6): per-cohort exhaustive null aggregates, populated only for
    # injected-source cohorts that emit ZERO null candidates (see second pass).
    # Keyed by as_of ISO string. Committed on date completion (buffer semantics).
    cohort_null_aggregates: dict = {}
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
            if candidate_source is not None:
                # D12 (Unit 1): pluggable path — delegate to the injected source callable.
                # source_fn(as_of, universe, bars_loader) -> list[CandidateResult-like]
                candidates = candidate_source.source_fn(as_of, universe, bars_loader)
            else:
                # Legacy config #0: pass bars_loader into run_filter (D2) — avoids re-fetching per as_of
                candidates = turnaround.run_filter(
                    universe=universe,
                    as_of=as_of,
                    params=params,
                    bars_loader=bars_loader,
                )
        except Exception as exc:
            logger.warning("run_validation: candidate source failed at %s: %s", as_of, exc)
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
        date_cohort_null_aggregate: Optional[dict] = None

        # fix-nulls (U6): when an injected candidate_source is active and this
        # cohort emits ZERO null candidates but ≥1 signal candidate, the legacy
        # events-based null path produces no matched null. Compute cohort-EXHAUSTIVE
        # null aggregates directly over the universe names NOT selected this as_of
        # (per-ticker stream-and-discard via the warm cached loader). Charter
        # §"Outcome spec" / §H2 (FROZEN). Buffered; committed on date completion.
        if (
            candidate_source is not None
            and signal_candidates
            and not null_candidates
        ):
            selected_tickers = {c.ticker for c in signal_candidates}
            try:
                date_cohort_null_aggregate = _compute_cohort_null_aggregates(
                    as_of, selected_tickers, universe, bars_loader, req,
                )
            except Exception as exc:
                logger.warning(
                    "run_validation: cohort null aggregate failed at %s: %s",
                    as_of, exc,
                )
                date_cohort_null_aggregate = None

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

                # Take-profit scan trigger (gross close).  COR-01: the early-exit
                # trigger is DIRECTION-AWARE.  A long profits when price RISES to
                # entry*(1+threshold); a short profits when price FALLS to
                # entry*(1-threshold).  Using the long-direction trigger for shorts
                # inverts the scan (it would fire on a deeply LOSING short and never
                # on a winner).  The final is_hit uses the net return (direction-aware
                # via _apply_costs); this scan only governs the early-exit touch.
                if req.direction == "short":
                    target_price = entry_close * (1 - hit_threshold)  # profit if price falls
                else:
                    target_price = entry_close * (1 + hit_threshold)  # profit if price rises

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
                    if req.direction == "short":
                        hit_condition = close_val <= target_price
                    else:
                        hit_condition = close_val >= target_price
                    if hit_condition:
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
                        _hold_to_horizon = (horizon_end - entry_date).days
                        _, _, _, horizon_end_return_pct = _apply_costs(
                            entry_close, horizon_end_close, req, hold_days=_hold_to_horizon
                        )
                else:
                    # No early exit: horizon-end close == exit close
                    horizon_end_close = exit_close

                # Gross return — direction-aware (Unit 2 / D14). For shorts the
                # diagnostic gross return is (entry - exit)/entry.
                if req.direction == "short":
                    gross_return_pct = (entry_close - exit_close) / entry_close * 100
                else:
                    gross_return_pct = (exit_close - entry_close) / entry_close * 100

                # Holding period in calendar days (for borrow accrual on shorts).
                exit_hold_days = (exit_date - entry_date).days if exit_date is not None else 0

                # Apply costs (D6 + Unit 2 D14: direction-aware + borrow) — net return
                _, _, _, net_return_pct = _apply_costs(
                    entry_close, exit_close, req, hold_days=exit_hold_days
                )

                if not hit_early:
                    horizon_end_return_pct = net_return_pct

                # Unit 2 (D14): bar-counted forward GROSS returns at 21/63/126
                # trading days on the already-fetched frame. None = incomplete
                # horizon (data ended before N bars forward).
                fwd_returns = _bar_counted_forward_returns(
                    df, entry_date, entry_close,
                    direction=req.direction,
                )

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
                    fwd_return_pct=fwd_returns,
                    # fix-nulls (U6): trailing-252d vol (charter §H2 proxy) on the
                    # same frame/entry the forward returns use.
                    trailing_vol_252d=_trailing_vol_252(df, entry_date),
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
        # fix-nulls (U6): commit the cohort exhaustive null aggregate (if computed).
        if date_cohort_null_aggregate is not None:
            cohort_null_aggregates[as_of.isoformat()] = date_cohort_null_aggregate
        dates_completed += 1
        if progress is not None:
            progress.dates_done = dates_completed
            # snap running counts back to committed totals (drops nothing here;
            # buffers were just committed)
            progress.signal_events = len(signal_outcomes)
            progress.null_events = len(null_outcomes)

    # ---------- Unit 2 (D14): cohort-relative excess (second pass) ----------
    #
    # Sampling design decision (locked here per the plan's "matched-null sampling
    # design" deferral): COHORT-EXHAUSTIVE, not sampled.  For each event the
    # matched null is *every* null-cohort event sharing the same as_of date — the
    # harness already emits a full null cohort per quarterly as_of (tens to low
    # hundreds of names at universe scale).  Cohort sizes are small enough that
    # using the entire null set is cheap and removes sampling variance, so a
    # with-replacement draw buys nothing.  excess_Nd = event_fwd_Nd - null_median_Nd
    # at the matched horizon; hit_v2 = excess > 0.
    #
    # BETA CONTROL (locked per Deferred-to-Implementation): the null is matched on
    # cohort (as_of) + universe ONLY — it neutralises time/regime/tape but NOT the
    # cross-sectional beta/size tilt of a directional selection.  This excess is
    # therefore MARKET/COHORT-EXCESS, explicitly NOT beta-adjusted.  Per the plan's
    # risk table, momentum CONFIRMED may not be claimed on this excess alone; a
    # beta/size null-stratification key (added to the cohort key) is the documented
    # upgrade and is left as an additive extension of this same second pass (the
    # cohort key below is the single insertion point).  Recording the choice in code
    # so the limitation travels with the metric.
    _null_by_cohort: dict[date, list[TradeOutcome]] = {}
    for o in null_outcomes:
        _null_by_cohort.setdefault(o.as_of, []).append(o)

    def _cohort_null_median(as_of_key: date, horizon: int) -> Optional[float]:
        vals = [
            o.fwd_return_pct.get(horizon)
            for o in _null_by_cohort.get(as_of_key, [])
            if o.fwd_return_pct.get(horizon) is not None
        ]
        if vals:
            return statistics.median(vals)
        # fix-nulls (U6): no events-based null cohort (injected source emitted zero
        # null candidates) — fall back to the cohort-EXHAUSTIVE null median computed
        # over the universe names NOT selected this as_of. None when that cohort was
        # flagged insufficient (no usable null peer) so excess stays None.
        agg = cohort_null_aggregates.get(as_of_key.isoformat())
        if agg and not agg.get("insufficient"):
            return agg.get("medians", {}).get(str(horizon))
        return None

    # Precompute per-cohort null medians once per (cohort, horizon).
    #
    # COR-02: hit_v2 / fwd_excess_pct are computed on GROSS bar-counted forward
    # returns (fwd_return_pct), and the cohort null median is likewise GROSS.  So
    # hit_v2 is a GROSS cohort-relative excess flag — it is NOT the same economic
    # quantity as is_hit (which is NET: post slippage + commission + borrow).  For
    # long runs with tight slippage the gap is small; for short runs with borrow,
    # hit_v2_rate can sit systematically above the net signal_hit_rate.  Downstream
    # callers MUST NOT compare hit_v2_rate directly against the net is_hit / signal
    # hit rate — hit_v2 is a relative (gross, cohort-excess) ranking metric only.
    #
    # COR-04: excess/hit_v2 is computed for BOTH signal and null outcomes below.
    # For NULL events the excess is self-referential — each null event is compared
    # against the median of its own cohort's null events — so a null cohort's
    # hit_v2_rate converges to ~50% by construction (half above the median, half
    # below) regardless of return level.  That is a pure self-comparison artifact:
    # null-event hit_v2 must NOT be read as "null effectiveness".  Only the SIGNAL
    # cohort's hit_v2 (signal vs null median) is a meaningful excess.  The fields are
    # still populated for null events for additive schema consistency (downstream
    # filters on is_null to exclude them).
    _null_median_cache: dict[tuple[date, int], Optional[float]] = {}
    for o in signal_outcomes + null_outcomes:
        excess: dict[int, Optional[float]] = {}
        hit_v2: dict[int, Optional[bool]] = {}
        for h in V2_HORIZONS_TRADING_DAYS:
            ev = o.fwd_return_pct.get(h)
            key = (o.as_of, h)
            if key not in _null_median_cache:
                _null_median_cache[key] = _cohort_null_median(o.as_of, h)
            nm = _null_median_cache[key]
            if ev is None or nm is None:
                excess[h] = None
                hit_v2[h] = None
            else:
                excess[h] = ev - nm
                hit_v2[h] = excess[h] > 0
        o.fwd_excess_pct = excess
        o.hit_v2 = hit_v2

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
    # Unit 1 (D12): additive fields config_name and direction tag every event with
    # its source config for downstream join.  Legacy path uses "legacy" / "long".
    _config_name = candidate_source.name if candidate_source is not None else "legacy"
    _direction = candidate_source.direction if candidate_source is not None else "long"
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
            # D12 (Unit 1): config provenance tags — additive, consumers check key presence
            "config_name": _config_name,
            "direction": _direction,
            # Unit 2 (D14): schema_version=2 ADDITIVE fields — bar-counted forward
            # returns + cohort-relative excess + hit_v2 at each trading-day horizon.
            # None = incomplete horizon (data ended before N bars) or no cohort null.
            # Existing schema_version=1 consumers ignore these unknown keys.
            "fwd_return_21d": o.fwd_return_pct.get(21),
            "fwd_return_63d": o.fwd_return_pct.get(63),
            "fwd_return_126d": o.fwd_return_pct.get(126),
            "excess_21d": o.fwd_excess_pct.get(21),
            "excess_63d": o.fwd_excess_pct.get(63),
            "excess_126d": o.fwd_excess_pct.get(126),
            "hit_v2_21d": o.hit_v2.get(21),
            "hit_v2_63d": o.hit_v2.get(63),
            "hit_v2_126d": o.hit_v2.get(126),
            # fix-nulls (U6): trailing-252d vol for H2 tercile placement downstream.
            "trailing_vol_252d": o.trailing_vol_252d,
        })
    # Sort for deterministic output: as_of asc, ticker asc, null last
    events_table.sort(key=lambda x: (x["as_of"] or "", x["is_null"], x["ticker"] or ""))

    elapsed = time.monotonic() - t0
    # F313: failures accumulate on the inner memoized loader (the wrapper only
    # instruments progress/cancel)
    fetch_failures = getattr(_inner_loader, "fetch_failures", 0)

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
        # fix-nulls (U6): cohort-exhaustive null aggregates (injected-source cohorts
        # with zero null candidates). Empty {} on the legacy path. Additive top-level.
        cohort_null_aggregates=cohort_null_aggregates,
        # Unit 2 (D14): schema_version=2 — events carry additive bar-counted forward
        # returns + cohort-relative excess + hit_v2. v1 fields all still populated.
        schema_version=2,
        # F313: timeout salvage annotation
        timed_out=timed_out,
        dates_completed=dates_completed,
    )
