"""S-1 One-Sample Direction Analysis — F414.

Reads meta.json written by the event_study harness (same as r1_analysis) and
performs a one-sample mean-excess direction test. This is the correct form when
the premise is about *direction* (does the all-event mean excess go negative?)
rather than *dose-response* (do high-dose events outperform low-dose events?).

Use this module when spec.analysis_form == "one_sample". The r1_analysis
dose-response path is UNTOUCHED and must not be called for one_sample specs.

Charter / gate semantics (F414 orchestrator decision, do not relitigate):
- Primary test: H_mean_excess_{primary_horizon}d — block-bootstrap one-sample
  test, H0: mean_excess == 0.
- FDR family of 1: BH with one hypothesis is trivially p_raw <= fdr_q.
- Direction gate: the harness emits market-convention excess regardless of
  spec.direction (its _forward_return direction param is hardcoded "long" at
  every call site — event_study.py:1636, 2155). The premise's predicted sign
  is applied in THIS module: direction="short" → require mean_excess < 0;
  direction="long" → require mean_excess > 0.
- Power gate: mde_1samp_pp (from harness per_horizon) <= spec.design_mde_pp.
- Verdict:
    ADVANCE                    iff predicted sign AND BH-rejected AND power gate passes
    UNTESTABLE-underpowered    iff mde_1samp_pp > design_mde_pp
    NOT-SUPPORTED              otherwise (sign wrong, or not BH-rejected despite power)
- 63td comparability row: report-only, never a bar, never in FDR family.
- Era/regime/peer/perturbation: honesty lenses, never bars.

Interface:
    run_s1_onesample_analysis(study_dir, *, seed, ledger_path, primary_horizon,
                               horizons, direction, design_mde_pp, fdr_q) -> dict
    _build_s1_ledger_entry(result, study_name, cfg_hash, primary_horizon,
                            all_horizons, spec_horizons) -> dict
    _atomic_write  (re-exported from r1_analysis for worker convenience)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Ensure backend/ is on sys.path when run as a script.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# Module-level defaults (mirrors r1_analysis pattern; overridable per-call)
# ---------------------------------------------------------------------------

PRIMARY_HORIZON: int = 30      # F414 default for the s1 insider-selling premise
ALL_HORIZONS: tuple[int, ...] = (10, 21, 30)
FDR_Q: float = 0.10
SEED: int = 20260606
_MDE_MULT: float = 2.80158  # z_0.975 + z_0.80 — census convention (premise_power_census.py)


def _mde_1samp_pp(ph: dict) -> Optional[float]:
    """One-sample MDE in TRUE percentage points from a harness per_horizon row.

    F415 TRAP (FIXED as of F415): the harness's per_horizon["mde_ppt"] field
    formerly emitted round(mde*100) over percent-valued std (event_study.py:1293),
    producing values ~100× too large (e.g. 659.07 instead of ~6.59pp).  Fixed in
    F415 by removing the ×100 multiplication.  Pre-F415 artifacts carry the 100×
    inflated values — consumers of those artifacts must recompute from std/n
    (which is what this function does, for backward-compat safety).
    """
    std = ph.get("std_excess_pct")
    n = ph.get("n_explore_valid")
    if std is None or not n or n < 2:
        return None
    return _MDE_MULT * std / math.sqrt(n)


# ---------------------------------------------------------------------------
# Config hash (for ledger identity)
# ---------------------------------------------------------------------------

def _config_hash(seed: int, primary_horizon: int, all_horizons: tuple[int, ...]) -> str:
    config_str = json.dumps({
        "seed": seed,
        "analysis_form": "one_sample",
        "horizons": list(sorted(all_horizons)),
        "primary_horizon": primary_horizon,
    }, sort_keys=True)
    return hashlib.sha256(config_str.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Atomic write (delegating to event_study, same as r1_analysis)
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (re-exported for worker convenience)."""
    try:
        from research.event_study import _atomic_write as _aw
        _aw(path, content)
    except Exception:
        import tempfile
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_meta(study_dir: Path) -> dict:
    """Load meta.json from study_dir (same pattern as r1_analysis._load_events)."""
    meta_path = study_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found in {study_dir}")
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def _get_ph(meta: dict, horizon: int) -> dict:
    """Extract per_horizon stats for a single horizon. Tries int and str keys."""
    per_h = meta.get("per_horizon", {})
    # event_study writes int keys in memory but json round-trips to str
    return per_h.get(horizon) or per_h.get(str(horizon)) or {}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_s1_onesample_analysis(
    study_dir: Path,
    *,
    seed: int = SEED,
    ledger_path: Optional[Path] = None,
    primary_horizon: Optional[int] = None,
    horizons: Optional[tuple[int, ...]] = None,
    direction: str = "long",          # "long" = positive excess; "short" = negative
    design_mde_pp: Optional[float] = None,
    fdr_q: float = FDR_Q,
) -> dict:
    """S-1 one-sample direction analysis.

    Reads meta.json from study_dir (written by the event_study harness).
    Tests whether the all-event mean excess at primary_horizon has the direction
    stated by the premise, is BH-rejected at fdr_q, and is adequately powered
    (mde_1samp_pp <= design_mde_pp).

    Writes s1_onesample_verdict.json into study_dir.
    Appends a ledger entry ONLY when ledger_path is given explicitly (same guard
    as r1_analysis — ledger_path=None skips with a warning).

    Returns the result dict.
    """
    study_dir = Path(study_dir)

    _primary = primary_horizon if primary_horizon is not None else PRIMARY_HORIZON
    _horizons = horizons if horizons is not None else ALL_HORIZONS
    # 63td comparability row: always computed but never a bar (mirrors F410)
    _horizons_with_63 = tuple(sorted(set(_horizons) | {63}))

    # ------------------------------------------------------------------
    # 1. Load meta.json
    # ------------------------------------------------------------------
    meta = _load_meta(study_dir)
    study_name = meta.get("study_name", study_dir.name)

    # ------------------------------------------------------------------
    # 2. Extract primary-horizon stats
    # ------------------------------------------------------------------
    ph = _get_ph(meta, _primary)
    mean_excess: Optional[float] = ph.get("mean_excess_pct")
    std_excess: Optional[float] = ph.get("std_excess_pct")
    p_bootstrap: Optional[float] = ph.get("p_bootstrap")
    p_nw: Optional[float] = ph.get("p_nw")
    n_events: Optional[int] = ph.get("n_explore_valid")

    # F415 TRAP (FIXED as of F415): the harness's per_horizon["mde_ppt"] field
    # formerly emitted round(mde * 100, 4) over percent-valued std (100× inflated,
    # e.g. 659.07 instead of ~6.59pp). Caught by the F338 anchor.  Fixed in F415
    # by removing the ×100 in event_study.py.  For pre-F415 artifacts this function
    # still recomputes from std_excess_pct and n directly (_mde_1samp_pp) to stay
    # correct regardless of whether the artifact predates the fix.
    mde_ppt: Optional[float] = _mde_1samp_pp(ph)

    if mean_excess is None or p_bootstrap is None:
        raise ValueError(
            f"meta.json missing per_horizon[{_primary}].mean_excess_pct or p_bootstrap. "
            f"Available horizons: {list(meta.get('per_horizon', {}).keys())}. "
            "Ensure the harness was run with the correct horizon set."
        )

    # ------------------------------------------------------------------
    # 3. FDR family of 1 — BH trivially reduces to p_raw <= fdr_q
    # ------------------------------------------------------------------
    h_key = f"H_mean_excess_{_primary}d"
    bh_rejected = p_bootstrap <= fdr_q

    # ------------------------------------------------------------------
    # 4. Direction-aware advance gate
    # ------------------------------------------------------------------
    # The harness computes ALL forward returns in market terms — its _forward_return
    # direction param exists but every call site hardcodes direction="long"
    # (event_study.py:1636, 2155). spec.direction does NOT flow into the numbers.
    # So the premise's predicted sign must be applied HERE:
    #   direction="long"  → thesis is outperformance  → expect mean_excess > 0
    #   direction="short" → thesis is underperformance → expect mean_excess < 0
    direction_correct = (mean_excess < 0) if direction == "short" else (mean_excess > 0)

    # Power gate: mde_1samp_pp must be <= design_mde_pp
    power_evaluable = (mde_ppt is not None) and math.isfinite(mde_ppt)
    if design_mde_pp is not None and power_evaluable:
        power_gate_passed = mde_ppt <= design_mde_pp
    elif design_mde_pp is None:
        # No design MDE provided — report underpowered check as N/A
        power_gate_passed = None
    else:
        power_gate_passed = None  # mde_ppt not available

    if not power_evaluable or (design_mde_pp is not None and not power_gate_passed):
        explore_decision = "UNTESTABLE-underpowered"
        if not power_evaluable:
            explore_decision_note = "mde_1samp_pp not computable (n too small or missing)"
        else:
            explore_decision_note = (
                f"mde_1samp_pp={mde_ppt:.2f}pp > design_mde_pp={design_mde_pp:.2f}pp"
            )
    elif direction_correct and bh_rejected:
        explore_decision = "ADVANCE"
        _correct_sign = "negative" if direction == "short" else "positive"
        explore_decision_note = (
            f"mean_excess={mean_excess:.4f}pp ({_correct_sign} = thesis correct for direction={direction!r}), "
            f"p_boot={p_bootstrap:.4f} <= fdr_q={fdr_q}, "
            f"mde_1samp_pp={mde_ppt:.2f}pp <= design_mde_pp={design_mde_pp:.2f}pp"
        )
    else:
        explore_decision = "NOT-SUPPORTED"
        reasons = []
        if not direction_correct:
            _expected_sign = "negative" if direction == "short" else "positive"
            reasons.append(
                f"direction wrong: expected {_expected_sign} mean_excess for direction={direction!r}, "
                f"got {mean_excess:.4f}pp"
            )
        if not bh_rejected:
            reasons.append(f"p_boot={p_bootstrap:.4f} > fdr_q={fdr_q} (not BH-rejected)")
        explore_decision_note = "; ".join(reasons)

    # ------------------------------------------------------------------
    # 5. Honesty lenses (report-only, never bars)
    # ------------------------------------------------------------------

    # Era consistency
    era_lens: dict = {}
    for era_name, blk in meta.get("era_consistency", {}).items():
        ph_era = (blk.get("per_horizon") or {})
        ph_h = ph_era.get(_primary) or ph_era.get(str(_primary)) or {}
        era_lens[era_name] = {
            "n_events": blk.get("n_events"),
            "mean_excess_pct": ph_h.get("mean_excess_pct"),
        }

    # Regime breakdown
    regime_lens: dict = {}
    for regime_name, blk in meta.get("regime_breakdown", {}).items():
        ph_reg = (blk.get("per_horizon") or {})
        ph_h = ph_reg.get(_primary) or ph_reg.get(str(_primary)) or {}
        regime_lens[regime_name] = {
            "n_events": blk.get("n_events"),
            "mean_excess_pct": ph_h.get("mean_excess_pct"),
        }

    # Peer lens: peer_median_excess_pct at primary horizon (report-only)
    peer_lens: dict = {}
    ph_primary = _get_ph(meta, _primary)
    peer_lens["peer_median_excess_pct"] = ph_primary.get("peer_median_excess_pct")

    # Survivorship
    survivorship = meta.get("survivorship", {})

    # Perturbation: not available in meta.json for one_sample form — note this.
    perturbation_note = (
        "Perturbation band not computed for one_sample form. "
        "The harness meta.json does not contain per-perturbation mean_excess rows. "
        "Sign stability across perturbations is not assessable from this artifact."
    )

    # ------------------------------------------------------------------
    # 6. 63td comparability row (report-only, never a bar)
    # ------------------------------------------------------------------
    ph_63 = _get_ph(meta, 63)
    comparability_63td: Optional[dict] = None
    if ph_63:
        comparability_63td = {
            "mean_excess_pct": ph_63.get("mean_excess_pct"),
            "p_bootstrap": ph_63.get("p_bootstrap"),
            "mde_ppt": _mde_1samp_pp(ph_63),
            "n_explore_valid": ph_63.get("n_explore_valid"),
            "note": "Report-only 63td comparability row (F410). Never a bar, never in FDR family.",
        }

    # Secondary horizons (spec horizons excluding primary)
    secondary_horizons: dict = {}
    for h in sorted(_horizons):
        if h == _primary:
            continue
        ph_h = _get_ph(meta, h)
        if ph_h:
            secondary_horizons[f"{h}d"] = {
                "mean_excess_pct": ph_h.get("mean_excess_pct"),
                "p_bootstrap": ph_h.get("p_bootstrap"),
                "mde_ppt": _mde_1samp_pp(ph_h),
                "n_explore_valid": ph_h.get("n_explore_valid"),
                "note": "Report-only secondary horizon.",
            }

    # ------------------------------------------------------------------
    # 7. Build result dict
    # ------------------------------------------------------------------
    cfg_hash = _config_hash(seed, primary_horizon=_primary, all_horizons=_horizons)

    result: dict = {
        "study_name": study_name,
        "analysis_version": "s1_onesample_analysis_v1",
        "analysis_form": "one_sample",
        "config_hash": cfg_hash,
        "seed": seed,
        "primary_horizon": _primary,
        "direction": direction,
        "design_mde_pp": design_mde_pp,
        "fdr_q": fdr_q,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_valid_events": n_events,
        # Primary test
        h_key: {
            "mean_excess_pct": round(mean_excess, 4) if mean_excess is not None else None,
            "std_excess_pct": round(std_excess, 4) if std_excess is not None else None,
            "p_bootstrap": round(p_bootstrap, 4) if p_bootstrap is not None else None,
            "p_nw": round(p_nw, 4) if p_nw is not None else None,
            "mde_1samp_pp": round(mde_ppt, 4) if mde_ppt is not None else None,
            "n": n_events,
            "bh_rejected": bh_rejected,
        },
        "fdr_report": {
            h_key: {
                "p_raw": round(p_bootstrap, 4) if p_bootstrap is not None else None,
                "bh_threshold": fdr_q,
                "rejected": bh_rejected,
                "note": "BH family of 1: p_raw <= fdr_q (trivially).",
            }
        },
        "mde_1samp_pp": round(mde_ppt, 4) if mde_ppt is not None else None,
        "power_gate_passed": power_gate_passed,
        # Decision
        "explore_decision": explore_decision,
        "explore_decision_rationale": {
            "direction_correct": direction_correct,
            "bh_rejected": bh_rejected,
            "power_gate_passed": power_gate_passed,
            "note": explore_decision_note,
        },
        # Lenses (report-only)
        "era_lens": era_lens,
        "regime_lens": regime_lens,
        "peer_lens": peer_lens,
        "survivorship": survivorship,
        "perturbation_note": perturbation_note,
        "comparability_63td": comparability_63td,
        "secondary_horizons": secondary_horizons,
    }

    # ------------------------------------------------------------------
    # 8. Write verdict file
    # ------------------------------------------------------------------
    verdict_path = study_dir / "s1_onesample_verdict.json"
    _atomic_write(verdict_path, json.dumps(result, indent=2, default=str))
    log.info("S-1 one-sample verdict written to %s (decision=%s)", verdict_path, explore_decision)

    # ------------------------------------------------------------------
    # 9. Ledger append (only when ledger_path given explicitly)
    # ------------------------------------------------------------------
    if ledger_path is not None:
        try:
            entry = _build_s1_ledger_entry(
                result=result,
                study_name=study_name,
                cfg_hash=cfg_hash,
                primary_horizon=_primary,
                all_horizons=_horizons_with_63,
                spec_horizons=_horizons,
            )
            _append_s1_ledger(entry, ledger_path)
        except Exception as exc:
            log.warning("Failed to append s1 ledger entry: %s", exc)
    else:
        log.warning(
            "ledger_path=None — FDR ledger append SKIPPED for %s "
            "(pass an explicit path to record this look)", study_name,
        )

    return result


