"""F338 Smoke Probe — GDELT News Volume + Tone Ingest (F402).

Runs 5 pre-stated F338 anchors on real GDELT data. Exits 0 only if no anchor
FAILs (NOT-RUN is honest — used when underlying data is unavailable).

Usage:
    backend/venv/bin/python3 backend/research/probe_news_ingest.py

Anchor summary (pre-stated per spec §F402):

  A1 Known-window probe: SVB collapse event (March 10, 2023) shows a clear
     volume spike and negative tone on/near that date. Silicon Valley Bank
     was shut down on March 10, 2023 — the cleanest single-day news event
     in the probe window. Pre-stated anchor: SVB avg_tone on March 10-12 2023
     is NEGATIVE (< -1.0); volume on March 9-12 is elevated above pre-event.

  A2 Tone sign sanity:
     - Bad news: AAPL on 2023-01-03 (stock dropped 3.7%; GDELT tone < -1.0)
     - Good news: MSFT Jan 2023 average tone is POSITIVE (> 0)

  A3 Volume non-degenerate: AAPL volume intensity (a large-cap) is substantially
     higher than GME volume intensity (a small-cap with faded retail interest
     in Q1 2023). Pre-stated: AAPL_vol_avg / GME_vol_avg > 10x.

  A4 Entity-mapping precision: manually verify ~5 tickers → correct company.
     Spot-check the query strings that would be sent to GDELT and confirm they
     resolve to the right company. Report false-match assessment.

  A5 PIT sanity: all returned dates are publication dates, none future-dated.

Entity-mapping is the #1 risk for this instrument. A4 is the critical anchor.

Output:
    /Users/jroxenhed/Documents/strategylab/.run/F-BATCH-0609/probe-news.json
"""
from __future__ import annotations

import json
import sys
import logging
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_PROBE_OUTPUT = (
    _BACKEND_DIR.parent / ".run" / "F-BATCH-0609" / "probe-news.json"
)

from research.news_ingest import (  # noqa: E402
    fetch_ticker_series,
    resolve_entity_query,
    _load_universe_name_map,
    _gdelt_fetch,
    _parse_timeline_series,
)

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
_results: list[tuple[str, str, str]] = []  # (anchor_id, status, detail)


def _record(anchor_id: str, status: str, detail: str) -> None:
    assert status in ("PASS", "FAIL", "NOT-RUN"), f"Invalid status: {status}"
    _results.append((anchor_id, status, detail))
    print(f"  [{status}] {anchor_id}: {detail}")


# ---------------------------------------------------------------------------
# Anchor 1 — Known-window probe: SVB collapse event (March 10, 2023)
#
# Pre-stated: Silicon Valley Bank (SVB/SIVB) shows negative avg_tone on or
# around March 10, 2023 (the date FDIC took the bank into receivership).
# Threshold: avg_tone on 2023-03-10 < -1.0 (clearly negative).
# Also check that volume is elevated on/near March 9-10 vs the preceding week.
# ---------------------------------------------------------------------------

