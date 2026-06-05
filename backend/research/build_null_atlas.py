"""Build the regime-aware null atlas (F333).

Reads validation_result.json, emits null_atlas.json with per-cohort and
per-year statistics so downstream signal tests can compare against their
LOCAL null baseline rather than the global 45.3% average.

Usage:
    python3 backend/research/build_null_atlas.py

Output: backend/data/turnaround/null_atlas.json
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VALIDATION_PATH = _REPO_ROOT / "backend" / "data" / "turnaround" / "validation_result.json"
_UNIVERSE_PATH = _REPO_ROOT / "backend" / "data" / "turnaround" / "edgar_cache" / "universe.json"
_SUBMISSIONS_DIR = _REPO_ROOT / "backend" / "data" / "turnaround" / "edgar_cache" / "submissions"
_OUTPUT_PATH = _REPO_ROOT / "backend" / "data" / "turnaround" / "null_atlas.json"

SCHEMA_VERSION = 1
COVERAGE_GATE = 0.60  # minimum submission coverage to include sector dimension

# Unit 2 (D14): atlas v2 — when the events table is schema_version=2, cells are
# built from the bar-counted forward returns + cohort-relative excess at the
# three trading-day horizons instead of the v1 touch/hit fields.  The v1 path is
# preserved unchanged so the existing artifact still regenerates.
V2_HORIZONS = (21, 63, 126)
INSUFFICIENT_N = 30  # n<30 → cell flagged insufficient (same convention as v1)


# ---------------------------------------------------------------------------
# Atomic write (inlined from fileutil.py pattern; no FastAPI dep)
# ---------------------------------------------------------------------------
def _atomic_write_json(path: Path, obj: object) -> None:
    """Write obj as JSON to path atomically (tmp + rename, same directory)."""
    content = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)
    dir_ = str(path.parent)
    fd = tempfile.NamedTemporaryFile(
        mode="w", delete=False, dir=dir_, suffix=".tmp", encoding="utf-8"
    )
    try:
        fd.write(content)
        fd.flush()
        os.fsync(fd.fileno())
        try:
            fd.close()
        except Exception:
            pass
        os.replace(fd.name, str(path))
    except Exception:
        try:
            fd.close()
        except Exception:
            pass
        try:
            os.unlink(fd.name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Statistics helpers (stdlib only)
# ---------------------------------------------------------------------------
def _wilson_ci(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% confidence interval for a proportion.

    Returns (low, high) rounded to 6 decimal places.
    Returns (0.0, 0.0) if n == 0 (degenerate).
    """
    if n == 0:
        return (0.0, 0.0)
    p_hat = hits / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    lo = max(0.0, centre - margin)
    hi = min(1.0, centre + margin)
    return (round(lo, 6), round(hi, 6))


