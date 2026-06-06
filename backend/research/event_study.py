"""F342 — Event Clock Harness (Radar Engine).

Generic event-time study harness: given an iterable of (ticker, event_ts, payload)
records, resolves entry dates, computes Open-anchored forward returns, measures
excess vs universe median, and runs statistical tests (moving-block bootstrap
primary, NW HAC cross-check, BH-FDR ledger).

Key design decisions:
- Fork A = Option 1 (orchestrator): enter at next trading day's OPEN.
  `_bar_counted_forward_returns_from_open()` is imported from turnaround_validation
  (additive extension; existing Close-based quarterly path is untouched).
- Fork B = Option 1 (orchestrator): universe median = all floor-passing symbols
  alive on the same entry_date, event pick EXCLUDED.
- Point-in-time discipline: floor/universe inclusion is decided from the last
  close ON OR BEFORE the event date (information available BEFORE entry, ADV-01),
  never from the entry bar's own price.
- Survivorship (ADV-02/03): missing-from-cache events are COUNTED, never dropped;
  at long horizons a pick or peer whose price series ENDS inside the horizon
  contributes its terminal (last available) close as the exit price — delisting
  is treated symmetrically on both sides of the excess, so long-horizon medians
  are not inflated by quietly dropping the worst (delisted) names.
- De-clustering (ADV-06/08): same-ticker events within `dedup_window_days`
  collapse to one event (default ON); the raw pre-dedup count is reported too.
- Split: entry_date <= 2020-12-31 → "explore"; later → "confirm".
- Era discipline (John, 2026-06-06): explore must NEVER reach 2025+ cache data
  (reserved as a future fresh confirm window); hard-guarded by explore_cutoff <=
  2020-12-31 unless allow_post_2020_explore is set.  An explore sub-era
  consistency report and a confirm-era breakdown are emitted in meta.
- MDE self-report is mandatory (Research Axiom); reported on BOTH excess (vs
  peers) and raw absolute returns (ADV-07).
- acceptanceDateTime → ET → next-trading-open; filingDate+16:01 fallback.

No FastAPI dependency (consistent with backend/research/ README rule).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional
# F351: concrete type alias for the per-(entry_date, horizon) return vector.
# Maps symbol → (fwd_return_pct, is_terminal).  Symbols absent from the dict
# either failed a gate or had no entry bar.
_ReturnVector = dict[str, tuple[float, bool]]
# Cache mapping (entry_date, horizon) → _ReturnVector.
_VectorCache = dict[tuple, _ReturnVector]

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — allow running from any cwd
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ---------------------------------------------------------------------------
# Imports from existing modules (additive only — no modifications)
# ---------------------------------------------------------------------------
from turnaround_validation import (  # noqa: E402
    PriceFrameCache,
    _bar_counted_forward_returns_from_open,
    _first_trading_close_on_or_after,
    _frame_dates,
    _prefetch_price_frames,
    V2_HORIZONS_TRADING_DAYS,
)
from research.universe_floors import (  # noqa: E402
    floor_status as _floor_status,
    OK as _FLOOR_OK,
    BELOW_FLOOR as _FLOOR_BELOW,
    CORRUPT_FRAME as _FLOOR_CORRUPT,
)
from research.power_audit import (  # noqa: E402
    _nw_ttest_pvalue,
    _compute_nw_lag,
)
from research.outcome_table import minimum_detectable_effect  # noqa: E402
from research.regime_validation import regime_state_for_as_of as _regime_state_for_as_of  # noqa: E402  # F350

# DI-06: prefer house-standard atomic writer (fsync before replace) for durability.
# Fall back to local _atomic_write if fileutil is unavailable (e.g. older installs).
try:
    _BACKEND_DIR_FOR_FU = Path(__file__).resolve().parent.parent
    if str(_BACKEND_DIR_FOR_FU) not in sys.path:
        sys.path.insert(0, str(_BACKEND_DIR_FOR_FU))
    from fileutil import atomic_write_text as _fileutil_atomic_write_text  # type: ignore[import]
    _HAS_FILEUTIL = True
except ImportError:
    _HAS_FILEUTIL = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_STUDIES_DIR = _BASE_DIR / "backend" / "data" / "turnaround" / "event_studies"
_SUBMISSIONS_DIR = _BASE_DIR / "backend" / "data" / "turnaround" / "edgar_cache" / "submissions"
_EDGAR_CACHE_DIR = _BASE_DIR / "backend" / "data" / "turnaround" / "edgar_cache"
_REGIME_STATES_PATH = _BASE_DIR / "backend" / "data" / "turnaround" / "regime_states.json"
# ADV-05: append-only cross-run FDR ledger — survives across runs, records the
# full multiplicity context (n_boot, block sizes, q, horizons) per study run so
# optional-stopping / parameter-shopping across reruns is auditable after the fact.
_FDR_LEDGER_PATH = _BASE_DIR / "backend" / "data" / "turnaround" / "fdr_ledger.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EXPLORE_CUTOFF = date(2020, 12, 31)
# Era-consistency (John, 2026-06-06): explore must NEVER reach into 2025+ cache
# data, which is reserved as a future fresh confirm window.  Hard guard unless a
# caller explicitly overrides via allow_post_2020_explore.
_EXPLORE_HARD_CEILING = date(2020, 12, 31)
# Default explore sub-eras (configurable) — used for the era-consistency report.
_DEFAULT_EXPLORE_ERAS: tuple[tuple[str, date, date], ...] = (
    ("2015-16", date(2015, 1, 1), date(2016, 12, 31)),
    ("2017-18", date(2017, 1, 1), date(2018, 12, 31)),
    ("2019-20", date(2019, 1, 1), date(2020, 12, 31)),
)
# Default confirm sub-eras for the single confirm evaluation breakdown.
_DEFAULT_CONFIRM_ERAS: tuple[tuple[str, date, date], ...] = (
    ("2021", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023-24", date(2023, 1, 1), date(2024, 12, 31)),
)
_DEFAULT_HORIZONS: tuple[int, ...] = V2_HORIZONS_TRADING_DAYS  # (21, 63, 126)
_SCHEMA_VERSION = 2
_ET_ZONE_NAME = "America/New_York"
# ADV-02: survivorship warning fires when no-price-data drops exceed this fraction.
_NO_PRICE_WARN_FRACTION = 0.10


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

# EventRecord: the wire type callers pass in.
# Using a simple NamedTuple-compatible tuple so the harness is agnostic to
# how the event stream is constructed.

@dataclass
class EventRecord:
    ticker: str
    event_ts: datetime  # timezone-aware UTC datetime
    payload: dict
    # COR-03 / PY-01: True when event_ts was derived from the filingDate+16:01
    # fallback (no acceptanceDateTime).  Threaded through so run_event_study can
    # produce a real acceptance_dt_fallbacks count in meta.json.
    is_fallback: bool = False


@dataclass
class EventOutcome:
    ticker: str
    event_ts: datetime               # original acceptanceDateTime (UTC)
    entry_date: date                 # first tradeable open after event became public
    entry_price: float               # Open price at entry_date (fallback: Close)
    payload: dict                    # caller payload forwarded verbatim
    split: str                       # "explore" | "confirm" | "out_of_range"
    fwd_return_pct: dict             # {21: x, 63: y, 126: z} — float or None
    fwd_excess_pct: dict             # excess vs same-date universe median
    floor_status: str                # "ok" | "below_floor" | "corrupt_frame"
    universe_n: dict                 # count of non-pick universe members per horizon
    # ADV-02: distinguishes "no price data in cache" (loader miss) from a real
    # below-floor exclusion, so survivorship of the event stream is counted.
    no_price_data: bool = False
    is_fallback: bool = False        # COR-03: forwarded from EventRecord
    # F349: sector-peer benchmark fields (None when universe_tickers not supplied
    # or SIC not found for event ticker).
    peer_sic: Optional[str] = None
    peer_sic_fallback_level: Optional[str] = None  # "3_digit" | "2_digit" | "universe" | None
    fwd_peer_excess_pct: dict = field(default_factory=dict)  # {21: float|None, ...}
    peer_n: dict = field(default_factory=dict)               # {21: int, ...}
    # F350: regime state at entry_date (RISK_ON|NEUTRAL|RISK_OFF|STRESS|None).
    # COR-03: None has two distinct meanings:
    #   (1) entry_date is outside regime_states.json date range (or file missing)
    #   (2) event was out_of_range / had no price data (early-continue path)
    # Future analysis that distinguishes these cases should add a sentinel value
    # rather than relying on None alone.
    regime_state: Optional[str] = None


@dataclass
class EventStudyConfig:
    study_name: str
    horizons: tuple[int, ...] = field(default_factory=lambda: _DEFAULT_HORIZONS)
    explore_cutoff: date = _EXPLORE_CUTOFF
    entry_lag_days: int = 1          # days to advance after event_ts date (1 = next bday)
    use_non_overlapping: bool = False
    cost_fn: Optional[Callable[["EventRecord", float], float]] = None
    n_boot: int = 999
    fdr_q: float = 0.10
    output_dir: Optional[Path] = None  # default: _STUDIES_DIR / study_name
    # ADV-06 / ADV-08: same-ticker event de-clustering.  When on, same-ticker
    # events whose entry_dates fall within `dedup_window_days` collapse to the
    # FIRST event (chronologically), so an insider filing a cluster of Form 4s in
    # one week contributes one degree of freedom, not N.  Default ON for the
    # primary analysis; the raw (pre-dedup) count is always reported alongside.
    dedup_same_ticker: bool = True
    dedup_window_days: int = 7
    # ADV-06: optional explicit block-size override (pre-registered).  When set,
    # used verbatim instead of the density-derived value and flagged in meta.
    block_size_override: Optional[int] = None
    # Era-consistency (John, 2026-06-06): sub-era boundaries for the explore
    # consistency report and the confirm-era breakdown.  (label, start, end).
    explore_eras: tuple[tuple[str, date, date], ...] = field(
        default_factory=lambda: _DEFAULT_EXPLORE_ERAS
    )
    confirm_eras: tuple[tuple[str, date, date], ...] = field(
        default_factory=lambda: _DEFAULT_CONFIRM_ERAS
    )
    # Hard guard: explore must not reach 2025+ (reserved fresh confirm window).
    # Set True only to deliberately override the 2020-12-31 ceiling.
    allow_post_2020_explore: bool = False
    # ADV-05: cross-run FDR ledger path.  None → the shared real ledger
    # (_FDR_LEDGER_PATH).  Tests pass a tmp path so they never pollute the real
    # ledger.  This stays a single append-only file per chosen path.
    fdr_ledger_path: Optional[Path] = None
    # F349: sector-peer benchmark configuration.
    min_peer_count: int = 5            # fallback cascade minimum (3-digit → 2-digit → universe)
    sic_coverage_path: Optional[Path] = None   # override submissions cache location
    # F350: regime-breakdown lens configuration.
    regime_states_path: Optional[Path] = None  # override regime_states.json location


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _NY_TZ = ZoneInfo(_ET_ZONE_NAME)
except ImportError:
    # Python < 3.9 fallback via pytz (available in the venv)
    try:
        import pytz
        _NY_TZ = pytz.timezone(_ET_ZONE_NAME)
    except ImportError:
        _NY_TZ = None  # type: ignore[assignment]


def _to_et(dt: datetime) -> datetime:
    """Convert a timezone-aware UTC datetime to US/Eastern wall-clock time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if _NY_TZ is None:
        # Last resort: UTC−5 (EST, ignores DST — conservative)
        return dt.astimezone(timezone(timedelta(hours=-5)))
    return dt.astimezone(_NY_TZ)