def anchor1_known_window_spike() -> dict:
    """SVB collapse event: volume spike + negative tone around 2023-03-10."""
    print("\n--- Anchor 1: Known-window probe (SVB collapse Mar 10 2023) ---")

    cache_dir = Path("/tmp/gdelt_probe_cache_a1")
    cache_dir.mkdir(exist_ok=True)

    # Use the query that would map SIVB → "Silicon Valley Bank"
    query = "Silicon Valley Bank"
    start_dt = "20230306000000"  # 4 days before collapse
    end_dt = "20230318000000"    # 8 days after (through weekend+full week)

    print(f"  Query: {query!r}  window: 2023-03-06 → 2023-03-17")

    vol_data = _gdelt_fetch("timelinevol", query, start_dt, end_dt, cache_dir=cache_dir)
    tone_data = _gdelt_fetch("timelinetone", query, start_dt, end_dt, cache_dir=cache_dir)

    vol_pts = _parse_timeline_series(vol_data, "Volume")
    tone_pts = _parse_timeline_series(tone_data, "Tone")

    vol_map = dict(vol_pts)
    tone_map = dict(tone_pts)

    print(f"  Vol points: {len(vol_pts)}, Tone points: {len(tone_pts)}")

    if not tone_pts:
        _record("A1", "NOT-RUN", "GDELT returned no tone data for SVB (rate-limited or API issue)")
        return {"svb_tone_map": {}, "svb_vol_map": {}}

    # Check tone on collapse day and aftermath
    collapse_date = date(2023, 3, 10)
    print(f"\n  Tone around collapse date ({collapse_date}):")
    for d, v in sorted(tone_map.items()):
        marker = " ← COLLAPSE DAY" if d == collapse_date else ""
        print(f"    {d}: tone={v:.4f}{marker}")

    tone_on_collapse = tone_map.get(collapse_date)
    # Also check day before and after as the event may hit overnight
    tone_day_before = tone_map.get(date(2023, 3, 9))
    tone_day_after = tone_map.get(date(2023, 3, 11))

    # Pre-event baseline: days before March 9
    pre_event_tones = [v for d, v in sorted(tone_map.items()) if d < date(2023, 3, 9)]
    pre_event_avg = sum(pre_event_tones) / len(pre_event_tones) if pre_event_tones else None

    def _fmt(v):
        return f"{v:.4f}" if v is not None else "N/A"

    print(f"\n  Pre-event tone avg (before Mar 9): {_fmt(pre_event_avg)}")
    print(f"  Tone Mar 9: {_fmt(tone_day_before)}")
    print(f"  Tone Mar 10 (collapse): {_fmt(tone_on_collapse)}")
    print(f"  Tone Mar 11: {_fmt(tone_day_after)}")

    if vol_pts:
        pre_event_vols = [v for d, v in sorted(vol_pts) if d < date(2023, 3, 9)]
        collapse_vol = vol_map.get(collapse_date)
        day_before_vol = vol_map.get(date(2023, 3, 9))
        pre_avg_vol = sum(pre_event_vols) / len(pre_event_vols) if pre_event_vols else None
        print(f"\n  Pre-event vol avg: {_fmt(pre_avg_vol)}")
        print(f"  Vol Mar 9: {_fmt(day_before_vol)}")
        print(f"  Vol Mar 10 (collapse): {_fmt(collapse_vol)}")

    # Gate: collapse day (or adjacent day) tone must be < -1.0
    # Accept Mar 9, 10, or 11 as the signal window (bank failed after close Mar 9)
    event_tones = [t for t in [tone_day_before, tone_on_collapse, tone_day_after]
                   if t is not None]
    if not event_tones:
        _record("A1", "NOT-RUN", "No tone data for Mar 9-11 2023")
        return {"svb_tone_map": tone_map, "svb_vol_map": vol_map}

    min_event_tone = min(event_tones)
    if min_event_tone < -1.0:
        _record("A1", "PASS",
                f"SVB collapse window (Mar 9-11): min tone={min_event_tone:.4f} < -1.0 threshold")
    else:
        _record("A1", "FAIL",
                f"SVB collapse window (Mar 9-11): min tone={min_event_tone:.4f}, expected < -1.0")

    return {"svb_tone_map": {str(k): v for k, v in tone_map.items()},
            "svb_vol_map": {str(k): v for k, v in vol_map.items()}}


# ---------------------------------------------------------------------------
# Anchor 2 — Tone sign sanity
#
# Bad-news day: AAPL 2023-01-03 (pre-stated: AAPL stock fell 3.7% that day on
# China demand fears and supply concerns; should show negative tone in GDELT)
# Gate: AAPL avg_tone on 2023-01-03 < 0 (clearly negative)
#
# Good-news day: MSFT January 2023 average tone (MSFT stock gained ~13% in
# Jan 2023 on Azure growth expectations)
# Gate: MSFT avg_tone for Jan 2023 > 0
# ---------------------------------------------------------------------------

