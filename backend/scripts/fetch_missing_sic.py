"""F349: Bulk EDGAR submissions cache extension for SIC coverage.

Reads the cached universe tickers, finds those whose submission JSON is
missing from edgar_cache/submissions/, and fetches them via edgar.py
(reuses edgar._get rate limiter and politeness headers — never rolls its own
HTTP).  Skips any file that already exists and is fresh (TTL 1 day per
edgar.py).

Usage:
    python3 backend/scripts/fetch_missing_sic.py [--limit N] [--dry-run]

    --limit N     Stop after fetching N new files (useful for smoke-probe).
    --dry-run     Print what would be fetched; do not fetch.

Run this BEFORE the first charter-run event_study when SIC coverage is
needed.  Cost: ~2400 requests at ≤10 req/s ≈ 4 minutes.

Notes:
    - Relies on universe.json being present (fetched by edgar.fetch_universe).
    - Convention: most standalone scripts in this repo live in
      backend/research/ (see build_null_atlas.py); this one is in
      backend/scripts/ because the scripts/ dir was created for F349.
    - DI-02: Do NOT run two instances concurrently.  Both will enumerate the same
      missing set and double the EDGAR request count.  The edgar._get rate limiter
      is shared within one process but not across processes.  Single-operator repo;
      no file locking added — just don't run in parallel.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running from any cwd (repo root or backend/).
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import edgar  # noqa: E402  — after sys.path patch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_SUBMISSIONS_DIR = edgar.CACHE_DIR / "submissions"
_UNIVERSE_PATH = edgar.CACHE_DIR / "universe.json"


def _load_universe() -> dict[str, dict]:
    """Load universe.json (requires prior edgar.fetch_universe() call or live fetch)."""
    if not _UNIVERSE_PATH.exists():
        log.info("universe.json not found — fetching from EDGAR...")
        return edgar.fetch_universe()
    return json.loads(_UNIVERSE_PATH.read_text(encoding="utf-8"))


def _cik_for_ticker(universe: dict, ticker: str) -> str | None:
    """Extract zero-padded 10-digit CIK from the universe dict for a given ticker."""
    # universe.json may be in normalized form {TICKER: {"cik_str": ...}} or
    # raw SEC form {"0": {"cik_str": int, "ticker": ...}}.
    sample = next(iter(universe.values()), {})
    if "ticker" in sample:
        # Raw SEC format: look up by scanning.
        for entry in universe.values():
            if str(entry.get("ticker", "")).upper() == ticker.upper():
                raw_cik = entry.get("cik_str", 0)
                return str(raw_cik).zfill(10)
        return None
    else:
        # Normalized: {TICKER: {"cik_str": "0000320193", ...}}
        entry = universe.get(ticker.upper())
        if entry is None:
            return None
        raw_cik = entry.get("cik_str", 0)
        return str(raw_cik).zfill(10)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-fetch missing EDGAR submissions for SIC coverage extension (F349)."
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Stop after fetching N new files (default: fetch all missing).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be fetched without actually fetching.",
    )
    args = parser.parse_args(argv)

    universe = _load_universe()
    log.info("Universe loaded: %d entries", len(universe))

    # DI-01: sweep orphan .tmp files left by interrupted _atomic_write calls.
    _SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    orphans = list(_SUBMISSIONS_DIR.glob("*.json.tmp"))
    if orphans:
        for tmp_path in orphans:
            try:
                tmp_path.unlink()
            except Exception as exc:
                log.warning("Could not remove orphan tmp file %s: %s", tmp_path, exc)
        log.info("Swept %d orphan .tmp file(s) from submissions dir.", len(orphans))

    # Enumerate tickers missing a fresh submission file.
    missing: list[tuple[str, str]] = []  # [(ticker, cik), ...]

    # Handle both universe.json formats.
    sample = next(iter(universe.values()), {})
    if "ticker" in sample:
        # Raw SEC format
        tickers_in_universe = [
            (str(v.get("ticker", "")).upper(), str(v.get("cik_str", 0)).zfill(10))
            for v in universe.values()
            if v.get("ticker")
        ]
    else:
        # Normalized format
        tickers_in_universe = [
            (t.upper(), str(info.get("cik_str", 0)).zfill(10))
            for t, info in universe.items()
            if t
        ]

    for ticker, cik in tickers_in_universe:
        sub_path = _SUBMISSIONS_DIR / f"{cik}.json"
        # Skip if file exists and is within TTL (1 day per edgar._SUBMISSIONS_TTL_DAYS).
        if edgar._cache_valid(sub_path, edgar._SUBMISSIONS_TTL_DAYS):
            continue
        missing.append((ticker, cik))

    total_missing = len(missing)
    log.info(
        "Submissions missing or stale: %d / %d tickers",
        total_missing, len(tickers_in_universe),
    )

    if args.dry_run:
        limit = args.limit or total_missing
        shown = missing[:limit]
        for ticker, cik in shown:
            print(f"  would fetch: {ticker} (CIK={cik})")
        print(f"\nDry run: would fetch {len(shown)} / {total_missing} missing submissions.")
        return

    fetched = 0
    skipped_error = 0
    limit = args.limit if args.limit is not None else total_missing

    for ticker, cik in missing[:limit]:
        try:
            # edgar.fetch_submissions uses edgar._get (rate-limited ≤10 req/s) and
            # caches to edgar_cache/submissions/{cik}.json with TTL 1 day.
            edgar.fetch_submissions(cik)
            fetched += 1
            if fetched % 50 == 0:
                log.info("Progress: %d / %d fetched", fetched, min(limit, total_missing))
        except Exception as exc:
            log.warning("Failed to fetch CIK %s (%s): %s", cik, ticker, exc)
            skipped_error += 1

    # Summary
    final_count = sum(
        1 for _, cik in tickers_in_universe
        if edgar._cache_valid(_SUBMISSIONS_DIR / f"{cik}.json", edgar._SUBMISSIONS_TTL_DAYS)
    )
    log.info(
        "Done. Fetched: %d new, %d errors. "
        "Submissions cache now covers %d / %d tickers (%.1f%%).",
        fetched, skipped_error,
        final_count, len(tickers_in_universe),
        100.0 * final_count / len(tickers_in_universe) if tickers_in_universe else 0,
    )


if __name__ == "__main__":
    main()
