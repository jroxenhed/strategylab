"""F400 — Widen the Price Universe.

Builds a master currently-listed US-equity universe from the free NASDAQ Trader
symbol directory (nasdaqtraded.txt — NASDAQ + NYSE + AMEX common stock + ETFs),
then fetches daily OHLCV 2015→2024 into the existing PriceFrameCache.

Public API:
    fetch_nasdaq_trader_manifest() → pd.DataFrame
        Downloads nasdaqtraded.txt, filters test issues and the footer row,
        returns a DataFrame with columns:
          ticker, name, exchange, listing_exchange, is_etf

    build_universe_manifest(tickers_df, liquid_universe_tickers=None)
        → (manifest_df, meta_dict)
        Enriches with in_liquid_universe_v1 flag; emits universe_manifest.parquet +
        metadata JSON sidecar.

    fetch_universe_prices(tickers, start, end, cache_dir)
        Fetches daily OHLCV via yf.Ticker().history() through PriceFrameCache.
        NEVER uses yf.download() (shared-global-state corruption — CLAUDE.md Key Bugs Fixed).

Design decisions (F400):
  - Membership is NOT gated by the $5/500k floor or SEC-filer status.
    Those become per-date labels via universe_floors.floor_status().
  - in_liquid_universe_v1 = ticker appears in the existing liquid universe
    (build_liquid_universe output, which requires non-zero SIC + price coverage
    from the 2012-2021 UNIVERSE_V2 price cache). NOT a general current-SEC-filer
    flag — post-2021 listings read False. Phase 1 can re-derive true filer status
    from the full EDGAR registry. This is a fast offline cross-reference.
  - survivorship: "survivors-only" — nasdaqtrader.txt lists CURRENTLY-LISTED
    symbols only. Delisted names are absent. Every artifact carries this stamp.
  - fetch_vintage is recorded so downstream callers can reason about vintage drift
    (yfinance retroactively re-adjusts prices after splits).

Survivorship caveat (F338 anchor A4):
  nasdaqtraded.txt reflects the snapshot at fetch time — only currently-listed
  tickers are present. Names delisted before the fetch date are absent.
  Phase 0 widens the live small-cap / ETF set; it does NOT add dead companies.
  Every metadata sidecar carries survivorship="survivors-only".
"""
from __future__ import annotations

import io
import json
import logging
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Path setup — standalone module, no FastAPI imports
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# PriceFrameCache lives in turnaround_validation (the existing price layer)
from turnaround_validation import PriceFrameCache  # noqa: E402

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# NASDAQ Trader free symbol directory
_NASDAQ_TRADER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

# Default output directory for the full production run (worker job)
_DEFAULT_PRICE_CACHE_DIR = _BACKEND_DIR / "data" / "turnaround" / "price_cache"
_DEFAULT_MANIFEST_DIR = _BACKEND_DIR / "data" / "universe"

# Canonical fetch window for the full universe price build
_DEFAULT_FETCH_START = "2015-01-01"
_DEFAULT_FETCH_END = "2024-12-31"

# Pacing between per-ticker yfinance fetches (politeness — ~2 req/s target)
_FETCH_PACE_SECS: float = 0.5


# ---------------------------------------------------------------------------
# NASDAQ Trader symbol directory fetch + parse
# ---------------------------------------------------------------------------