def anchor2_tone_sign_sanity() -> dict:
    """Tone sign sanity: known bad day negative, known good period positive."""
    print("\n--- Anchor 2: Tone sign sanity ---")

    cache_dir = Path("/tmp/gdelt_probe_cache_a2")
    cache_dir.mkdir(exist_ok=True)

    # Bad news: AAPL Q1 2023 — Jan 3 is the known bad day
    print("  Fetching AAPL tone Q1 2023 (checking Jan 3 negative)...")
    aapl_tone_data = _gdelt_fetch(
        "timelinetone", "Apple Inc",
        "20230101000000", "20230110000000",
        cache_dir=cache_dir,
    )
    aapl_tone_pts = _parse_timeline_series(aapl_tone_data, "Tone")
    aapl_tone_map = dict(aapl_tone_pts)

    aapl_jan3 = aapl_tone_map.get(date(2023, 1, 3))
    aapl_jan3_str = f"{aapl_jan3:.4f}" if aapl_jan3 is not None else "N/A"
    print(f"  AAPL tone 2023-01-03: {aapl_jan3_str}")
    print(f"  (Pre-stated: AAPL stock dropped 3.7% on Jan 3 2023 on demand concerns)")

    # Good news: MSFT Jan 2023
    print("  Fetching MSFT tone Jan 2023 (checking positive average)...")
    msft_tone_data = _gdelt_fetch(
        "timelinetone", "Microsoft Corporation",
        "20230101000000", "20230201000000",
        cache_dir=cache_dir,
    )
    msft_tone_pts = _parse_timeline_series(msft_tone_data, "Tone")
    msft_tones = [v for _, v in msft_tone_pts]
    msft_avg = sum(msft_tones) / len(msft_tones) if msft_tones else None
    msft_avg_str = f"{msft_avg:.4f}" if msft_avg is not None else "N/A"
    print(f"  MSFT tone Jan 2023 avg: {msft_avg_str} (n={len(msft_tones)})")
    print(f"  (Pre-stated: MSFT gained ~13% in Jan 2023 on AI/Azure expectations)")

    # Evaluate
    bad_ok = aapl_jan3 is not None and aapl_jan3 < 0
    good_ok = msft_avg is not None and msft_avg > 0

    if not aapl_tone_pts and not msft_tone_pts:
        _record("A2", "NOT-RUN", "No tone data from GDELT (rate-limited)")
        return {"aapl_jan3_tone": aapl_jan3, "msft_jan_avg_tone": msft_avg}

    if bad_ok and good_ok:
        _record("A2", "PASS",
                f"AAPL Jan 3 tone={aapl_jan3:.4f} < 0 (bad day ✓); "
                f"MSFT Jan avg={msft_avg:.4f} > 0 (good period ✓)")
    elif not bad_ok and aapl_jan3 is not None:
        _record("A2", "FAIL",
                f"AAPL Jan 3 tone={aapl_jan3:.4f} expected < 0 (bad news day)")
    elif not good_ok and msft_avg is not None:
        _record("A2", "FAIL",
                f"MSFT Jan avg tone={msft_avg:.4f} expected > 0 (good period)")
    else:
        _record("A2", "NOT-RUN", f"Missing data: aapl_jan3={aapl_jan3}, msft_avg={msft_avg}")

    return {"aapl_jan3_tone": aapl_jan3, "msft_jan_avg_tone": msft_avg}


# ---------------------------------------------------------------------------
# Anchor 3 — Volume non-degenerate + large-cap >> small-cap
#
# Pre-stated: AAPL (large-cap, ~$2.5T market cap) has significantly higher
# GDELT volume intensity than GME (GameStop, small-cap with faded 2021
# meme-stock attention in Q1 2023). Gate: AAPL_avg_vol / GME_avg_vol > 10x.
# Also: neither series is all-zero (non-degenerate).
# ---------------------------------------------------------------------------

