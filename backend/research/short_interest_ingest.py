"""F403 — FINRA Biweekly Short Interest Ingest.

Downloads FINRA consolidated short interest settlement files, parses them into
a tidy event panel, and produces a daily forward-filled alignment with a
staleness_days flag.

Source:  https://cdn.finra.org/equity/otcmarket/biweekly/shrt<YYYYMMDD>.csv
Format:  pipe-delimited, one row per (settlement_date, ticker)
Fields used:
    symbolCode                   → ticker
    settlementDate               → settlement_date (YYYYMMDD integer in file)
    currentShortPositionQuantity → short_interest_shares
    averageDailyVolumeQuantity   → avg_daily_volume
    daysToCoverQuantity          → days_to_cover (provided directly by FINRA)

Point-in-time (PIT) field = dissemination_date:
    FINRA Rule 4560: short interest is published on the **7th business day**
    after the settlement date.  The CDN file is made available on that 7th
    business day at approximately 06:00 ET.
    Method: add 7 US business days (Mon–Fri, using numpy busday) to the
    settlement date.  US public holidays are NOT excluded (FINRA's own
    schedule rarely avoids holidays and this approximation is within 1 day
    of the stated 7-business-day rule; documented limitation).
    For backtesting: use dissemination_date as the first date on which a
    position could have been opened on the information.

Coverage note:
    The CDN hosts files from approximately 2018-02-15 onward.  Files before
    that date return HTTP 403.  Survivorship: the panel contains only
    currently-listed tickers (survivors-only), consistent with the Phase 0
    constraint.

Public API:
    fetch_short_interest(settlement_dates)
        → (event_df: pd.DataFrame, meta: dict)

    build_short_interest_panel(settlement_dates, ...)
        → (event_df, daily_df, meta)

    biweekly_settlement_dates(start, end)
        → list[date]   — approximate mid-month + end-of-month settlement dates
"""
from __future__ import annotations

import io
import logging
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CDN_URL = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date_str}.csv"
_UA = "StrategyLab research john@milford.se"
_COVERAGE_START = date(2018, 2, 15)  # empirically confirmed earliest CDN file

# Columns in the raw FINRA CSV (pipe-delimited)
_COL_SETTLEMENT = "settlementDate"
_COL_TICKER = "symbolCode"
_COL_SHORT_INT = "currentShortPositionQuantity"
_COL_PREV_SHORT_INT = "previousShortPositionQuantity"
_COL_ADV = "averageDailyVolumeQuantity"
_COL_DTC = "daysToCoverQuantity"
_COL_EXCHANGE = "issuerServicesGroupExchangeCode"
_COL_MARKET = "marketClassCode"
_COL_SPLIT = "stockSplitFlag"
_COL_REVISION = "revisionFlag"

# Rate-limit: be polite to CDN (0 since CDN/CloudFront is not rate-limited,
# but space requests slightly to avoid hammering)
_FETCH_PACE_SECS = 0.3

# ---------------------------------------------------------------------------
# Settlement date helpers
# ---------------------------------------------------------------------------

def _add_business_days(d: date, n: int) -> date:
    """Add n US business days to date d (Mon–Fri; no holiday calendar)."""
    ts = np.busday_offset(d.isoformat(), n, roll="forward")
    return date.fromisoformat(str(ts))


def dissemination_date(settlement: date) -> date:
    """Return the estimated dissemination date for a settlement date.

    Per FINRA Rule 4560: published on the 7th business day after the
    settlement date.  No US holiday calendar is applied — within 1 day
    of true schedule.
    """
    return _add_business_days(settlement, 7)


def biweekly_settlement_dates(start: date, end: date) -> list[date]:
    """Generate approximate FINRA biweekly settlement dates in [start, end].

    FINRA settlement schedule:
    - Mid-month: 15th of each month, or the preceding business day if the
      15th is not a business day.
    - End-of-month: last business day of each month.

    Returns dates in ascending order.
    """
    dates: list[date] = []
    y, m = start.year, start.month
    while True:
        # Mid-month: 15th (or nearest preceding business day)
        mid = date(y, m, 15)
        mid_settled = date.fromisoformat(str(np.busday_offset(mid.isoformat(), 0, roll="preceding")))
        if start <= mid_settled <= end:
            dates.append(mid_settled)

        # End-of-month: last business day
        if m == 12:
            next_m_first = date(y + 1, 1, 1)
        else:
            next_m_first = date(y, m + 1, 1)
        eom = next_m_first - timedelta(days=1)
        eom_settled = date.fromisoformat(str(np.busday_offset(eom.isoformat(), 0, roll="preceding")))
        if start <= eom_settled <= end:
            dates.append(eom_settled)

        # Advance month
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1

        if date(y, m, 1) > end:
            break

    return sorted(set(dates))