# ---------------------------------------------------------------------------
# Ledger entry builder
# ---------------------------------------------------------------------------

def _build_s1_ledger_entry(
    result: dict,
    study_name: str,
    cfg_hash: str,
    primary_horizon: int = PRIMARY_HORIZON,
    all_horizons: tuple[int, ...] = ALL_HORIZONS,
    spec_horizons: Optional[tuple[int, ...]] = None,
) -> dict:
    """Build an FDR ledger entry dict for a one-sample analysis result.

    study_name suffix = "_s1_onesample_family" (distinct from r1_family).
    F410: all_horizons = actual computed set (incl. 63 always); spec_horizons = declared spec.
    F416: spec_horizons is recorded unconditionally (even when equal to all_horizons)
    for audit completeness.  None is recorded when spec_horizons is not provided.
    """
    h_key = f"H_mean_excess_{primary_horizon}d"
    entry_study_name = study_name + "_s1_onesample_family"

    entry: dict = {
        "study_name": entry_study_name,
        "analysis_form": "one_sample",
        "created_at": result.get("created_at"),
        "study_config_hash": cfg_hash,
        "fdr_q": result.get("fdr_q", FDR_Q),
        "primary_horizon": primary_horizon,
        "all_horizons": list(all_horizons),
        "design_mde_pp": result.get("design_mde_pp"),
    }
    # F416: record spec_horizons unconditionally for audit completeness.
    entry["spec_horizons"] = list(spec_horizons) if spec_horizons is not None else None

    h_data = result.get(h_key, {})
    entry.update({
        "per_test": {
            h_key: {
                "p_boot": h_data.get("p_bootstrap"),
                "p_nw": h_data.get("p_nw"),
                "n": h_data.get("n"),
                "mean_excess_pct": h_data.get("mean_excess_pct"),
                "mde_ppt": h_data.get("mde_1samp_pp"),
            },
        },
        "bh_rejection_set": {h_key: h_data.get("bh_rejected", False)},
        "explore_decision": result.get("explore_decision"),
        "mde_1samp_pp": result.get("mde_1samp_pp"),
    })
    return entry


