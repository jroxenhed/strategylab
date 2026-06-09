"""F338 Smoke Probe — Analyst Up/Downgrades Ingest (F401).

Runs 5 pre-stated F338 anchors on real yfinance data for ≤25 tickers.
Exits 0 only if no anchor FAILs.  NOT-RUN is used when data is genuinely absent.

Usage:
    backend/venv/bin/python3 backend/research/probe_ratings_ingest.py

Anchor summary (pre-stated per Phase 0 spec, 2026-06-09):
  A1. AAPL returns many rating actions spanning multiple years; firm names
      plausible (major banks present).
  A2. `unknown` action bucket < 5% of rows (else action-map is incomplete — FAIL).
  A3. Known-window probe: Goldman Sachs downgraded AAPL from Neutral → Sell on
      2020-04-17 (COVID-era; well-documented). Must land with action='down' on
      that date.
  A4. Coverage: fraction of probed tickers with ≥1 action — print it.
  A5. PIT sanity: max(date) ≤ today; no future-dated actions.

Output:
    /Users/jroxenhed/Documents/strategylab/.run/F-BATCH-0609/probe-ratings.json
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_OUTPUT_DIR = Path("/Users/jroxenhed/Documents/strategylab/.run/F-BATCH-0609")

from research.ratings_ingest import build_ratings_panels, _normalize_action  # noqa: E402

# ---------------------------------------------------------------------------
# Probe tickers (≤25)
# ---------------------------------------------------------------------------
_PROBE_TICKERS = [
    "AAPL", "MSFT", "TSLA", "AMZN", "NVDA",
    "GOOGL", "META", "JPM", "BAC", "GS",
    "XOM", "CVX", "PFE", "JNJ", "UNH",
    "WMT", "HD", "COST", "V", "MA",
    "NFLX", "AMD", "INTC", "ORCL", "CRM",
]
assert len(_PROBE_TICKERS) <= 25, "Probe must use ≤25 tickers"

# ---------------------------------------------------------------------------
# Known-window event (A3 — pre-stated)
# ---------------------------------------------------------------------------
# Goldman Sachs downgraded AAPL from Neutral to Sell on 2020-04-17.
# Source: widely covered at the time; this is an unusual GS sell rating on AAPL.
# Expected: action='down', firm contains 'goldman', date == 2020-04-17.
_A3_TICKER   = "AAPL"
_A3_FIRM_STR = "goldman sachs"  # lowercase substring match
_A3_DATE     = date(2020, 4, 17)
_A3_ACTION   = "down"

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
_results: list[dict] = []


def _record(anchor_id: str, passed: bool, observed: str) -> None:
    status = "pass" if passed else "fail"
    _results.append({"id": anchor_id, "pass": passed, "observed": observed})
    tag = "PASS" if passed else "FAIL"
    print(f"  [{tag}] {anchor_id}: {observed}")


# ---------------------------------------------------------------------------
# Main probe
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("F338 Probe — Analyst Up/Downgrades Ingest (F401)")
    print(f"Tickers: {_PROBE_TICKERS}")
    print(f"Output dir: {_OUTPUT_DIR}")
    print("=" * 70)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Fetch data (write parquet to output dir for inspection)
    # -----------------------------------------------------------------------
    print("\n--- Fetching ratings for probe tickers ---")
    events, agg, meta = build_ratings_panels(
        _PROBE_TICKERS,
        output_dir=_OUTPUT_DIR,
        write_parquet=True,
    )

    print(f"\nFetch complete: {len(events)} event rows, {meta['n_tickers']} tickers with data")
    print(f"Fetch vintage: {meta['fetch_vintage']}")
    print(f"Coverage: {meta['coverage_start']} → {meta['coverage_end']}")

    # Subset to AAPL for anchors A1/A2/A3
    aapl = events[events["ticker"] == "AAPL"].copy()

    # -----------------------------------------------------------------------
    # A1: AAPL has many actions spanning years; major banks present
    # -----------------------------------------------------------------------
    print("\n--- Anchor 1: AAPL coverage and firm names ---")
    if aapl.empty:
        _record("A1", False, "AAPL returned 0 rows — no data")
    else:
        n_rows   = len(aapl)
        n_years  = aapl["date"].dt.year.nunique()
        year_min = aapl["date"].dt.year.min()
        year_max = aapl["date"].dt.year.max()
        firms_lower = set(aapl["firm"].str.lower().unique())

        # Check for major banks
        major_banks = ["goldman sachs", "morgan stanley", "jpmorgan", "b of a",
                       "barclays", "ubs", "citigroup", "credit suisse", "deutsche bank",
                       "wells fargo", "jefferies", "piper sandler", "needham"]
        found_banks = [b for b in major_banks if any(b in f for f in firms_lower)]

        print(f"  AAPL rows: {n_rows}, years: {n_years} ({year_min}–{year_max})")
        print(f"  Major banks found: {found_banks}")

        ok = (n_rows >= 100) and (n_years >= 5) and (len(found_banks) >= 3)
        _record(
            "A1", ok,
            f"{n_rows} actions, {n_years} years ({year_min}–{year_max}), "
            f"{len(found_banks)} major banks: {found_banks[:5]}"
        )

    # -----------------------------------------------------------------------
    # A2: `unknown` bucket < 5%
    # -----------------------------------------------------------------------
    print("\n--- Anchor 2: unknown action bucket < 5% ---")
    if events.empty:
        _record("A2", False, "No events — cannot check action distribution")
    else:
        action_counts = events["action"].value_counts()
        n_unknown = int(action_counts.get("unknown", 0))
        n_total   = len(events)
        pct_unknown = n_unknown / n_total if n_total > 0 else 1.0

        print(f"  Action distribution (all tickers):")
        for act, cnt in action_counts.items():
            print(f"    {act}: {cnt} ({cnt/n_total:.1%})")
        print(f"  unknown fraction: {pct_unknown:.2%}")

        ok = pct_unknown < 0.05
        _record(
            "A2", ok,
            f"unknown={n_unknown}/{n_total} = {pct_unknown:.2%} "
            f"{'(< 5% gate PASS)' if ok else '(>= 5% gate FAIL — action map incomplete)'}"
        )

    # -----------------------------------------------------------------------
    # A3: Known-window probe — Goldman Sachs AAPL downgrade 2020-04-17
    # -----------------------------------------------------------------------
    print(f"\n--- Anchor 3: Known-window probe "
          f"({_A3_FIRM_STR.title()} AAPL down, {_A3_DATE}) ---")
    if aapl.empty:
        _record("A3", False, "AAPL has no data — cannot check known event")
    else:
        aapl_on_date = aapl[aapl["date"].dt.date == _A3_DATE]
        # Firm substring match (case-insensitive)
        aapl_match = aapl_on_date[
            aapl_on_date["firm"].str.lower().str.contains(_A3_FIRM_STR, na=False)
        ]
        if aapl_match.empty:
            # Print rows on that date to help diagnose
            print(f"  Rows on {_A3_DATE}:")
            print(aapl_on_date[["firm", "action", "from_grade", "to_grade"]].to_string())
            _record(
                "A3", False,
                f"No Goldman Sachs AAPL action found on {_A3_DATE}. "
                f"Rows on that date: {len(aapl_on_date)}"
            )
        else:
            row = aapl_match.iloc[0]
            observed_action = row["action"]
            ok = observed_action == _A3_ACTION
            _record(
                "A3", ok,
                f"Found: firm='{row['firm']}', date={_A3_DATE}, "
                f"action='{observed_action}' (expected='{_A3_ACTION}'), "
                f"from='{row['from_grade']}' to='{row['to_grade']}' "
                f"grade_delta={row['grade_delta']}"
            )

    # -----------------------------------------------------------------------
    # A4: Coverage — fraction of probed tickers with ≥1 action
    # -----------------------------------------------------------------------
    print("\n--- Anchor 4: Coverage (tickers with ≥1 action) ---")
    if events.empty:
        _record("A4", False, "No events at all — 0% coverage")
    else:
        tickers_with_data = set(events["ticker"].unique())
        n_covered  = len(tickers_with_data)
        n_probed   = len(_PROBE_TICKERS)
        pct        = n_covered / n_probed
        missing    = [t for t in _PROBE_TICKERS if t not in tickers_with_data]
        print(f"  Tickers with data: {n_covered}/{n_probed} = {pct:.0%}")
        if missing:
            print(f"  No data for: {missing}")

        # Coverage gate: ≥80% of probed tickers should have data
        ok = pct >= 0.80
        _record(
            "A4", ok,
            f"{n_covered}/{n_probed} = {pct:.0%} coverage "
            f"{'(>= 80% gate PASS)' if ok else '(< 80% gate FAIL)'}"
        )

    # -----------------------------------------------------------------------
    # A5: PIT sanity — max(date) ≤ today; no future-dated actions
    # -----------------------------------------------------------------------
    print("\n--- Anchor 5: PIT sanity (max date ≤ today, no future dates) ---")
    today = date.today()
    if events.empty:
        _record("A5", False, "No events — cannot check PIT sanity")
    else:
        max_date = events["date"].max().date()
        future_rows = events[events["date"].dt.date > today]
        n_future = len(future_rows)

        print(f"  max(date) = {max_date}, today = {today}")
        print(f"  Future-dated rows: {n_future}")

        ok = (max_date <= today) and (n_future == 0)
        _record(
            "A5", ok,
            f"max_date={max_date}, today={today}, future_rows={n_future} "
            f"{'(PASS)' if ok else '(FAIL)'}"
        )

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PROBE RESULTS SUMMARY")
    print("=" * 70)

    n_pass = n_fail = 0
    for r in _results:
        tag = "PASS" if r["pass"] else "FAIL"
        print(f"  [{tag}] {r['id']}: {r['observed']}")
        if r["pass"]:
            n_pass += 1
        else:
            n_fail += 1

    print(f"\nPASS={n_pass}  FAIL={n_fail}")

    # Write JSON anchor results
    anchors_out = {"anchors": _results}
    out_path = _OUTPUT_DIR / "probe-ratings.json"
    out_path.write_text(json.dumps(anchors_out, indent=2, default=str))
    print(f"\nAnchor results written to: {out_path}")

    if n_fail > 0:
        print("PROBE RESULT: FAIL — one or more anchors failed")
        return 1

    print("PROBE RESULT: PASS — all anchors passed (no FAILs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