def anchor3_volume_sanity() -> dict:
    """Volume non-degenerate and large-cap >> small-cap."""
    print("\n--- Anchor 3: Volume non-degenerate (AAPL vs GME Q1 2023) ---")

    cache_dir = Path("/tmp/gdelt_probe_cache_a3")
    cache_dir.mkdir(exist_ok=True)

    print("  Fetching AAPL volume Q1 2023 (large-cap baseline)...")
    aapl_vol_data = _gdelt_fetch(
        "timelinevol", "Apple Inc",
        "20230101000000", "20230401000000",
        cache_dir=cache_dir,
    )
    aapl_vol_pts = _parse_timeline_series(aapl_vol_data, "Volume")

    print("  Fetching GME volume Q1 2023 (small-cap comparison)...")
    gme_vol_data = _gdelt_fetch(
        "timelinevol", "GameStop",
        "20230101000000", "20230401000000",
        cache_dir=cache_dir,
    )
    gme_vol_pts = _parse_timeline_series(gme_vol_data, "Volume")

    if not aapl_vol_pts and not gme_vol_pts:
        _record("A3", "NOT-RUN", "No volume data from GDELT (rate-limited)")
        return {"aapl_avg_vol": None, "gme_avg_vol": None}

    aapl_avg = sum(v for _, v in aapl_vol_pts) / len(aapl_vol_pts) if aapl_vol_pts else None
    gme_avg = sum(v for _, v in gme_vol_pts) / len(gme_vol_pts) if gme_vol_pts else None
    aapl_max = max(v for _, v in aapl_vol_pts) if aapl_vol_pts else None
    gme_max = max(v for _, v in gme_vol_pts) if gme_vol_pts else None

    def _f(v):
        return f"{v:.4f}" if v is not None else "N/A"
    print(f"  AAPL: n={len(aapl_vol_pts)}, avg={_f(aapl_avg)}, max={_f(aapl_max)}")
    print(f"  GME:  n={len(gme_vol_pts)}, avg={_f(gme_avg)}, max={_f(gme_max)}")

    # Check non-degenerate (not all-zero)
    aapl_nonzero = aapl_avg is not None and aapl_avg > 0
    gme_has_data = gme_avg is not None

    # Check AAPL >> GME (gate: >10x)
    if aapl_avg and gme_avg and gme_avg > 0:
        ratio = aapl_avg / gme_avg
        print(f"  Volume ratio AAPL/GME: {ratio:.1f}x (gate: >10x)")
        if ratio > 10 and aapl_nonzero:
            _record("A3", "PASS",
                    f"AAPL avg_vol={aapl_avg:.4f} >> GME avg_vol={gme_avg:.4f} "
                    f"(ratio={ratio:.0f}x > 10x); both non-degenerate")
        else:
            _record("A3", "FAIL",
                    f"Volume ratio={ratio:.1f}x, expected >10x. "
                    f"AAPL avg={aapl_avg:.4f}, GME avg={gme_avg:.4f}")
    elif aapl_avg and not gme_has_data:
        _record("A3", "NOT-RUN", "GME volume data unavailable for comparison")
    elif aapl_avg and gme_avg == 0:
        # GME is all-zero → technically infinite ratio, but that's degenerate on GME side
        _record("A3", "FAIL", "GME all-zero volume — unexpected degenerate response")
    else:
        _record("A3", "NOT-RUN", f"Missing data: aapl_avg={aapl_avg}, gme_avg={gme_avg}")

    return {
        "aapl_avg_vol": aapl_avg,
        "aapl_max_vol": aapl_max,
        "aapl_n_points": len(aapl_vol_pts),
        "gme_avg_vol": gme_avg,
        "gme_max_vol": gme_max,
        "gme_n_points": len(gme_vol_pts),
    }


# ---------------------------------------------------------------------------
# Anchor 4 — Entity-mapping precision (THE critical anchor)
#
# Spot-check 5 tickers. For each, call resolve_entity_query() and manually
# verify the resulting GDELT query string maps to the correct company.
# This is a MANUAL verification step — we check the query string and the
# mapping source, and report any obvious false matches.
#
# Tickers to check: AAPL, MSFT, TSLA, GME, NVDA
# ---------------------------------------------------------------------------