def fetch_nasdaq_trader_manifest(
    max_retries: int = 2,
    timeout: int = 30,
) -> pd.DataFrame:
    """Download nasdaqtraded.txt and return a filtered symbol manifest.

    The file is pipe-delimited with a trailing footer row (starts with 'File Creation Time').
    Test issues (Test Issue == 'Y') and blank Symbol rows are dropped.

    Returns a DataFrame with columns:
      ticker, name, exchange, listing_exchange, is_etf

    Raises:
        RuntimeError: if the download fails after max_retries attempts.
    """
    url = _NASDAQ_TRADER_URL
    raw_text: Optional[str] = None
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            log.info("Fetching nasdaqtraded.txt (attempt %d/%d) from %s", attempt, max_retries, url)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "StrategyLab research john@milford.se"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
            raw_text = raw_bytes.decode("latin-1")  # NASDAQ uses latin-1 for some symbols
            log.info("nasdaqtraded.txt fetched: %d bytes", len(raw_bytes))
            break
        except Exception as exc:
            last_exc = exc
            log.warning("Fetch attempt %d failed: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(2.0)

    if raw_text is None:
        raise RuntimeError(
            f"Failed to fetch nasdaqtraded.txt after {max_retries} attempts: {last_exc}"
        )

    return _parse_nasdaq_trader_text(raw_text)


def _parse_nasdaq_trader_text(raw_text: str) -> pd.DataFrame:
    """Parse nasdaqtraded.txt content into a symbol manifest DataFrame.

    File format (pipe-delimited, first line is header):
      Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|
      Test Issue|Financial Status|CQS Symbol|NASDAQ Symbol|NextShares

    Column notes:
      - 'ETF' = 'Y' for ETF, 'N' for common stock (or blank)
      - 'Test Issue' = 'Y' for test/reserved symbols — always drop
      - Last row starts with 'File Creation Time' — always drop (footer)
      - 'Listing Exchange' codes: Q=NASDAQ, N=NYSE, A=AMEX, P=ARCA, Z=BATS, etc.

    Deduplication: nasdaqtraded.txt lists every symbol once; no dedup needed.
    The Symbol column is the primary ticker.
    """
    lines = [l for l in raw_text.splitlines() if l.strip()]

    # Drop footer row (starts with 'File Creation Time')
    lines = [l for l in lines if not l.startswith("File Creation Time")]

    if not lines:
        raise ValueError("nasdaqtraded.txt is empty after filtering")

    df = pd.read_csv(
        io.StringIO("\n".join(lines)),
        sep="|",
        dtype=str,
        encoding="utf-8",
        on_bad_lines="skip",
    )

    log.info("nasdaqtraded.txt raw rows (after footer drop): %d, columns: %s",
             len(df), list(df.columns))

    # Normalize column names to lowercase / stripped
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # The primary ticker column is 'symbol' (the traded symbol)
    if "symbol" not in df.columns:
        raise ValueError(f"Expected 'symbol' column, got: {list(df.columns)}")

    # Drop test issues (Test Issue == 'Y')
    if "test_issue" in df.columns:
        n_before = len(df)
        df = df[df["test_issue"].str.upper().str.strip() != "Y"].copy()
        log.info("Dropped %d test issues", n_before - len(df))

    # Drop blank/missing Symbol rows
    df = df[df["symbol"].str.strip().str.len() > 0].copy()

    # Drop rows where symbol contains '$' (warrants / preferred series oddities)
    # and rows with '.' that are class-share identifiers with no yfinance coverage
    # NOTE: we keep BRK.A / BRK.B style (dots are common for class shares)
    # but drop '$'-containing symbols (these are preferred/warrants, not equities)
    n_before = len(df)
    df = df[~df["symbol"].str.contains(r"\$", regex=True, na=False)].copy()
    log.info("Dropped %d '$'-containing symbols (preferred/warrants)", n_before - len(df))

    # Build clean output columns
    # ETF flag: 'Y' → True
    etf_col = "etf" if "etf" in df.columns else None
    is_etf = (
        df[etf_col].str.upper().str.strip() == "Y"
        if etf_col is not None
        else pd.Series([False] * len(df))
    )

    # Exchange: prefer 'listing_exchange', fall back to 'market_category'
    exchange_col = None
    for candidate in ("listing_exchange", "market_category", "exchange"):
        if candidate in df.columns:
            exchange_col = candidate
            break

    name_col = None
    for candidate in ("security_name", "name"):
        if candidate in df.columns:
            name_col = candidate
            break

    manifest = pd.DataFrame({
        "ticker": df["symbol"].str.strip().str.upper(),
        "name": df[name_col].str.strip() if name_col is not None else "",
        "exchange": df[exchange_col].str.strip() if exchange_col is not None else "",
        "listing_exchange": df["listing_exchange"].str.strip() if "listing_exchange" in df.columns else "",
        "is_etf": is_etf.values,
    })

    # Final dedup on ticker (should not occur, but safety net)
    n_before = len(manifest)
    manifest = manifest.drop_duplicates(subset=["ticker"]).reset_index(drop=True)
    if len(manifest) < n_before:
        log.info("Dropped %d duplicate ticker rows after dedup", n_before - len(manifest))

    log.info(
        "Manifest built: %d tickers total (ETFs: %d, common stock: %d)",
        len(manifest),
        manifest["is_etf"].sum(),
        (~manifest["is_etf"]).sum(),
    )
    return manifest


# ---------------------------------------------------------------------------
# SEC-filer cross-reference
# ---------------------------------------------------------------------------

def _get_sec_filer_tickers(
    liquid_universe_tickers: Optional[list[str]] = None,
) -> frozenset[str]:
    """Return the set of tickers known to be SEC filers.

    Method: cross-reference against the existing liquid-universe ticker list,
    which already encodes the SIC gate (non-zero SIC in EDGAR submissions +
    price coverage 2012-2021). This is the fast offline path.

    If liquid_universe_tickers is provided, use that directly.
    Otherwise attempt to build it from the canonical paths — if the paths are
    unavailable (e.g. probe context with no full data), return empty set and
    log a warning.

    in_liquid_universe_v1 interpretation:
      True  = ticker was in the 2012-2021 UNIVERSE_V2 liquid-universe price-cache
              set (SIC-bearing + price-covered). NOT a general current-SEC-filer flag
              — post-2021 listings read False. Phase 1 can re-derive true filer
              status from the full EDGAR registry.
      False = not in the liquid-universe set (sub-$5, non-SIC, new listing, or ETF).
    """
    if liquid_universe_tickers is not None:
        return frozenset(t.upper() for t in liquid_universe_tickers)

    # Attempt to load from canonical paths
    price_cache_v1 = _BACKEND_DIR / "data" / "turnaround" / "price_cache" / "v1"
    subs_dir = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "submissions"

    if not price_cache_v1.exists() or not subs_dir.exists():
        log.warning(
            "_get_sec_filer_tickers: price_cache_v1 or subs_dir not found — "
            "in_liquid_universe_v1 will be False for all tickers (probe context OK)"
        )
        return frozenset()

    try:
        from research.universe_loader import build_liquid_universe  # noqa: E402
        tickers = build_liquid_universe(price_cache_v1, subs_dir)
        log.info("Loaded liquid universe for in_liquid_universe_v1 cross-reference: %d tickers", len(tickers))
        return frozenset(t.upper() for t in tickers)
    except Exception as exc:
        log.warning("_get_sec_filer_tickers: failed to build liquid universe: %s", exc)
        return frozenset()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def build_universe_manifest(
    tickers_df: pd.DataFrame,
    liquid_universe_tickers: Optional[list[str]] = None,
    output_dir: Optional[Path] = None,
    fetch_vintage: Optional[str] = None,
) -> tuple[pd.DataFrame, dict]:
    """Enrich the ticker manifest with in_liquid_universe_v1 and emit parquet + JSON sidecar.

    Parameters
    ----------
    tickers_df:
        Output of fetch_nasdaq_trader_manifest() — must have 'ticker' column.
    liquid_universe_tickers:
        Optional pre-built list of liquid-universe tickers for in_liquid_universe_v1
        cross-reference. If None, attempts to build from canonical paths.
    output_dir:
        Directory to write universe_manifest.parquet + universe_manifest_meta.json.
        If None, files are NOT written (useful for probe contexts).
    fetch_vintage:
        ISO date string (YYYY-MM-DD) recording when the manifest was built.
        Defaults to today.

    Returns
    -------
    manifest_df:
        DataFrame with columns: ticker, name, exchange, listing_exchange,
        is_etf, in_liquid_universe_v1.
    meta:
        Metadata dict suitable for the JSON sidecar.
    """
    if fetch_vintage is None:
        fetch_vintage = date.today().isoformat()

    sec_filers = _get_sec_filer_tickers(liquid_universe_tickers)

    manifest = tickers_df.copy()
    manifest["in_liquid_universe_v1"] = manifest["ticker"].str.upper().isin(sec_filers)

    meta = {
        "source": "nasdaqtraded.txt",
        "source_url": _NASDAQ_TRADER_URL,
        "fetch_vintage": fetch_vintage,
        "survivorship": "survivors-only",
        "coverage_start": _DEFAULT_FETCH_START,
        "coverage_end": _DEFAULT_FETCH_END,
        "n_rows": len(manifest),
        "n_tickers": len(manifest),
        "n_etf": int(manifest["is_etf"].sum()),
        "n_in_liquid_universe_v1": int(manifest["in_liquid_universe_v1"].sum()),
        "in_liquid_universe_v1_description": (
            "True = ticker was in the 2012-2021 UNIVERSE_V2 liquid-universe price-cache set "
            "(SIC-bearing + price-covered). NOT a general current-SEC-filer flag — "
            "post-2021 listings read False. Phase 1 can re-derive true filer status "
            "from the full EDGAR registry."
        ),
        "in_liquid_universe_v1_method": (
            "cross-reference against existing liquid-universe tickers "
            "(SIC-bearing + price-covered 2012-2021); "
            "see universe_loader.build_liquid_universe"
        ),
        "survivorship_note": (
            "nasdaqtraded.txt lists only currently-listed symbols at fetch time. "
            "Delisted names from any period are absent. "
            "This panel widens the live small-cap/ETF set; it does NOT recover dead companies."
        ),
    }

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = output_dir / "universe_manifest.parquet"
        meta_path = output_dir / "universe_manifest_meta.json"

        manifest.to_parquet(str(parquet_path), index=False)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log.info("Wrote manifest parquet: %s (%d rows)", parquet_path, len(manifest))
        log.info("Wrote manifest meta: %s", meta_path)

    return manifest, meta


# ---------------------------------------------------------------------------
# Price fetch via PriceFrameCache
# ---------------------------------------------------------------------------

def fetch_universe_prices(
    tickers: list[str],
    start: str = _DEFAULT_FETCH_START,
    end: str = _DEFAULT_FETCH_END,
    cache_dir: Optional[Path] = None,
    data_source: str = "yahoo",
    pace_secs: float = _FETCH_PACE_SECS,
    progress_every: int = 100,
) -> dict[str, Optional[pd.DataFrame]]:
    """Fetch daily OHLCV for each ticker via PriceFrameCache (yf.Ticker().history()).

    NEVER uses yf.download() — that uses shared global state and corrupts data under
    concurrent requests (see CLAUDE.md Key Bugs Fixed / shared.py YahooProvider).

    Each ticker is fetched via:
      yf.Ticker(ticker).history(start, end, interval='1d', auto_adjust=True)

    Cache semantics:
      - Cache hit: return the pickled DataFrame from disk, no network call.
      - Cache miss: fetch from yfinance, store to disk, return.
      - Empty result (delisted/not-found): return None (no negative cache marker
        in this simplified path — the full production run uses bars_loader).

    Parameters
    ----------
    tickers: list of ticker symbols (upper-case recommended)
    start, end: YYYY-MM-DD date strings (inclusive start, exclusive end in yfinance)
    cache_dir: root price cache directory (parent of v1/); defaults to canonical path
    data_source: provider key for cache keying (default "yahoo")
    pace_secs: seconds between fetches (politeness; ~2 req/s default)
    progress_every: log progress every N tickers

    Returns
    -------
    dict mapping ticker → DataFrame (or None if unavailable)

    Note: for the full thousands-ticker fetch, dispatch to the worker via
    bin/worker-dispatch.sh — this runs at ~2 req/s and will take hours locally.
    """
    import yfinance as yf  # noqa: local import to keep module lightweight

    if cache_dir is None:
        cache_dir = _DEFAULT_PRICE_CACHE_DIR

    cache = PriceFrameCache(cache_dir=Path(cache_dir))

    # Normalize dates to match cache key format (no dashes for the span component)
    fetch_start = start
    fetch_end = end

    results: dict[str, Optional[pd.DataFrame]] = {}
    n_cache_hit = 0
    n_fetched = 0
    n_empty = 0
    n_error = 0
    last_fetch_time = 0.0

    for i, ticker in enumerate(tickers, 1):
        ticker_upper = ticker.upper()

        # Layer 1: disk cache
        cached = cache.load(ticker_upper, fetch_start, fetch_end, data_source)
        if cached is not None:
            results[ticker_upper] = cached
            n_cache_hit += 1
            if i % progress_every == 0:
                log.info(
                    "fetch_universe_prices: %d/%d  cache_hit=%d fetched=%d empty=%d err=%d",
                    i, len(tickers), n_cache_hit, n_fetched, n_empty, n_error,
                )
            continue

        # Pacing: respect inter-fetch delay
        now = time.monotonic()
        elapsed = now - last_fetch_time
        if elapsed < pace_secs:
            time.sleep(pace_secs - elapsed)

        # Layer 2: yfinance fetch — NEVER yf.download()
        try:
            df = yf.Ticker(ticker_upper).history(
                start=fetch_start,
                end=fetch_end,
                interval="1d",
                auto_adjust=True,
            )
            last_fetch_time = time.monotonic()

            if df is None or df.empty:
                results[ticker_upper] = None
                n_empty += 1
            else:
                # Drop any all-NaN rows
                df = df.dropna(how="all")
                if df.empty:
                    results[ticker_upper] = None
                    n_empty += 1
                else:
                    results[ticker_upper] = df
                    cache.store(ticker_upper, fetch_start, fetch_end, df, data_source)
                    n_fetched += 1

        except Exception as exc:
            log.warning("fetch_universe_prices: error for %s: %s", ticker_upper, exc)
            results[ticker_upper] = None
            n_error += 1
            last_fetch_time = time.monotonic()

        if i % progress_every == 0:
            log.info(
                "fetch_universe_prices: %d/%d  cache_hit=%d fetched=%d empty=%d err=%d",
                i, len(tickers), n_cache_hit, n_fetched, n_empty, n_error,
            )

    log.info(
        "fetch_universe_prices DONE: %d tickers — cache_hit=%d fetched=%d empty=%d err=%d",
        len(tickers), n_cache_hit, n_fetched, n_empty, n_error,
    )
    return results


# ---------------------------------------------------------------------------
# Full production run entry point (for worker dispatch)
# ---------------------------------------------------------------------------

def run_full_universe_build(
    output_dir: Path = _DEFAULT_MANIFEST_DIR,
    cache_dir: Path = _DEFAULT_PRICE_CACHE_DIR,
    fetch_start: str = _DEFAULT_FETCH_START,
    fetch_end: str = _DEFAULT_FETCH_END,
    log_file: Optional[Path] = None,
) -> None:
    """Run the full universe build: manifest + price fetch for all tickers.

    Intended to run on the worker (mfcore01) via bin/worker-dispatch.sh.
    Do NOT run locally — thousands of tickers × 10y daily takes hours.

    Progress is logged to stdout and optionally to log_file (for background
    followability — see CLAUDE.local.md "always have a way to follow progress").

    Steps:
      1. Download nasdaqtraded.txt → manifest
      2. Build + write universe_manifest.parquet + meta JSON
      3. Fetch daily OHLCV 2015→2024 for all tickers via PriceFrameCache
    """
    import logging as _logging

    handlers: list[_logging.Handler] = [_logging.StreamHandler()]
    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(_logging.FileHandler(str(log_file)))

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

    log.info("=== F400 full universe build START ===")
    log.info("output_dir=%s  cache_dir=%s", output_dir, cache_dir)
    log.info("fetch window: %s → %s", fetch_start, fetch_end)

    # Step 1: manifest
    manifest_df = fetch_nasdaq_trader_manifest()
    manifest, meta = build_universe_manifest(
        manifest_df,
        output_dir=output_dir,
        fetch_vintage=date.today().isoformat(),
    )
    log.info("Manifest: %d tickers (ETFs: %d, in_liquid_universe_v1: %d)",
             meta["n_tickers"], meta["n_etf"], meta["n_in_liquid_universe_v1"])

    # Step 2: price fetch
    tickers = manifest["ticker"].tolist()
    log.info("Starting price fetch for %d tickers...", len(tickers))
    fetch_universe_prices(
        tickers=tickers,
        start=fetch_start,
        end=fetch_end,
        cache_dir=cache_dir,
        pace_secs=_FETCH_PACE_SECS,
        progress_every=50,
    )
    log.info("=== F400 full universe build DONE ===")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="F400 universe ingest")
    parser.add_argument("--output-dir", default=str(_DEFAULT_MANIFEST_DIR))
    parser.add_argument("--cache-dir", default=str(_DEFAULT_PRICE_CACHE_DIR))
    parser.add_argument("--start", default=_DEFAULT_FETCH_START)
    parser.add_argument("--end", default=_DEFAULT_FETCH_END)
    parser.add_argument("--log-file", default=None)
    args = parser.parse_args()

    run_full_universe_build(
        output_dir=Path(args.output_dir),
        cache_dir=Path(args.cache_dir),
        fetch_start=args.start,
        fetch_end=args.end,
        log_file=Path(args.log_file) if args.log_file else None,
    )
