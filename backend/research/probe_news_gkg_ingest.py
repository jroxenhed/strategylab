"""F338 real-data smoke probe for the F405 GKG bulk news panel.

Runs pre-stated anchors against the PRODUCED parquet (backend/data/gdelt/
news_panel_gkg.parquet) — no BigQuery access, no quota. Anchors were stated
before the full panel was built (only the March-2023 pilot month existed).

Anchors:
  A1 — Known-window spike: Norfolk Southern (NSC) East Palestine derailment.
       Daily 'any' volume over 2023-02-04..14 ≥ 5× its Jan-2023 daily mean,
       and shock-window tone < January tone and < 0.
  A2 — Second known window, different era: GameStop (GME) squeeze. Daily 'any'
       volume over 2021-01-25..29 ≥ 5× its Dec-2020 daily mean.
  A3 — Tone distribution sanity: panel-wide avg_tone within GDELT's documented
       [-100, 100] scale, median within [-5, +5], ≥95% of rows within [-10, +10].
  A4 — Name-shape regimes match pilot empirics: AAPL has ZERO 'core' rows (GDELT
       never emits bare 'Apple') but nonzero 'full'; NFLX 'core' total > 10× NFLX
       'full' total; NDAQ 'core' total > 50× NDAQ 'full' total (pathology stays
       quarantined in the core channel).
  A5 — Coverage continuity / PIT sanity: every calendar day in [coverage_start,
       coverage_end] has ≥1 row EXCEPT days in _VERIFIED_CORPUS_GAPS (upstream
       GDELT outages, each verified against raw GKG partitions — zero rows in
       the source itself), and coverage_end ≤ today (no future-dated rows).

Anchor amendments (2026-06-09, after the first full-panel run — recorded per
F338 honesty; the SUBSTANTIVE failure that run exposed was a real SQL bug,
the 'any'-channel label never materializing, fixed in news_gkg_ingest.py):
  A1 went through three names; each replacement taught something real:
     1. Western Alliance (WAL): ZERO panel rows — its alias ("western alliance
        bancorporation") is a name shape GDELT's NER never emits. The stamped
        name-shape capture limitation, not a pipeline bug.
     2. NFLX April-2022 subscriber shock: tone responded exactly as predicted
        (+0.39 → −0.35) but volume rose only 1.2× (vs 2× pre-stated) — NFLX
        baseline coverage is saturated with entertainment content, so investor
        shocks barely move total volume. Lesson: volume-spike anchors need
        names with high-but-not-saturated coverage.
     3. Boeing (737 MAX window): ZERO panel rows — "The Boeing Company" yields
        core alias "the boeing" (leading article survives suffix-stripping),
        which GDELT never emits. Exposed the leading-"The" capture-gap class,
        now stamped in the panel metadata.
     Final anchor: NSC (proven present, high-but-not-saturated coverage) with
     the Feb-2023 derailment; thresholds locked before reading the window.
     Procedural lesson: anchor names need an existence PRECONDITION check
     (ticker present in panel at all — no window peeking) before thresholds.
  A3 hard bound was a guessed ±25; observed single-article-day max was 28.5.
     GDELT's documented tone scale is ±100 — bound corrected to the documented
     scale. The substantive checks (median, 95%-within-±10) passed unamended.
  A5 originally assumed "the corpus has no dark days" — false: 18 days verified
     to have zero rows in the raw GKG mirror itself (2017-08-29 and the
     2025-06-15..2025-07-01 outage block).

Exit 0 only if no anchor FAILs (NOT-RUN is honest when data is unavailable).
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PANEL = _BACKEND_DIR / "data" / "gdelt" / "news_panel_gkg.parquet"

# Days with zero rows in the raw GKG BigQuery mirror itself — verified
# 2026-06-09 by querying partition row counts around each gap (surrounding
# days populated, gap days absent; 2025-06-14 shows half volume = outage
# onset). New panel gaps NOT in this list fail A5 until verified the same way.
_VERIFIED_CORPUS_GAPS: set[date] = {date(2017, 8, 29)} | {
    date(2025, 6, 15) + timedelta(days=i) for i in range(17)  # ..2025-07-01
}

_results: list[tuple[str, str, str]] = []


def _record(anchor_id: str, status: str, detail: str) -> None:
    assert status in ("PASS", "FAIL", "NOT-RUN"), f"Invalid status: {status}"
    _results.append((anchor_id, status, detail))
    print(f"  [{status}] {anchor_id}: {detail}")


def _window_mean(df: pd.DataFrame, ticker: str, channel: str,
                 start: date, end: date) -> tuple[float, float]:
    """(mean daily n_articles, mean avg_tone) for ticker/channel in [start, end]."""
    sel = df[(df.ticker == ticker) & (df.alias_type == channel)
             & (df.date >= start) & (df.date <= end)]
    if sel.empty:
        return 0.0, float("nan")
    return float(sel.n_articles.mean()), float(sel.avg_tone.mean())


def anchor1_nsc_derailment(df: pd.DataFrame) -> None:
    print("\n--- Anchor 1: NSC February-2023 East Palestine derailment ---")
    base_vol, base_tone = _window_mean(df, "NSC", "any", date(2023, 1, 1), date(2023, 1, 31))
    shock_vol, shock_tone = _window_mean(df, "NSC", "any", date(2023, 2, 4), date(2023, 2, 14))
    if base_vol == 0.0 and shock_vol == 0.0:
        _record("A1", "FAIL", "NSC absent from panel in both windows")
        return
    ratio = shock_vol / base_vol if base_vol > 0 else float("inf")
    ok = ratio >= 5.0 and shock_tone < base_tone and shock_tone < 0
    _record("A1", "PASS" if ok else "FAIL",
            f"vol {base_vol:.1f}→{shock_vol:.1f}/day ({ratio:.1f}×, need ≥5×), "
            f"tone {base_tone:.2f}→{shock_tone:.2f} (need < baseline and < 0)")


def anchor2_gme_squeeze(df: pd.DataFrame) -> None:
    print("\n--- Anchor 2: GME January-2021 squeeze spike ---")
    base_vol, _ = _window_mean(df, "GME", "any", date(2020, 12, 1), date(2020, 12, 31))
    squeeze_vol, _ = _window_mean(df, "GME", "any", date(2021, 1, 25), date(2021, 1, 29))
    if base_vol == 0.0 and squeeze_vol == 0.0:
        _record("A2", "FAIL", "GME absent from panel in both windows")
        return
    ratio = squeeze_vol / base_vol if base_vol > 0 else float("inf")
    _record("A2", "PASS" if ratio >= 5.0 else "FAIL",
            f"vol {base_vol:.1f}→{squeeze_vol:.1f}/day ({ratio:.1f}×, need ≥5×)")


def anchor3_tone_distribution(df: pd.DataFrame) -> None:
    print("\n--- Anchor 3: tone distribution sanity ---")
    tones = df.avg_tone.dropna()
    if tones.empty:
        _record("A3", "FAIL", "no tone values in panel")
        return
    med = float(tones.median())
    frac10 = float(((tones >= -10) & (tones <= 10)).mean())
    hard_ok = float(tones.min()) >= -100 and float(tones.max()) <= 100
    ok = hard_ok and -5 <= med <= 5 and frac10 >= 0.95
    _record("A3", "PASS" if ok else "FAIL",
            f"median {med:.2f} (need ∈[-5,5]), {frac10:.1%} within ±10 (need ≥95%), "
            f"range [{tones.min():.1f}, {tones.max():.1f}] (need ⊆[-100,100])")


def anchor4_name_shape_regimes(df: pd.DataFrame) -> None:
    print("\n--- Anchor 4: name-shape regimes (entity-matching structure) ---")
    def _tot(ticker: str, channel: str) -> int:
        sel = df[(df.ticker == ticker) & (df.alias_type == channel)]
        return int(sel.n_articles.sum())

    aapl_core, aapl_full = _tot("AAPL", "core"), _tot("AAPL", "full")
    nflx_core, nflx_full = _tot("NFLX", "core"), _tot("NFLX", "full")
    ndaq_core, ndaq_full = _tot("NDAQ", "core"), _tot("NDAQ", "full")
    ba_core = _tot("BA", "core")
    jpm_alt = _tot("JPM", "alt")

    checks = [
        ("AAPL core==0", aapl_core == 0),
        ("AAPL full>0", aapl_full > 0),
        ("NFLX core>10×full", nflx_full > 0 and nflx_core > 10 * nflx_full),
        ("NDAQ core>50×full (pathology quarantined)", ndaq_full > 0 and ndaq_core > 50 * ndaq_full),
        # alias v2 (F407) recovery checks, pre-stated before the full v2 panel:
        ("BA core>1000 (leading-'The' fix)", ba_core > 1000),
        ("JPM alt>1000 (fused-name curated alias)", jpm_alt > 1000),
    ]
    failed = [name for name, ok in checks if not ok]
    detail = (f"AAPL core/full={aapl_core}/{aapl_full}, NFLX={nflx_core}/{nflx_full}, "
              f"NDAQ={ndaq_core}/{ndaq_full}, BA core={ba_core}, JPM alt={jpm_alt}")
    _record("A4", "PASS" if not failed else "FAIL",
            detail + (f" — failed: {', '.join(failed)}" if failed else ""))


def anchor5_coverage_continuity(df: pd.DataFrame) -> None:
    print("\n--- Anchor 5: coverage continuity / PIT sanity ---")
    days_present = set(df.date.unique())
    start, end = min(days_present), max(days_present)
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    missing = sorted(expected - days_present)
    unverified = [d for d in missing if d not in _VERIFIED_CORPUS_GAPS]
    future = end > date.today()
    ok = not unverified and not future
    _record("A5", "PASS" if ok else "FAIL",
            f"coverage {start} → {end}, {len(missing)} gap days "
            f"({len(missing) - len(unverified)} verified corpus outages, "
            f"{len(unverified)} UNVERIFIED)"
            + (f" (first unverified: {unverified[0]})" if unverified else "")
            + (", FUTURE-DATED rows present" if future else ""))


def main() -> int:
    print(f"F405 GKG panel probe — reading {_PANEL}")
    if not _PANEL.exists():
        print("PANEL NOT FOUND — build it first (news_gkg_ingest.py)")
        _record("A1", "NOT-RUN", "panel parquet missing")
        return 1
    df = pd.read_parquet(_PANEL)
    df["date"] = pd.to_datetime(df.date).dt.date
    print(f"panel: {len(df):,} rows, {df.ticker.nunique():,} tickers, "
          f"{df.date.min()} → {df.date.max()}")

    anchor1_nsc_derailment(df)
    anchor2_gme_squeeze(df)
    anchor3_tone_distribution(df)
    anchor4_name_shape_regimes(df)
    anchor5_coverage_continuity(df)

    n_fail = sum(1 for _, s, _ in _results if s == "FAIL")
    print(f"\n{'='*60}\nRESULT: {len(_results)} anchors, {n_fail} FAIL")
    for aid, status, _ in _results:
        print(f"  {aid}: {status}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
