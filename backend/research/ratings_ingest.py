"""F401 — Analyst Up/Downgrades Ingest (yfinance rating actions).

Fetches per-ticker analyst rating actions from yfinance `.upgrades_downgrades`
and produces two tidy, scan-ready parquet panels:

  1. **Event panel** (`ratings_events.parquet`)
     One row per (ticker, date, firm) action.
     Columns: ticker, date, firm, action, from_grade, to_grade, grade_delta

  2. **Aggregation panel** (`ratings_agg.parquet`)
     One row per (ticker, date) joining day.
     Columns: ticker, date, net_upgrades_21d, net_upgrades_63d, days_since_last_action

PIT field: the action `date` (GradeDate from yfinance — the dissemination
timestamp, NOT an earnings-period end).

Metadata sidecar (`ratings_meta.json`):
    source, fetch_vintage, survivorship, coverage_start, coverage_end,
    pit_field, n_rows, n_tickers

Usage:
    python3 backend/research/ratings_ingest.py --tickers AAPL MSFT TSLA ...
    python3 backend/research/ratings_ingest.py  # full run (reads tickers from CLI)

Standing constraints (Phase 0 spec, 2026-06-09):
    - Survivorship: "survivors-only" (yfinance serves only currently-listed tickers)
    - Concurrency rule: use yf.Ticker(sym) never yf.download()
    - Module shape: standalone, no FastAPI imports, runnable with python3
    - F338 gate: see probe_ratings_ingest.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action normalization
# ---------------------------------------------------------------------------
# yfinance Action codes → normalized set {up, down, init, reiterate, unknown}
# Source observations: 'up', 'down', 'init', 'main', 'reit'
_ACTION_MAP: dict[str, str] = {
    "up":   "up",
    "down": "down",
    "init": "init",
    "main": "reiterate",   # "maintains"
    "reit": "reiterate",   # "reiterates"
    # belt+suspenders for variants occasionally seen in the wild:
    "maintain":  "reiterate",
    "reiterate": "reiterate",
    "upgrade":   "up",
    "downgrade": "down",
    "initiated": "init",
    "coverage":  "init",
}


def _normalize_action(raw: str) -> str:
    """Map a raw yfinance Action string to the canonical 5-value set."""
    if not raw or (isinstance(raw, float)):
        return "unknown"
    key = str(raw).strip().lower()
    return _ACTION_MAP.get(key, "unknown")


# ---------------------------------------------------------------------------
# Grade ladder
# ---------------------------------------------------------------------------
# Ordinal grade → signed integer.
# Strong Buy = +2, Buy/Outperform/Overweight/Positive = +1,
# Hold/Neutral/Equal-Weight/Market-Perform/Sector-Weight/Perform/Peer-Perform = 0,
# Underperform/Underweight/Reduce = -1, Sell/Strong Sell = -2.
# Unknown/blank → None (recorded but flagged).
_GRADE_LADDER: dict[str, int] = {
    # +2
    "strong buy":     +2,
    # +1
    "buy":            +1,
    "outperform":     +1,
    "overweight":     +1,
    "positive":       +1,
    "accumulate":     +1,
    "add":            +1,
    # 0
    "hold":            0,
    "neutral":         0,
    "equal-weight":    0,
    "equal weight":    0,
    "market perform":  0,
    "market outperform": 0,  # some firms use this as mid-tier
    "in-line":         0,
    "inline":          0,
    "sector perform":  0,
    "sector weight":   0,
    "sector-weight":   0,
    "peer perform":    0,
    "perform":         0,
    # -1
    "underperform":   -1,
    "underweight":    -1,
    "reduce":         -1,
    "trim":           -1,
    # -2
    "sell":           -2,
    "strong sell":    -2,
}


def _grade_to_ordinal(grade: str) -> Optional[int]:
    """Map a grade string to [-2, -1, 0, +1, +2] or None if unmapped."""
    if not grade or (isinstance(grade, float) and pd.isna(grade)):
        return None
    key = str(grade).strip().lower()
    return _GRADE_LADDER.get(key)  # None for unknowns


def _grade_delta(from_grade: str, to_grade: str) -> Optional[int]:
    """Compute signed delta from from_grade → to_grade.

    Returns None if either grade is blank/unmapped.
    For 'init' actions, from_grade is blank → None is the correct sentinel.
    """
    f_ord = _grade_to_ordinal(from_grade)
    t_ord = _grade_to_ordinal(to_grade)
    if f_ord is None or t_ord is None:
        return None
    return t_ord - f_ord


# ---------------------------------------------------------------------------
# Per-ticker fetch
# ---------------------------------------------------------------------------

class RatingsFetchError(Exception):
    """Transient yfinance/network failure fetching ratings — caller should retry.

    Distinct from a genuine *no coverage* result (empty), which returns None and
    must NOT be retried: most small-caps have no analyst ratings, and retrying
    their definitive 404s 3x was the dominant cost of a full-universe run.
    """


def fetch_ticker_ratings(symbol: str) -> Optional[pd.DataFrame]:
    """Fetch upgrades_downgrades for one ticker and return a tidy event frame.

    Returns None if the ticker has genuinely no rating data (definitive — do NOT
    retry). Raises RatingsFetchError on a transient fetch error (caller retries).

    Output columns:
        ticker, date, firm, action, from_grade, to_grade,
        grade_delta (int or NaN), to_grade_ordinal (int or NaN),
        grade_delta_unmapped (bool)

    date is a timezone-naive date (just the calendar date; time stripped).
    """
    try:
        t = yf.Ticker(symbol)
        ud = t.upgrades_downgrades
    except Exception as exc:
        # Transient (network/throttle) — signal the caller to retry. Genuine
        # "no coverage" does NOT raise here (yfinance returns an empty frame,
        # logging a 404 internally) and is handled as definitive None below.
        raise RatingsFetchError(f"{symbol}: {exc}") from exc

    if ud is None or ud.empty:
        log.debug("fetch_ticker_ratings(%s): no data", symbol)
        return None

    rows = []
    for ts, row in ud.iterrows():
        # GradeDate index is datetime — convert to date only
        if hasattr(ts, "date"):
            action_date = ts.date()
        else:
            try:
                action_date = pd.Timestamp(ts).date()
            except Exception:
                continue

        raw_action = str(row.get("Action", "") or "")
        raw_firm = str(row.get("Firm", "") or "")
        raw_to = str(row.get("ToGrade", "") or "")
        raw_from = str(row.get("FromGrade", "") or "")

        norm_action = _normalize_action(raw_action)
        delta = _grade_delta(raw_from, raw_to)
        to_ord = _grade_to_ordinal(raw_to)

        rows.append({
            "ticker":               symbol.upper(),
            "date":                 action_date,
            "firm":                 raw_firm,
            "action":               norm_action,
            "from_grade":           raw_from,
            "to_grade":             raw_to,
            "grade_delta":          delta,          # int or None
            "to_grade_ordinal":     to_ord,         # int or None
            "grade_delta_unmapped": delta is None,  # True when either grade not in ladder
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Aggregation panel
# ---------------------------------------------------------------------------

def build_agg_panel(events: pd.DataFrame) -> pd.DataFrame:
    """Build per-(ticker, date) aggregation panel from the event frame.

    For each ticker, forward-fills onto its unique action dates:
      net_upgrades_21d     — rolling 21-day count of 'up' minus 'down' actions
      net_upgrades_63d     — rolling 63-day count of 'up' minus 'down'
      days_since_last_action — calendar days since last action of any kind

    The aggregation panel is NOT forward-filled to daily trading dates (that
    join belongs to the feature-panel loader, Phase 1). Output contains only
    rows for dates where ≥1 action occurred.
    """
    if events.empty:
        return pd.DataFrame(columns=[
            "ticker", "date", "net_upgrades_21d", "net_upgrades_63d",
            "days_since_last_action",
        ])

    results = []
    for ticker, grp in events.groupby("ticker"):
        grp = grp.sort_values("date").copy()
        grp["date"] = pd.to_datetime(grp["date"])

        # Create a daily is_up / is_down series on unique action dates
        daily = grp.groupby("date").agg(
            n_up   = ("action", lambda s: (s == "up").sum()),
            n_down = ("action", lambda s: (s == "down").sum()),
        ).reset_index()
        daily = daily.sort_values("date")

        # Reindex to daily calendar for rolling windows, then filter back
        # (rolling on sparse dates gives wrong window sizes)
        full_idx = pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")
        daily_full = daily.set_index("date").reindex(full_idx, fill_value=0)

        daily_full["net_upgrades_21d"] = (
            daily_full["n_up"].rolling(21, min_periods=1).sum()
            - daily_full["n_down"].rolling(21, min_periods=1).sum()
        )
        daily_full["net_upgrades_63d"] = (
            daily_full["n_up"].rolling(63, min_periods=1).sum()
            - daily_full["n_down"].rolling(63, min_periods=1).sum()
        )

        # days_since_last_action: vectorized via forward-fill of action dates.
        # Build a series indexed on the full daily range where each action date
        # carries itself as a value; forward-fill so every subsequent date
        # inherits the most recent action date; then compute the gap in days.
        # Dates before the first action remain NaT → days_since = None.
        action_date_series = pd.Series(
            pd.to_datetime(daily["date"].values),
            index=pd.to_datetime(daily["date"].values),
        )
        last_action_on_date = (
            action_date_series
            .reindex(daily_full.index)
            .ffill()
        )
        days_since_series = (pd.DatetimeIndex(daily_full.index) - last_action_on_date).dt.days
        # days_since_series: Int64 where pre-first-action rows are NaT → NaN
        # (pandas converts timedelta NaT to NaN automatically in integer context)

        daily_full["days_since_last_action"] = days_since_series

        for idx_date, row in daily_full.iterrows():
            ts = pd.Timestamp(idx_date)
            raw_days = row["days_since_last_action"]
            days_since: Optional[int] = None if pd.isna(raw_days) else int(raw_days)
            results.append({
                "ticker":               ticker,
                "date":                 ts,
                "net_upgrades_21d":     int(row["net_upgrades_21d"]),
                "net_upgrades_63d":     int(row["net_upgrades_63d"]),
                "days_since_last_action": days_since,
            })

    if not results:
        return pd.DataFrame(columns=[
            "ticker", "date", "net_upgrades_21d", "net_upgrades_63d",
            "days_since_last_action",
        ])

    agg = pd.DataFrame(results)
    # Keep only rows where an action actually occurred (not gap-fill rows)
    action_dates_set = set(
        zip(events["ticker"].values, events["date"].dt.strftime("%Y-%m-%d").values)
    )
    agg["_key"] = list(zip(
        agg["ticker"].values,
        agg["date"].dt.strftime("%Y-%m-%d").values,
    ))
    agg = agg[agg["_key"].isin(action_dates_set)].drop(columns=["_key"])
    agg = agg.sort_values(["ticker", "date"]).reset_index(drop=True)
    return agg


# ---------------------------------------------------------------------------
# Public API: build_ratings_panels
# ---------------------------------------------------------------------------

def build_ratings_panels(
    tickers: list[str],
    *,
    output_dir: Optional[Path] = None,
    write_parquet: bool = True,
    pace_seconds: float = 0.5,
    max_retries: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Fetch ratings for all tickers and produce event + aggregation panels.

    Parameters
    ----------
    tickers : list[str]
        Ticker symbols to fetch.
    output_dir : Path, optional
        Directory to write parquet files and metadata sidecar.
        Defaults to backend/data/ratings/.
    write_parquet : bool
        If True (default), write parquet + JSON sidecar to output_dir.
    pace_seconds : float
        Inter-ticker sleep in seconds (default 0.5) to avoid yfinance throttling.
        At full 12k-ticker scale a burst with no pacing causes silent throttling
        that returns false-empty results.
    max_retries : int
        How many times to retry a ticker that returns empty/error before
        recording it as failed (default 3). Retries use exponential back-off
        (1s, 2s, 4s, ...). Distinguishes "throttled/error" from "genuinely no
        ratings".

    Returns
    -------
    events_df   : tidy event panel DataFrame
    agg_df      : aggregation panel DataFrame
    meta        : metadata dict (same as JSON sidecar)
    """
    if output_dir is None:
        output_dir = _BACKEND_DIR / "data" / "ratings"

    output_dir = Path(output_dir)

    fetch_vintage = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info(
        "build_ratings_panels: fetching %d tickers (vintage=%s, pace=%.1fs, max_retries=%d)",
        len(tickers), fetch_vintage, pace_seconds, max_retries,
    )

    all_frames: list[pd.DataFrame] = []
    n_empty = 0
    n_failed = 0
    failed_tickers: list[str] = []
    last_fetch_time: float = 0.0

    for i, sym in enumerate(tickers):
        # Inter-ticker pacing
        if i > 0 and pace_seconds > 0:
            elapsed = time.monotonic() - last_fetch_time
            wait = max(0.0, pace_seconds - elapsed)
            if wait > 0:
                time.sleep(wait)

        # Retry ONLY on a transient fetch error (RatingsFetchError); a definitive
        # result — data OR genuine no-coverage (None) — never retries. Retrying
        # the no-coverage 404s was the dominant cost of a full-universe run.
        df: Optional[pd.DataFrame] = None
        errored = False
        for attempt in range(max_retries):
            try:
                df = fetch_ticker_ratings(sym)
                errored = False
                break  # definitive result (data or no-coverage) — done
            except RatingsFetchError:
                errored = True
                if attempt < max_retries - 1:
                    backoff = 2 ** attempt  # 1s, 2s, 4s, ...
                    log.debug(
                        "build_ratings_panels: %s transient error (attempt %d/%d), retrying in %ds",
                        sym, attempt + 1, max_retries, backoff,
                    )
                    time.sleep(backoff)
            finally:
                last_fetch_time = time.monotonic()

        if df is not None and not df.empty:
            all_frames.append(df)
        elif errored:
            # Transient failure that exhausted retries — re-fetchable later.
            n_failed += 1
            failed_tickers.append(sym)
            log.debug("build_ratings_panels: %s FAILED after %d attempts", sym, max_retries)
        else:
            # Definitive: ticker has no analyst coverage. Not a failure.
            n_empty += 1

    log.info(
        "build_ratings_panels: %d/%d tickers had data (%d no-coverage, %d failed; failed_tickers=%s)",
        len(all_frames), len(tickers), n_empty, n_failed, failed_tickers[:10],
    )

    if not all_frames:
        events_df = pd.DataFrame(columns=[
            "ticker", "date", "firm", "action", "from_grade", "to_grade",
            "grade_delta", "to_grade_ordinal", "grade_delta_unmapped",
        ])
    else:
        events_df = pd.concat(all_frames, ignore_index=True)
        events_df = events_df.sort_values(["ticker", "date"]).reset_index(drop=True)

    agg_df = build_agg_panel(events_df)

    # Metadata
    n_rows = len(events_df)
    n_tickers_with_data = events_df["ticker"].nunique() if n_rows > 0 else 0
    coverage_start = str(events_df["date"].min().date()) if n_rows > 0 else None
    coverage_end   = str(events_df["date"].max().date()) if n_rows > 0 else None

    meta = {
        "source":           "yfinance.upgrades_downgrades",
        "fetch_vintage":    fetch_vintage,
        "survivorship":     "survivors-only",
        "pit_field":        "date",
        "coverage_start":   coverage_start,
        "coverage_end":     coverage_end,
        "n_rows":           n_rows,
        "n_tickers":        n_tickers_with_data,
        "n_tickers_requested": len(tickers),
        "n_tickers_empty":  n_empty,
        "n_tickers_failed": n_failed,
        "failed_tickers":   failed_tickers,
        "note":             (
            "yfinance serves only currently-listed tickers. "
            "Ratings for delisted companies are absent. "
            "Rating history may shift between fetches (fetch_vintage records when)."
        ),
    }

    if write_parquet:
        output_dir.mkdir(parents=True, exist_ok=True)
        events_path = output_dir / "ratings_events.parquet"
        agg_path    = output_dir / "ratings_agg.parquet"
        meta_path   = output_dir / "ratings_meta.json"

        events_df.to_parquet(events_path, index=False)
        agg_df.to_parquet(agg_path, index=False)
        meta_path.write_text(json.dumps(meta, indent=2))
        log.info("Wrote: %s (%d rows)", events_path, n_rows)
        log.info("Wrote: %s (%d rows)", agg_path, len(agg_df))
        log.info("Wrote: %s", meta_path)

    return events_df, agg_df, meta


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="F401 ratings ingest — fetch yfinance analyst ratings")
    parser.add_argument("--tickers", nargs="+", required=True, help="Ticker symbols to fetch")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: backend/data/ratings/)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else None
    events, agg, meta = build_ratings_panels(args.tickers, output_dir=out_dir)
    print(json.dumps(meta, indent=2))
    print(f"\nEvent rows: {len(events)}")
    print(f"Agg rows:   {len(agg)}")