def _median(values: list[float]) -> float | None:
    """Median of a list. Returns None if empty."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _cohort_stats(events: list[dict]) -> dict:
    """Compute standard stats dict for a list of events."""
    n = len(events)
    if n == 0:
        return {"n": 0, "insufficient": True}

    hits = sum(1 for e in events if e.get("hit"))
    hit_rate = hits / n
    ci_lo, ci_hi = _wilson_ci(hits, n)

    net_returns = [e["net_return_pct"] for e in events if e.get("net_return_pct") is not None]
    horizon_returns = [
        e["horizon_end_return_pct"]
        for e in events
        if e.get("horizon_end_return_pct") is not None
    ]

    # Round-trip rate: fraction of ALL events that hit then reversed (return < 0).
    # PY-12: denominator is n (all events), NOT hits — this is P(hit ∧ reverse),
    # not P(reverse | hit). Named accordingly to prevent misinterpretation.
    round_trips = sum(
        1
        for e in events
        if e.get("hit") and e.get("horizon_end_return_pct") is not None and e["horizon_end_return_pct"] < 0
    )
    round_trip_rate = round_trips / n  # P(hit AND horizon_return<0) across all events

    n_null = sum(1 for e in events if e.get("is_null"))
    n_signal = sum(1 for e in events if not e.get("is_null"))

    result: dict = {
        "n": n,
        "hit_rate": round(hit_rate, 6),
        "wilson_ci_low": ci_lo,
        "wilson_ci_high": ci_hi,
        "median_net_return_pct": _median(net_returns),
        "median_horizon_end_return_pct": _median(horizon_returns),
        "round_trip_rate": round(round_trip_rate, 6),
        "n_null": n_null,
        "n_signal": n_signal,
    }

    if n < 30:
        result["insufficient"] = True

    return result


def _cohort_stats_v2(events: list[dict]) -> dict:
    """Unit 2 (D14): horizon-end excess-based cell stats for schema_version=2 events.

    For each of the 21/63/126 trading-day horizons, compute the cohort's median
    forward return, median cohort-relative excess, and the hit_v2 rate (fraction
    of events whose excess > 0).  None-valued horizon cells (incomplete horizon —
    data ended before N bars) are excluded from each horizon's denominator and the
    excluded count surfaced as n_complete[h].

    n is the cohort size; n<30 → insufficient flag (same convention as v1).
    """
    n = len(events)
    if n == 0:
        return {"n": 0, "insufficient": True}

    horizons: dict[str, dict] = {}
    for h in V2_HORIZONS:
        fwd_key = f"fwd_return_{h}d"
        exc_key = f"excess_{h}d"
        hit_key = f"hit_v2_{h}d"
        fwd_vals = [e[fwd_key] for e in events if e.get(fwd_key) is not None]
        exc_vals = [e[exc_key] for e in events if e.get(exc_key) is not None]
        hit_flags = [e[hit_key] for e in events if e.get(hit_key) is not None]
        n_complete = len(fwd_vals)
        hit_v2_rate = (sum(1 for f in hit_flags if f) / len(hit_flags)) if hit_flags else None
        ci_lo, ci_hi = (
            _wilson_ci(sum(1 for f in hit_flags if f), len(hit_flags))
            if hit_flags else (0.0, 0.0)
        )
        horizons[f"{h}d"] = {
            "n_complete": n_complete,
            "median_fwd_return_pct": _median(fwd_vals),
            "median_excess_pct": _median(exc_vals),
            "hit_v2_rate": round(hit_v2_rate, 6) if hit_v2_rate is not None else None,
            "hit_v2_ci_low": ci_lo,
            "hit_v2_ci_high": ci_hi,
            # Per-horizon insufficiency: too few completed events at this horizon.
            "insufficient": n_complete < INSUFFICIENT_N,
        }

    n_null = sum(1 for e in events if e.get("is_null"))
    n_signal = sum(1 for e in events if not e.get("is_null"))

    result: dict = {
        "n": n,
        "n_null": n_null,
        "n_signal": n_signal,
        "horizons": horizons,
    }
    if n < INSUFFICIENT_N:
        result["insufficient"] = True
    return result


def _price_band(entry_price: float | None) -> str:
    """Classify entry_price into penny/low/mid/high band."""
    if entry_price is None:
        return "unknown"
    if entry_price < 2.0:
        return "penny"
    if entry_price < 10.0:
        return "low"
    if entry_price < 50.0:
        return "mid"
    return "high"


# ---------------------------------------------------------------------------
# Coverage gate check
# ---------------------------------------------------------------------------
def _check_sector_coverage(events: list[dict]) -> tuple[bool, float, str]:
    """Return (gate_passed, coverage_fraction, reason_str)."""
    pond_tickers: set[str] = set(e["ticker"].upper() for e in events)

    # Load universe.json for ticker -> CIK mapping
    if not _UNIVERSE_PATH.exists():
        return False, 0.0, "universe.json not found"
    with open(_UNIVERSE_PATH, encoding="utf-8") as f:
        universe = json.load(f)

    ticker_to_cik: dict[str, str] = {}
    for entry in universe.values():
        t = entry.get("ticker", "").upper()
        cik = str(entry.get("cik_str", "")).zfill(10)
        if t:
            ticker_to_cik[t] = cik

    # Map pond tickers to CIKs
    pond_ciks: dict[str, str] = {}
    for ticker in pond_tickers:
        if ticker in ticker_to_cik:
            pond_ciks[ticker] = ticker_to_cik[ticker]

    if not pond_ciks:
        return False, 0.0, "no pond tickers found in universe.json"

    # Check which CIKs have submission files
    # PY-09: normalize to 10-digit zero-padded CIK so both sets use the same format
    available_ciks: set[str] = set()
    if _SUBMISSIONS_DIR.exists():
        for fname in os.listdir(_SUBMISSIONS_DIR):
            if fname.endswith(".json"):
                stem = fname[:-5]
                try:
                    available_ciks.add(str(int(stem)).zfill(10))
                except ValueError:
                    pass  # skip non-integer filenames

    covered = sum(1 for cik in pond_ciks.values() if cik in available_ciks)
    coverage = covered / len(pond_ciks)
    passed = coverage >= COVERAGE_GATE

    reason = (
        f"{covered}/{len(pond_ciks)} pond tickers have submission files "
        f"(coverage={coverage:.1%}; gate={COVERAGE_GATE:.0%})"
    )
    return passed, round(coverage, 4), reason


def _build_sector_dimension(
    events: list[dict], stats_fn: Callable[[list[dict]], dict] = _cohort_stats
) -> dict:
    """Build per-sector stats using SIC descriptions from submission files.

    Unit 2 (D14): stats_fn selects v1 (_cohort_stats) or v2 (_cohort_stats_v2)
    cells so the sector dimension matches the rest of the atlas.

    PY-04: guard the universe.json open() — in normal flow _check_sector_coverage
    has already validated the file exists, but a direct call (e.g. a test) would
    otherwise raise an undiagnosed FileNotFoundError.
    """
    if not _UNIVERSE_PATH.exists():
        return {}
    # Load universe.json for ticker -> CIK
    with open(_UNIVERSE_PATH, encoding="utf-8") as f:
        universe = json.load(f)
    ticker_to_cik: dict[str, str] = {}
    for entry in universe.values():
        t = entry.get("ticker", "").upper()
        cik = str(entry.get("cik_str", "")).zfill(10)
        if t:
            ticker_to_cik[t] = cik

    # Load SIC from available submission files
    # PY-09: normalize file stem to 10-digit CIK to match ticker_to_cik format
    ticker_to_sic: dict[str, str] = {}
    if _SUBMISSIONS_DIR.exists():
        for fname in os.listdir(_SUBMISSIONS_DIR):
            if not fname.endswith(".json"):
                continue
            stem = fname[:-5]
            try:
                cik = str(int(stem)).zfill(10)
            except ValueError:
                continue
            with open(_SUBMISSIONS_DIR / fname, encoding="utf-8") as f:
                sub = json.load(f)
            sic_desc = sub.get("sicDescription", "Unknown")
            # Map all tickers that share this CIK
            for ticker, tc in ticker_to_cik.items():
                if tc == cik:
                    ticker_to_sic[ticker] = sic_desc

    # Group events by sector
    sector_events: dict[str, list[dict]] = {}
    for e in events:
        t = e["ticker"].upper()
        sector = ticker_to_sic.get(t, "Unknown")
        sector_events.setdefault(sector, []).append(e)

    return {sector: stats_fn(evts) for sector, evts in sorted(sector_events.items())}


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def build_atlas() -> dict:
    print(f"Reading events from {_VALIDATION_PATH} …")
    with open(_VALIDATION_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    events: list[dict] = raw["events"]
    print(f"  {len(events)} events loaded.")

    # Unit 2 (D14): branch on the events table schema_version. v2 events carry
    # bar-counted forward returns + cohort-relative excess; cells are built with
    # _cohort_stats_v2 (horizon-end excess cells). v1 events keep the original
    # touch/hit cells via _cohort_stats. Default 1 for legacy artifacts.
    events_schema = int(raw.get("schema_version", 1))
    atlas_version = 2 if events_schema >= 2 else 1
    _stats = _cohort_stats_v2 if atlas_version == 2 else _cohort_stats
    print(f"  events schema_version={events_schema} → building atlas v{atlas_version}")

    # --- Coverage gate ---
    sector_gate_passed, sector_coverage, sector_reason = _check_sector_coverage(events)
    print(f"Sector coverage gate: {sector_reason}")
    if not sector_gate_passed:
        print("  => Skipping sector dimension (coverage < 60%)")

    # --- Per-cohort stats (36 as_of dates) ---
    cohort_map: dict[str, list[dict]] = {}
    for e in events:
        cohort_map.setdefault(e["as_of"], []).append(e)

    per_cohort: dict[str, dict] = {}
    for as_of in sorted(cohort_map.keys()):
        per_cohort[as_of] = _stats(cohort_map[as_of])

    print(f"  {len(per_cohort)} cohorts built.")

    # --- Per-year rollups ---
    year_map: dict[str, list[dict]] = {}
    for e in events:
        year = e["as_of"][:4]
        year_map.setdefault(year, []).append(e)

    per_year: dict[str, dict] = {}
    for year in sorted(year_map.keys()):
        per_year[year] = _stats(year_map[year])

    print(f"  {len(per_year)} year rollups built.")

    # --- Entry-price band × cohort-year cells ---
    # Classify each event by price band and cohort year
    band_year_map: dict[str, dict[str, list[dict]]] = {}
    for e in events:
        band = _price_band(e.get("entry_price"))
        year = e["as_of"][:4]
        band_year_map.setdefault(band, {}).setdefault(year, []).append(e)

    price_band_x_year: dict[str, dict] = {}
    for band in sorted(band_year_map.keys()):
        price_band_x_year[band] = {}
        for year in sorted(band_year_map[band].keys()):
            price_band_x_year[band][year] = _stats(band_year_map[band][year])

    print(f"  Price-band × year cells built.")

    # --- Sector dimension (conditional) ---
    sector: dict | None = None
    if sector_gate_passed:
        sector = _build_sector_dimension(events, stats_fn=_stats)
        print(f"  Sector dimension built ({len(sector)} sectors).")

    # --- Meta block ---
    meta = {
        "source_file": str(_VALIDATION_PATH),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_events_total": len(events),
        "sector_coverage_gate": {
            "threshold": COVERAGE_GATE,
            "actual_coverage": sector_coverage,
            "passed": sector_gate_passed,
            "reason": sector_reason,
        },
        "events_schema_version": events_schema,
        "atlas_version": atlas_version,
        "usage": (
            "Denominator lookup for validation: compare a candidate's cohort-matched events "
            "against these local rates; cohort_dir_pct >=0.6 across populated cohorts is the "
            "standard robustness bar"
            + (
                ". Atlas v2: cells carry per-horizon (21/63/126 trading-day) median "
                "forward return, median cohort-relative excess, and hit_v2_rate; excess "
                "is market/cohort-excess (NOT beta-adjusted)."
                if atlas_version == 2 else ""
            )
        ),
    }

    atlas: dict = {
        # atlas schema_version: 2 when built from schema_version=2 events (v2 cells).
        "schema_version": atlas_version,
        "meta": meta,
        "per_cohort": per_cohort,
        "per_year": per_year,
        "price_band_x_year": price_band_x_year,
    }
    if sector is not None:
        atlas["sector"] = sector

    return atlas


def _sanity_check(atlas: dict, null_events: list[dict]) -> list[str]:
    """Run EDA-0605 anchor checks. Returns list of pass/fail strings.

    PY-03: null_events passed directly so anchor-3 computes null hit rate from
    actual null-flagged events, not from cohort-total hit_rate applied to n_null
    (which conflates signal and null rates, producing a spurious PASS).
    """
    results = []

    # 1. Overall null hit rate ~45.3% (global across all cohorts), weighted by cohort.
    # COR-03: the atlas per-cohort `hit_rate`/`n` are computed over ALL events
    # (signal + null) — signal events have a higher hit rate by construction, so the
    # blended rate is tilted upward and the 0.453 null anchor is meaningless against
    # it (it could PASS while the null composition has shifted).  Compute the
    # weighted null hit rate from is_null events only — grouped by cohort and weighted
    # by per-cohort null n — so anchor-1 truly anchors the NULL baseline (same null
    # slice anchor-3 uses, just cohort-weighted instead of pooled).
    null_by_cohort: dict[str, list[dict]] = {}
    for e in null_events:
        null_by_cohort.setdefault(e["as_of"], []).append(e)
    total_n = 0
    weighted_hits = 0.0
    for cohort_events in null_by_cohort.values():
        n = len(cohort_events)
        if n == 0:
            continue
        hr = sum(1 for e in cohort_events if e.get("hit")) / n
        total_n += n
        weighted_hits += hr * n
    overall_hr = weighted_hits / total_n if total_n else 0.0
    anchor1_pass = abs(overall_hr - 0.453) < 0.005
    results.append(
        f"[{'PASS' if anchor1_pass else 'FAIL'}] Overall hit rate: {overall_hr:.4f} "
        f"(expected ~0.453)"
    )

    # 2. 2020 year hit rate ~84.3%
    yr2020 = atlas["per_year"].get("2020", {})
    hr2020 = yr2020.get("hit_rate", 0.0)
    anchor2_pass = abs(hr2020 - 0.843) < 0.010
    results.append(
        f"[{'PASS' if anchor2_pass else 'FAIL'}] 2020 year hit rate: {hr2020:.4f} "
        f"(expected ~0.843)"
    )

    # 3. PY-03: compute null hit rate DIRECTLY from is_null=True events only.
    # Previous implementation applied cohort-level hit_rate to n_null, which is
    # circular (signal events have higher hit_rate, inflating the null estimate).
    null_n = len(null_events)
    null_hits = sum(1 for e in null_events if e.get("hit"))
    null_overall = null_hits / null_n if null_n else 0.0
    anchor3_pass = abs(null_overall - 0.4537) < 0.005
    results.append(
        f"[{'PASS' if anchor3_pass else 'FAIL'}] Null-slice hit rate: {null_overall:.4f} "
        f"(expected ~0.4537; n_null={null_n}; computed directly from is_null events)"
    )

    return results


def _sanity_check_v2(atlas: dict) -> list[str]:
    """Unit 2 (D14): v2 atlas sanity — every populated cohort cell exposes the
    three horizon sub-cells with median excess + hit_v2_rate keys.  The v1
    touch-metric anchors (45.3% null hit rate, 84.3% 2020) do NOT apply to v2
    cells, so they are intentionally skipped here."""
    results = []
    n_cells = 0
    n_with_horizons = 0
    for cell in atlas["per_cohort"].values():
        if cell.get("n", 0) == 0:
            continue
        n_cells += 1
        h = cell.get("horizons", {})
        if all(f"{d}d" in h for d in V2_HORIZONS):
            n_with_horizons += 1
    ok = n_cells > 0 and n_with_horizons == n_cells
    results.append(
        f"[{'PASS' if ok else 'FAIL'}] v2 horizon cells: {n_with_horizons}/{n_cells} "
        f"populated cohorts expose all {len(V2_HORIZONS)} horizons"
    )
    return results


def main() -> None:
    atlas = build_atlas()

    # PY-03: load null events directly for anchor-3 computation
    with open(_VALIDATION_PATH, encoding="utf-8") as f:
        _raw = json.load(f)
    null_events = [e for e in _raw["events"] if e.get("is_null")]

    # Unit 2 (D14): v1 touch-metric anchors only apply to the v1 atlas.
    if atlas.get("schema_version", 1) >= 2:
        sanity = _sanity_check_v2(atlas)
    else:
        sanity = _sanity_check(atlas, null_events)
    print("\nSanity checks:")
    for s in sanity:
        print(" ", s)
    atlas["meta"]["sanity_checks"] = sanity

    print(f"\nWriting atlas to {_OUTPUT_PATH} …")
    _atomic_write_json(_OUTPUT_PATH, atlas)
    print("Done.")


if __name__ == "__main__":
    main()
