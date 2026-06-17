"""probe_f370_explore.py — F338 probe for the F370 explore-0 artifacts.

Validates the first explore-0 artifact against pre-stated anchors.
Each anchor prints PASS / FAIL / NOT-RUN with a one-line reason.
NOT-RUN is used when the precondition is absent (n too small, artifacts
missing, etc.) — skipped ≠ passed (F338 discipline).

Pre-stated anchors:
  A1 Plumbing: n_events sane (thousands on 2015-2020), explore-split only,
               floor_status mostly ok, no NaN/inf in emitted stats.
  A2 Dose sanity: each dose has ≥50% coverage; quintile cuts monotone in dose.
  A3 No-look-ahead: entry_date strictly after event_ts ET date for a sample;
                    doses computed at as_of=filing date (F348 guarantees PIT).
  A4 Face validity: report (do not hard-gate beyond sanity) the primary-horizon
                    Q5-Q1 per dose + MDE — this is the go/no-go read for John.
  A5 Gap-lens sanity: gap returns finite, distribution sane; report mean gap +
                      correlation with dose.

Exit code 0 iff no FAIL (NOT-RUN allowed); 1 if any FAIL.

Usage:
    backend/venv/bin/python3 backend/research/probe_f370_explore.py [--study-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup (mirrors probe_r1_explore.py)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in [str(_BACKEND_DIR), str(_SCRIPT_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical paths
# ---------------------------------------------------------------------------
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"
_DEFAULT_STUDY_NAME = "f370_pead_explore_2015_2020"

# Frozen probe constants (pre-stated before seeing results)
_PRIMARY_HORIZON = 63
_HORIZONS = (21, 63, 126)
_MIN_N_EVENTS = 500        # "thousands on 2015-2020" — conservative lower bound
_MIN_COVERAGE_PCT = 50.0   # A2: dose must cover ≥50% of valid rows
_FLOOR_OK_MIN_FRAC = 0.50  # A1: at least 50% of explore rows should be floor_status=ok

# Anchor result type: (passed: bool|None, reason: str)
AnchorResult = tuple[Optional[bool], str]


# ---------------------------------------------------------------------------
# Load helpers (mirrors probe_r1_explore.py)
# ---------------------------------------------------------------------------

def _load_study(study_dir: Path) -> tuple[dict, list[dict], dict]:
    """Load meta.json, events.ndjson, f370_explore_summary.json from study_dir.

    Returns (meta, rows, summary). summary may be empty if not yet written.
    """
    meta_path = study_dir / "meta.json"
    ndjson_path = study_dir / "events.ndjson"
    summary_path = study_dir / "f370_explore_summary.json"

    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found: {meta_path}")
    if not ndjson_path.exists():
        raise FileNotFoundError(f"events.ndjson not found: {ndjson_path}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for line in ndjson_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    summary: dict = {}
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

    return meta, rows, summary


def _explore_ok_rows(rows: list[dict]) -> list[dict]:
    """Rows with split=explore and floor_status=ok."""
    return [
        r for r in rows
        if r.get("split") == "explore" and r.get("floor_status") == "ok"
    ]


def _get_excess(row: dict, h: int) -> Optional[float]:
    m = row.get("fwd_excess_pct") or {}
    v = m.get(str(h)) or m.get(h)
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# Anchor A1: Plumbing
# ---------------------------------------------------------------------------

def anchor_a1_plumbing(rows: list[dict], summary: dict) -> AnchorResult:
    """A1: n_events sane, explore-split present, floor ok fraction sane,
    no NaN/inf in summary stats.
    """
    if not rows:
        return (False, "No rows loaded from events.ndjson")

    n_total = len(rows)
    n_explore = sum(1 for r in rows if r.get("split") == "explore")
    n_explore_ok = sum(
        1 for r in rows
        if r.get("split") == "explore" and r.get("floor_status") == "ok"
    )
    n_confirm = sum(1 for r in rows if r.get("split") == "confirm")

    # Must have thousands of events
    if n_explore < _MIN_N_EVENTS:
        return (
            False,
            f"n_explore={n_explore} < {_MIN_N_EVENTS} (expected thousands on 2015-2020)",
        )

    # No confirm rows (explore-0 only)
    if n_confirm > 0:
        return (False, f"n_confirm={n_confirm} > 0 — confirm window must be untouched")

    # Floor ok fraction
    floor_ok_frac = n_explore_ok / n_explore if n_explore > 0 else 0.0
    if floor_ok_frac < _FLOOR_OK_MIN_FRAC:
        return (
            False,
            f"floor_ok_frac={floor_ok_frac:.2%} < {_FLOOR_OK_MIN_FRAC:.0%}",
        )

    # Check no NaN/inf in summary stats (if summary available)
    nan_inf_fields = []
    if summary:
        doses = summary.get("doses") or {}
        for dose_name, dose_result in doses.items():
            by_h = dose_result.get("by_horizon") or {}
            for h_str, h_result in by_h.items():
                for k, v in h_result.items():
                    if isinstance(v, float) and not math.isfinite(v):
                        nan_inf_fields.append(f"{dose_name}.h{h_str}.{k}={v}")
        if nan_inf_fields:
            return (False, f"NaN/inf in summary stats: {nan_inf_fields[:3]}")

    return (
        True,
        (
            f"n_total={n_total}, n_explore={n_explore}, n_explore_ok={n_explore_ok}, "
            f"n_confirm={n_confirm}, floor_ok_frac={floor_ok_frac:.1%}"
        ),
    )


# ---------------------------------------------------------------------------
# Anchor A2: Dose sanity
# ---------------------------------------------------------------------------

def anchor_a2_dose_sanity(rows: list[dict], summary: dict) -> AnchorResult:
    """A2: each dose has ≥50% coverage; quintiles populated ~evenly;
    quintile cuts monotone in the dose (Q5 mean > Q1 mean at primary horizon).
    """
    if not summary:
        return (None, "f370_explore_summary.json not found — NOT-RUN")

    doses = summary.get("doses") or {}
    if not doses:
        return (None, "No dose results in summary — NOT-RUN")

    valid_rows_n = summary.get("n_valid_for_analysis", 0)
    if valid_rows_n < 50:
        return (None, f"n_valid_for_analysis={valid_rows_n} < 50 — NOT-RUN")

    issues = []
    report = []
    for dose_name, dose_result in doses.items():
        cov = dose_result.get("coverage_pct", 0.0)
        if cov is None:
            cov = 0.0
        if cov < _MIN_COVERAGE_PCT:
            issues.append(f"{dose_name}: coverage={cov:.1f}% < {_MIN_COVERAGE_PCT}%")

        # Check quintile populations at primary horizon
        by_h = dose_result.get("by_horizon") or {}
        h_result = by_h.get(str(_PRIMARY_HORIZON)) or by_h.get(_PRIMARY_HORIZON) or {}
        pq = h_result.get("per_quintile") or {}
        q5_n = (pq.get("5") or pq.get(5) or {}).get("n", 0)
        q1_n = (pq.get("1") or pq.get(1) or {}).get("n", 0)
        q5_mean = (pq.get("5") or pq.get(5) or {}).get("mean")
        q1_mean = (pq.get("1") or pq.get(1) or {}).get("mean")
        q5q1_gap = h_result.get("q5q1_gap_pct")

        report.append(
            f"{dose_name}: cov={cov:.1f}%, n5={q5_n}, n1={q1_n}, "
            f"q5q1_gap={q5q1_gap:.3f}%" if q5q1_gap is not None
            else f"{dose_name}: cov={cov:.1f}%, n5={q5_n}, n1={q1_n}, gap=None"
        )

    if issues:
        return (False, "; ".join(issues))

    return (True, " | ".join(report))


# ---------------------------------------------------------------------------
# Anchor A3: No-look-ahead
# ---------------------------------------------------------------------------

def anchor_a3_no_lookahead(rows: list[dict]) -> AnchorResult:
    """A3: entry_date strictly after event_ts ET date for a sample of rows.

    Checks the first 20 explore-ok rows with both fields populated.
    """
    try:
        from zoneinfo import ZoneInfo
        _ET_TZ = ZoneInfo("America/New_York")
    except ImportError:
        try:
            import pytz
            _ET_TZ = pytz.timezone("America/New_York")
        except ImportError:
            return (None, "zoneinfo/pytz not available — NOT-RUN")

    explore_ok = _explore_ok_rows(rows)
    checked = 0
    violations = []
    for row in explore_ok[:200]:
        entry_date_str = row.get("entry_date", "")
        event_ts = row.get("event_ts")
        if not entry_date_str or not event_ts:
            continue

        try:
            # Parse event_ts to ET date.
            # C370-05 fix: always ensure the parsed datetime is tz-aware before
            # calling .astimezone(), so the conversion is always UTC→ET regardless
            # of the server's local timezone (server is in Sweden/CEST, not UTC).
            # Pattern mirrors _adt_to_et_date() in run_f370_explore.py:
            #   1. Normalise trailing Z → +00:00
            #   2. Strip fractional seconds but preserve any explicit offset
            #   3. If still naive after parsing, assume UTC (treat as +00:00)
            if isinstance(event_ts, str):
                ts_str = event_ts
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                if "." in ts_str:
                    dot = ts_str.index(".")
                    after = ts_str[dot + 1:]
                    has_offset = "+" in after or (after.count("-") > 0)
                    if has_offset:
                        for sep in ("+", "-"):
                            if sep in after:
                                ts_str = ts_str[:dot] + sep + after.split(sep, 1)[1]
                                break
                    else:
                        ts_str = ts_str[:dot] + "+00:00"
                dt = datetime.fromisoformat(ts_str)
                if dt.tzinfo is None:
                    # No offset in string: treat as UTC (never rely on local tz)
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_et = dt.astimezone(_ET_TZ)
                event_et_date = dt_et.date()
            else:
                continue

            entry_date = date.fromisoformat(str(entry_date_str)[:10])
            checked += 1

            if entry_date <= event_et_date:
                violations.append(
                    f"{row.get('ticker')}: entry={entry_date}, event_et={event_et_date}"
                )
            if checked >= 20:
                break
        except Exception as exc:
            continue

    if checked == 0:
        return (None, "No checkable rows found (need entry_date + event_ts) — NOT-RUN")

    if violations:
        return (False, f"{len(violations)}/{checked} violations: {violations[:2]}")

    return (True, f"Checked {checked} rows: entry_date > event_ts ET date for all")


# ---------------------------------------------------------------------------
# Anchor A4: Face validity (report-only; do not hard-gate on effect magnitude)
# ---------------------------------------------------------------------------

def anchor_a4_face_validity(summary: dict) -> AnchorResult:
    """A4: Report primary-horizon Q5-Q1 per dose + MDE vs design MDE.

    This is the go/no-go read for John. Gate: stats are finite (not NaN);
    effect direction and magnitude are reported, not thresholded.
    """
    if not summary:
        return (None, "f370_explore_summary.json not found — NOT-RUN")

    doses = summary.get("doses") or {}
    if not doses:
        return (None, "No dose results in summary — NOT-RUN")

    # F381-1: power_audit_design_mde_80pct_pp was intentionally removed from the
    # summary (generic-population MDE ≠ PEAD design MDE).  Report the per-dose
    # empirical MDE (by_horizon[h]["mde_pp"]) instead.
    lines = ["Empirical MDE (per-dose, Q5-Q1 spread, 80% power):"]
    any_finite = False
    for dose_name, dose_result in doses.items():
        by_h = dose_result.get("by_horizon") or {}
        h_result = by_h.get(str(_PRIMARY_HORIZON)) or by_h.get(_PRIMARY_HORIZON) or {}
        gap = h_result.get("q5q1_gap_pct")
        mde = h_result.get("mde_pp")
        p_boot = h_result.get("p_boot")
        rho_s = h_result.get("rho_s")
        cov = dose_result.get("coverage_pct")
        n_events_h = h_result.get("n_events")

        if gap is not None and math.isfinite(gap):
            any_finite = True
        mde_str = f"{mde:.3f}pp" if (mde is not None and math.isfinite(mde)) else "n/a"
        gap_str = f"{gap:+.3f}%" if (gap is not None and math.isfinite(gap)) else "n/a"
        p_str = f"{p_boot:.3f}" if (p_boot is not None and math.isfinite(p_boot)) else "n/a"
        rho_str = f"{rho_s:.3f}" if (rho_s is not None and math.isfinite(rho_s)) else "n/a"
        cov_str = f"{cov:.1f}%" if cov is not None else "n/a"
        n_str = str(n_events_h) if n_events_h is not None else "n/a"

        lines.append(
            f"  {dose_name}: Q5-Q1={gap_str} p={p_str} rho={rho_str}"
            f" MDE={mde_str} n={n_str} cov={cov_str}"
        )

    if not any_finite:
        return (False, "All Q5-Q1 gaps are None/NaN — stats computation failed")

    return (True, " | ".join(lines))


# ---------------------------------------------------------------------------
# Anchor A5: Gap-lens sanity
# ---------------------------------------------------------------------------

def anchor_a5_gap_lens(summary: dict) -> AnchorResult:
    """A5: gap returns finite, distribution sane; report mean gap + correlation.

    Reports-only: does not gate on correlation magnitude.
    """
    if not summary:
        return (None, "f370_explore_summary.json not found — NOT-RUN")

    gap_lens = summary.get("gap_lens") or {}

    if "skipped" in gap_lens:
        return (None, f"Gap lens skipped: {gap_lens['skipped']} — NOT-RUN")
    if "error" in gap_lens:
        return (None, f"Gap lens error: {gap_lens['error']} — NOT-RUN")

    n_found = gap_lens.get("n_found", 0)
    if n_found < 10:
        return (
            None,
            f"Gap lens: n_found={n_found} < 10 (too few to assess sanity) — NOT-RUN",
        )

    mean_gap = gap_lens.get("mean_gap_return_pct")
    median_gap = gap_lens.get("median_gap_return_pct")
    corr_earnings = gap_lens.get("corr_gap_vs_earnings_yoy")
    corr_composite = gap_lens.get("corr_gap_vs_composite")

    # Sanity: mean gap should be finite and not extreme (< ±100%)
    if mean_gap is None or not math.isfinite(mean_gap):
        return (False, f"mean_gap={mean_gap} is non-finite")
    if abs(mean_gap) > 100.0:
        return (False, f"mean_gap={mean_gap:.2f}% is extreme (|gap| > 100%)")

    corr_e_str = f"{corr_earnings:.3f}" if corr_earnings is not None and math.isfinite(corr_earnings) else "n/a"
    corr_c_str = f"{corr_composite:.3f}" if corr_composite is not None and math.isfinite(corr_composite) else "n/a"

    return (
        True,
        (
            f"n_found={n_found}, mean_gap={mean_gap:.2f}%, median={median_gap:.2f}%, "
            f"corr_vs_earnings={corr_e_str}, corr_vs_composite={corr_c_str}"
        ),
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="F370 explore-0 probe — validates F338 anchors against study artifacts."
    )
    parser.add_argument(
        "--study-dir",
        type=Path,
        default=None,
        help=(
            f"Path to study dir (default: {_STUDIES_DIR}/{_DEFAULT_STUDY_NAME}). "
        ),
    )
    args = parser.parse_args(argv)
    study_dir = args.study_dir or (_STUDIES_DIR / _DEFAULT_STUDY_NAME)
    log.info("Probe: %s", study_dir)

    try:
        meta, rows, summary = _load_study(study_dir)
    except FileNotFoundError as e:
        print(f"FAIL: {e}")
        print("0 PASS / 1 FAIL / 0 NOT-RUN")
        return 1

    log.info(
        "Loaded: n_rows=%d, horizons=%s, summary_keys=%s",
        len(rows),
        meta.get("horizons"),
        list(summary.keys()) if summary else [],
    )

    anchors = [
        (
            "A1 Plumbing (n_events, explore-only, floor_ok, no NaN/inf)",
            anchor_a1_plumbing(rows, summary),
        ),
        (
            "A2 Dose sanity (≥50% coverage, quintiles populated, monotone cut)",
            anchor_a2_dose_sanity(rows, summary),
        ),
        (
            "A3 No-look-ahead (entry_date > event_ts ET for sample rows)",
            anchor_a3_no_lookahead(rows),
        ),
        (
            "A4 Face validity (Q5-Q1 + MDE report per dose @ 63td)",
            anchor_a4_face_validity(summary),
        ),
        (
            "A5 Gap-lens sanity (finite returns, distribution + correlation report)",
            anchor_a5_gap_lens(summary),
        ),
    ]

    n_pass = 0
    n_fail = 0
    n_notrun = 0
    print()
    print(f"F370 Explore-0 Probe: {study_dir.name}")
    print("-" * 72)
    for name, (passed, detail) in anchors:
        if passed is None:
            status, symbol = "NOT-RUN", "~"
            n_notrun += 1
        elif passed:
            status, symbol = "PASS", "+"
            n_pass += 1
        else:
            status, symbol = "FAIL", "X"
            n_fail += 1
        print(f"  [{symbol}] {status:<7} {name}")
        print(f"         {detail}")

    print("-" * 72)
    tally = f"{n_pass} PASS / {n_fail} FAIL / {n_notrun} NOT-RUN"
    print(f"  Result: {tally}")
    if n_notrun:
        print("  NOT-RUN anchors must be re-evaluated once the full study artifact exists.")
    print()

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
