"""backend/research/universe_loader.py — Shared liquid-universe loader (F358).

ONE canonical definition of the ~4,700-ticker liquid universe used by:
  - returns_matrix._get_universe_tickers (CLI/builder auto-discover)
  - run_r1_explore._build_universe_tickers
  - run_r1b_explore._build_universe_tickers

All three previously carried near-identical ~40-line copies that had begun
to drift (different span-end strings, missing comments). This module is the
consolidation point (F358 review-wave fix, 2026-06-08).

Caller notes
------------
- `run_smoke_study._build_universe_tickers` is deliberately NOT routed here.
  It uses a different span-end (20221231 vs 20211231 — 2012-2022 cache window
  required by the smoke study's 6-calendar-month horizon past 2020-12-31) and
  caps at 50 tickers for fast wall-clock.  Keep those differences; do not
  merge them into this helper.

- `form4_ingest._load_liquid_universe` is also NOT routed here.  It is a
  structurally different loader: returns a {cik_int → primary_ticker} map
  built from build_universe() / fetch_universe(), applies structural
  exclusions (ticker length, junk suffixes, ETF/Trust/SPAC by title), but
  no price/volume floors.  It is the broader CIK-level ingest map.  It must
  remain independent.  The relationship: form4_ingest produces the raw event
  stream; this helper defines the price-covered floor-checked benchmark pool
  those events are evaluated against.

Universe definition (frozen at R-1 charter, do NOT change post-outcome)
-----------------------------------------------------------------------
A ticker T is in the universe iff:
  1. T has an on-disk PriceFrameCache pickle whose filename encodes:
       start_date <= span_end_exclusive and end_date >= span_end_exclusive
     using the caller-supplied `span_end` argument (default "20211231").
     (R-1/R-1b use "20211231"; different studies may use different values.)
  2. T appears in the EDGAR submissions cache (submissions/*.json) with a
     non-zero SIC code (sic field present, non-empty, int != 0).
     Sorted over submission files → deterministic ordering (reproducibility).
  3. (Optional) caller-supplied `extra_tickers`: tickers that must appear in
     the universe for SIC look-up even if they were not in the SIC scan.
     F349 CRITICAL: absent event tickers force 100% universe fallback in
     _load_ticker_to_sic; guarantee inclusion for any ticker in the covering set.

Canonical: returns_matrix._get_universe_tickers (accepted F357 matrix build).
Any behavioral difference collapsed from the three copies is documented below.

Differences collapsed
---------------------
- run_r1_explore / run_r1b_explore used module-level _PRICE_SPAN_START /
  _PRICE_SPAN_END constants; returns_matrix used local variables.  All three
  had identical values ("20120101" / "20211231").  Unified here as defaults.
- returns_matrix._get_universe_tickers logged "Price-cache covering set
  (2012-2021 span)" — exact same log message now emitted from the helper
  with the caller-supplied span_end substituted.
- All three iterated sorted(subs_dir.iterdir()) for deterministic ordering.
  Preserved verbatim.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical span constants (R-1 / R-1b / returns_matrix default)
# These define the price-cache window that qualifies a ticker for the
# ~4,700-ticker universe.  Change only if re-chartering a new study.
# ---------------------------------------------------------------------------
_DEFAULT_SPAN_START = "20120101"
_DEFAULT_SPAN_END = "20211231"


def build_liquid_universe(
    price_cache_dir: Path,
    subs_dir: Path,
    span_start: str = _DEFAULT_SPAN_START,
    span_end: str = _DEFAULT_SPAN_END,
    extra_tickers: Sequence[str] | None = None,
) -> list[str]:
    """Return tickers in the liquid universe for the given price-cache span.

    Args:
        price_cache_dir: Path to the price cache "v1" directory that contains
            ``<ticker>_<...>_<start>_<end>.pkl`` files.  Must be the v1 sub-dir
            (e.g. ``backend/data/turnaround/price_cache/v1``).
        subs_dir: Path to the EDGAR submissions directory
            (``backend/data/turnaround/edgar_cache/submissions``).
        span_start: Earliest start date a cache file may have to qualify
            (lexicographic YYYYMMDD comparison, default "20120101").
        span_end: Latest end date a cache file must reach to qualify
            (lexicographic YYYYMMDD comparison, default "20211231").
        extra_tickers: Optional sequence of tickers that MUST appear in the
            returned list if they are present in the covering set, even if
            they are not in the SIC scan (F349 CRITICAL guard).  Pass the
            set of event tickers here.

    Returns:
        Sorted-by-submission list of tickers: SIC-bearing, price-covered,
        with any extra_tickers appended if not already present.

    Raises:
        FileNotFoundError: if price_cache_dir or subs_dir does not exist.
    """
    if not price_cache_dir.exists():
        raise FileNotFoundError(f"Price cache not found: {price_cache_dir}")
    if not subs_dir.exists():
        raise FileNotFoundError(f"Submissions dir not found: {subs_dir}")

    # Step 1: collect tickers with full price coverage [span_start, span_end]
    covering: set[str] = set()
    for f in price_cache_dir.iterdir():
        if not f.name.endswith(".pkl"):
            continue
        parts = f.stem.split("_")
        # Filename convention: <ticker_key>_<source>_<start>_<end>.pkl
        # The ticker key itself may contain underscores (safe_ticker + crc);
        # start and end are always 8-digit YYYYMMDD strings at positions [-2] / [-1].
        # Using parts[-2]/parts[-1] is robust to multi-part ticker keys (C1/COR-01/DI-07).
        if len(parts) < 5:
            continue
        ticker = parts[0]
        start = parts[-2]
        end = parts[-1]
        if start <= span_start and end >= span_end:
            covering.add(ticker)

    log.info(
        "Price-cache covering set (%s-%s span): %d tickers",
        span_start,
        span_end,
        len(covering),
    )

    # Step 2: keep those with non-zero SIC in EDGAR submissions.
    # sorted() over subs_dir for deterministic, reproducible ordering.
    universe: list[str] = []
    for subs_file in sorted(subs_dir.iterdir()):
        if not subs_file.name.endswith(".json"):
            continue
        try:
            d = json.loads(subs_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        tickers = d.get("tickers", [])
        if not tickers:
            continue
        ticker = tickers[0]
        if ticker not in covering:
            continue
        sic = d.get("sic")
        if sic:
            # C4/PY-06: guard against non-numeric SIC fields (e.g. " ", "N/A") that
            # would raise ValueError/TypeError and crash the entire universe build.
            try:
                sic_int = int(sic)
            except (ValueError, TypeError):
                log.debug("Skipping ticker %s — malformed SIC value: %r", ticker, sic)
                continue
            if sic_int != 0:
                universe.append(ticker)

    # Step 3 (F349 CRITICAL): event tickers MUST be in universe_tickers so
    # that _load_ticker_to_sic() can return their SIC codes.  A missing event
    # ticker forces 100% universe fallback (ticker_to_sic[event_ticker] = None
    # → 3-digit and 2-digit cascade both fail → universe fallback for 100% of
    # that ticker's events).
    universe_set = set(universe)
    n_added = 0
    if extra_tickers:
        for et in extra_tickers:
            if et in covering and et not in universe_set:
                universe.append(et)
                universe_set.add(et)
                n_added += 1

    if n_added > 0:
        log.info(
            "Extra tickers added to universe (price-covered but not in SIC scan): %d",
            n_added,
        )

    log.info(
        "Universe tickers (SIC-bearing, %s-%s cache): %d",
        span_start,
        span_end,
        len(universe),
    )
    return universe