# ---------------------------------------------------------------------------
# acceptanceDateTime parsing
# ---------------------------------------------------------------------------

def _parse_acceptance_dt(s: str) -> Optional[datetime]:
    """Parse EDGAR acceptanceDateTime ISO-8601 string to UTC-aware datetime.

    EDGAR format: '2026-05-28T20:13:31.000Z'
    Returns None on any parse failure.
    """
    if not s:
        return None
    try:
        # Strip trailing Z and parse as UTC
        s_clean = s.rstrip("Z").rstrip("z")
        # Handle optional milliseconds
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s_clean, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _filing_date_fallback_dt(filing_date_str: str) -> Optional[datetime]:
    """Build a conservative UTC datetime from a filingDate string.

    Uses 16:01 ET = 21:01 UTC (EST) / 20:01 UTC (EDT). We use the fixed
    UTC−5 offset (EST) as the conservative choice (later = more conservative).
    '2020-12-31' → 2020-12-31T21:01:00Z
    """
    if not filing_date_str:
        return None
    try:
        d = date.fromisoformat(filing_date_str)
        # 16:01 ET in UTC−5 (EST) → 21:01 UTC
        return datetime(d.year, d.month, d.day, 21, 1, 0, tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Entry date resolver
# ---------------------------------------------------------------------------

def _entry_date_from_event_ts(
    event_ts: datetime,
    df: pd.DataFrame,
    entry_lag_days: int = 1,
) -> Optional[tuple[date, float, bool]]:
    """Resolve (entry_date, entry_price, used_close_fallback) for an event.

    Fork A / Option 1: entry = Open of the first tradeable day >= (event_ts ET date + lag).

    Default entry_lag_days=1 (plan default: enter on next business day's open):
      candidate_date = event_ts ET date + entry_lag_days calendar days

    The 16:00 ET cutoff applies only for entry_lag_days=0 (caller requests same-day
    pre-market entry):
      - time < 16:00 ET  → same day's open (trading day)
      - time >= 16:00 ET → next calendar day (after-hours — market closed)

    For entry_lag_days >= 1 the 16:00 cutoff is irrelevant: we always advance by
    entry_lag_days calendar days from the ET date.  _first_trading_close_on_or_after
    then skips weekends/holidays to land on the actual next trading day.

    ADV-09 asymmetry (entry_lag_days=0 only): a same-day pre-market filing enters
    on that day's OPEN, whose price may already reflect pre-market reaction to the
    filing — so a 04:00 ET filer (before price discovery) and a 09:25 ET filer
    (after pre-market moves) share the same entry_date but a materially different
    entry Open.  This contamination does NOT apply to the default entry_lag_days=1
    (the entry bar is always the NEXT day).  run_event_study flags lag=0 usage as
    meta['same_day_entry']=True so reviewers know the entry Open may be info-laden.

    Returns (entry_date, entry_price, used_close_fallback) or None if no trading
    row found in df.
    """
    et_dt = _to_et(event_ts)
    event_et_date = et_dt.date()

    if entry_lag_days == 0:
        # Caller requests same-day entry with 16:00 ET cutoff
        if et_dt.hour >= 16:
            candidate_date = event_et_date + timedelta(days=1)
        else:
            candidate_date = event_et_date
    else:
        # Default (lag=1): always advance by entry_lag_days calendar days
        candidate_date = event_et_date + timedelta(days=entry_lag_days)

    # Find first trading row on or after candidate_date
    result = _first_trading_close_on_or_after(df, candidate_date)
    if result is None:
        return None

    entry_date, entry_close = result

    # Prefer Open column
    used_close_fallback = False
    try:
        dates_list = _frame_dates(df)
        for i, d in enumerate(dates_list):
            if d == entry_date:
                row = df.iloc[i]
                if "Open" in df.columns and not pd.isna(row["Open"]) and float(row["Open"]) > 0:
                    return (entry_date, float(row["Open"]), False)
                else:
                    used_close_fallback = True
                    return (entry_date, entry_close, True)
    except Exception as exc:
        log.warning("_entry_date_from_event_ts: Open lookup failed for %s: %s", entry_date, exc)

    return (entry_date, entry_close, True)


# ---------------------------------------------------------------------------
# Block bootstrap (primary test)
# ---------------------------------------------------------------------------

def _block_bootstrap_pvalue(
    values: np.ndarray,
    block_size: int,
    n_boot: int = 999,
    rng: Optional[np.random.Generator] = None,
    return_diag: bool = False,
):
    """Moving-block bootstrap (MBB) p-value for H0: mean=0 (two-sided).

    Implements the moving-block bootstrap (Künsch 1989), NOT the Politis-Romano
    stationary bootstrap: fixed-length blocks of length L are drawn by sampling
    start indices uniformly in [0, n-L], concatenated until length >= n, trimmed
    to n, mean computed, repeated `n_boot` times.  Values are shifted to satisfy
    H0 (same trick as power_audit._bootstrap_pvalue).  Fixed-length blocks make
    the resampled series not strictly stationary; for event-time excess returns
    this is adequate (COR-04).

    COR-01: block length is capped at min(block_size, n // 2) — a CEILING that
    prevents a single block from filling the whole series (degenerate) while
    preserving far more of the autocorrelation correction than the old n // 4
    cap, which crushed dense overlapping-return studies and inflated FPR.

    ADV-10: at very small n the floor forces L=1 (iid resample).  When the cap is
    binding (L < requested block_size) the caller is told via the `capped` flag so
    it can WARN and mark meta — an iid bootstrap labelled "block bootstrap" has no
    inferential value.

    Returns p (float) by default, or (p, used_L, capped) when return_diag=True.
    """
    n = len(values)
    if n == 0 or n == 1:
        return (1.0, max(1, block_size), block_size > 1) if return_diag else 1.0
    if rng is None:
        rng = np.random.default_rng()

    obs_mean = float(np.mean(values))
    # Shift to satisfy H0
    shifted = values - obs_mean

    # COR-01: cap is a CEILING at n//2 (was n//4 — too aggressive for overlap).
    L = max(1, min(block_size, max(n // 2, 1)))
    capped = L < block_size

    boot_means = np.empty(n_boot)
    for b in range(n_boot):
        blocks = []
        collected = 0
        while collected < n:
            start = int(rng.integers(0, n - L + 1))
            blocks.append(shifted[start: start + L])
            collected += L
        boot_sample = np.concatenate(blocks)[:n]
        boot_means[b] = float(np.mean(boot_sample))

    p = float(np.mean(np.abs(boot_means) >= abs(obs_mean)))
    return (p, L, capped) if return_diag else p


# ---------------------------------------------------------------------------
# FDR Ledger (Benjamini-Hochberg)
# ---------------------------------------------------------------------------

class FDRLedger:
    """Benjamini-Hochberg FDR multiplicity ledger.

    Usage:
        ledger = FDRLedger(q=0.10)
        ledger.add("excess_21d", p_value=0.031, description="...")
        report = ledger.finalize()  # {hypothesis: {p_raw, p_adj, rejected, rank}}
    """

    def __init__(self, q: float = 0.10) -> None:
        self.q = q
        self._entries: list[dict] = []
        self._finalized: Optional[dict] = None

    def add(self, hypothesis: str, p_value: float, description: str = "") -> None:
        if self._finalized is not None:
            raise RuntimeError("FDRLedger.add() called after finalize()")
        self._entries.append({
            "hypothesis": hypothesis,
            "p_raw": float(p_value),
            "description": description,
        })

    def finalize(self) -> dict:
        """Apply the Benjamini-Hochberg step-up procedure.

        Returns {hypothesis: {p_raw, p_adj, rejected, rank}}.

        Two correctness properties enforced here (TST-05 / PY-02 / COR-02):

        1. **Step-up rejection.**  Find the largest rank k with p_(k) <= (k/m)*q,
           then reject ALL hypotheses of rank <= k.  Evaluating each rank
           independently is wrong: with tied p-values it can reject a higher rank
           while leaving a lower (smaller-threshold) rank un-rejected — incoherent.

        2. **Monotone adjusted p-values.**  The raw Simes value p_(i)*m/i is not
           monotone in rank.  A reverse cumulative-minimum pass (highest rank down)
           enforces p_adj[i] <= p_adj[i+1], matching scipy's
           false_discovery_control.  Ties are handled naturally because both passes
           operate on the rank-sorted array.
        """
        if self._finalized is not None:
            return self._finalized
        m = len(self._entries)
        if m == 0:
            self._finalized = {}
            return self._finalized

        # Sort by p_value ascending, tracking original hypothesis names.
        sorted_entries = sorted(
            enumerate(self._entries), key=lambda x: x[1]["p_raw"]
        )

        # --- (1) Step-up rejection: largest k with p_(k) <= (k/m)*q -----------
        max_k = 0
        for rank_0, (_, entry) in enumerate(sorted_entries):
            rank = rank_0 + 1
            if entry["p_raw"] <= (rank / m) * self.q:
                max_k = rank
        # Reject everything at rank <= max_k.

        # --- (2) Raw Simes p_adj, then reverse cumulative-min for monotonicity -
        raw_adj = [
            min(1.0, entry["p_raw"] * m / (rank_0 + 1))
            for rank_0, (_, entry) in enumerate(sorted_entries)
        ]
        running_min = 1.0
        mono_adj = [0.0] * m
        for i in range(m - 1, -1, -1):
            running_min = min(running_min, raw_adj[i])
            mono_adj[i] = running_min

        result = {}
        for rank_0, (_, entry) in enumerate(sorted_entries):
            rank = rank_0 + 1
            result[entry["hypothesis"]] = {
                "p_raw": entry["p_raw"],
                "p_adj": mono_adj[rank_0],
                "rejected": rank <= max_k,
                "rank": rank,
                "description": entry["description"],
            }
        self._finalized = result
        return result


# ---------------------------------------------------------------------------
# Universe-median cache helper
# ---------------------------------------------------------------------------

def _forward_return_terminal(
    df: pd.DataFrame,
    entry_date: date,
    entry_open: float,
    horizon: int,
    direction: str = "long",
) -> tuple[Optional[float], bool]:
    """Open-anchored forward return at `horizon` with SYMMETRIC delisting handling.

    ADV-03: a name (pick or peer) whose price series ends *inside* the horizon is
    NOT silently dropped — it contributes its TERMINAL value (last available close)
    as the exit price.  This treats delisting symmetrically on both sides of the
    excess: long-horizon survivorship no longer biases the universe median upward
    by quietly excluding the worst (delisted) peers.

    Returns (return_pct, was_terminal) where was_terminal is True when the exit
    was taken from the last available bar rather than the bar at offset N.  Returns
    (None, False) only when the entry bar itself cannot be located.
    """
    if df is None or df.empty or entry_open <= 0:
        return (None, False)
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
        return (None, False)
    if "Close" not in df.columns:
        return (None, False)
    n_rows = len(dates)
    exit_idx = entry_idx + horizon
    was_terminal = False
    if exit_idx >= n_rows:
        # Series ends inside the horizon → use terminal (last) bar's close.
        exit_idx = n_rows - 1
        was_terminal = True
        if exit_idx <= entry_idx:
            return (None, False)  # no forward bars at all
    exit_close = float(df.iloc[exit_idx]["Close"])
    if direction == "short":
        r = (entry_open - exit_close) / entry_open * 100.0
    else:
        r = (exit_close - entry_open) / entry_open * 100.0
    return (r, was_terminal)


def _resolve_entry_open(
    df: pd.DataFrame,
    entry_date: date,
    sym: str,
) -> Optional[float]:
    """PY-04: Shared helper — resolve entry Open price for sym on entry_date.

    Prefers Open column; falls back to Close when Open is missing or zero.
    Returns None if the date is not in df or if an exception occurs.
    Logs a warning on exception so shrinkage of the peer/universe set is
    observable (PY-07).
    """
    try:
        dates_list = _frame_dates(df)
        for i, d in enumerate(dates_list):
            if d == entry_date:
                row = df.iloc[i]
                if "Open" in df.columns and not pd.isna(row["Open"]) and float(row["Open"]) > 0:
                    return float(row["Open"])
                else:
                    return float(row["Close"])
        return None
    except Exception as exc:
        log.warning(
            "Open lookup failed for %s at %s: %s",
            sym, entry_date, exc,
        )
        return None


def _build_return_vector(
    entry_date: date,
    horizon: int,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    universe_tickers: list[str],
) -> dict[str, tuple[Optional[float], bool]]:
    """Build the per-symbol forward-return vector for ALL floor-passing universe
    symbols alive on `entry_date` at `horizon`.

    F351: called ONCE per (entry_date, horizon) across all events sharing that date.
    Callers derive leave-one-out medians by dropping the event ticker from this
    vector (cheap dict-drop + median) rather than re-scanning every universe frame.

    Returns {symbol: (fwd_return_pct, is_terminal)}.
    Symbol is included in the dict ONLY when it passes all gates AND
    _forward_return_terminal returns a non-None float.  Symbols absent from the
    dict either failed a gate or had no entry bar.  Callers can safely assume
    all values in the dict have a valid (non-None) return.
    """
    vector: dict[str, tuple[Optional[float], bool]] = {}
    for sym in universe_tickers:
        df = loader_fn(sym)
        if df is None or df.empty:
            continue
        # ADV-01: floor decided from info available BEFORE entry.
        fs = _floor_status(df, entry_date)
        if fs != _FLOOR_OK:
            continue
        res = _first_trading_close_on_or_after(df, entry_date)
        if res is None:
            continue
        e_date, _ = res
        if e_date != entry_date:
            continue  # symbol doesn't trade on entry_date
        # PY-04: shared Open-lookup helper.
        entry_open = _resolve_entry_open(df, entry_date, sym)
        if entry_open is None or entry_open <= 0:
            continue
        r, was_terminal = _forward_return_terminal(df, entry_date, entry_open, horizon)
        if r is not None:
            vector[sym] = (r, was_terminal)
    return vector


def _compute_universe_median(
    entry_date: date,
    horizon: int,
    exclude_ticker: str,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    universe_tickers: list[str],
    _vector_cache: Optional[_VectorCache] = None,
) -> tuple[Optional[float], int, int]:
    """Compute the universe-median forward return (from Open) at `horizon` for
    all floor-passing symbols alive on `entry_date`, excluding `exclude_ticker`.

    Floor inclusion is decided at the LAST close ON OR BEFORE `entry_date` (the
    caller passes a pre-entry as_of, see ADV-01) — never using the entry bar.

    ADV-03: peers whose series ends inside the horizon contribute their terminal
    value rather than being excluded.  The count of such terminal (attrited) peers
    is returned per call so survivorship attrition can be persisted in meta.

    F351 performance: when `_vector_cache` is supplied (a dict shared across all
    events in the same run_event_study call), the full universe scan is performed
    ONCE per (entry_date, horizon) and cached; subsequent calls for the same date
    with a different exclude_ticker drop one key and take the median — O(1) per
    additional event on a shared date.  When _vector_cache is None (standalone
    call, legacy API), the function falls back to scanning the universe directly.

    Returns (median, count_of_valid_symbols, count_terminal_peers).
    Fork B / Option 1: same-date universe, pick excluded.
    """
    vec_key = (entry_date, horizon)

    if _vector_cache is not None:
        # Build the shared vector once per (entry_date, horizon).
        if vec_key not in _vector_cache:
            _vector_cache[vec_key] = _build_return_vector(
                entry_date, horizon, loader_fn, universe_tickers
            )
        vector = _vector_cache[vec_key]
        returns: list[float] = []
        terminal_peers = 0
        for sym, (r, was_terminal) in vector.items():
            if sym == exclude_ticker:
                continue
            # COR-02: _build_return_vector guarantees r is never None in the vector;
            # the dead `if r is not None` guard has been removed.
            returns.append(r)
            if was_terminal:
                terminal_peers += 1
        if not returns:
            return (None, 0, terminal_peers)
        return (float(np.median(returns)), len(returns), terminal_peers)

    # Legacy path (no vector cache supplied — identical to original logic).
    returns_direct: list[float] = []
    terminal_peers_direct = 0
    for sym in universe_tickers:
        if sym == exclude_ticker:
            continue
        df = loader_fn(sym)
        if df is None or df.empty:
            continue
        fs = _floor_status(df, entry_date)
        if fs != _FLOOR_OK:
            continue
        res = _first_trading_close_on_or_after(df, entry_date)
        if res is None:
            continue
        e_date, _ = res
        if e_date != entry_date:
            continue
        entry_open = _resolve_entry_open(df, entry_date, sym)
        if entry_open is None or entry_open <= 0:
            continue
        r, was_terminal = _forward_return_terminal(df, entry_date, entry_open, horizon)
        if r is not None:
            returns_direct.append(r)
            if was_terminal:
                terminal_peers_direct += 1

    if not returns_direct:
        return (None, 0, terminal_peers_direct)
    return (float(np.median(returns_direct)), len(returns_direct), terminal_peers_direct)


# ---------------------------------------------------------------------------
# F349: Sector-peer benchmark helpers
# ---------------------------------------------------------------------------

def _load_ticker_to_sic(
    universe_tickers: list[str],
    sic_cache_path: Optional[Path] = None,
) -> tuple[dict[str, Optional[str]], int]:
    """F349 (PROGRAM.md rule 6a): Load ticker→SIC map from EDGAR submissions cache.

    Reads edgar_cache/universe.json for ticker→CIK, then
    submissions/{cik}.json for the 'sic' field.
    Returns ({TICKER: "1311" or None}, parse_error_count).

    TICKER maps to None if the submission file is missing, unreadable, or has
    no sic entry.  parse_error_count counts JSON decode failures so callers can
    surface them in meta (DI-03: silent cache-corruption is observable post-run
    without changing the graceful-degradation behaviour).

    sic_cache_path: override for the submissions directory (tests use tmp).
    """
    submissions_dir = sic_cache_path or _SUBMISSIONS_DIR
    universe_path = (submissions_dir.parent / "universe.json")

    # Load ticker→CIK mapping from universe.json.
    ticker_to_cik: dict[str, str] = {}
    if universe_path.exists():
        try:
            raw = json.loads(universe_path.read_text(encoding="utf-8"))
            # universe.json layout: {"0": {"cik_str": int, "ticker": "NVDA", "title": ...}, ...}
            # or normalized {TICKER: {"cik_str": "0000320193", "title": ...}}.
            # edgar.fetch_universe() returns the normalized form; the raw SEC file uses the
            # numeric-key form.  Detect by checking whether first value has "ticker" key.
            sample = next(iter(raw.values()), {})
            if "ticker" in sample:
                # Raw SEC format
                for entry in raw.values():
                    t = str(entry.get("ticker", "")).upper()
                    cik_raw = entry.get("cik_str", 0)
                    if t:
                        ticker_to_cik[t] = str(cik_raw).zfill(10)
            else:
                # Normalized format: {TICKER: {"cik_str": "0000320193", ...}}
                for t, info in raw.items():
                    cik_raw = info.get("cik_str", 0)
                    ticker_to_cik[t.upper()] = str(cik_raw).zfill(10)
        except Exception as exc:
            log.warning("F349: failed to load universe.json from %s: %s", universe_path, exc)

    result: dict[str, Optional[str]] = {}
    parse_errors = 0
    for ticker in universe_tickers:
        cik = ticker_to_cik.get(ticker.upper())
        if cik is None:
            result[ticker] = None
            continue
        sub_path = submissions_dir / f"{cik}.json"
        if not sub_path.exists():
            result[ticker] = None
            continue
        try:
            sub = json.loads(sub_path.read_text(encoding="utf-8"))
            sic = sub.get("sic")
            result[ticker] = str(sic) if sic else None
        except Exception as exc:
            log.warning("F349: failed to read submission %s: %s", sub_path, exc)
            result[ticker] = None
            parse_errors += 1
    return result, parse_errors


def _get_peer_set_by_sic(
    ticker: str,
    universe_tickers: list[str],
    ticker_to_sic: dict[str, Optional[str]],
    min_peers: int = 5,
) -> tuple[list[str], Optional[str], str]:
    """F349 (PROGRAM.md rule 6a): Find peer set for ticker via SIC fallback cascade.

    1. Try 3-digit SIC match (excluding event ticker itself).
    2. If count < min_peers, try 2-digit prefix.
    3. If still < min_peers, use full universe (excluding event ticker).

    Returns (peer_tickers, sic_code_or_None, fallback_level).
    fallback_level ∈ {"3_digit", "2_digit", "universe"}.
    """
    sic = ticker_to_sic.get(ticker)
    # Exclude the event ticker itself (mirrors universe-median exclude_ticker discipline).
    others = [t for t in universe_tickers if t != ticker]

    # COR-02: cascade is independent per prefix length — a 2-char SIC must still
    # reach the 2-digit branch even though it cannot enter the 3-digit branch.
    if sic and len(sic) >= 3:
        prefix3 = sic[:3]
        peers3 = [t for t in others if (ticker_to_sic.get(t) or "")[:3] == prefix3]
        if len(peers3) >= min_peers:
            return (peers3, sic, "3_digit")

    if sic and len(sic) >= 2:
        prefix2 = sic[:2]
        peers2 = [t for t in others if (ticker_to_sic.get(t) or "")[:2] == prefix2]
        if len(peers2) >= min_peers:
            return (peers2, sic, "2_digit")

    # Full-universe fallback
    return (others, sic, "universe")


def _compute_peer_median(
    entry_date: date,
    horizon: int,
    pick_ticker: str,
    peer_tickers: list[str],
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    _vector_cache: Optional[_VectorCache] = None,
) -> tuple[Optional[float], int, int]:
    """F349 (PROGRAM.md rule 6a): Compute peer-median forward return.

    Operates on an explicit peer_tickers list.  PY-03: self-exclusion is
    enforced inline (pick_ticker always excluded regardless of peer_tickers),
    defensive against future callers not going through _get_peer_set_by_sic.
    Preserves: floor-pass discipline at entry_date, Open-anchor, terminal-exit ADV-03.

    F351 performance: when `_vector_cache` is supplied (shared with
    _compute_universe_median), the universe frame scan is reused — peer median
    pulls DIRECTLY from the cached vector rather than re-scanning frames.
    Only symbols already in the vector (floor-passed, trades on entry_date,
    Open available) are considered, which is methodologically identical to the
    previous full re-scan (both paths apply the same floor/date/Open gates).

    PY-04: if _vector_cache is non-None but the key is missing (cold cache —
    peer median called before universe median for this date), the vector is
    built on-demand from peer_tickers and stored.  This removes the implicit
    "universe median must run first" ordering dependency.

    When _vector_cache is None (standalone call, legacy API), frames are scanned
    directly.

    Returns (median, count_valid_peers, count_terminal_peers).
    """
    vec_key = (entry_date, horizon)

    if _vector_cache is not None:
        # PY-04: build the vector on-demand if the key is absent (cold cache).
        if vec_key not in _vector_cache:
            _vector_cache[vec_key] = _build_return_vector(
                entry_date, horizon, loader_fn, peer_tickers
            )
        # Fast path: derive peer subset from the shared return vector.
        # The vector only contains floor-passed, same-date, Open-available symbols,
        # so all methodology gates are already applied — no re-scan needed.
        vector = _vector_cache[vec_key]
        peer_set = set(peer_tickers)
        returns: list[float] = []
        terminal_peers = 0
        for sym, (r, was_terminal) in vector.items():
            # PY-03: defensive self-exclusion.
            if sym == pick_ticker:
                continue
            if sym not in peer_set:
                continue
            # COR-02: _build_return_vector guarantees r is never None in the vector.
            returns.append(r)
            if was_terminal:
                terminal_peers += 1
        if not returns:
            return (None, 0, terminal_peers)
        return (float(np.median(returns)), len(returns), terminal_peers)

    # Legacy / standalone path (vector cache not available for this key).
    returns_direct: list[float] = []
    terminal_peers_direct = 0
    for sym in peer_tickers:
        # PY-03: defensive self-exclusion guard.
        if sym == pick_ticker:
            continue
        df = loader_fn(sym)
        if df is None or df.empty:
            continue
        # ADV-01: floor decided from info available BEFORE entry.
        fs = _floor_status(df, entry_date)
        if fs != _FLOOR_OK:
            continue
        res = _first_trading_close_on_or_after(df, entry_date)
        if res is None:
            continue
        e_date, _ = res
        if e_date != entry_date:
            continue
        # PY-04: shared Open-lookup helper.
        entry_open = _resolve_entry_open(df, entry_date, sym)
        if entry_open is None or entry_open <= 0:
            continue
        r, was_terminal = _forward_return_terminal(df, entry_date, entry_open, horizon)
        if r is not None:
            returns_direct.append(r)
            if was_terminal:
                terminal_peers_direct += 1

    if not returns_direct:
        return (None, 0, terminal_peers_direct)
    return (float(np.median(returns_direct)), len(returns_direct), terminal_peers_direct)


# ---------------------------------------------------------------------------
# Non-overlapping filter
# ---------------------------------------------------------------------------

def _filter_non_overlapping(
    outcomes: list[EventOutcome],
    horizon: int,
) -> list[EventOutcome]:
    """Greedy chronological filter: include an event only if
    entry_date > last_included_entry_date + horizon (calendar days).
    """
    filtered: list[EventOutcome] = []
    last_date: Optional[date] = None
    for ev in sorted(outcomes, key=lambda e: e.entry_date):
        if last_date is None or (ev.entry_date - last_date).days > horizon:
            filtered.append(ev)
            last_date = ev.entry_date
    return filtered


# ---------------------------------------------------------------------------
# Statistics computation
# ---------------------------------------------------------------------------

def _block_size_for_horizon(
    horizon: int,
    entry_dates: list[date],
) -> int:
    """Compute MBB block size: round(horizon / median_gap_trading_days), floor 1.

    Capped at n // 4 at call time (done inside _block_bootstrap_pvalue).
    """
    if len(entry_dates) < 2:
        return 1
    sorted_dates = sorted(entry_dates)
    gaps_td: list[float] = []
    for i in range(1, len(sorted_dates)):
        cal_gap = (sorted_dates[i] - sorted_dates[i - 1]).days
        td_gap = cal_gap * (252.0 / 365.0)
        gaps_td.append(max(0.1, td_gap))
    median_gap = float(np.median(gaps_td))
    if median_gap <= 0:
        return 1
    return max(1, round(horizon / median_gap))


def _era_breakdown(
    outcomes: list[EventOutcome],
    horizons: tuple[int, ...],
    eras: tuple[tuple[str, date, date], ...],
) -> dict:
    """Group `outcomes` by sub-era and report per-era mean excess + sign agreement.

    Era-consistency (John, 2026-06-06): for each (label, start, end) era and each
    horizon, report n, mean excess (ppt) and `sign_agreement` — the fraction of
    events whose excess sign matches the era's mean sign (a crude within-era
    stability read).  Pure grouping; no resampling.
    """
    out: dict = {}
    for label, start, end in eras:
        in_era = [o for o in outcomes if start <= o.entry_date <= end]
        per_h: dict = {}
        for h in horizons:
            vals = [
                o.fwd_excess_pct.get(h)
                for o in in_era
                if o.fwd_excess_pct.get(h) is not None
            ]
            if not vals:
                per_h[h] = {"n": 0, "mean_excess_pct": None, "sign_agreement": None}
                continue
            arr = np.array(vals, dtype=float)
            mean = float(np.mean(arr))
            if mean >= 0:
                agree = float(np.mean(arr >= 0))
            else:
                agree = float(np.mean(arr < 0))
            per_h[h] = {
                "n": len(vals),
                "mean_excess_pct": round(mean, 4),
                "sign_agreement": round(agree, 4),
            }
        out[label] = {"n_events": len(in_era), "per_horizon": per_h}
    return out


def _regime_breakdown(
    outcomes: list[EventOutcome],
    horizons: tuple[int, ...],
    low_count_threshold: int = 10,
) -> dict:
    """F350 (PROGRAM.md rule 6a): Group outcomes by regime state and report
    per-regime effect + sign agreement.

    Mirrors _era_breakdown() structure:
      {regime: {n_events, per_horizon: {h: {n, mean_excess_pct, sign_agreement}}}}

    Both universe excess (fwd_excess_pct) AND peer excess (fwd_peer_excess_pct)
    are reported per horizon when available, so regime is a lens on both numbers.

    Regime vocabulary (charter): RISK_ON / NEUTRAL / RISK_OFF / STRESS.
    RISK_OFF is the rare state (~6 trading days 2015-2024); regimes with
    n_events < low_count_threshold get LOW_COUNT_FLAG=True.
    """
    # Collect all states present (guaranteed order for consistent output).
    _STATE_ORDER = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS")
    out: dict = {}
    for state in _STATE_ORDER:
        in_state = [o for o in outcomes if o.regime_state == state]
        per_h: dict = {}
        for h in horizons:
            # Universe excess
            univ_vals = [
                o.fwd_excess_pct.get(h)
                for o in in_state
                if o.fwd_excess_pct.get(h) is not None
            ]
            # Peer excess (F349; may be empty if SIC not loaded)
            peer_vals = [
                o.fwd_peer_excess_pct.get(h)
                for o in in_state
                if o.fwd_peer_excess_pct.get(h) is not None
            ]
            if not univ_vals:
                per_h[h] = {
                    "n": 0,
                    "mean_excess_pct": None,
                    "sign_agreement": None,
                    "peer_mean_excess_pct": float(np.mean(peer_vals)) if peer_vals else None,
                }
                continue
            arr = np.array(univ_vals, dtype=float)
            mean = float(np.mean(arr))
            agree = float(np.mean(arr >= 0)) if mean >= 0 else float(np.mean(arr < 0))
            per_h[h] = {
                "n": len(univ_vals),
                "mean_excess_pct": round(mean, 4),
                "sign_agreement": round(agree, 4),
                "peer_mean_excess_pct": round(float(np.mean(peer_vals)), 4) if peer_vals else None,
            }
        blk: dict = {"n_events": len(in_state), "per_horizon": per_h}
        if len(in_state) < low_count_threshold:
            blk["LOW_COUNT_FLAG"] = True
        out[state] = blk
    return out


def compute_study_stats(
    outcomes: list[EventOutcome],
    config: EventStudyConfig,
    rng: Optional[np.random.Generator] = None,
) -> dict:
    """Compute per-horizon statistics for the explore slice.

    Returns a structured dict suitable for inclusion in meta.json.
    """
    if rng is None:
        rng = np.random.default_rng(42)

    explore = [o for o in outcomes if o.split == "explore"]
    confirm = [o for o in outcomes if o.split == "confirm"]

    stats: dict = {
        "n_total": len(outcomes),
        "n_explore": len(explore),
        "n_confirm": len(confirm),
        "horizons": list(config.horizons),
        "n_boot": config.n_boot,          # ADV-05: persist multiplicity context
        "fdr_q": config.fdr_q,
        "per_horizon": {},
        "mde_by_horizon": {},
        "mde_raw_by_horizon": {},          # ADV-07: MDE on raw (absolute) returns
        "fdr_report": {},
        "block_size_warnings": [],
    }

    ledger = FDRLedger(q=config.fdr_q)

    for h in config.horizons:
        # COR-05: estimate autocorrelation structure from the SAME filtered set
        # that is actually tested — not the superset of all explore dates (which
        # includes floor/None-excess drops and biases the median gap upward).
        valid_explore = [o for o in explore if o.fwd_excess_pct.get(h) is not None]
        excess_vals = [o.fwd_excess_pct.get(h) for o in valid_explore]
        raw_vals = [
            o.fwd_return_pct.get(h)
            for o in valid_explore
            if o.fwd_return_pct.get(h) is not None
        ]
        entry_dates_h = [o.entry_date for o in valid_explore]
        n_valid = len(excess_vals)

        block_size_capped = False
        block_size_used = 1
        block_size_requested = 1
        block_size_source = "computed"
        if n_valid < 2:
            mde = float("nan")
            mde_raw = float("nan")
            p_boot = 1.0
            p_nw = 1.0
            mean_excess = float("nan")
            std_excess = float("nan")
        else:
            arr = np.array(excess_vals, dtype=float)
            mean_excess = float(np.mean(arr))
            std_excess = float(np.std(arr, ddof=1))
            mde = minimum_detectable_effect(n_valid, std_excess)

            # ADV-07: also report MDE on raw absolute returns (the "generates
            # positive return" claim, distinct from "beats peers").
            if len(raw_vals) >= 2:
                raw_arr = np.array(raw_vals, dtype=float)
                std_raw = float(np.std(raw_arr, ddof=1))
                mde_raw = minimum_detectable_effect(len(raw_vals), std_raw)
            else:
                mde_raw = float("nan")

            # ADV-06: pre-registered block-size override beats density estimate.
            if config.block_size_override is not None:
                block_size_requested = int(config.block_size_override)
                block_size_source = "preregistered"
            else:
                block_size_requested = _block_size_for_horizon(h, entry_dates_h)
                block_size_source = "computed"

            p_boot, block_size_used, block_size_capped = _block_bootstrap_pvalue(
                arr, block_size=block_size_requested, n_boot=config.n_boot,
                rng=rng, return_diag=True,
            )
            if block_size_capped:
                msg = (
                    f"h={h}: block_size capped {block_size_requested}->{block_size_used} "
                    f"(n={n_valid}); bootstrap under-corrects autocorrelation"
                )
                log.warning("block_size cap binding: %s", msg)
                stats["block_size_warnings"].append(msg)

            # NW cross-check
            nw_lag = _compute_nw_lag(
                [pd.Timestamp(d) for d in sorted(entry_dates_h)],
                forward_days=h,
            )
            p_nw = _nw_ttest_pvalue(arr, nw_lag=nw_lag)

            if abs(p_boot - p_nw) > 0.10:
                log.warning(
                    "bootstrap and NW disagree (h=%d): p_boot=%.3f p_nw=%.3f — "
                    "check event density / block size",
                    h, p_boot, p_nw,
                )

        stats["mde_by_horizon"][h] = round(mde * 100, 4) if math.isfinite(mde) else None
        stats["mde_raw_by_horizon"][h] = round(mde_raw * 100, 4) if math.isfinite(mde_raw) else None
        stats["per_horizon"][h] = {
            "n_explore_valid": n_valid,
            "mean_excess_pct": round(mean_excess, 4) if math.isfinite(mean_excess) else None,
            "std_excess_pct": round(std_excess, 4) if math.isfinite(std_excess) else None,
            "mde_ppt": round(mde * 100, 4) if math.isfinite(mde) else None,
            "mde_raw_ppt": round(mde_raw * 100, 4) if math.isfinite(mde_raw) else None,
            "p_bootstrap": round(p_boot, 4),
            "p_nw": round(p_nw, 4),
            "block_size_used": block_size_used,
            "block_size_requested": block_size_requested,
            "block_size_source": block_size_source,
            "block_size_capped": block_size_capped,
        }
        ledger.add(f"excess_{h}d", p_value=p_boot, description=f"mean excess at {h}d horizon (block bootstrap)")

    stats["fdr_report"] = ledger.finalize()

    # Era-consistency report (explore): per-sub-era mean excess + sign agreement.
    stats["era_consistency"] = _era_breakdown(explore, config.horizons, config.explore_eras)
    # Confirm-era breakdown mechanics (flagged; populated once confirm exists).
    stats["confirm_era_breakdown"] = _era_breakdown(confirm, config.horizons, config.confirm_eras)
    # F350: regime-breakdown lens — per-regime effect across all outcomes (explore+confirm).
    stats["regime_breakdown"] = _regime_breakdown(outcomes, config.horizons)

    # Non-overlapping variant for primary horizon (first in list) if requested
    if config.use_non_overlapping and explore:
        h0 = config.horizons[0]
        no_overlap = _filter_non_overlapping(explore, h0)
        stats["non_overlapping"] = {
            "horizon": h0,
            "n_full": len(explore),
            "n_filtered": len(no_overlap),
        }

    return stats


def print_study_report(meta: dict) -> None:
    """Print plain-English study report to stdout (MDE line always included)."""
    print(f"\n=== Event Study: {meta.get('study_name', '?')} ===")
    print(f"  Events: {meta.get('n_events', '?')} total, "
          f"{meta.get('n_explore', '?')} explore, "
          f"{meta.get('n_confirm', '?')} confirm")
    print(f"  Created: {meta.get('created_at', '?')}")

    # ADV-02: explicit event-stream survivorship line + warning when severe.
    surv = meta.get("survivorship", {})
    if surv:
        print(f"  Survivorship: {surv.get('events_total', 0)} total, "
              f"{surv.get('events_no_price_data', 0)} no-price-data, "
              f"{surv.get('events_below_floor', 0)} below-floor, "
              f"{surv.get('events_entered', 0)} entered "
              f"(fallback ts: {meta.get('acceptance_dt_fallbacks', 0)})")
        if surv.get("survivorship_warning"):
            frac = surv.get("no_price_data_fraction", 0.0)
            print(f"  ** SURVIVORSHIP WARNING: {frac*100:.1f}% of events had no price "
                  f"data (> {_NO_PRICE_WARN_FRACTION*100:.0f}%) — explore set may be "
                  f"biased toward cache survivors. Verdict suspect. **")

    per_h = meta.get("per_horizon", {})
    mde_h = meta.get("mde_by_horizon", {})
    mde_raw_h = meta.get("mde_raw_by_horizon", {})
    for h, stats in per_h.items():
        n = stats.get("n_explore_valid", 0)
        mde = mde_h.get(h)
        mde_raw = mde_raw_h.get(h)
        mean = stats.get("mean_excess_pct")
        p_boot = stats.get("p_bootstrap")
        p_nw = stats.get("p_nw")
        if mde is not None:
            print(f"  [{h}d] n={n}, mean_excess={mean:.2f}ppt, "
                  f"p_boot={p_boot:.3f}, p_nw={p_nw:.3f}, "
                  f"block_size={stats.get('block_size_used')}"
                  f"{' (CAPPED)' if stats.get('block_size_capped') else ''}")
            print(f"       MDE (excess, vs peers): >= {mde:.2f}ppt at {h}d (80% power).")
            if mde_raw is not None:
                # ADV-07: raw MDE measures the "positive absolute return" claim.
                print(f"       MDE (raw, absolute return): >= {mde_raw:.2f}ppt at {h}d (80% power).")
        else:
            print(f"  [{h}d] insufficient data (n={n})")

    fdr = meta.get("fdr_report", {})
    if fdr:
        rejected = [k for k, v in fdr.items() if v.get("rejected")]
        print(f"  FDR (BH q={meta.get('fdr_q', 0.10)}): "
              f"{len(rejected)}/{len(fdr)} hypotheses rejected: {rejected}")

    # Era-consistency one-liner (explore).
    era = meta.get("era_consistency", {})
    if era:
        h0 = meta.get("horizons", [None])[0]
        parts = []
        for label, blk in era.items():
            ph = blk.get("per_horizon", {}).get(h0) or blk.get("per_horizon", {}).get(str(h0)) or {}
            m = ph.get("mean_excess_pct")
            parts.append(f"{label}: {m if m is not None else 'n/a'}ppt (n={blk.get('n_events', 0)})")
        print(f"  Era-consistency [{h0}d excess]: " + " | ".join(parts))

    # F350: Regime-breakdown one-liner (all outcomes; RISK_OFF is the rare ~6-day state).
    # Plain-English gloss: RISK_ON (calm) / NEUTRAL / RISK_OFF (crisis) / STRESS (stormy).
    _REGIME_GLOSS = {
        "RISK_ON": "RISK_ON (calm)",
        "NEUTRAL": "NEUTRAL",
        "RISK_OFF": "RISK_OFF (crisis)",
        "STRESS": "STRESS (stormy)",
    }
    regime = meta.get("regime_breakdown", {})
    if regime:
        h0 = meta.get("horizons", [None])[0]
        parts = []
        for state in ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"):
            blk = regime.get(state, {})
            ph = blk.get("per_horizon", {}).get(h0) or blk.get("per_horizon", {}).get(str(h0)) or {}
            m = ph.get("mean_excess_pct")
            low_flag = " [LOW-COUNT]" if blk.get("LOW_COUNT_FLAG") else ""
            gloss = _REGIME_GLOSS.get(state, state)
            parts.append(
                f"{gloss}: {m if m is not None else 'n/a'}ppt (n={blk.get('n_events', 0)}){low_flag}"
            )
        # F349: peer-excess summary if available at primary horizon.
        peer_summary = ""
        per_h_meta = meta.get("per_horizon", {})
        h0_str = str(h0)
        if h0_str in per_h_meta and per_h_meta[h0_str].get("peer_median_excess_pct") is not None:
            pme = per_h_meta[h0_str]["peer_median_excess_pct"]
            peer_summary = f" | Peer-excess (median across explore): {pme:.2f}ppt"
        print(f"  Regime breakdown [{h0}d excess]: " + " | ".join(parts) + peer_summary)
    print()


# ---------------------------------------------------------------------------
# Same-ticker event de-clustering (ADV-06 / ADV-08)
# ---------------------------------------------------------------------------

def _dedup_events(
    events: list[EventRecord],
    window_days: int,
) -> tuple[list[EventRecord], int]:
    """Collapse same-ticker events within `window_days` (by event_ts ET date) to
    the FIRST (chronologically earliest) event of each cluster.

    ADV-06/ADV-08: a single insider buy expressed as a cluster of Form 4s in one
    week is one economic signal, not N independent draws.  Treating them as N
    inflates the sample and breaks the bootstrap's independence assumption.

    Returns (deduped_events, n_dropped).  Order of survivors follows the input
    iteration order of the first event in each cluster (input order preserved).
    """
    if window_days <= 0 or not events:
        return list(events), 0
    # Sort each ticker's events by event_ts, greedily keep the first of a cluster.
    by_ticker: dict[str, list[EventRecord]] = {}
    for ev in events:
        by_ticker.setdefault(ev.ticker, []).append(ev)

    keep_ids: set[int] = set()
    dropped = 0
    for _ticker, evs in by_ticker.items():
        evs_sorted = sorted(evs, key=lambda e: _to_et(e.event_ts).date())
        cluster_anchor: Optional[date] = None
        for ev in evs_sorted:
            d = _to_et(ev.event_ts).date()
            if cluster_anchor is None or (d - cluster_anchor).days > window_days:
                keep_ids.add(id(ev))
                cluster_anchor = d
            else:
                dropped += 1
    deduped = [ev for ev in events if id(ev) in keep_ids]
    return deduped, dropped


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

def run_event_study(
    events: Iterable[EventRecord],
    config: EventStudyConfig,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    universe_tickers: Optional[list[str]] = None,
    rng: Optional[np.random.Generator] = None,
) -> tuple[list[EventOutcome], dict]:
    """Run an event study.

    Parameters
    ----------
    events : iterable of EventRecord
    config : EventStudyConfig
    loader_fn : callable(ticker: str) -> Optional[pd.DataFrame]
        Memoized price loader (use _make_memoized_loader from turnaround_validation).
    universe_tickers : list of tickers to use as universe reference.
        If None, only per-event returns are computed (no excess).
    rng : seeded RNG for reproducibility (default: seed=42).

    Survivorship & point-in-time discipline (P0 fixes):
      - ADV-01: floor/universe inclusion is decided from the last close ON OR
        BEFORE the event date (pre-entry as_of), never the entry bar.
      - ADV-02: events whose ticker is missing from the price cache are COUNTED
        (events_no_price_data), never silently dropped; > 10% triggers a warning.
      - ADV-03: at long horizons, a pick or peer whose series ends inside the
        horizon contributes its terminal (last) close as the exit, symmetric on
        both sides of the excess.  Per-horizon attrition counts are persisted.

    Returns
    -------
    (outcomes, meta_dict)
    """
    if rng is None:
        rng = np.random.default_rng(42)

    # Era-consistency hard guard (John, 2026-06-06): explore must not reach 2025+.
    if not config.allow_post_2020_explore and config.explore_cutoff > _EXPLORE_HARD_CEILING:
        raise ValueError(
            f"explore_cutoff {config.explore_cutoff} reaches past the hard ceiling "
            f"{_EXPLORE_HARD_CEILING}; 2025+ cache is reserved as a future fresh "
            f"confirm window. Set allow_post_2020_explore=True to override."
        )

    # ADV-06 / ADV-08: same-ticker de-clustering (default ON), raw count preserved.
    events_list = list(events)
    events_total_raw = len(events_list)
    if config.dedup_same_ticker:
        events_list, n_declustered = _dedup_events(events_list, config.dedup_window_days)
    else:
        n_declustered = 0

    # F349: Load SIC cache once per study (not per event).
    # universe_tickers may be None when caller doesn't supply a universe; SIC lookup
    # is silently disabled in that case (backward-compatible).
    ticker_to_sic: dict[str, Optional[str]] = {}
    sic_fallback_counts = {"3_digit": 0, "2_digit": 0, "universe": 0}
    sic_parse_errors = 0
    if universe_tickers is not None:
        ticker_to_sic, sic_parse_errors = _load_ticker_to_sic(
            universe_tickers, sic_cache_path=config.sic_coverage_path
        )
        sic_found = sum(1 for t in universe_tickers if ticker_to_sic.get(t))
    else:
        sic_found = 0

    # F350: Load regime_states.json once per study (not per event).
    regime_states_path = config.regime_states_path or _REGIME_STATES_PATH
    regime_states: Optional[dict] = None
    if regime_states_path.exists():
        try:
            regime_states = json.loads(regime_states_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(
                "F350: failed to load regime_states.json at %s: %s; regime tagging disabled",
                regime_states_path, exc,
            )
    else:
        log.warning(
            "F350: regime_states.json not found at %s; regime tagging disabled",
            regime_states_path,
        )

    # F351 Part (b): Optional prefetch — warm the loader for all event + universe tickers
    # before the event loop so the loop hits in-process cache instead of re-scanning
    # pickle files.  Reuses the F331 _prefetch_price_frames machinery (pacing semaphore,
    # backoff, circuit breaker) without duplicating any of it.  Non-fatal: if prefetch
    # errors occur, the event loop falls through to the normal sequential loader path.
    if universe_tickers is not None:
        all_prefetch_tickers = list(
            dict.fromkeys([ev.ticker for ev in events_list] + list(universe_tickers))
        )
        prefetch_universe = [(t, t) for t in all_prefetch_tickers]
        if prefetch_universe:
            try:
                _pf_errors = _prefetch_price_frames(prefetch_universe, loader_fn)
                if _pf_errors:
                    log.warning(
                        "run_event_study: prefetch: %d/%d tickers failed (will use sequential loader): %s",
                        len(_pf_errors), len(prefetch_universe),
                        list(_pf_errors.keys())[:10],
                    )
            except Exception as _pf_exc:
                log.warning(
                    "run_event_study: prefetch failed entirely (%s) — falling through to sequential path",
                    _pf_exc,
                )

    outcomes: list[EventOutcome] = []
    fallback_count = 0
    # ADV-02: distinct survivorship counters (no silent drops).
    events_no_price_data = 0
    events_below_floor = 0
    events_entered = 0
    # ADV-03: per-horizon peer-attrition (terminal-exit) totals for the picks.
    pick_terminal_by_h: dict[int, int] = {h: 0 for h in config.horizons}
    # COR-04: separate accumulators for universe-median peers vs SIC-peer-median peers
    # so peer_attrition.peer_terminal_exits is not double-counted.
    universe_peer_terminal_by_h: dict[int, int] = {h: 0 for h in config.horizons}
    sic_peer_terminal_by_h: dict[int, int] = {h: 0 for h in config.horizons}

    # F351 Part (a): Shared per-date return vector cache.
    # Key: (entry_date, horizon) → {symbol: (fwd_return, is_terminal)}.
    # Built ONCE per unique (entry_date, horizon) across all events; passed into
    # _compute_universe_median and _compute_peer_median to avoid re-scanning the
    # universe for each event.  Leave-one-out median for event E on date D is:
    # median(vector[D,h].values() excluding E.ticker) — a cheap dict-drop.
    # Peer median (F349) = median over peer_tickers subset of the SAME vector.
    # Methodology is byte-identical to prior per-exclude-ticker caching:
    #   same floor gate, same Open-anchor, same terminal-exit semantics.
    _return_vector_cache: dict[tuple, dict[str, tuple[Optional[float], bool]]] = {}

    # ADV-04 (retained for peer cache): cache key IS (entry_date, horizon, "peers", ticker).
    # Peer results are still cached so same-event repeated horizon calls don't recompute.
    # Universe median results are now derived from _return_vector_cache (no separate cache
    # entry needed — the vector itself is the cache).
    _peer_result_cache: dict[tuple, tuple[Optional[float], int, int]] = {}

    for ev in events_list:
        ticker = ev.ticker
        event_ts = ev.event_ts
        if ev.is_fallback:
            fallback_count += 1

        # Ensure event_ts is UTC-aware
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)
        # ADV-01: the ET date on which the information became public.
        event_et_date = _to_et(event_ts).date()

        df = loader_fn(ticker)

        # ADV-02: missing-from-cache is a DISTINCT survivorship outcome.
        no_price_data = df is None or (hasattr(df, "empty") and df.empty)
        if no_price_data:
            fs = _FLOOR_BELOW
            entry_result = None
            events_no_price_data += 1
        else:
            entry_result = _entry_date_from_event_ts(event_ts, df, config.entry_lag_days)
            if entry_result is None:
                fs = _FLOOR_BELOW
            else:
                entry_date, entry_price, used_close_fallback = entry_result
                if used_close_fallback:
                    log.warning("%s %s: Open missing, using Close as entry price", ticker, entry_date)
                # ADV-01: floor decided at the event date (info available BEFORE
                # entry), NOT at entry_date — the entry bar's price must never
                # gate inclusion (would condition on the post-event open).
                fs = _floor_status(df, event_et_date)

        if entry_result is None:
            # Cannot resolve entry — record as excluded (counted, not dropped).
            if not no_price_data:
                events_below_floor += 1
            outcomes.append(EventOutcome(
                ticker=ticker,
                event_ts=event_ts,
                entry_date=date.min,
                entry_price=float("nan"),
                payload=ev.payload,
                split="out_of_range",
                fwd_return_pct={h: None for h in config.horizons},
                fwd_excess_pct={h: None for h in config.horizons},
                floor_status=fs,
                universe_n={h: 0 for h in config.horizons},
                no_price_data=no_price_data,
                is_fallback=ev.is_fallback,
            ))
            continue

        entry_date, entry_price, used_close_fallback = entry_result

        if fs != _FLOOR_OK:
            events_below_floor += 1
        else:
            events_entered += 1

        # PY-03: split is a clean two-way partition on entry_date.
        split = "explore" if entry_date <= config.explore_cutoff else "confirm"

        # F350: Tag event with regime state at entry_date (ET, ADV-01 point-in-time).
        # None when regime_states.json missing or entry_date outside its date range.
        regime_state: Optional[str] = None
        if regime_states is not None:
            regime_state = _regime_state_for_as_of(entry_date, regime_states)

        # Forward returns (Open-anchored, Fork A) with ADV-03 terminal-exit.
        if fs == _FLOOR_OK and df is not None:
            fwd_return_pct = {}
            for h in config.horizons:
                r, was_terminal = _forward_return_terminal(
                    df, entry_date, entry_price, h, direction="long",
                )
                fwd_return_pct[h] = r
                if was_terminal and r is not None:
                    pick_terminal_by_h[h] += 1
            # Apply cost_fn if provided
            if config.cost_fn is not None:
                cost = config.cost_fn(ev, entry_price)
                fwd_return_pct = {
                    h: (v - cost if v is not None else None)
                    for h, v in fwd_return_pct.items()
                }
        else:
            fwd_return_pct = {h: None for h in config.horizons}

        # Universe median excess
        fwd_excess_pct: dict[int, Optional[float]] = {}
        universe_n: dict[int, int] = {}

        if universe_tickers is not None and fs == _FLOOR_OK:
            for h in config.horizons:
                # F351: _compute_universe_median now builds the shared return vector
                # once per (entry_date, horizon) and caches it in _return_vector_cache.
                # Subsequent calls for the same date with a different ticker drop one
                # key from the vector and take the median — no extra pickle loads.
                med, n_univ, term_peers = _compute_universe_median(
                    entry_date, h, ticker, loader_fn, universe_tickers,
                    _vector_cache=_return_vector_cache,
                )
                universe_n[h] = n_univ
                universe_peer_terminal_by_h[h] += term_peers  # COR-04: separate accumulator
                r = fwd_return_pct.get(h)
                if r is not None and med is not None:
                    fwd_excess_pct[h] = r - med
                else:
                    fwd_excess_pct[h] = None
        else:
            for h in config.horizons:
                fwd_excess_pct[h] = None
                universe_n[h] = 0

        # F349: Sector-peer benchmark — compute peer median via SIC fallback cascade.
        # Only runs when universe_tickers supplied, event passed the floor, and SIC
        # data was loaded.  Silently skips (all None) otherwise (backward-compatible).
        fwd_peer_excess_pct: dict[int, Optional[float]] = {}
        peer_n: dict[int, int] = {}
        peer_sic: Optional[str] = ticker_to_sic.get(ticker) if ticker_to_sic else None
        peer_sic_fallback_level: Optional[str] = None

        if universe_tickers is not None and fs == _FLOOR_OK:
            if ticker_to_sic:
                peers, peer_sic, fallback = _get_peer_set_by_sic(
                    ticker, universe_tickers, ticker_to_sic, config.min_peer_count
                )
                peer_sic_fallback_level = fallback
            else:
                # DI-08: universe.json absent → no SIC data → forced universe fallback.
                # Still count it so sic_fallback_stats total == floor-ok events (probe A5).
                peers = [t for t in universe_tickers if t != ticker]
                fallback = "universe"
                peer_sic_fallback_level = fallback
            sic_fallback_counts[fallback] = sic_fallback_counts.get(fallback, 0) + 1

            for h in config.horizons:
                # F351: peer median pulls from the shared return vector (already built
                # by _compute_universe_median above for this same (entry_date, h)).
                # _peer_result_cache avoids recomputing for the same (event, horizon)
                # if this loop ever iterates the same event twice (defensive).
                peer_cache_key = (entry_date, h, "peers", ticker)
                if peer_cache_key not in _peer_result_cache:
                    _peer_result_cache[peer_cache_key] = _compute_peer_median(
                        entry_date, h, ticker, peers, loader_fn,
                        _vector_cache=_return_vector_cache,
                    )
                peer_med, n_peers, term_peers = _peer_result_cache[peer_cache_key]
                peer_n[h] = n_peers
                sic_peer_terminal_by_h[h] += term_peers  # COR-04: separate accumulator
                r = fwd_return_pct.get(h)
                if r is not None and peer_med is not None:
                    fwd_peer_excess_pct[h] = r - peer_med
                else:
                    fwd_peer_excess_pct[h] = None
        else:
            for h in config.horizons:
                fwd_peer_excess_pct[h] = None
                peer_n[h] = 0

        outcomes.append(EventOutcome(
            ticker=ticker,
            event_ts=event_ts,
            entry_date=entry_date,
            entry_price=entry_price,
            payload=ev.payload,
            split=split,
            fwd_return_pct=fwd_return_pct,
            fwd_excess_pct=fwd_excess_pct,
            floor_status=fs,
            universe_n=universe_n,
            no_price_data=False,
            is_fallback=ev.is_fallback,
            peer_sic=peer_sic,
            peer_sic_fallback_level=peer_sic_fallback_level,
            fwd_peer_excess_pct=fwd_peer_excess_pct,
            peer_n=peer_n,
            regime_state=regime_state,
        ))

    # Compute statistics (on explore slice; excess must be populated)
    stats = compute_study_stats(outcomes, config, rng=rng)

    # ADV-02: survivorship of the event stream — counted, never silent.
    no_price_frac = (events_no_price_data / events_total_raw) if events_total_raw else 0.0
    survivorship = {
        "events_total": events_total_raw,
        "events_after_dedup": len(events_list),
        "events_declustered": n_declustered,
        "events_no_price_data": events_no_price_data,
        "events_below_floor": events_below_floor,
        "events_entered": events_entered,
        "no_price_data_fraction": round(no_price_frac, 4),
        "survivorship_warning": no_price_frac > _NO_PRICE_WARN_FRACTION,
    }
    if survivorship["survivorship_warning"]:
        log.warning(
            "SURVIVORSHIP: %.1f%% of events had no price data (> %.0f%%) — explore "
            "set may be biased toward cache survivors",
            no_price_frac * 100, _NO_PRICE_WARN_FRACTION * 100,
        )

    # ADV-03: per-horizon delisting attrition (terminal-exit substitutions).
    # COR-04: universe-median and SIC-peer-median terminal exits tracked separately
    # to avoid double-counting.  peer_terminal_exits = universe path (backward-compat);
    # sic_peer_terminal_exits = SIC-peer-median path (new key, F349).
    peer_attrition = {
        h: {
            "pick_terminal_exits": pick_terminal_by_h.get(h, 0),
            "peer_terminal_exits": universe_peer_terminal_by_h.get(h, 0),
            "sic_peer_terminal_exits": sic_peer_terminal_by_h.get(h, 0),
        }
        for h in config.horizons
    }

    # ADV-05: study config hash makes reruns with shifted parameters detectable.
    cfg_sig = json.dumps({
        "horizons": list(config.horizons),
        "n_boot": config.n_boot,
        "fdr_q": config.fdr_q,
        "explore_cutoff": config.explore_cutoff.isoformat(),
        "entry_lag_days": config.entry_lag_days,
        "dedup_same_ticker": config.dedup_same_ticker,
        "dedup_window_days": config.dedup_window_days,
        "block_size_override": config.block_size_override,
    }, sort_keys=True)
    study_config_hash = hashlib.sha256(cfg_sig.encode("utf-8")).hexdigest()[:16]

    # Build meta
    now_str = datetime.now(tz=timezone.utc).isoformat()
    meta = {
        "study_name": config.study_name,
        "schema_version": _SCHEMA_VERSION,
        "created_at": now_str,
        "horizons": list(config.horizons),
        "n_events": len(outcomes),
        "n_explore": stats["n_explore"],
        "n_confirm": stats["n_confirm"],
        "mde_by_horizon": stats["mde_by_horizon"],
        "mde_raw_by_horizon": stats["mde_raw_by_horizon"],
        "fdr_report": stats["fdr_report"],
        "fdr_q": config.fdr_q,
        "n_boot": config.n_boot,
        "per_horizon": stats["per_horizon"],
        "block_size_warnings": stats.get("block_size_warnings", []),
        "acceptance_dt_fallbacks": fallback_count,
        "entry_lag_days": config.entry_lag_days,
        "same_day_entry": config.entry_lag_days == 0,  # ADV-09 contamination flag
        "explore_cutoff": config.explore_cutoff.isoformat(),
        "dedup_same_ticker": config.dedup_same_ticker,
        "dedup_window_days": config.dedup_window_days,
        "survivorship": survivorship,
        "peer_attrition": peer_attrition,
        "era_consistency": stats.get("era_consistency", {}),
        "confirm_era_breakdown": stats.get("confirm_era_breakdown", {}),
        "study_config_hash": study_config_hash,
    }
    if "non_overlapping" in stats:
        meta["non_overlapping"] = stats["non_overlapping"]

    # F349: SIC coverage + fallback stats (additive; absent when universe_tickers=None).
    if universe_tickers is not None:
        n_univ = len(universe_tickers)
        meta["sic_coverage"] = {
            "tickers_with_sic": sic_found,
            "tickers_without_sic": n_univ - sic_found,
            "coverage_pct": round((sic_found / n_univ * 100) if n_univ else 0.0, 1),
            "parse_errors": sic_parse_errors,  # DI-03: JSON decode failures in submissions cache
        }
        meta["sic_fallback_stats"] = dict(sic_fallback_counts)

        # Per-horizon peer-median excess summary (explore slice, for print_study_report).
        # COR-01: per_horizon uses int keys (stats["per_horizon"][h] where h is int).
        # Must use h (int) not str(h) — otherwise the key lookup always misses and
        # peer_median_excess_pct is never written.
        explore_outcomes = [o for o in outcomes if o.split == "explore"]
        per_h = meta.get("per_horizon", {})
        for h in config.horizons:
            peer_vals = [
                o.fwd_peer_excess_pct.get(h)
                for o in explore_outcomes
                if o.fwd_peer_excess_pct.get(h) is not None
            ]
            if h in per_h:
                per_h[h]["peer_median_excess_pct"] = (
                    round(float(np.median(peer_vals)), 4) if peer_vals else None
                )
    else:
        meta["sic_coverage"] = None
        meta["sic_fallback_stats"] = None

    # F350: Regime breakdown (additive; empty when regime_states.json absent).
    meta["regime_breakdown"] = stats.get("regime_breakdown", {})

    # Persist to disk
    out_dir = config.output_dir or (_STUDIES_DIR / config.study_name)
    _write_study_artifacts(outcomes, meta, out_dir)

    # ADV-05: append this run to the cross-run FDR ledger (survives runs).
    _append_fdr_ledger(config, stats, study_config_hash, now_str)

    # Print MDE report
    print_study_report(meta)

    return outcomes, meta


def _append_fdr_ledger(
    config: EventStudyConfig,
    stats: dict,
    study_config_hash: str,
    created_at: str,
) -> None:
    """ADV-05: append-only cross-run FDR ledger at _FDR_LEDGER_PATH.

    A ledger that resets per-run is not a ledger.  Each entry records the
    multiplicity context for one run — study, hash, q, n_boot, horizons, per-
    horizon block sizes and bootstrap p-values, and the rejection set — so that
    optional stopping or parameter shopping across reruns is auditable.

    Honors config.fdr_ledger_path when set (tests redirect to a tmp path).
    """
    ledger_path = config.fdr_ledger_path or _FDR_LEDGER_PATH
    try:
        entry = {
            "study_name": config.study_name,
            "created_at": created_at,
            "study_config_hash": study_config_hash,
            "fdr_q": config.fdr_q,
            "n_boot": config.n_boot,
            "horizons": list(config.horizons),
            "per_horizon": {
                str(h): {
                    "p_bootstrap": stats["per_horizon"][h].get("p_bootstrap"),
                    "block_size_used": stats["per_horizon"][h].get("block_size_used"),
                    "block_size_source": stats["per_horizon"][h].get("block_size_source"),
                    "n_explore_valid": stats["per_horizon"][h].get("n_explore_valid"),
                }
                for h in config.horizons
            },
            "fdr_report": {
                k: {"p_raw": v["p_raw"], "p_adj": v["p_adj"], "rejected": v["rejected"]}
                for k, v in stats.get("fdr_report", {}).items()
            },
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_rows: list[dict] = []
        if ledger_path.exists():
            try:
                ledger_rows = json.loads(ledger_path.read_text(encoding="utf-8"))
                if not isinstance(ledger_rows, list):
                    ledger_rows = []
            except Exception:
                ledger_rows = []
        ledger_rows.append(entry)
        _atomic_write(ledger_path, json.dumps(ledger_rows, indent=2, default=str))
    except Exception as exc:
        log.warning("FDR ledger append failed: %s", exc)


def _write_study_artifacts(
    outcomes: list[EventOutcome],
    meta: dict,
    out_dir: Path,
) -> None:
    """Atomically write events.ndjson + meta.json to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # events.ndjson
    ndjson_path = out_dir / "events.ndjson"
    rows: list[str] = []
    for o in outcomes:
        row = {
            "ticker": o.ticker,
            "event_ts": o.event_ts.isoformat(),
            "entry_date": o.entry_date.isoformat() if o.entry_date != date.min else None,
            "entry_price": o.entry_price if math.isfinite(o.entry_price) else None,
            "payload": o.payload,
            "split": o.split,
            "fwd_return_pct": {str(k): v for k, v in o.fwd_return_pct.items()},
            "fwd_excess_pct": {str(k): v for k, v in o.fwd_excess_pct.items()},
            "floor_status": o.floor_status,
            "universe_n": {str(k): v for k, v in o.universe_n.items()},
            "fwd_peer_excess_pct": {str(k): v for k, v in o.fwd_peer_excess_pct.items()},
            "peer_n": {str(k): v for k, v in o.peer_n.items()},
            "peer_sic": o.peer_sic,
            "peer_sic_fallback_level": o.peer_sic_fallback_level,
            "no_price_data": o.no_price_data,
            "is_fallback": o.is_fallback,
            "regime_state": o.regime_state,
        }
        rows.append(json.dumps(row))

    _atomic_write(ndjson_path, "\n".join(rows) + ("\n" if rows else ""))

    # meta.json
    meta_path = out_dir / "meta.json"
    _atomic_write(meta_path, json.dumps(meta, indent=2, default=str))


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (fsync + os.replace).

    DI-06: delegates to fileutil.atomic_write_text (house standard, fsync before
    replace) when available.  Falls back to local tmp+replace when import fails.
    """
    if _HAS_FILEUTIL:
        _fileutil_atomic_write_text(path, content)
        return
    # Local fallback (no fsync — acceptable for test/dev environments).
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    except Exception as exc:
        log.warning("atomic write failed for %s: %s", path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# iter_form4_events helper
# ---------------------------------------------------------------------------

def _pad_cik(cik: str | int) -> str:
    """Zero-pad CIK to 10 digits."""
    return str(int(cik)).zfill(10)


def _build_ticker_cik_map(subs_dir: Path) -> dict[str, str]:
    """Build {ticker: padded_cik} from all submission files in subs_dir."""
    mapping: dict[str, str] = {}
    if not subs_dir.exists():
        return mapping
    for f in subs_dir.iterdir():
        if not f.name.endswith(".json"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            cik_str = f.stem  # filename without .json = padded CIK
            for ticker in data.get("tickers", []):
                if ticker:
                    mapping[ticker] = cik_str
        except Exception:
            pass
    return mapping


def iter_form4_events(
    cik_list: Optional[list[str]] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    ticker_list: Optional[list[str]] = None,
    subs_dir: Optional[Path] = None,
    fallback_count_ref: Optional[list[int]] = None,
) -> Iterator[EventRecord]:
    """Yield EventRecord for each Form 4 / 4/A filing in [start, end].

    Parameters
    ----------
    cik_list : CIKs to include (padded or unpadded). If None and ticker_list
        given, CIKs are resolved from submissions directory.
    start, end : inclusive date range filter on acceptanceDateTime date.
    ticker_list : alternative to cik_list; resolved via submissions dir.
    subs_dir : path to submissions JSON directory (default: _SUBMISSIONS_DIR).
    fallback_count_ref : if a list[int] is passed, fallback count is incremented.

    Payload includes: {form_type, accession, filing_date}.
    event_ts is always UTC-aware.
    """
    _subs_dir = subs_dir or _SUBMISSIONS_DIR
    if not _subs_dir.exists():
        log.warning("iter_form4_events: submissions dir not found: %s", _subs_dir)
        return

    # Resolve CIK list
    effective_ciks: list[str] = []
    if cik_list:
        effective_ciks = [_pad_cik(c) for c in cik_list]
    elif ticker_list:
        t2c = _build_ticker_cik_map(_subs_dir)
        for t in ticker_list:
            c = t2c.get(t)
            if c:
                effective_ciks.append(c)
            else:
                log.warning("iter_form4_events: no CIK found for ticker %s", t)
    else:
        # All available CIKs
        effective_ciks = [
            f.stem for f in _subs_dir.iterdir() if f.name.endswith(".json")
        ]

    for cik_padded in effective_ciks:
        subs_path = _subs_dir / f"{cik_padded}.json"
        if not subs_path.exists():
            continue
        try:
            data = json.loads(subs_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("iter_form4_events: failed to read %s: %s", subs_path, exc)
            continue

        tickers = data.get("tickers", [])
        ticker = tickers[0] if tickers else cik_padded

        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        acceptance_dts = filings.get("acceptanceDateTime", [])
        filing_dates = filings.get("filingDate", [])

        for form, accession, adt_str, fd_str in zip(forms, accessions, acceptance_dts, filing_dates):
            if form not in ("4", "4/A"):
                continue

            # Parse event_ts
            is_fallback = False
            event_ts = _parse_acceptance_dt(adt_str) if adt_str else None
            if event_ts is None:
                # Fallback: filingDate + 16:01 ET
                event_ts = _filing_date_fallback_dt(fd_str)
                if event_ts is None:
                    continue
                is_fallback = True
                log.debug("acceptance_dt_fallback for %s %s", ticker, accession)
                if fallback_count_ref is not None:
                    fallback_count_ref[0] = fallback_count_ref[0] + 1

            # Date range filter on acceptanceDateTime date (ET)
            et_dt = _to_et(event_ts)
            event_date = et_dt.date()
            if start and event_date < start:
                continue
            if end and event_date > end:
                continue

            yield EventRecord(
                ticker=ticker,
                event_ts=event_ts,
                payload={
                    "form_type": form,
                    "accession": accession,
                    "filing_date": fd_str,
                },
                is_fallback=is_fallback,
            )