# ---------------------------------------------------------------------------
# Single-file fetch + parse
# ---------------------------------------------------------------------------

_last_fetch: float = 0.0


def _pace() -> None:
    global _last_fetch
    elapsed = time.monotonic() - _last_fetch
    wait = max(0.0, _FETCH_PACE_SECS - elapsed)
    if wait > 0:
        time.sleep(wait)
    _last_fetch = time.monotonic()


def fetch_one(settlement: date, *, timeout: int = 30) -> Optional[pd.DataFrame]:
    """Fetch and parse one FINRA short interest file for settlement date.

    Returns a DataFrame with columns:
        ticker, settlement_date, dissemination_date,
        short_interest_shares, avg_daily_volume, days_to_cover,
        prev_short_interest_shares, exchange_code, market_class,
        split_flag, revision_flag

    Returns None if the file is not available (HTTP 403/404 or parse error).
    """
    date_str = settlement.strftime("%Y%m%d")
    url = _CDN_URL.format(date_str=date_str)
    _pace()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        log.debug("fetch_one: HTTP %d for %s", exc.code, url)
        return None
    except Exception as exc:
        log.debug("fetch_one: error fetching %s: %s", url, exc)
        return None

    try:
        df = pd.read_csv(
            io.StringIO(raw),
            sep="|",
            dtype=str,
            on_bad_lines="skip",
        )
    except Exception as exc:
        log.warning("fetch_one: parse error for %s: %s", date_str, exc)
        return None

    if df.empty or _COL_TICKER not in df.columns:
        log.warning("fetch_one: empty or missing symbolCode column for %s", date_str)
        return None

    # -----------------------------------------------------------------------
    # Type coercions
    # -----------------------------------------------------------------------
    df[_COL_TICKER] = df[_COL_TICKER].astype(str).str.strip().str.upper()
    df[_COL_SHORT_INT] = pd.to_numeric(df.get(_COL_SHORT_INT, pd.Series(dtype=str)), errors="coerce")
    df[_COL_PREV_SHORT_INT] = pd.to_numeric(df.get(_COL_PREV_SHORT_INT, pd.Series(dtype=str)), errors="coerce")
    df[_COL_ADV] = pd.to_numeric(df.get(_COL_ADV, pd.Series(dtype=str)), errors="coerce")
    df[_COL_DTC] = pd.to_numeric(df.get(_COL_DTC, pd.Series(dtype=str)), errors="coerce")

    # Drop rows with missing ticker or zero/NaN short interest
    df = df[df[_COL_TICKER].notna() & (df[_COL_TICKER] != "")].copy()
    df = df[df[_COL_SHORT_INT].notna() & (df[_COL_SHORT_INT] > 0)].copy()

    dissem = dissemination_date(settlement)

    out = pd.DataFrame({
        "ticker": df[_COL_TICKER].values,
        "settlement_date": settlement,
        "dissemination_date": dissem,
        "short_interest_shares": df[_COL_SHORT_INT].values,
        "avg_daily_volume": df[_COL_ADV].values,
        "days_to_cover": df[_COL_DTC].values,
        "prev_short_interest_shares": df[_COL_PREV_SHORT_INT].values,
        "exchange_code": df.get(_COL_EXCHANGE, pd.Series([""] * len(df))).values,
        "market_class": df.get(_COL_MARKET, pd.Series([""] * len(df))).values,
        "split_flag": df.get(_COL_SPLIT, pd.Series([""] * len(df))).values,
        "revision_flag": df.get(_COL_REVISION, pd.Series([""] * len(df))).values,
    })
    log.debug("fetch_one: %s → %d rows", date_str, len(out))
    return out


# ---------------------------------------------------------------------------
# Multi-date fetch
# ---------------------------------------------------------------------------

