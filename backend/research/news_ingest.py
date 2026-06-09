"""F402 — GDELT News Volume + Tone Ingest.

SCALE CONSTRAINT (F402 / Phase 0):
    Per-ticker GDELT DOC API queries are bounded to a curated subset (liquid
    universe / names passing the UNIVERSE_V2 floor — approximately 4,700 names).
    Running the full 12k-name nasdaqtraded.txt universe would take 34–550 hours
    at GDELT's 1 req/5s rate limit (2 calls per ticker × 12k tickers = ~28h
    minimum at perfect throughput; in practice 3–5× longer due to retries and
    entity-mapping overhead).
    Full 12k-name coverage requires migrating to GDELT GKG bulk files — tracked
    as F405. News for illiquid micro-caps is sparse and low-value anyway.
    The build_news_panel() entry point raises ValueError if caller passes more
    than _MAX_TICKERS_PER_RUN tickers without explicit override.

Fetches per-company daily **news volume intensity** and **average tone** from
the GDELT DOC 2.0 API and outputs a tidy parquet panel joinable to the returns
matrix on (ticker, date).

Public API
----------
    build_news_panel(tickers, start, end, ...)
        → (pd.DataFrame, meta_dict)

    fetch_ticker_series(ticker, start, end, ...)
        → pd.DataFrame with columns [ticker, date, news_volume, avg_tone]

Design notes
------------
GDELT endpoints used:
  - Volume:  DOC 2.0 `mode=timelinevol`  → series "Volume Intensity"
             (normalised intensity relative to GDELT's full corpus; NOT a raw
             article count — this is an important honesty caveat)
  - Tone:    DOC 2.0 `mode=timelinetone` → series "Average Tone"
             (GDELT tone scale: ~-10 = very negative, ~+10 = very positive,
             0 = neutral)

Rate limit: GDELT enforces ~1 req / 5s per IP. The module paces at 6s between
requests (conservative), implements exponential-backoff retries, and caches raw
API responses to disk so re-runs are free.

Ticker → company entity mapping strategy (THE dominant risk — see honesty note):
  Priority order:
    1. Universe manifest parquet if available (F400 output)
    2. yfinance Ticker.info["longName"]
    3. Hardcoded fallback map for common tickers
  The query string sent to GDELT is the company's long name (or a cleaned
  variant). This is imprecise: a query for "Apple Inc" will match any article
  mentioning Apple — including Apple Records, apple orchards, or unrelated Apple
  entities in non-English sources. Entity-mapping false matches are the #1 risk
  for this instrument.

Output schema
-------------
parquet columns: ticker (str), date (date), news_volume (float64), avg_tone (float64)
  - news_volume: GDELT "Volume Intensity" — a relative intensity in [0, ~5];
    0 = no coverage, values > 1 = above-average corpus coverage
  - avg_tone: GDELT "Average Tone" — negative = negative news, positive = positive
  - date: publication date (PIT field — the date articles appeared, not any
    described event date)

Metadata sidecar (JSON) fields:
  source, fetch_vintage, survivorship, coverage_start, coverage_end,
  pit_field, n_rows, n_tickers, mapping_method, honesty_note
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_UA = "StrategyLab research john@milford.se"
_PACE_SECS = 6.0          # conservative: GDELT rate limit is 1 req/5s
_MAX_RETRIES = 3
_BACKOFF_BASE = 15.0      # first retry wait in seconds; doubles each retry
_DEFAULT_OUTPUT_DIR = _BACKEND_DIR / "data" / "gdelt"

# Maximum tickers accepted by build_news_panel() without explicit override.
# Per-ticker GDELT DOC API at 6s/req × 2 calls = 12s per ticker.
# 5,000 tickers ≈ 17h — already at the upper edge of feasible; 12k would be 40h+.
# See module docstring and F405 for GKG bulk migration.
_MAX_TICKERS_PER_RUN = 5_000

# Universe manifest location (F400 output — may or may not exist)
_UNIVERSE_MANIFEST_GLOB = str(
    _BACKEND_DIR.parent / ".run" / "F-BATCH-0609" / "universe_manifest*.parquet"
)

# ---------------------------------------------------------------------------
# Hardcoded fallback entity map for common tickers
# Key = uppercase ticker, Value = GDELT query string
# ---------------------------------------------------------------------------
_FALLBACK_ENTITY_MAP: dict[str, str] = {
    "AAPL": "Apple Inc",
    "MSFT": "Microsoft Corporation",
    "GOOGL": "Alphabet Google",
    "GOOG": "Alphabet Google",
    "AMZN": "Amazon.com",
    "META": "Meta Platforms Facebook",
    "NVDA": "NVIDIA Corporation",
    "TSLA": "Tesla Motors",
    "JPM": "JPMorgan Chase",
    "JNJ": "Johnson Johnson",
    "WMT": "Walmart",
    "BAC": "Bank of America",
    "XOM": "Exxon Mobil",
    "PG": "Procter Gamble",
    "MA": "Mastercard",
    "HD": "Home Depot",
    "UNH": "UnitedHealth Group",
    "V": "Visa",
    "CVX": "Chevron",
    "MRK": "Merck",
    "ABBV": "AbbVie",
    "PFE": "Pfizer",
    "LLY": "Eli Lilly",
    "GME": "GameStop",
    "AMC": "AMC Entertainment",
    "RIVN": "Rivian Automotive",
    "PLTR": "Palantir Technologies",
    "COIN": "Coinbase",
    "SVBF": "Silicon Valley Bank",
    "SIVB": "Silicon Valley Bank",
    "SPY": "S&P 500 stock market",
    "QQQ": "Nasdaq technology stocks",
}

# ---------------------------------------------------------------------------
# Rate-limit pacing (global last-fetch tracker)
# ---------------------------------------------------------------------------
_last_request_time: float = 0.0


def _pace() -> None:
    """Sleep if needed to stay within GDELT's 1-req/5s rate limit."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    wait = max(0.0, _PACE_SECS - elapsed)
    if wait > 0:
        log.debug("_pace: sleeping %.1fs", wait)
        time.sleep(wait)
    _last_request_time = time.monotonic()


