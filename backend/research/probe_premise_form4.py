"""F338 real-data smoke probe for the form4 stream (F388).

Pre-stated anchors verified against the submissions cache before implementation.
Prints PASS/FAIL for each anchor with expected vs actual.
Exits 0 if all pass, 1 otherwise.

Run:
    backend/venv/bin/python3 backend/research/probe_premise_form4.py

F338 discipline: green synthetic tests are NOT sufficient for new instruments.
Before output is interpreted or committed, run this probe and check the anchors.
"""
from __future__ import annotations

import sys
import os
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from research.event_study import iter_form4_events, _SUBMISSIONS_DIR  # noqa: E402
from research.streams import _REGISTRY                                 # noqa: E402
from research.streams.form4 import Form4Stream                         # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = "PASS"
_FAIL = "FAIL"
_results: list[tuple[str, str]] = []  # (label, PASS|FAIL)


def _check(label: str, condition: bool, expected: str, actual: str) -> None:
    status = _PASS if condition else _FAIL
    _results.append((label, status))
    print(f"  [{status}] {label}")
    print(f"         expected: {expected}")
    print(f"         actual:   {actual}")


# ---------------------------------------------------------------------------
# Pre-flight: verify submissions cache is reachable
# ---------------------------------------------------------------------------

def _check_cache() -> bool:
    if not _SUBMISSIONS_DIR.exists():
        print(f"  [SKIP] Submissions cache not found: {_SUBMISSIONS_DIR}")
        print("         Cannot run real-data anchors on this machine.")
        return False
    count = sum(1 for f in _SUBMISSIONS_DIR.iterdir() if f.name.endswith(".json"))
    print(f"  [INFO] Submissions dir: {_SUBMISSIONS_DIR}")
    print(f"  [INFO] CIK files found: {count}")
    return True


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------

def anchor_a() -> None:
    """Anchor A: AAPL single-CIK window count (2019-01-01..2020-12-31 → 78 events)."""
    print("\nAnchor A: AAPL CIK 0000320193, 2019-01-01..2020-12-31 → exactly 78 events")
    events = list(iter_form4_events(
        cik_list=["0000320193"],
        start=date(2019, 1, 1),
        end=date(2020, 12, 31),
    ))
    actual = len(events)
    _check(
        "Anchor A: AAPL 2019-2020 event count",
        actual == 78,
        expected="78",
        actual=str(actual),
    )


def anchor_b() -> None:
    """Anchor B: all-cache 2019 count sanity (expected: 50k..200k, ~103,782)."""
    print("\nAnchor B: all CIKs, 2019-01-01..2019-12-31 → between 50,000 and 200,000")
    events = list(iter_form4_events(
        start=date(2019, 1, 1),
        end=date(2019, 12, 31),
    ))
    actual = len(events)
    in_range = 50_000 <= actual <= 200_000
    _check(
        "Anchor B: all-cache 2019 sanity range [50k, 200k]",
        in_range,
        expected="50,000 ≤ count ≤ 200,000 (verified ~103,782)",
        actual=str(actual),
    )


def anchor_c() -> None:
    """Anchor C: event structure — ticker, event_ts tz-aware UTC, payload keys, form_type."""
    print("\nAnchor C: event structure for AAPL 2019-01-01..2019-12-31")
    events = list(iter_form4_events(
        cik_list=["0000320193"],
        start=date(2019, 1, 1),
        end=date(2019, 12, 31),
    ))

    if not events:
        _check(
            "Anchor C: non-empty sample",
            False,
            expected=">0 events",
            actual="0 events",
        )
        return

    all_ticker_ok = all(isinstance(e.ticker, str) and len(e.ticker) > 0 for e in events)
    _check(
        "Anchor C: ticker non-empty string",
        all_ticker_ok,
        expected="all tickers are non-empty strings",
        actual=f"ok={all_ticker_ok} ({len(events)} events)",
    )

    all_ts_ok = all(
        isinstance(e.event_ts, datetime) and e.event_ts.tzinfo is not None
        for e in events
    )
    _check(
        "Anchor C: event_ts timezone-aware",
        all_ts_ok,
        expected="all event_ts are tz-aware datetimes",
        actual=f"ok={all_ts_ok}",
    )

    required_keys = {"form_type", "accession", "filing_date"}
    all_keys_ok = all(required_keys.issubset(set(e.payload.keys())) for e in events)
    _check(
        "Anchor C: payload has form_type/accession/filing_date",
        all_keys_ok,
        expected=f"all payloads contain {sorted(required_keys)}",
        actual=f"ok={all_keys_ok}",
    )

    valid_form_types = {"4", "4/A"}
    all_form_ok = all(e.payload.get("form_type") in valid_form_types for e in events)
    _check(
        "Anchor C: form_type in {\"4\", \"4/A\"}",
        all_form_ok,
        expected='all form_type in {"4", "4/A"}',
        actual=f"ok={all_form_ok}",
    )


def anchor_d() -> None:
    """Anchor D: ticker-list resolution matches CIK-direct (same 78 events for AAPL)."""
    print("\nAnchor D: Form4Stream.iter_events(universe=['AAPL']) == CIK-direct 78 events")

    # CIK-direct count (from Anchor A result, but re-compute for independence)
    cik_events = list(iter_form4_events(
        cik_list=["0000320193"],
        start=date(2019, 1, 1),
        end=date(2020, 12, 31),
    ))
    cik_count = len(cik_events)

    # Ticker-list path via Form4Stream
    stream = Form4Stream()
    ticker_events = list(stream.iter_events(
        start=date(2019, 1, 1),
        end=date(2020, 12, 31),
        universe=["AAPL"],
    ))
    ticker_count = len(ticker_events)

    _check(
        "Anchor D: ticker-list resolution matches CIK-direct count",
        ticker_count == cik_count,
        expected=f"ticker_count == cik_count == {cik_count}",
        actual=f"ticker_count={ticker_count}, cik_count={cik_count}",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("probe_premise_form4.py — F338 real-data anchors (F388)")
    print("=" * 60)

    if not _check_cache():
        # Cache absent on this machine — report clearly rather than faking a pass
        print("\nResult: SKIP (submissions cache not available on this machine)")
        return 0  # Not a failure — infra issue, not a correctness issue

    # Run anchors
    anchor_a()
    anchor_b()
    anchor_c()
    anchor_d()

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, s in _results if s == _PASS)
    failed = sum(1 for _, s in _results if s == _FAIL)
    print(f"Result: {passed} PASS, {failed} FAIL")
    if failed:
        print("FAILED anchors:")
        for label, status in _results:
            if status == _FAIL:
                print(f"  - {label}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
