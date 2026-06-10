"""premise_census.py — F418 reusable census math for both analysis forms.

Callable from the F418 pre-explore census gate (in premise_run.py) and from
the F417 autopsy builder.  Pure computation: reads events.ndjson, computes
MDEs, returns structured result.  NEVER writes files, never touches the FDR
ledger.

MDE convention (matches premise_p1569aa97_census.py and r1_analysis.py):
    two-sided alpha=0.05, 80% power → z_0.975 + z_0.80 = 2.80158

F415 TRAP: never consume per_horizon["mde_ppt"] from meta.json — that field
is 100× the true pp value.  All MDE math uses std_excess_pct + n from the
events themselves.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

_MDE_MULT: float = 2.80158       # z_0.975 + z_0.80, two-sided alpha=.05, 80% power
_DOSE_GAP_FLOOR_PP: float = 1.0  # charter MDE abort threshold for dose-gap form


@dataclass
class HorizonCensus:
    horizon_td: int
    mean_excess_pp: Optional[float]
    std_excess_pp: Optional[float]
    mde_1samp_pp: Optional[float]   # _MDE_MULT * std / sqrt(n_valid)
    mde_gap_pp: Optional[float]     # dose form only: _MDE_MULT * sqrt(s5²/arm + s1²/arm)
    arm_size: Optional[int]         # n_score_defined // 5


@dataclass
class CensusResult:
    n_valid: int
    n_score_defined: int             # events with payload.score != None
    horizons: dict[int, HorizonCensus]
    primary_horizon: int
    analysis_form: Literal["dose_response", "one_sample"]
    testable_1samp: Optional[bool]   # True if mde_1samp_pp <= design_mde_pp
    testable_gap: Optional[bool]     # True if mde_gap_pp <= _DOSE_GAP_FLOOR_PP (1.0)
    design_mde_pp: Optional[float]   # from spec, None for dose_response
    note: str                        # plain-English one-liner


def _stdev(values: list[float]) -> float:
    """Sample std-dev (ddof=1), matching statistics.stdev / event_study.py:1298."""
    n = len(values)
    if n < 2:
        raise ValueError("need at least 2 values for stdev")
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def compute_census(
    events_path: Path,
    analysis_form: Literal["dose_response", "one_sample"],
    horizons: tuple[int, ...],
    primary_horizon: int,
    design_mde_pp: Optional[float] = None,
) -> CensusResult:
    """Compute census MDEs for all requested horizons.

    Parameters
    ----------
    events_path:
        Path to events.ndjson.  Raises FileNotFoundError with a clear message
        if missing or unreadable — callers (gate code) catch and warn.
    analysis_form:
        "dose_response" or "one_sample".  Dose form also computes mde_gap_pp.
    horizons:
        Tuple of horizon ints (e.g. (30, 63)) from the spec.
    primary_horizon:
        The horizon used for testability verdicts (usually max(horizons)).
    design_mde_pp:
        Required for one_sample testability verdict.  Ignored for dose form.
    """
    if not events_path.exists():
        raise FileNotFoundError(
            f"events.ndjson not found at {events_path} — "
            "run a preview first to populate the events artifact"
        )

    try:
        raw_lines = events_path.read_text().splitlines()
    except OSError as exc:
        raise FileNotFoundError(f"Cannot read {events_path}: {exc}") from exc

    rows = [json.loads(line) for line in raw_lines if line.strip()]

    # Filter: floor_status == "ok" AND split == "explore"
    valid = [
        r for r in rows
        if r.get("floor_status") == "ok" and r.get("split") == "explore"
    ]
    n_valid = len(valid)

    # Score-defined subset (for dose-gap arm computation)
    score_def = [r for r in valid if r.get("payload", {}).get("score") is not None]
    n_score_defined = len(score_def)

    # Pre-sort score-defined rows once for all horizons
    sd_sorted = sorted(score_def, key=lambda r: r["payload"]["score"])

    horizon_results: dict[int, HorizonCensus] = {}
    for h in horizons:
        h_key = str(h)
        values = [
            r["fwd_excess_pct"][h_key]
            for r in valid
            if isinstance(r.get("fwd_excess_pct", {}).get(h_key), (int, float))
        ]

        if len(values) < 2:
            horizon_results[h] = HorizonCensus(
                horizon_td=h,
                mean_excess_pp=None,
                std_excess_pp=None,
                mde_1samp_pp=None,
                mde_gap_pp=None,
                arm_size=None,
            )
            continue

        mean_pp = sum(values) / len(values)
        std_pp = _stdev(values)
        mde_1samp = _MDE_MULT * std_pp / math.sqrt(len(values))

        # Dose-gap MDE (floor-division quintiles on score-defined subset)
        mde_gap: Optional[float] = None
        arm: Optional[int] = None
        if analysis_form == "dose_response" and n_score_defined >= 2:
            arm = n_score_defined // 5
            if arm >= 2:
                # Q1 = lowest scores, Q5 = highest scores
                q1_vals = [
                    r["fwd_excess_pct"][h_key]
                    for r in sd_sorted[:arm]
                    if isinstance(r.get("fwd_excess_pct", {}).get(h_key), (int, float))
                ]
                q5_vals = [
                    r["fwd_excess_pct"][h_key]
                    for r in sd_sorted[-arm:]
                    if isinstance(r.get("fwd_excess_pct", {}).get(h_key), (int, float))
                ]
                if len(q1_vals) >= 2 and len(q5_vals) >= 2:
                    s1 = _stdev(q1_vals)
                    s5 = _stdev(q5_vals)
                    mde_gap = _MDE_MULT * math.sqrt(s5 ** 2 / arm + s1 ** 2 / arm)

        horizon_results[h] = HorizonCensus(
            horizon_td=h,
            mean_excess_pp=mean_pp,
            std_excess_pp=std_pp,
            mde_1samp_pp=mde_1samp,
            mde_gap_pp=mde_gap,
            arm_size=arm,
        )

    # Testability verdicts (primary horizon only)
    ph = horizon_results.get(primary_horizon)
    testable_1samp: Optional[bool] = None
    testable_gap: Optional[bool] = None

    if ph is not None:
        if ph.mde_1samp_pp is not None and design_mde_pp is not None:
            testable_1samp = ph.mde_1samp_pp <= design_mde_pp
        if ph.mde_gap_pp is not None:
            testable_gap = ph.mde_gap_pp <= _DOSE_GAP_FLOOR_PP

    # Build note
    ph_mde_str = ""
    if ph is not None and ph.mde_1samp_pp is not None:
        ph_mde_str = f"mde_1samp={ph.mde_1samp_pp:.2f}pp"
        if ph.mde_gap_pp is not None:
            ph_mde_str += f", mde_gap={ph.mde_gap_pp:.2f}pp"

    if analysis_form == "one_sample":
        if testable_1samp is True:
            note = f"Testable (one-sample): n={n_valid}, {ph_mde_str} <= design={design_mde_pp}pp"
        elif testable_1samp is False:
            note = (
                f"Underpowered: n={n_valid}, {ph_mde_str} > design={design_mde_pp}pp — "
                "consider collecting more events"
            )
        else:
            note = f"Power unknown: n={n_valid}, design_mde_pp not set or insufficient data"
    else:  # dose_response
        if testable_gap is True:
            note = f"Dose gap testable: n={n_valid}, {ph_mde_str} <= {_DOSE_GAP_FLOOR_PP}pp floor"
        elif testable_gap is False:
            note = (
                f"Dose gap underpowered: n={n_valid}, {ph_mde_str} > {_DOSE_GAP_FLOOR_PP}pp floor — "
                "consider one_sample reformulation"
            )
        else:
            note = f"Dose power unknown: n={n_valid}, too few score-defined events for arm computation"

    return CensusResult(
        n_valid=n_valid,
        n_score_defined=n_score_defined,
        horizons=horizon_results,
        primary_horizon=primary_horizon,
        analysis_form=analysis_form,
        testable_1samp=testable_1samp,
        testable_gap=testable_gap,
        design_mde_pp=design_mde_pp,
        note=note,
    )