# ---------------------------------------------------------------------------
# On-disk response cache
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, mode: str, query: str, start: str, end: str) -> Path:
    """Return deterministic cache file path for a GDELT request."""
    safe_query = urllib.parse.quote(query, safe="")[:60]
    fname = f"{mode}__{safe_query}__{start}__{end}.json"
    return cache_dir / fname


def _load_cache(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("_load_cache: stale/corrupt %s: %s", path, exc)
        return None


def _save_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# GDELT DOC 2.0 API fetch
# ---------------------------------------------------------------------------

def _gdelt_fetch(
    mode: str,
    query: str,
    start_dt: str,
    end_dt: str,
    *,
    cache_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Fetch one GDELT DOC 2.0 timeline series.

    Parameters
    ----------
    mode : str
        "timelinevol" or "timelinetone"
    query : str
        Company entity search string (e.g. "Apple Inc")
    start_dt, end_dt : str
        GDELT datetime format: "YYYYMMDDHHMMSS"
    cache_dir : Path, optional
        If set, cache raw responses here (on-disk TTL = indefinite for
        historical data; always returns cached result if present).

    Returns
    -------
    dict or None
        Parsed JSON response from GDELT, or None on failure.
    """
    if cache_dir is not None:
        cpath = _cache_path(cache_dir, mode, query, start_dt, end_dt)
        cached = _load_cache(cpath)
        if cached is not None:
            log.debug("_gdelt_fetch: cache hit %s", cpath.name)
            return cached

    params = {
        "query": query,
        "mode": mode,
        "format": "json",
        "startdatetime": start_dt,
        "enddatetime": end_dt,
    }
    url = _GDELT_BASE + "?" + urllib.parse.urlencode(params)
    log.debug("_gdelt_fetch: GET %s", url)

    for attempt in range(_MAX_RETRIES):
        _pace()
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = _BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "_gdelt_fetch: 429 rate-limit (attempt %d/%d), waiting %.0fs",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(wait)
                continue
            log.warning("_gdelt_fetch: HTTPError %d for query=%r: %s", exc.code, query, exc)
            return None
        except Exception as exc:
            log.warning("_gdelt_fetch: error for query=%r: %s", query, exc)
            return None

        # Parse response
        if not raw.strip().startswith("{"):
            # GDELT returns HTML error pages for invalid modes
            log.warning("_gdelt_fetch: non-JSON response for mode=%s query=%r: %s",
                        mode, query, raw[:200])
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("_gdelt_fetch: JSON parse error: %s", exc)
            return None

        # Cache on success
        if cache_dir is not None:
            _save_cache(cpath, parsed)

        return parsed

    log.error("_gdelt_fetch: all %d retries exhausted for mode=%s query=%r",
              _MAX_RETRIES, mode, query)
    return None


# ---------------------------------------------------------------------------
# Ticker → entity name resolution
# ---------------------------------------------------------------------------

_NAME_SUFFIXES_TO_STRIP = [
    # Security-type and exchange suffixes common in NASDAQ trader directory names
    " - Common Stock",
    " Common Stock",
    " Depositary Shares",
    " Depositary Receipt",
    "- Common Stock",
    " Class A",
    " Class B",
    " Class C",
    " Ordinary Shares",
    " American Depositary Shares",
]


def _clean_name_for_gdelt(raw_name: str) -> str:
    """Strip exchange/security-type suffixes from raw manifest names.

    NASDAQ trader directory names include suffixes like '- Common Stock' or
    'Common Stock' that are not part of the company's media name and would
    reduce GDELT article match rates if included in the query.

    Examples:
        'Apple Inc. - Common Stock' → 'Apple Inc.'
        'Microsoft Corporation - Common Stock' → 'Microsoft Corporation'
        'GameStop Corporation Common Stock' → 'GameStop Corporation'
    """
    name = raw_name.strip()
    # Strip known suffixes (longest first to avoid partial matches)
    for suffix in sorted(_NAME_SUFFIXES_TO_STRIP, key=len, reverse=True):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name or raw_name  # never return empty string


def _load_universe_name_map() -> dict[str, str]:
    """Load ticker → company name from F400 universe manifest if available.

    Applies _clean_name_for_gdelt() to strip security-type suffixes before
    returning so queries go to GDELT without the '- Common Stock' noise.

    Returns empty dict if the manifest doesn't exist yet.
    """
    import glob
    matches = glob.glob(_UNIVERSE_MANIFEST_GLOB)
    if not matches:
        return {}
    manifest_path = sorted(matches)[-1]  # latest if multiple
    try:
        df = pd.read_parquet(manifest_path, columns=["ticker", "name"])
        result = {
            row.ticker.upper(): _clean_name_for_gdelt(str(row.name))
            for row in df.itertuples()
            if row.name and str(row.name).strip()
        }
        log.info("_load_universe_name_map: loaded %d entries from %s",
                 len(result), manifest_path)
        return result
    except Exception as exc:
        log.warning("_load_universe_name_map: failed to load %s: %s", manifest_path, exc)
        return {}


def _yfinance_longname(ticker: str) -> Optional[str]:
    """Fetch company longName from yfinance Ticker.info."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return info.get("longName") or info.get("shortName")
    except Exception as exc:
        log.debug("_yfinance_longname: failed for %s: %s", ticker, exc)
        return None


def resolve_entity_query(
    ticker: str,
    universe_name_map: Optional[dict[str, str]] = None,
) -> tuple[str, str]:
    """Resolve a ticker to a GDELT entity query string.

    Resolution order:
      1. Universe manifest (F400) — most reliable, company names from NASDAQ trader directory
      2. yfinance Ticker.info["longName"]
      3. Hardcoded fallback map
      4. Raw ticker symbol (last resort — high false-match risk)

    Returns
    -------
    (query_str, mapping_source) where mapping_source ∈
      {"universe_manifest", "yfinance", "hardcoded_fallback", "raw_ticker"}

    Notes
    -----
    The query string is the dominant risk in this instrument. A query for
    "Apple Inc" in GDELT will match ALL articles containing those words,
    including articles about unrelated "Apple" entities in non-English sources,
    articles mentioning both Apple and another company, etc. This is aggregate
    document-level tone/volume, not entity-disambiguated NER extraction.
    """
    t = ticker.upper()

    # 1. Universe manifest
    if universe_name_map is not None:
        name = universe_name_map.get(t)
        if name:
            return name, "universe_manifest"

    # 2. yfinance
    name = _yfinance_longname(ticker)
    if name:
        return name, "yfinance"

    # 3. Hardcoded fallback
    if t in _FALLBACK_ENTITY_MAP:
        return _FALLBACK_ENTITY_MAP[t], "hardcoded_fallback"

    # 4. Raw ticker (last resort)
    log.warning("resolve_entity_query: no name found for %s, using raw ticker", ticker)
    return ticker, "raw_ticker"


# ---------------------------------------------------------------------------
# Series parser
# ---------------------------------------------------------------------------

def _parse_timeline_series(data: Optional[dict], series_name: str) -> list[tuple[date, float]]:
    """Extract (date, value) pairs from a GDELT timeline response.

    Parameters
    ----------
    data : dict or None
        Parsed JSON from _gdelt_fetch.
    series_name : str
        Expected series name substring (e.g. "Volume", "Tone").

    Returns
    -------
    List of (date, float) pairs in date order. Returns [] on any error or
    missing data — never raises.
    """
    if data is None:
        return []
    timeline = data.get("timeline", [])
    if not timeline:
        return []

    # Find matching series (case-insensitive substring match)
    target_series = None
    for series in timeline:
        if series_name.lower() in series.get("series", "").lower():
            target_series = series
            break

    if target_series is None:
        log.debug("_parse_timeline_series: series %r not found in %s",
                  series_name, [s.get("series") for s in timeline])
        return []

    result = []
    for point in target_series.get("data", []):
        raw_date = point.get("date", "")
        value = point.get("value")
        if not raw_date or value is None:
            continue
        # GDELT date format: "20230101T000000Z"
        try:
            parsed_date = datetime.strptime(raw_date[:8], "%Y%m%d").date()
        except ValueError:
            log.debug("_parse_timeline_series: unparseable date %r", raw_date)
            continue
        try:
            result.append((parsed_date, float(value)))
        except (TypeError, ValueError):
            continue

    return result


# ---------------------------------------------------------------------------
# Per-ticker fetch
# ---------------------------------------------------------------------------

def fetch_ticker_series(
    ticker: str,
    start: date,
    end: date,
    *,
    cache_dir: Optional[Path] = None,
    universe_name_map: Optional[dict[str, str]] = None,
) -> tuple[pd.DataFrame, dict]:
    """Fetch GDELT news volume + tone for one ticker over [start, end].

    Returns
    -------
    df : pd.DataFrame
        Columns: ticker, date, news_volume, avg_tone
        Indexed by date. May be empty if GDELT returns nothing for this entity.
    meta : dict
        entity_query, mapping_source, vol_points, tone_points, notes
    """
    # Resolve company name
    query, mapping_source = resolve_entity_query(ticker, universe_name_map)

    # GDELT datetime strings: "YYYYMMDDHHMMSS"
    start_dt = start.strftime("%Y%m%d") + "000000"
    # End is exclusive in GDELT — add one day
    end_plus1 = end + timedelta(days=1)
    end_dt = end_plus1.strftime("%Y%m%d") + "000000"

    log.info("fetch_ticker_series: %s → query=%r [%s → %s]", ticker, query, start_dt, end_dt)

    # Fetch volume series
    vol_data = _gdelt_fetch("timelinevol", query, start_dt, end_dt, cache_dir=cache_dir)
    vol_points = _parse_timeline_series(vol_data, "Volume")

    # Fetch tone series (separate call, pace between)
    tone_data = _gdelt_fetch("timelinetone", query, start_dt, end_dt, cache_dir=cache_dir)
    tone_points = _parse_timeline_series(tone_data, "Tone")

    # Merge on date
    vol_map = dict(vol_points)
    tone_map = dict(tone_points)
    all_dates = sorted(set(vol_map.keys()) | set(tone_map.keys()))

    if not all_dates:
        log.warning("fetch_ticker_series: no data for ticker=%s query=%r", ticker, query)
        empty = pd.DataFrame(columns=["ticker", "date", "news_volume", "avg_tone"])
        return empty, {
            "ticker": ticker,
            "entity_query": query,
            "mapping_source": mapping_source,
            "vol_points": 0,
            "tone_points": 0,
            "notes": "no_data",
        }

    rows = []
    for d in all_dates:
        rows.append({
            "ticker": ticker,
            "date": d,
            "news_volume": vol_map.get(d),   # None if vol call failed
            "avg_tone": tone_map.get(d),      # None if tone call failed
        })

    df = pd.DataFrame(rows)
    df["news_volume"] = pd.to_numeric(df["news_volume"], errors="coerce")
    df["avg_tone"] = pd.to_numeric(df["avg_tone"], errors="coerce")

    meta = {
        "ticker": ticker,
        "entity_query": query,
        "mapping_source": mapping_source,
        "vol_points": len(vol_points),
        "tone_points": len(tone_points),
        "notes": "ok",
    }
    return df, meta


# ---------------------------------------------------------------------------
# Multi-ticker panel builder
# ---------------------------------------------------------------------------

def build_news_panel(
    tickers: list[str],
    start: date,
    end: date,
    *,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    cache_dir: Optional[Path] = None,
    save_parquet: bool = True,
    allow_large: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Build daily news panel for a list of tickers.

    Fetches GDELT volume + tone for each ticker and writes:
      - ``{output_dir}/news_panel.parquet``
      - ``{output_dir}/news_panel_meta.json``

    SCALE NOTE: bounded to ≤_MAX_TICKERS_PER_RUN tickers (default 5,000) to
    prevent accidental full-12k-universe runs (would take 34–550 hours via the
    per-ticker GDELT DOC API). Pass allow_large=True only with an explicit,
    intentional large-batch configuration. For full 12k coverage, migrate to
    GDELT GKG bulk files (tracked as F405).

    Parameters
    ----------
    tickers : list[str]
        Uppercase ticker symbols. Must be a curated subset (liquid universe);
        passing the full nasdaqtraded.txt 12k list will raise ValueError.
    start, end : date
        Inclusive date range for the panel.
    output_dir : Path
        Where to write parquet + metadata sidecar.
    cache_dir : Path, optional
        Directory for raw GDELT response cache. Defaults to
        ``{output_dir}/gdelt_cache/`` if save_parquet is True.
    save_parquet : bool
        If False, skip disk writes (useful for probe runs where output goes
        to a custom path).
    allow_large : bool
        If True, bypass the _MAX_TICKERS_PER_RUN guard. Only set this when
        intentionally running a large batch with an explicit ticker list and
        adequate wall-clock budget (see F405 for GKG bulk alternative).

    Returns
    -------
    panel : pd.DataFrame
        Full panel, columns: [ticker, date, news_volume, avg_tone]
    meta : dict
        Standard sidecar fields + per-ticker mapping details.
    """
    if not allow_large and len(tickers) > _MAX_TICKERS_PER_RUN:
        raise ValueError(
            f"build_news_panel: tickers list has {len(tickers)} symbols, exceeding the "
            f"_MAX_TICKERS_PER_RUN={_MAX_TICKERS_PER_RUN} guard. "
            f"Per-ticker GDELT DOC API at this scale would take 34–550 hours. "
            f"Use a curated subset (liquid universe, ~4,700 names) or pass "
            f"allow_large=True to intentionally override. "
            f"For full 12k coverage, use GDELT GKG bulk files (tracked as F405)."
        )

    if cache_dir is None and save_parquet:
        cache_dir = output_dir / "gdelt_cache"

    # Load universe manifest once
    universe_name_map = _load_universe_name_map()
    log.info(
        "build_news_panel: %d tickers, %s→%s, universe_map_entries=%d",
        len(tickers), start, end, len(universe_name_map),
    )

    all_frames: list[pd.DataFrame] = []
    per_ticker_meta: list[dict] = []
    n_failed = 0

    for i, ticker in enumerate(tickers):
        log.info("  [%d/%d] %s", i + 1, len(tickers), ticker)
        df_t, tmeta = fetch_ticker_series(
            ticker, start, end,
            cache_dir=cache_dir,
            universe_name_map=universe_name_map,
        )
        per_ticker_meta.append(tmeta)
        if df_t.empty:
            n_failed += 1
        else:
            all_frames.append(df_t)

    if all_frames:
        panel = pd.concat(all_frames, ignore_index=True)
        panel["date"] = pd.to_datetime(panel["date"]).dt.date
    else:
        panel = pd.DataFrame(columns=["ticker", "date", "news_volume", "avg_tone"])

    # Build metadata sidecar
    coverage_dates = sorted(panel["date"].dropna().unique()) if not panel.empty else []
    meta = {
        "source": "GDELT",
        "source_detail": "GDELT DOC 2.0 API (timelinevol + timelinetone modes)",
        "fetch_vintage": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "survivorship": "survivors-only",
        "coverage_start": str(coverage_dates[0]) if coverage_dates else str(start),
        "coverage_end": str(coverage_dates[-1]) if coverage_dates else str(end),
        "pit_field": "date",
        "n_rows": len(panel),
        "n_tickers": panel["ticker"].nunique() if not panel.empty else 0,
        "n_tickers_requested": len(tickers),
        "n_tickers_failed": n_failed,
        "mapping_method": (
            "Ticker→company resolved via: 1) F400 universe manifest name, "
            "2) yfinance Ticker.info['longName'], "
            "3) hardcoded fallback map, 4) raw ticker. "
            "Query is full company name passed to GDELT full-text search."
        ),
        "honesty_note": (
            "AGGREGATE VOLUME/TONE ONLY, NOT ARTICLE CONTENT. "
            "news_volume is GDELT 'Volume Intensity' — a normalized corpus-relative "
            "intensity, NOT a raw article count. Entity-mapping false matches are the "
            "dominant risk: a query for 'Apple Inc' matches ANY article mentioning "
            "that string, including non-English sources, partial mentions, and unrelated "
            "entities. This instrument should be used as a noisy proxy for news attention, "
            "not as a precise measure of company-specific coverage."
        ),
        "per_ticker": per_ticker_meta,
    }

    if save_parquet and not panel.empty:
        output_dir.mkdir(parents=True, exist_ok=True)
        panel_path = output_dir / "news_panel.parquet"
        meta_path = output_dir / "news_panel_meta.json"
        panel.to_parquet(panel_path, index=False)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log.info("build_news_panel: wrote %d rows to %s", len(panel), panel_path)

    return panel, meta


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="F402 GDELT news ingest")
    parser.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT", "TSLA"],
                        help="Ticker symbols to fetch")
    parser.add_argument("--start", default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2023-03-31", help="End date YYYY-MM-DD")
    parser.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR),
                        help="Output directory for parquet + metadata")
    args = parser.parse_args()

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    out = Path(args.output_dir)

    panel, meta = build_news_panel(
        args.tickers, start_d, end_d,
        output_dir=out,
        save_parquet=True,
    )

    print(f"\nPanel shape: {panel.shape}")
    print(panel.head(10).to_string())
    print(f"\nMetadata: source={meta['source']}, rows={meta['n_rows']}, "
          f"tickers={meta['n_tickers']}")
    print(f"Output: {out / 'news_panel.parquet'}")