def fetch_short_interest(
    settlement_dates: list[date],
    *,
    timeout: int = 30,
) -> tuple[pd.DataFrame, dict]:
    """Fetch and parse FINRA short interest for a list of settlement dates.

    Parameters
    ----------
    settlement_dates : list[date]
        Settlement dates to fetch.  Dates before _COVERAGE_START will be
        attempted but will return no data (documents the coverage limit).
    timeout : int
        HTTP timeout per request in seconds.

    Returns
    -------
    event_df : pd.DataFrame
        Tidy event panel with columns:
            ticker, settlement_date, dissemination_date,
            short_interest_shares, avg_daily_volume, days_to_cover,
            prev_short_interest_shares, exchange_code, market_class,
            split_flag, revision_flag
    meta : dict
        Fetch metadata including coverage info, n_rows, n_tickers,
        dates_fetched, dates_missing.
    """
    frames: list[pd.DataFrame] = []
    dates_fetched: list[str] = []
    dates_missing: list[str] = []

    for sd in sorted(settlement_dates):
        df = fetch_one(sd, timeout=timeout)
        if df is not None and not df.empty:
            frames.append(df)
            dates_fetched.append(sd.isoformat())
            log.info("fetch_short_interest: %s → %d rows", sd.isoformat(), len(df))
        else:
            dates_missing.append(sd.isoformat())
            log.info("fetch_short_interest: %s → NOT AVAILABLE", sd.isoformat())

    if frames:
        event_df = pd.concat(frames, ignore_index=True)
        event_df["settlement_date"] = pd.to_datetime(event_df["settlement_date"]).dt.date
        event_df["dissemination_date"] = pd.to_datetime(event_df["dissemination_date"]).dt.date
    else:
        event_df = pd.DataFrame(columns=[
            "ticker", "settlement_date", "dissemination_date",
            "short_interest_shares", "avg_daily_volume", "days_to_cover",
            "prev_short_interest_shares", "exchange_code", "market_class",
            "split_flag", "revision_flag",
        ])

    n_tickers = event_df["ticker"].nunique() if not event_df.empty else 0
    meta = {
        "source": "FINRA",
        "survivorship": "survivors-only",
        "coverage_start": _COVERAGE_START.isoformat(),
        "pit_field": "dissemination_date",
        "pit_method": (
            "settlement_date + 7 US business days (numpy busday, no holiday calendar); "
            "within 1 day of FINRA Rule 4560 published 7th-business-day schedule"
        ),
        "dates_fetched": dates_fetched,
        "dates_missing": dates_missing,
        "n_dates": len(dates_fetched),
        "n_rows": len(event_df),
        "n_tickers": n_tickers,
        "exchange_coverage_note": (
            "Both OTC and exchange-listed names present from CDN start ~2018-02-15 "
            "(empirically verified: GE/BAC/F/GME present in 2019 files). "
            "issuerServicesGroupExchangeCode is FINRA issuer-services group, not an OTC/exchange split."
        ),
    }
    return event_df, meta


# ---------------------------------------------------------------------------
# Daily forward-filled alignment
# ---------------------------------------------------------------------------

