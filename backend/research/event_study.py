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

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_STUDIES_DIR = _BASE_DIR / "backend" / "data" / "turnaround" / "event_studies"
_SUBMISSIONS_DIR = _BASE_DIR / "backend" / "data" / "turnaround" / "edgar_cache" / "submissions"
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


def _compute_universe_median(
    entry_date: date,
    horizon: int,
    exclude_ticker: str,
    loader_fn: Callable[[str], Optional[pd.DataFrame]],
    universe_tickers: list[str],
) -> tuple[Optional[float], int, int]:
    """Compute the universe-median forward return (from Open) at `horizon` for
    all floor-passing symbols alive on `entry_date`, excluding `exclude_ticker`.

    Floor inclusion is decided at the LAST close ON OR BEFORE `entry_date` (the
    caller passes a pre-entry as_of, see ADV-01) — never using the entry bar.

    ADV-03: peers whose series ends inside the horizon contribute their terminal
    value rather than being excluded.  The count of such terminal (attrited) peers
    is returned per call so survivorship attrition can be persisted in meta.

    Returns (median, count_of_valid_symbols, count_terminal_peers).
    Fork B / Option 1: same-date universe, pick excluded.
    """
    returns: list[float] = []
    terminal_peers = 0
    for sym in universe_tickers:
        if sym == exclude_ticker:
            continue
        df = loader_fn(sym)
        if df is None or df.empty:
            continue
        # ADV-01: floor decided from info available BEFORE entry.
        fs = _floor_status(df, entry_date)
        if fs != _FLOOR_OK:
            continue
        # Get entry at entry_date
        res = _first_trading_close_on_or_after(df, entry_date)
        if res is None:
            continue
        e_date, e_close = res
        if e_date != entry_date:
            continue  # Symbol doesn't trade on entry_date
        # Get Open for entry
        try:
            dates_list = _frame_dates(df)
            entry_open = None
            for i, d in enumerate(dates_list):
                if d == entry_date:
                    row = df.iloc[i]
                    if "Open" in df.columns and not pd.isna(row["Open"]) and float(row["Open"]) > 0:
                        entry_open = float(row["Open"])
                    else:
                        entry_open = float(row["Close"])
                    break
            if entry_open is None or entry_open <= 0:
                continue
        except Exception as exc:  # PY-07: log instead of silently shrinking universe
            log.warning(
                "universe_median: Open lookup failed for %s at %s: %s",
                sym, entry_date, exc,
            )
            continue
        r, was_terminal = _forward_return_terminal(df, entry_date, entry_open, horizon)
        if r is not None:
            returns.append(r)
            if was_terminal:
                terminal_peers += 1

    if not returns:
        return (None, 0, terminal_peers)
    return (float(np.median(returns)), len(returns), terminal_peers)


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

    outcomes: list[EventOutcome] = []
    fallback_count = 0
    # ADV-02: distinct survivorship counters (no silent drops).
    events_no_price_data = 0
    events_below_floor = 0
    events_entered = 0
    # ADV-03: per-horizon peer-attrition (terminal-exit) totals for the picks.
    pick_terminal_by_h: dict[int, int] = {h: 0 for h in config.horizons}
    peer_terminal_by_h: dict[int, int] = {h: 0 for h in config.horizons}

    # ADV-04: cache key IS (entry_date, horizon, exclude_ticker).  Self-exclusion
    # is part of the key, so two different picks sharing an entry_date never alias
    # each other's median.  Do NOT narrow this key to (entry_date, horizon) — that
    # would reuse a median that excluded the wrong ticker.  Value carries the
    # per-call terminal-peer count for attrition bookkeeping.
    _median_cache: dict[tuple, tuple[Optional[float], int, int]] = {}

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
                cache_key = (entry_date, h, ticker)
                if cache_key not in _median_cache:
                    _median_cache[cache_key] = _compute_universe_median(
                        entry_date, h, ticker, loader_fn, universe_tickers
                    )
                med, n_univ, term_peers = _median_cache[cache_key]
                universe_n[h] = n_univ
                peer_terminal_by_h[h] += term_peers
                r = fwd_return_pct.get(h)
                if r is not None and med is not None:
                    fwd_excess_pct[h] = r - med
                else:
                    fwd_excess_pct[h] = None
        else:
            for h in config.horizons:
                fwd_excess_pct[h] = None
                universe_n[h] = 0

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
    peer_attrition = {
        h: {
            "pick_terminal_exits": pick_terminal_by_h.get(h, 0),
            "peer_terminal_exits": peer_terminal_by_h.get(h, 0),
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
            "no_price_data": o.no_price_data,
            "is_fallback": o.is_fallback,
        }
        rows.append(json.dumps(row))

    _atomic_write(ndjson_path, "\n".join(rows) + ("\n" if rows else ""))

    # meta.json
    meta_path = out_dir / "meta.json"
    _atomic_write(meta_path, json.dumps(meta, indent=2, default=str))


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (tmp + os.replace)."""
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
