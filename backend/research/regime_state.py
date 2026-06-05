"""Point-in-time regime classifier (Unit 4 / REGIME-TEST).

Computes a daily regime state for each trading date from 2015-01-01 onward,
using three price-only features:

  F1 — Index trend: SPY position + slope vs 200-day SMA (221-bar max lookback)
  F2 — Realized-vol band: 21-day annualized stdev of SPY log returns (22-bar)
  F3 — Breadth: % of universe-v2 names above their own 200-day SMA

State space (charter §2, evaluation order S4 → S3 → S1 → else S2):
  STRESS    — vol=HIGH AND pos=below AND breadth=WEAK
  RISK_OFF  — vol=HIGH AND NOT (pos=above AND slope=rising)
  RISK_ON   — pos=above AND slope=rising AND vol∈{LOW,MID} AND breadth∈{NEUTRAL,STRONG}
  NEUTRAL   — everything else

Feature thresholds (FROZEN per charter §1):
  F2:  LOW < 12%  ≤ MID < 20%  ≤ HIGH
  F3:  WEAK < 0.40  ≤ NEUTRAL < 0.60  ≤ STRONG

Warmup (charter §1):
  F1: first 221 bars → no F1
  F2: first 22 bars  → no F2
  F3: breadth absent if < 30 constituents have ≥ 200 bars of history

Any date missing a required feature → state = absent (counted reason, not error).

Output: backend/data/turnaround/regime_states.json
  schema_version = 1
  generation provenance includes charter sha256
  dates sorted ascending (deterministic)

Usage:
    python3 backend/research/regime_state.py \\
        --start 2015-01-01 --end 2024-12-31

    # Test override (smaller universe + custom output):
    python3 backend/research/regime_state.py \\
        --start 2015-01-01 --end 2015-06-30 \\
        --universe-limit 50 \\
        --output /tmp/regime_states_test.json
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent
_DEFAULT_OUTPUT = _REPO_ROOT / "backend" / "data" / "turnaround" / "regime_states.json"

# Charter provenance (frozen sha256 of .run/REGIME-TEST/charter.md)
_CHARTER_SHA256 = "d5da66aa48f457ab6d7a721d46070afc01d820fd1a3198e36c37f9852c9319e1"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("regime_state")

# ---------------------------------------------------------------------------
# Charter constants (§1 — FROZEN)
# ---------------------------------------------------------------------------

# F1 lookback: 200 bars (SMA level) + 21 bars (slope step) = 221 bars max
_F1_SMA_PERIOD = 200
_F1_SLOPE_STEP = 21
_F1_WARMUP_BARS = _F1_SMA_PERIOD + _F1_SLOPE_STEP  # 221

# F2 lookback: 22 closes to produce 21 log returns
_F2_RETURN_PERIOD = 21
_F2_WARMUP_BARS = _F2_RETURN_PERIOD + 1  # 22

# F2 vol bands (annualized %, FIXED)
_F2_LOW_THRESHOLD = 12.0
_F2_HIGH_THRESHOLD = 20.0

# F3 breadth bands (FIXED)
_F3_WEAK_THRESHOLD = 0.40
_F3_STRONG_THRESHOLD = 0.60
_F3_MIN_CONSTITUENTS = 30  # breadth absent if < 30 have ≥ 200 bars

# F3 constituent SMA period (same as F1, 200 bars per charter §1)
_F3_SMA_PERIOD = 200

# Annualization factor for realized vol
_SQRT_252 = math.sqrt(252.0)


# ---------------------------------------------------------------------------
# Feature helpers (pure, no I/O)
# ---------------------------------------------------------------------------

def compute_f1(spy_closes: pd.Series, bar_index: int) -> Optional[tuple[str, str]]:
    """Return (pos, slope) for bar at bar_index, or None if in warmup.

    pos   ∈ {'above', 'below'}
    slope ∈ {'rising', 'falling'}

    charter §1 F1: warmup = first 221 bars.
    """
    # Need bar_index >= _F1_WARMUP_BARS - 1 (0-indexed: need 221 bars for SMA, then
    # a second SMA 21 bars earlier).
    if bar_index < _F1_WARMUP_BARS - 1:
        return None
    # sma200(t) = mean of bars [i-199 .. i]  (200 bars)
    sma_now = spy_closes.iloc[bar_index - _F1_SMA_PERIOD + 1 : bar_index + 1].mean()
    # sma200(t-21)
    sma_prev = spy_closes.iloc[bar_index - _F1_SMA_PERIOD + 1 - _F1_SLOPE_STEP :
                               bar_index + 1 - _F1_SLOPE_STEP].mean()
    close_now = spy_closes.iloc[bar_index]
    pos = "above" if close_now >= sma_now else "below"
    slope = "rising" if sma_now >= sma_prev else "falling"
    return pos, slope


def compute_f2(spy_closes: pd.Series, bar_index: int) -> Optional[str]:
    """Return vol band string for bar at bar_index, or None if in warmup.

    Returns 'LOW', 'MID', or 'HIGH'.
    charter §1 F2: warmup = first 22 bars.
    """
    if bar_index < _F2_WARMUP_BARS - 1:
        return None
    # 21 log returns from closes[i-21 .. i] (22 points)
    window = spy_closes.iloc[bar_index - _F2_RETURN_PERIOD : bar_index + 1]
    log_rets = (window / window.shift(1)).apply(lambda x: math.log(x) if x > 0 else float("nan")).dropna()
    if len(log_rets) < _F2_RETURN_PERIOD:
        return None
    rv21 = float(log_rets.std(ddof=1)) * _SQRT_252 * 100.0  # annualized %
    if rv21 < _F2_LOW_THRESHOLD:
        return "LOW"
    elif rv21 < _F2_HIGH_THRESHOLD:
        return "MID"
    else:
        return "HIGH"


def compute_f3(
    constituent_frames: dict[str, pd.Series],
    bar_date: date,
) -> Optional[str]:
    """Return breadth band for bar_date, or None if breadth is absent.

    Each value in constituent_frames is a pd.Series of Close prices indexed
    by date (datetime64 or date).  A constituent with < 200 bars of history
    at bar_date is excluded from both numerator and denominator.

    charter §1 F3: breadth absent if < 30 constituents have ≥ 200 bars.
    Returns 'WEAK', 'NEUTRAL', or 'STRONG'.
    """
    bar_ts = pd.Timestamp(bar_date)
    above = 0
    total = 0
    for ticker, closes in constituent_frames.items():
        # yfinance delivers tz-aware (America/New_York) indices; bar_ts is naive.
        # Comparing the two RAISES — the 2026-06-05 all-WARMUP bug, where a
        # blanket `except: continue` here silently skipped every constituent.
        # Normalize instead of catching: real errors must propagate loudly.
        idx = closes.index
        if getattr(idx, "tz", None) is not None:
            closes = pd.Series(closes.values, index=idx.tz_localize(None))
        avail = closes.loc[closes.index <= bar_ts]
        n = len(avail)
        if n < _F3_SMA_PERIOD:
            continue  # excluded from both numerator and denominator
        sma200 = avail.iloc[-_F3_SMA_PERIOD:].mean()
        last_close = float(avail.iloc[-1])
        total += 1
        if last_close >= sma200:
            above += 1

    if total < _F3_MIN_CONSTITUENTS:
        return None  # breadth absent

    breadth = above / total
    if breadth < _F3_WEAK_THRESHOLD:
        return "WEAK"
    elif breadth < _F3_STRONG_THRESHOLD:
        return "NEUTRAL"
    else:
        return "STRONG"


def classify_state(
    pos: Optional[str],
    slope: Optional[str],
    vol: Optional[str],
    breadth: Optional[str],
) -> dict:
    """Apply charter §2 state rules. Returns dict with state and reason fields.

    Evaluation order: S4 → S3 → S1 → else S2.
    A WARMUP/absent date returns state='WARMUP' with reason.
    """
    # Check for warmup/missing features
    missing = []
    if pos is None or slope is None:
        missing.append("F1_warmup")
    if vol is None:
        missing.append("F2_warmup")
    if breadth is None:
        missing.append("F3_absent")

    if missing:
        return {"state": "WARMUP", "reason": ",".join(missing)}

    # S4: vol=HIGH AND pos=below AND breadth=WEAK
    if vol == "HIGH" and pos == "below" and breadth == "WEAK":
        return {"state": "STRESS", "reason": ""}

    # S3: vol=HIGH AND NOT (pos=above AND slope=rising)
    if vol == "HIGH" and not (pos == "above" and slope == "rising"):
        return {"state": "RISK_OFF", "reason": ""}

    # S1: pos=above AND slope=rising AND vol∈{LOW,MID} AND breadth∈{NEUTRAL,STRONG}
    if (pos == "above" and slope == "rising"
            and vol in ("LOW", "MID")
            and breadth in ("NEUTRAL", "STRONG")):
        return {"state": "RISK_ON", "reason": ""}

    # S2: everything else
    return {"state": "NEUTRAL", "reason": ""}


# ---------------------------------------------------------------------------
# Loader helpers (reuse PriceFrameCache + _make_memoized_loader from turnaround_validation)
# ---------------------------------------------------------------------------

def _make_regime_loader(
    fetch_start: str,
    fetch_end: str,
    data_source: str = "yahoo",
    price_cache=None,
) -> Callable[[str], Optional[pd.DataFrame]]:
    """Build a simple memoized loader for regime_state.py.

    Uses the same PriceFrameCache (F332) and _fetch function as
    turnaround_validation._make_memoized_loader.  The loader is NOT
    the full memoized loader from that module (which takes year/lookback
    params and computes the span internally) — here we pass the span
    directly, covering the full regime-build window.
    """
    sys.path.insert(0, str(_REPO_ROOT / "backend"))
    from turnaround_validation import PriceFrameCache, _price_frame_cache  # noqa: F401
    from shared import _fetch  # noqa: runtime import

    _disk_cache = price_cache if price_cache is not None else _price_frame_cache
    _mem_cache: dict[str, Optional[pd.DataFrame]] = {}

    def _loader(ticker: str) -> Optional[pd.DataFrame]:
        if ticker in _mem_cache:
            return _mem_cache[ticker]
        # Layer 2: disk cache
        hit = _disk_cache.load(ticker, fetch_start, fetch_end, data_source)
        if hit is not None:
            _mem_cache[ticker] = hit
            return hit
        # Layer 3: network fetch
        try:
            df = _fetch(ticker, fetch_start, fetch_end, "1d", data_source)
            result = df if df is not None and not df.empty else None
        except Exception as exc:
            logger.warning("regime_loader: failed to fetch %s: %s", ticker, exc)
            result = None
        _mem_cache[ticker] = result
        if result is not None:
            _disk_cache.store(ticker, fetch_start, fetch_end, result, data_source)
        return result

    return _loader


# ---------------------------------------------------------------------------
# Atomic JSON write (inlined from fileutil.py pattern)
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, obj: object) -> None:
    """Write obj as JSON to path atomically (tmp + rename, same directory)."""
    content = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)
    dir_ = str(path.parent)
    os.makedirs(dir_, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        mode="w", delete=False, dir=dir_, suffix=".tmp", encoding="utf-8"
    )
    try:
        fd.write(content)
        fd.flush()
        os.fsync(fd.fileno())
        try:
            fd.close()
        except Exception:
            pass
        os.replace(fd.name, str(path))
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


# ---------------------------------------------------------------------------
# Core build function
# ---------------------------------------------------------------------------

def build_regime_states(
    start_date: str,
    end_date: str,
    universe_limit: Optional[int] = None,
    output_path: Optional[Path] = None,
    bars_loader: Optional[Callable[[str], Optional[pd.DataFrame]]] = None,
    data_source: str = "yahoo",
    spy_frame: Optional[pd.DataFrame] = None,
    constituent_frames: Optional[dict[str, pd.DataFrame]] = None,
) -> dict:
    """Compute daily regime states for [start_date, end_date] and write to JSON.

    Parameters
    ----------
    start_date, end_date:
        ISO date strings defining the output window.  Bars outside this window
        are fetched for warmup but NOT emitted in the output.
    universe_limit:
        If set, truncate the universe-v2 constituent list to this count (for
        tests / fast smoke-runs).
    output_path:
        Override the default output path.  If None, writes to
        backend/data/turnaround/regime_states.json.
    bars_loader:
        If provided, overrides the default network + disk-cache loader.  For
        tests: inject a lambda that returns synthetic DataFrames.
    data_source:
        Provider key passed to PriceFrameCache (default 'yahoo').
    spy_frame:
        If provided, skip fetching SPY and use this frame directly.
    constituent_frames:
        If provided, skip fetching constituents and use these frames.

    Returns
    -------
    The dict that was written to JSON (for test assertions).
    """
    out_path = output_path if output_path is not None else _DEFAULT_OUTPUT

    # -----------------------------------------------------------------------
    # Determine fetch span: needs warmup before start_date
    # F1 needs 221 bars; add ~400 trading days of pad (≈1.6 years) so we have
    # F1+F2+F3 features available on the very first output date.
    # Charter §6: fetch from 2013-06-01 for the full 2015-2024 run.
    # For arbitrary start_date, use the same universal span or compute from it.
    # We fetch from a universal start (2013-06-01) through end_date + buffer.
    # -----------------------------------------------------------------------
    fetch_start = "2013-06-01"
    # Add 6 months of forward buffer past the end date for completeness
    _end_dt = date.fromisoformat(end_date)
    fetch_end = str(_end_dt.replace(year=_end_dt.year + 1))

    # -----------------------------------------------------------------------
    # Build loader (lazy: only created if not injected)
    # -----------------------------------------------------------------------
    if bars_loader is None:
        bars_loader = _make_regime_loader(fetch_start, fetch_end, data_source)

    # -----------------------------------------------------------------------
    # Fetch SPY
    # -----------------------------------------------------------------------
    if spy_frame is None:
        logger.info("Fetching SPY bars …")
        spy_frame = bars_loader("SPY")
        if spy_frame is None or spy_frame.empty:
            raise RuntimeError("Failed to fetch SPY price frame — cannot build regime states")

    # Normalize SPY frame: ensure a 'Close' column and a date-only index
    spy_closes = _extract_closes(spy_frame)
    if spy_closes is None or spy_closes.empty:
        raise RuntimeError("SPY frame has no usable 'Close' column")

    # -----------------------------------------------------------------------
    # Build universe-v2 constituent list
    # -----------------------------------------------------------------------
    if constituent_frames is None:
        logger.info("Building universe-v2 constituent list …")
        tickers = _get_universe_v2_tickers(universe_limit)
        logger.info("Universe-v2: %d tickers to fetch for breadth", len(tickers))
        constituent_frames = _fetch_constituent_frames(tickers, bars_loader, fetch_start, fetch_end)
    else:
        logger.info("Using injected constituent_frames (%d tickers)", len(constituent_frames))

    # Build per-constituent close Series (date-indexed, sorted ascending)
    constituent_closes: dict[str, pd.Series] = {
        ticker: _extract_closes(df)
        for ticker, df in constituent_frames.items()
        if df is not None and not df.empty
    }
    # Remove empty series
    constituent_closes = {t: s for t, s in constituent_closes.items() if s is not None and not s.empty}
    logger.info("Constituent close series available: %d", len(constituent_closes))

    # -----------------------------------------------------------------------
    # Build daily states for [start_date, end_date]
    # -----------------------------------------------------------------------
    start_dt = date.fromisoformat(start_date)
    end_dt = date.fromisoformat(end_date)

    # Ensure spy_closes is sorted
    spy_closes = spy_closes.sort_index()

    states: dict[str, dict] = {}
    counts: dict[str, int] = {
        "RISK_ON": 0, "NEUTRAL": 0, "RISK_OFF": 0, "STRESS": 0,
        "WARMUP": 0, "absent": 0,
    }

    spy_idx = spy_closes.index  # DatetimeIndex, sorted

    for bar_index, bar_ts in enumerate(spy_idx):
        bar_date = bar_ts.date()
        if bar_date < start_dt or bar_date > end_dt:
            continue

        # F1
        f1 = compute_f1(spy_closes, bar_index)
        pos, slope = (f1 if f1 is not None else (None, None))

        # F2
        vol = compute_f2(spy_closes, bar_index)

        # F3
        breadth = compute_f3(constituent_closes, bar_date)

        result = classify_state(pos, slope, vol, breadth)
        state_val = result["state"]
        reason = result.get("reason", "")

        entry: dict = {"state": state_val}
        if reason:
            entry["reason"] = reason

        states[str(bar_date)] = entry

        if state_val in counts:
            counts[state_val] += 1
        else:
            counts["absent"] += 1

    logger.info(
        "State counts: RISK_ON=%d NEUTRAL=%d RISK_OFF=%d STRESS=%d WARMUP=%d absent=%d",
        counts["RISK_ON"], counts["NEUTRAL"], counts["RISK_OFF"], counts["STRESS"],
        counts["WARMUP"], counts["absent"],
    )

    # -----------------------------------------------------------------------
    # Build output artifact (schema_version=1, sorted by date)
    # -----------------------------------------------------------------------
    sorted_states = dict(sorted(states.items()))

    artifact = {
        "schema_version": 1,
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "start_date": start_date,
            "end_date": end_date,
            "n_dates": len(sorted_states),
            "state_counts": counts,
            "charter_sha256": _CHARTER_SHA256,
            "data_source": data_source,
            "universe_limit": universe_limit,
            "fetch_start": fetch_start,
            "fetch_end": fetch_end,
            "features": {
                "F1": {
                    "source": "SPY",
                    "sma_period": _F1_SMA_PERIOD,
                    "slope_step": _F1_SLOPE_STEP,
                    "warmup_bars": _F1_WARMUP_BARS,
                },
                "F2": {
                    "source": "SPY",
                    "return_period": _F2_RETURN_PERIOD,
                    "warmup_bars": _F2_WARMUP_BARS,
                    "low_threshold_pct": _F2_LOW_THRESHOLD,
                    "high_threshold_pct": _F2_HIGH_THRESHOLD,
                },
                "F3": {
                    "source": "universe_v2",
                    "sma_period": _F3_SMA_PERIOD,
                    "min_constituents": _F3_MIN_CONSTITUENTS,
                    "weak_threshold": _F3_WEAK_THRESHOLD,
                    "strong_threshold": _F3_STRONG_THRESHOLD,
                },
            },
            "state_rules": (
                "S4=STRESS(vol=HIGH,pos=below,breadth=WEAK); "
                "S3=RISK_OFF(vol=HIGH,NOT(pos=above,slope=rising)); "
                "S1=RISK_ON(pos=above,slope=rising,vol∈{LOW,MID},breadth∈{NEUTRAL,STRONG}); "
                "else=NEUTRAL"
            ),
        },
        "states": sorted_states,
    }

    logger.info("Writing regime_states.json to %s …", out_path)
    _atomic_write_json(out_path, artifact)
    logger.info("Done. %d dates written.", len(sorted_states))

    return artifact


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def _get_universe_v2_tickers(limit: Optional[int] = None) -> list[str]:
    """Return universe-v2 ticker list (same build_universe + UNIVERSE_V2 as validation).

    This is the same list the breadth feature is computed over (charter §1 F3).
    """
    sys.path.insert(0, str(_REPO_ROOT / "backend"))
    try:
        from turnaround import build_universe, UNIVERSE_V2
        import edgar
    except ImportError as exc:
        raise ImportError(f"Cannot import turnaround/edgar: {exc}") from exc

    ticker_cik_map = edgar.fetch_universe()
    pairs = build_universe(ticker_cik_map, params=None)

    # UNIVERSE_V2 floors: applied as post-filter (build_universe itself doesn't
    # apply min_price/min_avg_volume — those are checked per-bar in run_filter).
    # For breadth, we use the ticker list returned by build_universe (F319 hygiene
    # applied) which is what the charter intends: the universe-v2 constituent set
    # is the one from build_universe(**UNIVERSE_V2) — meaning the HYGIENE filter is
    # applied but the price/volume floor is applied dynamically at each as_of.
    # Charter §1 F3: "A name with < 200 bars of history at t is excluded from both
    # numerator and denominator (not counted as below)."
    tickers = [t for t, _ in pairs]
    if limit is not None:
        tickers = tickers[:limit]
    return tickers


def _fetch_constituent_frames(
    tickers: list[str],
    loader: Callable[[str], Optional[pd.DataFrame]],
    fetch_start: str,
    fetch_end: str,
) -> dict[str, Optional[pd.DataFrame]]:
    """Fetch price frames for all tickers via the memoized loader.

    Logs progress every 100 tickers.  Returns a dict of {ticker: frame}.
    Missing/failed tickers map to None.
    """
    frames: dict[str, Optional[pd.DataFrame]] = {}
    total = len(tickers)
    for i, ticker in enumerate(tickers):
        if i > 0 and i % 100 == 0:
            logger.info("Fetching constituents: %d/%d …", i, total)
        frames[ticker] = loader(ticker)
    logger.info("Fetched %d/%d constituent frames (non-None).",
                sum(1 for v in frames.values() if v is not None), total)
    return frames


def _extract_closes(df: pd.DataFrame) -> Optional[pd.Series]:
    """Return a date-indexed Close series from a DataFrame.

    Handles both MultiIndex columns (yfinance v0.2+) and flat columns.
    Index is normalized to date-only (no time component).
    """
    if df is None or df.empty:
        return None

    # Handle MultiIndex columns (yfinance returns (field, ticker) tuples)
    if isinstance(df.columns, pd.MultiIndex):
        close_cols = [c for c in df.columns if c[0] == "Close"]
        if not close_cols:
            return None
        series = df[close_cols[0]].dropna()
    elif "Close" in df.columns:
        series = df["Close"].dropna()
    else:
        return None

    if series.empty:
        return None

    # Strip timezone FIRST (yfinance indices are tz-aware America/New_York;
    # naive-vs-aware comparisons raise — 2026-06-05 all-WARMUP regression)
    if getattr(series.index, "tz", None) is not None:
        series.index = series.index.tz_localize(None)
    # Normalize index to date-only
    if hasattr(series.index, "normalize"):
        series.index = series.index.normalize()
    # Cast to DatetimeIndex if not already
    try:
        series.index = pd.to_datetime(series.index)
    except Exception:
        pass

    return series.sort_index()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Build daily regime states artifact.")
    parser.add_argument("--start", default="2015-01-01", help="Start date (ISO, inclusive)")
    parser.add_argument("--end", default="2024-12-31", help="End date (ISO, inclusive)")
    parser.add_argument("--universe-limit", type=int, default=None,
                        help="Truncate universe-v2 to N tickers (for testing)")
    parser.add_argument("--output", default=None, help="Override output path")
    args = parser.parse_args()

    out = Path(args.output) if args.output else None

    logger.info(
        "Starting regime state build: start=%s end=%s universe_limit=%s output=%s",
        args.start, args.end, args.universe_limit, out or _DEFAULT_OUTPUT,
    )

    build_regime_states(
        start_date=args.start,
        end_date=args.end,
        universe_limit=args.universe_limit,
        output_path=out,
    )


if __name__ == "__main__":
    main()
