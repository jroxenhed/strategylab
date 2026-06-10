"""premise_autopsy.py — F417 verdict autopsy for premise explore runs.

Given a premise's study_dir + verdict dict + spec, builds a structured
AutopsyResult: which gate failed, census numbers, and suggested descendants.

Contracts:
- build_autopsy() is read-only: reads verdict dict + events.ndjson only.
- NEVER writes to FDR ledger, never appends run_history, never calls
  store.transition().
- Circularity caveat is ALWAYS attached to suggestions and always appended
  to plain_summary — never skip.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from research.premise_census import CensusResult, compute_census

# ---------------------------------------------------------------------------
# Circularity caveat — module-level constant, non-optional, always present
# ---------------------------------------------------------------------------
_CIRCULARITY_CAVEAT = (
    "Descendant premise is hypothesis-mined from the same explore data. "
    "Per program charter, a confirm run (F393) on this descendant requires "
    "explicit confirmation of the circularity obligation and FDR ledger entry."
)


@dataclass
class DescendantSuggestion:
    rationale: str                      # e.g. "one_sample reformulation"
    spec_overrides: dict                # fields to pass to duplicate_premise + add_spec
    predicted_testable: Optional[bool]  # from census.testable_1samp / testable_gap
    predicted: dict                     # {n_events, mde_pp, observed_pp, powered}
    caveat: str                         # circularity caveat — always present, non-optional
    actionable: bool = True             # C-03: False → guidance only, no derive path


@dataclass
class AutopsyResult:
    premise_id: str
    study_name: str
    explore_decision: str
    analysis_form: Literal["dose_response", "one_sample"]
    failed_gate: Optional[str]          # plain-English name of the failed gate
    failed_gate_detail: str             # numbers / reason
    census: Optional[CensusResult]      # from premise_census.compute_census
    suggestions: list[DescendantSuggestion]
    plain_summary: str                  # 2-4 sentence plain-English narrative


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _diagnose_r1(verdict: dict, census: Optional[CensusResult], primary_horizon: int) -> tuple[Optional[str], str]:
    """Return (failed_gate, failed_gate_detail) for a dose_response / r1 verdict."""
    mde_not_evaluable = verdict.get("mde_not_evaluable", False)
    mde_gate_passed = verdict.get("mde_gate_passed", False)
    mde_q5q1_pp = verdict.get("mde_q5q1_pp")
    explore_decision = verdict.get("explore_decision", "")

    if mde_not_evaluable:
        return (
            "dose MDE not evaluable (too few events per quintile arm)",
            f"Q5 n={verdict.get('H1', {}).get('n_q5')} / Q1 n={verdict.get('H1', {}).get('n_q1')} — "
            "floor-division arms too small to compute std",
        )

    if not mde_gate_passed:
        detail = (
            f"mde_q5q1_pp={mde_q5q1_pp:.1f}pp > 1.0pp threshold"
            if mde_q5q1_pp is not None
            else "mde_q5q1_pp not available"
        )
        return ("dose MDE gate: mde > 1.0pp threshold", detail)

    # MDE passed — look at sign/rho
    h1 = verdict.get("H1", {})
    obs_gap = h1.get("obs_gap_q5q1_pp")
    bh_rejected = h1.get("bh_rejected", False)
    h1b = verdict.get("H1b", {})
    rho_s = h1b.get("rho_s") if isinstance(h1b, dict) else None

    rationale = verdict.get("explore_decision_rationale", {})
    gap_positive = rationale.get("gap_positive")
    rho_positive = rationale.get("rho_positive")

    if obs_gap is not None and obs_gap <= 0:
        return (
            "direction (dose gap in wrong direction)",
            f"obs_gap_q5q1_pp={obs_gap:.2f}pp — gap negative; score predicts the wrong direction",
        )

    if rho_s is not None and rho_s < 0:
        return (
            "Spearman rho sign (wrong direction)",
            f"rho_s={rho_s:.3f} < 0 — monotone relationship inverted",
        )

    if not bh_rejected:
        return (
            "BH rejection (p too high despite passing MDE)",
            f"p_boot={h1.get('p_boot')} / bh_rejected=False",
        )

    return (None, "all gates passed")


def _diagnose_s1(verdict: dict) -> tuple[Optional[str], str]:
    """Return (failed_gate, failed_gate_detail) for a one_sample / s1 verdict."""
    power_gate_passed = verdict.get("power_gate_passed")
    mde_1samp_pp = verdict.get("mde_1samp_pp")
    rationale = verdict.get("explore_decision_rationale", {})
    direction_correct = rationale.get("direction_correct")
    bh_rejected = rationale.get("bh_rejected")
    design_mde_pp = verdict.get("design_mde_pp")

    if power_gate_passed is False:
        detail_parts = []
        if mde_1samp_pp is not None:
            detail_parts.append(f"mde_1samp_pp={mde_1samp_pp:.1f}pp")
        if design_mde_pp is not None:
            detail_parts.append(f"design_mde_pp={design_mde_pp:.1f}pp")
        return (
            "one-sample power gate: mde > design_mde_pp",
            " > ".join(detail_parts) if detail_parts else "power not evaluable",
        )

    if direction_correct is False:
        return (
            "direction (sign opposite to predicted)",
            "mean_excess has the wrong sign relative to the spec direction",
        )

    if bh_rejected is False:
        return (
            "BH rejection (p too high despite sufficient power)",
            f"bh_rejected=False — effect size insufficient for statistical rejection",
        )

    return (None, "all gates passed")


def _make_predicted(census: Optional[CensusResult], primary_horizon: int) -> dict:
    """Build the predicted numbers block for a DescendantSuggestion."""
    if census is None:
        return {"n_events": None, "mde_pp": None, "observed_pp": None, "powered": None}
    ph = census.horizons.get(primary_horizon)
    if ph is None:
        return {"n_events": census.n_valid, "mde_pp": None, "observed_pp": None, "powered": None}
    powered = census.testable_1samp  # for one_sample reformulation
    return {
        "n_events": census.n_valid,
        "mde_pp": ph.mde_1samp_pp,
        "observed_pp": ph.mean_excess_pp,
        "powered": powered,
    }


def _build_suggestions(
    explore_decision: str,
    analysis_form: Literal["dose_response", "one_sample"],
    verdict: dict,
    census: Optional[CensusResult],
    spec: dict,
    primary_horizon: int,
) -> list[DescendantSuggestion]:
    suggestions: list[DescendantSuggestion] = []

    # ADVANCE → no suggestions needed
    if explore_decision == "ADVANCE":
        return []

    # Dose-response UNTESTABLE (underpowered or not evaluable) → suggest one_sample
    if analysis_form == "dose_response" and (
        "UNTESTABLE" in explore_decision
    ):
        predicted_testable = census.testable_1samp if census is not None else None
        suggestions.append(DescendantSuggestion(
            rationale=(
                "one_sample reformulation — dose form is underpowered; "
                "the one-sample test needs fewer events to reach power. "
                "Set design_mde_pp before running."
            ),
            spec_overrides={
                "analysis_form": "one_sample",
                "design_mde_pp": None,  # USER MUST SET
            },
            predicted_testable=predicted_testable,
            predicted=_make_predicted(census, primary_horizon),
            caveat=_CIRCULARITY_CAVEAT,
        ))
        return suggestions

    # Dose-response WEAKENED-IN-EXPLORE: if census shows opposite sign → invert direction
    if analysis_form == "dose_response" and explore_decision == "WEAKENED-IN-EXPLORE":
        ph = census.horizons.get(primary_horizon) if census is not None else None
        if ph is not None and ph.mean_excess_pp is not None:
            spec_direction = spec.get("direction", "long")
            # If long and mean excess is strongly negative, or short and strongly positive
            if (spec_direction == "long" and ph.mean_excess_pp < 0) or \
               (spec_direction == "short" and ph.mean_excess_pp > 0):
                inverted = "short" if spec_direction == "long" else "long"
                suggestions.append(DescendantSuggestion(
                    rationale=(
                        f"inverted-direction descendant — census shows opposite sign "
                        f"(mean_excess={ph.mean_excess_pp:.2f}pp, spec direction={spec_direction})"
                    ),
                    spec_overrides={"direction": inverted},
                    predicted_testable=None,
                    predicted=_make_predicted(census, primary_horizon),
                    caveat=_CIRCULARITY_CAVEAT,
                ))
        # WEAKENED is ambiguous otherwise — no suggestion
        return suggestions

    # C-03: one_sample UNTESTABLE-underpowered — explicit guidance, not a derivable action
    if analysis_form == "one_sample" and "UNTESTABLE" in explore_decision:
        ph_c = census.horizons.get(primary_horizon) if census is not None else None
        observed_mde = ph_c.mde_1samp_pp if ph_c is not None else None
        design_mde = spec.get("design_mde_pp")
        predicted_block = {
            "n_events": census.n_valid if census is not None else None,
            "mde_pp": observed_mde,
            "observed_pp": ph_c.mean_excess_pp if ph_c is not None else None,
            "powered": False,
        }
        mde_detail = ""
        if observed_mde is not None and design_mde is not None:
            mde_detail = (
                f" Observed MDE at {primary_horizon}td: {observed_mde:.2f}pp "
                f"vs design MDE: {design_mde:.2f}pp."
            )
        elif observed_mde is not None:
            mde_detail = f" Observed MDE at {primary_horizon}td: {observed_mde:.2f}pp."
        suggestions.append(DescendantSuggestion(
            rationale=(
                "underpowered for your design MDE; widen the net (longer window / "
                "relaxed floors) or accept a higher design MDE." + mde_detail
            ),
            spec_overrides={},
            predicted_testable=False,
            predicted=predicted_block,
            caveat=_CIRCULARITY_CAVEAT,
            actionable=False,  # guidance only — no structural override to apply
        ))
        return suggestions

    # One-sample NOT-SUPPORTED: if mean has opposite sign to spec direction → invert
    if analysis_form == "one_sample" and explore_decision == "NOT-SUPPORTED":
        h_key = f"H_mean_excess_{primary_horizon}d"
        h_data = verdict.get(h_key, {})
        mean_excess = h_data.get("mean_excess_pct")
        spec_direction = spec.get("direction", "long")

        if mean_excess is not None:
            opposite = (
                (spec_direction == "long" and mean_excess < 0) or
                (spec_direction == "short" and mean_excess > 0)
            )
            if opposite:
                inverted = "short" if spec_direction == "long" else "long"
                suggestions.append(DescendantSuggestion(
                    rationale=(
                        f"inverted-direction descendant — observed mean_excess={mean_excess:.2f}pp "
                        f"is opposite to spec direction={spec_direction}"
                    ),
                    spec_overrides={"direction": inverted},
                    predicted_testable=None,
                    predicted={
                        "n_events": census.n_valid if census else None,
                        "mde_pp": census.horizons.get(primary_horizon, None) and
                                  census.horizons[primary_horizon].mde_1samp_pp
                                  if census else None,
                        "observed_pp": mean_excess,
                        "powered": None,
                    },
                    caveat=_CIRCULARITY_CAVEAT,
                ))

    return suggestions


def build_autopsy(
    premise_id: str,
    study_dir: Path,
    verdict: dict,
    spec: dict,
    cached_census: Optional[dict] = None,
) -> AutopsyResult:
    """Build an AutopsyResult for the given premise's latest valid explore run.

    Parameters
    ----------
    premise_id:
        The premise ID string (e.g. "p-1569aa97").
    study_dir:
        Path to the explore output directory (contains events.ndjson + verdict JSON).
    verdict:
        The stored verdict dict (from run_history[latest_explore]["verdict"]).
    spec:
        The premise's current spec dict.
    cached_census:
        If the run_history entry already has a "census" dict (written by the F418
        gate during the explore run), pass it here to skip re-computing from disk.
        If None, compute_census() is called against study_dir/events.ndjson.
    """
    study_name = verdict.get("study_name", study_dir.name)
    explore_decision = verdict.get("explore_decision", "UNKNOWN")

    # Determine analysis form from spec (fallback: look at verdict keys)
    analysis_form_raw = spec.get("analysis_form", "")
    if analysis_form_raw not in ("dose_response", "one_sample"):
        # Infer from verdict shape
        if "mde_q5q1_pp" in verdict:
            analysis_form_raw = "dose_response"
        else:
            analysis_form_raw = "one_sample"
    analysis_form: Literal["dose_response", "one_sample"] = analysis_form_raw  # type: ignore[assignment]

    primary_horizon = verdict.get("primary_horizon") or spec.get("primary_horizon") or 30
    horizons_raw = spec.get("horizons", [primary_horizon])
    if isinstance(horizons_raw, (list, tuple)):
        horizons = tuple(int(h) for h in horizons_raw)
    else:
        horizons = (int(primary_horizon),)
    design_mde_pp: Optional[float] = spec.get("design_mde_pp")

    # Census — use cached if available, otherwise compute from disk
    census: Optional[CensusResult] = None
    if cached_census is not None:
        # Reconstruct CensusResult from the cached dict (stored via dataclasses.asdict)
        try:
            from research.premise_census import HorizonCensus
            h_map: dict[int, HorizonCensus] = {}
            for h_key, hc in (cached_census.get("horizons") or {}).items():
                h_map[int(h_key)] = HorizonCensus(**hc)
            cached_copy = dict(cached_census)
            cached_copy["horizons"] = h_map
            census = CensusResult(**cached_copy)
        except Exception:
            # Malformed cache — fall through to disk read
            census = None

    if census is None:
        events_path = study_dir / "events.ndjson"
        try:
            census = compute_census(
                events_path=events_path,
                analysis_form=analysis_form,
                horizons=horizons,
                primary_horizon=primary_horizon,
                design_mde_pp=design_mde_pp,
            )
        except FileNotFoundError:
            census = None

    # Gate diagnosis
    if analysis_form == "dose_response":
        failed_gate, failed_gate_detail = _diagnose_r1(verdict, census, primary_horizon)
    else:
        failed_gate, failed_gate_detail = _diagnose_s1(verdict)

    # Suggestions
    suggestions = _build_suggestions(
        explore_decision=explore_decision,
        analysis_form=analysis_form,
        verdict=verdict,
        census=census,
        spec=spec,
        primary_horizon=primary_horizon,
    )

    # Plain summary
    ph_str = ""
    if census is not None:
        ph = census.horizons.get(primary_horizon)
        if ph is not None and ph.mde_1samp_pp is not None:
            ph_str = (
                f" Census at {primary_horizon}td: n={census.n_valid}, "
                f"mde_1samp={ph.mde_1samp_pp:.2f}pp"
            )
            if ph.mean_excess_pp is not None:
                ph_str += f", observed mean={ph.mean_excess_pp:.2f}pp."

    gate_str = f" Failed gate: {failed_gate}." if failed_gate else ""
    sugg_str = (
        f" {len(suggestions)} descendant suggestion(s) generated."
        if suggestions else " No descendant suggestions."
    )
    circularity_str = (
        " Note: any suggested descendant is hypothesis-mined from the same explore "
        "data — per program charter, circularity obligation and FDR ledger entry are "
        "required before running a confirm."
    ) if suggestions else ""

    plain_summary = (
        f"Explore decision for {premise_id}: {explore_decision}.{gate_str}"
        f"{ph_str}{sugg_str}{circularity_str}"
    )

    return AutopsyResult(
        premise_id=premise_id,
        study_name=study_name,
        explore_decision=explore_decision,
        analysis_form=analysis_form,
        failed_gate=failed_gate,
        failed_gate_detail=failed_gate_detail,
        census=census,
        suggestions=suggestions,
        plain_summary=plain_summary,
    )
