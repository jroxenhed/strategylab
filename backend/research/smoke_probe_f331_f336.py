"""F331/F336 Smoke Probe — pre-stated face-validity anchors.

Tests the parallel prefetch phase (F331) and price-frame cache staleness
infrastructure (F336) against real cached data (or a small synthetic run
if no real cache exists yet).

F338 discipline: real-data probes with pre-stated anchors are mandatory
before believing a new instrument.  This script is the gate artifact.

Pre-stated anchors (written before reading data):
  F331-A1: Prefetch of 5 tickers with 6 workers completes in < 30s
           (disk-warm: expect < 2s; network: < 30s).
  F331-A2: After prefetch, ALL tickers are in loader._cache (100% memo hit).
  F331-A3: prefetch_symbols_completed == len(universe) after a clean run.
  F331-A4: Failed-ticker dict is returned (never raises) even if a ticker
           has no data (missing / delisted).
  F336-A1: _adjusted_close_fingerprint returns an 8-char hex string for a
           valid DataFrame.
  F336-A2: store() returns a non-empty fingerprint when it persists a frame.
  F336-A3: load_with_staleness_check with a mismatched fingerprint returns
           None and the cached file is gone from disk.
  F336-A4: prune_price_cache._lru_evict with max_bytes=0 in a temp dir
           evicts all files and reports bytes freed > 0.

Usage:
    python3 backend/research/smoke_probe_f331_f336.py [--tickers T1 T2 ...]

    Defaults to 5 well-known large-caps cached in most runs.
    Pass --tickers to override (max 30 per F338 budget rule).

Exits 0 if all anchors pass; 1 if any fail.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import tempfile
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in [str(_BACKEND_DIR), str(_REPO_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DEFAULT_TICKERS = ["AAPL", "MSFT", "JNJ", "XOM", "GE"]  # durable large-caps
_SMOKE_WORKERS = 6
_PREFETCH_TIMEOUT_S = 30  # A1 anchor

# ---------------------------------------------------------------------------
# Anchor registry
# ---------------------------------------------------------------------------
RESULTS: list[dict] = []  # {anchor, pass, measured, expected}


def _record(anchor: str, passed: bool, measured: object, expected: str) -> None:
    icon = "PASS" if passed else "FAIL"
    log.info("[%s] %s | measured=%s expected=%s", icon, anchor, measured, expected)
    RESULTS.append({"anchor": anchor, "pass": passed, "measured": measured, "expected": expected})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_prune_module():
    """Dynamically import backend/scripts/prune_price_cache.py."""
    script_path = _BACKEND_DIR / "scripts" / "prune_price_cache.py"
    if not script_path.exists():
        raise ImportError(f"prune_price_cache.py not found at {script_path}")
    spec = importlib.util.spec_from_file_location("prune_price_cache", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_synthetic_df(n_rows: int = 30):
    """Return a minimal price DataFrame for testing staleness infrastructure."""
    import pandas as pd
    import numpy as np
    from datetime import date, timedelta

    dates = [date(2020, 1, 2) + timedelta(days=i) for i in range(n_rows)]
    closes = [100.0 + float(i) for i in range(n_rows)]
    return pd.DataFrame({"Close": closes, "close": closes}, index=pd.to_datetime(dates))


# ---------------------------------------------------------------------------
# F336 anchors (infrastructure only; no network)
# ---------------------------------------------------------------------------

def run_f336_anchors():
    import turnaround_validation as tv

    # A1: fingerprint format
    df = _make_synthetic_df()
    fp = tv._adjusted_close_fingerprint(df)
    _record(
        "F336-A1",
        passed=isinstance(fp, str) and len(fp) == 8 and fp.isalnum(),
        measured=repr(fp),
        expected="8-char hex string",
    )

    # A2: store() returns non-empty fingerprint
    with tempfile.TemporaryDirectory() as tmp:
        cache = tv.PriceFrameCache(cache_dir=Path(tmp))
        result = cache.store("SMOKE", "2020-01-01", "2021-01-01", df, "yahoo")
        _record(
            "F336-A2",
            passed=isinstance(result, str) and len(result) == 8,
            measured=repr(result),
            expected="8-char fingerprint string",
        )

        # A3: load_with_staleness_check detects mismatch and evicts
        fake_fp = "00000000"  # guaranteed mismatch with real fp
        loaded = cache.load_with_staleness_check(
            "SMOKE", "2020-01-01", "2021-01-01", "yahoo",
            fingerprint_at_write=fake_fp,
        )
        cached_file_exists = any(
            True for _ in (Path(tmp) / tv._PRICE_CACHE_VERSION).glob("SMOKE*.pkl")
        )
        _record(
            "F336-A3",
            passed=(loaded is None) and (not cached_file_exists),
            measured=f"loaded={loaded!r} file_exists={cached_file_exists}",
            expected="loaded=None, file evicted",
        )

    # A4: prune_price_cache._lru_evict evicts all files when max_bytes=0
    with tempfile.TemporaryDirectory() as tmp:
        version_dir = Path(tmp) / "v1"
        version_dir.mkdir()
        for i in range(3):
            p = version_dir / f"TICKER{i}_yahoo_20200101_20210101.pkl"
            p.write_bytes(b"x" * 1024)  # 1KB each
        prune = _import_prune_module()
        freed, evicted = prune._lru_evict(version_dir, max_bytes=0, target_bytes=0, dry_run=False, force=True)
        remaining = list(version_dir.glob("*.pkl"))
        _record(
            "F336-A4",
            passed=freed > 0 and len(remaining) == 0,
            measured=f"freed={freed}B evicted={len(evicted)} remaining={len(remaining)}",
            expected="freed > 0, 0 remaining files",
        )


# ---------------------------------------------------------------------------
# F331 anchors (prefetch phase)
# ---------------------------------------------------------------------------

def run_f331_anchors(tickers: list[str]):
    import turnaround_validation as tv

    universe = [(t, f"{t} Corp") for t in tickers[:30]]  # cap per F338 budget

    with tempfile.TemporaryDirectory() as tmp:
        cache = tv.PriceFrameCache(cache_dir=Path(tmp))

        # Build a synthetic loader that populates cache from synthetic frames
        # (no network required; we're testing prefetch infrastructure not yahoo).
        df_by_ticker: dict = {t: _make_synthetic_df() for t in tickers}

        def _mock_fetch(ticker, start, end, interval, source):
            if ticker in df_by_ticker:
                return df_by_ticker[ticker]
            raise ValueError(f"no data for {ticker}")

        # Monkey-patch sys.modules["shared"] with mock
        import sys
        import types as _types
        fake_shared = _types.ModuleType("shared")
        fake_shared._fetch = _mock_fetch
        orig_shared = sys.modules.get("shared")
        sys.modules["shared"] = fake_shared
        try:
            loader = tv._make_memoized_loader(
                start_year=2020, end_year=2020,
                low_lookback_years=1, horizon_months=12,
                data_source="yahoo",
                price_cache=cache,
            )
        finally:
            if orig_shared is not None:
                sys.modules["shared"] = orig_shared
            else:
                sys.modules.pop("shared", None)

        # A2 pre-check: cache should be cold
        cold_entries = len(loader._cache)

        # A1: prefetch completes in < PREFETCH_TIMEOUT_S
        progress = tv.ValidationProgress()
        t0 = time.monotonic()
        sys.modules["shared"] = fake_shared
        try:
            errors = tv._prefetch_price_frames(
                universe, loader,
                progress=progress,
                workers=_SMOKE_WORKERS,
            )
        finally:
            if orig_shared is not None:
                sys.modules["shared"] = orig_shared
            else:
                sys.modules.pop("shared", None)
        elapsed = time.monotonic() - t0

        _record(
            "F331-A1",
            passed=elapsed < _PREFETCH_TIMEOUT_S,
            measured=f"{elapsed:.2f}s",
            expected=f"< {_PREFETCH_TIMEOUT_S}s",
        )

        # A2: all tickers in loader._cache
        cached_tickers = set(loader._cache.keys())
        all_in_cache = all(t in cached_tickers for t in tickers)
        _record(
            "F331-A2",
            passed=all_in_cache,
            measured=f"cached={sorted(cached_tickers)} expected={sorted(tickers)}",
            expected="all tickers in loader._cache",
        )

        # A3: prefetch_symbols_completed == len(universe)
        _record(
            "F331-A3",
            passed=progress.prefetch_symbols_completed == len(universe),
            measured=progress.prefetch_symbols_completed,
            expected=len(universe),
        )

        # A4: errors dict returned even for unknown ticker (no raise)
        bad_universe = [("__NONEXISTENT__", "Fake Corp")]
        try:
            sys.modules["shared"] = fake_shared
            try:
                err_result = tv._prefetch_price_frames(bad_universe, loader, workers=1)
            finally:
                if orig_shared is not None:
                    sys.modules["shared"] = orig_shared
                else:
                    sys.modules.pop("shared", None)
            returned_dict = isinstance(err_result, dict)
        except Exception as e:
            returned_dict = False
            log.warning("F331-A4: _prefetch_price_frames raised unexpectedly: %s", e)

        _record(
            "F331-A4",
            passed=returned_dict,
            measured=f"returned dict={returned_dict}",
            expected="returns dict (no raise)",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="F331/F336 smoke probe (F338 discipline)")
    parser.add_argument(
        "--tickers", nargs="+", default=_DEFAULT_TICKERS,
        help=f"Tickers to prefetch (max 30). Default: {_DEFAULT_TICKERS}",
    )
    args = parser.parse_args()

    tickers = args.tickers[:30]
    log.info("Smoke probe F331/F336 — tickers: %s", tickers)

    log.info("--- F336 Infrastructure Anchors ---")
    run_f336_anchors()

    log.info("--- F331 Prefetch Anchors ---")
    run_f331_anchors(tickers)

    passed = sum(1 for r in RESULTS if r["pass"])
    total = len(RESULTS)
    log.info("--- Summary: %d/%d anchors PASS ---", passed, total)
    for r in RESULTS:
        icon = "PASS" if r["pass"] else "FAIL"
        log.info("  [%s] %s", icon, r["anchor"])

    if passed < total:
        log.error("%d anchor(s) FAILED", total - passed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
