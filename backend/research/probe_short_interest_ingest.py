"""F338 Smoke Probe — FINRA Short Interest Ingest (F403).

Runs 5 pre-stated anchors on real FINRA CDN data for a small sample of
settlement dates.  Exits 0 only if no anchor FAILs.  NOT-RUN is honest —
used when the data needed is unavailable.

Usage:
    backend/venv/bin/python3 backend/research/probe_short_interest_ingest.py

Anchors (pre-stated per F403 spec):
  A1 — Known-window: GME short interest ≈ 61.8M shares on 2021-01-15
  A2 — Coverage bound: 2021-01-15 present; 2017-12-15 absent
  A3 — days_to_cover positive with plausible distribution (summary stats)
  A4 — Dissemination lag: dissemination_date − settlement_date ≈ 7 BD
       (8–10 calendar days depending on week) across several dates
  A5 — Ticker coverage sane: n_tickers for one settlement date in [3000, 20000]

Writes anchors JSON to .run/F-BATCH-0609/probe-short-interest.json.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_REPO_ROOT = _BACKEND_DIR.parent
_OUT_DIR = _REPO_ROOT / ".run" / "F-BATCH-0609"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

from research.short_interest_ingest import (  # noqa: E402
    fetch_one,
    fetch_short_interest,
    build_daily_panel,
    dissemination_date,
    biweekly_settlement_dates,
)

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
_results: list[dict] = []


def _record(anchor_id: str, status: str, detail: str, data: dict | None = None) -> None:
    assert status in ("PASS", "FAIL", "NOT-RUN"), f"Invalid status: {status}"
    entry = {"anchor": anchor_id, "status": status, "detail": detail}
    if data:
        entry.update(data)
    _results.append(entry)
    tag = f"[{status}]".ljust(10)
    print(f"  {tag} {anchor_id}: {detail}")


# ---------------------------------------------------------------------------
# Anchor 1 — Known-window: GME 2021-01-15 ≈ 61.8M shares
# ---------------------------------------------------------------------------
_GME_SETTLEMENT = date(2021, 1, 15)
_GME_ANCHOR_SHARES = 61_782_730


def anchor1_gme_known_window() -> None:
    print("\n--- A1: GME known-window probe (2021-01-15, anchor=61,782,730 shares) ---")
    df = fetch_one(_GME_SETTLEMENT)
    if df is None or df.empty:
        _record("A1", "NOT-RUN", "2021-01-15 file not available")
        return

    gme = df[df["ticker"] == "GME"]
    if gme.empty:
        _record("A1", "FAIL", "GME not found in 2021-01-15 file")
        return

    observed = int(gme["short_interest_shares"].iloc[0])
    print(f"  GME short_interest_shares observed: {observed:,}")
    print(f"  GME avg_daily_volume: {gme['avg_daily_volume'].iloc[0]:,.0f}")
    print(f"  GME days_to_cover:    {gme['days_to_cover'].iloc[0]:.2f}")
    print(f"  GME dissemination_date: {gme['dissemination_date'].iloc[0]}")

    # Allow ±1 share rounding tolerance
    if abs(observed - _GME_ANCHOR_SHARES) <= 1:
        _record("A1", "PASS",
                f"GME short_interest_shares={observed:,} matches anchor {_GME_ANCHOR_SHARES:,}",
                {"gme_observed": observed, "gme_anchor": _GME_ANCHOR_SHARES})
    else:
        _record("A1", "FAIL",
                f"GME={observed:,} vs anchor={_GME_ANCHOR_SHARES:,} (diff={observed-_GME_ANCHOR_SHARES:+,})",
                {"gme_observed": observed, "gme_anchor": _GME_ANCHOR_SHARES})


# ---------------------------------------------------------------------------
# Anchor 2 — Coverage bound: 2021-01-15 present; pre-2018 absent
# ---------------------------------------------------------------------------
_PRE_2018_DATE = date(2017, 12, 15)


def anchor2_coverage_bound() -> None:
    print("\n--- A2: Coverage bound (2021-01-15 present; 2017-12-15 absent) ---")

    # Pre-2018 date must be absent
    df_pre = fetch_one(_PRE_2018_DATE)
    pre_absent = (df_pre is None or df_pre.empty)
    print(f"  2017-12-15 available: {not pre_absent} (expected: False)")

    # Post-2018 date must be present (reuse A1 date)
    df_post = fetch_one(_GME_SETTLEMENT)
    post_present = (df_post is not None and not df_post.empty)
    print(f"  2021-01-15 available: {post_present} (expected: True)")

    if pre_absent and post_present:
        _record("A2", "PASS",
                "pre-2018 absent, post-2018 present — coverage starts ~2018 as stated",
                {"pre_2018_absent": True, "post_2018_present": True})
    elif not pre_absent:
        _record("A2", "FAIL",
                "pre-2018 date returned data — coverage may extend further back than stated")
    else:
        _record("A2", "FAIL",
                "post-2018 date unavailable — fetch may be broken")


# ---------------------------------------------------------------------------
# Anchor 3 — days_to_cover positive with plausible distribution
# ---------------------------------------------------------------------------

def anchor3_dtc_distribution() -> None:
    print("\n--- A3: days_to_cover distribution (2021-01-15) ---")
    df = fetch_one(_GME_SETTLEMENT)
    if df is None or df.empty:
        _record("A3", "NOT-RUN", "2021-01-15 file not available")
        return

    dtc = df["days_to_cover"].dropna()
    if dtc.empty:
        _record("A3", "FAIL", "No days_to_cover values in 2021-01-15 file")
        return

    pct_positive = (dtc > 0).mean()
    stats = {
        "count": int(len(dtc)),
        "min": float(dtc.min()),
        "p25": float(dtc.quantile(0.25)),
        "median": float(dtc.median()),
        "p75": float(dtc.quantile(0.75)),
        "p95": float(dtc.quantile(0.95)),
        "max": float(dtc.max()),
        "pct_positive": float(pct_positive),
    }
    print(f"  n={stats['count']}")
    print(f"  min={stats['min']:.2f}  p25={stats['p25']:.2f}  median={stats['median']:.2f}")
    print(f"  p75={stats['p75']:.2f}  p95={stats['p95']:.2f}  max={stats['max']:.2f}")
    print(f"  pct_positive={pct_positive:.1%}")

    # Gate: >95% positive, median in [0.1, 30], max < 1000 (prevents degenerate output)
    gate_ok = (
        pct_positive >= 0.95
        and 0.1 <= stats["median"] <= 30
        and stats["max"] < 1000
    )
    if gate_ok:
        _record("A3", "PASS",
                f"median={stats['median']:.2f}d  pct_positive={pct_positive:.1%}  max={stats['max']:.2f}d",
                {"dtc_stats": stats})
    else:
        _record("A3", "FAIL",
                f"gate failed: pct_positive={pct_positive:.1%} median={stats['median']:.2f} max={stats['max']:.2f}",
                {"dtc_stats": stats})


# ---------------------------------------------------------------------------
# Anchor 4 — Dissemination lag ≈ 7 business days across several dates
# ---------------------------------------------------------------------------

_PROBE_DATES_LAG = [
    date(2021, 1, 15),
    date(2021, 1, 29),
    date(2022, 6, 15),
    date(2023, 9, 29),
]


def anchor4_dissemination_lag() -> None:
    print("\n--- A4: Dissemination lag ≈ 7 business days across probe dates ---")
    lags_bd: list[int] = []
    lags_cal: list[int] = []
    for sd in _PROBE_DATES_LAG:
        dd = dissemination_date(sd)
        cal_days = (dd - sd).days
        # Count business days between sd and dd
        bd = int(np.busday_count(sd.isoformat(), dd.isoformat()))
        lags_cal.append(cal_days)
        lags_bd.append(bd)
        print(f"  settlement={sd}  dissemination={dd}  "
              f"cal_days={cal_days}  business_days={bd}")

    mean_bd = float(np.mean(lags_bd))
    mean_cal = float(np.mean(lags_cal))
    all_exactly_7_bd = all(bd == 7 for bd in lags_bd)
    cal_in_range = all(8 <= c <= 12 for c in lags_cal)

    print(f"  mean business_days={mean_bd:.1f}  mean calendar_days={mean_cal:.1f}")
    print(f"  all exactly 7 BD: {all_exactly_7_bd}")
    print(f"  all calendar days in [8, 12]: {cal_in_range}")

    if all_exactly_7_bd:
        _record("A4", "PASS",
                f"All {len(_PROBE_DATES_LAG)} dates: exactly 7 BD lag; "
                f"calendar days {min(lags_cal)}–{max(lags_cal)} (expected 8–12)",
                {"lags_business_days": lags_bd, "lags_calendar_days": lags_cal})
    elif mean_bd >= 6.5 and mean_bd <= 7.5:
        _record("A4", "PASS",
                f"Mean BD lag={mean_bd:.1f} (near 7); calendar range={min(lags_cal)}–{max(lags_cal)}",
                {"lags_business_days": lags_bd, "lags_calendar_days": lags_cal})
    else:
        _record("A4", "FAIL",
                f"BD lags={lags_bd} (expected all=7); mean={mean_bd:.1f}",
                {"lags_business_days": lags_bd, "lags_calendar_days": lags_cal})


# ---------------------------------------------------------------------------
# Anchor 5 — Ticker coverage sane for one settlement date
# ---------------------------------------------------------------------------

_COVERAGE_DATE = date(2021, 1, 15)


def anchor5_ticker_coverage() -> None:
    print(f"\n--- A5: Ticker coverage for {_COVERAGE_DATE} ---")
    df = fetch_one(_COVERAGE_DATE)
    if df is None or df.empty:
        _record("A5", "NOT-RUN", f"{_COVERAGE_DATE} file not available")
        return

    n_tickers = df["ticker"].nunique()
    n_rows = len(df)
    exchanges = df["exchange_code"].value_counts().to_dict()
    print(f"  n_rows={n_rows}  n_tickers={n_tickers}")
    print(f"  exchange_code distribution: {dict(list(exchanges.items())[:8])}")

    # Gate: [3000, 20000] — FINRA covers NYSE + NASDAQ + OTC equity universe
    if 3_000 <= n_tickers <= 20_000:
        _record("A5", "PASS",
                f"n_tickers={n_tickers} in [3000, 20000] for {_COVERAGE_DATE}",
                {"n_tickers": n_tickers, "n_rows": n_rows, "exchanges": exchanges})
    else:
        _record("A5", "FAIL",
                f"n_tickers={n_tickers} outside sane band [3000, 20000]",
                {"n_tickers": n_tickers, "n_rows": n_rows})


# ---------------------------------------------------------------------------
# Anchor 6 — build_daily_panel with two settlement dates (regression for C-001)
# ---------------------------------------------------------------------------
# Pre-stated gate: build_daily_panel must NOT raise ValueError when called with
# event_df that contains two distinct settlement dates for the same ticker (GME
# on 2021-01-15 and 2021-01-29).  The fix: value_cols already contains
# dissemination_date so the old ["dissemination_date"] + value_cols selection
# produced a duplicate column → set_index raised ValueError.
# This anchor gates the exact code path the original probe missed.

_A6_SETTLEMENT_DATES = [date(2021, 1, 15), date(2021, 1, 29)]


def anchor6_build_daily_panel_multi_date() -> None:
    print("\n--- A6: build_daily_panel with two settlement dates for same ticker (C-001 regression) ---")
    frames = []
    for sd in _A6_SETTLEMENT_DATES:
        df = fetch_one(sd)
        if df is None or df.empty:
            _record("A6", "NOT-RUN", f"{sd} file not available — cannot run C-001 regression")
            return
        # Keep only GME rows to keep the panel tiny and deterministic
        gme = df[df["ticker"] == "GME"]
        if gme.empty:
            _record("A6", "NOT-RUN", f"GME not in {sd} file — cannot run C-001 regression")
            return
        frames.append(gme)

    event_df = pd.concat(frames, ignore_index=True)
    print(f"  event_df shape: {event_df.shape}")
    print(f"  settlement_dates: {sorted(event_df['settlement_date'].unique())}")
    print(f"  dissemination_dates: {sorted(event_df['dissemination_date'].unique())}")

    # This must NOT raise — C-001 fix removes the duplicate column
    try:
        daily = build_daily_panel(event_df)
    except Exception as exc:
        _record("A6", "FAIL",
                f"build_daily_panel raised {type(exc).__name__}: {exc}",
                {"exception": str(exc)})
        return

    print(f"  daily panel shape: {daily.shape}")
    print(f"  columns: {list(daily.columns)}")
    if not daily.empty:
        print(f"  date index range: {daily['date'].min()} → {daily['date'].max()}")
        print(f"  staleness_days range: {daily['staleness_days'].min()} → {daily['staleness_days'].max()}")

    # Gate checks
    not_empty = not daily.empty
    has_date_col = "date" in daily.columns
    has_staleness = "staleness_days" in daily.columns
    date_col_ok = has_date_col  # date comes from reset_index after ffill
    # Two settlement dates → should have entries spanning both dissemination periods
    n_rows = len(daily)

    if not_empty and has_date_col and has_staleness:
        _record("A6", "PASS",
                f"build_daily_panel succeeded: {n_rows} rows, date index OK, staleness_days present",
                {"n_rows": n_rows, "columns": list(daily.columns)})
    else:
        reasons = []
        if not not_empty:
            reasons.append("daily panel is empty")
        if not has_date_col:
            reasons.append("missing 'date' column")
        if not has_staleness:
            reasons.append("missing 'staleness_days' column")
        _record("A6", "FAIL",
                f"gate failed: {'; '.join(reasons)}",
                {"n_rows": n_rows, "columns": list(daily.columns)})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("F338 Probe — FINRA Short Interest Ingest (F403)")
    print("=" * 70)

    anchor1_gme_known_window()
    anchor2_coverage_bound()
    anchor3_dtc_distribution()
    anchor4_dissemination_lag()
    anchor5_ticker_coverage()
    anchor6_build_daily_panel_multi_date()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PROBE RESULTS SUMMARY")
    print("=" * 70)
    n_pass = n_fail = n_notrun = 0
    for r in _results:
        status = r["status"]
        tag = f"[{status}]".ljust(10)
        print(f"  {tag} {r['anchor']}: {r['detail']}")
        if status == "PASS":
            n_pass += 1
        elif status == "FAIL":
            n_fail += 1
        else:
            n_notrun += 1

    print(f"\nPASS={n_pass}  FAIL={n_fail}  NOT-RUN={n_notrun}")

    # Write JSON
    out_path = _OUT_DIR / "probe-short-interest.json"
    payload = {
        "probe": "F403 FINRA Short Interest Ingest",
        "anchors": _results,
        "summary": {"pass": n_pass, "fail": n_fail, "not_run": n_notrun},
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nAnchor results written to: {out_path}")

    if n_fail > 0:
        print("PROBE RESULT: FAIL — one or more anchors failed")
        return 1
    print("PROBE RESULT: PASS — all anchors passed or NOT-RUN (no FAILs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