def build_daily_panel(
    event_df: pd.DataFrame,
    date_range: Optional[tuple[date, date]] = None,
) -> pd.DataFrame:
    """Forward-fill the biweekly event panel to a daily ticker×date panel.

    The fill key is dissemination_date (PIT): a row is first "visible" on
    dissemination_date.  Before the first dissemination_date for a ticker,
    the row is absent (no look-ahead).

    Parameters
    ----------
    event_df : pd.DataFrame
        Output of fetch_short_interest().
    date_range : tuple[date, date], optional
        (start, end) for the daily index.  Defaults to
        [min(dissemination_date), max(dissemination_date)].

    Returns
    -------
    daily_df : pd.DataFrame
        Columns: ticker, date, short_interest_shares, avg_daily_volume,
        days_to_cover, settlement_date, dissemination_date, staleness_days
        where staleness_days = (date − dissemination_date).days.
    """
    if event_df.empty:
        return pd.DataFrame(columns=[
            "ticker", "date", "short_interest_shares", "avg_daily_volume",
            "days_to_cover", "settlement_date", "dissemination_date", "staleness_days",
        ])

    # Normalise date columns to date objects
    ev = event_df.copy()
    ev["settlement_date"] = pd.to_datetime(ev["settlement_date"]).dt.date
    ev["dissemination_date"] = pd.to_datetime(ev["dissemination_date"]).dt.date

    if date_range is None:
        start = ev["dissemination_date"].min()
        end = ev["dissemination_date"].max()
    else:
        start, end = date_range

    all_dates = pd.date_range(start=start, end=end, freq="D")
    tickers = ev["ticker"].unique()

    # Build a pivot on dissemination_date for forward-fill.
    # value_cols: columns to forward-fill (excluding the index key).
    # dissemination_date is added back as an explicit value column so the
    # downstream staleness computation can access daily_df["dissemination_date"].
    value_cols = ["short_interest_shares", "avg_daily_volume", "days_to_cover",
                  "settlement_date", "dissemination_date"]

    # For each ticker: reindex to daily range, forward-fill from dissemination_date
    frames: list[pd.DataFrame] = []
    for ticker in tickers:
        # Select dissemination_date + value_cols minus dissemination_date itself,
        # then assign dissemination_date back as a regular column before set_index
        # so it survives the reindex + ffill and is available in the output.
        ffill_cols = ["short_interest_shares", "avg_daily_volume", "days_to_cover",
                      "settlement_date"]
        ticker_df = (
            ev[ev["ticker"] == ticker][ffill_cols + ["dissemination_date"]]
            .drop_duplicates(subset=["dissemination_date"])
            .sort_values("dissemination_date")
        )
        # Copy dissemination_date as a value column before using it as the index
        ticker_df = ticker_df.copy()
        ticker_df["dissemination_date_val"] = ticker_df["dissemination_date"]
        sub = ticker_df.set_index("dissemination_date")
        # Re-index onto full daily range (date objects as index)
        date_idx = [d.date() for d in all_dates]
        sub_reindexed = sub.reindex(date_idx)
        sub_reindexed = sub_reindexed.ffill()
        # Drop rows that are still NaN (before first dissemination for this ticker)
        sub_reindexed = sub_reindexed.dropna(subset=["short_interest_shares"])
        if sub_reindexed.empty:
            continue
        # Rename the temporary copy back to dissemination_date
        sub_reindexed = sub_reindexed.rename(columns={"dissemination_date_val": "dissemination_date"})
        sub_reindexed["ticker"] = ticker
        sub_reindexed.index.name = "date"
        sub_reindexed = sub_reindexed.reset_index()
        frames.append(sub_reindexed)

    if not frames:
        return pd.DataFrame(columns=[
            "ticker", "date", "short_interest_shares", "avg_daily_volume",
            "days_to_cover", "settlement_date", "dissemination_date", "staleness_days",
        ])

    daily_df = pd.concat(frames, ignore_index=True)

    # staleness_days: calendar days since data was disseminated
    daily_df["staleness_days"] = (
        pd.to_datetime(daily_df["date"]) - pd.to_datetime(daily_df["dissemination_date"])
    ).dt.days

    col_order = [
        "ticker", "date", "short_interest_shares", "avg_daily_volume",
        "days_to_cover", "settlement_date", "dissemination_date", "staleness_days",
    ]
    return daily_df[col_order].sort_values(["ticker", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def build_short_interest_panel(
    settlement_dates: list[date],
    *,
    date_range: Optional[tuple[date, date]] = None,
    timeout: int = 30,
    out_dir: Optional[Path] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Fetch + parse + daily-align FINRA short interest for settlement_dates.

    Parameters
    ----------
    settlement_dates : list[date]
        Settlement dates to include.
    date_range : tuple[date, date], optional
        Start/end for the daily alignment panel.
    timeout : int
        HTTP timeout per request.
    out_dir : Path, optional
        If provided, write event_panel.parquet, daily_panel.parquet,
        and metadata.json to this directory.

    Returns
    -------
    event_df : pd.DataFrame
        Biweekly event panel (one row per ticker × settlement date).
    daily_df : pd.DataFrame
        Daily forward-filled alignment with staleness_days.
    meta : dict
        Metadata sidecar (source, survivorship, coverage, pit_field, etc.).
    """
    event_df, meta = fetch_short_interest(settlement_dates, timeout=timeout)

    daily_df = build_daily_panel(event_df, date_range=date_range)

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if not event_df.empty:
            event_df.to_parquet(out_dir / "event_panel.parquet", index=False)
        if not daily_df.empty:
            daily_df.to_parquet(out_dir / "daily_panel.parquet", index=False)
        import json
        meta_out = dict(meta)
        # Ensure dates are serialisable
        for k in ("coverage_start",):
            if k in meta_out and hasattr(meta_out[k], "isoformat"):
                meta_out[k] = meta_out[k].isoformat()
        (out_dir / "metadata.json").write_text(
            json.dumps(meta_out, indent=2, default=str), encoding="utf-8"
        )
        log.info("build_short_interest_panel: wrote artifacts to %s", out_dir)

    return event_df, daily_df, meta