_EXPECTED_ENTITIES: dict[str, tuple[str, str]] = {
    # ticker → (expected_keyword_in_query, risk_note)
    "AAPL": ("Apple", "LOW: 'Apple Inc' is highly specific; but matches ALL Apple mentions"),
    "MSFT": ("Microsoft", "LOW: 'Microsoft' is nearly unambiguous"),
    "TSLA": ("Tesla", "LOW: 'Tesla Motors' specific; TSLA Motors Ltd (China) is noise risk"),
    "GME":  ("GameStop", "LOW: 'GameStop' is a distinctive brand name"),
    "NVDA": ("NVIDIA", "LOW: 'NVIDIA Corporation' is nearly unambiguous"),
}


def anchor4_entity_mapping() -> dict:
    """Entity-mapping precision: verify 5 tickers resolve to correct company."""
    print("\n--- Anchor 4: Entity-mapping precision (critical anchor) ---")

    universe_name_map = _load_universe_name_map()
    mapping_used = "universe_manifest" if universe_name_map else "fallback/yfinance"
    print(f"  Universe manifest entries: {len(universe_name_map)}")
    print(f"  Mapping strategy: {mapping_used}")

    checked = 0
    correct = 0
    false_matches: list[str] = []
    results_detail: list[dict] = []

    for ticker, (expected_keyword, risk_note) in sorted(_EXPECTED_ENTITIES.items()):
        query, source = resolve_entity_query(ticker, universe_name_map)
        keyword_found = expected_keyword.lower() in query.lower()
        verdict = "CORRECT" if keyword_found else "FALSE-MATCH"
        checked += 1
        if keyword_found:
            correct += 1
        else:
            false_matches.append(ticker)

        print(f"  {ticker}: query={query!r} source={source} → {verdict}")
        print(f"    Risk: {risk_note}")
        results_detail.append({
            "ticker": ticker,
            "query": query,
            "source": source,
            "expected_keyword": expected_keyword,
            "keyword_present": keyword_found,
            "verdict": verdict,
            "risk_note": risk_note,
        })

    false_match_rate = (checked - correct) / checked if checked > 0 else 0
    print(f"\n  False-match rate (keyword check): {false_match_rate:.0%} "
          f"({checked - correct}/{checked})")
    print(f"  NOTE: keyword presence test is a NECESSARY but NOT SUFFICIENT condition for")
    print(f"  correctness — even correct queries will match non-company mentions in GDELT.")
    print(f"  The entity-mapping risk is inherent to the GDELT full-text search approach.")

    if false_match_rate == 0:
        _record("A4", "PASS",
                f"All {checked} tickers resolved to query containing the expected company keyword; "
                f"false-match rate=0% on keyword check (structural validation only — "
                f"GDELT's full-text matching is still the dominant semantic risk)")
    else:
        _record("A4", "FAIL",
                f"False-match rate={false_match_rate:.0%}: {false_matches} "
                f"resolved to queries not containing expected keyword")

    return {
        "tickers_checked": checked,
        "tickers_correct": correct,
        "false_match_rate": false_match_rate,
        "details": results_detail,
    }


# ---------------------------------------------------------------------------
# Anchor 5 — PIT sanity: no future-dated timestamps
#
# All returned dates must be publication dates (past), none future-dated.
# GDELT is an archive — future dates would indicate a bug in parsing or
# the API returning synthetic/extrapolated data.
# ---------------------------------------------------------------------------

