"""F349/F350 Smoke Probe — pre-stated face-validity anchors.

Runs 5 F350 (regime-breakdown) + 5 F349 (sector-peer) anchors against real
cached data and prints PASS/FAIL per anchor with the measured value.

F338 discipline: real-data probes are mandatory before believing a new
instrument.  This script is the gate artifact.

Usage:
    python3 backend/research/smoke_probe_f349_f350.py [--study STUDY_DIR]

    STUDY_DIR defaults to the most-recent study in
    backend/data/turnaround/event_studies/ (meta.json + events.ndjson expected).
    If no study exists yet, run a small real-data event study first.

Prerequisites:
    - backend/data/turnaround/regime_states.json must exist.
    - Run backend/scripts/fetch_missing_sic.py FIRST to extend SIC coverage.
    - backend/data/turnaround/event_studies/<STUDY_NAME>/events.ndjson and meta.json.
    - Missing events.ndjson → FAIL (not skip): the artifact must exist to validate F338.

Exits 0 if all anchors pass; 1 if any fail.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

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
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

_REGIME_STATES_PATH = _BACKEND_DIR / "data" / "turnaround" / "regime_states.json"
_STUDIES_DIR = _BACKEND_DIR / "data" / "turnaround" / "event_studies"
_SUBMISSIONS_DIR = _BACKEND_DIR / "data" / "turnaround" / "edgar_cache" / "submissions"


# ---------------------------------------------------------------------------
# Anchor definitions (F338: pre-stated before reading the data)
# ---------------------------------------------------------------------------

def probe_f350_regime_distribution(meta: dict) -> tuple[bool, str]:
    """F350 anchor 1: Regime distribution matches known proportions from the real file.

    Real regime_states.json (2015-2024, state_counts):
      RISK_ON=1697, NEUTRAL=485, STRESS=328, RISK_OFF=6, total≈2516

    Expected event distribution (fraction of events matching each state's base rate):
      RISK_ON: ~68% of events (1697/2516)
      NEUTRAL: ~19%
      STRESS:  ~13%
      RISK_OFF: <1% (6/2516 = 0.24%)

    Anchor: in any explore set of 50+ events, RISK_ON must be the plurality
    regime (count > NEUTRAL and > STRESS and > RISK_OFF).
    """
    bd = meta.get("regime_breakdown", {})
    if not bd:
        return False, "regime_breakdown absent from meta"
    risk_on_n = bd.get("RISK_ON", {}).get("n_events", 0)
    neutral_n = bd.get("NEUTRAL", {}).get("n_events", 0)
    stress_n = bd.get("STRESS", {}).get("n_events", 0)
    risk_off_n = bd.get("RISK_OFF", {}).get("n_events", 0)
    total = risk_on_n + neutral_n + stress_n + risk_off_n
    if total < 50:
        return None, f"not run (only {total} events, need >=50 for distribution anchor)"
    pass_ = risk_on_n > neutral_n and risk_on_n > stress_n and risk_on_n > risk_off_n
    detail = (f"RISK_ON={risk_on_n}, NEUTRAL={neutral_n}, STRESS={stress_n}, "
              f"RISK_OFF={risk_off_n}, total={total}")
    return pass_, detail


def probe_f350_risk_off_rare(meta: dict) -> tuple[bool, str]:
    """F350 anchor 2: RISK_OFF (crisis) is rare — never load-bearing.

    Real data: RISK_OFF = 6 days in 2515 total (0.24%). In any study over
    the 2015-2024 explore window, n_events[RISK_OFF] should be very small
    (<= 5% of total events).
    """
    bd = meta.get("regime_breakdown", {})
    if not bd:
        return False, "regime_breakdown absent"
    risk_off_n = bd.get("RISK_OFF", {}).get("n_events", 0)
    total = sum(bd.get(s, {}).get("n_events", 0) for s in ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"))
    if total == 0:
        return False, "no events in regime_breakdown"
    frac = risk_off_n / total
    pass_ = frac <= 0.05
    detail = f"RISK_OFF n={risk_off_n}, total={total}, fraction={frac:.3f} (<= 0.05 required)"
    return pass_, detail


def probe_f350_low_count_flag_on_risk_off(meta: dict) -> tuple[bool, str]:
    """F350 anchor 3: RISK_OFF gets LOW_COUNT_FLAG (n < 10 threshold).

    Given RISK_OFF's base rate of 6/2516 ≈ 0.24%, virtually any real study
    will have n[RISK_OFF] < 10 and must receive LOW_COUNT_FLAG=True.
    """
    bd = meta.get("regime_breakdown", {})
    if not bd:
        return False, "regime_breakdown absent"
    risk_off_blk = bd.get("RISK_OFF", {})
    n = risk_off_blk.get("n_events", 0)
    flag = risk_off_blk.get("LOW_COUNT_FLAG", False)
    if n >= 10:
        # If somehow RISK_OFF is common in this study, don't fail the flag check.
        return True, f"RISK_OFF n={n} >= 10; LOW_COUNT_FLAG not required"
    pass_ = flag is True
    detail = f"RISK_OFF n={n}, LOW_COUNT_FLAG={flag} (must be True when n<10)"
    return pass_, detail


def probe_f350_sign_agreement_valid(meta: dict) -> tuple[bool, str]:
    """F350 anchor 4: sign_agreement per regime is in [0.40, 1.0] (internal consistency).

    A sign_agreement < 0.40 indicates the regime's effect is unstable within-regime.
    """
    bd = meta.get("regime_breakdown", {})
    if not bd:
        return False, "regime_breakdown absent"
    horizons = meta.get("horizons", [])
    if not horizons:
        return False, "no horizons in meta"
    h0 = horizons[0]
    bad = []
    for state in ("RISK_ON", "NEUTRAL", "STRESS"):
        blk = bd.get(state, {})
        ph = blk.get("per_horizon", {})
        hdata = ph.get(h0) or ph.get(str(h0)) or {}
        sa = hdata.get("sign_agreement")
        n = hdata.get("n", 0)
        if n >= 5 and sa is not None and sa < 0.40:
            bad.append(f"{state}: sign_agreement={sa:.3f} < 0.40 (n={n})")
    pass_ = len(bad) == 0
    detail = f"All regimes sign_agreement >= 0.40" if pass_ else f"Violations: {bad}"
    return pass_, detail


def probe_f350_per_horizon_structure(meta: dict) -> tuple[bool, str]:
    """F350 anchor 5: regime_breakdown.per_horizon has n, mean_excess_pct, sign_agreement
    for all expected horizons."""
    bd = meta.get("regime_breakdown", {})
    horizons = meta.get("horizons", [])
    if not bd or not horizons:
        return False, "regime_breakdown or horizons absent"
    h0 = horizons[0]
    missing_keys = []
    for state in ("RISK_ON", "NEUTRAL"):  # these should have data in most studies
        blk = bd.get(state, {})
        ph = blk.get("per_horizon", {})
        hdata = ph.get(h0) or ph.get(str(h0)) or {}
        for key in ("n", "mean_excess_pct", "sign_agreement"):
            if key not in hdata:
                missing_keys.append(f"{state}[{h0}d].{key}")
    pass_ = len(missing_keys) == 0
    detail = f"All expected keys present" if pass_ else f"Missing: {missing_keys}"
    return pass_, detail


def probe_f349_sic_coverage(meta: dict) -> tuple[bool, str]:
    """F349 anchor 1: meta.sic_coverage.coverage_pct > 70% (after SIC extension).

    Current baseline: 564/~3000 ≈ 19%. After fetch_missing_sic.py: ~73%.
    """
    cov = meta.get("sic_coverage")
    if cov is None:
        return False, "sic_coverage absent (universe_tickers not supplied?)"
    pct = cov.get("coverage_pct", 0)
    pass_ = pct >= 70.0
    detail = (f"coverage_pct={pct:.1f}% (>= 70% required after extension). "
              f"with_sic={cov.get('tickers_with_sic')}, "
              f"without_sic={cov.get('tickers_without_sic')}")
    return pass_, detail


def probe_f349_peer_fallback_rate(meta: dict) -> tuple[bool, str]:
    """F349 anchor 2: Peer fallback rate to universe < 20% (good SIC coverage).

    After extension, most events should resolve to 3-digit or 2-digit peers.
    """
    fs = meta.get("sic_fallback_stats")
    if fs is None:
        return False, "sic_fallback_stats absent"
    total = sum(fs.values())
    if total == 0:
        return False, "no SIC lookups recorded (no floor-passing events?)"
    univ_frac = fs.get("universe", 0) / total
    pass_ = univ_frac < 0.20
    detail = (f"universe_fallback={fs.get('universe',0)}, "
              f"3_digit={fs.get('3_digit',0)}, 2_digit={fs.get('2_digit',0)}, "
              f"total={total}, universe_frac={univ_frac:.3f} (< 0.20 required)")
    return pass_, detail


def probe_f349_peer_median_excess_in_per_horizon(meta: dict) -> tuple[bool, str]:
    """F349 anchor 3: meta.per_horizon[h].peer_median_excess_pct is populated (not None for all).

    At least one horizon must have a non-None peer_median_excess_pct when SIC
    coverage is adequate.
    """
    per_h = meta.get("per_horizon", {})
    horizons = meta.get("horizons", [])
    if not per_h or not horizons:
        return False, "per_horizon or horizons absent"
    h0 = str(horizons[0])
    cov = meta.get("sic_coverage") or {}
    pct = cov.get("coverage_pct", 0)
    if pct < 5:
        return None, f"not run — SIC coverage only {pct:.1f}% (too low for peer median test)"
    hdata = per_h.get(h0, {})
    pme = hdata.get("peer_median_excess_pct")
    pass_ = pme is not None
    detail = f"per_horizon[{h0}d].peer_median_excess_pct = {pme}"
    return pass_, detail


def probe_f349_peer_excess_on_outcomes(outcomes: list[dict]) -> tuple[bool, str]:
    """F349 anchor 4: fwd_peer_excess_pct populated on entered outcomes (not all None).

    For outcomes with split=explore and floor_status=ok, at least some must
    have a non-None peer_excess (indicates SIC lookup succeeded).

    PY-02: reads from events.ndjson rows.  fwd_peer_excess_pct is a JSON dict
    keyed by string horizon ("21", "63", …) in the ndjson format.
    """
    entered = [o for o in outcomes if o.get("split") == "explore"
               and o.get("floor_status") == "ok"]
    if len(entered) < 5:
        return None, f"not run — only {len(entered)} entered explore events (<5)"
    # fwd_peer_excess_pct in events.ndjson is {"21": float_or_null, "63": ...}
    non_none = [
        o for o in entered
        if any(
            v not in (None, "None", "nan")
            for v in (o.get("fwd_peer_excess_pct") or {}).values()
        )
    ]
    frac = len(non_none) / len(entered)
    pass_ = frac > 0.10  # at least 10% of events must have peer excess populated
    detail = (f"{len(non_none)}/{len(entered)} entered events have any peer_excess populated "
              f"(fraction={frac:.2f}, > 0.10 required)")
    return pass_, detail


def probe_f349_fallback_stats_sum_matches_events(meta: dict, outcomes: list[dict]) -> tuple[bool, str]:
    """F349 anchor 5: sic_fallback_stats sum equals count of floor-OK events.

    Each floor-passing event contributes exactly one fallback-level counter.
    DI-08: this holds even when universe.json is absent (forced universe-fallback counted).

    PY-02: reads from events.ndjson rows (split and floor_status fields).
    """
    fs = meta.get("sic_fallback_stats")
    if fs is None:
        return False, "sic_fallback_stats absent"
    entered = [o for o in outcomes
               if o.get("split") in ("explore", "confirm")
               and o.get("floor_status") == "ok"]
    fs_total = sum(fs.values())
    pass_ = fs_total == len(entered)
    detail = (f"sic_fallback_stats total={fs_total}, "
              f"floor-ok events={len(entered)}")
    return pass_, detail


# ---------------------------------------------------------------------------
# Study loading
# ---------------------------------------------------------------------------

def _load_study(study_dir: Path) -> tuple[dict, list[dict]]:
    """Load meta.json and events.ndjson from a study directory.

    PY-02: reads events.ndjson (the artifact the harness actually writes).
    Missing events.ndjson raises FileNotFoundError → caller logs FAIL (not skip).
    """
    meta_path = study_dir / "meta.json"
    ndjson_path = study_dir / "events.ndjson"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found: {meta_path}")
    if not ndjson_path.exists():
        raise FileNotFoundError(
            f"events.ndjson not found: {ndjson_path} — "
            "run a real event study first (harness writes events.ndjson, not outcomes.csv)"
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    outcomes: list[dict] = []
    for line in ndjson_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            outcomes.append(json.loads(line))
    return meta, outcomes


def _find_latest_study() -> Optional[Path]:
    """Find the most-recently-modified study directory."""
    if not _STUDIES_DIR.exists():
        return None
    candidates = [d for d in _STUDIES_DIR.iterdir() if d.is_dir() and (d / "meta.json").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="F349/F350 face-validity smoke probe (F338 gate artifact)."
    )
    parser.add_argument(
        "--study", type=Path, default=None,
        help="Path to a study directory (default: most-recent in event_studies/).",
    )
    args = parser.parse_args(argv)

    study_dir = args.study or _find_latest_study()
    if study_dir is None:
        log.error(
            "No study directory found. Run a small event study first, or pass --study PATH."
        )
        return 1

    log.info("Loading study from: %s", study_dir)
    try:
        meta, outcomes = _load_study(study_dir)
    except Exception as exc:
        log.error("Failed to load study: %s", exc)
        return 1

    log.info(
        "Study: %s | n_events=%d | horizons=%s",
        meta.get("study_name"), meta.get("n_events"), meta.get("horizons"),
    )

    # Run all anchors.
    anchors = [
        # F350
        ("F350-A1 regime-distribution (RISK_ON plurality)", probe_f350_regime_distribution(meta)),
        ("F350-A2 RISK_OFF rare (<= 5% of events)",         probe_f350_risk_off_rare(meta)),
        ("F350-A3 RISK_OFF gets LOW_COUNT_FLAG",             probe_f350_low_count_flag_on_risk_off(meta)),
        ("F350-A4 sign_agreement in [0.40,1.0]",             probe_f350_sign_agreement_valid(meta)),
        ("F350-A5 per_horizon structure complete",           probe_f350_per_horizon_structure(meta)),
        # F349
        ("F349-A1 SIC coverage > 70% post-extension",       probe_f349_sic_coverage(meta)),
        ("F349-A2 peer fallback to universe < 20%",         probe_f349_peer_fallback_rate(meta)),
        ("F349-A3 peer_median_excess_pct in per_horizon",   probe_f349_peer_median_excess_in_per_horizon(meta)),
        ("F349-A4 peer_excess populated on outcomes",       probe_f349_peer_excess_on_outcomes(outcomes)),
        ("F349-A5 fallback_stats sum == floor-ok events",   probe_f349_fallback_stats_sum_matches_events(meta, outcomes)),
    ]

    n_pass = 0
    n_fail = 0
    n_notrun = 0
    print()
    print(f"Smoke probe: {study_dir.name}")
    print("-" * 72)
    for name, (passed, detail) in anchors:
        # Three honest states (F338 accounting): an anchor that could not be
        # exercised (n too small, coverage too low) is NOT-RUN — never PASS.
        # A skipped anchor does not gate this run, but it must be re-evaluated
        # on the first study large enough to exercise it.
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
    print(f"  Result: {n_pass} PASS / {n_fail} FAIL / {n_notrun} NOT-RUN")
    if n_notrun:
        print("  NOT-RUN anchors must be re-evaluated on the first full-size study.")
    print()

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