# ---------------------------------------------------------------------------
# Ledger append
# ---------------------------------------------------------------------------

def _append_s1_ledger(entry: dict, ledger_path: Path) -> None:
    """Append one s1 entry to the FDR ledger. Read-modify-write; never truncate."""
    ledger_path = Path(ledger_path)
    if ledger_path.exists():
        with open(ledger_path, encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            raise ValueError(f"Ledger at {ledger_path} is not a JSON array.")
    else:
        entries = []
    entries.append(entry)
    _atomic_write(ledger_path, json.dumps(entries, indent=2, default=str))
    log.info("S-1 ledger entry appended to %s (total entries: %d)", ledger_path, len(entries))


# ---------------------------------------------------------------------------
# __main__ — standalone invocation (mirrors r1_analysis pattern)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="S-1 one-sample analysis (F414)")
    parser.add_argument("--study-dir", required=True, help="Path to study_dir with meta.json")
    parser.add_argument("--primary-horizon", type=int, default=PRIMARY_HORIZON)
    parser.add_argument("--direction", default="short", help="long or short")
    parser.add_argument("--design-mde-pp", type=float, default=None)
    parser.add_argument("--fdr-q", type=float, default=FDR_Q)
    parser.add_argument("--ledger-path", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    result = run_s1_onesample_analysis(
        study_dir=Path(args.study_dir),
        primary_horizon=args.primary_horizon,
        direction=args.direction,
        design_mde_pp=args.design_mde_pp,
        fdr_q=args.fdr_q,
        ledger_path=Path(args.ledger_path) if args.ledger_path else None,
    )
    print(f"explore_decision: {result['explore_decision']}")
    print(f"mean_excess (primary): {result.get(f'H_mean_excess_{args.primary_horizon}d', {}).get('mean_excess_pct')}")
    print(f"mde_1samp_pp: {result.get('mde_1samp_pp')}")