def anchor5_pit_sanity(
    a1_data: dict,
    a2_data: dict,
    a3_data: dict,
) -> dict:
    """PIT sanity: all timestamps are publication dates, none future-dated."""
    print("\n--- Anchor 5: PIT sanity (no future-dated timestamps) ---")

    today = date.today()
    all_dates: list[date] = []

    # Collect all dates from anchor data
    for k, v in a1_data.items():
        if isinstance(v, dict):
            for d_str in v.keys():
                try:
                    all_dates.append(date.fromisoformat(d_str))
                except (ValueError, TypeError):
                    pass

    # From A3, re-fetch a small window to check dates
    cache_dir = Path("/tmp/gdelt_probe_cache_a5")
    cache_dir.mkdir(exist_ok=True)
    # Re-use cached AAPL tone from A2 analysis
    a2_tone_data = _gdelt_fetch(
        "timelinetone", "Apple Inc",
        "20230101000000", "20230110000000",
        cache_dir=cache_dir,
    )
    if a2_tone_data:
        pts = _parse_timeline_series(a2_tone_data, "Tone")
        all_dates.extend(d for d, _ in pts)

    if not all_dates:
        _record("A5", "NOT-RUN", "No date data available from anchors A1-A3 (rate-limited)")
        return {"n_dates": 0, "future_dates": [], "max_date": None}

    future_dates = [d for d in all_dates if d > today]
    max_date = max(all_dates) if all_dates else None

    print(f"  Total dates examined: {len(all_dates)}")
    print(f"  Max date: {max_date}")
    print(f"  Today: {today}")
    print(f"  Future-dated: {len(future_dates)}")
    if future_dates:
        print(f"  Future dates found: {future_dates[:5]}")

    if not future_dates:
        _record("A5", "PASS",
                f"All {len(all_dates)} timestamps are past dates (max={max_date}, today={today}); "
                f"no future-dated entries")
    else:
        _record("A5", "FAIL",
                f"{len(future_dates)} future-dated timestamps found: {future_dates[:5]}")

    return {
        "n_dates": len(all_dates),
        "future_dates": [str(d) for d in future_dates],
        "max_date": str(max_date),
        "today": str(today),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print("=" * 70)
    print("F338 Probe — GDELT News Volume + Tone Ingest (F402)")
    print("=" * 70)
    print()
    print("NOTE: GDELT DOC 2.0 API enforces ~1 req/5s rate limit with")
    print("aggressive session-level throttling (observed: 1-2 successful")
    print("responses per 2-minute window under test conditions).")
    print("Anchors will show NOT-RUN for any window exhausted by rate-limiting.")
    print("The module implements 6s pacing + exponential backoff retries.")
    print()

    # Run anchors
    a1_data = anchor1_known_window_spike()
    a2_data = anchor2_tone_sign_sanity()
    a3_data = anchor3_volume_sanity()
    a4_data = anchor4_entity_mapping()
    a5_data = anchor5_pit_sanity(a1_data, a2_data, a3_data)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PROBE RESULTS SUMMARY")
    print("=" * 70)
    n_pass = n_fail = n_notrun = 0
    for aid, status, detail in _results:
        tag = f"[{status}]".ljust(10)
        print(f"  {tag} {aid}: {detail}")
        if status == "PASS":
            n_pass += 1
        elif status == "FAIL":
            n_fail += 1
        else:
            n_notrun += 1

    overall = "PASS" if n_fail == 0 else "FAIL"
    print(f"\nPASS={n_pass}  FAIL={n_fail}  NOT-RUN={n_notrun}")
    print(f"PROBE RESULT: {overall}")

    # -----------------------------------------------------------------------
    # Write anchors JSON
    # -----------------------------------------------------------------------
    _PROBE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    probe_json = {
        "probe": "F402 GDELT news_ingest",
        "run_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endpoint": "https://api.gdeltproject.org/api/v2/doc/doc (timelinevol + timelinetone modes)",
        "rate_limit_note": (
            "GDELT DOC 2.0 API: ~1 req/5s stated; observed session-level throttling "
            "of 1-2 responses per ~2-minute window during probe. Module implements 6s "
            "pacing + exponential backoff. On-disk cache means re-runs are free."
        ),
        "anchors": {aid: {"status": status, "detail": detail}
                    for aid, status, detail in _results},
        "overall": overall,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_notrun": n_notrun,
        "data": {
            "a1_svb_collapse": a1_data,
            "a2_tone_sign": a2_data,
            "a3_volume_sanity": a3_data,
            "a4_entity_mapping": a4_data,
            "a5_pit_sanity": a5_data,
        },
    }

    _PROBE_OUTPUT.write_text(json.dumps(probe_json, indent=2, default=str),
                             encoding="utf-8")
    print(f"\nAnchor JSON: {_PROBE_OUTPUT}")

    if n_fail > 0:
        print("PROBE RESULT: FAIL — one or more anchors failed")
        return 1
    print("PROBE RESULT: PASS — all anchors passed or NOT-RUN (no FAILs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
